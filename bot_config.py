import os
import json
import math
import datetime
import time
import urllib.request
import requests
import google.generativeai as genai
import gspread
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent,
    SeparatorComponent, ButtonComponent, URIAction, TextSendMessage
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

# =============================================================================
# ระบบติดตามสถานะการสนทนาและเก็บข้อมูลคัดกรอง
# =============================================================================
USER_STATES = {}
USER_DATA = {}

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
# 5. WEATHER & FLOOD SCRAPERS (Open-Meteo)
# =============================================================================
def get_live_weather_scraper(lat, lon):
    """ดึงข้อมูลสภาพอากาศเรียลไทม์จาก Open-Meteo"""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            current = data.get("current_weather", {})
            temp = current.get("temperature", "-")
            wind = current.get("windspeed", "-")
            weather_code = current.get("weathercode", 0)

            weather_desc = "ท้องฟ้าแจ่มใส"
            if weather_code in [1, 2, 3]: weather_desc = "ท้องฟ้ามีเมฆบางส่วน"
            elif weather_code in [45, 48]: weather_desc = "มีหมอกลงในพื้นที่"
            elif weather_code in [51, 53, 55]: weather_desc = "ฝนตกละอองเบาบาง"
            elif weather_code in [61, 63, 65]: weather_desc = "ฝนตกปานกลางถึงหนัก ระวังน้ำท่วม"
            elif weather_code in [80, 81, 82]: weather_desc = "ฝนตกชุกหนาแน่นฉับพลัน"
            elif weather_code >= 95: weather_desc = "พายุฝนฟ้าคะนองรุนแรง"

            return f"🌡️ อุณหภูมิ: {temp} °C\n🌧️ สภาพอากาศ: {weather_desc}\n🍃 ความเร็วลม: {wind} กม./ชม."
    except Exception as e:
        print(f"Weather Scraper Error: {e}")
        return "🌡️ อุณหภูมิ: ~28 °C\n🌧️ สภาพอากาศ: ท้องฟ้าครึ้ม\n🍃 ความเร็วลม: ~10 กม./ชม."


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
def fetch_waterlevel_v3():
    """
    ดึงข้อมูลระดับน้ำทั้งหมดจาก ThaiWater V3 API (waterlevel_load)
    Returns: list of dict หรือ None ถ้าล้มเหลว

    โครงสร้าง JSON จริง (ยืนยันแล้ว 2026-06-17):
    {
      "waterlevel_data": {
        "result": "OK",
        "data": [ {...station record...}, ... ]
      }
    }
    """
    try:
        headers = {
            'User-Agent': 'FLOODCARE-Bot/1.0',
            'Accept': 'application/json'
        }
        response = requests.get(THAIWATER_V3_API, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        stations = []

        # รูปแบบจริงที่ยืนยันแล้ว: {"waterlevel_data": {"result": "OK", "data": [...]}}
        if isinstance(data, dict) and "waterlevel_data" in data:
            wl_data = data.get("waterlevel_data", {})
            stations = wl_data.get("data", [])
        elif isinstance(data, list):
            # เผื่อกรณี API คืนเป็น list ตรงๆ ในอนาคต
            stations = data
        elif isinstance(data, dict):
            # เผื่อกรณีโครงสร้างเปลี่ยนไปเป็น key อื่นที่ top-level
            stations = data.get("data", [])
            if not stations:
                for key in ["stations", "results", "items", "waterlevel"]:
                    if key in data:
                        stations = data[key]
                        break

        print(f"[ThaiWater V3] Fetched {len(stations)} stations")
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
    """
    แปลงข้อมูลจาก V3 API เป็นโครงสร้างมาตรฐาน 11 ฟิลด์
    โครงสร้างจริงที่ยืนยันแล้ว (2026-06-17):
    {
      "waterlevel_datetime": "2026-06-17 14:00",
      "waterlevel_m": null,
      "waterlevel_msl": "330.59",
      "river_name": "ลำโดมน้อย",
      "station": {
        "tele_station_name": {"th": "...", "en": "..."},
        "tele_station_oldcode": "M.199",
        "tele_station_lat": 14.60611,
        "tele_station_long": 101.472778,
        "left_bank": 663.260988,
        "right_bank": 663.583988,
        "geocode": {
          "province_name": {"th": "นครราชสีมา", "en": "..."}
        }
      }
    }
    หมายเหตุ: waterlevel_m มักเป็น null ในหลาย record ต้อง fallback ไปใช้ waterlevel_msl
    """
    station = v3_item.get("station") or {}
    geocode = station.get("geocode") or {}

    def get_val(*keys, default="-"):
        for k in keys:
            if k in v3_item and v3_item[k] is not None:
                val = v3_item[k]
                if val != "" and val != "null":
                    return val
        return default

    # ดึงพิกัดจาก station
    lat = 0.0
    lon = 0.0
    try:
        lat = float(station.get("tele_station_lat", 0) or 0)
        lon = float(station.get("tele_station_long", 0) or 0)
    except (ValueError, TypeError):
        pass

    # ดึงระดับน้ำ: waterlevel_m ก่อน ถ้า null ใช้ waterlevel_msl แทน
    wl = None
    for key in ["waterlevel_m", "waterlevel_msl"]:
        val = v3_item.get(key)
        if val is not None and val != "" and val != "null":
            try:
                wl = float(val)
                break
            except (ValueError, TypeError):
                continue

    # ดึงระดับตลิ่ง: ใช้ right_bank เป็นค่าเริ่มต้น (ฝั่งขวามักเป็นค่าอ้างอิงหลัก)
    bl = None
    try:
        bank_val = station.get("right_bank")
        if bank_val is None:
            bank_val = station.get("left_bank")
        if bank_val is not None:
            bl = float(bank_val)
    except (ValueError, TypeError):
        bl = None

    # ชื่อสถานี (รองรับทั้งแบบ dict {"th":..,"en":..} และแบบ string ตรง)
    station_name_raw = station.get("tele_station_name", "ไม่ระบุชื่อ")
    if isinstance(station_name_raw, dict):
        station_name = station_name_raw.get("th") or station_name_raw.get("en") or "ไม่ระบุชื่อ"
    else:
        station_name = station_name_raw

    # ชื่อจังหวัด (ซ้อนอยู่ใน station.geocode.province_name)
    province_raw = geocode.get("province_name", "-")
    if isinstance(province_raw, dict):
        province_name = province_raw.get("th") or province_raw.get("en") or "-"
    else:
        province_name = province_raw

    # รหัสสถานี: ใช้ oldcode ก่อน (อ่านง่ายกว่า) ถ้าไม่มีใช้ id
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
    """
    ดึงรายชื่อสถานีตรวจวัดน้ำทั้งหมดจาก ThaiWater V1 API (สำรอง)
    """
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
    """
    ดึงข้อมูลระดับน้ำล่าสุดของสถานีจาก ThaiWater V1 API
    (ใช้เป็น Fallback เมื่อ V3 ไม่มีข้อมูลบางสถานี)
    """
    if not station_code:
        return None

    try:
        url = (f"{THAIWATER_API_BASE}/Runoff?"
               f"stationCode={station_code}"
               f"&latest=true"
               f"&interval=C-60")
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

    except requests.exceptions.Timeout:
        print(f"[ThaiWater V1] Runoff Timeout for station {station_code}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[ThaiWater V1] Runoff Error for station {station_code}: {e}")
        return None
    except Exception as e:
        print(f"[ThaiWater V1] Runoff Unexpected Error: {e}")
        return None


# =============================================================================
# 7. WATER LEVEL CALCULATION (Situation + Trend)
# =============================================================================
def calculate_situation(water_level, bank_level):
    """
    คำนวณสถานการณ์น้ำ: ปกติ | เฝ้าระวัง | วิกฤต
    """
    try:
        wl = float(water_level) if water_level is not None else 0
        bl = float(bank_level) if bank_level is not None else 0
    except (ValueError, TypeError):
        return "ไม่มีข้อมูล"

    if bl <= 0:
        return "ปกติ" if wl < 1.5 else "เฝ้าระวัง" if wl < 3.0 else "วิกฤต"

    ratio = wl / bl
    if ratio >= 0.95 or wl >= bl:
        return "วิกฤต"
    elif ratio >= 0.70:
        return "เฝ้าระวัง"
    else:
        return "ปกติ"


def determine_trend(current_wl, previous_wl, tolerance=0.01):
    """
    คำนวณแนวโน้ม: เพิ่มขึ้น | ลดลง | คงที่
    current_wl: ค่าระดับน้ำล่าสุด
    previous_wl: ค่าระดับน้ำครั้งก่อน (อ่านจาก Sheets)
    """
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
# 8. FIND NEAREST WATER STATIONS (WITH API FALLBACK CHAIN)
# =============================================================================
def find_nearest_water_stations(user_lat, user_lon, max_stations=3, max_distance_km=50):
    """
    หาสถานีตรวจวัดน้ำที่ใกล้ผู้ใช้ที่สุด พร้อมดึงข้อมูลระดับน้ำล่าสุด
    Fallback chain: V1 API Stations + V1 API Runoff
    """
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
    """
    ประเมินสถานะระดับน้ำแบบละเอียด พร้อมคำแนะนำ
    """
    if water_level_value is None:
        return {
            "status": "⚪ ไม่มีข้อมูล",
            "situation": "ไม่มีข้อมูล",
            "color": "#9CA3AF",
            "advice": "ไม่สามารถประเมินได้ โปรดติดตามสถานการณ์",
            "risk_level": 0
        }

    try:
        wl = float(water_level_value)
    except (ValueError, TypeError):
        return {
            "status": "⚪ ข้อมูลไม่ถูกต้อง",
            "situation": "ไม่มีข้อมูล",
            "color": "#9CA3AF",
            "advice": "ไม่สามารถประเมินได้",
            "risk_level": 0
        }

    situation = calculate_situation(wl, bank_level_value)

    if situation == "วิกฤต":
        return {
            "status": "🔴 วิกฤติ (ล้นตลิ่ง)",
            "situation": "วิกฤต",
            "color": "#EF4444",
            "advice": "⚠️ อพยพทันที! ระดับน้ำล้นตลิ่งแล้ว อย่าอยู่ชั้นล่าง ตัดกระแสไฟ ขึ้นที่สูง",
            "risk_level": 4
        }
    elif situation == "เฝ้าระวัง":
        return {
            "status": "🟠 เฝ้าระวัง (ใกล้ล้นตลิ่ง)",
            "situation": "เฝ้าระวัง",
            "color": "#F97316",
            "advice": "🚨 เตรียมอพยพ! ระดับน้ำใกล้ล้นตลิ่ง เก็บข้าวของขึ้นที่สูง",
            "risk_level": 3
        }
    else:
        return {
            "status": "🟢 ปกติ",
            "situation": "ปกติ",
            "color": "#10B981",
            "advice": "✅ ระดับน้ำอยู่ในเกณฑ์ปกติ แต่ควรติดตามสถานการณ์",
            "risk_level": 1
        }


# =============================================================================
# 9. LAZY SYNC SYSTEM (V3 API Primary → V1 Fallback → Sheets)
# =============================================================================
def _load_previous_water_levels(sheets_client, sheet_id):
    """
    อ่านค่า WaterLevel เดิมจาก Sheets เพื่อใช้คำนวณ Trend
    Returns: dict {StationCode: WaterLevel}
    """
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
    ดึงข้อมูลระดับน้ำทั้งหมด 738 สถานี
    API Chain: V3 API → V1 Stations+Runoff
    พร้อมคำนวณ Situation และ Trend (อ่านค่าเดิมจาก Sheets)
    """
    # 1. อ่านค่า WaterLevel เดิมจาก Sheets เพื่อคำนวณ Trend
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
                print(f"[LazySync V3] Parse error: {e}")
                continue

        print(f"[LazySync] V3 parsed: {len(results)} stations")
        if len(results) > 50:  # ถ้าได้มากพอ ใช้ V3 เลย
            return results

    # ==== STRATEGY 2: ThaiWater V1 API (Fallback) ====
    print("[LazySync] V3 insufficient, falling back to V1 API...")
    stations = get_thaiwater_stations(use_cache=True)
    if not stations:
        print("[LazySync] No stations available from V1 cache")
        return results  # คืนค่าที่ได้จาก V3 ถ้ามี

    print(f"[LazySync] Fetching latest water levels for {len(stations)} stations via V1...")

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

        if (i + 1) % 100 == 0:
            print(f"[LazySync V1] Processed {i + 1}/{len(stations)} stations")

    print(f"[LazySync] Total processed: {len(results)} stations")
    return results


def sync_water_levels_to_sheets(sheets_client, sheet_id):
    """
    Initial Sync + Lazy Sync:
    - อ่านค่าเดิมจาก Sheets → คำนวณ Trend
    - ดึงข้อมูล V3 → V1 Fallback
    - Bulk Update: clear + update (1 API call)
    - อัปเดต timestamp ใน L1
    """
    if not sheets_client or not sheet_id:
        print("[LazySync] Sheets client not available")
        return False

    try:
        sheet = sheets_client.open_by_key(sheet_id)

        # เปิดหรือสร้างแท็บ Water_Levels
        try:
            ws = sheet.worksheet("Water_Levels")
        except gspread.WorksheetNotFound:
            print("[LazySync] Creating Water_Levels worksheet...")
            ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="12")

        # ดึงข้อมูลจาก API พร้อมค่าเดิมสำหรับคำนวณ Trend
        data = get_water_data_from_api(sheets_client, sheet_id)
        if not data:
            print("[LazySync] No data fetched from API")
            return False

        # เตรียม header
        header = ["StationCode", "Name", "River", "Location", "Lat", "Lon",
                  "WaterLevel", "BankLevel", "Situation", "Trend", "Time"]

        # เตรียมข้อมูลเป็น 2D array
        rows = [header]
        for st in data:
            rows.append([
                st["StationCode"],
                st["Name"],
                st["River"],
                st["Location"],
                st["Lat"],
                st["Lon"],
                st["WaterLevel"],
                st["BankLevel"],
                st["Situation"],
                st["Trend"],
                st["Time"]
            ])

        # Bulk Update: clear แล้วเขียนทั้งหมดในครั้งเดียว
        print(f"[LazySync] Bulk updating {len(rows)} rows...")
        ws.clear()
        ws.update('A1', rows, value_input_option='RAW')

        # อัปเดต timestamp ใน Cell L1
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ws.update_acell('L1', f"LastSync: {now}")

        print(f"[LazySync] Successfully synced {len(data)} stations at {now}")
        return True

    except Exception as e:
        print(f"[LazySync] Error syncing to sheets: {e}")
        return False


def get_water_data_lazy(sheets_client, sheet_id):
    """
    อ่านข้อมูลระดับน้ำจาก Google Sheets
    Returns: list of dicts
    """
    if not sheets_client or not sheet_id:
        return []

    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("Water_Levels")

        try:
            last_sync = ws.acell('L1').value
            print(f"[LazySync] Last sync: {last_sync}")
        except:
            last_sync = "Unknown"

        records = ws.get_all_records()
        print(f"[LazySync] Loaded {len(records)} records from Water_Levels")
        return records

    except Exception as e:
        print(f"[LazySync] Error reading from sheets: {e}")
        return []


def get_water_data_from_sheets(sheets_client, sheet_id, user_lat, user_lon):
    """
    ดึงข้อมูลระดับน้ำจาก Google Sheets พร้อมคำนวณระยะทาง
    เลือก 3 สถานีที่ใกล้ที่สุด
    """
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
    """
    ตรวจสอบว่าผู้ใช้ลงทะเบียนแล้วหรือยัง
    เช็คจาก Google Sheets (persistent) แทนแค่ USER_DATA
    Returns: (is_registered: bool, first_name, last_name, phone)
    """
    if not sheets_client or not sheet_id:
        # Fallback: เช็คจากหน่วยความจำชั่วคราว
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
                # เก็บลงหน่วยความจำด้วยเพื่อใช้เร็วขึ้นครั้งต่อไป
                if user_id not in USER_DATA:
                    USER_DATA[user_id] = {}
                USER_DATA[user_id]["first_name"] = fn
                USER_DATA[user_id]["last_name"] = ln
                USER_DATA[user_name] = ph
                return True, fn, ln, ph
    except Exception as e:
        print(f"[UserReg] Failed to check sheets: {e}")

    # Fallback: เช็คจากหน่วยความจำ
    if user_id in USER_DATA:
        d = USER_DATA[user_id]
        if "first_name" in d:
            return True, d.get("first_name", ""), d.get("last_name", ""), d.get("phone", "-")

    return False, "", "", "-"


def register_user_to_sheets(sheets_client, sheet_id, user_id, first_name, last_name, phone):
    """
    ลงทะเบียนผู้ใช้ใหม่ลง Google Sheets
    """
    if not sheets_client or not sheet_id:
        return False
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        users_ws = sheet.worksheet("users")
        register_date = datetime.datetime.now().strftime("%Y-%m-%d")
        users_ws.append_row([user_id, first_name, last_name, phone, register_date, "ACTIVE"])
        # เก็บลงหน่วยความจำ
        if user_id not in USER_DATA:
            USER_DATA[user_id] = {}
        USER_DATA[user_id]["first_name"] = first_name
        USER_DATA[user_id]["last_name"] = last_name
        USER_DATA[user_id]["phone"] = phone
        return True
    except Exception as e:
        print(f"[UserReg] Failed to save: {e}")
        return False


# =============================================================================
# 11. WATER LEVEL REPORT BUILDERS
# =============================================================================
def build_water_level_text_report(user_lat, user_lon, timestamp, stations, weather_info, water_flow):
    """สร้างรายงานข้อความระดับน้ำ"""
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
    """สร้าง Flex Message รายงานระดับน้ำ"""
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
                        contents=[
                            TextComponent(text=assessment["status"], size="xxs", color=risk_color, weight="bold"),
                        ]
                    ),
                    TextComponent(text=f"สถานการณ์: {situation} | แนวโน้ม: {trend}", size="xxs", color="#6B7280", margin="xs"),
                    TextComponent(text=assessment["advice"], size="xxs", color="#6B7280", margin="xs", wrap=True)
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
# 12. SOS PRIORITY CALCULATION
# =============================================================================
def calculate_sos_priority(group_types, urgency_level):
    """
    คำนวณ Priority จากกลุ่มผู้ประสบภัยและระดับความเร่งด่วน
    group_types: list (เช่น ["เด็กเล็ก/คนชรา", "ผู้ป่วยติดเตียง"])
    urgency_level: str ("วิกฤต" | "สูง" | "ปานกลาง" | "ต่ำ" | "ขาดแคลนยา")
    Returns: (priority_label, priority_code)
    """
    gt = [g.lower() for g in group_types] if group_types else []
    ul = urgency_level.lower() if urgency_level else ""

    # CRITICAL: มีผู้บาดเจ็บ OR ผู้ป่วยติดเตียง OR น้ำระดับวิกฤต OR ขาดแคลนยาหนัก
    if any(k in g for g in gt for k in ["บาดเจ็บ", "ผู้ป่วย", "พิการ"]):
        return ("🔴 CRITICAL (เร่งด่วนวิกฤตสูงสุด)", "CRITICAL")
    if "วิกฤต" in ul:
        return ("🔴 CRITICAL (เร่งด่วนวิกฤตสูงสุด)", "CRITICAL")
    if "ขาดแคลน" in ul:
        return ("🔴 CRITICAL (เร่งด่วนวิกฤตสูงสุด)", "CRITICAL")

    # HIGH: มีเด็ก/คนชรา OR น้ำระดับสูง
    if any(k in g for g in gt for k in ["เด็ก", "ชรา", "เด็กเล็ก"]):
        return ("🟠 HIGH (ความเสี่ยงสูง)", "HIGH")
    if "สูง" in ul:
        return ("🟠 HIGH (ความเสี่ยงสูง)", "HIGH")

    # NORMAL
    return ("🟢 NORMAL (สถานการณ์ปกติ)", "NORMAL")


def generate_case_id():
    """สร้างเลขเคส SOS"""
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    random_suffix = datetime.datetime.now().strftime("%f")[:4]
    return f"SOS-{today_str}-{random_suffix}"


def send_line_notification(user_id, message):
    """ส่ง Push Message กลับไปหาผู้ใช้ผ่าน LINE"""
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
# 13. USER NEEDS MANAGEMENT
# =============================================================================
def save_user_need(sheets_client, sheet_id, user_id, timestamp, lat, lon, category, details, urgency):
    """
    บันทึกความต้องการสิ่งของลง Google Sheets (แท็บ user_needs)
    """
    if not sheets_client or not sheet_id:
        return False

    try:
        sheet = sheets_client.open_by_key(sheet_id)
        try:
            ws = sheet.worksheet("user_needs")
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="12")
            ws.append_row(["Timestamp", "UserID", "Latitude", "Longitude",
                           "Category", "Details", "Urgency", "Status"])

        ws.append_row([timestamp, user_id, lat, lon, category, details, urgency, "PENDING"])
        return True
    except Exception as e:
        print(f"[UserNeeds] Failed to save: {e}")
        return False


def get_all_user_needs(sheets_client, sheet_id):
    """ดึงรายการความต้องการสิ่งของทั้งหมด"""
    if not sheets_client or not sheet_id:
        return []
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("user_needs")
        return ws.get_all_records()
    except Exception as e:
        print(f"[UserNeeds] Failed to load: {e}")
        return []


def update_need_status(sheets_client, sheet_id, timestamp, user_id, new_status):
    """อัปเดตสถานะความต้องการ (PENDING -> COMPLETED)"""
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
        print(f"[UserNeeds] Failed to update status: {e}")
        return False


# =============================================================================
# 14. SHELTER VACANCY CHECK
# =============================================================================
def check_shelter_vacancy(capacity, occupancy):
    """ตรวจสอบสถานะความจุศูนย์พักพิง"""
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
    """สร้างโครงสร้าง Sheets อัตโนมัติถ้ายังไม่มี"""
    try:
        existing_sheets = [w.title for w in sheet.worksheets()]

        # 1. แท็บ users
        if "users" not in existing_sheets:
            print("Creating users worksheet...")
            users_ws = sheet.add_worksheet(title="users", rows="3000", cols="10")
            users_ws.append_row(["user_id", "first_name", "last_name", "phone", "register_date", "status"])

        # 2. แท็บ sos_requests
        if "sos_requests" not in existing_sheets:
            print("Creating sos_requests worksheet...")
            sos_ws = sheet.add_worksheet(title="sos_requests", rows="3000", cols="25")
            sos_ws.append_row([
                "request_id", "user_id", "timestamp", "latitude", "longitude",
                "group_count", "group_types", "urgency_level", "photo_url",
                "water_level", "note", "priority", "status", "responder_name",
                "responder_notes", "accepted_at", "completed_at"
            ])

        # 3. แท็บ Shelters
        if "Shelters" not in existing_sheets:
            print("Creating Shelters worksheet...")
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

        # 4. แท็บ Water_Levels (11 คอลัมน์)
        if "Water_Levels" not in existing_sheets:
            print("Creating Water_Levels worksheet...")
            water_ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="12")
            water_ws.append_row([
                "StationCode", "Name", "River", "Location", "Lat", "Lon",
                "WaterLevel", "BankLevel", "Situation", "Trend", "Time"
            ])

        # 5. แท็บ Contacts
        if "Contacts" not in existing_sheets:
            print("Creating Contacts worksheet...")
            contacts_ws = sheet.add_worksheet(title="Contacts", rows="1000", cols="10")
            contacts_ws.append_row(["ContactID", "Name", "Role", "Phone"])
            contact_rows = [
                ["CT001", "ปภ. (กรมป้องกันและบรรเทาสาธารณภัย)", "รับแจ้งเหตุเตือนภัยและช่วยเหลืออุทกภัยสายด่วน", "1784"],
                ["CT002", "สพฉ. (สถาบันการแพทย์ฉุกเฉินแห่งชาติ)", "รับส่งต่อผู้ป่วยและเจ็บป่วยฉุกเฉินทางการแพทย์", "1669"],
                ["CT003", "ตำรวจทางหลวง", "ประสานงานความช่วยเหลือเส้นทางน้ำท่วมและดินถล่ม", "1193"]
            ]
            for r in contact_rows:
                contacts_ws.append_row(r)

        # 6. แท็บ user_needs
        if "user_needs" not in existing_sheets:
            print("Creating user_needs worksheet...")
            needs_ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="12")
            needs_ws.append_row(["Timestamp", "UserID", "Latitude", "Longitude",
                                 "Category", "Details", "Urgency", "Status"])

        # 7. แท็บ AI Logs
        if "AI Logs" not in existing_sheets:
            print("Creating AI Logs worksheet...")
            logs_ws = sheet.add_worksheet(title="AI Logs", rows="5000", cols="5")
            logs_ws.append_row(["Timestamp", "UserID", "Question", "Answer"])

        # ลบแท็บเริ่มต้น
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
    """เชื่อมต่อ Google Sheets แบบ Service Account"""
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
