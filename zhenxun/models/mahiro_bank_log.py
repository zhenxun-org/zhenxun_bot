from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.utils.enum import BankHandleType


class MahiroBankLog(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255, description="用户id")
    """用户id"""
    amount = fields.BigIntField(default=0, description="存款")
    """金币数量"""
    rate = fields.FloatField(default=0, description="小时利率")
    """小时利率"""
    handle_type = fields.CharEnumField(
        BankHandleType, null=True, description="处理类型"
    )
    """处理类型"""
    is_completed = fields.BooleanField(default=False, description="是否完成")
    """是否完成"""
    effective_hour = fields.IntField(default=0, description="有效小时")
    """有效小时"""
    update_time = fields.DatetimeField(auto_now=True)
    """修改时间"""
    create_time = fields.DatetimeField(auto_now_add=True)
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "mahiro_bank_log"
        table_description = "小真寻银行日志"
