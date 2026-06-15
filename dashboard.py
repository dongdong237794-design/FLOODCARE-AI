import json
from flask import Blueprint, render_template_string, request, redirect
import bot_config

# สร้างระบบ Blueprint สำหรับครอบหน้าต่างเว็บแดชบอร์ด
dashboard_bp = Blueprint('dashboard', __name__)

# หน้าหลักเช็กสถานะการรันเซิร์ฟเวอร์ แผนภูมิวินิจฉัยฐานข้อมูลกลาง (Diagnostic Control Panel)
@dashboard_bp.route("/", methods=['GET'])
def index():
    bot_config.get_sheets_client()
    db_status = f"<span style='color: #10b981; font-weight: bold;'>🟢 {bot_config.LAST_SHEETS_ERROR}</span>" if bot_config.SHEETS_INITIALIZED else f"<span style='color: #ef4444; font-weight: bold;'>🔴 เชื่อมต่อล้มเหลว (สาเหตุ: {bot_config.LAST_SHEETS_ERROR})</span>"
    
    routes_html = """
    <li style='margin-bottom:8px;'>🗺️ <b>index</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/</code> (Methods: GET)</li>
    <li style='margin-bottom:8px;'>🗺️ <b>dashboard</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/dashboard</code> (Methods: GET)</li>
    <li style='margin-bottom:8px;'>🗺️ <b>callback</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/callback</code> (Methods: POST)</li>
    """
    
    return f"""
    <div style="font-family: sans-serif; padding: 40px; max-width: 650px; margin: auto; border: 1px solid #ccc; border-radius: 12px; margin-top: 50px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
        <h2 style="color: #1E3A8A; text-anchor: center; margin-bottom: 25px;">🤖 FLOODCARE AI Diagnostic Panel</h2>
        <p style="color: #444; line-height: 1.6;">ระบบช่วยวิเคราะห์ความเสถียรและการเชื่อมต่อของเซิร์ฟเวอร์แบบเรียลไทม์:</p>
        
        <div style="background: #f8f9fa; padding: 15px; border-left: 4px solid #1E3A8A; margin: 20px 0; border-radius: 0 8px 8px 0;">
            <p style="margin: 0; font-weight: bold; color: #1E3A8A;">📊 Status การเชื่อม Google Sheets:</p>
            <p style="margin: 5px 0 0 0; font-size: 14px; color: #333;">{db_status}</p>
        </div>

        <p style="color: #666; font-size: 14px; margin-top: 25px;">นี่คือรายชื่อเส้นทางแอป (Active Routes):</p>
        <ul style="list-style: none; padding-left: 0; margin-top: 10px; font-size: 14px;">{routes_html}</ul>
        
        <hr style="border:0; border-top: 1px solid #eee; margin: 25px 0;">
        <p style="color: #e11d48; font-size: 13px; font-weight: bold; line-height:1.5;">
            ⚠️ คำแนะนำสำหรับการทำตามสเต็ปเชื่อมต่อสำเร็จ:<br>
            1. ตรวจเช็กหน้า Google Sheets ว่าได้กดปุ่มแชร์สิทธิ์เป็น <b>Editor (ผู้แก้ไข)</b> ให้กับอีเมลเมลบอตตัวนี้แล้วหรือยัง:<br>
            <code style="background:#fff1f2; padding:3px 6px; font-size: 12px; border-radius: 4px; display: inline-block; margin-top: 5px;">floodcare-api@floodcare-database.iam.gserviceaccount.com</code><br>
            2. ตรวจสอบว่าแปร GOOGLE_SHEET_ID และ GOOGLE_SERVICE_ACCOUNT_JSON สะกดถูกช่องไม่มีตกหล่นครับ
        </p>
    </div>
    """

# Endpoint สำหรับรับเคสและปิดเคสจากหน้าเว็บตรง
@dashboard_bp.route("/dashboard/update_status/<request_id>/<new_status>", methods=['GET'])
def update_status(request_id, new_status):
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    if sheets_client:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            sos_worksheet = sheet.worksheet("sos_requests")
            cell = sos_worksheet.find(request_id)
            if cell:
                sos_worksheet.update_cell(cell.row, 14, new_status)
        except Exception as e:
            print(f"Failed to update status on Sheets: {e}")
    return redirect("/dashboard")

# Endpoint สำหรับบันทึกศูนย์พักพิงที่ถูกสร้างขึ้นใหม่
@dashboard_bp.route("/dashboard/add_shelter", methods=['POST'])
def add_shelter():
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    
    sh_id = request.form.get("sh_id")
    sh_name = request.form.get("sh_name")
    sh_province = request.form.get("sh_province")
    sh_district = request.form.get("sh_district")
    sh_lat = request.form.get("sh_lat")
    sh_lon = request.form.get("sh_lon")
    sh_capacity = request.form.get("sh_capacity", "100")
    sh_occupancy = request.form.get("sh_occupancy", "0")
    
    facs = []
    if request.form.get("fac_elec"): facs.append("ไฟฟ้า")
    if request.form.get("fac_water"): facs.append("น้ำสะอาด")
    if request.form.get("fac_net"): facs.append("อินเทอร์เน็ต")
    if request.form.get("fac_wheelchair"): facs.append("รองรับผู้พิการ")
    if request.form.get("fac_pet"): facs.append("รับสัตว์เลี้ยง")
    if request.form.get("fac_doc"): facs.append("มีแพทย์ประจำ")
    facilities_str = ", ".join(facs) if facs else "ไม่มี"
    
    if sheets_client:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            shelters_ws = sheet.worksheet("Shelters")
            shelters_ws.append_row([
                sh_id, sh_name, sh_province, sh_district, sh_lat, sh_lon,
                sh_capacity, sh_occupancy, "ว่าง", "100", "15", "50", facilities_str
            ])
        except Exception as e:
            print(f"Failed to save shelter: {e}")
    return redirect("/dashboard")

# หน้าแผงควบคุมหลัก (Command Center Dashboard - Modern Light Minimal)
@dashboard_bp.route("/dashboard", methods=['GET'])
def dashboard():
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    sos_cases = []
    shelters = []
    error_msg = ""
    
    if not sheets_client:
        error_msg = f"⚠️ ระบบตรวจพบข้อขัดข้องในการเรียกสิทธิ์: {bot_config.LAST_SHEETS_ERROR}"
    else:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            
            # ดึงข้อมูลผู้ใช้เพื่อนำมา JOIN หาชื่อจริงและเบอร์โทรศัพท์บน Dashboard
            try:
                users_ws = sheet.worksheet("users")
                users_rows = users_ws.get_all_records()
                user_map = {u['user_id']: u for u in users_rows}
            except Exception as e:
                print(f"Failed to load users for JOIN: {e}")
                user_map = {}

            # 1. ดึงข้อมูลกรณีฉุกเฉินผู้ประสบภัย (sos_requests)
            try:
                sos_worksheet = sheet.worksheet("sos_requests")
                raw_cases = sos_worksheet.get_all_records()
                for rc in raw_cases:
                    u_id = rc.get("user_id")
                    u_info = user_map.get(u_id, {})
                    rc["first_name"] = u_info.get("first_name", "ผู้แจ้ง")
                    rc["last_name"] = u_info.get("last_name", "ทั่วไป")
                    rc["phone"] = u_info.get("phone", "-")
                    sos_cases.append(rc)
                sos_cases.reverse()
            except Exception as e:
                print(f"Failed to load SOS: {e}")
                
            # 2. ดึงข้อมูลศูนย์อพยพจริง (Shelters)
            try:
                shelters_worksheet = sheet.worksheet("Shelters")
                shelters = shelters_worksheet.get_all_records()
            except Exception as e:
                print(f"Failed to load Shelters: {e}")
                
        except Exception as e:
            error_msg = f"ไม่สามารถเข้าถึงฐานข้อมูลกลางได้: {e}"

    total_cases = len(sos_cases)
    critical_count = sum(1 for c in sos_cases if "CRITICAL" in str(c.get("priority", "")))
    high_count = sum(1 for c in sos_cases if "HIGH" in str(c.get("priority", "")))
    bedridden_count = sum(1 for c in sos_cases if "YES" in str(c.get("bedridden", "")) or "ใช่" in str(c.get("bedridden", "")))
    
    # ดึงค่าพิกัดพิกัด GIS สำหรับแผนที่เวกเตอร์ Leaflet.js
    sos_map_data = []
    for c in sos_cases:
        try:
            sos_map_data.append({
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}",
                "lat": float(c.get("latitude", 0)),
                "lon": float(c.get("longitude", 0)),
                "prio": c.get("priority", "🟢 NORMAL")
            })
        except:
            pass
            
    shelter_map_data = []
    for s in shelters:
        try:
            shelter_map_data.append({
                "name": s.get("Name", "ศูนย์อพยพ"),
                "lat": float(s.get("Latitude", 0)),
                "lon": float(s.get("Longitude", 0))
            })
        except:
            pass

    # หน้าจอเว็บดีไซน์ดีไซน์ Modern Minimal (Light Mode) 6 ปุ่มใหม่ระดับพรีเมียม
    html_template = """
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>COMMAND CENTER — FLOODCARE AI</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body { font-family: 'Prompt', sans-serif; }
        </style>
    </head>
    <body class="bg-[#F5F7FB] text-[#111827] min-h-screen">
        <div class="flex min-h-screen">
            <!-- Sidebar ด้านซ้ายระดับการตอบคำสั่ง -->
            <aside class="w-64 bg-white border-r border-gray-200 p-6 flex flex-col justify-between hidden lg:flex">
                <div>
                    <div class="flex items-center space-x-3 mb-8">
                        <span class="text-3xl">🛡️</span>
                        <h1 class="text-xl font-bold text-gray-800 tracking-wide">FLOODCARE AI</h1>
                    </div>
                    <nav class="space-y-1">
                        <a href="/dashboard" class="flex items-center space-x-3 bg-blue-50 text-blue-600 px-4 py-3 rounded-xl font-medium transition">
                            <span>📊</span> <span>Dashboard</span>
                        </a>
                        <a href="#" class="flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <span>📋</span> <span>SOS Cases</span>
                        </a>
                        <a href="#" class="flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <span>🏠</span> <span>Shelters</span>
                        </a>
                        <a href="#" class="flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <span>🗺️</span> <span>Flood Map</span>
                        </a>
                        <a href="#" class="flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <span>📈</span> <span>Analytics</span>
                        </a>
                        <a href="#" class="flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <span>⚙️</span> <span>Settings</span>
                        </a>
                    </nav>
                </div>
                <div class="pt-6 border-t border-gray-100 text-xs text-gray-500">
                    <div class="flex items-center space-x-2">
                        <div class="w-2.5 h-2.5 bg-green-500 rounded-full animate-pulse"></div>
                        <span class="font-semibold text-green-600">🟢 System Online</span>
                    </div>
                </div>
            </aside>

            <!-- Main Content Area -->
            <div class="flex-1 flex flex-col overflow-hidden">
                <!-- Top Navbar -->
                <header class="h-16 bg-white border-b border-gray-200 px-6 flex items-center justify-between">
                    <h2 class="text-lg font-bold text-gray-800">ศูนย์ประสานงานระบบอุทกภัยอัจฉริยะ (COMMAND CENTER)</h2>
                    <div class="flex items-center space-x-4">
                        <span class="text-xs bg-green-100 text-green-700 px-3 py-1.5 rounded-full font-bold">🟢 Realtime Sync Success</span>
                    </div>
                </header>

                <!-- Scrollable Body -->
                <main class="flex-1 overflow-y-auto p-6">
                    {% if error_msg %}
                    <div class="bg-red-50 border-l-4 border-red-500 text-red-700 p-4 rounded-xl mb-6 shadow-sm">
                        {{ error_msg }}
                    </div>
                    {% endif %}

                    <!-- Summary Cards (4 Cards) -->
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
                        <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-sm text-gray-500">เคสแจ้งเหตุทั้งหมด</p>
                                <p class="text-3xl font-bold text-gray-900 mt-2">{{ total_cases }} เคส</p>
                            </div>
                            <span class="text-3xl bg-blue-50 p-3 rounded-xl">📊</span>
                        </div>
                        <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-sm font-semibold text-red-500">🚨 เคสวิกฤต (Critical)</p>
                                <p class="text-3xl font-bold text-red-600 mt-2">{{ critical_count }} เคส</p>
                            </div>
                            <span class="text-3xl bg-red-50 p-3 rounded-xl">🔴</span>
                        </div>
                        <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-sm font-semibold text-gray-500">ผู้ประสบภัยรวมทั้งหมด</p>
                                <p class="text-3xl font-bold text-gray-900 mt-2">{{ total_cases }} ราย</p>
                            </div>
                            <span class="text-3xl bg-amber-50 p-3 rounded-xl">👥</span>
                        </div>
                        <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-sm font-semibold text-purple-600">🏥 ผู้ป่วยติดเตียง</p>
                                <p class="text-3xl font-bold text-purple-700 mt-2">{{ bedridden_count }} ราย</p>
                            </div>
                            <span class="text-3xl bg-purple-50 p-3 rounded-xl">🩹</span>
                        </div>
                    </div>

                    <!-- แผนที่ภูมิสารสนเทศ (GIS MAP) -->
                    <div class="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm mb-8">
                        <h3 class="text-base font-bold text-gray-800 mb-3 flex items-center space-x-2">
                            <span>🗺️</span> <span>แผนที่คัดกรองพิกัดกู้ภัยภูมิสารสนเทศ (Live GIS Crisis Map)</span>
                        </h3>
                        <div id="map" class="h-80 rounded-xl border border-gray-200 bg-gray-50"></div>
                    </div>

                    <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                        <!-- SOS Queue (2 ใน 3 ส่วน) -->
                        <div class="lg:col-span-2 bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
                            <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 mb-4 border-b border-gray-100 gap-4">
                                <h3 class="text-lg font-bold text-gray-800 flex items-center space-x-2">
                                    <span>📋</span> <span>รายการขอรับช่วยเหลือ SOS</span>
                                </h3>
                                <input id="searchInput" onkeyup="filterCases()" type="text" placeholder="🔍 ค้นหาตามชื่อ/พื้นที่..." class="w-full md:w-64 bg-gray-50 border border-gray-200 text-sm px-4 py-2 rounded-xl text-gray-800 focus:outline-none focus:border-blue-500">
                            </div>

                            <div class="overflow-x-auto">
                                <table class="w-full text-left border-collapse text-sm">
                                    <thead>
                                        <tr class="border-b border-gray-200 text-gray-500 font-semibold">
                                            <th class="py-3 px-2">เลขเคส / ระดับความเร่งด่วน</th>
                                            <th class="py-3 px-2">ผู้ประสบภัย / ข้อมูลติดต่อ</th>
                                            <th class="py-3 px-2">ความเร่งด่วน</th>
                                            <th class="py-3 px-2">ระดับน้ำ / รายละเอียด</th>
                                            <th class="py-3 px-2">การกู้ภัย</th>
                                        </tr>
                                    </thead>
                                    <tbody id="sosTable">
                                        {% for case in sos_cases %}
                                        <tr class="border-b border-gray-100 hover:bg-gray-50/50 transition py-4">
                                            <td class="py-4 px-2">
                                                <p class="font-bold text-gray-800 text-xs">{{ case.get('request_id', 'SOS-MOCK') }}</p>
                                                {% if '🔴' in case.get('priority', '') %}
                                                <span class="inline-block mt-2 px-2 py-0.5 text-[10px] font-bold bg-red-100 text-red-700 rounded-full">🔴 CRITICAL</span>
                                                {% elif '🟠' in case.get('priority', '') %}
                                                <span class="inline-block mt-2 px-2 py-0.5 text-[10px] font-bold bg-orange-100 text-orange-700 rounded-full">🟠 HIGH</span>
                                                {% else %}
                                                <span class="inline-block mt-2 px-2 py-0.5 text-[10px] font-bold bg-green-100 text-green-700 rounded-full">🟢 NORMAL</span>
                                                {% endif %}
                                            </td>
                                            <td class="py-4 px-2">
                                                <p class="font-bold text-gray-800">{{ case.get('first_name', '') }} {{ case.get('last_name', '') }}</p>
                                                <p class="text-xs text-blue-600 font-semibold mt-1">📞 {{ case.get('phone', '-') }}</p>
                                            </td>
                                            <td class="py-4 px-2">
                                                <p class="text-gray-700">จำนวน: <b>{{ case.get('people_count', '1') }}</b> คน</p>
                                                <p class="text-xs text-purple-600 mt-1">ติดเตียง: {{ case.get('bedridden', 'NO') }} | สัตว์เลี้ยง: {{ case.get('pets', 'NO') }}</p>
                                            </td>
                                            <td class="py-4 px-2">
                                                <p class="font-semibold text-blue-600 text-xs">🌊 น้ำท่วม: {{ case.get('water_level', '-') }}</p>
                                                <p class="text-xs text-gray-500 mt-1 max-w-xs truncate">{{ case.get('note', '-') }}</p>
                                            </td>
                                            <td class="py-4 px-2 space-y-2">
                                                <a href="https://www.google.com/maps/search/?api=1&query={{ case.get('latitude', 0) }},{{ case.get('longitude', 0) }}" target="_blank" class="w-full text-center block px-3 py-1.5 bg-blue-600 hover:bg-blue-700 transition font-bold text-[10px] text-white rounded-lg shadow-sm">
                                                    🗺️ แผนที่นำทาง
                                                </a>
                                                <div class="flex gap-1">
                                                    {% if case.get('status') == 'OPEN' %}
                                                    <a href="/dashboard/update_status/{{ case.get('request_id') }}/IN_PROGRESS" class="w-1/2 text-center block py-1 bg-amber-500 hover:bg-amber-600 text-white font-bold text-[9px] rounded">รับเคส</a>
                                                    {% elif case.get('status') == 'IN_PROGRESS' %}
                                                    <span class="w-1/2 text-center block py-1 bg-blue-100 text-blue-800 font-bold text-[9px] rounded">กำลังช่วย</span>
                                                    {% else %}
                                                    <span class="w-1/2 text-center block py-1 bg-green-100 text-green-800 font-bold text-[9px] rounded">เสร็จสิ้น</span>
                                                    {% endif %}
                                                    
                                                    {% if case.get('status') != 'CLOSED' %}
                                                    <a href="/dashboard/update_status/{{ case.get('request_id') }}/CLOSED" class="w-1/2 text-center block py-1 bg-green-600 hover:bg-green-700 text-white font-bold text-[9px] rounded">ปิดเคส</a>
                                                    {% endif %}
                                                </div>
                                            </td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <!-- Shelter Card Column (1 ใน 3 ส่วน) -->
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
                            <div class="flex justify-between items-center mb-6">
                                <h3 class="text-lg font-bold text-gray-800 flex items-center space-x-2">
                                    <span>🏠</span> <span>ศูนย์พักพิงในพื้นที่</span>
                                </h3>
                                <button onclick="toggleModal(true)" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 transition text-white text-xs font-bold rounded-xl shadow-sm">+ Add Shelter</button>
                            </div>

                            <div class="space-y-4">
                                {% for sh in shelters %}
                                <div class="bg-gray-50 p-4 rounded-xl border border-gray-200/60">
                                    <div class="flex justify-between items-start mb-2">
                                        <div>
                                            <p class="font-bold text-gray-800">{{ sh.get('Name', 'ไม่ระบุ') }}</p>
                                            <p class="text-xs text-gray-500 mt-1">📍 อ.{sh.get('District', '')} จ.{sh.get('Province', '')}</p>
                                        </div>
                                        <span class="px-2 py-0.5 text-xs font-bold rounded-full {{ 'bg-red-100 text-red-700' if sh.get('Status') == 'เต็ม' else 'bg-green-100 text-green-700' }}">
                                            {{ sh.get('Status', 'ว่าง') }}
                                        </span>
                                    </div>
                                    <div class="w-full bg-gray-200 rounded-full h-2 mt-4">
                                        <div class="bg-blue-600 h-2 rounded-full" style="width: {{ (sh.get('Occupancy', 0)|int / sh.get('Capacity', 100)|int * 100)|round|int if sh.get('Capacity', 100)|int > 0 else 0 }}%"></div>
                                    </div>
                                    <div class="flex justify-between items-center text-xs text-gray-500 mt-2">
                                        <span>พักอยู่: {{ sh.get('Occupancy', 0) }} / {{ sh.get('Capacity', 100) }} คน</span>
                                        <span>ติดต่อ: {{ sh.get('Contact', '-') }}</span>
                                    </div>
                                    <div class="text-xs text-blue-600 font-semibold mt-2 pt-2 border-t border-gray-100">
                                        🎒 สิ่งอำนวยความสะดวก: {{ sh.get('Facilities', 'ไม่มี') }}
                                    </div>
                                </div>
                                {% endfor %}
                            </div>
                        </div>
                    </div>
                </main>
            </div>
        </div>

        <!-- Add Shelter Wizard 3-Step Modal -->
        <div id="shelterModal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center hidden">
            <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 mx-4 relative border border-gray-200">
                <button onclick="toggleModal(false)" class="absolute top-4 right-4 text-gray-400 hover:text-gray-600 text-xl font-bold">&times;</button>
                <h3 class="text-xl font-bold text-gray-800 mb-4">🏠 เพิ่มศูนย์พักพิงใหม่ (Add Shelter)</h3>
                
                <form id="shelterForm" action="/dashboard/add_shelter" method="POST">
                    <!-- Step 1: ข้อมูลพื้นฐาน -->
                    <div id="step1" class="space-y-4">
                        <span class="text-xs bg-blue-100 text-blue-700 px-3 py-1 rounded-full font-bold">Step 1/3: ข้อมูลพื้นฐาน</span>
                        <div class="mt-3">
                            <label class="block text-xs font-bold text-gray-600 mb-1">รหัสศูนย์พักพิง (Shelter ID)</label>
                            <input type="text" name="sh_id" placeholder="เช่น SH004" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-gray-600 mb-1">ชื่อสถานที่/ศูนย์พักพิง</label>
                            <input type="text" name="sh_name" placeholder="เช่น โรงเรียนกู้ภัยอุทกภัย" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">จังหวัด</label>
                                <input type="text" name="sh_province" placeholder="เช่น สงขลา" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">อำเภอ</label>
                                <input type="text" name="sh_district" placeholder="เช่น หาดใหญ่" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">ละติจูด (Latitude)</label>
                                <input type="text" id="sh_lat" name="sh_lat" placeholder="7.0125" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">ลองจิจูด (Longitude)</label>
                                <input type="text" id="sh_lon" name="sh_lon" placeholder="100.4560" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                        </div>
                        <button type="button" onclick="getCurrentLocation()" class="w-full py-2 bg-slate-100 hover:bg-slate-200 text-xs font-bold rounded-xl text-slate-700 transition">📍 ดึงพิกัดจากตำแหน่งปัจจุบันของฉัน</button>
                        <button type="button" onclick="goToStep(2)" class="w-full py-3 bg-blue-600 hover:bg-blue-700 text-sm font-bold text-white rounded-xl transition">ถัดไป (Next)</button>
                    </div>

                    <!-- Step 2: ความจุ -->
                    <div id="step2" class="space-y-4 hidden">
                        <span class="text-xs bg-blue-100 text-blue-700 px-3 py-1 rounded-full font-bold">Step 2/3: ความจุและสิ่งก่อสร้าง</span>
                        <div class="mt-3">
                            <label class="block text-xs font-bold text-gray-600 mb-1">ความจุคนสูงสุด (คน)</label>
                            <input type="number" name="sh_capacity" value="100" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-gray-600 mb-1">จำนวนผู้เข้าพักในปัจจุบัน (คน)</label>
                            <input type="number" name="sh_occupancy" value="0" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                        <div class="flex gap-4">
                            <button type="button" onclick="goToStep(1)" class="w-1/2 py-3 bg-slate-100 hover:bg-slate-200 text-sm font-bold text-slate-700 rounded-xl transition">ย้อนกลับ (Back)</button>
                            <button type="button" onclick="goToStep(3)" class="w-1/2 py-3 bg-blue-600 hover:bg-blue-700 text-sm font-bold text-white rounded-xl transition">ถัดไป (Next)</button>
                        </div>
                    </div>

                    <!-- Step 3: สิ่งอำนวยความสะดวก -->
                    <div id="step3" class="space-y-4 hidden">
                        <span class="text-xs bg-blue-100 text-blue-700 px-3 py-1 rounded-full font-bold">Step 3/3: สิ่งอำนวยความสะดวกภายใน</span>
                        <div class="grid grid-cols-2 gap-3 mt-3 text-sm">
                            <label class="flex items-center space-x-2"><input type="checkbox" name="fac_elec" checked> <span>⚡ มีไฟฟ้า</span></label>
                            <label class="flex items-center space-x-2"><input type="checkbox" name="fac_water" checked> <span>💧 น้ำสะอาด</span></label>
                            <label class="flex items-center space-x-2"><input type="checkbox" name="fac_net"> <span>📶 อินเทอร์เน็ต</span></label>
                            <label class="flex items-center space-x-2"><input type="checkbox" name="fac_wheelchair"> <span>♿ รองรับผู้พิการ</span></label>
                            <label class="flex items-center space-x-2"><input type="checkbox" name="fac_pet"> <span>🐶 รับสัตว์เลี้ยง</span></label>
                            <label class="flex items-center space-x-2"><input type="checkbox" name="fac_doc"> <span>🩹 มีแพทย์ประจำ</span></label>
                        </div>
                        <div class="flex gap-4 mt-6">
                            <button type="button" onclick="goToStep(2)" class="w-1/2 py-3 bg-slate-100 hover:bg-slate-200 text-sm font-bold text-slate-700 rounded-xl transition">ย้อนกลับ (Back)</button>
                            <button type="submit" class="w-1/2 py-3 bg-green-600 hover:bg-green-700 text-sm font-bold text-white rounded-xl transition">บันทึกข้อมูล (Save)</button>
                        </div>
                    </div>
                </form>
            </div>
        </div>

        <script>
            // ระบบสไลด์แผนที่กู้ภัยจริงบน Leaflet.js
            var map = L.map('map').setView([13.7563, 100.5018], 6); // ซูมดูภาพรวมทั้งไทย
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors'
            }).addTo(map);

            var sosData = {{ sos_map_data|tojson }};
            var shelterData = {{ shelter_map_data|tojson }};

            // วาดหมุดสีแดงกรณีเคส SOS ฉุกเฉิน
            sosData.forEach(function(c) {
                if(c.lat && c.lon) {
                    L.circleMarker([c.lat, c.lon], {
                        radius: 8,
                        fillColor: "#EF4444",
                        color: "#B91C1C",
                        weight: 2,
                        opacity: 1,
                        fillOpacity: 0.8
                    }).addTo(map).bindPopup("<b>🆘 เคส SOS:</b> " + c.name + "<br>ระดับภัย: " + c.prio);
                }
            });

            // วาดหมุดกรณีศูนย์อพยพ
            shelterData.forEach(function(s) {
                if(s.lat && s.lon) {
                    L.marker([s.lat, s.lon]).addTo(map).bindPopup("<b>🏠 ศูนย์อพยพ:</b> " + s.name);
                }
            });

            // ฟังก์ชันซ่อน/เปิด Add Shelter Modal
            function toggleModal(open) {
                var modal = document.getElementById("shelterModal");
                if (open) {
                    modal.classList.remove("hidden");
                    goToStep(1); // รีเซ็ตมาหน้า 1 เสมอเมื่อกดเปิด
                } else {
                    modal.classList.add("hidden");
                }
            }

            // จัดการ Wizard สเต็ปของหน้าฟอร์ม
            function goToStep(stepNum) {
                document.getElementById("step1").classList.add("hidden");
                document.getElementById("step2").classList.add("hidden");
                document.getElementById("step3").classList.add("hidden");
                document.getElementById("step" + stepNum).classList.remove("hidden");
            }

            // ระบบดึงพิกัด GPS อัตโนมัติจากเบราว์เซอร์ของผู้ใช้
            function getCurrentLocation() {
                if (navigator.geolocation) {
                    navigator.geolocation.getCurrentPosition(function(position) {
                        document.getElementById("sh_lat").value = position.coords.latitude;
                        document.getElementById("sh_lon").value = position.coords.longitude;
                    }, function() {
                        alert("โปรดกดอนุญาตสิทธิ์เบราว์เซอร์เพื่อดึงตำแหน่งพิกัด GPS ครับ");
                    });
                } else {
                    alert("เบราว์เซอร์ของคุณไม่รองรับการดึงพิกัดอัตโนมัติครับ");
                }
            }

            // ฟังก์ชันฟิลเตอร์ตารางด่วนตามตัวอักษร
            function filterCases() {
                var input = document.getElementById("searchInput");
                var filter = input.value.toLowerCase();
                var table = document.getElementById("sosTable");
                var tr = table.getElementsByTagName("tr");

                for (var i = 0; i < tr.length; i++) {
                    var areaCell = tr[i].getElementsByTagName("td")[1];
                    if (areaCell) {
                        var textValue = areaCell.textContent || areaCell.innerText;
                        if (textValue.toLowerCase().indexOf(filter) > -1) {
                            tr[i].style.display = "";
                        } else {
                            tr[i].style.display = "none";
                        }
                    }
                }
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template, sos_cases=sos_cases, shelters=shelters, error_msg=error_msg, total_cases=total_cases, critical_count=critical_count, high_count=high_count, bedridden_count=bedridden_count, sos_map_data=sos_map_data, shelter_map_data=shelter_map_data)
