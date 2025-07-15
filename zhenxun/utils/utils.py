from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import time
from typing import ClassVar

import httpx
from nonebot_plugin_uninfo import Uninfo
import pypinyin

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

from .limiters import CountLimiter, FreqLimiter, UserBlockLimiter  # noqa: F401


@dataclass
class EntityIDs:
    user_id: str
    """用户id"""
    group_id: str | None
    """群组id"""
    channel_id: str | None
    """频道id"""


class ResourceDirManager:
    """
    临时文件管理器
    """

    temp_path: ClassVar[set[Path]] = set()

    @classmethod
    def __tree_append(cls, path: Path, deep: int = 1, current: int = 0):
        """递归添加文件夹"""
        if current >= deep and deep != -1:
            return
        path = path.resolve()  # 标准化路径
        for f in os.listdir(path):
            file = (path / f).resolve()  # 标准化子路径
            if file.is_dir():
                if file not in cls.temp_path:
                    cls.temp_path.add(file)
                    logger.debug(f"添加临时文件夹: {file}")
                cls.__tree_append(file, deep, current + 1)

    @classmethod
    def add_temp_dir(cls, path: str | Path, tree: bool = False, deep: int = 1):
        """添加临时清理文件夹，这些文件夹会被自动清理

        参数:
            path: 文件夹路径
            tree: 是否递归添加文件夹
            deep: 深度, -1 为无限深度
        """
        if isinstance(path, str):
            path = Path(path)
        if path not in cls.temp_path:
            cls.temp_path.add(path)
            logger.debug(f"添加临时文件夹: {path}")
        if tree:
            cls.__tree_append(path, deep)


def cn2py(word: str) -> str:
    """将字符串转化为拼音

    参数:
        word: 文本
    """
    return "".join("".join(i) for i in pypinyin.pinyin(word, style=pypinyin.NORMAL))


async def get_user_avatar(uid: int | str) -> bytes | None:
    """快捷获取用户头像

    参数:
        uid: 用户id
    """
    url = f"http://q1.qlogo.cn/g?b=qq&nk={uid}&s=160"
    async with httpx.AsyncClient() as client:
        for _ in range(3):
            try:
                return (await client.get(url)).content
            except Exception:
                logger.error("获取用户头像错误", "Util", target=uid)
    return None


async def get_group_avatar(gid: int | str) -> bytes | None:
    """快捷获取用群头像

    参数:
        gid: 群号
    """
    url = f"http://p.qlogo.cn/gh/{gid}/{gid}/640/"
    async with httpx.AsyncClient() as client:
        for _ in range(3):
            try:
                return (await client.get(url)).content
            except Exception:
                logger.error("获取群头像错误", "Util", target=gid)
    return None


def change_pixiv_image_links(
    url: str, size: str | None = None, nginx_url: str | None = None
) -> str:
    """根据配置改变图片大小和反代链接

    参数:
        url: 图片原图链接
        size: 模式
        nginx_url: 反代

    返回:
        str: url
    """
    if size == "master":
        img_sp = url.rsplit(".", maxsplit=1)
        url = img_sp[0]
        img_type = img_sp[1]
        url = url.replace("original", "master") + f"_master1200.{img_type}"
    if not nginx_url:
        nginx_url = Config.get_config("pixiv", "PIXIV_NGINX_URL")
    if nginx_url:
        url = (
            url.replace("i.pximg.net", nginx_url)
            .replace("i.pixiv.cat", nginx_url)
            .replace("i.pixiv.re", nginx_url)
            .replace("_webp", "")
        )
    return url


def change_img_md5(path_file: str | Path) -> bool:
    """改变图片MD5

    参数:
        path_file: 图片路径

    返还:
        bool: 是否修改成功
    """
    try:
        with open(path_file, "a") as f:
            f.write(str(int(time.time() * 1000)))
        return True
    except Exception as e:
        logger.warning(f"改变图片MD5错误 Path：{path_file}", e=e)
    return False


def is_valid_date(date_text: str, separator: str = "-") -> bool:
    """日期是否合法

    参数:
        date_text: 日期
        separator: 分隔符

    返回:
        bool: 日期是否合法
    """
    try:
        datetime.strptime(date_text, f"%Y{separator}%m{separator}%d")
        return True
    except ValueError:
        return False


def get_entity_ids(session: Uninfo) -> EntityIDs:
    """获取用户id，群组id，频道id

    参数:
        session: Uninfo

    返回:
        EntityIDs: 用户id，群组id，频道id
    """
    user_id = session.user.id
    group_id = None
    channel_id = None
    if session.group:
        if session.group.parent:
            group_id = session.group.parent.id
            channel_id = session.group.id
        else:
            group_id = session.group.id
    return EntityIDs(user_id=user_id, group_id=group_id, channel_id=channel_id)


def is_number(text: str) -> bool:
    """是否为数字

    参数:
        text: 文本

    返回:
        bool: 是否为数字
    """
    try:
        float(text)
        return True
    except ValueError:
        return False
