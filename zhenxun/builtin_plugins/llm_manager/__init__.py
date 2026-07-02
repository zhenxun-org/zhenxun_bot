from collections import defaultdict

from arclet.alconna import MultiVar
from nonebot.adapters import Event
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Match,
    Option,
    Query,
    Subcommand,
    on_alconna,
    store_true,
)
from nonebot_plugin_waiter import prompt

from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

from .data_source import DataSource
from .presenters import Presenters

__plugin_meta__ = PluginMetadata(
    name="LLM模型管理",
    description="查看和管理大语言模型服务。",
    usage="""
    LLM模型管理 (SUPERUSER)

    llm list [--all]
      - 查看可用模型列表。
      - --all: 显示包括不可用在内的所有模型。

    llm info <Provider/ModelName>
      - 查看指定模型的详细信息和能力。

    llm test <Provider/ModelName>
      - 测试指定模型的连通性和API Key有效性。

    llm keys <ProviderName>
      - 查看指定提供商的所有API Key状态。

    llm mcp [action] [targets...]
      - 管理 MCP (Model Context Protocol) 服务。
      - 不带参数: 查看当前配置的 MCP 服务列表及序号。
      - 添加/add <JSON>: 动态添加或修改 MCP 配置 (需包含 mcpServers)。
      - 开启/关闭 <ID/名称>: 批量切换目标 MCP 的状态。也可以使用 on/off。
      - 删除/del <ID/名称>: 删除指定 MCP 服务 (需要确认)。
      - 重载/reload: 重新读取 mcp.json 配置文件。
      - 例子: llm mcp 开启 1 3 bingcn
    """,
    extra=PluginExtraData(
        author="HibiKier",
        version="1.0.0",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)

llm_cmd = on_alconna(
    Alconna(
        "llm",
        Subcommand(
            "list",
            Option("--text", action=store_true, help_text="以纯文本格式输出模型列表"),
            alias=["ls"],
            help_text="查看模型列表",
        ),
        Subcommand("info", Args["model_name", str], help_text="查看模型详情"),
        Subcommand(
            "test", Args["model_name", str], alias=["ping"], help_text="测试模型连通性"
        ),
        Subcommand("keys", Args["provider_name", str], help_text="查看API密钥状态"),
        Subcommand(
            "mcp",
            Option("添加", Args["json_strs", MultiVar(str)], alias=["add"]),
            Option("开启", Args["targets", MultiVar(str)], alias=["on"]),
            Option("关闭", Args["targets", MultiVar(str)], alias=["off"]),
            Option("删除", Args["targets", MultiVar(str)], alias=["del"]),
            Option("重载", alias=["reload"]),
            help_text="管理 MCP 服务",
        ),
    ),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@llm_cmd.assign("list")
async def handle_list(
    arp: Arparma,
    show_all: Query[bool] = Query("all"),
    text_mode: Query[bool] = Query("list.text.value", False),
):
    """处理 'llm list' 命令"""
    logger.info("获取LLM模型列表", command="LLM Manage", session=arp.header_result)
    models = await DataSource.get_model_list(show_all=show_all.result)

    if text_mode.result:
        if not models:
            await llm_cmd.finish("当前没有配置任何LLM模型。")

        grouped_models = defaultdict(list)
        for model in models:
            grouped_models[model["provider_name"]].append(model)

        response_parts = ["可用的LLM模型列表:"]
        for provider, model_list in grouped_models.items():
            response_parts.append(f"\n{provider}:")
            for model in model_list:
                response_parts.append(
                    f"  {model['provider_name']}/{model['model_name']}"
                )

        response_text = "\n".join(response_parts)
        await llm_cmd.finish(response_text)
    else:
        image = await Presenters.format_model_list_as_image(models, show_all.result)
        await llm_cmd.finish(MessageUtils.build_message(image))


@llm_cmd.assign("info")
async def handle_info(arp: Arparma, model_name: Match[str]):
    """处理 'llm info' 命令"""
    logger.info(
        f"获取模型详情: {model_name.result}",
        command="LLM Manage",
        session=arp.header_result,
    )
    details = await DataSource.get_model_details(model_name.result)
    if not details:
        await llm_cmd.finish(f"未找到模型: {model_name.result}")

    image_bytes = await Presenters.format_model_details_as_markdown_image(details)
    await llm_cmd.finish(MessageUtils.build_message(image_bytes))


@llm_cmd.assign("test")
async def handle_test(arp: Arparma, model_name: Match[str]):
    """处理 'llm test' 命令"""
    logger.info(
        f"测试模型连通性: {model_name.result}",
        command="LLM Manage",
        session=arp.header_result,
    )
    await llm_cmd.send(f"正在测试模型 '{model_name.result}'，请稍候...")

    _success, message = await DataSource.test_model_connectivity(model_name.result)
    await llm_cmd.finish(message)


@llm_cmd.assign("keys")
async def handle_keys(arp: Arparma, provider_name: Match[str]):
    """处理 'llm keys' 命令"""
    logger.info(
        f"查看提供商API Key状态: {provider_name.result}",
        command="LLM Manage",
        session=arp.header_result,
    )
    sorted_stats = await DataSource.get_key_status(provider_name.result)
    if not sorted_stats:
        await llm_cmd.finish(
            f"未找到提供商 '{provider_name.result}' 或其没有配置API Keys。"
        )

    image = await Presenters.format_key_status_as_image(
        provider_name.result, sorted_stats
    )
    await llm_cmd.finish(MessageUtils.build_message(image))


@llm_cmd.assign("mcp")
async def handle_mcp(arp: Arparma, event: Event):
    """处理 'llm mcp' 命令"""
    is_enable = None
    targets = ()

    if arp.exist("mcp.重载"):
        await DataSource.reload_mcp_config()
        await llm_cmd.finish("✅ MCP 配置已成功重载并应用！")

    if arp.exist("mcp.添加"):
        raw_text = event.get_plaintext()
        import re

        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not match:
            await llm_cmd.finish("❌ 无法从输入中提取 JSON，请确保包含完整的 {} 括号。")

        json_str = match.group(0)
        _success, msg = await DataSource.add_mcp_servers_from_json(json_str)
        await llm_cmd.finish(msg)

    if arp.exist("mcp.删除"):
        targets = arp.query("mcp.删除.targets", ())
        if isinstance(targets, str):
            targets = (targets,)

        if not targets:
            await llm_cmd.finish(
                "请指定需要删除的 MCP ID 或名称，例如：llm mcp del 1 3"
            )

        valid_names, invalid_targets = await DataSource.resolve_mcp_targets(targets)
        if not valid_names:
            await llm_cmd.finish(
                f"⚠️ 未找到任何有效的 MCP 服务。\n无效目标: {', '.join(invalid_targets)}"
            )

        confirm_msg = (
            f"⚠️ 即将永久删除以下 {len(valid_names)} 个 MCP 服务:\n"
            f"{', '.join(valid_names)}\n\n"
            "确认删除请在 30 秒内回复「Y」或「是」，取消请回复其他内容。"
        )
        resp = await prompt(confirm_msg, timeout=30)
        if resp is None:
            await llm_cmd.finish("⏳ 等待超时，已自动取消删除操作。")

        user_input = resp.extract_plain_text().strip().lower()
        if user_input not in {"y", "yes", "是", "1", "确认", "ok"}:
            await llm_cmd.finish("🛑 已取消删除操作。")

        await DataSource.delete_mcp_servers(valid_names)
        await llm_cmd.finish(f"🗑️ 已成功删除 MCP 服务: {', '.join(valid_names)}")

    if arp.exist("mcp.开启"):
        is_enable = True
        targets = arp.query("mcp.开启.targets", ())
    elif arp.exist("mcp.关闭"):
        is_enable = False
        targets = arp.query("mcp.关闭.targets", ())

    if is_enable is None:
        logger.info("获取 MCP 列表", command="LLM Manage", session=arp.header_result)
        mcp_list = await DataSource.get_mcp_list()
        image = await Presenters.format_mcp_list_as_image(mcp_list)
        await llm_cmd.finish(MessageUtils.build_message(image))

    if not targets:
        await llm_cmd.finish(
            "请指定需要操作的 MCP ID 或名称，例如：llm mcp 开启 1 3 bingcn"
        )

    if isinstance(targets, str):
        targets = (targets,)

    logger.info(
        f"批量{'开启' if is_enable else '关闭'} MCP: {targets}",
        command="LLM Manage",
        session=arp.header_result,
    )

    success_names, invalid_targets = await DataSource.toggle_mcp_servers(
        targets, is_enable
    )

    msg_parts = []
    if success_names:
        status_txt = "开启" if is_enable else "关闭"
        msg_parts.append(
            f"✅ 已成功{status_txt} {len(success_names)} 个"
            f"MCP 服务:\n{', '.join(success_names)}"
        )
    if invalid_targets:
        msg_parts.append(f"⚠️ 以下 ID 或名称无效被忽略:\n{', '.join(invalid_targets)}")

    if not msg_parts:
        msg_parts.append("没有任何配置被修改。")

    await llm_cmd.finish("\n\n".join(msg_parts))
