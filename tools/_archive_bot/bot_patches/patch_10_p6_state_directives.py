#!/usr/bin/env python3
"""
Patch 10: P6 — Fix /state command to read bias from directives.

Root cause: market_state.json bias_map is NEVER updated (update_from_context()
has zero callers). Bias_map migrated to directives (v3_agent_directives.json).
The /state command shows stale data from market_state.

Fix: /state reads bias_map, active_scenario, narrative_mode from directives
instead of the stale market_state.json.
"""

import sys
import os

DRY_RUN = "--dry-run" in sys.argv
BASE = os.environ.get("BOT_DIR", "/opt/smc-trader-v3")
HANDLERS = os.path.join(BASE, "bot/transport/handlers.py")

changes = []

# ═══════════════════════════════════════════════════════════
# Change 1: /state reads bias from directives
# ═══════════════════════════════════════════════════════════
changes.append(
    {
        "file": HANDLERS,
        "label": "P6-state-from-directives",
        "old": """\
    @dp.message(Command("state"))
    async def cmd_state(msg: Message) -> None:
        if not is_authorized(cfg, msg):
            return
        s = deps.state.load_state()
        bias = s.get("bias_map", {})
        bias_s = "  ".join(f"{k}:{v}" for k, v in bias.items()) if bias else "—"
        log = s.get("session_log", [])[-3:]
        log_s = (
            "\\n".join(
                f"  {datetime.fromtimestamp(e['ts'], timezone.utc).strftime('%H:%M')} "
                f"score={e['score']} mode={e['mode']}"
                for e in log
            )
            or "  (порожньо)"
        )
        await msg.answer(
            f"Ринковий стан:\\n"
            f"Bias: {bias_s}\\n"
            f"Narrative: {s.get('narrative_mode','?')}\\n"
            f"Trend: {s.get('trend_bias','?')}\\n"
            f"P/D: {s.get('pd_label','?')}\\n"
            f"Чекаю: {s.get('waiting_for','—')}\\n\\n"
            f"Session log:\\n{log_s}"
        )""",
        "new": """\
    @dp.message(Command("state"))
    async def cmd_state(msg: Message) -> None:
        if not is_authorized(cfg, msg):
            return
        # P6 fix: read live data from directives (SSOT), not stale market_state
        d = deps.directives_store.load()
        s = deps.state.load_state()
        # Bias from directives (updated every heartbeat via emit_directives)
        bias = d.bias_map if d else {}
        bias_s = "  ".join(f"{k}:{v}" for k, v in bias.items()) if bias else "—"
        # Scenario from directives
        scenario_s = "—"
        if d and d.active_scenario:
            sc = d.active_scenario
            scenario_s = f"{sc.id} (conf={sc.confidence})"
        # VP from directives
        vp_s = "—"
        if d and d.virtual_position:
            vp = d.virtual_position
            vp_s = f"{vp.direction} @ {vp.entry_price}"
        log = s.get("session_log", [])[-3:]
        log_s = (
            "\\n".join(
                f"  {datetime.fromtimestamp(e['ts'], timezone.utc).strftime('%H:%M')} "
                f"score={e['score']} mode={e['mode']}"
                for e in log
            )
            or "  (порожньо)"
        )
        await msg.answer(
            f"Ринковий стан (live з directives):\\n"
            f"Bias: {bias_s}\\n"
            f"Сценарій: {scenario_s}\\n"
            f"VP: {vp_s}\\n"
            f"Чекаю: {s.get('waiting_for','—')}\\n\\n"
            f"Session log:\\n{log_s}"
        )""",
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
        old = c["old"]
        new = c["new"]

        if old not in text:
            # Try stripping trailing whitespace
            lines_old = [l.rstrip() for l in old.split("\n")]
            lines_text = [l.rstrip() for l in text.split("\n")]
            old_joined = "\n".join(lines_old)
            text_joined = "\n".join(lines_text)
            if old_joined in text_joined:
                text = text_joined.replace(
                    old_joined, "\n".join([l.rstrip() for l in new.split("\n")])
                )
                text += "\n"
            else:
                print(f"  FAIL {label}: pattern not found")
                print(f"       Expected first line: {old.split(chr(10))[0][:80]}")
                fail += 1
                continue
        else:
            text = text.replace(old, new, 1)

        if DRY_RUN:
            print(f"  DRY-RUN OK {label}")
        else:
            bak = path + ".bak10"
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
