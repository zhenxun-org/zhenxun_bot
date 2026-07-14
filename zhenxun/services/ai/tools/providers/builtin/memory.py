from typing import Any, Literal, Optional

from pydantic import Field, create_model

from zhenxun.services.ai.context.memory.storage.backends import MemoryScope
from zhenxun.services.ai.context.memory.types import SessionMetadata
from zhenxun.services.ai.context.rag.engine import ScopedRAGClient
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.decorators import tool
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ToolOptions, ToolResult
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.services.ai.utils.runtime import ContextUtils
from zhenxun.services.ai.utils.scope import ScopeBuilder


class MemoryManagementToolkit(BaseToolkit):
    """
    主动记忆管理工具箱 (Agentic Memory Toolkit)。
    赋予大模型自主存取、修改和删除用户长期设定的能力。
    """

    default_prefix = ""

    class Config:
        """声明式配置：该工具箱下的所有工具默认静默"""

        shared_options = ToolOptions(silent=True)

    _INTRO_TEXT = (
        "## 🧠 长期记忆管理系统 (Long-Term Memory)\n"
        "该系统是你的「无限档案馆」。系统默认不会主动向你提供所有历史信息，你必须通过主动搜索来回忆。\n\n"
        "### 📝 职责说明\n"
    )
    _READ_GUIDE = (
        "- **寻找历史线索**：当遇到未知情况，或用户提及过去的事情、"
        "特定设定时，必须主动检索历史库（使用 `search_memory`）。\n"
    )
    _WRITE_GUIDE = (
        "- **记录离散事实与经验**：当需要记录某个独立事件、历史经验、"
        "问题解决方案或具体事实时（使用 `save_memory`）。\n"
        "- **隐式记录**：当接收到值得记忆的重要信息时，请静默记录。"
        "除非用户主动提问，否则无需向用户显式汇报'我已记住'。\n"
        "- **按需更新**：如果发现某项历史记录已过时或状态发生扭转，"
        "请先检索出它的 ID，再修改（`update_memory`）或废弃（`delete_memory`）。\n"
        "- **精准提炼**：保存记忆时请提炼核心价值，避免保存无意义的闲聊。\n"
    )

    default_instructions = _INTRO_TEXT + _READ_GUIDE + _WRITE_GUIDE

    @classmethod
    def read_only(cls, **kwargs) -> "MemoryManagementToolkit":
        """[工厂方法] 创建一个只读模式的长期记忆工具箱。"""
        kwargs["include"] = ["search_memory"]
        kwargs.setdefault("instructions", cls._INTRO_TEXT + cls._READ_GUIDE)
        return cls(**kwargs)

    @classmethod
    def write_only(cls, **kwargs) -> "MemoryManagementToolkit":
        """[工厂方法] 创建一个仅写入模式的长期记忆工具箱。"""
        kwargs["exclude"] = ["search_memory"]
        kwargs.setdefault("instructions", cls._INTRO_TEXT + cls._WRITE_GUIDE)
        return cls(**kwargs)

    def __init__(
        self,
        rag_client: ScopedRAGClient | None = None,
        scopes: dict[str, ScopeBuilder] | None = None,
        namespace: str | None = None,
        **kwargs: Any,
    ):
        """
        初始化主动记忆管理工具箱。

        参数：
            rag_client: 底层 RAG 检索引擎客户端实例。
            scopes: 作用域构建器映射字典，用于动态限定存储的分区。
            namespace: 当前隔离环境的命名空间。
            kwargs: 其他透传给 BaseToolkit 的参数。
        """
        super().__init__(**kwargs)
        self.rag_client = rag_client
        from zhenxun.services.ai.context.memory.types import Isolation

        self.scopes = scopes or {"私有": Isolation.AGENT_USER()}
        self._namespace = namespace

    def _get_runtime_meta_and_scope(
        self, context: RunContext, scope_name: str | None = None
    ) -> tuple[Any, SessionMetadata]:
        """动态获取当前运行时的数据库实例与会话元信息，实现无状态化"""
        scope = MemoryScope(rag_client=self.rag_client) if self.rag_client else None

        scope_builder = (
            self.scopes.get(scope_name)
            if scope_name
            else next(iter(self.scopes.values()), None)
        )
        session_meta = ContextUtils.build_session_meta(
            context=context,
            target_builder=scope_builder,
            extra_scopes=self.scopes,
            custom_namespace=self._namespace,
        )
        return scope, session_meta

    async def get_tools(self, context: RunContext | None = None) -> dict[str, BaseTool]:
        tools = await super().get_tools(context)
        if not getattr(self, "rag_client", None):
            return tools

        scopes_dict = getattr(self, "scopes", {})
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
