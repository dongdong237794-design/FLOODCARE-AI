import os
import json
import math
import datetime
import time
import urllib.request
import requests
import google.generativeai as genai
from supabase import create_client, Client
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent,
    SeparatorComponent, ButtonComponent, URIAction, TextSendMessage
)

# =============================================================================
# 1. ENVIRONMENT VARIABLES
# =============================================================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RICH_MENU_ID = os.environ.get("RICH_MENU_ID")
TMD_ACCESS_TOKEN = os.environ.get("TMD_ACCESS_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# =============================================================================
# 2. STATE MANAGEMENT (in-memory for conversation flow)
# =============================================================================
USER_STATES = {}
USER_DATA = {}

# =============================================================================
# 3. SUPABASE CLIENT
# =============================================================================
_supabase_client = None

def get_supabase_client():
    """Initialize and return Supabase client (Service Role Key)"""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[Supabase] SUPABASE_URL / SUPABASE_KEY not configured")
        return None
    try:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[Supabase] Client initialized successfully")
        return _supabase_client
    except TypeError as e:
        print(
            "[Supabase] Initialization TypeError (likely a supabase-py/gotrue/httpx "
            f"version mismatch, not a credentials issue): {e}. "
            "Fix: pin compatible versions in requirements.txt, e.g. "
            "supabase==2.8.1 and gotrue==2.8.1, then reinstall/redeploy."
        )
        return None
    except Exception as e:
        print(f"[Supabase] Initialization error: {e}")
        return None

# =============================================================================
# 4. THAIWATER API CONFIGURATION
# =============================================================================
THAIWATER_V3_API = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
THAIWATER_API_BASE = "https://api.thaiwater.net/twsapi/v1.0"
THAIWATER_WEB_URL = "https://www.thaiwater.net/water/wl"

# RAM Cache
_WATER_STATIONS_CACHE = []
_WATER_STATIONS_CACHE_TIME = 0
_WATER_STATIONS_CACHE_TTL = 3600

_WEATHER_CACHE = {}
_WEATHER_CACHE_TTL = 1800

_V3_WATER_CACHE = []
_V3_WATER_CACHE_TIME = 0
_V3_WATER_CACHE_TTL = 3600

# LINE API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

# =============================================================================
# 5. GEMINI AI CONFIGURATION
# =============================================================================
gemini_model = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        gemini_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=(
                "คุณคือ FLOODCARE AI ผู้ช่วยกู้ภัยมืออาชีพประจำศูนย์ประสานงานภัยน้ำท่วมระดับชาติ\n"
                "บทบาท: ผู้นำในวิกฤตที่ใจดีแต่เด็ดขาด (Calm and Firm)\n"
                "เป้าหมาย: ให้ข้อมูลที่แม่นยำ กระชับ และช่วยผู้ประสบภัยเอาตัวรอดได้จริง\n\n"
                "[1] Data-Driven Response:\n"
                "- ข้อมูลระดับน้ำจาก ThaiWater คือข้อมูลหลัก\n"
                "- หากไม่มีในฐานข้อมูล ให้กล้ายอมรับว่า 'ไม่มีข้อมูลในระบบ'\n"
                "- ห้ามแนะนำเส้นทางที่ไม่แน่ใจ หรือยืนยันว่าปลอดภัย 100%\n\n"
                "[2] Emergency Detection:\n"
                "- คำสำคัญ: 'ช่วยด้วย' 'จะจมแล้ว' 'ไฟดูด' 'หายใจไม่ออก' 'จมน้ำ' 'ไฟฟ้าดูด'\n"
                "- หยุดการเกริ่นนำทันที ส่ง 'ขั้นตอนเอาตัวรอดทันที' + เบอร์ 1784 หรือ 1669\n\n"
                "[3] Tone of Voice (Calm and Authoritative):\n"
                "- ใช้โทน 'ใจดีแต่เด็ดขาด' ไม่ผวา ไม่เยิ่นเย้อ\n"
                "- เน้นการสั่งการเป็นขั้นตอน (1, 2, 3)\n"
                "- ใช้คำลงท้าย 'ครับ' หรือ 'นะครับ'\n\n"
                "[4] Shelter and Route Safety Rules:\n"
                "- ห้ามยืนยันว่าเส้นทางปลอดภัย 100%\n"
                "- ต้องมีประโยคเตือนเสมอ: 'โปรดใช้ความระมัดระวังในการเดินทางและสังเกตระดับน้ำจริงหน้างาน'\n\n"
                "[5] Formatting for Crisis:\n"
                "- ข้อมูลสำคัญสุดต้องอยู่ใน 3 บรรทัดแรกเสมอ\n"
                "- ห้ามใช้ตัวหนาเยอะ ใช้การเว้นบรรทัดแยกหัวข้อแทน\n"
                "- ห้ามใช้เครื่องหมายดอกจัน (*)\n"
                "- ใช้อิโมจิที่จำเป็นเท่านั้น (⚠️ 📞 🏃 🩹)\n"
                "- ความยาวข้อความไม่เกิน 10 บรรทัดต่อกลุ่ม\n\n"
                "[6] General Safety:\n"
                "- ห้ามเดาข้อมูลหรือจินตนาการสิ่งที่ไม่เป็นความจริง\n"
                "- หากข้อมูลไม่แน่ชัด ให้แสดงความห่วงใจ + แนะนำเบอร์สายด่วน\n"
                "- ให้คำตอบเป็นภาษาไทยเสมอ"
            )
        )
    except Exception as e:
        print(f"[Gemini] Initialization error: {e}")
        gemini_model = None

# =============================================================================
# 6. UTILITY FUNCTIONS
# =============================================================================
def clean_text_for_line(text):
    if not text:
        return ""
    return text.replace("**", "").replace("*", "")

def extract_number(text):
    if not text:
        return "1"
    cleaned = "".join(filter(lambda x: x.isdigit(), text))
    return cleaned if cleaned else "1"

def parse_yes_no(text):
    if not text:
        return "NO"
    text_clean = text.strip().lower()
    if any(word in text_clean for word in ["มี", "ใช่", "yes", "y", "เอส", "ตกลง"]):
        if "ไม่มี" in text_clean:
            return "NO"
        return "YES"
    return "NO"

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# =============================================================================
# 7. WEATHER FUNCTIONS (RAM Cache only - no Sheets)
# =============================================================================
def get_live_weather_scraper(lat, lon):
    """Fetch weather from TMD API with RAM cache only"""
    cache_key = f"{round(float(lat), 2)},{round(float(lon), 2)}"
    
    # Check RAM Cache
    if cache_key in _WEATHER_CACHE:
        entry = _WEATHER_CACHE[cache_key]
        if time.time() - entry["time"] < _WEATHER_CACHE_TTL:
            print(f"[Cache] RAM Hit for {cache_key}")
            return entry["data"]

    if not TMD_ACCESS_TOKEN:
        return "🌡️ อุณหภูมิ: ~28 °C\n🌧️ สภาพอากาศ: ข้อมูลพยากรณ์ทั่วไป"

    try:
        url = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at"
        params = {"lat": lat, "lon": lon, "duration": 1, "fields": "tc,rh,cond,ws10m"}
        headers = {"accept": "application/json", "authorization": f"Bearer {TMD_ACCESS_TOKEN}"}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 429:
            return "⚠️ ระบบหนาแน่น กรุณาลองใหม่ในอีก 1 นาที"
        
        response.raise_for_status()
        res_data = response.json()
        
        forecasts = res_data.get("WeatherForecasts", [])
        if not forecasts:
            return "🌡️ อุณหภูมิ: - °C\n🌧️ สภาพอากาศ: ไม่พบข้อมูลในพื้นที่"
        
        latest = forecasts[0].get("forecasts", [])[0]
        data = latest.get("data", {})
        
        temp = data.get("tc", "-")
        rh = data.get("rh", "-")
        wind = data.get("ws10m", "-")
        weather_code = data.get("cond", 0)
        
        weather_map = {
            1: "แจ่มใส", 2: "เมฆบางส่วน", 3: "เมฆมาก", 4: "ครึ้ม",
            5: "ฝนเล็กน้อย", 6: "ฝนปานกลาง", 7: "ฝนหนัก",
            8: "ฝนฟ้าคะนอง", 9: "หนาวจัด", 10: "หนาว", 11: "เย็น", 12: "ร้อนจัด"
        }
        weather_desc = weather_map.get(weather_code, "ไม่ระบุ")
        
        result_text = f"🌡️ {temp} °C | 🌧️ {weather_desc}\n💧 ชื้น {rh}% | 🍃 ลม {wind} m/s"
        
        _WEATHER_CACHE[cache_key] = {"data": result_text, "time": time.time()}
        return result_text
    
    except Exception as e:
        print(f"TMD API Error: {e}")
        return "🌡️ อุณหภูมิ: ~28 °C\n🌧️ สภาพอากาศ: ท้องฟ้าครึ้ม"


def get_live_water_scraper(lat, lon):
    """Fetch flood forecast from Open-Meteo"""
    try:
        url = f"https://flood-api.open-meteo.com/v1/flood?latitude={lat}&longitude={lon}&daily=river_discharge"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            daily = data.get("daily", {})
            discharges = daily.get("river_discharge", [])
            current_flow = discharges[-1] if discharges else 0.0
            
            status = "🟢 สถานการณ์ปกติ"
            icon = "🟢"
            simulated_height = 1.20 + (current_flow * 0.05)
            
            if current_flow >= 50.0:
                status = "🔴 อันตรายวิกฤตน้ำท่วม"
                icon = "🔴"
            elif current_flow >= 15.0:
                status = "🟡 เฝ้าระวังน้ำหลาก"
                icon = "🟡"
            
            return {
                "flow": f"{current_flow:.2f} ลบ.ม./วินาที",
                "height": f"{simulated_height:.2f} เมตร",
                "status": status,
                "icon": icon
            }
    except Exception as e:
        print(f"Water Scraper Error: {e}")
        return {
            "flow": "ไม่สามารถตรวจสอบได้",
            "height": "~1.5 เมตร",
            "status": "🟢 รอข้อมูลอัปเดต",
            "icon": "🟢"
        }


# =============================================================================
# 8. THAIWATER API (V3 Primary + V1 Fallback)
# =============================================================================
def fetch_waterlevel_v3(use_cache=True):
    """Fetch water levels from ThaiWater V3 API"""
    global _V3_WATER_CACHE, _V3_WATER_CACHE_TIME
    
    if use_cache and _V3_WATER_CACHE and (time.time() - _V3_WATER_CACHE_TIME < _V3_WATER_CACHE_TTL):
        print(f"[ThaiWater V3] Using RAM Cache (age: {int(time.time() - _V3_WATER_CACHE_TIME)}s)")
        return _V3_WATER_CACHE
    
    try:
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(THAIWATER_V3_API, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        stations = []
        # Improved parsing logic for various V3 API response structures
        if isinstance(data, dict):
            if "waterlevel_data" in data and isinstance(data["waterlevel_data"], dict):
                stations = data["waterlevel_data"].get("data", [])
            elif "data" in data and isinstance(data["data"], list):
                stations = data["data"]
            else:
                # Try common keys if direct 'data' or 'waterlevel_data' not found
                for key in ["stations", "results", "items", "waterlevel"]:
                    if key in data and isinstance(data[key], list):
                        stations = data[key]
                        break
        elif isinstance(data, list):
            stations = data
        
        print(f"[ThaiWater V3] Fetched {len(stations)} stations")
        _V3_WATER_CACHE = stations
        _V3_WATER_CACHE_TIME = time.time()
        return stations
    
    except requests.exceptions.Timeout:
        print("[ThaiWater V3] API Timeout")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[ThaiWater V3] API Error: {e}")
        return None
    except Exception as e:
        print(f"[ThaiWater V3] Unexpected Error: {e}")
        return None


def parse_v3_station(v3_item):
    """Parse V3 API response into standard format"""
    station = v3_item.get("station") or {}
    geocode = station.get("geocode") or {}
    
    def get_val(*keys, default=None):
        for k in keys:
            if k in v3_item and v3_item[k] is not None:
                val = v3_item[k]
                # Handle potential non-numeric values for water/bank levels
                if k in ["water_level", "bank_level"] and isinstance(val, str):
                    try:
                        return float(val)
                    except ValueError:
                        return None # Return None if cannot convert to float
                return val
        return default
    
    # Extract values with robust handling for missing keys and types
    station_code = get_val("station_code", "stationCode", default="N/A")
    station_name = get_val("station_name", "stationName", default="ไม่ระบุ")
    river_name = get_val("river_name", "riverName", default="-")
    province_name = get_val("province_name", "provinceName", default="-")
    latitude = get_val("latitude", "lat", default=0.0)
    longitude = get_val("longitude", "lon", default=0.0)
    water_level = get_val("water_level", "waterLevel", default=None)
    bank_level = get_val("bank_level", "bankLevel", default=None)
    measure_time = get_val("measure_time", "resultTime", "time", default="-")

    # Ensure lat/lon are floats
    try:
        latitude = float(latitude)
    except (ValueError, TypeError):
        latitude = 0.0
    try:
        longitude = float(longitude)
    except (ValueError, TypeError):
        longitude = 0.0

    return {
        "StationCode": station_code,
        "Name": station_name,
        "River": river_name,
        "Location": province_name,
        "Lat": latitude,
        "Lon": longitude,
        "WaterLevel": water_level,
        "BankLevel": bank_level,
        "Time": measure_time
    }


def fetch_thaiwater_stations_v1(use_cache=True):
    """Fetch station list from ThaiWater V1 API"""
    global _WATER_STATIONS_CACHE, _WATER_STATIONS_CACHE_TIME
    if use_cache and _WATER_STATIONS_CACHE and (time.time() - _WATER_STATIONS_CACHE_TIME < _WATER_STATIONS_CACHE_TTL):
        print(f"[ThaiWater V1] Using RAM Cache (age: {int(time.time() - _WATER_STATIONS_CACHE_TIME)}s)")
        return _WATER_STATIONS_CACHE
    
    try:
        url = f"{THAIWATER_API_BASE}/thaiwater/stations"
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        stations = []
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            stations = data["data"]
        elif isinstance(data, list):
            stations = data
        
        print(f"[ThaiWater V1] Fetched {len(stations)} stations")
        _WATER_STATIONS_CACHE = stations
        _WATER_STATIONS_CACHE_TIME = time.time()
        return stations
    except requests.exceptions.Timeout:
        print("[ThaiWater V1] API Timeout")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[ThaiWater V1] API Error: {e}")
        return None
    except Exception as e:
        print(f"[ThaiWater V1] Unexpected Error: {e}")
        return None


def get_thaiwater_stations(use_cache=True):
    """Unified function to get ThaiWater stations, prioritizing V3 then V1"""
    v3_stations = fetch_waterlevel_v3(use_cache=use_cache)
    if v3_stations:
        # Convert V3 format to a more generic format if needed, or use as is
        # For now, assuming parse_v3_station already standardizes it enough
        # Or, if V3 provides full station list, use it directly.
        # The current fetch_waterlevel_v3 gets waterlevel data, not just station list.
        # Let's assume for get_thaiwater_stations, we need a list of station metadata.
        # If V3 returns a list of waterlevel data, we can extract station info from it.
        # For simplicity, let's just return V3 data if it's available and seems like station data.
        # If V3 is just water levels, we still need V1 for station metadata.
        
        # Re-evaluating: fetch_waterlevel_v3 returns water level data, not just station metadata.
        # get_thaiwater_stations should ideally return a list of station metadata (code, name, lat, lon, etc.)
        # Let's make get_thaiwater_stations call fetch_thaiwater_stations_v1 directly for station list.
        # The V3 data will be used later for actual water levels.
        pass # Fall through to V1 for station list

    return fetch_thaiwater_stations_v1(use_cache=use_cache)


def get_thaiwater_runoff_latest(station_code):
    """Fetch latest runoff data for a specific station from ThaiWater V1 API"""
    try:
        url = f"{THAIWATER_API_BASE}/thaiwater/runoff/latest"
        params = {"station_code": station_code}
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list) and data["data"]:
            return data["data"][0] # Return the first (latest) item
        return None
    except requests.exceptions.Timeout:
        print(f"[ThaiWater V1 Runoff] API Timeout for {station_code}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[ThaiWater V1 Runoff] API Error for {station_code}: {e}")
        return None
    except Exception as e:
        print(f"[ThaiWater V1 Runoff] Unexpected Error for {station_code}: {e}")
        return None


# =============================================================================
# 9. WATER LEVEL SITUATION & TREND
# =============================================================================
def calculate_situation(water_level, bank_level):
    """Calculate water level situation based on water_level and bank_level"""
    try:
        wl = float(water_level) if water_level is not None else None
        bl = float(bank_level) if bank_level is not None else None
    except (ValueError, TypeError):
        return "ไม่มีข้อมูล"

    if wl is None:
        return "ไม่มีข้อมูล"

    if bl is None or bl <= 0:
        # If bank level is unknown or invalid, use absolute water level thresholds
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
    else: return "น้อยวิกฤต"


def determine_trend(current_wl, previous_wl, tolerance=0.01):
    try:
        cwl = float(current_wl) if current_wl is not None else None
        pwl = float(previous_wl) if previous_wl is not None else None
    except (ValueError, TypeError):
        return "คงที่"
    
    if cwl is None or pwl is None:
        return "คงที่"
    
    diff = cwl - pwl
    if abs(diff) <= tolerance: return "คงที่"
    elif diff > 0: return "เพิ่มขึ้น"
    else: return "ลดลง"


# =============================================================================
# 10. FIND NEAREST WATER STATIONS
# =============================================================================
def find_nearest_water_stations(user_lat, user_lon, max_stations=3, max_distance_km=50):
    """Find nearest water stations with API fallback chain"""
    stations = get_thaiwater_stations(use_cache=True)
    if not stations:
        return []
    
    # Filter out stations without valid lat/lon before calculating distance
    valid_stations = []
    for st in stations:
        try:
            st_lat = float(st.get("latitude", st.get("Lat", 0)))
            st_lon = float(st.get("longitude", st.get("Lon", 0)))
            if st_lat != 0.0 and st_lon != 0.0: # Exclude stations with default 0,0 coords
                st["latitude"] = st_lat
                st["longitude"] = st_lon
                valid_stations.append(st)
        except (ValueError, TypeError):
            continue

    for st in valid_stations:
        st["distance_km"] = calculate_distance(user_lat, user_lon, st["latitude"], st["longitude"])
    
    nearby = [s for s in valid_stations if s["distance_km"] <= max_distance_km]
    nearby.sort(key=lambda x: x["distance_km"])
    
    result = []
    for st in nearby[:max_stations]:
        # For V1 fallback, we need to fetch runoff data separately
        runoff_data = get_thaiwater_runoff_latest(st["stationCode"])
        if runoff_data:
            st["water_level"] = runoff_data.get("water_level", {}).get("value")
            st["bank_level"] = runoff_data.get("bank_level", {}).get("value")
            st["discharge"] = runoff_data.get("discharge", {}).get("value")
            st["measure_time"] = runoff_data.get("water_level", {}).get("time", "-")
        else:
            # If no runoff data, ensure fields are present with default values
            st["water_level"] = None
            st["bank_level"] = None
            st["discharge"] = None
            st["measure_time"] = "-"
        result.append(st)
    
    return result


def assess_water_level_status(water_level_value, bank_level_value=None):
    """
    ประเมินสถานะระดับน้ำพร้อมกำหนดสีสำหรับ Status Pill ตาม UI Specs
    """
    if water_level_value is None:
        return {
            "status": "ไม่มีข้อมูล", 
            "color": "#1F2937", "bg_color": "#F3F4F6", "icon": "⚪",
            "diff_text": "-", "advice": "ไม่สามารถประเมินได้"
        }
    
    try:
        wl = float(water_level_value)
        bl = float(bank_level_value) if bank_level_value not in [None, "-", ""] else 0
        diff = bl - wl
        diff_text = f"{abs(diff):.2f}"
    except (ValueError, TypeError):
        return {
            "status": "ข้อมูลไม่ถูกต้อง", 
            "color": "#1F2937", "bg_color": "#F3F4F6", "icon": "⚪",
            "diff_text": "-", "advice": "ไม่สามารถประเมินได้"
        }
    
    if bl <= 0:
        if wl >= 3.0:
            return {
                "status": "วิกฤต", "color": "#B91C1C", "bg_color": "#FEE2E2", "icon": "🔴",
                "diff_text": "-", "advice": "⚠️ อพยพทันที!"
            }
        return {
            "status": "ปกติ", "color": "#15803D", "bg_color": "#DCFCE7", "icon": "🟢",
            "diff_text": "-", "advice": "ติดตามสถานการณ์"
        }
    
    ratio = wl / bl
    
    if wl >= bl:
        return {
            "status": "วิกฤต", "color": "#B91C1C", "bg_color": "#FEE2E2", "icon": "🔴",
            "diff_text": f"-{abs(diff):.2f}", "advice": "⚠️ อพยพทันที! ระดับน้ำล้นตลิ่ง"
        }
    elif ratio >= 0.70:
        return {
            "status": "มาก", "color": "#0369A1", "bg_color": "#E0F2FE", "icon": "🔵",
            "diff_text": diff_text, "advice": "ระดับน้ำค่อนข้างสูง"
        }
    elif ratio >= 0.30:
        return {
            "status": "ปกติ", "color": "#15803D", "bg_color": "#DCFCE7", "icon": "🟢",
            "diff_text": diff_text, "advice": "ระดับน้ำปกติ"
        }
    elif ratio >= 0.10:
        return {
            "status": "น้อย", "color": "#9A3412", "bg_color": "#FEF9C3", "icon": "🟡",
            "diff_text": diff_text, "advice": "ระดับน้ำน้อย"
        }
    else:
        return {
            "status": "น้อยวิกฤต", "color": "#9A3412", "bg_color": "#FFEDD5", "icon": "🟠",
            "diff_text": diff_text, "advice": "ระดับน้ำน้อยวิกฤต"
        }


# =============================================================================
# 11. WATER DATA SYNC TO SUPABASE (TRUNCATE + INSERT)
# =============================================================================
def get_water_data_from_api():
    """
    Fetch water level data from APIs.
    Returns: list of dict with station data
    """
    results = []
    
    # Strategy 1: ThaiWater V3 API
    print("[LazySync] Trying ThaiWater V3 API...")
    v3_data = fetch_waterlevel_v3(use_cache=False) # Always fetch fresh for sync
    
    if v3_data and len(v3_data) > 0:
        print(f"[LazySync] V3 API success with {len(v3_data)} records")
        for item in v3_data:
            try:
                parsed = parse_v3_station(item)
                code = parsed["StationCode"]
                wl = parsed["WaterLevel"]
                bl = parsed["BankLevel"]
                situation = calculate_situation(wl, bl)
                trend = "คงที่"  # Will be updated after comparing with previous data
                
                results.append({
                    "StationCode": code,
                    "Name": parsed["Name"],
                    "River": parsed["River"],
                    "Location": parsed["Location"],
                    "Lat": parsed["Lat"],
                    "Lon": parsed["Lon"],
                    "WaterLevel": wl if wl is not None else None,
                    "BankLevel": bl if bl is not None else None,
                    "Situation": situation,
                    "Trend": trend,
                    "Time": parsed["Time"]
                })
            except Exception as e:
                print(f"[LazySync V3] Parse error: {e}")
                continue
        
        print(f"[LazySync] V3 parsed: {len(results)} stations")
        if len(results) > 50: # If V3 provides enough data, use it and skip V1 fallback
            return results
    
    # Strategy 2: ThaiWater V1 API (Fallback)
    print("[LazySync] V3 insufficient or failed, falling back to V1 API...")
    stations_v1_metadata = get_thaiwater_stations(use_cache=False) # Fetch fresh station metadata
    if not stations_v1_metadata:
        print("[LazySync] No stations available from V1 API")
        return results # Return whatever V3 managed to get, or empty list
    
    # Clear results if V3 was insufficient and we are now relying on V1
    if len(results) <= 50: # If V3 didn't provide enough, reset and use V1 fully
        results = []

    # Use ThreadPoolExecutor for faster fetching of V1 runoff data
    # Note: This might still hit rate limits if not careful. Original code had time.sleep(0.05)
    # For simplicity, let's keep it sequential for now or add a proper rate limiter.
    
    # For now, let's process V1 sequentially with a small delay
    for i, st in enumerate(stations_v1_metadata):
        runoff = get_thaiwater_runoff_latest(st["stationCode"])
        time.sleep(0.05)  # Rate limiting
        
        wl_value = None
        bl_value = None
        measure_time = "-"
        code = st["stationCode"]
        
        if runoff:
            wl_data = runoff.get("water_level", {})
            bl_data = runoff.get("bank_level", {})
            
            wl_value = wl_data.get("value")
            measure_time = wl_data.get("time", "-")
            bl_value = bl_data.get("value")
        
        situation = calculate_situation(wl_value, bl_value)
        trend = "คงที่"
        
        results.append({
            "StationCode": code,
            "Name": st.get("stationName", "ไม่ระบุ"),
            "River": st.get("riverName", "-"),
            "Location": st.get("provinceName", "-"),
            "Lat": float(st.get("latitude", 0)) if st.get("latitude") is not None else 0.0,
            "Lon": float(st.get("longitude", 0)) if st.get("longitude") is not None else 0.0,
            "WaterLevel": float(wl_value) if wl_value not in [None, "-", ""] else None,
            "BankLevel": float(bl_value) if bl_value not in [None, "-", ""] else None,
            "Situation": situation,
            "Trend": trend,
            "Time": measure_time
        })
        
        if (i + 1) % 100 == 0:
            print(f"[LazySync V1] Processed {i + 1}/{len(stations_v1_metadata)} stations")
    
    print(f"[LazySync] Total processed: {len(results)} stations")
    return results


def sync_water_levels_to_supabase():
    """
    Sync water levels to Supabase using TRUNCATE + INSERT strategy.
    This deletes all old data and inserts fresh data for maximum performance.
    """
    supabase = get_supabase_client()
    if not supabase:
        print("[Supabase Water] Client not available")
        return False
    
    try:
        # Fetch from API
        data = get_water_data_from_api()
        if not data:
            print("[Supabase Water] No data from API")
            return False
        
        # Prepare rows for Supabase
        rows_to_insert = []
        for st in data:
            situation_text = st.get("Situation", "ปกติ")
            # Ensure situation_text is a string, not a dict from assess_water_level_status
            if isinstance(situation_text, dict):
                situation_text = situation_text.get("status", "ปกติ")
            
            wl_val = st.get("WaterLevel")
            bl_val = st.get("BankLevel")
            
            rows_to_insert.append({
                "station_code": st.get("StationCode"),
                "name": st.get("Name"),
                "river": st.get("River"),
                "location": st.get("Location"),
                "latitude": float(st.get("Lat")) if st.get("Lat") is not None else None,
                "longitude": float(st.get("Lon")) if st.get("Lon") is not None else None,
                "water_level": float(wl_val) if wl_val not in [None, "-", ""] else None,
                "bank_level": float(bl_val) if bl_val not in [None, "-", ""] else None,
                "situation": situation_text,
                "trend": st.get("Trend", "คงที่"),
                "measure_time": st.get("Time"),
                "updated_at": datetime.datetime.now().isoformat()
            })
        
        # Step 1: TRUNCATE (delete all existing data)
        print("[Supabase Water] Truncating old data...")
        try:
            # Use rpc for truncate for better control and to avoid potential issues with .delete().neq()
            # Assuming a 'truncate_table' RPC function exists in Supabase for 'water_levels'
            # If not, the .delete().neq() might work, but RPC is safer for full truncate.
            # For now, keep the original logic but note the potential RPC alternative.
            supabase.table("water_levels").delete().neq("station_code", "").execute()
        except Exception as e:
            print(f"[Supabase Water] Truncate warning (may be empty table or permission issue): {e}")
            # Fallback to RPC if direct delete fails, assuming RPC 'truncate_water_levels' exists
            try:
                supabase.rpc("truncate_water_levels").execute()
                print("[Supabase Water] Truncated via RPC")
            except Exception as rpc_e:
                print(f"[Supabase Water] RPC Truncate also failed: {rpc_e}")
                # If both fail, log and continue, hoping insert will handle conflicts or user fixes manually
        
        # Step 2: INSERT new data in chunks
        print(f"[Supabase Water] Inserting {len(rows_to_insert)} new records...")
        chunk_size = 200
        inserted_count = 0
        
        for i in range(0, len(rows_to_insert), chunk_size):
            chunk = rows_to_insert[i:i + chunk_size]
            try:
                supabase.table("water_levels").insert(chunk).execute()
                inserted_count += len(chunk)
                print(f"[Supabase Water] Inserted chunk {i//chunk_size + 1}: {len(chunk)} records")
            except Exception as e:
                print(f"[Supabase Water] Chunk insert error: {e}")
                # Try inserting one by one for failed chunks to identify problematic rows
                for row in chunk:
                    try:
                        supabase.table("water_levels").insert(row).execute()
                        inserted_count += 1
                    except Exception as e2:
                        print(f"[Supabase Water] Single insert error for {row.get('station_code')}: {e2}")
        
        print(f"[Supabase Water] Sync completed: {inserted_count}/{len(rows_to_insert)} stations")
        
        # Store last sync time in a metadata table or update a specific row
        try:
            supabase.table("sync_metadata").upsert({
                "id": "water_levels_last_sync",
                "last_sync": datetime.datetime.now().isoformat(),
                "record_count": inserted_count
            }, on_conflict="id").execute()
        except Exception as e:
            print(f"[Supabase Water] Metadata update warning: {e}")
        
        return inserted_count > 0
    
    except Exception as e:
        print(f"[Supabase Water] Sync error: {e}")
        return False


def get_water_data_from_supabase(user_lat=None, user_lon=None, limit=2000):
    """
    Get water levels from Supabase.
    If lat/lon provided, calculate distance and return the TRUE nearest stations.

    แก้ไขตามคำขอ: "ที่ถูกสุ่มมา ไม่ต้องสุ่มแต่ให้ไล่อ่าน"
    - เมื่อมีพิกัดผู้ใช้ → ทำ FULL PAGINATION (ไล่อ่านทุกแถวในตาราง)
    - ไม่ใช้ limit แบบสุ่ม ไม่พึ่ง updated_at ที่ timestamp เหมือนกัน
    - รับประกันได้ 100% ว่าได้พิจารณาสถานีทุกแห่ง → ได้สถานีที่ใกล้จริงที่สุด
    """
    supabase = get_supabase_client()
    if not supabase:
        return []
    
    try:
        records = []
        if user_lat is not None and user_lon is not None:
            # Full sequential scan (ไล่อ่านทุกสถานี ไม่สุ่ม ไม่ตัดขาด)
            offset = 0
            page_size = 1000
            while True:
                resp = supabase.table("water_levels").select("*").range(offset, offset + page_size - 1).execute()
                batch = resp.data or []
                records.extend(batch)
                if len(batch) < page_size:
                    break
                offset += page_size
            print(f"[Supabase Water] Full scan for nearest search: loaded {len(records)} stations")
        else:
            response = supabase.table("water_levels").select("*").order("updated_at", desc=True).limit(limit).execute()
            records = response.data or []
        
        if user_lat is not None and user_lon is not None:
            for rec in records:
                try:
                    rec["distance_km"] = calculate_distance(
                        user_lat, user_lon,
                        float(rec.get("latitude", 0) or 0),
                        float(rec.get("longitude", 0) or 0)
                    )
                except (ValueError, TypeError):
                    rec["distance_km"] = 9999 # Assign a large distance if lat/lon are invalid
            records.sort(key=lambda x: x.get("distance_km", 9999))
            return records[:3]  # Return the 3 truly nearest stations
        
        return records
    except Exception as e:
        print(f"[Supabase Water] Query error: {e}")
        return []


def get_last_sync_time():
    """Get last sync timestamp from metadata"""
    supabase = get_supabase_client()
    if not supabase:
        return None
    try:
        response = supabase.table("sync_metadata").select("*").eq("id", "water_levels_last_sync").limit(1).execute()
        if response.data and len(response.data) > 0:
            return response.data[0].get("last_sync")
    except Exception as e:
        print(f"[Supabase] Get sync time error: {e}")
    return None


# =============================================================================
# 12. USER REGISTRATION (SUPABASE ONLY)
# =============================================================================
def is_user_registered(user_id):
    """
    Check if user is registered in Supabase.
    Returns: (is_registered: bool, first_name, last_name, phone)
    """
    supabase = get_supabase_client()
    if not supabase:
        return False, None, None, None
    try:
        response = supabase.table("users").select("first_name, last_name, phone").eq("user_id", str(user_id)).limit(1).execute()
        if response.data and len(response.data) > 0:
            user_data = response.data[0]
            return True, user_data.get("first_name"), user_data.get("last_name"), user_data.get("phone")
    except Exception as e:
        print(f"[Supabase User] Check registration error: {e}")
    return False, None, None, None


def register_user(user_id=None, first_name=None, last_name=None, phone=None):
    """Register user in Supabase"""
    supabase = get_supabase_client()
    if supabase and user_id:
        try:
            # Check if user already exists to avoid duplicate inserts
            is_reg, _, _, _ = is_user_registered(user_id)
            if is_reg:
                print(f"[Supabase User] User {user_id} already registered, updating.")
                supabase.table("users").update({
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone": phone,
                    "updated_at": datetime.datetime.now().isoformat()
                }).eq("user_id", str(user_id)).execute()
            else:
                supabase.table("users").insert({
                    "user_id": str(user_id),
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone": phone,
                    "created_at": datetime.datetime.now().isoformat(),
                    "updated_at": datetime.datetime.now().isoformat()
                }).execute()
            print("[Supabase] User registered/updated successfully")
            return True
        except Exception as e:
            print(f"[Supabase User] Registration error: {e}")
    return False


# =============================================================================
# 13. FLEX MESSAGE BUILDERS
# =============================================================================
def build_water_level_text_report(user_lat, user_lon, timestamp, stations, weather_info, water_flow):
    """Builds a text report for water levels and weather."""
    report_lines = [
        f"รายงานสถานการณ์น้ำและสภาพอากาศ ณ {timestamp}",
        "",
        f"📍 พิกัดของคุณ: {user_lat:.4f}, {user_lon:.4f}",
        "",
        weather_info, # e.g., 🌡️ 28 °C | 🌧️ ท้องฟ้าครึ้ม
        f"ปริมาณน้ำในแม่น้ำใกล้เคียง: {water_flow['flow']} (คาดการณ์)",
        f"ระดับน้ำคาดการณ์: {water_flow['height']} (คาดการณ์)",
        f"สถานะน้ำคาดการณ์: {water_flow['status']}",
        ""
    ]

    if stations:
        report_lines.append("สถานีวัดระดับน้ำใกล้เคียง:")
        for i, st in enumerate(stations[:3]): # Limit to top 3 for text report
            wl_val = st.get("water_level")
            bl_val = st.get("bank_level")
            assessment = assess_water_level_status(wl_val, bl_val)
            
            report_lines.append(f"{i+1}. {st.get('stationName', st.get('Name', 'ไม่ระบุ'))} ({st.get('distance_km', 0):.2f} กม.)")
            report_lines.append(f"   ระดับน้ำ: {wl_val if wl_val is not None else '-'} ม. / ตลิ่ง: {bl_val if bl_val is not None else '-'} ม.")
            report_lines.append(f"   สถานะ: {assessment['status']} | แนวโน้ม: {st.get('trend', 'คงที่')}")
            report_lines.append(f"   คำแนะนำ: {assessment['advice']}")
            report_lines.append("")
    else:
        report_lines.append("ไม่พบสถานีวัดระดับน้ำใกล้เคียงในระยะ 50 กม.")
        report_lines.append("โปรดตรวจสอบข้อมูลจากแหล่งอื่น หรือติดต่อ 1784")

    report_lines.append("แหล่งข้อมูล: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater) และ Open-Meteo")
    return "\n".join(report_lines)


def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, weather_info, water_flow):
    """
    Builds a minimalist Flex Message for water levels with Status Pills.
    """
    header_box = BoxComponent(
        layout="vertical",
        contents=[
            TextComponent(text="🌊 รายงานระดับน้ำจากสถานีใกล้คุณ", weight="bold", size="md", color="#1A1A1A"),
            BoxComponent(
                layout="vertical",
                margin="md",
                spacing="xs",
                contents=[
                    TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f}", size="xs", color="#666666"),
                    TextComponent(text=f"🕒 อัปเดตวันนี้ {timestamp}", size="xs", color="#666666")
                ]
            ),
            SeparatorComponent(margin="lg", color="#E0E0E0")
        ]
    )

    stations_box = BoxComponent(
        layout="vertical",
        spacing="lg",
        margin="lg",
        contents=[]
    )

    if stations:
        for st in stations[:3]:
            distance = st.get("distance_km", 0)
            wl = st.get("water_level")
            bl = st.get("bank_level")
            
            wl_display = f"{wl:.2f}" if wl is not None else "-"
            bl_display = f"{bl:.2f}" if bl is not None else "-"

            assessment = assess_water_level_status(wl, bl)
            
            station_card = BoxComponent(
                layout="vertical",
                contents=[
                    # Station Name & Distance
                    TextComponent(
                        text=f"{st.get('stationName', st.get('Name', 'ไม่ระบุ'))} (ห่าง {distance:.2f} กม.)",
                        size="sm", color="#111827", weight="bold"
                    ),
                    # Status Pill Row
                    BoxComponent(
                        layout="horizontal",
                        margin="md",
                        spacing="sm",
                        contents=[
                            # Status Pill
                            BoxComponent(
                                layout="vertical",
                                background_color=assessment["bg_color"],
                                corner_radius="999px",
                                padding_start="12px", padding_end="12px",
                                padding_top="2px", padding_bottom="2px",
                                flex=0,
                                contents=[
                                    TextComponent(
                                        text=f"{assessment['icon']} {assessment['status']}",
                                        size="xs", color=assessment["color"], weight="bold", align="center"
                                    )
                                ]
                            ),
                            # Advice/Short Desc
                            TextComponent(
                                text=assessment["advice"],
                                size="xs", color="#4B5563", gravity="center"
                            )
                        ]
                    ),
                    # Data Row
                    BoxComponent(
                        layout="vertical",
                        margin="sm",
                        contents=[
                            TextComponent(
                                text=f"ระดับน้ำ: {wl_display} ม. | ตลิ่ง: {bl_display} ม.",
                                size="xs", color="#6B7280"
                            ),
                            BoxComponent(
                                layout="horizontal",
                                contents=[
                                    TextComponent(text="ต่ำกว่าตลิ่ง: ", size="xs", color="#6B7280", flex=0),
                                    TextComponent(text=f"{assessment['diff_text']} ม.", size="xs", color="#111827", weight="bold")
                                ]
                            )
                        ]
                    )
                ]
            )
            stations_box.contents.append(station_card)
    else:
        stations_box.contents.append(
            TextComponent(
                text="ไม่พบสถานีวัดระดับน้ำใกล้เคียงในระยะ 50 กม.",
                size="sm", color="#6B7280", align="center"
            )
        )
    
    footer_box = BoxComponent(
        layout="vertical",
        margin="xl",
        contents=[
            SeparatorComponent(color="#E0E0E0"),
            TextComponent(
                text="📌 อ้างอิง: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)",
                size="xxs", color="#9CA3AF", margin="md"
            ),
            ButtonComponent(
                style="link",
                height="sm",
                action=URIAction(label="[ 🔗 ดูข้อมูลเพิ่มเติมที่ ThaiWater ]", uri=THAIWATER_WEB_URL),
                color="#2563EB"
            ),
            ButtonComponent(
                style="link",
                height="sm",
                action=URIAction(label="[ 🌦️ ตรวจสอบสภาพอากาศ (TMD) ]", uri="https://www.tmd.go.th/"),
                color="#0284C7"
            )
        ]
    )
    
    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=[
                header_box,
                stations_box,
                footer_box
            ],
            padding_all="xl"
        )
    )
    
    return FlexSendMessage(alt_text="รายงานระดับน้ำรายพิกัด", contents=bubble)


# =============================================================================
# 14. GREETING & QUICK INFO
# =============================================================================
GREETING_KEYWORDS = [
    "สวัสดี", "สวัสดีครับ", "สวัสดีค่ะ", "สวัสดีคับ", "สวัสดีคะ",
    "หวัดดี", "หวัดดีครับ", "หวัดดีค่ะ",
    "ดีครับ", "ดีค่ะ", "หวัดดีจ้า",
    "hello", "hi", "hey",
    "good morning", "good afternoon", "good evening",
    "เริ่ม", "start", "menu", "เมนู"
]


def is_greeting(text):
    if not text:
        return False
    
    clean = text.strip().lower()
    clean = clean.strip("!.,😊🙏👋 ")
    
    if not clean:
        return False
    
    if clean in [kw.lower() for kw in GREETING_KEYWORDS]:
        return True
    
    words = clean.split()
    if len(words) <= 4 and words:
        first_word = words[0]
        for kw in GREETING_KEYWORDS:
            if first_word.startswith(kw.lower()) or kw.lower().startswith(first_word):
                return True
    
    return False


def get_greeting_message(user_name="คุณ"):
    text = (
        f"สวัสดี คุณ {user_name}\n"
        "ผมคือ FLOODCARE AI\n"
        "แชทบอทอัจฉริยะสำหรับ ติดตามและพยากรณ์ระดับน้ำ แจ้งเหตุฉุกเฉิน และช่วยเหลือผู้ประสบภัยน้ำท่วม\n"
        "🔍 ผมช่วยคุณได้\n"
        "1. เบอร์โทรฉุกเฉิน\n"
        "2. SOS แจ้งเหตุฉุกเฉิน\n"
        "3. ค้นหาศูนย์อพยพ\n"
        "4. ตรวจสอบระดับน้ำตรวจสอบข้อมูลระดับน้ำ\n"
        "5. แจ้งความต้องการหรือขอความช่วยเหลือด้านต่าง ๆ\n"
        "6. สอบถามข้อมูลจาก AI\n\n"
        "🤝 ติดต่อและช่วยเหลือผู้ประสบภัยน้ำท่วม\n"
        "ผมพร้อมตอบทุกคำถามเกี่ยวกับสถานการณ์น้ำได้ตลอดเวลาครับ 💧😊"
    )
    return TextSendMessage(text=text)


def handle_greeting_logic(event):
    user_id = event.source.user_id
    profile = None
    if line_bot_api: # Add None check for line_bot_api
        try:
            profile = line_bot_api.get_profile(user_id)
        except Exception as e:
            print(f"[LINE] Failed to get profile: {e}")
            pass
    
    user_name = profile.display_name if profile else "คุณ"
    greeting_msg = get_greeting_message(user_name)
    
    if line_bot_api: # Add None check before replying
        try:
            line_bot_api.reply_message(event.reply_token, greeting_msg)
        except Exception as e:
            print(f"[LINE] Failed to reply greeting: {e}")
    else:
        print("[LINE] line_bot_api not initialized, cannot send greeting.")


# =============================================================================
# 15. SOS PRIORITY CALCULATION
# =============================================================================
def calculate_sos_priority(group_types, urgency_level):
    gt = [g.lower() for g in group_types] if group_types else []
    ul = urgency_level.lower() if urgency_level else ""
    
    if any(k in g for g in gt for k in ["บาดเจ็บ", "ผู้ป่วย", "พิการ"]):
        return ("🔴 CRITICAL (เร่งด่วนวิกฤตสูงสุด)", "CRITICAL")
    if "วิกฤต" in ul:
        return ("🔴 CRITICAL (เร่งด่วนวิกฤตสูงสุด)", "CRITICAL")
    if "ขาดแคลน" in ul:
        return ("🔴 CRITICAL (เร่งด่วนวิกฤตสูงสุด)", "CRITICAL")
    
    if any(k in g for g in gt for k in ["เด็ก", "ชรา", "เด็กเล็ก"]):
        return ("🟠 HIGH (ความเสี่ยงสูง)", "HIGH")
    if "สูง" in ul:
        return ("🟠 HIGH (ความเสี่ยงสูง)", "HIGH")
    
    return ("🟢 NORMAL (สถานการณ์ปกติ)", "NORMAL")


def generate_case_id():
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    random_suffix = datetime.datetime.now().strftime("%f")[:4]
    return f"SOS-{today_str}-{random_suffix}"


def send_line_notification(user_id, message):
    if not line_bot_api:
        print("[LINE] line_bot_api not initialized")
        return False
    try:
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
        print(f"[LINE] Notification sent to {user_id}")
        return True
    except Exception as e:
        print(f"[LINE] Failed to send notification: {e}")
        return False


# =============================================================================
# 16. TYPING INDICATOR (LINE Loading Animation)
# =============================================================================
LINE_LOADING_ANIMATION_URL = "https://api.line.me/v2/bot/chat/loading/start"


def show_loading_animation(user_id, loading_seconds=15):
    """
    Show typing indicator (3 dots animation) via LINE API.
    loading_seconds: 5-60 seconds (per LINE spec)
    """
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[TypingIndicator] LINE_CHANNEL_ACCESS_TOKEN not configured.")
        return False
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
        }
        payload = {
            "chatId": user_id,
            "loadingSeconds": max(5, min(loading_seconds, 60))
        }
        resp = requests.post(LINE_LOADING_ANIMATION_URL, headers=headers, json=payload, timeout=5)
        if resp.status_code != 202:
            print(f"[TypingIndicator] Unexpected status {resp.status_code}: {resp.text}")
            return False
        return True
    except Exception as e:
        print(f"[TypingIndicator] Failed: {e}")
        return False


# =============================================================================
# 17. USER NEEDS MANAGEMENT (SUPABASE ONLY)
# =============================================================================
def save_user_need(user_id=None, timestamp=None, lat=None, lon=None, category=None, details=None, urgency=None):
    """Save user need to Supabase"""
    supabase = get_supabase_client()
    if supabase and user_id:
        try:
            supabase.table("user_needs").insert({
                "timestamp": timestamp or datetime.datetime.now().isoformat(),
                "user_id": str(user_id),
                "latitude": float(lat) if lat is not None else None,
                "longitude": float(lon) if lon is not None else None,
                "category": category,
                "details": details,
                "urgency": urgency,
                "status": "PENDING"
            }).execute()
            print("[Supabase] User need saved successfully")
            return True
        except Exception as e:
            print(f"[Supabase UserNeeds] Error: {e}")
    return False


def get_all_user_needs():
    """Get all user needs from Supabase"""
    supabase = get_supabase_client()
    if supabase:
        try:
            response = supabase.table("user_needs").select("*").order("timestamp", desc=True).limit(500).execute()
            if response.data:
                return response.data
        except Exception as e:
            print(f"[Supabase UserNeeds] Select error: {e}")
    return []


# =============================================================================
# 18. SHELTER VACANCY CHECK
# =============================================================================
def check_shelter_vacancy(capacity, occupancy):
    try:
        cap = int(capacity) if capacity is not None else 100
        occ = int(occupancy) if occupancy is not None else 0
    except (ValueError, TypeError):
        cap = 100
        occ = 0
    remaining = cap - occ
    if remaining <= 0:
        return "🔴 เต็มแล้ว - โปรดเลี่ยงไปจุดอื่น"
    elif occ >= (cap * 0.8):
        return f"🟡 ใกล้เต็ม (ว่าง {remaining} ที่)"
    else:
        return f"🟢 มีที่ว่าง (ว่าง {remaining} ที่)"


# =============================================================================
# 19. EMERGENCY CONTACTS (Static Data - no Sheets needed)
# =============================================================================
EMERGENCY_CONTACTS = [
    {"name": "ปภ. (กรมป้องกันและบรรเทาสาธารณภัย)", "role": "รับแจ้งเหตุเตือนภัยและช่วยเหลืออุทกภัยสายด่วน", "phone": "1784"},
    {"name": "สพฉ. (สถาบันการแพทย์ฉุกเฉินแห่งชาติ)", "role": "รับส่งต่อผู้ป่วยและเจ็บป่วยฉุกเฉินทางการแพทย์", "phone": "1669"},
    {"name": "กู้ภัยทางน้ำ", "role": "ช่วยเหลือประสานงานทางน้ำ", "phone": "1196"},
    {"name": "ตำรวจทางหลวง", "role": "ประสานงานความช่วยเหลือเส้นทางน้ำท่วมและดินถล่ม", "phone": "1193"},
]


def get_emergency_contacts():
    """Return emergency contacts list"""
    lines = []
    for contact in EMERGENCY_CONTACTS:
        lines.append(f"🚨 {contact['name']} ({contact['role']})\n📞 โทร: {contact['phone']}")
    return "\n\n".join(lines)


# =============================================================================
# 20. AI LOGS (SUPABASE ONLY)
# =============================================================================
def log_ai_chat(user_id, question, answer, timestamp=None):
    """Log AI chat to Supabase for analytics"""
    supabase = get_supabase_client()
    if supabase:
        try:
            ts = timestamp or datetime.datetime.now().isoformat()
            supabase.table("ai_logs").insert({
                "timestamp": ts,
                "user_id": str(user_id),
                "question": question,
                "answer": answer
            }).execute()
        except Exception as e:
            print(f"[Supabase AI Logs] Error: {e}")

def log_user_question(question_text):
    """Log user questions to a 'popular_questions' table in Supabase and update count."""
    supabase = get_supabase_client()
    if supabase:
        try:
            # Check if question already exists
            response = supabase.table("popular_questions").select("id, count").eq("question_text", question_text).execute()
            if response.data:
                # Update count if exists
                question_id = response.data[0]["id"]
                current_count = response.data[0]["count"]
                supabase.table("popular_questions").update({"count": current_count + 1}).eq("id", question_id).execute()
            else:
                # Insert new question if not exists
                supabase.table("popular_questions").insert({"question_text": question_text, "count": 1}).execute()
        except Exception as e:
            print(f"[Supabase] Error logging popular question: {e}")

def get_popular_questions(limit=5):
    """Fetch the top N popular questions from Supabase."""
    supabase = get_supabase_client()
    if supabase:
        try:
            response = supabase.table("popular_questions").select("question_text").order("count", desc=True).limit(limit).execute()
            return [item["question_text"] for item in response.data]
        except Exception as e:
            print(f"[Supabase] Error fetching popular questions: {e}")
    return []


# =============================================================================
# 21. WEB RESEARCH (สำหรับตัวเลือก B - ค้นหาข้อมูลจากอินเทอร์เน็ต)
# =============================================================================
def web_research(query, max_results=5):
    """
    ค้นหาข้อมูลจากอินเทอร์เน็ตแบบง่าย (ใช้ DuckDuckGo Instant Answer API)
    คืนค่า dict ที่มี 'summary' และ 'links'
    """
    try:
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        
        results = []

        # Abstract (สรุปหลักจาก Wikipedia หรือแหล่งที่มา)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", query),
                "snippet": data.get("AbstractText"),
                "url": data.get("AbstractURL", "")
            })

        # Related Topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", ""),
                    "url": topic.get("FirstURL", "")
                })

        if not results:
            return {
                "summary": f"ไม่พบข้อมูลที่เกี่ยวข้องกับ \"{query}\" ในขณะนี้",
                "links": ""
            }

        # สร้างข้อความตอบกลับ
        summary = f"🔍 ผลการค้นหา: {query}\n\n"
        links_text = ""

        for i, item in enumerate(results[:4], 1):
            if item.get("url"):
                links_text += f"{i}. {item['title']}\n   {item['url']}\n\n"

        return {
            "summary": summary,
            "links": links_text.strip() if links_text else "ไม่พบลิงก์ที่เกี่ยวข้อง"
        }

    except requests.exceptions.RequestException as e:
        print(f"[Web Research] HTTP Request Error: {e}")
        return {
            "summary": "ขออภัยครับ ขณะนี้ระบบค้นหาข้อมูลจากอินเทอร์เน็ตขัดข้องชั่วคราว (ข้อผิดพลาดในการเชื่อมต่อ)",
            "links": ""
        }
    except Exception as e:
        print(f"[Web Research] Unexpected Error: {e}")
        return {
            "summary": "ขออภัยครับ ขณะนี้ระบบค้นหาข้อมูลจากอินเทอร์เน็ตขัดข้องชั่วคราว",
            "links": ""
        }

# =============================================================================
# 16. WEB RESEARCH FUNCTION (Placeholder/Basic)
# =============================================================================
def web_research(query):
    """
    Performs a simulated web search for the given query.
    In a real-world scenario, this would integrate with a web search API (e.g., Google Custom Search API, SerpApi).
    """
    print(f"[WebResearch] Performing simulated search for: {query}")
    # Placeholder for actual web search API integration
    # Example:
    # search_results = call_google_search_api(query)
    # summary = extract_summary_from_results(search_results)
    # links = extract_links_from_results(search_results)

    # For now, return a dummy result
    dummy_summary = f"จากการค้นหาข้อมูลเกี่ยวกับ '{query}' พบว่าสถานการณ์น้ำท่วมในปัจจุบันมีความซับซ้อนและมีการเปลี่ยนแปลงอยู่ตลอดเวลา. ข้อมูลเพิ่มเติมสามารถดูได้จากแหล่งอ้างอิงด้านล่าง"
    dummy_links = [
        "[กรมป้องกันและบรรเทาสาธารณภัย] https://www.disaster.go.th",
        "[ThaiWater] https://www.thaiwater.net",
        "[ข่าวล่าสุด] https://www.example.com/news/flood-update"
    ]

    return {"summary": dummy_summary, "links": "\n".join(dummy_links)}


# from dashboard import dashboard_bp # Assuming dashboard is not provided or needs separate handling

# LINE SDK



def get_popular_questions(limit=5):
    """Fetch the top N popular questions from Supabase."""
    supabase = get_supabase_client()
    if supabase:
        try:
            response = supabase.table("popular_questions").select("question_text").order("count", desc=True).limit(limit).execute()
            return [item["question_text"] for item in response.data]
        except Exception as e:
            print(f"[Supabase] Error fetching popular questions: {e}")
    return []


# =============================================================================
# 21. WEB RESEARCH (สำหรับตัวเลือก B - ค้นหาข้อมูลจากอินเทอร์เน็ต)
# =============================================================================
def web_research(query, max_results=5):
    """
    ค้นหาข้อมูลจากอินเทอร์เน็ตแบบง่าย (ใช้ DuckDuckGo Instant Answer API)
    คืนค่า dict ที่มี 'summary' และ 'links'
    """
    try:
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        
        results = []

        # Abstract (สรุปหลักจาก Wikipedia หรือแหล่งที่มา)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", query),
                "snippet": data.get("AbstractText"),
                "url": data.get("AbstractURL", "")
            })

        # Related Topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", ""),
                    "url": topic.get("FirstURL", "")
                })

        if not results:
            return {
                "summary": f"ไม่พบข้อมูลที่เกี่ยวข้องกับ \"{query}\" ในขณะนี้",
                "links": ""
            }

        # สร้างข้อความตอบกลับ
        summary = f"🔍 ผลการค้นหา: {query}\n\n"
        links_text = ""

        for i, item in enumerate(results[:4], 1):
            if item.get("url"):
                links_text += f"{i}. {item['title']}\n   {item['url']}\n\n"

        return {
            "summary": summary,
            "links": links_text.strip() if links_text else "ไม่พบลิงก์ที่เกี่ยวข้อง"
        }

    except requests.exceptions.RequestException as e:
        print(f"[Web Research] HTTP Request Error: {e}")
        return {
            "summary": "ขออภัยครับ ขณะนี้ระบบค้นหาข้อมูลจากอินเทอร์เน็ตขัดข้องชั่วคราว (ข้อผิดพลาดในการเชื่อมต่อ)",
            "links": ""
        }
    except Exception as e:
        print(f"[Web Research] Unexpected Error: {e}")
        return {
            "summary": "ขออภัยครับ ขณะนี้ระบบค้นหาข้อมูลจากอินเทอร์เน็ตขัดข้องชั่วคราว",
            "links": ""
        }

# =============================================================================
# 16. WEB RESEARCH FUNCTION (Placeholder/Basic)
# =============================================================================
