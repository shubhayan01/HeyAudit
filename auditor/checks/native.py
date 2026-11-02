"""Native checks: values extracted directly (page DOM, sockets, public APIs).

These produce hard, trustworthy values rather than scraping a third-party UI.
Everything HTTP goes through Playwright's request stack; SSL/WHOIS use sockets.
"""

from __future__ import annotations

import re
import socket
import ssl
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..core import AuditContext, CheckResult, Status, check

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 bd-auto/1.0"
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _get(ctx: AuditContext, url: str, timeout: int = 20000):
    """GET a URL through the browser's HTTP stack. Returns Playwright response or None."""
    try:
        return ctx.api.get(url, headers={"User-Agent": UA}, timeout=timeout, fail_on_status_code=False)
    except Exception:
        return None


def _visible_text(soup: BeautifulSoup) -> str:
    s = BeautifulSoup(str(soup), "lxml")
    for tag in s(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", s.get_text(" ")).strip()


# --------------------------------------------------------------------------
# Domain Age   (smallseotools / duplichecker / webfx)
# --------------------------------------------------------------------------

def _whois_raw(server: str, query: str) -> str:
    with socket.create_connection((server, 43), timeout=12) as s:
        s.sendall((query + "\r\n").encode())
        chunks = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", "ignore")


def _whois(domain: str) -> str:
    tld = domain.rsplit(".", 1)[-1]
    referral = _whois_raw("whois.iana.org", tld)
    m = re.search(r"whois:\s*(\S+)", referral, re.I)
    server = m.group(1).strip() if m else "whois.verisign-grs.com"
    return _whois_raw(server, domain)


_DATE_KEYS = [
    r"Creation Date",
    r"Created On",
    r"created",
    r"Registered on",
    r"Domain Registration Date",
    r"Registration Time",
]


@check("domain_age", "Domain Age", "Domain age (WHOIS registration date)",
       "smallseotools.com / duplichecker.com / webfx.com")
def domain_age(ctx: AuditContext) -> CheckResult:
    raw = _whois(ctx.domain)
    created = None
    for key in _DATE_KEYS:
        m = re.search(key + r"\.*:?\s*([0-9]{4}[-/.][0-9]{2}[-/.][0-9]{2})", raw, re.I)
        if m:
            created = m.group(1).replace("/", "-").replace(".", "-")
            break
    if not created:
        return CheckResult(
            "domain_age", "Domain Age", "Domain age (WHOIS registration date)",
            Status.INFO, "Could not parse a creation date from WHOIS (registry may hide it).",
            source="WHOIS", evidence_url="https://smallseotools.com/domain-age-checker/",
        )
    try:
        cdate = datetime.strptime(created[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        cdate = None
    if cdate:
        years = (datetime.now(timezone.utc) - cdate).days / 365.25
        status = Status.PASS if years >= 1 else Status.WARN
        summary = f"Registered {created[:10]} — about {years:.1f} years old."
        details = [("Creation date", created[:10]), ("Age", f"{years:.1f} years"), ("Domain", ctx.domain)]
    else:
        status, summary, details = Status.INFO, f"Creation date: {created}", [("Creation date", created)]
    return CheckResult("domain_age", "Domain Age", "Domain age (WHOIS registration date)",
                       status, summary, details, source="WHOIS",
                       evidence_url="https://smallseotools.com/domain-age-checker/")


# --------------------------------------------------------------------------
# Robots.txt
# --------------------------------------------------------------------------

@check("robots", "Robots.txt", "robots.txt presence & directives", "website.com/robots.txt")
def robots_txt(ctx: AuditContext) -> CheckResult:
    # robots.txt reflects a site-owner *choice*, not a defect. A missing file, or
    # one that intentionally blocks crawling, is reported as a neutral state
    # ("disabled") rather than a failure/critical — it's configuration, not breakage.
    url = f"{ctx.scheme}://{ctx.domain}/robots.txt"
    r = _get(ctx, url)
    eurl = url
    if not r or r.status >= 400:
        return CheckResult("robots", "Robots.txt", "robots.txt presence & directives",
                           Status.INFO,
                           "No robots.txt file — crawlers may access the whole site by default.",
                           [("Status", "Not present")], source="direct", evidence_url=eurl)
    body = r.text()
    if "<html" in body[:500].lower():
        return CheckResult("robots", "Robots.txt", "robots.txt presence & directives",
                           Status.INFO, "robots.txt URL returned an HTML page, not a robots file.",
                           [("Status", "Not a valid robots file")], source="direct", evidence_url=eurl)
    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", body)
    disallows = re.findall(r"(?im)^\s*disallow:\s*(\S*)", body)
    blocks_all = any(d.strip() == "/" for d in disallows)
    details = [("Status", "Disabled (blocks all crawlers)" if blocks_all else "Enabled"),
               ("Bytes", str(len(body))), ("Sitemap entries", str(len(sitemaps))),
               ("Disallow rules", str(len(disallows)))]
    if sitemaps:
        details.append(("Declared sitemaps", ", ".join(sitemaps[:3])))
    if blocks_all:
        # Crawling is switched off by choice — surface it as "disabled", info-level.
        return CheckResult("robots", "Robots.txt", "robots.txt presence & directives",
                           Status.INFO,
                           "Crawling disabled via robots.txt (contains 'Disallow: /').",
                           details, source="direct", evidence_url=eurl)
    return CheckResult("robots", "Robots.txt", "robots.txt presence & directives",
                       Status.PASS, "robots.txt present and not blocking the whole site.",
                       details, source="direct", evidence_url=eurl)


# --------------------------------------------------------------------------
# XML sitemap
# --------------------------------------------------------------------------

@check("sitemap_xml", "XML Sitemap", "XML sitemap availability", "website.com/sitemap.xml")
def sitemap_xml(ctx: AuditContext) -> CheckResult:
    candidates = [f"{ctx.scheme}://{ctx.domain}/sitemap.xml",
                  f"{ctx.scheme}://{ctx.domain}/sitemap_index.xml"]
    for url in candidates:
        r = _get(ctx, url)
        if r and r.status < 400:
            body = r.text()
            if "<urlset" in body or "<sitemapindex" in body:
                locs = re.findall(r"<loc>\s*([^<\s]+)", body)
                is_index = "<sitemapindex" in body
                details = [("URL", url), ("Type", "sitemap index" if is_index else "urlset"),
                           ("Entries", str(len(locs)))]
                return CheckResult("sitemap_xml", "XML Sitemap", "XML sitemap availability",
                                   Status.PASS, f"XML sitemap found with {len(locs)} entries.",
                                   details, source="direct", evidence_url=url)
    return CheckResult("sitemap_xml", "XML Sitemap", "XML sitemap availability",
                       Status.FAIL, "No XML sitemap found at /sitemap.xml or /sitemap_index.xml.",
                       source="direct", evidence_url=candidates[0])


@check("sitemap_html", "HTML Sitemap", "HTML sitemap availability", "website.com/sitemap.html")
def sitemap_html(ctx: AuditContext) -> CheckResult:
    candidates = [f"{ctx.scheme}://{ctx.domain}/sitemap.html",
                  f"{ctx.scheme}://{ctx.domain}/sitemap",
                  f"{ctx.scheme}://{ctx.domain}/site-map"]
    for url in candidates:
        r = _get(ctx, url)
        if r and r.status < 400 and "<html" in r.text()[:1000].lower():
            links = len(re.findall(r"<a\s", r.text(), re.I))
            return CheckResult("sitemap_html", "HTML Sitemap", "HTML sitemap availability",
                               Status.PASS, f"HTML sitemap page found ({links} links).",
                               [("URL", url), ("Links", str(links))], source="direct", evidence_url=url)
    return CheckResult("sitemap_html", "HTML Sitemap", "HTML sitemap availability",
                       Status.INFO, "No HTML sitemap found (optional; many sites omit it).",
                       source="direct", evidence_url=candidates[0])


# --------------------------------------------------------------------------
# Canonical tag
# --------------------------------------------------------------------------

@check("canonical", "Canonical Tag", "Canonical tag on homepage",
       "coderseo.com / seositecheckup.com / seoreviewtools.com")
def canonical(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("canonical", "Canonical Tag", "Canonical tag on homepage",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    # NB: don't pass a filter *function* for rel — bs4 4.15 hands the function
    # the space-joined string ("canonical"), so iterating it yields characters
    # and the tag is never matched (canonical then falsely reads as missing).
    # Read rel off each tag instead, where .get() reliably returns the token list.
    links = [l for l in ctx.home_soup.find_all("link")
             if "canonical" in [x.lower() for x in (l.get("rel") or [])]]
    if not links:
        return CheckResult("canonical", "Canonical Tag", "Canonical tag on homepage",
                           Status.WARN, "No <link rel=\"canonical\"> found on the homepage.",
                           source="DOM")
    hrefs = [l.get("href", "") for l in links]
    status = Status.PASS if len(hrefs) == 1 else Status.WARN
    summary = (f"Canonical points to: {hrefs[0]}" if len(hrefs) == 1
               else f"Multiple ({len(hrefs)}) canonical tags found — should be exactly one.")
    return CheckResult("canonical", "Canonical Tag", "Canonical tag on homepage",
                       status, summary, [("Canonical URL(s)", " | ".join(hrefs))], source="DOM")


# --------------------------------------------------------------------------
# SSL check
# --------------------------------------------------------------------------

@check("ssl", "SSL Certificate", "SSL/TLS certificate validity", "geocerts.com/ssl-checker")
def ssl_check(ctx: AuditContext) -> CheckResult:
    host = ctx.domain
    try:
        sctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=12) as sock:
            with sctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
    except Exception as exc:
        return CheckResult("ssl", "SSL Certificate", "SSL/TLS certificate validity",
                           Status.FAIL, f"TLS handshake failed: {exc}", source="socket")
    not_after = cert.get("notAfter")
    issuer = dict(x[0] for x in cert.get("issuer", [])).get("organizationName", "?")
    subject = dict(x[0] for x in cert.get("subject", [])).get("commonName", host)
    days = None
    if not_after:
        try:
            exp = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days = (exp - datetime.now(timezone.utc)).days
        except ValueError:
            pass
    if days is not None and days < 0:
        status, summary = Status.FAIL, f"Certificate EXPIRED {abs(days)} days ago."
    elif days is not None and days < 15:
        status, summary = Status.WARN, f"Certificate expires in {days} days — renew soon."
    else:
        status, summary = Status.PASS, f"Valid certificate (issued by {issuer})."
    details = [("Common name", subject), ("Issuer", issuer), ("Expires", not_after or "?")]
    if days is not None:
        details.append(("Days remaining", str(days)))
    return CheckResult("ssl", "SSL Certificate", "SSL/TLS certificate validity",
                       status, summary, details, source="socket",
                       evidence_url="https://www.geocerts.com/ssl-checker")


# --------------------------------------------------------------------------
# W3C markup validation  (validator.w3.org nu JSON API)
# --------------------------------------------------------------------------

@check("w3c", "W3C Validation", "W3C HTML markup validation", "validator.w3.org")
def w3c(ctx: AuditContext) -> CheckResult:
    api = f"https://validator.w3.org/nu/?doc={ctx.url}&out=json"
    r = _get(ctx, api, timeout=40000)
    if not r or r.status >= 400:
        return CheckResult("w3c", "W3C Validation", "W3C HTML markup validation",
                           Status.INFO, "The W3C validator did not return a result, so markup "
                           "could not be validated automatically this run.",
                           source="validator.w3.org", evidence_url=f"https://validator.w3.org/nu/?doc={ctx.url}")
    try:
        data = r.json()
        msgs = data.get("messages", [])
    except Exception:
        return CheckResult("w3c", "W3C Validation", "W3C HTML markup validation",
                           Status.INFO, "The W3C validator response could not be parsed, so markup "
                           "could not be validated automatically this run.",
                           source="validator.w3.org", evidence_url=f"https://validator.w3.org/nu/?doc={ctx.url}")
    errors = sum(1 for m in msgs if m.get("type") == "error")
    warns = sum(1 for m in msgs if m.get("type") == "info" or m.get("subType") == "warning")
    status = Status.PASS if errors == 0 else (Status.WARN if errors <= 10 else Status.FAIL)
    summary = f"{errors} error(s) and {warns} warning(s) reported by the W3C validator."
    sample = [m.get("message", "")[:120] for m in msgs if m.get("type") == "error"][:5]
    details = [("Errors", str(errors)), ("Warnings", str(warns))]
    details += [("Error sample", s) for s in sample]
    return CheckResult("w3c", "W3C Validation", "W3C HTML markup validation",
                       status, summary, details, source="validator.w3.org",
                       evidence_url=f"https://validator.w3.org/nu/?doc={ctx.url}")


# --------------------------------------------------------------------------
# Server response time
# --------------------------------------------------------------------------

@check("response_time", "Server Response Time", "Time to first byte / load", "bitcatcha.com")
def response_time(ctx: AuditContext) -> CheckResult:
    ttfb = ctx.home_ttfb_ms
    load = ctx.home_load_ms
    if not ttfb:
        return CheckResult("response_time", "Server Response Time", "Time to first byte / load",
                           Status.ERROR, "Timing unavailable (homepage did not load).", source="timing")
    if ttfb < 200:
        status, grade = Status.PASS, "excellent"
    elif ttfb < 600:
        status, grade = Status.PASS, "good"
    elif ttfb < 1200:
        status, grade = Status.WARN, "slow"
    else:
        status, grade = Status.FAIL, "very slow"
    return CheckResult("response_time", "Server Response Time", "Time to first byte / load",
                       status, f"TTFB {ttfb:.0f} ms ({grade}); full load {load:.0f} ms.",
                       [("TTFB", f"{ttfb:.0f} ms"), ("Full load", f"{load:.0f} ms")],
                       source="timing", evidence_url="https://www.bitcatcha.com/")


# --------------------------------------------------------------------------
# JavaScript minification
# --------------------------------------------------------------------------

@check("js_minify", "JS Minification", "External JavaScript minification",
       "seositecheckup.com/tools/javascript-minification-test")
def js_minify(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("js_minify", "JS Minification", "External JavaScript minification",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    srcs = [s.get("src") for s in ctx.home_soup.find_all("script") if s.get("src")]
    srcs = [urljoin(ctx.url, s) for s in srcs][:8]
    if not srcs:
        return CheckResult("js_minify", "JS Minification", "External JavaScript minification",
                           Status.INFO, "No external JavaScript files found on the homepage.",
                           source="heuristic")
    checked = minified = 0
    rows = []
    for s in srcs:
        r = _get(ctx, s, timeout=15000)
        if not r or r.status >= 400:
            continue
        body = r.text()
        if not body.strip():
            continue
        checked += 1
        lines = body.count("\n") + 1
        avg_len = len(body) / lines
        is_min = ".min.js" in s.lower() or avg_len > 200
        minified += 1 if is_min else 0
        rows.append((s.split("/")[-1][:40], "minified" if is_min else "NOT minified"))
    if checked == 0:
        return CheckResult("js_minify", "JS Minification", "External JavaScript minification",
                           Status.INFO, "Could not fetch external JS files to inspect.", source="heuristic")
    status = Status.PASS if minified == checked else Status.WARN
    summary = f"{minified}/{checked} sampled JS files appear minified."
    return CheckResult("js_minify", "JS Minification", "External JavaScript minification",
                       status, summary, rows, source="heuristic",
                       evidence_url="https://seositecheckup.com/tools/javascript-minification-test")


# --------------------------------------------------------------------------
# Title tag
# --------------------------------------------------------------------------

@check("title", "Title Tag", "Title tag optimization", "SEOquake")
def title_tag(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("title", "Title Tag", "Title tag optimization",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    t = ctx.home_soup.find("title")
    text = t.get_text(strip=True) if t else ""
    if not text:
        return CheckResult("title", "Title Tag", "Title tag optimization",
                           Status.FAIL, "No <title> tag found.", source="DOM")
    n = len(text)
    if n < 30:
        status, note = Status.WARN, "too short (<30 chars)"
    elif n > 65:
        status, note = Status.WARN, "too long (>65 chars, may truncate in SERP)"
    else:
        status, note = Status.PASS, "good length"
    return CheckResult("title", "Title Tag", "Title tag optimization",
                       status, f"Title is {n} chars — {note}.",
                       [("Title", text), ("Length", str(n))], source="DOM")


@check("meta_desc", "Meta Description", "Meta description tag", "SEOquake")
def meta_desc(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("meta_desc", "Meta Description", "Meta description tag",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    m = ctx.home_soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    text = (m.get("content") or "").strip() if m else ""
    if not text:
        return CheckResult("meta_desc", "Meta Description", "Meta description tag",
                           Status.WARN, "No meta description found.", source="DOM")
    n = len(text)
    if n < 70:
        status, note = Status.WARN, "too short (<70 chars)"
    elif n > 160:
        status, note = Status.WARN, "too long (>160 chars, may truncate)"
    else:
        status, note = Status.PASS, "good length"
    return CheckResult("meta_desc", "Meta Description", "Meta description tag",
                       status, f"Meta description is {n} chars — {note}.",
                       [("Description", text), ("Length", str(n))], source="DOM")


@check("headings", "Heading Tags", "Heading structure (H1–H6)", "seoreviewtools.com/html-headings-checker")
def headings(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("headings", "Heading Tags", "Heading structure (H1–H6)",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    counts = {f"h{i}": len(ctx.home_soup.find_all(f"h{i}")) for i in range(1, 7)}
    h1 = counts["h1"]
    if h1 == 0:
        status, summary = Status.FAIL, "No H1 heading found."
    elif h1 > 1:
        status, summary = Status.WARN, f"{h1} H1 headings found — usually should be exactly one."
    else:
        status, summary = Status.PASS, "Exactly one H1; heading structure present."
    details = [(k.upper(), str(v)) for k, v in counts.items()]
    return CheckResult("headings", "Heading Tags", "Heading structure (H1–H6)",
                       status, summary, details, source="DOM",
                       evidence_url="https://www.seoreviewtools.com/html-headings-checker/")


# --------------------------------------------------------------------------
# Google PageSpeed Insights  (works without key, rate-limited)
# --------------------------------------------------------------------------

@check("pagespeed", "PageSpeed Insights", "Google PageSpeed / Core Web Vitals",
       "developers.google.com/speed/pagespeed/insights")
def pagespeed(ctx: AuditContext) -> CheckResult:
    key = ctx.config.get("pagespeed_key")
    api = ("https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
           f"?url={ctx.url}&strategy=mobile&category=performance")
    if key:
        api += f"&key={key}"
    r = _get(ctx, api, timeout=60000)
    eurl = f"https://pagespeed.web.dev/report?url={ctx.url}"
    if not r or r.status >= 400:
        return CheckResult("pagespeed", "PageSpeed Insights", "Google PageSpeed / Core Web Vitals",
                           Status.INFO, "Google PageSpeed was unavailable (the keyless API is "
                           "rate-limited); see the on-page load-time check for measured timing.",
                           source="Google API", evidence_url=eurl)
    try:
        data = r.json()
        lh = data["lighthouseResult"]
        score = round(lh["categories"]["performance"]["score"] * 100)
        audits = lh.get("audits", {})

        def metric(k):
            return audits.get(k, {}).get("displayValue", "?")
    except Exception:
        return CheckResult("pagespeed", "PageSpeed Insights", "Google PageSpeed / Core Web Vitals",
                           Status.INFO, "The PageSpeed response could not be parsed; see the on-page "
                           "load-time check for measured timing.",
                           source="Google API", evidence_url=eurl)
    status = Status.PASS if score >= 90 else (Status.WARN if score >= 50 else Status.FAIL)
    details = [("Performance score", f"{score}/100"),
               ("First Contentful Paint", metric("first-contentful-paint")),
               ("Largest Contentful Paint", metric("largest-contentful-paint")),
               ("Total Blocking Time", metric("total-blocking-time")),
               ("Cumulative Layout Shift", metric("cumulative-layout-shift"))]
    return CheckResult("pagespeed", "PageSpeed Insights", "Google PageSpeed / Core Web Vitals",
                       status, f"Mobile performance score: {score}/100.", details,
                       source="Google API", evidence_url=eurl)


# --------------------------------------------------------------------------
# Broken link checker
# --------------------------------------------------------------------------

@check("broken_links", "Broken Links", "Broken links across the site", "drlinkcheck.com / Ahrefs")
def broken_links(ctx: AuditContext) -> CheckResult:
    # Prefer the whole-site crawl: it already recorded every internal link's
    # HTTP status, so we can report broken links domain-wide rather than just
    # on the homepage. Fall back to the homepage sweep if the crawl is missing.
    crawl = getattr(ctx, "crawl", None)
    if crawl is not None and getattr(crawl, "pages_crawled", 0):
        broken = crawl.broken
        pages = crawl.pages_crawled
        if not broken:
            return CheckResult("broken_links", "Broken Links", "Broken links across the site",
                               Status.PASS, f"No broken internal links found across {pages} crawled pages.",
                               [("Pages crawled", f"{pages:,}"), ("Broken links", "0")],
                               source="crawl", evidence_url="https://www.drlinkcheck.com/")
        status = Status.FAIL if len(broken) > 3 else Status.WARN
        details = [("Pages crawled", f"{pages:,}"), ("Broken links", f"{len(broken):,}")]
        details += [(str(b["url"])[:60], f"HTTP {b['status']}") for b in broken[:12]]
        return CheckResult("broken_links", "Broken Links", "Broken links across the site",
                           status, f"{len(broken)} broken internal link(s) found across {pages} pages.",
                           details, source="crawl", evidence_url="https://www.drlinkcheck.com/")

    if ctx.home_soup is None:
        return CheckResult("broken_links", "Broken Links", "Broken links across the site",
                           Status.ERROR, "Homepage DOM unavailable.", source="crawl")
    hrefs = []
    for a in ctx.home_soup.find_all("a", href=True):
        h = a["href"].strip()
        if h.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        hrefs.append(urljoin(ctx.url, h))
    hrefs = list(dict.fromkeys(hrefs))[:40]
    broken = []
    for h in hrefs:
        r = _get(ctx, h, timeout=12000)
        if not r:
            broken.append((h, "no response"))
        elif r.status >= 400:
            broken.append((h, f"HTTP {r.status}"))
    checked = len(hrefs)
    if not broken:
        return CheckResult("broken_links", "Broken Links", "Broken links across the site",
                           Status.PASS, f"Checked {checked} homepage links — none broken.",
                           [("Links checked", str(checked))], source="crawl",
                           evidence_url="https://www.drlinkcheck.com/")
    status = Status.FAIL if len(broken) > 2 else Status.WARN
    details = [("Links checked", str(checked)), ("Broken", str(len(broken)))]
    details += [(u[:60], reason) for u, reason in broken[:10]]
    return CheckResult("broken_links", "Broken Links", "Broken links across the site",
                       status, f"{len(broken)} of {checked} homepage links are broken.",
                       details, source="crawl", evidence_url="https://www.drlinkcheck.com/")


# --------------------------------------------------------------------------
# Mobile friendliness (viewport heuristic — Google's test API is retired)
# --------------------------------------------------------------------------

@check("mobile", "Mobile Friendliness", "Mobile-friendly signals",
       "search.google.com/test/mobile-friendly")
def mobile(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("mobile", "Mobile Friendliness", "Mobile-friendly signals",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    vp = ctx.home_soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    content = (vp.get("content") if vp else "") or ""
    has_width = "width=device-width" in content.replace(" ", "")
    if vp and has_width:
        status, summary = Status.PASS, "Responsive viewport meta tag present (width=device-width)."
    elif vp:
        status, summary = Status.WARN, f"Viewport tag present but unusual: '{content}'."
    else:
        status, summary = Status.FAIL, "No viewport meta tag — likely not mobile-friendly."
    return CheckResult("mobile", "Mobile Friendliness", "Mobile-friendly signals",
                       status, summary, [("Viewport", content or "(none)")], source="DOM",
                       evidence_url="https://search.google.com/test/mobile-friendly")


@check("amp", "AMP", "AMP version presence", "search.google.com/test/amp")
def amp(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("amp", "AMP", "AMP version presence",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    amphtml = ctx.home_soup.find("link", rel=lambda v: v and "amphtml" in [x.lower() for x in v])
    is_amp = bool(ctx.home_soup.find("html", attrs={"amp": True})) or "⚡" in str(ctx.home_soup.find("html"))
    if amphtml:
        return CheckResult("amp", "AMP", "AMP version presence", Status.INFO,
                           f"AMP version declared: {amphtml.get('href')}",
                           [("amphtml", amphtml.get("href", ""))], source="DOM",
                           evidence_url="https://search.google.com/test/amp")
    if is_amp:
        return CheckResult("amp", "AMP", "AMP version presence", Status.INFO,
                           "Homepage itself is an AMP page.", source="DOM")
    return CheckResult("amp", "AMP", "AMP version presence", Status.INFO,
                       "No AMP version found (AMP is optional and increasingly deprecated).",
                       source="DOM", evidence_url="https://search.google.com/test/amp")


# --------------------------------------------------------------------------
# Structured data / Rich results
# --------------------------------------------------------------------------

@check("structured_data", "Structured Data", "Structured data / rich results",
       "search.google.com/test/rich-results")
def structured_data(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("structured_data", "Structured Data", "Structured data / rich results",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    import json
    types: list[str] = []
    for s in ctx.home_soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or "{}")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict):
                t = it.get("@type")
                if isinstance(t, list):
                    types.extend(str(x) for x in t)
                elif t:
                    types.append(str(t))
    microdata = len(ctx.home_soup.find_all(attrs={"itemtype": True}))
    eurl = f"https://search.google.com/test/rich-results?url={ctx.url}"
    if not types and not microdata:
        return CheckResult("structured_data", "Structured Data", "Structured data / rich results",
                           Status.WARN, "No JSON-LD or microdata structured data found.",
                           source="DOM", evidence_url=eurl)
    details = [("JSON-LD blocks", str(len(set(types)))), ("Schema types", ", ".join(sorted(set(types))) or "—"),
               ("Microdata items", str(microdata))]
    return CheckResult("structured_data", "Structured Data", "Structured data / rich results",
                       Status.PASS, f"Structured data present: {', '.join(sorted(set(types))) or 'microdata'}.",
                       details, source="DOM", evidence_url=eurl)


# --------------------------------------------------------------------------
# Keywords cloud
# --------------------------------------------------------------------------

_STOP = set("""a an the and or but if then else for to of in on at by with from as is are was were be been
being this that these those it its it's you your we our they their he she his her i me my will would can
could should have has had do does did not no so up out about into over after under more most other some such
only own same than too very just also can't won't don't""".split())


@check("keywords", "Keywords Cloud", "Top keywords by frequency", "seositecheckup.com/analysis")
def keywords_cloud(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("keywords", "Keywords Cloud", "Top keywords by frequency",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    text = _visible_text(ctx.home_soup).lower()
    words = re.findall(r"[a-z][a-z'-]{2,}", text)
    words = [w for w in words if w not in _STOP]
    top = Counter(words).most_common(15)
    if not top:
        return CheckResult("keywords", "Keywords Cloud", "Top keywords by frequency",
                           Status.INFO, "Not enough text to build a keyword cloud.", source="DOM")
    details = [(w, str(c)) for w, c in top]
    summary = "Top terms: " + ", ".join(f"{w}({c})" for w, c in top[:8])
    return CheckResult("keywords", "Keywords Cloud", "Top keywords by frequency",
                       Status.INFO, summary, details, source="DOM",
                       evidence_url="https://seositecheckup.com/analysis")


@check("interlinking", "Internal Linking", "Internal vs external links",
       "seoreviewtools.com/internal-link-analyzer")
def interlinking(ctx: AuditContext) -> CheckResult:
    if ctx.home_soup is None:
        return CheckResult("interlinking", "Internal Linking", "Internal vs external links",
                           Status.ERROR, "Homepage DOM unavailable.", source="DOM")
    internal = external = 0
    for a in ctx.home_soup.find_all("a", href=True):
        h = a["href"].strip()
        if h.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        host = urlparse(urljoin(ctx.url, h)).netloc.lower().replace("www.", "")
        if host == "" or host == ctx.domain:
            internal += 1
        else:
            external += 1
    total = internal + external
    status = Status.PASS if internal >= 1 else Status.WARN
    return CheckResult("interlinking", "Internal Linking", "Internal vs external links",
                       status, f"{internal} internal and {external} external links on the homepage.",
                       [("Internal links", str(internal)), ("External links", str(external)),
                        ("Total", str(total))], source="DOM",
                       evidence_url="https://www.seoreviewtools.com/internal-link-analyzer/")


@check("text_ratio", "Text/HTML Ratio", "Code-to-text ratio", "smallseotools.com/code-to-text-ratio-checker")
def text_ratio(ctx: AuditContext) -> CheckResult:
    if not ctx.home_html:
        return CheckResult("text_ratio", "Text/HTML Ratio", "Code-to-text ratio",
                           Status.ERROR, "Homepage HTML unavailable.", source="DOM")
    html_len = len(ctx.home_html.encode("utf-8"))
    text = _visible_text(ctx.home_soup) if ctx.home_soup is not None else ""
    text_len = len(text.encode("utf-8"))
    ratio = (text_len / html_len * 100) if html_len else 0
    if ratio >= 25:
        status, note = Status.PASS, "healthy"
    elif ratio >= 10:
        status, note = Status.WARN, "low — consider trimming markup"
    else:
        status, note = Status.FAIL, "very low — page is mostly code"
    return CheckResult("text_ratio", "Text/HTML Ratio", "Code-to-text ratio",
                       status, f"Text-to-HTML ratio is {ratio:.1f}% ({note}).",
                       [("Text bytes", str(text_len)), ("HTML bytes", str(html_len)),
                        ("Ratio", f"{ratio:.1f}%")], source="DOM",
                       evidence_url="https://smallseotools.com/code-to-text-ratio-checker/")
