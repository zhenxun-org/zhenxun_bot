import contextlib
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from nonebot.utils import run_sync
import psutil
import ujson as json

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.db_context import with_db_timeout
from zhenxun.services.message_load import is_db_unhealthy

from .base_model import SystemFolderSize, SystemStatus, User

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
DB_BUSY_MESSAGE = "数据库繁忙，请稍后再试"
WEBUI_DB_TIMEOUT = 3.0

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

token_file = DATA_PATH / "web_ui" / "token.json"
token_file.parent.mkdir(parents=True, exist_ok=True)
token_data = {"token": []}
if token_file.exists():
    with contextlib.suppress(json.JSONDecodeError):
        token_data = json.load(open(token_file, encoding="utf8"))


async def webui_db_call(coro, operation: str):
    if is_db_unhealthy():
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise TimeoutError(DB_BUSY_MESSAGE)
    return await with_db_timeout(
        coro,
        timeout=WEBUI_DB_TIMEOUT,
        operation=operation,
        source="web_ui",
    )


def validate_path(path_str: str | None) -> tuple[Path | None, str | None]:
    """验证路径是否安全

    参数:
        path_str: 用户输入的路径

    返回:
        tuple[Path | None, str | None]: (验证后的路径, 错误信息)
    """
    try:
        if not path_str:
            return Path().resolve(), None

        # 1. 规范化路径并转换为绝对路径（resolve() 会展开所有 .. 和符号链接）
        path = Path(path_str).resolve()

        # 2. 获取项目根目录
        root_dir = Path().resolve()

        # 3. 验证 resolve() 后的路径是否仍在项目根目录内（防路径穿越）
        try:
            if not path.is_relative_to(root_dir):
                return None, "访问路径超出允许范围"
        except ValueError:
            return None, "无效的路径格式"

        # 4. 验证路径长度是否合理
        return (None, "路径长度超出限制") if len(str(path)) > 4096 else (path, None)
    except Exception as e:
        return None, f"路径验证失败: {e!s}"


def validate_filename(name: str) -> str | None:
    """验证文件名是否安全（不允许路径分隔符或路径穿越）

    参数:
        name: 用户输入的文件名

    返回:
        str | None: 错误信息，无错误则返回 None
    """
    if not name or not name.strip():
        return "文件名不能为空"
    # 禁止任何路径分隔符，防止将文件名当路径使用
    if any(c in name for c in ("/", "\\", "\x00")):
        return "文件名包含非法路径分隔符"
    # 禁止 . 和 .. 作为文件名
    if name.strip(".") == "":
        return "文件名非法"
    # 禁止危险字符（Windows / Linux 通用）
    if any(c in name for c in ("<", ">", ":", '"', "|", "?", "*")):
        return "文件名包含非法字符"
    return None


def get_user(uname: str) -> User | None:
    """获取账号密码

    参数:
        uname: uname

    返回:
        Optional[User]: 用户信息
    """
    username = Config.get_config("web-ui", "username")
    password = Config.get_config("web-ui", "password")
    if username and password and uname == username:
        return User(username=username, password=password)


def create_token(user: User, expires_delta: timedelta | None = None):
    """创建token

    参数:
        user: 用户信息
        expires_delta: 过期时间.
    """
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    return jwt.encode(
        claims={"sub": user.username, "exp": expire},
        key=Config.get_config("web-ui", "secret"),
        algorithm=ALGORITHM,
    )


def authentication():
    """权限验证

    异常:
        JWTError: JWTError
        HTTPException: HTTPException
    """

    # if token not in token_data["token"]:
    def inner(token: str = Depends(oauth2_scheme)):
        try:
            payload = jwt.decode(
                token, Config.get_config("web-ui", "secret"), algorithms=[ALGORITHM]
            )
            username, _ = payload.get("sub"), payload.get("exp")
            user = get_user(username)  # type: ignore
            if user is None:
                raise JWTError
        except JWTError:
            raise HTTPException(
                status_code=400, detail="登录验证失败或已失效, 踢出房间!"
            )

    return Depends(inner)


def _get_dir_size(dir_path: Path) -> float:
    """获取文件夹大小

    参数:
        dir_path: 文件夹路径
    """
    return sum(
        sum(os.path.getsize(os.path.join(root, name)) for name in files)
        for root, dirs, files in os.walk(dir_path)
    )


@run_sync
def get_system_status() -> SystemStatus:
    """获取系统信息等"""
    cpu = psutil.cpu_percent()
    memory = psutil.virtual_memory().percent
    disk_root = Path().resolve().anchor  # 跨平台：取当前工作目录所在盘的根
    disk = psutil.disk_usage(disk_root).percent
    return SystemStatus(
        cpu=cpu,
        memory=memory,
        disk=disk,
        check_time=datetime.now().replace(microsecond=0),
    )


@run_sync
def get_system_disk(
    full_path: str | None,
) -> list[SystemFolderSize]:
    """获取资源文件大小等"""
    if full_path:
        base_path, err = validate_path(full_path)
        if err or not base_path:
            return []
    else:
        base_path = Path().resolve()
    other_size = 0
    data_list = []
    for file in os.listdir(base_path):
        f = base_path / file
        if f.is_dir():
            size = _get_dir_size(f) / 1024 / 1024
            data_list.append(
                SystemFolderSize(name=file, size=size, full_path=str(f), is_dir=True)
            )
        else:
            other_size += f.stat().st_size / 1024 / 1024
    if other_size:
        data_list.append(
            SystemFolderSize(
                name="other_file", size=other_size, full_path=full_path, is_dir=False
            )
        )
    return data_list
