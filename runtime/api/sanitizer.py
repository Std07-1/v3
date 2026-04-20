"""Server-side input sanitizer (ADR-0052 S8, threats T1 + T5).

Two public surfaces:

* :func:`sanitize_message` — user chat text before XADD to Redis. Strips
  control characters, nukes dangerous HTML tags (``<script>``, ``<iframe>``,
  event handlers), caps length. Returns both the cleaned text and a *flags*
  dict enumerating what was stripped — callers MUST forward those flags to
  ``audit.log_event`` so the degradation is loud (I7), never silent.

* :func:`sanitize_handoff` — handoff payload shipped from Feed/Thinking/Mind/
  Relationship/Logs into the prompt. Whitelists the ``source``, caps the
  ``prompt`` length, strips control chars. A rejected source returns
  ``(None, flags)`` so the caller can surface a banner instead of forwarding
  a hostile payload into Claude (T5 prompt injection).

All checks are pure and side-effect-free; the only I/O is whatever the
caller does with the returned flags.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# Control chars: 0x00–0x08, 0x0B, 0x0C, 0x0E–0x1F, 0x7F. Keep \t (09) \n (0A) \r (0D).
_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
# Dangerous tag blocks (case-insensitive, multi-line). We *nuke the contents*
# for <script>/<style> so smuggled JS can't survive the stripping pass.
_SCRIPT_STYLE_RE = re.compile(
    r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_TAG_RE = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|link|meta|form|style|svg|math)\b[^>]*>",
    re.IGNORECASE,
)
# javascript:/vbscript: urls + on*= event handlers + data: (non-image).
_EVENT_HANDLER_RE = re.compile(r"\son[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_JS_URI_RE = re.compile(r"(?:javascript|vbscript|data)\s*:", re.IGNORECASE)

_DEFAULT_SOURCES = frozenset({"feed", "thinking", "relationship", "mind", "logs"})


@dataclass(frozen=True)
class SanitizerConfig:
    enabled: bool = False
    max_message_length: int = 4000
    max_handoff_prompt: int = 500
    allowed_handoff_sources: frozenset[str] = field(default_factory=lambda: _DEFAULT_SOURCES)

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "SanitizerConfig":
        srcs = m.get("allowed_handoff_sources")
        return cls(
            enabled=bool(m.get("enabled", False)),
            max_message_length=int(m.get("max_message_length", 4000)),
            max_handoff_prompt=int(m.get("max_handoff_prompt", 500)),
            allowed_handoff_sources=(
                frozenset(str(s) for s in srcs) if srcs else _DEFAULT_SOURCES
            ),
        )


def _strip_control_chars(text: str) -> tuple[str, int]:
    """Return ``(clean, stripped_count)``."""
    if not text:
        return (text, 0)
    stripped = _CTRL_RE.sub("", text)
    return (stripped, len(text) - len(stripped))


def _strip_html(text: str) -> tuple[str, dict[str, int]]:
    """Nuke dangerous constructs. Returns ``(clean, counters)``."""
    flags = {"script_blocks": 0, "dangerous_tags": 0, "event_handlers": 0, "js_uris": 0}
    cleaned, n = _SCRIPT_STYLE_RE.subn("", text)
    flags["script_blocks"] = n
    cleaned, n = _DANGEROUS_TAG_RE.subn("", cleaned)
    flags["dangerous_tags"] = n
    cleaned, n = _EVENT_HANDLER_RE.subn("", cleaned)
    flags["event_handlers"] = n
    cleaned, n = _JS_URI_RE.subn("", cleaned)
    flags["js_uris"] = n
    return (cleaned, flags)


def sanitize_message(text: str, cfg: SanitizerConfig) -> tuple[str, dict[str, Any]]:
    """Clean user-typed chat text. Returns ``(clean_text, flags)``."""
    flags: dict[str, Any] = {"length_original": len(text), "truncated": False}
    if not cfg.enabled:
        flags["disabled"] = True
        return (text, flags)
    cleaned, ctrl_stripped = _strip_control_chars(text)
    flags["control_chars_stripped"] = ctrl_stripped
    cleaned, html_flags = _strip_html(cleaned)
    flags.update(html_flags)
    if len(cleaned) > cfg.max_message_length:
        cleaned = cleaned[: cfg.max_message_length]
        flags["truncated"] = True
    flags["length_clean"] = len(cleaned)
    return (cleaned, flags)


def sanitize_handoff(
    source: str, prompt: str, cfg: SanitizerConfig
) -> tuple[str | None, dict[str, Any]]:
    """Validate + clean a handoff payload before it reaches the prompt.

    Returns ``(clean_prompt, flags)``. If ``source`` is not whitelisted the
    first tuple element is ``None`` and ``flags['rejected_source']`` carries
    the offending value — callers surface a banner and drop the handoff.
    """
    flags: dict[str, Any] = {"source": source, "length_original": len(prompt)}
    if not cfg.enabled:
        flags["disabled"] = True
        return (prompt, flags)
    if source not in cfg.allowed_handoff_sources:
        flags["rejected_source"] = source
        return (None, flags)
    cleaned, ctrl_stripped = _strip_control_chars(prompt)
    flags["control_chars_stripped"] = ctrl_stripped
    if len(cleaned) > cfg.max_handoff_prompt:
        cleaned = cleaned[: cfg.max_handoff_prompt]
        flags["truncated"] = True
    flags["length_clean"] = len(cleaned)
    return (cleaned, flags)
