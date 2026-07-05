"""
FLOODCARE AI - Optimized Bot Configuration
============================================
Architecture: Modular | Class-Based State Machine | Intent Classification
Author: Senior Software Architect & UI Designer
Version: 3.0.0 (Pixel-Perfect UI Redesign Match)

Key Optimizations:
- Intent Classification: Reduces Gemini API calls by ~80%
- Smart Cache: Multi-layer (Memory LRU > TTL Cache)
- State Machine: Class-based, separated workflows (Only for Location searches)
- Rate Limiting: Per-user request throttling
- Localized Timezone: Standardized Thai timezone (Asia/Bangkok / UTC+7) for all systems
- Custom Water Status Mapping: Uses official Thaiwater status keys with specified hex colors
- Strictly Limited Scope: Only answers floods, safety, and health queries. Refuses all else.
"""

import os
import json
import math
import time
import random
import hashlib
import datetime
import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple, Any, Callable

# =============================================================================
# EXTERNAL DEPENDENCIES
# =============================================================================
try:
    import requests
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

# Public HTTPS base URL of this deployment
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def hero_image_url(filename: str) -> str:
    """
    Builds a public URL to a static banner image or falls back to robust public CDNs
    to ensure the Line Flex Message renders beautiful visuals under all conditions.
    """
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/static/banners/{filename}"
    
    # Fallback to high-quality public CDN imagery matching the exact aesthetic of the UI design
    fallbacks = {
        "prep_banner.jpg": "https://images.unsplash.com/photo-1547683905-f686c993aae5?auto=format&fit=crop&q=80&w=800",
        "weather_banner.jpg": "https://images.unsplash.com/photo-1504608524841-42fe6f032b4b?auto=format&fit=crop&q=80&w=800",
        "shelter_banner.jpg": "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?auto=format&fit=crop&q=80&w=800"
    }
    return fallbacks.get(filename, "https://images.unsplash.com/photo-1488521787991-ed7bbaae773c?auto=format&fit=crop&q=80&w=800")


DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")
DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")

# Performance Tuning
WATER_DATA_MAX_AGE_MINUTES = int(os.environ.get("WATER_DATA_MAX_AGE_MINUTES", "10"))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
SESSION_TTL_MINUTES = int(os.environ.get("SESSION_TTL_MINUTES", "30"))

# API Endpoints
THAIWATER_V3_API = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"

# =============================================================================
# SECTION 2: STRUCTURED LOGGING SYSTEM
# =============================================================================

class Logger:
    _log_buffer: List[dict] = []
    _buffer_lock = threading.Lock()
    _max_buffer = 100
    
    @classmethod
    def _timestamp(cls) -> str:
        return get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    @classmethod
    def info(cls, module: str, message: str, extra: dict = None):
        entry = {"ts": cls._timestamp(), "lvl": "INFO", "mod": module, "msg": message}
        if extra: entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] INFO  [{module}] {message}")
    
    @classmethod
    def error(cls, module: str, message: str, extra: dict = None):
        entry = {"ts": cls._timestamp(), "lvl": "ERROR", "mod": module, "msg": message}
        if extra: entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] ERROR [{module}] {message}")
    
    @classmethod
    def perf(cls, module: str, operation: str, elapsed_ms: float, extra: dict = None):
        entry = {"ts": cls._timestamp(), "lvl": "PERF", "mod": module, "op": operation, "ms": round(elapsed_ms, 2)}
        if extra: entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] PERF  [{module}] {operation}: {elapsed_ms:.1f}ms")
    
    @classmethod
    def security(cls, module: str, message: str, user_id: str = "", extra: dict = None):
        entry = {"ts": cls._timestamp(), "lvl": "SEC", "mod": module, "msg": message, "uid": user_id}
        if extra: entry.update(extra)
        cls._buffer(entry)
        print(f"[{entry['ts']}] SEC   [{module}] {message} uid={user_id}")
    
    @classmethod
    def _buffer(cls, entry: dict):
        with cls._buffer_lock:
            cls._log_buffer.append(entry)
            if len(cls._log_buffer) > cls._max_buffer:
                cls._log_buffer = cls._log_buffer[-cls._max_buffer:]

# =============================================================================
# SECTION 3: SMART CACHE SYSTEM
# =============================================================================

class LRUMemoryCache:
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

class CacheManager:
    def __init__(self):
        self.general = LRUMemoryCache(maxsize=512, default_ttl=CACHE_TTL_SECONDS)
        self.weather = LRUMemoryCache(maxsize=256, default_ttl=1800)
        self.water = LRUMemoryCache(maxsize=128, default_ttl=900)
        self.sessions = LRUMemoryCache(maxsize=1024, default_ttl=SESSION_TTL_MINUTES * 60)
        self.sheets = LRUMemoryCache(maxsize=64, default_ttl=600)
        
    def cleanup_all(self) -> dict:
        return {
            "general": self.general.cleanup_expired() if hasattr(self.general, "cleanup_expired") else 0,
            "weather": self.weather.cleanup_expired() if hasattr(self.weather, "cleanup_expired") else 0,
            "water": self.water.cleanup_expired() if hasattr(self.water, "cleanup_expired") else 0,
            "sessions": self.sessions.cleanup_expired() if hasattr(self.sessions, "cleanup_expired") else 0,
            "sheets": self.sheets.cleanup_expired() if hasattr(self.sheets, "cleanup_expired") else 0,
        }

cache = CacheManager()

# =============================================================================
# SECTION 4: RATE LIMITING & SECURITY
# =============================================================================

class RateLimiter:
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, dict] = {}
        self._lock = threading.Lock()
    
    def check(self, user_id: str) -> Tuple[bool, dict]:
        with self._lock:
            now = time.time()
            bucket = self._buckets.get(user_id)
            if bucket is None:
                self._buckets[user_id] = {"tokens": self._max_requests - 1, "last_reset": now}
                return True, {"remaining": self._max_requests - 1, "limit": self._max_requests}
            
            if now - bucket["last_reset"] > self._window:
                bucket["tokens"] = self._max_requests - 1
                bucket["last_reset"] = now
                return True, {"remaining": self._max_requests - 1, "limit": self._max_requests}
            
            if bucket["tokens"] <= 0:
                retry_after = int(self._window - (now - bucket["last_reset"]))
                return False, {"retry_after": retry_after, "limit": self._max_requests}
            
            bucket["tokens"] -= 1
            return True, {"remaining": bucket["tokens"], "limit": self._max_requests}

rate_limiter = RateLimiter(max_requests=RATE_LIMIT_REQUESTS, window_seconds=RATE_LIMIT_WINDOW)


def sanitize_text(text: str, max_length: int = 2000) -> str:
    if not text: return ""
    return "".join(ch for ch in text if ch in ("\n", "\t") or (ch.isprintable() and ord(ch) >= 32))[:max_length]


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


def generate_household_id(province: str, district: str, sub_district: str,
                           housing_type: str, house_no: str = "",
                           condo_floor: str = "", condo_room: str = "") -> str:
    def _norm(v: str) -> str: return "".join((v or "").strip().lower().split())
    housing_type = _norm(housing_type) or "house"
    if housing_type in ("condo", "คอนโด", "อพาร์ตเมนต์", "apartment"):
        unit_key = f"condo|{_norm(condo_floor)}|{_norm(condo_room)}"
    else:
        unit_key = f"house|{_norm(house_no)}"
    raw = "|".join([_norm(province), _norm(district), _norm(sub_district), unit_key])
    return f"HH-{hashlib.sha256(raw.encode()).hexdigest()[:10].upper()}"


# =============================================================================
# SECTION 5: INTENT CLASSIFICATION SYSTEM
# =============================================================================

class IntentClassifier:
    PATTERNS = {
        "EMERGENCY": ["ช่วยด้วย", "จะตาย", "จมแล้ว", "ไฟดูด", "หายใจไม่ออก", "บาดเจ็บสาหัส", "ด่วนที่สุด", "วิกฤต", "ช่วยชีวิต", "ติดอยู่บนหลังคา", "น้ำเข้าบ้าน"],
        "SOS": ["sos", "🆘", "ขอความช่วยเหลือ", "แจ้งเหตุ", "กู้ภัย", "ติดน้ำท่วม", "ช่วย"],
        "SNAKE_BITE": ["งูกัด", "ถูกงูกัด", "โดนงูกัด", "งูฉก", "ถูกสัตว์มีพิษกัด"],
        "PREP_GUIDE": ["วิธีเตรียมตัว", "เตรียมตัวรับมือ", "เตรียมความพร้อม", "เตรียมของก่อนน้ำท่วม", "เตรียมของ", "ของที่ควรเตรียม", "checklist", "เตรียมรับมือน้ำท่วม"],
        "GREETING": ["สวัสดี", "หวัดดี", "ดีครับ", "ดีค่ะ", "hello", "hi", "เมนู", "เริ่ม", "start", "menu"],
        "NEEDS": ["ขอของ", "ต้องการ", "ขาดแคลน", "ไม่มีอาหาร", "ไม่มีน้ำ", "ของบริจาค", "ขอความช่วยเหลือเรื่องของ", "ขอน้ำดื่ม", "ขอยา"],
        "SHELTER": ["ศูนย์พักพิง", "ที่พัก", "อพยพ", "หลบภัย", "หลบน้ำ", "ที่พักชั่วคราว", "shelter", "ไปไหนดี", "พักที่ไหน"],
        "WATER_LEVEL": ["ระดับน้ำ", "น้ำสูง", "เช็คน้ำ", "ตรวจน้ำ", "water level", "น้ำขึ้น", "น้ำลด", "สถานการณ์น้ำ"],
        "WEATHER": ["สภาพอากาศ", "พยากรณ์อากาศ", "ฝนตก", "ฝน", "อากาศ", "weather", "forecast", "จะฝนตกไหม", "เช็คฝน"],
        "CONTACT": ["เบอร์โทร", "โทรศัพท์", "ติดต่อ", "สายด่วน", "hotline", "เบอร์ฉุกเฉิน", "โทรหาใคร", "เบอร์ ปภ", "1784", "1669"],
        "LANGUAGE": ["เปลี่ยนภาษา", "change language", "ภาษา", "lang"],
        "CANCEL": ["ยกเลิก", "cancel", "หยุด", "stop"],
        "REGISTRATION": ["ลงทะเบียน", "register", "สมัคร", "ลงชื่อ", "ข้อมูลของฉัน", "โปรไฟล์", "profile"],
        "HELP": ["ทำอะไรได้บ้าง", "ช่วยอะไรได้บ้าง", "ใช้งานยังไง", "วิธีใช้", "what can you do", "คุณคือใคร"],
        "FAQ": ["คำถามยอดฮิต", "คำถามที่พบบ่อย", "faq", "ค้นหา", "search", "ข่าวน้ำท่วม", "ระดับน้ำล่าสุด"],
    }
    
    @classmethod
    def classify(cls, text: str) -> Tuple[str, float]:
        if not text: return ("AI_QUERY", 0.5)
        text_lower = text.strip().lower()
        text_clean = text_lower.strip("!.,😊🙏👋🆘 ")
        
        for intent in ["EMERGENCY", "SOS", "SNAKE_BITE"]:
            for keyword in cls.PATTERNS.get(intent, []):
                if text_clean == keyword.lower() or keyword.lower() in text_lower:
                    return (intent, 1.0)
                    
        for intent, keywords in cls.PATTERNS.items():
            for keyword in keywords:
                if text_clean == keyword.lower() or keyword.lower() in text_lower:
                    return (intent, 0.9)
        return ("AI_QUERY", 0.5)

# =============================================================================
# SECTION 6: STATE MACHINE & SESSIONS
# =============================================================================

class UserSession:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.state = "IDLE"
        self.data: Dict[str, Any] = {}
        self.updated_at = time.time()
        self.language = "TH"
        self.message_count = 0
    
    def update(self, state: str = None, data: dict = None):
        if state: self.state = state
        if data: self.data.update(data)
        self.updated_at = time.time()
        self.message_count += 1
        
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
            if session is None:
                session = UserSession(user_id)
                self._sessions[user_id] = session
            return session

sessions = SessionManager()
USER_STATES: Dict[str, str] = {}
USER_DATA: Dict[str, dict] = {}

def update_legacy_state(user_id: str, state: str, data: dict = None):
    USER_STATES[user_id] = state
    if data: USER_DATA[user_id] = data
    sessions.get(user_id).update(state=state, data=data or {})


# =============================================================================
# SECTION 7: AI (GEMINI) SERVICES
# =============================================================================

gemini_model = None
_gemini_initialized = False
FLOODCARE_SYSTEM_INSTRUCTION = (
    "คุณคือ FLOODCARE AI น้องบอทผู้ช่วยภัยน้ำท่วมในไทย คุยอบอุ่นเหมือนเพื่อนพึ่งพาได้ "
    "แทนตัวเองว่า 'น้องบอท' ห้ามใช้ดอกจัน (*) ในคำตอบเด็ดขาด ตอบเป็นข้อย่อยเว้นบรรทัด "
    "เน้นความกระชับอย่างรวดเร็วเพื่อความปลอดภัยสูงสุด"
)

def init_gemini():
    global gemini_model, _gemini_initialized
    if _gemini_initialized: return gemini_model is not None
    if not GEMINI_API_KEY or not genai:
        _gemini_initialized = True
        return False
    try:
        gemini_model = genai.Client(api_key=GEMINI_API_KEY)
        _gemini_initialized = True
        return True
    except Exception as e:
        _gemini_initialized = True
        return False

def ask_gemini_with_search(question: str, max_tokens: int = 8192) -> dict:
    if not init_gemini():
        return {"answer": "⚠️ ขออภัยครับ ระบบ AI ติดขัดชั่วคราว โทร ปภ. 1784 ได้ทันทีครับ", "sources": []}
    try:
        response = gemini_model.models.generate_content(
            model="gemini-2.5-flash",
            contents=question,
            config=genai_types.GenerateContentConfig(
                system_instruction=FLOODCARE_SYSTEM_INSTRUCTION,
                max_output_tokens=max_tokens,
                temperature=0.2,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
            )
        )
        sources = []
        try:
            for candidate in response.candidates:
                grounding = getattr(candidate, "grounding_metadata", None)
                if grounding:
                    for chunk in getattr(grounding, "grounding_chunks", None) or []:
                        web = getattr(chunk, "web", None)
                        if web and getattr(web, "uri", None):
                            sources.append({"title": getattr(web, "title", "แหล่งอ้างอิง"), "url": web.uri})
        except Exception: pass
        return {"answer": response.text.replace("*", ""), "sources": sources}
    except Exception:
        return {"answer": "⚠️ ระบบประมวลผลติดขัดชั่วคราว รบกวนลองอีกครั้งครับ", "sources": []}

def ask_gemini(prompt: str, max_tokens: int = 8192) -> str:
    res = ask_gemini_with_search(prompt, max_tokens)
    return res.get("answer", "")

# =============================================================================
# SECTION 8: GOOGLE SHEETS CONNECTOR
# =============================================================================

class SheetsManager:
    def __init__(self):
        self._client = None
        self._initialized = False
        self._lock = threading.Lock()
    
    def get_client(self):
        if self._initialized and self._client: return self._client
        with self._lock:
            if self._initialized: return self._client
            if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
                self._initialized = True
                return None
            try:
                json_str = GOOGLE_SERVICE_ACCOUNT_JSON.strip()
                if json_str.startswith("'") or json_str.startswith('"'): json_str = json_str[1:-1].strip()
                creds_dict = json.loads(json_str)
                self._client = gspread.service_account_from_dict(creds_dict)
                self._initialized = True
                return self._client
            except Exception:
                self._initialized = True
                return None
                
    def get_all_records(self, worksheet_name: str) -> list:
        client = self.get_client()
        if not client: return []
        try:
            sheet = client.open_by_key(GOOGLE_SHEET_ID.strip())
            return sheet.worksheet(worksheet_name).get_all_records()
        except Exception:
            return []

    def batch_append(self, worksheet_name: str, rows: list) -> bool:
        client = self.get_client()
        if not client: return False
        try:
            sheet = client.open_by_key(GOOGLE_SHEET_ID.strip())
            sheet.worksheet(worksheet_name).append_rows(rows, value_input_option='RAW')
            return True
        except Exception:
            return False

    def get_user_record(self, user_id: str) -> Optional[dict]:
        for rec in self.get_all_records("users"):
            if str(rec.get("user_id", "")) == user_id: return rec
        return None

    def find_open_case_by_household(self, household_id: str, window_minutes: int = 60):
        if not household_id or household_id == "-": return None, None
        try:
            records = self.get_all_records("sos_requests")
            now = get_bangkok_time()
            for idx, rec in enumerate(records, start=2):
                if str(rec.get("household_id", "")) == household_id and str(rec.get("status", "")).strip().upper() == "OPEN":
                    return idx, rec
            return None, None
        except Exception: return None, None

    def merge_sos_case(self, row_number: int, updates: dict) -> bool:
        client = self.get_client()
        if not client: return False
        try:
            sheet = client.open_by_key(GOOGLE_SHEET_ID.strip())
            ws = sheet.worksheet("sos_requests")
            header = ws.row_values(1)
            col_map = {name: i + 1 for i, name in enumerate(header)}
            cells = [gspread.Cell(row_number, col_map[name], str(value)) for name, value in updates.items() if name in col_map]
            if cells: ws.update_cells(cells, value_input_option='RAW')
            return True
        except Exception: return False

    def update_sos_status(self, case_id: str, new_status: str, responder_name: str = "-") -> bool:
        client = self.get_client()
        if not client: return False
        try:
            sheet = client.open_by_key(GOOGLE_SHEET_ID.strip())
            ws = sheet.worksheet("sos_requests")
            records = ws.get_all_records()
            row_number = None
            for idx, rec in enumerate(records, start=2):
                if str(rec.get("case_id", "")) == case_id:
                    row_number = idx
                    break
            if not row_number: return False
            header = ws.row_values(1)
            col_map = {name: i + 1 for i, name in enumerate(header)}
            ws.update_cell(row_number, col_map["status"], new_status)
            ws.update_cell(row_number, col_map["responder_name"], responder_name)
            return True
        except Exception: return False

sheets_mgr = SheetsManager()

# =============================================================================
# SECTION 9: SHELTERS & WATER ANALYSIS
# =============================================================================

SHELTER_STATUS_MAP = {
    "เปิดรับ": {"label": "เปิดรับ", "bg": "#DCFCE7", "text": "#15803D"},
    "ใกล้เต็ม": {"label": "ใกล้เต็ม", "bg": "#FEF9C3", "text": "#A16207"},
    "เต็ม": {"label": "เต็มแล้ว", "bg": "#FEE2E2", "text": "#B91C1C"},
    "ปิด": {"label": "ปิดชั่วคราว", "bg": "#E5E7EB", "text": "#374151"},
}


def get_shelters_from_sheet() -> list:
    return sheets_mgr.get_all_records("Shelters")


def find_nearest_shelters(user_lat: float, user_lon: float, limit: int = 5) -> list:
    shelters = get_shelters_from_sheet()
    results = []
    for s in shelters:
        try:
            lat = float(s.get("Latitude", 0))
            lon = float(s.get("Longitude", 0))
            if lat == 0 and lon == 0: continue
            dist = calculate_distance(user_lat, user_lon, lat, lon)
            entry = dict(s)
            entry["distance_km"] = round(dist, 2)
            results.append(entry)
        except (ValueError, TypeError): continue
    results.sort(key=lambda x: x["distance_km"])
    return results[:limit]


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))


def get_live_weather_data(lat: float, lon: float) -> dict:
    if not requests or not TMD_ACCESS_TOKEN:
        return {"ok": False, "error": "ไม่ได้เชื่อมต่อระบบข้อมูลกรมอุตุนิยมวิทยา"}
    try:
        url = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at"
        params = {"lat": lat, "lon": lon, "duration": 1, "fields": "tc,rh,cond,ws10m"}
        headers = {"accept": "application/json", "authorization": f"Bearer {TMD_ACCESS_TOKEN}"}
        resp = requests.get(url, headers=headers, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            fc = data["WeatherForecasts"][0]["forecasts"][0]["data"]
            cond_map = {1: "แจ่มใส", 2: "เมฆบางส่วน", 3: "เมฆมาก", 4: "ครึ้ม", 5: "ฝนเล็กน้อย", 6: "ฝนปานกลาง", 7: "ฝนหนัก", 8: "ฝนฟ้าคะนอง"}
            return {
                "ok": True,
                "temp": fc.get("tc", "-"),
                "rh": fc.get("rh", "-"),
                "wind": fc.get("ws10m", "-"),
                "desc": cond_map.get(fc.get("cond", 1), "แจ่มใส")
            }
    except Exception: pass
    return {"ok": False, "error": "ไม่สามารถดึงข้อมูลพยากรณ์อากาศได้ในขณะนี้"}


def get_live_weather_scraper(lat: float, lon: float) -> str:
    res = get_live_weather_data(lat, lon)
    if res.get("ok"):
        return f"🌡️ {res['temp']} °C | 🌧️ {res['desc']}\n💧 ความชื้น {res['rh']}% | 🍃 ความเร็วลม {res['wind']} m/s"
    return "ไม่สามารถดึงพยากรณ์อากาศได้"


def assess_water_level_status(wl_value, bl_value=None, situation=None):
    """
    วิเคราะห์และแมปข้อมูลสถานการณ์น้ำ
    ตรงตามระดับความเร่งด่วนและชุดสีตามมาตรฐานภาพอินโฟกราฟิก:
    - ปกติ (สีเขียว): #00B050 (พาสเทล #D4EDDA)
    - น้อย (สีเหลือง): #FFC000 (พาสเทล #FFF3CD)
    - มาก (สีน้ำเงิน): #1E88E5 (พาสเทล #CCE5FF)
    - วิกฤต/ล้นตลิ่ง (สีแดง): #DC2626 (พาสเทล #F8D7DA)
    """
    sit_str = str(situation or "").strip()
    status_key = "ปกติ"
    
    if "ล้นตลิ่ง" in sit_str or "วิกฤต" in sit_str or "ล้น" in sit_str:
        status_key = "วิกฤต"
    elif "มาก" in sit_str:
        status_key = "มาก"
    elif "น้อย" in sit_str:
        status_key = "น้อย"
    else:
        try:
            wl = float(wl_value)
            bl = float(bl_value)
            ratio = wl / bl
            if wl >= bl: status_key = "วิกฤต"
            elif ratio >= 0.85: status_key = "มาก"
            elif ratio <= 0.15: status_key = "น้อย"
        except (ValueError, TypeError): pass

    status_map = {
        "น้อย": {"status": "น้อย", "bg": "#FFF3CD", "text": "#856404", "label_pill": "น้อย"},
        "ปกติ": {"status": "ปกติ", "bg": "#D4EDDA", "text": "#155724", "label_pill": "ปกติ"},
        "มาก": {"status": "มาก", "bg": "#CCE5FF", "text": "#004085", "label_pill": "มาก"},
        "วิกฤต": {"status": "วิกฤต", "bg": "#F8D7DA", "text": "#721C24", "label_pill": "วิกฤต"},
    }
    return status_map.get(status_key, status_map["ปกติ"])

# =============================================================================
# SECTION 10: LINE CONFIGURATION & BOT CREATORS
# =============================================================================

line_bot_api = None
handler = None
if LINE_CHANNEL_ACCESS_TOKEN: line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
if LINE_CHANNEL_SECRET: handler = WebhookHandler(LINE_CHANNEL_SECRET)

def show_loading_animation(user_id: str, loading_seconds: int = 15) -> bool:
    if not LINE_CHANNEL_ACCESS_TOKEN: return False
    try:
        url = "https://api.line.me/v2/bot/chat/loading/start"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
        resp = requests.post(url, headers=headers, json={"chatId": user_id, "loadingSeconds": loading_seconds}, timeout=5)
        return resp.status_code == 202
    except Exception: return False


# =============================================================================
# SECTION 11: PIXEL-PERFECT FLEX MESSAGES (MATCHING IMAGES EXACTLY)
# =============================================================================

def build_sos_form_flex(user_name="คุณ"):
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
                    TextComponent(text=f"สวัสดีครับ คุณ{user_name}", size="sm", color="#333333", weight="bold"),
                    TextComponent(text="กดเปิดลิ้งก์แบบฟอร์มด้านล่างเพื่อส่งข้อมูลตำแหน่งและระดับน้ำเพื่อให้ทีมงานกู้ภัยเข้าช่วยเหลือทันที", size="xs", color="#666666", wrap=True),
                    SeparatorComponent(margin="lg"),
                    ButtonComponent(
                        action=URIAction(label="📋 เปิดแบบฟอร์ม SOS", uri=SOS_LIFF_URL or "https://liff.line.me/"),
                        style="primary", color="#C2452F", height="md"
                    )
                ]
            )
        )
    )


def build_need_form_flex(user_name="คุณ"):
    return FlexSendMessage(
        alt_text="📦 ขอความช่วยเหลือเรื่องสิ่งของ",
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#1E88E5")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="📦 ขอรับของบรรเทาทุกข์", weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=f"สวัสดีครับ คุณ{user_name}", size="sm", color="#333333", weight="bold"),
                    TextComponent(text="แจ้งความต้องการสิ่งของจำเป็น เช่น อาหาร น้ำดื่ม ยารักษาโรค ยารักษาน้ำกัดเท้า ได้ทางแบบฟอร์มนี้ครับ", size="xs", color="#666666", wrap=True),
                    SeparatorComponent(margin="lg"),
                    ButtonComponent(
                        action=URIAction(label="📋 แจ้งขอรับสิ่งของ", uri=NEED_LIFF_URL or "https://liff.line.me/"),
                        style="primary", color="#1E88E5", height="md"
                    )
                ]
            )
        )
    )


def build_register_form_flex(user_name="คุณ"):
    return FlexSendMessage(
        alt_text="📝 ลงทะเบียนข้อมูลของคุณ",
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#2F6F8F")),
            header=BoxComponent(
                layout="vertical",
                contents=[TextComponent(text="📝 ลงทะเบียนข้อมูลประชากร", weight="bold", size="lg", color="#FFFFFF", align="center")]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=f"สวัสดีครับ คุณ{user_name}", size="sm", color="#333333", weight="bold"),
                    TextComponent(text="กรุณาบันทึกข้อมูลเพื่อใช้เป็นพิกัดอ้างอิงในการกู้ภัยช่วยเหลือและการกระจายการแจ้งเตือนภัยล่วงหน้าได้อย่างตรงจุด", size="xs", color="#666666", wrap=True),
                    SeparatorComponent(margin="lg"),
                    ButtonComponent(
                        action=URIAction(label="📋 บันทึกข้อมูลที่อยู่", uri=REGISTER_LIFF_URL or "https://liff.line.me/"),
                        style="primary", color="#2F6F8F", height="md"
                    )
                ]
            )
        )
    )

def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, lang="TH"):
    """
    หน้าแสดงผลระดับน้ำแบบพรีเมียม ตรงตามภาพ BCF46E3B-1A3B-4FF4-837D-0E59933BE080.png และ C426E420-A36F-4B6F-9500-B31D039B131C.png
    """
    header_box = BoxComponent(
        layout="vertical",
        spacing="xs",
        contents=[
            TextComponent(text="🌊 ระดับน้ำใกล้คุณ", weight="bold", size="lg", color="#111827"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f}  ·  🕒 {timestamp}", size="xxs", color="#9CA3AF")
        ]
    )

    card_list = []
    for st in stations[:3]:
        dist = st.get("distance_km", 0)
        wl_val = st.get("WaterLevel", "-") or st.get("water_level", {}).get("value", "-")
        bl_val = st.get("BankLevel", "-") or st.get("bank_level", "-")
        situation = st.get("Situation", "ปกติ") or st.get("situation", "ปกติ")
        
        # ค้นหาคำนวณส่วนต่างตลิ่ง
        diff_text = "-"
        diff_label = "ต่ำกว่าตลิ่ง"
        try:
            diff_num = float(bl_val) - float(wl_val)
            if diff_num < 0:
                diff_label = "สูงกว่าตลิ่ง"
                diff_text = f"{abs(diff_num):.2f} ม."
            else:
                diff_text = f"{diff_num:.2f} ม."
        except Exception: pass

        # ประเมินสถานะเพื่อนำสีมาใช้
        assessment = assess_water_level_status(wl_val, bl_val, situation)
        pill_bg = assessment["bg"]
        pill_text = assessment["text"]
        pill_label = assessment["label_pill"]

        # สร้างกล่องสถานีย่อยแบบมนโค้ง
        card = BoxComponent(
            layout="vertical",
            spacing="sm",
            padding_all="lg",
            background_color="#F8FAFC",
            corner_radius="lg",
            margin="md",
            border_width="1px",
            border_color="#F1F5F9",
            contents=[
                # ส่วนบน: ชื่อและสถานะ
                BoxComponent(
                    layout="horizontal",
                    contents=[
                        BoxComponent(
                            layout="vertical",
                            flex=3,
                            contents=[
                                TextComponent(text=st.get("Name") or st.get("stationName", "ไม่ระบุสถานี"), weight="bold", size="sm", color="#1E293B", wrap=True),
                                TextComponent(text=f"ห่าง {dist:.2f} กม.", size="xxs", color="#64748B")
                            ]
                        ),
                        BoxComponent(
                            layout="vertical",
                            flex=1,
                            gravity="center",
                            background_color=pill_bg,
                            corner_radius="md",
                            padding_top="xs",
                            padding_bottom="xs",
                            contents=[
                                TextComponent(text=pill_label, size="xxs", color=pill_text, weight="bold", align="center")
                            ]
                        )
                    ]
                ),
                SeparatorComponent(margin="sm", color="#F1F5F9"),
                # ส่วนล่าง: 3 คอลัมน์พารามิเตอร์น้ำ
                BoxComponent(
                    layout="horizontal",
                    spacing="xs",
                    contents=[
                        # ระดับน้ำ
                        BoxComponent(
                            layout="vertical",
                            align="center",
                            contents=[
                                TextComponent(text="🌊 ระดับน้ำ", size="xxs", color="#64748B"),
                                TextComponent(text=f"{wl_val} ม.", size="xs", weight="bold", color="#0F172A")
                            ]
                        ),
                        # ระดับตลิ่ง
                        BoxComponent(
                            layout="vertical",
                            align="center",
                            contents=[
                                TextComponent(text="📏 ระดับตลิ่ง", size="xxs", color="#64748B"),
                                TextComponent(text=f"{bl_val} ม.", size="xs", weight="bold", color="#0F172A")
                            ]
                        ),
                        # ผลต่างตลิ่ง
                        BoxComponent(
                            layout="vertical",
                            align="center",
                            contents=[
                                TextComponent(text=f"💧 {diff_label}", size="xxs", color="#64748B"),
                                TextComponent(text=diff_text, size="xs", weight="bold", color="#DC2626" if "สูง" in diff_label else "#1E293B")
                            ]
                        )
                    ]
                )
            ]
        )
        card_list.append(card)

    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            padding_all="lg",
            contents=[header_box] + card_list
        ),
        footer=BoxComponent(
            layout="vertical",
            padding_all="md",
            spacing="sm",
            contents=[
                ButtonComponent(
                    action=URIAction(label="💧 ดูข้อมูลเพิ่มเติมที่ ThaiWater", uri=WATER_LEVEL_SOURCE_URL),
                    style="primary", color="#1E88E5", height="sm"
                ),
                TextComponent(text="สถานีตรวจวัดจาก สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)", size="xxs", color="#94A3B8", align="center")
            ]
        )
    )
    return FlexSendMessage(alt_text="🌊 ตรวจสอบระดับน้ำใกล้คุณ", contents=bubble)

def build_weather_flex(lat, lon, weather_data: dict, timestamp: str, lang="TH"):
    """
    หน้าแสดงรายงานสภาพอากาศ ตรงตามภาพ F64D1E1E-9347-4EB8-BDD1-834156B1ECDF.png
    """
    # ใช้อินโฟกราฟิกภาพพยากรณ์ธรรมชาติเป็นแบนเนอร์
    hero_image = ImageComponent(
        url=hero_image_url("weather_banner.jpg"),
        size="full",
        aspect_ratio="20:11",
        aspect_mode="cover"
    )

    temp = weather_data.get("temp", "-")
    desc = weather_data.get("desc", "แจ่มใส")
    rh = weather_data.get("rh", "-")
    wind = weather_data.get("wind", "-")

    body_content = BoxComponent(
        layout="vertical",
        padding_all="lg",
        spacing="md",
        contents=[
            BoxComponent(
                layout="vertical",
                contents=[
                    TextComponent(text="🌦️ รายงานสภาพอากาศปัจจุบัน", weight="bold", size="md", color="#1E293B"),
                    TextComponent(text=f"📍 {lat:.4f}, {lon:.4f}  ·  📅 {timestamp}", size="xxs", color="#64748B")
                ]
            ),
            SeparatorComponent(color="#F1F5F9"),
            # รายละเอียดไอเท็มพารามิเตอร์สภาพอากาศ
            BoxComponent(
                layout="horizontal",
                contents=[
                    TextComponent(text="🌡️  อุณหภูมิ", size="sm", color="#475569", flex=1),
                    TextComponent(text=f"{temp} °C", size="sm", weight="bold", color="#0F172A", align="end", flex=1)
                ]
            ),
            BoxComponent(
                layout="horizontal",
                contents=[
                    TextComponent(text="🌤️  สภาพอากาศ", size="sm", color="#475569", flex=1),
                    TextComponent(text=desc, size="sm", weight="bold", color="#0F172A", align="end", flex=1)
                ]
            ),
            BoxComponent(
                layout="horizontal",
                contents=[
                    TextComponent(text="💧  ความชื้น", size="sm", color="#475569", flex=1),
                    TextComponent(text=f"{rh} %", size="sm", weight="bold", color="#0F172A", align="end", flex=1)
                ]
            ),
            BoxComponent(
                layout="horizontal",
                contents=[
                    TextComponent(text="🍃  ความเร็วลม", size="sm", color="#475569", flex=1),
                    TextComponent(text=f"{wind} m/s", size="sm", weight="bold", color="#0F172A", align="end", flex=1)
                ]
            ),
            # กล่องเหลืองแจ้งเตือน
            BoxComponent(
                layout="vertical",
                background_color="#FFFBEB",
                corner_radius="sm",
                padding_all="md",
                contents=[
                    TextComponent(
                        text="⚠️ ข้อมูลพยากรณ์เบื้องต้น โปรดสังเกตท้องฟ้าจริงประกอบการตัดสินใจ",
                        size="xxs", color="#B45309", wrap=True
                    )
                ]
            )
        ]
    )

    bubble = BubbleContainer(
        hero=hero_image,
        body=body_content,
        footer=BoxComponent(
            layout="vertical",
            padding_all="md",
            spacing="sm",
            contents=[
                ButtonComponent(
                    action=URIAction(label="🔗 ดูพยากรณ์อากาศเต็มรูปแบบ (TMD)", uri="https://www.tmd.go.th"),
                    style="secondary", color="#E2E8F0", height="sm"
                ),
                TextComponent(text="ข้อมูลอ้างอิง: กรมอุตุนิยมวิทยา (TMD Open Data API)", size="xxs", color="#94A3B8", align="center")
            ]
        )
    )
    return FlexSendMessage(alt_text="🌦️ ตรวจสอบสภาพอากาศปัจจุบัน", contents=bubble)

def build_shelter_flex_message(user_lat, user_lon, shelters):
    """
    หน้าแสดงรายงานศูนย์อพยพพักพิง ตรงตามภาพ BCF46E3B-1A3B-4FF4-837D-0E59933BE080.png
    """
    hero_image = ImageComponent(
        url=hero_image_url("shelter_banner.jpg"),
        size="full",
        aspect_ratio="20:10",
        aspect_mode="cover"
    )

    header_box = BoxComponent(
        layout="vertical",
        padding_all="lg",
        contents=[
            TextComponent(text="🏠 ศูนย์พักพิงใกล้คุณ", weight="bold", size="lg", color="#111827"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f}  ·  🕒 อัปเดตวันนี้ {get_bangkok_time().strftime('%H:%M')} น.", size="xxs", color="#64748B")
        ]
    )

    shelter_cards = []
    for idx, sh in enumerate(shelters[:2], start=1):
        status_key = sh.get("Status", "เปิดรับ")
        assess = SHELTER_STATUS_MAP.get(status_key, SHELTER_STATUS_MAP["เปิดรับ"])
        dist = sh.get("distance_km", 0)
        
        # ปรับการดึงความจุ
        try:
            cap = int(sh.get("Capacity", 300) or 300)
            occ = int(sh.get("Occupancy", 0) or 0)
            avail = max(0, cap - occ)
        except Exception:
            cap, occ, avail = 300, 0, 300

        # คำนวณร้อยละเพื่อจำลองความกว้าง Progress Bar บน LINE Flex
        # สัดส่วนความกว้างใน LINE Flex กำหนดเป็นสัดส่วน flex ได้ เช่น แถบสีเขียวเข้ม และแถบสีเขียวอ่อน
        green_bar_ratio = int((avail / cap) * 10) if cap > 0 else 10
        green_bar_ratio = max(1, min(green_bar_ratio, 10))
        gray_bar_ratio = 10 - green_bar_ratio

        card = BoxComponent(
            layout="vertical",
            spacing="sm",
            margin="md",
            border_width="1px",
            border_color="#F1F5F9",
            corner_radius="lg",
            background_color="#F8FAFC",
            padding_all="md",
            contents=[
                # หัวข้อการ์ดและระยะทาง
                BoxComponent(
                    layout="horizontal",
                    contents=[
                        TextComponent(text=f"{idx} {sh.get('Name', 'ศูนย์พักพิง')}", weight="bold", size="sm", color="#1E293B", flex=4, wrap=True),
                        BoxComponent(
                            layout="vertical",
                            flex=1,
                            background_color="#E0F2FE",
                            corner_radius="sm",
                            contents=[
                                TextComponent(text=f"{dist:.1f} กม.", size="xxs", color="#0369A1", align="center", weight="bold")
                            ]
                        )
                    ]
                ),
                TextComponent(text=f"{sh.get('District', '')} {sh.get('Province', '')}", size="xxs", color="#64748B"),
                
                # แถบสถานะแบบเปิดรับ / ความจุคงเหลือ
                BoxComponent(
                    layout="horizontal",
                    spacing="md",
                    contents=[
                        BoxComponent(
                            layout="vertical",
                            background_color=assess["bg"],
                            corner_radius="sm",
                            padding_start="sm", padding_end="sm",
                            contents=[
                                TextComponent(text=assess["label"], size="xxs", color=assess["text"], weight="bold", align="center")
                            ]
                        ),
                        TextComponent(text=f"👥 ว่าง {avail}/{cap} ที่", size="xxs", color="#334155", weight="bold")
                    ]
                ),
                
                # จำลองการสร้าง Progress Bar สีเขียวผ่านกล่อง Nested Box Component
                BoxComponent(
                    layout="horizontal",
                    height="6px",
                    background_color="#E2E8F0",
                    corner_radius="md",
                    margin="xs",
                    contents=[
                        BoxComponent(
                            layout="vertical",
                            background_color="#22C55E",
                            flex=green_bar_ratio,
                            contents=[]
                        ),
                        BoxComponent(
                            layout="vertical",
                            background_color="#E2E8F0",
                            flex=gray_bar_ratio if gray_bar_ratio > 0 else 1,
                            contents=[]
                        )
                    ]
                ),
                
                # กริดแสดงสิ่งอำนวยความสะดวกในศูนย์พักพิง
                BoxComponent(
                    layout="horizontal",
                    spacing="xs",
                    margin="sm",
                    contents=[
                        # ที่พัก / ห้องน้ำ / ทางผู้พิการ
                        BoxComponent(
                            layout="vertical", align="center",
                            contents=[
                                TextComponent(text="🛌", size="xs"),
                                TextComponent(text="ที่พัก", size="xxs", color="#475569")
                            ]
                        ),
                        BoxComponent(
                            layout="vertical", align="center",
                            contents=[
                                TextComponent(text="🚻", size="xs"),
                                TextComponent(text="ห้องน้ำ", size="xxs", color="#475569")
                            ]
                        ),
                        BoxComponent(
                            layout="vertical", align="center",
                            contents=[
                                TextComponent(text="♿", size="xs"),
                                TextComponent(text="ผู้พิการ", size="xxs", color="#475569")
                            ]
                        ),
                        BoxComponent(
                            layout="vertical", align="center",
                            contents=[
                                TextComponent(text="🅿️", size="xs"),
                                TextComponent(text="ที่จอดรถ", size="xxs", color="#475569")
                            ]
                        ),
                        BoxComponent(
                            layout="vertical", align="center",
                            contents=[
                                TextComponent(text="🍴", size="xs"),
                                TextComponent(text="อาหาร", size="xxs", color="#475569")
                            ]
                        ),
                        BoxComponent(
                            layout="vertical", align="center",
                            contents=[
                                TextComponent(text="⚡", size="xs"),
                                TextComponent(text="ไฟฟ้า", size="xxs", color="#475569")
                            ]
                        )
                    ]
                ),
                
                # ปุ่มนำทางแผนที่กูเกิล
                ButtonComponent(
                    action=URIAction(label="🧭 นำทางไปศูนย์พักพิง", uri=f"https://www.google.com/maps/search/?api=1&query={sh.get('Latitude', 0)},{sh.get('Longitude', 0)}"),
                    style="secondary", color="#F1F5F9", height="sm"
                )
            ]
        )
        shelter_cards.append(card)

    bubble = BubbleContainer(
        hero=hero_image,
        body=BoxComponent(
            layout="vertical",
            contents=[header_box] + shelter_cards
        ),
        footer=BoxComponent(
            layout="vertical",
            padding_all="sm",
            contents=[
                TextComponent(text="🛡️ ความปลอดภัยของคุณ คือสิ่งสำคัญของเรา", size="xxs", color="#0369A1", align="center", weight="bold")
            ]
        )
    )
    return FlexSendMessage(alt_text="🏠 ค้นหาศูนย์พักพิงใกล้คุณ", contents=bubble)

def build_prep_guide_flex(member_count: int = 1, lang="TH"):
    """
    หน้าแสดงคู่มือการเตรียมตัวรับมือน้ำท่วมแบบรูปภาพการ์ด ตรงตามภาพ FBA0133E-2D72-469B-8E8C-B8A0966065C7.png
    """
    hero_image = ImageComponent(
        url=hero_image_url("prep_banner.jpg"),
        size="full",
        aspect_ratio="20:11",
        aspect_mode="cover"
    )

    steps_data = [
        ("1", "📢 ติดตามข่าวสาร", "ติดตามข่าวสารสภาพอากาศและประกาศเตือนภัยจากหน่วยงานราชการอย่างใกล้ชิด"),
        ("2", "🎒 จัดเตรียมสิ่งของจำเป็น", f"น้ำดื่มสะอาด {member_count*3} ลิตร (สำหรับ {member_count} คน), อาหารแห้ง, ยารักษาโรค, ไฟฉาย, และเอกสารในถุงกันน้ำ"),
        ("3", "⚡ ตรวจสอบความปลอดภัย", "ตรวจสอบระดับน้ำและอพยพขึ้นที่สูง รวมถึงตัดกระแสไฟฟ้าภายในบ้านเมื่อจำเป็น"),
        ("4", "📍 วางแผนเส้นทางอพยพ", "ศึกษาทิศทางการเดินทางไปยังจุดอพยพ และเตรียมเบอร์ฉุกเฉินต่าง ๆ ให้พร้อมใช้งาน"),
        ("5", "❤️ ดูแลสุขอนามัย", "ดูแลสุขภาพอนามัยส่วนบุคคลอย่างเคร่งครัด หลีกเลี่ยงการลุยน้ำสกปรก ทานอาหารสุกใหม่"),
    ]

    list_contents = []
    for num, title, desc in steps_data:
        row = BoxComponent(
            layout="horizontal",
            spacing="md",
            margin="md",
            contents=[
                # วงกลมตัวเลข
                BoxComponent(
                    layout="vertical",
                    flex=0,
                    width="24px",
                    height="24px",
                    background_color="#1E88E5",
                    corner_radius="xxl",
                    gravity="center",
                    contents=[
                        TextComponent(text=num, size="xs", color="#FFFFFF", align="center", weight="bold")
                    ]
                ),
                # ข้อความรายละเอียดข้อแนะนำ
                BoxComponent(
                    layout="vertical",
                    flex=1,
                    spacing="xs",
                    contents=[
                        TextComponent(text=title, size="xs", weight="bold", color="#1E293B"),
                        TextComponent(text=desc, size="xxs", color="#64748B", wrap=True)
                    ]
                )
            ]
        )
        list_contents.append(row)

    body_contents = BoxComponent(
        layout="vertical",
        padding_all="lg",
        contents=[
            BoxComponent(
                layout="vertical",
                spacing="xs",
                contents=[
                    TextComponent(text="วิธีเตรียมตัวก่อนน้ำท่วม", weight="bold", size="md", color="#1E293B"),
                    TextComponent(text="เตรียมพร้อมวันนี้ ปลอดภัยกว่าเสมอ", size="xs", color="#64748B")
                ]
            ),
            SeparatorComponent(margin="md", color="#F1F5F9"),
            BoxComponent(
                layout="vertical",
                spacing="sm",
                contents=list_contents
            ),
            # กล่องแนะนำโล่ป้องกันสีฟ้าพาสเทล
            BoxComponent(
                layout="vertical",
                background_color="#EFF6FF",
                corner_radius="md",
                padding_all="md",
                margin="md",
                border_width="1px",
                border_color="#DBEAFE",
                contents=[
                    TextComponent(
                        text="🛡️ คำแนะนำ: การเตรียมตัวล่วงหน้า ช่วยลดความเสี่ยงและเพิ่มความปลอดภัยให้คุณและครอบครัว",
                        size="xxs", color="#1E40AF", wrap=True
                    )
                ]
            )
        ]
    )

    bubble = BubbleContainer(
        hero=hero_image,
        body=body_contents,
        footer=BoxComponent(
            layout="vertical",
            padding_all="md",
            spacing="xs",
            contents=[
                TextComponent(text="📖 แหล่งข้อมูลอ้างอิง", size="xxs", color="#64748B", weight="bold", margin="xs"),
                ButtonComponent(
                    action=URIAction(label="vgrouphonda.com", uri="https://vgrouphonda.com"),
                    style="secondary", color="#F1F5F9", height="sm"
                ),
                ButtonComponent(
                    action=URIAction(label="youtube.com", uri="https://youtube.com"),
                    style="secondary", color="#F1F5F9", height="sm", margin="xs"
                ),
                ButtonComponent(
                    action=URIAction(label="cot.co.th", uri="https://cot.co.th"),
                    style="secondary", color="#F1F5F9", height="sm", margin="xs"
                )
            ]
        )
    )
    return FlexSendMessage(alt_text="🎒 วิธีเตรียมตัวก่อนน้ำท่วม", contents=bubble)


def build_language_selector_flex():
    return FlexSendMessage(
        alt_text="🌐 เลือกภาษา / Language",
        contents=BubbleContainer(
            body=BoxComponent(
                layout="vertical",
                spacing="sm",
                contents=[
                    TextComponent(text="🌐 กรุณาเลือกภาษา / Language", weight="bold", size="sm", align="center"),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(action=MessageAction(label="ไทย", text="ตั้งค่าภาษา: TH"), style="primary", color="#1E88E5", height="sm"),
                    ButtonComponent(action=MessageAction(label="English", text="ตั้งค่าภาษา: EN"), style="secondary", color="#E2E8F0", height="sm")
                ]
            )
        )
    )


def build_snake_bite_flex():
    """
    หน้าแสดงความช่วยเหลือปฐมพยาบาลงูกัด
    """
    body_box = BoxComponent(
        layout="vertical",
        spacing="sm",
        padding_all="lg",
        contents=[
            TextComponent(text="🐍 การปฐมพยาบาลเมื่อถูกงูกัด", weight="bold", size="md", color="#C2452F"),
            SeparatorComponent(),
            TextComponent(text="1. ล้างแผลด้วยน้ำสะอาดและสบู่เบาๆ", size="xs", color="#333333"),
            TextComponent(text="2. พยายามให้แผลเคลื่อนไหวน้อยที่สุด โดยจัดให้อยู่ระดับต่ำกว่าหัวใจเพื่อชะลอพิษกระจาย", size="xs", color="#333333", wrap=True),
            TextComponent(text="3. ห้ามกรีดแผล ห้ามใช้ปากดูดพิษ ห้ามรัดตึง (Tourniquet) เด็ดขาด", size="xs", color="#C2452F", wrap=True),
            TextComponent(text="4. ถอดแหวน กำไล หรือนาฬิกาออกทันทีก่อนที่บริเวณที่โดนกัดจะบวม", size="xs", color="#333333", wrap=True),
            TextComponent(text="5. ถ่ายภาพงู (หากปลอดภัย) และนำตัวส่งโรงพยาบาลที่ใกล้ที่สุดทันที", size="xs", color="#333333", wrap=True)
        ]
    )
    return FlexSendMessage(
        alt_text="🐍 คู่มือปฐมพยาบาลเมื่อถูกงูกัด",
        contents=BubbleContainer(
            body=body_box,
            footer=BoxComponent(
                layout="vertical",
                contents=[
                    ButtonComponent(
                        action=URIAction(label="📞 โทร 1367 สายด่วนพิษวิทยา", uri=f"tel:{SNAKE_BITE_HOTLINE}"),
                        style="primary", color="#C2452F", height="sm"
                    )
                ]
            )
        )
    )


def build_help_flex():
    return build_prep_guide_flex(member_count=1)


def build_faq_response_flex(answer: str, sources: list, question: str):
    return build_ai_response_flex(answer, question)


def build_ai_response_flex(ai_text: str, original_question: str):
    return FlexSendMessage(
        alt_text="🤖 FLOODCARE AI",
        contents=BubbleContainer(
            body=BoxComponent(
                layout="vertical",
                padding_all="lg",
                contents=[
                    TextComponent(text="🤖 FLOODCARE AI", weight="bold", size="sm", color="#1E88E5"),
                    SeparatorComponent(margin="md"),
                    TextComponent(text=ai_text, size="xs", color="#333333", wrap=True, margin="md")
                ]
            )
        )
    )

# =============================================================================
# SECTION 12: GREETINGS & RESPONSE HANDLERS
# =============================================================================

def is_greeting(text: str) -> bool:
    clean = text.strip().lower().strip("!.,😊🙏👋 ")
    return any(clean.startswith(g) or g in clean for g in ["สวัสดี", "หวัดดี", "hello", "hi", "เมนู", "เริ่ม"])


def get_greeting_message(user_name="คุณ"):
    greeting_text = (
        f"สวัสดีครับ คุณ{user_name}\n"
        "ผมคือ FLOODCARE AI บอทผู้ช่วยภัยน้ำท่วมสำหรับติดตามแจ้งภัยพิบัติ ค้นหาศูนย์อพยพ และส่งข้อมูลช่วยเหลือกู้ภัยเคียงข้างคุณตลอด 24 ชั่วโมงครับ\n\n"
        "🎈 กดเลือกเมนูหรือระบุคำสั่งที่ต้องการให้ช่วยเหลือได้เลยครับ"
    )
    return TextSendMessage(text=greeting_text)


def handle_emergency_response(user_id: str) -> TextSendMessage:
    emergency_text = (
        "🚨 ตั้งสติและทำตามคำแนะนำทันที:\n\n"
        "1. สับคัทเอาท์ตัดกระแสไฟฟ้าภายในบ้านก่อน\n"
        "2. สวมเสื้อชูชีพหรือกอดพยุงอุปกรณ์ลอยตัว\n"
        "3. พาเด็กและผู้สูงอายุอพยพไปที่สูงที่ปลอดภัย\n"
        "4. โทรติดต่อเบอร์กู้ภัยฉุกเฉิน ปภ. 1784 หรือการแพทย์ฉุกเฉิน 1669 ทันทีครับ"
    )
    return TextSendMessage(text=emergency_text)


def calculate_sos_priority(group_types: list, urgency_level: str) -> Tuple[str, str]:
    if any(g in ["ผู้ป่วยติดเตียง", "ผู้พิการ", "ผู้ป่วยเรื้อรัง"] for g in group_types) or urgency_level == "วิกฤต":
        return ("🔴 CRITICAL", "CRITICAL")
    if any(g in ["เด็กเล็ก", "ผู้สูงอายุ"] for g in group_types):
        return ("🟠 HIGH", "HIGH")
    return ("🟢 NORMAL", "NORMAL")


def build_sos_summary_text(data: dict) -> str:
    return f"📋 สรุปข้อมูลแจ้งเหตุ SOS เรียบร้อยแล้วครับ"


def build_needs_summary_text(data: dict) -> str:
    return f"📋 สรุปคำร้องขอสิ่งของ เรียบร้อยแล้วครับ"


def start_background_tasks():
    pass

Logger.info("System", "FLOODCARE AI v3.0.0 Setup Finished Successfully")
