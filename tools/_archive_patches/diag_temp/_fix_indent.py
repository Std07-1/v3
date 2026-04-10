#!/usr/bin/env python3
"""Fix return indentation in prompts.py."""

import pathlib

p = pathlib.Path("/opt/smc-trader-v3/bot/agent/prompts.py")
text = p.read_text(encoding="utf-8")

# The bug: return "\n".join(lines) at column 0 instead of column 4
old = '\n\nreturn "\\n".join(lines)'
new = '\n\n    return "\\n".join(lines)'

if old not in text:
    # Check if already fixed
    if '    return "\\n".join(lines)' in text:
        print("ALREADY FIXED: return properly indented")
    else:
        print("ERROR: cannot find return pattern")
    exit(0)

text = text.replace(old, new, 1)
p.write_text(text, encoding="utf-8")
print("FIXED: return indentation restored")

# Syntax check
import py_compile

try:
    py_compile.compile(str(p), doraise=True)
    print("SYNTAX OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
