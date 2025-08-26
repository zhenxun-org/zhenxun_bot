from typing import Any

from zhenxun.services import renderer_service
from zhenxun.services.llm.core import KeyStatus
from zhenxun.services.llm.types import ModelModality
from zhenxun.ui.builders import MarkdownBuilder, TableBuilder
from zhenxun.ui.models import StatusBadgeCell, TextCell


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
    ) -> bytes:
        """将模型列表格式化为表格图片"""
        title = "LLM模型列表" + (" (所有已配置模型)" if show_all else " (仅可用)")

        if not models:
            builder = TableBuilder(
                title=title, tip="当前没有配置任何LLM模型。"
            ).set_headers(["提供商", "模型名称", "API类型", "状态"])
            return await renderer_service.render(builder.build())

        column_name = ["提供商", "模型名称", "API类型", "状态"]
        rows_data = []
        for model in models:
            is_available = model.get("is_available", True)
            embed_tag = " (Embed)" if model.get("is_embedding_model", False) else ""
            rows_data.append(
                [
                    TextCell(content=model.get("provider_name", "N/A")),
                    TextCell(content=f"{model.get('model_name', 'N/A')}{embed_tag}"),
                    TextCell(content=model.get("api_type", "N/A")),
                    StatusBadgeCell(
                        text="可用" if is_available else "不可用",
                        status_type="ok" if is_available else "error",
                    ),
                ]
            )

        builder = TableBuilder(
            title=title, tip="使用 `llm info <Provider/ModelName>` 查看详情"
        )
        builder.set_headers(column_name)
        builder.set_column_alignments(["left", "left", "left", "center"])
        builder.add_rows(rows_data)
        return await renderer_service.render(builder.build(), use_cache=True)

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

        builder = MarkdownBuilder()
        builder.head(f"🔎 模型详情: {provider.name}/{model.model_name}", 1)
        builder.text("---")
        builder.head("提供商信息", 2)
        builder.text(f"- **名称**: {provider.name}")
        builder.text(f"- **API 类型**: {provider.api_type}")
        builder.text(f"- **API Base**: {provider.api_base or '默认'}")

        builder.head("模型详情", 2)

        temp_value = model.temperature or provider.temperature or "未设置"
        token_value = model.max_tokens or provider.max_tokens or "未设置"

        builder.text(f"- **名称**: {model.model_name}")
        builder.text(f"- **默认温度**: {temp_value}")
        builder.text(f"- **最大Token**: {token_value}")
        builder.text(f"- **核心能力**: {', '.join(cap_list) or '纯文本'}")

        return await renderer_service.render(builder.with_style("light").build())

    @staticmethod
    async def format_key_status_as_image(
        provider_name: str, sorted_stats: list[dict[str, Any]]
    ) -> bytes:
        """将已排序的、详细的API Key状态格式化为表格图片"""
        title = f"🔑 '{provider_name}' API Key 状态"

        data_list = []

        for key_info in sorted_stats:
            status_enum: KeyStatus = key_info["status_enum"]

            if status_enum == KeyStatus.COOLDOWN:
                cooldown_seconds = int(key_info["cooldown_seconds_left"])
                formatted_time = _format_seconds(cooldown_seconds)
                status_cell = StatusBadgeCell(
                    text=f"冷却中({formatted_time})", status_type="info"
                )
            else:
                status_map = {
                    KeyStatus.DISABLED: ("永久禁用", "error"),
                    KeyStatus.ERROR: ("错误", "error"),
                    KeyStatus.WARNING: ("告警", "warning"),
                    KeyStatus.HEALTHY: ("健康", "ok"),
                    KeyStatus.UNUSED: ("未使用", "info"),
                }
                text, status_type = status_map.get(status_enum, ("未知", "info"))
                status_cell = StatusBadgeCell(text=text, status_type=status_type)  # type: ignore

            total_calls = key_info["total_calls"]
            total_calls_text = (
                f"{key_info['success_count']}/{total_calls}"
                if total_calls > 0
                else "0/0"
            )

            success_rate = key_info["success_rate"]
            success_rate_text = f"{success_rate:.1f}%" if total_calls > 0 else "N/A"
            rate_color = None
            if total_calls > 0:
                if success_rate < 80:
                    rate_color = "#F56C6C"
                elif success_rate < 95:
                    rate_color = "#E6A23C"
            success_rate_cell = TextCell(content=success_rate_text, color=rate_color)

            avg_latency = key_info["avg_latency"]
            avg_latency_text = f"{avg_latency / 1000:.2f}" if avg_latency > 0 else "N/A"

            last_error = key_info.get("last_error") or "-"
            if len(last_error) > 25:
                last_error = last_error[:22] + "..."

            data_list.append(
                [
                    TextCell(content=key_info["key_id"]),
                    status_cell,
                    TextCell(content=total_calls_text),
                    success_rate_cell,
                    TextCell(content=avg_latency_text),
                    TextCell(content=last_error),
                    TextCell(content=key_info["suggested_action"]),
                ]
            )

        builder = TableBuilder(
            title=title, tip="使用 `llm reset-key <Provider>` 重置Key状态"
        )
        builder.set_headers(
            [
                "Key (部分)",
                "状态",
                "总调用",
                "成功率",
                "平均延迟(s)",
                "上次错误",
                "建议操作",
            ]
        )
        builder.add_rows(data_list)
        return await renderer_service.render(builder.build(), use_cache=False)
