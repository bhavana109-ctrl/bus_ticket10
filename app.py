# ================= IMPORTS =================
from flask import Flask, Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
import random
import re
import json
import os
from datetime import datetime, timedelta
from flask_mail import Mail, Message
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import qrcode
import io
import base64

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'bus_booking_2026')

from flask_mail import Mail, Message

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True

app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'mbhavana109@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'rmrq apsv jpym jjie ')
app.config['MAIL_DEBUG'] = True
app.config['OTP_DEV_FALLBACK'] = os.environ.get('OTP_DEV_FALLBACK', True)

mail = Mail(app)

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'bhavana@123xxjb'),
    'database': os.environ.get('DB_NAME', 'bus_booking')
}

# OTP store (in production, use Redis or database)
otp_store = {}

# Blueprints
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')
user_bp = Blueprint('user', __name__, url_prefix='/user')
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def get_db_connection():
    """Get database connection"""
    return mysql.connector.connect(**DB_CONFIG)

def generate_otp():
    """Generate 6-digit OTP"""
    return str(random.randint(100000, 999999))

def is_strong_password(password):
    """Check if password meets requirements"""
    return (
        len(password) >= 8 and
        re.search(r"[A-Z]", password) and
        re.search(r"[a-z]", password) and
        re.search(r"\d", password)
    )

def calculate_duration(departure_time, arrival_time):
    """Calculate travel duration"""
    dep = datetime.strptime(str(departure_time), '%H:%M:%S')
    arr = datetime.strptime(str(arrival_time), '%H:%M:%S')
    if arr < dep:  # Next day arrival
        arr = arr + timedelta(days=1)
    duration = arr - dep
    hours = duration.seconds // 3600
    minutes = (duration.seconds % 3600) // 60
    return f"{hours}h {minutes}m"

def get_available_seats(bus_id):
    """Get available seats for a bus"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Get all booked seats
    cursor.execute("""
        SELECT seats FROM bookings
        WHERE bus_id = %s AND status = 'confirmed'
    """, (bus_id,))
    bookings = cursor.fetchall()

    booked_seats = set()
    for booking in bookings:
        seats_data = json.loads(booking['seats'])
        for seat in seats_data:
            booked_seats.add(seat['number'])

    # Get total seats
    cursor.execute("SELECT seats_total FROM buses WHERE id = %s", (bus_id,))
    bus = cursor.fetchone()
    total_seats = bus['seats_total'] if bus else 40

    available_count = total_seats - len(booked_seats)
    return available_count, list(booked_seats)


def ensure_bus_schema():
    """Ensure buses table has optional columns used by the app"""
    db = get_db_connection()
    cursor = db.cursor()

    optional_columns = {
        'stops': "JSON NULL",
        'amenities': "JSON NULL",
        'bus_type': "VARCHAR(20) DEFAULT 'Non-AC'",
    }

    for col, definition in optional_columns.items():
        cursor.execute(f"SHOW COLUMNS FROM buses LIKE '{col}'")
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE buses ADD COLUMN {col} {definition}")

    # Ensure bookings table has status column
    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'status'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE bookings ADD COLUMN status ENUM('confirmed', 'cancelled') DEFAULT 'confirmed'")

    db.commit()
    cursor.close()
    db.close()


def ensure_routes_for_buses():
    """Ensure routes table has entries for all buses"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) as cnt FROM routes")
    total_routes = cursor.fetchone()['cnt']

    if total_routes == 0:
        cursor.execute("SHOW COLUMNS FROM buses LIKE 'stops'")
        has_stops_col = cursor.fetchone() is not None

        if has_stops_col:
            cursor.execute("SELECT id, source, destination, stops FROM buses")
        else:
            cursor.execute("SELECT id, source, destination FROM buses")

        buses = cursor.fetchall()

        for bus in buses:
            if has_stops_col and bus.get('stops'):
                try:
                    stops = json.loads(bus['stops'])
                except Exception:
                    stops = []
            else:
                stops = []

            if not stops and bus.get('source') and bus.get('destination'):
                stops = [bus['source'], bus['destination']]

            for i, stop in enumerate(stops):
                cursor.execute(
                    "INSERT INTO routes (bus_id, stop_name, stop_order) VALUES (%s, %s, %s)",
                    (bus['id'], stop, i + 1)
                )

        db.commit()

    cursor.close()
    db.close()


def ensure_seat_details_for_buses():
    """Ensure seat_details table is populated for buses"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) as cnt FROM seat_details")
    total_seats = cursor.fetchone()['cnt']

    if total_seats == 0:
        cursor.execute("SELECT id, seats_total FROM buses")
        buses = cursor.fetchall()

        for bus in buses:
            seats_total = bus.get('seats_total', 40)
            for seat_number in range(1, seats_total + 1):
                cursor.execute(
                    "INSERT INTO seat_details (bus_id, seat_number, seat_type, deck, gender_restriction, price_modifier) VALUES (%s, %s, %s, %s, %s, %s)",
                    (bus['id'], str(seat_number), 'Seater', 'Lower', 'None', 1.0)
                )

        db.commit()

    cursor.close()
    db.close()


db_initialized = False

def initialize_db():
    """Initialize missing DB-supported data"""
    global db_initialized
    if db_initialized:
        return

    try:
        ensure_bus_schema()
        ensure_routes_for_buses()
        ensure_seat_details_for_buses()
        db_initialized = True
    except Exception as e:
        print(f"Initialization warning: {e}")


@app.before_request
def initialize_once_before_request():
    initialize_db()


def send_email(subject, recipients, body, html=None):
    """Send email"""
    try:
        msg = Message(subject, sender=app.config['MAIL_USERNAME'], recipients=recipients)
        msg.body = body
        if html:
            msg.html = html
        mail.send(msg)
        print("Email sent successfully ✅")  # debug
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def generate_ticket_pdf(booking):
    """Generate PDF ticket"""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Title
    c.setFont("Helvetica-Bold", 20)
    c.drawString(200, height - 50, "BusHub - E-Ticket")

    # Booking details
    c.setFont("Helvetica", 12)
    y = height - 100
    c.drawString(50, y, f"Booking ID: {booking['id']}")
    c.drawString(50, y - 20, f"Passenger: {booking['passenger_name']}")
    c.drawString(50, y - 40, f"Bus: {booking['bus_name']}")
    c.drawString(50, y - 60, f"Route: {booking['source']} → {booking['destination']}")
    c.drawString(50, y - 80, f"Date: {booking['travel_date']}")
    c.drawString(50, y - 100, f"Seats: {', '.join([s['number'] for s in json.loads(booking['seats'])])}")
    c.drawString(50, y - 120, f"Boarding: {booking['boarding_point']}")
    c.drawString(50, y - 140, f"Dropping: {booking['dropping_point']}")
    c.drawString(50, y - 160, f"Total Amount: ₹{booking['total_amount']}")

    # QR Code
    qr = qrcode.QRCode(version=1, box_size=5, border=2)
    qr.add_data(f"Booking ID: {booking['id']}")
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    # Save QR to buffer
    qr_buffer = io.BytesIO()
    qr_img.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)

    # Draw QR code on PDF
    from reportlab.lib.utils import ImageReader
    qr_reader = ImageReader(qr_buffer)
    c.drawImage(qr_reader, 400, y - 120, width=80, height=80)

    c.save()
    buffer.seek(0)
    return buffer

# ================= MAIN ROUTES =================

@app.route("/")
def index():
    """Landing page with auto redirect"""
    if "user_id" in session:
        return redirect(url_for("user.search"))
    return render_template("index.html")

@app.route("/contact")
def contact():
    """Contact page"""
    return render_template("contact.html")

@app.route("/login")
def login_redirect():
    """Redirect to auth login"""
    return redirect(url_for("auth.login"))

@app.route("/register")
def register_redirect():
    """Redirect to auth register"""
    return redirect(url_for("auth.register"))

# ================= AUTH BLUEPRINT =================

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """User registration with OTP"""
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Validation
        if not full_name or not username or not email or not password or not confirm_password:
            flash("All fields are required", "error")
            return redirect(url_for("auth.register"))

        if password != confirm_password:
            flash("Passwords do not match", "error")
            return redirect(url_for("auth.register"))

        if not is_strong_password(password):
            flash("Password must be at least 8 characters with uppercase, lowercase, and number", "error")
            return redirect(url_for("auth.register"))

        # Use provided username
        # Check if user exists
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE email = %s OR username = %s", (email, username))
        if cursor.fetchone():
            flash("Email or username already exists", "error")
            cursor.close()
            db.close()
            return redirect(url_for("auth.register"))

        # Generate OTP
        otp = generate_otp()
        otp_store[email] = otp

        # Send OTP email
        emailed = send_email(
            "BusHub - Email Verification",
            [email],
            f"Your OTP for registration is: {otp}\n\nValid for 10 minutes."
        )

        if emailed:
            flash("OTP sent to your email", "success")
        else:
            if app.config['OTP_DEV_FALLBACK']:
                flash("OTP email failed to send; using fallback OTP in development mode.", "warning")
            else:
                flash("Failed to send OTP email. Please check your mail settings and try again.", "error")
                cursor.close()
                db.close()
                return redirect(url_for("auth.register"))

        # Store temp data for OTP verification
        session["temp_user"] = {
            "name": full_name,
            "email": email,
            "username": username,
            "password": password,
            "otp": otp
        }
        cursor.close()
        db.close()
        return redirect(url_for("auth.verify_register_otp"))

    return render_template("register.html")

@auth_bp.route("/verify_register_otp", methods=["GET", "POST"])
def verify_register_otp():
    """Verify registration OTP"""
    if "temp_user" not in session:
        return redirect(url_for("auth.register"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()

        if not otp:
            flash("OTP is required", "error")
            return redirect(url_for("auth.verify_register_otp"))

        email = session["temp_user"]["email"]
        expected_otp = otp_store.get(email) or session["temp_user"].get("otp")

        if expected_otp and otp == expected_otp:
            user = session["temp_user"]

            db = get_db_connection()
            cursor = db.cursor()

            cursor.execute("""
                INSERT INTO users(name, email, username, password)
                VALUES(%s, %s, %s, %s)
            """, (user["name"], user["email"], user["username"],
                  generate_password_hash(user["password"])))

            db.commit()
            cursor.close()
            db.close()

            # Clear temp data and OTP
            del session["temp_user"]
            del otp_store[email]

            flash("Registration successful. You can now login.", "success")
            return redirect(url_for("auth.login"))
        else:
            flash("Invalid OTP", "error")

    return render_template("verify_register_otp.html", email=session.get("temp_user", {}).get("email", ""))

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """User login"""
    if request.method == "POST":
        username_or_email = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username_or_email or not password:
            flash("All fields are required", "error")
            return redirect(url_for("auth.login"))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM users WHERE username = %s OR email = %s",
            (username_or_email, username_or_email)
        )
        user = cursor.fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            flash(f"Welcome back, {user['name']}!", "success")
            cursor.close()
            db.close()
            return redirect(url_for("user.search"))

        cursor.close()
        db.close()
        flash("Invalid username (or email) or password", "error")

    return render_template("login.html")

@auth_bp.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    """Forgot password with OTP"""
    if request.method == "POST":
        email = request.form.get("email", "").strip()

        if not email:
            flash("Email is required", "error")
            return redirect(url_for("auth.forgot_password"))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user:
            flash("Email not found", "error")
            cursor.close()
            db.close()
            return redirect(url_for("auth.forgot_password"))

        # Generate OTP
        otp = generate_otp()
        otp_store[email] = otp

        # Send OTP
        emailed = send_email(
            "BusHub - Password Reset",
            [email],
            f"Your OTP for password reset is: {otp}\n\nValid for 10 minutes."
        )

        if emailed:
            flash("OTP sent to your email", "success")
        else:
            if app.config['OTP_DEV_FALLBACK']:
                flash("OTP email failed to send; using fallback OTP in development mode.", "warning")
            else:
                flash("Failed to send OTP email. Please check your mail settings and try again.", "error")
                cursor.close()
                db.close()
                return redirect(url_for("auth.forgot_password"))

        session["reset_email"] = email
        session["reset_otp"] = otp
        cursor.close()
        db.close()
        return redirect(url_for("auth.verify_forgot_otp"))

    return render_template("forgot_password.html")

@auth_bp.route("/verify_forgot_otp", methods=["GET", "POST"])
def verify_forgot_otp():
    """Verify forgot password OTP"""
    if "reset_email" not in session:
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        email = session["reset_email"]

        # Validation
        if not otp or not new_password or not confirm_password:
            flash("All fields are required", "error")
            return redirect(url_for("auth.verify_forgot_otp"))

        if new_password != confirm_password:
            flash("Passwords do not match", "error")
            return redirect(url_for("auth.verify_forgot_otp"))

        if not is_strong_password(new_password):
            flash("Password must be at least 8 characters with uppercase, lowercase, and number", "error")
            return redirect(url_for("auth.verify_forgot_otp"))

        expected_otp = otp_store.get(email) or session.get("reset_otp")

        if expected_otp and otp == expected_otp:
            hashed = generate_password_hash(new_password)

            db = get_db_connection()
            cursor = db.cursor()
            cursor.execute("UPDATE users SET password = %s WHERE email = %s", (hashed, email))
            db.commit()
            cursor.close()
            db.close()

            # Clear session
            if "reset_email" in session:
                del session["reset_email"]
            if email in otp_store:
                del otp_store[email]

            flash("Password updated successfully", "success")
            return redirect(url_for("auth.login"))
        else:
            flash("Invalid OTP", "error")

    return render_template("verify_otp.html", email=session.get("reset_email", ""))

@auth_bp.route("/logout")
def logout():
    """Logout user"""
    session.clear()
    flash("Logged out successfully", "success")
    return redirect(url_for("index"))

# ================= USER BLUEPRINT =================

@user_bp.before_request
def require_login():
    """Require login for user routes"""
    if "user_id" not in session and request.endpoint.startswith('user.'):
        return redirect(url_for("auth.login"))

@user_bp.route("/search", methods=["GET", "POST"])
def search():
    """Search buses with multiple stops support"""
    if request.method == "POST":
        source = request.form.get("from")
        destination = request.form.get("to")
        date = request.form.get("date")

        if not all([source, destination, date]):
            flash("Please fill all fields", "error")
            return redirect(url_for("user.search"))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Normalize search inputs
        source = source.strip()
        destination = destination.strip()
        date = date.strip()

        # Search for buses that have both stops in their route (case-insensitive, partial matching)
        cursor.execute("""
            SELECT DISTINCT b.*
            FROM buses b
            JOIN routes r1 ON b.id = r1.bus_id AND LOWER(r1.stop_name) LIKE LOWER(%s)
            JOIN routes r2 ON b.id = r2.bus_id AND LOWER(r2.stop_name) LIKE LOWER(%s)
            WHERE b.travel_date = %s
              AND r1.stop_order < r2.stop_order
            GROUP BY b.id
        """, (f"%{source}%", f"%{destination}%", date))

        buses = cursor.fetchall()

        # Fallback 1: exact source/destination on the same date (case-insensitive, partial)
        if not buses:
            cursor.execute("""
                SELECT * FROM buses
                WHERE LOWER(source) LIKE LOWER(%s)
                  AND LOWER(destination) LIKE LOWER(%s)
                  AND travel_date = %s
            """, (f"%{source}%", f"%{destination}%", date))
            buses = cursor.fetchall()

        # Fallback 2: route match on upcoming dates if still no results
        if not buses:
            cursor.execute("""
                SELECT DISTINCT b.*
                FROM buses b
                JOIN routes r1 ON b.id = r1.bus_id AND LOWER(r1.stop_name) LIKE LOWER(%s)
                JOIN routes r2 ON b.id = r2.bus_id AND LOWER(r2.stop_name) LIKE LOWER(%s)
                WHERE b.travel_date >= %s
                  AND r1.stop_order < r2.stop_order
                GROUP BY b.id
            """, (f"%{source}%", f"%{destination}%", date))
            buses = cursor.fetchall()

        # Fallback 3: direct route any date
        if not buses:
            cursor.execute("""
                SELECT * FROM buses
                WHERE LOWER(source) LIKE LOWER(%s)
                  AND LOWER(destination) LIKE LOWER(%s)
                ORDER BY travel_date ASC
                LIMIT 20
            """, (f"%{source}%", f"%{destination}%"))
            buses = cursor.fetchall()

        # Fallback 4: show all available buses (relaxed) for any route if still empty
        if not buses:
            cursor.execute("SELECT * FROM buses ORDER BY travel_date ASC LIMIT 30")
            buses = cursor.fetchall()
            if buses:
                flash("No buses found for your exact search. Showing available buses from all routes instead.", "info")

        for bus in buses:
            # Refresh per-bus route data from routes table (preferred)
            route_cursor = db.cursor(dictionary=True)
            route_cursor.execute("SELECT stop_name FROM routes WHERE bus_id = %s ORDER BY stop_order", (bus['id'],))
            route_rows = route_cursor.fetchall()
            route_cursor.close()

            if route_rows:
                bus['stops_list'] = [r['stop_name'] for r in route_rows]
            elif bus.get('stops'):
                try:
                    bus['stops_list'] = json.loads(bus['stops'])
                except Exception:
                    bus['stops_list'] = [bus['source'], bus['destination']]
            else:
                bus['stops_list'] = [bus['source'], bus['destination']]

            # Calculate available seats
            available_count, booked_seats = get_available_seats(bus['id'])
            bus['available_seats'] = available_count
            bus['booked_seats'] = booked_seats

            # Calculate duration
            bus['duration'] = calculate_duration(bus['departure_time'], bus['arrival_time'])

            # Parse amenities
            if bus['amenities']:
                try:
                    bus['amenities_list'] = json.loads(bus['amenities'])
                except Exception:
                    bus['amenities_list'] = []
            else:
                bus['amenities_list'] = []

        if not buses:
            flash("No buses found for the selected route and date", "info")

        return render_template("buslist.html", buses=buses, search_data={
            'from': source, 'to': destination, 'date': date
        })

    return render_template("search.html")

@user_bp.route("/bus/<int:bus_id>")
def bus_details(bus_id):
    """Show bus details and boarding/dropping points"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Get bus details
    cursor.execute("SELECT * FROM buses WHERE id = %s", (bus_id,))
    bus = cursor.fetchone()

    if not bus:
        flash("Bus not found", "error")
        return redirect(url_for("user.search"))
    
    # Get routes
    cursor.execute("""
        SELECT * FROM routes WHERE bus_id = %s ORDER BY stop_order
    """, (bus_id,))
    routes = cursor.fetchall()

    # Get seat details
    cursor.execute("SELECT * FROM seat_details WHERE bus_id = %s", (bus_id,))
    seats = cursor.fetchall()

    # Calculate available seats
    available_count, booked_seats = get_available_seats(bus_id)
    bus['available_seats'] = available_count
    bus['booked_seats'] = booked_seats
    bus['duration'] = calculate_duration(bus['departure_time'], bus['arrival_time'])

    if bus['amenities']:
        bus['amenities_list'] = json.loads(bus['amenities'])
    else:
        bus['amenities_list'] = []

    return render_template("bus_details.html", bus=bus, routes=routes, seats=seats)

@user_bp.route("/select_seats/<int:bus_id>", methods=["GET", "POST"])
def select_seats(bus_id):
    """Seat selection page"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Get bus details
    cursor.execute("SELECT * FROM buses WHERE id = %s", (bus_id,))
    bus = cursor.fetchone()

    if not bus:
        flash("Bus not found", "error")
        return redirect(url_for("user.search"))
    
     
    
    # Get routes for boarding/dropping
    cursor.execute("""
        SELECT * FROM routes WHERE bus_id = %s ORDER BY stop_order
    """, (bus_id,))
    routes = cursor.fetchall()

    # Get seat details
    cursor.execute("SELECT * FROM seat_details WHERE bus_id = %s ORDER BY seat_number", (bus_id,))
    seats = cursor.fetchall()

    # Get booked seats
    available_count, booked_seats = get_available_seats(bus_id)

    # Populate stops_list from routes table
    if routes:
        bus['stops_list'] = [r['stop_name'] for r in routes]
    elif bus.get('stops'):
        try:
            bus['stops_list'] = json.loads(bus['stops'])
        except Exception:
            bus['stops_list'] = [bus['source'], bus['destination']]
    else:
        bus['stops_list'] = [bus['source'], bus['destination']]

    # Calculate duration and available seats
    bus['duration'] = calculate_duration(bus['departure_time'], bus['arrival_time'])
    bus['available_seats'] = available_count

    if request.method == "POST":
        selected_seats_json = request.form.get("seats", "[]")
        boarding_point = request.form.get("boarding_point")
        dropping_point = request.form.get("dropping_point")
        
        # Parse JSON to get the list of selected seats
        try:
            selected_seats = json.loads(selected_seats_json)
        except (json.JSONDecodeError, ValueError):
            flash("Invalid seat selection. Please select seats again.", "error")
            return redirect(url_for("user.select_seats", bus_id=bus_id))

        if not selected_seats or not isinstance(selected_seats, list):
            flash("Please select at least one seat", "error")
            return redirect(url_for("user.select_seats", bus_id=bus_id))

        if not boarding_point or not dropping_point:
            flash("Please select boarding and dropping points", "error")
            return redirect(url_for("user.select_seats", bus_id=bus_id))

        # Store selection in session
        session['selected_seats'] = selected_seats
        session['boarding_point'] = boarding_point
        session['dropping_point'] = dropping_point
        session['bus_id'] = bus_id

        return redirect(url_for("user.payment"))

    return render_template("select_seats.html", bus=bus, routes=routes, seats=seats, booked_seats=booked_seats)

@user_bp.route("/payment", methods=["GET", "POST"])
def payment():
    """Payment page with proper error handling"""
    # Validate session data on GET and POST
    if 'selected_seats' not in session:
        flash("Invalid session. Please search and select seats again.", "error")
        return redirect(url_for("user.search"))
    
    # Validate all required session variables
    required_session_keys = ['bus_id', 'user_id', 'user_name', 'boarding_point', 'dropping_point', 'selected_seats']
    for key in required_session_keys:
        if key not in session:
            flash(f"Session error: Missing {key}. Please start over.", "error")
            return redirect(url_for("user.search"))
    
    db = None
    cursor = None
    
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        bus_id = session['bus_id']
        
        # Get bus details
        cursor.execute("SELECT * FROM buses WHERE id = %s", (bus_id,))
        bus = cursor.fetchone()
        
        # Validate bus exists
        if not bus:
            flash("Bus not found. Please search again.", "error")
            return redirect(url_for("user.search"))
        
        # Get seat details for price calculation
        selected_seats = session['selected_seats']
        
        if not selected_seats:
            flash("No seats selected. Please select seats again.", "error")
            return redirect(url_for("user.search"))
        
        cursor.execute("""
            SELECT seat_number, price_modifier FROM seat_details
            WHERE bus_id = %s AND seat_number IN ({})
        """.format(','.join(['%s'] * len(selected_seats))), [bus_id] + selected_seats)
        
        seat_details = cursor.fetchall()
        
        # Validate we got seat details for all selected seats
        if len(seat_details) != len(selected_seats):
            flash("Invalid seats selected. Please select seats again.", "error")
            return redirect(url_for("user.select_seats", bus_id=bus_id))
        
        # Check if any selected seats are already booked
        cursor.execute("""
            SELECT seats FROM bookings
            WHERE bus_id = %s AND status = 'confirmed'
        """, (bus_id,))
        
        bookings = cursor.fetchall()
        booked_seats = set()
        for booking in bookings:
            try:
                seats_list = json.loads(booking['seats'])
                for seat in seats_list:
                    booked_seats.add(seat['number'] if isinstance(seat, dict) else seat)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        
        # Check for conflicts
        selected_seats_set = set(selected_seats)
        conflict_seats = selected_seats_set & booked_seats
        if conflict_seats:
            flash(f"Sorry, seat{'s' if len(conflict_seats) > 1 else ''} {', '.join(sorted(conflict_seats))} {'are' if len(conflict_seats) > 1 else 'is'} no longer available. Please select different seats.", "error")
            return redirect(url_for("user.select_seats", bus_id=bus_id))
        
        # Calculate total amount
        total_amount = 0
        for seat in seat_details:
            total_amount += bus['price'] * seat['price_modifier']
        
        # Validate total amount is valid
        if total_amount <= 0:
            flash("Invalid booking amount. Please try again.", "error")
            return redirect(url_for("user.search"))
        
        if request.method == "POST":
            # Validate payment method
            payment_method = request.form.get("payment_method", "").strip()
            valid_payment_methods = ["UPI", "Credit/Debit Card", "Net Banking"]
            
            if not payment_method:
                flash("Please select a payment method.", "error")
                return render_template("payment.html", bus=bus, selected_seats=selected_seats,
                                     boarding_point=session['boarding_point'],
                                     dropping_point=session['dropping_point'],
                                     total_amount=total_amount)
            
            if payment_method not in valid_payment_methods:
                flash("Invalid payment method selected.", "error")
                return render_template("payment.html", bus=bus, selected_seats=selected_seats,
                                     boarding_point=session['boarding_point'],
                                     dropping_point=session['dropping_point'],
                                     total_amount=total_amount)
            
            # Simulate payment success
            import time
            time.sleep(1)  # Simulate processing
            
            # Create booking
            seats_data = []
            for seat in seat_details:
                seats_data.append({
                    'number': seat['seat_number'],
                    'price': bus['price'] * seat['price_modifier']
                })
            
            try:
                # Insert booking
                cursor.execute("""
                    INSERT INTO bookings(user_id, bus_id, seats, passenger_name, boarding_point, dropping_point, total_amount, payment_method)
                    VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    session['user_id'],
                    bus_id,
                    json.dumps(seats_data),
                    session['user_name'],
                    session['boarding_point'],
                    session['dropping_point'],
                    total_amount,
                    payment_method
                ))
                
                db.commit()
                booking_id = cursor.lastrowid
                
                # Only clear session after successful commit
                session.pop('selected_seats', None)
                session.pop('boarding_point', None)
                session.pop('dropping_point', None)
                session.pop('bus_id', None)
                
                flash("Payment successful! Your booking is confirmed.", "success")
                return redirect(url_for("user.booking_confirmation", booking_id=booking_id))
                
            except mysql.connector.Error as db_error:
                db.rollback()
                print(f"Database error during booking creation: {db_error}")
                flash("Failed to create booking. Please try again.", "error")
                return render_template("payment.html", bus=bus, selected_seats=selected_seats,
                                     boarding_point=session['boarding_point'],
                                     dropping_point=session['dropping_point'],
                                     total_amount=total_amount)
        
        # GET request - Show payment form
        return render_template("payment.html", bus=bus, selected_seats=selected_seats,
                             boarding_point=session['boarding_point'],
                             dropping_point=session['dropping_point'],
                             total_amount=total_amount)
    
    except mysql.connector.Error as db_error:
        print(f"Database error in payment route: {db_error}")
        flash("Database error occurred. Please try again.", "error")
        return redirect(url_for("user.search"))
    
    except KeyError as key_error:
        print(f"Session key error: {key_error}")
        flash("Session error occurred. Please start over.", "error")
        return redirect(url_for("user.search"))
    
    except Exception as e:
        print(f"Unexpected error in payment route: {e}")
        flash("An unexpected error occurred. Please try again.", "error")
        return redirect(url_for("user.search"))
    
    finally:
        # Always close database connections
        if cursor:
            cursor.close()
        if db:
            db.close()

@user_bp.route("/booking_confirmation/<int:booking_id>")
def booking_confirmation(booking_id):
    """Booking confirmation page"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.travel_date, buses.departure_time, buses.arrival_time,
               buses.bus_type, buses.operator
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s AND b.user_id = %s
    """, (booking_id, session['user_id']))

    booking = cursor.fetchone()

    if not booking:
        flash("Booking not found", "error")
        return redirect(url_for("user.booking_history"))

    # Parse seats
    booking['seats_list'] = json.loads(booking['seats'])

    return render_template("confirmation.html", booking=booking)

@user_bp.route("/booking_history")
def booking_history():
    """User booking history"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.travel_date, buses.departure_time, buses.arrival_time
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.user_id = %s AND b.status = 'confirmed'
        ORDER BY b.booking_date DESC
    """, (session['user_id'],))

    bookings = cursor.fetchall()

    for booking in bookings:
        booking['seats_list'] = json.loads(booking['seats'])

    return render_template("booking_history.html", bookings=bookings)

@user_bp.route("/cancel_booking/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    """Cancel booking"""
    db = get_db_connection()
    cursor = db.cursor()

    # Check if booking belongs to user and is not already cancelled
    cursor.execute("""
        SELECT id FROM bookings
        WHERE id = %s AND user_id = %s AND status = 'confirmed'
    """, (booking_id, session['user_id']))

    if cursor.fetchone():
        cursor.execute("""
            UPDATE bookings SET status = 'cancelled'
            WHERE id = %s
        """, (booking_id,))
        db.commit()
        flash("Booking cancelled successfully", "success")
    else:
        flash("Booking not found or already cancelled", "error")

    return redirect(url_for("user.booking_history"))

@user_bp.route("/download_ticket/<int:booking_id>")
def download_ticket(booking_id):
    """Download PDF ticket"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.travel_date, buses.departure_time, buses.arrival_time
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s AND b.user_id = %s
    """, (booking_id, session['user_id']))

    booking = cursor.fetchone()

    if not booking:
        flash("Booking not found", "error")
        return redirect(url_for("user.booking_history"))

    # Generate PDF
    pdf_buffer = generate_ticket_pdf(booking)

    from flask import send_file
    pdf_buffer.seek(0)
    return send_file(pdf_buffer, as_attachment=True, download_name=f"ticket_{booking_id}.pdf", mimetype='application/pdf')

@user_bp.route("/api/bus_routes/<int:bus_id>")
def get_bus_routes_api(bus_id):
    """API endpoint to fetch bus routes"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Get bus details
    cursor.execute("SELECT * FROM buses WHERE id = %s", (bus_id,))
    bus = cursor.fetchone()

    if not bus:
        return jsonify({'error': 'Bus not found'}), 404

    # Get all routes
    cursor.execute("""
        SELECT * FROM routes WHERE bus_id = %s ORDER BY stop_order
    """, (bus_id,))
    routes = cursor.fetchall()

    cursor.close()
    db.close()

    return jsonify({
        'bus_id': bus['id'],
        'bus_name': bus['bus_name'],
        'source': bus['source'],
        'destination': bus['destination'],
        'routes': routes if routes else []
    })

# ================= ADMIN BLUEPRINT =================

@admin_bp.before_request
def require_admin():
    """Require admin login"""
    if "admin_id" not in session and request.endpoint.startswith('admin.'):
        return redirect(url_for("admin.login"))

@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """Admin login"""
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute("SELECT * FROM admin_users WHERE username = %s", (username,))
        admin = cursor.fetchone()

        if admin and check_password_hash(admin["password"], password):
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            flash("Admin login successful", "success")
            return redirect(url_for("admin.dashboard"))
        else:
            flash("Invalid credentials", "error")

    return render_template("admin_login.html")

@admin_bp.route("/logout")
def logout():
    """Admin logout"""
    session.clear()
    return redirect(url_for("admin.login"))

@admin_bp.route("/dashboard")
def dashboard():
    """Admin dashboard"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Stats
    cursor.execute("SELECT COUNT(*) as total_users FROM users")
    total_users = cursor.fetchone()['total_users']

    cursor.execute("SELECT COUNT(*) as total_buses FROM buses")
    total_buses = cursor.fetchone()['total_buses']

    cursor.execute("SELECT COUNT(*) as total_bookings FROM bookings WHERE status = 'confirmed'")
    total_bookings = cursor.fetchone()['total_bookings']

    cursor.execute("SELECT SUM(total_amount) as total_revenue FROM bookings WHERE status = 'confirmed'")
    revenue = cursor.fetchone()['total_revenue'] or 0

    # Recent bookings
    cursor.execute("""
        SELECT b.id, b.passenger_name, b.total_amount, b.booking_date,
               buses.bus_name, buses.source, buses.destination, buses.travel_date
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.status = 'confirmed'
        ORDER BY b.booking_date DESC LIMIT 10
    """)
    recent_bookings = cursor.fetchall()

    return render_template("admin_dashboard.html",
                         stats={'users': total_users, 'buses': total_buses,
                               'bookings': total_bookings, 'revenue': revenue},
                         recent_bookings=recent_bookings)

@admin_bp.route("/buses", methods=["GET", "POST"])
def manage_buses():
    """Manage buses"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    if request.method == "POST":
        # Add new bus
        bus_name = request.form["bus_name"]
        source = request.form["source"]
        destination = request.form["destination"]
        stops = request.form.getlist("stops")
        departure_time = request.form["departure_time"]
        arrival_time = request.form["arrival_time"]
        travel_date = request.form["travel_date"]
        price = request.form["price"]
        seats_total = request.form["seats_total"]
        bus_type = request.form.get("bus_type", "").strip()
        amenities = request.form.getlist("amenities")
        
        # Validate bus_type
        valid_bus_types = ['AC', 'Non-AC', 'Sleeper', 'Seater', 'Double Decker']
        if not bus_type or bus_type not in valid_bus_types:
            flash(f"Invalid bus type. Must be one of: {', '.join(valid_bus_types)}", "error")
            return redirect(url_for("admin.admin_add_bus"))

        cursor.execute("""
            INSERT INTO buses(bus_name, source, destination, stops, departure_time, arrival_time,
                            travel_date, price, seats_total, bus_type, amenities)
            VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (bus_name, source, destination, json.dumps(stops), departure_time, arrival_time,
              travel_date, price, seats_total, bus_type, json.dumps(amenities)))

        bus_id = cursor.lastrowid

        # Add routes
        for i, stop in enumerate(stops):
            cursor.execute("""
                INSERT INTO routes(bus_id, stop_name, stop_order)
                VALUES(%s, %s, %s)
            """, (bus_id, stop, i+1))

        db.commit()
        flash("Bus added successfully", "success")
        return redirect(url_for("admin.manage_buses"))

    # Get all buses
    cursor.execute("SELECT * FROM buses ORDER BY travel_date DESC")
    buses = cursor.fetchall()

    for bus in buses:
        if bus['stops']:
            bus['stops_list'] = json.loads(bus['stops'])
        if bus['amenities']:
            bus['amenities_list'] = json.loads(bus['amenities'])

    return render_template("admin_add_bus.html", buses=buses)

@admin_bp.route("/bus/<int:bus_id>/delete", methods=["POST"])
def delete_bus(bus_id):
    """Delete bus"""
    db = get_db_connection()
    cursor = db.cursor()

    # Check if bus has bookings
    cursor.execute("SELECT COUNT(*) as count FROM bookings WHERE bus_id = %s AND status = 'confirmed'", (bus_id,))
    if cursor.fetchone()['count'] > 0:
        flash("Cannot delete bus with existing bookings", "error")
        return redirect(url_for("admin.manage_buses"))

    cursor.execute("DELETE FROM buses WHERE id = %s", (bus_id,))
    db.commit()

    flash("Bus deleted successfully", "success")
    return redirect(url_for("admin.manage_buses"))

@admin_bp.route("/bookings")
def view_bookings():
    """View all bookings"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination, buses.travel_date,
               users.name as user_name, users.email
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        JOIN users ON b.user_id = users.id
        ORDER BY b.booking_date DESC
    """)

    bookings = cursor.fetchall()

    for booking in bookings:
        booking['seats_list'] = json.loads(booking['seats'])

    return render_template("admin_bookings.html", bookings=bookings)

# ================= REGISTER BLUEPRINTS =================
app.register_blueprint(auth_bp)
app.register_blueprint(user_bp)
app.register_blueprint(admin_bp)

# ================= ERROR HANDLERS =================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

# ================= RUN APP =================
if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)