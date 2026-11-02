# F&B outlet tables for the SQL agent and catalog agent

Module `fnb`. F&B owns outlets, menu items and checks. Transaction codes, departments, taxes, business dates and postings are Finance's (`booking_system/finance/semantic_notes.md`): a menu item carries a Finance charge code, and settling a check posts to a Finance folio. Every read filters `hotel_id = %(hotel_id)s` on a table that carries `hotel_id` (`outlet`, `fnb_menu_item`, `fnb_check`, `finance_posting`); `fnb_check_line` reaches the hotel through `fnb_check`. Ids are `<prefix>_<uuid4>` text; names and codes are unique attributes, never join keys.

## Tables

| Table | Id (prefix) | Key columns | Notes |
|---|---|---|---|
| `outlet` | `outlet_id` (`out_`) | `hotel_id`, `outlet_name` (unique per hotel, uppercase), `outlet_type` (restaurant, bar, room_service, cafe, banquet, other), `status` | Selling points. `finance_posting.outlet_id` references it. |
| `fnb_menu_item` | `item_id` (`fmi_`) | `outlet_id`, `item_name` (unique per outlet), `price` (tax-exclusive), `transaction_code_id` (`txc_`, a Finance charge code), `status` | The code decides the Finance department (`transaction_code.department_id`) and the taxes (`transaction_code_tax`). Descriptions and images are `content_document` collection `fnb_menu_item`, key `item_id`. |
| `fnb_check` | `check_id` (`fck_`) | `outlet_id`, `business_date` (Finance's open business date when opened), `check_number` (per outlet per business date), `table_number`, `covers`, `status` (open, closed, void), `settlement` (direct, room; NULL unless closed), `booking_id` (`bkg_`) and `room_id` (`rm_`) (both set when settlement = room), `currency`, `subtotal`, `discount_total`, `tax_total`, `total` (set at close from the postings; NULL on open and void checks), `void_reason`, `opened_at`, `closed_at`, `opened_by_user_id`, `closed_by_user_id` | One guest bill. |
| `fnb_check_line` | `line_id` (`fcl_`) | `check_id`, `line_seq`, `item_id`, `transaction_code_id`, `quantity`, `unit_price`, `discount_amount`, `discount_reason`, `voided`, `void_reason`, `voided_by_user_id`, `added_by_user_id` | Price and charge code are copied from the menu item when ordered. Tax is not per line; Finance computes it per charge code at settlement. |

View `fnb_check_line_amount`: non-voided lines with `gross` = round(quantity × unit_price, 2), `discount`, `net` = gross − discount, `transaction_code_id`. Join on `check_id`; scope through `fnb_check`.

## Postings (Finance)

Settling a check posts, in the same transaction as the close, one `finance_posting` charge per charge code (net after discounts) plus Finance's tax rows (`parent_posting_id` = the charge), all with `source_module = 'fnb'`, `outlet_id`, `business_date` = the check's business date:

- Direct: `folio_type = 'outlet'`, `folio_ref = outlet_id`, then one payment row per tender (stored negative); the outlet folio nets to 0 per check.
- Room: `folio_type = 'guest'`, `folio_ref` = the folio (default the `booking_id`), `booking_id` set.
- `source_ref` = `<check_id>/<transaction code>` for charges (tax rows share their parent's), `<check_id>/pay/<n>` for payments. `split_part(source_ref, '/', 1) = fnb_check.check_id` joins a posting to its check.
- Rows posted before this design (core fixture history) have `source_ref` `fnb_pending_posting/<fpp_ id>` and only room charges; direct-paid fixture checks were not posted.

## Joins

- `fnb_check.outlet_id = outlet.outlet_id`; `fnb_check_line.check_id = fnb_check.check_id`; `fnb_check_line.item_id = fnb_menu_item.item_id`.
- Codes: `fnb_menu_item.transaction_code_id` / `fnb_check_line.transaction_code_id` = `transaction_code.transaction_code_id` (`transaction_code.transaction_code`, `department.department_name` via `department_id`).
- Room-charged checks: `(fnb_check.hotel_id, fnb_check.booking_id) = (booking.hotel_id, booking.booking_id)`; `fnb_check.room_id = room.room_id` (`room.room_number`); guest `booking.guest_id = guest.guest_id`. Direct-paid checks have no guest.
- Staff: every `*_by_user_id` is `user_account.user_id` (`user_account.user_name`).

## Reading rules

- Revenue and tax are `finance_posting` rows with `source_module = 'fnb'`: net = `transaction_type IN ('charge','adjustment')`, tax = `transaction_type = 'tax'`, grouped by `outlet_id` and `business_date`. This is what the daily close reports; do not total `fnb_check` amounts as revenue.
- Covers, check counts, discounts and voids come from `fnb_check` / `fnb_check_line` on `status = 'closed'` checks (voids: `fnb_check_line.voided`, and `fnb_check.status = 'void'` checks whose lines keep `voided = false`).
- Reconciliation: closed `fnb_check.total` minus posted charge + tax for the same outlet and date should be 0; a gap means checks that did not reach Finance.
- Room charge vs direct: `finance_posting.folio_type` (`guest` vs `outlet`), or `fnb_check.settlement` for counts. Payment methods: `transaction_code.payment_method` on payment rows of `folio_type = 'outlet'`.
- Average check = posted net / covers (per cover) or / closed checks (per check).
- Dates: `business_date` on both sides (Finance's business date, not a clock date). Timestamps (`opened_at`, `closed_at`, `posted_at`) are timestamptz.
- Currency: `fnb_check.currency` and `finance_posting.currency` are the hotel currency; compare with `hotel.currency` and flag mixed currency rather than summing across currencies.

## General recipes (booking_system/fnb/recipes.py)

`fnb_daily_outlet_revenue`, `fnb_daily_covers`, `fnb_daily_average_check`, `fnb_daily_voids_discounts`, `fnb_daily_settlement_mix`, output grain `outlet`. Each returns one row per named period (the `seed_general` period set) and per outlet, plus an `ALL OUTLETS` row, with `hotel_currency`, `mixed_currency`, `foreign_currency_checks`, `open_checks` and `posting_gap`.

## Access

`outlet`, `fnb_menu_item`, `fnb_check`, `fnb_check_line` belong to module `fnb` (view to read). F&B postings are `finance_posting` rows (module `finance`).
