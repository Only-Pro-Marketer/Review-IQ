"""Turn raw Apify dataset items into clean review / product records."""
from __future__ import annotations

import re
from datetime import datetime

from .config import MARKETPLACES

ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/product-reviews/|^)([A-Z0-9]{10})(?:[/?]|$)")
STAR_FILTERS = {"fiveStar": 5, "fourStar": 4, "threeStar": 3, "twoStar": 2, "oneStar": 1}
_WORD_NUMS = {"one": 1, "a": 1, "an": 1}


def validate_asin(text: str | None) -> str | None:
    """Accept a bare ASIN or an Amazon product URL; return the uppercase ASIN or None."""
    if not text:
        return None
    t = text.strip()
    m = ASIN_RE.search(t if t.startswith("http") else t.upper())
    if not m:
        m = ASIN_RE.search(t.upper())
    return m.group(1).upper() if m else None


def parse_helpful_votes(reaction) -> int:
    if reaction is None or reaction == "":
        return 0
    if isinstance(reaction, (int, float)):
        return int(reaction)
    s = str(reaction).strip().lower()
    m = re.search(r"([\d][\d,\.]*)", s)
    if m:
        return int(re.sub(r"[,\.]", "", m.group(1)))
    first = s.split(" ", 1)[0]
    return _WORD_NUMS.get(first, 0)


def _parse_date(date_str, reviewed_in) -> str | None:
    if date_str:
        try:
            return datetime.fromisoformat(str(date_str)[:10]).date().isoformat()
        except ValueError:
            pass
    if reviewed_in:
        m = re.search(r"on (\w+ \d{1,2}, \d{4})", str(reviewed_in))
        if m:
            try:
                return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
            except ValueError:
                return None
    return None


_AMZ_IMG_RE = re.compile(r"^https?://[^/]*media-amazon\.com/(?:.*?/)?images/I/([A-Za-z0-9+\-%]+)(?:\.[^/]*)?\.(jpg|jpeg|png|gif|webp)$")


def full_size_image(url: str) -> str:
    """Amazon review photos arrive as tiny thumbnails (e.g. ._SY88.jpg). Dropping the
    size modifier returns the original upload (e.g. 1632x1224)."""
    m = _AMZ_IMG_RE.match(url or "")
    return f"https://m.media-amazon.com/images/I/{m.group(1)}.{m.group(2)}" if m else url


def _image_urls(raw) -> list[str]:
    out = []
    for x in raw or []:
        url = x.get("url") if isinstance(x, dict) else x
        if url and isinstance(url, str):
            url = full_size_image(url)
            if url not in out:
                out.append(url)
    return out


def normalize_review(item: dict, marketplace: str) -> dict | None:
    rid = item.get("reviewId")
    if not rid:
        return None
    rating = item.get("ratingScore")
    reviewed_in = item.get("reviewedIn") or ""
    country = re.search(r"Reviewed in (.+?) on ", reviewed_in)
    return {
        "review_id": rid,
        "marketplace": marketplace,
        "asin": item.get("productOriginalAsin") or item.get("productAsin"),
        "variant_asin": item.get("variantAsin"),
        "rating": int(rating) if rating is not None else None,
        "title": (item.get("reviewTitle") or "").strip(),
        "body": (item.get("reviewDescription") or "").strip(),
        "review_date": _parse_date(item.get("date"), reviewed_in),
        "reviewer_country": country.group(1) if country else item.get("country"),
        "verified": bool(item.get("isVerified")),
        "vine": bool(item.get("isAmazonVine")),
        "helpful_votes": parse_helpful_votes(item.get("reviewReaction")),
        "variant": item.get("variant"),
        "review_url": item.get("reviewUrl"),
        "images": _image_urls(item.get("reviewImages")),
        "filter_star": STAR_FILTERS.get(item.get("filterByRating")),
        "filter_keyword": item.get("filterByKeyword"),
    }


def _kv_list(raw) -> list[dict]:
    return [{"key": x.get("key"), "value": x.get("value")} for x in (raw or []) if isinstance(x, dict)]


def normalize_product(p: dict, marketplace: str) -> dict:
    price = p.get("price") or {}
    list_price = p.get("listPrice") or {}
    breakdown_raw = p.get("starsBreakdown") or {}
    breakdown = {}
    for s in range(1, 6):
        v = breakdown_raw.get(f"{s}star")
        if v is not None:
            breakdown[s] = float(v)
    ai = p.get("aiReviewsSummary") or {}
    keywords = []
    for k in ai.get("keywords") or []:
        counts = k.get("customersMentionedCount") or {}
        keywords.append({
            "name": k.get("name"), "sentiment": k.get("sentiment"), "text": k.get("text"),
            "total": counts.get("total"), "positive": counts.get("positive"),
            "negative": counts.get("negative"),
        })
    images = p.get("highResolutionImages") or []
    if not images and p.get("thumbnailImage"):
        images = [p["thumbnailImage"]]
    seller = p.get("seller") or {}
    return {
        "asin": p.get("originalAsin") or p.get("asin"),
        "marketplace": marketplace,
        "title": p.get("title"),
        "brand": p.get("brand"),
        "url": p.get("url"),
        "price": price.get("value") if isinstance(price, dict) else None,
        "list_price": list_price.get("value") if isinstance(list_price, dict) else None,
        "currency": MARKETPLACES[marketplace]["currency"],
        "stars": p.get("stars"),
        "ratings_total": p.get("reviewsCount"),
        "star_breakdown": breakdown,
        "monthly_bought": p.get("monthlyPurchaseVolume"),
        "in_stock": p.get("inStock"),
        "stock_text": p.get("inStockText"),
        "seller": seller.get("name"),
        "is_amazon_choice": bool(p.get("isAmazonChoice")),
        "videos_count": p.get("videosCount") or 0,
        "variant_count": len(p.get("variantAsins") or []),
        "bestseller_ranks": p.get("bestsellerRanks"),
        "breadcrumbs": p.get("breadCrumbs"),
        "features": [f for f in (p.get("features") or []) if f],
        "description": p.get("description"),
        "attributes": _kv_list(p.get("attributes")) + _kv_list(p.get("productOverview")),
        "has_aplus": bool(p.get("aPlusContent")),
        "has_brand_story": bool(p.get("brandStory")),
        "ai_summary": ai.get("text"),
        "ai_keywords": keywords,
        "images": images,
        "thumbnail": p.get("thumbnailImage"),
    }


def parse_items(items: list[dict], marketplace: str) -> dict:
    """Split a dataset into reviews, products, per-star coverage and error records."""
    reviews: dict[str, dict] = {}
    products: dict[str, dict] = {}
    coverage: dict[tuple, dict] = {}
    errors: list[dict] = []
    for item in items:
        if item.get("error") and not item.get("reviewId"):
            errors.append({"input": item.get("input") or item.get("url"), "error": str(item.get("error"))})
            continue
        if isinstance(item.get("product"), dict) and item["product"].get("asin"):
            prod = normalize_product(item["product"], marketplace)
            products[prod["asin"]] = prod
        asin = item.get("productOriginalAsin") or item.get("productAsin")
        star = STAR_FILTERS.get(item.get("filterByRating"))
        if asin and star and item.get("totalCategoryReviews") is not None:
            coverage[(asin, star)] = {
                "available_reviews": item.get("totalCategoryReviews"),
                "available_ratings": item.get("totalCategoryRatings"),
            }
        r = normalize_review(item, marketplace)
        if r:
            if r["review_id"] in reviews:
                # keep the richer copy (more images / votes)
                old = reviews[r["review_id"]]
                if len(r["images"]) < len(old["images"]):
                    r["images"] = old["images"]
            reviews[r["review_id"]] = r
    return {"reviews": list(reviews.values()), "products": products, "coverage": coverage, "errors": errors}
