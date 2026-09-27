"""
app.py
-------
SmartPark KE — Flet web UI.

Tabs map onto the required modules:
  1. Slot Display      -> live grid of FREE / OCCUPIED slots
  2. Vehicle Entry      -> register arrival, assign slot
  3. Exit & Payment      -> calculate fee, confirm payment, "open barrier"
  4. Parked Vehicles      -> everyone currently in the lot (live duration,
                              or awaiting payment if they've already exited)
  5. Reports               -> daily revenue + session history

Run as a web app:
    python app.py
(opens in the default browser via ft.AppView.WEB_BROWSER)
"""

import os
import sys

# When packaged with PyInstaller in windowed/no-console mode (which `flet
# pack` uses by default), Windows gives the process no console — so
# sys.stdout and sys.stderr are None instead of a stream. Uvicorn's
# logging setup calls .isatty() on them without checking for None first,
# which crashes on startup with "Unable to configure formatter 'default'".
# Redirecting to a null stream keeps everything downstream (Flet, uvicorn)
# happy without ever needing a real console window.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import flet as ft

import database as db
import parking


def main(page: ft.Page):
    page.title = "SmartPark KE - Parking Management System"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 20
    page.scroll = ft.ScrollMode.AUTO
    page.window_width = 900

    # Tracks which session is currently awaiting payment on the Exit tab.
    current_exit_session = {"id": None}

    # -------------------------------------------------------------
    # Tab 1: Slot Display
    # -------------------------------------------------------------
    slot_grid_view = ft.GridView(
        expand=False,
        runs_count=5,
        max_extent=90,
        child_aspect_ratio=1,
        spacing=8,
        run_spacing=8,
        height=260,
    )
    available_text = ft.Text(size=18, weight=ft.FontWeight.BOLD)

    def refresh_slot_grid():
        slot_grid_view.controls.clear()
        slots = parking.get_slot_grid()
        for s in slots:
            is_free = s["status"] == "FREE"
            slot_grid_view.controls.append(
                ft.Container(
                    content=ft.Text(
                        s["slot_number"],
                        color=ft.Colors.WHITE,
                        weight=ft.FontWeight.BOLD,
                    ),
                    bgcolor=ft.Colors.GREEN_600 if is_free else ft.Colors.RED_600,
                    border_radius=8,
                    alignment=ft.alignment.center,
                )
            )
        available_text.value = (
            f"Available Slots: {parking.get_available_slots_count()} / {len(slots)}"
        )
        page.update()

    # -- Admin: change total slot count --
    total_slots_field = ft.TextField(label="Set Total Slots", width=180)
    manage_slots_result = ft.Text(size=13)

    def handle_set_total_slots(e):
        result = parking.set_total_slots(total_slots_field.value)
        if "error" in result:
            manage_slots_result.value = result["error"]
            manage_slots_result.color = ft.Colors.RED
        else:
            manage_slots_result.value = result["message"]
            manage_slots_result.color = ft.Colors.GREEN_800
            total_slots_field.value = ""
        refresh_slot_grid()

    display_tab = ft.Column(
        [
            available_text,
            ft.ElevatedButton(
                "Refresh",
                icon=ft.Icons.REFRESH,
                on_click=lambda e: refresh_slot_grid(),
            ),
            slot_grid_view,
            ft.Divider(),
            ft.Text("Manage Capacity", size=16, weight=ft.FontWeight.BOLD),
            ft.Row(
                [
                    total_slots_field,
                    ft.ElevatedButton(
                        "Apply", icon=ft.Icons.SETTINGS, on_click=handle_set_total_slots
                    ),
                ]
            ),
            manage_slots_result,
        ],
        spacing=15,
    )

    # -------------------------------------------------------------
    # Tab 2: Vehicle Entry
    # -------------------------------------------------------------
    entry_plate_field = ft.TextField(label="Vehicle Plate Number", width=300)
    entry_result = ft.Text(size=14)

    def handle_entry(e):
        result = parking.vehicle_entry(entry_plate_field.value)
        if "error" in result:
            entry_result.value = result["error"]
            entry_result.color = ft.Colors.RED
        else:
            entry_result.value = (
                f"Ticket issued: {result['plate_number']} -> Slot "
                f"{result['slot_number']} at {result['entry_time']}"
            )
            entry_result.color = ft.Colors.GREEN_800
            entry_plate_field.value = ""
        refresh_slot_grid()
        page.update()

    entry_tab = ft.Column(
        [
            ft.Text("Vehicle Entry", size=18, weight=ft.FontWeight.BOLD),
            entry_plate_field,
            ft.ElevatedButton(
                "Park Vehicle", icon=ft.Icons.DIRECTIONS_CAR, on_click=handle_entry
            ),
            entry_result,
        ],
        spacing=15,
    )

    # -------------------------------------------------------------
    # Tab 3: Exit & Payment
    # -------------------------------------------------------------
    exit_plate_field = ft.TextField(label="Vehicle Plate Number", width=300)
    exit_result = ft.Text(size=14)
    fee_due_text = ft.Text(size=16, weight=ft.FontWeight.BOLD)
    amount_field = ft.TextField(label="Amount Paid (Kshs)", width=300, visible=False)
    barrier_text = ft.Text(size=16, weight=ft.FontWeight.BOLD)

    def handle_exit_request(e):
        result = parking.vehicle_exit(exit_plate_field.value)
        barrier_text.value = ""
        if "error" in result:
            exit_result.value = result["error"]
            exit_result.color = ft.Colors.RED
            fee_due_text.value = ""
            amount_field.visible = False
            pay_button.visible = False
            current_exit_session["id"] = None
        else:
            current_exit_session["id"] = result["session_id"]
            exit_result.value = (
                f"{result['plate_number']} parked for "
                f"{result['duration_minutes']} minutes."
            )
            exit_result.color = ft.Colors.BLACK
            fee_due_text.value = f"Fee Due: Kshs {result['fee']}"
            amount_field.visible = True
            pay_button.visible = True
        refresh_parked()
        page.update()

    def handle_payment(e):
        session_id = current_exit_session["id"]
        if session_id is None:
            return
        result = parking.process_payment(session_id, amount_field.value)
        if "error" in result:
            exit_result.value = result["error"]
            exit_result.color = ft.Colors.RED
        else:
            barrier_text.value = "BARRIER OPEN \u2014 Vehicle may exit."
            barrier_text.color = ft.Colors.GREEN_800
            exit_result.value = (
                f"Payment of Kshs {result['fee_paid']} received for "
                f"{result['plate_number']}."
            )
            exit_result.color = ft.Colors.GREEN_800
            amount_field.visible = False
            pay_button.visible = False
            amount_field.value = ""
            exit_plate_field.value = ""
            fee_due_text.value = ""
            current_exit_session["id"] = None
        refresh_slot_grid()
        refresh_parked()
        page.update()

    pay_button = ft.ElevatedButton(
        "Confirm Payment",
        icon=ft.Icons.PAYMENT,
        visible=False,
        on_click=handle_payment,
    )

    exit_tab = ft.Column(
        [
            ft.Text("Vehicle Exit & Payment", size=18, weight=ft.FontWeight.BOLD),
            exit_plate_field,
            ft.ElevatedButton(
                "Request Exit", icon=ft.Icons.LOGOUT, on_click=handle_exit_request
            ),
            exit_result,
            fee_due_text,
            amount_field,
            pay_button,
            barrier_text,
        ],
        spacing=15,
    )

    # -------------------------------------------------------------
    # Tab 4: Parked Vehicles
    # -------------------------------------------------------------
    # Every vehicle currently in the lot: still parked (no exit request
    # yet, duration ticks live) or already exited and awaiting payment
    # (slot stays OCCUPIED until payment clears).
    parked_summary = ft.Text(size=14, color=ft.Colors.GREY_800)
    parked_list = ft.Column(spacing=8, height=340, scroll=ft.ScrollMode.AUTO)

    def load_into_exit_tab(session_id, plate_number, duration_minutes, fee):
        """Jump to the Exit & Payment tab pre-filled with this session,
        so staff can complete payment without retyping the plate."""
        current_exit_session["id"] = session_id
        exit_plate_field.value = plate_number
        exit_result.value = f"{plate_number} parked for {duration_minutes} minutes."
        exit_result.color = ft.Colors.BLACK
        fee_due_text.value = f"Fee Due: Kshs {fee}"
        amount_field.visible = True
        pay_button.visible = True
        barrier_text.value = ""
        tabs.selected_index = 2  # Exit & Payment tab
        refresh_parked()
        page.update()

    def handle_row_request_exit(plate_number):
        """Called from a parked vehicle's row when it has no exit_time
        yet — runs the exit calculation, then hands off to payment."""
        result = parking.vehicle_exit(plate_number)
        if "error" in result:
            exit_result.value = result["error"]
            exit_result.color = ft.Colors.RED
            tabs.selected_index = 2
            refresh_parked()
            page.update()
            return
        load_into_exit_tab(
            result["session_id"],
            result["plate_number"],
            result["duration_minutes"],
            result["fee"],
        )

    def refresh_parked(e=None):
        parked = parking.get_parked_vehicles()
        parked_summary.value = f"Currently Parked: {len(parked)}"
        parked_list.controls.clear()
        if not parked:
            parked_list.controls.append(
                ft.Text("No vehicles currently parked.", size=13, color=ft.Colors.GREY_600)
            )
        for s in parked:
            already_exited = s["exit_time"] is not None
            if already_exited:
                detail = (
                    f"{s['plate_number']} | Slot {s['slot_number']} | "
                    f"entered {s['entry_time']} | exited {s['exit_time']} | "
                    f"Kshs {s['fee']} due — AWAITING PAYMENT"
                )
                action_button = ft.ElevatedButton(
                    "Pay Now",
                    icon=ft.Icons.PAYMENT,
                    on_click=lambda e, sess=s: load_into_exit_tab(
                        sess["id"], sess["plate_number"], sess["duration_minutes"], sess["fee"]
                    ),
                )
            else:
                live_duration = parking.calculate_duration_minutes(
                    s["entry_time"], parking.now_str()
                )
                detail = (
                    f"{s['plate_number']} | Slot {s['slot_number']} | "
                    f"entered {s['entry_time']} | parked {live_duration} min so far"
                )
                action_button = ft.ElevatedButton(
                    "Request Exit",
                    icon=ft.Icons.LOGOUT,
                    on_click=lambda e, plate=s["plate_number"]: handle_row_request_exit(plate),
                )
            parked_list.controls.append(
                ft.Row(
                    [ft.Text(detail, size=12, expand=True), action_button],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            )
        page.update()

    parked_tab = ft.Column(
        [
            ft.Text("Parked Vehicles", size=18, weight=ft.FontWeight.BOLD),
            ft.ElevatedButton(
                "Refresh", icon=ft.Icons.REFRESH, on_click=refresh_parked
            ),
            parked_summary,
            ft.Divider(),
            parked_list,
        ],
        spacing=15,
    )

    # -------------------------------------------------------------
    # Tab 5: Reports
    # -------------------------------------------------------------
    report_text = ft.Text(size=14)
    history_list = ft.Column(spacing=5, height=340, scroll=ft.ScrollMode.AUTO)

    def refresh_reports(e=None):
        summary = parking.get_daily_report()
        report_text.value = (
            f"Today \u2014 Vehicles served: {summary['vehicles']} | "
            f"Revenue: Kshs {summary['revenue']} | "
            f"Avg duration: {round(summary['avg_duration'], 1)} min"
        )
        history_list.controls.clear()
        for s in parking.get_history(20):
            history_list.controls.append(
                ft.Text(
                    f"{s['plate_number']} | Slot {s['slot_id']} | "
                    f"{s['entry_time']} -> {s['exit_time']} | "
                    f"Kshs {s['fee']} | {'PAID' if s['paid'] else 'UNPAID'}",
                    size=12,
                )
            )
        page.update()

    reports_tab = ft.Column(
        [
            ft.Text("Daily Report", size=18, weight=ft.FontWeight.BOLD),
            ft.ElevatedButton(
                "Refresh Report", icon=ft.Icons.REFRESH, on_click=refresh_reports
            ),
            report_text,
            ft.Divider(),
            ft.Text("Recent Sessions", size=16, weight=ft.FontWeight.BOLD),
            history_list,
        ],
        spacing=15,
    )

    # -------------------------------------------------------------
    # Layout
    # -------------------------------------------------------------
    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=200,
        tabs=[
            ft.Tab(text="Slot Display", icon=ft.Icons.GRID_VIEW, content=ft.Container(display_tab, padding=20)),
            ft.Tab(text="Vehicle Entry", icon=ft.Icons.DIRECTIONS_CAR, content=ft.Container(entry_tab, padding=20)),
            ft.Tab(text="Exit & Payment", icon=ft.Icons.PAYMENT, content=ft.Container(exit_tab, padding=20)),
            ft.Tab(text="Parked Vehicles", icon=ft.Icons.DIRECTIONS_CAR_FILLED, content=ft.Container(parked_tab, padding=20)),
            ft.Tab(text="Reports", icon=ft.Icons.BAR_CHART, content=ft.Container(reports_tab, padding=20)),
        ],
        # No expand=1 here: combined with the page's own scroll=AUTO,
        # "expand" told Flet to fill an ambiguous/unbounded scroll area,
        # which is what produced the blank scrollable space below the
        # actual content. Letting Tabs size to its own content (each
        # tab's own list already caps its height with its own scroll)
        # keeps the whole page a fixed, finite height.
    )

    page.add(
        ft.Text("SmartPark KE", size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
        ft.Text("Automated Parking Management System", size=14, color=ft.Colors.GREY_700),
        ft.Divider(),
        tabs,
    )

    refresh_slot_grid()
    refresh_parked()
    refresh_reports()


if __name__ == "__main__":
    db.init_db()
    db.seed_slots(20)
    ft.app(target=main, view=ft.AppView.WEB_BROWSER)