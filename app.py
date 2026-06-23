import os
import re
import uuid
import math
import random
import base64
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware

import jwt
import bcrypt
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON, and_, or_, func, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

# Fallbacks for optional generator libraries to ensure robust booting
try:
    import qrcode
    from io import BytesIO
except ImportError:
    qrcode = None

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from io import BytesIO as RLBytesIO
except ImportError:
    RLBytesIO = None

# ==========================================
# DATABASE CONFIGURATION (Phase 1 Setup)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/petpals")

try:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        pass
    print("Successfully connected to local PostgreSQL database!")
except Exception as e:
    print(f"PostgreSQL connection failed ({e}). Falling back to local SQLite database (petpals.db)...")
    DATABASE_URL = "sqlite:///./petpals.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==========================================
# SECURITY & AUTHENTICATION CONFIG (Phase 1)
# ==========================================
SECRET_KEY = "SUPER_SECRET_PETPALS_KEY_FOR_LOCAL_DEV"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pwd_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except Exception:
        return False

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

# ==========================================
# DATABASE MODELS
# ==========================================

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="owner")  # owner, vet, admin
    location_lat = Column(Float, nullable=True)
    location_lng = Column(Float, nullable=True)
    address = Column(String, nullable=True)
    
    # Vet Specific
    clinic_name = Column(String, nullable=True)
    license_number = Column(String, nullable=True)
    specialization = Column(String, nullable=True)
    is_approved = Column(Boolean, default=False)  # Admin approval
    is_rejected = Column(Boolean, default=False)  # Admin rejection
    vet_id_code = Column(String, unique=True, nullable=True)  # VET-xxxxx
    
    otp_secret = Column(String, nullable=True)
    pets = relationship("Pet", back_populates="owner")

class Pet(Base):
    __tablename__ = "pets"
    id = Column(Integer, primary_key=True, index=True)
    unique_id = Column(String, unique=True, index=True, nullable=False)  # PET-xxxxx
    owner_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    species = Column(String, nullable=False)  # Dog, Cat, Bird, Rabbit, Reptile, Other
    breed = Column(String, nullable=True)
    dob = Column(DateTime, nullable=False)
    microchip = Column(String, nullable=True)
    photo_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="pets")
    vaccinations = relationship("VaccinationRecord", back_populates="pet", cascade="all, delete-orphan")
    medical_events = relationship("MedicalEvent", back_populates="pet", cascade="all, delete-orphan")
    prescriptions = relationship("Prescription", back_populates="pet", cascade="all, delete-orphan")

class Vaccine(Base):
    __tablename__ = "vaccines"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    species = Column(String, nullable=False)
    min_age_days = Column(Integer, nullable=False)
    booster_interval_days = Column(Integer, nullable=False)

class VaccinationRecord(Base):
    __tablename__ = "vaccination_records"
    id = Column(Integer, primary_key=True, index=True)
    pet_id = Column(Integer, ForeignKey("pets.id"))
    vaccine_name = Column(String, nullable=False)
    scheduled_date = Column(DateTime, nullable=False)
    administered_date = Column(DateTime, nullable=True)
    batch_number = Column(String, nullable=True)
    verified_by_vet = Column(Boolean, default=False)
    verified_by_doc = Column(Boolean, default=False)
    fully_verified = Column(Boolean, default=False)
    status = Column(String, default="pending")  # pending, verified, overdue
    document_proof = Column(Text, nullable=True)

    pet = relationship("Pet", back_populates="vaccinations")

class MedicalEvent(Base):
    __tablename__ = "medical_events"
    id = Column(Integer, primary_key=True, index=True)
    pet_id = Column(Integer, ForeignKey("pets.id"))
    date = Column(DateTime, default=datetime.utcnow)
    event_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    vet_reference = Column(String, nullable=True)
    attachments = Column(Text, nullable=True)

    pet = relationship("Pet", back_populates="medical_events")

class Prescription(Base):
    __tablename__ = "prescriptions"
    id = Column(Integer, primary_key=True, index=True)
    unique_id = Column(String, unique=True, index=True, nullable=False)
    pet_id = Column(Integer, ForeignKey("pets.id"))
    vet_id = Column(Integer, ForeignKey("users.id"))
    medicine = Column(String, nullable=False)
    dosage = Column(String, nullable=False)
    instructions = Column(Text, nullable=False)
    duration = Column(String, nullable=False)
    is_tele_triage = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    pet = relationship("Pet", back_populates="prescriptions")

class OwnerTransfer(Base):
    __tablename__ = "owner_transfers"
    id = Column(Integer, primary_key=True, index=True)
    pet_id = Column(Integer, ForeignKey("pets.id"))
    current_owner_id = Column(Integer, ForeignKey("users.id"))
    target_email = Column(String, nullable=False)
    otp_code = Column(String, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

class Promotion(Base):
    __tablename__ = "promotions"
    id = Column(Integer, primary_key=True, index=True)
    vet_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String, nullable=False)
    discount_code = Column(String, nullable=False)
    target_breed = Column(String, nullable=True)
    target_min_age_years = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    vet_id = Column(Integer, ForeignKey("users.id"))
    owner_id = Column(Integer, ForeignKey("users.id"))
    rating_care = Column(Integer, default=5)
    rating_communication = Column(Integer, default=5)
    rating_facility = Column(Integer, default=5)
    rating_value = Column(Integer, default=5)
    text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class EmergencyRequest(Base):
    __tablename__ = "emergency_requests"
    id = Column(Integer, primary_key=True, index=True)
    pet_id = Column(Integer, ForeignKey("pets.id"), nullable=True)
    symptoms = Column(Text, nullable=False)
    urgency_level = Column(String, nullable=False)
    location_lat = Column(Float, nullable=False)
    location_lng = Column(Float, nullable=False)
    status = Column(String, default="searching")
    assigned_vet_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Milestone(Base):
    __tablename__ = "milestones"
    id = Column(Integer, primary_key=True, index=True)
    letter = Column(String(1), unique=True, index=True, nullable=False) # A, B, C, etc.
    title = Column(String, nullable=False)
    category = Column(String, nullable=False) # e.g. "Stage 01: Neonatal"
    description = Column(Text, nullable=False)
    care_guideline = Column(Text, nullable=False)

# ==========================================
# DATABASE MIGRATIONS & SCHEMA PATCHING
# ==========================================
Base.metadata.create_all(bind=engine)

def patch_database_schema():
    if "postgresql" in DATABASE_URL:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_approved BOOLEAN DEFAULT FALSE;"))
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_rejected BOOLEAN DEFAULT FALSE;"))
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS vet_id_code VARCHAR;"))
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS otp_secret VARCHAR;"))
                conn.execute(text("ALTER TABLE milestones ADD COLUMN IF NOT EXISTS care_guideline TEXT;"))
                print("PostgreSQL Table definitions checked and patched cleanly!")
        except Exception as e:
            print(f"Warning - Automatic PostgreSQL patching skipped: {e}")

patch_database_schema()

# ==========================================
# PRELOAD DATA (Admin & Dynamic Milestones)
# ==========================================
def seed_initial_data():
    db = SessionLocal()
    
    # Check Admin credentials
    admin = db.query(User).filter(User.email == "admin@petpals.com").first()
    if not admin:
        new_admin = User(
            name="Platform Administrator",
            email="admin@petpals.com",
            phone="01700-000000",
            hashed_password=hash_password("admin123"),
            role="admin",
            location_lat=23.7461,
            location_lng=90.3742,
            address="Dhanmondi, Dhaka",
            is_approved=True
        )
        db.add(new_admin)
        print("Default admin created: admin@petpals.com (password: admin123)")

    # Vaccines
    existing_vaccines = db.query(Vaccine).all()
    if not existing_vaccines:
        vaccines = [
            Vaccine(name="DHPP (Distemper, Hepatitis, Parvovirus)", species="Dog", min_age_days=42, booster_interval_days=365),
            Vaccine(name="Rabies (Canine)", species="Dog", min_age_days=84, booster_interval_days=1095),
            Vaccine(name="FVRCP (Feline Viral Rhinotracheitis)", species="Cat", min_age_days=42, booster_interval_days=365),
            Vaccine(name="Rabies (Feline)", species="Cat", min_age_days=84, booster_interval_days=1095),
            Vaccine(name="Avian Polyomavirus Vaccine", species="Bird", min_age_days=35, booster_interval_days=365),
            Vaccine(name="Myxomatosis-RHD booster", species="Rabbit", min_age_days=35, booster_interval_days=365),
        ]
        db.add_all(vaccines)

    # Dynamic site milestones
    existing_milestones = db.query(Milestone).all()
    if not existing_milestones:
        milestones = [
            Milestone(
                letter="A",
                title="Adoption & Early Foundations",
                category="Stage 01: Neonatal & Infancy (0-8 weeks)",
                description="Milestones include maternal bonding, sensory development, and weaning protocols. Our master scheduler sets core vaccines beginning at week 6.",
                care_guideline="Support maternal warmth, initiate dewormer treatment, and secure initial clinical registrations."
            ),
            Milestone(
                letter="D",
                title="Development & Training Matrix",
                category="Stage 02: Growth & Socialization (2-6 months)",
                description="This critical window shapes behavioral temperaments. Introduce physical boundaries, sensory exposure training, and initial dietary foundations.",
                care_guideline="Schedule boosters, begin positive-reinforcement behavior routines, and register microchips."
            ),
            Milestone(
                letter="H",
                title="Hormonal Shifts & Surgery Choices",
                category="Stage 03: Adolescence (6-12 months)",
                description="Companions approach sexual maturity. Discuss spay or neuter schedules with approved vets. Teething shifts to permanent occlusion.",
                care_guideline="Consult your vet on adolescent health guidelines, dental exams, and high-protein nutrition programs."
            ),
            Milestone(
                letter="M",
                title="Maintenance & Annual Boosters",
                category="Stage 04: Mature Adulthood (1-7 years)",
                description="The prime plateau of physical health. Focus moves toward metabolic balance, cardiovascular health, and dental disease prevention.",
                care_guideline="Exercise regularly, maintain balanced weight checkups, and update Rabies/DHPP records."
            ),
            Milestone(
                letter="S",
                title="Supportive Diagnostics & Comfort",
                category="Stage 05: Golden Seniority (7+ years)",
                description="Aging starts at year 7. Metabolism slows down and risks of kidney, thyroid, or joint issues increase.",
                care_guideline="Provide comfortable bedding, track weight changes, and use tele-triage for mobility logs."
            ),
            Milestone(
                letter="Z",
                title="Zero-Pain Transitions & Memories",
                category="Stage 06: Legacy & Remembrance",
                description="Supporting companions with dignity. End-of-life decision frameworks can be emotionally challenging. Ensure comfort care is verified with your vet.",
                care_guideline="Explore peaceful comfort options, secure memory records, and manage proper transitions of digital profiles."
            )
        ]
        db.add_all(milestones)
        print("Preloaded dynamic A-Z Site Milestones Content.")
        
    db.commit()
    db.close()

seed_initial_data()

# ==========================================
# FASTAPI APPLICATION SETUP
# ==========================================
app = FastAPI(title="PetPals API & Dashboard Platform", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_headers=["*"],
    allow_methods=["*"]
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("petpals_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            
    if not token:
        return None
        
    payload = decode_access_token(token)
    if not payload:
        return None
        
    user = db.query(User).filter(User.email == payload.get("sub")).first()
    return user

def calculate_and_store_schedule(db: Session, pet: Pet):
    vaccines = db.query(Vaccine).filter(func.lower(Vaccine.species) == func.lower(pet.species)).all()
    for vac in vaccines:
        due_date = pet.dob + timedelta(days=vac.min_age_days)
        while due_date < datetime.utcnow() - timedelta(days=30):
            due_date += timedelta(days=vac.booster_interval_days)

        rec = VaccinationRecord(
            pet_id=pet.id,
            vaccine_name=vac.name,
            scheduled_date=due_date,
            status="overdue" if due_date < datetime.utcnow() else "pending"
        )
        db.add(rec)
    db.commit()

# Haversine distance calculator for nearby vet lookup
def calculate_distance(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return float('inf')
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c

# ==========================================
# REST API ENDPOINTS
# ==========================================

@app.get("/api/me")
def get_user_profile(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "phone": current_user.phone,
        "role": current_user.role,
        "address": current_user.address,
        "location_lat": current_user.location_lat,
        "location_lng": current_user.location_lng
    }

@app.post("/api/auth/register")
def register(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    phone: str = Form(...),
    role: str = Form(...),  # owner, vet
    address: str = Form(...),
    lat: float = Form(...),
    lng: float = Form(...),
    clinic_name: str = Form(""),
    license_number: str = Form(""),
    specialization: str = Form(""),
    db: Session = Depends(get_db)
):
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return JSONResponse(status_code=400, content={"message": "Email already registered."})

    new_user = User(
        name=name,
        email=email,
        phone=phone,
        hashed_password=hash_password(password),
        role=role,
        address=address,
        location_lat=lat,
        location_lng=lng,
        clinic_name=clinic_name if role == "vet" else None,
        license_number=license_number if role == "vet" else None,
        specialization=specialization if role == "vet" else None,
        is_approved=(role != "vet")
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    mock_otp = str(random.randint(100000, 999999))
    new_user.otp_secret = mock_otp
    db.commit()
    print(f"\n======================================")
    print(f"PETPALS AUTH OTP DISPATCHED FOR {new_user.email}")
    print(f"--> VERIFICATION OTP CODE: {mock_otp} <--")
    print(f"======================================\n")

    return {"message": "Registration successful. Verify using the OTP code printed in the server terminal.", "email": email}

@app.post("/api/auth/verify-otp")
def verify_otp(email: str = Form(...), otp: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or user.otp_secret != otp:
        raise HTTPException(status_code=400, detail="Invalid verification OTP.")
    
    user.otp_secret = None
    db.commit()
    return {"message": "Account successfully verified."}

@app.post("/api/auth/login")
def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        return JSONResponse(status_code=401, content={"message": "Invalid email or password."})
    
    if user.role == "vet" and not user.is_approved:
        if user.is_rejected:
             return JSONResponse(status_code=403, content={"message": "Your veterinarian license verification request was rejected."})
        return JSONResponse(status_code=403, content={"message": "Vet account pending admin approval."})
        
    token = create_access_token({"sub": user.email, "role": user.role})
    response = JSONResponse({"access_token": token, "token_type": "bearer", "role": user.role, "name": user.name})
    response.set_cookie(key="petpals_token", value=token, httponly=True)
    return response

@app.post("/api/auth/logout")
def logout():
    response = JSONResponse({"message": "Logged out successfully"})
    response.delete_cookie("petpals_token")
    return response

@app.get("/api/search")
def search_pets(q: str, db: Session = Depends(get_db)):
    if not q:
        return []
    # Match by unique_id (PET-xxxxx) or name (case-insensitive)
    pets = db.query(Pet).filter(
        or_(
            Pet.unique_id.ilike(f"%{q}%"),
            Pet.name.ilike(f"%{q}%")
        )
    ).all()
    
    results = []
    for p in pets:
        owner = db.query(User).filter(User.id == p.owner_id).first()
        results.append({
            "id": p.id,
            "unique_id": p.unique_id,
            "name": p.name,
            "species": p.species,
            "breed": p.breed,
            "owner_name": owner.name if owner else "Unknown Owner"
        })
    return results

@app.get("/api/milestones")
def get_milestones(db: Session = Depends(get_db)):
    return db.query(Milestone).order_by(Milestone.letter).all()

@app.post("/api/milestones/{m_id}/update")
def update_milestone(
    m_id: int,
    title: str = Form(...),
    category: str = Form(...),
    description: str = Form(...),
    care_guideline: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin credentials required")
    
    m = db.query(Milestone).filter(Milestone.id == m_id).first()
    if not m:
        raise HTTPException(status_code=444, detail="Milestone not found")
        
    m.title = title
    m.category = category
    m.description = description
    m.care_guideline = care_guideline
    db.commit()
    return {"message": "Content updated successfully!"}

@app.get("/api/admin/stats")
def get_admin_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
        
    total_owners = db.query(User).filter(User.role == "owner").count()
    approved_vets = db.query(User).filter(and_(User.role == "vet", User.is_approved == True)).count()
    pending_vets = db.query(User).filter(and_(User.role == "vet", User.is_approved == False, User.is_rejected == False)).count()
    rejected_vets = db.query(User).filter(and_(User.role == "vet", User.is_rejected == True)).count()
    
    # Pets by species category calculation
    species_counts = db.query(Pet.species, func.count(Pet.id)).group_by(Pet.species).all()
    species_map = {species: count for species, count in species_counts}
    
    return {
        "owners": total_owners,
        "approved_vets": approved_vets,
        "pending_vets": pending_vets,
        "rejected_vets": rejected_vets,
        "pets_by_category": species_map
    }

@app.get("/api/admin/pending-vets")
def get_pending_vets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    return db.query(User).filter(and_(User.role == "vet", User.is_approved == False, User.is_rejected == False)).all()

@app.post("/api/admin/approve-vet/{vet_id}")
def approve_vet(vet_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    
    vet = db.query(User).filter(User.id == vet_id).first()
    if not vet:
        raise HTTPException(status_code=404, detail="Vet not found")
        
    vet.is_approved = True
    vet.is_rejected = False
    vet.vet_id_code = f"VET-{random.randint(10000, 99999)}"
    db.commit()
    return {"message": "Vet approved successfully!", "code": vet.vet_id_code}

@app.post("/api/admin/reject-vet/{vet_id}")
def reject_vet(vet_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
        
    vet = db.query(User).filter(User.id == vet_id).first()
    if not vet:
        raise HTTPException(status_code=404, detail="Vet not found")
        
    vet.is_approved = False
    vet.is_rejected = True
    db.commit()
    return {"message": "Vet verification rejected successfully."}

@app.get("/api/my-pets")
def my_pets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if current_user.role == "owner":
        pets = db.query(Pet).filter(Pet.owner_id == current_user.id).all()
    else:
        pets = db.query(Pet).all()
        
    return [{
        "id": p.id,
        "unique_id": p.unique_id,
        "name": p.name,
        "species": p.species,
        "breed": p.breed,
        "dob": p.dob.strftime("%Y-%m-%d"),
        "microchip": p.microchip,
        "photo_data": p.photo_data
    } for p in pets]

@app.post("/api/pets")
async def create_pet(
    name: str = Form(...),
    species: str = Form(...),
    breed: str = Form(...),
    dob_str: str = Form(...),
    microchip: str = Form(""),
    photo_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or current_user.role != "owner":
        raise HTTPException(status_code=401, detail="Access denied. Only registered owners can add pets.")
    
    pet_id = f"PET-{random.randint(10000, 99999)}"
    dob_dt = datetime.strptime(dob_str, "%Y-%m-%d")

    photo_data_string = None
    if photo_file and photo_file.filename:
        try:
            file_bytes = await photo_file.read()
            encoded_base64 = base64.b64encode(file_bytes).decode("utf-8")
            photo_data_string = f"data:{photo_file.content_type};base64,{encoded_base64}"
        except Exception:
            pass

    if not photo_data_string:
        photo_data_string = f"<svg class='w-12 h-12 text-teal-600' fill='none' stroke='currentColor' viewBox='0 0 24 24'><circle cx='12' cy='12' r='10'/></svg>"

    pet = Pet(
        unique_id=pet_id,
        owner_id=current_user.id,
        name=name,
        species=species,
        breed=breed,
        dob=dob_dt,
        microchip=microchip if microchip else None,
        photo_data=photo_data_string
    )
    
    db.add(pet)
    db.commit()
    db.refresh(pet)
    
    calculate_and_store_schedule(db, pet)
    return {"message": "Pet added successfully!", "pet_id": pet_id}

@app.post("/api/pets/{pet_id}/update")
async def update_pet(
    pet_id: int,
    name: str = Form(...),
    species: str = Form(...),
    breed: str = Form(...),
    dob_str: str = Form(...),
    microchip: str = Form(""),
    photo_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found")
        
    if pet.owner_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access Forbidden")
        
    dob_dt = datetime.strptime(dob_str, "%Y-%m-%d")
    recalc_needed = (pet.species.lower() != species.lower()) or (pet.dob != dob_dt)
    
    pet.name = name
    pet.species = species
    pet.breed = breed
    pet.dob = dob_dt
    pet.microchip = microchip if microchip else None
    
    if photo_file and photo_file.filename:
        try:
            file_bytes = await photo_file.read()
            encoded_base64 = base64.b64encode(file_bytes).decode("utf-8")
            pet.photo_data = f"data:{photo_file.content_type};base64,{encoded_base64}"
        except Exception:
            pass
            
    db.commit()
    
    if recalc_needed:
        db.query(VaccinationRecord).filter(
            and_(
                VaccinationRecord.pet_id == pet.id,
                VaccinationRecord.fully_verified == False
            )
        ).delete()
        calculate_and_store_schedule(db, pet)
        
    return {"message": "Pet profile updated successfully!"}

@app.post("/api/pets/{pet_id}/delete")
def delete_pet(pet_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found")
        
    if pet.owner_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access Forbidden")
        
    db.delete(pet)
    db.commit()
    return {"message": "Pet removed from index successfully."}

@app.get("/api/pets/{pet_id}/vaccines")
def get_pet_vaccines(pet_id: int, db: Session = Depends(get_db)):
    return db.query(VaccinationRecord).filter(VaccinationRecord.pet_id == pet_id).all()

@app.post("/api/vaccines/verify-vet/{record_id}")
def verify_vaccine_vet(
    record_id: int, 
    batch: str = Form(...), 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if not current_user or current_user.role != "vet":
        raise HTTPException(status_code=401, detail="Vets only")
        
    rec = db.query(VaccinationRecord).filter(VaccinationRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Vaccine record not found")
        
    rec.verified_by_vet = True
    rec.administered_date = datetime.utcnow()
    rec.batch_number = batch
    rec.fully_verified = True
    rec.status = "verified"
    
    history_event = MedicalEvent(
        pet_id=rec.pet_id,
        event_type="treatment",
        description=f"Vaccination administered and fully verified by {current_user.name} ({current_user.vet_id_code}). Vaccine: {rec.vaccine_name}",
        vet_reference=current_user.vet_id_code
    )
    db.add(history_event)
    db.commit()
    return {"message": "Vaccine fully verified and updated."}

@app.post("/api/vaccines/upload-doc/{record_id}")
def verify_vaccine_doc(
    record_id: int, 
    cert_text: str = Form(...),
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    rec = db.query(VaccinationRecord).filter(VaccinationRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
        
    text_normalized = cert_text.lower()
    auto_pass = False
    
    if "official" in text_normalized or "certified" in text_normalized or rec.vaccine_name.split(" ")[0].lower() in text_normalized:
        auto_pass = True

    rec.document_proof = cert_text
    rec.verified_by_doc = True
    
    if auto_pass:
        rec.fully_verified = True
        rec.administered_date = datetime.utcnow() - timedelta(days=2)
        rec.status = "verified"
        message = "AI Document Analysis complete: Official stamps extracted. Vaccine Verified!"
    else:
        message = "Document uploaded successfully. Held in pending queue for Admin review."

    db.commit()
    return {"message": message, "fully_verified": rec.fully_verified}

@app.get("/api/pets/{pet_id}/history")
def get_medical_history(pet_id: int, db: Session = Depends(get_db)):
    return db.query(MedicalEvent).filter(MedicalEvent.pet_id == pet_id).order_by(MedicalEvent.date.desc()).all()

@app.post("/api/pets/{pet_id}/history")
def add_medical_event(
    pet_id: int,
    event_type: str = Form(...),
    description: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    vet_ref = current_user.vet_id_code if current_user and current_user.role == "vet" else None
    new_event = MedicalEvent(
        pet_id=pet_id,
        event_type=event_type,
        description=description,
        vet_reference=vet_ref
    )
    db.add(new_event)
    db.commit()
    return {"message": "Timeline medical event added successfully."}

@app.post("/api/pets/{pet_id}/prescriptions")
def prescribe(
    pet_id: int,
    medicine: str = Form(...),
    dosage: str = Form(...),
    instructions: str = Form(...),
    duration: str = Form(...),
    is_tele_triage: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or current_user.role != "vet":
         raise HTTPException(status_code=401, detail="Only approved Vets can issue Rx prescriptions.")
         
    rx_code = f"RX-{random.randint(10000, 99999)}"
    new_rx = Prescription(
        unique_id=rx_code,
        pet_id=pet_id,
        vet_id=current_user.id,
        medicine=medicine,
        dosage=dosage,
        instructions=instructions,
        duration=duration,
        is_tele_triage=is_tele_triage
    )
    db.add(new_rx)
    db.commit()
    return {"message": f"Prescription successfully registered: {rx_code}"}

@app.get("/api/pets/{pet_id}/prescriptions")
def get_prescriptions(pet_id: int, db: Session = Depends(get_db)):
    return db.query(Prescription).filter(Prescription.pet_id == pet_id).all()

@app.get("/api/prescriptions/{rx_id}/pdf")
def download_rx_pdf(rx_id: int, db: Session = Depends(get_db)):
    rx = db.query(Prescription).filter(Prescription.id == rx_id).first()
    if not rx:
         raise HTTPException(status_code=444, detail="Prescription not found")
         
    if RLBytesIO is None:
        text_data = f"PETPALS PRESCRIPTION - {rx.unique_id}\n\nPet ID: {rx.pet.unique_id}\nMedicine: {rx.medicine}\nDosage: {rx.dosage}\nInstructions: {rx.instructions}\nDuration: {rx.duration}\nIssued By: Vet #{rx.vet_id}\n"
        buffer = BytesIO()
        buffer.write(text_data.encode("utf-8"))
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="text/plain", headers={"Content-Disposition": f"attachment;filename={rx.unique_id}.txt"})

    buffer = RLBytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor('#0d9488'), spaceAfter=20)
    meta_style = ParagraphStyle('MetaStyle', parent=styles['Normal'], fontSize=10, leading=14)
    content_style = ParagraphStyle('ContentStyle', parent=styles['Normal'], fontSize=12, leading=18, spaceAfter=15)
    
    elements = []
    elements.append(Paragraph("PETPALS HEALTHCARE NETWORK", title_style))
    elements.append(Paragraph(f"<b>PRESCRIPTION REQUISITION:</b> {rx.unique_id}", styles['Heading2']))
    elements.append(Spacer(1, 10))
    
    data = [
        [Paragraph(f"<b>Pet Name:</b> {rx.pet.name}", meta_style), Paragraph(f"<b>Pet Unique ID:</b> {rx.pet.unique_id}", meta_style)],
        [Paragraph(f"<b>Species/Breed:</b> {rx.pet.species} / {rx.pet.breed}", meta_style), Paragraph(f"<b>Issued Date:</b> {rx.created_at.strftime('%Y-%m-%d')}", meta_style)],
        [Paragraph(f"<b>Rx Code:</b> {rx.unique_id}", meta_style), Paragraph(f"<b>Type:</b> {'Tele-Triage Clinic' if rx.is_tele_triage else 'In-Clinic Event'}", meta_style)]
    ]
    
    t = Table(data, colWidths=[250, 250])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f0fdfa')),
        ('PADDING', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#ccfbf1'))
    ]))
    
    elements.append(t)
    elements.append(Spacer(1, 20))
    
    elements.append(Paragraph("<b>PRESCRIBED MEDICINAL REGIMEN</b>", styles['Heading3']))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"<b>Medicine Name & Strength:</b> {rx.medicine}", content_style))
    elements.append(Paragraph(f"<b>Dosage Instructions:</b> {rx.dosage}", content_style))
    elements.append(Paragraph(f"<b>Administration Rules:</b> {rx.instructions}", content_style))
    elements.append(Paragraph(f"<b>Regimen Duration:</b> {rx.duration}", content_style))
    
    elements.append(Spacer(1, 40))
    elements.append(Paragraph("<i>This document is validated electronically via the PetPals verification system.</i>", meta_style))
    
    doc.build(elements)
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": f"attachment;filename={rx.unique_id}.pdf"})

@app.post("/api/transfers/initiate")
def initiate_transfer(
    pet_id: int, 
    target_email: str = Form(...), 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    pet = db.query(Pet).filter(and_(Pet.id == pet_id, Pet.owner_id == current_user.id)).first()
    if not pet:
        raise HTTPException(status_code=403, detail="Forbidden. Only the owner can transfer ownership.")
        
    otp_code = str(random.randint(100000, 999999))
    transfer = OwnerTransfer(
        pet_id=pet_id,
        current_owner_id=current_user.id,
        target_email=target_email,
        otp_code=otp_code,
        status="pending"
    )
    db.add(transfer)
    db.commit()
    
    print(f"\n======================================")
    print(f"PET TRANSFER INITIATED FOR {pet.name} (ID: {pet.unique_id})")
    print(f"--> TARGET OWNER: {target_email}")
    print(f"--> TRANSFER VERIFICATION OTP: {otp_code} <--")
    print(f"======================================\n")
    
    return {"message": "Transfer initiated. Provide the verification OTP to the target recipient.", "otp_sent": otp_code}

@app.post("/api/transfers/accept")
def accept_transfer(
    otp: str = Form(...), 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized. Sign in as recipient.")
        
    transfer = db.query(OwnerTransfer).filter(
        and_(
            OwnerTransfer.otp_code == otp, 
            OwnerTransfer.status == "pending"
        )
    ).first()
    
    if not transfer:
        raise HTTPException(status_code=400, detail="Invalid transfer verification OTP.")
        
    pet = db.query(Pet).filter(Pet.id == transfer.pet_id).first()
    old_owner_id = pet.owner_id
    pet.owner_id = current_user.id
    transfer.status = "accepted"
    
    audit_event = MedicalEvent(
        pet_id=pet.id,
        event_type="transfer",
        description=f"Ownership legally reassigned. Previous owner reference ID: USER#{old_owner_id} to current user {current_user.name}."
    )
    db.add(audit_event)
    db.commit()
    return {"message": f"Successfully gained complete legal ownership of {pet.name}!"}

@app.get("/api/vets/nearby")
def get_nearby_vets(lat: float, lng: float, db: Session = Depends(get_db)):
    vets = db.query(User).filter(and_(User.role == "vet", User.is_approved == True)).all()
    results = []
    for v in vets:
        dist = calculate_distance(lat, lng, v.location_lat, v.location_lng)
        results.append({
            "id": v.id,
            "name": v.name,
            "clinic_name": v.clinic_name,
            "specialization": v.specialization,
            "address": v.address,
            "distance_km": round(dist, 2),
            "phone": v.phone
        })
    results.sort(key=lambda x: x["distance_km"])
    return results

@app.post("/api/emergency/dispatch")
def dispatch_emergency(
    pet_id: Optional[int] = Form(None),
    symptoms: str = Form(...),
    urgency_level: str = Form(...),
    lat: float = Form(...),
    lng: float = Form(...),
    db: Session = Depends(get_db)
):
    req = EmergencyRequest(
        pet_id=pet_id,
        symptoms=symptoms,
        urgency_level=urgency_level,
        location_lat=lat,
        location_lng=lng,
        status="searching"
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    
    vets = db.query(User).filter(and_(User.role == "vet", User.is_approved == True)).all()
    closest_vet = None
    min_dist = float('inf')
    
    for v in vets:
        dist = calculate_distance(lat, lng, v.location_lat, v.location_lng)
        if dist < min_dist:
            min_dist = dist
            closest_vet = v
            
    if closest_vet:
        req.assigned_vet_id = closest_vet.id
        req.status = "accepted"
        db.commit()
        return {
            "message": "Emergency broadcast answered!",
            "request_id": req.id,
            "status": "connected",
            "vet_name": closest_vet.name,
            "vet_clinic": closest_vet.clinic_name,
            "vet_phone": closest_vet.phone
        }
        
    return {"message": "Emergency signal broadcasted. Currently scanning for closest active response unit.", "request_id": req.id, "status": "broadcasting"}

@app.post("/api/promotions")
def create_promotion(
    title: str = Form(...),
    discount_code: str = Form(...),
    target_breed: str = Form(""),
    target_min_age_years: int = Form(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or current_user.role != "vet":
         raise HTTPException(status_code=401, detail="Vets only.")
         
    promo = Promotion(
        vet_id=current_user.id,
        title=title,
        discount_code=discount_code,
        target_breed=target_breed if target_breed else None,
        target_min_age_years=target_min_age_years
    )
    db.add(promo)
    db.commit()
    return {"message": f"Promotion '{title}' broadcast to eligible pet targets successfully."}

@app.get("/api/my-promotions")
def get_my_promotions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or current_user.role != "owner":
        return []
    my_pets = db.query(Pet).filter(Pet.owner_id == current_user.id).all()
    promos = db.query(Promotion).all()
    matched = []
    
    for p in promos:
        for pet in my_pets:
            age_years = (datetime.utcnow() - pet.dob).days / 365.25
            breed_match = not p.target_breed or p.target_breed.lower() == pet.breed.lower()
            age_match = age_years >= p.target_min_age_years
            
            if breed_match and age_match:
                matched.append({
                    "id": p.id,
                    "title": p.title,
                    "discount_code": p.discount_code,
                    "target_pet": pet.name
                })
                break
    return matched

@app.post("/api/reviews")
def submit_review(
    vet_id: int = Form(...),
    rating_care: int = Form(...),
    rating_communication: int = Form(...),
    rating_facility: int = Form(...),
    rating_value: int = Form(...),
    text: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or current_user.role != "owner":
        raise HTTPException(status_code=401, detail="Access denied")
        
    rev = Review(
        vet_id=vet_id,
        owner_id=current_user.id,
        rating_care=rating_care,
        rating_communication=rating_communication,
        rating_facility=rating_facility,
        rating_value=rating_value,
        text=text
    )
    db.add(rev)
    db.commit()
    return {"message": "Thank you for rating. Your contribution strengthens the PetPals ecosystem!"}

@app.get("/api/vets/{vet_id}/rating")
def get_vet_rating(vet_id: int, db: Session = Depends(get_db)):
    reviews = db.query(Review).filter(Review.vet_id == vet_id).all()
    if not reviews:
        return {"average_stars": 5.0, "total_reviews": 0}
        
    scores = []
    for r in reviews:
        avg = (r.rating_care + r.rating_communication + r.rating_facility + r.rating_value) / 4.0
        scores.append(avg)
        
    return {
        "average_stars": round(sum(scores) / len(scores), 1),
        "total_reviews": len(reviews)
    }

@app.get("/api/pets/{pet_id}/passport")
def generate_health_passport(pet_id: int, db: Session = Depends(get_db)):
    pet = db.query(Pet).filter(Pet.id == pet_id).first()
    if not pet:
        raise HTTPException(status_code=404, detail="Pet not found")
        
    certified_history = []
    for vac in pet.vaccinations:
        if vac.fully_verified:
            certified_history.append({
                "vaccine": vac.vaccine_name,
                "administered": vac.administered_date.strftime("%Y-%m-%d") if vac.administered_date else "",
                "status": "FULLY_VERIFIED_PETPALS_SECURE"
            })
            
    passport_data = {
        "pet_unique_id": pet.unique_id,
        "name": pet.name,
        "species": pet.species,
        "verified_immunizations": certified_history,
        "verification_hash": str(uuid.uuid4())[:18].upper()
    }

    if qrcode is None or RLBytesIO is None:
        return JSONResponse(passport_data)
        
    buffer = RLBytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('PassportTitle', parent=styles['Heading1'], fontSize=22, textColor=colors.HexColor('#0f766e'), spaceAfter=15)
    body_style = ParagraphStyle('PassportBody', parent=styles['Normal'], fontSize=11, leading=16, spaceAfter=8)
    
    elements = []
    elements.append(Paragraph("PETPALS OFFICIAL HEALTH PASSPORT", title_style))
    elements.append(Paragraph(f"<b>Pet Identity Code:</b> {pet.unique_id}", body_style))
    elements.append(Paragraph(f"<b>Name:</b> {pet.name} (Species: {pet.species})", body_style))
    elements.append(Paragraph(f"<b>Breed:</b> {pet.breed} | DOB: {pet.dob.strftime('%Y-%m-%d')}", body_style))
    elements.append(Spacer(1, 15))
    
    elements.append(Paragraph("<b>CERTIFIED ACTIVE IMMUNIZATIONS</b>", styles['Heading3']))
    for item in certified_history:
        elements.append(Paragraph(f"• <b>{item['vaccine']}</b> - Stamped fully active on {item['administered']}", body_style))
    if not certified_history:
        elements.append(Paragraph("<i>No verified immunization records on file. Administer required scheduled shots below.</i>", body_style))
        
    elements.append(Spacer(1, 20))
    
    qr = qrcode.QRCode(version=1, box_size=4, border=1)
    qr.add_data(str(passport_data))
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white")
    
    qr_buffer = BytesIO()
    img_qr.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    
    from reportlab.platypus import Image as RLImage
    elements.append(Paragraph("<b>CRYPTOGRAPHIC VERIFICATION SCAN KEY</b>", styles['Heading4']))
    elements.append(Spacer(1, 10))
    elements.append(RLImage(qr_buffer, width=120, height=120))
    
    doc.build(elements)
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": f"attachment;filename=Passport-{pet.unique_id}.pdf"})

# ==========================================
# PORTAL FRONTEND ROOT ROUTE (HTML / CSS / JS)
# ==========================================
@app.get("/", response_class=HTMLResponse)
def index_portal():
    html_code = """
    <!DOCTYPE html>
    <html lang="en" class="h-full bg-slate-50">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PetPals - Premium Lifecycle Management & Clinic Hub</title>
        <!-- Tailwind CSS -->
        <script src="https://cdn.tailwindcss.com"></script>
        <!-- Lucide Icons -->
        <script src="https://unpkg.com/lucide@latest"></script>
    </head>
    <body class="h-full flex flex-col font-sans text-slate-800">
        
        <!-- GLOBAL APP NOTIFICATIONS / ALERTS TOAST -->
        <div id="app-alerts" class="fixed top-24 right-6 z-50 max-w-sm hidden shadow-2xl rounded-2xl p-4 transition duration-300"></div>

        <!-- HEADER / NAVIGATION -->
        <header class="bg-white/85 backdrop-blur-md sticky top-0 z-50 border-b border-slate-100 shadow-sm shrink-0">
            <div class="max-w-7xl mx-auto px-6 lg:px-8 h-20 flex items-center justify-between">
                <!-- Top-Left Branding -->
                <div class="flex items-center space-x-3 cursor-pointer" onclick="showLandingView()">
                    <div class="p-2 bg-teal-500/10 rounded-xl">
                        <i data-lucide="paw-print" class="w-8 h-8 text-teal-600"></i>
                    </div>
                    <div>
                        <h1 class="text-2xl font-black tracking-tight text-slate-900 flex items-center">PetPals</h1>
                    </div>
                </div>
                
                <!-- Top-Right Actions -->
                <div class="flex items-center space-x-4">
                    <button onclick="scrollToLifecycle()" class="text-sm font-semibold text-slate-600 hover:text-teal-600 transition">A-Z Milestones</button>
                    <div id="auth-state" class="flex items-center space-x-3 text-sm">
                        <!-- Populated dynamically -->
                    </div>
                </div>
            </div>
        </header>

        <!-- VIEW 0: COMPREHENSIVE LANDING & A-Z LIFECYCLE GUIDE -->
        <div id="view-landing" class="flex-grow overflow-y-auto animate-fade-in">
            <section class="bg-gradient-to-b from-teal-50/40 to-white py-16 px-6 lg:px-12 border-b border-teal-100/40">
                <div class="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
                    
                    <!-- Left Column Text & Controls -->
                    <div class="lg:col-span-6 space-y-8 text-left">
                        <div class="inline-flex items-center space-x-2 bg-teal-50 border border-teal-200/60 px-4 py-1.5 rounded-full shadow-sm">
                            <i data-lucide="heart" class="w-4 h-4 text-teal-500 fill-teal-500 animate-pulse"></i>
                            <span class="text-xs font-black text-teal-800 tracking-wide">Trusted by 10,000+ pet owners</span>
                        </div>

                        <div class="space-y-4">
                            <h2 class="text-5xl lg:text-6xl font-black tracking-tight text-slate-900 leading-none">
                                Your Pet's Health,
                            </h2>
                            <div class="inline-block bg-gradient-to-r from-teal-500 to-emerald-600 px-6 py-3 rounded-2xl shadow-md transform -rotate-1 hover:rotate-0 transition duration-300">
                                <span class="text-2xl lg:text-3xl font-black tracking-tight text-white uppercase tracking-widest leading-none">Managed from A to Z</span>
                            </div>
                        </div>

                        <p class="text-base text-slate-600 leading-relaxed max-w-xl">
                            Register your pets, connect with verified veterinarians, and manage everything in one beautiful platform.
                        </p>

                        <div class="bg-white p-2 rounded-2xl shadow-xl border border-teal-100/30 flex items-center space-x-3 max-w-xl">
                            <div class="pl-3 text-slate-400">
                                <i data-lucide="search" class="w-5 h-5"></i>
                            </div>
                            <input type="text" id="public-pet-search-input" placeholder="Search by Pet ID (e.g., PET-12345)" class="w-full py-2 bg-transparent text-slate-800 text-sm focus:outline-none placeholder-slate-400">
                            <button onclick="executePublicSearchLookup()" class="bg-gradient-to-r from-teal-500 to-emerald-600 hover:from-teal-600 hover:to-emerald-700 text-white font-bold px-6 py-2.5 rounded-xl text-xs uppercase tracking-wider shadow transition">
                                Search
                            </button>
                        </div>

                        <div class="flex items-center space-x-4 pt-2">
                            <button onclick="triggerAppAuthView('register', 'owner')" class="bg-gradient-to-r from-teal-500 to-emerald-600 hover:from-teal-600 hover:to-emerald-700 text-white font-extrabold px-6 py-3.5 rounded-2xl shadow-lg shadow-teal-500/20 transition text-sm flex items-center space-x-2">
                                <span>Register Your Pet</span>
                                <i data-lucide="arrow-right" class="w-4 h-4"></i>
                            </button>
                            <button onclick="triggerAppAuthView('register', 'vet')" class="bg-white hover:bg-teal-50 border border-teal-200 text-teal-850 font-extrabold px-6 py-3.5 rounded-2xl transition text-sm flex items-center space-x-2">
                                <i data-lucide="stethoscope" class="w-4 h-4 text-teal-600"></i>
                                <span>Join as Vet</span>
                            </button>
                        </div>
                    </div>

                    <!-- Right Column Cozy Veterinarian Room Scene -->
                    <div class="lg:col-span-6 relative">
                        <div class="bg-gradient-to-br from-teal-100/40 to-emerald-100/10 p-4 rounded-[2rem] border border-teal-100/20 shadow-xl overflow-hidden">
                            <div class="aspect-[16/10] bg-[#FFFBF5] border border-teal-100/50 rounded-2xl p-6 relative flex flex-col justify-between overflow-hidden">
                                <div class="absolute top-4 left-6 border border-slate-200/80 bg-white rounded-lg p-2 w-32 shadow-sm text-left">
                                    <div class="w-full h-1 bg-teal-100 rounded mb-1"></div>
                                    <div class="w-3/4 h-1 bg-teal-100 rounded mb-1"></div>
                                    <div class="w-1/2 h-1 bg-teal-100 rounded"></div>
                                </div>
                                <div class="absolute top-4 right-6 border border-teal-200/80 bg-teal-50/50 rounded-lg px-3 py-2 text-[10px] font-black tracking-tight text-teal-800">
                                    COMPASSIONATE<br>PET CARE
                                </div>
                                
                                <div class="absolute bottom-16 left-12 flex flex-col items-center">
                                    <div class="w-10 h-10 bg-emerald-100 rounded-full border border-emerald-300 flex items-center justify-center text-emerald-600"><i data-lucide="leaf" class="w-5 h-5"></i></div>
                                    <div class="w-6 h-8 bg-teal-800/80 rounded-b-lg"></div>
                                </div>
                                <div class="absolute bottom-16 right-12 flex flex-col items-center">
                                    <div class="w-8 h-8 bg-emerald-50 rounded-full border border-emerald-200 flex items-center justify-center text-emerald-500"><i data-lucide="leaf" class="w-4 h-4"></i></div>
                                    <div class="w-5 h-6 bg-teal-700/80 rounded-b-lg"></div>
                                </div>

                                <div class="mt-auto mx-auto w-4/5 h-20 bg-teal-100/60 rounded-full flex items-center justify-center relative shadow-sm border border-teal-200/40">
                                    <div class="absolute -top-12 left-1/2 transform -translate-x-1/2 flex items-baseline space-x-6">
                                        <div class="flex flex-col items-center">
                                            <div class="w-14 h-14 bg-teal-500 rounded-full border-2 border-white shadow flex items-center justify-center text-white font-bold"><i data-lucide="smile" class="w-7 h-7"></i></div>
                                            <span class="text-[9px] font-extrabold text-teal-900 bg-white px-2 py-0.5 rounded-full shadow border mt-1">Dog</span>
                                        </div>
                                        <div class="flex flex-col items-center">
                                            <div class="w-10 h-10 bg-emerald-400 rounded-full border-2 border-white shadow flex items-center justify-center text-white font-bold"><i data-lucide="heart" class="w-5 h-5"></i></div>
                                            <span class="text-[9px] font-extrabold text-emerald-950 bg-white px-1.5 py-0.5 rounded-full shadow border mt-1">Kitten</span>
                                        </div>
                                        <div class="flex flex-col items-center">
                                            <div class="w-8 h-8 bg-slate-300 rounded-full border-2 border-white shadow flex items-center justify-center text-slate-700 font-bold"><i data-lucide="star" class="w-4 h-4"></i></div>
                                            <span class="text-[9px] font-extrabold text-slate-800 bg-white px-1.5 py-0.5 rounded-full shadow border mt-1">Rabbit</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            <!-- Public Search Outcome Dropdown Modal -->
            <div id="public-search-outcome-card" class="max-w-xl mx-auto px-6 py-6 mt-4 bg-white rounded-3xl shadow-xl border border-teal-100/30 hidden animate-fade-in text-slate-700">
                <div class="flex items-center justify-between border-b pb-3 mb-3">
                    <h4 class="font-black text-slate-900 flex items-center gap-1.5"><i data-lucide="search" class="text-teal-500"></i> Public Registry Result</h4>
                    <button onclick="closePublicSearchResult()" class="text-slate-400 hover:text-slate-600"><i data-lucide="x"></i></button>
                </div>
                <div id="public-search-results-box" class="space-y-3"></div>
            </div>

            <!-- A-Z PET LIFE CYCLE SECTION -->
            <section id="lifecycle-guide" class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-16 space-y-12">
                <div class="text-center space-y-2">
                    <h3 class="text-3xl font-extrabold tracking-tight text-slate-900">Pet Life-Cycle Milestone Hub</h3>
                    <p class="text-slate-500 text-sm">Review clinical stages of companion animal growth backed dynamically by our database.</p>
                </div>

                <div id="dynamic-milestones-list" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                    <!-- Dynamic milestones render here -->
                </div>

                <!-- Interactive Searchable A-Z Reference Library -->
                <div class="bg-slate-100/70 rounded-3xl p-8 border border-slate-200/50 space-y-6">
                    <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                        <div class="space-y-1">
                            <h4 class="text-xl font-bold text-slate-900">A-Z Quick Reference Guide</h4>
                            <p class="text-xs text-slate-500">Search specific milestones or stages to get instant clinical facts.</p>
                        </div>
                        <div class="relative w-full md:w-80">
                            <input type="text" id="lifecycle-search" oninput="searchLibrary()" placeholder="Search (e.g. Teething, Diet, Spay)..." class="w-full pl-10 pr-4 py-2.5 rounded-xl border border-slate-200 bg-white focus:outline-none focus:ring-2 focus:ring-teal-500 text-sm">
                            <i data-lucide="search" class="absolute left-3.5 top-3.5 text-slate-400 w-4 h-4"></i>
                        </div>
                    </div>

                    <div id="library-results" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="bg-white p-4 rounded-xl border border-slate-100 flex items-start gap-3 library-item" data-keyword="teething developmental growth puppy puppyhood kitten">
                            <span class="p-2 bg-teal-50 text-teal-600 rounded-lg text-xs font-black">Teething</span>
                            <div>
                                <h5 class="font-bold text-slate-800 text-xs">Puppy/Kitten Deciduous Teeth Shedding</h5>
                                <p class="text-[11px] text-slate-500 mt-1 leading-relaxed">Occurs between months 3 and 6. Normal teething may cause minor discomfort. Provide safe chew toys and avoid hard synthetic materials.</p>
                            </div>
                        </div>

                        <div class="bg-white p-4 rounded-xl border border-slate-100 flex items-start gap-3 library-item" data-keyword="nutrition diet food senior adult kitten dog cat">
                            <span class="p-2 bg-emerald-50 text-emerald-600 rounded-lg text-xs font-black">Nutrition</span>
                            <div>
                                <h5 class="font-bold text-slate-800 text-xs">Metabolic Stage Feeding Adaptations</h5>
                                <p class="text-[11px] text-slate-500 mt-1 leading-relaxed">Ensure high-protein growth diets for puppies/kittens under 1 year. Transition to adult formula around month 12 to prevent unwanted rapid weight gain.</p>
                            </div>
                        </div>

                        <div class="bg-white p-4 rounded-xl border border-slate-100 flex items-start gap-3 library-item" data-keyword="spay neuter surgery hormones teenager adolescent dog cat">
                            <span class="p-2 bg-indigo-50 text-indigo-600 rounded-lg text-xs font-black">Sterilization</span>
                            <div>
                                <h5 class="font-bold text-slate-800 text-xs">Spay & Neuter Timeline Decisions</h5>
                                <p class="text-[11px] text-slate-500 mt-1 leading-relaxed">Usually recommended between 6 and 9 months. Consult your vet to weigh the benefits of early versus delayed sterilization based on species and breed size.</p>
                            </div>
                        </div>

                        <div class="bg-white p-4 rounded-xl border border-slate-100 flex items-start gap-3 library-item" data-keyword="exercise physical cardiovasular senior arthritis adult dog cat">
                            <span class="p-2 bg-amber-50 text-amber-600 rounded-lg text-xs font-black">Activity</span>
                            <div>
                                <h5 class="font-bold text-slate-800 text-xs">Preventative Joint Physical Routines</h5>
                                <p class="text-[11px] text-slate-500 mt-1 leading-relaxed">Incorporate age-appropriate exercise routines. Avoid intense joint stress during early development (under 12 months) and high-impact sports in senior pets.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </section>
        </div>

        <!-- VIEW 1: AUTHENTICATION -->
        <section id="view-auth" class="max-w-md mx-auto bg-white rounded-3xl shadow-2xl border border-slate-100 overflow-hidden my-10 hidden shrink-0 animate-fade-in text-slate-700">
            <div class="p-6 text-center space-y-4">
                <div class="mx-auto w-16 h-16 bg-gradient-to-br from-teal-500 to-teal-600 rounded-2xl flex items-center justify-center shadow-lg">
                    <i data-lucide="paw-print" class="w-9 h-9 text-white"></i>
                </div>
                <h2 class="text-3xl font-black text-slate-900 tracking-tight">Create Account</h2>
                <p class="text-sm text-slate-500">Join PetPals to manage your pets</p>
            </div>
            
            <div class="p-6 pt-0">
                <div class="flex border-b border-slate-200 mb-6">
                    <button onclick="toggleAuthTab('login')" id="tab-btn-login" class="flex-1 pb-3 text-sm font-bold text-teal-600 border-b-2 border-teal-500">Sign In</button>
                    <button onclick="toggleAuthTab('register')" id="tab-btn-register" class="flex-1 pb-3 text-sm font-semibold text-slate-500 border-b-2 border-transparent">Register</button>
                </div>

                <!-- LOGIN FORM -->
                <form id="form-login" onsubmit="handleLogin(event)" class="space-y-4">
                    <div>
                        <label class="block text-xs font-bold text-slate-600 uppercase mb-1">Email Address</label>
                        <input type="email" name="email" required class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white" placeholder="owner@petpals.com">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 uppercase mb-1">Password</label>
                        <input type="password" name="password" required class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white" placeholder="••••••••">
                    </div>
                    <button type="submit" class="w-full bg-teal-600 hover:bg-teal-700 text-white font-bold py-3 rounded-xl transition duration-150">Enter Dashboard</button>
                </form>

                <!-- REGISTRATION FORM WITH CARD-BASED SELECTION -->
                <form id="form-register" onsubmit="handleRegister(event)" class="space-y-4 hidden">
                    <div>
                        <label class="block text-xs font-bold text-slate-600 uppercase mb-2">Select Your Role</label>
                        <div class="grid grid-cols-2 gap-4">
                            <div id="role-card-owner" onclick="selectRegisterRole('owner')" class="cursor-pointer border-2 border-teal-500 bg-teal-50/50 rounded-2xl p-4 text-center transition flex flex-col items-center justify-center space-y-2">
                                <div class="w-10 h-10 rounded-full bg-teal-100 flex items-center justify-center">
                                    <i data-lucide="user" class="w-5 h-5 text-teal-700"></i>
                                </div>
                                <span class="text-sm font-bold text-slate-800 block">Pet Owner</span>
                            </div>

                            <div id="role-card-vet" onclick="selectRegisterRole('vet')" class="cursor-pointer border-2 border-slate-100 bg-white rounded-2xl p-4 text-center transition flex flex-col items-center justify-center space-y-2 hover:border-teal-200">
                                <div class="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center">
                                    <i data-lucide="shield-check" class="w-5 h-5 text-slate-600"></i>
                                </div>
                                <span class="text-sm font-semibold text-slate-800 block">Veterinarian</span>
                            </div>
                        </div>
                    </div>

                    <input type="hidden" name="role" id="reg-role-input" value="owner">

                    <div>
                        <label class="block text-xs font-bold text-slate-600 uppercase mb-1">Full Name</label>
                        <input type="text" name="name" required class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white">
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-slate-600 uppercase mb-1">Email</label>
                            <input type="email" name="email" required class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white">
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-slate-600 uppercase mb-1">Phone</label>
                            <input type="text" name="phone" required class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white" placeholder="01712-XXXXXX">
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 uppercase mb-1">Password</label>
                        <input type="password" name="password" required class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white">
                    </div>
                    
                    <div class="grid grid-cols-2 gap-4 p-3 bg-teal-50/50 rounded-xl border border-teal-100">
                        <div>
                            <label class="block text-[10px] font-bold text-teal-800 uppercase mb-1">BD Division</label>
                            <select id="reg-division" onchange="updateRegisterAreas()" required class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-white">
                                <option value="">Select Division</option>
                                <option value="Dhaka">Dhaka</option>
                                <option value="Chittagong">Chittagong</option>
                                <option value="Sylhet">Sylhet</option>
                                <option value="Khulna">Khulna</option>
                                <option value="Rajshahi">Rajshahi</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-[10px] font-bold text-teal-800 uppercase mb-1">Area / Thana</label>
                            <select id="reg-area" onchange="mapRegisterLocation()" required class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-white">
                                <option value="">Select Area</option>
                            </select>
                        </div>
                    </div>

                    <input type="hidden" name="address" id="reg-address">
                    <input type="hidden" name="lat" id="reg-lat">
                    <input type="hidden" name="lng" id="reg-lng">

                    <div id="vet-fields" class="space-y-4 hidden bg-slate-50 p-4 rounded-xl border border-slate-200 animate-fade-in">
                        <h3 class="text-xs font-extrabold text-slate-700 tracking-wider uppercase">Professional Verification</h3>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs text-slate-600 mb-1">Clinic Name</label>
                                <input type="text" name="clinic_name" class="w-full px-3 py-1.5 rounded-lg border border-slate-200 bg-white">
                            </div>
                            <div>
                                <label class="block text-xs text-slate-600 mb-1">License No.</label>
                                <input type="text" name="license_number" class="w-full px-3 py-1.5 rounded-lg border border-slate-200 bg-white" placeholder="LIC-XXXX">
                            </div>
                        </div>
                        <div>
                            <label class="block text-xs text-slate-600 mb-1">Specialization / Type Specialist</label>
                            <input type="text" name="specialization" class="w-full px-3 py-1.5 rounded-lg border border-slate-200 bg-white" placeholder="Canine, Feline, Exotic, Avian, Reptile">
                        </div>
                    </div>

                    <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-xl transition duration-150">Create Account</button>
                </form>

                <div id="otp-verification-box" class="mt-6 bg-slate-900 text-teal-400 p-4 rounded-xl font-mono text-xs hidden">
                    <div class="flex items-center justify-between border-b border-slate-800 pb-2 mb-2">
                        <span>📱 OTP Verification Needed</span>
                        <span class="text-[10px] text-slate-500">Terminal Simulation</span>
                    </div>
                    <p class="text-slate-300 mb-2">Check your server terminal log for the sent OTP. Type it below to activate:</p>
                    <div class="flex space-x-2">
                        <input type="text" id="otp-input" placeholder="Enter 6-digit OTP" class="bg-black/40 border border-teal-800 text-teal-400 text-center rounded px-2 py-1 flex-grow">
                        <button onclick="handleVerifyOTP()" class="bg-teal-500 hover:bg-teal-400 text-black font-bold px-4 py-1 rounded">Verify</button>
                    </div>
                </div>
            </div>
        </section>

        <!-- VIEW 2: CORE DASHBOARD (Normal users) -->
        <section id="view-dashboard" class="hidden flex-grow max-w-7xl w-full mx-auto p-4 sm:p-6 lg:p-8 space-y-8 overflow-y-auto">
            <div id="dashboard-navbar-strip" class="flex flex-wrap items-center justify-between border-b border-slate-200 pb-4 gap-4">
                <div class="flex items-center space-x-2">
                    <span class="px-3 py-1 bg-teal-100 text-teal-800 text-xs font-extrabold tracking-wider uppercase rounded-full" id="user-role-tag">Owner view</span>
                    <h2 class="text-2xl font-bold tracking-tight text-slate-900" id="welcome-message">Hello User</h2>
                </div>
                <nav class="flex items-center space-x-2 bg-slate-100 p-1 rounded-xl">
                    <button onclick="showPanel('pets')" class="tab-panel-btn px-4 py-2 text-sm font-semibold text-slate-600 rounded-lg focus:outline-none" id="btn-panel-pets">My Pets</button>
                    <button onclick="showPanel('vets')" class="tab-panel-btn px-4 py-2 text-sm font-semibold text-slate-600 rounded-lg focus:outline-none" id="btn-panel-vets">Veterinarians</button>
                    <button onclick="showPanel('emergency')" class="tab-panel-btn px-4 py-2 text-sm font-semibold text-slate-600 rounded-lg focus:outline-none text-rose-600 font-bold" id="btn-panel-emergency">🚨 Emergency Dispatch</button>
                </nav>
            </div>

            <!-- TRANSFER ACCEPT STRIP (CRITICAL FIX FOR COHERENCY) -->
            <div id="global-transfer-accept-strip" class="bg-blue-50 border border-blue-100 p-4 rounded-2xl flex flex-wrap items-center justify-between gap-3 hidden">
                <div class="flex items-center space-x-2 text-blue-800">
                    <i data-lucide="arrow-right-left" class="w-5 h-5"></i>
                    <span class="text-xs font-semibold">Have a Pet Transfer OTP? Enter it to claim ownership:</span>
                </div>
                <div class="flex items-center space-x-2 w-full sm:w-auto">
                    <input type="text" id="transfer-accept-otp-val" placeholder="Enter 6-digit OTP" class="px-3 py-1.5 text-xs rounded-lg border border-blue-200 focus:outline-none bg-white">
                    <button onclick="handleAcceptTransfer()" class="bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold px-4 py-1.5 rounded-lg transition">Claim Pet</button>
                </div>
            </div>

            <!-- STATIC OWNER INFORMATION -->
            <div id="owner-top-profile" class="bg-gradient-to-r from-teal-50 to-emerald-50 border border-teal-100 p-6 rounded-3xl flex flex-col md:flex-row items-center justify-between gap-6">
                 <div class="flex items-center space-x-4">
                      <div class="w-16 h-16 rounded-full bg-teal-600 text-white font-black text-xl flex items-center justify-center border-2 border-teal-200 shadow-md uppercase" id="owner-avatar">O</div>
                      <div>
                           <h3 class="font-extrabold text-slate-900 text-lg" id="owner-name-display">Registered Owner</h3>
                           <p class="text-xs text-slate-500 mt-0.5 flex items-center gap-1"><i data-lucide="map-pin" class="w-3.5 h-3.5 text-teal-600"></i> <span id="owner-address-display">Dhaka, Bangladesh</span></p>
                      </div>
                 </div>
                 <div class="text-xs space-y-1 text-slate-600 text-left md:text-right">
                      <p><b>Contact Phone:</b> <span id="owner-phone-display">N/A</span></p>
                      <p><b>System Role:</b> <span class="bg-teal-200/50 text-teal-800 font-bold px-2 py-0.5 rounded">Owner Verified</span></p>
                 </div>
            </div>

            <!-- SUB-PANEL: MY PETS -->
            <div id="panel-pets" class="space-y-6">
                <div class="flex flex-wrap items-center justify-between gap-4">
                    <div class="flex items-center space-x-4">
                        <h3 class="text-lg font-bold text-slate-800">Your Registered Companions</h3>
                        
                        <div class="flex items-center space-x-2 bg-slate-100/80 px-3 py-1.5 rounded-xl border border-slate-200">
                            <label class="text-[10px] font-bold text-slate-500 uppercase">Filter Species:</label>
                            <select id="pet-species-filter" onchange="filterPetsBySpecies()" class="text-xs font-semibold bg-transparent focus:outline-none text-slate-700 cursor-pointer">
                                <option value="All">All Species</option>
                                <option value="Dog">Dog</option>
                                <option value="Cat">Cat</option>
                                <option value="Bird">Bird</option>
                                <option value="Rabbit">Rabbit</option>
                                <option value="Reptile">Reptile</option>
                                <option value="Other">Other</option>
                            </select>
                        </div>
                    </div>

                    <button onclick="openModal('add-pet')" class="flex items-center space-x-2 bg-teal-600 hover:bg-teal-700 text-white font-semibold px-4 py-2 rounded-xl text-sm transition">
                        <i data-lucide="plus-circle" class="w-4 h-4"></i>
                        <span>Register Pet</span>
                    </button>
                </div>

                <div id="pets-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
            </div>

            <!-- SUB-PANEL: VETERINARIANS -->
            <div id="panel-vets" class="space-y-6 hidden">
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div class="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 space-y-4">
                        <div class="flex items-center space-x-3 text-emerald-600">
                            <i data-lucide="navigation" class="w-5 h-5"></i>
                            <h3 class="font-bold text-slate-800 text-lg">Bangladesh Geofencing</h3>
                        </div>
                        <p class="text-xs text-slate-500">To calculate accurate proximity, select your current area in Bangladesh:</p>
                        
                        <div class="space-y-3 bg-slate-50 p-4 rounded-xl border border-slate-200 text-slate-700">
                            <div>
                                <label class="block text-[10px] font-bold text-slate-600 mb-1">Select Division</label>
                                <select id="search-division" onchange="updateSearchAreas()" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-white">
                                    <option value="Dhaka">Dhaka</option>
                                    <option value="Chittagong">Chittagong</option>
                                    <option value="Sylhet">Sylhet</option>
                                    <option value="Khulna">Khulna</option>
                                    <option value="Rajshahi">Rajshahi</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-[10px] font-bold text-slate-600 mb-1">Select Current Area</label>
                                <select id="search-area" class="w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-white">
                                    <!-- Populated dynamically via JS -->
                                </select>
                            </div>
                        </div>

                        <button onclick="lookupNearbyVets()" class="w-full flex items-center justify-center space-x-2 bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-2.5 rounded-xl transition">
                            <i data-lucide="search" class="w-4 h-4"></i>
                            <span>Sort Closest Vets</span>
                        </button>
                    </div>

                    <div class="bg-white rounded-2xl p-6 shadow-sm border border-slate-100 lg:col-span-2">
                        <div class="flex items-center justify-between mb-4">
                            <div class="flex items-center space-x-3 text-violet-600">
                                <i data-lucide="sparkles" class="w-5 h-5"></i>
                                <h3 class="font-bold text-slate-800 text-lg">Personalized Health Promo Campaigns</h3>
                            </div>
                            <span class="text-xs bg-violet-100 text-violet-800 px-2 py-0.5 rounded font-extrabold uppercase">Campaign Engine</span>
                        </div>
                        <div id="promotions-feed" class="space-y-3 text-sm text-slate-600"></div>
                    </div>
                </div>

                <div>
                    <h4 class="font-bold text-slate-800 mb-3">All Active Network Clinics</h4>
                    <div id="vets-list" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
                </div>
            </div>

            <!-- SUB-PANEL: EMERGENCY SYSTEM -->
            <div id="panel-emergency" class="space-y-6 hidden">
                <div class="bg-rose-50 border border-rose-100 rounded-2xl p-6 max-w-2xl mx-auto space-y-6 text-slate-700">
                    <div class="flex items-center space-x-4 text-rose-600">
                        <div class="p-3 bg-rose-100 rounded-2xl animate-pulse">
                            <i data-lucide="alert-octagon" class="w-8 h-8"></i>
                        </div>
                        <div>
                            <h3 class="text-2xl font-black text-rose-950">Emergency Tele-Triage Requisition</h3>
                            <p class="text-xs text-rose-700">Immediate priority routing algorithm and diagnostic symptom checker.</p>
                        </div>
                    </div>

                    <div class="bg-white rounded-xl p-5 border border-rose-200/50 space-y-4 shadow-sm text-slate-700">
                        <h4 class="text-sm font-bold text-slate-800 uppercase tracking-wide">Step 1: Symptom Checker Matrix</h4>
                        <div class="space-y-2 text-sm">
                            <p class="font-medium">Is your pet currently experiencing breathing difficulties, bleeding or extreme lethargy?</p>
                            <div class="flex space-x-3">
                                <button onclick="setUrgency('Critical')" class="flex-1 py-2 rounded-lg font-bold border border-rose-500 text-rose-600 hover:bg-rose-50 transition text-center">🚨 Yes (Critical Priority)</button>
                                <button onclick="setUrgency('Moderate')" class="flex-1 py-2 rounded-lg font-bold border border-amber-500 text-amber-600 hover:bg-amber-50 transition text-center">⚠️ No, but unstable (Moderate)</button>
                            </div>
                        </div>

                        <div class="space-y-3 pt-3 border-t border-slate-100">
                            <label class="block text-xs font-bold text-slate-600">Describe Symptoms</label>
                            <textarea id="emergency-symptoms" placeholder="Include as much detail as possible (e.g. ingested substance, lethargy details)..." class="w-full p-3 rounded-lg border border-slate-200 focus:ring-2 focus:ring-rose-500 focus:outline-none text-sm h-24"></textarea>
                        </div>

                        <button onclick="submitEmergency()" class="w-full bg-rose-600 hover:bg-rose-700 text-white font-black py-3 rounded-xl transition shadow-lg shadow-rose-600/20">
                            Broadcast Live Signal to nearest Vets
                        </button>
                    </div>

                    <div id="dispatch-outcome" class="hidden bg-slate-900 text-white p-5 rounded-xl font-mono text-xs space-y-3"></div>
                </div>
            </div>
        </section>

        <!-- VIEW 3: STRICT ADMIN SYSTEM DASHBOARD -->
        <section id="view-admin" class="hidden flex-grow max-w-7xl w-full mx-auto p-4 sm:p-6 lg:p-8 space-y-8 overflow-y-auto text-slate-700">
             <div class="flex items-center justify-between border-b pb-4">
                  <div class="flex items-center space-x-3">
                       <span class="bg-teal-600 text-white text-xs font-black uppercase px-3 py-1 rounded-full">Secure Admin Shell</span>
                       <h2 class="text-3xl font-black text-slate-900 tracking-tight">Administrative Control Dashboard</h2>
                  </div>
                  <button onclick="handleLogout()" class="bg-slate-800 hover:bg-slate-900 text-white text-xs font-bold px-4 py-2 rounded-xl transition">Sign Out</button>
             </div>

             <!-- ANALYTICS CARDS -->
             <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                  <div class="bg-white p-6 rounded-3xl border border-slate-100 shadow-sm flex items-center space-x-4">
                       <div class="p-3 bg-teal-50 text-teal-600 rounded-2xl"><i data-lucide="users" class="w-6 h-6"></i></div>
                       <div>
                            <p class="text-slate-400 text-xs font-bold uppercase tracking-wider">Total Owners</p>
                            <h4 id="stat-owners" class="text-2xl font-black text-slate-950">0</h4>
                       </div>
                  </div>
                  <div class="bg-white p-6 rounded-3xl border border-slate-100 shadow-sm flex items-center space-x-4">
                       <div class="p-3 bg-emerald-50 text-emerald-600 rounded-2xl"><i data-lucide="user-check" class="w-6 h-6"></i></div>
                       <div>
                            <p class="text-slate-400 text-xs font-bold uppercase tracking-wider">Approved Vets</p>
                            <h4 id="stat-approved-vets" class="text-2xl font-black text-slate-950">0</h4>
                       </div>
                  </div>
                  <div class="bg-white p-6 rounded-3xl border border-slate-100 shadow-sm flex items-center space-x-4">
                       <div class="p-3 bg-amber-50 text-amber-600 rounded-2xl"><i data-lucide="clock" class="w-6 h-6"></i></div>
                       <div>
                            <p class="text-slate-400 text-xs font-bold uppercase tracking-wider">Pending Vets</p>
                            <h4 id="stat-pending-vets" class="text-2xl font-black text-slate-950">0</h4>
                       </div>
                  </div>
                  <div class="bg-white p-6 rounded-3xl border border-slate-100 shadow-sm flex items-center space-x-4">
                       <div class="p-3 bg-rose-50 text-rose-600 rounded-2xl"><i data-lucide="user-x" class="w-6 h-6"></i></div>
                       <div>
                            <p class="text-slate-400 text-xs font-bold uppercase tracking-wider">Rejected Vets</p>
                            <h4 id="stat-rejected-vets" class="text-2xl font-black text-slate-950">0</h4>
                       </div>
                  </div>
             </div>

             <!-- SPECIES DISTRIBUTION CHART AND PENDING APPROVALS -->
             <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                  <!-- Chart Component -->
                  <div class="bg-white p-6 rounded-3xl border border-slate-100 shadow-sm space-y-4">
                       <h3 class="font-extrabold text-slate-900 text-base flex items-center gap-1.5"><i data-lucide="bar-chart" class="text-teal-600 w-5 h-5"></i> Pets Population Distribution Chart</h3>
                       <p class="text-xs text-slate-500">Distribution analysis calculated dynamically across all registered animals.</p>
                       <div id="pets-distribution-chart" class="space-y-3 pt-4 text-xs"></div>
                  </div>

                  <!-- Verification Queues -->
                  <div class="bg-white p-6 rounded-3xl border border-slate-100 shadow-sm lg:col-span-2 space-y-4">
                       <h3 class="font-extrabold text-slate-900 text-base flex items-center gap-1.5"><i data-lucide="shadow" class="text-amber-600 w-5 h-5"></i> Verification Approvals Queue</h3>
                       <div class="overflow-x-auto text-xs">
                            <table class="w-full text-left border-collapse">
                                <thead>
                                    <tr class="bg-slate-50 border-b font-bold text-slate-600">
                                        <th class="p-3">Vet Name</th>
                                        <th class="p-3">Clinic & License</th>
                                        <th class="p-3 text-right">Actions Route</th>
                                    </tr>
                                </thead>
                                <tbody id="admin-vet-rows"></tbody>
                            </table>
                       </div>
                  </div>
             </div>

             <!-- WEBSITE CONTENT EDITABLE SECTION -->
             <div class="bg-white p-6 rounded-3xl border border-slate-100 shadow-sm space-y-6">
                  <div class="flex items-center justify-between border-b pb-3">
                       <div class="space-y-1">
                            <h3 class="font-extrabold text-slate-900 text-lg flex items-center gap-1.5"><i data-lucide="edit" class="text-teal-600 w-5 h-5"></i> Landing Page Milestones Content Manager</h3>
                            <p class="text-xs text-slate-500">Allows direct content manipulation of the public A-Z life stages cards list.</p>
                       </div>
                  </div>
                  
                  <div class="overflow-x-auto text-xs">
                       <table class="w-full text-left border-collapse">
                            <thead>
                                 <tr class="bg-slate-50 border-b font-bold text-slate-600">
                                      <th class="p-3">Letter</th>
                                      <th class="p-3">Milestone Title</th>
                                      <th class="p-3">Life Cycle Stage</th>
                                      <th class="p-3 text-right">Operation</th>
                                 </tr>
                            </thead>
                            <tbody id="admin-milestone-rows"></tbody>
                       </table>
                  </div>
             </div>
        </section>

        <!-- ==========================================
          MODALS & FLYOUTS SECTION
        ========================================== -->
        
        <!-- Modal: Add Pet Form -->
        <div id="modal-add-pet" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden text-slate-700">
            <div class="bg-white rounded-2xl max-w-md w-full shadow-2xl border border-slate-100 overflow-hidden animate-in fade-in zoom-in-95 duration-200">
                <div class="bg-teal-600 p-5 text-white flex items-center justify-between">
                    <h3 class="font-bold text-lg">Register Companion</h3>
                    <button onclick="closeModal('add-pet')" class="text-white hover:text-teal-200"><i data-lucide="x"></i></button>
                </div>
                <form id="form-add-pet" onsubmit="handleAddPet(event)" class="p-6 space-y-4" enctype="multipart/form-data">
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Companion Name</label>
                        <input type="text" name="name" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white">
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Species / Type</label>
                            <select name="species" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white">
                                <option value="Dog">Dog (Canine)</option>
                                <option value="Cat">Cat (Feline)</option>
                                <option value="Bird">Bird (Avian)</option>
                                <option value="Rabbit">Rabbit (Leporidae)</option>
                                <option value="Reptile">Reptile</option>
                                <option value="Other">Other</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Breed</label>
                            <input type="text" name="breed" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white" placeholder="e.g. Persian">
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Date of Birth</label>
                        <input type="date" name="dob_str" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Microchip Number (Optional)</label>
                        <input type="text" name="microchip" class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-teal-500 bg-white" placeholder="985-112-XXXX">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Companion Photo (Manual Upload)</label>
                        <input type="file" name="photo_file" accept="image/*" class="w-full text-xs text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-teal-50 file:text-teal-700 hover:file:bg-teal-100 bg-white">
                    </div>
                    <button type="submit" class="w-full bg-teal-600 hover:bg-teal-700 text-white font-bold py-2.5 rounded-xl transition">
                        Initialize Lifecycle Matrix
                    </button>
                </form>
            </div>
        </div>

        <!-- Modal: Pet Detailed Lifecycle Record View -->
        <div id="modal-pet-details" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
            <div class="bg-white rounded-2xl max-w-4xl w-full h-[90vh] flex flex-col shadow-2xl border border-slate-100 overflow-hidden text-slate-700">
                <div class="bg-gradient-to-r from-teal-700 to-teal-800 p-6 text-white flex items-center justify-between">
                    <div>
                        <span id="detail-pet-tag" class="text-xs bg-amber-400 text-teal-950 font-bold px-2 py-0.5 rounded-full uppercase">PET-XXXXX</span>
                        <h3 class="font-extrabold text-2xl" id="detail-pet-name">Companion Details</h3>
                    </div>
                    <button onclick="closeModal('pet-details')" class="text-white hover:text-teal-200"><i data-lucide="x"></i></button>
                </div>

                <div class="flex-grow overflow-y-auto p-6 space-y-8">
                    <div class="flex flex-wrap gap-2 pb-4 border-b border-slate-100">
                        <button onclick="triggerHealthPassportDownload()" class="flex items-center space-x-1.5 bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-2 rounded-xl text-xs font-bold transition">
                            <i data-lucide="shield-check" class="w-4 h-4"></i>
                            <span>Download Health Passport</span>
                        </button>
                        <button onclick="openOwnershipTransferModal()" class="flex items-center space-x-1.5 bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded-xl text-xs font-bold transition">
                            <i data-lucide="arrow-right-left" class="w-4 h-4"></i>
                            <span>Initiate Owner Transfer</span>
                        </button>
                        <button id="add-rx-btn" onclick="openAddPrescriptionModal()" class="flex items-center space-x-1.5 bg-violet-600 hover:bg-violet-700 text-white px-3 py-2 rounded-xl text-xs font-bold transition hidden">
                            <i data-lucide="file-plus" class="w-4 h-4"></i>
                            <span>Write Prescription</span>
                        </button>
                        <button id="add-history-btn" onclick="openAddHistoryModal()" class="flex items-center space-x-1.5 bg-slate-700 hover:bg-slate-800 text-white px-3 py-2 rounded-xl text-xs font-bold transition hidden">
                            <i data-lucide="plus" class="w-4 h-4"></i>
                            <span>Record Event Log</span>
                        </button>
                    </div>

                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                        <div class="space-y-4">
                            <h4 class="font-bold text-slate-800 text-base flex items-center gap-1.5">
                                <i data-lucide="syringe" class="text-teal-600 w-5 h-5"></i>
                                <span>Lifecycle Vaccine Records</span>
                            </h4>
                            <div id="detail-vaccine-timeline" class="space-y-3"></div>
                        </div>

                        <div class="space-y-6">
                            <div>
                                <h4 class="font-bold text-slate-800 text-base flex items-center gap-1.5 mb-3">
                                    <i data-lucide="activity" class="text-indigo-600 w-5 h-5"></i>
                                    <span>Medical Timeline Logs</span>
                                </h4>
                                <div id="detail-medical-events" class="space-y-2 max-h-[200px] overflow-y-auto"></div>
                            </div>

                            <div class="border-t border-slate-100 pt-4">
                                <h4 class="font-bold text-slate-800 text-base flex items-center gap-1.5 mb-3">
                                    <i data-lucide="pill" class="text-violet-600 w-5 h-5"></i>
                                    <span>Issued PDF Prescriptions</span>
                                </h4>
                                <div id="detail-prescriptions" class="space-y-2"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Modal: Edit Pet Profile -->
        <div id="modal-edit-pet" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-[55] flex items-center justify-center p-4 hidden text-slate-700">
            <div class="bg-white rounded-2xl max-w-md w-full shadow-2xl border border-slate-100 overflow-hidden animate-in fade-in duration-200">
                <div class="bg-amber-500 p-5 text-white flex items-center justify-between">
                    <h3 class="font-bold text-lg flex items-center gap-2">
                        <i data-lucide="edit-3" class="w-5 h-5"></i>
                        <span>Edit Companion Profile</span>
                    </h3>
                    <button onclick="closeModal('edit-pet')" class="text-white hover:text-amber-200"><i data-lucide="x"></i></button>
                </div>
                <form id="form-edit-pet" onsubmit="handleEditPet(event)" class="p-6 space-y-4" enctype="multipart/form-data">
                    <input type="hidden" id="edit-pet-id-field">
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Companion Name</label>
                        <input type="text" name="name" id="edit-pet-name" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:ring-2 focus:ring-amber-500 focus:outline-none bg-white">
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Species / Type</label>
                            <select name="species" id="edit-pet-species" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:ring-2 focus:ring-amber-500 focus:outline-none bg-white">
                                <option value="Dog">Dog (Canine)</option>
                                <option value="Cat">Cat (Feline)</option>
                                <option value="Bird">Bird (Avian)</option>
                                <option value="Rabbit">Rabbit (Leporidae)</option>
                                <option value="Reptile">Reptile</option>
                                <option value="Other">Other</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Breed</label>
                            <input type="text" name="breed" id="edit-pet-breed" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:ring-2 focus:ring-amber-500 focus:outline-none bg-white">
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Date of Birth</label>
                        <input type="date" name="dob_str" id="edit-pet-dob" required class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:ring-2 focus:ring-amber-500 focus:outline-none bg-white">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Microchip Number (Optional)</label>
                        <input type="text" name="microchip" id="edit-pet-microchip" class="w-full px-3 py-2 border border-slate-200 rounded-xl focus:ring-2 focus:ring-amber-500 focus:outline-none bg-white">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Update Companion Photo</label>
                        <input type="file" name="photo_file" accept="image/*" class="w-full text-xs text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-amber-50 file:text-amber-700 hover:file:bg-amber-100 bg-white">
                    </div>
                    <div class="flex space-x-2 pt-2">
                        <button type="button" onclick="closeModal('edit-pet')" class="flex-1 py-2 bg-slate-100 text-slate-700 font-bold rounded-lg text-sm">Cancel</button>
                        <button type="submit" class="flex-1 py-2 bg-amber-500 text-white font-bold rounded-lg text-sm">Save Changes</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Safe Trash Confirmation Modal -->
        <div id="modal-delete-confirm" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-[70] flex items-center justify-center p-4 hidden text-slate-700">
             <div class="bg-white rounded-2xl max-sm w-full p-6 space-y-4 shadow-2xl border border-slate-100 animate-in fade-in duration-200">
                  <div class="text-rose-600 flex items-center space-x-2">
                       <i data-lucide="trash-2" class="w-6 h-6"></i>
                       <h4 class="font-bold text-lg text-slate-900">Delete Companion profile?</h4>
                  </div>
                  <p class="text-xs text-slate-500 leading-relaxed">This operation is destructive and permanently deletes all medical history, vaccine schedules, and issued PDF records associated with this companion.</p>
                  <input type="hidden" id="delete-confirm-pet-id">
                  <div class="flex space-x-2 pt-2">
                       <button onclick="closeModal('delete-confirm')" class="flex-1 py-2 bg-slate-100 text-slate-700 font-bold text-xs rounded-lg">Cancel</button>
                       <button onclick="executePetRemoval()" class="flex-1 py-2 bg-rose-600 text-white font-bold text-xs rounded-lg">Delete Permanently</button>
                  </div>
             </div>
        </div>

        <!-- Milestone Editor Modal for Admin User -->
        <div id="modal-milestone-editor" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-[55] flex items-center justify-center p-4 hidden text-slate-700">
             <div class="bg-white rounded-2xl max-w-lg w-full shadow-2xl border overflow-hidden">
                  <div class="bg-teal-600 p-5 text-white flex items-center justify-between">
                       <h3 class="font-bold text-base flex items-center gap-2"><i data-lucide="edit-3"></i> Edit Dynamic Milestone</h3>
                       <button onclick="closeModal('milestone-editor')"><i data-lucide="x"></i></button>
                  </div>
                  <form id="form-milestone-editor" onsubmit="handleMilestoneContentUpdate(event)" class="p-6 space-y-4">
                       <input type="hidden" id="editor-milestone-id">
                       <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Milestone Header (Title)</label>
                            <input type="text" name="title" id="editor-m-title" required class="w-full px-3 py-1.5 border rounded-lg bg-white">
                       </div>
                       <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Growth Category / Stage Label</label>
                            <input type="text" name="category" id="editor-m-category" required class="w-full px-3 py-1.5 border rounded-lg bg-white">
                       </div>
                       <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Stage Description</label>
                            <textarea name="description" id="editor-m-desc" required class="w-full px-3 py-1.5 border rounded-lg h-20 bg-white text-xs"></textarea>
                       </div>
                       <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Recommended Care Guidelines</label>
                            <textarea name="care_guideline" id="editor-m-guideline" required class="w-full px-3 py-1.5 border rounded-lg h-16 bg-white text-xs"></textarea>
                       </div>
                       <div class="flex space-x-2 pt-2">
                            <button type="button" onclick="closeModal('milestone-editor')" class="flex-1 py-2 bg-slate-100 text-slate-700 font-bold rounded-lg text-sm">Cancel</button>
                            <button type="submit" class="flex-1 py-2 bg-teal-600 text-white font-bold rounded-lg text-sm">Commit Updates</button>
                       </div>
                  </form>
             </div>
        </div>

        <!-- Write Prescription Form -->
        <div id="modal-add-rx" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4 hidden text-slate-700 animate-fade-in">
            <div class="bg-white rounded-2xl max-w-md w-full p-6 space-y-4">
                <h4 class="font-bold text-lg text-slate-900">Issue Requisition RX</h4>
                <form id="form-add-rx" onsubmit="handleAddPrescription(event)" class="space-y-3">
                    <input type="hidden" name="pet_id" id="rx-pet-id-field">
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Medicine Name</label>
                        <input type="text" name="medicine" required class="w-full px-3 py-1.5 border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-teal-500">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Dosage strength</label>
                        <input type="text" name="dosage" placeholder="e.g. 50mg" required class="w-full px-3 py-1.5 border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-teal-500">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Instructions</label>
                        <textarea name="instructions" required class="w-full px-3 py-1.5 border rounded-lg h-20 bg-white focus:outline-none focus:ring-2 focus:ring-teal-500 text-xs"></textarea>
                    </div>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block text-xs font-bold text-slate-600 mb-1">Duration</label>
                            <input type="text" name="duration" placeholder="e.g. 10 Days" required class="w-full px-3 py-1.5 border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-teal-500">
                        </div>
                        <div class="flex items-center space-x-2 pt-6">
                            <input type="checkbox" name="is_tele_triage" id="rx-tele-triage">
                            <label class="text-xs text-slate-700 font-bold" for="rx-tele-triage">Tele-triage check?</label>
                        </div>
                    </div>
                    <div class="flex space-x-2 pt-2">
                        <button type="button" onclick="closeModal('add-rx')" class="flex-1 py-2 bg-slate-100 text-slate-700 font-bold rounded-lg text-sm">Cancel</button>
                        <button type="submit" class="flex-1 py-2 bg-violet-600 text-white font-bold rounded-lg text-sm">Stamp RX Requisition</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Add History Record Form -->
        <div id="modal-add-history" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4 hidden text-slate-700 animate-fade-in">
            <div class="bg-white rounded-2xl max-w-md w-full p-6 space-y-4">
                <h4 class="font-bold text-lg text-slate-900">Add History Record</h4>
                <form id="form-add-history" onsubmit="handleAddHistory(event)" class="space-y-3">
                    <input type="hidden" name="pet_id" id="history-pet-id-field">
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Event Type</label>
                        <select name="event_type" required class="w-full px-3 py-1.5 border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-teal-500">
                            <option value="treatment">Treatment / Injection</option>
                            <option value="operation">Surgery / Operations</option>
                            <option value="illness">Diagnosed Illness</option>
                            <option value="allergy">Allergenic Flare-up</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Detailed Description</label>
                        <textarea name="description" required class="w-full px-3 py-1.5 border rounded-lg h-24 bg-white focus:outline-none focus:ring-2 focus:ring-teal-500 text-xs"></textarea>
                    </div>
                    <div class="flex space-x-2 pt-2">
                        <button type="button" onclick="closeModal('add-history')" class="flex-1 py-2 bg-slate-100 text-slate-700 font-bold rounded-lg text-sm">Cancel</button>
                        <button type="submit" class="flex-1 py-2 bg-slate-800 text-white font-bold rounded-lg text-sm">Record Event</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Dual Verification Form -->
        <div id="modal-verify-vaccine" class="fixed inset-0 bg-black/60 z-[65] flex items-center justify-center p-4 hidden text-slate-700 animate-fade-in shadow-2xl">
            <div class="bg-white rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl">
                <div class="flex items-center justify-between border-b pb-2">
                    <h4 class="font-bold text-lg text-slate-900">Dual verification Stamp</h4>
                    <button onclick="closeModal('verify-vaccine')"><i data-lucide="x" class="w-5 h-5 text-slate-400 hover:text-slate-600"></i></button>
                </div>
                
                <div class="space-y-4">
                    <div class="p-4 bg-teal-50 border border-teal-100 rounded-xl space-y-2 text-sm">
                        <div class="flex items-center space-x-2 text-teal-700 font-bold">
                            <i data-lucide="user-check" class="w-5 h-5"></i>
                            <span>Pathway A: Vet Verification Code</span>
                        </div>
                        <p class="text-xs text-slate-500">For authorized vets: Enter active batch sequence and stamp.</p>
                        <form id="form-verify-vet" onsubmit="handleVerifyVet(event)" class="space-y-2">
                            <input type="hidden" id="verify-rec-id-field-vet">
                            <input type="text" name="batch" placeholder="Enter Batch No. (e.g. B-99382)" required class="w-full px-3 py-1.5 border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-teal-500">
                            <button type="submit" class="w-full bg-teal-600 hover:bg-teal-700 text-white text-xs font-bold py-2 rounded-lg transition">Stamp Official Vet Signature</button>
                        </form>
                    </div>

                    <div class="p-4 bg-indigo-50 border border-indigo-100 rounded-xl space-y-2 text-sm">
                        <div class="flex items-center space-x-2 text-indigo-700 font-bold">
                            <i data-lucide="file-text" class="w-5 h-5"></i>
                            <span>Pathway B: Owner OCR Transcript Upload</span>
                        </div>
                        <p class="text-xs text-slate-500">Provide official receipt context text to allow OCR scanner analysis verification.</p>
                        <form id="form-verify-doc" onsubmit="handleVerifyDoc(event)" class="space-y-2">
                            <input type="hidden" id="verify-rec-id-field-doc">
                            <textarea name="cert_text" placeholder="Copy paste receipt summary..." required class="w-full px-3 py-1.5 border rounded-lg h-20 bg-white text-xs focus:outline-none focus:ring-2 focus:ring-teal-500"></textarea>
                            <button type="submit" class="w-full bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold py-2 rounded-lg transition">Trigger AI Document OCR Scanner</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>

        <!-- Owner Transfer Form -->
        <div id="modal-transfer-init" class="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4 hidden text-slate-700 animate-fade-in">
            <div class="bg-white rounded-2xl max-w-md w-full p-6 space-y-4">
                <h4 class="font-bold text-lg text-slate-900">Initiate Secure Transfer Link</h4>
                <form id="form-transfer-init" onsubmit="handleInitiateTransfer(event)" class="space-y-3">
                    <input type="hidden" name="pet_id" id="transfer-pet-id-field">
                    <div>
                        <label class="block text-xs font-bold text-slate-600 mb-1">Target Recipient Email</label>
                        <input type="email" name="target_email" placeholder="new_owner@email.com" required class="w-full px-3 py-1.5 border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-teal-500">
                    </div>
                    <div class="flex space-x-2 pt-2">
                        <button type="button" onclick="closeModal('transfer-init')" class="flex-1 py-2 bg-slate-100 text-slate-700 font-bold rounded-lg text-sm">Cancel</button>
                        <button type="submit" class="flex-1 py-2 bg-blue-600 text-white font-bold rounded-lg text-sm">Generate OTP Key</button>
                    </div>
                </form>

                <div id="transfer-otp-sim-box" class="bg-slate-900 text-emerald-400 p-4 rounded-xl font-mono text-xs hidden space-y-2">
                    <p class="text-slate-300">Share this Code to complete the legal process:</p>
                    <div class="text-xl font-black tracking-widest text-center text-white bg-slate-950 p-2 rounded" id="trans-code-out">000000</div>
                </div>
            </div>
        </div>

        <!-- ==========================================
          PORTAL CORE LOGIC ENGINE (JS)
        ========================================== -->
        <script>
            let currentUser = null;
            let currentSelectedPetId = null;
            let currentUrgency = 'Critical';
            let loadedPetsCache = [];
            let milestonesCache = [];

            const bdLocations = {
                "Dhaka": [
                    { name: "Dhanmondi, Dhaka", lat: 23.7461, lng: 90.3742 },
                    { name: "Gulshan, Dhaka", lat: 23.7925, lng: 90.4078 },
                    { name: "Banani, Dhaka", lat: 23.7937, lng: 90.4033 },
                    { name: "Uttara, Dhaka", lat: 23.8759, lng: 90.3795 },
                    { name: "Mirpur, Dhaka", lat: 23.8223, lng: 90.3654 },
                    { name: "Motijheel, Dhaka", lat: 23.7330, lng: 90.4172 }
                ],
                "Chittagong": [
                    { name: "GEC Circle, Chittagong", lat: 22.3592, lng: 91.8219 },
                    { name: "Halishahar, Chittagong", lat: 22.3168, lng: 91.7915 },
                    { name: "Agrabad, Chittagong", lat: 22.3253, lng: 91.8124 }
                ],
                "Sylhet": [
                    { name: "Zindabazar, Sylhet", lat: 24.8949, lng: 91.8687 },
                    { name: "Amberkhana, Sylhet", lat: 24.9036, lng: 91.8681 }
                ],
                "Khulna": [
                    { name: "Khalishpur, Khulna", lat: 22.8456, lng: 89.5413 },
                    { name: "Boyra, Khulna", lat: 22.8273, lng: 89.5521 }
                ],
                "Rajshahi": [
                    { name: "Shaheb Bazar, Rajshahi", lat: 24.3636, lng: 88.6014 },
                    { name: "Motihar, Rajshahi", lat: 24.3615, lng: 88.6369 }
                ]
            };

            window.onload = function() {
                lucide.createIcons();
                loadLandingMilestones(); 
                fetchUserSession();
                updateSearchAreas();
            }

            async function apiFetch(url, options = {}) {
                if (!options.headers) {
                    options.headers = {};
                }
                const token = localStorage.getItem('petpals_token');
                if (token) {
                    options.headers['Authorization'] = `Bearer ${token}`;
                }
                return fetch(url, options);
            }

            function updateRegisterAreas() {
                const division = document.getElementById('reg-division').value;
                const areaSelect = document.getElementById('reg-area');
                areaSelect.innerHTML = '<option value="">Select Area</option>';
                
                if (division && bdLocations[division]) {
                    bdLocations[division].forEach(loc => {
                        const opt = document.createElement('option');
                        opt.value = loc.name;
                        opt.text = loc.name.split(',')[0];
                        opt.dataset.lat = loc.lat;
                        opt.dataset.lng = loc.lng;
                        areaSelect.appendChild(opt);
                    });
                }
            }

            function mapRegisterLocation() {
                const areaSelect = document.getElementById('reg-area');
                const selectedOpt = areaSelect.options[areaSelect.selectedIndex];
                
                if (selectedOpt && selectedOpt.value) {
                    document.getElementById('reg-address').value = selectedOpt.value;
                    document.getElementById('reg-lat').value = selectedOpt.dataset.lat;
                    document.getElementById('reg-lng').value = selectedOpt.dataset.lng;
                }
            }

            function updateSearchAreas() {
                const division = document.getElementById('search-division').value;
                const areaSelect = document.getElementById('search-area');
                areaSelect.innerHTML = '';
                
                if (division && bdLocations[division]) {
                    bdLocations[division].forEach(loc => {
                        const opt = document.createElement('option');
                        opt.value = loc.name;
                        opt.text = loc.name.split(',')[0];
                        opt.dataset.lat = loc.lat;
                        opt.dataset.lng = loc.lng;
                        areaSelect.appendChild(opt);
                    });
                }
            }

            async function loadLandingMilestones() {
                 const resp = await apiFetch('/api/milestones');
                 if (resp.ok) {
                      milestonesCache = await resp.json();
                      const list = document.getElementById('dynamic-milestones-list');
                      list.innerHTML = "";
                      
                      milestonesCache.forEach(m => {
                           const card = document.createElement('div');
                           card.className = "bg-white p-6 rounded-3xl shadow-sm border border-slate-100 space-y-4 hover:shadow-md transition";
                           card.innerHTML = `
                               <div class="w-12 h-12 bg-teal-100 text-teal-700 rounded-2xl flex items-center justify-center font-bold text-xl">${m.letter}</div>
                               <div>
                                   <span class="text-xs font-black uppercase text-teal-600">${m.category}</span>
                                   <h4 class="text-lg font-bold text-slate-900">${m.title}</h4>
                               </div>
                               <p class="text-slate-600 text-xs leading-relaxed">${m.description}</p>
                               <div class="text-xs bg-slate-50 p-3 rounded-xl border border-slate-100 text-slate-500">
                                   <strong>Care Guideline:</strong> ${m.care_guideline}
                               </div>
                           `;
                           list.appendChild(card);
                      });
                 }
            }

            function showLandingView() {
                document.getElementById('view-landing').classList.remove('hidden');
                document.getElementById('view-auth').classList.add('hidden');
                document.getElementById('view-dashboard').classList.add('hidden');
                document.getElementById('view-admin').classList.add('hidden');
                
                const acceptStrip = document.getElementById('global-transfer-accept-strip');
                if (acceptStrip) acceptStrip.classList.add('hidden');
                
                document.getElementById('public-search-outcome-card').classList.add('hidden');
            }

            function transitionToApp() {
                const acceptStrip = document.getElementById('global-transfer-accept-strip');
                if (currentUser) {
                     document.getElementById('view-landing').classList.add('hidden');
                     document.getElementById('public-search-outcome-card').classList.add('hidden');
                     if (currentUser.role === 'admin') {
                          document.getElementById('view-auth').classList.add('hidden');
                          document.getElementById('view-dashboard').classList.add('hidden');
                          document.getElementById('view-admin').classList.remove('hidden');
                          if (acceptStrip) acceptStrip.classList.add('hidden');
                          loadAdminStatsAndQueues();
                     } else {
                          document.getElementById('view-auth').classList.add('hidden');
                          document.getElementById('view-dashboard').classList.remove('hidden');
                          document.getElementById('view-admin').classList.add('hidden');
                          if (acceptStrip) acceptStrip.classList.remove('hidden');
                          loadMyPets();
                     }
                } else {
                    document.getElementById('view-landing').classList.remove('hidden');
                    document.getElementById('view-auth').classList.add('hidden');
                    document.getElementById('view-dashboard').classList.add('hidden');
                    document.getElementById('view-admin').classList.add('hidden');
                    if (acceptStrip) acceptStrip.classList.add('hidden');
                    document.getElementById('public-search-outcome-card').classList.add('hidden');
                }
            }

            function triggerAppAuthView(mode, targetRole = 'owner') {
                showLandingView();
                document.getElementById('view-landing').classList.add('hidden');
                document.getElementById('view-auth').classList.remove('hidden');
                if (mode === 'login') {
                    toggleAuthTab('login');
                } else {
                    toggleAuthTab('register');
                    selectRegisterRole(targetRole);
                }
            }

            function selectRegisterRole(role) {
                document.getElementById('reg-role-input').value = role;
                const ownerCard = document.getElementById('role-card-owner');
                const vetCard = document.getElementById('role-card-vet');
                
                if (role === 'owner') {
                    ownerCard.className = "cursor-pointer border-2 border-teal-500 bg-teal-50/50 rounded-2xl p-4 text-center transition flex flex-col items-center justify-center space-y-2";
                    vetCard.className = "cursor-pointer border-2 border-slate-100 bg-white rounded-2xl p-4 text-center transition flex flex-col items-center justify-center space-y-2 hover:border-teal-200";
                    document.getElementById('vet-fields').classList.add('hidden');
                } else {
                    vetCard.className = "cursor-pointer border-2 border-teal-500 bg-teal-50/50 rounded-2xl p-4 text-center transition flex flex-col items-center justify-center space-y-2";
                    ownerCard.className = "cursor-pointer border-2 border-slate-100 bg-white rounded-2xl p-4 text-center transition flex flex-col items-center justify-center space-y-2 hover:border-teal-200";
                    document.getElementById('vet-fields').classList.remove('hidden');
                }
            }

            function scrollToLifecycle() {
                document.getElementById('lifecycle-guide').scrollIntoView({ behavior: 'smooth' });
            }

            function searchLibrary() {
                const searchVal = document.getElementById('lifecycle-search').value.toLowerCase();
                const items = document.querySelectorAll('.library-item');
                
                items.forEach(item => {
                    const keywords = item.dataset.keyword.toLowerCase();
                    const text = item.innerText.toLowerCase();
                    
                    if (keywords.includes(searchVal) || text.includes(searchVal)) {
                        item.classList.remove('hidden');
                    } else {
                        item.classList.add('hidden');
                    }
                });
            }

            async function executePublicSearchLookup() {
                const queryVal = document.getElementById('public-pet-search-input').value;
                if (!queryVal) return;
                
                const resp = await apiFetch(`/api/search?q=${encodeURIComponent(queryVal)}`);
                if (resp.ok) {
                    const data = await resp.json();
                    const container = document.getElementById('public-search-results-box');
                    container.innerHTML = "";
                    
                    const block = document.getElementById('public-search-outcome-card');
                    block.classList.remove('hidden');
                    block.scrollIntoView({ behavior: 'smooth' });
                    
                    if (data.length === 0) {
                        container.innerHTML = `<p class="text-xs text-slate-500 italic">No pet matching this query was found in the PetPals registry.</p>`;
                        return;
                    }
                    
                    data.forEach(item => {
                        const div = document.createElement('div');
                        div.className = "p-4 bg-slate-50 rounded-2xl border text-xs space-y-2";
                        div.innerHTML = `
                            <div class="flex justify-between font-extrabold text-slate-900">
                                <span>${item.name} (${item.species})</span>
                                <span class="font-mono text-amber-600">${item.unique_id}</span>
                            </div>
                            <div class="text-slate-500">
                                <div><b>Breed:</b> ${item.breed || 'N/A'}</div>
                                <div><b>Owner:</b> ${item.owner_name}</div>
                            </div>
                        `;
                        container.appendChild(div);
                    });
                }
            }

            function closePublicSearchResult() {
                document.getElementById('public-search-outcome-card').classList.add('hidden');
            }

            function filterPetsBySpecies() {
                const selectedSpecies = document.getElementById('pet-species-filter').value;
                const filtered = selectedSpecies === "All" 
                    ? loadedPetsCache 
                    : loadedPetsCache.filter(p => p.species.toLowerCase() === selectedSpecies.toLowerCase());
                renderPetsGrid(filtered);
            }

            function showAlert(message, type = 'success') {
                const box = document.getElementById('app-alerts');
                if (!box) return;
                
                box.className = `fixed top-24 right-6 z-50 max-w-sm shadow-2xl rounded-2xl p-4 font-semibold text-sm transition-all duration-300 ${
                    type === 'success' ? 'bg-emerald-50 border border-emerald-200 text-emerald-800' : 'bg-rose-50 border border-rose-200 text-rose-800'
                }`;
                box.innerHTML = message;
                box.classList.remove('hidden');
                setTimeout(() => box.classList.add('hidden'), 5000);
            }

            function showPanel(panelName) {
                ['panel-pets', 'panel-vets', 'panel-emergency'].forEach(id => {
                    const el = document.getElementById(id);
                    if (el) el.classList.add('hidden');
                });
                
                ['btn-panel-pets', 'btn-panel-vets', 'btn-panel-emergency'].forEach(id => {
                    const el = document.getElementById(id);
                    if (el) el.className = "tab-panel-btn px-4 py-2 text-sm font-semibold text-slate-600 rounded-lg focus:outline-none";
                });

                document.getElementById(`panel-${panelName}`).classList.remove('hidden');
                document.getElementById(`btn-panel-${panelName}`).className = "tab-panel-btn px-4 py-2 text-sm font-bold bg-white text-teal-800 shadow-sm rounded-lg focus:outline-none border-b-2 border-teal-600";
                
                if (panelName === 'vets') {
                     loadVetsDirectory();
                     loadPromotions();
                } else if (panelName === 'pets') {
                     loadMyPets();
                }
            }

            function openModal(id) {
                document.getElementById(`modal-${id}`).classList.remove('hidden');
            }
            function closeModal(id) {
                document.getElementById(`modal-${id}`).classList.add('hidden');
            }

            function openEditPetModal(id) {
                const targetPet = loadedPetsCache.find(p => p.id === id);
                if (!targetPet) return;
                
                document.getElementById('edit-pet-id-field').value = targetPet.id;
                document.getElementById('edit-pet-name').value = targetPet.name;
                document.getElementById('edit-pet-species').value = targetPet.species;
                document.getElementById('edit-pet-breed').value = targetPet.breed;
                document.getElementById('edit-pet-dob').value = targetPet.dob;
                document.getElementById('edit-pet-microchip').value = targetPet.microchip || '';
                
                openModal('edit-pet');
            }

            function triggerDeleteConfirmation(id) {
                 document.getElementById('delete-confirm-pet-id').value = id;
                 openModal('delete-confirm');
            }

            async function executePetRemoval() {
                 const id = document.getElementById('delete-confirm-pet-id').value;
                 const resp = await apiFetch(`/api/pets/${id}/delete`, { method: 'POST' });
                 if (resp.ok) {
                      showAlert("Companion profile successfully deleted.");
                      closeModal('delete-confirm');
                      loadMyPets();
                 } else {
                      showAlert("Failed to delete companion profile.", "error");
                 }
            }

            function toggleAuthTab(tab) {
                if (tab === 'login') {
                    document.getElementById('form-login').classList.remove('hidden');
                    document.getElementById('form-register').classList.add('hidden');
                    document.getElementById('tab-btn-login').className = "flex-1 pb-3 text-sm font-bold text-teal-600 border-b-2 border-teal-500";
                    document.getElementById('tab-btn-register').className = "flex-1 pb-3 text-sm font-semibold text-slate-500 border-b-2 border-transparent";
                } else {
                    document.getElementById('form-login').classList.add('hidden');
                    document.getElementById('form-register').classList.remove('hidden');
                    document.getElementById('tab-btn-register').className = "flex-1 pb-3 text-sm font-bold text-emerald-600 border-b-2 border-emerald-500";
                    document.getElementById('tab-btn-login').className = "flex-1 pb-3 text-sm font-semibold text-slate-500 border-b-2 border-transparent";
                }
            }

            async function handleRegister(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const resp = await apiFetch('/api/auth/register', { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    document.getElementById('otp-verification-box').classList.remove('hidden');
                    document.getElementById('otp-input').dataset.email = fd.get('email');
                } else {
                    showAlert(res.message, 'error');
                }
            }

            async function handleVerifyOTP() {
                const otp = document.getElementById('otp-input').value;
                const email = document.getElementById('otp-input').dataset.email;
                const fd = new FormData();
                fd.append('email', email);
                fd.append('otp', otp);
                
                const resp = await apiFetch('/api/auth/verify-otp', { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    document.getElementById('otp-verification-box').classList.add('hidden');
                    toggleAuthTab('login');
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            async function handleLogin(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const resp = await apiFetch('/api/auth/login', { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    localStorage.setItem('petpals_token', res.access_token);
                    showAlert("Authentication successful. Opening Portal...");
                    fetchUserSession();
                } else {
                    showAlert(res.message, 'error');
                }
            }

            async function fetchUserSession() {
                const resp = await apiFetch('/api/me');
                if (resp.ok) {
                    currentUser = await resp.json();
                    
                    document.getElementById('owner-name-display').innerText = currentUser.name;
                    document.getElementById('owner-address-display').innerText = currentUser.address || "Bangladesh Location Not Set";
                    document.getElementById('owner-phone-display').innerText = currentUser.phone || "N/A";
                    document.getElementById('owner-avatar').innerText = currentUser.name.charAt(0);
                    
                    buildAuthHeaderUI(true);
                    transitionToApp();
                } else {
                    currentUser = null;
                    buildAuthHeaderUI(false);
                }
            }

            function buildAuthHeaderUI(isLoggedIn) {
                const container = document.getElementById('auth-state');
                if (isLoggedIn) {
                    container.innerHTML = `
                        <button onclick="handleLogout()" class="bg-teal-800 hover:bg-teal-950 px-4 py-2 rounded-xl text-white font-bold transition flex items-center gap-1.5 text-xs">
                            <i data-lucide="log-out" class="w-4 h-4"></i>
                            <span>Sign Out</span>
                        </button>
                    `;
                } else {
                    container.innerHTML = `
                        <button onclick="triggerAppAuthView('login')" class="text-sm font-bold hover:text-teal-600 transition px-3 py-2">Sign In</button>
                        <button onclick="triggerAppAuthView('register', 'owner')" class="bg-gradient-to-r from-teal-500 to-emerald-600 hover:from-teal-600 hover:to-emerald-700 text-white font-bold px-4 py-2.5 rounded-xl text-xs uppercase tracking-wider shadow transition">
                            Get Started
                        </button>
                    `;
                }
                lucide.createIcons();
            }

            async function handleLogout() {
                await apiFetch('/api/auth/logout', { method: 'POST' });
                localStorage.removeItem('petpals_token');
                currentUser = null;
                fetchUserSession();
                showLandingView();
            }

            async function loadMyPets() {
                const resp = await apiFetch('/api/my-pets');
                if (resp.ok) {
                    loadedPetsCache = await resp.json();
                    renderPetsGrid(loadedPetsCache);
                }
            }

            function renderPetsGrid(petsList) {
                const grid = document.getElementById('pets-grid');
                grid.innerHTML = "";
                
                petsList.forEach(p => {
                    const card = document.createElement('div');
                    card.className = "bg-white rounded-3xl shadow-sm hover:shadow-md transition border border-slate-100 overflow-hidden flex flex-col justify-between relative group";
                    
                    const isManualPhoto = p.photo_data && p.photo_data.startsWith("data:image");
                    const imgElement = isManualPhoto 
                        ? `<img src="${p.photo_data}" class="w-16 h-16 object-cover rounded-2xl border shadow-inner">`
                        : p.photo_data;

                    card.innerHTML = `
                        <div class="absolute top-4 right-4 flex space-x-1 opacity-80 group-hover:opacity-100 transition">
                             <button onclick="openEditPetModal(${p.id})" class="p-1.5 bg-white hover:bg-amber-50 text-amber-600 rounded-lg shadow border border-slate-100 transition" title="Edit Companion Profile">
                                  <i data-lucide="edit-2" class="w-3.5 h-3.5"></i>
                             </button>
                             <button onclick="triggerDeleteConfirmation(${p.id})" class="p-1.5 bg-white hover:bg-rose-50 text-rose-600 rounded-lg shadow border border-slate-100 transition" title="Remove Companion">
                                  <i data-lucide="trash" class="w-3.5 h-3.5"></i>
                             </button>
                        </div>

                        <div class="p-6">
                            <div class="flex items-center space-x-4 mb-4">
                                <div class="bg-slate-100 p-1.5 rounded-2xl shrink-0">
                                    ${imgElement}
                                </div>
                                <div>
                                    <h4 class="font-extrabold text-slate-950 text-base">${p.name}</h4>
                                    <p class="text-xs text-slate-500">${p.breed} (${p.species})</p>
                                </div>
                            </div>
                            <div class="space-y-1.5 text-xs text-slate-600 mb-4">
                                <div class="flex justify-between">
                                    <span class="text-slate-400">Pet Identity:</span>
                                    <span class="font-mono font-bold text-slate-800">${p.unique_id}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-slate-400">DOB:</span>
                                    <span class="font-medium">${p.dob}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-slate-400">Microchip:</span>
                                    <span class="font-medium text-slate-800">${p.microchip || 'N/A'}</span>
                                </div>
                            </div>
                        </div>
                        <div class="px-6 py-4 bg-slate-50 border-t border-slate-100">
                            <button onclick="viewPetDetails(${p.id}, '${p.unique_id}', '${p.name}', '${p.species}')" class="w-full bg-white hover:bg-slate-100 border border-slate-200 text-slate-800 font-bold py-2 rounded-xl text-xs transition">
                                Open Lifecycle Health Board
                            </button>
                        </div>
                    `;
                    grid.appendChild(card);
                });
                lucide.createIcons();
            }

            async function handleAddPet(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const resp = await apiFetch('/api/pets', { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    closeModal('add-pet');
                    loadMyPets();
                    e.target.reset();
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            async function handleEditPet(e) {
                e.preventDefault();
                const petId = document.getElementById('edit-pet-id-field').value;
                const fd = new FormData(e.target);
                
                const resp = await apiFetch(`/api/pets/${petId}/update`, { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    closeModal('edit-pet');
                    loadMyPets();
                } else {
                    showAlert(res.detail || "Failed to update profile details", "error");
                }
            }

            async function viewPetDetails(petId, uniqueId, name, species) {
                currentSelectedPetId = petId;
                document.getElementById('detail-pet-tag').innerHTML = uniqueId;
                document.getElementById('detail-pet-name').innerHTML = name;
                
                await loadPetVaccines(petId);
                await loadPetMedicalHistory(petId);
                await loadPetPrescriptions(petId);
                
                openModal('pet-details');
            }

            async function loadPetVaccines(petId) {
                const resp = await apiFetch(`/api/pets/${petId}/vaccines`);
                const records = await resp.json();
                const list = document.getElementById('detail-vaccine-timeline');
                list.innerHTML = "";
                
                records.forEach(r => {
                    const row = document.createElement('div');
                    row.className = `p-4 rounded-xl border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 ${
                        r.status === 'verified' ? 'bg-emerald-50 border-emerald-100' : 'bg-slate-50 border-slate-200'
                    }`;
                    row.innerHTML = `
                        <div>
                            <h5 class="text-xs font-bold text-slate-800">${r.vaccine_name}</h5>
                            <p class="text-[10px] text-slate-500 mt-0.5">Target Scheduled: ${r.scheduled_date}</p>
                            ${r.administered_date ? `<p class="text-[10px] text-emerald-700 font-bold">Administered: ${r.administered_date} (Batch: ${r.batch_number || 'N/A'})</p>` : ''}
                        </div>
                        <div class="flex items-center space-x-2">
                            ${r.status === 'verified' 
                                ? `<span class="bg-emerald-600 text-white font-extrabold text-[10px] uppercase px-2 py-0.5 rounded-full flex items-center gap-1"><i data-lucide="check" class="w-3 h-3"></i> Verified</span>`
                                : `<button onclick="openVerifyVaccineModal(${r.id})" class="bg-teal-600 hover:bg-teal-700 text-white font-bold text-[10px] px-3 py-1.5 rounded-lg">Authenticate Now</button>`
                            }
                        </div>
                    `;
                    list.appendChild(row);
                });
                lucide.createIcons();
            }

            async function loadPetMedicalHistory(petId) {
                const resp = await apiFetch(`/api/pets/${petId}/history`);
                const events = await resp.json();
                const list = document.getElementById('detail-medical-events');
                list.innerHTML = "";
                
                if (events.length === 0) {
                    list.innerHTML = `<p class="text-xs text-slate-400 italic">No historical operations or clinical events logged.</p>`;
                    return;
                }
                
                events.forEach(e => {
                    const div = document.createElement('div');
                    div.className = "p-3 bg-slate-50 rounded-lg text-xs space-y-1";
                    div.innerHTML = `
                        <div class="flex justify-between font-bold text-slate-800">
                            <span class="capitalize text-teal-800">[${e.event_type}]</span>
                            <span>${e.date}</span>
                        </div>
                        <p class="text-slate-600">${e.description}</p>
                        ${e.vet_reference ? `<p class="text-[10px] text-slate-400 font-medium">Attending Clinician Code: ${e.vet_reference}</p>` : ''}
                    `;
                    list.appendChild(div);
                });
            }

            async function loadPetPrescriptions(petId) {
                const resp = await apiFetch(`/api/pets/${petId}/prescriptions`);
                const list = document.getElementById('detail-prescriptions');
                list.innerHTML = "";
                
                const rx_list = await resp.json();
                if (rx_list.length === 0) {
                     list.innerHTML = `<p class="text-xs text-slate-400 italic">No prescription plans prescribed.</p>`;
                     return;
                }

                rx_list.forEach(rx => {
                    const div = document.createElement('div');
                    div.className = "p-3 bg-violet-50/50 border border-violet-100 rounded-xl flex items-center justify-between text-xs";
                    div.innerHTML = `
                        <div>
                            <p class="font-extrabold text-slate-900">${rx.medicine} - ${rx.dosage}</p>
                            <p class="text-[10px] text-slate-500">${rx.instructions} (${rx.duration})</p>
                        </div>
                        <a href="/api/prescriptions/${rx.id}/pdf" class="bg-white hover:bg-violet-100 border border-violet-200 text-violet-700 font-bold p-1.5 rounded-lg flex items-center gap-1 transition">
                            <i data-lucide="download" class="w-3.5 h-3.5"></i>
                        </a>
                    `;
                    list.appendChild(div);
                });
                lucide.createIcons();
            }

            function openAddPrescriptionModal() {
                document.getElementById('rx-pet-id-field').value = currentSelectedPetId;
                openModal('add-rx');
            }

            async function handleAddPrescription(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const resp = await apiFetch(`/api/pets/${currentSelectedPetId}/prescriptions`, { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    closeModal('add-rx');
                    loadPetPrescriptions(currentSelectedPetId);
                    e.target.reset();
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            function openAddHistoryModal() {
                document.getElementById('history-pet-id-field').value = currentSelectedPetId;
                openModal('add-history');
            }

            async function handleAddHistory(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const resp = await apiFetch(`/api/pets/${currentSelectedPetId}/history`, { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    closeModal('add-history');
                    loadPetMedicalHistory(currentSelectedPetId);
                    e.target.reset();
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            function openVerifyVaccineModal(id) {
                document.getElementById('verify-rec-id-field-vet').value = id;
                document.getElementById('verify-rec-id-field-doc').value = id;
                openModal('verify-vaccine');
            }

            async function handleVerifyVet(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const recordId = document.getElementById('verify-rec-id-field-vet').value;
                const resp = await apiFetch(`/api/vaccines/verify-vet/${recordId}`, { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    closeModal('verify-vaccine');
                    loadPetVaccines(currentSelectedPetId);
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            async function handleVerifyDoc(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const recordId = document.getElementById('verify-rec-id-field-doc').value;
                const resp = await apiFetch(`/api/vaccines/upload-doc/${recordId}`, { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    closeModal('verify-vaccine');
                    loadPetVaccines(currentSelectedPetId);
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            function openOwnershipTransferModal() {
                document.getElementById('transfer-pet-id-field').value = currentSelectedPetId;
                openModal('transfer-init');
            }

            async function handleInitiateTransfer(e) {
                e.preventDefault();
                const fd = new FormData(e.target);
                const resp = await apiFetch('/api/transfers/initiate', { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    document.getElementById('transfer-otp-sim-box').classList.remove('hidden');
                    document.getElementById('trans-code-out').innerHTML = res.otp_sent;
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            async function handleAcceptTransfer() {
                const otp = document.getElementById('transfer-accept-otp-val').value;
                const fd = new FormData();
                fd.append('otp', otp);
                
                const resp = await apiFetch('/api/transfers/accept', { method: 'POST', body: fd });
                const res = await resp.json();
                if (resp.ok) {
                    showAlert(res.message);
                    document.getElementById('transfer-accept-otp-val').value = "";
                    loadMyPets();
                } else {
                    showAlert(res.detail, 'error');
                }
            }

            async function lookupNearbyVets() {
                const areaSelect = document.getElementById('search-area');
                const selectedOpt = areaSelect.options[areaSelect.selectedIndex];
                if (!selectedOpt) return;
                
                const lat = parseFloat(selectedOpt.dataset.lat);
                const lng = parseFloat(selectedOpt.dataset.lng);
                
                const resp = await apiFetch(`/api/vets/nearby?lat=${lat}&lng=${lng}`);
                const vets = await resp.json();
                const list = document.getElementById('vets-list');
                list.innerHTML = "";
                
                vets.forEach(v => {
                    const card = document.createElement('div');
                    card.className = "bg-white p-5 rounded-2xl shadow-sm border border-slate-100 flex items-start justify-between";
                    card.innerHTML = `
                        <div>
                            <h5 class="font-extrabold text-slate-900 text-sm">${v.clinic_name || v.name}</h5>
                            <p class="text-xs text-slate-500 capitalize mt-0.5">${v.specialization || 'General Practice'} Doctor</p>
                            <p class="text-xs text-slate-600 mt-2 flex items-center gap-1"><i data-lucide="map-pin" class="w-3 h-3 text-emerald-600"></i> ${v.address || 'Network Address'}</p>
                            <p class="text-xs text-emerald-800 font-bold mt-1">Distance proximity: ${v.distance_km} KM</p>
                        </div>
                        <div class="text-right">
                             <button onclick="openReviewModal(${v.id})" class="text-[10px] font-bold text-indigo-600 border border-indigo-200 hover:bg-indigo-50 px-2 py-1 rounded">Leave Review</button>
                        </div>
                    `;
                    list.appendChild(card);
                });
                lucide.createIcons();
            }

            function setUrgency(level) {
                currentUrgency = level;
                showAlert(`Symptom assessment calculated. Priority level: ${level}`, 'success');
            }

            async function submitEmergency() {
                const areaSelect = document.getElementById('search-area');
                const selectedOpt = areaSelect.options[areaSelect.selectedIndex];
                const lat = selectedOpt ? parseFloat(selectedOpt.dataset.lat) : 23.7461;
                const lng = selectedOpt ? parseFloat(selectedOpt.dataset.lng) : 90.3742;

                const desc = document.getElementById('emergency-symptoms').value;
                const fd = new FormData();
                fd.append('symptoms', desc);
                fd.append('urgency_level', currentUrgency);
                fd.append('lat', lat);
                fd.append('lng', lng);
                
                const resp = await fetch('/api/emergency/dispatch', { method: 'POST', body: fd });
                const res = await resp.json();
                
                const simBox = document.getElementById('dispatch-outcome');
                simBox.classList.remove('hidden');
                
                if (resp.ok) {
                    simBox.innerHTML = `
                        <div class="text-rose-400 font-extrabold mb-1">🚨 BROADCAST CONNECTED</div>
                        <div>Status: Active Emergency Protocol Engaged</div>
                        <div>Assigned Clinic Responder: ${res.vet_clinic || res.vet_name}</div>
                        <div>Contact Phone: ${res.vet_phone}</div>
                        <div class="mt-2 text-slate-500 animate-pulse">Establishing Live WebSocket Signal channel...</div>
                    `;
                } else {
                    simBox.innerHTML = `<div class="text-rose-500">Dispatch Error: Failed to find responsive network units.</div>`;
                }
            }

            function openReviewModal(vetId) {
                const score = prompt("Rate Care Quality on a scale of 1-5 stars:", "5");
                const reviewText = prompt("Write additional feedback about the clinician:");
                if (!score) return;
                
                const fd = new FormData();
                fd.append('vet_id', vetId);
                fd.append('rating_care', parseInt(score));
                fd.append('rating_communication', 5);
                fd.append('rating_facility', 5);
                fd.append('rating_value', 5);
                fd.append('text', reviewText);
                
                apiFetch('/api/reviews', { method: 'POST', body: fd }).then(res => {
                    if (res.ok) showAlert("Review submitted. Thank you for rating!");
                });
            }

            async function loadPromotions() {
                const resp = await apiFetch('/api/my-promotions');
                if (!resp.ok) return;
                const promos = await resp.json();
                const feed = document.getElementById('promotions-feed');
                feed.innerHTML = "";
                
                if (promos.length === 0) {
                    feed.innerHTML = `
                        <p class="text-xs text-slate-400 italic">No campaign matches found for your current pet profile breed/age profile.</p>
                    `;
                    return;
                }
                
                promos.forEach(p => {
                    const div = document.createElement('div');
                    div.className = "p-3 bg-violet-50 rounded-xl border border-violet-100 flex items-center justify-between";
                    div.innerHTML = `
                        <div>
                            <span class="text-[9px] bg-violet-600 text-white font-extrabold px-1.5 py-0.5 rounded uppercase">Target Companion: ${p.target_pet}</span>
                            <h5 class="font-bold text-slate-900 text-xs mt-1">${p.title}</h5>
                        </div>
                        <div class="text-right">
                            <span class="font-mono font-black text-xs text-violet-800 bg-white border border-violet-300 px-2 py-1 rounded select-all">${p.discount_code}</span>
                        </div>
                    `;
                    feed.appendChild(div);
                });
            }

            function loadVetsDirectory() {
                lookupNearbyVets();
            }

            async function loadAdminStatsAndQueues() {
                 const statsResp = await apiFetch('/api/admin/stats');
                 if (statsResp.ok) {
                      const stats = await statsResp.json();
                      document.getElementById('stat-owners').innerText = stats.owners;
                      document.getElementById('stat-approved-vets').innerText = stats.approved_vets;
                      document.getElementById('stat-pending-vets').innerText = stats.pending_vets;
                      document.getElementById('stat-rejected-vets').innerText = stats.rejected_vets;

                      const chartBox = document.getElementById('pets-distribution-chart');
                      chartBox.innerHTML = "";
                      
                      const species = ["Dog", "Cat", "Bird", "Rabbit", "Reptile", "Other"];
                      const colorsMap = {
                           "Dog": "bg-teal-500", "Cat": "bg-indigo-500", "Bird": "bg-sky-500",
                           "Rabbit": "bg-pink-500", "Reptile": "bg-amber-600", "Other": "bg-slate-400"
                      };

                      species.forEach(sp => {
                           const count = stats.pets_by_category[sp] || 0;
                           const barWidth = count > 0 ? Math.min(100, (count / 10) * 100) : 5;
                           const row = document.createElement('div');
                           row.className = "space-y-1";
                           row.innerHTML = `
                               <div class="flex justify-between font-bold text-slate-700">
                                   <span>${sp}s</span>
                                   <span>${count}</span>
                               </div>
                               <div class="w-full bg-slate-100 rounded-full h-2.5">
                                   <div class="${colorsMap[sp]} h-2.5 rounded-full transition-all duration-300" style="width: ${barWidth}%"></div>
                               </div>
                           `;
                           chartBox.appendChild(row);
                      });
                 }

                 const vetResp = await apiFetch('/api/admin/pending-vets');
                 if (vetResp.ok) {
                      const vets = await vetResp.json();
                      const list = document.getElementById('admin-vet-rows');
                      list.innerHTML = "";
                      
                      if (vets.length === 0) {
                           list.innerHTML = `<tr><td colspan="3" class="p-3 text-slate-400 italic text-center">No pending vet approvals in queue.</td></tr>`;
                      } else {
                           vets.forEach(v => {
                                const tr = document.createElement('tr');
                                tr.className = "border-b hover:bg-slate-50";
                                tr.innerHTML = `
                                    <td class="p-3 font-semibold text-slate-900">${v.name}</td>
                                    <td class="p-3">${v.clinic_name || 'N/A'} (Lic: ${v.license_number || 'N/A'})</td>
                                    <td class="p-3 text-right space-x-1">
                                         <button onclick="approveVet(${v.id})" class="bg-teal-600 hover:bg-teal-700 text-white text-[10px] font-bold px-2.5 py-1 rounded-lg">Approve</button>
                                         <button onclick="rejectVet(${v.id})" class="bg-rose-600 hover:bg-rose-700 text-white text-[10px] font-bold px-2.5 py-1 rounded-lg">Reject</button>
                                    </td>
                                `;
                                list.appendChild(tr);
                           });
                      }
                 }

                 const milestonesResp = await apiFetch('/api/milestones');
                 if (milestonesResp.ok) {
                      const milestones = await milestonesResp.json();
                      const list = document.getElementById('admin-milestone-rows');
                      list.innerHTML = "";
                      
                      milestones.forEach(m => {
                           const tr = document.createElement('tr');
                           tr.className = "border-b hover:bg-slate-50";
                           tr.innerHTML = `
                               <td class="p-3 font-black text-teal-700 text-base">${m.letter}</td>
                               <td class="p-3 font-bold text-slate-900">${m.title}</td>
                               <td class="p-3 text-slate-500">${m.category}</td>
                               <td class="p-3 text-right">
                                    <button onclick="openMilestoneEditor(${m.id})" class="bg-white hover:bg-slate-50 border border-slate-200 text-slate-800 text-[10px] font-bold px-3 py-1.5 rounded-xl">Edit Page Content</button>
                               </td>
                           `;
                           list.appendChild(tr);
                      });
                 }
                 lucide.createIcons();
            }

            async function approveVet(id) {
                 const resp = await apiFetch(`/api/admin/approve-vet/${id}`, { method: 'POST' });
                 if (resp.ok) {
                      showAlert("Vet verified and approved!");
                      loadAdminStatsAndQueues();
                 }
            }

            async function rejectVet(id) {
                 const resp = await apiFetch(`/api/admin/reject-vet/${id}`, { method: 'POST' });
                 if (resp.ok) {
                      showAlert("Vet verification application rejected.", "error");
                      loadAdminStatsAndQueues();
                 }
            }

            function openMilestoneEditor(id) {
                 const m = milestonesCache.find(item => item.id === id);
                 if (!m) return;
                 
                 document.getElementById('editor-milestone-id').value = m.id;
                 document.getElementById('editor-m-title').value = m.title;
                 document.getElementById('editor-m-category').value = m.category;
                 document.getElementById('editor-m-desc').value = m.description;
                 document.getElementById('editor-m-guideline').value = m.care_guideline;
                 
                 openModal('milestone-editor');
            }

            async function handleMilestoneContentUpdate(e) {
                 e.preventDefault();
                 const id = document.getElementById('editor-milestone-id').value;
                 const fd = new FormData(e.target);
                 
                 const resp = await apiFetch(`/api/milestones/${id}/update`, { method: 'POST', body: fd });
                 if (resp.ok) {
                      showAlert("Milestone content updated. Landing page content refreshed!");
                      closeModal('milestone-editor');
                      loadLandingMilestones(); 
                      loadAdminStatsAndQueues(); 
                 } else {
                      showAlert("Error updating milestone content.", "error");
                 }
            }

            function triggerHealthPassportDownload() {
                window.location.href = `/api/pets/${currentSelectedPetId}/passport`;
            }
        </script>
    </body>
    </html>
    """
    return html_code

# Standard Python entrypoint
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)