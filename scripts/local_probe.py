#!/usr/bin/env python3
import json
import os
import subprocess
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
proc = subprocess.run(
    ["python3", os.path.join(root, "scripts", "read_codex_usage.py")],
    check=True,
    capture_output=True,
    text=True,
    env=os.environ.copy(),
)
obj = json.loads(proc.stdout.strip().splitlines()[-1])
snapshot = (obj.get("rateLimitsByLimitId") or {}).get("codex") or obj.get("rateLimits") or {}
print(json.dumps({
    "limitId": snapshot.get("limitId"),
    "primary": snapshot.get("primary"),
    "secondary": snapshot.get("secondary"),
}, indent=2))
