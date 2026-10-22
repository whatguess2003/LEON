#!/usr/bin/env python3
"""
BOOKING AGENT - COMPLETE BOOKING SYSTEM WITH DATABASE PERSISTENCE
================================================================

Complete booking system with:
- Session-based booking workflow (search → select → confirm)
- Clean PostgreSQL/MongoDB separation (transactional vs content)
- Guest-centric architecture with meaningful IDs
- Transparent fallback system with partial matching
- Contextual display intelligence
- Complete database persistence (Step 11)
- Payment window logic integration

Architecture:
- PostgreSQL: Transactional data (guest_id, booking_id, dates, amounts, payment_deadline)
- MongoDB: Descriptive data (guest names, hotel context, booking details)
- Shared Primary Key: guest_id (phone number)
"""

import os
import json
import openai
import psycopg2
import psycopg2.extras
from pymongo import MongoClient
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from datetime import datetime, timedelta, date
from decimal import Decimal
import uuid
from .llm_utils import canonical_key

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

class BookingAgent:
    def __init__(self):
        """Initialize with hybrid architecture and complete database persistence"""
        
        # MongoDB connection (Content Engine)
        self.mongo_client = MongoClient(os.getenv('MONGO_URL'))
        self.db = self.mongo_client.ella_hotel_encyclopedia
        self.collection = self.db.room_encyclopedia
        
        # PostgreSQL connection (Computational Engine)
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            print("❌ DATABASE_URL environment variable not set!")
            self.pg_conn = None
            self.pg_cursor = None
        else:
            try:
                self.pg_conn = psycopg2.connect(database_url)
                self.pg_cursor = self.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                print("✅ PostgreSQL connection successful")
            except Exception as e:
                print(f"❌ PostgreSQL connection failed: {e}")
                self.pg_conn = None
                self.pg_cursor = None
        
        # OpenAI setup (legacy)
        self.openai_client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        # Initialize LLM for parsing unstructured booking information
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        # Session management
        self.last_search_results = []
        self.current_session = None
        
        print("🚀 Complete Booking Agent Ready")
        print("   🔄 Session-based workflow")
        print("   📊 PostgreSQL: Transactional data + payment windows")
        print("   🍃 MongoDB: room_encyclopedia + guest context")
        print("   🎯 Guest-centric architecture")
        print("   💾 Complete database persistence")

    def _ensure_guest_exists(self, guest_id, guest_name=None, guest_email=None):
        """Ensure guest exists in database, create if needed"""
        if not self.pg_cursor:
            return False
        
        try:
            # Check if guest exists
            self.pg_cursor.execute(
                "SELECT guest_id FROM guests WHERE guest_id = %s",
                (guest_id,)
            )
            
            if self.pg_cursor.fetchone():
                return True
            
            # Create new guest
            self.pg_cursor.execute("""
                INSERT INTO guests (guest_id)
                VALUES (%s)
            """, (guest_id,))
            
            self.pg_conn.commit()
            print(f"✅ Created new guest: {guest_id}")
            
            # Also create guest profile in MongoDB
            if guest_name:
                self._ensure_guest_profile_mongodb(guest_id, guest_name)
            
            return True
            
        except Exception as e:
            print(f"❌ Guest creation failed: {e}")
            self.pg_conn.rollback()
            return False

    def _ensure_guest_profile_mongodb(self, guest_id, guest_name):
        """Ensure guest profile exists in MongoDB"""
        try:
            collection = self.db['guest_profiles']
            
            # Check if profile exists
            if not collection.find_one({'guest_id': guest_id}):
                # Create guest profile
                profile_data = {
                    '_id': guest_id,
                    'guest_id': guest_id,
                    'guest_name': guest_name,
                    'preferences': {},
                    'booking_history_summary': {
                        'favorite_hotels': [],
                        'preferred_room_types': [],
                        'average_stay_duration': 0
                    },
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }
                
                collection.insert_one(profile_data)
                print(f"✅ Created guest profile: {guest_id}")
                
        except Exception as e:
            print(f"❌ Error creating guest profile: {e}")

    def _llm_extract_intent(self, guest_query):
        """Pure LLM intent extraction - ONLY extract what guest explicitly mentions"""
        system_prompt = """You are a hotel search expert. Extract ONLY what the guest explicitly mentions.

CRITICAL: Do NOT add sentiment criteria unless the guest specifically mentions quality requirements.
For example:
- "room in Bangkok" → NO sentiment criteria (guest didn't mention quality)  
- "clean room in Bangkok" → cleanliness criteria (guest mentioned clean)
- "budget room" → NO sentiment criteria (budget is about price, not quality)

EXACT SENTIMENT FIELDS (20 attributes - use exactly these names):
- cleanliness, comfort, staff, location, wifi, bed, bathroom, view, noise, space
- amenities, service, value, food, business, family, romance, accessibility, parking, fitness

BOOLEAN AMENITIES (for precise filtering):
- wifi, tv, air_conditioning, coffee_machine, mini_bar, safe, balcony, bathtub, shower
- kitchenette, microwave, refrigerator, iron, hair_dryer, telephone, desk, seating_area
- soundproofing, heating, swimming_pool, fitness_center, business_center, parking
- room_service, laundry_service, concierge, spa_services, pet_friendly

CITIES (airport codes): KUL=Kuala Lumpur, BKK=Bangkok, PEN=Penang, LGK=Langkawi, SIN=Singapore

Return JSON with:
{
    "intent_summary": "clear intent description",
    "city_codes": ["BKK"],
    "sentiment_criteria": {"cleanliness": {"min": 8.0}},
    "boolean_amenities": {"wifi": true},
    "showcase_priorities": []
}"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Guest query: '{guest_query}'"}
                ],
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            print(f"🧠 LLM Intent: {json.loads(content)['intent_summary']}")
            return json.loads(content)
            
        except Exception as e:
            print(f"❌ LLM Intent Error: {e}")
            return {
                "intent_summary": "fallback search",
                "city_codes": [],
                "sentiment_criteria": {},
                "boolean_amenities": {},
                "showcase_priorities": []
            }

    def _boolean_filter_mongodb(self, intent_data):
        """STEP 1: Boolean filtering in MongoDB"""
        query_parts = []
        
        # City filtering
        if intent_data.get('city_codes'):
            city_patterns = [f"^{code}" for code in intent_data['city_codes']]
            query_parts.append({
                "room_type_id": {"$regex": "|".join(city_patterns)}
            })
        
        # Boolean amenity filtering
        boolean_amenities = intent_data.get('boolean_amenities', {})
        for amenity, required in boolean_amenities.items():
            if required:
                query_parts.append({f"SPECIFICATIONS.{amenity}": True})
        
        # Sentiment filtering
        sentiment_criteria = intent_data.get('sentiment_criteria', {})
        for attribute, criteria in sentiment_criteria.items():
            if 'min' in criteria:
                query_parts.append({
                    f"SENTIMENT.{attribute}": {"$gte": criteria['min']}
                })
            if 'max' in criteria:
                query_parts.append({
                    f"SENTIMENT.{attribute}": {"$lte": criteria['max']}
                })
        
        # Combine all query parts
        mongo_query = {"$and": query_parts} if query_parts else {}
        
        # Execute MongoDB query
        rooms = list(self.collection.find(mongo_query, {"room_type_id": 1}))
        room_type_ids = [room['room_type_id'] for room in rooms]
        
        print(f"📊 Boolean Filter: {len(room_type_ids)} rooms match criteria")
        return room_type_ids

    def _check_availability_postgresql(self, room_type_ids, check_in=None, check_out=None):
        """STEP 2: Availability checking in PostgreSQL"""
        
        if not self.pg_cursor:
            print("⚠️ PostgreSQL not available - returning all room_type_ids")
            return room_type_ids
        
        # Default dates
        if not check_in:
            check_in = datetime.now().date()
        if not check_out:
            check_out = check_in + timedelta(days=1)
        
        available_room_type_ids = []
        
        for room_type_id in room_type_ids:
            try:
                # Check availability using existing atomic_availability table
                self.pg_cursor.execute("""
                    SELECT COUNT(*) as available_nights
                    FROM atomic_availability 
                    WHERE room_type_id = %s 
                    AND stay_date >= %s 
                    AND stay_date < %s 
                    AND quantity >= 1
                """, (room_type_id, check_in, check_out))
                
                result = self.pg_cursor.fetchone()
                nights_needed = (check_out - check_in).days
                available_nights = result['available_nights'] if result else 0
                
                if available_nights == nights_needed:
                    available_room_type_ids.append(room_type_id)
                    
            except Exception as e:
                print(f"❌ Availability check error for {room_type_id}: {e}")
                available_room_type_ids.append(room_type_id)
        
        print(f"⚡ Availability Filter: {len(available_room_type_ids)} rooms available")
        return available_room_type_ids

    def _transparent_fallback(self, intent_data, check_in=None, check_out=None):
        """Transparent fallback: show partial matches with clear scoring"""
        
        # Start with city filtering - city is NON-NEGOTIABLE
        city_codes = intent_data.get('city_codes', [])
        if city_codes:
            city_query = {"room_type_id": {"$regex": f"^({'|'.join(city_codes)})"}}
            city_rooms = list(self.collection.find(city_query, {"room_type_id": 1}))
            city_room_type_ids = [room['room_type_id'] for room in city_rooms]
            print(f"   🏙️ City Filter: {len(city_room_type_ids)} rooms in {', '.join(city_codes)}")
        else:
            city_room_type_ids = self._get_all_room_type_ids()
        
        # Check availability for city-filtered rooms only
        available_room_type_ids = self._check_availability_postgresql(
            city_room_type_ids, check_in, check_out
        )
        
        if not available_room_type_ids:
            return []
        
        # Get full room data for partial match analysis
        available_rooms = self._get_rooms_full_data(available_room_type_ids)
        
        # Calculate partial match scores
        partial_matches = []
        for room in available_rooms:
            match_score = self._calculate_partial_match(room, intent_data)
            if match_score['total_criteria'] > 0:
                match_score['room_data'] = room
                partial_matches.append(match_score)
        
        # Sort by match percentage
        partial_matches.sort(key=lambda x: x['match_percentage'], reverse=True)
        
        if partial_matches:
            total_criteria = partial_matches[0]['total_criteria']
            print(f"   📊 Transparent Fallback: No rooms match all {total_criteria} criteria")
            print(f"   🎯 Showing partial matches (best alternatives first)")
            self._partial_match_data = partial_matches
        
        return [match['room_data']['room_type_id'] for match in partial_matches]

    def _calculate_partial_match(self, room, intent_data):
        """Calculate how many criteria each room matches"""
        matched_criteria = []
        missing_criteria = []
        total_criteria = 0
        
        # Check boolean amenities
        boolean_amenities = intent_data.get('boolean_amenities', {})
        for amenity, required in boolean_amenities.items():
            if required:
                total_criteria += 1
                room_specs = room.get('SPECIFICATIONS', {})
                if room_specs.get(amenity):
                    matched_criteria.append(f"{amenity.replace('_', ' ').title()}: Available")
                else:
                    missing_criteria.append(f"{amenity.replace('_', ' ').title()}: Not available")
        
        # Check sentiment criteria
        sentiment_criteria = intent_data.get('sentiment_criteria', {})
        for attribute, criteria in sentiment_criteria.items():
            total_criteria += 1
            room_sentiment = room.get('SENTIMENT', {})
            attribute_score = room_sentiment.get(attribute, 0)
            
            # Handle None values from MongoDB - treat as 0
            if attribute_score is None:
                attribute_score = 0
            
            meets_criteria = True
            if 'min' in criteria and attribute_score < criteria['min']:
                meets_criteria = False
            if 'max' in criteria and attribute_score > criteria['max']:
                meets_criteria = False
            
            if meets_criteria:
                matched_criteria.append(f"{attribute.title()}: {attribute_score:.1f} (meets requirement)")
            else:
                if 'min' in criteria:
                    missing_criteria.append(f"{attribute.title()}: {attribute_score:.1f} (need ≥{criteria['min']})")
                else:
                    missing_criteria.append(f"{attribute.title()}: {attribute_score:.1f} (need ≤{criteria['max']})")
        
        matched_count = len(matched_criteria)
        match_percentage = (matched_count / total_criteria * 100) if total_criteria > 0 else 0
        
        return {
            'matched_criteria': matched_criteria,
            'missing_criteria': missing_criteria,
            'matched_count': matched_count,
            'total_criteria': total_criteria,
            'match_percentage': match_percentage
        }

    def search_rooms(self, guest_query, check_in=None, check_out=None):
        """Complete search: Boolean → Availability → Transparent Fallback → Display"""
        print(f"\n🔍 '{guest_query}'")
        
        # Store search context for booking continuity
        self._last_check_in = check_in or datetime.now().date()
        self._last_check_out = check_out or (self._last_check_in + timedelta(days=1))
        self._last_guest_query = guest_query
        
        # Clear previous partial match data
        if hasattr(self, '_partial_match_data'):
            delattr(self, '_partial_match_data')
        
        # STEP 1: LLM Intent Extraction
        intent_data = self._llm_extract_intent(guest_query)
        
        # STEP 2: MongoDB Boolean Filtering
        candidate_room_type_ids = self._boolean_filter_mongodb(intent_data)
        
        # STEP 3: PostgreSQL Availability Checking
        if candidate_room_type_ids:
            available_room_type_ids = self._check_availability_postgresql(
                candidate_room_type_ids, check_in, check_out
            )
        else:
            available_room_type_ids = []
        
        # STEP 4: Transparent Fallback
        if not available_room_type_ids:
            print("   🔍 No perfect matches found - analyzing partial matches")
            available_room_type_ids = self._transparent_fallback(intent_data, check_in, check_out)
        
        # STEP 5: Get room data and display
        if not available_room_type_ids:
            print("   No available rooms found")
            self.last_search_results = []
            return []
        
        available_rooms = self._get_rooms_full_data(available_room_type_ids)
        
        if not available_rooms:
            print("   No room data found in MongoDB")
            self.last_search_results = []
            return []
        
        # STEP 6: Prepare display data
        for room in available_rooms:
            display_data = self._prepare_room_display(room, guest_query)
            room['display_data'] = display_data
            
            # Add partial match info if available
            if hasattr(self, '_partial_match_data'):
                for match_data in self._partial_match_data:
                    if match_data['room_data']['room_type_id'] == room['room_type_id']:
                        room['partial_match_info'] = {
                            'matched': match_data['matched_criteria'],
                            'missing': match_data['missing_criteria'],
                            'score': f"{match_data['matched_count']}/{match_data['total_criteria']}"
                        }
                        break
        
        # STEP 7: Return results and store for booking
        results = available_rooms[:3]
        self.last_search_results = results
        
        # Display results
        is_partial_match = any('partial_match_info' in room for room in results)
        
        if is_partial_match:
            print(f"\n🎯 PARTIAL MATCHES - Showing {len(results)} best alternatives:")
        else:
            print(f"\n🎯 AVAILABLE ROOMS - Showing {len(results)} available:")
        
        for i, room in enumerate(results, 1):
            display = room['display_data']
            hotel_name = room['DIRECTORY']['hotel_name']
            room_type = room['DIRECTORY']['room_type']
            
            if 'partial_match_info' in room:
                match_info = room['partial_match_info']
                match_emoji = "⭐" if match_info['score'].startswith('2') or match_info['score'].startswith('3') else "📍"
                print(f"\n   {i}. {room['room_type_id']}: {hotel_name} - {match_info['score']} MATCH {match_emoji}")
            else:
                print(f"\n   {i}. {room['room_type_id']}: {hotel_name}")
            
            print(f"      {room_type} | {room['DIRECTORY']['city']}")
            print(f"      ✅ AVAILABLE ({self._last_check_in} to {self._last_check_out})")
            
            if 'partial_match_info' in room:
                match_info = room['partial_match_info']
                if match_info['matched']:
                    print(f"      ✅ Matches: {', '.join(match_info['matched'])}")
                if match_info['missing']:
                    print(f"      ❌ Missing: {', '.join(match_info['missing'])}")
            
            if display['specifications']:
                print(f"      Specifications: {', '.join(display['specifications'])}")
            
            if display['sentiments']:
                sentiment_scores = [f"{s['attribute']} {s['score']:.1f}" for s in display['sentiments']]
                print(f"      Sentiments: {', '.join(sentiment_scores)}")
        
        print(f"\n💡 TO SELECT ROOM: Use select_room(choice, guest_name, guest_phone)")
        print(f"   Example: agent.select_room(1, 'John Doe', '+60123456789')")
        
        return results

    def _get_all_room_type_ids(self):
        """Get all room_type_ids from MongoDB"""
        return [room['room_type_id'] for room in self.collection.find({}, {"room_type_id": 1})]
    
    def _get_rooms_full_data(self, room_type_ids):
        """Get full MongoDB data for specified room_type_ids"""
        return list(self.collection.find({"room_type_id": {"$in": room_type_ids}}))

    def _prepare_room_display(self, room_data, guest_query):
        """Prepare room data for display"""
        guest_analysis = self._analyze_guest_query(guest_query)
        spec_descriptions = self._get_specification_descriptions(room_data, guest_analysis)
        sentiment_scores = self._get_relevant_sentiments(room_data, guest_analysis)
        
        return {
            'specifications': spec_descriptions,
            'sentiments': sentiment_scores
        }
    
    def _analyze_guest_query(self, guest_query):
        """Extract what specifications and sentiments guest mentions"""
        query_lower = guest_query.lower()
        
        spec_keywords = {
            'wifi': ['wifi', 'internet', 'connection'],
            'tv': ['tv', 'television', 'smart tv'],
            'bathtub': ['bathtub', 'bath tub', 'soaking tub'],
            'balcony': ['balcony', 'terrace', 'outdoor space'],
            'coffee_machine': ['coffee', 'nespresso', 'coffee machine'],
            'view': ['view', 'city view', 'ocean view', 'garden view'],
            'parking': ['parking', 'valet', 'car park'],
            'swimming_pool': ['pool', 'swimming', 'swim'],
            'fitness_center': ['gym', 'fitness', 'workout'],
            'spa_services': ['spa', 'massage', 'wellness']
        }
        
        sentiment_keywords = {
            'cleanliness': ['clean', 'spotless', 'hygiene', 'sanitary', 'dirty'],
            'comfort': ['comfortable', 'cozy', 'relaxing'],
            'staff': ['service', 'staff', 'helpful', 'friendly'],
            'wifi': ['wifi', 'internet', 'connection', 'fast internet'],
            'noise': ['quiet', 'peaceful', 'noisy', 'sound'],
            'bed': ['bed', 'sleep', 'mattress', 'pillow'],
            'bathroom': ['bathroom', 'shower', 'toilet'],
            'view': ['view', 'scenery', 'outlook'],
            'value': ['value', 'worth', 'price', 'expensive', 'cheap'],
            'location': ['location', 'convenient', 'accessible']
        }
        
        wanted_specs = [spec for spec, keywords in spec_keywords.items() 
                       if any(keyword in query_lower for keyword in keywords)]
        mentioned_sentiments = [sentiment for sentiment, keywords in sentiment_keywords.items()
                               if any(keyword in query_lower for keyword in keywords)]
        
        return {
            'wanted_specs': wanted_specs,
            'mentioned_sentiments': mentioned_sentiments
        }
    
    def _get_relevant_sentiments(self, room_data, guest_analysis):
        """Get sentiment scores if mentioned by guest OR if score <4.0"""
        mentioned_sentiments = guest_analysis['mentioned_sentiments']
        sentiment_data = room_data.get('SENTIMENT', {})
        
        relevant_sentiments = []
        
        # Add mentioned sentiments
        for sentiment in mentioned_sentiments:
            if sentiment in sentiment_data:
                score = sentiment_data[sentiment]
                # Handle None values from MongoDB
                if score is not None:
                    relevant_sentiments.append({
                        'attribute': sentiment,
                        'score': score
                    })
        
        # Add low scores (<4.0) even if not mentioned
        for attribute, score in sentiment_data.items():
            # Handle None values from MongoDB
            if score is not None and score < 4.0 and attribute not in mentioned_sentiments:
                relevant_sentiments.append({
                    'attribute': attribute,
                    'score': score
                })
        
        return relevant_sentiments
    
    def _get_specification_descriptions(self, room_data, guest_analysis):
        """Get specification descriptions for what guest requested"""
        wanted_specs = guest_analysis['wanted_specs']
        specs = room_data.get('SPECIFICATIONS', {})
        
        spec_descriptions = []
        for spec in wanted_specs:
            if spec in specs and specs[spec]:
                description_key = f"{spec}_details"
                if description_key in specs:
                    spec_descriptions.append(f"{spec}: {specs[description_key]}")
                else:
                    spec_descriptions.append(f"{spec}: Available")
        
        return spec_descriptions

    def _get_room_rate(self, room_type_id: str, stay_date: str) -> float:
        """Get room rate from PostgreSQL availability table"""
        if not self.pg_cursor:
            return 200.0
        
        try:
            self.pg_cursor.execute("""
                SELECT base_price 
                FROM atomic_availability 
                WHERE room_type_id = %s AND stay_date = %s;
            """, (room_type_id, stay_date))
            
            result = self.pg_cursor.fetchone()
            return float(result['base_price']) if result else 200.0
            
        except Exception as e:
            print(f"❌ Error getting room rate: {e}")
            return 200.0

    def select_room(self, room_choice, guest_name, guest_phone, check_in=None, check_out=None):
        """Create booking session for selected room"""
        
        if not self.last_search_results:
            return {"error": "No search results available. Please search first."}
        
        if room_choice < 1 or room_choice > len(self.last_search_results):
            return {"error": f"Invalid choice. Please select 1-{len(self.last_search_results)}"}
        
        selected_room = self.last_search_results[room_choice - 1]
        
        # Use dates from search context
        if not check_in:
            check_in = getattr(self, '_last_check_in', datetime.now().date())
        if not check_out:
            check_out = getattr(self, '_last_check_out', check_in + timedelta(days=1))
        
        # Generate session ID
        session_id = datetime.now().strftime("%Y%m%d%H%M%S") + str(uuid.uuid4())[:3].upper()
        nights = (check_out - check_in).days
        
        # Get room details
        hotel_name = selected_room.get('DIRECTORY', {}).get('hotel_name', 'Unknown Hotel')
        room_type = selected_room.get('DIRECTORY', {}).get('room_type', 'Unknown Room')
        
        # Get hotel policies and pricing
        hotel_policies = self._get_hotel_policies(hotel_name)
        room_rate = self._get_room_rate(selected_room['room_type_id'], str(check_in))
        total_amount = room_rate * nights
        payment_threshold = hotel_policies.get('payment_threshold', 30) / 100  # Convert percentage to decimal
        payment_required = total_amount * payment_threshold
        
        session_summary = {
            "session_id": session_id,
            "guest_name": guest_name,
            "guest_phone": guest_phone,
            "room_details": {
                "room_type_id": selected_room['room_type_id'],
                "room_type": room_type,
                "hotel_name": hotel_name
            },
            "stay_details": {
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "nights": nights
            },
            "pricing": {
                "rate_per_night": float(room_rate),
                "total_nights": nights,
                "total_amount": float(total_amount),
                "payment_required": float(payment_required),
                "currency": ""
            },
            "expires_in_minutes": 30,
            "next_step": f"Call confirm_booking('{session_id}', payment_amount) to complete"
        }
        
        self.current_session = session_summary
        return session_summary

    def confirm_booking(self, session_id, payment_amount, payment_method="online"):
        """Confirm booking with complete database persistence (Step 11)"""
        
        if not self.current_session or self.current_session.get('session_id') != session_id:
            return {"error": "Invalid or expired session"}
        
        session = self.current_session
        guest_id = session['guest_phone']  # Phone number as guest_id
        
        try:
            # Generate guest-centric booking ID
            booking_counter = self._get_next_booking_counter(guest_id)
            booking_id = f"{guest_id}booking{booking_counter}"
            
            # Parse dates
            check_in = datetime.fromisoformat(session['stay_details']['check_in']).date()
            check_out = datetime.fromisoformat(session['stay_details']['check_out']).date()
            
            # Get hotel policies for payment deadline
            hotel_policies = self._get_hotel_policies(session['room_details']['hotel_name'])
            payment_window_days = hotel_policies.get('payment_window_days', 7)
            
            # Get room data to extract hotel_id
            room_data = None
            if self.last_search_results:
                for room in self.last_search_results:
                    if room['room_type_id'] == session['room_details']['room_type_id']:
                        room_data = room
                        break
            
            # Get hotel_id from room data
            hotel_id = room_data.get('hotel_id') if room_data else None
            if not hotel_id:
                # Fallback: try to get hotel_id from hotel_catalog
                hotel_catalog = self.db['hotel_catalog']
                hotel_doc = hotel_catalog.find_one({'hotel_name': session['room_details']['hotel_name']})
                hotel_id = hotel_doc.get('hotel_id') if hotel_doc else 'UNKNOWN_HOTEL'
            
            # STEP 1: POSTGRESQL - Transactional data with payment deadline
            postgresql_data = {
                'booking_id': booking_id,
                'guest_id': guest_id,
                'room_type_id': session['room_details']['room_type_id'],
                'hotel_id': hotel_id,
                'check_in_date': check_in,
                'check_out_date': check_out,
                'total_nights': session['stay_details']['nights'],
                'room_rate': session['pricing']['rate_per_night'],
                'total_amount': session['pricing']['total_amount'],
                'payment_required': session['pricing']['payment_required'],
                'payment_received': payment_amount,
                'payment_status': "PAID" if payment_amount >= session['pricing']['payment_required'] else "PARTIAL",
                'booking_status': 'CONFIRMED',
                'payment_deadline': check_in - timedelta(days=payment_window_days)  # Uses real hotel policy
            }
            
            self._save_booking_postgresql(postgresql_data)
            
            # Get real hotel and room details from MongoDB
            hotel_details = self._get_hotel_details(session['room_details']['hotel_name'])
            # Get the room data from the last search results
            room_data = None
            if self.last_search_results:
                for room in self.last_search_results:
                    if room['room_type_id'] == session['room_details']['room_type_id']:
                        room_data = room
                        break
            room_details = self._get_room_details(room_data) if room_data else {
                'room_name': session['room_details']['room_type'],
                'room_description': f"Room {session['room_details']['room_type_id']}",
                'room_amenities': [],
                'max_occupancy': 2,
                'bedding_setup': 'Unknown',
                'floor_range': 'Unknown'
            }
            
            # STEP 2: MONGODB - Descriptive/content data
            mongodb_data = {
                '_id': booking_id,
                'booking_id': booking_id,
                'guest_id': guest_id,
                'room_type_id': session['room_details']['room_type_id'],
                'hotel_context': hotel_details,
                'room_context': room_details,
                'booking_context': {
                    'original_request': getattr(self, '_last_guest_query', 'Room booking request'),
                    'guest_requirements': ['Quality accommodation'],
                    'agent_recommendations': f"Recommended {session['room_details']['room_type']}",
                    'special_arrangements': []
                },
                'guest_communication': {
                    'booking_channel': 'WhatsApp',
                    'agent_notes': 'Standard booking process',
                    'confirmation_sent': True,
                    'reminder_preferences': ['24h before', 'Day of arrival']
                },
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            
            self._save_booking_mongodb(mongodb_data)
            
            # STEP 3: Update guest profile
            self._ensure_guest_exists(guest_id, session['guest_name'])
            self._add_booking_to_guest(guest_id, booking_id)
            
            # Database should handle availability reduction automatically via triggers
            # No manual Python reduction needed
            
            # Return confirmation
            confirmation = {
                "booking_confirmed": True,
                "booking_id": booking_id,
                "payment_status": postgresql_data['payment_status'],
                "total_amount": session['pricing']['total_amount'],
                "payment_required": session['pricing']['payment_required'],
                "payment_received": payment_amount,
                "payment_deadline": postgresql_data['payment_deadline'].isoformat(),
                "message": "Booking confirmed and saved to database!",
                "database_status": {
                    "postgresql_saved": True,
                    "mongodb_saved": True,
                    "guest_updated": True,
                    "availability_reduced": True
                },
                "next_steps": [
                    "✅ Booking saved to PostgreSQL (transactional data)",
                    "✅ Booking context saved to MongoDB (descriptive data)",
                    "✅ Guest profile updated with booking reference",
                    "✅ Room availability reduced for your dates",
                    f"📅 Payment deadline: {postgresql_data['payment_deadline']}",
                    "🏨 Present booking ID at check-in",
                    "🎉 Enjoy your stay!"
                ]
            }
            
            # Clear session
            self.current_session = None
            return confirmation
            
        except Exception as e:
            print(f"❌ Database persistence error: {e}")
            return {"error": f"Booking confirmation failed: {str(e)}"}

    def _get_next_booking_counter(self, guest_id):
        """Get next booking counter for guest-centric booking ID"""
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute("SELECT get_next_booking_counter(%s)", (guest_id,))
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 1
        except Exception as e:
            print(f"❌ Error getting booking counter: {e}")
            return 1

    def _save_booking_postgresql(self, booking_data):
        """Save transactional booking data to PostgreSQL"""
        try:
            cursor = self.pg_conn.cursor()
            
            insert_sql = """
                INSERT INTO confirmed_bookings (
                    booking_id, guest_id, room_type_id, hotel_id, check_in_date, check_out_date,
                    total_nights, room_rate, total_amount, payment_required,
                    payment_received, payment_status, booking_status, payment_deadline
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """
            
            cursor.execute(insert_sql, (
                booking_data['booking_id'],
                booking_data['guest_id'],
                booking_data['room_type_id'],
                booking_data['hotel_id'],
                booking_data['check_in_date'],
                booking_data['check_out_date'],
                booking_data['total_nights'],
                booking_data['room_rate'],
                booking_data['total_amount'],
                booking_data['payment_required'],
                booking_data['payment_received'],
                booking_data['payment_status'],
                booking_data['booking_status'],
                booking_data['payment_deadline']
            ))
            
            self.pg_conn.commit()
            cursor.close()
            print(f"✅ PostgreSQL: Booking {booking_data['booking_id']} saved")
            
        except Exception as e:
            self.pg_conn.rollback()
            raise Exception(f"PostgreSQL save failed: {e}")

    def _save_booking_mongodb(self, booking_data):
        """Save descriptive booking data to MongoDB"""
        try:
            collection = self.db['guest_bookings']
            result = collection.insert_one(booking_data)
            print(f"✅ MongoDB: Booking context {booking_data['booking_id']} saved")
            return result
            
        except Exception as e:
            raise Exception(f"MongoDB save failed: {e}")

    def _add_booking_to_guest(self, guest_id, booking_id):
        """Add booking ID to guest's booking array using database function"""
        try:
            cursor = self.pg_conn.cursor()
            cursor.execute("SELECT add_booking_to_guest(%s, %s)", (guest_id, booking_id))
            self.pg_conn.commit()
            cursor.close()
            print(f"✅ Guest {guest_id} updated with booking {booking_id}")
            
        except Exception as e:
            print(f"❌ Error updating guest: {e}")

    def get_booking_details(self, booking_id):
        """Retrieve complete booking details from both databases"""
        try:
            # Get transactional data from PostgreSQL
            cursor = self.pg_conn.cursor()
            cursor.execute("""
                SELECT booking_id, guest_id, room_type_id, check_in_date, check_out_date,
                       total_nights, room_rate, total_amount, payment_received,
                       payment_status, booking_status, payment_deadline, created_at
                FROM confirmed_bookings 
                WHERE booking_id = %s
            """, (booking_id,))
            
            postgresql_data = cursor.fetchone()
            cursor.close()
            
            if not postgresql_data:
                return {"error": "Booking not found"}
            
            # Get descriptive data from MongoDB
            collection = self.db['guest_bookings']
            mongodb_data = collection.find_one({'booking_id': booking_id})
            
            # Combine data
            return {
                "booking_id": postgresql_data[0],
                "guest_id": postgresql_data[1],
                "room_type_id": postgresql_data[2],
                "check_in_date": postgresql_data[3].isoformat(),
                "check_out_date": postgresql_data[4].isoformat(),
                "total_nights": postgresql_data[5],
                "room_rate": float(postgresql_data[6]),
                "total_amount": float(postgresql_data[7]),
                "payment_received": float(postgresql_data[8]),
                "payment_status": postgresql_data[9],
                "booking_status": postgresql_data[10],
                "payment_deadline": postgresql_data[11].isoformat(),
                "created_at": postgresql_data[12].isoformat(),
                "hotel_context": mongodb_data.get('hotel_context', {}) if mongodb_data else {},
                "room_context": mongodb_data.get('room_context', {}) if mongodb_data else {},
                "booking_context": mongodb_data.get('booking_context', {}) if mongodb_data else {}
            }
            
        except Exception as e:
            return {"error": f"Failed to retrieve booking: {str(e)}"}

    def _get_hotel_policies(self, hotel_name):
        """Get hotel policies from MongoDB hotel_catalog collection"""
        try:
            collection = self.db['hotel_catalog']
            hotel_data = collection.find_one({'hotel_name': hotel_name})
            
            if hotel_data and 'policies' in hotel_data:
                return hotel_data['policies']
            else:
                print(f"⚠️  No policies found for {hotel_name}, using defaults")
                return {
                    'payment_threshold': 30,
                    'payment_window_days': 7,
                    'cancellation_hours': 24,
                    'check_in': '3:00 PM',
                    'check_out': '12:00 PM'
                }
        except Exception as e:
            print(f"❌ Error getting hotel policies: {e}")
            return {
                'payment_threshold': 30,
                'payment_window_days': 7,
                'cancellation_hours': 24,
                'check_in': '3:00 PM',
                'check_out': '12:00 PM'
            }

    def _get_hotel_details(self, hotel_name):
        """Get hotel details from MongoDB hotel_catalog collection"""
        try:
            collection = self.db['hotel_catalog']
            hotel_data = collection.find_one({'hotel_name': hotel_name})
            
            if hotel_data:
                return {
                    'hotel_name': hotel_data.get('hotel_name', hotel_name),
                    'hotel_address': hotel_data.get('location', {}).get('address', 'Address not available'),
                    'check_in_instructions': f"Check-in at {hotel_data.get('policies', {}).get('check_in', '3:00 PM')}",
                    'amenities': hotel_data.get('facilities', [])
                }
            else:
                return {
                    'hotel_name': hotel_name,
                    'hotel_address': 'Address not available',
                    'check_in_instructions': 'Check-in at 3:00 PM',
                    'amenities': []
                }
        except Exception as e:
            print(f"❌ Error getting hotel details: {e}")
            return {
                'hotel_name': hotel_name,
                'hotel_address': 'Address not available',
                'check_in_instructions': 'Check-in at 3:00 PM',
                'amenities': []
            }

    def _get_room_details(self, room_data):
        """Get room details from room_encyclopedia SPECIFICATIONS"""
        try:
            specs = room_data.get('SPECIFICATIONS', {})
            directory = room_data.get('DIRECTORY', {})
            
            # Extract room amenities from specifications
            amenities = []
            if specs.get('wifi'): amenities.append('WiFi')
            if specs.get('tv'): amenities.append('Smart TV')
            if specs.get('bathtub'): amenities.append('Bathtub')
            if specs.get('balcony'): amenities.append('Balcony')
            if specs.get('coffee_machine'): amenities.append('Coffee Machine')
            if specs.get('view'): amenities.append(f"{specs['view']} View")
            
            return {
                'room_name': directory.get('room_type', 'Unknown Room'),
                'room_description': f"{specs.get('room_size_sqm', 'Unknown')} sqm {directory.get('room_type', 'room')}",
                'room_amenities': amenities,
                'max_occupancy': specs.get('max_occupancy', 2),
                'bedding_setup': specs.get('bedding_setup', 'Unknown'),
                'floor_range': specs.get('floor_range', 'Unknown')
            }
        except Exception as e:
            print(f"❌ Error getting room details: {e}")
            return {
                'room_name': 'Unknown Room',
                'room_description': 'Room details not available',
                'room_amenities': [],
                'max_occupancy': 2,
                'bedding_setup': 'Unknown',
                'floor_range': 'Unknown'
            }

def main():
    """Demo the complete booking agent with database persistence"""
    agent = BookingAgent()
    
    print("\n" + "="*60)
    print("🚀 COMPLETE BOOKING AGENT WITH DATABASE PERSISTENCE")
    print("="*60)
    
    # Demo search
    print("\n1️⃣ SEARCHING for rooms...")
    rooms = agent.search_rooms("clean room with wifi in KL", 
                              check_in=date(2025, 6, 20), 
                              check_out=date(2025, 6, 22))
    
    if rooms:
        # Demo selection
        print("\n2️⃣ SELECTING room...")
        session = agent.select_room(1, "Ahmad Rahman", "+60123456789")
        
        if not session.get('error'):
            print(f"\n📋 SESSION CREATED:")
            print(f"   Session ID: {session['session_id']}")
            print(f"   Guest: {session['guest_name']} ({session['guest_phone']})")
            print(f"   Room: {session['room_details']['room_type']}")
            print(f"   Total: {session['pricing'].get('currency','')} {session['pricing']['total_amount']:.2f}")
            print(f"   Required: {session['pricing'].get('currency','')} {session['pricing']['payment_required']:.2f}")
            
            # Demo confirmation with database persistence
            print("\n3️⃣ CONFIRMING booking with database persistence...")
            confirmation = agent.confirm_booking(session['session_id'], 210.00)
            
            if confirmation.get('booking_confirmed'):
                print(f"\n🎉 BOOKING CONFIRMED WITH DATABASE PERSISTENCE!")
                print(f"   Booking ID: {confirmation['booking_id']}")
                print(f"   Payment Status: {confirmation['payment_status']}")
                print(f"   Payment Deadline: {confirmation['payment_deadline']}")
                print(f"   Database Status: {confirmation['database_status']}")
                
                # Verify data was saved
                booking_details = agent.get_booking_details(confirmation['booking_id'])
                if not booking_details.get('error'):
                    print(f"\n✅ VERIFICATION: Booking retrieved from both databases")
                    print(f"   PostgreSQL: {booking_details.get('currency','')} {booking_details['total_amount']}")
                    print(f"   MongoDB: {booking_details['hotel_context'].get('hotel_name', 'N/A')}")
            else:
                print(f"❌ Booking failed: {confirmation.get('error')}")
        else:
            print(f"❌ Session creation failed: {session['error']}")

if __name__ == "__main__":
    main() 