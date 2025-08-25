import asyncio
import contextlib
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import nonebot
from nonebot.config import Config
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from zhenxun.models.bot_console import BotConsole
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.platform import PlatformUtils

from ....base_model import Result
from ....config import QueryDateType
from ....utils import authentication, get_system_status
from .data_source import ApiDataSource
from .model import (
    ActiveGroup,
    BaseInfo,
    BotBlockModule,
    BotManageUpdateParam,
    BotStatusParam,
    HotPlugin,
    NonebotData,
    QueryCount,
)

driver = nonebot.get_driver()
run_time = time.time()

ws_router = APIRouter()
router = APIRouter(prefix="/main")


@router.get(
    "/get_base_info",
    dependencies=[authentication()],
    response_model=Result[list[BaseInfo]],
    response_class=JSONResponse,
    description="基础信息",
)
async def _(bot_id: str | None = None) -> Result[list[BaseInfo]]:
    """获取Bot基础信息

    参数:
        bot_id (Optional[str], optional): bot_id. Defaults to None.

    返回:
        Result: 获取指定bot信息与bot列表
    """
    try:
        result = await ApiDataSource.get_base_info(bot_id)
        if not result:
            Result.warning_("无Bot连接...")
        return Result.ok(result, "拿到信息啦!")
    except Exception as e:
        logger.error(f"{router.prefix}/get_base_info 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_all_chat_count",
    dependencies=[authentication()],
    response_model=Result[QueryCount],
    response_class=JSONResponse,
    description="获取接收消息数量",
)
async def _(bot_id: str | None = None) -> Result[QueryCount]:
    try:
        return Result.ok(await ApiDataSource.get_all_chat_count(bot_id), "拿到信息啦!")
    except Exception as e:
        logger.error(f"{router.prefix}/get_all_chat_count 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_all_call_count",
    dependencies=[authentication()],
    response_model=Result[QueryCount],
    response_class=JSONResponse,
    description="获取调用次数",
)
async def _(bot_id: str | None = None) -> Result[QueryCount]:
    try:
        return Result.ok(await ApiDataSource.get_all_call_count(bot_id), "拿到信息啦!")
    except Exception as e:
        logger.error(f"{router.prefix}/get_all_call_count 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "get_fg_count",
    dependencies=[authentication()],
    response_model=Result[dict[str, int]],
    response_class=JSONResponse,
    description="好友/群组数量",
)
async def _(bot_id: str) -> Result[dict[str, int]]:
    try:
        bot = nonebot.get_bot(bot_id)
        data = {
            "friend_count": len(await PlatformUtils.get_friend_list(bot)),
            "group_count": len(await PlatformUtils.get_group_list(bot)),
        }
        return Result.ok(data, "拿到信息啦!")
    except (ValueError, KeyError):
        return Result.warning_("指定Bot未连接...")
    except Exception as e:
        logger.error(f"{router.prefix}/get_fg_count 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_nb_data",
    dependencies=[authentication()],
    response_model=Result[NonebotData],
    response_class=JSONResponse,
    description="获取nb数据",
)
async def _() -> Result[NonebotData]:
    global run_time
    return Result.ok(NonebotData(config=driver.config, run_time=int(run_time)))


@router.get(
    "/get_nb_config",
    dependencies=[authentication()],
    response_model=Result[Config],
    response_class=JSONResponse,
    description="获取nb配置",
)
async def _() -> Result[Config]:
    return Result.ok(driver.config)


@router.get(
    "/get_run_time",
    dependencies=[authentication()],
    response_model=Result[int],
    response_class=JSONResponse,
    description="获取nb运行时间",
)
async def _() -> Result[int]:
    global run_time
    return Result.ok(int(run_time))


@router.get(
    "/get_active_group",
    dependencies=[authentication()],
    response_model=Result[list[ActiveGroup]],
    response_class=JSONResponse,
    description="获取活跃群聊",
)
async def _(
    date_type: QueryDateType | None = None, bot_id: str | None = None
) -> Result[list[ActiveGroup]]:
    try:
        return Result.ok(
            await ApiDataSource.get_active_group(date_type, bot_id), "拿到信息啦!"
        )
    except Exception as e:
        logger.error(f"{router.prefix}/get_active_group 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_hot_plugin",
    dependencies=[authentication()],
    response_model=Result[list[HotPlugin]],
    response_class=JSONResponse,
    description="获取热门插件",
)
async def _(
    date_type: QueryDateType | None = None, bot_id: str | None = None
) -> Result[list[HotPlugin]]:
    try:
        return Result.ok(
            await ApiDataSource.get_hot_plugin(date_type, bot_id), "拿到信息啦!"
        )
    except Exception as e:
        logger.error(f"{router.prefix}/get_hot_plugin 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/change_bot_status",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="修改bot全局开关",
)
async def _(param: BotStatusParam):
    try:
        await BotConsole.set_bot_status(param.status, param.bot_id)
        return Result.ok(info="修改bot全局开关成功！")
    except (ValueError, KeyError):
        return Result.fail("Bot未初始化...")


@router.get(
    "/get_bot_block_module",
    dependencies=[authentication()],
    response_model=Result[BotBlockModule],
    response_class=JSONResponse,
    description="获取bot层面的禁用模块",
)
async def _(bot_id: str) -> Result[BotBlockModule]:
    try:
        return Result.ok(
            await ApiDataSource.get_bot_block_module(bot_id), "拿到信息啦!"
        )
    except Exception as e:
        logger.error(f"{router.prefix}/get_bot_block_module 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/update_bot_manage",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="修改bot全局开关",
)
async def _(param: BotManageUpdateParam):
    try:
        bot_data = await BotConsole.get_or_none(bot_id=param.bot_id)
        if not bot_data:
            return Result.fail("Bot数据不存在...")
        bot_data.block_plugins = CommonUtils.convert_module_format(param.block_plugins)
        bot_data.block_tasks = CommonUtils.convert_module_format(param.block_tasks)
        await bot_data.save(update_fields=["block_plugins", "block_tasks"])
        return Result.ok()
    except Exception as e:
        logger.error(f"{router.prefix}/update_bot_manage 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@ws_router.websocket("/system_status")
async def system_logs_realtime(websocket: WebSocket, sleep: int = 5):
    await websocket.accept()
    logger.debug("ws system_status is connect")
    with contextlib.suppress(
        WebSocketDisconnect, ConnectionClosedError, ConnectionClosedOK
    ):
        while websocket.client_state == WebSocketState.CONNECTED:
            system_status = await get_system_status()
            await websocket.send_text(system_status.json())
            await asyncio.sleep(sleep)
