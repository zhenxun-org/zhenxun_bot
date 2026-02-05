import os
from pathlib import Path
import random

from zhenxun import ui
from zhenxun.ui.models.charts import EChartsData

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

        chart_data = EChartsData.bar_chart(
            title=data.title,
            items=items,  # type: ignore
            direction="horizontal",
            background_image=background_image_name,
        )

        return await ui.render(chart_data)
