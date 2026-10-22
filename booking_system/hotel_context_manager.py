#!/usr/bin/env python3
"""
Manager Signup/Signin System - Pure Authentication Flow
- Signup: Register manager profile and store in MongoDB
- Signin: Load manager profile to manager_context.json for session
- Natural keys only (manager_phone), no artificial IDs
- MongoDB-only, pure authentication logic (no LLM)
"""
import os
import jwt  # PyJWT
from datetime import datetime, timezone
from typing import Dict, Any, List
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv
from passlib.hash import bcrypt

# Load environment
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(root_dir, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path, encoding='utf-8')

# --- MongoDB Connection for Manager Profiles ---
MONGO_URL = os.environ.get('MONGO_URL')
if not MONGO_URL:
    print("⚠️ MONGO_URL not found")

try:
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    # Test connection
    client.admin.command('ping')
    print("✅ Connected to MongoDB successfully")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")
    print("⚠️ Please ensure MongoDB is running or set MONGO_URL in .env file")
    print("💡 For local development, install MongoDB or use MongoDB Atlas")
    # Create a mock client for development
    client = None

if client:
    # Manager profiles collection
    manager_db = client.leon_manager_system
    manager_col = manager_db.manager_collection
else:
    # Mock collection for development
    manager_col = None

# --- Index Management ---
def setup_indexes():
    """Setup indexes with proper conflict handling"""
    if manager_col is None:
        print("⚠️ MongoDB not available - skipping index setup")
        return
        
    try:
        # Get existing indexes
        existing_indexes = manager_col.list_indexes()
        existing_index_names = [idx['name'] for idx in existing_indexes]
        
        # Define required indexes
        required_indexes = [
            ('manager_phone', 'manager_phone_1'),
            ('manager_name', 'manager_name_1'), 
            ('hotel_name', 'hotel_name_1')
        ]
        
        for field, index_name in required_indexes:
            if index_name not in existing_index_names:
                # Create new unique index
                manager_col.create_index([(field, ASCENDING)], unique=True, name=index_name)
                print(f"✅ Created unique index: {index_name}")
            else:
                # Check if existing index is unique
                existing_index = next((idx for idx in existing_indexes if idx['name'] == index_name), None)
                if existing_index and not existing_index.get('unique', False):
                    # Drop non-unique index and recreate as unique
                    manager_col.drop_index(index_name)
                    manager_col.create_index([(field, ASCENDING)], unique=True, name=index_name)
                    print(f"🔄 Updated index to unique: {index_name}")
                else:
                    print(f"✅ Index already exists: {index_name}")
                    
    except Exception as e:
        print(f"⚠️ Index setup warning: {e}")
        # Continue without failing the application

# Setup indexes
setup_indexes()

JWT_SECRET = os.getenv('JWT_SECRET', 'dev-secret')
JWT_ALGO = 'HS256'

# --- CM_code Utility ---
def get_cm_code_from_session() -> str:
    """Get CM_code from session file if present, else return default."""
    try:
        # This function is no longer needed as JWT is used.
        # Keeping it for now, but it will always return 'sample-pms'
        return 'sample-pms'
    except Exception:
        return 'sample-pms'

def set_cm_code_in_session(cm_code: str) -> bool:
    """Set or update CM_code in session file."""
    try:
        # This function is no longer needed as JWT is used.
        # Keeping it for now, but it will always return True
        return True
    except Exception as e:
        print(f'❌ Error setting CM_code: {e}')
        return False

# --- Manager Signup Logic ---
def signup_manager(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Signup: Register manager profile and store in MongoDB
    """
    if manager_col is None:
        return {"success": False, "message": "MongoDB not available - cannot register manager"}
        
    try:
        # Validate required fields
        required_fields = ['manager_name', 'manager_phone', 'hotel_name', 'role']
        for field in required_fields:
            if field not in data:
                return {"success": False, "message": f"Missing required field: {field}"}
        
        # Validate phone format (international E.164)
        phone = str(data.get('manager_phone', '')).strip()
        try:
            import re
            if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
                return {"success": False, "message": "Manager phone must be international format (+[countrycode][number])"}
            data['manager_phone'] = phone
        except Exception:
            return {"success": False, "message": "Invalid phone number"}
        
        # Check if any of the 3 primary authenticators already exist
        existing_phone = manager_col.find_one({"manager_phone": data['manager_phone']})
        if existing_phone:
            return {"success": False, "message": f"Manager with phone {data['manager_phone']} already registered"}
        
        existing_name = manager_col.find_one({"manager_name": data['manager_name']})
        if existing_name:
            return {"success": False, "message": f"Manager with name {data['manager_name']} already registered"}
        
        existing_hotel = manager_col.find_one({"hotel_name": data['hotel_name']})
        if existing_hotel:
            return {"success": False, "message": f"Hotel {data['hotel_name']} already has a manager registered"}
        
        # Set defaults
        if 'nationality' not in data:
            data['nationality'] = 'Malaysian'
        # Primary language required
        primary_lang_in = str(data.get('primary_language') or '').strip()
        if not primary_lang_in:
            return {"success": False, "message": "Primary language is required"}
        # Normalize "EN - English" → "English"
        def _norm_lang(s: str) -> str:
            try:
                s = str(s or '').strip()
                if ' - ' in s:
                    return s.split(' - ', 1)[1].strip()
                return s
            except Exception:
                return str(s or '').strip()
        primary_lang = _norm_lang(primary_lang_in)
        # Store only primary language
        data['primary_language'] = primary_lang
        # Validate hotel_timezone if provided (must be IANA)
        hotel_tz = data.get('hotel_timezone')
        if hotel_tz:
            try:
                # Validate IANA string by attempting ZoneInfo load
                from zoneinfo import ZoneInfo  # Python 3.9+
                _ = ZoneInfo(hotel_tz)
            except Exception:
                return {"success": False, "message": f"Invalid hotel_timezone: {hotel_tz}"}
        # Validate hotel_currency if provided (ISO 4217-like 3-letter)
        hotel_cur = data.get('hotel_currency')
        if hotel_cur:
            code = str(hotel_cur).upper()
            if len(code) != 3 or not code.isalpha():
                return {"success": False, "message": f"Invalid hotel_currency: {hotel_cur}"}
        
        # Password required and hashed
        if 'password' not in data or not data['password']:
            return {"success": False, "message": "Password is required"}
        data['password'] = bcrypt.hash(data['password'])
        
        # Add language expertise
        language_contexts = {
            'English': ['hotel operations', 'guest service', 'business communication'],
            'Bahasa Malaysia': ['komunikasi tempatan', 'perkhidmatan tamu', 'operasi hotel'],
            'Mandarin': ['客户服务', '酒店运营', '商务沟通'],
            'Tamil': ['விருந்தோம்பல்', 'होटल संचालन', 'வணிக தொடர்பு'],
            'Arabic': ['خدمة الضيوف', 'عمليات الفندق', 'التواصل التجاري'],
            'Japanese': ['ホテル運営', '顧客サービス', 'ビジネス コミュニケーション'],
            'Korean': ['호텔 운영', '고객 서비스', '비즈니스 커뮤니케이션'],
            'Thai': ['การบริการแขก', 'การดำเนินงานโรงแรม', 'การสื่อสารทางธุรกิจ'],
            'Indonesian': ['operasi hotel', 'layanan tamu', 'komunikasi bisnis'],
            'French': ['opérations hôtelières', 'service client', 'communication d\'affaires'],
            'German': ['Hotelbetrieb', 'Kundenservice', 'Geschäftskommunikation'],
            'Spanish': ['operaciones hoteleras', 'servicio al cliente', 'comunicación empresarial']
        }
        
        data['language_expertise'] = {}
        try:
            lang = data['primary_language']
            if lang in language_contexts:
                data['language_expertise'][lang] = language_contexts[lang]
            else:
                data['language_expertise'][lang] = ['hotel operations', 'guest service']
        except Exception:
            pass
        
        # Add cultural context
        cultural_contexts = {
            'Malaysian': {
                'greeting_style': 'formal_respectful',
                'business_hours': '9:00-18:00',
                'cultural_notes': ['halal_awareness', 'multicultural_sensitivity', 'local_customs']
            },
            'Singaporean': {
                'greeting_style': 'formal_efficient',
                'business_hours': '9:00-18:00',
                'cultural_notes': ['multicultural_awareness', 'efficiency_focus', 'service_excellence']
            },
            'Indonesian': {
                'greeting_style': 'warm_respectful',
                'business_hours': '8:00-17:00',
                'cultural_notes': ['hospitality_focus', 'family_values', 'religious_sensitivity']
            },
            'Thai': {
                'greeting_style': 'respectful_humble',
                'business_hours': '9:00-18:00',
                'cultural_notes': ['service_mindset', 'respect_hierarchy', 'smile_culture']
            },
            'International': {
                'greeting_style': 'professional_adaptive',
                'business_hours': '9:00-17:00',
                'cultural_notes': ['cultural_adaptability', 'international_standards', 'diverse_awareness']
            }
        }
        
        data['cultural_context'] = cultural_contexts.get(data['nationality'], cultural_contexts['International'])
        # Store canonical UTC timestamps; hotel_timezone is stored separately
        data['created_at'] = datetime.now(timezone.utc)
        data['updated_at'] = datetime.now(timezone.utc)
        data['status'] = 'active'
        
        # Optionally store CM_code if provided
        if 'CM_code' in data:
            data['CM_code'] = data['CM_code']
        
        # Insert into MongoDB
        manager_col.insert_one(data)
        
        return {
            "success": True,
            "message": f"Manager {data['manager_name']} registered successfully",
            "manager_phone": data['manager_phone'],
            "manager_name": data['manager_name'],
            "hotel_name": data['hotel_name']
        }
        
    except Exception as e:
        return {"success": False, "message": f"Signup failed: {str(e)}"}

# --- Manager Signin Logic (3 Primary Authenticators) ---
def signin_manager_by_phone(manager_phone: str, password: str) -> Dict[str, Any]:
    """
    Signin: Load manager profile by phone number
    """
    if manager_col is None:
        return {"success": False, "message": "MongoDB not available - cannot sign in"}
        
    try:
        # Find manager profile by phone
        profile = manager_col.find_one({"manager_phone": manager_phone})
        if not profile:
            return {"success": False, "message": f"Manager with phone {manager_phone} not found"}
        if not bcrypt.verify(password, profile['password']):
            return {"success": False, "message": "Incorrect password"}
        
        return _process_signin(profile, "phone")
        
    except Exception as e:
        return {"success": False, "message": f"Signin by phone failed: {str(e)}"}

def signin_manager_by_name(manager_name: str, password: str) -> Dict[str, Any]:
    """
    Signin: Load manager profile by name
    """
    if manager_col is None:
        return {"success": False, "message": "MongoDB not available - cannot sign in"}
        
    try:
        # Find manager profile by name
        profile = manager_col.find_one({"manager_name": manager_name})
        if not profile:
            return {"success": False, "message": f"Manager with name {manager_name} not found"}
        if not bcrypt.verify(password, profile['password']):
            return {"success": False, "message": "Incorrect password"}
        
        return _process_signin(profile, "name")
        
    except Exception as e:
        return {"success": False, "message": f"Signin by name failed: {str(e)}"}

def signin_manager_by_hotel(hotel_name: str, password: str) -> Dict[str, Any]:
    """
    Signin: Load manager profile by hotel name
    """
    if manager_col is None:
        return {"success": False, "message": "MongoDB not available - cannot sign in"}
        
    try:
        # Find manager profile by hotel
        profile = manager_col.find_one({"hotel_name": hotel_name})
        if not profile:
            return {"success": False, "message": f"Manager for hotel {hotel_name} not found"}
        if not bcrypt.verify(password, profile['password']):
            return {"success": False, "message": "Incorrect password"}
        
        return _process_signin(profile, "hotel")
        
    except Exception as e:
        return {"success": False, "message": f"Signin by hotel failed: {str(e)}"}

def _process_signin(profile: Dict[str, Any], auth_method: str) -> Dict[str, Any]:
    """
    Stateless JWT-based signin: return JWT with manager context (no file I/O)
    """
    try:
        profile = dict(profile)
        profile.pop('_id', None)
        profile.pop('password', None)
        # Convert datetime objects to strings
        for k, v in profile.items():
            if hasattr(v, 'isoformat'):
                profile[k] = v.isoformat()
        # Generate JWT
        # Ensure hotel_timezone / hotel_currency / nationality / primary_language are included in JWT
        if 'hotel_timezone' not in profile:
            profile['hotel_timezone'] = None
        if 'hotel_currency' not in profile:
            profile['hotel_currency'] = None
        if 'nationality' not in profile:
            profile['nationality'] = None
        if 'primary_language' not in profile:
            profile['primary_language'] = None
        token = jwt.encode(profile, JWT_SECRET, algorithm=JWT_ALGO)
        return {
            "success": True,
            "message": f"Manager {profile['manager_name']} signed in successfully via {auth_method}",
            "manager_name": profile['manager_name'],
            "hotel_name": profile['hotel_name'],
            "role": profile['role'],
            "auth_method": auth_method,
            "token": token
        }
    except Exception as e:
        return {"success": False, "message": f"Session processing failed: {str(e)}"}

# --- Utility Functions ---
def list_all_managers() -> List[Dict[str, Any]]:
    """List all registered managers"""
    if manager_col is None:
        print("❌ MongoDB not available - cannot list managers")
        return []
        
    try:
        managers = list(manager_col.find({}, {'_id': 0}))
        return managers
    except Exception as e:
        print(f"❌ Error listing managers: {e}")
        return []

def check_manager_exists_by_phone(manager_phone: str) -> bool:
    """Check if manager exists by phone"""
    return manager_col.find_one({"manager_phone": manager_phone}) is not None

def check_manager_exists_by_name(manager_name: str) -> bool:
    """Check if manager exists by name"""
    return manager_col.find_one({"manager_name": manager_name}) is not None

def check_manager_exists_by_hotel(hotel_name: str) -> bool:
    """Check if manager exists by hotel"""
    return manager_col.find_one({"hotel_name": hotel_name}) is not None

# --- Interactive Mode Functions ---
def display_menu():
    """Display main menu options"""
    print("\n" + "="*60)
    print("🔐 MANAGER AUTHENTICATION SYSTEM")
    print("="*60)
    print("1. 📝 Sign Up New Manager")
    print("2. 📱 Sign In by Phone")
    print("3. 👤 Sign In by Name")
    print("4. 🏨 Sign In by Hotel")
    print("5. 👤 View Active Manager")
    print("6. 📋 List All Managers")
    print("7. 🔍 Check Manager Exists")
    print("8. 🚪 Sign Out")
    print("9. 🔗 Set/Update CM_code")
    print("10. ❌ Exit")
    print("="*60)

def get_manager_input():
    """Get manager details from user input"""
    print("\n📝 Enter Manager Details:")
    manager_name = input("👤 Manager Name: ").strip()
    manager_phone = input("📞 Phone (+60xxxxxxxxx): ").strip()
    hotel_name = input("🏨 Hotel Name: ").strip()
    role = input("👔 Role: ").strip()
    nationality = input("🌍 Nationality (default: Malaysian): ").strip() or "Malaysian"
    primary_lang = input("🗣️ Primary Language (e.g., English, Mandarin): ").strip() or "English"
    
    return {
        'manager_name': manager_name,
        'manager_phone': manager_phone,
        'hotel_name': hotel_name,
        'role': role,
        'nationality': nationality,
        'primary_language': primary_lang
    }

def handle_signup():
    """Handle manager signup process"""
    print("\n📝 MANAGER SIGNUP")
    print("-" * 30)
    
    data = get_manager_input()
    
    if not all([data['manager_name'], data['manager_phone'], data['hotel_name'], data['role']]):
        print("❌ All fields are required!")
        return
    
    result = signup_manager(data)
    
    if result['success']:
        print(f"\n✅ {result['message']}")
        print(f"📞 Phone: {result['manager_phone']}")
        print(f"🏨 Hotel: {result['hotel_name']}")
    else:
        print(f"\n❌ {result['message']}")

def handle_signin_by_phone():
    """Handle signin by phone"""
    print("\n📱 SIGN IN BY PHONE")
    print("-" * 30)
    
    phone = input("📞 Enter phone number (+60xxxxxxxxx): ").strip()
    password = input("🔑 Enter password: ").strip()
    
    if not phone:
        print("❌ Phone number is required!")
        return
    if not password:
        print("❌ Password is required!")
        return
    
    result = signin_manager_by_phone(phone, password)
    
    if result['success']:
        print(f"\n✅ {result['message']}")
        print(f"👤 Manager: {result['manager_name']}")
        print(f"🏨 Hotel: {result['hotel_name']}")
        print(f"👔 Role: {result['role']}")
    else:
        print(f"\n❌ {result['message']}")

def handle_signin_by_name():
    """Handle signin by name"""
    print("\n👤 SIGN IN BY NAME")
    print("-" * 30)
    
    name = input("👤 Enter manager name: ").strip()
    password = input("🔑 Enter password: ").strip()
    
    if not name:
        print("❌ Manager name is required!")
        return
    if not password:
        print("❌ Password is required!")
        return
    
    result = signin_manager_by_name(name, password)
    
    if result['success']:
        print(f"\n✅ {result['message']}")
        print(f"👤 Manager: {result['manager_name']}")
        print(f"🏨 Hotel: {result['hotel_name']}")
        print(f"👔 Role: {result['role']}")
    else:
        print(f"\n❌ {result['message']}")

def handle_signin_by_hotel():
    """Handle signin by hotel"""
    print("\n🏨 SIGN IN BY HOTEL")
    print("-" * 30)
    
    hotel = input("🏨 Enter hotel name: ").strip()
    password = input("🔑 Enter password: ").strip()
    
    if not hotel:
        print("❌ Hotel name is required!")
        return
    if not password:
        print("❌ Password is required!")
        return
    
    result = signin_manager_by_hotel(hotel, password)
    
    if result['success']:
        print(f"\n✅ {result['message']}")
        print(f"👤 Manager: {result['manager_name']}")
        print(f"🏨 Hotel: {result['hotel_name']}")
        print(f"👔 Role: {result['role']}")
    else:
        print(f"\n❌ {result['message']}")

def handle_view_active_manager():
    """Handle viewing active manager"""
    print("\n👤 ACTIVE MANAGER")
    print("-" * 30)
    
    # This function is no longer needed as JWT is used.
    # Keeping it for now, but it will always print "No active manager in session"
    print("❌ No active manager in session")

def handle_list_managers():
    """Handle listing all managers"""
    print("\n📋 ALL REGISTERED MANAGERS")
    print("-" * 30)
    
    managers = list_all_managers()
    
    if managers:
        for i, manager in enumerate(managers, 1):
            print(f"{i}. {manager['manager_name']} ({manager['manager_phone']})")
            print(f"   🏨 {manager['hotel_name']} | 👔 {manager['role']}")
            print()
        print(f"Total: {len(managers)} managers")
    else:
        print("❌ No managers registered")

def handle_check_manager():
    """Handle checking if manager exists"""
    print("\n🔍 CHECK MANAGER EXISTS")
    print("-" * 30)
    print("1. Check by Phone")
    print("2. Check by Name")
    print("3. Check by Hotel")
    
    choice = input("Choose method (1-3): ").strip()
    
    if choice == "1":
        phone = input("📞 Enter phone number: ").strip()
        exists = check_manager_exists_by_phone(phone)
        print(f"✅ Manager exists" if exists else "❌ Manager not found")
    
    elif choice == "2":
        name = input("👤 Enter manager name: ").strip()
        exists = check_manager_exists_by_name(name)
        print(f"✅ Manager exists" if exists else "❌ Manager not found")
    
    elif choice == "3":
        hotel = input("🏨 Enter hotel name: ").strip()
        exists = check_manager_exists_by_hotel(hotel)
        print(f"✅ Manager exists" if exists else "❌ Manager not found")
    
    else:
        print("❌ Invalid choice!")

def handle_signout():
    """Handle manager signout"""
    print("\n🚪 SIGN OUT")
    print("-" * 30)
    
    # This function is no longer needed as JWT is used.
    # Keeping it for now, but it will always return {"success": True, "message": "Manager signed out successfully"}
    print("✅ Manager signed out successfully")

def handle_set_cm_code():
    """Handle setting/updating CM_code in session file"""
    print("\n🔗 SET/UPDATE CM_code")
    print("-" * 30)
    # This function is no longer needed as JWT is used.
    # Keeping it for now, but it will always print "No change made."
    print("No change made.")

def main():
    """Main interactive loop"""
    print("🔐 MANAGER AUTHENTICATION SYSTEM")
    print("Pure authentication logic - no LLM involved")
    print("=" * 60)
    
    while True:
        display_menu()
        
        choice = input("\nEnter your choice (1-10): ").strip()
        
        if choice == "1":
            handle_signup()
        elif choice == "2":
            handle_signin_by_phone()
        elif choice == "3":
            handle_signin_by_name()
        elif choice == "4":
            handle_signin_by_hotel()
        elif choice == "5":
            handle_view_active_manager()
        elif choice == "6":
            handle_list_managers()
        elif choice == "7":
            handle_check_manager()
        elif choice == "8":
            handle_signout()
        elif choice == "9":
            handle_set_cm_code()
        elif choice == "10":
            print("\n👋 Goodbye!")
            break
        else:
            print("❌ Invalid choice! Please enter 1-10.")
        
        input("\nPress Enter to continue...")

if __name__ == '__main__':
    main()