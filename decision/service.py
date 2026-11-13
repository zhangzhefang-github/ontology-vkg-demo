from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import pymysql

from decision.database import connect, occupied, decision_response
from decision.evidence import (InvalidEvidence, canonical_json, semantic_material, verify_code, capture)

from reasoner.core import ResultJSON, RuleApplication, compute


# RDFLib/pyparsing share parser state; concurrent tools must serialize reasoning.
_REASONING_LOCK = threading.RLock()


class DecisionResponse(TypedDict):
    id: int
    order: str
    event: str
    approval_status: str
    execution_status: str | None
    occupancy: int | None
    created_at: str
    action: str
    assigned_role: str
    description: str
    evidence: dict
    agent_note: dict


class QueryRuleApplication(RuleApplication):
    decision: DecisionResponse | None


class QueryResponse(ResultJSON):
    conclusion: str
    rule_applications: list[QueryRuleApplication]


@dataclass(frozen=True)
class Settings:
    endpoint: str = 'http://ontop:8080/sparql'
    base_query: Path = Path('queries/phase2-base-facts.rq')
    ontology: Path = Path('ontop/phase2-extension.ttl')
    rule: Path = Path('rules/R-01-needs-review.rq')
    mapping: Path = Path('ontop/mapping.obda')
    ontop_ontology: Path = Path('ontop/ontology.ttl')
    db_host: str = 'mysql'
    db_user: str = 'decision_agent'
    db_password: str = ''
    code_commit: str = ''
    git_dir: Path = Path('/git')

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            endpoint=os.getenv('ONTOP_ENDPOINT', cls.endpoint),
            db_host=os.getenv('DECISION_DB_HOST', cls.db_host),
            db_user=os.getenv('DECISION_DB_USER', cls.db_user),
            db_password=os.getenv('DECISION_DB_PASSWORD', ''),
            code_commit=os.getenv('CODE_COMMIT', ''),
            git_dir=Path(os.getenv('DECISION_GIT_DIR', '/git')),
        )


class DecisionService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    def _compute(self):
        with _REASONING_LOCK:
            return compute(
                endpoint=self.settings.endpoint, base_query=self.settings.base_query,
                ontology=self.settings.ontology, rule=self.settings.rule,
                without_inheritance=False,
            )

    def query(self) -> QueryResponse:
        """Read current rule applications without changing any decision state."""
        result = self._compute()
        response = {**result.to_json(), 'conclusion': '需要供应风险复核'}
        connection = connect(self.settings)
        try:
            for application in response['rule_applications']:
                application['decision'] = occupied(connection, application['order'], application['event'])
        finally:
            connection.close()
        return response

    def submit(self, order: str, event: str, agent_note: str = '', **ignored_action_parameters) -> DecisionResponse:
        """Only the named live rule application may create an immutable proposal."""
        connection = connect(self.settings)
        try:
            existing = occupied(connection, order, event)
            if existing:
                return existing
            connection.rollback()
            with _REASONING_LOCK:
                material, fingerprint = semantic_material(self.settings)
                commit = verify_code(self.settings, material['core'])
                result = self._compute()
                _, after = semantic_material(self.settings)
                if after != fingerprint:
                    raise InvalidEvidence('推理输入在计算期间发生变化')
                application = next((item for item in result.rule_applications
                                    if item['order'] == str(order) and item['event'] == event), None)
                if application is None:
                    raise InvalidEvidence('指定的规则应用已经不存在')
                evidence = capture(result, application, material, fingerprint, commit)
            description = f'订单 {application["order"]} 因事件 {application["event"]} 需要供应风险复核。'
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'INSERT INTO decision_record (order_id,event_id,evidence,agent_note,description) '
                        'VALUES (%s,%s,%s,%s,%s)',
                        (application['order'], application['event'], canonical_json(evidence), agent_note, description),
                    )
                    cursor.execute('SELECT * FROM decision_record WHERE id=%s', (cursor.lastrowid,))
                    created = decision_response(cursor.fetchone())
                connection.commit()
                return created
            except pymysql.IntegrityError as exc:
                connection.rollback()
                if exc.args[0] != 1062:
                    raise
            winner = occupied(connection, order, event)
            if winner is None:
                raise InvalidEvidence('占位在并发提交期间已释放，请重新提交')
            return winner
        finally:
            connection.close()
