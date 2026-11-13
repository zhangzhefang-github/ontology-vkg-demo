"""按任务编号追溯，只通过决策模块的公开操作观察。"""
import os
import unittest
from dataclasses import replace

import pymysql

from decision.service import DecisionService, Settings
from decision.test_config import SUPERVISOR_TOKEN
from decision.test_execute import VALID_TURTLE, scripted_endpoint
from reasoner import silence_dependency_warnings


def setUpModule():
    silence_dependency_warnings()


READER_PASSWORD = 'demo_reader_db_password'


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.admin = pymysql.connect(
            host='mysql', user='root', password=os.environ['TEST_DB_PASSWORD'], autocommit=True,
        )
        self._clear_decisions()
        self.agent = DecisionService(Settings.from_env())
        self.manager = DecisionService(replace(
            self.agent.settings, db_user='decision_manager', db_password='demo_manager_db_password',
        ))
        self.reader = DecisionService(replace(
            self.agent.settings, db_user='decision_reader', db_password=READER_PASSWORD,
        ))

    def tearDown(self):
        try:
            self._clear_decisions()
            self._restore_business()
        finally:
            self.admin.close()

    def _restore_business(self):
        with self.admin.cursor() as cursor:
            cursor.execute(
                "INSERT INTO procurement.supply_disruption_event "
                "(event_id, affected_supplier_id, event_type, is_current) "
                "VALUES ('E01',1,'DeliverySuspensionEvent',TRUE) "
                "ON DUPLICATE KEY UPDATE affected_supplier_id=1, event_type='DeliverySuspensionEvent', "
                "is_current=TRUE"
            )
            cursor.execute("DELETE FROM procurement.supply_disruption_event WHERE event_id<>'E01'")
            cursor.execute(
                "UPDATE procurement.purchase_order SET supplier_id=1, amount=50000, order_status='PENDING' "
                "WHERE order_id=101"
            )
            cursor.execute(
                "UPDATE procurement.purchase_order SET supplier_id=1, amount=30000, order_status='COMPLETED' "
                "WHERE order_id=102"
            )
            cursor.execute('DELETE FROM procurement.purchase_order WHERE order_id NOT IN (101,102,103)')
            cursor.execute(
                "UPDATE procurement.supplier SET supplier_name='华北钢材', supplier_status='DISABLED' "
                "WHERE supplier_id=1"
            )

    def _clear_decisions(self):
        with self.admin.cursor() as cursor:
            cursor.execute('DELETE FROM decision_loop.execution_attempt')
            cursor.execute('DELETE FROM procurement.supply_risk_review_task')
            cursor.execute('DELETE FROM decision_loop.approval_record')
            cursor.execute('DELETE FROM decision_loop.decision_record')

    def test_task_id_returns_its_decision_evidence_approval_and_attempt(self):
        note = '请主管查看；这段说明不是证据'
        decision = self.agent.submit('101', 'E01', note)
        approved = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '同意创建复核任务')
        before = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        traced = self.reader.trace(approved['task']['id'])
        after = self.manager.show(decision['id'], SUPERVISOR_TOKEN)

        self.assertEqual(before, after)
        self.assertTrue(traced['found'])
        self.assertEqual(traced['task'], approved['task'])
        self.assertEqual(traced['decision']['id'], decision['id'])
        self.assertEqual(traced['decision']['order'], '101')
        self.assertEqual(traced['decision']['event'], 'E01')
        self.assertEqual(traced['decision']['approval_status'], 'APPROVED')
        self.assertEqual(traced['decision']['execution_status'], 'SUCCEEDED')
        self.assertEqual(traced['fact_snapshot'], {
            'use': '发现快照是否被改写，不参与业务失效判断',
            'facts_nt': decision['evidence']['facts_nt'],
            'facts_hash': decision['evidence']['facts_hash'],
        })
        self.assertNotIn('precondition_hash', traced['fact_snapshot'])
        self.assertEqual(traced['business_precondition'], {
            'use': '判断绑定事实和语义版本是否变化',
            'bound_facts': decision['evidence']['bound_facts'],
            'precondition_hash': decision['evidence']['precondition_hash'],
            'semantic_fingerprint': decision['evidence']['semantic_fingerprint'],
        })
        self.assertNotIn('facts_nt', traced['business_precondition'])
        self.assertNotIn('facts_hash', traced['business_precondition'])
        self.assertNotEqual(traced['fact_snapshot']['use'], traced['business_precondition']['use'])
        self.assertEqual(traced['rule_application']['rule'], 'R-01')
        self.assertEqual(traced['rule_application']['order'], '101')
        self.assertEqual(traced['rule_application']['event'], 'E01')
        self.assertEqual(traced['rule_application']['record'], '绑定')
        self.assertEqual(
            traced['rule_application']['description'],
            '规则应用记录的是绑定，不是通用 OWL 证明。',
        )
        self.assertEqual(traced['reasoning_version']['use'], '供检出代码提交并离线重放当时的规则应用')
        self.assertEqual(traced['reasoning_version']['code_commit'], decision['evidence']['code_commit'])
        self.assertEqual(
            traced['reasoning_version']['semantic_fingerprint'],
            decision['evidence']['semantic_fingerprint'],
        )
        self.assertEqual(traced['reasoning_version']['ontology_text'], decision['evidence']['ontology_text'])
        self.assertEqual(
            traced['reasoning_version']['ontop_ontology_text'],
            decision['evidence']['ontop_ontology_text'],
        )
        self.assertEqual(traced['reasoning_version']['rule_text'], decision['evidence']['rule_text'])
        self.assertEqual(traced['approval']['conclusion'], 'APPROVE')
        self.assertEqual(traced['approval']['id'], approved['approval']['id'])
        self.assertEqual(traced['approval']['actor_name'], '演示采购主管')
        self.assertEqual(traced['approval']['actor_role'], '采购主管')
        self.assertEqual(traced['approval']['reason'], '同意创建复核任务')
        self.assertEqual(traced['agent_note'], {'text': note, 'is_evidence': False})
        self.assertEqual(len(traced['attempts']), 1)
        attempt = traced['attempts'][0]
        self.assertEqual(attempt['outcome'], 'SUCCEEDED')
        self.assertEqual(attempt['reason'], '已创建供应风险复核任务')
        self.assertEqual(attempt['mismatches'], [])
        self.assertEqual(attempt['semantic_fingerprint'], decision['evidence']['semantic_fingerprint'])
        self.assertEqual(attempt['code_commit'], decision['evidence']['code_commit'])
        self.assertEqual(attempt['task_id'], approved['task']['id'])
        self.assertEqual(self.reader.trace(approved['task']['id']), traced)
        self.assertEqual(self.agent.query()['rule_applications'][0]['order_status'], 'PENDING')

    def test_each_task_traces_only_its_bound_event_decision_and_approval(self):
        with self.admin.cursor() as cursor:
            cursor.execute(
                "INSERT INTO procurement.supply_disruption_event "
                "VALUES ('E02',1,'DeliverySuspensionEvent',TRUE)"
            )
        first = self.manager.approve(
            self.agent.submit('101', 'E01', '事件一说明')['id'], SUPERVISOR_TOKEN, '第一条',
        )
        second = self.manager.approve(
            self.agent.submit('101', 'E02', '事件二说明')['id'], SUPERVISOR_TOKEN, '第二条',
        )
        traced_first = self.reader.trace(first['task']['id'])
        traced_second = self.reader.trace(second['task']['id'])
        self.assertNotEqual(first['task']['id'], second['task']['id'])
        self.assertEqual(traced_first['task']['event'], 'E01')
        self.assertEqual(traced_second['task']['event'], 'E02')
        self.assertEqual(traced_first['decision']['id'], first['id'])
        self.assertEqual(traced_first['decision']['event'], 'E01')
        self.assertEqual(traced_second['decision']['id'], second['id'])
        self.assertEqual(traced_second['decision']['event'], 'E02')
        self.assertEqual(traced_first['rule_application']['event'], 'E01')
        self.assertEqual(traced_second['rule_application']['event'], 'E02')
        self.assertEqual(traced_first['approval']['id'], first['approval']['id'])
        self.assertEqual(traced_first['approval']['decision_id'], first['id'])
        self.assertEqual(traced_first['approval']['reason'], '第一条')
        self.assertEqual(traced_second['approval']['id'], second['approval']['id'])
        self.assertEqual(traced_second['approval']['decision_id'], second['id'])
        self.assertEqual(traced_second['approval']['reason'], '第二条')
        self.assertNotEqual(traced_first['approval']['id'], traced_second['approval']['id'])
        self.assertEqual(traced_first['agent_note']['text'], '事件一说明')
        self.assertEqual(traced_second['agent_note']['text'], '事件二说明')
        self.assertEqual(traced_first['attempts'][0]['task_id'], first['task']['id'])
        self.assertEqual(traced_second['attempts'][0]['task_id'], second['task']['id'])

    def test_trace_keeps_existing_failed_and_successful_attempts(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        parent = VALID_TURTLE + '\n<https://example.org/event/E01> a p:SupplyDisruptionEvent .\n'
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'turtle': parent}]) as endpoint:
            failed = DecisionService(replace(self.manager.settings, endpoint=endpoint)).approve(
                decision['id'], SUPERVISOR_TOKEN, '技术失败',
            )
        self.assertEqual(failed['execution_status'], 'FAILED')
        self.assertIsNone(failed['task'])
        succeeded = self.manager.execute(decision['id'])
        traced = self.reader.trace(succeeded['task']['id'])
        self.assertEqual([item['outcome'] for item in traced['attempts']], ['FAILED', 'SUCCEEDED'])
        self.assertEqual(traced['attempts'][0]['reason'], failed['attempts'][0]['reason'])
        self.assertEqual(traced['attempts'][0]['mismatches'], failed['attempts'][0]['mismatches'])
        self.assertEqual(
            traced['attempts'][0]['semantic_fingerprint'],
            failed['attempts'][0]['semantic_fingerprint'],
        )
        self.assertEqual(traced['attempts'][0]['code_commit'], failed['attempts'][0]['code_commit'])
        self.assertIsNone(traced['attempts'][0]['task_id'])
        self.assertEqual(traced['attempts'][1]['outcome'], 'SUCCEEDED')
        self.assertEqual(traced['attempts'][1]['reason'], '已创建供应风险复核任务')
        self.assertEqual(traced['attempts'][1]['mismatches'], [])
        self.assertEqual(
            traced['attempts'][1]['semantic_fingerprint'],
            decision['evidence']['semantic_fingerprint'],
        )
        self.assertEqual(traced['attempts'][1]['code_commit'], decision['evidence']['code_commit'])
        self.assertEqual(traced['attempts'][1]['task_id'], succeeded['task']['id'])
        shown = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(self.reader.trace(succeeded['task']['id'])['attempts'], shown['attempts'])
        self.assertEqual(shown['execution_status'], 'SUCCEEDED')

    def test_unknown_task_id_returns_not_found_without_changes(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        approved = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '同意')
        before = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        missing_id = approved['task']['id'] + 1_000_000
        self.assertEqual(self.reader.trace(missing_id), {
            'found': False,
            'task_id': missing_id,
            'message': '未找到供应风险复核任务',
        })
        self.assertEqual(self.manager.show(decision['id'], SUPERVISOR_TOKEN), before)
