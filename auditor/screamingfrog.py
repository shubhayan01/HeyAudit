"""Optional Screaming Frog SEO Spider integration for deeper technical detail.

Screaming Frog is the industry-standard desktop crawler. When it's installed
*and licensed* (its headless command-line mode needs the paid licence), bd-auto
shells out to ``ScreamingFrogSEOSpiderCli`` to pull richer technical facts —
response-code breakdown, directives, and detected technologies — than the native
crawler produces on its own.

This is strictly optional and best-effort:

  * ``available()`` returns the CLI path only if a real executable is found.
  * ``tech_summary()`` runs a headless crawl, parses the CSV exports, and returns
    a plain dict. It never raises — any failure (not installed, unlicensed CLI,
    timeout, changed export columns) returns ``None`` and the caller falls back
    to its normal data source.

Because it is auto-detected, installing Screaming Frog later automatically
upgrades the audit's technical section with no code change.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Where the Windows installer and common cross-platform installs put the CLI.
_CANDIDATES = [
    r"C:\Program Files\Screaming Frog SEO Spider\ScreamingFrogSEOSpiderCli.exe",
    r"C:\Program Files (x86)\Screaming Frog SEO Spider\ScreamingFrogSEOSpiderCli.exe",
    "/usr/bin/screamingfrogseospider",
    "/usr/local/bin/screamingfrogseospider",
    "/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderCli",
]

_CLI_NAMES = ["ScreamingFrogSEOSpiderCli", "screamingfrogseospider",
              "ScreamingFrogSEOSpiderCli.exe"]


def available() -> str | None:
    """Return the Screaming Frog CLI path if one exists, else ``None``.

    Honours a ``SCREAMINGFROG_CLI`` env override first, then PATH, then the
    usual install locations.
    """
    override = os.environ.get("SCREAMINGFROG_CLI")
    if override and Path(override).is_file():
        return override
    for name in _CLI_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for path in _CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def _read_csv(path: Path) -> list[dict]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def tech_summary(url: str, max_pages: int = 500, timeout_s: int = 600) -> dict | None:
    """Run a headless Screaming Frog crawl and return parsed technical facts.

    Returns ``None`` when Screaming Frog is unavailable or the run fails, so the
    caller keeps its existing behaviour. On success returns a dict like::

        {"source": "Screaming Frog", "urls_crawled": 412,
         "status_codes": {"200": 388, "301": 18, "404": 6},
         "technologies": ["WordPress", "Cloudflare", ...],
         "indexable": 380, "non_indexable": 32}
    """
    cli = available()
    if not cli:
        return None

    out_dir = Path(tempfile.mkdtemp(prefix="bd-sf-"))
    try:
        cmd = [
            cli, "--headless", "--crawl", url,
            "--output-folder", str(out_dir), "--overwrite",
            "--export-tabs", "Internal:All",
            "--bulk-export", "Response Codes:All",
            "--save-crawl",
        ]
        # Cap the crawl if the config supports it; unknown flags are ignored by
        # some CLI versions, so this stays best-effort.
        try:
            subprocess.run(cmd, cwd=str(out_dir), timeout=timeout_s,
                           capture_output=True, check=False)
        except subprocess.TimeoutExpired:
            return None

        internal = _read_csv(out_dir / "internal_all.csv")
        if not internal:
            # Column/name layouts vary between versions — take the first CSV.
            csvs = sorted(out_dir.glob("*.csv"))
            if csvs:
                internal = _read_csv(csvs[0])
        if not internal:
            return None

        status_codes: dict[str, int] = {}
        techs: set[str] = set()
        indexable = non_indexable = 0
        for row in internal[:max_pages]:
            code = (row.get("Status Code") or row.get("Status") or "").strip()
            if code:
                status_codes[code] = status_codes.get(code, 0) + 1
            idx = (row.get("Indexability") or "").strip().lower()
            if idx == "indexable":
                indexable += 1
            elif idx:
                non_indexable += 1
            # Screaming Frog can emit a "Technologies" column (with the tech add-on).
            tv = (row.get("Technologies") or "").strip()
            if tv:
                for t in tv.replace(";", ",").split(","):
                    t = t.strip()
                    if t:
                        techs.add(t)

        return {
            "source": "Screaming Frog",
            "urls_crawled": len(internal),
            "status_codes": dict(sorted(status_codes.items())),
            "technologies": sorted(techs),
            "indexable": indexable,
            "non_indexable": non_indexable,
        }
    except Exception:  # noqa: BLE001 - optional integration must never break the audit
        return None
    finally:
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass
