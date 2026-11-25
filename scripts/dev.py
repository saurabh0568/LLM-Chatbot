from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / ".dev-pids.json"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def terminate(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    load_env_file(ROOT / ".env")
    backend_port = os.getenv("BACKEND_PORT", "8000")
    frontend_port = os.getenv("FRONTEND_PORT", "3000")
    npm = "npm.cmd" if os.name == "nt" else "npm"

    commands = [
        (
            [sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", backend_port],
            ROOT / "backend",
        ),
        ([npm, "run", "dev", "--", "--port", frontend_port], ROOT / "frontend"),
    ]

    processes: list[subprocess.Popen] = []
    try:
        for command, cwd in commands:
            processes.append(subprocess.Popen(command, cwd=str(cwd)))
        PID_FILE.write_text("[" + ",".join(str(process.pid) for process in processes) + "]", encoding="utf-8")
        print(f"Backend:  http://localhost:{backend_port}")
        print(f"Frontend: http://localhost:{frontend_port}")
        print("Press Ctrl+C to stop both services.")
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(f"A service exited with code {return_code}.")
                    return return_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        terminate(processes)
        if PID_FILE.exists():
            PID_FILE.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
