#!/usr/bin/env python
"""bd-auto web frontend.

Serves index.html and exposes a small JSON API. Everything is chosen up front —
the client's budget, the package tier (price) and which service areas to feature
— so one request runs the whole-domain audit AND builds the combined deck:

    GET  /                      -> the frontend (index.html)
    POST /api/audit             -> {url, groq_key?, groq_model?, pagespeed_key?,
                                     seoptimer_key?, max_pages?, tier?,
                                     discovery?:{...}}
                                   runs the whole-domain audit + builds the deck
                                   in a background thread, returns {job_id}.
                                   ``tier`` (starter|growth|professional|enterprise)
                                   forces the package; omit/"auto" lets the budget
                                   decide. The service areas to feature come in on
                                   discovery.include_categories.
    GET  /api/status/<job_id>   -> live progress + result paths
    GET  /reports/<path>        -> generated report files (path-traversal guarded)

Run it:

    .\\.venv\\Scripts\\python.exe server.py
    # then open http://127.0.0.1:8000

Uses only the standard library plus the existing auditor package — no Flask.
Playwright's sync API is happy in the background thread (no asyncio loop there).
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from auditor import packages as pk
from auditor.discovery import Discovery
from auditor.pipeline import generate_deliverables
from auditor.runner import run_audit

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
# Bind to 0.0.0.0 in hosted environments (Render, Docker, etc.); the platform
# injects the port to listen on via $PORT. Falls back to localhost:8000 locally.
HOST = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))


# job_id -> job dict. Only JSON-safe keys are stored; the whole job is returned
# to the browser as-is.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _slug(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-") or "site"


def _rel(path) -> str | None:
    return str(path.relative_to(REPORTS)).replace("\\", "/") if path else None


def _run_job(job_id: str, url: str, groq_key: str | None,
             groq_model: str | None, pagespeed_key: str | None,
             max_pages: int | None,
             tier: str | None, discovery: dict | None) -> None:
    job = JOBS[job_id]
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = REPORTS / f"{_slug(url)}-{stamp}"
        assets = base / "assets"

        config: dict = {}
        if pagespeed_key:
            config["pagespeed_key"] = pagespeed_key
        if max_pages:
            config["max_pages"] = max_pages

        def progress(msg, cat):
            with JOBS_LOCK:
                job["progress"].append({"msg": msg, "cat": cat})

        disc = Discovery.from_dict(discovery)

        job["status"] = "running"
        ctx, results = run_audit(url, assets, config=config, progress=progress)
        job["domain"] = ctx.domain
        job["url"] = ctx.url

        out = generate_deliverables(
            ctx, results, base,
            discovery=disc,
            groq_key=groq_key,
            groq_model=groq_model,
            tier=tier,                      # None -> budget decides
            progress=progress,
        )

        counts: dict[str, int] = {}
        for r in results:
            counts[r.status.value] = counts.get(r.status.value, 0) + 1

        ai = out.get("ai") or {}
        metrics = out.get("metrics") or {}
        proj = out.get("projection") or {}
        crawl = getattr(ctx, "crawl", None)
        competitors = out.get("competitors") or []

        with JOBS_LOCK:
            job["status"] = "done"
            job["counts"] = counts
            job["ai_ok"] = bool(out.get("ai"))
            job["ai_grade"] = ai.get("grade")
            job["ai_score"] = ai.get("score")
            job["deck_html"] = _rel(out.get("deck_html"))
            job["deck_pdf"] = _rel(out.get("deck_pdf"))
            job["pitch_ok"] = bool(out.get("pitch"))
            job["chosen_tier"] = out.get("tier")
            job["tier_price"] = pk.price_line(out["tier"]) if out.get("tier") in pk.TIERS else None
            job["metrics_summary"] = {
                "organic_traffic": metrics.get("organic_traffic"),
                "referring_domains": metrics.get("referring_domains"),
                "pages_crawled": getattr(crawl, "pages_crawled", None),
            }
            job["opportunity"] = ({"visits": proj.get("added_visits"),
                                   "leads": proj.get("added_leads")} if proj else None)
            job["competitors"] = [
                {"domain": c.get("domain"), "loaded": bool(c.get("loaded"))}
                for c in competitors
            ]
    except Exception as exc:  # noqa: BLE001
        with JOBS_LOCK:
            job["status"] = "error"
            job["error"] = f"{exc.__class__.__name__}: {exc}"


class Handler(BaseHTTPRequestHandler):
    server_version = "bd-auto/1.0"

    # --- helpers ---------------------------------------------------------
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None

    def log_message(self, fmt, *args):  # quieter console
        return

    # --- routes ----------------------------------------------------------
    def do_GET(self):
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._file(ROOT / "index.html")
            return
        if route == "/sample_deck.html":   # pre-built deck for the UI's Demo button
            self._file(ROOT / "sample_deck.html")
            return
        if route.startswith("/api/status/"):
            job_id = route.rsplit("/", 1)[-1]
            job = JOBS.get(job_id)
            if not job:
                self._json(404, {"error": "unknown job"})
                return
            with JOBS_LOCK:
                self._json(200, dict(job))
            return
        if route.startswith("/reports/"):
            rel = unquote(route[len("/reports/"):])
            target = (REPORTS / rel).resolve()
            try:
                target.relative_to(REPORTS.resolve())  # block path traversal
            except ValueError:
                self._json(403, {"error": "forbidden"})
                return
            self._file(target)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/audit":
            self._audit()
            return
        self._json(404, {"error": "not found"})

    def _audit(self):
        payload = self._body_json()
        if payload is None:
            self._json(400, {"error": "invalid JSON"})
            return
        url = (payload.get("url") or "").strip()
        if not url:
            self._json(400, {"error": "url is required"})
            return

        discovery = payload.get("discovery")
        if not isinstance(discovery, dict):
            discovery = None

        try:
            max_pages = int(payload.get("max_pages") or 0) or None
        except (TypeError, ValueError):
            max_pages = None

        # Operator's package pick. "auto"/blank -> let the budget decide.
        tier = (payload.get("tier") or "").strip().lower()
        if tier not in pk.TIERS:
            tier = None

        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"status": "queued", "progress": [], "url": url,
                        "domain": "", "error": None}
        threading.Thread(
            target=_run_job,
            args=(job_id, url,
                  (payload.get("groq_key") or "").strip() or None,
                  (payload.get("groq_model") or "").strip() or None,
                  (payload.get("pagespeed_key") or "").strip() or None,
                  max_pages, tier, discovery),
            daemon=True,
        ).start()
        self._json(200, {"job_id": job_id})


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"\n  bd-auto web frontend running")
    print(f"  Open  http://{HOST}:{PORT}  in your browser")
    print("  Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
