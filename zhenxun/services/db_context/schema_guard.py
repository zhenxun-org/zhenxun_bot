from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Literal

from tortoise import Tortoise
from tortoise.exceptions import OperationalError

from zhenxun.services.log import logger

from .config import DB_TIMEOUT_SECONDS, LOG_COMMAND

Dialect = Literal["sqlite", "postgres", "mysql", "unknown"]


@dataclass(slots=True)
class SchemaGuardResult:
    checked_tables: int = 0
    repaired_columns: int = 0
    repaired_indexes: int = 0
    skipped_columns: int = 0
    skipped_indexes: int = 0
    type_mismatches: int = 0
    warnings: int = 0
    drift: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool | None = None
    default: str | None = None


def _quote_identifier(identifier: str, dialect: Dialect) -> str:
    escaped = identifier.replace('"', '""')
    if dialect == "mysql":
        return f"`{identifier.replace('`', '``')}`"
    return f'"{escaped}"'


def _connection_dialect(connection: Any) -> Dialect:
    capabilities = getattr(connection, "capabilities", None)
    raw = str(getattr(capabilities, "dialect", "") or "").lower()
    if raw.startswith("sqlite"):
        return "sqlite"
    if raw.startswith("postgres"):
        return "postgres"
    if raw.startswith("mysql"):
        return "mysql"
    return "unknown"


def _is_safe_missing_field(field: Any) -> bool:
    if getattr(field, "pk", False):
        return False
    if getattr(field, "generated", False):
        return False
    if bool(getattr(field, "null", False)):
        return True
    default = getattr(field, "default", None)
    return default is not None


def _field_sql_type(field: Any, dialect: Dialect) -> str | None:
    if dialect != "unknown" and hasattr(field, "get_for_dialect"):
        with contextlib.suppress(Exception):
            value = field.get_for_dialect(dialect, "SQL_TYPE")
            if value:
                return str(value)
    value = getattr(field, "SQL_TYPE", None)
    return str(value) if value else None


def _is_db_field(field: Any, dialect: Dialect) -> bool:
    if getattr(field, "virtual", False):
        return False
    return _field_sql_type(field, dialect) is not None


def _field_default_sql(field: Any, dialect: Dialect) -> str:
    default = getattr(field, "default", None)
    if default is None or callable(default):
        return ""
    if isinstance(default, bool):
        if dialect == "postgres":
            return " DEFAULT TRUE" if default else " DEFAULT FALSE"
        return " DEFAULT 1" if default else " DEFAULT 0"
    if isinstance(default, int | float):
        return f" DEFAULT {default}"
    if isinstance(default, str):
        escaped = default.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def _missing_column_sql(
    table: str,
    column: str,
    field: Any,
    dialect: Dialect,
) -> str | None:
    sql_type = _field_sql_type(field, dialect)
    if not sql_type:
        return None
    table_sql = _quote_identifier(table, dialect)
    column_sql = _quote_identifier(column, dialect)
    null_sql = "" if bool(getattr(field, "null", False)) else " NOT NULL"
    default_sql = _field_default_sql(field, dialect)
    if not bool(getattr(field, "null", False)) and not default_sql:
        return None
    if dialect == "postgres":
        return (
            f"ALTER TABLE {table_sql} ADD COLUMN IF NOT EXISTS "
            f"{column_sql} {sql_type}{default_sql}{null_sql}"
        )
    return (
        f"ALTER TABLE {table_sql} ADD COLUMN "
        f"{column_sql} {sql_type}{default_sql}{null_sql}"
    )


async def _table_columns(
    connection: Any, table: str, dialect: Dialect
) -> dict[str, ColumnInfo] | None:
    if dialect == "sqlite":
        rows = await connection.execute_query_dict(
            f"PRAGMA table_xinfo({_quote_identifier(table, dialect)})"
        )
        if not rows:
            return None
        return {
            str(row.get("name")): ColumnInfo(
                name=str(row.get("name")),
                data_type=str(row.get("type") or ""),
                nullable=not bool(row.get("notnull")),
                default=str(row.get("dflt_value"))
                if row.get("dflt_value") is not None
                else None,
            )
            for row in rows
            if row.get("name")
        }
    if dialect == "postgres":
        rows = await connection.execute_query_dict(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = $1",
            [table],
        )
        if not rows:
            return None
        return {
            str(row.get("column_name")): ColumnInfo(
                name=str(row.get("column_name")),
                data_type=str(row.get("data_type") or ""),
                nullable=str(row.get("is_nullable") or "").upper() == "YES",
                default=str(row.get("column_default"))
                if row.get("column_default") is not None
                else None,
            )
            for row in rows
            if row.get("column_name")
        }
    if dialect == "mysql":
        rows = await connection.execute_query_dict(
            "SELECT COLUMN_NAME AS column_name, COLUMN_TYPE AS column_type, "
            "IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [table],
        )
        if not rows:
            return None
        return {
            str(row.get("column_name")): ColumnInfo(
                name=str(row.get("column_name")),
                data_type=str(row.get("column_type") or ""),
                nullable=str(row.get("is_nullable") or "").upper() == "YES",
                default=str(row.get("column_default"))
                if row.get("column_default") is not None
                else None,
            )
            for row in rows
            if row.get("column_name")
        }
    return None


def _normalize_type(value: str, dialect: Dialect) -> str:
    text = value.lower().strip()
    if not text:
        return ""
    if dialect == "sqlite":
        if "int" in text:
            return "integer"
        if any(part in text for part in ("char", "clob", "text", "varchar")):
            return "text"
        if any(part in text for part in ("real", "floa", "doub")):
            return "real"
        if "blob" in text:
            return "blob"
        if "bool" in text:
            return "integer"
        if "timestamp" in text or "datetime" in text:
            return "text"
        return text
    if dialect == "postgres":
        text = text.replace("character varying", "varchar")
        if text.startswith("timestamp"):
            return "timestamp"
        if text in {"boolean", "bool"}:
            return "boolean"
        return text
    if dialect == "mysql":
        if text.startswith("tinyint(1)") or text == "bool" or text == "boolean":
            return "boolean"
        if text.startswith("datetime") or text.startswith("timestamp"):
            return "datetime"
        return text
    return text


def _field_type_compatible(field: Any, column: ColumnInfo, dialect: Dialect) -> bool:
    expected = _field_sql_type(field, dialect)
    if not expected:
        return True
    expected_norm = _normalize_type(expected, dialect)
    actual_norm = _normalize_type(column.data_type, dialect)
    if not expected_norm or not actual_norm:
        return True
    if expected_norm == actual_norm:
        return True
    if dialect == "sqlite":
        # SQLite affinity is intentionally loose; varchar/text and bool/integer are
        # compatible enough for startup validation.
        compatible = {
            ("varchar", "text"),
            ("text", "varchar"),
            ("boolean", "integer"),
            ("integer", "boolean"),
        }
        return (expected_norm, actual_norm) in compatible
    return False


def _index_name(table: str, columns: tuple[str, ...]) -> str:
    raw = f"idx_{table}_{'_'.join(columns)}"
    return raw[:62]


async def _table_indexes(
    connection: Any, table: str, dialect: Dialect
) -> set[tuple[str, ...]]:
    indexes: set[tuple[str, ...]] = set()
    if dialect == "sqlite":
        rows = await connection.execute_query_dict(
            f"PRAGMA index_list({_quote_identifier(table, dialect)})"
        )
        for row in rows:
            if bool(row.get("unique")):
                continue
            index_name = row.get("name")
            if not index_name:
                continue
            info = await connection.execute_query_dict(
                f"PRAGMA index_info({_quote_identifier(str(index_name), dialect)})"
            )
            columns = tuple(
                str(item.get("name"))
                for item in sorted(info, key=lambda item: int(item.get("seqno") or 0))
                if item.get("name")
            )
            if columns:
                indexes.add(columns)
        return indexes
    if dialect == "postgres":
        rows = await connection.execute_query_dict(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = $1",
            [table],
        )
        for row in rows:
            indexdef = str(row.get("indexdef") or "")
            if " UNIQUE INDEX " in indexdef.upper():
                continue
            start = indexdef.rfind("(")
            end = indexdef.rfind(")")
            if start < 0 or end <= start:
                continue
            columns = tuple(
                part.strip().strip('"')
                for part in indexdef[start + 1 : end].split(",")
                if part.strip()
            )
            if columns:
                indexes.add(columns)
        return indexes
    if dialect == "mysql":
        rows = await connection.execute_query_dict(
            "SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE "
            "FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            [table],
        )
        grouped: dict[str, list[tuple[int, str]]] = {}
        unique_names: set[str] = set()
        for row in rows:
            name = str(row.get("INDEX_NAME") or "")
            column = str(row.get("COLUMN_NAME") or "")
            if not name or not column:
                continue
            if int(row.get("NON_UNIQUE") or 0) == 0:
                unique_names.add(name)
                continue
            grouped.setdefault(name, []).append(
                (int(row.get("SEQ_IN_INDEX") or 0), column)
            )
        for name, items in grouped.items():
            if name in unique_names:
                continue
            columns = tuple(column for _, column in sorted(items))
            if columns:
                indexes.add(columns)
        return indexes
    return indexes


def _index_sql(table: str, columns: tuple[str, ...], dialect: Dialect) -> str | None:
    if not columns:
        return None
    table_sql = _quote_identifier(table, dialect)
    index_sql = _quote_identifier(_index_name(table, columns), dialect)
    columns_sql = ", ".join(_quote_identifier(column, dialect) for column in columns)
    if dialect in {"sqlite", "postgres"}:
        return (
            f"CREATE INDEX IF NOT EXISTS {index_sql} " f"ON {table_sql}({columns_sql})"
        )
    if dialect == "mysql":
        return f"CREATE INDEX {index_sql} ON {table_sql}({columns_sql})"
    return None


def _meta_index_columns(index: Any) -> tuple[str, ...]:
    if isinstance(index, str):
        return (index,)
    if isinstance(index, list | tuple):
        return tuple(str(column) for column in index if column)
    fields = getattr(index, "fields", None)
    if fields:
        return tuple(str(column) for column in fields if column)
    return ()


def _empty_drift(table: str) -> dict[str, Any]:
    return {
        "table": table,
        "missing_columns": [],
        "extra_columns": [],
        "type_mismatches": [],
        "missing_indexes": [],
        "unsafe_changes": [],
    }


async def _write_schema_report(result: SchemaGuardResult) -> None:
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "checked_tables": result.checked_tables,
            "repaired_columns": result.repaired_columns,
            "repaired_indexes": result.repaired_indexes,
            "skipped_columns": result.skipped_columns,
            "skipped_indexes": result.skipped_indexes,
            "type_mismatches": result.type_mismatches,
            "warnings": result.warnings,
        },
        "tables": result.drift or [],
    }
    path = Path() / "data" / "db" / "schema_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(report, ensure_ascii=False, indent=2),
        "utf-8",
    )


async def repair_safe_schema_drift() -> SchemaGuardResult:
    """Repair low-risk missing columns after Tortoise schema creation.

    This guard intentionally avoids type changes, constraints, unique indexes, foreign
    keys, and SQLite table rebuilds. It is a startup-only safety net for model/schema
    drift caused by copied databases or skipped legacy script hashes.
    """
    result = SchemaGuardResult()
    result.drift = []
    connection = Tortoise.get_connection("default")
    dialect = _connection_dialect(connection)
    if dialect == "unknown":
        logger.debug("SchemaGuard 跳过未知数据库方言", LOG_COMMAND)
        return result

    app = Tortoise.apps.get("models", {})
    for model in app.values():
        meta = getattr(model, "_meta", None)
        table = getattr(meta, "db_table", None) or getattr(meta, "table", None)
        if not table:
            continue
        try:
            columns = await _table_columns(connection, table, dialect)
        except Exception as exc:
            result.warnings += 1
            logger.debug(f"SchemaGuard 检查表 {table} 失败", LOG_COMMAND, e=exc)
            continue
        if columns is None:
            continue
        result.checked_tables += 1
        drift = _empty_drift(table)
        fields_map = getattr(meta, "fields_map", {}) or {}
        expected_sources: set[str] = set()
        for field_name, field in fields_map.items():
            if not _is_db_field(field, dialect):
                continue
            source = str(getattr(field, "source_field", None) or field_name)
            expected_sources.add(source)
            if source in columns:
                if not _field_type_compatible(field, columns[source], dialect):
                    result.type_mismatches += 1
                    drift["type_mismatches"].append(
                        {
                            "column": source,
                            "expected": _field_sql_type(field, dialect),
                            "actual": columns[source].data_type,
                        }
                    )
                continue
            drift["missing_columns"].append(source)
            if not _is_safe_missing_field(field):
                result.skipped_columns += 1
                drift["unsafe_changes"].append(
                    {
                        "kind": "missing_required_column",
                        "column": source,
                    }
                )
                logger.debug(
                    f"SchemaGuard 跳过非低风险缺字段: {table}.{source}",
                    LOG_COMMAND,
                )
                continue
            sql = _missing_column_sql(table, source, field, dialect)
            if not sql:
                result.skipped_columns += 1
                logger.debug(
                    f"SchemaGuard 无法生成补字段 SQL: {table}.{source}",
                    LOG_COMMAND,
                )
                continue
            try:
                await asyncio.wait_for(
                    connection.execute_query_dict(sql),
                    timeout=DB_TIMEOUT_SECONDS,
                )
                columns[source] = ColumnInfo(name=source, data_type="")
                result.repaired_columns += 1
                logger.info(f"SchemaGuard 已补齐字段: {table}.{source}", LOG_COMMAND)
            except OperationalError as exc:
                err = str(exc).lower()
                if any(
                    text in err
                    for text in ("duplicate column", "already exists", "已存在")
                ):
                    columns[source] = ColumnInfo(name=source, data_type="")
                    continue
                result.warnings += 1
                logger.warning(
                    f"SchemaGuard 补齐字段失败: {table}.{source}",
                    LOG_COMMAND,
                    e=exc,
                )
            except Exception as exc:
                result.warnings += 1
                logger.warning(
                    f"SchemaGuard 补齐字段失败: {table}.{source}",
                    LOG_COMMAND,
                    e=exc,
                )
        drift["extra_columns"] = sorted(
            column for column in columns.keys() if column not in expected_sources
        )
        try:
            existing_indexes = await _table_indexes(connection, table, dialect)
        except Exception as exc:
            result.warnings += 1
            logger.debug(f"SchemaGuard 检查索引 {table} 失败", LOG_COMMAND, e=exc)
            existing_indexes = set()
        indexes = getattr(meta, "indexes", ()) or ()
        for index in indexes:
            index_columns = _meta_index_columns(index)
            if not index_columns or index_columns in existing_indexes:
                continue
            drift["missing_indexes"].append(list(index_columns))
            if not all(column in columns for column in index_columns):
                result.skipped_indexes += 1
                logger.debug(
                    f"SchemaGuard 跳过缺字段索引: {table}.{index_columns}",
                    LOG_COMMAND,
                )
                continue
            sql = _index_sql(table, index_columns, dialect)
            if not sql:
                result.skipped_indexes += 1
                continue
            try:
                await asyncio.wait_for(
                    connection.execute_query_dict(sql),
                    timeout=DB_TIMEOUT_SECONDS,
                )
                existing_indexes.add(index_columns)
                result.repaired_indexes += 1
                logger.debug(
                    f"SchemaGuard 已补齐索引: {table}.{index_columns}",
                    LOG_COMMAND,
                )
            except OperationalError as exc:
                err = str(exc).lower()
                if any(
                    text in err for text in ("already exists", "duplicate", "已存在")
                ):
                    existing_indexes.add(index_columns)
                    continue
                result.warnings += 1
                logger.warning(
                    f"SchemaGuard 补齐索引失败: {table}.{index_columns}",
                    LOG_COMMAND,
                    e=exc,
                )
        if any(
            drift[key]
            for key in (
                "missing_columns",
                "extra_columns",
                "type_mismatches",
                "missing_indexes",
                "unsafe_changes",
            )
        ):
            result.drift.append(drift)

    logger.info(
        "SchemaGuard 完成: "
        f"checked={result.checked_tables}, "
        f"repaired_columns={result.repaired_columns}, "
        f"repaired_indexes={result.repaired_indexes}, "
        f"skipped_columns={result.skipped_columns}, "
        f"skipped_indexes={result.skipped_indexes}, "
        f"type_mismatches={result.type_mismatches}, "
        f"warnings={result.warnings}",
        LOG_COMMAND,
    )
    with contextlib.suppress(Exception):
        await _write_schema_report(result)
    return result


async def repair_table_schema(table_name: str) -> SchemaGuardResult:
    """Repair one table by reusing the startup guard path.

    The current guard is cheap enough and already table-scoped internally by model
    metadata, so this helper keeps write-path recovery simple and conservative.
    """
    result = SchemaGuardResult()
    result.drift = []
    connection = Tortoise.get_connection("default")
    dialect = _connection_dialect(connection)
    if dialect == "unknown":
        return result
    app = Tortoise.apps.get("models", {})
    for model in app.values():
        meta = getattr(model, "_meta", None)
        table = getattr(meta, "db_table", None) or getattr(meta, "table", None)
        if table == table_name:
            # Keep the implementation conservative: repairing all tables is still
            # startup-style work and avoids a second partial code path.
            return await repair_safe_schema_drift()
    return result
