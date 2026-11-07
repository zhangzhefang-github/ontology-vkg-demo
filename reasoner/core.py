from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from owlrl import DeductiveClosure, OWLRL_Semantics
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS


EX = Namespace("https://example.org/procurement#")
ORDER_PREFIX = "https://example.org/purchase-order/"
EVENT_PREFIX = "https://example.org/event/"
EXPECTED_AXIOM = (EX.DeliverySuspensionEvent, RDFS.subClassOf, EX.SupplyDisruptionEvent)


class ServiceFailure(RuntimeError):
    pass


class EmptyBaseFacts(RuntimeError):
    pass


class InvalidExperiment(RuntimeError):
    pass


def fetch_base_facts(endpoint: str, query_path: Path) -> Graph:
    body = urllib.parse.urlencode({"query": query_path.read_text(encoding="utf-8")}).encode()
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Accept": "text/turtle",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ServiceFailure(f"Ontop 服务请求失败: {exc}") from exc
    graph = Graph()
    try:
        graph.parse(data=payload, format="turtle")
    except Exception as exc:
        preview = payload[:500].decode(errors="replace")
        raise ServiceFailure(f"Ontop 返回内容无法解析为 Turtle: {preview}") from exc
    if not graph:
        raise EmptyBaseFacts("Ontop 查询成功，但基础事实图为空")
    return graph


def local_id(uri: URIRef, prefix: str) -> str:
    value = str(uri)
    return value[len(prefix):] if value.startswith(prefix) else value


def event_direct_types(graph: Graph) -> dict[URIRef, set[URIRef]]:
    events = set(graph.subjects(EX.affectsSupplier, None))
    return {
        event: {
            event_type
            for event_type in graph.objects(event, RDF.type)
            if isinstance(event_type, URIRef)
        }
        for event in events
    }


class RuleApplication(TypedDict):
    rule: str
    order: str
    order_status: str
    supplier: str
    supplier_uri: str
    event: str
    event_direct_types: list[str]
    event_inferred_type: str
    event_is_current: bool


class ResultJSON(TypedDict):
    base_triple_count: int
    axiom_loaded: bool
    inferred_parent_events: list[str]
    needs_review_orders: list[str]
    rule_applications: list[RuleApplication]


@dataclass
class ReasoningResult:
    base_facts: Graph
    axiom_loaded: bool
    inferred_parent_types: list[tuple[URIRef, URIRef]]
    needs_review_orders: list[str]
    rule_applications: list[RuleApplication]
    rule_path: Path

    @property
    def base_triple_count(self) -> int:
        return len(self.base_facts)

    @property
    def inferred_parent_events(self) -> list[str]:
        return [local_id(event, EVENT_PREFIX) for event, _ in self.inferred_parent_types]

    def to_json(self) -> ResultJSON:
        """Keep the existing RESULT_JSON schema separate from the full facts."""
        return {
            "base_triple_count": self.base_triple_count,
            "axiom_loaded": self.axiom_loaded,
            "inferred_parent_events": self.inferred_parent_events,
            "needs_review_orders": self.needs_review_orders,
            "rule_applications": self.rule_applications,
        }


def compute(
    *,
    endpoint: str,
    base_query: Path,
    ontology: Path,
    rule: Path,
    without_inheritance: bool = False,
) -> ReasoningResult:
    """Fetch current facts and compute review conclusions without terminal output."""
    base = fetch_base_facts(endpoint, base_query)
    base.bind("proc", EX)
    direct_types = event_direct_types(base)
    if not any(direct_types.values()):
        raise EmptyBaseFacts("基础事实包含订单，但没有供应中断事件")
    if any((event, RDF.type, EX.SupplyDisruptionEvent) in base for event in direct_types):
        raise InvalidExperiment("基础事实已提前包含 SupplyDisruptionEvent，无法进行本体依赖实验")

    working = Graph()
    for prefix, namespace in base.namespaces():
        working.bind(prefix, namespace)
    for triple in base:
        working.add(triple)
    ontology_graph = Graph().parse(ontology, format="turtle")
    if without_inheritance:
        ontology_graph.remove(EXPECTED_AXIOM)
    axiom_loaded = EXPECTED_AXIOM in ontology_graph
    for triple in ontology_graph:
        working.add(triple)

    before_closure = set(working)
    DeductiveClosure(OWLRL_Semantics).expand(working)
    inferred_parent_types = sorted(
        (
            event,
            EX.SupplyDisruptionEvent,
        )
        for event in direct_types
        if (event, RDF.type, EX.SupplyDisruptionEvent) in working
        and (event, RDF.type, EX.SupplyDisruptionEvent) not in before_closure
    )

    rule_text = rule.read_text(encoding="utf-8")
    rule_result = working.query(rule_text)
    derived = Graph()
    for triple in rule_result.graph:
        derived.add(triple)
        working.add(triple)

    applications = sorted(derived.subjects(RDF.type, EX.RuleApplication), key=str)
    review_orders = sorted(set(derived.subjects(RDF.type, EX.NeedsReviewOrder)), key=str)
    explanations: list[RuleApplication] = []
    for application in applications:
        order = derived.value(application, EX.concludedOrder)
        event = derived.value(application, EX.supportingEvent)
        supplier = derived.value(application, EX.supportingSupplier)
        direct = sorted(direct_types[event], key=str)
        supplier_name = working.value(supplier, EX.supplierName)
        order_status = working.value(order, EX.orderStatus)
        current = working.value(event, EX.isCurrent)
        evidence: RuleApplication = {
            "rule": str(derived.value(application, EX.appliedRule)),
            "order": local_id(order, ORDER_PREFIX),
            "order_status": str(order_status),
            "supplier": str(supplier_name),
            "supplier_uri": str(supplier),
            "event": local_id(event, EVENT_PREFIX),
            "event_direct_types": [str(value) for value in direct],
            "event_inferred_type": str(EX.SupplyDisruptionEvent),
            "event_is_current": bool(current.toPython()),
        }
        explanations.append(evidence)
    return ReasoningResult(
        base_facts=base,
        axiom_loaded=axiom_loaded,
        inferred_parent_types=inferred_parent_types,
        needs_review_orders=[local_id(order, ORDER_PREFIX) for order in review_orders],
        rule_applications=explanations,
        rule_path=rule,
    )
