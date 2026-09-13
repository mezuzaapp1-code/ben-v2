"""Capability audit for Gate P. Does not print secret values."""
from __future__ import annotations

import os
from typing import Any

from services.providers.anthropic_provider import ANTHROPIC_FAST_MODEL, ANTHROPIC_FLAGSHIP_MODEL
from services.providers.gemini_provider import GEMINI_FAST_MODEL
from services.providers.openai_provider import OPENAI_CHAT_FAST_MODEL, OPENAI_REASONING_MODEL
from services.providers.vision_input import VISION_MEDIA_TYPES
from services.providers.xai_provider import XAI_FAST_MODEL, XAI_FLAGSHIP_MODEL

KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
}


def key_present(provider: str) -> bool:
    env = KEY_ENV[provider]
    return bool(os.getenv(env, "").strip())


def key_present_any(*names: str) -> bool:
    return any(bool(os.getenv(n, "").strip()) for n in names)


def capability_matrix() -> list[dict[str, Any]]:
    openai_key = key_present("openai")
    anthropic_key = key_present("anthropic")
    google_key = key_present("google")
    xai_key = key_present("xai")
    vision_only = sorted(VISION_MEDIA_TYPES)
    return [
        {
            "provider": "OpenAI",
            "current_ben_integration": (
                f"Chat Completions adapter ({OPENAI_CHAT_FAST_MODEL} / {OPENAI_REASONING_MODEL}); "
                f"multimodal user parts are images only ({vision_only}). "
                "No Responses API, no input_file, no file_search in BEN adapters."
            ),
            "direct_pdf_document_input": (
                "Provider API: yes via Responses input_file (PDF text+page images on vision models). "
                "BEN integration: no."
            ),
            "provider_retrieval_file_search": (
                "Provider API: yes, Responses file_search + vector stores. BEN integration: no."
            ),
            "citation_evidence_support": (
                "file_search annotations / include=file_search_call.results. "
                "Native input_file does not expose retrieved chunks."
            ),
            "existing_credentials_usable": openai_key,
            "additional_setup_required": (
                "None beyond OPENAI_API_KEY for isolated benchmark. "
                "File Search also creates a temporary vector store."
            ),
            "safe_to_benchmark_now": openai_key,
            "blocked_reason": None if openai_key else "OPENAI_API_KEY not present in this environment",
            "ben_models": {
                "fast": OPENAI_CHAT_FAST_MODEL,
                "reasoning": OPENAI_REASONING_MODEL,
            },
        },
        {
            "provider": "Anthropic / Claude",
            "current_ben_integration": (
                f"Messages adapter ({ANTHROPIC_FAST_MODEL} / {ANTHROPIC_FLAGSHIP_MODEL}); "
                "image blocks only. No document/PDF content blocks in BEN adapters."
            ),
            "direct_pdf_document_input": (
                "Provider API: yes, Messages document block (base64 / URL / Files API) with optional citations. "
                "BEN integration: no."
            ),
            "provider_retrieval_file_search": (
                "No hosted File Search product comparable to OpenAI/Gemini. "
                "Files API is storage for document blocks, not retrieval. "
                "CLAUDE_PROVIDER_RETRIEVAL is not an applicable provider capability."
            ),
            "citation_evidence_support": (
                "Native PDF citations (page-indexed) when citations.enabled=true on the document block."
            ),
            "existing_credentials_usable": anthropic_key,
            "additional_setup_required": (
                "None beyond ANTHROPIC_API_KEY for native document. Files API beta header only if uploading."
            ),
            "safe_to_benchmark_now": anthropic_key,
            "blocked_reason": None if anthropic_key else "ANTHROPIC_API_KEY not present in this environment",
            "ben_models": {
                "fast": ANTHROPIC_FAST_MODEL,
                "flagship": ANTHROPIC_FLAGSHIP_MODEL,
            },
        },
        {
            "provider": "Google / Gemini",
            "current_ben_integration": (
                f"generateContent adapter ({GEMINI_FAST_MODEL}); inlineData images only. "
                "application/pdf is not a BEN vision media type, so chat never sends PDFs."
            ),
            "direct_pdf_document_input": (
                "Provider API: yes, generateContent inlineData/file_data application/pdf (native document vision). "
                "BEN integration: no."
            ),
            "provider_retrieval_file_search": (
                "Provider API: yes, File Search stores + file_search tool. BEN integration: no."
            ),
            "citation_evidence_support": (
                "File Search grounding citations. Native PDF does not expose retrieved chunks."
            ),
            "existing_credentials_usable": google_key,
            "additional_setup_required": (
                "None beyond GOOGLE_API_KEY for native PDF. File Search creates a temporary store."
            ),
            "safe_to_benchmark_now": google_key,
            "blocked_reason": None if google_key else "GOOGLE_API_KEY not present in this environment",
            "ben_models": {"fast": GEMINI_FAST_MODEL},
        },
        {
            "provider": "Grok / xAI",
            "current_ben_integration": (
                f"Chat Completions adapter ({XAI_FAST_MODEL} / {XAI_FLAGSHIP_MODEL}); images only. "
                "xAI document attachments require the Responses API, which BEN does not call."
            ),
            "direct_pdf_document_input": (
                "Provider API: yes via Responses input_file (file_id or file_url), which activates attachment_search. "
                "BEN Chat Completions path: no."
            ),
            "provider_retrieval_file_search": (
                "Implicit attachment_search when files are attached on Responses. Not exposed on BEN's Chat Completions client."
            ),
            "citation_evidence_support": "Not documented as observable retrieved chunks on Chat Completions.",
            "existing_credentials_usable": xai_key,
            "additional_setup_required": "XAI_API_KEY plus Responses API (not BEN's current adapter).",
            "safe_to_benchmark_now": xai_key,
            "blocked_reason": None if xai_key else "XAI_API_KEY not present in this environment",
            "ben_models": {"fast": XAI_FAST_MODEL, "flagship": XAI_FLAGSHIP_MODEL},
        },
    ]


def ben_local_modes() -> dict[str, Any]:
    return {
        "BEN_PREFIX_2000": {
            "safe_to_benchmark_now": True,
            "notes": "Committed Gate M baseline. Does not rerun unless requested.",
        },
        "BEN_EXISTING_FTS": {
            "safe_to_benchmark_now": True,
            "notes": (
                "Isolated process + throwaway workspace allowlist. "
                "Does not set production flags. Requires local Postgres chunk schema."
            ),
        },
    }
