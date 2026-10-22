import sys
import requests
from booking_system.leon_meta_orchestrator import LEONMetaOrchestrator

API_BASE = "http://localhost:8002/api"

def main():
    print("LEON Agent CLI (type 'exit' to quit)")
    jwt_token = None
    agent = LEONMetaOrchestrator()

    # Login flow
    while not jwt_token:
        print("\nPlease log in:")
        phone = input("Manager phone: ").strip()
        password = input("Password: ").strip()
        payload = {
            "auth_type": "phone",
            "auth_value": phone,
            "password": password
        }
        try:
            resp = requests.post(f"{API_BASE}/auth/signin", json=payload)
            data = resp.json()
            if data.get("success") and data.get("token"):
                jwt_token = data["token"]
                print(f"✅ Login successful. Welcome, {data.get('manager_name', phone)}!")
            else:
                print(f"❌ Login failed: {data.get('message', 'Unknown error')}")
        except Exception as e:
            print(f"❌ Error during login: {e}")

    # Chat loop
    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.strip().lower() in ['exit', 'quit']:
                print("Goodbye!")
                break
            response = agent.process_request(user_input, jwt_token=jwt_token)
            print(f"LEON: {response}")
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main() 