"""
LLM 模块的工具和转换函数
"""

import base64
import copy
from pathlib import Path

from nonebot.adapters import Message as PlatformMessage
from nonebot_plugin_alconna.uniseg import (
    At,
    File,
    Image,
    Reply,
    Text,
    UniMessage,
    Video,
    Voice,
)

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx

from .types import LLMContentPart


async def unimsg_to_llm_parts(message: UniMessage) -> list[LLMContentPart]:
    """
    将 UniMessage 实例转换为一个 LLMContentPart 列表。
    这是处理多模态输入的核心转换逻辑。

    参数:
        message: 要转换的UniMessage实例。

    返回:
        list[LLMContentPart]: 转换后的内容部分列表。
    """
    parts: list[LLMContentPart] = []
    for seg in message:
        part = None
        if isinstance(seg, Text):
            if seg.text.strip():
                part = LLMContentPart.text_part(seg.text)
        elif isinstance(seg, Image):
            if seg.path:
                part = await LLMContentPart.from_path(seg.path, target_api="gemini")
            elif seg.url:
                part = LLMContentPart.image_url_part(seg.url)
            elif hasattr(seg, "raw") and seg.raw:
                mime_type = (
                    getattr(seg, "mimetype", "image/png")
                    if hasattr(seg, "mimetype")
                    else "image/png"
                )
                if isinstance(seg.raw, bytes):
                    b64_data = base64.b64encode(seg.raw).decode("utf-8")
                    part = LLMContentPart.image_base64_part(b64_data, mime_type)

        elif isinstance(seg, File | Voice | Video):
            if seg.path:
                part = await LLMContentPart.from_path(seg.path)
            elif seg.url:
                try:
                    logger.debug(f"检测到媒体URL，开始下载: {seg.url}")
                    media_bytes = await AsyncHttpx.get_content(seg.url)

                    new_seg = copy.copy(seg)
                    new_seg.raw = media_bytes
                    seg = new_seg
                    logger.debug(f"媒体文件下载成功，大小: {len(media_bytes)} bytes")
                except Exception as e:
                    logger.error(f"从URL下载媒体失败: {seg.url}, 错误: {e}")
                    part = LLMContentPart.text_part(
                        f"[下载媒体失败: {seg.name or seg.url}]"
                    )

                if part:
                    parts.append(part)
                    continue

            if hasattr(seg, "raw") and seg.raw:
                mime_type = getattr(seg, "mimetype", None)
                if isinstance(seg.raw, bytes):
                    b64_data = base64.b64encode(seg.raw).decode("utf-8")

                    if isinstance(seg, Video):
                        if not mime_type:
                            mime_type = "video/mp4"
                        part = LLMContentPart.video_base64_part(
                            data=b64_data, mime_type=mime_type
                        )
                        logger.debug(
                            f"处理视频字节数据: {mime_type}, 大小: {len(seg.raw)} bytes"
                        )
                    elif isinstance(seg, Voice):
                        if not mime_type:
                            mime_type = "audio/wav"
                        part = LLMContentPart.audio_base64_part(
                            data=b64_data, mime_type=mime_type
                        )
                        logger.debug(
                            f"处理音频字节数据: {mime_type}, 大小: {len(seg.raw)} bytes"
                        )
                    else:
                        part = LLMContentPart.text_part(
                            f"[FILE: {mime_type or 'unknown'}, {len(seg.raw)} bytes]"
                        )
                        logger.debug(
                            f"处理其他文件字节数据: {mime_type}, "
                            f"大小: {len(seg.raw)} bytes"
                        )

        elif isinstance(seg, At):
            if seg.flag == "all":
                part = LLMContentPart.text_part("[Mentioned Everyone]")
            else:
                part = LLMContentPart.text_part(f"[Mentioned user: {seg.target}]")

        elif isinstance(seg, Reply):
            if seg.msg:
                try:
                    extract_method = getattr(seg.msg, "extract_plain_text", None)
                    if extract_method and callable(extract_method):
                        reply_text = str(extract_method()).strip()
                    else:
                        reply_text = str(seg.msg).strip()
                    if reply_text:
                        part = LLMContentPart.text_part(
                            f'[Replied to: "{reply_text[:50]}..."]'
                        )
                except Exception:
                    part = LLMContentPart.text_part("[Replied to a message]")

        if part:
            parts.append(part)

    return parts


def create_multimodal_message(
    text: str | None = None,
    images: list[str | Path | bytes] | str | Path | bytes | None = None,
    videos: list[str | Path | bytes] | str | Path | bytes | None = None,
    audios: list[str | Path | bytes] | str | Path | bytes | None = None,
    image_mimetypes: list[str] | str | None = None,
    video_mimetypes: list[str] | str | None = None,
    audio_mimetypes: list[str] | str | None = None,
) -> UniMessage:
    """
    创建多模态消息的便捷函数

    参数:
        text: 文本内容
        images: 图片数据，支持路径、字节数据或URL
        videos: 视频数据
        audios: 音频数据
        image_mimetypes: 图片MIME类型，bytes数据时需要指定
        video_mimetypes: 视频MIME类型，bytes数据时需要指定
        audio_mimetypes: 音频MIME类型，bytes数据时需要指定

    返回:
        UniMessage: 构建好的多模态消息
    """
    message = UniMessage()

    if text:
        message.append(Text(text))

    if images is not None:
        _add_media_to_message(message, images, image_mimetypes, Image, "image/png")

    if videos is not None:
        _add_media_to_message(message, videos, video_mimetypes, Video, "video/mp4")

    if audios is not None:
        _add_media_to_message(message, audios, audio_mimetypes, Voice, "audio/wav")

    return message


def _add_media_to_message(
    message: UniMessage,
    media_items: list[str | Path | bytes] | str | Path | bytes,
    mimetypes: list[str] | str | None,
    media_class: type,
    default_mimetype: str,
) -> None:
    """添加媒体文件到 UniMessage"""
    if not isinstance(media_items, list):
        media_items = [media_items]

    mime_list = []
    if mimetypes is not None:
        if isinstance(mimetypes, str):
            mime_list = [mimetypes] * len(media_items)
        else:
            mime_list = list(mimetypes)

    for i, item in enumerate(media_items):
        if isinstance(item, str | Path):
            if str(item).startswith(("http://", "https://")):
                message.append(media_class(url=str(item)))
            else:
                message.append(media_class(path=Path(item)))
        elif isinstance(item, bytes):
            mimetype = mime_list[i] if i < len(mime_list) else default_mimetype
            message.append(media_class(raw=item, mimetype=mimetype))


def message_to_unimessage(message: PlatformMessage) -> UniMessage:
    """
    将平台特定的 Message 对象转换为通用的 UniMessage。
    主要用于处理引用消息等未被自动转换的消息体。

    参数:
        message: 平台特定的Message对象。

    返回:
        UniMessage: 转换后的通用消息对象。
    """
    uni_segments = []
    for seg in message:
        if seg.type == "text":
            uni_segments.append(Text(seg.data.get("text", "")))
        elif seg.type == "image":
            uni_segments.append(Image(url=seg.data.get("url")))
        elif seg.type == "record":
            uni_segments.append(Voice(url=seg.data.get("url")))
        elif seg.type == "video":
            uni_segments.append(Video(url=seg.data.get("url")))
        elif seg.type == "at":
            uni_segments.append(At("user", str(seg.data.get("qq", ""))))
        else:
            logger.debug(f"跳过不支持的平台消息段类型: {seg.type}")

    return UniMessage(uni_segments)


def _sanitize_request_body_for_logging(body: dict) -> dict:
    """
    净化请求体用于日志记录，移除大数据字段并添加摘要信息

    参数:
        body: 原始请求体字典。

    返回:
        dict: 净化后的请求体字典。
    """
    try:
        sanitized_body = copy.deepcopy(body)

        if "contents" in sanitized_body and isinstance(
            sanitized_body["contents"], list
        ):
            for content_item in sanitized_body["contents"]:
                if "parts" in content_item and isinstance(content_item["parts"], list):
                    media_summary = []
                    new_parts = []
                    for part in content_item["parts"]:
                        if "inlineData" in part and isinstance(
                            part["inlineData"], dict
                        ):
                            data = part["inlineData"].get("data")
                            if isinstance(data, str):
                                mime_type = part["inlineData"].get(
                                    "mimeType", "unknown"
                                )
                                media_summary.append(f"{mime_type} ({len(data)} chars)")
                                continue
                        new_parts.append(part)

                    if media_summary:
                        summary_text = (
                            f"[多模态内容: {len(media_summary)}个文件 - "
                            f"{', '.join(media_summary)}]"
                        )
                        new_parts.insert(0, {"text": summary_text})

                    content_item["parts"] = new_parts

        return sanitized_body
    except Exception as e:
        logger.warning(f"日志净化失败: {e}，将记录原始请求体。")
        return body
