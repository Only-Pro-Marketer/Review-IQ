"""SQLite storage. Every write is an upsert, so re-running a pull never duplicates rows."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA = """
create table if not exists products (
  asin text not null, marketplace text not null, role text default 'competitor',
  title text, brand text, url text, price real, list_price real, currency text,
  stars real, ratings_total integer,
  pct_5 real, pct_4 real, pct_3 real, pct_2 real, pct_1 real,
  monthly_bought text, in_stock integer, stock_text text, seller text,
  is_amazon_choice integer, videos_count integer, variant_count integer,
  bestseller_ranks text, breadcrumbs text, features text, description text,
  attributes text, has_aplus integer, has_brand_story integer,
  ai_summary text, ai_keywords text, thumbnail text, updated_at text,
  primary key (asin, marketplace)
);
create table if not exists product_images (
  asin text not null, marketplace text not null, position integer not null,
  url text not null, local_path text,
  primary key (asin, marketplace, position)
);
create table if not exists reviews (
  review_id text not null, marketplace text not null, asin text not null,
  variant_asin text, rating integer, title text, body text, review_date text,
  reviewer_country text, verified integer, vine integer, helpful_votes integer,
  variant text, review_url text, image_count integer default 0,
  filter_star integer, filter_keyword text, first_seen text, updated_at text,
  primary key (review_id, marketplace)
);
create index if not exists ix_reviews_asin on reviews(marketplace, asin, rating);
create table if not exists review_images (
  review_id text not null, marketplace text not null, position integer not null,
  url text not null, local_path text,
  primary key (review_id, marketplace, position)
);
create table if not exists star_coverage (
  asin text not null, marketplace text not null, star integer not null,
  available_reviews integer, available_ratings integer, updated_at text,
  primary key (asin, marketplace, star)
);
create table if not exists review_tags (
  review_id text not null, marketplace text not null,
  sentiment real, emotion text, intensity integer, aspects text, topic text,
  pain_point text, praise text, use_case text, persona text, purchase_driver text,
  model text, tagged_at text,
  primary key (review_id, marketplace)
);
create table if not exists runs (
  id integer primary key autoincrement, started_at text, finished_at text,
  marketplace text, asins text, settings text, status text,
  apify_run_ids text, reviews_saved integer, est_cost_usd real, errors text
);
create table if not exists kw_niches (
  niche_id text primary key, marketplace text, hero_keyword text, label text,
  research_date text, pulled_at text
);
create table if not exists keywords (
  niche_id text not null, keyword text not null, search_volume integer, relevancy real,
  is_outlier integer, bid_min real, bid_median real, bid_max real,
  primary key (niche_id, keyword)
);
create table if not exists keyword_ranks (
  niche_id text not null, keyword text not null, asin text not null,
  organic_rank integer, source text default 'datadive',
  primary key (niche_id, keyword, asin)
);
create index if not exists ix_kr_asin on keyword_ranks(niche_id, asin);
create table if not exists dd_competitors (
  niche_id text not null, asin text not null, brand text, title text, image text, bsr integer,
  price real, rating real, review_count integer, sales integer, revenue real, kw_page1 integer,
  pct_sv_page1 real, advertised_kws integer, pct_sv_top_ads real, ranking_juice real,
  fulfillment text, variations integer, category text,
  primary key (niche_id, asin)
);
create table if not exists analyses (
  id integer primary key autoincrement, created_at text, marketplace text,
  my_asin text, competitor_asins text, model text, report_md text,
  insights_json text, input_tokens integer, output_tokens integer, cost_usd real
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn


def _upsert(conn, table: str, row: dict, keys: tuple, keep_on_update: tuple = ()):
    cols = list(row)
    updates = [c for c in cols if c not in keys and c not in keep_on_update]
    sql = (f"insert into {table} ({','.join(cols)}) values ({','.join('?' * len(cols))}) "
           f"on conflict ({','.join(keys)}) do update set "
           + ",".join(f"{c}=excluded.{c}" for c in updates))
    conn.execute(sql, [row[c] for c in cols])


def _replace_images(conn, table, key_cols: dict, urls: list[str]):
    where = " and ".join(f"{k}=?" for k in key_cols)
    existing = dict(conn.execute(f"select url, local_path from {table} where {where}",
                                 list(key_cols.values())).fetchall())
    conn.execute(f"delete from {table} where {where}", list(key_cols.values()))
    for i, u in enumerate(urls):
        conn.execute(
            f"insert into {table} ({','.join(key_cols)}, position, url, local_path) "
            f"values ({','.join('?' * len(key_cols))}, ?, ?, ?)",
            [*key_cols.values(), i, u, existing.get(u)])


def save_parsed(conn, parsed: dict, marketplace: str, roles: dict | None = None) -> int:
    roles = roles or {}
    ts = now()
    with conn:
        for asin, p in parsed["products"].items():
            b = p["star_breakdown"]
            row = {
                "asin": asin, "marketplace": marketplace, "role": roles.get(asin, "competitor"),
                "title": p["title"], "brand": p["brand"], "url": p["url"], "price": p["price"],
                "list_price": p["list_price"], "currency": p["currency"], "stars": p["stars"],
                "ratings_total": p["ratings_total"],
                **{f"pct_{s}": b.get(s) for s in range(1, 6)},
                "monthly_bought": p["monthly_bought"], "in_stock": p["in_stock"],
                "stock_text": p["stock_text"], "seller": p["seller"],
                "is_amazon_choice": int(p["is_amazon_choice"]), "videos_count": p["videos_count"],
                "variant_count": p["variant_count"],
                "bestseller_ranks": json.dumps(p["bestseller_ranks"]) if p["bestseller_ranks"] else None,
                "breadcrumbs": p["breadcrumbs"], "features": json.dumps(p["features"]),
                "description": p["description"], "attributes": json.dumps(p["attributes"]),
                "has_aplus": int(p["has_aplus"]), "has_brand_story": int(p["has_brand_story"]),
                "ai_summary": p["ai_summary"], "ai_keywords": json.dumps(p["ai_keywords"]),
                "thumbnail": p["thumbnail"], "updated_at": ts,
            }
            _upsert(conn, "products", row, ("asin", "marketplace"),
                    keep_on_update=() if asin in roles else ("role",))
            _replace_images(conn, "product_images", {"asin": asin, "marketplace": marketplace}, p["images"])
        for (asin, star), c in parsed["coverage"].items():
            _upsert(conn, "star_coverage", {"asin": asin, "marketplace": marketplace, "star": star,
                                            **c, "updated_at": ts}, ("asin", "marketplace", "star"))
        for r in parsed["reviews"]:
            row = {k: v for k, v in r.items() if k != "images"}
            row.update(marketplace=marketplace, verified=int(r["verified"]), vine=int(r["vine"]),
                       image_count=len(r["images"]), first_seen=ts, updated_at=ts)
            _upsert(conn, "reviews", row, ("review_id", "marketplace"), keep_on_update=("first_seen",))
            _replace_images(conn, "review_images",
                            {"review_id": r["review_id"], "marketplace": marketplace}, r["images"])
    return len(parsed["reviews"])


def set_roles(conn, marketplace: str, roles: dict):
    with conn:
        for asin, role in roles.items():
            conn.execute("update products set role=? where asin=? and marketplace=?", (role, asin, marketplace))


def save_tags(conn, marketplace: str, tags: list[dict], model: str):
    ts = now()
    with conn:
        for t in tags:
            row = dict(t)
            row["aspects"] = json.dumps(row.get("aspects") or [])
            row.update(marketplace=marketplace, model=model, tagged_at=ts)
            _upsert(conn, "review_tags", row, ("review_id", "marketplace"))


def set_local_path(conn, table: str, url: str, path: str):
    with conn:
        conn.execute(f"update {table} set local_path=? where url=?", (path, url))


# ---------- reads ----------

def df(conn, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def products_df(conn, marketplace: str, asins: list[str] | None = None) -> pd.DataFrame:
    d = df(conn, "select * from products where marketplace=?", (marketplace,))
    return d[d.asin.isin(asins)] if asins else d


def reviews_df(conn, marketplace: str, asins: list[str] | None = None) -> pd.DataFrame:
    d = df(conn, """
        select r.*, t.sentiment, t.emotion, t.intensity, t.aspects, t.topic, t.pain_point,
               t.praise, t.use_case, t.persona, t.purchase_driver,
               (select group_concat(url, ' ') from review_images i
                 where i.review_id=r.review_id and i.marketplace=r.marketplace) as image_urls
        from reviews r left join review_tags t
          on t.review_id=r.review_id and t.marketplace=r.marketplace
        where r.marketplace=?""", (marketplace,))
    return d[d.asin.isin(asins)] if asins else d


def coverage_df(conn, marketplace: str) -> pd.DataFrame:
    return df(conn, "select * from star_coverage where marketplace=?", (marketplace,))


def product_images_df(conn, marketplace: str) -> pd.DataFrame:
    return df(conn, "select * from product_images where marketplace=? order by asin, position", (marketplace,))


def review_images_df(conn, marketplace: str) -> pd.DataFrame:
    return df(conn, """select i.*, r.asin, r.rating, r.title, r.review_date, r.review_url
                       from review_images i join reviews r
                       on r.review_id=i.review_id and r.marketplace=i.marketplace
                       where i.marketplace=? order by r.asin, r.rating, r.review_date desc""", (marketplace,))


def save_niche(conn, marketplace: str, niche: dict, keywords: list[dict], competitors: list[dict]):
    """Replace one DataDive niche snapshot (keywords, ranks, competitors) atomically."""
    nid = niche["nicheId"]
    with conn:
        for t in ("keywords", "keyword_ranks", "dd_competitors"):
            conn.execute(f"delete from {t} where niche_id=?", (nid,))
        _upsert(conn, "kw_niches", {"niche_id": nid, "marketplace": marketplace,
                                    "hero_keyword": niche.get("heroKeyword"), "label": niche.get("nicheLabel"),
                                    "research_date": niche.get("latestResearchDate"), "pulled_at": now()},
                ("niche_id",))
        for k in keywords:
            conn.execute("insert into keywords values (?,?,?,?,?,?,?,?)",
                         (nid, k["keyword"], k["search_volume"], k["relevancy"], int(k["is_outlier"]),
                          k["bid_min"], k["bid_median"], k["bid_max"]))
            for asin, rank in k["ranks"].items():
                conn.execute("insert into keyword_ranks values (?,?,?,?,?)", (nid, k["keyword"], asin, rank, "datadive"))
        for c in competitors:
            conn.execute(f"insert into dd_competitors (niche_id,{','.join(c)}) values (?{',?' * len(c)})",
                         (nid, *c.values()))


def niches_df(conn, marketplace: str | None = None) -> pd.DataFrame:
    if marketplace:
        return df(conn, "select * from kw_niches where marketplace=? order by pulled_at desc", (marketplace,))
    return df(conn, "select * from kw_niches order by pulled_at desc")


def keywords_df(conn, niche_id: str) -> pd.DataFrame:
    return df(conn, "select * from keywords where niche_id=? order by search_volume desc", (niche_id,))


def keyword_ranks_df(conn, niche_id: str) -> pd.DataFrame:
    return df(conn, "select keyword, asin, organic_rank, source from keyword_ranks where niche_id=?", (niche_id,))


def dd_competitors_df(conn, niche_id: str) -> pd.DataFrame:
    return df(conn, "select * from dd_competitors where niche_id=? order by sales desc", (niche_id,))


def log_run(conn, **fields) -> int:
    with conn:
        if "id" in fields:
            rid = fields.pop("id")
            sets = ",".join(f"{k}=?" for k in fields)
            conn.execute(f"update runs set {sets} where id=?", [*fields.values(), rid])
            return rid
        cur = conn.execute(f"insert into runs ({','.join(fields)}) values ({','.join('?' * len(fields))})",
                           list(fields.values()))
        return cur.lastrowid


def save_analysis(conn, **fields) -> int:
    with conn:
        cur = conn.execute(f"insert into analyses ({','.join(fields)}) values ({','.join('?' * len(fields))})",
                           list(fields.values()))
        return cur.lastrowid
