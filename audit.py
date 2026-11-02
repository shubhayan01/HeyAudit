#!/usr/bin/env python
"""bd-auto — automated technical SEO audit + client growth proposal.

Usage:
    py audit.py https://client.com
    py audit.py client.com --competitors rival-a.com,rival-b.com
    py audit.py client.com --discovery discovery.json

Drops a timestamped folder under the output dir containing a single combined
client deck (deck.html/.pdf) — the audit findings followed by our solution — and
an assets/ folder of evidence screenshots.

Discovery-call details (budget, current leads, close rate, goals, pain points)
can't be found by auditing a URL, so pass them with the --client-* / --budget /
--current-leads flags below, or collect them all in a JSON file and pass
--discovery. Anything you leave out simply drops out of the proposal.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from auditor.discovery import Discovery
from auditor.pipeline import generate_deliverables
from auditor.pitch import resolve_key as resolve_groq_key
from auditor.runner import run_audit


def _slug(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")


def _build_discovery(args) -> Discovery:
    """Discovery data from --discovery FILE, with individual flags overriding it."""
    data: dict = {}
    if args.discovery:
        try:
            data = json.loads(Path(args.discovery).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! Could not read --discovery {args.discovery} ({exc}); ignoring.")
            data = {}

    for key, val in (
        ("client_name", args.client_name), ("prepared_by", args.prepared_by),
        ("prepared_by_title", args.prepared_by_title), ("currency", args.currency),
        ("budget", args.budget), ("current_leads", args.current_leads),
        ("close_rate", args.close_rate), ("avg_deal_value", args.avg_deal_value),
        ("target_leads", args.target_leads), ("goal", args.goal),
        ("geo_focus", args.geo_focus), ("services", args.services),
        ("competitors", args.competitors),
        ("include_categories", args.include_categories),
        ("da_override", args.da), ("organic_override", args.organic),
    ):
        if val is not None:
            data[key] = val
    if args.pain_point:
        data["pain_points"] = args.pain_point
    return Discovery.from_dict(data)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="Automated technical SEO audit -> client health report + growth proposal.")
    ap.add_argument("url", help="Client URL or bare domain (e.g. https://example.com or example.com)")
    ap.add_argument("--out", default="reports", help="Output directory (default: reports)")
    ap.add_argument("--pagespeed-key", default=None, help="Optional Google PageSpeed API key")
    ap.add_argument("--seoptimer-key", default=None,
                    help="Optional SEOptimer API key (also read from SEOPTIMER_API_KEY). "
                         "Enables reliable detailed data for the CAPTCHA/rate-limited checks.")
    ap.add_argument("--groq-key", default=None,
                    help="Groq API key for the AI analysis + proposal (also read from "
                         "GROQ_API_KEY or a .groq_key file in the project root).")
    ap.add_argument("--groq-model", default=None, help="Override the Groq model.")
    ap.add_argument("--no-pdf", action="store_true", help="Skip PDF rendering (HTML only)")
    ap.add_argument("--no-pitch", action="store_true",
                    help="Skip the solution half — render an audit-only deck.")
    ap.add_argument("--tier", default=None,
                    choices=["starter", "growth", "professional", "enterprise"],
                    help="Force the package tier the deck recommends. "
                         "Omit to let the client's budget decide (else Growth).")

    disc = ap.add_argument_group(
        "discovery call",
        "Business facts an audit can't discover. Optional — anything omitted is "
        "left out of the proposal rather than guessed at.")
    disc.add_argument("--discovery", default=None, metavar="FILE",
                      help="JSON file of discovery-call answers (individual flags override it).")
    disc.add_argument("--competitors", default=None, metavar="LIST",
                      help="Up to 5 competitor domains, comma-separated. Each gets a real "
                           "SEOptimer audit (1 API credit and ~70s each, run in parallel).")
    disc.add_argument("--client-name", default=None, help="Client company name for the cover.")
    disc.add_argument("--prepared-by", default=None, help="Your name.")
    disc.add_argument("--prepared-by-title", default=None, help="Your job title.")
    disc.add_argument("--currency", default=None, choices=["INR", "USD", "GBP", "EUR", "AED"],
                      help="Currency for budget/deal values (default: INR).")
    disc.add_argument("--budget", default=None, metavar="RANGE",
                      help="Their monthly marketing budget, e.g. '50K-95K'.")
    disc.add_argument("--current-leads", default=None, metavar="N",
                      help="Current inbound leads per month.")
    disc.add_argument("--close-rate", default=None, metavar="PCT",
                      help="Percentage of leads that close, e.g. 25.")
    disc.add_argument("--avg-deal-value", default=None, metavar="AMOUNT",
                      help="Average revenue per closed deal, e.g. 150000.")
    disc.add_argument("--target-leads", default=None, metavar="N",
                      help="Leads per month they want.")
    disc.add_argument("--goal", default=None, help="Their stated goal, in their words.")
    disc.add_argument("--geo-focus", default=None, help="Local / Regional / National.")
    disc.add_argument("--services", default=None, metavar="LIST",
                      help="Their primary services, semicolon-separated.")
    disc.add_argument("--pain-point", action="append", metavar="TEXT",
                      help="A pain point they raised (repeat the flag for several).")
    disc.add_argument("--include-categories", default=None, metavar="LIST",
                      help="Service areas to feature in the deck, comma/semicolon-separated "
                           "(e.g. 'Technical SEO;On-Page Optimization'). Default: all.")
    disc.add_argument("--da", default=None, metavar="N",
                      help="True Domain Authority (Ahrefs/Moz) — overrides SEOptimer's estimate.")
    disc.add_argument("--organic", default=None, metavar="N",
                      help="True monthly organic traffic — overrides SEOptimer's estimate.")
    args = ap.parse_args(argv)

    config = {}
    if args.pagespeed_key:
        config["pagespeed_key"] = args.pagespeed_key
    if args.seoptimer_key:
        config["seoptimer_key"] = args.seoptimer_key

    discovery = _build_discovery(args)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(args.out) / f"{_slug(args.url)}-{stamp}"
    assets = base / "assets"

    print(f"\n  bd-auto — auditing {args.url}")
    print(f"  Output: {base.resolve()}")
    if discovery.competitors:
        print(f"  Competitors: {', '.join(discovery.competitors)}")
    print()

    def progress(msg, cat):
        print(f"  • {msg:<46} {cat}")

    ctx, results = run_audit(args.url, assets, config=config, progress=progress)

    groq_key = resolve_groq_key(args.groq_key)
    if not groq_key and not args.no_pitch:
        print("\n  Proposal will be skipped (no Groq key — pass --groq-key, set "
              "GROQ_API_KEY, or add a .groq_key file).")

    print()
    out = generate_deliverables(
        ctx, results, base,
        discovery=discovery,
        groq_key=groq_key,
        groq_model=args.groq_model,
        seoptimer_key=args.seoptimer_key,
        tier=args.tier,
        make_pdf=not args.no_pdf,
        make_proposal=not args.no_pitch,
        progress=progress,
    )

    print()
    for label, path in (("Deck HTML", out["deck_html"]), ("Deck PDF", out["deck_pdf"])):
        if path:
            print(f"  {label + ':':<15}{path.resolve()}")
    if not args.no_pitch and groq_key and not out["pitch"]:
        print("  ! Solution half could not be generated — the deck is audit-only "
              "(see message above).")

    counts = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    print("\n  Summary: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if out["projection"]:           
        p = out["projection"]
        print(f"  Opportunity: +{p['added_visits']:,} visits/mo, "
              f"+{p['added_leads']:,} leads/mo at {p['capture_rate_pct']}% gap capture")
    print("  Done.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
