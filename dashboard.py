import bot_config as cfg
from flask import Blueprint, render_template_string, redirect, request

dashboard_bp = Blueprint('dashboard', __name__)

# หน้าหลักวิเคราะห์วินิจฉัยฐานข้อมูล
@dashboard_bp.route("/", methods=['GET'])
def index():
    cfg.get_sheets_client()
    db_status = f"<span class='text-emerald-600 font-bold'><i class='fa-solid fa-circle-check'></i> {cfg.LAST_SHEETS_ERROR}</span>" if cfg.SHEETS_INITIALIZED else f"<span class='text-red-600 font-bold'><i class='fa-solid fa-circle-xmark'></i> เชื่อมล้มเหลว (เหตุ: {cfg.LAST_SHEETS_ERROR})</span>"
    
    return f"""
    <div class="p-10 max-w-xl mx-auto mt-20 bg-white border border-slate-200 shadow-xl rounded-2xl" style="font-family: sans-serif;">
        <h2 class="text-2xl font-bold text-slate-800 mb-6 flex items-center"><i class="fa-solid fa-satellite-dish text-blue-600 mr-3"></i> FLOODCARE Diagnostic</h2>
        <div class="bg-slate-50 p-5 rounded-xl border-l-4 border-blue-600 mb-6">
            <p class="font-bold text-slate-700">สถานะการเชื่อมต่อ Google Sheets:</p>
            <p class="mt-2 text-sm">{db_status}</p>
        </div>
        <p class="text-xs text-slate-500 line-height-relaxed">
            <i class="fa-solid fa-circle-info"></i> กรุณาตรวจสอบให้มั่นใจว่าคุณได้ทำการแชร์สิทธิ์แบบ <b>Editor (ผู้แก้ไข)</b> ให้กับอีเมลเมลบอตตัวนี้แล้วใน Google Sheets:<br>
            <code class="bg-red-50 text-red-600 px-2 py-1 rounded inline-block mt-2 font-mono">floodcare-api@floodcare-database.iam.gserviceaccount.com</code>
        </p>
    </div>
    """

# Endpoint สำหรับผู้ใช้สั่งกู้ภัย และส่ง Push Message แจ้งเตือนสถานะผู้ประสบภัยเรียลไทม์
@dashboard_bp.route("/dashboard/update_status/<request_id>/<new_status>", methods=['GET'])
def update_status(request_id, new_status):
    sheets_client = cfg.get_sheets_client()
    clean_sheet_id = cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID)
    if sheets_client:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            sos_worksheet = sheet.worksheet("sos_requests")
            cell = sos_worksheet.find(request_id)
            if cell:
                # แก้ไขสถานะที่คอลัมน์ status (คอลัมน์ที่ 9)
                sos_worksheet.update_cell(cell.row, 9, new_status)
                
                # อ่านค่า User ID เพื่อยิง Push Message
                row_vals = sos_worksheet.row_values(cell.row)
                user_id = row_vals[1]
                
                if new_status == "IN_PROGRESS":
                    cfg.line_bot_api.push_message(
                        user_id,
                        cfg.TextSendMessage(text="📢 **อัปเดตความช่วยเหลือกู้ภัย:**\nขณะนี้เจ้าหน้าที่ศูนย์บัญชาการได้กด 'รับเรื่อง' เคสขอรับความช่วยเหลือของคุณแล้ว ทีมกู้ชีพกำลังเตรียมกำลังพลและเตรียมนำเรือเคลื่อนเข้าพื้นที่รับตัวท่าน โปรดรักษาความปลอดภัยและระมัดระวังในพื้นที่นะครับ")
                    )
                elif new_status == "CLOSED":
                    cfg.line_bot_api.push_message(
                        user_id,
                        cfg.TextSendMessage(text="✅ **ความช่วยเหลือเสร็จสิ้น:**\nเจ้าหน้าที่ได้ช่วยเหลือและปิดเคสประวัติ SOS ของคุณเรียบร้อยแล้ว ขอให้ปลอดภัยและรักษาความแข็งแรงของร่างกายต่อไปนะครับ")
                    )
        except Exception as e:
            print(f"Failed to update status and send push notification: {e}")
    return redirect("/dashboard")

# แผงบัญชาการแผนกควบคุม (Command Center Dashboard)
@dashboard_bp.route("/dashboard")
def dashboard():
    sheets_client = cfg.get_sheets_client()
    clean_sheet_id = cfg.extract_sheet_id(cfg.GOOGLE_SHEET_ID)
    
    sos_cases = []
    shelters = []
    user_needs = []
    error_msg = ""
    
    if not sheets_client:
        error_msg = f"ระบบตรวจพบบัญชีฐานข้อมูลล้มเหลว: {cfg.LAST_SHEETS_ERROR}"
    else:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            
            # โหลดข้อมูลผู้ใช้เพื่อทำ Join ชื่อจริง
            try:
                users_ws = sheet.worksheet("users")
                user_map = {u['user_id']: u for u in users_ws.get_all_records()}
            except:
                user_map = {}

            # 1. โหลดข้อมูลแจ้ง SOS
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
                print(f"Failed loading sos: {e}")
                
            # 2. โหลดข้อมูลศูนย์อพยพ
            try:
                shelters_worksheet = sheet.worksheet("Shelters")
                shelters = shelters_worksheet.get_all_records()
            except Exception as e:
                print(f"Failed loading shelters: {e}")

            # 3. โหลดความต้องการสิ่งของ
            try:
                needs_worksheet = sheet.worksheet("user_needs")
                user_needs = needs_worksheet.get_all_records()
                user_needs.reverse()
            except Exception as e:
                print(f"Failed loading needs: {e}")
                
        except Exception as e:
            error_msg = f"สิทธิ์การอ่านฐานข้อมูลของท่านไม่ผ่าน: {e}"

    # คำนวณสรุปสถิติหลัก
    total_cases = len(sos_cases)
    active_cases = sum(1 for c in sos_cases if c.get("status") in ["OPEN", "IN_PROGRESS"])
    pending_needs = sum(1 for n in user_needs if n.get("Status") == "PENDING")
    total_shelter_capacity = sum(int(s.get("Capacity", 100)) for s in shelters)
    total_shelter_occupancy = sum(int(s.get("Occupancy", 0)) for s in shelters)

    # แปลงพิกัด GIS ลงบนแผนที่ Leaflet
    map_cases = []
    for c in sos_cases:
        try:
            map_cases.append({
                "name": f"{c.get('first_name')} {c.get('last_name')}",
                "lat": float(c.get("latitude", 0)),
                "lon": float(c.get("longitude", 0)),
                "severity": c.get("severity", "ทั่วไป")
            })
        except: pass

    # โครงสร้างเว็บแดชบอร์ด Command Center แผนภาพกราฟฟิกสวยงามและไม่มี Emoji
    html_template = """
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>COMMAND CENTER — FLOODCARE AI</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body { font-family: 'Prompt', sans-serif; }
            .sidebar-active { background-color: #1E293B; border-left: 4px solid #3B82F6; color: #FFFFFF; }
            .sidebar-transition { transition: transform 0.3s ease-in-out; }
        </style>
    </head>
    <body class="bg-[#F8FAFC] text-slate-800 min-h-screen flex overflow-hidden">
        
        <!-- Sidebar Menu -->
        <aside id="sidebar" class="sidebar-transition w-64 bg-[#0F172A] text-slate-400 flex flex-col fixed inset-y-0 left-0 z-50 lg:static lg:translate-x-0 -translate-x-full">
            <div class="h-16 flex items-center px-6 border-b border-slate-850">
                <i class="fa-solid fa-tower-broadcast text-blue-500 text-2xl mr-3 animate-pulse"></i>
                <span class="text-xl font-bold text-white tracking-wide">CMD-CENTER</span>
            </div>
            <nav class="flex-1 p-4 space-y-2 overflow-y-auto">
                <a href="/dashboard" class="flex items-center space-x-3 px-4 py-3 rounded-lg sidebar-active transition">
                    <i class="fa-solid fa-chart-line"></i> <span>แผงควบคุมหลัก</span>
                </a>
                <a href="#sos-section" class="flex items-center space-x-3 hover:bg-slate-800 hover:text-white px-4 py-3 rounded-lg transition">
                    <i class="fa-solid fa-bell"></i> <span>เคสขอรับกู้ภัย</span>
                </a>
                <a href="#needs-section" class="flex items-center space-x-3 hover:bg-slate-800 hover:text-white px-4 py-3 rounded-lg transition">
                    <i class="fa-solid fa-box-open"></i> <span>ความต้องการสิ่งของ</span>
                </a>
                <a href="#shelter-section" class="flex items-center space-x-3 hover:bg-slate-800 hover:text-white px-4 py-3 rounded-lg transition">
                    <i class="fa-solid fa-house-chimney"></i> <span>ศูนย์พักพิงอพยพ</span>
                </a>
            </nav>
            <div class="p-4 border-t border-slate-800 text-xs text-slate-500 flex items-center justify-center space-x-2">
                <i class="fa-solid fa-circle text-green-500 animate-pulse"></i>
                <span>ระบบออนไลน์</span>
            </div>
        </aside>

        <!-- Main Panel -->
        <div class="flex-1 flex flex-col overflow-hidden">
            <!-- Header Nav -->
            <header class="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-6 z-40 shadow-sm flex-shrink-0">
                <div class="flex items-center space-x-4">
                    <button onclick="toggleSidebar()" class="lg:hidden text-slate-600 focus:outline-none">
                        <i class="fa-solid fa-bars text-xl"></i>
                    </button>
                    <h1 class="text-lg font-bold text-slate-800">ศูนย์ประสานงานช่วยเหลือระดับน้ำและศูนย์พักพิง</h1>
                </div>
                <div class="flex items-center space-x-3">
                    <span class="text-xs bg-blue-100 text-blue-700 px-3 py-1.5 rounded-full font-bold flex items-center">
                        <i class="fa-solid fa-arrows-spin mr-1.5 animate-spin"></i> อัปเดตข้อมูลอัตโนมัติ (60 วินาที)
                    </span>
                </div>
            </header>

            <!-- Scroll Area -->
            <main class="flex-1 overflow-y-auto p-6 space-y-6">
                {% if error_msg %}
                <div class="p-4 bg-red-50 border-l-4 border-red-500 text-red-700 rounded-xl shadow-sm flex items-center">
                    <i class="fa-solid fa-circle-exclamation mr-3 text-lg"></i> {{ error_msg }}
                </div>
                {% endif %}

                <!-- Metrics Widgets -->
                <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
                    <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
                        <div>
                            <p class="text-xs text-slate-400 font-bold uppercase tracking-wider">เคสขอรับกู้ภัยรวม</p>
                            <p class="text-3xl font-black text-slate-800 mt-1">{{ total_cases }}</p>
                        </div>
                        <div class="p-4 bg-blue-50 text-blue-600 rounded-2xl"><i class="fa-solid fa-bell text-xl"></i></div>
                    </div>
                    <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
                        <div>
                            <p class="text-xs text-slate-400 font-bold uppercase tracking-wider">อยู่ระหว่างช่วยเหลือ</p>
                            <p class="text-3xl font-black text-red-600 mt-1">{{ active_cases }}</p>
                        </div>
                        <div class="p-4 bg-red-50 text-red-600 rounded-2xl"><i class="fa-solid fa-triangle-exclamation text-xl"></i></div>
                    </div>
                    <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
                        <div>
                            <p class="text-xs text-slate-400 font-bold uppercase tracking-wider">ถุงยังชีพค้างเตรียม</p>
                            <p class="text-3xl font-black text-amber-600 mt-1">{{ pending_needs }}</p>
                        </div>
                        <div class="p-4 bg-amber-50 text-amber-600 rounded-2xl"><i class="fa-solid fa-box text-xl"></i></div>
                    </div>
                    <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
                        <div>
                            <p class="text-xs text-slate-400 font-bold uppercase tracking-wider">จำนวนผู้ลี้ภัยรวม</p>
                            <p class="text-3xl font-black text-purple-600 mt-1">{{ total_shelter_occupancy }} / {{ total_shelter_capacity }}</p>
                        </div>
                        <div class="p-4 bg-purple-50 text-purple-600 rounded-2xl"><i class="fa-solid fa-users text-xl"></i></div>
                    </div>
                </div>

                <!-- GIS Map Panel -->
                <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
                    <h3 class="font-bold text-slate-800 mb-4 flex items-center space-x-2">
                        <i class="fa-solid fa-map-location-dot text-blue-500"></i>
                        <span>แผนที่ภูมิสารสนเทศภัยพิบัติ และพิกัดผู้ขอความช่วยเหลือฉุกเฉิน (GIS Map)</span>
                    </h3>
                    <div id="map" class="h-96 rounded-2xl border border-slate-200 bg-slate-100 z-10"></div>
                </div>

                <!-- Tables Grid Layout -->
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    
                    <!-- SOS Queue List (2/3) -->
                    <div id="sos-section" class="lg:col-span-2 bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col">
                        <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 mb-4 border-b border-slate-100 gap-4">
                            <h3 class="font-bold text-slate-800 flex items-center space-x-2">
                                <i class="fa-solid fa-bell text-red-500"></i>
                                <span>รายการกรณีฉุกเฉินผู้ประสบภัย (SOS Queue)</span>
                            </h3>
                            <input id="searchInput" onkeyup="filterCases()" type="text" placeholder="ค้นหารายชื่อผู้ประสบภัย..." class="w-full md:w-64 bg-slate-50 border border-slate-200 text-xs px-4 py-2 rounded-xl text-slate-800 focus:outline-none focus:border-blue-500">
                        </div>
                        <div class="overflow-x-auto">
                            <table class="w-full text-left border-collapse text-xs">
                                <thead>
                                    <tr class="text-slate-400 border-b border-slate-100">
                                        <th class="py-3 px-2">ข้อมูลผู้ติดต่อ</th>
                                        <th class="py-3 px-2">ระดับความรุนแรง</th>
                                        <th class="py-3 px-2">รายละเอียดภัยพิบัติ</th>
                                        <th class="py-3 px-2 text-right">ดำเนินการกู้ภัย</th>
                                    </tr>
                                </thead>
                                <tbody id="sosTable">
                                    {% for case in sos_cases %}
                                    <tr class="border-b border-slate-50 hover:bg-slate-50/50 transition">
                                        <td class="py-4 px-2">
                                            <p class="font-bold text-slate-800">{{ case.get('first_name') }} {{ case.get('last_name') }}</p>
                                            <p class="text-[11px] text-blue-500 font-semibold mt-0.5"><i class="fa-solid fa-phone"></i> {{ case.get('phone') }}</p>
                                        </td>
                                        <td class="py-4 px-2">
                                            {% if 'วิกฤต' in case.get('severity', '') %}
                                            <span class="px-2.5 py-1 text-[10px] font-extrabold bg-red-100 text-red-700 rounded-full"><i class="fa-solid fa-circle-exclamation"></i> วิกฤตระดับสูงสุด</span>
                                            {% elif 'ระดับสูง' in case.get('severity', '') %}
                                            <span class="px-2.5 py-1 text-[10px] font-extrabold bg-orange-100 text-orange-700 rounded-full"><i class="fa-solid fa-triangle-exclamation"></i> ความรุนแรงสูง</span>
                                            {% else %}
                                            <span class="px-2.5 py-1 text-[10px] font-extrabold bg-green-100 text-green-700 rounded-full"><i class="fa-solid fa-circle-info"></i> ปานกลางปกติ</span>
                                            {% endif %}
                                        </td>
                                        <td class="py-4 px-2">
                                            <p class="text-slate-700 font-medium">กลุ่ม: {{ case.get('group', '-') }}</p>
                                            <p class="text-[10px] text-slate-400 mt-0.5"><i class="fa-solid fa-clock"></i> {{ case.get('timestamp') }}</p>
                                        </td>
                                        <td class="py-4 px-2 text-right space-y-1.5">
                                            <a href="https://www.google.com/maps/search/?api=1&query={{ case.get('latitude', 0) }},{{ case.get('longitude', 0) }}" target="_blank" class="w-full text-center block px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-[10px] font-semibold rounded-lg shadow-sm transition">
                                                <i class="fa-solid fa-map-location-dot mr-1"></i> เปิดพิกัดนำทาง
                                            </a>
                                            <div class="flex justify-end space-x-1">
                                                {% if case.get('status') == 'OPEN' %}
                                                <a href="/dashboard/update_status/{{ case.get('request_id') }}/IN_PROGRESS" class="px-2.5 py-1 bg-amber-500 hover:bg-amber-600 text-white font-bold text-[10px] rounded transition">รับเรื่อง</a>
                                                {% elif case.get('status') == 'IN_PROGRESS' %}
                                                <span class="px-2.5 py-1 bg-blue-100 text-blue-800 font-bold text-[10px] rounded">กำลังนำเรือเข้า</span>
                                                {% else %}
                                                <span class="px-2.5 py-1 bg-green-100 text-green-800 font-bold text-[10px] rounded">ช่วยสำเร็จ</span>
                                                {% endif %}
                                                
                                                {% if case.get('status') != 'CLOSED' %}
                                                <a href="/dashboard/update_status/{{ case.get('request_id') }}/CLOSED" class="px-2.5 py-1 bg-green-600 hover:bg-green-700 text-white font-bold text-[10px] rounded transition">ปิดเคส</a>
                                                {% endif %}
                                            </div>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Side Logistics Column (1/3) -->
                    <div class="space-y-6">
                        
                        <!-- Needs Logistics Summary -->
                        <div id="needs-section" class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col">
                            <h3 class="font-bold text-slate-800 mb-4 pb-2 border-b border-slate-100 flex items-center space-x-2">
                                <i class="fa-solid fa-box-open text-orange-500"></i>
                                <span>รายการจัดถุงยังชีพของอาสาสมัคร</span>
                            </h3>
                            <div class="space-y-3 overflow-y-auto max-h-[300px]">
                                {% for nd in user_needs %}
                                <div class="p-3 bg-slate-50 border border-slate-200/60 rounded-xl flex flex-col space-y-1 text-xs">
                                    <div class="flex justify-between items-center">
                                        <span class="font-extrabold text-blue-600"><i class="fa-solid fa-parachute-box"></i> {{ nd.get('Category', 'ของอุปโภค') }}</span>
                                        <a href="https://www.google.com/maps/search/?api=1&query={{ nd.get('Latitude', 0) }},{{ nd.get('Longitude', 0) }}" target="_blank" class="text-slate-400 hover:text-blue-600"><i class="fa-solid fa-location-crosshairs"></i></a>
                                    </div>
                                    <p class="text-slate-600">{{ nd.get('Details', '-') }}</p>
                                    <p class="text-[10px] text-slate-400"><i class="fa-solid fa-clock"></i> {{ nd.get('Timestamp') }}</p>
                                </div>
                                {% endfor %}
                            </div>
                        </div>

                        <!-- Shelter Panel -->
                        <div id="shelter-section" class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm flex flex-col">
                            <h3 class="font-bold text-slate-800 mb-4 pb-2 border-b border-slate-100 flex items-center space-x-2">
                                <i class="fa-solid fa-house-chimney text-emerald-500"></i>
                                <span>ศูนย์พักพิงอพยพที่มีที่ว่าง</span>
                            </h3>
                            <div class="space-y-4">
                                {% for sh in shelters %}
                                <div class="bg-slate-50 p-4 rounded-xl border border-slate-200/60 text-xs space-y-2">
                                    <div class="flex justify-between items-start">
                                        <div>
                                            <p class="font-bold text-slate-800 text-sm">{{ sh.get('Name') }}</p>
                                            <p class="text-slate-400 mt-0.5"><i class="fa-solid fa-location-dot"></i> อ.{sh.get('District')} จ.{sh.get('Province')}</p>
                                        </div>
                                        <span class="px-2 py-0.5 font-bold rounded {{ 'bg-red-100 text-red-700' if sh.get('Status') == 'เต็ม' else 'bg-green-100 text-green-700' }}">
                                            {{ sh.get('Status') }}
                                        </span>
                                    </div>
                                    <div class="w-full bg-slate-200 rounded-full h-1.5">
                                        <div class="bg-blue-600 h-1.5 rounded-full" style="width: {{ (sh.get('Occupancy', 0)|int / sh.get('Capacity', 100)|int * 100)|round|int if sh.get('Capacity', 100)|int > 0 else 0 }}%"></div>
                                    </div>
                                    <div class="flex justify-between text-[11px] text-slate-400">
                                        <span>ความจุ: {{ sh.get('Occupancy') }} / {{ sh.get('Capacity') }} คน</span>
                                        <span>โทร: {{ sh.get('Contact', '-') }}</span>
                                    </div>
                                    <p class="text-[11px] text-blue-600 font-medium pt-1 border-t border-slate-100"><i class="fa-solid fa-kit-medical"></i> {{ sh.get('Facilities', 'ไม่มี') }}</p>
                                </div>
                                {% endfor %}
                            </div>
                        </div>

                    </div>
                </div>
            </main>
        </div>
    </body>
    <script>
        // JS สำหรับ Sidebar บนอุปกรณ์เคลื่อนที่
        function toggleSidebar() {
            var sidebar = document.getElementById('sidebar');
            if (sidebar.classList.contains('-translate-x-full')) {
                sidebar.classList.remove('-translate-x-full');
            } else {
                sidebar.classList.add('-translate-x-full');
            }
        }

        // โหลดข้อมูลแผนที่ภูมิศาสตร์ Leaflet
        var map = L.map('map').setView([13.7563, 100.5018], 6);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);

        var cases = {{ map_cases|tojson }};
        cases.forEach(function(c) {
            L.circleMarker([c.lat, c.lon], {
                radius: 8,
                fillColor: c.severity == "วิกฤต" ? "#EF4444" : "#F97316",
                color: "#FFFFFF",
                weight: 1.5,
                opacity: 1,
                fillOpacity: 0.8
            }).addTo(map).bindPopup("<b>" + c.name + "</b><br>ระดับระดับน้ำ: " + c.severity);
        });

        // ฟังก์ชันฟิลเตอร์ตารางผู้ประสบภัย
        function filterCases() {
            var input = document.getElementById("searchInput");
            var filter = input.value.toLowerCase();
            var table = document.getElementById("sosTable");
            var tr = table.getElementsByTagName("tr");

            for (var i = 0; i < tr.length; i++) {
                var cell = tr[i].getElementsByTagName("td")[0];
                if (cell) {
                    var textValue = cell.textContent || cell.innerText;
                    if (textValue.toLowerCase().indexOf(filter) > -1) {
                        tr[i].style.display = "";
                    } else {
                        tr[i].style.display = "none";
                    }
                }
            }
        }
    </script>
    </html>
    """
    return render_template_string(html_template, sos_cases=sos_cases, shelters=shelters, user_needs=user_needs, error_msg=error_msg, total_cases=total_cases, active_cases=active_cases, pending_needs=pending_needs, total_shelter_capacity=total_shelter_capacity, total_shelter_occupancy=total_shelter_occupancy, map_cases=map_cases)
