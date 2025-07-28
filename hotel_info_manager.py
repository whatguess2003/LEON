#!/usr/bin/env python3
"""
Hotel Information Manager Tool for LEON Orchestrator
Stores general hotel information in keyword format

ARCHITECTURE:
- Single source of truth: hotel_name from manager_context.json
- Simple keyword storage: hotel_name + keyword + category + description
- No complex validation: Just store what's provided
- MongoDB: hotel_keywords collection for structured data
- LLM parsing: Extract keywords from unstructured text
"""

import os
import json
from typing import Dict, Any, List, Optional
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from datetime import datetime
import pymongo

# MongoDB imports
try:
    from pymongo import MongoClient
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False

# Import existing systems
# from hotel_context_manager import get_active_manager

class HotelInfoManager:
    def __init__(self):
        """Initialize Hotel Info Manager"""
        self.mongo_client = None
        self.mongo_db = None
        
        # Initialize LLM for parsing unstructured hotel information
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
        
        self._connect_database()
        self._ensure_collections()
    
    def _connect_database(self):
        """Connect to MongoDB only"""
        try:
            # MongoDB connection
            self.mongo_client = pymongo.MongoClient(os.getenv('MONGO_URL'))
            self.mongo_db = self.mongo_client['leon_hotel_memory']
            
            # Database connection established silently
        except Exception as e:
            print(f"❌ MongoDB connection error: {e}")
    
    def _ensure_collections(self):
        """Ensure hotel_keywords collection exists with proper indexes"""
        try:
            if self.mongo_db is None:
                return  # Skip if database is not available
            
            keywords_collection = self.mongo_db['hotel_keywords']
            
            # Create indexes for efficient querying
            keywords_collection.create_index([("hotel_name", 1)])
            keywords_collection.create_index([("keyword", 1)])
            keywords_collection.create_index([("category", 1)])
            keywords_collection.create_index([("timestamp", -1)])
            
            # Compound index for hotel + category queries
            keywords_collection.create_index([("hotel_name", 1), ("category", 1)])
            
        except Exception as e:
            print(f"⚠️ Collection setup warning: {e}")
    
    def _get_hotel_name_from_context(self) -> str:
        """Get hotel_name from manager_context.json (single source of truth)"""
        try:
            with open('manager_context.json', 'r') as f:
                context = json.load(f)
                hotel_name = context.get('active_manager', {}).get('hotel_name')
                if hotel_name:
                    return hotel_name
                else:
                    return "❌ No hotel_name found in manager_context.json"
        except Exception as e:
            return f"❌ Error reading manager_context.json: {e}"
    
    def _parse_hotel_keywords(self, text: str, hotel_name: str, dialog_history: str = None) -> List[Dict[str, str]]:
        """Parse unstructured text into structured keywords using LLM with dialog history context"""
        try:
            # Add dialog history context if available
            context_section = ""
            if dialog_history:
                context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "it", "its", "that hotel", "the facility").
"""
            
            prompt = f"""
            Extract structured keywords from this hotel information text.
            
            {context_section}
            HOTEL: {hotel_name}
            CURRENT TEXT: {text}
            
            Extract keywords in this format:
            - keyword: the key term
            - category: contact, awards, facilities, hours, policies, amenities, location, room_types
            - description: brief explanation of the keyword
            
            IMPORTANT CATEGORIZATION:
            - Room types (suite, room, accommodation types) → category: "room_types"
            - Physical facilities (pool, gym, cafe) → category: "facilities"
            
            Return as JSON array:
            [
                {{"keyword": "phone", "category": "contact", "description": "Hotel phone number"}},
                {{"keyword": "award", "category": "awards", "description": "Hotel recognition"}}
            ]
            
            Focus on:
            - Contact info (phone, email, address)
            - Awards and recognition
            - Facilities (pools, gyms, restaurants)
            - Operating hours
            - Policies (check-in/out times)
            - Amenities and services
            - Location and nearby attractions
            """
            
            response = self.llm.invoke(prompt)
            
            # Parse JSON response - strip markdown code blocks if present
            try:
                content = response.content.strip()
                
                # Remove markdown code block formatting
                if content.startswith('```json'):
                    content = content[7:]  # Remove '```json'
                if content.startswith('```'):
                    content = content[3:]  # Remove '```'
                if content.endswith('```'):
                    content = content[:-3]  # Remove closing '```'
                
                content = content.strip()
                
                keywords = json.loads(content)
                return keywords if isinstance(keywords, list) else []
            except json.JSONDecodeError as e:
                # Fallback: create simple keywords
                return [
                    {"keyword": "hotel_info", "category": "general", "description": f"Information about {hotel_name}"}
                ]
                
        except Exception as e:
            print(f"⚠️ Keyword parsing failed: {e}")
            return [
                {"keyword": "hotel_info", "category": "general", "description": f"Information about {hotel_name}"}
            ]
    
    def _process_keyword_query(self, query: str, keywords: List[Dict], hotel_name: str, dialog_history: str = None) -> str:
        """Process user query against stored keywords using LLM with dialog history context"""
        try:
            # Format keywords for LLM
            keywords_text = ""
            for kw in keywords:
                keywords_text += f"• {kw['keyword']} ({kw['category']}): {kw['description']}\n"
            
            # Add dialog history context if available
            context_section = ""
            if dialog_history:
                context_section = f"""
RECENT CONVERSATION CONTEXT:
{dialog_history}

Use this context to resolve pronouns and references (e.g., "it", "its", "that", "the hotel").
"""
            
            prompt = f"""
            Answer this query based on the hotel keywords:
            
            {context_section}
            QUERY: {query}
            HOTEL: {hotel_name}
            
            AVAILABLE KEYWORDS:
            {keywords_text}
            
            Provide a clear, helpful answer based on the available keywords.
            If no relevant information is found, say so clearly.
            """
            
            response = self.llm.invoke(prompt)
            return response.content.strip()
            
        except Exception as e:
            return f"❌ Error processing query: {e}"
    
    def store_hotel_keywords(self, keywords_data: str, dialog_history: str = None) -> str:
        """Store hotel keywords and information using hotel_name from manager_context.json"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # STEP 1: GET hotel_name from manager_context.json (single source of truth)
            hotel_name = self._get_hotel_name_from_context()
            if hotel_name.startswith("❌"):
                return hotel_name
            
            # STEP 2: PARSE keywords data using LLM with dialog history context
            parsed_keywords = self._parse_hotel_keywords(keywords_data, hotel_name, dialog_history)
            
            # STEP 3: STORE keywords in MongoDB
            keywords_collection = self.mongo_db['hotel_keywords']
            
            # Store each keyword with metadata
            stored_count = 0
            for keyword_data in parsed_keywords:
                keyword_doc = {
                    "hotel_name": hotel_name,
                    "keyword": keyword_data['keyword'],
                    "category": keyword_data['category'],
                    "description": keyword_data['description'],
                    "timestamp": datetime.now(),
                    "source": "hotel_info_manager"
                }
                
                # Use upsert to avoid duplicates
                keywords_collection.replace_one(
                    {"hotel_name": hotel_name, "keyword": keyword_data['keyword']},
                    keyword_doc,
                    upsert=True
                )
                stored_count += 1
            
            result = f"✅ Hotel keywords stored successfully for {hotel_name}:\n"
            result += f"  • Keywords stored: {stored_count}\n"
            result += f"  • Categories: {', '.join(set(k['category'] for k in parsed_keywords))}\n"
            result += f"  • 🎯 Single Source: hotel_name from manager_context.json"
            
            return result
            
        except Exception as e:
            return f"❌ Error storing hotel keywords: {e}"
    
    def query_hotel_keywords(self, query: str, dialog_history: str = None) -> str:
        """Query hotel keywords and information using hotel_name from manager_context.json"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # STEP 1: GET hotel_name from manager_context.json (single source of truth)
            hotel_name = self._get_hotel_name_from_context()
            if hotel_name.startswith("❌"):
                return hotel_name
            
            # STEP 2: QUERY keywords from MongoDB
            keywords_collection = self.mongo_db['hotel_keywords']
            keywords = list(keywords_collection.find(
                {"hotel_name": hotel_name},
                {"_id": 0}
            ).sort("timestamp", -1))
            
            if not keywords:
                return f"❌ No keywords found for {hotel_name}"
            
            # STEP 3: PROCESS query using LLM with dialog history context
            query_result = self._process_keyword_query(query, keywords, hotel_name, dialog_history)
            
            return query_result
            
        except Exception as e:
            return f"❌ Error querying hotel keywords: {e}"
    
    def clear_keywords_by_category(self, category: str) -> str:
        """Clear keywords by category (e.g., 'room_types') from keywords system"""
        if self.mongo_db is None:
            return "❌ Database connection not available"
        
        try:
            # STEP 1: GET hotel_name from manager_context.json (single source of truth)
            hotel_name = self._get_hotel_name_from_context()
            if hotel_name.startswith("❌"):
                return hotel_name
            
            # STEP 2: DELETE keywords by category from MongoDB
            keywords_collection = self.mongo_db['hotel_keywords']
            
            # Count before deletion
            count_before = keywords_collection.count_documents({
                "hotel_name": hotel_name,
                "category": category
            })
            
            # Delete keywords
            delete_result = keywords_collection.delete_many({
                "hotel_name": hotel_name,
                "category": category
            })
            
            return f"✅ Cleared {delete_result.deleted_count} keywords in category '{category}' for {hotel_name}"
            
        except Exception as e:
            return f"❌ Error clearing keywords: {e}"

# Singleton instance for tools
hotel_info_manager = HotelInfoManager()

@tool
def create_hotel_keywords(text: str, dialog_history: str = None) -> str:
    """CREATE structured keywords from hotel information using hotel_name from manager_context.json. DO NOT USE THIS TOOL TO UPDATE ROOM TYPE INFORMATION"""
    result = hotel_info_manager.store_hotel_keywords(text, dialog_history)
    return result

@tool
def read_hotel_keywords(query: str, dialog_history: str = None) -> str:
    """READ hotel keywords and information based on query"""
    answer = hotel_info_manager.query_hotel_keywords(query, dialog_history)
    return answer

@tool
def delete_hotel_keywords_by_category(category: str) -> str:
    """DELETE keywords by category (e.g., 'room_types') from keywords system"""
    result = hotel_info_manager.clear_keywords_by_category(category)
    return result

def test_hotel_info_tool():
    """Test hotel info tool functionality"""
    print("🧪 Testing Hotel Info Tool")
    print("=" * 50)
    
    # Test keyword storage
    test_text = """
    Seri Wawasan Putrajaya Hotel - 4-star business hotel.
    Contact: +60 3-8888-9999, reservations@seriwawasan.com
    Awards: Best Business Hotel 2023, Excellence in Service 2022
    Conference Hall capacity 200 guests
    Check-in: 2:00 PM, Check-out: 11:00 AM
    """
    
    result = hotel_info_manager.store_hotel_keywords(test_text)
    print(f"Storage result: {result}")
    
    # Test query
    query_result = hotel_info_manager.query_hotel_keywords("What awards has this hotel won?")
    print(f"Query result: {query_result}")

if __name__ == "__main__":
    test_hotel_info_tool() 