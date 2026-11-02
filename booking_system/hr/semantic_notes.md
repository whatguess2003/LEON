# HR tables for the SQL agent and catalog agent (module `hr`)

Every id is text `<prefix>_<uuid4>`. Every table has `hotel_id`; every read filters `hotel_id = %(hotel_id)s` on each HR table it touches. Names are stored UPPER and trimmed. The session `TimeZone` is the hotel's zone, so `CURRENT_DATE` is the hotel-local today and `(ts AT TIME ZONE current_setting('TimeZone'))` is the hotel-local time of a `timestamptz`.

## Tables

| Table | Id (prefix) | Columns | Notes |
|---|---|---|---|
| `department` | `department_id` (`dep_`) | hotel_id, department_name, department_type (`operated` / `undistributed` / `fixed`) | Shared with other modules. UNIQUE (hotel_id, department_name). |
| `staff_position` | `position_id` (`pos_`) | hotel_id, department_id, position_name | A job title within a department. |
| `staff` | `staff_id` (`stf_`) | hotel_id, staff_name, department_id (home department), position_id, user_id (login, nullable), employment_type (`full_time` / `part_time` / `casual` / `contract`), status (`active` / `inactive` / `terminated`), hired_on, terminated_on, created_at | One row per person per hotel. `user_id` → `user_account` when the person also logs in. |
| `staff_identity` | `staff_identity_id` (`sid_`) | hotel_id, staff_id, identity_type (`phone` / `email`), identity_value | Phones are E.164, emails lowercase. UNIQUE (hotel_id, identity_type, identity_value). Find a staff member by phone or email here, never by a column on `staff`. |
| `shift` | `shift_id` (`shf_`) | hotel_id, shift_name, starts_at TIME, ends_at TIME, break_minutes | `ends_at < starts_at` means the shift ends the next day (e.g. NIGHT 23:00–07:00). |
| `roster` | `roster_id` (`ros_`) | hotel_id, staff_id, shift_id, department_id, work_date, created_by_user_id | One planned shift for one person on one date. `department_id` is the department worked for that shift (may differ from `staff.department_id`). UNIQUE (staff_id, work_date, shift_id). |
| `staff_attendance` | `attendance_id` (`att_`) | hotel_id, staff_id, roster_id (NULL = unrostered), clock_in, clock_out (NULL = still on duty), clock_in_by_user_id, clock_out_by_user_id, corrected_by_user_id, corrected_at | `timestamptz`. At most one open row per staff. |
| `leave_type` | `leave_type_id` (`lvt_`) | hotel_id, leave_type_name, paid | e.g. ANNUAL, SICK, UNPAID. |
| `leave_request` | `leave_request_id` (`lvr_`) | hotel_id, staff_id, leave_type_id, start_date, end_date (inclusive), status (`requested` / `approved` / `rejected` / `cancelled`), requested_by_user_id, requested_at, decided_by_user_id, decided_at, cancelled_by_user_id, cancelled_at | Open requests (`requested`, `approved`) of one person never overlap. |

Personal content (address, emergency contact, ID document) is the `content_document` with `database_name = 'leon_hr'`, `collection_name = 'staff_profile'`, `document_key = staff_id`. It is not for recipes.

## Joins

- `staff.department_id = department.department_id`; `staff.position_id = staff_position.position_id`; `staff.user_id = user_account.user_id`.
- `staff_identity.staff_id = staff.staff_id`.
- `roster.staff_id = staff.staff_id`, `roster.shift_id = shift.shift_id`, `roster.department_id = department.department_id`.
- `staff_attendance.staff_id = staff.staff_id`; `staff_attendance.roster_id = roster.roster_id` (LEFT JOIN: unrostered clock-ins have none).
- `leave_request.staff_id = staff.staff_id`, `leave_request.leave_type_id = leave_type.leave_type_id`.
- Actor columns (`*_by_user_id`) → `user_account.user_id`.

## Standard readings

- **Shift start (local):** `roster.work_date + shift.starts_at` (a `timestamp`).
- **On duty now:** `staff_attendance.clock_out IS NULL`.
- **Clock-in date:** `(clock_in AT TIME ZONE current_setting('TimeZone'))::date`.
- **Late minutes:** `greatest(0, extract(epoch FROM (clock_in AT TIME ZONE current_setting('TimeZone')) - (work_date + starts_at)) / 60)`; only for rostered clock-ins.
- **Worked hours:** `extract(epoch FROM clock_out - clock_in) / 3600`; open rows have none. Breaks are not deducted.
- **On approved leave on date D:** `EXISTS (leave_request l WHERE l.staff_id = … AND l.status = 'approved' AND D BETWEEN l.start_date AND l.end_date)`.
- **Missed shift:** a roster row whose local start has passed, with no `staff_attendance.roster_id` pointing to it, and the person not on approved leave that date.
- **Leave days:** calendar days `end_date - start_date + 1`, clipped to a period with `LEAST` / `GREATEST`.
- **Assignable for a task** (same rule as `hr.service.check_assignable`): `staff.status = 'active'`, a roster row for that date in the task's department, and no approved leave on that date.

## General recipes (booking_system/hr/recipes.py)

| catalog_id | Default output | Grain |
|---|---|---|
| `daily_staff_on_duty` | staff_clocked_in | night (period rows) |
| `daily_attendance_punctuality` | late_clock_ins | night (period rows) |
| `daily_leave_overview` | leave_requests_pending (+ `all_open` row) | night (period rows) |
| `daily_roster_coverage_by_department` | rostered_shifts | department (period × department rows) |

All four use the standard period rows from `seed_general._PERIODS` and declare `module_name = 'hr'`.

## Not stored (answer "not recorded", don't infer)

Pay, salary, payroll, benefits, recruitment, appraisals, training, statutory HR data, minimum staffing targets, leave entitlements and balances.
