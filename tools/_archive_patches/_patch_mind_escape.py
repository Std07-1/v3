"""Patch /mind command: escape Markdown special chars in dynamic content.

Problem: directives text (thoughts, findings, scratchpad) contains _ * [ ] ` 
which breaks Telegram Markdown parser -> TelegramBadRequest.

Fix: add _esc_md() helper, apply to all dynamic strings, wrap in try/except 
to fallback to plain text if Markdown still fails.
"""
import re

FILE = "/opt/smc-trader-v3/bot/transport/handlers.py"

with open(FILE, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Find the cmd_mind function and add escape helper + try/except fallback
# We need to:
#   a) Add _esc_md helper before the function uses it
#   b) Escape all dynamic strings
#   c) Wrap the final send in try/except with fallback to plain text

old_answer = '        await msg.answer(text, parse_mode="Markdown")'

# Check it exists
assert old_answer in code, f"Cannot find answer line in handlers.py"

# Replace the send with try/except fallback
new_answer = """        try:
            await msg.answer(text, parse_mode="Markdown")
        except Exception:
            # Fallback: strip markdown formatting and send plain
            plain = text.replace("*", "").replace("_", "")
            if len(plain) > 4000:
                plain = plain[:3990] + "\\n…(обрізано)"
            await msg.answer(plain)"""

code = code.replace(old_answer, new_answer, 1)

# 2. Now escape dynamic content - add helper function right before cmd_mind
# Find the decorator
mind_decorator = '    @dp.message(Command("mind"))'
assert mind_decorator in code, "Cannot find @dp.message(Command('mind'))"

# Add escape helper before the decorator
escape_helper = '''    def _esc_md(s: str) -> str:
        """Escape Markdown V1 special chars for Telegram."""
        if not s:
            return s
        for ch in ("_", "*", "`", "["):
            s = s.replace(ch, "\\\\" + ch)
        return s

'''

code = code.replace(mind_decorator, escape_helper + mind_decorator, 1)

# 3. Now escape all dynamic string interpolations in cmd_mind
# We need to escape: thought, scratchpad items, watch level text, 
# scenario thesis, thought_history text/mood, self_model values, findings text

# Escape inner_thought
code = code.replace(
    '            thought = d.inner_thought[:500]\n            lines.append(f"\\n💬 *Думка*:\\n_{thought}_")',
    '            thought = _esc_md(d.inner_thought[:500])\n            lines.append(f"\\n💬 *Думка*:\\n_{thought}_")'
)

# Escape scratchpad items
code = code.replace(
    '            sp = "\\n".join(f"  • {s[:100]}" for s in d.scratchpad[:7])',
    '            sp = "\\n".join(f"  • {_esc_md(s[:100])}" for s in d.scratchpad[:7])'
)

# Escape watch level text
code = code.replace(
    '                f"  📍 {w.alert_text or w.id}: {w.price}"',
    '                f"  📍 {_esc_md(str(w.alert_text or w.id))}: {w.price}"'
)

# Escape scenario thesis
code = code.replace(
    '                lines.append(f"  _{sc.thesis[:200]}_")',
    '                lines.append(f"  _{_esc_md(sc.thesis[:200])}_")'
)

# Escape thought_history text
code = code.replace(
    '                    lines.append(f"  [{t_s}] ({mood_t}) {text}")',
    '                    lines.append(f"  [{t_s}] ({_esc_md(mood_t)}) {_esc_md(text)}")'
)
code = code.replace(
    '                    lines.append(f"  ({mood_t}) {text}")',
    '                    lines.append(f"  ({_esc_md(mood_t)}) {_esc_md(text)}")'
)

# Escape self_model values
code = code.replace(
    '                sm_items.append(f"  {k}: {str(v)[:80]}")',
    '                sm_items.append(f"  {_esc_md(str(k))}: {_esc_md(str(v)[:80])}")'
)

# Escape findings
code = code.replace(
    '                    lines.append(f"  • {str(f_item.get(\'text\', f_item))[:120]}")',
    '                    lines.append(f"  • {_esc_md(str(f_item.get(\'text\', f_item))[:120])}")'
)
code = code.replace(
    '                    lines.append(f"  • {str(f_item)[:120]}")',
    '                    lines.append(f"  • {_esc_md(str(f_item)[:120])}")'
)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("PATCHED: _esc_md helper + escape all dynamic content + try/except fallback")

# Verify syntax
import py_compile
try:
    py_compile.compile(FILE, doraise=True)
    print("SYNTAX OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
