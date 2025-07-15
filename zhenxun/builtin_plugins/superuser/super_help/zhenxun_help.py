from nonebot_plugin_htmlrender import template_to_pic

from zhenxun.configs.config import BotConfig
from zhenxun.configs.path_config import TEMPLATE_PATH
from zhenxun.models.task_info import TaskInfo
from zhenxun.utils._build_image import BuildImage

from .config import SUPERUSER_HELP_IMAGE
from .utils import get_plugins


async def get_task() -> dict[str, str] | None:
    """获取被动技能帮助"""
    if task_list := await TaskInfo.all():
        return {
            "name": "被动技能",
            "description": "控制群组中的被动技能状态",
            "usage": "通过 开启/关闭群被动 来控制群被动 <br>"
            + " 示例：开启/关闭群被动早晚安 <br> ---------- <br> "
            + "<br>".join([task.name for task in task_list]),
        }
    return None


async def build_html_help():
    """构建帮助图片"""
    plugins = await get_plugins()
    plugin_list = []
    for data in plugins:
        if data.metadata.extra:
            if superuser_help := data.metadata.extra.get("superuser_help"):
                data.metadata.usage += f"<br>以下为超级用户额外命令<br>{superuser_help}"
        plugin_list.append(
            {
                "name": data.plugin.name,
                "description": data.metadata.description.replace("\n", "<br>"),
                "usage": data.metadata.usage.replace("\n", "<br>"),
            }
        )
    if task := await get_task():
        plugin_list.append(task)
    plugin_list.sort(key=lambda p: len(p["description"]) + len(p["usage"]))
    pic = await template_to_pic(
        template_path=str((TEMPLATE_PATH / "help").absolute()),
        template_name="main.html",
        templates={
            "data": {
                "plugin_list": plugin_list,
                "nickname": BotConfig.self_nickname,
                "help_name": "超级用户",
            }
        },
        pages={
            "viewport": {"width": 824, "height": 10},
            "base_url": f"file://{TEMPLATE_PATH}",
        },
        wait=2,
    )
    result = await BuildImage.open(pic).resize(0.5)
    await result.save(SUPERUSER_HELP_IMAGE)
