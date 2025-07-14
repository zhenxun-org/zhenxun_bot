from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.models.group_console import GroupConsole
from zhenxun.utils._build_image import BuildImage
from zhenxun.utils.enum import BlockType
from zhenxun.utils.image_utils import build_sort_image, group_image

from ._utils import sort_type

BACKGROUND_PATH = IMAGE_PATH / "background" / "help" / "simple_help"


async def build_normal_image(group_id: str | None, is_detail: bool) -> BuildImage:
    """构造PIL帮助图片

    参数:
         group_id: 群号
         is_detail: 详细帮助
    """
    image_list = []
    font_size = 24
    font = BuildImage.load_font("HYWenHei-85W.ttf", 20)
    sort_data = await sort_type()
    for idx, menu_type in enumerate(sort_data):
        plugin_list = sort_data[menu_type]
        """拿到最大宽度和结算高度"""
        wh_list = [
            BuildImage.get_text_size(f"{x.id}.{x.name}", font) for x in plugin_list
        ]
        wh_list.append(BuildImage.get_text_size(menu_type, font))
        sum_height = (font_size + 6) * len(plugin_list) + 10
        max_width = max(x[0] for x in wh_list) + 30
        bk = BuildImage(
            max_width + 40,
            sum_height + 50,
            font_size=30,
            color="#a7d1fc",
            font="CJGaoDeGuo.otf",
        )
        title_size = bk.getsize(menu_type)
        max_width = max_width if max_width > title_size[0] else title_size[0]
        row = BuildImage(
            max_width + 40,
            sum_height,
            font_size=font_size,
            color="black" if idx % 2 else "white",
        )
        curr_h = 10
        group = await GroupConsole.get_group(group_id=group_id) if group_id else None
        for _, plugin in enumerate(plugin_list):
            text_color = (255, 255, 255) if idx % 2 else (0, 0, 0)
            if group and f"{plugin.module}," in group.block_plugin:
                text_color = (252, 75, 13)
            pos = None
            # 禁用状态划线
            if plugin.block_type in [BlockType.ALL, BlockType.GROUP] or (
                group and f"super:{plugin.module}," in group.block_plugin
            ):
                w = curr_h + int(row.getsize(plugin.name)[1] / 2) + 2
                line_width = row.getsize(plugin.name)[0] + 35
                pos = (7, w, line_width, w)
            await row.text((10, curr_h), f"{plugin.id}.{plugin.name}", text_color)
            if pos:
                await row.line(pos, (236, 66, 7), 3)
            curr_h += font_size + 5
        await bk.text((0, 14), menu_type, center_type="width")
        await bk.paste(row, (0, 50))
        await bk.transparent(2)
        image_list.append(bk)
    image_group, h = group_image(image_list)

    async def _a(image: BuildImage):
        await image.filter("GaussianBlur", 5)

    result = await build_sort_image(
        image_group,
        h,
        background_path=BACKGROUND_PATH,
        background_handle=_a,
    )
    width, height = 10, 10
    for s in [
        "目前支持的功能列表:",
        "可以通过 '帮助 [功能名称或功能Id]' 来获取对应功能的使用方法",
    ]:
        text = await BuildImage.build_text_image(s, "HYWenHei-85W.ttf", 24)
        await result.paste(text, (width, height))
        height += 50
        if s == "目前支持的功能列表:":
            width += 50
    text = await BuildImage.build_text_image(
        "注: 红字代表功能被群管理员禁用，红线代表功能正在维护",
        "HYWenHei-85W.ttf",
        24,
        (231, 74, 57),
    )
    await result.paste(
        text,
        (300, 10),
    )
    return result
