from pathlib import Path

from nonebot_plugin_htmlrender import html_to_pic

from .types import BaseScreenshotEngine


class PlaywrightEngine(BaseScreenshotEngine):
    """使用 nonebot-plugin-htmlrender 实现的截图引擎。"""

    async def render(self, html: str, base_url_path: Path, **render_options) -> bytes:
        base_url_for_browser = base_url_path.absolute().as_uri()
        if not base_url_for_browser.endswith("/"):
            base_url_for_browser += "/"

        final_render_options = {
            "viewport": {"width": 800, "height": 10},
            **render_options,
            "base_url": base_url_for_browser,
        }

        return await html_to_pic(
            html=html,
            template_path=base_url_for_browser,
            **final_render_options,
        )


class EngineManager:
    """
    引擎管理器，负责加载和提供具体的截图引擎实例。
    未来可在此处根据 Config 读取不同的驱动配置。
    """

    def __init__(self):
        self._engine_class: type[BaseScreenshotEngine] = PlaywrightEngine
        self._instance: BaseScreenshotEngine | None = None

    async def get_engine(self) -> BaseScreenshotEngine:
        if not self._instance:
            self._instance = self._engine_class()
            await self._instance.initialize()
        return self._instance

    async def close(self):
        if self._instance:
            await self._instance.close()
            self._instance = None


engine_manager = EngineManager()
