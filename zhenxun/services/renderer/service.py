import asyncio
from collections.abc import Awaitable, Callable
import hashlib
from pathlib import Path
from typing import Any, ClassVar

import aiofiles
from jinja2 import TemplateNotFound
from nonebot.utils import is_coroutine_callable
import ujson as json

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import UI_CACHE_PATH
from zhenxun.services.log import logger
from zhenxun.services.renderer.template import (
    ComponentRenderStrategy,
    JinjaTemplateEngine,
    TemplateFileRenderStrategy,
)
from zhenxun.services.renderer.theme import DependencyCollector, asset_registry
from zhenxun.services.renderer.types import (
    BaseScreenshotEngine,
    Renderable,
    RenderContext,
    RenderResult,
    RenderStrategy,
)
from zhenxun.utils.exception import RenderingError
from zhenxun.utils.log_sanitizer import sanitize_for_logging
from zhenxun.utils.pydantic_compat import _dump_pydantic_obj

from .engine import engine_manager
from .theme import ThemeManager


class RendererService:
    """
    图片渲染服务的统一门面。

    作为UI渲染的中心枢纽，负责编排和调用底层服务，提供统一的渲染接口。
    主要职责包括：
    - 管理和加载UI主题 (通过 ThemeManager)。
    - 使用Jinja2引擎将组件数据模型 (`Renderable`) 渲染为HTML。
    - 调用截图引擎 (ScreenshotEngine) 将HTML转换为图片。
    - 处理插件注册的模板、过滤器和全局函数。
    - (可选) 管理渲染结果的缓存。
    """

    _plugin_template_paths: ClassVar[dict[str, Path]] = {}

    def __init__(self):
        self._template_engine: JinjaTemplateEngine | None = None
        self._theme_manager: ThemeManager | None = None
        self._screenshot_engine: BaseScreenshotEngine | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._custom_filters: dict[str, Callable] = {}
        self._custom_globals: dict[str, Callable] = {}

        self.filter("dump_json")(self._pydantic_tojson_filter)
        self.global_function("inline_asset")(self._inline_asset_global)

    def register_template_namespace(self, namespace: str, path: Path):
        """
        为插件注册一个Jinja2模板命名空间。

        这允许插件在自己的目录中维护模板，并通过
        `{% include '@namespace/template.html' %}` 的方式引用它们，
        避免了与核心或其他插件的模板命名冲突。

        参数:
            namespace: 插件的唯一命名空间，建议使用插件模块名
            path: 包含该插件模板的目录路径

        异常:
            ValueError: 当提供的路径不是有效目录时抛出
        """
        if namespace in self._plugin_template_paths:
            logger.warning(f"模板命名空间 '{namespace}' 已被注册，将被覆盖。")
        if not path.is_dir():
            raise ValueError(f"提供的路径 '{path}' 不是一个有效的目录。")
        self._plugin_template_paths[namespace] = path

    def register_markdown_style(self, name: str, path: Path):
        """
        为 Markdown 渲染器注册一个具名样式 (委托给 AssetRegistry)。

        参数:
            name (str): 样式的唯一名称，例如 'cyberpunk'。
            path (Path): 指向该样式的CSS文件路径。
        """
        if not path.is_file():
            raise ValueError(f"提供的路径 '{path}' 不是一个有效的 CSS 文件。")
        asset_registry.register_markdown_style(name, path)

    def filter(self, name: str) -> Callable:
        """
        装饰器：注册一个自定义 Jinja2 过滤器。

        参数:
            name: 过滤器在模板中的调用名称。

        返回:
            Callable: 用于装饰过滤器函数的装饰器。
        """

        def decorator(func: Callable) -> Callable:
            if name in self._custom_filters:
                logger.warning(f"Jinja2 过滤器 '{name}' 已被注册，将被覆盖。")
            self._custom_filters[name] = func
            logger.debug(f"已注册自定义 Jinja2 过滤器: '{name}'")
            return func

        return decorator

    def global_function(self, name: str) -> Callable:
        """
        装饰器：注册一个自定义 Jinja2 全局函数。

        参数:
            name: 函数在模板中的调用名称。

        返回:
            Callable: 用于装饰全局函数的装饰器。
        """

        def decorator(func: Callable) -> Callable:
            if name in self._custom_globals:
                logger.warning(f"Jinja2 全局函数 '{name}' 已被注册，将被覆盖。")
            self._custom_globals[name] = func
            logger.debug(f"已注册自定义 Jinja2 全局函数: '{name}'")
            return func

        return decorator

    async def _inline_asset_global(self, namespaced_path: str) -> str:
        """
        一个Jinja2全局函数，用于读取并内联一个已注册命名空间下的资源文件内容。
        主要用于内联SVG，以解决浏览器的跨域安全问题。
        """
        if not self._template_engine or not self._template_engine.env.loader:
            return f"<!-- Error: Jinja env not ready for {namespaced_path} -->"
        try:
            source, _, _ = self._template_engine.env.loader.get_source(
                self._template_engine.env, namespaced_path
            )
            return source
        except TemplateNotFound:
            return f"<!-- Asset not found: {namespaced_path} -->"

    async def initialize(self):
        """
        延迟初始化方法，在 on_startup 钩子中调用。

        负责初始化截图引擎和主题管理器，确保在首次渲染前所有依赖都已准备就绪。
        使用锁来防止并发初始化。
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return

            try:
                hot_reload = Config.get_config("UI", "HOT_RELOAD", False)
                self._template_engine = JinjaTemplateEngine(
                    self._plugin_template_paths, auto_reload=hot_reload
                )

                self._template_engine.env.filters.update(self._custom_filters)
                self._template_engine.env.globals.update(self._custom_globals)

                self._theme_manager = ThemeManager()
                self._theme_manager.bind_template_engine(self._template_engine.env)

                self._template_engine.set_global(
                    "render", self._theme_manager._global_render_component
                )
                self._template_engine.set_global(
                    "asset", self._theme_manager.create_asset_loader()
                )
                self._template_engine.set_global(
                    "random_asset", self._theme_manager.create_random_asset_loader()
                )
                self._template_engine.set_global(
                    "resolve_template", self._theme_manager.resolve_component_template
                )
                self._template_engine.set_filter(
                    "md", self._theme_manager._markdown_filter
                )

                self._screenshot_engine = await engine_manager.get_engine()

                current_theme_name = Config.get_config("UI", "THEME", "default")
                await self._theme_manager.load_theme(current_theme_name)

                if self._theme_manager.current_theme:
                    self._template_engine.update_theme_loaders(
                        self._theme_manager.current_theme.assets_dir.parent
                    )

                self._template_engine.set_global(
                    "theme", self._theme_manager.current_theme_context
                )
                self._template_engine.set_global(
                    "default_theme_palette", self._theme_manager.current_default_palette
                )

                self._initialized = True
            except Exception as e:
                logger.error(
                    f"渲染服务初始化失败，UI功能将不可用: {e}", "RendererService"
                )

    async def _collect_dependencies_recursive(
        self, component: Renderable, context: "RenderContext"
    ):
        """
        递归遍历组件树，收集所有依赖项（CSS, JS, 额外CSS）并存入上下文。
        """
        await DependencyCollector.collect(component, context)

    async def _render_component(
        self,
        context: "RenderContext",
    ) -> RenderResult:
        """
        执行完整的组件渲染流程。
        包含缓存检查、组件生命周期调用、依赖收集、HTML生成、截图以及缓存写入。
        """
        return await self._apply_caching_layer(self._render_component_core, context)

    async def _apply_caching_layer(
        self,
        core_render_func: Callable[..., Awaitable[RenderResult]],
        context: "RenderContext",
    ) -> RenderResult:
        """
        一个高阶函数，为核心渲染逻辑提供缓存层。
        它负责处理缓存的读取和写入，而将实际的渲染工作委托给传入的函数。
        """
        cache_path = None
        component = context.component

        hot_reload = Config.get_config("UI", "HOT_RELOAD", False)
        if Config.get_config("UI", "CACHE") and context.use_cache and not hot_reload:
            try:
                template_name = str(
                    getattr(component, "template_path", None) or component.template_name
                )
                data_dict = component.get_render_data()
                resolved_data_dict = {}
                for key, value in data_dict.items():
                    if is_coroutine_callable(value):  # type: ignore
                        resolved_data_dict[key] = await value
                    else:
                        resolved_data_dict[key] = value
                data_str = json.dumps(resolved_data_dict, sort_keys=True)
                cache_key_str = f"{template_name}:{data_str}"
                cache_filename = (
                    f"{hashlib.sha256(cache_key_str.encode()).hexdigest()}.png"
                )
                cache_path = UI_CACHE_PATH / cache_filename

                if cache_path.exists():
                    logger.debug(f"UI缓存命中: {cache_path}")
                    async with aiofiles.open(cache_path, "rb") as f:
                        image_bytes = await f.read()
                    return RenderResult(
                        image_bytes=image_bytes, html_content="<!-- from cache -->"
                    )
                logger.debug(f"UI缓存未命中: {cache_key_str[:100]}...")
            except Exception as e:
                logger.warning(f"UI缓存读取失败: {e}", e=e)
                cache_path = None

        result = await core_render_func(context)

        if (
            Config.get_config("UI", "CACHE")
            and context.use_cache
            and not hot_reload
            and cache_path
            and result.image_bytes
        ):
            try:
                async with aiofiles.open(cache_path, "wb") as f:
                    await f.write(result.image_bytes)
                logger.debug(f"UI缓存写入成功: {cache_path}")
            except Exception as e:
                logger.warning(f"UI缓存写入失败: {e}", e=e)

        return result

    def _select_strategy(self, component: Renderable) -> RenderStrategy:
        """
        根据组件特性选择合适的渲染策略。
        """
        if getattr(component, "_is_standalone_template", False):
            return TemplateFileRenderStrategy()

        template_path = getattr(component, "template_path", None)
        if isinstance(template_path, Path) and template_path.is_absolute():
            return TemplateFileRenderStrategy()

        return ComponentRenderStrategy()

    async def _render_component_core(self, context: "RenderContext") -> RenderResult:
        """
        不含缓存处理的核心渲染逻辑，负责调度具体的渲染策略。
        """
        try:
            if not self._initialized:
                await self.initialize()
            if not self._initialized or not context.screenshot_engine:
                raise RenderingError(
                    "渲染服务未正确初始化(可能缺少资源文件)，无法渲染组件。"
                )

            strategy = self._select_strategy(context.component)
            return await strategy.render(context)

        except RenderingError:
            raise
        except Exception as e:
            logger.error(
                f"渲染组件 '{context.component.__class__.__name__}' 时发生错误",
                "RendererService",
                e=e,
            )
            raise RenderingError(
                f"渲染组件 '{context.component.__class__.__name__}' 失败"
            ) from e

    async def render(
        self, component: Renderable, use_cache: bool = False, **render_options
    ) -> bytes:
        """
        将组件渲染为图片字节数据。

        参数:
            component: 需要渲染的 Renderable 组件实例
            use_cache: 是否启用渲染缓存，默认为 False
            **render_options: 传递给底层截图引擎的额外参数，如 `viewport` (字典), `device_scale_factor` 等

        返回:
            bytes: 渲染后的PNG图片二进制数据

        异常:
            RenderingError: 当渲染流程中任何步骤（初始化、资源缺失、截图失败）发生错误时抛出
        """  # noqa: E501
        if not self._initialized:
            await self.initialize()
        if (
            not self._initialized
            or not self._theme_manager
            or not self._screenshot_engine
            or not self._template_engine
        ):
            raise RenderingError(
                "渲染服务未正确初始化(可能缺少资源文件)，无法生成图片。"
            )

        context = RenderContext(
            renderer=self,
            theme_manager=self._theme_manager,
            template_engine=self._template_engine,
            screenshot_engine=self._screenshot_engine,
            component=component,
            use_cache=use_cache,
            render_options=render_options,
        )
        result = await self._render_component(context)
        if Config.get_config("UI", "DEBUG_MODE") and result.html_content:
            sanitized_html = sanitize_for_logging(
                result.html_content, context="ui_html"
            )
            logger.info(
                f"--- [UI DEBUG] HTML for {component.__class__.__name__} ---\n"
                f"{sanitized_html}\n"
                f"--- [UI DEBUG] End of HTML ---"
            )
        if result.image_bytes is None:
            raise RenderingError("渲染成功但未能生成图片字节数据。")
        return result.image_bytes

    async def render_to_html(
        self, component: Renderable, frameless: bool = False
    ) -> str:
        """
        调试方法：只执行到HTML生成步骤，不进行截图。

        参数:
            component: 一个 `Renderable` 实例。
            frameless: 是否以无边框模式渲染（只渲染HTML片段）。

        返回:
            str: 最终渲染出的完整HTML字符串。
        """
        if not self._initialized:
            await self.initialize()
        if (
            not self._initialized
            or not self._theme_manager
            or not self._template_engine
            or not self._screenshot_engine
        ):
            raise RenderingError("渲染服务未正确初始化(可能缺少资源文件)。")

        context = RenderContext(
            renderer=self,
            theme_manager=self._theme_manager,
            template_engine=self._template_engine,
            screenshot_engine=self._screenshot_engine,
            component=component,
            use_cache=False,
            render_options={"frameless": frameless},
        )
        await component.prepare()
        await DependencyCollector.collect(component, context)

        resolved_template_name = await self._theme_manager.resolve_component_template(
            component, context
        )
        theme_css_template = self._template_engine.env.get_template("theme.css.jinja")
        theme_css_content = await theme_css_template.render_async(
            theme=self._theme_manager.current_theme_context
        )

        return await self._template_engine.render_component_to_html(
            component,
            resolved_template_name,
            self._theme_manager.current_theme_context,
            theme_css_content,
            context.collected_inline_css,
            list(context.collected_scripts),
            list(context.collected_asset_styles),
            frameless=frameless,
        )

    async def reload_theme(self) -> str:
        """
        重新加载当前主题的配置和样式，并清除缓存的Jinja环境。
        这在开发主题时非常有用，可以热重载主题更改。

        返回:
            str: 已成功加载的主题名称。
        """
        if not self._initialized:
            await self.initialize()
        if not self._initialized or not self._theme_manager:
            raise RenderingError(
                "渲染服务未正确初始化(可能缺少资源文件)，无法重新加载主题。"
            )

        if self._theme_manager.manifest_registry:
            self._theme_manager.manifest_registry.clear_cache()
            logger.debug("已清除UI清单缓存 (manifest cache)。")
        current_theme_name = Config.get_config("UI", "THEME", "default")
        await self._theme_manager.load_theme(current_theme_name)

        if self._theme_manager.current_theme and self._template_engine:
            self._template_engine.update_theme_loaders(
                self._theme_manager.current_theme.assets_dir.parent
            )
        if self._template_engine and self._template_engine.env.cache:
            self._template_engine.env.cache.clear()

        logger.info(f"主题 '{current_theme_name}' 已成功重载。")
        return current_theme_name

    def list_available_themes(self) -> list[str]:
        """获取所有可用主题的列表。"""
        if not self._initialized or not self._theme_manager:
            raise RuntimeError("ThemeManager尚未初始化。")
        return self._theme_manager.list_available_themes()

    async def switch_theme(self, theme_name: str) -> str:
        """
        切换UI主题，加载新主题并持久化配置。

        返回:
            str: 已成功切换到的主题名称。
        """
        if not self._initialized or not self._theme_manager:
            await self.initialize()
        assert self._theme_manager is not None

        available_themes = self._theme_manager.list_available_themes()
        if theme_name not in available_themes:
            raise FileNotFoundError(
                f"主题 '{theme_name}' 不存在。可用主题: {', '.join(available_themes)}"
            )

        await self._theme_manager.load_theme(theme_name)

        if self._theme_manager.current_theme and self._template_engine:
            self._template_engine.update_theme_loaders(
                self._theme_manager.current_theme.assets_dir.parent
            )
        if self._template_engine and self._template_engine.env.cache:
            self._template_engine.env.cache.clear()

        Config.set_config("UI", "THEME", theme_name, auto_save=True)
        logger.info(f"UI主题已切换为: {theme_name}")
        return theme_name

    @staticmethod
    def _pydantic_tojson_filter(obj: Any) -> str:
        """一个能够递归处理Pydantic模型及其集合的 tojson 过滤器"""
        dumped_obj = _dump_pydantic_obj(obj)
        return json.dumps(dumped_obj, ensure_ascii=False)
