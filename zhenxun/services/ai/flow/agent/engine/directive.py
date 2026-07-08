from collections import defaultdict
from collections.abc import Awaitable, Callable

from zhenxun.services.ai.flow.agent.models import AgentRunResources, AgentState
from zhenxun.services.ai.run.models import AgentRunResult, HandoffPayload
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.ai.utils.logger import log_agent as logger
from zhenxun.utils.pydantic_compat import model_construct
from zhenxun.utils.utils import infer_plugin_namespace

DirectiveHandlerFunc = Callable[
    [AgentState, AgentRunResources, ToolResult], Awaitable[None]
]


class DirectiveManager:
    """工具指令路由注册中心"""

    def __init__(self):
        self._handlers: dict[str, dict[str, DirectiveHandlerFunc]] = defaultdict(dict)

    def register(
        self, name: str, handler: DirectiveHandlerFunc, namespace: str = "global"
    ) -> None:
        self._handlers[namespace][name] = handler
        logger.debug(f"已注册工具副作用指令: '{name}' -> Namespace: '{namespace}'")

    def get_handler(
        self, name: str, namespace: str = "global"
    ) -> DirectiveHandlerFunc | None:
        """优先从指定 namespace 找，找不到回退到 global"""
        ns_dict = self._handlers.get(namespace, {})
        if name in ns_dict:
            return ns_dict[name]
        return self._handlers.get("global", {}).get(name)


directive_manager = DirectiveManager()


def directive(name: str | None = None, namespace: str | None = None):
    """
    注册一个自定义工具副作用指令处理器的装饰器。

    参数:
        name: 指令的名称，如果不填则默认使用被装饰的函数名。
        namespace: 插件命名空间，如果不填则基于代码调用栈自动推断。
    """

    def decorator(func: DirectiveHandlerFunc):
        dir_name = name or func.__name__
        ns = namespace if namespace is not None else infer_plugin_namespace()
        directive_manager.register(dir_name, func, ns)
        return func

    return decorator


@directive("submit_structured", namespace="global")
async def handle_submit_structured(
    state: AgentState, resources: AgentRunResources, tool_res: ToolResult
) -> None:
    parsed_obj = (
        tool_res.directive.payload.get("parsed_obj") if tool_res.directive else None
    )
    logger.debug("✅ 拦截到结构化结果提交，结束循环。")
    state.is_finished = True
    state.final_result = model_construct(
        AgentRunResult,
        output=None,
        messages=state.messages,
        structured_data=parsed_obj,
        usage=state.usage,
    )


@directive("end_run", namespace="global")
async def handle_end_run(
    state: AgentState, resources: AgentRunResources, tool_res: ToolResult
) -> None:
    output = (
        tool_res.directive.payload.get("output", tool_res.output)
        if tool_res.directive
        else tool_res.output
    )
    logger.debug("✅ 捕获到工具发出的终止信号，提前结束推理循环。")
    state.is_finished = True
    state.final_result = model_construct(
        AgentRunResult,
        output=output,
        messages=state.messages,
        usage=state.usage,
    )


@directive("handoff", namespace="global")
async def handle_handoff(
    state: AgentState, resources: AgentRunResources, tool_res: ToolResult
) -> None:
    payload = tool_res.directive.payload if tool_res.directive else {}
    handoff = HandoffPayload(
        target=payload.get("target", "unknown"),
        reason=payload.get("reason", ""),
        context_data=payload.get("context_data", ""),
    )
    output_text = f"已触发控制权移交 -> {handoff.target}。原因: {handoff.reason}"
    logger.info(f"✅ 拦截到移交(Handoff)信号: 移交给 -> {handoff.target}。结束循环。")
    state.is_finished = True
    state.final_result = model_construct(
        AgentRunResult,
        output=output_text,
        messages=state.messages,
        usage=state.usage,
        handoff=handoff,
    )
