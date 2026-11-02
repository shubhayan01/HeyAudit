"""Audit orchestrator: launches the browser, loads the homepage once, runs all
registered checks with isolation, and returns the collected results.
"""

from __future__ import annotations

import time
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from . import checks  # noqa: F401 - triggers check registration
from .core import REGISTRY, AuditContext, CheckResult, Status, normalize_url
from .crawler import crawl_site

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _load_homepage(ctx: AuditContext) -> None:
    """Render the homepage once; populate shared DOM/timing/header fields."""
    bctx = ctx.new_page()
    page = bctx.new_page()
    try:
        start = time.perf_counter()
        resp = page.goto(ctx.url, wait_until="domcontentloaded", timeout=45000)
        ttfb = (time.perf_counter() - start) * 1000
        try:
            page.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        load = (time.perf_counter() - start) * 1000
        ctx.home_html = page.content()
        ctx.home_soup = BeautifulSoup(ctx.home_html, "lxml")
        ctx.home_status = resp.status if resp else 0
        ctx.home_headers = dict(resp.headers) if resp else {}
        ctx.home_ttfb_ms = ttfb
        ctx.home_load_ms = load
        # capture a homepage screenshot for the report header
        try:
            page.screenshot(path=str(ctx.screenshot_path("_homepage")), full_page=False)
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        ctx.home_error = f"{exc.__class__.__name__}: {exc}"
    finally:
        try:
            bctx.close()
        except Exception:
            pass


def run_audit(target: str, screenshots_dir: Path, config: dict | None = None,
              progress=None) -> tuple[AuditContext, list[CheckResult]]:
    config = config or {}
    full, domain, scheme = normalize_url(target)
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    results: list[CheckResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        api = p.request.new_context(
            user_agent=UA,
            ignore_https_errors=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        ctx = AuditContext(
            url=full, domain=domain, scheme=scheme,
            browser=browser, api=api, screenshots_dir=screenshots_dir, config=config,
        )

        if progress:
            progress("Loading homepage", domain)
        _load_homepage(ctx)

        # Crawl the whole domain (sitemap + internal links) so the on-page and
        # technical checks report site-wide extent, not just the homepage.
        max_pages = int(config.get("max_pages", 500) or 500)
        if progress:
            progress(f"Crawling the whole site (up to {max_pages} pages)", domain)
        try:
            ctx.crawl = crawl_site(ctx, max_pages=max_pages, progress=progress)
        except Exception as exc:  # noqa: BLE001 - a crawl failure must not abort the audit
            ctx.crawl = None
            if progress:
                progress(f"Site crawl skipped: {exc.__class__.__name__}", domain)

        total = len(REGISTRY)
        for i, spec in enumerate(REGISTRY, 1):
            if progress:
                progress(f"[{i}/{total}] {spec.title}", spec.category)
            try:
                out = spec.fn(ctx)
            except Exception as exc:  # noqa: BLE001 - isolate every check
                out = CheckResult.errored(spec, exc)
            if isinstance(out, list):
                results.extend(out)
            elif out is not None:
                results.append(out)

        try:
            api.dispose()
        except Exception:
            pass
        browser.close()

    return ctx, results
