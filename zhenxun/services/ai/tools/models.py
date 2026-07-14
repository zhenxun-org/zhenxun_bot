"""
工具系统域类型定义
"""

from dataclasses import dataclass, field
import fnmatch
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.ai.core.messages import ToolCallPart, UsageInfo
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.utils.pydantic_compat import model_dump, model_validate


class DirectivePayload(BaseModel):
    """工具执行产生的副作用控制流指令载荷"""

    name: str
    """指令名称，对应 directive_manager 中的注册名"""
    payload: dict[str, Any] = Field(default_factory=dict)
    """传递给指令处理器的具体数据"""


class ToolResult(BaseModel):
    """结构化的工具执行结果模型"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    output: Any = Field(...)
    """大模型实际看到的执行结果，可以是字符串、字典等序列化对象。"""
    usage: UsageInfo | None = Field(default=None)
    """子智能体或复杂工具消耗的 Token 统计，将向主流程冒泡累加。"""
    is_error: bool = Field(default=False)
    """是否发生了业务级别的错误"""
    is_retryable: bool = Field(default=True)
    """标记该错误是否允许大模型进行自愈反思重试"""
    directive: DirectivePayload | None = Field(default=None)
    """工具执行产生的副作用指令（如移交、结束运行等），供底层的指令路由引擎调度"""

    def as_error(self, is_retryable: bool = True) -> "ToolResult":
        """链式方法：标记此结果为错误，并引导大模型在下一轮进行重试自愈"""
        self.is_error = True
        self.is_retryable = is_retryable
        return self

    def as_fatal(self) -> "ToolResult":
        """链式方法：标记此结果为致命错误，立即中断大模型的思考"""
        self.is_error = True
        self.is_retryable = False
        return self


class EndRunResult(ToolResult):
    """强制结束运行并返回结果给用户"""

    def __init__(self, output: Any, **kwargs):
        super().__init__(
            output=output,
            directive=DirectivePayload(name="end_run", payload={"output": output}),
            **kwargs,
        )


class HandoffResult(ToolResult):
    """触发智能体控制权物理移交"""

    def __init__(self, target: str, reason: str = "", context_data: Any = "", **kwargs):
        super().__init__(
            output=f"已触发控制权移交 -> {target}。原因: {reason}",
            directive=DirectivePayload(
                name="handoff",
                payload={
                    "target": target,
                    "reason": reason,
                    "context_data": context_data,
                },
            ),
            **kwargs,
        )


class StructuredSubmissionResult(ToolResult):
    """提交结构化解析结果并结束运行"""

    def __init__(self, output: Any, parsed_obj: Any, **kwargs):
        super().__init__(
            output=output,
            directive=DirectivePayload(
                name="submit_structured", payload={"parsed_obj": parsed_obj}
            ),
            **kwargs,
        )


class StateSyncResult(ToolResult):
    """
    状态同步结果模型。
    除了返回工具输出外，允许开发者向大模型发送一条“系统通知”，系统会自动将其追加到上下文中，防止大模型产生幻觉。
    """

    state_notice: str | None = Field(default=None)
    """状态同步通知文本，将被自动转化为 SystemPrompt 发送给大模型。"""

    def with_state_notice(self, notice: str) -> "StateSyncResult":
        """链式方法：设置状态同步通知"""
        self.state_notice = notice
        return self


class ToolResultChunk(BaseModel):
    """流式工具执行结果片段模型"""

    content: str = Field(...)
    """流式输出的文本片段"""
    status: str = Field(default="running")
    """当前状态 (如 running, finished)"""
    metadata: dict[str, Any] | None = Field(default=None)
    """携带的附加数据 (如进度比例、图片等)"""


class ToolOptions(BaseModel):
    """工具的高阶配置选项"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    silent: bool = Field(default=False)
    """是否静默执行，工具执行过程与结果不会作为界面流渲染给用户。"""
    strict: bool = Field(default=False)
    """是否开启严格的 JSON Schema 验证模式，开启后大模型的参数将不接受额外属性。"""
    max_usage_count: int | None = Field(default=None)
    """单次 Agent 会话中的最大允许调用次数，用于防止大模型陷入死循环调用。"""
    capabilities: list[Any] = Field(default_factory=list)
    """当前工具专属的生命周期拦截器 (Capabilities) 列表。"""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """额外扩展元数据字典，可供其他系统或自定义中间件读取。"""
    sandbox_requirements: dict[str, list[str]] | None = Field(default=None)
    """声明该工具在沙箱中执行时的环境依赖要求。"""
    tags: list[str] = Field(default_factory=list)
    """用于智能字符串路由和能力聚合的标签列表。"""
    max_retries: int | None = Field(default=None)
    """工具级别的局部重试上限。优先级高于全局配置。"""
    args_schema: type[BaseModel] | None = Field(default=None)
    """工具的 Pydantic 数据模型约束。大模型将以此 Schema 输出 JSON。"""
    require_intent: bool = Field(default=False)
    """是否强制要求大模型在调用此工具时提供意图 (_intent)。
    有助于减少幻觉和提高调用准确率。
    """
    concurrency: Literal["shared", "exclusive"] = Field(default="shared")
    """工具在批量调用时的并发策略。
    shared 可与其他 shared 并行，exclusive 会阻塞前后工具的执行。
    """

    def merge(self, other: "ToolOptions | None") -> "ToolOptions":
        """组合模式底层：合并另一个 ToolOptions，other 中的非默认值将覆盖当前值"""
        if not other:
            return self
        merged_data = model_dump(self, exclude_unset=False)
        other_data = model_dump(other, exclude_unset=True)

        if other.capabilities:
            merged_data["capabilities"] = self.capabilities + other.capabilities
        if other.metadata:
            merged_data["metadata"] = {**self.metadata, **other.metadata}
        if other.tags:
            merged_data["tags"] = list(set(self.tags + other.tags))

        for k, v in other_data.items():
            if k not in ("capabilities", "metadata", "tags"):
                merged_data[k] = v
        return model_validate(ToolOptions, merged_data)


class ToolkitConfig(BaseModel):
    """工具箱全局配置对象 (用于声明式配置解析)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    prefix: str | None = Field(default=None)
    """工具名称前缀"""
    include: list[str] | None = Field(default=None)
    """允许注册的工具名白名单"""
    exclude: list[str] | None = Field(default=None)
    """排除注册的工具名黑名单"""
    shared_options: ToolOptions | None = Field(default=None)
    """所有子工具默认继承的高阶配置项"""


class ToolOverride(BaseModel):
    """工具配置动态覆盖载体"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    """要覆盖的目标工具名称（在全局注册表或 Provider 中的原始名称）"""

    new_name: str | None = None
    """克隆后的新工具名称。如果为空，则保持原名称"""

    description: str | None = None
    """覆盖后的新描述。大模型将根据此新描述决定工具调用时机"""

    options: ToolOptions | None = None
    """用于覆盖该工具底层行为的高阶配置项"""

    def to_tool_options(self) -> ToolOptions:
        return self.options or ToolOptions()

    async def resolve(self, context: RunContext | None = None) -> "ResolvedToolPayload":
        from zhenxun.services.ai.tools.engine.registry import tool_provider_manager

        payload = await tool_provider_manager.resolve_tools(
            [self.name], context=context
        )
        if payload and payload.tools:
            base_tool = payload.tools[0]
            if hasattr(base_tool, "clone_with_options"):
                cloned_tool = base_tool.clone_with_options(self)
                if hasattr(cloned_tool, "resolve"):
                    return await cloned_tool.resolve(context)
                return ResolvedToolPayload(tools=[cloned_tool])
            else:
                logger.warning(f"工具 {self.name} 不支持动态覆盖，将原样装配。")
                if hasattr(base_tool, "resolve"):
                    return await base_tool.resolve(context)
                return ResolvedToolPayload(tools=[base_tool])

        logger.warning(f"ToolOverride 找不到目标基础工具: {self.name}")
        return ResolvedToolPayload()


class ValidatedToolCall(BaseModel):
    """工具调用验证结果载体"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    call: ToolCallPart = Field(...)
    """原始工具调用部件"""
    tool: Any | None = Field(default=None)
    """匹配到的目标工具实例"""
    args_valid: bool = Field(default=False)
    """参数是否成功通过校验"""
    validated_args: dict[str, Any] | None = Field(default=None)
    """通过验证并反序列化后的参数字典"""
    validation_error: BaseException | None = Field(default=None)
    """验证失败时的异常信息"""
    intent: str | None = Field(default=None)
    """从参数中剥离出的大模型调用意图 (_intent)"""


class Query(BaseModel):
    """
    工具的声明式查询对象。
    用于在 Agent 中精确或批量筛选加载特定命名空间、特定标签的工具。
    """

    name: str | list[str] | None = Field(default=None)
    """如果提供，则工具的最终解析名称必须等于该字符串或在列表中。"""
    toolkit: str | list[str] | None = Field(default=None)
    """如果提供，则工具必须属于指定的 Toolkit (支持字符串或列表)。"""
    tags: list[str] | None = Field(default=None)
    """如果提供，则工具必须包含这里列出的所有标签 (交集/AND匹配)。"""
    exclude_tags: list[str] | None = Field(default=None)
    """如果提供，则工具不能包含这里列出的任何标签 (排斥过滤)。"""
    namespace: str | None = Field(default=None)
    """限制搜索的插件命名空间。如果不指定，将自动推导为调用者所在的插件；
    'global' 将跨全插件搜索。"""
    metadata_filter: dict[str, Any] | None = Field(default=None)
    """如果提供，则工具的 metadata 必须包含这里列出的所有键值对。"""

    def match(self, tool: Any) -> bool:
        """判断某个工具或工具箱是否符合当前 Query 的筛选条件"""

        def _match_pattern(val: str, pattern: str | list[str]) -> bool:
            patterns = [pattern] if isinstance(pattern, str) else pattern
            for p in patterns:
                if "*" in p or "?" in p:
                    if fnmatch.fnmatch(val, p):
                        return True
                elif val == p:
                    return True
            return False

        is_toolkit_itself = hasattr(tool, "get_tools") and not hasattr(tool, "execute")

        if self.toolkit:
            if is_toolkit_itself:
                tk_name = getattr(
                    tool, "name", getattr(tool, "__class__", type).__name__
                )
            else:
                parent_tk = getattr(tool, "parent_toolkit", None)
                tk_name = (
                    getattr(
                        parent_tk,
                        "name",
                        getattr(parent_tk, "__class__", type).__name__,
                    )
                    if parent_tk
                    else None
                )

            if not tk_name:
                return False

            if not _match_pattern(tk_name, self.toolkit):
                return False

        if is_toolkit_itself and (self.name or self.tags or self.exclude_tags):
            return True

        if self.name:
            tool_name = getattr(tool, "name", getattr(tool, "__class__", type).__name__)
            if not _match_pattern(tool_name, self.name):
                return False

        tool_tags = []
        if self.tags or self.exclude_tags:
            tool_config = getattr(tool, "config", None)
            if (
                tool_config
                and hasattr(tool_config, "shared_options")
                and tool_config.shared_options
            ):
                tool_tags = tool_config.shared_options.tags
            else:
                tool_settings = getattr(tool, "settings", None)
                tool_tags = getattr(tool_settings, "tags", []) if tool_settings else []

        if self.tags:
            if not all(tag in tool_tags for tag in self.tags):
                return False

        if self.exclude_tags:
            if any(tag in tool_tags for tag in self.exclude_tags):
                return False

        if self.metadata_filter:
            tool_settings = getattr(tool, "settings", None)
            tool_metadata = (
                getattr(tool_settings, "metadata", getattr(tool, "metadata", {}))
                if tool_settings
                else getattr(tool, "metadata", {})
            )
            for k, v in self.metadata_filter.items():
                if tool_metadata.get(k) != v:
                    return False
        return True


@dataclass
class ResolvedToolPayload:
    """解析后的工具上下文包"""

    tools: list[Any] = field(default_factory=list)
    injected_prompts: list[str] = field(default_factory=list)
    toolkits: list[Any] = field(default_factory=list)

    def merge(self, other: "ResolvedToolPayload") -> "ResolvedToolPayload":
        """将另一个解析载荷合并到当前载荷中"""
        self.tools.extend(other.tools)
        self.injected_prompts.extend(other.injected_prompts)
        self.toolkits.extend(other.toolkits)
        return self


__all__ = [
    "Query",
    "ResolvedToolPayload",
    "ToolOptions",
    "ToolOverride",
    "ToolResult",
    "ToolResultChunk",
    "ToolkitConfig",
    "ValidatedToolCall",
]
