"""Daily auto-runs through macOS launchd, so they fire even when the app is closed.

Each scheduled strategy gets a LaunchAgent in ~/Library/LaunchAgents that calls runner.py at the
chosen local time on the chosen weekdays. If the laptop is asleep at that time, launchd runs the
job once when it wakes. If it is shut down, the run is missed. Scheduled runs use the strategy's
scheduled version when one is set, otherwise its working code.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import desk

AGENTS = Path.home() / "Library" / "LaunchAgents"
PREFIX = "com.simpliearn.desk."
DOMAIN = f"gui/{os.getuid()}"


def label(sid: str) -> str:
    return PREFIX + sid


def plist_path(sid: str) -> Path:
    return AGENTS / f"{label(sid)}.plist"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(sid: str, hour: int, minute: int, weekdays: list[int]) -> None:
    """weekdays: 1=Monday ... 7=Sunday (launchd uses 0 or 7 for Sunday)."""
    AGENTS.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": label(sid),
        "ProgramArguments": [str(desk.PYTHON), str(desk.DESKTOP / "runner.py"), sid, "--trigger", "scheduled"],
        "WorkingDirectory": str(desk.DESKTOP),
        "StartCalendarInterval": [{"Weekday": d % 7, "Hour": hour, "Minute": minute} for d in sorted(weekdays)],
        "StandardOutPath": str(desk.LOGS / f"launchd-{sid}.log"),
        "StandardErrorPath": str(desk.LOGS / f"launchd-{sid}.log"),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "ProcessType": "Background",
    }
    path = plist_path(sid)
    _launchctl("bootout", f"{DOMAIN}/{label(sid)}")
    with open(path, "wb") as fh:
        plistlib.dump(plist, fh)
    result = _launchctl("bootstrap", DOMAIN, str(path))
    if result.returncode != 0 and not loaded(sid):
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip() or result.stdout.strip()}")


def remove(sid: str) -> None:
    _launchctl("bootout", f"{DOMAIN}/{label(sid)}")
    plist_path(sid).unlink(missing_ok=True)


def loaded(sid: str) -> bool:
    return _launchctl("print", f"{DOMAIN}/{label(sid)}").returncode == 0


def apply(sid: str, hour: int, minute: int, weekdays: list[int], enabled: bool) -> None:
    desk.save_schedule(sid, hour, minute, weekdays, enabled)
    if enabled and weekdays:
        install(sid, hour, minute, weekdays)
    else:
        remove(sid)
