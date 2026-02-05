import base64
from io import BytesIO
from pathlib import Path
import random
from typing import ClassVar

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot_plugin_alconna import (
    At,
    AtAll,
    Button,
    CustomNode,
    Image,
    Reference,
    Text,
    UniMessage,
    Video,
    Voice,
)
from pydantic import BaseModel
import ujson as json

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils._build_image import BuildImage

driver = nonebot.get_driver()

MESSAGE_TYPE = (
    str
    | int
    | float
    | Path
    | bytes
    | BytesIO
    | BuildImage
    | At
    | AtAll
    | Image
    | Text
    | Voice
    | Video
    | Button
)


class Config(BaseModel):
    image_to_bytes: bool = False


class MessageUtils:
    # 可爱风格的失败提示消息列表
    FAILURE_MESSAGES: ClassVar[list[str]] = [
        "出了点小问题，待会再试试吧~ (´・ω・`)",
        "哎呀，失败了呢 QAQ",
        "好像哪里不对劲... (・∀・；)",
        "emm...出错了 (；′⌒`)",
        "失败了，但问题不大！╮(╯▽╰)╭",
        "搞砸了...下次一定行 (๑•̀ㅂ•́)و✧",
        "这次没成功，再来一次？",
        "出错啦，让我缓缓... (´-ω-`)",
        "翻车了，容我想想 ( ˘•ω•˘ )",
        "不太顺利呢，稍后再试吧 (´;ω;`)",
        "失败了...不过没关系啦 (・ω<)☆",
        "呃，出了点状况 Σ(っ°Д°;)っ",
        "没搞定，但别灰心~",
        "坏掉了...等等再试试看 (>_<)",
        "这次不太行，下次加油！(ง •_•)ง",
    ]

    @classmethod
    def get_failure_message(cls) -> str:
        """获取随机失败提示消息

        返回:
            str: 随机的可爱失败提示消息
        """
        return random.choice(cls.FAILURE_MESSAGES)

    @classmethod
    def build_failure_message(cls) -> UniMessage:
        """构造随机失败提示消息

        返回:
            UniMessage: 构造完成的失败提示消息
        """
        return cls.build_message(cls.get_failure_message())

    @classmethod
    def __build_message(
        cls, msg_list: list[MESSAGE_TYPE], format_args: dict | None = None
    ) -> list[Text | Image]:
        """构造消息

        参数:
            msg_list: 消息列表
            format_args: 用于格式化字符串的参数字典.

        返回:
            list[Text | Text]: 构造完成的消息列表
        """
        config = nonebot.get_plugin_config(Config)
        message_list = []
        for msg in msg_list:
            if isinstance(msg, str):
                if msg.startswith("base64://"):
                    message_list.append(Image(raw=BytesIO(base64.b64decode(msg[9:]))))
                else:
                    formatted_msg = msg
                    if format_args:
                        try:
                            formatted_msg = msg.format_map(format_args)
                        except (KeyError, IndexError) as e:
                            logger.debug(
                                f"格式化字符串 '{msg}' 失败 ({e})，将使用原始文本。"
                            )
                    message_list.append(Text(formatted_msg))
            elif isinstance(msg, int | float):
                message_list.append(Text(str(msg)))
            elif isinstance(msg, Path):
                if msg.exists():
                    if config.image_to_bytes:
                        logger.debug("图片转为bytes发送", "MessageUtils")
                        image = BuildImage.open(msg)
                        message_list.append(Image(raw=image.pic2bytes()))
                    else:
                        message_list.append(Image(path=msg))
                else:
                    logger.warning(f"图片路径不存在: {msg}")
            elif isinstance(msg, bytes):
                message_list.append(Image(raw=msg))
            elif isinstance(msg, BytesIO):
                message_list.append(Image(raw=msg))
            elif isinstance(msg, BuildImage):
                message_list.append(Image(raw=msg.pic2bytes()))
            else:
                message_list.append(msg)
        return message_list

    @classmethod
    def build_message(
        cls,
        msg_list: MESSAGE_TYPE | list[MESSAGE_TYPE | list[MESSAGE_TYPE]],
        format_args: dict | None = None,
    ) -> UniMessage:
        """构造消息

        参数:
            msg_list: 消息列表
            format_args: 用于格式化字符串的参数字典.

        返回:
            UniMessage: 构造完成的消息列表
        """
        message_list = []
        if not isinstance(msg_list, list):
            msg_list = [msg_list]
        for m in msg_list:
            _data = m if isinstance(m, list) else [m]
            message_list += cls.__build_message(_data, format_args)
        return UniMessage(message_list)

    @classmethod
    def alc_forward_msg(
        cls,
        msg_list: list,
        uin: str,
        name: str,
    ) -> UniMessage:
        """生成自定义合并消息

        参数:
            msg_list: 消息列表
            uin: 发送者 QQ
            name: 自定义名称

        返回:
            list[dict]: 转发消息
        """
        node_list = []
        for _message in msg_list:
            if isinstance(_message, list):
                for i in range(len(_message.copy())):
                    if isinstance(_message[i], Path):
                        _message[i] = Image(
                            raw=BuildImage.open(_message[i]).pic2bytes()
                        )
                    elif isinstance(_message[i], BuildImage):
                        _message[i] = Image(raw=_message[i].pic2bytes())
            node_list.append(
                CustomNode(uid=uin, name=name, content=UniMessage(_message))
            )
        return UniMessage(Reference(nodes=node_list))

    @classmethod
    def markdown(cls, content: dict) -> Message:
        """markdown格式消息

        参数:
            content: 消息内容

        返回:
            Message: 构造完成的消息
        """
        content_data = base64.b64encode(json.dumps(content).encode("utf-8")).decode(
            "utf-8"
        )
        return Message(f"[CQ:markdown,data=base64://{content_data}]")

    @classmethod
    def custom_forward_msg(
        cls,
        msg_list: list[str | Message],
        uin: str,
        name: str = f"这里是{BotConfig.self_nickname}",
    ) -> list[dict]:
        """生成自定义合并消息

        参数:
            msg_list: 消息列表
            uin: 发送者 QQ
            name: 自定义名称

        返回:
            list[dict]: 转发消息
        """
        mes_list = []
        for _message in msg_list:
            data = {
                "type": "node",
                "data": {
                    "name": name,
                    "uin": f"{uin}",
                    "content": _message,
                },
            }
            mes_list.append(data)
        return mes_list

    @classmethod
    def template2forward(cls, msg_list: list[UniMessage], uni: str) -> list[dict]:
        """模板转转发消息

        参数:
            msg_list: 消息列表
            uni: 发送者qq

        返回:
            list[dict]: 转发消息
        """
        forward_data = []
        for r_list in msg_list:
            s = ""
            if isinstance(r_list, UniMessage | list):
                for r in r_list:
                    if isinstance(r, Text):
                        s += str(r)
                    elif isinstance(r, Image):
                        if v := r.url or r.path or r.raw:
                            s += MessageSegment.image(v)
            elif isinstance(r_list, Image):
                if v := r_list.url or r_list.path:
                    s = MessageSegment.image(v)
            else:
                s = str(r_list)
            forward_data.append(s)
        return cls.custom_forward_msg(forward_data, uni)

    @classmethod
    def template2alc(cls, msg_list: list[MessageSegment]) -> list:
        """模板转alc

        参数:
            msg_list: 消息列表

        返回:
            list: alc模板
        """
        forward_data = []
        for msg in msg_list:
            if isinstance(msg, str):
                forward_data.append(Text(msg))
            elif msg.type == "at":
                if msg.data["qq"] == "0":
                    forward_data.append(AtAll())
                else:
                    forward_data.append(At(flag="user", target=msg.data["qq"]))
            elif msg.type == "image":
                forward_data.append(Image(url=msg.data["file"] or msg.data["url"]))
            elif msg.type == "text" and msg.data["text"]:
                forward_data.append(Text(msg.data["text"]))
        return forward_data
