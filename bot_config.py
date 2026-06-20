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
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

gemini_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=(
        "คุณคือ FLOODCARE AI ผู้ช่วยกู้ภัยมืออาชีประจำศูนย์ประสานงานภัยน้ำท่วมระดับชาติ\n"
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
        if isinstance(data, dict) and "waterlevel_data" in data:
            wl_data = data.get("waterlevel_data", {})
            stations = wl_data.get("data", [])
        elif isinstance(data, list):
            stations = data
        elif isinstance(data, dict):
            stations = data.get("data", [])
            if not stations:
                for key in ["stations", "results", "items", "waterlevel"]:
                    if key in data:
                        stations = data[key]
                        break
        
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
    
    def get_val(*keys, default="-"):
        for k in keys:
            if k in v3_item and v3_item[k] is not None:
                val = v3_item[k]
                if val != "" and val != "null":
                    return val
        return default
    
    lat, lon = 0.0, 0.0
    try:
        lat = float(station.get("tele_station_lat", 0) or 0)
        lon = float(station.get("tele_station_long", 0) or 0)
    except (ValueError, TypeError):
        pass
    
    wl = None
    for key in ["waterlevel_m", "waterlevel_msl"]:
        val = v3_item.get(key)
        if val is not None and val != "" and val != "null":
            try:
                wl = float(val)
                break
            except (ValueError, TypeError):
                continue
    
    bl = None
    try:
        left_b = station.get("left_bank")
        right_b = station.get("right_bank")
        banks = []
        if left_b is not None:
            try: banks.append(float(left_b))
            except: pass
        if right_b is not None:
            try: banks.append(float(right_b))
            except: pass
        if banks:
            bl = min(banks)
    except Exception:
        bl = None
    
    station_name_raw = station.get("tele_station_name", "ไม่ระบุชื่อ")
    if isinstance(station_name_raw, dict):
        station_name = station_name_raw.get("th") or station_name_raw.get("en") or "ไม่ระบุชื่อ"
    else:
        station_name = station_name_raw
    
    province_raw = geocode.get("province_name", "-")
    if isinstance(province_raw, dict):
        province_name = province_raw.get("th") or province_raw.get("en") or "-"
    else:
        province_name = province_raw
    
    station_code = station.get("tele_station_oldcode") or station.get("id") or "-"
    
    return {
        "StationCode": str(station_code),
        "Name": str(station_name),
        "River": str(get_val("river_name", default="-")),
        "Location": str(province_name),
        "Lat": lat,
        "Lon": lon,
        "WaterLevel": wl,
        "BankLevel": bl,
        "Time": str(get_val("waterlevel_datetime", default="-")),
    }


def get_thaiwater_stations(use_cache=True):
    """Get station list from ThaiWater V1 API (fallback)"""
    global _WATER_STATIONS_CACHE, _WATER_STATIONS_CACHE_TIME
    
    if use_cache and _WATER_STATIONS_CACHE:
        elapsed = time.time() - _WATER_STATIONS_CACHE_TIME
        if elapsed < _WATER_STATIONS_CACHE_TTL:
            print(f"[ThaiWater V1] Using cached stations ({len(_WATER_STATIONS_CACHE)} stations)")
            return _WATER_STATIONS_CACHE
    
    try:
        url = f"{THAIWATER_API_BASE}/StationInfo"
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        stations = []
        raw_stations = data.get("station", [])
        print(f"[ThaiWater V1] Fetched {len(raw_stations)} total stations from API")
        
        for st in raw_stations:
            meta = st.get("stationMetadata", {})
            st_type = meta.get("stationType", "")
            
            if any(kw in st_type for kw in ["ระดับน้ำ", "น้ำท่า", "Runoff", "WaterLevel", "ดิน", "อุทก"]):
                try:
                    lat = float(meta.get("latitude", 0))
                    lon = float(meta.get("longitude", 0))
                    if lat == 0 and lon == 0:
                        continue
                    
                    stations.append({
                        "stationCode": meta.get("stationCode", ""),
                        "stationName": meta.get("stationName", "ไม่ระบุชื่อ"),
                        "stationType": st_type,
                        "provinceCode": meta.get("locationCode", ""),
                        "provinceName": meta.get("provinceName", ""),
                        "districtName": meta.get("districtName", ""),
                        "riverName": meta.get("riverName", ""),
                        "latitude": lat,
                        "longitude": lon,
                        "status": meta.get("stationOperatingStatus", 1)
                    })
                except (ValueError, TypeError):
                    continue
        
        _WATER_STATIONS_CACHE = stations
        _WATER_STATIONS_CACHE_TIME = time.time()
        print(f"[ThaiWater V1] Filtered {len(stations)} water monitoring stations")
        return stations
    
    except requests.exceptions.Timeout:
        print("[ThaiWater V1] API Timeout - returning cached data if available")
        return _WATER_STATIONS_CACHE
    except requests.exceptions.RequestException as e:
        print(f"[ThaiWater V1] API Error: {e}")
        return _WATER_STATIONS_CACHE
    except Exception as e:
        print(f"[ThaiWater V1] Unexpected Error: {e}")
        return _WATER_STATIONS_CACHE


def get_thaiwater_runoff_latest(station_code):
    """Get latest water level for a specific station from V1 API"""
    if not station_code:
        return None
    
    try:
        url = (f"{THAIWATER_API_BASE}/Runoff?"
               f"stationCode={station_code}&latest=true&interval=C-60")
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        observations = data.get("timeSeriesObservation", [])
        if not observations:
            return None
        
        obs = observations[0]
        results = obs.get("measurementResults", [])
        
        water_level = None
        discharge = None
        bank_level = None
        
        for r in results:
            var_type = r.get("variable", "")
            if var_type == "WaterLevel":
                water_level = {"value": r.get("value"), "uom": r.get("uom", "m"),
                               "time": r.get("measureTime"), "quality": r.get("qualityControlLevel", "1")}
            elif var_type == "Discharge":
                discharge = {"value": r.get("value"), "uom": r.get("uom", "CMS"), "time": r.get("measureTime")}
            elif var_type == "BankLevel":
                bank_level = {"value": r.get("value"), "uom": r.get("uom", "m")}
        
        return {
            "stationCode": station_code,
            "stationName": obs.get("station", {}).get("stationReference", ""),
            "water_level": water_level,
            "bank_level": bank_level,
            "discharge": discharge,
            "resultTime": obs.get("resultTime")
        }
    
    except requests.exceptions.Timeout:
        print(f"[ThaiWater V1] Runoff Timeout for station {station_code}")
        return None
    except Exception as e:
        print(f"[ThaiWater V1] Runoff Error for station {station_code}: {e}")
        return None


# =============================================================================
# 9. WATER LEVEL CALCULATION
# =============================================================================
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
    
    for st in stations:
        st["distance_km"] = calculate_distance(user_lat, user_lon, st["latitude"], st["longitude"])
    
    nearby = [s for s in stations if s["distance_km"] <= max_distance_km]
    nearby.sort(key=lambda x: x["distance_km"])
    
    result = []
    for st in nearby[:max_stations]:
        runoff_data = get_thaiwater_runoff_latest(st["stationCode"])
        if runoff_data:
            st["water_level"] = runoff_data.get("water_level")
            st["bank_level"] = runoff_data.get("bank_level")
            st["discharge"] = runoff_data.get("discharge")
            st["measure_time"] = runoff_data.get("resultTime")
        result.append(st)
    
    return result


def assess_water_level_status(water_level_value, bank_level_value=None):
    if water_level_value is None:
        return {"status": "⚪ ไม่มีข้อมูล", "color": "#9CA3AF", "diff_text": "-", "advice": "ไม่สามารถประเมินได้"}
    
    try:
        wl = float(water_level_value)
        bl = float(bank_level_value) if bank_level_value else 0
        diff = bl - wl
        diff_text = f"{abs(diff):.2f}"
    except (ValueError, TypeError):
        return {"status": "⚪ ข้อมูลไม่ถูกต้อง", "color": "#9CA3AF", "diff_text": "-", "advice": "ไม่สามารถประเมินได้"}
    
    if bl <= 0:
        if wl >= 3.0:
            return {"status": "ล้นตลิ่ง", "color": "#EF4444", "diff_text": "-", "advice": "⚠️ อพยพทันที!"}
        return {"status": "ปกติ", "color": "#10B981", "diff_text": "-", "advice": "ติดตามสถานการณ์"}
    
    ratio = wl / bl
    
    if wl >= bl:
        return {"status": "ล้นตลิ่ง", "color": "#FF0000", "diff_text": f"ล้นตลิ่ง {abs(diff):.2f}", "advice": "⚠️ อพยพทันที! ระดับน้ำล้นตลิ่ง"}
    elif ratio >= 0.70:
        return {"status": "มาก", "color": "#0000FF", "diff_text": diff_text, "advice": "💧 ระดับน้ำค่อนข้างสูง"}
    elif ratio >= 0.30:
        return {"status": "ปกติ", "color": "#008000", "diff_text": diff_text, "advice": "✅ ระดับน้ำปกติ"}
    elif ratio >= 0.10:
        return {"status": "น้อย", "color": "#FFCC00", "diff_text": diff_text, "advice": "⚠️ ระดับน้ำน้อย"}
    else:
        return {"status": "น้อยวิกฤต", "color": "#E67E22", "diff_text": diff_text, "advice": "🚨 ระดับน้ำน้อยวิกฤต"}


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
    v3_data = fetch_waterlevel_v3()
    
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
                    "WaterLevel": wl if wl is not None else "-",
                    "BankLevel": bl if bl is not None else "-",
                    "Situation": situation,
                    "Trend": trend,
                    "Time": parsed["Time"]
                })
            except Exception as e:
                print(f"[LazySync V3] Parse error: {e}")
                continue
        
        print(f"[LazySync] V3 parsed: {len(results)} stations")
        if len(results) > 50:
            return results
    
    # Strategy 2: ThaiWater V1 API (Fallback)
    print("[LazySync] V3 insufficient, falling back to V1 API...")
    stations = get_thaiwater_stations(use_cache=True)
    if not stations:
        print("[LazySync] No stations available from V1 cache")
        return results
    
    for i, st in enumerate(stations):
        runoff = get_thaiwater_runoff_latest(st["stationCode"])
        time.sleep(0.05)  # Rate limiting
        
        wl_value = None
        bl_value = None
        measure_time = "-"
        code = st["stationCode"]
        
        if runoff:
            wl = runoff.get("water_level")
            bl = runoff.get("bank_level")
            if wl:
                wl_value = wl.get("value")
                measure_time = wl.get("time", "-")
            if bl:
                bl_value = bl.get("value")
        
        situation = calculate_situation(wl_value, bl_value)
        trend = "คงที่"
        
        results.append({
            "StationCode": code,
            "Name": st["stationName"],
            "River": st.get("riverName", "-"),
            "Location": st.get("provinceName", "-"),
            "Lat": st["latitude"],
            "Lon": st["longitude"],
            "WaterLevel": wl_value if wl_value is not None else "-",
            "BankLevel": bl_value if bl_value is not None else "-",
            "Situation": situation,
            "Trend": trend,
            "Time": measure_time
        })
        
        if (i + 1) % 100 == 0:
            print(f"[LazySync V1] Processed {i + 1}/{len(stations)} stations")
    
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
            if isinstance(situation_text, dict):
                situation_text = situation_text.get("status", "ปกติ")
            
            wl_val = st.get("WaterLevel")
            bl_val = st.get("BankLevel")
            
            rows_to_insert.append({
                "station_code": st.get("StationCode"),
                "name": st.get("Name"),
                "river": st.get("River"),
                "location": st.get("Location"),
                "lat": st.get("Lat"),
                "lon": st.get("Lon"),
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
            supabase.table("water_levels").delete().neq("station_code", "").execute()
        except Exception as e:
            print(f"[Supabase Water] Truncate warning (may be empty table): {e}")
            # Try alternative truncate via RPC
            try:
                supabase.rpc("truncate_water_levels").execute()
            except:
                pass
        
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
                # Try inserting one by one for failed chunks
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


def get_water_data_from_supabase(user_lat=None, user_lon=None, limit=100):
    """
    Get water levels from Supabase.
    If lat/lon provided, calculate distance and return nearest.
    """
    supabase = get_supabase_client()
    if not supabase:
        return []
    
    try:
        response = supabase.table("water_levels").select("*").order("updated_at", desc=True).limit(limit).execute()
        records = response.data or []
        
        if user_lat is not None and user_lon is not None:
            for rec in records:
                try:
                    rec["distance_km"] = calculate_distance(
                        user_lat, user_lon,
                        float(rec.get("lat", 0) or 0),
                        float(rec.get("lon", 0) or 0)
                    )
                except:
                    rec["distance_km"] = 9999
            records.sort(key=lambda x: x.get("distance_km", 9999))
            return records[:3]  # Return 3 nearest
        
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
    if supabase:
        try:
            response = supabase.table("users").select("first_name, last_name, phone").eq("user_id", str(user_id)).limit(1).execute()
            if response.data and len(response.data) > 0:
                row = response.data[0]
                fn = row.get("first_name", "ผู้แจ้ง")
                ln = row.get("last_name", "")
                ph = row.get("phone", "-")
                if user_id not in USER_DATA:
                    USER_DATA[user_id] = {}
                USER_DATA[user_id]["first_name"] = fn
                USER_DATA[user_id]["last_name"] = ln
                USER_DATA[user_id]["phone"] = ph
                return True, fn, ln, ph
        except Exception as e:
            print(f"[Supabase UserReg] Check error: {e}")
    
    # Memory fallback
    if user_id in USER_DATA:
        d = USER_DATA[user_id]
        if "first_name" in d:
            return True, d.get("first_name", ""), d.get("last_name", ""), d.get("phone", "-")
    
    return False, "", "", "-"


def register_user(user_id=None, first_name=None, last_name=None, phone=None):
    """
    Register new user to Supabase (Primary only).
    """
    if not user_id:
        return False
    
    supabase = get_supabase_client()
    if supabase:
        try:
            register_date = datetime.datetime.now().strftime("%Y-%m-%d")
            supabase.table("users").upsert({
                "user_id": str(user_id),
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "register_date": register_date,
                "status": "ACTIVE"
            }, on_conflict="user_id").execute()
            print("[Supabase] User registered/updated successfully")
            if user_id not in USER_DATA:
                USER_DATA[user_id] = {}
            USER_DATA[user_id]["first_name"] = first_name
            USER_DATA[user_id]["last_name"] = last_name
            USER_DATA[user_id]["phone"] = phone
            return True
        except Exception as e:
            print(f"[Supabase UserReg] Error: {e}")
    
    # At least store in memory
    if user_id not in USER_DATA:
        USER_DATA[user_id] = {}
    USER_DATA[user_id]["first_name"] = first_name or ""
    USER_DATA[user_id]["last_name"] = last_name or ""
    USER_DATA[user_id]["phone"] = phone or "-"
    return supabase is not None


# =============================================================================
# 13. WATER LEVEL REPORT BUILDERS
# =============================================================================
def build_water_level_text_report(user_lat, user_lon, timestamp, stations, weather_info, water_flow):
    """Build text report for water levels"""
    lines = [
        "🌊 รายงานสถานการณ์น้ำรายพิกัด",
        f"📍 พิกัด: {user_lat:.4f}, {user_lon:.4f}",
        f"⏰ อัปเดตล่าสุด: {timestamp}",
        ""
    ]
    
    lines.append("🌦️ สภาพอากาศ:")
    lines.append(weather_info)
    lines.append("")
    
    lines.append("📡 ข้อมูลจากสถานี ThaiWater ใกล้คุณ:")
    lines.append("")
    
    if not stations:
        lines.append("⚠️ ไม่พบสถานีในรัศมี 50 กม.")
    else:
        for i, st in enumerate(stations, 1):
            wl = st.get("water_level")
            distance = st.get("distance_km", 0)
            situation = st.get("situation", "ไม่มีข้อมูล")
            trend = st.get("trend", "คงที่")
            
            if wl and wl.get("value") not in [None, "-", ""]:
                try:
                    wl_value = float(wl["value"])
                    bl = st.get("bank_level")
                    assessment = assess_water_level_status(wl_value, bl if bl not in [None, "-", ""] else None)
                    lines.append(f"{i}. 📍 {st['stationName']}")
                    lines.append(f"   ห่าง: {distance:.2f} กม.")
                    lines.append(f"   ระดับน้ำ: {wl_value:.2f} ม.")
                    lines.append(f"   สถานะ: {assessment['status']}")
                    lines.append(f"   สถานการณ์: {situation} | แนวโน้ม: {trend}")
                    lines.append(f"   {assessment['advice']}")
                    if st.get('measure_time') and st['measure_time'] != '-':
                        lines.append(f"   วัดล่าสุด: {st['measure_time']}")
                except (ValueError, TypeError):
                    lines.append(f"{i}. 📍 {st['stationName']}")
                    lines.append(f"   ห่าง: {distance:.2f} กม.")
            else:
                lines.append(f"{i}. 📍 {st['stationName']}")
                lines.append(f"   ห่าง: {distance:.2f} กม.")
                lines.append(f"   ไม่มีข้อมูลระดับน้ำ")
            lines.append("")
    
    lines.append("🌊 ประมาณการน้ำหลาก:")
    lines.append(f"   อัตราการไหล: {water_flow.get('flow', 'N/A')}")
    lines.append(f"   ความสูงน้ำ: {water_flow.get('height', 'N/A')}")
    lines.append(f"   สถานะ: {water_flow.get('status', 'N/A')}")
    lines.append("")
    lines.append(f"📌 แหล่งข้อมูล: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)")
    lines.append(f"🔗 ดูเพิ่มเติม: {THAIWATER_WEB_URL}")
    
    return "\n".join(lines)


def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, weather_info, water_flow):
    """Build Flex Message for water levels"""
    header_box = BoxComponent(
        layout="vertical",
        contents=[
            TextComponent(text="🌊 รายงานระดับน้ำรายพิกัด", weight="bold", size="lg", color="#1E3A8A"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f} | {timestamp}", size="xs", color="#6B7280", margin="sm")
        ]
    )
    
    stations_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[TextComponent(text="📡 สถานีตรวจวัดใกล้คุณ", weight="bold", size="sm", color="#374151")]
    )
    
    if not stations:
        stations_box.contents.append(
            TextComponent(text="⚠️ ไม่พบสถานีในรัศมี 50 กม.", size="xs", color="#EF4444", margin="sm")
        )
    else:
        for st in stations:
            wl = st.get("water_level")
            distance = st.get("distance_km", 0)
            situation = st.get("situation", "ไม่มีข้อมูล")
            trend = st.get("trend", "คงที่")
            
            wl_value = "-"
            risk_color = "#9CA3AF"
            assessment = assess_water_level_status(None)
            
            if wl and wl.get("value") not in [None, "-", ""]:
                try:
                    wl_value = float(wl["value"])
                    bl = st.get("bank_level")
                    assessment = assess_water_level_status(wl_value, bl if bl not in [None, "-", ""] else None)
                    risk_color = assessment["color"]
                except (ValueError, TypeError):
                    pass
            
            station_card = BoxComponent(
                layout="vertical",
                margin="sm",
                background_color="#F9FAFB",
                corner_radius="md",
                padding_all="sm",
                contents=[
                    TextComponent(text=f"📍 {st['stationName']}", weight="bold", size="xs", color="#1F2937"),
                    TextComponent(text=f"ห่าง {distance:.2f} กม. | ระดับ {wl_value} ม. / ตลิ่ง {st.get('bank_level', '-')} ม.", size="xxs", color="#4B5563", margin="xs"),
                    BoxComponent(
                        layout="horizontal",
                        margin="xs",
                        spacing="sm",
                        contents=[
                            BoxComponent(
                                layout="vertical",
                                background_color=risk_color,
                                corner_radius="sm",
                                padding_start="sm",
                                padding_end="sm",
                                contents=[
                                    TextComponent(text=assessment["status"], size="xxs", color="#FFFFFF", weight="bold")
                                ]
                            ),
                            TextComponent(text=f"ต่ำกว่าตลิ่ง: {assessment['diff_text']} ม.", size="xxs", color=risk_color, weight="bold")
                        ]
                    ),
                    TextComponent(text=f"แนวโน้ม: {trend} | {assessment['advice']}", size="xxs", color="#6B7280", margin="xs")
                ]
            )
            stations_box.contents.append(station_card)
    
    footer_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[
            SeparatorComponent(margin="sm"),
            TextComponent(text="📌 แหล่งข้อมูล: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)", size="xxs", color="#9CA3AF", margin="sm"),
            ButtonComponent(
                style="link",
                height="sm",
                action=URIAction(label="ดูข้อมูลเพิ่มเติมที่ ThaiWater", uri=THAIWATER_WEB_URL),
                color="#2563EB"
            )
        ]
    )
    
    bubble = BubbleContainer(
        header=header_box,
        body=BoxComponent(
            layout="vertical",
            contents=[
                stations_box,
                footer_box
            ]
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
    try:
        profile = line_bot_api.get_profile(user_id)
    except:
        pass
    
    user_name = profile.display_name if profile else "คุณ"
    greeting_msg = get_greeting_message(user_name)
    
    line_bot_api.reply_message(event.reply_token, greeting_msg)


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
                "latitude": float(lat) if lat else 0,
                "longitude": float(lon) if lon else 0,
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
        cap = int(capacity)
        occ = int(occupancy)
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
