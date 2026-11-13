"""Supervisor review acceptance through public decision operations."""
import logging
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pymysql

from decision.auth import Unauthorized
from decision.service import DecisionService, Settings
from decision.test_config import BUYER_TOKEN, DISABLED_TOKEN, SUPERVISOR_TOKEN
from reasoner import silence_dependency_warnings

def setUpModule():
    silence_dependency_warnings()


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.admin = pymysql.connect(host='mysql', user='root', password=os.environ['TEST_DB_PASSWORD'], autocommit=True)
        with self.admin.cursor() as cursor:
            cursor.execute('DELETE FROM decision_loop.execution_attempt')
            cursor.execute('DELETE FROM procurement.supply_risk_review_task')
            cursor.execute('DELETE FROM decision_loop.approval_record')
            cursor.execute('DELETE FROM decision_loop.decision_record')
        self.agent = DecisionService(Settings.from_env())
        self.manager = DecisionService(replace(self.agent.settings, db_user='decision_manager', db_password='demo_manager_db_password'))

    def tearDown(self):
        self.admin.close()

    def test_supervisor_can_view_evidence_and_reject_with_auditable_reason(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        shown = self.manager.show(decision['id'], token=SUPERVISOR_TOKEN)
        self.assertEqual(shown['evidence'], decision['evidence'])
        self.assertEqual(shown['agent_note'], {'text': 'Agent说明', 'is_evidence': False})
        self.assertEqual(shown['approval_status'], 'PENDING')
        self.assertIsNone(shown['approval'])
        with self.admin.cursor() as cursor:
            cursor.execute('SELECT NOW(6)')
            before = cursor.fetchone()[0]
        rejected = self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '证据不足以停止复核')
        with self.admin.cursor() as cursor:
            cursor.execute('SELECT NOW(6)')
            after = cursor.fetchone()[0]
        self.assertEqual(rejected['approval_status'], 'REJECTED')
        self.assertEqual(rejected['occupancy'], 1)
        self.assertIsNone(rejected['execution_status'])
        self.assertEqual(rejected['evidence'], decision['evidence'])
        approval = rejected['approval']
        self.assertEqual(approval['conclusion'], 'REJECT')
        self.assertEqual(approval['actor_name'], '演示采购主管')
        self.assertEqual(approval['actor_role'], '采购主管')
        self.assertEqual(approval['reason'], '证据不足以停止复核')
        recorded = approval['created_at']
        self.assertGreaterEqual(recorded, before.isoformat())
        self.assertLessEqual(recorded, after.isoformat())
        self.assertEqual(self.manager.show(decision['id'], token=SUPERVISOR_TOKEN), rejected)

    def test_unauthorized_tokens_do_not_show_or_change_the_decision(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        attempts = (
            ('缺少令牌', None),
            ('空令牌', ''),
            ('错误令牌', 'wrong-token'),
            ('采购员令牌', BUYER_TOKEN),
            ('停用用户令牌', DISABLED_TOKEN),
        )
        operations = (
            ('展示', lambda token: self.manager.show(decision['id'], token)),
            ('拒绝', lambda token: self.manager.reject(decision['id'], token, '不应留下审批')),
            ('批准', lambda token: self.manager.approve(decision['id'], token, '不应留下审批')),
        )
        for label, token in attempts:
            for name, operation in operations:
                with self.subTest(identity=label, operation=name):
                    with self.assertRaises(Unauthorized):
                        operation(token)
        current = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(current['approval_status'], 'PENDING')
        self.assertIsNone(current['approval'])
        self.assertIsNone(current['execution_status'])
        self.assertEqual(current['evidence'], decision['evidence'])

    def test_terminal_decision_rejects_another_approval_without_execution(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        rejected = self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '维持拒绝')
        repeated = self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '另一条理由')
        approved = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '改为批准')
        self.assertEqual(repeated, rejected)
        self.assertEqual(approved, rejected)
        self.assertEqual(approved['approval']['reason'], '维持拒绝')
        self.assertEqual(approved['approval']['conclusion'], 'REJECT')
        self.assertIsNone(approved['execution_status'])
        self.assertEqual(self.agent.submit('101', 'E01', '试图重开'), rejected)

    def test_blank_reason_does_not_create_an_approval(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        for reason in ('', '   ', '\n\t'):
            with self.subTest(reason=repr(reason)):
                with self.assertRaisesRegex(ValueError, '拒绝必须填写理由'):
                    self.manager.reject(decision['id'], SUPERVISOR_TOKEN, reason)
        current = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(current['approval_status'], 'PENDING')
        self.assertIsNone(current['approval'])
        self.assertEqual(current['evidence'], decision['evidence'])

    def test_reject_occupies_without_rechecking_reasoning(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        offline = DecisionService(replace(self.manager.settings, endpoint='http://127.0.0.1:1/sparql'))
        try:
            with self.admin.cursor() as cursor:
                cursor.execute("UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'")
            rejected = offline.reject(decision['id'], SUPERVISOR_TOKEN, '不先做失效复核')
            self.assertEqual(rejected['approval_status'], 'REJECTED')
            self.assertEqual(rejected['occupancy'], 1)
            self.assertIsNone(rejected['execution_status'])
            self.assertEqual(self.agent.query()['rule_applications'], [])
        finally:
            with self.admin.cursor() as cursor:
                cursor.execute("UPDATE procurement.supply_disruption_event SET is_current=TRUE WHERE event_id='E01'")
        occupied = self.agent.query()['rule_applications'][0]['decision']
        self.assertEqual(occupied['id'], decision['id'])
        self.assertEqual(occupied['approval_status'], 'REJECTED')
        self.assertEqual(occupied['approval']['reason'], '不先做失效复核')

    def test_query_reports_rejection_without_changing_it(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        rejected = self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '不采纳这条建议')
        result = self.agent.query()
        application = result['rule_applications'][0]
        self.assertEqual(result['conclusion'], '需要供应风险复核')
        self.assertEqual(application['order'], '101')
        self.assertEqual(application['event'], 'E01')
        self.assertEqual(application['decision']['approval']['actor_name'], '演示采购主管')
        self.assertEqual(application['decision']['approval']['reason'], '不采纳这条建议')
        self.assertEqual(application['decision'], rejected)
        self.assertEqual(self.manager.show(decision['id'], SUPERVISOR_TOKEN), rejected)

    def test_recorded_role_remains_after_the_user_role_changes(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '记录当时角色')
        try:
            with self.admin.cursor() as cursor:
                cursor.execute("UPDATE decision_loop.app_user SET role='采购员' WHERE id=1")
            attached = self.agent.query()['rule_applications'][0]['decision']
            self.assertEqual(attached['approval']['actor_role'], '采购主管')
            self.assertEqual(attached['approval']['actor_name'], '演示采购主管')
        finally:
            with self.admin.cursor() as cursor:
                cursor.execute("UPDATE decision_loop.app_user SET role='采购主管' WHERE id=1")

    def test_concurrent_rejects_leave_one_approval(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(self.manager.reject, decision['id'], SUPERVISOR_TOKEN, f'并发理由{n}')
                for n in range(4)
            ]
            results = [future.result(timeout=30) for future in futures]
        approval_ids = {item['approval']['id'] for item in results}
        self.assertEqual(approval_ids, {results[0]['approval']['id']})
        self.assertTrue(all(item['approval_status'] == 'REJECTED' for item in results))
        self.assertTrue(all(item['occupancy'] == 1 for item in results))
        self.assertTrue(all(item['execution_status'] is None for item in results))
        shown = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(shown['approval']['id'], results[0]['approval']['id'])
        self.assertEqual(shown['approval']['conclusion'], 'REJECT')

    def test_plaintext_tokens_stay_out_of_database_and_logs(self):
        tokens = (SUPERVISOR_TOKEN, BUYER_TOKEN, DISABLED_TOKEN)
        captured = []

        class Capture(logging.Handler):
            def emit(self, record):
                captured.append(self.format(record))

        handler = Capture()
        module_log = logging.getLogger('decision')
        previous_level = module_log.level
        module_log.setLevel(logging.INFO)
        module_log.addHandler(handler)
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='decision_loop'")
            columns = {row[0] for row in cursor.fetchall()}
            cursor.execute("SHOW GLOBAL VARIABLES WHERE Variable_name IN ('general_log','log_output')")
            previous = dict(cursor.fetchall())
            cursor.execute("SET GLOBAL log_output='TABLE'")
            cursor.execute("SET GLOBAL general_log='ON'")
            cursor.execute('TRUNCATE TABLE mysql.general_log')
        try:
            decision = self.agent.submit('101', 'E01', 'Agent说明')
            self.manager.show(decision['id'], SUPERVISOR_TOKEN)
            with self.assertRaises(Unauthorized):
                self.manager.reject(decision['id'], 'wrong-token', '错误令牌不应留下审批')
            self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '日志中不应出现令牌')
            with self.admin.cursor() as cursor:
                cursor.execute('SELECT argument FROM mysql.general_log')
                database_log = '\n'.join(str(row[0]) for row in cursor.fetchall())
                cursor.execute(
                    'SELECT name, role, token_hash FROM decision_loop.app_user '
                    'UNION ALL SELECT actor_name, actor_role, comment FROM decision_loop.approval_record'
                )
                stored = str(cursor.fetchall())
        finally:
            module_log.removeHandler(handler)
            module_log.setLevel(previous_level)
            with self.admin.cursor() as cursor:
                cursor.execute("SET GLOBAL general_log=%s", (previous.get('general_log', 'OFF'),))
                cursor.execute("SET GLOBAL log_output=%s", (previous.get('log_output', 'FILE'),))
        self.assertIn('token_hash', columns)
        self.assertNotIn('token', columns)
        self.assertTrue(captured)
        visible = database_log + '\n' + stored + '\n' + '\n'.join(captured)
        for token in tokens:
            self.assertNotIn(token, visible)

    def test_review_accounts_cannot_rewrite_evidence_or_sources(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        rejected = self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '权限探针前的拒绝')
        accounts = (
            (self.agent.settings.db_user, self.agent.settings.db_password),
            ('decision_manager', 'demo_manager_db_password'),
        )
        statements = (
            "UPDATE decision_loop.decision_record SET evidence=JSON_OBJECT()",
            "UPDATE decision_loop.approval_record SET comment='改写审批'",
            'DELETE FROM decision_loop.approval_record',
            'DELETE FROM decision_loop.decision_record',
            "UPDATE procurement.supplier SET supplier_name='被改写' WHERE supplier_id=1",
            "UPDATE procurement.purchase_order SET order_status='CANCELLED' WHERE order_id=101",
            "UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'",
        )
        for user, password in accounts:
            connection = pymysql.connect(
                host=self.agent.settings.db_host, user=user, password=password, autocommit=True,
            )
            try:
                for statement in statements:
                    with self.subTest(user=user, statement=statement.split()[0]):
                        with connection.cursor() as cursor, self.assertRaises(pymysql.err.OperationalError) as denied:
                            cursor.execute(statement)
                        self.assertIn(denied.exception.args[0], (1142, 1143))
            finally:
                connection.close()
        with self.admin.cursor() as cursor:
            cursor.execute("UPDATE procurement.supplier SET supplier_name='华北钢材' WHERE supplier_id=1")
            cursor.execute("UPDATE procurement.purchase_order SET order_status='PENDING' WHERE order_id=101")
            cursor.execute("UPDATE procurement.supply_disruption_event SET is_current=TRUE WHERE event_id='E01'")
        self.assertEqual(self.manager.show(decision['id'], SUPERVISOR_TOKEN), rejected)
