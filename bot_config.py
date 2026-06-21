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
        if isinstance(data, dict):
            if "waterlevel_data" in data and isinstance(data["waterlevel_data"], dict):
                stations = data["waterlevel_data"].get("data", [])
            elif "data" in data and isinstance(data["data"], list):
                stations = data["data"]
            else:
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
    """Parse V3 API response into standard format with robust ID and value handling"""
    station = v3_item.get("station") or {}
    geocode = station.get("geocode") or {}
    
    # Priority for ID/Code: station_code -> id (as string) -> N/A
    raw_id = v3_item.get("id")
    station_code = v3_item.get("station_code") or station.get("station_code") or (str(raw_id) if raw_id else "N/A")
    
    station_name = station.get("station_name", {}).get("th") or v3_item.get("station_name") or "ไม่ระบุ"
    river_name = station.get("river", {}).get("river_name", {}).get("th") or v3_item.get("river_name") or "-"
    province_name = geocode.get("province", {}).get("province_name", {}).get("th") or v3_item.get("province_name") or "-"
    
    latitude = station.get("station_lat") or v3_item.get("latitude") or 0.0
    longitude = station.get("station_long") or v3_item.get("longitude") or 0.0
    
    # Robust water level extraction: waterlevel_m -> waterlevel_msl -> water_level
    water_level = v3_item.get("waterlevel_m")
    if water_level is None:
        water_level = v3_item.get("waterlevel_msl")
    if water_level is None:
        water_level = v3_item.get("water_level")
        
    bank_level = v3_item.get("bank_level") or station.get("bank_level")
    measure_time = v3_item.get("waterlevel_datetime") or v3_item.get("measure_time") or "-"

    # Ensure numeric types
    try:
        latitude = float(latitude)
        longitude = float(longitude)
        water_level = float(water_level) if water_level is not None else None
        bank_level = float(bank_level) if bank_level is not None else None
    except (ValueError, TypeError):
        pass

    return {
        "StationCode": str(station_code),
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
        return _WATER_STATIONS_CACHE
    
    try:
        url = f"{THAIWATER_API_BASE}/thaiwater/stations"
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        stations = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        _WATER_STATIONS_CACHE = stations
        _WATER_STATIONS_CACHE_TIME = time.time()
        return stations
    except Exception as e:
        print(f"[ThaiWater V1] Error: {e}")
        return None

def get_thaiwater_stations(use_cache=True):
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
        if isinstance(data, dict) and "data" in data and data["data"]:
            return data["data"][0]
        return None
    except Exception as e:
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
        return "วิกฤต" if wl >= 3.0 else "ปกติ"
    
    ratio = wl / bl
    if wl >= bl: return "วิกฤต"
    elif ratio >= 0.70: return "มาก"
    elif ratio >= 0.30: return "ปกติ"
    else: return "น้อย"

def get_nearby_water_stations(user_lat, user_lon, max_distance_km=50, max_stations=3):
    """Find nearest water stations using Supabase data with V1 fallback"""
    stations = get_water_data_from_supabase(user_lat, user_lon)
    if stations:
        for st in stations:
            st["distance_km"] = calculate_distance(user_lat, user_lon, st["latitude"], st["longitude"])
        stations.sort(key=lambda x: x["distance_km"])
        return stations[:max_stations]
    
    # Fallback to V1 API directly if Supabase is empty
    v1_stations = get_thaiwater_stations()
    if not v1_stations: return []
    
    valid_stations = []
    for st in v1_stations:
        try:
            lat = float(st.get("latitude", 0))
            lon = float(st.get("longitude", 0))
            if lat != 0 and lon != 0:
                st["latitude"] = lat
                st["longitude"] = lon
                st["distance_km"] = calculate_distance(user_lat, user_lon, lat, lon)
                valid_stations.append(st)
        except: continue
    
    valid_stations.sort(key=lambda x: x["distance_km"])
    nearby = [s for s in valid_stations if s["distance_km"] <= max_distance_km][:max_stations]
    
    for st in nearby:
        runoff = get_thaiwater_runoff_latest(st.get("stationCode"))
        if runoff:
            st["water_level"] = runoff.get("water_level", {}).get("value")
            st["bank_level"] = runoff.get("bank_level", {}).get("value")
            st["measure_time"] = runoff.get("water_level", {}).get("time", "-")
    return nearby

def assess_water_level_status(water_level_value, bank_level_value=None):
    if water_level_value is None:
        return {"status": "ไม่มีข้อมูล", "color": "#1F2937", "bg_color": "#F3F4F6", "icon": "⚪", "diff_text": "-", "advice": "ไม่สามารถประเมินได้"}
    try:
        wl = float(water_level_value)
        bl = float(bank_level_value) if bank_level_value not in [None, "-", ""] else 0
        diff = bl - wl
        diff_text = f"{abs(diff):.2f}"
    except:
        return {"status": "ข้อมูลไม่ถูกต้อง", "color": "#1F2937", "bg_color": "#F3F4F6", "icon": "⚪", "diff_text": "-", "advice": "ไม่สามารถประเมินได้"}
    
    if bl <= 0:
        return {"status": "วิกฤต" if wl >= 3.0 else "ปกติ", "color": "#B91C1C" if wl >= 3.0 else "#15803D", "bg_color": "#FEE2E2" if wl >= 3.0 else "#DCFCE7", "icon": "🔴" if wl >= 3.0 else "🟢", "diff_text": "-", "advice": "⚠️ อพยพทันที!" if wl >= 3.0 else "ติดตามสถานการณ์"}
    
    ratio = wl / bl
    if wl >= bl: return {"status": "วิกฤต", "color": "#B91C1C", "bg_color": "#FEE2E2", "icon": "🔴", "diff_text": f"-{abs(diff):.2f}", "advice": "⚠️ อพยพทันที! ระดับน้ำล้นตลิ่ง"}
    elif ratio >= 0.70: return {"status": "มาก", "color": "#0369A1", "bg_color": "#E0F2FE", "icon": "🔵", "diff_text": diff_text, "advice": "ระดับน้ำค่อนข้างสูง"}
    elif ratio >= 0.30: return {"status": "ปกติ", "color": "#15803D", "bg_color": "#DCFCE7", "icon": "🟢", "diff_text": diff_text, "advice": "ระดับน้ำปกติ"}
    else: return {"status": "น้อย", "color": "#9A3412", "bg_color": "#FEF9C3", "icon": "🟡", "diff_text": diff_text, "advice": "ระดับน้ำน้อย"}

# =============================================================================
# 11. WATER DATA SYNC TO SUPABASE (TRUNCATE + INSERT)
# =============================================================================
def get_water_data_from_api():
    results = []
    v3_data = fetch_waterlevel_v3(use_cache=False)
    if v3_data:
        for item in v3_data:
            try:
                parsed = parse_v3_station(item)
                if parsed["StationCode"] == "N/A": continue
                results.append({
                    "StationCode": parsed["StationCode"],
                    "Name": parsed["Name"],
                    "River": parsed["River"],
                    "Location": parsed["Location"],
                    "Lat": parsed["Lat"],
                    "Lon": parsed["Lon"],
                    "WaterLevel": parsed["WaterLevel"],
                    "BankLevel": parsed["BankLevel"],
                    "Situation": calculate_situation(parsed["WaterLevel"], parsed["BankLevel"]),
                    "Trend": "คงที่",
                    "Time": parsed["Time"]
                })
            except: continue
    
    if len(results) < 50:
        v1_meta = get_thaiwater_stations(use_cache=False)
        if v1_meta:
            for st in v1_meta[:200]: # Limit fallback to avoid long sync
                code = st.get("stationCode")
                if not code: continue
                runoff = get_thaiwater_runoff_latest(code)
                if runoff:
                    wl = runoff.get("water_level", {}).get("value")
                    bl = runoff.get("bank_level", {}).get("value")
                    results.append({
                        "StationCode": code,
                        "Name": st.get("stationName", "ไม่ระบุ"),
                        "River": st.get("riverName", "-"),
                        "Location": st.get("provinceName", "-"),
                        "Lat": float(st.get("latitude", 0)),
                        "Lon": float(st.get("longitude", 0)),
                        "WaterLevel": wl, "BankLevel": bl,
                        "Situation": calculate_situation(wl, bl),
                        "Trend": "คงที่", "Time": runoff.get("water_level", {}).get("time", "-")
                    })
    return results

def sync_water_levels_to_supabase():
    supabase = get_supabase_client()
    if not supabase: return False
    try:
        data = get_water_data_from_api()
        if not data: return False
        
        rows = []
        for st in data:
            rows.append({
                "station_code": st["StationCode"],
                "name": st["Name"],
                "river": st["River"],
                "location": st["Location"],
                "latitude": st["Lat"],
                "longitude": st["Lon"],
                "water_level": st["WaterLevel"],
                "bank_level": st["BankLevel"],
                "situation": st["Situation"],
                "trend": st["Trend"],
                "measure_time": st["Time"],
                "updated_at": datetime.datetime.now().isoformat()
            })
        
        # Delete old data
        supabase.table("water_levels").delete().neq("station_code", "").execute()
        
        # Chunked insert
        for i in range(0, len(rows), 100):
            supabase.table("water_levels").insert(rows[i:i+100]).execute()
            
        supabase.table("sync_metadata").upsert({"id": "water_levels_last_sync", "last_sync": datetime.datetime.now().isoformat(), "record_count": len(rows)}, on_conflict="id").execute()
        return True
    except Exception as e:
        print(f"Sync Error: {e}")
        return False

def get_water_data_from_supabase(user_lat=None, user_lon=None, limit=2000):
    supabase = get_supabase_client()
    if not supabase: return []
    try:
        records = []
        offset = 0
        while True:
            resp = supabase.table("water_levels").select("*").range(offset, offset + 999).execute()
            if not resp.data: break
            records.extend(resp.data)
            if len(resp.data) < 1000: break
            offset += 1000
        return records
    except: return []

# =============================================================================
# 13. LINE FLEX MESSAGES
# =============================================================================
def create_water_report_flex(stations, user_lat, user_lon):
    header_box = BoxComponent(layout="vertical", contents=[TextComponent(text="🌊 รายงานระดับน้ำใกล้คุณ", weight="bold", size="xl", color="#1E40AF"), TextComponent(text=f"อัปเดต: {datetime.datetime.now().strftime('%H:%M น.')}", size="xs", color="#6B7280", margin="xs")])
    stations_box = BoxComponent(layout="vertical", margin="lg", spacing="md", contents=[])
    if stations:
        for st in stations[:3]:
            assessment = assess_water_level_status(st.get("water_level"), st.get("bank_level"))
            stations_box.contents.append(BoxComponent(layout="vertical", contents=[TextComponent(text=f"{st.get('name', 'ไม่ระบุ')} (ห่าง {st.get('distance_km', 0):.2f} กม.)", size="sm", weight="bold"), BoxComponent(layout="horizontal", margin="md", contents=[BoxComponent(layout="vertical", background_color=assessment["bg_color"], corner_radius="999px", padding_start="12px", padding_end="12px", flex=0, contents=[TextComponent(text=f"{assessment['icon']} {assessment['status']}", size="xs", color=assessment["color"], weight="bold")]), TextComponent(text=assessment["advice"], size="xs", color="#4B5563", margin="sm")]), TextComponent(text=f"ระดับน้ำ: {st.get('water_level') or '-'} ม. | ตลิ่ง: {st.get('bank_level') or '-'} ม.", size="xs", color="#6B7280", margin="sm")]))
    else:
        stations_box.contents.append(TextComponent(text="ไม่พบสถานีในระยะ 50 กม.", size="sm", color="#6B7280"))
    
    bubble = BubbleContainer(body=BoxComponent(layout="vertical", padding_all="xl", contents=[header_box, stations_box, BoxComponent(layout="vertical", margin="xl", contents=[SeparatorComponent(), ButtonComponent(style="link", height="sm", action=URIAction(label="[ 🔗 ดูข้อมูลเพิ่มเติม ]", uri=THAIWATER_WEB_URL))])]))
    return FlexSendMessage(alt_text="รายงานระดับน้ำ", contents=bubble)

# =============================================================================
# 14. GREETING & UTILS
# =============================================================================
def get_greeting_message(user_name="คุณ"):
    return TextSendMessage(text=f"สวัสดีคุณ {user_name}\nผมคือ FLOODCARE AI พร้อมช่วยคุณตรวจสอบระดับน้ำและแจ้งเหตุฉุกเฉินครับ")

def is_user_registered(user_id):
    supabase = get_supabase_client()
    if not supabase: return False
    try:
        res = supabase.table("users").select("user_id").eq("user_id", user_id).execute()
        return len(res.data) > 0
    except: return False

def register_user(user_id, display_name):
    supabase = get_supabase_client()
    if not supabase: return False
    try:
        supabase.table("users").upsert({"user_id": user_id, "display_name": display_name}).execute()
        return True
    except: return False

def generate_case_id():
    return f"SOS-{int(time.time())}"

def get_last_sync_time():
    supabase = get_supabase_client()
    if not supabase: return None
    try:
        res = supabase.table("sync_metadata").select("last_sync").eq("id", "water_levels_last_sync").execute()
        return res.data[0]["last_sync"] if res.data else None
    except: return None
