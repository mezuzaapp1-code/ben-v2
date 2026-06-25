"""Project onboarding agent — OpenAI tool-calling loop with local filesystem tools."""

from __future__ import annotations



import json

import os

import uuid

from collections.abc import AsyncIterator

from typing import Any



import httpx



from database.thread_store import get_thread_metadata

from services.knowledge_store import build_multi_head_prompt_context

from services.ben_log_service import append_event

from services.chat_prompt import GLOBAL_CHAT_SYSTEM

from services.project_tool_router import conditional_project_tools

from services.project_tools import (

    TOOL_TELEMETRY_LABELS,

    execute_project_agent_tool,

)

from services.providers.openai_provider import OPENAI_CHAT_FAST_MODEL



PROJECT_SETUP_SYSTEM = (

    "[System: The user is initiating an interactive new project setup inside BEN's workspace. "

    "Act as an expert operational director. Greet the user in our centered 800px track, interview "

    "them briefly to discover the project's technical scope, and once they confirm parameters call "

    "initialize_project_files to provision data/projects/{slug}/ with specs/architecture.md and "

    "tasks/roadmap.md. Use create_project_directory or write_project_file only when incremental edits "

    "are needed after the initial provision.]"

)



PROJECT_SETUP_BOOTSTRAP_USER = (

    "Begin the interactive new-project setup interview. Greet the user warmly and ask focused "

    "questions to learn the project's name, goals, and technical scope."

)



_MAX_TOOL_ROUNDS = 6





def _stream_ndjson(payload: dict[str, Any]) -> str:

    return json.dumps(payload, ensure_ascii=False) + "\n"





def _openai_model() -> str:

    return (os.getenv("OPENAI_CHAT_FAST_MODEL") or OPENAI_CHAT_FAST_MODEL).strip()





def _tool_log_summary(tool_name: str, result_payload: dict[str, Any]) -> str:

    if result_payload.get("status") != "ok":

        return str(result_payload.get("message") or f"{tool_name} failed")

    if tool_name == "initialize_project_files":

        slug = result_payload.get("project_slug") or "project"

        return f"Project workspace initialized for {slug} with architecture and roadmap files."

    return str(result_payload.get("message") or f"{tool_name} completed successfully.")





async def _openai_chat_completion(

    *,

    messages: list[dict[str, Any]],

    tools: list[dict[str, Any]] | None,

    tenant_id: str,

) -> dict[str, Any]:

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()

    if not api_key:

        raise RuntimeError("OPENAI_API_KEY is not configured for project agent tools.")

    payload: dict[str, Any] = {

        "model": _openai_model(),

        "messages": messages,

    }

    if tools:

        payload["tools"] = tools

        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:

        response = await client.post(

            "https://api.openai.com/v1/chat/completions",

            headers={"Authorization": f"Bearer {api_key}"},

            json=payload,

        )

        response.raise_for_status()

        return response.json()





async def stream_project_agent_response(

    *,

    user_message: str,

    tenant_id: str,

    thread_id: uuid.UUID,

    bootstrap: bool = False,

    conversation_history: str | None = None,

) -> AsyncIterator[str]:

    """Run tool loop, emit telemetry, then stream the assistant's final visible reply."""

    org_id = uuid.UUID(tenant_id)

    tools = conditional_project_tools(thread_id=thread_id, provider_id="gpt")

    if not tools:

        yield _stream_ndjson({"type": "error", "message": "Project workspace tools unavailable for this thread."})

        return



    system_parts = [GLOBAL_CHAT_SYSTEM.strip(), PROJECT_SETUP_SYSTEM]

    if conversation_history:

        system_parts.append(f"Prior thread context:\n{conversation_history}")

    meta = get_thread_metadata(str(thread_id))
    project_slug = str((meta or {}).get("project_slug") or "").strip()
    if project_slug:
        hybrid_context = build_multi_head_prompt_context(
            project_slug,
            user_message or PROJECT_SETUP_BOOTSTRAP_USER,
            limit_per_head=3,
        )
        if hybrid_context.strip():
            system_parts.append(hybrid_context)

    system = "\n\n".join(part for part in system_parts if part)



    visible_user = PROJECT_SETUP_BOOTSTRAP_USER if bootstrap else (user_message or "").strip()

    if not visible_user:

        visible_user = PROJECT_SETUP_BOOTSTRAP_USER



    messages: list[dict[str, Any]] = [

        {"role": "system", "content": system},

        {"role": "user", "content": visible_user},

    ]



    yield _stream_ndjson(

        {

            "type": "meta",

            "thread_id": str(thread_id),

            "mode": "project_setup",

            "provider_id": "gpt",

            "tools_enabled": True,

            "tool_count": len(tools),

        }

    )



    tool_transcript: list[str] = []

    assistant_message: dict[str, Any] | None = None



    for _ in range(_MAX_TOOL_ROUNDS):

        data = await _openai_chat_completion(

            messages=messages,

            tools=tools,

            tenant_id=tenant_id,

        )

        choice = (data.get("choices") or [{}])[0]

        message = choice.get("message") or {}

        tool_calls = message.get("tool_calls") or []

        if not tool_calls:

            assistant_message = message

            break



        messages.append(message)

        for call in tool_calls:

            fn = call.get("function") or {}

            tool_name = str(fn.get("name") or "")

            raw_args = fn.get("arguments") or "{}"

            try:

                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)

            except json.JSONDecodeError:

                args = {}



            yield _stream_ndjson(

                {

                    "type": "tool_active",

                    "tool": tool_name,

                    "message": TOOL_TELEMETRY_LABELS.get(

                        tool_name,

                        f"⚙️ System: Executing {tool_name}...",

                    ),

                }

            )



            try:

                result = execute_project_agent_tool(tool_name, args)

            except Exception as exc:

                result = json.dumps({"status": "error", "message": str(exc)})



            try:

                result_payload = json.loads(result)

            except json.JSONDecodeError:

                result_payload = {"status": "error", "message": result}



            log_summary = _tool_log_summary(tool_name, result_payload)

            ben_log_id = await append_event(

                org_id=org_id,

                thread_id=thread_id,

                event_type="decision",

                summary=log_summary,

                source="system",

                provider="gpt",

                model=_openai_model(),

                payload={

                    "tool": tool_name,

                    "status": result_payload.get("status"),

                    "project_slug": result_payload.get("project_slug"),

                    "files": result_payload.get("files"),

                },

            )



            tool_transcript.append(f"{tool_name}: {result}")

            yield _stream_ndjson(

                {

                    "type": "tool_done",

                    "tool": tool_name,

                    "result": result,

                    "log_summary": log_summary,

                    "ben_log_id": str(ben_log_id) if ben_log_id else None,

                }

            )

            messages.append(

                {

                    "role": "tool",

                    "tool_call_id": call.get("id"),

                    "content": result,

                }

            )

    else:

        yield _stream_ndjson({"type": "error", "message": "Project agent tool loop exceeded safe limit."})

        return



    final_text = str((assistant_message or {}).get("content") or "").strip()

    if not final_text:

        if tool_transcript:

            final_text = (

                "Project workspace operations completed successfully. "

                "Review the provisioned folders and files, then tell me what to refine next."

            )

        else:

            final_text = "Project setup is ready. Tell me about the initiative you want to launch."



    chunk_size = 48

    for i in range(0, len(final_text), chunk_size):

        yield _stream_ndjson({"type": "chunk", "content": final_text[i : i + chunk_size]})



    yield _stream_ndjson(

        {

            "type": "done",

            "thread_id": str(thread_id),

            "response": final_text,

            "model_used": _openai_model(),

            "provider_id": "gpt",

            "provider_used": "openai",

            "cost_usd": 0.0,

            "mode": "project_setup",

        }

    )


