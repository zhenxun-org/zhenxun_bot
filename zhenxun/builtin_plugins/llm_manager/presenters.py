from typing import Any

from zhenxun.services.llm.core import KeyStatus
from zhenxun.services.llm.types import ModelModality
from zhenxun.utils._build_image import BuildImage
from zhenxun.utils._image_template import ImageTemplate, Markdown, RowStyle


def _format_seconds(seconds: int) -> str:
    """将秒数格式化为 'Xm Ys' 或 'Xh Ym' 的形式"""
    if seconds <= 0:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"

    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


class Presenters:
    """格式化LLM管理插件的输出 (图片格式)"""

    @staticmethod
    async def format_model_list_as_image(
        models: list[dict[str, Any]], show_all: bool
    ) -> BuildImage:
        """将模型列表格式化为表格图片"""
        title = "📋 LLM模型列表" + (" (所有已配置模型)" if show_all else " (仅可用)")

        if not models:
            return await BuildImage.build_text_image(
                f"{title}\n\n当前没有配置任何LLM模型。"
            )

        column_name = ["提供商", "模型名称", "API类型", "状态"]
        data_list = []
        for model in models:
            status_text = "✅ 可用" if model.get("is_available", True) else "❌ 不可用"
            embed_tag = " (Embed)" if model.get("is_embedding_model", False) else ""
            data_list.append(
                [
                    model.get("provider_name", "N/A"),
                    f"{model.get('model_name', 'N/A')}{embed_tag}",
                    model.get("api_type", "N/A"),
                    status_text,
                ]
            )

        return await ImageTemplate.table_page(
            head_text=title,
            tip_text="使用 `llm info <Provider/ModelName>` 查看详情",
            column_name=column_name,
            data_list=data_list,
        )

    @staticmethod
    async def format_model_details_as_markdown_image(details: dict[str, Any]) -> bytes:
        """将模型详情格式化为Markdown图片"""
        provider = details["provider_config"]
        model = details["model_detail"]
        caps = details["capabilities"]

        cap_list = []
        if ModelModality.IMAGE in caps.input_modalities:
            cap_list.append("视觉")
        if ModelModality.VIDEO in caps.input_modalities:
            cap_list.append("视频")
        if ModelModality.AUDIO in caps.input_modalities:
            cap_list.append("音频")
        if caps.supports_tool_calling:
            cap_list.append("工具调用")
        if caps.is_embedding_model:
            cap_list.append("文本嵌入")

        md = Markdown()
        md.head(f"🔎 模型详情: {provider.name}/{model.model_name}", level=1)
        md.text("---")
        md.head("提供商信息", level=2)
        md.list(
            [
                f"**名称**: {provider.name}",
                f"**API 类型**: {provider.api_type}",
                f"**API Base**: {provider.api_base or '默认'}",
            ]
        )
        md.head("模型详情", level=2)

        temp_value = model.temperature or provider.temperature or "未设置"
        token_value = model.max_tokens or provider.max_tokens or "未设置"

        md.list(
            [
                f"**名称**: {model.model_name}",
                f"**默认温度**: {temp_value}",
                f"**最大Token**: {token_value}",
                f"**核心能力**: {', '.join(cap_list) or '纯文本'}",
            ]
        )

        return await md.build()

    @staticmethod
    async def format_key_status_as_image(
        provider_name: str, sorted_stats: list[dict[str, Any]]
    ) -> BuildImage:
        """将已排序的、详细的API Key状态格式化为表格图片"""
        title = f"🔑 '{provider_name}' API Key 状态"

        if not sorted_stats:
            return await BuildImage.build_text_image(
                f"{title}\n\n该提供商没有配置API Keys。"
            )

        def _status_row_style(column: str, text: str) -> RowStyle:
            style = RowStyle()
            if column == "状态":
                if "✅ 健康" in text:
                    style.font_color = "#67C23A"
                elif "⚠️ 告警" in text:
                    style.font_color = "#E6A23C"
                elif "❌ 错误" in text or "🚫" in text:
                    style.font_color = "#F56C6C"
                elif "❄️ 冷却中" in text:
                    style.font_color = "#409EFF"
            elif column == "成功率":
                try:
                    if text != "N/A":
                        rate = float(text.replace("%", ""))
                        if rate < 80:
                            style.font_color = "#F56C6C"
                        elif rate < 95:
                            style.font_color = "#E6A23C"
                except (ValueError, TypeError):
                    pass
            return style

        column_name = [
            "Key (部分)",
            "状态",
            "总调用",
            "成功率",
            "平均延迟(s)",
            "上次错误",
            "建议操作",
        ]
        data_list = []

        for key_info in sorted_stats:
            status_enum: KeyStatus = key_info["status_enum"]

            if status_enum == KeyStatus.COOLDOWN:
                cooldown_seconds = int(key_info["cooldown_seconds_left"])
                formatted_time = _format_seconds(cooldown_seconds)
                status_text = f"❄️ 冷却中({formatted_time})"
            else:
                status_text = {
                    KeyStatus.DISABLED: "🚫 永久禁用",
                    KeyStatus.ERROR: "❌ 错误",
                    KeyStatus.WARNING: "⚠️ 告警",
                    KeyStatus.HEALTHY: "✅ 健康",
                    KeyStatus.UNUSED: "⚪️ 未使用",
                }.get(status_enum, "❔ 未知")

            total_calls = key_info["total_calls"]
            total_calls_text = (
                f"{key_info['success_count']}/{total_calls}"
                if total_calls > 0
                else "0/0"
            )

            success_rate = key_info["success_rate"]
            success_rate_text = f"{success_rate:.1f}%" if total_calls > 0 else "N/A"

            avg_latency = key_info["avg_latency"]
            avg_latency_text = f"{avg_latency / 1000:.2f}" if avg_latency > 0 else "N/A"

            last_error = key_info.get("last_error") or "-"
            if len(last_error) > 25:
                last_error = last_error[:22] + "..."

            data_list.append(
                [
                    key_info["key_id"],
                    status_text,
                    total_calls_text,
                    success_rate_text,
                    avg_latency_text,
                    last_error,
                    key_info["suggested_action"],
                ]
            )

        return await ImageTemplate.table_page(
            head_text=title,
            tip_text="使用 `llm reset-key <Provider>` 重置Key状态",
            column_name=column_name,
            data_list=data_list,
            text_style=_status_row_style,
            column_space=15,
        )
