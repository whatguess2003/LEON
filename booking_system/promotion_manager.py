#!/usr/bin/env python3
"""
PROMOTION MANAGER
Handles promotional codes, discounts, and marketing campaigns database operations
NO TABLE CREATION - Uses existing database schema
"""

import os
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from .timezone_utils import get_hotel_timezone_str
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
import psycopg2
from psycopg2.extras import RealDictCursor
from .llm_utils import canonical_key

load_dotenv()

# PostgreSQL Promotion Manager Class
class PostgreSQLPromotionManager:
    """PostgreSQL operations for promotions and marketing campaigns"""
    
    def __init__(self):
        self.db_url = os.getenv('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable not set")
        self._validate_database_schema()
    
    def _validate_database_schema(self):
        """Validate that required tables exist - DO NOT CREATE TABLES"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Check if promotions table exists
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' AND table_name = 'promotions'
                    )
                """)
                
                result = cursor.fetchone()
                if not result['exists']:
                    raise Exception("Promotions table does not exist. Run database_schema_setup.py first!")
                    
        except Exception as e:
            print(f"❌ Database validation failed: {str(e)}")
            raise e
    def _require_zoneinfo(self, hotel_name: str):
        tz = get_hotel_timezone_str(hotel_name)
        if not tz or ZoneInfo is None:
            raise ValueError("❌ Missing hotel timezone")
        return ZoneInfo(tz)
    
    def _get_connection(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)
    
    def create_promotion(self, code: str, discount_value: float, discount_type: str, valid_from: str, valid_to: str, description: str = None) -> dict:
        """Create a new promotion"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                code = canonical_key(code)
                cursor.execute("""
                    INSERT INTO promotions (code, discount_value, discount_type, valid_from, valid_to, description)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING promotion_id
                """, (code, discount_value, discount_type, valid_from, valid_to, description))
                
                promotion_id = cursor.fetchone()['promotion_id']
                conn.commit()
                
                return {
                    'success': True, 'promotion_id': promotion_id, 'code': code,
                    'discount_value': discount_value, 'discount_type': discount_type,
                    'valid_from': valid_from, 'valid_to': valid_to, 'description': description
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_promotions(self, active_only: bool = True, include_stats: bool = False) -> dict:
        """Get all promotions with optional usage statistics"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                base_query = """
                    SELECT promotion_id, code, discount_value, discount_type, 
                           valid_from, valid_to, description, active, usage_count, max_usage,
                           created_at, updated_at
                    FROM promotions
                """
                
                if active_only:
                    base_query += " WHERE active = true AND valid_to >= CURRENT_DATE"
                
                base_query += " ORDER BY created_at DESC"
                
                cursor.execute(base_query)
                results = cursor.fetchall()
                
                promotions = []
                for row in results:
                    promo = dict(row)
                    
                    # Add calculated fields
                    promo['is_expired'] = promo['valid_to'] < datetime.now().date()
                    promo['days_remaining'] = (promo['valid_to'] - datetime.now().date()).days if not promo['is_expired'] else 0
                    
                    if include_stats:
                        # Add usage statistics
                        promo['usage_rate'] = (promo['usage_count'] / promo['max_usage'] * 100) if promo['max_usage'] else 0
                        promo['remaining_uses'] = (promo['max_usage'] - promo['usage_count']) if promo['max_usage'] else None
                    
                    promotions.append(promo)
                
                return {'success': True, 'promotions': promotions, 'count': len(promotions)}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def update_promotion_status(self, code: str, active: bool) -> dict:
        """Enable/disable a promotion"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                code = canonical_key(code)
                cursor.execute("""
                    UPDATE promotions 
                    SET active = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE code = %s
                    RETURNING promotion_id, code
                """, (active, code))
                
                result = cursor.fetchone()
                if not result:
                    return {'success': False, 'error': f'Promotion {code} not found'}
                
                conn.commit()
                return {
                    'success': True, 'code': code, 'active': active,
                    'action': 'enabled' if active else 'disabled'
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def update_promotion_details(self, code: str, updates: dict) -> dict:
        """Update promotion details (description, max_usage, etc.)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Build dynamic update query
                update_fields = []
                params = []
                
                allowed_fields = ['description', 'max_usage', 'discount_value', 'valid_from', 'valid_to']
                for field, value in updates.items():
                    if field in allowed_fields:
                        update_fields.append(f"{field} = %s")
                        params.append(value)
                
                if not update_fields:
                    return {'success': False, 'error': 'No valid fields to update'}
                
                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                params.append(code)
                
                query = f"""
                    UPDATE promotions 
                    SET {', '.join(update_fields)}
                    WHERE code = %s
                    RETURNING promotion_id, code
                """
                
                cursor.execute(query, params)
                result = cursor.fetchone()
                
                if not result:
                    return {'success': False, 'error': f'Promotion {code} not found'}
                
                conn.commit()
                return {
                    'success': True, 'code': code, 'updated_fields': list(updates.keys()),
                    'message': f'Promotion {code} updated successfully'
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def delete_promotion(self, code: str) -> dict:
        """Delete a promotion (soft delete by setting active=false)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                code = canonical_key(code)
                cursor.execute("""
                    UPDATE promotions 
                    SET active = false, updated_at = CURRENT_TIMESTAMP
                    WHERE code = %s
                    RETURNING promotion_id, code, description
                """, (code,))
                
                result = cursor.fetchone()
                if not result:
                    return {'success': False, 'error': f'Promotion {code} not found'}
                
                conn.commit()
                return {
                    'success': True, 'code': code,
                    'message': f'Promotion {code} deleted (deactivated)'
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def validate_promotion(self, code: str) -> dict:
        """Validate if a promotion code is valid and active"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                code = canonical_key(code)
                cursor.execute("""
                    SELECT promotion_id, code, discount_value, discount_type, 
                           valid_from, valid_to, active, usage_count, max_usage
                    FROM promotions
                    WHERE code = %s
                """, (code,))
                
                promo = cursor.fetchone()
                if not promo:
                    return {'success': False, 'valid': False, 'error': 'Promotion code not found'}
                
                promo = dict(promo)
                today = datetime.now().date()
                
                # Validation checks
                if not promo['active']:
                    return {'success': True, 'valid': False, 'reason': 'Promotion is inactive'}
                
                if today < promo['valid_from']:
                    return {'success': True, 'valid': False, 'reason': 'Promotion not yet active'}
                
                if today > promo['valid_to']:
                    return {'success': True, 'valid': False, 'reason': 'Promotion has expired'}
                
                if promo['max_usage'] and promo['usage_count'] >= promo['max_usage']:
                    return {'success': True, 'valid': False, 'reason': 'Promotion usage limit reached'}
                
                return {
                    'success': True, 'valid': True, 'promotion': promo,
                    'discount_value': promo['discount_value'],
                    'discount_type': promo['discount_type']
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}

if __name__ == "__main__":
    # Test the manager
    manager = PostgreSQLPromotionManager()
    print("🌟 Promotion Manager initialized successfully!")