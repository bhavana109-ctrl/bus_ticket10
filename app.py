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
app.config['MAIL_USE_SSL'] = False

#
# Flask-Mail expects MAIL_USERNAME and MAIL_PASSWORD to be strings.
# (Previously these were mistakenly set as tuples, which breaks SMTP auth
# and prevents OTP emails from being sent.)
#
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'mbhavana109@gmail.com').strip()
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'rmrq apsv jpym jjie').strip()
app.config['MAIL_DEBUG'] = True
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']
#
# Environment variables arrive as strings; normalize to real bool.
#
app.config['OTP_DEV_FALLBACK'] = str(os.environ.get('OTP_DEV_FALLBACK', 'false')).strip().lower() in (
    '1', 'true', 'yes', 'y', 'on'
)

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


def ensure_date(value):
    """Normalize date-like values for templates and email rendering."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, 'strftime'):
        # Already date object or datetime
        return value
    if isinstance(value, str):
        # Try common string formats
        for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(value, fmt).date()
            except Exception:
                pass
        try:
            return datetime.fromisoformat(value).date()
        except Exception:
            return value
    return value


def ensure_time(value):
    """Normalize time-like values for templates and email rendering."""
    if value is None:
        return None
    if hasattr(value, 'strftime'):
        return value
    if isinstance(value, str):
        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                return datetime.strptime(value, fmt).time()
            except Exception:
                pass
        return value
    if isinstance(value, timedelta):
        return (datetime.min + value).time()
    return value


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
    """Ensure buses table has columns used by the app.

    This app is expected to auto-migrate a few columns when the existing
    MySQL schema is older/incomplete (for example, missing `source` /
    `destination`).
    """
    db = get_db_connection()
    cursor = db.cursor()

    required_or_used_columns = {
        # Used by search and routes generation
        'source': "VARCHAR(100) NULL",
        'destination': "VARCHAR(100) NULL",
        'departure_time': "TIME NULL",
        'arrival_time': "TIME NULL",
        'travel_date': "DATE NULL",
        'price': "DECIMAL(10,2) DEFAULT 0",
        # Used by seat generation + booking price calculations
        'seats_total': "INT DEFAULT 40",
        # Optional JSON columns / metadata used by the app
        'stops': "JSON NULL",
        'amenities': "JSON NULL",
        'bus_type': "VARCHAR(20) DEFAULT 'Non-AC'",
        'rating': "DECIMAL(3,2) DEFAULT 4.5",
        'operator': "VARCHAR(100) DEFAULT 'BusHub'",
    }

    # New entertainment options for passengers (kids toys, books, games)
    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'entertainment_items'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE bookings ADD COLUMN entertainment_items JSON NULL")

    for col, definition in required_or_used_columns.items():
        cursor.execute(f"SHOW COLUMNS FROM buses LIKE '{col}'")
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE buses ADD COLUMN {col} {definition}")

    # Ensure bookings table has status column
    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'status'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE bookings ADD COLUMN status ENUM('confirmed', 'cancelled') DEFAULT 'confirmed'")

    # booking_date is used for ORDER BY and admin views (older DBs may lack it)
    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'booking_date'")
    if not cursor.fetchone():
        cursor.execute(
            "ALTER TABLE bookings ADD COLUMN booking_date TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP"
        )

    # Refund-related columns used by cancellation flow
    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'refund_amount'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE bookings ADD COLUMN refund_amount DECIMAL(10,2) NULL")

    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'cancelled_at'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE bookings ADD COLUMN cancelled_at DATETIME NULL")

    # Offer data for bookings
    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'seasonal_offer'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE bookings ADD COLUMN seasonal_offer TINYINT(1) DEFAULT 0")

    cursor.execute("SHOW COLUMNS FROM bookings LIKE 'first_travel_offer'")
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE bookings ADD COLUMN first_travel_offer TINYINT(1) DEFAULT 0")

    db.commit()
    cursor.close()
    db.close()


def ensure_bus_locations_table():
    """Ensure bus_locations table exists for tracking bus locations"""
    db = get_db_connection()
    cursor = db.cursor()

    # Create bus_locations table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bus_locations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            bus_id INT NOT NULL,
            latitude DECIMAL(10,8) NULL,
            longitude DECIMAL(11,8) NULL,
            current_stop VARCHAR(100) NULL,
            next_stop VARCHAR(100) NULL,
            estimated_arrival TIME NULL,
            status ENUM('not_started', 'in_transit', 'arrived', 'delayed') DEFAULT 'not_started',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (bus_id) REFERENCES buses(id) ON DELETE CASCADE,
            INDEX idx_bus_id (bus_id),
            INDEX idx_last_updated (last_updated)
        )
    """)

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

    # Backfill buses.source / buses.destination from routes (first/last stop).
    # This helps search work even if the buses table schema was created
    # without those columns earlier.
    try:
        cursor2 = db.cursor()
        cursor2.execute(
            """
            UPDATE buses b
            SET
              b.source = CASE
                  WHEN b.source IS NULL THEN (
                      SELECT r.stop_name
                      FROM routes r
                      WHERE r.bus_id = b.id
                      ORDER BY r.stop_order ASC
                      LIMIT 1
                  )
                  ELSE b.source
              END,
              b.destination = CASE
                  WHEN b.destination IS NULL THEN (
                      SELECT r.stop_name
                      FROM routes r
                      WHERE r.bus_id = b.id
                      ORDER BY r.stop_order DESC
                      LIMIT 1
                  )
                  ELSE b.destination
              END
            """
        )
        db.commit()
        cursor2.close()
    except Exception as e:
        # Don't block app startup if backfilling fails for any reason.
        print(f"Backfill source/destination warning: {e}")

    cursor.close()
    db.close()


def ensure_seat_details_for_buses():
    """Ensure seat_details table is populated for buses"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Create seat_details table if it doesn't exist (older DBs may be missing it)
    cursor.execute("SHOW TABLES LIKE 'seat_details'")
    if not cursor.fetchone():
        cursor2 = db.cursor()
        cursor2.execute("""
            CREATE TABLE IF NOT EXISTS seat_details (
                id INT AUTO_INCREMENT PRIMARY KEY,
                bus_id INT NOT NULL,
                seat_number VARCHAR(10) NOT NULL,
                seat_type ENUM('Window', 'Aisle', 'Sleeper', 'Seater') DEFAULT 'Seater',
                deck ENUM('Lower', 'Upper') DEFAULT 'Lower',
                gender_restriction ENUM('Male', 'Female', 'None') DEFAULT 'None',
                price_modifier DECIMAL(3,2) DEFAULT 1.0,
                FOREIGN KEY (bus_id) REFERENCES buses(id) ON DELETE CASCADE,
                UNIQUE KEY unique_bus_seat (bus_id, seat_number)
            )
        """)
        db.commit()
        cursor2.close()

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

def effective_travel_date_from_session(bus_row):
    """Travel date shown/stored for the trip: user's search date when present, else the bus row."""
    raw = session.get('search_travel_date')
    if raw is None:
        return bus_row.get('travel_date')
    s = str(raw).strip()
    return s if s else bus_row.get('travel_date')


def initialize_db():
    """Initialize missing DB-supported data"""
    global db_initialized
    try:
        # Always keep schema additions in sync (prevents crashes on older DBs).
        ensure_bus_schema()
        ensure_bus_locations_table()
        if db_initialized:
            return

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
    # Used by callers to surface a more helpful flash message.
    send_email.last_error = None
    try:
        msg = Message(subject, sender=app.config['MAIL_USERNAME'], recipients=recipients)
        msg.body = body
        if html:
            msg.html = html
        mail.send(msg)
        print("Email sent successfully ✅")  # debug
        return True
    except Exception as e:
        # Keep the exact SMTP error for the UI (helps fix Gmail/app-password issues quickly).
        send_email.last_error = str(e)
        print(f"Email error ({type(e).__name__}): {e}")
        return False

def format_time_value(time_value):
    """Return a display-friendly HH:MM string."""
    if not time_value:
        return "N/A"
    if hasattr(time_value, "strftime"):
        try:
            return time_value.strftime("%H:%M")
        except Exception:
            pass
    if hasattr(time_value, "seconds"):
        hours = time_value.seconds // 3600
        minutes = (time_value.seconds % 3600) // 60
        return f"{hours:02d}:{minutes:02d}"
    return str(time_value)

def send_booking_confirmation_email(booking):
    """Send booking confirmation email immediately after a successful booking."""
    recipient = (booking.get("email") or "").strip()
    if not recipient:
        return False

    seats = booking.get("seats_list") or json.loads(booking["seats"])
    emergency_services = booking.get("emergency_services") or []
    entertainment_items = booking.get("entertainment_items") or []
    offers_applied = booking.get("offers_applied") or []
    if isinstance(entertainment_items, str):
        try:
            entertainment_items = json.loads(entertainment_items)
        except Exception:
            entertainment_items = [entertainment_items] if entertainment_items else []
    if isinstance(offers_applied, str):
        try:
            offers_applied = json.loads(offers_applied)
        except Exception:
            offers_applied = [offers_applied] if offers_applied else []

    travel_date = ensure_date(booking.get("travel_date"))
    subject = f"BusHub - Booking Confirmed - {booking['booking_id']}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
            <h1>Booking Confirmed</h1>
            <p>Your bus ticket has been successfully booked.</p>
        </div>
        <div style="background: white; padding: 20px; border: 1px solid #ddd; border-radius: 0 0 10px 10px;">
            <h2 style="color: #333;">Booking Details</h2>
            <p><strong>Booking ID:</strong> {booking['booking_id']}</p>
            <p><strong>Passenger:</strong> {booking['passenger_name']}</p>
            <p><strong>Route:</strong> {booking['source']} to {booking['destination']}</p>
            <p><strong>Travel Date:</strong> {travel_date.strftime('%Y-%m-%d') if hasattr(travel_date, 'strftime') else (travel_date or 'N/A')}</p>
            <p><strong>Departure:</strong> {format_time_value(ensure_time(booking.get('departure_time')))}</p>
            <p><strong>Bus:</strong> {booking['bus_name']} ({booking['bus_type']})</p>
            <p><strong>Seats:</strong> {', '.join([seat['number'] for seat in seats])}</p>
            <p><strong>Base Fare:</strong> Rs. {float(booking.get('base_total_amount', booking['total_amount'])):.2f}</p>
            <p><strong>Total Discount:</strong> Rs. {float(booking.get('total_discount', 0) or 0):.2f}</p>
            <p><strong>Offers Applied:</strong> {', '.join(offers_applied) if offers_applied else 'None'}</p>
            <p><strong>Total Amount:</strong> Rs. {float(booking['total_amount']):.2f}</p>
            <p><strong>Emergency Services Requested:</strong> {', '.join(emergency_services) if emergency_services else 'None'}</p>
            <p><strong>Entertainment Items Requested:</strong> {', '.join(entertainment_items) if entertainment_items else 'None'}</p>
            <p style="color: #666; font-size: 12px;">Thank you for choosing BusHub. This is an automated email.</p>
        </div>
    </body>
    </html>
    """
    return send_email(subject, [recipient], f"Booking confirmed for {booking['booking_id']}", html_body)

def send_cancellation_confirmation_email(booking, refund_amount, refund_percentage=None, refund_policy=None):
    """Send booking cancellation email immediately after a successful cancellation."""
    recipient = (booking.get("email") or "").strip()
    if not recipient:
        return False

    seats = booking.get("seats_list") or json.loads(booking.get("seats", "[]"))
    travel_date = ensure_date(booking.get("travel_date") or booking.get("bus_travel_date"))
    departure_time = format_time_value(ensure_time(booking.get("departure_time")))
    arrival_time = format_time_value(ensure_time(booking.get("arrival_time")))
    entertainment_items = booking.get("entertainment_items") or []
    if isinstance(entertainment_items, str):
        try:
            entertainment_items = json.loads(entertainment_items)
        except Exception:
            entertainment_items = [entertainment_items] if entertainment_items else []

    subject = f"BusHub - Booking Cancelled - {booking['booking_id']}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #dc3545 0%, #c82333 100%); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
            <h1>Booking Cancelled</h1>
            <p>Your bus booking has been cancelled successfully.</p>
        </div>
        <div style="background: white; padding: 20px; border: 1px solid #ddd; border-radius: 0 0 10px 10px;">
            <h2 style="color: #333;">Cancellation Details</h2>
            <p><strong>Booking ID:</strong> {booking['booking_id']}</p>
            <p><strong>Passenger:</strong> {booking['passenger_name']}</p>
            <p><strong>Route:</strong> {booking['source']} to {booking['destination']}</p>
            <p><strong>Bus:</strong> {booking.get('bus_name', 'N/A')} ({booking.get('bus_type', 'N/A')})</p>
            <p><strong>Travel Date:</strong> {travel_date.strftime('%Y-%m-%d') if hasattr(travel_date, 'strftime') else (travel_date or 'N/A')}</p>
            <p><strong>Departure:</strong> {departure_time}</p>
            <p><strong>Arrival:</strong> {arrival_time}</p>
            <p><strong>Seats:</strong> {', '.join([seat['number'] if isinstance(seat, dict) else str(seat) for seat in seats])}</p>
            <p><strong>Original Amount:</strong> Rs. {float(booking['total_amount']):.2f}</p>
            <p><strong>Refund Amount:</strong> Rs. {float(refund_amount):.2f}</p>
            <p><strong>Refund Percentage:</strong> {refund_percentage if refund_percentage is not None else 'N/A'}%</p>
            <p><strong>Policy:</strong> {refund_policy or '90% refund if cancelled before 24 hours, 50% refund if cancelled 2-24 hours before departure.'}</p>
            <p><strong>Refund Timeline:</strong> 5-7 business days</p>
            <p><strong>Entertainment Items Requested:</strong> {', '.join(entertainment_items) if entertainment_items else 'None'}</p>
            <p style="color: #666; font-size: 12px;">This is an automated email. Please do not reply.</p>
        </div>
    </body>
    </html>
    """
    return send_email(subject, [recipient], f"Booking cancelled for {booking['booking_id']}", html_body)

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
        email = request.form.get("email", "").strip().lower()
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
        cursor.execute(
            "SELECT id FROM users WHERE LOWER(email) = LOWER(%s) OR username = %s",
            (email, username)
        )
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
                err = getattr(send_email, "last_error", None) or "Unknown mail error"
                flash(f"Failed to send OTP email: {err}", "error")
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

@app.route("/resend_register_otp", methods=["POST"])
def resend_register_otp_api():
    """Resend OTP for registration verification.

    Templates call this endpoint without the `/auth` prefix, so we register it
    on the main app (not the auth blueprint).
    """
    if "temp_user" not in session:
        return jsonify({"success": False, "error": "Session expired"}), 400

    user = session["temp_user"]
    email = (user.get("email") or "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "Missing email"}), 400

    otp = generate_otp()
    otp_store[email] = otp
    user["otp"] = otp

    emailed = send_email(
        "BusHub - Email Verification",
        [email],
        f"Your OTP for registration is: {otp}\n\nValid for 10 minutes."
    )

    if emailed:
        return jsonify({"success": True})

    if app.config['OTP_DEV_FALLBACK']:
        # Keep UX moving during local development if SMTP is misconfigured.
        return jsonify({"success": True})

    return jsonify({"success": False, "error": "Failed to send OTP email"}), 500

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """User login"""
    if request.method == "POST":
        username_or_email = request.form.get("username", "").strip()
        if "@" in username_or_email:
            username_or_email = username_or_email.lower()
        password = request.form.get("password", "")

        if not username_or_email or not password:
            flash("All fields are required", "error")
            return redirect(url_for("auth.login"))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM users WHERE username = %s OR LOWER(email) = LOWER(%s)",
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
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Email is required", "error")
            return redirect(url_for("auth.forgot_password"))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(%s)", (email,))
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
                err = getattr(send_email, "last_error", None) or "Unknown mail error"
                flash(f"Failed to send OTP email: {err}", "error")
                cursor.close()
                db.close()
                return redirect(url_for("auth.forgot_password"))

        session["reset_email"] = email
        session["reset_otp"] = otp
        cursor.close()
        db.close()
        return redirect(url_for("auth.verify_forgot_otp"))

    return render_template("forgot_password.html")

@app.route("/resend_forgot_otp", methods=["POST"])
def resend_forgot_otp_api():
    """Resend OTP for forgot-password flow.

    Templates call this endpoint without the `/auth` prefix, so we register it
    on the main app (not the auth blueprint).
    """
    if "reset_email" not in session:
        return jsonify({"success": False, "error": "Session expired"}), 400

    email = (session.get("reset_email") or "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "Missing email"}), 400

    otp = generate_otp()
    otp_store[email] = otp
    session["reset_otp"] = otp

    emailed = send_email(
        "BusHub - Password Reset",
        [email],
        f"Your OTP for password reset is: {otp}\n\nValid for 10 minutes."
    )

    if emailed:
        return jsonify({"success": True})

    if app.config['OTP_DEV_FALLBACK']:
        return jsonify({"success": True})

    return jsonify({"success": False, "error": "Failed to send OTP email"}), 500

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
            cursor.execute(
                "UPDATE users SET password = %s WHERE LOWER(email) = LOWER(%s)",
                (hashed, email)
            )
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

def get_popular_routes():
    """Get popular routes from the database"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    
    # Get unique source-destination pairs from buses table
    cursor.execute("""
        SELECT DISTINCT source, destination, COUNT(*) as bus_count
        FROM buses 
        GROUP BY source, destination
        ORDER BY bus_count DESC
        LIMIT 10
    """)
    
    routes = cursor.fetchall()
    cursor.close()
    return routes

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
        travel_date = request.form.get("date")

        if not all([source, destination, travel_date]):
            flash("Please fill all fields", "error")
            return redirect(url_for("user.search"))

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        # Normalize search inputs
        source = source.strip()
        destination = destination.strip()
        travel_date = travel_date.strip()
        # Persist the date the user searched for so booking details match search (bus row may differ in fallback flows).
        session['search_travel_date'] = travel_date

        # Search for buses that have both stops in their route (exact matching)
        cursor.execute("""
            SELECT DISTINCT b.*
            FROM buses b
            JOIN routes r1 ON b.id = r1.bus_id AND LOWER(r1.stop_name) = LOWER(%s)
            JOIN routes r2 ON b.id = r2.bus_id AND LOWER(r2.stop_name) = LOWER(%s)
            WHERE b.travel_date = %s
              AND r1.stop_order < r2.stop_order
            GROUP BY b.id
        """, (source, destination, travel_date))

        buses = cursor.fetchall()

        # Fallback 1: exact source/destination on the same date
        if not buses:
            cursor.execute("""
                SELECT * FROM buses
                WHERE LOWER(source) = LOWER(%s)
                  AND LOWER(destination) = LOWER(%s)
                  AND travel_date = %s
            """, (source, destination, travel_date))
            buses = cursor.fetchall()

        # Fallback 2: route match on upcoming dates if still no results
        if not buses:
            cursor.execute("""
                SELECT DISTINCT b.*
                FROM buses b
                JOIN routes r1 ON b.id = r1.bus_id AND LOWER(r1.stop_name) = LOWER(%s)
                JOIN routes r2 ON b.id = r2.bus_id AND LOWER(r2.stop_name) = LOWER(%s)
                WHERE b.travel_date >= %s
                  AND r1.stop_order < r2.stop_order
                GROUP BY b.id
            """, (source, destination, travel_date))
            buses = cursor.fetchall()

        # Fallback 3: direct route any date
        if not buses:
            cursor.execute("""
                SELECT * FROM buses
                WHERE LOWER(source) = LOWER(%s)
                  AND LOWER(destination) = LOWER(%s)
                ORDER BY travel_date ASC
                LIMIT 20
            """, (source, destination))
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
            'from': source, 'to': destination, 'date': travel_date
        })

    return render_template("search.html", popular_routes=get_popular_routes())

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

        # Store selection and emergency service requests in session
        emergency_services_raw = request.form.get("emergency_services_str", "[]")
        try:
            emergency_services = json.loads(emergency_services_raw)
            if not isinstance(emergency_services, list):
                emergency_services = []
        except Exception:
            emergency_services = request.form.getlist("emergency_services")

        entertainment_items_raw = request.form.get("entertainment_items_str", "[]")
        try:
            entertainment_items = json.loads(entertainment_items_raw)
            if not isinstance(entertainment_items, list):
                entertainment_items = []
        except Exception:
            entertainment_items = request.form.getlist("entertainment_items")

        # Fallback when hidden JSON field is absent/empty but checkbox values are present.
        if not emergency_services:
            emergency_services = request.form.getlist("emergency_services")
        if not entertainment_items:
            entertainment_items = request.form.getlist("entertainment_items")

        session['selected_seats'] = selected_seats
        session['boarding_point'] = boarding_point
        session['dropping_point'] = dropping_point
        session['bus_id'] = bus_id
        session['emergency_services'] = emergency_services
        session['entertainment_items'] = entertainment_items

        return redirect(url_for("user.payment"))

    display_travel_date = effective_travel_date_from_session(bus)
    return render_template(
        "select_seats.html",
        bus=bus,
        routes=routes,
        seats=seats,
        booked_seats=booked_seats,
        display_travel_date=display_travel_date,
    )

@user_bp.route("/payment", methods=["GET", "POST"])
def payment():
    """Payment page with proper error handling"""
    if 'selected_seats' not in session:
        flash("Invalid session. Please search and select seats again.", "error")
        return redirect(url_for("user.search"))

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

        cursor.execute("SELECT * FROM buses WHERE id = %s", (bus_id,))
        bus = cursor.fetchone()
        if not bus:
            flash("Bus not found. Please search again.", "error")
            return redirect(url_for("user.search"))

        selected_seats = session['selected_seats']
        if not selected_seats:
            flash("No seats selected. Please select seats again.", "error")
            return redirect(url_for("user.search"))

        cursor.execute("""
            SELECT seat_number, price_modifier FROM seat_details
            WHERE bus_id = %s AND seat_number IN ({})
        """.format(','.join(['%s'] * len(selected_seats))), [bus_id] + selected_seats)
        seat_details = cursor.fetchall()

        if len(seat_details) != len(selected_seats):
            flash("Invalid seats selected. Please select seats again.", "error")
            return redirect(url_for("user.select_seats", bus_id=bus_id))

        cursor.execute("""
            SELECT seats FROM bookings
            WHERE bus_id = %s AND status = 'confirmed'
        """, (bus_id,))
        booked_seats = set()
        for booking in cursor.fetchall():
            try:
                for seat in json.loads(booking['seats']):
                    booked_seats.add(seat['number'] if isinstance(seat, dict) else seat)
            except Exception:
                pass

        conflict_seats = set(selected_seats) & booked_seats
        if conflict_seats:
            flash(
                f"Sorry, seat{'s' if len(conflict_seats) > 1 else ''} {', '.join(sorted(conflict_seats))} "
                f"{'are' if len(conflict_seats) > 1 else 'is'} no longer available. Please select different seats.",
                "error"
            )
            return redirect(url_for("user.select_seats", bus_id=bus_id))

        base_total_amount = sum(float(bus['price']) * float(seat['price_modifier']) for seat in seat_details)
        if base_total_amount <= 0:
            flash("Invalid booking amount. Please try again.", "error")
            return redirect(url_for("user.search"))

        display_travel_date = effective_travel_date_from_session(bus)
        travel_date_obj = ensure_date(display_travel_date) or ensure_date(bus.get('travel_date'))

        cursor.execute("SELECT COUNT(*) AS cnt FROM bookings WHERE user_id = %s", (session['user_id'],))
        user_booking_count = int((cursor.fetchone() or {}).get('cnt') or 0)
        first_travel_offer_eligible = (user_booking_count == 0)

        entertainment_items = session.get('entertainment_items', []) or []
        has_books_item = any('book' in str(item).lower() for item in entertainment_items)
        seasonal_months = {4, 5, 6, 11, 12, 1}
        seasonal_books_offer_eligible = bool(
            has_books_item and travel_date_obj and getattr(travel_date_obj, 'month', None) in seasonal_months
        )

        selected_first_offer = request.form.get("apply_first_travel_offer") == "1" if request.method == "POST" else False
        selected_seasonal_books_offer = request.form.get("apply_seasonal_books_offer") == "1" if request.method == "POST" else False

        if selected_first_offer and not first_travel_offer_eligible:
            selected_first_offer = False
            flash("First travel offer is available only on your first booking.", "warning")

        if selected_seasonal_books_offer and not seasonal_books_offer_eligible:
            selected_seasonal_books_offer = False
            flash("Seasonal books offer is available only when books are selected during seasonal months.", "warning")

        first_travel_discount = round(base_total_amount * 0.10, 2) if selected_first_offer else 0.0
        seasonal_books_discount = round(base_total_amount * 0.15, 2) if selected_seasonal_books_offer else 0.0
        total_discount = round(first_travel_discount + seasonal_books_discount, 2)
        total_amount = round(max(base_total_amount - total_discount, 0.0), 2)

        def render_payment_form(contact_number="", email="", payment_method="", upi_id="", netbanking_id="", card_id=""):
            return render_template(
                "payment.html",
                bus=bus,
                selected_seats=selected_seats,
                boarding_point=session['boarding_point'],
                dropping_point=session['dropping_point'],
                display_travel_date=display_travel_date,
                contact_number=contact_number,
                email=email,
                payment_method=payment_method,
                upi_id=upi_id,
                netbanking_id=netbanking_id,
                card_id=card_id,
                emergency_services=session.get('emergency_services', []),
                entertainment_items=entertainment_items,
                base_total_amount=base_total_amount,
                first_travel_offer_eligible=first_travel_offer_eligible,
                seasonal_books_offer_eligible=seasonal_books_offer_eligible,
                apply_first_travel_offer=selected_first_offer,
                apply_seasonal_books_offer=selected_seasonal_books_offer,
                first_travel_discount=first_travel_discount,
                seasonal_books_discount=seasonal_books_discount,
                total_discount=total_discount,
                total_amount=total_amount,
            )

        if request.method == "POST":
            contact_number = (request.form.get("contact_number") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            payment_method = (request.form.get("payment_method") or "").strip()
            upi_id = (request.form.get("upi_id") or "").strip()
            netbanking_id = (request.form.get("netbanking_id") or "").strip()
            card_id = (request.form.get("card_id") or "").strip()

            if total_amount <= 0:
                flash("Invalid final booking amount. Please review offers.", "error")
                return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)

            valid_payment_methods = ["UPI", "Credit/Debit Card", "Net Banking"]
            if payment_method not in valid_payment_methods:
                flash("Please select a valid payment method.", "error")
                return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)

            if not re.fullmatch(r"\d{10}", contact_number):
                flash("Please enter a valid 10-digit contact number.", "error")
                return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)

            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                flash("Please enter a valid passenger email address.", "error")
                return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)

            payment_reference_id = ""
            if payment_method == "UPI":
                payment_reference_id = upi_id
                if not payment_reference_id:
                    flash("Please enter your UPI ID.", "error")
                    return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)
            elif payment_method == "Net Banking":
                payment_reference_id = netbanking_id
                if not payment_reference_id:
                    flash("Please enter your Net Banking ID.", "error")
                    return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)
            else:
                payment_reference_id = card_id
                if not payment_reference_id:
                    flash("Please enter your Card ID/Number.", "error")
                    return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)

            import time
            time.sleep(1)

            seats_data = []
            for seat in seat_details:
                seats_data.append({
                    'number': seat['seat_number'],
                    'price': float(bus['price']) * float(seat['price_modifier'])
                })

            try:
                booking_code = "BUS" + str(random.randint(1000, 9999))
                booking_travel_date = effective_travel_date_from_session(bus)
                emergency_services = session.get('emergency_services', [])

                has_emergency_column = False
                has_payment_reference_column = False
                has_entertainment_column = False
                try:
                    cursor.execute("""
                        SELECT COLUMN_NAME
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = 'bookings'
                          AND COLUMN_NAME IN ('emergency_services', 'payment_reference_id', 'entertainment_items')
                    """)
                    cols = {r.get("COLUMN_NAME") for r in (cursor.fetchall() or [])}
                    has_emergency_column = "emergency_services" in cols
                    has_payment_reference_column = "payment_reference_id" in cols
                    has_entertainment_column = "entertainment_items" in cols
                except Exception as schema_check_error:
                    print(f"Could not check bookings optional columns: {schema_check_error}")

                if not has_emergency_column:
                    try:
                        cursor.execute("ALTER TABLE bookings ADD COLUMN emergency_services JSON NULL")
                        has_emergency_column = True
                    except Exception as schema_alter_error:
                        print(f"Could not add emergency_services column: {schema_alter_error}")

                if not has_payment_reference_column:
                    try:
                        cursor.execute("ALTER TABLE bookings ADD COLUMN payment_reference_id VARCHAR(100) NULL")
                        has_payment_reference_column = True
                    except Exception as schema_alter_error:
                        print(f"Could not add payment_reference_id column: {schema_alter_error}")

                if not has_entertainment_column:
                    try:
                        cursor.execute("ALTER TABLE bookings ADD COLUMN entertainment_items JSON NULL")
                        has_entertainment_column = True
                    except Exception as schema_alter_error:
                        print(f"Could not add entertainment_items column: {schema_alter_error}")

                columns = [
                    "user_id", "bus_id", "seats", "passenger_name", "contact_number", "email",
                    "boarding_point", "dropping_point", "total_amount", "payment_method", "travel_date", "booking_id",
                ]
                values = [
                    session['user_id'], bus_id, json.dumps(seats_data), session['user_name'], contact_number, email,
                    session['boarding_point'], session['dropping_point'], total_amount, payment_method, booking_travel_date, booking_code,
                ]

                if has_emergency_column:
                    columns.append("emergency_services")
                    values.append(json.dumps(emergency_services))
                if has_entertainment_column:
                    columns.append("entertainment_items")
                    values.append(json.dumps(session.get('entertainment_items', [])))
                if has_payment_reference_column:
                    columns.append("payment_reference_id")
                    values.append(payment_reference_id)

                placeholders = ", ".join(["%s"] * len(values))
                cursor.execute(f"INSERT INTO bookings({', '.join(columns)}) VALUES({placeholders})", tuple(values))
                db.commit()
                booking_id = cursor.lastrowid

                offers_applied = []
                if selected_first_offer:
                    offers_applied.append("First Travel Offer (10% OFF)")
                if selected_seasonal_books_offer:
                    offers_applied.append("Seasonal Books Offer (15% OFF)")

                booking_email_data = {
                    'booking_id': booking_code,
                    'passenger_name': session['user_name'],
                    'source': bus['source'],
                    'destination': bus['destination'],
                    'travel_date': booking_travel_date,
                    'departure_time': bus.get('departure_time'),
                    'bus_name': bus['bus_name'],
                    'bus_type': bus.get('bus_type', 'N/A'),
                    'seats': json.dumps(seats_data),
                    'seats_list': seats_data,
                    'total_amount': total_amount,
                    'base_total_amount': base_total_amount,
                    'total_discount': total_discount,
                    'offers_applied': offers_applied,
                    'emergency_services': emergency_services,
                    'entertainment_items': session.get('entertainment_items', []),
                    'payment_reference_id': payment_reference_id,
                    'email': email
                }
                if not send_booking_confirmation_email(booking_email_data):
                    print(f"Failed to send booking confirmation email: {getattr(send_email, 'last_error', 'Unknown mail error')}")

                session.pop('selected_seats', None)
                session.pop('boarding_point', None)
                session.pop('dropping_point', None)
                session.pop('bus_id', None)
                session.pop('search_travel_date', None)
                session.pop('emergency_services', None)
                session.pop('entertainment_items', None)

                flash("Payment successful! Your booking is confirmed.", "success")
                return redirect(url_for("user.booking_confirmation", booking_id=booking_id))

            except mysql.connector.Error as db_error:
                db.rollback()
                print(f"Database error during booking creation: {db_error}")
                flash("Failed to create booking. Please try again.", "error")
                return render_payment_form(contact_number, email, payment_method, upi_id, netbanking_id, card_id)

        return render_payment_form()

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
        flash(f"An unexpected error occurred: {e}", "error")
        return redirect(url_for("user.search"))

    finally:
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
        buses.departure_time, buses.arrival_time,
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

    # Parse emergency service requests (if any)
    try:
        booking['emergency_services'] = json.loads(booking.get('emergency_services') or '[]')
    except Exception:
        booking['emergency_services'] = []

    try:
        booking['entertainment_items'] = json.loads(booking.get('entertainment_items') or '[]')
    except Exception:
        booking['entertainment_items'] = []

    booking['travel_date'] = ensure_date(booking.get('travel_date'))
    booking['departure_time'] = ensure_time(booking.get('departure_time'))
    booking['arrival_time'] = ensure_time(booking.get('arrival_time'))

    return render_template("confirmation.html", booking=booking)
    try:
        subject = f"BusHub - Booking Confirmed - {booking['booking_id']}"
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1>🎉 Booking Confirmed!</h1>
                <p>Your bus ticket has been successfully booked</p>
            </div>

            <div style="background: white; padding: 20px; border: 1px solid #ddd; border-radius: 0 0 10px 10px;">
                <h2 style="color: #333;">Booking Details</h2>
                <p><strong>Booking ID:</strong> {booking['booking_id']}</p>
                <p><strong>Passenger:</strong> {booking['passenger_name']}</p>
                <p><strong>Route:</strong> {booking['source']} → {booking['destination']}</p>
                <p><strong>Travel Date:</strong> {booking['travel_date'].strftime('%Y-%m-%d') if hasattr(booking['travel_date'], 'strftime') else (booking['travel_date'] or 'N/A')}</p>
                <p><strong>Departure:</strong> {booking['departure_time'].strftime('%H:%M') if hasattr(booking['departure_time'], 'strftime') else (booking['departure_time'] or 'N/A')}</p>
                <p><strong>Bus:</strong> {booking['bus_name']} ({booking['bus_type']})</p>
                <p><strong>Seats:</strong> {', '.join([seat['number'] for seat in booking['seats_list']])}</p>
                <p><strong>Total Amount:</strong> ₹{booking['total_amount']}</p>
                <p><strong>Emergency Services Requested:</strong> {', '.join(booking.get('emergency_services', [])) if booking.get('emergency_services') else 'None'}</p>

                <div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="color: #28a745; margin-top: 0;">Important Instructions:</h3>
                    <ul>
                        <li>Arrive at the boarding point 30 minutes before departure</li>
                        <li>Carry a valid ID proof for verification</li>
                        <li>Show this email or the e-ticket on your mobile</li>
                        <li>For any queries, contact our support team</li>
                    </ul>
                </div>

                <p style="color: #666; font-size: 12px;">
                    Thank you for choosing BusHub! Safe travels.<br>
                    This is an automated email. Please do not reply.
                </p>
            </div>
        </body>
        </html>
        """

        send_email(subject, [booking['email']], f"Booking confirmed for {booking['booking_id']}", html_body)
    except Exception as e:
        print(f"Failed to send booking confirmation email: {e}")
        # Don't fail the booking if email fails

    return render_template("confirmation.html", booking=booking)

@user_bp.route("/booking_history")
def booking_history():
    """User booking history"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Do not select buses.travel_date here: it shares the key `travel_date` with b.* and overwrites the booking date in dict rows.
    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.departure_time, buses.arrival_time
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.user_id = %s
        ORDER BY b.booking_date DESC
    """, (session['user_id'],))

    bookings = cursor.fetchall()

    for booking in bookings:
        booking['seats_list'] = json.loads(booking['seats'])
        try:
            booking['emergency_services'] = json.loads(booking.get('emergency_services') or '[]')
        except Exception:
            booking['emergency_services'] = []

        try:
            booking['entertainment_items'] = json.loads(booking.get('entertainment_items') or '[]')
        except Exception:
            booking['entertainment_items'] = []

        booking['travel_date'] = ensure_date(booking.get('travel_date'))
        booking['departure_time'] = ensure_time(booking.get('departure_time'))
        booking['arrival_time'] = ensure_time(booking.get('arrival_time'))

    return render_template("booking_history.html", bookings=bookings, current_date=datetime.now().date())

@user_bp.route("/refund_info/<int:booking_id>")
def refund_info(booking_id):
    """Return refund details for a booking without cancelling."""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.id, b.total_amount, b.status, b.travel_date AS booking_travel_date,
               buses.departure_time, buses.travel_date AS bus_travel_date
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s AND b.user_id = %s
    """, (booking_id, session['user_id']))

    booking = cursor.fetchone()
    cursor.close()
    db.close()

    if not booking:
        return jsonify({"success": False, "message": "Booking not found"}), 404

    if booking.get('status') != 'confirmed':
        return jsonify({"success": False, "message": "Refund info is available only for confirmed bookings"}), 400

    travel_date_effective = booking.get('booking_travel_date') or booking.get('bus_travel_date')
    departure_time_obj = (datetime.min + booking['departure_time']).time()
    travel_datetime = datetime.combine(travel_date_effective, departure_time_obj)
    current_time = datetime.now()
    time_diff = travel_datetime - current_time

    if time_diff < timedelta(hours=2):
        return jsonify({
            "success": True,
            "can_cancel": False,
            "policy": "Cancellation not allowed within 2 hours of departure.",
            "refund_percentage": 0,
            "refund_amount": 0
        })

    if time_diff > timedelta(hours=24):
        refund_percentage = 90
    else:
        refund_percentage = 50

    refund_amount = float(booking['total_amount']) * (refund_percentage / 100)

    return jsonify({
        "success": True,
        "can_cancel": True,
        "policy": "90% refund if cancelled before 24 hours, 50% refund if cancelled 2-24 hours before departure.",
        "refund_percentage": refund_percentage,
        "refund_amount": round(refund_amount, 2)
    })

@user_bp.route("/cancel_booking/<int:booking_id>", methods=["POST"])
def cancel_booking(booking_id):
    """Cancel booking with refund calculation"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Get booking details with bus info (bus_travel_date avoids overwriting b.travel_date from b.*)
    cursor.execute("""
        SELECT
            b.*,
            buses.bus_name,
            buses.source,
            buses.destination,
            buses.bus_type,
            buses.operator,
            buses.departure_time,
            buses.arrival_time,
            buses.travel_date AS bus_travel_date
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s AND b.user_id = %s AND b.status = 'confirmed'
    """, (booking_id, session['user_id']))

    booking = cursor.fetchone()

    if not booking:
        flash("Booking not found or already cancelled", "error")
        return redirect(url_for("user.booking_history"))

    travel_date_effective = booking.get('travel_date') or booking.get('bus_travel_date')

    # Check cancellation policy - no cancellation within 2 hours of departure
    from datetime import datetime, timedelta
    departure_time_obj = (datetime.min + booking['departure_time']).time()
    travel_datetime = datetime.combine(travel_date_effective, departure_time_obj)
    current_time = datetime.now()

    if travel_datetime - current_time < timedelta(hours=2):
        flash("Cancellation not allowed within 2 hours of departure", "error")
        return redirect(url_for("user.booking_history"))

    # Calculate refund amount (90% if more than 24 hours, 50% if 2-24 hours)
    time_diff = travel_datetime - current_time
    total = float(booking['total_amount'])
    if time_diff > timedelta(hours=24):
        refund_percentage = 90
    else:
        refund_percentage = 50

    refund_amount = total * (refund_percentage / 100)
    refund_policy = "90% refund if cancelled before 24 hours, 50% refund if cancelled 2-24 hours before departure."

    # Update booking status and add refund info
    cursor.execute("""
        UPDATE bookings
        SET status = 'cancelled', refund_amount = %s, cancelled_at = NOW()
        WHERE id = %s
    """, (refund_amount, booking_id))

    db.commit()
    flash(f"Booking cancelled successfully. Refund amount: ₹{refund_amount:.2f} will be processed within 5-7 business days.", "success")

    # Send cancellation email with structured email builder
    try:
        send_cancellation_confirmation_email(
            booking,
            refund_amount,
            refund_percentage=refund_percentage,
            refund_policy=refund_policy,
        )
    except Exception as e:
        print(f"Failed to send cancellation email: {e}")
        # Don't fail the cancellation if email fails

    # Get updated booking details for the cancellation page
    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.bus_type, buses.operator, buses.departure_time, buses.arrival_time, buses.price
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s
    """, (booking_id,))

    updated_booking = cursor.fetchone()
    cursor.close()
    db.close()

    if updated_booking:
        # Parse and normalize values
        updated_booking['seats_list'] = json.loads(updated_booking['seats'])
        updated_booking['seat_count'] = len(updated_booking['seats_list'])
        try:
            updated_booking['emergency_services'] = json.loads(updated_booking.get('emergency_services') or '[]')
        except Exception:
            updated_booking['emergency_services'] = []

        try:
            updated_booking['entertainment_items'] = json.loads(updated_booking.get('entertainment_items') or '[]')
        except Exception:
            updated_booking['entertainment_items'] = []

        updated_booking['travel_date'] = ensure_date(updated_booking.get('travel_date'))
        updated_booking['departure_time'] = ensure_time(updated_booking.get('departure_time'))
        updated_booking['arrival_time'] = ensure_time(updated_booking.get('arrival_time'))
        return render_template("cancellation_details.html", booking=updated_booking)
    else:
        return redirect(url_for("user.booking_history"))

@user_bp.route("/cancellation_details/<int:booking_id>")
def cancellation_details(booking_id):
    """Show cancellation details and refund information"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.bus_type, buses.operator, buses.departure_time, buses.arrival_time, buses.price
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s AND b.user_id = %s AND b.status = 'cancelled'
    """, (booking_id, session['user_id']))

    booking = cursor.fetchone()
    cursor.close()
    db.close()

    if not booking:
        flash("Cancellation details not found", "error")
        return redirect(url_for("user.booking_history"))

    # Parse and normalize values
    booking['seats_list'] = json.loads(booking['seats'])
    booking['seat_count'] = len(booking['seats_list'])
    try:
        booking['emergency_services'] = json.loads(booking.get('emergency_services') or '[]')
    except Exception:
        booking['emergency_services'] = []

    try:
        booking['entertainment_items'] = json.loads(booking.get('entertainment_items') or '[]')
    except Exception:
        booking['entertainment_items'] = []

    booking['travel_date'] = ensure_date(booking.get('travel_date'))
    booking['departure_time'] = ensure_time(booking.get('departure_time'))
    booking['arrival_time'] = ensure_time(booking.get('arrival_time'))

    return render_template("cancellation_details.html", booking=booking)

@user_bp.route("/view_ticket/<int:booking_id>")
def view_ticket(booking_id):
    """View ticket in browser"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.bus_type, buses.operator, buses.departure_time, buses.arrival_time, buses.price
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s AND b.user_id = %s
    """, (booking_id, session['user_id']))

    booking = cursor.fetchone()

    if not booking:
        flash("Booking not found", "error")
        return redirect(url_for("user.booking_history"))

    # Parse and normalize values
    booking['seats_list'] = json.loads(booking['seats'])
    booking['seat_count'] = len(booking['seats_list'])
    try:
        booking['emergency_services'] = json.loads(booking.get('emergency_services') or '[]')
    except Exception:
        booking['emergency_services'] = []

    try:
        booking['entertainment_items'] = json.loads(booking.get('entertainment_items') or '[]')
    except Exception:
        booking['entertainment_items'] = []

    booking['travel_date'] = ensure_date(booking.get('travel_date'))
    booking['departure_time'] = ensure_time(booking.get('departure_time'))
    booking['arrival_time'] = ensure_time(booking.get('arrival_time'))

    return render_template("ticket.html", booking=booking)

@user_bp.route("/download_ticket/<int:booking_id>")
def download_ticket(booking_id):
    """Download PDF ticket"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.bus_type, buses.operator, buses.departure_time, buses.arrival_time
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

@app.route("/verify_ticket/<int:booking_id>")
def verify_ticket(booking_id):
    """Public route to verify ticket by scanning QR code"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.bus_type, buses.operator, buses.departure_time, buses.arrival_time, buses.price
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s
    """, (booking_id,))

    booking = cursor.fetchone()
    cursor.close()
    db.close()

    if not booking:
        return render_template("404.html"), 404

    # Parse and normalize values
    booking['seats_list'] = json.loads(booking['seats'])
    booking['seat_count'] = len(booking['seats_list'])
    booking['travel_date'] = ensure_date(booking.get('travel_date'))
    booking['departure_time'] = ensure_time(booking.get('departure_time'))
    booking['arrival_time'] = ensure_time(booking.get('arrival_time'))

    return render_template("verify_ticket.html", booking=booking)

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

@user_bp.route("/feedback/<int:booking_id>", methods=["GET", "POST"])
def feedback(booking_id):
    """Passenger feedback form after travel"""
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    # Get booking details
    cursor.execute("""
        SELECT b.*, buses.bus_name, buses.source, buses.destination,
               buses.bus_type, buses.operator
        FROM bookings b
        JOIN buses ON b.bus_id = buses.id
        WHERE b.id = %s AND b.user_id = %s AND b.status = 'confirmed'
    """, (booking_id, session['user_id']))

    booking = cursor.fetchone()

    if not booking:
        flash("Booking not found", "error")
        return redirect(url_for("user.booking_history"))

    # Check if travel date has passed (allow feedback only after travel).
    # When coming from the booking confirmation page, we allow early feedback too.
    allow_early = request.method == "POST" and request.form.get("allow_early") in ("1", "true", "True", "yes", "YES")
    if booking['travel_date'] and booking['travel_date'] > datetime.now().date() and not allow_early:
        flash("Feedback can only be submitted after the travel date", "warning")
        return redirect(url_for("user.booking_history"))

    if request.method == "POST":
        # Get feedback data
        # `rating` is optional in some forms; fall back to journey/overall rating.
        overall_experience = request.form.get('overall_experience') or request.form.get('journey_rating') or '5'
        rating = request.form.get('rating') or overall_experience
        comfort = request.form.get('comfort', '5')
        cleanliness = request.form.get('cleanliness', '5')
        punctuality = request.form.get('punctuality', '5')
        staff_behavior = request.form.get('staff_behavior', '5')
        facilities_rating = request.form.get('facilities_rating', '5')
        comments = request.form.get('comments', '')

        # Save feedback to database (you might want to create a feedback table)
        # For now, just send an email with feedback
        try:
            subject = f"BusHub - Passenger Feedback - {booking['booking_id']}"
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #17a2b8 0%, #138496 100%); color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                    <h1>📝 Passenger Feedback</h1>
                    <p>Feedback received for booking {booking['booking_id']}</p>
                </div>

                <div style="background: white; padding: 20px; border: 1px solid #ddd; border-radius: 0 0 10px 10px;">
                    <h2 style="color: #333;">Booking Details</h2>
                    <p><strong>Booking ID:</strong> {booking['booking_id']}</p>
                    <p><strong>Passenger:</strong> {booking['passenger_name']}</p>
                    <p><strong>Route:</strong> {booking['source']} → {booking['destination']}</p>
                    <p><strong>Bus:</strong> {booking['bus_name']} ({booking['bus_type']})</p>

                    <h3 style="color: #17a2b8;">Feedback Ratings</h3>
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 5px;">
                        <p><strong>Overall Rating:</strong> {rating}/5 ⭐</p>
                        <p><strong>Comfort:</strong> {comfort}/5</p>
                        <p><strong>Cleanliness:</strong> {cleanliness}/5</p>
                        <p><strong>Punctuality:</strong> {punctuality}/5</p>
                        <p><strong>Staff Behavior:</strong> {staff_behavior}/5</p>
                        <p><strong>Overall Experience:</strong> {overall_experience}/5</p>
                        <p><strong>Bus Facilities:</strong> {facilities_rating}/5</p>
                    </div>"""

            if comments:
                html_body += f"""
                    <div style="background: #e9ecef; padding: 15px; border-radius: 5px; margin: 15px 0;">
                        <h4 style="margin-top: 0;">Additional Comments:</h4>
                        <p style="font-style: italic;">{comments}</p>
                    </div>"""

            html_body += f"""
                    <p style="color: #666; font-size: 12px;">
                        Feedback submitted on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
                        This feedback helps us improve our services.
                    </p>
                </div>
            </body>
            </html>
            """

            # Send feedback email to admin/support
            send_email(subject, [app.config['MAIL_USERNAME']], f"New feedback received for booking {booking['booking_id']}", html_body)

            # Update bus average rating in the buses table (bus feedback)
            try:
                cursor.execute("ALTER TABLE buses ADD COLUMN IF NOT EXISTS rating_count INT DEFAULT 0")
            except Exception:
                pass

            cursor.execute("SELECT rating, rating_count FROM buses WHERE id = %s", (booking['bus_id'],))
            bus_info = cursor.fetchone()
            if bus_info:
                current_rating = float(bus_info.get('rating') or 0)
                current_count = int(bus_info.get('rating_count') or 0)
                new_count = current_count + 1
                new_rating = round(((current_rating * current_count) + float(rating)) / new_count, 2)
                cursor.execute("UPDATE buses SET rating = %s, rating_count = %s WHERE id = %s", (new_rating, new_count, booking['bus_id']))
                db.commit()

            flash("Thank you for your feedback! Your input helps us improve our services.", "success")
            return redirect(url_for("user.booking_history"))

        except Exception as e:
            print(f"Failed to send feedback email: {e}")
            flash("Feedback submitted successfully!", "success")
            return redirect(url_for("user.booking_history"))

    # Parse seats for display
    booking['seats_list'] = json.loads(booking['seats'])

    return render_template("feedback.html", booking=booking)

@app.route("/emergency")
def emergency():
    """Emergency contact page"""
    return render_template("emergency.html")

@user_bp.route("/rate-site", methods=["GET", "POST"])
def rate_site():
    """Allow users to rate the website"""
    if "user_id" not in session:
        flash("Please login to rate our site", "error")
        return redirect(url_for("auth.login"))

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    if request.method == "POST":
        rating = request.form.get("rating")
        review = request.form.get("review", "").strip()

        if not rating or not rating.isdigit() or not (1 <= int(rating) <= 5):
            flash("Please select a valid rating (1-5 stars)", "error")
            return redirect(url_for("user.rate_site"))

        try:
            # Check if user already rated
            cursor.execute("SELECT id FROM site_ratings WHERE user_id = %s", (session["user_id"],))
            existing = cursor.fetchone()

            if existing:
                # Update existing rating
                cursor.execute("""
                    UPDATE site_ratings 
                    SET rating = %s, review = %s, created_at = CURRENT_TIMESTAMP 
                    WHERE user_id = %s
                """, (int(rating), review, session["user_id"]))
                flash("Your rating has been updated! Thank you for your feedback.", "success")
            else:
                # Insert new rating
                cursor.execute("""
                    INSERT INTO site_ratings (user_id, rating, review) 
                    VALUES (%s, %s, %s)
                """, (session["user_id"], int(rating), review))
                flash("Thank you for rating our site! Your feedback helps us improve.", "success")

            db.commit()

        except Exception as e:
            db.rollback()
            print(f"Error saving site rating: {e}")
            flash("An error occurred while saving your rating. Please try again.", "error")
            return redirect(url_for("user.rate_site"))

        return redirect(url_for("user.search"))

    # Get current user's rating if exists
    cursor.execute("SELECT rating, review FROM site_ratings WHERE user_id = %s", (session["user_id"],))
    user_rating = cursor.fetchone()

    # Get overall site statistics
    cursor.execute("SELECT AVG(rating) as avg_rating, COUNT(*) as total_ratings FROM site_ratings")
    stats = cursor.fetchone()

    cursor.close()

    return render_template("rate_site.html", user_rating=user_rating, stats=stats)

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
        travel_date = request.form.get("travel_date")
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

@app.route('/booking-success')
def booking_success():
     return "Payment Successful! Booking Confirmed."

# ================= RUN APP =================
if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)

