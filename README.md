
# bd-auto — Automated Technical SEO Audit

Point it at a client URL; it drives a headless Chromium browser through ~26
SEO/technical checks and produces a self-contained **PDF report** (plus HTML and
evidence screenshots). A Groq-powered AI layer turns the raw checks into a
client-ready analysis — overall grade, executive summary, prioritised findings
and quick wins.

Two ways to run it: a **web frontend** (paste a URL in the browser) or the CLI.

### Web frontend (recommended)

```powershell
.\.venv\Scripts\python.exe server.py
```

Then open **http://127.0.0.1:8000**, paste the client URL, optionally add your
Groq API key (stored only in your browser), and click *Run audit*. You get live
progress and download links for the PDF / HTML report. No extra dependencies —
the server is pure standard library.

### CLI

```
py audit.py https://client.com --groq-key gsk_...
```

Output lands in `reports/<domain>-<timestamp>/`:
- `deck.pdf` — the single client **deck**: the audit findings (issues + whole-site
  data) followed by our solution (strategy, roadmap, keyword plan, investment)
- `deck.html` — same deck, web version
- `assets/` — homepage + evidence screenshots

With a Groq key the deck includes both halves; without one it renders as an
audit-only deck (no solution chapter).

## Deploy on Render

The app ships as a Docker web service (Playwright needs a real Chromium build
plus system libraries that Render's native Python runtime can't install).
**Plug-and-play — there are no environment variables to configure.**

1. Push this repo to GitHub.
2. In Render: **New + → Blueprint**, pick the repo. Render reads `render.yaml`
   and provisions a Docker web service (`heyaudit`).
3. Deploy. The server binds `0.0.0.0:$PORT` automatically (Render injects
   `$PORT`); the health check hits `/`.

Notes:
- The audit runs entirely on free, first-party data (no third-party API key).
- The AI-written strategy half of the deck is optional: paste a Groq API key
  into the web form at run time (it's stored only in your browser). Without one,
  the deck renders as an audit-only document.
- A whole-site crawl + Chromium is memory-hungry; the blueprint requests the
  `standard` plan. For lighter sites you can lower the plan and cap pages with
  the `max_pages` field in the audit form.
- Generated reports under `reports/` are ephemeral on Render (regenerated per
  run and downloaded through the UI).

## Setup (one time, local)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Then run with the venv's Python:

```powershell
.\.venv\Scripts\python.exe audit.py https://client.com
```

## Usage

```
py audit.py <url> [--out DIR] [--pagespeed-key KEY] [--groq-key KEY] [--groq-model M] [--no-pdf] [--no-pitch]

  url                 Client URL or bare domain (example.com or https://example.com)
  --out DIR           Output directory (default: reports)
  --pagespeed-key K   Google PageSpeed API key (lifts rate limits on the speed check)
  --groq-key K        Groq API key for the AI analysis + pitch deck (or set GROQ_API_KEY,
                      or drop the key in a .groq_key file in the project root)
  --groq-model M      Groq model (default: llama-3.3-70b-versatile)
  --no-pdf            Produce HTML only, skip PDF rendering
  --no-pitch          Skip the solution half — render an audit-only deck
```

## AI analysis (Groq)

With a Groq API key, the report gains an **AI Analysis** section at the top: an
overall letter grade + 0–100 health score, a plain-English executive summary,
4–8 prioritised findings (each with a severity and a specific fix) and a list of
quick wins. The model only interprets the data the audit actually collected.

Provide the key via the frontend field, the `--groq-key` flag, or a
`GROQ_API_KEY` environment variable. Without a key the report renders exactly as
before (no AI section). Calls go to the Groq API over the standard library — no
SDK to install.

## Solution half of the deck (Groq)

With a Groq key, the deck's second half turns the audit into a **6-month
cornerstone SEO / GEO / AEO strategy** — a professional, yellow/black/white
proposal built
around Justwords' Next Gen SEO Packages. Written in the voice of a Senior
SEO/AEO/GEO & Content Director, it includes:

- a 5-sentence executive summary and the "Justwords advantage",
- a **6-month roadmap** broken into monthly phases,
- a **keyword clustering table** (seed keyword · intent · pillar URL) plus a
  table of high-intent keyword targets with estimated volume/difficulty,
- the **Competitive Moat** — one durable strategic play tied to the audit's
  weaknesses,
- a **content plan** (buckets, pieces/month, E-E-A-T + GEO/AEO standard),
- a **recommended package** with rationale and the full deliverables matrix.

Pricing and deliverable counts are rendered deterministically from
`auditor/packages.py` — the model recommends a tier and writes the strategy, but
it never invents a price. Skip it with `--no-pitch`.

## What it checks

Every check runs through a real browser. Results are graded
**PASS / WARN / FAIL / INFO / ERROR**.

| Category | How it's done |
|---|---|
| Domain age | Live WHOIS lookup (registration date → age) |
| Robots.txt / XML sitemap / HTML sitemap | Direct fetch + parse |
| Canonical tag | Parsed from the rendered homepage DOM |
| SSL certificate | Live TLS handshake (issuer, expiry, days left) |
| W3C markup validation | validator.w3.org Nu JSON API |
| Server response time | Measured TTFB / load during the audit |
| JS minification | Samples external scripts, heuristic check |
| Title / Meta description / Headings | Extracted from the DOM with length rules |
| PageSpeed Insights | Google PageSpeed API (mobile, Core Web Vitals) |
| Broken links | Crawls homepage links, flags 4xx/5xx |
| Mobile friendliness | Viewport meta analysis |
| AMP | `rel=amphtml` detection |
| Structured data / Rich results | JSON-LD + microdata type extraction |
| Keywords cloud | Top term frequencies from visible text |
| Internal linking | Internal vs external link counts |
| Text/HTML ratio | Visible text bytes ÷ HTML bytes |
| Domain categorization | Browser-driven (Klazify) + screenshot |
| Backlinks | Browser-driven (Ahrefs) + screenshot |
| Indexed pages | Google + Bing `site:` queries + screenshot |
| GTmetrix | Browser-driven + screenshot |
| Content analytics | Browser-driven (Siteliner) + screenshot |

### Hard values vs. INFO/screenshot

- **Hard values**: anything readable from the page DOM, sockets, or a public API
  (domain age, SSL, title, meta, headings, canonical, text ratio, structured
  data, links, response time, W3C, PageSpeed). These reflect live values.
- **INFO + screenshot**: tools with no public API and CAPTCHA/login walls
  (Klazify, Ahrefs, GTmetrix, Siteliner, Google/Bing `site:`). The tool still
  navigates there and captures a screenshot as evidence for manual review.

## Architecture

```
audit.py                 CLI entry point
auditor/
  core.py                CheckResult model, Status enum, check registry, URL helpers
  runner.py              Launches Chromium, loads homepage once, runs all checks isolated
  report.py              Renders HTML (Jinja2) then PDF (Chromium page.pdf)
  templates/report.html.j2
  checks/
    native.py            DOM/socket/API checks (hard values)
    scrapers.py          Browser-driven third-party tools (screenshot evidence)
```

Adding a check: write a function decorated with
`@check(id, category, title, source)` in `native.py` or `scrapers.py`. It
receives an `AuditContext` (shared homepage DOM, timing, a browser, an HTTP
client) and returns a `CheckResult`. It's auto-registered and appears in the
report. Each check is isolated — an exception in one becomes an `ERROR` row, it
never aborts the run.

## Optional integrations

- **Google PageSpeed key** — pass `--pagespeed-key` to avoid keyless rate limits.
- **Ahrefs / GTmetrix APIs** — currently screenshot-only; wire their APIs into
  the matching functions in `scrapers.py` for hard numbers.

## Notes

- Third-party sites change layout and may block automation; those checks degrade
  to INFO + screenshot rather than failing the audit.
- The response-time figure is a navigation-timing proxy, not a multi-region test.
```
