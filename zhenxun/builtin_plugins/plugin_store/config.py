from pathlib import Path

BASE_PATH = Path() / "zhenxun"
BASE_PATH.mkdir(parents=True, exist_ok=True)


DEFAULT_GITHUB_URL = "https://github.com/zhenxun-org/zhenxun_bot_plugins/tree/main"
"""伴生插件github仓库地址"""

EXTRA_GITHUB_URL = "https://github.com/zhenxun-org/zhenxun_bot_plugins_index/tree/index"
"""插件库索引github仓库地址"""

DEFAULT_GITCODE_RAW_URL = "https://raw.gitcode.com/gh_mirrors/zh/zhenxun_bot/raw/main"
"""伴生插件gitcode仓库地址"""
