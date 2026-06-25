"""Global BEN service infrastructure — engines and integrations in system_main.db."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from database.thread_store import get_system_db_connection

ChannelKind = Literal["engine", "integration"]
ChannelStatus = Literal["active", "disconnected"]
SourceType = Literal["local", "google_drive", "gmail", "external_library", "sovereign_sonar"]

CHANNEL_KINDS: frozenset[str] = frozenset({"engine", "integration"})
CHANNEL_STATUSES: frozenset[str] = frozenset({"active", "disconnected"})
SOURCE_TYPES: frozenset[str] = frozenset(
    {"local", "google_drive", "gmail", "external_library", "sovereign_sonar"}
)

_SENSITIVE_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "refresh_token",
        "oauth_token",
        "api_key",
        "client_secret",
        "credentials",
        "session_token",
        "bearer_token",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def init_global_service_schema() -> None:
    """Create org-scoped engine + integration tables in system_main.db."""
    with get_system_db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS global_engine_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id TEXT NOT NULL,
                catalog_key TEXT NOT NULL,
                engine_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK (
                    status IN ('active', 'disconnected')
                ),
                config_metadata TEXT NOT NULL DEFAULT '{}',
                credentials_json TEXT NOT NULL DEFAULT '{}',
                feature_flags TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(org_id, catalog_key)
            );

            CREATE INDEX IF NOT EXISTS idx_global_engine_org_status
                ON global_engine_activations(org_id, status);

            CREATE TABLE IF NOT EXISTS global_integrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                org_id TEXT NOT NULL,
                catalog_key TEXT NOT NULL,
                integration_type TEXT NOT NULL CHECK (
                    integration_type IN (
                        'local', 'google_drive', 'gmail', 'external_library', 'sovereign_sonar'
                    )
                ),
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK (
                    status IN ('active', 'disconnected')
                ),
                source_metadata TEXT NOT NULL DEFAULT '{}',
                credentials_json TEXT NOT NULL DEFAULT '{}',
                feature_flags TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(org_id, catalog_key)
            );

            CREATE INDEX IF NOT EXISTS idx_global_integrations_org_status
                ON global_integrations(org_id, status);
            CREATE INDEX IF NOT EXISTS idx_global_integrations_org_type
                ON global_integrations(org_id, integration_type);
            """
        )
        conn.commit()


def _serialize_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))


def _deserialize_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _validate_status(status: str) -> ChannelStatus:
    token = str(status or "").strip().lower()
    if token not in CHANNEL_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(CHANNEL_STATUSES))}")
    return token  # type: ignore[return-value]


def _validate_source_type(source_type: str) -> SourceType:
    token = str(source_type or "").strip().lower()
    if token not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of: {', '.join(sorted(SOURCE_TYPES))}")
    return token  # type: ignore[return-value]


def _split_metadata_and_credentials(metadata: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    public: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if str(key).lower() in _SENSITIVE_CREDENTIAL_KEYS:
            secrets[str(key)] = value
        else:
            public[str(key)] = value
    return public, secrets


def metadata_has_sensitive_tokens(metadata: dict[str, Any]) -> bool:
    return any(str(key).lower() in _SENSITIVE_CREDENTIAL_KEYS for key in metadata)


def scrub_sensitive_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metadata.items() if str(k).lower() not in _SENSITIVE_CREDENTIAL_KEYS}


def _infer_channel_kind(catalog_key: str, source_type: str) -> ChannelKind:
    key = (catalog_key or "").strip().lower()
    if key.startswith("engine-"):
        return "engine"
    if key.startswith("sonar-"):
        return "integration"
    return "integration"


def _engine_id_from_catalog(catalog_key: str) -> str:
    key = (catalog_key or "").strip().lower()
    if key.startswith("engine-"):
        return key.removeprefix("engine-")
    return key or "unknown"


def _integration_type_from_source(source_type: str, catalog_key: str) -> SourceType:
    validated = _validate_source_type(source_type)
    key = (catalog_key or "").strip().lower()
    if key.startswith("sonar-"):
        return "sovereign_sonar"
    if validated == "external_library" and "gmail" in key:
        return "gmail"
    return validated


def _row_to_engine(row: sqlite3.Row) -> dict[str, Any]:
    config = _deserialize_json(str(row["config_metadata"]))
    credentials = _deserialize_json(str(row["credentials_json"]))
    return {
        "id": int(row["id"]),
        "channel_kind": "engine",
        "catalog_key": str(row["catalog_key"]),
        "engine_id": str(row["engine_id"]),
        "name": str(row["catalog_key"]).replace("engine-", "").replace("-", " ").title(),
        "source_type": "external_library",
        "source_metadata": {**config, "catalog_key": str(row["catalog_key"])},
        "status": str(row["status"]),
        "feature_flags": _deserialize_json(str(row["feature_flags"])),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "has_credentials": bool(credentials),
    }


def _row_to_integration(row: sqlite3.Row) -> dict[str, Any]:
    metadata = _deserialize_json(str(row["source_metadata"]))
    credentials = _deserialize_json(str(row["credentials_json"]))
    catalog_key = str(row["catalog_key"])
    return {
        "id": int(row["id"]),
        "channel_kind": "integration",
        "catalog_key": catalog_key,
        "name": str(row["name"]),
        "source_type": str(row["integration_type"]),
        "source_metadata": {**metadata, "catalog_key": catalog_key},
        "status": str(row["status"]),
        "feature_flags": _deserialize_json(str(row["feature_flags"])),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "has_credentials": bool(credentials),
    }


def _public_channel_view(channel: dict[str, Any]) -> dict[str, Any]:
    """Repository-compatible payload without credential material."""
    return {
        "id": channel["id"],
        "name": channel["name"],
        "source_type": channel["source_type"],
        "source_metadata": channel["source_metadata"],
        "status": channel["status"],
        "created_at": channel["created_at"],
        "updated_at": channel["updated_at"],
        "channel_kind": channel["channel_kind"],
        "catalog_key": channel["catalog_key"],
        "feature_flags": channel.get("feature_flags") or {},
    }


def list_global_channels(org_id: str) -> list[dict[str, Any]]:
    init_global_service_schema()
    org = str(org_id or "").strip()
    if not org:
        raise ValueError("org_id is required")
    with get_system_db_connection() as conn:
        engines = conn.execute(
            """
            SELECT id, org_id, catalog_key, engine_id, status, config_metadata,
                   credentials_json, feature_flags, created_at, updated_at
            FROM global_engine_activations
            WHERE org_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (org,),
        ).fetchall()
        integrations = conn.execute(
            """
            SELECT id, org_id, catalog_key, integration_type, name, status,
                   source_metadata, credentials_json, feature_flags, created_at, updated_at
            FROM global_integrations
            WHERE org_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (org,),
        ).fetchall()
    channels = [_public_channel_view(_row_to_engine(row)) for row in engines]
    channels.extend(_public_channel_view(_row_to_integration(row)) for row in integrations)
    channels.sort(key=lambda item: item["updated_at"], reverse=True)
    return channels


def list_active_global_channels(org_id: str) -> list[dict[str, Any]]:
    return [channel for channel in list_global_channels(org_id) if channel["status"] == "active"]


def get_global_channel(org_id: str, channel_id: int) -> dict[str, Any] | None:
    init_global_service_schema()
    org = str(org_id or "").strip()
    with get_system_db_connection() as conn:
        engine = conn.execute(
            """
            SELECT id, org_id, catalog_key, engine_id, status, config_metadata,
                   credentials_json, feature_flags, created_at, updated_at
            FROM global_engine_activations
            WHERE org_id = ? AND id = ?
            """,
            (org, int(channel_id)),
        ).fetchone()
        if engine is not None:
            return _public_channel_view(_row_to_engine(engine))
        integration = conn.execute(
            """
            SELECT id, org_id, catalog_key, integration_type, name, status,
                   source_metadata, credentials_json, feature_flags, created_at, updated_at
            FROM global_integrations
            WHERE org_id = ? AND id = ?
            """,
            (org, int(channel_id)),
        ).fetchone()
        if integration is not None:
            return _public_channel_view(_row_to_integration(integration))
    return None


def connect_global_channel(
    org_id: str,
    *,
    name: str,
    source_type: str,
    source_metadata: dict[str, Any],
    feature_flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Activate or re-activate a global engine/integration for an org scope."""
    init_global_service_schema()
    org = str(org_id or "").strip()
    if not org:
        raise ValueError("org_id is required")
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("name is required")

    metadata = dict(source_metadata or {})
    catalog_key = str(metadata.get("catalog_key") or "").strip()
    if not catalog_key:
        raise ValueError("source_metadata.catalog_key is required")

    public_meta, credentials = _split_metadata_and_credentials(metadata)
    public_meta["catalog_key"] = catalog_key
    flags = dict(feature_flags or {})
    now = _now_iso()
    kind = _infer_channel_kind(catalog_key, source_type)

    with get_system_db_connection() as conn:
        if kind == "engine":
            engine_id = _engine_id_from_catalog(catalog_key)
            conn.execute(
                """
                INSERT INTO global_engine_activations (
                    org_id, catalog_key, engine_id, status, config_metadata,
                    credentials_json, feature_flags, created_at, updated_at
                )
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                ON CONFLICT(org_id, catalog_key) DO UPDATE SET
                    engine_id = excluded.engine_id,
                    status = 'active',
                    config_metadata = excluded.config_metadata,
                    credentials_json = excluded.credentials_json,
                    feature_flags = excluded.feature_flags,
                    updated_at = excluded.updated_at
                """,
                (
                    org,
                    catalog_key,
                    engine_id,
                    _serialize_json(public_meta),
                    _serialize_json(credentials),
                    _serialize_json(flags),
                    now,
                    now,
                ),
            )
        else:
            integration_type = _integration_type_from_source(source_type, catalog_key)
            conn.execute(
                """
                INSERT INTO global_integrations (
                    org_id, catalog_key, integration_type, name, status,
                    source_metadata, credentials_json, feature_flags, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                ON CONFLICT(org_id, catalog_key) DO UPDATE SET
                    integration_type = excluded.integration_type,
                    name = excluded.name,
                    status = 'active',
                    source_metadata = excluded.source_metadata,
                    credentials_json = excluded.credentials_json,
                    feature_flags = excluded.feature_flags,
                    updated_at = excluded.updated_at
                """,
                (
                    org,
                    catalog_key,
                    integration_type,
                    clean_name[:256],
                    _serialize_json(public_meta),
                    _serialize_json(credentials),
                    _serialize_json(flags),
                    now,
                    now,
                ),
            )
        conn.commit()
        row = conn.execute(
            """
            SELECT id FROM global_engine_activations
            WHERE org_id = ? AND catalog_key = ?
            UNION ALL
            SELECT id FROM global_integrations
            WHERE org_id = ? AND catalog_key = ?
            LIMIT 1
            """,
            (org, catalog_key, org, catalog_key),
        ).fetchone()

    if row is None:
        raise RuntimeError("global channel connect persist failed")
    channel = get_global_channel(org, int(row["id"]))
    if channel is None:
        raise RuntimeError("global channel connect persist failed")
    return channel


def toggle_global_channel(org_id: str, channel_id: int) -> dict[str, Any]:
    """Disconnect channel and scrub stored credentials at the global layer."""
    init_global_service_schema()
    org = str(org_id or "").strip()
    existing = get_global_channel(org, channel_id)
    if existing is None:
        raise ValueError("channel not found")

    scrubbed_meta = scrub_sensitive_metadata(existing.get("source_metadata") or {})
    now = _now_iso()

    with get_system_db_connection() as conn:
        if existing["channel_kind"] == "engine":
            conn.execute(
                """
                UPDATE global_engine_activations
                SET status = 'disconnected',
                    config_metadata = ?,
                    credentials_json = '{}',
                    updated_at = ?
                WHERE org_id = ? AND id = ?
                """,
                (_serialize_json(scrubbed_meta), now, org, int(channel_id)),
            )
        else:
            conn.execute(
                """
                UPDATE global_integrations
                SET status = 'disconnected',
                    source_metadata = ?,
                    credentials_json = '{}',
                    updated_at = ?
                WHERE org_id = ? AND id = ?
                """,
                (_serialize_json(scrubbed_meta), now, org, int(channel_id)),
            )
        conn.commit()

    updated = get_global_channel(org, channel_id)
    if updated is None:
        raise RuntimeError("global channel toggle persist failed")
    return updated


def global_channel_has_credentials(org_id: str, channel_id: int) -> bool:
    init_global_service_schema()
    org = str(org_id or "").strip()
    with get_system_db_connection() as conn:
        row = conn.execute(
            """
            SELECT credentials_json FROM global_engine_activations
            WHERE org_id = ? AND id = ?
            UNION ALL
            SELECT credentials_json FROM global_integrations
            WHERE org_id = ? AND id = ?
            LIMIT 1
            """,
            (org, int(channel_id), org, int(channel_id)),
        ).fetchone()
    if row is None:
        return False
    creds = _deserialize_json(str(row["credentials_json"]))
    return bool(creds)
