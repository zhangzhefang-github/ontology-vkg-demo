from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import pymysql

from decision.database import connect, occupied, decision_response, decision_by_id, attach_execution, task_by_id
from decision.auth import supervisor
from decision.evidence import (
    InvalidEvidence, bound_facts, canonical_json, semantic_material, verify_code, capture,
)
from decision.precondition import OutsideCheck, judge, observed_binding

from reasoner.core import (
    EmptyBaseFacts, InvalidExperiment, ResultJSON, RuleApplication, ServiceFailure, compute,
)


# RDFLib/pyparsing share parser state; concurrent tools must serialize reasoning.
_REASONING_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


class CheckIncomplete(RuntimeError):
    """批准前的业务前提检查没有做完，决策保持待审批。"""


class ExecutionRejected(ValueError):
    """当前决策不允许开始或继续执行。"""


class DecisionResponse(TypedDict):
    id: int
    order: str
    event: str
    approval_status: str
    execution_status: str | None
    occupancy: int | None
    invalidation_reason: str | None
    created_at: str
    action: str
    assigned_role: str
    description: str
    evidence: dict
    agent_note: dict
    approval: dict | None
    task: dict | None
    attempts: list


class QueryRuleApplication(RuleApplication):
    decision: DecisionResponse | None


class QueryResponse(ResultJSON):
    conclusion: str
    rule_applications: list[QueryRuleApplication]


@dataclass(frozen=True)
class Settings:
    endpoint: str = 'http://ontop:8080/sparql'
    base_query: Path = Path('queries/phase2-base-facts.rq')
    ontology: Path = Path('ontop/phase2-extension.ttl')
    rule: Path = Path('rules/R-01-needs-review.rq')
    mapping: Path = Path('ontop/mapping.obda')
    ontop_ontology: Path = Path('ontop/ontology.ttl')
    db_host: str = 'mysql'
    db_user: str = 'decision_agent'
    db_password: str = ''
    code_commit: str = ''
    git_dir: Path = Path('/git')

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            endpoint=os.getenv('ONTOP_ENDPOINT', cls.endpoint),
            db_host=os.getenv('DECISION_DB_HOST', cls.db_host),
            db_user=os.getenv('DECISION_DB_USER', cls.db_user),
            db_password=os.getenv('DECISION_DB_PASSWORD', ''),
            code_commit=os.getenv('CODE_COMMIT', ''),
            git_dir=Path(os.getenv('DECISION_GIT_DIR', '/git')),
        )


class DecisionService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    def _compute(self):
        with _REASONING_LOCK:
            return compute(
                endpoint=self.settings.endpoint, base_query=self.settings.base_query,
                ontology=self.settings.ontology, rule=self.settings.rule,
                without_inheritance=False,
            )

    def query(self) -> QueryResponse:
        """Read current rule applications without changing any decision state."""
        result = self._compute()
        response = {**result.to_json(), 'conclusion': '需要供应风险复核'}
        connection = connect(self.settings)
        try:
            for application in response['rule_applications']:
                application['decision'] = occupied(connection, application['order'], application['event'])
        finally:
            connection.close()
        return response

    def submit(self, order: str, event: str, agent_note: str = '', **ignored_action_parameters) -> DecisionResponse:
        """Only the named live rule application may create an immutable proposal."""
        connection = connect(self.settings)
        try:
            existing = occupied(connection, order, event)
            if existing:
                return existing
            connection.rollback()
            with _REASONING_LOCK:
                material, fingerprint = semantic_material(self.settings)
                commit = verify_code(self.settings, material['core'])
                result = self._compute()
                _, after = semantic_material(self.settings)
                if after != fingerprint:
                    raise InvalidEvidence('推理输入在计算期间发生变化')
                application = next((item for item in result.rule_applications
                                    if item['order'] == str(order) and item['event'] == event), None)
                if application is None:
                    raise InvalidEvidence('指定的规则应用已经不存在')
                evidence = capture(result, application, material, fingerprint, commit)
            description = f'订单 {application["order"]} 因事件 {application["event"]} 需要供应风险复核。'
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO decision_record (order_id,event_id,evidence,agent_note,description) '
                        'VALUES (%s,%s,%s,%s,%s)',
                        (application['order'], application['event'], canonical_json(evidence), agent_note, description),
                    )
                    cursor.execute('SELECT * FROM decision_record WHERE id=%s', (cursor.lastrowid,))
                    created = decision_response(cursor.fetchone())
                connection.commit()
                return created
            except pymysql.IntegrityError as exc:
                connection.rollback()
                if exc.args[0] != 1062:
                    raise
            winner = occupied(connection, order, event)
            if winner is None:
                raise InvalidEvidence('占位在并发提交期间已释放，请重新提交')
            return winner
        finally:
            connection.close()

    def replay(self, evidence: dict) -> dict:
        """用已保存证据离线重放规则应用，不读取也不写入决策。"""
        from decision.replay import replay_evidence
        with _REASONING_LOCK:
            return replay_evidence(evidence, self.settings)

    def trace(self, task_id: int) -> dict:
        """按供应风险复核任务编号只读追溯，不批准、不执行、不改任何数据。"""
        connection = connect(self.settings)
        try:
            task = task_by_id(connection, task_id)
            if task is None:
                logger.info('任务 %s 未找到，未修改数据', task_id)
                return {'found': False, 'task_id': task_id, 'message': '未找到供应风险复核任务'}
            decision = attach_execution(connection, decision_by_id(connection, task['source_decision_id']))
            connection.rollback()
            evidence = decision['evidence']
            logger.info('已按任务 %s 追溯来源决策 %s，未修改数据', task_id, decision['id'])
            return {
                'found': True,
                'task': task,
                'decision': {
                    'id': decision['id'],
                    'order': decision['order'],
                    'event': decision['event'],
                    'approval_status': decision['approval_status'],
                    'execution_status': decision['execution_status'],
                    'created_at': decision['created_at'],
                    'description': decision['description'],
                    'assigned_role': decision['assigned_role'],
                },
                'fact_snapshot': {
                    'use': '发现快照是否被改写，不参与业务失效判断',
                    'facts_nt': evidence['facts_nt'],
                    'facts_hash': evidence['facts_hash'],
                },
                'business_precondition': {
                    'use': '判断绑定事实和语义版本是否变化',
                    'bound_facts': evidence['bound_facts'],
                    'precondition_hash': evidence['precondition_hash'],
                    'semantic_fingerprint': evidence['semantic_fingerprint'],
                },
                'rule_application': {
                    'rule': evidence['application']['rule'],
                    'order': evidence['application']['order'],
                    'event': evidence['application']['event'],
                    'record': '绑定',
                    'description': '规则应用记录的是绑定，不是通用 OWL 证明。',
                },
                'reasoning_version': {
                    'use': '供检出代码提交并离线重放当时的规则应用',
                    'code_commit': evidence['code_commit'],
                    'semantic_fingerprint': evidence['semantic_fingerprint'],
                    'ontop_ontology_text': evidence['ontop_ontology_text'],
                    'ontology_text': evidence['ontology_text'],
                    'rule_text': evidence['rule_text'],
                },
                'approval': decision['approval'],
                'agent_note': decision['agent_note'],
                'attempts': decision['attempts'],
            }
        finally:
            connection.close()

    def show(self, decision_id: int, token: str | None = None) -> DecisionResponse:
        connection = connect(self.settings)
        try:
            supervisor(connection, token)
            return attach_execution(connection, decision_by_id(connection, decision_id))
        finally:
            connection.close()

    def approve(self, decision_id: int, token: str | None = None, comment: str = '') -> DecisionResponse:
        """先提交批准，再在另一个事务里尝试创建任务。已有结论不触发执行。"""
        if not isinstance(comment, str):
            raise ValueError('批准意见必须是文本')
        connection = connect(self.settings)
        try:
            actor = supervisor(connection, token)
            connection.rollback()
            current = decision_by_id(connection, decision_id)
            if current['approval_status'] != 'PENDING':
                presented = attach_execution(connection, current)
                connection.rollback()
                return presented
            connection.rollback()
            check = self._outside_check(current)
            try:
                current = decision_by_id(connection, decision_id, lock=True)
                if current['approval_status'] != 'PENDING':
                    connection.rollback()
                    return attach_execution(connection, decision_by_id(connection, decision_id))
                # 先锁决策，再按订单、事件顺序加共享锁，锁保持到本事务提交。
                order_row, event_row = self._lock_bound(connection, current)
                judgement = judge(current['evidence'], check, order_row, event_row)
                if judgement.kind == 'incomplete':
                    connection.rollback()
                    raise CheckIncomplete(f'批准前检查未完成：{check.error}')
                if judgement.kind == 'business':
                    self._invalidate(connection, decision_id, judgement.reason)
                    connection.commit()
                    logger.info('决策 %s 在批准前已失效', decision_id)
                    return attach_execution(connection, decision_by_id(connection, decision_id))
                self._insert_approval(connection, decision_id, actor, comment)
                connection.commit()
                logger.info('决策 %s 已批准，准备在另一事务执行', decision_id)
            except CheckIncomplete:
                raise
            except pymysql.IntegrityError as exc:
                connection.rollback()
                if exc.args[0] != 1062:
                    raise
                return attach_execution(connection, decision_by_id(connection, decision_id))
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()
        return self.execute(decision_id)

    def reject(self, decision_id: int, token: str | None, reason: str) -> DecisionResponse:
        """锁定后拒绝仍待审批的决策，并留下不可改的审批记录。"""
        return self._review(decision_id, token, 'REJECT', reason)

    def _review(self, decision_id: int, token: str | None, conclusion: str, reason: str) -> DecisionResponse:
        connection = connect(self.settings)
        try:
            actor = supervisor(connection, token)
            # 身份查询不能占住可重复读快照，否则并发拒绝看不到刚提交的审批记录。
            connection.rollback()
            if conclusion == 'REJECT' and (not isinstance(reason, str) or not reason.strip()):
                raise ValueError('拒绝必须填写理由')
            try:
                current = decision_by_id(connection, decision_id, lock=True)
                if current['approval_status'] != 'PENDING':
                    connection.rollback()
                    return decision_by_id(connection, decision_id)
                if conclusion != 'REJECT':
                    connection.rollback()
                    raise NotImplementedError('批准事务不在本票范围')
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO approval_record '
                        '(decision_id,conclusion,actor_id,actor_name,actor_role,comment) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',
                        (decision_id, conclusion, actor['id'], actor['name'], actor['role'], reason),
                    )
                    cursor.execute(
                        "UPDATE decision_record SET approval_status='REJECTED' WHERE id=%s",
                        (decision_id,),
                    )
                connection.commit()
                logger.info('决策 %s 已拒绝，未记录令牌', decision_id)
                return decision_by_id(connection, decision_id)
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()

    def execute(self, decision_id: int, **ignored_action_parameters) -> DecisionResponse:
        """只接收决策编号。动作、订单、事件、角色、描述和新证据一律忽略，也不再次批准。

        成功和被阻止是终态；执行失败可完整复核后重试。
        """
        del ignored_action_parameters
        connection = connect(self.settings)
        try:
            current = decision_by_id(connection, decision_id)
            connection.rollback()
            if current['execution_status'] in ('SUCCEEDED', 'BLOCKED'):
                return attach_execution(connection, current)
            self._require_executable(current)
            check = self._outside_check(current)
            try:
                return self._execute_locked(connection, decision_id, check)
            except pymysql.err.OperationalError as exc:
                connection.rollback()
                if exc.args and exc.args[0] in (1205, 1213):
                    logger.info('决策 %s 执行遇到锁等待或死锁，记为执行失败', decision_id)
                    return self._record_outcome(
                        connection, decision_id, 'FAILED', '执行事务锁等待或死锁，已记为执行失败',
                        (), check, None,
                    )
                raise
        finally:
            connection.close()

    def _outside_check(self, decision) -> OutsideCheck:
        """推理和版本指纹都在事务外完成。"""
        fingerprint = {}
        code_commit = ''
        try:
            material, fingerprint = semantic_material(self.settings)
            code_commit = verify_code(self.settings, material['core'])
            result = self._compute()
            _, after = semantic_material(self.settings)
            if after != fingerprint:
                raise InvalidEvidence('推理输入在计算期间发生变化')
        except (ServiceFailure, InvalidExperiment, EmptyBaseFacts, InvalidEvidence, OSError, ValueError) as exc:
            return OutsideCheck(False, fingerprint, code_commit, False, None, [], str(exc))
        application = next(
            (item for item in result.rule_applications
             if item['order'] == str(decision['order']) and item['event'] == decision['event']),
            None,
        )
        if application is None:
            fresh, direct = observed_binding(result.base_facts, decision['order'], decision['event'])
            return OutsideCheck(True, fingerprint, code_commit, False, fresh, direct, '')
        fresh = bound_facts(result.base_facts, application)
        return OutsideCheck(
            True, fingerprint, code_commit, True, fresh, list(application['event_direct_types']), '',
        )

    def _lock_bound(self, connection, decision):
        """事务内只读取绑定列。订单锁在事件锁之前，两把锁保持到提交。"""
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT order_id, supplier_id, order_status FROM procurement.purchase_order '
                'WHERE order_id=%s FOR SHARE',
                (decision['order'],),
            )
            order_row = cursor.fetchone()
            cursor.execute(
                'SELECT event_id, affected_supplier_id, event_type, is_current '
                'FROM procurement.supply_disruption_event WHERE event_id=%s FOR SHARE',
                (decision['event'],),
            )
            event_row = cursor.fetchone()
        return order_row, event_row

    def _invalidate(self, connection, decision_id, reason):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE decision_record SET approval_status='INVALIDATED', occupancy=NULL, "
                'invalidation_reason=%s WHERE id=%s',
                (reason, decision_id),
            )

    def _insert_approval(self, connection, decision_id, actor, comment):
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO approval_record '
                '(decision_id,conclusion,actor_id,actor_name,actor_role,comment) '
                'VALUES (%s,%s,%s,%s,%s,%s)',
                (decision_id, 'APPROVE', actor['id'], actor['name'], actor['role'], comment),
            )
            cursor.execute(
                "UPDATE decision_record SET approval_status='APPROVED', execution_status='NOT_STARTED' "
                'WHERE id=%s',
                (decision_id,),
            )

    def _require_executable(self, current):
        if current['approval_status'] in ('PENDING', 'REJECTED', 'INVALIDATED'):
            raise ExecutionRejected('待审批、已拒绝或已失效的决策不能执行')
        if current['approval_status'] != 'APPROVED' or current['execution_status'] not in ('NOT_STARTED', 'FAILED'):
            raise ExecutionRejected('只有尚未完成的已批准决策可以执行')
        if current['approval'] is None or current['approval']['conclusion'] != 'APPROVE':
            raise ExecutionRejected('审批结论不是批准，不能创建任务')

    def _execute_locked(self, connection, decision_id, check):
        current = decision_by_id(connection, decision_id, lock=True)
        if current['execution_status'] in ('SUCCEEDED', 'BLOCKED'):
            connection.rollback()
            return attach_execution(connection, decision_by_id(connection, decision_id))
        self._require_executable(current)
        order_row, event_row = self._lock_bound(connection, current)
        judgement = judge(current['evidence'], check, order_row, event_row)
        if judgement.kind == 'business':
            return self._record_outcome(
                connection, decision_id, 'BLOCKED', judgement.reason, judgement.mismatches, check, None,
                release=True,
            )
        if judgement.kind == 'incomplete':
            return self._record_outcome(
                connection, decision_id, 'FAILED', check.error or '执行检查未完成', (), check, None,
            )
        description = f'订单 {current["order"]} 因事件 {current["event"]} 需要供应风险复核。'
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'INSERT INTO procurement.supply_risk_review_task '
                    '(source_decision_id,approval_id,order_id,event_id,assigned_role,description) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (
                        decision_id, current['approval']['id'], current['order'], current['event'],
                        '采购员', description,
                    ),
                )
                task_id = cursor.lastrowid
            self._insert_attempt(connection, decision_id, 'SUCCEEDED', '已创建供应风险复核任务', (), check, task_id)
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE decision_record SET execution_status='SUCCEEDED' WHERE id=%s",
                    (decision_id,),
                )
            connection.commit()
        except pymysql.IntegrityError as exc:
            connection.rollback()
            if exc.args[0] != 1062:
                raise
            logger.info('决策 %s 已有任务，本次执行不再写库', decision_id)
            return attach_execution(connection, decision_by_id(connection, decision_id))
        logger.info('决策 %s 已创建供应风险复核任务 %s', decision_id, task_id)
        return attach_execution(connection, decision_by_id(connection, decision_id))

    def _record_outcome(self, connection, decision_id, outcome, reason, mismatches, check, task_id, release=False):
        current = decision_by_id(connection, decision_id, lock=True)
        if current['execution_status'] in ('SUCCEEDED', 'BLOCKED'):
            connection.rollback()
            return attach_execution(connection, decision_by_id(connection, decision_id))
        self._insert_attempt(connection, decision_id, outcome, reason, mismatches, check, task_id)
        with connection.cursor() as cursor:
            if release:
                cursor.execute(
                    "UPDATE decision_record SET execution_status=%s, occupancy=NULL WHERE id=%s",
                    (outcome, decision_id),
                )
            else:
                cursor.execute(
                    'UPDATE decision_record SET execution_status=%s WHERE id=%s',
                    (outcome, decision_id),
                )
        connection.commit()
        logger.info('决策 %s 执行结果为 %s', decision_id, outcome)
        return attach_execution(connection, decision_by_id(connection, decision_id))

    def _insert_attempt(self, connection, decision_id, outcome, reason, mismatches, check, task_id):
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO execution_attempt '
                '(decision_id,outcome,reason,mismatches,semantic_fingerprint,code_commit,task_id) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (
                    decision_id, outcome, reason, canonical_json(list(mismatches)),
                    canonical_json(check.fingerprint if check is not None else {}),
                    (check.code_commit if check is not None else ''), task_id,
                ),
            )
