from langgraph.graph import StateGraph, START, END
from booking_system.front_desk_manager import (
    read_room_assignment_tool,
    update_room_assignment_auto,
    update_guest_check_in_status
)
from booking_system.guest_manager import (
    read_guest_profiles_tool,
    create_guest_profile_tool
)
from booking_system.booking_manager import create_booking_tool
from booking_system.room_manager import read_room_type_from_room_number_tool

# Node 1: Availability
def availability_node(state):
    hotel_name = state['hotel_name']
    room_number = state['room_number']
    stay_dates = state['stay_dates']
    # Check for any assignment for the room on any stay_date
    for date in stay_dates:
        res = read_room_assignment_tool({'hotel_name': hotel_name, 'room_number': room_number, 'date_range': date})
        if res and res != '[]' and res != []:
            state['available'] = False
            state['error'] = f"Room {room_number} is not available on {date}"
            return state
    state['available'] = True
    return state

# Node 2: Guest Profile
def guest_profile_node(state):
    guest_phone = state['guest_phone']
    guest_name = state['guest_name']
    hotel_name = state['hotel_name']
    manager_name = state['manager_name']
    profile = read_guest_profiles_tool({'guest_phone': guest_phone})
    if not profile or profile.startswith('❌'):
        result = create_guest_profile_tool({'hotel_name': hotel_name, 'guest_name': guest_name, 'guest_phone': guest_phone, 'manager_name': manager_name})
        if result.startswith('❌'):
            state['guest_profile_ok'] = False
            state['error'] = result
            return state
        state['guest_profile_ok'] = True
        state['guest_profile'] = result
    else:
        state['guest_profile_ok'] = True
        state['guest_profile'] = profile
    return state

# Node: Derive Room Type
def derive_room_type_node(state):
    if state.get('room_type'):
        state['room_type_ok'] = True
        return state
    hotel_name = state['hotel_name']
    room_number = state['room_number']
    result = read_room_type_from_room_number_tool({'hotel_name': hotel_name, 'room_number': room_number})
    if result.startswith('❌'):
        state['room_type_ok'] = False
        state['error'] = result
        return state
    state['room_type'] = result
    state['room_type_ok'] = True
    return state

# Node 3: Booking
def booking_node(state):
    hotel_name = state['hotel_name']
    manager_name = state['manager_name']
    guest_phone = state['guest_phone']
    room_type = state.get('room_type')
    room_number = state['room_number']
    stay_dates = state['stay_dates']
    # Try to infer room_type if not provided
    if not room_type:
        from booking_system.front_desk_manager import intelligent_front_desk
        conn = intelligent_front_desk.get_postgresql_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT room_type FROM room_number_assignments
            WHERE hotel_name = %s AND room_number = %s LIMIT 1
        """, (hotel_name, room_number))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            room_type = row[0]
        else:
            state['booking_ok'] = False
            state['error'] = f"Cannot determine room_type for room {room_number} at {hotel_name}."
            return state
    # Create booking
    booking_result = create_booking_tool({
        'hotel_name': hotel_name,
        'manager_name': manager_name,
        'guest_phone': guest_phone,
        'room_type': room_type,
        'stay_dates': stay_dates,
        'rooms_booked': 1,
        'booking_notes': "WALK-IN CHECK-IN",
        'channel': 'PMS'
    })
    state['booking'] = booking_result
    if isinstance(booking_result, dict) and booking_result.get('status', '').startswith('❌'):
        state['booking_ok'] = False
        state['error'] = booking_result
        return state
    # Extract booking_id from booking_result string
    import re
    booking_id = None
    if isinstance(booking_result, dict):
        booking_id = booking_result.get('booking_id')
    else:
        m = re.search(r"Booking ID: ([^\n]+)", str(booking_result))
        if m:
            booking_id = m.group(1).strip()
    if not booking_id:
        state['booking_ok'] = False
        state['error'] = "Could not determine booking_id from booking result."
        return state
    state['booking_id'] = booking_id
    state['room_type'] = room_type
    state['booking_ok'] = True
    return state

# Node 4: Assignment
def assignment_node(state):
    hotel_name = state['hotel_name']
    manager_name = state['manager_name']
    booking_id = state['booking_id']
    room_number = state['room_number']
    stay_dates = state['stay_dates']
    result = update_room_assignment_auto({'hotel_name': hotel_name, 'manager_name': manager_name, 'booking_id': booking_id, 'room_number': room_number, 'stay_dates': stay_dates})
    state['assignment'] = result
    state['assignment_ok'] = not (isinstance(result, str) and result.startswith('❌'))
    if not state['assignment_ok']:
        state['error'] = result
    return state

# Node 5: Check-In
def checkin_node(state):
    hotel_name = state['hotel_name']
    manager_name = state['manager_name']
    booking_id = state['booking_id']
    room_number = state['room_number']
    result = update_guest_check_in_status({'hotel_name': hotel_name, 'manager_name': manager_name, 'booking_id': booking_id, 'room_number': room_number})
    state['checkin'] = result
    state['checkin_ok'] = not (isinstance(result, str) and result.startswith('❌'))
    if not state['checkin_ok']:
        state['error'] = result
    return state

# Build the LangGraph
walkin_graph = StateGraph(dict)
walkin_graph.add_node("Availability", availability_node)
walkin_graph.add_node("GuestProfile", guest_profile_node)
walkin_graph.add_node("DeriveRoomType", derive_room_type_node)
walkin_graph.add_node("Booking", booking_node)
walkin_graph.add_node("Assignment", assignment_node)
walkin_graph.add_node("CheckIn", checkin_node)

walkin_graph.add_edge(START, "Availability")
walkin_graph.add_conditional_edges("Availability", lambda s: "GuestProfile" if s.get('available', False) else END)
walkin_graph.add_conditional_edges("GuestProfile", lambda s: "DeriveRoomType" if s.get('guest_profile_ok', False) else END)
walkin_graph.add_conditional_edges("DeriveRoomType", lambda s: "Booking" if s.get('room_type_ok', False) else END)
walkin_graph.add_conditional_edges("Booking", lambda s: "Assignment" if s.get('booking_ok', False) else END)
walkin_graph.add_conditional_edges("Assignment", lambda s: "CheckIn" if s.get('assignment_ok', False) else END)
walkin_graph.add_edge("CheckIn", END)

graph = walkin_graph.compile()

# Example usage:
if __name__ == "__main__":
    initial_state = {
        'hotel_name': 'AOE HOTEL',
        'manager_name': 'JOHN HITLER',
        'room_number': '130',
        'guest_name': 'JANE MARY',
        'guest_phone': '+60132299591',
        'stay_dates': ['2025-08-05']
    }
    result = graph.invoke(initial_state)
    print(result)

    # Debug: Print all assignments for the test room, hotel, and stay_date
    