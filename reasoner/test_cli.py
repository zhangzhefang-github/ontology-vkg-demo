"""CLI boundary checks, including proof of zero HTTP requests on invalid input."""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "reasoner.run", *args],
            capture_output=True, text=True, timeout=30,
        )

    def test_normal_cli_preserves_json_schema(self):
        completed = self.run_cli("--expect", "one", "--expected-order", "101")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout.split("RESULT_JSON=", 1)[1])
        self.assertEqual(set(summary), {
            "base_triple_count", "axiom_loaded", "inferred_parent_events",
            "needs_review_orders", "rule_applications",
        })
        self.assertEqual(summary["needs_review_orders"], ["101"])
        self.assertEqual(completed.stdout.count("=== "), 4)

    def test_expectation_mismatches_are_experiment_failures(self):
        for args in (("--expect", "one", "--expected-order", "999"), ("--expect", "none")):
            with self.subTest(args=args):
                completed = self.run_cli(*args)
                self.assertEqual(completed.returncode, 4, completed.stderr)
                self.assertIn("EXPERIMENT_FAILURE:", completed.stderr)
                self.assertNotIn("RESULT_JSON=", completed.stdout)

    def test_core_failures_keep_cli_exit_codes(self):
        cases = [
            ("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o FILTER(false) }", 3, "EMPTY_BASE_FACTS:"),
            ("PREFIX : <https://example.org/procurement#> "
             "CONSTRUCT { ?event :affectsSupplier ?supplier } "
             "WHERE { ?event :affectsSupplier ?supplier }", 3, "EMPTY_BASE_FACTS:"),
            ("PREFIX : <https://example.org/procurement#> "
             "CONSTRUCT { ?event a :SupplyDisruptionEvent ; :affectsSupplier ?supplier } "
             "WHERE { ?event :affectsSupplier ?supplier }", 4, "EXPERIMENT_FAILURE:"),
            ("this is not SPARQL", 2, "SERVICE_ERROR:"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "facts.rq"
            for text, code, marker in cases:
                with self.subTest(code=code, query=text):
                    query.write_text(text, encoding="utf-8")
                    completed = self.run_cli("--base-query", str(query))
                    self.assertEqual(completed.returncode, code, completed.stderr)
                    self.assertIn(marker, completed.stderr)
            missing = Path(directory) / "missing.ttl"
            completed = self.run_cli("--ontology", str(missing))
            self.assertEqual(completed.returncode, 5, completed.stderr)
            self.assertIn("REASONER_ERROR:", completed.stderr)

    def test_missing_expected_order_fails_before_querying_ontop(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(self.path)
                self.send_error(503)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                completed = subprocess.run(
                    [sys.executable, "-B", "-m", "reasoner.run", "--expect", "one",
                     "--endpoint", f"http://127.0.0.1:{server.server_port}/sparql"],
                    capture_output=True, text=True, timeout=15,
                )
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(completed.returncode, 4, completed.stderr)
        self.assertEqual(requests, [])
        self.assertEqual(completed.stdout, "")
        self.assertIn("EXPERIMENT_FAILURE: --expect one 必须同时提供 --expected-order", completed.stderr)


if __name__ == "__main__":
    unittest.main()
