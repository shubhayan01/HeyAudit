"""Competitor intelligence for the proposal's Competitive Landscape section.

The comparison table pits the client against named competitors on authority,
traffic, keywords and referring domains. Those numbers have to be *real* — a
prospect will check them — and there is no free source for a rival site's
off-page metrics, so bd-auto no longer fetches them: the comparison is omitted
rather than estimated. ``analyse`` therefore returns no competitor rows.

The ``table`` and ``digest_for_prompt`` helpers are kept so the deck and the
pitch prompt degrade cleanly (empty comparison, "no competitor data") and so a
future paid data source can be wired back into ``analyse`` without touching the
rest of the pipeline.
"""

from __future__ import annotations

from typing import Callable, Optional

MAX_COMPETITORS = 5


def analyse(domains: list[str], key: Optional[str] = None,
            progress: Optional[Callable[[str, str], None]] = None) -> list[dict]:
    """No-op: competitor off-page metrics have no free data source.

    Returns [] so the Competitive Landscape section is omitted rather than shown
    with estimated numbers a prospect could disprove.
    """
    return []


def table(client: dict, competitors: list[dict], client_label: str = "Your site") -> dict:
    """The comparison table: one row per site, client last, plus column labels.

    Rows carry raw values (``None`` where unmeasured) — formatting is the
    template's job.
    """
    rows = []
    for c in competitors:
        if not c.get("loaded"):
            continue
        rows.append({
            "name": c.get("domain"),
            "is_client": False,
            "authority": c.get("domain_strength"),
            "traffic": c.get("organic_traffic"),
            "keywords": c.get("keywords_total"),
            "referring_domains": c.get("referring_domains"),
            "backlinks": c.get("backlinks"),
            "load_s": (round(c["load_ms"] / 1000, 1)
                       if isinstance(c.get("load_ms"), (int, float)) else None),
            "grade": c.get("overall_grade"),
        })
    rows.sort(key=lambda r: (r["authority"] or 0), reverse=True)
    rows.append({
        "name": client_label,
        "is_client": True,
        "authority": client.get("domain_strength"),
        "traffic": client.get("organic_traffic"),
        "keywords": client.get("keywords_total"),
        "referring_domains": client.get("referring_domains"),
        "backlinks": client.get("backlinks"),
        "load_s": (round(client["load_ms"] / 1000, 1)
                   if isinstance(client.get("load_ms"), (int, float)) else None),
        "grade": client.get("overall_grade"),
    })
    return {"rows": rows, "count": len(rows) - 1}


def digest_for_prompt(client: dict, competitors: list[dict]) -> str:
    """Measured competitor metrics, as prompt text.

    The model is given these so its narrative matches the tables; it is told
    elsewhere never to restate the numbers itself.
    """
    live = [c for c in competitors if c.get("loaded")]
    if not live:
        return "(No competitor data was gathered — do not invent competitor metrics.)"

    def line(m: dict, label: str) -> str:
        def f(key, fmt="{:,}"):
            v = m.get(key)
            return fmt.format(v) if isinstance(v, (int, float)) else "n/a"
        return (f"- {label}: authority {f('domain_strength')}, "
                f"organic traffic {f('organic_traffic')}/mo, "
                f"ranking keywords {f('keywords_total')}, "
                f"referring domains {f('referring_domains')}, "
                f"backlinks {f('backlinks')}, "
                f"overall grade {m.get('overall_grade') or 'n/a'}")

    lines = ["MEASURED COMPETITOR METRICS (live):",
             line(client, "CLIENT (the site we are pitching)")]
    lines += [line(c, c.get("domain", "?")) for c in live]
    failed = [c.get("domain") for c in competitors if not c.get("loaded")]
    if failed:
        lines.append(f"(No data retrieved for: {', '.join(failed)} — ignore these.)")
    return "\n".join(lines)
