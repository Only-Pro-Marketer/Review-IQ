"""Numbers shown in the dashboard and handed to the AI analyst.

Sampling note: reviews are pulled with an equal cap per star level, so the raw sample
over-represents low-star reviews. Anything that claims "% of customers" must go through
weighted_share(), which re-weights each star bucket by Amazon's real star breakdown.
"""
from __future__ import annotations

import json

import pandas as pd


def safe_pct(num, den) -> float | None:
    if den is None or num is None or den == 0:
        return None
    return round(100.0 * num / den, 1)


def weighted_share(mentions: dict, sampled: dict, breakdown: dict | None) -> float | None:
    """Estimated % of ALL reviewers mentioning something, corrected for per-star sampling.

    mentions[star]  = sampled reviews at that star mentioning it
    sampled[star]   = sampled reviews at that star
    breakdown[star] = Amazon's share of ratings at that star (0..1)
    Stars with no sample are excluded and the remaining weights renormalized.
    """
    if not breakdown:
        return None
    total_w, acc = 0.0, 0.0
    for star, n in sampled.items():
        w = breakdown.get(star) or 0.0
        if not n or w <= 0:
            continue
        acc += w * (mentions.get(star, 0) / n)
        total_w += w
    if total_w == 0:
        return None
    return 100.0 * acc / total_w


def breakdown_of(product_row) -> dict:
    return {s: product_row.get(f"pct_{s}") for s in range(1, 6) if product_row.get(f"pct_{s}") is not None}


def coverage_table(reviews: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    got = (reviews.groupby(["asin", "rating"]).size().rename("collected").reset_index()
           .rename(columns={"rating": "star"}))
    cov = coverage[["asin", "star", "available_reviews"]] if len(coverage) else \
        pd.DataFrame(columns=["asin", "star", "available_reviews"])
    t = got.merge(cov, on=["asin", "star"], how="outer")
    t["collected"] = t["collected"].fillna(0).astype(int)
    t["coverage_pct"] = [safe_pct(min(c, a), a) if pd.notna(a) else None
                         for c, a in zip(t.collected, t.available_reviews)]
    t["coverage_pct"] = pd.to_numeric(t["coverage_pct"])
    t["partial"] = t.coverage_pct.lt(100).fillna(False)
    return t.sort_values(["asin", "star"], ascending=[True, False]).reset_index(drop=True)


def product_summary(products: pd.DataFrame, reviews: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, p in products.iterrows():
        r = reviews[reviews.asin == p.asin]
        rows.append({
            "asin": p.asin, "role": p.role, "brand": p.brand,
            "title": (p.title or "")[:80],
            "price": p.price, "currency": p.currency,
            "stock": ("in stock" if p.in_stock else "OUT OF STOCK / no Buy Box") if pd.notna(p.in_stock) else None,
            "stars (Amazon)": p.stars,
            "ratings (Amazon)": p.ratings_total,
            "% 1-2 star (Amazon)": round(100 * ((p.pct_1 or 0) + (p.pct_2 or 0)), 1)
            if pd.notna(p.pct_1) else None,
            "monthly bought": p.monthly_bought,
            "listing images": None, "videos": p.videos_count, "A+": bool(p.has_aplus),
            "Amazon's Choice": bool(p.is_amazon_choice), "variants": p.variant_count,
            "reviews collected": len(r),
            "% verified (sample)": safe_pct(int(r.verified.sum()), len(r)),
            "% with photos (sample)": safe_pct(int((r.image_count > 0).sum()), len(r)),
            "customer photos": int(r.image_count.sum()),
            "newest review": r.review_date.max() if len(r) else None,
        })
    return pd.DataFrame(rows)


def monthly_trend(reviews: pd.DataFrame) -> pd.DataFrame:
    d = reviews.dropna(subset=["review_date"]).copy()
    if d.empty:
        return pd.DataFrame(columns=["asin", "month", "reviews", "negative", "neg_pct"])
    d["month"] = pd.to_datetime(d.review_date).dt.to_period("M").dt.to_timestamp()
    g = d.groupby(["asin", "month"]).agg(reviews=("review_id", "count"),
                                         negative=("rating", lambda s: int((s <= 2).sum()))).reset_index()
    g["neg_pct"] = [safe_pct(n, t) for n, t in zip(g.negative, g.reviews)]
    return g


def aspect_prevalence(reviews: pd.DataFrame, products: pd.DataFrame, negative_only: bool) -> pd.DataFrame:
    """Weighted % of all customers mentioning each aspect (positive or negative), per ASIN."""
    tagged = reviews.dropna(subset=["aspects"]).copy()
    if tagged.empty:
        return pd.DataFrame()
    tagged["aspect_list"] = tagged.aspects.apply(lambda a: json.loads(a) if isinstance(a, str) else [])
    rows = []
    for asin, grp in tagged.groupby("asin"):
        prow = products[products.asin == asin]
        if prow.empty:
            continue
        bd = breakdown_of(prow.iloc[0].to_dict())
        sampled = grp.groupby("rating").size().to_dict()
        sel = grp[grp.sentiment < 0] if negative_only else grp[grp.sentiment > 0]
        exploded = sel.explode("aspect_list").dropna(subset=["aspect_list"])
        for aspect, g2 in exploded.groupby("aspect_list"):
            share = weighted_share(g2.groupby("rating").review_id.nunique().to_dict(), sampled, bd)
            rows.append({"asin": asin, "aspect": aspect, "weighted_pct": round(share, 1) if share is not None else None,
                         "sample_mentions": int(g2.review_id.nunique())})
    return pd.DataFrame(rows)


def emotion_mix(reviews: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    tagged = reviews.dropna(subset=["emotion"])
    rows = []
    for asin, grp in tagged.groupby("asin"):
        prow = products[products.asin == asin]
        if prow.empty:
            continue
        bd = breakdown_of(prow.iloc[0].to_dict())
        sampled = grp.groupby("rating").size().to_dict()
        for emo, g2 in grp.groupby("emotion"):
            share = weighted_share(g2.groupby("rating").size().to_dict(), sampled, bd)
            rows.append({"asin": asin, "emotion": emo,
                         "weighted_pct": round(share, 1) if share is not None else None,
                         "sample_count": len(g2)})
    return pd.DataFrame(rows)
