from abc import ABC, abstractmethod
from collections.abc import Callable

import nonebot
from nonebot.utils import is_coroutine_callable
from pydantic import BaseModel

from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

driver = nonebot.get_driver()


class PluginInit(ABC):
    """
    插件安装与卸载模块
    """

    def __init_subclass__(cls, **kwargs):
        module_path = cls.__module__
        install_func = getattr(cls, "install", None)
        remove_func = getattr(cls, "remove", None)
        if install_func or remove_func:
            PluginInitManager.plugins[module_path] = PluginInitData(
                module_path=module_path,
                install=install_func,
                remove=remove_func,
                class_=cls,
            )

    @abstractmethod
    async def install(self):
        raise NotImplementedError

    @abstractmethod
    async def remove(self):
        raise NotImplementedError


class PluginInitData(BaseModel):
    module_path: str
    """模块名"""
    install: Callable | None
    """安装方法"""
    remove: Callable | None
    """卸载方法"""
    class_: type[PluginInit]
    """类"""


class PluginInitManager:
    plugins: dict[str, PluginInitData] = {}  # noqa: RUF012

    @classmethod
    async def install_all(cls):
        """运行所有插件安装方法"""
        if cls.plugins:
            for module_path, model in cls.plugins.items():
                if model.install:
                    class_ = model.class_()
                    try:
                        logger.debug(f"开始执行: {module_path}:install 方法")
                        if is_coroutine_callable(class_.install):
                            await class_.install()
                        else:
                            class_.install()  # type: ignore
                            logger.debug(f"执行: {module_path}:install 完成")
                    except Exception as e:
                        logger.error(f"执行: {module_path}:install 失败", e=e)

    @classmethod
    async def install(cls, module_path: str):
        """运行指定插件安装方法"""
        if model := cls.plugins.get(module_path):
            if model.install:
                class_ = model.class_()
                try:
                    logger.debug(f"开始执行: {module_path}:install 方法")
                    if is_coroutine_callable(class_.install):
                        await class_.install()
                    else:
                        class_.install()  # type: ignore
                        logger.debug(f"执行: {module_path}:install 完成")
                except Exception as e:
                    logger.error(f"执行: {module_path}:install 失败", e=e)

    @classmethod
    async def remove(cls, module_path: str):
        """运行指定插件安装方法"""
        if model := cls.plugins.get(module_path):
            if model.remove:
                class_ = model.class_()
                try:
                    logger.debug(f"开始执行: {module_path}:remove 方法")
                    if is_coroutine_callable(class_.remove):
                        await class_.remove()
                    else:
                        class_.remove()  # type: ignore
                        logger.debug(f"执行: {module_path}:remove 完成")
                except Exception as e:
                    logger.error(f"执行: {module_path}:remove 失败", e=e)


@PriorityLifecycle.on_startup(priority=5)
async def _():
    await PluginInitManager.install_all()
