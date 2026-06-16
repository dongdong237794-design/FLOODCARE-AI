import json
from flask import Blueprint, render_template_string, request, redirect
import bot_config

dashboard_bp = Blueprint('dashboard', __name__)


# =============================================================================
# DIAGNOSTIC PANEL (หน้าแรก /)
# =============================================================================
@dashboard_bp.route("/", methods=['GET'])
def index():
    bot_config.get_sheets_client()
    db_status = f"<span style='color: #10b981; font-weight: bold;'>🟢 {bot_config.LAST_SHEETS_ERROR}</span>" if bot_config.SHEETS_INITIALIZED else f"<span style='color: #ef4444; font-weight: bold;'>🔴 เชื่อมต่อล้มเหลว: {bot_config.LAST_SHEETS_ERROR}</span>"

    routes_html = """
    <li style='margin-bottom:8px;'><b>index</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/</code> (GET)</li>
    <li style='margin-bottom:8px;'><b>dashboard</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/dashboard</code> (GET)</li>
    <li style='margin-bottom:8px;'><b>callback</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/callback</code> (POST)</li>
    """

    return f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>FLOODCARE AI - Diagnostic</title>
        <style>
            body {{ font-family: 'Prompt', sans-serif; background: #f5f7fb; }}
            .container {{ max-width: 650px; margin: 50px auto; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border: 1px solid #ccc; }}
            h2 {{ color: #1E3A8A; margin-bottom: 25px; }}
            .status-box {{ background: #f8f9fa; padding: 15px; border-left: 4px solid #1E3A8A; margin: 20px 0; border-radius: 0 8px 8px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>FLOODCARE AI Diagnostic Panel</h2>
            <p style="color: #444; line-height: 1.6;">ระบบวิเคราะห์ความเสถียรเรียลไทม์:</p>
            <div class="status-box">
                <p style="margin: 0; font-weight: bold; color: #1E3A8A;">📊 Google Sheets Status:</p>
                <p style="margin: 5px 0 0 0; font-size: 14px; color: #333;">{db_status}</p>
            </div>
            <p style="color: #666; font-size: 14px; margin-top: 25px;">Active Routes:</p>
            <ul style="list-style: none; padding-left: 0; margin-top: 10px; font-size: 14px;">{routes_html}</ul>
            <hr style="border:0; border-top: 1px solid #eee; margin: 25px 0;">
            <p style="color: #e11d48; font-size: 13px; font-weight: bold;">
                ⚠️ คำแนะนำ:<br>
                1. ตรวจสอบว่าแชร์สิทธิ์ Editor ให้เมลบอตแล้ว<br>
                <code style="background:#fff1f2; padding:3px 6px; font-size: 12px; border-radius: 4px; display: inline-block; margin-top: 5px;">floodcare-api@floodcare-database.iam.gserviceaccount.com</code>
            </p>
        </div>
    </body>
    </html>
    """


# =============================================================================
# UPDATE STATUS (รับเคส / ปิดเคส)
# =============================================================================
@dashboard_bp.route("/dashboard/update_status/<request_id>/<new_status>", methods=['GET'])
def update_status(request_id, new_status):
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    if sheets_client:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            sos_ws = sheet.worksheet("sos_requests")
            cell = sos_ws.find(request_id)
            if cell:
                sos_ws.update_cell(cell.row, 14, new_status)
                # TODO: ส่ง Push Message แจ้งผู้ใช้เมื่อ status = IN_PROGRESS
                if new_status == "IN_PROGRESS":
                    # ดึง user_id จากแถวที่อัปเดต
                    user_id = sos_ws.cell(cell.row, 2).value
                    if user_id:
                        msg = (
                            "📢 อัปเดตสถานะการช่วยเหลือ:\n"
                            "ทีมกู้ภัยรับทราบเคสของคุณแล้ว\n"
                            "กำลังเดินทางเข้าพื้นที่ครับ\n\n"
                            "โปรดเตรียมพร้อมและรักษาความปลอดภัย"
                        )
                        bot_config.send_line_notification(user_id, msg)
        except Exception as e:
            print(f"Failed to update status: {e}")
    return redirect("/dashboard")


# =============================================================================
# ADD SHELTER
# =============================================================================
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


# =============================================================================
# UPDATE NEED STATUS (ปิดเคสความต้องการ)
# =============================================================================
@dashboard_bp.route("/dashboard/update_need/<timestamp>/<user_id>/<new_status>", methods=['GET'])
def update_need_status_route(timestamp, user_id, new_status):
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    if sheets_client:
        bot_config.update_need_status(sheets_client, clean_sheet_id, timestamp, user_id, new_status)
    return redirect("/dashboard?tab=needs")


# =============================================================================
# MAIN DASHBOARD COMMAND CENTER
# =============================================================================
@dashboard_bp.route("/dashboard", methods=['GET'])
def dashboard():
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    sos_cases = []
    shelters = []
    user_needs = []
    error_msg = ""

    if not sheets_client:
        error_msg = f"⚠️ ระบบขัดข้อง: {bot_config.LAST_SHEETS_ERROR}"
    else:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)

            # ดึงข้อมูล users
            try:
                users_ws = sheet.worksheet("users")
                users_rows = users_ws.get_all_records()
                user_map = {u['user_id']: u for u in users_rows}
            except Exception as e:
                print(f"Failed to load users: {e}")
                user_map = {}

            # 1. SOS Cases
            try:
                sos_ws = sheet.worksheet("sos_requests")
                raw_cases = sos_ws.get_all_records()
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

            # 2. Shelters
            try:
                shelters_ws = sheet.worksheet("Shelters")
                shelters = shelters_ws.get_all_records()
            except Exception as e:
                print(f"Failed to load Shelters: {e}")

            # 3. User Needs
            try:
                needs_ws = sheet.worksheet("user_needs")
                user_needs = needs_ws.get_all_records()
                user_needs.reverse()
            except Exception as e:
                print(f"Failed to load user_needs: {e}")

        except Exception as e:
            error_msg = f"ไม่สามารถเข้าถึงฐานข้อมูลได้: {e}"

    total_cases = len(sos_cases)
    critical_count = sum(1 for c in sos_cases if "CRITICAL" in str(c.get("priority", "")))
    high_count = sum(1 for c in sos_cases if "HIGH" in str(c.get("priority", "")))
    bedridden_count = sum(1 for c in sos_cases if any(k in str(c.get("group_types", "")) for k in ["ผู้ป่วย", "พิการ", "บาดเจ็บ"]))
    needs_count = len([n for n in user_needs if n.get("Status") == "PENDING"])

    # พิกัด GIS
    sos_map_data = []
    for c in sos_cases:
        try:
            lat = float(c.get("latitude", 0))
            lon = float(c.get("longitude", 0))
            if lat != 0 and lon != 0:
                sos_map_data.append({
                    "name": f"{c.get('first_name', '')} {c.get('last_name', '')}",
                    "lat": lat,
                    "lon": lon,
                    "prio": c.get("priority", "NORMAL"),
                    "status": c.get("status", "OPEN")
                })
        except:
            pass

    shelter_map_data = []
    for s in shelters:
        try:
            lat = float(s.get("Latitude", 0))
            lon = float(s.get("Longitude", 0))
            if lat != 0 and lon != 0:
                shelter_map_data.append({
                    "name": s.get("Name", "ศูนย์อพยพ"),
                    "lat": lat,
                    "lon": lon,
                    "status": s.get("Status", "ว่าง")
                })
        except:
            pass

    needs_map_data = []
    for n in user_needs:
        try:
            lat = float(n.get("Latitude", 0))
            lon = float(n.get("Longitude", 0))
            if lat != 0 and lon != 0:
                needs_map_data.append({
                    "category": n.get("Category", "อื่นๆ"),
                    "lat": lat,
                    "lon": lon,
                    "urgency": n.get("Urgency", "ไม่ด่วน"),
                    "status": n.get("Status", "PENDING")
                })
        except:
            pass

    # HTML Template ฉบับปรับปรุง
    html_template = """
    <!DOCTYPE html>
    <html lang="th" class="light">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>COMMAND CENTER — FLOODCARE AI</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css" />
        <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <script>
            tailwind.config = {
                darkMode: 'class',
                theme: {
                    extend: {
                        fontFamily: { prompt: ['Prompt', 'sans-serif'] },
                        colors: {
                            dark: { bg: '#0f172a', card: '#1e293b', border: '#334155', text: '#e2e8f0' }
                        }
                    }
                }
            }
        </script>
        <style>
            body { font-family: 'Prompt', sans-serif; }
            .dark body { background-color: #0f172a; color: #e2e8f0; }
            .dark .bg-white { background-color: #1e293b !important; }
            .dark .bg-gray-50 { background-color: #334155 !important; }
            .dark .bg-\[\#F5F7FB\] { background-color: #0f172a !important; }
            .dark .text-gray-800 { color: #e2e8f0 !important; }
            .dark .text-gray-900 { color: #f1f5f9 !important; }
            .dark .text-gray-600 { color: #94a3b8 !important; }
            .dark .text-gray-500 { color: #64748b !important; }
            .dark .border-gray-200 { border-color: #334155 !important; }
            .dark .border-gray-100 { border-color: #334155 !important; }
            #sidebar { transition: transform 0.3s ease; }
            @media (max-width: 1023px) {
                #sidebar { position: fixed; left: 0; top: 0; height: 100vh; z-index: 50; transform: translateX(-100%); }
                #sidebar.open { transform: translateX(0); }
                #sidebarOverlay { display: none; }
                #sidebarOverlay.open { display: block; }
            }
            .nav-item.active { background-color: #eff6ff; color: #2563eb; }
            .dark .nav-item.active { background-color: #1e3a5f; color: #60a5fa; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
            @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
            .animate-fade-in { animation: fadeIn 0.3s ease; }
        </style>
    </head>
    <body class="bg-[#F5F7FB] text-[#111827] min-h-screen">
        <!-- Mobile Overlay -->
        <div id="sidebarOverlay" class="fixed inset-0 bg-black/50 z-40 hidden lg:hidden" onclick="toggleSidebar()"></div>

        <div class="flex min-h-screen">
            <!-- Sidebar -->
            <aside id="sidebar" class="w-64 bg-white border-r border-gray-200 p-6 flex flex-col justify-between">
                <div>
                    <div class="flex items-center justify-between mb-8">
                        <div class="flex items-center space-x-3">
                            <i class="fa-solid fa-shield-halved text-2xl text-blue-600"></i>
                            <h1 class="text-xl font-bold tracking-wide">FLOODCARE AI</h1>
                        </div>
                        <button onclick="toggleSidebar()" class="lg:hidden text-gray-500">
                            <i class="fa-solid fa-xmark text-xl"></i>
                        </button>
                    </div>
                    <nav class="space-y-1">
                        <a href="#" onclick="switchTab('dashboard', this)" class="nav-item active flex items-center space-x-3 px-4 py-3 rounded-xl font-medium transition">
                            <i class="fa-solid fa-chart-line w-5"></i> <span>Dashboard</span>
                        </a>
                        <a href="#" onclick="switchTab('sos', this)" class="nav-item flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <i class="fa-solid fa-circle-exclamation w-5 text-red-500"></i> <span>SOS Cases</span>
                        </a>
                        <a href="#" onclick="switchTab('needs', this)" class="nav-item flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <i class="fa-solid fa-box-open w-5 text-amber-500"></i> <span>ความต้องการ</span>
                            {% if needs_count > 0 %}
                            <span class="bg-amber-100 text-amber-700 text-xs font-bold px-2 py-0.5 rounded-full">{{ needs_count }}</span>
                            {% endif %}
                        </a>
                        <a href="#" onclick="switchTab('shelters', this)" class="nav-item flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <i class="fa-solid fa-house-chimney-user w-5 text-green-500"></i> <span>Shelters</span>
                        </a>
                        <a href="#" onclick="switchTab('map', this)" class="nav-item flex items-center space-x-3 text-gray-600 hover:bg-gray-50 px-4 py-3 rounded-xl font-medium transition">
                            <i class="fa-solid fa-map w-5 text-purple-500"></i> <span>Flood Map</span>
                        </a>
                    </nav>
                </div>
                <div class="space-y-4">
                    <!-- Dark Mode Toggle -->
                    <button onclick="toggleDarkMode()" class="w-full flex items-center justify-center space-x-2 px-4 py-2 bg-gray-100 hover:bg-gray-200 rounded-xl transition text-sm font-medium">
                        <i id="darkModeIcon" class="fa-solid fa-moon"></i>
                        <span id="darkModeText">Dark Mode</span>
                    </button>
                    <div class="pt-4 border-t border-gray-100 text-xs text-gray-500">
                        <div class="flex items-center space-x-2">
                            <div class="w-2.5 h-2.5 bg-green-500 rounded-full animate-pulse"></div>
                            <span class="font-semibold text-green-600">System Online</span>
                        </div>
                        <p class="mt-1">Last refresh: <span id="lastRefresh">-</span></p>
                    </div>
                </div>
            </aside>

            <!-- Main Content -->
            <div class="flex-1 flex flex-col overflow-hidden">
                <!-- Top Navbar -->
                <header class="h-16 bg-white border-b border-gray-200 px-6 flex items-center justify-between">
                    <div class="flex items-center space-x-4">
                        <button onclick="toggleSidebar()" class="lg:hidden text-gray-600">
                            <i class="fa-solid fa-bars text-xl"></i>
                        </button>
                        <h2 class="text-lg font-bold">ศูนย์ประสานงานระบบอุทกภัยอัจฉริยาย</h2>
                    </div>
                    <div class="flex items-center space-x-4">
                        <span class="text-xs bg-green-100 text-green-700 px-3 py-1.5 rounded-full font-bold">
                            <i class="fa-solid fa-rotate fa-spin mr-1"></i> Auto Refresh
                        </span>
                        <button onclick="manualRefresh()" class="text-sm bg-blue-600 hover:bg-blue-700 text-white px-4 py-1.5 rounded-lg transition">
                            <i class="fa-solid fa-rotate-right mr-1"></i> Refresh
                        </button>
                    </div>
                </header>

                <!-- Scrollable Body -->
                <main class="flex-1 overflow-y-auto p-6" id="mainContent">
                    {% if error_msg %}
                    <div class="bg-red-50 border-l-4 border-red-500 text-red-700 p-4 rounded-xl mb-6 shadow-sm">
                        {{ error_msg }}
                    </div>
                    {% endif %}

                    <!-- ==================== TAB: DASHBOARD ==================== -->
                    <div id="tab-dashboard" class="tab-content active animate-fade-in">
                        <!-- Summary Cards -->
                        <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
                            <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                                <div>
                                    <p class="text-sm text-gray-500">เคสแจ้งเหตุทั้งหมด</p>
                                    <p class="text-3xl font-bold mt-2">{{ total_cases }} <span class="text-lg font-normal">เคส</span></p>
                                </div>
                                <div class="text-2xl bg-blue-50 p-3 rounded-xl"><i class="fa-solid fa-chart-line text-blue-600"></i></div>
                            </div>
                            <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                                <div>
                                    <p class="text-sm font-semibold text-red-500">เคสวิกฤต (Critical)</p>
                                    <p class="text-3xl font-bold text-red-600 mt-2">{{ critical_count }} <span class="text-lg font-normal">เคส</span></p>
                                </div>
                                <div class="text-2xl bg-red-50 p-3 rounded-xl"><i class="fa-solid fa-circle-exclamation text-red-600"></i></div>
                            </div>
                            <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                                <div>
                                    <p class="text-sm font-semibold text-gray-500">เคสความเสี่ยงสูง</p>
                                    <p class="text-3xl font-bold mt-2">{{ high_count }} <span class="text-lg font-normal">เคส</span></p>
                                </div>
                                <div class="text-2xl bg-amber-50 p-3 rounded-xl"><i class="fa-solid fa-triangle-exclamation text-amber-600"></i></div>
                            </div>
                            <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                                <div>
                                    <p class="text-sm font-semibold text-purple-600">ความต้องการรอดำเนินการ</p>
                                    <p class="text-3xl font-bold text-purple-700 mt-2">{{ needs_count }} <span class="text-lg font-normal">ราย</span></p>
                                </div>
                                <div class="text-2xl bg-purple-50 p-3 rounded-xl"><i class="fa-solid fa-box-open text-purple-600"></i></div>
                            </div>
                        </div>

                        <!-- Map Preview -->
                        <div class="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm mb-8">
                            <h3 class="text-base font-bold mb-3 flex items-center space-x-2">
                                <i class="fa-solid fa-map text-purple-500"></i>
                                <span>แผนที่ภูมิสารสนเทศ (Live GIS Crisis Map)</span>
                            </h3>
                            <div id="mapDashboard" class="h-80 rounded-xl border border-gray-200 bg-gray-50"></div>
                        </div>

                        <!-- Recent SOS Table -->
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
                            <div class="flex justify-between items-center pb-4 mb-4 border-b border-gray-100">
                                <h3 class="text-lg font-bold flex items-center space-x-2">
                                    <i class="fa-solid fa-clock-rotate-left text-blue-500"></i>
                                    <span>เคสล่าสุด</span>
                                </h3>
                                <button onclick="switchTab('sos', document.querySelectorAll('.nav-item')[1])" class="text-sm text-blue-600 hover:underline">ดูทั้งหมด</button>
                            </div>
                            <div class="overflow-x-auto">
                                <table class="w-full text-left border-collapse text-sm">
                                    <thead>
                                        <tr class="border-b border-gray-200 text-gray-500 font-semibold">
                                            <th class="py-3 px-2">เคส</th>
                                            <th class="py-3 px-2">ผู้แจ้ง</th>
                                            <th class="py-3 px-2">ระดับ</th>
                                            <th class="py-3 px-2">สถานะ</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for case in sos_cases[:5] %}
                                        <tr class="border-b border-gray-100 hover:bg-gray-50/50 transition">
                                            <td class="py-3 px-2 font-bold text-xs">{{ case.get('request_id', '-') }}</td>
                                            <td class="py-3 px-2">{{ case.get('first_name', '') }} {{ case.get('last_name', '') }}</td>
                                            <td class="py-3 px-2">
                                                {% if 'CRITICAL' in case.get('priority', '') %}
                                                <span class="px-2 py-0.5 text-xs font-bold bg-red-100 text-red-700 rounded-full">CRITICAL</span>
                                                {% elif 'HIGH' in case.get('priority', '') %}
                                                <span class="px-2 py-0.5 text-xs font-bold bg-orange-100 text-orange-700 rounded-full">HIGH</span>
                                                {% else %}
                                                <span class="px-2 py-0.5 text-xs font-bold bg-green-100 text-green-700 rounded-full">NORMAL</span>
                                                {% endif %}
                                            </td>
                                            <td class="py-3 px-2">{{ case.get('status', 'OPEN') }}</td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <!-- ==================== TAB: SOS CASES ==================== -->
                    <div id="tab-sos" class="tab-content animate-fade-in">
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
                            <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 mb-4 border-b border-gray-100 gap-4">
                                <h3 class="text-lg font-bold flex items-center space-x-2">
                                    <i class="fa-solid fa-circle-exclamation text-red-500"></i>
                                    <span>รายการขอรับช่วยเหลือ SOS</span>
                                </h3>
                                <input id="searchSOS" onkeyup="filterTable('searchSOS', 'sosTable')" type="text" placeholder="ค้นหาตามชื่อ/พื้นที่..." class="w-full md:w-64 bg-gray-50 border border-gray-200 text-sm px-4 py-2 rounded-xl focus:outline-none focus:border-blue-500">
                            </div>

                            <div class="overflow-x-auto">
                                <table class="w-full text-left border-collapse text-sm">
                                    <thead>
                                        <tr class="border-b border-gray-200 text-gray-500 font-semibold">
                                            <th class="py-3 px-2">เลขเคส</th>
                                            <th class="py-3 px-2">ผู้ประสบภัย</th>
                                            <th class="py-3 px-2">กลุ่ม/จำนวน</th>
                                            <th class="py-3 px-2">ระดับน้ำ/รายละเอียด</th>
                                            <th class="py-3 px-2">การกู้ภัย</th>
                                        </tr>
                                    </thead>
                                    <tbody id="sosTable">
                                        {% for case in sos_cases %}
                                        <tr class="border-b border-gray-100 hover:bg-gray-50/50 transition">
                                            <td class="py-4 px-2">
                                                <p class="font-bold text-gray-800 text-xs">{{ case.get('request_id', '-') }}</p>
                                                {% if 'CRITICAL' in case.get('priority', '') %}
                                                <span class="inline-block mt-1 px-2 py-0.5 text-[10px] font-bold bg-red-100 text-red-700 rounded-full">CRITICAL</span>
                                                {% elif 'HIGH' in case.get('priority', '') %}
                                                <span class="inline-block mt-1 px-2 py-0.5 text-[10px] font-bold bg-orange-100 text-orange-700 rounded-full">HIGH</span>
                                                {% else %}
                                                <span class="inline-block mt-1 px-2 py-0.5 text-[10px] font-bold bg-green-100 text-green-700 rounded-full">NORMAL</span>
                                                {% endif %}
                                            </td>
                                            <td class="py-4 px-2">
                                                <p class="font-bold">{{ case.get('first_name', '') }} {{ case.get('last_name', '') }}</p>
                                                <p class="text-xs text-blue-600 font-semibold mt-1"><i class="fa-solid fa-phone"></i> {{ case.get('phone', '-') }}</p>
                                            </td>
                                            <td class="py-4 px-2">
                                                <p class="text-gray-700">{{ case.get('group_types', '-') }}</p>
                                                <p class="text-xs text-purple-600 mt-1">เร่งด่วน: {{ case.get('urgency_level', '-') }}</p>
                                            </td>
                                            <td class="py-4 px-2">
                                                <p class="font-semibold text-blue-600 text-xs">{{ case.get('water_level', '-') }}</p>
                                                <p class="text-xs text-gray-500 mt-1 max-w-xs truncate">{{ case.get('note', '-') }}</p>
                                            </td>
                                            <td class="py-4 px-2 space-y-2">
                                                <a href="https://www.google.com/maps/search/?api=1&query={{ case.get('latitude', 0) }},{{ case.get('longitude', 0) }}" target="_blank" class="w-full text-center block px-3 py-1.5 bg-blue-600 hover:bg-blue-700 transition font-bold text-[10px] text-white rounded-lg">
                                                    <i class="fa-solid fa-map"></i> นำทาง
                                                </a>
                                                <div class="flex gap-1">
                                                    {% if case.get('status') == 'OPEN' %}
                                                    <a href="/dashboard/update_status/{{ case.get('request_id') }}/IN_PROGRESS" class="w-1/2 text-center block py-1 bg-amber-500 hover:bg-amber-600 text-white font-bold text-[9px] rounded transition">
                                                        <i class="fa-solid fa-hand"></i> รับเคส
                                                    </a>
                                                    {% elif case.get('status') == 'IN_PROGRESS' %}
                                                    <span class="w-1/2 text-center block py-1 bg-blue-100 text-blue-800 font-bold text-[9px] rounded">กำลังช่วย</span>
                                                    {% else %}
                                                    <span class="w-1/2 text-center block py-1 bg-green-100 text-green-800 font-bold text-[9px] rounded">เสร็จสิ้น</span>
                                                    {% endif %}
                                                    {% if case.get('status') != 'CLOSED' %}
                                                    <a href="/dashboard/update_status/{{ case.get('request_id') }}/CLOSED" class="w-1/2 text-center block py-1 bg-green-600 hover:bg-green-700 text-white font-bold text-[9px] rounded transition">
                                                        <i class="fa-solid fa-check"></i> ปิดเคส
                                                    </a>
                                                    {% endif %}
                                                </div>
                                            </td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <!-- ==================== TAB: USER NEEDS ==================== -->
                    <div id="tab-needs" class="tab-content animate-fade-in">
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
                            <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 mb-4 border-b border-gray-100 gap-4">
                                <h3 class="text-lg font-bold flex items-center space-x-2">
                                    <i class="fa-solid fa-box-open text-amber-500"></i>
                                    <span>รายการความต้องการสิ่งของ</span>
                                </h3>
                                <input id="searchNeeds" onkeyup="filterTable('searchNeeds', 'needsTable')" type="text" placeholder="ค้นหาตามหมวดหมู่/รายละเอียด..." class="w-full md:w-64 bg-gray-50 border border-gray-200 text-sm px-4 py-2 rounded-xl focus:outline-none focus:border-blue-500">
                            </div>

                            <div class="overflow-x-auto">
                                <table class="w-full text-left border-collapse text-sm">
                                    <thead>
                                        <tr class="border-b border-gray-200 text-gray-500 font-semibold">
                                            <th class="py-3 px-2">เวลา</th>
                                            <th class="py-3 px-2">หมวดหมู่</th>
                                            <th class="py-3 px-2">รายละเอียด</th>
                                            <th class="py-3 px-2">ความเร่งด่วน</th>
                                            <th class="py-3 px-2">การดำเนินการ</th>
                                        </tr>
                                    </thead>
                                    <tbody id="needsTable">
                                        {% for need in user_needs %}
                                        <tr class="border-b border-gray-100 hover:bg-gray-50/50 transition">
                                            <td class="py-4 px-2">
                                                <p class="font-bold text-xs">{{ need.get('Timestamp', '-') }}</p>
                                                <p class="text-[10px] text-gray-500">User: {{ need.get('UserID', '-')[:12] }}...</p>
                                            </td>
                                            <td class="py-4 px-2">
                                                <span class="px-2 py-0.5 text-xs font-bold bg-blue-100 text-blue-700 rounded-full">{{ need.get('Category', '-') }}</span>
                                            </td>
                                            <td class="py-4 px-2">
                                                <p class="text-gray-700 max-w-xs">{{ need.get('Details', '-') }}</p>
                                            </td>
                                            <td class="py-4 px-2">
                                                {% if 'ด่วนมาก' in need.get('Urgency', '') %}
                                                <span class="px-2 py-0.5 text-xs font-bold bg-red-100 text-red-700 rounded-full">ด่วนมาก</span>
                                                {% elif 'ปานกลาง' in need.get('Urgency', '') %}
                                                <span class="px-2 py-0.5 text-xs font-bold bg-amber-100 text-amber-700 rounded-full">ปานกลาง</span>
                                                {% else %}
                                                <span class="px-2 py-0.5 text-xs font-bold bg-green-100 text-green-700 rounded-full">ไม่ด่วน</span>
                                                {% endif %}
                                            </td>
                                            <td class="py-4 px-2 space-y-2">
                                                <a href="https://www.google.com/maps/search/?api=1&query={{ need.get('Latitude', 0) }},{{ need.get('Longitude', 0) }}" target="_blank" class="w-full text-center block px-3 py-1.5 bg-blue-600 hover:bg-blue-700 transition font-bold text-[10px] text-white rounded-lg">
                                                    <i class="fa-solid fa-map"></i> นำทาง
                                                </a>
                                                {% if need.get('Status') == 'PENDING' %}
                                                <a href="/dashboard/update_need/{{ need.get('Timestamp') }}/{{ need.get('UserID') }}/COMPLETED" class="w-full text-center block py-1 bg-green-600 hover:bg-green-700 text-white font-bold text-[9px] rounded transition">
                                                    <i class="fa-solid fa-check"></i> ส่งมอบสำเร็จ
                                                </a>
                                                {% else %}
                                                <span class="w-full text-center block py-1 bg-gray-100 text-gray-600 font-bold text-[9px] rounded">เสร็จสิ้นแล้ว</span>
                                                {% endif %}
                                            </td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                                {% if not user_needs %}
                                <div class="text-center py-8 text-gray-500">
                                    <i class="fa-solid fa-box-open text-4xl mb-2"></i>
                                    <p>ยังไม่มีรายการความต้องการ</p>
                                </div>
                                {% endif %}
                            </div>
                        </div>
                    </div>

                    <!-- ==================== TAB: SHELTERS ==================== -->
                    <div id="tab-shelters" class="tab-content animate-fade-in">
                        <div class="flex justify-between items-center mb-6">
                            <h3 class="text-lg font-bold flex items-center space-x-2">
                                <i class="fa-solid fa-house-chimney-user text-green-500"></i>
                                <span>ศูนย์พักพิงในพื้นที่</span>
                            </h3>
                            <button onclick="toggleModal(true)" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 transition text-white text-sm font-bold rounded-xl shadow-sm">
                                <i class="fa-solid fa-plus mr-1"></i> Add Shelter
                            </button>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {% for sh in shelters %}
                            <div class="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
                                <div class="flex justify-between items-start mb-3">
                                    <div>
                                        <p class="font-bold">{{ sh.get('Name', 'ไม่ระบุ') }}</p>
                                        <p class="text-xs text-gray-500 mt-1"><i class="fa-solid fa-location-dot"></i> อ.{{ sh.get('District', '') }} จ.{{ sh.get('Province', '') }}</p>
                                    </div>
                                    <span class="px-2 py-0.5 text-xs font-bold rounded-full {{ 'bg-red-100 text-red-700' if sh.get('Status') == 'เต็ม' else 'bg-green-100 text-green-700' }}">
                                        {{ sh.get('Status', 'ว่าง') }}
                                    </span>
                                </div>
                                <div class="w-full bg-gray-200 rounded-full h-2.5 mb-2">
                                    <div class="bg-blue-600 h-2.5 rounded-full transition-all" style="width: {{ (sh.get('Occupancy', 0)|int / sh.get('Capacity', 100)|int * 100)|round|int if sh.get('Capacity', 100)|int > 0 else 0 }}%"></div>
                                </div>
                                <div class="flex justify-between text-xs text-gray-500 mb-3">
                                    <span>{{ sh.get('Occupancy', 0) }} / {{ sh.get('Capacity', 100) }} คน</span>
                                    <span><i class="fa-solid fa-phone"></i> {{ sh.get('Contact', '-') }}</span>
                                </div>
                                <div class="text-xs text-blue-600 font-semibold pt-2 border-t border-gray-100">
                                    <i class="fa-solid fa-list-check"></i> {{ sh.get('Facilities', 'ไม่มี') }}
                                </div>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

                    <!-- ==================== TAB: MAP ==================== -->
                    <div id="tab-map" class="tab-content animate-fade-in">
                        <div class="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm">
                            <div class="flex justify-between items-center mb-3">
                                <h3 class="text-base font-bold flex items-center space-x-2">
                                    <i class="fa-solid fa-map text-purple-500"></i>
                                    <span>แผนที่ GIS (Filter)</span>
                                </h3>
                                <div class="flex space-x-2">
                                    <button onclick="setMapFilter('all')" class="px-3 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded-lg transition">ทั้งหมด</button>
                                    <button onclick="setMapFilter('critical')" class="px-3 py-1 text-xs bg-red-100 text-red-700 hover:bg-red-200 rounded-lg transition">วิกฤต</button>
                                    <button onclick="setMapFilter('shelter')" class="px-3 py-1 text-xs bg-green-100 text-green-700 hover:bg-green-200 rounded-lg transition">ศูนย์พักพิง</button>
                                    <button onclick="setMapFilter('needs')" class="px-3 py-1 text-xs bg-amber-100 text-amber-700 hover:bg-amber-200 rounded-lg transition">ความต้องการ</button>
                                </div>
                            </div>
                            <div id="mapFull" class="h-[500px] rounded-xl border border-gray-200 bg-gray-50"></div>
                        </div>
                    </div>
                </main>
            </div>
        </div>

        <!-- Add Shelter Modal -->
        <div id="shelterModal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center hidden">
            <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 mx-4 relative border border-gray-200 max-h-[90vh] overflow-y-auto">
                <button onclick="toggleModal(false)" class="absolute top-4 right-4 text-gray-400 hover:text-gray-600 text-xl font-bold">&times;</button>
                <h3 class="text-xl font-bold mb-4"><i class="fa-solid fa-house-chimney-user text-green-500 mr-2"></i>เพิ่มศูนย์พักพิงใหม่</h3>
                <form id="shelterForm" action="/dashboard/add_shelter" method="POST">
                    <div class="space-y-4">
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">รหัส</label>
                                <input type="text" name="sh_id" placeholder="SH004" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">ชื่อ</label>
                                <input type="text" name="sh_name" placeholder="โรงเรียน..." class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">จังหวัด</label>
                                <input type="text" name="sh_province" placeholder="สงขลา" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">อำเภอ</label>
                                <input type="text" name="sh_district" placeholder="หาดใหญ่" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">Lat</label>
                                <input type="text" id="sh_lat" name="sh_lat" placeholder="7.0125" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">Lon</label>
                                <input type="text" id="sh_lon" name="sh_lon" placeholder="100.4560" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">ความจุ</label>
                                <input type="number" name="sh_capacity" value="100" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm" required>
                            </div>
                            <div>
                                <label class="block text-xs font-bold text-gray-600 mb-1">จำนวนปัจจุบัน</label>
                                <input type="number" name="sh_occupancy" value="0" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm" required>
                            </div>
                        </div>
                        <div class="text-xs font-bold text-gray-600 mb-2">สิ่งอำนวยความสะดวก:</div>
                        <div class="grid grid-cols-3 gap-2 text-sm">
                            <label class="flex items-center space-x-1"><input type="checkbox" name="fac_elec" checked> <span>ไฟฟ้า</span></label>
                            <label class="flex items-center space-x-1"><input type="checkbox" name="fac_water" checked> <span>น้ำสะอาด</span></label>
                            <label class="flex items-center space-x-1"><input type="checkbox" name="fac_net"> <span>WiFi</span></label>
                            <label class="flex items-center space-x-1"><input type="checkbox" name="fac_wheelchair"> <span>ผู้พิการ</span></label>
                            <label class="flex items-center space-x-1"><input type="checkbox" name="fac_pet"> <span>สัตว์เลี้ยง</span></label>
                            <label class="flex items-center space-x-1"><input type="checkbox" name="fac_doc"> <span>แพทย์</span></label>
                        </div>
                        <button type="button" onclick="getCurrentLocation()" class="w-full py-2 bg-gray-100 hover:bg-gray-200 text-xs font-bold rounded-xl transition">
                            <i class="fa-solid fa-location-crosshairs mr-1"></i> ดึงพิกัดปัจจุบัน
                        </button>
                        <button type="submit" class="w-full py-3 bg-green-600 hover:bg-green-700 text-sm font-bold text-white rounded-xl transition">
                            <i class="fa-solid fa-check mr-1"></i> บันทึก
                        </button>
                    </div>
                </form>
            </div>
        </div>

        <script>
            // ==================== DATA ====================
            const sosData = {{ sos_map_data|tojson }};
            const shelterData = {{ shelter_map_data|tojson }};
            const needsData = {{ needs_map_data|tojson }};

            // ==================== LEAFLET MAPS ====================
            let mapDashboard, mapFull;
            let sosMarkers = [], shelterMarkers = [], needsMarkers = [];

            function initMap(elementId, zoom = 6) {
                const map = L.map(elementId).setView([13.7563, 100.5018], zoom);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '&copy; OpenStreetMap'
                }).addTo(map);
                return map;
            }

            function addSOSMarkers(map, data, markersArray) {
                data.forEach(c => {
                    if(c.lat && c.lon) {
                        const color = c.prio.includes('CRITICAL') ? '#EF4444' : c.prio.includes('HIGH') ? '#F97316' : '#10B981';
                        const m = L.circleMarker([c.lat, c.lon], {
                            radius: 8, fillColor: color, color: '#fff', weight: 2, opacity: 1, fillOpacity: 0.9
                        }).addTo(map).bindPopup(`<b>SOS:</b> ${c.name}<br>ระดับ: ${c.prio}`);
                        markersArray.push(m);
                    }
                });
            }

            function addShelterMarkers(map, data, markersArray) {
                data.forEach(s => {
                    if(s.lat && s.lon) {
                        const m = L.marker([s.lat, s.lon]).addTo(map)
                            .bindPopup(`<b>ศูนย์พักพิง:</b> ${s.name}<br>สถานะ: ${s.status}`);
                        markersArray.push(m);
                    }
                });
            }

            function addNeedsMarkers(map, data, markersArray) {
                data.forEach(n => {
                    if(n.lat && n.lon) {
                        const color = n.urgency.includes('ด่วนมาก') ? '#EF4444' : n.urgency.includes('ปานกลาง') ? '#FBBF24' : '#10B981';
                        const m = L.circleMarker([n.lat, n.lon], {
                            radius: 6, fillColor: color, color: '#fff', weight: 2, fillOpacity: 0.9
                        }).addTo(map).bindPopup(`<b>ต้องการ:</b> ${n.category}<br>เร่งด่วน: ${n.urgency}`);
                        markersArray.push(m);
                    }
                });
            }

            // Init maps
            document.addEventListener('DOMContentLoaded', () => {
                mapDashboard = initMap('mapDashboard', 6);
                addSOSMarkers(mapDashboard, sosData, sosMarkers);
                addShelterMarkers(mapDashboard, shelterData, shelterMarkers);

                mapFull = initMap('mapFull', 6);
                addSOSMarkers(mapFull, sosData, sosMarkers);
                addShelterMarkers(mapFull, shelterData, shelterMarkers);
                addNeedsMarkers(mapFull, needsData, needsMarkers);

                // Set last refresh time
                document.getElementById('lastRefresh').textContent = new Date().toLocaleTimeString('th-TH');

                // Check URL params for tab
                const urlParams = new URLSearchParams(window.location.search);
                const tab = urlParams.get('tab');
                if(tab === 'needs') {
                    switchTab('needs', document.querySelectorAll('.nav-item')[2]);
                }
            });

            // ==================== MAP FILTER ====================
            function setMapFilter(type) {
                // Clear all
                sosMarkers.forEach(m => mapFull.removeLayer(m));
                shelterMarkers.forEach(m => mapFull.removeLayer(m));
                needsMarkers.forEach(m => mapFull.removeLayer(m));

                if(type === 'all') {
                    sosMarkers.forEach(m => m.addTo(mapFull));
                    shelterMarkers.forEach(m => m.addTo(mapFull));
                    needsMarkers.forEach(m => m.addTo(mapFull));
                } else if(type === 'critical') {
                    sosData.filter(c => c.prio.includes('CRITICAL')).forEach(c => {
                        if(c.lat && c.lon) L.circleMarker([c.lat, c.lon], {radius: 10, fillColor: '#EF4444', color: '#fff', weight: 2, fillOpacity: 0.9}).addTo(mapFull).bindPopup(`<b>CRITICAL:</b> ${c.name}`);
                    });
                } else if(type === 'shelter') {
                    shelterMarkers.forEach(m => m.addTo(mapFull));
                } else if(type === 'needs') {
                    needsMarkers.forEach(m => m.addTo(mapFull));
                }
            }

            // ==================== TAB SWITCHING ====================
            function switchTab(tabName, navEl) {
                // Hide all tabs
                document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
                // Show selected
                document.getElementById('tab-' + tabName).classList.add('active');
                // Update nav
                document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                if(navEl) navEl.classList.add('active');
                // Close sidebar on mobile
                document.getElementById('sidebar').classList.remove('open');
                document.getElementById('sidebarOverlay').classList.remove('open');
                // Resize map if needed
                setTimeout(() => { if(mapFull) mapFull.invalidateSize(); }, 100);
            }

            // ==================== SIDEBAR TOGGLE ====================
            function toggleSidebar() {
                document.getElementById('sidebar').classList.toggle('open');
                document.getElementById('sidebarOverlay').classList.toggle('open');
            }

            // ==================== DARK MODE ====================
            function toggleDarkMode() {
                const html = document.documentElement;
                const icon = document.getElementById('darkModeIcon');
                const text = document.getElementById('darkModeText');
                if(html.classList.contains('dark')) {
                    html.classList.remove('dark');
                    html.classList.add('light');
                    icon.className = 'fa-solid fa-moon';
                    text.textContent = 'Dark Mode';
                    localStorage.setItem('theme', 'light');
                } else {
                    html.classList.remove('light');
                    html.classList.add('dark');
                    icon.className = 'fa-solid fa-sun';
                    text.textContent = 'Light Mode';
                    localStorage.setItem('theme', 'dark');
                }
                setTimeout(() => { if(mapFull) mapFull.invalidateSize(); if(mapDashboard) mapDashboard.invalidateSize(); }, 100);
            }

            // Load theme
            const savedTheme = localStorage.getItem('theme');
            if(savedTheme === 'dark') {
                document.documentElement.classList.remove('light');
                document.documentElement.classList.add('dark');
                document.getElementById('darkModeIcon').className = 'fa-solid fa-sun';
                document.getElementById('darkModeText').textContent = 'Light Mode';
            }

            // ==================== AUTO REFRESH ====================
            function manualRefresh() {
                location.reload();
            }
            // Auto refresh every 2 minutes
            setInterval(() => {
                location.reload();
            }, 120000);

            // ==================== TABLE FILTER ====================
            function filterTable(inputId, tableId) {
                const input = document.getElementById(inputId);
                const filter = input.value.toLowerCase();
                const table = document.getElementById(tableId);
                if(!table) return;
                const tr = table.getElementsByTagName("tr");
                for(let i = 0; i < tr.length; i++) {
                    const tds = tr[i].getElementsByTagName("td");
                    let found = false;
                    for(let j = 0; j < tds.length; j++) {
                        if(tds[j] && tds[j].textContent.toLowerCase().indexOf(filter) > -1) {
                            found = true; break;
                        }
                    }
                    tr[i].style.display = found ? "" : "none";
                }
            }

            // ==================== MODAL ====================
            function toggleModal(open) {
                const modal = document.getElementById("shelterModal");
                modal.classList.toggle("hidden", !open);
            }

            // ==================== GPS ====================
            function getCurrentLocation() {
                if(navigator.geolocation) {
                    navigator.geolocation.getCurrentPosition(pos => {
                        document.getElementById("sh_lat").value = pos.coords.latitude;
                        document.getElementById("sh_lon").value = pos.coords.longitude;
                    }, () => alert("โปรดอนุญาตสิทธิ์เบราว์เซอร์เพื่อดึงพิกัด"));
                } else alert("เบราว์เซอร์ไม่รองรับ GPS");
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(
        html_template,
        sos_cases=sos_cases,
        shelters=shelters,
        user_needs=user_needs,
        error_msg=error_msg,
        total_cases=total_cases,
        critical_count=critical_count,
        high_count=high_count,
        bedridden_count=bedridden_count,
        needs_count=needs_count,
        sos_map_data=sos_map_data,
        shelter_map_data=shelter_map_data,
        needs_map_data=needs_map_data
    )
