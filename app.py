"""Amazon Review Intelligence - Streamlit dashboard.  Run:  ./run.sh"""
from __future__ import annotations

import json
import os
import threading
import time

import pandas as pd
import plotly.express as px
import plotly.io as pio
import streamlit as st

from review_intel import analyst, db, export, images, keywords as kwm, metrics, ui
from review_intel.apify import ApifyClient, collect, estimate_cost
from review_intel.config import (CLAUDE_MODELS, DB_PATH, DEFAULT_MODEL, MARKETPLACES, MAX_COMPETITORS,
                                 PRICE_PER_REVIEW_USD, anthropic_key, apify_token, datadive_key)
from review_intel.datadive import DD_MARKETPLACE, DataDiveClient, best_niche_for, parse_competitors, parse_keywords
from review_intel.parse import parse_items, validate_asin

st.set_page_config(page_title="Review Intelligence", page_icon="⭐", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(ui.CSS, unsafe_allow_html=True)
pio.templates.default = "plotly_white"
PLOT_LAYOUT = dict(font=dict(family="Inter, system-ui, sans-serif", size=13), margin=dict(l=10, r=10, t=30, b=10),
                   legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0), paper_bgcolor="rgba(0,0,0,0)")


@st.cache_resource
def get_conn(path: str):
    return db.connect(path)


conn = get_conn(os.getenv("REVIEW_DB") or str(DB_PATH))


def html(s: str):
    st.markdown(s, unsafe_allow_html=True)


def run_in_thread(fn, render, interval=2.0):
    """Run fn() in a worker thread while the main script thread re-renders progress."""
    box = {"progress": {}, "result": None, "error": None}

    def target():
        try:
            box["result"] = fn(lambda p: box.__setitem__("progress", p))
        except Exception as e:  # surfaced below
            box["error"] = e
    t = threading.Thread(target=target, daemon=True)
    t.start()
    while t.is_alive():
        render(box["progress"])
        time.sleep(interval)
    render(box["progress"])
    if box["error"]:
        raise box["error"]
    return box["result"]


PAGES = ["🏠  Overview", "📥  Collect", "🛍️  Products", "💬  Reviews", "📸  Customer Photos",
         "📊  Insights", "🔎  Keywords", "🧠  AI Analyst", "⬇️  Export"]
P_KW, P_AI, P_EXPORT = PAGES[6], PAGES[7], PAGES[8]

# ---------------- sidebar ----------------
with st.sidebar:
    html("<h2 style='margin:0 0 2px 0;color:#fff'>⭐ Review<span style='color:#f97316'>IQ</span></h2>"
         "<div style='color:#94a3b8;font-size:.8rem;margin-bottom:10px'>Amazon review intelligence</div>")
    page = st.radio("Navigation", PAGES, label_visibility="collapsed", key="page")
    st.divider()
    mp = st.selectbox("Marketplace", list(MARKETPLACES), format_func=lambda k: MARKETPLACES[k]["label"])
    all_products = db.products_df(conn, mp)
    known = all_products.asin.tolist()
    if known:
        mine_default = all_products[all_products.role == "mine"].asin.tolist()
        label = {r.asin: f"{(r.brand or '')[:16]} · {r.asin}" for r in all_products.itertuples()}
        my_asin = st.selectbox("Your product", known, index=known.index(mine_default[0]) if mine_default else 0,
                               format_func=lambda a: label.get(a, a))
        others = [a for a in known if a != my_asin]
        comps = st.multiselect("Compare with", others, default=others[:MAX_COMPETITORS],
                               max_selections=MAX_COMPETITORS, format_func=lambda a: label.get(a, a))
    else:
        my_asin, comps = None, []
    work = ([my_asin] if my_asin else []) + comps
    st.divider()
    ok_a, ok_c, ok_d = bool(apify_token()), bool(anthropic_key()), bool(datadive_key())
    html(f"<div style='font-size:.8rem'>{'🟢' if ok_a else '🔴'} Apify &nbsp; {'🟢' if ok_c else '🔴'} Claude"
         f" &nbsp; {'🟢' if ok_d else '⚪'} DataDive"
         f"<br><span style='color:#94a3b8'>Prices in {MARKETPLACES[mp]['currency']}</span></div>")

prods = db.products_df(conn, mp, work) if work else db.products_df(conn, mp).iloc[0:0]
rv = db.reviews_df(conn, mp, work) if work else db.reviews_df(conn, mp).iloc[0:0]
short = {r.asin: f"{'★ ' if r.asin == my_asin else ''}{(r.brand or r.asin)[:14]}" for r in prods.itertuples()}
for a in work:
    short.setdefault(a, a)
color_map = {short[a]: ui.PRODUCT_PALETTE[i % len(ui.PRODUCT_PALETTE)] for i, a in enumerate(work)}
first_img = db.product_images_df(conn, mp).groupby("asin").url.first().to_dict()


def need_data():
    html(ui.empty("📦", "No products yet", "Go to Collect, enter your ASIN and competitors, and pull the data."))


def niche_choices():
    """Saved DataDive niches for this marketplace, best match for the working set first."""
    ns = db.niches_df(conn, mp)
    if ns.empty:
        return ns
    tracked = db.df(conn, "select niche_id, group_concat(distinct asin) asins from keyword_ranks group by niche_id")
    ns = ns.merge(tracked, on="niche_id", how="left")
    ns["has_mine"] = ns.asins.fillna("").str.contains(my_asin or "~")
    ns["overlap"] = ns.asins.fillna("").apply(lambda x: sum(a in x.split(",") for a in work))
    return ns.sort_values(["has_mine", "overlap", "pulled_at"], ascending=False).reset_index(drop=True)


def niche_label(r):
    return (f"{r.hero_keyword or r.label} · {r.overlap} of your products tracked"
            f"{' · incl. yours' if r.has_mine else ''} · pulled {str(r.pulled_at)[:10]}")


# ================= OVERVIEW =================
if page == PAGES[0]:
    chips = ui.chip(MARKETPLACES[mp]["label"], "gray") + ui.chip(f"{len(work)} products in view", "gray")
    html(ui.hero("Amazon Review Intelligence",
                 "Collect every review and image for your product and competitors, then let the AI analyst tell you what to fix.",
                 chips))
    n_tagged = int(rv.emotion.notna().sum()) if len(rv) else 0
    has_report = bool(db.df(conn, "select count(*) n from analyses where marketplace=?", (mp,)).n.iloc[0])
    states = [ok_a and ok_c, len(rv) > 0, len(rv) > 0 and n_tagged >= len(rv), has_report]
    current = states.index(False) if False in states else 99
    steps = [("Connect keys", "Apify + Claude in .env"), ("Collect", "Reviews, images, product data"),
             ("Tag reviews", "AI reads every review"), ("Get report", "Strategy + action plan")]
    cols = st.columns(4)
    for i, (c, (t, s)) in enumerate(zip(cols, steps)):
        state = "done" if states[i] else ("now" if i == current else "")
        c.markdown(ui.step(i + 1, t, s, state), unsafe_allow_html=True)
    st.write("")
    if not work:
        need_data()
        st.stop()
    mine = prods[prods.asin == my_asin]
    comp = prods[prods.asin != my_asin]
    k = st.columns(5)
    k[0].metric("Reviews collected", f"{len(rv):,}")
    k[1].metric("Customer photos", f"{int(rv.image_count.sum()):,}")
    k[2].metric("AI-tagged", f"{n_tagged:,}", f"{metrics.safe_pct(n_tagged, len(rv)) or 0:.0f}% done",
                delta_color="off")
    if len(mine) and len(comp):
        my_star = mine.stars.iloc[0]
        avg_c = comp.stars.mean()
        k[3].metric("Your rating vs avg competitor", f"{my_star:.1f} ★", f"{my_star - avg_c:+.2f} vs {avg_c:.2f}")
        my_neg = 100 * ((mine.pct_1.iloc[0] or 0) + (mine.pct_2.iloc[0] or 0))
        c_neg = (100 * (comp.pct_1.fillna(0) + comp.pct_2.fillna(0))).mean()
        k[4].metric("Your 1–2★ share vs competitors", f"{my_neg:.1f}%", f"{my_neg - c_neg:+.1f} pts",
                    delta_color="inverse")
    elif len(mine):
        k[3].metric("Your rating", f"{mine.stars.iloc[0]:.1f} ★")
        k[4].metric("Ratings on Amazon", f"{int(mine.ratings_total.iloc[0] or 0):,}")
    st.subheader("Your product vs competitors")
    order = [a for a in work if a in set(prods.asin)]
    for row_start in range(0, len(order), 4):
        cols = st.columns(4)
        for c, a in zip(cols, order[row_start:row_start + 4]):
            p = prods[prods.asin == a].iloc[0].to_dict()
            c.markdown(ui.product_card(p, first_img.get(a), a == my_asin), unsafe_allow_html=True)
    last = db.df(conn, "select insights_json, created_at from analyses where marketplace=? and my_asin=? "
                       "order by id desc limit 1", (mp, my_asin))
    if len(last):
        st.subheader("Top actions from your latest AI report")
        for a in json.loads(last.insights_json.iloc[0]).get("action_items", [])[:3]:
            html(ui.action_card(a))

# ================= COLLECT =================
elif page == PAGES[1]:
    html(ui.hero("Collect data", "Reviews are pulled separately for each star level (5★ → 1★), plus listing images, "
                 "product details and customer photos."))
    if not ok_a:
        st.error("Apify key missing. Add APIFY_TOKEN to the .env file, then restart.")
    with st.container(border=True):
        c1, c2 = st.columns([1, 2], gap="large")
        with c1:
            st.markdown("**1 · Your product**")
            raw_mine = st.text_input("Your ASIN or product link", placeholder="B0XXXXXXXX",
                                     label_visibility="collapsed")
        with c2:
            st.markdown(f"**2 · Competitors** (up to {MAX_COMPETITORS}, one per line)")
            raw_comp = st.text_area("Competitors", height=120, label_visibility="collapsed",
                                    placeholder="B0AAAAAAAA\nB0BBBBBBBB\nhttps://www.amazon.com/dp/B0CCCCCCCC")
        mine_asin = validate_asin(raw_mine)
        comp_list, bad = [], []
        for line in raw_comp.replace(",", "\n").splitlines():
            if line.strip():
                a = validate_asin(line)
                if a:
                    comp_list.append(a)
                else:
                    bad.append(line.strip())
        comp_list = [a for a in dict.fromkeys(comp_list) if a != mine_asin]
        preview = ""
        if mine_asin:
            preview += ui.chip("★ " + mine_asin, "dark")
        elif raw_mine:
            preview += ui.chip("✗ " + raw_mine[:20], "red")
        preview += "".join(ui.chip("✓ " + a, "green" if i < MAX_COMPETITORS else "red") for i, a in enumerate(comp_list))
        preview += "".join(ui.chip("✗ " + b[:20], "red") for b in bad)
        if preview:
            html(f"<div style='margin:4px 0 8px'>{preview}</div>")
        if bad:
            st.caption("Red items aren't valid ASINs and will be skipped.")
        if len(comp_list) > MAX_COMPETITORS:
            st.warning(f"Only the first {MAX_COMPETITORS} competitors will be used.")
        st.markdown("**3 · How many reviews?**")
        depth = st.segmented_control(
            "Review depth", ["Standard", "Extended", "Maximum"], default="Extended", label_visibility="collapsed",
            help="Amazon shows at most ~100 reviews per filter view. More views = more unique reviews.")
        depth = depth or "Extended"
        html({"Standard": ui.chip("≤100 per star · 1 view (most recent)", "gray"),
              "Extended": ui.chip("≈170 per star · 2 views (recent + most helpful)", "blue"),
              "Maximum": ui.chip("≈400+ per star · 2 views + keyword searches", "orange")}[depth])
        s1, s2 = st.columns(2)
        per_star = s1.select_slider("Reviews per view", options=[10, 20, 30, 50, 75, 100], value=100,
                                    help="100 is Amazon's maximum per view. Lower it for a cheap test run.")
        dl_images = s2.toggle("Save images to disk", value=True)
        sorts = ["recent"] if depth == "Standard" else ["recent", "helpful"]
        keywords, keyword_stars = [], []
        if depth == "Maximum":
            with st.container(border=True):
                st.markdown("**Keyword searches**. Each keyword finds up to ~100 more reviews per star level.")
                kw = st.text_area(
                    "Keywords (comma-separated)", height=68,
                    value="quality, broke, stopped working, return, cheap, disappointed, size, smell, "
                          "price, easy, love, gift")
                keywords = [x.strip() for x in kw.replace("\n", ",").split(",") if x.strip()]
                keyword_stars = st.pills("Search keywords in", [5, 4, 3, 2, 1], default=[3, 2, 1],
                                         selection_mode="multi", format_func=lambda s_: f"{s_}★") or []
                st.caption("Tip: use words your customers actually say (e.g. 'lid', 'strap', 'battery'). "
                           "Low-star keyword searches surface the most complaints.")
    asins = ([mine_asin] if mine_asin else []) + comp_list[:MAX_COMPETITORS]
    est = estimate_cost(len(asins), per_star, len(sorts), len(keywords), len(keyword_stars)) if asins else 0.0
    max_reviews = len(asins) * (5 * per_star * len(sorts) + 100 * len(keywords) * len(keyword_stars))
    m1, m2, m3, m4 = st.columns([1, 1, 1, 1.3])
    m1.metric("Products", len(asins))
    m2.metric("Max reviews", f"{max_reviews:,}",
              help="Upper bound. Views overlap and many products have fewer reviews; duplicates are merged.")
    m3.metric("Max Apify cost", f"${est:,.2f}",
              help=f"${PRICE_PER_REVIEW_USD}/review · real cost is usually 30-60% lower")
    with m4:
        st.write("")
        go = st.button("🚀  Collect now", type="primary", disabled=not (mine_asin and ok_a), width="stretch")
        if not mine_asin:
            st.caption("Enter your ASIN to start.")
    if go:
        client = ApifyClient(apify_token())
        run_id = db.log_run(conn, started_at=db.now(), marketplace=mp, asins=json.dumps(asins),
                            settings=json.dumps({"per_star": per_star, "sorts": sorts, "keywords": keywords,
                                                 "keyword_stars": keyword_stars}),
                            status="running", est_cost_usd=est)
        with st.status("Collecting from Amazon… usually 2–10 minutes", expanded=True) as status:
            ph = st.empty()

            def render(p):
                if p:
                    cells = []
                    for k_, v in p.items():
                        color = "green" if str(v).startswith("done") else ("red" if v == "FAILED" else "amber")
                        cells.append(ui.chip(f"{k_}: {v}", color))
                    ph.markdown("".join(cells), unsafe_allow_html=True)
            try:
                items, apify_ids, errors = run_in_thread(
                    lambda cb: collect(client, asins, mp, per_star, sorts, keywords, keyword_stars,
                                       progress=cb), render)
                parsed = parse_items(items, mp)
                roles = {a: ("mine" if a == mine_asin else "competitor") for a in asins}
                n = db.save_parsed(conn, parsed, mp, roles=roles)
                db.set_roles(conn, mp, roles)
                missing = [a for a in asins if a not in parsed["products"]]
                all_err = errors + [f"{e['input']}: {e['error']}" for e in parsed["errors"]]
                if dl_images:
                    st.write("Saving images…")
                    res = images.download_all(conn, mp, asins)
                    st.write(f"Images saved: {res['downloaded']} (failed: {res['failed']})")
                db.log_run(conn, id=run_id, finished_at=db.now(), status="done" if not all_err else "partial",
                           apify_run_ids=json.dumps(apify_ids), reviews_saved=n, errors=json.dumps(all_err))
                status.update(label=f"Done: {n:,} reviews saved for {len(asins)} products", state="complete")
                if missing:
                    st.warning("No product details came back for: " + ", ".join(missing) +
                               " (wrong marketplace, delisted or blocked; try again).")
                if all_err:
                    st.warning("Some parts failed, so data is PARTIAL:\n\n- " + "\n- ".join(all_err[:20]))
                st.success("Done. Open **Overview** to see the results.")
            except Exception as e:
                db.log_run(conn, id=run_id, finished_at=db.now(), status="failed", errors=json.dumps([str(e)]))
                status.update(label="Collection failed", state="error")
                st.error(str(e))
    with st.expander("Run history"):
        st.dataframe(db.df(conn, "select id, started_at, marketplace, asins, status, reviews_saved, est_cost_usd, "
                                 "errors from runs order by id desc limit 20"), width="stretch", hide_index=True)

elif not work:
    need_data()

# ================= PRODUCTS =================
elif page == PAGES[2]:
    html(ui.hero("Products & listings", "Side-by-side listing comparison, galleries, bullets and Amazon's own AI summary."))
    summ = metrics.product_summary(prods, rv)
    img_counts = db.product_images_df(conn, mp).groupby("asin").size()
    summ["listing images"] = summ.asin.map(img_counts).fillna(0).astype(int)
    with st.container(border=True):
        st.markdown("**Comparison table**")
        st.dataframe(summ, width="stretch", hide_index=True,
                     column_config={"stars (Amazon)": st.column_config.NumberColumn(format="%.1f ★"),
                                    "price": st.column_config.NumberColumn(format="%.2f")})
        st.caption("“(Amazon)” covers all ratings on the listing. “(sample)” = reviews collected here.")
    pimgs = db.product_images_df(conn, mp)
    for a in work:
        prow = prods[prods.asin == a]
        if prow.empty:
            continue
        p = prow.iloc[0]
        with st.container(border=True):
            left, right = st.columns([1, 2], gap="large")
            with left:
                html(ui.product_card(p.to_dict(), first_img.get(a), a == my_asin))
                st.link_button("Open on Amazon ↗", p.url or "#", width="stretch")
            with right:
                t1, t2, t3 = st.tabs(["Gallery", "Bullets & specs", "Amazon AI summary"])
                with t1:
                    ims = pimgs[pimgs.asin == a]
                    gcols = st.columns(4)
                    for i, im in enumerate(ims.itertuples()):
                        gcols[i % 4].image(im.local_path or im.url, width="stretch")
                with t2:
                    for f in json.loads(p.features or "[]"):
                        st.markdown(f"- {f}")
                    attrs = pd.DataFrame(json.loads(p.attributes or "[]"))
                    if len(attrs):
                        st.dataframe(attrs.drop_duplicates("key"), hide_index=True, width="stretch")
                with t3:
                    if p.ai_summary:
                        st.info(p.ai_summary)
                        kws = pd.DataFrame(json.loads(p.ai_keywords or "[]"))
                        if len(kws):
                            kws["positive %"] = (100 * kws.positive / kws.total).round(0)
                            st.dataframe(kws[["name", "sentiment", "total", "positive", "negative", "positive %"]],
                                         hide_index=True, width="stretch",
                                         column_config={"positive %": st.column_config.ProgressColumn(
                                             min_value=0, max_value=100, format="%d%%")})
                    else:
                        st.caption("Amazon shows no AI summary for this listing.")

# ================= REVIEWS =================
elif page == PAGES[3]:
    html(ui.hero("Reviews", "Every collected review: filter it, search it and read it."))
    with st.container(border=True):
        f1, f2, f3 = st.columns([2, 2, 2])
        sel_asins = f1.multiselect("Products", work, default=work, format_func=lambda a: short[a])
        sel_stars = f2.pills("Stars", [5, 4, 3, 2, 1], default=[5, 4, 3, 2, 1], selection_mode="multi",
                             format_func=lambda s: f"{s}★")
        text_q = f3.text_input("Search text", placeholder="e.g. battery, smell, broke")
        g1, g2, g3, g4 = st.columns([2, 2, 1, 1])
        emo_opts = sorted(rv.emotion.dropna().unique())
        sel_emo = g1.multiselect("Emotion", emo_opts, placeholder="after AI tagging")
        asp_opts = sorted({x for v in rv.aspects.dropna() for x in json.loads(v)})
        sel_asp = g2.multiselect("Aspect", asp_opts, format_func=lambda x: x.replace("_", " "),
                                 placeholder="after AI tagging")
        only_verified = g3.toggle("Verified only")
        only_photos = g4.toggle("With photos")
    d = rv[rv.asin.isin(sel_asins) & rv.rating.isin(sel_stars or [])]
    if only_verified:
        d = d[d.verified == 1]
    if only_photos:
        d = d[d.image_count > 0]
    if text_q:
        d = d[(d.title.fillna("") + " " + d.body.fillna("")).str.contains(text_q, case=False, regex=False)]
    if sel_emo:
        d = d[d.emotion.isin(sel_emo)]
    if sel_asp:
        d = d[d.aspects.fillna("[]").apply(lambda x: bool(set(json.loads(x)) & set(sel_asp)))]
    d = d.sort_values(["helpful_votes", "review_date"], ascending=False)
    top = st.columns([3, 1])
    top[0].caption(f"{len(d):,} reviews shown")
    view_mode = top[1].segmented_control("View", ["Cards", "Table"], default="Cards", label_visibility="collapsed")
    if view_mode == "Table":
        view = d.assign(product=d.asin.map(short), photo=d.image_urls.fillna("").str.split(" ").str[0],
                        verified=d.verified.astype(bool))
        st.dataframe(
            view[["product", "rating", "review_date", "title", "body", "verified", "helpful_votes", "image_count",
                  "photo", "variant", "emotion", "sentiment", "aspects", "pain_point", "praise", "review_url"]],
            width="stretch", hide_index=True, height=640,
            column_config={
                "photo": st.column_config.ImageColumn("1st photo"),
                "review_url": st.column_config.LinkColumn("link", display_text="open"),
                "rating": st.column_config.NumberColumn("★", format="%d★"),
                "body": st.column_config.TextColumn("review", width="large"),
                "sentiment": st.column_config.ProgressColumn(min_value=-1, max_value=1, format="%.2f"),
            })
    else:
        per_page = 25
        pages_n = max(1, -(-len(d) // per_page))
        pg = st.number_input("Page", 1, pages_n, 1, label_visibility="collapsed") if pages_n > 1 else 1
        for r in d.iloc[(pg - 1) * per_page: pg * per_page].to_dict("records"):
            html(ui.review_card(r, short.get(r["asin"], r["asin"])))
        if pages_n > 1:
            st.caption(f"Page {pg} of {pages_n}")
    with st.expander("Coverage: collected vs available on Amazon"):
        cov = metrics.coverage_table(rv, db.coverage_df(conn, mp))
        cov = cov[cov.asin.isin(work)].assign(product=lambda x: x.asin.map(short))
        st.dataframe(cov[["product", "star", "collected", "available_reviews", "coverage_pct"]],
                     hide_index=True, width="stretch",
                     column_config={"coverage_pct": st.column_config.ProgressColumn(
                         "coverage", min_value=0, max_value=100, format="%.1f%%")})
        st.caption("Amazon lists ~100 reviews per star filter; use Deep mode for more. "
                   "‘available_reviews’ counts written reviews (not star-only ratings).")

# ================= PHOTOS =================
elif page == PAGES[4]:
    html(ui.hero("Customer photos", "What buyers actually photograph. Low-star photos often show defects and "
                 "packaging problems."))
    ri = db.review_images_df(conn, mp)
    ri = ri[ri.asin.isin(work)]
    with st.container(border=True):
        c1, c2 = st.columns(2)
        pa = c1.multiselect("Products", work, default=work, format_func=lambda a: short[a], key="ph_p")
        ps = c2.pills("Stars", [5, 4, 3, 2, 1], default=[5, 4, 3, 2, 1], selection_mode="multi",
                      format_func=lambda s: f"{s}★", key="ph_s")
    ri = ri[ri.asin.isin(pa) & ri.rating.isin(ps or [])]
    st.caption(f"{len(ri):,} photos")
    if ri.empty:
        html(ui.empty("📷", "No customer photos here", "Try other stars/products, or collect more reviews."))
    cols = st.columns(5)
    for i, im in enumerate(ri.head(300).itertuples()):
        cols[i % 5].markdown(ui.photo_tile(im.url, im.rating, short.get(im.asin, im.asin), im.title,
                                           im.review_date, im.review_url), unsafe_allow_html=True)
    if len(ri) > 300:
        st.info("Showing the first 300. Narrow the filters to see more.")

# ================= INSIGHTS =================
elif page == PAGES[5]:
    html(ui.hero("Insights", "Weighted % = estimated share of ALL customers (sample re-weighted by Amazon's real star mix)."))
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.markdown("**Star breakdown (Amazon, all ratings)**")
        bd = prods.melt(id_vars="asin", value_vars=[f"pct_{s}" for s in range(1, 6)], var_name="star", value_name="share")
        bd["star"] = bd.star.str[-1] + "★"
        bd["share"] = bd.share * 100
        bd["product"] = bd.asin.map(short)
        fig = px.bar(bd, x="share", y="product", color="star", orientation="h", labels={"share": "% of ratings", "product": ""},
                     color_discrete_map={f"{k}★": v for k, v in ui.STAR_COLORS.items()},
                     category_orders={"star": ["5★", "4★", "3★", "2★", "1★"]})
        st.plotly_chart(fig.update_layout(**PLOT_LAYOUT, height=320), width="stretch")
    with c2, st.container(border=True):
        st.markdown("**Review volume over time (collected sample)**")
        tr = metrics.monthly_trend(rv)
        if len(tr):
            tr["product"] = tr.asin.map(short)
            tr["month"] = tr.month.dt.strftime("%b %Y")
            fig = px.bar(tr, x="month", y="reviews", color="product", barmode="group", color_discrete_map=color_map,
                         labels={"month": "", "reviews": "reviews"}, hover_data={"neg_pct": True})
            fig.update_xaxes(type="category")
            st.plotly_chart(fig.update_layout(**PLOT_LAYOUT, height=320), width="stretch")
            st.caption("Capped per star, so read this as a relative trend, not total sales.")
    neg = metrics.aspect_prevalence(rv, prods, negative_only=True)
    pos = metrics.aspect_prevalence(rv, prods, negative_only=False)
    emo = metrics.emotion_mix(rv, prods)
    if neg.empty and pos.empty:
        html(ui.empty("🧠", "Unlock deeper insights", "Run AI Analyst → Tag reviews to see complaints, praise, "
                      "emotions and pain points by product."))
    else:
        c3, c4 = st.columns(2)
        for col, title, frame, scale in [(c3, "Complaints by aspect", neg, "Reds"), (c4, "Praise by aspect", pos, "Greens")]:
            with col, st.container(border=True):
                st.markdown(f"**{title}** · % of all customers")
                if len(frame):
                    frame = frame.assign(product=frame.asin.map(short), aspect=frame.aspect.str.replace("_", " "))
                    pv = frame.pivot_table(index="aspect", columns="product", values="weighted_pct").fillna(0)
                    fig = px.imshow(pv, text_auto=".1f", color_continuous_scale=scale, aspect="auto",
                                    labels={"color": "%", "x": "", "y": ""})
                    st.plotly_chart(fig.update_layout(**PLOT_LAYOUT, height=max(300, 28 * len(pv))), width="stretch")
        if len(emo):
            with st.container(border=True):
                st.markdown("**Emotional mix (EQ)** · % of all customers")
                emo = emo.assign(product=emo.asin.map(short))
                fig = px.bar(emo, x="weighted_pct", y="product", color="emotion", orientation="h",
                             color_discrete_map=ui.EMOTION_COLORS, labels={"weighted_pct": "% customers", "product": ""})
                st.plotly_chart(fig.update_layout(**PLOT_LAYOUT, height=120 + 45 * emo.asin.nunique()), width="stretch")
        if comps:
            wl = kwm.review_win_lose(neg, pos, my_asin, comps)
            if len(wl):
                with st.container(border=True):
                    st.markdown(f"**Where {short.get(my_asin)} wins vs loses with customers** · "
                                "vs competitor average, % of all customers")
                    wl = wl.assign(aspect=wl.aspect.str.replace("_", " "),
                                   best_competitor_on_aspect=wl.best_competitor_on_aspect.map(
                                       lambda a: short.get(a, a) if isinstance(a, str) else a))
                    st.dataframe(
                        wl.style.map(lambda v: {"WIN": "background-color:#dcfce7;color:#166534;font-weight:600",
                                                "LOSE": "background-color:#fee2e2;color:#991b1b;font-weight:600"}
                                     .get(v, ""), subset=["verdict"]),
                        hide_index=True, width="stretch")
                    st.caption("Score = (their complaints − yours) + (your praise − theirs). WIN ≥ +3 pts, LOSE ≤ −3 pts.")
        tagged = rv.dropna(subset=["emotion"])
        st.subheader("Grouped pain points, praise & use cases")
        tabs_ = st.tabs([short[a] for a in work if a in set(tagged.asin)] or ["—"])
        for t_, a in zip(tabs_, [a for a in work if a in set(tagged.asin)]):
            with t_:
                t = tagged[tagged.asin == a]
                cc = st.columns(3)
                for col, field, title in [(cc[0], "pain_point", "🔴 Pain points"), (cc[1], "praise", "🟢 Praise"),
                                          (cc[2], "use_case", "🎯 Use cases")]:
                    s = t[field].fillna("").str.strip().str.lower()
                    vc = s[s != ""].value_counts().head(15).rename("mentions").reset_index()
                    col.markdown(f"**{title}**")
                    col.dataframe(vc, hide_index=True, width="stretch",
                                  column_config={"mentions": st.column_config.ProgressColumn(
                                      min_value=0, max_value=int(vc.mentions.max()) if len(vc) else 1, format="%d")})


# ================= KEYWORDS =================
elif page == P_KW:
    html(ui.hero("Search keywords", "Which Amazon searches you and each competitor win, with search volume and "
                 "organic rank from DataDive."))
    if MARKETPLACES[mp] and mp not in DD_MARKETPLACE:
        st.warning(f"DataDive doesn't support {MARKETPLACES[mp]['label']}.")
    with st.container(border=True):
        st.markdown("**1 · Get keyword data from DataDive**")
        if not ok_d:
            st.info("Add your DataDive API key to `.env` as `DATADIVE_API_KEY=...` and restart. "
                    "Create it at https://2.datadive.tools/api-key (needs a Standard or Enterprise plan).")
        else:
            c1, c2 = st.columns([1, 1], gap="large")
            with c1:
                st.caption("Use a niche you already dived in DataDive (no tokens used).")
                if st.button("🔄  Find my niches in DataDive", width="stretch"):
                    with st.spinner("Checking your DataDive niches…"):
                        try:
                            st.session_state["dd_niches"] = best_niche_for(DataDiveClient(datadive_key()), mp, work)
                        except Exception as e:
                            st.error(str(e))
                found = st.session_state.get("dd_niches")
                if found is not None:
                    if not found:
                        st.warning("No DataDive niches for this marketplace yet. Create one →")
                    else:
                        pick = st.selectbox("Niche", range(len(found)), format_func=lambda i: (
                            f"{found[i].get('heroKeyword')} · {len(found[i]['overlap'])} of your products"
                            f"{' · incl. yours' if found[i]['has_mine'] else ''} · {found[i]['n_competitors']} competitors"))
                        if st.button("⬇️  Pull keywords for this niche", type="primary", width="stretch"):
                            n = found[pick]
                            with st.spinner("Downloading keywords and competitor ranks…"):
                                try:
                                    dd = DataDiveClient(datadive_key())
                                    kw_rows = parse_keywords(dd.keywords(n["nicheId"]))
                                    comp_rows = parse_competitors(dd.competitors(n["nicheId"]))
                                    db.save_niche(conn, mp, n, kw_rows, comp_rows)
                                    st.success(f"Saved {len(kw_rows)} keywords and {len(comp_rows)} competitors.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
            with c2:
                st.caption("Or start a new DataDive dive seeded with your ASIN (uses dive tokens).")
                n_comp = st.slider("Competitors to analyse", 2, 15, 10)
                agree = st.checkbox(f"I understand this uses about {n_comp} DataDive dive tokens")
                if st.button("🚀  Create dive for my ASIN", disabled=not (agree and my_asin), width="stretch"):
                    with st.status("DataDive is researching your niche (usually 5-20 minutes)…", expanded=True) as stt:
                        try:
                            dd = DataDiveClient(datadive_key())
                            dive = dd.create_dive(mp, my_asin, n_comp)
                            ph = st.empty()
                            nid = run_in_thread(lambda cb: dd.wait_for_dive(dive["diveId"], on_poll=cb),
                                                lambda p: ph.write(f"Status: {p.get('status', 'starting')}"
                                                                   if p else "Starting…"), interval=5)
                            kw_rows, comp_rows = parse_keywords(dd.keywords(nid)), parse_competitors(dd.competitors(nid))
                            db.save_niche(conn, mp, {"nicheId": nid, "heroKeyword": f"dive for {my_asin}"},
                                          kw_rows, comp_rows)
                            stt.update(label=f"Done: {len(kw_rows)} keywords saved", state="complete")
                        except Exception as e:
                            stt.update(label="Dive failed", state="error")
                            st.error(str(e))
    ns = niche_choices()
    if ns.empty:
        html(ui.empty("🔎", "No keyword data yet", "Connect DataDive above to see who wins which search terms."))
    else:
        nid = st.selectbox("Keyword data to analyse", ns.niche_id.tolist(),
                           format_func=lambda i: niche_label(ns[ns.niche_id == i].iloc[0]))
        kws, rk, ddc = db.keywords_df(conn, nid), db.keyword_ranks_df(conn, nid), db.dd_competitors_df(conn, nid)
        tracked = set(rk.asin)
        if my_asin not in tracked:
            st.warning(f"Your ASIN ({my_asin}) isn't tracked in this niche, so it shows as unranked everywhere. "
                       "Create a dive seeded with your ASIN for accurate results.")
        rivals = [c for c in comps if c in tracked] + [a for a in ddc.asin if a != my_asin and a not in comps]
        missing = [c for c in comps if c not in tracked]
        if missing:
            st.caption("Not tracked by this DataDive niche: " + ", ".join(short.get(c, c) for c in missing))
        brand = dict(zip(ddc.asin, ddc.brand))
        name = lambda a: short.get(a) if a in short else f"{brand.get(a) or ''} {a[-4:]}".strip()  # noqa: E731
        cls = kwm.classify_keywords(kws, rk, my_asin, rivals)
        sov = kwm.share_of_voice(kws, rk, [my_asin] + rivals)
        sov["product"] = sov.asin.map(name)
        me_row = sov[sov.asin == my_asin].iloc[0]
        leader = sov.sort_values("sov_pct", ascending=False).iloc[0]
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Keywords", f"{len(kws):,}")
        k2.metric("Monthly searches", f"{int(kws.search_volume.sum()):,}")
        k3.metric("Your share of voice", f"{me_row.sov_pct or 0:.1f}%", help="Estimated share of all search clicks")
        k4.metric("Leader", f"{leader.sov_pct or 0:.1f}%", str(leader["product"]), delta_color="off")
        k5.metric("Your top-10 keywords", int(me_row.keywords_top10))
        c1, c2 = st.columns([1, 1])
        with c1, st.container(border=True):
            st.markdown("**Share of search voice** (estimated)")
            fig = px.bar(sov.sort_values("sov_pct"), x="sov_pct", y="product", orientation="h",
                         color=sov.sort_values("sov_pct").asin.eq(my_asin).map({True: "You", False: "Competitor"}),
                         color_discrete_map={"You": "#f97316", "Competitor": "#94a3b8"},
                         labels={"sov_pct": "% of estimated clicks", "product": "", "color": ""})
            st.plotly_chart(fig.update_layout(**PLOT_LAYOUT, height=60 + 32 * len(sov)), width="stretch")
        with c2, st.container(border=True):
            st.markdown("**Your keyword position**")
            summ = cls.groupby("status").agg(keywords=("keyword", "count"), searches=("search_volume", "sum")).reset_index()
            order = {"win": 0, "contested": 1, "striking": 2, "gap": 3, "open": 4}
            summ = summ.sort_values("status", key=lambda x: x.map(order))
            colors = {"win": "#16a34a", "contested": "#eab308", "striking": "#0ea5e9", "gap": "#dc2626", "open": "#94a3b8"}
            fig = px.bar(summ, x="searches", y="status", orientation="h", color="status", text="keywords",
                         color_discrete_map=colors, labels={"searches": "monthly searches", "status": ""})
            st.plotly_chart(fig.update_layout(**PLOT_LAYOUT, height=260, showlegend=False), width="stretch")
            st.caption("Win = you're top-10 and ahead · Contested = top-10 but behind · Striking = you're 11-20 · "
                       "Gap = a competitor is top-10, you're not · Open = nobody owns it")
        cols_show = ["keyword", "search_volume", "my_rank", "best_competitor", "best_competitor_rank", "bid_median"]
        cfg = {"search_volume": st.column_config.NumberColumn("searches/mo", format="%d"),
               "bid_median": st.column_config.NumberColumn("suggested bid", format="%.2f"),
               "my_rank": st.column_config.NumberColumn("your rank", format="%d"),
               "best_competitor_rank": st.column_config.NumberColumn("their rank", format="%d")}
        t = st.tabs(["🏆 You win", "⚔️ Contested", "🎯 Striking distance", "🚨 Gaps (they win)", "🌱 Open",
                     "🔍 By competitor", "🗺️ Rank matrix", "💼 Competitor business"])
        for tab, status in zip(t[:5], ["win", "contested", "striking", "gap", "open"]):
            with tab:
                d = cls[cls.status == status][cols_show].assign(best_competitor=lambda x: x.best_competitor.map(
                    lambda a: name(a) if isinstance(a, str) else a))
                st.dataframe(d, hide_index=True, width="stretch", column_config=cfg, height=420)
        with t[5]:
            who = st.selectbox("Competitor", rivals, format_func=name)
            w = kwm.competitor_winning_keywords(kws, rk, my_asin, who, limit=100)
            st.caption(f"{len(w)} keywords where {name(who)} is top-10 and beats you · "
                       f"{int(w.search_volume.sum()):,} searches/mo")
            st.dataframe(w, hide_index=True, width="stretch", height=420)
        with t[6]:
            top = kws.head(40).keyword.tolist()
            m = rk[rk.keyword.isin(top) & rk.asin.isin([my_asin] + rivals)].copy()
            m["product"] = m.asin.map(name)
            pv = m.pivot_table(index="keyword", columns="product", values="organic_rank").reindex(top)
            fig = px.imshow(pv, text_auto=True, color_continuous_scale="RdYlGn_r", zmin=1, zmax=50, aspect="auto",
                            labels={"color": "rank", "x": "", "y": ""})
            st.plotly_chart(fig.update_layout(**PLOT_LAYOUT, height=40 + 22 * len(top)), width="stretch")
            st.caption("Organic rank for the 40 biggest keywords (green = top, red = low, blank = not ranked).")
        with t[7]:
            st.dataframe(ddc.drop(columns=["niche_id"]), hide_index=True, width="stretch",
                         column_config={"image": st.column_config.ImageColumn(),
                                        "revenue": st.column_config.NumberColumn(format="%.0f"),
                                        "pct_sv_page1": st.column_config.NumberColumn("% searches on page 1"),
                                        "pct_sv_top_ads": st.column_config.NumberColumn("% searches with top ads")})
            st.caption("Sales and revenue are DataDive monthly estimates.")

# ================= AI ANALYST =================
elif page == P_AI:
    html(ui.hero("AI Analyst", "Step 1 tags every review (emotion, aspect, pain point, persona). "
                 "Step 2 writes the full strategy report and action plan."))
    if not ok_c:
        st.error("Claude key missing. Add ANTHROPIC_API_KEY to the .env file, then restart.")
    model = st.segmented_control("Model", list(CLAUDE_MODELS), default=DEFAULT_MODEL,
                                 format_func=lambda m: CLAUDE_MODELS[m]["label"]) or DEFAULT_MODEL
    todo = analyst.untagged_reviews(conn, mp, work)
    s1, s2 = st.columns(2, gap="large")
    with s1, st.container(border=True):
        st.markdown("#### 1 · Tag reviews")
        done_n = len(rv) - len(todo)
        st.progress(done_n / max(1, len(rv)), f"{done_n:,} of {len(rv):,} reviews tagged")
        st.caption(f"Estimated cost for the rest: ${analyst.estimate_ai_cost(len(todo), 0, model):.2f} USD")
        if st.button("🏷️  Tag untagged reviews", disabled=not (ok_c and len(todo)), width="stretch"):
            bar = st.progress(0.0, "Tagging…")

            def prog(s):
                bar.progress(s["done"] / max(1, s["batches"]),
                             f"Batch {s['done']}/{s['batches']} · tagged {s['tagged']} · ${s['cost_usd']:.2f}")
            stats = analyst.tag_reviews(conn, mp, work, model, progress=prog)
            st.success(f"Tagged {stats['tagged']} reviews · cost ≈ ${stats['cost_usd']:.2f}")
            for e in stats["errors"][:10]:
                st.warning(e)
            time.sleep(1)
            st.rerun()
    with s2, st.container(border=True):
        st.markdown("#### 2 · Strategy report")
        st.markdown(f"**{short.get(my_asin)}** vs **{len(comps)}** competitors")
        ns = niche_choices()
        niche_opts = [None] + ns.niche_id.tolist()
        report_niche = st.selectbox(
            "Search-keyword data", niche_opts,
            index=1 if len(ns) and bool(ns.has_mine.iloc[0]) else 0,
            format_func=lambda i: "None (reviews only)" if i is None else niche_label(ns[ns.niche_id == i].iloc[0]))
        if not len(ns):
            st.caption("💡 Add DataDive keyword data on the Keywords page to include the search battlefield.")
        st.caption(f"Estimated cost ≈ ${analyst.estimate_ai_cost(0, len(work), model):.2f} USD · takes 2–5 minutes")
        if len(todo):
            st.caption("💡 Tag all reviews first. The report uses the tags as its evidence.")
        if st.button("🧠  Write full analysis report", type="primary", disabled=not ok_c, width="stretch"):
            with st.spinner("The strategist is reading all the evidence… (3-6 minutes)"):
                try:
                    res = analyst.write_report(conn, mp, my_asin, comps, model, niche_id=report_niche)
                    st.success(f"Report ready · cost ≈ ${res['cost_usd']:.2f}")
                except Exception as e:
                    st.error(str(e))
    hist = db.df(conn, "select id, created_at, my_asin, model, cost_usd from analyses where marketplace=? "
                       "order by id desc", (mp,))
    if len(hist):
        st.write("")
        pick = st.selectbox("Report", hist.id.tolist(),
                            format_func=lambda i: (lambda r: f"#{r.id} · {r.created_at[:16].replace('T', ' ')} · "
                                                   f"{r.my_asin} · {r.model} · ${r.cost_usd:.2f}")(
                                hist[hist.id == i].iloc[0]))
        rep = db.df(conn, "select * from analyses where id=?", (pick,)).iloc[0]
        items = json.loads(rep.insights_json).get("action_items", [])
        t_plan, t_report = st.tabs(["✅ Action plan", "📄 Full report"])
        with t_plan:
            areas = sorted({a["area"] for a in items})
            pick_areas = st.pills("Area", areas, selection_mode="multi", default=areas,
                                  format_func=lambda x: x.replace("_", " ").title())
            for a in items:
                if a["area"] in (pick_areas or []):
                    html(ui.action_card(a))
        with t_report:
            st.download_button("⬇️ Download report (.md)", rep.report_md,
                               file_name=f"review_report_{rep.my_asin}_{pick}.md")
            with st.container(border=True):
                st.markdown(rep.report_md)

# ================= EXPORT =================
elif page == P_EXPORT:
    html(ui.hero("Export", "Take your data anywhere: Excel for the team, CSV for other tools."))
    c1, c2, c3 = st.columns(3)
    with c1, st.container(border=True):
        st.markdown("#### 📗 Excel workbook")
        st.caption("Summary, products, images, reviews, customer photos, coverage, trend, weighted aspects & emotions, action plan.")
        if st.button("Build Excel file", width="stretch"):
            st.download_button("⬇️ Download Excel", export.to_excel(conn, mp, work), width="stretch",
                               file_name=f"amazon_reviews_{mp}_{time.strftime('%Y%m%d')}.xlsx")
    with c2, st.container(border=True):
        st.markdown("#### 📄 Reviews CSV")
        st.caption(f"{len(rv):,} reviews with AI tags, for Sheets or other tools.")
        st.download_button("⬇️ Download CSV", rv.to_csv(index=False), file_name=f"reviews_{mp}.csv", width="stretch")
    with c3, st.container(border=True):
        st.markdown("#### 🗄️ Database")
        st.caption("SQLite file with all history. Opens in DB Browser for SQLite or Python.")
        st.code(str(DB_PATH), language=None)
