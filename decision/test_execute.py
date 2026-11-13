"""批准与执行通过决策模块的公开操作验收。"""
import contextlib
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pymysql

from decision.auth import Unauthorized
from decision.database import attach_execution, connect, decision_by_id
from decision.service import CheckIncomplete, DecisionService, ExecutionRejected, Settings
from decision.test_config import BUYER_TOKEN, SUPERVISOR_TOKEN
from reasoner import silence_dependency_warnings


def setUpModule():
    silence_dependency_warnings()


VALID_TURTLE = """
@prefix p: <https://example.org/procurement#> .
<https://example.org/purchase-order/101> a p:PurchaseOrder ;
    p:orderedFrom <https://example.org/supplier/1> ; p:orderStatus "PENDING" .
<https://example.org/supplier/1> p:supplierName "华北钢材" .
<https://example.org/event/E01> a p:DeliverySuspensionEvent ;
    p:affectsSupplier <https://example.org/supplier/1> ; p:isCurrent true .
"""

TASK_COLUMNS = [
    'id', 'source_decision_id', 'approval_id', 'order_id', 'event_id',
    'assigned_role', 'description', 'created_at',
]


@contextlib.contextmanager
def scripted_endpoint(responses):
    """responses 的每一项在对应一次 SPARQL 请求时返回。"""
    state = {'index': 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if length:
                self.rfile.read(length)
            item = responses[min(state['index'], len(responses) - 1)]
            state['index'] += 1
            if item.get('mutate'):
                item['mutate']()
            if item.get('fail'):
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'unavailable')
                return
            payload = item['turtle'].encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/turtle')
            self.send_header('Content-Length', str(len(payload)))
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


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.admin = pymysql.connect(
            host='mysql', user='root', password=os.environ['TEST_DB_PASSWORD'], autocommit=True,
        )
        self._clear_decisions()
        self.agent = DecisionService(Settings.from_env())
        self.manager = DecisionService(replace(
            self.agent.settings, db_user='decision_manager', db_password='demo_manager_db_password',
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

    def _sql(self, statement):
        with self.admin.cursor() as cursor:
            cursor.execute(statement)

    def _manager(self, endpoint):
        return DecisionService(replace(self.manager.settings, endpoint=endpoint))

    def test_approve_creates_exactly_one_auditable_task(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with self.admin.cursor() as cursor:
            cursor.execute('SELECT NOW(6)')
            before = cursor.fetchone()[0]
        approved = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '同意创建复核任务')
        with self.admin.cursor() as cursor:
            cursor.execute('SELECT NOW(6)')
            after = cursor.fetchone()[0]
        self.assertEqual(approved['approval_status'], 'APPROVED')
        self.assertEqual(approved['execution_status'], 'SUCCEEDED')
        self.assertEqual(approved['occupancy'], 1)
        self.assertIsNone(approved['invalidation_reason'])
        self.assertEqual(approved['evidence'], decision['evidence'])
        approval = approved['approval']
        self.assertEqual(approval['conclusion'], 'APPROVE')
        self.assertEqual(approval['actor_name'], '演示采购主管')
        self.assertEqual(approval['actor_role'], '采购主管')
        self.assertEqual(approval['reason'], '同意创建复核任务')
        self.assertGreaterEqual(approval['created_at'], before.isoformat())
        self.assertLessEqual(approval['created_at'], after.isoformat())
        task = approved['task']
        self.assertEqual(set(task), {
            'id', 'source_decision_id', 'approval_id', 'order', 'event',
            'assigned_role', 'description', 'created_at',
        })
        self.assertEqual(task['source_decision_id'], decision['id'])
        self.assertEqual(task['approval_id'], approval['id'])
        self.assertEqual(task['order'], '101')
        self.assertEqual(task['event'], 'E01')
        self.assertEqual(task['assigned_role'], '采购员')
        self.assertEqual(task['description'], '订单 101 因事件 E01 需要供应风险复核。')
        self.assertNotIn('同意创建复核任务', task['description'])
        self.assertNotIn('无法交付', task['description'])
        self.assertGreaterEqual(task['created_at'], before.isoformat())
        self.assertLessEqual(task['created_at'], after.isoformat())
        self.assertEqual(len(approved['attempts']), 1)
        attempt = approved['attempts'][0]
        self.assertEqual(attempt['outcome'], 'SUCCEEDED')
        self.assertEqual(attempt['task_id'], task['id'])
        self.assertEqual(attempt['mismatches'], [])
        self.assertEqual(attempt['semantic_fingerprint'], decision['evidence']['semantic_fingerprint'])
        self.assertEqual(attempt['code_commit'], decision['evidence']['code_commit'])
        self.assertGreaterEqual(attempt['created_at'], before.isoformat())
        self.assertLessEqual(attempt['created_at'], after.isoformat())
        self.assertEqual(self.agent.query()['rule_applications'][0]['order_status'], 'PENDING')
        self.assertEqual(self.manager.show(decision['id'], SUPERVISOR_TOKEN), approved)

    def test_repeat_approve_and_execute_do_not_write(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        approved = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '同意')
        offline = DecisionService(replace(self.manager.settings, endpoint='http://127.0.0.1:1/sparql'))
        repeated = offline.approve(decision['id'], SUPERVISOR_TOKEN, '另一条意见')
        executed = offline.execute(decision['id'])
        self.assertEqual(repeated['approval']['id'], approved['approval']['id'])
        self.assertEqual(repeated['approval']['reason'], '同意')
        self.assertEqual(repeated['task']['id'], approved['task']['id'])
        self.assertEqual(repeated['attempts'], approved['attempts'])
        self.assertEqual(executed, repeated)
        self.assertEqual(self.agent.submit('101', 'E01', '不能另开一条'), {
            **approved, 'task': None, 'attempts': [],
        })

    def test_terminal_rejection_is_not_executed(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        rejected = self.manager.reject(decision['id'], SUPERVISOR_TOKEN, '维持拒绝')
        approved = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '改为批准')
        self.assertEqual(approved['approval_status'], 'REJECTED')
        self.assertEqual(approved['approval']['conclusion'], 'REJECT')
        self.assertIsNone(approved['execution_status'])
        self.assertIsNone(approved['task'])
        self.assertEqual(approved['attempts'], [])
        with self.assertRaises(ExecutionRejected):
            self.manager.execute(decision['id'])
        self.assertEqual(self.manager.show(decision['id'], SUPERVISOR_TOKEN)['approval']['id'], rejected['approval']['id'])

    def test_stale_event_after_the_outside_check_is_not_approved(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')

        def expire_event():
            self._sql("UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'")

        with scripted_endpoint([{'turtle': VALID_TURTLE, 'mutate': expire_event}]) as endpoint:
            invalidated = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '不应批准过时事实')
        self.assertEqual(invalidated['approval_status'], 'INVALIDATED')
        self.assertIsNone(invalidated['approval'])
        self.assertIsNone(invalidated['execution_status'])
        self.assertIsNone(invalidated['occupancy'])
        self.assertIsNone(invalidated['task'])
        self.assertEqual(invalidated['attempts'], [])
        self.assertIn('事件不再当前有效', invalidated['invalidation_reason'])
        self.assertEqual(self.agent.query()['rule_applications'], [])

    def test_writer_commit_while_approval_waits_is_not_approved(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        holder = pymysql.connect(
            host='mysql', user='root', password=os.environ['TEST_DB_PASSWORD'], autocommit=False,
        )
        try:
            with holder.cursor() as cursor:
                cursor.execute(
                    "SELECT event_id FROM procurement.supply_disruption_event WHERE event_id='E01' FOR UPDATE"
                )
            with scripted_endpoint([{'turtle': VALID_TURTLE}]) as endpoint:
                future_box = {}

                def approve():
                    future_box['result'] = self._manager(endpoint).approve(
                        decision['id'], SUPERVISOR_TOKEN, '等待期间事实会变',
                    )

                worker = threading.Thread(target=approve)
                worker.start()
                deadline = time.time() + 20
                waiting = 0
                while time.time() < deadline:
                    with self.admin.cursor() as cursor:
                        cursor.execute('SELECT COUNT(*) FROM performance_schema.data_lock_waits')
                        waiting = cursor.fetchone()[0]
                    if waiting:
                        break
                    time.sleep(0.1)
                self.assertGreater(waiting, 0, '批准事务没有停在事件共享锁上')
                with holder.cursor() as cursor:
                    cursor.execute(
                        "UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'"
                    )
                holder.commit()
                worker.join(timeout=30)
            self.assertFalse(worker.is_alive())
            invalidated = future_box['result']
            self.assertEqual(invalidated['approval_status'], 'INVALIDATED')
            self.assertIsNone(invalidated['approval'])
            self.assertIn('事件不再当前有效', invalidated['invalidation_reason'])
        finally:
            holder.rollback()
            holder.close()

    def test_incomplete_check_keeps_the_decision_pending(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        offline = self._manager('http://127.0.0.1:1/sparql')
        with self.assertRaises(CheckIncomplete):
            offline.approve(decision['id'], SUPERVISOR_TOKEN, '服务不可用')
        current = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(current['approval_status'], 'PENDING')
        self.assertIsNone(current['approval'])
        self.assertIsNone(current['execution_status'])
        self.assertEqual(current['occupancy'], 1)
        self.assertEqual(current['evidence'], decision['evidence'])
        parent = VALID_TURTLE + '\n<https://example.org/event/E01> a p:SupplyDisruptionEvent .\n'
        with scripted_endpoint([{'turtle': parent}]) as endpoint:
            with self.assertRaises(CheckIncomplete):
                self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '父类不是业务失效')
        current = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(current['approval_status'], 'PENDING')
        self.assertIsNone(current['approval'])

    def test_missing_bound_row_invalidates_even_when_compute_fails(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        self._sql("DELETE FROM procurement.supply_disruption_event WHERE event_id='E01'")
        offline = self._manager('http://127.0.0.1:1/sparql')
        invalidated = offline.approve(decision['id'], SUPERVISOR_TOKEN, '行已经不在')
        self.assertEqual(invalidated['approval_status'], 'INVALIDATED')
        self.assertIsNone(invalidated['approval'])
        self.assertIsNone(invalidated['occupancy'])
        self.assertIn('绑定的事件行已经不存在', invalidated['invalidation_reason'])
        self.assertEqual(invalidated['attempts'], [])

    def test_changes_after_approval_block_execution_without_a_task(self):
        cases = (
            (
                '事件不再当前有效',
                "UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'",
                VALID_TURTLE.replace('p:isCurrent true', 'p:isCurrent false'),
                '事件不再当前有效',
            ),
            (
                '订单状态变化',
                "UPDATE procurement.purchase_order SET order_status='COMPLETED' WHERE order_id=101",
                VALID_TURTLE.replace('"PENDING"', '"COMPLETED"'),
                '订单状态变化',
            ),
            (
                '订单的供应商变化',
                'UPDATE procurement.purchase_order SET supplier_id=2 WHERE order_id=101',
                VALID_TURTLE.replace(
                    'p:orderedFrom <https://example.org/supplier/1>',
                    'p:orderedFrom <https://example.org/supplier/2>',
                ),
                '订单的供应商变化',
            ),
            (
                '事件影响的供应商变化',
                'UPDATE procurement.supply_disruption_event SET affected_supplier_id=2 WHERE event_id=\'E01\'',
                VALID_TURTLE.replace(
                    'p:affectsSupplier <https://example.org/supplier/1>',
                    'p:affectsSupplier <https://example.org/supplier/2>',
                ),
                '事件影响的供应商变化',
            ),
            (
                '事件的具体类型变化',
                "UPDATE procurement.supply_disruption_event SET event_type='AnotherConcreteEvent' WHERE event_id='E01'",
                VALID_TURTLE.replace('p:DeliverySuspensionEvent', 'p:AnotherConcreteEvent'),
                '事件的具体类型变化',
            ),
        )
        for label, statement, turtle, mismatch in cases:
            with self.subTest(change=label):
                self._clear_decisions()
                self.tearDown()
                self.setUp()
                decision = self.agent.submit('101', 'E01', 'Agent说明')

                def mutate(statement=statement):
                    self._sql(statement)

                with scripted_endpoint([
                    {'turtle': VALID_TURTLE},
                    {'turtle': turtle, 'mutate': mutate},
                ]) as endpoint:
                    blocked = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '批准后事实变化')
                self.assertEqual(blocked['approval_status'], 'APPROVED')
                self.assertEqual(blocked['approval']['conclusion'], 'APPROVE')
                self.assertEqual(blocked['execution_status'], 'BLOCKED')
                self.assertIsNone(blocked['occupancy'])
                self.assertIsNone(blocked['task'])
                self.assertIn(mismatch, blocked['attempts'][-1]['mismatches'])
                self.assertEqual(blocked['attempts'][-1]['outcome'], 'BLOCKED')
                self.assertIsNone(blocked['attempts'][-1]['task_id'])
                if mismatch == '事件不再当前有效':
                    self.assertIn('规则应用不再成立', blocked['attempts'][-1]['mismatches'])
                repeated = self.manager.execute(decision['id'])
                self.assertEqual(repeated['attempts'], blocked['attempts'])
                self.assertEqual(repeated['execution_status'], 'BLOCKED')
                self._restore_business()
                reopened = self.agent.submit('101', 'E01', '占位已释放')
                self.assertNotEqual(reopened['id'], decision['id'])
                self.assertEqual(reopened['approval_status'], 'PENDING')

    def test_multiple_direct_types_block_execution(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        doubled = VALID_TURTLE + '\n<https://example.org/event/E01> a p:AnotherConcreteEvent .\n'
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'turtle': doubled}]) as endpoint:
            blocked = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '两个直接类型')
        self.assertEqual(blocked['approval_status'], 'APPROVED')
        self.assertEqual(blocked['execution_status'], 'BLOCKED')
        self.assertIsNone(blocked['task'])
        self.assertIsNone(blocked['occupancy'])
        self.assertIn('直接类型多于一个', blocked['attempts'][-1]['mismatches'])

    def test_fingerprint_change_blocks_the_retry(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'fail': True}]) as endpoint:
            failed = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '先留下批准')
        self.assertEqual(failed['approval_status'], 'APPROVED')
        self.assertEqual(failed['execution_status'], 'FAILED')
        self.assertEqual(failed['occupancy'], 1)
        self.assertIsNone(failed['task'])
        self.assertEqual(failed['attempts'][0]['outcome'], 'FAILED')
        self.assertEqual(failed['attempts'][0]['mismatches'], [])
        changed_rule = Path(self.manager.settings.rule).read_text(encoding='utf-8') + '\n# 指纹变化\n'
        copy = Path('/tmp/R-01-changed.rq')
        copy.write_text(changed_rule, encoding='utf-8')
        shifted = DecisionService(replace(self.manager.settings, rule=copy))
        blocked = shifted.execute(decision['id'])
        self.assertEqual(blocked['approval_status'], 'APPROVED')
        self.assertEqual(blocked['execution_status'], 'BLOCKED')
        self.assertIsNone(blocked['task'])
        self.assertIsNone(blocked['occupancy'])
        self.assertIn('语义版本指纹变化', blocked['attempts'][-1]['mismatches'])
        self.assertNotEqual(
            blocked['attempts'][-1]['semantic_fingerprint'], decision['evidence']['semantic_fingerprint'],
        )
        self.assertEqual(len(blocked['attempts']), 2)

    def test_technical_failure_preserves_occupancy_until_retry_succeeds(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        parent = VALID_TURTLE + '\n<https://example.org/event/E01> a p:SupplyDisruptionEvent .\n'
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'turtle': parent}]) as endpoint:
            failed = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '技术失败')
        self.assertEqual(failed['execution_status'], 'FAILED')
        self.assertEqual(failed['approval_status'], 'APPROVED')
        self.assertEqual(failed['occupancy'], 1)
        self.assertIsNone(failed['task'])
        self.assertNotIn('已失效', failed['attempts'][-1]['reason'])
        retried = self.manager.execute(decision['id'])
        self.assertEqual(retried['execution_status'], 'SUCCEEDED')
        self.assertEqual(retried['occupancy'], 1)
        self.assertIsNotNone(retried['task'])
        self.assertEqual(len(retried['attempts']), 2)
        self.assertEqual(retried['attempts'][1]['outcome'], 'SUCCEEDED')
        self.assertEqual(retried['attempts'][1]['task_id'], retried['task']['id'])
        again = self.manager.execute(decision['id'])
        self.assertEqual(again['task']['id'], retried['task']['id'])
        self.assertEqual(again['attempts'], retried['attempts'])

    def test_unrelated_changes_do_not_block_the_task(self):
        self._sql("UPDATE procurement.supplier SET supplier_name='临时名称', supplier_status='ACTIVE' WHERE supplier_id=1")
        self._sql('UPDATE procurement.purchase_order SET amount=1 WHERE order_id=101')
        self._sql("UPDATE procurement.purchase_order SET order_status='CANCELLED' WHERE order_id=102")
        self._sql("INSERT INTO procurement.supply_disruption_event VALUES ('E99',2,'DeliverySuspensionEvent',FALSE)")
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        approved = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '这些变化无关')
        self.assertEqual(approved['execution_status'], 'SUCCEEDED')
        self.assertEqual(approved['task']['order'], '101')
        self.assertEqual(approved['task']['event'], 'E01')
        self.assertEqual(self.agent.query()['rule_applications'][0]['order_status'], 'PENDING')

    def test_two_current_events_create_two_tasks(self):
        self._sql("INSERT INTO procurement.supply_disruption_event VALUES ('E02',1,'DeliverySuspensionEvent',TRUE)")
        first = self.manager.approve(self.agent.submit('101', 'E01')['id'], SUPERVISOR_TOKEN, '第一条')
        second = self.manager.approve(self.agent.submit('101', 'E02')['id'], SUPERVISOR_TOKEN, '第二条')
        self.assertNotEqual(first['task']['id'], second['task']['id'])
        self.assertEqual(first['task']['event'], 'E01')
        self.assertEqual(second['task']['event'], 'E02')
        self.assertEqual(first['execution_status'], 'SUCCEEDED')
        self.assertEqual(second['execution_status'], 'SUCCEEDED')

    def test_concurrent_approvals_leave_one_approval_and_one_task(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(self.manager.approve, decision['id'], SUPERVISOR_TOKEN, f'并发批准{n}')
                for n in range(4)
            ]
            results = [future.result(timeout=120) for future in futures]
        approval_ids = {item['approval']['id'] for item in results}
        self.assertEqual(len(approval_ids), 1)
        self.assertTrue(all(item['approval']['conclusion'] == 'APPROVE' for item in results))
        shown = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(shown['execution_status'], 'SUCCEEDED')
        self.assertEqual(shown['task']['source_decision_id'], decision['id'])
        self.assertEqual(
            [item['outcome'] for item in shown['attempts']],
            ['SUCCEEDED'],
        )

    def test_concurrent_retries_leave_one_task(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'fail': True}]) as endpoint:
            failed = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '待重试')
        self.assertEqual(failed['execution_status'], 'FAILED')
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(self.manager.execute, decision['id']) for _ in range(4)]
            results = [future.result(timeout=120) for future in futures]
        task_ids = {item['task']['id'] for item in results}
        self.assertEqual(len(task_ids), 1)
        shown = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual([item['outcome'] for item in shown['attempts']], ['FAILED', 'SUCCEEDED'])

    def test_pending_rejected_and_unapproved_records_cannot_execute(self):
        pending = self.agent.submit('101', 'E01', 'Agent说明')
        with self.assertRaises(ExecutionRejected):
            self.manager.execute(pending['id'])
        self.assertEqual(self.manager.show(pending['id'], SUPERVISOR_TOKEN)['attempts'], [])
        self._sql(
            "UPDATE decision_loop.decision_record SET approval_status='APPROVED', execution_status='NOT_STARTED' "
            f"WHERE id={pending['id']}"
        )
        with self.assertRaisesRegex(ExecutionRejected, '审批结论不是批准'):
            self.manager.execute(pending['id'])
        self.assertIsNone(self.manager.show(pending['id'], SUPERVISOR_TOKEN)['task'])
        self._sql(
            "UPDATE decision_loop.decision_record SET approval_status='PENDING', execution_status=NULL "
            f"WHERE id={pending['id']}"
        )
        rejected = self.manager.reject(pending['id'], SUPERVISOR_TOKEN, '先拒绝')
        self._sql(
            "UPDATE decision_loop.decision_record SET approval_status='APPROVED', execution_status='NOT_STARTED' "
            f"WHERE id={pending['id']}"
        )
        with self.assertRaisesRegex(ExecutionRejected, '审批结论不是批准'):
            self.manager.execute(pending['id'])
        shown = self.manager.show(pending['id'], SUPERVISOR_TOKEN)
        self.assertEqual(shown['approval']['conclusion'], 'REJECT')
        self.assertEqual(shown['approval']['id'], rejected['approval']['id'])
        self.assertIsNone(shown['task'])
        self.assertEqual(shown['attempts'], [])

    def test_execute_command_ignores_action_and_keeps_one_approval(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')

        class ApproveWithoutExecution(DecisionService):
            def execute(self, decision_id, **ignored_action_parameters):
                connection = connect(self.settings)
                try:
                    current = decision_by_id(connection, decision_id)
                    connection.rollback()
                    return attach_execution(connection, current)
                finally:
                    connection.close()

        approved = ApproveWithoutExecution(self.manager.settings).approve(
            decision['id'], SUPERVISOR_TOKEN, '先批准，执行另走命令',
        )
        self.assertEqual(approved['approval_status'], 'APPROVED')
        self.assertEqual(approved['execution_status'], 'NOT_STARTED')
        self.assertEqual(approved['occupancy'], 1)
        self.assertIsNone(approved['task'])
        self.assertEqual(approved['attempts'], [])
        executed = self.manager.execute(
            decision['id'],
            action='cancel_order',
            order='102',
            event='E99',
            assigned_role='采购主管',
            description='无法交付',
            evidence={'facts_hash': '0' * 64},
        )
        self.assertEqual(executed['approval']['id'], approved['approval']['id'])
        self.assertEqual(executed['approval']['conclusion'], 'APPROVE')
        self.assertEqual(executed['execution_status'], 'SUCCEEDED')
        self.assertEqual(executed['task']['order'], '101')
        self.assertEqual(executed['task']['event'], 'E01')
        self.assertEqual(executed['task']['assigned_role'], '采购员')
        self.assertEqual(executed['task']['description'], '订单 101 因事件 E01 需要供应风险复核。')
        self.assertNotIn('无法交付', executed['task']['description'])
        self.assertEqual(executed['evidence'], decision['evidence'])
        self.assertEqual(len(executed['attempts']), 1)
        self.assertEqual(executed['attempts'][0]['outcome'], 'SUCCEEDED')

    def test_ontop_outage_can_fail_twice_then_retry_creates_one_task(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with scripted_endpoint([
            {'turtle': VALID_TURTLE}, {'fail': True}, {'fail': True},
        ]) as endpoint:
            offline = self._manager(endpoint)
            failed = offline.approve(decision['id'], SUPERVISOR_TOKEN, 'Ontop 不可用')
            self.assertEqual(failed['approval_status'], 'APPROVED')
            self.assertEqual(failed['execution_status'], 'FAILED')
            self.assertEqual(failed['occupancy'], 1)
            self.assertIsNone(failed['task'])
            self.assertEqual([item['outcome'] for item in failed['attempts']], ['FAILED'])
            second = offline.execute(decision['id'])
        self.assertEqual(second['approval']['id'], failed['approval']['id'])
        self.assertEqual(second['execution_status'], 'FAILED')
        self.assertEqual(second['occupancy'], 1)
        self.assertIsNone(second['task'])
        self.assertEqual([item['outcome'] for item in second['attempts']], ['FAILED', 'FAILED'])
        self.assertNotEqual(second['attempts'][0]['id'], second['attempts'][1]['id'])
        self.assertEqual(
            second['attempts'][0]['semantic_fingerprint'], decision['evidence']['semantic_fingerprint'],
        )
        self.assertEqual(
            second['attempts'][1]['semantic_fingerprint'], decision['evidence']['semantic_fingerprint'],
        )
        recovered = self.manager.execute(decision['id'])
        self.assertEqual(recovered['approval']['id'], failed['approval']['id'])
        self.assertEqual(recovered['execution_status'], 'SUCCEEDED')
        self.assertEqual(recovered['occupancy'], 1)
        self.assertEqual(recovered['task']['order'], '101')
        self.assertEqual(recovered['task']['event'], 'E01')
        self.assertEqual(
            [item['outcome'] for item in recovered['attempts']],
            ['FAILED', 'FAILED', 'SUCCEEDED'],
        )
        self.assertEqual(recovered['attempts'][-1]['task_id'], recovered['task']['id'])
        shown = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(shown['task']['id'], recovered['task']['id'])
        self.assertEqual(shown['attempts'], recovered['attempts'])

    def test_retry_blocks_when_binding_changes_then_new_decision_can_be_approved(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'fail': True}]) as endpoint:
            failed = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '留下可重试的批准')
        self.assertEqual(failed['execution_status'], 'FAILED')
        self._sql("UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'")
        blocked = self.manager.execute(decision['id'])
        self.assertEqual(blocked['approval_status'], 'APPROVED')
        self.assertEqual(blocked['approval']['id'], failed['approval']['id'])
        self.assertEqual(blocked['execution_status'], 'BLOCKED')
        self.assertIsNone(blocked['occupancy'])
        self.assertIsNone(blocked['task'])
        self.assertEqual(blocked['attempts'][-1]['outcome'], 'BLOCKED')
        self.assertIn('事件不再当前有效', blocked['attempts'][-1]['mismatches'])
        repeated = self.manager.execute(decision['id'])
        self.assertEqual(repeated['attempts'], blocked['attempts'])
        self.assertEqual(repeated['attempts'][-1]['reason'], blocked['attempts'][-1]['reason'])
        self._restore_business()
        reopened = self.agent.submit('101', 'E01', '占位释放后重新建议')
        self.assertNotEqual(reopened['id'], decision['id'])
        approved = self.manager.approve(reopened['id'], SUPERVISOR_TOKEN, '重新审批')
        self.assertEqual(approved['approval_status'], 'APPROVED')
        self.assertEqual(approved['approval']['conclusion'], 'APPROVE')
        self.assertNotEqual(approved['approval']['id'], failed['approval']['id'])
        self.assertEqual(approved['execution_status'], 'SUCCEEDED')
        self.assertEqual(approved['task']['order'], '101')
        self.assertEqual(approved['task']['event'], 'E01')

    def test_missing_bound_row_blocks_retry_even_when_compute_fails(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'fail': True}]) as endpoint:
            failed = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '先失败')
        self.assertEqual(failed['execution_status'], 'FAILED')
        self._sql("DELETE FROM procurement.supply_disruption_event WHERE event_id='E01'")
        blocked = self._manager('http://127.0.0.1:1/sparql').execute(decision['id'])
        self.assertEqual(blocked['approval_status'], 'APPROVED')
        self.assertEqual(blocked['approval']['id'], failed['approval']['id'])
        self.assertEqual(blocked['execution_status'], 'BLOCKED')
        self.assertIsNone(blocked['occupancy'])
        self.assertIsNone(blocked['task'])
        self.assertIn('绑定的事件行已经不存在', blocked['attempts'][-1]['mismatches'])
        self.assertEqual(blocked['attempts'][-1]['outcome'], 'BLOCKED')
        self.assertEqual([item['outcome'] for item in blocked['attempts']], ['FAILED', 'BLOCKED'])
        repeated = self.manager.execute(decision['id'])
        self.assertEqual(repeated['attempts'], blocked['attempts'])

    def test_unrelated_changes_do_not_block_a_retry(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'fail': True}]) as endpoint:
            failed = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, '失败后改无关事实')
        self.assertEqual(failed['execution_status'], 'FAILED')
        original_hash = decision['evidence']['facts_hash']
        self._sql(
            "UPDATE procurement.supplier SET supplier_name='临时名称', supplier_status='ACTIVE' "
            "WHERE supplier_id=1"
        )
        self._sql('UPDATE procurement.purchase_order SET amount=1 WHERE order_id=101')
        self._sql("UPDATE procurement.purchase_order SET order_status='CANCELLED' WHERE order_id=102")
        self._sql(
            "INSERT INTO procurement.supply_disruption_event VALUES "
            "('E99',2,'DeliverySuspensionEvent',FALSE)"
        )
        retried = self.manager.execute(decision['id'])
        self.assertEqual(retried['approval']['id'], failed['approval']['id'])
        self.assertEqual(retried['execution_status'], 'SUCCEEDED')
        self.assertEqual(retried['evidence']['facts_hash'], original_hash)
        self.assertNotEqual(retried['evidence']['facts_hash'], retried['evidence']['precondition_hash'])
        self.assertEqual(retried['task']['order'], '101')
        self.assertEqual(retried['task']['event'], 'E01')
        self.assertEqual(retried['task']['description'], '订单 101 因事件 E01 需要供应风险复核。')
        self.assertEqual([item['outcome'] for item in retried['attempts']], ['FAILED', 'SUCCEEDED'])
        self.assertEqual(self.agent.query()['rule_applications'][0]['order_status'], 'PENDING')

    def test_invalidated_decision_cannot_execute(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        self._sql("UPDATE procurement.supply_disruption_event SET is_current=FALSE WHERE event_id='E01'")
        invalidated = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '不应留下批准')
        self.assertEqual(invalidated['approval_status'], 'INVALIDATED')
        self.assertIsNone(invalidated['approval'])
        with self.assertRaises(ExecutionRejected):
            self.manager.execute(decision['id'])
        shown = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(shown['approval_status'], 'INVALIDATED')
        self.assertEqual(shown['attempts'], [])
        self.assertIsNone(shown['task'])
        self.assertIsNone(shown['execution_status'])

    def test_agent_account_cannot_execute(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with scripted_endpoint([{'turtle': VALID_TURTLE}, {'fail': True}]) as endpoint:
            failed = self._manager(endpoint).approve(decision['id'], SUPERVISOR_TOKEN, 'Agent 不能重试')
        with self.assertRaises(pymysql.err.OperationalError):
            self.agent.execute(
                decision['id'], action='cancel_order', order='101', event='E01',
                assigned_role='采购员', description='改写', evidence={'fake': True},
            )
        shown = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(shown['execution_status'], 'FAILED')
        self.assertEqual(shown['approval']['id'], failed['approval']['id'])
        self.assertEqual(shown['attempts'], failed['attempts'])
        self.assertIsNone(shown['task'])

    def test_unauthorized_approve_does_not_execute(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        with self.assertRaises(Unauthorized):
            self.manager.approve(decision['id'], BUYER_TOKEN, '采购员不能批准')
        current = self.manager.show(decision['id'], SUPERVISOR_TOKEN)
        self.assertEqual(current['approval_status'], 'PENDING')
        self.assertIsNone(current['task'])
        self.assertEqual(current['attempts'], [])

    def test_execution_accounts_cannot_rewrite_evidence_sources_or_tasks(self):
        decision = self.agent.submit('101', 'E01', 'Agent说明')
        created = self.manager.approve(decision['id'], SUPERVISOR_TOKEN, '权限探针')
        with self.admin.cursor() as cursor:
            cursor.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA='procurement' AND TABLE_NAME='supply_risk_review_task' "
                "ORDER BY ORDINAL_POSITION"
            )
            self.assertEqual([row[0] for row in cursor.fetchall()], TASK_COLUMNS)
            cursor.execute(
                "SELECT COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME "
                "FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA='procurement' AND TABLE_NAME='supply_risk_review_task' "
                "AND REFERENCED_TABLE_NAME IS NOT NULL"
            )
            references = {(row[0], row[1], row[2]) for row in cursor.fetchall()}
            self.assertEqual(references, {
                ('approval_id', 'decision_loop', 'approval_record'),
                ('order_id', 'procurement', 'purchase_order'),
                ('event_id', 'procurement', 'supply_disruption_event'),
            })
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA='decision_loop' AND TABLE_NAME='decision_record' "
                "AND REFERENCED_TABLE_NAME IS NOT NULL"
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        accounts = (
            ('decision_agent', 'demo_agent_db_password'),
            ('decision_manager', 'demo_manager_db_password'),
            ('ontop', 'demo_ontop_password'),
        )
        denied = (
            "UPDATE decision_loop.decision_record SET evidence=JSON_OBJECT()",
            "UPDATE decision_loop.execution_attempt SET reason='改写'",
            'DELETE FROM decision_loop.execution_attempt',
            "UPDATE procurement.supply_risk_review_task SET description='改写'",
            'DELETE FROM procurement.supply_risk_review_task',
            "UPDATE procurement.purchase_order SET amount=9 WHERE order_id=101",
            "INSERT INTO procurement.supplier (supplier_id,supplier_name,supplier_status) VALUES (9,'x','ACTIVE')",
            'SELECT id FROM procurement.supply_risk_review_task',
            'SELECT id FROM decision_loop.decision_record',
        )
        for user, password in accounts:
            connection = pymysql.connect(host='mysql', user=user, password=password, autocommit=True)
            try:
                for statement in denied:
                    if user == 'decision_manager' and statement.startswith('SELECT'):
                        continue
                    if user == 'ontop' and not statement.startswith('SELECT'):
                        continue
                    if user == 'decision_agent' and statement.startswith('SELECT id FROM decision_loop'):
                        continue
                    with self.subTest(user=user, statement=statement.split()[0]):
                        with connection.cursor() as cursor, self.assertRaises(pymysql.err.OperationalError) as caught:
                            cursor.execute(statement)
                        self.assertIn(caught.exception.args[0], (1044, 1142, 1143))
            finally:
                connection.close()
        allowed = pymysql.connect(
            host='mysql', user='decision_manager', password='demo_manager_db_password', autocommit=False,
        )
        try:
            with allowed.cursor() as cursor:
                cursor.execute(
                    "UPDATE decision_loop.decision_record SET execution_status='FAILED' WHERE id=%s",
                    (decision['id'],),
                )
            allowed.rollback()
        finally:
            allowed.close()
        self.assertEqual(self.manager.show(decision['id'], SUPERVISOR_TOKEN)['task']['id'], created['task']['id'])
        self.assertEqual(self.manager.show(decision['id'], SUPERVISOR_TOKEN)['execution_status'], 'SUCCEEDED')
