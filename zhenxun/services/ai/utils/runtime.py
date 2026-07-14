from typing import Any

from nonebot.adapters import Bot, Event

from zhenxun.services.ai.utils.scope import ScopeBuilder


class ContextUtils:
    """
    从底层依赖容器 (deps) 中提取运行环境信息的纯静态工具类。
    """

    @staticmethod
    def extract_user_id(deps: Any) -> str | None:
        """从依赖容器中提取当前用户的 ID"""
        if not deps:
            return None
        if hasattr(deps, "user_id") and getattr(deps, "user_id") is not None:
            return str(getattr(deps, "user_id"))
        event = getattr(deps, "event", None)
        if event:
            try:
                return str(event.get_user_id())
            except Exception:
                return (
                    str(getattr(event, "user_id", ""))
                    or str(getattr(event, "sender_id", ""))
                    or None
                )
        return None

    @staticmethod
    def extract_group_id(deps: Any) -> str | None:
        """从依赖容器中提取当前群聊的 ID"""
        if not deps:
            return None
        if hasattr(deps, "group_id") and getattr(deps, "group_id") is not None:
            return str(getattr(deps, "group_id"))
        event = getattr(deps, "event", None)
        if event:
            return str(getattr(event, "group_id", "")) or None
        return None

    @staticmethod
    def extract_platform(deps: Any) -> str:
        """从依赖容器的 Bot 实例中提取当前聊天平台名称"""
        if not deps:
            return "unknown"
        if hasattr(deps, "platform") and getattr(deps, "platform") is not None:
            return str(getattr(deps, "platform"))
        bot = getattr(deps, "bot", None)
        if bot:
            from zhenxun.utils.platform import PlatformUtils

            return PlatformUtils.get_platform(bot)
        return "unknown"

    @staticmethod
    def extract_concurrency_lock_id(
        context: Any, scope: Any, default_session_id: str
    ) -> str:
        """根据并发隔离范围 scope 动态计算并返回当前会话的并发锁 ID"""
        from zhenxun.services.ai.flow.core.models import ConcurrencyScope

        scope = scope or ConcurrencyScope.GROUP

        ns = getattr(getattr(context, "session", None), "namespace", "global")
        ns_suffix = f"_{ns}" if ns and ns != "global" else ""

        if scope == ConcurrencyScope.GLOBAL:
            return f"lock_global{ns_suffix}"
        elif scope == ConcurrencyScope.GROUP:
            gid = ContextUtils.extract_group_id(getattr(context, "deps", None))
            uid = ContextUtils.extract_user_id(getattr(context, "deps", None))
            return (
                f"lock_group_{gid}{ns_suffix}" if gid else f"lock_user_{uid}{ns_suffix}"
            )
        elif scope == ConcurrencyScope.USER:
            uid = ContextUtils.extract_user_id(getattr(context, "deps", None))
            return (
                f"lock_user_{uid}{ns_suffix}"
                if uid
                else f"lock_default_user{ns_suffix}"
            )
        else:
            return f"lock_session_{default_session_id}"

    @staticmethod
    def generate_session_meta(
        bot: Bot,
        event: Event | None = None,
        deps: Any | None = None,
        scope_builder: ScopeBuilder | None = None,
        prefix: str = "",
        namespace: str | None = None,
        agent_name: str | None = None,
    ) -> Any:
        """根据事件和隔离级别，自动提取生成基于路径作用域的 SessionMetadata"""
        from zhenxun.services.ai.context.memory.types import (
            Isolation,
            SessionMetadata,
        )
        from zhenxun.services.ai.run.context import NoneBotDeps

        if scope_builder is None:
            scope_builder = Isolation.AGENT_USER()

        if deps is None:
            deps = NoneBotDeps(bot=bot, event=event) if event else NoneBotDeps(bot=bot)
        selector = scope_builder.resolve(
            deps=deps,
            prefix=prefix,
            default_namespace=namespace,
            default_agent=agent_name,
        )

        parts = selector.get_scope_parts()
        session_id = selector.scope_prefix
        scope_prefix = selector.scope_prefix

        all_scopes = {"/"}
        current_path = ""
        for part in parts:
            current_path += f"/{part}"
            all_scopes.add(current_path)

        accessible_scopes = list(all_scopes)
        accessible_scopes.sort(key=lambda x: len(x.split("/")))

        return SessionMetadata(
            session_id=session_id,
            scope_prefix=scope_prefix,
            accessible_scopes=accessible_scopes,
            selector=selector,
            isolation_level=scope_builder,
        )

    @staticmethod
    def build_session_meta(
        context: Any,
        target_builder: Any | None = None,
        extra_scopes: dict[str, Any] | None = None,
        custom_namespace: str | None = None,
    ) -> Any:
        """基于 RunContext 动态提取并生成 SessionMetadata"""
        from zhenxun.services.ai.context.memory.types import Isolation, SessionMetadata

        ns = custom_namespace or getattr(
            getattr(context, "session", None), "namespace", "global"
        )
        agent_name = getattr(getattr(context, "run", None), "agent_name", None)
        deps = getattr(context, "deps", None)

        if target_builder is None:
            target_builder = Isolation.AGENT_USER()

        selector = target_builder.resolve(
            deps=deps,
            prefix="",
            default_namespace=ns,
            default_agent=agent_name,
        )

        all_scopes = {"/"}
        parts = selector.get_scope_parts()
        current_path = ""
        for part in parts:
            current_path += f"/{part}"
            all_scopes.add(current_path)

        scope_name_mapping = {}
        if extra_scopes:
            for name, builder in extra_scopes.items():
                sel = builder.resolve(
                    deps=deps, prefix="", default_namespace=ns, default_agent=agent_name
                )
                all_scopes.add(sel.scope_prefix)
                scope_name_mapping[sel.scope_prefix] = name

        accessible_scopes = list(all_scopes)
        accessible_scopes.sort(key=lambda x: len(x.split("/")))

        session_id = (
            getattr(context, "session_id", None)
            or getattr(getattr(context, "session", None), "session_id", None)
            or selector.scope_prefix
        )

        return SessionMetadata(
            session_id=session_id,
            selector=selector,
            scope_prefix=selector.scope_prefix,
            accessible_scopes=accessible_scopes,
            scope_name_mapping=scope_name_mapping,
            isolation_level=target_builder,
        )


class PermissionUtils:
    """运行时权限校验通用工具类"""

    @staticmethod
    async def check_superuser(context: Any) -> bool:
        """异步校验当前运行上下文中的用户是否为超级用户"""
        bot = context.get_bot()
        event = context.get_event()
        if bot and event:
            from nonebot.permission import SUPERUSER

            return await SUPERUSER(bot, event)
        return False

    @staticmethod
    async def check_admin_level(context: Any, min_level: int) -> bool:
        """异步校验当前用户在全局或当前群聊中的管理权限等级是否达到最低要求"""
        if await PermissionUtils.check_superuser(context):
            return True

        user_id = context.get_user_id()
        group_id = context.get_group_id()
        if not user_id:
            return False

        from zhenxun.services.cache.runtime_cache import LevelUserMemoryCache

        global_user, group_users = await LevelUserMemoryCache.get_levels(
            user_id, group_id
        )
        user_level = global_user.user_level if global_user else 0
        if group_id and group_users:
            user_level = max(user_level, group_users.user_level)

        return user_level >= min_level
