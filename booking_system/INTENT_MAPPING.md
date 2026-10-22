# LEON PMS: Intent Mapping to Tools

This document maps typical user intents in a Property Management System (PMS) to the corresponding tools available in LEON PMS, as registered in `leon_meta_orchestrator.py`. Each tool is designed to serve a specific business intent, enabling the agent to fulfill user requests naturally and robustly.

---

## Booking Intents
- **Create a booking**
  - Example queries:
    - "I want to book a Deluxe Suite for next Friday."
    - "Reserve a room for guest John Doe from July 10 to July 12."
  - `create_booking_tool`: Create a new booking for a guest, including all required details.
- **Check room availability**
  - Example queries:
    - "Is a Triple Suite available this weekend?"
    - "Do you have any rooms open for tomorrow night?"
  - `check_room_availability_tool`: Check if a specific room type is available for a given date.
- **List bookable room types**
  - Example queries:
    - "What room types can I book?"
    - "Show me all available room categories."
  - `list_bookable_room_types_tool`: Show all room types available for booking.
- **Get booking details**
  - Example queries:
    - "Show me the details for booking #12345."
    - "What are the details of my last reservation?"
  - `get_booking_details_tool`: Retrieve details of a specific booking.
- **Modify a booking**
  - Example queries:
    - "Change my booking to start on July 15 instead."
    - "Update my reservation to 2 rooms."
  - `modify_booking_tool`: Update booking details (dates, room type, status, etc.).
- **Cancel a booking**
  - Example queries:
    - "Cancel my booking for next week."
    - "I need to cancel reservation #54321."
  - `cancel_booking_tool`: Cancel an existing booking.
- **Search bookings**
  - Example queries:
    - "Find all bookings for John Doe."
    - "Show me all confirmed bookings for August."
  - `search_bookings_tool`: Find bookings by criteria (room type, guest, status, date range).
- **Change booking dates**
  - Example queries:
    - "Move my booking to start on July 20."
    - "Change my stay from 2 nights to 3 nights."
  - `change_booking_dates_tool`: Change the check-in date or number of nights for a booking.
- **Get booking analytics**
  - Example queries:
    - "Show me booking trends for the last month."
    - "How many bookings did we have last week?"
  - `get_booking_analytics_tool`: View analytics for bookings in a date range.
- **Get revenue forecast**
  - Example queries:
    - "Forecast our revenue for the next 30 days."
    - "What is the expected revenue for July?"
  - `get_revenue_forecast_tool`: Forecast revenue based on bookings.
- **Analyze booking patterns**
  - Example queries:
    - "Analyze our booking patterns for the past quarter."
    - "What are the most popular check-in days?"
  - `analyze_booking_patterns_tool`: Analyze booking trends for strategic insights.

## Guest Intents
- **Create guest profile**
  - Example queries:
    - "Add a new guest named Jane Smith."
    - "Register guest +60123456789."
  - `create_guest_profile_tool`: Add a new guest to the system.
- **Read guest profile**
  - Example queries:
    - "Show me the profile for guest John Doe."
    - "Get details for guest +60123456789."
  - `read_guest_profile_tool`: View details of a guest profile.
- **Update guest profile**
  - Example queries:
    - "Update Jane Smith's email address."
    - "Change the phone number for guest John Doe."
  - `update_guest_profile_tool`, `update_guest_field_tool`: Edit guest information.
- **Delete guest profile**
  - Example queries:
    - "Delete guest profile for +60123456789."
    - "Remove John Doe from the guest list."
  - `delete_guest_profile_tool`: Remove a guest from the system.
- **Search guests**
  - Example queries:
    - "Find all guests from Singapore."
    - "Show me all VIP guests."
  - `read_guests_by_criteria_tool`, `read_guest_profiles_tool`: Find guests by criteria.
- **Update loyalty program**
  - Example queries:
    - "Upgrade John Doe to Gold status."
    - "Update loyalty points for guest +60123456789."
  - `update_loyalty_program`: Update guest loyalty status.
- **Track guest communication**
  - Example queries:
    - "Log a call with guest Jane Smith."
    - "Record an email sent to John Doe."
  - `track_guest_communication_tool`: Log guest communications.
- **Guest analytics**
  - Example queries:
    - "Show guest analytics for the last quarter."
    - "How many repeat guests did we have last year?"
  - `read_guest_analytics`: View guest-related analytics.
- **Extract guest info from description**
  - Example queries:
    - "Extract guest info from: 'John Doe, +60123456789, johndoe@email.com'"
    - "Parse this guest description: 'Jane Smith, VIP, Singapore'"
  - `read_guest_information_from_description`: Parse guest info from unstructured text.

## Room Intents
- **Manage room types**
  - Example queries:
    - "Add a new room type: Executive Suite."
    - "Update the description for Deluxe Room."
  - `manage_room_type`: Create, update, or delete room types.
- **Manage room numbers**
  - Example queries:
    - "Add room number 101 to Deluxe Room."
    - "Delete room 202 from Superior Room."
  - `manage_room_number`: Create, update, or delete physical room numbers.
- **Read room info**
  - Example queries:
    - "Show me all room types."
    - "Get details for room 305."
  - `read_room_information_from_description`, `read_room_type`, `read_room_types_by_criteria`, `read_room_portfolio`, `read_room_system_validation`, `read_room_assignment_status`, `read_room_readiness_status`: Various tools for room info and validation.

## Inventory Intents
- **Manage room quantity**
  - Example queries:
    - "Increase inventory for Deluxe Room to 20."
    - "Reduce available Superior Rooms by 2."
  - `create_room_quantity`, `read_room_quantity`, `update_room_quantity`, `delete_room_quantity`: Manage inventory for room types.
- **Manage room pricing**
  - Example queries:
    - "Set the price for Executive Suite to RM500."
    - "Update Deluxe Room price for next weekend."
  - `create_room_price`, `read_room_price`, `update_room_price`: Set and update room prices.
- **List available room types**
  - Example queries:
    - "List all available room types."
    - "What rooms can I book right now?"
  - `read_available_room_types`: Show all available room types.
- **Inventory info from description**
  - Example queries:
    - "Extract inventory info from: '10 Deluxe Rooms, 5 Executive Suites'"
    - "Parse this inventory description: 'Superior Room, 8 available'"
  - `read_inventory_information_from_description`: Parse inventory info from text.

## Policy Intents
- **Read available hotels**
  - Example queries:
    - "List all hotels in the system."
    - "Show me available hotels in Kuala Lumpur."
  - `read_available_hotels_tool`: List all hotels in the system.
- **Read policies**
  - Example queries:
    - "Show me the cancellation policy."
    - "What is the payment policy for bookings?"
  - `read_policies_tool`: View hotel policies.
- **Create/update/delete policy**
  - Example queries:
    - "Add a new refund policy."
    - "Update the payment window to 7 days."
    - "Delete the old cancellation policy."
  - `create_policy_tool`, `update_policy_tool`, `delete_policy_tool`: Manage hotel policies.
- **Read booking policy impact**
  - Example queries:
    - "Analyze the impact of the new refund policy."
    - "What effect does the strict cancellation policy have?"
  - `read_booking_policy_impact`: Analyze impact of booking policies.

## Front Desk Intents
- **Update room assignment**
  - Example queries:
    - "Assign room 101 to John Doe."
    - "Auto-assign rooms for all arrivals today."
  - `update_room_assignment`, `update_room_assignment_auto`: Assign rooms to guests.
- **Read assignment dashboard**
  - Example queries:
    - "Show me the room assignment dashboard."
    - "Which rooms are unassigned?"
  - `read_assignment_dashboard`: View room assignment status.
- **Update room status**
  - Example queries:
    - "Mark room 202 as out of order."
    - "Set room 305 as ready."
  - `update_room_out_of_order_status`, `update_room_available_status`, `update_room_readiness_status`: Update room operational status.
- **Read rooms exceeding threshold**
  - Example queries:
    - "Which rooms are not ready by 2pm?"
    - "List rooms exceeding the preparation threshold."
  - `read_rooms_exceeding_prepared_threshold`: Find rooms not ready in time.
- **Guest check-in/check-out**
  - Example queries:
    - "Check in guest John Doe."
    - "Check out guest +60123456789."
  - `update_guest_check_in_status`, `update_guest_check_out_status`: Manage guest arrivals and departures.
- **Room assignment suggestions**
  - Example queries:
    - "Suggest room assignments for tomorrow's arrivals."
    - "Which rooms are best for VIP guests?"
  - `read_room_assignment_suggestions`: Get suggestions for room assignments.
- **Maintenance issue analysis**
  - Example queries:
    - "Analyze maintenance issues for the past month."
    - "What are the most common maintenance problems?"
  - `read_maintenance_issue_analysis`: Analyze maintenance issues.

## Staff Intents
- **Manage staff profiles**
  - Example queries:
    - "Add a new staff member: Alice Tan."
    - "Update the role for staff +60123456780."
  - `read_staff_profiles_tool`, `create_staff_profile_tool`, `update_staff_profile_tool`, `delete_staff_profile_tool`: Staff management.
- **Read staff by department**
  - Example queries:
    - "List all housekeeping staff."
    - "Show me staff in the front desk department."
  - `read_staff_by_department_tool`: List staff by department.
- **Read available staff**
  - Example queries:
    - "Who is available for the night shift?"
    - "List available staff for maintenance."
  - `read_available_staff_tool`: Find available staff.
- **Staff requirements/skills/performance**
  - Example queries:
    - "What skills does Alice Tan have?"
    - "Analyze staff performance for last quarter."
  - `read_staff_requirements_from_description`, `read_staff_skills_from_description`, `read_staff_performance_analysis`: Analyze staff data.

## Housekeeping Intents
- **Read unprepared rooms**
  - Example queries:
    - "Which rooms are not ready for check-in?"
    - "List all unprepared rooms."
  - `read_unprepared_rooms`: List rooms not ready for guests.
- **Create/update preparation tasks**
  - Example queries:
    - "Create a cleaning task for room 101."
    - "Update the progress of room 202 cleaning."
  - `create_preparation_task`, `update_preparation_progress`: Manage housekeeping tasks.
- **Read preparation tasks/analytics**
  - Example queries:
    - "Show all preparation tasks for today."
    - "Give me housekeeping analytics for last month."
  - `read_preparation_tasks`, `read_housekeeping_analytics`, `read_staff_performance_report`, `read_operational_efficiency_metrics`: Housekeeping analytics and reporting.

## Payment Intents
- **Read/update payment status**
  - Example queries:
    - "Check payment status for booking #12345."
    - "Update payment status to paid for reservation #54321."
  - `read_payment_status_tool`, `update_payment_status_tool`: Payment tracking.
- **Read payment deadlines**
  - Example queries:
    - "When is payment due for booking #12345?"
    - "List all upcoming payment deadlines."
  - `read_payment_deadlines`: View payment deadlines for bookings.

## Maintenance Intents
- **Create/update maintenance requests**
  - Example queries:
    - "Create a maintenance request for room 305."
    - "Update the status of maintenance request #789."
  - `create_maintenance_request`, `update_maintenance_request_assignment`, `update_maintenance_progress`: Maintenance management.
- **Read maintenance requests/dashboard**
  - Example queries:
    - "Show all open maintenance requests."
    - "Display the maintenance dashboard."
  - `read_maintenance_requests`, `read_maintenance_dashboard`: Maintenance tracking and reporting.

## Concierge Intents
- **Read/create local attractions**
  - Example queries:
    - "List local attractions near the hotel."
    - "Add a new attraction: City Art Museum."
  - `read_local_attractions`, `create_local_attraction`: Manage local attraction info.
- **Read concierge system validation**
  - Example queries:
    - "Validate the concierge system data."
    - "Check if all attractions are up to date."
  - `read_concierge_system_validation`: Validate concierge data.
- **Read local restaurants, shopping, events, transport, city guide**
  - Example queries:
    - "List nearby restaurants."
    - "Show me the city guide for Kuala Lumpur."
    - "What events are happening this weekend?"
    - "How do I get to the airport from here?"
  - `read_local_restaurants`, `read_city_guide`, `read_local_shopping`, `read_local_events`, `read_transportation_info`: Concierge info for guests.

## Reports & Analytics
- **Get reports/tools**
  - Example queries:
    - "Generate a monthly occupancy report."
    - "Show me analytics for last quarter."
  - (See `get_report_tools()`): Various reporting and analytics tools for management.

---

This mapping enables the agent to fulfill any typical PMS user intent by invoking the appropriate tool(s). As new intents arise, add or update tools and this mapping accordingly. 