#!/usr/bin/env python3
"""
Patch 09: P8 — Don't save API errors to conversation history.

Root cause: When Claude API returns 529 Overloaded (or any error),
handlers.py stores "Помилка Claude API: {e}" as assistant message
in conversation. This pollutes context — Claude reads its own
error messages as "previous responses".

Fix: Skip add_conv("assistant", reply) when reply starts with "Помилка".
Still save user message. Applied to 5 code paths:
1. Main reactive handler (add_conv_with_memory)
2. /analyze command
3. /deep command
4. /review command
5. Voice handler

Also adds cache usage logging to call_reactive() (P2 diagnostic).
"""

import sys
import os

DRY_RUN = "--dry-run" in sys.argv
BASE = os.environ.get("BOT_DIR", "/opt/smc-trader-v3")
HANDLERS = os.path.join(BASE, "bot/transport/handlers.py")
CORE = os.path.join(BASE, "bot/agent/core.py")

changes = []

# ═══════════════════════════════════════════════════════════
# Change 1: Main reactive handler — skip conv save for errors
# ═══════════════════════════════════════════════════════════
changes.append(
    {
        "file": HANDLERS,
        "label": "P8-main-reactive: skip error conv save",
        "old": """\
    # Module 1: use memory-aware add_conv — summarizes old messages before trim 
    await deps.state.add_conv_with_memory(
        "user", user_content, deps.claude, cfg.agent.model_utility
    )
    await deps.state.add_conv_with_memory(
        "assistant", reply, deps.claude, cfg.agent.model_utility
    )""",
        "new": """\
    # Module 1: use memory-aware add_conv — summarizes old messages before trim 
    # P8 fix: don't save API errors to conversation (pollutes Claude context)
    _is_error_reply = reply and reply.startswith("Помилка")
    await deps.state.add_conv_with_memory(
        "user", user_content, deps.claude, cfg.agent.model_utility
    )
    if not _is_error_reply:
        await deps.state.add_conv_with_memory(
            "assistant", reply, deps.claude, cfg.agent.model_utility
        )
    else:
        _log.warning("P8: skipped saving error reply to conversation (%d chars)", len(reply))""",
    }
)

# ═══════════════════════════════════════════════════════════
# Change 2: /analyze — skip conv save for errors
# ═══════════════════════════════════════════════════════════
changes.append(
    {
        "file": HANDLERS,
        "label": "P8-analyze: skip error conv save",
        "old": """\
        deps.state.add_conv("user", "/analyze")
        deps.state.add_conv("assistant", reply)
        await send_safe(bot, msg.chat.id, reply, reply_to=msg)""",
        "new": """\
        deps.state.add_conv("user", "/analyze")
        if not reply.startswith("Помилка"):
            deps.state.add_conv("assistant", reply)
        await send_safe(bot, msg.chat.id, reply, reply_to=msg)""",
    }
)

# ═══════════════════════════════════════════════════════════
# Change 3: /deep — skip conv save for errors
# ═══════════════════════════════════════════════════════════
changes.append(
    {
        "file": HANDLERS,
        "label": "P8-deep: skip error conv save",
        "old": """\
        deps.state.add_conv("user", "/deep")
        deps.state.add_conv("assistant", reply)
        if deps.claude and ctx:""",
        "new": """\
        deps.state.add_conv("user", "/deep")
        if not reply.startswith("Помилка"):
            deps.state.add_conv("assistant", reply)
        if deps.claude and ctx:""",
    }
)

# ═══════════════════════════════════════════════════════════
# Change 4: /review — skip conv save for errors
# ═══════════════════════════════════════════════════════════
changes.append(
    {
        "file": HANDLERS,
        "label": "P8-review: skip error conv save",
        "old": """\
        deps.state.add_conv("user", "/review")
        deps.state.add_conv("assistant", reply)
        await send_safe(bot, msg.chat.id, reply, reply_to=msg)""",
        "new": """\
        deps.state.add_conv("user", "/review")
        if not reply.startswith("Помилка"):
            deps.state.add_conv("assistant", reply)
        await send_safe(bot, msg.chat.id, reply, reply_to=msg)""",
    }
)

# ═══════════════════════════════════════════════════════════
# Change 5: Voice handler — skip conv save for errors
# ═══════════════════════════════════════════════════════════
changes.append(
    {
        "file": HANDLERS,
        "label": "P8-voice: skip error conv save",
        "old": """\
        # Save conversation
        deps.state.add_conv("user", f"🎙 {text}")
        deps.state.add_conv("assistant", reply)

        # 4. Decide reply mode""",
        "new": """\
        # Save conversation (P8: skip API error replies)
        deps.state.add_conv("user", f"🎙 {text}")
        if not reply.startswith("Помилка"):
            deps.state.add_conv("assistant", reply)

        # 4. Decide reply mode""",
    }
)

# ═══════════════════════════════════════════════════════════
# Change 6: P2 — Add cache usage logging to call_reactive
# ═══════════════════════════════════════════════════════════
changes.append(
    {
        "file": CORE,
        "label": "P2-reactive-cache-logging",
        "old": """\
    # Detect truncation — stop_reason=="max_tokens" means response was cut mid-sentence
    if getattr(resp, "stop_reason", None) == "max_tokens":
        out_tok = getattr(last_usage, "output_tokens", "?") if last_usage else "?"
        _log.warning(
            "TRUNCATED_REACTIVE model=%s output_tokens=%s — response cut mid-sentence",
            model, out_tok,
        )

    # Parse response: extract text + tool_use (skip thinking blocks)""",
        "new": """\
    # Log usage stats for reactive calls (P2 diagnostic: verify cache behavior)
    if last_usage:
        _cache_read = getattr(last_usage, "cache_read_input_tokens", 0) or 0
        _cache_create = getattr(last_usage, "cache_creation_input_tokens", 0) or 0
        _input_tok = getattr(last_usage, "input_tokens", 0) or 0
        _output_tok = getattr(last_usage, "output_tokens", 0) or 0
        _cache_pct = (_cache_read / _input_tok * 100) if _input_tok > 0 else 0
        _log.info(
            "Reactive API usage: in=%d out=%d cache_read=%d cache_create=%d (%.0f%% cached) model=%s",
            _input_tok, _output_tok, _cache_read, _cache_create, _cache_pct, model,
        )

    # Detect truncation — stop_reason=="max_tokens" means response was cut mid-sentence
    if getattr(resp, "stop_reason", None) == "max_tokens":
        out_tok = getattr(last_usage, "output_tokens", "?") if last_usage else "?"
        _log.warning(
            "TRUNCATED_REACTIVE model=%s output_tokens=%s — response cut mid-sentence",
            model, out_tok,
        )

    # Parse response: extract text + tool_use (skip thinking blocks)""",
    }
)


def apply():
    import shutil

    ok = 0
    fail = 0
    for c in changes:
        path = c["file"]
        label = c["label"]
        if not os.path.exists(path):
            print(f"  SKIP {label}: file not found {path}")
            fail += 1
            continue
        text = open(path, "r", encoding="utf-8").read()
        if c["old"] not in text:
            # Try to match by stripping trailing whitespace
            lines_old = [l.rstrip() for l in c["old"].split("\n")]
            lines_text = [l.rstrip() for l in text.split("\n")]
            old_joined = "\n".join(lines_old)
            text_joined = "\n".join(lines_text)
            if old_joined in text_joined:
                # Do replacement on stripped version, then reconstitute
                text = text_joined.replace(
                    old_joined, "\n".join([l.rstrip() for l in c["new"].split("\n")])
                )
                text += "\n"  # ensure trailing newline
            else:
                print(f"  FAIL {label}: pattern not found")
                # Show first 80 chars of expected pattern
                print(f"       Expected: {c['old'][:80]}...")
                fail += 1
                continue
        else:
            text = text.replace(c["old"], c["new"], 1)

        if DRY_RUN:
            print(f"  DRY-RUN OK {label}")
        else:
            bak = path + ".bak09"
            if not os.path.exists(bak):
                shutil.copy2(path, bak)
            open(path, "w", encoding="utf-8").write(text)
            print(f"  APPLIED {label}")
        ok += 1

    print(f"\nTotal: {ok} ok, {fail} fail (dry_run={DRY_RUN})")
    return fail == 0


if __name__ == "__main__":
    if DRY_RUN:
        print("=== DRY RUN ===")
    else:
        print("=== APPLYING ===")
    success = apply()
    sys.exit(0 if success else 1)
