import ujson as json

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

from .model import MenuData, MenuItem

default_menus = [
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
    MenuItem(name="插件列表", module="plugin", router="/plugin", icon="plugin"),
    MenuItem(name="插件商店", module="store", router="/store", icon="store"),
    MenuItem(name="好友/群组", module="manage", router="/manage", icon="user"),
    MenuItem(
        name="数据库管理",
        module="database",
        router="/database",
        icon="database",
    ),
    MenuItem(name="系统信息", module="system", router="/system", icon="system"),
    MenuItem(name="关于我们", module="about", router="/about", icon="about"),
]


class MenuManager:
    def __init__(self) -> None:
        self.file = DATA_PATH / "web_ui" / "menu.json"
        self.menu = []
        if self.file.exists():
            try:
                temp_menu = []
                self.menu = json.load(self.file.open(encoding="utf8"))
                self_menu_name = [menu["name"] for menu in self.menu]
                for module in [m.module for m in default_menus]:
                    if module in self_menu_name:
                        temp_menu.append(
                            MenuItem(
                                **next(m for m in self.menu if m["module"] == module)
                            )
                        )
                    else:
                        temp_menu.append(self.__get_menu_model(module))
                self.menu = temp_menu
            except Exception as e:
                logger.warning("菜单文件损坏，已重新生成...", "WebUi", e=e)
        if not self.menu:
            self.menu = default_menus
        self.save()

    def __get_menu_model(self, module: str):
        return default_menus[
            next(i for i, m in enumerate(default_menus) if m.module == module)
        ]

    def get_menus(self):
        return MenuData(menus=self.menu)

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = [menu.to_dict() for menu in self.menu]
        with self.file.open("w", encoding="utf8") as f:
            json.dump(temp, f, ensure_ascii=False, indent=4)


menu_manage = MenuManager()
