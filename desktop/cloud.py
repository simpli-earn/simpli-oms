"""Cloud copies of strategy folders, kept in the shared OMS database through the backend.

Every strategy on this laptop is uploaded automatically while the app is open and the OMS is
running. Any laptop can then download it, so deleting the app or the whole folder doesn't lose
the code. What goes up:

    strategy.json, the code file, versions/, models/, and any other file in the folder
    the schedule's time and days (not whether it is on: each laptop turns that on itself)

runs/ (executed notebooks), hidden files, and caches stay local. A linked strategy uploads its
linked file, and a laptop that downloads it gets a stored copy.

Each upload bumps the cloud revision. This laptop remembers the revision and file hashes it last
synced (desk.db, cloud_sync), so it can tell three cases apart:

    changed here only      upload
    changed in the cloud   "update" (download it with one click)
    changed in both        "conflict" (keep this laptop's copy, or take the cloud's)
"""
from __future__ import annotations

import base64
import hashlib
import json
import platform
from pathlib import Path

import requests

import desk
import scheduler
from oms_client import DEFAULT_KEY, DEFAULT_URL

MAX_FILE_BYTES = 10 * 1024 * 1024  # the backend's limit
SKIP_PARTS = {"__pycache__", ".ipynb_checkpoints"}
LOCAL_KEYS = ("removed_at",)  # manifest fields that belong to this laptop only
HEADERS = {"X-API-Key": DEFAULT_KEY}


class CloudError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def _call(method: str, path: str, **kwargs) -> dict | list:
    try:
        r = requests.request(method, f"{DEFAULT_URL}{path}", headers=HEADERS, timeout=60, **kwargs)
    except requests.RequestException:
        raise CloudError(0, "OMS backend is not running")
    if not r.ok:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise CloudError(r.status_code, detail if isinstance(detail, str) else r.reason)
    return r.json()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- what this laptop has

def pack(sid: str) -> tuple[dict[str, bytes], list[str]]:
    """({path: bytes} to upload, [paths skipped for being over 10 MB])."""
    d = desk.folder(sid)
    m = desk.read_manifest(sid)
    files: dict[str, bytes] = {}
    skipped: list[str] = []
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if (not p.is_file() or rel.parts[0] == "runs" or rel.as_posix() == desk.MANIFEST or p.suffix == ".tmp"
                or any(part.startswith(".") or part in SKIP_PARTS for part in rel.parts)):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            skipped.append(rel.as_posix())
            continue
        files[rel.as_posix()] = p.read_bytes()
    shared = {k: v for k, v in m.items() if k not in LOCAL_KEYS}
    if m["source"]["kind"] == "linked":
        src = desk.source_path(sid, m)
        if src.is_file():
            files[src.name] = src.read_bytes()
        shared["source"] = {"kind": "local", "file": src.name}
    files[desk.MANIFEST] = (json.dumps(shared, indent=2) + "\n").encode()
    return files, skipped


def schedule_payload(sid: str) -> dict | None:
    s = desk.get_schedule(sid)
    if not s or not s["weekdays"]:
        return None
    return {"hour": s["hour"], "minute": s["minute"], "weekdays": s["weekdays"]}


def _base(sid: str) -> dict | None:
    with desk.db() as conn:
        row = conn.execute("select * from cloud_sync where strategy = ?", (sid,)).fetchone()
    if not row:
        return None
    return {"revision": row["revision"], "files": json.loads(row["files"]),
            "schedule": json.loads(row["schedule"]) if row["schedule"] else None}


def _save_base(sid: str, revision: int) -> None:
    """Record that this laptop's folder now matches cloud `revision`."""
    files, _ = pack(sid)
    with desk.db() as conn:
        conn.execute(
            "insert or replace into cloud_sync (strategy, revision, files, schedule, synced_at) values (?,?,?,?,?)",
            (sid, revision, json.dumps({p: _sha(b) for p, b in files.items()}),
             json.dumps(schedule_payload(sid)), desk.now()),
        )


# ---------------------------------------------------------------- syncing

def _upload(sid: str, files: dict[str, bytes], deleted: list[str], base_revision: int, force: bool = False) -> dict:
    body = {
        "base_revision": base_revision,
        "files": [{"path": p, "content_b64": base64.b64encode(b).decode()} for p, b in files.items()],
        "deleted": deleted,
        "schedule": schedule_payload(sid),
        "updated_by": platform.node(),
        "force": force,
    }
    out = _call("PUT", f"/api/strategies/{sid}/code", json=body)
    _save_base(sid, out["revision"])
    return out


def sync_one(sid: str, cloud: dict | None) -> dict:
    """Upload this laptop's changes to one strategy, or report why not. `cloud` is its entry
    from GET /api/strategy-code (None if it has never been uploaded)."""
    files, skipped = pack(sid)
    hashes = {p: _sha(b) for p, b in files.items()}
    sched = schedule_payload(sid)
    base = _base(sid)
    cloud_rev = cloud["revision"] if cloud else 0
    info = {"revision": cloud_rev, "updated_by": cloud and cloud["updated_by"],
            "updated_at": cloud and cloud["updated_at"], "skipped": skipped}
    try:
        if cloud_rev == 0:  # first upload, or the cloud copy is gone
            out = _upload(sid, files, [], 0)
            return {**info, "state": "synced", "revision": out["revision"], "uploaded": True}
        if base is None:  # set up here before cloud sync existed, or downloaded by hand
            remote = _call("GET", f"/api/strategies/{sid}/code", params={"content": "false"})
            if {f["path"]: f["sha256"] for f in remote["files"]} == hashes:
                _save_base(sid, cloud_rev)
                return {**info, "state": "synced"}
            return {**info, "state": "conflict"}
        local_changed = hashes != base["files"] or sched != base["schedule"]
        cloud_changed = cloud_rev != base["revision"]
        if not local_changed and not cloud_changed:
            return {**info, "state": "synced"}
        if cloud_changed:
            return {**info, "state": "conflict" if local_changed else "update"}
        changed = {p: b for p, b in files.items() if base["files"].get(p) != hashes[p]}
        out = _upload(sid, changed, sorted(set(base["files"]) - set(files)), base["revision"])
        return {**info, "state": "synced", "revision": out["revision"], "uploaded": True}
    except CloudError as exc:
        if exc.status == 404:
            return {**info, "state": "unregistered"}
        if exc.status == 409:
            return {**info, "state": "conflict"}
        return {**info, "state": "error", "error": str(exc)}


def sync_all() -> dict:
    """Upload every local strategy's changes. Returns what the app shows:
    {online, strategies: {id: {state, ...}}, available: [cloud strategies not on this laptop]}."""
    try:
        listing = _call("GET", "/api/strategy-code")
    except CloudError as exc:
        if exc.status == 404:  # a backend started before cloud backup existed
            raise CloudError(404, "The OMS backend is running older code. Click Stop OMS, then Start OMS") from exc
        raise
    cloud = {c["strategy_id"]: c for c in listing}
    local = desk.strategy_ids(include_removed=True)
    out = {}
    for sid in local:
        try:
            out[sid] = sync_one(sid, cloud.get(sid))
        except (OSError, ValueError, KeyError) as exc:
            out[sid] = {"state": "error", "error": f"Couldn't read the folder: {exc}"}
    gone = {sid for sid, f in desk.flags().items() if f["state"] == "deleted"}
    available = [c for sid, c in cloud.items() if sid not in local and sid not in gone]
    return {"online": True, "strategies": out, "available": available}


# ---------------------------------------------------------------- downloading

def download(sid: str) -> None:
    """Write the cloud copy into strategies/<sid>/: a fresh setup, or an update that replaces
    this laptop's copy. A linked strategy keeps its link and gets the new code written there."""
    remote = _call("GET", f"/api/strategies/{sid}/code")
    files = {f["path"]: base64.b64decode(f["content_b64"]) for f in remote["files"]}
    if desk.MANIFEST not in files:
        raise CloudError(422, "the cloud copy has no strategy.json")
    d = desk.folder(sid)
    local_m = desk.read_manifest(sid) if (d / desk.MANIFEST).exists() else None
    manifest = json.loads(files.pop(desk.MANIFEST))
    if local_m:
        if local_m["source"]["kind"] == "linked":
            code = files.pop(manifest["source"]["file"], None)
            if code is not None:
                desk.source_path(sid, local_m).write_bytes(code)
            manifest["source"] = local_m["source"]
        manifest.update({k: local_m[k] for k in LOCAL_KEYS if k in local_m})
        base = _base(sid)
        for p in (base or {}).get("files", {}):  # files the cloud copy no longer has
            if p not in files and p != desk.MANIFEST and p != manifest["source"].get("file"):
                (d / p).unlink(missing_ok=True)
                parent = (d / p).parent
                while parent != d and parent.is_dir() and not any(parent.iterdir()):  # e.g. an empty versions/
                    parent.rmdir()
                    parent = parent.parent
    root = d.resolve()
    for p, data in files.items():
        target = (d / p).resolve()
        if root not in target.parents:
            raise CloudError(422, f"bad file path in the cloud copy: {p}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    d.mkdir(parents=True, exist_ok=True)
    desk.write_manifest(sid, manifest)

    s = remote.get("schedule")
    if s and s.get("weekdays"):
        mine = desk.get_schedule(sid)
        if mine and mine["enabled"]:
            scheduler.apply(sid, s["hour"], s["minute"], s["weekdays"], True)
        else:  # a new laptop gets the time and days, and turns the schedule on itself
            desk.save_schedule(sid, s["hour"], s["minute"], s["weekdays"], False)
    _save_base(sid, remote["revision"])


def keep_mine(sid: str) -> dict:
    """Resolve a conflict by uploading this laptop's copy over the cloud's."""
    files, _ = pack(sid)
    try:
        remote = _call("GET", f"/api/strategies/{sid}/code", params={"content": "false"})
        deleted = sorted({f["path"] for f in remote["files"]} - set(files))
        revision = remote["revision"]
    except CloudError as exc:
        if exc.status != 404:
            raise
        deleted, revision = [], 0
    return _upload(sid, files, deleted, revision, force=True)
