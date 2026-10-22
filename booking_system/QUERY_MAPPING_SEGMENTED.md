# LEON PMS: Segmented Query Mapping (BASIC / ADVANCED / AI-PMS)

This catalog organizes queries into three tiers (PMS BASIC, PMS ADVANCED, AI-PMS) within three delivery states (Completed, In-Progress, Future).

---

## Completed (Fully Supported)

### PMS BASIC (Core)
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

### PMS ADVANCED
- Hotel Info keywords and query processing. **create_hotel_keywords**, **read_hotel_keywords**, **delete_hotel_keywords_by_category**, **parse_hotel_keywords_tool**, **process_keyword_query_tool**
- Booking/Guest analytics and revenue forecast. **get_booking_analytics_tool**, **get_revenue_forecast_tool**, **analyze_booking_patterns_tool**, **read_guest_analytics**

### AI-PMS
- Parse guest info from description; parse staff requirements/skills; reasoning suggestions. **read_guest_information_from_description**, **parse_staff_requirements_tool**, **parse_staff_skills_description_tool**, **suggest_room_assignment**, **read_maintenance_issue_analysis**

---

## In-Progress (Partially Supported / Being Improved)

### PMS BASIC (Core)
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

### PMS ADVANCED
- Spa reservations; packages/upsell candidates; transport demand heatmap; advanced city/shopping filters.

### AI-PMS
- VIP detection with special requests (cross-tool); overbook mitigation plans; churn retention playbooks.
- Preventive maintenance forecasting and auto-scheduling; sentiment-driven service recovery.

### Miscellaneous
- Restart the PMS server.
- Enable integration with new payment gateway.
- Update system to the latest version.
- Generate a custom report for my manager.
- Archive old guest records.
- Purge test data from the system.
- Integration health check (OTA/payment).
- Data quality audit (missing keys/formats).
- SLA dashboard (all teams).

---

## Future (Not Yet Supported / Requires New Tools or Features)

### PMS BASIC (Core)
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

### PMS ADVANCED
- Multi‑property CRS and portfolio analytics; group BEO; deep AR/AP & City Ledger; accounting integrations.
- Spa/golf/banquet integrations and packaging.

### AI-PMS
- Reasoning fabric orchestrating multi‑department workflows with constraint solver and policy engine.
- Consent‑first personalization with explainable decisions.
 - Experience composer (in‑house + local) to guest goals/time.
 - Real‑time demand shaping for spa/gym/restaurant.
 - LLM‑curated itineraries with live bookings and in‑house service pricing.

---

## Appendix: Original QUERY_MAPPING.md (verbatim mirror)

The following section mirrors every query from the original `QUERY_MAPPING.md` to ensure completeness while segmentation is finalized.

### Original Content

[BEGIN ORIGINAL]

# LEON PMS: Query Mapping & Progress Tracking

This document tracks the operational coverage of LEON PMS by categorizing brainstormed user/manager queries into three sections:
- **Completed**: Fully supported by LEON (tested, reliable)
- **In-Progress**: Partially supported, tool exists but needs refinement, or workflow is being improved
- **Future**: Not yet supported, requires new tools, integrations, or major features

Queries are grouped by PMS domain within each section. This living document is used to track progress and guide future development.

---

## Completed (Fully Supported)

### PMS BASIC (Core)

### Booking
- Book a Deluxe Suite for tomorrow night. **create_booking_tool**
- Cancel my booking for next week. **cancel_booking_tool**
- Modify my reservation to add an extra night. **modify_stay_dates_tool**
- Check availability for a Triple Suite this weekend. **check_room_availability_tool**
- Show me all bookings with special requests. **list_bookings_with_special_requests_tool**
- Book a room for a guest with dietary restrictions. **create_booking_tool**, **add_booking_special_request_tool**
 - Add airport pickup to my booking. **add_booking_special_request_tool**
 - List all bookable room types. **list_bookable_room_types_tool**
 - Show booking details for +60123456789BOOKING1. **get_booking_details_tool**
 - Change booking +60123456789BOOKING1 to 2 rooms. **modify_booking_quantity_tool**
 - Search confirmed bookings from 2025-08-10 to 2025-08-15. **search_bookings_tool**
 - Show booking analytics for July 2025. **get_booking_analytics_tool**
 - Forecast room revenue for the next 30 days. **get_revenue_forecast_tool**
 - Analyze booking patterns for the past 90 days. **analyze_booking_patterns_tool**
 - Send booking confirmation for +60123456789BOOKING1 by email. **send_booking_confirmation_tool**
 - Set booking +60123456789BOOKING1 status to CONFIRMED. **update_booking_status_tool**
 - Extend my current stay by two nights. **modify_stay_dates_tool**
 - Confirm all pending bookings for this weekend. **search_bookings_tool**, **update_booking_status_tool**


### Guest
- Register a new guest: Jane Smith, +60123456789. **create_guest_profile_tool**
- Update John Doe's email address to john@email.com. **update_guest_profile_tool**
- Delete guest profile for +60123456789. **deactivate_guest_profile_tool**
- Upgrade John Doe to VIP status. **update_guest_profile_tool**
- Show me all guests who have stayed more than five times. **read_guests_by_criteria_tool**
- Find guests who left a negative review. **read_guests_by_criteria_tool**
- List all guests who requested hypoallergenic pillows. **read_guests_by_criteria_tool**
- Show me guests who have not completed payment. **read_guests_by_criteria_tool**
- Find guests who have stayed in multiple properties. **read_guests_by_criteria_tool**
 - Log a call with guest Jane Smith. **track_guest_communication_tool**
 - Record a complaint from guest +60123456789. **track_guest_communication_tool**
 - Assign loyalty points to guest John Doe. **update_loyalty_program**
 - Send a welcome message to all arriving guests. (Already supported in Misc)
 - Show profile for guest +60123456789. **read_guest_profiles_tool**
 - Show guest analytics for last quarter. **read_guest_analytics**
 - Parse guest info: "John Doe, +60123456789, USA, vegan". **read_guest_information_from_description**
 - Reactivate guest profile +60123456789. **reactivate_guest_profile_tool**

### Room
- Add a new room type: Executive Suite. **create_room_type_tool**
- Update the description for Deluxe Room. **manage_room_description_tool**
- Delete room 202 from Superior Room. **delete_room_number_tool**
- Assign room 101 to John Doe. **assign_room_number**
- Mark room 305 as out of order. **mark_room_out_of_order**
- Set room 305 as ready. **update_room_readiness_status**
- Show me all rooms with jacuzzi tubs. **read_room_types_by_criteria_tool**
- List rooms with connecting doors. **read_room_types_by_criteria_tool**
- Find rooms with accessibility features. **read_room_types_by_criteria_tool**
- Show me rooms that are due for renovation. **read_room_portfolio_tool**
 - Delete room type EXECUTIVE SUITE. **delete_room_type_tool**
 - Add room numbers 201, 202, 203 to DELUXE ROOM. **add_room_numbers_tool**
 - Show details of room type EXECUTIVE SUITE. **read_room_type_tool**
 - Validate the room system setup. **read_room_system_validation_tool**
 - What room type is room 120? **read_room_type_from_room_number_tool**

### PMS ADVANCED

### Hotel Info
- Add hotel keywords: "SKYLINE VIEW, NEAR KLCC, JACUZZI". **create_hotel_keywords**
- Show hotel keywords. **read_hotel_keywords**
- Delete DINING keywords. **delete_hotel_keywords_by_category**
- Parse keywords: "romantic, city view, bathtub". **parse_hotel_keywords_tool**
- Which rooms match "city view AND bathtub"? **process_keyword_query_tool**


### Inventory
- Increase inventory for Deluxe Room to 20. **manage_room_quantity**
- Reduce available Superior Rooms by 2. **manage_room_quantity**
- Update the price for Executive Suite to RM600. **manage_room_price**
- List all inventory items that need restocking. **read_inventory_information_from_description**
 - What is the price for EXECUTIVE SUITE on 2025-09-01? **check_room_pricing**


### Policy
- Add a new refund policy: 100% refund within 24 hours. **create_policy_tool**
- Update the payment window to 7 days. **update_policy_tool**
- Delete the old cancellation policy. **delete_policy_tool**
- Show me all policy changes in the last year. **read_policies_tool**
- List all policies that apply to group bookings. **read_policies_tool**
- Show me the late check-out policy. **read_policies_tool**
- What’s the policy for early check-in? **read_policies_tool**
 - List available hotels in our portfolio. **read_available_hotels_tool**
 - How do policies affect booking +60123456789BOOKING1? **calculate_policy_impact_tool**

### Front Desk
- Check in guest John Doe. **check_in_guest**
- Check out guest +60123456789. **check_out_guest**
- Show me all guests arriving after midnight. **read_assignment_dashboard**
- List all guests with late arrivals. **read_assignment_dashboard**
- Who has not returned their room key? **read_assignment_dashboard**
 - Mark room 305 available from 2025-08-12 to 2025-08-15. **mark_room_available**
 - List rooms exceeding clean threshold days. **read_rooms_exceeding_clean_threshold**
 - Auto-update rooms exceeding clean threshold. **update_rooms_by_threshold_auto**
 - Show assignments for room 101 for August 2025. **read_room_assignment_tool**
 - Suggest a room for booking +60123456789BOOKING2 on 2025-08-20. **suggest_room_assignment**
 - Show room assignments for booking +60123456789BOOKING1. **read_room_assignments_for_booking_tool**
 - Analyze maintenance issue for room 101: "AC not cooling". **read_maintenance_issue_analysis**
 - Show readiness status for room 305. **read_room_readiness_status_tool**
 - Relocate booking +60123456789BOOKING1 to room 120. **relocate_guest_tool**
 - Assign a specific room to guest John Doe. **assign_room_number**

### Staff
- Add a new staff member: Alice Tan. **create_staff_profile_tool**
- Update the role for staff +60123456780 to Supervisor. **update_staff_profile_tool**
- Delete staff profile for +60123456780. **delete_staff_profile_tool**
- Show me staff with first aid certification. **read_staff_profiles_tool**
- List all staff with language skills in Japanese. **read_staff_profiles_tool**
- Show me staff who have completed safety training. **read_staff_profiles_tool**
 - Show housekeeping staff. **read_staff_by_department_tool**
 - Show available housekeeping staff with deep cleaning skill. **read_available_staff_tool**
 - Parse staff requirements: "2 night-shift housekeepers with first aid". **parse_staff_requirements_tool**
 - Parse staff skills: "Laundry ops, deep cleaning, chemical handling". **parse_staff_skills_description_tool**
 - Analyze housekeeping staff performance for July. **analyze_staff_performance_query_tool**

### Housekeeping
- Create a cleaning task for room 101. **create_cleaning_task**
- Update the progress of room 202 cleaning. **update_cleaning_progress**
- Mark room 305 as cleaned. **update_room_readiness_status**
- Who completed the most cleaning tasks last week? **read_staff_performance_report**
 - Flag room 210 for deep cleaning. **create_cleaning_task**
 - Show dirty rooms needing cleaning. **read_dirty_rooms**
 - Show housekeeping analytics for last week. **read_housekeeping_analytics**
 - Show operational efficiency metrics for last month. **read_operational_efficiency_metrics**
 - What’s the current cleaning status for room 140? **read_room_current_cleaning**
 - Show current cleaning assignments for +60139977651. **read_staff_current_cleaning_status**
 - Assign deep cleaning to all suites. **create_cleaning_task**


### Payment
- Check payment status for booking #12345. **read_payment_status_tool**
- Update payment status to paid for reservation #54321. **update_payment_status_tool**
- Refund payment for booking #98765. **record_refund_tool**
- Show me all unpaid bookings. **read_payment_status_tool**
- Show me payment trends by method (credit card, cash, etc.). **read_payment_status_tool**
- Who has a payment due this week? **read_payment_deadlines**
- Show me all refunded payments. **read_payment_status_tool**

### Maintenance
- Create a maintenance request for room 305. **create_maintenance_request**
- Update the status of maintenance request #789 to completed. **update_maintenance_progress**
 - Assign technician to fix the elevator. **update_maintenance_progress**
 - Close all resolved maintenance requests. **update_maintenance_progress**
 - Send maintenance report to engineering. **read_maintenance_dashboard**
 - List maintenance requests with status IN_PROGRESS. **read_maintenance_requests**
 - Show maintenance dashboard for AOE HOTEL. **read_maintenance_dashboard**

### Concierge
- Arrange a city tour for my guests. **read_local_attractions**
- Book a table at a Michelin-starred restaurant. **read_local_restaurants**
- Show me the best local restaurants for vegetarians. **read_local_restaurants**
 - List all nearby attractions with free entry. **read_local_attractions**
 - List all shopping malls within 5km. **read_local_shopping**
 - Add a local attraction: "Skyline Park, free entry, 1km". **create_local_attraction**
 - Validate concierge system setup. **read_concierge_system_validation**
 - Show city guide for KUALA LUMPUR. **read_city_guide**
 - Show local shopping options. **read_local_shopping**
 - Order a taxi for guest John Doe. **read_transportation_info**


### Analytics & Reports
- Generate a monthly occupancy report. **generate_occupancy_analytics**
- Show me a year-over-year revenue comparison. **calculate_revenue_analytics**
- List all KPIs for the last quarter. **generate_performance_dashboard**
- Show me a breakdown of revenue by room type. **occupancy_revenue_analysis**
- Show me a forecast of next month’s occupancy. **daily_forecast_report**
- List all negative reviews in the last 30 days. **generate_guest_analytics**
 - Show me the average length of stay by guest segment. **generate_guest_analytics**
 - Send daily performance summary to management. **daily_manager_report**
 - Generate financial reconciliation report for July 2025. **generate_financial_reconciliation**
 - Generate operational alerts. **generate_operational_efficiency_metrics**
 - Generate today’s daily manager report. **daily_manager_report**
 - Generate weekly pace report for next week. **weekly_pace_report**
 - Show channel performance for last month. **channel_performance_report**
 - Show revenue analytics for Q2 2025. **read_revenue_analytics**

### Miscellaneous
- Send a welcome message to all arriving guests.
- Show me a summary of all system alerts.
- Show me the current server status.
- Show me all pending approvals.
 - Web search: "best housekeeping SOP checklist PDF". **web_search_tool**

---

## In-Progress (Partially Supported / Being Improved)

### PMS BASIC (Core)

### Booking
- Book the best available room for my VIP guest next Friday.
- Reserve three rooms for the wedding party, all next to each other.
- Upgrade my room to a suite.
- Block all rooms on the 5th floor for maintenance.
- List all bookings that include airport pickup.
 - VIP arrivals next 7 days with special requests.
 - Overbook risk dates (next 14 days) by room type.
 - Hold 5 Deluxe Rooms for VIP group until 6PM today.
 - Block 10 rooms for wedding, release on 2025-09-01.

### Guest
- Merge duplicate guest profiles for Jane Smith.
- Blacklist guest for repeated violations.
 - Who are our top-spending guests this year?
 - Which guests have birthdays this month?
 - Who has a pet-friendly booking this week?
 - Today’s VIP arrivals with contacts.
 - Guests with outstanding documents (passport/invoice).
 - Churn-risk guests this quarter.

### Room
- Which rooms have been unoccupied for more than a month?
- Schedule renovation for all rooms on the 8th floor.
- Enable smart home features for room 501.
 - Which rooms have the highest guest satisfaction scores?
 - Which rooms have been flagged for maintenance more than twice this year?
 - Rooms due for preventive maintenance (next 7 days).
 - Rooms with repeated housekeeping failures (last 30 days).
 - Rooms with low sentiment scores (<6.0) by attribute.

### Inventory
- Order 10 more baby cots.
- Restock minibar items for all suites.
- Set special pricing for public holidays.
- Forecast inventory shortages for next month.
 - Show me inventory trends for the past six months.
 - Show me a forecast of inventory shortages.
 - How many extra beds are available right now?
 - Which room types are most often overbooked?
 - What’s the average occupancy rate for Deluxe Rooms?
 - Which room types have the highest cancellation rates?
 - Show me inventory for conference rooms.
 - How many baby cots are available?
 - Which inventory items are most requested by guests?
 - Linen usage forecast (next 14 days).
 - Amenities restock list by floor.
 - Minibar variance report (consumption vs stock).

### Policy
- Enforce no-smoking policy in all rooms.
- Set minimum check-in age to 21.
- Publish new group booking policy.
 - What’s the maximum stay allowed for a guest?
 - What’s the policy for lost and found items?
 - Show me the smoking policy for all properties.
 - Which policies are most frequently cited in guest complaints? (analysis in progress)
 - Compare cancellation policy vs comp set.
 - Simulate impact of payment window = 5 days.

### Front Desk
- Print shift handover notes.
- Send wake-up call reminders to all guests who requested.
- Issue new key card for room 101.
- Assign all arrivals today to available rooms.
- Auto-assign rooms for tomorrow's arrivals.
- Mark guest +60123456789 as no-show.
 - Show me all guests with special accessibility needs.
 - Early check-in candidates today.
 - Late check-out requests pending approval.
 - Expected arrivals without room assignment.

### Staff
- Assign Alice Tan to the night shift.
- Approve leave request for John Lee.
- Send training reminder to all housekeeping staff.
- Blacklist staff for policy violations.
 - Which staff have worked overtime this month?
 - Who is due for performance review?
 - Show me staff with the highest guest ratings.
 - Which staff are available for emergency call-in?
 - List all new hires in the last 90 days.
 - Who is the most tenured staff member?
 - Next week overtime risk by department.
 - Training compliance gaps (housekeeping).
 - Shift optimization suggestion (night coverage).

### Housekeeping
- Restock towels in all unprepared rooms.
- Send housekeeping report to manager.
- Show me all overdue housekeeping tasks.
- Show me rooms that failed inspection.
 - Turnaround time outliers today.
 - Rooms overdue vs SLA (60 min) right now.
 - Supplies usage anomalies (last 7 days).

### Payment
- Send payment reminder to guest +60123456789.
- Generate invoice for all bookings this week.
- List all payment disputes.
 - Which guests have outstanding balances?
 - List all payments received today.
 - Which bookings have partial payments?
 - Show me the average payment processing time.
 - Which guests have used loyalty points for payment?
 - Expiring payment windows (next 72h).
 - Refunds pending manager review.
 - Chargeback risk bookings (heuristics).

### Maintenance
- Schedule HVAC inspection for all rooms.
- Close all resolved maintenance requests.
- Send maintenance report to engineering.
 - List all urgent maintenance issues.
 - Show me maintenance costs by category.
 - Which maintenance requests are overdue?
 - List all rooms with water leakage reports.
 - Show me all completed maintenance tasks this month.
 - Which equipment is due for inspection?
 - List all rooms with HVAC issues.
 - Open tickets >48h, sort by urgency.
 - Parts restock needs (top 10).
 - Energy use anomalies by room stack.

### Concierge
- Send local event guide to all guests.
- Show me local emergency contacts.
- Show me the weather forecast for the next 5 days.
  - What events are happening in the city this weekend?
  - What’s the best way to get to the airport?

### Analytics & Reports
- Send daily performance summary to management.
- Export guest satisfaction trends to Excel.
 - Which months have the highest occupancy rates?
 - Which marketing campaigns drove the most bookings?
 - Which channels have the highest booking conversion rates?
 - Owner summary one-pager (weekly).
 - Citywide pace vs comp set (this month).
 - Segment mix shift MoM.

### Miscellaneous
- Restart the PMS server.
- Enable integration with new payment gateway.
- Update system to the latest version.
- Generate a custom report for my manager.
- Archive old guest records.
- Purge test data from the system.
- Integration health check (OTA/payment).
- Data quality audit (missing keys/formats).
- SLA dashboard (all teams).
### PMS ADVANCED
 - Reserve spa appointments for VIP guests.
 - Top-converting packages last month.
 - Upsell candidates for today’s arrivals.
 - Transport demand heatmap for weekend events.
 - City guide and shopping with advanced filters and partnerships.

### AI-PMS (In-Progress)
 - VIP arrivals detection with special requests (cross-tool reasoning).
 - Overbook risk detection and mitigation plans.
 - Churn-risk guest identification and retention playbooks.
 - Preventive maintenance forecasting and auto-scheduling.
 - Sentiment-driven service recovery suggestions.
 

---

## Future (Not Yet Supported / Requires New Tools or Features)

### PMS BASIC (Core)

### Booking
- Book three rooms for a wedding party, all next to each other. (Full adjacency logic, group booking UI)
- Book a room for a guest who hasn’t checked in yet.
- What’s the maximum stay allowed for a guest?
 - Create and manage group blocks with rooming lists and allotments.
 - Build packages with inclusions and split revenue correctly.

### Guest
- Blacklist guest for repeated violations. (Full guest risk scoring, automated enforcement)
- Who are our top-spending guests this year?
- Which guests have birthdays this month?
- Who has a pet-friendly booking this week?
 - Real-time expectation contract enforcement (quiet room, pillow type, etc.).
 - In-stay micro-personalization (lighting/temperature/amenities) by context.
 - Live sentiment radar with proactive recovery playbooks.
 - Guest digital twin shared across departments (preferences, accessibility, do-not rules).
 - Multi-stage CRM journeys and campaigns with advanced segmentation.
 - Partner commissions, vouchers, and promo code governance.

### Room
- Enable smart home features for room 501. (IoT/smart room integration)
- Which rooms have the highest guest satisfaction scores?
- Show me all rooms with smart home features.
- Which rooms are closest to the elevator?
- Which rooms have been flagged for maintenance more than twice this year?
 - Comfort vs energy optimization with explainable tradeoffs.
 - Live comfort monitoring (noise/temperature) with alerts.

### Inventory
- How many extra beds are available right now?
- Which room types are most often overbooked?
- What’s the average occupancy rate for Deluxe Rooms?
- Which room types have the highest cancellation rates?
- Show me inventory for conference rooms.
- How many baby cots are available?
- Which inventory items are most requested by guests?
 - Two-way OTA/GDS distribution with content/rates/restrictions parity.
 - Two-way OTA/GDS distribution with rate/restriction sync and parity monitoring.

### Policy
- Which policies are most frequently cited in guest complaints?
- What’s the policy for lost and found items?
- Show me the smoking policy for all properties.
 - Policy reasoner: simulate changes (cancellation/deposits) with impact on satisfaction and revenue.
 - Multi-currency, tax breakdowns, and country-specific e-invoicing/fiscalization.

### Front Desk
- Who is scheduled for front desk duty tomorrow?
- Who is the manager on duty right now?
- Show me all guests with special accessibility needs.
 - Arrival choreography (room prep/bell/concierge/payment/ID) per ETA wave.
 - Dynamic upgrade fairness across LTV and fairness constraints.
 - Accessibility guardian (verify and enforce accessibility promises end-to-end).
 - Night audit workflow and automated postings.
 - Folio splits, transfers, and charge routing rules.

### Staff
- Which staff have worked overtime this month?
- Who is due for performance review?
- Show me staff with the highest guest ratings.
- Which staff are available for emergency call-in?
- List all new hires in the last 90 days.
- Who is the most tenured staff member?
 - Role-aware copilots (HK/ENG/CON/F&B) with action-first guidance and auto-logging.
 - Knowledge-on-tap grounded in hotel SOPs with one-tap execution.
 - Auto shift generation with constraints and fatigue risk modeling.

### Housekeeping
- Who is assigned to clean the penthouse today?
- Show me the cleaning schedule for tomorrow.
- Which rooms have minibar restocking requests?
- List all rooms with do-not-disturb status.
- Which rooms need extra towels delivered?
 - Predictive readiness (forecast clean times, staffing, bottlenecks; JIT assists).
 - Intelligent inspections (camera/IoT quality scoring and explainable fails).
 - Anomaly mining (detergent overuse, repetitive defects) with auto ticketing.
 - Housekeeping boards by zones/shifts and mobile workflows.
 - Laundry/linen counts and Lost & Found tracking.

### Payment
- Which guests have outstanding balances?
- List all payments received today.
- Which bookings have partial payments?
- Show me the average payment processing time.
- Which guests have used loyalty points for payment?
 - PCI DSS grade flows with 3‑D Secure and automated chargeback handling.
 - PCI-compliant workflows, 3-D Secure, and chargeback automation.

### Maintenance
- Show me all completed maintenance tasks this month.
- Which equipment is due for inspection?
- List all rooms with HVAC issues.
 - Predictive failures (HVAC/assets) from sensor data with pre-emptive scheduling.
 - CMMS parts inventory with reorder automation.

### Concierge
 - What events are happening in the city this weekend?
 - What’s the best way to get to the airport?
 - Experience composer: in-house + local itineraries optimized to guest goals/time.
 - Real-time demand shaping for spa/gym/restaurant to reduce queues.
 - LLM-curated itineraries with live bookings and in-house service pricing.

### Analytics & Reports
- Which months have the highest occupancy rates?
- Which marketing campaigns drove the most bookings?
- Which channels have the highest booking conversion rates?
 - Hotel knowledge graph (rooms/assets/staff/policies/incidents/guest context).
 - Digital twin simulation for what-if (price/policy/staffing) before deploy.
 - Comp-set benchmarking with pickup/pace and price elasticity insights.
 - Dynamic pricing recommendations and pickup/pace dashboards.
 - Multi-property benchmarking and portfolio analytics.

### Miscellaneous

### PMS ADVANCED
 - Group blocks with rooming lists and banquet/event orders (BEO).
 - Multi-property central reservations and portfolio analytics.
 - Deep AR/AP & City Ledger; accounting integrations.
  - Spa/golf/banquet integrations and packaging.

### AI-PMS (Future)
 - Hotel knowledge graph and digital twin for what-if simulation.
 - Contextual pricing with elasticity and comp-set integration.
 - Reasoning fabric orchestrating multi-department workflows.
 - Consent-first personalization with explainable decisions.
- List all integrations currently enabled.
- What’s the average response time for guest requests?
- Show me a summary of today’s operations.
- What’s the most common guest complaint? 
 - Consent-first personalization with transparent “why” and preference scopes.
 - Privacy-preserving/federated learning across properties.
 - Agent safety rails: constraint solver + policy engine to prevent unsafe actions.
 - Granular RBAC, approval workflows, full audit trails, GDPR tooling.
 - Finance: City ledger/AR/AP and exports to Xero/Sage/NetSuite.

[END ORIGINAL]
