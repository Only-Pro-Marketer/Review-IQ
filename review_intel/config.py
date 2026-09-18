"""Settings, secrets and marketplace definitions."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("REVIEW_DB") or DATA_DIR / "reviews.db")
IMAGES_DIR = DATA_DIR / "images"
REPORTS_DIR = DATA_DIR / "reports"

load_dotenv(ROOT / ".env", override=True)

MARKETPLACES = {
    "US": {"domain": "amazon.com", "currency": "USD", "label": "United States (amazon.com)"},
    "CA": {"domain": "amazon.ca", "currency": "CAD", "label": "Canada (amazon.ca)"},
    "UK": {"domain": "amazon.co.uk", "currency": "GBP", "label": "United Kingdom (amazon.co.uk)"},
    "DE": {"domain": "amazon.de", "currency": "EUR", "label": "Germany (amazon.de)"},
    "FR": {"domain": "amazon.fr", "currency": "EUR", "label": "France (amazon.fr)"},
    "IT": {"domain": "amazon.it", "currency": "EUR", "label": "Italy (amazon.it)"},
    "ES": {"domain": "amazon.es", "currency": "EUR", "label": "Spain (amazon.es)"},
    "AU": {"domain": "amazon.com.au", "currency": "AUD", "label": "Australia (amazon.com.au)"},
    "MX": {"domain": "amazon.com.mx", "currency": "MXN", "label": "Mexico (amazon.com.mx)"},
    "IN": {"domain": "amazon.in", "currency": "INR", "label": "India (amazon.in)"},
    "JP": {"domain": "amazon.co.jp", "currency": "JPY", "label": "Japan (amazon.co.jp)"},
}

REVIEWS_ACTOR = "junglee~amazon-reviews-scraper"
# Apify pay-per-event price per review (BRONZE tier). FREE tier is $0.006.
PRICE_PER_REVIEW_USD = 0.005
MAX_COMPETITORS = 10

CLAUDE_MODELS = {
    "claude-opus-5": {"label": "Claude Opus 5 (best)", "in": 5.0, "out": 25.0},
    "claude-sonnet-5": {"label": "Claude Sonnet 5 (cheaper)", "in": 2.0, "out": 10.0},
    "claude-haiku-4-5": {"label": "Claude Haiku 4.5 (cheapest, tagging only)", "in": 1.0, "out": 5.0},
}
DEFAULT_MODEL = "claude-opus-5"


def product_url(asin: str, marketplace: str) -> str:
    return f"https://www.{MARKETPLACES[marketplace]['domain']}/dp/{asin}"


def apify_token() -> str | None:
    return os.getenv("APIFY_TOKEN") or None


def datadive_key() -> str | None:
    return os.getenv("DATADIVE_API_KEY") or None


def anthropic_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY") or None
