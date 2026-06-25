"""JIT conversational schema generation and safe SQLite provisioning."""
from __future__ import annotations

import json
import sqlite3

import pytest

from services.project_schema_generator import (
    apply_schema_to_project_db,
    build_create_table_sql,
    extract_schema_blueprint,
    normalize_sqlite_type,
    provision_conversational_workspace_schema,
    validate_sql_identifier,
)
from services.project_tools import projects_root, slugify_project_name


@pytest.fixture
def project_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "projects"))
    return "inventory-tracker"


def test_validate_sql_identifier_rejects_injection():
    with pytest.raises(ValueError, match="invalid table name"):
        validate_sql_identifier("users; DROP TABLE users", field="table name")


def test_normalize_sqlite_type_aliases():
    assert normalize_sqlite_type("varchar") == "TEXT"
    assert normalize_sqlite_type("bool") == "INTEGER"
    assert normalize_sqlite_type("datetime") == "TEXT"


def test_extract_schema_from_json_fence():
    description = """
    Build an inventory app.
    ```json
    {
      "tables": [
        {
          "name": "inventory_items",
          "columns": [
            {"name": "id", "data_type": "integer", "primary_key": true, "nullable": false},
            {"name": "sku", "data_type": "text", "unique": true},
            {"name": "quantity", "data_type": "integer"}
          ]
        }
      ]
    }
    ```
    """
    tables = extract_schema_blueprint(description)
    assert len(tables) == 1
    assert tables[0].name == "inventory_items"
    assert tables[0].columns[0].primary_key is True


def test_extract_schema_from_table_header_lines():
    description = """
    Table: work_orders
    - id (integer, PRIMARY KEY)
    - title: text
    - status: text NOT NULL
    """
    tables = extract_schema_blueprint(description)
    assert len(tables) == 1
    assert tables[0].name == "work_orders"
    assert any(column.name == "status" and column.nullable is False for column in tables[0].columns)


def test_build_create_table_sql_uses_validated_identifiers():
    tables = extract_schema_blueprint(
        "",
        explicit_tables=[
            {
                "name": "crew_members",
                "columns": [
                    {"name": "id", "data_type": "integer", "primary_key": True, "nullable": False},
                    {"name": "name", "data_type": "text"},
                ],
            }
        ],
    )
    ddl = build_create_table_sql(tables[0])
    assert ddl.startswith('CREATE TABLE IF NOT EXISTS "crew_members"')
    assert '"id" INTEGER PRIMARY KEY' in ddl
    assert '"name" TEXT' in ddl


def test_apply_schema_to_project_db_creates_tables(project_slug):
    tables = extract_schema_blueprint(
        "",
        explicit_tables=[
            {
                "name": "assets",
                "columns": [
                    {"name": "id", "data_type": "integer", "primary_key": True, "nullable": False},
                    {"name": "tag", "data_type": "text", "unique": True},
                ],
            }
        ],
    )
    blueprint = apply_schema_to_project_db(project_slug, tables)
    assert len(blueprint) == 1
    assert blueprint[0]["name"] == "assets"

    db_path = projects_root() / slugify_project_name(project_slug) / "project_context.db"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"
        ).fetchall()
        assert rows
        registry = conn.execute(
            "SELECT blueprint_json FROM jit_schema_registry WHERE table_name='assets'"
        ).fetchone()
        assert registry is not None
        payload = json.loads(registry[0])
        assert payload["columns"][1]["name"] == "tag"


def test_provision_conversational_workspace_schema_end_to_end(project_slug):
    description = (
        "Table: inspections with columns id integer PRIMARY KEY, site text, score real, inspected_at datetime"
    )
    payload = provision_conversational_workspace_schema(project_slug, description)
    assert payload["project_slug"] == slugify_project_name(project_slug)
    assert payload["tables_created"] >= 1
    assert payload["schema_blueprint"][0]["name"] == "inspections"


def test_rejects_reserved_table_name():
    with pytest.raises(ValueError, match="reserved table name"):
        extract_schema_blueprint(
            "",
            explicit_tables=[
                {
                    "name": "knowledge_store",
                    "columns": [{"name": "id", "data_type": "integer", "primary_key": True}],
                }
            ],
        )
