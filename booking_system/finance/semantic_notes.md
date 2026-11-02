# Finance tables for the SQL agent and the catalog agent

PMS finance only (folio postings, tax, cashier, city ledger, night audit close). There is no general ledger, journal, trial balance or P&L.
Every table carries `hotel_id` (`htl_<uuid>`); bind it with `hotel_id = %(hotel_id)s`. Ids are `prefix_<uuid>` text; codes and names are unique attributes per hotel, never join keys. Join on the `*_id` columns.

## Dates and money
- `business_date` is the hotel's night-audit date. Postings, shifts and the close are dated by it, not by `posted_at`. Exactly one `business_day` per hotel has `status = 'open'`; that is "today" for finance. Closed dates are locked.
- Every amount is `NUMERIC` in the hotel currency (`hotel.currency`); a posting in another currency is refused by the database. Keep `currency` in outputs anyway.
- Folio sign: `finance_posting.amount` is + for what the folio owes (charge, tax, refund, transfer in) and − for credits (payment, allowance, transfer out). Money received = `-SUM(amount)` of payment/refund postings.
- Postings are immutable. A correction is a reversal row (`reverses_posting_id` set, amount negated) dated on the business date it was made. Sums are net of reversals; do not filter reversals out unless asked for "gross".

## Tables
| Table | Grain | Key columns |
|---|---|---|
| `department` | department | `department_id`, `department_name` |
| `transaction_code` | code | `transaction_code_id`, `transaction_code`, `transaction_type` (charge, tax, payment, refund, adjustment, transfer), `department_id`, `payment_method` (payments/refunds only: cash, card, bank_transfer, channel_collect, voucher, other), `is_taxable`, `gl_export_code` (external GL code, may be NULL), `status` |
| `tax_code` | tax | `tax_code_id`, `tax_code`, `tax_basis` (percent, per_unit), `unit` (night, person_night, item), `tax_transaction_code_id` (the code its tax postings use) |
| `tax_rule` | tax × effective date | `tax_code_id`, `effective_from`, `rate_percent` or `amount_per_unit` + `currency`. The rule in force on a date is the latest `effective_from <= date`. |
| `transaction_code_tax` | code × tax | which taxes a taxable code adds |
| `business_day` | hotel × date | `business_day_id`, `business_date`, `status` (open, closed), `closed_at`, `closed_by_user_id` |
| `finance_posting` | one folio line | `posting_id`, `business_day_id`, `business_date`, `folio_type` (guest, account, outlet), `folio_ref`, `booking_id`, `ar_account_id` (account folios), `outlet_id`, `transaction_code_id`, `transaction_type`, `quantity`, `amount`, `currency`, `tax_code_id` + `tax_rule_id` + `parent_posting_id` (tax rows → their charge), `reverses_posting_id`, `transfer_posting_id` (the other half of a direct-bill transfer), `cashier_shift_id`, `reason`, `source_module`, `source_ref`, `posted_at`, `posted_by_user_id` / `posted_by_system` |
| `cashier_shift` | one drawer session | `cashier_shift_id`, `business_day_id`, `cashier_user_id`, `opening_float`, `status`, `expected_cash`, `counted_cash`, `variance` (counted − expected, set at close) |
| `ar_account` | city-ledger account | `ar_account_id`, `account_name`, `account_type` (company, travel_agent, channel, government, other), `credit_limit`, `payment_terms_days`, `status` |
| `ar_invoice` | invoice | `ar_invoice_id`, `ar_account_id`, `invoice_number`, `invoice_date`, `due_date`, `status` (open, void) |
| `ar_invoice_item` | invoiced posting | `ar_invoice_id`, `posting_id` |
| `ar_allocation` | payment applied to invoice | `ar_invoice_id`, `credit_posting_id`, `amount` |
| `daily_close_summary` | closed date × code × tax | snapshot written at close: `business_date`, `transaction_code_id`, `transaction_type`, `department_id`, `payment_method`, `tax_code_id`, `posting_count`, `quantity_total`, `amount_total` |

## Views (filter each by `hotel_id = %(hotel_id)s`)
- `fin_folio_balance`: one row per folio: `balance`, `charges`, `payments`, `transfers`.
- `fin_invoice_balance`: per invoice: `invoiced`, `applied`, `open_amount`.
- `fin_ar_aging`: per account as of the open business date: `not_due`, `overdue_1_30`, `overdue_31_60`, `overdue_61_90`, `overdue_90_plus`, `uninvoiced`, `unapplied_credit`, `balance`.
- `fin_cashier_shift_activity`: per shift × payment method × code: `net_received`.
- `fin_daily_revenue`, `fin_daily_payments`, `fin_daily_tax`, `fin_gl_export`: closed-day snapshot readings.

## Joins
- Department of a posting: `finance_posting.transaction_code_id → transaction_code.department_id → department`.
- Payment method: `transaction_code.payment_method` (only for payment/refund codes).
- Tax code of a tax line: `finance_posting.tax_code_id`; its taxable base: the parent row `finance_posting.posting_id = parent_posting_id`.
- Guest folio to booking: `finance_posting.booking_id` (core booking id); account folio: `ar_account_id` (`folio_ref` equals it).
- Cashier, closer, poster: `cashier_shift.cashier_user_id`, `business_day.closed_by_user_id`, `finance_posting.posted_by_user_id → user_account.user_id` (name: `user_account.user_name`).
- Booking: `finance_posting (hotel_id, booking_id) → booking (hotel_id, booking_id)`; nights via `booking_room_night.booking_id`.
- FKs to core tables (`user_account`, `booking`) and F&B `outlet` are added by `schema.ensure_links()` once those tables exist.
- Open date: `business_day WHERE status = 'open'`; "yesterday" in finance is the latest closed `business_date`.

## Readings to keep apart
- Revenue = charge + adjustment postings, before tax. Tax = tax postings. Payments = payment − refund. Transfers move balances between folios and are neither revenue nor payment.
- Posted revenue (finance) differs from booking value (`room_bookings` / recipes like `daily_room_revenue`); say which one is used.
- A missing tax row means the code is not taxable or the charge was refused; the module never posts 0 % tax by default.
- Open shifts have NULL `expected_cash`, `counted_cash`, `variance`.

## Contracts for other modules (wave 2 core, F&B, night audit)
All functions are in `booking_system.finance.service`. They return `{'ok': True, ...}` or `{'ok': False, 'error': ..., details}`; nothing raises to the caller, and nothing is defaulted.

**Transaction codes (finance owns them).**
- `resolve_transaction_code(hotel_id, code)` returns `transaction_code_id`, `transaction_type`, `department_id`/`department_name`, `payment_method`, `is_taxable`, `taxes` [{tax_code, tax_basis, unit}], `gl_export_code` and `status`. An unknown code is refused, with `similar` names.
- `save_transaction_code(hotel_id, code, type, department_name, is_taxable, payment_method=None, gl_export_code=None, tax_codes=(), status='active', confirm_new=False)` reads first; a near-matching name returns `similar` unless `confirm_new`. F&B registers its outlet codes this way (for example `FNB_FOOD`, `FNB_BEVERAGE` in department `FOOD_AND_BEVERAGE`) instead of keeping its own code and tax tables.
- `quote_tax(hotel_id, code, amount, units=None)` returns the tax lines a charge would post, without posting.

**Folio postings (the folio contract).** The core owns folios; finance owns the lines. A folio is `(folio_type, folio_ref)`:
- `folio_type='guest'`: `folio_ref` is the core folio id (or the `bkg_` booking id until a folio table exists). Pass `booking_id` (`bkg_`) as well.
- `folio_type='account'`: `folio_ref` is `ar_account_id`.
- `folio_type='outlet'`: `folio_ref` is `outlet_id`.

Every posting is dated on the hotel's open `business_day`. A caller that has its own date passes it as `business_date`, and it is refused unless it equals the open date; it never redates. `source_module` + `source_ref` make it idempotent: a repeat returns `already_posted`, and a repeat with a different code or amount is refused. Actor: exactly one of `user_id` (`usr_`) or `system` (night_audit, channel_aiosell, migration, scheduler).
- `post_charge(hotel_id, folio_type, folio_ref, code, amount, source_module, source_ref, user_id|system, quantity=1, booking_id, outlet_id, ar_account_id, units={'night': n, ...}, reference)` posts a positive net amount plus one tax row per linked tax code. It refuses when a taxable code has no rule effective on the business date, or when a per-unit tax has no `units` count. A caller that computed its own tax passes `expected_tax` (the total) and/or `expected_tax_codes` (names); a difference is refused, so finance's rules are checked, never overridden.
- `post_charge_in(cur, ...same arguments)` is the same charge on the caller's cursor, so it commits or rolls back with the caller's write (for example an F&B check close). A refusal writes nothing and returns `ok: False`; a database error propagates.
- `post_adjustment(..., amount (signed), reason, ...)` posts an allowance or rebate with its taxes.
- `post_payment(..., code of type payment or refund, amount (positive), cashier_shift_id)`: payments are stored negative and refunds positive. A cash code requires the poster's open `cashier_shift_id`.
- `transfer_to_account(hotel_id, folio_type, folio_ref, ar_account_id, transfer code, amount, ...)` is the direct bill. It writes two linked transfer rows and checks the account's status, its credit limit and the folio balance.
- `reverse_posting(hotel_id, posting_id, reason, source_module, source_ref, user_id|system, cashier_shift_id)` is the only correction. It negates the posting with its tax rows (and the transfer counterpart), on the open date. Postings are never updated or deleted; the database refuses it.
- `read_folio(hotel_id, folio_type, folio_ref)` returns the postings and the balance. Check-out settlement rule for the core: every guest folio of the booking has balance 0 in `fin_folio_balance`.
- Room-night charges from the core's night audit: `post_charge(..., 'ROOM', sell_rate, 'night_audit', '<booking_night_id>', system='night_audit', booking_id=..., units={'night': 1})`. The booking night id as `source_ref` makes a rerun post nothing.

**Business date / night audit.**
- `open_business_day(hotel_id, date, user_id)` opens the first date only.
- `close_business_day(hotel_id, business_date, user_id)` requires the open date and no open cashier shifts. It writes `daily_close_summary`, locks the date and opens the next one, all in one transaction, and returns revenue by department and code, payments by method and the tax summary.
- `read_business_day(hotel_id)` returns the open date and the recent ones.

**Cashier.** `open_cashier_shift(hotel_id, user_id, opening_float)` and `close_cashier_shift(hotel_id, shift_id, user_id, counted_cash)`. Expected cash = float + net cash in the shift; variance = counted − expected.

**City ledger.** `save_ar_account`, `issue_invoice(hotel_id, ar_account_id, user_id, posting_ids | all_uninvoiced)` (numbered `INV-000001`, due after the account's terms), `void_invoice`, `record_account_payment(..., ar_invoice_id)`, `allocate_credit`, `ar_aging`.

**GL export.** `gl_export(hotel_id, date_from, date_to)` returns closed-day totals per code with `gl_export_code`. It is refused while any exported code is unmapped.

## F&B: one hotel-wide code and tax source

Finance `transaction_code`, `tax_code` + `tax_rule` and `department` are the hotel's single source. F&B keeps its local tables only until it switches. The switch is a code change on the F&B side; there is no data migration, because finance codes are matched by their code text, which F&B already stores.

**Mapping (F&B table → finance).**
| F&B | Finance |
|---|---|
| `fnb_transaction_code` (`ftc_`), type `charge` | `transaction_code` type `charge`, department `FOOD_AND_BEVERAGE`, same `transaction_code` text (seeded: `FNB_FOOD`, `FNB_BEVERAGE`) |
| `fnb_transaction_code`, type `payment` | `transaction_code` type `payment` with `payment_method` (seeded: `CASH`, `CARD`, `BANK_TRANSFER`); refunds use `REFUND_CASH`/`REFUND_CARD` |
| `fnb_transaction_code`, type `tax` | `tax_code.tax_transaction_code_id` (seeded `TAX_SST`, `TAX_SERVICE_CHARGE`) |
| `fnb_transaction_code.gl_account_code` | `transaction_code.gl_export_code` (`set_gl_export_code`) |
| `fnb_tax_code` (`ftx_`) + `rate_percent` | `tax_code` (basis `percent`) + dated `tax_rule.rate_percent`; the rate is chosen by business date |
| `fnb_menu_item.tax_code_id` | taxes belong to the charge code (`transaction_code_tax`): an item's taxability is chosen by its code, e.g. a `FNB_FOOD_EXEMPT` charge code without taxes |
| `fnb_department_code` (outlet, department → `ftc_`) | keeps its shape; `transaction_code_id` holds the finance `txc_` id |
| `fnb_check_line.tax_rate_percent` (snapshot) | stays a snapshot, filled from `quote_tax(hotel_id, code, net)` (`taxes[].amount`, `tax_total`) at the time of the check |
| `fnb_check_payment.transaction_code_id` | finance payment `txc_` id |

**F&B code switch, step by step.**
1. Read codes with `resolve_transaction_code` / `list_transaction_codes` instead of `fnb_transaction_code`; register a new outlet code with `save_transaction_code(..., 'charge', 'FOOD_AND_BEVERAGE', is_taxable, tax_codes=[...])`.
2. Compute line tax with `quote_tax` instead of `fnb_tax_code.rate_percent`. A missing rule is a refusal, not a zero.
3. Point the `fnb_department_code`, `fnb_check_payment` and `fnb_pending_posting` FKs at `transaction_code(transaction_code_id)` and `tax_code(tax_code_id)`. Both id columns are `VARCHAR(48)`, and the `ftc_`/`ftx_` prefix CHECKs exist only on F&B's own primary keys, so the referencing columns don't change type.
4. Group `settle_room` lines by finance charge code only: the code carries its taxes, so (code, tax code) groups collapse to one row per code.
5. Optionally replace the pending queue: post inside the check close with `post_charge_in(cur, hotel_id, 'guest', folio_ref or booking_id, code, net, 'fnb', '<source_ref>/<code>', user_id=..., booking_id=..., outlet_id=..., business_date=..., expected_tax=...)`.
6. Outlet settlements (cash or card at the outlet) post to the outlet folio: `post_charge(..., 'outlet', outlet_id, ...)` then `post_payment(..., 'outlet', outlet_id, code, amount, cashier_shift_id=...)`. That puts outlet revenue and drawer cash in the daily close and the cashier reconciliation.

**How finance consumes `fnb_pending_posting` today** (`booking_system/finance/fnb_bridge.py`, `drain(hotel_id)`; run by a scheduler or `python -m booking_system.finance.fnb_bridge <hotel_id>`):
- If the hotel has no open business date, it stops and leaves every row pending.
- Otherwise, in one F&B transaction, it takes `fnb.posting.pending_postings(cur, hotel_id)` (`FOR UPDATE SKIP LOCKED`, so concurrent drains don't collide) and calls `post_charge` for each row:
  - folio `('guest', folio_ref or booking_id)` and code = the row's `transaction_code` text;
  - the amount is the net; `source_module='fnb'`; `source_ref` = the `fpp_` `posting_id`; `reference` = the F&B `source_ref` (`fnb/<outlet>/<date>/<check>`);
  - the actor is `created_by_user_id`; `booking_id` and `outlet_id` are set;
  - `business_date`, `expected_tax=tax_amount` and `expected_tax_codes=[tax_code]` are checked.
- Posted: `mark_posted(cur, fpp_id, core_txn_ref = finance pst_ id)`. Finance writes the tax rows itself, from its rules.
- Refused: `mark_rejected(cur, fpp_id, reason)`. Reasons are an unknown code, a tax total or tax code differing from finance's rules, a business date that isn't the open one (already closed), or a zero amount.
- Rerunning is safe. If F&B's transaction fails after finance posted, the next drain finds the posting by `('fnb', fpp_id)`, returns `already_posted` and marks it.

## General recipes (booking_system/finance/recipes.py)
`fin_revenue_by_department`, `fin_revenue_by_transaction_code`, `fin_payments_by_method`, `fin_tax_collected`, `fin_cashier_variance` (rows per named period of business_date and dimension), `fin_outstanding_folio_balances`, `fin_city_ledger_aging`, `fin_daily_close_status` (current state). All are module `finance`.
