"""Bootstrap rows for setting_catalog. The table is the catalog the backend reads: startup inserts a key only when the table has
no row for it, and never rewrites or retires a row that is already stored. Per-hotel answers are versioned rows in hotel_setting.
Nothing here is a hotel's value.

Fields: setting_key, module_name (whose operation reads it), question, explanation, value_type (boolean | choice | choices | time |
amount | tax_codes), options [{value, meaning}], required (the module refuses while unset), required_when {other_key: value} (required
only while those settings hold those values), set_module / set_level (the access a user needs to change it), sop_prompt (non-empty:
the hotel's SOP text for this choice is asked and kept as a document), display_order, effective_basis (which date a change is
dated against: business_date for modules whose operations post on the finance business date, local_date otherwise)."""

# Per module: the date its operations are dated by, so a change cannot take effect on a business date that is already closed.
EFFECTIVE_BASIS = {"front_desk": "local_date", "finance": "business_date", "fnb": "business_date", "hr": "local_date"}

_WEEKDAYS = [{"value": day, "meaning": name} for day, name in (
    ("mon", "Monday"), ("tue", "Tuesday"), ("wed", "Wednesday"), ("thu", "Thursday"), ("fri", "Friday"), ("sat", "Saturday"), ("sun", "Sunday"))]

_ROWS = [
    {"setting_key": "front_desk.checkin_time", "module_name": "front_desk", "value_type": "time", "options": [], "required": True,
     "question": "From what local time (HH:MM) can guests check in?",
     "explanation": "Standard check-in time; arrival messages, mobile check-in and digital keys use it.",
     "set_module": "front_desk", "set_level": "manage", "sop_prompt": "", "display_order": 10},
    {"setting_key": "front_desk.checkout_time", "module_name": "front_desk", "value_type": "time", "options": [], "required": True,
     "question": "By what local time (HH:MM) must guests check out?",
     "explanation": "Standard check-out time; a digital key stops working at this time on the departure day.",
     "set_module": "front_desk", "set_level": "manage", "sop_prompt": "", "display_order": 11},
    {"setting_key": "front_desk.night_audit_time", "module_name": "front_desk", "value_type": "time", "options": [], "required": True,
     "question": "At what local time (HH:MM) does your night audit close the business day?",
     "explanation": "The time the business date is rolled; between midnight and this time the open business date is yesterday.",
     "set_module": "front_desk", "set_level": "manage", "sop_prompt": "", "display_order": 12},
    {"setting_key": "front_desk.checkin_room_readiness", "module_name": "front_desk", "value_type": "choice", "required": True,
     "options": [{"value": "clean_or_inspected", "meaning": "a room marked clean can be given to a guest before a supervisor inspects it"},
                 {"value": "inspected_only", "meaning": "only a room a supervisor has inspected can be given to a guest"}],
     "question": "Can a clean room be checked in before it is inspected, or only inspected rooms?",
     "explanation": "Which housekeeping readiness a room needs before check-in or a room move of an in-house guest.",
     "set_module": "front_desk", "set_level": "manage", "sop_prompt": "Describe your room release and inspection procedure.", "display_order": 13},
    {"setting_key": "front_desk.late_checkout_fee", "module_name": "front_desk", "value_type": "amount", "options": [], "required": False,
     "question": "What late-departure fee, in the hotel currency, applies after check-out time?",
     "explanation": "Charged once when a guest departs after the check-out time. Leave it unset, or set it to zero, when a late departure has no fee.",
     "set_module": "front_desk", "set_level": "manage", "sop_prompt": "", "display_order": 14},

    {"setting_key": "finance.enabled", "module_name": "finance", "value_type": "boolean", "options": [], "required": True,
     "question": "Will you post folio charges, payments, cashier shifts and the night audit in LEON (the finance module)?",
     "explanation": "While false the finance module is refused; turning it on asks the finance settings below.",
     "set_module": "admin", "set_level": "manage", "sop_prompt": "", "display_order": 20},
    {"setting_key": "finance.tax_pricing", "module_name": "finance", "value_type": "choice", "required": True,
     "options": [{"value": "exclusive", "meaning": "prices are before tax; taxes are added on top"},
                 {"value": "inclusive", "meaning": "prices already include tax; the tax is taken out of the price"}],
     "question": "Are your prices (room rates, menu prices, charges) tax-exclusive or tax-inclusive?",
     "explanation": "Exclusive: a 100.00 charge posts 100.00 revenue plus tax. Inclusive: a 100.00 charge is split into revenue and tax that add up to 100.00.",
     "set_module": "finance", "set_level": "manage", "sop_prompt": "Describe how prices and taxes are shown to guests and on invoices.",
     "display_order": 21},
    {"setting_key": "finance.tax_calculation", "module_name": "finance", "value_type": "choice", "required": True,
     "options": [{"value": "simple", "meaning": "every tax is computed on the net amount"},
                 {"value": "compound", "meaning": "taxes apply in a set order, each on the net plus the taxes before it (e.g. tax on service charge)"}],
     "question": "Is each tax computed on the net amount only, or are taxes compounded (for example government tax charged on the service charge too)?",
     "explanation": "Compound taxes need the order in which they apply (finance.tax_order).",
     "set_module": "finance", "set_level": "manage", "sop_prompt": "", "display_order": 22},
    {"setting_key": "finance.tax_order", "module_name": "finance", "value_type": "tax_codes", "options": [], "required": True,
     "required_when": {"finance.tax_calculation": "compound"},
     "question": "In what order do your taxes apply? List every tax code, first applied first (each later tax is charged on the ones before it).",
     "explanation": "The compounding order of the hotel's tax codes; asked only for compound tax.",
     "set_module": "finance", "set_level": "manage", "sop_prompt": "", "display_order": 23},
    {"setting_key": "finance.cashier_variance_tolerance", "module_name": "finance", "value_type": "amount", "options": [], "required": True,
     "question": "What is the largest cash over or short (in the hotel currency) a cashier may close a shift with, without a written reason?",
     "explanation": "A drawer variance beyond this amount needs the cashier's reason before the shift closes.",
     "set_module": "finance", "set_level": "manage", "sop_prompt": "Describe your cash drawer counting and variance procedure.",
     "display_order": 24},

    {"setting_key": "fnb.enabled", "module_name": "fnb", "value_type": "boolean", "options": [], "required": True,
     "question": "Do you sell food and beverage through outlets (restaurant, bar, room service) that LEON should run?",
     "explanation": "While false the F&B module is refused; turning it on asks the F&B settings below.",
     "set_module": "admin", "set_level": "manage", "sop_prompt": "", "display_order": 30},
    {"setting_key": "fnb.closed_check_correction", "module_name": "fnb", "value_type": "choice", "required": True,
     "options": [{"value": "folio_adjustment", "meaning": "a settled check stays closed; mistakes are corrected with an adjustment posting on its folio"},
                 {"value": "reopen_with_approval", "meaning": "a settled check is reopened after a manager approves; its postings are reversed and it is settled again"}],
     "question": "When a settled check is wrong, do you correct it with a folio adjustment, or reopen it with a manager's approval?",
     "explanation": "The only correction path F&B allows for a closed check.",
     "set_module": "fnb", "set_level": "manage", "sop_prompt": "Describe your check correction procedure (who may ask, who approves).",
     "display_order": 31},

    {"setting_key": "hr.enabled", "module_name": "hr", "value_type": "boolean", "options": [], "required": True,
     "question": "Will you keep staff, rosters, attendance and leave in LEON (the HR module)?",
     "explanation": "While false the HR module is refused; turning it on asks the HR settings below.",
     "set_module": "admin", "set_level": "manage", "sop_prompt": "", "display_order": 40},
    {"setting_key": "hr.worked_hours_breaks", "module_name": "hr", "value_type": "choice", "required": True,
     "options": [{"value": "deduct_unpaid_breaks", "meaning": "worked hours are clock-out minus clock-in minus the rostered shift's break"},
                 {"value": "breaks_paid", "meaning": "worked hours are clock-out minus clock-in; breaks count as work"}],
     "question": "Are staff breaks unpaid (deducted from worked hours) or paid (counted as work)?",
     "explanation": "How attendance turns into worked hours.",
     "set_module": "hr", "set_level": "manage", "sop_prompt": "", "display_order": 41},
    {"setting_key": "hr.leave_day_counting", "module_name": "hr", "value_type": "choice", "required": True,
     "options": [{"value": "calendar_days", "meaning": "every calendar day from the first to the last leave day counts"},
                 {"value": "working_days", "meaning": "only the hotel's working weekdays inside the leave count"}],
     "question": "Does leave count every calendar day, or only working days?",
     "explanation": "How many leave days a request uses; working days need the working weekdays (hr.working_weekdays).",
     "set_module": "hr", "set_level": "manage", "sop_prompt": "Describe your leave policy (entitlements, approval).", "display_order": 42},
    {"setting_key": "hr.working_weekdays", "module_name": "hr", "value_type": "choices", "options": _WEEKDAYS, "required": True,
     "required_when": {"hr.leave_day_counting": "working_days"},
     "question": "Which weekdays are working days for leave counting?",
     "explanation": "Leave days on other weekdays are not counted.",
     "set_module": "hr", "set_level": "manage", "sop_prompt": "", "display_order": 43},
]
CATALOG = [dict(row, effective_basis=EFFECTIVE_BASIS[row["module_name"]]) for row in _ROWS]
