from datetime import datetime

from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import Alconna, Args, Arparma, Match, Subcommand, on_alconna
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_uninfo import Uninfo
from nonebot_plugin_waiter import prompt_until

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.depends import UserName
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.utils import is_number

from .data_source import BankManager

__plugin_meta__ = PluginMetadata(
    name="小真寻银行",
    description="""
    小真寻银行，提供高品质的存款！当好感度等级达到指初识时，小真寻会偷偷的帮助你哦。
    存款额度与好感度有关，每日存款次数有限制。
    基础存款提供基础利息
    每日存款提供高额利息
    """.strip(),
    usage="""
    指令：
        存款 [金额]
        取款 [金额]
        银行信息
        我的银行信息
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        menu_type="群内小游戏",
        configs=[
            RegisterConfig(
                key="sign_max_deposit",
                value=100,
                help="好感度换算存款金额比例，当值是100时，最大存款金额=好感度*100，存款的最低金额是100（强制）",
                default_value=100,
                type=int,
            ),
            RegisterConfig(
                key="max_daily_deposit_count",
                value=3,
                help="每日最大存款次数",
                default_value=3,
                type=int,
            ),
            RegisterConfig(
                key="rate_range",
                value=[0.0005, 0.001],
                help="小时利率范围",
                default_value=[0.0005, 0.001],
                type=list[float],
            ),
            RegisterConfig(
                key="impression_event",
                value=25,
                help="到达指定好感度时随机提高或降低利率",
                default_value=25,
                type=int,
            ),
            RegisterConfig(
                key="impression_event_range",
                value=[0.00001, 0.0003],
                help="到达指定好感度时随机提高或降低利率",
                default_value=[0.00001, 0.0003],
                type=list[float],
            ),
            RegisterConfig(
                key="impression_event_prop",
                value=0.3,
                help="到达指定好感度时随机提高或降低利率触发概率",
                default_value=0.3,
                type=float,
            ),
        ],
    ).to_dict(),
)


_matcher = on_alconna(
    Alconna(
        "mahiro-bank",
        Subcommand("deposit", Args["amount?", int]),
        Subcommand("withdraw", Args["amount?", int]),
        Subcommand("user-info"),
        Subcommand("bank-info"),
        # Subcommand("loan", Args["amount?", int]),
        # Subcommand("repayment", Args["amount?", int]),
    ),
    priority=5,
    block=True,
)

_matcher.shortcut(
    r"存款\s*(?P<amount>\d+)?",
    command="mahiro-bank",
    arguments=["deposit", "{amount}"],
    prefix=True,
)

_matcher.shortcut(
    r"取款\s*(?P<withdraw>\d+)?",
    command="mahiro-bank",
    arguments=["withdraw", "{withdraw}"],
    prefix=True,
)

_matcher.shortcut(
    r"我的银行信息",
    command="mahiro-bank",
    arguments=["user-info"],
    prefix=True,
)

_matcher.shortcut(
    r"银行信息",
    command="mahiro-bank",
    arguments=["bank-info"],
    prefix=True,
)


async def get_amount(handle_type: str) -> int:
    amount_num = await prompt_until(
        f"请输入{handle_type}金币数量",
        lambda msg: is_number(msg.extract_plain_text()),
        timeout=60,
        retry=3,
        retry_prompt="输入错误，请输入数字。剩余次数：{count}",
    )
    if not amount_num:
        await MessageUtils.build_message(
            "输入超时了哦，小真寻柜员以取消本次存款操作..."
        ).finish()
    return int(amount_num.extract_plain_text())


@_matcher.assign("deposit")
async def _(session: Uninfo, arparma: Arparma, amount: Match[int]):
    amount_num = amount.result if amount.available else await get_amount("存款")
    if result := await BankManager.deposit_check(session.user.id, amount_num):
        await MessageUtils.build_message(result).finish(reply_to=True)
    _, rate, event_rate = await BankManager.deposit(session.user.id, amount_num)
    result = (
        f"存款成功！\n此次存款金额为: {amount.result}\n"
        f"当前小时利率为: {rate * 100:.2f}%"
    )
    effective_hour = int(24 - datetime.now().hour)
    if event_rate:
        result += f"（小真寻偷偷将小时利率给你增加了 {event_rate:.2f}% 哦）"
    result += (
        f"\n预计总收益为: {int(amount.result * rate * effective_hour) or 1} 金币。"
    )
    logger.info(
        f"小真寻银行存款:{amount_num},当前存款数:{amount.result},存款小时利率: {rate}",
        arparma.header_result,
        session=session,
    )
    await MessageUtils.build_message(result).finish(at_sender=True)


@_matcher.assign("withdraw")
async def _(session: Uninfo, arparma: Arparma, amount: Match[int]):
    amount_num = amount.result if amount.available else await get_amount("取款")
    if result := await BankManager.withdraw_check(session.user.id, amount_num):
        await MessageUtils.build_message(result).finish(reply_to=True)
    try:
        user = await BankManager.withdraw(session.user.id, amount_num)
        result = (
            f"取款成功！\n当前取款金额为: {amount_num}\n当前存款金额为: {user.amount}"
        )
        logger.info(
            f"小真寻银行取款:{amount_num}, 当前存款数:{user.amount},"
            f" 存款小时利率:{user.rate}",
            arparma.header_result,
            session=session,
        )
        await MessageUtils.build_message(result).finish(reply_to=True)
    except ValueError:
        await MessageUtils.build_message("你的银行内的存款数量不足哦...").finish(
            reply_to=True
        )


@_matcher.assign("user-info")
async def _(session: Uninfo, arparma: Arparma, uname: str = UserName()):
    result = await BankManager.get_user_info(session, uname)
    await MessageUtils.build_message(result).send()
    logger.info("查看银行个人信息", arparma.header_result, session=session)


@_matcher.assign("bank-info")
async def _(session: Uninfo, arparma: Arparma):
    result = await BankManager.get_bank_info()
    await MessageUtils.build_message(result).send()
    logger.info("查看银行信息", arparma.header_result, session=session)


# @_matcher.assign("loan")
# async def _(session: Uninfo, arparma: Arparma, amount: Match[int]):
#     amount_num = amount.result if amount.available else await get_amount("贷款")
#     if amount_num <= 0:
#         await MessageUtils.build_message("贷款数量必须大于 0 啊笨蛋！").finish()
#     try:
#         user, event_rate = await BankManager.loan(session.user.id, amount_num)
#         result = (
#             f"贷款成功！\n当前贷金额为: {user.loan_amount}"
#             f"\n当前利率为: {user.loan_rate * 100}%"
#         )
#         if event_rate:
#             result += f"（小真寻偷偷将利率给你降低了 {event_rate}% 哦）"
#         result += f"\n预计每小时利息为:{int(user.loan_amount * user.loan_rate)}金币。"
#         logger.info(
#             f"小真寻银行贷款: {amount_num}, 当前贷款数: {user.loan_amount}, "
#             f"贷款利率: {user.loan_rate}",
#             arparma.header_result,
#             session=session,
#         )
#     except ValueError:
#         await MessageUtils.build_message(
#             "贷款数量超过最大限制，请签到提升好感度获取更多额度吧..."
#         ).finish(reply_to=True)


# @_matcher.assign("repayment")
# async def _(session: Uninfo, arparma: Arparma, amount: Match[int]):
#     amount_num = amount.result if amount.available else await get_amount("还款")
#     if amount_num <= 0:
#         await MessageUtils.build_message("还款数量必须大于 0 啊笨蛋！").finish()
#     user = await BankManager.repayment(session.user.id, amount_num)
#     result = (f"还款成功！\n当前还款金额为: {amount_num}\n"
# f"当前贷款金额为: {user.loan_amount}")
#     logger.info(
#         f"小真寻银行还款:{amount_num},当前贷款数:{user.amount}, 贷款利率:{user.rate}",
#         arparma.header_result,
#         session=session,
#     )
#     await MessageUtils.build_message(result).finish(at_sender=True)


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=0,
)
async def _():
    await BankManager.settlement()
    logger.info("小真寻银行结算", "定时任务")
