"""Discovery-call inputs — the facts about the client's business that an
automated audit can never find on its own.

The JW proposal template opens with "What You Told Us": budget, current lead
flow, close rate, goals, pain points. None of that is discoverable from a URL,
so it's collected from the person running the audit (CLI flags, the web form, or
a JSON file) and threaded into the proposal.

Every field is optional. Anything left blank simply drops out of the rendered
proposal rather than showing a placeholder — a half-filled form still produces a
clean document, and ``projections.py`` only does ROI maths for the numbers it
actually has.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# Currency display. INR is Justwords' primary; USD is the secondary in packages.py.
_SYMBOL = {"INR": "₹", "USD": "$", "GBP": "£", "EUR": "€", "AED": "AED "}


def _num(v) -> Optional[float]:
    """Parse a number out of loose human input: '1,200', '₹50k', '2.5%', '$1.2M'."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(",", "").replace("_", "")
    if not s:
        return None
    mult = 1.0
    m = re.search(r"([\d.]+)\s*(k|l|lakh|lac|m|mn|cr|crore)?", s)
    if not m:
        return None
    suffix = m.group(2)
    mult = {"k": 1e3, "l": 1e5, "lakh": 1e5, "lac": 1e5,
            "m": 1e6, "mn": 1e6, "cr": 1e7, "crore": 1e7}.get(suffix or "", 1.0)
    try:
        return float(m.group(1)) * mult
    except ValueError:
        return None


def _int(v) -> Optional[int]:
    n = _num(v)
    return int(round(n)) if n is not None else None


def _pct(v) -> Optional[float]:
    """A percentage as 0-100. Accepts '25', '25%', or a 0-1 fraction ('0.25')."""
    n = _num(v)
    if n is None:
        return None
    if 0 < n <= 1 and "%" not in str(v):
        n *= 100.0  # they gave a fraction
    return max(0.0, min(100.0, n))


def _lines(v) -> list[str]:
    """A list from either a real list or newline/semicolon-separated text."""
    if isinstance(v, (list, tuple)):
        items = [str(x).strip() for x in v]
    elif isinstance(v, str):
        items = [p.strip() for p in re.split(r"[\n;]+", v)]
    else:
        return []
    return [i for i in items if i]


def _domains(v) -> list[str]:
    """Competitor domains from a list or comma/newline-separated text."""
    if isinstance(v, (list, tuple)):
        raw = [str(x) for x in v]
    elif isinstance(v, str):
        raw = re.split(r"[\s,;\n]+", v)
    else:
        return []
    out: list[str] = []
    for item in raw:
        d = item.strip().lower()
        if not d:
            continue
        d = re.sub(r"^https?://", "", d).rstrip("/")
        d = re.sub(r"^www\.", "", d).split("/")[0]
        if "." in d and d not in out:
            out.append(d)
    return out[:5]  # the template compares against 5 competitors


@dataclass
class Discovery:
    """What the client told us on the discovery call. All fields optional."""

    client_name: str = ""
    prepared_by: str = ""
    prepared_by_title: str = ""
    currency: str = "INR"

    budget_min: Optional[float] = None
    budget_max: Optional[float] = None

    current_leads: Optional[int] = None        # monthly inbound leads today
    close_rate: Optional[float] = None         # % of leads that become deals
    avg_deal_value: Optional[float] = None     # revenue per closed deal
    deals_per_month: Optional[int] = None      # stated, else derived
    conversion_rate: Optional[float] = None    # % of visits -> leads

    target_leads: Optional[int] = None         # leads/month they want
    goal: str = ""                             # their stated goal, in their words
    dream_state: str = ""
    geo_focus: str = ""                        # Local / Regional / National
    services: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)

    # --- deck presentation choices (operator-set, not client facts) ---------
    include_categories: list[str] = field(default_factory=list)  # service areas to feature
    organic_override: Optional[float] = None   # true monthly organic traffic

    valid_days: int = 30

    # ---- construction -----------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict | None) -> "Discovery":
        d = d or {}

        def s(*keys) -> str:
            for k in keys:
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""

        cur = (s("currency") or "INR").upper()

        # Budget may arrive split (budget_min/budget_max) or as one range string
        # from a form field: "50K-95K", "₹50,000 to ₹95,000", "under 60k".
        bmin, bmax = _num(d.get("budget_min")), _num(d.get("budget_max"))
        if bmin is None and bmax is None and s("budget"):
            parts = [p for p in re.split(r"\s*(?:-|–|to)\s*", s("budget")) if p.strip()]
            nums = [n for n in (_num(p) for p in parts) if n is not None]
            if len(nums) >= 2:
                bmin, bmax = min(nums[:2]), max(nums[:2])
            elif nums:
                bmax = nums[0]

        obj = cls(
            client_name=s("client_name", "client", "company"),
            prepared_by=s("prepared_by", "author"),
            prepared_by_title=s("prepared_by_title", "title"),
            currency=cur if cur in _SYMBOL else "INR",
            budget_min=bmin,
            budget_max=bmax,
            current_leads=_int(d.get("current_leads")),
            close_rate=_pct(d.get("close_rate")),
            avg_deal_value=_num(d.get("avg_deal_value")),
            deals_per_month=_int(d.get("deals_per_month")),
            conversion_rate=_pct(d.get("conversion_rate")),
            target_leads=_int(d.get("target_leads")),
            goal=s("goal"),
            dream_state=s("dream_state"),
            geo_focus=s("geo_focus"),
            services=_lines(d.get("services")),
            pain_points=_lines(d.get("pain_points")),
            competitors=_domains(d.get("competitors")),
            include_categories=_lines(
                re.split(r"[,;\n]+", d["include_categories"])
                if isinstance(d.get("include_categories"), str)
                else d.get("include_categories")),
            # Organic visits are whole numbers — keep them integers so the deck
            # shows "48,000", not "48,000.0".
            organic_override=_int(d.get("organic_override")),
            valid_days=_int(d.get("valid_days")) or 30,
        )
        # Deals/month is derivable when they gave leads + close rate.
        if obj.deals_per_month is None and obj.current_leads and obj.close_rate:
            obj.deals_per_month = int(round(obj.current_leads * obj.close_rate / 100.0))
        return obj

    @classmethod
    def from_file(cls, path: str | Path) -> "Discovery":
        raw = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(raw))

    # ---- introspection ----------------------------------------------------

    @property
    def is_empty(self) -> bool:
        """True when nothing was supplied — the proposal drops Section 1 entirely."""
        d = asdict(self)
        d.pop("valid_days", None)
        d.pop("currency", None)
        return not any(v for v in d.values())

    @property
    def has_economics(self) -> bool:
        """Enough to model revenue: what a lead is worth and how often one closes."""
        return bool(self.close_rate and self.avg_deal_value)

    @property
    def valid_until(self) -> str:
        return (date.today() + timedelta(days=self.valid_days)).strftime("%B %d, %Y")

    @property
    def symbol(self) -> str:
        return _SYMBOL.get(self.currency, "")

    def money(self, amount: Optional[float]) -> str:
        """Format a number as currency, abbreviated for readability (₹1.2Cr, $1.25M).

        INR uses the lakh/crore scale a client in India actually reads; every
        other currency uses K/M.
        """
        if amount is None:
            return "—"
        a = float(amount)
        sym = self.symbol
        scale = (((1e7, "Cr"), (1e5, "L"), (1e3, "K")) if self.currency == "INR"
                 else ((1e6, "M"), (1e3, "K")))
        for cutoff, suffix in scale:
            if a >= cutoff:
                return f"{sym}{a / cutoff:,.2f}".rstrip("0").rstrip(".") + suffix
        return f"{sym}{a:,.0f}"

    @property
    def budget_figure(self) -> Optional[float]:
        """The single number to fit a package against — the top of their range,
        or the one figure they gave. Drives the deterministic tier choice."""
        return self.budget_max or self.budget_min

    @property
    def budget_line(self) -> str:
        if self.budget_min and self.budget_max:
            return f"{self.money(self.budget_min)} – {self.money(self.budget_max)}/month"
        if self.budget_max:
            return f"up to {self.money(self.budget_max)}/month"
        if self.budget_min:
            return f"from {self.money(self.budget_min)}/month"
        return ""

    def digest_for_prompt(self) -> str:
        """What the LLM is told about the client's business. Only stated facts —
        anything absent is omitted so the model can't treat a blank as a zero."""
        if self.is_empty:
            return "(No discovery call data was supplied — do not invent any.)"
        L: list[str] = []
        if self.client_name:
            L.append(f"Client: {self.client_name}")
        if self.budget_line:
            L.append(f"Stated monthly marketing budget: {self.budget_line}")
        if self.current_leads is not None:
            L.append(f"Current inbound leads/month: {self.current_leads}")
        if self.close_rate is not None:
            L.append(f"Close rate: {self.close_rate:g}%")
        if self.deals_per_month is not None:
            L.append(f"Deals closed/month: {self.deals_per_month}")
        if self.avg_deal_value is not None:
            L.append(f"Average deal value: {self.money(self.avg_deal_value)}")
        if self.target_leads is not None:
            L.append(f"Target leads/month: {self.target_leads}")
        if self.goal:
            L.append(f"Stated goal (their words): {self.goal}")
        if self.dream_state:
            L.append(f"Dream-state outcome: {self.dream_state}")
        if self.geo_focus:
            L.append(f"Geographic focus: {self.geo_focus}")
        if self.services:
            L.append("Primary services: " + ", ".join(self.services))
        if self.pain_points:
            L.append("Pain points they raised:")
            L += [f"  - {p}" for p in self.pain_points]
        if self.competitors:
            L.append("Competitors they named: " + ", ".join(self.competitors))
        return "\n".join(L)
