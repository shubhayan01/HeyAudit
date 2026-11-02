"""Everything that happens after the audit finishes.

Two entry points, because the web flow is now two steps:

  * ``analyse``       — extract the hard metrics, run the AI analysis, audit the
                        competitors, and work out which package the budget
                        suggests. Produces no deck. The web server calls this,
                        shows the operator the findings, and lets them *choose*
                        the package.
  * ``build_deck``    — given a chosen tier, write the growth solution (Groq) and
                        render the single combined deck.

``generate_deliverables`` chains both for the CLI one-shot path, so ``audit.py``
keeps working unchanged: analyse, pick the tier the budget implies (or Growth),
then build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import competitors as comp_mod
from . import packages as pk
from . import projections
from .ai import generate_ai_report
from .checks import seoptimer
from .core import AuditContext, CheckResult
from .discovery import Discovery
from .pitch import generate_pitch
from .report import render_deck_html, render_pdf


def _tier_options(suggested: Optional[str]) -> list[dict]:
    """Every package tier with its price, for the operator's post-audit picker."""
    return [{
        "tier": t,
        "label": pk.TIER_LABELS[t],
        "price": pk.price_line(t),
        "keyword_range": pk.PACKAGES[t]["keyword_range"],
        "aeo_geo": pk.PACKAGES[t]["aeo_geo"],
        "recommended": t == suggested,
    } for t in pk.TIERS]


def analyse(
    ctx: AuditContext,
    results: list[CheckResult],
    *,
    discovery: Optional[Discovery] = None,
    groq_key: Optional[str] = None,
    groq_model: Optional[str] = None,
    seoptimer_key: Optional[str] = None,
    progress: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """Analyse the audited site: metrics, AI headline, competitors, suggested tier.

    Returns a dict the caller keeps and later hands to :func:`build_deck`. Does
    NOT generate the deck — the package is chosen after this returns.
    """
    discovery = discovery or Discovery()

    def note(msg: str, cat: str = "") -> None:
        if progress:
            try:
                progress(msg, cat or ctx.domain)
            except Exception:
                pass

    client_metrics = seoptimer.metrics(getattr(ctx, "seoptimer", None))
    client_metrics["domain"] = ctx.domain

    ai = None
    if groq_key:
        note("Generating AI analysis (Groq)")
        ai = generate_ai_report(ctx.domain, ctx.url, results,
                                api_key=groq_key, model=groq_model)

    competitor_metrics: list[dict] = []
    if discovery.competitors:
        key = seoptimer.resolve_key({"seoptimer_key": seoptimer_key} if seoptimer_key else None)
        competitor_metrics = comp_mod.analyse(discovery.competitors, key, progress=progress)

    # The budget's implied tier is the default the picker pre-selects; the
    # operator can still override it when building the deck.
    suggested_tier = pk.tier_for_budget(discovery.budget_figure, discovery.currency) or "growth"

    return {
        "metrics": client_metrics,
        "ai": ai,
        "competitors": competitor_metrics,
        "suggested_tier": suggested_tier,
        "tier_options": _tier_options(suggested_tier),
    }


def build_deck(
    ctx: AuditContext,
    results: list[CheckResult],
    out_dir: Path,
    *,
    tier: str,
    analysis: dict,
    discovery: Optional[Discovery] = None,
    groq_key: Optional[str] = None,
    groq_model: Optional[str] = None,
    make_pdf: bool = True,
    make_proposal: bool = True,
    progress: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """Write the solution narrative for the chosen ``tier`` and render the deck.

    The operator's chosen tier is authoritative: it drives the pitch, the ROI
    model, and every priced element of the deck. Degrades gracefully — no Groq
    key (or ``make_proposal`` off) renders an audit-only deck.
    """
    discovery = discovery or Discovery()
    if tier not in pk.TIERS:
        tier = analysis.get("suggested_tier") or "growth"

    def note(msg: str, cat: str = "") -> None:
        if progress:
            try:
                progress(msg, cat or ctx.domain)
            except Exception:
                pass

    client_metrics = analysis.get("metrics") or {}
    ai = analysis.get("ai")
    competitor_metrics = analysis.get("competitors") or []

    out: dict = {"deck_html": None, "deck_pdf": None, "ai": ai, "pitch": None,
                 "metrics": client_metrics, "competitors": competitor_metrics,
                 "projection": None, "tier": tier}

    pitch = None
    projection = None
    if make_proposal and groq_key:
        note(f"Writing growth solution for the {pk.TIER_LABELS[tier]} package (Groq)")
        pitch = generate_pitch(ctx.domain, ctx.url, results, api_key=groq_key,
                               model=groq_model, ai=ai, discovery=discovery,
                               client_metrics=client_metrics,
                               competitor_metrics=competitor_metrics,
                               forced_tier=tier)
        out["pitch"] = pitch
        if pitch:
            projection = projections.build(discovery, client_metrics,
                                           competitor_metrics, tier)
    out["projection"] = projection

    note("Building combined audit + solution deck")
    deck_html = render_deck_html(
        ctx, out_dir, results,
        pitch=pitch,
        ai=ai,
        discovery=discovery,
        client_metrics=client_metrics,
        competitor_metrics=competitor_metrics,
        projection=projection,
        forced_tier=tier,
    )
    out["deck_html"] = deck_html
    if make_pdf:
        pdf = out_dir / "deck.pdf"
        if render_pdf(deck_html, pdf):
            out["deck_pdf"] = pdf
    return out


def generate_deliverables(
    ctx: AuditContext,
    results: list[CheckResult],
    out_dir: Path,
    *,
    discovery: Optional[Discovery] = None,
    groq_key: Optional[str] = None,
    groq_model: Optional[str] = None,
    seoptimer_key: Optional[str] = None,
    tier: Optional[str] = None,
    make_pdf: bool = True,
    make_proposal: bool = True,
    progress: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """One-shot: analyse then build the deck (used by the CLI).

    ``tier`` forces the package; otherwise the budget's implied tier (or Growth)
    is used. Returns the ``build_deck`` result plus the analysis' competitors so
    existing callers keep the fields they read.
    """
    discovery = discovery or Discovery()
    analysis = analyse(ctx, results, discovery=discovery, groq_key=groq_key,
                       groq_model=groq_model, seoptimer_key=seoptimer_key,
                       progress=progress)
    chosen = tier or analysis["suggested_tier"]
    out = build_deck(ctx, results, out_dir, tier=chosen, analysis=analysis,
                     discovery=discovery, groq_key=groq_key, groq_model=groq_model,
                     make_pdf=make_pdf, make_proposal=make_proposal, progress=progress)
    return out
