import asyncio
import random
import traceback
from typing import ClassVar

from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Bot as V11Bot
from nonebot.exception import ActionFailed
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Receipt, Reference
from nonebot_plugin_session import EventSession

from zhenxun.models.group_console import GroupConsole
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.platform import PlatformUtils

from .models import BroadcastDetailResult, BroadcastResult
from .utils import custom_nodes_to_v11_nodes, uni_message_to_v11_list_of_dicts


class BroadcastManager:
    """广播管理器"""

    _last_broadcast_msg_ids: ClassVar[dict[str, int]] = {}

    @staticmethod
    def _get_session_info(session: EventSession | None) -> str:
        """获取会话信息字符串"""
        if not session:
            return ""

        try:
            platform = getattr(session, "platform", "unknown")
            session_id = str(session)
            return f"[{platform}:{session_id}]"
        except Exception:
            return "[session-info-error]"

    @staticmethod
    def log_error(
        message: str, error: Exception, session: EventSession | None = None, **kwargs
    ):
        """记录错误日志"""
        session_info = BroadcastManager._get_session_info(session)
        error_type = type(error).__name__
        stack_trace = traceback.format_exc()
        error_details = f"\n类型: {error_type}\n信息: {error!s}\n堆栈: {stack_trace}"

        logger.error(
            f"{session_info} {message}{error_details}", "广播", e=error, **kwargs
        )

    @staticmethod
    def log_warning(message: str, session: EventSession | None = None, **kwargs):
        """记录警告级别日志"""
        session_info = BroadcastManager._get_session_info(session)
        logger.warning(f"{session_info} {message}", "广播", **kwargs)

    @staticmethod
    def log_info(message: str, session: EventSession | None = None, **kwargs):
        """记录信息级别日志"""
        session_info = BroadcastManager._get_session_info(session)
        logger.info(f"{session_info} {message}", "广播", **kwargs)

    @classmethod
    def get_last_broadcast_msg_ids(cls) -> dict[str, int]:
        """获取最近广播消息ID"""
        return cls._last_broadcast_msg_ids.copy()

    @classmethod
    def clear_last_broadcast_msg_ids(cls) -> None:
        """清空消息ID记录"""
        cls._last_broadcast_msg_ids.clear()

    @classmethod
    async def get_all_groups(cls, bot: Bot) -> tuple[list[GroupConsole], str]:
        """获取群组列表"""
        return await PlatformUtils.get_group_list(bot)

    @classmethod
    async def send(
        cls, bot: Bot, message: UniMessage, session: EventSession
    ) -> BroadcastResult:
        """发送广播到所有群组"""
        logger.debug(
            f"开始广播(send - 广播到所有群组)，Bot ID: {bot.self_id}",
            "广播",
            session=session,
        )

        logger.debug("清空上一次的广播消息ID记录", "广播", session=session)
        cls.clear_last_broadcast_msg_ids()

        all_groups, _ = await cls.get_all_groups(bot)
        return await cls.send_to_specific_groups(bot, message, all_groups, session)

    @classmethod
    async def send_to_specific_groups(
        cls,
        bot: Bot,
        message: UniMessage,
        target_groups: list[GroupConsole],
        session_info: EventSession | str | None = None,
    ) -> BroadcastResult:
        """发送广播到指定群组"""
        log_session = session_info or bot.self_id
        logger.debug(
            f"开始广播，目标 {len(target_groups)} 个群组，Bot ID: {bot.self_id}",
            "广播",
            session=log_session,
        )

        if not target_groups:
            logger.debug("目标群组列表为空，广播结束", "广播", session=log_session)
            return 0, 0

        platform = PlatformUtils.get_platform(bot)
        is_forward_broadcast = any(
            isinstance(seg, Reference) and getattr(seg, "nodes", None)
            for seg in message
        )

        if platform == "qq" and isinstance(bot, V11Bot) and is_forward_broadcast:
            if (
                len(message) == 1
                and isinstance(message[0], Reference)
                and getattr(message[0], "nodes", None)
            ):
                nodes_list = getattr(message[0], "nodes", [])
                v11_nodes = custom_nodes_to_v11_nodes(nodes_list)
                node_count = len(v11_nodes)
                logger.debug(
                    f"从 UniMessage<Reference> 构造转发节点数: {node_count}",
                    "广播",
                    session=log_session,
                )
            else:
                logger.warning(
                    "广播消息包含合并转发段和其他段，将尝试打平成一个节点发送",
                    "广播",
                    session=log_session,
                )
                v11_content_list = uni_message_to_v11_list_of_dicts(message)
                v11_nodes = (
                    [
                        {
                            "type": "node",
                            "data": {
                                "user_id": bot.self_id,
                                "nickname": "广播",
                                "content": v11_content_list,
                            },
                        }
                    ]
                    if v11_content_list
                    else []
                )

            if not v11_nodes:
                logger.warning(
                    "构造出的 V11 合并转发节点为空，无法发送",
                    "广播",
                    session=log_session,
                )
                return 0, len(target_groups)
            success_count, error_count, skip_count = await cls._broadcast_forward(
                bot, log_session, target_groups, v11_nodes
            )
        else:
            if is_forward_broadcast:
                logger.warning(
                    f"合并转发消息在适配器 ({platform}) 不支持，将作为普通消息发送",
                    "广播",
                    session=log_session,
                )
            success_count, error_count, skip_count = await cls._broadcast_normal(
                bot, log_session, target_groups, message
            )

        total = len(target_groups)
        stats = f"成功: {success_count}, 失败: {error_count}"
        stats += f", 跳过: {skip_count}, 总计: {total}"
        logger.debug(
            f"广播统计 - {stats}",
            "广播",
            session=log_session,
        )

        msg_ids = cls.get_last_broadcast_msg_ids()
        if msg_ids:
            id_list_str = ", ".join([f"{k}:{v}" for k, v in msg_ids.items()])
            logger.debug(
                f"广播结束，记录了 {len(msg_ids)} 条消息ID: {id_list_str}",
                "广播",
                session=log_session,
            )
        else:
            logger.warning(
                "广播结束，但没有记录任何消息ID",
                "广播",
                session=log_session,
            )

        return success_count, error_count

    @classmethod
    async def _extract_message_id_from_result(
        cls,
        result: dict | Receipt,
        group_key: str,
        session_info: EventSession | str,
        msg_type: str = "普通",
    ) -> None:
        """提取消息ID并记录"""
        if isinstance(result, dict) and "message_id" in result:
            msg_id = result["message_id"]
            try:
                msg_id_int = int(msg_id)
                cls._last_broadcast_msg_ids[group_key] = msg_id_int
                logger.debug(
                    f"记录群 {group_key} 的{msg_type}消息ID: {msg_id_int}",
                    "广播",
                    session=session_info,
                )
            except (ValueError, TypeError):
                logger.warning(
                    f"{msg_type}结果中的 message_id 不是有效整数: {msg_id}",
                    "广播",
                    session=session_info,
                )
        elif isinstance(result, Receipt) and result.msg_ids:
            try:
                first_id_info = result.msg_ids[0]
                msg_id = None
                if isinstance(first_id_info, dict) and "message_id" in first_id_info:
                    msg_id = first_id_info["message_id"]
                    logger.debug(
                        f"从 Receipt.msg_ids[0] 提取到 ID: {msg_id}",
                        "广播",
                        session=session_info,
                    )
                elif isinstance(first_id_info, int | str):
                    msg_id = first_id_info
                    logger.debug(
                        f"从 Receipt.msg_ids[0] 提取到原始ID: {msg_id}",
                        "广播",
                        session=session_info,
                    )

                if msg_id is not None:
                    try:
                        msg_id_int = int(msg_id)
                        cls._last_broadcast_msg_ids[group_key] = msg_id_int
                        logger.debug(
                            f"记录群 {group_key} 的消息ID: {msg_id_int}",
                            "广播",
                            session=session_info,
                        )
                    except (ValueError, TypeError):
                        logger.warning(
                            f"提取的ID ({msg_id}) 不是有效整数",
                            "广播",
                            session=session_info,
                        )
                else:
                    info_str = str(first_id_info)
                    logger.warning(
                        f"无法从 Receipt.msg_ids[0] 提取ID: {info_str}",
                        "广播",
                        session=session_info,
                    )
            except IndexError:
                logger.warning("Receipt.msg_ids 为空", "广播", session=session_info)
            except Exception as e_extract:
                logger.error(
                    f"从 Receipt 提取 msg_id 时出错: {e_extract}",
                    "广播",
                    session=session_info,
                    e=e_extract,
                )
        else:
            logger.warning(
                f"发送成功但无法从结果获取消息 ID. 结果: {result}",
                "广播",
                session=session_info,
            )

    @classmethod
    async def _check_group_availability(cls, bot: Bot, group: GroupConsole) -> bool:
        """检查群组是否可用"""
        if not group.group_id:
            return False

        if await CommonUtils.task_is_block(bot, "broadcast", group.group_id):
            return False

        return True

    @classmethod
    async def _broadcast_forward(
        cls,
        bot: V11Bot,
        session_info: EventSession | str,
        group_list: list[GroupConsole],
        v11_nodes: list[dict],
    ) -> BroadcastDetailResult:
        """发送合并转发"""
        success_count = 0
        error_count = 0
        skip_count = 0

        for _, group in enumerate(group_list):
            group_key = group.group_id or group.channel_id

            if not await cls._check_group_availability(bot, group):
                skip_count += 1
                continue

            try:
                result = await bot.send_group_forward_msg(
                    group_id=int(group.group_id), messages=v11_nodes
                )

                logger.debug(
                    f"合并转发消息发送结果: {result}, 类型: {type(result)}",
                    "广播",
                    session=session_info,
                )

                await cls._extract_message_id_from_result(
                    result, group_key, session_info, "合并转发"
                )

                success_count += 1
                await asyncio.sleep(random.randint(1, 3))
            except ActionFailed as af_e:
                error_count += 1
                logger.error(
                    f"发送失败(合并转发) to {group_key}: {af_e}",
                    "广播",
                    session=session_info,
                    e=af_e,
                )
            except Exception as e:
                error_count += 1
                logger.error(
                    f"发送失败(合并转发) to {group_key}: {e}",
                    "广播",
                    session=session_info,
                    e=e,
                )

        return success_count, error_count, skip_count

    @classmethod
    async def _broadcast_normal(
        cls,
        bot: Bot,
        session_info: EventSession | str,
        group_list: list[GroupConsole],
        message: UniMessage,
    ) -> BroadcastDetailResult:
        """发送普通消息"""
        success_count = 0
        error_count = 0
        skip_count = 0

        for _, group in enumerate(group_list):
            group_key = (
                f"{group.group_id}:{group.channel_id}"
                if group.channel_id
                else str(group.group_id)
            )

            if not await cls._check_group_availability(bot, group):
                skip_count += 1
                continue

            try:
                target = PlatformUtils.get_target(
                    group_id=group.group_id, channel_id=group.channel_id
                )

                if target:
                    receipt: Receipt = await message.send(target, bot=bot)

                    logger.debug(
                        f"广播消息发送结果: {receipt}, 类型: {type(receipt)}",
                        "广播",
                        session=session_info,
                    )

                    await cls._extract_message_id_from_result(
                        receipt, group_key, session_info
                    )

                    success_count += 1
                    await asyncio.sleep(random.randint(1, 3))
                else:
                    logger.warning(
                        "target为空", "广播", session=session_info, target=group_key
                    )
                    skip_count += 1
            except Exception as e:
                error_count += 1
                logger.error(
                    f"发送失败(普通) to {group_key}: {e}",
                    "广播",
                    session=session_info,
                    e=e,
                )

        return success_count, error_count, skip_count

    @classmethod
    async def recall_last_broadcast(
        cls, bot: Bot, session_info: EventSession | str
    ) -> BroadcastResult:
        """撤回最近广播"""
        msg_ids_to_recall = cls.get_last_broadcast_msg_ids()

        if not msg_ids_to_recall:
            logger.warning(
                "没有找到最近的广播消息ID记录", "广播撤回", session=session_info
            )
            return 0, 0

        id_list_str = ", ".join([f"{k}:{v}" for k, v in msg_ids_to_recall.items()])
        logger.debug(
            f"找到 {len(msg_ids_to_recall)} 条广播消息ID记录: {id_list_str}",
            "广播撤回",
            session=session_info,
        )

        success_count = 0
        error_count = 0

        logger.info(
            f"准备撤回 {len(msg_ids_to_recall)} 条广播消息",
            "广播撤回",
            session=session_info,
        )

        for group_key, msg_id in msg_ids_to_recall.items():
            try:
                logger.debug(
                    f"尝试撤回消息 (ID: {msg_id}) in {group_key}",
                    "广播撤回",
                    session=session_info,
                )
                await bot.call_api("delete_msg", message_id=msg_id)
                success_count += 1
            except ActionFailed as af_e:
                retcode = getattr(af_e, "retcode", None)
                wording = getattr(af_e, "wording", "")
                if retcode == 100 and "MESSAGE_NOT_FOUND" in wording.upper():
                    logger.warning(
                        f"消息 (ID: {msg_id}) 可能已被撤回或不存在于 {group_key}",
                        "广播撤回",
                        session=session_info,
                    )
                elif retcode == 300 and "delete message" in wording.lower():
                    logger.warning(
                        f"消息 (ID: {msg_id}) 可能已被撤回或不存在于 {group_key}",
                        "广播撤回",
                        session=session_info,
                    )
                else:
                    error_count += 1
                    logger.error(
                        f"撤回消息失败 (ID: {msg_id}) in {group_key}: {af_e}",
                        "广播撤回",
                        session=session_info,
                        e=af_e,
                    )
            except Exception as e:
                error_count += 1
                logger.error(
                    f"撤回消息时发生未知错误 (ID: {msg_id}) in {group_key}: {e}",
                    "广播撤回",
                    session=session_info,
                    e=e,
                )
            await asyncio.sleep(0.2)

        logger.debug("撤回操作完成，清空消息ID记录", "广播撤回", session=session_info)
        cls.clear_last_broadcast_msg_ids()

        return success_count, error_count
