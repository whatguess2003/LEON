# LEON Schema Migration and Extension Plan (for approval)

Status (1 Nov 2026): **A0, A1, waves 2, 3 and 4 done** (see below). The whole database is test data. It was wiped and rebuilt from the SANDBOX fixture (`python -m fixtures.sandbox wipe | seed`). Only the general recipes (6 catalog / knowledge tables) and the structure are kept; the safety copy is `_backups/general_recipes_20261031_214842.json`.

**Wave 2 (core, 31 Oct): done.** A1b, A2, A4, A6 (adults / children / commission on `booking`), A8 guest part, C1 (`department` is a core table; HR, Finance and F&B DDL only verify it), C3 / C4 (Finance: codes, tax, folios, postings, business day, cashier shifts), HR, FB-1, and the K1 core: rooms, inventory, pricing, OTA mapping, Aiosell, bookings, guests, payments, front desk, housekeeping, maintenance and staff lookups run on ids. The area routes live in `booking_system/routes/{rooms,bookings,frontdesk,operations}.py`, each with its `ACCESS` points; `access.check_routes` refuses to start the server when a route has no access point.
- **Fixtures.** `python -m fixtures.sandbox wipe | seed` builds **SANDBOX HOTEL** (values; users SANDBOX MANAGER / SANDBOX FRONTDESK, phones `+1 555 0100–0199`, emails `@example.com`, sign-in by phone, email or username) and **SANDBOX ANNEX** (isolation), bookings around today, then the HR seed, the Finance seed with an open business date, and the F&B outlet with sample checks. `python fixtures/test_hotel.py --stage core|hr|fnb|finance|all|verify [--reset]` builds **LEON TEST HOTEL** (50 rooms, 1000 bookings, 10 users, 10 staff with rosters, 2 outlets, 78 business days of postings). Recipes validate on SANDBOX HOTEL with SANDBOX ANNEX as the isolation hotel; general recipes are republished with `python -m booking_system.semantic.seed_general`.
- **Folio and postings contract.** Departments post through `finance.service.post_charge_in` / `post_payment_in` on the caller's cursor, so a check close and its postings commit together. Folio types: `guest` (ref = `bkg_` id), `outlet` (ref = `out_` id, `outlet_id` required), `account` (ref = AR account id). A tax posting carries `tax_code_id`, `tax_rule_id` and its parent posting; a cash posting needs the poster's open cashier shift; payments are negative, refunds positive. Postings are immutable (triggers); corrections are reversals. F&B lines carry Finance's `txc_` code (FNB_FOOD, FNB_BEVERAGE); `source_ref` is `<check_id>/<code>` or `<check_id>/pay/<n>`.
- **Staff and users.** `staff.user_id` links an HR staff record to a login (the general manager is also staff). Housekeeping and maintenance resolve a person through `staff` / `staff_identity` and ask HR whether they may take the task (`hr.service.check_assignable` with the HOUSEKEEPING or MAINTENANCE department and the hotel-local date); available and on-duty lists come from `available_staff` / `on_duty_staff`. `staff_duty_log` is retired.

**Wave 3 (remaining modules, 1 Nov): done.** K1 is complete for every non-semantic module.
- **Guest services on ids.** `loyalty_manager` (tiers, rewards, members, points ledger, occasions), `guest_engagement_manager` (preferences, requests, complaints, upgrade offers), `referral_agent` (partnerships between hotels by `hotel_id`, referrals), `mobile_checkin_manager` (`mobile_checkin` started → verified; `digital_key` per booking room, valid until the hotel's check-out time after the last night), `concierge_manager` (content collection `concierge`, fixed kinds and fields) and `ota_agent` (search on `daily_pricing` + `availability`, book through `booking_manager`). Tables live in `schema_guests`; routes in `booking_system/routes/guest_services.py`, and the guest thread / approvals / tasks / WhatsApp / messages / biography routes moved from `main.py` to `booking_system/routes/guests.py`. Guest needs are written by the `guest_needs` job (`jobs.enqueue`) keyed by `hotel_id`.
- **Desk actions and chat.** `desk_actions` takes `hotel_id`; the approval list is the `desk_action_approval` table and desk tasks are open `guest_request` rows. `leon_turn` keeps the hotel NAME in its signatures (the semantic workflow passes it) and resolves the id; pins, screen and seen documents are keyed by `hotel_id`. Kite pricing, `guest_summary`, the Aiosell buffer (`room_type_id`) and the channel manager lists (`?hotel_id=`) run on ids. Guest links carry `hotel_id` (older links are invalid).
- **Manager WhatsApp.** `leon_turn.manager_account(phone)` reads `user_identity` → active `user_account` → active `hotel_user` (last hotel, else the single hotel); the 24-hour window is `user_whatsapp_window` by `user_id`, and the chat is stored under the `usr_` id, so WhatsApp and the web desk share one history. The manager documents are no longer read.
- **Bridges.** All `bind_hotel_id` bridge triggers are gone; the remaining triggers are Finance guards and access-version bumps. Tables that still carry `hotel_name` are semantic-owned (catalogs side: `agent_usage`, `complaints`, `composition_plans`, `events`, `evidence`, `execution_runs`, `gap_candidates`, `investigations`, `investigation_traces`, `knowledge_tests`, `problem_specs`, `tasks`) or runtime state (`desk_dialog`, `desk_session`, `desk_topic`, `read_sql_cache`, keyed by hotel name + user id). The owner column of `catalogs`, `investigations` and `gap_candidates` is `user_id`; no startup DDL outside `semantic/` re-adds `manager_name`.
- **Keep-alive.** The 4xx-then-disconnect report did not reproduce with a real keep-alive client; `ActivityLog` is now a pure ASGI middleware and uvicorn's `timeout_keep_alive` is 75 s, and a 400 / 401 / 404 / 422 followed by further requests on the same connection passes.

**Wave 4 (last hotel-name areas, 1 Nov): done.** Nothing outside `semantic/` and the runtime-state tables is keyed by hotel name.
- **Documents.** `content_store.document_key` takes an explicit `_id` first, then the natural-key fields including `hotel_id` (a hotel_id-only body used to get an empty key, so hotels overwrote each other). Existing policy, profile and screen documents were re-keyed to `hotel_id`.
- **Policies, registration card, property details.** `policy_manager` is module functions on `hotel_id` (`read_policies`, `create_policy` one per type, `update_policy`, `delete_policy`, `read_registration_card`, `save_registration_card`; keys `hotel_id|type` and `hotel_id`; writers recorded as `user_id`). Routes live in `booking_system/routes/property.py`. `hotel_info_manager` keeps contact, address, description, facilities and amenities on the `hotel_profile` document (key `hotel_id`); the time zone is the hotel row's (a different value is refused) and check-in / check-out come from the booking policy.
- **Channel manager.** `ota_mapping` and the channel manager's `/mapping` and `/room-mapping` take `hotel_id`. The UI calls the session routes `GET|POST /api/ota/mapping` and `POST /api/ota/room-mapping` on main, which forward with the session's hotel. `workers/cm.js` is a pure proxy and is unchanged.
- **Onboarding.** `hotel_context_manager` and `manager_collection` are gone. `onboard_turn` creates and reads `user_account`, `user_identity`, `hotel`, `hotel_user` and the roles and modules through `user_manager`; definitions are saved on the `usr_` profile; the guide is `hotel_guide` keyed by `hotel_id`. Writers act only for the signed-in person (`/api/onboard/turn` passes the cookie's `user_id`); an existing phone without its password gets no token and no writes.
- **Guest links.** They carry `{hotel_id, guest_phone, exp}`, are HMAC-signed and expire after 7 days; expired or altered links are refused.
- **Verified.** A full chat onboarding of SANDBOX ONBOARD HOTEL (account, hotel, rooms, policies, definitions, booking) was run and the hotel deleted with `fixtures.sandbox.wipe([...])`. Policies, registration card, property templates and OTA mapping routes were run on SANDBOX HOTEL, with SANDBOX ANNEX documents and hotel row unchanged.
- **Channel manager credential.** Every channel manager endpoint except `/health` and the Aiosell webhook requires `X-Leon-Service`: `{hotel_id, exp}` signed with `CM_SERVICE_SECRET` (`booking_system/service_token.py`, 60 s, constant-time check, must name the hotel the call acts on). Unset secret refuses everything. The Aiosell webhook keeps Basic auth against `AIOSSELL_USERNAME` / `AIOSSELL_PASSWORD` (constant time; refused when unset). Both workers pass `CM_SERVICE_SECRET`; it must be set as a secret on both.
- **Onboarding replies.** The extraction step only fills fields; the reply is written after the writes from their outcomes ("Nothing was saved this turn" when none), and may claim a save only from a Saved / Open result.
- **Desk read presentation.** `leon_turn.present_read` reads the card title (cached 300 s per process) and the pins while the sentence is written, and sends the WhatsApp copy to open windows after the reply; `hotel_registry.read_hotel_by_id` uses the per-process hotel cache (cleared by every hotel write in `hotel_registry`). Its own cost fell from about 2.4 s to 0.25 s; a recipe-answered `/api/chat/start` turn on SANDBOX HOTEL took 18.5 s cold and 16.5 s warm.
- **What remains.** `semantic/` tables still carry hotel names and `semantic/workflow.py` upper-cases the owner for personal-recipe candidates and definition requests (semantic agent, in progress); runtime-state tables (`desk_*`, `read_sql_cache`); semantic cache invalidation on booking writes; P1–P4, FB-2, GEN, R1–R2.

**Strategy (overrides every stage below).** There is no data migration at all: no backfill, no dual-write, no diff verification window, no quarantine, no rollback plan for data. Each stage is:
1. new structure (DDL in the startup schema, idempotent, `add_column` / `lock_timeout` pattern);
2. code switch: readers and writers move in one step, superseded tables, columns and documents are dropped in the same change, and general recipes are republished with `python -m booking_system.semantic.seed_general`;
3. fixture update: `fixtures/sandbox.py` builds the new structure through the app writers, then `wipe` + `seed` + recipe check.

The "Backfill", "Dual-write", "Verify (3 days)", "Rollback" and live-count paragraphs in the stages below are void; their DDL and code lists still describe the target. Defaults D1–D9 and D14–D15 were about cleaning old data and no longer apply; D10–D13 and D16–D17 are fixture/seed values.

**Ids (overrides the "natural keys" rule everywhere below).** Every entity has a text primary id `<prefix>_<uuid4>`, `VARCHAR(48)`, with `CHECK (left(id, n) = '<prefix>_')`. The id is generated in app code (`hotel_registry.new_id(prefix)`, Python `uuid4`), never by a DB default, so the same code works on any Postgres or behind any pooler. A prefix is used because the id appears in logs, chat, URLs and LLM-written SQL; it tells which entity it belongs to, and an id of one entity can't be passed for another. Natural attributes (hotel_name, room_type name, room_number, guest phone/email, OTA booking reference, staff phone) become UNIQUE, indexed attributes scoped by `hotel_id`; people's phones and emails live in identity tables (`user_identity`, `guest_identity`) that point to one `user_id` / `guest_id`. Every FK references an id, never a name or phone. Search indexes stay on the natural attributes. Wherever the DDL below writes `hotel_name` as a key or FK column, read `hotel_id REFERENCES hotel (hotel_id)`; wherever it keys on room_type / room_number / guest_phone / staff_phone / outlet_name / department_name / account_name, read the entity id plus a UNIQUE `(hotel_id, name)`.

| Prefix | Entity | Prefix | Entity |
|---|---|---|---|
| `htl_` | hotel | `bkg_` | booking (desk ids done; OTA ids next) |
| `usr_` | user_account | `bkn_` | booking_room_night |
| `uid_` | user_identity | `pay_` | payment / folio transaction |
| `gst_` | guest | `fol_` | folio |
| `gid_` | guest_identity | `stf_` | staff |
| `rt_` | room_type | `dep_`, `out_`, `cc_` | department, outlet, cost centre |
| `rm_` | room | `acc_`, `txc_` | account, transaction code |

**Portability (Railway now, Cloudflare Postgres behind Hyperdrive later).** The connection comes only from `DATABASE_URL`. Only core Postgres features are used, plus the standard `pg_trgm` extension. Settings that a request needs are transaction-scoped: `content_store.with_settings` (`BEGIN` + `set_config(name, value, true)`) is used by `execute_reads` (TimeZone, statement_timeout), the SQL agent, the schema cache, and keyphrase search. There is no LISTEN/NOTIFY, no advisory lock and no temp table. Startup DDL runs on its own autocommit connection with a session `lock_timeout`, so it must run on a direct (non-pooled) connection at deploy time.

Goal: a multi-user PMS schema (each hotel has many users, each limited to certain modules) that covers standard front-office, housekeeping and revenue operations, plus a shared core (organisation, access, money, business date) that Finance, HR, F&B, Spa, Events, Laundry and Reporting plug into without schema rework.

---

## Part 0: Rules, delivery pattern, and the defaults that replace manager decisions

### Rules (every stage)
- **Storage.** Computable data goes in tables: numbers, dates, statuses, keys, prices, rates, quantities, money. Content and setup prose goes in `content_document`: descriptions, photos, allergens, contact details, policy wording, credentials.
- **Keys.** `<prefix>_<uuid4>` ids (see Ids above); `hotel_id` is on every hotel-owned table and in its unique keys. Natural attributes are UNIQUE within the hotel and indexed for discovery.
  - Ordinals inside a parent are still allowed because staff print or say them: `room_seq`, folio `folio_window`, `txn_seq`, `line_seq`, `check_number`.
- **Case.** CHECKs enforce uppercase canonical names; phones are E.164 (without regex: `left(p,1)='+' AND translate(substr(p,2),'0123456789','')='' AND char_length(p) BETWEEN 8 AND 16`).
- **No fallback logic.**
  - A missing value stays NULL and appears in an explicit exception list.
  - Seeded configuration values (for example the default check-out time) are written once as explicit rows the manager can edit. Code never substitutes them at runtime.
  - This replaces the current `booking_manager.create_booking` behaviour (lines 510–526 and 1475–1478), where a missing tax policy silently becomes 0 % tax.
- **Identity and access (from A1b).** The acting person is always `(hotel_name, user_phone)` from `hotel_user`. Every actor column is `*_by_user_phone` with an FK to `hotel_user`. `staff` (A3) is the HR/employment record keyed on the same phone; a person who logs in and is employed has both rows. Every API route, desk action, desk read, orchestrator tool and readable table is listed in `access_point` with the module and level it needs. A point that isn't listed is refused, and startup fails.

### Delivery pattern (every stage)
1. **Structure.** DDL in startup schema code.
2. **Code switch.** Readers and writers move in one change, superseded structures are dropped, and general recipes are republished. User recipes are wiped with the data.
3. **Fixture.** Update `fixtures/sandbox.py`, run `wipe` then `seed`, run the general recipes on JACKHOTEL (values) and LITTLE COVE (isolation), and do one `/api/chat` turn.

### Defaults (JACKHOTEL and the other hotels are test data, so nothing blocks)

| # | Issue | Default applied |
|---|---|---|
| D1 | BDC4082659: header 25→26 Oct vs nightly 30–31 Oct (room 103, checked in) | **Nightly rows are the truth.** The header is derived from nights: 30 Oct, 2 nights. `channel_reservations` keeps the channel's claim, and a `channel_date_mismatch` exception lists it. |
| D2 | 3 JACKHOTEL bookings with `guest_phone=''` | **Quarantine.** Copy the booking, its nights, payment and documents to `mig_backup.quarantine_*`, then delete them from live. They are logged in `audit_log` as system actor `migration`. |
| D3 | DJ HOTEL room type `STANDARD ROOM, TRIPLE ROOM` | **Keep as-is.** It is a valid key with an odd spelling; the manager can rename it later (FKs cascade). |
| D4 | 3 identical maintenance schedules for room 101 | **Dedupe.** Keep the one with the earliest `start_date` (ties broken by `next_due_date`). Relink the 4 requests to it; any request colliding on the new key goes to quarantine. |
| D5 | 84 booked nights (23 Sep–22 Oct) with no inventory row | **Create history inventory** from room counts, `created_by='migration_history'`. No pricing rows are created; those nights have no rate. |
| D6 | Currency unknown for 90 channel bookings | **NULL currency means "currency unknown".** Amounts are kept but excluded from every revenue, ADR and RevPAR figure; recipes output an `excluded_unknown_currency` count; night audit posts no charge and lists them as exceptions. Folios are always in hotel currency; a foreign-currency rate (the 3 INR bookings) posts only with an explicit `currency_rate` row, otherwise it becomes an exception. |
| D7 | Zone of Aiosell `bookedOn` (no offset) | **Hotel local time** (`hotel.timezone`). |
| D8 | Managers as staff | **Yes.** Each manager with a hotel becomes staff: department `MANAGEMENT`, position equal to their role. |
| D9 | Currency of the 2 desk ledger payments | **Hotel currency** (MYR). Desk payments are recorded at the hotel's front desk. |
| D10 | Standard check-in/out times | **Seed** from `hotel_guide.fields.checkin_time` / `checkout_time` where present (5 hotels); otherwise seed explicit rows 14:00 / 12:00. Editable. |
| D11 | Night audit time | **Seed 03:00 hotel local** per hotel. Editable. |
| D12 | No tax policy exists for any hotel | **Seed transaction codes as non-taxable.** Tax lines post only after a `tax_rule` exists. The desk shows "no tax configured" instead of computing 0 %. |
| D13 | Rate plans (0 documents; `rate_plan` NULL on 93 of 93 bookings) | **Seed** one plan per distinct OTA `rateplanCode` (uppercased) plus `BAR` per hotel for desk sales. |
| D14 | Existing manager role (6 "Hotel Manager", JACKHOTEL "Assistant Manager") | **All 7 become `GENERAL_MANAGER`** of their hotel; each is its hotel's only user. The old title is kept as `hotel_user.job_title`. |
| D15 | 1 manager document with no hotel | **`user_account` only, no `hotel_user`.** That person can finish onboarding (creating a hotel makes them its GENERAL_MANAGER) or be invited. |
| D16 | Role matrix and module list | **Seed the matrix below (A1b)** for every hotel; the GENERAL_MANAGER edits it. Modules are a fixed system list. |
| D17 | A phone that belongs to several hotels signs in or messages on WhatsApp | **Clarify.** Sign-in lists that person's active hotels and asks which one; WhatsApp uses the hotel last chosen in this channel (`user_account.last_hotel_name`); if none is chosen, Leon asks. |

---

## Part 1: PMS operations coverage matrix

| Operation | Today | Evidence | Delivered by |
|---|---|---|---|
| Reservations | **Partial** | `room_bookings` / `booking_stays` / `room_stay` plus documents. Header and nightly copies disagree; `total_amount` is NULL on 93 of 93. | A4, A6 |
| Groups, room blocks, allotments | **Partial** | `room_bookings.group_name`, `booking_manager.create_group_booking`, `room_allotments` (0 rows) and `inventory_manager.save_allotment`. No block, pickup or cutoff. | P2 |
| Rate plans and packages | **Missing** | `rate_plans` documents (0) via `main.save_rate_plan`; only a base `daily_pricing` per room type; no package components. | P1 |
| Check-in | **Exists** | `front_desk_manager.update_guest_check_in_status_internal`, `room_stay.checked_in_at`. Mobile check-in has no data. | A4, A5 (cleanup), C3 (deposit / pre-auth) |
| Room moves | **Exists** | `relocate_guest_internal`, `reassign_room_for_date_internal`. No move history. | A5, C2 (audit trail) |
| Early / late checkout | **Partial** | Early checkout releases nights (`_release_nights_after_checkout`); there is no late checkout, departure time or fee. | P3, P1 (fees) |
| Check-out | **Partial** | Status only; no folio settlement check. | C3 (settlement rule) |
| Night audit and business date | **Missing** | Calendar dates only; `bulk_checkout_past_internal` is the only end-of-day action. | C4 |
| Room status: housekeeping, OOO / OOS | **Partial** | `room_readiness_status` (6 JACKHOTEL rooms, all dirty); OOO is stored as `room_number_assignments` rows; no OOS distinction or history. | A5 (blocks), A7 (history), HR (assignment) |
| Guest folios: charges, posting, routing, splits, settlement | **Missing** | `payment_tracking` holds one total per booking; `payment_ledger` has 2 rows; no charge postings. | C3 |
| City ledger / AR | **Missing** | | C3 (accounts), P4, FIN |
| Deposits; cancellation and no-show policies | **Partial** | `booking_policies` documents (booking / payment) for 6 hotels and none for JACKHOTEL; `room_bookings.no_show_at`; no fees. | P1, C4 |
| Channel manager | **Partial (strong)** | Aiosell inbound bookings, room-code mapping, inventory and rate push, `channel_reservations`. No commission or rate-plan mapping. | A6, P1, P2, C3 (channel accounts) |
| Guest profile and loyalty | **Partial** | `guest_profile` (98), `guests` / `loyalty_program` documents duplicated; all points 0. | A8 |
| Lost & found; guest requests | **Missing / Partial** | `guest_request` / `guest_complaint` tables (0 rows, no status or time); no lost & found. | P3 |
| Departments: Finance, HR, F&B, Spa, Events, Laundry | **Missing** (HR partial) | No `staff_profiles` documents; `staff_duty_log` is empty. | FIN, HR, FB, GEN |
| Reporting snapshots | **Missing** | Recipes compute live from operational tables. | R1, R2 |
| Users, roles, module access | **Missing** | One manager document per hotel: `set_manager_hotel` rejects a second manager for a hotel and a second hotel for a phone. The JWT is the whole profile with no expiry and no modules. 36 of 151 routes don't read the JWT (about 25 of them read or write hotel data, for example `/api/hotels/{hotel_id}/inventory/update`, `/pricing/update`, `/api/payments/ledger`, `/api/guest/biography`). Recipes and desk actions have no module. | A1b, C2 |

---

## Part 2: Stage list and dependencies

Effort is recomputed for "structure + code switch + fixture" (no backfill, dual-write or verification window). The id re-key adds work: the old code holds about 1,800 natural-key references (front_desk_manager about 670, booking_manager about 190, main.py about 150). That work is counted in the stage that introduces each entity, plus K1 for the remaining modules.

| Phase | Stage | Effort (was) | Depends on |
|---|---|---|---|
| **A. Foundation** | A0 Backups and defaults | **done** (0.5) | — |
| | A1 `hotel` registry (`htl_` id, bridge trigger, recipes and agents on `hotel_id`) | **done** (1–1.5) | A0 |
| | **A1b Users and module access**: `user_account` (`usr_`), `user_identity` (phone / email to one user_id), `hotel_user`, roles, module levels, `access_point` guard, semantic-layer filtering, actor columns as `user_id` | **6–7 d** (8–10) | A1 |
| | A2 `room_type` (`rt_`) / `room` (`rm_`) masters; inventory, pricing, assignments, mapping by id | 2–2.5 d (2) | A1 |
| | A3 `staff` (`stf_`) and ids for operations tables | 1.5–2 d (2–3) | A2, A1b |
| | A4 `booking_room_night` (`bkn_`); `bkg_` ids for OTA bookings; booking → guest_id | 4–5 d (5–7) | A2, A8 guest part |
| | A5 Occupancy-only room assignments (OOO / OOS blocks) | 2–2.5 d (3–4) | A4 |
| | A6 Booking value and channel commission | 1–1.5 d (1.5–2) | A4 |
| | A7 timestamptz, `booked_at`, room status history | 1.5–2 d (2.5–3) | A1, A3 |
| | A8 Documents: indexes, computable fields out; `guest` (`gst_`) + `guest_identity` | 2.5–3 d (3–4) | A1 |
| | **K1 Re-key the remaining modules** (front desk, housekeeping, maintenance, loyalty, concierge, engagement, referral, Kite pricing, main.py routes, desk.js / UI payloads), then drop the `bind_hotel_id` bridge triggers and every `hotel_name` column on child tables | **6–8 d** (new) | A2–A8 |
| **B. Shared core** | C1 Department, cost centre, outlet | 1 d (1–1.5) | A1, A3 |
| | C2 Audit trail (roles moved to A1b) | 1.5 d (1.5–2) | A1b, A3 |
| | C3 Financial core (transaction codes, tax, accounts, channels, folios, transactions) | 4.5–6 d (5–7) | A4, A6, C1, C2 |
| | C4 Business date and night audit | 3.5–4.5 d (4–5) | C3, A5, A7 |
| **C. PMS modules** | P1 Rate plans, packages, cancellation / no-show / deposit policies | 3 d (3–4) | C3 |
| | P2 Groups, blocks, allotments | 2.5 d (3) | A4, P1 |
| | P3 Late checkout, service requests, lost & found | 2 d (2–3) | C1, C3 |
| | P4 City ledger operations and deposits | 2 d (2–3) | C3, P1 |
| **D. Departments** | FIN Finance | 4.5–6 d (5–7) | C4 |
| | HR Human resources | 4.5–6 d (5–7) | C1, C2, A1b |
| | FB-1 F&B outlets, menu, checks, posting | 3.5–4.5 d (4–5) | C4 |
| | FB-2 F&B stock and recipe cost | 3–3.5 d (3–4) | FB-1, FIN (vendors) |
| | GEN Spa / Events / Laundry (each) | 3–4.5 d each (3–5) | C4, HR |
| **E. Reporting** | R1 Night-audit daily snapshots | 2.5–3.5 d (3–4) | C4 |
| | R2 Standard reports as general recipes | 3.5–4.5 d (4–5), +1 d per department report | R1, plus FIN / HR / FB for their reports |

**Totals (remaining work)**
- PMS path (A, B, C, E): **about 53–64 developer days** (was 57–76). Dropping data migration saves about 13–17 days; the id re-key (K1 plus the larger A2 / A4 / A8) costs back about 8–10.
- Adding FIN, HR and F&B: **about 68–84 days** (was 74–99).
- Each extra department module (Spa, Events, Laundry) adds 3–4.5 days, plus 0.5 day to register its module, role rows and access points.

**What can run in parallel**
- After A1: A1b and A2. A1b's semantic-layer part (module tags, filtering) and its API-guard part can ship separately.
- After A4: A5, A6 and A8.
- After C4: P1–P4, FIN, HR, FB-1 and R1.

---

## Part 3: Foundation stages (A)

### A0: Backups and defaults (done)

Only the general recipes are kept: `python -m migrations.general_recipes export` wrote `_backups/general_recipes_20261031_214842.json` (catalogs 16, catalog_versions 36, catalog_active_versions 15, catalog_keyphrase 91, knowledge_index 15, knowledge_publications 36). `python -m fixtures.sandbox wipe` truncates every other public table in one statement (no CASCADE, so a kept table is never touched); `seed` rebuilds the SANDBOX fixture through the app writers.

SANDBOX fixture (`fixtures/sandbox.py`, test password printed only as "set"):
- **JACKHOTEL** (MYR, Asia/Kuala_Lumpur, channel code `sandbox-pms`), manager MR. JACK +6033445566. DELUXE rooms 101–103 at 250, FAMILY rooms 201–202 at 380. Inventory and pricing from today−14 to today+30, with booking and payment policies.
- **LITTLE COVE** (MYR), manager LINA TAN +60175550007, STANDARD room 1 at 180. It exists only for the recipe isolation check.
- **6 guests and 7 bookings** around today: 1 checked out last week, 1 in-house departing today, 1 in-house staying, 2 arrivals today, 1 future booking and 1 cancelled. 2 payments.

### A1: `hotel` registry (done)

**Built.** `booking_system/hotel_registry.py`, run at startup (`main.py`) and by the fixture.

```sql
CREATE TABLE hotel (
  hotel_id               VARCHAR(48)  PRIMARY KEY CHECK (left(hotel_id, 4) = 'htl_'),
  hotel_name             VARCHAR(255) NOT NULL UNIQUE CHECK (hotel_name = UPPER(btrim(hotel_name)) AND hotel_name <> ''),
  currency               CHAR(3)      NOT NULL CHECK (currency = UPPER(currency)),
  timezone               VARCHAR(64)  NOT NULL,
  channel_hotel_code     VARCHAR(64)  UNIQUE,
  standard_checkin_time  TIME         NOT NULL,   -- seeded 14:00 (D10), editable
  standard_checkout_time TIME         NOT NULL,   -- seeded 12:00
  night_audit_time       TIME         NOT NULL);  -- seeded 03:00 (D11)
-- bridge until K1, per child table T:
--   ADD COLUMN hotel_id VARCHAR(48); BEFORE INSERT OR UPDATE OF hotel_name trigger bind_hotel_id() sets hotel_id
--   from hotel_name and raises when the hotel is not registered;
--   T_hotel_fk FOREIGN KEY (hotel_id) REFERENCES hotel (hotel_id) (validated); index T_hotel_id_idx.
```

Child tables: `room_inventory_nightly`, `daily_pricing`, `room_bookings`, `booking_stays`, `room_stay`, `room_number_assignments`, `room_readiness_status`, `payment_ledger`, `channel_reservations`, `channel_reservation_sync`, `hotel_room_code_mapping`, `booking_guest_counts`, `guest_profile`, `guest_message`, `guest_preference`, `guest_request`, `guest_complaint`, `maintenance_requests`, `maintenance_schedules`, `cleaning_status`, `staff_duty_log`, `room_allotments`, `kite_hotel_setting`, `kite_push_day`, `kite_price_anchor`, `booking_modification_log`, `aiosell_buffer_room_nights`.

**Code switched (no dual-write).**
- Writers: `signup_manager` and `set_manager_hotel` write the hotel row, the manager document and the `hotel_profile` document (city_name) in one transaction. `onboard_turn` asks for the city. `ota_mapping.save_mapping` writes `hotel.channel_hotel_code`, and the `ota_link` collection is gone. Manager documents no longer hold currency or time zone.
- Readers: `timezone_utils`, `currency_utils`, `guest_manager.hotel_currency`, `onboard_turn`, `leon_meta_orchestrator.read_hotel_local_time_tool`, `aiosell_reservation._hotel_name`, `channel_reservations`, `aiosell_channel_manager.hotel_code_for`, the hotel checks in `policy_manager`, and the JWT `hotel_timezone` / `hotel_currency` claims (filled from the row at sign-in).
- Desk booking ids are `bkg_<uuid>` (the regex counter is gone); `create_booking` now writes `check_in_date` and `currency`.

**Recipes and agents.** `execute_reads` resolves the session hotel to its row and binds `%(hotel_id)s`, which every statement must use. All 15 general recipes filter `alias.hotel_id = %(hotel_id)s`, and `_HOTEL_CURRENCY` reads `hotel.currency` by id. `sql_agent`, `catalog_agent` (and its prompt), `review_flow` and `library` require and teach `hotel_id = %(hotel_id)s` and joins on id columns.

### A1b: Users and module access (8–10 days)

**Goal.** Each hotel has many users, and each user reaches only the modules their role (plus per-user overrides) allows. One person (phone) can work at several hotels with a different role at each. This applies to every API route, desk action, desk read, orchestrator tool, readable table and recipe, and to what Leon learns.

The stage replaces `manager_collection` as the identity: after cut-over that document becomes the user's setup profile. It also replaces C2's earlier `permission` / `role` / `staff_role` design. C2 now keeps only the audit trail.

**Live state (31 Oct)**
- **Manager documents:** 8, with 8 distinct phones, all E.164 and all bcrypt-hashed. 7 hotels have exactly one manager each: 6 "Hotel Manager", plus JACKHOTEL's "Assistant Manager". 1 document has no hotel. All are `active`.
- **What sign-in blocks today:** `hotel_context_manager.set_manager_hotel` rejects a second manager for a hotel and a second hotel for a phone. `signin_manager_by_hotel` assumes one manager per hotel. `_process_signin` encodes the whole profile as the JWT, with no `exp` and no modules.
- **Data keyed by `manager_name`:**
  - `investigations` 34 (1 manager)
  - `desk_dialog` 6 (4 hotels), `desk_session` 2, `desk_topic` 2
  - `gap_candidates` 0
  - `catalogs` user scope: 0 now (the 4 earlier user recipes and the manager document's definitions and personal reports are gone)
  - `activity_logs` 2 documents
- **Per-hotel chat state:** `hotel_screen` 1, unique per hotel; `hotel_pins` 1; `hotel_card_seen` 4.
- **Routes:** 151 in `main.py`; 36 don't read `leon_jwt`.

```sql
CREATE TABLE user_account (                       -- the person, across hotels
  user_phone      VARCHAR(16)  PRIMARY KEY CHECK (left(user_phone,1)='+' AND translate(substr(user_phone,2),'0123456789','')='' AND char_length(user_phone) BETWEEN 8 AND 16),
  user_name       VARCHAR(255) NOT NULL CHECK (user_name = UPPER(btrim(user_name)) AND user_name <> ''),
  status          VARCHAR(16)  NOT NULL CHECK (status IN ('active','locked')),
  last_hotel_name VARCHAR(255) REFERENCES hotel ON UPDATE CASCADE,      -- D17, WhatsApp hotel choice
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now());
-- password hash, primary_language, nationality, cultural_context: content document collection user_account, key user_phone

CREATE TABLE module (module_name VARCHAR(32) PRIMARY KEY CHECK (module_name = lower(module_name) AND module_name <> ''));  -- fixed system list
CREATE TABLE access_level (access_level VARCHAR(8) PRIMARY KEY, level_rank SMALLINT NOT NULL UNIQUE);                     -- none 0, view 1, operate 2, manage 3

CREATE TABLE access_role (
  hotel_name                 VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  role_name                  VARCHAR(64)  NOT NULL CHECK (role_name = UPPER(btrim(role_name)) AND role_name <> ''),
  daily_investigation_cap    SMALLINT     NOT NULL CHECK (daily_investigation_cap >= 0),   -- gatekeeper, per user per local day
  PRIMARY KEY (hotel_name, role_name));
CREATE TABLE role_module (
  hotel_name   VARCHAR(255) NOT NULL,
  role_name    VARCHAR(64)  NOT NULL,
  module_name  VARCHAR(32)  NOT NULL REFERENCES module ON UPDATE CASCADE,
  access_level VARCHAR(8)   NOT NULL REFERENCES access_level,
  PRIMARY KEY (hotel_name, role_name, module_name),
  FOREIGN KEY (hotel_name, role_name) REFERENCES access_role ON UPDATE CASCADE ON DELETE CASCADE);
-- every (role, module) pair has a row, 'none' included; a missing pair is a defect (Verify), never read as 'none'

CREATE TABLE hotel_user (
  hotel_name                     VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  user_phone                     VARCHAR(16)  NOT NULL REFERENCES user_account ON UPDATE CASCADE,
  role_name                      VARCHAR(64)  NOT NULL,
  job_title                      VARCHAR(64),
  status                         VARCHAR(16)  NOT NULL CHECK (status IN ('invited','active','suspended','removed')),
  access_version                 INTEGER      NOT NULL DEFAULT 1,     -- bumped on role, override or status change
  daily_investigation_cap_override SMALLINT   CHECK (daily_investigation_cap_override >= 0),
  invited_by_user_phone          VARCHAR(16),
  invited_at                     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  activated_at                   TIMESTAMPTZ,
  PRIMARY KEY (hotel_name, user_phone),
  FOREIGN KEY (hotel_name, role_name) REFERENCES access_role ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, invited_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  CHECK (status <> 'active' OR activated_at IS NOT NULL));
CREATE TABLE hotel_user_module (                  -- per-user override: replaces the role's level for this module
  hotel_name            VARCHAR(255) NOT NULL,
  user_phone            VARCHAR(16)  NOT NULL,
  module_name           VARCHAR(32)  NOT NULL REFERENCES module ON UPDATE CASCADE,
  access_level          VARCHAR(8)   NOT NULL REFERENCES access_level,
  granted_by_user_phone VARCHAR(16)  NOT NULL,
  granted_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (hotel_name, user_phone, module_name),
  FOREIGN KEY (hotel_name, user_phone) REFERENCES hotel_user ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, granted_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE);

CREATE VIEW user_module_access AS                 -- active users only; one row per module
SELECT x.hotel_name, x.user_phone, x.module_name, x.access_level, l.level_rank
FROM (SELECT u.hotel_name, u.user_phone, rm.module_name,
             CASE WHEN o.access_level IS NULL THEN rm.access_level ELSE o.access_level END AS access_level
      FROM hotel_user u
      JOIN role_module rm ON rm.hotel_name = u.hotel_name AND rm.role_name = u.role_name
      LEFT JOIN hotel_user_module o ON o.hotel_name = u.hotel_name AND o.user_phone = u.user_phone AND o.module_name = rm.module_name
      WHERE u.status = 'active') x
JOIN access_level l USING (access_level);

CREATE TABLE access_point (                       -- system catalogue: what each entry point needs
  access_kind       VARCHAR(16)  NOT NULL CHECK (access_kind IN ('api','desk_action','desk_read','tool','table')),
  access_point_name VARCHAR(255) NOT NULL,        -- 'POST /api/bookings/create', 'cancel', '_rows_payments', 'read_activity_logs_tool', 'room_bookings'
  module_name       VARCHAR(32)  NOT NULL REFERENCES module ON UPDATE CASCADE,
  access_level      VARCHAR(8)   NOT NULL REFERENCES access_level CHECK (access_level <> 'none'),
  PRIMARY KEY (access_kind, access_point_name, module_name));   -- several rows = any one satisfies (e.g. folio tables: front_desk view OR finance view)
CREATE TABLE public_route (route_name VARCHAR(255) PRIMARY KEY);   -- the only routes without a hotel token

ALTER TABLE catalogs ADD COLUMN module_name VARCHAR(32) REFERENCES module ON UPDATE CASCADE,
  ADD COLUMN owner_hotel_name VARCHAR(255), ADD COLUMN owner_user_phone VARCHAR(16),
  ADD FOREIGN KEY (owner_hotel_name, owner_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  ADD CHECK ((scope = 'user') = (owner_user_phone IS NOT NULL AND owner_hotel_name IS NOT NULL));
-- after backfill: ALTER COLUMN module_name SET NOT NULL; later DROP COLUMN manager_name
-- gap_candidates, investigations, desk_dialog, desk_session, desk_topic: ADD user_phone VARCHAR(16), FK (hotel_name, user_phone) REFERENCES hotel_user,
-- swap manager_name for user_phone in each primary / unique key, then DROP COLUMN manager_name
```

**Triggers (no fallback; these raise)**
- **`keep_general_manager`** is a deferred constraint trigger on `hotel_user`, `role_module` and `hotel_user_module`. Each hotel must keep at least one `active` GENERAL_MANAGER. GENERAL_MANAGER rows in `role_module` stay `manage`, and a GENERAL_MANAGER cannot take an override.
- **`bump_access_version`** fires on `hotel_user`, `hotel_user_module` and `role_module` changes and increments `access_version` for the affected users.

**Seed (D16)**
- **`module`, 12 rows:** front_desk, reservations, housekeeping, maintenance, fnb, finance, hr, reporting, setup, spa, events, laundry.
- **What each level means:**
  - `view`: read screens and run reports.
  - `operate`: day-to-day writes, for example bookings, check-in, posting a payment, cleaning a room.
  - `manage`: configuration, approvals, refunds, closing the day and, in setup, managing users.
- **`access_level`:** 4 rows.
- **`access_role`:** 8 roles × 7 hotels = **56**. `daily_investigation_cap` is 5 for GENERAL_MANAGER (today's `LEON_CATALOG_MANAGER_DAILY`) and 2 for every other role. The hotel cap (15) and the token budget (300k) stay in `gate._limits()`.
- **`role_module`:** 8 × 12 × 7 = **672** rows from this matrix:

| module | GENERAL_MANAGER | FRONT_DESK | NIGHT_AUDITOR | HOUSEKEEPING | MAINTENANCE | FNB_CASHIER | ACCOUNTANT | HR |
|---|---|---|---|---|---|---|---|---|
| front_desk | manage | operate | manage | view | view | none | view | none |
| reservations | manage | operate | view | none | none | none | view | none |
| housekeeping | manage | view | view | operate | view | none | none | none |
| maintenance | manage | operate | view | operate | manage | none | none | none |
| fnb | manage | view | view | none | none | operate | view | none |
| finance | manage | none | view | none | none | none | manage | none |
| hr | manage | none | none | none | none | none | none | manage |
| reporting | manage | view | view | none | none | none | operate | view |
| setup | manage | none | none | none | none | none | none | none |
| spa / events / laundry | manage | none | none | none | none | none | none | none |

- **`hotel_user`:** **7** rows: the 7 manager documents with a hotel, all GENERAL_MANAGER (D14), `status='active'`, `activated_at = created_at`.
- **`user_account`:** **8** rows (D15).
- **`hotel_user_module`:** 0.
- **`public_route`, 12 rows:**
  - `GET /`, `GET /health`, `GET /api/status`
  - `POST /api/auth/signup`, `/signin`, `/logout`
  - `GET /api/meta/country_codes`, `/languages`
  - `GET` and `POST /webhook/whatsapp` (Meta signature)
  - `POST /update_reservation` (Aiosell secret)
  - `POST /api/onboard/turn` (needs a `user_account` token, not a hotel token)
- **`access_point`:** one or more rows for every one of the other **139 routes**, every `desk_actions.action_names()` / `information_names()` entry, every orchestrator tool and every table in `semantic/catalog.py`'s schema. A seed script lists all four registries and fails on any point without a row. Main assignments:

| Points | Module, level |
|---|---|
| `/api/bookings/*` create / group / modify / cancel; desk actions create, modify, cancel; `/api/assignments/cancel` | reservations, operate |
| `/api/availability`, `/api/inventory`, `/api/bookings-calendar`, `/api/ota/*`; desk reads availability, bookings, types | reservations, view |
| `/api/hotels/{hotel_id}/inventory/*`, `/pricing/*`, `/api/pricing/override`, `/api/dynamic_pricing`, `/api/kite*`, `/api/rate_plans` writes | reservations, manage |
| `/api/assignments/*` check_in, check_out, undo_check_in, reset_stay, reassign, bulk_checkout_*, update; desk actions check_in, check_out, reassign, payment, profile; `/api/payments/update`; `/api/guest_profile/*`, `/api/guest/biography`, `/api/guests/*` (WhatsApp, approvals, tasks) | front_desk, operate |
| `/api/assignments/calendar*`, `detail`, `history`, `past_stays`, `/api/guests` search / record / thread; desk reads arrivals, departures, rooms, payments, profile | front_desk, view |
| `/api/assignments/mark_out_of_order`, `mark_available`, `/api/rooms/readiness_status/update`, `/api/hotels/{hotel_id}/update-room-status`, `/api/housekeeping/*` writes | housekeeping, operate |
| `/api/housekeeping/*` reads, `/api/rooms/readiness_status*` | housekeeping, view |
| `/api/maintenance/create`, `update`, `pm_run_now`; `schedules/setup`, `schedules/update` | maintenance, operate (create); manage (schedules) |
| `/api/payments/refund`; desk action refund; desk read refunds; `/api/payments/ledger` | finance, operate (refund) / view |
| `/api/rooms/*` add / remove / rename / labels / types, `/api/room_types/*`, `/api/property/*` writes, `/api/hotels` setup reads, desk `save_approval` / `drop_approval`, user and role management (new) | setup, manage |
| `/api/analytics`, `/api/hotels/{hotel_id}/analytics`, `/api/activity/logs/by_hotel`, `/api/personal_recipes` (GET view, POST operate), `/api/definitions` | reporting, view / operate |
| `/api/chat/*`, `/api/desk/queue`, `/api/guide`, `/api/navigation/simple`, `/api/reset` | front_desk, view (the turn is then checked per action and per recipe) |
| `/api/staff/available` | housekeeping, view (HR later adds hr, view) |
| Tables: `room_bookings`, `booking_room_night`, `channel_reservations` → reservations view and front_desk view; `room_number_assignments`, `room_stay` → front_desk view; `room_readiness_status`, `cleaning_status` → housekeeping view; `maintenance_*` → maintenance view; `payment_*`, `folio*` → finance view and front_desk view; `staff*`, `pay*` → hr view; `daily_*` snapshots → reporting view | as listed |

**Auth and JWT**
- `signin` checks the password from the `user_account` document, then reads the person's active `hotel_user` rows.
  - No active rows: "not a user of any hotel".
  - Several rows: list them and clarify (D17).
- `_process_signin(hotel_name, user_phone)` issues claims `{hotel_name, user_phone, user_name, role_name, modules: {module: level}, access_version, hotel_timezone, hotel_currency, primary_language, exp}`, with `exp` set to 12 h. It no longer encodes the whole profile.
- New `POST /api/auth/switch_hotel` re-issues the token for another active hotel of the same phone.
- **Every request is checked in `main.py` middleware `_module_guard`, after `_bearer_session`:**
  - Resolve the route template, then require a `public_route` row or matching `access_point` rows.
  - Decode the JWT; the `(hotel_name, user_phone)` row must be `active`, and its `access_version` must equal the claim. That is one indexed read per request, so a revoked user is refused immediately.
  - The claim's level must meet the point's level.
  - The `{hotel_id}` path, `hotel_name` query and body parameters must equal the JWT hotel, or the request is refused.
  - On refusal: `403 {"error":"outside your access","module":...,"needed":...}`.
- Startup check `_access_points_complete()` compares `app.routes` with `public_route` + `access_point` and refuses to start if any route is missing.

**Desk, tools, Leon**
- `access.py` (new):
  - `levels(jwt)`
  - `allowed(hotel_name, user_phone, kind, name)`: reads `access_point` joined to `user_module_access`.
  - `outside_access(kind, name)`: returns the fixed reply.
  - `set_actor(cur, hotel_name, user_phone=None, system=None)`: used by C2.
- `desk_actions.open_action`, `settle`, `read_information`, `save_approval` and `mark_task_done` call `allowed` before READ / validate. A refusal returns "That is outside your access (module: finance)." and stops: no alternate action, no SQL fallback, no gap noted.
- `leon_turn.desk_turn` and `desk_action_turn` pass `(hotel_name, user_phone)` from the JWT. `apply_desk` and `card_click` check the target action. `menu_screen`, `function_screen` and `catalog_text` list only allowed actions.
- `leon_meta_orchestrator.process_request` / `process_desk` filter the tool list to allowed tools before the model sees them, and check again at dispatch. `get_manager_name_from_jwt` becomes `get_user_from_jwt`.
- WhatsApp: `leon_turn.manager_hotel(phone)` becomes `user_hotels(phone)`; `receive_manager_whatsapp` and `note_manager_inbound` apply D17. A guest phone that is also a user is treated as a user only when it has an active `hotel_user` row at that hotel.

**Semantic layer**
- **Module tag.** `catalogs.module_name` is NOT NULL. `seed_general._recipe(catalog_id, ..., module_name)` and `_publish` write it.
  - reporting: occupancy_rates, room_nights_sold, room_revenue, adr, revpar, channel_mix, average_length_of_stay.
  - reservations: bookings_made, cancellation_counts, no_show_counts, booking_lead_time.
  - front_desk: arrival_counts, departure_counts, in_house_counts.
  - finance: outstanding_balance.
  - (All of the above carry the `daily_` prefix.) Every later general recipe (R2, department reports) declares its module.
- **`context_engine.recipe_candidates(question, hotel_name, user_phone, limit)`** keeps a page when `visible_to(scope, hotel_name, user_phone)` holds and the user's level on `module_name` is at least `view`. It also returns `withheld`, the matched pages dropped for module only.
  - `workflow.handle_user_message` / `_query_turn`: when nothing usable remains and `withheld > 0`, reply "outside your access" with the module and stop. No SQL agent, no catalog queue, no `gate.admit`.
- **`sql_agent` / `query.accepted_reads`** also reject a statement that reads a table without a `table` access point the user meets. This closes the raw-SQL route around recipes.
- **`registry.visible_to` / `catalog_scopes` / `promote_proposal(owner=...)`** key ownership on `(owner_hotel_name, owner_user_phone)` instead of `manager_name`.
- **`catalog_agent.improve_from_trace`:**
  - The trace carries the asker's hotel, phone and modules.
  - `_tools` / `_schema_problem` expose only tables the asker can read.
  - A proposal must declare a `module_name` the asker holds at `view` or above.
- **`reviewer_agent.review_once` / `review_flow.consider_proposal`** decline a proposal whose module or tables are outside the asker's access (`_decline` reason `outside_access`).
  - User-recipe publication needs reporting at `operate` or above.
  - General recipes are unchanged (system authored).
- **`gate.admit(hotel_name, user_phone, ...)`:**
  - The per-user cap comes from `daily_investigation_cap_override`, or from the role's cap when the override is NULL.
  - `_note`, `_mark` and `_spent` key on `user_phone`.
  - The investigation fingerprint suffix becomes `|hotel_name|user_phone`.
- **`definitions.py` / `personal.py`:**
  - `_profile` / `_write` read and write the `hotel_user_profile` document, keyed `hotel_name|user_phone`, which is the old manager document re-keyed (no behaviour change).
  - `map_questions` / `request_definitions` / `open_definitions` ask only for keys whose recipe module the user holds.
  - `personal_recipes`, `save_personal`, `learn`, `run_personal`: a personal report can include only items in the user's modules; at run time each item is rechecked, and one outside access is reported, not skipped silently.
- **Re-keyed runtime state** (`runtime_state.read_turns`, `leon_turn._desk_dialog`, `store_screen` / `read_screen`, pins, `_card_seen` / `_mark_seen`, `remember_topic` / `read_topic`): `(hotel_name, user_phone)` instead of `hotel_name` + "the hotel's manager". `hotel_screen` becomes unique on `hotel_name|user_phone`.

**Onboarding and invites**
- `onboard_turn._write_account` writes `user_account`. `_write_hotel` creates `hotel`, seeds the 8 roles and 96 `role_module` rows, and makes the creator GENERAL_MANAGER.
- `hotel_context_manager.signup_manager` / `set_manager_hotel` become `signup_user` / `create_hotel_user`. The "one manager per hotel" and "one hotel per phone" checks go.
- **New `user_manager.py` tools (setup, manage; each does READ, then validate, then act, or clarifies on a near match):**
  - `invite_user(phone, name, role_name)`: if the phone already exists at this hotel it updates, otherwise it creates the user `invited`. The WhatsApp invite is sent through `whatsapp_list`.
  - `accept_invite`: the invitee sets a password on first sign-in, and the status becomes `active`.
  - `change_role`, `set_module_override`, `suspend_user`, `remove_user`.
  - Role CRUD: `create_role`, `set_role_module`.
- UI:
  - `onboard.js` adds an invite step.
  - `property_setup.js` gets a Users and roles panel.
  - `spa-shell.js` hides modules whose level is `none` (the server still enforces).
  - `leon-chat.js` / `desk.js` show the "outside your access" reply as is.

**Backfill and migration**
1. Insert the seeds above, then the 8 `user_account` rows and 7 `hotel_user` rows (D14, D15).
2. Copy each manager document to `hotel_user_profile` (key `hotel_name|user_phone`) and to the `user_account` document (password hash, language fields). `manager_collection` stays read-only until A8.
3. **Re-key** `manager_name` to `user_phone` through the manager documents (8 distinct names map 1:1):
   - `investigations` 34
   - `desk_dialog` 6, `desk_session` 2, `desk_topic` 2
   - `gap_candidates` 0
   - `catalogs` / `catalog_versions` user bodies 0
   - `hotel_screen` 1, `hotel_pins` 1, `hotel_card_seen` 4
   - `activity_logs` 2

   An unmapped name is listed and its row is copied to `mig_backup`, not guessed.
4. Tag `catalogs.module_name` for the 15 general recipes and the 1 retired one, then set NOT NULL.
5. Seed `access_point` from the four registries and review the generated sheet once. Then turn on `_module_guard`, in report-only mode for 3 days (it logs would-be refusals) before enforcing.

**Verify**
- `SELECT hotel_name FROM hotel h WHERE NOT EXISTS (SELECT 1 FROM hotel_user u WHERE u.hotel_name=h.hotel_name AND u.role_name='GENERAL_MANAGER' AND u.status='active')` returns 0 rows.
- `role_module` has exactly roles × 12 rows per hotel (672).
- Every route in `app.routes` is in `public_route` or `access_point`; the startup check passes.
- `manager_name` columns are gone from the 5 tables; `catalogs.module_name` NULL count is 0.
- **Scripted test per seeded role:** sign in, then call each API, desk action and tool.
  - A 403 or "outside your access" appears exactly where the matrix says `none` or too low.
  - A FRONT_DESK user asking "outstanding balance" gets "outside your access", and no investigation row is created.
  - An ACCOUNTANT gets the recipe answer.
- **Multi-hotel test:** one phone at two hotels gets the hotel question at sign-in. `switch_hotel` changes the claims. A token for hotel A can't touch hotel B.
- **Revocation:** suspending a user makes their next request fail without waiting for `exp`.
- **Report-only log:** 0 unexpected refusals for the GENERAL_MANAGER accounts over the 3 days.

**Rollback**
- Before enforcement, turn off `_module_guard` (one flag) and revert `_process_signin` to the profile token.
- The `manager_name` columns are dropped only after 3 clean days; until then the dual-written `manager_name` is still there.
- Recipe module tags are a new catalog column; they don't change bodies or versions.
- Drop the tables in reverse order.

**Risk.** Medium to high.
- **Lock-out.** A wrong `access_point` row can lock real staff out. Mitigated by report-only mode, the GENERAL_MANAGER invariant and the per-role test.
- **Guarding the unguarded routes.** About 25 routes that today read or write hotel data without a token, including the inventory, pricing and room-status updates, the payments ledger and the guest biography, start requiring one. Callers such as `workers/pms.js` and the UI must send it.
- **Phone overlap.** A user who is also a guest by phone must not have guest messages routed to the desk; the D17 rule and a test cover this.

**Effort.** 8–10 days:
- tables, seeds and backfill: 1 d
- auth, JWT, switch and invites: 1.5 d
- guard, access points and the unguarded routes: 2 d
- desk, tools and WhatsApp: 1 d
- semantic layer: 1.5–2 d
- runtime re-key: 0.5–1 d
- UI: 1 d
- role tests: 0.5–1 d

### A2: `room_type` and `room` masters (2 days)

```sql
CREATE TABLE room_type (
  hotel_name VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  room_type  VARCHAR(255) NOT NULL CHECK (room_type = UPPER(btrim(room_type)) AND room_type <> ''),
  status     VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, room_type));
CREATE TABLE room (
  hotel_name  VARCHAR(255) NOT NULL,
  room_number VARCHAR(32)  NOT NULL CHECK (room_number = UPPER(btrim(room_number)) AND room_number <> ''),
  room_type   VARCHAR(255) NOT NULL,
  status      VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, room_number),
  FOREIGN KEY (hotel_name, room_type) REFERENCES room_type ON UPDATE CASCADE);
```

Add FKs (NOT VALID, then VALIDATE, ON UPDATE CASCADE):
- **To `room_type`:** from `room_inventory_nightly`, `booking_stays`, `room_bookings`, `channel_reservations`, `hotel_room_code_mapping`, `room_allotments`, `kite_price_anchor`, `room_number_assignments`.
- **To `room`:** from `room_number_assignments`, `room_readiness_status`, `room_stay`, `cleaning_status`.

**Backfill.** **8 room types and 28 rooms** from `room_inventory_nightly` and the `room_encyclopedia` room documents (D3: the DJ HOTEL type is kept). `room_quantity` in documents becomes `COUNT(room)`.

**Code**
- Writers: `room_manager._create_room_type_logic`, `add_room_numbers`, `_write_room_rows`, `manage_room_description`, `delete_readiness_status_record`, `_move_room_number_sql` (replaced by CASCADE); `inventory_manager.manage_room_quantity`, `apply_room_count`.
- Key validation reads the tables: `desk_actions._known_room_types`, `ota_mapping._room_types`, `policy_manager.read_available_hotels`, `booking_manager.read_available_room_combinations`, `create_booking`, `leon_turn.sales_facts`, `front_desk_manager.read_room_number_assignments_calendar_internal`, `room_manager.read_room_type_from_room_number_tool`.
- Delete paths remove children first.

**Recipes.** None.

**Verify.** Room count per type vs inventory: JACKHOTEL FAMILY 3/3 and NEW SUITE 3/3. No unvalidated FKs.

**Rollback.** Drop the FKs and tables. **Risk.** Low to medium.

### A3: `staff` key and natural keys for operations tables (2–3 days)

```sql
CREATE TABLE staff (
  hotel_name      VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  staff_phone     VARCHAR(16)  NOT NULL CHECK (left(staff_phone,1)='+' AND translate(substr(staff_phone,2),'0123456789','')='' AND char_length(staff_phone) BETWEEN 8 AND 16),
  staff_name      VARCHAR(255) NOT NULL,
  department_name VARCHAR(64)  NOT NULL,      -- FK added in C1
  status          VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, staff_phone));

ALTER TABLE staff_duty_log DROP CONSTRAINT staff_duty_log_pkey, DROP COLUMN duty_id,
  ALTER COLUMN clock_in TYPE timestamptz USING clock_in AT TIME ZONE 'UTC',
  ALTER COLUMN clock_out TYPE timestamptz USING clock_out AT TIME ZONE 'UTC',
  ADD PRIMARY KEY (hotel_name, staff_phone, clock_in),
  ADD FOREIGN KEY (hotel_name, staff_phone) REFERENCES staff ON UPDATE CASCADE;

ALTER TABLE cleaning_status DROP CONSTRAINT cleaning_status_pkey, DROP COLUMN cleaning_id;
ALTER TABLE cleaning_status RENAME COLUMN assigned_to TO assigned_staff_phone;
ALTER TABLE cleaning_status RENAME COLUMN quality_checked_by TO quality_checked_staff_phone;
ALTER TABLE cleaning_status ALTER COLUMN created_at SET NOT NULL,
  ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC',
  ADD PRIMARY KEY (hotel_name, room_number, created_at),
  ADD FOREIGN KEY (hotel_name, assigned_staff_phone) REFERENCES staff ON UPDATE CASCADE,
  ADD FOREIGN KEY (hotel_name, quality_checked_staff_phone) REFERENCES staff ON UPDATE CASCADE;
DROP TABLE housekeeping_tasks;   -- 0 rows, keyed by room_type_id; folded into cleaning_status

ALTER TABLE maintenance_schedules ADD COLUMN target VARCHAR(64);
UPDATE maintenance_schedules SET target = CASE WHEN target_type = 'ROOM' THEN room_number ELSE area END;
ALTER TABLE maintenance_schedules ADD CHECK ((room_number IS NULL) <> (area IS NULL)),
  ALTER COLUMN target SET NOT NULL;
-- D4 dedupe here (keep earliest start_date), then:
ALTER TABLE maintenance_schedules DROP CONSTRAINT maintenance_schedules_pkey, DROP COLUMN schedule_id,
  ADD PRIMARY KEY (hotel_name, target_type, target, issue),
  ADD FOREIGN KEY (hotel_name, room_number) REFERENCES room ON UPDATE CASCADE;

ALTER TABLE maintenance_requests ADD COLUMN target_type VARCHAR(8), ADD COLUMN target VARCHAR(64),
  ADD COLUMN schedule_issue VARCHAR, ADD COLUMN reported_by_user_phone VARCHAR(16), ADD COLUMN assigned_staff_phone VARCHAR(16);
-- backfill (below), then:
ALTER TABLE maintenance_requests ALTER COLUMN room_number DROP NOT NULL;
UPDATE maintenance_requests SET room_number = NULL WHERE target_type = 'AREA';
ALTER TABLE maintenance_requests DROP CONSTRAINT maintenance_requests_pkey,
  DROP COLUMN task_id, DROP COLUMN schedule_id, DROP COLUMN reported_by, DROP COLUMN assigned_to,
  ALTER COLUMN reported_at TYPE timestamptz USING reported_at AT TIME ZONE 'UTC',
  ADD PRIMARY KEY (hotel_name, target_type, target, issue, reported_at),
  ADD FOREIGN KEY (hotel_name, target_type, target, schedule_issue) REFERENCES maintenance_schedules (hotel_name, target_type, target, issue) ON UPDATE CASCADE,
  ADD FOREIGN KEY (hotel_name, room_number) REFERENCES room ON UPDATE CASCADE,
  ADD FOREIGN KEY (hotel_name, reported_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  ADD FOREIGN KEY (hotel_name, assigned_staff_phone) REFERENCES staff ON UPDATE CASCADE;
DROP INDEX ux_mr_pm_unique;
CREATE UNIQUE INDEX ux_mr_pm_due ON maintenance_requests (hotel_name, target_type, target, schedule_issue, due_date) WHERE schedule_issue IS NOT NULL;
ALTER TABLE room_readiness_status ADD COLUMN changed_by_user_phone VARCHAR(16),
  ADD FOREIGN KEY (hotel_name, changed_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE;
```

**Backfill**
- **`staff`:** 7 rows from manager documents (D8): `department_name='MANAGEMENT'`.
- **Requests:** `target_type` / `target` come from `room_number` (`'N/A'` means AREA). `schedule_issue` comes from the linked schedule, joined on the old `schedule_id` before it is dropped.
- **People:** `reported_by_user_phone` and `room_readiness_status.changed_by_user_phone` are mapped from `manager_name` to the `hotel_user` phone (A1b) (1 and 7 distinct names). Unmapped names stay NULL and are listed. `assigned_staff_phone` / `quality_checked_staff_phone` stay on `staff`: workers can be assigned without having a login.

**Code**
- `maintenance_manager`: delete `generate_task_id`; change `create_maintenance_request`, `read_maintenance_requests`, `update_maintenance_progress`, `setup_scheduled_maintenance`, `read_scheduled_maintenance`, `update_scheduled_maintenance`, `run_daily_pm_generation`. Tools identify a task by room or area, issue and reported date: READ, then UPDATE or clarify.
- `housekeeping_manager`: `create_cleaning_task`, `start_cleaning_for_room`, `update_cleaning_progress`, `staff_clock_in`, `staff_clock_out`, `get_on_duty_staff_json`, `get_staff_workload_json`, `read_staff_current_cleaning_status`, `read_room_current_cleaning`, and the metrics/report functions. Fold `assign_cleaning_task`, `update_task_progress`, `schedule_maintenance_cleaning` and `get_room_status_dashboard` onto `cleaning_status`.
- `staff_manager` (all profile functions, `_validate_phone_uniqueness`) and `guest_manager._validate_phone_uniqueness`.
- UI `maintenance.js` / `rooms.js` and the `main.py` maintenance endpoints (7 `task_id` / `schedule_id` references). Schema text in `semantic/catalog.py`.

**Recipes.** None.

**Verify.** All FKs are validated; `maintenance_requests` without a reporter returns 0.

**Rollback.** Restore from `mig_backup` (7 rows of data). **Risk.** Medium (task_id is in the API, UI and tools).

### A4: `booking_room_night`, the nightly fact (5–7 days)

**Goal.** One row per booked room-night replaces `booking_stays` and the non-room fields of `room_stay`. The header loses `check_in_date`, `nights`, `rooms_booked` and `room_type`; the `booking_summary` view derives them. This applies D1 by construction.

```sql
ALTER TABLE room_bookings DROP CONSTRAINT room_bookings_pkey, ADD PRIMARY KEY (hotel_name, booking_id);
CREATE TABLE booking_room_night (
  hotel_name     VARCHAR(255) NOT NULL,
  booking_id     VARCHAR(255) NOT NULL,
  stay_date      DATE         NOT NULL,
  room_seq       SMALLINT     NOT NULL CHECK (room_seq > 0),
  room_type      VARCHAR(255) NOT NULL,
  night_status   VARCHAR(16)  NOT NULL CHECK (night_status IN ('booked','checked_in','checked_out','cancelled','released','no_show')),
  sell_rate      NUMERIC(12,2) CHECK (sell_rate >= 0),
  currency       CHAR(3),
  rate_source    VARCHAR(16)  CHECK (rate_source IN ('channel','desk','override','rate_plan')),
  checked_in_at  TIMESTAMPTZ,
  checked_out_at TIMESTAMPTZ,
  PRIMARY KEY (hotel_name, booking_id, stay_date, room_seq),
  FOREIGN KEY (hotel_name, booking_id) REFERENCES room_bookings ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, room_type, stay_date) REFERENCES room_inventory_nightly ON UPDATE CASCADE,
  CHECK ((sell_rate IS NULL) = (rate_source IS NULL)),
  CHECK (sell_rate IS NULL OR currency IS NOT NULL));
CREATE INDEX brn_hotel_date ON booking_room_night (hotel_name, stay_date);
CREATE INDEX brn_hotel_type_date ON booking_room_night (hotel_name, room_type, stay_date);
ALTER TABLE room_number_assignments ADD COLUMN room_seq SMALLINT;

CREATE VIEW booking_summary AS
SELECT b.hotel_name, b.booking_id, b.guest_phone, b.booking_status, b.channel, b.group_name, b.currency,
       MIN(n.stay_date) AS check_in_date, MAX(n.stay_date) + 1 AS check_out_date,
       COUNT(DISTINCT n.stay_date) AS nights, MAX(n.room_seq) AS rooms_booked,
       string_agg(DISTINCT n.room_type, ', ') AS room_types
FROM room_bookings b JOIN booking_room_night n USING (hotel_name, booking_id)
GROUP BY b.hotel_name, b.booking_id;
```

**Backfill**
- **History inventory (D5):** at most about 60 rows (2 types × 30 days).
  ```sql
  INSERT INTO room_inventory_nightly (hotel_name, room_type, stay_date, total_inventory, blocked_quantity, created_by)
  SELECT DISTINCT s.hotel_name, s.room_type, s.stay_date,
         (SELECT COUNT(*) FROM room r WHERE (r.hotel_name, r.room_type) = (s.hotel_name, s.room_type)), 0, 'migration_history'
  FROM booking_stays s
  WHERE NOT EXISTS (SELECT 1 FROM room_inventory_nightly i WHERE (i.hotel_name, i.room_type, i.stay_date) = (s.hotel_name, s.room_type, s.stay_date));
  ```
- **Nights:** expect **105 rows** (JACKHOTEL 100, other hotels 5), with 5 cancelled and 4 checked in.
  ```sql
  INSERT INTO booking_room_night (hotel_name, booking_id, stay_date, room_seq, room_type, night_status, checked_in_at, checked_out_at)
  SELECT s.hotel_name, s.booking_id, s.stay_date, k.seq, s.room_type,
         CASE WHEN s.booking_status = 'cancelled' OR r.stay_status = 'cancelled' THEN 'cancelled'
              WHEN r.stay_status IN ('checked_in','checked_out') THEN r.stay_status ELSE 'booked' END,
         r.checked_in_at AT TIME ZONE 'UTC', r.checked_out_at AT TIME ZONE 'UTC'
  FROM booking_stays s CROSS JOIN LATERAL generate_series(1, s.rooms_booked) AS k(seq)
  LEFT JOIN room_stay r ON (r.hotel_name, r.booking_id, r.stay_date, r.stay_index) = (s.hotel_name, s.booking_id, s.stay_date, k.seq);
  ```
- **Assignments:** link 16 rows: `UPDATE room_number_assignments a SET room_seq = r.stay_index FROM room_stay r WHERE (r.hotel_name, r.booking_id, r.stay_date, r.room_number) = (a.hotel_name, a.booking_id, a.stay_date, a.room_number)`.
- **Sell rates:**
  - **6 nights** from the OTA `rooms[].prices[].sellRate`, with `rate_source='channel'`, `currency` = booking currency (INR), joined on `rooms` ordinality = `room_seq`.
  - **At least 5 nights** from desk `pricing_breakdown.lines[]`, with `rate_source='desk'` and `currency = pricing_breakdown.currency`.
  - The remaining **~94 nights** have `sell_rate` NULL ("unrated").

**Code**
- **Dual-write (same transaction).**
  - `booking_manager`: `create_booking`, `create_group_booking`, `_rebuild_booking_same_id`, `cancel_booking`, `update_booking_status`, `modify_booking`, `_assign_open_rooms`, `_ensure_booking_channel`.
  - `aiosell_reservation`: `_apply_locked`, `_cancel`.
  - `front_desk_manager`: `update_guest_check_in_status_internal`, `update_guest_check_out_status_internal`, `undo_guest_check_in_internal`, `_release_nights_after_checkout` (writes `released`), `reset_stay_state_for_date_internal`, `bulk_checkout_past_internal`, `bulk_checkout_selected_internal`, `_fill_blank_stay_slots`, `_hold_room_for_stay_slot`, `reassign_room_for_date_internal`, `relocate_guest_internal`, `unassign_room_number_internal`, `update_room_assignment_auto`, `update_booking_detail_internal`, `override_pricing_internal` (`override`).
  - `main.api_bookings_cancel`, `cancel_assignment_booking`; `room_manager._move_room_number_sql`.
- **Readers.**
  - `booking_manager`: `read_booking`, `read_bookings_by_criteria`, `read_recent_bookings`, `read_availability`, `get_stay_dates_for_booking`, `get_booking_analytics`, `analyze_booking_patterns`, `get_revenue_forecast`, `read_hotel_analytics`, `pull`.
  - `front_desk_manager`: `read_booking_detail_internal`, `read_booking_preview_internal`, `read_room_number_assignments_calendar_internal`, `read_past_stays_internal`, `read_assignment_history_internal`, `read_available_room_numbers_internal`, `read_room_assignment_tool`, `read_room_assignments_for_booking_tool`, `_room_open_for_slot`, `_stay_slot_for_room`, `verify_booking_phone`.
  - `guest_manager._guest_booking_rows`; `leon_turn.priced`, `stays`; `kite_pricing.attach_quotes`, `quote`, `quotes_for_range`; `payment_manager._recompute_payment_tracking`; `aiosell_channel_manager._available_by_date_locked`; `desk_actions.query`; `main._stay_dates_for_booking`.
  - Schema text in `semantic/catalog.py`, `sql_agent._rules`, `library.ensure_confirmed_page`.
- **Header readers** move to `booking_summary`: `booking_manager.create_booking_tool`, `payment_manager.check_payment_deadlines`, `guest_engagement_manager` (5 functions), `mobile_checkin_manager.initiate_mobile_checkin`, `ota_agent.*`, the `llm_utils` prompt text.

**Recipes**
- **General:**
  - Rewrite the `_FACTS` `nightly`, `checked`, `actual_in`, `actual_out`, `guests_night` and `revenue` on `booking_room_night`. Delete `header`.
  - `arrivals` / `departures` use `booking_summary`.
  - Remove the "sold vs header" limitation.
  - Affected: 10 recipes reading `booking_stays`, 9 reading `room_stay`, 8 reading `check_in_date`.
  - The only expected output diff is BDC4082659's arrival moving from 25 to 30 Oct.
- **User:** new versions of `scheduled_arrivals_today`, `confirmed_reservation_counts`, `daily_booking_counts` and `monthly_long_stay_arrival_count` on `booking_summary`. Activate them before the header columns are dropped.

**Verify.** All 0 during dual-write: missing nights (stays × rooms vs nights), cancellation-status diffs, and header vs `booking_summary` diffs (the last is 1 until D1 is applied).

**Cut-over.**

```sql
ALTER TABLE room_bookings DROP COLUMN check_in_date, DROP COLUMN nights, DROP COLUMN rooms_booked, DROP COLUMN room_type;
DROP TABLE booking_stays;
ALTER TABLE room_stay RENAME TO room_stay_legacy;
```

**Rollback.** Before the drop, revert the code and recipe versions. After it, restore `mig_backup` plus a replay from `booking_room_night`.

**Risk.** **High.** About 30 front-desk functions change. Lock order must be `booking_room_night` first, then `room_number_assignments`.

### A5: Occupancy-only room assignments, with OOO and OOS blocks (3–4 days)

```sql
DELETE FROM room_number_assignments WHERE assignment_status = 'available' AND booking_id IS NULL;   -- 10,204 rows
ALTER TABLE room_number_assignments
  DROP COLUMN assignment_id, DROP COLUMN room_type, DROP COLUMN guest_phone, DROP COLUMN check_in_time, DROP COLUMN check_out_time,
  ALTER COLUMN assigned_at TYPE timestamptz USING assigned_at AT TIME ZONE 'UTC',
  ADD COLUMN block_reason VARCHAR(255),
  ADD CHECK (assignment_status IN ('assigned','occupied','out_of_order','out_of_service','hold')),
  ADD CHECK ((booking_id IS NULL) = (room_seq IS NULL)),
  ADD CHECK ((booking_id IS NOT NULL) = (assignment_status IN ('assigned','occupied'))),
  ADD CHECK ((assignment_status IN ('out_of_order','out_of_service')) = (block_reason IS NOT NULL)),
  ADD FOREIGN KEY (hotel_name, booking_id, stay_date, room_seq) REFERENCES booking_room_night ON UPDATE CASCADE ON DELETE CASCADE,
  ADD UNIQUE (hotel_name, booking_id, stay_date, room_seq);
DROP TABLE room_stay_legacy;
```

The two block types differ in effect: **OOO** (out of order) reduces sellable inventory, while **OOS** (out of service) is still sellable but excluded from room assignment.

**Backfill.** None. **16 rows remain.**

**Code**
- **Free-room logic** becomes "no row": `front_desk_manager.read_available_room_numbers_internal`, `_room_open_for_slot`, `read_assignment_dashboard`, `read_room_number_assignments_calendar_internal`, `suggest_room_assignment`, `test_assignment_system`, `update_room_available_status_internal` (DELETE the block), `update_room_out_of_order_status_internal` / `mark_room_out_of_order` (INSERT the block with a reason; add OOS).
- **Room counts** come from `room`: `inventory_manager.count_rooms`, `apply_room_count`, `room_counts_between`, `read_calendar`, `update_dates`.
- **Channels and pricing:** `aiosell_channel_manager._available_by_date_locked` subtracts OOO only; `kite_pricing.quote`, `attach_quotes`, `quotes_for_range`.
- **Maintenance blocks:** `maintenance_manager.create_maintenance_request`, `setup_scheduled_maintenance`, `update_scheduled_maintenance` (`auto_out_of_order`).
- **`room_manager`:** `_create_assignment_records` stops creating placeholders; also `_delete_assignment_records`, `_write_room_rows`.
- **`assignment_id` users:** `booking_manager._assign_open_rooms`, `_ensure_booking_channel`, `_rebuild_booking_same_id`, `create_booking`; `aiosell_reservation._apply_locked`; `front_desk_manager._hold_room_for_stay_slot`, `update_guest_check_in_status_internal`; `main.assignments_check_in`.

**Recipes.** None today. R1 snapshots read OOO and OOS.

**Verify.** A 30-day availability comparison (channel push and calendar), before and after, is identical. Checked-in nights without a room return 0.

**Rollback.** Regenerate placeholders (`room × generate_series ... ON CONFLICT DO NOTHING`) and restore the columns from `mig_backup`.

**Risk.** Medium to high (overselling).

### A6: Booking value and channel commission (1.5–2 days)

**Goal.** Store the value the channel or desk sold the booking for (on-the-books revenue, commission) as computable columns. Posted revenue comes from folios (C3/C4); A6 is the forecast-side value.

```sql
ALTER TABLE room_bookings
  ADD COLUMN gross_amount      NUMERIC(12,2) CHECK (gross_amount >= 0),
  ADD COLUMN tax_amount        NUMERIC(12,2),
  ADD COLUMN commission_amount NUMERIC(12,2),
  ADD COLUMN net_amount        NUMERIC(12,2) GENERATED ALWAYS AS (gross_amount - commission_amount) STORED,
  ADD COLUMN amount_source     VARCHAR(16) CHECK (amount_source IN ('channel','desk')),
  ALTER COLUMN currency TYPE CHAR(3),
  ADD CHECK (currency = UPPER(currency)),
  ADD CHECK (gross_amount IS NULL OR amount_source IS NOT NULL);
```

**Backfill**
- **93 rows** from `ota_reservations.payload.amount`: `amountAfterTax`, `tax`, `commission`. Commission is 0 for 81 and NULL for 12.
- **5 rows** from desk `pricing_breakdown`.
- **Currency** is NULL for 90 (D6: currency unknown).
- After the readers move, drop `room_bookings.total_amount` (NULL on 93 of 93).

**Code**
- Writers: `aiosell_reservation._apply_locked`, `booking_manager.create_booking`, `create_group_booking`.
- `total_amount` readers move to `gross_amount`:
  - `booking_manager`: `read_booking`, `read_bookings_by_criteria`, `read_recent_bookings`, `get_booking_analytics`, `analyze_booking_patterns`, `get_revenue_forecast`
  - `front_desk_manager`: `read_booking_detail_internal`, `read_booking_preview_internal`, `read_pricing_breakdown_internal`
  - `guest_manager`: `_guest_booking_rows`, `query`, `read_guest_with_bookings`
  - `guest_summary._facts`; `ota_agent.*`

**Recipes**
- `revenue` fact (general):
  - Nightly revenue is `SUM(sell_rate)` where known.
  - Otherwise it is `gross_amount` spread over the booking's active nights by rooms, as a stated definition with `revenue_basis` = `nightly` or `booking_spread`.
  - Add `net_revenue` and `commission`.
- `daily_room_revenue`, `daily_adr` and `daily_revpar` group by currency and output `excluded_unknown_currency` (D6).
- R1 later replaces the past side with posted revenue.

**Verify.** 98 rows have `amount_source`; old `payment_tracking.total_amount` equals `gross_amount` for 93 of 93.

**Rollback.** Drop the columns. **Risk.** Low.

### A7: timestamptz, `booked_at`, room status history (2.5–3 days)

```sql
-- generate, review, run per table
SELECT format('ALTER TABLE %I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC'';', table_name, column_name, column_name)
FROM information_schema.columns WHERE table_schema='public' AND data_type='timestamp without time zone'
  AND table_name IN ('room_bookings','room_number_assignments','payment_ledger','payment_tracking','channel_reservations','channel_reservation_sync',
                     'room_readiness_status','cleaning_status','room_inventory_nightly','daily_pricing','guest_complaint',
                     'kite_price_anchor','kite_hotel_setting','room_allotments');
ALTER TABLE guest_message ALTER COLUMN sent_at TYPE timestamptz USING sent_at::timestamptz;            -- 42 rows
ALTER TABLE guest_profile ALTER COLUMN registered_at TYPE timestamptz USING registered_at::timestamptz,  -- 9 non-null
                          ALTER COLUMN date_of_birth TYPE date USING date_of_birth::date;
ALTER TABLE room_bookings ADD COLUMN booked_at timestamptz, ADD COLUMN cancelled_at timestamptz;
CREATE TABLE room_status_history (
  hotel_name             VARCHAR(255) NOT NULL,
  room_number            VARCHAR(32)  NOT NULL,
  changed_at             TIMESTAMPTZ  NOT NULL,
  readiness_status       VARCHAR(16)  NOT NULL,
  changed_by_user_phone VARCHAR(16),
  PRIMARY KEY (hotel_name, room_number, changed_at),
  FOREIGN KEY (hotel_name, room_number) REFERENCES room ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, changed_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE);
CREATE FUNCTION log_room_status() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN INSERT INTO room_status_history VALUES (NEW.hotel_name, NEW.room_number, NEW.status_changed_at, NEW.readiness_status, NEW.changed_by_user_phone); RETURN NEW; END $$;
CREATE TRIGGER room_status_history_t AFTER INSERT OR UPDATE OF readiness_status ON room_readiness_status FOR EACH ROW EXECUTE FUNCTION log_room_status();
```

Room status changes are recorded **only** in `room_status_history`; C2's generic `audit_log` excludes this table, so the fact is not duplicated.

**Backfill**
- `booked_at`: 93 rows from `bookedOn` converted with `AT TIME ZONE hotel.timezone` (D7); 5 from desk `created_timestamp`. Then `NOT NULL`.
- `cancelled_at`: 3 rows from channel cancel messages. Earlier desk cancellations stay NULL.
- `room_status_history`: 28 seed rows.

**Code**
- **Aware/naive datetime audit** (database values become timezone-aware): `housekeeping_manager` (26 sites), `inventory_manager` (18), `room_manager` (8), `front_desk_manager` (8), `booking_manager` (7), `maintenance_manager` (6), `payment_manager` (2).
- **Write `booked_at`** in `aiosell_reservation._apply_locked` and `booking_manager.create_booking`.

**Recipes**
- `_local(col)` becomes `(col AT TIME ZONE h.timezone)::date`.
- `daily_bookings_made`, `daily_booking_lead_time` and `daily_channel_mix` use `booked_at`.
- 15 general recipes and 1 user recipe reference `created_at`.
- The `_PERIODS` anchor moves to the business date in C4.

**Verify.** No naive timestamp columns remain in the listed tables; `booked_at` NULL count is 0.

**Rollback.** Convert back with `USING col AT TIME ZONE 'UTC'` (lossless). **Risk.** Medium to high (runtime TypeErrors).

### A8: Documents: indexes, computable fields out, guest key (3–4 days)

```sql
CREATE INDEX CONCURRENTLY cd_body_gin  ON content_document USING gin (body jsonb_path_ops);
CREATE INDEX CONCURRENTLY cd_hotel     ON content_document (database_name, collection_name, (body->>'hotel_name'));
CREATE INDEX CONCURRENTLY cd_room_type ON content_document ((body->>'hotel_name'), (body->>'room_type')) WHERE collection_name = 'room_encyclopedia';
CREATE INDEX CONCURRENTLY cd_guest     ON content_document ((body->>'hotel_name'), (body->>'guest_phone')) WHERE collection_name IN ('guests','guest_bookings','loyalty_program','guest_messages');
CREATE INDEX CONCURRENTLY cd_city      ON content_document ((body->>'city_name')) WHERE collection_name = 'hotel_profile';
CREATE TABLE loyalty_points (
  hotel_name  VARCHAR NOT NULL,
  guest_phone VARCHAR NOT NULL,
  earned_at   TIMESTAMPTZ NOT NULL,
  points      INTEGER NOT NULL CHECK (points <> 0),
  reason      VARCHAR NOT NULL,
  booking_id  VARCHAR,
  PRIMARY KEY (hotel_name, guest_phone, earned_at),
  FOREIGN KEY (hotel_name, guest_phone) REFERENCES guest_profile ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, booking_id) REFERENCES room_bookings ON UPDATE CASCADE);
ALTER TABLE guest_profile
  ADD CHECK (left(guest_phone,1)='+' AND translate(substr(guest_phone,2),'0123456789','')='' AND char_length(guest_phone) BETWEEN 8 AND 16),
  DROP COLUMN points_balance, DROP COLUMN points_earned, DROP COLUMN points_redeemed, DROP COLUMN average_rating,
  DROP COLUMN approval_actions, DROP COLUMN desk_tasks, DROP COLUMN carried_actions, DROP COLUMN executable_actions,
  DROP COLUMN guest_needs, DROP COLUMN communication_preferences, DROP COLUMN guest_profile_summary, DROP COLUMN recommended_action;
-- D2 quarantine of the 3 empty-phone bookings, then:
ALTER TABLE room_bookings ADD FOREIGN KEY (hotel_name, guest_phone) REFERENCES guest_profile ON UPDATE CASCADE;
```

**Backfill**
- `loyalty_points`: 0 rows (all balances are 0).
- Guest JSON and summary fields move to `guests` documents (1 non-empty profile).
- `$unset` computable document fields:
  - `guest_bookings` (98): `amount`, the money keys in `pricing_breakdown`, `total_amount`, `rooms_booked`, `stay_dates`, `checkin`, `checkout`
  - `guests` (9): `total_spent`, `total_bookings`, `average_rating`
  - `loyalty_program` (9): `points_*`
  - `room_encyclopedia` (2): `room_quantity`
  - `manager_collection` (7): `hotel_currency`, `hotel_timezone`

**Code**
- **`content_store.Collection._matched` / `_rows`:** push top-level scalar equality filters into SQL as `body @> %s::jsonb`, skipping `None` values (Python treats a missing key as `None`). Make `create_index` and `list_indexes` real.
- **`guest_bookings` writers:** `aiosell_reservation._apply_locked`, `run`; `booking_manager.create_booking`, `_rebuild_booking_same_id`, `add_booking_special_request`; `ota_agent._save_booking_details`; `onboard_turn._booking_doc`.
- **`guest_bookings` readers:** `front_desk_manager.read_pricing_breakdown_internal`, `read_booking_detail_internal`, `read_past_stays_internal`, `verify_booking_phone`; `payment_manager.check_payment_deadlines`; `booking_manager.read_recent_bookings`, `list_bookings_with_special_requests`.
- **Guest and loyalty:** `guest_manager._ensure_loyalty_collection`, `manage_loyalty_program`, `get_guest_analytics`, `_ensure_guest_operations`; `loyalty_manager`; `guest_summary._facts`.
- **Unscoped lookups** (`find_one({"guest_phone": ...})`, booking lookups by `booking_id` only) in `front_desk_manager` and `staff_manager` gain `hotel_name`.

**Recipes.** None.

**Verify.** `EXPLAIN` shows an index scan; no money fields remain in `guest_bookings`; the guest FK is validated.

**Rollback.** Drop the indexes, revert `content_store`, restore from `mig_backup`. **Risk.** Medium.

---

## Part 4: Shared core (B), used by every department

### C1: Department, cost centre, outlet (1–1.5 days)

**Goal.** One organisational spine. Every posting, staff member, task and report rolls up to `outlet → cost_center → department → hotel`. Descriptions and opening hours go in `outlet_profile` documents keyed `hotel_name|outlet_name`.

```sql
CREATE TABLE department (
  hotel_name      VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  department_name VARCHAR(64)  NOT NULL CHECK (department_name = UPPER(btrim(department_name)) AND department_name <> ''),
  department_type VARCHAR(16)  NOT NULL CHECK (department_type IN ('operated','undistributed','fixed')),   -- USALI grouping
  PRIMARY KEY (hotel_name, department_name));
CREATE TABLE cost_center (
  hotel_name       VARCHAR(255) NOT NULL,
  cost_center_name VARCHAR(64)  NOT NULL CHECK (cost_center_name = UPPER(btrim(cost_center_name))),
  department_name  VARCHAR(64)  NOT NULL,
  PRIMARY KEY (hotel_name, cost_center_name),
  FOREIGN KEY (hotel_name, department_name) REFERENCES department ON UPDATE CASCADE);
CREATE TABLE outlet (
  hotel_name       VARCHAR(255) NOT NULL,
  outlet_name      VARCHAR(64)  NOT NULL CHECK (outlet_name = UPPER(btrim(outlet_name))),
  outlet_type      VARCHAR(16)  NOT NULL CHECK (outlet_type IN ('rooms','restaurant','bar','room_service','banquet','spa','laundry','shop','other')),
  department_name  VARCHAR(64)  NOT NULL,
  cost_center_name VARCHAR(64)  NOT NULL,
  status           VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, outlet_name),
  FOREIGN KEY (hotel_name, department_name) REFERENCES department ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, cost_center_name) REFERENCES cost_center ON UPDATE CASCADE);
ALTER TABLE staff ADD FOREIGN KEY (hotel_name, department_name) REFERENCES department ON UPDATE CASCADE;
```

**Backfill**
- **Departments:** per hotel `ROOMS`, `HOUSEKEEPING`, `MAINTENANCE`, `FOOD_AND_BEVERAGE`, `ADMIN_GENERAL`, `SALES_MARKETING`, `MANAGEMENT`, giving 7 × 7 = **49**.
- **Cost centres:** one per department (**49**).
- **Outlets:** `ROOMS` per hotel (**7**). F&B, spa and similar outlets are created by their modules.

**Code**
- `onboard_turn._write_hotel` seeds the departments for a new hotel.
- `staff_manager.read_staff_by_department` validates against `department`.
- `housekeeping_manager` and `maintenance_manager` take their department from the table.
- New `org_manager.py` (CRUD with READ-validate-clarify).

**Recipes.** None yet (R2 uses this). **Verify.** Every staff row's department exists; 49 / 49 / 7.

**Rollback.** Drop the tables and the staff FK. **Risk.** Low.

### C2: Audit trail (1.5–2 days)

**Goal.** Record who changed what, in every table, as `(hotel_name, user_phone)` or a named system actor. Roles and module access moved to A1b. This replaces `booking_modification_log` (0 rows). `manager_collection.desk_require_approval` (1 document) becomes the `desk_approval` setting in the `hotel_user_profile` document, editable at setup `manage`.

```sql
CREATE TABLE audit_log (
  hotel_name             VARCHAR(255) NOT NULL,
  entity_name            VARCHAR(64)  NOT NULL,           -- table name
  entity_key             TEXT         NOT NULL,           -- natural key values, e.g. 'booking_id=BDC4082659'
  changed_at             TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
  action                 VARCHAR(8)   NOT NULL CHECK (action IN ('insert','update','delete')),
  changed_by_user_phone VARCHAR(16),
  changed_by_system      VARCHAR(32)  CHECK (changed_by_system IN ('channel_aiosell','night_audit','migration','scheduler')),
  before_row             JSONB,
  after_row              JSONB,
  PRIMARY KEY (hotel_name, entity_name, entity_key, changed_at),
  FOREIGN KEY (hotel_name, changed_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  CHECK ((changed_by_user_phone IS NULL) <> (changed_by_system IS NULL)));
-- generic trigger audit_row(key_columns text[]): reads current_setting('leon.actor_user_phone', true) / ('leon.actor_system', true);
-- RAISE EXCEPTION when neither is set, so every writer must declare its actor.
DROP TABLE booking_modification_log;
```

**Backfill.** None (0 historical changes recorded). The A1b re-key and the D2 quarantine are logged with system actor `migration`.

**Code**
- **`access.set_actor(cur, hotel_name, user_phone=None, system=None)`** (A1b's module) runs `set_config('leon.actor_user_phone' | 'leon.actor_system', ..., true)`, which is transaction-local.
- **Where it is called:**
  - `_module_guard` hands `(hotel_name, user_phone)` to the request.
  - `content_store._with` / `_transaction` and the psycopg helpers in `booking_manager`, `front_desk_manager`, `room_manager`, `inventory_manager`, `housekeeping_manager`, `maintenance_manager`, `payment_manager` and `guest_manager` call `set_actor` from a request-context variable before the first write.
  - System actors: `aiosell_reservation_webhook` / `channel_reservations` (`channel_aiosell`), `night_audit.py` (`night_audit`), `maintenance_manager.run_daily_pm_generation` and the kite pushes (`scheduler`), and migration scripts (`migration`).
- **Install the audit trigger per table** only once every writer of that table sets an actor. Start with `room_bookings`, `booking_room_night`, `room_number_assignments`, `hotel_user`, `hotel_user_module`, `role_module` and `staff`, then every new core table.
- `activity_logs` (the `main._activity_log_middleware` documents) records `user_phone` instead of `manager_name`. It stays a request log; `audit_log` is the change record.

**Recipes.** None. A user recipe can read `audit_log` (for example "who moved room 103"). The `audit_log` access point is setup at `view`, plus the module of the entity, so a front-desk user can't read HR changes.

**Verify.**
- After a scripted desk session for each seeded role, `audit_log` has rows whose `changed_by_user_phone` is that user.
- A writer that doesn't set an actor fails in test.
- A role change in A1b appears in `audit_log` with the GENERAL_MANAGER as actor.

**Rollback.** Drop the triggers (the table is harmless). **Risk.** Medium to high: a writer path missed by `set_actor` raises in production. Mitigate with the per-table rollout and a test that exercises every tool. **Effort.** 1.5–2 days (was 2–3; roles moved to A1b).

### C3: Financial core: transaction codes, tax, accounts, channels, folios, transactions (5–7 days)

**Goal.** One transaction model for every department. Each row is a charge, tax, payment, adjustment, transfer or commission line on a **folio**, with a `business_date`, currency and the actor who posted it. Transaction codes map to GL accounts in FIN. This replaces `payment_tracking` (a stored total) and `payment_ledger` (payments only).

```sql
CREATE TABLE transaction_code (
  hotel_name       VARCHAR(255) NOT NULL,
  transaction_code VARCHAR(32)  NOT NULL CHECK (transaction_code = UPPER(btrim(transaction_code))),
  transaction_type VARCHAR(16)  NOT NULL CHECK (transaction_type IN ('charge','tax','payment','adjustment','transfer','commission')),
  department_name  VARCHAR(64)  NOT NULL,
  gl_account_code  VARCHAR(32),                              -- FK added in FIN
  status           VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, transaction_code),
  UNIQUE (hotel_name, transaction_code, transaction_type),
  FOREIGN KEY (hotel_name, department_name) REFERENCES department ON UPDATE CASCADE);
CREATE TABLE tax_code (
  hotel_name           VARCHAR(255) NOT NULL,
  tax_code             VARCHAR(32)  NOT NULL,
  tax_transaction_code VARCHAR(32)  NOT NULL,
  tax_type             VARCHAR(16)  NOT NULL DEFAULT 'tax',
  PRIMARY KEY (hotel_name, tax_code),
  FOREIGN KEY (hotel_name, tax_transaction_code, tax_type) REFERENCES transaction_code (hotel_name, transaction_code, transaction_type),
  CHECK (tax_type = 'tax'));
CREATE TABLE tax_rule (
  hotel_name      VARCHAR(255) NOT NULL,
  tax_code        VARCHAR(32)  NOT NULL,
  effective_from  DATE         NOT NULL,
  rate_percent    NUMERIC(7,4),
  amount_per_unit NUMERIC(12,2),
  unit            VARCHAR(16)  CHECK (unit IN ('night','person_night','item')),
  PRIMARY KEY (hotel_name, tax_code, effective_from),
  FOREIGN KEY (hotel_name, tax_code) REFERENCES tax_code ON UPDATE CASCADE,
  CHECK ((rate_percent IS NULL) <> (amount_per_unit IS NULL)),
  CHECK ((amount_per_unit IS NULL) = (unit IS NULL)));
CREATE TABLE transaction_code_tax (
  hotel_name       VARCHAR(255) NOT NULL,
  transaction_code VARCHAR(32)  NOT NULL,
  tax_code         VARCHAR(32)  NOT NULL,
  PRIMARY KEY (hotel_name, transaction_code, tax_code),
  FOREIGN KEY (hotel_name, transaction_code) REFERENCES transaction_code ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, tax_code) REFERENCES tax_code ON UPDATE CASCADE);
CREATE TABLE ledger_account (                                 -- city ledger: companies, travel agents, channels
  hotel_name         VARCHAR(255) NOT NULL,
  account_name       VARCHAR(128) NOT NULL CHECK (account_name = UPPER(btrim(account_name))),
  account_type       VARCHAR(16)  NOT NULL CHECK (account_type IN ('company','travel_agent','channel')),
  credit_limit       NUMERIC(12,2),
  payment_terms_days SMALLINT,
  currency           CHAR(3)      NOT NULL,
  status             VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, account_name));                  -- contacts and addresses go in 'ledger_account_profile' documents
CREATE TABLE channel (
  hotel_name         VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  channel_name       VARCHAR(64)  NOT NULL CHECK (channel_name = UPPER(btrim(channel_name))),
  account_name       VARCHAR(128),                          -- set for channels that collect and pay the hotel
  commission_percent NUMERIC(7,4),
  PRIMARY KEY (hotel_name, channel_name),
  FOREIGN KEY (hotel_name, account_name) REFERENCES ledger_account ON UPDATE CASCADE);
ALTER TABLE room_bookings ADD FOREIGN KEY (hotel_name, channel) REFERENCES channel (hotel_name, channel_name) ON UPDATE CASCADE;

CREATE TABLE folio (
  hotel_name            VARCHAR(255) NOT NULL,
  owner_type            VARCHAR(8)   NOT NULL CHECK (owner_type IN ('booking','guest','account','outlet')),
  owner                 VARCHAR(255) NOT NULL,              -- booking_id / guest_phone / account_name / outlet_name
  folio_window          SMALLINT     NOT NULL CHECK (folio_window > 0),
  booking_id            VARCHAR(255) GENERATED ALWAYS AS (CASE WHEN owner_type='booking' THEN owner END) STORED,
  guest_phone           VARCHAR(16)  GENERATED ALWAYS AS (CASE WHEN owner_type='guest'   THEN owner END) STORED,
  account_name          VARCHAR(128) GENERATED ALWAYS AS (CASE WHEN owner_type='account' THEN owner END) STORED,
  outlet_name           VARCHAR(64)  GENERATED ALWAYS AS (CASE WHEN owner_type='outlet'  THEN owner END) STORED,
  payer_guest_phone     VARCHAR(16),                       -- who pays this window (split by window)
  currency              CHAR(3)      NOT NULL,             -- always hotel currency (D6)
  status                VARCHAR(8)   NOT NULL CHECK (status IN ('open','settled','closed')),
  opened_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
  settled_business_date DATE,
  PRIMARY KEY (hotel_name, owner_type, owner, folio_window),
  UNIQUE (hotel_name, owner_type, owner, folio_window, currency),
  FOREIGN KEY (hotel_name, booking_id)        REFERENCES room_bookings ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, guest_phone)       REFERENCES guest_profile ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, account_name)      REFERENCES ledger_account ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, outlet_name)       REFERENCES outlet ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, payer_guest_phone) REFERENCES guest_profile ON UPDATE CASCADE);

CREATE TABLE folio_transaction (
  hotel_name            VARCHAR(255) NOT NULL,
  owner_type            VARCHAR(8)   NOT NULL,
  owner                 VARCHAR(255) NOT NULL,
  folio_window          SMALLINT     NOT NULL,
  txn_seq               INTEGER      NOT NULL CHECK (txn_seq > 0),   -- ordinal within the folio, printed on the folio
  business_date         DATE         NOT NULL,                       -- FK to business_day added in C4
  transaction_code      VARCHAR(32)  NOT NULL,
  transaction_type      VARCHAR(16)  NOT NULL,
  quantity              NUMERIC(10,2) NOT NULL CHECK (quantity <> 0),
  amount                NUMERIC(12,2) NOT NULL,                      -- + debit to the folio, - credit
  currency              CHAR(3)      NOT NULL,
  outlet_name           VARCHAR(64)  NOT NULL,
  booking_id            VARCHAR(255) GENERATED ALWAYS AS (CASE WHEN owner_type='booking' THEN owner END) STORED,
  stay_date             DATE,                                        -- room-night charges
  room_seq              SMALLINT,
  parent_txn_seq        INTEGER,                                     -- tax line -> its charge; adjustment -> original
  transfer_owner_type   VARCHAR(8),
  transfer_owner        VARCHAR(255),
  transfer_window       SMALLINT,
  transfer_txn_seq      INTEGER,                                     -- counterpart row (routing, split, direct bill)
  reference             VARCHAR(128),                                -- check number, card auth, channel ref
  posted_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
  posted_by_user_phone VARCHAR(16),
  posted_by_system      VARCHAR(32),
  PRIMARY KEY (hotel_name, owner_type, owner, folio_window, txn_seq),
  FOREIGN KEY (hotel_name, owner_type, owner, folio_window, currency) REFERENCES folio (hotel_name, owner_type, owner, folio_window, currency),
  FOREIGN KEY (hotel_name, transaction_code, transaction_type) REFERENCES transaction_code (hotel_name, transaction_code, transaction_type),
  FOREIGN KEY (hotel_name, outlet_name) REFERENCES outlet ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, booking_id, stay_date, room_seq) REFERENCES booking_room_night,
  FOREIGN KEY (hotel_name, owner_type, owner, folio_window, parent_txn_seq) REFERENCES folio_transaction,
  FOREIGN KEY (hotel_name, transfer_owner_type, transfer_owner, transfer_window, transfer_txn_seq) REFERENCES folio_transaction DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (hotel_name, posted_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  CHECK ((posted_by_user_phone IS NULL) <> (posted_by_system IS NULL)),
  CHECK (CASE transaction_type WHEN 'charge' THEN amount >= 0 WHEN 'tax' THEN amount >= 0
                               WHEN 'payment' THEN amount <= 0 WHEN 'commission' THEN amount <= 0 ELSE true END),
  CHECK ((transaction_type = 'transfer') = (transfer_txn_seq IS NOT NULL)));
CREATE UNIQUE INDEX ux_night_charge_once ON folio_transaction (hotel_name, booking_id, stay_date, room_seq, transaction_code)
  WHERE transaction_type = 'charge' AND stay_date IS NOT NULL;
-- trigger: forbid UPDATE / DELETE on folio_transaction (corrections are 'adjustment' rows with parent_txn_seq)
CREATE TABLE routing_rule (
  hotel_name          VARCHAR(255) NOT NULL,
  booking_id          VARCHAR(255) NOT NULL,
  transaction_code    VARCHAR(32)  NOT NULL,
  target_owner_type   VARCHAR(8)   NOT NULL,
  target_owner        VARCHAR(255) NOT NULL,
  target_folio_window SMALLINT     NOT NULL,
  PRIMARY KEY (hotel_name, booking_id, transaction_code),
  FOREIGN KEY (hotel_name, booking_id) REFERENCES room_bookings ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, transaction_code) REFERENCES transaction_code ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, target_owner_type, target_owner, target_folio_window) REFERENCES folio);
CREATE VIEW folio_balance AS
  SELECT hotel_name, owner_type, owner, folio_window, currency, SUM(amount) AS balance,
         SUM(amount) FILTER (WHERE transaction_type IN ('charge','tax','adjustment')) AS charges,
         -SUM(amount) FILTER (WHERE transaction_type = 'payment') AS payments
  FROM folio_transaction GROUP BY 1,2,3,4,5;
```

**Operation rules** (implemented in the new `folio_manager.py`; every rule is explicit):

| Operation | How it works |
|---|---|
| **Post** | Resolve the target folio: the `routing_rule` for (booking, code) if one exists, otherwise window 1, the defined primary window. Allocate `txn_seq` under `SELECT … FOR UPDATE` on the folio row. Add one tax row per `transaction_code_tax` × the effective `tax_rule`. A taxable code with no effective rule raises (D12: codes are seeded non-taxable). |
| **Split** | A transfer of part of an amount to another window: two transfer rows referencing each other. |
| **Routing** | `routing_rule` rows (for example ROOM to the company account window). |
| **Settlement** | Payments until `balance = 0`, or a transfer to an account folio (direct bill). Check-out requires every window of the booking to be at 0, then sets `status='settled'` and `settled_business_date`. |
| **Deposits** | Payments posted to the booking folio before arrival. The deposit ledger is a report over the folios of bookings not yet arrived. |
| **Commission** | A `commission` row on the channel's account folio at check-out, from `room_bookings.commission_amount` or `channel.commission_percent`. If neither exists, it raises an exception item; it never posts 0. |

**Backfill**
- **Per hotel, about 15 `transaction_code` rows (about 105 in total):**
  - charges: ROOM, ROOM_ADJ, LATE_CO, NO_SHOW_FEE, CANCEL_FEE, MISC
  - tax: VAT, SERVICE_CHARGE, TOURISM_TAX
  - payments: CASH, CARD, BANK_TRANSFER, CHANNEL_COLLECT
  - REFUND (payment), TRANSFER, COMMISSION
  - `tax_code` rows: 3 per hotel. `tax_rule` and `transaction_code_tax`: 0 rows (D12).
- **`channel`:** JACKHOTEL has 5 (DIRECT, PMS, AIOSELL BE, SAMPLE-OTA, BOOKING.COM), plus the distinct `room_bookings.channel` values of the other hotels.
- **`ledger_account`:** one per collecting OTA channel (BOOKING.COM, AIOSELL BE, SAMPLE-OTA at JACKHOTEL), in hotel currency.
- **`folio`:** window 1 per booking, giving **98** (95 after D2), plus 1 `outlet` folio per hotel for ROOMS (7). Currency is the hotel currency.
- **`folio_transaction`:** the 2 `payment_ledger` rows become CASH −120 and CARD −80 (D9), with `business_date` = the hotel-local date of `payment_date` and `posted_by_system='migration'`. **No historical charges are backfilled.** History revenue stays as booking value (A6), and charges post from the night-audit go-live (C4), so pre-go-live folios show payments only.

**Code**
- **Cut over (dual-write first):**
  - `payment_manager`: `record_payment`, `record_refund`, `read_payment_status`, `read_payment_ledger`, `check_payment_deadlines`; delete `_recompute_payment_tracking`.
  - `main.payments_ledger`, `payments_refund`; `desk_actions._check_refund`, `_rows_refunds`, `_perform_refund`.
  - `front_desk_manager.override_pricing_internal` (becomes an adjustment), `update_booking_detail_internal`, `verify_booking_phone`, `update_guest_check_out_status_internal` (settlement rule), `bulk_checkout_*`.
  - `booking_manager.create_booking` / `create_group_booking` / `_rebuild_booking_same_id` open the folio; `get_payment_status_for_booking` reads `folio_balance`.
  - `aiosell_reservation._apply_locked` / `run` open the folio and mark channel-collect.
  - `guest_manager.guest_booking_totals`, `read_guest_with_bookings`; `guest_summary._facts`; the UI payment panels in `desk.js` / `guests.js`.
- **Legacy tables:** `payment_tracking` becomes a compatibility view (booking_id, hotel_name, guest_phone_number, total_amount = charges, amount_paid = payments, remaining_balance, payment_status, currency) until readers move. Then drop the view, `payment_tracking_legacy` and `payment_ledger`.

**Recipes.** `daily_outstanding_balance` reads `folio_balance` joined to `booking_summary`. Revenue recipes still use A6 until R1 adds posted revenue. User recipes: none.

**Verify.**

```sql
SELECT SUM(-amount) FROM folio_transaction WHERE transaction_type='payment';   -- 200 = old ledger
SELECT COUNT(*) FROM room_bookings b WHERE NOT EXISTS (SELECT 1 FROM folio f WHERE f.hotel_name=b.hotel_name AND f.booking_id=b.booking_id);  -- 0
```

Also run a scripted post → split → route → settle → refund scenario and check that the balances match.

**Rollback.** Before the drop, revert to `payment_ledger` / `payment_tracking`, which are dual-written. **Risk.** **High** (money correctness, immutability, `txn_seq` contention).

### C4: Business date and night audit (4–5 days)

**Goal.** A hotel business date, independent of the calendar, that all postings and reports use, plus an automated night audit that posts room and tax, marks no-shows, lists exceptions, writes snapshots (R1) and rolls the date.

```sql
CREATE TABLE business_day (
  hotel_name            VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  business_date         DATE         NOT NULL,
  status                VARCHAR(8)   NOT NULL CHECK (status IN ('open','closing','closed')),
  opened_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
  closed_at             TIMESTAMPTZ,
  closed_by_user_phone VARCHAR(16),
  closed_by_system      VARCHAR(32),
  PRIMARY KEY (hotel_name, business_date),
  FOREIGN KEY (hotel_name, closed_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  CHECK (status <> 'closed' OR (closed_by_user_phone IS NULL) <> (closed_by_system IS NULL)));
CREATE UNIQUE INDEX one_open_business_day ON business_day (hotel_name) WHERE status <> 'closed';
CREATE TABLE night_audit_exception (
  hotel_name             VARCHAR(255) NOT NULL,
  business_date          DATE         NOT NULL,
  exception_type         VARCHAR(32)  NOT NULL CHECK (exception_type IN ('unrated_night','currency_unknown','currency_rate_missing',
                           'departure_open_balance','overstay','occupied_out_of_order','channel_date_mismatch','commission_unknown')),
  entity_key             TEXT         NOT NULL,
  resolved_at            TIMESTAMPTZ,
  resolved_by_user_phone VARCHAR(16),
  PRIMARY KEY (hotel_name, business_date, exception_type, entity_key),
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day,
  FOREIGN KEY (hotel_name, resolved_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE);
ALTER TABLE folio_transaction ADD FOREIGN KEY (hotel_name, business_date) REFERENCES business_day;
-- trigger: INSERT into folio_transaction requires business_day.status = 'open'
-- ('closing' only when posted_by_system = 'night_audit')
```

**Night audit steps** (new `booking_system/night_audit.py`, run at `hotel.night_audit_time` local by the `main.py` scheduler or the `workers/pms.js` cron calling `/night-audit`, or manually with `night_audit.close`):
1. Set the day to `closing`.
2. **No-shows.** First nights of the business date still `booked` (not checked in) become `no_show`, and `room_bookings.no_show_at` is set. The fee posts from the P1 policy when one exists.
3. **Room charges.** Each `checked_in` night of the date posts ROOM = `sell_rate` when its currency equals the folio currency. A foreign-currency rate converts with `currency_rate` (FIN); a missing rate becomes `currency_rate_missing`. Unrated nights become `unrated_night`, unknown currency becomes `currency_unknown` (D6). Package splits come from P1, taxes from C3, and `ux_night_charge_once` makes reruns safe.
4. **Exceptions:** `departure_open_balance`, `overstay`, `occupied_out_of_order`, `channel_date_mismatch` (D1) and `commission_unknown`.
5. **Snapshots (R1),** in the same transaction.
6. Close the day and insert the next one as `open`.

**Backfill.** One open `business_day` per hotel for today's hotel-local date (**7 rows**). R1 creates closed history days.

**Code**
- New `night_audit.py`; `main.py` endpoint, scheduler and `startup_event`; `workers/pms.js` cron.
- New `business_date(hotel)` helper used by `folio_manager`, `payment_manager.record_payment`, `front_desk_manager` check-in/out, `desk_actions` (its today parameter), `leon_meta_orchestrator.read_hotel_local_time_tool` and `timezone_utils`.
- `bulk_checkout_past_internal` becomes part of the overstay handling.

**Recipes.** The `_PERIODS` anchor `CURRENT_DATE` becomes the open `business_date` (`SELECT business_date FROM business_day WHERE hotel_name=… AND status<>'closed'`). "Yesterday" is the last closed day.

**Verify.** Run 3 simulated days on a copy and check that `SUM(ROOM)` per day equals `SUM(sell_rate)` of the checked-in rated nights, that a rerun posts nothing, and that the exceptions are listed.

**Rollback.** Disable the scheduler; delete the open `business_day` rows (postings made through the API are reversed with adjustments).

**Risk.** **High** (automated money posting; every tool's "today" changes meaning).

---

## Part 5: PMS operations modules (C)

### P1: Rate plans, packages, cancellation / no-show / deposit policies (3–4 days)

```sql
CREATE TABLE cancellation_policy (
  hotel_name           VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  policy_name          VARCHAR(64)  NOT NULL CHECK (policy_name = UPPER(btrim(policy_name))),
  free_until_hours     SMALLINT     NOT NULL CHECK (free_until_hours >= 0),     -- hours before arrival
  cancel_penalty_basis VARCHAR(8)   NOT NULL CHECK (cancel_penalty_basis IN ('nights','percent','amount')),
  cancel_penalty_value NUMERIC(12,2) NOT NULL,
  no_show_basis        VARCHAR(8)   NOT NULL CHECK (no_show_basis IN ('nights','percent','amount')),
  no_show_value        NUMERIC(12,2) NOT NULL,
  deposit_basis        VARCHAR(8)   CHECK (deposit_basis IN ('nights','percent','amount')),
  deposit_value        NUMERIC(12,2),
  deposit_due_days     SMALLINT,
  PRIMARY KEY (hotel_name, policy_name),
  CHECK ((deposit_basis IS NULL) = (deposit_value IS NULL)));
CREATE TABLE rate_plan (
  hotel_name          VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  rate_plan           VARCHAR(64)  NOT NULL CHECK (rate_plan = UPPER(btrim(rate_plan))),
  price_basis         VARCHAR(8)   NOT NULL CHECK (price_basis IN ('base','fixed','channel')),   -- base = daily_pricing +/- offset
  offset_percent      NUMERIC(7,4),
  offset_amount       NUMERIC(12,2),
  meal_plan           VARCHAR(8)   CHECK (meal_plan IN ('RO','BB','HB','FB','AI')),
  cancellation_policy VARCHAR(64),
  min_stay            SMALLINT,
  status              VARCHAR(16)  NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, rate_plan),
  FOREIGN KEY (hotel_name, cancellation_policy) REFERENCES cancellation_policy (hotel_name, policy_name) ON UPDATE CASCADE);
CREATE TABLE rate_plan_room_type (
  hotel_name VARCHAR(255),
  rate_plan  VARCHAR(64),
  room_type  VARCHAR(255),
  PRIMARY KEY (hotel_name, rate_plan, room_type),
  FOREIGN KEY (hotel_name, rate_plan) REFERENCES rate_plan ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, room_type) REFERENCES room_type ON UPDATE CASCADE);
CREATE TABLE rate_plan_fixed_price (
  hotel_name VARCHAR(255),
  rate_plan  VARCHAR(64),
  room_type  VARCHAR(255),
  stay_date  DATE,
  price      NUMERIC(12,2) NOT NULL CHECK (price >= 0),
  PRIMARY KEY (hotel_name, rate_plan, room_type, stay_date),
  FOREIGN KEY (hotel_name, rate_plan, room_type) REFERENCES rate_plan_room_type ON DELETE CASCADE);
CREATE TABLE package_component (                  -- e.g. BB: BREAKFAST 30 per adult per night split out of the room rate
  hotel_name       VARCHAR(255),
  rate_plan        VARCHAR(64),
  transaction_code VARCHAR(32),
  amount           NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
  posting_rhythm   VARCHAR(8)    NOT NULL CHECK (posting_rhythm IN ('per_night','per_stay')),
  per_unit         VARCHAR(8)    NOT NULL CHECK (per_unit IN ('room','adult','child')),
  PRIMARY KEY (hotel_name, rate_plan, transaction_code),
  FOREIGN KEY (hotel_name, rate_plan) REFERENCES rate_plan ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, transaction_code) REFERENCES transaction_code ON UPDATE CASCADE);
CREATE TABLE channel_rate_mapping (
  hotel_code VARCHAR(64),
  rate_code  VARCHAR(64),
  hotel_name VARCHAR(255) NOT NULL,
  rate_plan  VARCHAR(64)  NOT NULL,
  PRIMARY KEY (hotel_code, rate_code),
  FOREIGN KEY (hotel_name, rate_plan) REFERENCES rate_plan ON UPDATE CASCADE);
ALTER TABLE booking_room_night ADD COLUMN rate_plan VARCHAR(64),
  ADD FOREIGN KEY (hotel_name, rate_plan) REFERENCES rate_plan ON UPDATE CASCADE;
ALTER TABLE room_bookings DROP COLUMN rate_plan;          -- NULL on 93 / 93; rate plan is per night
```

Marketing text and inclusions stay in `rate_plans` documents keyed `hotel_name|rate_plan`.

**Backfill (D13)**
- **`rate_plan`:** `BAR` (basis `base`) per hotel (7), plus one `channel`-basis plan per distinct OTA `rateplanCode` (for example `SUITE-D-CP`, `EXECUTIVE-S-EP`).
- **`channel_rate_mapping`:** from the same codes.
- **`booking_room_night.rate_plan`:** from the OTA `rooms[].rateplanCode` (93 bookings) and `BAR` for the 5 desk bookings.
- **`cancellation_policy`:** from the `booking_policies` documents (12 documents for 6 hotels; `payment_window_days` and `payment_threshold_percent` become the deposit fields). JACKHOTEL has none, so none is created; the desk shows "no cancellation policy".

**Code**
- `main.list_rate_plans`, `save_rate_plan`, `update_rate_plan`, `delete_rate_plan`, `_rate_plan_hotel`; the `front_desk_manager` rate plan lookup (around line 3740).
- `booking_manager.create_booking` / `create_group_booking`: price = rate plan rule, and the tax policy lookup is replaced by C3 tax rules.
- `policy_manager.create_policy`, `update_policy`, `delete_policy`, `read_policies`, `read_policies_structured`; `payment_manager.check_payment_deadlines` (deposit due).
- `aiosell_reservation._apply_locked` (rateplanCode mapping); `kite_pricing` / `aiosell_channel_manager` (rate push per plan).
- Night audit posts packages, no-show fees and cancellation fees (when a cancellation is made inside `free_until_hours`).

**Recipes.** Add a `rate_plan` dimension to the revenue facts (R2). User recipes: none.

**Verify.** Every OTA `rateplanCode` maps to a plan; a 2-night BB booking posts ROOM + BREAKFAST that sum to the sell rate.

**Rollback.** Drop the new tables and columns; the documents remain. **Risk.** Medium (price derivation for desk bookings).

### P2: Groups, room blocks, allotments (3 days)

```sql
CREATE TABLE group_block (
  hotel_name     VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  group_name     VARCHAR(128) NOT NULL CHECK (group_name = UPPER(btrim(group_name))),
  account_name   VARCHAR(128),
  rate_plan      VARCHAR(64),
  arrival_date   DATE         NOT NULL,
  departure_date DATE         NOT NULL,
  cutoff_date    DATE         NOT NULL,
  status         VARCHAR(16)  NOT NULL CHECK (status IN ('tentative','definite','released','cancelled')),
  PRIMARY KEY (hotel_name, group_name),
  CHECK (departure_date > arrival_date AND cutoff_date <= arrival_date),
  FOREIGN KEY (hotel_name, account_name) REFERENCES ledger_account ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, rate_plan) REFERENCES rate_plan ON UPDATE CASCADE);
CREATE TABLE group_block_night (
  hotel_name    VARCHAR(255),
  group_name    VARCHAR(128),
  room_type     VARCHAR(255),
  stay_date     DATE,
  rooms_blocked SMALLINT NOT NULL CHECK (rooms_blocked >= 0),
  PRIMARY KEY (hotel_name, group_name, room_type, stay_date),
  FOREIGN KEY (hotel_name, group_name) REFERENCES group_block ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, room_type, stay_date) REFERENCES room_inventory_nightly ON UPDATE CASCADE);
ALTER TABLE room_bookings ADD FOREIGN KEY (hotel_name, group_name) REFERENCES group_block ON UPDATE CASCADE;
ALTER TABLE room_allotments ADD FOREIGN KEY (hotel_name, channel) REFERENCES channel (hotel_name, channel_name) ON UPDATE CASCADE;
CREATE VIEW group_pickup AS
  SELECT g.hotel_name, g.group_name, g.room_type, g.stay_date, g.rooms_blocked, COUNT(n.*) AS rooms_picked_up
  FROM group_block_night g
  LEFT JOIN room_bookings b ON b.hotel_name = g.hotel_name AND b.group_name = g.group_name
  LEFT JOIN booking_room_night n ON n.hotel_name = b.hotel_name AND n.booking_id = b.booking_id
       AND n.room_type = g.room_type AND n.stay_date = g.stay_date AND n.night_status NOT IN ('cancelled','released')
  GROUP BY 1,2,3,4,5;
```

Sellable rooms = inventory − OOO − sold − (blocked − picked up), for definite blocks before cutoff. Blocks auto-release at cutoff (night audit). Allotments reduce channel availability until `release_date`.

**Backfill.** One `group_block` per distinct non-NULL `room_bookings.group_name` (run `SELECT COUNT(DISTINCT group_name)`). Dates come from their nights, with `status='definite'`, cutoff = arrival, and `rooms_blocked` = rooms picked up, so pickup is 100 %. `room_allotments`: 0 rows.

**Code.** `booking_manager.create_group_booking`, `main.api_bookings_group`, `inventory_manager.save_allotment`, `read_availability`, `aiosell_channel_manager._available_by_date_locked`, `kite_pricing` (occupancy input), and `night_audit` (block release).

**Recipes.** New general recipe `group_pickup` (block vs pickup by date). Occupancy recipes are unchanged (blocks aren't sold).

**Verify.** Availability equals inventory − OOO − sold − open block for 30 days; pickup is 100 % for the backfilled groups.

**Rollback.** Drop the tables and FKs. **Risk.** Medium (availability formula).

### P3: Late checkout, service requests, lost & found (2–3 days)

```sql
ALTER TABLE room_bookings ADD COLUMN expected_arrival_time TIME, ADD COLUMN expected_departure_time TIME;  -- NULL = standard time from hotel
CREATE TABLE service_request (
  hotel_name           VARCHAR(255) NOT NULL,
  location             VARCHAR(64)  NOT NULL,          -- room_number or area name
  request_type         VARCHAR(32)  NOT NULL CHECK (request_type IN ('amenity','housekeeping','maintenance','wake_up','transport','complaint','other')),
  requested_at         TIMESTAMPTZ  NOT NULL,
  department_name      VARCHAR(64)  NOT NULL,
  room_number          VARCHAR(32),
  booking_id           VARCHAR(255),
  guest_phone          VARCHAR(16),
  status               VARCHAR(16)  NOT NULL CHECK (status IN ('open','assigned','done','cancelled')),
  assigned_staff_phone VARCHAR(16),
  completed_at         TIMESTAMPTZ,
  PRIMARY KEY (hotel_name, location, request_type, requested_at),
  FOREIGN KEY (hotel_name, department_name) REFERENCES department ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, room_number) REFERENCES room ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, booking_id) REFERENCES room_bookings ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, guest_phone) REFERENCES guest_profile ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, assigned_staff_phone) REFERENCES staff ON UPDATE CASCADE);
CREATE TABLE lost_found_item (
  hotel_name          VARCHAR(255) NOT NULL,
  found_location      VARCHAR(64)  NOT NULL,
  found_at            TIMESTAMPTZ  NOT NULL,
  found_by_user_phone VARCHAR(16) NOT NULL,
  room_number         VARCHAR(32),
  status              VARCHAR(16)  NOT NULL CHECK (status IN ('stored','claimed','returned','disposed')),
  claimant_guest_phone VARCHAR(16),
  returned_at         TIMESTAMPTZ,
  disposed_at         TIMESTAMPTZ,
  PRIMARY KEY (hotel_name, found_location, found_at),
  FOREIGN KEY (hotel_name, found_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, room_number) REFERENCES room ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, claimant_guest_phone) REFERENCES guest_profile ON UPDATE CASCADE);
-- item description and photos go in 'lost_found' documents keyed hotel_name|found_location|found_at
DROP TABLE guest_request; DROP TABLE guest_complaint;     -- 0 rows each; replaced by service_request
```

Late checkout: an approved `expected_departure_time` later than `hotel.standard_checkout_time` posts LATE_CO (amount per the P1 rate-plan or hotel rule, entered by staff) to the folio.

**Code**
- `guest_manager` request and complaint writers (the `guest_request` / `guest_complaint` paths in `_ensure_guest_operations` and the related tools).
- `front_desk_manager.update_guest_check_out_status_internal` (late checkout fee and time).
- `housekeeping_manager` / `maintenance_manager` (requests become tasks).
- New `service_manager.py`; `concierge_manager` (moves off the empty `ella_hotel_encyclopedia` documents).

**Recipes.** New `service_request_sla` (open and late requests by department).

**Verify.** A scripted request → assign → done; a found → returned item.

**Rollback.** Drop the tables (the old ones are empty). **Risk.** Low.

### P4: City ledger operations and deposits (2–3 days)

**Goal.** Operate the accounts from C3: credit limits, direct bill at check-out, statements, aging and deposit tracking.

```sql
CREATE TABLE ar_statement (
  hotel_name      VARCHAR(255),
  account_name    VARCHAR(128),
  statement_date  DATE,
  due_date        DATE NOT NULL,
  opening_balance NUMERIC(12,2) NOT NULL,
  closing_balance NUMERIC(12,2) NOT NULL,
  currency        CHAR(3) NOT NULL,
  PRIMARY KEY (hotel_name, account_name, statement_date),
  FOREIGN KEY (hotel_name, account_name) REFERENCES ledger_account ON UPDATE CASCADE);
CREATE VIEW ar_aging AS
  SELECT t.hotel_name, t.owner AS account_name, t.currency,
         SUM(t.amount) FILTER (WHERE b.business_date - t.business_date <= 30) AS d0_30,
         SUM(t.amount) FILTER (WHERE b.business_date - t.business_date BETWEEN 31 AND 60) AS d31_60,
         SUM(t.amount) FILTER (WHERE b.business_date - t.business_date BETWEEN 61 AND 90) AS d61_90,
         SUM(t.amount) FILTER (WHERE b.business_date - t.business_date > 90) AS d90_plus
  FROM folio_transaction t
  JOIN business_day b ON b.hotel_name = t.hotel_name AND b.status <> 'closed'
  WHERE t.owner_type = 'account' GROUP BY 1,2,3;
CREATE VIEW deposit_ledger AS
  SELECT f.hotel_name, f.booking_id, s.check_in_date, -SUM(t.amount) AS deposit_held
  FROM folio f
  JOIN folio_transaction t USING (hotel_name, owner_type, owner, folio_window)
  JOIN booking_summary s ON s.hotel_name = f.hotel_name AND s.booking_id = f.booking_id
  JOIN business_day b ON b.hotel_name = f.hotel_name AND b.status <> 'closed'
  WHERE t.transaction_type = 'payment' AND s.check_in_date > b.business_date
  GROUP BY 1,2,3;
```

**Code**
- `folio_manager.transfer_to_account`: the direct-bill check-out checks `credit_limit` against the account balance.
- `payment_manager.check_payment_deadlines` checks the deposit due date from P1.
- New AR tools: statement, aging, record account payment.

**Recipes.** New `deposit_ledger` and `ar_aging` general recipes.

**Verify.** A scripted direct bill moves the folio balance into the account; aging totals equal the account balances.

**Rollback.** Drop the views and the table. **Risk.** Low to medium.

---

## Part 6: Department modules (D), each plugging into the core

**Common contract for any department module:**
- `department` / `outlet` / `cost_center` rows (C1).
- `transaction_code` rows for its revenue (C3).
- Postings only through `folio_manager.post` (room, account or outlet folio).
- Users by `(hotel_name, user_phone)` with a role and module levels (A1b); employees by `staff_phone` (A3, HR).
- **Each module registers itself:**
  - a `module` row (spa, events and laundry are pre-seeded)
  - `role_module` rows for every existing role (`none` except GENERAL_MANAGER `manage`)
  - `access_point` rows for its routes, tools and tables
  - module-tagged general recipes for its reports
  - optionally a new role, for example `SPA_THERAPIST`
- Business date from C4.
- Daily statistics written by night audit (R1).
- Content (menus, treatment descriptions, event documents) in documents.

### FIN: Finance (5–7 days)

```sql
CREATE TABLE gl_account (
  hotel_name          VARCHAR(255) NOT NULL REFERENCES hotel ON UPDATE CASCADE,
  gl_account_code     VARCHAR(32)  NOT NULL,
  gl_account_name     VARCHAR(128) NOT NULL,
  account_type        VARCHAR(16)  NOT NULL CHECK (account_type IN ('asset','liability','equity','revenue','expense')),
  parent_account_code VARCHAR(32),
  PRIMARY KEY (hotel_name, gl_account_code),
  FOREIGN KEY (hotel_name, parent_account_code) REFERENCES gl_account (hotel_name, gl_account_code));
CREATE TABLE gl_control (                          -- the control accounts the posting rules need, one row per hotel
  hotel_name               VARCHAR(255) PRIMARY KEY REFERENCES hotel ON UPDATE CASCADE,
  guest_ledger_account     VARCHAR(32) NOT NULL,
  ar_control_account       VARCHAR(32) NOT NULL,
  deposit_ledger_account   VARCHAR(32) NOT NULL,
  ap_control_account       VARCHAR(32) NOT NULL,
  payroll_payable_account  VARCHAR(32) NOT NULL,
  fx_difference_account    VARCHAR(32) NOT NULL
  -- each column has FK (hotel_name, col) REFERENCES gl_account
);
ALTER TABLE transaction_code ADD FOREIGN KEY (hotel_name, gl_account_code) REFERENCES gl_account ON UPDATE CASCADE;
ALTER TABLE transaction_code ALTER COLUMN gl_account_code SET NOT NULL;
CREATE TABLE currency_rate (
  hotel_name    VARCHAR(255),
  currency      CHAR(3),
  rate_date     DATE,
  rate_to_hotel NUMERIC(14,6) NOT NULL CHECK (rate_to_hotel > 0),
  PRIMARY KEY (hotel_name, currency, rate_date),
  FOREIGN KEY (hotel_name) REFERENCES hotel ON UPDATE CASCADE);
CREATE TABLE journal_line (
  hotel_name       VARCHAR(255) NOT NULL,
  business_date    DATE         NOT NULL,
  journal_name     VARCHAR(16)  NOT NULL CHECK (journal_name IN ('night_audit','ap','payroll','stock','manual')),
  line_seq         INTEGER      NOT NULL,
  gl_account_code  VARCHAR(32)  NOT NULL,
  cost_center_name VARCHAR(64)  NOT NULL,
  debit            NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
  credit           NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
  currency         CHAR(3)      NOT NULL,
  source_entity    VARCHAR(64),
  source_key       TEXT,
  PRIMARY KEY (hotel_name, business_date, journal_name, line_seq),
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day,
  FOREIGN KEY (hotel_name, gl_account_code) REFERENCES gl_account ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, cost_center_name) REFERENCES cost_center ON UPDATE CASCADE,
  CHECK ((debit = 0) <> (credit = 0)));
CREATE TABLE vendor (
  hotel_name  VARCHAR(255) REFERENCES hotel ON UPDATE CASCADE,
  vendor_name VARCHAR(128) CHECK (vendor_name = UPPER(btrim(vendor_name))),
  currency    CHAR(3) NOT NULL,
  PRIMARY KEY (hotel_name, vendor_name));           -- contacts go in 'vendor_profile' documents
CREATE TABLE vendor_invoice (
  hotel_name     VARCHAR(255),
  vendor_name    VARCHAR(128),
  invoice_number VARCHAR(64),
  invoice_date   DATE NOT NULL,
  due_date       DATE NOT NULL,
  business_date  DATE NOT NULL,
  currency       CHAR(3) NOT NULL,
  status         VARCHAR(16) NOT NULL CHECK (status IN ('entered','approved','paid','void')),
  PRIMARY KEY (hotel_name, vendor_name, invoice_number),
  FOREIGN KEY (hotel_name, vendor_name) REFERENCES vendor ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day);
CREATE TABLE vendor_invoice_line (
  hotel_name       VARCHAR(255),
  vendor_name      VARCHAR(128),
  invoice_number   VARCHAR(64),
  line_seq         SMALLINT,
  gl_account_code  VARCHAR(32) NOT NULL,
  cost_center_name VARCHAR(64) NOT NULL,
  amount           NUMERIC(12,2) NOT NULL,
  tax_code         VARCHAR(32),
  PRIMARY KEY (hotel_name, vendor_name, invoice_number, line_seq),
  FOREIGN KEY (hotel_name, vendor_name, invoice_number) REFERENCES vendor_invoice ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, gl_account_code) REFERENCES gl_account,
  FOREIGN KEY (hotel_name, cost_center_name) REFERENCES cost_center,
  FOREIGN KEY (hotel_name, tax_code) REFERENCES tax_code);
CREATE TABLE vendor_payment (
  hotel_name     VARCHAR(255),
  vendor_name    VARCHAR(128),
  invoice_number VARCHAR(64),
  paid_at        TIMESTAMPTZ,
  amount         NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  business_date  DATE NOT NULL,
  PRIMARY KEY (hotel_name, vendor_name, invoice_number, paid_at),
  FOREIGN KEY (hotel_name, vendor_name, invoice_number) REFERENCES vendor_invoice ON UPDATE CASCADE);
```

**Posting rules** (night audit close, one aggregated journal per day):

| Transaction | Debit | Credit |
|---|---|---|
| charge / tax on a booking or guest folio | guest ledger | `transaction_code.gl_account_code` (cost centre of the outlet) |
| charge on an account folio | AR control | the same revenue account |
| payment | the payment code's account (cash / bank / card clearing) | guest ledger, or the deposit ledger before arrival |
| transfer to an account | AR control | guest ledger |
| commission | commission expense | AR control |
| foreign currency | converted with `currency_rate`; a missing rate stops the close for that line with an exception, never a 1:1 default | |

The daily revenue close requires each journal to balance (Σ debit = Σ credit). Tax returns come from tax rows grouped by `tax_code`.

**Backfill**
- A USALI-lite chart of about 40 accounts per hotel (**~280**).
- `gl_control` (7).
- Map the ~105 transaction codes.
- `currency_rate`: 0 rows. The INR nights stay as `currency_rate_missing` until a rate is entered.

**Code.** New `finance_manager.py` (chart, AP, journals, close); `night_audit` close step; new UI page.

**Recipes.** Departmental P&L (R2). **Verify.** Every closed day's journals balance; revenue accounts equal `daily_revenue` (R1).

**Rollback.** Drop the FIN tables; `transaction_code.gl_account_code` becomes nullable again. **Risk.** Medium (mapping correctness; the close blocks on missing FX rates, which is intentional).

### HR: Human resources (5–7 days)

```sql
CREATE TABLE position (
  hotel_name      VARCHAR(255),
  position_name   VARCHAR(64) CHECK (position_name = UPPER(btrim(position_name))),
  department_name VARCHAR(64) NOT NULL,
  PRIMARY KEY (hotel_name, position_name),
  FOREIGN KEY (hotel_name, department_name) REFERENCES department ON UPDATE CASCADE);
ALTER TABLE staff ADD COLUMN position_name VARCHAR(64),
  ADD COLUMN employment_type VARCHAR(16) CHECK (employment_type IN ('full_time','part_time','casual','contract')),
  ADD COLUMN hired_on DATE, ADD COLUMN terminated_on DATE,
  ADD FOREIGN KEY (hotel_name, position_name) REFERENCES position ON UPDATE CASCADE;
CREATE TABLE pay_rate (                            -- access_point table: hr, manage only
  hotel_name     VARCHAR(255),
  staff_phone    VARCHAR(16),
  effective_from DATE,
  pay_basis      VARCHAR(8) NOT NULL CHECK (pay_basis IN ('monthly','hourly')),
  amount         NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  currency       CHAR(3) NOT NULL,
  PRIMARY KEY (hotel_name, staff_phone, effective_from),
  FOREIGN KEY (hotel_name, staff_phone) REFERENCES staff ON UPDATE CASCADE);
CREATE TABLE shift (
  hotel_name VARCHAR(255) REFERENCES hotel ON UPDATE CASCADE,
  shift_name VARCHAR(32),
  starts_at  TIME NOT NULL,
  ends_at    TIME NOT NULL,
  PRIMARY KEY (hotel_name, shift_name));
CREATE TABLE roster (
  hotel_name      VARCHAR(255),
  staff_phone     VARCHAR(16),
  work_date       DATE,
  shift_name      VARCHAR(32),
  department_name VARCHAR(64) NOT NULL,
  PRIMARY KEY (hotel_name, staff_phone, work_date, shift_name),
  FOREIGN KEY (hotel_name, staff_phone) REFERENCES staff ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, shift_name) REFERENCES shift ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, department_name) REFERENCES department ON UPDATE CASCADE);
ALTER TABLE staff_duty_log ADD COLUMN work_date DATE, ADD COLUMN shift_name VARCHAR(32),
  ADD FOREIGN KEY (hotel_name, staff_phone, work_date, shift_name) REFERENCES roster;   -- attendance against the roster; NULL = unrostered (listed)
CREATE TABLE leave_type (
  hotel_name  VARCHAR(255) REFERENCES hotel ON UPDATE CASCADE,
  leave_type  VARCHAR(32),
  annual_days NUMERIC(5,1) NOT NULL,
  paid        BOOLEAN NOT NULL,
  PRIMARY KEY (hotel_name, leave_type));
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE leave_request (
  hotel_name              VARCHAR(255),
  staff_phone             VARCHAR(16),
  start_date              DATE,
  end_date                DATE NOT NULL,
  leave_type              VARCHAR(32) NOT NULL,
  status                  VARCHAR(16) NOT NULL CHECK (status IN ('requested','approved','rejected','cancelled')),
  approved_by_user_phone VARCHAR(16),
  PRIMARY KEY (hotel_name, staff_phone, start_date),
  CHECK (end_date >= start_date),
  FOREIGN KEY (hotel_name, staff_phone) REFERENCES staff ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, leave_type) REFERENCES leave_type ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, approved_by_user_phone) REFERENCES hotel_user ON UPDATE CASCADE,
  EXCLUDE USING gist (hotel_name WITH =, staff_phone WITH =, daterange(start_date, end_date, '[]') WITH &&) WHERE (status IN ('requested','approved')));
CREATE TABLE payroll_period (
  hotel_name   VARCHAR(255) REFERENCES hotel ON UPDATE CASCADE,
  period_start DATE,
  period_end   DATE NOT NULL,
  status       VARCHAR(16) NOT NULL CHECK (status IN ('open','calculated','posted')),
  PRIMARY KEY (hotel_name, period_start));
CREATE TABLE payroll_line (                        -- hook: amounts come from the payroll provider or a calculation; posted to FIN 'payroll'
  hotel_name       VARCHAR(255),
  period_start     DATE,
  staff_phone      VARCHAR(16),
  pay_component    VARCHAR(24) CHECK (pay_component IN ('base','overtime','service_charge_share','allowance','deduction','employer_contribution')),
  amount           NUMERIC(12,2) NOT NULL,
  currency         CHAR(3) NOT NULL,
  cost_center_name VARCHAR(64) NOT NULL,
  PRIMARY KEY (hotel_name, period_start, staff_phone, pay_component),
  FOREIGN KEY (hotel_name, period_start) REFERENCES payroll_period ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, staff_phone) REFERENCES staff ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, cost_center_name) REFERENCES cost_center ON UPDATE CASCADE);
```

Personal content (address, ID, emergency contact, bank details) goes in `staff_profiles` documents keyed `hotel_name|staff_phone`, readable only at hr `manage` (A1b access point).

**Task assignment rule.** Assigning a cleaning, maintenance or service request requires the staff member to be rostered for that date, in the task's department, and not on approved leave. Otherwise the assignment fails with the reason; there is no auto-pick.

**Backfill**
- **`position`:** HOTEL MANAGER and ASSISTANT MANAGER per hotel (D8); `staff.position_name` = role.
- **`shift`:** MORNING 07–15, EVENING 15–23 and NIGHT 23–07 per hotel (**21**, explicit seed).
- **`leave_type`:** ANNUAL, SICK and UNPAID per hotel (**21**).
- Everything else: 0.

**Code**
- `staff_manager` (all functions), `housekeeping_manager.staff_clock_in` / `staff_clock_out` / `get_on_duty_staff_json` / `get_staff_workload_json` and the assign paths, the `maintenance_manager` assignment, `service_manager` assignment.
- New `hr_manager.py`; HR UI.

**Recipes.** Payroll cost and labour hours (R2).

**Verify.** Assigning to an unrostered staff member is rejected; overlapping leave is rejected; a payroll period posts a balanced journal.

**Rollback.** Drop the HR tables and columns. **Risk.** Medium (PII and pay access depend on A1b's hr access points).

### FB-1: F&B outlets, menu, checks, posting (4–5 days)

```sql
-- outlets: rows in outlet with outlet_type restaurant / bar / room_service (C1)
CREATE TABLE menu_item (
  hotel_name       VARCHAR(255),
  outlet_name      VARCHAR(64),
  item_name        VARCHAR(128) CHECK (item_name = UPPER(btrim(item_name))),
  transaction_code VARCHAR(32) NOT NULL,     -- e.g. FB_FOOD, FB_BEVERAGE
  price            NUMERIC(12,2) NOT NULL CHECK (price >= 0),
  status           VARCHAR(16) NOT NULL DEFAULT 'active',
  PRIMARY KEY (hotel_name, outlet_name, item_name),
  FOREIGN KEY (hotel_name, outlet_name) REFERENCES outlet ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, transaction_code) REFERENCES transaction_code ON UPDATE CASCADE);
-- description, allergens and photos go in 'menu_item' documents keyed hotel_name|outlet_name|item_name
CREATE TABLE pos_check (
  hotel_name         VARCHAR(255),
  outlet_name        VARCHAR(64),
  business_date      DATE,
  check_number       INTEGER CHECK (check_number > 0),    -- printed on the check, per outlet per business date
  opened_at          TIMESTAMPTZ NOT NULL,
  closed_at          TIMESTAMPTZ,
  covers             SMALLINT NOT NULL CHECK (covers > 0),
  table_name         VARCHAR(16),
  server_staff_phone VARCHAR(16) NOT NULL,
  status             VARCHAR(8) NOT NULL CHECK (status IN ('open','closed','void')),
  settlement         VARCHAR(8) CHECK (settlement IN ('room','direct','account')),
  booking_id         VARCHAR(255),
  room_number        VARCHAR(32),
  account_name       VARCHAR(128),
  PRIMARY KEY (hotel_name, outlet_name, business_date, check_number),
  FOREIGN KEY (hotel_name, outlet_name) REFERENCES outlet ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day,
  FOREIGN KEY (hotel_name, server_staff_phone) REFERENCES staff ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, booking_id) REFERENCES room_bookings ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, room_number) REFERENCES room ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, account_name) REFERENCES ledger_account ON UPDATE CASCADE,
  CHECK ((status = 'closed') = (settlement IS NOT NULL)),
  CHECK ((settlement = 'room') = (booking_id IS NOT NULL AND room_number IS NOT NULL)),
  CHECK ((settlement = 'account') = (account_name IS NOT NULL)));
CREATE TABLE pos_check_line (
  hotel_name      VARCHAR(255),
  outlet_name     VARCHAR(64),
  business_date   DATE,
  check_number    INTEGER,
  line_seq        SMALLINT,
  item_name       VARCHAR(128) NOT NULL,
  quantity        NUMERIC(8,2) NOT NULL CHECK (quantity > 0),
  unit_price      NUMERIC(12,2) NOT NULL,
  discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
  voided          BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (hotel_name, outlet_name, business_date, check_number, line_seq),
  FOREIGN KEY (hotel_name, outlet_name, business_date, check_number) REFERENCES pos_check ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, outlet_name, item_name) REFERENCES menu_item ON UPDATE CASCADE);
```

**Settlement on close** (`folio_manager.post`, with one charge row per transaction code plus tax; `reference = outlet/business_date/check_number`):

| Settlement | Posting |
|---|---|
| **room** | The guest's booking folio, honouring routing rules. Rejected if the booking isn't in-house on the business date. |
| **direct** | Charges and the payment on the outlet folio (`owner_type='outlet'`), netting to 0. |
| **account** | The city-ledger account folio, subject to the credit limit. |

**Backfill.** None (no F&B data). For testing only, create a `RESTAURANT` outlet for JACKHOTEL and FB_FOOD / FB_BEVERAGE codes for each hotel.

**Code.** New `fnb_manager.py` (menu CRUD, open / add / close check, settle); `main.py` endpoints; F&B UI; the night audit lists open checks as exceptions.

**Recipes.** `fb_covers` (R2). **Verify.** Room-charged checks appear on the folio with the same totals; direct-check outlet folios net to 0 per day.

**Rollback.** Drop the tables (folio rows stay as posted history). **Risk.** Medium.

### FB-2: Stock and recipe cost (3–4 days)

```sql
CREATE TABLE store (
  hotel_name       VARCHAR(255) REFERENCES hotel ON UPDATE CASCADE,
  store_name       VARCHAR(64),
  cost_center_name VARCHAR(64) NOT NULL,
  PRIMARY KEY (hotel_name, store_name),
  FOREIGN KEY (hotel_name, cost_center_name) REFERENCES cost_center);
CREATE TABLE stock_item (
  hotel_name VARCHAR(255) REFERENCES hotel ON UPDATE CASCADE,
  stock_item VARCHAR(128),
  unit       VARCHAR(16) NOT NULL,
  PRIMARY KEY (hotel_name, stock_item));
CREATE TABLE recipe_line (
  hotel_name  VARCHAR(255),
  outlet_name VARCHAR(64),
  item_name   VARCHAR(128),
  stock_item  VARCHAR(128),
  quantity    NUMERIC(10,4) NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (hotel_name, outlet_name, item_name, stock_item),
  FOREIGN KEY (hotel_name, outlet_name, item_name) REFERENCES menu_item ON UPDATE CASCADE ON DELETE CASCADE,
  FOREIGN KEY (hotel_name, stock_item) REFERENCES stock_item ON UPDATE CASCADE);
CREATE TABLE stock_movement (
  hotel_name        VARCHAR(255),
  store_name        VARCHAR(64),
  stock_item        VARCHAR(128),
  moved_at          TIMESTAMPTZ,
  movement_type     VARCHAR(16) NOT NULL CHECK (movement_type IN ('purchase','issue','transfer','waste','count_adjustment')),
  quantity          NUMERIC(12,4) NOT NULL,
  unit_cost         NUMERIC(12,4),
  currency          CHAR(3),
  business_date     DATE NOT NULL,
  vendor_name       VARCHAR(128),
  vendor_invoice_number VARCHAR(64),
  PRIMARY KEY (hotel_name, store_name, stock_item, moved_at),
  FOREIGN KEY (hotel_name, store_name) REFERENCES store ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, stock_item) REFERENCES stock_item ON UPDATE CASCADE,
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day,
  FOREIGN KEY (hotel_name, vendor_name, vendor_invoice_number) REFERENCES vendor_invoice,
  CHECK ((movement_type = 'purchase') = (unit_cost IS NOT NULL)));
```

Views:
- `stock_on_hand` (Σ quantity by store and item).
- `theoretical_food_cost` (check lines × `recipe_line` × latest purchase `unit_cost`).
- `actual_food_cost` (issues + waste valued at the latest cost); the variance between them is a report.

**Code.** `fnb_manager` stock functions; FIN AP links; night audit stock journal (`journal_name='stock'`).

**Verify.** Stock on hand after a purchase → issue → count reconciles. **Rollback.** Drop the tables. **Risk.** Low to medium.

### GEN: Spa, Events / Banquets, Laundry (3–5 days each)

Each module follows the same generic pattern:
1. Add `department` and `outlet` rows (`spa`, `banquet`, `laundry`).
2. Add a **service catalogue** table keyed `(hotel_name, outlet_name, service_name)`, with `transaction_code`, `price` and `duration_minutes`. Descriptions go in documents.
3. Add **one operational table** with a natural time-based key, plus lines if needed:
   - **Spa:** `spa_appointment (hotel_name, outlet_name, therapist_staff_phone, starts_at)`, with `service_name`, `ends_at`, `booking_id` / `guest_phone`, `status`, and `EXCLUDE USING gist (hotel_name =, therapist_staff_phone =, tstzrange(starts_at, ends_at) &&)`.
   - **Events:** `function_space (hotel_name, space_name)`; `event (hotel_name, event_name, start_date)` with `account_name` (city ledger) and an optional `group_name` (P2); `event_function (hotel_name, event_name, start_date, space_name, starts_at)` with `ends_at`, `expected_covers`, `guaranteed_covers` and the same gist exclusion per space. The banquet event order (BEO) is a document.
   - **Laundry:** `laundry_order (hotel_name, room_number, collected_at)` with `booking_id`, `status`, plus `laundry_order_line (…, line_seq, service_name, quantity, unit_price)`.
4. **Settlement** through `folio_manager.post`: room, direct (outlet folio) or account.
5. **Staff assignment** through HR roster checks.
6. **Daily statistics** in `daily_outlet_stats` (R1). **GL** through the transaction-code mapping (FIN).

Verification, rollback and risk mirror FB-1. Effort is 3–5 days each.

### SET: Hotel operating settings, versioned (done)

Operational choices belong to each hotel and are captured from the hotel's own answers, at onboarding or later. Nothing in code chooses them, and nothing falls back to a default.

**Schema (`booking_system/settings/schema.py`)**
- `setting_catalog` (data, seeded from `settings/catalog.py`) has one row per choice. Columns:
  - `setting_key` and `module_name`;
  - `question` and `explanation`;
  - `value_type` (boolean, choice, choices, time, amount, tax_codes) and `options [{value, meaning}]`;
  - `required` and `required_when`;
  - `set_module` and `set_level` (the role allowed to set it);
  - `sop_prompt`, `display_order`;
  - `effective_basis`: `local_date` for front desk and HR, `business_date` for finance and F&B.
- `hotel_setting` holds per-hotel values: `hst_` id, `value` jsonb, `effective_from`, `set_by_user_id`, `set_at`, `source`, `note`.
  - It is append-only (a trigger refuses UPDATE), so history is never overwritten.
  - The value in effect on a day is the latest `effective_from` on or before that day.
  - A new value may start no earlier than the hotel's open date for the row's basis.
- SOP free text is kept as versioned documents (`hotel_sop`, `sop_` id) linked by `setting_key`, each with its own `effective_from`.
- SQL functions:
  - `hotel_local_date`, `hotel_setting_at(hotel, key, day)`;
  - `hotel_setting_required`, which raises an error naming the setting and its question;
  - `hotel_module_state(hotel, module, day)`;
  - `hr_worked_hours`, and `hr_leave_days(hotel, from, to, rule_day)` (PL/pgSQL, so it reads only the branch the setting selects).
- Migration: the three `hotel` time columns became `hotel_setting` rows (source `migration`) and were dropped.

**Catalog**

| Area | Settings |
|---|---|
| Front desk | Check-in time, check-out time, night-audit time, check-in room readiness |
| Finance | `enabled`; tax pricing (inclusive or exclusive); tax calculation (simple or compound); tax order (required when compound); cashier variance tolerance |
| F&B | `enabled`; closed-check correction (folio adjustment, or reopen with approval) |
| HR | `enabled`; worked-hours breaks (deduct unpaid breaks, or breaks paid); leave day counting (calendar days or working days); working weekdays (required when working days) |

**Module gating**
- A module with an `.enabled` setting (finance, F&B, HR) is refused by the access guard with HTTP 409 in three cases:
  - it is not set up;
  - it is turned off;
  - it is enabled but has unanswered required settings. The message names each missing setting and its question.
- Front desk is core: each of its operations refuses on the specific setting it reads.
- The settings routes (`/api/settings`, `/pending`, `/{key}/history`, POST) are member routes. They need membership only, and the service checks `set_module` / `set_level` for each setting.
- `pending()` splits each module's missing settings into two lists:
  - `ask`: answerable now;
  - `waiting`: needs hotel data first. For example, a tax order needs the hotel's tax codes. Waiting settings still keep the module refused.

**Behaviour per setting**
- **Finance tax:**
  - Exclusive pricing adds tax on top of the price.
  - Inclusive pricing splits the price into net plus taxes. The net is rounded, and the rounding residual stays in the net.
  - Compound tax applies the taxes in `finance.tax_order`, each on the net plus the taxes before it.
  - The tolerance setting decides whether a cashier shift variance needs a reason.
- **F&B:** the closed-check correction setting allows only one path. Under folio adjustment the check stays closed and an adjustment is posted. Under reopen with approval, a manager other than the requester approves, the settlement postings are reversed, and the check reopens for a new settlement round.
- **HR:** worked hours and leave days are computed by the SQL functions, using the setting in effect on the work date or the leave start date.

**Changes over time**
- Enabling a module later asks that module's required questions through the desk, using the `hotel_settings` intent:
  - a change is held with awaiting kind `setting_confirm` and saved only after the manager says yes;
  - each follow-up question is held with awaiting kind `setting`;
  - the module stays refused until every follow-up is answered.
- Operations use the value in effect at their business or local date.
- Finance and F&B recipes read the setting for each posting's business date. They report `tax_pricing_in_effect` (one value, or several when it changed within the period) and `revenue_at_quoted_prices`. HR recipes report `break_rule_in_effect` and `day_counting_in_effect`.
- The live SQL agent and the catalog agent get `SQL_FACTS`, which tells them to use these functions.

**Definitions**
- Per-user definitions are versioned: `version_id` (`dfv_`), `effective_from`, `supersedes`. A superseded entry moves to `definition_history`.
- `execution_runs.definition_versions`, `answer_complaints.definition_versions` and the session's `last_answer` record the versions an answer used.

**Onboarding**
- The `settings` step sits between policies and definitions. Its fields (`set_<key>`, `sop_<key>`) and questions are generated from the catalog: first each module's yes/no, then what each enabled module needs.
- Values are recorded with source `onboarding`, and replies state only what was saved.
- The step completes when nothing answerable is left. A waiting setting is reported, and its module stays refused until it is answered.

**Verified (SANDBOX HOTEL vs SANDBOX ANNEX, opposite values)**

| Check | SANDBOX HOTEL | SANDBOX ANNEX |
|---|---|---|
| FNB_FOOD priced 100 (SC 10%, SST 6%) | Net 100, tax 16, total 116 | Net 85.76, SC 8.58, SST 5.66, total 100 |
| F&B check at menu 42 | Total 48.72 | Total 42.00 |
| Cashier variance of 3 | Closes (tolerance 5) | Refused without a reason (tolerance 0) |
| Worked hours, 8-hour shift with 30-minute break | 7.50 | 8.00 |
| Leave Sun–Tue | 3 calendar days | 2 working days |
| Correcting a closed check | Folio adjustment; reopen refused | Adjustment refused; reopen requested, refused for the requester, approved by another manager (3 postings reversed) |

- **F&B enablement on ANNEX:**
  - The F&B route returned 409 while F&B was turned off.
  - The desk turn "we now serve food" asked for confirmation; "yes" saved it and asked the correction question.
  - Between those turns, the route returned 409 naming `fnb.closed_check_correction`.
  - Once answered (with its SOP), the route returned 200.
- **Mid-period tax change on HOTEL:**
  - A change dated on a closed business date was refused.
  - Inclusive pricing was set from the next business date.
  - `fin_revenue_by_department` this month read `tax_pricing_in_effect = "exclusive, inclusive"`, with quoted revenue 249.20 = 207.20 (net, exclusive day) + 42.00 (gross, inclusive day).
- **Definitions:** the complaint kept the old `dfv_` version; later runs recorded the new versions. A change of population moved the answer from 5 to 9 guests.
- **Onboarding:** a synthetic hotel was asked and recorded every applicable setting, plus SOPs, and was then wiped.
- **Recipes:** 12 recipes were validated and republished by direct insert: finance v4, HR v3/v4, F&B v3.

**Rollback.** Drop `hotel_setting`, `setting_catalog` and the functions. Then restore the hotel time columns from the `migration` rows.

---

## Part 7: Reporting layer (E)

### R1: Night-audit daily snapshots (3–4 days)

**Goal.** Immutable per-business-date statistics written by the night audit close (C4 step 5). Past-period reports read only these tables; future periods read on-the-books data from `booking_room_night`. Corrections after close are posted as adjustments on the next day, as in a standard PMS.

```sql
CREATE TABLE daily_room_stats (
  hotel_name                VARCHAR(255),
  business_date             DATE,
  room_type                 VARCHAR(255),
  physical_rooms            SMALLINT NOT NULL,
  out_of_order_rooms        SMALLINT NOT NULL,
  out_of_service_rooms      SMALLINT NOT NULL,
  available_rooms           SMALLINT GENERATED ALWAYS AS (physical_rooms - out_of_order_rooms) STORED,
  rooms_sold                SMALLINT NOT NULL,
  complimentary_rooms       SMALLINT NOT NULL,
  house_use_rooms           SMALLINT NOT NULL,
  arrivals_rooms            SMALLINT NOT NULL,
  departures_rooms          SMALLINT NOT NULL,
  no_show_rooms             SMALLINT NOT NULL,
  cancellations_made        SMALLINT NOT NULL,
  in_house_guests           SMALLINT,
  room_revenue_net          NUMERIC(14,2) NOT NULL,
  currency                  CHAR(3) NOT NULL,
  excluded_unknown_currency SMALLINT NOT NULL,   -- D6: rooms sold whose revenue is excluded
  unrated_rooms             SMALLINT NOT NULL,
  source                    VARCHAR(16) NOT NULL CHECK (source IN ('night_audit','reconstructed')),
  PRIMARY KEY (hotel_name, business_date, room_type),
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day,
  FOREIGN KEY (hotel_name, room_type) REFERENCES room_type ON UPDATE CASCADE);
CREATE TABLE daily_revenue (
  hotel_name        VARCHAR(255),
  business_date     DATE,
  outlet_name       VARCHAR(64),
  transaction_code  VARCHAR(32),
  gross_amount      NUMERIC(14,2) NOT NULL,
  adjustment_amount NUMERIC(14,2) NOT NULL,
  net_amount        NUMERIC(14,2) NOT NULL,
  tax_amount        NUMERIC(14,2) NOT NULL,
  currency          CHAR(3) NOT NULL,
  PRIMARY KEY (hotel_name, business_date, outlet_name, transaction_code),
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day,
  FOREIGN KEY (hotel_name, outlet_name) REFERENCES outlet,
  FOREIGN KEY (hotel_name, transaction_code) REFERENCES transaction_code);
CREATE TABLE daily_outlet_stats (
  hotel_name    VARCHAR(255),
  business_date DATE,
  outlet_name   VARCHAR(64),
  checks        INTEGER NOT NULL,
  covers        INTEGER NOT NULL,
  revenue_net   NUMERIC(14,2) NOT NULL,
  currency      CHAR(3) NOT NULL,
  PRIMARY KEY (hotel_name, business_date, outlet_name),
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day,
  FOREIGN KEY (hotel_name, outlet_name) REFERENCES outlet);
CREATE TABLE daily_ledger_balance (
  hotel_name           VARCHAR(255),
  business_date        DATE,
  guest_ledger         NUMERIC(14,2) NOT NULL,
  deposit_ledger       NUMERIC(14,2) NOT NULL,
  city_ledger          NUMERIC(14,2) NOT NULL,
  payments_received    NUMERIC(14,2) NOT NULL,
  open_exceptions      INTEGER NOT NULL,
  currency             CHAR(3) NOT NULL,
  PRIMARY KEY (hotel_name, business_date),
  FOREIGN KEY (hotel_name, business_date) REFERENCES business_day);
-- trigger: snapshots are insert-only; only system actor 'migration' may replace rows with source = 'reconstructed'
```

**Backfill (history, `source='reconstructed'`)**
1. Create closed `business_day` rows from each hotel's first booked night to yesterday (`closed_by_system='migration'`). JACKHOTEL runs from 23 Sep, about 38 days.
2. Compute `daily_room_stats` from `booking_room_night`, `room`, the OOO blocks and the history inventory. Expect **about 76 rows for JACKHOTEL** (2 types × 38 days) plus a few per other hotel.
3. Revenue for history is booking value spread by night (A6) in hotel currency. Unknown-currency rooms are counted in `excluded_unknown_currency` (D6).
4. `daily_revenue`, `daily_outlet_stats` and `daily_ledger_balance` get no history (no postings before go-live).

**Code.** `night_audit` step 5; the backfill script.

**Verify.** `daily_room_stats.rooms_sold` for reconstructed days equals the live `booking_room_night` counts; after a simulated close, `daily_revenue` ROOM equals the posted ROOM charges.

**Rollback.** Drop the tables (operational data is untouched). **Risk.** Low to medium.

### R2: Standard reports as general recipes (4–5 days, +1 day per department report)

**Rules for hotel-agnostic general recipes:**
- Read only these core tables: `hotel`, `room_type`, `department`, `outlet`, `business_day`, `booking_room_night`, `booking_summary`, `folio_transaction`, `transaction_code`, `daily_*`, `journal_line`, `payroll_line`, `roster`, `group_pickup`.
- **Never** read `content_document`.
- Parameters are only `hotel_name` and a period.
- "Today" is the open business date.
- Past periods (`< business_date`) read snapshots; future periods read `booking_room_night`.
- Amounts are grouped by currency, and `excluded_unknown_currency` / `unrated_rooms` are always output (D6).
- A recipe whose module isn't configured for the hotel returns the explicit row `module_not_configured` (for example, no outlets of type `restaurant`). It never returns zeros.
- `seed()` validates each recipe on every hotel, not only JACKHOTEL.

| Report | Recipe(s) | Reads | Needs |
|---|---|---|---|
| Manager flash (day / MTD / YTD, same period last year) | `manager_flash` | `daily_room_stats`, `daily_revenue`, `daily_ledger_balance`, live arrivals / departures / in-house from `booking_room_night` | R1 |
| Occupancy and revenue | existing `daily_occupancy_rates`, `daily_room_nights_sold`, `daily_room_revenue`, `daily_adr`, `daily_revpar` re-pointed | snapshots (past) + `booking_room_night` sell rates (future, on the books) | R1 |
| Movement | existing arrivals / departures / in-house / cancellations / no-shows / length of stay | snapshots + `booking_summary` | R1 |
| Pace and mix | existing `daily_bookings_made`, `daily_booking_lead_time`, `daily_channel_mix` | `booking_summary.booked_at`, `channel` | A7, C3 |
| Balances | existing `daily_outstanding_balance` + new `deposit_ledger`, `ar_aging` | `folio_balance`, P4 views | C3, P4 |
| Departmental P&L | `departmental_pnl` | `journal_line` by `cost_center → department` and `gl_account.account_type` | FIN (+ HR payroll journal) |
| Payroll cost | `payroll_cost`, `labour_hours` | `payroll_line`, `roster`, `staff_duty_log` by department | HR |
| F&B covers | `fb_covers` (covers, average check, revenue per cover) | `daily_outlet_stats` | FB-1 |
| Group pickup | `group_pickup` | `group_pickup` view | P2 |

**User recipes.** The 4 active ones were already moved in A4. At R2, their past-period versions are re-authored onto the snapshots with the managers' approval.

**Verify.** Every general recipe passes `validate_publication` on all 7 hotels; old and new outputs for the past 30 days match within the documented differences (D1 arrival shift, D6 exclusions).

**Rollback.** Re-activate the previous recipe versions. **Risk.** Low to medium (recipe output changes visible to managers).

---

## Part 8: Main risks

1. **Front-desk churn (A4, A5).** About 30 functions in `front_desk_manager` change their night and room logic, and availability is pushed to channels (overselling risk). Mitigate with dual-write, a 30-day availability diff and the lock order.
2. **Money correctness (C3, C4, FIN).** Immutable transactions, sign rules, `txn_seq` contention and automated night posting. Mitigate with simulated multi-day closes on a copy, the idempotent charge index and balanced-journal checks.
3. **Actor coverage (C2).** The audit trigger raises when a writer doesn't declare its actor. Roll it out per table, with a test that calls every tool.
4. **Access lock-out and leaks (A1b).**
   - A wrong `access_point` row either locks staff out or exposes a module.
   - About 25 data routes that need no token today start requiring one, so the UI and `workers/pms.js` must send it.
   - The SQL agent must not reach tables outside the user's modules.
   - Mitigate with the startup completeness check, 3 days in report-only mode, the per-role test, the GENERAL_MANAGER invariant and `access_version` revocation.
5. **Business date (C4).** Every tool's "today" changes meaning from the calendar to the business date. Close-time behaviour must be tested around 03:00 local.
6. **Time zones (A7).** Aware/naive datetime TypeErrors (68 call sites to audit).
7. **`content_store` push-down (A8).** It must match the Python matcher exactly (missing vs NULL).
8. **Recipe drift.** Agent 688931ae and others edit `seed_general.py` concurrently. Each stage's recipe change is a new version validated on all hotels; user recipes need their owner's approval. The A1b `module_name` tag is a catalog column, so it doesn't conflict with body edits.
9. **Scope.** The full scope is about 74–99 days. Phases A (with A1b) + B + C4 + R1 deliver a complete multi-user PMS; departments can follow independently.
