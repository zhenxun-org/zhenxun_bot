import base64
import json
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import Message as V11Message
from nonebot.adapters.onebot.v11 import MessageSegment as V11MessageSegment
from nonebot.exception import ActionFailed
import nonebot_plugin_alconna as alc
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg.segment import (
    At,
    AtAll,
    CustomNode,
    Image,
    Reference,
    Reply,
    Text,
    Video,
)
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_session import EventSession

from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.message import MessageUtils

from .broadcast_manager import BroadcastManager

MAX_FORWARD_DEPTH = 3


async def _process_forward_content(
    forward_content: Any, forward_id: str | None, bot: Bot, depth: int
) -> list[CustomNode]:
    """处理转发消息内容"""
    nodes_for_alc = []
    content_parsed = False

    if forward_content:
        nodes_from_content = None
        if isinstance(forward_content, list):
            nodes_from_content = forward_content
        elif isinstance(forward_content, str):
            try:
                parsed_content = json.loads(forward_content)
                if isinstance(parsed_content, list):
                    nodes_from_content = parsed_content
            except Exception as json_e:
                logger.debug(
                    f"[Depth {depth}] JSON解析失败: {json_e}",
                    "广播",
                )

        if nodes_from_content is not None:
            logger.debug(
                f"[D{depth}] 节点数: {len(nodes_from_content)}",
                "广播",
            )
            content_parsed = True
            for node_data in nodes_from_content:
                node = await _create_custom_node_from_data(node_data, bot, depth + 1)
                if node:
                    nodes_for_alc.append(node)

    if not content_parsed and forward_id:
        logger.debug(
            f"[D{depth}] 尝试API调用ID: {forward_id}",
            "广播",
        )
        try:
            forward_data = await bot.call_api("get_forward_msg", id=forward_id)
            nodes_list = None

            if isinstance(forward_data, dict) and "messages" in forward_data:
                nodes_list = forward_data["messages"]
            elif (
                isinstance(forward_data, dict)
                and "data" in forward_data
                and isinstance(forward_data["data"], dict)
                and "message" in forward_data["data"]
            ):
                nodes_list = forward_data["data"]["message"]
            elif isinstance(forward_data, list):
                nodes_list = forward_data

            if nodes_list:
                node_count = len(nodes_list)
                logger.debug(
                    f"[D{depth + 1}] 节点:{node_count}",
                    "广播",
                )
                for node_data in nodes_list:
                    node = await _create_custom_node_from_data(
                        node_data, bot, depth + 1
                    )
                    if node:
                        nodes_for_alc.append(node)
            else:
                logger.warning(
                    f"[D{depth + 1}] ID:{forward_id}无节点",
                    "广播",
                )
                nodes_for_alc.append(
                    CustomNode(
                        uid="0",
                        name="错误",
                        content="[嵌套转发消息获取失败]",
                    )
                )
        except ActionFailed as af_e:
            logger.error(
                f"[D{depth + 1}] API失败: {af_e}",
                "广播",
                e=af_e,
            )
            nodes_for_alc.append(
                CustomNode(
                    uid="0",
                    name="错误",
                    content="[嵌套转发消息获取失败]",
                )
            )
        except Exception as e:
            logger.error(
                f"[D{depth + 1}] 处理出错: {e}",
                "广播",
                e=e,
            )
            nodes_for_alc.append(
                CustomNode(
                    uid="0",
                    name="错误",
                    content="[处理嵌套转发时出错]",
                )
            )
    elif not content_parsed and not forward_id:
        logger.warning(
            f"[D{depth}] 转发段无内容也无ID",
            "广播",
        )
        nodes_for_alc.append(
            CustomNode(
                uid="0",
                name="错误",
                content="[嵌套转发消息无法解析]",
            )
        )
    elif content_parsed and not nodes_for_alc:
        logger.warning(
            f"[D{depth}] 解析成功但无有效节点",
            "广播",
        )
        nodes_for_alc.append(
            CustomNode(
                uid="0",
                name="信息",
                content="[嵌套转发内容为空]",
            )
        )

    return nodes_for_alc


async def _create_custom_node_from_data(
    node_data: dict, bot: Bot, depth: int
) -> CustomNode | None:
    """从节点数据创建CustomNode"""
    node_content_raw = node_data.get("message") or node_data.get("content")
    if not node_content_raw:
        logger.warning(f"[D{depth}] 节点缺少消息内容", "广播")
        return None

    sender = node_data.get("sender", {})
    uid = str(sender.get("user_id", "10000"))
    name = sender.get("nickname", f"用户{uid[:4]}")

    extracted_uni_msg = await _extract_content_from_message(
        node_content_raw, bot, depth
    )
    if not extracted_uni_msg:
        return None

    return CustomNode(uid=uid, name=name, content=extracted_uni_msg)


async def _extract_broadcast_content(
    bot: Bot,
    event: Event,
    arp: alc.Arparma,
    session: EventSession,
) -> UniMessage | None:
    """从命令参数或引用消息中提取广播内容"""
    broadcast_content_msg: UniMessage | None = None

    command_content_list = arp.all_matched_args.get("content", [])

    processed_command_list = []
    has_command_content = False

    if command_content_list:
        for item in command_content_list:
            if isinstance(item, alc.Segment):
                processed_command_list.append(item)
                if not (isinstance(item, Text) and not item.text.strip()):
                    has_command_content = True
            elif isinstance(item, str):
                if item.strip():
                    processed_command_list.append(Text(item.strip()))
                    has_command_content = True
            else:
                logger.warning(
                    f"Unexpected type in command content: {type(item)}", "广播"
                )

    if has_command_content:
        logger.debug("检测到命令参数内容，优先使用参数内容", "广播", session=session)
        broadcast_content_msg = UniMessage(processed_command_list)

        if not broadcast_content_msg.filter(
            lambda x: not (isinstance(x, Text) and not x.text.strip())
        ):
            logger.warning(
                "命令参数内容解析后为空或只包含空白", "广播", session=session
            )
            broadcast_content_msg = None

    if not broadcast_content_msg:
        reply_segment_obj: Reply | None = await reply_fetch(event, bot)
        if (
            reply_segment_obj
            and hasattr(reply_segment_obj, "msg")
            and reply_segment_obj.msg
        ):
            logger.debug(
                "未检测到有效命令参数，检测到引用消息", "广播", session=session
            )
            raw_quoted_content = reply_segment_obj.msg
            is_forward = False
            forward_id = None

            if isinstance(raw_quoted_content, V11Message):
                for seg in raw_quoted_content:
                    if isinstance(seg, V11MessageSegment):
                        if seg.type == "forward":
                            forward_id = seg.data.get("id")
                            is_forward = bool(forward_id)
                            break
                        elif seg.type == "json":
                            try:
                                json_data_str = seg.data.get("data", "{}")
                                if isinstance(json_data_str, str):
                                    import json

                                    json_data = json.loads(json_data_str)
                                    if (
                                        json_data.get("app") == "com.tencent.multimsg"
                                        or json_data.get("view") == "Forward"
                                    ) and json_data.get("meta", {}).get(
                                        "detail", {}
                                    ).get("resid"):
                                        forward_id = json_data["meta"]["detail"][
                                            "resid"
                                        ]
                                        is_forward = True
                                        break
                            except Exception:
                                pass

            if is_forward and forward_id:
                logger.info(
                    f"尝试获取并构造合并转发内容 (ID: {forward_id})",
                    "广播",
                    session=session,
                )
                nodes_to_forward: list[CustomNode] = []
                try:
                    forward_data = await bot.call_api("get_forward_msg", id=forward_id)
                    nodes_list = None
                    if isinstance(forward_data, dict) and "messages" in forward_data:
                        nodes_list = forward_data["messages"]
                    elif (
                        isinstance(forward_data, dict)
                        and "data" in forward_data
                        and isinstance(forward_data["data"], dict)
                        and "message" in forward_data["data"]
                    ):
                        nodes_list = forward_data["data"]["message"]
                    elif isinstance(forward_data, list):
                        nodes_list = forward_data

                    if nodes_list is not None:
                        for node_data in nodes_list:
                            node_sender = node_data.get("sender", {})
                            node_user_id = str(node_sender.get("user_id", "10000"))
                            node_nickname = node_sender.get(
                                "nickname", f"用户{node_user_id[:4]}"
                            )
                            node_content_raw = node_data.get(
                                "message"
                            ) or node_data.get("content")
                            if node_content_raw:
                                extracted_node_uni_msg = (
                                    await _extract_content_from_message(
                                        node_content_raw, bot
                                    )
                                )
                                if extracted_node_uni_msg:
                                    nodes_to_forward.append(
                                        CustomNode(
                                            uid=node_user_id,
                                            name=node_nickname,
                                            content=extracted_node_uni_msg,
                                        )
                                    )
                    if nodes_to_forward:
                        broadcast_content_msg = UniMessage(
                            Reference(nodes=nodes_to_forward)
                        )
                except ActionFailed:
                    await MessageUtils.build_message(
                        "获取合并转发消息失败，可能不支持此 API。"
                    ).send(reply_to=True)
                    return None
                except Exception as api_e:
                    logger.error(f"处理合并转发时出错: {api_e}", "广播", e=api_e)
                    await MessageUtils.build_message(
                        "处理合并转发消息时发生内部错误。"
                    ).send(reply_to=True)
                    return None
            else:
                broadcast_content_msg = await _extract_content_from_message(
                    raw_quoted_content, bot
                )
        else:
            logger.debug("未检测到命令参数和引用消息", "广播", session=session)
            await MessageUtils.build_message("请提供广播内容或引用要广播的消息").send(
                reply_to=True
            )
            return None

    if not broadcast_content_msg:
        logger.error(
            "未能从命令参数或引用消息中获取有效的广播内容", "广播", session=session
        )
        await MessageUtils.build_message("错误：未能获取有效的广播内容。").send(
            reply_to=True
        )
        return None

    return broadcast_content_msg


async def _process_v11_segment(
    seg_obj: V11MessageSegment | dict, depth: int, index: int, bot: Bot
) -> list[alc.Segment]:
    """处理V11消息段"""
    result = []
    seg_type = None
    data_dict = None

    if isinstance(seg_obj, V11MessageSegment):
        seg_type = seg_obj.type
        data_dict = seg_obj.data
    elif isinstance(seg_obj, dict):
        seg_type = seg_obj.get("type")
        data_dict = seg_obj.get("data")
    else:
        return result

    if not (seg_type and data_dict is not None):
        logger.warning(f"[D{depth}] 跳过无效数据: {type(seg_obj)}", "广播")
        return result

    if seg_type == "text":
        text_content = data_dict.get("text", "")
        if isinstance(text_content, str) and text_content.strip():
            result.append(Text(text_content))
    elif seg_type == "image":
        img_seg = None
        if data_dict.get("url"):
            img_seg = Image(url=data_dict["url"])
        elif data_dict.get("file"):
            file_val = data_dict["file"]
            if isinstance(file_val, str) and file_val.startswith("base64://"):
                b64_data = file_val[9:]
                raw_bytes = base64.b64decode(b64_data)
                img_seg = Image(raw=raw_bytes)
            else:
                img_seg = Image(path=file_val)
        if img_seg:
            result.append(img_seg)
        else:
            logger.warning(f"[Depth {depth}] V11 图片 {index} 缺少URL/文件", "广播")
    elif seg_type == "at":
        target_qq = data_dict.get("qq", "")
        if target_qq.lower() == "all":
            result.append(AtAll())
        elif target_qq:
            result.append(At(flag="user", target=target_qq))
    elif seg_type == "video":
        video_seg = None
        if data_dict.get("url"):
            video_seg = Video(url=data_dict["url"])
        elif data_dict.get("file"):
            file_val = data_dict["file"]
            if isinstance(file_val, str) and file_val.startswith("base64://"):
                b64_data = file_val[9:]
                raw_bytes = base64.b64decode(b64_data)
                video_seg = Video(raw=raw_bytes)
            else:
                video_seg = Video(path=file_val)
        if video_seg:
            result.append(video_seg)
            logger.debug(f"[Depth {depth}] 处理视频消息成功", "广播")
        else:
            logger.warning(f"[Depth {depth}] V11 视频 {index} 缺少URL/文件", "广播")
    elif seg_type == "forward":
        nested_forward_id = data_dict.get("id") or data_dict.get("resid")
        nested_forward_content = data_dict.get("content")

        logger.debug(f"[D{depth}] 嵌套转发ID: {nested_forward_id}", "广播")

        nested_nodes = await _process_forward_content(
            nested_forward_content, nested_forward_id, bot, depth
        )

        if nested_nodes:
            result.append(Reference(nodes=nested_nodes))
    else:
        logger.warning(f"[D{depth}] 跳过类型: {seg_type}", "广播")

    return result


async def _extract_content_from_message(
    message_content: Any, bot: Bot, depth: int = 0
) -> UniMessage:
    """提取消息内容到UniMessage"""
    temp_msg = UniMessage()
    input_type_str = str(type(message_content))

    if depth >= MAX_FORWARD_DEPTH:
        logger.warning(
            f"[Depth {depth}] 达到最大递归深度 {MAX_FORWARD_DEPTH}，停止解析嵌套转发。",
            "广播",
        )
        temp_msg.append(Text("[嵌套转发层数过多，内容已省略]"))
        return temp_msg

    segments_to_process = []

    if isinstance(message_content, UniMessage):
        segments_to_process = list(message_content)
    elif isinstance(message_content, V11Message):
        segments_to_process = list(message_content)
    elif isinstance(message_content, list):
        segments_to_process = message_content
    elif (
        isinstance(message_content, dict)
        and "type" in message_content
        and "data" in message_content
    ):
        segments_to_process = [message_content]
    elif isinstance(message_content, str):
        if message_content.strip():
            temp_msg.append(Text(message_content))
        return temp_msg
    else:
        logger.warning(f"[Depth {depth}] 无法处理的输入类型: {input_type_str}", "广播")
        return temp_msg

    if segments_to_process:
        for index, seg_obj in enumerate(segments_to_process):
            try:
                if isinstance(seg_obj, Text):
                    text_content = getattr(seg_obj, "text", None)
                    if isinstance(text_content, str) and text_content.strip():
                        temp_msg.append(seg_obj)
                elif isinstance(seg_obj, Image):
                    if (
                        getattr(seg_obj, "url", None)
                        or getattr(seg_obj, "path", None)
                        or getattr(seg_obj, "raw", None)
                    ):
                        temp_msg.append(seg_obj)
                elif isinstance(seg_obj, At):
                    temp_msg.append(seg_obj)
                elif isinstance(seg_obj, AtAll):
                    temp_msg.append(seg_obj)
                elif isinstance(seg_obj, Video):
                    if (
                        getattr(seg_obj, "url", None)
                        or getattr(seg_obj, "path", None)
                        or getattr(seg_obj, "raw", None)
                    ):
                        temp_msg.append(seg_obj)
                        logger.debug(f"[D{depth}] 处理Video对象成功", "广播")
                else:
                    processed_segments = await _process_v11_segment(
                        seg_obj, depth, index, bot
                    )
                    temp_msg.extend(processed_segments)
            except Exception as e_conv_seg:
                logger.warning(
                    f"[D{depth}] 处理段 {index} 出错: {e_conv_seg}",
                    "广播",
                    e=e_conv_seg,
                )

    if not temp_msg and message_content:
        logger.warning(f"未能从类型 {input_type_str} 中提取内容", "广播")

    return temp_msg


async def get_broadcast_target_groups(
    bot: Bot, session: EventSession
) -> tuple[list, list]:
    """获取广播目标群组和启用了广播功能的群组"""
    target_groups = []
    all_groups, _ = await BroadcastManager.get_all_groups(bot)

    current_group_id = None
    if hasattr(session, "id2") and session.id2:
        current_group_id = session.id2

    if current_group_id:
        target_groups = [
            group for group in all_groups if group.group_id != current_group_id
        ]
        logger.info(
            f"向除当前群组({current_group_id})外的所有群组广播", "广播", session=session
        )
    else:
        target_groups = all_groups
        logger.info("向所有群组广播", "广播", session=session)

    if not target_groups:
        await MessageUtils.build_message("没有找到符合条件的广播目标群组。").send(
            reply_to=True
        )
        return [], []

    enabled_groups = []
    for group in target_groups:
        if not await CommonUtils.task_is_block(bot, "broadcast", group.group_id):
            enabled_groups.append(group)

    if not enabled_groups:
        await MessageUtils.build_message(
            "没有启用了广播功能的目标群组可供立即发送。"
        ).send(reply_to=True)
        return target_groups, []

    return target_groups, enabled_groups


async def send_broadcast_and_notify(
    bot: Bot,
    event: Event,
    message: UniMessage,
    enabled_groups: list,
    target_groups: list,
    session: EventSession,
) -> None:
    """发送广播并通知结果"""
    BroadcastManager.clear_last_broadcast_msg_ids()
    count, error_count = await BroadcastManager.send_to_specific_groups(
        bot, message, enabled_groups, session
    )

    result = f"成功广播 {count} 个群组"
    if error_count:
        result += f"\n发送失败 {error_count} 个群组"
    result += f"\n有效: {len(enabled_groups)} / 总计: {len(target_groups)}"

    user_id = str(event.get_user_id())
    await bot.send_private_msg(user_id=user_id, message=f"发送广播完成!\n{result}")

    BroadcastManager.log_info(
        f"广播完成，有效/总计: {len(enabled_groups)}/{len(target_groups)}",
        session,
    )
