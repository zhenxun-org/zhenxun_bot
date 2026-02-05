from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import inspect
from pathlib import Path
import random
from typing import TYPE_CHECKING, Any, ClassVar

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    TemplateNotFound,
    pass_context,
)
import markdown
from markupsafe import Markup
import ujson as json

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import THEMES_PATH
from zhenxun.services.log import logger
from zhenxun.services.renderer.types import Renderable, TemplateManifest, Theme
from zhenxun.utils.pydantic_compat import model_validate

if TYPE_CHECKING:
    from .types import RenderContext


def deep_merge_dict(base: dict, new: dict) -> dict:
    """递归合并字典"""
    result = base.copy()
    for key, value in new.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


class ManifestRegistry:
    """负责加载、缓存和合并组件的 manifest.json 文件。"""

    def __init__(self, jinja_env: Environment):
        self.jinja_env = jinja_env
        self._manifest_cache: dict[str, TemplateManifest] = {}
        self._lock = asyncio.Lock()

    def clear_cache(self):
        self._manifest_cache.clear()

    async def get_manifest(
        self, component_path: str, skin: str | None = None
    ) -> TemplateManifest | None:
        hot_reload = Config.get_config("UI", "HOT_RELOAD", False)
        cache_key = f"{component_path}:{skin or 'base'}"
        if not hot_reload and cache_key in self._manifest_cache:
            return self._manifest_cache[cache_key]
        async with self._lock:
            if not hot_reload and cache_key in self._manifest_cache:
                return self._manifest_cache[cache_key]
            manifest_dict = await self._load_and_merge(component_path, skin)
            if manifest_dict:
                try:
                    manifest_obj = model_validate(TemplateManifest, manifest_dict)
                    if not hot_reload:
                        self._manifest_cache[cache_key] = manifest_obj
                    return manifest_obj
                except Exception as e:
                    logger.error(f"清单文件校验失败 [{cache_key}]: {e}")
                    return None
            return None

    async def _load_and_merge(
        self, component_path: str, skin: str | None
    ) -> dict[str, Any] | None:
        base_manifest = await self._load_single(component_path)
        if skin:
            skin_path = f"{component_path}/skins/{skin}"
            skin_manifest = await self._load_single(skin_path)
            if skin_manifest:
                if base_manifest:
                    return deep_merge_dict(base_manifest, skin_manifest)
                return skin_manifest
        return base_manifest

    async def _load_single(self, path_str: str) -> dict[str, Any] | None:
        normalized_path = path_str.replace("\\", "/")
        manifest_path = f"{normalized_path}/manifest.json"
        if not self.jinja_env.loader:
            return None
        try:
            source, _, _ = self.jinja_env.loader.get_source(
                self.jinja_env, manifest_path
            )
            return json.loads(source)
        except (TemplateNotFound, json.JSONDecodeError):
            return None


class AssetRegistry:
    """一个独立的、用于存储由插件动态注册的资源的单例服务。"""

    _markdown_styles: ClassVar[dict[str, Path]] = {}

    def register_markdown_style(self, name: str, path: Path):
        if name in self._markdown_styles:
            logger.warning(f"Markdown 样式 '{name}' 已被注册，将被覆盖。")
        self._markdown_styles[name] = path

    def resolve_markdown_style(self, name: str) -> Path | None:
        return self._markdown_styles.get(name)


asset_registry = AssetRegistry()


@dataclass
class AssetRequest:
    asset_path: str
    template_name: str
    theme_manager: "ThemeManager"
    is_dir: bool = False


@dataclass
class ComponentDependency:
    """组件静态依赖缓存容器"""

    inline_css: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    asset_styles: list[str] = field(default_factory=list)


class IAssetResolver(ABC):
    @abstractmethod
    def resolve(self, request: AssetRequest) -> Path | None:
        pass


class NamespaceResolver(IAssetResolver):
    def resolve(self, request: AssetRequest) -> Path | None:
        if (
            not request.asset_path.startswith("@")
            or "/" not in request.asset_path
            or not request.theme_manager.jinja_env
        ):
            return None
        try:
            namespace, rel_path = request.asset_path.split("/", 1)
            loader = request.theme_manager.jinja_env.loader
            if (
                isinstance(loader, ChoiceLoader)
                and loader.loaders
                and isinstance(loader.loaders[0], PrefixLoader)
            ):
                prefix_loader = loader.loaders[0]
                if namespace in prefix_loader.mapping:
                    loader_for_ns = prefix_loader.mapping[namespace]
                    if isinstance(loader_for_ns, FileSystemLoader):
                        base_path = Path(loader_for_ns.searchpath[0])
                        file_path = (base_path / rel_path).resolve()
                        if (request.is_dir and file_path.is_dir()) or (
                            not request.is_dir and file_path.is_file()
                        ):
                            logger.debug(
                                f"解析资源 '{request.asset_path}' -> "
                                f"找到 命名空间 '{namespace}' 资源: '{file_path}'"
                            )
                            return file_path
                        return None
        except Exception:
            pass
        return None


class ComponentContextResolver(IAssetResolver):
    def resolve(self, request: AssetRequest) -> Path | None:
        if not (
            request.asset_path.startswith("./") or request.asset_path.startswith("../")
        ):
            return None
        if (
            not request.theme_manager.current_theme
            or not request.theme_manager.jinja_env
            or not request.theme_manager.jinja_env.loader
        ):
            return None
        try:
            source_info = request.theme_manager.jinja_env.loader.get_source(
                request.theme_manager.jinja_env, request.template_name
            )
        except TemplateNotFound:
            return None
        if not source_info[1]:
            return None

        parent_template_abs_path = Path(source_info[1])
        component_logical_root = Path(request.template_name).parent
        current_theme_root = request.theme_manager.current_theme.assets_dir.parent
        default_theme_root = (
            request.theme_manager.current_theme.default_assets_dir.parent
        )
        asset_rel_clean = (
            request.asset_path[2:]
            if request.asset_path.startswith("./")
            else request.asset_path
        )

        if "/skins/" in parent_template_abs_path.as_posix():
            skin_asset = parent_template_abs_path.parent / "assets" / asset_rel_clean
            if (request.is_dir and skin_asset.is_dir()) or (
                not request.is_dir and skin_asset.is_file()
            ):
                theme_name = request.theme_manager.current_theme.name
                logger.debug(
                    f"解析资源 '{request.asset_path}' -> "
                    f"找到 '{theme_name}' 主题皮肤资源: '{skin_asset}'"
                )
                return skin_asset
        theme_comp_asset = (
            current_theme_root / component_logical_root / "assets" / asset_rel_clean
        )
        if (request.is_dir and theme_comp_asset.is_dir()) or (
            not request.is_dir and theme_comp_asset.is_file()
        ):
            theme_name = request.theme_manager.current_theme.name
            logger.debug(
                f"解析资源 '{request.asset_path}' -> "
                f"找到 '{theme_name}' 主题组件资源: '{theme_comp_asset}'"
            )
            return theme_comp_asset
        if request.theme_manager.current_theme.name != "default":
            default_comp_asset = (
                default_theme_root / component_logical_root / "assets" / asset_rel_clean
            )
            if (request.is_dir and default_comp_asset.is_dir()) or (
                not request.is_dir and default_comp_asset.is_file()
            ):
                logger.debug(
                    f"解析资源 '{request.asset_path}' -> "
                    f"找到 'default' 主题组件资源 (回退): '{default_comp_asset}'"
                )
                return default_comp_asset
        return None


class ThemeGlobalResolver(IAssetResolver):
    def resolve(self, request: AssetRequest) -> Path | None:
        if (
            request.asset_path.startswith(("@", "./", "../"))
            or not request.theme_manager.current_theme
        ):
            return None
        theme_asset = (
            request.theme_manager.current_theme.assets_dir / request.asset_path
        )
        if (request.is_dir and theme_asset.is_dir()) or (
            not request.is_dir and theme_asset.is_file()
        ):
            theme_name = request.theme_manager.current_theme.name
            logger.debug(
                f"解析资源 '{request.asset_path}' -> "
                f"找到 '{theme_name}' 主题全局资源: '{theme_asset}'"
            )
            return theme_asset
        if request.theme_manager.current_theme.name != "default":
            default_asset = (
                request.theme_manager.current_theme.default_assets_dir
                / request.asset_path
            )
            if (request.is_dir and default_asset.is_dir()) or (
                not request.is_dir and default_asset.is_file()
            ):
                logger.debug(
                    f"解析资源 '{request.asset_path}' -> "
                    f"找到 'default' 主题全局资源 (回退): '{default_asset}'"
                )
                return default_asset
        return None


class AssetResolutionService:
    def __init__(self, theme_manager: "ThemeManager"):
        self.theme_manager = theme_manager
        self.resolvers: list[IAssetResolver] = [
            NamespaceResolver(),
            ComponentContextResolver(),
            ThemeGlobalResolver(),
        ]

    def register_resolver(self, resolver: IAssetResolver, index: int = -1):
        self.resolvers.insert(index, resolver)

    def resolve_directory_path(
        self, asset_path: str, current_template_name: str
    ) -> Path | None:
        request = AssetRequest(
            asset_path=asset_path,
            template_name=current_template_name,
            theme_manager=self.theme_manager,
            is_dir=True,
        )
        for resolver in self.resolvers:
            if result_path := resolver.resolve(request):
                return result_path
        return None

    def resolve_asset_uri(self, asset_path: str, current_template_name: str) -> str:
        hot_reload = Config.get_config("UI", "HOT_RELOAD", False)
        cache_key = (asset_path, current_template_name)
        if not hot_reload and cache_key in self.theme_manager._asset_resolution_cache:
            return self.theme_manager._asset_resolution_cache[cache_key]
        request = AssetRequest(
            asset_path=asset_path,
            template_name=current_template_name,
            theme_manager=self.theme_manager,
        )
        for resolver in self.resolvers:
            if result_path := resolver.resolve(request):
                uri = result_path.absolute().as_uri()
                if not hot_reload:
                    self.theme_manager._asset_resolution_cache[cache_key] = uri
                return uri
        logger.warning(
            f"资源文件未找到: '{asset_path}' (在 '{current_template_name}' 中)"
        )
        return ""


class ThemeManager:
    def __init__(self):
        """
        主题管理器，负责UI主题的加载、解析和模板渲染。

        主要职责:
        - 加载和管理UI主题，包括 `palette.json` (调色板) 和 `theme.css.jinja`(主题样式)
        """
        self.current_theme: Theme | None = None
        self.jinja_env: Environment | None = None
        self.manifest_registry: ManifestRegistry | None = None
        self.asset_service = AssetResolutionService(self)
        self.current_theme_context: dict[str, Any] = {}
        self.current_default_palette: dict[str, Any] = {}

        self._asset_resolution_cache: dict[tuple[str, str], str] = {}
        self._global_template_cache: dict[str, str] = {}
        self._component_dependency_cache: dict[
            tuple[type, str, str | None], ComponentDependency
        ] = {}

    def bind_template_engine(self, env: Environment):
        """绑定模板引擎环境，用于Manifest加载和asset解析"""
        self.jinja_env = env
        self.manifest_registry = ManifestRegistry(self.jinja_env)

    def list_available_themes(self) -> list[str]:
        """扫描主题目录并返回所有可用的主题名称。"""
        if not THEMES_PATH.is_dir():
            return []
        return [d.name for d in THEMES_PATH.iterdir() if d.is_dir()]

    def create_asset_loader(self) -> Callable[..., str]:
        """
        创建一个闭包函数 (Jinja2中的 `asset()` 函数)，使用
        AssetResolutionService 进行路径解析。
        """

        @pass_context
        def asset_loader(ctx, asset_path: str) -> str:
            if not ctx.name:
                logger.warning("Jinja2 上下文缺少模板名称，无法进行资源解析。")
                return self.asset_service.resolve_asset_uri(
                    asset_path, "unknown_template"
                )
            parent_template_name = ctx.name
            return self.asset_service.resolve_asset_uri(
                asset_path, parent_template_name
            )

        return asset_loader

    def create_random_asset_loader(self) -> Callable[..., str]:
        """
        创建一个闭包函数 (Jinja2中的 `random_asset()` 函数)。
        用于从指定目录随机获取一个资源文件的 URI。
        """

        @pass_context
        def random_loader(ctx, asset_path: str, key: str | None = None) -> str:
            if not ctx.name:
                return ""
            return self.get_random_asset_uri(asset_path, ctx.name, key)

        return random_loader

    def get_random_asset_uri(
        self, path_pattern: str, current_template_name: str, key: str | None = None
    ) -> str:
        """解析目录并返回随机文件URI"""
        if not Config.get_config("UI", "ENABLE_RANDOM_DECORATION", True):
            return ""

        dir_path = self.asset_service.resolve_directory_path(
            path_pattern, current_template_name
        )
        if not dir_path or not dir_path.is_dir():
            return ""

        valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
        images = [
            f
            for f in dir_path.iterdir()
            if f.is_file() and f.suffix.lower() in valid_exts
        ]

        if not images:
            return ""

        return random.choice(images).absolute().as_uri()

    def _create_standalone_asset_loader(
        self, local_base_path: Path
    ) -> Callable[[str], str]:
        """为独立模板创建一个专用的 asset loader。"""

        def asset_loader(asset_path: str) -> str:
            full_path = local_base_path / asset_path
            if full_path.exists():
                return full_path.absolute().as_uri()
            return ""

        return asset_loader

    async def _global_render_component(self, component: Renderable | None) -> str:
        """
        一个全局的Jinja2函数，用于在模板内部渲染子组件
        它封装了查找模板、设置上下文和渲染的逻辑。
        """
        if not self.jinja_env:
            return ""

        if not component:
            return ""
        try:

            class MockContext:
                def __init__(self):
                    self.resolved_template_paths = {}
                    self.theme_manager = self

            mock_context = MockContext()
            template_path = await self.resolve_component_template(
                component,
                mock_context,  # type: ignore
            )
            template = self.jinja_env.get_template(template_path)

            template_context = {
                "data": component,
                "frameless": True,
            }
            render_data = component.get_render_data()
            template_context.update(render_data)

            return Markup(await template.render_async(**template_context))
        except Exception as e:
            logger.error(
                f"在全局 render 函数中渲染组件 '{component.__class__.__name__}' 失败",
                e=e,
            )
            return f"<!-- 组件渲染失败{component.__class__.__name__}: {e} -->"

    @staticmethod
    def _markdown_filter(text: str) -> str:
        """一个将 Markdown 文本转换为 HTML 的 Jinja2 过滤器。"""
        if not isinstance(text, str):
            return ""
        return markdown.markdown(
            text,
            extensions=[
                "pymdownx.tasklist",
                "tables",
                "fenced_code",
                "codehilite",
                "mdx_math",
                "pymdownx.tilde",
            ],
            extension_configs={"mdx_math": {"enable_dollar_delimiter": True}},
        )

    async def load_theme(self, theme_name: str = "default"):
        theme_dir = THEMES_PATH / theme_name
        if not theme_dir.is_dir():
            logger.error(f"主题 '{theme_name}' 不存在，将回退到默认主题。")
            if theme_name == "default":
                raise FileNotFoundError("默认主题 'default' 未找到！")
            theme_name = "default"
            theme_dir = THEMES_PATH / "default"

        self._asset_resolution_cache.clear()
        self._global_template_cache.clear()
        self._component_dependency_cache.clear()
        if self.manifest_registry:
            self.manifest_registry.clear_cache()

        default_palette_path = THEMES_PATH / "default" / "palette.json"
        default_palette = (
            json.loads(default_palette_path.read_text("utf-8"))
            if default_palette_path.exists()
            else {}
        )

        palette_path = theme_dir / "palette.json"
        target_palette = (
            json.loads(palette_path.read_text("utf-8")) if palette_path.exists() else {}
        )

        final_palette = deep_merge_dict(default_palette, target_palette)

        self.current_theme = Theme(
            name=theme_name,
            palette=final_palette,
            style_css="",
            assets_dir=theme_dir / "assets",
            default_assets_dir=THEMES_PATH / "default" / "assets",
        )
        theme_context_dict = {
            "name": theme_name,
            "palette": final_palette,
            "assets_dir": theme_dir / "assets",
            "default_assets_dir": THEMES_PATH / "default" / "assets",
        }
        self.current_theme_context = theme_context_dict
        self.current_default_palette = default_palette
        logger.info(f"主题管理器已加载主题: {theme_name}")

    async def resolve_component_template(
        self, component: Renderable, context: "RenderContext"
    ) -> str:
        """
        智能解析组件模板的路径，支持简单组件和带皮肤(variant)的复杂组件。

        查找顺序如下:
        1.  **带皮肤的组件**: 如果组件定义了 `variant`，则在
            `components/{component_name}/skins/{variant_name}/` 目录下查找入口文件。
        2.  **标准组件**: 在组件的根目录 `components/{component_name}/` 下查找入口文件。
        3.  **兼容模式**: (作为最终回退)直接查找名为`components/{component_name}.html`
            的文件

        入口文件名默认为 `main.html`，但可以被组件目录下的 `manifest.json` 文件中的
        `entrypoint` 字段覆盖。
        """
        from zhenxun.ui.registry import registry as component_registry

        instance_path = getattr(component, "template_path", None)

        registry_path = component_registry.get_template_for_class(type(component))

        class_path = getattr(component, "template_name", "")

        raw_path = instance_path or registry_path or class_path

        if not raw_path:
            raise ValueError(f"组件 {type(component).__name__} 未绑定任何模板路径。")

        hot_reload = Config.get_config("UI", "HOT_RELOAD", False)

        component_path_base = str(raw_path).replace("\\", "/")

        variant = getattr(component, "variant", None)
        cache_key = f"{component_path_base}::{variant or 'default'}"

        if not hot_reload and (
            cached_path := self._global_template_cache.get(cache_key)
        ):
            return cached_path

        if not hot_reload and (
            cached_path := context.resolved_template_paths.get(cache_key)
        ):
            logger.trace(f"模板路径缓存命中: '{cache_key}' -> '{cached_path}'")
            return cached_path

        if Path(component_path_base).suffix:
            try:
                if self.jinja_env:
                    self.jinja_env.get_template(component_path_base)
                logger.debug(f"解析到直接模板路径: '{component_path_base}'")
                return component_path_base
            except TemplateNotFound as e:
                logger.error(f"指定的模板文件路径不存在: '{component_path_base}'", e=e)
                raise e

        base_manifest = await self.get_template_manifest(component_path_base)

        skin_to_use = variant or (base_manifest.skin if base_manifest else None)

        final_manifest = await self.get_template_manifest(
            component_path_base, skin=skin_to_use
        )
        logger.debug(f"final_manifest: {final_manifest}")

        entrypoint_filename = (
            final_manifest.entrypoint
            if final_manifest and final_manifest.entrypoint
            else "main.html"
        )

        potential_paths = []

        if skin_to_use:
            potential_paths.append(
                f"{component_path_base}/skins/{skin_to_use}/{entrypoint_filename}"
            )

        potential_paths.append(f"{component_path_base}/{entrypoint_filename}")

        if entrypoint_filename == "main.html":
            potential_paths.append(f"{component_path_base}.html")

        for path in potential_paths:
            try:
                if self.jinja_env:
                    self.jinja_env.get_template(path)
                logger.debug(f"解析到模板路径: '{path}'")
                if not hot_reload:
                    context.resolved_template_paths[cache_key] = path
                    self._global_template_cache[cache_key] = path
                return path
            except TemplateNotFound:
                continue

        err_msg = (
            f"无法为组件 '{component_path_base}' 找到任何可用的模板。"
            f"检查路径: {potential_paths}"
        )
        logger.error(err_msg)
        raise TemplateNotFound(err_msg)

    async def get_template_manifest(
        self, component_path: str, skin: str | None = None
    ) -> Any | None:
        """
        查找并解析组件的 manifest.json 文件。

        参数:
            component_path: 组件路径
            skin: 皮肤名称(可选)

        返回:
            合并后的清单字典,如果不存在则返回 None
        """
        if not self.manifest_registry:
            return None
        return await self.manifest_registry.get_manifest(component_path, skin)

    async def resolve_markdown_style_path(
        self, style_name: str, context: "RenderContext"
    ) -> Path | None:
        """
        按照 注册 -> 主题约定 -> 默认约定 的顺序解析 Markdown 样式路径。
        """
        if cached_path := context.resolved_style_paths.get(style_name):
            logger.trace(f"Markdown样式路径缓存命中: '{style_name}'")
            return cached_path

        resolved_path: Path | None = None
        if registered_path := asset_registry.resolve_markdown_style(style_name):
            logger.debug(f"找到已注册的 Markdown 样式: '{style_name}'")
            resolved_path = registered_path

        elif self.current_theme:
            theme_style_path = (
                self.current_theme.assets_dir
                / "css"
                / "styles"
                / "markdown"
                / f"{style_name}.css"
            )
            if theme_style_path.exists():
                logger.debug(
                    f"在主题 '{self.current_theme.name}' 中找到"
                    f"Markdown 样式: '{style_name}'"
                )
                resolved_path = theme_style_path

            default_style_path = (
                self.current_theme.default_assets_dir
                / "css"
                / "styles"
                / "markdown"
                / f"{style_name}.css"
            )
            if not resolved_path and default_style_path.exists():
                logger.debug(f"在 'default' 主题中找到 Markdown 样式: '{style_name}'")
                resolved_path = default_style_path

        if resolved_path:
            context.resolved_style_paths[style_name] = resolved_path
        else:
            logger.warning(
                f"Markdown 样式 '{style_name}' 在注册表和主题目录中均未找到。"
            )

        return resolved_path


class DependencyCollector:
    """
    负责递归遍历组件树，收集 CSS/JS 依赖。
    """

    @classmethod
    async def collect(cls, component: Renderable, context: "RenderContext"):
        hot_reload = Config.get_config("UI", "HOT_RELOAD", False)
        component_id = id(component)
        if component_id in context.processed_components:
            return
        context.processed_components.add(component_id)

        component_path_base = str(
            getattr(component, "template_path", None) or component.template_name
        )
        variant = getattr(component, "variant", None)
        cache_key = (type(component), component_path_base, variant)

        cached_dep = None
        if not hot_reload:
            cached_dep = context.theme_manager._component_dependency_cache.get(
                cache_key
            )

        if cached_dep:
            context.collected_inline_css.extend(cached_dep.inline_css)
            context.collected_scripts.update(cached_dep.scripts)
            context.collected_asset_styles.update(cached_dep.asset_styles)
        else:
            new_dep = ComponentDependency()
            cached_css_results: list[str] = []
            manifest = await context.theme_manager.get_template_manifest(
                component_path_base, skin=variant
            )
            style_paths_to_load = []

            if manifest and manifest.styles:
                styles = (
                    manifest.styles
                    if isinstance(manifest.styles, list)
                    else [manifest.styles]
                )
                resolution_base_path = (
                    Path(component_path_base) / "skins" / variant
                    if variant
                    and await context.theme_manager.get_template_manifest(
                        component_path_base, skin=variant
                    )
                    else Path(component_path_base)
                )
                style_paths_to_load.extend(
                    str(resolution_base_path / style).replace("\\", "/")
                    for style in styles
                )
            else:
                base_template_path = (
                    await context.theme_manager.resolve_component_template(
                        component, context
                    )
                )
                style_paths_to_load.append(
                    str(Path(base_template_path).with_name("style.css")).replace(
                        "\\", "/"
                    )
                )
                if variant:
                    style_paths_to_load.append(
                        f"{component_path_base}/skins/{variant}/style.css"
                    )

            for css_template_path in style_paths_to_load:
                try:
                    css_template = context.template_engine.env.get_template(
                        css_template_path
                    )
                    css_content = await css_template.render_async(
                        theme=context.theme_manager.current_theme_context
                    )
                    cached_css_results.append(css_content)
                except Exception:
                    pass

            new_dep.inline_css = cached_css_results
            new_dep.scripts = component.get_required_scripts()
            new_dep.asset_styles = component.get_required_styles()

            if not hot_reload:
                context.theme_manager._component_dependency_cache[cache_key] = new_dep

            context.collected_inline_css.extend(cached_css_results)
            context.collected_scripts.update(new_dep.scripts)
            context.collected_asset_styles.update(new_dep.asset_styles)

        if hasattr(component, "get_extra_css"):
            res = component.get_extra_css(context)
            css_str = await res if inspect.isawaitable(res) else str(res)
            if css_str:
                context.collected_inline_css.append(css_str)

        for child in component.get_children():
            if child:
                await cls.collect(child, context)
