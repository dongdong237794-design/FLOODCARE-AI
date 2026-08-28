"""
FLOODCARE AI - Optimized Bot Configuration
============================================
Architecture: Modular | Class-Based State Machine | Intent Classification
Author: Senior Software Architect
Version: 2.5.1 (Fixed Line Flex Message Spacing Bug)

Key Optimizations:
- Intent Classification: Reduces Gemini API calls by ~80%
- Smart Cache: Multi-layer (Memory LRU > TTL Cache)
- State Machine: Class-based, separated workflows (Only for Location searches)
- Rate Limiting: Per-user request throttling
- Localized Timezone: Standardized Thai timezone (Asia/Bangkok / UTC+7) for all systems
- Custom Water Status Mapping: Uses official Thaiwater status keys with specified hex colors
- Strictly Limited Scope: Only answers floods, safety, and health queries. Refuses all else.
- CONCISE MODE (New): Enforces extremely short responses (max 3 lines) with brief inline source citations.
"""

import os
import json
import math
import time
import random
import hashlib
import datetime
import threading
import functools
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple, Any, Callable

# =============================================================================
# EXTERNAL DEPENDENCIES
# =============================================================================
try:
    import requests
    import urllib.request
except ImportError:
    requests = None

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None

try:
    import gspread
except ImportError:
    gspread = None

try:
    from linebot import LineBotApi, WebhookHandler
    from linebot.models import (
        FlexSendMessage, BubbleContainer, CarouselContainer, BoxComponent, TextComponent,
        SeparatorComponent, ButtonComponent, URIAction, TextSendMessage,
        LocationAction, MessageAction, BubbleStyle, BlockStyle, ImageComponent
    )
except ImportError:
    LineBotApi = None
    WebhookHandler = None


# =============================================================================
# TIMEZONE HELPER (Asia/Bangkok - UTC+7)
# =============================================================================

def get_bangkok_time() -> datetime.datetime:
    """
    Get the current localized Thai datetime (UTC+7 / Asia/Bangkok).
    Guarantees correct timezone mapping even on foreign cloud platforms like Render.
    """
    bangkok_tz = datetime.timezone(datetime.timedelta(hours=7))
    return datetime.datetime.now(bangkok_tz)


# =============================================================================
# SECTION 1: CONFIGURATION & ENVIRONMENT
# =============================================================================

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
TMD_ACCESS_TOKEN = os.environ.get("TMD_ACCESS_TOKEN", "")

# LIFF Configuration
# Default URLs point straight at the production LIFF pages so typing
# 'sos' / 'ขอของ' / 'ลงทะเบียน' works immediately; env vars still override
# these if set (e.g. for a staging deployment on a different domain).
SOS_LIFF_ID = os.environ.get("SOS_LIFF_ID", "2010532052-LWWlJ9M9")
SOS_LIFF_URL = os.environ.get("SOS_LIFF_URL", "https://floodcare-ai-2.onrender.com/liff/sos?liffId=2010532052-LWWlJ9M9")
NEED_LIFF_ID = os.environ.get("NEED_LIFF_ID", "2010532052-7OVUW4Fb")
NEED_LIFF_URL = os.environ.get("NEED_LIFF_URL", "https://floodcare-ai-2.onrender.com/liff/need?liffId=2010532052-7OVUW4Fb")
# NOTE: the register link you sent had "liffld=" (typo) — corrected to "liffId=" here to match the other two.
REGISTER_LIFF_ID = os.environ.get("REGISTER_LIFF_ID", "2010532052-JZ9Fz0Uv")
REGISTER_LIFF_URL = os.environ.get("REGISTER_LIFF_URL", "https://floodcare-ai-2.onrender.com/liff/register?liffId=2010532052-JZ9Fz0Uv")
# TRACK_LIFF_ID has no hardcoded default — unlike the other three LIFF apps
# above, this one doesn't exist yet in the LINE Developers console. The
# /liff/track page works fine without it (manual case-ID entry, like a
# shipping tracking number — no login needed). Setting this env var turns
# on the extra "ดูคำขอของฉัน" auto-list, which needs to know who's asking.
TRACK_LIFF_ID = os.environ.get("TRACK_LIFF_ID", "")

WATER_LEVEL_SOURCE_URL = os.environ.get(
    "WATER_LEVEL_SOURCE_URL", "https://www.thaiwater.net/water/wl"
)
SNAKE_BITE_INFO_URL = "https://www.rama.mahidol.ac.th/poisoncenter/th"
SNAKE_BITE_HOTLINE = "1367"

# Public HTTPS base URL of this deployment (e.g. https://floodcare.onrender.com)
# Required so LINE can fetch static "hero" banner images for Flex Message cards
# (LINE downloads images from a public URL — it cannot read local files).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def hero_image_url(filename: str) -> Optional[str]:
    """
    Builds a public URL to a static banner image (served by Flask's /static route)
    for use as a Flex Message "hero" image. Returns None if PUBLIC_BASE_URL isn't
    configured yet, so callers can gracefully render the card without a banner
    instead of sending LINE a broken/local image URL.
    """
    if not PUBLIC_BASE_URL:
        return None
    return f"{PUBLIC_BASE_URL}/static/banners/{filename}"


DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")

# API key for the separately-hosted React dashboard (artifacts/floodcare-dashboard).
# Unlike DASHBOARD_PASSWORD (session-cookie login for the built-in HTML dashboard),
# this is a simple bearer token so a dashboard hosted on a different domain can call
# the JSON API directly (Authorization: Bearer <DASHBOARD_API_KEY>) without needing
# cross-origin cookies.
DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")

# Performance Tuning
WATER_DATA_MAX_AGE_MINUTES = int(os.environ.get("WATER_DATA_MAX_AGE_MINUTES", "10"))

# ── Water Alert Engine ──
# See FLOODCARE_AI_Water_Map_and_Alert_Implementation_Spec: proactive LINE
# push when a station near an opted-in user crosses into มาก/ล้นตลิ่ง.
WATER_ALERT_ENABLED = os.environ.get("WATER_ALERT_ENABLED", "true").strip().lower() == "true"
WATER_ALERT_RADIUS_KM = float(os.environ.get("WATER_ALERT_RADIUS_KM", "20"))
WATER_ALERT_COOLDOWN_MINUTES = int(os.environ.get("WATER_ALERT_COOLDOWN_MINUTES", "60"))
MAX_CASES_PER_SECTION = int(os.environ.get("MAX_CASES_PER_SECTION", "10"))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
SESSION_TTL_MINUTES = int(os.environ.get("SESSION_TTL_MINUTES", "30"))

# API Endpoints
THAIWATER_V3_API = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
THAIWATER_API_BASE = "https://api.thaiwater.net/twsapi/v1.0"
THAIWATER_WEB_URL = "https://www.thaiwater.net/water/waterlevel"


# =============================================================================
# SECTION 2: STRUCTURED LOGGING SYSTEM
# =============================================================================

class Logger:
    """Structured logging with performance tracking"""
    
    _log_buffer: List[dict] = []
    _buffer_lock = threading.Lock()
    _max_buffer = 100
    
    @classmethod
    def _timestamp(cls) -> str:
        return get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    @classmethod
    def info(cls, module: str, message: str, extra: dict = None):
        entry = {"ts": cls._timestamp(), "lvl": "INFO", "mod": module, "msg": message}
        if extra:
            entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] INFO  [{module}] {message}")
    
    @classmethod
    def error(cls, module: str, message: str, extra: dict = None):
        entry = {"ts": cls._timestamp(), "lvl": "ERROR", "mod": module, "msg": message}
        if extra:
            entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] ERROR [{module}] {message}")
    
    @classmethod
    def perf(cls, module: str, operation: str, elapsed_ms: float, extra: dict = None):
        entry = {
            "ts": cls._timestamp(), "lvl": "PERF", "mod": module,
            "op": operation, "ms": round(elapsed_ms, 2)
        }
        if extra:
            entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] PERF  [{module}] {operation}: {elapsed_ms:.1f}ms")
    
    @classmethod
    def security(cls, module: str, message: str, user_id: str = "", extra: dict = None):
        entry = {
            "ts": cls._timestamp(), "lvl": "SEC", "mod": module,
            "msg": message, "uid": user_id
        }
        if extra:
            entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] SEC   [{module}] {message} uid={user_id}")
    
    @classmethod
    def _buffer(cls, entry: dict):
        with cls._buffer_lock:
            cls._log_buffer.append(entry)
            if len(cls._log_buffer) > cls._max_buffer:
                cls._log_buffer = cls._log_buffer[-cls._max_buffer:]
    
    @classmethod
    def get_logs(cls, limit: int = 50) -> List[dict]:
        with cls._buffer_lock:
            return cls._log_buffer[-limit:]


# =============================================================================
# SECTION 3: SMART CACHE SYSTEM (Multi-Layer)
# =============================================================================

class LRUMemoryCache:
    """Thread-safe LRU Cache with TTL support"""
    
    def __init__(self, maxsize: int = 256, default_ttl: int = 300):
        self._cache: OrderedDict = OrderedDict()
        self._ttl = default_ttl
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
    
    def _is_expired(self, entry: dict) -> bool:
        return time.time() - entry["time"] > entry["ttl"]
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if self._is_expired(entry):
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return entry["value"]
    
    def set(self, key: str, value: Any, ttl: int = None):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = {
                "value": value,
                "time": time.time(),
                "ttl": ttl or self._ttl
            }
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
    
    def delete(self, key: str):
        with self._lock:
            self._cache.pop(key, None)
    
    def clear(self):
        with self._lock:
            self._cache.clear()
    
    def cleanup_expired(self) -> int:
        with self._lock:
            expired = [k for k, v in self._cache.items() if self._is_expired(v)]
            for k in expired:
                del self._cache[k]
            return len(expired)
    
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total * 100, 1) if total > 0 else 0,
                "ttl": self._ttl
            }


class CacheManager:
    """Multi-layer cache manager"""
    def __init__(self):
        self.general = LRUMemoryCache(maxsize=512, default_ttl=CACHE_TTL_SECONDS)
        self.weather = LRUMemoryCache(maxsize=256, default_ttl=1800)
        self.water = LRUMemoryCache(maxsize=128, default_ttl=900)
        self.sessions = LRUMemoryCache(maxsize=1024, default_ttl=SESSION_TTL_MINUTES * 60)
        self.sheets = LRUMemoryCache(maxsize=64, default_ttl=600)
    
    def cleanup_all(self) -> dict:
        return {
            "general": self.general.cleanup_expired(),
            "weather": self.weather.cleanup_expired(),
            "water": self.water.cleanup_expired(),
            "sessions": self.sessions.cleanup_expired(),
            "sheets": self.sheets.cleanup_expired(),
        }
    
    def all_stats(self) -> dict:
        return {
            "general": self.general.stats(),
            "weather": self.weather.stats(),
            "water": self.water.stats(),
            "sessions": self.sessions.stats(),
            "sheets": self.sheets.stats(),
        }

cache = CacheManager()


# =============================================================================
# SECTION 4: RATE LIMITING & SECURITY
# =============================================================================

class RateLimiter:
    """Token bucket rate limiter per user"""
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 300
        self._last_cleanup = time.time()
    
    def _cleanup(self):
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        with self._lock:
            expired = []
            for user_id, bucket in self._buckets.items():
                if now - bucket["last_reset"] > self._window * 2:
                    expired.append(user_id)
            for uid in expired:
                del self._buckets[uid]
            self._last_cleanup = now
            if expired:
                Logger.info("RateLimiter", f"Cleaned up {len(expired)} expired buckets")
    
    def check(self, user_id: str) -> Tuple[bool, dict]:
        self._cleanup()
        with self._lock:
            now = time.time()
            bucket = self._buckets.get(user_id)
            
            if bucket is None:
                self._buckets[user_id] = {
                    "tokens": self._max_requests - 1,
                    "last_reset": now
                }
                return True, {"remaining": self._max_requests - 1, "limit": self._max_requests}
            
            if now - bucket["last_reset"] > self._window:
                bucket["tokens"] = self._max_requests - 1
                bucket["last_reset"] = now
                return True, {"remaining": self._max_requests - 1, "limit": self._max_requests}
            
            if bucket["tokens"] <= 0:
                retry_after = int(self._window - (now - bucket["last_reset"]))
                Logger.security("RateLimiter", f"Rate limit exceeded", user_id)
                return False, {"retry_after": retry_after, "limit": self._max_requests}
            
            bucket["tokens"] -= 1
            return True, {"remaining": bucket["tokens"], "limit": self._max_requests}

rate_limiter = RateLimiter(max_requests=RATE_LIMIT_REQUESTS, window_seconds=RATE_LIMIT_WINDOW)


def sanitize_text(text: str, max_length: int = 2000) -> str:
    if not text:
        return ""
    sanitized = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or (ch.isprintable() and ord(ch) >= 32)
    )
    return sanitized[:max_length]


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


def generate_household_id(province: str, district: str, sub_district: str,
                           housing_type: str, house_no: str = "",
                           condo_floor: str = "", condo_room: str = "") -> str:
    """
    Builds a stable Household ID from normalized address parts so that
    every family member who registers with the *same* address (same house
    number, or same condo floor+room) is grouped under one household.

    Used to de-duplicate simultaneous SOS reports coming from the same
    household (see SheetsManager.find_open_case_by_household) and merge
    them into a single case instead of creating separate ones.
    """
    def _norm(v: str) -> str:
        return "".join((v or "").strip().lower().split())

    housing_type = _norm(housing_type) or "house"
    if housing_type in ("condo", "คอนโด", "อพาร์ตเมนต์", "apartment"):
        unit_key = f"condo|{_norm(condo_floor)}|{_norm(condo_room)}"
    else:
        unit_key = f"house|{_norm(house_no)}"

    raw = "|".join([_norm(province), _norm(district), _norm(sub_district), unit_key])
    digest = hashlib.sha256(raw.encode()).hexdigest()[:10].upper()
    return f"HH-{digest}"


# =============================================================================
# SECTION 5: INTENT CLASSIFICATION SYSTEM
# =============================================================================

class IntentClassifier:
    """Rule-based Intent Classifier to reduce API costs"""
    PATTERNS = {
        "EMERGENCY": [
            "ช่วยด้วย", "ช่วยด้วยครับ", "ช่วยด้วยค่ะ", "จะตาย", "จมแล้ว", "ไฟดูด", "ไฟฟ้าดูด"
        ],
        "ACCIDENT": [
            "อุบัติเหตุ", "รถชน", "รถคว่ำ", "ตกใจ", "เลือดออก", "ขาหัก", "แขนหัก", "แผลฉกรรจ์"
        ],
        "SOS": [
            "sos", "ขอความช่วยเหลือ", "แจ้งเหตุ", "กู้ภัย", "ติดน้ำท่วม", "จมน้ำ", "ช่วย"
        ],
        "NEEDS": [
            "ขอของ", "ขอสิ่งของ", "ต้องการสิ่งของ", "ขาดแคลน", "need supplies", "need items"
        ],
        "TRACK": [
            "ติดตามเคส", "เช็คสถานะ", "เช็กสถานะ", "ติดตามสถานะ", "track", "tracking"
        ],
        "SNAKE_BITE": [
            "งูกัด", "ถูกงูกัด", "โดนงูกัด", "งูกัดครับ", "งูกัดค่ะ", "ถูกงู", "โดนงู", "งูฉก"
        ],
        "PREP_GUIDE": [
            "วิธีเตรียมตัว", "เตรียมตัวรับมือ"
        ],
        "GREETING": [
            "สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "ดีจ้า", "ดีคับ", "hello", "hi", "hey", 
            "good morning", "good afternoon", "good evening"
        ],
        "SHELTER": [
            "ศูนย์พักพิง", "ที่พัก", "อพยพ", "หลบภัย", "หลบน้ำ", "ที่พักชั่วคราว", 
            "evacuation center", "shelter", "ไปไหนดี", "พักที่ไหน", "ห้างน้ำท่วม",
            "ค้นหาศูนย์พักพิง", "ตรวจสอบศูนย์พักพิง", "หาศูนย์พักพิง"
        ],
        "WATER_LEVEL": [
            "ระดับน้ำ", "น้ำสูง", "เช็คน้ำ", "ตรวจน้ำ", "water level", 
            "flood level", "น้ำขึ้น", "น้ำลด", "สถานการณ์น้ำ", "check water",
            "ตรวจสอบระดับน้ำ", "เช็คระดับน้ำ", "เช็กระดับน้ำ"
        ],
        "WEATHER": [
            "สภาพอากาศ", "พยากรณ์อากาศ", "ฝนตก", "ฝน", "อากาศ", "weather", 
            "forecast", "rain", " raining", "จะฝนตกไหม", "เช็คฝน", "check weather",
            "ตรวจสอบสภาพอากาศ", "เช็คสภาพอากาศ", "เช็กสภาพอากาศ"
        ],
        "CONTACT": [
            "เบอร์โทร", "โทรศัพท์", "ติดต่อ", "สายด่วน", "hotline", "phone", 
            "contact", "call", "เบอร์ฉุกเฉิน", "โทรหาใคร", "เบอร์ ปภ", "1784", "1669"
        ],
        "LANGUAGE": [
            "เปลี่ยนภาษา", "change language", "language", "ภาษา", "lang", "english", "ไทย", "japanese", "日本語"
        ],
        "CANCEL": [
            "ยกเลิก", "cancel", "หยุด", "stop", "ออก", "exit", "เริ่มใหม่", "restart", "reset"
        ],
        "REGISTRATION": [
            "ลงทะเบียน", "register", "สมัคร", "เข้าร่วม", "ลงชื่อ", "ข้อมูลของฉัน", "โปรไฟล์", "profile"
        ],
        "HELP": [
            "ทำอะไรได้บ้าง", "ทำอะไรได้", "มีอะไรบ้าง", "ช่วยอะไรได้บ้าง", "ใช้งานยังไง", 
            "ใช้งานอย่างไร", "วิธีใช้", "วิธีการใช้งาน", "วิธีใช้งาน", "คู่มือการใช้งาน",
            "สอนใช้งาน", "แนะนำการใช้งาน", "เมนู", "menu", "help",
            "what can you do", "capabilities", "คุณคือใคร", "คุณทำอะไรได้",
            # Self-introduction: this is a fixed fact about the bot itself,
            # not something that needs (or benefits from) a Google Search —
            # routing it here instead of AI_QUERY avoids a search-grounded
            # answer showing up with an irrelevant citation attached.
            "แนะนำตัว", "แนะนำตัวเอง", "introduce yourself", "who are you", "about you", "tell me about yourself"
        ],
        # Distinct from HELP on purpose: this is the exact text the cover
        # card's button sends back ("เปิดคู่มือ"), AND the text the Rich Menu
        # button should be configured to send directly ("คู่มือฉบับเต็ม") —
        # both skip straight to the full carousel. If either shared the HELP
        # list, tapping would just re-show the cover card instead of opening
        # the actual detailed guide.
        "FULL_GUIDE": [
            "เปิดคู่มือ", "open guide", "คู่มือฉบับเต็ม", "full guide"
        ],
        "FAQ": [
            "คำถามยอดฮิต", "คำถามที่พบบ่อย", "faq", "คำถามทั่วไป", "อยากรู้เรื่อง", "บอกข้อมูล", 
            "ค้นหา", "search", "น้ำท่วม 2567", "น้ำท่วม 2568", "น้ำท่วมล่าสุด", "สถานการณ์น้ำ", 
            "ข่าวน้ำท่วม", "อัพเดทน้ำท่วม", "ระดับน้ำล่าสุด", "คาดการณ์น้ำ", "พยากรณ์น้ำ"
        ],
    }
    
    # Words that would otherwise substring-match into WEATHER via "อากาศ"
    # (air/atmosphere) but ask something WEATHER's flow can't actually
    # answer — it only fetches temperature/rain/wind, not air-quality
    # readings. Left unclassified here, these fall through to the AI
    # classifier and get a real searched answer (AI_QUERY/FAQ) instead of
    # a location prompt that leads to weather data that doesn't address
    # what was actually asked.
    _AIR_QUALITY_TERMS = [
        "pm2.5", "pm 2.5", "pm2", "ฝุ่น", "คุณภาพอากาศ", "aqi", "มลพิษทางอากาศ", "หมอกควัน"
    ]

    @classmethod
    def classify(cls, text: str) -> Tuple[str, float]:
        if not text:
            return ("AI_QUERY", 0.5)
        
        text_lower = text.strip().lower()
        text_clean = text_lower.strip("!.,😊🙏👋🆘 ")
        is_air_quality = any(term in text_clean for term in cls._AIR_QUALITY_TERMS)
        
        PRIORITY_INTENTS = ["EMERGENCY", "SOS", "SNAKE_BITE"]
        for intent in PRIORITY_INTENTS:
            keywords = cls.PATTERNS.get(intent, [])
            for keyword in keywords:
                kw_lower = keyword.lower()
                if text_clean == kw_lower:
                    return (intent, 1.0)
                if text_clean.startswith(kw_lower):
                    return (intent, 0.9)
                if len(keyword) >= 4 and kw_lower in text_lower:
                    return (intent, 0.8)
                if len(keyword) < 4 and kw_lower in text_lower:
                    return (intent, 0.7)
        
        for intent, keywords in cls.PATTERNS.items():
            if intent in PRIORITY_INTENTS:
                continue
            if intent == "WEATHER" and is_air_quality:
                continue
            for keyword in keywords:
                kw_lower = keyword.lower()
                if text_clean == kw_lower:
                    return (intent, 1.0)
                if text_clean.startswith(kw_lower):
                    return (intent, 0.9)
                if len(keyword) >= 4 and kw_lower in text_lower:
                    return (intent, 0.8)
                if len(keyword) < 4 and kw_lower in text_lower:
                    return (intent, 0.7)
        
        emergency_words = ["ช่วย", "ด่วน", "วิกฤต", "ฉุกเฉิน", "help", "emergency", "urgent"]
        if any(w in text_lower for w in emergency_words):
            return ("EMERGENCY", 0.6)
        
        return ("AI_QUERY", 0.5)


# =============================================================================
# SECTION 5B: AI-BASED INTENT ANALYSIS (replaces keyword matching)
# =============================================================================
# Free-text messages are now classified by asking Gemini to read the whole
# sentence and decide the user's real intent, instead of guessing from the
# presence/absence of specific keywords. IntentClassifier.classify() (above)
# is kept only as an automatic fallback if Gemini is unavailable or returns
# something unparsable, so classification never silently fails.
#
# "scope" is only meaningful for WATER_LEVEL / SHELTER: NEARBY means the user
# is asking about their own location (-> ask for LINE location share),
# GENERAL means an overview/regional question (-> answer directly, no
# location prompt needed). This is what lets "ภาคเหนือน้ำเป็นไง" go straight
# to an answer while "น้ำแถวบ้านผมเป็นไง" asks for the user's location first.

INTENT_LIST_AI = [
    "EMERGENCY", "SOS", "NEEDS", "TRACK", "SNAKE_BITE", "ACCIDENT", "PREP_GUIDE", "GREETING",
    "HELP", "FAQ", "CONTACT", "SHELTER", "WATER_LEVEL", "WEATHER",
    "REGISTRATION", "LANGUAGE", "CANCEL", "AI_QUERY"
]

INTENT_AI_SYSTEM_INSTRUCTION = (
    "คุณคือระบบวิเคราะห์เจตนา (Intent Analyzer) ของแชทบอท FLOODCARE AI "
    "หน้าที่ของคุณคือวิเคราะห์ข้อความของผู้ใช้ แล้วตอบกลับเป็น JSON เท่านั้น "
    "ห้ามมีคำอธิบาย ห้ามมี markdown code fence ห้ามมีข้อความอื่นใดนอกจาก JSON object เดียว\n\n"
    "รูปแบบ JSON ที่ต้องตอบกลับเป๊ะๆ:\n"
    '{"intent": "<ONE_OF_INTENTS>", "scope": "NEARBY หรือ GENERAL หรือ NONE", '
    '"in_scope": <true หรือ false>, "confidence": <0.0-1.0>}\n\n'
    f"รายการ intent ที่เลือกได้: {', '.join(INTENT_LIST_AI)}\n\n"
    "คำอธิบาย intent:\n"
    "- EMERGENCY: สถานการณ์คับขันเป็นอันตรายถึงชีวิตตอนนี้ (กำลังจมน้ำ ไฟดูด ฯลฯ)\n"
    "- SOS: ขอความช่วยเหลือกู้ภัยจากน้ำท่วม ต้องการให้ทีมไปช่วยเหลือ (ชีวิต/ความปลอดภัย)\n"
    "- NEEDS: ขอความช่วยเหลือเรื่องสิ่งของ/เสบียง/ของบรรเทาทุกข์ (ไม่ใช่ขอกู้ภัยฉุกเฉิน)\n"
    "- TRACK: ต้องการติดตามสถานะ/ความคืบหน้าของเคสที่เคยแจ้งไปแล้ว (SOS หรือขอของ) เช่น \"เคสของฉันถึงไหนแล้ว\" \"เช็คสถานะการแจ้งเหตุ\"\n"
    "- SNAKE_BITE: ถูกงูกัด\n"
    "- ACCIDENT: อุบัติเหตุ บาดเจ็บ\n"
    "- PREP_GUIDE: ถามวิธีเตรียมตัวรับมือน้ำท่วมล่วงหน้า\n"
    "- GREETING: ทักทาย\n"
    "- HELP: ถามว่าบอททำอะไรได้บ้าง/วิธีใช้งาน หรือขอให้บอทแนะนำตัวเอง/บอกว่าตัวเองคือใคร "
    "(เช่น \\\"แนะนำตัวหน่อย\\\" \\\"คุณคือใคร\\\" \\\"introduce yourself\\\") — กรณีเหล่านี้เป็นข้อเท็จจริงคงที่เกี่ยวกับตัวบอทเอง "
    "ไม่ต้องค้นหาข้อมูลใดๆ จึงต้องจัดเป็น HELP เสมอ ห้ามจัดเป็น AI_QUERY หรือ FAQ เด็ดขาด "
    "เพราะ intent เหล่านั้นจะถูกบังคับให้ค้นหาและแนบแหล่งอ้างอิงมาด้วย ซึ่งไม่มีอะไรให้ค้นหาจริงๆ และจะได้แหล่งอ้างอิงที่ไม่เกี่ยวข้องมาแทน\n"
    "- CONTACT: ขอเบอร์โทรฉุกเฉิน/หน่วยงาน\n"
    "- SHELTER: ถามเกี่ยวกับศูนย์พักพิง/ที่อพยพ/ที่ควรไปหลบภัย รวมถึงคำถามที่ไม่ได้พูดคำว่า 'ศูนย์พักพิง' ตรงๆ "
    "แต่ความหมายคือต้องการรู้ว่าตนเอง ณ ตอนนี้ควรไปที่ไหน/อพยพไปทางไหน (เช่น \"ตอนนี้ผมควรอพยพไปที่ไหน\", "
    "\"ควรไปหลบที่ไหนดี\") ให้ถือเป็น SHELTER เช่นกัน — ให้ scope=NEARBY ก็ต่อเมื่อถามถึงตำแหน่งของผู้ใช้เองเท่านั้น "
    "(แถวนี้ ใกล้ฉัน บ้านฉัน ตอนนี้ตรงนี้ โดยไม่ได้ระบุชื่อสถานที่ใดๆ) ส่วนกรณีอื่นทั้งหมดให้ scope=GENERAL "
    "รวมถึงเมื่อถามถึงสถานที่ที่ระบุชื่อเจาะจง (เช่น ชื่อเขต/อำเภอ/จังหวัด/ตำบล) แม้จะเจาะจงแค่จุดเดียวก็ตาม "
    "เพราะไม่ใช่ตำแหน่งของผู้ใช้เอง ต้องตอบด้วยการค้นหาข้อมูลเกี่ยวกับสถานที่นั้นแทนการขอพิกัด\n"
    "- WATER_LEVEL: ถามเกี่ยวกับระดับน้ำ — ให้ scope=NEARBY ก็ต่อเมื่อถามถึงตำแหน่งของผู้ใช้เองเท่านั้น "
    "(บ้าน แถวนี้ ตอนนี้ตรงนี้ โดยไม่ได้ระบุชื่อสถานที่ใดๆ) ส่วนกรณีอื่นทั้งหมดให้ scope=GENERAL รวมถึงเมื่อถามถึง "
    "สถานที่ที่ระบุชื่อเจาะจง (เช่น \"หาดใหญ่\" \"อำเภอเมืองสงขลา\") แม้จะเป็นจุดเดียวไม่ใช่ภาพรวมทั้งภาค/จังหวัดก็ตาม "
    "— กติกาคือ: มีชื่อสถานที่ระบุมาในคำถามชัดเจน = GENERAL เสมอ (ค้นหาข้อมูลเกี่ยวกับที่นั้นแทนการขอพิกัดผู้ใช้), "
    "ไม่มีชื่อสถานที่และหมายถึงตัวผู้ใช้เอง = NEARBY (ต้องขอพิกัดผู้ใช้จริง)\n"
    "- WEATHER: ถามสภาพอากาศ/พยากรณ์อากาศ (อุณหภูมิ ฝน ลม) เท่านั้น — ห้ามรวมคำถามเกี่ยวกับฝุ่น PM2.5/PM2/"
    "คุณภาพอากาศ/AQI/หมอกควัน/มลพิษทางอากาศ เพราะระบบสภาพอากาศของบอทดึงได้แค่อุณหภูมิ/ฝน/ลม ไม่มีข้อมูลฝุ่นเลย "
    "ถ้าจัดเป็น WEATHER ผู้ใช้จะถูกขอพิกัดแล้วได้รายงานอุณหภูมิ/ฝนกลับมาซึ่งไม่ตรงกับคำถามฝุ่นที่ถามจริง "
    "ให้จัดคำถามเกี่ยวกับฝุ่น/คุณภาพอากาศเป็น AI_QUERY แทนเสมอ (จะได้ค้นหาคำตอบจริงจากอินเทอร์เน็ตให้)\n"
    "- REGISTRATION: ต้องการลงทะเบียนข้อมูลส่วนตัว\n"
    "- LANGUAGE: ต้องการเปลี่ยนภาษา\n"
    "- CANCEL: ต้องการยกเลิก/หยุดขั้นตอนที่ทำอยู่\n"
    "- FAQ: ถามข้อมูลข่าวสาร/สถานการณ์น้ำท่วมทั่วไปที่ต้องอาศัยข้อมูลล่าสุดจากอินเทอร์เน็ต\n"
    "- AI_QUERY: คำถามทั่วไปเกี่ยวกับน้ำท่วม/ความปลอดภัย/สุขภาพกายใจจากภัยพิบัติที่ไม่เข้าเงื่อนไขข้างต้น "
    "รวมถึงคำถามที่ไม่เกี่ยวข้องกับน้ำท่วม/ความปลอดภัยเลย (เช่น ขอเลขหวย แต่งกลอน สูตรอาหาร คำถามทั่วไปอื่นๆ) "
    "ให้จัดเป็น AI_QUERY เสมอเช่นกัน (ระบบปลายทางจะปฏิเสธอย่างสุภาพเองตามขอบเขตที่กำหนดไว้)\n\n"
    "field \"in_scope\" (สำคัญมาก — ใช้ตัดสินว่าจะปล่อยให้ตอบจริงหรือปฏิเสธทันทีโดยไม่ต้องค้นหา/ตอบเลย):\n"
    "ให้ in_scope=false ก็ต่อเมื่อ intent เป็น AI_QUERY หรือ FAQ เท่านั้น "
    "และเนื้อหาคำถามไม่เกี่ยวข้องกับ 1) น้ำท่วม/อุทกภัย 2) ความปลอดภัย/การกู้ภัย 3) อุบัติเหตุ/การบาดเจ็บ/ปฐมพยาบาล "
    "4) สุขภาพกายหรือใจที่เกี่ยวกับภัยพิบัติ เลยแม้แต่น้อย — นี่คือ 'ความรู้ทั่วไปที่ไม่เกี่ยวกับภารกิจของบอท' "
    "เช่น ถามความยาวคอยีราฟ, จำนวนนักแข่ง F1, ผลบอล, สูตรอาหาร, ดารา, ประวัติศาสตร์ทั่วไป, คณิตศาสตร์, "
    "โปรแกรมมิ่ง, ขอเลขหวย, แต่งกลอน ฯลฯ — คำถามความรู้ทั่วไปที่ไม่มีความเกี่ยวข้องกับภัยพิบัติแม้แต่น้อยทุกชนิด "
    "ให้ in_scope=false เสมอ ไม่ว่าจะดูไม่เป็นอันตรายหรือตอบง่ายแค่ไหนก็ตาม ห้ามยกเว้นให้เพราะคิดว่า 'น่าจะตอบได้ไม่เสียหาย' "
    "ส่วนกรณีอื่นทั้งหมด (intent อื่นทุกตัว หรือ AI_QUERY/FAQ ที่เกี่ยวกับ 4 หัวข้อข้างต้นจริง) ให้ in_scope=true เสมอ\n\n"
    "สำหรับ intent ที่ไม่ใช่ SHELTER หรือ WATER_LEVEL ให้ใส่ scope เป็น \"NONE\" เสมอ\n"
    "ตัวอย่าง: \"ภาคเหนือระดับน้ำเป็นอย่างไร\" -> WATER_LEVEL / GENERAL / in_scope=true (เพราะถามภาพรวมภูมิภาค ไม่ใช่ใกล้ตัวผู้ใช้)\n"
    "ตัวอย่าง: \"ระดับน้ำหาดใหญ่เป็นอย่างไร\" -> WATER_LEVEL / GENERAL / in_scope=true (ระบุชื่อสถานที่ \"หาดใหญ่\" ชัดเจน แม้เป็นจุดเดียว ก็ไม่ใช่ตำแหน่งผู้ใช้เอง จึงตอบด้วยการค้นหาแทนการขอพิกัด)\n"
    "ตัวอย่าง: \"น้ำแถวบ้านผมเป็นไงบ้าง\" -> WATER_LEVEL / NEARBY / in_scope=true (เพราะถามใกล้ตัวผู้ใช้ ไม่มีชื่อสถานที่)\n"
    "ตัวอย่าง: \"ตอนนี้ผมควรอพยพไปที่ไหน\" -> SHELTER / NEARBY / in_scope=true (แม้ไม่มีคำว่าศูนย์พักพิง แต่ความหมายคือถามหาที่ปลอดภัยใกล้ตัวตอนนี้)\n"
    "ตัวอย่าง: \"ยีราฟคอยาวกี่เมตร\" -> AI_QUERY / NONE / in_scope=false (ความรู้ทั่วไปเรื่องสัตว์ ไม่เกี่ยวกับภัยพิบัติเลย)\n"
    "ตัวอย่าง: \"นักแข่ง F1 มีกี่คน\" -> AI_QUERY / NONE / in_scope=false (ความรู้ทั่วไปเรื่องกีฬา ไม่เกี่ยวกับภัยพิบัติเลย)\n"
    "ตัวอย่าง: \"ปวดหัวเป็นไข้ควรทำไง\" -> AI_QUERY / NONE / in_scope=true (สุขภาพกาย อาจเกี่ยวกับโรคจากน้ำสกปรกหรือช่วงภัยพิบัติ จึงยังอยู่ในขอบเขต)"
)

# =============================================================================
# SECTION 5C: COMBINED CLASSIFY + ANSWER (single round-trip, latency fix)
# =============================================================================
# classify_intent_ai() (above) needs a second Gemini call afterwards for any
# intent that ends in a search-grounded AI answer (FAQ, AI_QUERY, and
# WATER_LEVEL/SHELTER with scope=GENERAL) — two sequential LLM round-trips
# for one user message, often 5-12s combined. This does both in ONE call:
# the model classifies the message AND, only when the intent calls for it,
# drafts the final search-grounded answer in the same response. app.py uses
# the draft directly for those intents instead of calling
# ask_gemini_with_search a second time. Every other intent (SOS, EMERGENCY,
# SHELTER/WATER_LEVEL NEARBY, GREETING, etc.) still costs only one call,
# same as before, since no answer needs to be drafted for those.
#
# This trades a bit of classification-call simplicity (structured JSON mode
# can't be combined with the Search tool, so this uses a plain-text header +
# delimiter format instead) for the latency win. If parsing ever fails for
# any reason, classify_and_maybe_answer() returns None and the caller falls
# straight back to the original two-step classify_intent_ai() +
# ask_gemini_with_search() flow — so this can only ever help, never break
# anything if the model doesn't follow the format exactly.

_ANSWER_DELIMITER = "===ANSWER==="

COMBINED_CLASSIFY_ANSWER_SYSTEM_INSTRUCTION = (
    INTENT_AI_SYSTEM_INSTRUCTION.replace(
        "หน้าที่ของคุณคือวิเคราะห์ข้อความของผู้ใช้ แล้วตอบกลับเป็น JSON เท่านั้น "
        "ห้ามมีคำอธิบาย ห้ามมี markdown code fence ห้ามมีข้อความอื่นใดนอกจาก JSON object เดียว\n\n",
        "หน้าที่ของคุณคือวิเคราะห์ข้อความของผู้ใช้ แล้ววิเคราะห์เจตนาก่อนเสมอ\n\n"
    )
    + "\n\n"
    "หลังวิเคราะห์เจตนาแล้ว ให้ตอบกลับตามรูปแบบนี้เป๊ะๆ:\n\n"
    "บรรทัดแรก: JSON บรรทัดเดียวเท่านั้น ห้ามมี markdown code fence "
    '{"intent": "<ONE_OF_INTENTS>", "scope": "NEARBY หรือ GENERAL หรือ NONE", '
    '"in_scope": <true หรือ false>, "confidence": <0.0-1.0>}\n\n'
    f"ให้ตามด้วยบรรทัด {_ANSWER_DELIMITER} แล้วตามด้วยคำตอบเต็ม ก็ต่อเมื่อเข้าเงื่อนไข **ทั้งสองข้อ** นี้พร้อมกันเท่านั้น: "
    "(1) intent ที่วิเคราะห์ได้คือ FAQ หรือ AI_QUERY และ (2) in_scope=true "
    "ถ้า in_scope=false (คำถามความรู้ทั่วไปที่ไม่เกี่ยวกับภัยพิบัติเลย เช่น ยีราฟ, F1, ผลบอล, สูตรอาหาร) "
    f"ห้ามใส่ {_ANSWER_DELIMITER} หรือคำตอบใดๆ ต่อท้ายเด็ดขาด แม้ intent จะเป็น FAQ/AI_QUERY ก็ตาม "
    "ให้จบแค่บรรทัด JSON เท่านั้น — ห้ามค้นหาข้อมูลหรือร่างคำตอบให้คำถามนอกขอบเขตเหล่านี้โดยเด็ดขาด "
    "ระบบปลายทางจะแสดงข้อความปฏิเสธที่ตายตัวเองแทน ไม่ต้องพยายามตอบหรือปฏิเสธเองในส่วนนี้\n\n"
    "เมื่อเข้าเงื่อนไขให้ร่างคำตอบ (intent เป็น FAQ/AI_QUERY และ in_scope=true) "
    "ให้ค้นหาข้อมูลด้วย Google Search ประกอบคำตอบ และทำตามกฎการตอบต่อไปนี้อย่างเคร่งครัด:\n"
    "1. ห้ามใช้เครื่องหมายดอกจันเดี่ยวหรือสองชั้น (*) ในข้อความอย่างเด็ดขาด\n"
    "2. ตอบเป็นข้อๆ เสมอ ขึ้นต้นแต่ละประเด็นด้วยเลขข้อ (1. 2. 3. ...) เว้นบรรทัดระหว่างข้อ ยกเว้นคำตอบสั้นมากที่มีประเด็นเดียวให้ตอบเป็นประโยคปกติได้\n"
    "3. กระชับ ตอบเฉพาะสิ่งที่ถามจริงๆ สูงสุดไม่เกิน 4-5 ข้อ แต่ละข้อไม่เกิน 1-2 บรรทัด รวมไม่เกินประมาณ 60-80 คำ เว้นแต่จำเป็นต้องครบทุกขั้นตอนจริงๆ\n"
    "4. ห้ามระบุแหล่งที่มา/อ้างอิงในเนื้อความคำตอบเด็ดขาด ระบบจะแสดงแหล่งอ้างอิงแยกให้เอง\n"
    "5. จบประโยคให้ครบเสมอ ห้ามตัดจบกลางประโยค\n"
    "6. ก่อนตอบทุกครั้งที่มีคำตอบ ต้องเรียกใช้เครื่องมือ Google Search อย่างน้อยหนึ่งครั้งเสมอ "
    "แม้จะคิดว่ารู้คำตอบอยู่แล้วก็ตาม ห้ามตอบจากความรู้เดิมเพียงอย่างเดียวโดยไม่ค้นหาก่อนเด็ดขาด "
    "เพราะระบบต้องมีแหล่งอ้างอิงแนบให้ผู้ใช้ตรวจสอบข้อมูลด้านความปลอดภัยได้เสมอ\n\n"
    f"ถ้า intent ไม่ใช่ FAQ หรือ AI_QUERY หรือ in_scope=false ให้จบคำตอบแค่บรรทัด JSON บรรทัดเดียวเท่านั้น "
    f"ห้ามใส่ {_ANSWER_DELIMITER} หรือคำตอบใดๆ ต่อท้ายเด็ดขาด เพราะกรณีเหล่านั้นระบบอื่นจะจัดการเอง"
)


def classify_and_maybe_answer(text: str, lang: str = "TH"):
    """
    Single-call combined intent classification + (conditionally) search-
    grounded answer draft. Returns a dict {intent, scope, confidence,
    answer, sources} on success, or None if the model's output couldn't be
    parsed — callers must fall back to classify_intent_ai() +
    ask_gemini_with_search() when this returns None.
    """
    if not text or not text.strip():
        return None
    if not init_gemini():
        return None

    cache_key = f"classify_answer:{lang}:{hashlib.md5(text.strip().encode()).hexdigest()}"
    cached = cache.general.get(cache_key)
    if cached:
        return cached

    start_time = time.time()
    try:
        lang_note = (
            "\n\nถ้ามีคำตอบ ให้ตอบเป็นภาษามลายู (Bahasa Melayu) แทนภาษาไทย ยกเว้นชื่อเฉพาะ" if lang == "MY"
            else "\n\nIf answering, respond in English instead of Thai, except proper nouns (place names, station names)." if lang == "EN"
            else ""
        )
        response = gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"ข้อความผู้ใช้: {text.strip()}",
            config=genai_types.GenerateContentConfig(
                system_instruction=COMBINED_CLASSIFY_ANSWER_SYSTEM_INSTRUCTION + lang_note,
                max_output_tokens=2048,
                temperature=0.2,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                # gemini-2.5-flash "thinks" before answering by default, which
                # adds a couple of extra seconds per call for a task (intent
                # classification + short grounded answer) that doesn't need
                # deep reasoning. Disabling it is the single biggest lever
                # for response speed here.
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        raw = (response.text or "").strip()

        if _ANSWER_DELIMITER in raw:
            header_part, answer_part = raw.split(_ANSWER_DELIMITER, 1)
        else:
            header_part, answer_part = raw, ""

        header_part = header_part.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(header_part)

        intent = str(parsed.get("intent", "")).strip().upper()
        scope = str(parsed.get("scope", "GENERAL")).strip().upper()
        in_scope = bool(parsed.get("in_scope", True))
        confidence = float(parsed.get("confidence", 0.7))

        if intent not in set(INTENT_LIST_AI):
            return None
        if scope not in ("NEARBY", "GENERAL", "NONE"):
            scope = "GENERAL"
        if intent not in ("AI_QUERY", "FAQ"):
            in_scope = True

        answer_text = clean_text_for_line(answer_part.strip()) if answer_part.strip() else None

        sources = []
        if answer_text:
            try:
                for candidate in response.candidates:
                    grounding = getattr(candidate, "grounding_metadata", None)
                    if grounding:
                        for chunk in getattr(grounding, "grounding_chunks", None) or []:
                            web = getattr(chunk, "web", None)
                            if web:
                                uri = getattr(web, "uri", "") or ""
                                if uri:
                                    sources.append({"title": getattr(web, "title", "") or "", "url": uri})
            except Exception:
                pass

        result = {
            "intent": intent, "scope": scope, "in_scope": in_scope, "confidence": confidence,
            "answer": answer_text, "sources": sources,
        }
        cache.general.set(cache_key, result, ttl=120)

        elapsed = (time.time() - start_time) * 1000
        Logger.perf("ClassifyAnswer", "combined_call", elapsed, {"intent": intent, "in_scope": in_scope, "had_answer": bool(answer_text)})
        return result
    except Exception as e:
        Logger.info("ClassifyAnswer", f"Combined call failed, falling back to two-step flow: {e}")
        return None


def classify_intent_ai(text: str) -> dict:
    """
    AI-based intent + scope classifier — this is what free-text (non-menu)
    messages are routed through now, replacing keyword guessing. Returns
    {"intent": str, "scope": "NEARBY"|"GENERAL"|"NONE", "in_scope": bool, "confidence": float}.

    "in_scope" is the hard scope gate for AI_QUERY/FAQ: it's decided right
    here, at classification time, instead of being left to the later
    answer-generation call to notice and self-refuse. That matters because
    the generation call is also told to always use Google Search, and in
    practice that "always search" instinct can win out over a softer
    "refuse if off-topic" instruction — a model asked to both judge scope
    AND draft a helpful answer in the same breath will sometimes just
    answer harmless-seeming trivia (e.g. "ยีราฟคอยาวกี่เมตร") instead of
    declining. Deciding in_scope here, before any answer is drafted, means
    the caller can skip generation (and Google Search) entirely for a
    genuinely off-topic question and show a fixed decline message instead
    — no reliance on the generation step remembering to refuse itself.

    Always falls back to the old rule-based IntentClassifier.classify() if
    Gemini is unavailable, errors out, or returns something that can't be
    parsed as valid JSON/intent — so the bot never gets stuck without an
    intent just because the AI call had a hiccup. The fallback defaults to
    in_scope=True (fail-open) since the rule-based classifier has no way to
    judge topic relevance — this only matters on the rare occasion Gemini
    itself is fully unavailable, and erring toward "answer it" there is
    better for availability than silently blocking legitimate questions.
    """
    fallback_intent, fallback_conf = IntentClassifier.classify(text)
    fallback = {"intent": fallback_intent, "scope": "GENERAL", "in_scope": True, "confidence": fallback_conf}

    if not text or not text.strip():
        return fallback

    if not init_gemini():
        return fallback

    cache_key = f"intent_ai:{hashlib.md5(text.strip().encode()).hexdigest()}"
    cached = cache.general.get(cache_key)
    if cached:
        return cached

    start_time = time.time()
    try:
        response = gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"ข้อความผู้ใช้: {text.strip()}",
            config=genai_types.GenerateContentConfig(
                system_instruction=INTENT_AI_SYSTEM_INSTRUCTION,
                max_output_tokens=200,
                temperature=0.0,
                response_mime_type="application/json",
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        raw = (response.text or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)

        intent = str(parsed.get("intent", "")).strip().upper()
        scope = str(parsed.get("scope", "GENERAL")).strip().upper()
        in_scope = bool(parsed.get("in_scope", True))
        confidence = float(parsed.get("confidence", 0.7))

        if intent not in set(INTENT_LIST_AI):
            Logger.info("IntentAI", f"Unknown intent '{intent}' returned by AI — using keyword fallback")
            return fallback
        if scope not in ("NEARBY", "GENERAL", "NONE"):
            scope = "GENERAL"
        # in_scope only ever matters for AI_QUERY/FAQ — force True for every
        # other intent so a model slip on this field can't block a real
        # SOS/SHELTER/etc. flow.
        if intent not in ("AI_QUERY", "FAQ"):
            in_scope = True

        result = {"intent": intent, "scope": scope, "in_scope": in_scope, "confidence": confidence}
        cache.general.set(cache_key, result, ttl=120)

        elapsed = (time.time() - start_time) * 1000
        Logger.perf("IntentAI", "classify", elapsed, {"intent": intent, "scope": scope, "in_scope": in_scope})
        return result
    except Exception as e:
        Logger.error("IntentAI", f"Classification failed, using keyword fallback: {e}")
        return fallback


NEARBY_DATA_REPLY_SYSTEM_INSTRUCTION = (
    "คุณคือ FLOODCARE AI ผู้ช่วยอัจฉริยะด้านภัยน้ำท่วมและเหตุฉุกเฉินในประเทศไทย\n"
    "บุคลิกภาพ: สุภาพ มืออาชีพ อบอุ่น เป็นธรรมชาติเหมือนคุยกับคนจริง\n"
    "ใช้สรรพนามแทนตนเองว่า 'ผม' หรือ 'น้องบอท' ห้ามใช้คำว่า 'ฉัน' และห้ามใช้อีโมจิเด็ดขาด\n\n"
    "หน้าที่ของคุณตอนนี้คือนำข้อมูลดิบ (ชื่อสถานี/ศูนย์, ระยะทาง, ค่าตัวเลข, สถานะ) ที่ผู้ใช้ส่งมาให้ "
    "มาเรียบเรียงเป็นคำตอบสนทนา 'แบบร้อยแก้วต่อเนื่อง' ไม่ใช่รายการข้อ ๆ (ห้ามขึ้นต้นด้วยเลข 1. 2. 3. "
    "และห้ามใช้เครื่องหมายดอกจันเด็ดขาด)\n\n"
    "กฎสำคัญที่สุด: ต้องใส่ 'ตัวเลข/สถานะที่ผู้ใช้ให้มาทุกตัว' ลงในคำตอบให้ครบ ห้ามตัดข้อมูลตัวเลขทิ้งเพื่อให้สั้น "
    "(เช่น ระยะทาง กม., ระดับน้ำ ม., สถานะศูนย์พักพิง, ความจุ) เพราะเป็นข้อมูลที่ผู้ใช้ต้องการที่สุด "
    "แต่ให้เรียบเรียงเป็นประโยคสนทนาไม่เกิน 4-5 บรรทัด ไม่ใช่ตาราง\n"
    "หากสถานการณ์ดูอันตราย (น้ำวิกฤต/เกินตลิ่ง/ศูนย์เต็ม) ให้เตือนและแนะนำขั้นตอนถัดไปสั้นๆ ต่อท้าย"
)


def compose_water_level_reply(user_question: str, stations: list, lang: str = "TH") -> str:
    """
    Turns already-computed nearest-station data (distance, level, situation —
    all calculated in app.py using the existing Google Sheets lookup, never
    by the AI) into a short, natural conversational reply. Used only for
    the AI-intent path; the Rich-Menu path keeps using
    build_water_level_flex_message for its card UI, unchanged.
    lang="MY" makes the composed reply Bahasa Melayu — station names stay
    in Thai since they're official proper nouns with no translation.
    """
    if not stations:
        if lang == "MY":
            return (
                "Belum ada stesen paras air berdekatan lokasi anda dalam sistem buat masa ini. "
                f"Sila semak peta paras air seluruh negara di {WATER_LEVEL_SOURCE_URL}."
            )
        return (
            "ตอนนี้ยังไม่พบสถานีวัดระดับน้ำในระบบที่อยู่ใกล้ตำแหน่งของคุณครับ "
            f"ลองดูแผนที่ระดับน้ำทั้งประเทศเพิ่มเติมได้ที่ {WATER_LEVEL_SOURCE_URL} ครับ"
        )

    lines = []
    for st in stations:
        wl = st.get("water_level", {}).get("value", "-")
        lines.append(
            f"- {st.get('stationName', 'ไม่ระบุ')} (ห่างประมาณ {st.get('distance_km', 0):.1f} กม.): "
            f"ระดับน้ำ {wl} ม., สถานการณ์ {st.get('situation', 'ปกติ')}, แนวโน้ม {st.get('trend', 'คงที่')}"
        )
    data_block = "\n".join(lines)

    lang_instruction = (
        "จงเรียบเรียงข้อมูลนี้เป็นคำตอบสนทนาภาษามลายู (Bahasa Melayu) ที่เป็นธรรมชาติ กระชับ ไม่เกิน 4-5 บรรทัด "
        "คงชื่อสถานีเป็นภาษาไทยตามเดิมเพราะเป็นชื่อเฉพาะ "
        if lang == "MY" else
        "จงเรียบเรียงข้อมูลนี้เป็นคำตอบสนทนาภาษาไทยที่เป็นธรรมชาติ กระชับ ไม่เกิน 4-5 บรรทัด "
    )
    prompt = (
        f'ผู้ใช้ถามว่า: "{user_question}"\n\n'
        "นี่คือข้อมูลสถานีวัดระดับน้ำที่ใกล้ตำแหน่งผู้ใช้ที่สุด (ระยะทางและตัวเลขคำนวณมาให้แล้ว "
        "ห้ามคำนวณ ห้ามเดา หรือแก้ไขตัวเลขใดๆ เพิ่มเอง ใช้ตามที่ให้มาเท่านั้น):\n\n"
        f"{data_block}\n\n"
        f"{lang_instruction}"
        "บอกสถานีที่ใกล้ที่สุดก่อน แล้วเสริมสถานีถัดไปถ้าจำเป็น ห้ามใช้เครื่องหมายดอกจัน "
        "ถ้าพบว่าระดับน้ำอยู่ในสถานการณ์วิกฤตหรือเกินตลิ่ง ให้เตือนให้ระวังและแนะนำให้ติดตามสถานการณ์ใกล้ชิดด้วย"
    )
    return ask_gemini(prompt, max_tokens=1024, system_instruction=NEARBY_DATA_REPLY_SYSTEM_INSTRUCTION, lang=lang)


def compose_shelter_reply(user_question: str, shelters: list, lang: str = "TH") -> str:
    """
    Same idea as compose_water_level_reply, but for nearest-shelter data from
    find_nearest_shelters() (distance/capacity/status all pre-computed).
    """
    if not shelters:
        if lang == "MY":
            return (
                "Maaf, belum ada pusat perlindungan berdekatan lokasi anda dalam sistem buat masa ini. "
                "Untuk keselamatan, sila hubungi talian kecemasan 1784 untuk maklumat pusat perlindungan terdekat."
            )
        return (
            "ขออภัยครับ ตอนนี้ยังไม่พบศูนย์พักพิงในระบบที่อยู่ใกล้ตำแหน่งของคุณ "
            "เพื่อความปลอดภัย รบกวนติดต่อสายด่วน ปภ. 1784 เพื่อสอบถามจุดอพยพที่ใกล้ที่สุดในพื้นที่ได้เลยครับ"
        )

    lines = []
    for s in shelters:
        lines.append(
            f"- {s.get('Name', 'ไม่ระบุชื่อ')} ({s.get('District', '')} {s.get('Province', '')}) "
            f"ห่างประมาณ {s.get('distance_km', 0):.1f} กม., สถานะ {s.get('Status', 'เปิดรับ')}, "
            f"รองรับได้ {s.get('Occupancy', 0)}/{s.get('Capacity', 0)} คน"
        )
    data_block = "\n".join(lines)

    lang_instruction = (
        "จงเรียบเรียงข้อมูลนี้เป็นคำตอบสนทนาภาษามลายู (Bahasa Melayu) ที่เป็นธรรมชาติ กระชับ ไม่เกิน 4-5 บรรทัด "
        "คงชื่อศูนย์พักพิงเป็นภาษาไทยตามเดิมเพราะเป็นชื่อเฉพาะ "
        if lang == "MY" else
        "จงเรียบเรียงข้อมูลนี้เป็นคำตอบสนทนาภาษาไทยที่เป็นธรรมชาติ กระชับ ไม่เกิน 4-5 บรรทัด "
    )
    prompt = (
        f'ผู้ใช้ถามว่า: "{user_question}"\n\n'
        "นี่คือข้อมูลศูนย์พักพิงที่ใกล้ตำแหน่งผู้ใช้ที่สุด (ระยะทางและข้อมูลคำนวณมาให้แล้ว "
        "ห้ามเดาหรือแก้ไขตัวเลขใดๆ เพิ่มเอง ใช้ตามที่ให้มาเท่านั้น):\n\n"
        f"{data_block}\n\n"
        f"{lang_instruction}"
        "บอกศูนย์ที่ใกล้ที่สุดก่อน ถ้าศูนย์ที่ใกล้ที่สุดมีสถานะ 'เต็ม' ให้แนะนำศูนย์ถัดไปที่ยังเปิดรับแทน "
        "ห้ามใช้เครื่องหมายดอกจัน"
    )
    return ask_gemini(prompt, max_tokens=1024, system_instruction=NEARBY_DATA_REPLY_SYSTEM_INSTRUCTION, lang=lang)


# =============================================================================
# SECTION 6: STATE MACHINE (Class-Based Workflows)
# =============================================================================

class UserSession:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.state = "IDLE"
        self.data: Dict[str, Any] = {}
        self.created_at = time.time()
        self.updated_at = time.time()
        self.language = "TH"
        self.message_count = 0
        self.last_intent = ""
    
    def update(self, state: str = None, data: dict = None):
        if state:
            self.state = state
        if data:
            self.data.update(data)
        self.updated_at = time.time()
        self.message_count += 1
    
    def is_expired(self, ttl_minutes: int = SESSION_TTL_MINUTES) -> bool:
        return time.time() - self.updated_at > ttl_minutes * 60
    
    def reset(self):
        self.state = "IDLE"
        self.data = {}
        self.updated_at = time.time()


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, UserSession] = {}
        self._lock = threading.Lock()
    
    def get(self, user_id: str) -> UserSession:
        with self._lock:
            session = self._sessions.get(user_id)
            if session is None or session.is_expired():
                session = UserSession(user_id)
                try:
                    user_record = sheets_mgr.get_user_record(user_id)
                    preferred_lang = (user_record or {}).get("preferred_language", "").strip().upper()
                    if preferred_lang:
                        session.language = preferred_lang
                except Exception as e:
                    Logger.error("Session", f"Could not load preferred_language for user: {e}")
                self._sessions[user_id] = session
            return session
    
    def update(self, user_id: str, state: str = None, data: dict = None):
        session = self.get(user_id)
        session.update(state=state, data=data)
        return session
    
    def reset(self, user_id: str):
        session = self.get(user_id)
        session.reset()
    
    def delete(self, user_id: str):
        with self._lock:
            self._sessions.pop(user_id, None)
    
    def cleanup_expired(self) -> int:
        with self._lock:
            expired = [uid for uid, s in self._sessions.items() if s.is_expired()]
            for uid in expired:
                del self._sessions[uid]
            return len(expired)

sessions = SessionManager()
USER_STATES: Dict[str, str] = {}
USER_DATA: Dict[str, dict] = {}


def update_legacy_state(user_id: str, state: str, data: dict = None):
    USER_STATES[user_id] = state
    if data:
        USER_DATA[user_id] = data
    sessions.update(user_id, state=state, data=data or {})


# =============================================================================
# SECTION 7: GEMINI AI OPTIMIZATION (Concise Responses with Citations)
# =============================================================================

gemini_model = None
_gemini_initialized = False


def get_out_of_scope_decline_text(lang: str = "TH") -> str:
    """
    Fixed, deterministic decline message for AI_QUERY/FAQ questions the
    classifier has flagged as in_scope=False (general trivia with no
    connection at all to flooding/safety/accidents/disaster health — e.g.
    "ยีราฟคอยาวกี่เมตร", "นักแข่ง F1 มีกี่คน").

    This is plain Python string selection, not model output — intentionally
    bypassing Gemini entirely for these cases. Relying on the model to
    notice a question is off-topic *and* choose to refuse in the same
    generation step it could otherwise just answer has proven unreliable in
    practice (the "always search before answering" instruction tends to
    win out over a softer "refuse if off-topic" one for harmless-seeming
    trivia). Deciding in_scope at classification time and short-circuiting
    to this fixed text guarantees the refusal actually happens, and as a
    bonus skips a wasted Gemini + Google Search call entirely.
    """
    if lang == "MY":
        return (
            "Maaf, saya direka khas untuk membantu isu banjir, keselamatan, kemalangan, "
            "dan kesihatan yang berkaitan bencana sahaja. Taip 'ทำอะไรได้บ้าง' untuk lihat "
            "apa yang boleh saya bantu."
        )
    if lang == "EN":
        return (
            "Sorry, I'm built to help specifically with flooding, safety, accidents, and "
            "disaster-related health questions. Type \"what can you do\" to see what I can help with."
        )
    return (
        "ขออภัยครับ ผมถูกออกแบบมาเพื่อช่วยเหลือเฉพาะเรื่องน้ำท่วม ความปลอดภัย อุบัติเหตุ "
        "และสุขภาพที่เกี่ยวข้องกับภัยพิบัติเท่านั้นครับ พิมพ์ 'ทำอะไรได้บ้าง' เพื่อดูสิ่งที่ผมช่วยได้ครับ"
    )


FLOODCARE_SYSTEM_INSTRUCTION = (
    "คุณคือ FLOODCARE AI ผู้ช่วยอัจฉริยะด้านภัยน้ำท่วมและเหตุฉุกเฉินในประเทศไทย\n"
    "บุคลิกภาพ: สุภาพ มืออาชีพ กระชับ และจริงใจ เน้นการให้ข้อมูลที่รวดเร็วและปลอดภัย\n"
    "ใช้สรรพนามแทนตนเองว่า 'ผม' หรือ 'น้องบอท' ห้ามใช้คำว่า 'ฉัน' และห้ามใช้อีโมจิในข้อความตอบกลับทุกกรณี\n\n"
    "ข้อจำกัดด้านขอบเขตการตอบคำถามอย่างเข้มงวด (STRICT SCOPE LOCK):\n"
    "1. ตอบเฉพาะเรื่อง: 1) อุทกภัย 2) ความปลอดภัย/กู้ภัย 3) อุบัติเหตุ/การบาดเจ็บ/การปฐมพยาบาล 4) สุขภาพกายและใจจากภัยพิบัติ\n"
    "2. หากเป็นเรื่องอื่นนอกเหนือจากนี้ ให้ปฏิเสธอย่างสุภาพและมินิมอล เช่น:\n"
    "   'ขออภัยครับ ผมถูกออกแบบมาเพื่อช่วยเหลือด้านน้ำท่วม ความปลอดภัย และอุบัติเหตุเท่านั้น หากมีคำถามด้านนี้ผมยินดีตอบครับ'\n\n"
    "กฎการตอบคำถาม (CRITICAL FORMATTING RULES):\n"
    "1. **ห้ามใช้อีโมจิเด็ดขาด**\n"
    "2. ตอบเป็นข้อๆ (1. 2. 3.) และเว้นบรรทัดให้ชัดเจน\n"
    "3. **เน้นความปลอดภัยสูงสุด:** หากสถานการณ์ดูอันตราย ให้ขึ้นต้นด้วยคำเตือนและแนะนำขั้นตอนการเอาตัวรอดหรือเบอร์ฉุกเฉินทันที\n"
    "4. **ความกระชับ:** สูงสุดไม่เกิน 3-4 ข้อ แต่ละข้อไม่เกิน 1 บรรทัด\n"
    "5. **ห้ามระบุแหล่งที่มาในเนื้อความ** (เช่น 'อ้างอิงจาก...') เด็ดขาด(ที่มา: ...)' หรือ '(ข้อมูลจาก: ...)' แทรกในข้อความ) เพราะระบบจะแสดงแหล่งอ้างอิงแยกไว้ด้านล่างของข้อความให้เองโดยอัตโนมัติ ให้เนื้อหาคำตอบเป็นเนื้อข้อมูลล้วนๆ\n"
    "4. ห้ามใช้เครื่องหมายดอกจันสองตัว (**) หรือดอกจันตัวเดียว (*) ในข้อความอย่างเด็ดขาด เพราะทำให้ข้อความรกบนระบบ LINE ให้เว้นบรรทัดและเขียนข้อความให้อ่านง่ายแทน\n"
    "5. คำตอบทุกข้อความต้องจบประโยคอย่างสมบูรณ์เสมอ ห้ามหยุดหรือตัดจบกลางประโยค กลางคำ หรือกลางรายการเด็ดขาด — แต่ความสมบูรณ์นี้หมายถึง 'จบประโยคให้ครบ' ไม่ใช่ข้ออ้างให้ตอบยืดยาวเกินความจำเป็น ให้ตัดสินใจล่วงหน้าว่าจะพูดกี่ประเด็นแล้วจบให้ครบตามข้อ 2\n"
    "6. หากมีลิงก์อ้างอิงให้จัดเก็บไว้ในโครงสร้างส่วนท้ายของการ์ดหรือแสดงผลเป็นรูปแบบปุ่มกดให้เรียบร้อยสวยงาม ไม่เขียนลิงก์ยาวเปลือยในตัวข้อความหลัก\n"
    "7. หากคำถามของผู้ใช้สื่อถึงความเครียด ความกลัว หรือความเดือดร้อน (เช่น ถามเรื่องอาการเจ็บป่วยของตนเอง คนในครอบครัว หรือน้ำท่วมบ้านตัวเอง) ให้เปิดประโยคแรกด้วยคำรับรู้ความรู้สึกสั้นๆ ไม่เกิน 1 บรรทัด ก่อนให้ข้อมูล เช่น 'เข้าใจว่าตอนนี้คงเป็นห่วงมากเลยนะครับ' แล้วจึงตอบข้อมูลที่เป็นประโยชน์ต่อทันที ห้ามใส่คำปลอบใจซ้ำหลายประโยคหรือทำให้คำตอบยาวเกินไป"
)


def init_gemini():
    global gemini_model, _gemini_initialized
    if _gemini_initialized:
        return gemini_model is not None
    
    if not GEMINI_API_KEY or not genai:
        _gemini_initialized = True
        return False
    
    try:
        gemini_model = genai.Client(api_key=GEMINI_API_KEY)
        _gemini_initialized = True
        Logger.info("Gemini", "Initialized successfully (google-genai SDK)")
        return True
    except Exception as e:
        Logger.error("Gemini", f"Initialization failed: {e}")
        _gemini_initialized = True
        return False


def ask_gemini(prompt: str, max_tokens: int = 8192, system_instruction: str = None, lang: str = "TH") -> str:
    """
    Optimized Gemini API call.
    - Uses full token capacity (8192) to avoid truncation issues.
    - system_instruction defaults to FLOODCARE_SYSTEM_INSTRUCTION (the usual
      bulleted-list persona) but callers that need a different reply shape
      (e.g. a natural one-paragraph conversational answer, like
      compose_water_level_reply) can pass their own instead.
    - lang="MY" appends a directive forcing the reply into Bahasa Melayu
      instead of Thai, on top of whichever system_instruction is used.
    """
    start_time = time.time()
    if not init_gemini():
        if lang == "MY":
            return (
                "Maaf, sistem AI tidak tersedia buat masa ini. Jika dalam keadaan bahaya segera, "
                "sila hubungi talian kecemasan 1784 dengan serta-merta."
            )
        if lang == "EN":
            return (
                "Sorry, the AI system is temporarily unavailable. If this is an urgent emergency, "
                "please call the DDPM hotline 1784 right away."
            )
        return "⚠️ ขออภัยครับ ระบบ AI ไม่พร้อมใช้งานชั่วคราว หากอยู่ในอันตรายเร่งด่วน โทร ปภ. 1784 ได้ทันทีครับ"

    effective_system_instruction = system_instruction or FLOODCARE_SYSTEM_INSTRUCTION
    if lang == "MY":
        effective_system_instruction += (
            "\n\nสำคัญ: ตอบเป็นภาษามลายู (Bahasa Melayu) เท่านั้น ห้ามตอบเป็นภาษาไทยหรืออังกฤษ "
            "ยกเว้นชื่อเฉพาะ เช่น ชื่อสถานที่ ชื่อสถานีวัดน้ำ ที่ไม่มีคำแปล ให้คงเป็นภาษาไทยตามเดิม"
        )
    elif lang == "EN":
        effective_system_instruction += (
            "\n\nIMPORTANT: Respond in English only, never Thai or Malay — except proper nouns "
            "like place names or water-station names that have no English equivalent, which "
            "should stay in their original Thai form."
        )

    cache_key = f"gemini:{lang}:{hashlib.md5((effective_system_instruction + '|' + prompt).encode()).hexdigest()}"
    cached = cache.general.get(cache_key)
    if cached:
        elapsed = (time.time() - start_time) * 1000
        Logger.perf("Gemini", "cache_hit", elapsed)
        return cached
    
    try:
        response = gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=effective_system_instruction,
                max_output_tokens=max_tokens,
                temperature=0.3,
                safety_settings=[
                    genai_types.SafetySetting(
                        category="HARM_CATEGORY_DANGEROUS_CONTENT",
                        threshold="BLOCK_ONLY_HIGH",
                    ),
                ],
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        result = clean_text_for_line((response.text or "").strip())
        cache.general.set(cache_key, result, ttl=300)
        
        elapsed = (time.time() - start_time) * 1000
        Logger.perf("Gemini", "api_call", elapsed, {"prompt_len": len(prompt)})
        return result
    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        Logger.error("Gemini", f"API error: {e}", {"elapsed_ms": round(elapsed, 1)})
        return (
            "Maaf, sistem AI menghadapi masalah sementara. Jika dalam keadaan bahaya segera, "
            "sila hubungi talian kecemasan 1784 dengan serta-merta."
            if lang == "MY" else
            "⚠️ ขออภัยครับ ระบบ AI ขัดข้องชั่วคราว หากอยู่ในอันตรายเร่งด่วน โทร ปภ. 1784 ได้ทันทีครับ"
        )




def ask_gemini_with_search(question: str, max_tokens: int = 8192, lang: str = "TH") -> dict:
    """
    Gemini API call with Google Search grounding.
    - Uses full token capacity (8192) to avoid truncation issues.
    - lang="MY" makes Gemini answer in Bahasa Melayu instead of Thai —
      place names, station names, and other proper nouns from the source
      data stay as-is since they don't have a Malay equivalent.
    """
    if not init_gemini():
        if lang == "MY":
            fallback_text = (
                "Maaf, sistem AI tidak tersedia buat masa ini. Jika dalam keadaan bahaya segera, "
                "sila hubungi talian kecemasan 1784 dengan serta-merta."
            )
        elif lang == "EN":
            fallback_text = (
                "Sorry, the AI system is temporarily unavailable. If this is an urgent emergency, "
                "please call the DDPM hotline 1784 right away."
            )
        else:
            fallback_text = "⚠️ ขออภัยครับ ระบบ AI ไม่พร้อมใช้งานชั่วคราว หากอยู่ในอันตรายเร่งด่วน โทร ปภ. 1784 ได้ทันทีครับ"
        return {"answer": fallback_text, "sources": []}

    start_time = time.time()
    prompt = (
        "ค้นหาข้อมูลอย่างละเอียดและตอบคำถามนี้โดยทำตามกฎต่อไปนี้อย่างเคร่งครัด:\n\n"
        f"คำถาม: {question}\n\n"
        "กฎในการตอบเพื่อความเป็นระเบียบ อ่านง่าย และกระชับ:\n"
        "1. ห้ามใช้เครื่องหมายดอกจันเดี่ยวหรือสองชั้น (*) ในข้อความอย่างเด็ดขาด\n"
        "2. ตอบเป็นข้อๆ เสมอ โดยขึ้นต้นแต่ละประเด็นด้วยเลขข้อ (1. 2. 3. ...) แล้วเว้นบรรทัดระหว่างข้อ ยกเว้นคำตอบสั้นมากที่มีประเด็นเดียวจริงๆ ให้ตอบเป็นประโยคปกติได้โดยไม่ต้องใส่เลขข้อ\n"
        "3. **เน้นความกระชับเป็นสำคัญ** ตอบเฉพาะสิ่งที่ผู้ใช้ถามจริงๆ ไม่ต้องใส่ข้อมูลพื้นหลังหรือรายละเอียดปลีกย่อยที่ไม่จำเป็น สูงสุดไม่เกิน 4-5 ข้อ แต่ละข้อยาวไม่เกิน 1-2 บรรทัด รวมความยาวทั้งหมดไม่ควรเกินประมาณ 60-80 คำ เว้นแต่คำถามนั้นจำเป็นต้องมีข้อมูลครบทุกขั้นตอนจริงๆ จึงตอบยาวกว่านี้ได้เท่าที่จำเป็น\n"
        "4. **ห้ามระบุแหล่งที่มา/อ้างอิงไว้ในเนื้อความคำตอบเด็ดขาด** ห้ามเขียนคำว่า (ที่มา: ...) หรือ (ข้อมูลจาก: ...) แทรกในข้อความ เพราะระบบจะดึงรายชื่อแหล่งอ้างอิงไปแสดงแยกไว้ด้านล่างข้อความให้เองโดยอัตโนมัติ\n"
        "5. จบข้อความให้ครบประโยคเสมอ ห้ามหยุดหรือตัดจบกลางประโยค กลางคำ หรือกลางรายการเด็ดขาด แต่ให้วางแผนตอบให้กระชับตามข้อ 3 ไว้ล่วงหน้า ไม่ใช่ตอบยืดยาวแล้วค่อยจบ\n"
        "6. ลิงก์ URL อ้างอิงทั้งหมดจะถูกแยกไปแสดงด้านล่าง ไม่ต้องระบุลิงก์ยาวในย่อหน้าหลัก"
    )

    try:
        response = gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=(
                    "You are FLOODCARE AI. "
                    + (
                        "Always respond in Bahasa Melayu (Malay), never Thai or English."
                        if lang == "MY" else
                        "Always respond in English, never Thai or Malay."
                        if lang == "EN" else
                        "Always respond in Thai."
                    )
                    + " STRICT SCOPE LOCK: you only answer questions about 1) flooding/disasters "
                    "2) safety/rescue 3) accidents/injuries/first aid 4) physical or mental health "
                    "related to a disaster. If the question is genuinely outside all of these topics "
                    "(e.g. lottery numbers, poems, recipes, unrelated general knowledge), do NOT "
                    "search for or answer it — instead reply with ONLY a short, warm, polite decline "
                    "explaining you're built to help with flooding, safety, and accidents, and invite "
                    "them to ask about those instead. Never let the instruction to always use Google "
                    "Search override this scope check — the scope check always comes first, before "
                    "deciding whether to search at all."
                    + " Be concise — answer only what "
                    "was asked, skip background info or details the user didn't request. Structure "
                    "the answer as a numbered list (1. 2. 3. ...) with a line break between each "
                    "point, max 4-5 points, each point 1-2 short lines, total answer roughly 60-80 "
                    "words unless the question genuinely requires a complete step-by-step procedure. "
                    "This length limit applies with equal strictness no matter which language you "
                    "answer in — do not become more thorough, add extra sections, or cover more "
                    "sub-topics (e.g. separate full sections for causes, remedies, and when to see a "
                    "doctor) just because you were asked to answer in a different language. "
                    "Only skip numbering for a genuinely single-point, very short answer. No "
                    "asterisks. Never state or cite sources inline in the answer text (no "
                    "'(ที่มา: ...)' or similar) — the system displays the reference sources "
                    "separately below the message automatically. Use the Google Search tool to "
                    "ground your answer. Always finish complete sentences — never truncate or stop "
                    "mid-sentence — but plan for a concise answer up front rather than writing long "
                    "and cutting it off. Place names, station names, and other proper nouns from the "
                    "source data should stay in their original Thai form even when responding in "
                    "Bahasa Melayu, since they don't have a translated equivalent. For every in-scope "
                    "question you do answer, you MUST call the Google Search tool at least once before "
                    "answering, even if you believe you already know the answer from your own "
                    "training — an answer with no search sources is not acceptable for this "
                    "application, since users rely on the cited sources to verify safety-critical "
                    "information themselves. This search requirement does not apply to out-of-scope "
                    "questions you are declining — decline those directly without searching."
                ),
                max_output_tokens=max_tokens,
                temperature=0.2,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
        raw_text = clean_text_for_line((response.text or "").strip())

        sources = []
        try:
            for candidate in response.candidates:
                grounding = getattr(candidate, "grounding_metadata", None)
                if grounding:
                    for chunk in getattr(grounding, "grounding_chunks", None) or []:
                        web = getattr(chunk, "web", None)
                        if web:
                            title = getattr(web, "title", "") or ""
                            uri = getattr(web, "uri", "") or ""
                            if uri:
                                sources.append({"title": title, "url": uri})
        except Exception:
            pass

        elapsed = (time.time() - start_time) * 1000
        Logger.perf("Gemini", "search_call", elapsed)
        return {"answer": raw_text, "sources": sources}
    except Exception as e:
        Logger.info("Gemini", f"Search grounding failed ({e}), falling back to plain ask_gemini")
        answer = ask_gemini(prompt, max_tokens=max_tokens, lang=lang)
        return {"answer": answer, "sources": []}


def clean_text_for_line(text: str) -> str:
    if not text:
        return ""
    return text.replace("**", "").replace("*", "").replace("###", "").replace("##", "").replace("#", "")


def extract_sheet_id(sheet_var: str) -> str:
    if not sheet_var:
        return ""
    if "/d/" in sheet_var:
        parts = sheet_var.split("/d/")
        if len(parts) > 1:
            sub = parts[1].split("/")[0].strip()
            return sub
    return sheet_var.strip()


def format_phone_th(value) -> str:
    """
    Repairs a Thai phone number that lost its leading '0'.

    Root cause of the bug: gspread's get_all_records() auto-numericises any
    cell that *looks* like a number, so a phone number stored as text
    ("0812345678") comes back as the int 812345678 — the leading zero is a
    no-op in a number and silently disappears. We now pass
    numericise_ignore=['all'] everywhere we call get_all_records(), which
    stops this at the source for anything written going forward. This
    helper is a second line of defense for rows that were already saved
    with the zero missing (e.g. from before that fix, or from a manual
    paste into the sheet that Google auto-converted to a number).

    Thai mobile numbers are always 10 digits starting with 0. If we see
    exactly 9 digits, that's the unmistakable signature of a dropped
    leading zero, so we restore it. Anything else (already 10 digits,
    landlines, blank/'-', or malformed input) is returned unchanged.
    """
    if value is None:
        return value
    s = str(value).strip()
    if not s or s == "-":
        return s
    digits = "".join(filter(str.isdigit, s))
    if digits and len(digits) == 9 and not s.startswith("0"):
        return "0" + digits
    return s


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def generate_case_id() -> str:
    import uuid
    today = get_bangkok_time().strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"SOS-{today}-{suffix}"


def generate_need_id() -> str:
    import uuid
    today = get_bangkok_time().strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"NEED-{today}-{suffix}"


# =============================================================================
# SECTION 9: GOOGLE SHEETS OPTIMIZATION
# =============================================================================

class SheetsManager:
    def __init__(self):
        self._client = None
        self._initialized = False
        self._last_error = ""
        self._lock = threading.Lock()
    
    def get_client(self):
        if self._initialized and self._client:
            return self._client
        
        with self._lock:
            if self._initialized:
                return self._client
            
            if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
                self._last_error = "Environment variables not set"
                self._initialized = True
                return None
            
            try:
                json_str = GOOGLE_SERVICE_ACCOUNT_JSON.strip()
                if json_str.startswith("'") and json_str.endswith("'"):
                    json_str = json_str[1:-1].strip()
                if json_str.startswith('"') and json_str.endswith('"'):
                    json_str = json_str[1:-1].strip()
                
                creds_dict = json.loads(json_str)
                self._client = gspread.service_account_from_dict(creds_dict)
                self._initialized = True
                
                self._auto_setup()
                self._last_error = "Connected"
                Logger.info("Sheets", "Client initialized")
                return self._client
            except Exception as e:
                self._last_error = f"Auth failed: {e}"
                self._initialized = True
                Logger.error("Sheets", f"Init failed: {e}")
                return None
    
    def _auto_setup(self):
        try:
            sheet = self._client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            existing = [w.title for w in sheet.worksheets()]
            
            required_sheets = {
                "users": ["user_id", "household_id", "first_name", "last_name", "phone",
                         "housing_type", "house_no", "condo_floor", "condo_room",
                         "province", "district", "sub_district", "gps_lat", "gps_lon",
                         "member_count", "emergency_contact", "sms_enabled",
                         "consent_pdpa", "register_date", "status", "preferred_language",
                         "water_alert_enabled", "water_alert_radius_km"],
                "sos_requests": ["request_id", "household_id", "user_id", "timestamp", "latitude", "longitude",
                                "people_count", "children", "elderly", "bedridden", "pets",
                                "water_level", "note", "priority", "status",
                                "accepted_at", "completed_at", "responder_name", "last_notified_status"],
                "user_needs": ["need_id", "timestamp", "user_id", "first_name", "last_name", "phone",
                              "latitude", "longitude", "categories", "details", "urgency", "status",
                              "halal_required", "volunteer_name", "delivered_at", "last_notified_status"],
                "Shelters": ["ShelterID", "Name", "Province", "District", "Subdistrict", "Latitude",
                            "Longitude", "Capacity", "Occupancy", "Status",
                            "Beds", "Toilets", "Parking", "Facilities"],
                "Water_Levels": ["StationCode", "Name", "River", "Location", "Lat", "Lon",
                                "WaterLevel", "BankLevel", "Situation", "Trend", "Time"],
                "Contacts": ["ContactID", "Name", "Role", "Phone"],
                "AI_Logs": ["Timestamp", "UserID", "Intent", "Question", "Answer", "ResponseTimeMs"],
                "System_Logs": ["Timestamp", "Level", "Module", "Message", "UserID"],
                # Durable dedup state for the Water Alert Engine — one row per
                # (user_id, station_code) pair. Kept as its own sheet (not
                # in-memory) specifically so alert history survives an app/
                # Render restart, per the implementation spec's requirement.
                "Water_Alert_State": ["user_id", "station_code", "last_situation",
                                     "last_alert_at", "last_measure_time", "updated_at"],
            }
            
            for name, headers in required_sheets.items():
                if name not in existing:
                    ws = sheet.add_worksheet(title=name, rows="3000", cols=len(headers) + 5)
                    ws.append_row(headers)
                    Logger.info("Sheets", f"Created worksheet: {name}")

            # Patch sheets that already exist but are missing newer columns —
            # e.g. a 'Shelters' sheet set up before Subdistrict/Beds/Toilets/
            # Parking/Facilities were added to the schema. Only ever appends
            # missing header names to the end of row 1; never touches,
            # reorders, or deletes existing columns or data, so this is safe
            # to run on every startup.
            for name, headers in required_sheets.items():
                if name not in existing:
                    continue  # just created above with the full header set already
                try:
                    ws = sheet.worksheet(name)
                    current_headers = ws.row_values(1)
                    missing = [h for h in headers if h not in current_headers]
                    if missing:
                        start_col = len(current_headers) + 1
                        for i, h in enumerate(missing):
                            ws.update_cell(1, start_col + i, h)
                        Logger.info("Sheets", f"Patched '{name}': added missing columns {missing}")
                except Exception as e:
                    Logger.error("Sheets", f"Column patch failed for '{name}': {e}")
            
            if "Contacts" not in existing:
                ws = sheet.worksheet("Contacts")
                defaults = [
                    ["CT001", "ปภ. (กรมป้องกันและบรรเทาสาธารณภัย)", "รับแจ้งเหตุเตือนภัยและช่วยเหลืออุทกภัยสายด่วน", "1784"],
                    ["CT002", "สพฉ. (สถาบันการแพทย์ฉุกเฉินแห่งชาติ)", "รับส่งต่อผู้ป่วยฉุกเฉินทางการแพทย์", "1669"],
                    ["CT003", "ตำรวจทางหลวง", "ประสานงานความช่วยเหลือเส้นทางน้ำท่วม", "1193"],
                    ["CT004", "หน่วยกู้ชีพวชิรพยาบาล", "กู้ภัยทางน้ำและอุบัติเหตุ", "1554"],
                ]
                for row in defaults:
                    ws.append_row(row)
            
            # Seed default shelters if the sheet is brand new OR already exists but
            # has no data rows yet (e.g. headers only, as when the user set it up by hand).
            shelters_ws = sheet.worksheet("Shelters")
            shelters_is_empty = len(shelters_ws.get_all_values()) <= 1
            if shelters_is_empty:
                ws = shelters_ws
                shelter_defaults = [
                    ["S001", "โรงเรียนเทศบาล 2 (มลายูบางกอก)", "ยะลา", "เมืองยะลา", "",
                     6.5458, 101.2825, "", "", "เปิดรับ", "", "", "", ""],
                    ["S002", "โรงเรียนเทศบาล 3 (วัดพุทธภูมิ)", "ยะลา", "เมืองยะลา", "",
                     6.5445, 101.2912, "", "", "เปิดรับ", "", "", "", ""],
                    ["S003", "โรงเรียนเทศบาล 4 (ธนวิถี)", "ยะลา", "เมืองยะลา", "",
                     6.5401, 101.2833, "", "", "เปิดรับ", "", "", "", ""],
                    ["S004", "โรงเรียนเทศบาล 5 (บ้านตลาดเก่า)", "ยะลา", "เมืองยะลา", "",
                     6.5385, 101.2980, "", "", "เปิดรับ", "", "", "", ""],
                    ["S005", "ศูนย์เยาวชน (TK Park)", "ยะลา", "เมืองยะลา", "",
                     6.5470, 101.2905, "", "", "เปิดรับ", "", "", "", ""],
                    # NOTE: S006 has no verified Lat/Long yet. get_shelters_from_sheet()
                    # will silently skip this row until coordinates are filled in.
                    ["S006", "อาคารศรีนิบง", "ยะลา", "เมืองยะลา", "",
                     "", "", "", "", "เปิดรับ", "", "", "", ""],
                ]
                for row in shelter_defaults:
                    ws.append_row(row)
                Logger.info("Sheets", f"Seeded {len(shelter_defaults)} default shelter rows")
        except Exception as e:
            Logger.error("Sheets", f"Auto-setup error: {e}")
    
    def batch_append(self, worksheet_name: str, rows: list):
        client = self.get_client()
        if not client:
            return False
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet(worksheet_name)
            if rows:
                ws.append_rows(rows, value_input_option='RAW')
                cache.sheets.delete(f"sheets:{worksheet_name}")
            return True
        except Exception as e:
            Logger.error("Sheets", f"Batch append error: {e}")
            return False

    def append_row_by_headers(self, worksheet_name: str, row_dict: dict) -> bool:
        """
        Appends a row built to match the sheet's ACTUAL header row (read fresh
        at write time) instead of a hardcoded column-position list.

        This is what keeps data landing in the right columns even if the
        live Google Sheet was created before a column was added/reordered in
        code (e.g. 'household_id') — a value is only ever written under the
        header it belongs to, by name, so nothing downstream ever shifts.
        Any header the sheet doesn't have yet is simply left blank for that
        row (instead of corrupting every column after it); add the column
        header in the sheet whenever you want that field populated.
        """
        client = self.get_client()
        if not client:
            return False
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet(worksheet_name)
            headers = ws.row_values(1)
            if not headers:
                Logger.error("Sheets", f"'{worksheet_name}' has no header row — cannot append by header name")
                return False

            missing = [k for k in row_dict.keys() if k not in headers]
            if missing:
                Logger.info("Sheets", f"'{worksheet_name}' is missing columns {missing} — those values were not saved. Add these headers to the sheet to start storing them.")

            row = [row_dict.get(h, "") for h in headers]
            ws.append_rows([row], value_input_option='RAW')
            cache.sheets.delete(f"sheets:{worksheet_name}")
            return True
        except Exception as e:
            Logger.error("Sheets", f"append_row_by_headers error: {e}")
            return False
    
    def get_all_records(self, worksheet_name: str) -> list:
        cache_key = f"sheets:{worksheet_name}"
        cached = cache.sheets.get(cache_key)
        if cached:
            return cached
        
        client = self.get_client()
        if not client:
            return []
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet(worksheet_name)
            # numericise_ignore=['all'] stops gspread from auto-converting
            # number-looking cells (like phone numbers) into int/float,
            # which is what strips a leading '0' off a phone number. All
            # numeric fields we actually need as numbers (Capacity, lat/lon,
            # etc.) are already cast explicitly with int()/float() by the
            # calling code, so this is safe to apply everywhere.
            records = ws.get_all_records(numericise_ignore=['all'])
            cache.sheets.set(cache_key, records, ttl=300)
            return records
        except Exception as e:
            Logger.error("Sheets", f"Get records error: {e}")
            return []
    
    def update_row_by_id(self, worksheet_name: str, id_column: str, id_value: str, update_dict: dict) -> bool:
        """
        Finds the row where `id_column` == `id_value` and updates only the
        fields in `update_dict`, matched by header name (same header-name
        matching approach as append_row_by_headers, so nothing shifts if the
        sheet's column order differs from the code's).
        """
        client = self.get_client()
        if not client:
            return False
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet(worksheet_name)
            headers = ws.row_values(1)
            if id_column not in headers:
                Logger.error("Sheets", f"'{worksheet_name}' has no '{id_column}' column")
                return False

            id_col_index = headers.index(id_column) + 1
            id_cells = ws.col_values(id_col_index)
            row_num = next((i + 1 for i, v in enumerate(id_cells) if v == id_value), None)
            if row_num is None:
                return False

            for field, value in update_dict.items():
                if field in headers:
                    col_index = headers.index(field) + 1
                    ws.update_cell(row_num, col_index, value)

            cache.sheets.delete(f"sheets:{worksheet_name}")
            return True
        except Exception as e:
            Logger.error("Sheets", f"update_row_by_id error: {e}")
            return False

    def update_cell(self, worksheet_name: str, row: int, col: int, value):
        client = self.get_client()
        if not client:
            return False
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet(worksheet_name)
            ws.update_cell(row, col, value)
            return True
        except Exception as e:
            Logger.error("Sheets", f"Update cell error: {e}")
            return False

    def get_user_record(self, user_id: str) -> Optional[dict]:
        """Looks up a registered user's row (cached) by LINE user_id."""
        if not user_id or user_id == "unknown":
            return None
        for rec in self.get_all_records("users"):
            if str(rec.get("user_id", "")) == user_id:
                return rec
        return None

    def find_open_case_by_household(self, household_id: str, window_minutes: int = 60):
        """
        Looks for the most recent OPEN sos_requests row belonging to the same
        household within `window_minutes`. Reads live (uncached) so a case
        created moments ago by another household member is always seen.

        Returns (row_number, record_dict) — row_number is 1-indexed as used by
        gspread (header = row 1), or (None, None) if no match.
        """
        if not household_id or household_id in ("-", ""):
            return None, None
        client = self.get_client()
        if not client:
            return None, None
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("sos_requests")
            records = ws.get_all_records(numericise_ignore=['all'])
            now = get_bangkok_time()

            best_row, best_record, best_time = None, None, None
            for idx, rec in enumerate(records, start=2):
                if str(rec.get("household_id", "")) != household_id:
                    continue
                if str(rec.get("status", "")).strip().upper() != "OPEN":
                    continue
                ts_raw = str(rec.get("timestamp", ""))
                try:
                    ts = datetime.datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
                    ts = ts.replace(tzinfo=now.tzinfo)
                except Exception:
                    continue
                age_minutes = (now - ts).total_seconds() / 60
                if age_minutes < 0 or age_minutes > window_minutes:
                    continue
                if best_time is None or ts > best_time:
                    best_row, best_record, best_time = idx, rec, ts
            return best_row, best_record
        except Exception as e:
            Logger.error("Sheets", f"find_open_case_by_household error: {e}")
            return None, None

    def merge_sos_case(self, row_number: int, updates: dict) -> bool:
        """Overwrites specific columns of an existing sos_requests row by name."""
        client = self.get_client()
        if not client:
            return False
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("sos_requests")
            header = ws.row_values(1)
            col_map = {name: i + 1 for i, name in enumerate(header)}
            cells = [
                gspread.Cell(row_number, col_map[name], str(value))
                for name, value in updates.items() if name in col_map
            ]
            if cells:
                ws.update_cells(cells, value_input_option='RAW')
            cache.sheets.delete("sheets:sos_requests")
            return True
        except Exception as e:
            Logger.error("Sheets", f"merge_sos_case error: {e}")
            return False

    def update_sos_status(self, case_id: str, new_status: str, responder_name: str = "-") -> Optional[dict]:
        """
        Updates an sos_requests row's status by request_id (e.g. OPEN -> IN_PROGRESS -> CLOSED),
        used by the dashboard's "รับเคส" / "ปิดเคส" actions. Also stamps accepted_at /
        completed_at if those columns exist, so responders can see how long a case took.

        Returns the case's record dict (as it was before the update, so callers can
        read its user_id to notify the reporter on LINE) — or None if not found / on error.
        """
        client = self.get_client()
        if not client:
            return None
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("sos_requests")
            records = ws.get_all_records(numericise_ignore=['all'])
            row_number = None
            matched_record = None
            for idx, rec in enumerate(records, start=2):
                if str(rec.get("request_id", "")) == case_id:
                    row_number = idx
                    matched_record = rec
                    break
            if not row_number:
                return None

            now_str = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")
            updates = {"status": new_status, "responder_name": responder_name or "-"}
            if new_status == "IN_PROGRESS":
                updates["accepted_at"] = now_str
            elif new_status == "CLOSED":
                updates["completed_at"] = now_str

            header = ws.row_values(1)
            col_map = {name: i + 1 for i, name in enumerate(header)}
            cells = [
                gspread.Cell(row_number, col_map[name], str(value))
                for name, value in updates.items() if name in col_map
            ]
            if cells:
                ws.update_cells(cells, value_input_option='RAW')
            cache.sheets.delete("sheets:sos_requests")
            return matched_record
        except Exception as e:
            Logger.error("Sheets", f"update_sos_status error: {e}")
            return None

    def update_need_status(self, need_id: str, new_status: str, volunteer_name: str = None) -> Optional[dict]:
        """Updates a user_needs row's status by need_id — used by the dashboard's
        need-fulfillment actions. Stamps delivered_at when marked delivered/done.
        volunteer_name (if given) records who claimed/is handling the request."""
        client = self.get_client()
        if not client:
            return None
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("user_needs")
            records = ws.get_all_records(numericise_ignore=['all'])
            row_number, matched_record = None, None
            for idx, rec in enumerate(records, start=2):
                if str(rec.get("need_id", "")) == need_id:
                    row_number, matched_record = idx, rec
                    break
            if not row_number:
                return None

            updates = {"status": new_status}
            if new_status.upper() in ("DELIVERED", "DONE", "COMPLETED"):
                updates["delivered_at"] = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")
            if volunteer_name:
                updates["volunteer_name"] = volunteer_name

            header = ws.row_values(1)
            col_map = {name: i + 1 for i, name in enumerate(header)}
            cells = [
                gspread.Cell(row_number, col_map[name], str(value))
                for name, value in updates.items() if name in col_map
            ]
            if cells:
                ws.update_cells(cells, value_input_option='RAW')
            cache.sheets.delete("sheets:user_needs")
            return matched_record
        except Exception as e:
            Logger.error("Sheets", f"update_need_status error: {e}")
            return None

    def update_shelter_occupancy(self, shelter_id: str, new_occupancy: int) -> Optional[dict]:
        """Updates a Shelters row's Occupancy (and recomputed Status label) by ShelterID —
        used by the dashboard's +/- occupancy counter."""
        client = self.get_client()
        if not client:
            return None
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("Shelters")
            records = ws.get_all_records(numericise_ignore=['all'])
            row_number, matched_record = None, None
            for idx, rec in enumerate(records, start=2):
                if str(rec.get("ShelterID", "")) == shelter_id:
                    row_number, matched_record = idx, rec
                    break
            if not row_number:
                return None

            try:
                capacity = int(float(matched_record.get("Capacity", 0) or 0))
            except (TypeError, ValueError):
                capacity = 0
            pct = (new_occupancy / capacity * 100) if capacity > 0 else 0
            status_label = "เต็ม" if pct >= 100 else "ใกล้เต็ม" if pct >= 80 else "ว่าง"

            updates = {"Occupancy": new_occupancy, "Status": status_label}
            header = ws.row_values(1)
            col_map = {name: i + 1 for i, name in enumerate(header)}
            cells = [
                gspread.Cell(row_number, col_map[name], str(value))
                for name, value in updates.items() if name in col_map
            ]
            if cells:
                ws.update_cells(cells, value_input_option='RAW')
            cache.sheets.delete("sheets:Shelters")
            cache.sheets.delete("sheets:shelters:normalized")
            return matched_record
        except Exception as e:
            Logger.error("Sheets", f"update_shelter_occupancy error: {e}")
            return None

    def overwrite_water_levels(self, stations: list) -> bool:
        """
        Replaces the entire 'Water_Levels' sheet with fresh station data in
        one batch call (clear + single update), instead of appending —
        station rows get updated in place on every refresh rather than
        piling up duplicates. Used by the 10-minute background refresh job
        so 'Water_Levels' always holds live ThaiWater data other tools/views
        can read directly, without each request hitting the ThaiWater API.
        """
        client = self.get_client()
        if not client or not stations:
            return False
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("Water_Levels")
            header = ["StationCode", "Name", "River", "Location", "Lat", "Lon",
                      "WaterLevel", "BankLevel", "Situation", "Trend", "Time"]
            rows = [[s.get(h, "") for h in header] for s in stations]
            ws.clear()
            ws.update([header] + rows, value_input_option='RAW')
            cache.sheets.delete("sheets:Water_Levels")
            return True
        except Exception as e:
            Logger.error("Sheets", f"overwrite_water_levels error: {e}")
            return False

    def get_water_alert_state_map(self) -> dict:
        """
        Reads the entire Water_Alert_State sheet into a dict keyed by
        (user_id, station_code) for O(1) lookup while the alert engine
        walks every user/station pair in a single refresh cycle, instead of
        one sheet read per pair.
        """
        records = self.get_all_records("Water_Alert_State")
        return {(str(r.get("user_id", "")), str(r.get("station_code", ""))): r for r in records}

    def upsert_water_alert_state(self, user_id: str, station_code: str, situation: str,
                                   alert_sent: bool, measure_time: str) -> bool:
        """
        Writes/updates the durable dedup row for one (user_id, station_code)
        pair after the alert engine evaluates it. last_alert_at only moves
        forward when alert_sent is True — situation/measure_time are always
        updated so the next cycle compares against the latest reading even
        on a poll where nothing was actually sent (e.g. still cooling down).
        """
        client = self.get_client()
        if not client:
            return False
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("Water_Alert_State")
            records = ws.get_all_records(numericise_ignore=['all'])
            now_str = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")
            row_number = None
            for idx, rec in enumerate(records, start=2):
                if str(rec.get("user_id", "")) == user_id and str(rec.get("station_code", "")) == station_code:
                    row_number = idx
                    break

            header = ws.row_values(1)
            col_map = {name: i + 1 for i, name in enumerate(header)}
            values = {
                "user_id": user_id,
                "station_code": station_code,
                "last_situation": situation,
                "last_measure_time": measure_time,
                "updated_at": now_str,
            }
            if alert_sent:
                values["last_alert_at"] = now_str

            if row_number:
                cells = [gspread.Cell(row_number, col_map[k], str(v)) for k, v in values.items() if k in col_map]
                ws.update_cells(cells, value_input_option='RAW')
            else:
                if not alert_sent:
                    values.setdefault("last_alert_at", "")
                row = [str(values.get(h, "")) for h in header]
                ws.append_row(row, value_input_option='RAW')
            cache.sheets.delete("sheets:Water_Alert_State")
            return True
        except Exception as e:
            Logger.error("Sheets", f"upsert_water_alert_state error: {e}")
            return False

sheets_mgr = SheetsManager()


# =============================================================================
# SECTION 9B: SHELTER (EVACUATION CENTER) DATA
# =============================================================================

SHELTER_STATUS_MAP = {
    "เปิดรับ": {"label": "เปิดรับ", "bg": "#DCFCE7", "text": "#15803D"},
    "ใกล้เต็ม": {"label": "ใกล้เต็ม", "bg": "#FEF9C3", "text": "#A16207"},
    "เต็ม": {"label": "เต็มแล้ว", "bg": "#FEE2E2", "text": "#B91C1C"},
    "ปิด": {"label": "ปิดชั่วคราว", "bg": "#E5E7EB", "text": "#374151"},
}


def get_shelters_from_sheet(force_refresh: bool = False) -> list:
    """
    Pulls evacuation-center (shelter) records from the 'Shelters' worksheet.
    Normalizes numeric fields (Latitude/Longitude/Capacity/Occupancy) and applies
    a short-lived cache to avoid hammering the Google Sheets API.
    """
    start_time = time.time()
    cache_key = "sheets:shelters:normalized"

    if not force_refresh:
        cached = cache.sheets.get(cache_key)
        if cached is not None:
            Logger.perf("Shelters", "cache_hit", (time.time() - start_time) * 1000)
            return cached

    raw_records = sheets_mgr.get_all_records("Shelters")
    shelters = []

    for row in raw_records:
        try:
            lat_raw = row.get("Latitude")
            lon_raw = row.get("Longitude")
            if lat_raw in (None, "", "-") or lon_raw in (None, "", "-"):
                continue

            lat = float(lat_raw)
            lon = float(lon_raw)

            def _to_int(val):
                try:
                    return int(float(val))
                except (TypeError, ValueError):
                    return 0

            shelters.append({
                "ShelterID": row.get("ShelterID", ""),
                "Name": row.get("Name", "ไม่ระบุชื่อ"),
                "Province": row.get("Province", ""),
                "District": row.get("District", ""),
                "Subdistrict": row.get("Subdistrict", ""),
                "Latitude": lat,
                "Longitude": lon,
                "Capacity": _to_int(row.get("Capacity")),
                "Occupancy": _to_int(row.get("Occupancy")),
                "Status": (row.get("Status") or "เปิดรับ").strip(),
                "Beds": row.get("Beds", "-"),
                "Toilets": row.get("Toilets", "-"),
                "Parking": row.get("Parking", "-"),
                "Facilities": row.get("Facilities", "-"),
            })
        except (ValueError, TypeError) as e:
            Logger.error("Shelters", f"Skipped malformed row: {e}", {"row": row})
            continue

    cache.sheets.set(cache_key, shelters, ttl=300)
    Logger.perf("Shelters", "fetched_from_sheet", (time.time() - start_time) * 1000,
                {"count": len(shelters)})
    return shelters


def find_nearest_shelters(user_lat: float, user_lon: float, limit: int = 5,
                           exclude_full: bool = False) -> list:
    """
    Returns the `limit` closest shelters to the given coordinates, each annotated
    with distance_km, sorted nearest-first.
    """
    shelters = get_shelters_from_sheet()
    if not shelters:
        return []

    results = []
    for s in shelters:
        if exclude_full and s.get("Status") == "เต็ม":
            continue
        dist = calculate_distance(user_lat, user_lon, s["Latitude"], s["Longitude"])
        entry = dict(s)
        entry["distance_km"] = round(dist, 2)
        results.append(entry)

    results.sort(key=lambda x: x["distance_km"])
    return results[:limit]


# =============================================================================
# SECTION 10: WEATHER & FLOOD DATA (With Real-time Direct ThaiWater Connection)
# =============================================================================

WEATHER_CONDITION_MAP = {
    1: "แจ่มใส", 2: "เมฆบางส่วน", 3: "เมฆมาก", 4: "ครึ้ม",
    5: "ฝนเล็กน้อย", 6: "ฝนปานกลาง", 7: "ฝนหนัก",
    8: "ฝนฟ้าคะนอง", 9: "หนาวจัด", 10: "หนาว",
    11: "เย็น", 12: "ร้อนจัด"
}

# WMO weather codes used by the Open-Meteo fallback (api.open-meteo.com) —
# keyless and globally available, so weather keeps working even if the TMD
# token is missing/expired or TMD's quota/service is temporarily down.
OPEN_METEO_CONDITION_MAP = {
    0: "แจ่มใส", 1: "แจ่มใสเป็นส่วนใหญ่", 2: "เมฆบางส่วน", 3: "เมฆมาก",
    45: "หมอก", 48: "หมอกน้ำแข็ง",
    51: "ฝนละอองเล็กน้อย", 53: "ฝนละอองปานกลาง", 55: "ฝนละอองหนัก",
    56: "ฝนละอองเยือกแข็งเล็กน้อย", 57: "ฝนละอองเยือกแข็งหนัก",
    61: "ฝนเล็กน้อย", 63: "ฝนปานกลาง", 65: "ฝนหนัก",
    66: "ฝนเยือกแข็งเล็กน้อย", 67: "ฝนเยือกแข็งหนัก",
    71: "หิมะเล็กน้อย", 73: "หิมะปานกลาง", 75: "หิมะหนัก", 77: "เกล็ดหิมะ",
    80: "ฝนซู่เล็กน้อย", 81: "ฝนซู่ปานกลาง", 82: "ฝนซู่รุนแรง",
    85: "หิมะซู่เล็กน้อย", 86: "หิมะซู่หนัก",
    95: "ฝนฟ้าคะนอง", 96: "ฝนฟ้าคะนองมีลูกเห็บเล็กน้อย", 99: "ฝนฟ้าคะนองมีลูกเห็บหนัก",
}

TMD_SOURCE_URL = "https://www.tmd.go.th"


def _get_weather_from_tmd(lat: float, lon: float) -> dict:
    """Primary source: Thai Meteorological Department (requires TMD_ACCESS_TOKEN)."""
    if not TMD_ACCESS_TOKEN or not requests:
        return {"ok": False, "error": "ไม่ได้ตั้งค่า TMD_ACCESS_TOKEN", "source": "TMD"}

    try:
        url = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at"
        params = {"lat": lat, "lon": lon, "duration": 1, "fields": "tc,rh,cond,ws10m"}
        headers = {"accept": "application/json", "authorization": f"Bearer {TMD_ACCESS_TOKEN}"}

        resp = requests.get(url, headers=headers, params=params, timeout=8)

        if resp.status_code == 401 or resp.status_code == 403:
            Logger.error("Weather", f"TMD auth error {resp.status_code}: {resp.text[:300]}")
            return {"ok": False, "error": "TMD_ACCESS_TOKEN ไม่ถูกต้องหรือหมดอายุ", "source": "TMD"}
        if resp.status_code == 429:
            Logger.error("Weather", f"TMD rate-limited: {resp.text[:300]}")
            return {"ok": False, "error": "ระบบ TMD หนาแน่น กรุณาลองใหม่ในอีก 1 นาที", "source": "TMD"}

        resp.raise_for_status()
        data = resp.json()

        forecasts = data.get("WeatherForecasts", [])
        if not forecasts:
            Logger.error("Weather", f"TMD returned no forecasts for {lat},{lon}: {str(data)[:300]}")
            return {"ok": False, "error": "ไม่พบข้อมูลพยากรณ์สำหรับพิกัดนี้จาก TMD", "source": "TMD"}

        latest = forecasts[0].get("forecasts", [])[0]
        d = latest.get("data", {})
        code = d.get("cond", 0)

        return {
            "ok": True,
            "temp": d.get("tc", "-"),
            "rh": d.get("rh", "-"),
            "wind": d.get("ws10m", "-"),
            "desc": WEATHER_CONDITION_MAP.get(code, "ไม่ระบุ"),
            "source": "TMD",
            "error": None,
        }
    except Exception as e:
        Logger.error("Weather", f"TMD API exception: {e}")
        return {"ok": False, "error": f"TMD API error: {e}", "source": "TMD"}


def _get_weather_from_open_meteo(lat: float, lon: float) -> dict:
    """Fallback source: Open-Meteo (no API key, no quota) — used automatically
    whenever TMD is unavailable, so the weather feature never goes fully dark."""
    if not requests:
        return {"ok": False, "error": "requests library not available", "source": "Open-Meteo"}
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            "timezone": "Asia/Bangkok",
        }
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {})
        if not current:
            Logger.error("Weather", f"Open-Meteo returned no current data for {lat},{lon}: {str(data)[:300]}")
            return {"ok": False, "error": "ไม่พบข้อมูลอากาศสำหรับพิกัดนี้", "source": "Open-Meteo"}

        code = current.get("weather_code", 0)
        return {
            "ok": True,
            "temp": current.get("temperature_2m", "-"),
            "rh": current.get("relative_humidity_2m", "-"),
            "wind": current.get("wind_speed_10m", "-"),
            "desc": OPEN_METEO_CONDITION_MAP.get(code, "ไม่ระบุ"),
            "source": "Open-Meteo",
            "error": None,
        }
    except Exception as e:
        Logger.error("Weather", f"Open-Meteo API exception: {e}")
        return {"ok": False, "error": f"Open-Meteo API error: {e}", "source": "Open-Meteo"}


def get_live_weather_data(lat: float, lon: float) -> dict:
    """
    Returns current weather for (lat, lon). Tries TMD first (official Thai
    source); if that fails for ANY reason (missing/expired token, TMD quota,
    TMD outage, no data for this point), automatically falls back to
    Open-Meteo (keyless, globally reliable) instead of showing an error —
    so 'เช็คสภาพอากาศ' keeps working even when TMD alone would not.
    """
    start = time.time()
    cache_key = f"{round(float(lat), 2)},{round(float(lon), 2)}"

    cached = cache.weather.get(cache_key)
    if cached:
        Logger.perf("Weather", "cache_hit", (time.time() - start) * 1000)
        return cached

    result = _get_weather_from_tmd(lat, lon)

    if not result.get("ok"):
        Logger.info("Weather", f"TMD unavailable ({result.get('error')}) — falling back to Open-Meteo")
        fallback = _get_weather_from_open_meteo(lat, lon)
        if fallback.get("ok"):
            result = fallback
        else:
            # Both sources failed — surface the TMD error (primary source) but
            # log both so the real cause is easy to find in the server logs.
            Logger.error("Weather", f"Both TMD and Open-Meteo failed. TMD={result.get('error')} | Open-Meteo={fallback.get('error')}")

    cache.weather.set(cache_key, result)
    Logger.perf("Weather", "api_call", (time.time() - start) * 1000)
    return result


def get_live_weather_scraper(lat: float, lon: float) -> str:
    d = get_live_weather_data(lat, lon)
    if not d.get("ok"):
        return f"⚠️ {d.get('error', 'ไม่สามารถดึงข้อมูลอากาศได้ในขณะนี้')}\nกรุณาตรวจสอบจากแอปพยากรณ์อากาศโดยตรง"
    return f"🌡️ {d['temp']} °C | 🌧️ {d['desc']}\n💧 ชื้น {d['rh']}% | 🍃 ลม {d['wind']} m/s"


_last_water_refresh_ts = 0.0


def is_water_data_stale() -> bool:
    """
    True if the background refresh job (water_level_refresh_loop, runs every
    10 min) hasn't successfully updated the 'Water_Levels' sheet within
    WATER_DATA_MAX_AGE_MINUTES — e.g. ThaiWater's API has been down or sheet
    writes have been silently failing for a while. Used to fall back to a
    direct live API call instead of serving old data with no indication
    it's stale.
    """
    if _last_water_refresh_ts == 0:
        return False  # haven't refreshed even once yet this run — let the "sheet empty" fallback handle it
    age_minutes = (time.time() - _last_water_refresh_ts) / 60
    return age_minutes > WATER_DATA_MAX_AGE_MINUTES


def get_live_water_levels_from_api() -> list:
    """
    Directly pulls real-time water levels from ThaiWater V3 API.
    Updated to match V3 Schema and official situation mapping.
    """
    start_time = time.time()
    cache_key = "thaiwater:water_levels_live"
    cached = cache.water.get(cache_key)
    if cached:
        Logger.perf("WaterLevelAPI", "cache_hit", (time.time() - start_time) * 1000)
        return cached

    if not requests:
        Logger.error("WaterLevelAPI", "Requests library is not installed.")
        return []

    # Mapping based on situation_level (V3 Standard)
    STATUS_MAP = {
        1: "น้อยวิกฤต",
        2: "น้อย",
        3: "ปกติ",
        4: "มาก",
        5: "ล้นตลิ่ง"
    }

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
        resp = requests.get(THAIWATER_V3_API, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        # V3 data structure: data["waterlevel_data"]["data"]
        raw_stations = data.get("waterlevel_data", {}).get("data", [])
        parsed_stations = []
        
        for item in raw_stations:
            station = item.get("station", {})
            geocode = item.get("geocode", {})
            
            lat_val = station.get("tele_station_lat")
            lon_val = station.get("tele_station_long")
            if lat_val is None or lon_val is None:
                continue
                
            # Use waterlevel_msl as primary, fallback to waterlevel_m
            wl_val = item.get("waterlevel_msl") or item.get("waterlevel_m")
            # Use min_bank as primary for bank level
            bl_val = station.get("min_bank") or station.get("left_bank") or "-"
                
            # Get situation from situation_level mapping
            sit_level = item.get("situation_level")
            situation = STATUS_MAP.get(sit_level, "ปกติ")
            
            trend = item.get("water_trend", {}).get("name", "คงที่") if isinstance(item.get("water_trend"), dict) else "คงที่"
            measure_time = item.get("waterlevel_datetime", "-")
            
            parsed_stations.append({
                "StationCode": station.get("tele_station_oldcode") or str(station.get("id", "")),
                "Name": station.get("tele_station_name", {}).get("th", "ไม่ระบุ"),
                "River": item.get("river_name", "ไม่ระบุ"),
                "Location": geocode.get("province_name", {}).get("th", ""),
                "Lat": float(lat_val),
                "Lon": float(lon_val),
                "WaterLevel": wl_val if wl_val is not None else "-",
                "BankLevel": bl_val,
                "Situation": situation,
                "Trend": trend,
                "Time": measure_time
            })
            
        cache.water.set(cache_key, parsed_stations, ttl=900)
        Logger.perf("WaterLevelAPI", "fetched_live", (time.time() - start_time) * 1000, {"count": len(parsed_stations)})
        return parsed_stations
    except Exception as e:
        Logger.error("WaterLevelAPI", f"Failed to pull live water level telemetry from API: {e}")
        return []


def assess_water_level_status(wl_value, bl_value=None, situation=None, lang="TH"):
    """
    Assess water level status using Thaiwater official tags and specified HEX codes.
    - 🟧 น้อยวิกฤต: #D67B27
    - 🟨 น้อย: #FFC000
    - 🟩 ปกติ: #00B050
    - 🟦 มาก: #0000FF
    - 🟥 ล้นตลิ่ง: #FF0000
    """
    status_key = str(situation or "ปกติ").strip()

    status_map = {
        "น้อยวิกฤต": {
            "status": "น้อยวิกฤต",
            "bg": "#FFF7ED",
            "text": "#C2410C",
            "advice": "เฝ้าระวังภัยแล้ง/น้ำลดขีดอันตราย",
            "label_pill": "น้อยวิกฤต"
        },
        "น้อย": {
            "status": "น้อย",
            "bg": "#FEFCE8",
            "text": "#A16207",
            "advice": "ระดับน้ำน้อย",
            "label_pill": "น้อย"
        },
        "ปกติ": {
            "status": "ปกติ",
            "bg": "#F0FDF4",
            "text": "#15803D",
            "advice": "ระดับน้ำปกติ ปลอดภัยดี",
            "label_pill": "ปกติ"
        },
        "มาก": {
            "status": "มาก",
            "bg": "#EFF6FF",
            "text": "#1D4ED8",
            "advice": "ระดับน้ำค่อนข้างสูง",
            "label_pill": "มาก"
        },
        "ล้นตลิ่ง": {
            "status": "ล้นตลิ่ง",
            "bg": "#FEF2F2",
            "text": "#B91C1C",
            "advice": "ระดับน้ำล้นตลิ่ง วิกฤติ",
            "label_pill": "ล้นตลิ่ง"
        },
    }

    res = status_map.get(status_key, status_map["ปกติ"]).copy()
    
    try:
        wl = float(wl_value) if wl_value not in [None, "-", ""] else 0
        bl = float(bl_value) if bl_value not in [None, "-", ""] else 0
        res["diff"] = bl - wl
        res["diff_text"] = f"{abs(bl - wl):.2f}"
    except (ValueError, TypeError):
        res["diff"] = 0
        res["diff_text"] = "-"
        
    return res


# =============================================================================
# SECTION 11: LINE BOT INITIALIZATION
# =============================================================================

line_bot_api = None
handler = None

if LINE_CHANNEL_ACCESS_TOKEN and LineBotApi:
    line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
if LINE_CHANNEL_SECRET and WebhookHandler:
    handler = WebhookHandler(LINE_CHANNEL_SECRET)


# =============================================================================
# WATER ALERT ENGINE
# =============================================================================
# Proactive LINE push when a station near an opted-in, located user crosses
# into a risky situation (มาก / ล้นตลิ่ง). Called from water_level_refresh_loop
# right after Water_Levels is overwritten with a fresh ThaiWater pull, reusing
# that same `stations` list — this file never calls ThaiWater a second time
# just for alerting (see spec §5.3).

_WATER_ALERT_RISK_SITUATIONS = {"มาก", "ล้นตลิ่ง"}
# Situations worth telling someone about even when it's not a fresh risk —
# recovering out of a risky state is genuinely useful to know, per spec §5.4's
# "should support" cases. Anything not in either set (e.g. น้อย, ปกติ<-น้อย)
# is a normal/quiet transition and never generates a push.
_WATER_ALERT_TRACKED_TRANSITIONS = {
    ("ปกติ", "มาก"): "risk_up",
    ("น้อย", "มาก"): "risk_up",
    ("น้อยวิกฤต", "มาก"): "risk_up",
    ("มาก", "ล้นตลิ่ง"): "risk_up_critical",
    ("ล้นตลิ่ง", "มาก"): "recovering",
    ("มาก", "ปกติ"): "recovered",
    ("มาก", "น้อย"): "recovered",
}


def _build_water_alert_text(kind: str, station: dict, distance_km: float) -> str:
    """
    Message copy per spec §12. Deliberately never claims flooding has
    reached the user's own location — only reports the station's reading
    and distance, and always points them to official channels for anything
    beyond that (spec §12's explicit prohibition).
    """
    name = station.get("Name", "ไม่ระบุ")
    area = station.get("Location", "-") or "-"
    wl = station.get("WaterLevel", "-")
    measure_time = station.get("Time", "-")

    if kind == "risk_up":
        return (
            f"⚠️ แจ้งเตือนระดับน้ำสูง\n\n"
            f"สถานี: {name}\nพื้นที่: {area}\nระดับน้ำ: {wl}\nสถานะ: มาก\n"
            f"ระยะห่างจากคุณ: {distance_km:.1f} กม.\nเวลาอัปเดต: {measure_time}\n\n"
            f"ระดับน้ำที่สถานีอยู่ในระดับสูง โปรดติดตามสถานการณ์อย่างใกล้ชิดครับ"
        )
    if kind == "risk_up_critical":
        return (
            f"🚨 แจ้งเตือนระดับน้ำวิกฤต\n\n"
            f"สถานี: {name}\nพื้นที่: {area}\nระดับน้ำ: {wl}\nสถานะ: ล้นตลิ่ง\n"
            f"ระยะห่างจากคุณ: {distance_km:.1f} กม.\nเวลาอัปเดต: {measure_time}\n\n"
            f"โปรดติดตามประกาศและคำแนะนำจากหน่วยงานในพื้นที่อย่างใกล้ชิด "
            f"และเตรียมพร้อมปฏิบัติตามคำแนะนำด้านความปลอดภัยครับ"
        )
    if kind == "recovering":
        return (
            f"ℹ️ อัปเดตสถานการณ์น้ำ\n\n"
            f"สถานี: {name}\nพื้นที่: {area}\nระดับน้ำ: {wl}\nสถานะ: มาก (ลดลงจากล้นตลิ่ง)\n"
            f"ระยะห่างจากคุณ: {distance_km:.1f} กม.\nเวลาอัปเดต: {measure_time}\n\n"
            f"ระดับน้ำลดลงจากระดับล้นตลิ่งแล้ว แต่ยังอยู่ในระดับสูง โปรดเฝ้าระวังต่อเนื่องครับ"
        )
    if kind == "recovered":
        return (
            f"✅ อัปเดตสถานการณ์น้ำ\n\n"
            f"สถานี: {name}\nพื้นที่: {area}\nระดับน้ำ: {wl}\nสถานะ: กลับสู่ปกติ\n"
            f"ระยะห่างจากคุณ: {distance_km:.1f} กม.\nเวลาอัปเดต: {measure_time}\n\n"
            f"ระดับน้ำที่สถานีนี้กลับสู่ภาวะปกติแล้วครับ"
        )
    return ""


def run_water_alert_engine(stations: list) -> dict:
    """
    Entry point called once per background refresh cycle with the stations
    list that refresh cycle just fetched (no second ThaiWater call — spec
    §5.3). For every opted-in, located, ACTIVE user within their alert
    radius of a station whose situation changed in a way worth telling them
    about, sends one LINE push and records the transition in
    Water_Alert_State so a repeat situation (e.g. มาก -> มาก) never re-sends,
    and the record survives an app restart.

    Returns {"sent": n, "skipped": n, "failed": n} for logging/reporting —
    this function never raises; a single bad user/station row is caught and
    skipped so it can't take down the whole cycle (spec §5.7, acceptance
    criteria #14).
    """
    result = {"sent": 0, "skipped": 0, "failed": 0}
    if not WATER_ALERT_ENABLED:
        return result
    if not line_bot_api:
        Logger.error("WaterAlert", "line_bot_api not configured — skipping alert cycle")
        return result

    risky_or_recovering_stations = [
        s for s in (stations or [])
        if s.get("Situation") in _WATER_ALERT_RISK_SITUATIONS or True
        # (kept simple: we still need last-known state for stations that
        # *used* to be risky to detect recovery, so we don't pre-filter by
        # current situation alone — the transition table below does that.)
    ]
    if not risky_or_recovering_stations:
        return result

    try:
        users = sheets_mgr.get_all_records("users")
    except Exception as e:
        Logger.error("WaterAlert", f"Failed to load users: {e}")
        return result

    located_users = []
    for u in users:
        try:
            if str(u.get("status", "ACTIVE")).strip().upper() not in ("", "ACTIVE"):
                continue
            if str(u.get("water_alert_enabled", "TRUE")).strip().upper() in ("FALSE", "0", "NO"):
                continue
            lat = float(u.get("gps_lat", 0) or 0)
            lon = float(u.get("gps_lon", 0) or 0)
            if lat == 0 and lon == 0:
                continue  # spec §7: no valid GPS -> never enters proximity alerting
            radius = float(u.get("water_alert_radius_km", 0) or 0) or WATER_ALERT_RADIUS_KM
            located_users.append({"user_id": str(u.get("user_id", "")), "lat": lat, "lon": lon, "radius": radius})
        except (ValueError, TypeError):
            result["skipped"] += 1
            continue

    if not located_users:
        return result

    try:
        state_map = sheets_mgr.get_water_alert_state_map()
    except Exception as e:
        Logger.error("WaterAlert", f"Failed to load Water_Alert_State: {e}")
        return result

    cooldown_seconds = WATER_ALERT_COOLDOWN_MINUTES * 60
    now = get_bangkok_time()

    for station in risky_or_recovering_stations:
        try:
            st_code = str(station.get("StationCode", "")).strip()
            st_lat = float(station.get("Lat", 0) or 0)
            st_lon = float(station.get("Lon", 0) or 0)
            if not st_code or (st_lat == 0 and st_lon == 0):
                continue  # spec §7 / test case 9: station with no GPS is skipped, not errored
            current_situation = str(station.get("Situation", "ปกติ")).strip()
            measure_time = str(station.get("Time", "-"))
        except (ValueError, TypeError):
            result["skipped"] += 1
            continue

        for u in located_users:
            try:
                distance = calculate_distance(u["lat"], u["lon"], st_lat, st_lon)
                if distance > u["radius"]:
                    continue

                key = (u["user_id"], st_code)
                prior = state_map.get(key)
                last_situation = str((prior or {}).get("last_situation", "")).strip()

                if not prior:
                    # First time this engine has ever seen this pair — just
                    # establish the baseline. Don't alert on it: otherwise
                    # every user within radius of an already-risky station
                    # gets paged the moment they register, which isn't a
                    # real state *change*.
                    sheets_mgr.upsert_water_alert_state(u["user_id"], st_code, current_situation, False, measure_time)
                    continue

                if last_situation == current_situation:
                    result["skipped"] += 1
                    continue  # e.g. มาก -> มาก / ล้นตลิ่ง -> ล้นตลิ่ง: no repeat alert

                transition_kind = _WATER_ALERT_TRACKED_TRANSITIONS.get((last_situation, current_situation))
                if not transition_kind:
                    # Situation changed but not in a way worth a push (e.g.
                    # ปกติ -> น้อย). Still record the new baseline so the
                    # *next* change is compared against the right value.
                    sheets_mgr.upsert_water_alert_state(u["user_id"], st_code, current_situation, False, measure_time)
                    continue

                last_alert_at = str((prior or {}).get("last_alert_at", "")).strip()
                if last_alert_at:
                    try:
                        last_alert_dt = datetime.datetime.strptime(last_alert_at, "%Y-%m-%d %H:%M:%S")
                        if (now.replace(tzinfo=None) - last_alert_dt).total_seconds() < cooldown_seconds:
                            result["skipped"] += 1
                            continue  # still cooling down from the last push to this user for this station
                    except ValueError:
                        pass

                text = _build_water_alert_text(transition_kind, station, distance)
                if not text:
                    continue

                try:
                    line_bot_api.push_message(u["user_id"], TextSendMessage(text=text))
                    sheets_mgr.upsert_water_alert_state(u["user_id"], st_code, current_situation, True, measure_time)
                    result["sent"] += 1
                except Exception as e:
                    # One user's push failing (blocked bot, invalid token, etc.)
                    # must never stop the rest of the batch — spec §5.7 / acceptance #14.
                    Logger.error("WaterAlert", f"Push failed for {u['user_id']} / {st_code}: {e}")
                    result["failed"] += 1
            except Exception as e:
                Logger.error("WaterAlert", f"Pair evaluation error ({u.get('user_id')}, {station.get('StationCode')}): {e}")
                result["failed"] += 1
                continue

    Logger.info("WaterAlert", f"Cycle done: sent={result['sent']} skipped={result['skipped']} failed={result['failed']}")
    return result


def show_loading_animation(user_id: str, loading_seconds: int = 30) -> bool:
    """
    Show LINE typing indicator (dots) for a specified duration.
    """
    if not LINE_CHANNEL_ACCESS_TOKEN or not requests:
        return False
    try:
        url = "https://api.line.me/v2/bot/chat/loading/start"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
        }
        payload = {"chatId": user_id, "loadingSeconds": max(5, min(loading_seconds, 60))}
        resp = requests.post(url, headers=headers, json=payload, timeout=5)
        return resp.status_code == 202
    except Exception:
        return False


# =============================================================================
# SECTION 12: FLEX MESSAGE BUILDERS (Exact match for specified features)
# =============================================================================

def build_register_form_flex(user_name="คุณ", lang="TH"):
    liff_url = REGISTER_LIFF_URL or "https://liff.line.me/"
    return FlexSendMessage(
        alt_text="📝 ลงทะเบียนข้อมูลของคุณ",
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#2F6F8F")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="📝 ลงทะเบียนข้อมูลของคุณ", weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=f"สวัสดีครับ คุณ{user_name}", size="sm", color="#374151"),
                    TextComponent(text="กรุณาลงทะเบียนข้อมูลที่อยู่และเบอร์ติดต่อ เพื่อรับการแจ้งเตือนและการช่วยเหลือฉุกเฉินล่วงหน้า", size="xs", color="#6B7280", wrap=True),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(
                        action=URIAction(label="📋 ลงทะเบียนข้อมูลของคุณ", uri=liff_url),
                        style="primary", color="#2F6F8F", height="lg"
                    )
                ]
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="ใช้เวลาไม่ถึง 1 นาที ข้อมูลของคุณจะได้รับความปลอดภัยสูงสุด", size="xs", color="#9CA3AF", align="center", wrap=True)]
            )
        )
    )


def build_snake_bite_flex(lang="TH"):
    steps = [
        "1. ตั้งสติ อยู่ให้นิ่งที่สุด การเคลื่อนไหวจะทำให้พิษกระจายเร็วขึ้น",
        "2. ถอดแหวน นาฬิกา หรือของรัดแน่นบริเวณที่ถูกกัดออกก่อนที่จะบวม",
        "3. ล้างแผลด้วยน้ำสะอาด ห้ามกรีด ดูด หรือใช้ปากดูดพิษออกเด็ดขาด",
        "4. ห้ามขันชะเนาะ (ห้ามรัดแน่นจนเลือดไม่ไหล) ให้ใช้ผ้าพันแผลแบบหลวมๆแทน",
        "5. พยายามจดจำลักษณะงู (สี ลาย ขนาด) ถ้าปลอดภัยและทำได้ เพื่อแจ้งแพทย์",
        "6. รีบนำส่งโรงพยาบาลที่ใกล้ที่สุดทันที หรือโทร 1669 ให้มารับ",
    ]
    body_contents = [
        TextComponent(text="🐍 ถูกงูกัด — ทำตามนี้ทันที", weight="bold", size="lg", color="#C2452F"),
        SeparatorComponent(margin="md"),
    ]
    for s in steps:
        body_contents.append(TextComponent(text=s, size="sm", color="#374151", wrap=True, margin="md"))

    body_contents.append(SeparatorComponent(margin="lg"))
    body_contents.append(
        TextComponent(
            text=f"☎️ ปรึกษาผู้เชี่ยวชาญตลอด 24 ชม.: สายด่วนศูนย์พิษวิทยารามาธิบดี {SNAKE_BITE_HOTLINE}",
            size="xs", color="#6B7280", wrap=True, margin="md"
        )
    )

    return FlexSendMessage(
        alt_text="🐍 วิธีปฐมพยาบาลเมื่อถูกงูกัด",
        contents=BubbleContainer(
            body=BoxComponent(layout="vertical", contents=body_contents),
            footer=BoxComponent(
                layout="vertical",
                spacing="sm",
                contents=[
                    ButtonComponent(
                        action=URIAction(label=f"📞 โทร {SNAKE_BITE_HOTLINE} ศูนย์พิษวิทยา", uri=f"tel:{SNAKE_BITE_HOTLINE}"),
                        style="primary", color="#C2452F", height="sm"
                    ),
                    ButtonComponent(
                        action=URIAction(label="📖 ข้อมูลเพิ่มเติม (รามาธิบดี)", uri=SNAKE_BITE_INFO_URL),
                        style="secondary", color="#F3F4F6", height="sm"
                    ),
                ]
            )
        )
    )


def build_prep_guide_flex(member_count: int = 1, lang="TH"):
    """
    'วิธีเตรียมตัวก่อนน้ำท่วม' checklist card.
    Quantities (drinking water, etc.) are personalized using the user's
    registered household member_count — real data from the system, not a
    generic fixed number.
    """
    try:
        member_count = max(1, int(member_count))
    except (TypeError, ValueError):
        member_count = 1

    water_liters = member_count * 3

    checklist = [
        ("💧", "น้ำดื่มสะอาด", f"อย่างน้อย {water_liters} ลิตร (สำหรับ {member_count} คน)"),
        ("🥫", "อาหารแห้ง", "เก็บได้นาน ทานง่าย"),
        ("💊", "ยาสามัญประจำบ้าน", "และยาประจำตัว"),
        ("🔦", "ไฟฉาย / แบตเตอรี่สำรอง", "พร้อมใช้งานเสมอ"),
        ("📄", "เอกสารสำคัญ", "ใส่ซองกันน้ำ"),
        ("🔋", "โทรศัพท์ / Power Bank", "ชาร์จให้เต็มอยู่เสมอ"),
    ]

    body_contents = [
        TextComponent(text="🎒 วิธีเตรียมตัวก่อนน้ำท่วม", weight="bold", size="lg", color="#1F2937"),
        TextComponent(
            text=f"เตรียมพร้อมไว้ ปลอดภัยกว่าแน่นอน · สำหรับสมาชิกในบ้าน {member_count} คน",
            size="xs", color="#9CA3AF", wrap=True
        ),
        SeparatorComponent(margin="md"),
    ]

    for icon, label, value in checklist:
        body_contents.append(
            BoxComponent(
                layout="horizontal", margin="md", spacing="sm",
                contents=[
                    TextComponent(text=f"✅ {icon} {label}", size="sm", color="#374151", flex=3, wrap=True),
                    TextComponent(text=value, size="xs", color="#6B7280", flex=2, align="end", wrap=True),
                ]
            )
        )

    body_contents.append(
        BoxComponent(
            layout="vertical",
            background_color="#FEF3C7",
            corner_radius="md",
            padding_all="md",
            margin="lg",
            contents=[
                TextComponent(
                    text="⚠️ หากมีคำสั่งอพยพ ให้ปฏิบัติตามทันที และออกจากพื้นที่โดยเร็ว",
                    size="xs", color="#92400E", wrap=True
                )
            ]
        )
    )

    hero = None
    hero_url = hero_image_url("prep_banner.jpg")
    if hero_url:
        hero = ImageComponent(
            url=hero_url,
            size="full",
            aspect_ratio="20:13",
            aspect_mode="cover",
        )

    return FlexSendMessage(
        alt_text="🎒 วิธีเตรียมตัวก่อนน้ำท่วม",
        contents=BubbleContainer(
            hero=hero,
            body=BoxComponent(layout="vertical", contents=body_contents),
            footer=BoxComponent(
                layout="vertical",
                spacing="sm",
                contents=[
                    ButtonComponent(
                        action=MessageAction(label="🏠 ศูนย์อพยพใกล้ฉัน", text="ศูนย์พักพิง"),
                        style="secondary", color="#F3F4F6", height="sm"
                    ),
                    ButtonComponent(
                        action=MessageAction(label="🆘 แจ้งเหตุ SOS", text="sos"),
                        style="primary", color="#DC2626", height="sm"
                    ),
                ]
            )
        )
    )


_CASE_STATUS_COLORS = {
    "รอดำเนินการ": {"bg": "#FEF3C7", "text": "#92400E"},
    "ทีมกำลังช่วยเหลือ": {"bg": "#DBEAFE", "text": "#1D4ED8"},
    "กำลังจัดเตรียม": {"bg": "#DBEAFE", "text": "#1D4ED8"},
    "ช่วยเหลือสำเร็จ": {"bg": "#A7F0D2", "text": "#047857"},
    "ส่งมอบแล้ว": {"bg": "#A7F0D2", "text": "#047857"},
}


def _case_avatar(kind: str):
    """Small initial-circle avatar — pastel background + accent-colored letter, matching the same muted pill palette used across every other Flex card in the app (water level / shelter / contact), instead of a solid saturated fill."""
    bg, letter_color, letter = ("#FBC7D4", "#B91C1C", "S") if kind == "sos" else ("#DBEAFE", "#1D4ED8", "N")
    return BoxComponent(
        layout="vertical", width="36px", height="36px", corner_radius="18px",
        background_color=bg, justify_content="center", align_items="center", flex=0,
        contents=[TextComponent(text=letter, size="sm", weight="bold", color=letter_color, align="center")]
    )


def _tag_chip(label: str):
    """Small neutral gray tag chip (e.g. category / priority / people-count tags)."""
    return BoxComponent(
        layout="vertical", flex=0, background_color="#F3F4F6", corner_radius="8px",
        padding_start="9px", padding_end="9px", padding_top="4px", padding_bottom="4px",
        contents=[TextComponent(text=label, size="xxs", color="#4B5563", weight="bold")]
    )


def _case_row(c: dict, base_url: str):
    sev = _CASE_STATUS_COLORS.get(c["status_label"], {"bg": "#EFEFF1", "text": "#374151"})
    track_url = f"{base_url}/liff/track?id={c['case_id']}"

    return BoxComponent(
        layout="vertical",
        background_color="#FFFFFF",
        corner_radius="16px",
        padding_all="lg",
        margin="md",
        action=URIAction(label="ดูรายละเอียด", uri=track_url),
        contents=[
            BoxComponent(
                layout="horizontal", spacing="sm",
                contents=[
                    _case_avatar(c["kind"]),
                    BoxComponent(
                        layout="vertical", flex=1, justify_content="center",
                        contents=[
                            TextComponent(text=c["case_id"], size="xs", weight="bold", color="#111827"),
                            TextComponent(text=c["date"], size="xxs", color="#9CA3AF", margin="xs"),
                        ]
                    ),
                    _pill_badge(c["status_label"], sev["bg"], sev["text"]),
                ]
            ),
            BoxComponent(
                layout="vertical", height="38px", justify_content="flex-start", margin="md",
                contents=[
                    TextComponent(text=c.get("summary") or "ไม่มีรายละเอียดเพิ่มเติม", size="sm",
                                  weight="bold", color="#111827", wrap=True, max_lines=2),
                ]
            ),
            BoxComponent(
                layout="horizontal", spacing="xs", margin="sm", wrap=True,
                contents=[_tag_chip(t) for t in c.get("tags", [])] or [TextComponent(text="", size="xxs")]
            ),
            _dashed_rule(),
            BoxComponent(
                layout="horizontal", margin="md", align_items="center",
                contents=[
                    BoxComponent(
                        layout="vertical", flex=1,
                        contents=[
                            TextComponent(text="สถานะล่าสุด", size="xxs", color="#9CA3AF"),
                            TextComponent(text=c["status_label"], size="sm", weight="bold", color="#111827", margin="xs"),
                        ]
                    ),
                    _pill_button("ดูรายละเอียด", URIAction(label="ดูรายละเอียด", uri=track_url)),
                ]
            ),
        ]
    )


def build_my_cases_flex_message(cases: list, base_url: str) -> FlexSendMessage:
    """
    Lists every SOS/Need case a user has filed as ONE vertically-stacked
    Flex bubble (not a horizontal swipe carousel) — each case rendered as
    its own card-like row, tappable straight through to its tracking page.
    SOS and Need cases are grouped into their own clearly-labeled sections
    since they carry different fields (priority/people-count vs categories).
    `cases` is a list of dicts: {case_id, kind ('sos'|'need'), status_label,
    date, summary, tags}, already sorted newest-first by the caller.
    """
    sos_all = [c for c in cases if c["kind"] == "sos"]
    need_all = [c for c in cases if c["kind"] == "need"]
    sos_cases = sos_all[:MAX_CASES_PER_SECTION]
    need_cases = need_all[:MAX_CASES_PER_SECTION]

    contents = [
        TextComponent(text="เคสของคุณ", weight="bold", size="lg", color="#111827"),
        TextComponent(text=f"ทั้งหมด {len(cases)} รายการ", size="xs", color="#9CA3AF", margin="xs"),
    ]

    if sos_cases:
        contents.append(TextComponent(text="แจ้งเหตุฉุกเฉิน", size="sm", weight="bold", color="#B91C1C", margin="xl"))
        for c in sos_cases:
            contents.append(_case_row(c, base_url))
        if len(sos_all) > MAX_CASES_PER_SECTION:
            contents.append(TextComponent(
                text=f"แสดง {MAX_CASES_PER_SECTION} รายการล่าสุด จากทั้งหมด {len(sos_all)} รายการ",
                size="xxs", color="#9CA3AF", margin="sm"
            ))

    if need_cases:
        contents.append(TextComponent(text="ขอความช่วยเหลือสิ่งของ", size="sm", weight="bold", color="#1D4ED8", margin="xl"))
        for c in need_cases:
            contents.append(_case_row(c, base_url))
        if len(need_all) > MAX_CASES_PER_SECTION:
            contents.append(TextComponent(
                text=f"แสดง {MAX_CASES_PER_SECTION} รายการล่าสุด จากทั้งหมด {len(need_all)} รายการ",
                size="xxs", color="#9CA3AF", margin="sm"
            ))

    bubble = BubbleContainer(
        size="giga",
        styles=BubbleStyle(body=BlockStyle(background_color="#F5F6F8")),
        body=BoxComponent(layout="vertical", padding_all="lg", contents=contents)
    )
    return FlexSendMessage(alt_text=f"เคสของคุณ ({len(cases)} รายการ)", contents=bubble)


def build_help_flex(lang="TH"):
    """
    Guide "cover card" — the entry point shown when someone taps the Rich
    Menu's manual button or types a help keyword. Deliberately a single,
    graphic-forward bubble (banner + big title + one clear button) rather
    than a dense list, mirroring the "tap here for the full guide" pattern
    the product team asked for: this card's only job is to invite the tap
    that opens build_full_guide_flex() below, which carries the real detail.
    """
    copy = {
        "TH": {
            "alt": "📖 คู่มือการใช้งาน FLOODCARE AI",
            "eyebrow": "คู่มือการใช้งาน",
            "title": "FLOODCARE AI",
            "desc": "ทุกฟีเจอร์ที่ช่วยคุณผ่านสถานการณ์น้ำท่วม อธิบายทีละขั้นตอน แจ้งเหตุฉุกเฉิน ขอความช่วยเหลือ หาศูนย์พักพิง และอีกมากมาย",
            "cta": "👉 กดเพื่อเปิดคู่มือฉบับเต็ม",
        },
        "EN": {
            "alt": "📖 FLOODCARE AI User Guide",
            "eyebrow": "USER GUIDE",
            "title": "FLOODCARE AI",
            "desc": "Every feature that helps you through a flood, explained step by step — emergency reports, requesting help, finding shelters, and more.",
            "cta": "👉 Tap to open the full guide",
        },
    }
    c = copy.get(lang, copy["TH"])

    hero = None
    hero_url = hero_image_url("guide_banner.jpg")
    if hero_url:
        hero = ImageComponent(url=hero_url, size="full", aspect_ratio="20:13", aspect_mode="cover")

    body_contents = [
        TextComponent(text=c["eyebrow"], size="xs", color="#F97316", weight="bold"),
        TextComponent(text=c["title"], weight="bold", size="xxl", color="#14181F", margin="xs"),
        TextComponent(text=c["desc"], size="sm", color="#6B7280", wrap=True, margin="md"),
    ]

    return FlexSendMessage(
        alt_text=c["alt"],
        contents=BubbleContainer(
            hero=hero,
            body=BoxComponent(layout="vertical", padding_all="20px", spacing="sm", contents=body_contents),
            footer=BoxComponent(
                layout="vertical", padding_all="20px", padding_top="0px",
                contents=[
                    ButtonComponent(
                        action=MessageAction(label=c["cta"], text="เปิดคู่มือ" if lang != "EN" else "open guide"),
                        style="primary", color="#F97316", height="md",
                    )
                ]
            ),
        )
    )


def build_full_guide_flex(lang="TH"):
    """
    The full manual, opened by tapping the cover card above. A Carousel
    instead of one long bubble — LINE's Flex bubbles don't scroll their own
    body content, so a topic-per-page carousel is the only layout that lets
    someone read a thorough guide without everything getting truncated.
    Each page ends with a real, working button (the exact keyword that
    already triggers that feature elsewhere in the bot), so "try it" is
    never a dead end.
    """
    TH = lang != "EN"

    def page(icon, title, subtitle, steps, btn_label, btn_text, accent="#F97316"):
        body = [
            BoxComponent(
                layout="horizontal", spacing="md", contents=[
                    BoxComponent(
                        layout="vertical", width="44px", height="44px", corner_radius="12px",
                        background_color=accent, justify_content="center", align_items="center",
                        contents=[TextComponent(text=icon, size="xl", align="center", gravity="center")],
                    ),
                    BoxComponent(
                        layout="vertical", justify_content="center", contents=[
                            TextComponent(text=title, weight="bold", size="lg", color="#14181F", wrap=True),
                            TextComponent(text=subtitle, size="xxs", color="#9CA3AF", wrap=True),
                        ]
                    ),
                ]
            ),
            SeparatorComponent(margin="lg"),
        ]
        for i, step in enumerate(steps, 1):
            body.append(
                BoxComponent(
                    layout="horizontal", margin="lg", spacing="sm", contents=[
                        BoxComponent(
                            layout="vertical", width="22px", height="22px", corner_radius="11px",
                            background_color="#F1F0EC", justify_content="center", align_items="center",
                            contents=[TextComponent(text=str(i), size="xs", weight="bold", color="#6B7280", align="center")],
                        ),
                        TextComponent(text=step, size="sm", color="#374151", wrap=True, flex=1),
                    ]
                )
            )
        return BubbleContainer(
            size="mega",
            # Fixed height (not auto) is what makes every card in the carousel
            # come out the same total size regardless of whether a page has
            # 3 or 4 steps — without this, shorter pages render a shorter
            # bubble and the button ends up sitting at a different vertical
            # position on each card instead of lining up across the row.
            body=BoxComponent(layout="vertical", padding_all="20px", height="440px", contents=body),
            footer=BoxComponent(
                layout="vertical", padding_all="20px", padding_top="0px", contents=[
                    ButtonComponent(action=MessageAction(label=btn_label, text=btn_text), style="primary", color=accent, height="sm")
                ]
            ) if btn_text else None,
        )

    if TH:
        pages = [
            page("🤖", "FLOODCARE AI คืออะไร", "ผู้ช่วยรับมือน้ำท่วมของคุณ ทำงานผ่าน LINE ตลอด 24 ชั่วโมง", [
                "แจ้งเหตุฉุกเฉินและขอความช่วยเหลือได้ทันที ไม่ต้องโทรหาใคร",
                "เช็คระดับน้ำ สภาพอากาศ และหาศูนย์พักพิงใกล้คุณ",
                "ถามอะไรก็ได้เกี่ยวกับสถานการณ์น้ำท่วม ระบบตอบด้วย AI พร้อมแหล่งอ้างอิง",
                "เลื่อนดูหน้าถัดไปเพื่อดูวิธีใช้ทีละฟีเจอร์",
            ], "เมนูหลัก", "เมนู", "#14181F"),
            page("🆘", "แจ้งเหตุฉุกเฉิน (SOS)", "ใช้เมื่อคุณหรือคนใกล้ตัวติดอยู่ในพื้นที่น้ำท่วมและต้องการความช่วยเหลือด่วน", [
                "พิมพ์ 'sos' หรือกดปุ่ม SOS บนเมนูด้านล่าง",
                "กรอกข้อมูล ชื่อ เบอร์โทร จำนวนคน และแชร์พิกัดที่อยู่ปัจจุบัน",
                "ระบุว่ามีผู้ป่วยติดเตียงหรือสัตว์เลี้ยงหรือไม่ เพื่อให้ทีมกู้ภัยเตรียมพร้อม",
                "ระบบจะส่งเคสให้ทีมอาสาสมัครทันที และคุณจะได้รับหมายเลขเคสไว้ติดตามสถานะ",
            ], "🆘 แจ้งเหตุ SOS", "sos", "#EF4444"),
            page("📦", "ขอความช่วยเหลือเรื่องสิ่งของ", "สำหรับของจำเป็น เช่น อาหาร น้ำดื่ม ยา ที่ไม่ใช่เหตุฉุกเฉินถึงชีวิต", [
                "พิมพ์ 'ขอของ' เพื่อเริ่มแจ้งความต้องการ",
                "เลือกประเภทสิ่งของที่ต้องการ ระบุรายละเอียดเพิ่มเติมได้",
                "แชร์พิกัดที่อยู่ เพื่อให้อาสาสมัครนำของไปส่งถูกจุด",
                "ติดตามสถานะคำขอได้ตลอดผ่านเมนู 'ติดตามเคส'",
            ], "📦 ขอความช่วยเหลือ", "ขอของ", "#F97316"),
            page("🏠", "ค้นหาศูนย์พักพิง", "หากต้องอพยพออกจากบ้าน ใช้ฟีเจอร์นี้เพื่อหาที่พักที่ใกล้และยังมีที่ว่าง", [
                "พิมพ์ 'ศูนย์พักพิง' แล้วแชร์พิกัดปัจจุบันของคุณ",
                "ระบบจะแสดงศูนย์ที่ใกล้ที่สุด พร้อมจำนวนที่ว่างและสิ่งอำนวยความสะดวก เช่น ไฟฟ้า ห้องน้ำ รับสัตว์เลี้ยง",
                "กดปุ่มนำทางในการ์ดเพื่อเปิด Google Maps ไปยังศูนย์นั้นได้ทันที",
            ], "🏠 ศูนย์พักพิงใกล้ฉัน", "ศูนย์พักพิง", "#22C55E"),
            page("📍", "ติดตามเคสของคุณ", "เช็คได้ตลอดเวลาว่าเคส SOS หรือคำขอสิ่งของที่แจ้งไปถึงไหนแล้ว", [
                "พิมพ์ 'ติดตามเคส' เพื่อดูรายการเคสทั้งหมดที่คุณเคยแจ้ง",
                "สถานะจะอัปเดตแบบเรียลไทม์ เช่น รอรับเคส / กำลังช่วยเหลือ / เสร็จสิ้น",
                "หากมีอาสาสมัครรับเคสของคุณแล้ว จะเห็นชื่อผู้รับผิดชอบในการ์ดด้วย",
            ], "📍 ติดตามเคส", "ติดตามเคส", "#3B82F6"),
            page("🌊", "แผนที่ระดับน้ำ", "ดูสถานการณ์ระดับน้ำทั่วประเทศแบบสาธารณะ พร้อมระบบแจ้งเตือนอัตโนมัติ", [
                "พิมพ์ 'เช็คระดับน้ำ' แล้วแชร์พิกัด เพื่อดูสถานีวัดน้ำใกล้คุณ",
                "หรือเปิดแผนที่สาธารณะแบบเต็มจอได้จากลิงก์ในเมนู",
                "เมื่อสมัครรับแจ้งเตือน ระบบจะส่งข้อความอัตโนมัติทันทีที่ระดับน้ำใกล้บ้านคุณเปลี่ยนแปลงมาก",
            ], "🌊 เช็คระดับน้ำ", "เช็คระดับน้ำ", "#0EA5E9"),
            page("💬", "ถาม-ตอบกับ AI", "ถามอะไรก็ได้ที่เกี่ยวกับสถานการณ์น้ำท่วม สภาพอากาศ หรือข่าวสารล่าสุด", [
                "พิมพ์คำถามเป็นประโยคปกติ เช่น 'น้ำท่วมหาดใหญ่ตอนนี้เป็นยังไง'",
                "ระบบค้นหาข้อมูลล่าสุดให้อัตโนมัติ พร้อมแนบแหล่งอ้างอิงทุกครั้ง",
                "พิมพ์ 'สภาพอากาศ' แล้วแชร์พิกัด เพื่อเช็คพยากรณ์อากาศเฉพาะจุดของคุณ",
            ], "🌦️ เช็คสภาพอากาศ", "สภาพอากาศ", "#8B5CF6"),
            page("🎒", "เตรียมตัว เบอร์ฉุกเฉิน และภาษา", "ฟีเจอร์เสริมที่ช่วยให้ใช้งานระบบได้เต็มที่ตามที่คุณต้องการ", [
                "พิมพ์ 'วิธีเตรียมตัว' เพื่อดูเช็คลิสต์เตรียมพร้อมก่อนน้ำท่วมมา",
                "พิมพ์ 'เบอร์โทร' เพื่อดูเบอร์ติดต่อหน่วยงานฉุกเฉินทั้งหมด",
                "พิมพ์ 'เปลี่ยนภาษา' เพื่อสลับใช้งานเป็นไทย / อังกฤษ / มลายู",
                "พิมพ์ 'ลงทะเบียน' เพื่อบันทึกข้อมูลของคุณไว้ล่วงหน้า ช่วยให้แจ้งเหตุฉุกเฉินได้เร็วขึ้น",
            ], "🌐 เปลี่ยนภาษา", "เปลี่ยนภาษา", "#EC4899"),
        ]
        alt = "📖 คู่มือการใช้งาน FLOODCARE AI ฉบับเต็ม"
    else:
        pages = [
            page("🤖", "What is FLOODCARE AI", "Your 24/7 flood-response assistant, right inside LINE", [
                "Report emergencies and request help instantly — no phone call needed.",
                "Check water levels, weather, and find nearby shelters.",
                "Ask anything about the flood situation — answered by AI with sources.",
                "Swipe to see how to use each feature.",
            ], "Main menu", "menu", "#14181F"),
            page("🆘", "Emergency Report (SOS)", "Use this if you or someone nearby is trapped and needs urgent help", [
                "Type 'sos' or tap the SOS button on the menu below.",
                "Fill in your name, phone, number of people, and share your current location.",
                "Note if anyone is bedridden or if you have pets, so responders can prepare.",
                "Your case is sent to volunteers immediately, and you'll get a case number to track it.",
            ], "🆘 Report SOS", "sos", "#EF4444"),
            page("📦", "Request Supplies", "For essentials like food, water, or medicine — not life-threatening emergencies", [
                "Type 'need supplies' to start your request.",
                "Choose the category of item you need, with extra detail if useful.",
                "Share your location so a volunteer can deliver to the right place.",
                "Track the status anytime from the 'track' menu.",
            ], "📦 Request supplies", "need supplies", "#F97316"),
            page("🏠", "Find a Shelter", "If you need to evacuate, use this to find the nearest shelter with space", [
                "Type 'shelter' and share your current location.",
                "You'll see the nearest shelters, with open capacity and facilities like power, toilets, or pet-friendly space.",
                "Tap the navigate button on any card to open Google Maps directions.",
            ], "🏠 Nearby shelters", "shelter", "#22C55E"),
            page("📍", "Track Your Cases", "Check anytime on the status of an SOS report or supply request you've sent", [
                "Type 'track' to see every case you've reported.",
                "Status updates in real time: open / in progress / completed.",
                "Once a volunteer takes your case, their name appears on the card.",
            ], "📍 Track my cases", "track", "#3B82F6"),
            page("🌊", "Water Level Map", "See flood conditions nationwide, plus automatic alerts near you", [
                "Type 'water level' and share your location to see nearby monitoring stations.",
                "Or open the full public map from the link in the menu.",
                "Once subscribed, you'll get an automatic message the moment water levels near you change significantly.",
            ], "🌊 Check water level", "water level", "#0EA5E9"),
            page("💬", "Ask the AI", "Ask anything about the flood situation, weather, or latest news", [
                "Type a normal question, e.g. 'what's the flood situation in Hat Yai right now'.",
                "The system searches for current information automatically, with sources attached.",
                "Type 'weather' and share your location for a forecast specific to you.",
            ], "🌦️ Check weather", "weather", "#8B5CF6"),
            page("🎒", "Prep, Hotlines & Language", "A few extra features to get the most out of the system", [
                "Type 'how to prepare' for a pre-flood readiness checklist.",
                "Type 'hotline' for a list of every emergency contact number.",
                "Type 'change language' to switch between Thai / English / Malay.",
                "Type 'register' to save your details in advance, so future SOS reports go even faster.",
            ], "🌐 Change language", "change language", "#EC4899"),
        ]
        alt = "📖 FLOODCARE AI — Full User Guide"

    return FlexSendMessage(alt_text=alt, contents=CarouselContainer(contents=pages))


def build_faq_response_flex(answer: str, sources: list, question: str, lang="TH"):
    body_contents = [
        TextComponent(
            text=f"คำถาม: {question[:60]}{'...' if len(question) > 60 else ''}",
            size="xs", color="#8C8980", wrap=True, margin="none"
        ),
        SeparatorComponent(margin="md"),
        TextComponent(
            text=answer,
            size="sm", color="#15151A", wrap=True, margin="md"
        ),
    ]

    footer_contents = []
    if sources:
        body_contents.append(SeparatorComponent(margin="lg"))
        body_contents.append(
            TextComponent(text="แหล่งข้อมูลอ้างอิง", size="xs", color="#8C8980", weight="bold", margin="md")
        )
        for src in sources[:3]:
            title = src.get("title", "") or src.get("url", "")
            url = src.get("url", "")
            label = (title[:30] + "...") if len(title) > 30 else title
            if url and label:
                footer_contents.append(
                    ButtonComponent(
                        action=URIAction(label=label, uri=url),
                        style="secondary", color="#F1EEE8", height="sm"
                    )
                )
    else:
        footer_contents.append(
            TextComponent(
                text="ข้อมูลจาก FLOODCARE AI (Gemini) — ตรวจสอบจากแหล่งข้อมูลทางการอีกครั้งเสมอ",
                size="xxs", color="#A6A199", wrap=True
            )
        )

    return FlexSendMessage(
        alt_text=f"ข้อมูล: {question[:40]}",
        contents=BubbleContainer(
            body=BoxComponent(layout="vertical", contents=body_contents),
            footer=BoxComponent(layout="vertical", spacing="sm", contents=footer_contents) if footer_contents else None,
        )
    )


def build_ai_response_flex(ai_text: str, original_question: str, lang="TH"):
    return FlexSendMessage(
        alt_text="🤖 FLOODCARE AI",
        contents=BubbleContainer(
            body=BoxComponent(
                layout="vertical",
                contents=[
                    BoxComponent(
                        layout="horizontal",
                        contents=[
                            TextComponent(text="🤖 FLOODCARE AI", weight="bold", size="sm", color="#1E40AF", flex=1),
                            TextComponent(text="AI", size="xxs", color="#9CA3AF", align="end")
                        ]
                    ),
                    SeparatorComponent(margin="md"),
                    TextComponent(text=ai_text, wrap=True, size="sm", color="#374151", margin="md")
                ]
            )
        )
    )


def build_language_selector_flex():
    """
    Honest language status card — the bot only actually operates in Thai
    (every Gemini prompt, Flex Message, and hardcoded string is Thai; full
    translation is a much larger effort than a toggle). Rather than faking
    a working language switch that silently does nothing, this shows Thai
    as fully supported and the rest as clearly labeled 'in development' so
    tapping them can't be mistaken for a real language change.
    """
    # Flag emoji as a leading icon instead of relying on the text label —
    # someone who can't read Thai (the whole point of switching language)
    # can still recognize their flag and tap the right row without being
    # able to read "ภาษาไทย" / "ใช้งานได้" first. The label stays alongside
    # for people who *can* read it, and the tap target is the whole row.
    def _lang_row(flag: str, label: str, status_label: str, bg: str, text_color: str, msg_text: str):
        return BoxComponent(
            layout="horizontal",
            margin="md",
            spacing="md",
            action=MessageAction(label=label, text=msg_text),
            align_items="center",
            contents=[
                TextComponent(text=flag, size="xxl", flex=0, gravity="center"),
                TextComponent(text=label, size="sm", weight="bold", color="#111827", flex=1, gravity="center"),
                _pill_badge(status_label, bg, text_color),
            ]
        )

    return FlexSendMessage(
        alt_text="🌐 ภาษา / Language",
        contents=BubbleContainer(
            size="kilo",
            body=BoxComponent(
                layout="vertical",
                padding_all="lg",
                spacing="sm",
                contents=[
                    TextComponent(text="🌐 ภาษา / Language", weight="bold", size="md", color="#111827"),
                    TextComponent(text="แตะธงเพื่อเลือกภาษา · Tap a flag to choose your language",
                                  size="xs", color="#9CA3AF", margin="xs", wrap=True),
                    _dashed_rule(),
                    _lang_row("🇹🇭", "ภาษาไทย", "ใช้งานได้", "#A7F0D2", "#047857", "ตั้งค่าภาษา: TH"),
                    _lang_row("🇲🇾", "Bahasa Melayu", "ใช้งานได้", "#A7F0D2", "#047857", "ตั้งค่าภาษา: MY"),
                    _lang_row("🇬🇧", "English", "ใช้งานได้", "#A7F0D2", "#047857", "ตั้งค่าภาษา: EN"),
                    _lang_row("🇯🇵", "日本語", "กำลังพัฒนา", "#FEF3C7", "#92400E", "ตั้งค่าภาษา: JP"),
                ]
            )
        )
    )


def _metric_row(icon_file: str, label: str, value: str):
    """Row with a small custom icon image (not an emoji) + label + value."""
    icon_url = hero_image_url(icon_file)
    icon_component = (
        ImageComponent(url=icon_url, size="22px", aspect_ratio="1:1", aspect_mode="cover", flex=0, gravity="center")
        if icon_url else
        TextComponent(text="•", size="sm", color="#9CA3AF", flex=0, gravity="center")
    )
    return BoxComponent(
        layout="horizontal", margin="md", spacing="md",
        padding_start="md", padding_end="md",
        contents=[
            icon_component,
            TextComponent(text=label, size="sm", color="#6B7280", flex=3, gravity="center"),
            TextComponent(text=value, size="sm", weight="bold", color="#111827", flex=3, align="end", gravity="center"),
        ]
    )


def build_weather_flex(lat, lon, weather_data: dict, timestamp: str, lang="TH"):
    if not weather_data.get("ok"):
        body_contents = [
            TextComponent(text="รายงานสภาพอากาศ", weight="bold", size="lg", color="#1F2937"),
            SeparatorComponent(margin="md"),
            TextComponent(
                text=weather_data.get('error', 'ไม่สามารถดึงข้อมูลอากาศได้ในขณะนี้'),
                size="sm", color="#C2452F", wrap=True, margin="md"
            ),
        ]
    else:
        temp = weather_data["temp"]
        desc = weather_data["desc"]
        rh = weather_data["rh"]
        wind = weather_data["wind"]

        rows = [
            ("icon_temp.jpg", "อุณหภูมิ", f"{temp} °C"),
            ("icon_condition.jpg", "สภาพอากาศ", desc),
            ("icon_humidity.jpg", "ความชื้น", f"{rh} %"),
            ("icon_wind.jpg", "ความเร็วลม", f"{wind} m/s"),
        ]
        body_contents = [
            TextComponent(text="รายงานสภาพอากาศ", weight="bold", size="lg", color="#1F2937"),
            _icon_text("icon_pin.jpg", f"พิกัด : {lat:.4f}, {lon:.4f}"),
            _icon_text("icon_clock.jpg", f"เวลา: {timestamp}"),
            SeparatorComponent(margin="md", color="#F3F4F6"),
        ]
        for icon_file, label, value in rows:
            body_contents.append(_metric_row(icon_file, label, value))
        body_contents.append(
            TextComponent(
                text="หมายเหตุ: ข้อมูลพยากรณ์เบื้องต้น โปรดพิจารณาสภาพอากาศจริงประกอบ",
                size="xs", color="#9CA3AF", wrap=True, margin="lg"
            )
        )

    hero = None
    hero_url = hero_image_url("weather_banner.jpg")
    if hero_url:
        hero = ImageComponent(
            url=hero_url,
            size="full",
            aspect_ratio="20:13",
            aspect_mode="cover",
        )

    return FlexSendMessage(
        alt_text="รายงานสภาพอากาศ",
        contents=BubbleContainer(
            hero=hero,
            body=BoxComponent(layout="vertical", contents=body_contents),
            footer=BoxComponent(
                layout="vertical",
                spacing="sm",
                padding_all="md",
                contents=[
                    ButtonComponent(
                        action=URIAction(label="ดูข้อมูลเพิ่มเติม (กรมอุตุนิยมวิทยา)", uri=TMD_SOURCE_URL),
                        style="secondary", color="#F9FAFB", height="sm"
                    ),
                    TextComponent(
                        text="ที่มา: กรมอุตุนิยมวิทยา",
                        size="xxs", color="#9CA3AF", align="center", wrap=True
                    )
                ]
            )
        )
    )


def _icon_text(icon_file: str, text: str, size="xs", color="#9CA3AF"):
    """Small inline icon image + caption text (replaces emoji in header meta rows)."""
    icon_url = hero_image_url(icon_file)
    icon_component = (
        ImageComponent(url=icon_url, size="13px", aspect_ratio="1:1", aspect_mode="cover", flex=0, gravity="center")
        if icon_url else
        TextComponent(text="•", size=size, color=color, flex=0, gravity="center")
    )
    return BoxComponent(
        layout="horizontal", spacing="xs",
        contents=[icon_component, TextComponent(text=text, size=size, color=color, wrap=True, gravity="center")]
    )


def _pill_badge(label: str, bg: str, text_color: str):
    """
    Small fully-rounded status chip (e.g. ปกติ / เฝ้าระวัง / เต็มแล้ว) — a real
    pill shape via a BoxComponent with a large corner_radius, since LINE's
    native ButtonComponent can't be shaped into a pill.
    flex=0 is required here: without it, LINE Flex gives this box an equal
    1:1 share of the row alongside the label next to it, which is what made
    the badge stretch to ~half the card width in testing.
    """
    return BoxComponent(
        layout="vertical",
        flex=0,
        background_color=bg,
        corner_radius="12px",
        padding_start="10px", padding_end="10px", padding_top="4px", padding_bottom="4px",
        contents=[TextComponent(text=label, size="xs", weight="bold", color=text_color, align="center")]
    )


def _pill_button(label: str, action, bg="#0F172A", text_color="#FFFFFF"):
    """Fully-rounded pill action button (BoxComponent + action, tappable). flex=0 for the same reason as _pill_badge."""
    return BoxComponent(
        layout="vertical",
        flex=0,
        background_color=bg,
        corner_radius="16px",
        padding_top="7px", padding_bottom="7px", padding_start="13px", padding_end="13px",
        action=action,
        gravity="center",
        contents=[TextComponent(text=label, size="xs", weight="bold", color=text_color, align="center")]
    )


def _dashed_rule():
    """
    Divider between the header info and the key numbers. Previously faked a
    dashed line with repeated '┈' characters, but that overflowed the card
    width and LINE rendered it as a truncated '...' in testing. A native
    SeparatorComponent is a solid line instead of dashed, but it's reliable
    and never truncates.
    """
    return SeparatorComponent(margin="md", color="#EDEFF2")


def _facility_mark(present: bool):
    """
    Small checkmark/dash for a shelter amenity (เตียง/ห้องน้ำ/ที่จอดรถ). The
    'Shelters' sheet currently only tracks whether an amenity exists, not a
    count, so this renders a simple ✓ / — instead of a fabricated number.
    """
    return TextComponent(
        text="✓" if present else "—",
        size="md", weight="bold",
        color="#15803D" if present else "#D1D5DB",
        margin="xs", align="center"
    )


def _has_facility(value) -> bool:
    """Interprets a shelter facility sheet cell (checkbox-style: TRUE/มี/1/etc.) as present or absent."""
    if value in (None, ""):
        return False
    return str(value).strip().lower() in ("true", "1", "y", "yes", "มี", "✓")


# The dashboard's "เพิ่มศูนย์พักพิงใหม่" form has 6 optional-amenity checkboxes
# beyond the 3 core ones (เตียง/ห้องน้ำ/ที่จอดรถ, which have their own Beds/
# Toilets/Parking columns). Those 6 are saved as a single comma-joined string
# in the 'Facilities' column (e.g. "ไฟฟ้า, น้ำสะอาด"). Keep this list in the
# exact same order/labels as the dashboard's `FACILITIES` JS array so a
# checkbox ticked in the dashboard always shows up ticked on the LINE card.
EXTRA_FACILITIES = ['ไฟฟ้า', 'น้ำสะอาด', 'อินเทอร์เน็ต', 'รองรับผู้พิการ', 'รับสัตว์เลี้ยง', 'มีแพทย์ประจำ']


def _has_extra_facility(shelter: dict, label: str) -> bool:
    """Checks whether `label` (e.g. 'ไฟฟ้า') is present in the shelter's comma-joined Facilities cell."""
    raw = str(shelter.get("Facilities", "") or "")
    return label in [part.strip() for part in raw.split(",")]


def build_contact_flex_message(contacts: list) -> FlexSendMessage:
    """
    All emergency hotlines in ONE pricing-card-style bubble: dark header with
    an urgency badge, the most important number shown as the big hero
    figure, then every contact listed underneath as a symmetric checklist
    row (checkmark + name + number, role as a smaller line beneath) — same
    row shape repeated for every contact so the list stays visually even no
    matter how many rows the 'Contacts' sheet grows to.
    `contacts` is a list of dicts with Name / Phone / Role keys.
    """
    primary = contacts[0] if contacts else {}
    primary_phone = str(primary.get("Phone", "")).strip()

    header = BoxComponent(
        layout="horizontal",
        padding_all="lg",
        background_color="#0F172A",
        contents=[
            TextComponent(text="เบอร์โทรฉุกเฉิน", weight="bold", size="md", color="#FFFFFF", flex=1),
            _pill_badge("ด่วน", "#DC2626", "#FFFFFF"),
        ]
    )

    hero = BoxComponent(
        layout="baseline",
        spacing="xs",
        contents=[
            TextComponent(text=primary_phone or "—", weight="bold", size="3xl", color="#111827"),
            TextComponent(text=f"/ {primary.get('Name', '')}", size="sm", color="#9CA3AF"),
        ]
    )

    rows = [hero, _dashed_rule()]
    for c in contacts:
        name = c.get("Name", "ไม่ระบุ")
        phone = str(c.get("Phone", "")).strip()
        role = c.get("Role", "")

        detail_contents = [
            BoxComponent(
                layout="horizontal",
                contents=[
                    TextComponent(text=name, size="sm", weight="bold", color="#111827", flex=1, wrap=True),
                    TextComponent(text=phone or "—", size="sm", weight="bold", color="#111827", flex=0, align="end"),
                ]
            )
        ]
        if role:
            detail_contents.append(TextComponent(text=role, size="xs", color="#9CA3AF", margin="xs", wrap=True))

        rows.append(
            BoxComponent(
                layout="horizontal", spacing="md", margin="lg",
                contents=[
                    TextComponent(text="✓", size="sm", weight="bold", color="#16A34A", flex=0, gravity="center"),
                    BoxComponent(layout="vertical", flex=1, contents=detail_contents),
                ]
            )
        )

    body = BoxComponent(layout="vertical", padding_all="lg", contents=rows)

    footer_contents = []
    if primary_phone:
        footer_contents.append(
            BoxComponent(
                layout="vertical",
                background_color="#0F172A",
                corner_radius="10px",
                padding_all="md",
                action=URIAction(label=f"โทร {primary_phone}", uri=f"tel:{primary_phone}"),
                contents=[TextComponent(text=f"โทรด่วน {primary_phone}", size="sm", weight="bold", color="#FFFFFF", align="center")]
            )
        )
    footer_contents.append(
        TextComponent(text="ข้อมูลจากฐานข้อมูลเบอร์โทรฉุกเฉิน FLOODCARE AI", size="xxs", color="#9CA3AF", align="center", margin="md", wrap=True)
    )
    footer = BoxComponent(layout="vertical", padding_all="lg", padding_top="none", contents=footer_contents)

    bubble = BubbleContainer(size="mega", header=header, body=body, footer=footer)
    return FlexSendMessage(alt_text="เบอร์โทรฉุกเฉิน", contents=bubble)


_WATER_SEVERITY_COLORS = {
    "น้อยวิกฤต": {"bg": "#FBD9B4", "text": "#9A5B12"},   # ส้มมินิมอล — น้ำแล้งวิกฤต (จากโทนทางการ #D67B27)
    "น้อย":      {"bg": "#FEF3C7", "text": "#92400E"},   # เหลืองมินิมอล — น้ำน้อย (จากโทนทางการ #FFC000)
    "ปกติ":      {"bg": "#A7F0D2", "text": "#047857"},   # เขียวมินิมอล — ปลอดภัย (จากโทนทางการ #00B050)
    "มาก":       {"bg": "#DBEAFE", "text": "#1D4ED8"},   # ฟ้ามินิมอล — เฝ้าระวัง (จากโทนทางการ #0000FF)
    "ล้นตลิ่ง":  {"bg": "#FBC7D4", "text": "#B91C1C"},   # แดงมินิมอล — วิกฤต (จากโทนทางการ #FF0000)
}

_SHELTER_SEVERITY_COLORS = {
    "เปิดรับ":  {"bg": "#CFF3E3", "text": "#047857"},
    "ใกล้เต็ม": {"bg": "#FDECC8", "text": "#92400E"},
    "เต็ม":     {"bg": "#FBD9DD", "text": "#B91C1C"},
    "ปิด":      {"bg": "#E5E7EB", "text": "#4B5563"},
}


def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, lang="TH"):
    """
    Ticket-card carousel — one swipeable card per station. Header color maps
    to the real severity of that station (green=ปกติ, blue=น้อย, orange=น้อยวิกฤต,
    yellow=มาก, pink=ล้นตลิ่ง), so color always carries meaning rather than
    being decorative. Replaces the old single long vertical-list bubble.
    """
    if not stations:
        bubble = BubbleContainer(
            body=BoxComponent(
                layout="vertical", padding_all="lg",
                contents=[
                    TextComponent(text="รายงานระดับน้ำ", weight="bold", size="lg", color="#111827"),
                    TextComponent(
                        text="ไม่พบสถานีวัดระดับน้ำในพื้นที่ใกล้เคียง",
                        size="sm", color="#6B7280", margin="md", wrap=True
                    ),
                ]
            )
        )
        return FlexSendMessage(alt_text="รายงานระดับน้ำ", contents=bubble)

    bubbles = []
    for i, st in enumerate(stations):
        wl = st.get("water_level")
        dist = st.get("distance_km", 0)
        wl_val = "-"
        assessment = assess_water_level_status(None)

        if wl and wl.get("value") not in [None, "-", ""]:
            try:
                wl_val = float(wl["value"])
                bl = st.get("bank_level")
                situation = st.get("situation")
                assessment = assess_water_level_status(wl_val, bl, situation)
            except (ValueError, TypeError):
                pass

        lbl_pill = assessment.get("label_pill", "ปกติ")
        sev = _WATER_SEVERITY_COLORS.get(lbl_pill, _WATER_SEVERITY_COLORS["ปกติ"])

        lat = st.get("latitude")
        lon = st.get("longitude")
        nav_action = (
            URIAction(label="นำทาง", uri=f"https://www.google.com/maps/search/?api=1&query={lat},{lon}")
            if lat and lon else
            URIAction(label="ดูเพิ่มเติม", uri=WATER_LEVEL_SOURCE_URL)
        )

        header = BoxComponent(
            layout="horizontal",
            padding_all="lg",
            contents=[
                TextComponent(text=f"อันดับ {i + 1}", size="sm", weight="bold", color="#0F172A", flex=1),
                TextComponent(text=f"ห่าง {dist:.1f} กม.", size="sm", weight="bold", color="#0F172A", align="end", flex=1),
            ]
        )

        body = BoxComponent(
            layout="vertical",
            padding_all="lg",
            spacing="sm",
            contents=[
                BoxComponent(
                    layout="horizontal",
                    contents=[
                        TextComponent(text="สถานีวัดระดับน้ำ", size="xs", color="#9CA3AF", flex=1, gravity="center"),
                        _pill_badge(lbl_pill, sev["bg"], sev["text"]),
                    ]
                ),
                BoxComponent(
                    layout="vertical", height="54px", justify_content="flex-start", margin="sm",
                    contents=[
                        TextComponent(
                            text=st.get("stationName", "ไม่ระบุ"), weight="bold", size="lg",
                            color="#111827", wrap=True, max_lines=2
                        ),
                    ]
                ),
                _dashed_rule(),
                BoxComponent(
                    layout="horizontal",
                    margin="lg",
                    align_items="center",
                    contents=[
                        BoxComponent(
                            layout="vertical", flex=1,
                            contents=[
                                TextComponent(text="ระดับน้ำปัจจุบัน", size="xs", color="#9CA3AF"),
                                TextComponent(
                                    text=f"{wl_val} ม." if wl_val != "-" else "ไม่มีข้อมูล",
                                    size="xl", weight="bold", color="#111827", margin="xs", wrap=True, max_lines=1
                                ),
                            ]
                        ),
                        _pill_button("นำทาง", nav_action),
                    ]
                ),
                TextComponent(
                    text="ข้อมูลจาก ThaiWater · อัปเดตทุก 10 นาที",
                    size="xxs", color="#9CA3AF", margin="lg", wrap=True
                ),
            ]
        )

        bubbles.append(BubbleContainer(
            size="mega",
            styles=BubbleStyle(header=BlockStyle(background_color=sev["bg"])),
            header=header,
            body=body,
        ))

    carousel = CarouselContainer(contents=bubbles)
    return FlexSendMessage(alt_text=f"รายงานระดับน้ำ ({len(stations)} สถานีใกล้คุณ)", contents=carousel)


MORE_SHELTERS_TRIGGERS = {
    "TH": "ดูศูนย์พักพิงเพิ่มเติม",
    "EN": "See more shelters",
    "MY": "Lihat lagi tempat perlindungan",
}

_SHELTER_CARD_I18N = {
    "TH": {
        "no_data_title": "ข้อมูลศูนย์พักพิง",
        "no_data_body": "ไม่พบข้อมูลศูนย์พักพิงในพื้นที่ใกล้เคียง",
        "alt_text": "ข้อมูลศูนย์พักพิง ({n} แห่งใกล้คุณ)",
        "rank": "อันดับ {i}",
        "distance": "ห่าง {d:.1f} กม.",
        "type_prefix": "ศูนย์พักพิง",
        "available": "จำนวนที่ว่าง",
        "unit": "ที่",
        "unspecified": "ไม่ระบุ",
        "nav": "นำทาง",
        "footer": "ข้อมูลจากฐานข้อมูลศูนย์พักพิง FLOODCARE AI",
        "facility_labels": {
            "เตียง": "เตียง", "ห้องน้ำ": "ห้องน้ำ", "ที่จอดรถ": "ที่จอดรถ",
            "ไฟฟ้า": "ไฟฟ้า", "น้ำสะอาด": "น้ำสะอาด", "อินเทอร์เน็ต": "อินเทอร์เน็ต",
            "รองรับผู้พิการ": "รองรับผู้พิการ", "รับสัตว์เลี้ยง": "รับสัตว์เลี้ยง", "มีแพทย์ประจำ": "มีแพทย์ประจำ",
        },
        "status_labels": {"เปิดรับ": "เปิดรับ", "ใกล้เต็ม": "ใกล้เต็ม", "เต็ม": "เต็ม", "ปิด": "ปิด"},
        "more_title": "ดูเพิ่มเติม",
        "more_body": "ยังมีศูนย์พักพิงใกล้คุณอีก {n} แห่ง",
        "more_button": "ดูเพิ่มเติม",
    },
    "EN": {
        "no_data_title": "Shelter Information",
        "no_data_body": "No shelters found near your location right now.",
        "alt_text": "Shelter info ({n} nearby)",
        "rank": "#{i}",
        "distance": "{d:.1f} km away",
        "type_prefix": "Shelter",
        "available": "Spots available",
        "unit": "spots",
        "unspecified": "Not specified",
        "nav": "Navigate",
        "footer": "Data from the FLOODCARE AI shelter database",
        "facility_labels": {
            "เตียง": "Beds", "ห้องน้ำ": "Restroom", "ที่จอดรถ": "Parking",
            "ไฟฟ้า": "Electricity", "น้ำสะอาด": "Clean water", "อินเทอร์เน็ต": "Internet",
            "รองรับผู้พิการ": "Wheelchair access", "รับสัตว์เลี้ยง": "Pets allowed", "มีแพทย์ประจำ": "On-site medic",
        },
        "status_labels": {"เปิดรับ": "Open", "ใกล้เต็ม": "Nearly full", "เต็ม": "Full", "ปิด": "Closed"},
        "more_title": "See more",
        "more_body": "There are {n} more shelters near you",
        "more_button": "See more",
    },
    "MY": {
        "no_data_title": "Maklumat Tempat Perlindungan",
        "no_data_body": "Tiada tempat perlindungan dijumpai berhampiran lokasi anda buat masa ini.",
        "alt_text": "Maklumat tempat perlindungan ({n} berhampiran)",
        "rank": "#{i}",
        "distance": "{d:.1f} km jauhnya",
        "type_prefix": "Tempat perlindungan",
        "available": "Tempat kosong",
        "unit": "tempat",
        "unspecified": "Tidak dinyatakan",
        "nav": "Navigasi",
        "footer": "Data daripada pangkalan data tempat perlindungan FLOODCARE AI",
        "facility_labels": {
            "เตียง": "Katil", "ห้องน้ำ": "Tandas", "ที่จอดรถ": "Tempat letak kereta",
            "ไฟฟ้า": "Elektrik", "น้ำสะอาด": "Air bersih", "อินเทอร์เน็ต": "Internet",
            "รองรับผู้พิการ": "Mesra OKU", "รับสัตว์เลี้ยง": "Terima haiwan peliharaan", "มีแพทย์ประจำ": "Ada petugas perubatan",
        },
        "status_labels": {"เปิดรับ": "Dibuka", "ใกล้เต็ม": "Hampir penuh", "เต็ม": "Penuh", "ปิด": "Ditutup"},
        "more_title": "Lihat lagi",
        "more_body": "Terdapat {n} lagi tempat perlindungan berhampiran anda",
        "more_button": "Lihat lagi",
    },
}


def build_shelter_flex_message(user_lat, user_lon, shelters, lang="TH", more_count=0):
    """
    Ticket-card carousel — one swipeable card per shelter, same visual
    language as build_water_level_flex_message. Header color maps to real
    availability (green=เปิดรับ, yellow=ใกล้เต็ม, pink=เต็ม, gray=ปิด).

    lang picks TH/EN/MY card text via _SHELTER_CARD_I18N (falls back to TH
    for any other/unset value). more_count, if > 0, appends a trailing
    "see more" bubble whose button re-triggers the search for the next
    batch — see MORE_SHELTERS_TRIGGERS / the /callback handler for how the
    tap is picked back up.
    """
    t = _SHELTER_CARD_I18N.get(lang, _SHELTER_CARD_I18N["TH"])

    if not shelters:
        bubble = BubbleContainer(
            body=BoxComponent(
                layout="vertical", padding_all="lg",
                contents=[
                    TextComponent(text=t["no_data_title"], weight="bold", size="lg", color="#111827"),
                    TextComponent(
                        text=t["no_data_body"],
                        size="sm", color="#6B7280", margin="md", wrap=True
                    ),
                ]
            )
        )
        return FlexSendMessage(alt_text=t["no_data_title"], contents=bubble)

    bubbles = []
    for i, sh in enumerate(shelters):
        status_key = sh.get("Status", "เปิดรับ")
        sev = _SHELTER_SEVERITY_COLORS.get(status_key, _SHELTER_SEVERITY_COLORS["เปิดรับ"])
        status_label = t["status_labels"].get(status_key, status_key)

        dist = sh.get("distance_km", 0)
        capacity = sh.get("Capacity", 0)
        occupancy = sh.get("Occupancy", 0)
        remaining = max(capacity - occupancy, 0) if capacity else None
        location_text = f"{sh.get('Subdistrict', '')} {sh.get('District', '')} {sh.get('Province', '')}".split()
        location_text = " ".join(location_text)

        nav_action = URIAction(
            label=t["nav"],
            uri=f"https://www.google.com/maps/search/?api=1&query={sh.get('Latitude')},{sh.get('Longitude')}"
        )

        header = BoxComponent(
            layout="horizontal",
            padding_all="lg",
            contents=[
                TextComponent(text=t["rank"].format(i=i + 1), size="sm", weight="bold", color="#0F172A", flex=1),
                TextComponent(text=t["distance"].format(d=dist), size="sm", weight="bold", color="#0F172A", align="end", flex=1),
            ]
        )

        body_contents = [
            BoxComponent(
                layout="horizontal",
                contents=[
                    TextComponent(
                        text=f"{t['type_prefix']}{' · ' + location_text if location_text else ''}",
                        size="xs", color="#9CA3AF", flex=1, gravity="center", wrap=True, max_lines=1
                    ),
                    _pill_badge(status_label, sev["bg"], sev["text"]),
                ]
            ),
            BoxComponent(
                layout="vertical", height="54px", justify_content="flex-start", margin="sm",
                contents=[
                    TextComponent(
                        text=sh.get("Name", t["unspecified"]), weight="bold", size="lg",
                        color="#111827", wrap=True, max_lines=2
                    ),
                ]
            ),
            _dashed_rule(),
            BoxComponent(
                layout="horizontal",
                margin="lg",
                align_items="center",
                contents=[
                    BoxComponent(
                        layout="vertical", flex=1,
                        contents=[
                            TextComponent(text=t["available"], size="xs", color="#9CA3AF"),
                            TextComponent(
                                text=f"{remaining}/{capacity} {t['unit']}" if remaining is not None else t["unspecified"],
                                size="xl", weight="bold", color="#111827", margin="xs", wrap=True, max_lines=1
                            ),
                        ]
                    ),
                    _pill_button(t["nav"], nav_action, bg="#0D9488"),
                ]
            ),
        ]

        # Facilities: only show the ones that are actually present, as small
        # tinted chips, instead of a 3x3 grid of every possible amenity with
        # a dash for whatever's missing. A shelter usually has 2-4 amenities
        # checked, so this keeps the card short and easy to scan instead of
        # forcing every card to render the same 9 rows regardless.
        present_facilities = [
            label for label, has in [
                ("เตียง", _has_facility(sh.get("Beds"))),
                ("ห้องน้ำ", _has_facility(sh.get("Toilets"))),
                ("ที่จอดรถ", _has_facility(sh.get("Parking"))),
                *[(label, _has_extra_facility(sh, label)) for label in EXTRA_FACILITIES],
            ] if has
        ]

        if present_facilities:
            body_contents.append(_dashed_rule())
            for row_start in range(0, len(present_facilities), 3):
                row_labels = present_facilities[row_start:row_start + 3]
                body_contents.append(
                    BoxComponent(
                        layout="horizontal",
                        margin="md" if row_start == 0 else "xs",
                        spacing="xs",
                        contents=[
                            BoxComponent(
                                layout="vertical", flex=1,
                                background_color="#ECFDF5", corner_radius="8px",
                                padding_top="6px", padding_bottom="6px", padding_start="4px", padding_end="4px",
                                contents=[TextComponent(
                                    text=f"✓ {t['facility_labels'].get(label, label)}", size="xxs", weight="bold", color="#047857",
                                    align="center", wrap=True, max_lines=1
                                )]
                            )
                            for label in row_labels
                        ]
                    )
                )

        body_contents.append(
            TextComponent(
                text=t["footer"],
                size="xxs", color="#9CA3AF", margin="lg", wrap=True
            )
        )

        body = BoxComponent(
            layout="vertical",
            padding_all="lg",
            spacing="sm",
            contents=body_contents
        )

        bubbles.append(BubbleContainer(
            size="mega",
            styles=BubbleStyle(header=BlockStyle(background_color=sev["bg"])),
            header=header,
            body=body,
        ))

    if more_count > 0:
        more_trigger = MORE_SHELTERS_TRIGGERS.get(lang, MORE_SHELTERS_TRIGGERS["TH"])
        bubbles.append(BubbleContainer(
            size="mega",
            body=BoxComponent(
                layout="vertical", padding_all="lg", justify_content="center",
                align_items="center", spacing="md",
                contents=[
                    TextComponent(text="➕", size="3xl", align="center"),
                    TextComponent(text=t["more_title"], weight="bold", size="lg", color="#111827", align="center"),
                    TextComponent(text=t["more_body"].format(n=more_count), size="xs", color="#6B7280",
                                  align="center", wrap=True),
                    _pill_button(t["more_button"], MessageAction(label=t["more_button"], text=more_trigger), bg="#0D9488"),
                ]
            )
        ))

    carousel = CarouselContainer(contents=bubbles)
    return FlexSendMessage(alt_text=t["alt_text"].format(n=len(shelters)), contents=carousel)



UI_TEXT = {
    "greeting_time_morning": {"TH": "อรุณสวัสดิ์", "MY": "Selamat pagi", "EN": "Good morning"},
    "greeting_time_default": {"TH": "สวัสดี", "MY": "Salam sejahtera", "EN": "Hello"},
    "greeting_intro": {
        "TH": "ผมคือ FLOODCARE AI ผู้ช่วยอัจฉริยะด้านภัยน้ำท่วมและเหตุฉุกเฉินครับ",
        "MY": "Saya FLOODCARE AI, pembantu pintar untuk banjir dan kecemasan.",
        "EN": "I'm FLOODCARE AI, your smart assistant for flood and emergency situations.",
    },
    "greeting_services_label": {
        "TH": "รายการบริการที่ผมช่วยคุณได้:",
        "MY": "Perkhidmatan yang boleh saya bantu:",
        "EN": "Here's what I can help you with:",
    },
    "greeting_services": {
        "TH": [
            "เบอร์โทรฉุกเฉินและสายด่วน",
            "SOS แจ้งเหตุขอความช่วยเหลือกู้ภัย",
            "ค้นหาศูนย์พักพิงและจุดอพยพ",
            "ตรวจสอบระดับน้ำและสภาพอากาศ",
            "แจ้งความต้องการสิ่งของบรรเทาทุกข์",
            "ติดตามสถานะเคสที่เคยแจ้งไว้",
            "คู่มือเตรียมความพร้อมและปฐมพยาบาล",
            "สอบถามข้อมูลภัยพิบัติผ่านระบบ AI",
        ],
        "MY": [
            "Talian kecemasan dan hotline",
            "SOS - lapor kecemasan minta bantuan",
            "Cari pusat perlindungan berdekatan",
            "Semak paras air dan cuaca",
            "Minta bantuan bekalan keperluan",
            "Semak status kes yang pernah dilaporkan",
            "Panduan persediaan dan pertolongan cemas",
            "Tanya soalan tentang bencana melalui AI",
        ],
        "EN": [
            "Emergency numbers and hotlines",
            "SOS — report an emergency and request rescue",
            "Find nearby shelters and evacuation points",
            "Check water levels and weather",
            "Request relief supplies",
            "Track the status of a case you've reported",
            "Preparedness and first-aid guides",
            "Ask disaster-related questions via AI",
        ],
    },
    "greeting_footer": {
        "TH": "ยินดีช่วยเหลือคุณตลอด 24 ชั่วโมงครับ",
        "MY": "Sedia membantu anda 24 jam sehari.",
        "EN": "Happy to help you 24 hours a day.",
    },
    "help_title": {
        "TH": "FLOODCARE AI ทำอะไรได้บ้าง",
        "MY": "Apa yang FLOODCARE AI boleh buat",
        "EN": "What FLOODCARE AI can do",
    },
    "note_ai_replies_thai": {
        "TH": None,
        "MY": "หมายเหตุ: คำตอบจาก AI และข้อมูลสถานี/ศูนย์พักพิงยังเป็นภาษาไทยเป็นหลัก (Nota: jawapan AI dan data stesen/pusat perlindungan masih dalam Bahasa Thai buat masa ini)",
        "EN": "Note: station/shelter names in the data are still shown in their original Thai form.",
    },
}


def t(key: str, lang: str = "TH"):
    """Small real translation lookup — falls back to Thai if the key or language isn't covered yet."""
    entry = UI_TEXT.get(key, {})
    return entry.get(lang) or entry.get("TH")


def get_greeting_message(user_name="คุณ", lang="TH"):
    now = get_bangkok_time()
    time_greeting = t("greeting_time_morning", lang) if 5 <= now.hour < 10 else t("greeting_time_default", lang)

    services = t("greeting_services", lang)
    services_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(services))

    text = (
        f"{time_greeting} คุณ {user_name}\n"
        f"{t('greeting_intro', lang)}\n\n"
        f"{t('greeting_services_label', lang)}\n"
        f"{services_text}\n\n"
        f"{t('greeting_footer', lang)}"
    )
    note = t("note_ai_replies_thai", lang)
    if note:
        text += f"\n\n{note}"
    return TextSendMessage(text=text)


def build_accident_flex_message() -> FlexSendMessage:
    """Symmetrical, minimal Flex Message for accident/injury response."""
    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            spacing="md",
            contents=[
                TextComponent(text="คำแนะนำกรณีอุบัติเหตุ", weight="bold", size="lg", color="#1F2937"),
                BoxComponent(
                    layout="vertical",
                    padding_all="md",
                    background_color="#FEF2F2",
                    corner_radius="md",
                    contents=[
                        TextComponent(text="ขั้นตอนการช่วยเหลือเบื้องต้น", weight="bold", size="sm", color="#B91C1C"),
                        TextComponent(text="1. ประเมินความปลอดภัยของสถานที่", size="xs", color="#B91C1C", margin="xs"),
                        TextComponent(text="2. ตรวจสอบการตอบสนองของผู้บาดเจ็บ", size="xs", color="#B91C1C"),
                        TextComponent(text="3. ห้ามเคลื่อนย้ายหากสงสัยว่ากระดูกหัก", size="xs", color="#B91C1C"),
                        TextComponent(text="4. โทรแจ้งสายด่วนกู้ชีพทันที", size="xs", color="#B91C1C"),
                    ]
                ),
                BoxComponent(
                    layout="vertical",
                    spacing="sm",
                    contents=[
                        ButtonComponent(
                            action=URIAction(label="โทรสายด่วนกู้ชีพ 1669", uri="tel:1669"),
                            style="primary", color="#DC2626", height="sm"
                        ),
                        ButtonComponent(
                            action=URIAction(label="แจ้งเหตุด่วนเหตุร้าย 191", uri="tel:191"),
                            style="secondary", color="#F9FAFB", height="sm"
                        )
                    ]
                )
            ]
        )
    )
    return FlexSendMessage(alt_text="คำแนะนำกรณีอุบัติเหตุ", contents=bubble)


def handle_emergency_response(user_id: str, event=None) -> TextSendMessage:
    emergency_text = (
        "คำแนะนำกรณีฉุกเฉิน\n\n"
        "1. ตัดกระแสไฟฟ้าทันที\n"
        "2. เคลื่อนย้ายขึ้นที่สูง\n"
        "3. ติดต่อเจ้าหน้าที่:\n"
        "   ปภ. 1784\n"
        "   กู้ชีพ 1669\n\n"
        "รักษาสติและรอในจุดที่ปลอดภัย"
    )
    return TextSendMessage(text=emergency_text)


def calculate_sos_priority(group_types: list, urgency_level: str) -> Tuple[str, str]:
    gt = [g.lower() for g in group_types] if group_types else []
    ul = (urgency_level or "").lower()
    
    critical_keywords = ["บาดเจ็บ", "ผู้ป่วย", "พิการ", "วิกฤต", "ขาดแคลน"]
    if any(k in g for g in gt for k in critical_keywords) or "วิกฤต" in ul:
        return ("🔴 CRITICAL", "CRITICAL")
    
    high_keywords = ["เด็ก", "ชรา", "เด็กเล็ก"]
    if any(k in g for g in gt for k in high_keywords) or "สูง" in ul:
        return ("🟠 HIGH", "HIGH")
    
    return ("🟢 NORMAL", "NORMAL")


SOS_STATUS_LABELS_TH = {"OPEN": "รอดำเนินการ", "IN_PROGRESS": "ทีมกำลังช่วยเหลือ", "CLOSED": "ช่วยเหลือสำเร็จ"}


def need_status_label_th(status: str) -> str:
    status = (status or "").upper()
    if status in ("DELIVERED", "DONE", "COMPLETED"):
        return "ส่งมอบแล้ว"
    if status in ("", "PENDING"):
        return "รอดำเนินการ"
    return "กำลังจัดเตรียม"


def start_background_tasks():
    def cleanup_loop():
        while True:
            try:
                time.sleep(300)
                session_count = sessions.cleanup_expired()
                cache_count = sum(cache.cleanup_all().values())
                
                if session_count > 0 or cache_count > 0:
                    Logger.info("Cleanup", f"Removed {session_count} sessions, {cache_count} cache entries")
            except Exception as e:
                Logger.error("Cleanup", f"Loop error: {e}")

    def water_level_refresh_loop():
        # Runs once immediately on startup, then every 10 minutes — pulls
        # live data straight from ThaiWater's API (bypassing the 15-min
        # in-memory cache) and persists it into the 'Water_Levels' sheet, so
        # the sheet itself always holds current data other tools can read,
        # and per-request bot replies can just read the sheet directly
        # instead of calling ThaiWater on every single user request.
        while True:
            try:
                cache.water.delete("thaiwater:water_levels_live")
                stations = get_live_water_levels_from_api()
                if stations:
                    ok = sheets_mgr.overwrite_water_levels(stations)
                    if ok:
                        global _last_water_refresh_ts
                        _last_water_refresh_ts = time.time()
                        Logger.info("WaterLevelRefresh", f"Updated {len(stations)} stations in Water_Levels sheet")
                        try:
                            run_water_alert_engine(stations)
                        except Exception as e:
                            Logger.error("WaterAlert", f"run_water_alert_engine failed: {e}")
                    else:
                        Logger.error("WaterLevelRefresh", "Failed to write stations to sheet")
                else:
                    Logger.error("WaterLevelRefresh", "ThaiWater API returned no stations — sheet left unchanged")
            except Exception as e:
                Logger.error("WaterLevelRefresh", f"Loop error: {e}")
            time.sleep(600)

    def status_notification_loop():
        # Polls sos_requests / user_needs every 3 minutes for status changes
        # made directly in the sheet by staff (e.g. accepting a case, marking
        # delivered) and pushes a LINE message to the reporting user the
        # moment it changes — instead of making them keep re-checking
        # 'ติดตามเคส' themselves. Uses 'last_notified_status' as a durable
        # marker (stored in the sheet itself) so a bot restart never causes
        # duplicate or missed notifications.
        base = PUBLIC_BASE_URL or "https://floodcare-ai-2.onrender.com"
        while True:
            try:
                for r in sheets_mgr.get_all_records("sos_requests"):
                    status = str(r.get("status", "")).upper().strip()
                    last_notified = str(r.get("last_notified_status", "")).upper().strip()
                    user_id = str(r.get("user_id", "")).strip()
                    case_id = str(r.get("request_id", "")).strip()
                    if not status or not user_id or not case_id or status == last_notified:
                        continue
                    if last_notified:  # skip push on first-ever sighting of a fresh row — just establish baseline
                        label = SOS_STATUS_LABELS_TH.get(status, status)
                        try:
                            line_bot_api.push_message(
                                user_id,
                                TextSendMessage(
                                    text=f"อัปเดตเคส {case_id}\nสถานะล่าสุด: {label}\n\nดูรายละเอียดเพิ่มเติมที่ {base}/liff/track?id={case_id}"
                                )
                            )
                            Logger.info("StatusNotify", f"Pushed SOS {case_id} -> {status}")
                        except Exception as e:
                            Logger.error("StatusNotify", f"Push failed for {case_id}: {e}")
                    sheets_mgr.update_row_by_id("sos_requests", "request_id", case_id, {"last_notified_status": status})

                for r in sheets_mgr.get_all_records("user_needs"):
                    status_raw = str(r.get("status", "")).strip()
                    last_notified = str(r.get("last_notified_status", "")).strip()
                    user_id = str(r.get("user_id", "")).strip()
                    case_id = str(r.get("need_id", "")).strip()
                    if not user_id or not case_id or status_raw.upper() == last_notified.upper():
                        continue
                    if last_notified:
                        label = need_status_label_th(status_raw)
                        try:
                            line_bot_api.push_message(
                                user_id,
                                TextSendMessage(
                                    text=f"อัปเดตรายการ {case_id}\nสถานะล่าสุด: {label}\n\nดูรายละเอียดเพิ่มเติมที่ {base}/liff/track?id={case_id}"
                                )
                            )
                            Logger.info("StatusNotify", f"Pushed NEED {case_id} -> {status_raw}")
                        except Exception as e:
                            Logger.error("StatusNotify", f"Push failed for {case_id}: {e}")
                    sheets_mgr.update_row_by_id("user_needs", "need_id", case_id, {"last_notified_status": status_raw})
            except Exception as e:
                Logger.error("StatusNotify", f"Loop error: {e}")
            time.sleep(180)

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()

    water_thread = threading.Thread(target=water_level_refresh_loop, daemon=True)
    water_thread.start()

    notify_thread = threading.Thread(target=status_notification_loop, daemon=True)
    notify_thread.start()

    Logger.info("System", "Background cleanup + water-level refresh + status notifications started")

start_background_tasks()
Logger.info("System", "FLOODCARE AI Bot Config v2.5.1 Initialized Successfully")
