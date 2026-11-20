"""用保存的证据离线重放同一条规则应用，不接触当前业务事实。"""
import argparse
import importlib.metadata
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from rdflib import Graph

from decision.evidence import InvalidEvidence, digest, loaded_core_bytes, verify_code
from reasoner.core import compute_from_facts


def _unmet(reason):
    return InvalidEvidence(f'重放前提未满足：{reason}')


def replay_evidence(evidence, settings):
    """先核验事实快照文本，再核验依赖与代码提交，最后只用保存的原文计算。

    成功只表示记录中的规则、订单和事件绑定再次出现。
    这不证明 Ontop 当时加载了保存的 Mapping，Agent 说明也不参与计算。
    """
    try:
        facts = evidence['facts_nt']
        # 完整性只比较已保存文本与其 SHA-256，必须先于推理。
        if not isinstance(facts, str) or digest(facts.encode('utf-8')) != evidence['facts_hash']:
            raise InvalidEvidence('事实快照完整性哈希不匹配')
        fingerprint = evidence['semantic_fingerprint']
        for library in ('rdflib', 'owlrl'):
            if importlib.metadata.version(library) != fingerprint[library]:
                raise _unmet(f'依赖版本不匹配：{library}')
        core_bytes = loaded_core_bytes()
        if digest(core_bytes) != fingerprint['core']:
            raise _unmet('推理核心与记录的指纹不一致，请检出记录的代码提交')
        try:
            commit = verify_code(replace(settings, code_commit=evidence['code_commit']), core_bytes)
        except InvalidEvidence as exc:
            raise _unmet(str(exc)) from exc
        for name in ('ontop_ontology', 'ontology', 'rule'):
            text = evidence[name + '_text']
            if not isinstance(text, str) or digest(text.encode('utf-8')) != fingerprint[name]:
                raise _unmet(f'保存的推理原文与指纹不一致：{name}')
        expected = {name: evidence['application'][name] for name in ('rule', 'order', 'event')}
        # 只把保存的事实图、推理侧本体和规则送入同一计算，不读当前文件或在线端点。
        with tempfile.TemporaryDirectory() as directory:
            ontology = Path(directory) / 'saved-extension.ttl'
            rule = Path(directory) / 'saved-R-01.rq'
            ontology.write_text(evidence['ontology_text'], encoding='utf-8')
            rule.write_text(evidence['rule_text'], encoding='utf-8')
            graph = Graph().parse(data=facts, format='nt')
            result = compute_from_facts(base_facts=graph, ontology=ontology, rule=rule)
        bindings = [{name: item[name] for name in expected} for item in result.rule_applications]
        if expected not in bindings:
            raise InvalidEvidence('离线重放未得到记录中的同一条规则应用')
        return {'matched': True, 'rule_application': expected, 'code_commit': commit}
    except (KeyError, TypeError, AttributeError) as exc:
        raise _unmet('缺少快照、版本或绑定字段') from exc


def main():
    parser = argparse.ArgumentParser(description='使用导出的证据离线重放同一条规则应用')
    parser.add_argument('snapshot', type=Path, help='包含 evidence 的决策 JSON，或证据 JSON')
    args = parser.parse_args()
    from decision.service import DecisionService
    try:
        saved = json.loads(args.snapshot.read_text(encoding='utf-8'))
        result = DecisionService().replay(saved.get('evidence', saved))
    except (InvalidEvidence, ValueError, OSError) as exc:
        parser.exit(1, f'离线重放失败：{exc}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
