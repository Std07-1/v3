"""
Patch: Audit Inbox — deliver unread audit messages to Archi.

Adds CHECK 4.5 between external channel posts (CHECK 4) and heartbeat (CHECK 5).
When v3_audit_inbox.json has unread messages, fires an agent call with the audit
content as extra_prompt so Archi can read and reflect on it.

Also adds `import json` to imports (needed for audit inbox JSON parsing).
"""

from pathlib import Path

MONITOR = Path("/opt/smc-trader-v3/bot/scheduling/monitor.py")
text = MONITOR.read_text(encoding="utf-8")
lines = text.split("\n")

# ── Step 1: Add `import json` to imports ──
# Find the line with `import time` and add `import json` after it
json_import_added = False
for i, line in enumerate(lines):
    if line.strip() == "import time":
        if "import json" not in text:
            lines.insert(i + 1, "import json")
            json_import_added = True
            print(f"Added 'import json' after line {i + 1}")
        break

if not json_import_added and "import json" not in text:
    print("WARNING: Could not add 'import json' — adding at line 28")
    lines.insert(27, "import json")

# ── Step 2: Find insertion point — between CHECK 4 and CHECK 5 ──
# Look for the heartbeat comment
insert_idx = None
for i, line in enumerate(lines):
    if "CHECK 5: Heartbeat" in line:
        insert_idx = i
        break

if insert_idx is None:
    print("ERROR: Could not find CHECK 5 insertion point")
    exit(1)

print(f"Inserting CHECK 4.5 before line {insert_idx + 1} (CHECK 5)")

# ── Step 3: Build the audit inbox check code ──
AUDIT_CHECK = """
            # ─── CHECK 4.5: Audit inbox — deliver unread audits to Archi ────
            _audit_path = os.path.join(cfg.storage.data_dir, "v3_audit_inbox.json")
            if os.path.exists(_audit_path):
                try:
                    with open(_audit_path, "r", encoding="utf-8") as _af:
                        _audit_data = json.load(_af)
                    _unread = [m for m in _audit_data.get("messages", []) if not m.get("read")]
                    if _unread:
                        _amsg = _unread[0]  # one at a time
                        _log.info(
                            "AUDIT_INBOX: delivering id=%s from=%s subject=%s",
                            _amsg.get("id", "?"),
                            _amsg.get("from", "?"),
                            _amsg.get("subject", "?")[:60],
                        )
                        _audit_body = (
                            f"\\U0001f4cb ВХІДНЕ ПОВІДОМЛЕННЯ від {_amsg.get('from', 'невідомо')}:\\n"
                            f"Тема: {_amsg.get('subject', 'без теми')}\\n\\n"
                            f"{_amsg.get('body', '')}\\n\\n"
                            "---\\n"
                            "Прочитай уважно. Це аудит/повідомлення від архітектора. "
                            "Вислови свої думки, згоди, незгоди, висновки. "
                            "Якщо є конкретні пункти для покращення — постав собі план."
                        )
                        await _do_agent_call(
                            cfg,
                            client,
                            d,
                            directives_store,
                            state_manager,
                            bot,
                            send_safe_fn,
                            fetch_context_fn,
                            context_summary_fn,
                            wake_reason=f"audit_inbox:{_amsg.get('id', 'unknown')}",
                            extra_prompt=_audit_body,
                            current_price=current_price or 0.0,
                            personality_dna=personality_dna,
                            market_status=current_status,
                            full_prompt=full_prompt,
                            digest=digest,
                        )
                        # Mark as read
                        _amsg["read"] = True
                        _amsg["read_ts"] = time.time()
                        with open(_audit_path, "w", encoding="utf-8") as _af:
                            json.dump(_audit_data, _af, ensure_ascii=False, indent=2)
                        _log.info("AUDIT_INBOX: marked id=%s as read", _amsg.get("id"))
                        continue
                except Exception as _ae:
                    _log.warning("AUDIT_INBOX load error: %s", _ae)

"""

# ── Step 4: Insert the audit check code ──
audit_lines = AUDIT_CHECK.rstrip().split("\n")
new_lines = lines[:insert_idx] + audit_lines + [""] + lines[insert_idx:]
new_text = "\n".join(new_lines)

# ── Step 5: Write back ──
MONITOR.write_text(new_text, encoding="utf-8")
print(
    f"PATCH APPLIED: {len(audit_lines)} lines (CHECK 4.5 audit inbox) inserted before CHECK 5"
)
print("Archi will now read unread audit messages on the next monitor cycle.")
