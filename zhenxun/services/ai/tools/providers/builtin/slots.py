import asyncio
from contextlib import asynccontextmanager
import time
from typing import Any, Literal

from pydantic import Field, create_model

from zhenxun.services.ai.context.memory.manager import memory_manager
from zhenxun.services.ai.context.memory.types import (
    MemorySlot,
    SessionMetadata,
)
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.decorators import tool
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ToolOptions, ToolResult
from zhenxun.services.ai.utils.runtime import ContextUtils

_SLOT_LOCKS: dict[str, asyncio.Lock] = {}
_GLOBAL_LOCK = asyncio.Lock()


@asynccontextmanager
async def _slot_lock(session_id: str, label: str):
    key = f"{session_id}:{label}"
    async with _GLOBAL_LOCK:
        if key not in _SLOT_LOCKS:
            _SLOT_LOCKS[key] = asyncio.Lock()
        lock = _SLOT_LOCKS[key]
    async with lock:
        yield


class MemorySlotToolkit(BaseToolkit):
    """
    中期记忆槽工具箱。
    向大模型开放直接编辑上下文 XML 节点的能力。
    """

    default_prefix = ""

    class Config:
        """声明式配置：该工具箱下的所有工具默认静默"""

        shared_options = ToolOptions(silent=True)

    _INTRO_TEXT = (
        "## 📋 状态与规则面板 (Memory Slots / 中期记忆)\n"
        "该系统是你的「桌面便利贴」或「共享黑板」，用于保存你当前需要随时查阅的核心状态与全局规范。\n\n"
        "### 💡 核心机制\n"
        "- 记忆槽内容会在每次对话时**直接注入提示词中**，你无需搜索即可看见。\n"
        "- 槽位容量极其有限，仅用于维持最新的运行状态。\n\n"
        "### 📝 职责说明\n"
    )
    _READ_GUIDE = (
        "- **探索可用面板**：接手新任务时，可使用 `list_slots` "
        "宏观查看当前存在哪些面板。\n"
    )
    _WRITE_GUIDE = (
        "- **维护规范与进度**：如设定'沟通口吻'等规范（`update_slot`），"
        "或记录'待办清单'（`append_slot`）。\n"
        "- **保持精简**：内容过长时，主动将其归档到长期记忆，"
        "再重新提炼或调用 `delete_slot` 删除。\n"
    )

    default_instructions = _INTRO_TEXT + _READ_GUIDE + _WRITE_GUIDE

    @classmethod
    def read_only(cls, **kwargs) -> "MemorySlotToolkit":
        """[工厂方法] 创建一个只读模式的记忆槽工具箱。"""
        kwargs["include"] = ["list_slots", "read_slot"]
        kwargs.setdefault("instructions", cls._INTRO_TEXT + cls._READ_GUIDE)
        return cls(**kwargs)

    @classmethod
    def write_only(cls, **kwargs) -> "MemorySlotToolkit":
        """[工厂方法] 创建一个仅写入模式的记忆槽工具箱。"""
        kwargs["exclude"] = ["list_slots", "read_slot"]
        kwargs.setdefault("instructions", cls._INTRO_TEXT + cls._WRITE_GUIDE)
        return cls(**kwargs)

    def __init__(
        self,
        scopes: dict[str, Any] | None = None,
        backend: Any = None,
        namespace: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.scopes = scopes or {}
        self.backend = backend
        self._namespace = namespace

    def _get_runtime_meta_and_ctx(
        self, context: RunContext, scope_name: str | None = None
    ) -> tuple[Any, SessionMetadata]:
        ns = self._namespace or getattr(context.session, "namespace", "global")
        slot_ctx = self.backend or memory_manager.get_backend("slots", namespace=ns)

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
        return slot_ctx, session_meta

    async def get_tools(self, context: RunContext | None = None) -> dict[str, BaseTool]:
        tools = await super().get_tools(context)

        if not self.scopes:
            return tools
        scope_keys = tuple(self.scopes.keys())

        if len(scope_keys) > 1:
            ScopeType = Literal[scope_keys]

            UpdateArgs = create_model(
                "UpdateSlotArgs",
                label=(str, Field(..., description="槽位标签名称")),
                content=(str, Field(..., description="槽位内容(全量覆写)")),
                description=(str, Field(default="", description="槽位的简要说明")),
                scope=(
                    ScopeType,
                    Field(..., description="选择记忆存储的隔离分区"),
                ),
            )

            AppendArgs = create_model(
                "AppendSlotArgs",
                label=(str, Field(..., description="槽位标签名称")),
                text=(str, Field(..., description="要追加的文本内容")),
                scope=(
                    ScopeType,
                    Field(..., description="选择记忆存储的隔离分区"),
                ),
            )

            ReadArgs = create_model(
                "ReadSlotArgs",
                label=(str, Field(..., description="槽位标签名称")),
                scope=(
                    ScopeType,
                    Field(..., description="选择记忆存储的隔离分区"),
                ),
            )

            DeleteArgs = create_model(
                "DeleteSlotArgs",
                label=(str, Field(..., description="槽位标签名称")),
                scope=(
                    ScopeType,
                    Field(..., description="选择记忆存储的隔离分区"),
                ),
            )

            ListArgs = create_model(
                "ListSlotsArgs",
                scope=(
                    ScopeType,
                    Field(..., description="选择记忆存储的隔离分区"),
                ),
            )

            for t_name, t in tools.items():
                if t_name.endswith("update_slot"):
                    t.args_schema = UpdateArgs
                elif t_name.endswith("append_slot"):
                    t.args_schema = AppendArgs
                elif t_name.endswith("read_slot"):
                    t.args_schema = ReadArgs
                elif t_name.endswith("delete_slot"):
                    t.args_schema = DeleteArgs
                elif t_name.endswith("list_slots"):
                    t.args_schema = ListArgs

        return tools

    @tool(
        description="列出当前所有可用的记忆槽（包括未置顶显示的槽位），方便你了解有哪些信息可供读取或更新。"
    )
    async def list_slots(self, context: RunContext, **kwargs) -> ToolResult:
        scope_name = kwargs.get("scope")
        slot_ctx, meta = self._get_runtime_meta_and_ctx(context, scope_name)
        if not slot_ctx:
            return ToolResult(output="错误：未配置记忆槽后端").as_error()
        slots = await slot_ctx.list_all_slots(meta)
        if not slots:
            return ToolResult(output="当前没有任何记忆槽。")
        res = ["已创建的记忆槽列表："]

        show_scope = False
        if len(self.scopes) > 1:
            show_scope = True

        for s in slots:
            pin_str = "置顶" if s.pinned else "隐藏"
            if show_scope:
                semantic_name = meta.scope_name_mapping.get(s.scope, "未知")
                res.append(
                    f"- [{s.label}] (分区: "
                    f"{semantic_name}, {pin_str}) - {s.description}"
                )
            else:
                res.append(f"- [{s.label}] ({pin_str}) - {s.description}")
        return ToolResult(output="\n".join(res))

    @tool(description="读取某个尚未展示在上下文中的记忆槽完整内容。")
    async def read_slot(self, label: str, context: RunContext, **kwargs) -> ToolResult:
        scope_name = kwargs.get("scope")
        slot_ctx, meta = self._get_runtime_meta_and_ctx(context, scope_name)
        if not slot_ctx:
            return ToolResult(output="错误：未配置记忆槽后端").as_error()
        slot = await slot_ctx.get_slot(meta, label)
        if not slot:
            return ToolResult(output=f"未找到标签为 '{label}' 的槽位。").as_error()
        return ToolResult(output=f"[{label}] 内容:\n{slot.content}")

    @tool(description=("更新或新建记忆槽的内容（全量覆写）。"))
    async def update_slot(
        self,
        label: str,
        content: str,
        context: RunContext,
        description: str = "",
        **kwargs,
    ) -> ToolResult:
        scope_name = kwargs.get("scope")
        slot_ctx, meta = self._get_runtime_meta_and_ctx(context, scope_name)
        if not slot_ctx:
            return ToolResult(output="错误：未配置记忆槽后端").as_error()

        target_sid = meta.scope_prefix
        async with _slot_lock(target_sid, label):
            slot = await slot_ctx.get_slot(meta, label)
            if not slot:
                slot = MemorySlot(
                    label=label,
                    content=content,
                    scope=meta.scope_prefix,
                    description=description,
                )
            else:
                slot.content = content
                slot.scope = meta.scope_prefix
                if description:
                    slot.description = description
                slot.updated_at = time.time()

            if len(content) > slot.size_limit:
                return ToolResult(
                    output=(
                        f"错误：内容长度超过限制 ({len(content)} > {slot.size_limit})。"
                    )
                ).as_error()

            await slot_ctx.set_slot(meta, slot)
            return ToolResult(output=f"已成功将 '{label}' 更新至记忆槽中。")

    @tool(description="在指定记忆槽的末尾追加文本（例如追加待办事项清单）。")
    async def append_slot(
        self, label: str, text: str, context: RunContext, **kwargs
    ) -> ToolResult:
        scope_name = kwargs.get("scope")
        slot_ctx, meta = self._get_runtime_meta_and_ctx(context, scope_name)
        if not slot_ctx:
            return ToolResult(output="错误：未配置记忆槽后端").as_error()

        slot = await slot_ctx.get_slot(meta, label)
        if not slot:
            return ToolResult(
                output=(
                    f"错误：标签为 '{label}' 的槽位不存在，请先使用 update_slot 创建。"
                )
            ).as_error()

        target_sid = meta.scope_prefix

        async with _slot_lock(target_sid, label):
            slot = await slot_ctx.get_slot(meta, label)
            if not slot:
                return ToolResult(
                    output="错误：并发写入异常，槽位已被删除。"
                ).as_error()

            sep = "\n" if slot.content and not slot.content.endswith("\n") else ""
            new_content = f"{slot.content}{sep}{text}"

            if len(new_content) > slot.size_limit:
                return ToolResult(
                    output=(
                        "错误：追加后总长度超过限制 "
                        f"({len(new_content)} > {slot.size_limit})。"
                    )
                ).as_error()

            slot.content = new_content
            slot.updated_at = time.time()
            await slot_ctx.set_slot(meta, slot)

            return ToolResult(output=f"已成功追加至 '{label}'。")

    @tool(description="删除不再需要的记忆槽（全量删除）。")
    async def delete_slot(
        self, label: str, context: RunContext, **kwargs
    ) -> ToolResult:
        scope_name = kwargs.get("scope")
        slot_ctx, meta = self._get_runtime_meta_and_ctx(context, scope_name)
        if not slot_ctx:
            return ToolResult(output="错误：未配置记忆槽后端").as_error()

        target_sid = meta.scope_prefix
        async with _slot_lock(target_sid, label):
            await slot_ctx.delete_slot(meta, label, meta.scope_prefix)

        return ToolResult(output=f"已成功删除槽位 '{label}'。")
