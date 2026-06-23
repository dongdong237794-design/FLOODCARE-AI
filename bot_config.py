import os
import json
import math
import datetime
import time
import urllib.request
import requests
import google.generativeai as genai
import gspread
try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = None
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent,
    SeparatorComponent, ButtonComponent, URIAction, TextSendMessage,
    MessageAction, LocationAction, BubbleStyle, BlockStyle
)

# =============================================================================
# 1. โหลดข้อมูลกำหนดค่าจาก Environment Variables
# =============================================================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RICH_MENU_ID = os.environ.get("RICH_MENU_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
TMD_ACCESS_TOKEN = os.environ.get("TMD_ACCESS_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# =============================================================================
# ระบบติดตามสถานะการสนทนาและเก็บข้อมูลคัดกรอง
# =============================================================================
USER_STATES = {}
USER_DATA = {}

# =============================================================================
# SUPABASE CLIENT (เชื่อมต่อ Supabase แทน/คู่กับ Google Sheets)
# =============================================================================
_supabase_client = None

def get_supabase_client():
    """เชื่อมต่อ Supabase Client (ใช้ Service Role Key สำหรับ backend)"""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not SUPABASE_URL or not SUPABASE_KEY or create_client is None:
        print("[Supabase] SUPABASE_URL / SUPABASE_KEY not configured or library missing")
        return None
    try:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[Supabase] Client initialized successfully")
        return _supabase_client
    except Exception as e:
        print(f"[Supabase] Initialization error: {e}")
        return None

# =============================================================================
# 2. THAIWATER API CONFIGURATION (V3 + V1 Legacy)
# =============================================================================
THAIWATER_V3_API = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
THAIWATER_API_BASE = "https://api.thaiwater.net/twsapi/v1.0"
THAIWATER_WEB_URL = "https://www.thaiwater.net/water/wl"

# Cache สำหรับสถานี ThaiWater (cache 1 ชั่วโมง)
_WATER_STATIONS_CACHE = []
_WATER_STATIONS_CACHE_TIME = 0
_WATER_STATIONS_CACHE_TTL = 3600  # 1 ชั่วโมง (วินาที)

# Cache สำหรับสภาพอากาศ TMD (RAM Cache)
_WEATHER_CACHE = {}  # { "lat,lon": {"data": "...", "time": timestamp} }
_WEATHER_CACHE_TTL = 1800  # 30 นาที (วินาที)

# Cache สำหรับ ThaiWater V3 (RAM Cache)
_V3_WATER_CACHE = []
_V3_WATER_CACHE_TIME = 0
_V3_WATER_CACHE_TTL = 3600  # 1 ชั่วโมง (วินาที)

# เริ่มใช้งาน LINE API แบบปลอดภัย
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

# =============================================================================
# 3. Gemini AI Configuration (System Instruction ฉบับปรับปรุง 5 ด้าน)
# =============================================================================
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

gemini_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=(
        "คุณคือ FLOODCARE AI ผู้ช่วยกู้ภัยมืออาชีพประจำศูนย์ประสานงานภัยน้ำท่วมระดับชาติ\n"
        "บทบาท: ผู้นำในวิกฤตที่ใจดีแต่เด็ดขาด (Calm and Firm)\n"
        "เป้าหมาย: ให้ข้อมูลที่แม่นยำ กระชับ และช่วยผู้ประสบภัยเอาตัวรอดได้จริง\n\n"

        "[1] Data-Driven Response (ให้ความสำคัญกับข้อมูลระบบก่อนเสมอ):\n"
        "- ข้อมูลระดับน้ำจาก ThaiWater, รายชื่อศูนย์พักพิง, เบอร์โทรในฐานข้อมูล คือข้อมูลหลัก\n"
        "- หากผู้ใช้ถามสถานที่ที่ไม่มีในฐานข้อมูล ให้กล้ายอมรับว่า 'ไม่มีข้อมูลในระบบ'\n"
        "- แนะนำให้ดูลิงก์แผนที่รวมแทนการเดาพิกัดเอง\n"
        "- ห้ามแนะนำเส้นทางที่ไม่แน่ใจ หรือยืนยันว่าปลอดภัย 100%\n\n"

        "[2] Emergency Detection (ตรวจจับความเร่งด่วน):\n"
        "- หากพบคำสำคัญบ่งบอกอันตรายถึงชีวิต เช่น 'ช่วยด้วย' 'จะจมแล้ว' 'ไฟดูด' 'หายใจไม่ออก' 'จมน้ำ' 'ไฟฟ้าดูด'\n"
        "- หยุดการเกริ่นนำทันที ส่ง 'ขั้นตอนเอาตัวรอดทันที' พร้อมเบอร์ 1784 หรือ 1669 เป็นอันดับแรก\n"
        "- จากนั้นค่อยถามรายละเอียดเพิ่มเติม\n\n"

        "[3] Tone of Voice (Calm and Authoritative):\n"
        "- ใช้โทน 'ใจดีแต่เด็ดขาด' ไม่ผวา ไม่เยิ่นเย้อ\n"
        "- เน้นการสั่งการเป็นขั้นตอน (1, 2, 3) แทนการพูดคลุมเครือ\n"
        "- เช่น '1. ยกเบรกเกอร์ 2. ขึ้นที่สูง 3. เตรียมไฟฉาย' แทน 'แนะนำให้ลองทำแบบนี้ดูนะครับ'\n"
        "- ใช้คำลงท้าย 'ครับ' หรือ 'นะครับ' เป็นหลัก\n\n"

        "[4] Shelter and Route Safety Rules:\n"
        "- ห้ามยืนยันว่าเส้นทางปลอดภัย 100% เพราะระดับน้ำเปลี่ยนตลอดเวลา\n"
        "- ต้องมีประโยคเตือนเสมอ: 'โปรดใช้ความระมัดระวังในการเดินทางและสังเกตระดับน้ำจริงหน้างาน'\n"
        "- แนะนำให้โทรศูนย์พักพิงก่อนออกเดินทางทุกครั้ง\n\n"

        "[5] Formatting for Crisis (อ่านง่ายบนมือถือ):\n"
        "- กฎ 3 บรรทัดแรก: ข้อมูลสำคัญสุดต้องอยู่ใน 3 บรรทัดแรกเสมอ\n"
        "- ห้ามใช้ตัวหนาเยอะ ให้ใช้การเว้นบรรทัดแยกหัวข้อแทน\n"
        "- ห้ามใช้เครื่องหมายดอกจัน (*) ในการทำสัญลักษณ์\n"
        "- ใช้อิโมจิที่จำเป็นเท่านั้น (เช่น ⚠️ 📞 🏃 🩹)\n"
        "- ความยาวข้อความไม่เกิน 10 บรรทัดต่อกลุ่ม\n\n"

        "[6] General Safety:\n"
        "- ห้ามเดาข้อมูลหรือจินตนาการสิ่งที่ไม่เป็นความจริง\n"
        "- หากข้อมูลไม่แน่ชิด ให้แสดงความห่วงใจ + แนะนำเบอร์สายด่วน\n"
        "- ให้คำตอบเป็นภาษาไทยเสมอ"
    )
)

# =============================================================================
# 4. UTILITY FUNCTIONS
# =============================================================================
def clean_text_for_line(text):
    """กรองลบเครื่องหมายดอกจัน (*) สำหรับ LINE"""
    if not text:
        return ""
    return text.replace("**", "").replace("*", "")

def extract_number(text):
    """ดึงตัวเลขจากข้อความ"""
    if not text:
        return "1"
    cleaned = "".join(filter(lambda x: x.isdigit(), text))
    return cleaned if cleaned else "1"

def parse_yes_no(text):
    """แปลงข้อความเป็น YES/NO"""
    if not text:
        return "NO"
    text_clean = text.strip().lower()
    if any(word in text_clean for word in ["มี", "ใช่", "yes", "y", "เอส", "ตกลง"]):
        if "ไม่มี" in text_clean:
            return "NO"
        return "YES"
    return "NO"

def extract_sheet_id(sheet_var):
    """คัดกรองรหัส Google Sheet ID จาก URL"""
    if not sheet_var:
        return ""
    if "/d/" in sheet_var:
        parts = sheet_var.split("/d/")
        if len(parts) > 1:
            sub_parts = parts[1].split("/")
            if len(sub_parts) > 0:
                return sub_parts[0].strip()
    return sheet_var.strip()

def calculate_distance(lat1, lon1, lat2, lon2):
    """คำนวณระยะทาง Haversine (หน่วย: กิโลเมตร)"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# =============================================================================
# 5. WEATHER & FLOOD SCRAPERS (TMD NWPAPI + Hybrid Cache)
# =============================================================================
def get_weather_from_sheet(lat, lon):
    """ค้นหาข้อมูลสภาพอากาศจาก Google Sheets (Sheet: 'WeatherCache')"""
    try:
        if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
            return None
            
        gc = get_sheets_client()
        if not gc: return None
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        
        try:
            ws = sh.worksheet("WeatherCache")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title="WeatherCache", rows="1000", cols="5")
            ws.append_row(["lat_lon", "weather_text", "timestamp"])
            return None

        records = ws.get_all_records()
        key = f"{round(float(lat), 2)},{round(float(lon), 2)}"
        
        for row in records:
            if row["lat_lon"] == key:
                cache_time = float(row["timestamp"])
                if time.time() - cache_time < _WEATHER_CACHE_TTL:
                    return row["weather_text"]
        return None
    except Exception as e:
        print(f"Sheet Cache Read Error: {e}")
        return None

def save_weather_to_sheet(lat, lon, text):
    """บันทึกข้อมูลสภาพอากาศลง Google Sheets"""
    try:
        if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
            return
            
        gc = get_sheets_client()
        if not gc: return
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = sh.worksheet("WeatherCache")
        
        key = f"{round(float(lat), 2)},{round(float(lon), 2)}"
        now = time.time()
        
        cell = ws.find(key)
        if cell:
            ws.update_cell(cell.row, 2, text)
            ws.update_cell(cell.row, 3, now)
        else:
            ws.append_row([key, text, now])
    except Exception as e:
        print(f"Sheet Cache Write Error: {e}")

def get_live_weather_scraper(lat, lon):
    """ดึงข้อมูลสภาพอากาศพร้อมระบบ Hybrid Cache (RAM -> Sheet -> API)"""
    cache_key = f"{round(float(lat), 2)},{round(float(lon), 2)}"
    
    if cache_key in _WEATHER_CACHE:
        entry = _WEATHER_CACHE[cache_key]
        if time.time() - entry["time"] < _WEATHER_CACHE_TTL:
            print(f"[Cache] RAM Hit for {cache_key}")
            return entry["data"]

    sheet_data = get_weather_from_sheet(lat, lon)
    if sheet_data:
        print(f"[Cache] Sheet Hit for {cache_key}")
        _WEATHER_CACHE[cache_key] = {"data": sheet_data, "time": time.time()}
        return sheet_data

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
        
        temp, rh, wind, weather_code = data.get("tc", "-"), data.get("rh", "-"), data.get("ws10m", "-"), data.get("cond", 0)

        weather_map = {1: "แจ่มใส", 2: "เมฆบางส่วน", 3: "เมฆมาก", 4: "ครึ้ม", 5: "ฝนเล็กน้อย", 
                       6: "ฝนปานกลาง", 7: "ฝนหนัก", 8: "ฝนฟ้าคะนอง", 9: "หนาวจัด", 10: "หนาว", 11: "เย็น", 12: "ร้อนจัด"}
        weather_desc = weather_map.get(weather_code, "ไม่ระบุ")

        result_text = f"🌡️ {temp} °C | 🌧️ {weather_desc}\n💧 ชื้น {rh}% | 🍃 ลม {wind} m/s"
        
        _WEATHER_CACHE[cache_key] = {"data": result_text, "time": time.time()}
        save_weather_to_sheet(lat, lon, result_text)
        
        return result_text

    except Exception as e:
        print(f"TMD API Error: {e}")
        return "🌡️ อุณหภูมิ: ~28 °C\n🌧️ สภาพอากาศ: ท้องฟ้าครึ้ม"


def get_live_water_scraper(lat, lon):
    """ดึงข้อมูลน้ำหลากประมาณการจาก Open-Meteo Flood API"""
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
# 6. THAIWATER API V3 (NEW PRIMARY) + V1 LEGACY
# =============================================================================
def fetch_waterlevel_v3(use_cache=True):
    """
    ดึงข้อมูลระดับน้ำทั้งหมดจาก ThaiWater V3 API (waterlevel_load)
    Returns: list of dict หรือ None ถ้าล้มเหลว
    """
    global _V3_WATER_CACHE, _V3_WATER_CACHE_TIME
    
    if use_cache and _V3_WATER_CACHE and (time.time() - _V3_WATER_CACHE_TIME < _V3_WATER_CACHE_TTL):
        print(f"[ThaiWater V3] Using RAM Cache (age: {int(time.time() - _V3_WATER_CACHE_TIME)}s)")
        return _V3_WATER_CACHE

    try:
        headers = {
            'User-Agent': 'FLOODCARE-Bot/1.0',
            'Accept': 'application/json'
        }
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

    except Exception as e:
        print(f"[ThaiWater V3] API Error: {e}")
        return None

def parse_v3_station(v3_item):
    """แปลงข้อมูลจาก V3 API เป็นโครงสร้างมาตรฐาน 11 ฟิลด์"""
    station = v3_item.get("station") or {}
    geocode = station.get("geocode") or {}

    def get_val(*keys, default="-"):
        for k in keys:
            if k in v3_item and v3_item[k] is not None:
                val = v3_item[k]
                if val != "" and val != "null":
                    return val
        return default

    lat = 0.0
    lon = 0.0
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
    """ดึงรายชื่อสถานีตรวจวัดน้ำทั้งหมดจาก ThaiWater V1 API (สำรอง)"""
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

    except Exception as e:
        print(f"[ThaiWater V1] Error: {e}")
        return _WATER_STATIONS_CACHE

def get_thaiwater_runoff_latest(station_code):
    """ดึงข้อมูลระดับน้ำล่าสุดของสถานีจาก ThaiWater V1 API"""
    if not station_code:
        return None

    try:
        url = (f"{THAIWATER_API_BASE}/Runoff?"
               f"stationCode={station_code}"
               f"&latest=true"
               f"&interval=C-60")
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(url, headers=headers, timeout=10)
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
                water_level = {
                    "value": r.get("value"),
                    "uom": r.get("uom", "m"),
                    "time": r.get("measureTime"),
                    "quality": r.get("qualityControlLevel", "1")
                }
            elif var_type == "Discharge":
                discharge = {
                    "value": r.get("value"),
                    "uom": r.get("uom", "CMS"),
                    "time": r.get("measureTime")
                }
            elif var_type == "BankLevel":
                bank_level = {
                    "value": r.get("value"),
                    "uom": r.get("uom", "m")
                }

        return {
            "stationCode": station_code,
            "stationName": obs.get("station", {}).get("stationReference", ""),
            "water_level": water_level,
            "bank_level": bank_level,
            "discharge": discharge,
            "resultTime": obs.get("resultTime")
        }
    except Exception as e:
        print(f"[ThaiWater V1] Runoff Error for station {station_code}: {e}")
        return None

# =============================================================================
# 7. WATER LEVEL CALCULATION (Situation + Trend)
# =============================================================================
def calculate_situation(water_level, bank_level):
    """คำนวณสถานการณ์น้ำตามเงื่อนไขใหม่"""
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
    if wl >= bl:
        return "ล้นตลิ่ง"
    elif ratio >= 0.70:
        return "มาก"
    elif ratio >= 0.30:
        return "ปกติ"
    elif ratio >= 0.10:
        return "น้อย"
    else:
        return "น้อยวิกฤต"

def determine_trend(current_wl, previous_wl, tolerance=0.01):
    """คำนวณแนวโน้ม"""
    try:
        cwl = float(current_wl) if current_wl is not None else None
        pwl = float(previous_wl) if previous_wl is not None else None
    except (ValueError, TypeError):
        return "คงที่"

    if cwl is None or pwl is None:
        return "คงที่"

    diff = cwl - pwl
    if abs(diff) <= tolerance:
        return "คงที่"
    elif diff > 0:
        return "เพิ่มขึ้น"
    else:
        return "ลดลง"

# =============================================================================
# 8. FIND NEAREST WATER STATIONS
# =============================================================================
def find_nearest_water_stations(user_lat, user_lon, max_stations=3, max_distance_km=50):
    """หาสถานีตรวจวัดน้ำที่ใกล้ผู้ใช้ที่สุด"""
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

def assess_water_level_status(water_level_value, bank_level_value=None, situation=None, lang="TH"):
    """
    ประเมินสถานะระดับน้ำพร้อมรองรับหลายภาษา (TH, EN, JP, MY)
    """
    if not situation:
        situation = calculate_situation(water_level_value, bank_level_value)

    try:
        wl = float(water_level_value) if water_level_value not in [None, "-", ""] else 0
        bl = float(bank_level_value) if bank_level_value not in [None, "-", ""] else 0
        diff = bl - wl
        diff_text = f"{abs(diff):.2f}"
    except (ValueError, TypeError):
        diff_text = "-"

    # พจนานุกรมแปลภาษา
    translations = {
        "TH": {
            "ล้นตลิ่ง": "ล้นตลิ่ง", "มาก": "มาก", "ปกติ": "ปกติ", "น้อย": "น้อย", "น้อยวิกฤต": "น้อยวิกฤต",
            "advice_ล้นตลิ่ง": "อพยพทันที", "advice_มาก": "ระดับน้ำสูง", "advice_ปกติ": "ระดับน้ำปกติ",
            "advice_น้อย": "ระดับน้ำน้อย", "advice_น้อยวิกฤต": "น้อยวิกฤต", "none": "ไม่มีข้อมูล", "wait": "ติดตามสถานการณ์"
        },
        "EN": {
            "ล้นตลิ่ง": "Overflow", "มาก": "High", "ปกติ": "Normal", "น้อย": "Low", "น้อยวิกฤต": "Critical Low",
            "advice_ล้นตลิ่ง": "Evacuate Now", "advice_มาก": "High Water Level", "advice_ปกติ": "Normal Level",
            "advice_น้อย": "Low Water Level", "advice_น้อยวิกฤต": "Critical Low Level", "none": "No Data", "wait": "Stay Alert"
        },
        "JP": {
            "ล้นตลิ่ง": "氾濫", "มาก": "高い", "ปกติ": "通常", "น้อย": "低い", "น้อยวิกฤต": "危機的低水位",
            "advice_ล้นตลิ่ง": "直ちに避難", "advice_มาก": "水位上昇中", "advice_ปกติ": "水位は正常です",
            "advice_น้อย": "水位が低いです", "advice_น้อยวิกฤต": "危機的な低水位", "none": "データなし", "wait": "状況を注視"
        },
        "MY": {
            "ล้นตลิ่ง": "Limpah", "มาก": "Tinggi", "ปกติ": "Normal", "น้อย": "Rendah", "น้อยวิกฤต": "Rendah Kritikal",
            "advice_ล้นตลิ่ง": "Pindah Segera", "advice_มาก": "Aras Air Tinggi", "advice_ปกติ": "Aras Air Normal",
            "advice_น้อย": "Aras Air Rendah", "advice_น้อยวิกฤต": "Aras Air Rendah Kritikal", "none": "Tiada Data", "wait": "Pantau Keadaan"
        }
    }

    t = translations.get(lang, translations["TH"])
    
    status_map = {
        "ล้นตลิ่ง": {"status": t["ล้นตลิ่ง"], "bg_color": "#FEE2E2", "text_color": "#EF4444", "advice": t["advice_ล้นตลิ่ง"]},
        "มาก": {"status": t["มาก"], "bg_color": "#DBEAFE", "text_color": "#3B82F6", "advice": t["advice_มาก"]},
        "ปกติ": {"status": t["ปกติ"], "bg_color": "#D1FAE5", "text_color": "#10B981", "advice": t["advice_ปกติ"]},
        "น้อย": {"status": t["น้อย"], "bg_color": "#FEF9C3", "text_color": "#F59E0B", "advice": t["advice_น้อย"]},
        "น้อยวิกฤต": {"status": t["น้อยวิกฤต"], "bg_color": "#FFEDD5", "text_color": "#F97316", "advice": t["advice_น้อยวิกฤต"]}
    }

    res = status_map.get(situation, {
        "status": t["none"] if not situation else situation,
        "bg_color": "#9CA3AF", "text_color": "#FFFFFF", "advice": t["wait"]
    })
    
    res["diff_text"] = diff_text
    return res

# =============================================================================
# 9. LAZY SYNC SYSTEM (V3 API Primary → V1 Fallback → Sheets)
# =============================================================================
def _load_previous_water_levels(sheets_client, sheet_id):
    """อ่านค่า WaterLevel เดิมจาก Sheets เพื่อใช้คำนวณ Trend"""
    previous = {}
    if not sheets_client or not sheet_id:
        return previous
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("Water_Levels")
        records = ws.get_all_records()
        for row in records:
            code = str(row.get("StationCode", "")).strip()
            wl = row.get("WaterLevel", "-")
            if code and wl not in ["-", "", None]:
                try:
                    previous[code] = float(wl)
                except (ValueError, TypeError):
                    pass
        print(f"[LazySync] Loaded {len(previous)} previous water levels for trend calculation")
    except Exception as e:
        print(f"[LazySync] Could not load previous levels: {e}")
    return previous

def get_water_data_from_api(sheets_client=None, sheet_id=None):
    """
    ดึงข้อมูลระดับน้ำ API Chain: V3 API → V1 Stations+Runoff
    พร้อมคำนวณ Situation และ Trend
    """
    previous_data = {}
    if sheets_client and sheet_id:
        previous_data = _load_previous_water_levels(sheets_client, sheet_id)

    results = []

    # ==== STRATEGY 1: ThaiWater V3 API (Primary) ====
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
                trend = determine_trend(wl, previous_data.get(code))

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
                continue

        print(f"[LazySync] V3 parsed: {len(results)} stations")
        if len(results) > 50:
            return results

    # ==== STRATEGY 2: ThaiWater V1 API (Fallback) ====
    print("[LazySync] V3 insufficient, falling back to V1 API...")
    stations = get_thaiwater_stations(use_cache=True)
    if not stations:
        print("[LazySync] No stations available from V1 cache")
        return results

    # FIX: จำกัดจำนวนสถานี V1 ป้องกัน LINE Webhook Timeout (เกิน 15 วินาทีระบบจะค้าง)
    MAX_FALLBACK = 40 
    print(f"[LazySync] Fetching latest water levels for {MAX_FALLBACK} stations via V1 to prevent timeout...")

    for i, st in enumerate(stations[:MAX_FALLBACK]):
        runoff = get_thaiwater_runoff_latest(st["stationCode"])
        time.sleep(0.05)

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
        trend = determine_trend(wl_value, previous_data.get(code))

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

    print(f"[LazySync] Total processed via Fallback: {len(results)} stations")
    return results

def sync_water_levels_to_sheets(sheets_client, sheet_id):
    """
    อัปเดตข้อมูลระดับน้ำลง Google Sheets (ลบของเก่าเขียนของใหม่ทับ)
    """
    if not sheets_client or not sheet_id:
        print("[LazySync] Sheets client not available")
        return False

    try:
        sheet = sheets_client.open_by_key(sheet_id)

        try:
            ws = sheet.worksheet("Water_Levels")
        except gspread.WorksheetNotFound:
            print("[LazySync] Creating Water_Levels worksheet...")
            ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="12")

        data = get_water_data_from_api(sheets_client, sheet_id)
        if not data:
            print("[LazySync] No data fetched from API")
            return False

        header = ["StationCode", "Name", "River", "Location", "Lat", "Lon",
                  "WaterLevel", "BankLevel", "Situation", "Trend", "Time"]

        rows = [header]
        for st in data:
            situation_text = st["Situation"]
            if isinstance(situation_text, dict):
                situation_text = situation_text.get("status", "ปกติ").replace("🔴 ", "").replace("🟠 ", "").replace("🟢 ", "").replace("🔵 ", "").replace("🟡 ", "").split(" (")[0]

            rows.append([
                st["StationCode"],
                st["Name"],
                st["River"],
                st["Location"],
                st["Lat"],
                st["Lon"],
                st["WaterLevel"],
                st["BankLevel"],
                situation_text,
                st["Trend"],
                st["Time"]
            ])

        print(f"[LazySync] Overwriting {len(rows)} rows to manage sheet limits...")
        
        # FIX: แก้ไขรูปแบบคำสั่ง ws.update ให้รองรับ gspread เวอร์ชันใหม่ ป้องกัน Error ไม่เซฟข้อมูล
        ws.clear()
        ws.update(values=rows, range_name='A1', value_input_option='RAW')

        now_dt = datetime.datetime.now()
        rounded_minute = (now_dt.minute // 15) * 15
        rounded_now = now_dt.replace(minute=rounded_minute, second=0, microsecond=0)
        now_str = rounded_now.strftime("%Y-%m-%d %H:%M:%S")
        ws.update_acell('L1', f"LastSync: {now_str}")

        print(f"[LazySync] Successfully synced {len(data)} stations at {now_str}")
        return True

    except Exception as e:
        print(f"[LazySync] Error syncing to sheets: {e}")
        return False

# =============================================================================
# SUPABASE WATER LEVELS SYNC (ใหม่ - แนะนำใช้แทน/คู่กับ Sheets)
# =============================================================================
def sync_water_levels_to_supabase():
    """Sync ข้อมูลระดับน้ำจาก ThaiWater ไปยัง Supabase"""
    supabase = get_supabase_client()
    if not supabase:
        print("[Supabase Water] Client not available")
        return False

    try:
        data = get_water_data_from_api(None, None)
        if not data:
            return False

        rows_to_upsert = []
        for st in data:
            situation_text = st.get("Situation", "ปกติ")
            if isinstance(situation_text, dict):
                situation_text = situation_text.get("status", "ปกติ")

            rows_to_upsert.append({
                "station_code": st.get("StationCode"),
                "name": st.get("Name"),
                "river": st.get("River"),
                "location": st.get("Location"),
                "lat": st.get("Lat"),
                "lon": st.get("Lon"),
                "water_level": st.get("WaterLevel") if st.get("WaterLevel") not in [None, "-"] else None,
                "bank_level": st.get("BankLevel") if st.get("BankLevel") not in [None, "-"] else None,
                "situation": situation_text,
                "trend": st.get("Trend", "คงที่"),
                "measure_time": st.get("Time"),
                "updated_at": datetime.datetime.now().isoformat()
            })

        if rows_to_upsert:
            chunk_size = 200
            for i in range(0, len(rows_to_upsert), chunk_size):
                chunk = rows_to_upsert[i:i + chunk_size]
                supabase.table("water_levels").upsert(chunk, on_conflict="station_code").execute()
            
            print(f"[Supabase Water] Synced {len(rows_to_upsert)} stations successfully")
            return True
        return False

    except Exception as e:
        print(f"[Supabase Water] Sync error: {e}")
        return False

def get_water_data_from_supabase(user_lat=None, user_lon=None, limit=100):
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

        return records[:3] if user_lat else records
    except Exception as e:
        print(f"[Supabase Water] Query error: {e}")
        return []

def get_water_data_lazy(sheets_client, sheet_id):
    """อ่านข้อมูลระดับน้ำจาก Google Sheets พร้อมระบบ Auto-Refresh (15 นาที)"""
    if not sheets_client or not sheet_id:
        return []

    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("Water_Levels")

        should_sync = False
        try:
            last_sync_raw = ws.acell('L1').value 
            if last_sync_raw and "LastSync:" in last_sync_raw:
                last_sync_str = last_sync_raw.replace("LastSync:", "").strip()
                last_sync_dt = datetime.datetime.strptime(last_sync_str, "%Y-%m-%d %H:%M:%S")
                
                now_dt = datetime.datetime.now()
                current_slot_minute = (now_dt.minute // 15) * 15
                current_slot_dt = now_dt.replace(minute=current_slot_minute, second=0, microsecond=0)
                
                if last_sync_dt < current_slot_dt:
                    print(f"[LazySync] Data is from previous slot. Triggering auto-refresh...")
                    should_sync = True
            else:
                should_sync = True
        except Exception as te:
            should_sync = True

        if should_sync:
            sync_success = sync_water_levels_to_sheets(sheets_client, sheet_id)
            if not sync_success:
                print("[LazySync] Auto-refresh failed, using existing data.")

        records = ws.get_all_records()
        return records

    except Exception as e:
        print(f"[LazySync] Error in lazy data management: {e}")
        return []

def get_water_data_from_sheets(sheets_client, sheet_id, user_lat, user_lon):
    water_stations = []
    try:
        records = get_water_data_lazy(sheets_client, sheet_id)
        for row in records:
            try:
                st_lat = float(row.get('Lat', 0))
                st_lon = float(row.get('Lon', 0))
                if st_lat == 0 and st_lon == 0:
                    continue
                distance = calculate_distance(user_lat, user_lon, st_lat, st_lon)

                wl_value = row.get('WaterLevel', '-')
                bl_value = row.get('BankLevel', '-')
                situation = row.get('Situation', 'ปกติ')
                trend = row.get('Trend', 'คงที่')

                water_stations.append({
                    "stationName": row.get('Name', 'ไม่ระบุชื่อ'),
                    "provinceName": row.get('Location', ''),
                    "riverName": row.get('River', ''),
                    "latitude": st_lat,
                    "longitude": st_lon,
                    "distance_km": distance,
                    "water_level": {"value": wl_value, "uom": "m"},
                    "bank_level": bl_value,
                    "situation": situation,
                    "trend": trend,
                    "measure_time": row.get('Time', '-'),
                    "source": "sheets"
                })
            except (ValueError, TypeError):
                continue

        water_stations.sort(key=lambda x: x["distance_km"])
        return water_stations[:3]
    except Exception as e:
        print(f"[Fallback Sheets] Error: {e}")
        return []

# =============================================================================
# 10. USER REGISTRATION (CHECK FROM SHEETS - PERSISTENT)
# =============================================================================
def is_user_registered(sheets_client, sheet_id, user_id):
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
            pass

    if not sheets_client or not sheet_id:
        if user_id in USER_DATA:
            d = USER_DATA[user_id]
            if "first_name" in d:
                return True, d.get("first_name", ""), d.get("last_name", ""), d.get("phone", "-")
        return False, "", "", "-"

    try:
        sheet = sheets_client.open_by_key(sheet_id)
        users_ws = sheet.worksheet("users")
        rows = users_ws.get_all_records()
        for r in rows:
            if str(r.get("user_id")) == user_id:
                fn = r.get("first_name", "ผู้แจ้ง")
                ln = r.get("last_name", "")
                ph = r.get("phone", "-")
                if user_id not in USER_DATA:
                    USER_DATA[user_id] = {}
                USER_DATA[user_id]["first_name"] = fn
                USER_DATA[user_id]["last_name"] = ln
                USER_DATA[user_id]["phone"] = ph
                return True, fn, ln, ph
    except Exception as e:
        print(f"[UserReg] Failed to check sheets: {e}")

    if user_id in USER_DATA:
        d = USER_DATA[user_id]
        if "first_name" in d:
            return True, d.get("first_name", ""), d.get("last_name", ""), d.get("phone", "-")

    return False, "", "", "-"

def register_user_to_sheets(sheets_client=None, sheet_id=None, user_id=None, first_name=None, last_name=None, phone=None):
    supabase = get_supabase_client()
    if supabase and user_id:
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
            if user_id not in USER_DATA:
                USER_DATA[user_id] = {}
            USER_DATA[user_id]["first_name"] = first_name
            USER_DATA[user_id]["last_name"] = last_name
            USER_DATA[user_id]["phone"] = phone
            return True
        except Exception as e:
            print(f"[Supabase UserReg] Error: {e}")

    if sheets_client and sheet_id and user_id:
        try:
            sheet = sheets_client.open_by_key(sheet_id)
            users_ws = sheet.worksheet("users")
            register_date = datetime.datetime.now().strftime("%Y-%m-%d")
            users_ws.append_row([user_id, first_name, last_name, phone, register_date, "ACTIVE"])
            if user_id not in USER_DATA:
                USER_DATA[user_id] = {}
            USER_DATA[user_id]["first_name"] = first_name
            USER_DATA[user_id]["last_name"] = last_name
            USER_DATA[user_id]["phone"] = phone
            return True
        except Exception as e:
            print(f"[Legacy Sheets] Register error: {e}")

    if user_id:
        if user_id not in USER_DATA:
            USER_DATA[user_id] = {}
        USER_DATA[user_id]["first_name"] = first_name or ""
        USER_DATA[user_id]["last_name"] = last_name or ""
        USER_DATA[user_id]["phone"] = phone or "-"
    return supabase is not None

# =============================================================================
# 11. WATER LEVEL REPORT BUILDERS
# =============================================================================
def build_water_level_text_report(user_lat, user_lon, timestamp, stations, weather_info=None, water_flow=None):
    lines = [
        "🌊 รายงานระดับน้ำจากสถานีใกล้คุณ",
        f"📍 พิกัด: {user_lat:.4f}, {user_lon:.4f}",
        f"🕒 อัปเดตวันนี้ {timestamp}",
        ""
    ]

    lines.append("📡 ข้อมูลจากสถานี ThaiWater ใกล้คุณ:")
    lines.append("")

    if not stations:
        lines.append("⚠️ ไม่พบสถานีตรวจวัดในพื้นที่ใกล้เคียง")
    else:
        for i, st in enumerate(stations, 1):
            wl = st.get("water_level")
            distance = st.get("distance_km", 0)
            situation = st.get("situation")
            
            if wl and wl.get("value") not in [None, "-", ""]:
                try:
                    wl_value = float(wl["value"])
                    bl = st.get("bank_level")
                    assessment = assess_water_level_status(wl_value, bl if bl not in [None, "-", ""] else None, situation)
                    lines.append(f"{i}. {st['stationName']} (ห่าง {distance:.2f} กม.)")
                    lines.append(f"   [{assessment['status']}] {assessment['advice']}")
                    lines.append(f"   ระดับน้ำ: {wl_value:.2f} ม. | ตลิ่ง: {st.get('bank_level', '-')} ม.")
                    lines.append(f"   ต่ำกว่าตลิ่ง: {assessment['diff_text']} ม.")
                except (ValueError, TypeError):
                    lines.append(f"{i}. {st['stationName']} (ห่าง {distance:.2f} กม.)")
            else:
                lines.append(f"{i}. {st['stationName']} (ห่าง {distance:.2f} กม.)")
                lines.append(f"   ไม่มีข้อมูลระดับน้ำ")
            lines.append("")

    lines.append(f"📌 อ้างอิง: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)")
    lines.append(f"🔗 ดูข้อมูลเพิ่มเติมที่ ThaiWater: {THAIWATER_WEB_URL}")

    return "\n".join(lines)

def build_sos_form_flex(user_name="คุณ", lang="TH"):
    """สร้าง SOS Flex Form สีแดงมินิมอลสำหรับการแจ้งเหตุแบบรวดเร็ว (รองรับหลายภาษา)"""
    translations = {
        "TH": {"alt": "🚨 แจ้งเหตุฉุกเฉิน SOS", "title": "🚨 แจ้งเหตุฉุกเฉิน SOS", "hi": "สวัสดีครับคุณ", "info": "โปรดระบุข้อมูลเพื่อประสานงานกู้ภัย:", "loc": "📍 ตำแหน่งที่เกิดเหตุ", "btn_loc": "แชร์พิกัดปัจจุบัน", "wl": "🌊 ระดับน้ำปัจจุบัน:", "cri": "วิกฤต", "high": "สูง", "norm": "ปกติ", "grp": "👥 กลุ่มผู้ประสบภัย:", "child": "👶 เด็กเล็ก/คนชรา", "sick": "🚑 ผู้ป่วยติดเตียง", "pet": "🐶 สัตว์เลี้ยง", "footer": "*ข้อมูลจะถูกส่งไปยังทีมกู้ภัยทันที"},
        "EN": {"alt": "🚨 SOS Emergency", "title": "🚨 SOS Emergency", "hi": "Hello", "info": "Please provide info for rescue:", "loc": "📍 Incident Location", "btn_loc": "Share Current Location", "wl": "🌊 Current Water Level:", "cri": "Critical", "high": "High", "norm": "Normal", "grp": "👥 Victim Groups:", "child": "👶 Children/Elderly", "sick": "🚑 Bedridden Patients", "pet": "🐶 Pets", "footer": "*Data sent to rescue team immediately"},
        "JP": {"alt": "🚨 SOS 緊急通報", "title": "🚨 SOS 緊急通報", "hi": "こんにちは", "info": "救助のための情報を提供してください:", "loc": "📍 発生場所", "btn_loc": "現在地を共有", "wl": "🌊 現在の水位:", "cri": "危機的", "high": "高い", "norm": "通常", "grp": "👥 被災者グループ:", "child": "👶 子供/高齢者", "sick": "🚑 寝たきり患者", "pet": "🐶 ペット", "footer": "*データは直ちに救助隊に送信されます"},
        "MY": {"alt": "🚨 Kecemasan SOS", "title": "🚨 Kecemasan SOS", "hi": "Helo", "info": "Sila berikan maklumat untuk menyelamat:", "loc": "📍 Lokasi Kejadian", "btn_loc": "Kongsi Lokasi Semasa", "wl": "🌊 Aras Air Semasa:", "cri": "Kritikal", "high": "Tinggi", "norm": "Normal", "grp": "👥 Kumpulan Mangsa:", "child": "👶 Kanak-kanak/Warga Emas", "sick": "🚑 Pesakit Terlantar", "pet": "🐶 Haiwan Peliharaan", "footer": "*Data dihantar ke pasukan penyelamat segera"}
    }
    t = translations.get(lang, translations["TH"])

    return FlexSendMessage(
        alt_text=t["alt"],
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#EF4444")),
            header=BoxComponent(
                layout="vertical",
                contents=[
                    TextComponent(text=t["title"], weight="bold", size="lg", color="#FFFFFF", align="center")
                ]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text=f"{t['hi']} {user_name}", size="sm", color="#4B5563", weight="bold"),
                    TextComponent(text=t["info"], size="xs", color="#9CA3AF"),
                    
                    # ส่วนที่ 1: พิกัด
                    BoxComponent(
                        layout="vertical",
                        background_color="#FEE2E2",
                        corner_radius="md",
                        padding_all="md",
                        contents=[
                            TextComponent(text=t["loc"], size="xs", color="#B91C1C", weight="bold"),
                            ButtonComponent(
                                action=LocationAction(label=t["btn_loc"]),
                                style="primary",
                                color="#EF4444",
                                margin="sm",
                                height="sm"
                            )
                        ]
                    ),
                    
                    # ส่วนที่ 2: ระดับความรุนแรง
                    TextComponent(text=t["wl"], size="xs", color="#4B5563", margin="md"),
                    BoxComponent(
                        layout="horizontal",
                        spacing="sm",
                        contents=[
                            ButtonComponent(action=MessageAction(label=t["cri"], text=f"🚨 {t['wl']} {t['cri']}"), style="outline", color="#EF4444", height="sm"),
                            ButtonComponent(action=MessageAction(label=t["high"], text=f"🌊 {t['wl']} {t['high']}"), style="outline", color="#EF4444", height="sm"),
                            ButtonComponent(action=MessageAction(label=t["norm"], text=f"✅ {t['wl']} {t['norm']}"), style="outline", color="#10B981", height="sm")
                        ]
                    ),
                    
                    # ส่วนที่ 3: กลุ่มผู้ประสบภัย
                    TextComponent(text=t["grp"], size="xs", color="#4B5563", margin="md"),
                    BoxComponent(
                        layout="vertical",
                        spacing="xs",
                        contents=[
                            ButtonComponent(action=MessageAction(label=t["child"], text=t["child"]), style="secondary", height="sm", color="#F3F4F6"),
                            ButtonComponent(action=MessageAction(label=t["sick"], text=t["sick"]), style="secondary", height="sm", color="#F3F4F6"),
                            ButtonComponent(action=MessageAction(label=t["pet"], text=t["pet"]), style="secondary", height="sm", color="#F3F4F6")
                        ]
                    )
                ]
            ),
            footer=BoxComponent(
                layout="vertical",
                contents=[
                    TextComponent(text=t.get("footer", "*Data sent to rescue team immediately"), size="xxs", color="#9CA3AF", align="center", margin="sm")
                ]
            )
        )
    )

def _build_needs_summary_flex(user_id, user_data):
    """สร้างการ์ดสรุปคำขอความช่วยเหลือ (ความต้องการสิ่งของบรรเทาทุกข์)
    พร้อมปุ่ม 'ยืนยัน' / 'ยกเลิก' ฝังอยู่บนการ์ด กดได้ทันทีไม่ต้องพิมพ์"""
    categories = user_data.get("need_categories", []) or []
    category_text = ", ".join(categories) if categories else "-"
    details_text = user_data.get("need_details", "-") or "-"
    urgency_text = user_data.get("need_urgency", "-") or "-"
    lat = user_data.get("need_latitude", "-")
    lon = user_data.get("need_longitude", "-")

    if "ด่วนมาก" in urgency_text:
        urgency_color = "#EF4444"
    elif "ปานกลาง" in urgency_text:
        urgency_color = "#F59E0B"
    else:
        urgency_color = "#10B981"

    return FlexSendMessage(
        alt_text="📋 สรุปคำขอความช่วยเหลือ โปรดตรวจสอบและยืนยัน",
        contents=BubbleContainer(
            styles=BubbleStyle(header=BlockStyle(background_color="#2563EB")),
            header=BoxComponent(
                layout="vertical",
                contents=[
                    TextComponent(text="📋 สรุปคำขอความช่วยเหลือ", weight="bold", size="lg", color="#FFFFFF", align="center")
                ]
            ),
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text="โปรดตรวจสอบรายการก่อนยืนยันครับ", size="xs", color="#9CA3AF"),

                    BoxComponent(
                        layout="vertical",
                        background_color="#EFF6FF",
                        corner_radius="md",
                        padding_all="md",
                        spacing="sm",
                        contents=[
                            TextComponent(text="📦 หมวดหมู่", size="xs", color="#2563EB", weight="bold"),
                            TextComponent(text=category_text, size="sm", color="#111827", wrap=True),
                        ]
                    ),

                    BoxComponent(
                        layout="vertical",
                        background_color="#F3F4F6",
                        corner_radius="md",
                        padding_all="md",
                        spacing="sm",
                        margin="sm",
                        contents=[
                            TextComponent(text="📝 รายละเอียด", size="xs", color="#4B5563", weight="bold"),
                            TextComponent(text=details_text, size="sm", color="#111827", wrap=True),
                        ]
                    ),

                    BoxComponent(
                        layout="horizontal",
                        margin="sm",
                        contents=[
                            TextComponent(text="⏳ ความเร่งด่วน", size="xs", color="#4B5563", weight="bold", flex=1),
                            TextComponent(text=urgency_text, size="xs", color=urgency_color, weight="bold", align="end", wrap=True, flex=2)
                        ]
                    ),

                    SeparatorComponent(margin="md"),

                    TextComponent(
                        text=f"📍 พิกัด: {lat}, {lon}",
                        size="xxs",
                        color="#9CA3AF",
                        margin="sm",
                        wrap=True
                    )
                ]
            ),
            footer=BoxComponent(
                layout="horizontal",
                spacing="sm",
                contents=[
                    ButtonComponent(
                        action=MessageAction(label="❌ ยกเลิก", text="ยกเลิก"),
                        style="secondary",
                        height="sm",
                        color="#F3F4F6"
                    ),
                    ButtonComponent(
                        action=MessageAction(label="✅ ยืนยัน", text="ยืนยัน"),
                        style="primary",
                        height="sm",
                        color="#10B981"
                    )
                ]
            )
        )
    )


def build_ai_response_flex(ai_text, original_question, lang="TH"):
    """สร้างกล่องคำตอบ AI พร้อมปุ่ม Research ข้อมูลเชิงลึก (รองรับหลายภาษา)"""
    translations = {
        "TH": {"alt": "🤖 คำตอบจาก AI", "info": "ต้องการข้อมูลเชิงลึกเรื่องความปลอดภัย/การรักษา?", "btn": "🔍 ค้นหาข้อมูลวิจัย (Research AI)", "cmd": "Research:"},
        "EN": {"alt": "🤖 AI Response", "info": "Need in-depth safety/medical info?", "btn": "🔍 Research AI", "cmd": "Research:"},
        "JP": {"alt": "🤖 AIの回答", "info": "安全性や医療に関する詳細情報が必要ですか？", "btn": "🔍 詳細リサーチ (Research AI)", "cmd": "Research:"},
        "MY": {"alt": "🤖 Jawapan AI", "info": "Perlu info keselamatan/perubatan mendalam?", "btn": "🔍 Penyelidikan AI", "cmd": "Research:"}
    }
    t = translations.get(lang, translations["TH"])

    return FlexSendMessage(
        alt_text=t["alt"],
        contents=BubbleContainer(
            body=BoxComponent(
                layout="vertical",
                contents=[
                    BoxComponent(
                        layout="horizontal",
                        contents=[
                            TextComponent(text="🤖 FLOODCARE AI", weight="bold", size="sm", color="#1E40AF", flex=1),
                            TextComponent(text="BETA", size="xxs", color="#9CA3AF", align="end")
                        ]
                    ),
                    SeparatorComponent(margin="md"),
                    TextComponent(text=ai_text, wrap=True, size="sm", color="#374151", margin="md"),
                    
                    # ปุ่ม Research ข้อมูลเชิงลึก
                    BoxComponent(
                        layout="vertical",
                        margin="xl",
                        spacing="sm",
                        contents=[
                            TextComponent(text=t["info"], size="xxs", color="#9CA3AF", align="center"),
                            ButtonComponent(
                                action=MessageAction(label=t["btn"], text=f"{t['cmd']} {original_question}"),
                                style="primary",
                                color="#1E40AF",
                                height="sm"
                            )
                        ]
                    )
                ]
            )
        )
    )

def build_language_selector_flex():
    """สร้างแผ่นเลือกภาษามินิมอล"""
    return FlexSendMessage(
        alt_text="🌐 เลือกภาษา / Select Language",
        contents=BubbleContainer(
            size="sm",
            body=BoxComponent(
                layout="vertical",
                spacing="md",
                contents=[
                    TextComponent(text="🌐 Language Settings", weight="bold", size="md", color="#1F2937", align="center"),
                    TextComponent(text="โปรดเลือกภาษาที่ต้องการใช้งาน\nPlease select your language", size="xxs", color="#9CA3AF", align="center", wrap=True),
                    SeparatorComponent(margin="md"),
                    ButtonComponent(
                        action=MessageAction(label="[ TH ] ภาษาไทย", text="ตั้งค่าภาษา: TH"),
                        style="secondary",
                        color="#F3F4F6",
                        height="sm"
                    ),
                    ButtonComponent(
                        action=MessageAction(label="[ EN ] English", text="ตั้งค่าภาษา: EN"),
                        style="secondary",
                        color="#F3F4F6",
                        height="sm"
                    ),
                    ButtonComponent(
                        action=MessageAction(label="[ JP ] 日本語", text="ตั้งค่าภาษา: JP"),
                        style="secondary",
                        color="#F3F4F6",
                        height="sm"
                    ),
                    ButtonComponent(
                        action=MessageAction(label="[ MY ] Bahasa Melayu", text="ตั้งค่าภาษา: MY"),
                        style="secondary",
                        color="#F3F4F6",
                        height="sm"
                    )
                ]
            )
        )
    )

def set_user_language(sheets_client, sheet_id, user_id, lang):
    """บันทึกภาษาที่ผู้ใช้เลือกลงใน Google Sheets"""
    if not sheets_client or not sheet_id:
        return False
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        try:
            ws = sheet.worksheet("User_Settings")
        except:
            ws = sheet.add_worksheet(title="User_Settings", rows="1000", cols="5")
            ws.append_row(["UserID", "Language", "UpdatedAt"])
        
        records = ws.get_all_records()
        row_idx = -1
        for idx, row in enumerate(records):
            if str(row.get("UserID")) == str(user_id):
                row_idx = idx + 2 # +2 เพราะ header และ 0-index
                break
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if row_idx != -1:
            ws.update_cell(row_idx, 2, lang)
            ws.update_cell(row_idx, 3, timestamp)
        else:
            ws.append_row([user_id, lang, timestamp])
        return True
    except Exception as e:
        print(f"Error setting language: {e}")
        return False

def get_user_language(sheets_client, sheet_id, user_id):
    """ดึงค่าภาษาของผู้ใช้จาก Google Sheets"""
    if not sheets_client or not sheet_id:
        return "TH" # Default
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("User_Settings")
        records = ws.get_all_records()
        for row in records:
            if str(row.get("UserID")) == str(user_id):
                return row.get("Language", "TH")
    except:
        pass
    return "TH"

def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, weather_info=None, water_flow=None):
    header_box = BoxComponent(
        layout="vertical",
        contents=[
            TextComponent(text="🌊 ระดับน้ำใกล้คุณ", weight="bold", size="xl", color="#1F2937"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f}", size="xs", color="#6B7280"),
            TextComponent(text=f"🕒 อัปเดตล่าสุด: {timestamp}", size="xs", color="#9CA3AF")
        ]
    )

    stations_box = BoxComponent(
        layout="vertical",
        spacing="md",
        margin="lg",
        contents=[]
    )

    if not stations:
        stations_box.contents.append(
            TextComponent(text="⚠️ ไม่พบสถานีตรวจวัดในพื้นที่ใกล้เคียง", size="sm", color="#EF4444", margin="md")
        )
    else:
        for st in stations:
            wl = st.get("water_level")
            distance = st.get("distance_km", 0)
            
            wl_value = "-"
            assessment = assess_water_level_status(None)

            if wl and wl.get("value") not in [None, "-", ""]:
                try:
                    wl_value = float(wl["value"])
                    bl = st.get("bank_level")
                    situation = st.get("situation") # ดึงสถานะจากข้อมูลสถานี
                    assessment = assess_water_level_status(wl_value, bl if bl not in [None, "-", ""] else None, situation)
                except (ValueError, TypeError):
                    pass

            station_card = BoxComponent(
                layout="vertical",
                contents=[
                    TextComponent(text=f"{st['stationName']} (ห่าง {distance:.2f} กม.)", weight="bold", size="sm", color="#1F2937"),
                    BoxComponent(
                        layout="horizontal",
                        margin="sm",
                        spacing="sm",
                        contents=[
                            BoxComponent(
                                layout="vertical",
                                background_color=assessment.get("bg_color", "#9CA3AF"),
                                corner_radius="xl", # Status Pill shape
                                padding_start="md",
                                padding_end="md",
                                padding_top="xs",
                                padding_bottom="xs",
                                contents=[
                                    TextComponent(text=assessment["status"], size="xs", color=assessment.get("text_color", "#FFFFFF"), weight="bold", align="center")
                                ]
                            ),
                            TextComponent(text=assessment["advice"], size="xs", color="#4B5563", gravity="center")
                        ]
                    ),
                    TextComponent(
                        text=f"ระดับน้ำ: {wl_value} ม. | ตลิ่ง: {st.get('bank_level', '-')} ม.", 
                        size="xs", color="#4B5563", margin="sm"
                    ),
                    BoxComponent(
                        layout="horizontal",
                        contents=[
                            TextComponent(text="ต่ำกว่าตลิ่ง: ", size="xs", color="#4B5563"),
                            TextComponent(text=f"{assessment['diff_text']} ม.", size="xs", color=assessment.get("text_color", "#1F2937"), weight="bold")
                        ]
                    )
                ]
            )
            stations_box.contents.append(station_card)

    footer_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[
            SeparatorComponent(margin="md"),
            TextComponent(text=t["ref"], size="xxs", color="#9CA3AF", margin="sm"),
            ButtonComponent(
                style="link",
                height="sm",
                action=URIAction(label=t["web"], uri=THAIWATER_WEB_URL),
                color="#2563EB"
            )
        ]
    )

    bubble = BubbleContainer(
        body=BoxComponent(
            layout="vertical",
            contents=[
                header_box,
                SeparatorComponent(margin="md"),
                stations_box,
                footer_box
            ]
        )
    )

    return FlexSendMessage(alt_text="รายงานระดับน้ำจากสถานีใกล้คุณ", contents=bubble)

# =============================================================================
# 12. GREETING & QUICK INFO FLEX MESSAGE
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
    # เช็คเวลาเพื่อทักทายพิเศษตอนเช้า
    now = datetime.datetime.now()
    time_greeting = "สวัสดี"
    if 5 <= now.hour < 10:
        time_greeting = "อรุณสวัสดิ์"
    
    text = (
        f"{time_greeting} คุณ {user_name}\n"
        "ผมคือ FLOODCARE AI\n"
        "แชทบอทอัจฉริยะสำหรับ ติดตามสถานการณ์น้ำ แจ้งเหตุฉุกเฉิน และช่วยเหลือผู้ประสบภัยแบบครบวงจรครับ\n\n"
        "🔍 ผมช่วยคุณได้ดังนี้:\n"
        "1. 📞 เบอร์โทรฉุกเฉิน\n"
        "2. 🚨 SOS แจ้งเหตุฉุกเฉิน\n"
        "3. 🏠 ค้นหาศูนย์อพยพใกล้เคียง\n"
        "4. 🌊 ตรวจสอบระดับน้ำแบบเรียลไทม์\n"
        "5. 📦 แจ้งความต้องการของใช้จำเป็น\n"
        "6. 🤖 สอบถามข้อมูลจาก AI อัจฉริยะ\n\n"
        "🤝 ติดต่อและช่วยเหลือผู้ประสบภัยน้ำท่วม\n"
        "ผมพร้อมตอบทุกคำถามและช่วยเหลือคุณตลอด 24 ชั่วโมงครับ 💧😊"
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
# 13. SOS PRIORITY CALCULATION
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
        return False
    try:
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
        return True
    except Exception as e:
        print(f"[LINE] Failed to send notification: {e}")
        return False

# =============================================================================
# 13B. TYPING INDICATOR
# =============================================================================
LINE_LOADING_ANIMATION_URL = "https://api.line.me/v2/bot/chat/loading/start"

def show_loading_animation(user_id, loading_seconds=10):
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
            return False
        return True
    except Exception as e:
        return False

# =============================================================================
# 13C. USER NEEDS MANAGEMENT
# =============================================================================
def save_user_need(sheets_client=None, sheet_id=None, user_id=None, timestamp=None, lat=None, lon=None, category=None, details=None, urgency=None):
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
            return True
        except Exception as e:
            print(f"[Supabase UserNeeds] Error: {e}")

    if sheets_client and sheet_id:
        try:
            sheet = sheets_client.open_by_key(sheet_id)
            try:
                ws = sheet.worksheet("user_needs")
            except:
                ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="12")
                ws.append_row(["Timestamp", "UserID", "Latitude", "Longitude", "Category", "Details", "Urgency", "Status"])
            ws.append_row([timestamp, user_id, lat, lon, category, details, urgency, "PENDING"])
            return True
        except Exception as e:
            print(f"[Legacy Sheets] save_user_need error: {e}")

    return False

def get_all_user_needs(sheets_client, sheet_id):
    supabase = get_supabase_client()
    if supabase:
        try:
            response = supabase.table("user_needs").select("*").order("timestamp", desc=True).limit(500).execute()
            if response.data:
                return response.data
        except Exception as e:
            pass

    if not sheets_client or not sheet_id:
        return []
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("user_needs")
        return ws.get_all_records()
    except Exception as e:
        return []

def update_need_status(sheets_client, sheet_id, timestamp, user_id, new_status):
    supabase = get_supabase_client()
    if supabase:
        try:
            response = supabase.table("user_needs").update({"status": new_status}).eq("timestamp", timestamp).eq("user_id", str(user_id)).execute()
            if response.data:
                return True
        except Exception as e:
            pass

    if not sheets_client or not sheet_id:
        return False
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("user_needs")
        rows = ws.get_all_records()
        for i, row in enumerate(rows, start=2):
            if row.get("Timestamp") == timestamp and row.get("UserID") == user_id:
                ws.update_cell(i, 8, new_status)
                return True
        return False
    except Exception as e:
        return False

# =============================================================================
# 14. SHELTER VACANCY CHECK
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
# 15. GOOGLE SHEETS AUTO-SETUP
# =============================================================================
def setup_sheets_automatically(sheet):
    try:
        existing_sheets = [w.title for w in sheet.worksheets()]

        if "users" not in existing_sheets:
            users_ws = sheet.add_worksheet(title="users", rows="3000", cols="10")
            users_ws.append_row(["user_id", "first_name", "last_name", "phone", "register_date", "status"])

        if "sos_requests" not in existing_sheets:
            sos_ws = sheet.add_worksheet(title="sos_requests", rows="3000", cols="25")
            sos_ws.append_row([
                "request_id", "user_id", "timestamp", "latitude", "longitude",
                "group_count", "group_types", "urgency_level", "photo_url",
                "water_level", "note", "priority", "status", "responder_name",
                "responder_notes", "accepted_at", "completed_at"
            ])

        if "Shelters" not in existing_sheets:
            shelters_ws = sheet.add_worksheet(title="Shelters", rows="1000", cols="15")
            shelters_ws.append_row([
                "ShelterID", "Name", "Province", "District", "Latitude",
                "Longitude", "Capacity", "Occupancy", "Status",
                "Beds", "Toilets", "Parking", "Facilities"
            ])
            mock_rows = [
                ["SH001", "ศูนย์อพยพโรงเรียนโคกสมานคุณ (หาดใหญ่)", "สงขลา", "หาดใหญ่", "7.0095", "100.4682", "500", "120", "ว่าง", "300", "40", "100", "ไฟฟ้า, น้ำสะอาด, มีแพทย์ประจำ"],
                ["SH002", "ศูนย์อพยพโรงเรียนวัดสุทัศน์ (กทม)", "กรุงเทพ", "พระนคร", "13.7511", "100.5002", "150", "45", "ว่าง", "100", "15", "20", "ไฟฟ้า, อินเทอร์เน็ต"],
                ["SH003", "ศูนย์เยาวชนกรุงเทพมหานคร (กทม)", "กรุงเทพ", "ดินแดง", "13.7654", "100.5231", "300", "300", "เต็ม", "200", "30", "50", "ไฟฟ้า, น้ำสะอาด, รองรับผู้พิการ"]
            ]
            for r in mock_rows:
                shelters_ws.append_row(r)

        if "Water_Levels" not in existing_sheets:
            water_ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="12")
            water_ws.append_row([
                "StationCode", "Name", "River", "Location", "Lat", "Lon",
                "WaterLevel", "BankLevel", "Situation", "Trend", "Time"
            ])

        if "Contacts" not in existing_sheets:
            contacts_ws = sheet.add_worksheet(title="Contacts", rows="1000", cols="10")
            contacts_ws.append_row(["ContactID", "Name", "Role", "Phone"])
            contact_rows = [
                ["CT001", "ปภ. (กรมป้องกันและบรรเทาสาธารณภัย)", "รับแจ้งเหตุเตือนภัยและช่วยเหลืออุทกภัยสายด่วน", "1784"],
                ["CT002", "สพฉ. (สถาบันการแพทย์ฉุกเฉินแห่งชาติ)", "รับส่งต่อผู้ป่วยและเจ็บป่วยฉุกเฉินทางการแพทย์", "1669"],
                ["CT003", "ตำรวจทางหลวง", "ประสานงานความช่วยเหลือเส้นทางน้ำท่วมและดินถล่ม", "1193"]
            ]
            for r in contact_rows:
                contacts_ws.append_row(r)

        if "user_needs" not in existing_sheets:
            needs_ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="12")
            needs_ws.append_row(["Timestamp", "UserID", "Latitude", "Longitude",
                                 "Category", "Details", "Urgency", "Status"])

        if "AI Logs" not in existing_sheets:
            logs_ws = sheet.add_worksheet(title="AI Logs", rows="5000", cols="5")
            logs_ws.append_row(["Timestamp", "UserID", "Question", "Answer"])

        for default_name in ["ชีต1", "Sheet1"]:
            if default_name in existing_sheets:
                try:
                    default_ws = sheet.worksheet(default_name)
                    sheet.del_worksheet(default_ws)
                except:
                    pass
        print("Auto-setup Google Sheets completed successfully!")
    except Exception as e:
        print(f"Error in automatic sheet setup: {e}")

# =============================================================================
# 16. GOOGLE SHEETS CLIENT INITIALIZATION
# =============================================================================
SHEETS_INITIALIZED = False
LAST_SHEETS_ERROR = "ยังไม่ได้เปิดใช้งาน"

def get_sheets_client():
    global SHEETS_INITIALIZED, LAST_SHEETS_ERROR
    clean_sheet_id = extract_sheet_id(GOOGLE_SHEET_ID)

    if not GOOGLE_SERVICE_ACCOUNT_JSON or not clean_sheet_id:
        print("Warning: Google Sheets variables not configured")
        return None
    try:
        json_str = GOOGLE_SERVICE_ACCOUNT_JSON.strip()
        if json_str.startswith("'") and json_str.endswith("'"):
            json_str = json_str[1:-1].strip()
        if json_str.startswith('"') and json_str.endswith('"'):
            json_str = json_str[1:-1].strip()

        creds_dict = json.loads(json_str)
        client = gspread.service_account_from_dict(creds_dict)

        if not SHEETS_INITIALIZED:
            try:
                sheet = client.open_by_key(clean_sheet_id)
                setup_sheets_automatically(sheet)
                SHEETS_INITIALIZED = True
                LAST_SHEETS_ERROR = "เชื่อมต่อสำเร็จ"
            except Exception as setup_err:
                LAST_SHEETS_ERROR = f"สิทธิ์ไม่ผ่าน: {setup_err}"
                print(f"Auto-setup failed: {setup_err}")

        return client
    except Exception as e:
        LAST_SHEETS_ERROR = f"JSON Key ไม่ถูกต้อง: {e}"
        print(f"Error initializing Sheets client: {e}")
        return None
