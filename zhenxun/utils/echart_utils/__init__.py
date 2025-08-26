import os
from pathlib import Path
import random

from zhenxun import ui
from zhenxun.ui.builders import charts as chart_builders

from .models import Barh

BACKGROUND_PATH = (
    Path() / "resources" / "themes" / "default" / "assets" / "ui" / "background"
)


class ChartUtils:
    @classmethod
    async def barh(cls, data: Barh) -> bytes:
        """横向统计图"""
        background_image_name = (
            random.choice(os.listdir(BACKGROUND_PATH))
            if BACKGROUND_PATH.exists()
            else None
        )
        items = list(zip(data.category_data, data.data))
        builder = chart_builders.bar_chart(
            title=data.title, items=items, direction="horizontal"
        )
        if background_image_name:
            builder.set_background_image(background_image_name)

        return await ui.render(builder.build())
