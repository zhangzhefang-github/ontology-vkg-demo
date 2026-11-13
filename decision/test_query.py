"""Acceptance at the decision module seam, against real Ontop/MySQL."""
import os
import unittest


from decision.service import DecisionService, Settings
from reasoner.core import ServiceFailure
from reasoner import silence_dependency_warnings


def setUpModule():
    silence_dependency_warnings()


class QueryTests(unittest.TestCase):
    def test_query_explains_only_orders_needing_supply_review(self):
        result = DecisionService(Settings.from_env()).query()
        self.assertEqual(result['needs_review_orders'], ['101'])
        application = result['rule_applications'][0]
        self.assertEqual(application['order'], '101')
        self.assertEqual(application['event'], 'E01')
        self.assertEqual(application['rule'], 'R-01')
        self.assertEqual(application['event_direct_types'], [
            'https://example.org/procurement#DeliverySuspensionEvent'])
        self.assertEqual(application['event_inferred_type'],
                         'https://example.org/procurement#SupplyDisruptionEvent')
        self.assertEqual(result['conclusion'], '需要供应风险复核')
        self.assertTrue(result['axiom_loaded'])

    def test_service_failure_is_not_an_empty_safe_result(self):
        settings = Settings(endpoint='http://127.0.0.1:1/sparql')
        with self.assertRaises(ServiceFailure):
            DecisionService(settings).query()

    def test_no_match_does_not_claim_other_orders_are_safe(self):
        import pymysql

        # The management credential is only a fixture capability, never MCP config.
        connection = pymysql.connect(host=os.getenv('MYSQL_HOST', 'mysql'),
                                     user='root', password=os.environ['TEST_DB_PASSWORD'],
                                     database='procurement', autocommit=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE supply_disruption_event SET is_current=FALSE WHERE event_id='E01'")
            result = DecisionService(Settings.from_env()).query()
            self.assertEqual(result['needs_review_orders'], [])
            self.assertEqual(result['rule_applications'], [])
            self.assertNotIn('safe_orders', result)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE supply_disruption_event SET is_current=TRUE WHERE event_id='E01'")
            connection.close()
