"""SEOptimer fallback — a detailed third-party report used to fill in topics
where bd-auto could not fetch a hard value itself.

**API only.** SEOptimer's public site is behind a Cloudflare bot check that
blocks automated browsers, so we do NOT scrape it. Instead we call the official
audit API and read hard grades / recommendations out of the JSON:

    POST https://api.seoptimer.com/v1/report/create   (header x-api-key)
      -> {success, data:{id}}
    GET  https://api.seoptimer.com/v1/report/get/{id}  (poll until complete)
      -> data.output.scores  (8 category grades) + ~80 named check objects

The report is fetched **once per audit** (the first fallback that needs it
triggers it) and cached on the AuditContext; every other fallback reads the
cached data. Everything is wrapped in try/except so SEOptimer never aborts the
audit. If no API key is configured, each affected check simply notes that a key
is required and links to the public report.

Key resolution order: run config (`--seoptimer-key` / web field) ->
`SEOPTIMER_API_KEY` env var -> a `.seoptimer_key` file in the project root.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ..core import AuditContext, CheckResult

API_BASE = "https://api.seoptimer.com/v1"

# Project root (…/bd auto): auditor/checks/seoptimer.py -> parents[2].
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KEY_FILE = _PROJECT_ROOT / ".seoptimer_key"

# bd-auto check id -> (scores category, [output check keys in priority order]).
# Category grades come from output.scores[cat]; the check keys pull specific
# named audits out of output.<key> (title/shortAnswer/recommendation/data).
# Field names verified against a live api.seoptimer.com /report/get response.
API_TOPIC_MAP: dict[str, tuple[str, list[str]]] = {
    "backlinks":       ("links",       ["backlinks", "backlinksList", "referringDomainsList"]),
    "gtmetrix":        ("performance", ["serverResponseTime", "pageSize", "numberOfResources",
                                        "gzip", "http2", "minified", "optimizedImages"]),
    "pagespeed":       ("performance", ["coreWebVitals", "mobilePageInsights",
                                        "desktopPageInsights", "serverResponseTime"]),
    "siteliner":       ("seo",         ["contentLength", "keywords", "imageAlt"]),
    "domain_category": ("overall",     ["technologies", "ip", "webServer"]),
    "indexed_google":  ("seo",         ["totalTrafficFromSearch", "topKeywordRankings",
                                        "sitemap", "robotsTxt"]),
    "w3c":             ("ui",          ["javascriptErrors", "deprecated", "deviceRendering"]),
    # SEOptimer-only sections that bd-auto has no native equivalent for:
    "geo":             ("geo",         ["llmsTxt", "identitySchema", "llmReadability",
                                        "topAIOverviewRankings"]),
    "security":        ("security",    ["malware", "sslEnabled", "httpsRedirect"]),
}

# SEOptimer letter grade -> bd-auto status.
_GRADE_STATUS = {"A": "pass", "B": "pass", "C": "warn", "D": "fail", "F": "fail"}

# Order + friendly labels for the at-a-glance scorecard (only rendered if graded).
SCORECARD_ORDER = [
    ("overall", "Overall"), ("seo", "On-Page SEO"), ("performance", "Performance"),
    ("links", "Backlinks"), ("ui", "Usability"),
    ("geo", "AI Search"), ("security", "Security"),
]


def _public_report_url(domain: str) -> str:
    """The public (human-viewable) SEOptimer report link for a domain."""
    return f"https://www.seoptimer.com/{domain}"


def _report_url(ctx: AuditContext) -> str:
    return _public_report_url(ctx.domain)


# --------------------------------------------------------------------------
# API client
# --------------------------------------------------------------------------

def resolve_key(config: dict | None = None) -> str | None:
    """SEOptimer key from run config -> SEOPTIMER_API_KEY env -> .seoptimer_key file."""
    key = (config or {}).get("seoptimer_key") or os.environ.get("SEOPTIMER_API_KEY")
    if not (isinstance(key, str) and key.strip()):
        try:
            if _KEY_FILE.is_file():
                key = _KEY_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            key = None
    return key.strip() if isinstance(key, str) and key.strip() else None


def _api_key(ctx: AuditContext) -> str | None:
    return resolve_key(ctx.config)


def _api_call(method: str, url: str, key: str, body: dict | None = None,
              timeout: int = 30) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "x-api-key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore") or "{}")


def _dig(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _grade_line(cat_entry: dict) -> str | None:
    """Render a scores category ({grade,title,description}) as 'B+ — Your ...'."""
    if not isinstance(cat_entry, dict):
        return None
    grade = (cat_entry.get("grade") or "").strip()
    if not grade:
        return None
    title = (cat_entry.get("title") or "").strip()
    return f"{grade} — {title}" if title else grade


def _check_text(check: dict) -> str:
    """Flatten one named audit ({shortAnswer,recommendation,value,data}) to text."""
    if not isinstance(check, dict):
        return ""
    parts: list[str] = []
    sa = check.get("shortAnswer")
    if isinstance(sa, str) and sa.strip():
        parts.append(sa.strip())
    rec = check.get("recommendation")
    if isinstance(rec, str) and rec.strip():
        parts.append("Fix: " + rec.strip())
    val = check.get("value")
    if val not in (None, "", []):
        parts.append(f"Value: {val}")
    data = check.get("data")
    if isinstance(data, dict):
        scal = [f"{k}={v}" for k, v in data.items()
                if isinstance(v, (int, float, str)) and str(v).strip() != ""]
        if scal:
            parts.append(", ".join(scal[:8]))
    return " — ".join(parts)[:500]


def _apply_output(out: dict, output: dict) -> None:
    """Populate the cache from a completed report's data.output object."""
    scores = output.get("scores") if isinstance(output.get("scores"), dict) else {}
    out["loaded"] = True
    out["scores"] = scores
    out["checks"] = {k: v for k, v in output.items()
                     if isinstance(v, dict) and "section" in v}
    out["overall"] = _grade_line(scores.get("overall") or {})
    out["screenshot_url"] = output.get("screenshot")


def fetch_report(url: str, key: str, domain: str | None = None,
                 timeout_s: int = 150) -> dict:
    """Create + poll a SEOptimer report for ANY url. Never raises.

    Used both for the audited site (via ``ensure_report``) and for competitor
    domains (see ``auditor.competitors``). Each call creates a new report and
    therefore costs one API credit.
    """
    domain = domain or urlparse(url if "://" in url else "https://" + url).netloc
    if domain.startswith("www."):
        domain = domain[4:]
    out: dict = {"url": _public_report_url(domain), "domain": domain,
                 "error": None, "overall": None, "scores": {}, "checks": {},
                 "screenshot_url": None, "loaded": False}
    try:
        created = _api_call("POST", f"{API_BASE}/report/create", key,
                            {"url": url, "pdf": False})
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        out["error"] = f"SEOptimer API create failed: HTTP {e.code} {detail}".strip()
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = f"SEOptimer API create failed: {e.__class__.__name__}: {e}"
        return out

    report_id = _dig(created, "data", "id") or created.get("id")
    if not report_id:
        out["error"] = "SEOptimer API: no report id returned"
        return out

    # The audit crawls + tests the site; it takes ~60-70s. Poll report/get
    # until data.output.scores appears.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            raw = _api_call("GET", f"{API_BASE}/report/get/{report_id}", key, timeout=40)
        except Exception:
            time.sleep(6)
            continue
        output = _dig(raw, "data", "output")
        if isinstance(output, dict) and isinstance(output.get("scores"), dict):
            _apply_output(out, output)
            return out
        time.sleep(6)
    out["error"] = f"SEOptimer API report timed out (still generating after {timeout_s}s)"
    return out


def ensure_report(ctx: AuditContext) -> dict:
    """Fetch (once) and return the cached SEOptimer API report for this audit."""
    if getattr(ctx, "seoptimer", None) is not None:
        return ctx.seoptimer

    key = _api_key(ctx)
    if not key:
        ctx.seoptimer = {
            "url": _report_url(ctx), "loaded": False, "scores": {}, "checks": {},
            "overall": None, "screenshot_url": None,
            "error": ("no SEOptimer API key configured — set SEOPTIMER_API_KEY, pass "
                      "--seoptimer-key, or put the key in .seoptimer_key"),
        }
        return ctx.seoptimer

    ctx.seoptimer = fetch_report(ctx.url, key, domain=ctx.domain)
    return ctx.seoptimer


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------

def _status_from_grade(grade: str):
    """Map a SEOptimer letter grade to a bd-auto Status (or None if ungraded)."""
    from ..core import Status
    letter = (grade or "").strip()[:1].upper()
    val = _GRADE_STATUS.get(letter)
    return Status(val) if val else None


def _pretty_key(k: str) -> str:
    words = str(k).replace("_", " ").replace("-", " ").split()
    return " ".join(w if w.isupper() else w.capitalize() for w in words)


def _fmt_value(v) -> str | None:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.1f}".rstrip("0").rstrip(".")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


_SKIP_KEYS = {"allbacklinks", "mozda", "id", "url", "status"}


def _humanize(data: dict, limit: int = 6) -> str:
    """Turn a check's `data` dict into 'Backlinks 5,832,765 · Referring Domains 271,518'."""
    out = []
    for k, v in data.items():
        if str(k).lower() in _SKIP_KEYS:
            continue
        sval = _fmt_value(v)
        if sval is None:
            continue
        out.append(f"{_pretty_key(k)} {sval}")
        if len(out) >= limit:
            break
    return "  ·  ".join(out)


def _metric_text(check: dict) -> str:
    """A compact, client-readable metric line for one named audit."""
    if not isinstance(check, dict):
        return ""
    bits: list[str] = []
    data = check.get("data")
    if isinstance(data, dict):
        h = _humanize(data)
        if h:
            bits.append(h)
    val = check.get("value")
    if not bits and val not in (None, "", []):
        bits.append(str(val))
    if not bits:
        sa = check.get("shortAnswer")
        if isinstance(sa, str) and sa.strip():
            bits.append(sa.strip())
    rec = check.get("recommendation")
    if isinstance(rec, str) and rec.strip():
        bits.append("→ " + rec.strip())
    return "  ".join(bits)[:320]


def metrics(rep: dict | None) -> dict:
    """Pull the hard numbers the proposal needs out of a report, as a flat dict.

    Every value is ``None`` when SEOptimer didn't supply it, so callers can test
    presence rather than guessing at zeroes. Field names below are verified
    against a live api.seoptimer.com response (2026-07-16).
    """
    rep = rep or {}
    checks = rep.get("checks") or {}
    scores = rep.get("scores") or {}

    def data(key) -> dict:
        d = (checks.get(key) or {}).get("data")
        return d if isinstance(d, dict) else {}

    def num(key, field):
        v = data(key).get(field)
        return v if isinstance(v, (int, float)) else None

    m: dict = {
        "domain": rep.get("domain"),
        "report_url": rep.get("url"),
        "loaded": bool(rep.get("loaded")),
        # Authority / links  (backlinks.data)
        "domain_strength": num("backlinks", "domain_strength"),
        "page_strength": num("backlinks", "page_strength"),
        "backlinks": num("backlinks", "backlinks"),
        "referring_domains": num("backlinks", "referring_domains"),
        "dofollow_backlinks": num("backlinks", "dofollow_backlinks"),
        "nofollow_backlinks": num("backlinks", "nofollow_backlinks"),
        "edu_backlinks": num("backlinks", "edu_backlinks"),
        "gov_backlinks": num("backlinks", "gov_backlinks"),
        # Organic performance  (rankings section)
        "organic_traffic": num("totalTrafficFromSearch", "total"),
        "ai_traffic": num("totalTrafficFromSearch", "aitotal"),
        # Performance
        "response_ms": num("serverResponseTime", "responseTime"),
        "load_ms": num("serverResponseTime", "loadTime"),
        "page_bytes": num("pageSize", "totalSize"),
        "mobile_score": num("mobilePageInsights", "score"),
        "desktop_score": num("desktopPageInsights", "score"),
        "lcp": num("coreWebVitals", "largest-contentful-paint"),
        "cls": num("coreWebVitals", "cumulative-layout-shift"),
        "inp": num("coreWebVitals", "interaction-to-next-paint"),
        # On-page
        "images_total": num("imageAlt", "total"),
        "images_no_alt": num("imageAlt", "noAltCount"),
        "internal_links": num("onPageLinks", "internalFollowCount"),
        "external_links": num("onPageLinks", "externalFollowCount"),
        "grades": {k: (v or {}).get("grade") for k, v in scores.items() if isinstance(v, dict)},
        "overall_grade": (scores.get("overall") or {}).get("grade"),
    }

    # Word count arrives as a string ("3083") on `value`, not in `data`.
    cl = checks.get("contentLength") or {}
    try:
        m["content_words"] = int(str(cl.get("value")).strip())
    except (TypeError, ValueError):
        m["content_words"] = None

    # Keyword ranking distribution -> the template's positions 1-3 / 4-10 / 11-20.
    pos = data("keywordPositions")
    if pos:
        def p(*names):
            return sum(int(pos.get(n) or 0) for n in names)
        m["positions"] = {
            "1-3": p("Position 1", "Position 2-3"),
            "4-10": p("Position 4-10"),
            "11-20": p("Position 11-20"),
            "21-50": p("Position 21-30"),
            "51-100": p("Position 31-100"),
        }
        m["keywords_total"] = sum(int(v or 0) for v in pos.values() if isinstance(v, (int, float)))
    else:
        m["positions"] = None
        m["keywords_total"] = None

    # Top ranking keywords (position + volume + traffic), used in the audit report.
    kws = data("topKeywordRankings").get("keywords")
    m["top_keywords"] = [
        {
            "keyword": k.get("keyword"),
            "country": k.get("country"),
            "position": k.get("position"),
            "volume": k.get("total_searches"),
            "traffic": k.get("estimated_traffic"),
        }
        for k in (kws or []) if isinstance(k, dict) and k.get("keyword")
    ][:10]

    # Best backlinks by domain strength — evidence for the authority section.
    bl = data("backlinksList").get("list")
    m["top_backlinks"] = [
        {"url": b.get("url"), "anchor": b.get("anchor_text"),
         "strength": b.get("domain_strength"), "title": b.get("title")}
        for b in (bl or []) if isinstance(b, dict) and b.get("url")
    ][:8]

    techs = (checks.get("technologies") or {}).get("data")
    m["technologies"] = [t for t in techs if isinstance(t, str)] if isinstance(techs, list) else []
    return m


def scorecard(rep: dict | None) -> list[dict]:
    """Category grades for the at-a-glance scorecard, in display order."""
    scores = (rep or {}).get("scores") or {}
    out = []
    for k, label in SCORECARD_ORDER:
        entry = scores.get(k) or {}
        grade = (entry.get("grade") or "").strip()
        if not grade:
            continue
        out.append({"key": k, "label": label, "grade": grade,
                    "letter": grade[:1].upper(), "title": entry.get("title") or ""})
    return out


# --------------------------------------------------------------------------
# Enrichment
# --------------------------------------------------------------------------

def _enrich_from_api(result: CheckResult, rep: dict, key: str) -> None:
    """Fill a check with SEOptimer data: derive status, a plain summary, metrics."""
    cat, check_keys = API_TOPIC_MAP.get(key, ("overall", []))
    scores = rep.get("scores") or {}
    checks = rep.get("checks") or {}
    cat_entry = scores.get(cat) or {}
    grade = (cat_entry.get("grade") or "").strip()

    # 1. status from the category grade (only if the check had none of its own)
    from ..core import Status
    if grade and result.status == Status.INFO:
        st = _status_from_grade(grade)
        if st:
            result.status = st

    # 2. a plain-language summary (category verdict, else the lead check's answer)
    primary = next((checks[k] for k in check_keys
                    if isinstance(checks.get(k), dict) and checks[k].get("shortAnswer")), None)
    if cat != "overall" and cat_entry.get("title"):
        result.summary = cat_entry["title"].strip()
    elif primary and primary.get("shortAnswer"):
        result.summary = primary["shortAnswer"].strip()
    elif not result.summary:
        result.summary = "Reviewed."

    # 3. detail rows: grade, category assessment, then per-check metrics
    rows = list(result.details)
    if grade:
        rows.append(("Grade", f"{grade}"))
    if cat != "overall" and cat_entry.get("description"):
        rows.append(("Assessment", cat_entry["description"].strip()))
    added = 0
    for ck in check_keys:
        c = checks.get(ck)
        text = _metric_text(c)
        if not text:
            continue
        rows.append((c.get("title") or _pretty_key(ck), text))
        added += 1
        if added >= 4:
            break
    result.details = rows


def enrich(ctx: AuditContext, result: CheckResult, key: str) -> CheckResult:
    """Attach SEOptimer's detailed API data for `key` to a data-less CheckResult.

    Derives a pass/improve/fix status from SEOptimer's grade, sets a plain-language
    summary, and adds clean metric rows. No screenshots, no tool branding. If the
    API is unavailable, the check falls back to a neutral 'manual review' note.
    """
    try:
        rep = ensure_report(ctx)
    except Exception as exc:  # noqa: BLE001
        result.error = (result.error or "") + f" | SEOptimer fallback failed: {exc}"
        return result

    if rep.get("loaded"):
        _enrich_from_api(result, rep, key)
    else:
        if not result.summary:
            result.summary = "This item was flagged for manual review."
        if rep.get("error"):
            result.error = (result.error or "") + f" | SEOptimer: {rep['error']}"
    return result
