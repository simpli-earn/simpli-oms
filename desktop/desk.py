"""Shared paths, strategies, versions, and run history for the SimpliEarn Desk app.

The app window, the runner, and launchd jobs all import this module, so a run started from
any of them shows up in the same history.

Each strategy is a folder in strategies/ named after its OMS strategy id:

    strategies/zscore-spy/
        strategy.json      manifest: where the code lives, saved versions, scheduled version
        zscore_spy.ipynb   the code, when it is stored here (template or uploaded copy)
        versions/          snapshots you saved, one file per version
        runs/              executed notebooks from past runs

A linked strategy keeps its code wherever it already is; the manifest stores the path and
every run reads that file fresh.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent
OMS_ROOT = DESKTOP.parent
STRATEGIES = OMS_ROOT / "strategies"
TEMPLATES = STRATEGIES / "templates"
BACKEND = OMS_ROOT / "backend"
FRONTEND = OMS_ROOT / "frontend"
DATA = DESKTOP / "data"
LOGS = DATA / "logs"
DB_PATH = DATA / "desk.db"
CONFIG_PATH = DATA / "config.json"
PYTHON = STRATEGIES / ".venv" / "bin" / "python"
KERNEL = "simpli-oms-strategies"
MANIFEST = "strategy.json"
CODE_SUFFIXES = (".py", ".ipynb")

for d in (DATA, LOGS):
    d.mkdir(parents=True, exist_ok=True)

ACTIVE = ("queued", "running")
VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
VALID_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def config() -> dict:
    """Machine settings written by build_app.sh (node path for the dashboard)."""
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- code files

def _nb_cells(path: Path) -> list[dict]:
    return json.loads(path.read_text()).get("cells", [])


def _source(cell: dict) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else src


def code_text(path: Path) -> str:
    """The code that runs: the whole .py file, or a notebook's code cells (outputs ignored)."""
    if path.suffix == ".ipynb":
        cells = [c for c in _nb_cells(path) if c.get("cell_type") == "code"]
        return "\n".join(f"# ── cell {i} ──\n{_source(c).rstrip()}\n" for i, c in enumerate(cells, 1))
    return path.read_text()


MAX_VIEW_CHARS = 1_000_000
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def file_view(name: str, data: bytes) -> dict:
    """A file's contents for the app's file viewer: notebooks as cells (with text outputs),
    JSON pretty-printed, other text as is, binaries by size only."""
    try:
        if b"\0" in data[:8192]:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
        text = data.decode()
    except UnicodeDecodeError:
        return {"kind": "binary", "size": len(data)}
    if name.endswith(".ipynb"):
        try:
            cells = []
            for c in json.loads(text).get("cells", []):
                outs = []
                for o in c.get("outputs", []):
                    if o.get("output_type") == "stream":
                        outs.append(_source({"source": o.get("text", "")}))
                    elif o.get("output_type") == "error":
                        outs.append(ANSI.sub("", "\n".join(o.get("traceback", []))))
                    elif "text/plain" in o.get("data", {}):
                        outs.append(_source({"source": o["data"]["text/plain"]}) + "\n")
                cells.append({"type": c.get("cell_type"), "source": _source(c), "output": "".join(outs)[:20_000]})
            return {"kind": "notebook", "cells": cells, "size": len(data)}
        except (ValueError, AttributeError):
            pass
    if name.endswith(".json"):
        try:
            text = _pretty_json(json.loads(text))
        except ValueError:
            pass
    return {"kind": "text", "text": text[:MAX_VIEW_CHARS], "truncated": len(text) > MAX_VIEW_CHARS, "size": len(data)}


def _pretty_json(obj, pad: str = "") -> str:
    """Indented JSON, but a list of plain values stays on one line (model files are mostly those)."""
    inner = pad + "  "
    if isinstance(obj, dict) and obj:
        return "{\n" + ",\n".join(f"{inner}{json.dumps(k)}: {_pretty_json(v, inner)}" for k, v in obj.items()) + f"\n{pad}}}"
    if isinstance(obj, list) and any(isinstance(v, (dict, list)) for v in obj):
        return "[\n" + ",\n".join(inner + _pretty_json(v, inner) for v in obj) + f"\n{pad}]"
    return json.dumps(obj)


def code_hash(path: Path) -> str:
    return hashlib.sha256(code_text(path).encode()).hexdigest()[:12]


def file_title(path: Path) -> tuple[str, str]:
    """(name, one-line description) from a .py docstring or a notebook's first markdown cell."""
    lines: list[str] = []
    try:
        if path.suffix == ".ipynb":
            md = next((c for c in _nb_cells(path) if c.get("cell_type") == "markdown"), None)
            lines = [l.strip() for l in _source(md).splitlines() if l.strip()] if md else []
        else:
            doc = ast.get_docstring(ast.parse(path.read_text())) or ""
            lines = [l.strip() for l in doc.splitlines() if l.strip()]
    except (OSError, ValueError, SyntaxError):
        pass
    if not lines:
        return path.stem.replace("_", " "), ""
    title = lines[0].lstrip("#").strip()
    desc = next((l for l in lines[1:] if not l.startswith(("#", "**", "|", "-", "*", "1.", "How ", "Strategy `"))), "")
    desc = re.sub(r"[*`_]{1,2}", "", desc)
    first = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0]  # one sentence is enough for a card
    return title, (first[:157] + "…") if len(first) > 160 else first


HARDCODED_ID = re.compile(r"""(?:STRATEGY_ID\s*=\s*(?:os\.environ\.get\([^,]+,\s*)?|OMS\(\s*)["']([^"']+)["']""")


def check_code(path: Path, strategy_id: str) -> list[dict]:
    """Static checks for the platform contract. Errors block a run; warnings are advice."""
    out = []
    def add(level, msg):
        out.append({"level": level, "msg": msg})

    if not path.exists():
        add("error", f"Code file not found: {path}")
        return out
    if path.suffix not in CODE_SUFFIXES:
        add("error", "Only .py and .ipynb files can run")
        return out
    try:
        text = code_text(path)
    except (OSError, ValueError) as exc:
        add("error", f"Can't read the file: {exc}")
        return out
    if path.suffix == ".py":
        try:
            compile(text, str(path), "exec")
        except SyntaxError as exc:
            add("error", f"Syntax error on line {exc.lineno}: {exc.msg}")
    if "oms_client" not in text:
        add("warn", "Doesn't import oms_client, so it can't place orders through the OMS")
    elif "OMS(" not in text:
        add("warn", "Imports oms_client but never creates OMS()")
    ids = set(HARDCODED_ID.findall(text))
    wrong = sorted(i for i in ids if i != strategy_id)
    if wrong:
        add("warn", f"Code mentions strategy id {', '.join(wrong)}, but this strategy is {strategy_id}. "
                    "Use OMS() or os.environ['SIMPLI_STRATEGY_ID'] so the app's id is used")
    if path.suffix == ".ipynb" and "rebalance(" not in text and ".buy(" not in text and ".sell(" not in text and ".order(" not in text:
        add("warn", "No oms.rebalance(), buy(), sell() or order() call found")
    return out


# ---------------------------------------------------------------- strategies

def folder(sid: str) -> Path:
    return STRATEGIES / sid


def read_manifest(sid: str) -> dict:
    return json.loads((folder(sid) / MANIFEST).read_text())


def write_manifest(sid: str, manifest: dict) -> None:
    path = folder(sid) / MANIFEST
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n")
    tmp.replace(path)


def strategy_ids(include_removed: bool = False) -> list[str]:
    """Strategy folders on this laptop. A removed strategy keeps its folder and code but is hidden."""
    out = []
    for p in STRATEGIES.glob(f"*/{MANIFEST}"):
        try:
            removed = json.loads(p.read_text()).get("removed_at")
        except (OSError, ValueError):
            removed = None
        if include_removed or not removed:
            out.append(p.parent.name)
    return sorted(out)


def set_removed(sid: str, removed: bool) -> None:
    if not (folder(sid) / MANIFEST).exists():
        return
    m = read_manifest(sid)
    m["removed_at"] = now() if removed else None
    write_manifest(sid, m)


def source_path(sid: str, manifest: dict | None = None) -> Path:
    m = manifest or read_manifest(sid)
    src = m["source"]
    return Path(src["path"]).expanduser() if src["kind"] == "linked" else folder(sid) / src["file"]


def runs_dir(sid: str) -> Path:
    d = folder(sid) / "runs"
    d.mkdir(exist_ok=True)
    return d


def describe(sid: str) -> dict:
    """Everything the app shows about one strategy."""
    m = read_manifest(sid)
    path = source_path(sid, m)
    exists = path.exists()
    title, desc = file_title(path) if exists else (sid, "")
    current = code_hash(path) if exists else None
    versions = m.get("versions", [])
    match = next((v for v in reversed(versions) if v["hash"] == current), None)
    return {
        "strategy_id": sid,
        "title": m.get("title") or title,
        "name": m.get("title") or "",  # set by you (Edit details); title falls back to the file's heading
        "description": m.get("description") or desc,
        "custom_description": m.get("description") or "",
        "kind": path.suffix.lstrip("."),
        "source": m["source"]["kind"],
        "origin": m.get("origin", m["source"]["kind"]),
        "path": str(path),
        "folder": str(folder(sid)),
        "exists": exists,
        "code_hash": current,
        "versions": versions,
        "matches_version": match["label"] if match else None,
        "latest_version": versions[-1]["label"] if versions else None,
        "scheduled_version": m.get("scheduled_version"),
        "checks": check_code(path, sid),
    }


def set_details(sid: str, name: str, description: str) -> None:
    """The name and description you give a strategy. Stored in strategy.json, so they're
    backed up and shared with the cloud copy. Empty means "use the code's own heading"."""
    name, description = name.strip(), description.strip()
    if len(name) > 80:
        raise ValueError("Keep the name under 80 characters")
    if len(description) > 500:
        raise ValueError("Keep the description under 500 characters")
    m = read_manifest(sid)
    m["title"], m["description"] = name or None, description or None
    write_manifest(sid, m)


def _fill_template(text: str, title: str, sid: str) -> str:
    for key, value in {"{{TITLE}}": title, "{{STRATEGY_ID}}": sid}.items():
        text = text.replace(key, json.dumps(value)[1:-1] if text.lstrip().startswith("{") else value)
    return text


def create_strategy(sid: str, origin: str, title: str = "", file: str | None = None) -> dict:
    """origin: template_py | template_ipynb | upload (copy `file` here) | link (run `file` in place)."""
    sid = sid.strip()
    if not VALID_ID.fullmatch(sid):
        raise ValueError("Strategy id must be 1-64 letters, numbers, hyphens, or underscores")
    if (folder(sid) / MANIFEST).exists():
        if read_manifest(sid).get("removed_at"):
            raise ValueError(f"{sid} was removed. Restore it from Strategies → Removed instead")
        raise ValueError(f"{sid} is already set up on this laptop")
    src_file = Path(file).expanduser() if file else None
    if origin in ("upload", "link"):
        if not src_file or not src_file.is_file():
            raise ValueError("Choose a .py or .ipynb file")
        if src_file.suffix not in CODE_SUFFIXES:
            raise ValueError("Only .py and .ipynb files can run")

    d = folder(sid)
    d.mkdir(parents=True, exist_ok=True)
    stem = sid.replace("-", "_")
    if origin in ("template_py", "template_ipynb"):
        ext = ".py" if origin == "template_py" else ".ipynb"
        target = d / f"{stem}{ext}"
        target.write_text(_fill_template((TEMPLATES / f"new_strategy{ext}").read_text(), title.strip() or sid, sid))
        source = {"kind": "local", "file": target.name}
    elif origin == "upload":
        target = d / src_file.name
        shutil.copy2(src_file, target)
        source = {"kind": "local", "file": target.name}
    elif origin == "link":
        source = {"kind": "linked", "path": str(src_file.resolve())}
    else:
        raise ValueError(f"unknown origin {origin}")
    manifest = {"strategy_id": sid, "title": title.strip() or None, "source": source, "origin": origin,
                "scheduled_version": None, "versions": [], "created_at": now()}
    write_manifest(sid, manifest)
    if origin in ("upload", "link"):  # real code from the start: save it as v1, used for scheduled runs
        save_version(sid, "v1", "First version")
    return describe(sid)


# ---------------------------------------------------------------- versions

def version_file(sid: str, label: str) -> Path:
    m = read_manifest(sid)
    v = next((v for v in m.get("versions", []) if v["label"] == label), None)
    if not v:
        raise ValueError(f"no version {label}")
    return folder(sid) / v["file"]


def next_label(sid: str) -> str:
    nums = [int(m.group(1)) for v in read_manifest(sid).get("versions", []) if (m := re.fullmatch(r"v(\d+)", v["label"]))]
    return f"v{max(nums, default=0) + 1}"


def save_version(sid: str, label: str, name: str, note: str = "") -> dict:
    """Snapshot the current code as a version you name. The first version saved while no version
    is picked for scheduled runs becomes the scheduled one (and so the one backed up to the cloud)."""
    label = label.strip()
    if not VALID_LABEL.fullmatch(label):
        raise ValueError("Version label: letters, numbers, dots, dashes (e.g. v3 or 2.1)")
    if not name.strip():
        raise ValueError("Give the version a name")
    m = read_manifest(sid)
    if any(v["label"] == label for v in m.get("versions", [])):
        raise ValueError(f"{label} already exists")
    src = source_path(sid, m)
    if not src.exists():
        raise ValueError("Code file not found")
    h = code_hash(src)
    same = next((v for v in m.get("versions", []) if v["hash"] == h), None)
    if same:
        raise ValueError(f"The code is unchanged since {same['label']} ({same['name']})")
    (folder(sid) / "versions").mkdir(exist_ok=True)
    rel = f"versions/{label}{src.suffix}"
    shutil.copy2(src, folder(sid) / rel)
    v = {"label": label, "name": name.strip(), "note": note.strip(), "file": rel, "hash": h, "created_at": now()}
    m.setdefault("versions", []).append(v)
    if not m.get("scheduled_version"):
        m["scheduled_version"] = label
    write_manifest(sid, m)
    return v


def delete_version(sid: str, label: str) -> None:
    m = read_manifest(sid)
    v = next((v for v in m.get("versions", []) if v["label"] == label), None)
    if not v:
        raise ValueError(f"no version {label}")
    (folder(sid) / v["file"]).unlink(missing_ok=True)
    m["versions"] = [x for x in m["versions"] if x["label"] != label]
    if m.get("scheduled_version") == label:
        m["scheduled_version"] = None
    write_manifest(sid, m)


def restore_version(sid: str, label: str) -> None:
    """Overwrite the working code (stored or linked file) with a saved version."""
    shutil.copy2(version_file(sid, label), source_path(sid))


def set_scheduled_version(sid: str, label: str | None) -> None:
    m = read_manifest(sid)
    if label and not any(v["label"] == label for v in m.get("versions", [])):
        raise ValueError(f"no version {label}")
    m["scheduled_version"] = label or None
    write_manifest(sid, m)


def diff(sid: str, a: str, b: str = "working") -> str:
    """Unified diff of the code between two versions ("working" = the current file)."""
    def text(ref):
        return code_text(source_path(sid) if ref == "working" else version_file(sid, ref))
    lines = difflib.unified_diff(text(a).splitlines(), text(b).splitlines(),
                                 fromfile=a, tofile="working copy" if b == "working" else b, lineterm="", n=3)
    return "\n".join(lines)


# ---------------------------------------------------------------- history

SCHEMA = """
create table if not exists runs (
    id text primary key,
    notebook text not null,
    strategy_id text,
    trigger text not null,
    dry_run integer not null default 0,
    status text not null,
    summary text,
    orders integer,
    started_at text not null,
    finished_at text,
    output_path text,
    log_path text,
    pid integer
);
create index if not exists runs_by_notebook on runs (notebook, started_at);
create table if not exists strategy_flags (
    strategy_id text primary key,
    state text not null,          -- deleted (never traded) | removed (traded, kept for the record)
    flagged_at text not null,
    final text                    -- JSON snapshot of the book when it was removed
);
create table if not exists strategy_schedules (
    strategy text primary key,
    hour integer not null,
    minute integer not null,
    weekdays text not null,
    enabled integer not null
);
create table if not exists cloud_sync (   -- the cloud revision this laptop last matched (cloud.py)
    strategy text primary key,
    revision integer not null,
    files text not null,          -- JSON {path: sha256} of what was uploaded or downloaded
    schedule text,
    synced_at text not null
);
"""
EXTRA_COLUMNS = {"version": "text", "code_hash": "text"}


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("pragma table_info(runs)")}
    for col, typ in EXTRA_COLUMNS.items():
        if col not in have:
            conn.execute(f"alter table runs add column {col} {typ}")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def create_run(sid: str, trigger: str, dry_run: bool, version: str | None = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    try:
        name = source_path(sid).name
    except (OSError, KeyError, ValueError):
        name = sid
    with db() as conn:
        conn.execute(
            "insert into runs (id, notebook, strategy_id, trigger, dry_run, status, started_at, version) values (?,?,?,?,?,?,?,?)",
            (run_id, name, sid, trigger, int(dry_run), "queued", now(), version),
        )
    return run_id


def update_run(run_id: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db() as conn:
        conn.execute(f"update runs set {cols} where id = ?", (*fields.values(), run_id))


def stop_marker(run_id: str) -> Path:
    return DATA / f".stop-{run_id}"


def get_run(run_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def recent_runs(limit: int = 60) -> list[dict]:
    reap_dead_runs()
    with db() as conn:
        rows = conn.execute("select * from runs order by started_at desc limit ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def last_run(sid: str) -> dict | None:
    with db() as conn:
        row = conn.execute("select * from runs where strategy_id = ? order by started_at desc limit 1", (sid,)).fetchone()
    return dict(row) if row else None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def reap_dead_runs() -> None:
    """A run whose process vanished (laptop shut down, killed) is marked interrupted."""
    with db() as conn:
        rows = conn.execute("select id, pid, started_at from runs where status in ('queued','running')").fetchall()
        for r in rows:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["started_at"])).total_seconds()
            if (r["pid"] and not pid_alive(r["pid"])) or (not r["pid"] and age > 120):
                conn.execute(
                    "update runs set status='interrupted', summary=coalesce(summary,'process stopped before finishing'), "
                    "finished_at=? where id=?",
                    (now(), r["id"]),
                )


# ---------------------------------------------------------------- who needs the OMS running

APP_PID = DATA / "app.pid"  # written while the app window is open


def app_open() -> bool:
    try:
        return pid_alive(int(APP_PID.read_text()))
    except (OSError, ValueError):
        return False


def oms_needed(except_run: str | None = None) -> bool:
    """The app is open or a run (other than except_run) is still going. The app starts the OMS
    when it opens; a run starts it if it's down. Whoever finishes last stops it."""
    if app_open():
        return True
    reap_dead_runs()
    with db() as conn:
        rows = conn.execute("select id from runs where status in ('queued','running')").fetchall()
    return any(r["id"] != except_run for r in rows)


# ---------------------------------------------------------------- deleted / removed strategies

def flags() -> dict[str, dict]:
    with db() as conn:
        rows = conn.execute("select * from strategy_flags").fetchall()
    return {r["strategy_id"]: {"state": r["state"], "flagged_at": r["flagged_at"],
                               "final": json.loads(r["final"]) if r["final"] else None} for r in rows}


def set_flag(sid: str, state: str | None, final: dict | None = None) -> None:
    with db() as conn:
        if state is None:
            conn.execute("delete from strategy_flags where strategy_id = ?", (sid,))
        else:
            conn.execute("insert or replace into strategy_flags (strategy_id, state, flagged_at, final) values (?,?,?,?)",
                         (sid, state, now(), json.dumps(final) if final else None))


# ---------------------------------------------------------------- schedules

def get_schedule(sid: str) -> dict | None:
    with db() as conn:
        row = conn.execute("select * from strategy_schedules where strategy = ?", (sid,)).fetchone()
    if not row:
        return None
    s = dict(row)
    s["weekdays"] = [int(x) for x in s["weekdays"].split(",") if x]
    s["enabled"] = bool(s["enabled"])
    return s


def save_schedule(sid: str, hour: int, minute: int, weekdays: list[int], enabled: bool) -> None:
    with db() as conn:
        conn.execute(
            "insert into strategy_schedules (strategy, hour, minute, weekdays, enabled) values (?,?,?,?,?) "
            "on conflict(strategy) do update set hour=excluded.hour, minute=excluded.minute, "
            "weekdays=excluded.weekdays, enabled=excluded.enabled",
            (sid, hour, minute, ",".join(str(d) for d in sorted(weekdays)), int(enabled)),
        )


# ---------------------------------------------------------------- JupyterLab

def _jupyter(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(STRATEGIES / ".venv" / "bin" / "jupyter"), *args], capture_output=True, text=True)


def jupyter_url(path: Path) -> str | None:
    """URL of `path` on a running JupyterLab whose root folder contains it."""
    path = path.resolve()
    for line in _jupyter("server", "list", "--json").stdout.splitlines():
        try:
            s = json.loads(line)
        except ValueError:
            continue
        root = Path(s.get("root_dir") or s.get("notebook_dir") or "/").resolve()
        if root == path or root in path.parents:
            return f"{s['url'].rstrip('/')}/lab/tree/{path.relative_to(root)}?token={s.get('token', '')}"
    return None


def start_jupyter(path: Path) -> str:
    """Start JupyterLab rooted at strategies/ (or the linked file's folder) and return its URL."""
    path = path.resolve()
    root = STRATEGIES.resolve() if STRATEGIES.resolve() in path.parents else path.parent
    log = open(LOGS / "jupyter.log", "a")
    subprocess.Popen([str(STRATEGIES / ".venv" / "bin" / "jupyter"), "lab", "--no-browser", f"--notebook-dir={root}"],
                     cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(40):
        time.sleep(0.5)
        url = jupyter_url(path)
        if url:
            return url
    raise RuntimeError("JupyterLab did not start; see desktop/data/logs/jupyter.log")
