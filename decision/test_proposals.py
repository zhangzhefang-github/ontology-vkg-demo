"""Proposal acceptance through public decision operations and real services."""
import os
import contextlib
import shutil
import sys
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from reasoner import silence_dependency_warnings
from reasoner.core import InvalidExperiment
from decision.evidence import InvalidEvidence

import pymysql

from decision.service import DecisionService, Settings


def setUpModule():
    silence_dependency_warnings()


BASE_TURTLE = """
@prefix p: <https://example.org/procurement#> .
<https://example.org/purchase-order/101> a p:PurchaseOrder ;
    p:orderedFrom <https://example.org/supplier/1> ; p:orderStatus "PENDING" .
<https://example.org/supplier/1> p:supplierName "华北钢材" .
<https://example.org/event/E01> a p:DeliverySuspensionEvent ;
    p:affectsSupplier <https://example.org/supplier/1> ; p:isCurrent true .
"""


@contextlib.contextmanager
def rdf_endpoint(turtle, on_request=None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if on_request:
                on_request()
            payload = turtle.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/turtle')
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/sparql'
        finally:
            server.shutdown()
            worker.join()


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.admin = pymysql.connect(host='mysql', user='root',
                                     password=os.environ['TEST_DB_PASSWORD'], autocommit=True)
        with self.admin.cursor() as cursor:
            cursor.execute('DELETE FROM decision_loop.decision_record')
        self.service = DecisionService(Settings.from_env())

    def tearDown(self):
        self.admin.close()

    def test_submit_preserves_full_evidence_and_query_exposes_pending_decision(self):
        decision = self.service.submit('101', 'E01', agent_note='请采购主管复核')
        self.assertEqual(decision['approval_status'], 'PENDING')
        self.assertIsNone(decision['execution_status'])
        self.assertEqual(decision['order'], '101')
        self.assertEqual(decision['event'], 'E01')
        self.assertEqual(decision['assigned_role'], '采购员')
        self.assertEqual(decision['agent_note'], {'text': '请采购主管复核', 'is_evidence': False})
        self.assertIn('https://example.org/event/E01', decision['evidence']['facts_nt'])
        self.assertEqual(self.service.query()['rule_applications'][0]['decision'], decision)

    def test_concurrent_submissions_return_one_occupied_decision(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(self.service.submit, '101', 'E01', f'note {n}') for n in range(4)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(len({item['id'] for item in results}), 1)
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(self.service.query()['rule_applications'][0]['decision'], results[0])

    def test_repeat_returns_original_without_rechecking_or_overwriting_note(self):
        first = self.service.submit('101', 'E01', '原说明')
        unavailable = DecisionService(replace(self.service.settings, endpoint='http://127.0.0.1:1/sparql', code_commit=''))
        self.assertEqual(unavailable.submit('101', 'E01', '新说明'), first)

    def test_agent_parameters_cannot_change_action_role_description_or_evidence(self):
        decision = self.service.submit('101', 'E01', '非证据', action='cancel_order',
                                       assigned_role='采购主管', description='无法交付', evidence={'fake': True})
        self.assertEqual(decision['action'], 'create_supply_risk_review_task')
        self.assertEqual(decision['assigned_role'], '采购员')
        self.assertEqual(decision['description'], '订单 101 因事件 E01 需要供应风险复核。')
        self.assertNotIn('fake', decision['evidence'])

    def test_stale_rule_application_does_not_create_a_decision(self):
        try:
            with self.admin.cursor() as cursor:
                cursor.execute("UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'")
            with self.assertRaises(InvalidEvidence):
                self.service.submit('101', 'E01')
        finally:
            with self.admin.cursor() as cursor:
                cursor.execute("UPDATE procurement.supply_disruption_event SET is_current=TRUE WHERE event_id='E01'")
        self.assertIsNone(self.service.query()['rule_applications'][0]['decision'])

    def test_missing_code_commit_is_rejected_without_a_decision(self):
        missing = DecisionService(replace(self.service.settings, code_commit=''))
        with self.assertRaisesRegex(InvalidEvidence, '代码提交号必填'):
            missing.submit('101', 'E01')
        self.assertIsNone(self.service.query()['rule_applications'][0]['decision'])

    def test_two_current_events_get_independent_decisions(self):
        try:
            with self.admin.cursor() as cursor:
                cursor.execute("INSERT INTO procurement.supply_disruption_event VALUES ('E02',1,'DeliverySuspensionEvent',TRUE)")
            first = self.service.submit('101', 'E01')
            second = self.service.submit('101', 'E02')
            self.assertNotEqual(first['id'], second['id'])
            self.assertEqual(second['evidence']['application']['event'], 'E02')
            self.assertEqual(len(self.service.query()['rule_applications']), 2)
        finally:
            with self.admin.cursor() as cursor:
                cursor.execute("DELETE FROM procurement.supply_disruption_event WHERE event_id='E02'")

    def test_blank_nodes_are_rejected_instead_of_rewritten(self):
        with rdf_endpoint(BASE_TURTLE + '\n[] p:supplierName "匿名事实" .') as endpoint:
            service = DecisionService(replace(self.service.settings, endpoint=endpoint))
            with self.assertRaisesRegex(InvalidEvidence, '空白节点'):
                service.submit('101', 'E01')
        self.assertIsNone(self.service.query()['rule_applications'][0]['decision'])

    def test_parent_in_base_facts_is_a_technical_failure(self):
        with rdf_endpoint(BASE_TURTLE + '\n<https://example.org/event/E01> a p:SupplyDisruptionEvent .') as endpoint:
            service = DecisionService(replace(self.service.settings, endpoint=endpoint))
            with self.assertRaises(InvalidExperiment):
                service.submit('101', 'E01')
        self.assertIsNone(self.service.query()['rule_applications'][0]['decision'])

    def test_input_change_during_computation_cannot_become_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            rule = Path(directory) / 'R-01.rq'
            rule.write_bytes(self.service.settings.rule.read_bytes())
            def change_rule():
                rule.write_text(rule.read_text() + '\n# changed during computation\n')
            with rdf_endpoint(BASE_TURTLE, change_rule) as endpoint:
                service = DecisionService(replace(self.service.settings, endpoint=endpoint, rule=rule))
                with self.assertRaisesRegex(InvalidEvidence, '输入在计算期间发生变化'):
                    service.submit('101', 'E01')
        self.assertIsNone(self.service.query()['rule_applications'][0]['decision'])

    def test_core_must_match_the_recorded_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'reasoner').mkdir()
            (root / 'reasoner/core.py').write_text('# a different core implementation\n')
            subprocess.run(['git', 'init', '-q', directory], check=True)
            subprocess.run(['git', '-C', directory, 'add', '.'], check=True)
            subprocess.run(['git', '-C', directory, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            'commit', '-qm', 'fixture core'], check=True)
            commit = subprocess.check_output(['git', '-C', directory, 'rev-parse', 'HEAD']).decode().strip()
            service = DecisionService(replace(self.service.settings, code_commit=commit, git_dir=root / '.git'))
            with self.assertRaisesRegex(InvalidEvidence, '核心哈希与代码提交不一致'):
                service.submit('101', 'E01')
        self.assertIsNone(self.service.query()['rule_applications'][0]['decision'])

    def test_unrelated_changes_only_change_snapshot_integrity(self):
        first = self.service.submit('101', 'E01', '原说明')
        try:
            with self.admin.cursor() as cursor:
                cursor.execute('UPDATE decision_loop.decision_record SET occupancy=NULL')
                cursor.execute("UPDATE procurement.supplier SET supplier_name='新名称',supplier_status='ACTIVE' WHERE supplier_id=1")
                cursor.execute('UPDATE procurement.purchase_order SET amount=1234 WHERE order_id=101')
                cursor.execute("INSERT INTO procurement.purchase_order VALUES (104,2,1,'COMPLETED')")
                cursor.execute("INSERT INTO procurement.supply_disruption_event VALUES ('E99',2,'DeliverySuspensionEvent',FALSE)")
            second = self.service.submit('101', 'E01', '另一段说明')
            self.assertNotEqual(first['evidence']['facts_hash'], second['evidence']['facts_hash'])
            self.assertEqual(first['evidence']['precondition_hash'], second['evidence']['precondition_hash'])
            self.assertEqual(first['evidence']['bound_facts'], second['evidence']['bound_facts'])
        finally:
            with self.admin.cursor() as cursor:
                cursor.execute("UPDATE procurement.supplier SET supplier_name='华北钢材',supplier_status='DISABLED' WHERE supplier_id=1")
                cursor.execute('UPDATE procurement.purchase_order SET amount=50000 WHERE order_id=101')
                cursor.execute('DELETE FROM procurement.purchase_order WHERE order_id=104')
                cursor.execute("DELETE FROM procurement.supply_disruption_event WHERE event_id='E99'")

    def test_commit_and_agent_note_are_not_business_preconditions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'reasoner').mkdir()
            (root / 'reasoner/core.py').write_bytes(Path('reasoner/core.py').read_bytes())
            subprocess.run(['git', 'init', '-q', directory], check=True)
            subprocess.run(['git', '-C', directory, 'add', '.'], check=True)
            command = ['git', '-C', directory, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid']
            subprocess.run([*command, 'commit', '-qm', 'same core first commit'], check=True)
            first_commit = subprocess.check_output(['git', '-C', directory, 'rev-parse', 'HEAD']).decode().strip()
            subprocess.run([*command, 'commit', '--allow-empty', '-qm', 'same core second commit'], check=True)
            second_commit = subprocess.check_output(['git', '-C', directory, 'rev-parse', 'HEAD']).decode().strip()
            settings = replace(self.service.settings, git_dir=root / '.git', code_commit=first_commit)
            first = DecisionService(settings).submit('101', 'E01', '原说明')
            with self.admin.cursor() as cursor:
                cursor.execute('UPDATE decision_loop.decision_record SET occupancy=NULL')
            second = DecisionService(replace(settings, code_commit=second_commit)).submit('101', 'E01', '另一段说明')
        self.assertEqual(first['evidence']['facts_hash'], second['evidence']['facts_hash'])
        self.assertEqual(first['evidence']['precondition_hash'], second['evidence']['precondition_hash'])
        self.assertNotEqual(first['evidence']['code_commit'], second['evidence']['code_commit'])

    def test_evidence_contains_full_graph_and_complete_semantic_fingerprint(self):
        decision = self.service.submit('101', 'E01')
        evidence = decision['evidence']
        self.assertIn('https://example.org/purchase-order/102', evidence['facts_nt'])
        self.assertIn('https://example.org/purchase-order/103', evidence['facts_nt'])
        self.assertEqual(evidence['bound_facts'], {
            'order_status': 'PENDING', 'order_supplier': 'https://example.org/supplier/1',
            'event_is_current': True, 'affected_supplier': 'https://example.org/supplier/1',
            'event_direct_types': ['https://example.org/procurement#DeliverySuspensionEvent'],
        })
        fingerprint = evidence['semantic_fingerprint']
        self.assertEqual(set(fingerprint), {'mapping','ontop_ontology','ontology','base_query','rule','core','rdflib','owlrl'})
        self.assertEqual(fingerprint['rdflib'], '7.1.4')
        self.assertEqual(fingerprint['owlrl'], '7.1.4')
        self.assertIn('DeliverySuspensionEvent', evidence['ontology_text'])
        self.assertIn('CONSTRUCT', evidence['rule_text'])
        self.assertIn('Supplier', evidence['ontop_ontology_text'])

    def test_each_semantic_input_changes_preconditions_but_query_does_not_invalidate(self):
        baseline = self.service.submit('101', 'E01')
        with tempfile.TemporaryDirectory() as directory:
            for name in ('mapping', 'ontop_ontology', 'ontology', 'base_query', 'rule'):
                with self.subTest(input=name):
                    with self.admin.cursor() as cursor:
                        cursor.execute('UPDATE decision_loop.decision_record SET occupancy=NULL')
                    changed = Path(directory) / name
                    changed.write_bytes(getattr(self.service.settings, name).read_bytes() + b'\n')
                    service = DecisionService(replace(self.service.settings, **{name: changed}))
                    record = service.submit('101', 'E01')
                    self.assertNotEqual(baseline['evidence']['precondition_hash'], record['evidence']['precondition_hash'])
                    self.assertEqual(baseline['evidence']['bound_facts'], record['evidence']['bound_facts'])
                    self.assertEqual(self.service.query()['rule_applications'][0]['decision'], record)

    def test_agent_cannot_update_delete_insert_status_or_write_source_rows(self):
        record = self.service.submit('101', 'E01')
        settings = self.service.settings
        connection = pymysql.connect(host=settings.db_host, user=settings.db_user,
                                     password=settings.db_password, autocommit=True)
        try:
            # Infrastructure authorization probes, not assertions on SQL text.
            for statement in (
                "UPDATE decision_loop.decision_record SET evidence=JSON_OBJECT()",
                'DELETE FROM decision_loop.decision_record',
                "INSERT INTO decision_loop.decision_record (order_id,event_id,evidence,agent_note,description,approval_status) VALUES (103,'E01',JSON_OBJECT(),'','','APPROVED')",
                "UPDATE procurement.supplier SET supplier_status='DISABLED' WHERE supplier_id=1",
            ):
                with self.subTest(statement=statement.split()[0]):
                    with connection.cursor() as cursor, self.assertRaises(pymysql.err.OperationalError) as denied:
                        cursor.execute(statement)
                    self.assertIn(denied.exception.args[0], (1142, 1143))
        finally:
            connection.close()
        self.assertEqual(self.service.query()['rule_applications'][0]['decision'], record)

    def test_loaded_core_cannot_be_misrepresented_by_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('decision', 'reasoner', 'ontop', 'rules', 'queries'):
                shutil.copytree(name, root / name, ignore=shutil.ignore_patterns('__pycache__'))
            script = r"""
from pathlib import Path
from decision.service import DecisionService
from decision.evidence import InvalidEvidence
path = Path('reasoner/core.py')
path.write_text(path.read_text() + '\n# changed after module import\n')
try:
    DecisionService().submit('101', 'E01')
except InvalidEvidence as exc:
    assert '加载后发生变化' in str(exc), str(exc)
    print('runtime change rejected')
else:
    raise AssertionError('changed source was accepted as running code')
"""
            result = subprocess.run([sys.executable, '-B', '-c', script], cwd=root,
                                    env={**os.environ, 'PYTHONPATH': directory}, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'runtime change rejected')
        self.assertIsNone(self.service.query()['rule_applications'][0]['decision'])

    def test_known_full_snapshot_has_sorted_ntriples_sha256(self):
        with rdf_endpoint(BASE_TURTLE) as endpoint:
            service = DecisionService(replace(self.service.settings, endpoint=endpoint))
            evidence = service.submit('101', 'E01')['evidence']
        self.assertEqual(len(evidence['facts_nt'].splitlines()), 7)
        self.assertEqual(evidence['facts_hash'], 'caff03227a294c08c433a5dd6fcc342cb6ae8264a7212f53159334425d3b0f48')
