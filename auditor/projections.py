"""Deterministic opportunity and ROI maths for the proposal.

The JW template's Sections 3-4 ("The Competitive Gap", "What This Costs You")
are the numbers a prospect will scrutinise hardest, so none of them come from
the language model. Everything here is arithmetic over two sources only:

  * hard metrics from SEOptimer (the client's and each competitor's), and
  * what the client stated on the discovery call (``discovery.py``).

Anything we cannot derive is returned as ``None`` and simply doesn't render. The
assumptions behind every figure are returned alongside it in ``assumptions`` and
printed as footnotes under the tables — a proposal that hides its assumptions
falls apart the moment the client's own analyst opens it.
"""

from __future__ import annotations

from typing import Optional

from . import packages as pk
from .discovery import Discovery

# --- Stated modelling assumptions -----------------------------------------
# Deliberately conservative: it is far better to beat a modest projection than
# to miss a flattering one.

CAPTURE_RATE = 0.20        # share of the competitor traffic gap we model closing
DEFAULT_CONV_RATE = 2.0    # visit -> lead %, used only when the client has no figure

# Share of the month-12 run rate realised in each month. SEO compounds: nothing
# lands in months 1-2, rankings move from month 3, leads follow from month 6.
RAMP = [0.0, 0.0, 0.05, 0.10, 0.15, 0.25, 0.35, 0.45, 0.55, 0.70, 0.85, 1.00]


def _best(metrics_list: list[dict], key: str) -> Optional[float]:
    vals = [m.get(key) for m in metrics_list if isinstance(m.get(key), (int, float))]
    return max(vals) if vals else None


def _avg(metrics_list: list[dict], key: str) -> Optional[float]:
    vals = [m.get(key) for m in metrics_list if isinstance(m.get(key), (int, float))]
    return sum(vals) / len(vals) if vals else None


def gaps(client: dict, competitors: list[dict]) -> dict:
    """Client-vs-competitor gaps, from SEOptimer metrics on both sides.

    Returns ``None`` for any gap whose inputs are missing rather than guessing.
    """
    comp = [c for c in competitors if c.get("loaded")]
    out: dict = {"competitor_count": len(comp)}
    if not comp:
        return out

    for key, label in (("organic_traffic", "traffic"),
                       ("referring_domains", "referring_domains"),
                       ("domain_strength", "authority"),
                       ("keywords_total", "keywords"),
                       ("backlinks", "backlinks")):
        mine = client.get(key)
        best = _best(comp, key)
        avg = _avg(comp, key)
        out[label] = {
            "mine": mine,
            "best": best,
            "avg": round(avg) if avg is not None else None,
            "leader": next((c.get("domain") for c in comp if c.get(key) == best), None),
            "gap": (round(best - mine) if isinstance(mine, (int, float))
                    and isinstance(best, (int, float)) and best > mine else None),
        }

    # To match the leader's referring domains inside a year, how many new
    # referring domains per month does that imply?
    rd = out.get("referring_domains") or {}
    if rd.get("gap"):
        out["referring_domains_per_month"] = max(1, round(rd["gap"] / 12))

    # Page speed: the leader's load time vs ours (seconds).
    mine_ms, best_ms = client.get("load_ms"), None
    loads = [c["load_ms"] for c in comp if isinstance(c.get("load_ms"), (int, float))]
    if loads:
        best_ms = min(loads)
    out["speed"] = {
        "mine_s": round(mine_ms / 1000, 1) if isinstance(mine_ms, (int, float)) else None,
        "best_s": round(best_ms / 1000, 1) if best_ms is not None else None,
        "avg_s": round(sum(loads) / len(loads) / 1000, 1) if loads else None,
    }
    return out


def _conversion_rate(d: Discovery, client: dict) -> tuple[Optional[float], str]:
    """Visit -> lead rate, and where it came from (we always disclose which)."""
    if d.conversion_rate:
        return d.conversion_rate, "stated"
    traffic = client.get("organic_traffic")
    if d.current_leads and isinstance(traffic, (int, float)) and traffic > 0:
        rate = min(100.0, d.current_leads / traffic * 100.0)
        return round(rate, 2), "derived"
    return DEFAULT_CONV_RATE, "assumed"


def build(discovery: Discovery, client: dict, competitors: list[dict],
          tier: str) -> Optional[dict]:
    """The opportunity model, or ``None`` when there isn't enough to model.

    Needs, at minimum, a traffic gap between the client and a competitor. Lead
    and revenue layers switch on only as the client's own numbers allow.
    """
    g = gaps(client, competitors)
    traffic = g.get("traffic") or {}
    gap = traffic.get("gap")
    if not gap:
        return None  # no measurable gap -> no honest opportunity claim

    assumptions: list[str] = []
    out: dict = {"gaps": g, "assumptions": assumptions}

    # --- Traffic ---------------------------------------------------------
    visits = round(gap * CAPTURE_RATE)
    out["traffic_gap"] = gap
    out["capture_rate_pct"] = round(CAPTURE_RATE * 100)
    out["added_visits"] = visits
    out["leader"] = traffic.get("leader")
    assumptions.append(
        f"Opportunity is modelled as capturing {out['capture_rate_pct']}% of the "
        f"{gap:,} monthly-visit gap between {discovery.client_name or 'your site'} and "
        f"{traffic.get('leader') or 'the strongest competitor'} — not the whole gap."
    )

    # --- Leads -----------------------------------------------------------
    conv, conv_src = _conversion_rate(discovery, client)
    out["conversion_rate"] = conv
    out["conversion_source"] = conv_src
    out["added_leads"] = round(visits * conv / 100.0)
    assumptions.append({
        "stated": f"Visit-to-lead conversion of {conv:g}% is the rate you gave us.",
        "derived": (f"Visit-to-lead conversion of {conv:g}% is derived from your own "
                    f"numbers ({discovery.current_leads} leads/month from "
                    f"{client.get('organic_traffic'):,} organic visits)."),
        "assumed": (f"Visit-to-lead conversion of {conv:g}% is an industry-standard "
                    f"placeholder — give us your real figure and we'll re-run this."),
    }[conv_src])

    # --- Revenue ---------------------------------------------------------
    if discovery.has_economics:
        deals = visits * conv / 100.0 * discovery.close_rate / 100.0
        monthly_rev = deals * discovery.avg_deal_value
        out["added_deals"] = round(deals, 1)
        out["added_revenue_monthly"] = round(monthly_rev)
        out["added_revenue_annualised"] = round(monthly_rev * 12)
        # Year 1 banks only what the ramp actually delivers, not 12x the peak.
        out["year1_revenue"] = round(monthly_rev * sum(RAMP))
        assumptions.append(
            f"Revenue applies your stated {discovery.close_rate:g}% close rate and "
            f"{discovery.money(discovery.avg_deal_value)} average deal value."
        )
        assumptions.append(
            "Year-1 revenue is the sum of the monthly ramp below, not 12 months at "
            "the month-12 run rate — SEO compounds, so early months contribute little."
        )

    # --- Monthly ramp ----------------------------------------------------
    ramp = []
    for i, pct in enumerate(RAMP, 1):
        row = {"month": i, "pct": round(pct * 100),
               "visits": round(visits * pct), "leads": round(visits * conv / 100.0 * pct)}
        if discovery.has_economics:
            row["revenue"] = round(visits * pct * conv / 100.0
                                   * discovery.close_rate / 100.0
                                   * discovery.avg_deal_value)
        ramp.append(row)
    out["ramp"] = ramp
    out["milestones"] = {m: ramp[m - 1] for m in (3, 6, 9, 12)}

    # --- Authority: is parity actually purchasable? -----------------------
    # The raw "gap / 12" figure routinely demands a link velocity no package
    # funds. Rather than print a promise the deliverables contradict, work out
    # what the recommended tier really buys and how long parity really takes.
    rd = g.get("referring_domains") or {}
    velocity = pk.link_velocity(tier)
    if rd.get("gap") and velocity:
        needed = g.get("referring_domains_per_month")
        delivered_lo, delivered_hi = velocity
        out["authority"] = {
            "gap": rd["gap"],
            "needed_for_12mo_parity": needed,
            "delivered_per_month": delivered_hi,
            "delivered_range": f"{delivered_lo}-{delivered_hi}",
            "parity_months": max(1, round(rd["gap"] / delivered_hi)) if delivered_hi else None,
            "parity_realistic": bool(needed and delivered_hi >= needed),
        }
        if not out["authority"]["parity_realistic"]:
            assumptions.append(
                "We are not claiming to match the leader's total referring domains inside "
                "12 months — at any honest link velocity that would take far longer. The "
                "plan closes the gap on the keywords that make you money, which needs "
                "relevant authority, not a matching link count."
            )

    # --- Investment & ROI ------------------------------------------------
    bounds = pk.price_bounds(tier, discovery.currency)
    if bounds:
        lo, hi = bounds
        out["investment_year1_min"] = lo * 12
        out["investment_year1_max"] = hi * 12
        if discovery.has_economics and out.get("year1_revenue"):
            # ROI against the top of the range — the honest, least flattering read.
            spend = hi * 12
            out["roi_pct"] = round((out["year1_revenue"] - spend) / spend * 100)
            out["breakeven_month"] = _breakeven(ramp, hi)
        leads_y1 = sum(r["leads"] for r in ramp)
        if leads_y1:
            out["cost_per_lead"] = round(hi * 12 / leads_y1)
            out["cost_per_lead_m12"] = (round(hi / ramp[-1]["leads"])
                                        if ramp[-1]["leads"] else None)
        assumptions.append(
            "ROI and cost-per-lead are calculated against the TOP of the recommended "
            "tier's price range, so the real figures should be better, not worse."
        )
    return out


def _breakeven(ramp: list[dict], monthly_spend: float) -> Optional[int]:
    """First month where cumulative revenue overtakes cumulative spend."""
    rev = 0.0
    for r in ramp:
        rev += r.get("revenue") or 0
        if rev >= monthly_spend * r["month"]:
            return r["month"]
    return None
