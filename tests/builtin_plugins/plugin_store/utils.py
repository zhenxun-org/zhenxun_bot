from pathlib import Path

from respx import MockRouter

from tests.utils import get_content_bytes as _get_content_bytes
from tests.utils import get_response_json as _get_response_json


def get_response_json(file: str) -> dict:
    return _get_response_json(Path() / "plugin_store", file=file)


def get_content_bytes(file: str) -> bytes:
    return _get_content_bytes(Path() / "plugin_store", file)


def init_mocked_api(mocked_api: MockRouter) -> None:
    # metadata
    mocked_api.get(
        "https://data.jsdelivr.com/v1/packages/gh/zhenxun-org/zhenxun_bot_plugins@main",
        name="zhenxun_bot_plugins_metadata",
    ).respond(json=get_response_json("zhenxun_bot_plugins_metadata.json"))
    mocked_api.get(
        "https://data.jsdelivr.com/v1/packages/gh/xuanerwa/zhenxun_github_sub@main",
        name="zhenxun_github_sub_metadata",
    ).respond(json=get_response_json("zhenxun_github_sub_metadata.json"))
    mocked_api.get(
        "https://data.jsdelivr.com/v1/packages/gh/zhenxun-org/zhenxun_bot_plugins@b101fbc",
        name="zhenxun_bot_plugins_metadata_commit",
    ).respond(json=get_response_json("zhenxun_bot_plugins_metadata.json"))
    mocked_api.get(
        "https://data.jsdelivr.com/v1/packages/gh/xuanerwa/zhenxun_github_sub@f524632f78d27f9893beebdf709e0e7885cd08f1",
        name="zhenxun_github_sub_metadata_commit",
    ).respond(json=get_response_json("zhenxun_github_sub_metadata.json"))

    # tree
    mocked_api.get(
        "https://api.github.com/repos/zhenxun-org/zhenxun_bot_plugins/git/trees/main?recursive=1",
        name="zhenxun_bot_plugins_tree",
    ).respond(json=get_response_json("zhenxun_bot_plugins_tree.json"))
    mocked_api.get(
        "https://api.github.com/repos/xuanerwa/zhenxun_github_sub/git/trees/main?recursive=1",
        name="zhenxun_github_sub_tree",
    ).respond(json=get_response_json("zhenxun_github_sub_tree.json"))
    mocked_api.get(
        "https://api.github.com/repos/zhenxun-org/zhenxun_bot_plugins/git/trees/b101fbc?recursive=1",
        name="zhenxun_bot_plugins_tree_commit",
    ).respond(json=get_response_json("zhenxun_bot_plugins_tree.json"))
    mocked_api.get(
        "https://api.github.com/repos/xuanerwa/zhenxun_github_sub/git/trees/f524632f78d27f9893beebdf709e0e7885cd08f1?recursive=1",
        name="zhenxun_github_sub_tree_commit",
    ).respond(json=get_response_json("zhenxun_github_sub_tree.json"))

    mocked_api.head(
        "https://raw.githubusercontent.com/",
        name="head_raw",
    ).respond(200, text="")

    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins/b101fbc/plugins.json",
        name="basic_plugins",
    ).respond(json=get_response_json("basic_plugins.json"))
    mocked_api.get(
        "https://cdn.jsdelivr.net/gh/zhenxun-org/zhenxun_bot_plugins@b101fbc/plugins.json",
        name="basic_plugins_jsdelivr",
    ).respond(200, json=get_response_json("basic_plugins.json"))
    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins/main/plugins.json",
        name="basic_plugins_no_commit",
    ).respond(json=get_response_json("basic_plugins.json"))
    mocked_api.get(
        "https://cdn.jsdelivr.net/gh/zhenxun-org/zhenxun_bot_plugins@main/plugins.json",
        name="basic_plugins_jsdelivr_no_commit",
    ).respond(200, json=get_response_json("basic_plugins.json"))

    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins_index/2ed61284873c526802752b12a3fd3b5e1a59d948/plugins.json",
        name="extra_plugins",
    ).respond(200, json=get_response_json("extra_plugins.json"))
    mocked_api.get(
        "https://cdn.jsdelivr.net/gh/zhenxun-org/zhenxun_bot_plugins_index@2ed61284873c526802752b12a3fd3b5e1a59d948/plugins.json",
        name="extra_plugins_jsdelivr",
    ).respond(200, json=get_response_json("extra_plugins.json"))
    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins_index/index/plugins.json",
        name="extra_plugins_no_commit",
    ).respond(200, json=get_response_json("extra_plugins.json"))
    mocked_api.get(
        "https://cdn.jsdelivr.net/gh/zhenxun-org/zhenxun_bot_plugins_index@index/plugins.json",
        name="extra_plugins_jsdelivr_no_commit",
    ).respond(200, json=get_response_json("extra_plugins.json"))

    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins/main/plugins/search_image/__init__.py",
        name="search_image_plugin_file_init",
    ).respond(content=get_content_bytes("search_image.py"))
    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins/b101fbc/plugins/search_image/__init__.py",
        name="search_image_plugin_file_init_commit",
    ).respond(content=get_content_bytes("search_image.py"))
    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins/main/plugins/alapi/jitang.py",
        name="jitang_plugin_file",
    ).respond(content=get_content_bytes("jitang.py"))
    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins/b101fbc/plugins/alapi/jitang.py",
        name="jitang_plugin_file_commit",
    ).respond(content=get_content_bytes("jitang.py"))
    mocked_api.get(
        "https://raw.githubusercontent.com/xuanerwa/zhenxun_github_sub/main/github_sub/__init__.py",
        name="github_sub_plugin_file_init",
    ).respond(content=get_content_bytes("github_sub.py"))
    mocked_api.get(
        "https://raw.githubusercontent.com/xuanerwa/zhenxun_github_sub/f524632f78d27f9893beebdf709e0e7885cd08f1/github_sub/__init__.py",
        name="github_sub_plugin_file_init_commit",
    ).respond(content=get_content_bytes("github_sub.py"))
    mocked_api.get(
        "https://raw.githubusercontent.com/zhenxun-org/zhenxun_bot_plugins/b101fbc/plugins/bilibili_sub/__init__.py",
        name="bilibili_sub_plugin_file_init",
    ).respond(content=get_content_bytes("bilibili_sub.py"))

    mocked_api.get(
        "https://api.github.com/repos/zhenxun-org/zhenxun_bot_plugins/commits/main",
        name="zhenxun_bot_plugins_commit",
    ).respond(json=get_response_json("zhenxun_bot_plugins_commit.json"))
    mocked_api.get(
        "https://git-api.zhenxun.org/repos/zhenxun-org/zhenxun_bot_plugins/commits/main",
        name="zhenxun_bot_plugins_commit_proxy",
    ).respond(json=get_response_json("zhenxun_bot_plugins_commit.json"))
    mocked_api.get(
        "https://api.github.com/repos/zhenxun-org/zhenxun_bot_plugins_index/commits/index",
        name="zhenxun_bot_plugins_index_commit",
    ).respond(json=get_response_json("zhenxun_bot_plugins_index_commit.json"))
    mocked_api.get(
        "https://git-api.zhenxun.org/repos/zhenxun-org/zhenxun_bot_plugins_index/commits/index",
        name="zhenxun_bot_plugins_index_commit_proxy",
    ).respond(json=get_response_json("zhenxun_bot_plugins_index_commit.json"))
    mocked_api.get(
        "https://api.github.com/repos/xuanerwa/zhenxun_github_sub/commits/main",
        name="zhenxun_github_sub_commit",
    ).respond(json=get_response_json("zhenxun_github_sub_commit.json"))
    mocked_api.get(
        "https://git-api.zhenxun.org/repos/xuanerwa/zhenxun_github_sub/commits/main",
        name="zhenxun_github_sub_commit_proxy",
    ).respond(json=get_response_json("zhenxun_github_sub_commit.json"))
