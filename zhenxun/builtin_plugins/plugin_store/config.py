from pathlib import Path

BASE_PATH = Path() / "zhenxun"
BASE_PATH.mkdir(parents=True, exist_ok=True)


DEFAULT_GITHUB_URL = "https://github.com/zhenxun-org/zhenxun_bot_plugins/tree/main"
"""伴生插件github仓库地址"""

EXTRA_GITHUB_URL = "https://github.com/zhenxun-org/zhenxun_bot_plugins_index/tree/index"
"""插件库索引github仓库地址"""

GITEE_RAW_URL = "https://gitee.com/two_Dimension/zhenxun_bot_plugins/raw/main"
"""GITEE仓库文件内容"""

GITEE_CONTENTS_URL = (
    "https://gitee.com/api/v5/repos/two_Dimension/zhenxun_bot_plugins/contents"
)
"""GITEE仓库文件列表获取"""


LOG_COMMAND = "插件商店"
