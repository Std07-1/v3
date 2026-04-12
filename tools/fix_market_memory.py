"""One-shot fix: add drain_market_memory() to StateManager (ADR-023)."""

import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/opt/smc-trader-v3/bot/state/manager.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

if "drain_market_memory" in content:
    print("SKIP: drain_market_memory already exists")
    sys.exit(0)

# 1. Add _pending_market_memory field after _pending_doc_feedback
old_init = "        self._pending_doc_feedback: list[str] = []"
new_init = (
    old_init + "\n"
    "        # ADR-023: Market memory queue (session recap, bookmarks)\n"
    "        self._pending_market_memory: list[str] = []"
)
if old_init not in content:
    print("ERROR: could not find _pending_doc_feedback init line")
    sys.exit(1)
content = content.replace(old_init, new_init, 1)

# 2. Add drain_market_memory() after drain_doc_feedback()
marker = "        self._pending_doc_feedback.clear()\n        return result"
insert_after = marker + "\n"
new_methods = (
    "\n"
    "    def queue_market_memory(self, text: str) -> None:\n"
    '        """Queue market memory snippet for injection into next agent call (ADR-023)."""\n'
    "        self._pending_market_memory.append(text)\n"
    "\n"
    "    def drain_market_memory(self) -> str:\n"
    '        """Return and clear pending market memory. ADR-023 E4-Lite."""\n'
    "        if not self._pending_market_memory:\n"
    '            return ""\n'
    '        result = "\\n\\n".join(self._pending_market_memory)\n'
    "        self._pending_market_memory.clear()\n"
    "        return result\n"
)

if marker not in content:
    print("ERROR: could not find drain_doc_feedback end marker")
    sys.exit(1)

content = content.replace(insert_after, insert_after + new_methods, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: drain_market_memory + queue_market_memory added to StateManager")
