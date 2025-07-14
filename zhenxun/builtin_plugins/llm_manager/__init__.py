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

    llm default [Provider/ModelName]
      - 查看或设置全局默认模型。
      - 不带参数: 查看当前默认模型。
      - 带参数: 设置新的默认模型。
      - 例子: llm default Gemini/gemini-2.0-flash

    llm test <Provider/ModelName>
      - 测试指定模型的连通性和API Key有效性。

    llm keys <ProviderName>
      - 查看指定提供商的所有API Key状态。

    llm reset-key <ProviderName> [--key <api_key>]
      - 重置提供商的所有或指定API Key的失败状态。
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
        Subcommand("list", alias=["ls"], help_text="查看模型列表"),
        Subcommand("info", Args["model_name", str], help_text="查看模型详情"),
        Subcommand("default", Args["model_name?", str], help_text="查看或设置默认模型"),
        Subcommand(
            "test", Args["model_name", str], alias=["ping"], help_text="测试模型连通性"
        ),
        Subcommand("keys", Args["provider_name", str], help_text="查看API密钥状态"),
        Subcommand(
            "reset-key",
            Args["provider_name", str],
            Option("--key", Args["api_key", str], help_text="指定要重置的API Key"),
            help_text="重置API Key状态",
        ),
        Option("--all", action=store_true, help_text="显示所有条目"),
    ),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@llm_cmd.assign("list")
async def handle_list(arp: Arparma, show_all: Query[bool] = Query("all")):
    """处理 'llm list' 命令"""
    logger.info("获取LLM模型列表", command="LLM Manage", session=arp.header_result)
    models = await DataSource.get_model_list(show_all=show_all.result)

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


@llm_cmd.assign("default")
async def handle_default(arp: Arparma, model_name: Match[str]):
    """处理 'llm default' 命令"""
    if model_name.available:
        logger.info(
            f"设置默认模型为: {model_name.result}",
            command="LLM Manage",
            session=arp.header_result,
        )
        success, message = await DataSource.set_default_model(model_name.result)
        await llm_cmd.finish(message)
    else:
        logger.info("查看默认模型", command="LLM Manage", session=arp.header_result)
        current_default = await DataSource.get_default_model()
        await llm_cmd.finish(f"当前全局默认模型为: {current_default or '未设置'}")


@llm_cmd.assign("test")
async def handle_test(arp: Arparma, model_name: Match[str]):
    """处理 'llm test' 命令"""
    logger.info(
        f"测试模型连通性: {model_name.result}",
        command="LLM Manage",
        session=arp.header_result,
    )
    await llm_cmd.send(f"正在测试模型 '{model_name.result}'，请稍候...")

    success, message = await DataSource.test_model_connectivity(model_name.result)
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


@llm_cmd.assign("reset-key")
async def handle_reset_key(
    arp: Arparma, provider_name: Match[str], api_key: Match[str]
):
    """处理 'llm reset-key' 命令"""
    key_to_reset = api_key.result if api_key.available else None
    log_msg = f"重置 {provider_name.result} 的 " + (
        "指定API Key" if key_to_reset else "所有API Keys"
    )
    logger.info(log_msg, command="LLM Manage", session=arp.header_result)

    success, message = await DataSource.reset_key(provider_name.result, key_to_reset)
    await llm_cmd.finish(message)
