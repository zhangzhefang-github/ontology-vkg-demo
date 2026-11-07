from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rdflib import Graph, Literal, URIRef

from reasoner.core import (
    EmptyBaseFacts,
    InvalidExperiment,
    ReasoningResult,
    ResultJSON,
    ServiceFailure,
    compute,
)


def compact(term, graph: Graph) -> str:
    if isinstance(term, Literal):
        return term.n3(graph.namespace_manager)
    try:
        return graph.namespace_manager.normalizeUri(term)
    except Exception:
        return str(term)


def print_result(result: ReasoningResult) -> None:
    base = result.base_facts
    print("=== 1. Ontop 导出的基础事实 ===")
    triples = sorted(base, key=lambda triple: tuple(str(value) for value in triple))
    for subject, predicate, obj in triples:
        print(f"{compact(subject, base)} {compact(predicate, base)} {compact(obj, base)}")

    print("\n=== 2. OWL-RL 类型推理新增的父类型事实 ===")
    print(f"加载继承公理: {result.axiom_loaded}")
    if result.inferred_parent_types:
        for event, parent in result.inferred_parent_types:
            print(f"{compact(event, base)} rdf:type {compact(parent, base)}")
    else:
        print("（无）")

    print("\n=== 3. 显式 SPARQL 业务规则 ===")
    print(f"规则文件: {result.rule_path}（OWL-RL 之外独立执行）")
    if result.rule_applications:
        for evidence in result.rule_applications:
            supplier = compact(URIRef(evidence["supplier_uri"]), base)
            print(
                f"R-01 命中: order={evidence['order']}, "
                f"event={evidence['event']}, supplier={supplier}"
            )
    else:
        print("R-01 未命中")

    print("\n=== 4. 最终结论与本 Demo 依据 ===")
    for evidence in result.rule_applications:
        direct_labels = ", ".join(
            compact(URIRef(value), base) for value in evidence["event_direct_types"]
        )
        print(f"订单{evidence['order']}需要供应风险复核（不是‘必然无法交付’）。")
        print(f"  事件{evidence['event']}直接类型: {direct_labels}")
        print(f"  本体公理推出: proc:SupplyDisruptionEvent")
        print(f"  事件当前有效={evidence['event_is_current']}，影响供应商={evidence['supplier']}")
        print(f"  订单状态={evidence['order_status']}，实际采购自={evidence['supplier']}")
        print(f"  命中规则={evidence['rule']}")
    if not result.needs_review_orders:
        print("没有订单被标记为 NeedsReviewOrder；这不表示其他订单安全。")


def validate_expectation(
    result: ReasoningResult, expect: str | None, expected_order: str | None,
) -> None:
    actual_ids = result.needs_review_orders
    if expect == "one" and actual_ids != [expected_order]:
        raise InvalidExperiment(f"预期仅订单 {expected_order} 命中，实际为 {actual_ids}")
    if expect == "none" and actual_ids:
        raise InvalidExperiment(f"预期规则不命中，实际为 {actual_ids}")


def run(args: argparse.Namespace) -> ResultJSON:
    if args.expect == "one" and not args.expected_order:
        raise InvalidExperiment("--expect one 必须同时提供 --expected-order")
    result = compute(
        endpoint=args.endpoint,
        base_query=args.base_query,
        ontology=args.ontology,
        rule=args.rule,
        without_inheritance=args.without_inheritance,
    )
    print_result(result)
    validate_expectation(result, args.expect, args.expected_order)
    summary = result.to_json()
    print("\nRESULT_JSON=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


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
