#!/usr/bin/env python3
"""Final verification of Claude Code setup on VPS."""

import json, os, pathlib


def check(label, ok, detail=""):
    status = "✅" if ok else "❌"
    print(f"  {status} {label}" + (f" — {detail}" if detail else ""))
    return ok


errors = 0
print("=== Claude Code VPS Setup Verification ===\n")

# 1. CLAUDE.md
print("1. CLAUDE.md:")
for p in ["/opt/smc-trader-v3/CLAUDE.md", "/home/ubuntu/CLAUDE.md"]:
    exists = os.path.isfile(p)
    lines = len(open(p).readlines()) if exists else 0
    if not check(p, exists and lines > 100, f"{lines} lines"):
        errors += 1

# 2. Settings
print("\n2. Settings:")
for p in [
    "/home/ubuntu/.claude/settings.json",
    "/home/ubuntu/.claude/projects/-home-ubuntu/settings.json",
    "/home/ubuntu/.claude/projects/-opt-smc-trader-v3/settings.json",
]:
    try:
        d = json.load(open(p))
        allow = len(d.get("permissions", {}).get("allow", []))
        deny = len(d.get("permissions", {}).get("deny", []))
        check(p, allow > 0, f"{allow} allow, {deny} deny")
    except Exception as e:
        check(p, False, str(e))
        errors += 1

# 3. Memory
print("\n3. Project memory:")
mem_dir = "/home/ubuntu/.claude/projects/-home-ubuntu/memory"
files = list(pathlib.Path(mem_dir).glob("*.md")) if os.path.isdir(mem_dir) else []
if not check(f"Memory files in {mem_dir}", len(files) >= 2, f"{len(files)} files"):
    errors += 1

# 4. Stale files
print("\n4. Stale files in /home/ubuntu/:")
stale = [f for f in os.listdir("/home/ubuntu/") if f.endswith(".py")]
if not check("No stale .py files", len(stale) == 0, str(stale) if stale else "clean"):
    errors += 1

# 5. Symlink
print("\n5. Symlink:")
link = "/home/ubuntu/trader-v3"
target = os.readlink(link) if os.path.islink(link) else None
if not check("trader-v3 symlink", target == "/opt/smc-trader-v3", f"→ {target}"):
    errors += 1

# 6. .bashrc
print("\n6. .bashrc:")
bashrc = open("/home/ubuntu/.bashrc").read()
if not check("No ANTHROPIC_API_KEY leak", "ANTHROPIC_API_KEY" not in bashrc):
    errors += 1
check("archi alias", "alias archi=" in bashrc)

# 7. .env exists (secrets)
print("\n7. Secrets:")
env = "/opt/smc-trader-v3/.env"
if os.path.isfile(env):
    content = open(env).read()
    check(".env exists", True, f"{len(content.splitlines())} lines")
    has_key = "ANTHROPIC_API_KEY=" in content
    check("Has ANTHROPIC_API_KEY", has_key)
else:
    check(".env exists", False)
    errors += 1

# 8. Security: no keys in wrong places
print("\n8. Security scan:")
for p in ["/home/ubuntu/CLAUDE.md", "/opt/smc-trader-v3/CLAUDE.md"]:
    content = open(p).read() if os.path.isfile(p) else ""
    has_key = "sk-ant" in content or "ANTHROPIC_API_KEY=" in content
    if not check(f"No API key in {os.path.basename(p)}", not has_key):
        errors += 1

# Check if .claude.json has leaked keys
cj = "/home/ubuntu/.claude.json"
if os.path.isfile(cj):
    cjd = json.load(open(cj))
    has_key = "apiKey" in json.dumps(cjd)
    check(".claude.json: no embedded apiKey", not has_key)

# 9. Bot status
print("\n9. Bot status:")
import subprocess

r = subprocess.run(
    ["sudo", "supervisorctl", "status", "smc_trader_v3"], capture_output=True, text=True
)
running = "RUNNING" in r.stdout
check("smc_trader_v3 running", running, r.stdout.strip())

# Summary
print(f"\n{'='*50}")
if errors == 0:
    print("✅ ALL CHECKS PASSED")
else:
    print(f"❌ {errors} CHECK(S) FAILED")
