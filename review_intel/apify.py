"""Thin Apify REST client + the collection job (one actor run per star level)."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import PRICE_PER_REVIEW_USD, REVIEWS_ACTOR, product_url

API = "https://api.apify.com/v2"
STAR_KEYS = ["fiveStar", "fourStar", "threeStar", "twoStar", "oneStar"]
REVIEWS_PER_KEYWORD = 100  # max per keyword view (measured 14-98 on a real listing)
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyError(RuntimeError):
    pass


class ApifyClient:
    def __init__(self, token: str, session=None, sleep=time.sleep, max_retries: int = 5):
        self.token = token
        self.session = session or requests.Session()
        self.sleep = sleep
        self.max_retries = max_retries

    def _req(self, method: str, path: str, **kw):
        kw.setdefault("timeout", 60)
        kw.setdefault("headers", {})["Authorization"] = f"Bearer {self.token}"
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, f"{API}{path}", **kw)
            except requests.RequestException as e:  # network blip
                last = str(e)
                self.sleep(min(2 ** attempt, 30))
                continue
            if resp.status_code in (401, 403):
                raise ApifyError("Apify rejected the token (check APIFY_TOKEN in .env).")
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                wait = resp.headers.get("Retry-After")
                self.sleep(float(wait) if wait else min(2 ** attempt, 30))
                continue
            if resp.status_code >= 400:
                raise ApifyError(f"Apify error {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise ApifyError(f"Apify kept failing after {self.max_retries} retries ({last}).")

    def start_run(self, actor: str, run_input: dict, max_charge_usd: float | None = None) -> dict:
        params = {"maxTotalChargeUsd": round(max_charge_usd, 2)} if max_charge_usd else {}
        return self._req("POST", f"/acts/{actor}/runs", json=run_input, params=params)["data"]

    def get_run(self, run_id: str) -> dict:
        return self._req("GET", f"/actor-runs/{run_id}")["data"]

    def wait_for_run(self, run_id: str, poll_secs: float = 5, timeout_secs: int = 3600, on_poll=None) -> dict:
        start = time.time()
        while True:
            run = self.get_run(run_id)
            if on_poll:
                on_poll(run)
            status = run.get("status")
            if status == "SUCCEEDED":
                return run
            if status in TERMINAL:
                raise ApifyError(f"Apify run {run_id} {status}: {run.get('statusMessage', '')}")
            if time.time() - start > timeout_secs:
                raise ApifyError(f"Apify run {run_id} still {status} after {timeout_secs}s")
            self.sleep(poll_secs)

    def dataset_items(self, dataset_id: str, page_size: int = 1000) -> list[dict]:
        items, offset = [], 0
        while True:
            page = self._req("GET", f"/datasets/{dataset_id}/items",
                             params={"offset": offset, "limit": page_size, "format": "json"})
            items.extend(page)
            if len(page) < page_size:
                return items
            offset += page_size


STAR_LABEL = {"fiveStar": 5, "fourStar": 4, "threeStar": 3, "twoStar": 2, "oneStar": 1}


def build_runs(asins: list[str], marketplace: str, per_star: int, sorts: list[str], keywords: list[str],
               keyword_stars: list[int] | None = None) -> list[tuple[str, dict]]:
    """Plan the actor runs as (label, input) pairs.

    Amazon shows at most ~100 reviews per filter view, so we request several views and merge:
    one run per star level and sort order ("recent", "helpful"), plus an optional keyword run
    per chosen star (each keyword is its own view of up to ~100). The actor's maxReviews is a
    per-product total, hence one run per view instead of one big run."""
    urls = [{"url": product_url(a, marketplace)} for a in asins]
    keyword_stars = [5, 4, 3, 2, 1] if keyword_stars is None else keyword_stars
    runs, first = [], True
    common = {"productUrls": urls, "reviewsAlwaysSaveCategoryData": True,
              "deduplicateRedirectedAsins": True, "includeGdprSensitive": False}
    for key in STAR_KEYS:
        star = STAR_LABEL[key]
        for sort in sorts:
            runs.append((f"{star}★ {sort}", {**common, "filterByRatings": [key], "sort": sort,
                                             "maxReviews": per_star, "scrapeProductDetails": first}))
            first = False
        if keywords and star in keyword_stars:
            runs.append((f"{star}★ keywords", {**common, "filterByRatings": [key], "sort": sorts[0],
                                               "maxReviews": len(keywords) * REVIEWS_PER_KEYWORD,
                                               "reviewsFilterByKeywords": keywords,
                                               "scrapeProductDetails": False}))
    return runs


def estimate_cost(n_asins: int, per_star: int, n_sorts: int, n_keywords: int, n_keyword_stars: int) -> float:
    """Upper bound in USD. Real cost is lower: many products have fewer reviews, and keyword
    searches often return fewer than 100 (measured 14-98 per keyword)."""
    per_product = 5 * per_star * n_sorts + n_keywords * n_keyword_stars * REVIEWS_PER_KEYWORD
    return n_asins * per_product * PRICE_PER_REVIEW_USD


def collect(client: ApifyClient, asins: list[str], marketplace: str, per_star: int, sorts: list[str],
            keywords: list[str], keyword_stars: list[int] | None = None, progress=None,
            workers: int = 5) -> tuple[list[dict], list[str], list[str]]:
    """Run every planned view (in parallel) and return (all items, apify run ids, errors).

    A failed run does not throw away the others - its error is returned so the UI can flag the gap.
    Overlapping views return some of the same reviews; parse_items() de-duplicates by review ID."""
    runs = build_runs(asins, marketplace, per_star, sorts, keywords, keyword_stars)
    status = {label: "queued" for label, _ in runs}

    def report(label, value):
        status[label] = value
        if progress:
            progress(dict(status))

    def one(label_inp):
        label, inp = label_inp
        cap = max(0.5, len(asins) * inp["maxReviews"] * PRICE_PER_REVIEW_USD * 1.25 + 0.25)
        try:
            run = client.start_run(REVIEWS_ACTOR, inp, max_charge_usd=cap)
            run = client.wait_for_run(run["id"], on_poll=lambda r: report(label, r.get("status")))
            items = client.dataset_items(run["defaultDatasetId"])
            report(label, f"done ({len(items)} items)")
            return run["id"], items, None
        except ApifyError as e:
            report(label, "FAILED")
            return None, [], f"{label}: {e}"

    all_items, run_ids, errors = [], [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for run_id, items, err in ex.map(one, runs):
            if run_id:
                run_ids.append(run_id)
            if err:
                errors.append(err)
            all_items.extend(items)
    return all_items, run_ids, errors
