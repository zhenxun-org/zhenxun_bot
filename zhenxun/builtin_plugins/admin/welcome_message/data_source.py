import os
from pathlib import Path
import re
import shutil
import uuid

import nonebot
from nonebot_plugin_alconna import UniMessage, UniMsg
from nonebot_plugin_uninfo import Uninfo
import ujson as json

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger
from zhenxun.utils._build_image import BuildImage
from zhenxun.utils._image_template import ImageTemplate
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.platform import PlatformUtils

BASE_PATH = DATA_PATH / "welcome_message"
BASE_PATH.mkdir(parents=True, exist_ok=True)

driver = nonebot.get_driver()


def __migrate():
    """首次数据迁移"""
    old_file = DATA_PATH / "custom_welcome_msg" / "custom_welcome_msg.json"
    if not old_file.exists():
        return
    try:
        old_data: dict[str, str] = json.load(old_file.open(encoding="utf8"))
        for group_id, message in old_data.items():
            file = BASE_PATH / "qq" / f"{group_id}" / "text.json"
            file.parent.mkdir(parents=True, exist_ok=True)
            json.dump(
                {
                    uuid.uuid4(): {
                        "at": "[at]" in message,
                        "status": True,
                        "message": message.replace("[at]", ""),
                    }
                },
                file.open("w", encoding="utf8"),
                ensure_ascii=False,
                indent=4,
            )
            logger.debug("群欢迎消息数据迁移", group_id=group_id)
        shutil.rmtree(old_file.parent.absolute())
    except Exception as e:
        logger.error("群欢迎消息数据迁移失败...", e=e)


def migrate(path: Path):
    """数据迁移

    参数:
        path: 路径
    """
    __migrate()
    text_file = path / "text.json"
    if not text_file.exists():
        return
    with text_file.open(encoding="utf8") as f:
        json_data = json.load(f)
    new_data = {}
    if "at" in json_data:
        split_msg = re.split(r"\[image:\d\]", str(json_data["message"]))
        data = []
        for i in range(len(split_msg)):
            msg = split_msg[i]
            data.append(
                {
                    "type": "text",
                    "text": msg,
                }
            )
            image_file = path / f"{i}.png"
            if image_file.exists():
                data.append(
                    {
                        "type": "image",
                        "path": str(image_file),
                    }
                )
        new_data[uuid.uuid4()] = {
            "at": json_data.get("at", False),
            "status": json_data.get("status", True),
            "message": data,
        }
    with text_file.open("w", encoding="utf8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=4)


@PriorityLifecycle.on_startup(priority=5)
def _():
    """数据迁移

    参数:
        path: 存储路径
        json_data: 存储数据
    """
    flag_file = BASE_PATH / "flag.txt"
    if flag_file.exists():
        return
    logger.info("开始迁移群欢迎消息数据...")
    base_path = BASE_PATH
    path_list = []
    for platform in os.listdir(BASE_PATH):
        base_path = base_path / platform
        for group_id in os.listdir(base_path):
            group_path = base_path / group_id
            is_channel = False
            for file in os.listdir(group_path):
                inner_file = group_path / file
                if inner_file.is_dir():
                    path_list.append(inner_file)
                    is_channel = True
            if not is_channel:
                path_list.append(group_path)
    if path_list:
        for path in path_list:
            migrate(path)
    if not flag_file.exists():
        flag_file.touch()
    logger.success("迁移群欢迎消息数据完成!", "")


class Manager:
    @classmethod
    def __get_data(cls, session: Uninfo) -> dict | None:
        """获取存储数据

        参数:
            session: Uninfo

        返回:
            dict | None: 欢迎消息数据
        """
        if not session.group:
            return None
        path = cls.get_path(session)
        if not path:
            return None
        file = path / "text.json"
        if not file.exists():
            return None
        with file.open(encoding="utf8") as f:
            return json.load(f)

    @classmethod
    def get_path(cls, session: Uninfo) -> Path | None:
        """根据Session获取存储路径

        参数:
            session: Uninfo:

        返回:
            Path: 存储路径
        """
        if not session.group:
            return None
        platform = PlatformUtils.get_platform(session)
        path = BASE_PATH / f"{platform}" / f"{session.group.id}"
        if session.group.parent:
            path = (
                BASE_PATH
                / f"{platform}"
                / f"{session.group.parent.id}"
                / f"{session.group.id}"
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    async def save(cls, path: Path, message: UniMsg):
        """保存群欢迎消息

        参数:
            path: 存储路径
            message: 消息内容
        """
        file = path / "text.json"
        json_data = {}
        if file.exists():
            with file.open(encoding="utf8") as f:
                json_data = json.load(f)
        data = []
        is_at = False
        for msg in message.dump(True):
            if msg["type"] == "image":
                image_file = path / f"{uuid.uuid4()}.png"
                await AsyncHttpx.download_file(msg["url"], image_file)
                msg["path"] = str(image_file)
            if not is_at and msg["type"] == "text" and "-at" in msg["text"]:
                msg["text"] = msg["text"].replace("-at", "", 1).strip()
                is_at = True
            data.append(msg)
        json_data[str(uuid.uuid4())] = {"at": is_at, "status": True, "message": data}
        with file.open("w", encoding="utf8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=4)

    @classmethod
    async def get_group_message(
        cls, session: Uninfo, idx: int | None
    ) -> BuildImage | UniMessage | None:
        """获取群欢迎消息

        参数:
            session: Uninfo
            idx: 指定id

        返回:
            list: 消息内容
        """
        if not session.group:
            return None
        json_data = cls.__get_data(session)
        if not json_data:
            return None
        if idx is not None:
            key_list = list(json_data.keys())
            if idx < 0 or idx > len(key_list):
                return None
            return UniMessage().load(json_data[key_list[idx]]["message"])
        else:
            msg_list = []
            for i, uid in enumerate(json_data):
                msg_data = json_data[uid]
                msg_list.append(
                    [
                        i,
                        "开启" if msg_data["status"] else "关闭",
                        "是" if msg_data["at"] else "否",
                        str(UniMessage().load(msg_data["message"])),
                    ]
                )
            if not msg_list:
                return None
            column_name = ["ID", "状态", "是否@", "消息"]
            return await ImageTemplate.table_page(
                "群欢迎消息", session.group.id, column_name, msg_list
            )

    @classmethod
    async def delete_group_message(cls, session: Uninfo, idx: int) -> str | None:
        """获取群欢迎消息

        参数:
            session: EventSession:
            id: 消息ID

        返回:
            list: 消息内容
        """
        path = cls.get_path(session)
        json_data = cls.__get_data(session)
        if not json_data or not path:
            return None
        file = path / "text.json"
        key_list = list(json_data.keys())
        if idx < 0 or idx >= len(key_list):
            return None
        old_msg = str(UniMessage().load(json_data[key_list[idx]]["message"]))
        for msg in json_data[key_list[idx]]["message"]:
            if msg["type"] == "image" and msg["path"]:
                image_path = Path(msg["path"])
                if image_path.exists():
                    image_path.unlink()
        del json_data[key_list[idx]]
        with file.open("w", encoding="utf8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=4)
        return f"删除群组欢迎消息成功！消息内容: {old_msg}"
