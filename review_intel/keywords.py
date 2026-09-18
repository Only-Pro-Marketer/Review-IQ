"""Search-keyword battlefield + customer win/lose math.

Everything here is deterministic so the AI report quotes numbers instead of inventing them.
Traffic figures use an estimated click-through curve by organic position and are labelled
"estimated" wherever they are shown.
"""
from __future__ import annotations

import pandas as pd

# Estimated share of a search's clicks by organic position (industry-typical Amazon curve).
_CTR = {1: 0.30, 2: 0.18, 3: 0.12, 4: 0.08, 5: 0.06, 6: 0.045, 7: 0.035, 8: 0.03, 9: 0.025, 10: 0.02}
TOP = 10        # "page 1, above the fold" threshold for winning a keyword
WEAK = 20       # beyond this we treat ourselves as effectively absent


def rank_ctr(rank) -> float:
    if rank is None or pd.isna(rank) or rank < 1:
        return 0.0
    r = int(rank)
    if r in _CTR:
        return _CTR[r]
    if r <= 20:
        return 0.01
    if r <= 48:
        return 0.003
    return 0.0


def est_clicks(volume, rank) -> float:
    return float(volume or 0) * rank_ctr(rank)


def _wide(ranks: pd.DataFrame) -> pd.DataFrame:
    if ranks.empty:
        return pd.DataFrame()
    return ranks.pivot_table(index="keyword", columns="asin", values="organic_rank", aggfunc="min")


def classify_keywords(kws: pd.DataFrame, ranks: pd.DataFrame, my_asin: str, competitors: list[str]) -> pd.DataFrame:
    """One row per keyword: my rank, best competitor + rank, and a status:
    win (I'm top-10 and ahead), contested (top-10 but a competitor is ahead),
    striking (I rank 11-20: close to page-1 top spots, cheapest to win),
    gap (a competitor is top-10, I'm beyond 20 or unranked), open (nobody in the set is top-10)."""
    wide = _wide(ranks)
    rows = []
    for k in kws.itertuples():
        r = wide.loc[k.keyword] if k.keyword in wide.index else pd.Series(dtype=float)
        mine = r.get(my_asin) if my_asin in r.index else None
        mine = None if mine is None or pd.isna(mine) else int(mine)
        comp = {c: int(r[c]) for c in competitors if c in r.index and pd.notna(r[c])}
        best_c, best_r = (min(comp.items(), key=lambda kv: kv[1]) if comp else (None, None))
        if mine is not None and mine <= TOP and (best_r is None or mine <= best_r):
            status = "win"
        elif mine is not None and mine <= TOP:
            status = "contested"
        elif mine is not None and mine <= WEAK:
            status = "striking"
        elif best_r is not None and best_r <= TOP:
            status = "gap"
        else:
            status = "open"
        rows.append({"keyword": k.keyword, "search_volume": k.search_volume, "relevancy": k.relevancy,
                     "bid_median": getattr(k, "bid_median", None), "my_rank": mine,
                     "best_competitor": best_c, "best_competitor_rank": best_r,
                     "competitors_top10": sum(1 for v in comp.values() if v <= TOP), "status": status})
    out = pd.DataFrame(rows)
    if len(out):
        out["my_rank"] = pd.to_numeric(out["my_rank"])
        out = out.sort_values("search_volume", ascending=False).reset_index(drop=True)
    return out


def share_of_voice(kws: pd.DataFrame, ranks: pd.DataFrame, asins: list[str]) -> pd.DataFrame:
    """Estimated share of all search clicks across the keyword set, per ASIN."""
    total = float(kws.search_volume.sum()) if len(kws) else 0.0
    vol = dict(zip(kws.keyword, kws.search_volume)) if len(kws) else {}
    rows = []
    for a in asins:
        mine = ranks[ranks.asin == a] if len(ranks) else ranks
        clicks = sum(est_clicks(vol.get(r.keyword, 0), r.organic_rank) for r in mine.itertuples())
        top10 = mine[(mine.organic_rank.notna()) & (mine.organic_rank <= TOP)] if len(mine) else mine
        rows.append({"asin": a,
                     "sov_pct": round(100 * clicks / total, 2) if total else None,
                     "est_monthly_clicks": round(clicks),
                     "keywords_ranked": int(mine.organic_rank.notna().sum()) if len(mine) else 0,
                     "keywords_top10": len(top10),
                     "volume_top10": int(sum(vol.get(k, 0) for k in top10.keyword)) if len(top10) else 0})
    return pd.DataFrame(rows)


def competitor_winning_keywords(kws: pd.DataFrame, ranks: pd.DataFrame, my_asin: str, competitor: str,
                                limit: int = 25) -> pd.DataFrame:
    """Keywords where this competitor is top-10 and ranks better than me, biggest first."""
    wide = _wide(ranks)
    rows = []
    for k in kws.itertuples():
        if k.keyword not in wide.index or competitor not in wide.columns:
            continue
        c = wide.loc[k.keyword, competitor]
        m = wide.loc[k.keyword, my_asin] if my_asin in wide.columns else None
        if pd.notna(c) and c <= TOP and (m is None or pd.isna(m) or c < m):
            rows.append({"keyword": k.keyword, "search_volume": k.search_volume,
                         "competitor_rank": int(c), "my_rank": None if m is None or pd.isna(m) else int(m)})
    out = pd.DataFrame(rows, columns=["keyword", "search_volume", "competitor_rank", "my_rank"])
    return out.sort_values("search_volume", ascending=False).head(limit).reset_index(drop=True)


def review_win_lose(neg: pd.DataFrame, pos: pd.DataFrame, my_asin: str, competitors: list[str],
                    min_gap: float = 3.0) -> pd.DataFrame:
    """Per product aspect: my complaint/praise rate vs the competitor average (weighted % of all
    customers). score = (competitor complaints - mine) + (my praise - competitor praise)."""
    def lookup(frame, asin, aspect):
        if frame is None or frame.empty:
            return 0.0
        m = frame[(frame.asin == asin) & (frame.aspect == aspect)]
        return float(m.weighted_pct.iloc[0]) if len(m) and pd.notna(m.weighted_pct.iloc[0]) else 0.0

    aspects = sorted(set(neg.aspect if len(neg) else []) | set(pos.aspect if len(pos) else []))
    tagged = set(neg.asin if len(neg) else []) | set(pos.asin if len(pos) else [])
    comps = [c for c in competitors if c in tagged]
    rows = []
    for a in aspects:
        my_n, my_p = lookup(neg, my_asin, a), lookup(pos, my_asin, a)
        c_n = sum(lookup(neg, c, a) for c in comps) / len(comps) if comps else 0.0
        c_p = sum(lookup(pos, c, a) for c in comps) / len(comps) if comps else 0.0
        best = max(comps, key=lambda c: lookup(pos, c, a) - lookup(neg, c, a)) if comps else None
        score = (c_n - my_n) + (my_p - c_p)
        rows.append({"aspect": a, "my_complaint_pct": round(my_n, 1), "competitor_complaint_pct": round(c_n, 1),
                     "my_praise_pct": round(my_p, 1), "competitor_praise_pct": round(c_p, 1),
                     "score": round(score, 1), "best_competitor_on_aspect": best,
                     "verdict": "WIN" if score >= min_gap else ("LOSE" if score <= -min_gap else "PARITY")})
    out = pd.DataFrame(rows)
    return out.sort_values("score").reset_index(drop=True) if len(out) else out


def keyword_battlefield(conn, niche_id: str, my_asin: str, competitors: list[str], top_n: int = 60) -> dict:
    """Everything the strategist needs about search keywords, computed (not guessed)."""
    from . import db
    kws, ranks = db.keywords_df(conn, niche_id), db.keyword_ranks_df(conn, niche_id)
    niche = db.df(conn, "select * from kw_niches where niche_id=?", (niche_id,))
    dd = db.dd_competitors_df(conn, niche_id)
    tracked = set(ranks.asin)
    comps_in = [c for c in competitors if c in tracked]
    # competitors DataDive tracks that the user did not pick still matter: include them
    others = [a for a in dd.asin if a != my_asin and a not in comps_in]
    rivals = comps_in + others
    cls = classify_keywords(kws, ranks, my_asin, rivals)
    sov = share_of_voice(kws, ranks, [my_asin] + rivals)
    brand = dict(zip(dd.asin, dd.brand))
    sov["brand"] = sov.asin.map(brand)

    def rows(status, n):
        d = cls[cls.status == status].head(n)
        return d[["keyword", "search_volume", "my_rank", "best_competitor", "best_competitor_rank",
                  "bid_median"]].to_dict("records")

    per_comp = {}
    for c in rivals[:10]:
        w = competitor_winning_keywords(kws, ranks, my_asin, c, limit=12)
        per_comp[f"{c} ({brand.get(c, '')})"] = w.to_dict("records")
    return {
        "source": "DataDive",
        "niche": niche.iloc[0][["hero_keyword", "research_date"]].to_dict() if len(niche) else {},
        "my_asin_tracked_by_datadive": my_asin in tracked,
        "your_competitors_missing_from_datadive": [c for c in competitors if c not in tracked],
        "note": ("Ranks are organic positions. est_monthly_clicks and sov_pct use an estimated "
                 "click-through curve by position - treat as directional. Bids are DataDive suggested CPC "
                 "in the marketplace currency."),
        "keyword_count": len(kws), "total_search_volume": int(kws.search_volume.sum()) if len(kws) else 0,
        "share_of_voice": sov.sort_values("sov_pct", ascending=False).to_dict("records"),
        "status_summary": cls.groupby("status").agg(keywords=("keyword", "count"),
                                                    search_volume=("search_volume", "sum")).reset_index()
        .to_dict("records") if len(cls) else [],
        "we_win": rows("win", 20), "contested": rows("contested", 15), "striking_distance": rows("striking", 20),
        "gaps_competitors_win": rows("gap", 25), "open_white_space": rows("open", 15),
        "top_keywords_rank_matrix": _matrix_rows(kws, ranks, [my_asin] + rivals, top_n),
        "competitor_winning_keywords": per_comp,
        "competitor_business_metrics": dd.drop(columns=["niche_id", "image", "title"], errors="ignore")
        .to_dict("records"),
    }


def _matrix_rows(kws, ranks, asins, n):
    wide = _wide(ranks)
    out = []
    for k in kws.head(n).itertuples():
        r = wide.loc[k.keyword] if k.keyword in wide.index else pd.Series(dtype=float)
        out.append({"keyword": k.keyword, "sv": int(k.search_volume),
                    "ranks": {a: (int(r[a]) if a in r.index and pd.notna(r[a]) else None) for a in asins}})
    return out
