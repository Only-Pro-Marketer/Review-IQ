"""Download listing images and customer review photos to data/images/."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from . import db
from .config import IMAGES_DIR


def _target(kind: str, marketplace: str, asin: str, url: str) -> Path:
    ext = Path(url.split("?")[0]).suffix or ".jpg"
    name = hashlib.md5(url.encode()).hexdigest()[:16] + ext
    return IMAGES_DIR / marketplace / asin / kind / name


def _fetch(url: str, path: Path) -> bool:
    if path.exists() and path.stat().st_size > 0:
        return True
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return True
    except requests.RequestException:
        pass
    return False


def download_all(conn, marketplace: str, asins: list[str], include_reviews: bool = True) -> dict:
    jobs = []
    for row in db.product_images_df(conn, marketplace).itertuples():
        if row.asin in asins:
            jobs.append(("product_images", row.url, _target("listing", marketplace, row.asin, row.url)))
    if include_reviews:
        for row in db.review_images_df(conn, marketplace).itertuples():
            if row.asin in asins:
                jobs.append(("review_images", row.url, _target("customer", marketplace, row.asin, row.url)))
    ok = failed = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda j: (j, _fetch(j[1], j[2])), jobs))
    for (table, url, path), success in results:
        if success:
            db.set_local_path(conn, table, url, str(path))
            ok += 1
        else:
            failed += 1
    return {"downloaded": ok, "failed": failed}
