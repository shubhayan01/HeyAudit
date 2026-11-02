"""Render the client deliverable to HTML and PDF (via Chromium).

  * ``render_deck_html``  -> the single combined deck (deck.html.j2): the audit
                             half (health scorecard, critical issues, on-page +
                             technical, organic search performance) followed by
                             the solution half (competitive landscape, the
                             opportunity, three-pillar strategy, 12-month
                             roadmap, keyword plan, investment, next steps).
                             This is the only deliverable the pipeline produces.

  * ``render_html``       -> LEGACY, no longer wired into the pipeline: the old
                             standalone audit report (report.html.j2). Kept for
                             reference; the combined deck replaced it.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

from . import __version__
from .core import AuditContext, CheckResult, Status
from .discovery import Discovery

_TEMPLATES = Path(__file__).parent / "templates"

# Group the individual checks into a handful of client-facing sections
# (keyed by check id) so the report reads as themed chapters, not 28 one-offs.
DISPLAY_CATEGORY = {
    "domain_age": "Security & Infrastructure", "ssl": "Security & Infrastructure",
    "security": "Security & Infrastructure", "domain_category": "Security & Infrastructure",
    "crawl_overview": "Indexing & Crawlability",
    "robots": "Indexing & Crawlability", "sitemap_xml": "Indexing & Crawlability",
    "sitemap_html": "Indexing & Crawlability", "canonical": "Indexing & Crawlability",
    "site_indexability": "Indexing & Crawlability",
    "response_time": "Performance", "js_minify": "Performance",
    "pagespeed": "Performance", "gtmetrix": "Performance",
    "title": "On-Page SEO", "meta_desc": "On-Page SEO", "headings": "On-Page SEO",
    "structured_data": "On-Page SEO", "keywords": "On-Page SEO", "siteliner": "On-Page SEO",
    "site_titles": "On-Page SEO", "site_meta": "On-Page SEO", "site_headings": "On-Page SEO",
    "site_alt": "On-Page SEO", "site_thin": "On-Page SEO",
    "w3c": "Code Quality", "text_ratio": "Code Quality",
    "broken_links": "Links & Navigation", "interlinking": "Links & Navigation",
    "mobile": "Mobile & Usability", "amp": "Mobile & Usability",
    "backlinks": "Off-Page & Authority", "indexed_google": "Off-Page & Authority",
    "geo": "AI Visibility",
}

# The order sections appear in the report.
_SECTION_ORDER = [
    "Security & Infrastructure", "Indexing & Crawlability", "On-Page SEO",
    "Content", "Code Quality", "Performance", "Links & Navigation",
    "Mobile & Usability", "Off-Page & Authority", "AI Visibility",
]

# Why each failing check costs rankings — the template's "Impact on Rankings"
# column. Keyed by check id; written for a client, not an engineer.
ISSUE_IMPACT: dict[str, str] = {
    "title": "High — the title tag is the single strongest on-page ranking signal and your search-result headline.",
    "meta_desc": "High — Google can't summarise the page properly, so click-through from search suffers.",
    "headings": "High — a broken heading structure confuses search engines about what the page is actually about.",
    "canonical": "High — without a clear canonical, duplicate URLs compete against each other and split your rankings.",
    "structured_data": "High — no schema means no rich results, and far less chance of being quoted by AI answer engines.",
    "pagespeed": "High — around 40% of visitors abandon a page that takes over three seconds to load.",
    "gtmetrix": "High — page weight and load time are confirmed ranking factors and a direct drag on conversion.",
    "response_time": "High — slow server response delays every other metric Google measures.",
    "mobile": "High — Google indexes the mobile version of your site first; mobile problems are ranking problems.",
    "ssl": "Critical — browsers warn visitors away and Google demotes sites without valid encryption.",
    "security": "Critical — security flags destroy trust and can remove a site from results entirely.",
    "robots": "Informational — robots.txt controls what crawlers may access; its state is reported, not scored.",
    "sitemap_xml": "Medium — without a sitemap, new and deep pages take far longer to get indexed.",
    "sitemap_html": "Low — helps visitors and crawlers find deeper pages.",
    "indexed_google": "High — pages that aren't indexed cannot rank at all.",
    "backlinks": "High — authority from other sites is the strongest off-page ranking factor.",
    "broken_links": "Medium — poor user experience, and link equity leaks out of dead ends.",
    "interlinking": "Medium — internal links route authority to the pages that make you money.",
    "siteliner": "High — thin or duplicated content struggles to rank against deeper competitor pages.",
    "keywords": "Medium — weak keyword coverage means missing the searches your buyers actually make.",
    "w3c": "Low — markup errors can break how crawlers parse the page.",
    "text_ratio": "Low — very low text-to-code ratio can signal a thin page.",
    "js_minify": "Medium — unminified scripts slow the page down for every visitor.",
    "geo": "High — without AI-readable structure you're invisible in ChatGPT, Perplexity and AI Overviews.",
    "amp": "Low — optional; only relevant for some publishing models.",
    "domain_age": "Informational — domain history provides context for how fast authority can build.",
    "domain_category": "Informational — the technology profile shapes what's practical to fix.",
    # Site-wide (whole-domain crawl) issues.
    "crawl_overview": "Informational — the crawl inventory that scopes every finding below.",
    "site_titles": "High — titles are the strongest on-page signal; missing or duplicated ones across the site cap rankings on every affected page.",
    "site_meta": "Medium — missing and duplicate meta descriptions cost click-through from search on those pages.",
    "site_headings": "Medium — pages with no H1 (or several) leave search engines unsure what each page is about.",
    "site_alt": "Medium — images without alt text lose accessibility and image-search traffic site-wide.",
    "site_thin": "High — thin pages struggle to rank and can drag down the whole domain's quality signal.",
    "site_indexability": "High — missing or cross-pointing canonicals split ranking signals between duplicate URLs.",
}

# Ranked worst-first for the critical-issues table.
_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Informational": 4}


def _group_in_order(results: list[CheckResult]):
    """Group results by (remapped) category, following _SECTION_ORDER."""
    order: list[str] = []
    buckets: dict[str, list[CheckResult]] = {}
    for r in results:
        cat = DISPLAY_CATEGORY.get(r.id, r.category)
        if cat not in buckets:
            buckets[cat] = []
            order.append(cat)
        buckets[cat].append(r)

    def rank(cat: str) -> tuple[int, int]:
        return (_SECTION_ORDER.index(cat) if cat in _SECTION_ORDER else len(_SECTION_ORDER),
                order.index(cat))

    return [(cat, buckets[cat]) for cat in sorted(order, key=rank)]


def _sanitize_for_client(results: list[CheckResult]) -> None:
    """Never surface raw exceptions to a client: soften ERROR cards to neutral INFO."""
    for r in results:
        if r.status == Status.ERROR:
            r.status = Status.INFO
            r.summary = "This item requires manual review."
            r.details = []
        r.error = None  # internal only — never rendered


def _extent(r: CheckResult, metrics: dict, crawl_agg: dict | None = None) -> str:
    """How widespread an issue is.

    bd-auto crawls the whole domain, so site-wide checks report their real
    page-count extent ("42 of 318 pages"). Homepage-only checks say "Homepage",
    and SEOptimer-derived checks report the metric they measured.
    """
    a = crawl_agg or {}
    pages = a.get("pages_crawled")
    if pages:
        if r.id == "site_titles":
            return f"{a['missing_title'] + a['duplicate_title']} of {pages} pages"
        if r.id == "site_meta":
            return f"{a['missing_meta'] + a['duplicate_meta']} of {pages} pages"
        if r.id == "site_headings":
            return f"{a['missing_h1'] + a['multiple_h1']} of {pages} pages"
        if r.id == "site_thin":
            return f"{a['thin_pages']} of {pages} pages"
        if r.id == "site_indexability":
            return f"{a['missing_canonical'] + a['non_self_canonical']} of {pages} pages"
        if r.id == "site_alt":
            return f"{a['images_missing_alt']:,} of {a['images_total']:,} images"
        if r.id == "broken_links":
            return f"{a['broken_links']} broken links across {pages} pages"
    if r.id == "siteliner" and metrics.get("content_words"):
        return f"{metrics['content_words']:,} words on the homepage"
    if r.id == "backlinks" and metrics.get("referring_domains") is not None:
        return f"{metrics['referring_domains']:,} referring domains"
    if r.id == "indexed_google" and metrics.get("keywords_total") is not None:
        return f"{metrics['keywords_total']} ranking keywords tracked"
    if r.id in ("pagespeed", "gtmetrix", "response_time") and metrics.get("load_ms"):
        return f"{metrics['load_ms'] / 1000:.1f}s load time"
    if r.id == "interlinking" and metrics.get("internal_links"):
        return f"{metrics['internal_links']} internal links"
    return "Homepage"


def _severity(r: CheckResult) -> str:
    impact = ISSUE_IMPACT.get(r.id, "")
    word = impact.split("—")[0].strip() if "—" in impact else ""
    if word in _SEVERITY_RANK:
        return word
    return "Critical" if r.status == Status.FAIL else "Medium"


def _critical_issues(results: list[CheckResult], metrics: dict,
                     crawl_agg: dict | None = None) -> list[dict]:
    """The template's Critical Issues table: what's broken, how widely, what it costs.

    Only real failures and warnings — a table padded with passing checks trains
    the client to skim past the things that matter.
    """
    rows = []
    for r in results:
        if r.status not in (Status.FAIL, Status.WARN):
            continue
        sev = _severity(r)
        impact = ISSUE_IMPACT.get(r.id, "")
        rows.append({
            "issue": r.title,
            "summary": r.summary,
            "extent": _extent(r, metrics, crawl_agg),
            "impact": impact.split("—", 1)[1].strip() if "—" in impact else impact,
            "severity": sev,
            "status": r.status.value,
        })
    rows.sort(key=lambda x: (_SEVERITY_RANK.get(x["severity"], 9),
                             0 if x["status"] == "fail" else 1))
    return rows


def _find(results: list[CheckResult], check_id: str) -> CheckResult | None:
    return next((r for r in results if r.id == check_id), None)


def _homepage_analysis(results: list[CheckResult], metrics: dict) -> list[dict]:
    """The template's Homepage Analysis: element, what's there now, the issue."""
    rows = []
    for cid, label in (("title", "Title tag"), ("meta_desc", "Meta description"),
                       ("headings", "H1 / heading structure"),
                       ("structured_data", "Structured data"),
                       ("canonical", "Canonical tag")):
        r = _find(results, cid)
        if not r:
            continue
        current = next((v for k, v in (r.details or []) if k.lower() in
                        ("title", "meta description", "h1", "value", "content")), "")
        rows.append({
            "element": label,
            "current": current or "—",
            "verdict": r.summary,
            "status": r.status.value,
        })

    if metrics.get("load_ms"):
        rows.append({
            "element": "Page load speed",
            "current": f"{metrics['load_ms'] / 1000:.1f}s",
            "verdict": ("Within target." if metrics["load_ms"] <= 2000
                        else "Above the 2-second target — losing visitors before the page paints."),
            "status": "pass" if metrics["load_ms"] <= 2000 else "fail",
        })
    if metrics.get("mobile_score") is not None:
        rows.append({
            "element": "Mobile PageSpeed score",
            "current": f"{metrics['mobile_score']}/100",
            "verdict": ("Healthy." if metrics["mobile_score"] >= 80
                        else "Below Google's threshold on the version it indexes first."),
            "status": "pass" if metrics["mobile_score"] >= 80 else "fail",
        })
    if metrics.get("content_words"):
        rows.append({
            "element": "Content depth",
            "current": f"{metrics['content_words']:,} words",
            "verdict": ("Substantial enough to rank." if metrics["content_words"] >= 500
                        else "Thin — competitors' equivalent pages go far deeper."),
            "status": "pass" if metrics["content_words"] >= 500 else "warn",
        })
    if metrics.get("images_total"):
        missing = metrics.get("images_no_alt") or 0
        rows.append({
            "element": "Image alt text",
            "current": f"{metrics['images_total'] - missing} of {metrics['images_total']} images described",
            "verdict": ("All images described." if not missing
                        else f"{missing} image{'s' if missing != 1 else ''} missing alt text — "
                             "lost accessibility and image-search traffic."),
            "status": "pass" if not missing else "warn",
        })
    return rows


def _site_analysis(crawl_agg: dict | None) -> list[dict]:
    """The deck's whole-site issue table: element, count across the site, verdict.

    Built from the crawl aggregates so the deck shows domain-wide extent
    ("42 of 318 pages") rather than a homepage spot check. Returns [] when no
    crawl ran, so the template simply omits the section.
    """
    a = crawl_agg or {}
    pages = a.get("pages_crawled")
    if not pages:
        return []

    def row(element, bad, total, unit="pages", ok_msg="", bad_msg=""):
        status = "pass" if not bad else ("fail" if total and bad / total >= 0.30 else "warn")
        if total:
            current = f"{bad:,} of {total:,} {unit}"
        else:
            current = f"{bad:,} {unit}"
        return {"element": element, "current": current,
                "verdict": ok_msg if not bad else bad_msg, "status": status}

    rows = [
        row("Missing page titles", a["missing_title"], pages,
            ok_msg="Every page has a title.",
            bad_msg="These pages have no title tag — search engines fall back to guessing a headline."),
        row("Duplicate titles", a["duplicate_title"], pages,
            ok_msg="All titles are unique.",
            bad_msg="Duplicate titles make pages compete against each other for the same terms."),
        row("Missing meta descriptions", a["missing_meta"], pages,
            ok_msg="Every page has a meta description.",
            bad_msg="Google writes its own snippet for these pages, hurting click-through."),
        row("Missing H1 heading", a["missing_h1"], pages,
            ok_msg="Every page has an H1.",
            bad_msg="No H1 leaves search engines unsure what these pages are about."),
        row("Thin content (<300 words)", a["thin_pages"], pages,
            ok_msg="No thin pages found.",
            bad_msg="Thin pages rarely rank and can drag down the whole domain's quality signal."),
        row("Images missing alt text", a["images_missing_alt"], a["images_total"], unit="images",
            ok_msg="All images described.",
            bad_msg="Undescribed images lose accessibility and image-search traffic."),
        row("Canonical issues", a["missing_canonical"] + a["non_self_canonical"], pages,
            ok_msg="Canonical tags are clean.",
            bad_msg="Missing or cross-pointing canonicals split ranking signals between URLs."),
        row("Broken links", a["broken_links"], 0, unit="broken across the site",
            ok_msg="No broken internal links.",
            bad_msg="Broken links waste crawl budget and leak link equity into dead ends."),
    ]
    return rows


def _quick_win_keywords(metrics: dict) -> list[dict]:
    """Real 'quick-win' keywords: terms the site ALREADY ranks for on page 2+.

    Drawn straight from SEOptimer's measured ``top_keywords`` — a keyword sitting
    in positions 4-30 is already ranking and one push away from the traffic that
    converts, which makes it the honest, evidence-backed target list (no invented
    volumes or difficulty scores). Returns [] when no ranking data is available,
    so the template simply omits the section.
    """
    out: list[dict] = []
    for k in (metrics.get("top_keywords") or []):
        term = (k.get("keyword") or "").strip() if isinstance(k, dict) else ""
        if not term:
            continue
        try:
            pos = int(k.get("position"))
        except (TypeError, ValueError):
            continue
        if not (4 <= pos <= 30):
            continue
        out.append({
            "keyword": term,
            "country": k.get("country"),
            "position": pos,
            "volume": k.get("volume"),
            "traffic": k.get("traffic"),
            # Where the push realistically lands it.
            "target": "Top 3" if pos <= 10 else "Page 1",
        })
    out.sort(key=lambda r: r["position"])
    return out[:10]


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
    )


def render_html(ctx: AuditContext, results: list[CheckResult], out_dir: Path,
                ai: dict | None = None, discovery: Discovery | None = None,
                metrics: dict | None = None) -> Path:
    _sanitize_for_client(results)
    discovery = discovery or Discovery()

    summary = {s.value: 0 for s in Status}
    for r in results:
        summary[r.status.value] += 1

    # SEOptimer scorecard (letter grades) + overall grade, if the API ran.
    try:
        from .checks import seoptimer
        rep = getattr(ctx, "seoptimer", None)
        cards = seoptimer.scorecard(rep)
        metrics = metrics if metrics is not None else seoptimer.metrics(rep)
    except Exception:
        cards, metrics = [], (metrics or {})
    overall = next((c for c in cards if c["key"] == "overall"), None)

    tpl = _env().get_template("report.html.j2")
    html = tpl.render(
        domain=ctx.domain,
        url=ctx.url,
        client_name=discovery.client_name or ctx.domain,
        discovery=discovery,
        generated=datetime.now().strftime("%B %d, %Y"),
        home_status=ctx.home_status,
        home_error=ctx.home_error,
        results=results,
        grouped=_group_in_order(results),
        summary=summary,
        scorecard=cards,
        overall=overall,
        metrics=metrics,
        issues=_critical_issues(results, metrics,
                                getattr(getattr(ctx, "crawl", None), "agg", None) or {}),
        homepage=_homepage_analysis(results, metrics),
        has_home_shot=(ctx.screenshots_dir / "_homepage.png").exists(),
        version=__version__,
        ai=ai,
    )
    html_path = out_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def render_deck_html(ctx: AuditContext, out_dir: Path,
                     results: list[CheckResult],
                     pitch: dict | None = None,
                     ai: dict | None = None,
                     discovery: Discovery | None = None,
                     client_metrics: dict | None = None,
                     competitor_metrics: list[dict] | None = None,
                     projection: dict | None = None,
                     forced_tier: str | None = None) -> Path:
    """Render the single combined client deck (yellow/black/white) to HTML.

    One deliverable, two halves: first the **audit** — what we found and what it's
    costing them (health scorecard, critical issues, on-page + technical, organic
    search performance) — then the **solution**: the strategy, roadmap, keyword
    plan, investment and next steps.

    ``pitch`` is the narrative from ``auditor.pitch.generate_pitch`` and may be
    ``None`` (no Groq key), in which case the solution half is omitted and the
    deck renders as an audit-only document. ``ai`` is the audit's AI analysis.
    Every checkable number — prices, deliverables, competitor metrics, ROI —
    comes from ``packages``/``competitors``/``projections``, never the model.
    """
    from . import competitors as comp_mod
    from . import packages as pk
    from .checks import seoptimer

    discovery = discovery or Discovery()
    pitch = pitch or {}
    client_metrics = client_metrics or {}
    competitor_metrics = competitor_metrics or []

    # The deck is the ONLY place these results are shown to the client now, so it
    # owns the sanitisation the standalone audit report used to do.
    _sanitize_for_client(results)
    summary = {s.value: 0 for s in Status}
    for r in results:
        summary[r.status.value] += 1

    rep = getattr(ctx, "seoptimer", None)
    scorecard = seoptimer.scorecard(rep)
    overall_card = next((c for c in scorecard if c["key"] == "overall"), None)
    audit_grade = (ai or {}).get("grade") or (overall_card or {}).get("grade")

    # Operator-supplied true organic traffic wins over SEOptimer's estimate.
    # Mutate a copy so the caller's dict and any cache stay untouched.
    client_metrics = dict(client_metrics)
    if discovery.organic_override is not None:
        client_metrics["organic_traffic"] = discovery.organic_override
    # Domain Authority is intentionally not surfaced any more: SEOptimer's 0-100
    # "domain strength" is a proprietary estimate (not Moz/Ahrefs DA), and the
    # client asked for it removed. Drop it so no DA-style tile can render.
    client_metrics.pop("domain_strength", None)

    crawl_agg = (getattr(getattr(ctx, "crawl", None), "agg", None) or {})
    issues = _critical_issues(results, client_metrics, crawl_agg)
    homepage = _homepage_analysis(results, client_metrics)
    site_analysis = _site_analysis(crawl_agg)
    quick_win_keywords = _quick_win_keywords(client_metrics)

    # Tier precedence: an explicit operator pick (forced_tier) wins; otherwise the
    # client's budget fixes it; otherwise the model's pick (or Growth) stands.
    rec_tier = forced_tier if forced_tier in pk.TIERS else None
    if not rec_tier:
        rec_tier = pk.tier_for_budget(discovery.budget_figure, discovery.currency)
    if not rec_tier:
        rec_tier = pitch.get("recommended_tier", "growth")
    if rec_tier not in pk.TIERS:
        rec_tier = "growth"

    include_cats = discovery.include_categories or None

    live_competitors = [c for c in competitor_metrics if c.get("loaded")]
    comparison = (comp_mod.table(client_metrics, competitor_metrics,
                                 client_label=discovery.client_name or ctx.domain)
                  if live_competitors else None)

    # Insight text is keyed to a domain so the template can pair it with that
    # competitor's measured row rather than trusting the model's ordering.
    insights = {i["domain"]: i for i in (pitch.get("competitor_insights") or [])}

    tpl = _env().get_template("deck.html.j2")
    html = tpl.render(
        domain=ctx.domain,
        url=ctx.url,
        client_name=discovery.client_name or ctx.domain,
        generated=datetime.now().strftime("%B %d, %Y"),
        valid_until=discovery.valid_until,
        audit_grade=audit_grade,
        # --- audit half ---
        results=results,
        summary=summary,
        scorecard=scorecard,
        overall=overall_card,
        issues=issues,
        homepage=homepage,
        site_analysis=site_analysis,
        quick_win_keywords=quick_win_keywords,
        crawl=crawl_agg,
        ai=ai,
        # --- solution half ---
        pitch=pitch or None,
        d=discovery,
        metrics=client_metrics,
        comparison=comparison,
        competitors=live_competitors,
        insights=insights,
        proj=projection,
        meta=pk.METADATA,
        tiers=pk.TIERS,
        tier_labels=pk.TIER_LABELS,
        tier_prices={t: pk.price_line(t) for t in pk.TIERS},
        categories=pk.categories_matrix(include_cats),
        options=pk.options_for(rec_tier),
        deliverables=pk.deliverables_for(rec_tier, include_cats),
        rec_tier=rec_tier,
        rec_label=pk.TIER_LABELS[rec_tier],
        rec_price=pk.price_line(rec_tier),
        rec_pkg=pk.PACKAGES[rec_tier],
    )
    html_path = out_dir / "deck.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def render_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Convert the HTML report to PDF using Chromium. Returns True on success."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle", timeout=60000)
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
            )
            browser.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ! PDF rendering failed ({exc.__class__.__name__}: {exc}).")
        print(f"    The HTML report is still available at: {html_path}")
        return False
