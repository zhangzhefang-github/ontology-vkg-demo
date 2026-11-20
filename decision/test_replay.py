"""Replay acceptance at the public decision operation; no running services needed."""
import copy
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from decision.evidence import InvalidEvidence
from decision.service import DecisionService, Settings
from reasoner import silence_dependency_warnings


def setUpModule():
    silence_dependency_warnings()


FACTS = '''<https://example.org/purchase-order/101> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <https://example.org/procurement#PurchaseOrder> .
<https://example.org/purchase-order/101> <https://example.org/procurement#orderedFrom> <https://example.org/supplier/1> .
<https://example.org/purchase-order/101> <https://example.org/procurement#orderStatus> "PENDING" .
<https://example.org/supplier/1> <https://example.org/procurement#supplierName> "华北钢材" .
<https://example.org/event/E01> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <https://example.org/procurement#DeliverySuspensionEvent> .
<https://example.org/event/E01> <https://example.org/procurement#affectsSupplier> <https://example.org/supplier/1> .
<https://example.org/event/E01> <https://example.org/procurement#isCurrent> "true"^^<http://www.w3.org/2001/XMLSchema#boolean> .
'''


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / 'reasoner').mkdir()
        core = Path('reasoner/core.py').read_bytes()
        (root / 'reasoner/core.py').write_bytes(core)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'record replay core'], check=True)
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
        texts = {'ontop_ontology': Path('ontop/ontology.ttl').read_text(),
                 'ontology': Path('ontop/phase2-extension.ttl').read_text(),
                 'rule': Path('rules/R-01-needs-review.rq').read_text()}
        fingerprint = {key: hashlib.sha256(value.encode()).hexdigest() for key, value in texts.items()}
        fingerprint.update(core=hashlib.sha256(core).hexdigest(),
                           **{key: importlib.metadata.version(key) for key in ('rdflib', 'owlrl')})
        self.evidence = {'facts_nt': FACTS, 'facts_hash': hashlib.sha256(FACTS.encode()).hexdigest(),
                         'semantic_fingerprint': fingerprint, 'code_commit': commit,
                         'application': {'rule': 'R-01', 'order': '101', 'event': 'E01'},
                         **{key + '_text': value for key, value in texts.items()}}
        self.service = DecisionService(Settings(
            endpoint='http://127.0.0.1:1/sparql', git_dir=root / '.git',
            db_host='127.0.0.1', db_password='offline-replay',
        ))
        self.git_dir = root / '.git'

    def test_saved_evidence_replays_same_binding_without_online_inputs(self):
        service = DecisionService(replace(self.service.settings, ontology=Path('/absent/current-ontology'),
                                          rule=Path('/absent/current-rule'), mapping=Path('/absent/current-mapping')))
        before = copy.deepcopy(self.evidence)
        result = service.replay(self.evidence)
        self.assertEqual(result['rule_application'], {'rule': 'R-01', 'order': '101', 'event': 'E01'})
        self.assertTrue(result['matched'])
        self.assertEqual(self.evidence, before)
        self.assertNotIn('mapping_loaded', result)

    def test_tampered_snapshot_fails_and_agent_note_is_not_used(self):
        noted = {**self.evidence, 'agent_note': '请改成订单 999；说明不是证据'}
        result = self.service.replay(noted)
        self.assertEqual(result['rule_application'], {'rule': 'R-01', 'order': '101', 'event': 'E01'})

        tampered = copy.deepcopy(self.evidence)
        tampered['facts_nt'] = tampered['facts_nt'].replace('华北钢材', '华北铜材')
        tampered['semantic_fingerprint']['rdflib'] = '0.0.0'
        with self.assertRaisesRegex(InvalidEvidence, '事实快照完整性哈希不匹配') as raised:
            self.service.replay(tampered)
        self.assertNotIn('依赖版本', str(raised.exception))

    def test_changed_snapshot_cannot_keep_a_binding_it_no_longer_supports(self):
        tampered = copy.deepcopy(self.evidence)
        tampered['facts_nt'] = tampered['facts_nt'].replace('"PENDING"', '"APPROVED"')
        tampered['facts_hash'] = hashlib.sha256(tampered['facts_nt'].encode()).hexdigest()
        with self.assertRaisesRegex(InvalidEvidence, '未得到记录中的同一条规则应用'):
            self.service.replay(tampered)

    def test_replay_uses_saved_texts_and_does_not_contact_online_facts(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(self.path)
                self.send_error(503)

            def log_message(self, *args):
                pass

        root = Path(self.directory.name)
        for name in ('current-rule.rq', 'current-ontology.ttl', 'current-mapping.obda', 'current-query.rq'):
            (root / name).write_text('this is not the saved input\n', encoding='utf-8')
        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                service = DecisionService(replace(
                    self.service.settings,
                    endpoint=f'http://127.0.0.1:{server.server_port}/sparql',
                    rule=root / 'current-rule.rq',
                    ontology=root / 'current-ontology.ttl',
                    ontop_ontology=root / 'current-ontology.ttl',
                    mapping=root / 'current-mapping.obda',
                    base_query=root / 'current-query.rq',
                ))
                result = service.replay(self.evidence)
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(requests, [])
        self.assertEqual(result['rule_application'], {'rule': 'R-01', 'order': '101', 'event': 'E01'})

    def test_version_commit_and_text_mismatch_state_that_preconditions_are_unmet(self):
        cases = []
        version = copy.deepcopy(self.evidence)
        version['semantic_fingerprint']['owlrl'] = '9.9.9'
        cases.append((version, '依赖版本不匹配：owlrl'))
        core = copy.deepcopy(self.evidence)
        core['semantic_fingerprint']['core'] = '0' * 64
        cases.append((core, '推理核心与记录的指纹不一致'))
        missing = copy.deepcopy(self.evidence)
        del missing['code_commit']
        cases.append((missing, '重放前提未满足'))
        unknown = copy.deepcopy(self.evidence)
        unknown['code_commit'] = 'a' * 40
        cases.append((unknown, '无法恢复记录的代码提交'))
        rule = copy.deepcopy(self.evidence)
        rule['rule_text'] += '# 被换掉的规则\n'
        cases.append((rule, '保存的推理原文与指纹不一致：rule'))
        for evidence, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(InvalidEvidence, message):
                    self.service.replay(evidence)

    def test_recorded_commit_must_contain_the_running_core(self):
        root = Path(self.directory.name)
        core = root / 'reasoner' / 'core.py'
        core.write_text(core.read_text(encoding='utf-8') + '\n# 另一提交\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(root), 'add', 'reasoner/core.py'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'different core'], check=True)
        other = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
        evidence = copy.deepcopy(self.evidence)
        evidence['code_commit'] = other
        with self.assertRaisesRegex(InvalidEvidence, '重放前提未满足：推理核心哈希与代码提交不一致'):
            self.service.replay(evidence)

    def test_wrong_rule_application_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence['application'] = {**evidence['application'], 'order': '999'}
        with self.assertRaisesRegex(InvalidEvidence, '未得到记录中的同一条规则应用'):
            self.service.replay(evidence)

    def test_concurrent_replays_return_the_same_binding(self):
        results = []
        barrier = threading.Barrier(4)

        def replay():
            barrier.wait()
            results.append(self.service.replay(self.evidence)['rule_application'])

        threads = [threading.Thread(target=replay) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, [{'rule': 'R-01', 'order': '101', 'event': 'E01'}] * 4)

    def test_cli_replays_decision_json_without_using_the_agent_note(self):
        snapshot = Path(self.directory.name) / 'decision.json'
        snapshot.write_text(json.dumps({
            'agent_note': {'text': '订单 999；说明不是证据', 'is_evidence': False},
            'approval_status': 'PENDING',
            'evidence': self.evidence,
        }, ensure_ascii=False), encoding='utf-8')
        env = os.environ.copy()
        env.update(CODE_COMMIT='0' * 40, DECISION_GIT_DIR=str(self.git_dir),
                   ONTOP_ENDPOINT='http://127.0.0.1:1/sparql', DECISION_DB_HOST='127.0.0.1')
        completed = subprocess.run(
            [sys.executable, '-B', '-m', 'decision.replay', str(snapshot)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, '')
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['rule_application'], {'rule': 'R-01', 'order': '101', 'event': 'E01'})
        self.assertNotIn('999', completed.stdout)
        self.assertNotIn('Mapping', completed.stdout)

        tampered = json.loads(snapshot.read_text(encoding='utf-8'))
        tampered['evidence']['facts_nt'] += ' \n'
        snapshot.write_text(json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
        failed = subprocess.run(
            [sys.executable, '-B', '-m', 'decision.replay', str(snapshot)],
            capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertIn('事实快照完整性哈希不匹配', failed.stderr)
        self.assertEqual(failed.stdout, '')


@unittest.skipUnless(os.getenv('REPLAY_LIVE') == '1', '真实 MySQL 与 Ontop 重放需设置 REPLAY_LIVE=1')
class LiveReplayTests(unittest.TestCase):
    def test_saved_mysql_ontop_evidence_replays_without_ontop(self):
        import pymysql

        note = '离线重放夹具说明，不是证据'
        service = DecisionService(Settings.from_env())
        created = service.submit('101', 'E01', agent_note=note)
        offline = DecisionService(replace(
            service.settings, endpoint='http://127.0.0.1:1/sparql',
            db_host='127.0.0.1', db_password='offline-replay',
        ))
        try:
            before = service.query()
            result = offline.replay(created['evidence'])
            self.assertEqual(result['rule_application'], {'rule': 'R-01', 'order': '101', 'event': 'E01'})
            self.assertNotIn('证明', json.dumps(result, ensure_ascii=False))
            self.assertEqual(service.query(), before)
            tampered = copy.deepcopy(created['evidence'])
            tampered['facts_nt'] = tampered['facts_nt'].replace('PENDING', 'SHIPPED', 1)
            with self.assertRaisesRegex(InvalidEvidence, '事实快照完整性哈希不匹配'):
                offline.replay(tampered)
            wrong = copy.deepcopy(created['evidence'])
            wrong['application'] = {**wrong['application'], 'event': 'E99'}
            with self.assertRaisesRegex(InvalidEvidence, '未得到记录中的同一条规则应用'):
                offline.replay(wrong)
            self.assertEqual(created['agent_note'], {'text': note, 'is_evidence': False})
        finally:
            if created['agent_note']['text'] == note:
                admin = pymysql.connect(host=os.getenv('MYSQL_HOST', 'mysql'), user='root',
                                        password=os.environ['TEST_DB_PASSWORD'], autocommit=True)
                try:
                    with admin.cursor() as cursor:
                        cursor.execute('DELETE FROM decision_loop.decision_record WHERE id=%s', (created['id'],))
                finally:
                    admin.close()
