import asyncio
from urllib.parse import urlparse

import nonebot
from nonebot.utils import is_coroutine_callable
from tortoise import Tortoise
from tortoise.connection import connections

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from .base_model import Model
from .config import (
    DB_TIMEOUT_SECONDS,
    MYSQL_CONFIG,
    POSTGRESQL_CONFIG,
    SLOW_QUERY_THRESHOLD,
    SQLITE_CONFIG,
    db_model,
    prompt,
)
from .exceptions import DbConnectError, DbUrlIsNode
from .utils import with_db_timeout

MODELS = db_model.models
SCRIPT_METHOD = db_model.script_method

__all__ = [
    "DB_TIMEOUT_SECONDS",
    "MODELS",
    "SCRIPT_METHOD",
    "SLOW_QUERY_THRESHOLD",
    "DbConnectError",
    "DbUrlIsNode",
    "Model",
    "disconnect",
    "init",
    "with_db_timeout",
]

driver = nonebot.get_driver()


def get_config() -> dict:
    """获取数据库配置"""
    parsed = urlparse(BotConfig.db_url)

    # 基础配置
    config = {
        "connections": {
            "default": BotConfig.db_url  # 默认直接使用连接字符串
        },
        "apps": {
            "models": {
                "models": db_model.models,
                "default_connection": "default",
            }
        },
        "timezone": "Asia/Shanghai",
    }

    # 根据数据库类型应用高级配置
    if parsed.scheme.startswith("postgres"):
        config["connections"]["default"] = {
            "engine": "tortoise.backends.asyncpg",
            "credentials": {
                "host": parsed.hostname,
                "port": parsed.port or 5432,
                "user": parsed.username,
                "password": parsed.password,
                "database": parsed.path[1:],
            },
            **POSTGRESQL_CONFIG,
        }
    elif parsed.scheme == "mysql":
        config["connections"]["default"] = {
            "engine": "tortoise.backends.mysql",
            "credentials": {
                "host": parsed.hostname,
                "port": parsed.port or 3306,
                "user": parsed.username,
                "password": parsed.password,
                "database": parsed.path[1:],
            },
            **MYSQL_CONFIG,
        }
    elif parsed.scheme == "sqlite":
        config["connections"]["default"] = {
            "engine": "tortoise.backends.sqlite",
            "credentials": {
                "file_path": parsed.path or ":memory:",
            },
            **SQLITE_CONFIG,
        }
    return config


@PriorityLifecycle.on_startup(priority=1)
async def init():
    global MODELS, SCRIPT_METHOD

    MODELS = db_model.models
    SCRIPT_METHOD = db_model.script_method
    if not BotConfig.db_url:
        error = prompt.format(host=driver.config.host, port=driver.config.port)
        raise DbUrlIsNode("\n" + error.strip())
    try:
        await Tortoise.init(
            config=get_config(),
        )
        if db_model.script_method:
            db = Tortoise.get_connection("default")
            logger.debug(
                "即将运行SCRIPT_METHOD方法, 合计 "
                f"<u><y>{len(db_model.script_method)}</y></u> 个..."
            )
            sql_list = []
            for module, func in db_model.script_method:
                try:
                    sql = await func() if is_coroutine_callable(func) else func()
                    if sql:
                        sql_list += sql
                except Exception as e:
                    logger.debug(f"{module} 执行SCRIPT_METHOD方法出错...", e=e)
            for sql in sql_list:
                logger.debug(f"执行SQL: {sql}")
                try:
                    await asyncio.wait_for(
                        db.execute_query_dict(sql), timeout=DB_TIMEOUT_SECONDS
                    )
                    # await TestSQL.raw(sql)
                except Exception as e:
                    logger.debug(f"执行SQL: {sql} 错误...", e=e)
            if sql_list:
                logger.debug("SCRIPT_METHOD方法执行完毕!")
        logger.debug("开始生成数据库表结构...")
        await Tortoise.generate_schemas()
        logger.debug("数据库表结构生成完毕!")
        logger.info("Database loaded successfully!")
    except Exception as e:
        raise DbConnectError(f"数据库连接错误... e:{e}") from e


async def disconnect():
    await connections.close_all()
