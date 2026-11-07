"""Public core integration tests; run in the existing reasoner container."""

import contextlib
import io
import os
import tempfile
import threading
import urllib.parse
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from reasoner import silence_dependency_warnings
from reasoner.core import EmptyBaseFacts, InvalidExperiment, compute


def setUpModule():
    # unittest 开跑后会覆盖导入时的过滤，这里重新盖住依赖库告警。
    silence_dependency_warnings()


class CoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.inputs = dict(
            endpoint=os.getenv("ONTOP_ENDPOINT", "http://ontop:8080/sparql"),
            base_query=Path("queries/phase2-base-facts.rq"),
            ontology=Path("ontop/phase2-extension.ttl"),
            rule=Path("rules/R-01-needs-review.rq"),
        )

    def test_export_query_keeps_every_non_vocabulary_event_type(self):
        # The fixed Ontop sample cannot expose an unseen event-table type.
        # Serve a queryable RDF fixture at the HTTP boundary; compute and its
        # export query, OWL-RL closure and rule execution all remain real.
        facts = Graph().parse(data="""
            @prefix : <https://example.org/procurement#> .
            :order a :PurchaseOrder ; :orderedFrom :supplier ; :orderStatus "PENDING" .
            :supplier :supplierName "供应商" .
            :event a :AnotherConcreteEvent ; :affectsSupplier :supplier ; :isCurrent true .
        """, format="turtle")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                query = urllib.parse.parse_qs(body.decode())["query"][0]
                payload = facts.query(query).graph.serialize(format="turtle").encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.inputs["endpoint"] = f"http://127.0.0.1:{server.server_port}/sparql"
            try:
                result = compute(**self.inputs)
                event = URIRef("https://example.org/procurement#event")
                self.assertIn((event, RDF.type,
                               URIRef("https://example.org/procurement#AnotherConcreteEvent")),
                              result.base_facts)
                facts.add((event, RDF.type,
                           URIRef("https://example.org/procurement#SupplyDisruptionEvent")))
                with self.assertRaises(InvalidExperiment):
                    compute(**self.inputs)
            finally:
                server.shutdown()
                thread.join()

    def test_core_removes_only_vocabulary_types_before_computing(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "vocabulary-types.rq"
            text = self.inputs["base_query"].read_text(encoding="utf-8")
            text = text.replace("CONSTRUCT {", """CONSTRUCT {
                ?event a <http://www.w3.org/1999/02/22-rdf-syntax-ns#Statement>,
                    <http://www.w3.org/2000/01/rdf-schema#Resource>,
                    <http://www.w3.org/2002/07/owl#NamedIndividual>,
                    <https://elsewhere.example/ConcreteEvent> .
            """)
            query.write_text(text, encoding="utf-8")
            self.inputs["base_query"] = query
            result = compute(**self.inputs)
        event = URIRef("https://example.org/event/E01")
        self.assertEqual(set(result.base_facts.objects(event, RDF.type)), {
            URIRef("https://example.org/procurement#DeliverySuspensionEvent"),
            URIRef("https://elsewhere.example/ConcreteEvent"),
        })
        self.assertEqual(result.needs_review_orders, ["101"])
        self.assertEqual(set(result.rule_applications[0]["event_direct_types"]), {
            "https://example.org/procurement#DeliverySuspensionEvent",
            "https://elsewhere.example/ConcreteEvent",
        })

    def test_parent_in_base_facts_fails_in_both_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "premature-parent.rq"
            text = self.inputs["base_query"].read_text(encoding="utf-8")
            query.write_text(text.replace("CONSTRUCT {", """CONSTRUCT {
                ?event a <http://www.w3.org/2002/07/owl#Thing>,
                    <https://example.org/procurement#SupplyDisruptionEvent> .
            """), encoding="utf-8")
            self.inputs["base_query"] = query
            for without_inheritance in (False, True):
                with self.subTest(without_inheritance=without_inheritance):
                    with self.assertRaises(InvalidExperiment):
                        compute(**self.inputs, without_inheritance=without_inheritance)

    def test_without_inheritance_does_not_load_any_subclass_axioms(self):
        with tempfile.TemporaryDirectory() as directory:
            ontology = Path(directory) / "extension.ttl"
            ontology.write_text(
                self.inputs["ontology"].read_text(encoding="utf-8") +
                "\n:Supplier rdfs:subClassOf :BusinessParty .\n", encoding="utf-8",
            )
            rule = Path(directory) / "observe-axioms.rq"
            # A caller-supplied rule observes the actual reasoning graph, rather
            # than relying on the axiom_loaded flag or only the demo conclusion.
            rule.write_text("""
                PREFIX : <https://example.org/procurement#>
                PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
                CONSTRUCT { ?order a :NeedsReviewOrder }
                WHERE {
                    VALUES (?child ?parent ?order) {
                        (:DeliverySuspensionEvent :SupplyDisruptionEvent
                         <https://example.org/purchase-order/101>)
                        (:Supplier :BusinessParty <https://example.org/purchase-order/102>)
                    }
                    ?child rdfs:subClassOf ?parent .
                }
            """, encoding="utf-8")
            self.inputs.update(ontology=ontology, rule=rule)
            normal = compute(**self.inputs)
            without = compute(**self.inputs, without_inheritance=True)
        self.assertEqual(normal.needs_review_orders, ["101", "102"])
        self.assertEqual(without.needs_review_orders, [])
        self.assertEqual(set(normal.base_facts), set(without.base_facts))

    def test_parent_anywhere_in_base_facts_fails_before_empty_type_check(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "parent-only.rq"
            query.write_text("""
                PREFIX : <https://example.org/procurement#>
                CONSTRUCT { ?event a :SupplyDisruptionEvent }
                WHERE { ?event :affectsSupplier ?supplier }
            """, encoding="utf-8")
            self.inputs["base_query"] = query
            for without_inheritance in (False, True):
                with self.subTest(without_inheritance=without_inheritance):
                    with self.assertRaises(InvalidExperiment):
                        compute(**self.inputs, without_inheritance=without_inheritance)

    def test_vocabulary_types_alone_are_not_concrete_event_types(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "vocabulary-only.rq"
            query.write_text("""
                PREFIX : <https://example.org/procurement#>
                CONSTRUCT {
                    ?event :affectsSupplier ?supplier ;
                        a <http://www.w3.org/2002/07/owl#Thing> .
                }
                WHERE { ?event :affectsSupplier ?supplier }
            """, encoding="utf-8")
            self.inputs["base_query"] = query
            with self.assertRaises(EmptyBaseFacts):
                compute(**self.inputs)

    def test_event_without_concrete_type_is_empty_base_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "missing-type.rq"
            query.write_text(
                "PREFIX : <https://example.org/procurement#> "
                "CONSTRUCT { ?event :affectsSupplier ?supplier } "
                "WHERE { ?event :affectsSupplier ?supplier }", encoding="utf-8",
            )
            self.inputs["base_query"] = query
            with self.assertRaises(EmptyBaseFacts):
                compute(**self.inputs)

    def test_supply_disruption_returns_evidence_without_printing(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with warnings.catch_warnings(), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            warnings.simplefilter("ignore", DeprecationWarning)
            result = compute(**self.inputs, without_inheritance=False)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(result.needs_review_orders, ["101"])
        event = URIRef("https://example.org/event/E01")
        direct_type = URIRef("https://example.org/procurement#DeliverySuspensionEvent")
        parent = URIRef("https://example.org/procurement#SupplyDisruptionEvent")
        self.assertIn((event, RDF.type, direct_type), result.base_facts)
        self.assertNotIn((event, RDF.type, parent), result.base_facts)
        self.assertEqual(result.inferred_parent_types, [(event, parent)])
        self.assertEqual(result.base_triple_count, 16)
        self.assertEqual(result.to_json()["needs_review_orders"], ["101"])
        self.assertEqual(result.rule_applications[0]["supplier"], "华北钢材")

    def test_without_inheritance_preserves_facts_but_has_no_review_orders(self):
        normal = compute(**self.inputs)
        without_inheritance = compute(**self.inputs, without_inheritance=True)
        self.assertEqual(set(without_inheritance.base_facts), set(normal.base_facts))
        self.assertFalse(without_inheritance.axiom_loaded)
        self.assertEqual(without_inheritance.inferred_parent_events, [])
        self.assertEqual(without_inheritance.needs_review_orders, [])
        self.assertEqual(without_inheritance.rule_applications, [])


if __name__ == "__main__":
    unittest.main()
