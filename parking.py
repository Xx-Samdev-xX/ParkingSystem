"""
parking.py
-----------
Core business logic for SmartPark KE:
  - vehicle_entry   : Entry Module
  - vehicle_exit     : Exit Module (duration + fee calculation)
  - process_payment  : Payment / Barrier Control Module
  - get_* helpers     : Slot Display Module + Reporting Module

Kept separate from database.py so the rules (how fees are computed,
what counts as "already parked", etc.) are readable without SQL noise.
"""

from datetime import datetime

import database as db

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str():
    return datetime.now().strftime(TIME_FORMAT)


# ---------------------------------------------------------------------
# Entry Module
# ---------------------------------------------------------------------

def vehicle_entry(plate_number):
    """
    Registers a vehicle on arrival.

    Algorithm:
      1. Reject if this plate already has an active (unpaid/parked) session.
      2. Find the first FREE slot; reject if none (lot full).
      3. Mark that slot OCCUPIED, open a new session with entry_time = now.
    """
    if not plate_number or not plate_number.strip():
        return {"error": "Plate number is required."}

    plate_number = plate_number.strip().upper()

    existing = db.get_active_session(plate_number)
    if existing:
        return {
            "error": f"{plate_number} is already parked (slot id {existing['slot_id']})."
        }

    free_slot = db.first_free_slot()
    if free_slot is None:
        return {"error": "Parking is full."}

    db.register_vehicle(plate_number)
    db.set_slot_status(free_slot["id"], "OCCUPIED")
    entry_time = now_str()
    session_id = db.create_session(plate_number, free_slot["id"], entry_time)

    return {
        "session_id": session_id,
        "plate_number": plate_number,
        "slot_id": free_slot["id"],
        "slot_number": free_slot["slot_number"],
        "entry_time": entry_time,
    }


# ---------------------------------------------------------------------
# Exit Module
# ---------------------------------------------------------------------

def calculate_duration_minutes(entry_time_str, exit_time_str):
    entry = datetime.strptime(entry_time_str, TIME_FORMAT)
    exit_ = datetime.strptime(exit_time_str, TIME_FORMAT)
    return round((exit_ - entry).total_seconds() / 60, 2)


def calculate_fee(duration_minutes):
    """
    Looks up the dynamic pricing_tiers table (client rate card):
        <= 30 min   -> free
        <= 2 hours  -> Kshs 50
        <= 4 hours  -> Kshs 100
        <= 6 hours  -> Kshs 300
        > 6 hours   -> Kshs 500
    Reading tiers from the DB (rather than hardcoding thresholds) means
    an admin can change the rate card without touching this code.
    """
    tiers = db.get_pricing_tiers()
    for tier in tiers:
        if tier["max_minutes"] is None or duration_minutes <= tier["max_minutes"]:
            return tier["fee"]
    return tiers[-1]["fee"]  # defensive fallback; shouldn't normally hit


def vehicle_exit(plate_number):
    """
    Called when a vehicle requests to leave. Calculates duration and fee
    and stores them, but does NOT free the slot yet — that only happens
    once payment is confirmed (see process_payment). This models the
    real barrier flow: fee is shown first, barrier opens after payment.
    """
    if not plate_number or not plate_number.strip():
        return {"error": "Plate number is required."}

    plate_number = plate_number.strip().upper()
    session = db.get_active_session(plate_number)
    if session is None:
        return {"error": f"No active session found for {plate_number}."}

    exit_time = now_str()
    duration = calculate_duration_minutes(session["entry_time"], exit_time)
    fee = calculate_fee(duration)

    db.update_session_exit(session["id"], exit_time, duration, fee)

    return {
        "session_id": session["id"],
        "plate_number": plate_number,
        "slot_id": session["slot_id"],
        "entry_time": session["entry_time"],
        "exit_time": exit_time,
        "duration_minutes": duration,
        "fee": fee,
    }


# ---------------------------------------------------------------------
# Payment / Barrier Control Module
# ---------------------------------------------------------------------

def process_payment(session_id, amount_paid):
    """
    Confirms payment against the fee already calculated by vehicle_exit,
    opens the barrier (simulated here, wired to a UI animation in app.py),
    frees the slot, and closes the session.
    """
    session = db.get_session_by_id(session_id)
    if session is None:
        return {"error": "Session not found."}
    if session["fee"] is None:
        return {"error": "Fee not yet calculated — request exit first."}
    try:
        amount_paid = float(amount_paid)
    except (TypeError, ValueError):
        return {"error": "Enter a valid numeric amount."}
    if amount_paid < session["fee"]:
        return {"error": f"Insufficient payment. Fee due: Kshs {session['fee']}."}

    db.mark_session_paid(session_id)
    db.set_slot_status(session["slot_id"], "FREE")

    return {
        "status": "success",
        "plate_number": session["plate_number"],
        "fee_paid": session["fee"],
    }


# ---------------------------------------------------------------------
# Slot Display Module
# ---------------------------------------------------------------------

def get_available_slots_count():
    return db.count_available_slots()


def get_total_slots_count():
    return db.count_total_slots()


def get_slot_grid():
    return db.get_slot_grid()


# ---------------------------------------------------------------------
# Reporting Module
# ---------------------------------------------------------------------

def get_daily_report(date_str=None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return db.get_daily_summary(date_str)


def get_history(limit=50):
    return db.get_history(limit)