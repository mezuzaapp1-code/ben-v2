"""Prompt templates for the Local Codebase Expert."""
from __future__ import annotations

from services.codebase_expert.retriever import CodeContextPack

S_CODEBASE = """You are the Local Codebase Expert for BEN-V2.
You reason ONLY from the provided repository excerpts — not general knowledge.
Identify where behavior lives (routes, services, stream pipeline), risks, and concrete next steps.
If excerpts are insufficient, say what file/path you would need.
Be direct. Max 3 sentences."""


def render_pack_for_prompt(pack: CodeContextPack) -> str:
    parts = [f"Architecture:\n{pack.architecture_blurb}\n"]
    for entry in pack.files:
        path = entry.get("path", "")
        lines = entry.get("lines", "")
        excerpt = entry.get("excerpt", "")
        parts.append(f"--- {path} (L{lines}) ---\n{excerpt}\n")
    return "\n".join(parts)
