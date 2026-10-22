import json
from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator

if __name__ == '__main__':
    agent = LEONMetaOrchestrator()
    user_message = "add room 160 to riverside deluxe"
    print("\n=== TEST: User Message for Room_Type ===")
    print(f"User: {user_message}")
    response = agent.process_request(user_message)
    print("Agent Response:")
    print(response)