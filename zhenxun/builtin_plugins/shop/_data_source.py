import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import inspect
import time
from types import MappingProxyType
from typing import Any, Literal

from nonebot.adapters import Bot, Event
from nonebot.compat import model_dump
from nonebot_plugin_alconna import UniMessage, UniMsg
from nonebot_plugin_uninfo import Uninfo
from pydantic import BaseModel, Field, create_model
from tortoise.expressions import Q

from zhenxun.models.friend_user import FriendUser
from zhenxun.models.goods_info import GoodsInfo
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.user_console import UserConsole
from zhenxun.models.user_gold_log import UserGoldLog
from zhenxun.models.user_props_log import UserPropsLog
from zhenxun.services.log import logger
from zhenxun.utils.enum import GoldHandle, PropHandle
from zhenxun.utils.image_utils import BuildImage, ImageTemplate
from zhenxun.utils.platform import PlatformUtils

from .config import ICON_PATH, PLATFORM_PATH, base_config
from .html_image import html_image
from .normal_image import normal_image


class Goods(BaseModel):
    name: str
    """商品名称"""
    before_handle: list[Callable] = Field(default_factory=list)
    """使用前函数"""
    after_handle: list[Callable] = Field(default_factory=list)
    """使用后函数"""
    func: Callable | None = None
    """使用函数"""
    params: Any = None
    """参数"""
    send_success_msg: bool = True
    """使用成功是否发送消息"""
    max_num_limit: int = 1
    """单次使用最大次数"""
    model: Any = None
    """model"""
    session: Uninfo | None = None
    """Uninfo"""


class ShopParam(BaseModel):
    goods_name: str
    """商品名称"""
    user_id: str
    """用户id"""
    group_id: str | None
    """群聊id"""
    bot: Any
    """bot"""
    event: Event
    """event"""
    num: int
    """道具单次使用数量"""
    text: str
    """text"""
    send_success_msg: bool = True
    """是否发送使用成功信息"""
    max_num_limit: int = 1
    """单次使用最大次数"""
    session: Uninfo | None = None
    """Uninfo"""
    message: UniMsg
    """UniMessage"""
    extra_data: dict[str, Any] = Field(default_factory=dict)
    """额外数据"""

    class Config:
        arbitrary_types_allowed = True

    def to_dict(self, **kwargs):
        return model_dump(self, **kwargs)


async def gold_rank(
    session: Uninfo, group_id: str | None, num: int
) -> BuildImage | str:
    query = UserConsole
    if group_id:
        uid_list = await GroupInfoUser.filter(group_id=group_id).values_list(
            "user_id", flat=True
        )
        if uid_list:
            query = query.filter(user_id__in=uid_list)
    user_list = await query.annotate().order_by("-gold").values_list("user_id", "gold")
    if not user_list:
        return "当前还没有人拥有金币哦..."
    user_id_list = [user[0] for user in user_list]
    if session.user.id in user_id_list:
        index = user_id_list.index(session.user.id) + 1
    else:
        index = "-1（未统计）"
    user_list = user_list[:num] if num < len(user_list) else user_list
    friend_user = await FriendUser.filter(user_id__in=user_id_list).values_list(
        "user_id", "user_name"
    )
    uid2name = {user[0]: user[1] for user in friend_user}
    if diff_id := set(user_id_list).difference(set(uid2name.keys())):
        group_user = await GroupInfoUser.filter(user_id__in=diff_id).values_list(
            "user_id", "user_name"
        )
        for g in group_user:
            uid2name[g[0]] = g[1]
    column_name = ["排名", "-", "名称", "金币", "平台"]
    data_list = []
    platform = PlatformUtils.get_platform(session)
    for i, user in enumerate(user_list):
        ava_bytes = await PlatformUtils.get_user_avatar(
            user[0], platform, session.self_id
        )
        data_list.append(
            [
                f"{i + 1}",
                (ava_bytes, 30, 30) if platform == "qq" else "",
                uid2name.get(user[0]),
                user[1],
                (PLATFORM_PATH.get(platform), 30, 30),
            ]
        )
    if group_id:
        title = "金币群组内排行"
        tip = f"你的排名在本群第 {index} 位哦!"
    else:
        title = "金币全局排行"
        tip = f"你的排名在全局第 {index} 位哦!"
    return await ImageTemplate.table_page(title, tip, column_name, data_list)


class ShopManage:
    uuid2goods: dict[str, Goods] = {}  # noqa: RUF012

    @classmethod
    async def get_shop_image(cls) -> bytes:
        if base_config.get("style") == "zhenxun":
            return await html_image()
        return await normal_image()

    @classmethod
    def __build_params(
        cls,
        bot: Bot,
        event: Event,
        session: Uninfo,
        message: UniMsg,
        goods: Goods,
        num: int,
        text: str,
    ) -> tuple[ShopParam, dict[str, Any]]:
        """构造参数

        参数:
            bot: bot
            event: event
            goods_name: 商品名称
            num: 数量
            text: 其他信息
        """
        group_id = None
        if session.group:
            group_id = (
                session.group.parent.id if session.group.parent else session.group.id
            )
        _kwargs = goods.params
        model = goods.model(
            **{
                "goods_name": goods.name,
                "bot": bot,
                "event": event,
                "user_id": session.user.id,
                "group_id": group_id,
                "num": num,
                "text": text,
                "session": session,
                "message": message,
            }
        )
        return model, {
            **_kwargs,
            "_bot": bot,
            "event": event,
            "user_id": session.user.id,
            "group_id": group_id,
            "num": num,
            "text": text,
            "goods_name": goods.name,
        }

    @classmethod
    def __parse_args(
        cls,
        args: MappingProxyType,
        param: ShopParam,
        session: Uninfo,
        message: UniMsg,
        **kwargs,
    ) -> dict:
        """解析参数

        参数:
            args: MappingProxyType
            param: ShopParam

        返回:
            dict: 参数
        """
        _bot = param.bot
        param.bot = None
        param_json = {
            "bot": _bot,
            "kwargs": kwargs,
            **param.to_dict(),
            **param.extra_data,
            "session": session,
            "message": message,
        }
        for key in list(param_json.keys()):
            if key not in args:
                del param_json[key]
        return param_json

    @classmethod
    async def run_before_after(
        cls,
        goods: Goods,
        param: ShopParam,
        session: Uninfo,
        message: UniMsg,
        run_type: Literal["after", "before"],
        **kwargs,
    ):
        """运行使用前使用后函数

        参数:
            goods: Goods
            param: 参数
            run_type: 运行类型
        """
        fun_list = goods.before_handle if run_type == "before" else goods.after_handle
        if fun_list:
            for func in fun_list:
                if args := inspect.signature(func).parameters:
                    if asyncio.iscoroutinefunction(func):
                        await func(
                            **cls.__parse_args(args, param, session, message, **kwargs)
                        )
                    else:
                        func(
                            **cls.__parse_args(args, param, session, message, **kwargs)
                        )
                elif asyncio.iscoroutinefunction(func):
                    await func()
                else:
                    func()

    @classmethod
    async def __run(
        cls,
        goods: Goods,
        param: ShopParam,
        session: Uninfo,
        message: UniMsg,
        **kwargs,
    ) -> str | UniMessage | None:
        """运行道具函数

        参数:
            goods: Goods
            param: ShopParam

        返回:
            str | MessageFactory | None: 使用完成后返回信息
        """
        args = inspect.signature(goods.func).parameters  # type: ignore
        if goods.func:
            if args:
                return (
                    await goods.func(
                        **cls.__parse_args(args, param, session, message, **kwargs)
                    )
                    if asyncio.iscoroutinefunction(goods.func)
                    else goods.func(
                        **cls.__parse_args(args, param, session, message, **kwargs)
                    )
                )
            if asyncio.iscoroutinefunction(goods.func):
                return await goods.func()
            else:
                return goods.func()

    @classmethod
    async def use(
        cls,
        bot: Bot,
        event: Event,
        session: Uninfo,
        message: UniMsg,
        goods_name: str,
        num: int,
        text: str,
    ) -> str | UniMessage | None:
        """使用道具

        参数:
            bot: Bot
            event: Event
            session: Session
            message: 消息
            goods_name: 商品名称
            num: 使用数量
            text: 其他信息

        返回:
            str | MessageFactory | None: 使用完成后返回信息
        """
        if goods_name.isdigit():
            try:
                user = await UserConsole.get_user(user_id=session.user.id)
                uuid = list(user.props.keys())[int(goods_name)]
                goods_info = await GoodsInfo.get_or_none(uuid=uuid)
            except IndexError:
                return "仓库中道具不存在..."
        else:
            goods_info = await GoodsInfo.get_or_none(goods_name=goods_name)
        if not goods_info:
            return f"{goods_name} 不存在..."
        if goods_info.is_passive:
            return f"{goods_info.goods_name} 是被动道具, 无法使用..."
        goods = cls.uuid2goods.get(goods_info.uuid)
        if not goods or not goods.func:
            return f"{goods_info.goods_name} 未注册使用函数, 无法使用..."
        param, kwargs = cls.__build_params(
            bot, event, session, message, goods, num, text
        )
        if num > param.max_num_limit:
            return f"{goods_info.goods_name} 单次使用最大数量为{param.max_num_limit}..."
        await cls.run_before_after(goods, param, session, message, "before", **kwargs)
        await UserConsole.use_props(
            session.user.id, goods_info.uuid, num, PlatformUtils.get_platform(session)
        )
        result = await cls.__run(goods, param, session, message, **kwargs)

        await cls.run_before_after(goods, param, session, message, "after", **kwargs)
        if not result and param.send_success_msg:
            result = f"使用道具 {goods.name} {num} 次成功！"
        return result

    @classmethod
    async def register_use(
        cls,
        name: str,
        uuid: str,
        func: Callable,
        send_success_msg: bool = True,
        max_num_limit: int = 1,
        before_handle: list[Callable] = [],
        after_handle: list[Callable] = [],
        **kwargs,
    ):
        """注册使用方法

        参数:
            uuid: uuid
            func: 使用函数
            send_success_msg: 使用成功时发送消息.
            max_num_limit: 单次最大使用限制.
            before_handle: 使用前函数.
            after_handle: 使用后函数.

        异常:
            ValueError: 该商品使用函数已被注册！
        """
        if uuid in cls.uuid2goods:
            raise ValueError("该商品使用函数已被注册！")
        cls.uuid2goods[uuid] = Goods(
            model=create_model(
                f"{uuid}_model",
                __base__=ShopParam,
                send_success_msg=(bool, Field(default=send_success_msg)),
                max_num_limit=(int, Field(default=max_num_limit)),
                extra_data=(dict[str, Any], Field(default=kwargs)),
            ),
            params=kwargs,
            before_handle=before_handle,
            after_handle=after_handle,
            name=name,
            func=func,
        )

    @classmethod
    async def buy_prop(
        cls, user_id: str, name: str, num: int = 1, platform: str | None = None
    ) -> str:
        """购买道具

        参数:
            user_id: 用户id
            name: 道具名称
            num: 购买数量.
            platform: 平台.

        返回:
            str: 返回小
        """
        if num < 0:
            return "购买的数量要大于0!"
        goods_list = (
            await GoodsInfo.filter(
                Q(goods_limit_time__gte=time.time()) | Q(goods_limit_time=0)
            )
            .annotate()
            .order_by("id")
            .all()
        )
        if name.isdigit():
            if int(name) > len(goods_list) or int(name) <= 0:
                return "道具编号不存在..."
            goods = goods_list[int(name) - 1]
        elif filter_goods := [g for g in goods_list if g.goods_name == name]:
            goods = filter_goods[0]
        else:
            return "道具名称不存在..."
        user = await UserConsole.get_user(user_id, platform)
        price = goods.goods_price * num * goods.goods_discount
        if user.gold < price:
            return "糟糕! 您的金币好像不太够哦..."
        today = datetime.now()
        create_time = today - timedelta(
            hours=today.hour, minutes=today.minute, seconds=today.second
        )
        count = await UserPropsLog.filter(
            user_id=user_id,
            handle=PropHandle.BUY,
            uuid=goods.uuid,
            create_time__gte=create_time,
        ).count()
        if goods.daily_limit and count >= goods.daily_limit:
            return "今天的购买已达限制了喔!"
        await UserGoldLog.create(user_id=user_id, gold=price, handle=GoldHandle.BUY)
        await UserPropsLog.create(
            user_id=user_id, uuid=goods.uuid, gold=price, num=num, handle=PropHandle.BUY
        )
        logger.info(
            f"花费 {price} 金币购买 {goods.goods_name} ×{num} 成功！",
            "购买道具",
            session=user_id,
        )
        user.gold -= int(price)
        if goods.uuid not in user.props:
            user.props[goods.uuid] = 0
        user.props[goods.uuid] += num
        await user.save(update_fields=["gold", "props"])
        return f"花费 {price} 金币购买 {goods.goods_name} ×{num} 成功！"

    @classmethod
    async def my_props(
        cls, user_id: str, name: str, platform: str | None = None
    ) -> BuildImage | None:
        """获取道具背包

        参数:
            user_id: 用户id
            name: 用户昵称
            platform: 平台.

        返回:
            BuildImage | None: 道具背包图片
        """
        user = await UserConsole.get_user(user_id, platform)
        if not user.props:
            return None

        user.props = {uuid: count for uuid, count in user.props.items() if count > 0}

        goods_list = await GoodsInfo.filter(uuid__in=user.props.keys()).all()
        goods_by_uuid = {item.uuid: item for item in goods_list}

        table_rows = []
        for i, prop_uuid in enumerate(user.props):
            prop = goods_by_uuid.get(prop_uuid)
            if not prop:
                continue

            icon = ""
            if prop.icon:
                icon_path = ICON_PATH / prop.icon
                icon = (icon_path, 33, 33) if icon_path.exists() else ""

            table_rows.append(
                [
                    icon,
                    i,
                    prop.goods_name,
                    user.props[prop_uuid],
                    prop.goods_description,
                ]
            )

        if not table_rows:
            return None

        column_name = ["-", "使用ID", "名称", "数量", "简介"]
        return await ImageTemplate.table_page(
            f"{name}的道具仓库",
            "通过 使用道具[ID/名称] 令道具生效",
            column_name,
            table_rows,
        )

    @classmethod
    async def my_cost(cls, user_id: str, platform: str | None = None) -> int:
        """用户金币

        参数:
            user_id: 用户id
            platform: 平台.

        返回:
            int: 金币数量
        """
        user = await UserConsole.get_user(user_id, platform)
        return user.gold
