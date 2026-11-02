"""Whole-domain crawler — the site-wide inventory the audit is built on.

bd-auto used to look only at the homepage. A SEMrush-style audit needs the whole
site: every page's title, meta description, headings, canonical, indexability,
word count, image alt coverage and HTTP status, plus the broken internal links
between them. This module discovers and fetches up to ``max_pages`` HTML pages
(sitemap first, then a breadth-first walk of internal links) and rolls the
per-page facts up into site-wide aggregates the checks and the deck read from.

It fetches over Playwright's HTTP stack (``ctx.api``) rather than rendering each
page in a browser tab — rendering 500 pages would take far too long, and the
on-page signals we need live in the served HTML. Every fetch is wrapped so a bad
page never aborts the crawl.
"""

from __future__ import annotations

import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urldefrag, urlparse

from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 bd-auto/1.0"
)

# Paths that are never worth crawling (assets, feeds, admin, common junk).
_SKIP_EXT = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|json|xml|pdf|zip|gz|rar|mp4|mp3|"
    r"webm|avi|mov|woff2?|ttf|eot|csv|xlsx?|docx?|pptx?)(\?|$)", re.I)
_SKIP_PATH = re.compile(r"/(wp-admin|wp-json|wp-content/uploads|cdn-cgi|feed)(/|$)", re.I)

THIN_WORDS = 300          # below this a page is "thin" content
SLOW_MS = 1500            # a page slower than this to first byte is flagged


@dataclass
class Page:
    """Everything the audit needs to know about one crawled URL."""

    url: str
    status: int = 0
    ok: bool = False
    is_html: bool = False
    title: str = ""
    meta_description: str = ""
    h1_count: int = 0
    h1_text: str = ""
    canonical: str = ""
    self_canonical: bool = True     # canonical points at this page (or none)
    word_count: int = 0
    images_total: int = 0
    images_missing_alt: int = 0
    internal_links: int = 0
    external_links: int = 0
    noindex: bool = False           # meta robots / X-Robots-Tag noindex
    response_ms: float = 0.0
    error: str = ""


@dataclass
class SiteCrawl:
    """The crawl result: every page plus rolled-up, site-wide aggregates."""

    domain: str
    pages: list[Page] = field(default_factory=list)
    broken: list[dict] = field(default_factory=list)   # {url, status, found_on}
    sitemap_urls: int = 0
    discovered: int = 0
    truncated: bool = False         # hit the page cap before finishing
    duration_s: float = 0.0
    agg: dict = field(default_factory=dict)

    @property
    def ok_pages(self) -> list[Page]:
        return [p for p in self.pages if p.ok and p.is_html]

    @property
    def pages_crawled(self) -> int:
        return len(self.ok_pages)


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------

def _same_site(url: str, domain: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host == domain or host == "www." + domain


def _crawlable(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    if _SKIP_EXT.search(p.path) or _SKIP_PATH.search(p.path):
        return False
    return True


def _canon(url: str) -> str:
    """Normalise for de-duplication: drop fragment + trailing slash."""
    url = urldefrag(url)[0]
    if len(url) > 1 and url.endswith("/"):
        url = url[:-1]
    return url


# --------------------------------------------------------------------------
# Fetch + parse
# --------------------------------------------------------------------------

def _get(ctx, url: str, timeout: int = 20000):
    try:
        return ctx.api.get(url, headers={"User-Agent": UA}, timeout=timeout,
                           fail_on_status_code=False)
    except Exception:
        return None


def _visible_words(soup: BeautifulSoup) -> int:
    s = BeautifulSoup(str(soup), "lxml")
    for tag in s(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", s.get_text(" ")).strip()
    return len(text.split()) if text else 0


def _sitemap_urls(ctx, domain: str, scheme: str, limit: int) -> list[str]:
    """Collect page URLs from sitemap.xml / sitemap_index.xml (one level of index)."""
    found: list[str] = []
    seen: set[str] = set()
    queue = deque([f"{scheme}://{domain}/sitemap.xml",
                   f"{scheme}://{domain}/sitemap_index.xml"])
    visited_maps: set[str] = set()
    while queue and len(found) < limit:
        sm = queue.popleft()
        if sm in visited_maps:
            continue
        visited_maps.add(sm)
        r = _get(ctx, sm, timeout=25000)
        if not r or r.status >= 400:
            continue
        body = r.text()
        if "<sitemapindex" in body:
            for loc in re.findall(r"<loc>\s*([^<\s]+)", body):
                if loc.endswith(".xml") and len(visited_maps) < 40:
                    queue.append(loc.strip())
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)", body):
            u = _canon(loc.strip())
            if u in seen or not _same_site(u, domain) or not _crawlable(u):
                continue
            seen.add(u)
            found.append(u)
            if len(found) >= limit:
                break
    return found


def _parse_page(url: str, resp, domain: str) -> Page:
    pg = Page(url=url, status=getattr(resp, "status", 0))
    pg.ok = 200 <= pg.status < 300
    headers = {}
    try:
        headers = {k.lower(): v for k, v in resp.headers.items()}
    except Exception:
        pass
    ctype = headers.get("content-type", "")
    pg.is_html = "html" in ctype.lower() or not ctype
    if not pg.ok or not pg.is_html:
        return pg
    try:
        body = resp.text()
        soup = BeautifulSoup(body, "lxml")
    except Exception as exc:  # noqa: BLE001
        pg.error = f"{exc.__class__.__name__}: {exc}"
        return pg

    t = soup.find("title")
    pg.title = t.get_text(strip=True) if t else ""
    m = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    pg.meta_description = (m.get("content") or "").strip() if m else ""

    h1s = soup.find_all("h1")
    pg.h1_count = len(h1s)
    pg.h1_text = h1s[0].get_text(strip=True) if h1s else ""

    link = next((l for l in soup.find_all("link")
                 if "canonical" in [x.lower() for x in (l.get("rel") or [])]), None)
    if link:
        pg.canonical = (link.get("href") or "").strip()
        pg.self_canonical = _canon(urljoin(url, pg.canonical)) == _canon(url) if pg.canonical else True

    # Indexability: meta robots noindex OR X-Robots-Tag noindex header.
    mr = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    robots_val = ((mr.get("content") or "") if mr else "") + " " + headers.get("x-robots-tag", "")
    pg.noindex = "noindex" in robots_val.lower()

    pg.word_count = _visible_words(soup)

    imgs = soup.find_all("img")
    pg.images_total = len(imgs)
    pg.images_missing_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())

    return pg, soup  # type: ignore[return-value]


def _links_from(soup: BeautifulSoup, base_url: str, domain: str) -> tuple[list[str], int, int]:
    """(internal crawlable URLs, internal_count, external_count) on a page."""
    internal_urls: list[str] = []
    internal = external = 0
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if h.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        full = _canon(urljoin(base_url, h))
        if _same_site(full, domain):
            internal += 1
            if _crawlable(full):
                internal_urls.append(full)
        else:
            external += 1
    return internal_urls, internal, external


# --------------------------------------------------------------------------
# Crawl
# --------------------------------------------------------------------------

def crawl_site(ctx, max_pages: int = 500, progress=None) -> SiteCrawl:
    """Discover + fetch up to ``max_pages`` HTML pages and aggregate the findings.

    Never raises: any per-page failure is recorded on the Page and the crawl
    continues. Returns a populated :class:`SiteCrawl` even if the site is
    unreachable (``pages`` empty, ``agg`` zeroed).
    """
    start = time.perf_counter()
    domain = ctx.domain
    scheme = ctx.scheme or "https"
    crawl = SiteCrawl(domain=domain)

    def note(msg: str) -> None:
        if progress:
            try:
                progress(msg, domain)
            except Exception:
                pass

    home = _canon(ctx.url)
    seeds = _sitemap_urls(ctx, domain, scheme, max_pages)
    crawl.sitemap_urls = len(seeds)
    note(f"Sitemap: {len(seeds)} URLs" if seeds else "No sitemap — crawling internal links")

    queued: set[str] = set()
    queue: deque[str] = deque()
    for u in [home] + seeds:
        if u not in queued and _crawlable(u):
            queued.add(u)
            queue.append(u)

    # Track internal link targets so we can report broken ones that we don't
    # otherwise crawl (e.g. once the page cap is hit).
    link_targets: dict[str, str] = {}   # target url -> a page it was found on
    status_cache: dict[str, int] = {}

    crawled = 0
    last_note = 0.0
    while queue and crawled < max_pages:
        url = queue.popleft()
        t0 = time.perf_counter()
        r = _get(ctx, url)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        crawled += 1
        if r is None:
            pg = Page(url=url, status=0, error="no response")
            crawl.pages.append(pg)
            status_cache[url] = 0
            continue
        parsed = _parse_page(url, r, domain)
        if isinstance(parsed, tuple):
            pg, soup = parsed
            pg.response_ms = elapsed_ms
            internal_urls, internal, external = _links_from(soup, url, domain)
            pg.internal_links = internal
            pg.external_links = external
            for tgt in internal_urls:
                link_targets.setdefault(tgt, url)
                if tgt not in queued and len(queued) < max_pages * 3:
                    queued.add(tgt)
                    queue.append(tgt)
        else:
            pg = parsed
        crawl.pages.append(pg)
        status_cache[url] = pg.status
        now = time.perf_counter()
        if now - last_note > 3:
            note(f"Crawled {crawled} pages…")
            last_note = now

    crawl.truncated = bool(queue)
    crawl.discovered = len(queued)

    # Broken internal links: any internal target that returned >= 400 (or nothing).
    # Prefer statuses we already have; HEAD-check a bounded sample of the rest.
    unchecked = [u for u in link_targets if u not in status_cache]
    for u in unchecked[:150]:
        r = _get(ctx, u, timeout=12000)
        status_cache[u] = r.status if r else 0
    for u, found_on in link_targets.items():
        st = status_cache.get(u)
        if st is not None and (st == 0 or st >= 400):
            crawl.broken.append({"url": u, "status": st or "no response", "found_on": found_on})

    crawl.duration_s = time.perf_counter() - start
    crawl.agg = _aggregate(crawl)
    note(f"Crawl done: {crawl.pages_crawled} pages in {crawl.duration_s:.0f}s")
    return crawl


def _aggregate(crawl: SiteCrawl) -> dict:
    """Roll per-page facts into the site-wide numbers the audit reports."""
    pages = crawl.ok_pages
    n = len(pages)

    titles = [p.title.strip().lower() for p in pages if p.title.strip()]
    metas = [p.meta_description.strip().lower() for p in pages if p.meta_description.strip()]
    # "duplicate" = the extra copies beyond the first of each repeated value.
    dup_titles = sum(c - 1 for c in Counter(titles).values() if c > 1)
    dup_metas = sum(c - 1 for c in Counter(metas).values() if c > 1)

    return {
        "pages_crawled": n,
        "discovered": crawl.discovered,
        "sitemap_urls": crawl.sitemap_urls,
        "truncated": crawl.truncated,
        "duration_s": round(crawl.duration_s, 1),
        "missing_title": sum(1 for p in pages if not p.title.strip()),
        "duplicate_title": dup_titles,
        "long_title": sum(1 for p in pages if len(p.title) > 65),
        "short_title": sum(1 for p in pages if p.title.strip() and len(p.title) < 30),
        "missing_meta": sum(1 for p in pages if not p.meta_description.strip()),
        "duplicate_meta": dup_metas,
        "missing_h1": sum(1 for p in pages if p.h1_count == 0),
        "multiple_h1": sum(1 for p in pages if p.h1_count > 1),
        "non_self_canonical": sum(1 for p in pages if not p.self_canonical),
        "missing_canonical": sum(1 for p in pages if not p.canonical),
        "thin_pages": sum(1 for p in pages if p.word_count < THIN_WORDS),
        "noindex_pages": sum(1 for p in pages if p.noindex),
        "slow_pages": sum(1 for p in pages if p.response_ms > SLOW_MS),
        "images_total": sum(p.images_total for p in pages),
        "images_missing_alt": sum(p.images_missing_alt for p in pages),
        "pages_with_missing_alt": sum(1 for p in pages if p.images_missing_alt),
        "broken_links": len(crawl.broken),
        "error_pages": sum(1 for p in crawl.pages if p.status and p.status >= 400),
        "avg_words": round(sum(p.word_count for p in pages) / n) if n else 0,
    }
