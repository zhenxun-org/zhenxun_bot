from fastapi import APIRouter
from fastapi.responses import JSONResponse
import nonebot
from nonebot.adapters.onebot.v11 import ActionFailed

from zhenxun.models.fg_request import FgRequest
from zhenxun.models.group_console import GroupConsole
from zhenxun.services.log import logger
from zhenxun.utils.enum import RequestHandleType, RequestType
from zhenxun.utils.exception import NotFoundError
from zhenxun.utils.platform import PlatformUtils

from ....base_model import Result
from ....config import AVA_URL, GROUP_AVA_URL
from ....utils import DB_BUSY_MESSAGE, authentication
from .data_source import ApiDataSource
from .model import (
    ClearRequest,
    DeleteFriend,
    Friend,
    GroupDetail,
    GroupResult,
    HandleRequest,
    LeaveGroup,
    ReqResult,
    SendMessageParam,
    UpdateGroup,
    UserDetail,
)

router = APIRouter(prefix="/manage")


@router.get(
    "/get_group_list",
    dependencies=[authentication()],
    response_model=Result[list[GroupResult]],
    response_class=JSONResponse,
    description="获取群组列表",
)
async def _(bot_id: str) -> Result:
    """
    获取群信息
    """
    group_list_result = []
    try:
        bot = nonebot.get_bot(bot_id)
        group_list, _ = await PlatformUtils.get_group_list(bot)
        for g in group_list:
            ava_url = GROUP_AVA_URL.format(g.group_id, g.group_id)
            group_list_result.append(
                GroupResult(
                    group_id=g.group_id, group_name=g.group_name, ava_url=ava_url
                )
            )
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/get_group_list 调用错误", "WebUi", e=e)
        return Result.fail(f"{type(e)}: {e}")
    return Result.ok(group_list_result, "拿到了新鲜出炉的数据!")


@router.post(
    "/update_group",
    dependencies=[authentication()],
    response_model=Result[str],
    response_class=JSONResponse,
    description="修改群组信息",
)
async def _(group: UpdateGroup) -> Result[str]:
    try:
        await ApiDataSource.update_group(group)
        return Result.ok(info="已完成记录!")
    except Exception as e:
        logger.error(f"{router.prefix}/update_group 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_friend_list",
    dependencies=[authentication()],
    response_model=Result[list[Friend]],
    response_class=JSONResponse,
    description="获取好友列表",
)
async def _(bot_id: str) -> Result[list[Friend]]:
    try:
        bot = nonebot.get_bot(bot_id)
        friend_list, _ = await PlatformUtils.get_friend_list(bot)
        result_list = []
        for f in friend_list:
            ava_url = AVA_URL.format(f.user_id)
            result_list.append(
                Friend(user_id=f.user_id, nickname=f.user_name or "", ava_url=ava_url)
            )
        return Result.ok(
            result_list,
            "拿到了新鲜出炉的数据!",
        )
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error("调用API错误", "/get_group_list", e=e)
        return Result.fail(f"{type(e)}: {e}")


@router.get(
    "/get_request_count",
    dependencies=[authentication()],
    response_model=Result[dict[str, int]],
    response_class=JSONResponse,
    description="获取请求数量",
)
async def _() -> Result[dict[str, int]]:
    try:
        f_count = await FgRequest.filter(
            request_type=RequestType.FRIEND, handle_type__isnull=True
        ).count()
        g_count = await FgRequest.filter(
            request_type=RequestType.GROUP, handle_type__isnull=True
        ).count()
        data = {
            "friend_count": f_count,
            "group_count": g_count,
        }
        return Result.ok(data, "拿到了新鲜出炉的数据!")
    except Exception as e:
        logger.error("调用API错误", "/get_request_count", e=e)
        return Result.fail(f"{type(e)}: {e}")


@router.get(
    "/get_request_list",
    dependencies=[authentication()],
    response_model=Result[ReqResult],
    response_class=JSONResponse,
    description="获取请求列表",
)
async def _() -> Result[ReqResult]:
    try:
        return Result.ok(await ApiDataSource.get_request_list(), "拿到信息啦!")
    except Exception as e:
        logger.error(f"{router.prefix}/get_request_list 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/clear_request",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="清空请求列表",
)
async def _(cr: ClearRequest) -> Result:
    await FgRequest.filter(
        handle_type__isnull=True, request_type=cr.request_type
    ).update(handle_type=RequestHandleType.IGNORE)
    return Result.ok(info="成功清除了数据!")


@router.post(
    "/refuse_request",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="拒绝请求",
)
async def _(param: HandleRequest) -> Result:
    try:
        bot = nonebot.get_bot(param.bot_id)
        try:
            await FgRequest.refused(bot, param.id)
        except ActionFailed:
            await FgRequest.expire(param.id)
            return Result.warning_("请求失败，可能该请求已失效或请求数据错误...")
        except NotFoundError:
            return Result.warning_("未找到此Id请求...")
        return Result.ok(info="成功处理了请求!")
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/refuse_request 调用错误", "WebUi", e=e)
        return Result.fail(f"{type(e)}: {e}")


@router.post(
    "/delete_request",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="忽略请求",
)
async def _(param: HandleRequest) -> Result:
    await FgRequest.ignore(param.id)
    return Result.ok(info="成功处理了请求!")


@router.post(
    "/approve_request",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="同意请求",
)
async def _(param: HandleRequest) -> Result:
    try:
        bot = nonebot.get_bot(param.bot_id)
        if not (req := await FgRequest.get_or_none(id=param.id)):
            return Result.warning_("未找到此Id请求...")
        if req.request_type == RequestType.GROUP:
            if group := await GroupConsole.get_group_db(group_id=req.group_id):
                group.group_flag = 1
                await group.save(update_fields=["group_flag"])
            else:
                await GroupConsole.get_or_create_root_group(
                    group_id=req.group_id,
                    defaults={"group_flag": 1},
                    update_defaults=True,
                )
        try:
            await FgRequest.approve(bot, param.id)
            return Result.ok(info="成功处理了请求!")
        except ActionFailed:
            await FgRequest.expire(param.id)
            return Result.warning_("请求失败，可能该请求已失效或请求数据错误...")
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/approve_request 调用错误", "WebUi", e=e)
        return Result.fail(f"{type(e)}: {e}")


@router.post(
    "/leave_group",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="退群",
)
async def _(param: LeaveGroup) -> Result:
    try:
        bot = nonebot.get_bot(param.bot_id)
        if PlatformUtils.get_platform_scope(bot) != "qq_client":
            return Result.warning_("该平台不支持退群操作...")
        group_list, _ = await PlatformUtils.get_group_list(bot)
        if param.group_id not in [g.group_id for g in group_list]:
            return Result.warning_("Bot未在该群聊中...")
        await bot.set_group_leave(group_id=param.group_id)
        return Result.ok(info="成功处理了请求!")
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/leave_group 调用错误", "WebUi", e=e)
        return Result.fail(f"{type(e)}: {e}")


@router.post(
    "/delete_friend",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="删除好友",
)
async def _(param: DeleteFriend) -> Result:
    try:
        bot = nonebot.get_bot(param.bot_id)
        if PlatformUtils.get_platform_scope(bot) != "qq_client":
            return Result.warning_("该平台不支持删除好友操作...")
        friend_list, _ = await PlatformUtils.get_friend_list(bot)
        if param.user_id not in [f.user_id for f in friend_list]:
            return Result.warning_("Bot未有其好友...")
        await bot.delete_friend(user_id=param.user_id)
        return Result.ok(info="成功处理了请求!")
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/delete_friend 调用错误", "WebUi", e=e)
        return Result.fail(f"{type(e)}: {e}")


@router.get(
    "/get_friend_detail",
    dependencies=[authentication()],
    response_model=Result[UserDetail],
    response_class=JSONResponse,
    description="获取好友详情",
)
async def _(bot_id: str, user_id: str) -> Result[UserDetail]:
    try:
        result = await ApiDataSource.get_friend_detail(bot_id, user_id)
        return (
            Result.ok(result, "拿到信息啦!")
            if result
            else Result.warning_("未找到该好友...")
        )
    except TimeoutError:
        return Result.fail(DB_BUSY_MESSAGE)
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/get_friend_detail 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_group_detail",
    dependencies=[authentication()],
    response_model=Result[GroupDetail],
    response_class=JSONResponse,
    description="获取群组详情",
)
async def _(group_id: str) -> Result[GroupDetail]:
    try:
        return Result.ok(await ApiDataSource.get_group_detail(group_id), "拿到信息啦!")
    except TimeoutError:
        return Result.fail(DB_BUSY_MESSAGE)
    except Exception as e:
        logger.error(f"{router.prefix}/get_group_detail 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/send_message",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="发送消息",
)
async def _(param: SendMessageParam) -> Result:
    try:
        bot = nonebot.get_bot(param.bot_id)
        await PlatformUtils.send_message(
            bot, param.user_id, param.group_id, param.message
        )
        return Result.ok("发送成功!")
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/send_message 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
