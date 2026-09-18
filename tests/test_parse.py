import json
from pathlib import Path

from review_intel.parse import (
    normalize_product,
    normalize_review,
    parse_helpful_votes,
    parse_items,
    validate_asin,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "apify_reviews_sample.json").read_text())


def test_helpful_votes_variants():
    assert parse_helpful_votes(None) == 0
    assert parse_helpful_votes("") == 0
    assert parse_helpful_votes("One person found this helpful") == 1
    assert parse_helpful_votes("12 people found this helpful") == 12
    assert parse_helpful_votes("1,234 people found this helpful") == 1234
    assert parse_helpful_votes(7) == 7


def test_validate_asin():
    assert validate_asin(" b0cwxns552 ") == "B0CWXNS552"
    assert validate_asin("https://www.amazon.com/Some-Thing/dp/B0CWXNS552/ref=sr_1") == "B0CWXNS552"
    assert validate_asin("B0CWXNS55") is None
    assert validate_asin("") is None


def test_normalize_real_review():
    r = normalize_review(FIXTURE[0], "US")
    assert r["review_id"] == "RK4SEUGGJK3B9"
    assert r["asin"] == "B0CWXNS552"
    assert r["rating"] == 1
    assert r["review_date"] == "2026-09-15"
    assert r["verified"] is True
    assert r["vine"] is False
    assert r["helpful_votes"] == 0
    assert r["images"] == []
    assert r["title"] == "Took me in circles"
    assert r["marketplace"] == "US"
    assert r["filter_star"] == 1


def test_review_images_strings_or_dicts_and_date_fallback():
    item = dict(FIXTURE[0])
    item["reviewImages"] = ["https://a/1.jpg", {"url": "https://a/2.jpg"}, None, ""]
    item["date"] = None
    item["reviewedIn"] = "Reviewed in Canada on March 3, 2025"
    r = normalize_review(item, "CA")
    assert r["images"] == ["https://a/1.jpg", "https://a/2.jpg"]
    assert r["review_date"] == "2025-03-03"


def test_error_record_is_not_a_review():
    assert normalize_review({"error": "Product not found", "input": "x"}, "US") is None


def test_normalize_real_product():
    p = normalize_product(FIXTURE[0]["product"], "US")
    assert p["asin"] == "B0CWXNS552"
    assert p["brand"] == "Apple"
    assert p["price"] == 29
    assert p["currency"] == "USD"  # from marketplace, not the ambiguous "$"
    assert p["stars"] == 4.7
    assert p["ratings_total"] == 59652
    assert abs(sum(p["star_breakdown"].values()) - 1.0) < 0.02
    assert p["star_breakdown"][5] == 0.86
    assert len(p["images"]) >= 1 and p["images"][0].startswith("https://")
    assert p["ai_summary"].startswith("Customers find")
    assert p["ai_keywords"][0]["name"] == "Quality"
    assert p["ai_keywords"][0]["negative"] == 398


def test_parse_items_collects_reviews_products_coverage_errors():
    items = FIXTURE + [{"error": "Captcha", "input": "https://www.amazon.com/dp/B000000000"}]
    out = parse_items(items, "US")
    assert len(out["reviews"]) == 5
    assert set(out["products"]) == {"B0CWXNS552"}
    assert out["coverage"][("B0CWXNS552", 1)] == {"available_reviews": 1533, "available_ratings": 59652}
    assert len(out["errors"]) == 1


def test_parse_items_dedupes_same_review_twice():
    out = parse_items(FIXTURE + FIXTURE, "US")
    assert len(out["reviews"]) == 5


def test_review_image_thumbnails_upgraded_to_full_resolution():
    from review_intel.parse import full_size_image
    thumb = "https://m.media-amazon.com/images/W/BW_MEDIAX_AVIF_MEASUREMENT_1306696-T2/images/I/61vNaNoTO7L._SY88.jpg"
    assert full_size_image(thumb) == "https://m.media-amazon.com/images/I/61vNaNoTO7L.jpg"
    assert full_size_image("https://m.media-amazon.com/images/I/71rP7f78eFL._AC_SL1500_.jpg") == \
        "https://m.media-amazon.com/images/I/71rP7f78eFL.jpg"
    assert full_size_image("https://example.com/x.png") == "https://example.com/x.png"
    item = dict(FIXTURE[0], reviewImages=[thumb])
    assert normalize_review(item, "US")["images"] == ["https://m.media-amazon.com/images/I/61vNaNoTO7L.jpg"]


def test_bare_number_reaction():
    assert parse_helpful_votes("96") == 96
