"""批准前与执行前的业务前提比对。事务内只看锁定行，不用 SQL 重写 R-01。"""
from dataclasses import dataclass

from rdflib import URIRef

from reasoner import core
from reasoner.core import event_direct_types

from decision.evidence import precondition_hash


SUPPLIER_PREFIX = 'https://example.org/supplier/'
EVENT_TYPE_PREFIX = 'https://example.org/procurement#'


@dataclass
class OutsideCheck:
    completed: bool
    fingerprint: dict
    code_commit: str
    application_holds: bool
    fresh_bound: dict | None
    direct_types: list
    error: str


@dataclass(frozen=True)
class Judgement:
    kind: str
    mismatches: tuple
    reason: str


def locked_binding(order_row, event_row):
    """把锁定的订单行和事件行归一成与证据相同的绑定事实。"""
    return {
        'order_status': str(order_row['order_status']),
        'order_supplier': SUPPLIER_PREFIX + str(order_row['supplier_id']),
        'event_is_current': bool(event_row['is_current']),
        'affected_supplier': SUPPLIER_PREFIX + str(event_row['affected_supplier_id']),
        'event_direct_types': [EVENT_TYPE_PREFIX + str(event_row['event_type'])],
    }


def observed_binding(graph, order, event):
    """规则应用已经不存在时，仍从基础事实图读取可比对的绑定。"""
    order_uri = URIRef(core.ORDER_PREFIX + str(order))
    event_uri = URIRef(core.EVENT_PREFIX + str(event))
    direct = sorted(str(item) for item in event_direct_types(graph).get(event_uri, ()))
    status = graph.value(order_uri, core.EX.orderStatus)
    supplier = graph.value(order_uri, core.EX.orderedFrom)
    affected = graph.value(event_uri, core.EX.affectsSupplier)
    current = graph.value(event_uri, core.EX.isCurrent)
    if None in (status, supplier, affected, current):
        return None, direct
    return {
        'order_status': str(status),
        'order_supplier': str(supplier),
        'event_is_current': bool(current.toPython()),
        'affected_supplier': str(affected),
        'event_direct_types': direct,
    }, direct


def _types(value):
    return sorted(str(item) for item in value)


def field_mismatches(saved, observed):
    if observed is None:
        return []
    items = []
    if bool(observed['event_is_current']) != bool(saved['event_is_current']):
        items.append('事件不再当前有效')
    if str(observed['order_status']) != str(saved['order_status']):
        items.append('订单状态变化')
    if str(observed['order_supplier']) != str(saved['order_supplier']):
        items.append('订单的供应商变化')
    if str(observed['affected_supplier']) != str(saved['affected_supplier']):
        items.append('事件影响的供应商变化')
    if _types(observed['event_direct_types']) != _types(saved['event_direct_types']):
        items.append('事件的具体类型变化')
    return items


def _dedupe(items):
    unique = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return unique


def judge(evidence, check: OutsideCheck, order_row, event_row) -> Judgement:
    """锁定行缺失优先于未完成的计算；其余业务差异保持已有审批结论之外的处理由调用方决定。"""
    missing = []
    if order_row is None:
        missing.append('绑定的订单行已经不存在')
    if event_row is None:
        missing.append('绑定的事件行已经不存在')
    if missing:
        reason = '业务前提不一致：' + '、'.join(missing)
        return Judgement('business', tuple(missing), reason)
    if not check.completed:
        return Judgement('incomplete', (), check.error)
    locked = locked_binding(order_row, event_row)
    saved = evidence['bound_facts']
    items = field_mismatches(saved, locked)
    items.extend(field_mismatches(saved, check.fresh_bound))
    if check.fingerprint != evidence['semantic_fingerprint']:
        items.append('语义版本指纹变化')
    if len(check.direct_types) > 1:
        items.append('直接类型多于一个')
    if not check.application_holds:
        items.append('规则应用不再成立')
    items = _dedupe(items)
    fingerprint = check.fingerprint or evidence['semantic_fingerprint']
    hash_drift = (
        precondition_hash(locked, fingerprint) != evidence['precondition_hash']
        or (
            check.fresh_bound is not None
            and precondition_hash(check.fresh_bound, fingerprint) != evidence['precondition_hash']
        )
    )
    if hash_drift and not items:
        items.append('业务前提不一致')
    if not items:
        return Judgement('ok', (), '业务前提仍然成立')
    return Judgement('business', tuple(items), '业务前提不一致：' + '、'.join(items))
