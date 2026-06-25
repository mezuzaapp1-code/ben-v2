"""BASE44-style JIT relational schema extraction and safe SQLite provisioning."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.knowledge_store import init_portable_context_store, insert_context_record, resolve_project_db_path
from services.project_tools import slugify_project_name

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", re.IGNORECASE)
_TABLE_HEADER_RE = re.compile(
    r"^(?:table|entity|model)\s*:?\s*[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TABLE_INLINE_RE = re.compile(
    r"(?:table|entity|model)\s*:?\s*[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?\s+"
    r"(?:with|having)\s+(?:columns?|fields?)\s*:?\s*([^\n.;]+)",
    re.IGNORECASE,
)
_COLUMN_LINE_RE = re.compile(
    r"^\s*[-*]?\s*([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\(([^)]+)\)|:\s*([A-Za-z_][A-Za-z0-9_]*))?"
    r"(?:\s*(PRIMARY\s+KEY|UNIQUE|NOT\s+NULL|NULLABLE))?"
    r"\s*$",
    re.IGNORECASE,
)
_TYPE_ALIASES: dict[str, str] = {
    "text": "TEXT",
    "string": "TEXT",
    "varchar": "TEXT",
    "str": "TEXT",
    "integer": "INTEGER",
    "int": "INTEGER",
    "number": "INTEGER",
    "real": "REAL",
    "float": "REAL",
    "double": "REAL",
    "decimal": "REAL",
    "boolean": "INTEGER",
    "bool": "INTEGER",
    "datetime": "TEXT",
    "date": "TEXT",
    "timestamp": "TEXT",
    "uuid": "TEXT",
    "json": "TEXT",
    "blob": "BLOB",
}

_SQLITE_TYPES = frozenset(_TYPE_ALIASES.values())
_RESERVED_TABLE_NAMES = frozenset(
    {
        "context_records",
        "context_records_fts",
        "knowledge_store",
        "jit_schema_registry",
        "sqlite_master",
        "sqlite_sequence",
    }
)


@dataclass(frozen=True)
class ColumnBlueprint:
    name: str
    data_type: str
    primary_key: bool = False
    nullable: bool = True
    unique: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "primary_key": self.primary_key,
            "nullable": self.nullable,
            "unique": self.unique,
        }


@dataclass(frozen=True)
class TableBlueprint:
    name: str
    columns: tuple[ColumnBlueprint, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": [column.as_dict() for column in self.columns],
        }


def validate_sql_identifier(raw: str, *, field: str) -> str:
    name = str(raw or "").strip()
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"invalid {field}: {raw!r}")
    return name


def normalize_sqlite_type(raw: str) -> str:
    token = str(raw or "text").strip().lower()
    mapped = _TYPE_ALIASES.get(token)
    if mapped is None:
        raise ValueError(f"unsupported data type: {raw!r}")
    return mapped


def _parse_column_flags(raw: str | None) -> tuple[bool, bool, bool]:
    flags = str(raw or "").upper()
    primary_key = "PRIMARY KEY" in flags or "PK" in flags
    unique = "UNIQUE" in flags
    nullable = "NOT NULL" not in flags
    if primary_key:
        nullable = False
    return primary_key, nullable, unique


def _column_from_tokens(name: str, type_token: str | None, flags: str | None = None) -> ColumnBlueprint:
    col_name = validate_sql_identifier(name, field="column name")
    sqlite_type = normalize_sqlite_type(type_token or "text")
    primary_key, nullable, unique = _parse_column_flags(flags)
    return ColumnBlueprint(
        name=col_name,
        data_type=sqlite_type,
        primary_key=primary_key,
        nullable=nullable,
        unique=unique,
    )


def _split_column_list(raw: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in raw:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            piece = "".join(buffer).strip()
            if piece:
                parts.append(piece)
            buffer = []
            continue
        buffer.append(char)
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_column_token(token: str) -> ColumnBlueprint | None:
    cleaned = token.strip()
    if not cleaned:
        return None
    match = re.match(
        r"^([A-Za-z_][A-Za-z0-9_]*)(?:\s+\(?([A-Za-z_][A-Za-z0-9_]*)\)?)?"
        r"(?:\s+(PRIMARY\s+KEY|UNIQUE|NOT\s+NULL))?$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    name, type_token, flag = match.groups()
    return _column_from_tokens(name, type_token, flag)


def _assert_table_name_allowed(table_name: str) -> str:
    safe_table = validate_sql_identifier(table_name, field="table name")
    if safe_table.lower() in _RESERVED_TABLE_NAMES:
        raise ValueError(f"reserved table name: {safe_table}")
    return safe_table


def _table_from_name_and_columns(table_name: str, column_tokens: list[str]) -> TableBlueprint:
    safe_table = _assert_table_name_allowed(table_name)
    columns: list[ColumnBlueprint] = []
    for token in column_tokens:
        column = _parse_column_token(token)
        if column is not None:
            columns.append(column)
    if not columns:
        columns = [
            ColumnBlueprint(name="id", data_type="INTEGER", primary_key=True, nullable=False),
            ColumnBlueprint(name="payload", data_type="TEXT", nullable=False),
            ColumnBlueprint(name="created_at", data_type="TEXT", nullable=False),
        ]
    if not any(column.primary_key for column in columns):
        columns.insert(
            0,
            ColumnBlueprint(name="id", data_type="INTEGER", primary_key=True, nullable=False),
        )
    return TableBlueprint(name=safe_table, columns=tuple(columns))


def _parse_json_schema(raw: str) -> list[TableBlueprint]:
    payload = json.loads(raw)
    tables_raw = payload.get("tables") if isinstance(payload, dict) else payload
    if not isinstance(tables_raw, list):
        raise ValueError("schema JSON must be an array or {tables: [...]}")
    tables: list[TableBlueprint] = []
    for entry in tables_raw:
        if not isinstance(entry, dict):
            raise ValueError("each table entry must be an object")
        table_name = _assert_table_name_allowed(str(entry.get("name") or ""))
        columns_raw = entry.get("columns")
        if not isinstance(columns_raw, list) or not columns_raw:
            raise ValueError(f"table {table_name} requires a non-empty columns array")
        columns: list[ColumnBlueprint] = []
        for column in columns_raw:
            if not isinstance(column, dict):
                raise ValueError(f"table {table_name} columns must be objects")
            columns.append(
                ColumnBlueprint(
                    name=validate_sql_identifier(str(column.get("name") or ""), field="column name"),
                    data_type=normalize_sqlite_type(str(column.get("data_type") or column.get("type") or "text")),
                    primary_key=bool(column.get("primary_key") or column.get("pk")),
                    nullable=bool(column.get("nullable", not column.get("primary_key"))),
                    unique=bool(column.get("unique")),
                )
            )
        tables.append(TableBlueprint(name=table_name, columns=tuple(columns)))
    return tables


def _parse_description_tables(description: str) -> list[TableBlueprint]:
    text = (description or "").strip()
    if not text:
        return []

    for match in _JSON_FENCE_RE.finditer(text):
        try:
            return _parse_json_schema(match.group(1))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    try:
        if text.startswith("{") or text.startswith("["):
            return _parse_json_schema(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    tables: list[TableBlueprint] = []
    for match in _TABLE_INLINE_RE.finditer(text):
        columns = _split_column_list(match.group(2))
        tables.append(_table_from_name_and_columns(match.group(1), columns))

    current_table: str | None = None
    current_columns: list[str] = []
    for line in text.splitlines():
        header = _TABLE_HEADER_RE.match(line.strip())
        if header:
            if current_table and current_columns:
                tables.append(_table_from_name_and_columns(current_table, current_columns))
            current_table = header.group(1)
            current_columns = []
            continue
        if current_table:
            column_match = _COLUMN_LINE_RE.match(line)
            if column_match:
                name, paren_type, colon_type, flags = column_match.groups()
                type_token = paren_type or colon_type
                current_columns.append(
                    f"{name} {type_token or 'text'} {flags or ''}".strip()
                )
    if current_table and current_columns:
        tables.append(_table_from_name_and_columns(current_table, current_columns))

    if tables:
        return tables

    entity_match = re.search(
        r"\b(?:track|manage|store|monitor)\s+(?:a\s+)?([a-z][a-z0-9_\s,-]{2,80})",
        text,
        flags=re.IGNORECASE,
    )
    if entity_match:
        nouns = re.findall(r"[a-z][a-z0-9_]{2,32}", entity_match.group(1).lower())
        for noun in nouns[:4]:
            if noun in {"with", "and", "for", "the", "app", "system", "data"}:
                continue
            plural = noun if noun.endswith("s") else f"{noun}s"
            tables.append(_table_from_name_and_columns(plural, ["name text", "status text", "notes text"]))

    return tables


def extract_schema_blueprint(
    description: str,
    explicit_tables: list[dict[str, Any]] | None = None,
) -> list[TableBlueprint]:
    """Build validated table blueprints from explicit payload or software description."""
    if explicit_tables:
        return _parse_json_schema(json.dumps({"tables": explicit_tables}))
    return _parse_description_tables(description)


def build_create_table_sql(table: TableBlueprint) -> str:
    """Render a parameterized-safe CREATE TABLE statement using validated identifiers."""
    table_name = _assert_table_name_allowed(table.name)

    parts: list[str] = []
    for column in table.columns:
        col_name = validate_sql_identifier(column.name, field="column name")
        if column.data_type not in _SQLITE_TYPES:
            raise ValueError(f"unsupported sqlite type: {column.data_type}")
        definition = [f'"{col_name}"', column.data_type]
        if column.primary_key:
            definition.append("PRIMARY KEY")
        if not column.nullable and not column.primary_key:
            definition.append("NOT NULL")
        if column.unique and not column.primary_key:
            definition.append("UNIQUE")
        parts.append(" ".join(definition))

    joined = ", ".join(parts)
    return f'CREATE TABLE IF NOT EXISTS "{table_name}" ({joined})'


def _ensure_registry_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jit_schema_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL UNIQUE,
            blueprint_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def apply_schema_to_project_db(project_slug: str, tables: list[TableBlueprint]) -> list[dict[str, Any]]:
    """Execute validated CREATE TABLE statements inside the isolated project_context.db."""
    slug = slugify_project_name(project_slug)
    init_portable_context_store(slug)
    if not tables:
        return []

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    blueprint: list[dict[str, Any]] = []

    with sqlite3.connect(str(resolve_project_db_path(slug)), check_same_thread=False) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_registry_table(conn)
        for table in tables:
            ddl = build_create_table_sql(table)
            conn.execute(ddl)
            payload = table.as_dict()
            blueprint.append(payload)
            conn.execute(
                """
                INSERT INTO jit_schema_registry (table_name, blueprint_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(table_name) DO UPDATE SET
                    blueprint_json = excluded.blueprint_json,
                    created_at = excluded.created_at
                """,
                (table.name, json.dumps(payload, ensure_ascii=False), now),
            )
        conn.commit()

    summary = json.dumps({"tables": blueprint}, ensure_ascii=False, indent=2)
    insert_context_record(
        slug,
        head="documentation",
        title="JIT workspace schema blueprint",
        content=summary,
    )
    return blueprint


def provision_conversational_workspace_schema(
    project_slug: str,
    software_description: str,
    explicit_tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract schema intent and seed relational tables in project_context.db."""
    tables = extract_schema_blueprint(software_description, explicit_tables)
    blueprint = apply_schema_to_project_db(project_slug, tables)
    return {
        "project_slug": slugify_project_name(project_slug),
        "schema_blueprint": blueprint,
        "tables_created": len(blueprint),
    }
