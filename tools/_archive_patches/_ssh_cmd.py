#!/usr/bin/env python3
"""Quick check: run SSH command and print output."""

import subprocess, sys

cmd = sys.argv[1] if len(sys.argv) > 1 else "echo hello"
r = subprocess.run(
    ["ssh", "aione-vps", cmd], capture_output=True, text=True, timeout=15
)
print("STDOUT:", r.stdout)
print("STDERR:", r.stderr)
print("RC:", r.returncode)
