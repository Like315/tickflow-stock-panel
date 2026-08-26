"""投资专家领域异常。"""


class PaperAgentError(Exception):
    """投资专家领域异常基类。"""


class PaperAgentSchemaMigrationError(PaperAgentError):
    """投资专家持久化结构无法安全迁移。"""


class StrategyLabError(PaperAgentError):
    """策略生成或参数优化任务执行失败。"""


class StrategyDependencyUnavailableError(StrategyLabError):
    """策略实验所需的行情或策略运行时不可用。"""
