from pathlib import Path

BASE_PATH = Path() / "zhenxun"
BASE_PATH.mkdir(parents=True, exist_ok=True)


DEFAULT_GITHUB_URL = "https://github.com/zhenxun-org/zhenxun_bot_plugins/tree/main"
"""伴生插件github仓库地址"""

EXTRA_GITHUB_URL = "https://github.com/zhenxun-org/zhenxun_bot_plugins_index/tree/index"
"""插件库索引github仓库地址"""

LOG_COMMAND = "插件商店"
