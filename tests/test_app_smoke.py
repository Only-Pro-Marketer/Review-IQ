"""Render the real Streamlit app against a seeded database and fail on any exception."""
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from review_intel import db
from review_intel.parse import parse_items

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "apify_reviews_sample.json").read_text())
APP = str(Path(__file__).parent.parent / "app.py")


def test_app_renders_empty_db(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEW_DB", str(tmp_path / "empty.db"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception


def test_app_renders_all_tabs_with_data(tmp_path, monkeypatch):
    path = tmp_path / "seed.db"
    conn = db.connect(path)
    items = json.loads(json.dumps(FIXTURE))
    items[0]["reviewImages"] = ["https://m.media-amazon.com/images/I/71rP7f78eFL._AC_SL1500_.jpg"]
    db.save_parsed(conn, parse_items(items, "US"), "US", roles={"B0CWXNS552": "mine"})
    db.save_tags(conn, "US", [{"review_id": i["reviewId"], "sentiment": -0.5, "emotion": "frustration",
                               "intensity": 2, "aspects": ["battery_power"], "topic": "t", "pain_point": "battery",
                               "praise": "", "use_case": "keys", "persona": "", "purchase_driver": ""}
                              for i in FIXTURE], model="test")
    db.save_analysis(conn, created_at=db.now(), marketplace="US", my_asin="B0CWXNS552", competitor_asins="[]",
                     model="test", report_md="## Executive Summary\n- test",
                     insights_json=json.dumps({"action_items": [{"priority": 1, "area": "product", "action": "a",
                                                                 "evidence": "e", "expected_impact": "high",
                                                                 "effort": "low"}]}),
                     input_tokens=1, output_tokens=1, cost_usd=0.0)
    from review_intel.datadive import parse_competitors, parse_keywords, unwrap
    fx = Path(__file__).parent / "fixtures"
    niche_kw = json.loads((fx / "datadive_keywords_sample.json").read_text())
    niche_kw["data"]["keywords"][0]["asinRanks"]["B0CWXNS552"] = 15   # pretend we rank for the top keyword
    db.save_niche(conn, "US", {"nicheId": "N1", "heroKeyword": "demo spray"}, parse_keywords(unwrap(niche_kw)),
                  parse_competitors(unwrap(json.loads((fx / "datadive_competitors_sample.json").read_text()))))
    monkeypatch.setenv("REVIEW_DB", str(path))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    nav = at.sidebar.radio(key="page")
    seen = {}
    for page in nav.options:
        at = nav.set_value(page).run()
        assert not at.exception, (page, at.exception)
        seen[page] = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
        nav = at.sidebar.radio(key="page")
    assert len(seen) == 9
    assert "YOUR PRODUCT" in seen[nav.options[0]]           # overview product card
    assert "reviews shown" in seen[nav.options[3]]          # reviews page
    assert "Took me in circles" in seen[nav.options[3]]     # review card rendered
    assert "ri-photo" in seen[nav.options[4]]               # photo wall
    assert "Your share of voice" not in seen[nav.options[6]]  # metrics aren't markdown; checked below
    assert "Evidence" in seen[nav.options[7]]               # action plan cards


def test_collect_page_has_no_stray_output(tmp_path, monkeypatch):
    """Streamlit 'magic' prints bare expressions; make sure the ASIN parser doesn't leak 'None' onto the page."""
    monkeypatch.setenv("REVIEW_DB", str(tmp_path / "empty.db"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = at.sidebar.radio(key="page").set_value(at.sidebar.radio(key="page").options[1]).run()
    at.text_area[0].set_value("B0D1XD1ZV3\nB0CZ9ZLWM4\nnot-an-asin").run()
    assert not at.exception
    assert not [m.value for m in at.markdown if m.value.strip("`").strip() == "None"]
    assert "B0D1XD1ZV3" in " ".join(m.value for m in at.markdown)


def test_keywords_page_shows_battlefield(tmp_path, monkeypatch):
    from review_intel.datadive import parse_competitors, parse_keywords, unwrap
    fx = Path(__file__).parent / "fixtures"
    path = tmp_path / "kw.db"
    conn = db.connect(path)
    db.save_parsed(conn, parse_items(FIXTURE, "US"), "US", roles={"B0CWXNS552": "mine"})
    niche_kw = json.loads((fx / "datadive_keywords_sample.json").read_text())
    niche_kw["data"]["keywords"][0]["asinRanks"]["B0CWXNS552"] = 15
    db.save_niche(conn, "US", {"nicheId": "N1", "heroKeyword": "demo spray"}, parse_keywords(unwrap(niche_kw)),
                  parse_competitors(unwrap(json.loads((fx / "datadive_competitors_sample.json").read_text()))))
    monkeypatch.setenv("REVIEW_DB", str(path))
    at = AppTest.from_file(APP, default_timeout=60).run()
    nav = at.sidebar.radio(key="page")
    at = nav.set_value([o for o in nav.options if "Keywords" in o][0]).run()
    assert not at.exception, at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Keywords"] == "179"
    assert labels["Your top-10 keywords"] == "0"
    assert float(labels["Your share of voice"].rstrip("%")) > 0     # rank 15 on the 71k keyword
    assert any("isn't tracked" in w.value for w in at.warning) is False
