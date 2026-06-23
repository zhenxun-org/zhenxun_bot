from fastapi import APIRouter
from fastapi.responses import JSONResponse
import nonebot
from nonebot import require
from nonebot.config import Config

from zhenxun.services.log import logger

from ....base_model import BaseResultModel, QueryModel, Result
from ....utils import DB_BUSY_MESSAGE, authentication
from .data_source import ApiDataSource
from .model import AllChatAndCallCount, BotInfo, ChatCallMonthCount, QueryChatCallCount

require("plugin_store")

router = APIRouter(prefix="/dashboard")

driver = nonebot.get_driver()


@router.get(
    "/get_bot_list",
    dependencies=[authentication()],
    response_model=Result[list[BotInfo]],
    response_class=JSONResponse,
    description="获取bot列表",  # type: ignore
)
async def _() -> Result[list[BotInfo]]:
    try:
        return Result.ok(await ApiDataSource.get_bot_list(), "拿到信息啦!")
    except TimeoutError:
        return Result.fail(DB_BUSY_MESSAGE)
    except Exception as e:
        logger.error(f"{router.prefix}/get_bot_list 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_chat_and_call_count",
    dependencies=[authentication()],
    response_model=Result[QueryChatCallCount],
    response_class=JSONResponse,
    description="获取聊天/调用记录的全部和今日数量",
)
async def _(bot_id: str | None = None) -> Result[QueryChatCallCount]:
    try:
        return Result.ok(
            await ApiDataSource.get_chat_and_call_count(bot_id), "拿到信息啦!"
        )
    except TimeoutError:
        return Result.fail(DB_BUSY_MESSAGE)
    except Exception as e:
        logger.error(f"{router.prefix}/get_chat_and_call_count 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_all_chat_and_call_count",
    dependencies=[authentication()],
    response_model=Result[AllChatAndCallCount],
    response_class=JSONResponse,
    description="获取聊天/调用记录的全部数据次数",
)
async def _(bot_id: str | None = None) -> Result[AllChatAndCallCount]:
    try:
        return Result.ok(
            await ApiDataSource.get_all_chat_and_call_count(bot_id), "拿到信息啦!"
        )
    except TimeoutError:
        return Result.fail(DB_BUSY_MESSAGE)
    except Exception as e:
        logger.error(
            f"{router.prefix}/get_all_chat_and_call_count 调用错误", "WebUi", e=e
        )
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_chat_and_call_month",
    dependencies=[authentication()],
    response_model=Result[ChatCallMonthCount],
    response_class=JSONResponse,
    description="获取聊天/调用记录的一个月数量",  # type: ignore
)
async def _(bot_id: str | None = None) -> Result[ChatCallMonthCount]:
    try:
        return Result.ok(
            await ApiDataSource.get_chat_and_call_month(bot_id), "拿到信息啦!"
        )
    except TimeoutError:
        return Result.fail(DB_BUSY_MESSAGE)
    except Exception as e:
        logger.error(f"{router.prefix}/get_chat_and_call_month 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/get_connect_log",
    dependencies=[authentication()],
    response_model=Result[BaseResultModel],
    response_class=JSONResponse,
    description="获取Bot连接记录",  # type: ignore
)
async def _(query: QueryModel) -> Result[BaseResultModel]:
    try:
        return Result.ok(await ApiDataSource.get_connect_log(query), "拿到信息啦!")
    except Exception as e:
        logger.error(f"{router.prefix}/get_connect_log 调用错误", "WebUi", e=e)
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_nonebot_config",
    dependencies=[authentication()],
    response_model=Result[Config],
    response_class=JSONResponse,
    description="获取nb配置",  # type: ignore
)
async def _() -> Result[Config]:
    return Result.ok(driver.config)
