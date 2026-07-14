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
        FlexSendMessage, BubbleContainer, BoxComponent, TextComponent,
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
            "what can you do", "capabilities", "คุณคือใคร", "คุณทำอะไรได้"
        ],
        "FAQ": [
            "คำถามยอดฮิต", "คำถามที่พบบ่อย", "faq", "คำถามทั่วไป", "อยากรู้เรื่อง", "บอกข้อมูล", 
            "ค้นหา", "search", "น้ำท่วม 2567", "น้ำท่วม 2568", "น้ำท่วมล่าสุด", "สถานการณ์น้ำ", 
            "ข่าวน้ำท่วม", "อัพเดทน้ำท่วม", "ระดับน้ำล่าสุด", "คาดการณ์น้ำ", "พยากรณ์น้ำ"
        ],
    }
    
    @classmethod
    def classify(cls, text: str) -> Tuple[str, float]:
        if not text:
            return ("AI_QUERY", 0.5)
        
        text_lower = text.strip().lower()
        text_clean = text_lower.strip("!.,😊🙏👋🆘 ")
        
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
    "EMERGENCY", "SOS", "NEEDS", "SNAKE_BITE", "ACCIDENT", "PREP_GUIDE", "GREETING",
    "HELP", "FAQ", "CONTACT", "SHELTER", "WATER_LEVEL", "WEATHER",
    "REGISTRATION", "LANGUAGE", "CANCEL", "AI_QUERY"
]

INTENT_AI_SYSTEM_INSTRUCTION = (
    "คุณคือระบบวิเคราะห์เจตนา (Intent Analyzer) ของแชทบอท FLOODCARE AI "
    "หน้าที่ของคุณคือวิเคราะห์ข้อความของผู้ใช้ แล้วตอบกลับเป็น JSON เท่านั้น "
    "ห้ามมีคำอธิบาย ห้ามมี markdown code fence ห้ามมีข้อความอื่นใดนอกจาก JSON object เดียว\n\n"
    "รูปแบบ JSON ที่ต้องตอบกลับเป๊ะๆ:\n"
    '{"intent": "<ONE_OF_INTENTS>", "scope": "NEARBY หรือ GENERAL หรือ NONE", "confidence": <0.0-1.0>}\n\n'
    f"รายการ intent ที่เลือกได้: {', '.join(INTENT_LIST_AI)}\n\n"
    "คำอธิบาย intent:\n"
    "- EMERGENCY: สถานการณ์คับขันเป็นอันตรายถึงชีวิตตอนนี้ (กำลังจมน้ำ ไฟดูด ฯลฯ)\n"
    "- SOS: ขอความช่วยเหลือกู้ภัยจากน้ำท่วม ต้องการให้ทีมไปช่วยเหลือ (ชีวิต/ความปลอดภัย)\n"
    "- NEEDS: ขอความช่วยเหลือเรื่องสิ่งของ/เสบียง/ของบรรเทาทุกข์ (ไม่ใช่ขอกู้ภัยฉุกเฉิน)\n"
    "- SNAKE_BITE: ถูกงูกัด\n"
    "- ACCIDENT: อุบัติเหตุ บาดเจ็บ\n"
    "- PREP_GUIDE: ถามวิธีเตรียมตัวรับมือน้ำท่วมล่วงหน้า\n"
    "- GREETING: ทักทาย\n"
    "- HELP: ถามว่าบอททำอะไรได้บ้าง/วิธีใช้งาน\n"
    "- CONTACT: ขอเบอร์โทรฉุกเฉิน/หน่วยงาน\n"
    "- SHELTER: ถามเกี่ยวกับศูนย์พักพิง/ที่อพยพ/ที่ควรไปหลบภัย รวมถึงคำถามที่ไม่ได้พูดคำว่า 'ศูนย์พักพิง' ตรงๆ "
    "แต่ความหมายคือต้องการรู้ว่าตนเอง ณ ตอนนี้ควรไปที่ไหน/อพยพไปทางไหน (เช่น \"ตอนนี้ผมควรอพยพไปที่ไหน\", "
    "\"ควรไปหลบที่ไหนดี\") ให้ถือเป็น SHELTER เช่นกัน — ถ้าถามหาที่ใกล้ตัวเอง (แถวนี้ ใกล้ฉัน บ้านฉัน ตอนนี้) "
    "ให้ scope=NEARBY, ถ้าถามภาพรวม/ทั่วไป/จำนวน/ต่างจังหวัด-ต่างภาค ให้ scope=GENERAL\n"
    "- WATER_LEVEL: ถามเกี่ยวกับระดับน้ำ — ถ้าถามระดับน้ำใกล้ตัวเอง (บ้าน แถวนี้ ตอนนี้ตรงนี้) "
    "ให้ scope=NEARBY, ถ้าถามภาพรวมภูมิภาค/จังหวัด/ประเทศ/สถานการณ์ทั่วไปที่ไม่เจาะจงตัวผู้ใช้ ให้ scope=GENERAL\n"
    "- WEATHER: ถามสภาพอากาศ/พยากรณ์อากาศ\n"
    "- REGISTRATION: ต้องการลงทะเบียนข้อมูลส่วนตัว\n"
    "- LANGUAGE: ต้องการเปลี่ยนภาษา\n"
    "- CANCEL: ต้องการยกเลิก/หยุดขั้นตอนที่ทำอยู่\n"
    "- FAQ: ถามข้อมูลข่าวสาร/สถานการณ์น้ำท่วมทั่วไปที่ต้องอาศัยข้อมูลล่าสุดจากอินเทอร์เน็ต\n"
    "- AI_QUERY: คำถามทั่วไปเกี่ยวกับน้ำท่วม/ความปลอดภัย/สุขภาพกายใจจากภัยพิบัติที่ไม่เข้าเงื่อนไขข้างต้น "
    "รวมถึงคำถามที่ไม่เกี่ยวข้องกับน้ำท่วม/ความปลอดภัยเลย (เช่น ขอเลขหวย แต่งกลอน สูตรอาหาร คำถามทั่วไปอื่นๆ) "
    "ให้จัดเป็น AI_QUERY เสมอเช่นกัน (ระบบปลายทางจะปฏิเสธอย่างสุภาพเองตามขอบเขตที่กำหนดไว้)\n\n"
    "สำหรับ intent ที่ไม่ใช่ SHELTER หรือ WATER_LEVEL ให้ใส่ scope เป็น \"NONE\" เสมอ\n"
    "ตัวอย่าง: \"ภาคเหนือระดับน้ำเป็นอย่างไร\" -> WATER_LEVEL / GENERAL (เพราะถามภาพรวมภูมิภาค ไม่ใช่ใกล้ตัวผู้ใช้)\n"
    "ตัวอย่าง: \"น้ำแถวบ้านผมเป็นไงบ้าง\" -> WATER_LEVEL / NEARBY (เพราะถามใกล้ตัวผู้ใช้)\n"
    "ตัวอย่าง: \"ตอนนี้ผมควรอพยพไปที่ไหน\" -> SHELTER / NEARBY (แม้ไม่มีคำว่าศูนย์พักพิง แต่ความหมายคือถามหาที่ปลอดภัยใกล้ตัวตอนนี้)"
)


def classify_intent_ai(text: str) -> dict:
    """
    AI-based intent + scope classifier — this is what free-text (non-menu)
    messages are routed through now, replacing keyword guessing. Returns
    {"intent": str, "scope": "NEARBY"|"GENERAL"|"NONE", "confidence": float}.

    Always falls back to the old rule-based IntentClassifier.classify() if
    Gemini is unavailable, errors out, or returns something that can't be
    parsed as valid JSON/intent — so the bot never gets stuck without an
    intent just because the AI call had a hiccup.
    """
    fallback_intent, fallback_conf = IntentClassifier.classify(text)
    fallback = {"intent": fallback_intent, "scope": "GENERAL", "confidence": fallback_conf}

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
            ),
        )
        raw = (response.text or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)

        intent = str(parsed.get("intent", "")).strip().upper()
        scope = str(parsed.get("scope", "GENERAL")).strip().upper()
        confidence = float(parsed.get("confidence", 0.7))

        if intent not in set(INTENT_LIST_AI):
            Logger.info("IntentAI", f"Unknown intent '{intent}' returned by AI — using keyword fallback")
            return fallback
        if scope not in ("NEARBY", "GENERAL", "NONE"):
            scope = "GENERAL"

        result = {"intent": intent, "scope": scope, "confidence": confidence}
        cache.general.set(cache_key, result, ttl=120)

        elapsed = (time.time() - start_time) * 1000
        Logger.perf("IntentAI", "classify", elapsed, {"intent": intent, "scope": scope})
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


def compose_water_level_reply(user_question: str, stations: list) -> str:
    """
    Turns already-computed nearest-station data (distance, level, situation —
    all calculated in app.py using the existing Google Sheets lookup, never
    by the AI) into a short, natural conversational Thai reply. Used only for
    the AI-intent path; the Rich-Menu path keeps using
    build_water_level_flex_message for its card UI, unchanged.
    """
    if not stations:
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

    prompt = (
        f'ผู้ใช้ถามว่า: "{user_question}"\n\n'
        "นี่คือข้อมูลสถานีวัดระดับน้ำที่ใกล้ตำแหน่งผู้ใช้ที่สุด (ระยะทางและตัวเลขคำนวณมาให้แล้ว "
        "ห้ามคำนวณ ห้ามเดา หรือแก้ไขตัวเลขใดๆ เพิ่มเอง ใช้ตามที่ให้มาเท่านั้น):\n\n"
        f"{data_block}\n\n"
        "จงเรียบเรียงข้อมูลนี้เป็นคำตอบสนทนาภาษาไทยที่เป็นธรรมชาติ กระชับ ไม่เกิน 4-5 บรรทัด "
        "บอกสถานีที่ใกล้ที่สุดก่อน แล้วเสริมสถานีถัดไปถ้าจำเป็น ห้ามใช้เครื่องหมายดอกจัน "
        "ถ้าพบว่าระดับน้ำอยู่ในสถานการณ์วิกฤตหรือเกินตลิ่ง ให้เตือนให้ระวังและแนะนำให้ติดตามสถานการณ์ใกล้ชิดด้วย"
    )
    return ask_gemini(prompt, max_tokens=1024, system_instruction=NEARBY_DATA_REPLY_SYSTEM_INSTRUCTION)


def compose_shelter_reply(user_question: str, shelters: list) -> str:
    """
    Same idea as compose_water_level_reply, but for nearest-shelter data from
    find_nearest_shelters() (distance/capacity/status all pre-computed).
    """
    if not shelters:
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

    prompt = (
        f'ผู้ใช้ถามว่า: "{user_question}"\n\n'
        "นี่คือข้อมูลศูนย์พักพิงที่ใกล้ตำแหน่งผู้ใช้ที่สุด (ระยะทางและข้อมูลคำนวณมาให้แล้ว "
        "ห้ามเดาหรือแก้ไขตัวเลขใดๆ เพิ่มเอง ใช้ตามที่ให้มาเท่านั้น):\n\n"
        f"{data_block}\n\n"
        "จงเรียบเรียงข้อมูลนี้เป็นคำตอบสนทนาภาษาไทยที่เป็นธรรมชาติ กระชับ ไม่เกิน 4-5 บรรทัด "
        "บอกศูนย์ที่ใกล้ที่สุดก่อน ถ้าศูนย์ที่ใกล้ที่สุดมีสถานะ 'เต็ม' ให้แนะนำศูนย์ถัดไปที่ยังเปิดรับแทน "
        "ห้ามใช้เครื่องหมายดอกจัน"
    )
    return ask_gemini(prompt, max_tokens=1024, system_instruction=NEARBY_DATA_REPLY_SYSTEM_INSTRUCTION)


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


def ask_gemini(prompt: str, max_tokens: int = 8192, system_instruction: str = None) -> str:
    """
    Optimized Gemini API call.
    - Uses full token capacity (8192) to avoid truncation issues.
    - system_instruction defaults to FLOODCARE_SYSTEM_INSTRUCTION (the usual
      bulleted-list persona) but callers that need a different reply shape
      (e.g. a natural one-paragraph conversational answer, like
      compose_water_level_reply) can pass their own instead.
    """
    start_time = time.time()
    if not init_gemini():
        return "⚠️ ขออภัยครับ ระบบ AI ไม่พร้อมใช้งานชั่วคราว หากอยู่ในอันตรายเร่งด่วน โทร ปภ. 1784 ได้ทันทีครับ"
    
    effective_system_instruction = system_instruction or FLOODCARE_SYSTEM_INSTRUCTION

    cache_key = f"gemini:{hashlib.md5((effective_system_instruction + '|' + prompt).encode()).hexdigest()}"
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
        return "⚠️ ขออภัยครับ ระบบ AI ขัดข้องชั่วคราว หากอยู่ในอันตรายเร่งด่วน โทร ปภ. 1784 ได้ทันทีครับ"




def ask_gemini_with_search(question: str, max_tokens: int = 8192) -> dict:
    """
    Gemini API call with Google Search grounding.
    - Uses full token capacity (8192) to avoid truncation issues.
    """
    if not init_gemini():
        return {"answer": "⚠️ ขออภัยครับ ระบบ AI ไม่พร้อมใช้งานชั่วคราว หากอยู่ในอันตรายเร่งด่วน โทร ปภ. 1784 ได้ทันทีครับ", "sources": []}

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
                    "You are FLOODCARE AI. Always respond in Thai. Be concise — answer only what "
                    "was asked, skip background info or details the user didn't request. Structure "
                    "the answer as a numbered list (1. 2. 3. ...) with a line break between each "
                    "point, max 4-5 points, each point 1-2 short lines, total answer roughly 60-80 "
                    "words unless the question genuinely requires a complete step-by-step procedure. "
                    "Only skip numbering for a genuinely single-point, very short answer. No "
                    "asterisks. Never state or cite sources inline in the answer text (no "
                    "'(ที่มา: ...)' or similar) — the system displays the reference sources "
                    "separately below the message automatically. Use the Google Search tool to "
                    "ground your answer. Always finish complete sentences — never truncate or stop "
                    "mid-sentence — but plan for a concise answer up front rather than writing long "
                    "and cutting it off."
                ),
                max_output_tokens=max_tokens,
                temperature=0.2,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
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
        answer = ask_gemini(prompt, max_tokens=max_tokens)
        return {"answer": answer, "sources": []}


def clean_text_for_line(text: str) -> str:
    if not text:
        return ""
    return text.replace("**", "").replace("*", "").replace("###", "").replace("##", "").replace("#", "")


def extract_number(text: str) -> str:
    if not text:
        return "1"
    cleaned = "".join(filter(str.isdigit, text))
    return cleaned if cleaned else "1"


def parse_yes_no(text: str) -> str:
    if not text:
        return "NO"
    text_clean = text.strip().lower()
    yes_words = ["มี", "ใช่", "yes", "y", "ตกลง", "ok", "ได้"]
    if any(word in text_clean for word in yes_words):
        if "ไม่มี" in text_clean or "ไม่ใช่" in text_clean:
            return "NO"
        return "YES"
    return "NO"


def extract_sheet_id(sheet_var: str) -> str:
    if not sheet_var:
        return ""
    if "/d/" in sheet_var:
        parts = sheet_var.split("/d/")
        if len(parts) > 1:
            sub = parts[1].split("/")[0].strip()
            return sub
    return sheet_var.strip()


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
                         "consent_pdpa", "register_date", "status"],
                "sos_requests": ["request_id", "household_id", "user_id", "timestamp", "latitude", "longitude",
                                "people_count", "children", "elderly", "bedridden", "pets",
                                "water_level", "note", "priority", "status"],
                "user_needs": ["need_id", "timestamp", "user_id", "first_name", "last_name", "phone",
                              "latitude", "longitude", "categories", "details", "urgency", "status",
                              "halal_required", "volunteer_name", "delivered_at"],
                "Shelters": ["ShelterID", "Name", "Province", "District", "Subdistrict", "Latitude",
                            "Longitude", "Capacity", "Occupancy", "Status",
                            "Beds", "Toilets", "Parking", "Facilities"],
                "Water_Levels": ["StationCode", "Name", "River", "Location", "Lat", "Lon",
                                "WaterLevel", "BankLevel", "Situation", "Trend", "Time"],
                "Contacts": ["ContactID", "Name", "Role", "Phone"],
                "AI_Logs": ["Timestamp", "UserID", "Intent", "Question", "Answer", "ResponseTimeMs"],
                "System_Logs": ["Timestamp", "Level", "Module", "Message", "UserID"],
            }
            
            for name, headers in required_sheets.items():
                if name not in existing:
                    ws = sheet.add_worksheet(title=name, rows="3000", cols=len(headers) + 5)
                    ws.append_row(headers)
                    Logger.info("Sheets", f"Created worksheet: {name}")
            
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
                    ["S001", "โรงเรียนเทศบาล 2 (มลายูบางกอก)", "ยะลา", "เมืองยะลา",
                     6.5458, 101.2825, "", "", "เปิดรับ", "", "", "", ""],
                    ["S002", "โรงเรียนเทศบาล 3 (วัดพุทธภูมิ)", "ยะลา", "เมืองยะลา",
                     6.5445, 101.2912, "", "", "เปิดรับ", "", "", "", ""],
                    ["S003", "โรงเรียนเทศบาล 4 (ธนวิถี)", "ยะลา", "เมืองยะลา",
                     6.5401, 101.2833, "", "", "เปิดรับ", "", "", "", ""],
                    ["S004", "โรงเรียนเทศบาล 5 (บ้านตลาดเก่า)", "ยะลา", "เมืองยะลา",
                     6.5385, 101.2980, "", "", "เปิดรับ", "", "", "", ""],
                    ["S005", "ศูนย์เยาวชน (TK Park)", "ยะลา", "เมืองยะลา",
                     6.5470, 101.2905, "", "", "เปิดรับ", "", "", "", ""],
                    # NOTE: S006 has no verified Lat/Long yet. get_shelters_from_sheet()
                    # will silently skip this row until coordinates are filled in.
                    ["S006", "อาคารศรีนิบง", "ยะลา", "เมืองยะลา",
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
            records = ws.get_all_records()
            cache.sheets.set(cache_key, records, ttl=300)
            return records
        except Exception as e:
            Logger.error("Sheets", f"Get records error: {e}")
            return []
    
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
            records = ws.get_all_records()
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
            records = ws.get_all_records()
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

    def update_need_status(self, need_id: str, new_status: str) -> Optional[dict]:
        """Updates a user_needs row's status by need_id — used by the dashboard's
        need-fulfillment actions. Stamps delivered_at when marked delivered/done."""
        client = self.get_client()
        if not client:
            return None
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet("user_needs")
            records = ws.get_all_records()
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
            records = ws.get_all_records()
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

sheets_mgr = SheetsManager()


# =============================================================================
# SECTION 9B: SHELTER (EVACUATION CENTER) DATA
# =============================================================================

SHELTER_STATUS_MAP = {
    "เปิดรับ": {"label": "🟢 เปิดรับ", "bg": "#DCFCE7", "text": "#15803D"},
    "ใกล้เต็ม": {"label": "🟡 ใกล้เต็ม", "bg": "#FEF9C3", "text": "#A16207"},
    "เต็ม": {"label": "🔴 เต็มแล้ว", "bg": "#FEE2E2", "text": "#B91C1C"},
    "ปิด": {"label": "⚫ ปิดชั่วคราว", "bg": "#E5E7EB", "text": "#374151"},
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


def calculate_situation(water_level, bank_level):
    try:
        wl = float(water_level) if water_level is not None else 0
        bl = float(bank_level) if bank_level is not None else 0
    except (ValueError, TypeError):
        return "ไม่มีข้อมูล"
    
    if bl <= 0:
        if wl >= 3.0: return "ล้นตลิ่ง"
        if wl >= 2.0: return "มาก"
        if wl >= 1.0: return "ปกติ"
        if wl >= 0.5: return "น้อย"
        return "น้อยวิกฤต"
    
    ratio = wl / bl
    if wl >= bl: return "ล้นตลิ่ง"
    elif ratio >= 0.70: return "มาก"
    elif ratio >= 0.30: return "ปกติ"
    elif ratio >= 0.10: return "น้อย"
    return "น้อยวิกฤต"


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


def build_help_flex(lang="TH"):
    """
    Capabilities / help menu.
    Perfectly aligned to IMG_8355.jpeg specifications.
    """
    items = [
        ("🆘", "แจ้งเหตุฉุกเฉิน", "พิมพ์ 'sos'"),
        ("📦", "ขอความช่วยเหลือเรื่องสิ่งของ", "พิมพ์ 'ขอของ'"),
        ("🌊", "เช็คระดับน้ำใกล้คุณ", "พิมพ์ 'เช็คระดับน้ำ' แล้วแชร์พิกัด"),
        ("🌦️", "เช็คสภาพอากาศ", "พิมพ์ 'สภาพอากาศ' แล้วแชร์พิกัด"),
        ("🏠", "หาศูนย์พักพิงใกล้คุณ", "พิมพ์ 'ศูนย์พักพิง' แล้วแชร์พิกัด"),
        ("🎒", "วิธีเตรียมตัวก่อนน้ำท่วม", "พิมพ์ 'วิธีเตรียมตัว'"),
        ("☎️", "เบอร์ติดต่อฉุกเฉิน", "พิมพ์ 'เบอร์โทร'"),
        ("📝", "ลงทะเบียนข้อมูลของคุณ", "พิมพ์ 'ลงทะเบียน'"),
        ("🌐", "เปลี่ยนภาษา", "พิมพ์ 'เปลี่ยนภาษา'"),
    ]
    contents = [
        TextComponent(text="🤖 FLOODCARE AI ทำอะไรได้บ้าง", weight="bold", size="lg", color="#1F2937"),
        SeparatorComponent(margin="md"),
    ]
    for icon, title, how in items:
        contents.append(
            BoxComponent(
                layout="horizontal", margin="md", spacing="sm",
                contents=[
                    TextComponent(text=icon, size="md", flex=0),
                    BoxComponent(
                        layout="vertical", flex=1,
                        contents=[
                            TextComponent(text=title, size="sm", weight="bold", color="#1F2937"),
                            TextComponent(text=how, size="xs", color="#6B7280"),
                        ]
                    )
                ]
            )
        )
    return FlexSendMessage(
        alt_text="🤖 FLOODCARE AI ทำอะไรได้บ้าง",
        contents=BubbleContainer(body=BoxComponent(layout="vertical", contents=contents))
    )


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
    return FlexSendMessage(
        alt_text="🌐 เลือกภาษา",
        contents=BubbleContainer(
            size="sm",
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text="🌐 Language", weight="bold", size="md", color="#1F2937", align="center"),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(action=MessageAction(label="[TH] ภาษาไทย", text="ตั้งค่าภาษา: TH"),
                                    style="secondary", color="#F3F4F6", height="sm"),
                    ButtonComponent(action=MessageAction(label="[EN] English", text="ตั้งค่าภาษา: EN"),
                                    style="secondary", color="#F3F4F6", height="sm"),
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


def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, lang="TH"):
    """
    Minimal water-level report card.
    Each station is rendered as a soft, self-contained stat card:
    name + status pill on one row, then a clean 3-column stat grid
    (ระดับน้ำ / ระดับตลิ่ง / ต่างจากตลิ่ง) — no clutter, no extra dividers.
    """
    header = BoxComponent(
        layout="vertical",
        spacing="xs",
        contents=[
            TextComponent(text="รายงานระดับน้ำ", weight="bold", size="lg", color="#1F2937"),
            _icon_text("icon_pin.jpg", f"พิกัด : {user_lat:.4f}, {user_lon:.4f}"),
            _icon_text("icon_clock.jpg", f"เวลา: {timestamp}"),
        ]
    )

    stations_box = BoxComponent(layout="vertical", spacing="md", margin="lg", contents=[])

    is_critical_any = False
    if not stations:
        stations_box.contents.append(
            TextComponent(text="ไม่พบสถานีวัดระดับน้ำในพื้นที่ใกล้เคียง", size="sm", color="#EF4444", align="center")
        )
    else:
        def _stat_cell(label: str, value: str, value_color: str = "#111827"):
            return BoxComponent(
                layout="vertical",
                flex=1,
                spacing="xs",
                contents=[
                    TextComponent(text=label, size="xxs", color="#9CA3AF", align="center"),
                    TextComponent(text=value, size="sm", weight="bold", color=value_color, wrap=True, align="center"),
                ]
            )

        for st in stations:
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

            bl_val = st.get("bank_level", "-")
            lbl_pill = assessment.get("label_pill", "ปกติ")
            if lbl_pill in ["ล้นตลิ่ง", "วิกฤต"]:
                is_critical_any = True

            # Safe parsing for diff calculation
            diff_label = "ต่างจากตลิ่ง"
            diff_text_formatted = "-"
            diff_color = "#111827"
            if wl_val != "-" and bl_val != "-":
                try:
                    wl_f = float(wl_val)
                    bl_f = float(bl_val)
                    diff_val = bl_f - wl_f
                    if diff_val < 0:
                        diff_text_formatted = f"สูงกว่า {abs(diff_val):.2f} ม."
                        diff_color = "#DC2626"
                    else:
                        diff_text_formatted = f"ต่ำกว่า {diff_val:.2f} ม."
                except Exception:
                    pass

            # Pick a themed illustration to match the station's context —
            # bridge / city / village houses — same visual language as the
            # reference template (small inline thumbnail, not a full hero).
            name_l = st['stationName']
            if "สะพาน" in name_l:
                station_img = "water_bridge.jpg"
            elif "เมือง" in name_l or "อำเภอเมือง" in name_l:
                station_img = "water_city.jpg"
            else:
                station_img = "water_houses.jpg"
            thumb_url = hero_image_url(station_img)

            card_inner = BoxComponent(
                layout="vertical",
                flex=1,
                spacing="sm",
                contents=[
                    # Row 1 — Station name
                    TextComponent(text=st['stationName'], weight="bold", size="sm", color="#111827", wrap=True),
                    # Row 2 — distance and status pill
                    BoxComponent(
                        layout="horizontal",
                        spacing="sm",
                        contents=[
                            TextComponent(text=f"ห่าง {dist:.2f} กม.", size="xs", color="#6B7280", flex=1, gravity="center"),
                            BoxComponent(
                                layout="vertical",
                                flex=0,
                                gravity="center",
                                background_color=assessment.get("bg", "#E5E7EB"),
                                corner_radius="xxl",
                                padding_start="md",
                                padding_end="md",
                                padding_top="xs",
                                padding_bottom="xs",
                                contents=[
                                    TextComponent(
                                        text=lbl_pill, size="xs",
                                        color=assessment.get("text", "#1F2937"),
                                        weight="bold", align="center"
                                    )
                                ]
                            ),
                        ]
                    ),
                    SeparatorComponent(margin="sm", color="#F3F4F6"),
                    # Row 2 — Clean 3-column stat grid
                    BoxComponent(
                        layout="horizontal",
                        spacing="md",
                        margin="sm",
                        contents=[
                            _stat_cell("ระดับน้ำ", f"{wl_val} ม." if wl_val != "-" else "-"),
                            _stat_cell("ระดับตลิ่ง", f"{bl_val} ม." if bl_val != "-" else "-"),
                            _stat_cell(diff_label, diff_text_formatted, diff_color),
                        ]
                    ),
                ]
            )

            card_contents = [card_inner]
            if thumb_url:
                card_contents = [
                    BoxComponent(
                        layout="horizontal",
                        spacing="md",
                        contents=[
                            card_inner,
                            ImageComponent(
                                url=thumb_url,
                                size="72px",
                                flex=0,
                                aspect_ratio="1:1",
                                aspect_mode="cover",
                                gravity="center",
                            ),
                        ]
                    )
                ]

            card = BoxComponent(
                layout="vertical",
                spacing="sm",
                padding_all="md",
                contents=card_contents
            )
            stations_box.contents.append(card)

    body_contents = [header]
    if is_critical_any:
        body_contents.append(
            BoxComponent(
                layout="vertical",
                margin="md",
                padding_all="md",
                background_color="#FEF2F2",
                corner_radius="md",
                contents=[
                    TextComponent(text="คำแนะนำความปลอดภัย", weight="bold", size="sm", color="#B91C1C"),
                    TextComponent(text="1. ตัดกระแสไฟฟ้าในจุดที่น้ำท่วมถึง", size="xs", color="#B91C1C", margin="xs"),
                    TextComponent(text="2. เคลื่อนย้ายคนและสิ่งของขึ้นที่สูง", size="xs", color="#B91C1C"),
                    TextComponent(text="3. ติดตามสถานการณ์อย่างใกล้ชิด", size="xs", color="#B91C1C"),
                    ButtonComponent(
                        action=URIAction(label="โทรสายด่วน 1784", uri="tel:1784"),
                        style="primary", color="#DC2626", height="sm", margin="md"
                    )
                ]
            )
        )
    body_contents.append(stations_box)

    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            spacing="md",
            contents=body_contents
        ),
        footer=BoxComponent(
            layout="vertical",
            spacing="sm",
            padding_top="sm",
            contents=[
                ButtonComponent(
                    action=URIAction(label="ดูข้อมูลเพิ่มเติมที่ ThaiWater", uri=WATER_LEVEL_SOURCE_URL),
                    style="secondary",
                    color="#F3F4F6",
                    height="sm"
                ),
                TextComponent(
                    text="สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)",
                    size="xxs",
                    color="#9CA3AF",
                    align="center",
                    margin="xs",
                    wrap=True
                )
            ]
        )
    )
    return FlexSendMessage(alt_text="รายงานระดับน้ำ", contents=bubble)


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


def build_shelter_flex_message(user_lat, user_lon, shelters, lang="TH"):
    """
    Minimalist Shelter (Evacuation Center) Report card.
    Mirrors the water-level card's visual language (status pill + spacing).
    """
    header = BoxComponent(
        layout="vertical",
        spacing="xs",
        contents=[
            TextComponent(text="ข้อมูลศูนย์พักพิง", weight="bold", size="lg", color="#1F2937"),
            _icon_text("icon_pin.jpg", f"พิกัด : {user_lat:.4f}, {user_lon:.4f}"),
            _icon_text("icon_clock.jpg", f"เวลา: {get_bangkok_time().strftime('%d %b %Y %H:%M')} น."),
        ]
    )

    shelters_box = BoxComponent(layout="vertical", spacing="xl", margin="lg", contents=[])

    if not shelters:
        shelters_box.contents.append(
            TextComponent(text="ไม่พบข้อมูลศูนย์พักพิงในพื้นที่ใกล้เคียง", size="sm", color="#EF4444", align="center")
        )
    else:
        for sh in shelters:
            status_key = sh.get("Status", "เปิดรับ")
            assessment = SHELTER_STATUS_MAP.get(status_key, SHELTER_STATUS_MAP["เปิดรับ"])
            dist = sh.get("distance_km", 0)
            capacity = sh.get("Capacity", 0)
            occupancy = sh.get("Occupancy", 0)
            remaining = max(capacity - occupancy, 0) if capacity else None

            capacity_text = (
                f"ว่าง {remaining}/{capacity} ที่" if remaining is not None else "ไม่ระบุความจุ"
            )

            card = BoxComponent(
                layout="vertical",
                spacing="xs",
                padding_all="md",
                contents=[
                    # Name
                    TextComponent(text=sh.get("Name", "ไม่ระบุชื่อ"), weight="bold",
                                size="sm", color="#111827", wrap=True),
                    # Distance & Status Pill
                    BoxComponent(
                        layout="horizontal",
                        spacing="sm",
                        contents=[
                            TextComponent(text=f"ห่าง {dist:.1f} กม.", size="xs", color="#6B7280", flex=1, gravity="center"),
                            BoxComponent(
                                layout="vertical",
                                flex=0,
                                gravity="center",
                                background_color=assessment.get("bg", "#F0FDF4") if status_key == "เปิดรับ" else assessment.get("bg", "#FEF2F2"),
                                corner_radius="xxl",
                                padding_start="md",
                                padding_end="md",
                                padding_top="xs",
                                padding_bottom="xs",
                                contents=[
                                    TextComponent(
                                        text=assessment.get("label", status_key),
                                        size="xs",
                                        color=assessment.get("text", "#15803D") if status_key == "เปิดรับ" else assessment.get("text", "#B91C1C"),
                                        weight="bold",
                                        align="center"
                                    )
                                ]
                            ),
                        ]
                    ),
                    TextComponent(
                        text=f"{sh.get('District', '')} {sh.get('Province', '')}".strip(),
                        size="xs", color="#6B7280"
                    ),
                    # Capacity Info
                    TextComponent(
                        text=capacity_text,
                        size="xs",
                        color="#4B5563",
                        margin="xs",
                        align="center"
                    ),
                    SeparatorComponent(margin="sm", color="#F3F4F6"),
                    # Amenities Grid (Symmetrical 3-column)
                    BoxComponent(
                        layout="horizontal",
                        spacing="md",
                        margin="sm",
                        contents=[
                            BoxComponent(
                                layout="vertical", flex=1, spacing="xs",
                                contents=[
                                    TextComponent(text="เตียง", size="xxs", color="#9CA3AF", align="center"),
                                    TextComponent(text=str(sh.get("Beds", "-")), size="sm", weight="bold", color="#111827", align="center"),
                                ]
                            ),
                            BoxComponent(
                                layout="vertical", flex=1, spacing="xs",
                                contents=[
                                    TextComponent(text="ห้องน้ำ", size="xxs", color="#9CA3AF", align="center"),
                                    TextComponent(text=str(sh.get("Toilets", "-")), size="sm", weight="bold", color="#111827", align="center"),
                                ]
                            ),
                            BoxComponent(
                                layout="vertical", flex=1, spacing="xs",
                                contents=[
                                    TextComponent(text="ที่จอดรถ", size="xxs", color="#9CA3AF", align="center"),
                                    TextComponent(text=str(sh.get("Parking", "-")), size="sm", weight="bold", color="#111827", align="center"),
                                ]
                            ),
                        ]
                    ),
                    ButtonComponent(
                        action=URIAction(
                            label="นำทางไปศูนย์พักพิง",
                            uri=f"https://www.google.com/maps/search/?api=1&query={sh.get('Latitude')},{sh.get('Longitude')}"
                        ),
                        style="secondary", color="#F9FAFB", height="sm", margin="sm"
                    )
                ]
            )
            shelters_box.contents.append(card)

    hero = None
    hero_url = hero_image_url("shelter_banner.jpg")
    if hero_url:
        hero = ImageComponent(
            url=hero_url,
            size="full",
            aspect_ratio="20:13",
            aspect_mode="cover",
        )

    bubble = BubbleContainer(
        hero=hero,
        body=BoxComponent(
            layout="vertical",
            contents=[
                header,
                SeparatorComponent(margin="md", color="#E5E7EB"),
                shelters_box
            ]
        )
    )
    return FlexSendMessage(alt_text="ข้อมูลศูนย์พักพิง", contents=bubble)


# =============================================================================
# SECTION 13: GREETING & RESPONSE HANDLERS
# =============================================================================

def is_greeting(text: str) -> bool:
    if not text:
        return False
    clean = text.strip().lower().strip("!.,😊🙏👋 ")
    greetings = ["สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "hello", "hi", "hey",
                "good morning", "good afternoon", "good evening", "menu", "เมนู", "เริ่ม", "start"]
    return any(clean.startswith(g.lower()) or g.lower() in clean for g in greetings)


def get_greeting_message(user_name="คุณ"):
    now = get_bangkok_time()
    time_greeting = "สวัสดี"
    if 5 <= now.hour < 10:
        time_greeting = "อรุณสวัสดิ์"
    
    text = (
        f"{time_greeting} คุณ {user_name}\n"
        "ผมคือ FLOODCARE AI ผู้ช่วยอัจฉริยะด้านภัยน้ำท่วมและเหตุฉุกเฉินครับ\n\n"
        "รายการบริการที่ผมช่วยคุณได้:\n"
        "1. เบอร์โทรฉุกเฉินและสายด่วน\n"
        "2. SOS แจ้งเหตุขอความช่วยเหลือกู้ภัย\n"
        "3. ค้นหาศูนย์พักพิงและจุดอพยพ\n"
        "4. ตรวจสอบระดับน้ำและสภาพอากาศ\n"
        "5. แจ้งความต้องการสิ่งของบรรเทาทุกข์\n"
        "6. คู่มือเตรียมความพร้อมและปฐมพยาบาล\n"
        "7. สอบถามข้อมูลภัยพิบัติผ่านระบบ AI\n\n"
        "ยินดีช่วยเหลือคุณตลอด 24 ชั่วโมงครับ"
    )
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


def build_sos_summary_text(data: dict) -> str:
    lat = data.get("latitude", "0")
    lon = data.get("longitude", "0")
    maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    priority_label = data.get("priority_label", "🟢 NORMAL")
    
    return (
        "📋 สรุปข้อมูลแจ้งเหตุ\n\n"
        f"📍 พิกัด: {maps_link}\n"
        f"👥 กลุ่ม: {', '.join(data.get('group_types', []))}\n"
        f"🌊 สถานการณ์: {data.get('urgency_level', 'ต่ำ')}\n"
        f"📊 ระดับความเร่งด่วน: {priority_label}\n\n"
        f"ยืนยันการส่งข้อมูลแจ้งกู้ภัย?"
    )


def build_needs_summary_text(data: dict) -> str:
    lat = data.get("latitude", "0")
    lon = data.get("longitude", "0")
    maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    
    return (
        "📋 สรุปความต้องการ\n\n"
        f"📍 พิกัด: {maps_link}\n"
        f"📦 หมวดหมู่: {', '.join(data.get('categories', []))}\n"
        f"📝 รายละเอียด: {data.get('details', '-')}\n"
        f"⏳ ความเร่งด่วน: {data.get('urgency', '-')}\n\n"
        f"ยืนยันการส่งข้อมูล?"
    )


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
                        Logger.info("WaterLevelRefresh", f"Updated {len(stations)} stations in Water_Levels sheet")
                    else:
                        Logger.error("WaterLevelRefresh", "Failed to write stations to sheet")
                else:
                    Logger.error("WaterLevelRefresh", "ThaiWater API returned no stations — sheet left unchanged")
            except Exception as e:
                Logger.error("WaterLevelRefresh", f"Loop error: {e}")
            time.sleep(600)

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()

    water_thread = threading.Thread(target=water_level_refresh_loop, daemon=True)
    water_thread.start()

    Logger.info("System", "Background cleanup + water-level refresh started")

start_background_tasks()
Logger.info("System", "FLOODCARE AI Bot Config v2.5.1 Initialized Successfully")
