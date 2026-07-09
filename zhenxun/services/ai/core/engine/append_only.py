from dataclasses import dataclass
import hashlib
import json
from typing import Any

from zhenxun.utils.pydantic_compat import model_dump


@dataclass
class StablePrefixSnapshot:
    """系统提示词与工具的稳定前缀快照"""

    system_prompt: list[str]
    tools: list[Any]
    fingerprint: str


class StablePrefix:
    """
    一个冻结 of 系统前缀（系统提示词 + 工具）。
    通过比对特征指纹，在内容未改变时避免重新构建。
    """

    def __init__(self):
        """初始化稳定前缀实例。"""
        self._snapshot: StablePrefixSnapshot | None = None
        self._version = 0

    @property
    def fingerprint(self) -> str:
        """获取当前快照的唯一指纹。"""
        return self._snapshot.fingerprint if self._snapshot else "<unbuilt>"

    @property
    def version(self) -> int:
        """获取当前前缀的版本号。"""
        return self._version

    @property
    def built(self) -> bool:
        """判断当前是否已构建快照。"""
        return self._snapshot is not None

    def build(self, system_prompt: list[str], tools: list[Any]) -> bool:
        """
        构建或重新构建前缀。
        返回 True 表示内容发生实质变化（缓存可能失效），False 表示使用旧快照。
        """
        snapshot = self._take_snapshot(system_prompt, tools)
        if self._snapshot and self._snapshot.fingerprint == snapshot.fingerprint:
            return False
        self._snapshot = snapshot
        self._version += 1
        return True

    def invalidate(self):
        """使当前前缀快照失效。"""
        self._snapshot = None

    def to_context(self) -> tuple[list[str], list[Any]]:
        """将前缀快照导出为上下文元组。"""
        if not self._snapshot:
            raise RuntimeError("StablePrefix.to_context() called before build()")
        return self._snapshot.system_prompt, self._snapshot.tools

    def _take_snapshot(
        self, system_prompt: list[str], tools: list[Any]
    ) -> StablePrefixSnapshot:
        """为系统提示词与工具列表生成指纹快照。"""
        parsed_tools = []
        for t in tools:
            if hasattr(t, "name"):
                parsed_tools.append((t.name, getattr(t, "description", "")))
            elif isinstance(t, dict):
                parsed_tools.append((t.get("name", ""), t.get("description", "")))
            else:
                parsed_tools.append(str(t))

        payload = {"s": system_prompt, "t": parsed_tools}
        json_str = json.dumps(payload, default=str, sort_keys=True)
        fingerprint = hashlib.md5(json_str.encode("utf-8")).hexdigest()[:8]
        return StablePrefixSnapshot(
            system_prompt=list(system_prompt),
            tools=list(tools),
            fingerprint=fingerprint,
        )


class AppendOnlyLog:
    """追加写入模式 of 对话日志管理器"""

    def __init__(self):
        """初始化追加对话日志。"""
        self._entries: list[Any] = []

    @property
    def length(self) -> int:
        """获取当前对话日志的条数。"""
        return len(self._entries)

    def append(self, message: Any):
        """向对话日志中追加单条消息。"""
        self._entries.append(message)

    def extend(self, messages: list[Any]):
        """批量追加多条消息到对话日志。"""
        self._entries.extend(messages)

    def clear(self):
        """清空所有对话日志。"""
        self._entries.clear()

    def to_messages(self) -> list[Any]:
        """返回浅拷贝 of 消息列表，防止外部意外修改"""
        return list(self._entries)


class AppendOnlyContextManager:
    """
    为大模型 Prefix Cache 深度定制 of 上下文管理器。
    将上下文拆分为绝对稳定 of Prefix (系统提示/工具) 和只增不减 of Log (对话历史)。
    """

    def __init__(self):
        """初始化上下文管理器。"""
        self.prefix = StablePrefix()
        self.log = AppendOnlyLog()
        self._last_sync_count = 0
        self._synced_digest = 0

    def build(
        self, system_prompt: list[str], tools: list[Any]
    ) -> tuple[list[str], list[Any], list[Any]]:
        """装配并获取当前 of 完整上下文元组：(系统提示词, 历史消息, 工具)"""
        self.prefix.build(system_prompt, tools)
        sys_p, ts = self.prefix.to_context()
        return sys_p, self.log.to_messages(), ts

    def sync_messages(self, normalized_messages: list[Any]):
        """
        同步消息游标。
        通过滚动摘要算法(Rolling Digest)自动检测历史消息是否被就地篡改或截断。
        如果是，则自动重置基线；否则执行极速追加写入。
        """
        if 0 < self._last_sync_count <= len(normalized_messages):
            synced_part = normalized_messages[: self._last_sync_count]
            if self._compute_digest(synced_part) != self._synced_digest:
                self.log.clear()
                self._last_sync_count = 0

        if len(normalized_messages) < self._last_sync_count:
            self.log.clear()
            self._last_sync_count = 0

        new_msgs = normalized_messages[self._last_sync_count :]
        for msg in new_msgs:
            self.log.append(msg)

        self._last_sync_count = len(normalized_messages)
        self._synced_digest = self._compute_digest(normalized_messages)

    def invalidate(self):
        """使前缀快照失效（通常在模型发生变更时调用）"""
        self.prefix.invalidate()

    def reset_sync_cursor(self):
        """强制重置对话历史游标和日志"""
        self.log.clear()
        self._last_sync_count = 0
        self._synced_digest = 0

    def _compute_digest(self, messages: list[Any]) -> int:
        """核心：计算消息列表 of 指纹，用于识别内容篡改。包含 role 与 content。"""
        from .context_renderer import ContextConverter

        payloads = []
        flattened = ContextConverter.flatten_to_llm_messages(messages)
        for msg in flattened:
            try:
                d = model_dump(msg, include={"role", "content"})
                payloads.append(d)
            except Exception:
                payloads.append(str(msg))

        def _default(obj):
            if isinstance(obj, bytes):
                return "<bytes>"
            return str(obj)

        json_str = json.dumps(payloads, default=_default, sort_keys=True)
        return int(hashlib.md5(json_str.encode("utf-8")).hexdigest()[:8], 16)
