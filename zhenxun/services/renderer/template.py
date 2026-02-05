from collections.abc import Callable
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    select_autoescape,
)

from zhenxun.configs.path_config import THEMES_PATH
from zhenxun.services.log import logger
from zhenxun.services.renderer.theme import DependencyCollector
from zhenxun.services.renderer.types import (
    RESERVED_TEMPLATE_KEYS,
    Renderable,
    RenderResult,
    RenderStrategy,
)
from zhenxun.utils.exception import RenderingError

if TYPE_CHECKING:
    from .types import RenderContext


class RelativePathEnvironment(Environment):
    """
    一个自定义的 Jinja2 环境，重写了 join_path 方法以支持模板间的相对路径引用。
    """

    def join_path(self, template: str, parent: str) -> str:
        if template.startswith("./") or template.startswith("../"):
            path = os.path.normpath(os.path.join(os.path.dirname(parent), template))
            return path.replace(os.path.sep, "/")
        return super().join_path(template, parent)


class JinjaTemplateEngine:
    """
    负责 HTML 生成的核心引擎。
    """

    def __init__(
        self, plugin_template_paths: dict[str, Path], auto_reload: bool = False
    ):
        self._plugin_template_paths = plugin_template_paths
        self._auto_reload = auto_reload
        self.env = self._create_jinja_env()

    def _create_jinja_env(self) -> Environment:
        """创建并配置 Jinja2 渲染环境"""
        prefix_loader = PrefixLoader(
            {
                namespace: FileSystemLoader(str(path.absolute()))
                for namespace, path in self._plugin_template_paths.items()
            }
        )
        theme_loader = FileSystemLoader(str(THEMES_PATH / "default"))
        final_loader = ChoiceLoader([prefix_loader, theme_loader])

        env = RelativePathEnvironment(
            loader=final_loader,
            enable_async=True,
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
            auto_reload=self._auto_reload,
        )
        return env

    def update_theme_loaders(self, theme_dir: Path):
        """更新 Loader 以支持多主题"""
        if self.env.loader and isinstance(self.env.loader, ChoiceLoader):
            current_loaders = list(self.env.loader.loaders)
            if len(current_loaders) > 1 and isinstance(
                current_loaders[0], PrefixLoader
            ):
                prefix_loader = current_loaders[0]
                new_theme_loader = FileSystemLoader(
                    [str(theme_dir), str(THEMES_PATH / "default")]
                )
                self.env.loader.loaders = [prefix_loader, new_theme_loader]

    def set_global(self, key: str, value: Any):
        self.env.globals[key] = value

    def set_filter(self, key: str, func: Callable):
        self.env.filters[key] = func

    async def render_component_to_html(
        self,
        component: Renderable,
        template_name: str,
        theme_context: dict,
        theme_css_content: str,
        inline_css: list[str],
        scripts: list[str],
        styles: list[str],
        **kwargs,
    ) -> str:
        """
        将组件数据和模板结合，生成最终的 HTML 字符串。
        """
        logger.debug(
            f"正在渲染组件模板: '{template_name}'",
            "JinjaTemplateEngine",
        )
        template = self.env.get_template(template_name)
        data_dict = component.get_render_data()

        unpacked_data = {}
        for key, value in data_dict.items():
            if key in RESERVED_TEMPLATE_KEYS:
                logger.warning(
                    f"模板数据键 '{key}' 与渲染器保留关键字冲突，"
                    f"在模板 '{template_name}' 中请使用 'data.{key}' 访问。"
                )
            else:
                unpacked_data[key] = value

        template_context = {
            "data": component,
            "theme": theme_context,
            "frameless": True,
        }
        template_context.update(unpacked_data)
        template_context.update({k: v for k, v in kwargs.items() if k != "frameless"})

        html_fragment = await template.render_async(**template_context)

        if not kwargs.get("frameless", False):
            base_template = self.env.get_template("partials/_base.html")
            page_context = {
                "data": component,
                "theme_css": theme_css_content,
                "collected_inline_css": inline_css,
                "required_scripts": scripts,
                "collected_asset_styles": styles,
                "body_content": html_fragment,
            }
            return await base_template.render_async(**page_context)
        else:
            style_blocks: list[str] = []
            if theme_css_content:
                style_blocks.append(theme_css_content)
            if inline_css:
                style_blocks.extend(inline_css)

            if style_blocks:
                css_content = "\n".join(style_blocks)
                return f"<style>{css_content}</style>\n{html_fragment}"

            return html_fragment


class ComponentRenderStrategy(RenderStrategy):
    """标准组件渲染策略。"""

    async def render(self, context: "RenderContext") -> RenderResult:
        component = context.component
        await component.prepare()
        await DependencyCollector.collect(component, context)

        data_dict = component.get_render_data()
        _opts = data_dict.get("render_options")
        component_render_options = _opts if isinstance(_opts, dict) else {}

        component_template_identifier = str(
            getattr(component, "template_path", None) or component.template_name
        )
        variant = getattr(component, "variant", None)
        manifest_options = {}
        if manifest := await context.theme_manager.get_template_manifest(
            component_template_identifier, skin=variant
        ):
            manifest_options = manifest.render_options

        final_render_options = component_render_options.copy()
        final_render_options.update(manifest_options)
        final_render_options.update(context.render_options)

        if getattr(component, "is_page", False):
            final_render_options["frameless"] = True

        if not context.theme_manager.current_theme:
            raise RenderingError("渲染失败：主题未被正确加载。")

        resolved_template_name = await context.theme_manager.resolve_component_template(
            component, context
        )
        theme_css_template = context.template_engine.env.get_template("theme.css.jinja")
        theme_css_content = await theme_css_template.render_async(
            theme=context.theme_manager.current_theme_context
        )

        html_content = await context.template_engine.render_component_to_html(
            component,
            resolved_template_name,
            context.theme_manager.current_theme_context,
            theme_css_content,
            context.collected_inline_css,
            list(context.collected_scripts),
            list(context.collected_asset_styles),
            **final_render_options,
        )

        screenshot_options = final_render_options.copy()
        screenshot_options.pop("extra_css", None)
        screenshot_options.pop("frameless", None)

        image_bytes = await context.screenshot_engine.render(
            html=html_content,
            base_url_path=THEMES_PATH.parent,
            **screenshot_options,
        )
        return RenderResult(image_bytes=image_bytes, html_content=html_content)


class TemplateFileRenderStrategy(RenderStrategy):
    """独立模板文件渲染策略。"""

    async def render(self, context: "RenderContext") -> RenderResult:
        component = context.component
        template_path = getattr(component, "template_path")
        await component.prepare()
        logger.debug(f"正在渲染独立模板: '{template_path}'", "RendererService")

        template_dir = template_path.parent
        temp_loader = FileSystemLoader(str(template_dir))
        temp_env = Environment(
            loader=temp_loader,
            enable_async=True,
            autoescape=select_autoescape(["html", "xml"]),
        )
        temp_env.globals.update(context.template_engine.env.globals)
        temp_env.filters.update(context.template_engine.env.filters)
        temp_env.globals["asset"] = (
            context.theme_manager._create_standalone_asset_loader(template_dir)
        )
        temp_env.globals["random_asset"] = (
            context.theme_manager.create_random_asset_loader()
        )
        temp_env.filters["md"] = context.theme_manager._markdown_filter

        data_dict = component.get_render_data()
        template = temp_env.get_template(template_path.name)
        template_context = {
            "theme": context.theme_manager.current_theme_context,
            "data": data_dict,
        }

        for key, value in data_dict.items():
            if key not in RESERVED_TEMPLATE_KEYS:
                template_context[key] = value

        html_content = await template.render_async(**template_context)
        _opts = data_dict.get("render_options")
        component_render_options = _opts if isinstance(_opts, dict) else {}
        final_render_options = component_render_options.copy()
        final_render_options.update(context.render_options)
        if getattr(component, "is_page", False):
            final_render_options["frameless"] = True

        image_bytes = await context.screenshot_engine.render(
            html=html_content, base_url_path=template_dir, **final_render_options
        )
        return RenderResult(image_bytes=image_bytes, html_content=html_content)
