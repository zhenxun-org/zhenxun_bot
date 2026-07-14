from collections.abc import Callable
import re
from typing import Any, Generic, TypeVar
from typing_extensions import Self

from pydantic import BaseModel, Field

from zhenxun.utils.utils import infer_plugin_namespace


class ScopeSelector(BaseModel):
    """领域驱动：统一的作用域与实体资源选择器"""

    base_prefix: str | None = None
    """基础路径前缀。"""
    session_id: str | None = None
    """特定的会话 ID，如指定则绕过前缀拼接，直接作为统一路径。"""
    platform: str | None = None
    """目标平台标识（如 'qq'）。"""
    group_id: str | None = None
    """目标群组 ID。"""
    user_id: str | None = None
    """目标用户 ID。"""
    namespace: str | None = None
    """插件命名空间标识。"""
    agent_name: str | None = None
    """具体智能体标识。"""
    bot_id: str | None = None
    """触发环境的 Bot ID。"""
    custom_dimensions: dict[str, str] = Field(default_factory=dict)
    """动态的自定义隔离维度字典。"""

    def get_scope_parts(self) -> list[str]:
        """获取标准化的路径分段"""
        parts = []
        if self.base_prefix:
            clean = self.base_prefix.strip("/")
            if clean:
                parts.append(clean)
        if self.platform:
            parts.append(f"p_{self.platform}")
        if self.bot_id:
            parts.append(f"b_{self.bot_id}")
        if self.group_id:
            parts.append(f"g_{self.group_id}")
        if self.user_id:
            parts.append(f"u_{self.user_id}")
        if self.namespace:
            parts.append(f"ns_{self.namespace}")
        if self.agent_name:
            parts.append(f"ag_{self.agent_name}")
        for k, v in self.custom_dimensions.items():
            parts.append(f"{k}_{v}")
        return parts

    @property
    def scope_prefix(self) -> str:
        """统一的路径生成逻辑"""
        if self.session_id:
            return self.session_id
        parts = self.get_scope_parts()
        return "/" + "/".join(parts) if parts else "/"


T_Builder = TypeVar("T_Builder", bound="BaseScopeBuilder")


class BaseScopeBuilder(Generic[T_Builder]):
    """
    泛型化作用域构建器基类 (Fluent Builder)。
    为继承 of 子类提供极简的链式调用 API，用于快速指定平台、群组、用户等。
    """

    def __init__(self):
        self._selector = ScopeSelector()

    def bot(self, b: str) -> Self:
        """指定目标 Bot ID"""
        self._selector.bot_id = b
        return self

    def platform(self, p: str) -> Self:
        """指定目标平台标识 (如 'qq')"""
        self._selector.platform = p
        return self

    def group(self, g: str) -> Self:
        """指定目标群组 ID"""
        self._selector.group_id = g
        return self

    def user(self, u: str) -> Self:
        """指定目标用户 ID"""
        self._selector.user_id = u
        return self

    def namespace(self, ns: str) -> Self:
        """指定插件命名空间 (如 'rpg_game')"""
        self._selector.namespace = ns
        return self

    def agent(self, a: str) -> Self:
        """指定具体的 Agent 智能体名称"""
        self._selector.agent_name = a
        return self

    def session(self, sid: str) -> Self:
        """直接指定完整的 Session ID 绕过前缀拼接"""
        self._selector.session_id = sid
        return self

    def current(self, bot: Any = None, event: Any = None) -> Self:
        """自动提取当前触发上下文的特征，匹配当前用户/群组"""
        from zhenxun.services.ai.run.context import NoneBotDeps
        from zhenxun.services.ai.utils.runtime import ContextUtils

        deps = (
            NoneBotDeps(bot=bot, event=event)
            if bot and event
            else NoneBotDeps.get_current()
        )
        if deps:
            self._selector.platform = ContextUtils.extract_platform(deps)
            bot_inst = getattr(deps, "bot", None)
            if bot_inst and hasattr(bot_inst, "self_id"):
                self._selector.bot_id = str(bot_inst.self_id)
            self._selector.group_id = ContextUtils.extract_group_id(deps)
            self._selector.user_id = ContextUtils.extract_user_id(deps)
        return self


def normalize_scope_path(path: str) -> str:
    """标准化作用域路径，消除多余的斜杠并确保以 / 开头"""
    if not path or path == "/":
        return "/"
    path = re.sub(r"/+", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/")
    return path


class ScopeBuilder:
    """流式作用域声明构建器，用于在顶层配置并在底层延迟提取具体的维度值"""

    def __init__(self):
        """初始化作用域声明构建器。"""
        self._dims: set[str] = set()
        self._customs: dict[str, Callable[[Any], str | None]] = {}

    def bot(self) -> Self:
        """声明隔离维度包含 Bot ID。"""
        self._dims.add("bot")
        return self

    def platform(self) -> Self:
        """声明隔离维度包含平台类型。"""
        self._dims.add("platform")
        return self

    def group(self) -> Self:
        """声明隔离维度包含群组 ID。"""
        self._dims.add("group")
        return self

    def user(self) -> Self:
        """声明隔离维度包含用户 ID。"""
        self._dims.add("user")
        return self

    def namespace(self) -> Self:
        """声明隔离维度包含插件命名空间。"""
        self._dims.add("namespace")
        return self

    def agent(self) -> Self:
        """声明隔离维度包含智能体名称。"""
        self._dims.add("agent")
        return self

    def custom(
        self, key: str, value_extractor: str | Callable[[Any], str | None]
    ) -> Self:
        """声明自定义的隔离维度及值提取器。"""
        if isinstance(value_extractor, str):
            self._customs[key] = lambda _: value_extractor
        else:
            self._customs[key] = value_extractor
        return self

    def resolve(
        self,
        deps: Any,
        prefix: str = "",
        default_namespace: str | None = None,
        default_agent: str | None = None,
    ) -> ScopeSelector:
        """解析当前上下文依赖并生成作用域选择器实例。"""
        from zhenxun.services.ai.utils.runtime import ContextUtils

        selector = ScopeSelector(base_prefix=prefix)
        if "platform" in self._dims:
            selector.platform = ContextUtils.extract_platform(deps)
        if "bot" in self._dims:
            bot_inst = getattr(deps, "bot", None)
            if bot_inst and hasattr(bot_inst, "self_id"):
                selector.bot_id = str(bot_inst.self_id)
        if "group" in self._dims:
            selector.group_id = ContextUtils.extract_group_id(deps)
        if "user" in self._dims:
            selector.user_id = ContextUtils.extract_user_id(deps)
        if "namespace" in self._dims:
            selector.namespace = (
                default_namespace
                or getattr(deps, "namespace", None)
                or infer_plugin_namespace()
            )
        if "agent" in self._dims:
            selector.agent_name = default_agent

        for k, extractor in self._customs.items():
            val = extractor(deps)
            if val is not None:
                selector.custom_dimensions[k] = str(val)

        return selector
