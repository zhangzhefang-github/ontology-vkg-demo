"""Connection and immutable decision record representation."""
import json

import pymysql


def connect(settings):
    return pymysql.connect(
        host=settings.db_host, user=settings.db_user, password=settings.db_password,
        database='decision_loop', charset='utf8mb4', autocommit=False,
        connect_timeout=10, read_timeout=30, write_timeout=30,
        cursorclass=pymysql.cursors.DictCursor,
    )


def json_value(value):
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        return json.loads(value)
    return value


def decision_response(row, approval=None):
    if row is None:
        return None
    return {
        'id': row['id'], 'order': str(row['order_id']), 'event': row['event_id'],
        'approval_status': row['approval_status'], 'execution_status': row['execution_status'],
        'occupancy': row['occupancy'], 'invalidation_reason': row['invalidation_reason'],
        'created_at': row['created_at'].isoformat(),
        'action': 'create_supply_risk_review_task', 'assigned_role': '采购员',
        'description': row['description'], 'evidence': json.loads(row['evidence']),
        'agent_note': {'text': row['agent_note'], 'is_evidence': False},
        'approval': approval, 'task': None, 'attempts': [],
    }


def occupied(connection, order, event):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT * FROM decision_record WHERE order_id=%s AND event_id=%s AND occupancy=1',
            (order, event),
        )
        row = cursor.fetchone()
        return decision_response(row, approval_for(connection, row['id'])) if row else None


def approval_for(connection, decision_id):
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM approval_record WHERE decision_id=%s', (decision_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        'id': row['id'], 'decision_id': row['decision_id'], 'conclusion': row['conclusion'],
        'actor_id': row['actor_id'], 'actor_name': row['actor_name'], 'actor_role': row['actor_role'],
        'reason': row['comment'], 'created_at': row['created_at'].isoformat(),
    }


def decision_by_id(connection, decision_id, *, lock=False):
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM decision_record WHERE id=%s' + (' FOR UPDATE' if lock else ''), (decision_id,))
        row = cursor.fetchone()
    if row is None:
        raise LookupError('决策记录不存在')
    return decision_response(row, approval_for(connection, decision_id))


def task_view(row):
    if row is None:
        return None
    return {
        'id': row['id'], 'source_decision_id': row['source_decision_id'], 'approval_id': row['approval_id'],
        'order': str(row['order_id']), 'event': row['event_id'], 'assigned_role': row['assigned_role'],
        'description': row['description'], 'created_at': row['created_at'].isoformat(),
    }


def task_for(connection, decision_id):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT * FROM procurement.supply_risk_review_task WHERE source_decision_id=%s',
            (decision_id,),
        )
        return task_view(cursor.fetchone())


def task_by_id(connection, task_id):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT * FROM procurement.supply_risk_review_task WHERE id=%s',
            (task_id,),
        )
        return task_view(cursor.fetchone())


def attempts_for(connection, decision_id):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT * FROM execution_attempt WHERE decision_id=%s ORDER BY id',
            (decision_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            'id': row['id'], 'decision_id': row['decision_id'], 'outcome': row['outcome'],
            'reason': row['reason'], 'mismatches': json_value(row['mismatches']),
            'semantic_fingerprint': json_value(row['semantic_fingerprint']),
            'code_commit': row['code_commit'], 'task_id': row['task_id'],
            'created_at': row['created_at'].isoformat(),
        }
        for row in rows
    ]


def attach_execution(connection, decision):
    """主管通道才能读取任务和执行尝试；查询响应保持默认空值。"""
    if decision is None:
        return None
    presented = dict(decision)
    presented['task'] = task_for(connection, decision['id'])
    presented['attempts'] = attempts_for(connection, decision['id'])
    return presented
