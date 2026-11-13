from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from reasoner.core import ResultJSON, compute


class QueryResponse(ResultJSON):
    conclusion: str


@dataclass(frozen=True)
class Settings:
    endpoint: str = 'http://ontop:8080/sparql'
    base_query: Path = Path('queries/phase2-base-facts.rq')
    ontology: Path = Path('ontop/phase2-extension.ttl')
    rule: Path = Path('rules/R-01-needs-review.rq')

    @classmethod
    def from_env(cls) -> Settings:
        return cls(endpoint=os.getenv('ONTOP_ENDPOINT', cls.endpoint))


class DecisionService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    def query(self) -> QueryResponse:
        """Read current rule applications without changing any decision state."""
        result = compute(
            endpoint=self.settings.endpoint,
            base_query=self.settings.base_query,
            ontology=self.settings.ontology,
            rule=self.settings.rule,
            without_inheritance=False,
        )
        return {**result.to_json(), 'conclusion': '需要供应风险复核'}
