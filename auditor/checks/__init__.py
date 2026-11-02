"""Importing this package registers every check in auditor.core.REGISTRY."""

from . import native  # noqa: F401  (registers native checks)
from . import crawl_checks  # noqa: F401  (registers whole-domain, site-wide checks)
from . import scrapers  # noqa: F401  (registers browser-driven scrapers)
