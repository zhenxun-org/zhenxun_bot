from collections import defaultdict
from datetime import date, datetime
import os
from pathlib import Path
import time
from typing import Any

import httpx
import pypinyin
import pytz

from zhenxun.configs.config import Config
from zhenxun.services.log import logger


class ResourceDirManager:
    """
    临时文件管理器
    """

    temp_path = []  # noqa: RUF012

    @classmethod
    def __tree_append(cls, path: Path):
        """递归添加文件夹

        参数:
            path: 文件夹路径
        """
        for f in os.listdir(path):
            file = path / f
            if file.is_dir():
                if file not in cls.temp_path:
                    cls.temp_path.append(file)
                    logger.debug(f"添加临时文件夹: {path}")
                cls.__tree_append(file)

    @classmethod
    def add_temp_dir(cls, path: str | Path, tree: bool = False):
        """添加临时清理文件夹，这些文件夹会被自动清理

        参数:
            path: 文件夹路径
            tree: 是否递归添加文件夹
        """
        if isinstance(path, str):
            path = Path(path)
        if path not in cls.temp_path:
            cls.temp_path.append(path)
            logger.debug(f"添加临时文件夹: {path}")
        if tree:
            cls.__tree_append(path)


class CountLimiter:
    """
    每日调用命令次数限制
    """

    tz = pytz.timezone("Asia/Shanghai")

    def __init__(self, max_num):
        self.today = -1
        self.count = defaultdict(int)
        self.max = max_num

    def check(self, key) -> bool:
        day = datetime.now(self.tz).day
        if day != self.today:
            self.today = day
            self.count.clear()
        return self.count[key] < self.max

    def get_num(self, key):
        return self.count[key]

    def increase(self, key, num=1):
        self.count[key] += num

    def reset(self, key):
        self.count[key] = 0


class UserBlockLimiter:
    """
    检测用户是否正在调用命令
    """

    def __init__(self):
        self.flag_data = defaultdict(bool)
        self.time = time.time()

    def set_true(self, key: Any):
        self.time = time.time()
        self.flag_data[key] = True

    def set_false(self, key: Any):
        self.flag_data[key] = False

    def check(self, key: Any) -> bool:
        if time.time() - self.time > 30:
            self.set_false(key)
        return not self.flag_data[key]


class FreqLimiter:
    """
    命令冷却，检测用户是否处于冷却状态
    """

    def __init__(self, default_cd_seconds: int):
        self.next_time = defaultdict(float)
        self.default_cd = default_cd_seconds

    def check(self, key: Any) -> bool:
        return time.time() >= self.next_time[key]

    def start_cd(self, key: Any, cd_time: int = 0):
        self.next_time[key] = time.time() + (
            cd_time if cd_time > 0 else self.default_cd
        )

    def left_time(self, key: Any) -> float:
        return self.next_time[key] - time.time()


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


class TimeUtils:
    @classmethod
    def get_day_start(cls, target_date: date | datetime | None = None) -> datetime:
        """获取某天的0点时间

        返回:
            datetime: 今天某天的0点时间
        """
        if not target_date:
            target_date = datetime.now()
        return (
            target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            if isinstance(target_date, datetime)
            else datetime.combine(target_date, datetime.min.time())
        )
