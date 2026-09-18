import json
from pathlib import Path

import pandas as pd
import pytest

from review_intel import db
from review_intel.datadive import (DataDiveClient, DataDiveError, parse_competitors, parse_keywords,
                                   unwrap)
from review_intel.keywords import (classify_keywords, est_clicks, rank_ctr, review_win_lose,
                                   share_of_voice, competitor_winning_keywords)

FX = Path(__file__).parent / "fixtures"
KW = json.loads((FX / "datadive_keywords_sample.json").read_text())
COMP = json.loads((FX / "datadive_competitors_sample.json").read_text())
MINE = "B0DEMO0008"  # Brand B, the smallest brand in the demo niche


# ---------- parsing ----------

def test_unwrap_envelope_and_pagination():
    assert unwrap({"data": {"a": 1}}) == {"a": 1}
    page = {"data": [1], "currentPage": 1, "hasNext": False}
    assert unwrap(page) is page
    assert unwrap([1, 2]) == [1, 2]


def test_parse_keywords_real():
    rows = parse_keywords(unwrap(KW))
    assert len(rows) == 179
    top = rows[0]
    assert top["keyword"] == "demo spray"
    assert top["search_volume"] == 52162
    assert top["relevancy"] == 1.0 and top["is_outlier"] is False
    assert top["bid_median"] == pytest.approx(0.63)  # DataDive bids are in cents
    assert top["ranks"]["B0DEMO0002"] == 1
    assert top["ranks"]["B0DEMO0005"] == 24


def test_parse_keywords_outlier_and_missing_bid():
    rows = parse_keywords({"keywords": [
        {"keyword": "x", "searchVolume": 10, "relevancy": "Outlier", "asinRanks": {"A": None}},
        {"keyword": "y", "searchVolume": None, "relevancy": 0.5, "asinRanks": {}, "suggestedBid": None}]})
    assert rows[0]["is_outlier"] is True and rows[0]["relevancy"] == 1.0
    assert rows[0]["ranks"] == {"A": None}
    assert rows[1]["search_volume"] == 0 and rows[1]["bid_median"] is None


def test_parse_competitors_real():
    rows = parse_competitors(unwrap(COMP))
    assert [r["asin"] for r in rows] == ["B0DEMO0002", "B0DEMO0003", "B0DEMO0008"]
    r = rows[1]
    assert r["brand"] == "Brand A" and r["sales"] == 3256 and r["revenue"] == 91065
    assert r["ranking_juice"] == 282545
    assert r["pct_sv_page1"] == pytest.approx(90.3, abs=0.05)
    assert r["pct_sv_top_ads"] == pytest.approx(48.5, abs=0.05)


# ---------- client ----------

class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p, self.text, self.headers = status, payload, str(payload), {}

    def json(self):
        return self._p


class _Sess:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


def test_client_sends_api_key_and_paginates_niches():
    s = _Sess([_Resp(200, {"data": [{"nicheId": "a"}], "currentPage": 1, "hasNext": True}),
               _Resp(200, {"data": [{"nicheId": "b"}], "currentPage": 2, "hasNext": False})])
    c = DataDiveClient("KEY", session=s, sleep=lambda x: None)
    assert [n["nicheId"] for n in c.list_niches()] == ["a", "b"]
    assert s.calls[0][2]["headers"]["x-api-key"] == "KEY"
    assert s.calls[1][2]["params"]["currentPage"] == 2


def test_client_bad_key_clear_error():
    c = DataDiveClient("KEY", session=_Sess([_Resp(401, {"message": "nope"})]), sleep=lambda x: None)
    with pytest.raises(DataDiveError, match="API key"):
        c.list_niches()


def test_client_retries_429():
    s = _Sess([_Resp(429, {}), _Resp(200, {"data": {"keywords": [], "latestResearchDate": "x"}})])
    c = DataDiveClient("KEY", session=s, sleep=lambda x: None)
    assert c.keywords("n1") == {"keywords": [], "latestResearchDate": "x"}


# ---------- storage ----------

def test_save_niche_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    kws, comps = parse_keywords(unwrap(KW)), parse_competitors(unwrap(COMP))
    for _ in range(2):
        db.save_niche(conn, "CA", {"nicheId": "N-DEMO", "heroKeyword": "demo spray", "nicheLabel": "demo spray",
                                   "latestResearchDate": "2026-09-18"}, kws, comps)
    assert conn.execute("select count(*) from keywords").fetchone()[0] == 179
    assert conn.execute("select count(*) from keyword_ranks").fetchone()[0] == 179 * 8
    assert conn.execute("select count(*) from dd_competitors").fetchone()[0] == 3
    k = db.keywords_df(conn, "N-DEMO")
    assert set(k.columns) >= {"keyword", "search_volume", "relevancy", "bid_median"}
    r = db.keyword_ranks_df(conn, "N-DEMO")
    assert r[(r.keyword == "demo spray") & (r.asin == "B0DEMO0002")].organic_rank.iloc[0] == 1


# ---------- keyword strategy math ----------

def test_rank_ctr_curve_monotonic_and_zero_when_unranked():
    vals = [rank_ctr(r) for r in [1, 2, 3, 5, 10, 15, 30, 60]]
    assert vals == sorted(vals, reverse=True)
    assert rank_ctr(None) == 0 and rank_ctr(0) == 0 and rank_ctr(200) == 0
    assert est_clicks(1000, 1) == pytest.approx(1000 * rank_ctr(1))


def _matrix():
    kws = pd.DataFrame({"keyword": ["a", "b", "c", "d", "e", "f"], "search_volume": [1000, 500, 300, 200, 100, 50],
                        "relevancy": [1, 1, 0.5, 1, 0.375, 1], "bid_median": [1.0, 0.5, None, 0.2, 0.1, 0.3]})
    ranks = pd.DataFrame([
        ("a", "ME", 2), ("a", "C1", 1),          # contested: we're top10 but C1 better
        ("b", "ME", 1), ("b", "C1", 6),          # win
        ("c", "ME", None), ("c", "C1", 3),       # lose / gap
        ("d", "ME", 35), ("d", "C1", 40),        # open: nobody top 10
        ("e", "ME", 4), ("e", "C1", None),       # win (competitor absent)
        ("f", "ME", 14), ("f", "C1", 2),         # striking distance
    ], columns=["keyword", "asin", "organic_rank"])
    return kws, ranks


def test_classify_keywords():
    kws, ranks = _matrix()
    c = classify_keywords(kws, ranks, "ME", ["C1"]).set_index("keyword")
    assert c.loc["a", "status"] == "contested"
    assert c.loc["b", "status"] == "win"
    assert c.loc["c", "status"] == "gap"
    assert c.loc["d", "status"] == "open"
    assert c.loc["e", "status"] == "win"
    assert c.loc["f", "status"] == "striking"
    assert c.loc["c", "best_competitor"] == "C1" and c.loc["c", "best_competitor_rank"] == 3
    assert pd.isna(c.loc["c", "my_rank"])


def test_share_of_voice_sums_estimated_clicks_over_total_volume():
    kws, ranks = _matrix()
    sov = share_of_voice(kws, ranks, ["ME", "C1"]).set_index("asin")
    total = kws.search_volume.sum()
    me = sum(est_clicks(v, r) for v, r in [(1000, 2), (500, 1), (200, 35), (100, 4), (50, 14)])
    assert sov.loc["ME", "sov_pct"] == pytest.approx(100 * me / total, abs=0.01)
    assert sov.loc["ME", "keywords_top10"] == 3
    assert sov.loc["C1", "keywords_top10"] == 4


def test_share_of_voice_empty_is_safe():
    sov = share_of_voice(pd.DataFrame(columns=["keyword", "search_volume"]),
                         pd.DataFrame(columns=["keyword", "asin", "organic_rank"]), ["ME"])
    assert sov.loc[0, "sov_pct"] is None


def test_competitor_winning_keywords():
    kws, ranks = _matrix()
    w = competitor_winning_keywords(kws, ranks, "ME", "C1")
    assert list(w.keyword) == ["a", "c", "f"]  # C1 top-10 and better than us, sorted by volume


# ---------- customer win/lose from reviews ----------

def test_review_win_lose():
    neg = pd.DataFrame([("ME", "battery_power", 2.0), ("C1", "battery_power", 10.0),
                        ("ME", "ease_of_use", 12.0), ("C1", "ease_of_use", 3.0)],
                       columns=["asin", "aspect", "weighted_pct"])
    pos = pd.DataFrame([("ME", "value_price", 40.0), ("C1", "value_price", 20.0)],
                       columns=["asin", "aspect", "weighted_pct"])
    t = review_win_lose(neg, pos, "ME", ["C1"], min_gap=3).set_index("aspect")
    assert t.loc["battery_power", "verdict"] == "WIN"      # we get fewer complaints
    assert t.loc["ease_of_use", "verdict"] == "LOSE"       # we get more complaints
    assert t.loc["value_price", "verdict"] == "WIN"        # we get more praise
    assert t.loc["battery_power", "my_complaint_pct"] == 2.0
    assert t.loc["battery_power", "competitor_complaint_pct"] == 10.0


# ---------- report evidence ----------

def _seed_niche(conn):
    db.save_niche(conn, "CA", {"nicheId": "N1", "heroKeyword": "demo spray",
                               "latestResearchDate": "2026-09-18"},
                  parse_keywords(unwrap(KW)), parse_competitors(unwrap(COMP)))


def test_keyword_battlefield_real_niche(tmp_path):
    from review_intel.keywords import keyword_battlefield
    conn = db.connect(tmp_path / "t.db")
    _seed_niche(conn)
    b = keyword_battlefield(conn, "N1", MINE, ["B0DEMO0002", "B0NOTINDD1"])
    assert b["my_asin_tracked_by_datadive"] is True
    assert b["your_competitors_missing_from_datadive"] == ["B0NOTINDD1"]
    assert b["keyword_count"] == 179
    sov = {r["asin"]: r for r in b["share_of_voice"]}
    assert sov["B0DEMO0002"]["sov_pct"] > sov[MINE]["sov_pct"]      # Brand A dominates the niche
    assert sov["B0DEMO0002"]["brand"] == "Brand A"
    assert b["gaps_competitors_win"][0]["search_volume"] >= b["gaps_competitors_win"][-1]["search_volume"]
    assert any("Brand A" in k for k in b["competitor_winning_keywords"])
    assert b["top_keywords_rank_matrix"][0]["ranks"]["B0DEMO0002"] == 1
    json.dumps(b, default=str)


def test_brief_includes_keywords_and_win_lose(tmp_path):
    from review_intel import analyst
    from review_intel.parse import parse_items
    conn = db.connect(tmp_path / "t.db")
    _seed_niche(conn)
    reviews = json.loads((FX / "apify_reviews_sample.json").read_text())
    db.save_parsed(conn, parse_items(reviews, "US"), "US", roles={"B0CWXNS552": "mine"})
    b = analyst.build_brief(conn, "US", "B0CWXNS552", [], niche_id="N1")
    assert b["search_keywords"]["my_asin_tracked_by_datadive"] is False   # AirTag isn't in the demo niche
    assert b["customer_win_lose"] == "not available (tag reviews first)"
    assert analyst.build_brief(conn, "US", "B0CWXNS552", [])["search_keywords"] == "not provided"
