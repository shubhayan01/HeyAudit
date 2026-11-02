"""SEO checks powered by the SEOptimer API — no browser, no screenshots.

bd-auto measures everything it can itself (see native.py). The topics below need
third-party data (backlinks, organic presence, off-DOM performance, social, AI
readiness, security), so each check delegates to the SEOptimer API report — which
is fetched once and cached — via seoptimer.enrich(). Statuses are derived from
SEOptimer's letter grades; no CAPTCHA screenshots are ever produced.
"""

from __future__ import annotations

from .. import screamingfrog
from ..core import AuditContext, CheckResult, Status, check
from . import seoptimer


def _seo(ctx: AuditContext, cid: str, category: str, title: str, topic: str) -> CheckResult:
    result = CheckResult(cid, category, title, Status.INFO, "", source="")
    return seoptimer.enrich(ctx, result, topic)


@check("backlinks", "Backlink Profile", "Backlinks & referring domains", "")
def backlinks(ctx: AuditContext) -> CheckResult:
    return _seo(ctx, "backlinks", "Backlink Profile", "Backlinks & referring domains", "backlinks")


@check("indexed_google", "Organic Search", "Organic keyword presence & traffic", "")
def organic_presence(ctx: AuditContext) -> CheckResult:
    return _seo(ctx, "indexed_google", "Organic Search",
                "Organic keyword presence & traffic", "indexed_google")


@check("gtmetrix", "Page Load", "Load time, page weight & requests", "")
def page_load(ctx: AuditContext) -> CheckResult:
    return _seo(ctx, "gtmetrix", "Page Load", "Load time, page weight & requests", "gtmetrix")


@check("siteliner", "Content", "Content depth & on-page signals", "")
def content_quality(ctx: AuditContext) -> CheckResult:
    return _seo(ctx, "siteliner", "Content", "Content depth & on-page signals", "siteliner")


@check("geo", "AI Search Readiness", "Generative Engine Optimization (LLMs/AI)", "")
def ai_readiness(ctx: AuditContext) -> CheckResult:
    return _seo(ctx, "geo", "AI Search Readiness",
                "Generative Engine Optimization (LLMs/AI)", "geo")


@check("security", "Security", "Malware & connection safety", "")
def security(ctx: AuditContext) -> CheckResult:
    return _seo(ctx, "security", "Security", "Malware & connection safety", "security")


@check("domain_category", "Technology Profile", "Detected technologies & hosting", "")
def technology_profile(ctx: AuditContext) -> CheckResult:
    result = _seo(ctx, "domain_category", "Technology Profile",
                  "Detected technologies & hosting", "domain_category")
    # When Screaming Frog SEO Spider is installed + licensed, enrich the technical
    # profile with its deeper crawl data. Auto-detected and entirely optional: if
    # it isn't present (or the run fails) the SEOptimer-derived result stands.
    if screamingfrog.available():
        sf = getattr(ctx, "_sf_tech", None)
        if sf is None:
            sf = screamingfrog.tech_summary(ctx.url,
                                            max_pages=int(ctx.config.get("max_pages", 500) or 500)) or {}
            ctx._sf_tech = sf
        if sf:
            rows = list(result.details)
            if sf.get("urls_crawled"):
                rows.append(("Screaming Frog URLs crawled", f"{sf['urls_crawled']:,}"))
            if sf.get("status_codes"):
                rows.append(("Response codes",
                             "  ·  ".join(f"{k}: {v}" for k, v in sf["status_codes"].items())))
            if sf.get("technologies"):
                rows.append(("Detected technologies", ", ".join(sf["technologies"][:20])))
            result.details = rows
    return result
