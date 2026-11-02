"""Groq-powered narrative layer for the audit.

Turns the raw list of CheckResults into a client-ready analysis: an overall
grade, an executive summary, prioritised findings and quick wins. Uses the
Groq Chat Completions API (OpenAI-compatible) over stdlib urllib so it adds no
new dependencies. Degrades gracefully — any failure returns ``None`` and the
report simply renders without the AI section.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from .core import CheckResult, strip_emoji


def _retry_after_seconds(exc: "urllib.error.HTTPError", default: float = 6.0) -> float:
    """Wait time before retrying a 429, from the Retry-After header or the
    'try again in 5.465s' hint in Groq's body. Capped so we never hang."""
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
    "You are a senior technical-SEO consultant writing the analysis section of a "
    "client-facing audit report for a marketing agency. You are given the raw, "
    "machine-collected results of an automated technical SEO audit. Interpret them "
    "for a non-technical business owner: be concrete, prioritise by business impact, "
    "and never invent findings that aren't supported by the data. Reference real "
    "numbers from the data where useful. Keep it sharp and professional."
)

_INSTRUCTIONS = """\
Analyse the audit results below and respond with ONLY a JSON object (no markdown
fences) in exactly this shape:

{
  "grade": "A" | "B" | "C" | "D" | "F",
  "score": <integer 0-100, overall technical SEO health>,
  "headline": "<one punchy sentence summarising the site's SEO health>",
  "executive_summary": "<2-3 short paragraphs, plain English, for a business owner>",
  "key_findings": [
    {
      "title": "<short finding name>",
      "severity": "critical" | "high" | "medium" | "low",
      "finding": "<what the data shows, 1-2 sentences, cite the number/value>",
      "recommendation": "<the specific fix, 1-2 sentences>"
    }
  ],
  "quick_wins": ["<short, high-impact action>", "..."]
}

Rules:
- 4 to 8 key_findings, ordered most-to-least important.
- Base everything strictly on the supplied data. Treat INFO/manual items as "needs review", not failures.
- score should broadly reflect the pass/warn/fail balance and severity of issues.
- 3 to 6 quick_wins, each a single actionable sentence.
"""


def _results_payload(domain: str, url: str, results: list[CheckResult]) -> str:
    lines = [f"Site: {url}  (domain: {domain})", ""]
    for r in results:
        lines.append(f"### {r.category} — {r.title}  [{r.status.label}]")
        if r.summary:
            lines.append(f"Summary: {r.summary}")
        for k, v in (r.details or []):
            lines.append(f"  - {k}: {v}")
        if r.source:
            lines.append(f"Source: {r.source}")
        lines.append("")
    return "\n".join(lines)


def _call_groq(api_key: str, model: str, system: str, user: str,
               timeout: int = 90, max_retries: int = 4) -> str:
    body = json.dumps({
        "model": model,
        "temperature": 0.3,
        "max_tokens": 2600,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
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
        req = urllib.request.Request(GROQ_URL, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                delay = _retry_after_seconds(exc)
                print(f"  … Groq rate-limited; retrying in {delay:.1f}s "
                      f"(attempt {attempt + 1}/{max_retries}).")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("Groq retries exhausted")  # pragma: no cover


def generate_ai_report(
    domain: str,
    url: str,
    results: list[CheckResult],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[dict]:
    """Return the AI analysis dict, or ``None`` if unavailable/failed."""
    api_key = api_key or os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    model = model or os.environ.get("GROQ_MODEL") or DEFAULT_MODEL

    user = _INSTRUCTIONS + "\n\n---\nAUDIT RESULTS:\n\n" + _results_payload(domain, url, results)
    try:
        raw = _call_groq(api_key, model, _SYSTEM, user)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        print(f"  ! Groq request failed (HTTP {exc.code}). {detail[:300]}")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  ! Groq request failed ({exc.__class__.__name__}: {exc}).")
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Some models wrap JSON in stray text; try to salvage the object.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            try:
                parsed = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                print("  ! Groq returned unparseable JSON; skipping AI section.")
                return None
        else:
            return None

    parsed["model"] = model
    return _sanitise(parsed)


def _sanitise(d: dict) -> dict:
    """Coerce the model output into safe, predictable shapes for the template."""
    out: dict = {}
    out["grade"] = str(d.get("grade", "")).strip()[:2] or "—"
    try:
        out["score"] = max(0, min(100, int(round(float(d.get("score", 0))))))
    except (TypeError, ValueError):
        out["score"] = None
    out["headline"] = strip_emoji(str(d.get("headline", "")).strip())
    out["executive_summary"] = strip_emoji(str(d.get("executive_summary", "")).strip())
    out["model"] = d.get("model", "")

    findings = []
    for f in (d.get("key_findings") or [])[:10]:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "medium")).strip().lower()
        if sev not in {"critical", "high", "medium", "low"}:
            sev = "medium"
        findings.append({
            "title": strip_emoji(str(f.get("title", "")).strip()),
            "severity": sev,
            "finding": strip_emoji(str(f.get("finding", "")).strip()),
            "recommendation": strip_emoji(str(f.get("recommendation", "")).strip()),
        })
    out["key_findings"] = findings

    wins = [strip_emoji(str(w).strip()) for w in (d.get("quick_wins") or []) if str(w).strip()]
    out["quick_wins"] = wins[:8]
    return out
