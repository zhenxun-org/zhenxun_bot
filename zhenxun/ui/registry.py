from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, TypeVar

from zhenxun.services.log import logger
from zhenxun.services.renderer.types import Renderable

T = TypeVar("T", bound=Renderable)


@dataclass
class ComponentEntry:
    """组件注册条目，存储类与元数据的关联"""

    component_class: type[Renderable]
    default_template: str | None


class ComponentRegistry:
    """
    UI 组件注册中心。
    负责管理组件类的索引，并自动处理模板命名空间的注册。
    """

    _instance: ClassVar = None
    _registry: ClassVar[dict[str, ComponentEntry]] = {}
    _class_template_map: ClassVar[dict[type[Renderable], str]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(
        cls,
        name: str,
        namespace: str = "core",
        template: str | None = None,
        template_root: Path | None = None,
    ) -> Callable[[type[T]], type[T]]:
        """
        装饰器：注册一个 UI 组件。

        参数:
            name: 组件名称 (例如 'card')
            namespace: 命名空间 (例如 'core' 或插件名)
            template: (可选) 该组件默认绑定的模板路径 (例如 'components/card/main.html')
            template_root: (可选) 该组件对应的模板根目录。
                           如果提供，会自动将其注册到 RendererService。
        """

        def wrapper(component_cls: type[T]) -> type[T]:
            full_key = f"{namespace}:{name}"

            if full_key in cls._registry:
                logger.warning(f"UI组件 '{full_key}' 已被注册，将被覆盖。")

            cls._registry[full_key] = ComponentEntry(
                component_class=component_cls, default_template=template
            )
            if template:
                cls._class_template_map[component_cls] = template

            if template_root:
                if not template_root.exists():
                    logger.warning(
                        f"组件 '{full_key}' 提供的模板路径不存在: {template_root}"
                    )
                else:
                    from zhenxun.services import renderer_service

                    renderer_service.register_template_namespace(
                        namespace, template_root
                    )
                    logger.debug(
                        f"已自动注册组件模板空间: @{namespace} -> {template_root}"
                    )

            logger.trace(f"UI组件注册成功: {full_key} -> {component_cls.__name__}")
            return component_cls

        return wrapper

    @classmethod
    def get(cls, key: str) -> type[Renderable] | None:
        """根据 'namespace:name' 获取组件类。"""
        entry = cls._registry.get(key)
        return entry.component_class if entry else None

    @classmethod
    def get_template_for_class(cls, component_cls: type[Renderable]) -> str | None:
        """
        根据组件类查找注册时绑定的默认模板。
        支持子类继承查找 (可选，目前先做精确匹配)。
        """
        return cls._class_template_map.get(component_cls)

    @classmethod
    def create(cls, key: str, **kwargs) -> Renderable:
        """
        动态工厂方法。

        参数:
            key: 组件标识符 'namespace:name'
            **kwargs: 传递给组件模型的初始化参数
        """
        component_cls = cls.get(key)
        if not component_cls:
            if ":" not in key:
                return cls.create(f"core:{key}", **kwargs)
            raise ValueError(f"未找到 UI 组件: {key}")

        return component_cls(**kwargs)  # type: ignore


registry = ComponentRegistry()
component = registry.register
create = registry.create
