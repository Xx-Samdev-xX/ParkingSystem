# SmartPark KE — Automated Parking Management System

A web-based parking system built for a client automating parking operations
in Kenya. Drivers see live slot availability before entry; the system logs
arrivals, calculates fee + duration on exit, and opens the barrier once
payment is confirmed.

**Stack:** Python 3, SQLite3, [Flet](https://flet.dev) (web UI)

---

## Quick Start (no Python needed)

A pre-built `SmartParkKE.exe` is included for Windows.

1. Download/copy `SmartParkKE.exe` anywhere on your machine.
2. Double-click it.
3. It opens automatically in your browser at `http://127.0.0.1:8000`.

That's it — no Python, no pip install, nothing else to set up.

A `parking_system.db` file will be created in the same folder as the
`.exe` the first time you run it. That's the database — delete it if
you want to reset the system back to empty.

If you'd rather run it from source instead, see the **Setup & Run**
section further down.

---

## 1. Terms of Reference → Modules

| Client requirement | Module |
|---|---|
| Drivers see available slots before entry | **Slot Display Module** |
| System records vehicles on arrival | **Vehicle Entry Module** |
| Auto-calculates time spent + amount to pay on exit | **Exit Module** |
| Barrier opens on payment | **Payment / Barrier Control Module** |
| Staff need visibility into everyone currently in the lot | **Parked Vehicles Module** |
| Lot capacity may need to change over time | **Capacity Management Module** |
| (Implied — "modern system") | **Reporting Module** |

## 2. Algorithms (per module)

**Vehicle Entry**
1. Reject if the plate already has an active session (already parked).
2. Find the first `FREE` slot; reject if none (lot full).
3. Mark the slot `OCCUPIED`, create a session with `entry_time = now()`.

**Exit (duration + fee)**
1. Look up the vehicle's active session; reject if none.
2. `duration = exit_time - entry_time` (minutes).
3. Look up the fee from the `pricing_tiers` table (first tier whose
   `max_minutes >= duration`, or the final "over" tier).
4. Store `exit_time`, `duration`, `fee` on the session — slot stays
   `OCCUPIED` until payment.

**Payment / Barrier**
1. Reject if `amount_paid < fee`.
2. Mark session `paid = 1`, `is_active = 0`.
3. Free the slot (`status = FREE`).
4. Open barrier (simulated in UI); close after exit.

**Slot Display**
- `available = COUNT(slots WHERE status = 'FREE')`, refreshed on demand
  (real hardware would trigger this on a timer or sensor event).

**Reporting**
- Aggregate `SUM(fee)`, `COUNT(*)`, `AVG(duration)` over completed
  sessions for a given day.

## 3. Data Structures & Reasoning

| Structure | Where | Why |
|---|---|---|
| Hash-indexed lookup (`plate_number`) | Active session lookup | SQLite's `PRIMARY KEY` / indexed lookups on `vehicle.plate_number` give O(1)-ish lookup — needed on every entry/exit call |
| Fixed-size list/array | Slot grid (`slots` table, scanned into a Python list for the UI) | Slot count is bounded (tens/hundreds), so a linear scan for "first free slot" is cheap and simple |
| Append-only log | `session` table (never deleted, only closed) | Preserves full history for reporting without needing a separate archive structure |
| Ordered/ranked table | `pricing_tiers` | Rows are read in ascending `max_minutes` order to find the first matching tier — a simple ordered lookup, editable without code changes (this is what makes the DB "dynamic") |

Everything is backed by SQLite tables rather than in-memory Python
structures, so state survives restarts — which a real barrier-controlled
parking lot needs.

## 4. Dynamic Database Design

```
slots(id PK, slot_number UNIQUE, status)
vehicle(plate_number PK, vehicle_type)
session(id PK, plate_number FK, slot_id FK, entry_time, exit_time,
        duration_minutes, fee, paid, is_active)
pricing_tiers(id PK, tier_name, max_minutes, fee)
```

- **Dynamic** because the rate card lives in `pricing_tiers` as data, not
  as hardcoded thresholds — an admin can insert/update rows to change
  pricing without redeploying code.
- **Dynamic** because slots can be added/removed by inserting/deleting
  `slots` rows — no schema change needed.
- `session.is_active` and `session.paid` let the app distinguish
  "currently parked", "awaiting payment", and "completed" without
  separate tables.

Client rate card seeded on first run:

| Duration | Fee (Kshs) |
|---|---|
| Up to 30 min | Free |
| Up to 2 hours | 50 |
| Up to 4 hours | 100 |
| Up to 6 hours | 300 |
| Over 6 hours | 500 |

## Project structure

```
smartpark/
├── database.py     # SQLite schema, seeding, all queries
├── parking.py       # Business logic: entry, exit, fee calc, payment
├── app.py            # Flet web UI (4 tabs = 4 modules)
├── requirements.txt
├── .gitignore
└── README.md
```

## Setup & Run

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

This launches SmartPark KE as a web app in your default browser
(`ft.AppView.WEB_BROWSER`). The SQLite database (`parking_system.db`)
and 20 seeded slots are created automatically on first run.

### Optional: package as a desktop app later

```bash
pip install pyinstaller
pyinstaller --onefile --add-data "parking_system.db;." app.py
```
(Change `view=ft.AppView.WEB_BROWSER` to `view=ft.AppView.FLET_APP` in
`app.py` first if you want a native desktop window instead of a browser tab.)