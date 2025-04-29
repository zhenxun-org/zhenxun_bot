from typing_extensions import Self

from nonebot.adapters import Bot
from tortoise import fields

from zhenxun.configs.config import BotConfig
from zhenxun.models.group_console import GroupConsole
from zhenxun.services.db_context import Model
from zhenxun.utils.common_utils import SqlUtils
from zhenxun.utils.enum import RequestHandleType, RequestType
from zhenxun.utils.exception import NotFoundError


class FgRequest(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    request_type = fields.CharEnumField(
        RequestType, default=None, description="请求类型"
    )
    """请求类型"""
    platform = fields.CharField(255, description="平台")
    """平台"""
    bot_id = fields.CharField(255, description="Bot Id")
    """botId"""
    flag = fields.CharField(max_length=255, default="", description="flag")
    """flag"""
    user_id = fields.CharField(max_length=255, description="请求用户id")
    """请求用户id"""
    group_id = fields.CharField(max_length=255, null=True, description="邀请入群id")
    """邀请入群id"""
    nickname = fields.CharField(max_length=255, description="请求人名称")
    """对象名称"""
    comment = fields.CharField(max_length=255, null=True, description="验证信息")
    """验证信息"""
    handle_type = fields.CharEnumField(
        RequestHandleType, null=True, description="处理类型"
    )
    """处理类型"""
    message_ids = fields.CharField(max_length=255, null=True, description="消息id列表")
    """消息id列表"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "fg_request"
        table_description = "好友群组请求"

    @classmethod
    async def approve(cls, bot: Bot, id: int) -> Self:
        """同意请求

        参数:
            bot: Bot
            id: 请求id

        异常:
            NotFoundError: 未发现请求
        """
        return await cls._handle_request(bot, id, RequestHandleType.APPROVE)

    @classmethod
    async def refused(cls, bot: Bot, id: int) -> Self:
        """拒绝请求

        参数:
            bot: Bot
            id: 请求id

        异常:
            NotFoundError: 未发现请求
        """
        return await cls._handle_request(bot, id, RequestHandleType.REFUSED)

    @classmethod
    async def ignore(cls, id: int) -> Self:
        """忽略请求

        参数:
            id: 请求id

        异常:
            NotFoundError: 未发现请求
        """
        return await cls._handle_request(None, id, RequestHandleType.IGNORE)

    @classmethod
    async def expire(cls, id: int):
        """忽略请求

        参数:
            id: 请求id

        异常:
            NotFoundError: 未发现请求
        """
        await cls._handle_request(None, id, RequestHandleType.EXPIRE)

    @classmethod
    async def _handle_request(
        cls,
        bot: Bot | None,
        id: int,
        handle_type: RequestHandleType,
    ) -> Self:
        """处理请求

        参数:
            bot: Bot
            id: 请求id
            handle_type: 处理类型

        异常:
            NotFoundError: 未发现请求
        """
        req = await cls.get_or_none(id=id)
        if not req:
            raise NotFoundError
        req.handle_type = handle_type
        await req.save(update_fields=["handle_type"])
        if bot and handle_type not in [
            RequestHandleType.IGNORE,
            RequestHandleType.EXPIRE,
        ]:
            if req.request_type == RequestType.FRIEND:
                await bot.set_friend_add_request(
                    flag=req.flag, approve=handle_type == RequestHandleType.APPROVE
                )
            else:
                await GroupConsole.update_or_create(
                    group_id=req.group_id, defaults={"group_flag": 1}
                )
                if req.flag == "0":
                    # 用户手动申请入群，创建群认证后提醒用户拉群
                    await bot.send_private_msg(
                        user_id=req.user_id,
                        message=f"已同意你对{BotConfig.self_nickname}的申请群组："
                        f"{req.group_id}，可以直接手动拉入群组，{BotConfig.self_nickname}会自动同意。",
                    )
                else:
                    # 正常同意群组请求
                    await bot.set_group_add_request(
                        flag=req.flag,
                        sub_type="invite",
                        approve=handle_type == RequestHandleType.APPROVE,
                    )
        return req

    @classmethod
    async def _run_script(cls):
        return [
            SqlUtils.add_column("fg_request", "message_ids", "character varying(255)")
        ]
