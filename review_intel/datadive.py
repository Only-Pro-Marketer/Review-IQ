"""DataDive API client: niche keyword lists (search volume + every competitor's organic rank)
and competitor business metrics. Docs: https://developer.datadive.tools/docs#/v1"""
from __future__ import annotations

import time

import requests

API = "https://api.datadive.tools"
# our marketplace code -> DataDive marketplace
DD_MARKETPLACE = {"US": "com", "CA": "ca", "UK": "co.uk", "MX": "com.mx", "IN": "in", "FR": "fr",
                  "DE": "de", "ES": "es", "IT": "it", "JP": "co.jp"}
_PAGINATION_KEYS = ("currentPage", "lastPage", "hasNext", "hasPrev", "pageSize", "total")


class DataDiveError(RuntimeError):
    pass


def unwrap(body):
    """Strip DataDive's {"data": ...} envelope, but keep paginated bodies whole."""
    if not isinstance(body, dict) or "data" not in body:
        return body
    if any(k in body for k in _PAGINATION_KEYS):
        return body
    return body["data"]


class DataDiveClient:
    def __init__(self, api_key: str, session=None, sleep=time.sleep, max_retries: int = 4):
        self.key = api_key
        self.session = session or requests.Session()
        self.sleep = sleep
        self.max_retries = max_retries

    def _req(self, method: str, path: str, **kw):
        kw.setdefault("timeout", 60)
        kw["headers"] = {"x-api-key": self.key, "accept": "application/json"}
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, f"{API}{path}", **kw)
            except requests.RequestException as e:
                last = str(e)
                self.sleep(min(2 ** attempt, 20))
                continue
            if resp.status_code in (401, 403):
                raise DataDiveError("DataDive rejected the API key (check DATADIVE_API_KEY in .env; "
                                    "API access needs a Standard or Enterprise plan).")
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                self.sleep(min(2 ** attempt, 20))
                continue
            if resp.status_code >= 400:
                raise DataDiveError(f"DataDive error {resp.status_code}: {resp.text[:300]}")
            return unwrap(resp.json())
        raise DataDiveError(f"DataDive kept failing after {self.max_retries} retries ({last}).")

    def list_niches(self) -> list[dict]:
        out, page = [], 1
        while True:
            body = self._req("GET", "/v1/niches", params={"currentPage": page, "pageSize": 50})
            out.extend(body.get("data") or [])
            if not body.get("hasNext"):
                return out
            page += 1

    def keywords(self, niche_id: str) -> dict:
        return self._req("GET", f"/v1/niches/{niche_id}/keywords")

    def competitors(self, niche_id: str) -> dict:
        return self._req("GET", f"/v1/niches/{niche_id}/competitors")

    def create_dive(self, marketplace: str, asin: str, n_competitors: int) -> dict:
        return self._req("POST", "/v1/niches/dives",
                         json={"marketplace": DD_MARKETPLACE[marketplace], "asin": asin,
                               "numberOfCompetitors": n_competitors})

    def dive_status(self, dive_id: str) -> dict:
        return self._req("GET", f"/v1/niches/dives/{dive_id}")

    def wait_for_dive(self, dive_id: str, poll_secs: float = 15, timeout_secs: int = 3600, on_poll=None) -> str:
        """Poll until the dive finishes; return the new nicheId."""
        start = time.time()
        while True:
            st = self.dive_status(dive_id)
            if on_poll:
                on_poll(st)
            if st.get("status") == "success":
                return st["nicheId"]
            if st.get("status") == "error":
                raise DataDiveError(f"DataDive dive failed: {st.get('error')}")
            if time.time() - start > timeout_secs:
                raise DataDiveError("DataDive dive is still running - check again later.")
            self.sleep(poll_secs)


def best_niche_for(client: DataDiveClient, marketplace: str, asins: list[str]) -> list[dict]:
    """Rank this marketplace's niches by how many of our ASINs they already track (my ASIN first)."""
    dd_mp = DD_MARKETPLACE.get(marketplace)
    out = []
    for n in client.list_niches():
        if n.get("marketplace") != dd_mp:
            continue
        comps = {c.get("asin") for c in (client.competitors(n["nicheId"]).get("competitors") or [])}
        out.append({**n, "overlap": [a for a in asins if a in comps], "has_mine": bool(asins) and asins[0] in comps,
                    "n_competitors": len(comps)})
    return sorted(out, key=lambda n: (n["has_mine"], len(n["overlap"]), n.get("latestResearchDate") or ""),
                  reverse=True)


def _num(x, default=None):
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def parse_keywords(payload: dict) -> list[dict]:
    rows = []
    for k in payload.get("keywords") or []:
        rel = k.get("relevancy")
        outlier = isinstance(rel, str) and rel.lower() == "outlier"
        bid = k.get("suggestedBid") or {}
        cents = lambda v: round(v / 100, 2) if v is not None else None  # noqa: E731 - bids come in cents
        rows.append({
            "keyword": k.get("keyword"),
            "search_volume": int(k.get("searchVolume") or 0),
            "relevancy": 1.0 if outlier else _num(rel, 0.0),
            "is_outlier": outlier,
            "bid_min": cents(bid.get("min")), "bid_median": cents(bid.get("median")), "bid_max": cents(bid.get("max")),
            "ranks": {a: (int(r) if r is not None else None) for a, r in (k.get("asinRanks") or {}).items()},
        })
    return rows


def parse_competitors(payload: dict) -> list[dict]:
    rows = []
    for c in payload.get("competitors") or []:
        pct = lambda v: round(100 * v, 1) if v is not None else None  # noqa: E731
        rows.append({
            "asin": c.get("asin"), "brand": c.get("brand"), "title": c.get("title"), "image": c.get("imageUrl"),
            "bsr": c.get("bsr"), "price": c.get("price"), "rating": c.get("rating"),
            "review_count": c.get("reviewCount"), "sales": c.get("sales"), "revenue": c.get("revenue"),
            "kw_page1": c.get("kwRankedOnP1"), "pct_sv_page1": pct(c.get("svRankedOnP1Percent")),
            "advertised_kws": c.get("advertisedKws"), "pct_sv_top_ads": pct(c.get("tosSvAdsPercent")),
            "ranking_juice": (c.get("listingRankingJuice") or {}).get("value"),
            "fulfillment": c.get("fulfillment"), "variations": c.get("numberOfVariations"),
            "category": c.get("category"),
        })
    return rows
