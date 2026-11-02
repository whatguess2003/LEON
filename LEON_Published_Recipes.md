# LEON published recipes

250 published recipes as of 2026-11-02.

Each line is the catalog id and one question a hotel manager can ask that recipe.

## Seeded

- **daily_adr** — What is our ADR for a hotel-local day or date range?
- **daily_arrival_counts** — How many arrivals, rooms, and known guests are scheduled, and how many guests actually checked in?
- **daily_attendance_punctuality** — How many clock-ins were late, and what was the on-time rate?
- **daily_average_length_of_stay** — What is the average length of stay for bookings arriving in the period?
- **daily_booking_lead_time** — What is the average booking lead time for arrivals in the period?
- **daily_bookings_made** — How many bookings were created on each day, and how many room nights did they book?
- **daily_cancellation_counts** — How many cancellations fell on the arrival date, the stay night, or the cancellation date?
- **daily_channel_mix** — What share of room nights and of new bookings did each channel take?
- **daily_departure_counts** — How many departures are due, and how many guests actually checked out?
- **daily_in_house_counts** — How many rooms, bookings, and known guests are in house?
- **daily_leave_overview** — How many leave requests are pending, and how many approved leave days fall in the span?
- **daily_no_show_counts** — How many no-shows were there, and which past arrivals were never checked in?
- **daily_occupancy_rates** — What is the occupancy rate for a hotel-local day or date range?
- **daily_outstanding_balance** — What outstanding balance is still due on active bookings arriving in the span?
- **daily_revpar** — What is RevPAR for a hotel-local day or date range?
- **daily_room_nights_sold** — How many room nights were sold in the period?
- **daily_room_revenue** — What was total room revenue for a hotel-local day or date range?
- **daily_rooms_left_by_room_type** — How many rooms are left to sell for each room type on each night?
- **daily_roster_coverage_by_department** — For each department, how many staff were rostered and what share of started shifts were attended?
- **daily_staff_on_duty** — How many staff clocked in compared with how many were rostered?
- **fin_cashier_variance** — What is each cashier's drawer variance, and which shifts are still open?
- **fin_city_ledger_aging** — How do open city-ledger invoices age as of the end date?
- **fin_daily_close_status** — Which business date is still open, and what revenue and payments were posted on each closed date?
- **fin_outstanding_folio_balances** — What folio balances are still outstanding, by folio type?
- **fin_payments_by_method** — How much was received and refunded by payment method?
- **fin_revenue_by_department** — What revenue was posted by department, net of allowances and before tax?
- **fin_revenue_by_transaction_code** — What revenue was posted by transaction code?
- **fin_tax_collected** — How much tax was posted by tax code, and on what taxable base?
- **fnb_daily_average_check** — What is the F&B average check per cover and per check?
- **fnb_daily_covers** — How many F&B covers were served, by outlet?
- **fnb_daily_outlet_revenue** — What was each outlet's F&B net sales, tax, and discounts?
- **fnb_daily_settlement_mix** — How much F&B was charged to guest folios versus settled at the outlet?
- **fnb_daily_voids_discounts** — How much was voided or discounted in F&B, by outlet?
- **fnb_open_business_date_net_sales** — What is the hotel-wide F&B net sales total for the currently open business date?
- **tonight_family_stays** — How many family stays are in house now or staying tonight?
- **tonight_occupancy_by_room_type** — How full is each room type tonight?
- **tonight_party_counts** — How many adults and children are in tonight's sold parties?

## q01–q50

- **q01_reservations_currently_arriving** — How many reservations do we currently have for arrivals in the period?
- **q02_bookings_created_by_status** — How many bookings were created, broken down by booking status?
- **q03_made_then_cancelled_percent** — What percentage of reservations made in the period were subsequently cancelled?
- **q04_average_booking_lead_time** — What is our average booking lead time for guests arriving in the period?
- **q05_upcoming_confirmed_arrival_dates** — Which upcoming dates have the highest number of confirmed arrivals?
- **q06_daily_occupancy_by_day** — What is the occupancy rate for each day in the span?
- **q07_room_type_occupancy** — Which room types had the highest occupancy over the span?
- **q08_rooms_still_sellable** — How many rooms can we still sell tonight, excluding blocked and out-of-order rooms?
- **q09_full_occupancy_dates** — Which dates in the span are already at full occupancy on current reservations?
- **q10_lost_room_nights** — How many room nights were lost because rooms were blocked or unavailable?
- **q11_total_room_revenue** — What is total room revenue for a hotel-local day or span?
- **q12_daily_adr_revpar_occupancy** — What are ADR, RevPAR, and occupancy for each day in the span?
- **q13_room_type_revenue** — Which room type generated the most room revenue in the span?
- **q14_room_revenue_versus_prior** — How much did room revenue increase or decrease compared with the previous span?
- **q15_high_occupancy_below_average_adr** — Which dates had high occupancy but below-average ADR?
- **q16_channel_revenue** — How much room revenue did each booking channel generate?
- **q17_ota_net_after_commission** — Which OTA delivered the highest net revenue after commission?
- **q18_channel_cancellation_rate** — Which booking channel had the highest cancellation rate?
- **q19_direct_ota_adr** — How does the ADR of direct bookings compare with OTA bookings?
- **q20_ota_nights_vs_booking_share** — Which OTA produced the most room nights relative to its share of total bookings?
- **q21_arriving_adults_children** — How many adults and children are arriving in the period?
- **q22_arriving_guests_prior_stay** — Which arriving guests have stayed with us before?
- **q23_unique_guests_stayed** — How many unique guests stayed in the period?
- **q24_repeat_guest_revenue** — Which repeat guests generated the most room revenue?
- **q25_upcoming_guest_complaints_requests** — Which upcoming guests previously submitted complaints or special requests?
- **q26_inhouse_outstanding** — What outstanding payment is tied to guests who are currently in house?
- **q27_departures_unpaid** — Which guests checking out still have an unpaid balance?
- **q28_collections_by_method** — How much was actually collected, by payment method?
- **q29_period_revenue_uncollected** — How much room revenue from the period remains uncollected?
- **q30_balance_discrepancies** — Which reservations do not reconcile across charges, payments, and the outstanding balance?
- **q31_departure_rooms_to_clean** — How many rooms require cleaning following guest departures?
- **q32_arrivals_in_unready_rooms** — Which arriving reservations are assigned to rooms that are not yet ready?
- **q33_room_turnaround_time** — What was the average room turnaround time from checkout until the room was ready again?
- **q34_maintenance_blocks_by_room_type** — Which room types had the most maintenance-related blocks?
- **q35_rooms_ready_after_checkin** — How many rooms were marked ready after the standard check-in time?
- **q36_selling_rate_vs_achieved_adr** — How far is today's selling rate from the achieved ADR for each room type?
- **q37_weekend_booking_pace** — How does booking pace for a future weekend compare with the same lead time last weekend?
- **q38_occupancy_below_booking_target** — Which future dates have occupancy below the hotel booking target?
- **q39_unsold_tonight_revenue** — How much additional room revenue would the remaining rooms generate at current selling rates?
- **q40_adr_change_by_room_type** — Which room types had the largest change in achieved ADR?
- **q41_overdue_operational_tasks_by_staff** — How many operational tasks are currently overdue, by responsible staff member?
- **q42_housekeeping_tasks_on_time** — How many housekeeping tasks were created in the span, and how many were completed or inspected?
- **q43_guest_requests_unresolved_two_hours** — Which guest requests have remained unresolved for more than two hours?
- **q44_complaint_resolution_time_by_category** — How many guest complaints are recorded?
- **q45_reopened_or_repeated_operational_issues** — Which operational issues were reopened or reported repeatedly?
- **q46_room_type_revenue_fewer_nights** — Which room types generated higher revenue this month despite selling fewer room nights than last month?
- **q47_ota_above_average_adr_below_average_stay** — Which OTAs delivered bookings with above-average ADR but below-average length of stay?
- **q48_rooms_to_clean_for_confirmed_arrivals** — How many rooms need to be cleaned and made ready for all confirmed arrivals?
- **q49_arrival_balance_and_open_request** — Which upcoming reservations have an outstanding balance, an unresolved guest request, and an arrival within 48 hours?
- **q50_performance_recent_span_versus_previous** — How did occupancy, ADR, RevPAR, revenue, channels, cancellations, and outstanding payments compare with the previous span?

## qb0001

- **qb0001_active_future_and_assignment** — Which active future reservations are missing a room, conflict with another assignment, or sit on a retired room type?
- **qb0001_audit_and_channel_sync** — Which reservation events are out of order, missing an audit row, or out of step with the channel?
- **qb0001_cancellation_lifecycle** — What share of bookings were cancelled after confirmation, and how close to arrival were they cancelled?
- **qb0001_checkin_checkout_timing** — What share of confirmed arrivals became no-shows, and who checked in early or departed late?
- **qb0001_confirmation_speed** — How many reservations were created but never confirmed?
- **qb0001_groups_and_commercial** — Which group, company, package, and deposit bookings are missing a rooming list, a component, or a due date?
- **qb0001_reservation_amendments** — What share of reservations were amended, and which stays were shortened or extended?
- **qb0001_source_guest_repeat** — How many future arrivals are missing a phone or email, and what share of active reservations are repeat guests?
- **qb0001_stay_integrity** — Which reservations have impossible stay dates, duplicate references, or overlapping stays for the same guest?
- **qb0001_walkin_lead_and_rooms** — What share of stays changed room, and how many bookings were made on the arrival day?

## qb0101

- **qb0101_assignment_board** — Which ready rooms are unassigned, and which vacant rooms are still held by an uncleared checkout?
- **qb0101_audit_occupancy** — How many physical rooms were occupied at the night-audit clock, and which of those still look clean?
- **qb0101_channel_inventory** — Where does channel-manager inventory sit above the PMS, and were bookings taken after sellable inventory hit zero?
- **qb0101_free_runs** — Which rooms are free for the whole span, and where is the longest free run by room type?
- **qb0101_holds** — How many temporary holds have no booking, and how many quotes have already expired?
- **qb0101_inventory_exceptions** — Which bookings need an upgrade because their room type is sold above inventory?
- **qb0101_inventory_history** — How many sellable room nights are there, and do any closed days still have a reservation?
- **qb0101_restrictions** — How many room nights are held back by stop-sell, closed-to-arrival, or a minimum stay?
- **qb0101_room_blocks** — How many rooms are blocked for maintenance, housekeeping, or renovation?
- **qb0101_room_fit** — How many rooms have a recorded king bed?
- **qb0101_stay_use** — How many room nights were paid guest, complimentary, or house use?

## qb0201

- **qb0201_adr_weights** — What is night-weighted ADR, ADR averaged once per reservation, and ADR for one-night stays versus longer stays?
- **qb0201_ancillary_attachment** — How much ancillary and package revenue is attached to stays, including breakfast on room-only bookings?
- **qb0201_booking_value_by_room_type** — What is the average gross booking value by room type, excluding cancelled and no-show reservations?
- **qb0201_channel_refund_ratio** — What is refund value as a share of gross booking value for each channel?
- **qb0201_completed_versus_advance** — How much room revenue is recognized on checked-out stays versus cash still sitting on nights after the end date?
- **qb0201_contract_position** — How many corporate accounts are on file, and what is the unrealized contract commitment?
- **qb0201_currency_translation** — What share of room revenue was charged in a foreign currency, and what did exchange-rate translation change?
- **qb0201_discount_reasons** — How much was discounted manually or by staff, by reason?
- **qb0201_forward_risk** — How much confirmed room revenue can still be cancelled without a stored penalty?
- **qb0201_group_versus_transient** — How does group ADR compare with transient ADR, and what share of group package income is not room revenue?
- **qb0201_lead_time_revenue** — How much stay-date room revenue came from same-week bookings versus bookings made more than 30 days ahead?
- **qb0201_negative_net_bookings** — Which bookings have room revenue minus credits and commission below zero?
- **qb0201_nightly_rate_distribution** — What are the median, 10th percentile, and 90th percentile nightly sell rates, excluding zeros?
- **qb0201_operating_revpar** — What is total operating revenue per available room night?
- **qb0201_pending_and_walkin** — How much room revenue is on pending reservations, and how do walk-in and advance direct revenue compare?
- **qb0201_posting_integrity** — Which folio items have a business date that differs from the posting date, and what was written off?
- **qb0201_promo_yield** — What share of room revenue was sold at a promotional price, and how does realized revenue compare with published-rate capacity?
- **qb0201_rate_plan_gap** — What is the gap between the published price and the realized sell rate for each rate plan?
- **qb0201_reservation_value_concentration** — What share of reservations and gross value is under 300, and what share of room revenue comes from the top 10 percent?
- **qb0201_revenue_mix_shift** — How did the room versus ancillary mix change against the previous span of the same length?
- **qb0201_revenue_per_created_booking** — What is realized room revenue per reservation, counting cancelled and no-show reservations too?
- **qb0201_revpar_bases** — What is RevPAR per physical room night and per sellable room night?
- **qb0201_room_contribution** — What is room revenue, direct cost, and contribution by room type and per occupied room night?
- **qb0201_room_fee_counts** — How many no-show penalties, cancellation fees, late checkouts, early check-ins, and extra-person charges were posted?
- **qb0201_room_revenue_ex_tax** — What is stay-night room revenue excluding taxes and service charges?
- **qb0201_run_rate_versus_budget** — What month-end room-revenue run rate does the month-to-date span imply, and how does it compare with the approved budget?
- **qb0201_service_recovery_rate** — How much service-recovery allowance was given per 100 completed stays?
- **qb0201_sold_below_floor** — What share of paid room nights sold below the approved minimum rate, and how much revenue was forgone?
- **qb0201_space_yield** — What is room revenue per occupied square metre and per available guest place or bed?
- **qb0201_unbilled_checked_out** — What is the value of checked-out accommodation that has not been placed on an invoice?
- **qb0201_upgrade_value** — How much paid upgrade revenue was taken, and what is the published-rate difference of complimentary upgrades?
- **qb0201_waived_penalties** — How much cancellation penalty was waived rather than collected?
- **qb0201_weekday_room_revenue** — What is average room revenue by weekday?

## qb0301

- **qb0301_channel_rate_compare** — How many OTA nights sold below the direct rate for the same room type and date?
- **qb0301_lead_time_mix** — How far ahead of the stay date was each sold room night booked?
- **qb0301_list_versus_achieved** — What share of sold nights went at the published list price, and how does achieved ADR compare?
- **qb0301_pace_versus_target** — How do on-the-books room nights compare with sellable inventory, and is a pace target on file?
- **qb0301_price_movement** — What is the standing published price of each room type, and did the amount change?
- **qb0301_pricing_events** — What pricing events were recorded in the span, including rate-plan gaps?
- **qb0301_rate_floor_ceiling** — Which future stay dates are priced below the rate floor or above the ceiling?
- **qb0301_rate_math** — What is the face value of unsold rooms at the current published rate?
- **qb0301_sellout_last_room** — Which room-type dates sold out, and what rate did the last room sell at?
- **qb0301_stay_restrictions** — How many inventory nights have a minimum stay above one night, closed-to-arrival, or stop-sell?
- **qb0301_weekday_price_pickup** — Which weekdays have strong pickup but a weaker published price?

## qb0401

- **qb0401_allotment_position** — How much of each channel's allotment was used for stay dates in the span?
- **qb0401_channel_cash** — What share of each channel's bookings did the hotel collect, and how much was remitted?
- **qb0401_channel_commission** — What commission expense did each channel incur, and how much of it is still unbilled?
- **qb0401_channel_guest_fit** — By channel, what share of bookings lack a phone, and what share of guests are repeat guests?
- **qb0401_channel_share_versus_prior_span** — Which channels increased their share of booked room nights versus the previous span?
- **qb0401_channel_stay_mix** — How do weekday long stays, single-night weekends, and pickup vary by channel?
- **qb0401_channel_sync** — Where do channel feeds differ from local reservations?
- **qb0401_channel_value_and_net_adr** — Which channels have a higher average booking value but a lower net ADR?
- **qb0401_confirmed_by_channel** — Which distribution channels generated confirmed reservations during the period?
- **qb0401_listing_controls** — How many listings sit on a retired room type, and how many nights are on stop-sell?
- **qb0401_rate_integrity** — Which nights undercut the published price, and which direct totals do not match the nightly rates?
- **qb0401_unrecorded_distribution** — Which distribution events, from virtual cards to disputes, does the hotel have on file for the span?

## qb0501

- **qb0501_acquisition_cohort_repeat** — Which acquisition month has the strongest six-month repeat-stay rate?
- **qb0501_arrival_messages** — How many arrivals have no language on file, and what share received instructions before the travel day?
- **qb0501_cases_requests** — How many guest requests, complaints, and reviews were recorded, and what share of checkouts had every request done?
- **qb0501_channel_switching** — How many guests booked an OTA first and a direct stay later?
- **qb0501_guests_at_other_properties** — How many guests of this hotel also stayed at another property in the group?
- **qb0501_inquiries_identity** — How many phone numbers are still unconfirmed, and what is the median days from inquiry to booking?
- **qb0501_preference_request_consent** — How many guests with more than three completed stays still have no preference on file?
- **qb0501_repeat_stay_intervals** — How many guests returned within 30 days, and what is the median gap between completed stays?
- **qb0501_reservations_missing_primary_guest** — How many reservations have no designated primary staying guest?
- **qb0501_return_room_choice** — How often do returning guests take a different room type, or the same physical room?
- **qb0501_verified_contact_conflicts** — Which guest profiles share a verified phone or email with a different profile?

## qb0601

- **qb0601_card_lifecycle** — Which card authorizations expired without capture, and how much is sitting in open chargebacks?
- **qb0601_cash_after_obligations** — How much counted cash is left after approved supplier payments due in the span?
- **qb0601_cash_exceptions** — Which cash refunds have no original receipt, and which cash was posted outside a cashier shift?
- **qb0601_charge_tax_gaps** — Which posted charges have no tax, or used a tax rule before its effective date?
- **qb0601_close_integrity** — How much was reversed after the business day closed, and which room-ledger postings lack a reservation charge?
- **qb0601_collection_reminders** — Which guest balances already have two payment reminders, and what was collected within two days after a reminder?
- **qb0601_corporate_billing** — Which corporate accounts are past terms, and which invoices are disputed or unbilled?
- **qb0601_desk_cash_by_date** — How much cash did the front desk collect on each business date?
- **qb0601_duplicate_ledger_keys** — Which folios share one payment reference, and which source references were imported twice?
- **qb0601_events_and_terms** — Which banquet revenue is still unbilled after the event date, and which compulsory bookings are unpaid past the due date?
- **qb0601_folio_reopen** — Which settled folios were reopened because new charges arrived after settlement?
- **qb0601_guest_deposits** — How much refundable guest deposit is still held, including deposits not released after checkout?
- **qb0601_guest_folio_aging** — How do outstanding guest folio balances age, and which folios have a credit?
- **qb0601_intercompany** — Which guest folios carry another property's charges without an authorized transfer?
- **qb0601_invoice_bank_alias** — Which open invoices still look unpaid while an unmatched bank line could be the receipt?
- **qb0601_operating_expenses** — How were operating expenses split between rooms and food and beverage, and which cost centres are over budget?
- **qb0601_period_results** — What costs were posted to the wrong business period, and how does cash-basis operating result compare with accrual?
- **qb0601_pos_reconciliation** — Which F&B cash is in the ledger but not in a direct POS settlement, and which paid orders were later voided?
- **qb0601_receipt_exceptions** — Which guest receipts were posted to a different reservation than the payer, or to no booking?
- **qb0601_refund_position** — Which approved refunds are not yet processed, and which refunds exceed the original payment?
- **qb0601_settlements** — Which gateway charges have not settled to the bank, and which bank lines have no guest payment?
- **qb0601_shift_tolerance** — Which closed cashier shifts exceed the hotel's drawer-variance tolerance?
- **qb0601_staff_claims** — Which approved employee reimbursements are not yet paid?
- **qb0601_stored_value** — How much guest credit is unused after expiry, and what unredeemed gift-voucher value is still outstanding?
- **qb0601_supplier_bills** — Which supplier invoices are duplicates, above the purchase order, or overdue?
- **qb0601_tips** — How much credit-card tip money has been collected and not yet distributed to staff?
- **qb0601_top_debtors** — How much of the open guest receivable sits in the five largest open accounts?
- **qb0601_utility_meters** — What electricity cost fell in unoccupied periods, and what utility accrual is not yet invoiced?

## qb0701

- **qb0701_checkout_to_clean_start_median** — What is the median wait from guest checkout to housekeeping starting work?
- **qb0701_checkout_to_ready_median** — What is the median time from guest checkout until the room is sellable-ready?
- **qb0701_checkout_without_cleaner** — Which rooms had no cleaning assignment after checkout?
- **qb0701_clean_awaiting_inspection** — Which rooms still need supervisory inspection after housekeeping marked them clean?
- **qb0701_cleaner_load** — Which rooms were assigned to more than one cleaner at once, and which attendants are over the rest of their shift?
- **qb0701_cleaning_pace** — What is the median cleaning duration by room type, and how long is the wait from completion to inspection?
- **qb0701_cleaning_quality** — Which rooms failed a second quality check, or were marked ready and then dirty before occupancy?
- **qb0701_hk_overtime_travel** — How much housekeeping overtime was worked, and how many minutes did guests wait to be relocated?
- **qb0701_keys_and_access** — Which physical keys were not returned after checkout, and which access cards are still active?
- **qb0701_linen_operations** — How much staff time was lost waiting for linen, and what share of linen-change requests finished within 30 minutes?
- **qb0701_lost_found** — What share of lost-and-found items were logged within one hour, and which unclaimed items have reached the disposal date?
- **qb0701_maintenance_cases** — How many maintenance tickets were raised while a room was occupied, and which repairs were reopened?
- **qb0701_maintenance_cost** — What was the material cost of repairs for each room?
- **qb0701_maintenance_timing** — What is the median time from a maintenance report to the technician starting, and what share of tickets closed by the due date?
- **qb0701_minibar** — Which minibar restocks have no item quantities, and which rooms lost stock with no guest charge?
- **qb0701_parts_and_services** — Which spare parts are below reorder, and which public-area cleaning was overdue at 7 AM?
- **qb0701_prepare_before_three** — How many room attendants are rostered to start before 3 PM?
- **qb0701_preventive_maintenance** — What fraction of scheduled preventive maintenance was completed on time, and which safety devices failed inspection?
- **qb0701_stayover_dnd** — Which occupied rooms received stayover cleaning, and which rooms were entered during do-not-disturb?
- **qb0701_unsellable_pending_inspection** — Which guest rooms are withheld from sale only because housekeeping inspection is pending?

## qb0801

- **qb0801_friday_housekeeping_shortfall** — What housekeeping hour shortfall should we expect this Friday from earlier Friday departures?
- **qb0801_reception_coverage** — How many reception minutes had no one clocked in, and what was the peak arrivals per receptionist?
- **qb0801_shift_compliance** — Which shifts are missing a break entry or an opening checklist, and how many scheduled shifts are unfilled?
- **qb0801_task_followthrough** — Which tasks closed late, stayed open past a deadline after acknowledgment, or closed with no evidence?
- **qb0801_task_queue_snapshot** — How many front-desk tasks are open at the morning shift start, and which urgent tasks have no owner?
- **qb0801_workflow_records** — How many handover items, refund approvals, and SOP acknowledgments were recorded in the span?

## qb0901

- **qb0901_arrival_followups** — Which upcoming arrivals owe money and have an unconfirmed early check-in promise?
- **qb0901_channel_demand** — Which active reservations are cancelled on the channel while the night is oversold, and which bookings were taken during stop-sell?
- **qb0901_fnb_cross** — Which restaurant checks were settled to a booking that is already checked out, and which breakfast-inclusive stays also paid for breakfast?
- **qb0901_functions** — Which functions have meeting space and no catering, and which deposits sit below the penalty?
- **qb0901_groups** — Which groups are short of clean rooms, and which room types lose money on group nights?
- **qb0901_leon_ops** — How many staff hours were saved after corrections, and what net benefit was recorded?
- **qb0901_ota_contribution** — For each OTA, what is room revenue, commission, and net contribution?
- **qb0901_promises** — Which late checkouts delay a confirmed early check-in, and which handover commitments have no acknowledgment?
- **qb0901_reviews** — Which low review scores fall on a night when front-desk attendance was below the rooms roster?
- **qb0901_room_assignment_risk** — Which reservations are assigned to a room with unresolved maintenance and no free room of the same type?
- **qb0901_self_checkin** — Which late arrivals completed a verified mobile check-in, and which self check-in failures were followed by a guest request?
- **qb0901_service_recovery** — Which cleaning complaints followed a long turnaround, and what contribution was lost to downtime on a full night?
- **qb0901_transfers** — Which school groups need more coach bays than were reserved, and what is transport contribution after fuel, tolls, and driver pay?
- **qb0901_upgrade_moves** — Which stays changed room type while the sell rate stayed on the original type?
- **qb0901_vacancy_housekeeping** — On which dates do vacant sellable rooms exceed the housekeeping staff rostered that day?

## qbfix

- **qbfix_guest_payment_methods** — How much did guests pay by card, cash, bank transfer, and e-wallet?
- **qbfix_ota_payment_complaints** — How many payment-expectation complaints were recorded by channel?
- **qbfix_revenue_per_stay_reservation** — What is room revenue per reservation that has a night in the span?
- **qbfix_revpar_bridge** — How did RevPAR change between the first and last month in the span, split into ADR and occupancy effects?
- **qbfix_room_reversal_share** — What share of posted room revenue was later reversed?
- **qbfix_type_inventory_vs_rooms** — On this date, which room types have published inventory that differs from the count of active rooms?
