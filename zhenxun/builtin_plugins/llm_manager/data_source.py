import time
from typing import Any

from zhenxun.services.llm import (
    LLMException,
    get_global_default_model_name,
    get_model_instance,
    list_available_models,
    set_global_default_model_name,
)
from zhenxun.services.llm.core import KeyStatus
from zhenxun.services.llm.manager import (
    reset_key_status,
)


class DataSource:
    """LLM管理插件的数据源和业务逻辑"""

    @staticmethod
    async def get_model_list(show_all: bool = False) -> list[dict[str, Any]]:
        """获取模型列表"""
        models = list_available_models()
        if show_all:
            return models
        return [m for m in models if m.get("is_available", True)]

    @staticmethod
    async def get_model_details(model_name_str: str) -> dict[str, Any] | None:
        """获取指定模型的详细信息"""
        try:
            model = await get_model_instance(model_name_str)
            return {
                "provider_config": model.provider_config,
                "model_detail": model.model_detail,
                "capabilities": model.capabilities,
            }
        except LLMException:
            return None

    @staticmethod
    async def get_default_model() -> str | None:
        """获取全局默认模型"""
        return get_global_default_model_name()

    @staticmethod
    async def set_default_model(model_name_str: str) -> tuple[bool, str]:
        """设置全局默认模型"""
        success = set_global_default_model_name(model_name_str)
        if success:
            return True, f"✅ 成功将默认模型设置为: {model_name_str}"
        else:
            return False, f"❌ 设置失败，模型 '{model_name_str}' 不存在或无效。"

    @staticmethod
    async def test_model_connectivity(model_name_str: str) -> tuple[bool, str]:
        """测试模型连通性"""
        start_time = time.monotonic()
        try:
            async with await get_model_instance(model_name_str) as model:
                await model.generate_text("你好")
            end_time = time.monotonic()
            latency = (end_time - start_time) * 1000
            return (
                True,
                f"✅ 模型 '{model_name_str}' 连接成功！\n响应延迟: {latency:.2f} ms",
            )
        except LLMException as e:
            return (
                False,
                f"❌ 模型 '{model_name_str}' 连接测试失败:\n"
                f"{e.user_friendly_message}\n错误码: {e.code.name}",
            )
        except Exception as e:
            return False, f"❌ 测试时发生未知错误: {e!s}"

    @staticmethod
    async def get_key_status(provider_name: str) -> list[dict[str, Any]] | None:
        """获取并排序指定提供商的API Key状态"""
        from zhenxun.services.llm.manager import get_key_usage_stats

        all_stats = await get_key_usage_stats()
        provider_stats = all_stats.get(provider_name)

        if not provider_stats or not provider_stats.get("key_stats"):
            return None

        key_stats_dict = provider_stats["key_stats"]

        stats_list = [
            {"key_id": key_id, **stats} for key_id, stats in key_stats_dict.items()
        ]

        def sort_key(item: dict[str, Any]):
            status_priority = item.get("status_enum", KeyStatus.UNUSED).value
            return (
                status_priority,
                100 - item.get("success_rate", 100.0),
                -item.get("total_calls", 0),
            )

        sorted_stats_list = sorted(stats_list, key=sort_key)

        return sorted_stats_list

    @staticmethod
    async def reset_key(provider_name: str, api_key: str | None) -> tuple[bool, str]:
        """重置API Key状态"""
        success = await reset_key_status(provider_name, api_key)
        if success:
            if api_key:
                if len(api_key) > 8:
                    target = f"API Key '{api_key[:4]}...{api_key[-4:]}'"
                else:
                    target = f"API Key '{api_key}'"
            else:
                target = "所有API Keys"
            return True, f"✅ 成功重置提供商 '{provider_name}' 的 {target} 的状态。"
        else:
            return False, "❌ 重置失败，请检查提供商名称或API Key是否正确。"
