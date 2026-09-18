"""Visual layer: theme CSS and small HTML components for the dashboard.

All user/Amazon text goes through esc() before it is placed in HTML.
"""
from __future__ import annotations

import html
import json

import pandas as pd

STAR_COLORS = {5: "#16a34a", 4: "#84cc16", 3: "#eab308", 2: "#f97316", 1: "#dc2626"}
EMOTION_COLORS = {
    "delight": "#16a34a", "satisfaction": "#22c55e", "trust": "#0ea5e9", "relief": "#14b8a6",
    "surprise": "#a855f7", "neutral": "#94a3b8", "confusion": "#f59e0b", "anxiety": "#fb923c",
    "disappointment": "#f97316", "frustration": "#ef4444", "anger": "#b91c1c", "regret": "#7f1d1d",
}
PRODUCT_PALETTE = ["#f97316", "#2563eb", "#16a34a", "#9333ea", "#0891b2", "#db2777",
                   "#65a30d", "#ca8a04", "#4f46e5", "#0d9488", "#be123c"]

CSS = """
<style>
:root{
  --ri-accent:#f97316; --ri-accent-soft:#fff3e8; --ri-ink:#0f172a; --ri-muted:#64748b;
  --ri-line:#e2e8f0; --ri-card:#ffffff; --ri-bg:#f8fafc; --ri-good:#16a34a; --ri-bad:#dc2626;
}
.stApp{background:var(--ri-bg);}
.block-container{padding-top:1.4rem; max-width:1400px;}
h1,h2,h3{letter-spacing:-.01em; color:var(--ri-ink);}
[data-testid="stSidebar"]{background:#0f172a;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{color:#e2e8f0;}
[data-testid="stSidebar"] label[data-testid="stRadioOption"]{padding:.5rem .7rem; border-radius:10px; margin:2px 0;
  width:100%; transition:background .15s;}
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover{background:#1e293b;}
[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div:first-child:not([data-testid]){display:none;}
[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"]{background:var(--ri-accent);}
[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] p{color:#fff !important; font-weight:600;}
[data-testid="stSidebar"] hr{border-color:#1e293b;}
[data-testid="stMetric"]{background:var(--ri-card); border:1px solid var(--ri-line); border-radius:14px;
  padding:14px 16px; box-shadow:0 1px 2px rgba(15,23,42,.04);}
[data-testid="stMetricLabel"] p{color:var(--ri-muted); font-size:.8rem; text-transform:uppercase; letter-spacing:.04em;}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:14px;}
div[data-testid="stExpander"] details{border-radius:12px; background:var(--ri-card);}
.stButton button[kind="primary"]{background:var(--ri-accent); border-color:var(--ri-accent); font-weight:600;}
.stButton button[kind="primary"]:hover{background:#ea580c; border-color:#ea580c;}

.ri-hero{background:linear-gradient(120deg,#0f172a 0%,#1e293b 60%,#7c2d12 140%); color:#fff;
  border-radius:18px; padding:22px 26px; margin-bottom:18px;}
.ri-hero h1{color:#fff; margin:0; font-size:1.7rem;}
.ri-hero p{color:#cbd5e1; margin:.3rem 0 0 0;}
.ri-chip{display:inline-block; padding:2px 10px; border-radius:999px; font-size:.75rem; font-weight:600;
  margin:2px 4px 2px 0; border:1px solid transparent; white-space:nowrap;}
.ri-chip.gray{background:#f1f5f9; color:#334155;}
.ri-chip.green{background:#dcfce7; color:#166534;}
.ri-chip.red{background:#fee2e2; color:#991b1b;}
.ri-chip.amber{background:#fef3c7; color:#92400e;}
.ri-chip.blue{background:#dbeafe; color:#1e40af;}
.ri-chip.orange{background:var(--ri-accent-soft); color:#9a3412;}
.ri-chip.dark{background:#0f172a; color:#fff;}
.ri-hero .ri-chip.gray{background:rgba(255,255,255,.12); color:#fff;}

.ri-card{background:var(--ri-card); border:1px solid var(--ri-line); border-radius:16px; padding:16px;
  box-shadow:0 1px 3px rgba(15,23,42,.05); height:100%;}
.ri-card.mine{border:2px solid var(--ri-accent); box-shadow:0 4px 14px rgba(249,115,22,.15);}
.ri-pimg{width:100%; height:170px; object-fit:contain; background:#fff; border-radius:10px;}
.ri-ptitle{font-weight:600; color:var(--ri-ink); font-size:.92rem; line-height:1.3; margin:.5rem 0 .3rem;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; min-height:2.4em;}
.ri-brand{color:var(--ri-muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.05em;}
.ri-price{font-size:1.35rem; font-weight:700; color:var(--ri-ink);}
.ri-price small{font-size:.75rem; color:var(--ri-muted); font-weight:500;}
.ri-stars{color:#f59e0b; letter-spacing:1px; font-size:1rem;}
.ri-stars .off{color:#e2e8f0;}
.ri-muted{color:var(--ri-muted); font-size:.8rem;}
.ri-bars{margin-top:8px;}
.ri-bar{display:flex; align-items:center; gap:6px; font-size:.72rem; color:var(--ri-muted); margin:2px 0;}
.ri-bar .track{flex:1; height:7px; background:#f1f5f9; border-radius:99px; overflow:hidden;}
.ri-bar .fill{height:100%; border-radius:99px;}
.ri-bar .lbl{width:18px;} .ri-bar .val{width:34px; text-align:right;}

.ri-review{background:var(--ri-card); border:1px solid var(--ri-line); border-left:4px solid var(--c);
  border-radius:12px; padding:14px 16px; margin-bottom:10px;}
.ri-review .head{display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;}
.ri-review .title{font-weight:650; color:var(--ri-ink); margin:.35rem 0 .2rem;}
.ri-review .body{color:#334155; font-size:.9rem; line-height:1.5; white-space:pre-wrap;}
.ri-review .imgs{display:flex; gap:6px; margin-top:8px; flex-wrap:wrap;}
.ri-review .imgs img{width:74px; height:74px; object-fit:cover; border-radius:8px; border:1px solid var(--ri-line);}
.ri-review .insight{margin-top:8px; font-size:.82rem; color:#334155; background:#f8fafc; border-radius:8px; padding:6px 10px;}

.ri-step{display:flex; gap:12px; align-items:flex-start; padding:12px 14px; border-radius:12px;
  background:var(--ri-card); border:1px solid var(--ri-line); height:100%;}
.ri-step .n{width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  font-weight:700; font-size:.85rem; flex:none; background:#e2e8f0; color:#475569;}
.ri-step.done .n{background:var(--ri-good); color:#fff;}
.ri-step.now{border-color:var(--ri-accent); box-shadow:0 0 0 3px var(--ri-accent-soft);}
.ri-step.now .n{background:var(--ri-accent); color:#fff;}
.ri-step b{color:var(--ri-ink); font-size:.9rem;} .ri-step div div{font-size:.78rem; color:var(--ri-muted);}

.ri-action{background:var(--ri-card); border:1px solid var(--ri-line); border-radius:14px; padding:14px 16px; margin-bottom:10px;
  display:flex; gap:14px;}
.ri-action .p{font-size:1.3rem; font-weight:800; color:var(--ri-accent); min-width:34px;}
.ri-action .a{font-weight:600; color:var(--ri-ink); margin:.25rem 0;}
.ri-action .e{font-size:.82rem; color:var(--ri-muted);}
.ri-report{background:var(--ri-card); border:1px solid var(--ri-line); border-radius:16px; padding:8px 28px 20px;}
.ri-photo{position:relative; border-radius:12px; overflow:hidden; border:1px solid var(--ri-line); background:#fff; margin-bottom:12px;}
.ri-photo img{width:100%; height:190px; object-fit:cover; display:block;}
.ri-photo .cap{padding:8px 10px; font-size:.78rem; color:#334155;}
.ri-photo .star{position:absolute; top:8px; left:8px;}
.ri-empty{text-align:center; padding:48px 20px; color:var(--ri-muted); background:var(--ri-card);
  border:1px dashed #cbd5e1; border-radius:16px;}
.ri-empty .big{font-size:2.4rem;}
</style>
"""


def esc(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return html.escape(str(x))


def chip(text, color="gray") -> str:
    return f'<span class="ri-chip {color}">{esc(text)}</span>'


def stars(value, size="1rem") -> str:
    if value is None or pd.isna(value):
        return ""
    v = float(value)
    full = int(round(v))
    return (f'<span class="ri-stars" style="font-size:{size}">' + "★" * full +
            f'<span class="off">{"★" * (5 - full)}</span></span>')


def star_bars(row) -> str:
    out = ['<div class="ri-bars">']
    for s in [5, 4, 3, 2, 1]:
        v = row.get(f"pct_{s}")
        pct = 0 if v is None or pd.isna(v) else 100 * float(v)
        out.append(f'<div class="ri-bar"><span class="lbl">{s}★</span><div class="track">'
                   f'<div class="fill" style="width:{pct:.1f}%;background:{STAR_COLORS[s]}"></div></div>'
                   f'<span class="val">{pct:.0f}%</span></div>')
    out.append("</div>")
    return "".join(out)


def fmt_money(price, currency) -> str:
    if price is None or pd.isna(price):
        return '<span class="ri-muted">no price</span>'
    return f'{float(price):,.2f} <small>{esc(currency)}</small>'


def product_card(p: dict, image_url: str | None, mine: bool) -> str:
    badges = []
    if mine:
        badges.append(chip("YOUR PRODUCT", "dark"))
    if p.get("in_stock") == 0:
        badges.append(chip("Out of stock", "red"))
    if p.get("is_amazon_choice"):
        badges.append(chip("Amazon's Choice", "blue"))
    if p.get("has_aplus"):
        badges.append(chip("A+", "green"))
    if p.get("videos_count"):
        badges.append(chip(f"{int(p['videos_count'])} video", "gray"))
    ratings = p.get("ratings_total")
    rating_txt = f"{float(p['stars']):.1f}" if p.get("stars") is not None and not pd.isna(p.get("stars")) else "–"
    img = f'<img class="ri-pimg" src="{esc(image_url)}">' if image_url else '<div class="ri-pimg"></div>'
    return f"""
<div class="ri-card {'mine' if mine else ''}">
  {img}
  <div style="margin-top:8px">{''.join(badges)}</div>
  <div class="ri-ptitle">{esc(p.get('title'))}</div>
  <div class="ri-brand">{esc(p.get('brand'))} · {esc(p.get('asin'))}</div>
  <div style="display:flex;justify-content:space-between;align-items:end;margin-top:6px">
    <div class="ri-price">{fmt_money(p.get('price'), p.get('currency'))}</div>
    <div style="text-align:right">{stars(p.get('stars'))} <b>{rating_txt}</b><br>
      <span class="ri-muted">{f"{int(ratings):,}" if ratings and not pd.isna(ratings) else "–"} ratings</span></div>
  </div>
  <div class="ri-muted" style="margin-top:4px">{esc(p.get('monthly_bought') or '')}</div>
  {star_bars(p)}
</div>"""


def review_card(r: dict, product_label: str) -> str:
    rating = int(r["rating"]) if r.get("rating") is not None and not pd.isna(r.get("rating")) else 0
    chips = [chip(product_label, "gray")]
    if r.get("verified"):
        chips.append(chip("✓ Verified", "green"))
    if r.get("vine"):
        chips.append(chip("Vine", "blue"))
    if r.get("helpful_votes"):
        chips.append(chip(f"👍 {int(r['helpful_votes'])} helpful", "gray"))
    emo = r.get("emotion")
    if isinstance(emo, str) and emo:
        chips.append(f'<span class="ri-chip" style="background:{EMOTION_COLORS.get(emo, "#94a3b8")}22;'
                     f'color:{EMOTION_COLORS.get(emo, "#334155")}">{esc(emo)}</span>')
    imgs = [u for u in str(r.get("image_urls") or "").split(" ") if u]
    img_html = ('<div class="imgs">' + "".join(f'<a href="{esc(u)}" target="_blank"><img src="{esc(u)}"></a>'
                                               for u in imgs[:8]) + "</div>") if imgs else ""
    insight = []
    if isinstance(r.get("pain_point"), str) and r["pain_point"]:
        insight.append(f"<b style='color:#dc2626'>Pain:</b> {esc(r['pain_point'])}")
    if isinstance(r.get("praise"), str) and r["praise"]:
        insight.append(f"<b style='color:#16a34a'>Praise:</b> {esc(r['praise'])}")
    aspects = r.get("aspects")
    if isinstance(aspects, str) and aspects not in ("", "[]"):
        insight.append(" ".join(chip(a.replace("_", " "), "orange") for a in json.loads(aspects)))
    insight_html = f'<div class="insight">{" &nbsp;·&nbsp; ".join(insight)}</div>' if insight else ""
    variant = f' · {esc(r.get("variant"))}' if r.get("variant") else ""
    link = f' · <a href="{esc(r.get("review_url"))}" target="_blank">open on Amazon ↗</a>' if r.get("review_url") else ""
    return f"""
<div class="ri-review" style="--c:{STAR_COLORS.get(rating, '#94a3b8')}">
  <div class="head"><div>{stars(rating)} <span class="ri-muted">{esc(r.get('review_date'))}{variant}{link}</span></div>
  <div>{''.join(chips)}</div></div>
  <div class="title">{esc(r.get('title'))}</div>
  <div class="body">{esc(r.get('body'))}</div>
  {img_html}{insight_html}
</div>"""


def step(n: int, title: str, sub: str, state: str) -> str:
    mark = "✓" if state == "done" else str(n)
    return f'<div class="ri-step {state}"><div class="n">{mark}</div><div><b>{esc(title)}</b><div>{esc(sub)}</div></div></div>'


def action_card(a: dict) -> str:
    impact = {"high": "green", "medium": "amber", "low": "gray"}.get(a.get("expected_impact"), "gray")
    effort = {"low": "green", "medium": "amber", "high": "red"}.get(a.get("effort"), "gray")
    return f"""
<div class="ri-action"><div class="p">#{esc(a.get('priority'))}</div><div>
  {chip(str(a.get('area', '')).replace('_', ' ').title(), 'orange')}
  {chip('Impact: ' + str(a.get('expected_impact')), impact)} {chip('Effort: ' + str(a.get('effort')), effort)}
  <div class="a">{esc(a.get('action'))}</div>
  <div class="e"><b>Evidence:</b> {esc(a.get('evidence'))}</div>
</div></div>"""


def photo_tile(url: str, rating, label: str, title: str, date, link) -> str:
    r = int(rating) if rating is not None and not pd.isna(rating) else 0
    return f"""
<div class="ri-photo"><a href="{esc(link or url)}" target="_blank"><img src="{esc(url)}" loading="lazy"></a>
  <span class="ri-chip star" style="background:{STAR_COLORS.get(r, '#64748b')};color:#fff">{r}★</span>
  <div class="cap"><b>{esc(label)}</b> · {esc(date)}<br>{esc((title or '')[:70])}</div></div>"""


def empty(icon: str, title: str, sub: str) -> str:
    return f'<div class="ri-empty"><div class="big">{icon}</div><h3>{esc(title)}</h3><p>{esc(sub)}</p></div>'


def hero(title: str, subtitle: str, chips_html: str = "") -> str:
    return f'<div class="ri-hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p><div style="margin-top:10px">{chips_html}</div></div>'
