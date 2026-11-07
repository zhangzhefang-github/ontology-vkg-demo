"""Public core integration tests; run in the existing reasoner container."""

import contextlib
import io
import os
import tempfile
import unittest
import warnings
from pathlib import Path

from rdflib import URIRef
from rdflib.namespace import RDF

from reasoner.core import EmptyBaseFacts, compute


class CoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.inputs = dict(
            endpoint=os.getenv("ONTOP_ENDPOINT", "http://ontop:8080/sparql"),
            base_query=Path("queries/phase2-base-facts.rq"),
            ontology=Path("ontop/phase2-extension.ttl"),
            rule=Path("rules/R-01-needs-review.rq"),
        )

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
