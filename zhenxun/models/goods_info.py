from typing_extensions import Self
import uuid

from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.services.db_context.schema_ops import DropColumn


class GoodsInfo(Model):
    __tablename__ = "goods_info"

    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    uuid = fields.CharField(255, null=True)
    """uuid"""
    goods_name = fields.CharField(255, unique=True)
    """商品名称"""
    goods_price = fields.IntField()
    """价格"""
    goods_description = fields.TextField()
    """描述"""
    goods_discount = fields.FloatField(default=1)
    """折扣"""
    goods_limit_time = fields.BigIntField(default=0)
    """限时"""
    daily_limit = fields.IntField(default=0)
    """每日限购"""
    is_passive = fields.BooleanField(default=False)
    """是否为被动道具"""
    partition = fields.CharField(255, null=True)
    """分区名称"""
    icon = fields.TextField(null=True)
    """图标路径"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "goods_info"
        table_description = "商品数据表"

    @classmethod
    async def add_goods(
        cls,
        goods_name: str,
        goods_price: int,
        goods_description: str,
        goods_discount: float = 1,
        goods_limit_time: int = 0,
        daily_limit: int = 0,
        is_passive: bool = False,
        partition: str | None = None,
        icon: str | None = None,
    ) -> str:
        """添加商品

        参数:
            goods_name: 商品名称
            goods_price: 商品价格
            goods_description: 商品简介
            goods_discount: 商品折扣
            goods_limit_time: 商品限时
            daily_limit: 每日购买限制
            is_passive: 是否为被动道具
            partition: 分区名称
            icon: 图标
        """
        if not await cls.exists(goods_name=goods_name):
            uuid_ = uuid.uuid1()
            await cls.create(
                uuid=uuid_,
                goods_name=goods_name,
                goods_price=goods_price,
                goods_description=goods_description,
                goods_discount=goods_discount,
                goods_limit_time=goods_limit_time,
                daily_limit=daily_limit,
                is_passive=is_passive,
                partition=partition,
                icon=icon,
            )
            return str(uuid_)
        else:
            return (await cls.get(goods_name=goods_name)).uuid

    @classmethod
    async def delete_goods(cls, goods_name: str) -> bool:
        """删除商品

        参数:
            goods_name: 商品名称

        返回:
            bool: 是否删除成功
        """
        if goods := await cls.get_or_none(goods_name=goods_name):
            await goods.delete()
            return True
        return False

    @classmethod
    async def update_goods(
        cls,
        goods_name: str,
        goods_price: int | None = None,
        goods_description: str | None = None,
        goods_discount: float | None = None,
        goods_limit_time: int | None = None,
        daily_limit: int | None = None,
        is_passive: bool | None = None,
        icon: str | None = None,
    ):
        """更新商品信息

        参数:
            goods_name: 商品名称
            goods_price: 商品价格
            goods_description: 商品简介
            goods_discount: 商品折扣
            goods_limit_time: 商品限时时间
            daily_limit: 每日次数限制
            is_passive: 是否为被动
            icon: 图标
        """
        if goods := await cls.get_or_none(goods_name=goods_name):
            await cls.update_or_create(
                goods_name=goods_name,
                defaults={
                    "goods_price": goods_price or goods.goods_price,
                    "goods_description": goods_description or goods.goods_description,
                    "goods_discount": goods_discount or goods.goods_discount,
                    "goods_limit_time": (
                        goods_limit_time
                        if goods_limit_time is not None
                        else goods.goods_limit_time
                    ),
                    "daily_limit": (
                        daily_limit if daily_limit is not None else goods.daily_limit
                    ),
                    "is_passive": (
                        is_passive if is_passive is not None else goods.is_passive
                    ),
                    "icon": icon or goods.icon,
                },
            )

    @classmethod
    async def get_all_goods(cls) -> list[Self]:
        """
        获得全部有序商品对象
        """
        query = await cls.all()
        id_lst = [x.id for x in query]
        goods_lst = []
        for _ in range(len(query)):
            min_id = min(id_lst)
            goods_lst.append(next(x for x in query if x.id == min_id))
            id_lst.remove(min_id)
        return goods_lst

    @classmethod
    async def _run_script(cls):
        return [
            # 删除 daily_purchase_limit 字段
            DropColumn("goods_info", "daily_purchase_limit"),
        ]
