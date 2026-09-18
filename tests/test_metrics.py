import pandas as pd

from review_intel.metrics import coverage_table, safe_pct, weighted_share


def test_safe_pct():
    assert safe_pct(1, 4) == 25.0
    assert safe_pct(0, 0) is None
    assert safe_pct(3, None) is None


def test_weighted_share_corrects_for_equal_per_star_sampling():
    # 100 reviews sampled at each star, but 90% of real customers are 5-star.
    # Aspect mentioned by 10% of 5-star sample and 80% of 1-star sample.
    breakdown = {5: 0.9, 4: 0.0, 3: 0.0, 2: 0.0, 1: 0.1}
    sampled = {5: 100, 4: 0, 3: 0, 2: 0, 1: 100}
    mentions = {5: 10, 1: 80}
    # raw sample share would be 90/200 = 45% -> wrong
    # correct: 0.9*0.10 + 0.1*0.80 = 0.17
    assert abs(weighted_share(mentions, sampled, breakdown) - 17.0) < 1e-9


def test_weighted_share_ignores_stars_with_no_sample_and_renormalizes():
    breakdown = {5: 0.5, 4: 0.5, 3: 0, 2: 0, 1: 0}
    sampled = {5: 10, 4: 0}
    mentions = {5: 5}
    # only 5-star observed -> estimate is over observed weight only: 50%
    assert weighted_share(mentions, sampled, breakdown) == 50.0


def test_weighted_share_no_data_returns_none():
    assert weighted_share({}, {}, {5: 1.0}) is None
    assert weighted_share({5: 1}, {5: 10}, None) is None
    assert weighted_share({5: 1}, {5: 10}, {}) is None


def test_coverage_table_flags_partial():
    reviews = pd.DataFrame({"asin": ["A"] * 3 + ["B"], "rating": [1, 1, 5, 5]})
    cov = pd.DataFrame({"asin": ["A", "A", "B"], "star": [1, 5, 5],
                        "available_reviews": [2, 10, 0]})
    t = coverage_table(reviews, cov).set_index(["asin", "star"])
    assert t.loc[("A", 1), "collected"] == 2
    assert t.loc[("A", 1), "coverage_pct"] == 100.0
    assert t.loc[("A", 5), "coverage_pct"] == 10.0
    assert bool(t.loc[("A", 5), "partial"]) is True
    # Amazon says 0 available but we got 1 -> coverage undefined, not infinity
    assert pd.isna(t.loc[("B", 5), "coverage_pct"])
