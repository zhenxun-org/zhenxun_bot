from pathlib import Path
import shutil
import zipfile

from nonebot.utils import run_sync

from zhenxun.services.log import logger
from zhenxun.utils.github_utils import GithubUtils
from zhenxun.utils.http_utils import AsyncHttpx

from ..config import PUBLIC_PATH, TMP_PATH, WEBUI_DIST_GITHUB_URL

COMMAND_NAME = "WebUI资源管理"


async def update_webui_assets():
    webui_assets_path = TMP_PATH / "webui_assets.zip"
    download_url = await GithubUtils.parse_github_url(
        WEBUI_DIST_GITHUB_URL
    ).get_archive_download_urls()
    logger.info("开始下载 webui_assets 资源...", COMMAND_NAME)
    if await AsyncHttpx.download_file(
        download_url, webui_assets_path, follow_redirects=True
    ):
        logger.info("下载 webui_assets 成功...", COMMAND_NAME)
        return await _file_handle(webui_assets_path)
    raise Exception("下载 webui_assets 失败", COMMAND_NAME)


@run_sync
def _file_handle(webui_assets_path: Path):
    logger.debug("开始解压 webui_assets...", COMMAND_NAME)
    if webui_assets_path.exists():
        tf = zipfile.ZipFile(webui_assets_path)
        tf.extractall(TMP_PATH)
        logger.debug("解压 webui_assets 成功...", COMMAND_NAME)
    else:
        raise Exception("解压 webui_assets 失败，文件不存在...", COMMAND_NAME)
    download_file_path = next(f for f in TMP_PATH.iterdir() if f.is_dir())
    shutil.rmtree(PUBLIC_PATH, ignore_errors=True)
    shutil.copytree(download_file_path / "dist", PUBLIC_PATH, dirs_exist_ok=True)
    logger.debug("复制 webui_assets 成功...", COMMAND_NAME)
    shutil.rmtree(TMP_PATH, ignore_errors=True)
    return [x.name for x in PUBLIC_PATH.iterdir() if x.is_dir()]
