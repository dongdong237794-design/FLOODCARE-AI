"""
FLOODCARE AI - Optimized Bot Configuration
============================================
Architecture: Modular | Class-Based State Machine | Intent Classification
Author: Senior Software Architect
Version: 2.0 (Production-Ready)

Key Optimizations:
- Intent Classification: Reduces Gemini API calls by ~80%
- Smart Cache: Multi-layer (Memory LRU > TTL Cache)
- State Machine: Class-based, separated workflows
- Rate Limiting: Per-user request throttling
- Memory Management: Auto-cleanup stale sessions
- Sheets Optimization: Batch writes, connection pooling
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
    import google.generativeai as genai
except ImportError:
    genai = None

try:
    import gspread
except ImportError:
    gspread = None

# supabase ถูกลบออก — ไม่ได้ใช้งานในโปรเจกต์นี้ (dependency เกินจำเป็น)

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

# --- Trusted reference sources (used so safety-critical replies link to a
#     verified source instead of relying purely on AI-generated text) ---
WATER_LEVEL_SOURCE_URL = os.environ.get(
    "WATER_LEVEL_SOURCE_URL", "https://www.thaiwater.net/water/wl"
)  # คลังข้อมูลน้ำแห่งชาติ - สถาบันสารสนเทศทรัพยากรน้ำ (สสน.)
SNAKE_BITE_INFO_URL = "https://www.rama.mahidol.ac.th/poisoncenter/th"
SNAKE_BITE_HOTLINE = "1367"  # สายด่วนศูนย์พิษวิทยารามาธิบดี (24 ชม.)

# --- Staff dashboard (read-only admin view of Sheets data) ---
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
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
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
    """
    Thread-safe LRU Cache with TTL support
    Layer 1: Fastest - In-memory ordered dict
    """
    
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
            # Move to end (most recently used)
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
            # Evict oldest if over capacity
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
    
    def delete(self, key: str):
        with self._lock:
            self._cache.pop(key, None)
    
    def clear(self):
        with self._lock:
            self._cache.clear()
    
    def cleanup_expired(self) -> int:
        """Remove expired entries, return count removed"""
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
    """
    Multi-layer cache manager
    - Layer 1: LRUMemoryCache (fastest, per-process)
    - Layer 2: TTL-based weather/water cache
    """
    
    def __init__(self):
        # General purpose cache
        self.general = LRUMemoryCache(maxsize=512, default_ttl=CACHE_TTL_SECONDS)
        # Weather-specific cache (30 min)
        self.weather = LRUMemoryCache(maxsize=256, default_ttl=1800)
        # Water levels cache (15 min)
        self.water = LRUMemoryCache(maxsize=128, default_ttl=900)
        # User sessions (30 min)
        self.sessions = LRUMemoryCache(maxsize=1024, default_ttl=SESSION_TTL_MINUTES * 60)
        # Sheets data cache (10 min)
        self.sheets = LRUMemoryCache(maxsize=64, default_ttl=600)
    
    def cleanup_all(self) -> dict:
        """Cleanup all expired entries, return stats"""
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


# Global cache manager instance
cache = CacheManager()


# =============================================================================
# SECTION 4: RATE LIMITING & SECURITY
# =============================================================================

class RateLimiter:
    """
    Token bucket rate limiter per user
    - Default: 30 requests per 60 seconds
    - Burst protection with token bucket algorithm
    """
    
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 300  # Cleanup every 5 minutes
        self._last_cleanup = time.time()
    
    def _cleanup(self):
        """Remove old bucket entries"""
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
        """
        Check if user can make a request
        Returns: (allowed, metadata)
        """
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
            
            # Reset window
            if now - bucket["last_reset"] > self._window:
                bucket["tokens"] = self._max_requests - 1
                bucket["last_reset"] = now
                return True, {"remaining": self._max_requests - 1, "limit": self._max_requests}
            
            # Check token
            if bucket["tokens"] <= 0:
                retry_after = int(self._window - (now - bucket["last_reset"]))
                Logger.security("RateLimiter", f"Rate limit exceeded", user_id)
                return False, {"retry_after": retry_after, "limit": self._max_requests}
            
            bucket["tokens"] -= 1
            return True, {"remaining": bucket["tokens"], "limit": self._max_requests}
    
    def get_status(self, user_id: str) -> dict:
        with self._lock:
            bucket = self._buckets.get(user_id)
            if not bucket:
                return {"remaining": self._max_requests, "limit": self._max_requests}
            return {"remaining": max(0, bucket["tokens"]), "limit": self._max_requests}


# Global rate limiter
rate_limiter = RateLimiter(max_requests=RATE_LIMIT_REQUESTS, window_seconds=RATE_LIMIT_WINDOW)


def sanitize_text(text: str, max_length: int = 2000) -> str:
    """Sanitize user input — strip non-printable control chars and enforce length limit.

    หมายเหตุ: ระบบนี้ไม่ได้คุยกับ SQL database โดยตรง
    จึงไม่จำเป็นต้องกรอง SQL syntax (เช่น --, @@)
    การกรองนั้นอาจตัดข้อความภาษาไทยจริงๆ ออกไปโดยไม่ตั้งใจ
    """
    if not text:
        return ""
    # Remove control characters except newline/tab
    sanitized = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or (ch.isprintable() and ord(ch) >= 32)
    )
    # Limit length
    return sanitized[:max_length]


def hash_user_id(user_id: str) -> str:
    """Hash user_id for logging without exposing PII"""
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


# =============================================================================
# SECTION 5: INTENT CLASSIFICATION SYSTEM
# =============================================================================

class IntentClassifier:
    """
    Rule-based Intent Classifier
    Purpose: Filter messages BEFORE sending to Gemini API
    Reduces token usage by ~80% for common patterns
    
    Intents:
    - GREETING: ทักทาย, สวัสดี
    - HELP: ถามว่าบอททำอะไรได้บ้าง / วิธีใช้
    - SOS: ขอความช่วยเหลือฉุกเฉิน
    - NEEDS: ขอสิ่งของ, ความต้องการ
    - EMERGENCY: ช่วยด้วย, อันตรายถึงชีวิต
    - SNAKE_BITE: ถูกงูกัด (ตอบด้วยข้อมูลปฐมพยาบาลที่ตรวจสอบแล้ว ไม่ใช่ AI freeform)
    - SHELTER: หาศูนย์พักพิง
    - WATER_LEVEL: เช็คระดับน้ำ
    - WEATHER: เช็คสภาพอากาศ
    - CONTACT: เบอร์โทรฉุกเฉิน
    - LANGUAGE: เปลี่ยนภาษา
    - CANCEL: ยกเลิก
    - AI_QUERY: ส่งต่อ Gemini (เฉพาะคำถามทั่วไปที่ไม่ตรงกับ intent ข้างต้น)
    """
    
    # ⚠️ ลำดับ key ใน dict มีความสำคัญ: classify() วน loop ตามลำดับนี้
    # EMERGENCY และ SOS ต้องมาก่อน GREETING เสมอ
    # คำที่ทับซ้อนกับ EMERGENCY/SOS ถูกเอาออกจาก GREETING แล้ว (เช่น "ช่วยด้วยครับ")
    PATTERNS = {
        "EMERGENCY": [
            "ช่วยด้วย", "ช่วยด้วยครับ", "ช่วยด้วยค่ะ",
            "จะตาย", "จมแล้ว", "ไฟดูด", "ไฟฟ้าดูด",
            "หายใจไม่ออก", "เป็นลม", "บาดเจ็บสาหัส", "ด่วนที่สุด",
            "วิกฤต", "ช่วยชีวิต", "กำลังจม", "ติดอยู่", "ขอความช่วยเหลือด่วน",
            "น้ำเข้าบ้าน", "น้ำกำลังเข้าบ้าน", "ติดอยู่บนหลังคา", "ติดหลังคา",
            "น้ำเชี่ยว", "คนจมน้ำ", "รถติดกลางน้ำ", "น้ำเข้ารถ"
        ],
        "SOS": [
            "sos", "🆘", "ขอความช่วยเหลือ", "แจ้งเหตุ", "กู้ภัย",
            "ติดน้ำท่วม", "จมน้ำ", "ช่วย"
        ],
        "SNAKE_BITE": [
            "งูกัด", "ถูกงูกัด", "โดนงูกัด", "งูกัดครับ", "งูกัดค่ะ",
            "ถูกงู", "โดนงู", "งูฉก", "ถูกสัตว์มีพิษกัด"
        ],
        "GREETING": [
            "สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "ดีจ้า", "ดีคับ",
            "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
            "เริ่ม", "start", "menu", "เมนู"
        ],
        "NEEDS": [
            "ขอของ", "ต้องการ", "ขาดแคลน", "ไม่มีอาหาร", "ไม่มีน้ำ",
            "ของบริจาค", "ขอความช่วยเหลือเรื่องของ", "need help",
            "แจ้งความต้องการ", "ขอน้ำดื่ม", "ขอยา", "ขอเสื้อผ้า"
        ],
        "SHELTER": [
            "ศูนย์พักพิง", "ที่พัก", "อพยพ", "หลบภัย", "หลบน้ำ",
            "ที่พักชั่วคราว", "evacuation center", "shelter",
            "ไปไหนดี", "พักที่ไหน", "ห้างน้ำท่วม"
        ],
        "WATER_LEVEL": [
            "ระดับน้ำ", "น้ำท่วม", "น้ำสูง", "เช็คน้ำ", "ตรวจน้ำ",
            "water level", "flood level", "น้ำขึ้น", "น้ำลด",
            "สถานการณ์น้ำ", "check water"
        ],
        "WEATHER": [
            "สภาพอากาศ", "พยากรณ์อากาศ", "ฝนตก", "ฝน", "อากาศ",
            "weather", "forecast", "rain", " raining",
            "จะฝนตกไหม", "เช็คฝน", "check weather"
        ],
        "CONTACT": [
            "เบอร์โทร", "โทรศัพท์", "ติดต่อ", "สายด่วน", "hotline",
            "phone", "contact", "call", "เบอร์ฉุกเฉิน",
            "โทรหาใคร", "เบอร์ ปภ", "1784", "1669"
        ],
        "LANGUAGE": [
            "เปลี่ยนภาษา", "change language", "language", "ภาษา",
            "lang", "english", "ไทย", "japanese", "日本語"
        ],
        "CANCEL": [
            "ยกเลิก", "cancel", "หยุด", "stop", "ออก", "exit",
            "เริ่มใหม่", "restart", "reset"
        ],
        "REGISTRATION": [
            "ลงทะเบียน", "register", "สมัคร", "เข้าร่วม",
            "ลงชื่อ", "ข้อมูลของฉัน", "โปรไฟล์", "profile"
        ],
        "HELP": [
            "ทำอะไรได้บ้าง", "ทำอะไรได้", "มีอะไรบ้าง", "ช่วยอะไรได้บ้าง",
            "ใช้งานยังไง", "ใช้งานอย่างไร", "วิธีใช้", "what can you do",
            "capabilities", "คุณคือใคร", "คุณทำอะไรได้"
        ],
        "FAQ": [
            "คำถามยอดฮิต", "คำถามที่พบบ่อย", "faq", "คำถามทั่วไป",
            "อยากรู้เรื่อง", "บอกข้อมูล", "ค้นหา", "search",
            "น้ำท่วม 2567", "น้ำท่วม 2568", "น้ำท่วมล่าสุด",
            "สถานการณ์น้ำ", "ข่าวน้ำท่วม", "อัพเดทน้ำท่วม",
            "ระดับน้ำล่าสุด", "คาดการณ์น้ำ", "พยากรณ์น้ำ",
        ],
    }
    
    @classmethod
    def classify(cls, text: str) -> Tuple[str, float]:
        """
        Classify user text into intent
        Returns: (intent, confidence)
        
        ⚠️ ลำดับการตรวจ: EMERGENCY → SOS → อื่นๆ
        เพื่อให้แน่ใจว่าคำขอความช่วยเหลือฉุกเฉินไม่ถูกจำแนกผิด
        """
        if not text:
            return ("AI_QUERY", 0.5)
        
        text_lower = text.strip().lower()
        text_clean = text_lower.strip("!.,😊🙏👋🆘 ")
        
        # ✅ ตรวจ EMERGENCY, SOS และ SNAKE_BITE ก่อนเสมอ (priority override)
        # SNAKE_BITE ต้องมาก่อน เพราะเป็นเหตุการณ์ที่ต้องตอบด้วยข้อมูลปฐมพยาบาลที่ถูกต้อง
        # แม่นยำ ไม่ใช่คำตอบสั้นๆ ที่ AI สร้างขึ้นเองซึ่งอาจขาดความครบถ้วน
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
        
        # ตรวจ intent อื่นๆ ตามลำดับปกติ (ยกเว้น EMERGENCY/SOS ที่ตรวจแล้ว)
        for intent, keywords in cls.PATTERNS.items():
            if intent in PRIORITY_INTENTS:
                continue  # ข้ามไป เพราะตรวจแล้วด้านบน
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
        
        # Fallback: ตรวจ emergency keywords กว้างๆ อีกรอบ
        emergency_words = ["ช่วย", "ด่วน", "วิกฤต", "ฉุกเฉิน", "help", "emergency", "urgent"]
        if any(w in text_lower for w in emergency_words):
            return ("EMERGENCY", 0.6)
        
        # Default: ส่งต่อ AI
        return ("AI_QUERY", 0.5)
    
    @classmethod
    def should_use_ai(cls, text: str) -> bool:
        """Determine if message needs Gemini processing"""
        intent, confidence = cls.classify(text)
        # Only use AI for AI_QUERY intent
        return intent == "AI_QUERY"
    
    @classmethod
    def get_quick_response(cls, intent: str) -> Optional[str]:
        """Get pre-defined response for common intents (avoid AI call)"""
        responses = {
            "GREETING": None,  # Use greeting handler
            "SOS": None,       # Use SOS flow
            "NEEDS": None,     # Use needs flow
            "SHELTER": None,   # Use shelter flow
            "WATER_LEVEL": None,  # Use water level flow
            "WEATHER": None,   # Use weather flow
            "CONTACT": None,   # Use contact handler
            "LANGUAGE": None,  # Use language handler
            "CANCEL": "❌ ยกเลิกขั้นตอนเรียบร้อยแล้วครับ คุณสามารถกดใช้งานเมนูหลักใหม่ได้ทันทีครับ",
            "EMERGENCY": None,  # Special emergency handler
            "REGISTRATION": None,  # Use registration flow
            "SNAKE_BITE": None,  # Use dedicated first-aid handler
            "HELP": None,  # Use capabilities/menu handler
            "FAQ": None,  # Use web-search grounded AI handler
        }
        return responses.get(intent)


# =============================================================================
# SECTION 6: STATE MACHINE (Class-Based Workflows)
# =============================================================================

class UserSession:
    """
    Enhanced user session with TTL and metadata
    """
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
        """Update session state and data"""
        if state:
            self.state = state
        if data:
            self.data.update(data)
        self.updated_at = time.time()
        self.message_count += 1
    
    def is_expired(self, ttl_minutes: int = SESSION_TTL_MINUTES) -> bool:
        """Check if session has expired"""
        return time.time() - self.updated_at > ttl_minutes * 60
    
    def reset(self):
        """Reset session to idle state"""
        self.state = "IDLE"
        self.data = {}
        self.updated_at = time.time()
    
    def to_dict(self) -> dict:
        return {
            "user_id": hash_user_id(self.user_id),
            "state": self.state,
            "language": self.language,
            "message_count": self.message_count,
            "last_intent": self.last_intent,
            "age_minutes": round((time.time() - self.updated_at) / 60, 1)
        }


class SessionManager:
    """
    Central session management with auto-cleanup
    """
    
    def __init__(self):
        self._sessions: Dict[str, UserSession] = {}
        self._lock = threading.Lock()
    
    def get(self, user_id: str) -> UserSession:
        """Get or create user session"""
        with self._lock:
            session = self._sessions.get(user_id)
            if session is None or session.is_expired():
                session = UserSession(user_id)
                self._sessions[user_id] = session
            return session
    
    def update(self, user_id: str, state: str = None, data: dict = None):
        """Update user session"""
        session = self.get(user_id)
        session.update(state=state, data=data)
        return session
    
    def reset(self, user_id: str):
        """Reset user session"""
        session = self.get(user_id)
        session.reset()
    
    def delete(self, user_id: str):
        """Delete user session"""
        with self._lock:
            self._sessions.pop(user_id, None)
    
    def cleanup_expired(self) -> int:
        """Remove expired sessions, return count"""
        with self._lock:
            expired = [uid for uid, s in self._sessions.items() if s.is_expired()]
            for uid in expired:
                del self._sessions[uid]
            return len(expired)
    
    def stats(self) -> dict:
        """Get session statistics"""
        with self._lock:
            total = len(self._sessions)
            active = sum(1 for s in self._sessions.values() if not s.is_expired())
            by_state = {}
            for s in self._sessions.values():
                by_state[s.state] = by_state.get(s.state, 0) + 1
            return {
                "total_sessions": total,
                "active_sessions": active,
                "expired_sessions": total - active,
                "by_state": by_state
            }


# Global session manager
sessions = SessionManager()

# Legacy compatibility
USER_STATES: Dict[str, str] = {}  # Maps to sessions
USER_DATA: Dict[str, dict] = {}   # Maps to sessions


def sync_legacy_state(user_id: str) -> str:
    """Sync legacy USER_STATES with new session manager"""
    session = sessions.get(user_id)
    USER_STATES[user_id] = session.state
    USER_DATA[user_id] = session.data
    return session.state


def update_legacy_state(user_id: str, state: str, data: dict = None):
    """Update both legacy and new state"""
    USER_STATES[user_id] = state
    if data:
        USER_DATA[user_id] = data
    sessions.update(user_id, state=state, data=data or {})


# =============================================================================
# SECTION 7: GEMINI AI OPTIMIZATION
# =============================================================================

gemini_model = None
_gemini_initialized = False

def init_gemini():
    """Lazy initialize Gemini with error handling"""
    global gemini_model, _gemini_initialized
    if _gemini_initialized:
        return gemini_model is not None
    
    if not GEMINI_API_KEY or not genai:
        _gemini_initialized = True
        return False
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=(
                "You are FLOODCARE AI, an emergency flood assistant.\n"
                "คุณคือ FLOODCARE AI ผู้ช่วยฉุกเฉินด้านน้ำท่วม ตอบเป็นภาษาไทยเป็นหลัก "
                "เสมือนเจ้าหน้าที่กู้ภัยมืออาชีพที่ใจเย็นและน่าเชื่อถือ ไม่ใช่แชทบอททั่วไปที่ตอบเรียบเฉย\n"
                "[1] Severity Check (ทำก่อนตอบทุกครั้ง): ประเมินระดับสถานการณ์จากความหมายของข้อความ ไม่ใช่แค่คำสำคัญตรงตัว "
                "Normal=สอบถามข้อมูลทั่วไป, Warning=กังวล/ไม่แน่ใจ เช่น 'บ้านผมจะท่วมไหม', "
                "Emergency=มีปัญหาที่ต้องแก้ไขแต่ยังไม่ถึงชีวิต เช่น 'ลูกติดอยู่ที่โรงเรียน' 'ไฟดับเพราะน้ำเข้าหม้อแปลง' 'น้ำเข้ารถแล้วทำไง', "
                "SOS=อันตรายถึงชีวิตเฉพาะหน้า เช่น 'น้ำกำลังเข้าบ้าน' 'ติดอยู่บนหลังคา' 'น้ำเชี่ยว' 'คนจมน้ำ' 'ไฟดูด' 'ถูกงูกัด' 'รถติดกลางน้ำ' 'ช่วยด้วย'\n"
                "[2] ถ้าระดับเป็น Emergency หรือ SOS: ให้คำแนะนำเร่งด่วนเป็นขั้นตอนทันที, แนะนำให้ออกจากพื้นที่อันตรายหากปลอดภัยที่จะทำ, "
                "แนะนำให้โทร 1784 (ปภ.) หรือ 1669 (การแพทย์ฉุกเฉิน) หรือหน่วยงานที่เหมาะสมตามอาการ, "
                "แนะนำให้กดปุ่ม SOS ของระบบเพื่อแจ้งทีมช่วยเหลือ (พิมพ์ 'sos'), "
                "ใช้น้ำเสียงชัดเจน สุภาพ หนักแน่น ไม่สร้างความตื่นตระหนกเพิ่ม ห้ามตอบเรียบเฉยเหมือนแชทบอททั่วไปในสถานการณ์เหล่านี้\n"
                "[3] Data-Driven: ใช้ข้อมูลระบบเป็นหลัก หากไม่มีให้บอกตรงๆว่า 'ไม่มีข้อมูล' อย่าเดา\n"
                "[4] Tone: ใจดี ชัดเจน เป็นขั้นตอน 1-2-3 เมื่อเหมาะสม\n"
                "[5] Completeness: ตอบให้ครบถ้วนและเข้าใจง่ายเสมอ ห้ามตอบสั้นจนไม่ตอบคำถามจริง "
                "(เช่น ถ้าถูกถามว่า 'ทำอะไรได้บ้าง' ต้องตอบรายการสิ่งที่ทำได้จริง ไม่ใช่แค่บอกชื่อตัวเอง) "
                "ความยาวที่เหมาะสมคือสั้นกระชับแต่ครบประเด็น ไม่จำกัดจำนวนบรรทัดตายตัว\n"
                "[6] No Meta-Talk: ห้ามพูดถึง 'คำสั่งที่ได้รับ' ข้อจำกัดของระบบ หรือปฏิเสธที่จะตอบยาวขึ้นโดยอ้างกฎภายใน "
                "ถ้าผู้ใช้ขอคำตอบที่ละเอียดขึ้น ให้ตอบให้ละเอียดขึ้นตามที่ขอ\n"
                "[7] Safety & Sources: เรื่องความปลอดภัย การปฐมพยาบาล หรือสุขภาพ ให้ตอบเป็นขั้นตอนที่ทำตามได้จริง "
                "ห้ามยืนยันความปลอดภัย 100% และถ้าเป็นไปได้ให้ระบุชื่อแหล่งอ้างอิงที่น่าเชื่อถือต่อท้ายคำตอบ\n"
                "[8] Scope (เรื่องที่ตอบได้): น้ำท่วม, ฝนตกหนัก, น้ำป่า, ดินถล่ม, การอพยพ, จุดปลอดภัย, การช่วยเหลือ, "
                "การปฐมพยาบาล, การเอาตัวรอด, เบอร์ฉุกเฉิน, การเตรียมตัว, ศูนย์พักพิง, คำแนะนำจากภาครัฐ, การเดินทางช่วงน้ำท่วม, "
                "อาหาร, น้ำดื่ม, ไฟฟ้า, สัตว์มีพิษช่วงน้ำท่วม, การช่วยผู้ประสบภัย, การแจ้งเหตุ SOS "
                "— รวมถึงอาการเจ็บป่วยทางกาย (เช่น ไข้ ปวดหัว ท้องเสีย ผื่นคัน บาดแผล) และสภาวะทางจิตใจ (เครียด วิตกกังวล ซึมเศร้า) "
                "ของผู้ใช้ด้วยเสมอ เพราะอาจเกี่ยวข้องกับการอยู่ในสถานการณ์น้ำท่วม (เช่น โรคจากน้ำสกปรกอย่างไข้เลือดออกหรือเลปโตสไปโรซิส, "
                "ความเครียดจากภัยพิบัติ) — คำถามเรื่องอาการป่วยหรือความรู้สึกของผู้ใช้ถือว่าเกี่ยวข้องกับขอบเขตนี้เสมอ "
                "ห้ามปฏิเสธคำถามกลุ่มนี้เด็ดขาด ให้ตอบคำแนะนำที่เหมาะสมและแนะนำให้พบแพทย์หรือผู้เชี่ยวชาญถ้าอาการรุนแรงหรือไม่แน่ใจ\n"
                "[9] ถ้าคำถามไม่เกี่ยวข้องกับเรื่องในข้อ [8] เลยจริงๆ (เช่น ฟุตบอล, เกม, การบ้านที่ไม่เกี่ยวกับภัยพิบัติ, "
                "เขียนโปรแกรมทั่วไปที่ไม่เกี่ยวกับระบบนี้, ข่าวบันเทิง, ยานยนต์ทั่วไป) ให้ตอบอย่างสุภาพด้วยความหมายนี้เท่านั้น "
                "(ปรับสำนวนเล็กน้อยได้แต่ความหมายต้องตรง): 'ขออภัยครับ ระบบนี้ถูกออกแบบมาเพื่อให้ข้อมูลและช่วยเหลือเกี่ยวกับ"
                "สถานการณ์น้ำท่วมและเหตุฉุกเฉินครับ หากต้องการข้อมูลด้านน้ำท่วมหรือความปลอดภัย ผมยินดีช่วยเหลือครับ' "
                "ห้ามตอบข้อมูลนอกขอบเขตแม้ผู้ใช้จะยืนยันหรือขอร้องซ้ำก็ตาม แต่ห้ามใช้ข้อนี้กับคำถามสุขภาพ/ความรู้สึกตามข้อ [8]"
            ),
            safety_settings={
                # ลดความเข้มงวดของตัวกรองหมวด "เนื้อหาเสี่ยงอันตราย" ลงเล็กน้อย เพราะแอปนี้
                # ต้องให้คำแนะนำปฐมพยาบาล/ความปลอดภัยที่อาจถูกตัวกรองเข้มงวดบล็อกหรือตัดให้สั้นลง
                # ทั้งที่เป็นเนื้อหาช่วยชีวิตที่ถูกต้องตามหลักการ ยังคงบล็อกเนื้อหาที่อันตรายจริงๆอยู่
                "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_ONLY_HIGH",
            },
        )
        _gemini_initialized = True
        Logger.info("Gemini", "Initialized successfully")
        return True
    except Exception as e:
        Logger.error("Gemini", f"Initialization failed: {e}")
        _gemini_initialized = True
        return False


def ask_gemini(prompt: str, max_tokens: int = 300) -> str:
    """
    Optimized Gemini API call with caching
    - Cache responses for identical prompts
    - Limit max tokens
    - Handle errors gracefully
    """
    start_time = time.time()
    
    if not init_gemini():
        return "⚠️ AI ไม่พร้อมใช้งาน หากตกอยู่ในอันตราย โทร ปภ. 1784 ทันทีครับ"
    
    # Generate cache key from prompt hash
    cache_key = f"gemini:{hashlib.md5(prompt.encode()).hexdigest()}"
    
    # Check cache (5 min TTL for AI responses)
    cached = cache.general.get(cache_key)
    if cached:
        elapsed = (time.time() - start_time) * 1000
        Logger.perf("Gemini", "cache_hit", elapsed)
        return cached
    
    try:
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.3,
            )
        )
        result = clean_text_for_line(response.text.strip())
        
        # Cache for 5 minutes
        cache.general.set(cache_key, result, ttl=300)
        
        elapsed = (time.time() - start_time) * 1000
        Logger.perf("Gemini", "api_call", elapsed, {"prompt_len": len(prompt)})
        
        return result
        
    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        Logger.error("Gemini", f"API error: {e}", {"elapsed_ms": round(elapsed, 1)})
        return "⚠️ AI ขัดข้องชั่วคราว หากตกอยู่ในอันตราย โทร ปภ. 1784 ทันทีครับ"


def ask_gemini_with_search(question: str, max_tokens: int = 700) -> dict:
    """
    Gemini API call with Google Search grounding enabled.
    Returns dict: {"answer": str, "sources": list[{"title": str, "url": str}]}

    Uses Gemini's built-in Google Search grounding tool — the model searches
    Google automatically when it needs current information (e.g. latest flood
    news, current water levels, recent warnings). This is the right tool for
    FAQ/current events, NOT `ask_gemini()` which has no real-time data access.

    Falls back to plain `ask_gemini()` if grounding fails or is unavailable
    (e.g. older google-generativeai SDK version doesn't support it).
    """
    if not init_gemini():
        return {"answer": "⚠️ AI ไม่พร้อมใช้งาน โทร ปภ. 1784 หากฉุกเฉินครับ", "sources": []}

    start_time = time.time()

    # Build search-optimised prompt that instructs the model to cite sources
    prompt = (
        "คุณคือ FLOODCARE AI ผู้ช่วยด้านน้ำท่วมของไทย ค้นหาข้อมูลและตอบคำถามต่อไปนี้:\n\n"
        f"คำถาม: {question}\n\n"
        "กฎการตอบ:\n"
        "1. ตอบเป็นภาษาไทยที่อ่านง่าย เรียบเรียงใหม่จากข้อมูลที่ค้นพบ ไม่คัดลอกมาตรงๆ\n"
        "2. ย่อหน้าสั้น ชัดเจน มีหัวข้อย่อยถ้าเหมาะสม\n"
        "3. ถ้าเป็นข้อมูลล่าสุด/ข่าว ให้ระบุวันที่หรือช่วงเวลาที่แหล่งข้อมูลรายงาน\n"
        "4. ต่อท้ายด้วยส่วน 'แหล่งข้อมูล:' พร้อม URL จริงของแหล่งที่มาที่ค้นพบ\n"
        "5. ตอบเฉพาะเรื่องน้ำท่วม ภัยพิบัติ ความปลอดภัย สภาพอากาศ หรือการช่วยเหลือผู้ประสบภัยเท่านั้น"
    )

    try:
        # Try with Google Search grounding tool
        search_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            tools=[{"google_search": {}}],
            system_instruction=(
                "You are FLOODCARE AI, a Thai flood emergency assistant. "
                "Always respond in Thai. Use Google Search to find current information. "
                "Always cite your sources with real URLs at the end of your response."
            ),
        )
        response = search_model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.2,
            ),
        )
        raw_text = clean_text_for_line(response.text.strip())

        # Extract source URLs from grounding metadata if available
        sources = []
        try:
            for candidate in response.candidates:
                grounding = getattr(candidate, "grounding_metadata", None)
                if grounding:
                    for chunk in getattr(grounding, "grounding_chunks", []):
                        web = getattr(chunk, "web", None)
                        if web:
                            title = getattr(web, "title", "") or ""
                            uri = getattr(web, "uri", "") or ""
                            if uri:
                                sources.append({"title": title, "url": uri})
        except Exception:
            pass  # Grounding metadata may not be available in all SDK versions

        elapsed = (time.time() - start_time) * 1000
        Logger.perf("Gemini", "search_call", elapsed)
        return {"answer": raw_text, "sources": sources}

    except Exception as e:
        # Grounding not available (old SDK / API error) — fall back to plain AI
        Logger.info("Gemini", f"Search grounding failed ({e}), falling back to plain ask_gemini")
        answer = ask_gemini(prompt, max_tokens=max_tokens)
        return {"answer": answer, "sources": []}

def clean_text_for_line(text: str) -> str:
    """กรองลบเครื่องหมายดอกจัน (*) สำหรับ LINE"""
    if not text:
        return ""
    return text.replace("**", "").replace("*", "")


def extract_number(text: str) -> str:
    """ดึงตัวเลขจากข้อความ"""
    if not text:
        return "1"
    cleaned = "".join(filter(str.isdigit, text))
    return cleaned if cleaned else "1"


def parse_yes_no(text: str) -> str:
    """แปลงข้อความเป็น YES/NO"""
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
    """คัดกรองรหัส Google Sheet ID จาก URL"""
    if not sheet_var:
        return ""
    if "/d/" in sheet_var:
        parts = sheet_var.split("/d/")
        if len(parts) > 1:
            sub = parts[1].split("/")[0].strip()
            return sub
    return sheet_var.strip()


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """คำนวณระยะทาง Haversine (หน่วย: กิโลเมตร)"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def generate_case_id() -> str:
    """Generate unique SOS case ID using date + random UUID suffix"""
    import uuid
    today = datetime.datetime.now().strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"SOS-{today}-{suffix}"


def generate_need_id() -> str:
    """Generate unique Need case ID using date + random UUID suffix"""
    import uuid
    today = datetime.datetime.now().strftime("%Y%m%d")
    suffix = uuid.uuid4().hex[:6].upper()
    return f"NEED-{today}-{suffix}"


# =============================================================================
# SECTION 9: GOOGLE SHEETS OPTIMIZATION
# =============================================================================

class SheetsManager:
    """
    Optimized Google Sheets manager with connection pooling
    and batch write operations
    """
    
    def __init__(self):
        self._client = None
        self._initialized = False
        self._last_error = ""
        self._lock = threading.Lock()
    
    def get_client(self):
        """Lazy initialize Sheets client with caching"""
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
                
                # Auto-setup sheets
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
        """Auto-create required worksheets"""
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
            
            # Add default contacts if new
            if "Contacts" not in existing:
                ws = sheet.worksheet("Contacts")
                defaults = [
                    ["CT001", "ปภ. (กรมป้องกันและบรรเทาสาธารณภัย)", 
                     "รับแจ้งเหตุเตือนภัยและช่วยเหลืออุทกภัยสายด่วน", "1784"],
                    ["CT002", "สพฉ. (สถาบันการแพทย์ฉุกเฉินแห่งชาติ)", 
                     "รับส่งต่อผู้ป่วยฉุกเฉินทางการแพทย์", "1669"],
                    ["CT003", "ตำรวจทางหลวง", 
                     "ประสานงานความช่วยเหลือเส้นทางน้ำท่วม", "1193"],
                    ["CT004", "หน่วยกู้ชีพวชิรพยาบาล", 
                     "กู้ภัยทางน้ำและอุบัติเหตุ", "1554"],
                ]
                for row in defaults:
                    ws.append_row(row)
            
        except Exception as e:
            Logger.error("Sheets", f"Auto-setup error: {e}")
    
    def batch_append(self, worksheet_name: str, rows: list):
        """Batch append rows to reduce API calls"""
        client = self.get_client()
        if not client:
            return False
        
        try:
            sheet = client.open_by_key(extract_sheet_id(GOOGLE_SHEET_ID))
            ws = sheet.worksheet(worksheet_name)
            
            # Append all rows at once
            if rows:
                ws.append_rows(rows, value_input_option='RAW')
            return True
        except Exception as e:
            Logger.error("Sheets", f"Batch append error: {e}")
            return False
    
    def get_all_records(self, worksheet_name: str) -> list:
        """Get all records with caching"""
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
        """Update single cell"""
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


# Global sheets manager
sheets_mgr = SheetsManager()


def get_sheets_client():
    """Legacy compatibility wrapper"""
    return sheets_mgr.get_client()


# =============================================================================
# SECTION 10: WEATHER & FLOOD DATA (Optimized)
# =============================================================================

WEATHER_CONDITION_MAP = {
    1: "แจ่มใส", 2: "เมฆบางส่วน", 3: "เมฆมาก", 4: "ครึ้ม",
    5: "ฝนเล็กน้อย", 6: "ฝนปานกลาง", 7: "ฝนหนัก",
    8: "ฝนฟ้าคะนอง", 9: "หนาวจัด", 10: "หนาว",
    11: "เย็น", 12: "ร้อนจัด"
}

# ทางการ/อ้างอิงได้ - หน้าเว็บกรมอุตุนิยมวิทยาสำหรับให้ผู้ใช้ดูพยากรณ์เต็มรูปแบบเพิ่มเติม
TMD_SOURCE_URL = "https://www.tmd.go.th"


def get_live_weather_data(lat: float, lon: float) -> dict:
    """
    Fetch live weather from the Thai Meteorological Department (TMD) official
    open-data API (data.tmd.go.th) — a legitimate, authenticated, rate-limited
    API call (requires TMD_ACCESS_TOKEN), NOT a website scrape. This keeps the
    integration legal and avoids putting unnecessary load on government
    infrastructure.

    Returns a structured dict so callers can build either a Flex card or
    plain text:
        {"ok": bool, "temp", "desc", "rh", "wind", "source": "TMD", "error": str|None}
    """
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
            return result  # don't cache rate-limit errors

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
    """Backward-compatible string-formatted weather summary (built on top of
    get_live_weather_data). Kept for any code that still expects plain text."""
    d = get_live_weather_data(lat, lon)
    if not d.get("ok"):
        return f"⚠️ {d.get('error', 'ไม่สามารถดึงข้อมูลอากาศได้ในขณะนี้')}\nกรุณาตรวจสอบจากแอปพยากรณ์อากาศโดยตรง"
    return f"🌡️ {d['temp']} °C | 🌧️ {d['desc']}\n💧 ชื้น {d['rh']}% | 🍃 ลม {d['wind']} m/s"


def calculate_situation(water_level, bank_level):
    """คำนวณสถานการณ์น้ำ"""
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


def assess_water_level_status(wl_value, bl_value=None, situation=None, lang="TH"):
    """ประเมินสถานะระดับน้ำ"""
    if not situation:
        situation = calculate_situation(wl_value, bl_value)
    
    try:
        wl = float(wl_value) if wl_value not in [None, "-", ""] else 0
        bl = float(bl_value) if bl_value not in [None, "-", ""] else 0
        diff_text = f"{abs(bl - wl):.2f}"
    except (ValueError, TypeError):
        diff_text = "-"
    
    t = {
        "ล้นตลิ่ง": "ล้นตลิ่ง", "มาก": "มาก", "ปกติ": "ปกติ",
        "น้อย": "น้อย", "น้อยวิกฤต": "น้อยวิกฤต", "none": "ไม่มีข้อมูล"
    }
    
    status_map = {
        "ล้นตลิ่ง": {"status": t["ล้นตลิ่ง"], "bg": "#FEE2E2", "text": "#EF4444", "advice": "อพยพทันที"},
        "มาก": {"status": t["มาก"], "bg": "#DBEAFE", "text": "#3B82F6", "advice": "ระดับน้ำสูง"},
        "ปกติ": {"status": t["ปกติ"], "bg": "#D1FAE5", "text": "#10B981", "advice": "ระดับน้ำปกติ"},
        "น้อย": {"status": t["น้อย"], "bg": "#FEF9C3", "text": "#F59E0B", "advice": "ระดับน้ำน้อย"},
        "น้อยวิกฤต": {"status": t["น้อยวิกฤต"], "bg": "#FFEDD5", "text": "#F97316", "advice": "น้อยวิกฤต"},
    }
    
    res = status_map.get(situation, {
        "status": t["none"], "bg": "#9CA3AF", "text": "#FFFFFF", "advice": "ติดตามสถานการณ์"
    })
    res["diff_text"] = diff_text
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


def show_loading_animation(user_id: str, loading_seconds: int = 10) -> bool:
    """Show LINE typing indicator"""
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
# SECTION 12: FLEX MESSAGE BUILDERS
# =============================================================================

def build_sos_form_flex(user_name="คุณ", lang="TH"):
    """SOS Flex Form - redirects to LIFF for full experience"""
    texts = {
        "TH": {"title": "🚨 แจ้งเหตุฉุกเฉิน SOS", "hi": f"สวัสดีครับ คุณ{user_name}",
               "desc": "กรุณากรอกข้อมูลผ่านแบบฟอร์มด้านล่าง", "btn": "📋 เปิดแบบฟอร์ม SOS",
               "footer": "ข้อมูลจะถูกส่งไปยังทีมกู้ภัยทันที"},
        "EN": {"title": "🚨 SOS Emergency", "hi": f"Hello {user_name}",
               "desc": "Please fill out the form below", "btn": "📋 Open SOS Form",
               "footer": "Data will be sent to rescue team immediately"},
    }
    t = texts.get(lang, texts["TH"])
    
    liff_url = SOS_LIFF_URL or "https://liff.line.me/"
    
    return FlexSendMessage(
        alt_text=t["title"],
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#C2452F")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text=t["title"], weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=t["hi"], size="sm", color="#374151"),
                    TextComponent(text=t["desc"], size="xs", color="#6B7280"),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(
                        action=URIAction(label=t["btn"], uri=liff_url),
                        style="primary", color="#C2452F", height="lg"
                    )
                ]
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text=t["footer"], size="xxs", color="#9CA3AF", align="center")]
            )
        )
    )


def build_need_form_flex(user_name="คุณ", lang="TH"):
    """Needs Flex Form - redirects to LIFF for full experience"""
    texts = {
        "TH": {"title": "📦 แจ้งความต้องการสิ่งของ", "hi": f"สวัสดีครับ คุณ{user_name}",
               "desc": "กรุณากรอกข้อมูลผ่านแบบฟอร์มด้านล่าง เพื่อให้ทีมงานจัดส่งสิ่งของได้ตรงตามความต้องการ",
               "btn": "📋 เปิดแบบฟอร์มแจ้งความต้องการ",
               "footer": "ข้อมูลจะถูกส่งไปยังทีมจัดส่งสิ่งของทันที"},
        "EN": {"title": "📦 Request Supplies", "hi": f"Hello {user_name}",
               "desc": "Please fill out the form below so our team can prepare the right supplies",
               "btn": "📋 Open Needs Form",
               "footer": "Data will be sent to the supplies team immediately"},
    }
    t = texts.get(lang, texts["TH"])

    liff_url = NEED_LIFF_URL or "https://liff.line.me/"

    return FlexSendMessage(
        alt_text=t["title"],
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#2F6F8F")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text=t["title"], weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=t["hi"], size="sm", color="#374151"),
                    TextComponent(text=t["desc"], size="xs", color="#6B7280", wrap=True),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(
                        action=URIAction(label=t["btn"], uri=liff_url),
                        style="primary", color="#2F6F8F", height="lg"
                    )
                ]
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text=t["footer"], size="xxs", color="#9CA3AF", align="center")]
            )
        )
    )


def build_register_form_flex(user_name="คุณ", lang="TH"):
    """Registration Flex Form - opens the Register LIFF for first-time setup"""
    texts = {
        "TH": {"title": "📝 ลงทะเบียนผู้ใช้งาน", "hi": f"สวัสดีครับ คุณ{user_name}",
               "desc": "กรอกข้อมูลเบื้องต้นของคุณ เพื่อให้ทีมช่วยเหลือติดต่อและดูแลคุณได้รวดเร็วขึ้น",
               "btn": "📋 เปิดแบบฟอร์มลงทะเบียน",
               "footer": "ใช้เวลาไม่ถึง 1 นาที ข้อมูลของคุณจะถูกเก็บเป็นความลับ"},
        "EN": {"title": "📝 User Registration", "hi": f"Hello {user_name}",
               "desc": "Fill in your basic info so our team can reach you faster",
               "btn": "📋 Open Registration Form",
               "footer": "Takes less than a minute. Your data is kept confidential."},
    }
    t = texts.get(lang, texts["TH"])

    liff_url = REGISTER_LIFF_URL or "https://liff.line.me/"

    return FlexSendMessage(
        alt_text=t["title"],
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#2F6F8F")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text=t["title"], weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=t["hi"], size="sm", color="#374151"),
                    TextComponent(text=t["desc"], size="xs", color="#6B7280", wrap=True),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(
                        action=URIAction(label=t["btn"], uri=liff_url),
                        style="primary", color="#2F6F8F", height="lg"
                    )
                ]
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text=t["footer"], size="xxs", color="#9CA3AF", align="center", wrap=True)]
            )
        )
    )


def build_snake_bite_flex(lang="TH"):
    """
    Snake-bite first-aid response.

    Deliberately a fixed, pre-written message (NOT generated by Gemini per
    request) — this is exactly the kind of high-stakes medical safety
    content where a verified, complete answer matters more than a short
    AI-generated one. Links to the Ramathibodi Poison Center (ศูนย์พิษวิทยา
    รามาธิบดี), the standard reference in Thailand for bite/poison cases,
    24-hour hotline 1367.
    """
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
    """Capabilities / help menu — answers 'ทำอะไรได้บ้าง' with a complete,
    fixed list instead of letting Gemini guess (which previously produced
    unhelpfully short non-answers like 'ฉันคือ FLOODCARE')."""
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
    """
    Flex message for FAQ / web-search grounded answers.
    Shows the AI answer and up to 3 clickable source links.
    The sources come from Gemini's Google Search grounding metadata — real URLs
    retrieved by the model, not hardcoded.
    """
    header_text = "ข้อมูลจาก FLOODCARE AI"
    body_contents = [
        TextComponent(
            text=f"คำถาม: {question[:60]}{'...' if len(question) > 60 else ''}",
            size="xs", color="#8C8980", wrap=True, margin="none"
        ),
        SeparatorComponent(margin="md"),
        TextComponent(
            text=answer[:900] + ("..." if len(answer) > 900 else ""),
            size="sm", color="#15151A", wrap=True, margin="md"
        ),
    ]

    footer_contents = []
    if sources:
        body_contents.append(SeparatorComponent(margin="lg"))
        body_contents.append(
            TextComponent(text="แหล่งข้อมูลอ้างอิง", size="xs", color="#8C8980", weight="bold", margin="md")
        )
        # Show up to 3 sources as link buttons
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
    """AI Response Flex with action buttons"""
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
    """Language selector Flex"""
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
    """
    Professional, easy-to-read weather report card.
    Data source: Thai Meteorological Department (TMD) official API — shown
    in the footer with a link so users can verify / see the full forecast.
    """
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
    """Water Level Report Flex"""
    header = BoxComponent(
        layout="vertical",
        contents=[
            TextComponent(text="🌊 ระดับน้ำใกล้คุณ", weight="bold", size="xl", color="#1F2937"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f}", size="xs", color="#6B7280"),
            TextComponent(text=f"🕒 {timestamp}", size="xs", color="#9CA3AF")
        ]
    )
    
    stations_box = BoxComponent(layout="vertical", spacing="md", margin="lg", contents=[])
    
    if not stations:
        stations_box.contents.append(
            TextComponent(text="⚠️ ไม่พบสถานีในพื้นที่ใกล้เคียง", size="sm", color="#EF4444")
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
            
            card = BoxComponent(
                layout="vertical",
                contents=[
                    TextComponent(text=f"{st['stationName']} (ห่าง {dist:.2f} กม.)", 
                                 weight="bold", size="sm", color="#1F2937"),
                    BoxComponent(
                        layout="horizontal", margin="sm", spacing="sm",
                        contents=[
                            BoxComponent(
                                layout="vertical",
                                background_color=assessment.get("bg", "#9CA3AF"),
                                corner_radius="xl",
                                padding_all="sm",
                                contents=[TextComponent(text=assessment["status"], size="xs",
                                          color=assessment.get("text", "#FFF"), weight="bold", align="center")]
                            ),
                            TextComponent(text=assessment["advice"], size="xs", color="#4B5563", gravity="center")
                        ]
                    ),
                    TextComponent(text=f"ระดับน้ำ: {wl_val} ม. | ตลิ่ง: {st.get('bank_level', '-')}",
                                 size="xs", color="#4B5563", margin="sm")
                ]
            )
            stations_box.contents.append(card)
    
    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=[header, SeparatorComponent(margin="md"), stations_box]
        ),
        footer=BoxComponent(
            layout="vertical",
            contents=[
                ButtonComponent(
                    action=URIAction(label="🔗 ดูแผนที่ระดับน้ำทั้งประเทศ (Thaiwater)", uri=WATER_LEVEL_SOURCE_URL),
                    style="secondary", color="#F3F4F6", height="sm"
                ),
                TextComponent(
                    text="ข้อมูลอ้างอิง: สถาบันสารสนเทศทรัพยากรน้ำ (สสน.) - thaiwater.net",
                    size="xxs", color="#9CA3AF", align="center", margin="sm", wrap=True
                )
            ]
        )
    )
    return FlexSendMessage(alt_text="รายงานระดับน้ำ", contents=bubble)


# =============================================================================
# SECTION 13: GREETING & RESPONSE HANDLERS
# =============================================================================

def is_greeting(text: str) -> bool:
    """Check if text is a greeting"""
    if not text:
        return False
    clean = text.strip().lower().strip("!.,😊🙏👋 ")
    greetings = ["สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "hello", "hi", "hey",
                "good morning", "good afternoon", "good evening", "menu", "เมนู", "เริ่ม", "start"]
    return any(clean.startswith(g.lower()) or g.lower() in clean for g in greetings)


def get_greeting_message(user_name="คุณ"):
    """Generate greeting message"""
    now = datetime.datetime.now()
    time_greeting = "สวัสดี"
    if 5 <= now.hour < 10:
        time_greeting = "อรุณสวัสดิ์"
    
    text = (
        f"{time_greeting} คุณ {user_name}\n"
        "ผมคือ FLOODCARE AI\n"
        "แชทบอทอัจฉริยะสำหรับติดตามสถานการณ์น้ำ แจ้งเหตุฉุกเฉิน และช่วยเหลือผู้ประสบภัยครับ\n\n"
        "🔍 ผมช่วยคุณได้:\n"
        "1. 📞 เบอร์โทรฉุกเฉิน\n"
        "2. 🚨 SOS แจ้งเหตุ\n"
        "3. 🏠 ค้นหาศูนย์อพยพ\n"
        "4. 🌊 ตรวจสอบระดับน้ำ\n"
        "5. 📦 แจ้งความต้องการ\n"
        "6. 🤖 สอบถาม AI\n\n"
        "ผมพร้อมช่วยเหลือตลอด 24 ชั่วโมงครับ 💧"
    )
    return TextSendMessage(text=text)


def handle_emergency_response(user_id: str, event=None) -> TextSendMessage:
    """Immediate emergency response without AI"""
    emergency_text = (
        "🚨 ฉุกเฉิน! ทำตามนี้ทันที:\n\n"
        "1️⃣ ยกเบรกเกอร์ไฟฟ้าทันที\n"
        "2️⃣ ขึ้นที่สูงที่สุดเท่าที่ทำได้\n"
        "3️⃣ โทรแจ้งเจ้าหน้าที่:\n"
        "   📞 ปภ. 1784\n"
        "   📞 สพฉ. 1669\n"
        "   📞 ตำรวจทางหลวง 1193\n\n"
        "⚠️ อย่าตกใจ ประหยัดแบตมือถือ\n"
        "รอความช่วยเหลืออยู่ที่จุดปลอดภัย"
    )
    return TextSendMessage(text=emergency_text)


# =============================================================================
# SECTION 14: SOS & NEEDS WORKFLOW HELPERS
# =============================================================================

def calculate_sos_priority(group_types: list, urgency_level: str) -> Tuple[str, str]:
    """Calculate SOS priority level"""
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
    """Build SOS summary text for confirmation"""
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
    """Build Needs summary text for confirmation"""
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


# =============================================================================
# SECTION 15: BACKGROUND CLEANUP
# =============================================================================

def start_background_tasks():
    """Start background cleanup thread"""
    def cleanup_loop():
        while True:
            try:
                time.sleep(300)  # Every 5 minutes
                
                # Cleanup expired sessions
                session_count = sessions.cleanup_expired()
                
                # Cleanup expired cache entries
                cache_count = sum(cache.cleanup_all().values())
                
                if session_count > 0 or cache_count > 0:
                    Logger.info("Cleanup", f"Removed {session_count} sessions, {cache_count} cache entries")
                    
            except Exception as e:
                Logger.error("Cleanup", f"Loop error: {e}")
    
    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()
    Logger.info("System", "Background cleanup started")


# Start background tasks on import
start_background_tasks()

Logger.info("System", "FLOODCARE AI Bot Config v2.0 loaded successfully")
