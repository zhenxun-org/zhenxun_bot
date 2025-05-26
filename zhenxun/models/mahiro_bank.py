from datetime import datetime
from typing_extensions import Self

from tortoise import fields

from zhenxun.services.db_context import Model

from .mahiro_bank_log import BankHandleType, MahiroBankLog


class MahiroBank(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255, description="用户id")
    """用户id"""
    amount = fields.BigIntField(default=0, description="存款")
    """用户存款"""
    rate = fields.FloatField(default=0.0005, description="小时利率")
    """小时利率"""
    loan_amount = fields.BigIntField(default=0, description="贷款")
    """用户贷款"""
    loan_rate = fields.FloatField(default=0.0005, description="贷款利率")
    """贷款利率"""
    update_time = fields.DatetimeField(auto_now=True)
    """修改时间"""
    create_time = fields.DatetimeField(auto_now_add=True)
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "mahiro_bank"
        table_description = "小真寻银行"

    @classmethod
    async def deposit(cls, user_id: str, amount: int, rate: float) -> Self:
        """存款

        参数:
            user_id: 用户id
            amount: 金币数量
            rate: 小时利率

        返回:
            Self: MahiroBank
        """
        effective_hour = int(24 - datetime.now().hour)
        user, _ = await cls.get_or_create(user_id=user_id)
        user.amount += amount
        await user.save(update_fields=["amount", "rate"])
        await MahiroBankLog.create(
            user_id=user_id,
            amount=amount,
            rate=rate,
            effective_hour=effective_hour,
            handle_type=BankHandleType.DEPOSIT,
        )
        return user

    @classmethod
    async def withdraw(cls, user_id: str, amount: int) -> Self:
        """取款

        参数:
            user_id: 用户id
            amount: 金币数量

        返回:
            Self: MahiroBank
        """
        if amount <= 0:
            raise ValueError("取款金额必须大于0")
        user, _ = await cls.get_or_create(user_id=user_id)
        if user.amount < amount:
            raise ValueError("取款金额不能大于存款金额")
        user.amount -= amount
        await user.save(update_fields=["amount"])
        await MahiroBankLog.create(
            user_id=user_id, amount=amount, handle_type=BankHandleType.WITHDRAW
        )
        return user

    @classmethod
    async def loan(cls, user_id: str, amount: int, rate: float) -> Self:
        """贷款

        参数:
            user_id: 用户id
            amount: 贷款金额
            rate: 贷款利率

        返回:
            Self: MahiroBank
        """
        user, _ = await cls.get_or_create(user_id=user_id)
        user.loan_amount += amount
        user.loan_rate = rate
        await user.save(update_fields=["loan_amount", "loan_rate"])
        await MahiroBankLog.create(
            user_id=user_id, amount=amount, rate=rate, handle_type=BankHandleType.LOAN
        )
        return user

    @classmethod
    async def repayment(cls, user_id: str, amount: int) -> Self:
        """还款

        参数:
            user_id: 用户id
            amount: 还款金额

        返回:
            Self: MahiroBank
        """
        if amount <= 0:
            raise ValueError("还款金额必须大于0")
        user, _ = await cls.get_or_create(user_id=user_id)
        if user.loan_amount < amount:
            raise ValueError("还款金额不能大于贷款金额")
        user.loan_amount -= amount
        await user.save(update_fields=["loan_amount"])
        await MahiroBankLog.create(
            user_id=user_id, amount=amount, handle_type=BankHandleType.REPAYMENT
        )
        return user
