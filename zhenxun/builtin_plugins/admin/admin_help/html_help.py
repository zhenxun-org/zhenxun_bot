from nonebot_plugin_htmlrender import template_to_pic

from zhenxun.builtin_plugins.admin.admin_help.config import ADMIN_HELP_IMAGE
from zhenxun.configs.config import BotConfig
from zhenxun.configs.path_config import TEMPLATE_PATH
from zhenxun.models.task_info import TaskInfo
from zhenxun.utils._build_image import BuildImage

from .utils import get_plugins


async def get_task() -> dict[str, str] | None:
    """获取被动技能帮助"""
    if task_list := await TaskInfo.all():
        return {
            "name": "被动技能",
            "description": "控制群组中的被动技能状态",
            "usage": "通过 开启/关闭群被动 来控制群被动 <br>"
            + " 示例：开启/关闭群被动早晚安 <br> 示例：开启/关闭全部群被动"
            + " <br> ---------- <br> "
            + "<br>".join([task.name for task in task_list]),
        }
    return None


async def build_html_help():
    """构建帮助图片"""
    plugins = await get_plugins()
    plugin_list = [
        {
            "name": data.plugin.name,
            "description": data.metadata.description.replace("\n", "<br>"),
            "usage": data.metadata.usage.replace("\n", "<br>"),
        }
        for data in plugins
    ]
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
                "help_name": "群管理员",
            }
        },
        pages={
            "viewport": {"width": 824, "height": 10},
            "base_url": f"file://{TEMPLATE_PATH}",
        },
        wait=2,
    )
    result = await BuildImage.open(pic).resize(0.5)
    await result.save(ADMIN_HELP_IMAGE)
