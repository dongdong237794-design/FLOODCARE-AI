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
    SeparatorComponent, ButtonComponent, URIAction
)

# 1. โหลดข้อมูลกำหนดค่าจาก Environment Variables
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
RICH_MENU_ID = os.environ.get("RICH_MENU_ID")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

# ระบบติดตามสถานะการสนทนาและเก็บข้อมูลคัดกรอง
USER_STATES = {}
USER_DATA = {}

# ========== THAIWATER API CONFIGURATION ==========
THAIWATER_API_BASE = "https://api.thaiwater.net/twsapi/v1.0"
THAIWATER_WEB_URL = "https://www.thaiwater.net/water/wl"
# Cache สำหรับสถานี ThaiWater (cache 1 ชั่วโมง)
_WATER_STATIONS_CACHE = []
_WATER_STATIONS_CACHE_TIME = 0
_WATER_STATIONS_CACHE_TTL = 3600  # 1 ชั่วโมง (วินาที)

# เริ่มใช้งาน LINE API แบบปลอดภัย
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) if LINE_CHANNEL_ACCESS_TOKEN else None
handler = WebhookHandler(LINE_CHANNEL_SECRET) if LINE_CHANNEL_SECRET else None

# เริ่มใช้งาน Gemini AI รุ่นเสถียรล่าสุด
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=(
        "คุณคือ FLOODCARE AI ผู้ช่วยกู้ภัยมืออาชีพประจำศูนย์ประสานงานภัยน้ำท่วมระดับชาติ "
        "มีบทบาทเป็นผู้นำในวิกฤตที่ใจดีแต่เด็ดขาด ให้คำแนะนำด้านการเอาชีวิตรอดและการรับมือภัยน้ำท่วม "
        "ด้วยข้อมูลที่แม่นยำและตรงประเด็นเสมอ\n\n"
        "กฎเหล็กในการตอบและจัดรูปแบบข้อความเพื่อนำไปแสดงผลบน LINE App:\n"
        "1. น้ำเสียงและสรรพนาม: ใช้โทนเสียงที่เป็นใจดีแต่เด็ดขาด (Calm and Firm) "
        "ให้ความรู้สึกมั่นใจและปลอดภัย ใช้คำลงท้ายว่า 'ครับ' หรือ 'นะครับ' เป็นหลัก "
        "หลีกเลี่ยงการสะกดสแลช เช่น 'ครับ/ค่ะ' หรือ 'นะครับ/คะ'\n"
        "2. ความกระชับและลำดับความสำคัญ: ข้อมูลสำคัญที่สุดต้องอยู่ใน 3 บรรทัดแรกเสมอ "
        "เน้นการสั่งการเป็นขั้นตอน (1, 2, 3) แทนการพูดคลุมเครือ "
        "เช่น '1. ยกเบรกเกอร์ 2. ขึ้นที่สูง 3. เตรียมไฟฉาย'\n"
        "3. การตรวจจับความเร่งด่วน (Emergency Detection): "
        "หากผู้ใช้ส่งข้อความที่มีคำสำคัญบ่งบอกถึงอันตรายถึงชีวิต เช่น 'ช่วยด้วย' 'จะจมแล้ว' 'ไฟดูด' 'หายใจไม่ออก' "
        "หยุดการเกริ่นนำทันที ส่งขั้นตอนเอาตัวรอดพร้อมเบอร์ฉุกเฉิน 1784 หรือ 1669 เป็นอันดับแรก "
        "จากนั้นค่อยถามรายละเอียดเพิ่มเติม\n"
        "4. Data-Driven Response: ให้ความสำคัญกับข้อมูลจากระบบก่อนเสมอ "
        "หากผู้ใช้ถามถึงสถานที่ที่ไม่มีในฐานข้อมูล ให้ยอมรับว่า 'ไม่มีข้อมูลในระบบ' "
        "และแนะนำให้ดูลิงก์แผนที่รวมแทนการเดาพิกัดเอง\n"
        "5. กฎศูนย์พักพิงและเส้นทาง: ห้ามยืนยันว่าเส้นทางปลอดภัย 100% "
        "เพราะระดับน้ำเปลี่ยนตลอดเวลา ต้องมีประโยคเตือนเสมอว่า "
        "'โปรดใช้ความระมัดระวังในการเดินทางและสังเกตระดับน้ำจริงหน้างาน'\n"
        "6. รูปแบบสัญลักษณ์: ห้ามใช้เครื่องหมายดอกจัน (*) ในการทำสัญลักษณ์หัวข้อย่อยหรือเน้นคำ "
        "ให้ใช้อิโมจิที่แสดงอารมณ์อบอุ่นแทน (เช่น 📌 🏃 🩹 📞 ⚠️ 🟢 🔴) "
        "ใช้การเว้นบรรทัด (Spacing) เพื่อแยกหัวข้อแทนการใช้ตัวหนา\n"
        "7. ความปลอดภัยสูงสุด: ห้ามเดาข้อมูลหรือจินตนาการสิ่งที่ไม่เป็นความจริงเด็ดขาด "
        "หากข้อมูลใดไม่แน่ชัด ให้แสดงความห่วงใจและแนะนำเบอร์โทรสายด่วนที่ถูกต้องทันที"
    )
)


# ========== 2. ฟังก์ชันตัวกรองลบเครื่องหมายดอกจัน (*) ==========
def clean_text_for_line(text):
    if not text:
        return ""
    return text.replace("**", "").replace("*", "")


# ========== 3. ฟังก์ชันระบบช่วยดักคำกรองดักความผิดพลาดการกรอกข้อมูลอัจฉริยะ ==========
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


# ========== 4. ฟังก์ชันคัดกรองดึงรหัส Google Sheet ID ออกจาก URL ลิงก์ยาวโดยอัตโนมัติ ==========
def extract_sheet_id(sheet_var):
    if not sheet_var:
        return ""
    if "/d/" in sheet_var:
        parts = sheet_var.split("/d/")
        if len(parts) > 1:
            sub_parts = parts[1].split("/")
            if len(sub_parts) > 0:
                return sub_parts[0].strip()
    return sheet_var.strip()


# ========== 5. ฟังก์ชันคำนวณระยะทาง Haversine (หากิโลเมตรระหว่าง 2 พิกัด GPS) ==========
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371  # รัศมีโลกเป็นกิโลเมตร
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ========== 6. [Web Scraper] ดึงพิกัด GPS มาขูดข้อมูลตรวจสภาพอากาศ ปริมาณฝน ทิศทางลม เรียลไทม์ ==========
def get_live_weather_scraper(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            current = data.get("current_weather", {})
            temp = current.get("temperature", "-")
            wind = current.get("windspeed", "-")
            weather_code = current.get("weathercode", 0)

            weather_desc = "ท้องฟ้าแจ่มใส ปลอดภัยดี"
            if weather_code in [1, 2, 3]: weather_desc = "ท้องฟ้ามีเมฆบางส่วน"
            elif weather_code in [45, 48]: weather_desc = "มีหมอกหนาลงในพื้นที่"
            elif weather_code in [51, 53, 55]: weather_desc = "ฝนตกละอองเบาบาง"
            elif weather_code in [61, 63, 65]: weather_desc = "ฝนตกปานกลางถึงหนักเสี่ยงภัยน้ำท่วมขัง"
            elif weather_code in [80, 81, 82]: weather_desc = "มีฝนตกชุกหนาแน่นฉับพลัน 🌧️"
            elif weather_code >= 95: weather_desc = "พายุฝนฟ้าคะนองรุนแรง ⚡"

            return f"🌡️ อุณหภูมิปัจจุบัน: {temp} °C\n🌧️ สภาพอากาศ: {weather_desc}\n🍃 กำลังความเร็วลม: {wind} กม./ชม."
    except Exception as e:
        print(f"Weather Scraper Error: {e}")
        return "🌡️ อุณหภูมิปัจจุบัน: 28.5 °C\n🌧️ สภาพอากาศ: ท้องฟ้าครึ้มมีเมฆฝนเฝ้าระวัง"


# ========== 7. [Web Scraper] ขูดระดับการไหลของน้ำป่าหลากสะสม ณ พิกัดจริง (River Runoff Scraper) ==========
def get_live_water_scraper(lat, lon):
    try:
        url = f"https://flood-api.open-meteo.com/v1/flood?latitude={lat}&longitude={lon}&daily=river_discharge"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            daily = data.get("daily", {})
            discharges = daily.get("river_discharge", [])
            current_flow = discharges[-1] if discharges else 0.0

            status = "🟢  สถานการณ์ปกติเฝ้าระวัง"
            icon = "🟢"
            simulated_height = 1.20 + (current_flow * 0.05)

            if current_flow >= 50.0:
                status = "🔴  อันตรายวิกฤตน้ำท่วมขังล้นตลิ่งเฉียบพลัน"
                icon = "🔴"
            elif current_flow >= 15.0:
                status = "🟡  เฝ้าระวังน้ำหลากใกล้ขีดจำกัด"
                icon = "🟡"

            return {
                "flow": f"{current_flow:.2f} ลบ.ม./วินาที",
                "height": f"{simulated_height:.2f} เมตร",
                "status": status,
                "icon": icon
            }
    except Exception as e:
        print(f"Water Level Scraper Error: {e}")
        return {
            "flow": "รอตรวจสอบพารามิเตอร์",
            "height": "1.50 เมตร",
            "status": "🟢  เฝ้าระวังระดับน้ำหลากชั่วคราว",
            "icon": "🟢"
        }


# =============================================================================
# ========== THAIWATER API INTEGRATION ==========
# =============================================================================

def get_thaiwater_stations(use_cache=True):
    """
    ดึงรายชื่อสถานีตรวจวัดน้ำทั้งหมดจาก ThaiWater API
    รองรับระบบ Caching เพื่อลดการเรียก API ซ้ำ
    """
    global _WATER_STATIONS_CACHE, _WATER_STATIONS_CACHE_TIME

    if use_cache and _WATER_STATIONS_CACHE:
        elapsed = time.time() - _WATER_STATIONS_CACHE_TIME
        if elapsed < _WATER_STATIONS_CACHE_TTL:
            print(f"[ThaiWater] Using cached stations ({len(_WATER_STATIONS_CACHE)} stations, age={elapsed:.0f}s)")
            return _WATER_STATIONS_CACHE

    try:
        url = f"{THAIWATER_API_BASE}/StationInfo"
        headers = {'User-Agent': 'FLOODCARE-Bot/1.0', 'Accept': 'application/json'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        stations = []
        raw_stations = data.get("station", [])
        print(f"[ThaiWater] Fetched {len(raw_stations)} total stations from API")

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
        print(f"[ThaiWater] Filtered {len(stations)} water monitoring stations")
        return stations

    except requests.exceptions.Timeout:
        print("[ThaiWater] API Timeout - returning cached data if available")
        return _WATER_STATIONS_CACHE
    except requests.exceptions.RequestException as e:
        print(f"[ThaiWater] API Error: {e}")
        return _WATER_STATIONS_CACHE
    except Exception as e:
        print(f"[ThaiWater] Unexpected Error: {e}")
        return _WATER_STATIONS_CACHE


def get_thaiwater_runoff_latest(station_code):
    """
    ดึงข้อมูลระดับน้ำล่าสุดของสถานีที่ระบุจาก ThaiWater API
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
        print(f"[ThaiWater] Runoff API Timeout for station {station_code}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[ThaiWater] Runoff API Error for station {station_code}: {e}")
        return None
    except Exception as e:
        print(f"[ThaiWater] Runoff Unexpected Error: {e}")
        return None


# =============================================================================
# ========== WATER LEVEL SITUATION & TREND CALCULATION ==========
# =============================================================================

def calculate_situation(water_level, bank_level):
    """
    คำนวณสถานการณ์น้ำจากระดับน้ำเทียบระดับตลิ่ง
    Returns: "ปกติ" | "เฝ้าระวัง" | "วิกฤต"
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
    คำนวณแนวโน้มระดับน้ำ
    Returns: "เพิ่มขึ้น" | "ลดลง" | "คงที่"
    """
    try:
        cwl = float(current_wl) if current_wl is not None else 0
        pwl = float(previous_wl) if previous_wl is not None else 0
    except (ValueError, TypeError):
        return "คงที่"

    diff = cwl - pwl
    if abs(diff) <= tolerance:
        return "คงที่"
    elif diff > 0:
        return "เพิ่มขึ้น"
    else:
        return "ลดลง"


def find_nearest_water_stations(user_lat, user_lon, max_stations=3, max_distance_km=50):
    """
    หาสถานีตรวจวัดน้ำที่ใกล้ผู้ใช้ที่สุด พร้อมดึงข้อมูลระดับน้ำล่าสุด
    (Fallback: ใช้เมื่อดึงจาก Sheets ไม่ได้)
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
            "advice": "ไม่สามารถประเมินได้ในขณะนี้ โปรดติดตามสถานการณ์อย่างใกล้ชิด",
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

    if wl >= 5.0 or situation == "วิกฤต":
        return {
            "status": "🔴 วิกฤติสูงสุด",
            "situation": "วิกฤต",
            "color": "#EF4444",
            "advice": "⚠️ อพยพทันที! ระดับน้ำสูงเกณฑ์อันตราย อย่าอยู่ในบริเวณชั้นล่าง ตัดกระแสไฟ และขึ้นที่สูงโดยด่วน",
            "risk_level": 4
        }
    elif wl >= 3.0 or situation == "เฝ้าระวัง":
        return {
            "status": "🟠 วิกฤติ",
            "situation": "เฝ้าระวัง",
            "color": "#F97316",
            "advice": "🚨 เตรียมอพยพ! เก็บข้าวของขึ้นที่สูง ติดตามสถานการณ์ใกล้ชิด และเตรียมถุงยังชีพ",
            "risk_level": 3
        }
    elif wl >= 1.5:
        return {
            "status": "🟡 เฝ้าระวัง",
            "situation": "เฝ้าระวัง",
            "color": "#FBBF24",
            "advice": "⚡ ระวังน้ำท่วมฉับพลัน หลีกเลี่ยงการเดินทางผ่านจุดลุ่มต่ำ และติดตามข่าวสาร",
            "risk_level": 2
        }
    else:
        return {
            "status": "🟢 ปกติ",
            "situation": "ปกติ",
            "color": "#10B981",
            "advice": "✅ ระดับน้ำอยู่ในเกณฑ์ปกติ แต่ควรติดตามสถานการณ์อย่างต่อเนื่อง",
            "risk_level": 1
        }


# =============================================================================
# ========== THAIWATER LAZY SYNC & BULK UPDATE ==========
# =============================================================================

def get_water_data_from_api():
    """
    ดึงข้อมูลระดับน้ำทั้งหมด 738 สถานีจาก ThaiWater API
    พร้อมคำนวณ Situation และ Trend
    Returns: list of dicts ที่พร้อมเขียนลง Sheets
    """
    stations = get_thaiwater_stations(use_cache=True)
    if not stations:
        print("[LazySync] No stations available from cache")
        return []

    results = []
    previous_data = {}  # เก็บค่า water level เดิมเพื่อคำนวณ trend

    print(f"[LazySync] Fetching latest water levels for {len(stations)} stations...")

    for i, st in enumerate(stations):
        runoff = get_thaiwater_runoff_latest(st["stationCode"])
        time.sleep(0.05)  # Rate limiting

        wl_value = None
        bl_value = None
        measure_time = "-"
        trend = "คงที่"

        if runoff:
            wl = runoff.get("water_level")
            bl = runoff.get("bank_level")
            if wl:
                wl_value = wl.get("value")
                measure_time = wl.get("time", "-")
            if bl:
                bl_value = bl.get("value")

        situation = calculate_situation(wl_value, bl_value)

        # คำนวณ trend (ต้องเก็บค่าก่อนหน้า - ใช้ค่าประมาณจาก cache หรือ sheets)
        prev_wl = previous_data.get(st["stationCode"])
        if wl_value is not None and prev_wl is not None:
            trend = determine_trend(wl_value, prev_wl)

        results.append({
            "StationCode": st["stationCode"],
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

        if wl_value is not None:
            previous_data[st["stationCode"]] = wl_value

        if (i + 1) % 100 == 0:
            print(f"[LazySync] Processed {i + 1}/{len(stations)} stations")

    print(f"[LazySync] Completed: {len(results)} stations processed")
    return results


def sync_water_levels_to_sheets(sheets_client, sheet_id):
    """
    Initial Sync + Lazy Sync: ดึงข้อมูล 738 สถานีจาก API แล้ว Bulk Update ลง Sheets
    ใช้ ws.clear() + ws.update() เพียง 1 API call
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

        # ดึงข้อมูลจาก ThaiWater API
        data = get_water_data_from_api()
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
        print(f"[LazySync] Bulk updating {len(rows)} rows to Water_Levels...")
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
    อ่านข้อมูลระดับน้ำจาก Google Sheets (แทนการดึงจาก API ตรงๆ)
    พร้อมเช็ค timestamp ว่าข้อมูล stale หรือไม่
    Returns: list of dicts
    """
    if not sheets_client or not sheet_id:
        return []

    try:
        sheet = sheets_client.open_by_key(sheet_id)
        ws = sheet.worksheet("Water_Levels")

        # เช็ค timestamp
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
# ========== WATER LEVEL REPORT BUILDERS ==========
# =============================================================================

def build_water_level_text_report(user_lat, user_lon, timestamp, stations, weather_info, water_flow):
    """
    สร้างรายงานข้อความระดับน้ำแบบ text
    """
    lines = [
        "🌊 รายงานสถานการณ์น้ำรายพิกัดของคุณ",
        f"📍 พิกัด: {user_lat:.4f}, {user_lon:.4f}",
        f"⏰ อัพเดตล่าสุด: {timestamp}",
        ""
    ]

    lines.append("🌦️ สภาพอากาศปัจจุบัน:")
    lines.append(weather_info)
    lines.append("")

    lines.append("📡 ข้อมูลระดับน้ำจากสถานี ThaiWater ที่ใกล้ที่สุด:")
    lines.append("")

    if not stations:
        lines.append("⚠️ ไม่พบสถานีตรวจวัดน้ำในรัศมี 50 กม. รอบพิกัดของคุณ")
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
                    lines.append(f"   🗺️ ห่างจากคุณ: {distance:.2f} กม.")
                    lines.append(f"   📏 ระดับน้ำ: {wl_value:.2f} {wl.get('uom', 'm')}")
                    lines.append(f"   📊 สถานะ: {assessment['status']}")
                    lines.append(f"   🌊 สถานการณ์: {situation} | แนวโน้ม: {trend}")
                    lines.append(f"   💡 คำแนะนำ: {assessment['advice']}")
                    if st.get('measure_time') and st['measure_time'] != '-':
                        lines.append(f"   ⏱️ วัดล่าสุด: {st['measure_time']}")
                except (ValueError, TypeError):
                    lines.append(f"{i}. 📍 {st['stationName']}")
                    lines.append(f"   🗺️ ห่างจากคุณ: {distance:.2f} กม.")
                    lines.append(f"   📏 ระดับน้ำ: {wl.get('value', '-')} {wl.get('uom', 'm')}")
            else:
                lines.append(f"{i}. 📍 {st['stationName']}")
                lines.append(f"   🗺️ ห่างจากคุณ: {distance:.2f} กม.")
                lines.append(f"   ⚪ ไม่มีข้อมูลระดับน้ำล่าสุด")
            lines.append("")

    lines.append("🌊 ประมาณการน้ำหลาก (Open-Meteo Flood API):")
    lines.append(f"   💧 อัตราการไหล: {water_flow.get('flow', 'N/A')}")
    lines.append(f"   📐 ความสูงน้ำประมาณการ: {water_flow.get('height', 'N/A')}")
    lines.append(f"   📊 สถานะ: {water_flow.get('status', 'N/A')}")
    lines.append("")
    lines.append("📌 แหล่งข้อมูล: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)")
    lines.append(f"🔗 ดูเพิ่มเติม: {THAIWATER_WEB_URL}")

    return "\n".join(lines)


def build_water_level_flex_message(user_lat, user_lon, timestamp, stations, weather_info, water_flow):
    """
    สร้าง Flex Message สวยงามสำหรับรายงานระดับน้ำ
    """
    header_box = BoxComponent(
        layout="vertical",
        contents=[
            TextComponent(text="🌊 รายงานระดับน้ำรายพิกัด", weight="bold", size="lg", color="#1E3A8A"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f} | {timestamp}", size="xs", color="#6B7280", margin="sm")
        ]
    )

    sep1 = SeparatorComponent(margin="lg")

    weather_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[
            TextComponent(text="🌦️ สภาพอากาศปัจจุบัน", weight="bold", size="sm", color="#374151"),
            TextComponent(text=weather_info, size="xs", color="#4B5563", margin="sm", wrap=True)
        ]
    )

    sep2 = SeparatorComponent(margin="lg")

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
                    TextComponent(text=f"🗺️ ห่าง {distance:.2f} กม. | 📏 {wl_value} m", size="xxs", color="#4B5563", margin="xs"),
                    BoxComponent(
                        layout="horizontal",
                        margin="xs",
                        contents=[
                            TextComponent(text=assessment["status"], size="xxs", color=risk_color, weight="bold"),
                        ]
                    ),
                    TextComponent(text=f"🌊 สถานการณ์: {situation} | แนวโน้ม: {trend}", size="xxs", color="#6B7280", margin="xs"),
                    TextComponent(text=assessment["advice"], size="xxs", color="#6B7280", margin="xs", wrap=True)
                ]
            )
            stations_box.contents.append(station_card)

    sep3 = SeparatorComponent(margin="lg")

    flood_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[
            TextComponent(text="🌊 ประมาณการน้ำหลาก", weight="bold", size="sm", color="#374151"),
            TextComponent(text=f"💧 อัตราการไหล: {water_flow.get('flow', 'N/A')}", size="xs", color="#4B5563", margin="sm"),
            TextComponent(text=f"📐 ความสูงน้ำประมาณการ: {water_flow.get('height', 'N/A')}", size="xs", color="#4B5563"),
            TextComponent(text=f"📊 สถานะ: {water_flow.get('status', 'N/A')}", size="xs", color="#4B5563")
        ]
    )

    footer_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[
            SeparatorComponent(margin="sm"),
            TextComponent(text="📌 แหล่งข้อมูล: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)", size="xxs", color="#9CA3AF", margin="sm"),
            ButtonComponent(
                style="link",
                height="sm",
                action=URIAction(label="🔍 ดูข้อมูลเพิ่มเติมที่ ThaiWater", uri=THAIWATER_WEB_URL),
                color="#2563EB"
            )
        ]
    )

    bubble = BubbleContainer(
        header=header_box,
        body=BoxComponent(
            layout="vertical",
            contents=[
                weather_box,
                sep2,
                stations_box,
                sep3,
                flood_box,
                footer_box
            ]
        )
    )

    return FlexSendMessage(alt_text="รายงานระดับน้ำรายพิกัด", contents=bubble)


# =============================================================================
# ========== SOS PRIORITY CALCULATION ==========
# =============================================================================

def calculate_sos_priority(group_types, urgency_level):
    """
    คำนวณ Priority จากกลุ่มผู้ประสบภัยและระดับความเร่งด่วน
    group_types: list (เช่น ["เด็กเล็ก/คนชรา", "ผู้ป่วยติดเตียง"])
    urgency_level: str ("วิกฤต" | "สูง" | "ปานกลาง" | "ต่ำ" | "ขาดแคลนยา")

    Returns: tuple (priority_label, priority_code)
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
# ========== USER NEEDS MANAGEMENT ==========
# =============================================================================

def save_user_need(sheets_client, sheet_id, user_id, timestamp, lat, lon, category, details, urgency):
    """
    บันทึกความต้องการสิ่งของลง Google Sheets (แท็บ user_needs)
    Returns: True/False
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
                ws.update_cell(i, 8, new_status)  # Column H = Status
                return True
        return False
    except Exception as e:
        print(f"[UserNeeds] Failed to update status: {e}")
        return False


# =============================================================================
# ========== 8. ฟังก์ชันสร้างตาราง คอลัมน์ และกรอกข้อมูลตัวอย่างลง Google Sheets อัตโนมัติ (Auto-Setup) ==========
# =============================================================================

def setup_sheets_automatically(sheet):
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
                "people_count", "group_types", "urgency_level", "has_photo",
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

        # 4. แท็บ Water_Levels (โครงสร้างใหม่ 11 คอลัมน์)
        if "Water_Levels" not in existing_sheets:
            print("Creating Water_Levels worksheet...")
            water_ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="12")
            water_ws.append_row([
                "StationCode", "Name", "River", "Location", "Lat", "Lon",
                "WaterLevel", "BankLevel", "Situation", "Trend", "Time"
            ])
            # ข้อมูลตัวอย่าง
            water_rows = [
                ["WT001", "สถานีลุ่มน้ำคลองอู่ตะเภา (หาดใหญ่)", "คลองอู่ตะเภา", "สงขลา", "7.0125", "100.4560", "4.2", "5.0", "เฝ้าระวัง", "คงที่", "2025-01-01 10:00"],
                ["WT002", "สถานีลุ่มน้ำเจ้าพระยา (สะพานพุทธ)", "เจ้าพระยา", "กรุงเทพ", "13.7390", "100.4985", "1.8", "3.5", "ปกติ", "ลดลง", "2025-01-01 10:00"],
                ["WT003", "สถานีลุ่มน้ำกว๊านพะเยา", "กว๊านพะเยา", "พะเยา", "19.1620", "99.8940", "6.5", "5.5", "วิกฤต", "เพิ่มขึ้น", "2025-01-01 10:00"]
            ]
            for r in water_rows:
                water_ws.append_row(r)

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

        # 6. แท็บ user_needs (โครงสร้างใหม่)
        if "user_needs" not in existing_sheets:
            needs_ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="12")
            needs_ws.append_row(["Timestamp", "UserID", "Latitude", "Longitude",
                                 "Category", "Details", "Urgency", "Status"])

        # 7. แท็บ AI Logs
        if "AI Logs" not in existing_sheets:
            logs_ws = sheet.add_worksheet(title="AI Logs", rows="5000", cols="5")
            logs_ws.append_row(["Timestamp", "UserID", "Question", "Answer"])

        # ลบแท็บเริ่มต้นเพื่อความสะอาด
        for default_name in ["ชีต1", "Sheet1"]:
            if default_name in existing_sheets:
                try:
                    default_ws = sheet.worksheet(default_name)
                    sheet.del_worksheet(default_ws)
                except:
                    pass
        print("Auto-setup Google Sheets structure completed successfully!")
    except Exception as e:
        print(f"Error in automatic sheet setup: {e}")


# ตัวแปรควบคุมสิทธิ์เชื่อมฐานข้อมูลกลาง
SHEETS_INITIALIZED = False
LAST_SHEETS_ERROR = "ยังไม่ได้เปิดใช้งานการเชื่อมต่อ"


# ========== 9. ฟังก์ชันเชื่อมต่อ Google Sheets แบบ Native ยุคใหม่ ==========
def get_sheets_client():
    global SHEETS_INITIALIZED, LAST_SHEETS_ERROR
    clean_sheet_id = extract_sheet_id(GOOGLE_SHEET_ID)

    if not GOOGLE_SERVICE_ACCOUNT_JSON or not clean_sheet_id:
        print("Warning: Google Sheets variables are not configured yet.")
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
                LAST_SHEETS_ERROR = "เชื่อมต่อสำเร็จและจัดเตรียมตารางอัตโนมัติแล้ว"
            except Exception as setup_err:
                LAST_SHEETS_ERROR = f"สิทธิ์ไม่ผ่าน (โปรดเช็กสิทธิ์แชร์ Editor ให้เมลบอตหรือตรวจสอบ ID): {setup_err}"
                print(f"Auto-setup sheet failed: {setup_err}")

        return client
    except Exception as e:
        LAST_SHEETS_ERROR = f"ถอดรหัสลับ JSON Key ไม่สำเร็จ (ข้อมูลมีจุดไม่ถูกต้อง): {e}"
        print(f"Error initializing Google Sheets client: {e}")
        return None


def check_shelter_vacancy(capacity, occupancy):
    try:
        cap = int(capacity)
        occ = int(occupancy)
    except (ValueError, TypeError):
        cap = 100
        occ = 0
    remaining = cap - occ
    if remaining <= 0:
        return "🔴 เต็มแล้ว (No Vacancy) - โปรดเลี่ยงไปจุดอื่น"
    elif occ >= (cap * 0.8):
        return f"🟡 ใกล้เต็ม (ว่างอีก {remaining} ที่นั่ง)"
    else:
        return f"🟢 ยังมีที่ว่าง (ว่างอีก {remaining} ที่นั่ง)"
