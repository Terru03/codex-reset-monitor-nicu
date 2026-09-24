#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time


def send(proc, message):
    proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def read_response(proc, wanted_id, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line == "":
            raise RuntimeError("codex app-server exited before returning the requested response")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") != wanted_id:
            continue
        if "error" in msg:
            raise RuntimeError(f"Codex app-server RPC error: {json.dumps(msg['error'])}")
        if "result" not in msg:
            raise RuntimeError(f"Codex app-server returned an unexpected response: {line}")
        return msg["result"]
    raise TimeoutError(f"Timed out waiting for Codex app-server response id={wanted_id}")


def main():
    codex_home = os.environ.get("CODEX_HOME")
    if not codex_home:
        raise SystemExit("CODEX_HOME is not set")
    auth_path = os.path.join(codex_home, "auth.json")
    if not os.path.isfile(auth_path):
        raise SystemExit(f"Missing isolated Codex auth file: {auth_path}")

    proc = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )
    try:
        send(proc, {
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "codex-reset-monitor",
                    "title": "Codex Reset Monitor",
                    "version": "1.0.0",
                },
                "capabilities": {
                    "experimentalApi": False,
                    "requestAttestation": False,
                },
            },
        })
        read_response(proc, 1)
        send(proc, {"method": "initialized"})
        send(proc, {
            "method": "account/rateLimits/read",
            "id": 2,
            "params": {"excludeResetCreditDetails": True},
        })
        result = read_response(proc, 2)
        print(json.dumps(result, separators=(",", ":")))
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    main()
