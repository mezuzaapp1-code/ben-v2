#!/usr/bin/env python3
"""Gate D1 production canary for existing chunk FTS. Does not enable FTS globally."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import httpx

from tests.gate_d1.decision import EXPECTED_SHA, decide_gate_d1
from tests.gate_d1.prod_client import (
    ABSENT_QUERY,
    BASE,
    FTS_MODES,
    chat_stream,
    delete_file,
    get_project,
    get_thread,
    score_done,
    thread_polluted,
    upload_gold,
    wait_ready,
)
from tests.gate_m.gold_document import PAGES
from tests.gate_m.gold_questions import QUESTIONS
from tests.gate_m.pdf_builder import make_multiline_pdf
from tests.gate_p.score import summarize_mode

RAIL = "/home/ubuntu/.local/node_modules/@railway/cli/bin/railway"
WS_PATH = Path("/tmp/gate_d1_workspaces.json")
BEARER_PATH = Path("/tmp/ben_d1_bearer")
DEPLOYMENT_ID = "1c93a9bd-0f52-4253-b69d-458490b90d5e"


def _bearer() -> str:
    return BEARER_PATH.read_text(encoding="utf-8").strip()


def _workspaces() -> dict:
    return json.loads(WS_PATH.read_text(encoding="utf-8"))


def _mint_bearer() -> str:
    """Mint a short-lived Clerk JWT. Never prints the token or secret."""
    env = os.environ.copy()
    raw = subprocess.check_output(
        [RAIL, "variables", "--service", "ben-v2", "--json"],
        env=env,
        text=True,
    )
    secret = str(json.loads(raw).get("CLERK_SECRET_KEY") or "").strip().strip('"').strip("'")
    if not secret:
        raise RuntimeError("CLERK_SECRET_KEY missing from production service vars")
    from clerk_backend_api import Clerk
    from clerk_backend_api.models.getuserlistop import GetUserListRequest

    try:
        with Clerk(bearer_auth=secret) as clerk:
            users = clerk.users.list(request=GetUserListRequest(limit=1))
            if not users:
                raise RuntimeError("no Clerk users")
            session = clerk.sessions.create(request={"user_id": users[0].id})
            token = clerk.sessions.create_token(session_id=session.id, expires_in_seconds=3600)
            jwt = getattr(token, "jwt", None) or (token.get("jwt") if isinstance(token, dict) else None)
            if not jwt:
                raise RuntimeError("clerk jwt missing")
    except Exception as exc:
        raise RuntimeError(f"clerk mint failed: {type(exc).__name__}") from None
    bearer = f"Bearer {jwt}"
    BEARER_PATH.write_text(bearer, encoding="utf-8")
    BEARER_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return bearer


def _railway_var_names() -> set[str]:
    raw = subprocess.check_output([RAIL, "variables", "--service", "ben-v2", "--json"], text=True)
    data = json.loads(raw)
    return set(data) if isinstance(data, dict) else set()


def _allowlist_state() -> dict[str, str | None]:
    raw = subprocess.check_output([RAIL, "variables", "--service", "ben-v2", "--json"], text=True)
    data = json.loads(raw) if raw.strip() else {}
    flag = data.get("BEN_WORKSPACE_CHUNK_RETRIEVAL")
    allow = data.get("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS")
    return {
        "flag": None if flag is None else str(flag),
        "allowlist": None if allow is None else str(allow),
        "allowlist_uuid_count": 0
        if not allow
        else len([p for p in str(allow).split(",") if p.strip()]),
    }


def _revert_canary_vars() -> dict[str, Any]:
    names = _railway_var_names()
    deleted: list[str] = []
    for key in ("BEN_WORKSPACE_CHUNK_RETRIEVAL", "BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS"):
        if key not in names:
            continue
        subprocess.run(
            [RAIL, "variable", "delete", key, "--service", "ben-v2"],
            input="y\n",
            text=True,
            check=True,
        )
        deleted.append(key)
    return {"deleted": deleted}


async def _health() -> dict:
    async with httpx.AsyncClient(timeout=20.0) as cx:
        h = (await cx.get(f"{BASE}/health")).json()
        r = (await cx.get(f"{BASE}/ready")).json()
        return {"health": h, "ready": r}


async def _run_questions(cx, bearer, project_id, questions) -> tuple[list[dict], list[str]]:
    rows = []
    threads: list[str] = []
    for q in questions:
        print(f"D1 {q['question_id']} …", flush=True)
        status, done, latency = await chat_stream(
            cx, bearer, message=q["question"], project_id=project_id
        )
        row = score_done(q, done, latency_ms=latency, http_status=status)
        print(
            f"D1 {q['question_id']} status={status} mode={row.get('retrieval_mode')} "
            f"chunks={row.get('chunks_selected')} pass={row.get('pass')}",
            flush=True,
        )
        rows.append(row)
        if row.get("thread_id"):
            threads.append(row["thread_id"])
    return rows, threads


def _write_outputs(artifacts: dict) -> None:
    out = Path("/opt/cursor/artifacts")
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate_d1_canary.json").write_text(json.dumps(artifacts, indent=2, default=str) + "\n")
    fixture = _ROOT / "tests" / "fixtures" / "gate_d1"
    fixture.mkdir(parents=True, exist_ok=True)
    slim_rows = []
    for row in artifacts.get("canary_rows") or []:
        slim_rows.append(
            {
                "question_id": row.get("question_id"),
                "category": row.get("category"),
                "position": row.get("position"),
                "http_status": row.get("http_status"),
                "retrieval_mode": row.get("retrieval_mode"),
                "fallback_reason": row.get("fallback_reason"),
                "chunks_considered": row.get("chunks_considered"),
                "chunks_selected": row.get("chunks_selected"),
                "chunk_id_count": len(row.get("chunk_ids") or []),
                "evidence_pages": row.get("evidence_pages"),
                "decisive_in_injected": row.get("decisive_in_injected"),
                "extractive_answer_ok": row.get("extractive_answer_ok"),
                "pass": row.get("pass"),
                "mrl": row.get("mrl"),
                "failure_category": row.get("failure_category"),
                "citation_page_accuracy": row.get("citation_page_accuracy"),
                "latency_ms": row.get("latency_ms"),
                "fts_latency_ms": row.get("fts_latency_ms"),
            }
        )
    slim = {
        "gate": "D1",
        "decision": artifacts.get("decision"),
        "production_sha": artifacts.get("production_sha"),
        "deployment_id": artifacts.get("deployment_id"),
        "canary_workspace_id": artifacts.get("canary_workspace_id"),
        "control_workspace_id": artifacts.get("control_workspace_id"),
        "canary_summary": artifacts.get("canary_summary"),
        "control_summary": artifacts.get("control_summary"),
        "fallback": {
            k: v
            for k, v in (artifacts.get("fallback") or {}).items()
            if k != "thread_id"
        },
        "fts_actually_used": artifacts.get("fts_actually_used"),
        "retrieval_modes_observed": artifacts.get("retrieval_modes_observed"),
        "transcript_pollution": artifacts.get("transcript_pollution"),
        "non_canary_unaffected": artifacts.get("non_canary_unaffected"),
        "cleanup": artifacts.get("cleanup"),
        "allowlist_before": artifacts.get("allowlist_before"),
        "allowlist_after_revert": artifacts.get("allowlist_after_revert"),
        "canary_ready": artifacts.get("canary_ready"),
        "control_ready": artifacts.get("control_ready"),
        "canary_rows": slim_rows,
        "control_rows": [
            {
                "question_id": r.get("question_id"),
                "retrieval_mode": r.get("retrieval_mode"),
                "chunk_id_count": len(r.get("chunk_ids") or []),
                "fallback_reason": r.get("fallback_reason"),
                "http_status": r.get("http_status"),
            }
            for r in artifacts.get("control_rows") or []
        ],
    }
    (fixture / "canary_result.json").write_text(json.dumps(slim, indent=2, default=str) + "\n")


async def main() -> int:
    ws = _workspaces()
    canary = ws["canary_workspace_id"]
    control = ws["control_workspace_id"]
    bearer = _mint_bearer()
    pdf = make_multiline_pdf(PAGES)
    pre = await _health()
    allow_before = _allowlist_state()
    artifacts: dict = {
        "gate": "D1",
        "base": BASE,
        "production_sha": pre["health"].get("version"),
        "deployment_id": DEPLOYMENT_ID,
        "pre_health": {
            "version": pre["health"].get("version"),
            "db": pre["health"].get("checks", {}).get("database"),
            "migration_head": pre["ready"].get("migration_head"),
        },
        "canary_workspace_id": canary,
        "control_workspace_id": control,
        "allowlist_before": {
            "flag": allow_before["flag"],
            "allowlist_uuid_count": allow_before["allowlist_uuid_count"],
            "allowlist_matches_canary": allow_before["allowlist"] == canary,
        },
    }
    if pre["health"].get("version") != EXPECTED_SHA:
        artifacts["blocked_reason"] = f"SHA drift: {pre['health'].get('version')}"
        artifacts["decision"] = decide_gate_d1(artifacts)
        _write_outputs(artifacts)
        print(json.dumps(artifacts["decision"], indent=2))
        return 2
    if allow_before["flag"] not in {"on", "true", "1", "yes"} or allow_before["allowlist"] != canary:
        artifacts["blocked_reason"] = "canary allowlist not isolated"
        artifacts["decision"] = decide_gate_d1(artifacts)
        _write_outputs(artifacts)
        print(json.dumps(artifacts["decision"], indent=2))
        return 2

    canary_file_id = None
    control_file_id = None
    canary_threads: list[str] = []
    control_threads: list[str] = []
    try:
        async with httpx.AsyncClient() as cx:
            await get_project(cx, bearer, canary)
            await get_project(cx, bearer, control)
            canary_file = await upload_gold(cx, bearer, canary, pdf)
            control_file = await upload_gold(cx, bearer, control, pdf)
            canary_file_id = canary_file.get("id")
            control_file_id = control_file.get("id")
            artifacts["canary_file"] = {
                "id": canary_file.get("id"),
                "status": canary_file.get("status"),
                "index_status": canary_file.get("index_status"),
                "indexed_chunk_count": canary_file.get("indexed_chunk_count"),
            }
            artifacts["control_file"] = {
                "id": control_file.get("id"),
                "status": control_file.get("status"),
            }
            canary_ready = await wait_ready(cx, bearer, canary, canary_file["id"])
            control_ready = await wait_ready(cx, bearer, control, control_file["id"])
            artifacts["canary_ready"] = {
                "status": canary_ready.get("status"),
                "index_status": canary_ready.get("index_status"),
                "indexed_chunk_count": canary_ready.get("indexed_chunk_count"),
                "page_count": canary_ready.get("page_count"),
                "extraction_status": canary_ready.get("extraction_status"),
                "has_extracted_text": canary_ready.get("has_extracted_text"),
            }
            artifacts["control_ready"] = {
                "status": control_ready.get("status"),
                "index_status": control_ready.get("index_status"),
                "indexed_chunk_count": control_ready.get("indexed_chunk_count"),
                "page_count": control_ready.get("page_count"),
            }

            canary_rows, canary_threads = await _run_questions(cx, bearer, canary, QUESTIONS)
            probe = [
                next(q for q in QUESTIONS if q["question_id"] == "M01"),
                next(q for q in QUESTIONS if q["question_id"] == "M23"),
                next(q for q in QUESTIONS if q["question_id"] == "M44"),
            ]
            control_rows, control_threads = await _run_questions(cx, bearer, control, probe)

            fb_status, fb_done, fb_latency = await chat_stream(
                cx, bearer, message=ABSENT_QUERY, project_id=canary
            )
            fallback = {
                "http_status": fb_status,
                "retrieval_mode": fb_done.get("retrieval_mode"),
                "fallback_reason": fb_done.get("fallback_reason"),
                "chunks_selected": fb_done.get("chunks_selected"),
                "error_500": fb_status >= 500,
                "latency_ms": fb_latency,
            }
            if fb_done.get("thread_id"):
                canary_threads.append(fb_done["thread_id"])

            pollution = False
            file_ids_ok = True
            canary_fid = str(canary_file["id"])
            unique_threads = list(dict.fromkeys(canary_threads))
            for tid in unique_threads[:5]:
                detail = await get_thread(cx, bearer, tid)
                if thread_polluted(detail):
                    pollution = True
            for row in canary_rows:
                ids = {str(x) for x in (row.get("source_ids") or [])}
                if row.get("used_files") and canary_fid not in ids and canary_fid not in {
                    str(u.get("id") if isinstance(u, dict) else u) for u in row.get("used_files") or []
                }:
                    file_ids_ok = False

            add_opinion = None
            first = next((r for r in canary_rows if r.get("thread_id") and r.get("sqlite_assistant_id")), None)
            if first:
                r = await cx.post(
                    f"{BASE}/api/threads/{first['thread_id']}/adhoc/expert/stream",
                    headers={"Authorization": bearer, "Content-Type": "application/json"},
                    json={
                        "session_id": str(uuid.uuid4()),
                        "provider_id": "claude",
                        "anchor_message_id": first["sqlite_assistant_id"],
                        "project_id": canary,
                    },
                    timeout=90.0,
                )
                add_opinion = {"http_status": r.status_code}
            council = await cx.post(
                f"{BASE}/council",
                headers={"Authorization": bearer, "Content-Type": "application/json"},
                json={},
                timeout=20.0,
            )

            c_del, c_gone = await delete_file(cx, bearer, canary, canary_file["id"])
            n_del, n_gone = await delete_file(cx, bearer, control, control_file["id"])
            thread_cleanup = []
            for tid in dict.fromkeys([*canary_threads, *control_threads]):
                dr = await cx.delete(
                    f"{BASE}/api/threads/{tid}",
                    headers={"Authorization": bearer},
                    timeout=30.0,
                )
                thread_cleanup.append({"id": tid, "status": dr.status_code})

        extra = {
            "status": "measured",
            "retrieval_kind": "fts",
            "processes_entire_pdf": False,
            "performs_retrieval": True,
            "retrieved_chunks_observable": True,
            "citations_observable": True,
        }
        canary_summary = summarize_mode("PROD_CANARY_FTS", canary_rows, extra=extra)
        control_summary = summarize_mode("PROD_CONTROL_PREFIX", control_rows, extra={"status": "measured"})
        used_chunks = any(r.get("chunk_ids") for r in canary_rows)
        modes = {r.get("retrieval_mode") for r in canary_rows}
        control_modes = {r.get("retrieval_mode") for r in control_rows}
        non_canary = (
            "PASS"
            if not (control_modes & FTS_MODES) and not any(r.get("chunk_ids") for r in control_rows)
            else "FAIL"
        )
        artifacts.update(
            {
                "canary_summary": canary_summary,
                "control_summary": control_summary,
                "canary_rows": canary_rows,
                "control_rows": control_rows,
                "fallback": fallback,
                "fts_actually_used": used_chunks and bool(modes & FTS_MODES),
                "retrieval_modes_observed": sorted(m for m in modes if m),
                "control_retrieval_modes": sorted(m for m in control_modes if m),
                "add_opinion_smoke": add_opinion,
                "council_smoke": {"http_status": council.status_code},
                "transcript_pollution": "FAIL" if pollution else "PASS",
                "file_ids_preserved": file_ids_ok,
                "non_canary_unaffected": non_canary,
                "cleanup": {
                    "canary_delete": c_del,
                    "canary_get_after": c_gone,
                    "control_delete": n_del,
                    "control_get_after": n_gone,
                    "threads": [{"status": t["status"]} for t in thread_cleanup],
                    "thread_count": len(thread_cleanup),
                },
            }
        )
    except Exception as exc:
        artifacts["blocked_reason"] = f"{type(exc).__name__}: {exc}"
        if canary_file_id or control_file_id:
            try:
                async with httpx.AsyncClient() as cx:
                    if canary_file_id:
                        await delete_file(cx, bearer, canary, canary_file_id)
                    if control_file_id:
                        await delete_file(cx, bearer, control, control_file_id)
            except Exception:
                pass
    finally:
        try:
            revert = _revert_canary_vars()
            artifacts["allowlist_revert"] = revert
            # Variable delete triggers a deploy; wait until the names are gone.
            deadline = time.time() + 180
            after = _allowlist_state()
            while time.time() < deadline and (
                after["flag"] is not None or after["allowlist"] is not None
            ):
                await asyncio.sleep(5)
                after = _allowlist_state()
            artifacts["allowlist_after_revert"] = {
                "flag": after["flag"],
                "allowlist_uuid_count": after["allowlist_uuid_count"],
            }
            artifacts["allowlist_reverted"] = after["flag"] is None and after["allowlist"] is None
        except Exception as exc:
            artifacts["allowlist_reverted"] = False
            artifacts["allowlist_revert_error"] = type(exc).__name__

    artifacts["decision"] = decide_gate_d1(artifacts)
    _write_outputs(artifacts)
    print(
        json.dumps(
            {
                "decision": artifacts.get("decision"),
                "canary_summary": artifacts.get("canary_summary"),
                "control_summary": artifacts.get("control_summary"),
                "fallback": artifacts.get("fallback"),
                "fts_actually_used": artifacts.get("fts_actually_used"),
                "retrieval_modes_observed": artifacts.get("retrieval_modes_observed"),
                "non_canary_unaffected": artifacts.get("non_canary_unaffected"),
                "cleanup": artifacts.get("cleanup"),
                "transcript_pollution": artifacts.get("transcript_pollution"),
                "allowlist_reverted": artifacts.get("allowlist_reverted"),
            },
            indent=2,
            default=str,
        )
    )
    return 0 if artifacts.get("decision", {}).get("status") in {"PASS", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
