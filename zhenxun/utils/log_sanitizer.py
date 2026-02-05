import copy
import re
from typing import Any

from nonebot.adapters import Message, MessageSegment


def _truncate_base64_string(value: str, threshold: int = 256) -> str:
    """如果字符串是超长的base64或data URI，则截断它。"""
    if not isinstance(value, str):
        return value

    prefixes = ("base64://", "data:image", "data:video", "data:audio")
    if value.startswith(prefixes) and len(value) > threshold:
        prefix = next((p for p in prefixes if value.startswith(p)), "base64")
        return f"[{prefix}_data_omitted_len={len(value)}]"

    if len(value) > 1000:
        return f"[long_string_omitted_len={len(value)}] {value[:20]}...{value[-20:]}"

    if len(value) > 2000:
        return f"[long_string_omitted_len={len(value)}] {value[:50]}...{value[-20:]}"

    return value


def _truncate_vector_list(vector: list, threshold: int = 10) -> list:
    """如果列表过长（通常是embedding向量），则截断它用于日志显示。"""
    if isinstance(vector, list) and len(vector) > threshold:
        return [*vector[:3], f"...({len(vector)} floats omitted)...", *vector[-3:]]
    return vector


def _recursive_sanitize_any(obj: Any) -> Any:
    """递归清洗任何对象中的长字符串"""
    if isinstance(obj, dict):
        return {k: _recursive_sanitize_any(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_recursive_sanitize_any(v) for v in obj]
    elif isinstance(obj, str):
        return _truncate_base64_string(obj)
    return obj


def _sanitize_ui_html(html_string: str) -> str:
    """
    专门用于净化UI渲染调试HTML的函数。
    它会查找所有内联的base64数据（如字体、图片）并将其截断。
    同时会折叠冗长的样式标签，避免主题 CSS 在日志中撑爆。
    """
    if not isinstance(html_string, str):
        return html_string

    pattern = re.compile(r"(data:[^;]+;base64,)[A-Za-z0-9+/=\s]{100,}")

    def replacer(match):
        prefix = match.group(1)
        original_len = len(match.group(0)) - len(prefix)
        return f"{prefix}[...base64_omitted_len={original_len}...]"

    html_string = pattern.sub(replacer, html_string)

    pattern_style = re.compile(
        r"(<style[^>]*>)(.*?)(</style>)", re.DOTALL | re.IGNORECASE
    )

    def replacer_style(match):
        start_tag, content, end_tag = match.group(1), match.group(2), match.group(3)
        keywords = [
            "Base Styles - Assembled by Jinja2",
            "Utility Classes",
            "@layer reset, base, components, utilities;",
        ]

        if len(content) > 600 or any(keyword in content for keyword in keywords):
            excerpt = (
                f"\n    /* [theme.css.jinja content hidden for brevity - "
                f"{len(content)} chars] */\n"
            )
            return f"{start_tag}{excerpt}{end_tag}"

        return match.group(0)

    return pattern_style.sub(replacer_style, html_string)


def _sanitize_nonebot_message(message: Message) -> Message:
    """净化nonebot.adapter.Message对象，用于日志记录。"""
    sanitized_message = copy.deepcopy(message)
    for seg in sanitized_message:
        seg: MessageSegment
        if seg.type in ("image", "record", "video"):
            file_info = seg.data.get("file", "")
            if isinstance(file_info, str):
                seg.data["file"] = _truncate_base64_string(file_info)
    return sanitized_message


def _sanitize_openai_response(response_json: dict) -> dict:
    """净化OpenAI兼容API的响应体。"""
    try:
        sanitized_json = copy.deepcopy(response_json)
        if "choices" in sanitized_json and isinstance(sanitized_json["choices"], list):
            for choice in sanitized_json["choices"]:
                if "message" in choice and isinstance(choice["message"], dict):
                    message = choice["message"]
                    if "images" in message and isinstance(message["images"], list):
                        for i, image_info in enumerate(message["images"]):
                            if "image_url" in image_info and isinstance(
                                image_info["image_url"], dict
                            ):
                                url = image_info["image_url"].get("url", "")
                                message["images"][i]["image_url"]["url"] = (
                                    _truncate_base64_string(url)
                                )
                    if "reasoning_details" in message and isinstance(
                        message["reasoning_details"], list
                    ):
                        for detail in message["reasoning_details"]:
                            if isinstance(detail, dict):
                                if "data" in detail and isinstance(detail["data"], str):
                                    if len(detail["data"]) > 100:
                                        detail["data"] = (
                                            f"[encrypted_data_omitted_len={len(detail['data'])}]"
                                        )
                                if "text" in detail and isinstance(detail["text"], str):
                                    detail["text"] = _truncate_base64_string(
                                        detail["text"], threshold=2000
                                    )
        if "data" in sanitized_json and isinstance(sanitized_json["data"], list):
            for item in sanitized_json["data"]:
                if "embedding" in item and isinstance(item["embedding"], list):
                    item["embedding"] = _truncate_vector_list(item["embedding"])
                if "b64_json" in item and isinstance(item["b64_json"], str):
                    if len(item["b64_json"]) > 256:
                        item["b64_json"] = (
                            f"[base64_json_omitted_len={len(item['b64_json'])}]"
                        )
        if "input" in sanitized_json and isinstance(sanitized_json["input"], list):
            for item in sanitized_json["input"]:
                if "content" in item and isinstance(item["content"], list):
                    for part in item["content"]:
                        if isinstance(part, dict) and part.get("type") == "input_image":
                            image_url = part.get("image_url")
                            if isinstance(image_url, str):
                                part["image_url"] = _truncate_base64_string(image_url)
        return sanitized_json
    except Exception:
        return response_json


def _sanitize_openai_request(body: dict) -> dict:
    """净化OpenAI兼容API的请求体，主要截断图片base64。"""
    from zhenxun.services.llm.config.providers import (
        DebugLogOptions,
        get_llm_config,
    )

    debug_conf = get_llm_config().debug_log
    if isinstance(debug_conf, bool):
        debug_conf = DebugLogOptions(
            show_tools=debug_conf, show_schema=debug_conf, show_safety=debug_conf
        )

    try:
        sanitized_json = _recursive_sanitize_any(copy.deepcopy(body))
        if "tools" in sanitized_json and not debug_conf.show_tools:
            tools = sanitized_json["tools"]
            if isinstance(tools, list):
                tool_names = []
                for t in tools:
                    if isinstance(t, dict):
                        name = None
                        if "function" in t and isinstance(t["function"], dict):
                            name = t["function"].get("name")
                        if not name and "name" in t:
                            name = t.get("name")
                        tool_names.append(name or "unknown")
                sanitized_json["tools"] = (
                    f"<{len(tool_names)} tools hidden: {', '.join(tool_names)}>"
                )

        if "response_format" in sanitized_json and not debug_conf.show_schema:
            response_format = sanitized_json["response_format"]
            if isinstance(response_format, dict):
                if response_format.get("type") == "json_schema":
                    sanitized_json["response_format"] = {
                        "type": "json_schema",
                        "json_schema": "<JSON Schema Hidden>",
                    }

        return sanitized_json
    except Exception:
        return body


def _sanitize_gemini_response(response_json: dict) -> dict:
    """净化Gemini API的响应体，处理文本和图片生成两种格式。"""
    from zhenxun.services.llm.config.providers import get_llm_config

    debug_mode = get_llm_config().debug_log
    try:
        sanitized_json = copy.deepcopy(response_json)

        def _process_candidates(candidates_list: list):
            """辅助函数，用于处理任何 candidates 列表。"""
            if not isinstance(candidates_list, list):
                return
            for candidate in candidates_list:
                if "content" in candidate and isinstance(candidate["content"], dict):
                    content = candidate["content"]
                    if "parts" in content and isinstance(content["parts"], list):
                        for i, part in enumerate(content["parts"]):
                            if "inlineData" in part and isinstance(
                                part["inlineData"], dict
                            ):
                                data = part["inlineData"].get("data", "")
                                if isinstance(data, str) and len(data) > 256:
                                    content["parts"][i]["inlineData"]["data"] = (
                                        f"[base64_data_omitted_len={len(data)}]"
                                    )
                            if "thoughtSignature" in part:
                                signature = part.get("thoughtSignature", "")
                                if isinstance(signature, str) and len(signature) > 256:
                                    content["parts"][i]["thoughtSignature"] = (
                                        f"[signature_omitted_len={len(signature)}]"
                                    )
                if not debug_mode and isinstance(candidate, dict):
                    if "safetyRatings" in candidate:
                        candidate["safetyRatings"] = "<Safety Ratings Hidden>"

        if "candidates" in sanitized_json:
            _process_candidates(sanitized_json["candidates"])

        if "image_generation" in sanitized_json and isinstance(
            sanitized_json["image_generation"], dict
        ):
            if "candidates" in sanitized_json["image_generation"]:
                _process_candidates(sanitized_json["image_generation"]["candidates"])

        if "embeddings" in sanitized_json and isinstance(
            sanitized_json["embeddings"], list
        ):
            for embedding in sanitized_json["embeddings"]:
                if "values" in embedding and isinstance(embedding["values"], list):
                    embedding["values"] = _truncate_vector_list(embedding["values"])

        if not debug_mode and "promptFeedback" in sanitized_json:
            prompt_feedback = sanitized_json.get("promptFeedback") or {}
            if isinstance(prompt_feedback, dict) and "safetyRatings" in prompt_feedback:
                prompt_feedback["safetyRatings"] = "<Safety Ratings Hidden>"
                sanitized_json["promptFeedback"] = prompt_feedback

        return sanitized_json
    except Exception:
        return response_json


def _sanitize_gemini_request(body: dict) -> dict:
    """净化Gemini API的请求体，进行结构转换和总结。"""
    from zhenxun.services.llm.config.providers import (
        DebugLogOptions,
        get_llm_config,
    )

    debug_conf = get_llm_config().debug_log
    if isinstance(debug_conf, bool):
        debug_conf = DebugLogOptions(
            show_tools=debug_conf, show_schema=debug_conf, show_safety=debug_conf
        )

    try:
        sanitized_body = copy.deepcopy(body)
        if "tools" in sanitized_body and not debug_conf.show_tools:
            tool_summary = []
            for tool_group in sanitized_body["tools"]:
                if (
                    isinstance(tool_group, dict)
                    and "functionDeclarations" in tool_group
                ):
                    declarations = tool_group["functionDeclarations"]
                    if isinstance(declarations, list):
                        for func in declarations:
                            if isinstance(func, dict):
                                tool_summary.append(func.get("name", "unknown"))
            sanitized_body["tools"] = (
                f"<{len(tool_summary)} functions hidden: {', '.join(tool_summary)}>"
            )

        if not debug_conf.show_safety and "safetySettings" in sanitized_body:
            sanitized_body["safetySettings"] = "<Safety Settings Hidden>"

        if not debug_conf.show_schema and "generationConfig" in sanitized_body:
            generation_config = sanitized_body["generationConfig"]
            if (
                isinstance(generation_config, dict)
                and "responseJsonSchema" in generation_config
            ):
                generation_config["responseJsonSchema"] = "<JSON Schema Hidden>"

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

                        if "thoughtSignature" in part:
                            sig = part["thoughtSignature"]
                            if isinstance(sig, str) and len(sig) > 64:
                                part["thoughtSignature"] = (
                                    f"[signature_omitted_len={len(sig)}]"
                                )

                    if media_summary:
                        summary_text = (
                            f"[多模态内容: {len(media_summary)}个文件 - "
                            f"{', '.join(media_summary)}]"
                        )
                        new_parts.insert(0, {"text": summary_text})

                    content_item["parts"] = new_parts
        return sanitized_body
    except Exception:
        return body


def sanitize_for_logging(data: Any, context: str | None = None) -> Any:
    """
    统一的日志净化入口。

    Args:
        data: 需要净化的数据 (dict, Message, etc.).
        context: 净化场景的上下文标识，例如 'gemini_request', 'openai_response'.

    Returns:
        净化后的数据。
    """
    if context == "nonebot_message":
        if isinstance(data, Message):
            return _sanitize_nonebot_message(data)
    elif context == "openai_response":
        if isinstance(data, dict):
            return _sanitize_openai_response(data)
    elif context == "gemini_response":
        if isinstance(data, dict):
            return _sanitize_gemini_response(data)
    elif context == "gemini_request":
        if isinstance(data, dict):
            return _sanitize_gemini_request(data)
    elif context == "openai_request":
        if isinstance(data, dict):
            return _sanitize_openai_request(data)
    elif context == "ui_html":
        if isinstance(data, str):
            return _sanitize_ui_html(data)

    return _recursive_sanitize_any(data)
