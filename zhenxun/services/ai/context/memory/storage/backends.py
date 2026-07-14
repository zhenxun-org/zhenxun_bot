from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import datetime
from pathlib import Path
import time
from typing import Any, cast

from nonebot.utils import is_coroutine_callable
from tortoise import fields
from tortoise.timezone import now

from zhenxun.services.ai.context.memory.types import (
    MemorySlot,
    SessionMetadata,
)
from zhenxun.services.ai.context.rag.engine import ScopedRAGClient
from zhenxun.services.ai.context.rag.models import BaseRecord, SearchResult
from zhenxun.services.ai.core.messages import (
    AssistantMessage,
    LLMContentPart,
    LLMMessage,
    SystemMessage,
    TextPart,
    ToolMessage,
    UserMessage,
)
from zhenxun.services.ai.utils.scope import ScopeSelector
from zhenxun.services.db_context import Model
from zhenxun.utils.pydantic_compat import TypeAdapter, model_dump

from .interfaces import (
    BaseChatContext,
    BaseSlotContext,
)


class AbstractMemoryRecord(Model):
    """Tortoise ORM 短期记忆持久化基类 (Mixin)。"""

    id = fields.UUIDField(pk=True, description="主键")
    session_id = fields.CharField(max_length=255, index=True)
    role = fields.CharField(max_length=32)
    content = fields.JSONField()
    api_context = fields.JSONField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    metadata = fields.JSONField(null=True)

    class Meta:  # type: ignore
        abstract = True


class AbstractSlotRecord(Model):
    """Tortoise ORM 记忆槽持久化基类 (Mixin)。"""

    id = fields.CharField(
        pk=True, max_length=128, description="复合主键: session_id + label"
    )
    session_id = fields.CharField(max_length=255, index=True)
    label = fields.CharField(max_length=64, index=True)
    content = fields.TextField()
    size_limit = fields.IntField(default=2000)
    pinned = fields.BooleanField(default=True)
    scope = fields.CharField(max_length=255)
    description = fields.CharField(max_length=255, default="")
    created_at = fields.FloatField()
    updated_at = fields.FloatField()

    class Meta:  # type: ignore
        abstract = True


class DBMessageSerializer:
    """将 LLMMessage 与数据库 JSON 格式进行序列化/反序列化的帮助类"""

    @staticmethod
    def deserialize_content(content_raw: Any) -> list[LLMContentPart]:
        """反序列化数据库中的 JSON 数据为 LLMMessage 消息内容部件列表"""
        content_parts: list[LLMContentPart] = []
        if isinstance(content_raw, list):
            adapter = TypeAdapter(LLMContentPart)
            for p in content_raw:
                if isinstance(p, dict):
                    for k in list(p.keys()):
                        if k.startswith("_is_b64_"):
                            orig_k = k[8:]
                            if orig_k in p and isinstance(p[orig_k], str):
                                p[orig_k] = base64.b64decode(p[orig_k])
                            p.pop(k, None)
                    content_parts.append(adapter.validate_python(p))
        elif isinstance(content_raw, str):
            content_parts.append(TextPart(text=content_raw))
        return content_parts

    @staticmethod
    def serialize_content(
        content_payload: list[LLMContentPart] | str,
    ) -> list[dict[str, Any]]:
        """将 LLMMessage 消息内容序列化为可存储于数据库的 JSON 格式"""
        if isinstance(content_payload, str):
            return [{"type": "text", "text": content_payload}]
        elif isinstance(content_payload, list):
            processed_content = []
            for p in content_payload:
                p_dump = (
                    model_dump(p, exclude_none=True)
                    if hasattr(p, "model_dump")
                    else (p.copy() if isinstance(p, dict) else p)
                )
                if isinstance(p_dump, dict):
                    for k, v in list(p_dump.items()):
                        if isinstance(v, bytes):
                            p_dump[k] = base64.b64encode(v).decode("utf-8")
                            p_dump[f"_is_b64_{k}"] = True
                        elif isinstance(v, Path):
                            p_dump[k] = str(v)
                processed_content.append(p_dump)
            return (
                processed_content
                if processed_content
                else [
                    {"type": "text", "text": "[仅包含思维链或工具调度，无实质文本输出]"}
                ]
            )
        return []


class MemoryScope:
    """长期记忆的作用域视图与 RAG 管线。"""

    def __init__(
        self,
        rag_client: ScopedRAGClient,
    ):
        """初始化长期记忆作用域与 RAG 客户端"""
        self.rag_client = rag_client
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def remember(
        self,
        session: SessionMetadata,
        content: str,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """通过 RAG Ingestion Pipeline 完成记忆落盘"""
        meta = metadata.copy() if metadata else {}
        meta.update(
            {
                "scope": session.scope_prefix,
                "importance": importance,
                "created_at": time.time(),
            }
        )
        record = BaseRecord(content=content, metadata=meta)

        await self.rag_client.ingest([record])

    async def recall(
        self,
        session: SessionMetadata,
        query: str,
        limit: int = 10,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """委托至 Retriever 检索与重排，并触发读时惰性强化"""
        matches = await self.rag_client.search(
            query=query,
            limit=limit,
            scopes=session.accessible_scopes,
            metadata_filters=metadata_filter,
        )
        if matches:
            task = asyncio.create_task(
                self._reinforce_memories([m.record for m in matches])
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        return matches

    async def update(
        self,
        session: SessionMetadata,
        record_id: str,
        new_content: str,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """原子更新：通过先删后插，确保底层向量(Embedding)能根据新文本被正确刷新"""
        deleted_count = await self.forget(session, record_ids=[record_id])
        if deleted_count > 0:
            await self.remember(
                session=session,
                content=new_content,
                importance=importance,
                metadata=metadata,
            )
            return True
        return False

    async def forget(
        self, session: SessionMetadata, record_ids: list[str] | None = None
    ) -> int:
        """从 RAG 向量数据库中删除指定的记忆记录"""
        return await self.rag_client.delete(
            record_ids=record_ids,
        )

    async def _reinforce_memories(self, records: list[BaseRecord]):
        """惰性强化记忆：更新被检索记忆的访问次数和最后访问时间"""
        now = time.time()
        for r in records:
            r.metadata["access_count"] = r.metadata.get("access_count", 0) + 1
            r.metadata["last_accessed_at"] = now
            await self.rag_client.storage.update(r)


class InMemoryChatContext(BaseChatContext):
    """基于内存的聊天上下文存储后端"""

    def __init__(self):
        """初始化内存聊天上下文"""
        self._messages: dict[str, list[LLMMessage]] = {}

    async def get_messages(self, session: SessionMetadata) -> list[LLMMessage]:
        """获取指定会话的所有短期历史消息"""
        return list(self._messages.get(session.session_id, []))

    async def search(
        self, query: str, session: SessionMetadata, limit: int = 10
    ) -> list[LLMMessage]:
        """在内存中简单检索包含查询词的历史消息"""
        results = []
        for msg in self._messages.get(session.session_id, []):
            if query in msg.extract_text:
                results.append(msg)
            if len(results) >= limit:
                break
        return results

    async def add_messages(
        self, session: SessionMetadata, messages: list[LLMMessage]
    ) -> None:
        """向指定会话中追加历史消息"""
        if session.session_id not in self._messages:
            self._messages[session.session_id] = []
        self._messages[session.session_id].extend(messages)

    async def set_messages(
        self, session: SessionMetadata, messages: list[LLMMessage]
    ) -> None:
        """覆盖设置指定会话的历史消息"""
        self._messages[session.session_id] = list(messages)

    async def clear(self, session: SessionMetadata) -> None:
        """清空指定会话的全部历史消息"""
        self._messages.pop(session.session_id, None)

    async def clear_by_query(self, query: ScopeSelector) -> None:
        """内存级：前缀匹配清理所有符合要求的短期会话"""
        scope_prefix = query.scope_prefix
        keys_to_delete = [
            sid for sid in self._messages.keys() if sid.startswith(scope_prefix)
        ]
        for sid in keys_to_delete:
            self._messages.pop(sid, None)


class TortoiseChatContext(BaseChatContext):
    """基于 Tortoise ORM 的聊天上下文存储后端"""

    def __init__(
        self,
        model_class: type[AbstractMemoryRecord],
        custom_save_hook: Callable[
            [AbstractMemoryRecord, LLMMessage, SessionMetadata], Any
        ]
        | None = None,
    ):
        """初始化 Tortoise ORM 聊天上下文存储后端"""
        self.model_class = model_class
        self.custom_save_hook = custom_save_hook

    def _row_to_message(self, row: AbstractMemoryRecord) -> LLMMessage:
        """将数据库记录转换为 LLMMessage 实例"""
        content_parts = DBMessageSerializer.deserialize_content(row.content)
        metadata: dict[str, Any] | None = (
            row.metadata if isinstance(row.metadata, dict) else None
        )
        kwargs = {
            "content": content_parts,
            "metadata": metadata,
            "created_at": row.created_at.timestamp() if row.created_at else time.time(),
        }
        role = row.role
        if role == "system":
            return cast(LLMMessage, SystemMessage(**kwargs))
        elif role == "user":
            return cast(LLMMessage, UserMessage(**kwargs))
        elif role == "assistant":
            return cast(LLMMessage, AssistantMessage(**kwargs))
        elif role == "tool":
            return cast(LLMMessage, ToolMessage(**kwargs))
        return cast(LLMMessage, LLMMessage(role=role, **kwargs))

    async def get_messages(self, session: SessionMetadata) -> list[LLMMessage]:
        """从数据库中查询并获取指定会话的短期历史消息"""
        rows = (
            await self.model_class.filter(session_id=session.session_id)
            .order_by("created_at")
            .all()
        )
        return [self._row_to_message(row) for row in rows]

    async def search(
        self, query: str, session: SessionMetadata, limit: int = 10
    ) -> list[LLMMessage]:
        """在数据库中检索包含查询词的历史消息"""
        rows = (
            await self.model_class.filter(
                session_id=session.session_id, content__icontains=query
            )
            .order_by("-created_at")
            .limit(limit)
            .all()
        )
        return [self._row_to_message(row) for row in reversed(rows)]

    async def add_messages(
        self, session: SessionMetadata, messages: list[LLMMessage]
    ) -> None:
        """向数据库中批量追加指定会话的历史消息"""
        if not messages:
            return

        base_time = now()

        last_msg = (
            await self.model_class.filter(session_id=session.session_id)
            .order_by("-created_at")
            .first()
        )
        if last_msg and last_msg.created_at and last_msg.created_at >= base_time:
            base_time = last_msg.created_at + datetime.timedelta(milliseconds=10)

        orm_objects = []
        for i, msg in enumerate(messages):
            content_payload = DBMessageSerializer.serialize_content(msg.content)

            msg_time = base_time + datetime.timedelta(milliseconds=i * 10)
            orm_obj = self.model_class(
                session_id=session.session_id,
                role=msg.role,
                content=content_payload,
                api_context=None,
                metadata=msg.metadata,
                created_at=msg_time,
            )
            if self.custom_save_hook:
                if is_coroutine_callable(self.custom_save_hook):
                    await self.custom_save_hook(orm_obj, msg, session)
                else:
                    self.custom_save_hook(orm_obj, msg, session)
            orm_objects.append(orm_obj)
        if orm_objects:
            await self.model_class.bulk_create(orm_objects)

    async def set_messages(
        self, session: SessionMetadata, messages: list[LLMMessage]
    ) -> None:
        """覆盖设置指定会话的数据库历史消息"""
        await self.clear(session)
        await self.add_messages(session, messages)

    async def clear(self, session: SessionMetadata) -> None:
        """删除指定会话在数据库中的全部历史消息"""
        await self.model_class.filter(session_id=session.session_id).delete()

    async def clear_by_query(self, query: ScopeSelector) -> None:
        """ORM 级：利用数据库 startswith 原生语法批量级联删除短期记忆"""
        scope_prefix = query.scope_prefix
        await self.model_class.filter(session_id__startswith=scope_prefix).delete()


def get_orm_chat_context(
    model_class: type[AbstractMemoryRecord],
    custom_save_hook: Callable[[AbstractMemoryRecord, LLMMessage, SessionMetadata], Any]
    | None = None,
) -> TortoiseChatContext:
    """
    [工厂方法] 供第三方开发者调用，
    将 Tortoise ORM 表直接包装为对话历史记录系统。
    """
    return TortoiseChatContext(
        model_class=model_class, custom_save_hook=custom_save_hook
    )


class TortoiseSlotContext(BaseSlotContext):
    """基于 Tortoise ORM 的记忆槽存储后端"""

    def __init__(self, model_class: type[AbstractSlotRecord]):
        """初始化 Tortoise ORM 记忆槽存储后端"""
        self.model_class = model_class

    def _row_to_slot(self, row: AbstractSlotRecord) -> MemorySlot:
        """将数据库记忆槽记录转换为 MemorySlot 实例"""
        return MemorySlot(
            label=row.label,
            content=row.content,
            size_limit=row.size_limit,
            pinned=row.pinned,
            scope=row.scope,
            description=row.description,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_slot(self, session: SessionMetadata, label: str) -> MemorySlot | None:
        """查询并获取指定会话及作用域下 label 对应的记忆槽"""
        rows = await self.model_class.filter(
            session_id__in=session.accessible_scopes, label=label
        ).all()

        row_map = {r.session_id: r for r in rows}
        for scope in reversed(session.accessible_scopes):
            if scope in row_map:
                return self._row_to_slot(row_map[scope])
        return None

    async def set_slot(self, session: SessionMetadata, slot: MemorySlot) -> None:
        """保存或更新指定会话的记忆槽到数据库"""
        composite_id = f"{slot.scope}_{slot.label}"

        await self.model_class.update_or_create(
            id=composite_id,
            defaults={
                "session_id": slot.scope,
                "label": slot.label,
                "content": slot.content,
                "size_limit": slot.size_limit,
                "pinned": slot.pinned,
                "scope": slot.scope,
                "description": slot.description,
                "created_at": slot.created_at,
                "updated_at": slot.updated_at,
            },
        )

    async def delete_slot(
        self, session: SessionMetadata, label: str, scope: str
    ) -> None:
        """从数据库中删除指定作用域和 label 的记忆槽"""
        composite_id = f"{scope}_{label}"
        await self.model_class.filter(id=composite_id).delete()

    async def list_pinned_slots(self, session: SessionMetadata) -> list[MemorySlot]:
        """获取指定会话所有可访问的、置顶且非空的记忆槽"""
        rows = await self.model_class.filter(
            session_id__in=session.accessible_scopes, pinned=True
        ).all()

        merged = {}
        for scope in session.accessible_scopes:
            for row in rows:
                if row.session_id == scope:
                    merged[row.label] = self._row_to_slot(row)

        return [s for s in merged.values() if s.content.strip()]

    async def list_all_slots(self, session: SessionMetadata) -> list[MemorySlot]:
        """获取指定会话所有可访问的记忆槽列表"""
        rows = await self.model_class.filter(
            session_id__in=session.accessible_scopes
        ).all()

        merged = {}
        for scope in session.accessible_scopes:
            for row in rows:
                if row.session_id == scope:
                    merged[row.label] = self._row_to_slot(row)
        return list(merged.values())

    async def clear_by_query(self, query: ScopeSelector) -> None:
        """批量清理匹配指定前缀的所有记忆槽"""
        scope_prefix = query.scope_prefix
        await self.model_class.filter(session_id__startswith=scope_prefix).delete()


def get_orm_slot_context(model_class: type[AbstractSlotRecord]) -> TortoiseSlotContext:
    """
    [工厂方法] 供第三方开发者调用，将 Tortoise ORM 表直接包装为记忆槽存储系统。
    """
    return TortoiseSlotContext(model_class=model_class)


__all__ = [
    "AbstractMemoryRecord",
    "AbstractSlotRecord",
    "InMemoryChatContext",
    "MemoryScope",
    "TortoiseChatContext",
    "TortoiseSlotContext",
]
