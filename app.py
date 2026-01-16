from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from sqlalchemy import (create_engine, Column, String, Integer, DateTime,
                        Boolean, Table, ForeignKey, select, and_, func)
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker
from datetime import datetime, timedelta, timezone
import uuid
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "db.sqlite")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Load config
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

def save_config(new_conf):
    with open(CONFIG_PATH, "w") as f:
        json.dump(new_conf, f, indent=2)
    global config
    config = new_conf

app = Flask(__name__, static_folder="public", static_url_path="/")
CORS(app)

# SQLAlchemy setup
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}, future=True)
SessionLocal = sessionmaker(bind=engine, future=True)
Base = declarative_base()

booking_participants = Table(
    "booking_participants",
    Base.metadata,
    Column("booking_id", String, ForeignKey("bookings.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    phone = Column(String, unique=True, nullable=False)
    name = Column(String)
    no_show_count = Column(Integer, default=0)
    restricted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(String, primary_key=True)
    organizer_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    start_ts = Column(DateTime, nullable=False)
    end_ts = Column(DateTime, nullable=False)
    note = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    participants = relationship("User", secondary=booking_participants, backref="bookings")
    organizer = relationship("User", foreign_keys=[organizer_user_id])

Base.metadata.create_all(bind=engine)

# Seed predefined users if empty
def seed_users():
    with SessionLocal() as session:
        count = session.scalar(select(func.count(User.id)))
        if count == 0:
            seed = [
                {"id": "u-1", "phone": "+15550000001", "name": "Alice"},
                {"id": "u-2", "phone": "+15550000002", "name": "Bob"},
                {"id": "u-3", "phone": "+15550000003", "name": "Carol"},
                {"id": "u-4", "phone": "+15550000004", "name": "David"},
            ]
            for s in seed:
                u = User(id=s["id"], phone=s["phone"], name=s["name"])
                session.add(u)
            session.commit()

seed_users()

# Helpers for ISO datetimes (UTC)
def now_utc():
    return datetime.now(timezone.utc)

def parse_iso(s: str):
    if s is None:
        return None
    # Accept 'Z' or offset; normalize Z => +00:00
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None

def to_iso(dt: datetime):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

# API endpoints

@app.get("/api/users")
def list_users():
    with SessionLocal() as session:
        users = session.execute(select(User).order_by(User.name)).scalars().all()
        out = []
        for u in users:
            out.append({
                "id": u.id,
                "phone": u.phone,
                "name": u.name,
                "no_show_count": u.no_show_count,
                "restricted": bool(u.restricted)
            })
        return jsonify(out)

@app.get("/api/bookings")
def list_bookings():
    phone = request.args.get("phone")
    with SessionLocal() as session:
        if phone:
            user = session.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
            if not user:
                return jsonify({"error": "user not found"}), 404
            # bookings where user is a participant
            stmt = select(Booking).join(booking_participants).where(booking_participants.c.user_id == user.id).order_by(Booking.start_ts)
            rows = session.execute(stmt).scalars().all()
        else:
            rows = session.execute(select(Booking).order_by(Booking.start_ts)).scalars().all()

        out = []
        for b in rows:
            out.append({
                "id": b.id,
                "organizer_user_id": b.organizer_user_id,
                "start_ts": to_iso(b.start_ts),
                "end_ts": to_iso(b.end_ts),
                "participants_phones": [p.phone for p in b.participants],
                "note": b.note
            })
        return jsonify(out)

@app.post("/api/bookings")
def create_booking():
    data = request.get_json() or {}
    organizer_phone = data.get("organizer_phone")
    start_ts = data.get("start_ts")
    end_ts = data.get("end_ts")
    participants_phones = data.get("participants_phones") or []
    note = data.get("note")

    if not organizer_phone or not start_ts or not end_ts:
        return jsonify({"error": "organizer_phone, start_ts and end_ts required"}), 400

    start = parse_iso(start_ts)
    end = parse_iso(end_ts)
    if not start or not end:
        return jsonify({"error": "invalid start or end timestamp (use ISO format)"}), 400
    if end <= start:
        return jsonify({"error": "end must be after start"}), 400

    # booking advance check
    min_start = now_utc() + timedelta(hours=config.get("bookingAdvanceHours", 24))
    if start < min_start:
        return jsonify({"error": f"bookings must be made at least {config.get('bookingAdvanceHours',24)} hours in advance"}), 400

    # max duration check
    max_days = config.get("maxBookingDurationDays", 366)
    if (end - start) > timedelta(days=max_days):
        return jsonify({"error": f"booking longer than {max_days} days is not allowed"}), 400

    with SessionLocal() as session:
        organizer = session.execute(select(User).where(User.phone == organizer_phone)).scalar_one_or_none()
        if not organizer:
            return jsonify({"error": "organizer not a predefined user"}), 404

        # build participant set and ensure predefined
        participants_set = set([p.strip() for p in participants_phones if p and p.strip()])
        participants_set.add(organizer_phone)
        participants = []
        for pphone in participants_set:
            u = session.execute(select(User).where(User.phone == pphone)).scalar_one_or_none()
            if not u:
                return jsonify({"error": f"participant {pphone} is not a predefined user"}), 400
            # check restriction against configured threshold
            if u.no_show_count > config.get("noShowThreshold", 7):
                return jsonify({"error": f"participant {pphone} is restricted due to too many no-shows"}), 403
            participants.append(u)

        # overlap check: for each participant, ensure no booking where NOT (b.end <= start OR b.start >= end)
        for u in participants:
            stmt = (
                select(Booking)
                .join(booking_participants)
                .where(
                    booking_participants.c.user_id == u.id,
                    # overlap condition:
                    and_(
                        Booking.end_ts > start,
                        Booking.start_ts < end
                    )
                )
            )
            overlaps = session.execute(stmt).scalars().all()
            if overlaps:
                return jsonify({
                    "error": f"participant {u.phone} has overlapping booking(s)",
                    "overlaps": [
                        {"id": ov.id, "start_ts": to_iso(ov.start_ts), "end_ts": to_iso(ov.end_ts)}
                        for ov in overlaps
                    ]
                }), 409

        # create booking
        b_id = str(uuid.uuid4())
        b = Booking(id=b_id, organizer_user_id=organizer.id, start_ts=start, end_ts=end, note=note)
        b.participants = participants
        session.add(b)
        session.commit()

        return jsonify({
            "id": b_id,
            "start_ts": to_iso(start),
            "end_ts": to_iso(end),
            "participants": [p.phone for p in participants]
        }), 201

@app.post("/api/admin/no-show")
def mark_no_show():
    data = request.get_json() or {}
    phone = data.get("phone")
    if not phone:
        return jsonify({"error": "phone required"}), 400
    with SessionLocal() as session:
        user = session.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
        if not user:
            return jsonify({"error": "user not found"}), 404
        user.no_show_count += 1
        user.restricted = user.no_show_count > config.get("noShowThreshold", 7)
        session.add(user)
        session.commit()
        return jsonify({"phone": phone, "no_show_count": user.no_show_count, "restricted": bool(user.restricted)})

@app.post("/api/admin/reset-no-show")
def reset_no_show():
    data = request.get_json() or {}
    phone = data.get("phone")
    if not phone:
        return jsonify({"error": "phone required"}), 400
    with SessionLocal() as session:
        user = session.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
        if not user:
            return jsonify({"error": "user not found"}), 404
        user.no_show_count = 0
        user.restricted = False
        session.add(user)
        session.commit()
        return jsonify({"phone": phone, "no_show_count": user.no_show_count, "restricted": False})

@app.get("/api/admin/config")
def get_config():
    return jsonify(config)

@app.post("/api/admin/config")
def update_config():
    data = request.get_json() or {}
    new_conf = dict(config)
    # Only allow expected keys
    for k in ("noShowThreshold", "bookingAdvanceHours", "maxBookingDurationDays"):
        if k in data:
            new_conf[k] = data[k]
    save_config(new_conf)
    return jsonify(new_conf)

# Serve static files (UI)
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.get("/<path:filename>")
def public_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)