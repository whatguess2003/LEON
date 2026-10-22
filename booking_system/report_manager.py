#!/usr/bin/env python3
"""
REPORT MANAGER AGENT - AGENT-RULE COMPLIANT
Strategic analytics, business intelligence, and executive dashboards
Manager/Supervisor facing - Analyze, Review, Monitor, Forecast operations

AGENT-RULE COMPLIANCE:
- READ database first and validate parameters
- Natural keys only: booking_id, guest_phone, hotel_name, room_type
- Correct booking-system schema: room_bookings, payment_tracking, atomic_availability
- Manager context integration for hotel-specific operations
"""

import os
from datetime import timedelta, date, datetime, timezone
from dotenv import load_dotenv
from langchain_core.tools import tool
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()
# AGENT-RULE COMPLIANT TOOLS - Strategic Analytics


class ReportManager:
    """Strategic Analytics and Reporting - AGENT-RULE COMPLIANT"""
    def __init__(self):
        self.db_url = os.getenv('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable not set")
    # Timezone logic centralized in timezone_utils

    def _get_connection(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def calculate_revenue_analytics(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Calculate revenue analytics for a specific hotel. Requires hotel_name."""
        try:
            default_start = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
            default_end = datetime.now(timezone.utc).date().isoformat()
            start_date_norm = (str(start_date).strip() or default_start)
            end_date_norm = (str(end_date).strip() or default_end)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                hotel_filter = ""
                params = [start_date_norm, end_date_norm]
                if hotel_name:
                    hotel_filter = " AND b.hotel_name = %s"
                    params.append(hotel_name)
                cursor.execute(f"""
                    SELECT booking_status,
                           COUNT(*) as booking_count,
                           SUM(total_amount) as total_revenue,
                           AVG(total_amount) as avg_booking_value,
                           SUM(CASE WHEN EXISTS (
                               SELECT 1 FROM payment_tracking pt 
                               WHERE pt.booking_id = b.booking_id AND pt.payment_status = 'completed'
                           ) THEN total_amount ELSE 0 END) as paid_revenue
                    FROM room_bookings b
                    WHERE check_in_date >= %s AND check_in_date <= %s{hotel_filter}
                    GROUP BY booking_status
                    ORDER BY total_revenue DESC
                """, params)
                revenue_by_status = cursor.fetchall()
                cursor.execute(f"""
                    SELECT b.hotel_name,
                           b.room_type,
                           COUNT(*) as booking_count,
                           SUM(b.total_amount) as room_revenue,
                           AVG(b.total_amount) as avg_rate,
                           MAX(b.total_amount) as highest_rate,
                           MIN(b.total_amount) as lowest_rate
                    FROM room_bookings b
                    WHERE b.check_in_date >= %s AND b.check_in_date <= %s
                      AND b.booking_status = 'confirmed'{hotel_filter}
                    GROUP BY b.hotel_name, b.room_type
                    ORDER BY room_revenue DESC
                """, params)
                revenue_by_room = cursor.fetchall()
                total_revenue = sum(float(row['total_revenue']) for row in revenue_by_status if row['total_revenue'])
                total_paid = sum(float(row['paid_revenue']) for row in revenue_by_status if row['paid_revenue'])
                total_bookings = sum(row['booking_count'] for row in revenue_by_status if row['booking_count'])
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"💰 Revenue Analytics Report (AGENT-RULE COMPLIANT)\n"
                result += f"Scope: {scope}\n"
                result += f"Period: {start_date_norm} to {end_date_norm}\n\n"
                result += f"📊 Executive Summary:\n"
                result += f"  • Total Revenue: ${total_revenue:,.2f}\n"
                result += f"  • Paid Revenue: ${total_paid:,.2f}\n"
                result += f"  • Total Bookings: {total_bookings}\n"
                result += f"  • Average Booking Value: ${total_revenue/total_bookings:.2f}\n" if total_bookings > 0 else "  • Average Booking Value: $0.00\n"
                result += f"  • Payment Collection Rate: {total_paid/total_revenue*100:.1f}%\n\n" if total_revenue > 0 else "  • Payment Collection Rate: 0.0%\n\n"
                result += f"📈 Revenue by Status:\n"
                for row in revenue_by_status:
                    status = row['booking_status']
                    revenue = float(row['total_revenue']) if row['total_revenue'] else 0
                    count = row['booking_count']
                    result += f"  • {status.upper()}: ${revenue:,.2f} ({count} bookings)\n"
                result += f"\n🏠 Top Performing Rooms (Natural Keys):\n"
                for i, row in enumerate(revenue_by_room[:5]):
                    hotel_name_result = row['hotel_name']
                    room_type = row['room_type']
                    revenue = float(row['room_revenue']) if row['room_revenue'] else 0
                    avg_rate = float(row['avg_rate']) if row['avg_rate'] else 0
                    result += f"  {i+1}. {hotel_name_result} - {room_type}: ${revenue:,.2f} revenue, ${avg_rate:.2f} avg rate\n"
                result += f"\n🎯 Natural Keys Used: booking_status, hotel_name, room_type"
                return result
        except ValueError as ve:
            return str(ve)
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error: {e}"

    def generate_occupancy_analytics(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Generate occupancy analytics for a specific hotel. Requires hotel_name."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if not start_date:
                    start_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
                if not end_date:
                    end_date = datetime.now(timezone.utc).date().isoformat()
                hotel_filter = ""
                params = [start_date, end_date]
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                cursor.execute(f"""
                    SELECT 
                        COUNT(DISTINCT s.booking_id) AS total_bookings,
                        COUNT(DISTINCT CASE WHEN s.booking_status = 'confirmed' THEN s.booking_id END) AS confirmed_bookings,
                        COUNT(DISTINCT CASE WHEN s.booking_status = 'cancelled' THEN s.booking_id END) AS cancelled_bookings,
                        COUNT(DISTINCT CASE WHEN s.booking_status = 'pending' THEN s.booking_id END) AS pending_bookings,
                        COUNT(*) FILTER (WHERE s.booking_status = 'confirmed') AS total_nights_booked
                    FROM booking_stays s
                    WHERE s.stay_date >= %s AND s.stay_date <= %s{hotel_filter}
                """, params)
                booking_stats = cursor.fetchone()
                inventory_params = []
                inventory_filter = ""
                if hotel_name:
                    inventory_filter = " WHERE hotel_name = %s"
                    inventory_params.append(hotel_name)
                cursor.execute(f"""
                    SELECT SUM(total_inventory) as total_rooms,
                           SUM(active_inventory) as active_rooms
                    FROM room_inventory{inventory_filter}
                """, inventory_params)
                inventory_result = cursor.fetchone()
                total_rooms = inventory_result['total_rooms'] or 0
                active_rooms = inventory_result['active_rooms'] or 0
                cursor.execute(f"""
                    SELECT s.hotel_name,
                           s.room_type,
                           COUNT(DISTINCT s.booking_id) AS booking_count,
                           COUNT(*) FILTER (WHERE s.booking_status = 'confirmed') AS confirmed_nights,
                           COUNT(*) FILTER (WHERE s.booking_status = 'confirmed') AS total_nights_booked
                    FROM booking_stays s
                    WHERE s.stay_date >= %s AND s.stay_date <= %s{hotel_filter}
                    GROUP BY s.hotel_name, s.room_type
                    ORDER BY confirmed_nights DESC
                """, params)
                room_stats = cursor.fetchall()
                confirmed_bookings = booking_stats['confirmed_bookings'] or 0
                total_bookings = booking_stats['total_bookings'] or 0
                cancelled_bookings = booking_stats['cancelled_bookings'] or 0
                total_nights_booked = booking_stats['total_nights_booked'] or 0
                cancellation_rate = (cancelled_bookings / total_bookings * 100) if total_bookings > 0 else 0
                y1,m1,d1 = [int(x) for x in str(start_date).split('-')]
                y2,m2,d2 = [int(x) for x in str(end_date).split('-')]
                start_dt = date(y1,m1,d1)
                end_dt = date(y2,m2,d2)
                period_days = (end_dt - start_dt).days + 1
                total_available_nights = active_rooms * period_days
                occupancy_rate = (total_nights_booked / total_available_nights * 100) if total_available_nights > 0 else 0
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"📊 Occupancy Analytics Report (AGENT-RULE COMPLIANT)\n"
                result += f"Scope: {scope}\n"
                result += f"Period: {start_date} to {end_date} ({period_days} days)\n\n"
                result += f"🎯 Executive Summary:\n"
                result += f"  • Total Bookings: {total_bookings}\n"
                result += f"  • Confirmed Bookings: {confirmed_bookings}\n"
                result += f"  • Cancelled Bookings: {cancelled_bookings}\n"
                result += f"  • Cancellation Rate: {cancellation_rate:.1f}%\n"
                result += f"  • Total Nights Booked: {total_nights_booked}\n"
                result += f"  • Occupancy Rate: {occupancy_rate:.1f}%\n\n"
                result += f"🏨 Room Inventory:\n"
                result += f"  • Total Rooms: {total_rooms}\n"
                result += f"  • Active Rooms: {active_rooms}\n"
                result += f"  • Available Room-Nights: {total_available_nights}\n\n"
                result += f"🏠 Top Performing Room Types (Natural Keys):\n"
                for i, row in enumerate(room_stats[:5]):
                    hotel_name_result = row['hotel_name']
                    room_type = row['room_type']
                    confirmed = row['confirmed_count']
                    nights = row['total_nights_booked']
                    result += f"  {i+1}. {hotel_name_result} - {room_type}: {confirmed} bookings, {nights} nights\n"
                result += f"\n🎯 Natural Keys Used: hotel_name, room_type, booking_status"
                return result
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Occupancy analytics failed: {str(e)}"

    def generate_occupancy_and_booking_analytics(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Generate a consolidated report: Occupancy analytics + Booking (revenue) analytics. READ operation."""
        try:
            if not start_date:
                start_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
            if not end_date:
                end_date = datetime.now(timezone.utc).date().isoformat()

            # Part 1: Occupancy (this manager)
            occupancy_text = self.generate_occupancy_analytics(hotel_name, start_date, end_date)

            # Part 2: Booking analytics (reuse booking_manager)
            try:
                from .booking_manager import booking_manager  # local import to avoid potential cycles
                booking_text = booking_manager.get_booking_analytics(hotel_name, start_date, end_date)
            except Exception as be:
                booking_text = f"❌ Booking analytics unavailable: {be}"

            combined = (
                "📘 CONSOLIDATED OCCUPANCY + BOOKING ANALYTICS\n"
                f"Scope: Hotel: {hotel_name}\n"
                f"Period: {start_date} to {end_date}\n\n"
                "===== OCCUPANCY =====\n" + occupancy_text.strip() + "\n\n"
                "===== BOOKING ANALYTICS =====\n" + booking_text.strip()
            )
            return combined
        except Exception as e:
            return f"❌ Consolidated analytics failed: {e}"

    def generate_guest_analytics(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Generate guest analytics for a specific hotel. Requires hotel_name."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if not start_date:
                    start_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
                if not end_date:
                    end_date = datetime.now(timezone.utc).date().isoformat()
                hotel_filter = ""
                params = [start_date, end_date]
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                cursor.execute(f"""
                    SELECT guest_phone,
                           COUNT(*) as total_bookings,
                           COUNT(CASE WHEN booking_status = 'confirmed' THEN 1 END) as confirmed_bookings,
                           COUNT(CASE WHEN booking_status = 'cancelled' THEN 1 END) as cancelled_bookings,
                           SUM(total_amount) as total_spent,
                           AVG(total_amount) as avg_booking_value,
                           SUM(nights) as total_nights,
                           COUNT(DISTINCT hotel_name) as hotels_visited
                    FROM room_bookings
                    WHERE check_in_date >= %s AND check_in_date <= %s{hotel_filter}
                    GROUP BY guest_phone
                    ORDER BY total_spent DESC
                    LIMIT 20
                """, params)
                guest_stats = cursor.fetchall()
                cursor.execute(f"""
                    SELECT hotel_name,
                           room_type,
                           COUNT(*) as booking_count,
                           COUNT(DISTINCT guest_phone) as unique_guests,
                           AVG(total_amount) as avg_rate
                    FROM room_bookings
                    WHERE check_in_date >= %s AND check_in_date <= %s
                      AND booking_status = 'confirmed'{hotel_filter}
                    GROUP BY hotel_name, room_type
                    ORDER BY booking_count DESC
                    LIMIT 10
                """, params)
                room_preferences = cursor.fetchall()
                loyalty_params = []
                loyalty_filter = ""
                if hotel_name:
                    loyalty_filter = " AND hotel_name = %s"
                    loyalty_params.append(hotel_name)
                cursor.execute(f"""
                    SELECT guest_phone,
                           COUNT(*) as booking_frequency,
                           MIN(check_in_date) as first_booking,
                           MAX(check_in_date) as latest_booking,
                           SUM(total_amount) as lifetime_value
                    FROM room_bookings
                    WHERE booking_status = 'confirmed'{loyalty_filter}
                    GROUP BY guest_phone
                    HAVING COUNT(*) > 1
                    ORDER BY booking_frequency DESC, lifetime_value DESC
                    LIMIT 10
                """, loyalty_params)
                repeat_guests = cursor.fetchall()
                total_unique_guests = len(guest_stats)
                total_guest_revenue = sum(float(row['total_spent']) for row in guest_stats if row['total_spent'])
                avg_guest_value = total_guest_revenue / total_unique_guests if total_unique_guests > 0 else 0
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"👥 Guest Analytics Report (AGENT-RULE COMPLIANT)\n"
                result += f"Scope: {scope}\n"
                result += f"Period: {start_date} to {end_date}\n\n"
                result += f"📊 Guest Summary:\n"
                result += f"  • Unique Guests: {total_unique_guests}\n"
                result += f"  • Total Guest Revenue: ${total_guest_revenue:,.2f}\n"
                result += f"  • Average Guest Value: ${avg_guest_value:.2f}\n"
                result += f"  • Repeat Guests: {len(repeat_guests)}\n\n"
                result += f"💰 Top Spending Guests (Natural Keys):\n"
                for i, guest in enumerate(guest_stats[:5]):
                    guest_phone = guest['guest_phone']
                    total_spent = float(guest['total_spent']) if guest['total_spent'] else 0
                    bookings = guest['total_bookings']
                    avg_value = float(guest['avg_booking_value']) if guest['avg_booking_value'] else 0
                    result += f"  {i+1}. {guest_phone}: ${total_spent:,.2f} ({bookings} bookings, ${avg_value:.2f} avg)\n"
                result += f"\n🏠 Popular Room Types (Natural Keys):\n"
                for i, room in enumerate(room_preferences[:5]):
                    hotel_name_result = room['hotel_name']
                    room_type = room['room_type']
                    bookings = room['booking_count']
                    unique_guests = room['unique_guests']
                    result += f"  {i+1}. {hotel_name_result} - {room_type}: {bookings} bookings, {unique_guests} unique guests\n"
                result += f"\n🔄 Loyal Repeat Guests:\n"
                for i, guest in enumerate(repeat_guests[:5]):
                    guest_phone = guest['guest_phone']
                    frequency = guest['booking_frequency']
                    lifetime_value = float(guest['lifetime_value']) if guest['lifetime_value'] else 0
                    result += f"  {i+1}. {guest_phone}: {frequency} bookings, ${lifetime_value:,.2f} lifetime value\n"
                result += f"\n🎯 Natural Keys Used: guest_phone, hotel_name, room_type, booking_status"
                return result
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Guest analytics failed: {str(e)}"

    def generate_performance_dashboard(self, hotel_name: str) -> str:
        """Generate performance dashboard for a specific hotel. Requires hotel_name."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                today = datetime.now(timezone.utc).date().isoformat()
                yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
                week_ago = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
                
                # Build WHERE clause for hotel filtering
                hotel_filter = ""
                params_today = [today]
                params_yesterday = [yesterday]
                params_week = [week_ago]
                
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params_today.append(hotel_name)
                    params_yesterday.append(hotel_name)
                    params_week.append(hotel_name)
                
                # READ today's performance from room_bookings table (AGENT-RULE COMPLIANT)
                cursor.execute(f"""
                    SELECT COUNT(*) as bookings_today,
                           COUNT(CASE WHEN booking_status = 'confirmed' THEN 1 END) as confirmed_today,
                           SUM(total_amount) as revenue_today
                    FROM room_bookings
                    WHERE DATE(created_at) = %s{hotel_filter}
                """, params_today)
                
                today_stats = cursor.fetchone()
                
                # READ yesterday's performance for comparison
                cursor.execute(f"""
                    SELECT COUNT(*) as bookings_yesterday,
                           SUM(total_amount) as revenue_yesterday
                    FROM room_bookings
                    WHERE DATE(created_at) = %s{hotel_filter}
                """, params_yesterday)
                
                yesterday_stats = cursor.fetchone()
                
                # READ weekly trends
                cursor.execute(f"""
                    SELECT DATE(created_at) as booking_date,
                           COUNT(*) as daily_bookings,
                           SUM(total_amount) as daily_revenue
                    FROM room_bookings
                    WHERE DATE(created_at) >= %s{hotel_filter}
                    GROUP BY DATE(created_at)
                    ORDER BY booking_date
                """, params_week)
                
                weekly_trends = cursor.fetchall()
                
                # READ current inventory status using natural keys
                inventory_params = []
                inventory_filter = ""
                if hotel_name:
                    inventory_filter = " WHERE hotel_name = %s"
                    inventory_params.append(hotel_name)
                
                cursor.execute(f"""
                    SELECT hotel_name,
                           room_type,
                           total_inventory,
                           active_inventory
                    FROM room_inventory{inventory_filter}
                    ORDER BY hotel_name, room_type
                """, inventory_params)
                
                inventory_status = cursor.fetchall()
                
                # READ pending payments from payment_tracking table (AGENT-RULE COMPLIANT)
                payment_params = []
                payment_filter = ""
                if hotel_name:
                    payment_filter = " AND EXISTS (SELECT 1 FROM room_bookings rb WHERE rb.booking_id = payment_tracking.booking_id AND rb.hotel_name = %s)"
                    payment_params.append(hotel_name)
                
                cursor.execute(f"""
                    SELECT COUNT(*) as pending_payments,
                           SUM(remaining_balance) as outstanding_amount
                    FROM payment_tracking
                    WHERE payment_status IN ('pending', 'partial'){payment_filter}
                """, payment_params)
                
                payment_stats = cursor.fetchone()
                
                # Calculate metrics
                bookings_today = today_stats['bookings_today'] or 0
                revenue_today = float(today_stats['revenue_today']) if today_stats['revenue_today'] else 0
                bookings_yesterday = yesterday_stats['bookings_yesterday'] or 0
                revenue_yesterday = float(yesterday_stats['revenue_yesterday']) if yesterday_stats['revenue_yesterday'] else 0
                
                booking_change = ((bookings_today - bookings_yesterday) / bookings_yesterday * 100) if bookings_yesterday > 0 else 0
                revenue_change = ((revenue_today - revenue_yesterday) / revenue_yesterday * 100) if revenue_yesterday > 0 else 0
                
                total_inventory = sum(room['total_inventory'] for room in inventory_status)
                active_inventory = sum(room['active_inventory'] for room in inventory_status)
                
                pending_payments = payment_stats['pending_payments'] or 0
                outstanding_amount = float(payment_stats['outstanding_amount']) if payment_stats['outstanding_amount'] else 0
                
                # Format response
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"📊 PERFORMANCE DASHBOARD (AGENT-RULE COMPLIANT)\n"
                result += f"Scope: {scope}\n"
                result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
                result += "=" * 50 + "\n\n"
                
                result += f"📈 TODAY'S PERFORMANCE:\n"
                result += f"  • Bookings: {bookings_today} ({booking_change:+.1f}% vs yesterday)\n"
                result += f"  • Revenue: ${revenue_today:,.2f} ({revenue_change:+.1f}% vs yesterday)\n"
                result += f"  • Confirmed: {today_stats['confirmed_today'] or 0}\n\n"
                
                result += f"💰 PAYMENT STATUS:\n"
                result += f"  • Pending Payments: {pending_payments}\n"
                result += f"  • Outstanding Amount: ${outstanding_amount:,.2f}\n\n"
                
                result += f"🏨 INVENTORY STATUS (Natural Keys):\n"
                result += f"  • Total Rooms: {total_inventory}\n"
                result += f"  • Active Rooms: {active_inventory}\n"
                result += f"  • Room Types: {len(inventory_status)}\n\n"
                
                result += f"📅 WEEKLY TREND:\n"
                for trend in weekly_trends[-7:]:
                    date = trend['booking_date']
                    bookings = trend['daily_bookings']
                    revenue = float(trend['daily_revenue']) if trend['daily_revenue'] else 0
                    result += f"  • {date}: {bookings} bookings, ${revenue:,.2f}\n"
                
                result += f"\n🎯 Natural Keys Used: hotel_name, room_type, guest_phone, booking_status"
                return result
                       
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Performance dashboard failed: {str(e)}"

    def generate_financial_reconciliation(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Generate financial reconciliation for a specific hotel. Requires hotel_name."""
        try:
            default_start = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
            default_end = datetime.now(timezone.utc).date().isoformat()
            start_date_norm = (str(start_date).strip() or default_start)
            end_date_norm = (str(end_date).strip() or default_end)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # READ booking revenue from room_bookings table (AGENT-RULE COMPLIANT)
                cursor.execute("""
                    SELECT booking_status,
                           COUNT(*) as booking_count,
                           SUM(subtotal) as gross_revenue,
                           SUM(taxes) as total_taxes,
                           SUM(total_amount) as net_revenue
                    FROM room_bookings
                    WHERE check_in_date >= %s AND check_in_date <= %s
                    GROUP BY booking_status
                    ORDER BY net_revenue DESC
                """, (start_date_norm, end_date_norm))
                
                booking_revenue = cursor.fetchall()
                
                # READ payment reconciliation from payment_tracking table (AGENT-RULE COMPLIANT)
                cursor.execute("""
                    SELECT p.payment_status,
                           COUNT(*) as payment_count,
                           SUM(p.amount_paid) as collected_amount,
                           SUM(p.remaining_balance) as outstanding_balance
                    FROM payment_tracking p
                    LEFT JOIN room_bookings b ON p.booking_id = b.booking_id
                    WHERE b.check_in_date >= %s AND b.check_in_date <= %s
                    GROUP BY p.payment_status
                    ORDER BY collected_amount DESC
                """, (start_date_norm, end_date_norm))
                
                payment_reconciliation = cursor.fetchall()
                
                # READ refund analysis from room_bookings table
                cursor.execute("""
                    SELECT COUNT(*) as cancelled_bookings,
                           SUM(total_amount) as cancelled_revenue,
                           SUM(COALESCE(refund_amount, 0)) as total_refunds,
                           AVG(COALESCE(refund_amount, 0)) as avg_refund
                    FROM room_bookings
                    WHERE check_in_date >= %s AND check_in_date <= %s
                      AND booking_status = 'cancelled'
                """, (start_date_norm, end_date_norm))
                
                refund_stats = cursor.fetchone()
                
                # Calculate financial metrics
                total_gross_revenue = sum(float(row['gross_revenue']) for row in booking_revenue if row['gross_revenue'])
                total_taxes = sum(float(row['total_taxes']) for row in booking_revenue if row['total_taxes'])
                total_net_revenue = sum(float(row['net_revenue']) for row in booking_revenue if row['net_revenue'])
                
                total_collected = sum(float(row['collected_amount']) for row in payment_reconciliation if row['collected_amount'])
                total_outstanding = sum(float(row['outstanding_balance']) for row in payment_reconciliation if row['outstanding_balance'])
                
                collection_rate = (total_collected / total_net_revenue * 100) if total_net_revenue > 0 else 0
                
                total_refunds = float(refund_stats['total_refunds']) if refund_stats['total_refunds'] else 0
                cancelled_bookings = refund_stats['cancelled_bookings'] or 0
                
                # Format response
                result = f"💳 Financial Reconciliation Report (AGENT-RULE COMPLIANT)\n"
                result += f"Period: {start_date_norm} to {end_date_norm}\n\n"
                
                result += f"📊 REVENUE SUMMARY:\n"
                result += f"  • Gross Revenue: ${total_gross_revenue:,.2f}\n"
                result += f"  • Total Taxes: ${total_taxes:,.2f}\n"
                result += f"  • Net Revenue: ${total_net_revenue:,.2f}\n\n"
                
                result += f"💰 PAYMENT COLLECTION:\n"
                result += f"  • Amount Collected: ${total_collected:,.2f}\n"
                result += f"  • Outstanding Balance: ${total_outstanding:,.2f}\n"
                result += f"  • Collection Rate: {collection_rate:.1f}%\n\n"
                
                result += f"↩️ REFUND ANALYSIS:\n"
                result += f"  • Cancelled Bookings: {cancelled_bookings}\n"
                result += f"  • Total Refunds: ${total_refunds:,.2f}\n"
                result += f"  • Average Refund: ${float(refund_stats['avg_refund']) if refund_stats['avg_refund'] else 0:.2f}\n\n"
                
                result += f"📋 REVENUE BY STATUS (Natural Keys):\n"
                for row in booking_revenue:
                    status = row['booking_status']
                    count = row['booking_count']
                    revenue = float(row['net_revenue']) if row['net_revenue'] else 0
                    result += f"  • {status.upper()}: ${revenue:,.2f} ({count} bookings)\n"
                
                result += f"\n💳 PAYMENT STATUS BREAKDOWN:\n"
                for row in payment_reconciliation:
                    status = row['payment_status']
                    count = row['payment_count']
                    collected = float(row['collected_amount']) if row['collected_amount'] else 0
                    outstanding = float(row['outstanding_balance']) if row['outstanding_balance'] else 0
                    result += f"  • {status.upper()}: ${collected:,.2f} collected, ${outstanding:,.2f} outstanding ({count} records)\n"
                
                result += f"\n🎯 Natural Keys Used: booking_status, payment_status, booking_id, guest_phone"
                return result
        except ValueError as ve:
            return str(ve)
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error: {e}"

    def generate_operational_alerts(self, hotel_name: str) -> str:
        """Generate operational alerts for a specific hotel. Requires hotel_name."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                alerts = []
                
                # Build WHERE clause for hotel filtering
                hotel_filter = ""
                params = []
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                
                # CHECK 1: Overdue payments from payment_tracking table (AGENT-RULE COMPLIANT)
                payment_params = []
                payment_filter = ""
                if hotel_name:
                    payment_filter = " AND EXISTS (SELECT 1 FROM room_bookings rb WHERE rb.booking_id = payment_tracking.booking_id AND rb.hotel_name = %s)"
                    payment_params.append(hotel_name)
                
                cursor.execute(f"""
                    SELECT COUNT(*) as overdue_count,
                           SUM(remaining_balance) as overdue_amount
                    FROM payment_tracking
                    WHERE payment_status IN ('pending', 'partial')
                      AND payment_date < CURRENT_DATE - INTERVAL '7 days'{payment_filter}
                """, payment_params)
                
                overdue_payments = cursor.fetchone()
                if overdue_payments['overdue_count'] and overdue_payments['overdue_count'] > 0:
                    alerts.append(f"🚨 CRITICAL: {overdue_payments['overdue_count']} overdue payments totaling ${float(overdue_payments['overdue_amount']):,.2f}")
                
                # CHECK 2: Low inventory from room_inventory table (natural keys)
                inventory_params = []
                inventory_filter = ""
                if hotel_name:
                    inventory_filter = " WHERE hotel_name = %s AND active_inventory <= 2"
                    inventory_params.append(hotel_name)
                else:
                    inventory_filter = " WHERE active_inventory <= 2"
                
                cursor.execute(f"""
                    SELECT hotel_name, room_type, active_inventory
                    FROM room_inventory{inventory_filter}
                    ORDER BY active_inventory ASC
                """, inventory_params)
                
                low_inventory = cursor.fetchall()
                if low_inventory:
                    alerts.append(f"⚠️ LOW INVENTORY: {len(low_inventory)} room types with ≤2 rooms available")
                    for room in low_inventory[:3]:
                        alerts.append(f"   • {room['hotel_name']} - {room['room_type']}: {room['active_inventory']} rooms")
                
                # CHECK 3: High cancellation rate from room_bookings table
                cursor.execute(f"""
                    SELECT COUNT(*) as total_bookings,
                           COUNT(CASE WHEN booking_status = 'cancelled' THEN 1 END) as cancelled_bookings
                    FROM room_bookings
                    WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'{hotel_filter}
                """, params)
                
                cancellation_stats = cursor.fetchone()
                total_bookings = cancellation_stats['total_bookings'] or 0
                cancelled_bookings = cancellation_stats['cancelled_bookings'] or 0
                
                if total_bookings > 0:
                    cancellation_rate = (cancelled_bookings / total_bookings) * 100
                    if cancellation_rate > 20:
                        alerts.append(f"⚠️ HIGH CANCELLATION RATE: {cancellation_rate:.1f}% ({cancelled_bookings}/{total_bookings} bookings)")
                
                # Format response
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                if not alerts:
                    result = f"✅ OPERATIONAL STATUS: ALL CLEAR\n"
                    result += f"Scope: {scope}\n"
                    result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                    result += f"• No critical alerts detected\n"
                    result += f"• All systems operating normally\n"
                    result += f"• Payment processing on track\n"
                    result += f"• Inventory levels adequate\n"
                else:
                    result = f"🚨 OPERATIONAL ALERTS (AGENT-RULE COMPLIANT)\n"
                    result += f"Scope: {scope}\n"
                    result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                    result += f"📊 ALERT SUMMARY: {len(alerts)} issues detected\n\n"
                    
                    for i, alert in enumerate(alerts, 1):
                        result += f"{i}. {alert}\n"
                
                result += f"\n🎯 Natural Keys Used: hotel_name, room_type, booking_id, payment_status"
                return result
                       
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Operational alerts failed: {str(e)}"

    def read_system_integrity_validation(self,hotel_name: str) -> str:
        """READ comprehensive system integrity validation and health checks"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                integrity_checks = []
                
                # CHECK 1: Bookings without corresponding payments
                cursor.execute("""
                    SELECT COUNT(*) as bookings_without_payments
                    FROM room_bookings b
                    WHERE NOT EXISTS (
                        SELECT 1 FROM payment_tracking p 
                        WHERE p.booking_id = b.booking_id
                    )
                """)
                
                bookings_without_payments = cursor.fetchone()['bookings_without_payments']
                integrity_checks.append(f"Bookings without payment tracking: {bookings_without_payments}")
                
                # CHECK 2: Orphaned payment records
                cursor.execute("""
                    SELECT COUNT(*) as orphaned_payments
                    FROM payment_tracking p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM room_bookings b 
                        WHERE b.booking_id = p.booking_id
                    )
                """)
                
                orphaned_payments = cursor.fetchone()['orphaned_payments']
                integrity_checks.append(f"Orphaned payment records: {orphaned_payments}")
                
                # CHECK 3: Natural key consistency for room inventory
                cursor.execute("""
                    SELECT COUNT(*) as inventory_records,
                           COUNT(CASE WHEN hotel_name IS NULL OR hotel_name = '' THEN 1 END) as missing_hotel_names,
                           COUNT(CASE WHEN room_type IS NULL OR room_type = '' THEN 1 END) as missing_room_types
                    FROM room_inventory
                """)
                
                inventory_check = cursor.fetchone()
                integrity_checks.append(f"Room inventory records: {inventory_check['inventory_records']}")
                integrity_checks.append(f"Missing hotel names in inventory: {inventory_check['missing_hotel_names']}")
                integrity_checks.append(f"Missing room types in inventory: {inventory_check['missing_room_types']}")
                
                # CHECK 4: Booking natural key validation
                cursor.execute("""
                    SELECT COUNT(*) as total_bookings,
                           COUNT(CASE WHEN guest_phone IS NULL OR guest_phone = '' THEN 1 END) as missing_guest_phone,
                           COUNT(CASE WHEN hotel_name IS NULL OR hotel_name = '' THEN 1 END) as missing_hotel_name,
                           COUNT(CASE WHEN room_type IS NULL OR room_type = '' THEN 1 END) as missing_room_type
                    FROM room_bookings
                """)
                
                booking_check = cursor.fetchone()
                integrity_checks.append(f"Total bookings: {booking_check['total_bookings']}")
                integrity_checks.append(f"Missing guest phone: {booking_check['missing_guest_phone']}")
                integrity_checks.append(f"Missing hotel name: {booking_check['missing_hotel_name']}")
                integrity_checks.append(f"Missing room type: {booking_check['missing_room_type']}")
                
                # CHECK 5: Payment amount consistency
                cursor.execute("""
                    SELECT COUNT(*) as inconsistent_payments
                    FROM payment_tracking p
                    JOIN room_bookings b ON p.booking_id = b.booking_id
                    WHERE (p.amount_paid + p.remaining_balance) != b.total_amount
                """)
                
                inconsistent_payments = cursor.fetchone()['inconsistent_payments']
                integrity_checks.append(f"Payment amount inconsistencies: {inconsistent_payments}")
                
                # CHECK 6: Date validity
                cursor.execute("""
                    SELECT COUNT(*) as invalid_dates
                    FROM room_bookings
                    WHERE check_in_date > check_out_date
                       OR check_in_date < '2020-01-01'
                       OR check_out_date > '2030-12-31'
                """)
                
                invalid_dates = cursor.fetchone()['invalid_dates']
                integrity_checks.append(f"Invalid booking dates: {invalid_dates}")
                
                # Calculate integrity score
                critical_issues = sum([
                    bookings_without_payments,
                    orphaned_payments,
                    inventory_check['missing_hotel_names'],
                    inventory_check['missing_room_types'],
                    booking_check['missing_guest_phone'],
                    booking_check['missing_hotel_name'],
                    booking_check['missing_room_type'],
                    inconsistent_payments,
                    invalid_dates
                ])
                
                total_records = (booking_check['total_bookings'] + 
                               inventory_check['inventory_records'])
                
                integrity_score = max(0, 100 - (critical_issues / max(total_records, 1) * 100))
                
                # Format response
                result = f"🔧 System Integrity Validation (AGENT-RULE COMPLIANT)\n"
                result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                
                result += f"📊 INTEGRITY SCORE: {integrity_score:.1f}/100\n"
                result += f"🎯 Critical Issues: {critical_issues}\n\n"
                
                result += f"🔍 DETAILED CHECKS:\n"
                for i, check in enumerate(integrity_checks, 1):
                    status = "✅" if "0" in check.split(": ")[1] else "⚠️"
                    result += f"  {i}. {status} {check}\n"
                
                result += f"\n📋 RECOMMENDATIONS:\n"
                if critical_issues == 0:
                    result += f"  • System integrity is excellent\n"
                    result += f"  • All natural key constraints satisfied\n"
                    result += f"  • No immediate action required\n"
                else:
                    result += f"  • Review and fix {critical_issues} data integrity issues\n"
                    result += f"  • Ensure all natural keys are properly populated\n"
                    result += f"  • Validate booking-payment relationships\n"
                
                result += f"\n🎯 Natural Keys Validated: hotel_name, room_type, guest_phone, booking_id"
                return result
                       
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ System integrity validation failed: {str(e)}"

    def daily_manager_report(self, hotel_name: str, report_date: str = "") -> str:
        """Generate daily manager report for a specific hotel. Requires hotel_name."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Default to today if no date provided
                if not report_date:
                    report_date = datetime.now(timezone.utc).date().isoformat()
                
                # Build WHERE clause for hotel filtering
                hotel_filter = ""
                params = [report_date]
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                
                # READ daily booking performance from room_bookings table
                cursor.execute(f"""
                    SELECT COUNT(*) as total_bookings,
                           COUNT(CASE WHEN booking_status = 'confirmed' THEN 1 END) as confirmed_bookings,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN rooms_booked ELSE 0 END) as rooms_sold,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN total_amount ELSE 0 END) as total_revenue,
                           AVG(CASE WHEN booking_status = 'confirmed' THEN room_rate ELSE NULL END) as adr,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN nights ELSE 0 END) as room_nights
                    FROM room_bookings
                    WHERE check_in_date = %s{hotel_filter}
                """, params)
                
                daily_stats = cursor.fetchone()
                
                # READ total room inventory
                inventory_params = []
                inventory_filter = ""
                if hotel_name:
                    inventory_filter = " WHERE hotel_name = %s"
                    inventory_params.append(hotel_name)
                
                cursor.execute(f"""
                    SELECT SUM(active_inventory) as total_rooms
                    FROM room_inventory{inventory_filter}
                """, inventory_params)
                
                inventory_result = cursor.fetchone()
                total_rooms = inventory_result['total_rooms'] or 0
                
                # Calculate key metrics
                rooms_sold = daily_stats['rooms_sold'] or 0
                total_revenue = float(daily_stats['total_revenue'] or 0)
                adr = float(daily_stats['adr'] or 0)
                
                occupancy_rate = (rooms_sold / total_rooms * 100) if total_rooms > 0 else 0
                revpar = total_revenue / total_rooms if total_rooms > 0 else 0
                
                # Estimate CPOR (Cost Per Occupied Room) - basic calculation
                estimated_operating_costs = total_revenue * 0.35  # Industry average 35%
                cpor = estimated_operating_costs / rooms_sold if rooms_sold > 0 else 0
                
                # Format response
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"📊 DAILY MANAGER REPORT - {report_date}\n"
                result += f"Scope: {scope}\n"
                result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                
                result += f"🏨 KEY PERFORMANCE INDICATORS:\n"
                result += f"  • Occupancy Rate: {occupancy_rate:.1f}%\n"
                result += f"  • Average Daily Rate (ADR): ${adr:.2f}\n"
                result += f"  • Revenue Per Available Room (RevPAR): ${revpar:.2f}\n"
                result += f"  • Total Revenue: ${total_revenue:,.2f}\n"
                result += f"  • Cost Per Occupied Room (CPOR): ${cpor:.2f}\n\n"
                
                result += f"📈 OPERATIONAL METRICS:\n"
                result += f"  • Total Rooms Available: {total_rooms}\n"
                result += f"  • Rooms Sold: {rooms_sold}\n"
                result += f"  • Total Bookings: {daily_stats['total_bookings']}\n"
                result += f"  • Confirmed Bookings: {daily_stats['confirmed_bookings']}\n"
                result += f"  • Room Nights: {daily_stats['room_nights']}\n\n"
                
                result += f"🎯 PERFORMANCE STATUS:\n"
                if occupancy_rate >= 80:
                    result += f"  • 🟢 Excellent occupancy performance\n"
                elif occupancy_rate >= 60:
                    result += f"  • 🟡 Good occupancy performance\n"
                else:
                    result += f"  • 🔴 Low occupancy - review pricing strategy\n"
                
                if adr >= 200:
                    result += f"  • 🟢 Strong average daily rate\n"
                elif adr >= 100:
                    result += f"  • 🟡 Moderate average daily rate\n"
                else:
                    result += f"  • 🔴 Low ADR - consider rate optimization\n"
                
                result += f"\n🎯 Natural Keys Used: hotel_name, room_type, booking_status"
                return result
                       
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Daily Manager Report failed: {str(e)}"

    def daily_forecast_report(self, hotel_name: str, forecast_date: str = "") -> str:
        """Generate daily forecast report for a specific hotel. Requires hotel_name."""
        try:
            default_forecast = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
            forecast_date_norm = (str(forecast_date).strip() or default_forecast)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Build WHERE clause for hotel filtering
                hotel_filter = ""
                params = [forecast_date_norm]
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                
                # READ arrivals for forecast date
                cursor.execute(f"""
                    SELECT COUNT(*) as arrivals,
                           SUM(rooms_booked) as arriving_rooms,
                           SUM(total_amount) as arrival_revenue,
                           AVG(room_rate) as avg_arrival_rate
                    FROM room_bookings
                    WHERE check_in_date = %s 
                      AND booking_status = 'confirmed'{hotel_filter}
                """, params)
                
                arrivals = cursor.fetchone()
                
                # READ departures for forecast date
                cursor.execute(f"""
                    SELECT COUNT(*) as departures,
                           SUM(rooms_booked) as departing_rooms,
                           SUM(total_amount) as departure_revenue
                    FROM room_bookings
                    WHERE check_out_date = %s 
                      AND booking_status = 'confirmed'{hotel_filter}
                """, params)
                
                departures = cursor.fetchone()
                
                # READ stayovers (guests staying through forecast date)
                cursor.execute(f"""
                    SELECT COUNT(*) as stayovers,
                           SUM(rooms_booked) as stayover_rooms
                    FROM room_bookings
                    WHERE check_in_date < %s 
                      AND check_out_date > %s 
                      AND booking_status = 'confirmed'{hotel_filter}
                """, params + [forecast_date_norm])
                
                stayovers = cursor.fetchone()
                
                # READ total room inventory
                inventory_params = []
                inventory_filter = ""
                if hotel_name:
                    inventory_filter = " WHERE hotel_name = %s"
                    inventory_params.append(hotel_name)
                
                cursor.execute(f"""
                    SELECT SUM(active_inventory) as total_rooms
                    FROM room_inventory{inventory_filter}
                """, inventory_params)
                
                inventory_result = cursor.fetchone()
                total_rooms = inventory_result['total_rooms'] or 0
                
                # Calculate forecast metrics
                arriving_rooms = arrivals['arriving_rooms'] or 0
                departing_rooms = departures['departing_rooms'] or 0
                stayover_rooms = stayovers['stayover_rooms'] or 0
                
                total_occupied = arriving_rooms + stayover_rooms
                forecast_occupancy = (total_occupied / total_rooms * 100) if total_rooms > 0 else 0
                
                arrival_revenue = float(arrivals['arrival_revenue'] or 0)
                avg_arrival_rate = float(arrivals['avg_arrival_rate'] or 0)
                
                # Format response
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"🔮 DAILY FORECAST REPORT - {forecast_date_norm}\n"
                result += f"Scope: {scope}\n"
                result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                
                result += f"📅 ARRIVAL FORECAST:\n"
                result += f"  • Expected Arrivals: {arrivals['arrivals']} guests\n"
                result += f"  • Arriving Rooms: {arriving_rooms}\n"
                result += f"  • Arrival Revenue: ${arrival_revenue:,.2f}\n"
                result += f"  • Average Arrival Rate: ${avg_arrival_rate:.2f}\n\n"
                
                result += f"🚪 DEPARTURE FORECAST:\n"
                result += f"  • Expected Departures: {departures['departures']} guests\n"
                result += f"  • Departing Rooms: {departing_rooms}\n"
                result += f"  • Departure Revenue: ${float(departures['departure_revenue'] or 0):,.2f}\n\n"
                
                result += f"🏠 OCCUPANCY FORECAST:\n"
                result += f"  • Stayover Guests: {stayovers['stayovers']}\n"
                result += f"  • Stayover Rooms: {stayover_rooms}\n"
                result += f"  • Total Occupied Rooms: {total_occupied}\n"
                result += f"  • Forecast Occupancy: {forecast_occupancy:.1f}%\n"
                result += f"  • Available Rooms: {total_rooms - total_occupied}\n\n"
                
                result += f"📊 OPERATIONAL PLANNING:\n"
                if forecast_occupancy >= 90:
                    result += f"  • 🔴 High occupancy - prepare for full capacity\n"
                    result += f"  • 🔧 Ensure all rooms are service-ready\n"
                    result += f"  • 👥 Schedule full housekeeping staff\n"
                elif forecast_occupancy >= 70:
                    result += f"  • 🟡 Good occupancy - standard operations\n"
                    result += f"  • 📈 Opportunity for last-minute upsells\n"
                else:
                    result += f"  • 🟢 Low occupancy - focus on revenue optimization\n"
                    result += f"  • 💰 Consider promotional pricing\n"
                
                result += f"\n🎯 Natural Keys Used: hotel_name, check_in_date, check_out_date, booking_status"
                return result
        except ValueError as ve:
            return str(ve)
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error: {e}"

    def weekly_pace_report(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Generate weekly pace report for a specific hotel. Requires hotel_name."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Default to last 7 days if no dates provided
                if not start_date:
                    start_date = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
                if not end_date:
                    end_date = datetime.now(timezone.utc).date().isoformat()
                
                # Build WHERE clause for hotel filtering
                hotel_filter = ""
                params = [start_date, end_date]
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                
                # READ current period booking pace
                cursor.execute(f"""
                    SELECT DATE(created_at) as booking_date,
                           COUNT(*) as daily_bookings,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN total_amount ELSE 0 END) as daily_revenue,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN rooms_booked ELSE 0 END) as daily_rooms,
                           AVG(CASE WHEN booking_status = 'confirmed' THEN room_rate ELSE NULL END) as daily_adr
                    FROM room_bookings
                    WHERE DATE(created_at) >= %s 
                      AND DATE(created_at) <= %s{hotel_filter}
                    GROUP BY DATE(created_at)
                    ORDER BY booking_date
                """, params)
                
                current_pace = cursor.fetchall()
                
                # READ comparison period (same period last month)
                y1,m1,d1 = [int(x) for x in str(start_date).split('-')]
                y2,m2,d2 = [int(x) for x in str(end_date).split('-')]
                comparison_start = (date(y1,m1,d1) - timedelta(days=30)).isoformat()
                comparison_end = (date(y2,m2,d2) - timedelta(days=30)).isoformat()
                
                comparison_params = [comparison_start, comparison_end]
                if hotel_name:
                    comparison_params.append(hotel_name)
                
                cursor.execute(f"""
                    SELECT COUNT(*) as total_bookings,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN total_amount ELSE 0 END) as total_revenue,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN rooms_booked ELSE 0 END) as total_rooms,
                           AVG(CASE WHEN booking_status = 'confirmed' THEN room_rate ELSE NULL END) as avg_adr
                    FROM room_bookings
                    WHERE DATE(created_at) >= %s 
                      AND DATE(created_at) <= %s{hotel_filter}
                """, comparison_params)
                
                comparison_data = cursor.fetchone()
                
                # Calculate current period totals
                current_bookings = sum(row['daily_bookings'] for row in current_pace)
                current_revenue = sum(float(row['daily_revenue'] or 0) for row in current_pace)
                current_rooms = sum(row['daily_rooms'] or 0 for row in current_pace)
                current_adr = current_revenue / current_rooms if current_rooms > 0 else 0
                
                # Calculate pace changes
                comparison_bookings = comparison_data['total_bookings'] or 0
                comparison_revenue = float(comparison_data['total_revenue'] or 0)
                comparison_rooms = comparison_data['total_rooms'] or 0
                comparison_adr = float(comparison_data['avg_adr'] or 0)
                
                booking_pace_change = ((current_bookings - comparison_bookings) / comparison_bookings * 100) if comparison_bookings > 0 else 0
                revenue_pace_change = ((current_revenue - comparison_revenue) / comparison_revenue * 100) if comparison_revenue > 0 else 0
                adr_pace_change = ((current_adr - comparison_adr) / comparison_adr * 100) if comparison_adr > 0 else 0
                
                # Format response
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"📈 WEEKLY PACE REPORT\n"
                result += f"Scope: {scope}\n"
                result += f"Period: {start_date} to {end_date}\n"
                result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                
                result += f"📊 CURRENT PERIOD PERFORMANCE:\n"
                result += f"  • Total Bookings: {current_bookings}\n"
                result += f"  • Total Revenue: ${current_revenue:,.2f}\n"
                result += f"  • Rooms Booked: {current_rooms}\n"
                result += f"  • Average ADR: ${current_adr:.2f}\n\n"
                
                result += f"🔄 PACE COMPARISON (vs Same Period Last Month):\n"
                result += f"  • Booking Pace: {booking_pace_change:+.1f}%\n"
                result += f"  • Revenue Pace: {revenue_pace_change:+.1f}%\n"
                result += f"  • ADR Pace: {adr_pace_change:+.1f}%\n\n"
                
                result += f"📅 DAILY BOOKING TREND:\n"
                for row in current_pace:
                    booking_date = row['booking_date']
                    daily_bookings = row['daily_bookings']
                    daily_revenue = float(row['daily_revenue'] or 0)
                    result += f"  • {booking_date}: {daily_bookings} bookings, ${daily_revenue:,.2f} revenue\n"
                
                result += f"\n🎯 PACE ANALYSIS:\n"
                if booking_pace_change > 10:
                    result += f"  • 🟢 Strong booking momentum (+{booking_pace_change:.1f}%)\n"
                elif booking_pace_change > 0:
                    result += f"  • 🟡 Positive booking trend (+{booking_pace_change:.1f}%)\n"
                else:
                    result += f"  • 🔴 Declining booking pace ({booking_pace_change:.1f}%)\n"
                
                if revenue_pace_change > 10:
                    result += f"  • 🟢 Excellent revenue growth (+{revenue_pace_change:.1f}%)\n"
                elif revenue_pace_change > 0:
                    result += f"  • 🟡 Revenue improvement (+{revenue_pace_change:.1f}%)\n"
                else:
                    result += f"  • 🔴 Revenue decline ({revenue_pace_change:.1f}%)\n"
                
                result += f"\n🎯 Natural Keys Used: hotel_name, booking_status, created_at"
                return result
                       
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Weekly Pace Report failed: {str(e)}"

    def occupancy_revenue_analysis(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Generate occupancy & revenue analysis for a specific hotel. Requires hotel_name."""
        date_context = {'today': datetime.now(timezone.utc).date().isoformat()}
        try:
            default_start = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
            default_end = datetime.now(timezone.utc).date().isoformat()
            start_date_norm = (str(start_date).strip() or default_start)
            end_date_norm = (str(end_date).strip() or default_end)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Build WHERE clause for hotel filtering
                hotel_filter = ""
                params = [start_date_norm, end_date_norm]
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                
                # READ occupancy and revenue by room type
                cursor.execute(f"""
                    SELECT hotel_name,
                           room_type,
                           COUNT(*) as total_bookings,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN rooms_booked ELSE 0 END) as rooms_sold,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN total_amount ELSE 0 END) as revenue,
                           AVG(CASE WHEN booking_status = 'confirmed' THEN room_rate ELSE NULL END) as avg_rate,
                           SUM(CASE WHEN booking_status = 'confirmed' THEN nights ELSE 0 END) as room_nights,
                           AVG(CASE WHEN booking_status = 'confirmed' THEN nights ELSE NULL END) as avg_los
                    FROM room_bookings
                    WHERE check_in_date >= %s 
                      AND check_in_date <= %s{hotel_filter}
                    GROUP BY hotel_name, room_type
                    ORDER BY revenue DESC
                """, params)
                
                room_performance = cursor.fetchall()
                
                # READ room inventory for occupancy calculation
                inventory_params = []
                inventory_filter = ""
                if hotel_name:
                    inventory_filter = " WHERE hotel_name = %s"
                    inventory_params.append(hotel_name)
                
                cursor.execute(f"""
                    SELECT hotel_name,
                           room_type,
                           active_inventory,
                           total_inventory
                    FROM room_inventory{inventory_filter}
                    ORDER BY hotel_name, room_type
                """, inventory_params)
                
                inventory_data = cursor.fetchall()
                
                # Create inventory lookup
                inventory_lookup = {}
                total_rooms = 0
                for inv in inventory_data:
                    key = f"{inv['hotel_name']}_{inv['room_type']}"
                    inventory_lookup[key] = inv['active_inventory']
                    total_rooms += inv['active_inventory']
                
                # Calculate period length for occupancy calculation
                y1,m1,d1 = [int(x) for x in str(start_date_norm).split('-')]
                y2,m2,d2 = [int(x) for x in str(end_date_norm).split('-')]
                period_days = (date(y2,m2,d2) - date(y1,m1,d1)).days + 1
                
                # Calculate totals and metrics
                total_revenue = sum(float(row['revenue'] or 0) for row in room_performance)
                total_rooms_sold = sum(row['rooms_sold'] or 0 for row in room_performance)
                total_room_nights = sum(row['room_nights'] or 0 for row in room_performance)
                
                overall_occupancy = (total_room_nights / (total_rooms * period_days) * 100) if total_rooms > 0 else 0
                overall_adr = total_revenue / total_rooms_sold if total_rooms_sold > 0 else 0
                overall_revpar = total_revenue / (total_rooms * period_days) if total_rooms > 0 else 0
                
                # Format response
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"🏨 OCCUPANCY & REVENUE ANALYSIS\n"
                result += f"Scope: {scope}\n"
                result += f"Period: {start_date_norm} to {end_date_norm} ({period_days} days)\n"
                result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                
                result += f"📊 OVERALL PERFORMANCE:\n"
                result += f"  • Overall Occupancy: {overall_occupancy:.1f}%\n"
                result += f"  • Overall ADR: ${overall_adr:.2f}\n"
                result += f"  • Overall RevPAR: ${overall_revpar:.2f}\n"
                result += f"  • Total Revenue: ${total_revenue:,.2f}\n"
                result += f"  • Total Room Nights: {total_room_nights:,}\n"
                result += f"  • Total Rooms Available: {total_rooms * period_days:,}\n\n"
                
                result += f"🏠 ROOM TYPE PERFORMANCE:\n"
                for i, row in enumerate(room_performance[:10]):  # Top 10 performing room types
                    hotel_name_result = row['hotel_name']
                    room_type = row['room_type']
                    revenue = float(row['revenue'] or 0)
                    rooms_sold = row['rooms_sold'] or 0
                    avg_rate = float(row['avg_rate'] or 0)
                    avg_los = float(row['avg_los'] or 0)
                    room_nights = row['room_nights'] or 0
                    
                    # Calculate occupancy for this room type
                    inventory_key = f"{hotel_name_result}_{room_type}"
                    room_inventory = inventory_lookup.get(inventory_key, 0)
                    room_occupancy = (room_nights / (room_inventory * period_days) * 100) if room_inventory > 0 else 0
                    room_revpar = revenue / (room_inventory * period_days) if room_inventory > 0 else 0
                    
                    result += f"  {i+1}. {hotel_name_result} - {room_type}:\n"
                    result += f"     • Revenue: ${revenue:,.2f}\n"
                    result += f"     • Occupancy: {room_occupancy:.1f}%\n"
                    result += f"     • ADR: ${avg_rate:.2f}\n"
                    result += f"     • RevPAR: ${room_revpar:.2f}\n"
                    result += f"     • Avg LOS: {avg_los:.1f} nights\n\n"
                
                result += f"🎯 PERFORMANCE INSIGHTS:\n"
                if overall_occupancy >= 75:
                    result += f"  • 🟢 Strong occupancy performance\n"
                elif overall_occupancy >= 50:
                    result += f"  • 🟡 Moderate occupancy - room for improvement\n"
                else:
                    result += f"  • 🔴 Low occupancy - review pricing and marketing\n"
                
                if overall_revpar >= 150:
                    result += f"  • 🟢 Excellent RevPAR performance\n"
                elif overall_revpar >= 100:
                    result += f"  • 🟡 Good RevPAR performance\n"
                else:
                    result += f"  • 🔴 Low RevPAR - optimize revenue strategy\n"
                
                result += f"\n🎯 Natural Keys Used: hotel_name, room_type, booking_status"
                return result
        except ValueError as ve:
            return str(ve)
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Error: {e}"

    def channel_performance_report(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Generate channel performance report for a specific hotel. Requires hotel_name."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Default to last 30 days if no dates provided
                if not start_date:
                    start_date = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
                if not end_date:
                    end_date = datetime.now(timezone.utc).date().isoformat()
                
                # Build WHERE clause for hotel filtering
                hotel_filter = ""
                params = [start_date, end_date]
                if hotel_name:
                    hotel_filter = " AND hotel_name = %s"
                    params.append(hotel_name)
                
                # READ channel performance by analyzing booking patterns
                # Note: Since we don't have explicit channel data, we'll analyze based on booking_id patterns
                cursor.execute(f"""
                    SELECT 
                        CASE 
                            WHEN booking_id LIKE '%booking%' THEN 'Direct Booking'
                            WHEN booking_id LIKE '%+%' THEN 'Phone/Walk-in'
                            WHEN LENGTH(booking_id) > 20 THEN 'OTA Channel'
                            ELSE 'Unknown Channel'
                        END as channel,
                        COUNT(*) as total_bookings,
                        COUNT(CASE WHEN booking_status = 'confirmed' THEN 1 END) as confirmed_bookings,
                        COUNT(CASE WHEN booking_status = 'cancelled' THEN 1 END) as cancelled_bookings,
                        SUM(CASE WHEN booking_status = 'confirmed' THEN total_amount ELSE 0 END) as channel_revenue,
                        AVG(CASE WHEN booking_status = 'confirmed' THEN room_rate ELSE NULL END) as avg_rate,
                        SUM(CASE WHEN booking_status = 'confirmed' THEN rooms_booked ELSE 0 END) as rooms_sold,
                        AVG(CASE WHEN booking_status = 'confirmed' THEN nights ELSE NULL END) as avg_los
                    FROM room_bookings
                    WHERE created_at >= %s 
                      AND created_at <= %s{hotel_filter}
                    GROUP BY 
                        CASE 
                            WHEN booking_id LIKE '%booking%' THEN 'Direct Booking'
                            WHEN booking_id LIKE '%+%' THEN 'Phone/Walk-in'
                            WHEN LENGTH(booking_id) > 20 THEN 'OTA Channel'
                            ELSE 'Unknown Channel'
                        END
                    ORDER BY channel_revenue DESC
                """, params)
                
                channel_data = cursor.fetchall()
                
                # READ booking timing analysis (lead time patterns)
                cursor.execute(f"""
                    SELECT 
                        CASE 
                            WHEN (check_in_date - DATE(created_at)) <= 1 THEN 'Same Day/Next Day'
                            WHEN (check_in_date - DATE(created_at)) <= 7 THEN 'Within Week'
                            WHEN (check_in_date - DATE(created_at)) <= 30 THEN 'Within Month'
                            ELSE 'Advance Booking'
                        END as booking_window,
                        COUNT(*) as bookings,
                        SUM(CASE WHEN booking_status = 'confirmed' THEN total_amount ELSE 0 END) as revenue,
                        AVG(CASE WHEN booking_status = 'confirmed' THEN room_rate ELSE NULL END) as avg_rate
                    FROM room_bookings
                    WHERE created_at >= %s 
                      AND created_at <= %s{hotel_filter}
                    GROUP BY 
                        CASE 
                            WHEN (check_in_date - DATE(created_at)) <= 1 THEN 'Same Day/Next Day'
                            WHEN (check_in_date - DATE(created_at)) <= 7 THEN 'Within Week'
                            WHEN (check_in_date - DATE(created_at)) <= 30 THEN 'Within Month'
                            ELSE 'Advance Booking'
                        END
                    ORDER BY revenue DESC
                """, params)
                
                timing_data = cursor.fetchall()
                
                # Calculate totals
                total_revenue = sum(float(row['channel_revenue'] or 0) for row in channel_data)
                total_bookings = sum(row['total_bookings'] for row in channel_data)
                total_confirmed = sum(row['confirmed_bookings'] for row in channel_data)
                
                # Format response
                scope = f"Hotel: {hotel_name}" if hotel_name else "All Hotels (System-wide)"
                result = f"📊 CHANNEL PERFORMANCE REPORT\n"
                result += f"Scope: {scope}\n"
                result += f"Period: {start_date} to {end_date}\n"
                result += f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
                
                result += f"🌐 CHANNEL PERFORMANCE OVERVIEW:\n"
                result += f"  • Total Bookings: {total_bookings}\n"
                result += f"  • Confirmed Bookings: {total_confirmed}\n"
                result += f"  • Total Revenue: ${total_revenue:,.2f}\n"
                result += f"  • Overall Conversion Rate: {(total_confirmed/total_bookings*100):.1f}%\n\n" if total_bookings > 0 else "  • Overall Conversion Rate: 0.0%\n\n"
                
                result += f"📈 CHANNEL BREAKDOWN:\n"
                for row in channel_data:
                    channel = row['channel']
                    revenue = float(row['channel_revenue'] or 0)
                    bookings = row['total_bookings']
                    confirmed = row['confirmed_bookings']
                    cancelled = row['cancelled_bookings']
                    avg_rate = float(row['avg_rate'] or 0)
                    rooms_sold = row['rooms_sold'] or 0
                    avg_los = float(row['avg_los'] or 0)
                    
                    revenue_share = (revenue / total_revenue * 100) if total_revenue > 0 else 0
                    conversion_rate = (confirmed / bookings * 100) if bookings > 0 else 0
                    cancellation_rate = (cancelled / bookings * 100) if bookings > 0 else 0
                    
                    result += f"  📊 {channel}:\n"
                    result += f"     • Revenue: ${revenue:,.2f} ({revenue_share:.1f}% share)\n"
                    result += f"     • Bookings: {bookings} ({confirmed} confirmed, {cancelled} cancelled)\n"
                    result += f"     • Conversion Rate: {conversion_rate:.1f}%\n"
                    result += f"     • Cancellation Rate: {cancellation_rate:.1f}%\n"
                    result += f"     • Average Rate: ${avg_rate:.2f}\n"
                    result += f"     • Rooms Sold: {rooms_sold}\n"
                    result += f"     • Average LOS: {avg_los:.1f} nights\n\n"
                
                result += f"⏰ BOOKING TIMING ANALYSIS:\n"
                for row in timing_data:
                    booking_window = row['booking_window']
                    bookings = row['bookings']
                    revenue = float(row['revenue'] or 0)
                    avg_rate = float(row['avg_rate'] or 0)
                    
                    booking_share = (bookings / total_bookings * 100) if total_bookings > 0 else 0
                    
                    result += f"  • {booking_window}:\n"
                    result += f"     • Bookings: {bookings} ({booking_share:.1f}% of total)\n"
                    result += f"     • Revenue: ${revenue:,.2f}\n"
                    result += f"     • Average Rate: ${avg_rate:.2f}\n\n"
                
                result += f"🎯 CHANNEL INSIGHTS:\n"
                if channel_data:
                    top_channel = channel_data[0]
                    top_revenue_share = (float(top_channel['channel_revenue'] or 0) / total_revenue * 100) if total_revenue > 0 else 0
                    result += f"  • Top Channel: {top_channel['channel']} ({top_revenue_share:.1f}% revenue share)\n"
                    
                    # Analyze conversion rates
                    best_conversion = max(channel_data, key=lambda x: (x['confirmed_bookings'] / x['total_bookings']) if x['total_bookings'] > 0 else 0)
                    best_conv_rate = (best_conversion['confirmed_bookings'] / best_conversion['total_bookings'] * 100) if best_conversion['total_bookings'] > 0 else 0
                    result += f"  • Best Conversion: {best_conversion['channel']} ({best_conv_rate:.1f}%)\n"
                    
                    # Revenue per booking analysis
                    if total_confirmed > 0:
                        revenue_per_booking = total_revenue / total_confirmed
                        result += f"  • Revenue per Confirmed Booking: ${revenue_per_booking:.2f}\n"
                
                result += f"\n🎯 Natural Keys Used: hotel_name, booking_id, booking_status, created_at"
                return result
                       
        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            return f"❌ Channel Performance Report failed: {str(e)}"

    def read_revenue_analytics(self, hotel_name: str, start_date: str = "", end_date: str = "") -> str:
        """Read revenue analytics for a specific hotel. Requires hotel_name."""
        return self.calculate_revenue_analytics(hotel_name, start_date, end_date)

# Singleton instance
report_manager = ReportManager()

@tool
def calculate_revenue_analytics(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Revenue analytics from room_bookings: totals by status, total/avg revenue, top rooms. Use for revenue/amount/ADR/paid queries."""
    return report_manager.calculate_revenue_analytics(hotel_name, start_date, end_date)

@tool
def generate_occupancy_and_booking_analytics(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Consolidated report: Occupancy (booking_stays + room_inventory) + Booking revenue (payment_tracking). Use when user asks occupancy or both."""
    return report_manager.generate_occupancy_and_booking_analytics(hotel_name, start_date, end_date)

@tool
def generate_guest_analytics(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Guest analytics: top spenders, repeat guests, preferences, unique guests. Use for guest behavior/loyalty questions."""
    return report_manager.generate_guest_analytics(hotel_name, start_date, end_date)

@tool
def generate_performance_dashboard(hotel_name: str) -> str:
    """Performance dashboard (today/yesterday/week): bookings, revenue, inventory snapshot, pending payments. Use for 'today'/'dashboard' requests."""
    return report_manager.generate_performance_dashboard(hotel_name)

@tool
def generate_financial_reconciliation(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Financial reconciliation: bookings vs payments, collections, outstanding, refunds. Use for finance/accounting/audit queries."""
    return report_manager.generate_financial_reconciliation(hotel_name, start_date, end_date)

@tool
def generate_operational_alerts(hotel_name: str) -> str:
    """Operational alerts: overdue payments, low inventory, high cancellations. Use for 'any issues/alerts' queries."""
    return report_manager.generate_operational_alerts(hotel_name)

@tool
def daily_manager_report(hotel_name: str, report_date: str = "") -> str:
    """Daily Manager Report (single date): occupancy %, ADR, RevPAR, revenue, rooms sold. Use for 'daily report' on a specific date."""
    return report_manager.daily_manager_report(hotel_name, report_date)

@tool
def daily_forecast_report(hotel_name: str, forecast_date: str = "") -> str:
    """Daily Forecast (single date): arrivals, departures, stayovers, forecast occupancy. Use for 'tomorrow forecast' etc."""
    return report_manager.daily_forecast_report(hotel_name, forecast_date)

@tool
def weekly_pace_report(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Weekly Pace (range): booking/revenue pace and trend vs same period last month. Use for 'pace'/'trend' questions."""
    return report_manager.weekly_pace_report(hotel_name, start_date, end_date)

@tool
def occupancy_revenue_analysis(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Occupancy & Revenue Analysis by room type: occupancy %, ADR, RevPAR, revenue. Use for combined ops + revenue by room type."""
    return report_manager.occupancy_revenue_analysis(hotel_name, start_date, end_date)

@tool
def channel_performance_report(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Channel performance (derived): revenue share, conversion, cancellation, ADR by channel. Use for 'channel' questions."""
    return report_manager.channel_performance_report(hotel_name, start_date, end_date)

@tool
def read_revenue_analytics(hotel_name: str, start_date: str = "", end_date: str = "") -> str:
    """Alias of revenue analytics (calculate_revenue_analytics). Use interchangeably for revenue KPIs."""
    return report_manager.read_revenue_analytics(hotel_name, start_date, end_date)

# REPORT MANAGER AGENT CLASS
