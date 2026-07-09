from typing import TYPE_CHECKING, ClassVar

from zhenxun.services.ai.utils.logger import log_sandbox as logger

if TYPE_CHECKING:
    from .addons.base import BaseSandboxExtension
    from .drivers.base import BaseSandboxClient


class SandboxRegistry:
    """沙箱组件与能力扩展注册中心"""

    _clients: ClassVar[dict[str, type["BaseSandboxClient"]]] = {}
    _extensions: ClassVar[dict[str, type["BaseSandboxExtension"]]] = {}

    @classmethod
    def register_client(cls, name: str, client_cls: type["BaseSandboxClient"]) -> None:
        """注册一个沙箱驱动客户端类"""
        if name in cls._clients:
            logger.warning(f"[SandboxRegistry] 覆盖已存在的沙箱客户端: {name}")
        cls._clients[name] = client_cls
        logger.debug(f"[SandboxRegistry] 成功注册沙箱客户端: {name}")

    @classmethod
    def get_client_cls(cls, name: str) -> type["BaseSandboxClient"]:
        """获取指定名称的沙箱驱动客户端类"""
        if name not in cls._clients:
            raise ValueError(f"未找到名为 '{name}' 的沙箱客户端。")
        return cls._clients[name]

    @classmethod
    def get_all_clients(cls) -> dict[str, type["BaseSandboxClient"]]:
        """获取所有已注册的沙箱驱动客户端类"""
        return cls._clients.copy()

    @classmethod
    def register_extension(cls, extension_cls: type["BaseSandboxExtension"]) -> None:
        """注册一个沙箱功能能力扩展类"""
        if (
            isinstance(getattr(extension_cls, "extension_name", None), property)
            and extension_cls.extension_name.fget
        ):
            name = extension_cls.extension_name.fget(None)
        else:
            name = getattr(extension_cls, "extension_name", str(extension_cls))
        cls._extensions[name] = extension_cls
        logger.debug(f"[SandboxRegistry] 成功注册沙箱扩展: {name}")

    @classmethod
    def get_extension_cls(cls, name: str) -> type["BaseSandboxExtension"] | None:
        """获取指定名称的沙箱能力扩展类"""
        return cls._extensions.get(name)
