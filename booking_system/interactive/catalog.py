"""Bootstrap rows for the WhatsApp interactive catalog. A key already stored is left as it is.

Each template is one Cloud API interactive message (a list of at most 10 rows, or one to three reply buttons). The tree is how a
hotel user moves through the PMS from WhatsApp: navigate opens the next template, read fills rows from the hotel's tables, and
input asks for the next value. A list reply id is either a template key or template_key/record_id."""

_HOTEL = """
SELECT h.hotel_name, to_char(hotel_local_date(h.hotel_id), 'YYYY-MM-DD') AS local_date,
       (SELECT count(*) FROM room r WHERE r.hotel_id = h.hotel_id AND r.status = 'active') AS rooms,
       (SELECT count(DISTINCT n.room_id) FROM booking_room_night n
         WHERE n.hotel_id = h.hotel_id AND n.stay_date = hotel_local_date(h.hotel_id) AND n.night_status = 'checked_in') AS in_house
FROM hotel h WHERE h.hotel_id = %(hotel_id)s
"""

_STAY = """
SELECT g.guest_name, s.booking_status, to_char(s.check_in_date, 'YYYY-MM-DD') AS check_in,
       to_char(s.check_out_date, 'YYYY-MM-DD') AS check_out, COALESCE(s.channel, '') AS channel
FROM booking_summary s JOIN guest g ON g.guest_id = s.guest_id
WHERE s.hotel_id = %(hotel_id)s AND s.booking_id = %(record_id)s
"""

_GUEST = """
SELECT g.guest_name, COALESCE(g.nationality, '') AS nationality, g.status,
       COALESCE((SELECT i.identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'phone'
                 ORDER BY i.identity_value LIMIT 1), '') AS phone
FROM guest g WHERE g.hotel_id = %(hotel_id)s AND g.guest_id = %(record_id)s
"""

_ROOM_TYPE = """
SELECT t.room_type_name,
       COALESCE((SELECT (i.total_inventory - i.blocked_quantity)::text FROM room_inventory_nightly i
                 WHERE i.room_type_id = t.room_type_id AND i.stay_date = hotel_local_date(t.hotel_id)), 'n/a') AS rooms_left,
       COALESCE((SELECT p.base_price::text FROM daily_pricing p
                 WHERE p.room_type_id = t.room_type_id AND p.stay_date = hotel_local_date(t.hotel_id)), 'n/a') AS rate
FROM room_type t WHERE t.hotel_id = %(hotel_id)s AND t.room_type_id = %(record_id)s
"""

_SETTING = """
SELECT c.question,
       COALESCE(hotel_setting_at(%(hotel_id)s, c.setting_key, hotel_local_date(%(hotel_id)s)) #>> '{}', 'not set') AS current_value
FROM setting_catalog c WHERE c.setting_key = %(record_id)s
"""

_MAINT = """
SELECT m.issue, m.request_status, m.urgency, COALESCE(m.area, '') AS area
FROM maintenance_request m WHERE m.hotel_id = %(hotel_id)s AND m.request_id = %(record_id)s
"""

_TASK = """
SELECT c.task_type, c.task_status, c.priority
FROM cleaning_task c WHERE c.hotel_id = %(hotel_id)s AND c.cleaning_task_id = %(record_id)s
"""


def _stay_rows(section, where):
    return (
        "SELECT 'read.stay/' || s.booking_id AS row_id, left(g.guest_name, 20) AS title, "
        "left(to_char(s.check_in_date, 'DD Mon') || ' to ' || to_char(s.check_out_date, 'DD Mon') || ' ' || s.booking_status, 72) AS description, "
        "'" + section + "' AS section_title "
        "FROM booking_summary s JOIN guest g ON g.guest_id = s.guest_id "
        "WHERE s.hotel_id = %(hotel_id)s AND " + where + " ORDER BY s.check_in_date, g.guest_name LIMIT 9"
    )


_ARRIVALS = _stay_rows("Arrivals", "s.check_in_date = hotel_local_date(s.hotel_id) AND s.booking_status = 'confirmed'")
_DEPARTURES = _stay_rows("Departures", "s.check_out_date = hotel_local_date(s.hotel_id) AND s.booking_status = 'checked_in'")
_IN_HOUSE = _stay_rows("In house", "s.booking_status = 'checked_in'")
_PAST = _stay_rows("Past stays", "s.booking_status = 'checked_out'")
_CONFIRMED = _stay_rows("Bookings", "s.booking_status = 'confirmed'")

_ROOMS = """
SELECT 'input.room_readiness/' || r.room_id AS row_id, left(r.room_number, 20) AS title,
       left(COALESCE(s.readiness_status, 'unknown'), 72) AS description, 'Rooms' AS section_title
FROM room r LEFT JOIN room_readiness_status s ON s.room_id = r.room_id
WHERE r.hotel_id = %(hotel_id)s AND r.status = 'active' ORDER BY r.room_number LIMIT 9
"""

_DIRTY = """
SELECT 'input.room_readiness/' || r.room_id AS row_id, left(r.room_number, 20) AS title,
       'Dirty' AS description, 'Dirty' AS section_title
FROM room r JOIN room_readiness_status s ON s.room_id = r.room_id
WHERE r.hotel_id = %(hotel_id)s AND r.status = 'active' AND s.readiness_status = 'dirty'
ORDER BY r.room_number LIMIT 9
"""

_READINESS = """
SELECT 'input.readiness.' || v.status || '/' || %(record_id)s AS row_id, v.title,
       'Set this room' AS description, 'Readiness' AS section_title
FROM (VALUES ('clean','Clean'),('dirty','Dirty'),('cleaning','Cleaning'),('inspected','Inspected')) AS v(status, title)
"""

_TYPES = """
SELECT 'read.room_type/' || t.room_type_id AS row_id, left(t.room_type_name, 20) AS title,
       left(COALESCE((SELECT (i.total_inventory - i.blocked_quantity)::text FROM room_inventory_nightly i
                      WHERE i.room_type_id = t.room_type_id AND i.stay_date = hotel_local_date(t.hotel_id)), 'n/a') || ' left', 72) AS description,
       'Room types' AS section_title
FROM room_type t WHERE t.hotel_id = %(hotel_id)s AND t.status = 'active' ORDER BY t.room_type_name LIMIT 9
"""

_GUESTS = """
SELECT 'read.guest/' || g.guest_id AS row_id, left(g.guest_name, 20) AS title,
       left(COALESCE((SELECT i.identity_value FROM guest_identity i WHERE i.guest_id = g.guest_id AND i.identity_type = 'phone'
                      ORDER BY i.identity_value LIMIT 1), ''), 72) AS description,
       'Guests' AS section_title
FROM guest g WHERE g.hotel_id = %(hotel_id)s AND g.status = 'active' ORDER BY g.guest_name LIMIT 9
"""

_JOBS = """
SELECT 'read.maint_one/' || m.request_id AS row_id, left(m.issue, 20) AS title,
       left(m.request_status || ' ' || m.urgency, 72) AS description, 'Jobs' AS section_title
FROM maintenance_request m
WHERE m.hotel_id = %(hotel_id)s AND m.request_status IN ('reported','assigned','in_progress')
ORDER BY m.reported_at DESC LIMIT 9
"""

_TASKS = """
SELECT 'read.cleaning/' || c.cleaning_task_id AS row_id, left(c.task_type, 20) AS title,
       left(c.task_status || ' ' || c.priority, 72) AS description, 'Tasks' AS section_title
FROM cleaning_task c
WHERE c.hotel_id = %(hotel_id)s AND c.task_status IN ('pending','assigned','in_progress')
ORDER BY c.created_at DESC LIMIT 9
"""

_SETTINGS = """
SELECT 'input.setting/' || c.setting_key AS row_id,
       left(replace(split_part(c.setting_key, '.', 2), '_', ' '), 20) AS title,
       left(c.question, 72) AS description, 'Settings' AS section_title
FROM setting_catalog c WHERE c.status = 'active' ORDER BY c.display_order LIMIT 8
"""

_SETTINGS_MORE = """
SELECT 'input.setting/' || c.setting_key AS row_id,
       left(replace(split_part(c.setting_key, '.', 2), '_', ' '), 20) AS title,
       left(c.question, 72) AS description, 'Settings' AS section_title
FROM setting_catalog c WHERE c.status = 'active' ORDER BY c.display_order OFFSET 8 LIMIT 9
"""

# module, menu title (<=20), menu description (<=72), PMS page this menu stands in for
_MENUS = (
    ("front_desk", "Front desk", "Arrivals, stays, queue", "assignments.html"),
    ("reservations", "Reservations", "Bookings, rates, OTA", "bookings.html"),
    ("housekeeping", "Housekeeping", "Rooms, cleaning, duty", "housekeeping.html"),
    ("maintenance", "Maintenance", "Jobs and schedules", "maintenance.html"),
    ("reporting", "Reports", "Occupancy and close", "analytics.html"),
    ("hr", "People", "Staff, roster, leave", "operations.html"),
    ("fnb", "Restaurant", "Outlets and checks", "operations.html"),
    ("finance", "Finance", "Day, tax, folio, AR", "analytics.html"),
    ("admin", "Property", "Rooms, guests, setup", "settings.html"),
)

# key, module, purpose, title, description, sentence, page, kind, button, complete_sql, choice_sql, choices, listed, back
# choices are (title, description, next_template_key) or (title, description, next_template_key, row_id)
def _leaves():
    pay = (
        ("Cash", "Pay by cash", "input.payment_amount", "input.payment_amount/cash"),
        ("Card", "Pay by card", "input.payment_amount", "input.payment_amount/card"),
        ("Transfer", "Pay by transfer", "input.payment_amount", "input.payment_amount/transfer"),
        ("City ledger", "Charge the account", "input.payment_amount", "input.payment_amount/city_ledger"),
    )
    return (
        ("read.arrivals", "front_desk", "read", "Arrivals", "Who arrives today", "Who arrives today.", "assignments.html", "list", "Arrivals", "", _ARRIVALS, (), True, ""),
        ("read.departures", "front_desk", "read", "Departures", "Who leaves today", "Who leaves today.", "assignments.html", "list", "Departures", "", _DEPARTURES, (), True, ""),
        ("read.in_house", "front_desk", "read", "In house", "Who is staying", "Who is in house.", "assignments.html", "list", "In house", "", _IN_HOUSE, (), True, ""),
        ("read.stay_calendar", "front_desk", "navigate", "Stay calendar", "The stay view", "Open the stay view.", "assignments.html", "list", "Stay calendar", "", "", (("Arrivals", "Today's arrivals", "read.arrivals"), ("Departures", "Today's departures", "read.departures"), ("In house", "Staying now", "read.in_house"), ("Past stays", "Already checked out", "read.past_stays")), True, ""),
        ("read.past_stays", "front_desk", "read", "Past stays", "Checked-out stays", "Stays already checked out.", "assignments.html", "list", "Past stays", "", _PAST, (), True, ""),
        ("read.desk_queue", "front_desk", "read", "Desk queue", "Approvals and tasks", "Approvals and tasks waiting at the desk.", "desk.html", "list", "Desk queue", "", "", (("Arrivals", "Check a guest in", "read.arrivals"), ("Departures", "Check a guest out", "read.departures")), True, ""),
        ("input.check_in", "front_desk", "input", "Check in", "Check a guest in", "Pick the arrival to check in.", "assignments.html", "list", "Check in", "", _ARRIVALS, (), True, ""),
        ("input.check_out", "front_desk", "input", "Check out", "Check a guest out", "Pick the stay to check out.", "assignments.html", "list", "Check out", "", _DEPARTURES, (), True, ""),
        ("input.undo_check_in", "front_desk", "input", "Undo check-in", "Reverse a check-in", "Reply with the booking to undo the check-in.", "assignments.html", "button", "", "", "", (), True, ""),
        ("input.reset_stay", "front_desk", "input", "Reset stay", "Reset a stay night", "Reply with the booking whose stay should be reset.", "assignments.html", "button", "", "", "", (), True, ""),
        ("input.reassign", "front_desk", "input", "Reassign room", "Move a guest", "Reply with the room to move the guest to.", "assignments.html", "list", "Reassign", "", _ROOMS, (), True, ""),
        ("input.bulk_checkout", "front_desk", "input", "Bulk checkout", "Check out past stays", "Reply yes to check out every past stay still in house.", "assignments.html", "button", "", "", "", (("Confirm", "Check out past stays", "input.bulk_checkout_go"),), True, ""),
        ("input.verify_phone", "front_desk", "input", "Verify phone", "Confirm a guest phone", "Reply with the guest phone to verify.", "assignments.html", "button", "", "", "", (), True, ""),
        ("input.update_stay", "front_desk", "input", "Update stay", "Change stay details", "Reply with the field to change: dates, room type, or rooms.", "assignments.html", "list", "Update stay", "", "", (("Dates", "Change the stay dates", "input.stay_dates"), ("Room type", "Change the room type", "input.stay_type"), ("Rooms", "Change how many rooms", "input.stay_rooms")), True, ""),
        ("read.stay", "front_desk", "read", "Stay", "One stay", "{guest_name} is {booking_status}. In {check_in}, out {check_out}, via {channel}.", "assignments.html", "list", "Stay", _STAY, "", (("Check in", "Check this guest in", "input.check_in"), ("Check out", "Check this guest out", "input.check_out"), ("Payment", "Record a payment", "input.record_payment")), False, "read.arrivals"),
        ("input.bulk_checkout_go", "front_desk", "input", "Confirm checkout", "Confirm bulk checkout", "Bulk checkout is confirmed when you send this from the desk. Reply with the date.", "assignments.html", "button", "", "", "", (), False, "input.bulk_checkout"),
        ("input.stay_dates", "front_desk", "input", "Stay dates", "New stay dates", "Reply with the new dates as YYYY-MM-DD to YYYY-MM-DD.", "assignments.html", "button", "", "", "", (), False, "input.update_stay"),
        ("input.stay_type", "front_desk", "input", "Stay type", "New room type", "Reply with the room type name.", "assignments.html", "button", "", "", "", (), False, "input.update_stay"),
        ("input.stay_rooms", "front_desk", "input", "Stay rooms", "How many rooms", "Reply with how many rooms.", "assignments.html", "button", "", "", "", (), False, "input.update_stay"),

        ("read.booking_calendar", "reservations", "read", "Booking calendar", "Bookings on the books", "Confirmed bookings.", "bookings.html", "list", "Bookings", "", _CONFIRMED, (), True, ""),
        ("input.create_booking", "reservations", "input", "Create booking", "Book a room", "Reply with guest, phone, room type, and dates.", "bookings.html", "list", "Create", "", _TYPES, (), True, ""),
        ("input.group_booking", "reservations", "input", "Group booking", "Book a group", "Reply with the group name, room count, and dates.", "bookings.html", "button", "", "", "", (), True, ""),
        ("input.modify_booking", "reservations", "input", "Modify booking", "Change a booking", "Pick the booking to change.", "bookings.html", "list", "Modify", "", _CONFIRMED, (), True, ""),
        ("input.cancel_booking", "reservations", "input", "Cancel booking", "Cancel a booking", "Pick the booking to cancel.", "bookings.html", "list", "Cancel", "", _CONFIRMED, (), True, ""),
        ("read.availability", "reservations", "read", "Rooms left", "Availability by type", "Rooms left today, by type.", "rooms.html", "list", "Rooms left", "", _TYPES, (), True, ""),
        ("input.rate_override", "reservations", "input", "Rate override", "Override one rate", "Reply with the booking and the new nightly rate.", "bookings.html", "button", "", "", "", (), True, ""),
        ("read.payment_ledger", "reservations", "read", "Payment ledger", "Payments on a booking", "Reply with the booking to read its payments.", "bookings.html", "list", "Ledger", "", _CONFIRMED, (), True, ""),
        ("input.record_payment", "reservations", "input", "Record payment", "Take a payment", "How is the guest paying?", "bookings.html", "list", "Payment", "", "", pay, True, ""),
        ("input.payment_amount", "reservations", "input", "Payment amount", "Amount to record", "Method {record_id}. Reply with the amount.", "bookings.html", "button", "", "", "", (), False, "input.record_payment"),
        ("input.record_refund", "reservations", "input", "Record refund", "Refund a payment", "Reply with the booking and which payment to refund.", "bookings.html", "button", "", "", "", (), True, ""),
        ("read.ota_mapping", "reservations", "read", "OTA mapping", "Channel room map", "The channel mapping is read from the property.", "ota_mapping.html", "button", "", "", "", (), True, ""),
        ("input.ota_mapping", "reservations", "input", "Save OTA map", "Save a channel map", "Reply with the channel and the mapped room type.", "ota_mapping.html", "button", "", "", "", (), True, ""),
        ("input.ota_room_map", "reservations", "input", "OTA room map", "Map one room", "Reply with the channel room code and the PMS room.", "ota_mapping.html", "button", "", "", "", (), True, ""),
        ("read.ota_out", "reservations", "read", "OTA outbound", "Reservations sent out", "Outbound channel reservations.", "ota_mapping.html", "button", "", "", "", (), True, ""),
        ("read.ota_in", "reservations", "read", "OTA inbound", "Reservations received", "Inbound channel reservations.", "ota_mapping.html", "button", "", "", "", (), True, ""),
        ("read.upgrade_offers", "reservations", "read", "Upgrade offers", "Offers on a booking", "Reply with the booking to see upgrade offers.", "bookings.html", "button", "", "", "", (), True, ""),
        ("input.online_search", "reservations", "input", "Online search", "Search a stay", "Reply with check-in, check-out, and rooms.", "bookings.html", "button", "", "", "", (), True, ""),
        ("input.online_book", "reservations", "input", "Online book", "Book from search", "Reply with the guest and the room type to book.", "bookings.html", "button", "", "", "", (), True, ""),
        ("read.room_type", "reservations", "read", "Room type", "One room type", "{room_type_name}: {rooms_left} left, rate {rate}.", "rooms.html", "list", "Room type", _ROOM_TYPE, "", (("Book", "Create a booking", "input.create_booking"), ("Rates", "See the rate", "read.pricing")), False, "read.availability"),

        ("read.hk_board", "housekeeping", "read", "HK board", "Housekeeping board", "The housekeeping board for today.", "housekeeping.html", "list", "HK board", "", "", (("Dirty rooms", "Rooms still dirty", "read.hk_dirty"), ("Tasks", "Open cleaning tasks", "read.cleaning_tasks"), ("On duty", "Who is clocked in", "read.hk_on_duty")), True, ""),
        ("read.hk_kpis", "housekeeping", "read", "HK figures", "Housekeeping figures", "{in_house} of {rooms} rooms are in house.", "housekeeping.html", "list", "HK figures", _HOTEL, "", (("Dirty rooms", "Rooms still dirty", "read.hk_dirty"), ("Board", "Open the board", "read.hk_board")), True, ""),
        ("read.hk_dirty", "housekeeping", "read", "Dirty rooms", "Rooms to clean", "Rooms marked dirty.", "housekeeping.html", "list", "Dirty rooms", "", _DIRTY, (), True, ""),
        ("read.hk_workload", "housekeeping", "read", "HK workload", "Work by attendant", "Workload is counted from open cleaning tasks.", "housekeeping.html", "list", "Workload", "", _TASKS, (), True, ""),
        ("read.hk_activity", "housekeeping", "read", "HK activity", "Recent housekeeping", "Recent housekeeping activity.", "housekeeping.html", "button", "", "", "", (), True, ""),
        ("read.hk_on_duty", "housekeeping", "read", "On duty", "Attendants on duty", "Who is clocked in to housekeeping.", "housekeeping.html", "button", "", "", "", (), True, ""),
        ("input.hk_clock_in", "housekeeping", "input", "Clock in", "Start a duty", "Reply with the attendant phone to clock in.", "housekeeping.html", "button", "", "", "", (), True, ""),
        ("input.hk_clock_out", "housekeeping", "input", "Clock out", "End a duty", "Reply with the attendant phone to clock out.", "housekeeping.html", "button", "", "", "", (), True, ""),
        ("input.start_cleaning", "housekeeping", "input", "Start cleaning", "Start a room", "Pick the dirty room to start.", "housekeeping.html", "list", "Start clean", "", _DIRTY, (), True, ""),
        ("input.create_task", "housekeeping", "input", "New HK task", "Create a cleaning task", "Reply with the room and the task type.", "housekeeping.html", "button", "", "", "", (), True, ""),
        ("input.update_progress", "housekeeping", "input", "Task progress", "Move a task forward", "Pick the open task.", "housekeeping.html", "list", "Progress", "", _TASKS, (), True, ""),
        ("read.room_readiness", "housekeeping", "read", "Room readiness", "Clean, dirty, inspected", "Room readiness.", "housekeeping.html", "list", "Readiness", "", _ROOMS, (), True, ""),
        ("input.room_readiness", "housekeeping", "input", "Set readiness", "Set one room", "Pick the readiness for this room.", "housekeeping.html", "list", "Readiness", "", _READINESS, (), False, "read.rooms"),
        ("input.readiness.clean", "housekeeping", "input", "Mark clean", "Room becomes clean", "Room {record_id} is marked clean when the desk saves it.", "housekeeping.html", "button", "", "", "", (), False, "read.room_readiness"),
        ("input.readiness.dirty", "housekeeping", "input", "Mark dirty", "Room becomes dirty", "Room {record_id} is marked dirty when the desk saves it.", "housekeeping.html", "button", "", "", "", (), False, "read.room_readiness"),
        ("input.readiness.cleaning", "housekeeping", "input", "Mark cleaning", "Room is being cleaned", "Room {record_id} is marked cleaning when the desk saves it.", "housekeeping.html", "button", "", "", "", (), False, "read.room_readiness"),
        ("input.readiness.inspected", "housekeeping", "input", "Mark inspected", "Room is inspected", "Room {record_id} is marked inspected when the desk saves it.", "housekeeping.html", "button", "", "", "", (), False, "read.room_readiness"),
        ("input.mark_ooo", "housekeeping", "input", "Mark OOO", "Out of order", "Reply with the room to mark out of order.", "housekeeping.html", "button", "", "", "", (), True, ""),
        ("input.mark_available", "housekeeping", "input", "Mark available", "Back in service", "Reply with the room to mark available.", "housekeeping.html", "button", "", "", "", (), True, ""),
        ("read.cleaning_tasks", "housekeeping", "read", "Cleaning tasks", "Open tasks", "Open cleaning tasks.", "housekeeping.html", "list", "Tasks", "", _TASKS, (), True, ""),
        ("read.cleaning", "housekeeping", "read", "Cleaning task", "One task", "{task_type} is {task_status}, priority {priority}.", "housekeeping.html", "button", "", _TASK, "", (), False, "read.cleaning_tasks"),

        ("read.maint_open", "maintenance", "read", "Open jobs", "Reported maintenance", "Open maintenance jobs.", "maintenance.html", "list", "Open jobs", "", _JOBS, (), True, ""),
        ("input.maint_create", "maintenance", "input", "Report issue", "New maintenance job", "Reply with the room or area, the issue, and the urgency.", "maintenance.html", "list", "Report", "", "", (("Low", "Low urgency", "input.maint_detail", "input.maint_detail/low"), ("Normal", "Normal urgency", "input.maint_detail", "input.maint_detail/normal"), ("High", "High urgency", "input.maint_detail", "input.maint_detail/high"), ("Urgent", "Urgent", "input.maint_detail", "input.maint_detail/urgent")), True, ""),
        ("input.maint_detail", "maintenance", "input", "Issue detail", "Describe the issue", "Urgency {record_id}. Reply with the room and the issue.", "maintenance.html", "button", "", "", "", (), False, "input.maint_create"),
        ("input.maint_update", "maintenance", "input", "Update job", "Change a job", "Pick the job to update.", "maintenance.html", "list", "Update job", "", _JOBS, (), True, ""),
        ("read.maint_schedules", "maintenance", "read", "PM schedules", "Preventive schedules", "Preventive maintenance schedules.", "maintenance.html", "button", "", "", "", (), True, ""),
        ("input.maint_schedule", "maintenance", "input", "Edit schedule", "Change a PM schedule", "Reply with the area, the issue, and how many days between services.", "maintenance.html", "button", "", "", "", (), True, ""),
        ("input.pm_run", "maintenance", "input", "Run PM now", "Generate due jobs", "Reply yes to generate the preventive jobs that are due.", "maintenance.html", "button", "", "", "", (("Run now", "Generate due jobs", "input.pm_run_go"),), True, ""),
        ("input.pm_run_go", "maintenance", "input", "PM confirmed", "Run is confirmed", "The preventive run is confirmed from the desk.", "maintenance.html", "button", "", "", "", (), False, "input.pm_run"),
        ("read.maint_one", "maintenance", "read", "Job", "One job", "{issue} is {request_status}, urgency {urgency}. {area}", "maintenance.html", "list", "Job", _MAINT, "", (("Update", "Update this job", "input.maint_update"),), False, "read.maint_open"),

        ("read.analytics", "reporting", "read", "Analytics", "Hotel analytics", "{in_house} of {rooms} rooms are in house on {local_date}.", "analytics.html", "list", "Analytics", _HOTEL, "", (("Occupancy", "In-house count", "read.occupancy"), ("Arrivals", "Who arrives", "read.arrivals"), ("Activity", "The activity log", "read.activity_log")), True, ""),
        ("read.activity_log", "reporting", "read", "Activity log", "What changed", "The activity log for this hotel.", "activities.html", "button", "", "", "", (), True, ""),
        ("read.occupancy", "reporting", "read", "Occupancy", "Rooms in house", "{in_house} of {rooms} rooms are in house on {local_date}.", "analytics.html", "list", "Occupancy", _HOTEL, "", (("In house", "Who is staying", "read.in_house"), ("Rooms left", "Availability", "read.availability")), True, ""),
        ("read.revenue_today", "reporting", "read", "Daily revenue", "Revenue for the day", "Daily revenue is read from the business day.", "analytics.html", "button", "", "", "", (), True, ""),
        ("read.gl_export", "reporting", "read", "GL export", "Export the ledger", "The general-ledger export for a closed day.", "analytics.html", "button", "", "", "", (), True, ""),
        ("read.ar_aging", "reporting", "read", "AR aging", "Aged receivables", "Aged city-ledger receivables.", "analytics.html", "button", "", "", "", (), True, ""),
        ("read.daily_close", "reporting", "read", "Daily close", "The closed day", "Reply with the business date to read that close.", "analytics.html", "button", "", "", "", (), True, ""),

        ("read.staff", "hr", "read", "Staff list", "People at the hotel", "The staff list.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.staff_add", "hr", "input", "Add staff", "Add a person", "Reply with the name, phone, and department.", "operations.html", "button", "", "", "", (), True, ""),
        ("read.roster", "hr", "read", "Roster", "Who works when", "The roster.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.roster", "hr", "input", "Edit roster", "Change a shift", "Reply with the person, the date, and the shift.", "operations.html", "button", "", "", "", (), True, ""),
        ("read.attendance", "hr", "read", "Attendance", "Clock records", "Attendance for the working day.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.staff_clock_in", "hr", "input", "Staff clock in", "Clock a person in", "Reply with the staff phone to clock in.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.staff_clock_out", "hr", "input", "Staff clock out", "Clock a person out", "Reply with the staff phone to clock out.", "operations.html", "button", "", "", "", (), True, ""),
        ("read.leave", "hr", "read", "Leave list", "Leave requests", "Open leave requests.", "operations.html", "list", "Leave", "", "", (("Decide", "Approve or reject", "input.leave_decide"),), True, ""),
        ("input.leave_request", "hr", "input", "Request leave", "Ask for leave", "Reply with the person and the dates.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.leave_decide", "hr", "input", "Leave decision", "Approve or reject", "What should happen to the request?", "operations.html", "button", "", "", "", (("Approve", "Approve the leave", "input.leave_approve"), ("Reject", "Reject the leave", "input.leave_reject")), True, ""),
        ("input.leave_approve", "hr", "input", "Approve leave", "Approve one request", "Reply with the leave request to approve.", "operations.html", "button", "", "", "", (), False, "input.leave_decide"),
        ("input.leave_reject", "hr", "input", "Reject leave", "Reject one request", "Reply with the leave request to reject.", "operations.html", "button", "", "", "", (), False, "input.leave_decide"),

        ("read.outlets", "fnb", "read", "Outlets", "Restaurants and bars", "The outlets.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.outlet", "fnb", "input", "Add outlet", "Open an outlet", "Reply with the outlet name.", "operations.html", "button", "", "", "", (), True, ""),
        ("read.menu", "fnb", "read", "Menu", "Dishes on an outlet", "Reply with the outlet to read its menu.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.menu_item", "fnb", "input", "Add menu item", "Add a dish", "Reply with the outlet, the dish, and the price.", "operations.html", "button", "", "", "", (), True, ""),
        ("read.open_checks", "fnb", "read", "Open checks", "Checks still open", "Open restaurant checks.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.open_check", "fnb", "input", "New check", "Open a check", "Reply with the outlet and the table.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.add_line", "fnb", "input", "Add check line", "Add a dish to a check", "Reply with the check and the dish.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.void_line", "fnb", "input", "Void line", "Remove a dish", "Reply with the check and the line to void.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.void_check", "fnb", "input", "Void check", "Void a whole check", "Reply with the check to void.", "operations.html", "button", "", "", "", (), True, ""),
        ("input.settle_check", "fnb", "input", "Settle check", "Close a check", "How should the check be settled?", "operations.html", "button", "", "", "", (("Direct", "Settle at the outlet", "input.settle_direct"), ("Room", "Charge the room", "input.settle_room")), True, ""),
        ("input.settle_direct", "fnb", "input", "Settle direct", "Pay at the outlet", "Reply with the check and the amount.", "operations.html", "button", "", "", "", (), False, "input.settle_check"),
        ("input.settle_room", "fnb", "input", "Settle to room", "Charge a room", "Reply with the check and the room.", "operations.html", "button", "", "", "", (), False, "input.settle_check"),
        ("input.check_discount", "fnb", "input", "Check discount", "Discount a check", "Reply with the check and the discount.", "operations.html", "button", "", "", "", (), True, ""),
        ("read.fnb_revenue", "fnb", "read", "F&B revenue", "Outlet revenue", "Restaurant revenue for the day.", "operations.html", "button", "", "", "", (), True, ""),

        ("read.business_day", "finance", "read", "Business day", "The open day", "The finance business day.", "analytics.html", "list", "Business day", "", "", (("Open", "Open a day", "input.open_day"), ("Close", "Close the day", "input.close_day"), ("Read close", "Read a closed day", "read.daily_close")), True, ""),
        ("input.open_day", "finance", "input", "Open the day", "Open the business day", "Reply with the business date to open.", "analytics.html", "button", "", "", "", (), True, ""),
        ("input.close_day", "finance", "input", "Close the day", "Close the business day", "Reply with the business date to close.", "analytics.html", "button", "", "", "", (), True, ""),
        ("read.tax_codes", "finance", "read", "Tax codes", "Active taxes", "The tax codes.", "settings.html", "button", "", "", "", (), True, ""),
        ("input.tax_code", "finance", "input", "Add tax code", "Add a tax", "Reply with the tax code and the rate.", "settings.html", "button", "", "", "", (), True, ""),
        ("read.txn_codes", "finance", "read", "Txn codes", "Transaction codes", "The transaction codes.", "settings.html", "button", "", "", "", (), True, ""),
        ("input.txn_code", "finance", "input", "Add txn code", "Add a transaction code", "Reply with the code and the group.", "settings.html", "button", "", "", "", (), True, ""),
        ("read.cashier_shift", "finance", "read", "Cashier shift", "The open shift", "The cashier shift.", "analytics.html", "list", "Shift", "", "", (("Open shift", "Open a shift", "input.open_shift"), ("Close shift", "Close the shift", "input.close_shift")), True, ""),
        ("input.open_shift", "finance", "input", "Open a shift", "Start cashiering", "Reply with the opening float.", "analytics.html", "button", "", "", "", (), True, ""),
        ("input.close_shift", "finance", "input", "Close a shift", "Count the drawer", "Reply with the counted amount.", "analytics.html", "button", "", "", "", (), True, ""),
        ("input.post_charge", "finance", "input", "Post a charge", "Charge a folio", "Reply with the folio, the code, and the amount.", "analytics.html", "button", "", "", "", (), True, ""),
        ("input.post_payment", "finance", "input", "Post a payment", "Pay a folio", "Reply with the folio and the amount.", "analytics.html", "button", "", "", "", (), True, ""),
        ("input.post_adjust", "finance", "input", "Post adjustment", "Adjust a folio", "Reply with the folio and the adjustment.", "analytics.html", "button", "", "", "", (), True, ""),
        ("input.reverse_posting", "finance", "input", "Reverse posting", "Reverse one posting", "Reply with the posting to reverse.", "analytics.html", "button", "", "", "", (), True, ""),
        ("read.folio", "finance", "read", "Read a folio", "One folio", "Reply with the folio type and reference.", "analytics.html", "button", "", "", "", (), True, ""),
        ("read.ar_accounts", "finance", "read", "AR accounts", "City ledger accounts", "City-ledger accounts.", "analytics.html", "list", "AR accounts", "", "", (("Invoice", "Raise an invoice", "input.ar_invoice"), ("Payment", "Take an AR payment", "input.ar_payment"), ("Aging", "Aged receivables", "read.ar_aging")), True, ""),
        ("input.ar_invoice", "finance", "input", "AR invoice", "Invoice an account", "Reply with the account and the amount.", "analytics.html", "button", "", "", "", (), True, ""),
        ("input.ar_payment", "finance", "input", "AR payment", "Pay an account", "Reply with the account and the amount.", "analytics.html", "button", "", "", "", (), True, ""),

        ("read.settings", "admin", "read", "Settings", "How the hotel operates", "Operating settings.", "settings.html", "list", "Settings", "", _SETTINGS, (("More", "The rest of the settings", "read.settings_more"),), True, ""),
        ("read.settings_more", "admin", "read", "More settings", "The rest of the catalog", "The rest of the operating settings.", "settings.html", "list", "Settings", "", _SETTINGS_MORE, (), False, "read.settings"),
        ("input.setting", "admin", "input", "One setting", "Answer one setting", "{question} Now: {current_value}. Reply with the new value.", "settings.html", "button", "", _SETTING, "", (), False, "read.settings"),
        ("read.property", "admin", "read", "Property", "Hotel profile", "The property profile for {hotel_name}.", "property_setup.html", "button", "", "", "", (), True, ""),
        ("input.property_save", "admin", "input", "Save property", "Update the profile", "Reply with the field and the new value.", "property_setup.html", "button", "", "", "", (), True, ""),
        ("read.policies", "admin", "read", "Policies", "Payment and booking", "Which policy?", "property_setup.html", "list", "Policies", "", "", (("Payment", "How the guest pays", "input.payment_policy"), ("Booking", "Cancellation rules", "input.booking_policy")), True, ""),
        ("input.payment_policy", "admin", "input", "Payment policy", "When payment is due", "Reply with the threshold, the window, and the methods.", "property_setup.html", "button", "", "", "", (), True, ""),
        ("input.booking_policy", "admin", "input", "Booking policy", "Cancellation rules", "Reply with the window, the penalty, and the refund.", "property_setup.html", "button", "", "", "", (), True, ""),
        ("read.registration_card", "admin", "read", "Reg card", "Registration card", "The registration card.", "property_setup.html", "button", "", "", "", (), True, ""),
        ("input.registration_card", "admin", "input", "Save reg card", "Update the card", "Reply with the card text.", "property_setup.html", "button", "", "", "", (), True, ""),
        ("read.guests", "admin", "read", "Guest list", "Active guests", "Active guests.", "guests.html", "list", "Guests", "", _GUESTS, (), True, ""),
        ("read.guest", "admin", "read", "Guest", "One guest", "{guest_name}, {phone}, {nationality}, {status}.", "guests.html", "list", "Guest", _GUEST, "", (("Update", "Change the profile", "input.guest_update"), ("Thread", "Messages", "read.guest_thread"), ("WhatsApp", "Send a message", "input.guest_whatsapp")), False, "read.guests"),
        ("input.guest_create", "admin", "input", "Add guest", "Create a profile", "Reply with the name and phone.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.guest_update", "admin", "input", "Update guest", "Change a profile", "Reply with the phone and the field to change.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.guest_deactivate", "admin", "input", "Deactivate guest", "Hide a profile", "Reply with the phone to deactivate.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.guest_thread", "admin", "read", "Guest thread", "Messages with a guest", "Reply with the phone to read the thread.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.guest_whatsapp", "admin", "input", "WhatsApp guest", "Text a guest", "Reply with the phone and the message. A free-text send needs an open window.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.guest_template", "admin", "input", "WA template", "Send an approved template", "Reply with the phone and the template name.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.loyalty", "admin", "read", "Loyalty", "A member", "Reply with the phone to read loyalty.", "guests.html", "list", "Loyalty", "", "", (("Enroll", "Enroll a guest", "input.loyalty_enroll"), ("Award", "Award points", "input.loyalty_award"), ("Redeem", "Redeem points", "input.loyalty_redeem")), True, ""),
        ("input.loyalty_enroll", "admin", "input", "Enroll loyalty", "Enroll a guest", "Reply with the phone to enroll.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.loyalty_award", "admin", "input", "Award points", "Give points", "Reply with the phone and the points.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.loyalty_redeem", "admin", "input", "Redeem points", "Use points", "Reply with the phone and the reward.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.occasions", "admin", "read", "Occasions", "Guest occasions", "Reply with the phone to read occasions.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.occasion", "admin", "input", "Add occasion", "Save an occasion", "Reply with the phone, the occasion, and the date.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.preferences", "admin", "read", "Preferences", "Guest preferences", "Reply with the phone to read preferences.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.preference", "admin", "input", "Add preference", "Save a preference", "Reply with the phone and the preference.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.requests", "admin", "read", "Requests", "Guest requests", "Open guest requests.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.request", "admin", "input", "Add request", "Save a request", "Reply with the guest and the request.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.complaint", "admin", "input", "Complaint", "Record a complaint", "Reply with the guest and the complaint.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.concierge", "admin", "read", "Concierge", "Concierge services", "Concierge services on file.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.concierge", "admin", "input", "Add concierge", "Save a service", "Reply with the service name.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.partnerships", "admin", "read", "Partnerships", "Partner accounts", "Partnerships on file.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.partnership", "admin", "input", "Add partner", "Save a partner", "Reply with the partner name.", "guests.html", "button", "", "", "", (), True, ""),
        ("input.referral", "admin", "input", "Referral", "Record a referral", "Reply with who referred the guest.", "guests.html", "button", "", "", "", (), True, ""),
        ("read.mobile_checkin", "admin", "read", "Mobile check-in", "A mobile check-in", "Reply with the booking to read mobile check-in.", "assignments.html", "list", "Mobile", "", "", (("Start", "Start mobile check-in", "input.mobile_start"), ("Issue key", "Send a key", "input.mobile_key")), True, ""),
        ("input.mobile_start", "admin", "input", "Start mobile", "Begin mobile check-in", "Reply with the booking to start.", "assignments.html", "button", "", "", "", (), True, ""),
        ("input.mobile_key", "admin", "input", "Issue key", "Send a mobile key", "Reply with the booking to issue a key.", "assignments.html", "button", "", "", "", (), True, ""),
        ("read.onboarding", "admin", "read", "Onboarding", "Setup still open", "Hotel setup still open.", "onboard.html", "list", "Onboarding", "", "", (("SOP", "Confirm a procedure", "input.onboard_sop"), ("Invite", "Invite a user", "input.onboard_invite"), ("Settings", "Operating settings", "read.settings")), True, ""),
        ("input.onboard_sop", "admin", "input", "Confirm SOP", "Accept a procedure", "Reply with the procedure to confirm.", "onboard.html", "button", "", "", "", (), True, ""),
        ("input.onboard_invite", "admin", "input", "Invite user", "Invite a person", "Reply with the name and email to invite.", "onboard.html", "button", "", "", "", (), True, ""),
        ("read.rooms", "admin", "read", "Rooms", "Rooms in the hotel", "Rooms and their readiness.", "rooms.html", "list", "Rooms", "", _ROOMS, (), True, ""),
        ("input.room_add", "admin", "input", "Add room", "Add a room number", "Reply with the room number and the room type.", "rooms.html", "button", "", "", "", (), True, ""),
        ("input.room_rename", "admin", "input", "Rename room", "Change a room number", "Reply with the current number and the new number.", "rooms.html", "button", "", "", "", (), True, ""),
        ("read.room_types", "admin", "read", "Room types", "Types on sale", "Room types on sale.", "rooms.html", "list", "Room types", "", _TYPES, (), True, ""),
        ("input.room_type", "admin", "input", "Add room type", "Create a type", "Reply with the room type name.", "rooms.html", "button", "", "", "", (), True, ""),
        ("read.inventory", "admin", "read", "Inventory", "Rooms to sell", "Rooms left by type for today.", "inventory.html", "list", "Inventory", "", _TYPES, (), True, ""),
        ("input.inventory", "admin", "input", "Update inventory", "Change rooms to sell", "Reply with the room type, the date, and the count.", "inventory.html", "button", "", "", "", (), True, ""),
        ("read.pricing", "admin", "read", "Rates", "Rates by type", "Today's rate by room type.", "inventory.html", "list", "Rates", "", _TYPES, (), True, ""),
        ("input.pricing", "admin", "input", "Update rate", "Change a rate", "Reply with the room type, the date, and the rate.", "inventory.html", "button", "", "", "", (), True, ""),
        ("read.rate_plans", "admin", "read", "Rate plans", "Plans on sale", "Rate plans.", "rooms.html", "button", "", "", "", (), True, ""),
        ("input.rate_plan", "admin", "input", "Add rate plan", "Create a plan", "Reply with the plan name.", "rooms.html", "button", "", "", "", (), True, ""),
        ("read.dynamic_pricing", "admin", "read", "Dynamic rates", "Demand pricing", "Dynamic pricing for the hotel.", "dynamic_pricing.html", "button", "", "", "", (), True, ""),
        ("input.dynamic_pricing", "admin", "input", "Edit dyn rates", "Change demand pricing", "Reply with the rule to change.", "dynamic_pricing.html", "button", "", "", "", (), True, ""),
    )


def _item(row):
    key, module, purpose, title, description, sentence, page, kind, button, complete_sql, choice_sql, choices, listed, back = row
    body = sentence if sentence.startswith("{") else "{hotel_name} on {local_date}. " + sentence
    return {
        "template_key": key, "module_name": module, "purpose": purpose, "interactive_type": kind,
        "header_text": title, "body_text": body, "footer_text": "{hotel_name}",
        "button_text": button if kind == "list" else "", "page_route": page,
        "complete_sql": complete_sql.strip(), "choice_sql": choice_sql.strip(),
        "back_template_key": back, "status": "active", "listed": listed, "choices": choices,
    }


def _page(key, module, title, description, page, choices, back):
    return {
        "template_key": key, "module_name": module, "purpose": "navigate", "interactive_type": "list",
        "header_text": title, "body_text": "{hotel_name} on {local_date}. " + description + ".",
        "footer_text": "{hotel_name}", "button_text": "Open", "page_route": page,
        "complete_sql": "", "choice_sql": "", "back_template_key": back, "status": "active",
        "listed": False, "choices": choices,
    }


def _paginate(module, title, description, page, group):
    pages = []
    remaining = list(group)
    index = 1
    previous = "nav.home"
    while remaining:
        more = len(remaining) > 9
        take = 8 if more else len(remaining)
        chunk = remaining[:take]
        remaining = remaining[take:]
        key = "nav." + module if index == 1 else "nav." + module + "." + str(index)
        label = title if index == 1 else (title + " " + str(index))
        choices = [(item["header_text"], item["body_text"].split(". ", 1)[-1][:72], item["template_key"]) for item in chunk]
        if remaining:
            choices.append(("More", "More " + title.lower(), "nav." + module + "." + str(index + 1)))
        pages.append(_page(key, module, label[:20], description, page, tuple(choices), previous))
        previous = key
        index += 1
    return pages


def _choice(template_key, order, title, description, next_key, row_id, purpose, section):
    return {
        "template_key": template_key, "choice_order": order, "row_id": row_id or next_key,
        "title": title, "description": description[:72], "section_title": section[:24] or "Menu",
        "choice_kind": purpose, "next_template_key": next_key,
    }


def build():
    """Templates and their static choices. Navigation pages are added in front of each module's listed actions."""
    items = [_item(row) for row in _leaves()]
    by_key = {}
    for item in items:
        if item["template_key"] in by_key:
            raise RuntimeError("duplicate template " + item["template_key"])
        by_key[item["template_key"]] = item
    pages = []
    home_choices = []
    for module, title, description, page in _MENUS:
        group = [item for item in items if item["listed"] and item["module_name"] == module]
        made = _paginate(module, title, description, page, group)
        pages.extend(made)
        for item in group:
            if not item["back_template_key"] and made:
                item["back_template_key"] = made[0]["template_key"]
        if made:
            home_choices.append((title, description, made[0]["template_key"]))
    home = _page("nav.home", "front_desk", "Leon", "Choose where to work", "dashboard.html", tuple(home_choices), "")
    templates = [home] + pages + items
    known = {}
    for item in templates:
        if item["template_key"] in known:
            raise RuntimeError("duplicate template " + item["template_key"])
        known[item["template_key"]] = item
        _fit(item)
    choices = []
    for item in templates:
        section = item["header_text"][:24]
        seen = set()
        order = 1
        for choice in item["choices"]:
            title, description, next_key = choice[0], choice[1], choice[2]
            row_id = choice[3] if len(choice) > 3 else next_key
            target = known.get(next_key)
            if target is None:
                raise RuntimeError(item["template_key"] + " points at missing " + next_key)
            if row_id in seen:
                raise RuntimeError(item["template_key"] + " repeats " + row_id)
            seen.add(row_id)
            if len(title) > (20 if item["interactive_type"] == "button" else 24) or not title.strip():
                raise RuntimeError(item["template_key"] + " title " + title)
            choices.append(_choice(item["template_key"], order, title, description, next_key, row_id, target["purpose"], section))
            order += 1
        # The agent adds Back at read time, so a stored list keeps one row free and a stored button keeps one button free.
        limit = 2 if item["interactive_type"] == "button" else 9
        if not item["back_template_key"]:
            limit += 1
        if order - 1 > limit:
            raise RuntimeError(item["template_key"] + " has " + str(order - 1) + " choices")
        back = item["back_template_key"]
        if back and back not in known:
            raise RuntimeError(item["template_key"] + " returns to missing " + back)
    if len(templates) < 100:
        raise RuntimeError("interactive catalog has " + str(len(templates)) + " templates")
    stored = []
    for item in templates:
        stored.append({key: item[key] for key in (
            "template_key", "module_name", "purpose", "interactive_type", "header_text", "body_text", "footer_text",
            "button_text", "page_route", "complete_sql", "choice_sql", "back_template_key", "status")})
    return stored, choices


def _fit(item):
    if item["interactive_type"] not in ("list", "button"):
        raise RuntimeError(item["template_key"] + " type")
    if item["purpose"] not in ("navigate", "read", "input"):
        raise RuntimeError(item["template_key"] + " purpose")
    if len(item["header_text"]) > 60 or len(item["body_text"]) > 1024 or len(item["footer_text"]) > 60:
        raise RuntimeError(item["template_key"] + " text length")
    if not item["body_text"].strip():
        raise RuntimeError(item["template_key"] + " needs a body")
    if item["interactive_type"] == "list" and not (1 <= len(item["button_text"]) <= 20):
        raise RuntimeError(item["template_key"] + " button")
    if item["interactive_type"] == "button" and item["button_text"]:
        raise RuntimeError(item["template_key"] + " button text")
    for sql in (item["complete_sql"], item["choice_sql"]):
        if not sql:
            continue
        if ";" in sql or not sql.lstrip().lower().startswith(("select", "with")):
            raise RuntimeError(item["template_key"] + " sql")
