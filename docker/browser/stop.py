"""Stop only the recorded browser fixture, and wait for its data cleanup."""

import os
import signal
from pathlib import Path
from time import sleep

pid_file = Path("/tmp/frontend-browser-fixture.pid")  # noqa: S108 - Container IPC, verified PID below.
pid = int(pid_file.read_text())
command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
if b"/tmp/frontend_browser_fixture.py" not in command:
    raise RuntimeError("Recorded PID is not the browser fixture")
os.kill(pid, signal.SIGINT)
for _ in range(100):
    if not pid_file.exists():
        print("Browser fixture: isolated data cleanup confirmed")
        break
    sleep(0.1)
else:
    raise RuntimeError("Browser fixture cleanup did not finish")
