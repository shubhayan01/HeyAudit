"""Off-DOM SEO checks — powered entirely by free, first-party signals.

bd-auto measures everything it can directly (see native.py). The topics below —
page load, content depth, security posture, technology profile and AI-search
readiness — used to be filled by a paid third-party API. They are now derived
from the data the audit already gathers for free: navigation timing, the crawl
aggregates, the rendered homepage DOM and the response headers (see
``freedata.enrich``). No browser automation, no screenshots, no API key.

Off-site authority metrics (backlinks, referring domains, organic traffic,
keyword rankings) have no free source for a prospect's site, so bd-auto no
longer reports them rather than showing estimates dressed up as measurements.
"""

from __future__ import annotations

from .. import screamingfrog
from ..core import AuditContext, CheckResult, Status, check
from . import freedata


def _free(ctx: AuditContext, cid: str, category: str, title: str, topic: str) -> CheckResult:
    result = CheckResult(cid, category, title, Status.INFO, "", source="")
    return freedata.enrich(ctx, result, topic)


@check("gtmetrix", "Page Load", "Load time & homepage weight", "")
def page_load(ctx: AuditContext) -> CheckResult:
    return _free(ctx, "gtmetrix", "Page Load", "Load time & homepage weight", "gtmetrix")


@check("siteliner", "Content", "Content depth & on-page signals", "")
def content_quality(ctx: AuditContext) -> CheckResult:
    return _free(ctx, "siteliner", "Content", "Content depth & on-page signals", "siteliner")


@check("geo", "AI Search Readiness", "Generative Engine Optimization (LLMs/AI)", "")
def ai_readiness(ctx: AuditContext) -> CheckResult:
    return _free(ctx, "geo", "AI Search Readiness",
                 "Generative Engine Optimization (LLMs/AI)", "geo")


@check("security", "Security", "HTTPS & connection safety", "")
def security(ctx: AuditContext) -> CheckResult:
    return _free(ctx, "security", "Security", "HTTPS & connection safety", "security")


@check("domain_category", "Technology Profile", "Detected technologies & hosting", "")
def technology_profile(ctx: AuditContext) -> CheckResult:
    result = _free(ctx, "domain_category", "Technology Profile",
                   "Detected technologies & hosting", "domain_category")
    # When Screaming Frog SEO Spider is installed + licensed, enrich the technical
    # profile with its deeper crawl data. Auto-detected and entirely optional: if
    # it isn't present (or the run fails) the free result stands.
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
