from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from zhenxun.services.log import logger
from zhenxun.utils.manager.zhenxun_repo_manager import ZhenxunRepoManager

router = APIRouter()


@router.get("/")
async def index():
    return FileResponse(ZhenxunRepoManager.config.WEBUI_PATH / "index.html")


@router.get("/favicon.ico")
async def favicon():
    return FileResponse(ZhenxunRepoManager.config.WEBUI_PATH / "favicon.ico")


async def init_public(app: FastAPI):
    try:
        if not ZhenxunRepoManager.check_webui_exists():
            await ZhenxunRepoManager.webui_update(branch="test")
        folders = [
            x.name for x in ZhenxunRepoManager.config.WEBUI_PATH.iterdir() if x.is_dir()
        ]
        app.include_router(router)
        for pathname in folders:
            logger.debug(f"挂载文件夹: {pathname}")
            app.mount(
                f"/{pathname}",
                StaticFiles(
                    directory=ZhenxunRepoManager.config.WEBUI_PATH / pathname,
                    check_dir=True,
                ),
                name=f"public_{pathname}",
            )
    except Exception as e:
        logger.error("初始化 WebUI资源 失败", "WebUI", e=e)
