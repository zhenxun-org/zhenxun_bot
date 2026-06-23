import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlparse

import aiofiles
import nonebot
from nonebot.utils import is_coroutine_callable
from tortoise import Tortoise
from tortoise.connection import connections
from tortoise.exceptions import ConfigurationError, OperationalError

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from . import watchdog as _watchdog  # noqa: F401
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
from .schema_guard import repair_safe_schema_drift
from .schema_ops import SchemaOpRisk, normalize_schema_ops
from .utils import with_db_timeout

Dialect = Literal["sqlite", "postgres", "mysql", "unknown"]

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

_SCRIPT_HASH_DIR = Path() / "data" / ".db_script_hashes"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _connection_dialect() -> Dialect:
    try:
        connection = Tortoise.get_connection("default")
        capabilities = getattr(connection, "capabilities", None)
        raw = str(getattr(capabilities, "dialect", "") or "").lower()
        if raw.startswith("sqlite"):
            return "sqlite"
        if raw.startswith("postgres"):
            return "postgres"
        if raw.startswith("mysql"):
            return "mysql"
    except Exception:
        pass
    return "unknown"


def _allow_guarded_schema_ops() -> bool:
    """Whether startup may run guarded SchemaOp migrations.

    Safe SchemaOps are limited to non-destructive changes such as adding nullable
    columns and non-unique indexes. Guarded operations may rename, drop, or alter
    columns, so keep them opt-in to avoid damaging existing databases during
    normal startup.
    """
    return os.getenv("DB_SCHEMA_RUN_GUARDED_OPS", "").strip().lower() in _TRUE_VALUES


def _extract_alter_table_name(sql: str) -> str | None:
    match = re.match(r"ALTER\s+TABLE\s+[`\"]?(\w+)[`\"]?", sql, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_create_index_table_name(sql: str) -> str | None:
    match = re.search(r"\bON\s+[`\"]?(\w+)[`\"]?\s*\(", sql, re.IGNORECASE)
    return match.group(1) if match else None


def _db_script_hash_file(script_fingerprint: str) -> Path:
    parsed = urlparse(BotConfig.db_url or "")
    dialect = parsed.scheme or "unknown"
    if dialect == "sqlite":
        db_identity = str(Path(parsed.path).resolve())
    else:
        db_identity = f"{parsed.hostname or ''}:{parsed.port or ''}{parsed.path}"
    db_hash = hashlib.md5(
        json.dumps(
            {
                "dialect": dialect,
                "db": db_identity,
                "script": script_fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return _SCRIPT_HASH_DIR / f"{db_hash}.json"


def get_config() -> dict:
    """获取数据库配置"""
    if not BotConfig.db_url:
        raise DbUrlIsNode("数据库Url连接字符串为空，请检查配置文件（.env.dev）")
    parsed = urlparse(BotConfig.db_url)

    config = {
        "connections": {"default": BotConfig.db_url},
        "apps": {
            "models": {
                "models": db_model.models,
                "default_connection": "default",
            }
        },
        "timezone": "Asia/Shanghai",
    }

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
        Path(parsed.path).parent.mkdir(parents=True, exist_ok=True)
        config["connections"]["default"] = {
            "engine": "tortoise.backends.sqlite",
            "credentials": {
                "file_path": parsed.path,
            },
            **SQLITE_CONFIG,
        }
    return config


@PriorityLifecycle.on_startup(priority=1)
async def init():
    global MODELS, SCRIPT_METHOD

    env_example_file = Path() / ".env.example"
    env_dev_file = Path() / ".env.dev"
    if not env_dev_file.exists():
        async with aiofiles.open(env_example_file, encoding="utf-8") as f:
            env_text = await f.read()
        async with aiofiles.open(env_dev_file, "w", encoding="utf-8") as f:
            await f.write(env_text)
        logger.info("已生成 .env.dev 文件，请根据 .env.example 文件配置进行配置")

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
            logger.debug(
                "即将运行SCRIPT_METHOD方法, 合计 "
                f"<u><y>{len(db_model.script_method)}</y></u> 个..."
            )
            sql_list = []
            allow_guarded_ops = _allow_guarded_schema_ops()
            for module, func in db_model.script_method:
                try:
                    items = await func() if is_coroutine_callable(func) else func()
                    if not items:
                        continue
                    for item in items:
                        if not isinstance(item, str):
                            if item.risk == SchemaOpRisk.MANUAL:
                                logger.debug(f"{module} 跳过手动迁移动作: {item}")
                                continue
                            if (
                                item.risk == SchemaOpRisk.GUARDED
                                and not allow_guarded_ops
                            ):
                                logger.debug(f"{module} 跳过受保护迁移动作: {item}")
                                continue
                            if item.risk != SchemaOpRisk.SAFE and not allow_guarded_ops:
                                logger.debug(f"{module} 跳过未知风险迁移动作: {item}")
                                continue
                        sql_list += normalize_schema_ops([item], _connection_dialect())
                except Exception as e:
                    logger.debug(f"{module} 执行SCRIPT_METHOD方法出错...", e=e)
            if sql_list:
                fingerprint = hashlib.md5(
                    json.dumps(sorted(sql_list), ensure_ascii=False).encode()
                ).hexdigest()
                script_hash_file = _db_script_hash_file(fingerprint)
                need_run = not (
                    script_hash_file.exists()
                    and json.loads(script_hash_file.read_text(encoding="utf-8")).get(
                        "script_fingerprint"
                    )
                    == fingerprint
                )
                if need_run:
                    db = Tortoise.get_connection("default")

                    async def table_exists(table_name: str) -> bool:
                        """检查表是否存在"""
                        try:
                            # PostgreSQL
                            result = await db.execute_query_dict(
                                "SELECT to_regclass($1) IS NOT NULL as exists",
                                [table_name],
                            )
                            if result:
                                return result[0]["exists"]
                        except Exception:
                            pass
                        try:
                            # MySQL
                            result = await db.execute_query_dict(
                                "SELECT COUNT(*) as count FROM information_schema.tables "  # noqa: E501
                                "WHERE table_name = %s",
                                [table_name],
                            )
                            if result:
                                return result[0]["count"] > 0
                        except Exception:
                            pass
                        try:
                            # SQLite
                            result = await db.execute_query_dict(
                                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",  # noqa: E501
                                [table_name],
                            )
                            return len(result) > 0
                        except Exception:
                            pass
                        return True  # 如果检查失败，假设表存在，让SQL自己报错

                    for sql in sql_list:
                        # 对于 ALTER TABLE 操作，先检查表是否存在
                        sql_upper = sql.strip().upper()
                        if sql_upper.startswith("ALTER TABLE"):
                            table_name = _extract_alter_table_name(sql)
                            if table_name:
                                if not await table_exists(table_name):
                                    logger.debug(f"跳过SQL（表不存在）: {sql}")
                                    continue
                        elif sql_upper.startswith("CREATE INDEX"):
                            table_name = _extract_create_index_table_name(sql)
                            if table_name:
                                if not await table_exists(table_name):
                                    logger.debug(f"跳过SQL（表不存在）: {sql}")
                                    continue

                        logger.debug(f"执行SQL: {sql}")
                        try:
                            await asyncio.wait_for(
                                db.execute_query_dict(sql),
                                timeout=DB_TIMEOUT_SECONDS,
                            )
                        except OperationalError as e:
                            err_str = str(e).lower()
                            sql_lower = sql.lower()
                            if any(
                                x in err_str
                                for x in [
                                    "already exists",
                                    "duplicate column",
                                    "已经存在",
                                    "已存在",
                                ]
                            ):
                                pass
                            elif any(
                                x in err_str
                                for x in [
                                    "does not exist",
                                    "check that",
                                    "不存在",
                                    "no such column",
                                ]
                            ) and ("drop" in sql_lower or "rename" in sql_lower):
                                pass
                            elif "syntax error" in err_str and (
                                "alter column" in sql_lower
                                or "drop not null" in sql_lower
                            ):
                                # SQLite 不支持 PostgreSQL 的 ALTER COLUMN 语法
                                pass
                            else:
                                logger.warning(f"执行SQL警告: {sql} || {e}")
                        except Exception as e:
                            logger.debug(f"执行SQL: {sql} 错误...", e=e)
                    logger.debug("SCRIPT_METHOD方法执行完毕!")
                    script_hash_file.parent.mkdir(parents=True, exist_ok=True)
                    script_hash_file.write_text(
                        json.dumps(
                            {
                                "dialect": urlparse(BotConfig.db_url or "").scheme,
                                "db_url_hash": hashlib.md5(
                                    (BotConfig.db_url or "").encode()
                                ).hexdigest(),
                                "script_fingerprint": fingerprint,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                else:
                    logger.debug("迁移脚本无变化，跳过执行")
        # Tortoise may emit column comments/index SQL during generate_schemas().
        # On existing databases with newly added nullable fields, PostgreSQL can
        # fail before the post-generate SchemaGuard gets a chance to repair drift.
        await repair_safe_schema_drift()
        logger.debug("开始生成数据库表结构...")
        await Tortoise.generate_schemas()
        logger.debug("数据库表结构生成完毕!")
        await repair_safe_schema_drift()
        logger.info("Database loaded successfully!")
    except Exception as e:
        raise DbConnectError(f"数据库连接错误... e:{e}") from e


@PriorityLifecycle.on_shutdown(priority=100)
async def disconnect():
    try:
        await connections.close_all()
    except ConfigurationError:
        logger.debug("数据库连接未初始化，跳过关闭")
    except Exception as e:
        logger.error(f"关闭数据库连接时发生意外错误: {e}")
