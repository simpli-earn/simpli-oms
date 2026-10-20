#!/usr/bin/env python3
"""Install Simpli OMS on macOS, Linux, and Windows.

    python setup.py

Creates backend/.venv, installs Python and npm dependencies, and copies
backend/.env.example to backend/.env when that file is absent.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
MIN_PYTHON = (3, 11)
MIN_NODE = 18


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], cwd: Path) -> None:
    shown = " ".join(f'"{part}"' if " " in part else part for part in command)
    log("  " + shown)
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError:
        fail(f"could not run {command[0]!r}")
    except subprocess.CalledProcessError as exc:
        fail(f"command failed with exit code {exc.returncode}: {' '.join(command)}")


def python_version(command: list[str]) -> tuple[int, int] | None:
    try:
        completed = subprocess.run(
            [*command, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    text = completed.stdout.strip()
    if not text:
        return None
    major, minor = text.split(".", 1)
    return int(major), int(minor)


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def host_python() -> list[str]:
    current = [sys.executable]
    version = python_version(current)
    if version is not None and version >= MIN_PYTHON:
        return current

    candidates: list[list[str]] = []
    if sys.platform == "win32":
        candidates.extend(["py", f"-3.{minor}"] for minor in range(14, 10, -1))
    candidates.extend([name] for name in (
        "python3.14",
        "python3.13",
        "python3.12",
        "python3.11",
        "python3",
        "python",
    ))
    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        version = python_version(command)
        if version is not None and version >= MIN_PYTHON:
            return command
    fail(
        "Python 3.11 or newer is required. Install it, then run this script again "
        "with that interpreter: python setup.py"
    )


def ensure_venv(python: list[str]) -> Path:
    target = venv_python()
    if VENV.exists() and python_version([str(target)]) is None:
        log("replacing an unusable backend/.venv")
        shutil.rmtree(VENV)
    elif target.is_file():
        version = python_version([str(target)])
        if version is not None and version < MIN_PYTHON:
            log(f"replacing backend/.venv (Python {version[0]}.{version[1]} is older than 3.11)")
            shutil.rmtree(VENV)
        else:
            log(f"backend/.venv already exists (Python {version[0]}.{version[1]})")
            return target

    log("creating backend/.venv")
    run([*python, "-m", "venv", str(VENV)], ROOT)
    if not venv_python().is_file():
        fail(f"venv was created but {venv_python()} is missing")
    return venv_python()


def install_python(python: Path) -> None:
    log("installing backend dependencies")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"], BACKEND)
    run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], BACKEND)
    run(
        [
            str(python),
            "-c",
            "import fastapi, uvicorn, sqlalchemy, pydantic_settings, alpaca",
        ],
        BACKEND,
    )


def ensure_env() -> None:
    example = BACKEND / ".env.example"
    destination = BACKEND / ".env"
    if not example.is_file():
        fail("backend/.env.example is missing")
    if destination.exists():
        log("backend/.env already exists; left it unchanged")
        return
    shutil.copyfile(example, destination)
    log("created backend/.env from backend/.env.example")


def env_needs_keys() -> bool:
    destination = BACKEND / ".env"
    values: dict[str, str] = {}
    for line in destination.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return not values.get("APCA_API_KEY_ID") or not values.get("APCA_API_SECRET_KEY")


def npm_command(args: list[str]) -> list[str]:
    npm = shutil.which("npm")
    if npm is None:
        fail("npm is not on PATH. Install Node.js 18 or newer, then run this script again.")
    if sys.platform == "win32":
        return ["cmd", "/c", "npm", *args]
    return [npm, *args]


def install_node() -> None:
    node = shutil.which("node")
    if node is None:
        fail("node is not on PATH. Install Node.js 18 or newer, then run this script again.")
    completed = subprocess.run(
        [node, "-p", "process.versions.node"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        fail("could not read the Node.js version")
    major = int(completed.stdout.strip().split(".", 1)[0])
    if major < MIN_NODE:
        fail(f"Node.js {MIN_NODE} or newer is required (found {completed.stdout.strip()})")
    log(f"installing frontend dependencies (Node.js {completed.stdout.strip()})")
    lockfile = FRONTEND / "package-lock.json"
    args = ["ci"] if lockfile.is_file() else ["install"]
    run(npm_command(args), FRONTEND)


def main() -> None:
    if not (BACKEND / "requirements.txt").is_file() or not (FRONTEND / "package.json").is_file():
        fail("run this script from a Simpli OMS checkout")
    log("Simpli OMS setup")
    python = ensure_venv(host_python())
    install_python(python)
    ensure_env()
    install_node()
    log("")
    log("Setup finished.")
    if env_needs_keys():
        log("Add Alpaca paper keys to backend/.env before sending orders:")
        log("  APCA_API_KEY_ID")
        log("  APCA_API_SECRET_KEY")
    uvicorn = f"{python} -m uvicorn app.main:app --reload --port 8787"
    log("Start the backend from the backend directory:")
    log(f"  {uvicorn}")
    log("Start the frontend from the frontend directory:")
    log("  npm run dev")
    log("Dashboard: http://localhost:5173")
    log("API docs:  http://localhost:8787/docs")


if __name__ == "__main__":
    main()
