"""Free, first-party data layer — no third-party API, no key, no network.

bd-auto used to lean on the paid SEOptimer API for the off-DOM topics
(performance, content depth, security, technology, AI readiness) and for an
at-a-glance letter-grade scorecard. That dependency is gone. Everything here is
derived from data the audit already gathered for free:

  * the whole-domain crawl (``ctx.crawl`` aggregates),
  * the rendered homepage DOM (``ctx.home_soup``),
  * navigation timing captured when the homepage loaded (``ctx.home_ttfb_ms`` /
    ``ctx.home_load_ms``), and
  * the homepage response headers (``ctx.home_headers``).

Three public helpers keep the shape the rest of the app expects:

  * :func:`enrich`    — fill a scraper check (page load / content / security /
                        technology / AI readiness) from the free signals above.
  * :func:`metrics`   — the flat metrics dict the deck reads. Fields with no
                        free source for a third party's site (backlinks,
                        referring domains, organic traffic, keyword rankings)
                        are ``None`` / ``[]`` so the template simply omits those
                        slides rather than showing invented numbers.
  * :func:`scorecard` — category letter grades derived honestly from the audit's
                        own pass / improve / fix results.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..core import AuditContext, CheckResult, Status

# --------------------------------------------------------------------------
# Scorecard — letter grades derived from our own check results
# --------------------------------------------------------------------------

# Which check ids roll up into each scorecard category.
_SCORE_CATS: dict[str, set[str]] = {
    "seo": {"title", "meta_desc", "headings", "structured_data", "keywords",
            "canonical", "siteliner", "site_titles", "site_meta", "site_headings",
            "site_alt", "site_thin", "site_indexability"},
    "performance": {"response_time", "js_minify", "pagespeed", "gtmetrix", "text_ratio"},
    "security": {"ssl", "security", "domain_age"},
    "ui": {"mobile", "amp", "broken_links", "interlinking", "w3c"},
    "geo": {"geo", "structured_data"},
}
_SCORE_LABELS = {
    "overall": "Overall", "seo": "On-Page SEO", "performance": "Performance",
    "security": "Security", "ui": "Usability", "geo": "AI Search",
}
SCORECARD_ORDER = ["overall", "seo", "performance", "security", "ui", "geo"]

# A check's contribution to its category score.
_WEIGHT = {Status.PASS: 1.0, Status.WARN: 0.5, Status.FAIL: 0.0}


def _letter(frac: float) -> str:
    if frac >= 0.90:
        return "A"
    if frac >= 0.75:
        return "B"
    if frac >= 0.55:
        return "C"
    if frac >= 0.35:
        return "D"
    return "F"


def _grade_bucket(results: list[CheckResult], ids: set[str] | None) -> str | None:
    """Letter grade for the checks whose id is in ``ids`` (all, if None)."""
    scored = [r for r in results
              if (ids is None or r.id in ids) and r.status in _WEIGHT]
    if not scored:
        return None
    frac = sum(_WEIGHT[r.status] for r in scored) / len(scored)
    return _letter(frac)


def scorecard(results: list[CheckResult] | None) -> list[dict]:
    """At-a-glance category grades, derived from the audit's own results.

    Honest by construction: an "A" means the free checks in that category
    passed, not that a third-party tool said so. Returns [] if nothing is
    gradeable, so the template omits the scorecard.
    """
    results = results or []
    cards: list[dict] = []
    for key in SCORECARD_ORDER:
        ids = None if key == "overall" else _SCORE_CATS.get(key, set())
        grade = _grade_bucket(results, ids)
        if not grade:
            continue
        cards.append({"key": key, "label": _SCORE_LABELS[key], "grade": grade,
                      "letter": grade[:1].upper(), "title": ""})
    return cards


# --------------------------------------------------------------------------
# Free homepage / crawl signals
# --------------------------------------------------------------------------

def _crawl_agg(ctx: AuditContext) -> dict:
    return getattr(getattr(ctx, "crawl", None), "agg", None) or {}


def _home_signals(ctx: AuditContext) -> dict:
    """Word count, image alt coverage and link split from the rendered homepage."""
    soup = getattr(ctx, "home_soup", None)
    sig = {"content_words": None, "images_total": None, "images_no_alt": None,
           "internal_links": None, "external_links": None}
    if soup is None:
        return sig
    try:
        text = soup.get_text(" ", strip=True)
        sig["content_words"] = len(text.split())
    except Exception:
        pass
    try:
        imgs = soup.find_all("img")
        sig["images_total"] = len(imgs)
        sig["images_no_alt"] = sum(1 for i in imgs if not (i.get("alt") or "").strip())
    except Exception:
        pass
    try:
        internal = external = 0
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            host = urlparse(href).netloc.lower()
            if not host or host.replace("www.", "") == ctx.domain.replace("www.", ""):
                internal += 1
            else:
                external += 1
        sig["internal_links"], sig["external_links"] = internal, external
    except Exception:
        pass
    return sig


def _server_tech(ctx: AuditContext) -> list[str]:
    """Best-effort technology signals from response headers + a generator meta."""
    techs: list[str] = []
    headers = {k.lower(): v for k, v in (getattr(ctx, "home_headers", None) or {}).items()}
    for h in ("server", "x-powered-by", "x-generator"):
        v = headers.get(h)
        if v and v not in techs:
            techs.append(v)
    soup = getattr(ctx, "home_soup", None)
    if soup is not None:
        try:
            gen = soup.find("meta", attrs={"name": "generator"})
            if gen and gen.get("content"):
                techs.append(gen["content"])
        except Exception:
            pass
    return techs[:10]


# --------------------------------------------------------------------------
# metrics() — the flat dict the deck/report read
# --------------------------------------------------------------------------

def metrics(ctx: AuditContext | None, results: list[CheckResult] | None = None) -> dict:
    """Flat metrics dict for the deck, built entirely from free signals.

    Off-site metrics (backlinks, referring domains, organic traffic, keyword
    rankings) have no free source for a prospect's site, so they are ``None`` /
    ``[]`` and the deck omits them — bd-auto shows what it measured, not guesses.
    """
    if ctx is None:
        return {"loaded": False, "top_keywords": [], "top_backlinks": [], "technologies": []}

    sig = _home_signals(ctx)
    agg = _crawl_agg(ctx)
    cards = scorecard(results or [])
    grades = {c["key"]: c["grade"] for c in cards}

    try:
        page_bytes = len((getattr(ctx, "home_html", "") or "").encode("utf-8")) or None
    except Exception:
        page_bytes = None

    return {
        "domain": getattr(ctx, "domain", None),
        "report_url": None,
        "loaded": True,
        # Off-site authority / organic / rankings — no free source for a 3rd party.
        "domain_strength": None, "page_strength": None,
        "backlinks": None, "referring_domains": None,
        "dofollow_backlinks": None, "nofollow_backlinks": None,
        "edu_backlinks": None, "gov_backlinks": None,
        "organic_traffic": None, "ai_traffic": None,
        "positions": None, "keywords_total": None,
        "top_keywords": [], "top_backlinks": [],
        # Performance — measured live when the homepage loaded.
        "response_ms": round(getattr(ctx, "home_ttfb_ms", 0) or 0) or None,
        "load_ms": round(getattr(ctx, "home_load_ms", 0) or 0) or None,
        "page_bytes": page_bytes,
        "mobile_score": None, "desktop_score": None,
        "lcp": None, "cls": None, "inp": None,
        # On-page — from the rendered homepage.
        "content_words": sig["content_words"],
        "images_total": sig["images_total"],
        "images_no_alt": sig["images_no_alt"],
        "internal_links": sig["internal_links"],
        "external_links": sig["external_links"],
        "technologies": _server_tech(ctx),
        # Grades derived from our own results.
        "grades": grades,
        "overall_grade": grades.get("overall"),
    }


# --------------------------------------------------------------------------
# enrich() — free-source the off-DOM scraper checks
# --------------------------------------------------------------------------

def _ms(v) -> str:
    return f"{v / 1000:.1f}s" if isinstance(v, (int, float)) and v else "n/a"


def _page_load(ctx: AuditContext, r: CheckResult) -> CheckResult:
    load = getattr(ctx, "home_load_ms", 0) or 0
    ttfb = getattr(ctx, "home_ttfb_ms", 0) or 0
    if load:
        r.status = (Status.PASS if load <= 2500 else
                    Status.WARN if load <= 4000 else Status.FAIL)
        r.summary = f"The homepage finished loading in about {_ms(load)}."
    else:
        r.status = Status.INFO
        r.summary = "Load timing was not captured for this page."
    rows = [("Full load time", _ms(load)), ("Time to first byte", _ms(ttfb))]
    try:
        kb = len((ctx.home_html or "").encode("utf-8")) / 1024
        if kb:
            rows.append(("Homepage weight (HTML)", f"{kb:,.0f} KB"))
    except Exception:
        pass
    r.details = rows
    return r


def _content(ctx: AuditContext, r: CheckResult) -> CheckResult:
    sig = _home_signals(ctx)
    agg = _crawl_agg(ctx)
    words = sig["content_words"]
    if words is not None:
        r.status = (Status.PASS if words >= 500 else
                    Status.WARN if words >= 300 else Status.FAIL)
        r.summary = f"The homepage carries about {words:,} words of readable content."
    else:
        r.status = Status.INFO
        r.summary = "Content depth could not be measured."
    rows = []
    if words is not None:
        rows.append(("Homepage word count", f"{words:,}"))
    if agg.get("avg_words"):
        rows.append(("Average words per page (site-wide)", f"{agg['avg_words']:,}"))
    if agg.get("thin_pages") is not None and agg.get("pages_crawled"):
        rows.append(("Thin pages (<300 words)", f"{agg['thin_pages']} of {agg['pages_crawled']}"))
    r.details = rows
    return r


def _security(ctx: AuditContext, r: CheckResult) -> CheckResult:
    headers = {k.lower(): v for k, v in (getattr(ctx, "home_headers", None) or {}).items()}
    https = getattr(ctx, "scheme", "") == "https"
    hsts = "strict-transport-security" in headers
    csp = "content-security-policy" in headers
    xfo = "x-frame-options" in headers
    if not https:
        r.status = Status.FAIL
        r.summary = "The site is not served over HTTPS — a critical trust and ranking problem."
    elif hsts and (csp or xfo):
        r.status = Status.PASS
        r.summary = "Served over HTTPS with modern security headers in place."
    else:
        r.status = Status.WARN
        r.summary = "Served over HTTPS, but some hardening headers are missing."
    r.details = [
        ("HTTPS", "Yes" if https else "No"),
        ("HSTS (Strict-Transport-Security)", "Present" if hsts else "Missing"),
        ("Content-Security-Policy", "Present" if csp else "Missing"),
        ("X-Frame-Options", "Present" if xfo else "Missing"),
    ]
    return r


def _technology(ctx: AuditContext, r: CheckResult) -> CheckResult:
    techs = _server_tech(ctx)
    r.status = Status.INFO
    if techs:
        r.summary = "Hosting and platform signals detected from the site's responses."
        r.details = [("Detected technologies", ", ".join(techs))]
    else:
        r.summary = "No platform fingerprints were exposed in the site's responses."
        r.details = []
    return r


def _ai_readiness(ctx: AuditContext, r: CheckResult) -> CheckResult:
    soup = getattr(ctx, "home_soup", None)
    jsonld = meta_desc = h1 = False
    if soup is not None:
        try:
            jsonld = bool(soup.find("script", attrs={"type": "application/ld+json"}))
            md = soup.find("meta", attrs={"name": "description"})
            meta_desc = bool(md and (md.get("content") or "").strip())
            h1 = bool(soup.find("h1"))
        except Exception:
            pass
    ready = sum([jsonld, meta_desc, h1])
    r.status = (Status.PASS if ready == 3 else
                Status.WARN if ready >= 1 else Status.FAIL)
    r.summary = ("The homepage gives AI answer engines clear, structured signals."
                 if ready == 3 else
                 "AI answer engines get only partial structure from this page.")
    r.details = [
        ("Structured data (JSON-LD)", "Present" if jsonld else "Missing"),
        ("Meta description", "Present" if meta_desc else "Missing"),
        ("Primary heading (H1)", "Present" if h1 else "Missing"),
    ]
    return r


_ENRICHERS = {
    "gtmetrix": _page_load,
    "siteliner": _content,
    "security": _security,
    "domain_category": _technology,
    "geo": _ai_readiness,
}


def enrich(ctx: AuditContext, result: CheckResult, topic: str) -> CheckResult:
    """Fill a scraper check from free signals. Never raises."""
    try:
        fn = _ENRICHERS.get(topic)
        if fn:
            return fn(ctx, result)
    except Exception as exc:  # noqa: BLE001
        result.status = Status.INFO
        result.summary = "This item requires manual review."
        result.details = []
        result.error = f"{exc.__class__.__name__}: {exc}"
        return result
    # Unknown topic (e.g. the old w3c/pagespeed fallbacks): leave as-is.
    if not result.summary:
        result.summary = "This item requires manual review."
    return result
