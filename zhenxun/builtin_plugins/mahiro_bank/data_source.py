import asyncio
from datetime import datetime, timedelta
import random

from nonebot_plugin_htmlrender import template_to_pic
from nonebot_plugin_uninfo import Uninfo
from tortoise.expressions import RawSQL
from tortoise.functions import Count, Sum

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import TEMPLATE_PATH
from zhenxun.models.mahiro_bank import MahiroBank
from zhenxun.models.mahiro_bank_log import MahiroBankLog
from zhenxun.models.sign_user import SignUser
from zhenxun.models.user_console import UserConsole
from zhenxun.utils.enum import BankHandleType, GoldHandle
from zhenxun.utils.platform import PlatformUtils

base_config = Config.get("mahiro_bank")


class BankManager:
    @classmethod
    async def random_event(cls, impression: float):
        """随机事件"""
        impression_event = base_config.get("impression_event")
        impression_event_prop = base_config.get("impression_event_prop")
        impression_event_range = base_config.get("impression_event_range")
        if impression >= impression_event and random.random() < impression_event_prop:
            """触发好感度事件"""
            return random.uniform(impression_event_range[0], impression_event_range[1])
        return None

    @classmethod
    async def deposit_check(cls, user_id: str, amount: int) -> str | None:
        """检查存款是否合法

        参数:
            user_id: 用户id
            amount: 存款金额

        返回:
            str | None: 存款信息
        """
        if amount <= 0:
            return "存款数量必须大于 0 啊笨蛋！"
        user, sign_user, bank_user = await asyncio.gather(
            *[
                UserConsole.get_user(user_id),
                SignUser.get_user(user_id),
                cls.get_user(user_id),
            ]
        )
        sign_max_deposit: int = base_config.get("sign_max_deposit")
        max_deposit = max(int(float(sign_user.impression) * sign_max_deposit), 100)
        if user.gold < amount:
            return f"金币数量不足，当前你的金币为：{user.gold}."
        if bank_user.amount + amount > max_deposit:
            return (
                f"存款超过上限，存款上限为：{max_deposit}，"
                f"当前你的还可以存款金额：{max_deposit - bank_user.amount}。"
            )
        max_daily_deposit_count: int = base_config.get("max_daily_deposit_count")
        today_deposit_count = len(await cls.get_user_deposit(user_id))
        if today_deposit_count >= max_daily_deposit_count:
            return f"存款次数超过上限，每日存款次数上限为：{max_daily_deposit_count}。"
        return None

    @classmethod
    async def withdraw_check(cls, user_id: str, amount: int) -> str | None:
        """检查取款是否合法

        参数:
            user_id: 用户id
            amount: 取款金额

        返回:
            str | None: 取款信息
        """
        if amount <= 0:
            return "取款数量必须大于 0 啊笨蛋！"
        user = await cls.get_user(user_id)
        data_list = await cls.get_user_deposit(user_id)
        lock_amount = sum(data.amount for data in data_list)
        if user.amount - lock_amount < amount:
            return (
                "取款金额不足，当前你的存款为："
                f"{user.amount}（{lock_amount}已被锁定）！"
            )
        return None

    @classmethod
    async def get_user_deposit(
        cls, user_id: str, is_completed: bool = False
    ) -> list[MahiroBankLog]:
        """获取用户今日存款次数

        参数:
            user_id: 用户id

        返回:
            list[MahiroBankLog]: 存款列表
        """
        return await MahiroBankLog.filter(
            user_id=user_id,
            handle_type=BankHandleType.DEPOSIT,
            is_completed=is_completed,
        )

    @classmethod
    async def get_user(cls, user_id: str) -> MahiroBank:
        """查询余额

        参数:
            user_id: 用户id

        返回:
            MahiroBank
        """
        user, _ = await MahiroBank.get_or_create(user_id=user_id)
        return user

    @classmethod
    async def get_user_data(
        cls,
        user_id: str,
        data_type: BankHandleType,
        is_completed: bool = False,
        count: int = 5,
    ) -> list[MahiroBankLog]:
        return (
            await MahiroBankLog.filter(
                user_id=user_id, handle_type=data_type, is_completed=is_completed
            )
            .order_by("-id")
            .limit(count)
            .all()
        )

    @classmethod
    async def complete_projected_revenue(cls, user_id: str) -> int:
        """预计收益

        参数:
            user_id: 用户id

        返回:
            int: 预计收益金额
        """
        deposit_list = await cls.get_user_deposit(user_id)
        if not deposit_list:
            return 0
        return int(
            sum(
                deposit.rate * deposit.amount * deposit.effective_hour
                for deposit in deposit_list
            )
        )

    @classmethod
    async def get_user_info(cls, session: Uninfo, uname: str) -> bytes:
        """获取用户数据

        参数:
            session: Uninfo
            uname: 用户id

        返回:
            bytes: 图片数据
        """
        user_id = session.user.id
        user = await cls.get_user(user_id=user_id)
        (
            rank,
            deposit_count,
            user_today_deposit,
            projected_revenue,
            sum_data,
        ) = await asyncio.gather(
            *[
                MahiroBank.filter(amount__gt=user.amount).count(),
                MahiroBankLog.filter(user_id=user_id).count(),
                cls.get_user_deposit(user_id),
                cls.complete_projected_revenue(user_id),
                MahiroBankLog.filter(
                    user_id=user_id, handle_type=BankHandleType.INTEREST
                )
                .annotate(sum=Sum("amount"))
                .values("sum"),
            ]
        )
        now = datetime.now()
        end_time = (
            now
            + timedelta(days=1)
            - timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)
        )
        today_deposit_amount = sum(deposit.amount for deposit in user_today_deposit)
        deposit_list = [
            {
                "id": deposit.id,
                "date": now.date(),
                "start_time": str(deposit.create_time).split(".")[0],
                "end_time": end_time.replace(microsecond=0),
                "amount": deposit.amount,
                "rate": f"{deposit.rate * 100:.2f}",
                "projected_revenue": int(
                    deposit.amount * deposit.rate * deposit.effective_hour
                )
                or 1,
            }
            for deposit in user_today_deposit
        ]
        platform = PlatformUtils.get_platform(session)
        data = {
            "name": uname,
            "rank": rank + 1,
            "avatar_url": PlatformUtils.get_user_avatar_url(
                user_id, platform, session.self_id
            ),
            "amount": user.amount,
            "deposit_count": deposit_count,
            "today_deposit_count": len(user_today_deposit),
            "cumulative_gain": sum_data[0]["sum"] or 0,
            "projected_revenue": projected_revenue,
            "today_deposit_amount": today_deposit_amount,
            "deposit_list": deposit_list,
            "create_time": now.replace(microsecond=0),
        }
        return await template_to_pic(
            template_path=str((TEMPLATE_PATH / "mahiro_bank").absolute()),
            template_name="user.html",
            templates={"data": data},
            pages={
                "viewport": {"width": 386, "height": 700},
                "base_url": f"file://{TEMPLATE_PATH}",
            },
            wait=2,
        )

    @classmethod
    async def get_bank_info(cls) -> bytes:
        now = datetime.now()
        now_start = now - timedelta(
            hours=now.hour, minutes=now.minute, seconds=now.second
        )
        (
            bank_data,
            today_count,
            interest_amount,
            active_user_count,
            date_data,
        ) = await asyncio.gather(
            *[
                MahiroBank.annotate(
                    amount_sum=Sum("amount"), user_count=Count("id")
                ).values("amount_sum", "user_count"),
                MahiroBankLog.filter(
                    create_time__gt=now_start, handle_type=BankHandleType.DEPOSIT
                ).count(),
                MahiroBankLog.filter(handle_type=BankHandleType.INTEREST)
                .annotate(amount_sum=Sum("amount"))
                .values("amount_sum"),
                MahiroBankLog.filter(
                    create_time__gte=now_start - timedelta(days=7),
                    handle_type=BankHandleType.DEPOSIT,
                )
                .annotate(count=Count("user_id", distinct=True))
                .values("count"),
                MahiroBankLog.filter(
                    create_time__gte=now_start - timedelta(days=7),
                    handle_type=BankHandleType.DEPOSIT,
                )
                .annotate(date=RawSQL("DATE(create_time)"), total_amount=Sum("amount"))
                .group_by("date")
                .values("date", "total_amount"),
            ]
        )
        date2cnt = {str(date["date"]): date["total_amount"] for date in date_data}
        date = now.date()
        e_date, e_amount = [], []
        for _ in range(7):
            if str(date) in date2cnt:
                e_amount.append(date2cnt[str(date)])
            else:
                e_amount.append(0)
            e_date.append(str(date)[5:])
            date -= timedelta(days=1)
        e_date.reverse()
        e_amount.reverse()
        date = 1
        lasted_log = await MahiroBankLog.annotate().order_by("create_time").first()
        if lasted_log:
            date = now.date() - lasted_log.create_time.date()
            date = (date.days or 1) + 1
        data = {
            "amount_sum": bank_data[0]["amount_sum"],
            "user_count": bank_data[0]["user_count"],
            "today_count": today_count,
            "day_amount": int(bank_data[0]["amount_sum"] / date),
            "interest_amount": interest_amount[0]["amount_sum"] or 0,
            "active_user_count": active_user_count[0]["count"] or 0,
            "e_data": e_date,
            "e_amount": e_amount,
            "create_time": now.replace(microsecond=0),
        }
        return await template_to_pic(
            template_path=str((TEMPLATE_PATH / "mahiro_bank").absolute()),
            template_name="bank.html",
            templates={"data": data},
            pages={
                "viewport": {"width": 450, "height": 750},
                "base_url": f"file://{TEMPLATE_PATH}",
            },
            wait=2,
        )

    @classmethod
    async def deposit(
        cls, user_id: str, amount: int
    ) -> tuple[MahiroBank, float, float | None]:
        """存款

        参数:
            user_id: 用户id
            amount: 存款数量

        返回:
            tuple[MahiroBank, float, float]: MahiroBank，利率，增加的利率
        """
        rate_range = base_config.get("rate_range")
        rate = random.uniform(rate_range[0], rate_range[1])
        sign_user = await SignUser.get_user(user_id)
        random_add_rate = await cls.random_event(float(sign_user.impression))
        if random_add_rate:
            rate += random_add_rate
        await UserConsole.reduce_gold(user_id, amount, GoldHandle.PLUGIN, "bank")
        return await MahiroBank.deposit(user_id, amount, rate), rate, random_add_rate

    @classmethod
    async def withdraw(cls, user_id: str, amount: int) -> MahiroBank:
        """取款

        参数:
            user_id: 用户id
            amount: 取款数量

        返回:
            MahiroBank
        """
        await UserConsole.add_gold(user_id, amount, "bank")
        return await MahiroBank.withdraw(user_id, amount)

    @classmethod
    async def loan(cls, user_id: str, amount: int) -> tuple[MahiroBank, float | None]:
        """贷款

        参数:
            user_id: 用户id
            amount: 贷款数量

        返回:
            tuple[MahiroBank, float]: MahiroBank，贷款利率
        """
        rate_range = base_config.get("rate_range")
        rate = random.uniform(rate_range[0], rate_range[1])
        sign_user = await SignUser.get_user(user_id)
        user, _ = await MahiroBank.get_or_create(user_id=user_id)
        if user.loan_amount + amount > sign_user.impression * 150:
            raise ValueError("贷款数量超过最大限制，请签到提升好感度获取更多额度吧...")
        random_reduce_rate = await cls.random_event(float(sign_user.impression))
        if random_reduce_rate:
            rate -= random_reduce_rate
        await UserConsole.add_gold(user_id, amount, "bank")
        return await MahiroBank.loan(user_id, amount, rate), random_reduce_rate

    @classmethod
    async def repayment(cls, user_id: str, amount: int) -> MahiroBank:
        """还款

        参数:
            user_id: 用户id
            amount: 还款数量

        返回:
            MahiroBank
        """
        await UserConsole.reduce_gold(user_id, amount, GoldHandle.PLUGIN, "bank")
        return await MahiroBank.repayment(user_id, amount)

    @classmethod
    async def settlement(cls):
        """结算每日利率"""
        bank_user_list = await MahiroBank.filter(amount__gt=0).all()
        log_list = await MahiroBankLog.filter(
            is_completed=False, handle_type=BankHandleType.DEPOSIT
        ).all()
        user_list = await UserConsole.filter(
            user_id__in=[user.user_id for user in bank_user_list]
        ).all()
        user_data = {user.user_id: user for user in user_list}
        bank_data: dict[str, list[MahiroBankLog]] = {}
        for log in log_list:
            if log.user_id not in bank_data:
                bank_data[log.user_id] = []
            bank_data[log.user_id].append(log)
        log_create_list = []
        log_update_list = []
        # 计算每日默认金币
        for bank_user in bank_user_list:
            if user := user_data.get(bank_user.user_id):
                amount = bank_user.amount
                if logs := bank_data.get(bank_user.user_id):
                    amount -= sum(log.amount for log in logs)
                if not amount:
                    continue
                # 计算每日默认金币
                gold = int(amount * bank_user.rate)
                user.gold += gold
                log_create_list.append(
                    MahiroBankLog(
                        user_id=bank_user.user_id,
                        amount=gold,
                        rate=bank_user.rate,
                        handle_type=BankHandleType.INTEREST,
                        is_completed=True,
                    )
                )
        # 计算每日存款金币
        for user_id, logs in bank_data.items():
            if user := user_data.get(user_id):
                for log in logs:
                    gold = int(log.amount * log.rate * log.effective_hour) or 1
                    user.gold += gold
                    log.is_completed = True
                    log_update_list.append(log)
                    log_create_list.append(
                        MahiroBankLog(
                            user_id=user_id,
                            amount=gold,
                            rate=log.rate,
                            handle_type=BankHandleType.INTEREST,
                            is_completed=True,
                        )
                    )
        if log_create_list:
            await MahiroBankLog.bulk_create(log_create_list, 10)
        if log_update_list:
            await MahiroBankLog.bulk_update(log_update_list, ["is_completed"], 10)
        await UserConsole.bulk_update(user_list, ["gold"], 10)
