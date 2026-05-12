"""dedup_db: SQLite dedup store — schema, insert idempotency, filter, migration."""

import sqlite3
from pathlib import Path
import pytest

import dedup_db


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh leads.db in a temp directory, never touches the real one."""
    db_path = tmp_path / "test_leads.db"
    return dedup_db.connect(db_path)


def _make_lead(tid: str, **overrides):
    base = {
        "twogis_id": tid,
        "name": f"Biz {tid}",
        "city": "almaty",
        "niche": "hair_beauty",
        "phone": "+77011112233",
        "phone_type": "mobile",
        "owner_name": "",
        "owner_phone": "",
        "owner_instagram": "",
        "owner_ig_source": "",
        "company_instagram": "",
        "website": "",
        "has_website": False,
        "contact_method": "phone",
        "data_source": "apify",
        "twogis_url": f"https://2gis.kz/firm/{tid}",
        "address": "ул. Тест 1",
    }
    base.update(overrides)
    return base


# ─── schema + migration ───────────────────────────────────────────────────────

def test_connect_creates_schema(tmp_db):
    cols = {r["name"] for r in tmp_db.execute("PRAGMA table_info(leads)").fetchall()}
    expected = {"twogis_id", "name", "city", "niche", "phone", "phone_type",
                "owner_name", "owner_phone", "owner_instagram", "owner_ig_source",
                "company_instagram", "website", "has_website", "contact_method",
                "data_source", "twogis_url", "address", "discovered_at", "sheet_row"}
    assert expected.issubset(cols)


def test_migration_adds_owner_ig_source_to_old_db(tmp_path):
    """A pre-existing DB without owner_ig_source should auto-migrate on connect."""
    db_path = tmp_path / "old.db"
    # Hand-craft an "old" leads.db without owner_ig_source.
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        CREATE TABLE leads (
            twogis_id TEXT PRIMARY KEY,
            name TEXT, city TEXT, niche TEXT,
            phone TEXT, phone_type TEXT,
            owner_name TEXT, owner_phone TEXT,
            owner_instagram TEXT, company_instagram TEXT,
            website TEXT, has_website INTEGER, contact_method TEXT,
            data_source TEXT, twogis_url TEXT, address TEXT,
            discovered_at DATETIME, sheet_row INTEGER
        );
        INSERT INTO leads (twogis_id, name) VALUES ('legacy_1', 'Old Lead');
    """)
    raw.commit()
    raw.close()

    # Connect via our helper — should migrate.
    conn = dedup_db.connect(db_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    assert "owner_ig_source" in cols

    # Old data is preserved.
    row = conn.execute("SELECT * FROM leads WHERE twogis_id='legacy_1'").fetchone()
    assert row["name"] == "Old Lead"
    assert row["owner_ig_source"] == "" or row["owner_ig_source"] is None


# ─── insert_lead idempotency ─────────────────────────────────────────────────

def test_insert_lead_first_time_returns_true(tmp_db):
    assert dedup_db.insert_lead(tmp_db, _make_lead("biz1")) is True


def test_insert_lead_duplicate_returns_false(tmp_db):
    lead = _make_lead("biz1")
    dedup_db.insert_lead(tmp_db, lead)
    # Second insert with same twogis_id is INSERT OR IGNORE → returns False.
    assert dedup_db.insert_lead(tmp_db, lead) is False


def test_insert_lead_persists_all_fields(tmp_db):
    lead = _make_lead("biz1", owner_name="Иван Иванов",
                     owner_instagram="ivan_iv", owner_ig_source="serper-auto",
                     contact_method="owner_ig")
    dedup_db.insert_lead(tmp_db, lead)
    row = tmp_db.execute("SELECT * FROM leads WHERE twogis_id='biz1'").fetchone()
    assert row["owner_name"] == "Иван Иванов"
    assert row["owner_instagram"] == "ivan_iv"
    assert row["owner_ig_source"] == "serper-auto"
    assert row["contact_method"] == "owner_ig"


def test_insert_lead_with_sheet_row(tmp_db):
    dedup_db.insert_lead(tmp_db, _make_lead("biz1"), sheet_row=42)
    row = tmp_db.execute("SELECT sheet_row FROM leads WHERE twogis_id='biz1'").fetchone()
    assert row["sheet_row"] == 42


def test_insert_lead_persists_size_signals(tmp_db):
    lead = _make_lead("biz1", size_estimate="sweet_spot",
                     review_count=75, rating_count=140, branch_count=2)
    dedup_db.insert_lead(tmp_db, lead)
    row = tmp_db.execute("SELECT * FROM leads WHERE twogis_id='biz1'").fetchone()
    assert row["size_estimate"] == "sweet_spot"
    assert row["review_count"] == 75
    assert row["rating_count"] == 140
    assert row["branch_count"] == 2


def test_migration_adds_size_columns_to_pre_v6_db(tmp_path):
    """A pre-existing DB without size columns should auto-migrate on connect."""
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        CREATE TABLE leads (
            twogis_id TEXT PRIMARY KEY,
            name TEXT, city TEXT, niche TEXT,
            phone TEXT, phone_type TEXT,
            owner_name TEXT, owner_phone TEXT,
            owner_instagram TEXT, owner_ig_source TEXT,
            company_instagram TEXT,
            website TEXT, has_website INTEGER, contact_method TEXT,
            data_source TEXT, twogis_url TEXT, address TEXT,
            discovered_at DATETIME, sheet_row INTEGER
        );
        INSERT INTO leads (twogis_id, name) VALUES ('legacy_1', 'Old Lead');
    """)
    raw.commit()
    raw.close()

    conn = dedup_db.connect(db_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    for required in {"size_estimate", "review_count", "rating_count", "branch_count"}:
        assert required in cols, f"Migration didn't add {required}"

    row = conn.execute("SELECT * FROM leads WHERE twogis_id='legacy_1'").fetchone()
    assert row["name"] == "Old Lead"
    # New columns should have sensible defaults for legacy rows.
    assert row["review_count"] == 0
    assert row["branch_count"] == 1


# ─── is_known / filter_unknown ───────────────────────────────────────────────

def test_is_known_after_insert(tmp_db):
    assert dedup_db.is_known(tmp_db, "biz1") is False
    dedup_db.insert_lead(tmp_db, _make_lead("biz1"))
    assert dedup_db.is_known(tmp_db, "biz1") is True


def test_is_known_empty_id_returns_false(tmp_db):
    assert dedup_db.is_known(tmp_db, "") is False


def test_filter_unknown_returns_only_new_ids(tmp_db):
    dedup_db.insert_lead(tmp_db, _make_lead("biz1"))
    dedup_db.insert_lead(tmp_db, _make_lead("biz2"))
    result = dedup_db.filter_unknown(tmp_db, ["biz1", "biz3", "biz2", "biz4"])
    assert result == {"biz3", "biz4"}


def test_filter_unknown_empty_input(tmp_db):
    assert dedup_db.filter_unknown(tmp_db, []) == set()


def test_filter_unknown_skips_blank_ids(tmp_db):
    result = dedup_db.filter_unknown(tmp_db, ["", "biz1", None])  # None tolerated
    # None is falsy and filtered; biz1 doesn't exist so returns biz1.
    assert "biz1" in result


# ─── stats / clean ───────────────────────────────────────────────────────────

def test_stats_by_city_niche(tmp_db):
    dedup_db.insert_lead(tmp_db, _make_lead("a1", city="almaty", niche="hair_beauty"))
    dedup_db.insert_lead(tmp_db, _make_lead("a2", city="almaty", niche="hair_beauty"))
    dedup_db.insert_lead(tmp_db, _make_lead("b1", city="bishkek", niche="fitness"))

    s = dedup_db.stats(tmp_db)
    assert s["total"] == 3
    # Breakdown should include both city/niche combos.
    combos = {(b["city"], b["niche"]): b["count"] for b in s["breakdown"]}
    assert combos == {("almaty", "hair_beauty"): 2, ("bishkek", "fitness"): 1}


def test_stats_filtered_by_city(tmp_db):
    dedup_db.insert_lead(tmp_db, _make_lead("a1", city="almaty"))
    dedup_db.insert_lead(tmp_db, _make_lead("b1", city="bishkek"))
    s = dedup_db.stats(tmp_db, city="almaty")
    assert s["total"] == 1


def test_clean_removes_only_specified_slice(tmp_db):
    dedup_db.insert_lead(tmp_db, _make_lead("a1", city="almaty", niche="hair_beauty"))
    dedup_db.insert_lead(tmp_db, _make_lead("a2", city="almaty", niche="fitness"))
    dedup_db.insert_lead(tmp_db, _make_lead("b1", city="bishkek", niche="hair_beauty"))

    deleted = dedup_db.clean(tmp_db, city="almaty", niche="hair_beauty")
    assert deleted == 1

    remaining = {r["twogis_id"] for r in tmp_db.execute("SELECT twogis_id FROM leads").fetchall()}
    assert remaining == {"a2", "b1"}


def test_clean_nonexistent_slice_returns_zero(tmp_db):
    deleted = dedup_db.clean(tmp_db, city="bishkek", niche="travel")
    assert deleted == 0
