from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from owlrl import DeductiveClosure, OWLRL_Semantics
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD


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


def compact(term, graph: Graph) -> str:
    if isinstance(term, Literal):
        return term.n3(graph.namespace_manager)
    try:
        return graph.namespace_manager.normalizeUri(term)
    except Exception:
        return str(term)


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


def sorted_triples(graph: Graph):
    return sorted(graph, key=lambda triple: tuple(str(value) for value in triple))


def run(args: argparse.Namespace) -> dict:
    base = fetch_base_facts(args.endpoint, args.base_query)
    base.bind("proc", EX)
    direct_types = event_direct_types(base)
    if not direct_types:
        raise EmptyBaseFacts("基础事实包含订单，但没有供应中断事件")
    if any((event, RDF.type, EX.SupplyDisruptionEvent) in base for event in direct_types):
        raise InvalidExperiment("基础事实已提前包含 SupplyDisruptionEvent，无法进行本体依赖实验")

    print("=== 1. Ontop 导出的基础事实 ===")
    for subject, predicate, obj in sorted_triples(base):
        print(f"{compact(subject, base)} {compact(predicate, base)} {compact(obj, base)}")

    working = Graph()
    for prefix, namespace in base.namespaces():
        working.bind(prefix, namespace)
    for triple in base:
        working.add(triple)
    ontology = Graph().parse(args.ontology, format="turtle")
    if args.without_inheritance:
        ontology.remove(EXPECTED_AXIOM)
    axiom_loaded = EXPECTED_AXIOM in ontology
    for triple in ontology:
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

    print("\n=== 2. OWL-RL 类型推理新增的父类型事实 ===")
    print(f"加载继承公理: {axiom_loaded}")
    if inferred_parent_types:
        for event, parent in inferred_parent_types:
            print(f"{compact(event, working)} rdf:type {compact(parent, working)}")
    else:
        print("（无）")

    rule_text = args.rule.read_text(encoding="utf-8")
    rule_result = working.query(rule_text)
    derived = Graph()
    for triple in rule_result.graph:
        derived.add(triple)
        working.add(triple)

    applications = sorted(derived.subjects(RDF.type, EX.RuleApplication), key=str)
    review_orders = sorted(set(derived.subjects(RDF.type, EX.NeedsReviewOrder)), key=str)
    print("\n=== 3. 显式 SPARQL 业务规则 ===")
    print(f"规则文件: {args.rule}（OWL-RL 之外独立执行）")
    if applications:
        for application in applications:
            order = derived.value(application, EX.concludedOrder)
            event = derived.value(application, EX.supportingEvent)
            supplier = derived.value(application, EX.supportingSupplier)
            print(
                f"R-01 命中: order={local_id(order, ORDER_PREFIX)}, "
                f"event={local_id(event, EVENT_PREFIX)}, supplier={compact(supplier, working)}"
            )
    else:
        print("R-01 未命中")

    print("\n=== 4. 最终结论与本 Demo 依据 ===")
    explanations = []
    for application in applications:
        order = derived.value(application, EX.concludedOrder)
        event = derived.value(application, EX.supportingEvent)
        supplier = derived.value(application, EX.supportingSupplier)
        direct = sorted(direct_types[event], key=str)
        supplier_name = working.value(supplier, EX.supplierName)
        order_status = working.value(order, EX.orderStatus)
        current = working.value(event, EX.isCurrent)
        evidence = {
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
        direct_labels = ", ".join(compact(value, working) for value in direct)
        print(f"订单{evidence['order']}需要供应风险复核（不是‘必然无法交付’）。")
        print(f"  事件{evidence['event']}直接类型: {direct_labels}")
        print(f"  本体公理推出: proc:SupplyDisruptionEvent")
        print(f"  事件当前有效={evidence['event_is_current']}，影响供应商={evidence['supplier']}")
        print(f"  订单状态={evidence['order_status']}，实际采购自={evidence['supplier']}")
        print(f"  命中规则={evidence['rule']}")
    if not review_orders:
        print("没有订单被标记为 NeedsReviewOrder；这不表示其他订单安全。")

    actual_ids = [local_id(order, ORDER_PREFIX) for order in review_orders]
    if args.expect == "one" and not args.expected_order:
        raise InvalidExperiment("--expect one 必须同时提供 --expected-order")
    if args.expect == "one" and actual_ids != [args.expected_order]:
        raise InvalidExperiment(f"预期仅订单 {args.expected_order} 命中，实际为 {actual_ids}")
    if args.expect == "none" and actual_ids:
        raise InvalidExperiment(f"预期规则不命中，实际为 {actual_ids}")
    result = {
        "base_triple_count": len(base),
        "axiom_loaded": axiom_loaded,
        "inferred_parent_events": [local_id(event, EVENT_PREFIX) for event, _ in inferred_parent_types],
        "needs_review_orders": actual_ids,
        "rule_applications": explanations,
    }
    print("\nRESULT_JSON=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=os.getenv("ONTOP_ENDPOINT", "http://ontop:8080/sparql"))
    parser.add_argument("--base-query", type=Path, default=Path("queries/phase2-base-facts.rq"))
    parser.add_argument("--ontology", type=Path, default=Path("ontop/phase2-extension.ttl"))
    parser.add_argument("--rule", type=Path, default=Path("rules/R-01-needs-review.rq"))
    parser.add_argument("--without-inheritance", action="store_true")
    parser.add_argument("--expect", choices=("one", "none"))
    parser.add_argument("--expected-order")
    return parser.parse_args()


def main() -> int:
    try:
        run(parse_args())
    except ServiceFailure as exc:
        print(f"SERVICE_ERROR: {exc}", file=sys.stderr)
        return 2
    except EmptyBaseFacts as exc:
        print(f"EMPTY_BASE_FACTS: {exc}", file=sys.stderr)
        return 3
    except InvalidExperiment as exc:
        print(f"EXPERIMENT_FAILURE: {exc}", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"REASONER_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
