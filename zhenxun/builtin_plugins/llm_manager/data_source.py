import json
import time
from typing import Any

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.ai.core.exceptions import LLMException
from zhenxun.services.ai.llm.api import chat
from zhenxun.services.ai.llm.manager import (
    get_configured_providers,
    get_model_instance,
    list_available_models,
    reset_key_status,
)
from zhenxun.services.ai.tools.providers.mcp.provider import mcp_provider


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
    async def test_model_connectivity(model_name_str: str) -> tuple[bool, str]:
        """测试模型连通性"""
        start_time = time.monotonic()
        try:
            await chat("你好", model=model_name_str)
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
                f"{e.user_friendly_message}\n错误类型: {e.__class__.__name__}",
            )
        except Exception as e:
            return False, f"❌ 测试时发生未知错误: {e!s}"

    @staticmethod
    async def get_key_status(provider_name: str) -> list[dict[str, Any]] | None:
        """获取并排序指定提供商的API Key状态"""
        from zhenxun.services.ai.llm.manager import get_key_usage_stats

        all_stats = await get_key_usage_stats()
        provider_stats = all_stats.get(provider_name)

        if not provider_stats or not provider_stats.get("key_stats"):
            return None

        key_stats_dict = provider_stats["key_stats"]

        stats_list = [
            {"key_id": key_id, **stats} for key_id, stats in key_stats_dict.items()
        ]

        def sort_key(item: dict[str, Any]):
            status_map = {
                "DISABLED": 0,
                "ERROR": 1,
                "COOLDOWN": 2,
                "WARNING": 3,
                "HEALTHY": 4,
                "UNUSED": 5,
            }
            status_str = item.get("status", "HEALTHY")
            if (
                item.get("successes", 0) == 0
                and item.get("failures", 0) == 0
                and status_str == "HEALTHY"
            ):
                status_str = "UNUSED"
            status_priority = status_map.get(status_str, 5)
            total = item.get("successes", 0) + item.get("failures", 0)
            success_rate = (
                (item.get("successes", 0) / total * 100) if total > 0 else 100.0
            )
            return (
                status_priority,
                100 - success_rate,
                -total,
            )

        sorted_stats_list = sorted(stats_list, key=sort_key)

        return sorted_stats_list

    @staticmethod
    async def reset_keys(provider_name: str | None = None) -> tuple[bool, str]:
        """重置指定或所有提供商的 API Key 状态"""
        providers = get_configured_providers()

        if provider_name:
            target = next(
                (p for p in providers if p.name.lower() == provider_name.lower()), None
            )
            if not target:
                return False, f"❌ 未找到提供商 '{provider_name}'，请检查名称是否正确。"
            await reset_key_status(target.name)
            return (
                True,
                f"✅ 已成功重置提供商 '{target.name}'"
                "的所有 API Key 状态为健康 (HEALTHY)。",
            )
        else:
            count = 0
            for p in providers:
                await reset_key_status(p.name)
                count += 1
            return (
                True,
                f"✅ 已成功重置所有提供商 (共 {count} 个) "
                "的 API Key 状态为健康 (HEALTHY)。",
            )

    @staticmethod
    async def get_mcp_list() -> list[dict[str, Any]]:
        """获取排序后的 MCP 列表"""
        await mcp_provider.initialize()
        if not mcp_provider._config:
            return []

        mcp_servers = mcp_provider._config.mcpServers
        sorted_names = sorted(mcp_servers.keys())

        result = []
        for idx, name in enumerate(sorted_names):
            conf = mcp_servers[name]
            target = ""
            if conf.transport in ("stdio", "sandbox_proxy") and conf.command:
                target = f"{conf.command} {' '.join(conf.args)}"
            elif conf.transport in ("sse", "streamable-http") and conf.url:
                target = conf.url

            result.append(
                {
                    "id": idx + 1,
                    "name": name,
                    "enabled": conf.enabled,
                    "transport": conf.transport,
                    "target": target,
                }
            )
        return result

    @staticmethod
    async def resolve_mcp_targets(
        targets: tuple[Any, ...],
    ) -> tuple[list[str], list[str]]:
        """将输入的 ID 或名称解析为实际的 MCP 服务名称"""
        await mcp_provider.initialize()
        if not mcp_provider._config:
            return [], list(map(str, targets))

        mcp_servers = mcp_provider._config.mcpServers
        sorted_names = sorted(mcp_servers.keys())

        valid_names = []
        invalid_targets = []

        for tgt in targets:
            tgt_str = str(tgt)
            target_name = None

            if tgt_str.isdigit():
                idx = int(tgt_str) - 1
                if 0 <= idx < len(sorted_names):
                    target_name = sorted_names[idx]
            else:
                if tgt_str in mcp_servers:
                    target_name = tgt_str

            if target_name:
                valid_names.append(target_name)
            else:
                invalid_targets.append(tgt_str)

        return list(dict.fromkeys(valid_names)), list(dict.fromkeys(invalid_targets))

    @staticmethod
    async def toggle_mcp_servers(
        targets: tuple[Any, ...], is_enable: bool
    ) -> tuple[list[str], list[str]]:
        """批量切换 MCP 状态"""
        valid_names, invalid_targets = await DataSource.resolve_mcp_targets(targets)
        if not mcp_provider._config:
            return [], invalid_targets

        mcp_servers = mcp_provider._config.mcpServers
        success_names = []

        for target_name in valid_names:
            conf = mcp_servers[target_name]
            if conf.enabled != is_enable:
                conf.enabled = is_enable
                if not is_enable:
                    if tk := mcp_provider._toolkits.pop(target_name, None):
                        await tk.close()
                else:
                    if target_name not in mcp_provider._toolkits:
                        mcp_provider._setup_toolkit(target_name, conf)
            success_names.append(target_name)

        if success_names:
            mcp_provider._discovered_tools = None
            mcp_provider._save_config()

        return success_names, invalid_targets

    @staticmethod
    async def reload_mcp_config() -> None:
        """完全重新加载 MCP 配置"""
        await mcp_provider.shutdown()
        mcp_provider._config = None
        mcp_provider._discovered_tools = None
        await mcp_provider.initialize()

    @staticmethod
    async def delete_mcp_servers(names: list[str]) -> None:
        """删除指定的 MCP 服务"""
        for name in names:
            await mcp_provider.unregister_server(name)

    @staticmethod
    async def add_mcp_servers_from_json(json_str: str) -> tuple[bool, str]:
        """将 JSON 字符串解析并合并到 mcp.json"""
        mcp_path = DATA_PATH / "ai" / "mcp.json"

        try:
            json_str = json_str.strip()
            if json_str.startswith("```"):
                lines = json_str.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                json_str = "\n".join(lines).strip()

            new_config = json.loads(json_str)
            if not isinstance(new_config, dict) or "mcpServers" not in new_config:
                return False, "❌ JSON 格式不正确，必须包含顶层键 'mcpServers'。"

            new_servers = new_config["mcpServers"]
            if not isinstance(new_servers, dict) or not new_servers:
                return False, "❌ 'mcpServers' 不能为空且必须为 JSON 对象(dict)。"

            if mcp_path.exists():
                with mcp_path.open("r", encoding="utf-8") as f:
                    current_config = json.load(f)
            else:
                current_config = {"mcpServers": {}}

            if "mcpServers" not in current_config:
                current_config["mcpServers"] = {}

            added_names = []
            for name, conf in new_servers.items():
                current_config["mcpServers"][name] = conf
                added_names.append(name)

            mcp_path.parent.mkdir(parents=True, exist_ok=True)
            with mcp_path.open("w", encoding="utf-8") as f:
                json.dump(current_config, f, ensure_ascii=False, indent=2)

            await DataSource.reload_mcp_config()

            return True, f"✅ 成功添加/更新 MCP 服务: {', '.join(added_names)}"

        except json.JSONDecodeError as e:
            return False, f"❌ JSON 解析失败: {e}"
        except Exception as e:
            return False, f"❌ 添加 MCP 服务时发生未知错误: {e}"
