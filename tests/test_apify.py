import pytest

from review_intel.apify import ApifyClient, ApifyError, build_runs, estimate_cost


class FakeResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


def client(responses):
    c = ApifyClient("tok", session=FakeSession(responses), sleep=lambda s: None)
    return c


def test_dataset_pagination_reads_every_page():
    page1 = [{"i": i} for i in range(1000)]
    page2 = [{"i": i} for i in range(1000, 1500)]
    c = client([FakeResp(200, page1), FakeResp(200, page2)])
    items = c.dataset_items("ds1", page_size=1000)
    assert len(items) == 1500
    assert c.session.calls[1][2]["params"]["offset"] == 1000


def test_retries_on_429_then_succeeds():
    c = client([FakeResp(429, {}, {"Retry-After": "0"}), FakeResp(500, {}), FakeResp(200, [{"a": 1}])])
    assert c.dataset_items("ds1") == [{"a": 1}]
    assert len(c.session.calls) == 3


def test_gives_up_after_max_retries():
    c = client([FakeResp(429, {})] * 10)
    with pytest.raises(ApifyError):
        c.dataset_items("ds1")


def test_wait_for_run_raises_on_failure():
    c = client([FakeResp(200, {"data": {"status": "RUNNING"}}),
                FakeResp(200, {"data": {"status": "FAILED", "statusMessage": "boom"}})])
    with pytest.raises(ApifyError, match="boom"):
        c.wait_for_run("r1", poll_secs=0)


def test_bad_token_is_a_clear_error_not_retried():
    c = client([FakeResp(401, {"error": {"message": "User was not found or authentication token is not valid"}})])
    with pytest.raises(ApifyError, match="token"):
        c.dataset_items("ds1")
    assert len(c.session.calls) == 1


def test_build_runs_standard_one_run_per_star_product_details_once():
    runs = build_runs(["B000000001", "B000000002"], "CA", per_star=50, sorts=["recent"], keywords=[])
    assert [label for label, _ in runs] == ["5★ recent", "4★ recent", "3★ recent", "2★ recent", "1★ recent"]
    inputs = [i for _, i in runs]
    assert all(i["maxReviews"] == 50 for i in inputs)
    assert inputs[0]["productUrls"][0]["url"] == "https://www.amazon.ca/dp/B000000001"
    assert [i["scrapeProductDetails"] for i in inputs] == [True, False, False, False, False]
    assert all(i["reviewsAlwaysSaveCategoryData"] for i in inputs)
    assert all("reviewsFilterByKeywords" not in i for i in inputs)


def test_build_runs_both_sorts_and_keywords_only_on_chosen_stars():
    runs = build_runs(["B000000001"], "US", per_star=100, sorts=["recent", "helpful"],
                      keywords=["broke", "smell"], keyword_stars=[1, 2])
    labels = [label for label, _ in runs]
    assert labels.count("1★ keywords") == 1 and labels.count("5★ keywords") == 0
    assert len(runs) == 5 * 2 + 2
    kw = dict(runs)["1★ keywords"]
    assert kw["reviewsFilterByKeywords"] == ["broke", "smell"]
    assert kw["maxReviews"] == 2 * 100
    assert dict(runs)["1★ helpful"]["sort"] == "helpful"
    assert sum(i["scrapeProductDetails"] for _, i in runs) == 1


def test_estimate_cost():
    # 11 asins * 5 stars * 100 reviews * $0.005
    assert estimate_cost(11, 100, 1, 0, 0) == pytest.approx(27.5)
    # + helpful sort doubles the sort part; + 3 keywords on 2 stars = 600 more per product
    assert estimate_cost(1, 100, 2, 3, 2) == pytest.approx((1000 + 600) * 0.005)


def test_collect_keeps_other_stars_when_one_run_fails(monkeypatch):
    from review_intel import apify as mod

    class StubClient:
        def start_run(self, actor, inp, max_charge_usd=None):
            if inp["filterByRatings"] == ["threeStar"] and inp["sort"] == "recent":
                raise ApifyError("boom")
            return {"id": inp["filterByRatings"][0]}

        def wait_for_run(self, run_id, on_poll=None):
            return {"id": run_id, "defaultDatasetId": run_id}

        def dataset_items(self, ds):
            return [{"reviewId": ds}]

    items, run_ids, errors = mod.collect(StubClient(), ["B000000001"], "US", 10, ["recent"], [])
    assert len(items) == 4 and len(run_ids) == 4
    assert errors == ["3★ recent: boom"]
