"""Excel export of everything in the database for a marketplace."""
from __future__ import annotations

import io
import json

import pandas as pd

from . import db, metrics


def to_excel(conn, marketplace: str, asins: list[str] | None = None) -> bytes:
    prods = db.products_df(conn, marketplace, asins)
    rv = db.reviews_df(conn, marketplace, asins)
    sheets = {
        "Summary": metrics.product_summary(prods, rv),
        "Products": prods,
        "Listing Images": db.product_images_df(conn, marketplace),
        "Reviews": rv.sort_values(["asin", "rating", "review_date"], ascending=[True, False, False]),
        "Customer Photos": db.review_images_df(conn, marketplace),
        "Coverage": metrics.coverage_table(rv, db.coverage_df(conn, marketplace)),
        "Monthly Trend": metrics.monthly_trend(rv),
        "Neg Aspects (weighted)": metrics.aspect_prevalence(rv, prods, negative_only=True),
        "Pos Aspects (weighted)": metrics.aspect_prevalence(rv, prods, negative_only=False),
        "Emotions (weighted)": metrics.emotion_mix(rv, prods),
    }
    niches = db.niches_df(conn, marketplace)
    if len(niches):
        nid = niches.niche_id.iloc[0]
        ranks = db.keyword_ranks_df(conn, nid)
        sheets["Keywords (DataDive)"] = db.keywords_df(conn, nid)
        sheets["Keyword Ranks"] = ranks.pivot_table(index="keyword", columns="asin", values="organic_rank").reset_index() \
            if len(ranks) else ranks
        sheets["Competitors (DataDive)"] = db.dd_competitors_df(conn, nid)
    last = db.df(conn, "select * from analyses where marketplace=? order by id desc limit 1", (marketplace,))
    if len(last):
        items = json.loads(last.insights_json.iloc[0]).get("action_items", [])
        sheets["AI Action Plan"] = pd.DataFrame(items)
    if asins:
        for name in ("Listing Images", "Customer Photos"):
            sheets[name] = sheets[name][sheets[name].asin.isin(asins)]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, frame in sheets.items():
            (frame if len(frame) else pd.DataFrame({"note": ["no data yet"]})).to_excel(
                xw, sheet_name=name[:31], index=False)
    return buf.getvalue()
