from fastapi import APIRouter
from loguru import logger
from nonebot.utils import escape_tag
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from .log_manager import LOG_STORAGE, ensure_log_sink_started, stop_log_sink_if_idle

router = APIRouter()


@router.websocket("/logs")
async def system_logs_realtime(websocket: WebSocket):
    await websocket.accept()
    await ensure_log_sink_started()

    async def log_listener(log: str):
        await websocket.send_text(log)

    if not LOG_STORAGE.add_listener(log_listener):
        await websocket.send_text("日志连接数已达上限，请稍后再试。")
        await websocket.close()
        stop_log_sink_if_idle()
        return
    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            recv = await websocket.receive()
            logger.trace(
                f"{system_logs_realtime.__name__!r} received "
                f"<e>{escape_tag(repr(recv))}</e>"
            )
    except WebSocketDisconnect:
        pass
    finally:
        LOG_STORAGE.remove_listener(log_listener)
        stop_log_sink_if_idle()
