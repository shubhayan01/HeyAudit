"""Groq-powered proposal generator (the JW SEO Proposal template).

Turns the audit, the discovery-call notes and the measured competitor data into
the narrative of a value-first client proposal: what we found, what it's costing
them, the three-pillar strategy, the 12-month roadmap and the investment options.

**Division of labour.** The model writes prose and strategy only. Every number a
prospect could check — prices, deliverable counts, competitor metrics, traffic
gaps, ROI — is rendered deterministically from ``packages.py``,
``competitors.py`` and ``projections.py``. The model is explicitly told not to
restate those figures, because a proposal whose numbers disagree with its own
tables is worse than no proposal.

Transport mirrors ``ai.py``: Groq Chat Completions over stdlib urllib, JSON
response, degrades to ``None`` on any failure so the pipeline still finishes.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import competitors as comp_mod
from . import packages
from .core import CheckResult, strip_emoji
from .discovery import Discovery


def _retry_after_seconds(exc: "urllib.error.HTTPError", default: float = 6.0) -> float:
    """How long to wait before retrying a 429, from the Retry-After header or the
    'try again in 5.465s' hint in Groq's JSON body. Capped so we never hang."""
    try:
        ra = exc.headers.get("Retry-After") if exc.headers else None
        if ra:
            return min(60.0, float(ra) + 0.5)
    except (ValueError, TypeError):
        pass
    try:
        m = re.search(r"try again in ([\d.]+)s", exc.read().decode("utf-8", "replace"))
        if m:
            return min(60.0, float(m.group(1)) + 0.5)
    except Exception:
        pass
    return default


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

_SYSTEM = (
    "You are a Senior SEO / AEO / GEO and Content Director with 15+ years of "
    "experience, known for turning technical audits into high-growth business "
    "proposals for C-suite executives. You write for busy decision-makers: "
    "growth- and ROI-focused, concrete, and free of jargon. You base every "
    "claim on the supplied audit, discovery notes and measured competitor data, "
    "and you never invent findings, prices, metrics or deliverable counts. "
    "GEO = Generative Engine Optimization (ranking in ChatGPT / Perplexity / "
    "Google AI Overviews); AEO = Answer Engine Optimization. You always frame "
    "content as SEO-optimised, human-written and held to Google's E-E-A-T "
    "standard. You are honest about timelines: SEO shows movement at 3-6 months "
    "and revenue at 9-12 months, and you never promise results in 90 days."
)

_INSTRUCTIONS = """\
Write the narrative for a value-first SEO growth proposal following the sections
below. Infer the client's INDUSTRY and what they sell from the audit data
(domain, title, content, keywords).

CRITICAL RULE ON NUMBERS: every metric, price, competitor figure, traffic count
and ROI number is rendered from measured data by our own code, NOT by you. Write
around them qualitatively ("your strongest competitor holds a clear authority
lead", never "they have 859 referring domains"). Do NOT state specific prices,
percentages of growth, competitor metrics, or rupee/dollar amounts anywhere.
Do NOT invent any numbers — including keyword search volumes, difficulty scores
or CPCs. The deck's keyword data comes from the site's real ranking data, not
from you; you only supply the topic-cluster STRATEGY (keyword names, no metrics).

Respond with ONLY a JSON object (no markdown fences) in exactly this shape:

{
  "client_industry": "<the industry / what the brand sells, inferred from the audit>",
  "client_descriptor": "<how to refer to the client in prose, e.g. 'a Delhi-based content marketing agency'>",
  "executive_summary": "<4-6 sentences: what our analysis discovered and the opportunity it reveals. Growth/ROI-framed, no metrics.>",
  "discovery_synthesis": "<2-4 sentences synthesising what the discovery notes tell us they need. If no discovery notes were supplied, return an empty string.>",
  "goal_restated": "<their goal in one line, echoing their own words where given; empty string if no discovery notes>",
  "what_it_will_take": "<one line: the shape of the campaign needed to hit that goal>",
  "success_vision": {
    "m3_6": "<what improves in months 3-6>",
    "m9_12": "<what improves in months 9-12>",
    "m12_plus": "<what compounds after month 12>"
  },
  "homepage_recommendation": "<2-3 sentences on what the homepage should target and why, grounded in the audit's on-page findings>",
  "gap_narrative": {
    "content": "<2 sentences on the content/keyword coverage gap vs competitors — qualitative>",
    "authority": "<2 sentences on the backlink/authority gap — qualitative>",
    "technical": "<2 sentences on the technical gap — qualitative, cite audit issues by name>"
  },
  "opportunity_narrative": "<3-4 sentences: why they are leaving money on the table today>",
  "competitor_insights": [
    {"domain": "<competitor domain EXACTLY as supplied to you>",
     "doing_right": ["<specific thing they do well>", "<another>"],
     "takeaway": "<what we do about it>"}
    // one per competitor supplied; [] if none were supplied
  ],
  "pillars": {
    "content": {"approach": "<2-3 sentences>",
                "focus": [{"months": "1-3", "theme": "<theme>"}, {"months": "4-6", "theme": "<theme>"},
                          {"months": "7-9", "theme": "<theme>"}, {"months": "10-12", "theme": "<theme>"}],
                "why": "<why this closes the gap for THIS client>"},
    "digital_pr": {"approach": "<2-3 sentences>",
                   "targets": ["<type of publication/site we'll target>", "...3-5 items"],
                   "why": "<why authority is the constraint for THIS client>"},
    "technical": {"approach": "<2-3 sentences>",
                  "month0_fixes": ["<fix drawn from the audit's failures>", "...4-6 items"],
                  "why": "<why these fixes gate everything else>"}
  },
  "roadmap": [
    {"phase": "Month 0", "title": "Foundation & Setup", "goal": "<one line>",
     "content": ["<activity>", "..."], "technical": ["<activity>", "..."],
     "digital_pr": ["<activity>", "..."], "expected": ["<deliverable/outcome>", "..."]},
    {"phase": "Months 1-3", "title": "<phase name>", ...same keys...},
    {"phase": "Months 4-6", "title": "<phase name>", ...same keys...},
    {"phase": "Months 7-9", "title": "<phase name>", ...same keys...},
    {"phase": "Months 10-12", "title": "<phase name>", ...same keys...}
    // exactly 5 phases, each grounded in this audit's gaps. No percentages or metrics.
  ],
  "month13_plus": "<2-3 sentences on sustained growth and falling cost-per-lead after month 12>",
  "keyword_clusters": [
    {"seed": "<seed keyword>", "intent": "Informational" | "Transactional" | "Commercial" | "Navigational",
     "pillar_url": "</proposed-url-or-pillar-page/>", "cluster_keywords": ["<kw>", "<kw>", "<kw>"]}
    // 5-8 NEW topic clusters to build. If REAL RANKING KEYWORDS are supplied
    // below, expand OUT from the themes the site already ranks for into adjacent,
    // higher-intent territory — don't just repeat the terms it already ranks for.
    // Keyword names only, NO volumes/difficulty/CPC.
  ],
  "competitive_moat": {
    "title": "<name of the ONE unique strategic play>",
    "play": "<what we do, 2-3 sentences — tie it to a specific audit weakness>",
    "advantage": "<why this creates a durable, hard-to-copy advantage>"
  },
  "content_plan": {
    "buckets": [
      {"name": "<content bucket>", "purpose": "<what it does for the funnel>", "examples": ["<topic>", "<topic>"]}
      // 3-5 buckets
    ],
    "standards": "<1-2 sentences confirming all content is SEO-optimised, human-written, E-E-A-T compliant, and built with GEO + AEO best practices>"
  },
  "recommended_tier": "starter" | "growth" | "professional" | "enterprise",
  "recommendation_rationale": "<2-3 sentences: why this tier fits THIS audit — cite audit weaknesses and the competitive gap. No prices.>",
  "why_not_lower": "<2-3 sentences, honest and direct: what happens if they under-invest relative to the gap. No prices.>",
  "justwords_advantage": ["<how Justwords helps the brand — benefit, not feature>", "...4-6 items"]
}

Rules:
- Everything must be specific to THIS client's audit — no generic filler.
- Size the keyword strategy to the recommended tier's keyword range.
- Plain English. Growth and ROI, not technical jargon.
- Do NOT use emojis anywhere. This is a formal business document.
- If no discovery notes were supplied, return "" for discovery_synthesis and
  goal_restated rather than inventing what the client said.
"""


def _audit_digest(domain: str, url: str, results: list[CheckResult],
                  ai: dict | None) -> str:
    """Compact the audit into prompt text: headline health + the issues that
    justify the proposal (WARN/FAIL/INFO first), plus AI summary if present.

    Kept deliberately tight. Groq's free tier budgets prompt + max_tokens against
    one 12k tokens-per-minute limit, so every token spent restating a passing
    check is a token the proposal can't be written with. Detail lines are
    therefore attached only to the failures the strategy actually has to fix.
    """
    lines = [f"CLIENT SITE: {url}  (domain: {domain})", ""]
    if ai:
        if ai.get("grade"):
            lines.append(f"Overall audit grade: {ai['grade']}  (score {ai.get('score', '—')}/100)")
        if ai.get("headline"):
            lines.append(f"Headline: {ai['headline']}")
        if ai.get("executive_summary"):
            lines.append(f"Audit summary: {ai['executive_summary'][:600]}")
        lines.append("")

    # Weaknesses first — these are what the strategy must fix.
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3, "error": 4}
    ranked = sorted(results, key=lambda r: order.get(r.status.value, 9))
    lines.append("AUDIT FINDINGS (worst first):")
    for r in ranked:
        lines.append(f"- [{r.status.label}] {r.category} · {r.title}: {(r.summary or '')[:180]}")
        if r.status.value in ("fail", "warn"):
            for k, v in (r.details or [])[:1]:
                lines.append(f"    · {k}: {str(v)[:140]}")
    return "\n".join(lines)


def _keyword_digest(client_metrics: dict | None) -> str:
    """The site's REAL ranking keywords, for grounding the cluster strategy.

    Lists the terms SEOptimer measured the site already ranking for so the model
    builds *new* clusters that expand outward from proven territory instead of
    guessing at keywords from scratch. Returns "" when no real data is available,
    in which case the model falls back to inferring themes from the audit.
    """
    m = client_metrics or {}
    kws = m.get("top_keywords") or []
    if not kws:
        return ""
    lines = ["REAL RANKING KEYWORDS (measured — the site already ranks for these; "
             "build NEW clusters that expand around these themes into higher-intent terms):"]
    for k in kws[:12]:
        term = (k.get("keyword") or "").strip()
        if not term:
            continue
        pos = k.get("position")
        pos_txt = f" (currently position {pos})" if pos not in (None, "") else ""
        lines.append(f"  - {term}{pos_txt}")
    if m.get("keywords_total"):
        lines.append(f"The site ranks for roughly {m['keywords_total']} keywords in total.")
    return "\n".join(lines)


# Groq's free tier budgets prompt tokens AND max_tokens against a single 12k
# tokens-per-minute limit, so an over-generous max_tokens is what triggers a 413
# — not the size of the reply we actually get back. The proposal JSON lands
# around 4.5k tokens; 6k leaves room to finish without crowding out the ~3k
# prompt. MIN_TOKENS is the floor we'll shrink to before giving up.
MAX_TOKENS = 6000
MIN_TOKENS = 3000


def _call_groq(api_key: str, model: str, system: str, user: str,
               timeout: int = 180, max_retries: int = 4,
               max_tokens: int = MAX_TOKENS) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Groq sits behind Cloudflare, which 403s the default Python-urllib
        # User-Agent (error 1010). A normal browser UA is required.
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    for attempt in range(max_retries + 1):
        body = json.dumps({
            "model": model,
            "temperature": 0.4,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(GROQ_URL, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            # 429 = rate limit (free-tier tokens-per-minute). Wait the amount Groq
            # asks for and retry — the proposal fits under the cap on its own.
            if exc.code == 429 and attempt < max_retries:
                delay = _retry_after_seconds(exc)
                print(f"  … Groq rate-limited; retrying in {delay:.1f}s "
                      f"(attempt {attempt + 1}/{max_retries}).")
                time.sleep(delay)
                continue
            # 413 = prompt + max_tokens exceeds the per-minute budget. Retrying the
            # same request is pointless, but a smaller reply ceiling can still fit —
            # a slightly tighter proposal beats no proposal.
            if exc.code == 413 and max_tokens > MIN_TOKENS and attempt < max_retries:
                max_tokens = max(MIN_TOKENS, int(max_tokens * 0.7))
                print(f"  … Groq request too large; retrying with a "
                      f"{max_tokens}-token reply ceiling.")
                continue
            raise
    raise RuntimeError("Groq retries exhausted")  # pragma: no cover


def resolve_key(explicit: str | None = None) -> str | None:
    """Groq key from arg -> GROQ_API_KEY env -> .groq_key file in project root."""
    if explicit:
        return explicit.strip()
    env = os.environ.get("GROQ_API_KEY")
    if env:
        return env.strip()
    try:
        f = Path(__file__).resolve().parent.parent / ".groq_key"
        if f.is_file():
            txt = f.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    except Exception:
        pass
    return None


def generate_pitch(
    domain: str,
    url: str,
    results: list[CheckResult],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    ai: dict | None = None,
    discovery: Discovery | None = None,
    client_metrics: dict | None = None,
    competitor_metrics: list[dict] | None = None,
    forced_tier: str | None = None,
) -> Optional[dict]:
    """Return the proposal narrative dict, or ``None`` if unavailable/failed.

    ``forced_tier`` pins the recommendation (e.g. the tier the client's budget
    can afford). When set, the model is told to build the whole strategy around
    that tier and the returned ``recommended_tier`` is locked to it — the budget
    decides the package, the model only argues for it.
    """
    api_key = resolve_key(api_key)
    if not api_key:
        return None
    model = model or os.environ.get("GROQ_MODEL") or DEFAULT_MODEL
    discovery = discovery or Discovery()
    if forced_tier not in packages.TIERS:
        forced_tier = None

    budget_directive = ""
    if forced_tier:
        budget_directive = (
            f"\n\n---\nBUDGET CONSTRAINT (HARD): the client's budget fits the "
            f"'{forced_tier}' tier. You MUST set recommended_tier to "
            f"'{forced_tier}', size the keyword strategy to that tier's range, and "
            f"frame recommendation_rationale as why this tier is the right fit for "
            f"their budget and audit. Do not recommend a different tier.\n"
        )

    user = (
        _INSTRUCTIONS
        + "\n\n---\nPACKAGE MENU (recommend ONE tier from these):\n\n"
        + packages.summary_for_prompt()
        + budget_directive
        + "\n\n---\nDISCOVERY CALL NOTES:\n\n"
        + discovery.digest_for_prompt()
        + "\n\n---\n"
        + comp_mod.digest_for_prompt(client_metrics or {}, competitor_metrics or [])
        + (("\n\n---\n" + _keyword_digest(client_metrics)) if _keyword_digest(client_metrics) else "")
        + "\n\n---\nSEO AUDIT:\n\n"
        + _audit_digest(domain, url, results, ai)
    )
    try:
        raw = _call_groq(api_key, model, _SYSTEM, user)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        print(f"  ! Groq proposal request failed (HTTP {exc.code}). {detail[:300]}")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  ! Groq proposal request failed ({exc.__class__.__name__}: {exc}).")
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            try:
                parsed = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                print("  ! Groq returned unparseable proposal JSON; skipping deck.")
                return None
        else:
            return None

    parsed["model"] = model
    if forced_tier:
        parsed["recommended_tier"] = forced_tier  # budget decides; model can't override
    return _sanitise(parsed)


# --------------------------------------------------------------------------
# Sanitisation — coerce model output into safe, predictable shapes.
# --------------------------------------------------------------------------

def _s(v) -> str:
    return strip_emoji(str(v).strip()) if v is not None else ""


def _list_of_str(v, limit: int) -> list[str]:
    return [_s(x) for x in (v or []) if _s(x)][:limit]


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _sanitise(d: dict) -> dict:
    out: dict = {}
    out["model"] = _s(d.get("model"))
    out["client_industry"] = _s(d.get("client_industry"))
    out["client_descriptor"] = _s(d.get("client_descriptor"))
    out["executive_summary"] = _s(d.get("executive_summary"))
    out["discovery_synthesis"] = _s(d.get("discovery_synthesis"))
    out["goal_restated"] = _s(d.get("goal_restated"))
    out["what_it_will_take"] = _s(d.get("what_it_will_take"))
    out["homepage_recommendation"] = _s(d.get("homepage_recommendation"))
    out["opportunity_narrative"] = _s(d.get("opportunity_narrative"))
    out["month13_plus"] = _s(d.get("month13_plus"))
    out["why_not_lower"] = _s(d.get("why_not_lower"))

    sv = _dict(d.get("success_vision"))
    out["success_vision"] = {k: _s(sv.get(k)) for k in ("m3_6", "m9_12", "m12_plus")}

    gn = _dict(d.get("gap_narrative"))
    out["gap_narrative"] = {k: _s(gn.get(k)) for k in ("content", "authority", "technical")}

    tier = _s(d.get("recommended_tier")).lower()
    if tier not in packages.TIERS:
        tier = "growth"  # sensible default
    out["recommended_tier"] = tier
    out["recommendation_rationale"] = _s(d.get("recommendation_rationale"))

    insights = []
    for c in (d.get("competitor_insights") or [])[:5]:
        if not isinstance(c, dict) or not _s(c.get("domain")):
            continue
        insights.append({
            "domain": _s(c.get("domain")).lower().replace("https://", "").strip("/"),
            "doing_right": _list_of_str(c.get("doing_right"), 4),
            "takeaway": _s(c.get("takeaway")),
        })
    out["competitor_insights"] = insights

    pillars = _dict(d.get("pillars"))
    content = _dict(pillars.get("content"))
    focus = []
    for f in (content.get("focus") or [])[:4]:
        if isinstance(f, dict) and _s(f.get("theme")):
            focus.append({"months": _s(f.get("months")), "theme": _s(f.get("theme"))})
    out["pillars"] = {
        "content": {"approach": _s(content.get("approach")), "focus": focus,
                    "why": _s(content.get("why"))},
        "digital_pr": {
            "approach": _s(_dict(pillars.get("digital_pr")).get("approach")),
            "targets": _list_of_str(_dict(pillars.get("digital_pr")).get("targets"), 6),
            "why": _s(_dict(pillars.get("digital_pr")).get("why")),
        },
        "technical": {
            "approach": _s(_dict(pillars.get("technical")).get("approach")),
            "month0_fixes": _list_of_str(_dict(pillars.get("technical")).get("month0_fixes"), 8),
            "why": _s(_dict(pillars.get("technical")).get("why")),
        },
    }

    roadmap = []
    for p in (d.get("roadmap") or [])[:5]:
        if not isinstance(p, dict):
            continue
        roadmap.append({
            "phase": _s(p.get("phase")),
            "title": _s(p.get("title")),
            "goal": _s(p.get("goal")),
            "content": _list_of_str(p.get("content"), 5),
            "technical": _list_of_str(p.get("technical"), 5),
            "digital_pr": _list_of_str(p.get("digital_pr"), 5),
            "expected": _list_of_str(p.get("expected"), 5),
        })
    out["roadmap"] = roadmap

    clusters = []
    for c in (d.get("keyword_clusters") or [])[:10]:
        if not isinstance(c, dict):
            continue
        clusters.append({
            "seed": _s(c.get("seed")),
            "intent": _s(c.get("intent")) or "Informational",
            "pillar_url": _s(c.get("pillar_url")),
            "cluster_keywords": _list_of_str(c.get("cluster_keywords"), 10),
        })
    out["keyword_clusters"] = clusters

    moat = _dict(d.get("competitive_moat"))
    out["competitive_moat"] = {
        "title": _s(moat.get("title")),
        "play": _s(moat.get("play")),
        "advantage": _s(moat.get("advantage")),
    }

    cp = _dict(d.get("content_plan"))
    buckets = []
    for b in (cp.get("buckets") or [])[:6]:
        if not isinstance(b, dict):
            continue
        buckets.append({
            "name": _s(b.get("name")),
            "purpose": _s(b.get("purpose")),
            "examples": _list_of_str(b.get("examples"), 5),
        })
    out["content_plan"] = {"buckets": buckets, "standards": _s(cp.get("standards"))}

    out["justwords_advantage"] = _list_of_str(d.get("justwords_advantage"), 8)
    return out
