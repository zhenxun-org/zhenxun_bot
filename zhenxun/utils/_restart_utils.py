import _thread
import asyncio
import copy
import json
from pathlib import Path
import time
from typing import Any

from nonebot.adapters import Bot

from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

_RESTART_STATE_FILE = Path() / "data" / ".restart_state.json"
_LEGACY_RESTART_MARK = Path() / "is_restart"
_LEGACY_RESTART_SCRIPT = Path() / "restart.sh"
_LEGACY_CONFIGURE_RESTART_PREFIX = ".configure_restart"
_RESTART_TICKET_KEY = "restart_ticket"
_PENDING_REQUEST_KEY = "pending_request"
_LAUNCHER_ACTION_KEY = "launcher_action"
_ACTION_RESTART = "restart"

_restart_pending: bool = False


def _ensure_state_parent() -> None:
    _RESTART_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_restart_state() -> dict[str, Any]:
    if not _RESTART_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_RESTART_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取重启状态文件失败，已忽略旧状态: {e}", "重启")
        return {}
    return data if isinstance(data, dict) else {}


def _write_restart_state(state: dict[str, Any]) -> None:
    if not state:
        if _RESTART_STATE_FILE.exists():
            _RESTART_STATE_FILE.unlink()
        return
    _ensure_state_parent()
    temp_file = _RESTART_STATE_FILE.with_name(f"{_RESTART_STATE_FILE.name}.tmp")
    temp_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(_RESTART_STATE_FILE)


def _cleanup_legacy_restart_artifacts() -> None:
    legacy_paths = [_LEGACY_RESTART_MARK, _LEGACY_RESTART_SCRIPT]
    legacy_paths.extend(Path().glob(f"{_LEGACY_CONFIGURE_RESTART_PREFIX}*"))
    for path in legacy_paths:
        if not path.exists():
            continue
        try:
            path.unlink()
            logger.info(f"已清理旧重启遗留文件: {path.name}", "重启")
        except Exception as e:
            logger.warning(f"清理旧重启遗留文件失败: {path.name} | {e}", "重启")


def issue_restart_ticket(source: str, *, ttl_seconds: int = 600) -> None:
    now = time.time()
    state = _read_restart_state()
    state[_RESTART_TICKET_KEY] = {
        "source": source,
        "issued_at": now,
        "expires_at": now + ttl_seconds,
    }
    _write_restart_state(state)
    logger.info(f"已记录重启授权，来源: {source}", "重启")


def _validate_restart_ticket(
    state: dict[str, Any],
    expected_source: str,
) -> tuple[bool, str]:
    ticket = state.get(_RESTART_TICKET_KEY)
    if not isinstance(ticket, dict):
        return False, "重启标志不存在..."
    if ticket.get("source") != expected_source:
        return False, "重启标志来源不匹配，请重新发起操作。"
    expires_at = float(ticket.get("expires_at", 0))
    if time.time() > expires_at:
        state.pop(_RESTART_TICKET_KEY, None)
        _write_restart_state(state)
        return False, "重启标志已过期，请重新设置配置。"
    return True, ""


async def _schedule_restart() -> tuple[bool, str]:
    global _restart_pending
    if _restart_pending:
        logger.warning("重启已在进行中，忽略重复请求。", "重启")
        return False, "重启已在进行中，请稍后查看结果。"
    _restart_pending = True
    logger.info("已标记重启请求，等待 launcher 接管下一代 worker...", "重启")

    async def _send_sigint() -> None:
        await asyncio.sleep(0.3)
        logger.info("发送重启信号...", "重启")
        _thread.interrupt_main()

    asyncio.create_task(_send_sigint())  # noqa: RUF006
    return True, "执行重启命令成功"


async def request_restart(
    source: str,
    *,
    receipt_bot_id: str | None = None,
    receipt_user_id: str | None = None,
    require_ticket: str | None = None,
) -> tuple[bool, str]:
    state = _read_restart_state()
    previous_state = copy.deepcopy(state)
    if require_ticket:
        ok, message = _validate_restart_ticket(state, require_ticket)
        if not ok:
            return False, message

    pending_request: dict[str, Any] = {
        "source": source,
        "requested_at": time.time(),
    }
    if receipt_bot_id and receipt_user_id:
        pending_request["receipt"] = {
            "bot_id": receipt_bot_id,
            "user_id": receipt_user_id,
        }
    state[_PENDING_REQUEST_KEY] = pending_request
    state[_LAUNCHER_ACTION_KEY] = _ACTION_RESTART
    if require_ticket:
        state.pop(_RESTART_TICKET_KEY, None)

    try:
        _write_restart_state(state)
    except Exception as e:
        logger.error(f"写入重启状态失败: {e}", "重启")
        return False, "写入重启状态失败。"

    ok, message = await _schedule_restart()
    if not ok:
        try:
            _write_restart_state(previous_state)
        except Exception as e:
            logger.warning(f"回滚重启状态失败: {e}", "重启")
        return False, message

    logger.info(f"收到重启请求，来源: {source}", "重启")
    return True, message


async def handle_restart_connect(bot: Bot) -> None:
    state = _read_restart_state()
    pending_request = state.get(_PENDING_REQUEST_KEY)
    if not isinstance(pending_request, dict):
        return

    source = str(pending_request.get("source", "unknown"))
    receipt = pending_request.get("receipt")
    if not isinstance(receipt, dict):
        logger.info(f"检测到重启完成，来源: {source}", "重启")
        state.pop(_PENDING_REQUEST_KEY, None)
        _write_restart_state(state)
        return

    expected_bot_id = str(receipt.get("bot_id", ""))
    receipt_user_id = str(receipt.get("user_id", ""))
    if expected_bot_id and expected_bot_id != str(bot.self_id):
        logger.debug(
            f"重启回执等待目标 Bot 连接: source={source} bot={expected_bot_id}"
        )
        return

    logger.info(f"检测到重启完成，来源: {source}", "重启")

    from zhenxun.configs.config import BotConfig
    from zhenxun.utils.message import MessageUtils
    from zhenxun.utils.platform import PlatformUtils

    target = PlatformUtils.get_target(user_id=receipt_user_id)
    if target:
        try:
            await MessageUtils.build_message(
                f"{BotConfig.self_nickname}已成功重启！"
            ).send(target, bot=bot)
        except Exception as e:
            logger.warning(f"发送重启回执失败: {e}", "重启")
    else:
        logger.warning("未找到重启回执目标，已跳过发送。", "重启")

    state.pop(_PENDING_REQUEST_KEY, None)
    _write_restart_state(state)


def _finalize_restart_state_on_startup() -> None:
    state = _read_restart_state()
    pending_request = state.get(_PENDING_REQUEST_KEY)
    if not isinstance(pending_request, dict):
        return

    source = str(pending_request.get("source", "unknown"))
    receipt = pending_request.get("receipt")
    if isinstance(receipt, dict):
        logger.info(f"检测到待发送的重启回执，来源: {source}", "重启")
        return

    logger.info(f"检测到重启完成，来源: {source}", "重启")
    state.pop(_PENDING_REQUEST_KEY, None)
    _write_restart_state(state)


@PriorityLifecycle.on_startup(priority=0)
async def _cleanup_restart_artifacts() -> None:
    _cleanup_legacy_restart_artifacts()
    _finalize_restart_state_on_startup()


@PriorityLifecycle.on_shutdown(priority=99)
async def _notify_restart_shutdown() -> None:
    if _restart_pending:
        logger.info("launcher 将在当前 worker 退出后接管重启。", "重启")
