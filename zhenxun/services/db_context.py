from collections.abc import Iterable
import re

import nonebot
from nonebot.utils import is_coroutine_callable
from tortoise import BaseDBAsyncClient, Tortoise
from tortoise.connection import connections
from tortoise.models import Model as Model_

from zhenxun.configs.config import BotConfig
from zhenxun.utils.exception import HookPriorityException
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from .log import logger

SCRIPT_METHOD = []
MODELS: list[str] = []


driver = nonebot.get_driver()


def unicode_escape(value: str) -> str:
    """
    将字符串转换为Unicode转义形式（仅处理未转义的特殊字符）
    已经转义过的字符串保持不变
    """
    if not value:
        return value

    if re.search(r"\\u[0-9a-fA-F]{4}", value):
        return value

    return "".join(
        char
        if 0x20 <= ord(char) <= 0x7E or char in ("\n", "\r", "\t")
        else f"\\u{ord(char):04x}"
        for char in value
    )


def unicode_unescape(value: str) -> str:
    """
    安全还原字符串中的Unicode转义序列
    如果不是有效转义序列，保留原样
    """
    if not value:
        return value

    # 仅处理有效的 \uXXXX 格式
    return re.sub(
        r"(?<!\\)\\u([0-9a-fA-F]{4})",  # 匹配未被转义的 \uXXXX
        lambda m: chr(int(m.group(1), 16)),
        value,
    )


class UnicodeSafeMixin(Model_):
    _unicode_safe_fields: list[str] = []  # noqa: RUF012
    """需要处理的字段名列表"""

    async def save(self, *args, **kwargs):
        for field_name in self._unicode_safe_fields:
            value = getattr(self, field_name)
            if isinstance(value, str):
                # 如果是新数据或数据未标记已处理
                if not getattr(self, f"_{field_name}_converted", False):
                    setattr(self, field_name, unicode_escape(value))
                    setattr(self, f"_{field_name}_converted", True)
        await super().save(*args, **kwargs)

    @classmethod
    def get(cls, *args, **kwargs):
        instance = super().get(*args, **kwargs)
        cls._process_unicode_fields(instance)
        return instance

    @classmethod
    def filter(cls, *args, **kwargs): # pyright: ignore[reportIncompatibleMethodOverride]
        for field in cls._unicode_safe_fields:
            if field in kwargs and isinstance(kwargs[field], str):
                kwargs[field] = unicode_escape(kwargs[field])
        return super().filter(*args, **kwargs)

    @classmethod
    def bulk_update(
        cls,
        objects: Iterable,
        fields: Iterable[str],
        batch_size: int | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ):
        safe_fields = [f for f in fields if f in cls._unicode_safe_fields]

        for obj in objects:
            for field in safe_fields:
                value = getattr(obj, field)
                if isinstance(value, str):
                    # 如果是新数据或数据未标记已处理
                    if not getattr(obj, f"_{field}_converted", False):
                        setattr(obj, field, unicode_escape(value))
                        setattr(obj, f"_{field}_converted", True)

        # 调用原始 bulk_update 方法
        return super().bulk_update(
            objects, fields, batch_size=batch_size, using_db=using_db
        )

    @classmethod
    def bulk_create(
        cls,
        objects: Iterable,
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        on_conflict: Iterable[str] | None = None,
        using_db: BaseDBAsyncClient | None = None,
    ):
        for obj in objects:
            for field_name in cls._unicode_safe_fields:
                value = getattr(obj, field_name)
                if isinstance(value, str):
                    # 如果是新数据或数据未标记已处理
                    if not getattr(obj, f"_{field_name}_converted", False):
                        setattr(obj, field_name, unicode_escape(value))
                        setattr(obj, f"_{field_name}_converted", True)

        # 调用原始 bulk_create 方法
        return super().bulk_create(
            objects,
            batch_size,
            ignore_conflicts,
            update_fields,
            on_conflict,
            using_db,
        )

    @classmethod
    def _process_unicode_fields(cls, instance):
        """处理实例的Unicode字段（兼容新旧数据）"""
        for field_name in cls._unicode_safe_fields:
            value = getattr(instance, field_name)
            if isinstance(value, str):
                # 如果字段包含有效转义序列才处理
                if re.search(r"(?<!\\)\\u[0-9a-fA-F]{4}", value):
                    setattr(instance, field_name, unicode_unescape(value))
                # 标记字段已处理
                setattr(instance, f"_{field_name}_converted", True)


class Model(UnicodeSafeMixin):
    """
    自动添加模块

    Args:
        UnicodeSafeMixin: Model
    """

    def __init_subclass__(cls, **kwargs):
        if cls.__module__ not in MODELS:
            MODELS.append(cls.__module__)

        if func := getattr(cls, "_run_script", None):
            SCRIPT_METHOD.append((cls.__module__, func))


class DbUrlIsNode(HookPriorityException):
    """
    数据库链接地址为空
    """

    pass


class DbConnectError(Exception):
    """
    数据库连接错误
    """

    pass


@PriorityLifecycle.on_startup(priority=1)
async def init():
    if not BotConfig.db_url:
        # raise DbUrlIsNode("数据库配置为空，请在.env.dev中配置DB_URL...")
        error = f"""
**********************************************************************
🌟 **************************** 配置为空 ************************* 🌟
🚀 请打开 WebUi 进行基础配置 🚀
🌐 配置地址：http://{driver.config.host}:{driver.config.port}/#/configure 🌐
***********************************************************************
***********************************************************************
        """
        raise DbUrlIsNode("\n" + error.strip())
    try:
        await Tortoise.init(
            db_url=BotConfig.db_url,
            modules={"models": MODELS},
            timezone="Asia/Shanghai",
        )
        if SCRIPT_METHOD:
            db = Tortoise.get_connection("default")
            logger.debug(
                "即将运行SCRIPT_METHOD方法, 合计 "
                f"<u><y>{len(SCRIPT_METHOD)}</y></u> 个..."
            )
            sql_list = []
            for module, func in SCRIPT_METHOD:
                try:
                    sql = await func() if is_coroutine_callable(func) else func()
                    if sql:
                        sql_list += sql
                except Exception as e:
                    logger.debug(f"{module} 执行SCRIPT_METHOD方法出错...", e=e)
            for sql in sql_list:
                logger.debug(f"执行SQL: {sql}")
                try:
                    await db.execute_query_dict(sql)
                    # await TestSQL.raw(sql)
                except Exception as e:
                    logger.debug(f"执行SQL: {sql} 错误...", e=e)
            if sql_list:
                logger.debug("SCRIPT_METHOD方法执行完毕!")
        await Tortoise.generate_schemas()
        logger.info("Database loaded successfully!")
    except Exception as e:
        raise DbConnectError(f"数据库连接错误... e:{e}") from e


async def disconnect():
    await connections.close_all()
