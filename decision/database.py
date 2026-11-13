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


def decision_response(row):
    if row is None:
        return None
    return {
        'id': row['id'], 'order': str(row['order_id']), 'event': row['event_id'],
        'approval_status': row['approval_status'], 'execution_status': row['execution_status'],
        'occupancy': row['occupancy'], 'created_at': row['created_at'].isoformat(),
        'action': 'create_supply_risk_review_task', 'assigned_role': '采购员',
        'description': row['description'], 'evidence': json.loads(row['evidence']),
        'agent_note': {'text': row['agent_note'], 'is_evidence': False},
    }


def occupied(connection, order, event):
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT * FROM decision_record WHERE order_id=%s AND event_id=%s AND occupancy=1',
            (order, event),
        )
        return decision_response(cursor.fetchone())
