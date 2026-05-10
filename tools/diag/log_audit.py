"""Log audit: count SCREAMING_SNAKE vs other naming in log messages.

Usage: python -m tools.diag.log_audit
"""

import os
import re
import sys

SCAN_DIRS = ["runtime", "app"]
LOG_PATTERN = re.compile(r'(?:logging|_log|log|Logging)\.\w+\(\s*["\x27](.*?)[\s"\x27]')
SCREAMING = re.compile(r"^[A-Z][A-Z0-9_]+")


def main() -> None:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    s = n = 0
    for scan_dir in SCAN_DIRS:
        root = os.path.join(base, scan_dir)
        for dp, _, fns in os.walk(root):
            if "__pycache__" in dp:
                continue
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                with open(
                    os.path.join(dp, fn), encoding="utf-8", errors="replace"
                ) as f:
                    for line in f:
                        m = LOG_PATTERN.search(line)
                        if m:
                            g = m.group(1)
                            if SCREAMING.match(g):
                                s += 1
                            elif g:
                                n += 1
    total = s + n
    if total:
        print(f"SCREAMING_SNAKE: {s}  other: {n}  ratio: {s/total*100:.0f}%")
    else:
        print("No log messages found")


if __name__ == "__main__":
    main()
