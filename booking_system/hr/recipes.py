"""General HR recipes (module hr), in the seed_general body format. Not published here; the coordinator adds them to the catalog.
Run validation (read-only): python -m booking_system.hr.recipes"""

from booking_system.semantic.seed_general import PARENT, SOURCE, _PERIOD_NAMES, _PERIODS

MODULE = "hr"
VALIDATION_HOTEL = "SANDBOX HOTEL"
HOTEL = "(SELECT hotel_name FROM hotel WHERE hotel_id = %(hotel_id)s) AS hotel_name"
LOCAL_NOW = "(now() AT TIME ZONE current_setting('TimeZone'))"
PERIOD_KEYS = ["hotel_name", "period", "period_start", "period_end", "days_in_period"]
LIMITS = [
    "Rows cover only these hotel-local periods: " + _PERIOD_NAMES + ". Another calendar date or range is not answered by this recipe.",
    "Clock times are stored as timestamptz and read in the hotel's configured time zone; a clock-in belongs to its hotel-local date.",
    "Every reading reflects the current recorded state, not a snapshot taken on that date.",
]

_ROSTERED = (
    "rostered AS (SELECT r.roster_id, r.staff_id, r.department_id, r.work_date, r.work_date + f.starts_at AS starts_local, "
    "EXISTS (SELECT 1 FROM staff_attendance a WHERE a.roster_id = r.roster_id) AS attended, "
    "EXISTS (SELECT 1 FROM leave_request l WHERE l.staff_id = r.staff_id AND l.status = 'approved' "
    "AND r.work_date BETWEEN l.start_date AND l.end_date) AS on_leave "
    "FROM roster r JOIN shift f ON f.shift_id = r.shift_id WHERE r.hotel_id = %(hotel_id)s)"
)
_CLOCKED = (
    "clocked AS (SELECT a.attendance_id, a.staff_id, a.roster_id, a.clock_out, "
    "(a.clock_in AT TIME ZONE current_setting('TimeZone'))::date AS d, "
    "hr_worked_hours(a.hotel_id, COALESCE(r.work_date, (a.clock_in AT TIME ZONE current_setting('TimeZone'))::date), a.clock_in, a.clock_out, "
    "f.break_minutes) AS worked_hours, "
    "CASE WHEN r.roster_id IS NOT NULL THEN greatest(0, extract(epoch FROM (a.clock_in AT TIME ZONE current_setting('TimeZone')) "
    "- (r.work_date + f.starts_at)) / 60) END AS late_minutes "
    "FROM staff_attendance a LEFT JOIN roster r ON r.roster_id = a.roster_id LEFT JOIN shift f ON f.shift_id = r.shift_id "
    "WHERE a.hotel_id = %(hotel_id)s)"
)
_IN = " BETWEEN p.period_start AND p.period_end"
_ORDER = "ORDER BY p.period_end - p.period_start, p.period_start"


def _select(ctes, columns, tail=_ORDER, source="FROM periods p", keys="p.period, p.period_start, p.period_end, p.period_end - p.period_start + 1 AS days_in_period"):
    return (
        "WITH " + ",\n".join([_PERIODS] + ctes) + "\n"
        "SELECT " + HOTEL + ", " + keys + ",\n    "
        + ",\n    ".join(expr + " AS " + name for name, expr in columns) + "\n" + source + "\n" + tail
    )


_ON_DUTY_SQL = _select([_ROSTERED, _CLOCKED], [
    ("staff_clocked_in", "(SELECT COUNT(DISTINCT c.staff_id) FROM clocked c WHERE c.d" + _IN + ")"),
    ("rostered_staff", "(SELECT COUNT(DISTINCT r.staff_id) FROM rostered r WHERE r.work_date" + _IN + ")"),
    ("rostered_shifts", "(SELECT COUNT(*) FROM rostered r WHERE r.work_date" + _IN + ")"),
    ("attended_shifts", "(SELECT COUNT(*) FROM rostered r WHERE r.work_date" + _IN + " AND r.attended)"),
    ("missed_shifts", "(SELECT COUNT(*) FROM rostered r WHERE r.work_date" + _IN + " AND NOT r.attended AND NOT r.on_leave AND r.starts_local < " + LOCAL_NOW + ")"),
    ("rostered_shifts_on_leave", "(SELECT COUNT(*) FROM rostered r WHERE r.work_date" + _IN + " AND r.on_leave)"),
    ("unrostered_clock_ins", "(SELECT COUNT(*) FROM clocked c WHERE c.d" + _IN + " AND c.roster_id IS NULL)"),
    ("on_duty_now", "(SELECT COUNT(*) FROM staff_attendance a WHERE a.hotel_id = %(hotel_id)s AND a.clock_out IS NULL)"),
])

_PUNCTUALITY_SQL = _select([_CLOCKED], [
    ("clock_ins", "(SELECT COUNT(*) FROM clocked c WHERE c.d" + _IN + ")"),
    ("rostered_clock_ins", "(SELECT COUNT(*) FROM clocked c WHERE c.d" + _IN + " AND c.late_minutes IS NOT NULL)"),
    ("late_clock_ins", "(SELECT COUNT(*) FROM clocked c WHERE c.d" + _IN + " AND c.late_minutes >= 1)"),
    ("late_clock_ins_over_15_min", "(SELECT COUNT(*) FROM clocked c WHERE c.d" + _IN + " AND c.late_minutes > 15)"),
    ("avg_late_minutes", "(SELECT ROUND(AVG(c.late_minutes)::numeric, 1) FROM clocked c WHERE c.d" + _IN + " AND c.late_minutes >= 1)"),
    ("on_time_pct", "(SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE c.late_minutes < 1) / NULLIF(COUNT(c.late_minutes), 0), 2) FROM clocked c WHERE c.d" + _IN + ")"),
    ("missing_clock_outs", "(SELECT COUNT(*) FROM clocked c WHERE c.d" + _IN + " AND c.clock_out IS NULL AND c.d < CURRENT_DATE)"),
    ("worked_hours", "(SELECT COALESCE(ROUND(SUM(c.worked_hours)::numeric, 2), 0) FROM clocked c WHERE c.d" + _IN + ")"),
    ("avg_worked_hours", "(SELECT ROUND(AVG(c.worked_hours)::numeric, 2) FROM clocked c WHERE c.d" + _IN + ")"),
    ("break_rule_in_effect", "(SELECT string_agg(DISTINCT hotel_setting_at(%(hotel_id)s, 'hr.worked_hours_breaks', g.d::date) #>> '{}', ', ') "
                             "FROM generate_series(p.period_start, p.period_end, interval '1 day') g(d))"),
])

# Days of a leave inside the window, counted by the hotel's hr.leave_day_counting in effect on the leave's first day.
_LEAVE_DAYS = "hr_leave_days(%(hotel_id)s, GREATEST(q.start_date, w.period_start), LEAST(q.end_date, w.period_end), q.start_date)"
_LEAVE_SQL = (
    "WITH " + _PERIODS + ",\n"
    "requests AS (SELECT l.staff_id, l.status, l.start_date, l.end_date, t.paid FROM leave_request l "
    "JOIN leave_type t ON t.leave_type_id = l.leave_type_id WHERE l.hotel_id = %(hotel_id)s),\n"
    "windows AS (SELECT period, period_start, period_end, period_end - period_start + 1 AS days_in_period FROM periods)\n"
    "SELECT " + HOTEL + ", w.period, w.period_start, w.period_end, w.days_in_period,\n    "
    "COUNT(q.status) FILTER (WHERE q.status = 'requested') AS leave_requests_pending,\n    "
    "COUNT(q.status) FILTER (WHERE q.status = 'approved') AS leave_requests_approved,\n    "
    "COUNT(DISTINCT q.staff_id) FILTER (WHERE q.status = 'approved') AS staff_on_approved_leave,\n    "
    "COALESCE(SUM(" + _LEAVE_DAYS + ") FILTER (WHERE q.status = 'requested'), 0) AS pending_leave_days,\n    "
    "COALESCE(SUM(" + _LEAVE_DAYS + ") FILTER (WHERE q.status = 'approved'), 0) AS approved_leave_days,\n    "
    "COALESCE(SUM(" + _LEAVE_DAYS + ") FILTER (WHERE q.status = 'approved' AND NOT q.paid), 0) AS approved_unpaid_leave_days,\n    "
    "string_agg(DISTINCT hotel_setting_at(%(hotel_id)s, 'hr.leave_day_counting', q.start_date) #>> '{}', ', ') AS day_counting_in_effect\n"
    "FROM windows w LEFT JOIN requests q ON q.status IN ('requested', 'approved') "
    "AND q.start_date <= w.period_end AND q.end_date >= w.period_start\n"
    "GROUP BY w.period, w.period_start, w.period_end, w.days_in_period\n"
    "ORDER BY w.period_end - w.period_start, w.period_start"
)

_COVERAGE_SQL = _select(
    [_ROSTERED, "departments AS (SELECT department_id, department_name FROM department WHERE hotel_id = %(hotel_id)s)"],
    [
        ("active_staff", "(SELECT COUNT(*) FROM staff s WHERE s.hotel_id = %(hotel_id)s AND s.department_id = d.department_id AND s.status = 'active')"),
        ("rostered_staff", "(SELECT COUNT(DISTINCT r.staff_id) FROM rostered r WHERE r.department_id = d.department_id AND r.work_date" + _IN + ")"),
        ("rostered_shifts", "(SELECT COUNT(*) FROM rostered r WHERE r.department_id = d.department_id AND r.work_date" + _IN + ")"),
        ("rostered_shifts_on_leave", "(SELECT COUNT(*) FROM rostered r WHERE r.department_id = d.department_id AND r.work_date" + _IN + " AND r.on_leave)"),
        ("started_shifts", "(SELECT COUNT(*) FROM rostered r WHERE r.department_id = d.department_id AND r.work_date" + _IN + " AND NOT r.on_leave AND r.starts_local < " + LOCAL_NOW + ")"),
        ("attended_shifts", "(SELECT COUNT(*) FROM rostered r WHERE r.department_id = d.department_id AND r.work_date" + _IN + " AND r.attended)"),
        ("attendance_pct", "(SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE r.attended) / NULLIF(COUNT(*), 0), 2) FROM rostered r "
                           "WHERE r.department_id = d.department_id AND r.work_date" + _IN + " AND NOT r.on_leave AND r.starts_local < " + LOCAL_NOW + ")"),
    ],
    tail="ORDER BY p.period_end - p.period_start, p.period_start, d.department_name",
    source="FROM periods p CROSS JOIN departments d",
    keys="p.period, p.period_start, p.period_end, p.period_end - p.period_start + 1 AS days_in_period, d.department_name",
)


def _recipe(catalog_id, title, problem, keyphrases, default, concepts, columns, limitations, sql, grain="night", keys=None):
    outputs = list(keys or PERIOD_KEYS) + list(columns)
    return {
        "catalog_id": catalog_id,
        "parent": PARENT,
        "module_name": MODULE,
        "title": title,
        "purpose": problem,
        "problem": problem,
        "preconditions": [
            "The hotel has a configured time zone; CURRENT_DATE is the hotel-local today.",
            "Only the session hotel is bound; the read filters hotel_id = %(hotel_id)s. Select the row whose period is the requested day or range.",
        ],
        "inputs": {
            "hotel_id": "bound by the platform from the session hotel",
            "start": "MM/DD/YYYY inclusive start, bound as %(start)s and read with to_date(start, 'MM/DD/YYYY')",
            "end": "MM/DD/YYYY inclusive end, bound as %(end)s and read with to_date(end, 'MM/DD/YYYY')",
        },
        "output_grain": grain,
        "outputs": outputs,
        "default_output": default,
        "limitations": LIMITS + ["Default reading: " + default + ". The other readings are alternatives a manager definition may choose."] + list(limitations),
        "verification": "Executed for the validation hotel against the HR seed (booking_system.hr.seed); today and yesterday rows checked against independent reads of roster, staff_attendance and leave_request.",
        "keyphrases": list(keyphrases),
        "aliases": list(keyphrases),
        "concepts": [{"name": name, "formula": formula, "tables": tables} for name, formula, tables in concepts],
        "solution": {"sql_template": sql, "parameters": ["hotel_id", "start", "end"], "method": SOURCE},
        "validation": {"expected_value": ""},
    }


RECIPES = [
    _recipe(
        "daily_staff_on_duty", "Staff on duty by period",
        "How many staff worked (clocked in) on a hotel-local day or range, against how many were rostered, with missed and on-leave shifts and the number clocked in right now.",
        ["staff on duty", "who is working today", "how many staff today", "staff working", "on duty staff", "staff clocked in", "missed shifts", "no shows staff"],
        "staff_clocked_in",
        [("staff_clocked_in", "COUNT(DISTINCT staff_attendance.staff_id) by hotel-local clock-in date", ["staff_attendance"]),
         ("missed_shifts", "roster shifts already started, with no attendance linked and no approved leave", ["roster", "shift", "staff_attendance", "leave_request"])],
        ["staff_clocked_in", "rostered_staff", "rostered_shifts", "attended_shifts", "missed_shifts", "rostered_shifts_on_leave", "unrostered_clock_ins", "on_duty_now"],
        ["attended_shifts counts roster shifts with a linked clock-in; a clock-in without a roster is counted in unrostered_clock_ins only.",
         "missed_shifts counts only shifts whose start time (hotel local) has passed; future shifts are not missed.",
         "on_duty_now is the current number of open clock-ins and is the same on every row."],
        _ON_DUTY_SQL,
    ),
    _recipe(
        "daily_attendance_punctuality", "Attendance and late clock-ins by period",
        "Clock-ins on a hotel-local day or range: how many were late against the rostered shift start, average lateness, on-time rate, missing clock-outs and hours worked.",
        ["late clock ins", "late arrivals staff", "staff punctuality", "attendance", "who was late", "on time rate", "hours worked", "missing clock out", "forgot to clock out"],
        "late_clock_ins",
        [("late_minutes", "local clock_in minus (roster.work_date + shift.starts_at), floored at 0; NULL when unrostered", ["staff_attendance", "roster", "shift"]),
         ("worked_hours", "hr_worked_hours: clock_out - clock_in in hours over closed clock-ins, less the rostered shift's break_minutes "
                          "when the hotel's hr.worked_hours_breaks in effect on the work date is deduct_unpaid_breaks", ["staff_attendance", "roster", "shift"])],
        ["clock_ins", "rostered_clock_ins", "late_clock_ins", "late_clock_ins_over_15_min", "avg_late_minutes", "on_time_pct",
         "missing_clock_outs", "worked_hours", "avg_worked_hours", "break_rule_in_effect"],
        ["Late means at least 1 minute after the rostered shift start; there is no grace period. Unrostered clock-ins have no lateness.",
         "worked_hours follows the hotel's setting hr.worked_hours_breaks in effect on each work date: deduct_unpaid_breaks subtracts the "
         "rostered shift's break (an unrostered clock-in has no scheduled break), breaks_paid counts the whole clocked time. It excludes "
         "clock-ins that are still open. break_rule_in_effect lists the setting over the period (two values when it changed inside it). "
         "The read is refused while the hotel has not answered the setting.",
         "missing_clock_outs counts open clock-ins from before today."],
        _PUNCTUALITY_SQL,
    ),
    _recipe(
        "daily_leave_overview", "Leave requests and staff on leave by period",
        "Leave requests pending approval and approved leave overlapping the span from start through end, with leave days inside that span.",
        ["leave pending approval", "pending leave", "leave requests", "who is on leave", "staff on leave", "leave days", "time off requests", "annual leave", "sick leave"],
        "leave_requests_pending",
        [("leave_requests_pending", "COUNT(leave_request) with status requested overlapping the period", ["leave_request"]),
         ("approved_leave_days", "SUM of hr_leave_days of approved leave clipped to the period", ["leave_request", "leave_type"])],
        ["leave_requests_pending", "leave_requests_approved", "staff_on_approved_leave", "pending_leave_days", "approved_leave_days", "approved_unpaid_leave_days",
         "day_counting_in_effect"],
        ["A request is counted in every period it overlaps; its days inside the period are counted by the hotel's hr.leave_day_counting "
         "in effect on the leave's first day: calendar_days counts every day, working_days counts only the weekdays in hr.working_weekdays. "
         "day_counting_in_effect lists the rules the counted leaves used. The read is refused while the hotel has not answered the setting.",
         "The row is the one span from start through end inclusive.",
         "Rejected and cancelled requests are not counted."],
        _LEAVE_SQL,
    ),
    _recipe(
        "daily_roster_coverage_by_department", "Roster coverage by department and period",
        "For each department and hotel-local day or range: active headcount, rostered staff and shifts, shifts lost to approved leave, and the share of started shifts that were attended.",
        ["roster coverage", "coverage by department", "rostered staff by department", "department staffing", "headcount by department", "shifts by department", "staffing level"],
        "rostered_shifts",
        [("attendance_pct", "100 * attended / started roster shifts not on approved leave", ["roster", "shift", "staff_attendance", "leave_request"]),
         ("active_staff", "COUNT(staff) with status active in the department (current)", ["staff", "department"])],
        ["active_staff", "rostered_staff", "rostered_shifts", "rostered_shifts_on_leave", "started_shifts", "attended_shifts", "attendance_pct"],
        ["A roster's department is the department it was rostered for, which can differ from the staff member's home department.",
         "active_staff is the current headcount and is the same in every period.",
         "There is no required-headcount (minimum staffing) table; coverage is rostered and attended shifts, not a target."],
        _COVERAGE_SQL,
        grain="department",
        keys=PERIOD_KEYS + ["department_name"],
    ),
]


def validate(hotel_name=VALIDATION_HOTEL):
    """Contract and validate_publication (reads only) for each recipe; nothing is written to the catalog."""
    from booking_system.semantic.hotel import session_hotel_id
    from booking_system.semantic.library import validate_publication
    from booking_system.semantic.query import execute_reads
    from booking_system.semantic.recipe import recipe_contract

    results = []
    for body in RECIPES:
        report = validate_publication(body["catalog_id"], body)
        rows = execute_reads([body["solution"]["sql_template"]], {"hotel_id": session_hotel_id(hotel_name)}, limit=None)
        results.append({"catalog_id": body["catalog_id"], "contract_missing": recipe_contract(body), "publication": report,
                        "rows": rows["rows"], "error": rows["error"]})
    return results


if __name__ == "__main__":
    import json
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    for result in validate(*sys.argv[1:]):
        print(json.dumps(result, default=str), flush=True)
