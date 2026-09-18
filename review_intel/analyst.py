"""The AI analyst agent.

Stage 1 - Review Tagger: reads every review (in batches) and tags emotion (EQ), sentiment,
          product aspects, pain point / praise, use case, persona and purchase driver.
Stage 2 - Strategist: gets the weighted, aggregated evidence for your ASIN vs competitors and
          writes the full report plus a prioritized action list.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import pandas as pd

from . import db, keywords as kwmod, metrics
from .config import CLAUDE_MODELS, anthropic_key

EMOTIONS = ["delight", "satisfaction", "trust", "relief", "surprise", "neutral", "confusion",
            "anxiety", "disappointment", "frustration", "anger", "regret"]
ASPECTS = ["quality_durability", "performance_effectiveness", "ease_of_use", "setup_instructions",
           "size_fit_dimensions", "design_look", "materials_feel", "smell_taste", "comfort",
           "value_price", "packaging_condition", "shipping_delivery", "customer_service_warranty",
           "compatibility", "safety_health", "listing_accuracy", "quantity_count", "battery_power",
           "noise", "cleaning_maintenance", "other"]
BATCH_SIZE = 60

TAG_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "review_id": {"type": "string"},
                    "sentiment": {"type": "number", "description": "-1 very negative .. 1 very positive"},
                    "emotion": {"type": "string", "enum": EMOTIONS},
                    "intensity": {"type": "integer", "description": "1 mild, 2 clear, 3 strong"},
                    "aspects": {"type": "array", "items": {"type": "string", "enum": ASPECTS}},
                    "topic": {"type": "string", "description": "2-5 word specific subject, e.g. 'lid leaks when tilted'"},
                    "pain_point": {"type": "string", "description": "specific problem in <=12 words, '' if none"},
                    "praise": {"type": "string", "description": "specific thing loved in <=12 words, '' if none"},
                    "use_case": {"type": "string", "description": "what/where/who they use it for, '' if unknown"},
                    "persona": {"type": "string", "description": "buyer type e.g. 'new parent', 'gift buyer', '' if unknown"},
                    "purchase_driver": {"type": "string", "description": "why they bought / chose it, '' if unknown"},
                },
                "required": ["review_id", "sentiment", "emotion", "intensity", "aspects", "topic",
                             "pain_point", "praise", "use_case", "persona", "purchase_driver"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tags"],
    "additionalProperties": False,
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "report_markdown": {"type": "string"},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "integer", "description": "1 = do first"},
                    "area": {"type": "string", "enum": ["product", "listing_copy", "seo_keywords", "ppc",
                                                        "images_video", "packaging", "pricing",
                                                        "customer_experience", "reviews_strategy",
                                                        "new_product"]},
                    "timeframe": {"type": "string", "enum": ["0-30 days", "31-60 days", "61-90 days"]},
                    "kpi": {"type": "string", "description": "how success is measured, with a target"},
                    "action": {"type": "string"},
                    "evidence": {"type": "string"},
                    "expected_impact": {"type": "string", "enum": ["high", "medium", "low"]},
                    "effort": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["priority", "area", "action", "evidence", "expected_impact", "effort",
                             "timeframe", "kpi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["report_markdown", "action_items"],
    "additionalProperties": False,
}

REPORT_CREDIT = ("\n\n---\n*Generated with [ReviewIQ](https://github.com/Only-Pro-Marketer/Review-IQ) by "
                 "[Pro Marketer](https://promarketer.ca) · info@promarketer.ca*\n")

TAGGER_SYSTEM = """You are an expert Amazon customer-insight analyst with high emotional intelligence.
For each review, read what the customer actually felt and experienced, not just the star rating.
- emotion: the dominant feeling expressed (a 5-star review can still carry anxiety or relief).
- aspects: every product aspect the review talks about (can be several).
- topic / pain_point / praise: be concrete and specific to this product, never generic ("bad quality").
- use_case, persona, purchase_driver: only if the text supports it, otherwise "".
Return exactly one tag per review_id given."""

STRATEGIST_SYSTEM = """You are a senior Amazon brand strategist, consumer psychologist and competitive-intelligence analyst.
You write for a founder who will act on your recommendations with real money, so every claim must be
tied to the evidence provided, and numbers must be quoted exactly as given (they are already weighted
to correct for review sampling). If evidence is thin (small sample, partial coverage), say so.
Never invent competitor facts that are not in the data."""

REPORT_OUTLINE = """Write the report_markdown with these sections (use ## headings; use tables wherever you compare):
1. Executive Summary - 5-7 bullets: where MY product wins, where it loses (with customers AND in search), the single biggest opportunity, and the #1 move.
2. Market Snapshot - table of all products: price+currency, rating, ratings count, % 1-2 star, monthly bought / DataDive est. monthly sales & revenue, listing images/videos/A+, customer-photo share.
3. Where We Win vs Lose With Customers - use customer_win_lose (weighted, per product aspect): a table aspect | my complaint % | competitor complaint % | my praise % | competitor praise % | verdict. Then: "We win on…" and "We lose on…" with customer quotes as proof, and for each key competitor what their customers love that ours don't mention (and vice versa).
4. Customer Emotional Intelligence (EQ) - dominant emotions per product, what triggers delight vs frustration/regret, pre-purchase anxieties, and the emotional job the product is hired for. Quote customers.
5. Pain Points Deep-Dive - grouped by aspect, per product, weighted %, root cause; separate product defects from expectation gaps (listing mismatch). Mark which competitor weaknesses we can exploit.
6. Search Keyword Battlefield (only if search_keywords data is present; otherwise say keyword data was not provided and how to add it) -
   a) Share of search voice table (estimated) for every product.
   b) Keywords we own (win) - defend these.
   c) Keywords each competitor pulls that we don't - one sub-table per major competitor.
   d) Striking-distance keywords (we rank 11-20) - cheapest wins.
   e) Gaps - high-volume keywords where competitors are top-10 and we are absent/beyond 20.
   f) Open white space - volume nobody owns.
   g) Customer language vs search terms - connect review use cases/praise/pain points to keywords (e.g. a use case customers love that is also a searched term we don't rank for).
7. Where We Can Win - Strategy Plan:
   a) Listing SEO - rewritten TITLE and 5 BULLETS for MY product using the priority keywords AND pre-empting top objections from reviews; plus backend search terms.
   b) PPC plan - table: keyword | match type | objective (defend / attack / rank-up / competitor conquest) | suggested bid (from data) | reason. Add product-targeting (competitor ASINs whose reviews show weaknesses we beat).
   c) Messaging & creative that exploits competitor weaknesses and amplifies our wins - main image, gallery shot list, A+ modules, video ideas.
   d) Product, packaging & customer-experience improvements ranked by impact.
   e) Review & rating strategy.
8. 30/60/90-Day Plan - table with actions, owner type, KPI and target (e.g. share of voice from X% to Y%, keywords in top-10, 1-2 star share, rating).
9. Risks, Data Limits & Watch-outs - coverage limits, keyword estimate caveats, competitor moves, review-manipulation signals.
Also return action_items: 10-20 prioritized actions covering customers, SEO keywords and PPC, each with timeframe and KPI."""


def _client() -> anthropic.Anthropic:
    key = anthropic_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is missing - add it to the .env file.")
    # base_url pinned so an inherited ANTHROPIC_BASE_URL from another tool can't redirect calls
    return anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", max_retries=4, timeout=600)


def _cost(model: str, usage) -> float:
    rates = CLAUDE_MODELS.get(model, CLAUDE_MODELS["claude-opus-5"])
    inp = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
    return (inp * rates["in"] + (usage.output_tokens or 0) * rates["out"]) / 1_000_000


def _call_json(client, model: str, system: str, user: str, schema: dict, effort: str, max_tokens: int):
    kwargs = dict(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort},
    )
    if model == "claude-opus-5":
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        stream_ctx = client.beta.messages.stream(**kwargs)
    elif model == "claude-haiku-4-5":
        kwargs["output_config"].pop("effort")  # Haiku 4.5 does not take effort
        stream_ctx = client.messages.stream(**kwargs)
    else:
        stream_ctx = client.messages.stream(**kwargs)
    with stream_ctx as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Response was cut off (max_tokens). Try a smaller batch.")
    text = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(text), _cost(msg.model, msg.usage), msg.usage


# ---------------- Stage 1: tagging ----------------

def _review_block(r) -> dict:
    return {"review_id": r.review_id, "stars": int(r.rating) if pd.notna(r.rating) else None,
            "title": r.title, "text": (r.body or "")[:2500], "variant": r.variant}


def untagged_reviews(conn, marketplace: str, asins: list[str]) -> pd.DataFrame:
    rv = db.reviews_df(conn, marketplace, asins)
    return rv[rv.emotion.isna()]


def tag_reviews(conn, marketplace: str, asins: list[str], model: str, progress=None, workers: int = 4) -> dict:
    todo = untagged_reviews(conn, marketplace, asins)
    batches = []
    for asin, grp in todo.groupby("asin"):
        prod = db.products_df(conn, marketplace, [asin])
        title = prod.title.iloc[0] if len(prod) else asin
        rows = list(grp.itertuples())
        for i in range(0, len(rows), BATCH_SIZE):
            batches.append((asin, title, rows[i:i + BATCH_SIZE]))
    client = _client()
    stats = {"batches": len(batches), "done": 0, "tagged": 0, "cost_usd": 0.0, "errors": []}

    def run(batch):
        asin, title, rows = batch
        ids = {r.review_id for r in rows}
        payload = [_review_block(r) for r in rows]
        user = (f"Product ASIN {asin}: {title}\n\nTag each of these {len(payload)} reviews:\n"
                + json.dumps(payload, ensure_ascii=False))
        data, cost, _ = _call_json(client, model, TAGGER_SYSTEM, user, TAG_SCHEMA, "low", 32000)
        tags = [t for t in data["tags"] if t["review_id"] in ids]
        return tags, cost, ids - {t["review_id"] for t in tags}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(run, b) for b in batches]
        for f in as_completed(futures):
            try:
                tags, cost, missing = f.result()
                for t in tags:
                    t["sentiment"] = max(-1.0, min(1.0, float(t["sentiment"])))
                    t["intensity"] = max(1, min(3, int(t["intensity"])))
                db.save_tags(conn, marketplace, tags, model)
                stats["tagged"] += len(tags)
                stats["cost_usd"] += cost
                if missing:
                    stats["errors"].append(f"{len(missing)} reviews not returned by model (will retry next run)")
            except Exception as e:  # keep other batches going, surface the error
                stats["errors"].append(str(e)[:300])
            stats["done"] += 1
            if progress:
                progress(dict(stats))
    return stats


# ---------------- Stage 2: evidence brief + report ----------------

def _top_counts(series: pd.Series, n: int) -> list:
    s = series.dropna().astype(str).str.strip()
    s = s[s != ""]
    return [{"text": k, "count": int(v)} for k, v in s.str.lower().value_counts().head(n).items()]


def _quotes(grp: pd.DataFrame, n: int) -> list:
    g = grp.sort_values(["helpful_votes", "intensity"], ascending=False).head(n)
    return [{"stars": int(r.rating), "helpful": int(r.helpful_votes), "date": r.review_date,
             "quote": f"{r.title}: {(r.body or '')[:350]}"} for r in g.itertuples()]


def build_brief(conn, marketplace: str, my_asin: str, competitor_asins: list[str],
                niche_id: str | None = None) -> dict:
    asins = [my_asin] + competitor_asins
    prods = db.products_df(conn, marketplace, asins)
    rv = db.reviews_df(conn, marketplace, asins)
    cov = metrics.coverage_table(rv, db.coverage_df(conn, marketplace))
    img_counts = db.product_images_df(conn, marketplace).groupby("asin").size().to_dict()
    neg = metrics.aspect_prevalence(rv, prods, negative_only=True)
    pos = metrics.aspect_prevalence(rv, prods, negative_only=False)
    emo = metrics.emotion_mix(rv, prods)
    trend = metrics.monthly_trend(rv)
    wl = kwmod.review_win_lose(neg, pos, my_asin, competitor_asins) if len(neg) or len(pos) else pd.DataFrame()
    out = {"marketplace": marketplace, "my_asin": my_asin, "products": [],
           "customer_win_lose": wl.to_dict("records") if len(wl) else "not available (tag reviews first)",
           "search_keywords": kwmod.keyword_battlefield(conn, niche_id, my_asin, competitor_asins)
           if niche_id else "not provided"}
    for asin in asins:
        p = prods[prods.asin == asin]
        r = rv[rv.asin == asin]
        if p.empty and r.empty:
            continue
        p = p.iloc[0].to_dict() if len(p) else {}
        tagged = r.dropna(subset=["emotion"])
        t = trend[trend.asin == asin].tail(12)
        entry = {
            "asin": asin, "role": "MY PRODUCT" if asin == my_asin else "competitor",
            "title": p.get("title"), "brand": p.get("brand"),
            "price": p.get("price"), "currency": p.get("currency"),
            "stars": p.get("stars"), "ratings_total": p.get("ratings_total"),
            "star_breakdown_pct": {s: round(100 * p[f"pct_{s}"], 1) for s in range(1, 6)
                                   if p.get(f"pct_{s}") is not None},
            "monthly_bought": p.get("monthly_bought"), "amazons_choice": bool(p.get("is_amazon_choice")),
            "listing_images": img_counts.get(asin, 0), "videos": p.get("videos_count"),
            "has_aplus": bool(p.get("has_aplus")), "variants": p.get("variant_count"),
            "bullets": json.loads(p["features"]) if p.get("features") else [],
            "attributes": (json.loads(p["attributes"]) if p.get("attributes") else [])[:20],
            "amazon_ai_summary": p.get("ai_summary"),
            "amazon_ai_keywords": json.loads(p["ai_keywords"]) if p.get("ai_keywords") else [],
            "sample": {
                "reviews_collected": len(r), "reviews_tagged": len(tagged),
                "pct_verified": metrics.safe_pct(int(r.verified.sum()), len(r)),
                "pct_vine": metrics.safe_pct(int(r.vine.sum()), len(r)),
                "pct_with_customer_photos": metrics.safe_pct(int((r.image_count > 0).sum()), len(r)),
                "coverage_by_star": cov[cov.asin == asin][["star", "collected", "available_reviews", "coverage_pct"]]
                .to_dict("records"),
            },
            "weighted_negative_aspects_pct_of_all_customers": neg[neg.asin == asin]
            .sort_values("weighted_pct", ascending=False).drop(columns="asin").to_dict("records") if len(neg) else [],
            "weighted_positive_aspects_pct_of_all_customers": pos[pos.asin == asin]
            .sort_values("weighted_pct", ascending=False).drop(columns="asin").to_dict("records") if len(pos) else [],
            "weighted_emotion_mix_pct": emo[emo.asin == asin].sort_values("weighted_pct", ascending=False)
            .drop(columns="asin").to_dict("records") if len(emo) else [],
            "top_pain_points": _top_counts(tagged.pain_point, 25),
            "top_praise": _top_counts(tagged.praise, 20),
            "use_cases": _top_counts(tagged.use_case, 12),
            "personas": _top_counts(tagged.persona, 10),
            "purchase_drivers": _top_counts(tagged.purchase_driver, 10),
            "monthly_trend_sample": [{"month": str(x.month)[:7], "reviews": int(x.reviews), "neg_pct": x.neg_pct}
                                     for x in t.itertuples()],
            "quotes_negative": _quotes(tagged[tagged.rating <= 2], 8) if len(tagged) else [],
            "quotes_mixed": _quotes(tagged[tagged.rating == 3], 4) if len(tagged) else [],
            "quotes_positive": _quotes(tagged[tagged.rating >= 4], 6) if len(tagged) else [],
        }
        out["products"].append(entry)
    return out


def write_report(conn, marketplace: str, my_asin: str, competitor_asins: list[str], model: str,
                 niche_id: str | None = None) -> dict:
    brief = build_brief(conn, marketplace, my_asin, competitor_asins, niche_id)
    user = (f"MY product is ASIN {my_asin}. Competitors: {', '.join(competitor_asins) or 'none'}.\n"
            "Percentages named weighted_* are estimates of the share of ALL customers (corrected for "
            "equal-per-star sampling). Use them, not raw counts, when stating how common something is.\n\n"
            f"{REPORT_OUTLINE}\n\nEVIDENCE (JSON):\n{json.dumps(brief, ensure_ascii=False, default=str)}")
    client = _client()
    data, cost, usage = _call_json(client, model, STRATEGIST_SYSTEM, user, REPORT_SCHEMA, "high", 64000)
    data["action_items"] = sorted(data["action_items"], key=lambda a: a["priority"])
    aid = db.save_analysis(
        conn, created_at=db.now(), marketplace=marketplace, my_asin=my_asin,
        competitor_asins=json.dumps(competitor_asins), model=model, report_md=data["report_markdown"],
        insights_json=json.dumps({"action_items": data["action_items"], "brief": brief}, default=str),
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, cost_usd=cost)
    return {"id": aid, "cost_usd": cost, **data}


def estimate_ai_cost(n_reviews: int, n_products: int, model: str) -> float:
    rates = CLAUDE_MODELS[model]
    tag_in = n_reviews * 220 + (n_reviews / BATCH_SIZE) * 1500
    tag_out = n_reviews * 90
    rep_in = n_products * 6000 + (12000 if n_products else 0)
    rep_out = 22000 if n_products else 0
    return ((tag_in + rep_in) * rates["in"] + (tag_out + rep_out) * rates["out"]) / 1_000_000
