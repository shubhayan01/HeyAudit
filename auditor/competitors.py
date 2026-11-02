"""Competitor intelligence for the proposal's Competitive Landscape section.

The JW template compares the client against up to five named competitors on
authority, traffic, keywords and referring domains. Those numbers have to be
real — a prospect will check them — so each competitor domain gets its own
SEOptimer report through the same API client the audit uses, and every figure in
the comparison table is measured rather than estimated.

Cost and time: one API credit per competitor, ~60-70s each. Reports are fetched
concurrently (a few workers, not all at once) so four competitors add roughly
90 seconds to a run rather than five minutes.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from .checks import seoptimer

# SEOptimer reports are slow but light on our side; a small pool keeps wall-clock
# down without hammering their API.
MAX_WORKERS = 3
MAX_COMPETITORS = 5


def _fetch_one(domain: str, key: str) -> dict:
    """One competitor's metrics. Never raises — a failed competitor is dropped
    from the comparison rather than taking the proposal down with it."""
    try:
        rep = seoptimer.fetch_report(f"https://{domain}/", key, domain=domain)
    except Exception as exc:  # noqa: BLE001
        return {"domain": domain, "loaded": False,
                "error": f"{exc.__class__.__name__}: {exc}"}
    m = seoptimer.metrics(rep)
    m["domain"] = domain
    m["loaded"] = bool(rep.get("loaded"))
    if rep.get("error"):
        m["error"] = rep["error"]
    return m


def analyse(domains: list[str], key: Optional[str],
            progress: Optional[Callable[[str, str], None]] = None) -> list[dict]:
    """Fetch metrics for each competitor domain, in parallel. Best-effort.

    Returns one metrics dict per domain (see ``seoptimer.metrics``), including
    ones that failed — those carry ``loaded: False`` and are filtered out of the
    comparison tables by ``projections.gaps``.
    """
    domains = [d for d in dict.fromkeys(domains) if d][:MAX_COMPETITORS]
    if not domains:
        return []
    if not key:
        return [{"domain": d, "loaded": False,
                 "error": "no SEOptimer API key configured"} for d in domains]

    def note(msg: str, cat: str = "competitors") -> None:
        if progress:
            try:
                progress(msg, cat)
            except Exception:
                pass

    note(f"Analysing {len(domains)} competitor{'s' if len(domains) > 1 else ''}"
         f" (~90s)", "competitors")

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(domains))) as pool:
        futures = {pool.submit(_fetch_one, d, key): d for d in domains}
        for fut in as_completed(futures):
            d = futures[fut]
            try:
                out[d] = fut.result()
            except Exception as exc:  # noqa: BLE001
                out[d] = {"domain": d, "loaded": False,
                          "error": f"{exc.__class__.__name__}: {exc}"}
            ok = out[d].get("loaded")
            note(f"Competitor: {d}", "done" if ok else "unavailable")

    # Preserve the order the user listed them in.
    return [out[d] for d in domains if d in out]


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

    lines = ["MEASURED COMPETITOR METRICS (SEOptimer, live):",
             line(client, "CLIENT (the site we are pitching)")]
    lines += [line(c, c.get("domain", "?")) for c in live]
    failed = [c.get("domain") for c in competitors if not c.get("loaded")]
    if failed:
        lines.append(f"(No data retrieved for: {', '.join(failed)} — ignore these.)")
    return "\n".join(lines)
