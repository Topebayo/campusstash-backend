import os
import time
import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, HTMLResponse, Response, RedirectResponse
import base64
import uuid
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.limiter import limiter, rate_limit_exceeded_handler

# Set up structured logger
logger = logging.getLogger("campusstash")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs('static/uploads', exist_ok=True)
    try:
        from app.db.base import Base
        from app.db.session import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            try:
                await conn.execute(text("SET lock_timeout = '2s';"))
            except Exception:
                pass
            # Enable pgcrypto for password/PIN hashing in Postgres / Supabase
            try:
                await conn.execute(text('CREATE EXTENSION IF NOT EXISTS pgcrypto;'))
            except Exception:
                pass

            await conn.run_sync(Base.metadata.create_all)

            try:
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_phone_verified BOOLEAN DEFAULT FALSE;'))
            except Exception:
                try:
                    await conn.execute(text('ALTER TABLE user ADD COLUMN is_phone_verified BOOLEAN DEFAULT 0;'))
                except Exception:
                    pass
            try:
                await conn.execute(text('ALTER TABLE listing ALTER COLUMN image_url TYPE TEXT;'))
                await conn.execute(text('ALTER TABLE listing_image ALTER COLUMN image_url TYPE TEXT;'))
            except Exception:
                pass

            try:
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS campus_id UUID;'))
                await conn.execute(text('ALTER TABLE listing ADD COLUMN IF NOT EXISTS campus_id UUID;'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS account_type VARCHAR(50) DEFAULT \'individual\';'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS business_name VARCHAR(255);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS business_category VARCHAR(255);'))
                await conn.execute(text('ALTER TABLE "user" ALTER COLUMN business_category TYPE VARCHAR(255);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS business_services TEXT;'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS business_description VARCHAR(500);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS business_address VARCHAR(255);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS business_whatsapp VARCHAR(50);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS business_instagram VARCHAR(100);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_status VARCHAR(50) DEFAULT \'unverified\';'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_student_id VARCHAR(100);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_id_card_url VARCHAR(500);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_submitted_at TIMESTAMP WITH TIME ZONE;'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_verified_at TIMESTAMP WITH TIME ZONE;'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_user_type VARCHAR(50) DEFAULT \'student\';'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_id_type VARCHAR(50);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_id_number VARCHAR(100);'))
                await conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS kyc_selfie_url TEXT;'))
                await conn.execute(text('ALTER TABLE "user" ALTER COLUMN kyc_id_card_url TYPE TEXT;'))
            except Exception:
                try:
                    await conn.execute(text('ALTER TABLE user ADD COLUMN account_type VARCHAR(50) DEFAULT \'individual\';'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN business_name VARCHAR(255);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN business_category VARCHAR(100);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN business_description VARCHAR(500);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN business_address VARCHAR(255);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN business_whatsapp VARCHAR(50);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN business_instagram VARCHAR(100);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN kyc_status VARCHAR(50) DEFAULT \'unverified\';'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN kyc_student_id VARCHAR(100);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN kyc_id_card_url VARCHAR(500);'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN kyc_submitted_at TIMESTAMP;'))
                    await conn.execute(text('ALTER TABLE user ADD COLUMN kyc_verified_at TIMESTAMP;'))
                except Exception:
                    pass
        logger.info("Database schemas verified and synchronized successfully.")

        # Seed campuses if not already seeded or update Ibadan, Osun, and Lagos locations
        try:
            from app.db.session import AsyncSessionLocal
            from app.models.campus import Campus
            from sqlalchemy import select, update
            async with AsyncSessionLocal() as session:
                # Migrate generic UNIOSUN to UNIOSUN Osogbo Campus so existing students map smoothly
                try:
                    await session.execute(text("""
                        UPDATE campus 
                        SET name = 'Osun State University, Osogbo Campus', 
                            acronym = 'UNIOSUN OSOGBO' 
                        WHERE acronym = 'UNIOSUN' OR name = 'Osun State University';
                    """))
                    await session.commit()
                except Exception:
                    pass

                # Auto-repair and normalize existing WhatsApp numbers for campus stores
                try:
                    await session.execute(text("""
                        UPDATE "user" 
                        SET business_whatsapp = '+234' || SUBSTRING(business_whatsapp FROM 2)
                        WHERE business_whatsapp LIKE '0%' AND LENGTH(REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g')) = 11;
                    """))
                    await session.execute(text("""
                        UPDATE "user" 
                        SET business_whatsapp = '+234' || SUBSTRING(business_whatsapp FROM 3)
                        WHERE business_whatsapp LIKE '+0%' AND LENGTH(REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g')) = 11;
                    """))
                    await session.execute(text("""
                        UPDATE "user" 
                        SET business_whatsapp = '+234' || SUBSTRING(REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g') FROM 5)
                        WHERE REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g') LIKE '2340%' AND LENGTH(REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g')) = 14;
                    """))
                    await session.execute(text("""
                        UPDATE "user" 
                        SET business_whatsapp = '+' || REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g')
                        WHERE REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g') LIKE '234%' 
                          AND LENGTH(REGEXP_REPLACE(business_whatsapp, '[^0-9]', '', 'g')) = 13
                          AND NOT business_whatsapp LIKE '+%';
                    """))
                    await session.execute(text("""
                        UPDATE "user" 
                        SET business_whatsapp = NULL
                        WHERE business_whatsapp LIKE '%@%';
                    """))
                    await session.commit()
                except Exception as e:
                    logger.warning(f"WhatsApp migration skipped or failed: {e}")

                existing_res = await session.execute(select(Campus))
                existing_campuses = {c.name.strip().lower(): c for c in existing_res.scalars().all()}

                all_target_campuses = [
                    # IBADAN CLUSTER
                    {
                        "name": "Lead City University",
                        "acronym": "LCU",
                        "state": "Ibadan",
                        "popular_hostels": ["Achievers Hall", "Peace Hall", "Senate Hall", "Honours Hall"]
                    },
                    {
                        "name": "University of Ibadan",
                        "acronym": "UI",
                        "state": "Ibadan",
                        "popular_hostels": ["Kuti", "Bello", "Queen Idia", "Awo", "Tedder", "Mellanby", "Zik", "Abadina", "Agbowo"]
                    },
                    {
                        "name": "The Polytechnic, Ibadan",
                        "acronym": "Poly Ibadan",
                        "state": "Ibadan",
                        "popular_hostels": ["North Campus", "South Campus", "Apete", "Middle Gate", "Ologuneru"]
                    },
                    {
                        "name": "KolaDaisi University",
                        "acronym": "KDU",
                        "state": "Ibadan",
                        "popular_hostels": ["KM 18 Ibadan-Oyo Expressway", "Kings Hall", "Queens Hall"]
                    },
                    {
                        "name": "First Technical University",
                        "acronym": "Tech-U",
                        "state": "Ibadan",
                        "popular_hostels": ["University Hostel", "Lagos-Ibadan Expressway", "Alomaja"]
                    },
                    {
                        "name": "Dominican University",
                        "acronym": "DU",
                        "state": "Ibadan",
                        "popular_hostels": ["Dominican Student Hostel", "Samonda", "Poly Road"]
                    },
                    {
                        "name": "Precious Cornerstone University",
                        "acronym": "PCU",
                        "state": "Ibadan",
                        "popular_hostels": ["Grace Hall", "Peace Hall", "Olaogun"]
                    },
                    {
                        "name": "Federal College of Animal Health and Production Technology",
                        "acronym": "FCAH&PT",
                        "state": "Ibadan",
                        "popular_hostels": ["Moor Plantation", "Apata", "College Hostel"]
                    },
                    {
                        "name": "Federal College of Forestry",
                        "acronym": "FCF",
                        "state": "Ibadan",
                        "popular_hostels": ["Jericho Campus", "Forestry Hall", "Dugbe"]
                    },
                    {
                        "name": "Federal School of Statistics",
                        "acronym": "FSS",
                        "state": "Ibadan",
                        "popular_hostels": ["Sango Campus", "UI Road"]
                    },

                    # OSUN STATE (ALL UNIVERSITIES & MAJOR TERTIARY HUBS)
                    {
                        "name": "Obafemi Awolowo University",
                        "acronym": "OAU",
                        "state": "Osun",
                        "popular_hostels": ["Angola", "Mozambique", "Fajuyi", "Awolowo", "Moremi", "Akintola", "ETF", "Maintenance", "Asher"]
                    },
                    {
                        "name": "Osun State University, Osogbo Campus",
                        "acronym": "UNIOSUN OSOGBO",
                        "state": "Osun",
                        "popular_hostels": ["Osogbo Main Campus", "Oke-Baale", "Powerline", "Stadium Area", "Isale-Osun"]
                    },
                    {
                        "name": "Osun State University, Ikire Campus",
                        "acronym": "UNIOSUN IKIRE",
                        "state": "Osun",
                        "popular_hostels": ["Campus Gate", "Oja Ale", "Sanngo", "Naira & Kobo", "College Hostel"]
                    },
                    {
                        "name": "Osun State University, Okuku Campus",
                        "acronym": "UNIOSUN OKUKU",
                        "state": "Osun",
                        "popular_hostels": ["Okuku Central", "Campus Gate", "Odo-Otin Area", "Adeleke Hall"]
                    },
                    {
                        "name": "Osun State University, Ejigbo Campus",
                        "acronym": "UNIOSUN EJIGBO",
                        "state": "Osun",
                        "popular_hostels": ["Agric Campus Hostel", "Ejigbo Town", "Inisa Road", "Oguro"]
                    },
                    {
                        "name": "Osun State University, Ifetedo Campus",
                        "acronym": "UNIOSUN IFETEDO",
                        "state": "Osun",
                        "popular_hostels": ["College of Law Hostel", "Ifetedo Central", "Garage Area", "Barristers Villa"]
                    },
                    {
                        "name": "Osun State University, Ipetu-Ijesha Campus",
                        "acronym": "UNIOSUN IPETU-IJESHA",
                        "state": "Osun",
                        "popular_hostels": ["Education Campus Hostel", "Ipetu Central", "Expressway Junction", "Ijesha Villa"]
                    },
                    {
                        "name": "Federal University of Health Sciences, Ila-Orangun",
                        "acronym": "FUHSI",
                        "state": "Osun",
                        "popular_hostels": ["Main Campus Hostel", "Ila-Orangun Central", "Ajaba Road"]
                    },
                    {
                        "name": "University of Ilesa",
                        "acronym": "UNILESA",
                        "state": "Osun",
                        "popular_hostels": ["Main Campus Hostel", "Imo Area", "Ilesa Central", "Brewery Area"]
                    },
                    {
                        "name": "Bowen University",
                        "acronym": "Bowen",
                        "state": "Osun",
                        "popular_hostels": ["Luke Hall", "Mark Hall", "Matthew Hall", "John Hall", "Dorcas Hall", "Mary Hall", "Iwo Campus"]
                    },
                    {
                        "name": "Redeemer's University",
                        "acronym": "RUN",
                        "state": "Osun",
                        "popular_hostels": ["Queen Esther Hall", "Dorcas Hall", "Deborah Hall", "Daniel Hall", "Paul Hall", "Ede Main Campus"]
                    },
                    {
                        "name": "Adeleke University",
                        "acronym": "AU",
                        "state": "Osun",
                        "popular_hostels": ["Grace Hall", "Wisdom Hall", "Spring of Life", "Ede Campus"]
                    },
                    {
                        "name": "Fountain University",
                        "acronym": "Fountain",
                        "state": "Osun",
                        "popular_hostels": ["Khadijah Hall", "Aisha Hall", "Osogbo Campus", "Oke-Osun"]
                    },
                    {
                        "name": "Oduduwa University",
                        "acronym": "OUI",
                        "state": "Osun",
                        "popular_hostels": ["Ipetumodu Campus", "Prestige Hall", "Royal Hall", "Ife-Ibadan Expressway"]
                    },
                    {
                        "name": "Joseph Ayo Babalola University",
                        "acronym": "JABU",
                        "state": "Osun",
                        "popular_hostels": ["Bethel Hall", "Zion Hall", "Ikeji-Arakeji", "Shiloh Hall"]
                    },
                    {
                        "name": "Kings University",
                        "acronym": "Kings Uni",
                        "state": "Osun",
                        "popular_hostels": ["Ode-Omu Campus", "Alpha Hall", "Omega Hall"]
                    },
                    {
                        "name": "Westland University",
                        "acronym": "Westland",
                        "state": "Osun",
                        "popular_hostels": ["Iwo Main Campus", "Unity Hall", "Progress Hall"]
                    },
                    {
                        "name": "Mercy Medical University",
                        "acronym": "MMU",
                        "state": "Osun",
                        "popular_hostels": ["Iwo Campus", "Clinical Science Hostel"]
                    },
                    {
                        "name": "Federal Polytechnic, Ede",
                        "acronym": "Federal Poly Ede",
                        "state": "Osun",
                        "popular_hostels": ["North Campus", "South Campus", "Agip Area", "Ede Central"]
                    },
                    {
                        "name": "Osun State Polytechnic, Iree",
                        "acronym": "OSPOLY",
                        "state": "Osun",
                        "popular_hostels": ["Koko Area", "Ladoja Area", "Iree Main Campus"]
                    },
                    {
                        "name": "Osun State College of Technology, Esa-Oke",
                        "acronym": "OSCOTECH",
                        "state": "Osun",
                        "popular_hostels": ["Esa-Oke Campus", "Oke-Imesi Road"]
                    },

                    # LAGOS STATE (ALL UNIVERSITIES & MAJOR TERTIARY HUBS)
                    {
                        "name": "University of Lagos",
                        "acronym": "UNILAG",
                        "state": "Lagos",
                        "popular_hostels": ["Jaja", "Moremi", "Mariere", "Eni Njoku", "Madam Tinubu", "Biobaku", "Fagunwa", "New Hall", "Akintunde"]
                    },
                    {
                        "name": "Lagos State University",
                        "acronym": "LASU",
                        "state": "Lagos",
                        "popular_hostels": ["Ojo Main Campus", "Iba", "Igando", "Frankie", "Badagry Expressway", "Epe Campus", "LASUTH Ikeja"]
                    },
                    {
                        "name": "Lagos State University of Science and Technology",
                        "acronym": "LASUSTECH",
                        "state": "Lagos",
                        "popular_hostels": ["Ikorodu Campus", "First Gate", "Lucky Fibre", "Odogunyan", "Isolo Campus"]
                    },
                    {
                        "name": "Lagos State University of Education",
                        "acronym": "LASUED",
                        "state": "Lagos",
                        "popular_hostels": ["Otto/Ijanikin Campus", "Noforija Epe Campus", "Iyana School", "Cele"]
                    },
                    {
                        "name": "Pan-Atlantic University",
                        "acronym": "PAU",
                        "state": "Lagos",
                        "popular_hostels": ["Pearl Hostel", "Amethyst Hostel", "Emerald Hostel", "Ibeju-Lekki", "Eleko"]
                    },
                    {
                        "name": "Caleb University",
                        "acronym": "Caleb",
                        "state": "Lagos",
                        "popular_hostels": ["Imota Campus", "Glory Hall", "Wisdom Hall", "Ikorodu-Itokin Road"]
                    },
                    {
                        "name": "Anchor University",
                        "acronym": "Anchor",
                        "state": "Lagos",
                        "popular_hostels": ["Ayobo Campus", "Paul Hall", "Peter Hall", "Lydia Hall", "Ipaja"]
                    },
                    {
                        "name": "Augustine University",
                        "acronym": "AUL",
                        "state": "Lagos",
                        "popular_hostels": ["Ilara-Epe Campus", "St. Thomas More", "St. Monica"]
                    },
                    {
                        "name": "Eko University of Medicine and Health Sciences",
                        "acronym": "EKOUNIMED",
                        "state": "Lagos",
                        "popular_hostels": ["Ijanikin Campus", "College Hostel", "Vespa Area"]
                    },
                    {
                        "name": "James Hope University",
                        "acronym": "JHU",
                        "state": "Lagos",
                        "popular_hostels": ["Twin Lakes Campus", "Lekki Phase 1", "Victoria Island"]
                    },
                    {
                        "name": "Maranatha University",
                        "acronym": "Maranatha",
                        "state": "Lagos",
                        "popular_hostels": ["Lekki Campus", "Grace Hall", "Faith Hall"]
                    },
                    {
                        "name": "Nigerian University of Technology and Management",
                        "acronym": "NUTM",
                        "state": "Lagos",
                        "popular_hostels": ["Apapa / Victoria Island Campus", "Scholars Hall"]
                    },
                    {
                        "name": "Yaba College of Technology",
                        "acronym": "YABATECH",
                        "state": "Lagos",
                        "popular_hostels": ["Bakassi Hall", "Moshood Abiola", "Postgraduate Hall", "Akata", "Epe Campus"]
                    },
                    {
                        "name": "Federal College of Education (Technical), Akoka",
                        "acronym": "FCET Akoka",
                        "state": "Lagos",
                        "popular_hostels": ["Akoka Campus", "Main Hall", "St. Finbarrs Area"]
                    },

                    # OTHER LAUNCH REGIONS
                    {
                        "name": "Covenant University",
                        "acronym": "CU",
                        "state": "Ogun",
                        "popular_hostels": ["Peter Hall", "Paul Hall", "John Hall", "Esther Hall", "Lydia Hall", "Dorcas Hall"]
                    },
                    {
                        "name": "Babcock University",
                        "acronym": "BU",
                        "state": "Ogun",
                        "popular_hostels": ["Topaz", "Sapphire", "Emerald", "Diamond", "Gideon Troopers", "Welch"]
                    },
                    {
                        "name": "Federal University of Technology, Akure",
                        "acronym": "FUTA",
                        "state": "Ondo",
                        "popular_hostels": ["Jibowu", "Abiola", "Oluwafemi", "Albert", "South Gate", "North Gate"]
                    },
                    {
                        "name": "University of Ilorin",
                        "acronym": "UNILORIN",
                        "state": "Kwara",
                        "popular_hostels": ["Village 1", "Village 2", "Zamfara", "Al-Hikmah", "Tanke Area", "PS"]
                    },
                    {
                        "name": "University of Benin",
                        "acronym": "UNIBEN",
                        "state": "Edo",
                        "popular_hostels": ["Hall 1", "Hall 2", "Hall 3", "Hall 4", "NDDC", "Ugbowo Campus"]
                    }
                ]

                to_insert = []
                for c_data in all_target_campuses:
                    key = c_data["name"].strip().lower()
                    if key not in existing_campuses:
                        to_insert.append(Campus(
                            name=c_data["name"].strip(),
                            acronym=c_data["acronym"].strip().upper(),
                            state=c_data["state"].strip(),
                            popular_hostels=c_data["popular_hostels"]
                        ))
                    else:
                        # Update state and acronym to ensure consistency
                        existing_obj = existing_campuses[key]
                        existing_obj.acronym = c_data["acronym"].strip().upper()
                        existing_obj.state = c_data["state"].strip()
                        if c_data.get("popular_hostels"):
                            existing_obj.popular_hostels = c_data["popular_hostels"]

                if to_insert:
                    session.add_all(to_insert)
                    logger.info(f"Seeding {len(to_insert)} new Nigerian campuses.")
                await session.commit()
                logger.info("Successfully synced all Osun, Lagos, and Ibadan tertiary campuses.")
        except Exception as seed_err:
            logger.warning(f"Campus seeding note: {seed_err}")
    except Exception as e:
        logger.warning(f"Database initialization note: {e}")
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    description='A privacy-first, anonymous campus marketplace.',
    version='1.2.0',
    lifespan=lifespan,
)

# Connect SlowAPI Limiter state & error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Custom Security Headers Middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Prevent iframe clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Cross-Site Scripting protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions Policy
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Enforce HTTPS strictly in non-development environments
        if settings.ENVIRONMENT != "development":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Configure CORS with strict origins whitelist and mobile regex
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|capacitor://localhost|ionic://localhost|https://.*\.onrender\.com|https://.*\.vercel\.app|https://.*\.netlify\.app|https://.*\.railway\.app)$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"]
)

# Global unhandled exception shielding & monitoring
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Log complete traceback on server for monitoring, but hide internal details from client
    logger.error(
        f"[SERVER ERROR] {request.method} {request.url.path}: {exc}\n"
        f"{traceback.format_exc()}"
    )
    headers = {}
    origin = request.headers.get("origin")
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected server error occurred. Incident has been logged for monitoring.",
            "error": "internal_server_error"
        },
        headers=headers
    )

os.makedirs('static/uploads', exist_ok=True)
app.mount('/static', StaticFiles(directory='static'), name='static')

@app.get('/', tags=['Health'])
async def root():
    return {
        'status': 'online',
        'project': settings.PROJECT_NAME,
        'message': 'CampusStash API is running live on Railway.',
        'version': '1.2.0',
        'docs': '/docs',
        'health': '/health'
    }

@app.get('/health', tags=['Health'])
async def health_check():
    db_status = "healthy"
    try:
        from app.db.session import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)[:60]}"

    return {
        'status': 'ok' if db_status == 'healthy' else 'degraded',
        'project': settings.PROJECT_NAME,
        'environment': settings.ENVIRONMENT,
        'version': '1.2.0',
        'features': ['whatsapp_otp', 'email_otp', 'voice_otp', 'forgot_password', 'delete_account'],
        'database': db_status,
        'timestamp': time.time()
    }

from app.api.v1 import auth, listings, dropzones, chat, escrow, users, conversations, campuses, item_requests, whatsapp, admin

# Auth routes (mounted at both /api/auth and /api/v1/auth)
app.include_router(auth.router, prefix='/api/auth', tags=['auth'])
app.include_router(auth.router, prefix='/api/v1/auth', tags=['auth'])

# Listings routes (mounted at both /api/listings and /api/v1/listings)
app.include_router(listings.router, prefix='/api/listings', tags=['listings'])
app.include_router(listings.router, prefix='/api/v1/listings', tags=['listings'])

# Conversations routes (mounted at both /api/conversations and /api/v1/conversations)
app.include_router(conversations.router, prefix='/api/conversations', tags=['conversations'])
app.include_router(conversations.router, prefix='/api/v1/conversations', tags=['conversations'])

# Chat WebSocket and routes (WebSocket mounted at /ws/chat/{conversation_id})
app.include_router(chat.router, tags=['chat'])
app.include_router(chat.router, prefix='/api/v1/chat', tags=['chat'])

app.include_router(dropzones.router, prefix='/api/v1/dropzones', tags=['dropzones'])
app.include_router(escrow.router, prefix='/api/v1/escrow', tags=['escrow'])
app.include_router(users.router, prefix='/api/v1/users', tags=['users'])

# Campuses routes (publicly accessible)
app.include_router(campuses.router, prefix='/api/campuses', tags=['campuses'])
app.include_router(campuses.router, prefix='/api/v1/campuses', tags=['campuses'])

# Wanted Board / Student Item Requests (public browse, auth create)
app.include_router(item_requests.router, prefix='/api/requests', tags=['requests'])
app.include_router(item_requests.router, prefix='/api/v1/requests', tags=['requests'])

# Meta WhatsApp Cloud API (Webhook verification, commands, and inbound/outbound messaging)
app.include_router(whatsapp.router, prefix='/api/whatsapp', tags=['whatsapp'])
app.include_router(whatsapp.router, prefix='/api/v1/whatsapp', tags=['whatsapp'])

# Dedicated Secure Admin API
app.include_router(admin.router, prefix='/api/admin', tags=['admin'])
app.include_router(admin.router, prefix='/api/v1/admin', tags=['admin'])


# ─── Dynamic Open Graph (OG) Tag Injector for WhatsApp & Social Sharing ───
CRAWLER_USER_AGENTS = [
    "facebookexternalhit", "whatsapp", "facebot", "twitterbot", 
    "telegrambot", "linkedinbot", "slackbot", "discordbot", "googlebot", "applebot"
]

@app.get("/l/{listing_id}/image", tags=["Sharing"])
@app.get("/share/{listing_id}/image", tags=["Sharing"])
async def get_listing_preview_image(listing_id: str):
    """
    Serves the listing cover photo as a binary JPEG image response.
    Supports base64 data URLs, local static files, and remote redirects,
    so WhatsApp scrapers can display rich photo thumbnails.
    """
    try:
        lid = uuid.UUID(listing_id)
    except ValueError:
        return Response(status_code=404)

    from app.db.session import AsyncSessionLocal
    from app.models.marketplace import Listing
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Listing).where(Listing.id == lid))
        listing = result.scalar_one_or_none()
        if not listing or not listing.image_url:
            return Response(status_code=404)

        img_str = listing.image_url.strip()
        if img_str.startswith("data:image/"):
            try:
                header, encoded = img_str.split(",", 1)
                media_type = header.split(";")[0].replace("data:", "")
                data = base64.b64decode(encoded)
                return Response(
                    content=data, 
                    media_type=media_type, 
                    headers={"Cache-Control": "public, max-age=86400"}
                )
            except Exception:
                return Response(status_code=500)
        elif img_str.startswith("http://") or img_str.startswith("https://"):
            return RedirectResponse(url=img_str, status_code=302)
        else:
            return Response(status_code=404)


@app.get("/l/{listing_id}", response_class=HTMLResponse, tags=["Sharing"])
@app.get("/share/{listing_id}", response_class=HTMLResponse, tags=["Sharing"])
async def share_listing_preview(request: Request, listing_id: str):
    """
    Dynamic Open Graph (OG) tag injector for WhatsApp, Twitter, and Facebook.
    When crawlers visit, it outputs custom Open Graph meta tags (title, price in Naira, campus, and image).
    When human students visit, it instantly redirects to the React frontend listing view.
    """
    user_agent = (request.headers.get("user-agent") or "").lower()
    is_crawler = any(c in user_agent for c in CRAWLER_USER_AGENTS)

    # Determine frontend target base (Production frontend is at https://campusstash.onrender.com)
    frontend_url = "https://campusstash.onrender.com"

    redirect_target = f"{frontend_url}/?item={listing_id}"

    # If it's a normal human browser and not a crawler, redirect immediately
    if not is_crawler and "debug" not in request.query_params:
        return RedirectResponse(url=redirect_target, status_code=302)

    # For crawlers: fetch listing details from DB
    try:
        lid = uuid.UUID(listing_id)
    except ValueError:
        return RedirectResponse(url=frontend_url, status_code=302)

    from app.db.session import AsyncSessionLocal
    from app.models.marketplace import Listing
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Listing)
            .options(selectinload(Listing.campus))
            .where(Listing.id == lid)
        )
        listing = result.scalar_one_or_none()

    if not listing:
        return RedirectResponse(url=frontend_url, status_code=302)

    # Format price in Naira
    price_val = (listing.price / 100) if listing.price else 0
    price_str = f"₦{price_val:,.0f}" if price_val.is_integer() else f"₦{price_val:,.2f}"
    
    campus_tag = listing.campus.acronym if listing.campus else "Campus"
    title_escaped = listing.title.replace('"', '&quot;').replace('<', '&lt;')
    desc_clean = (listing.description or "View condition and details on CampusStash.").replace('"', '&quot;').replace('\n', ' ')[:160]

    backend_host = str(request.base_url).rstrip("/")
    preview_img_url = f"{backend_host}/l/{listing_id}/image"

    og_title = f"{price_str} • {title_escaped} ({campus_tag})"
    og_desc = f"{desc_clean} — 100% anonymous campus marketplace with safe campus dropzones."

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{og_title}</title>
    
    <!-- Open Graph / WhatsApp / Facebook -->
    <meta property="og:type" content="product" />
    <meta property="og:site_name" content="CampusStash" />
    <meta property="og:title" content="{og_title}" />
    <meta property="og:description" content="{og_desc}" />
    <meta property="og:image" content="{preview_img_url}" />
    <meta property="og:image:width" content="800" />
    <meta property="og:image:height" content="600" />
    <meta property="og:url" content="{backend_host}/l/{listing_id}" />
    
    <!-- Twitter -->
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{og_title}" />
    <meta name="twitter:description" content="{og_desc}" />
    <meta name="twitter:image" content="{preview_img_url}" />

    <!-- Instant client redirect for human users -->
    <meta http-equiv="refresh" content="0; url={redirect_target}" />
</head>
<body style="font-family: sans-serif; text-align: center; padding: 2rem;">
    <script>window.location.replace("{redirect_target}");</script>
    <h2>CampusStash</h2>
    <p>Opening <strong>{title_escaped}</strong> for <strong>{price_str}</strong>...</p>
    <p><a href="{redirect_target}">Click here if not redirected automatically.</a></p>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
