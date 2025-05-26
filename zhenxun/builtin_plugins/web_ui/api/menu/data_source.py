import ujson as json

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

from .model import MenuData, MenuItem


class MenuManage:
    def __init__(self) -> None:
        self.file = DATA_PATH / "web_ui" / "menu.json"
        self.menu = []
        if self.file.exists():
            try:
                self.menu = json.load(self.file.open(encoding="utf8"))
            except Exception as e:
                logger.warning("菜单文件损坏，已重新生成...", "WebUi", e=e)
        if not self.menu:
            self.menu = [
                MenuItem(
                    name="仪表盘",
                    module="dashboard",
                    router="/dashboard",
                    icon="dashboard",
                    default=True,
                ),
                MenuItem(
                    name="真寻控制台",
                    module="command",
                    router="/command",
                    icon="command",
                ),
                MenuItem(
                    name="插件列表", module="plugin", router="/plugin", icon="plugin"
                ),
                MenuItem(
                    name="插件商店", module="store", router="/store", icon="store"
                ),
                MenuItem(
                    name="好友/群组", module="manage", router="/manage", icon="user"
                ),
                MenuItem(
                    name="数据库管理",
                    module="database",
                    router="/database",
                    icon="database",
                ),
                MenuItem(
                    name="文件管理", module="system", router="/system", icon="system"
                ),
                MenuItem(
                    name="关于我们", module="about", router="/about", icon="about"
                ),
            ]
            self.save()

    def get_menus(self):
        return MenuData(menus=self.menu)

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = [menu.to_dict() for menu in self.menu]
        with self.file.open("w", encoding="utf8") as f:
            json.dump(temp, f, ensure_ascii=False, indent=4)


menu_manage = MenuManage()
