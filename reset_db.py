#!/usr/bin/env python3
"""
Database Reset Script for BusHub
Reinitializes the database with sample data
"""

import mysql.connector
import json
from datetime import datetime, timedelta
import os

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'bhavana@123xxjb'),
    'database': os.environ.get('DB_NAME', 'bus_booking')
}

def reset_database():
    """Reset and reinitialize the database with fresh data"""
    
    try:
        # Connect to MySQL
        db = mysql.connector.connect(**DB_CONFIG)
        cursor = db.cursor()
        
        print("🔄 Resetting database...")
        
        # Clear existing data
        cursor.execute("DELETE FROM routes")
        cursor.execute("DELETE FROM seat_details")
        cursor.execute("DELETE FROM bookings")
        cursor.execute("DELETE FROM buses")
        cursor.execute("DELETE FROM users")
        
        print("✅ Cleared old data")
        
        # Get today's date for sample buses
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Insert sample buses with today's date
        buses_data = [
            ('KSRTC Express', 'Kochi', 'Coimbatore', '["Kochi", "Thrissur", "Palakkad", "Coimbatore"]', 
             '08:00:00', '16:00:00', today, 1200.00, 40, 'AC', '["WiFi", "Charging", "Water", "Blanket"]', 'KSRTC'),
            ('Super Deluxe', 'Calicut', 'Trivandrum', '["Calicut", "Kannur", "Kochi", "Trivandrum"]',
             '09:00:00', '20:00:00', today, 1500.00, 45, 'Sleeper', '["WiFi", "AC", "Water"]', 'Private'),
            ('TNSTC Express', 'Chennai', 'Coimbatore', '["Chennai", "Vellore", "Salem", "Coimbatore"]',
             '06:00:00', '14:00:00', today, 1000.00, 40, 'Non-AC', '["Water"]', 'TNSTC'),
            ('Volvo AC', 'Bangalore', 'Chennai', '["Bangalore", "Hosur", "Krishnagiri", "Chennai"]',
             '22:00:00', '06:00:00', today, 1800.00, 40, 'AC', '["WiFi", "Charging", "Water", "Entertainment"]', 'Volvo'),
            ('Shivneri', 'Mumbai', 'Pune', '["Mumbai", "Thane", "Pune"]',
             '07:00:00', '10:00:00', today, 600.00, 50, 'AC', '["WiFi", "Water"]', 'MSRTC'),
        ]
        
        for bus in buses_data:
            cursor.execute("""
                INSERT INTO buses (bus_name, source, destination, stops, departure_time, arrival_time, 
                                  travel_date, price, seats_total, bus_type, amenities, operator)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, bus)
        
        db.commit()
        print(f"✅ Inserted 5 sample buses for date: {today}")
        
        # Get bus IDs
        cursor.execute("SELECT id, source, destination, stops FROM buses")
        buses = cursor.fetchall()
        
        # Insert routes
        routes_data = []
        for bus_id, source, destination, stops_json in buses:
            try:
                stops = json.loads(stops_json)
            except:
                stops = [source, destination]
            
            for i, stop in enumerate(stops, 1):
                routes_data.append((bus_id, stop, i))
        
        for route in routes_data:
            cursor.execute("""
                INSERT INTO routes (bus_id, stop_name, stop_order)
                VALUES (%s, %s, %s)
            """, route)
        
        db.commit()
        print(f"✅ Inserted {len(routes_data)} routes")
        
        # Insert seat details for all buses
        seat_count = 0
        for bus_id, _, _, _ in buses:
            cursor.execute("SELECT seats_total FROM buses WHERE id = %s", (bus_id,))
            result = cursor.fetchone()
            seats_total = result[0] if result else 40
            
            for seat_num in range(1, seats_total + 1):
                cursor.execute("""
                    INSERT INTO seat_details (bus_id, seat_number, seat_type, deck, gender_restriction, price_modifier)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (bus_id, str(seat_num), 'Seater', 'Lower', 'None', 1.0))
                seat_count += 1
        
        db.commit()
        print(f"✅ Inserted {seat_count} seat details")
        
        cursor.close()
        db.close()
        
        print("\n" + "="*50)
        print("✅ Database reset successfully!")
        print("="*50)
        print(f"\nSample data has been loaded for date: {today}")
        print("\nYou can now:")
        print("1. Register a new user")
        print("2. Login with your credentials")
        print("3. Search for buses from Kochi to Coimbatore, etc.")
        print("4. View available routes for each bus")
        print("\n" + "="*50)
        
    except mysql.connector.Error as err:
        print(f"❌ Database error: {err}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    
    return True

if __name__ == "__main__":
    import sys
    print("\n🚌 BusHub - Database Reset Tool\n")
    
    # Auto-run if --auto flag is provided
    if '--auto' in sys.argv or '--force' in sys.argv:
        reset_database()
    else:
        print("This script will reset the database and load sample data for today.\n")
        response = input("Are you sure you want to reset the database? (yes/no): ").strip().lower()
        
        if response == 'yes':
            reset_database()
        else:
            print("❌ Operation cancelled.")
