# LEON PMS: PMS BASIC (Core) Query Mapping

## Completed (Fully Supported)
- Booking: Book, cancel, modify stay dates, availability, special requests. **create_booking_tool**, **cancel_booking_tool**, **modify_stay_dates_tool**, **check_room_availability_tool**, **list_bookings_with_special_requests_tool**, **add_booking_special_request_tool**
- Booking: List bookable room types; booking details; change room quantity; confirm pending; send confirmation; update status. **list_bookable_room_types_tool**, **get_booking_details_tool**, **modify_booking_quantity_tool**, **search_bookings_tool**, **send_booking_confirmation_tool**, **update_booking_status_tool**
- Guest: Profile CRUD, queries, communications. **create_guest_profile_tool**, **update_guest_profile_tool**, **deactivate_guest_profile_tool**, **read_guests_by_criteria_tool**, **track_guest_communication_tool**, **read_guest_profiles_tool**, **reactivate_guest_profile_tool**
- Room: Room type/number CRUD, readiness/out-of-order, features. **create_room_type_tool**, **delete_room_number_tool**, **assign_room_number**, **mark_room_out_of_order**, **update_room_readiness_status**, **read_room_types_by_criteria_tool**, **read_room_portfolio_tool**, **read_room_type_from_room_number_tool**
- Housekeeping: Dirty rooms; create/update tasks; mark clean; staff status. **read_dirty_rooms**, **create_cleaning_task**, **update_cleaning_progress**, **read_room_current_cleaning**, **read_staff_current_cleaning_status**
- Maintenance: Create/complete request; dashboards/lists. **create_maintenance_request**, **update_maintenance_progress**, **read_maintenance_dashboard**, **read_maintenance_requests**
- Payment: Read/update status; refund; unpaid; deadlines. **read_payment_status_tool**, **update_payment_status_tool**, **record_refund_tool**, **read_payment_deadlines**
- Policy: Read/update/delete policy; list available hotels. **read_policies_tool**, **create_policy_tool**, **update_policy_tool**, **delete_policy_tool**, **read_available_hotels_tool**
- Reports: Occupancy, performance dashboards, channel performance, daily/weekly reports, revenue analytics. **generate_occupancy_analytics**, **generate_performance_dashboard**, **channel_performance_report**, **daily_forecast_report**, **daily_manager_report**, **weekly_pace_report**, **calculate_revenue_analytics**, **read_revenue_analytics**
- Concierge (core): Nearby attractions/restaurants/shopping, taxi, city guide. **read_local_attractions**, **read_local_restaurants**, **read_local_shopping**, **read_transportation_info**, **read_city_guide**

## In-Progress (Partially Supported / Being Improved)
- Booking: VIP arrivals next 7d; overbook risk next 14d; hold blocks; group release date.
- Guest: VIP arrivals today; outstanding docs; churn-risk this quarter.
- Room: Preventive maintenance due next 7d; repeated HK failures; low-sentiment rooms by attribute.
- Inventory: Linen usage forecast 14d; amenities restock by floor; minibar variance report.
- Policy: Compare cancellation policy vs comp set; simulate payment window impact; max stay allowed surfacing.
- Front Desk: Early check-in candidates; late check-out pending; arrivals without room.
- Staff: Overtime risk next week; training gaps; shift optimization (night coverage).
- Housekeeping: Turnaround time outliers; SLA overdue right now; supplies usage anomalies 7d.
- Payment: Expiring payment windows 72h; refunds pending; chargeback risk.
- Maintenance: Open tickets >48h; parts restock (top 10); energy anomalies by stack.
- Concierge (core): Events this weekend; best way to airport.
- Analytics: Owner one‑pager; city pace vs comp set; segment mix shift MoM.

### Miscellaneous (In-Progress)
- Restart the PMS server.
- Enable integration with new payment gateway.
- Update system to the latest version.
- Generate a custom report for my manager.
- Archive old guest records.
- Purge test data from the system.
- Integration health check (OTA/payment).
- Data quality audit (missing keys/formats).
- SLA dashboard (all teams).

## Future (Not Yet Supported / Requires New Tools or Features)
- Booking: Group blocks with rooming lists/allotments; packages with inclusions and split revenue.
- Guest: Expectation contract enforcement; in‑stay micro‑personalization; live sentiment radar; guest digital twin.
- Room: Comfort vs energy optimizer; live comfort monitoring (noise/temp).
- Inventory: Two‑way OTA/GDS distribution with parity monitoring.
- Policy: Policy reasoner (simulate cxl/deposits) with impact on satisfaction/revenue; multi‑currency tax/e‑invoicing.
- Front Desk: Arrival choreography across teams; dynamic upgrade fairness; accessibility guardian; night audit; folio splits/transfers/charge routing.
- Staff: Role‑aware copilots; knowledge‑on‑tap; auto shift generation with fatigue constraints.
- Housekeeping: Predictive readiness; intelligent inspections; anomaly mining; HK boards by zone; linen/L&F tracking.
- Payment: PCI‑DSS grade flows incl. 3‑D Secure; automated chargeback handling.
- Maintenance: Predictive failures from sensors; CMMS parts inventory with reorder.
- Analytics: Knowledge graph + digital twin for what‑if; comp‑set benchmarking with elasticity; dynamic pricing recs.
- Misc/Platform: RBAC/approvals/audit; GDPR tooling; data quality monitors; federated learning; agent safety rails.

