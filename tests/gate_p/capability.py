"""Capability audit against BEN adapters + current official provider docs.

This module never calls paid APIs. Live credential probes belong in providers.py.
"""
from __future__ import annotations

import inspect
import os
from typing import Any

from services.providers.anthropic_provider import AnthropicProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.model_registry import resolve_api_model
from services.providers.openai_provider import OpenAIProvider
from services.providers.vision_input import (
    VISION_MEDIA_TYPES,
    anthropic_user_content,
    gemini_user_parts,
    openai_user_content,
)
from services.providers.xai_provider import XAIProvider
from services.workspace_files.chunk_retriever import chunk_retrieval_enabled
from services.workspace_files.file_resolver import PER_FILE_MAX_CHARS


def _key_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _ben_pdf_in_adapter() -> bool:
    src = "\n".join(
        [
            inspect.getsource(openai_user_content),
            inspect.getsource(anthropic_user_content),
            inspect.getsource(gemini_user_parts),
        ]
    )
    return "application/pdf" in src or "input_file" in src or '"document"' in src


def capability_matrix() -> list[dict[str, Any]]:
    pdf_in_ben = _ben_pdf_in_adapter()
    openai_key = _key_present("OPENAI_API_KEY")
    anthropic_key = _key_present("ANTHROPIC_API_KEY")
    google_key = _key_present("GOOGLE_API_KEY") or _key_present("GEMINI_API_KEY")
    xai_key = _key_present("XAI_API_KEY")
    openai_chat = resolve_api_model("openai", "gpt-5.5-instant")
    openai_reason = resolve_api_model("openai", "gpt-5.5-pro")
    claude = resolve_api_model("anthropic", "claude-sonnet-4.6")
    gemini = resolve_api_model("google", "gemini-3.5-flash")
    grok = resolve_api_model("xai", "grok-4.6")

    return [
        {
            "provider": "OPENAI",
            "current_ben_integration": (
                f"OpenAIProvider Chat Completions only ({OpenAIProvider().provider_name}); "
                f"canonical gpt-5.5-instant -> API {openai_chat}; "
                f"gpt-5.5-pro -> API {openai_reason}; "
                f"multimodal parts are {sorted(VISION_MEDIA_TYPES)}; "
                f"no Files API, no Responses API, no file_search tool in adapter."
            ),
            "direct_pdf_document_input": (
                "YES at provider API (Responses input_file / Chat Completions type=file). "
                f"NO in current BEN adapter (pdf_in_ben={pdf_in_ben})."
            ),
            "provider_retrieval_file_search": (
                "YES at provider API (Responses file_search + vector store). "
                "NO in current BEN adapter. Requires creating a vector store."
            ),
            "citation_evidence_support": (
                "File Search annotations/search_results can be requested via include=. "
                "Native input_file does not expose retrieved chunks."
            ),
            "existing_credentials_usable": openai_key,
            "additional_setup_required": (
                "Native PDF: isolated Responses call only. "
                "File Search: ephemeral vector store + file upload (not present in BEN)."
            ),
            "safe_to_benchmark_now": bool(openai_key),
            "blocked_reason": None if openai_key else "OPENAI_API_KEY not injected in this environment",
        },
        {
            "provider": "ANTHROPIC / CLAUDE",
            "current_ben_integration": (
                f"AnthropicProvider Messages API ({AnthropicProvider().provider_name}); "
                f"canonical claude-sonnet-4.6 / claude-opus-4.8 -> API {claude}; "
                "image blocks only; production ANTHROPIC_CHAT_MAX_TOKENS default 1024; "
                "no document blocks, no Files API beta header."
            ),
            "direct_pdf_document_input": (
                "YES at provider API (Messages document source base64/url/file_id, "
                "citations.enabled). NO in current BEN adapter."
            ),
            "provider_retrieval_file_search": (
                "NO applicable API-level retrieval product. Files API is upload/reuse only. "
                "Project RAG exists on claude.ai consumer projects, not the Messages API. "
                "CLAUDE_PROVIDER_RETRIEVAL is not an available API mode."
            ),
            "citation_evidence_support": (
                "YES for native PDF when citations.enabled=true (page_location + cited_text)."
            ),
            "existing_credentials_usable": anthropic_key,
            "additional_setup_required": "Isolated Messages document block; no extra infra.",
            "safe_to_benchmark_now": bool(anthropic_key),
            "blocked_reason": None if anthropic_key else "ANTHROPIC_API_KEY not injected in this environment",
        },
        {
            "provider": "GOOGLE / GEMINI",
            "current_ben_integration": (
                f"GeminiProvider generateContent ({GeminiProvider().provider_name}); "
                f"canonical gemini-3.5-flash -> API {gemini}; "
                "inlineData images only; no Files API upload, no File Search store."
            ),
            "direct_pdf_document_input": (
                "YES at provider API (inlineData application/pdf or Files API file_data). "
                "NO in current BEN adapter."
            ),
            "provider_retrieval_file_search": (
                "YES at provider API (File Search store + fileSearch tool). "
                "NO in current BEN adapter. Requires creating a File Search store."
            ),
            "citation_evidence_support": (
                "File Search can return page_number on retrieved_context / grounding. "
                "Native PDF does not expose retrieved chunks."
            ),
            "existing_credentials_usable": google_key,
            "additional_setup_required": (
                "Native PDF: isolated generateContent inline PDF. "
                "File Search: File Search store + import (not present in BEN)."
            ),
            "safe_to_benchmark_now": bool(google_key),
            "blocked_reason": None
            if google_key
            else "GOOGLE_API_KEY / GEMINI_API_KEY not injected in this environment",
        },
        {
            "provider": "GROK / XAI",
            "current_ben_integration": (
                f"XAIProvider Chat Completions ({XAIProvider().provider_name}); "
                f"canonical grok-4.6 -> API {grok}; "
                "OpenAI-compatible image_url only; search/tools omitted (HTTP 410). "
                "No Files API, no Responses attachment_search."
            ),
            "direct_pdf_document_input": (
                "YES at provider API (Responses input_file file_id/file_url; "
                "agentic attachment_search on grok-4.6). "
                "Chat Completions used by BEN does not accept PDF parts. "
                "NO in current BEN adapter."
            ),
            "provider_retrieval_file_search": (
                "Implicit attachment_search when files are attached on Responses. "
                "Collections/semantic search exist on Files API. "
                "Neither is wired in BEN."
            ),
            "citation_evidence_support": (
                "attachment_search output can be requested; not exposed by BEN adapter."
            ),
            "existing_credentials_usable": xai_key,
            "additional_setup_required": "Isolated Files upload + Responses API.",
            "safe_to_benchmark_now": bool(xai_key),
            "blocked_reason": None if xai_key else "XAI_API_KEY not injected in this environment",
        },
        {
            "provider": "BEN local (prefix / FTS)",
            "current_ben_integration": (
                f"Gate 3D prefix_fallback PER_FILE_MAX_CHARS={PER_FILE_MAX_CHARS}; "
                f"Gate 4A chunk_retrieval_enabled default={chunk_retrieval_enabled('00000000-0000-0000-0000-000000000000')}; "
                "no embeddings, no vector DB, no provider file tools."
            ),
            "direct_pdf_document_input": "Local pypdf extraction then 2000-char prefix.",
            "provider_retrieval_file_search": (
                "Local Postgres chunk FTS exists behind allowlist flag (default OFF)."
            ),
            "citation_evidence_support": (
                "Prefix path: page numbers not on evidence units. "
                "FTS path: chunk page attributes are observable."
            ),
            "existing_credentials_usable": True,
            "additional_setup_required": (
                "FTS live Postgres path needs DB + flag+allowlist. "
                "Isolated lexical simulation needs no extra setup."
            ),
            "safe_to_benchmark_now": True,
            "blocked_reason": None,
        },
    ]


def injected_secret_names() -> list[str]:
    raw = os.getenv("CLOUD_AGENT_INJECTED_SECRET_NAMES") or os.getenv("CLOUD_AGENT_ALL_SECRET_NAMES") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]
