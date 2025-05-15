import base64

import nonebot_plugin_alconna as alc
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Reference
from nonebot_plugin_alconna.uniseg.segment import CustomNode, Video

from zhenxun.services.log import logger


def uni_segment_to_v11_segment_dict(
    seg: alc.Segment, depth: int = 0
) -> dict | list[dict] | None:
    """UniSeg段转V11字典"""
    if isinstance(seg, alc.Text):
        return {"type": "text", "data": {"text": seg.text}}
    elif isinstance(seg, alc.Image):
        if getattr(seg, "url", None):
            return {
                "type": "image",
                "data": {"file": seg.url},
            }
        elif getattr(seg, "raw", None):
            raw_data = seg.raw
            if isinstance(raw_data, str):
                if len(raw_data) >= 9 and raw_data[:9] == "base64://":
                    return {"type": "image", "data": {"file": raw_data}}
            elif isinstance(raw_data, bytes):
                b64_str = base64.b64encode(raw_data).decode()
                return {"type": "image", "data": {"file": f"base64://{b64_str}"}}
            else:
                logger.warning(f"无法处理 Image.raw 的类型: {type(raw_data)}", "广播")
        elif getattr(seg, "path", None):
            logger.warning(
                f"在合并转发中使用了本地图片路径，可能无法显示: {seg.path}", "广播"
            )
            return {"type": "image", "data": {"file": f"file:///{seg.path}"}}
        else:
            logger.warning(f"alc.Image 缺少有效数据，无法转换为 V11 段: {seg}", "广播")
    elif isinstance(seg, alc.At):
        return {"type": "at", "data": {"qq": seg.target}}
    elif isinstance(seg, alc.AtAll):
        return {"type": "at", "data": {"qq": "all"}}
    elif isinstance(seg, Video):
        if getattr(seg, "url", None):
            return {
                "type": "video",
                "data": {"file": seg.url},
            }
        elif getattr(seg, "raw", None):
            raw_data = seg.raw
            if isinstance(raw_data, str):
                if len(raw_data) >= 9 and raw_data[:9] == "base64://":
                    return {"type": "video", "data": {"file": raw_data}}
            elif isinstance(raw_data, bytes):
                b64_str = base64.b64encode(raw_data).decode()
                return {"type": "video", "data": {"file": f"base64://{b64_str}"}}
            else:
                logger.warning(f"无法处理 Video.raw 的类型: {type(raw_data)}", "广播")
        elif getattr(seg, "path", None):
            logger.warning(
                f"在合并转发中使用了本地视频路径，可能无法显示: {seg.path}", "广播"
            )
            return {"type": "video", "data": {"file": f"file:///{seg.path}"}}
        else:
            logger.warning(f"Video 缺少有效数据，无法转换为 V11 段: {seg}", "广播")
    elif isinstance(seg, Reference) and getattr(seg, "nodes", None):
        if depth >= 3:
            logger.warning(
                f"嵌套转发深度超过限制 (depth={depth})，不再继续解析", "广播"
            )
            return {"type": "text", "data": {"text": "[嵌套转发层数过多，内容已省略]"}}

        nested_v11_content_list = []
        nodes_list = getattr(seg, "nodes", [])
        for node in nodes_list:
            if isinstance(node, CustomNode):
                node_v11_content = []
                if isinstance(node.content, UniMessage):
                    for nested_seg in node.content:
                        converted_dict = uni_segment_to_v11_segment_dict(
                            nested_seg, depth + 1
                        )
                        if isinstance(converted_dict, list):
                            node_v11_content.extend(converted_dict)
                        elif converted_dict:
                            node_v11_content.append(converted_dict)
                elif isinstance(node.content, str):
                    node_v11_content.append(
                        {"type": "text", "data": {"text": node.content}}
                    )
                if node_v11_content:
                    separator = {
                        "type": "text",
                        "data": {
                            "text": f"\n--- 来自 {node.name} ({node.uid}) 的消息 ---\n"
                        },
                    }
                    nested_v11_content_list.insert(0, separator)
                    nested_v11_content_list.extend(node_v11_content)
                    nested_v11_content_list.append(
                        {"type": "text", "data": {"text": "\n---\n"}}
                    )

        return nested_v11_content_list

    else:
        logger.warning(f"广播时跳过不支持的 UniSeg 段类型: {type(seg)}", "广播")
    return None


def uni_message_to_v11_list_of_dicts(uni_msg: UniMessage | str | list) -> list[dict]:
    """UniMessage转V11字典列表"""
    try:
        if isinstance(uni_msg, str):
            return [{"type": "text", "data": {"text": uni_msg}}]

        if isinstance(uni_msg, list):
            if not uni_msg:
                return []

            if all(isinstance(item, str) for item in uni_msg):
                return [{"type": "text", "data": {"text": item}} for item in uni_msg]

            result = []
            for item in uni_msg:
                if hasattr(item, "__iter__") and not isinstance(item, str | bytes):
                    result.extend(uni_message_to_v11_list_of_dicts(item))
                elif hasattr(item, "text") and not isinstance(item, str | bytes):
                    text_value = getattr(item, "text", "")
                    result.append({"type": "text", "data": {"text": str(text_value)}})
                elif hasattr(item, "url") and not isinstance(item, str | bytes):
                    url_value = getattr(item, "url", "")
                    if isinstance(item, Video):
                        result.append(
                            {"type": "video", "data": {"file": str(url_value)}}
                        )
                    else:
                        result.append(
                            {"type": "image", "data": {"file": str(url_value)}}
                        )
                else:
                    try:
                        result.append({"type": "text", "data": {"text": str(item)}})
                    except Exception as e:
                        logger.warning(f"无法转换列表元素: {item}, 错误: {e}", "广播")
            return result
    except Exception as e:
        logger.warning(f"消息转换过程中出错: {e}", "广播")

    return [{"type": "text", "data": {"text": str(uni_msg)}}]


def custom_nodes_to_v11_nodes(custom_nodes: list[CustomNode]) -> list[dict]:
    """CustomNode列表转V11节点"""
    v11_nodes = []
    for node in custom_nodes:
        v11_content_list = uni_message_to_v11_list_of_dicts(node.content)

        if v11_content_list:
            v11_nodes.append(
                {
                    "type": "node",
                    "data": {
                        "user_id": str(node.uid),
                        "nickname": node.name,
                        "content": v11_content_list,
                    },
                }
            )
        else:
            logger.warning(
                f"CustomNode (uid={node.uid}) 内容转换后为空，跳过此节点", "广播"
            )
    return v11_nodes
