"""Core data model and check registry for bd-auto."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse


class Status(str, Enum):
    PASS = "pass"      # check ran, result is healthy
    WARN = "warn"      # check ran, something to look at
    FAIL = "fail"      # check ran, clear problem
    INFO = "info"      # informational / manual follow-up needed
    ERROR = "error"    # the check itself could not complete

    @property
    def label(self) -> str:
        return {
            "pass": "PASS",
            "warn": "WARN",
            "fail": "FAIL",
            "info": "INFO",
            "error": "ERROR",
        }[self.value]


@dataclass
class CheckResult:
    """The outcome of a single audit check."""

    id: str
    category: str
    title: str
    status: Status
    summary: str
    details: list[tuple[str, str]] = field(default_factory=list)
    source: str = ""                 # the tool/site this check corresponds to
    evidence_url: Optional[str] = None
    screenshot: Optional[str] = None  # filename relative to the report's assets dir
    # extra evidence images shown beneath the primary one: (caption, filename)
    extra_screenshots: list[tuple[str, str]] = field(default_factory=list)
    error: Optional[str] = None

    @staticmethod
    def errored(spec: "CheckSpec", exc: Exception) -> "CheckResult":
        return CheckResult(
            id=spec.id,
            category=spec.category,
            title=spec.title,
            status=Status.ERROR,
            summary=f"Check could not complete: {exc.__class__.__name__}: {exc}",
            source=spec.source,
            error=str(exc),
        )


@dataclass
class CheckSpec:
    id: str
    category: str
    title: str
    source: str
    fn: Callable[["AuditContext"], object]  # returns CheckResult or list[CheckResult]


# --- Registry -------------------------------------------------------------

REGISTRY: list[CheckSpec] = []


def check(id: str, category: str, title: str, source: str = ""):
    """Decorator registering a check function in execution order."""

    def deco(fn: Callable[["AuditContext"], object]):
        REGISTRY.append(CheckSpec(id=id, category=category, title=title, source=source, fn=fn))
        return fn

    return deco


# --- Audit context --------------------------------------------------------


@dataclass
class AuditContext:
    """Everything a check needs to run. Shared across all checks for one audit."""

    url: str                  # normalized full URL, e.g. https://example.com/
    domain: str               # registrable host, e.g. example.com
    scheme: str               # http / https
    browser: object           # Playwright Browser
    api: object               # Playwright APIRequestContext (HTTP through browser stack)
    screenshots_dir: Path
    config: dict = field(default_factory=dict)

    # Populated by the runner after fetching the homepage once:
    home_html: str = ""
    home_soup: object = None          # BeautifulSoup of the rendered homepage
    home_status: int = 0
    home_headers: dict = field(default_factory=dict)
    home_ttfb_ms: float = 0.0
    home_load_ms: float = 0.0
    home_error: Optional[str] = None

    # Whole-domain crawl (auditor/crawler.py SiteCrawl). Populated by the runner
    # before the checks run; site-wide checks read its aggregates.
    crawl: object = None

    def new_page(self):
        """Create a fresh browser page/tab for a check that navigates elsewhere."""
        ctx = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        return ctx

    def screenshot_path(self, name: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        return self.screenshots_dir / f"{safe}.png"


# --- Text helpers ---------------------------------------------------------

# Pictographic emoji / dingbat ranges. Deliberately does NOT touch currency
# symbols (₹ €), arrows (→ ←) or typographic marks (– · —), which are legitimate
# in a professional document — only the decorative emoji a model sometimes adds.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emoji, pictographs, supplemental symbols
    "\U00002600-\U000027BF"   # misc symbols + dingbats (☀ ★ ✅ ❌ ✂ …)
    "\U00002B00-\U00002BFF"   # misc symbols & arrows (⭐ ⬆ …)
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "\U0000200D"              # zero-width joiner
    "\U000020E3"              # combining enclosing keycap
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Remove decorative emoji from model-written prose, keeping it professional.

    Returns the input unchanged when it is empty or not a string. Collapses the
    run of whitespace an emoji leaves behind so "Be a Winner 🏆 now" doesn't
    become "Be a Winner  now".
    """
    if not text or not isinstance(text, str):
        return text
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


# --- URL helpers ----------------------------------------------------------


def normalize_url(raw: str) -> tuple[str, str, str]:
    """Return (full_url, host, scheme) for a user-supplied URL or bare domain."""
    raw = raw.strip()
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    p = urlparse(raw)
    scheme = p.scheme.lower()
    host = p.netloc.lower()
    path = p.path or "/"
    full = urlunparse((scheme, host, path, "", p.query, ""))
    bare = host[4:] if host.startswith("www.") else host
    return full, bare, scheme
