from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, cast
from typing_extensions import TypeVar

from jinja2 import Environment

from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump

if TYPE_CHECKING:
    from zhenxun.services.ai.run.context import RunContext

AgentDepsT = TypeVar("AgentDepsT", default=Any)


class PromptTemplate(Generic[AgentDepsT]):
    """
    核心 Prompt 渲染引擎。
    基于 Jinja2 提供强大的模板变量替换功能。
    支持沙盒隔离的自定义过滤器(filters)、全局函数(globals)，
    """

    def __init__(
        self,
        template_string: str,
        custom_filters: dict[str, Callable] | None = None,
        custom_globals: dict[str, Any] | None = None,
    ):
        """
        初始化 Prompt 渲染引擎。

        参数:
            template_string: Jinja2 模板格式的提示词原文本。
            custom_filters: 挂载到 Jinja2 渲染环境的自定义过滤器字典，默认 None。
            custom_globals: 挂载到 Jinja2 渲染环境的全局变量或辅助函数字典，默认 None。
        """
        self.template_string = template_string

        self._env = Environment(autoescape=False)
        if custom_filters:
            self._env.filters.update(custom_filters)
        if custom_globals:
            self._env.globals.update(custom_globals)

        try:
            self._template = (
                self._env.from_string(template_string) if template_string else None
            )
        except Exception as e:
            logger.error(f"PromptTemplate 模板语法编译失败: {e}")
            self._template = None

    def format_with_context(self, context: "RunContext[AgentDepsT]") -> str:
        """从运行上下文中自动提取依赖和状态字典，用于模板渲染"""

        vars_dict: dict[str, Any] = {
            "ctx": context,
            "ctx_deps": context.deps,
            "ctx_state": context.state,
            "ctx_shared": context.shared_state,
        }

        flat_deps = {}
        if context.deps is not None:
            if hasattr(context.deps, "model_dump"):
                flat_deps.update(model_dump(cast(Any, context.deps), exclude_none=True))
            elif hasattr(context.deps, "__dict__"):
                flat_deps.update(context.deps.__dict__)
            elif isinstance(context.deps, dict):
                flat_deps.update(context.deps)

        vars_dict.update(flat_deps)

        if context.state:
            collisions = set(flat_deps.keys()) & set(context.state.keys())
            if collisions:
                logger.warning(
                    f"Prompt 模板变量发生名称冲突: {collisions}。"
                    "`state` 已覆盖 `deps` 中的同名变量。"
                    "建议在模板中使用安全隔离对象获取（如 {{ ctx_state.xxx }}）。"
                )
            vars_dict.update(context.state)

        return self.render(**vars_dict)

    def render(self, **variables: Any) -> str:
        if not self._template:
            return self.template_string or ""

        try:
            return self._template.render(**variables)
        except Exception as e:
            logger.error(
                f"Jinja2 Prompt 模板渲染失败！\n"
                f"错误信息: {e}\n"
                f"请检查传入的变量或 Prompt 模板语法是否有误。\n"
                f"模板原内容截断: {self.template_string[:150]}...",
                e=e,
            )
            raise ValueError(f"Prompt 模板渲染异常: {e}") from e

    def __str__(self) -> str:
        return self.template_string
