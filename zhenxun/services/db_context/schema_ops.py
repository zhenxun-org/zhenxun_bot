from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

Dialect = Literal["sqlite", "postgres", "mysql", "unknown"]


class SchemaOpRisk(str, Enum):
    SAFE = "safe"
    GUARDED = "guarded"
    MANUAL = "manual"


class SchemaOp(Protocol):
    risk: SchemaOpRisk

    def to_sql(self, dialect: Dialect) -> list[str]: ...


def quote_identifier(identifier: str, dialect: Dialect) -> str:
    if dialect == "mysql":
        return f"`{identifier.replace('`', '``')}`"
    return f'"{identifier.replace(chr(34), chr(34) + chr(34))}"'


def _column_type(column_type: str | dict[str, str], dialect: Dialect) -> str:
    if isinstance(column_type, dict):
        return column_type.get(dialect) or column_type.get("default") or "TEXT"
    return column_type


def _default_sql(default: str | float | bool | None, dialect: Dialect) -> str:
    if default is None:
        return ""
    if isinstance(default, bool):
        if dialect == "postgres":
            return " DEFAULT TRUE" if default else " DEFAULT FALSE"
        return " DEFAULT 1" if default else " DEFAULT 0"
    if isinstance(default, int | float):
        return f" DEFAULT {default}"
    escaped = default.replace("'", "''")
    return f" DEFAULT '{escaped}'"


@dataclass(frozen=True, slots=True)
class AddColumn:
    table: str
    column: str
    column_type: str | dict[str, str]
    nullable: bool = True
    default: str | float | bool | None = None
    risk: SchemaOpRisk = SchemaOpRisk.SAFE

    def to_sql(self, dialect: Dialect) -> list[str]:
        table = quote_identifier(self.table, dialect)
        column = quote_identifier(self.column, dialect)
        column_type = _column_type(self.column_type, dialect)
        null_sql = "" if self.nullable else " NOT NULL"
        default_sql = _default_sql(self.default, dialect)
        if not self.nullable and not default_sql:
            return []
        if dialect == "postgres":
            return [
                "ALTER TABLE "
                f"{table} ADD COLUMN IF NOT EXISTS {column} "
                f"{column_type}{default_sql}{null_sql}"
            ]
        return [
            "ALTER TABLE "
            f"{table} ADD COLUMN {column} {column_type}{default_sql}{null_sql}"
        ]


@dataclass(frozen=True, slots=True)
class CreateIndex:
    table: str
    columns: tuple[str, ...]
    name: str | None = None
    if_not_exists: bool = True
    unique: bool = False
    where: str | None = None
    risk: SchemaOpRisk = SchemaOpRisk.SAFE

    def __init__(
        self,
        table: str,
        columns: tuple[str, ...] | list[str],
        name: str | None = None,
        if_not_exists: bool = True,
        unique: bool = False,
        where: str | None = None,
        risk: SchemaOpRisk | None = None,
    ) -> None:
        object.__setattr__(self, "table", table)
        object.__setattr__(self, "columns", tuple(columns))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "if_not_exists", if_not_exists)
        object.__setattr__(self, "unique", unique)
        object.__setattr__(self, "where", where)
        resolved_risk = risk or (SchemaOpRisk.MANUAL if unique else SchemaOpRisk.SAFE)
        object.__setattr__(self, "risk", resolved_risk)

    def to_sql(self, dialect: Dialect) -> list[str]:
        if self.unique or not self.columns:
            return []
        name = self.name or f"idx_{self.table}_{'_'.join(self.columns)}"[:62]
        if dialect == "mysql" and self.where:
            return []
        exists_sql = (
            "IF NOT EXISTS " if self.if_not_exists and dialect != "mysql" else ""
        )
        table = quote_identifier(self.table, dialect)
        index = quote_identifier(name, dialect)
        columns = ", ".join(
            quote_identifier(column, dialect) for column in self.columns
        )
        where = f" WHERE {self.where}" if self.where and dialect != "mysql" else ""
        return [f"CREATE INDEX {exists_sql}{index} ON {table}({columns}){where}"]


@dataclass(frozen=True, slots=True)
class RenameColumn:
    table: str
    old: str
    new: str
    risk: SchemaOpRisk = SchemaOpRisk.GUARDED

    def to_sql(self, dialect: Dialect) -> list[str]:
        table = quote_identifier(self.table, dialect)
        old = quote_identifier(self.old, dialect)
        new = quote_identifier(self.new, dialect)
        return [f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"]


@dataclass(frozen=True, slots=True)
class DropColumn:
    table: str
    column: str
    risk: SchemaOpRisk = SchemaOpRisk.GUARDED

    def to_sql(self, dialect: Dialect) -> list[str]:
        table = quote_identifier(self.table, dialect)
        column = quote_identifier(self.column, dialect)
        return [f"ALTER TABLE {table} DROP COLUMN {column}"]


@dataclass(frozen=True, slots=True)
class AlterColumnType:
    table: str
    column: str
    column_type: str | dict[str, str]
    nullable: bool | None = None
    risk: SchemaOpRisk = SchemaOpRisk.GUARDED

    def to_sql(self, dialect: Dialect) -> list[str]:
        column_type = _column_type(self.column_type, dialect)
        table = quote_identifier(self.table, dialect)
        column = quote_identifier(self.column, dialect)
        if dialect == "postgres":
            return [f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {column_type}"]
        if dialect == "mysql":
            null_sql = " NULL" if self.nullable else " NOT NULL"
            if self.nullable is None:
                null_sql = ""
            return [
                f"ALTER TABLE {table} MODIFY COLUMN {column} {column_type}{null_sql}"
            ]
        return []


def normalize_schema_ops(items: list[str | SchemaOp], dialect: Dialect) -> list[str]:
    sql_list: list[str] = []
    for item in items:
        if isinstance(item, str):
            sql_list.append(item)
        else:
            sql_list.extend(item.to_sql(dialect))
    return sql_list
