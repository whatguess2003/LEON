-- Project-wide SQL indexes manifest (idempotent)
-- Apply using setup_schema.py integration; uses standard CREATE INDEX IF NOT EXISTS

-- maintenance_requests
CREATE INDEX IF NOT EXISTS idx_mr_hotel_task ON maintenance_requests(hotel_name, task_id);
CREATE INDEX IF NOT EXISTS idx_mr_hotel_status ON maintenance_requests(hotel_name, status);
CREATE INDEX IF NOT EXISTS idx_mr_hotel_urgency ON maintenance_requests(hotel_name, urgency);
CREATE INDEX IF NOT EXISTS idx_mr_hotel_reported_at ON maintenance_requests(hotel_name, reported_at);
CREATE INDEX IF NOT EXISTS idx_mr_assigned_to ON maintenance_requests(assigned_to) WHERE assigned_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mr_hotel_room ON maintenance_requests(hotel_name, room_number);

-- room_number_assignments
CREATE INDEX IF NOT EXISTS idx_rna_hotel_status_date ON room_number_assignments(hotel_name, assignment_status, stay_date);
CREATE INDEX IF NOT EXISTS idx_rna_hotel_date ON room_number_assignments(hotel_name, stay_date);

-- room_readiness_status
CREATE INDEX IF NOT EXISTS idx_rrs_hotel_status ON room_readiness_status(hotel_name, readiness_status);
CREATE INDEX IF NOT EXISTS idx_rrs_status ON room_readiness_status(readiness_status);

-- booking_stays
CREATE INDEX IF NOT EXISTS idx_bs_hotel_date ON booking_stays(hotel_name, stay_date);
CREATE INDEX IF NOT EXISTS idx_bs_hotel_booking ON booking_stays(hotel_name, booking_id);
CREATE INDEX IF NOT EXISTS idx_bs_hotel_roomtype_date ON booking_stays(hotel_name, room_type, stay_date);

-- room_bookings
CREATE INDEX IF NOT EXISTS idx_rb_hotel_status ON room_bookings(hotel_name, booking_status);
CREATE INDEX IF NOT EXISTS idx_rb_hotel_created ON room_bookings(hotel_name, created_at);

-- payment_tracking
CREATE INDEX IF NOT EXISTS idx_pt_booking ON payment_tracking(booking_id);
CREATE INDEX IF NOT EXISTS idx_pt_status ON payment_tracking(payment_status);

-- room_stay
CREATE INDEX IF NOT EXISTS idx_rs_hotel_room_date ON room_stay(hotel_name, room_number, stay_date);

-- cleaning_status
CREATE INDEX IF NOT EXISTS idx_cs_hotel_status ON cleaning_status(hotel_name, cleaning_status);
CREATE INDEX IF NOT EXISTS idx_cs_assigned_to ON cleaning_status(assigned_to) WHERE assigned_to IS NOT NULL;


