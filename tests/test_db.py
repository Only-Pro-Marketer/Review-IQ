import json
from pathlib import Path

from review_intel import db
from review_intel.parse import parse_items

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "apify_reviews_sample.json").read_text())


def _conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def test_save_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    parsed = parse_items(FIXTURE, "US")
    db.save_parsed(conn, parsed, "US", roles={"B0CWXNS552": "mine"})
    db.save_parsed(conn, parsed, "US", roles={"B0CWXNS552": "mine"})  # double run
    assert conn.execute("select count(*) from reviews").fetchone()[0] == 5
    assert conn.execute("select count(*) from products").fetchone()[0] == 1
    n_img = conn.execute("select count(*) from product_images").fetchone()[0]
    assert n_img == len(parsed["products"]["B0CWXNS552"]["images"])
    assert conn.execute("select count(*) from star_coverage").fetchone()[0] == 1


def test_review_update_changes_fields_not_rowcount(tmp_path):
    conn = _conn(tmp_path)
    parsed = parse_items(FIXTURE, "US")
    db.save_parsed(conn, parsed, "US")
    parsed["reviews"][0]["helpful_votes"] = 42
    parsed["reviews"][0]["images"] = ["https://x/1.jpg", "https://x/2.jpg"]
    db.save_parsed(conn, parsed, "US")
    rid = parsed["reviews"][0]["review_id"]
    assert conn.execute("select helpful_votes from reviews where review_id=?", (rid,)).fetchone()[0] == 42
    assert conn.execute("select count(*) from review_images where review_id=?", (rid,)).fetchone()[0] == 2
    assert conn.execute("select count(*) from reviews").fetchone()[0] == 5


def test_same_review_id_different_marketplace_kept_separately(tmp_path):
    conn = _conn(tmp_path)
    db.save_parsed(conn, parse_items(FIXTURE, "US"), "US")
    db.save_parsed(conn, parse_items(FIXTURE, "CA"), "CA")
    assert conn.execute("select count(*) from reviews").fetchone()[0] == 10


def test_role_update(tmp_path):
    conn = _conn(tmp_path)
    parsed = parse_items(FIXTURE, "US")
    db.save_parsed(conn, parsed, "US", roles={"B0CWXNS552": "competitor"})
    db.set_roles(conn, "US", {"B0CWXNS552": "mine"})
    assert conn.execute("select role from products").fetchone()[0] == "mine"


def test_tags_upsert(tmp_path):
    conn = _conn(tmp_path)
    db.save_parsed(conn, parse_items(FIXTURE, "US"), "US")
    tag = {"review_id": "RK4SEUGGJK3B9", "sentiment": -0.8, "emotion": "frustration", "intensity": 3,
           "aspects": ["performance"], "topic": "can't locate item", "pain_point": "lost keys", "praise": "",
           "use_case": "keys", "persona": "", "purchase_driver": ""}
    db.save_tags(conn, "US", [tag], model="m")
    tag["emotion"] = "anger"
    db.save_tags(conn, "US", [tag], model="m")
    rows = conn.execute("select emotion from review_tags").fetchall()
    assert rows == [("anger",)]
