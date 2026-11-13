"""Evidence capture: integrity, business preconditions and code provenance."""
import hashlib
import importlib.metadata
import json
import subprocess
from pathlib import Path

from rdflib import BNode, URIRef

from reasoner import core

_LOADED_CORE_BYTES = Path(core.__file__).read_bytes()


class InvalidEvidence(ValueError):
    """A proposal cannot be bound to reproducible, stable evidence."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def precondition_hash(bound_facts, fingerprint):
    return digest(canonical_json({'bound_facts': bound_facts, 'semantic_fingerprint': fingerprint}).encode())


def semantic_material(settings):
    paths = {
        'mapping': settings.mapping,
        'ontop_ontology': settings.ontop_ontology,
        'ontology': settings.ontology,
        'base_query': settings.base_query,
        'rule': settings.rule,
        'core': Path(core.__file__),
    }
    content = {name: path.read_bytes() for name, path in paths.items()}
    if content['core'] != _LOADED_CORE_BYTES:
        raise InvalidEvidence('推理核心在加载后发生变化，必须重启进程')
    fingerprint = {name: digest(value) for name, value in content.items()}
    fingerprint.update({name: importlib.metadata.version(name) for name in ('rdflib', 'owlrl')})
    return content, fingerprint


def verify_code(settings, core_bytes):
    if not settings.code_commit:
        raise InvalidEvidence('代码提交号必填')
    command = ['git', '--git-dir=' + str(settings.git_dir)]
    try:
        commit = subprocess.check_output(
            [*command, 'rev-parse', '--verify', settings.code_commit + '^{commit}'],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        recorded = subprocess.check_output(
            [*command, 'show', commit + ':reasoner/core.py'], stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise InvalidEvidence('无法恢复记录的代码提交') from exc
    if digest(recorded) != digest(core_bytes):
        raise InvalidEvidence('推理核心哈希与代码提交不一致')
    return commit


def bound_facts(graph, application):
    """从规则应用提取进入业务前提哈希的绑定事实。"""
    order = URIRef(core.ORDER_PREFIX + application['order'])
    event = URIRef(core.EVENT_PREFIX + application['event'])
    return {
        'order_status': application['order_status'],
        'order_supplier': str(graph.value(order, core.EX.orderedFrom)),
        'event_is_current': application['event_is_current'],
        'affected_supplier': str(graph.value(event, core.EX.affectsSupplier)),
        'event_direct_types': list(application['event_direct_types']),
    }


def capture(result, application, material, fingerprint, commit):
    graph = result.base_facts
    if any(isinstance(node, BNode) for triple in graph for node in triple):
        raise InvalidEvidence('基础事实含有空白节点，不能创建决策记录')
    facts_nt = ''.join(sorted(graph.serialize(format='nt').splitlines(keepends=True)))
    bound = bound_facts(graph, application)
    return {
        'facts_nt': facts_nt, 'facts_hash': digest(facts_nt.encode()),
        'application': application, 'bound_facts': bound,
        'semantic_fingerprint': fingerprint,
        'precondition_hash': precondition_hash(bound, fingerprint),
        'code_commit': commit,
        'ontop_ontology_text': material['ontop_ontology'].decode(),
        'ontology_text': material['ontology'].decode(),
        'rule_text': material['rule'].decode(),
    }
