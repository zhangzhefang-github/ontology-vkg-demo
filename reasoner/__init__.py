"""Phase 2 OWL-RL and explicit SPARQL rule demo."""

import warnings


def silence_dependency_warnings():
    """忽略 RDFLib 7.1.4 调用 Pyparsing 旧接口产生的弃用告警。

    告警来自依赖库，不影响推理结果。unittest 会在开跑前把过滤改成默认显示，
    因此测试模块需要在 setUpModule 里再次调用。
    """
    from pyparsing.warnings import PyparsingDeprecationWarning

    warnings.filterwarnings("ignore", category=PyparsingDeprecationWarning)


silence_dependency_warnings()
