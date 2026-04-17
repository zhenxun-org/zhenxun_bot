from pathlib import Path
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import nonebot

from zhenxun.configs.config import BotConfig, Config
from zhenxun.utils._restart_utils import issue_restart_ticket, request_restart

from ...base_model import Result
from .data_source import test_db_connection
from .model import Setting

router = APIRouter(prefix="/configure")

driver = nonebot.get_driver()

port = driver.config.port


@router.post(
    "/set_configure",
    response_model=Result,
    response_class=JSONResponse,
    description="设置基础配置",
)
async def _(setting: Setting) -> Result:
    global port
    password = Config.get_config("web-ui", "password")
    if password or BotConfig.db_url:
        return Result.fail("配置已存在，请先删除DB_URL内容和前端密码再进行设置。")
    env_file = Path() / ".env.example"
    if not env_file.exists():
        return Result.fail("基础配置文件.env.example不存在。")
    env_text = env_file.read_text(encoding="utf-8")
    to_env_file = Path() / ".env.dev"
    if setting.db_url:
        if setting.db_url.startswith("sqlite"):
            base_dir = Path().resolve()
            # 清理和验证数据库路径
            db_path_str = setting.db_url.split(":")[-1].strip()
            # 移除任何可能的路径遍历尝试
            db_path_str = re.sub(r"[\\/]\.\.[\\/]", "", db_path_str)
            # 规范化路径
            db_path = Path(db_path_str).resolve()
            parent_path = db_path.parent

            # 验证路径是否在项目根目录内
            try:
                if not parent_path.absolute().is_relative_to(base_dir):
                    return Result.fail("数据库路径不在项目根目录内。")
            except ValueError:
                return Result.fail("无效的数据库路径。")

            # 创建目录
            try:
                parent_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return Result.fail(f"创建数据库目录失败: {e!s}")

        env_text = env_text.replace('DB_URL = ""', f'DB_URL = "{setting.db_url}"')
    if setting.superusers:
        superusers = ", ".join([f'"{s}"' for s in setting.superusers])
        env_text = re.sub(r"SUPERUSERS=\[.*?\]", f"SUPERUSERS=[{superusers}]", env_text)
    if setting.host:
        env_text = env_text.replace("HOST = 127.0.0.1", f"HOST = {setting.host}")
    if setting.port:
        env_text = env_text.replace("PORT = 8080", f"PORT = {setting.port}")
        port = setting.port
    if setting.username:
        Config.set_config("web-ui", "username", setting.username)
    Config.set_config("web-ui", "password", setting.password, True)
    to_env_file.write_text(env_text, encoding="utf-8")
    issue_restart_ticket("webui.configure", ttl_seconds=10 * 60)
    return Result.ok(True, info="设置成功，请重启真寻以完成配置！")


@router.get(
    "/test_db",
    response_model=Result,
    response_class=JSONResponse,
    description="设置基础配置",
)
async def _(db_url: str) -> Result:
    result = await test_db_connection(db_url)
    if isinstance(result, str):
        return Result.fail(result)
    return Result.ok(info="数据库连接成功!")


@router.post(
    "/restart",
    response_model=Result,
    response_class=JSONResponse,
    description="重启",
)
async def _() -> Result:
    ok, message = await request_restart(
        "webui.configure",
        require_ticket="webui.configure",
    )
    if not ok:
        return Result.fail(message)
    return Result.ok(info=message)
