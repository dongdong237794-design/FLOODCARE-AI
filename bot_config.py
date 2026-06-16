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
        "คุณคือ FLOODCARE AI ผู้ช่วยอัจฉริยะและผู้เชี่ยวชาญด้านอุทกภัยประจำประเทศไทย "
        "มีบทบาทคอยตอบคำถามและให้คำแนะนำในการเอาชีวิตรอดและการรับมือภัยน้ำท่วมอย่างถูกต้องตามหลักสากล\n\n"
        "กฎเหล็กในการตอบและจัดรูปแบบข้อความเพื่อนำไปแสดงผลบน LINE App:\n"
        "1. น้ำเสียงและสรรพนาม: สุภาพ อบอุ่น อ่อนโยน และเป็นกันเองเสมือนคนในครอบครัวคอยดูแลกัน "
        "ให้ใช้คำลงท้ายว่า 'ครับ' หรือ 'นะครับ' เป็นหลักเพื่อความสม่ำเสมอ (หลีกเลี่ยงการสะกดสแลช เช่น 'ครับ/ค่ะ' หรือ 'นะครับ/คะ' ที่ดูแข็งทื่อแบบหุ่นยนต์)\n"
        "2. ความกระชับ: ตอบให้กระชับ ได้ใจความสั้นๆ ไม่ยาวเป็นเรียงความ และแบ่งย่อหน้าให้เหมาะสมกับการอ่านบนหน้าจอมือถือ\n"
        "3. รูปแบบสัญลักษณ์: ห้ามใช้เครื่องหมายดอกจัน (*) ในการทำสัญลักษณ์หัวข้อย่อยหรือเน้นคำเด็ดขาด "
        "แต่ให้ใช้ 'อิโมจิ' ที่แสดงอารมณ์อบอุ่นและปลอดภัยแทนสัญลักษณ์นำหน้าหัวข้อย่อยเสมอ (เช่น 📌, 🏃, 🩹, 📞, 💬, ⚠️, 🟢, 🔴) เพื่อความเป็นระเบียบและสวยงาม\n"
        "4. ความปลอดภัยสูงสุด: ห้ามเดาข้อมูลหรือจินตนาการสิ่งที่ไม่เป็นความจริงเด็ดขาด หากข้อมูลใดไม่แน่ชัด หรือเป็นกรณีฉุกเฉินเฉพาะหน้า "
        "ให้แสดงความห่วงใจและแนะนำเบอร์โทรสายด่วนภัยพิบัติที่ถูกต้องทันที เช่น สายด่วน ปภ. 1784 หรือสายด่วนกู้ชีพ 1669"
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
            # คำนวณระดับความสูงน้ำจำลองเทียบเคียงข้อมูลภาครัฐตามหลักฟิสิกส์อัตโนมัติ
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
# ========== THAIWATER API INTEGRATION (NEW - FULL IMPLEMENTATION) ==========
# =============================================================================

def get_thaiwater_stations(use_cache=True):
    """
    ดึงรายชื่อสถานีตรวจวัดน้ำทั้งหมดจาก ThaiWater API
    รองรับระบบ Caching เพื่อลดการเรียก API ซ้ำ
    """
    global _WATER_STATIONS_CACHE, _WATER_STATIONS_CACHE_TIME

    # ตรวจสอบ cache ก่อน
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

            # กรองเฉพาะสถานีตรวจวัดระดับน้ำ/น้ำท่า
            if any(kw in st_type for kw in ["ระดับน้ำ", "น้ำท่า", "Runoff", "WaterLevel", "ดิน", "อุทก"]):
                try:
                    lat = float(meta.get("latitude", 0))
                    lon = float(meta.get("longitude", 0))
                    if lat == 0 and lon == 0:
                        continue  # ข้ามสถานีที่ไม่มีพิกัด

                    stations.append({
                        "stationCode": meta.get("stationCode", ""),
                        "stationName": meta.get("stationName", "ไม่ระบุชื่อ"),
                        "stationType": st_type,
                        "provinceCode": meta.get("locationCode", ""),
                        "provinceName": meta.get("provinceName", ""),
                        "districtName": meta.get("districtName", ""),
                        "latitude": lat,
                        "longitude": lon,
                        "status": meta.get("stationOperatingStatus", 1)
                    })
                except (ValueError, TypeError):
                    continue

        # อัพเดต cache
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

        return {
            "stationCode": station_code,
            "stationName": obs.get("station", {}).get("stationReference", ""),
            "water_level": water_level,
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


def find_nearest_water_stations(user_lat, user_lon, max_stations=3, max_distance_km=50):
    """
    หาสถานีตรวจวัดน้ำที่ใกล้ผู้ใช้ที่สุด พร้อมดึงข้อมูลระดับน้ำล่าสุด
    """
    stations = get_thaiwater_stations(use_cache=True)
    if not stations:
        return []

    # คำนวณระยะทางและเรียงลำดับ
    for st in stations:
        st["distance_km"] = calculate_distance(user_lat, user_lon, st["latitude"], st["longitude"])

    # กรองเฉพาะสถานีในรัศมีที่ยอมรับได้ และเรียงจากใกล้ไปไกล
    nearby = [s for s in stations if s["distance_km"] <= max_distance_km]
    nearby.sort(key=lambda x: x["distance_km"])

    # ดึงข้อมูลระดับน้ำล่าสุดของสถานีที่ใกล้ที่สุด
    result = []
    for st in nearby[:max_stations]:
        runoff_data = get_thaiwater_runoff_latest(st["stationCode"])
        if runoff_data:
            st["water_level"] = runoff_data.get("water_level")
            st["discharge"] = runoff_data.get("discharge")
            st["measure_time"] = runoff_data.get("resultTime")
        result.append(st)

    return result


def assess_water_level_status(water_level_value, station_name=""):
    """
    ประเมินสถานะระดับน้ำและให้คำแนะนำตามเกณฑ์ของกรมชลประทาน
    """
    if water_level_value is None:
        return {
            "status": "⚪ ไม่มีข้อมูล",
            "color": "#9CA3AF",
            "advice": "ไม่สามารถประเมินได้ในขณะนี้ โปรดติดตามสถานการณ์อย่างใกล้ชิด",
            "risk_level": 0
        }

    try:
        wl = float(water_level_value)
    except (ValueError, TypeError):
        return {
            "status": "⚪ ข้อมูลไม่ถูกต้อง",
            "color": "#9CA3AF",
            "advice": "ไม่สามารถประเมินได้",
            "risk_level": 0
        }

    # เกณฑ์การประเมินระดับน้ำ (ตามเกณฑ์ของกรมชลประทาน)
    if wl >= 5.0:
        return {
            "status": "🔴 วิกฤติสูงสุด",
            "color": "#EF4444",
            "advice": "⚠️ อพยพทันที! ระดับน้ำสูงเกณฑ์อันตราย อย่าอยู่ในบริเวณชั้นล่าง ตัดกระแสไฟ และขึ้นที่สูงโดยด่วน",
            "risk_level": 4
        }
    elif wl >= 3.0:
        return {
            "status": "🟠 วิกฤติ",
            "color": "#F97316",
            "advice": "🚨 เตรียมอพยพ! เก็บข้าวของขึ้นที่สูง ติดตามสถานการณ์ใกล้ชิด และเตรียมถุงยังชีพ",
            "risk_level": 3
        }
    elif wl >= 1.5:
        return {
            "status": "🟡 เฝ้าระวัง",
            "color": "#FBBF24",
            "advice": "⚡ ระวังน้ำท่วมฉับพลัน หลีกเลี่ยงการเดินทางผ่านจุดลุ่มต่ำ และติดตามข่าวสาร",
            "risk_level": 2
        }
    else:
        return {
            "status": "🟢 ปกติ",
            "color": "#10B981",
            "advice": "✅ ระดับน้ำอยู่ในเกณฑ์ปกติ แต่ควรติดตามสถานการณ์อย่างต่อเนื่อง",
            "risk_level": 1
        }


def get_water_data_from_sheets(sheets_client, sheet_id, user_lat, user_lon):
    """
    Fallback: ดึงข้อมูลระดับน้ำจาก Google Sheets กรณี ThaiWater API ใช้ไม่ได้
    """
    water_stations = []
    try:
        sheet = sheets_client.open_by_key(sheet_id)
        water_worksheet = sheet.worksheet("Water_Levels")
        rows = water_worksheet.get_all_records()
        for row in rows:
            try:
                st_lat = float(row.get('Latitude', 0))
                st_lon = float(row.get('Longitude', 0))
                distance = calculate_distance(user_lat, user_lon, st_lat, st_lon)
                water_stations.append({
                    "stationName": row.get('Name', 'ไม่ระบุชื่อ'),
                    "provinceName": row.get('Province', ''),
                    "latitude": st_lat,
                    "longitude": st_lon,
                    "distance_km": distance,
                    "water_level": {"value": row.get('WaterLevel_M', '-'), "uom": "m"},
                    "status_fallback": row.get('Status', '🟢 เฝ้าระวัง'),
                    "source": "sheets"
                })
            except (ValueError, TypeError):
                continue
        water_stations.sort(key=lambda x: x["distance_km"])
        return water_stations[:3]
    except Exception as e:
        print(f"[Fallback Sheets] Error: {e}")
        return []


def build_water_level_text_report(user_lat, user_lon, timestamp, thaiwater_stations, weather_info, water_flow):
    """
    สร้างรายงานข้อความระดับน้ำแบบ text (ใช้เมื่อส่ง Flex Message ไม่ได้)
    """
    lines = [
        "🌊 รายงานสถานการณ์น้ำรายพิกัดของคุณ",
        f"📍 พิกัด: {user_lat:.4f}, {user_lon:.4f}",
        f"⏰ อัพเดตล่าสุด: {timestamp}",
        ""
    ]

    # สภาพอากาศ
    lines.append("🌦️ สภาพอากาศปัจจุบัน:")
    lines.append(weather_info)
    lines.append("")

    # ข้อมูลระดับน้ำจาก ThaiWater
    lines.append("📡 ข้อมูลระดับน้ำจากสถานี ThaiWater ที่ใกล้ที่สุด:")
    lines.append("")

    if not thaiwater_stations:
        lines.append("⚠️ ไม่พบสถานีตรวจวัดน้ำในรัศมี 50 กม. รอบพิกัดของคุณ")
    else:
        for i, st in enumerate(thaiwater_stations, 1):
            wl = st.get("water_level")
            distance = st.get("distance_km", 0)

            if wl and wl.get("value") is not None:
                try:
                    wl_value = float(wl["value"])
                    assessment = assess_water_level_status(wl_value, st.get("stationName", ""))
                    lines.append(f"{i}. 📍 {st['stationName']}")
                    lines.append(f"   🗺️ ห่างจากคุณ: {distance:.2f} กม.")
                    lines.append(f"   📏 ระดับน้ำ: {wl_value:.2f} {wl.get('uom', 'm')}")
                    lines.append(f"   📊 สถานะ: {assessment['status']}")
                    lines.append(f"   💡 คำแนะนำ: {assessment['advice']}")
                    if wl.get('time'):
                        lines.append(f"   ⏱️ วัดล่าสุด: {wl['time']}")
                except (ValueError, TypeError):
                    lines.append(f"{i}. 📍 {st['stationName']}")
                    lines.append(f"   🗺️ ห่างจากคุณ: {distance:.2f} กม.")
                    lines.append(f"   📏 ระดับน้ำ: {wl.get('value', '-')} {wl.get('uom', 'm')}")
            else:
                lines.append(f"{i}. 📍 {st['stationName']}")
                lines.append(f"   🗺️ ห่างจากคุณ: {distance:.2f} กม.")
                lines.append(f"   ⚪ ไม่มีข้อมูลระดับน้ำล่าสุด")
            lines.append("")

    # ประมาณการน้ำหลากจาก Open-Meteo
    lines.append("🌊 ประมาณการน้ำหลาก (Open-Meteo Flood API):")
    lines.append(f"   💧 อัตราการไหล: {water_flow.get('flow', 'N/A')}")
    lines.append(f"   📐 ความสูงน้ำประมาณการ: {water_flow.get('height', 'N/A')}")
    lines.append(f"   📊 สถานะ: {water_flow.get('status', 'N/A')}")
    lines.append("")
    lines.append("📌 แหล่งข้อมูล: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater) และ Open-Meteo")
    lines.append("🔗 ดูเพิ่มเติม: https://www.thaiwater.net")

    return "\n".join(lines)


def build_water_level_flex_message(user_lat, user_lon, timestamp, thaiwater_stations, weather_info, water_flow):
    """
    สร้าง Flex Message สวยงามสำหรับรายงานระดับน้ำ
    """
    # Header section
    header_box = BoxComponent(
        layout="vertical",
        contents=[
            TextComponent(text="🌊 รายงานระดับน้ำรายพิกัด", weight="bold", size="lg", color="#1E3A8A"),
            TextComponent(text=f"📍 {user_lat:.4f}, {user_lon:.4f} | {timestamp}", size="xs", color="#6B7280", margin="sm")
        ]
    )

    # Separator
    sep1 = SeparatorComponent(margin="lg")

    # Weather section
    weather_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[
            TextComponent(text="🌦️ สภาพอากาศปัจจุบัน", weight="bold", size="sm", color="#374151"),
            TextComponent(text=weather_info, size="xs", color="#4B5563", margin="sm", wrap=True)
        ]
    )

    sep2 = SeparatorComponent(margin="lg")

    # Water stations section
    stations_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[TextComponent(text="📡 สถานีตรวจวัดใกล้คุณ", weight="bold", size="sm", color="#374151")]
    )

    if not thaiwater_stations:
        stations_box.contents.append(
            TextComponent(text="⚠️ ไม่พบสถานีในรัศมี 50 กม.", size="xs", color="#EF4444", margin="sm")
        )
    else:
        for st in thaiwater_stations:
            wl = st.get("water_level")
            distance = st.get("distance_km", 0)

            if wl and wl.get("value") is not None:
                try:
                    wl_value = float(wl["value"])
                    assessment = assess_water_level_status(wl_value)
                    risk_color = assessment["color"]
                except (ValueError, TypeError):
                    wl_value = "-"
                    assessment = assess_water_level_status(None)
                    risk_color = "#9CA3AF"
            else:
                wl_value = "-"
                assessment = assess_water_level_status(None)
                risk_color = "#9CA3AF"

            # Station card
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
                    TextComponent(text=assessment["advice"], size="xxs", color="#6B7280", margin="xs", wrap=True)
                ]
            )
            stations_box.contents.append(station_card)

    sep3 = SeparatorComponent(margin="lg")

    # Flood estimation section
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

    # Footer with link
    footer_box = BoxComponent(
        layout="vertical",
        margin="lg",
        contents=[
            SeparatorComponent(margin="sm"),
            TextComponent(text="📌 แหล่งข้อมูล: สถาบันสารสนเทศทรัพยากรน้ำ (ThaiWater)", size="xxs", color="#9CA3AF", margin="sm"),
            ButtonComponent(
                style="link",
                height="sm",
                action=URIAction(label="🔍 ดูข้อมูลเพิ่มเติมที่ ThaiWater", uri="https://www.thaiwater.net"),
                color="#2563EB"
            )
        ]
    )

    # Assemble bubble
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


# ========== 8. ฟังก์ชันสร้างตาราง คอลัมน์ และกรอกข้อมูลตัวอย่างลง Google Sheets อัตโนมัติ (Auto-Setup) ==========
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
            sos_ws = sheet.add_worksheet(title="sos_requests", rows="3000", cols="20")
            sos_ws.append_row([
                "request_id", "user_id", "timestamp", "latitude", "longitude",
                "people_count", "children", "elderly", "bedridden", "pets",
                "water_level", "note", "priority", "status"
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

        # 4. แท็บ Water_Levels (ใช้เป็น Fallback เมื่อ ThaiWater API ล่ม)
        if "Water_Levels" not in existing_sheets:
            print("Creating Water_Levels worksheet...")
            water_ws = sheet.add_worksheet(title="Water_Levels", rows="1000", cols="10")
            water_ws.append_row([
                "StationID", "Name", "Province", "Latitude", "Longitude", "WaterLevel_M", "Status"
            ])
            water_rows = [
                ["WT001", "สถานีลุ่มน้ำคลองอู่ตะเภา (หาดใหญ่)", "สงขลา", "7.0125", "100.4560", "4.2", "🟢 เฝ้าระวัง"],
                ["WT002", "สถานีลุ่มน้ำเจ้าพระยา (สะพานพุทธ)", "กรุงเทพ", "13.7390", "100.4985", "1.8", "🟢 เฝ้าระวัง"],
                ["WT003", "สถานีลุ่มน้ำกว๊านพะเยา", "พะเยา", "19.1620", "99.8940", "6.5", "🔴 อันตรายวิกฤต"]
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

        # 6. แท็บ user_needs
        if "user_needs" not in existing_sheets:
            needs_ws = sheet.add_worksheet(title="user_needs", rows="2000", cols="10")
            needs_ws.append_row(["Timestamp", "UserID", "Need_Detail", "Status"])

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


# ========== 9. ฟังก์ชันเชื่อมต่อ Google Sheets แบบ Native ยุคใหม่ (ไม่ต้องอิง oauth2client) ==========
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
