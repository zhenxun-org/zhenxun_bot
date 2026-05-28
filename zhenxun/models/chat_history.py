from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal
from typing_extensions import Self

from tortoise import fields
from tortoise.expressions import Q

from zhenxun.services.db_context import Model
from zhenxun.services.db_context.schema_ops import AlterColumnType, RenameColumn


class ChatHistory(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255)
    """用户id"""
    group_id = fields.CharField(255, null=True)
    """群聊id"""
    text = fields.TextField(null=True)
    """文本内容"""
    plain_text = fields.TextField(null=True)
    """纯文本"""
    create_time = fields.DatetimeField(auto_now_add=True)
    """创建时间"""
    bot_id = fields.CharField(255, null=True)
    """bot记录id"""
    platform = fields.CharField(255, null=True)
    """平台"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "chat_history"
        table_description = "聊天记录数据表"
        indexes: ClassVar = [
            ("user_id", "create_time"),
            ("group_id", "create_time"),
            ("user_id", "group_id"),
        ]

    @classmethod
    def _platform_from_scope(cls, platform_scope: str | None) -> str | None:
        """Map the new fine-grained scope back to the legacy platform column."""
        if not platform_scope:
            return None
        scope = str(platform_scope).lower()
        if scope in {"qq", "qq_client", "qq_api"} or scope.startswith("qq_"):
            return "qq"
        if "onebot" in scope:
            return "qq"
        return scope

    @classmethod
    def scoped_query(cls, platform_scope: str | None = None, **filters: Any):
        """Return a chat-history query compatible with platform_scope callers.

        chat_history currently stores the coarse legacy ``platform`` column rather
        than a dedicated ``platform_scope`` column, so this method intentionally
        stays as a thin compatibility shim.
        """
        query = cls.filter(**filters)
        if not platform_scope:
            return query
        if "platform" in filters or any(k.startswith("platform__") for k in filters):
            return query

        platform = cls._platform_from_scope(platform_scope)
        if not platform:
            return query
        if platform == "qq" and str(platform_scope).lower() in {"qq", "qq_client"}:
            return query.filter(
                Q(platform=platform) | Q(platform__isnull=True) | Q(platform="")
            )
        return query.filter(platform=platform)

    @classmethod
    async def get_group_msg_rank(
        cls,
        gid: str | None,
        limit: int = 10,
        order: str = "DESC",
        date_scope: tuple[datetime, datetime] | None = None,
    ) -> list[tuple[str, int]]:
        """获取排行数据

        参数:
            gid: 群号
            limit: 获取数量
            order: 排序类型，desc，des
            date_scope: 日期范围
        """
        from zhenxun.services.hot_query_cache import get_chat_history_rank_cached

        return await get_chat_history_rank_cached(cls, gid, limit, order, date_scope)

    @classmethod
    async def get_group_first_msg_datetime(
        cls, group_id: str | None
    ) -> datetime | None:
        """获取群第一条记录消息时间

        参数:
            group_id: 群组id
        """
        from zhenxun.services.hot_query_cache import (
            get_chat_history_first_msg_datetime_cached,
        )

        return await get_chat_history_first_msg_datetime_cached(cls, group_id)

    @classmethod
    async def get_message(
        cls,
        uid: str | None,
        gid: str | None,
        type_: Literal["user", "group"],
        msg_type: Literal["private", "group"] | None = None,
        days: int | tuple[datetime, datetime] | None = None,
        platform_scope: str | None = None,
    ) -> list[Self]:
        """获取消息查询query

        参数:
            uid: 用户id
            gid: 群聊id
            type_: 类型，私聊或群聊
            msg_type: 消息类型，用户或群聊
            days: 限制日期
            platform_scope: 兼容细粒度平台作用域
        """
        if type_ == "user":
            query = cls.scoped_query(platform_scope=platform_scope, user_id=uid)
            if msg_type == "private":
                query = query.filter(group_id__isnull=True)
            elif msg_type == "group":
                query = query.filter(group_id__not_isnull=True)
        else:
            query = cls.scoped_query(platform_scope=platform_scope, group_id=gid)
            if uid:
                query = query.filter(user_id=uid)
        if days:
            if isinstance(days, int):
                query = query.filter(
                    create_time__gte=datetime.now() - timedelta(days=days)
                )
            elif isinstance(days, tuple):
                query = query.filter(create_time__range=days)
        return await query.all()  # type: ignore

    @classmethod
    async def _run_script(cls):
        return [
            # 允许 group_id 为空
            "alter table chat_history alter group_id drop not null;",
            # 允许 text 为空
            "alter table chat_history alter text drop not null;",
            # 允许 plain_text 为空
            "alter table chat_history alter plain_text drop not null;",
            # 将user_id改为user_id
            RenameColumn("chat_history", "user_qq", "user_id"),
            AlterColumnType(
                "chat_history",
                "user_id",
                {"postgres": "character varying(255)", "mysql": "VARCHAR(255)"},
            ),
            AlterColumnType(
                "chat_history",
                "group_id",
                {"postgres": "character varying(255)", "mysql": "VARCHAR(255)"},
            ),
        ]
