from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / ".dev-pids.json"


def main() -> int:
    if not PID_FILE.exists():
        print("No dev processes are recorded. Nothing to stop.")
        return 0
    raw = PID_FILE.read_text(encoding="utf-8")
    try:
        pids = json.loads(raw)
    except json.JSONDecodeError:
        pids = [int(item) for item in raw.strip("[]").split(",") if item.strip()]

    stopped_any = False
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.kill(int(pid), signal.SIGTERM)
            stopped_any = True
        except (ProcessLookupError, PermissionError, OSError):
            continue
    PID_FILE.unlink(missing_ok=True)
    time.sleep(0.5)
    print("Development services stopped." if stopped_any else "Development services were already stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
