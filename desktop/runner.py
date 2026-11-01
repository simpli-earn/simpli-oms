"""Run one strategy and record the result in the Desk run history.

    python runner.py zscore-spy                        # manual run of the working code
    python runner.py zscore-spy --version v2           # run a saved version
    python runner.py zscore-spy --trigger scheduled    # what launchd calls (uses the scheduled version, if set)
    python runner.py zscore-spy --dry-run              # print orders, send nothing

A .py strategy runs as a script and a .ipynb runs every cell top to bottom. Either way the code
runs from its own folder with the strategies environment, and gets SIMPLI_STRATEGY_ID,
SIMPLI_VERSION, and (for dry runs) OMS_DRY_RUN=1. Only one run per strategy at a time.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import desk

ORDER_SENT = re.compile(r"^(buy|sell|mleg)\b.* -> \w+ \(order ", re.M)
ORDER_PLANNED = re.compile(r"would POST /api/orders", re.M)
FAILED_ORDER = re.compile(r"^\s+(buy|sell) \S+ failed:", re.M)
PLAN_ONLY = re.compile(r"showing the plan only", re.I)


def classify(text: str, error: str | None, dry_run: bool) -> tuple[str, str, int]:
    sent = len(ORDER_SENT.findall(text))
    planned = len(ORDER_PLANNED.findall(text))
    failed = len(FAILED_ORDER.findall(text))
    if error:
        return "failed", error, sent
    if PLAN_ONLY.search(text):
        return "plan_only", f"market closed, plan only ({planned} orders planned)", 0
    if dry_run:
        return "succeeded", f"test run, {planned} orders planned", 0
    summary = f"{sent} orders sent" + (f", {failed} rejected" if failed else "")
    if sent == 0 and not failed:
        summary = "no trades needed"
    return "succeeded", summary, sent


def nb_text(nb) -> str:
    parts = []
    for cell in nb.cells:
        for out in cell.get("outputs", []):
            if out.get("output_type") == "stream":
                parts.append(out.get("text", ""))
            elif out.get("output_type") in ("execute_result", "display_data"):
                parts.append(out.get("data", {}).get("text/plain", "") + "\n")
            elif out.get("output_type") == "error":
                parts.append(f"\n{out.get('ename')}: {out.get('evalue')}\n")
    return "".join(parts)


def run_script(code: Path, cwd: Path, log_path: Path, run_id: str) -> tuple[str, str | None]:
    proc = subprocess.Popen([str(desk.PYTHON), "-u", str(code)], cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    with open(log_path, "w") as log:
        for line in proc.stdout:
            lines.append(line)
            log.write(line)
            log.flush()
    rc = proc.wait()
    text = "".join(lines)
    if rc == 0:
        return text, None
    tail = [l.strip() for l in lines if l.strip()]
    return text, (tail[-1] if tail else f"exited with code {rc}")


def run_notebook(code: Path, cwd: Path, log_path: Path, out_path: Path, run_id: str) -> tuple[str, str | None]:
    import nbformat
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError

    nb = nbformat.read(code, as_version=4)
    client = NotebookClient(nb, timeout=900, kernel_name=desk.KERNEL, resources={"metadata": {"path": str(cwd)}})
    code_cells = [i for i, c in enumerate(nb.cells) if c.cell_type == "code"]

    def progress(cell, cell_index, execute_reply):  # live log and "cell 3 of 9" while running
        log_path.write_text(nb_text(nb))
        done = sum(1 for i in code_cells if i <= cell_index)
        desk.update_run(run_id, summary=f"running cell {done} of {len(code_cells)}")

    client.on_cell_executed = progress
    error = None
    try:
        client.execute()
    except CellExecutionError as exc:
        error = (getattr(exc, "ename", "") + ": " + getattr(exc, "evalue", "")).strip(": ") or str(exc).splitlines()[0]
    except KeyboardInterrupt:
        error = "stopped"
    except Exception as exc:  # kernel died, timeout, missing kernel
        error = f"{type(exc).__name__}: {exc}"
    finally:
        nbformat.write(nb, out_path)
    text = nb_text(nb)
    log_path.write_text(text)
    return text, error


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--trigger", default="manual", choices=["manual", "scheduled"])
    ap.add_argument("--version", help="saved version label to run instead of the working code")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run-id", help="history row created by the app before launching")
    args = ap.parse_args()
    sid = args.strategy

    try:
        manifest = desk.read_manifest(sid)
    except (OSError, ValueError):
        run_id = args.run_id or desk.create_run(sid, args.trigger, args.dry_run)
        desk.update_run(run_id, status="failed", summary=f"{sid} is not set up in strategies/", finished_at=desk.now())
        return 1
    version = args.version or (manifest.get("scheduled_version") if args.trigger == "scheduled" else None)
    run_id = args.run_id or desk.create_run(sid, args.trigger, args.dry_run, version)
    desk.update_run(run_id, pid=os.getpid())

    def fail(summary: str) -> int:
        desk.update_run(run_id, status="failed", summary=summary[:300], finished_at=desk.now())
        return 1

    working = desk.source_path(sid, manifest)
    try:
        code = desk.version_file(sid, version) if version else working
    except ValueError as exc:
        return fail(str(exc))
    errors = [c["msg"] for c in desk.check_code(code, sid) if c["level"] == "error"]
    if errors:
        return fail(errors[0])
    if not version:  # name the run after the saved version it matches, if any
        h = desk.code_hash(code)
        version = next((v["label"] for v in reversed(manifest.get("versions", [])) if v["hash"] == h), None)
    cwd = working.parent if working.parent.exists() else desk.folder(sid)

    lock = open(desk.DATA / f".{sid}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        desk.update_run(run_id, status="skipped", summary="another run of this strategy was in progress",
                        finished_at=desk.now())
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    tag = f"{'_' + version if version else ''}{'_dry' if args.dry_run else ''}"
    log_path = desk.LOGS / f"{sid}_{stamp}{tag}.txt"
    out_path = desk.runs_dir(sid) / f"{code.stem}_{stamp}{tag}.ipynb" if code.suffix == ".ipynb" else log_path
    desk.update_run(run_id, status="running", output_path=str(out_path), log_path=str(log_path),
                    version=version, code_hash=desk.code_hash(code), summary="starting")

    os.environ["SIMPLI_STRATEGY_ID"] = sid
    os.environ["SIMPLI_VERSION"] = version or "working"
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(desk.STRATEGIES), os.environ.get("PYTHONPATH")]))
    os.environ.pop("OMS_DRY_RUN", None)
    if args.dry_run:
        os.environ["OMS_DRY_RUN"] = "1"

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    sys.path.insert(0, str(desk.STRATEGIES))
    import oms_client
    try:  # the OMS may be down when the app is closed; the run starts it and stops it after
        oms_client.ensure_server()
    except RuntimeError as exc:
        return fail(f"couldn't start the OMS: {exc}")
    try:
        if code.suffix == ".ipynb":
            text, error = run_notebook(code, cwd, log_path, out_path, run_id)
        else:
            text, error = run_script(code, cwd, log_path, run_id)
    except KeyboardInterrupt:
        text, error = (log_path.read_text() if log_path.exists() else ""), "stopped"
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # Stop from the app leaves a marker and then signals the process group; the child dies with
    # it and can surface that as any error, so the marker decides the status.
    marker = desk.stop_marker(run_id)
    if marker.exists():
        marker.unlink()
        error = "stopped"
    if error == "stopped":
        status, summary, orders = "stopped", "stopped from the app", len(ORDER_SENT.findall(text))
    else:
        status, summary, orders = classify(text, error, args.dry_run)
    desk.update_run(run_id, status=status, summary=summary[:300], orders=orders, finished_at=desk.now())
    print(f"{sid}: {status} - {summary}")
    try:  # so other laptops see this run even when the app is closed here
        import cloud
        cloud.report_installs(app_open=desk.app_open())
    except Exception:
        pass
    if not desk.oms_needed(except_run=run_id):
        oms_client.stop_server()
    return 0 if status in ("succeeded", "plan_only") else 1


if __name__ == "__main__":
    sys.exit(main())
