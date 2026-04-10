"""Fix indent: move _BUDGET_WARN constants to module level."""

MONITOR = "/opt/smc-trader-v3/bot/scheduling/monitor.py"

with open(MONITOR, "r", encoding="utf-8") as f:
    code = f.read()

# Remove the wrongly-placed constants (no indent, inside function)
code = code.replace(
    "_BUDGET_WARN_INTERVAL = 300  # log budget warning at most once per 5 min\n_last_budget_warn_ts: float = 0.0\n",
    "",
)

# Find the function definition and add constants BEFORE it
marker = "async def monitor_loop_v2("
if marker in code:
    code = code.replace(
        marker,
        "_BUDGET_WARN_INTERVAL = 300  # log budget warning at most once per 5 min\n_last_budget_warn_ts: float = 0.0\n\n\n"
        + marker,
    )
    print("OK: Moved constants to module level")
else:
    print("FAIL: Could not find monitor_loop_v2 definition")

with open(MONITOR, "w", encoding="utf-8") as f:
    f.write(code)

import ast

try:
    ast.parse(code)
    print("SYNTAX OK: monitor.py")
except SyntaxError as e:
    print(f"SYNTAX ERROR: monitor.py: {e}")
