from typing import Any, Literal, Optional

from pydantic import Field, create_model

from zhenxun.services.ai.context.memory.manager import memory_manager
from zhenxun.services.ai.context.memory.models import MemoryConfig
from zhenxun.services.ai.context.memory.types import SessionMetadata
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.decorators import tool
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ToolOptions, ToolResult
from zhenxun.services.ai.utils.logger import log_tool as logger


class MemoryManagementToolkit(BaseToolkit):
    """
    主动记忆管理工具箱 (Agentic Memory Toolkit)。
    赋予大模型自主存取、修改和删除用户长期设定的能力。
    """

    default_prefix = ""

    class Config:
        """声明式配置：该工具箱下的所有工具默认静默"""

        shared_options = ToolOptions(silent=True)

    default_instructions = """\
## 🧠 长期记忆管理系统 (Long-Term Memory)
该系统是你的「无限档案馆」。⚠️ **注意：系统默认不会主动向你提供所有历史信息，你必须通过主动搜索来回忆。**

### 📝 何时使用长期记忆？
- **记录离散事实与经验**：当需要记录某个独立事件、历史经验、问题解决方案或具体事实时（使用 `save_memory`）。
- **寻找历史线索**：当遇到未知情况，或用户提及过去的事情、特定设定时，必须主动检索历史库（使用 `search_memory`）。

### ⚙️ 操作规范
1. **隐式记录**：当接收到值得记忆的重要信息时，请静默记录。除非用户主动提问，否则无需向用户显式汇报"我已记住"。
2. **按需更新**：如果发现某项历史记录已过时或状态发生扭转，请先检索出它的 ID，再进行修改（使用 `update_memory`）或废弃（使用 `delete_memory`）。
3. **精准提炼**：保存记忆时请提炼核心价值，避免保存无意义的闲聊。\
"""  # noqa: E501

    def __init__(
        self,
        memory_config: MemoryConfig | None = None,
        namespace: str | None = None,
        **kwargs: Any,
    ):
        """
        初始化主动记忆管理工具箱。

        参数：
            memory_config: 记忆系统的全局配置对象，为空则使用全局默认。
            namespace: 当前隔离环境的命名空间。
            kwargs: 其他透传给 BaseToolkit 的参数。
        """
        super().__init__(**kwargs)
        self.memory_config = memory_config
        self._namespace = namespace

    def _get_runtime_meta_and_scope(
        self, context: RunContext, scope_name: str | None = None
    ) -> tuple[Any, SessionMetadata]:
        """动态获取当前运行时的数据库实例与会话元信息，实现无状态化"""
        ns = self._namespace or getattr(context.session, "namespace", "global")
        scope = memory_manager.get_long_term_memory(self.memory_config, namespace=ns)

        scope_builder = None
        if (
            self.memory_config
            and self.memory_config.long_term
            and self.memory_config.long_term.scopes
        ):
            scopes_dict = self.memory_config.long_term.scopes
            if not scope_name:
                scope_builder = next(iter(scopes_dict.values()))
            else:
                scope_builder = scopes_dict.get(scope_name)

        if not scope_builder:
            scope_builder = getattr(self.memory_config, "base_isolation", None)
            if not scope_builder:
                from zhenxun.services.ai.context.memory.types import Isolation

                scope_builder = Isolation.AGENT_USER()

        selector = scope_builder.resolve(
            deps=context.deps,
            prefix="",
            default_namespace=ns,
            default_agent=context.run.agent_name,
        )
        parts = selector.get_scope_parts()
        all_scopes = {"/"}
        current_path = ""
        for part in parts:
            current_path += f"/{part}"
            all_scopes.add(current_path)
        accessible_scopes = list(all_scopes)
        accessible_scopes.sort(key=lambda x: len(x.split("/")))

        scope_name_mapping = {}
        if (
            self.memory_config
            and self.memory_config.long_term
            and self.memory_config.long_term.scopes
        ):
            for name, builder in self.memory_config.long_term.scopes.items():
                sel = builder.resolve(
                    deps=context.deps,
                    prefix="",
                    default_namespace=ns,
                    default_agent=context.run.agent_name,
                )
                scope_name_mapping[sel.scope_prefix] = name

        session_meta = SessionMetadata(
            session_id=context.session_id or "default_session",
            selector=selector,
            scope_prefix=selector.scope_prefix,
            accessible_scopes=accessible_scopes,
            scope_name_mapping=scope_name_mapping,
        )
        return scope, session_meta

    async def get_tools(self, context: RunContext | None = None) -> dict[str, BaseTool]:
        tools = await super().get_tools(context)
        if not self.memory_config or not self.memory_config.long_term.enable:
            return tools

        scopes_dict = self.memory_config.long_term.scopes
        if not scopes_dict:
            return tools

        scope_keys = tuple(scopes_dict.keys())

        if len(scope_keys) > 1:
            ScopeType = Literal[scope_keys]

            SaveArgs = create_model(
                "SaveMemoryArgs",
                content=(str, Field(..., description="要保存的记忆内容")),
                importance=(float, Field(default=0.5, description="重要性(0-1)")),
                scope=(
                    ScopeType,
                    Field(..., description="选择记忆存储的隔离分区"),
                ),
            )

            SearchArgs = create_model(
                "SearchMemoryArgs",
                query=(str, Field(..., description="搜索关键词或问题")),
                filters=(
                    str | None,
                    Field(default=None, description="可选的元数据过滤JSON字符串"),
                ),
                scope=(
                    Optional[ScopeType],  # noqa
                    Field(
                        default=None,
                        description="搜索特定的分区。留空则跨所有有权分区混合检索！",
                    ),
                ),
            )

            UpdateArgs = create_model(
                "UpdateMemoryArgs",
                record_id=(str, Field(..., description="要更新的记忆ID")),
                new_content=(str, Field(..., description="新的记忆内容")),
                importance=(float, Field(default=0.5, description="重要性(0-1)")),
                scope=(
                    ScopeType,
                    Field(..., description="指定该记忆所在的隔离分区"),
                ),
            )

            DeleteArgs = create_model(
                "DeleteMemoryArgs",
                record_id=(str, Field(..., description="要删除的记忆ID")),
                scope=(
                    ScopeType,
                    Field(..., description="指定该记忆所在的隔离分区"),
                ),
            )

            for t_name, t in tools.items():
                if t_name.endswith("save_memory"):
                    t.args_schema = SaveArgs
                elif t_name.endswith("search_memory"):
                    t.args_schema = SearchArgs
                elif t_name.endswith("update_memory"):
                    t.args_schema = UpdateArgs
                elif t_name.endswith("delete_memory"):
                    t.args_schema = DeleteArgs

        return tools

    @tool(
        name="save_memory",
        description="保存关于用户的重要设定、偏好或事实到长期记忆中。",
    )
    async def save_memory(
        self, content: str, context: RunContext, importance: float = 0.5, **kwargs
    ) -> ToolResult:
        scope_name = kwargs.get("scope")
        scope, meta = self._get_runtime_meta_and_scope(context, scope_name)
        if not scope:
            return ToolResult(output="错误：未启用或未配置长期记忆后端。").as_error()

        await scope.remember(session=meta, content=content, importance=importance)

        logger.debug(
            f"[Agentic Memory] AI 主动存入记忆: {content} (重要性: {importance})"
        )
        return ToolResult(output="记忆已成功排入系统后台合并存入。")

    @tool(
        name="search_memory",
        description=(
            "在长期记忆中主动检索关于用户的设定和事实。"
            "支持自然语言模糊搜索。"
            "可选的 filters 参数用于元数据精确匹配。如果不需要过滤，"
            "请直接省略该参数，不要传入空字典或空字符串。"
        ),
    )
    async def search_memory(
        self,
        query: str,
        context: RunContext,
        **kwargs,
    ) -> ToolResult:
        scope_name = kwargs.get("scope")
        scope, meta = self._get_runtime_meta_and_scope(context, scope_name)
        if not scope:
            return ToolResult(output="错误：未启用或未配置长期记忆后端。").as_error()

        filters = kwargs.get("filters")
        parsed_filters = None
        if isinstance(filters, dict):
            parsed_filters = filters
        elif isinstance(filters, str) and filters.strip() and filters.strip() != "{}":
            import json

            try:
                parsed_filters = json.loads(filters)
            except Exception:
                pass

        if scope_name is not None:
            meta.accessible_scopes = [meta.scope_prefix]

        matches = await scope.recall(
            session=meta,
            query=query,
            limit=5,
            metadata_filter=parsed_filters,
        )
        if not matches:
            return ToolResult(
                output="未检索到相关记忆。这可能是你们关于此话题的首次探讨，请直接根据常识回答或向用户确认。"
            )

        results = [
            (
                f"ID: {m.record.id} | 内容: {m.record.content} | "
                f"重要性: {m.record.metadata.get('importance', 0.5)}"
            )
            for m in matches
        ]
        return ToolResult(output="检索到的记忆如下：\n" + "\n".join(results))

    @tool(
        name="update_memory",
        description="更新指定ID的长期记忆内容。当你发现用户的某个旧设定发生改变时，请使用此工具覆盖旧记忆。",
    )
    async def update_memory(
        self,
        record_id: str,
        new_content: str,
        context: RunContext,
        importance: float = 0.5,
        **kwargs,
    ) -> ToolResult:
        scope_name = kwargs.get("scope")
        scope, meta = self._get_runtime_meta_and_scope(context, scope_name)
        if not scope:
            return ToolResult(output="错误：未启用或未配置长期记忆后端。").as_error()

        success = await scope.update(
            session=meta,
            record_id=record_id,
            new_content=new_content,
            importance=importance,
        )
        if success:
            logger.debug(
                f"[Agentic Memory] AI 主动更新记忆: {record_id} -> {new_content}"
            )
            return ToolResult(
                output=f"记忆 {record_id} 更新成功。新内容：{new_content}"
            )
        return ToolResult(
            output=f"更新失败：未找到ID为 {record_id} 的记忆。"
        ).as_error()

    @tool(
        name="delete_memory",
        description="根据记忆的唯一ID删除已经作废或过期的用户记忆。",
    )
    async def delete_memory(
        self, record_id: str, context: RunContext, **kwargs
    ) -> ToolResult:
        scope_name = kwargs.get("scope")
        scope, meta = self._get_runtime_meta_and_scope(context, scope_name)
        if not scope:
            return ToolResult(output="错误：未启用或未配置长期记忆后端。").as_error()

        deleted_count = await scope.forget(session=meta, record_ids=[record_id])
        if deleted_count > 0:
            logger.info(f"[Agentic Memory] AI 主动删除记忆: {record_id}")
            return ToolResult(output=f"记忆 {record_id} 删除成功。")
        return ToolResult(
            output=f"删除失败：未找到ID为 {record_id} 的记忆。"
        ).as_error()
