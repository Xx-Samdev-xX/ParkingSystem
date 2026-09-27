"""
database.py
------------
All SQLite access for SmartPark KE lives here. No business logic —
just table creation, seeding, and simple CRUD queries. Keeping this
separate from parking.py means the storage layer can be swapped out
(e.g. for Postgres later) without touching business rules.
"""

import sqlite3
from contextlib import contextmanager

DB_NAME = "parking_system.db"


@contextmanager
def get_conn(db_name=DB_NAME):
    """Yields a connection with dict-like row access and FK enforcement on.
    Commits automatically on clean exit; rolls back is left to sqlite3's
    default (an exception propagates and nothing is committed)."""
    conn = None
    try:
        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    finally:
        if conn:
            conn.close()


def init_db():
    """Create all tables if they don't exist yet, and seed the pricing
    tiers table with the client's rate card (idempotent — safe to call
    on every app startup)."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS slots(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_number TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'FREE'
                    CHECK(status IN ('FREE', 'OCCUPIED')),
                -- is_enabled lets capacity shrink WITHOUT deleting rows.
                -- A slot row can never be deleted once a session has
                -- referenced it (that would break the FK on session.slot_id
                -- and destroy history), so "removing" a slot means taking
                -- it out of service, not dropping it from the table.
                is_enabled INTEGER NOT NULL DEFAULT 1
                    CHECK (is_enabled IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS vehicle(
                plate_number TEXT PRIMARY KEY,
                vehicle_type TEXT DEFAULT 'car'
            );

            CREATE TABLE IF NOT EXISTS session(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate_number TEXT NOT NULL,
                slot_id INTEGER NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                duration_minutes REAL,
                fee REAL,
                paid INTEGER DEFAULT 0 CHECK (paid IN (0, 1)),
                is_active INTEGER DEFAULT 1 CHECK (is_active IN (0, 1)),
                FOREIGN KEY (plate_number) REFERENCES vehicle(plate_number),
                FOREIGN KEY (slot_id) REFERENCES slots(id)
            );

            -- Dynamic pricing: rows can be edited/added by an admin without
            -- touching code. NULL max_minutes marks the final "and above" tier.
            CREATE TABLE IF NOT EXISTS pricing_tiers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tier_name TEXT NOT NULL,
                max_minutes INTEGER,
                fee REAL NOT NULL
            );
            """
        )

        # Migration: anyone running a DB created before is_enabled existed
        # gets the column added in place — ALTER TABLE ADD COLUMN is safe
        # and non-destructive in SQLite, no data is touched.
        cursor.execute("PRAGMA table_info(slots);")
        existing_columns = {row["name"] for row in cursor.fetchall()}
        if "is_enabled" not in existing_columns:
            cursor.execute(
                "ALTER TABLE slots ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1;"
            )

        cursor.execute("SELECT COUNT(*) FROM pricing_tiers")
        if cursor.fetchone()[0] == 0:
            cursor.executemany(
                "INSERT INTO pricing_tiers (tier_name, max_minutes, fee) "
                "VALUES (?, ?, ?)",
                [
                    ("Free (up to 30 min)", 30, 0),
                    ("Up to 2 hours", 120, 50),
                    ("Up to 4 hours", 240, 100),
                    ("Up to 6 hours", 360, 300),
                    ("Over 6 hours", None, 500),
                ],
            )


def seed_slots(total_slots=20):
    """Populate the slots table once. Guarded by a count check so calling
    this on every startup never creates duplicates."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM slots")
        if cursor.fetchone()[0] == 0:
            rows = [(f"A{i + 1}",) for i in range(total_slots)]
            cursor.executemany(
                "INSERT INTO slots (slot_number) VALUES (?)", rows
            )


# ---------------------------------------------------------------------
# Slot queries
# ---------------------------------------------------------------------

def first_free_slot():
    """Only ever offers an enabled, FREE slot — a disabled slot is
    effectively out of service and must never be assigned."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM slots WHERE status='FREE' AND is_enabled=1 "
            "ORDER BY id LIMIT 1;"
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_slot_grid():
    """Driver-facing grid — disabled slots are out of service and simply
    don't appear, matching a reduced lot capacity."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, slot_number, status FROM slots "
            "WHERE is_enabled=1 ORDER BY id;"
        )
        return [dict(r) for r in cursor.fetchall()]


def count_available_slots():
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM slots WHERE status='FREE' AND is_enabled=1;"
        )
        return cursor.fetchone()[0]


def count_total_slots():
    """Total slots currently in service (enabled), i.e. the lot's capacity."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM slots WHERE is_enabled=1;")
        return cursor.fetchone()[0]


def set_slot_status(slot_id, status):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE slots SET status=? WHERE id=?;", (status, slot_id))


def add_slots(count):
    """Increase capacity by `count`. Re-enables previously disabled slots
    first (oldest slot_number first) so numbering stays compact, then
    creates brand-new numbered slots for anything still needed."""
    added = []
    with get_conn() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, slot_number FROM slots WHERE is_enabled=0 "
            "ORDER BY id ASC LIMIT ?;",
            (count,),
        )
        to_reenable = cursor.fetchall()
        for row in to_reenable:
            cursor.execute(
                "UPDATE slots SET is_enabled=1, status='FREE' WHERE id=?;",
                (row["id"],),
            )
            added.append(row["slot_number"])

        remaining = count - len(to_reenable)
        for _ in range(remaining):
            # Insert with a placeholder, then name it after its own id so
            # numbers never collide even if earlier slots were re-enabled
            # out of order.
            cursor.execute(
                "INSERT INTO slots (slot_number, status, is_enabled) "
                "VALUES ('PENDING', 'FREE', 1);"
            )
            new_id = cursor.lastrowid
            slot_number = f"A{new_id}"
            cursor.execute(
                "UPDATE slots SET slot_number=? WHERE id=?;", (slot_number, new_id)
            )
            added.append(slot_number)

    return added


def disable_slots(count):
    """Decrease capacity by up to `count`. Only ever disables slots that
    are currently FREE — an OCCUPIED slot can never be pulled out of
    service while a vehicle is parked in it. Prefers disabling the
    highest slot_numbers first, leaving the original numbering intact.
    Returns how many were actually disabled and any shortfall."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, slot_number FROM slots WHERE status='FREE' AND is_enabled=1 "
            "ORDER BY id DESC LIMIT ?;",
            (count,),
        )
        rows = cursor.fetchall()
        for row in rows:
            cursor.execute(
                "UPDATE slots SET is_enabled=0 WHERE id=?;", (row["id"],)
            )

    disabled_numbers = [r["slot_number"] for r in rows]
    return {
        "disabled": disabled_numbers,
        "requested": count,
        "shortfall": count - len(disabled_numbers),
    }


# ---------------------------------------------------------------------
# Vehicle / session queries
# ---------------------------------------------------------------------

def register_vehicle(plate_number, vehicle_type="car"):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO vehicle (plate_number, vehicle_type) "
            "VALUES (?, ?);",
            (plate_number, vehicle_type),
        )


def create_session(plate_number, slot_id, entry_time):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO session (plate_number, slot_id, entry_time, paid, is_active) "
            "VALUES (?, ?, ?, 0, 1);",
            (plate_number, slot_id, entry_time),
        )
        return cursor.lastrowid


def get_active_session(plate_number):
    """Return the vehicle's current parked (unpaid) session, or None.
    Ordered by id DESC so a plate that has parked multiple times in the
    past never accidentally matches a stale, already-closed session."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session WHERE plate_number=? AND is_active=1 "
            "ORDER BY id DESC LIMIT 1;",
            (plate_number,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_session_by_id(session_id):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM session WHERE id=?;", (session_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_session_exit(session_id, exit_time, duration_minutes, fee):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session SET exit_time=?, duration_minutes=?, fee=? WHERE id=?;",
            (exit_time, duration_minutes, fee, session_id),
        )


def mark_session_paid(session_id):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session SET paid=1, is_active=0 WHERE id=?;", (session_id,)
        )


# ---------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------

def get_pricing_tiers():
    """Ordered ascending by max_minutes, with the NULL catch-all tier last."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM pricing_tiers "
            "ORDER BY (max_minutes IS NULL), max_minutes ASC;"
        )
        return [dict(r) for r in cursor.fetchall()]


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------

def get_parked_vehicles():
    """Every vehicle currently in the lot — is_active=1 covers both:
      - still parked, no exit requested yet (exit_time IS NULL)
      - already exited and billed, but unpaid (exit_time set, paid=0)
    Joined with slots so the UI gets the human-readable slot_number
    instead of just the numeric slot_id."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT session.*, slots.slot_number FROM session "
            "JOIN slots ON slots.id = session.slot_id "
            "WHERE session.is_active = 1 "
            "ORDER BY session.entry_time ASC;"
        )
        return [dict(r) for r in cursor.fetchall()]


def get_history(limit=50):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session WHERE is_active=0 ORDER BY id DESC LIMIT ?;",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_daily_summary(date_str):
    """date_str format: 'YYYY-MM-DD'."""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as vehicles, COALESCE(SUM(fee),0) as revenue, "
            "COALESCE(AVG(duration_minutes),0) as avg_duration "
            "FROM session WHERE is_active=0 AND exit_time LIKE ?;",
            (f"{date_str}%",),
        )
        row = cursor.fetchone()
        return dict(row) if row else {"vehicles": 0, "revenue": 0, "avg_duration": 0}