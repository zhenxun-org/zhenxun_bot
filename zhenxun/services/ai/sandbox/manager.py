import asyncio
from typing import Any, cast

import nonebot

from zhenxun.services.ai.config import get_llm_config
from zhenxun.services.ai.sandbox.models import SandboxBlueprint
from zhenxun.services.log import logger
from zhenxun.utils.lifespan import LifespanManager

from .drivers.base import BaseSandboxClient, BaseSandboxSession
from .registry import SandboxRegistry

_startup_tasks = set()


class SandboxManager:
    """
    沙箱底层环境的全局调度中心。
    负责读取用户配置，并动态分发给对应的安全 Driver 执行。
    """

    def __init__(self):
        """初始化沙箱环境管理器，配置活跃会话、会话锁及生存期管理器"""
        self._active_sessions: dict[str, BaseSandboxSession] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self.lifespan_manager = LifespanManager()

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        """获取或创建指定会话的 asyncio 互斥锁，确保并发操作的安全性"""
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    def _get_client(
        self,
        blueprint: SandboxBlueprint,
    ) -> BaseSandboxClient:
        """根据全局配置和蓝图参数决定并获取具体的沙箱驱动客户端实例"""
        global_type = get_llm_config().sandbox.sandbox_type

        effective_type = (
            blueprint.sandbox_type
            if blueprint.sandbox_type and blueprint.sandbox_type != "auto"
            else global_type
        )

        clients = SandboxRegistry.get_all_clients()

        if effective_type not in clients:
            raise RuntimeError(f"未找到指定的沙箱客户端: {effective_type}")

        return cast(BaseSandboxClient, clients[effective_type]())

    async def get_or_create_session(
        self,
        session_id: str,
        blueprint: SandboxBlueprint | None = None,
    ) -> BaseSandboxSession:
        """根据 Session ID 获取或创建持久化沙箱环境"""

        if not get_llm_config().sandbox.enable_sandbox:
            from zhenxun.services.ai.core.exceptions import SandboxFatalError

            raise SandboxFatalError(
                "全局沙箱功能已关闭(enable_sandbox=False)，无法创建运行环境。"
            )

        async with self._get_lock(session_id):
            if not blueprint:
                blueprint = SandboxBlueprint()

            if blueprint.setup_steps:
                blueprint.enable_network = True

            cleanup_timeout = get_llm_config().sandbox.cleanup_timeout
            await self.lifespan_manager.register(
                session_id,
                ttl=float(cleanup_timeout),
                cleanup_callback=self.close_session,
            )

            if session_id in self._active_sessions:
                session = self._active_sessions[session_id]
                is_alive = await session.is_alive()
                if not is_alive:
                    logger.warning(
                        f"⚠️ Session '{session_id}' 容器已死亡，重建中。",
                        command="SandboxManager",
                    )
                    await self.release_resource(session_id)
                else:
                    session.touch()
                    if blueprint.setup_steps:
                        await session.install_dependencies(blueprint)
                    await session.apply_blueprint(blueprint)
                    for p in blueprint.required_extensions:
                        await session.mount_extension(p)
                    return session

            logger.info(
                f"为 Session '{session_id}' 创建沙箱环境...", command="SandboxManager"
            )
            client = self._get_client(blueprint)
            try:
                session = await client.create(session_id, blueprint)

                await session.apply_blueprint(blueprint)

                if blueprint.setup_steps:
                    await session.install_dependencies(blueprint)

                for p in blueprint.required_extensions:
                    await session.mount_extension(p)

                self._active_sessions[session_id] = session
                return session
            except Exception as e:
                import traceback

                from zhenxun.services.ai.core.exceptions import SandboxFatalError

                err_msg = str(e) or type(e).__name__
                logger.error(
                    f"创建 Session 失败: {err_msg}\n{traceback.format_exc()}",
                    command="SandboxManager",
                )
                if not isinstance(e, SandboxFatalError):
                    raise SandboxFatalError(f"沙箱初始化异常: {err_msg}") from e
                raise e

    async def setup_workspace_environment(
        self, session_id: str, workspace_dir: str
    ) -> bool:
        """统一扫描指定工作区，通过 Provisioner 体系完成环境装配"""
        if session_id not in self._active_sessions:
            logger.warning(
                f"找不到活跃的 Session '{session_id}'，无法配置环境。",
                command="SandboxManager",
            )
            return False

        session = self._active_sessions[session_id]

        from zhenxun.services.ai.sandbox.environments import ProvisionerRegistry

        for prov in ProvisionerRegistry.get_all().values():
            await prov.scan_and_setup_workspace(cast(Any, session), workspace_dir)
        return True

    async def release_resource(self, resource_id: str):
        """释放指定会话的沙箱资源并销毁底层物理容器"""
        if resource_id in self._active_sessions:
            session = self._active_sessions.pop(resource_id)
            try:
                client_cls = SandboxRegistry.get_client_cls(session.state.sandbox_type)
                client = client_cls()
                await client.delete(cast(Any, session))
            except Exception as e:
                logger.error(
                    f"销毁沙箱环境失败 (Session: {resource_id}): {e}",
                    command="SandboxManager",
                )

    async def close_session(self, session_id: str) -> None:
        """从生存期管理器中注销并销毁指定会话的沙箱环境"""
        await self.lifespan_manager.unregister(session_id)
        await self.release_resource(session_id)

    async def shutdown_all(self) -> None:
        """关闭所有当前活跃的沙箱环境并停止生存期监测"""
        keys = list(self._active_sessions.keys())
        for sid in keys:
            await self.close_session(sid)
        await self.lifespan_manager.stop()


sandbox_manager = SandboxManager()


driver = nonebot.get_driver()


@driver.on_startup
async def _startup_sandboxes():
    """异步初始化沙箱，触发后台自动清理孤儿容器"""
    if not get_llm_config().sandbox.enable_sandbox:
        return
    clients = SandboxRegistry.get_all_clients()
    if "docker" in clients:
        from .drivers.docker import DockerSandboxClient

        async def _delayed_silent_cleanup():
            await asyncio.sleep(15)
            try:
                await DockerSandboxClient.silent_prune_orphans()
            except Exception:
                pass

        task = asyncio.create_task(_delayed_silent_cleanup())
        _startup_tasks.add(task)
        task.add_done_callback(_startup_tasks.discard)


@driver.on_shutdown
async def _shutdown_sandboxes():
    """在系统关闭时优雅关闭所有沙箱并清理 Docker 环境"""
    if not get_llm_config().sandbox.enable_sandbox:
        return
    await sandbox_manager.shutdown_all()
    clients = SandboxRegistry.get_all_clients()
    if "docker" in clients and getattr(clients["docker"], "_engine_available", False):
        from .drivers.docker import DockerSandboxClient

        await DockerSandboxClient.close_env()
