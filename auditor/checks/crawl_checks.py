"""Site-wide checks — the SEMrush-style, whole-domain view.

The native checks in ``native.py`` inspect the homepage. These read the whole
crawl (``ctx.crawl``, an :class:`auditor.crawler.SiteCrawl`) and report issues
across every page: how many pages are missing titles or meta descriptions, have
duplicate metadata, no H1, thin content, images without alt text, or return
broken links. That page-count extent ("42 of 318 pages") is what makes the audit
read like a real crawler's findings instead of a single-page spot check.

Every check degrades to a neutral INFO note when the crawl is unavailable, so an
audit still completes if the crawl was skipped.
"""

from __future__ import annotations

from ..core import AuditContext, CheckResult, Status, check


def _crawl(ctx: AuditContext):
    c = getattr(ctx, "crawl", None)
    if c is None or not getattr(c, "pages_crawled", 0):
        return None
    return c


def _unavailable(cid: str, category: str, title: str) -> CheckResult:
    return CheckResult(cid, category, title, Status.INFO,
                       "Site-wide crawl data unavailable — assessed on the homepage only.",
                       source="crawl")


def _status(issue_pages: int, total: int, warn_ratio: float = 0.10) -> Status:
    """PASS when clean, WARN for a minority, FAIL when it's widespread."""
    if issue_pages <= 0:
        return Status.PASS
    if total and issue_pages / total >= max(warn_ratio * 3, 0.30):
        return Status.FAIL
    return Status.WARN


# --------------------------------------------------------------------------

@check("crawl_overview", "Site Crawl", "Pages crawled across the whole domain", "bd-auto crawler")
def crawl_overview(ctx: AuditContext) -> CheckResult:
    c = _crawl(ctx)
    if not c:
        return _unavailable("crawl_overview", "Site Crawl", "Pages crawled across the whole domain")
    a = c.agg
    indexable = a["pages_crawled"] - a["noindex_pages"]
    summary = (f"Crawled {a['pages_crawled']} pages across {ctx.domain} "
               f"({indexable} indexable, {a['error_pages']} returning errors).")
    details = [
        ("Pages crawled", f"{a['pages_crawled']:,}"),
        ("URLs discovered", f"{a['discovered']:,}"),
        ("From XML sitemap", f"{a['sitemap_urls']:,}"),
        ("Indexable pages", f"{indexable:,}"),
        ("Non-indexable (noindex)", f"{a['noindex_pages']:,}"),
        ("Error pages (4xx/5xx)", f"{a['error_pages']:,}"),
        ("Average words / page", f"{a['avg_words']:,}"),
        ("Crawl coverage", "capped at the page limit" if a["truncated"] else "complete"),
    ]
    return CheckResult("crawl_overview", "Site Crawl", "Pages crawled across the whole domain",
                       Status.INFO, summary, details, source="bd-auto crawler")


@check("site_titles", "Title Tags (site-wide)", "Title tags across all pages", "bd-auto crawler")
def site_titles(ctx: AuditContext) -> CheckResult:
    c = _crawl(ctx)
    if not c:
        return _unavailable("site_titles", "Title Tags (site-wide)", "Title tags across all pages")
    a = c.agg
    bad = a["missing_title"] + a["duplicate_title"]
    st = _status(bad, a["pages_crawled"])
    summary = (f"{a['missing_title']} pages missing a title and {a['duplicate_title']} "
               f"duplicate titles across {a['pages_crawled']} pages."
               if bad else f"Every one of {a['pages_crawled']} crawled pages has a unique title.")
    details = [("Pages crawled", f"{a['pages_crawled']:,}"),
               ("Missing title", f"{a['missing_title']:,}"),
               ("Duplicate title", f"{a['duplicate_title']:,}"),
               ("Too long (>65)", f"{a['long_title']:,}"),
               ("Too short (<30)", f"{a['short_title']:,}")]
    return CheckResult("site_titles", "Title Tags (site-wide)", "Title tags across all pages",
                       st, summary, details, source="bd-auto crawler")


@check("site_meta", "Meta Descriptions (site-wide)", "Meta descriptions across all pages", "bd-auto crawler")
def site_meta(ctx: AuditContext) -> CheckResult:
    c = _crawl(ctx)
    if not c:
        return _unavailable("site_meta", "Meta Descriptions (site-wide)", "Meta descriptions across all pages")
    a = c.agg
    bad = a["missing_meta"] + a["duplicate_meta"]
    st = _status(bad, a["pages_crawled"])
    summary = (f"{a['missing_meta']} pages missing a meta description and "
               f"{a['duplicate_meta']} duplicates across {a['pages_crawled']} pages."
               if bad else f"All {a['pages_crawled']} pages have a unique meta description.")
    details = [("Pages crawled", f"{a['pages_crawled']:,}"),
               ("Missing meta description", f"{a['missing_meta']:,}"),
               ("Duplicate meta description", f"{a['duplicate_meta']:,}")]
    return CheckResult("site_meta", "Meta Descriptions (site-wide)", "Meta descriptions across all pages",
                       st, summary, details, source="bd-auto crawler")


@check("site_headings", "Headings (site-wide)", "H1 coverage across all pages", "bd-auto crawler")
def site_headings(ctx: AuditContext) -> CheckResult:
    c = _crawl(ctx)
    if not c:
        return _unavailable("site_headings", "Headings (site-wide)", "H1 coverage across all pages")
    a = c.agg
    bad = a["missing_h1"] + a["multiple_h1"]
    st = _status(bad, a["pages_crawled"])
    summary = (f"{a['missing_h1']} pages with no H1 and {a['multiple_h1']} with multiple H1s "
               f"across {a['pages_crawled']} pages."
               if bad else f"Every one of {a['pages_crawled']} pages has exactly one H1.")
    details = [("Pages crawled", f"{a['pages_crawled']:,}"),
               ("Missing H1", f"{a['missing_h1']:,}"),
               ("Multiple H1", f"{a['multiple_h1']:,}")]
    return CheckResult("site_headings", "Headings (site-wide)", "H1 coverage across all pages",
                       st, summary, details, source="bd-auto crawler")


@check("site_alt", "Image Alt Text (site-wide)", "Image alt coverage across all pages", "bd-auto crawler")
def site_alt(ctx: AuditContext) -> CheckResult:
    c = _crawl(ctx)
    if not c:
        return _unavailable("site_alt", "Image Alt Text (site-wide)", "Image alt coverage across all pages")
    a = c.agg
    st = _status(a["images_missing_alt"], max(a["images_total"], 1))
    summary = (f"{a['images_missing_alt']:,} of {a['images_total']:,} images across the site "
               f"are missing alt text."
               if a["images_missing_alt"] else
               f"All {a['images_total']:,} images across the site have alt text.")
    details = [("Images found", f"{a['images_total']:,}"),
               ("Missing alt text", f"{a['images_missing_alt']:,}"),
               ("Pages affected", f"{a['pages_with_missing_alt']:,}")]
    return CheckResult("site_alt", "Image Alt Text (site-wide)", "Image alt coverage across all pages",
                       st, summary, details, source="bd-auto crawler")


@check("site_thin", "Thin Content (site-wide)", "Pages with thin content", "bd-auto crawler")
def site_thin(ctx: AuditContext) -> CheckResult:
    c = _crawl(ctx)
    if not c:
        return _unavailable("site_thin", "Thin Content (site-wide)", "Pages with thin content")
    a = c.agg
    st = _status(a["thin_pages"], a["pages_crawled"])
    summary = (f"{a['thin_pages']} of {a['pages_crawled']} pages have under 300 words of content."
               if a["thin_pages"] else
               f"All {a['pages_crawled']} pages carry substantial content (avg {a['avg_words']:,} words).")
    details = [("Pages crawled", f"{a['pages_crawled']:,}"),
               ("Thin pages (<300 words)", f"{a['thin_pages']:,}"),
               ("Average words / page", f"{a['avg_words']:,}")]
    return CheckResult("site_thin", "Thin Content (site-wide)", "Pages with thin content",
                       st, summary, details, source="bd-auto crawler")


@check("site_indexability", "Indexability (site-wide)", "Canonical & indexability across all pages", "bd-auto crawler")
def site_indexability(ctx: AuditContext) -> CheckResult:
    c = _crawl(ctx)
    if not c:
        return _unavailable("site_indexability", "Indexability (site-wide)", "Canonical & indexability across all pages")
    a = c.agg
    bad = a["missing_canonical"] + a["non_self_canonical"]
    st = _status(bad, a["pages_crawled"], warn_ratio=0.20)
    summary = (f"{a['missing_canonical']} pages missing a canonical tag and "
               f"{a['non_self_canonical']} canonicalised elsewhere, across {a['pages_crawled']} pages."
               if bad else f"Canonical tags are clean across all {a['pages_crawled']} pages.")
    details = [("Pages crawled", f"{a['pages_crawled']:,}"),
               ("Missing canonical", f"{a['missing_canonical']:,}"),
               ("Canonical points elsewhere", f"{a['non_self_canonical']:,}"),
               ("Noindex pages", f"{a['noindex_pages']:,}")]
    return CheckResult("site_indexability", "Indexability (site-wide)", "Canonical & indexability across all pages",
                       st, summary, details, source="bd-auto crawler")
