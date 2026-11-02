"""Static definition of Justwords' Next Gen SEO Packages.

Pricing and deliverable data lives here (not in the LLM prompt as free text) so
the pitch deck renders it *deterministically* — the model recommends a tier and
explains why, but it never invents a price or a deliverable count.
"""

from __future__ import annotations

import re

METADATA = {
    "name": "Next Gen SEO Packages",
    "billing": "Monthly (Research & Analysis is a one-time activity)",
    "currency": {"primary": "INR", "secondary": "USD"},
}

# Tier order used everywhere (columns, comparison, recommendation).
TIERS = ["starter", "growth", "professional", "enterprise"]

TIER_LABELS = {
    "starter": "Starter",
    "growth": "Growth",
    "professional": "Professional",
    "enterprise": "Enterprise",
}

# Monthly price bounds, in whole currency units. These numbers are the single
# source of truth: the display strings and the ROI maths in projections.py both
# derive from them, so a price can never be shown one way and modelled another.
PACKAGES = {
    "starter": {
        "keyword_range": "10-25",
        "aeo_geo": False,
        "pricing": {"inr_min": 24_000, "inr_max": 50_000, "usd_min": 300, "usd_max": 600},
    },
    "growth": {
        "keyword_range": "26-50",
        "aeo_geo": True,
        "pricing": {"inr_min": 50_000, "inr_max": 95_000, "usd_min": 600, "usd_max": 1_150},
    },
    "professional": {
        "keyword_range": "51-100",
        "aeo_geo": True,
        "pricing": {"inr_min": 95_000, "inr_max": 180_000, "usd_min": 1_150, "usd_max": 2_200},
    },
    "enterprise": {
        "keyword_range": "100+",
        "aeo_geo": True,
        "pricing": {"custom": True},
    },
}

# category -> list of rows. Each row: name + per-tier value.
# A value may be True (included), False (not included), or a string (a count /
# frequency / range). ``frequency`` is an optional note on the row itself.
CATEGORIES: dict[str, list[dict]] = {
    "Research & Analysis": [
        {"name": "On-Page Analysis & Suggestions", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Backlink Analysis", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Duplicate Content Analysis", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Content Quality Analysis (E-E-A-T)", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Competitor Analysis", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Website Audit Report", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Click Depth Analysis", "frequency": "Quarterly", "starter": False, "growth": False, "professional": True, "enterprise": True},
    ],
    "Setup": [
        {"name": "GA4 + Search Console + GTM Setup", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Keyword Research", "frequency": "One-time + Bi-Annual", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Baseline Analytics Report", "starter": True, "growth": True, "professional": True, "enterprise": True},
    ],
    "On-Page Optimization": [
        {"name": "Meta Tag Optimization", "starter": "8-20", "growth": "20-40", "professional": "40-80", "enterprise": "80+"},
        {"name": "Heading Optimization", "starter": "8-20", "growth": "20-40", "professional": "40-80", "enterprise": "80+"},
        {"name": "Content Optimization", "starter": "8-20", "growth": "20-40", "professional": "40-80", "enterprise": "80+"},
        {"name": "Internal Linking", "starter": "8-20", "growth": "20-40", "professional": "40-80", "enterprise": "80+"},
        {"name": "URL Optimization", "starter": "8-20", "growth": "20-40", "professional": "40-80", "enterprise": "80+"},
        {"name": "Breadcrumb Optimization", "starter": True, "growth": True, "professional": True, "enterprise": True},
    ],
    "Technical SEO": [
        {"name": "Sitemap", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Robots.txt Optimization", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Redirect Management", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Crawl Error Fixes", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Broken Links", "starter": "20-50", "growth": "50-100", "professional": "100-200", "enterprise": "Unlimited"},
        {"name": "Canonical Issues", "starter": "8-20", "growth": "20-40", "professional": "40-80", "enterprise": "80+"},
    ],
    "Website Performance": [
        {"name": "Speed Optimization Suggestions", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Mobile Optimization", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Core Web Vitals Analysis", "starter": True, "growth": True, "professional": True, "enterprise": True},
    ],
    "Structured Data": [
        {"name": "Schema Markup", "starter": "8-15", "growth": "20-40", "professional": "45-75", "enterprise": "75+"},
        {"name": "Keyword Mapping & Clustering", "starter": True, "growth": True, "professional": True, "enterprise": True},
    ],
    "Visual & Media Optimization": [
        {"name": "Image Optimization", "starter": "8-15", "growth": "20-40", "professional": "45-75", "enterprise": "75+"},
    ],
    "Content Research & Development": [
        {"name": "AI, AEO & GEO Content Strategy", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Existing Content Audit", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "AI Cluster Development", "starter": False, "growth": False, "professional": True, "enterprise": True},
        {"name": "Website Blog Topics", "starter": "2-5", "growth": "5-10", "professional": "10-15", "enterprise": "15+"},
        {"name": "Blog Publishing", "starter": "2-5", "growth": "5-10", "professional": "10-15", "enterprise": "15+"},
        {"name": "Voice Search Optimization", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "GEO Implementation", "starter": True, "growth": True, "professional": True, "enterprise": True},
    ],
    "Off Page SEO": [
        {"name": "Guest Posting", "starter": "3-7", "growth": "7-15", "professional": "15-30", "enterprise": "Custom"},
        {"name": "Q&A Participation", "starter": "2-4", "growth": "4-6", "professional": "6-10", "enterprise": "Custom"},
        {"name": "Business Listings", "starter": "5-7", "growth": "8-10", "professional": "10-15", "enterprise": "TBD"},
        {"name": "Link Quality Assessment", "starter": True, "growth": True, "professional": True, "enterprise": True},
    ],
    "Local SEO": [
        {"name": "Local Citations", "starter": "5-7", "growth": "8-10", "professional": "10-15", "enterprise": "Custom"},
        {"name": "Google Business Profile Optimization", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Review Management", "starter": False, "growth": True, "professional": True, "enterprise": True},
        {"name": "Multi-location Support", "starter": False, "growth": "4-8", "professional": "8-12", "enterprise": True},
    ],
    "Reporting": [
        {"name": "Live SEO Dashboard", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Google Analytics Report", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Keyword Ranking Report", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "AI Visibility Report", "starter": True, "growth": True, "professional": True, "enterprise": True},
        {"name": "Strategy Consultation", "starter": "Quarterly", "growth": "Monthly", "professional": "Bi-Monthly", "enterprise": "Monthly"},
    ],
    "Add-ons": [
        {"name": "Gated Content", "starter": "TBD", "growth": "TBD", "professional": "TBD", "enterprise": "TBD"},
        {"name": "Website Blog Creation", "starter": "3", "growth": "6", "professional": "10", "enterprise": "14"},
        {"name": "Paid Guest Posts", "starter": "0", "growth": "1", "professional": "2", "enterprise": "3"},
        {"name": "Google Business Posts", "starter": "TBD", "growth": "TBD", "professional": "TBD", "enterprise": "TBD"},
    ],
}


def _k(n: int) -> str:
    """24000 -> '24K'; 180000 -> '180K'; 600 -> '600'."""
    return f"{n // 1000}K" if n >= 1000 and n % 1000 == 0 else f"{n:,}"


def link_velocity(tier: str) -> tuple[int, int] | None:
    """(min, max) new referring domains a tier actually contracts per month.

    Guest posting is the deliverable that earns new referring domains, so it is
    the honest ceiling on how fast an authority gap can close. projections.py
    uses this to check a parity claim against what we've actually sold — a
    proposal that promises a link velocity the package doesn't fund is one
    division away from falling apart in front of the client.
    """
    row = next((r for r in CATEGORIES["Off Page SEO"] if r["name"] == "Guest Posting"), None)
    v = (row or {}).get(tier)
    if not isinstance(v, str):
        return None
    nums = [int(n) for n in re.findall(r"\d+", v)]
    if not nums:
        return None
    return (nums[0], nums[-1])


def price_bounds(tier: str, currency: str = "INR") -> tuple[int, int] | None:
    """(min, max) monthly price in whole units, or None for custom-quote tiers."""
    p = PACKAGES[tier]["pricing"]
    if p.get("custom"):
        return None
    pre = "usd" if currency.upper() == "USD" else "inr"
    return (p[f"{pre}_min"], p[f"{pre}_max"])


def tier_for_budget(budget: float | None, currency: str = "INR") -> str | None:
    """The best tier whose monthly price the client's budget can actually cover.

    Given a single budget figure (their stated max, or the top of a range), pick
    the most capable tier whose *minimum* monthly price still fits — so a client
    on ₹60K lands on Growth (₹50K-95K), not Professional. Below the cheapest tier
    we still return Starter rather than nothing: the honest floor, not a refusal.
    Enterprise is custom-priced, so it's only reached when the budget clears the
    top fixed tier's minimum. Returns ``None`` when no budget was given.
    """
    if not budget or budget <= 0:
        return None
    fixed = [t for t in TIERS if not PACKAGES[t]["pricing"].get("custom")]
    # The tier whose price band the budget lands in — i.e. the first tier whose
    # ceiling the budget doesn't exceed. On a shared boundary the lower tier wins,
    # so "50K-95K" lands on Growth (₹50K-95K), not the floor of Professional.
    for t in fixed:
        _lo, hi = price_bounds(t, currency)
        if budget <= hi:
            return t
    # Budget clears every fixed tier's ceiling → the custom/top tier.
    return TIERS[-1] if PACKAGES[TIERS[-1]]["pricing"].get("custom") else fixed[-1]


def price_line(tier: str) -> str:
    """Human-readable price for a tier, e.g. '₹50K-95K/month  ·  $600-1150/month'."""
    p = PACKAGES[tier]["pricing"]
    if p.get("custom"):
        return "Custom quote"
    return (f"₹{_k(p['inr_min'])}-{_k(p['inr_max'])}/month  ·  "
            f"${p['usd_min']}-{p['usd_max']}/month")


# The proposal presents three options, most aggressive first, with the
# recommended tier flagged wherever it lands on the ladder.
OPTION_LABELS = ["A", "B", "C"]
OPTION_POSITIONING = [
    ("Aggressive Growth", "Built to overtake your competitors inside 12 months."),
    ("Steady Growth", "Real progress on a lighter budget, over a longer timeline."),
    ("Foundation", "The minimum that still moves — slower, and less dramatic."),
]


def options_for(tier: str) -> list[dict]:
    """The A/B/C option ladder around a recommended tier.

    Always three tiers, ordered most → least aggressive, so Option A is always
    the biggest commitment. The recommendation is flagged on whichever option it
    actually falls on rather than being forced to the top — recommending a tier
    the audit doesn't justify is how proposals lose credibility.
    """
    if tier not in TIERS:
        tier = "growth"
    i = TIERS.index(tier)
    start = max(0, min(i - 2, len(TIERS) - 3))
    window = list(reversed(TIERS[start:start + 3]))
    out = []
    for n, t in enumerate(window):
        name, blurb = OPTION_POSITIONING[n]
        out.append({
            "label": OPTION_LABELS[n],
            "tier": t,
            "tier_label": TIER_LABELS[t],
            "positioning": name,
            "blurb": blurb,
            "price": price_line(t),
            "bounds": price_bounds(t),
            "keyword_range": PACKAGES[t]["keyword_range"],
            "aeo_geo": PACKAGES[t]["aeo_geo"],
            "recommended": t == tier,
        })
    return out


def _select_categories(include: list[str] | None) -> list[str]:
    """Normalise a requested category set to the real CATEGORIES keys.

    Matching is case/spacing-insensitive so the web form and CLI can pass loose
    labels. An empty or unrecognised selection means "all categories" — the deck
    should never silently render an empty deliverables section because a filter
    didn't match anything.
    """
    if not include:
        return list(CATEGORIES.keys())
    norm = {re.sub(r"[^a-z0-9]", "", c.lower()): c for c in CATEGORIES}
    picked = [norm[k] for c in include
              if (k := re.sub(r"[^a-z0-9]", "", str(c).lower())) in norm]
    # De-dupe while preserving CATEGORIES order.
    picked = [c for c in CATEGORIES if c in picked]
    return picked or list(CATEGORIES.keys())


def categories_matrix(include: list[str] | None = None) -> list[tuple[str, list[dict]]]:
    """The (category, rows) pairs for the full comparison matrix, category-filtered."""
    keep = set(_select_categories(include))
    return [(cat, rows) for cat, rows in CATEGORIES.items() if cat in keep]


def deliverables_for(tier: str, include_categories: list[str] | None = None) -> list[dict]:
    """Every deliverable this tier actually includes, grouped by category.

    Feeds the proposal's "What We'll Deliver Each Month" section, so the counts
    shown are the contracted ones rather than anything the model dreamt up.
    ``include_categories`` (optional) restricts the section to the service areas
    the operator chose to feature; an empty/unmatched set keeps them all.
    """
    keep = set(_select_categories(include_categories))
    out = []
    for cat, rows in CATEGORIES.items():
        if cat not in keep:
            continue
        items = []
        for row in rows:
            v = row.get(tier)
            if v is False or v in ("", None, "0"):
                continue
            items.append({
                "name": row["name"],
                "value": v if v is not True else "",
                "frequency": row.get("frequency", ""),
            })
        if items:
            out.append({"category": cat, "items": items})
    return out


def summary_for_prompt() -> str:
    """A compact text digest of the packages for the LLM (so it can reason about
    which tier fits and reference real deliverable counts, without us pasting the
    whole matrix into the prompt)."""
    lines = [f"{METADATA['name']} — billing: {METADATA['billing']}", ""]
    for t in TIERS:
        pkg = PACKAGES[t]
        aeo = "AEO/GEO included" if pkg["aeo_geo"] else "no AEO/GEO"
        lines.append(
            f"- {TIER_LABELS[t]}: {pkg['keyword_range']} keywords, {aeo}, "
            f"pricing {price_line(t)}"
        )
    # A few deliverable highlights that scale by tier, useful for the roadmap.
    lines.append("")
    lines.append("Deliverables that scale by tier (Starter / Growth / Professional / Enterprise):")
    highlight = [
        "Website Blog Topics", "Blog Publishing", "Guest Posting",
        "Schema Markup", "Meta Tag Optimization", "Content Optimization",
    ]
    for cat, rows in CATEGORIES.items():
        for row in rows:
            if row["name"] in highlight:
                vals = " / ".join(str(row[t]) for t in TIERS)
                lines.append(f"  - {row['name']}: {vals}")
    return "\n".join(lines)
