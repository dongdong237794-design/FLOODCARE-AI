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
        LocationAction, MessageAction, BubbleStyle, BlockStyle
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
SOS_LIFF_ID = os.environ.get("SOS_LIFF_ID", "")
SOS_LIFF_URL = os.environ.get("SOS_LIFF_URL", "")
NEED_LIFF_ID = os.environ.get("NEED_LIFF_ID", "")
NEED_LIFF_URL = os.environ.get("NEED_LIFF_URL", "")
REGISTER_LIFF_ID = os.environ.get("REGISTER_LIFF_ID", "")
REGISTER_LIFF_URL = os.environ.get("REGISTER_LIFF_URL", "")

WATER_LEVEL_SOURCE_URL = os.environ.get(
    "WATER_LEVEL_SOURCE_URL", "https://www.thaiwater.net/water/wl"
)
SNAKE_BITE_INFO_URL = "https://www.rama.mahidol.ac.th/poisoncenter/th"
SNAKE_BITE_HOTLINE = "1367"

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")

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


# =============================================================================
# SECTION 5: INTENT CLASSIFICATION SYSTEM
# =============================================================================

class IntentClassifier:
    """Rule-based Intent Classifier to reduce API costs"""
    PATTERNS = {
        "EMERGENCY": [
            "ช่วยด้วย", "ช่วยด้วยครับ", "ช่วยด้วยค่ะ", "จะตาย", "จมแล้ว", "ไฟดูด", "ไฟฟ้าดูด",
            "หายใจไม่ออก", "เป็นลม", "บาดเจ็บสาหัส", "ด่วนที่สุด", "วิกฤต", "ช่วยชีวิต", 
            "กำลังจม", "ติดอยู่", "ขอความช่วยเหลือด่วน", "น้ำเข้าบ้าน", "น้ำกำลังเข้าบ้าน", 
            "ติดอยู่บนหลังคา", "ติดหลังคา", "น้ำเชี่ยว", "คนจมน้ำ", "รถติดกลางน้ำ", "น้ำเข้ารถ"
        ],
        "SOS": [
            "sos", "🆘", "ขอความช่วยเหลือ", "แจ้งเหตุ", "กู้ภัย", "ติดน้ำท่วม", "จมน้ำ", "ช่วย"
        ],
        "SNAKE_BITE": [
            "งูกัด", "ถูกงูกัด", "โดนงูกัด", "งูกัดครับ", "งูกัดค่ะ", "ถูกงู", "โดนงู", "งูฉก", "ถูกสัตว์มีพิษกัด"
        ],
        "GREETING": [
            "สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "ดีจ้า", "ดีคับ", "hello", "hi", "hey", 
            "good morning", "good afternoon", "good evening", "เริ่ม", "start", "menu", "เมนู"
        ],
        "NEEDS": [
            "ขอของ", "ต้องการ", "ขาดแคลน", "ไม่มีอาหาร", "ไม่มีน้ำ", "ของบริจาค", 
            "ขอความช่วยเหลือเรื่องของ", "need help", "แจ้งความต้องการ", "ขอน้ำดื่ม", "ขอยา", "ขอเสื้อผ้า"
        ],
        "SHELTER": [
            "ศูนย์พักพิง", "ที่พัก", "อพยพ", "หลบภัย", "หลบน้ำ", "ที่พักชั่วคราว", 
            "evacuation center", "shelter", "ไปไหนดี", "พักที่ไหน", "ห้างน้ำท่วม"
        ],
        "WATER_LEVEL": [
            "ระดับน้ำ", "น้ำสูง", "เช็คน้ำ", "ตรวจน้ำ", "water level", 
            "flood level", "น้ำขึ้น", "น้ำลด", "สถานการณ์น้ำ", "check water"
        ],
        "WEATHER": [
            "สภาพอากาศ", "พยากรณ์อากาศ", "ฝนตก", "ฝน", "อากาศ", "weather", 
            "forecast", "rain", " raining", "จะฝนตกไหม", "เช็คฝน", "check weather"
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
            "ใช้งานอย่างไร", "วิธีใช้", "what can you do", "capabilities", "คุณคือใคร", "คุณทำอะไรได้"
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
    "คุณคือ FLOODCARE AI น้องบอทผู้ช่วยอัจฉริยะด้านภัยน้ำท่วมและเหตุฉุกเฉินในประเทศไทย\n"
    "บุคลิกภาพ: เป็นกันเอง อบอุ่น สุภาพ และพร้อมช่วยเหลือผู้ใช้เหมือนเพื่อนแท้ คุยเข้าใจง่าย สบายตา\n"
    "ใช้สรรพนามแทนตนเองว่า 'น้องบอท' หรือ 'ผม' เสนอตัวช่วยเสมอ ห้ามแทนตัวเองด้วยคำว่า 'ฉัน' หรือคุยเป็นทางการแบบบอทหุ่นยนต์เด็ดขาด\n\n"
    "ข้อจำกัดด้านขอบเขตการตอบคำถามอย่างเข้มงวด (STRICT SCOPE LOCK):\n"
    "1. ตอบเฉพาะคำถามที่เกี่ยวข้องกับ: 1) อุทกภัย/ภัยพิบัติน้ำท่วม 2) ความปลอดภัย/การกู้ภัย/เบอร์ฉุกเฉิน 3) สุขภาพกาย/อาการเจ็บป่วยจากน้ำท่วม/การปฐมพยาบาล 4) สุขภาพจิต/ความเครียดของผู้ประสบภัย เท่านั้น!\n"
    "2. หากมีคำถามใดๆ ที่อยู่นอกเหนือจากขอบเขตความปลอดภัยและน้ำท่วมด้านบนนี้ (เช่น กีฬา บันเทิง เกม ข่าวสังคม การทำอาหารทั่วไป แฟชั่น) คุณต้องปฏิเสธอย่างมีมารยาทและอบอุ่นทันที เช่น:\n"
    "   'เรื่องนี้ผมอาจจะยังไม่เชี่ยวชาญเท่าไหร่ครับ น้องบอทอยากเน้นช่วยพี่ๆ เรื่องน้ำท่วม ความปลอดภัย และการดูแลสุขภาพในช่วงนี้มากกว่าครับ มีอะไรเกี่ยวกับระดับน้ำหรืออาการป่วยไม่สบายให้ช่วยดูแลไหมครับ?'\n\n"
    "กฎการตอบคำถามเพื่อความงาม ความกระชับ และความเป็นระเบียบ (CRITICAL FORMATTING RULES):\n"
    "1. **เน้นตอบให้สั้นและกระชับที่สุดและต้องอธิบายและข้อมูลมาจกแหล่งอ้างอิง\n"
    "2. **ระบุแหล่งที่มา (Citation) สั้นๆ ในวงเล็บปิดท้ายข้อความเสมอ** เช่น (ที่มา: กรมอุตุนิยมวิทยา) หรือ (ข้อมูลจาก: ปภ. 1784) โดยห้ามละเลยการระบุแหล่งที่มาเด็ดขาดเพื่อให้ข้อมูลมีความน่าเชื่อถือ\n"
    "3. ห้ามใช้เครื่องหมายดอกจันสองตัว (**) หรือดอกจันตัวเดียว (*) ในข้อความอย่างเด็ดขาด เพราะทำให้ข้อความรกบนระบบ LINE ให้เว้นบรรทัดและเขียนข้อความให้อ่านง่ายแทน\n"
    "4. ทุกข้อความคำตอบต้องจบอย่างบริบูรณ์สมบูรณ์ ห้ามจบกลางประโยคเด็ดขาด\n"
    "5. หากมีลิงก์อ้างอิงให้จัดเก็บไว้ในโครงสร้างส่วนท้ายของการ์ดหรือแสดงผลเป็นรูปแบบปุ่มกดให้เรียบร้อยสวยงาม ไม่เขียนลิงก์ยาวเปลือยในตัวข้อความหลัก\n"
    "6. หากคำถามของผู้ใช้สื่อถึงความเครียด ความกลัว หรือความเดือดร้อน (เช่น ถามเรื่องอาการเจ็บป่วยของตนเอง คนในครอบครัว หรือน้ำท่วมบ้านตัวเอง) ให้เปิดประโยคแรกด้วยคำรับรู้ความรู้สึกสั้นๆ ไม่เกิน 1 บรรทัด ก่อนให้ข้อมูล เช่น 'เข้าใจว่าตอนนี้คงเป็นห่วงมากเลยนะครับ' แล้วจึงตอบข้อมูลที่เป็นประโยชน์ต่อทันที ห้ามใส่คำปลอบใจซ้ำหลายประโยคหรือทำให้คำตอบยาวเกินไป"
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


def ask_gemini(prompt: str, max_tokens: int = 8192) -> str:
    """
    Optimized Gemini API call.
    - Uses full token capacity (8192) to avoid truncation issues.
    """
    start_time = time.time()
    if not init_gemini():
        return "⚠️ ขออภัยครับ ระบบ AI ไม่พร้อมใช้งานชั่วคราว หากอยู่ในอันตรายเร่งด่วน โทร ปภ. 1784 ได้ทันทีครับ"
    
    cache_key = f"gemini:{hashlib.md5(prompt.encode()).hexdigest()}"
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
                system_instruction=FLOODCARE_SYSTEM_INSTRUCTION,
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
        "กฎในการตอบเพื่อความปลอดภัยและกะทัดรัด:\n"
        "1. ห้ามใช้เครื่องหมายดอกจันเดี่ยวหรือสองชั้น (*) ในข้อความอย่างเด็ดขาด\n"
        "2. เขียนข้อความให้อ่านง่าย สั้นและตรงประเด็นที่สุด (ความยาวห้ามเกิน 2-3 บรรทัด หรือ 80 คำ)\n"
        "3. **ต้องระบุแหล่งที่มาอย่างกระชับในวงเล็บท้ายประโยค** เช่น (ที่มา: กรมอุตุนิยมวิทยา) หรือ (ข้อมูลจาก: ปภ.) เพื่ออ้างอิงแหล่งข้อมูล\n"
        "4. จบข้อความอย่างสมบูรณ์แบบ ห้ามหยุดประโยคกลางคัน\n"
        "5. ลิงก์ URL อ้างอิงทั้งหมดจะถูกแยกไปแสดงด้านล่าง ไม่ต้องระบุลิงก์ยาวในย่อหน้าหลัก"
    )

    try:
        response = gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=(
                    "You are FLOODCARE AI. Always respond in Thai. Make sure to generate "
                    "extremely short (max 2-3 lines), concise, highly readable, complete Thai responses without any asterisks. "
                    "Always include a brief source citation in parentheses, e.g., (ที่มา: ...). "
                    "Use Google Search tool. Under no circumstances should you truncate or leave the response cut off."
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
                "users": ["user_id", "first_name", "last_name", "phone", "province", 
                         "district", "sub_district", "gps_lat", "gps_lon", 
                         "member_count", "emergency_contact", "sms_enabled", 
                         "consent_pdpa", "register_date", "status"],
                "sos_requests": ["case_id", "user_id", "timestamp", "latitude", "longitude",
                                "water_level_status", "victim_count", "vulnerable_groups",
                                "group_types", "urgency_level", "details", "photo_url",
                                "priority", "status", "responder_name", "responder_notes",
                                "accepted_at", "completed_at"],
                "user_needs": ["need_id", "timestamp", "user_id", "latitude", "longitude",
                              "categories", "details", "urgency", "status",
                              "halal_required", "volunteer_name", "delivered_at"],
                "Shelters": ["ShelterID", "Name", "Province", "District", "Latitude",
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
            return True
        except Exception as e:
            Logger.error("Sheets", f"Batch append error: {e}")
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

TMD_SOURCE_URL = "https://www.tmd.go.th"


def get_live_weather_data(lat: float, lon: float) -> dict:
    start = time.time()
    cache_key = f"{round(float(lat), 2)},{round(float(lon), 2)}"

    cached = cache.weather.get(cache_key)
    if cached:
        Logger.perf("Weather", "cache_hit", (time.time() - start) * 1000)
        return cached

    if not TMD_ACCESS_TOKEN or not requests:
        result = {"ok": False, "error": "ไม่ได้ตั้งค่า TMD_ACCESS_TOKEN", "source": "TMD"}
        cache.weather.set(cache_key, result)
        return result

    try:
        url = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at"
        params = {"lat": lat, "lon": lon, "duration": 1, "fields": "tc,rh,cond,ws10m"}
        headers = {"accept": "application/json", "authorization": f"Bearer {TMD_ACCESS_TOKEN}"}

        resp = requests.get(url, headers=headers, params=params, timeout=8)

        if resp.status_code == 429:
            result = {"ok": False, "error": "ระบบ TMD หนาแน่น กรุณาลองใหม่ในอีก 1 นาที", "source": "TMD"}
            return result

        resp.raise_for_status()
        data = resp.json()

        forecasts = data.get("WeatherForecasts", [])
        if not forecasts:
            result = {"ok": False, "error": "ไม่พบข้อมูลพยากรณ์สำหรับพิกัดนี้", "source": "TMD"}
            cache.weather.set(cache_key, result)
            return result

        latest = forecasts[0].get("forecasts", [])[0]
        d = latest.get("data", {})
        code = d.get("cond", 0)

        result = {
            "ok": True,
            "temp": d.get("tc", "-"),
            "rh": d.get("rh", "-"),
            "wind": d.get("ws10m", "-"),
            "desc": WEATHER_CONDITION_MAP.get(code, "ไม่ระบุ"),
            "source": "TMD",
            "error": None,
        }
        cache.weather.set(cache_key, result)

        Logger.perf("Weather", "api_call", (time.time() - start) * 1000)
        return result
    except Exception as e:
        Logger.error("Weather", f"API error: {e}")
        result = {"ok": False, "error": "ไม่สามารถดึงข้อมูลอากาศได้ในขณะนี้", "source": "TMD"}
        cache.weather.set(cache_key, result)
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
    Directly pulls real-time water levels from ThaiWater V3 API as an automatic fallback
    when Google Sheets is empty.
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

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
        resp = requests.get(THAIWATER_V3_API, headers=headers, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        
        raw_stations = data.get("data", [])
        parsed_stations = []
        
        for item in raw_stations:
            station = item.get("station", {})
            geocode = station.get("geocode", {})
            
            lat_val = geocode.get("lat")
            lon_val = geocode.get("lng") or geocode.get("lon")
            if lat_val is None or lon_val is None:
                continue
                
            wl_val = item.get("water_level")
            bl_val = item.get("ground_level") or item.get("bank_high")
            if bl_val is None:
                bl_val = station.get("bank_high") or "-"
                
            situation = item.get("water_situation", {}).get("name", "ปกติ")
            trend = item.get("water_trend", {}).get("name", "คงที่")
            measure_time = item.get("datetime", "-")
            
            # Formats exactly matching the sheet headers schema to preserve code portability
            parsed_stations.append({
                "StationCode": station.get("code", ""),
                "Name": station.get("name", {}).get("th", "ไม่ระบุ"),
                "River": station.get("river", {}).get("th", "ไม่ระบุ"),
                "Location": geocode.get("province", {}).get("name", {}).get("th", ""),
                "Lat": float(lat_val),
                "Lon": float(lon_val),
                "WaterLevel": wl_val if wl_val is not None else "-",
                "BankLevel": bl_val,
                "Situation": situation,
                "Trend": trend,
                "Time": measure_time
            })
            
        cache.water.set(cache_key, parsed_stations, ttl=900)  # Cached for 15 minutes
        Logger.perf("WaterLevelAPI", "fetched_live", (time.time() - start_time) * 1000, {"count": len(parsed_stations)})
        return parsed_stations
    except Exception as e:
        Logger.error("WaterLevelAPI", f"Failed to pull live water level telemetry from API: {e}")
        return []


def assess_water_level_status(wl_value, bl_value=None, situation=None, lang="TH"):
    """
    Assess water level status.
    Directly extracts the situation tag string and maps it to the custom specifications.
    Uses .copy() to secure from memory mutation corruption across array rendering.
    - 🟧 น้อยวิกฤต: #D67B27
    - 🟨 น้อย: #FFC000 (UI Specs: Background: #FFF3CD, Text: #856404)
    - 🟩 ปกติ: #00B050 (UI Specs: Background: #D4EDDA, Text: #155724)
    - 🟦 มาก: #0000FF (UI Specs: Background: #CCE5FF, Text: #004085)
    - 🟥 ล้นตลิ่ง: #FF0000 (UI Specs: Background: #F8D7DA, Text: #721C24)
    """
    sit_str = str(situation or "").strip()

    if "ล้นตลิ่ง" in sit_str or ("วิกฤต" in sit_str and ("สูง" in sit_str or "มาก" in sit_str or "ล้น" in sit_str)):
        status_key = "ล้นตลิ่ง"
    elif "น้อยวิกฤต" in sit_str or ("วิกฤต" in sit_str and ("น้อย" in sit_str or "ต่ำ" in sit_str or "แห้ง" in sit_str)):
        status_key = "น้อยวิกฤต"
    elif "มาก" in sit_str:
        status_key = "มาก"
    elif "น้อย" in sit_str:
        status_key = "น้อย"
    elif "ปกติ" in sit_str:
        status_key = "ปกติ"
    else:
        try:
            wl = float(wl_value) if wl_value not in [None, "-", ""] else 0
            bl = float(bl_value) if bl_value not in [None, "-", ""] else 0
            if bl > 0:
                ratio = wl / bl
                if wl >= bl:
                    status_key = "ล้นตลิ่ง"
                elif ratio >= 0.70:
                    status_key = "มาก"
                elif ratio >= 0.30:
                    status_key = "ปกติ"
                elif ratio >= 0.10:
                    status_key = "น้อย"
                else:
                    status_key = "น้อยวิกฤต"
            else:
                status_key = "ปกติ"
        except (ValueError, TypeError):
            status_key = "ปกติ"

    status_map = {
        "น้อยวิกฤต": {
            "status": "น้อยวิกฤต",
            "bg": "#F8E9DC",
            "text": "#D67B27",
            "advice": "เฝ้าระวังภัยแล้ง/น้ำลดขีดอันตราย",
            "label_pill": "น้อยวิกฤต"
        },
        "น้อย": {
            "status": "น้อย",
            "bg": "#FFF3CD",
            "text": "#856404",
            "advice": "ระดับน้ำน้อย",
            "label_pill": "น้อย"
        },
        "ปกติ": {
            "status": "ปกติ",
            "bg": "#D4EDDA",
            "text": "#155724",
            "advice": "ระดับน้ำปกติ ปลอดภัยดี",
            "label_pill": "ปกติ"
        },
        "มาก": {
            "status": "มาก",
            "bg": "#CCE5FF",
            "text": "#004085",
            "advice": "ค่อนข้างสูง",
            "label_pill": "มาก"
        },
        "ล้นตลิ่ง": {
            "status": "วิกฤต",
            "bg": "#F8D7DA",
            "text": "#721C24",
            "advice": "ระดับน้ำล้นตลิ่ง",
            "label_pill": "วิกฤต"
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

def build_sos_form_flex(user_name="คุณ", lang="TH"):
    liff_url = SOS_LIFF_URL or "https://liff.line.me/"
    return FlexSendMessage(
        alt_text="🚨 แจ้งเหตุฉุกเฉิน SOS",
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#C2452F")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="🚨 แจ้งเหตุฉุกเฉิน SOS", weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=f"สวัสดีครับ คุณ{user_name}", size="sm", color="#374151"),
                    TextComponent(text="กรุณากรอกข้อมูลเพื่อส่งตำแหน่งและรายละเอียดให้ทีมกู้ภัยช่วยเหลือทันที", size="xs", color="#6B7280", wrap=True),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(
                        action=URIAction(label="📋 เปิดแบบฟอร์ม SOS", uri=liff_url),
                        style="primary", color="#C2452F", height="lg"
                    )
                ]
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="ข้อมูลจะถูกส่งไปยังทีมกู้ภัยทันทีเพื่อความช่วยเหลืออย่างเร่งด่วน", size="xxs", color="#9CA3AF", align="center")]
            )
        )
    )


def build_need_form_flex(user_name="คุณ", lang="TH"):
    liff_url = NEED_LIFF_URL or "https://liff.line.me/"
    return FlexSendMessage(
        alt_text="📦 แจ้งความต้องการสิ่งของ",
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#2F6F8F")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="📦 ขอความช่วยเหลือเรื่องสิ่งของ", weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=f"สวัสดีครับ คุณ{user_name}", size="sm", color="#374151"),
                    TextComponent(text="กรุณากรอกประเภทสิ่งของที่ท่านขาดแคลนเพื่อให้อาสาสมัครจัดเตรียมสิ่งของช่วยเหลือได้ถูกต้อง", size="xs", color="#6B7280", wrap=True),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(
                        action=URIAction(label="📋 ขอความช่วยเหลือเรื่องสิ่งของ", uri=liff_url),
                        style="primary", color="#2F6F8F", height="lg"
                    )
                ]
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="ข้อมูลจะอัปเดตตรงไปยังระบบฐานข้อมูลอาสาสมัครจัดส่ง", size="xxs", color="#9CA3AF", align="center")]
            )
        )
    )


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


def build_weather_flex(lat, lon, weather_data: dict, timestamp: str, lang="TH"):
    if not weather_data.get("ok"):
        body_contents = [
            TextComponent(text="🌦️ สภาพอากาศ", weight="bold", size="lg", color="#1F2937"),
            SeparatorComponent(margin="md"),
            TextComponent(
                text=f"⚠️ {weather_data.get('error', 'ไม่สามารถดึงข้อมูลอากาศได้ในขณะนี้')}",
                size="sm", color="#C2452F", wrap=True, margin="md"
            ),
        ]
    else:
        temp = weather_data["temp"]
        desc = weather_data["desc"]
        rh = weather_data["rh"]
        wind = weather_data["wind"]

        rows = [
            ("🌡️", "อุณหภูมิ", f"{temp} °C"),
            ("🌧️", "สภาพอากาศ", desc),
            ("💧", "ความชื้น", f"{rh} %"),
            ("🍃", "ความเร็วลม", f"{wind} m/s"),
        ]
        body_contents = [
            TextComponent(text="🌦️ รายงานสภาพอากาศปัจจุบัน", weight="bold", size="lg", color="#1F2937"),
            TextComponent(text=f"📍 {lat:.4f}, {lon:.4f}  •  🕒 {timestamp}", size="xxs", color="#9CA3AF", wrap=True),
            SeparatorComponent(margin="md"),
        ]
        for icon, label, value in rows:
            body_contents.append(
                BoxComponent(
                    layout="horizontal", margin="md",
                    contents=[
                        TextComponent(text=f"{icon} {label}", size="sm", color="#6B7280", flex=2),
                        TextComponent(text=value, size="sm", weight="bold", color="#1F2937", flex=2, align="end"),
                    ]
                )
            )
        body_contents.append(
            TextComponent(
                text="⚠️ ข้อมูลพยากรณ์เบื้องต้น โปรดสังเกตท้องฟ้าจริงประกอบการตัดสินใจ",
                size="xxs", color="#9CA3AF", wrap=True, margin="lg"
            )
        )

    return FlexSendMessage(
        alt_text="🌦️ รายงานสภาพอากาศ",
        contents=BubbleContainer(
            body=BoxComponent(layout="vertical", contents=body_contents),
            footer=BoxComponent(
                layout="vertical",
                contents=[
                    ButtonComponent(
                        action=URIAction(label="🔗 ดูพยากรณ์อากาศเต็มรูปแบบ (กรมอุตุฯ)", uri=TMD_SOURCE_URL),
                        style="secondary", color="#F3F4F6", height="sm"
                    ),
                    TextComponent(
                        text="ข้อมูลอ้างอิง: กรมอุตุนิยมวิทยา (TMD Open Data API) - tmd.go.th",
                        size="xxs", color="#9CA3AF", align="center", margin="sm", wrap=True
                    )
                ]
            )
        )
    )


def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, lang="TH"):
    """
    Modern Minimalist Water Level Report using Soft Pastel Status Pills & Spacing.
    Fully compliant with ThaiWater Hex specification.
    """
    header = BoxComponent(
        layout="vertical",
        spacing="xs",
        contents=[
            TextComponent(text="🌊 รายงานระดับน้ำจากสถานีใกล้คุณ", weight="bold", size="md", color="#1F2937"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f}", size="xs", color="#4B5563"),
            TextComponent(text=f"🕒 อัปเดตวันนี้ {timestamp}", size="xs", color="#9CA3AF")
        ]
    )
    
    stations_box = BoxComponent(layout="vertical", spacing="xl", margin="lg", contents=[])
    
    if not stations:
        stations_box.contents.append(
            TextComponent(text="⚠️ ไม่พบสถานีวัดระดับน้ำในพื้นที่ใกล้คุณ", size="sm", color="#EF4444", align="center")
        )
    else:
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
            
            # Format text label next to the Status Pill
            lbl_pill = assessment.get("label_pill", "ปกติ")
            status_desc = assessment.get("advice", "ระดับน้ำปกติ ปลอดภัยดี")
            
            # Safe parsing for diff calculation
            diff_text_formatted = "-"
            diff_prefix = "ต่ำกว่าตลิ่ง: "
            if wl_val != "-" and bl_val != "-":
                try:
                    wl_f = float(wl_val)
                    bl_f = float(bl_val)
                    diff_val = bl_f - wl_f
                    if diff_val < 0:
                        diff_prefix = "สูงกว่าตลิ่ง: "
                        diff_text_formatted = f"{abs(diff_val):.2f} ม."
                    else:
                        diff_text_formatted = f"{diff_val:.2f} ม."
                except Exception:
                    pass

            card = BoxComponent(
                layout="vertical",
                spacing="sm",
                contents=[
                    # Station Name & Distance (Clean spacing)
                    TextComponent(text=f"{st['stationName']} (ห่าง {dist:.2f} กม.)", 
                                 weight="bold", size="sm", color="#111827"),
                    
                    # Status Pill Layout (Rounded Pill + Description)
                    BoxComponent(
                        layout="horizontal",
                        spacing="md",
                        contents=[
                            # Status Pill Capsule
                            BoxComponent(
                                layout="vertical",
                                background_color=assessment.get("bg", "#E5E7EB"),
                                corner_radius="xxl",
                                padding_start="md",
                                padding_end="md",
                                padding_top="xs",
                                padding_bottom="xs",
                                flex=0,
                                contents=[
                                    TextComponent(
                                        text=lbl_pill,
                                        size="xs",
                                        color=assessment.get("text", "#1F2937"),
                                        weight="bold",
                                        align="center"
                                    )
                                ]
                            ),
                            # Advice description next to the pill
                            TextComponent(
                                text=status_desc,
                                size="xs",
                                color="#4B5563",
                                gravity="center"
                            )
                        ]
                    ),
                    
                    # Measurement Values with Bold Highlight
                    BoxComponent(
                        layout="vertical",
                        spacing="xs",  # FIXED: Changed from "xxs" to "xs" to comply with LINE Flex API
                        margin="xs",
                        contents=[
                            TextComponent(
                                text=f"ระดับน้ำ: {wl_val} ม. | ตลิ่ง: {bl_val} ม.",
                                size="xs",
                                color="#4B5563"
                            ),
                            # Highlights the distance difference to bank
                            BoxComponent(
                                layout="horizontal",
                                contents=[
                                    TextComponent(text=diff_prefix, size="xs", color="#4B5563", flex=0),
                                    TextComponent(text=diff_text_formatted, size="xs", weight="bold", color="#111827", flex=1)
                                ]
                            )
                        ]
                    )
                ]
            )
            stations_box.contents.append(card)
    
    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=[
                header,
                SeparatorComponent(margin="md", color="#E5E7EB"),
                stations_box
            ]
        ),
        footer=BoxComponent(
            layout="vertical",
            spacing="sm",
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


def build_shelter_flex_message(user_lat, user_lon, shelters, lang="TH"):
    """
    Minimalist Shelter (Evacuation Center) Report card.
    Mirrors the water-level card's visual language (status pill + spacing).
    """
    header = BoxComponent(
        layout="vertical",
        spacing="xs",
        contents=[
            TextComponent(text="🏠 ศูนย์พักพิงใกล้คุณ", weight="bold", size="md", color="#1F2937"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f}", size="xs", color="#4B5563"),
            TextComponent(text=f"🕒 อัปเดตวันนี้ {get_bangkok_time().strftime('%H:%M')} น.",
                          size="xs", color="#9CA3AF")
        ]
    )

    shelters_box = BoxComponent(layout="vertical", spacing="xl", margin="lg", contents=[])

    if not shelters:
        shelters_box.contents.append(
            TextComponent(text="⚠️ ไม่พบศูนย์พักพิงในพื้นที่ใกล้คุณ", size="sm", color="#EF4444", align="center")
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
                contents=[
                    # Name & Distance
                    BoxComponent(
                        layout="horizontal",
                        contents=[
                            TextComponent(text=sh.get("Name", "ไม่ระบุชื่อ"), weight="bold",
                                        size="sm", color="#111827", flex=1, wrap=True),
                            TextComponent(text=f"{dist:.1f} กม.", size="xs", color="#6B7280",
                                        align="end", flex=0)
                        ]
                    ),
                    TextComponent(
                        text=f"{sh.get('District', '')} {sh.get('Province', '')}".strip(),
                        size="xs", color="#6B7280"
                    ),
                    # Status Pill Layout
                    BoxComponent(
                        layout="horizontal",
                        spacing="md",
                        contents=[
                            BoxComponent(
                                layout="vertical",
                                background_color=assessment.get("bg", "#E5E7EB"),
                                corner_radius="xxl",
                                padding_start="md",
                                padding_end="md",
                                padding_top="xs",
                                padding_bottom="xs",
                                flex=0,
                                contents=[
                                    TextComponent(
                                        text=assessment.get("label", status_key),
                                        size="xs",
                                        color=assessment.get("text", "#1F2937"),
                                        weight="bold",
                                        align="center"
                                    )
                                ]
                            ),
                            TextComponent(
                                text=capacity_text,
                                size="xs",
                                color="#4B5563",
                                gravity="center"
                            )
                        ]
                    ),
                    TextComponent(
                        text=f"🛏️ {sh.get('Beds', '-')} | 🚻 {sh.get('Toilets', '-')} | 🅿️ {sh.get('Parking', '-')}",
                        size="xs", color="#4B5563", margin="xs"
                    ),
                    ButtonComponent(
                        action=URIAction(
                            label="🧭 นำทางไปศูนย์พักพิง",
                            uri=f"https://www.google.com/maps/search/?api=1&query={sh.get('Latitude')},{sh.get('Longitude')}"
                        ),
                        style="secondary", color="#F3F4F6", height="sm", margin="sm"
                    )
                ]
            )
            shelters_box.contents.append(card)

    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=[
                header,
                SeparatorComponent(margin="md", color="#E5E7EB"),
                shelters_box
            ]
        )
    )
    return FlexSendMessage(alt_text="ศูนย์พักพิงใกล้คุณ", contents=bubble)


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
        "ผมคือ FLOODCARE AI\n"
        "น้องบอทผู้ช่วยอัจฉริยะสำหรับติดตามสถานการณ์น้ำ แจ้งเหตุฉุกเฉิน และช่วยเหลือผู้ประสบภัยครับ\n\n"
        "🔍 ผมช่วยคุณได้ดังนี้ครับ:\n"
        "1. 📞 เบอร์โทรฉุกเฉิน\n"
        "2. 🚨 SOS แจ้งเหตุกู้ภัย\n"
        "3. 🏠 ค้นหาศูนย์อพยพ\n"
        "4. 🌊 ตรวจสอบระดับน้ำจริง\n"
        "5. 📦 ขอความช่วยเหลือสิ่งของ\n"
        "6. 🤖 สอบถามข้อมูลภัยพิบัติ สภาพอากาศ หรืออาการเจ็บป่วย\n\n"
        "ยินดีช่วยเหลือเคียงข้างคุณตลอด 24 ชั่วโมงครับ 💧"
    )
    return TextSendMessage(text=text)


def handle_emergency_response(user_id: str, event=None) -> TextSendMessage:
    emergency_text = (
        "🚨 ตั้งสติไว้ก่อนนะครับ น้องบอทอยู่กับคุณ ทำตามขั้นตอนนี้ทันที:\n\n"
        "1️⃣ ยกเบรกเกอร์ไฟฟ้าทันที\n"
        "2️⃣ ขึ้นที่สูงที่สุดเท่าที่ทำได้\n"
        "3️⃣ โทยแจ้งเจ้าหน้าที่:\n"
        "   📞 ปภ. 1784\n"
        "   📞 สพฉ. 1669\n"
        "   📞 ตำรวจทางหลวง 1193\n\n"
        "⚠️ อย่าตกใจ ประหยัดแบตมือถือ\n"
        "รอความช่วยเหลืออยู่ที่จุดปลอดภัย"
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
    
    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()
    Logger.info("System", "Background cleanup started")

start_background_tasks()
Logger.info("System", "FLOODCARE AI Bot Config v2.5.1 Initialized Successfully")
