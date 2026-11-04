# World Mode: repository audit

Audit date: 2026-11-04. Repositories inspected: `LEON` (PMS API), `LEON_UI` (static web UI), `CM` (channel manager) and `SANDBOX_MANAGER`.
Only LEON and LEON_UI are touched by World Mode.

## Backend (LEON)

| Area | Finding |
|---|---|
| Framework | FastAPI (`main.py`), uvicorn, Python 3.12, psycopg2. Area routers live in `booking_system/routes/*`; department routers in `finance/`, `fnb/`, `hr/`. |
| Database | PostgreSQL is authoritative. DDL is idempotent and runs at startup (`setup_schema.py` → `core_schema`, workflow tables, departments). Some documents are kept in `content_document` (JSONB). |
| Auth | HS256 JWT (`leon_jwt` cookie or `Authorization: Bearer`). Claims carry `hotel_id`, `user_id`, `modules` (per-module level), `hotel_timezone`, `hotel_currency`, `access_version`. |
| Tenancy and RBAC | `booking_system/access.guard` middleware: every route must be public, an account route, or have a registered access point `("METHOD /path", module, level)`, or **startup fails**. On each request it checks that the membership is active, that `access_version` is current, and that the module level is high enough; it refuses any `hotel_id` / `hotel_name` in the query that is not the session's. Levels: none < view < operate < manage. The role matrix is `access.ROLES`. |
| Events | No websocket or SSE. The UI polls. A job runner (`booking_system/jobs.py`) handles background work. |
| Calendar read path | `GET /api/assignments/calendar?start_date&end_date[&room_type&room_numbers]` (front_desk.view) → `front_desk_manager.read_room_number_assignments_calendar_internal`. One row per room per hotel-local night (`booking_room_night.stay_date`). `stay_status` is `assigned` (booked), `checked_in` or `checked_out`. The read adds the night before `start`; the route reads through `end+1` so the last check-out shows. Out-of-order rows come from `room_block`. Nights without a room are listed in `unassigned_by_type`. |
| Booking detail | `GET /api/assignments/detail?booking_id&stay_date` → `read_booking_detail_internal`: guest, booking, payment, and the rooms for that night. |
| Readiness | `room_readiness_status` (clean, dirty, cleaning, inspected). Read all: `read_all_room_readiness_status_internal`. |
| Read recipes | The semantic catalog (`booking_system/semantic/*`): `get_recipe` then `run_sql`, used by the LEON desk agent. World Mode does **not** use run_sql. It calls the same typed desk services the classic UI uses. |
| Action recipes | `booking_system/action_recipe.py`. A published recipe page (`seed_general.ACTION_RECIPES`; recipe id = catalog id, e.g. `reservation_check_in`) names a registered backend action (`_REGISTRY`, e.g. `reservation.check_in` → `desk_actions._PERFORM["check_in"]`), its parameter contract and its permission. `prepare_action(hotel_id, user_id, recipe_id, params)` validates the contract and the preview, then stores a `leon_pending_action` row (`pending`, 30-minute TTL, idempotency key) and returns an approval card. **Nothing changes yet.** `POST /api/leon/actions/{id}/approve` (front_desk.operate plus the recipe's own permission) runs the action once and records `approved` or `failed`. A second approve returns the stored result. `/reject` cancels. |
| Media | **No room or facility photo storage existed.** The only binary store is `guest_media_file` (WhatsApp attachments). Room types have no image column. |
| Settings / flags | Versioned per-hotel settings (`settings/catalog.py` + `hotel_setting`, `hotel_setting_accept`). There was no feature-flag convention beyond that. |
| Tests / CI | No CI config and no pytest suite. The fixtures are scripts: `fixtures/sandbox.py` (5-room sandbox hotel), `fixtures/test_hotel.py` (its data module `test_hotel_data.py` is missing from the repository), and `fixtures/stress/*`. |
| Deploy | Docker (`Dockerfile.pms`) on Cloudflare Containers (`wrangler.pms.jsonc`, `workers/pms.js`). |

## Frontend (LEON_UI)

* Plain HTML/CSS/JS with no build step, Tailwind 2 and Font Awesome from CDNs. Deployed as static assets on a Cloudflare Worker.
* `js/session.js` wraps `fetch` to add the bearer token and to send a 401 to `login.html`. API origin: `localhost:8002` locally, otherwise `api.inap.ai`.
* The classic Stay View is `assignments.html` + `js/components/assignments_calendar.js` (about 3,200 lines, DOM table, drag to reassign). It accepts `?start=&end=`.
* **There is no mobile client and no 2D rendering stack.** The brief's "Light / Medium" modes do not exist as named modes: the only Stay View is the classic table.

## Decision

The brief prefers Flutter + Flame for Android/iOS when no mobile app exists. For the P1 slice I built Fun Mode **into the existing
web PMS** instead, as a dependency-free Canvas 2D page (`LEON_UI/world.html`). Reasons:

1. Definition of Done #1: "open the current LEON PMS via the existing workflow and switch into Fun Mode", and #7 "switch back". A
   web page beside `assignments.html` meets both with the same session, the same deploy and no app-store release.
2. No Flutter SDK, Android SDK or iOS toolchain is available in this environment, so a Flutter client could not be built or verified
   here. A web canvas can be exercised end to end (Playwright, real API, real data, phone emulation).
3. The brief's renderer-independence rule is kept. Everything a Flutter/Flame client needs is a server contract: `/api/world/*`, the
   scene manifest schema and the approval routes. The client logic that is not about drawing (`js/world/world-core.js`: date math,
   stay geometry, hit testing, gestures, advisor rules, outcome rules) is pure and portable, and the tests document it. A Flame
   client can follow without backend changes.

## Extension points used (minimal)

* New router `booking_system/world/api.py`, registered with `access.register(ACCESS)`, so the guard enforces every point.
* New tables `world_media`, `world_scene_version` and `world_scene_active` (`booking_system/world/schema.py`), hooked into `setup_schema.py`. No PMS truth is copied.
* One new per-hotel setting, `front_desk.world_mode` (boolean, default unset = off, set by admin.manage), plus the server switch `WORLD_MODE_ENABLED`.
* UI: new `world.html`, `css/world.css`, `js/world/*`, and one script tag in `assignments.html` that adds the Fun Mode switch only when World Mode is on.

## Integration risks

* **Environment `DATABASE_URL`.** In this container the process environment already pointed `DATABASE_URL` at a remote Railway host,
  and `load_dotenv()` (without override) does not replace it. Local runs must set it explicitly. The integration tests refuse
  non-local databases.
* `seed_general` needs `registry.initialize_catalog()` first on a fresh database, and the four action recipes must be published or
  `prepare` returns "action recipe is not available".
* PMS rules apply in World Mode exactly as in the classic view (check-in opening time, a balance due before check-out, required hotel
  settings). World Mode shows these refusals and does not hide them. On a fresh hotel most front-desk actions fail until its
  front-desk settings are answered.
* The calendar read is the classic read and fetches the whole window, so large hotels over 31 to 62 nights load more. The canvas
  culls drawing, but the payload is not paginated.
* Photos are stored as BYTEA in Postgres, like `guest_media_file`. That is fine for a few photos per room type; move to object
  storage with signed URLs before hotels upload large libraries.
