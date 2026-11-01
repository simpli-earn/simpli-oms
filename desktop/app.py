"""SimpliEarn Desk: a desktop window to start the OMS and run, version, and schedule strategies.

    ../strategies/.venv/bin/python app.py

The window is ui/index.html; its buttons call the methods on Api through pywebview.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import webview

import desk
import history
import scheduler

sys.path.insert(0, str(desk.STRATEGIES))
import oms_client  # noqa: E402

BACKEND_URL = "http://127.0.0.1:8787"
DASHBOARD_URL = "http://localhost:5173"
FRONTEND_PID = desk.DATA / "frontend.pid"
BACKEND_PID = desk.STRATEGIES / ".logs" / "oms-backend.pid"
HEADERS = {"X-API-Key": oms_client.DEFAULT_KEY}


def _up(url: str) -> bool:
    try:
        requests.get(url, timeout=1.5)
        return True
    except requests.RequestException:
        return False


def _read_pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text())
    except (OSError, ValueError):
        return None
    return pid if desk.pid_alive(pid) else None


class Api:
    def __init__(self):
        self._oms_lock = threading.Lock()
        self._oms_busy = ""
        self._snaps: dict[str, tuple[float, dict]] = {}
        self._books: dict[str, tuple[float, dict | None]] = {}
        self._hist: dict[tuple, tuple[float, dict]] = {}
        self.window = None

    # OMS servers

    def oms_status(self) -> dict:
        backend, frontend = _up(f"{BACKEND_URL}/openapi.json"), _up(DASHBOARD_URL)
        return {
            "backend": backend,
            "frontend": frontend,
            "backend_ours": bool(_read_pid(BACKEND_PID)),
            "frontend_ours": bool(_read_pid(FRONTEND_PID)),
            "busy": self._oms_busy,
        }

    def start_oms(self) -> dict:
        with self._oms_lock:
            try:
                self._oms_busy = "starting backend"
                oms_client.ensure_server(BACKEND_URL)
                if not _up(DASHBOARD_URL):
                    self._oms_busy = "starting dashboard"
                    self._start_frontend()
            except Exception as exc:
                return {"ok": False, "error": str(exc), **self.oms_status()}
            finally:
                self._oms_busy = ""
        return {"ok": True, **self.oms_status()}

    def _start_frontend(self) -> None:
        node_bin = desk.config().get("node_bin", "")
        npm = Path(node_bin) / "npm" if node_bin else Path("npm")
        env = {**os.environ, "PATH": f"{node_bin}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
        log = open(desk.LOGS / "oms-frontend.log", "a")
        proc = subprocess.Popen([str(npm), "run", "dev"], cwd=desk.FRONTEND, env=env,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        FRONTEND_PID.write_text(str(proc.pid))
        for _ in range(60):
            if _up(DASHBOARD_URL):
                return
            if proc.poll() is not None:
                raise RuntimeError("dashboard exited early, see desktop/data/logs/oms-frontend.log")
            threading.Event().wait(0.5)
        raise RuntimeError("dashboard did not start within 30s")

    def stop_oms(self) -> dict:
        pid = _read_pid(FRONTEND_PID)
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        FRONTEND_PID.unlink(missing_ok=True)
        if _read_pid(BACKEND_PID):
            oms_client.stop_server()
        threading.Event().wait(1)
        return self.oms_status()

    def open_dashboard(self) -> None:
        subprocess.Popen(["open", DASHBOARD_URL])


    # strategies on this laptop

    def strategies(self) -> list[dict]:
        out = []
        for sid in desk.strategy_ids():
            try:
                s = desk.describe(sid)
            except (OSError, ValueError, KeyError) as exc:
                s = {"strategy_id": sid, "title": sid, "broken": str(exc), "checks": [{"level": "error", "msg": f"strategy.json: {exc}"}],
                     "versions": [], "exists": False, "kind": "", "source": "", "path": "", "folder": str(desk.folder(sid))}
            s["schedule"] = desk.get_schedule(sid)
            s["scheduled_loaded"] = scheduler.loaded(sid)
            s["last_run"] = desk.last_run(sid)
            out.append(s)
        return out

    def strategy_books(self) -> dict:
        """{strategy_id: {cash, invested, equity, positions, unrealized}} from the OMS, if it is up."""
        if not _up(f"{BACKEND_URL}/openapi.json"):
            return {}
        books = {}
        fetched = self.books(desk.strategy_ids())
        snaps = self.snapshots(sorted({p["symbol"] for b in fetched.values() if b for p in b["positions"]}))
        for sid, b in fetched.items():
            if b is None:
                books[sid] = {"registered": False}
                continue
            st = b["strategy"]
            perf = _performance(b, snaps)["summary"]
            books[sid] = {
                "registered": True,
                "budget": st["cash_budget"],
                "positions": len(b["positions"]),
                "unrealized": st["unrealized_pnl"],
                "realized": st["realized_pnl"],
                **perf,
            }
        return books

    # daily and total change

    def snapshots(self, symbols: list[str]) -> dict:
        """{symbol: {price, prev_close}} from Alpaca snapshots, cached for 30 seconds.

        prev_close is the close of the session before the latest one, so during and after a
        trading day "today" is that day's move; before the open it is the previous day's move.
        """
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockSnapshotRequest

        now = time.time()
        stocks = {s for s in symbols if STOCK.fullmatch(s)}  # option contracts have no stock snapshot
        need = sorted(s for s in stocks if now - self._snaps.get(s, (0, None))[0] > 30)
        if need:
            client = StockHistoricalDataClient(*oms_client.alpaca_keys())
            for feed in (DataFeed.SIP, DataFeed.IEX):
                try:
                    snaps = client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=need, feed=feed))
                    break
                except Exception:
                    snaps = {}
            for sym, snap in snaps.items():
                price = snap.latest_trade.price if snap.latest_trade else (snap.daily_bar.close if snap.daily_bar else None)
                prev = snap.previous_daily_bar.close if snap.previous_daily_bar else None
                self._snaps[sym] = (now, {"price": price, "prev_close": prev})
        return {s: self._snaps[s][1] for s in stocks if s in self._snaps}

    def strategy_performance(self, sid: str, book: dict | None = None) -> dict:
        """Day and total change for one strategy and each of its positions."""
        if book is None:
            res = self.oms("GET", f"/api/strategies/{sid}/book")
            if not res["ok"]:
                return {"ok": False, "error": res["error"]}
            book = res["data"]
        return {"ok": True, **_performance(book, self.snapshots([p["symbol"] for p in book["positions"]]))}

    def books(self, ids: list[str], max_age: float = 8) -> dict:
        """{id: book or None if not registered}. Fetched in parallel (each book is ~0.8s on the
        shared database) and cached briefly, since several views poll the same books."""
        now = time.time()
        need = [i for i in ids if now - self._books.get(i, (0, None))[0] > max_age]

        def fetch(sid):
            res = self.oms("GET", f"/api/strategies/{sid}/book")
            if res["ok"]:
                return sid, res["data"]
            return sid, None if res.get("status") == 404 else KeyError
        if need:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for sid, book in pool.map(fetch, need):
                    if book is not KeyError:  # keep the last good copy on a transient error
                        self._books[sid] = (now, book)
        return {i: self._books[i][1] for i in ids if i in self._books}

    def all_strategy_performance(self, ids: list[str] | None = None) -> dict:
        """{strategy_id: summary} for every strategy in the OMS (the Strategies tab)."""
        if ids is None:
            res = self.oms("GET", "/api/strategies")
            if not res["ok"]:
                return {}
            ids = [s["id"] for s in res["data"]]
        books = {k: v for k, v in self.books(ids).items() if v}
        snaps = self.snapshots(sorted({p["symbol"] for b in books.values() for p in b["positions"]}))
        return {sid: _performance(b, snaps)["summary"] for sid, b in books.items()}

    def oms_strategy_choices(self) -> dict:
        """OMS strategies for the New strategy dropdown, marking the ones already on this laptop."""
        res = self.oms("GET", "/api/strategies")
        if not res["ok"]:
            return {"ok": False, "error": res["error"], "choices": []}
        local = set(desk.strategy_ids())
        gone = desk.flags()  # deleted or removed strategies can't be picked again
        return {"ok": True, "choices": [
            {"id": s["id"], "cash_budget": s["cash_budget"], "cash_remaining": s["cash_remaining"], "local": s["id"] in local}
            for s in res["data"] if s["id"] not in gone
        ]}

    def choose_file(self) -> str | None:
        result = self.window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False,
            file_types=("Strategy code (*.py;*.ipynb)", "All files (*.*)"),
        )
        return result[0] if result else None

    def new_strategy(self, sid: str, origin: str, title: str = "", file: str | None = None,
                     register_budget: float | None = None) -> dict:
        """Set up a strategy folder. register_budget registers a brand-new id in the OMS first."""
        if register_budget:
            res = self.oms("POST", "/api/strategies", {"id": sid, "cash_budget": float(register_budget)})
            if not res["ok"] and res.get("status") != 409:
                return {"ok": False, "error": f"Registering {sid} failed: {res['error']}"}
        try:
            s = desk.create_strategy(sid, origin, title, file)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.open_code(sid)
        return {"ok": True, "strategy": s}

    # deleting and removing

    def strategy_status(self, sid: str) -> dict:
        """What deleting would do: never traded -> delete; traded -> remove and keep the record."""
        try:
            _, fills = history.strategy_record(sid)
        except ValueError:
            fills = None  # not in the OMS: only a local folder
        res = self.oms("GET", f"/api/strategies/{sid}/book")
        book = res["data"] if res["ok"] else None
        open_orders = [o for o in (book or {}).get("orders", []) if o["status"] not in oms_client.TERMINAL_STATUSES]
        perf = _performance(book, self.snapshots([p["symbol"] for p in book["positions"]]))["summary"] if book else None
        return {
            "ok": True, "strategy_id": sid, "in_oms": fills is not None, "fills": len(fills or []),
            "traded": bool(fills), "positions": len(book["positions"]) if book else 0,
            "position_value": perf["invested"] if perf else 0, "open_orders": len(open_orders),
            "local": (desk.folder(sid) / desk.MANIFEST).exists(),
            "linked": (desk.folder(sid) / desk.MANIFEST).exists() and desk.read_manifest(sid)["source"]["kind"] == "linked",
        }

    def delete_strategy(self, sid: str) -> dict:
        """Never traded: delete it from this app (records and local folder, to the Trash).
        Traded: remove it from My strategies, keep its code and folder, and list it under
        Strategies -> Removed with a snapshot of its final numbers."""
        st = self.strategy_status(sid)
        scheduler.remove(sid)
        desk.save_schedule(sid, 0, 0, [], False)
        if not st["traded"]:
            if st["local"]:
                from Foundation import NSURL, NSFileManager
                ok, _, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
                    NSURL.fileURLWithPath_(str(desk.folder(sid))), None, None)
                if not ok:
                    return {"ok": False, "error": f"Could not move the folder to the Trash: {err}"}
            if st["in_oms"]:
                desk.set_flag(sid, "deleted")
            return {"ok": True, "action": "deleted"}
        perf = self.strategy_performance(sid)
        book = self.oms("GET", f"/api/strategies/{sid}/book")
        final = {**(perf.get("summary") or {}), "fills": st["fills"], "positions": st["positions"]}
        if book["ok"]:
            s = book["data"]["strategy"]
            final.update(budget=s["cash_budget"], realized=s["realized_pnl"], unrealized=s["unrealized_pnl"])
        desk.set_flag(sid, "removed", final)
        desk.set_removed(sid, True)
        return {"ok": True, "action": "removed"}

    def restore_strategy(self, sid: str) -> dict:
        desk.set_flag(sid, None)
        desk.set_removed(sid, False)
        return {"ok": True}

    def strategy_flags(self) -> dict:
        return desk.flags()

    # value over time

    def strategy_history(self, sid: str, rng: str = "ALL") -> dict:
        key = ("s", sid, rng)
        if time.time() - self._hist.get(key, (0, None))[0] > 60:
            try:
                equity = (self.strategy_performance(sid).get("summary") or {}).get("equity")
                self._hist[key] = (time.time(), {"ok": True, **history.strategy_history(sid, rng, equity)})
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        return self._hist[key][1]

    def strategy_stats(self, ids: list[str]) -> dict:
        """{id: volatility, beta and alpha against SPY}. Rebuilt at most every 10 minutes, since
        each one reads every fill and a week of bars."""
        need = [i for i in ids if time.time() - self._hist.get(("stats", i), (0, None))[0] > 600]

        def fetch(sid):
            try:
                return sid, history.strategy_stats(sid)
            except ValueError as exc:
                return sid, {"ready": False, "reason": str(exc)}
            except Exception as exc:
                return sid, {"ready": False, "reason": f"Couldn't compute: {exc}"}
        if need:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for sid, stats in pool.map(fetch, need):
                    self._hist[("stats", sid)] = (time.time(), stats)
        return {i: self._hist[("stats", i)][1] for i in ids}

    def account_history(self, rng: str = "1M") -> dict:
        key = ("a", rng)
        if time.time() - self._hist.get(key, (0, None))[0] > 60:
            try:
                self._hist[key] = (time.time(), {"ok": True, **history.account_history(rng)})
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        return self._hist[key][1]

    def open_code(self, sid: str) -> dict:
        """Open the working code: notebooks in JupyterLab, scripts in your editor."""
        path = desk.source_path(sid)
        if not path.exists():
            return {"ok": False, "error": f"{path} not found"}
        if path.suffix == ".ipynb":
            try:
                url = desk.jupyter_url(path) or desk.start_jupyter(path)
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            subprocess.Popen(["open", url])
        else:
            editor = next((a for a in ("Visual Studio Code", "Cursor", "PyCharm", "PyCharm CE", "Sublime Text")
                           if Path(f"/Applications/{a}.app").exists()), None)
            subprocess.Popen(["open", "-a", editor, str(path)] if editor else ["open", "-t", str(path)])
        return {"ok": True}

    def reveal(self, sid: str) -> None:
        """Show the strategy folder, or the linked file wherever it lives."""
        s = desk.read_manifest(sid)
        if s["source"]["kind"] == "linked":
            subprocess.Popen(["open", "-R", str(desk.source_path(sid))])
        else:
            subprocess.Popen(["open", str(desk.folder(sid))])

    def open_strategies_folder(self) -> None:
        subprocess.Popen(["open", str(desk.STRATEGIES)])

    def template(self, kind: str) -> str:
        return (desk.TEMPLATES / f"new_strategy.{'ipynb' if kind == 'ipynb' else 'py'}").read_text()

    # versions

    def versions(self, sid: str) -> dict:
        s = desk.describe(sid)
        return {"strategy": s, "next_label": desk.next_label(sid)}

    def save_version(self, sid: str, label: str, name: str, note: str = "") -> dict:
        try:
            return {"ok": True, "version": desk.save_version(sid, label, name, note)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def delete_version(self, sid: str, label: str) -> dict:
        try:
            desk.delete_version(sid, label)
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def restore_version(self, sid: str, label: str) -> dict:
        try:
            desk.restore_version(sid, label)
            return {"ok": True}
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def set_scheduled_version(self, sid: str, label: str | None) -> dict:
        try:
            desk.set_scheduled_version(sid, label)
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def diff(self, sid: str, a: str, b: str = "working") -> dict:
        try:
            return {"ok": True, "text": desk.diff(sid, a, b)}
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    # runs

    def run_strategy(self, sid: str, dry_run: bool = False, version: str | None = None) -> dict:
        if sid not in desk.strategy_ids():
            return {"ok": False, "error": f"{sid} is not set up on this laptop"}
        run_id = desk.create_run(sid, "manual", dry_run, version)
        args = [str(desk.PYTHON), str(desk.DESKTOP / "runner.py"), sid, "--run-id", run_id]
        if dry_run:
            args.append("--dry-run")
        if version:
            args += ["--version", version]
        log = open(desk.LOGS / "runner.log", "a")
        proc = subprocess.Popen(args, cwd=desk.DESKTOP, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        desk.update_run(run_id, pid=proc.pid)
        return {"ok": True, "run_id": run_id}

    def stop_run(self, run_id: str) -> dict:
        run = desk.get_run(run_id)
        if run and run["status"] in desk.ACTIVE and run["pid"]:
            desk.stop_marker(run_id).touch()  # the runner reads this to report "stopped"
            try:
                os.killpg(run["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        return {"ok": True}

    def runs(self, limit: int = 60) -> list[dict]:
        return desk.recent_runs(limit)

    def run_log(self, run_id: str) -> dict:
        run = desk.get_run(run_id) or {}
        text = ""
        if run.get("log_path") and Path(run["log_path"]).is_file():
            text = Path(run["log_path"]).read_text()
        return {"run": run, "text": text}

    def reveal_output(self, run_id: str) -> None:
        run = desk.get_run(run_id) or {}
        path = run.get("output_path")
        if path and Path(path).exists():
            subprocess.Popen(["open", "-R", path])

    # schedules

    def set_schedule(self, sid: str, hour: int, minute: int, weekdays: list[int], enabled: bool) -> dict:
        try:
            scheduler.apply(sid, int(hour), int(minute), [int(d) for d in weekdays], bool(enabled))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "schedule": desk.get_schedule(sid), "loaded": scheduler.loaded(sid)}

    # OMS data for the Account / Strategies / New order tabs

    def oms(self, method: str, path: str, body: dict | None = None) -> dict:
        """Proxy one OMS API call: {ok, status, data} or {ok: False, error}."""
        if not path.startswith("/api/"):
            return {"ok": False, "error": "only /api/ paths"}
        try:
            r = requests.request(method, f"{BACKEND_URL}{path}", headers=HEADERS, json=body, timeout=30)
        except requests.RequestException:
            return {"ok": False, "status": 0, "error": "OMS backend is not running"}
        try:
            data = r.json()
        except ValueError:
            data = None
        if not r.ok:
            return {"ok": False, "status": r.status_code, "error": _detail(data) or r.reason}
        return {"ok": True, "status": r.status_code, "data": data}


STOCK = re.compile(r"[A-Z][A-Z.]{0,6}")
ET = ZoneInfo("America/New_York")


def _performance(book: dict, snaps: dict) -> dict:
    """Day and total change for a strategy book.

    Prices are the OMS's (Alpaca's position price, which includes extended hours). Position total %
    is against its average cost. Position day % is against the previous close, or against cost
    for a position first bought today. Strategy total % is equity against the
    cash budget; day % is the day's dollar change against equity before that change.
    """
    today = datetime.now(ET).date()
    first_fill: dict[str, datetime] = {}
    for f in book["fills"]:
        t = datetime.fromisoformat(f["filled_at"].replace("Z", "+00:00"))
        if f["side"] == "buy" and (f["symbol"] not in first_fill or t < first_fill[f["symbol"]]):
            first_fill[f["symbol"]] = t
    positions, day_total, value = {}, 0.0, 0.0
    for p in book["positions"]:
        sym, qty, avg = p["symbol"], p["qty"], p["avg_price"]
        snap = snaps.get(sym) or {}
        mult = 100 if p["asset_class"] == "us_option" else 1
        # Same price the OMS uses for unrealized P&L (Alpaca's position price); snapshot as fallback.
        if p["unrealized_pnl"] is not None and qty:
            price = avg + p["unrealized_pnl"] / (qty * mult)
        else:
            price = snap.get("price") or avg
        opened_today = sym in first_fill and first_fill[sym].astimezone(ET).date() == today
        base = avg if opened_today or not snap.get("prev_close") else snap["prev_close"]
        day = qty * (price - base) * mult
        day_total += day
        value += qty * price * mult
        positions[sym] = {
            "price": price, "prev_close": snap.get("prev_close"), "opened_today": opened_today,
            "day_change": day, "day_pct": (price / base - 1) * 100 if base else None,
            "total_change": qty * (price - avg) * mult, "total_pct": (price / avg - 1) * 100 if avg else None,
        }
    st = book["strategy"]
    cash = st["cash_remaining"] + oms_client.open_buy_cost(book["orders"])
    equity = cash + value
    start = equity - day_total
    return {
        "summary": {
            "cash": cash, "invested": value, "equity": equity,
            "day_change": day_total, "day_pct": day_total / start * 100 if start else None,
            "total_change": equity - st["cash_budget"],
            "total_pct": (equity / st["cash_budget"] - 1) * 100 if st["cash_budget"] else None,
        },
        "positions": positions,
    }


def _detail(body) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return " ".join(
            (".".join(str(p) for p in d.get("loc", []) if p != "body") + ": " if d.get("loc") else "") + d.get("msg", "")
            for d in detail if isinstance(d, dict)
        )
    return ""


ICON = desk.DESKTOP / "ui" / "icon.png"


def _brand_process() -> None:
    """The window runs inside python.org's Python.app; give it this app's name and Dock icon."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "SimpliEarn Desk"
        if ICON.exists():
            NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(str(ICON)))
    except Exception:
        pass


def main() -> None:
    api = Api()
    _brand_process()
    api.window = webview.create_window(
        "SimpliEarn Desk",
        url=str(desk.DESKTOP / "ui" / "index.html"),
        js_api=api,
        width=1240,
        height=840,
        min_size=(960, 620),
        background_color="#050505",
    )
    webview.start(_brand_process)


if __name__ == "__main__":
    main()
