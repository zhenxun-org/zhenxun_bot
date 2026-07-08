from collections import defaultdict
from collections.abc import Awaitable, Callable
import inspect
from io import BytesIO
import mimetypes
from pathlib import Path
from typing import Any, ClassVar, TypeVar, cast

import anyio
from nonebot.adapters import Bot, Event
from nonebot.adapters import Message as PlatformMessage
from nonebot.matcher import current_bot, current_event, current_matcher
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import (
    At,
    AtAll,
    Audio,
    Image,
    Reply,
    Segment,
    Text,
    Video,
    Voice,
)
from nonebot_plugin_alconna.uniseg.tools import image_fetch, reply_fetch
from PIL.Image import Image as PILImageType

from zhenxun.services.ai.core.engine.context_renderer import ContextConverter
from zhenxun.services.ai.core.messages import (
    AgentEvent,
    AudioPart,
    BaseContentPart,
    EmbedBatch,
    EmbedPayload,
    FilePart,
    ImagePart,
    LLMContentPart,
    LLMMessage,
    PromptInput,
    SystemMessage,
    TextPart,
    UserContentUnion,
    VideoPart,
)
from zhenxun.services.ai.core.options import LLMEmbeddingConfig
from zhenxun.services.ai.run import get_current_run_context
from zhenxun.services.ai.utils.logger import log_core as logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.pydantic_compat import TypeAdapter, model_copy, model_dump
from zhenxun.utils.utils import infer_plugin_namespace

S = TypeVar("S", bound=Segment)


class MessageBuilder:
    """
    平台消息与 LLM 内部结构转换的 Builder 门面。
    实现 Nonebot/Alconna 生态与底层 LLM 核心（types/messages）的绝对解耦。
    """

    _MESSAGE_CONVERTERS: ClassVar[
        dict[type, dict[str, Callable[..., Awaitable[list[LLMMessage]]]]]
    ] = defaultdict(dict)

    _SEGMENT_HANDLERS: ClassVar[
        dict[
            type[Segment],
            dict[
                str,
                Callable[
                    [Any], Awaitable[LLMContentPart | list[LLMContentPart] | None]
                ],
            ],
        ]
    ] = defaultdict(dict)

    @classmethod
    def register_segment_handler(cls, seg_type: type[S], scope: str | None = None):
        """装饰器：注册 Uniseg 消息段的处理器"""
        ns = scope if scope is not None else infer_plugin_namespace()

        def decorator(
            func: Callable[
                [S], Awaitable[LLMContentPart | list[LLMContentPart] | None]
            ],
        ):
            cls._SEGMENT_HANDLERS[seg_type][ns] = func
            return func

        return decorator

    @classmethod
    def register_message_converter(cls, msg_type: type, scope: str | None = None):
        """装饰器：注册全局消息体类型的转换器"""
        ns = scope if scope is not None else infer_plugin_namespace()

        def decorator(func: Callable[..., Awaitable[list[LLMMessage]]]):
            cls._MESSAGE_CONVERTERS[msg_type][ns] = func
            return func

        return decorator

    @classmethod
    async def content_part_from_path(
        cls, path_like: str | Path, target_api: str | None = None
    ) -> LLMContentPart | None:
        """将本地路径读取为多态消息部件"""
        try:
            aio_path = anyio.Path(path_like)
            if not await aio_path.exists() or not await aio_path.is_file():
                logger.warning(f"文件不存在或不是一个文件: {path_like}")
                return None

            std_path = Path(path_like)
            resolved_aio_path = await aio_path.absolute()
            _ = resolved_aio_path

            mime_type, _ = mimetypes.guess_type(str(std_path))
            file_name = std_path.name

            if not mime_type:
                logger.warning(
                    f"无法猜测文件 {file_name} 的MIME类型，尝试作为文本处理。"
                )
                try:
                    async with await anyio.open_file(aio_path, encoding="utf-8") as f:
                        text_content = await f.read()
                    return TextPart(text=text_content)
                except Exception as e:
                    logger.error(f"读取文本文件 {file_name} 失败: {e}")
                    return None

            if mime_type.startswith("image/"):
                return ImagePart(path=std_path, mime_type=mime_type)
            elif mime_type.startswith("audio/"):
                return AudioPart(path=std_path, mime_type=mime_type)
            elif mime_type.startswith("video/"):
                return VideoPart(path=std_path, mime_type=mime_type)
            elif mime_type.startswith("text/") or mime_type in (
                "application/json",
                "application/xml",
            ):
                try:
                    async with await anyio.open_file(aio_path, encoding="utf-8") as f:
                        text_content = await f.read()
                    return TextPart(text=text_content)
                except Exception as e:
                    logger.error(f"读取文本类文件 {file_name} 失败: {e}")
                    return None
            else:
                return FilePart(
                    path=std_path,
                    mime_type=mime_type,
                    metadata={"name": file_name, "source": "local_path"},
                )
        except Exception as e:
            logger.error(f"从路径 {path_like} 创建 ContentPart 时出错: {e}")
            return None

    @classmethod
    async def _transform_to_content_part(cls, item: Any) -> UserContentUnion:
        """将任意输入项转换为符合 LLM 规范的内容部件"""
        if isinstance(item, BaseContentPart):
            return TypeAdapter(UserContentUnion).validate_python(model_dump(item))
        if isinstance(item, str):
            return TextPart(text=item)
        if isinstance(item, Path):
            part = await cls.content_part_from_path(item)
            if part is None:
                raise ValueError(f"无法从路径加载内容: {item}")
            return cast(UserContentUnion, part)
        if isinstance(item, dict):
            return TypeAdapter(UserContentUnion).validate_python(item)
        if PILImageType and isinstance(item, PILImageType):
            buffer = BytesIO()
            fmt = item.format or "PNG"
            item.save(buffer, format=fmt)
            mime_type = f"image/{fmt.lower()}"
            return ImagePart(raw=buffer.getvalue(), mime_type=mime_type)
        raise TypeError(f"不支持的输入类型用于构建 ContentPart: {type(item)}")

    @classmethod
    async def unimsg_to_llm_parts(
        cls,
        message: UniMessage,
        namespace: str | None = None,
        allowed_modalities: set[str] | None = None,
    ) -> list[UserContentUnion]:
        """将 UniMessage 消息解析并转换为 LLM 内容部件列表"""
        namespace = namespace or infer_plugin_namespace(default="global")
        parts: list[UserContentUnion] = []
        for seg in message:
            if allowed_modalities is not None:
                if isinstance(seg, Image) and "image" not in allowed_modalities:
                    continue
                if isinstance(seg, Audio | Voice) and "audio" not in allowed_modalities:
                    continue
                if isinstance(seg, Video) and "video" not in allowed_modalities:
                    continue
                if (
                    getattr(seg, "__class__", type).__name__ == "File"
                    and "file" not in allowed_modalities
                ):
                    continue

            handler_dict = cls._SEGMENT_HANDLERS.get(type(seg), {})
            handler = handler_dict.get(namespace) or handler_dict.get("global")
            if handler:
                try:
                    part = await handler(seg)

                    if part:
                        if isinstance(part, list):
                            parts.extend(cast(list[UserContentUnion], part))
                        else:
                            parts.append(cast(UserContentUnion, part))
                except Exception as e:
                    logger.warning(f"处理消息段 {seg} 失败: {e}", "LLMUtils")
        merged_parts: list[UserContentUnion] = []
        for part in parts:
            if (
                isinstance(part, TextPart)
                and merged_parts
                and isinstance(merged_parts[-1], TextPart)
            ):
                merged_parts[-1].text += part.text
            else:
                merged_parts.append(part)
        return merged_parts

    @classmethod
    async def _fetch_reply_as_parts(
        cls,
        bot: "Bot",
        event: "Event",
        namespace: str | None = None,
        allowed_modalities: set[str] | None = None,
    ) -> list[LLMContentPart] | None:
        """获取并解析引用消息的内容片段"""
        namespace = namespace or infer_plugin_namespace(default="global")
        try:
            orig_msg = await reply_fetch(event, bot)
            if not orig_msg or not orig_msg.msg:
                return None

            orig_content = orig_msg.msg
            if isinstance(orig_content, PlatformMessage):
                uni_msg = cls.message_to_unimessage(orig_content)
            else:
                uni_msg = UniMessage.text(str(orig_content))

            uni_msg = uni_msg.exclude(Reply)

            parts = await cls.unimsg_to_llm_parts(
                uni_msg, namespace=namespace, allowed_modalities=allowed_modalities
            )
            if not parts:
                return None

            if isinstance(parts[0], TextPart):
                parts[0].text = f"[引用] {parts[0].text}"
            else:
                parts.insert(0, TextPart(text="[引用] "))
            return cast(list[LLMContentPart], parts)
        except Exception as e:
            logger.debug(f"拉取引用消息失败: {e}")
            return None

    @classmethod
    async def normalize_to_llm_messages(
        cls,
        message: PromptInput,
        instruction: str | None = None,
        bot: Bot | None = None,
        event: Event | None = None,
        namespace: str | None = None,
        allowed_modalities: set[str] | None = None,
    ) -> list[LLMMessage]:
        """将任意类型的提示输入标准化为统一的 LLM 消息历史列表"""
        namespace = namespace or infer_plugin_namespace(default="global")
        messages = []
        if instruction:
            messages.append(SystemMessage(content=[TextPart(text=instruction)]))

        reply_parts = []
        try:
            bot_inst = bot or current_bot.get(None)
            event_inst = event or current_event.get(None)

            if not bot_inst or not event_inst:
                try:
                    ctx = get_current_run_context()
                    if ctx:
                        bot_inst = bot_inst or ctx.get_bot()
                        event_inst = event_inst or ctx.get_event()
                except Exception:
                    pass

            should_fetch_reply = True
            if isinstance(message, UniMessage) and message.has(Reply):
                should_fetch_reply = False

            if bot_inst and event_inst and should_fetch_reply:
                parts = await cls._fetch_reply_as_parts(
                    bot_inst,
                    event_inst,
                    namespace=namespace,
                    allowed_modalities=allowed_modalities,
                )
                if parts:
                    reply_parts = parts
        except Exception as e:
            logger.debug(f"全局语义增强提取引用失败 (静默跳过): {e}")

        converted_msgs: list[LLMMessage] = []
        converted = False

        for msg_type, converter_dict in cls._MESSAGE_CONVERTERS.items():
            if isinstance(message, msg_type):
                converter = converter_dict.get(namespace) or converter_dict.get(
                    "global"
                )
                if not converter:
                    continue
                sig = inspect.signature(converter)
                if "allowed_modalities" in sig.parameters:
                    converted_msgs = await converter(
                        message, allowed_modalities=allowed_modalities
                    )
                else:
                    converted_msgs = await converter(message)
                converted = True
                break

        if not converted:
            if isinstance(message, LLMMessage):
                converted_msgs = [message]
            elif isinstance(message, list) and all(
                isinstance(m, LLMMessage) for m in message
            ):
                converted_msgs = cast(list[LLMMessage], list(message))
            elif isinstance(message, str):
                converted_msgs = [LLMMessage.user(message)]
            elif isinstance(message, AgentEvent):
                converted_msgs = ContextConverter.flatten_to_llm_messages([message])
            elif isinstance(message, list):
                parts = []
                for item in message:
                    parts.append(await cls._transform_to_content_part(item))
                converted_msgs = [LLMMessage.user(parts)]
            else:
                raise TypeError(f"不支持的消息类型: {type(message)}")

        if reply_parts:
            for i, msg in enumerate(converted_msgs):
                if getattr(msg, "role", None) == "user":
                    new_msg = model_copy(msg, deep=True)
                    new_msg.content = cast(list[Any], reply_parts) + new_msg.content
                    converted_msgs[i] = new_msg
                    break
            else:
                converted_msgs.insert(
                    0, LLMMessage.user(cast(list[UserContentUnion], reply_parts))
                )

        messages.extend(converted_msgs)
        return messages

    @classmethod
    def message_to_unimessage(cls, message: PlatformMessage) -> UniMessage:
        """将平台原生 Message 转换为统一的 UniMessage"""
        return UniMessage.of(message)

    @classmethod
    async def _extract_parts_for_embed(
        cls,
        item: Any,
        bot: Bot | None = None,
        event: Event | None = None,
        namespace: str | None = None,
        config: LLMEmbeddingConfig | None = None,
    ) -> list[LLMContentPart]:
        """为 Embed 向量化提取纯粹的内容片段，忽略杂项"""
        namespace = namespace or infer_plugin_namespace(default="global")
        allowed_modalities = {"text"}
        if config:
            if config.multimodal is True:
                allowed_modalities = None
            elif isinstance(config.multimodal, list):
                allowed_modalities = set(config.multimodal)
                allowed_modalities.add("text")
            elif config.multimodal is False:
                allowed_modalities = {"text"}

        messages = await cls.normalize_to_llm_messages(
            item,
            bot=bot,
            event=event,
            namespace=namespace,
            allowed_modalities=allowed_modalities,
        )
        parts = []
        for msg in messages:
            for part in msg.content:
                if isinstance(
                    part, TextPart | ImagePart | AudioPart | VideoPart | FilePart
                ):
                    parts.append(part)
        return parts

    @classmethod
    async def normalize_to_embed_batch(
        cls,
        inputs: Any,
        bot: Bot | None = None,
        event: Event | None = None,
        namespace: str | None = None,
        config: LLMEmbeddingConfig | None = None,
    ) -> "EmbedBatch":
        """将任意输入标准化为嵌入向量批处理对象"""
        namespace = namespace or infer_plugin_namespace(default="global")

        if isinstance(inputs, list) and not isinstance(inputs, UniMessage):
            if not inputs:
                return EmbedBatch(payloads=[])
            if isinstance(inputs[0], BaseContentPart):
                return EmbedBatch(payloads=[EmbedPayload(parts=inputs)])

            batch = EmbedBatch(payloads=[])
            for item in inputs:
                parts = await cls._extract_parts_for_embed(
                    item, bot, event, namespace, config
                )
                if parts:
                    batch.payloads.append(EmbedPayload(parts=parts))
                else:
                    fallback_parts: list[LLMContentPart] = [TextPart(text=" ")]
                    batch.payloads.append(EmbedPayload(parts=fallback_parts))
            return batch

        else:
            parts = await cls._extract_parts_for_embed(
                inputs, bot, event, namespace, config
            )
            if not parts:
                fallback_parts: list[LLMContentPart] = [TextPart(text=" ")]
                parts = fallback_parts
            return EmbedBatch(payloads=[EmbedPayload(parts=parts)])


@MessageBuilder.register_message_converter(UniMessage, scope="global")
async def _convert_unimessage(
    msg: UniMessage, allowed_modalities: set[str] | None = None
) -> list[LLMMessage]:
    content_parts = await MessageBuilder.unimsg_to_llm_parts(
        msg, allowed_modalities=allowed_modalities
    )
    return [LLMMessage.user(content_parts)]


@MessageBuilder.register_segment_handler(Text, scope="global")
async def _handle_text(seg: Text) -> TextPart | None:
    return TextPart(text=seg.text) if seg.text.strip() else None


def _extract_media_kwargs(seg: Segment, default_mime: str) -> dict | None:
    """提取媒体 Segment 的公共属性字典，消除冗余解析"""
    mime_type = getattr(seg, "mimetype", None) or default_mime

    raw_data = getattr(seg, "raw", None)
    if raw_data:
        return {
            "raw": raw_data if isinstance(raw_data, bytes) else raw_data.read(),
            "mime_type": mime_type,
        }

    path_data = getattr(seg, "path", None)
    if path_data is not None:
        return {"path": Path(str(path_data)), "mime_type": mime_type}

    url_data = getattr(seg, "url", None)
    if url_data:
        return {"url": url_data, "mime_type": mime_type}

    return None


@MessageBuilder.register_segment_handler(Image, scope="global")
async def _handle_image(seg: Image) -> ImagePart | None:
    if not seg.raw and not getattr(seg, "path", None):
        try:
            bot = current_bot.get(None)
            event = current_event.get(None)
            matcher = current_matcher.get(None)

            if bot and event and matcher:
                logger.debug("MessageBuilder 正在底层静默拉取图片实体...")
                raw_bytes = await image_fetch(event, bot, matcher.state, seg)
                if raw_bytes:
                    seg.raw = raw_bytes
        except Exception as e:
            logger.debug(f"底层静默水合下载图片失败: {e}")

        if not seg.raw and seg.url:
            try:
                logger.debug(f"正在从临时 URL 物理固化图片: {seg.url[:50]}...")
                raw_bytes = await AsyncHttpx.get_content(seg.url)
                if raw_bytes:
                    seg.raw = raw_bytes
                    seg.url = None
            except Exception as e:
                logger.warning(f"固化图片 URL 失败: {e}")

    kwargs = _extract_media_kwargs(seg, "image/png")
    return ImagePart(**kwargs) if kwargs else None


async def _process_audio_seg(seg: Audio | Voice) -> AudioPart | None:
    if not seg.raw and not getattr(seg, "path", None) and seg.url:
        try:
            raw_bytes = await AsyncHttpx.get_content(seg.url)
            if raw_bytes:
                seg.raw = raw_bytes
                seg.url = None
        except Exception:
            pass
    kwargs = _extract_media_kwargs(seg, "audio/mp3")
    return AudioPart(**kwargs) if kwargs else None


@MessageBuilder.register_segment_handler(Audio, scope="global")
async def _handle_audio(seg: Audio) -> AudioPart | None:
    return await _process_audio_seg(seg)


@MessageBuilder.register_segment_handler(Voice, scope="global")
async def _handle_voice(seg: Voice) -> AudioPart | None:
    return await _process_audio_seg(seg)


@MessageBuilder.register_segment_handler(Video, scope="global")
async def _handle_video(seg: Video) -> VideoPart | None:
    if not seg.raw and not getattr(seg, "path", None) and seg.url:
        try:
            raw_bytes = await AsyncHttpx.get_content(seg.url)
            if raw_bytes:
                seg.raw = raw_bytes
                seg.url = None
        except Exception:
            pass
    kwargs = _extract_media_kwargs(seg, "video/mp4")
    return VideoPart(**kwargs) if kwargs else None


@MessageBuilder.register_segment_handler(Reply, scope="global")
async def _handle_reply(seg: Reply) -> list[LLMContentPart] | LLMContentPart | None:
    try:
        bot = current_bot.get(None)
        event = current_event.get(None)
        if not bot or not event:
            return None

        ctx = get_current_run_context()
        ns = getattr(ctx.session, "namespace", "global") if ctx else "global"

        return await MessageBuilder._fetch_reply_as_parts(bot, event, namespace=ns)
    except Exception as e:
        logger.warning(f"拉取引用消息代理失败: {e}")
        return None


@MessageBuilder.register_segment_handler(AtAll, scope="global")
async def _handle_at_all(seg: AtAll) -> TextPart:
    return TextPart(text="[@全体成员] ")


@MessageBuilder.register_segment_handler(At, scope="global")
async def _handle_at(seg: At) -> TextPart:
    if seg.display:
        return TextPart(text=f"[@{seg.display}] ")

    target_id = seg.target
    try:
        bot = current_bot.get(None)
        event = current_event.get(None)

        nickname = str(target_id)
        if bot and event:
            group_id = getattr(event, "group_id", None)
            if group_id and hasattr(bot, "get_group_member_info"):
                info = await bot.get_group_member_info(
                    group_id=group_id, user_id=int(target_id)
                )
                nickname = info.get("card") or info.get("nickname") or nickname
            elif hasattr(bot, "get_stranger_info"):
                info = await bot.get_stranger_info(user_id=int(target_id))
                nickname = info.get("nickname") or nickname

        return TextPart(text=f"[@{nickname}] ")
    except Exception:
        return TextPart(text=f"[@{target_id}] ")
