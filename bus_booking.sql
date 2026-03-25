-- ================= DATABASE =================
CREATE DATABASE IF NOT EXISTS bus_booking;
USE bus_booking;

-- ================= USERS TABLE =================
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================= BUSES TABLE =================
CREATE TABLE IF NOT EXISTS buses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    bus_name VARCHAR(100) NOT NULL,
    source VARCHAR(100) NOT NULL,
    destination VARCHAR(100) NOT NULL,
    stops JSON, -- Array of stop names in order
    departure_time TIME NOT NULL,
    arrival_time TIME NOT NULL,
    travel_date DATE NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    seats_total INT NOT NULL DEFAULT 40,
    bus_type ENUM('AC', 'Non-AC', 'Sleeper', 'Seater', 'Double Decker') DEFAULT 'Non-AC',
    amenities JSON, -- Array of amenities like ['WiFi', 'Charging', 'Water']
    rating DECIMAL(3,2) DEFAULT 4.5,
    operator VARCHAR(100) DEFAULT 'BusHub'
);

-- ================= ROUTES TABLE (for multiple stops) =================
CREATE TABLE IF NOT EXISTS routes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    bus_id INT NOT NULL,
    stop_name VARCHAR(100) NOT NULL,
    stop_order INT NOT NULL,
    arrival_time TIME,
    departure_time TIME,
    FOREIGN KEY (bus_id) REFERENCES buses(id) ON DELETE CASCADE
);

-- ================= BOOKINGS TABLE =================


-- ================= SEAT DETAILS TABLE =================
CREATE TABLE IF NOT EXISTS seat_details (
    id INT AUTO_INCREMENT PRIMARY KEY,
    bus_id INT NOT NULL,
    seat_number VARCHAR(10) NOT NULL,
    seat_type ENUM('Window', 'Aisle', 'Sleeper', 'Seater') DEFAULT 'Seater',
    deck ENUM('Lower', 'Upper') DEFAULT 'Lower',
    gender_restriction ENUM('Male', 'Female', 'None') DEFAULT 'None',
    price_modifier DECIMAL(3,2) DEFAULT 1.0, -- Multiplier for seat price
    FOREIGN KEY (bus_id) REFERENCES buses(id) ON DELETE CASCADE,
    UNIQUE KEY unique_bus_seat (bus_id, seat_number)
);

-- ================= ADMIN USERS =================
CREATE TABLE IF NOT EXISTS admin_users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ================= CLEAR OLD DATA =================
DELETE FROM routes;
DELETE FROM seat_details;
DELETE FROM bookings;
DELETE FROM buses;
DELETE FROM users;

-- ================= INSERT ADMIN USER =================
INSERT INTO admin_users (username, password, email) VALUES
('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LeCt1uB0YgLpBqLZS', 'admin@bushub.com'); -- password: admin123

-- ================= INSERT SAMPLE BUSES =================
INSERT INTO buses (bus_name, source, destination, stops, departure_time, arrival_time, travel_date, price, seats_total, bus_type, amenities, rating, operator) VALUES
('KSRTC Express', 'Kochi', 'Coimbatore', '["Kochi", "Thrissur", "Palakkad", "Coimbatore"]', '08:00:00', '16:00:00', '2026-03-22', 1200.00, 40, 'AC', '["WiFi", "Charging", "Water", "Blanket"]', 4.2, 'KSRTC'),
('Super Deluxe', 'Calicut', 'Trivandrum', '["Calicut", "Kannur", "Kochi", "Trivandrum"]', '09:00:00', '20:00:00', '2026-03-22', 1500.00, 45, 'Sleeper', '["WiFi", "AC", "Water"]', 4.5, 'Private'),
('TNSTC Express', 'Chennai', 'Coimbatore', '["Chennai", "Vellore", "Salem", "Coimbatore"]', '06:00:00', '14:00:00', '2026-03-22', 1000.00, 40, 'Non-AC', '["Water"]', 3.8, 'TNSTC'),
('Volvo AC', 'Bangalore', 'Chennai', '["Bangalore", "Hosur", "Krishnagiri", "Chennai"]', '22:00:00', '06:00:00', '2026-03-22', 1800.00, 40, 'AC', '["WiFi", "Charging", "Water", "Entertainment"]', 4.7, 'Volvo'),
('Shivneri', 'Mumbai', 'Pune', '["Mumbai", "Thane", "Pune"]', '07:00:00', '10:00:00', '2026-03-22', 600.00, 50, 'AC', '["WiFi", "Water"]', 4.0, 'MSRTC');

-- ================= INSERT ROUTES =================
INSERT INTO routes (bus_id, stop_name, stop_order, arrival_time, departure_time) VALUES
-- Bus 1: Kochi -> Coimbatore
(1, 'Kochi', 1, NULL, '08:00:00'),
(1, 'Thrissur', 2, '10:00:00', '10:15:00'),
(1, 'Palakkad', 3, '12:00:00', '12:15:00'),
(1, 'Coimbatore', 4, '16:00:00', NULL),

-- Bus 2: Calicut -> Trivandrum
(2, 'Calicut', 1, NULL, '09:00:00'),
(2, 'Kannur', 2, '11:00:00', '11:15:00'),
(2, 'Kochi', 3, '14:00:00', '14:30:00'),
(2, 'Trivandrum', 4, '20:00:00', NULL),

-- Bus 3: Chennai -> Coimbatore
(3, 'Chennai', 1, NULL, '06:00:00'),
(3, 'Vellore', 2, '08:00:00', '08:15:00'),
(3, 'Salem', 3, '11:00:00', '11:15:00'),
(3, 'Coimbatore', 4, '14:00:00', NULL),

-- Bus 4: Bangalore -> Chennai
(4, 'Bangalore', 1, NULL, '22:00:00'),
(4, 'Hosur', 2, '23:30:00', '23:45:00'),
(4, 'Krishnagiri', 3, '01:00:00', '01:15:00'),
(4, 'Chennai', 4, '06:00:00', NULL),

-- Bus 5: Mumbai -> Pune
(5, 'Mumbai', 1, NULL, '07:00:00'),
(5, 'Thane', 2, '08:00:00', '08:15:00'),
(5, 'Pune', 3, '10:00:00', NULL);

-- ================= INSERT SEAT DETAILS =================
-- For simplicity, insert seats for bus 1 (40 seats)
INSERT INTO seat_details (bus_id, seat_number, seat_type, deck, gender_restriction, price_modifier) VALUES
(1, '1A', 'Window', 'Lower', 'None', 1.0),
(1, '1B', 'Aisle', 'Lower', 'None', 1.0),
(1, '2A', 'Window', 'Lower', 'None', 1.0),
(1, '2B', 'Aisle', 'Lower', 'None', 1.0),
(1, '3A', 'Window', 'Lower', 'None', 1.0),
(1, '3B', 'Aisle', 'Lower', 'None', 1.0),
(1, '4A', 'Window', 'Lower', 'None', 1.0),
(1, '4B', 'Aisle', 'Lower', 'None', 1.0),
(1, '5A', 'Window', 'Lower', 'None', 1.0),
(1, '5B', 'Aisle', 'Lower', 'None', 1.0),
(1, '6A', 'Window', 'Lower', 'None', 1.0),
(1, '6B', 'Aisle', 'Lower', 'None', 1.0),
(1, '7A', 'Window', 'Lower', 'None', 1.0),
(1, '7B', 'Aisle', 'Lower', 'None', 1.0),
(1, '8A', 'Window', 'Lower', 'None', 1.0),
(1, '8B', 'Aisle', 'Lower', 'None', 1.0),
(1, '9A', 'Window', 'Lower', 'None', 1.0),
(1, '9B', 'Aisle', 'Lower', 'None', 1.0),
(1, '10A', 'Window', 'Lower', 'None', 1.0),
(1, '10B', 'Aisle', 'Lower', 'None', 1.0),
(1, '11A', 'Window', 'Upper', 'None', 1.1),
(1, '11B', 'Aisle', 'Upper', 'None', 1.1),
(1, '12A', 'Window', 'Upper', 'None', 1.1),
(1, '12B', 'Aisle', 'Upper', 'None', 1.1),
(1, '13A', 'Window', 'Upper', 'None', 1.1),
(1, '13B', 'Aisle', 'Upper', 'None', 1.1),
(1, '14A', 'Window', 'Upper', 'None', 1.1),
(1, '14B', 'Aisle', 'Upper', 'None', 1.1),
(1, '15A', 'Window', 'Upper', 'None', 1.1),
(1, '15B', 'Aisle', 'Upper', 'None', 1.1),
(1, '16A', 'Window', 'Upper', 'None', 1.1),
(1, '16B', 'Aisle', 'Upper', 'None', 1.1),
(1, '17A', 'Window', 'Upper', 'None', 1.1),
(1, '17B', 'Aisle', 'Upper', 'None', 1.1),
(1, '18A', 'Window', 'Upper', 'None', 1.1),
(1, '18B', 'Aisle', 'Upper', 'None', 1.1),
(1, '19A', 'Window', 'Upper', 'None', 1.1),
(1, '19B', 'Aisle', 'Upper', 'None', 1.1),
(1, '20A', 'Window', 'Upper', 'None', 1.1),
(1, '20B', 'Aisle', 'Upper', 'None', 1.1);CREATE TABLE IF NOT EXISTS bookings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    bus_id INT NOT NULL,
    seats JSON NOT NULL, -- Array of seat objects with details
    emergency_services JSON NULL, -- Optional emergency medical items requested
    passenger_name VARCHAR(100) NOT NULL,
    contact_number VARCHAR(15),
    email VARCHAR(100),
    boarding_point VARCHAR(100) NOT NULL,
    dropping_point VARCHAR(100) NOT NULL,
    total_amount DECIMAL(10,2) NOT NULL,
    booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    travel_date DATE,
    booking_id VARCHAR(20) UNIQUE,
    status ENUM('confirmed', 'cancelled') DEFAULT 'confirmed',
    refund_amount DECIMAL(10,2) DEFAULT 0,
    cancelled_at TIMESTAMP NULL,
    payment_method ENUM('UPI', 'Credit/Debit Card', 'Net Banking') NOT NULL,
    payment_reference_id VARCHAR(100) NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (bus_id) REFERENCES buses(id),
    CONSTRAINT check_amount CHECK (total_amount > 0),
    INDEX idx_user_id (user_id),
    INDEX idx_bus_id (bus_id),
    INDEX idx_booking_date (booking_date)
);

-- ================= SITE RATINGS TABLE =================
CREATE TABLE IF NOT EXISTS site_ratings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    rating INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
    review TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE KEY unique_user_rating (user_id)
);

-- ================= INSERT SAMPLE SITE RATINGS =================
INSERT INTO site_ratings (user_id, rating, review) VALUES
(1, 5, 'Excellent service! Very easy to book buses and the interface is user-friendly.'),
(2, 4, 'Great platform for bus booking. Would love to see more payment options.'),
(3, 5, 'Amazing experience! Fast booking process and reliable service.');