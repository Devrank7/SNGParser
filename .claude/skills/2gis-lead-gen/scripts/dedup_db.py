"""SQLite-backed dedup store for previously-discovered leads.

Schema is created on first use. The primary key is `twogis_id` so the same
business across runs (even in different niches) is recognized.

Operations:
  - is_known(twogis_id)               → bool
  - filter_unknown(list of twogis_ids) → set of new ones
  - insert_lead(record)               → idempotent
  - stats(city, niche)                → counts for the slice
  - clean(city, niche)                → wipe a slice (user-confirmed)
"""

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

DB_PATH_DEFAULT = Path(__file__).resolve().parent.parent / "db" / "leads.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    twogis_id          TEXT PRIMARY KEY,
    name               TEXT,
    city               TEXT,
    niche              TEXT,
    phone              TEXT,
    phone_type         TEXT,
    owner_name         TEXT,
    owner_phone        TEXT,
    owner_instagram    TEXT,
    owner_ig_source    TEXT,        -- '' (manual / not set) | 'serper-auto'
    company_instagram  TEXT,
    website            TEXT,
    has_website        INTEGER,
    contact_method     TEXT,
    data_source        TEXT,
    twogis_url         TEXT,
    address            TEXT,
    discovered_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    sheet_row          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_city_niche ON leads(city, niche);
CREATE INDEX IF NOT EXISTS idx_discovered_at ON leads(discovered_at);
"""


def connect(db_path: Path = DB_PATH_DEFAULT) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _maybe_migrate(conn)
    return conn


def _maybe_migrate(conn):
    """Add owner_ig_source column to existing leads.db that pre-dates it."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    if "owner_ig_source" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN owner_ig_source TEXT DEFAULT ''")
        conn.commit()


def is_known(conn: sqlite3.Connection, twogis_id: str) -> bool:
    if not twogis_id:
        return False
    row = conn.execute("SELECT 1 FROM leads WHERE twogis_id = ?", (twogis_id,)).fetchone()
    return row is not None


def filter_unknown(conn: sqlite3.Connection, twogis_ids: Iterable[str]) -> set:
    ids = [i for i in twogis_ids if i]
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(f"SELECT twogis_id FROM leads WHERE twogis_id IN ({placeholders})", ids).fetchall()
    known = {r["twogis_id"] for r in rows}
    return set(ids) - known


def insert_lead(conn: sqlite3.Connection, lead: dict, sheet_row: Optional[int] = None) -> bool:
    """INSERT OR IGNORE — returns True if a new row was inserted."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO leads (
            twogis_id, name, city, niche,
            phone, phone_type, owner_name, owner_phone,
            owner_instagram, owner_ig_source, company_instagram,
            website, has_website, contact_method,
            data_source, twogis_url, address, sheet_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead.get("twogis_id", ""),
            lead.get("name", ""),
            lead.get("city", ""),
            lead.get("niche", ""),
            lead.get("phone", ""),
            lead.get("phone_type", ""),
            lead.get("owner_name", ""),
            lead.get("owner_phone", ""),
            lead.get("owner_instagram", ""),
            lead.get("owner_ig_source", ""),
            lead.get("company_instagram", ""),
            lead.get("website", ""),
            int(bool(lead.get("has_website"))),
            lead.get("contact_method", ""),
            lead.get("data_source", ""),
            lead.get("twogis_url", ""),
            lead.get("address", ""),
            sheet_row,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def stats(conn: sqlite3.Connection, city: Optional[str] = None, niche: Optional[str] = None) -> dict:
    sql = "SELECT city, niche, COUNT(*) as n FROM leads"
    where, args = [], []
    if city:
        where.append("city = ?")
        args.append(city)
    if niche:
        where.append("niche = ?")
        args.append(niche)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY city, niche ORDER BY n DESC"
    rows = conn.execute(sql, args).fetchall()
    breakdown = [{"city": r["city"], "niche": r["niche"], "count": r["n"]} for r in rows]
    total = sum(b["count"] for b in breakdown)
    return {"total": total, "breakdown": breakdown}


def clean(conn: sqlite3.Connection, city: str, niche: str) -> int:
    """Wipe a city × niche slice. Returns number of rows deleted."""
    cur = conn.execute("DELETE FROM leads WHERE city = ? AND niche = ?", (city, niche))
    conn.commit()
    return cur.rowcount
