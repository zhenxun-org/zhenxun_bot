from datetime import timedelta
from typing import Any, overload

import nonebot
from nonebot import require

require("nonebot_plugin_session")
from loguru import logger as logger_
from nonebot.log import default_filter, default_format
from nonebot_plugin_session import Session
from nonebot_plugin_uninfo import Session as uninfoSession

from zhenxun.configs.path_config import LOG_PATH

driver = nonebot.get_driver()

log_level = driver.config.log_level or "INFO"

logger_.add(
    LOG_PATH / "{time:YYYY-MM-DD}.log",
    level=log_level,
    rotation="00:00",
    format=default_format,
    filter=default_filter,
    retention=timedelta(days=30),
)

logger_.add(
    LOG_PATH / "error_{time:YYYY-MM-DD}.log",
    level="ERROR",
    rotation="00:00",
    format=default_format,
    filter=default_filter,
    retention=timedelta(days=30),
)


class logger:
    """
    一个经过优化的、支持多种上下文和格式的日志记录器。
    """

    TEMPLATE_ADAPTER = "Adapter[<m>{}</m>]"
    TEMPLATE_USER = "用户[<u><e>{}</e></u>]"
    TEMPLATE_GROUP = "群聊[<u><e>{}</e></u>]"
    TEMPLATE_COMMAND = "CMD[<u><c>{}</c></u>]"
    TEMPLATE_PLATFORM = "平台[<u><m>{}</m></u>]"
    TEMPLATE_TARGET = "[Target]([<u><e>{}</e></u>])"
    SUCCESS_TEMPLATE = "[<u><c>{}</c></u>]: {} | 参数[{}] 返回: [<y>{}</y>]"

    @classmethod
    def __parser_template(
        cls,
        info: str,
        command: str | None = None,
        user_id: int | str | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
    ) -> str:
        """
        优化后的模板解析器，构建并连接日志信息片段。
        """
        parts = []
        if adapter:
            parts.append(cls.TEMPLATE_ADAPTER.format(adapter))
        if platform:
            parts.append(cls.TEMPLATE_PLATFORM.format(platform))
        if group_id:
            parts.append(cls.TEMPLATE_GROUP.format(group_id))
        if user_id:
            parts.append(cls.TEMPLATE_USER.format(user_id))
        if command:
            parts.append(cls.TEMPLATE_COMMAND.format(command))
        if target:
            parts.append(cls.TEMPLATE_TARGET.format(target))

        parts.append(info)
        return " ".join(parts)

    @classmethod
    def _log(
        cls,
        level: str,
        info: str,
        command: str | None = None,
        session: int | str | Session | uninfoSession | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ):
        """
        核心日志处理方法，处理所有日志级别的通用逻辑。
        """
        user_id: str | None = str(session) if isinstance(session, int | str) else None

        if isinstance(session, Session):
            user_id = session.id1
            adapter = session.bot_type
            group_id = f"{session.id3}:{session.id2}" if session.id3 else session.id2
            platform = platform or session.platform
        elif isinstance(session, uninfoSession):
            user_id = session.user.id
            adapter = session.adapter
            if session.group:
                group_id = session.group.id
            platform = session.basic.get("scope")

        template = cls.__parser_template(
            info, command, user_id, group_id, adapter, target, platform
        )

        if e:
            template += f" || 错误 <r>{type(e).__name__}: {e}</r>"

        try:
            log_func = getattr(logger_.opt(colors=True), level)
            log_func(template)
        except Exception:
            log_func_fallback = getattr(logger_, level)
            log_func_fallback(template)

    @overload
    @classmethod
    def info(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
    ): ...
    @overload
    @classmethod
    def info(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: Session | None = None,
        target: Any = None,
        platform: str | None = None,
    ): ...
    @overload
    @classmethod
    def info(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: uninfoSession | None = None,
        target: Any = None,
        platform: str | None = None,
    ): ...

    @classmethod
    def info(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | Session | uninfoSession | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
    ):
        cls._log(
            "info",
            info=info,
            command=command,
            session=session,
            group_id=group_id,
            adapter=adapter,
            target=target,
            platform=platform,
        )

    @classmethod
    def success(
        cls,
        info: str,
        command: str,
        param: dict[str, Any] | None = None,
        result: str = "",
    ):
        param_str = (
            ",".join([f"<m>{k}</m>:<g>{v}</g>" for k, v in param.items()])
            if param
            else ""
        )
        logger_.opt(colors=True).success(
            cls.SUCCESS_TEMPLATE.format(command, info, param_str, result)
        )

    @overload
    @classmethod
    def warning(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def warning(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: Session | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def warning(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: uninfoSession | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...

    @classmethod
    def warning(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | Session | uninfoSession | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ):
        cls._log(
            "warning",
            info=info,
            command=command,
            session=session,
            group_id=group_id,
            adapter=adapter,
            target=target,
            platform=platform,
            e=e,
        )

    @overload
    @classmethod
    def error(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def error(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: Session | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def error(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: uninfoSession | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...

    @classmethod
    def error(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | Session | uninfoSession | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ):
        cls._log(
            "error",
            info=info,
            command=command,
            session=session,
            group_id=group_id,
            adapter=adapter,
            target=target,
            platform=platform,
            e=e,
        )

    @overload
    @classmethod
    def debug(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def debug(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: Session | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def debug(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: uninfoSession | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...

    @classmethod
    def debug(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | Session | uninfoSession | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ):
        cls._log(
            "debug",
            info=info,
            command=command,
            session=session,
            group_id=group_id,
            adapter=adapter,
            target=target,
            platform=platform,
            e=e,
        )

    @overload
    @classmethod
    def trace(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def trace(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: Session | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...
    @overload
    @classmethod
    def trace(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: uninfoSession | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ): ...

    @classmethod
    def trace(
        cls,
        info: str,
        command: str | None = None,
        *,
        session: int | str | Session | uninfoSession | None = None,
        group_id: int | str | None = None,
        adapter: str | None = None,
        target: Any = None,
        platform: str | None = None,
        e: Exception | None = None,
    ):
        cls._log(
            "trace",
            info=info,
            command=command,
            session=session,
            group_id=group_id,
            adapter=adapter,
            target=target,
            platform=platform,
            e=e,
        )
