from collections.abc import Callable
from dataclasses import dataclass, field
import fnmatch
from typing import Any, cast
from typing_extensions import Self

from pydantic import BaseModel, Field

from zhenxun.services.ai.utils.logger import log_capability as logger
from zhenxun.services.ai.utils.utils import parse_routing_string
from zhenxun.utils.utils import infer_plugin_namespace

from .base import AbstractCapability
from .wrappers import DynamicCapability


class CapabilityQuery(BaseModel):
    """
    拦截器/能力组件的声明式查询对象。
    用于在 Agent 中精确或批量筛选加载特定命名空间、特定标签的能力。
    """

    name: str | list[str] | None = Field(default=None)
    """如果提供，则能力的名称必须等于该字符串或在列表中。支持 * / ? 通配符。"""
    tags: list[str] | None = Field(default=None)
    """如果提供，则能力必须包含这里列出的所有标签 (交集/AND匹配)。"""
    exclude_tags: list[str] | None = Field(default=None)
    """如果提供，则能力不能包含这里列出的任何标签 (排斥过滤)。"""
    namespace: str | None = Field(default=None)
    """限制搜索的插件命名空间。如果不指定，将自动推导为调用者所在的插件；
    'global' 将跨全插件搜索，'*' 代表所有插件。"""


CapabilitySource = (
    str | Callable | AbstractCapability | type[AbstractCapability] | CapabilityQuery
)
"""能力/拦截器来源

支持字符串别名/标签、普通函数、Capability类或实例，以及声明式 Query 对象。
"""


@dataclass
class CapabilityEntry:
    """能力组件元数据载体"""

    cls: type[AbstractCapability]
    """能力类"""
    name: str
    """能力名称"""
    namespace: str
    """能力所在的命名空间"""
    tags: list[str] = field(default_factory=list)
    """能力标签列表"""
    auto_apply: bool = False
    """是否自动挂载该能力"""


class CapabilityManager:
    """能力组件全局注册与发现中心 (单例)"""

    _instance: "CapabilityManager | None" = None
    _entries: list[CapabilityEntry]

    def __new__(cls) -> Self:
        """单例模式：获取或创建全局唯一的能力管理器实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._entries = []
        return cast(Self, cls._instance)

    def register(
        self,
        cls: type[AbstractCapability],
        name: str,
        namespace: str,
        tags: list[str],
        auto_apply: bool,
    ) -> None:
        """注册一个能力组件到管理器中"""
        self._entries.append(
            CapabilityEntry(
                cls=cls,
                name=name,
                namespace=namespace,
                tags=tags,
                auto_apply=auto_apply,
            )
        )
        tag_str = f" | Tags: {tags}" if tags else ""
        logger.debug(
            f"已注册 Capability: '{name}' -> Namespace: '{namespace}'{tag_str})"
        )

    def get_auto_apply_capabilities(self, namespace: str) -> list[AbstractCapability]:
        """获取指定命名空间及其它全局命名空间下自动挂载的能力实例"""
        instances = []
        for entry in self._entries:
            if entry.auto_apply and entry.namespace in ("global", namespace):
                try:
                    instances.append(entry.cls())
                except Exception as e:
                    logger.error(f"实例化自动装配能力 {entry.name} 失败: {e}")
        return instances

    def query_capabilities(
        self, query: CapabilityQuery, default_namespace: str
    ) -> list[AbstractCapability]:
        """根据声明式查询条件筛选并实例化匹配的能力组件"""
        matched = []
        ns = query.namespace or default_namespace

        for entry in self._entries:
            if ns != "*" and entry.namespace != ns:
                continue

            if query.name:
                names = [query.name] if isinstance(query.name, str) else query.name
                name_matched = False
                for pattern in names:
                    if fnmatch.fnmatch(entry.name, pattern):
                        name_matched = True
                        break
                if not name_matched:
                    continue

            if query.tags:
                if not all(tag in entry.tags for tag in query.tags):
                    continue

            if query.exclude_tags:
                if any(tag in entry.tags for tag in query.exclude_tags):
                    continue

            try:
                matched.append(entry.cls())
            except Exception as e:
                logger.error(f"实例化能力 {entry.name} 失败: {e}")

        return matched

    def resolve_capabilities(
        self, sources: list[Any], default_namespace: str
    ) -> list[AbstractCapability]:
        """解析多种类型的能力来源并实例化为能力组件列表"""
        resolved = []
        for source in sources:
            if isinstance(source, AbstractCapability):
                resolved.append(source)
            elif callable(source) and not isinstance(source, type):
                resolved.append(DynamicCapability(source))
            elif isinstance(source, CapabilityQuery):
                resolved.extend(self.query_capabilities(source, default_namespace))
            elif isinstance(source, str):
                s = cast(str, source)
                parsed_args = parse_routing_string(s, default_namespace)
                q = CapabilityQuery(**parsed_args)
                resolved.extend(self.query_capabilities(q, default_namespace))
            elif isinstance(source, type) and issubclass(source, AbstractCapability):
                try:
                    resolved.append(source())
                except Exception as e:
                    logger.error(f"实例化能力 {source.__name__} 失败: {e}")
            else:
                raise TypeError(f"不支持的 Capability 来源: {type(source)}")
        return resolved


capability_manager = CapabilityManager()


def capability(
    name: str | None = None,
    tags: list[str] | None = None,
    auto_apply: bool = False,
    namespace: str | None = None,
) -> Callable:
    """
    类装饰器：声明式地注册一个 Capability 到全局能力池中。
    允许第三方插件通过字符串别名或标签进行引用，彻底解耦模块依赖。

    参数:
        name: 能力的名称，如果为None则默认使用类名。
        tags: 能力的标签列表，用于分类或批量筛选。
        auto_apply: 是否自动应用挂载该能力。
        namespace: 能力的命名空间，如果为None则自动推导为调用者所在的插件。

    返回:
        Callable: 装饰器函数，用于包装 AbstractCapability 类。
    """

    def decorator(cls: type[AbstractCapability]):
        """装饰器内部函数，实现类注册"""
        final_name = name or cls.__name__
        final_tags = tags or []
        ns = (
            namespace
            if namespace is not None
            else infer_plugin_namespace(default="global")
        )
        capability_manager.register(
            cls, name=final_name, namespace=ns, tags=final_tags, auto_apply=auto_apply
        )
        return cls

    return decorator
