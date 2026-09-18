import json
from pathlib import Path

from review_intel import analyst, db
from review_intel.export import to_excel
from review_intel.parse import parse_items

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "apify_reviews_sample.json").read_text())


def _seed(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.save_parsed(conn, parse_items(FIXTURE, "US"), "US", roles={"B0CWXNS552": "mine"})
    tags = []
    for i, item in enumerate(FIXTURE):
        tags.append({"review_id": item["reviewId"], "sentiment": -0.7, "emotion": "frustration",
                     "intensity": 2, "aspects": ["performance_effectiveness", "battery_power"] if i % 2 else ["ease_of_use"],
                     "topic": "location not updating", "pain_point": "location not updating" if i < 3 else "battery door",
                     "praise": "", "use_case": "keys", "persona": "", "purchase_driver": "apple ecosystem"})
    db.save_tags(conn, "US", tags, model="test")
    return conn


def test_untagged_query_excludes_tagged(tmp_path):
    conn = _seed(tmp_path)
    assert len(analyst.untagged_reviews(conn, "US", ["B0CWXNS552"])) == 0


def test_build_brief_contains_weighted_evidence(tmp_path):
    conn = _seed(tmp_path)
    brief = analyst.build_brief(conn, "US", "B0CWXNS552", ["B0MISSING01"])
    assert len(brief["products"]) == 1  # missing competitor skipped, not crashing
    p = brief["products"][0]
    assert p["role"] == "MY PRODUCT"
    assert p["currency"] == "USD"
    assert p["sample"]["reviews_collected"] == 5
    assert p["top_pain_points"][0] == {"text": "location not updating", "count": 3}
    # only 1-star sampled, so every negative aspect estimate is over the 1-star bucket
    aspects = {a["aspect"]: a for a in p["weighted_negative_aspects_pct_of_all_customers"]}
    assert aspects["ease_of_use"]["weighted_pct"] == 60.0
    json.dumps(brief, default=str)  # must be serializable for the prompt


def test_excel_export_has_all_sheets(tmp_path):
    import io
    import pandas as pd
    conn = _seed(tmp_path)
    xl = pd.ExcelFile(io.BytesIO(to_excel(conn, "US")))
    assert {"Summary", "Reviews", "Customer Photos", "Coverage", "Neg Aspects (weighted)"} <= set(xl.sheet_names)
    assert len(xl.parse("Reviews")) == 5


def test_ai_cost_estimate_positive():
    assert 0 < analyst.estimate_ai_cost(1000, 5, "claude-opus-5") < 20


def test_ai_cost_estimate_zero_when_nothing_to_do():
    assert analyst.estimate_ai_cost(0, 0, "claude-opus-5") == 0
