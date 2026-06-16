import json
import csv
import io
from flask import Blueprint, render_template_string, request, redirect, jsonify, Response
import bot_config
from datetime import datetime, timedelta

dashboard_bp = Blueprint('dashboard', __name__)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_sheets_data():
    """ดึงข้อมูลทั้งหมดจาก Google Sheets"""
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)

    if not sheets_client:
        return None, "ไม่สามารถเชื่อมต่อ Google Sheets ได้"

    try:
        sheet = sheets_client.open_by_key(clean_sheet_id)

        # ดึง users
        try:
            users_ws = sheet.worksheet("users")
            users_rows = users_ws.get_all_records()
            user_map = {u['user_id']: u for u in users_rows}
        except:
            user_map = {}

        # ดึง SOS cases
        try:
            sos_ws = sheet.worksheet("sos_requests")
            sos_cases = sos_ws.get_all_records()
            for rc in sos_cases:
                u_id = rc.get("user_id")
                u_info = user_map.get(u_id, {})
                rc["first_name"] = u_info.get("first_name", "ผู้แจ้ง")
                rc["last_name"] = u_info.get("last_name", "ทั่วไป")
                rc["phone"] = u_info.get("phone", "-")
            sos_cases.reverse()
        except:
            sos_cases = []

        # ดึง Shelters
        try:
            shelters_ws = sheet.worksheet("Shelters")
            shelters = shelters_ws.get_all_records()
        except:
            shelters = []

        # ดึง User Needs
        try:
            needs_ws = sheet.worksheet("user_needs")
            user_needs = needs_ws.get_all_records()
            for rn in user_needs:
                u_id = rn.get("UserID")
                u_info = user_map.get(u_id, {})
                rn["first_name"] = u_info.get("first_name", "-")
                rn["phone"] = u_info.get("phone", "-")
            user_needs.reverse()
        except:
            user_needs = []

        # ดึงระดับน้ำ
        try:
            water_ws = sheet.worksheet("Water_Levels")
            water_records = water_ws.get_all_records()
        except:
            water_records = []

        return {
            "sos_cases": sos_cases,
            "shelters": shelters,
            "user_needs": user_needs,
            "water_records": water_records,
            "user_map": user_map
        }, None

    except Exception as e:
        return None, str(e)


# =============================================================================
# API ENDPOINTS (สำหรับ Auto-Refresh และ Frontend JS)
# =============================================================================

@dashboard_bp.route("/dashboard/api/data", methods=['GET'])
def api_dashboard_data():
    """ส่งข้อมูล Dashboard ทั้งหมดเป็น JSON"""
    data, error = get_sheets_data()
    if error:
        return jsonify({"error": error}), 500

    sos_cases = data["sos_cases"]
    total_cases = len(sos_cases)
    critical_count = sum(1 for c in sos_cases if "CRITICAL" in str(c.get("priority", "")))
    high_count = sum(1 for c in sos_cases if "HIGH" in str(c.get("priority", "")))
    bedridden_count = sum(1 for c in sos_cases if any(k in str(c.get("group_types", "")) for k in ["ป่วย", "บาดเจ็บ"]))

    # สถิติกลุ่มเปราะบาง (สำหรับ Pie Chart)
    group_stats = {}
    for c in sos_cases:
        gt = str(c.get("group_types", "ผู้ใหญ่ทั่วไป"))
        for g in gt.split(", "):
            g = g.strip()
            if g:
                group_stats[g] = group_stats.get(g, 0) + 1

    # สถิติรายชั่วโมง (สำหรับ Line Chart) - 24 ชั่วโมงล่าสุด
    hourly_stats = {}
    now = datetime.now()
    for i in range(24):
        hour_key = (now - timedelta(hours=i)).strftime("%H:00")
        hourly_stats[hour_key] = 0
    for c in sos_cases:
        try:
            ts = c.get("timestamp", "")
            if ts:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                hour_key = dt.strftime("%H:00")
                if hour_key in hourly_stats:
                    hourly_stats[hour_key] += 1
        except:
            pass

    # Map data
    sos_map_data = []
    for c in sos_cases:
        try:
            sos_map_data.append({
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}",
                "lat": float(c.get("latitude", 0)),
                "lon": float(c.get("longitude", 0)),
                "prio": c.get("priority", "🟢 NORMAL"),
                "case_id": c.get("request_id", ""),
                "status": c.get("status", "OPEN")
            })
        except:
            pass

    shelter_map_data = []
    for s in data["shelters"]:
        try:
            shelter_map_data.append({
                "name": s.get("Name", "ศูนย์อพยพ"),
                "lat": float(s.get("Latitude", 0)),
                "lon": float(s.get("Longitude", 0)),
                "status": s.get("Status", "ว่าง"),
                "capacity": s.get("Capacity", 100),
                "occupancy": s.get("Occupancy", 0)
            })
        except:
            pass

    return jsonify({
        "sos_cases": sos_cases,
        "user_needs": data["user_needs"],
        "shelters": data["shelters"],
        "water_records": data["water_records"],
        "stats": {
            "total_cases": total_cases,
            "critical_count": critical_count,
            "high_count": high_count,
            "bedridden_count": bedridden_count
        },
        "charts": {
            "group_stats": group_stats,
            "hourly_stats": hourly_stats
        },
        "map_data": {
            "sos": sos_map_data,
            "shelters": shelter_map_data
        }
    })


@dashboard_bp.route("/dashboard/api/sync_water", methods=['POST'])
def api_sync_water():
    """เรียกซิงค์ข้อมูลระดับน้ำจาก ThaiWater API"""
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    success = bot_config.sync_water_levels_to_sheets(sheets_client, clean_sheet_id)
    return jsonify({"success": success})


# =============================================================================
# ACTION ENDPOINTS (รับเคส / ปิดเคส / ส่ง LINE)
# =============================================================================

@dashboard_bp.route("/dashboard/accept_case/<request_id>", methods=['POST'])
def accept_case(request_id):
    """รับเคส + ส่ง LINE แจ้งผู้ใช้"""
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    responder_name = request.form.get("responder_name", "ทีมกู้ภัย")
    responder_notes = request.form.get("responder_notes", "")

    if sheets_client:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            sos_ws = sheet.worksheet("sos_requests")
            rows = sos_ws.get_all_records()
            for i, row in enumerate(rows, start=2):
                if str(row.get("request_id")) == request_id:
                    sos_ws.update_cell(i, 13, "IN_PROGRESS")   # status
                    sos_ws.update_cell(i, 14, responder_name)    # responder_name
                    sos_ws.update_cell(i, 15, responder_notes)   # responder_notes
                    sos_ws.update_cell(i, 16, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))  # accepted_at

                    # ส่ง LINE แจ้งผู้ใช้
                    user_id = row.get("user_id")
                    if user_id:
                        message = (
                            f"📢 อัปเดตสถานะการช่วยเหลือ:\n\n"
                            f"ทีมกู้ภัย {responder_name} รับทราบเคสของคุณแล้ว "
                            f"และกำลังเดินทางเข้าพื้นที่พร้อมเรือกู้ชีพ\n\n"
                            f"📝 หมายเหตุ: {responder_notes if responder_notes else 'กำลังดำเนินการ'}\n\n"
                            f"โปรดเตรียมพร้อมและรักษาความปลอดภัยของตนเองครับ"
                        )
                        bot_config.send_line_notification(user_id, message)
                    break
        except Exception as e:
            print(f"Failed to accept case: {e}")

    return jsonify({"success": True})


@dashboard_bp.route("/dashboard/complete_case/<request_id>", methods=['POST'])
def complete_case(request_id):
    """ปิดเคส + ส่ง LINE แจ้งผู้ใช้"""
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)

    if sheets_client:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            sos_ws = sheet.worksheet("sos_requests")
            rows = sos_ws.get_all_records()
            for i, row in enumerate(rows, start=2):
                if str(row.get("request_id")) == request_id:
                    sos_ws.update_cell(i, 13, "CLOSED")
                    sos_ws.update_cell(i, 17, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                    user_id = row.get("user_id")
                    if user_id:
                        message = (
                            "✅ อัปเดตสถานะการช่วยเหลือ:\n\n"
                            "เคสของคุณถูกจัดการเรียบร้อยแล้ว\n"
                            "ทีมกู้ภัยได้ดำเนินการช่วยเหลือเสร็จสิ้น\n\n"
                            "หากยังต้องการความช่วยเหลือเพิ่มเติม สามารถแจ้ง SOS ใหม่ได้ทันทีครับ"
                        )
                        bot_config.send_line_notification(user_id, message)
                    break
        except Exception as e:
            print(f"Failed to complete case: {e}")

    return jsonify({"success": True})


@dashboard_bp.route("/dashboard/complete_need", methods=['POST'])
def complete_need():
    """ปิดรายการความต้องการ"""
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    timestamp = request.form.get("timestamp")
    user_id = request.form.get("user_id")

    success = bot_config.update_need_status(sheets_client, clean_sheet_id, timestamp, user_id, "COMPLETED")
    return jsonify({"success": success})


# =============================================================================
# EXPORT CSV
# =============================================================================

@dashboard_bp.route("/dashboard/export/<data_type>", methods=['GET'])
def export_csv(data_type):
    """Export ข้อมูลเป็น CSV"""
    data, error = get_sheets_data()
    if error:
        return error, 500

    si = io.StringIO()
    writer = csv.writer(si)

    if data_type == "sos":
        cases = data["sos_cases"]
        writer.writerow(["เลขเคส", "ชื่อ", "นามสกุล", "เบอร์โทร", "เวลา", "พิกัด",
                        "กลุ่มผู้ประสบภัย", "ระดับความรุนแรง", "สถานะ", "ผู้รับเคส"])
        for c in cases:
            writer.writerow([
                c.get("request_id", ""),
                c.get("first_name", ""),
                c.get("last_name", ""),
                c.get("phone", ""),
                c.get("timestamp", ""),
                f"{c.get('latitude', '')},{c.get('longitude', '')}",
                c.get("group_types", ""),
                c.get("urgency_level", ""),
                c.get("status", ""),
                c.get("responder_name", "")
            ])
        filename = f"SOS_Cases_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    elif data_type == "needs":
        needs = data["user_needs"]
        writer.writerow(["เวลา", "UserID", "ชื่อ", "เบอร์โทร", "พิกัด", "หมวดหมู่",
                        "รายละเอียด", "ความเร่งด่วน", "สถานะ"])
        for n in needs:
            writer.writerow([
                n.get("Timestamp", ""),
                n.get("UserID", ""),
                n.get("first_name", ""),
                n.get("phone", ""),
                f"{n.get('Latitude', '')},{n.get('Longitude', '')}",
                n.get("Category", ""),
                n.get("Details", ""),
                n.get("Urgency", ""),
                n.get("Status", "")
            ])
        filename = f"User_Needs_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    else:
        return "Invalid export type", 400

    output = si.getvalue()
    si.close()

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# PAGES
# =============================================================================

@dashboard_bp.route("/", methods=['GET'])
def index():
    bot_config.get_sheets_client()
    db_status = f"<span style='color: #10b981; font-weight: bold;'>🟢 {bot_config.LAST_SHEETS_ERROR}</span>" if bot_config.SHEETS_INITIALIZED else f"<span style='color: #ef4444; font-weight: bold;'>🔴 เชื่อมต่อล้มเหลว (สาเหตุ: {bot_config.LAST_SHEETS_ERROR})</span>"

    routes_html = """
    <li style='margin-bottom:8px;'><i class="fa-solid fa-map"></i> <b>index</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/</code> (Methods: GET)</li>
    <li style='margin-bottom:8px;'><i class="fa-solid fa-chart-line"></i> <b>dashboard</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/dashboard</code> (Methods: GET)</li>
    <li style='margin-bottom:8px;'><i class="fa-solid fa-phone"></i> <b>callback</b>: <code style='background:#f1f1f1; padding:3px 8px;'>/callback</code> (Methods: POST)</li>
    """

    return f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>FLOODCARE AI - Diagnostic Panel</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    </head>
    <body style="font-family: sans-serif; padding: 40px; max-width: 650px; margin: auto; border: 1px solid #ccc; border-radius: 12px; margin-top: 50px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
        <h2 style="color: #1E3A8A; margin-bottom: 25px;"><i class="fa-solid fa-shield-halved"></i> FLOODCARE AI Diagnostic Panel</h2>
        <p style="color: #444; line-height: 1.6;">ระบบช่วยวิเคราะห์ความเสถียรและการเชื่อมต่อของเซิร์ฟเวอร์แบบเรียลไทม์:</p>
        <div style="background: #f8f9fa; padding: 15px; border-left: 4px solid #1E3A8A; margin: 20px 0; border-radius: 0 8px 8px 0;">
            <p style="margin: 0; font-weight: bold; color: #1E3A8A;"><i class="fa-solid fa-database"></i> Status การเชื่อม Google Sheets:</p>
            <p style="margin: 5px 0 0 0; font-size: 14px; color: #333;">{db_status}</p>
        </div>
        <p style="color: #666; font-size: 14px; margin-top: 25px;">นี่คือรายชื่อเส้นทางแอป (Active Routes):</p>
        <ul style="list-style: none; padding-left: 0; margin-top: 10px; font-size: 14px;">{routes_html}</ul>
        <hr style="border:0; border-top: 1px solid #eee; margin: 25px 0;">
        <p style="color: #e11d48; font-size: 13px; font-weight: bold; line-height:1.5;">
            <i class="fa-solid fa-triangle-exclamation"></i> คำแนะนำสำหรับการทำตามสเต็ปเชื่อมต่อสำเร็จ:<br>
            1. ตรวจเช็กหน้า Google Sheets ว่าได้กดปุ่มแชร์สิทธิ์เป็น <b>Editor (ผู้แก้ไข)</b> ให้กับอีเมลบอตตัวนี้แล้วหรือยัง:<br>
            <code style="background:#fff1f2; padding:3px 6px; font-size: 12px; border-radius: 4px; display: inline-block; margin-top: 5px;">floodcare-api@floodcare-database.iam.gserviceaccount.com</code><br>
            2. ตรวจสอบว่าแปร GOOGLE_SHEET_ID และ GOOGLE_SERVICE_ACCOUNT_JSON สะกดถูกช่องไม่มีตกหล่นครับ
        </p>
    </body>
    </html>
    """


@dashboard_bp.route("/dashboard", methods=['GET'])
def dashboard():
    return render_template_string(DASHBOARD_HTML)


# =============================================================================
# DASHBOARD HTML TEMPLATE (COMPLETE)
# =============================================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="th" class="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>COMMAND CENTER — FLOODCARE AI</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: { extend: { fontFamily: { prompt: ['Prompt', 'sans-serif'] } } }
        }
    </script>
    <style>
        body { font-family: 'Prompt', sans-serif; }
        .dark body { background-color: #0f172a; color: #e2e8f0; }
        .dark .bg-white { background-color: #1e293b !important; }
        .dark .bg-gray-50 { background-color: #334155 !important; }
        .dark .bg-\\[\\#F5F7FB\\] { background-color: #0f172a !important; }
        .dark .text-gray-800 { color: #e2e8f0 !important; }
        .dark .text-gray-600 { color: #94a3b8 !important; }
        .dark .text-gray-500 { color: #94a3b8 !important; }
        .dark .border-gray-200 { border-color: #475569 !important; }
        .dark .border-gray-100 { border-color: #475569 !important; }
        #mobileMenu { transition: transform 0.3s ease; }
        .nav-item.active { background-color: #eff6ff; color: #2563eb; }
        .dark .nav-item.active { background-color: #1e3a5f; color: #60a5fa; }
        .tab-btn { transition: all 0.2s; }
        .tab-btn.active { border-bottom: 2px solid #2563eb; color: #2563eb; }
    </style>
</head>
<body class="bg-[#F5F7FB] text-[#111827] min-h-screen">
    <div class="flex min-h-screen">
        <!-- Sidebar -->
        <aside id="sidebar" class="fixed inset-y-0 left-0 z-40 w-64 bg-white border-r border-gray-200 p-6 flex flex-col justify-between transform -translate-x-full lg:translate-x-0 lg:static lg:h-auto transition-transform duration-300">
            <div>
                <div class="flex items-center justify-between mb-8">
                    <div class="flex items-center space-x-3">
                        <i class="fa-solid fa-shield-halved text-2xl text-blue-600"></i>
                        <h1 class="text-xl font-bold text-gray-800 tracking-wide">FLOODCARE AI</h1>
                    </div>
                    <button onclick="toggleSidebar()" class="lg:hidden text-gray-500">
                        <i class="fa-solid fa-xmark text-xl"></i>
                    </button>
                </div>
                <nav class="space-y-1">
                    <a href="#" onclick="switchTab('dashboard')" id="nav-dashboard" class="nav-item active flex items-center space-x-3 px-4 py-3 rounded-xl font-medium transition text-gray-600 hover:bg-gray-50">
                        <i class="fa-solid fa-chart-line w-5"></i> <span>Dashboard</span>
                    </a>
                    <a href="#" onclick="switchTab('sos')" id="nav-sos" class="nav-item flex items-center space-x-3 px-4 py-3 rounded-xl font-medium transition text-gray-600 hover:bg-gray-50">
                        <i class="fa-solid fa-circle-exclamation w-5 text-red-500"></i> <span>SOS Cases</span>
                    </a>
                    <a href="#" onclick="switchTab('needs')" id="nav-needs" class="nav-item flex items-center space-x-3 px-4 py-3 rounded-xl font-medium transition text-gray-600 hover:bg-gray-50">
                        <i class="fa-solid fa-box-open w-5 text-amber-500"></i> <span>Needs</span>
                    </a>
                    <a href="#" onclick="switchTab('shelters')" id="nav-shelters" class="nav-item flex items-center space-x-3 px-4 py-3 rounded-xl font-medium transition text-gray-600 hover:bg-gray-50">
                        <i class="fa-solid fa-house-chimney-user w-5 text-green-500"></i> <span>Shelters</span>
                    </a>
                    <a href="#" onclick="switchTab('water')" id="nav-water" class="nav-item flex items-center space-x-3 px-4 py-3 rounded-xl font-medium transition text-gray-600 hover:bg-gray-50">
                        <i class="fa-solid fa-droplet w-5 text-blue-500"></i> <span>Water Levels</span>
                    </a>
                    <a href="#" onclick="switchTab('map')" id="nav-map" class="nav-item flex items-center space-x-3 px-4 py-3 rounded-xl font-medium transition text-gray-600 hover:bg-gray-50">
                        <i class="fa-solid fa-map w-5 text-purple-500"></i> <span>Crisis Map</span>
                    </a>
                </nav>
            </div>
            <div class="space-y-3">
                <button onclick="toggleDarkMode()" class="w-full flex items-center space-x-3 px-4 py-2 rounded-xl font-medium text-gray-600 hover:bg-gray-50 transition border border-gray-200">
                    <i class="fa-solid fa-moon w-5"></i> <span id="darkModeText">Dark Mode</span>
                </button>
                <div class="pt-4 border-t border-gray-100 text-xs text-gray-500">
                    <div class="flex items-center space-x-2">
                        <div class="w-2.5 h-2.5 bg-green-500 rounded-full animate-pulse"></div>
                        <span class="font-semibold text-green-600"><i class="fa-solid fa-circle-check"></i> System Online</span>
                    </div>
                    <p class="mt-1" id="lastRefresh">Last refresh: Just now</p>
                </div>
            </div>
        </aside>

        <!-- Mobile Overlay -->
        <div id="sidebarOverlay" onclick="toggleSidebar()" class="fixed inset-0 bg-black/50 z-30 hidden lg:hidden"></div>

        <!-- Main Content -->
        <div class="flex-1 flex flex-col overflow-hidden min-w-0">
            <!-- Top Navbar -->
            <header class="h-16 bg-white border-b border-gray-200 px-4 lg:px-6 flex items-center justify-between">
                <div class="flex items-center space-x-3">
                    <button onclick="toggleSidebar()" class="lg:hidden text-gray-600 p-2">
                        <i class="fa-solid fa-bars text-xl"></i>
                    </button>
                    <h2 class="text-base lg:text-lg font-bold text-gray-800">ศูนย์ประสานงานระบบอุทกภัยอัจฉริยะ</h2>
                </div>
                <div class="flex items-center space-x-3">
                    <button onclick="manualRefresh()" class="text-sm bg-blue-50 text-blue-600 px-3 py-1.5 rounded-full font-semibold hover:bg-blue-100 transition">
                        <i class="fa-solid fa-rotate"></i> <span class="hidden sm:inline">Refresh</span>
                    </button>
                    <span class="text-xs bg-green-100 text-green-700 px-3 py-1.5 rounded-full font-bold">
                        <i class="fa-solid fa-circle-check"></i> Realtime Sync
                    </span>
                </div>
            </header>

            <!-- Scrollable Body -->
            <main class="flex-1 overflow-y-auto p-4 lg:p-6">
                <!-- Loading State -->
                <div id="loadingState" class="flex items-center justify-center py-20">
                    <div class="text-center">
                        <i class="fa-solid fa-circle-notch fa-spin text-4xl text-blue-600 mb-4"></i>
                        <p class="text-gray-500">กำลังโหลดข้อมูล...</p>
                    </div>
                </div>

                <!-- Error State -->
                <div id="errorState" class="hidden bg-red-50 border-l-4 border-red-500 text-red-700 p-4 rounded-xl mb-6 shadow-sm">
                    <p id="errorMessage"></p>
                </div>

                <!-- Content -->
                <div id="dashboardContent" class="hidden">
                    <!-- Summary Cards -->
                    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 lg:gap-6 mb-6 lg:mb-8">
                        <div class="bg-white p-4 lg:p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-xs lg:text-sm text-gray-500">เคสแจ้งเหตุทั้งหมด</p>
                                <p class="text-2xl lg:text-3xl font-bold text-gray-900 mt-2" id="statTotal">0</p>
                            </div>
                            <span class="text-2xl lg:text-3xl bg-blue-50 p-2 lg:p-3 rounded-xl"><i class="fa-solid fa-chart-line text-blue-600"></i></span>
                        </div>
                        <div class="bg-white p-4 lg:p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-xs lg:text-sm font-semibold text-red-500">เคสวิกฤต</p>
                                <p class="text-2xl lg:text-3xl font-bold text-red-600 mt-2" id="statCritical">0</p>
                            </div>
                            <span class="text-2xl lg:text-3xl bg-red-50 p-2 lg:p-3 rounded-xl"><i class="fa-solid fa-circle-exclamation text-red-600"></i></span>
                        </div>
                        <div class="bg-white p-4 lg:p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-xs lg:text-sm text-gray-500">ความต้องการรอดำเนินการ</p>
                                <p class="text-2xl lg:text-3xl font-bold text-gray-900 mt-2" id="statNeeds">0</p>
                            </div>
                            <span class="text-2xl lg:text-3xl bg-amber-50 p-2 lg:p-3 rounded-xl"><i class="fa-solid fa-box-open text-amber-600"></i></span>
                        </div>
                        <div class="bg-white p-4 lg:p-6 rounded-2xl border border-gray-200 shadow-sm flex items-center justify-between">
                            <div>
                                <p class="text-xs lg:text-sm font-semibold text-purple-600">ศูนย์พักพิง</p>
                                <p class="text-2xl lg:text-3xl font-bold text-purple-700 mt-2" id="statShelters">0</p>
                            </div>
                            <span class="text-2xl lg:text-3xl bg-purple-50 p-2 lg:p-3 rounded-xl"><i class="fa-solid fa-house-chimney-user text-purple-600"></i></span>
                        </div>
                    </div>

                    <!-- Charts Row -->
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 lg:gap-6 mb-6 lg:mb-8">
                        <div class="bg-white p-4 lg:p-6 rounded-2xl border border-gray-200 shadow-sm">
                            <h3 class="text-sm lg:text-base font-bold text-gray-800 mb-4"><i class="fa-solid fa-chart-pie text-blue-600 mr-2"></i>สัดส่วนกลุ่มเปราะบาง</h3>
                            <canvas id="groupChart" height="200"></canvas>
                        </div>
                        <div class="bg-white p-4 lg:p-6 rounded-2xl border border-gray-200 shadow-sm">
                            <h3 class="text-sm lg:text-base font-bold text-gray-800 mb-4"><i class="fa-solid fa-chart-area text-blue-600 mr-2"></i>การแจ้งเหตุรายชั่วโมง</h3>
                            <canvas id="hourlyChart" height="200"></canvas>
                        </div>
                    </div>

                    <!-- Action Buttons -->
                    <div class="flex flex-wrap gap-3 mb-6">
                        <button onclick="syncWaterData()" class="flex items-center space-x-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold rounded-xl transition shadow-sm">
                            <i class="fa-solid fa-droplet"></i> <span>Sync ระดับน้ำ</span>
                        </button>
                        <a href="/dashboard/export/sos" class="flex items-center space-x-2 px-4 py-2 bg-gray-600 hover:bg-gray-700 text-white text-sm font-bold rounded-xl transition shadow-sm">
                            <i class="fa-solid fa-file-csv"></i> <span>Export SOS</span>
                        </a>
                        <a href="/dashboard/export/needs" class="flex items-center space-x-2 px-4 py-2 bg-gray-600 hover:bg-gray-700 text-white text-sm font-bold rounded-xl transition shadow-sm">
                            <i class="fa-solid fa-file-csv"></i> <span>Export Needs</span>
                        </a>
                    </div>

                    <!-- Tabs Navigation -->
                    <div class="flex border-b border-gray-200 mb-6 overflow-x-auto">
                        <button onclick="switchContentTab('sos')" id="tab-sos" class="tab-btn active px-4 py-3 text-sm font-semibold whitespace-nowrap">
                            <i class="fa-solid fa-circle-exclamation mr-1"></i> SOS Cases
                        </button>
                        <button onclick="switchContentTab('needs')" id="tab-needs" class="tab-btn px-4 py-3 text-sm font-semibold whitespace-nowrap">
                            <i class="fa-solid fa-box-open mr-1"></i> Needs
                        </button>
                        <button onclick="switchContentTab('shelters')" id="tab-shelters" class="tab-btn px-4 py-3 text-sm font-semibold whitespace-nowrap">
                            <i class="fa-solid fa-house-chimney-user mr-1"></i> Shelters
                        </button>
                        <button onclick="switchContentTab('water')" id="tab-water" class="tab-btn px-4 py-3 text-sm font-semibold whitespace-nowrap">
                            <i class="fa-solid fa-droplet mr-1"></i> Water Levels
                        </button>
                    </div>

                    <!-- SOS Cases Tab -->
                    <div id="content-sos" class="content-tab">
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-4 lg:p-6">
                            <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 mb-4 border-b border-gray-100 gap-4">
                                <h3 class="text-base lg:text-lg font-bold text-gray-800">
                                    <i class="fa-solid fa-clipboard-list mr-2"></i>รายการขอรับช่วยเหลือ SOS
                                </h3>
                                <input id="searchSOS" onkeyup="filterTable('sosTable', 'searchSOS')" type="text" placeholder="ค้นหาตามชื่อ/พื้นที่..." class="w-full md:w-64 bg-gray-50 border border-gray-200 text-sm px-4 py-2 rounded-xl text-gray-800 focus:outline-none focus:border-blue-500">
                            </div>
                            <div class="overflow-x-auto" id="sosTableContainer">
                                <p class="text-gray-500 text-center py-8">ไม่มีข้อมูล SOS</p>
                            </div>
                        </div>
                    </div>

                    <!-- Needs Tab -->
                    <div id="content-needs" class="content-tab hidden">
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-4 lg:p-6">
                            <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-4 mb-4 border-b border-gray-100 gap-4">
                                <h3 class="text-base lg:text-lg font-bold text-gray-800">
                                    <i class="fa-solid fa-box-open mr-2"></i>รายการความต้องการสิ่งของ
                                </h3>
                                <input id="searchNeeds" onkeyup="filterTable('needsTable', 'searchNeeds')" type="text" placeholder="ค้นหาตามหมวดหมู่/รายละเอียด..." class="w-full md:w-64 bg-gray-50 border border-gray-200 text-sm px-4 py-2 rounded-xl text-gray-800 focus:outline-none focus:border-blue-500">
                            </div>
                            <div class="overflow-x-auto" id="needsTableContainer">
                                <p class="text-gray-500 text-center py-8">ไม่มีข้อมูลความต้องการ</p>
                            </div>
                        </div>
                    </div>

                    <!-- Shelters Tab -->
                    <div id="content-shelters" class="content-tab hidden">
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-4 lg:p-6">
                            <div class="flex justify-between items-center mb-6">
                                <h3 class="text-base lg:text-lg font-bold text-gray-800">
                                    <i class="fa-solid fa-house-chimney-user mr-2"></i>ศูนย์พักพิงในพื้นที่
                                </h3>
                                <button onclick="toggleShelterModal(true)" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 transition text-white text-xs font-bold rounded-xl shadow-sm">
                                    <i class="fa-solid fa-plus"></i> Add Shelter
                                </button>
                            </div>
                            <div id="sheltersContainer" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <p class="text-gray-500 text-center py-8 col-span-2">ไม่มีข้อมูลศูนย์พักพิง</p>
                            </div>
                        </div>
                    </div>

                    <!-- Water Levels Tab -->
                    <div id="content-water" class="content-tab hidden">
                        <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-4 lg:p-6">
                            <div class="flex justify-between items-center mb-6">
                                <h3 class="text-base lg:text-lg font-bold text-gray-800">
                                    <i class="fa-solid fa-droplet mr-2"></i>ระดับน้ำ ThaiWater
                                </h3>
                                <span id="lastWaterSync" class="text-xs text-gray-500">-</span>
                            </div>
                            <div class="overflow-x-auto" id="waterTableContainer">
                                <p class="text-gray-500 text-center py-8">ไม่มีข้อมูลระดับน้ำ</p>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Crisis Map Full Page -->
                <div id="mapContent" class="hidden">
                    <div class="bg-white rounded-2xl border border-gray-200 shadow-sm p-4 lg:p-6 mb-6">
                        <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-4 gap-4">
                            <h3 class="text-base lg:text-lg font-bold text-gray-800">
                                <i class="fa-solid fa-map mr-2"></i>แผนที่วิกฤติภูมิสารสนเทศ (Crisis Map)
                            </h3>
                            <div class="flex flex-wrap gap-2">
                                <button onclick="toggleMapLayer('all')" class="px-3 py-1 text-xs font-bold bg-blue-100 text-blue-700 rounded-full hover:bg-blue-200 transition">
                                    <i class="fa-solid fa-layer-group"></i> ทั้งหมด
                                </button>
                                <button onclick="toggleMapLayer('critical')" class="px-3 py-1 text-xs font-bold bg-red-100 text-red-700 rounded-full hover:bg-red-200 transition">
                                    <i class="fa-solid fa-circle-exclamation"></i> วิกฤต
                                </button>
                                <button onclick="toggleMapLayer('shelter')" class="px-3 py-1 text-xs font-bold bg-green-100 text-green-700 rounded-full hover:bg-green-200 transition">
                                    <i class="fa-solid fa-house-chimney-user"></i> พักพิง
                                </button>
                                <button onclick="toggleHeatmap()" class="px-3 py-1 text-xs font-bold bg-purple-100 text-purple-700 rounded-full hover:bg-purple-200 transition">
                                    <i class="fa-solid fa-fire"></i> Heatmap
                                </button>
                            </div>
                        </div>
                        <div id="crisisMap" class="h-96 rounded-xl border border-gray-200"></div>
                    </div>
                </div>
            </main>
        </div>
    </div>

    <!-- Accept Case Modal -->
    <div id="acceptModal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center hidden">
        <div class="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 mx-4 relative border border-gray-200">
            <button onclick="toggleAcceptModal(false)" class="absolute top-4 right-4 text-gray-400 hover:text-gray-600 text-xl font-bold"><i class="fa-solid fa-xmark"></i></button>
            <h3 class="text-lg font-bold text-gray-800 mb-4"><i class="fa-solid fa-hand-holding-medical text-blue-600 mr-2"></i>รับเคส</h3>
            <form id="acceptForm" onsubmit="submitAcceptCase(event)">
                <input type="hidden" id="acceptCaseId">
                <div class="space-y-4">
                    <div>
                        <label class="block text-xs font-bold text-gray-600 mb-1">ชื่อผู้รับเคส / หน่วยงาน</label>
                        <input type="text" id="acceptResponderName" placeholder="เช่น ทีมกู้ภัยมูลนิธิร่วมกตัญญู" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-gray-600 mb-1">หมายเหตุ</label>
                        <textarea id="acceptResponderNotes" placeholder="เช่น กำลังเดินทางด้วยเรือท้องแบน" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" rows="2"></textarea>
                    </div>
                    <button type="submit" class="w-full py-3 bg-blue-600 hover:bg-blue-700 text-sm font-bold text-white rounded-xl transition">
                        <i class="fa-solid fa-check"></i> ยืนยันรับเคส
                    </button>
                </div>
            </form>
        </div>
    </div>

    <!-- Add Shelter Modal -->
    <div id="shelterModal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center hidden">
        <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6 mx-4 relative border border-gray-200 max-h-[90vh] overflow-y-auto">
            <button onclick="toggleShelterModal(false)" class="absolute top-4 right-4 text-gray-400 hover:text-gray-600 text-xl font-bold"><i class="fa-solid fa-xmark"></i></button>
            <h3 class="text-lg font-bold text-gray-800 mb-4"><i class="fa-solid fa-house-chimney-user text-green-600 mr-2"></i>เพิ่มศูนย์พักพิงใหม่</h3>
            <form action="/dashboard/add_shelter" method="POST">
                <div class="space-y-4">
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-gray-600 mb-1">รหัสศูนย์พักพิง</label>
                            <input type="text" name="sh_id" placeholder="SH004" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-gray-600 mb-1">ชื่อสถานที่</label>
                            <input type="text" name="sh_name" placeholder="โรงเรียนกู้ภัยอุทกภัย" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
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
                            <label class="block text-xs font-bold text-gray-600 mb-1">ละติจูด</label>
                            <input type="text" id="sh_lat" name="sh_lat" placeholder="7.0125" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-gray-600 mb-1">ลองจิจูด</label>
                            <input type="text" id="sh_lon" name="sh_lon" placeholder="100.4560" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                    </div>
                    <button type="button" onclick="getCurrentLocation()" class="w-full py-2 bg-slate-100 hover:bg-slate-200 text-xs font-bold rounded-xl text-slate-700 transition">
                        <i class="fa-solid fa-location-crosshairs"></i> ดึงพิกัดจากตำแหน่งปัจจุบัน
                    </button>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-gray-600 mb-1">ความจุ (คน)</label>
                            <input type="number" name="sh_capacity" value="100" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-gray-600 mb-1">จำนวนผู้เข้าพัก</label>
                            <input type="number" name="sh_occupancy" value="0" class="w-full bg-gray-50 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-blue-500" required>
                        </div>
                    </div>
                    <div class="grid grid-cols-3 gap-3 text-sm">
                        <label class="flex items-center space-x-2"><input type="checkbox" name="fac_elec" checked> <span>ไฟฟ้า</span></label>
                        <label class="flex items-center space-x-2"><input type="checkbox" name="fac_water" checked> <span>น้ำสะอาด</span></label>
                        <label class="flex items-center space-x-2"><input type="checkbox" name="fac_net"> <span>อินเทอร์เน็ต</span></label>
                        <label class="flex items-center space-x-2"><input type="checkbox" name="fac_wheelchair"> <span>ผู้พิการ</span></label>
                        <label class="flex items-center space-x-2"><input type="checkbox" name="fac_pet"> <span>สัตว์เลี้ยง</span></label>
                        <label class="flex items-center space-x-2"><input type="checkbox" name="fac_doc"> <span>แพทย์</span></label>
                    </div>
                    <button type="submit" class="w-full py-3 bg-green-600 hover:bg-green-700 text-sm font-bold text-white rounded-xl transition">
                        <i class="fa-solid fa-save"></i> บันทึกข้อมูล
                    </button>
                </div>
            </form>
        </div>
    </div>

    <script>
        // ========== GLOBAL STATE ==========
        let dashboardData = null;
        let groupChartInstance = null;
        let hourlyChartInstance = null;
        let crisisMap = null;
        let heatLayer = null;
        let currentLayer = 'all';
        let autoRefreshInterval = null;

        // ========== INITIALIZATION ==========
        document.addEventListener('DOMContentLoaded', function() {
            loadDashboardData();
            startAutoRefresh();
        });

        function startAutoRefresh() {
            if (autoRefreshInterval) clearInterval(autoRefreshInterval);
            autoRefreshInterval = setInterval(loadDashboardData, 120000); // 2 นาที
        }

        // ========== DATA LOADING ==========
        async function loadDashboardData() {
            try {
                const response = await fetch('/dashboard/api/data');
                if (!response.ok) throw new Error('Failed to load data');
                dashboardData = await response.json();

                document.getElementById('loadingState').classList.add('hidden');
                document.getElementById('errorState').classList.add('hidden');
                document.getElementById('dashboardContent').classList.remove('hidden');

                updateStats();
                renderSOSTable();
                renderNeedsTable();
                renderShelters();
                renderWaterTable();
                renderCharts();
                if (crisisMap) updateCrisisMap();

                document.getElementById('lastRefresh').textContent = 'Last refresh: ' + new Date().toLocaleTimeString('th-TH');
            } catch (error) {
                console.error('Error loading data:', error);
                document.getElementById('loadingState').classList.add('hidden');
                document.getElementById('errorState').classList.remove('hidden');
                document.getElementById('errorMessage').textContent = 'ไม่สามารถโหลดข้อมูลได้: ' + error.message;
            }
        }

        function manualRefresh() {
            document.getElementById('loadingState').classList.remove('hidden');
            document.getElementById('dashboardContent').classList.add('hidden');
            loadDashboardData();
        }

        async function syncWaterData() {
            const btn = event.target.closest('button');
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> กำลังซิงค์...';
            try {
                const response = await fetch('/dashboard/api/sync_water', { method: 'POST' });
                const result = await response.json();
                if (result.success) {
                    alert('ซิงค์ข้อมูลระดับน้ำสำเร็จ!');
                    loadDashboardData();
                } else {
                    alert('ซิงค์ไม่สำเร็จ');
                }
            } catch (e) {
                alert('ข้อผิดพลาด: ' + e.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-droplet"></i> <span>Sync ระดับน้ำ</span>';
            }
        }

        // ========== STATS ==========
        function updateStats() {
            if (!dashboardData) return;
            document.getElementById('statTotal').textContent = dashboardData.stats.total_cases;
            document.getElementById('statCritical').textContent = dashboardData.stats.critical_count;
            document.getElementById('statNeeds').textContent = dashboardData.user_needs.filter(n => n.Status === 'PENDING').length;
            document.getElementById('statShelters').textContent = dashboardData.shelters.length;
        }

        // ========== RENDER TABLES ==========
        function renderSOSTable() {
            const container = document.getElementById('sosTableContainer');
            const cases = dashboardData.sos_cases;

            if (!cases || cases.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-center py-8">ไม่มีข้อมูล SOS</p>';
                return;
            }

            let html = `
                <table class="w-full text-left border-collapse text-sm" id="sosTable">
                    <thead>
                        <tr class="border-b border-gray-200 text-gray-500 font-semibold">
                            <th class="py-3 px-2">เลขเคส</th>
                            <th class="py-3 px-2">ผู้ประสบภัย</th>
                            <th class="py-3 px-2">ความเร่งด่วน</th>
                            <th class="py-3 px-2">กลุ่ม/รายละเอียด</th>
                            <th class="py-3 px-2">สถานะ</th>
                            <th class="py-3 px-2">การดำเนินการ</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            cases.forEach(c => {
                const prio = c.priority || '';
                let prioBadge = '';
                if (prio.includes('CRITICAL')) {
                    prioBadge = '<span class="inline-block px-2 py-0.5 text-[10px] font-bold bg-red-100 text-red-700 rounded-full">🔴 CRITICAL</span>';
                } else if (prio.includes('HIGH')) {
                    prioBadge = '<span class="inline-block px-2 py-0.5 text-[10px] font-bold bg-orange-100 text-orange-700 rounded-full">🟠 HIGH</span>';
                } else {
                    prioBadge = '<span class="inline-block px-2 py-0.5 text-[10px] font-bold bg-green-100 text-green-700 rounded-full">🟢 NORMAL</span>';
                }

                let statusBadge = '';
                const st = c.status || 'OPEN';
                if (st === 'OPEN') statusBadge = '<span class="px-2 py-0.5 text-[10px] font-bold bg-gray-100 text-gray-700 rounded-full">รอดำเนินการ</span>';
                else if (st === 'IN_PROGRESS') statusBadge = '<span class="px-2 py-0.5 text-[10px] font-bold bg-blue-100 text-blue-700 rounded-full">กำลังช่วย</span>';
                else statusBadge = '<span class="px-2 py-0.5 text-[10px] font-bold bg-green-100 text-green-700 rounded-full">เสร็จสิ้น</span>';

                let actions = '';
                if (st === 'OPEN') {
                    actions = `<button onclick="openAcceptModal('${c.request_id}')" class="px-3 py-1 bg-amber-500 hover:bg-amber-600 text-white font-bold text-[10px] rounded transition"><i class="fa-solid fa-hand"></i> รับเคส</button>`;
                } else if (st === 'IN_PROGRESS') {
                    actions = `<button onclick="completeCase('${c.request_id}')" class="px-3 py-1 bg-green-600 hover:bg-green-700 text-white font-bold text-[10px] rounded transition"><i class="fa-solid fa-check"></i> ส่งมอบสำเร็จ</button>`;
                } else {
                    actions = '<span class="text-green-600 text-[10px]"><i class="fa-solid fa-check-circle"></i> เสร็จสิ้น</span>';
                }

                html += `
                    <tr class="border-b border-gray-100 hover:bg-gray-50/50 transition">
                        <td class="py-3 px-2">
                            <p class="font-bold text-gray-800 text-xs">${c.request_id || ''}</p>
                            ${prioBadge}
                        </td>
                        <td class="py-3 px-2">
                            <p class="font-bold text-gray-800">${c.first_name || ''} ${c.last_name || ''}</p>
                            <p class="text-xs text-blue-600 font-semibold mt-1"><i class="fa-solid fa-phone"></i> ${c.phone || '-'}</p>
                            <a href="https://www.google.com/maps/search/?api=1&query=${c.latitude || 0},${c.longitude || 0}" target="_blank" class="text-[10px] text-blue-500 hover:underline"><i class="fa-solid fa-map-marker-alt"></i> ดูแผนที่</a>
                        </td>
                        <td class="py-3 px-2">
                            <p class="text-gray-700 font-semibold">${c.urgency_level || '-'}</p>
                        </td>
                        <td class="py-3 px-2">
                            <p class="text-xs text-purple-600 font-semibold">${c.group_types || '-'}</p>
                            <p class="text-xs text-gray-500 mt-1 max-w-xs truncate">${c.note || '-'}</p>
                        </td>
                        <td class="py-3 px-2">${statusBadge}</td>
                        <td class="py-3 px-2 space-y-1">${actions}</td>
                    </tr>
                `;
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        }

        function renderNeedsTable() {
            const container = document.getElementById('needsTableContainer');
            const needs = dashboardData.user_needs;

            if (!needs || needs.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-center py-8">ไม่มีข้อมูลความต้องการ</p>';
                return;
            }

            let html = `
                <table class="w-full text-left border-collapse text-sm" id="needsTable">
                    <thead>
                        <tr class="border-b border-gray-200 text-gray-500 font-semibold">
                            <th class="py-3 px-2">เวลา</th>
                            <th class="py-3 px-2">ผู้ขอ</th>
                            <th class="py-3 px-2">หมวดหมู่</th>
                            <th class="py-3 px-2">รายละเอียด</th>
                            <th class="py-3 px-2">ความเร่งด่วน</th>
                            <th class="py-3 px-2">การดำเนินการ</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            needs.forEach(n => {
                const urgency = n.Urgency || '';
                let urgencyBadge = '';
                if (urgency.includes('ด่วนมาก')) urgencyBadge = '<span class="px-2 py-0.5 text-[10px] font-bold bg-red-100 text-red-700 rounded-full">🔴 ด่วนมาก</span>';
                else if (urgency.includes('ปานกลาง')) urgencyBadge = '<span class="px-2 py-0.5 text-[10px] font-bold bg-yellow-100 text-yellow-700 rounded-full">🟡 ปานกลาง</span>';
                else urgencyBadge = '<span class="px-2 py-0.5 text-[10px] font-bold bg-green-100 text-green-700 rounded-full">🟢 ไม่ด่วน</span>';

                const status = n.Status || 'PENDING';
                let actionBtn = '';
                if (status === 'PENDING') {
                    actionBtn = `<button onclick="completeNeed('${n.Timestamp}', '${n.UserID}')" class="px-3 py-1 bg-green-600 hover:bg-green-700 text-white font-bold text-[10px] rounded transition"><i class="fa-solid fa-check"></i> ส่งมอบแล้ว</button>`;
                } else {
                    actionBtn = '<span class="text-green-600 text-[10px]"><i class="fa-solid fa-check-circle"></i> เสร็จสิ้น</span>';
                }

                html += `
                    <tr class="border-b border-gray-100 hover:bg-gray-50/50 transition">
                        <td class="py-3 px-2 text-xs text-gray-500">${n.Timestamp || ''}</td>
                        <td class="py-3 px-2">
                            <p class="font-bold text-gray-800 text-xs">${n.first_name || '-'}</p>
                            <p class="text-xs text-blue-600"><i class="fa-solid fa-phone"></i> ${n.phone || '-'}</p>
                        </td>
                        <td class="py-3 px-2">
                            <span class="px-2 py-0.5 text-[10px] font-bold bg-blue-100 text-blue-700 rounded-full">${n.Category || '-'}</span>
                        </td>
                        <td class="py-3 px-2 text-xs text-gray-700 max-w-xs">${n.Details || '-'}</td>
                        <td class="py-3 px-2">${urgencyBadge}</td>
                        <td class="py-3 px-2">${actionBtn}</td>
                    </tr>
                `;
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        }

        function renderShelters() {
            const container = document.getElementById('sheltersContainer');
            const shelters = dashboardData.shelters;

            if (!shelters || shelters.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-center py-8 col-span-2">ไม่มีข้อมูลศูนย์พักพิง</p>';
                return;
            }

            let html = '';
            shelters.forEach(sh => {
                const cap = parseInt(sh.Capacity) || 100;
                const occ = parseInt(sh.Occupancy) || 0;
                const pct = cap > 0 ? Math.round((occ / cap) * 100) : 0;
                let statusColor = 'bg-green-100 text-green-700';
                if (sh.Status === 'เต็ม') statusColor = 'bg-red-100 text-red-700';
                else if (occ >= cap * 0.8) statusColor = 'bg-yellow-100 text-yellow-700';

                html += `
                    <div class="bg-gray-50 p-4 rounded-xl border border-gray-200/60">
                        <div class="flex justify-between items-start mb-2">
                            <div>
                                <p class="font-bold text-gray-800 text-sm">${sh.Name || 'ไม่ระบุ'}</p>
                                <p class="text-xs text-gray-500 mt-1"><i class="fa-solid fa-location-dot"></i> อ.${sh.District || ''} จ.${sh.Province || ''}</p>
                            </div>
                            <span class="px-2 py-0.5 text-xs font-bold rounded-full ${statusColor}">${sh.Status || 'ว่าง'}</span>
                        </div>
                        <div class="w-full bg-gray-200 rounded-full h-2 mt-3">
                            <div class="bg-blue-600 h-2 rounded-full transition-all" style="width: ${pct}%"></div>
                        </div>
                        <div class="flex justify-between items-center text-xs text-gray-500 mt-2">
                            <span>พักอยู่: ${occ} / ${cap} คน (${pct}%)</span>
                        </div>
                        <div class="text-xs text-blue-600 font-semibold mt-2 pt-2 border-t border-gray-100">
                            <i class="fa-solid fa-toolbox"></i> ${sh.Facilities || 'ไม่มีข้อมูล'}
                        </div>
                    </div>
                `;
            });
            container.innerHTML = html;
        }

        function renderWaterTable() {
            const container = document.getElementById('waterTableContainer');
            const records = dashboardData.water_records;

            if (!records || records.length === 0) {
                container.innerHTML = '<p class="text-gray-500 text-center py-8">ไม่มีข้อมูลระดับน้ำ</p>';
                return;
            }

            // แสดงเฉพาะสถานีที่มีข้อมูลระดับน้ำ และเรียงตามสถานการณ์ (วิกฤตก่อน)
            let filtered = records.filter(r => r.WaterLevel && r.WaterLevel !== '-');
            filtered.sort((a, b) => {
                const order = { 'วิกฤต': 0, 'เฝ้าระวัง': 1, 'ปกติ': 2 };
                return (order[a.Situation] || 3) - (order[b.Situation] || 3);
            });

            let html = `
                <table class="w-full text-left border-collapse text-sm">
                    <thead>
                        <tr class="border-b border-gray-200 text-gray-500 font-semibold">
                            <th class="py-3 px-2">สถานี</th>
                            <th class="py-3 px-2">จังหวัด</th>
                            <th class="py-3 px-2">ระดับน้ำ (ม.)</th>
                            <th class="py-3 px-2">ระดับตลิ่ง (ม.)</th>
                            <th class="py-3 px-2">สถานการณ์</th>
                            <th class="py-3 px-2">แนวโน้ม</th>
                            <th class="py-3 px-2">อัปเดตล่าสุด</th>
                        </tr>
                    </thead>
                    <tbody>
            `;

            filtered.slice(0, 50).forEach(r => {  // แสดง 50 สถานีแรก
                let sitColor = 'text-green-600';
                let sitBg = 'bg-green-100';
                if (r.Situation === 'วิกฤต') { sitColor = 'text-red-600'; sitBg = 'bg-red-100'; }
                else if (r.Situation === 'เฝ้าระวัง') { sitColor = 'text-yellow-600'; sitBg = 'bg-yellow-100'; }

                let trendIcon = '<i class="fa-solid fa-minus text-gray-400"></i>';
                if (r.Trend === 'เพิ่มขึ้น') trendIcon = '<i class="fa-solid fa-arrow-trend-up text-red-500"></i>';
                else if (r.Trend === 'ลดลง') trendIcon = '<i class="fa-solid fa-arrow-trend-down text-green-500"></i>';

                html += `
                    <tr class="border-b border-gray-100 hover:bg-gray-50/50 transition">
                        <td class="py-3 px-2 font-semibold text-gray-800 text-xs">${r.Name || ''}</td>
                        <td class="py-3 px-2 text-xs text-gray-600">${r.Location || ''}</td>
                        <td class="py-3 px-2 font-bold text-blue-600">${r.WaterLevel || '-'}</td>
                        <td class="py-3 px-2 text-gray-600">${r.BankLevel || '-'}</td>
                        <td class="py-3 px-2"><span class="px-2 py-0.5 text-[10px] font-bold ${sitBg} ${sitColor} rounded-full">${r.Situation || '-'}</span></td>
                        <td class="py-3 px-2">${trendIcon} <span class="text-xs text-gray-600">${r.Trend || 'คงที่'}</span></td>
                        <td class="py-3 px-2 text-xs text-gray-500">${r.Time || '-'}</td>
                    </tr>
                `;
            });

            html += '</tbody></table>';
            if (filtered.length > 50) {
                html += `<p class="text-center text-xs text-gray-500 mt-4">แสดง 50 จาก ${filtered.length} สถานี</p>`;
            }
            container.innerHTML = html;
        }

        // ========== CHARTS ==========
        function renderCharts() {
            if (!dashboardData) return;

            // Group Pie Chart
            const groupCtx = document.getElementById('groupChart').getContext('2d');
            const groupData = dashboardData.charts.group_stats;
            const groupLabels = Object.keys(groupData);
            const groupValues = Object.values(groupData);
            const groupColors = ['#EF4444', '#F97316', '#FBBF24', '#10B981', '#3B82F6', '#8B5CF6', '#EC4899'];

            if (groupChartInstance) groupChartInstance.destroy();
            groupChartInstance = new Chart(groupCtx, {
                type: 'doughnut',
                data: {
                    labels: groupLabels,
                    datasets: [{
                        data: groupValues,
                        backgroundColor: groupColors.slice(0, groupLabels.length),
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { position: 'right', labels: { font: { size: 10 } } } }
                }
            });

            // Hourly Line Chart
            const hourlyCtx = document.getElementById('hourlyChart').getContext('2d');
            const hourlyData = dashboardData.charts.hourly_stats;
            const hourlyLabels = Object.keys(hourlyData).reverse();
            const hourlyValues = Object.values(hourlyData).reverse();

            if (hourlyChartInstance) hourlyChartInstance.destroy();
            hourlyChartInstance = new Chart(hourlyCtx, {
                type: 'line',
                data: {
                    labels: hourlyLabels,
                    datasets: [{
                        label: 'จำนวนการแจ้งเหตุ',
                        data: hourlyValues,
                        borderColor: '#3B82F6',
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
                }
            });
        }

        // ========== CRISIS MAP ==========
        function initCrisisMap() {
            if (crisisMap) return;
            crisisMap = L.map('crisisMap').setView([13.7563, 100.5018], 6);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap'
            }).addTo(crisisMap);
            updateCrisisMap();
        }

        function updateCrisisMap() {
            if (!crisisMap || !dashboardData) return;

            // Clear existing layers
            crisisMap.eachLayer(layer => {
                if (layer instanceof L.Marker || layer instanceof L.CircleMarker || layer instanceof L.HeatLayer) {
                    crisisMap.removeLayer(layer);
                }
            });

            // Add SOS markers
            dashboardData.map_data.sos.forEach(c => {
                if (c.lat && c.lon) {
                    let color = '#EF4444';
                    if (c.prio.includes('HIGH')) color = '#F97316';
                    else if (c.prio.includes('NORMAL')) color = '#10B981';

                    if (currentLayer === 'all' || currentLayer === 'critical' && c.prio.includes('CRITICAL')) {
                        L.circleMarker([c.lat, c.lon], {
                            radius: 8,
                            fillColor: color,
                            color: '#fff',
                            weight: 2,
                            opacity: 1,
                            fillOpacity: 0.8
                        }).addTo(crisisMap).bindPopup(`<b>SOS:</b> ${c.name}<br>ระดับ: ${c.prio}<br>สถานะ: ${c.status}`);
                    }
                }
            });

            // Add Shelter markers
            if (currentLayer === 'all' || currentLayer === 'shelter') {
                dashboardData.map_data.shelters.forEach(s => {
                    if (s.lat && s.lon) {
                        L.marker([s.lat, s.lon]).addTo(crisisMap)
                            .bindPopup(`<b>ศูนย์พักพิง:</b> ${s.name}<br>สถานะ: ${s.status}<br>จำนวน: ${s.occupancy}/${s.capacity}`);
                    }
                });
            }

            // Heatmap
            if (heatLayer) crisisMap.removeLayer(heatLayer);
            const heatPoints = dashboardData.map_data.sos
                .filter(c => c.lat && c.lon)
                .map(c => [c.lat, c.lon, c.prio.includes('CRITICAL') ? 1 : 0.5]);
            heatLayer = L.heatLayer(heatPoints, { radius: 25, blur: 15, maxZoom: 10 });
        }

        function toggleMapLayer(layer) {
            currentLayer = layer;
            updateCrisisMap();
        }

        function toggleHeatmap() {
            if (!heatLayer) return;
            if (crisisMap.hasLayer(heatLayer)) {
                crisisMap.removeLayer(heatLayer);
            } else {
                heatLayer.addTo(crisisMap);
            }
        }

        // ========== ACTION FUNCTIONS ==========
        function openAcceptModal(requestId) {
            document.getElementById('acceptCaseId').value = requestId;
            document.getElementById('acceptModal').classList.remove('hidden');
        }

        function toggleAcceptModal(show) {
            document.getElementById('acceptModal').classList.toggle('hidden', !show);
        }

        async function submitAcceptCase(event) {
            event.preventDefault();
            const caseId = document.getElementById('acceptCaseId').value;
            const responderName = document.getElementById('acceptResponderName').value;
            const responderNotes = document.getElementById('acceptResponderNotes').value;

            const formData = new FormData();
            formData.append('responder_name', responderName);
            formData.append('responder_notes', responderNotes);

            try {
                const response = await fetch(`/dashboard/accept_case/${caseId}`, {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    toggleAcceptModal(false);
                    alert('รับเคสสำเร็จ! ระบบได้แจ้งเตือนผู้ใช้ทาง LINE แล้ว');
                    loadDashboardData();
                }
            } catch (e) {
                alert('ข้อผิดพลาด: ' + e.message);
            }
        }

        async function completeCase(requestId) {
            if (!confirm('ยืนยันการปิดเคส?')) return;
            try {
                const response = await fetch(`/dashboard/complete_case/${requestId}`, { method: 'POST' });
                const result = await response.json();
                if (result.success) {
                    alert('ปิดเคสสำเร็จ! ระบบได้แจ้งผู้ใช้ทาง LINE แล้ว');
                    loadDashboardData();
                }
            } catch (e) {
                alert('ข้อผิดพลาด: ' + e.message);
            }
        }

        async function completeNeed(timestamp, userId) {
            if (!confirm('ยืนยันว่าส่งมอบสิ่งของแล้ว?')) return;
            const formData = new FormData();
            formData.append('timestamp', timestamp);
            formData.append('user_id', userId);
            try {
                const response = await fetch('/dashboard/complete_need', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    alert('อัปเดตสถานะสำเร็จ!');
                    loadDashboardData();
                }
            } catch (e) {
                alert('ข้อผิดพลาด: ' + e.message);
            }
        }

        // ========== UI FUNCTIONS ==========
        function switchTab(tab) {
            // Update sidebar
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            const navEl = document.getElementById('nav-' + tab);
            if (navEl) navEl.classList.add('active');

            // Show/hide content
            if (tab === 'map') {
                document.getElementById('dashboardContent').classList.add('hidden');
                document.getElementById('mapContent').classList.remove('hidden');
                setTimeout(() => {
                    initCrisisMap();
                    if (crisisMap) crisisMap.invalidateSize();
                }, 100);
            } else {
                document.getElementById('mapContent').classList.add('hidden');
                document.getElementById('dashboardContent').classList.remove('hidden');
            }

            // Mobile: close sidebar
            if (window.innerWidth < 1024) {
                toggleSidebar();
            }
        }

        function switchContentTab(tab) {
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            document.querySelectorAll('.content-tab').forEach(el => el.classList.add('hidden'));
            document.getElementById('content-' + tab).classList.remove('hidden');
        }

        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const overlay = document.getElementById('sidebarOverlay');
            sidebar.classList.toggle('-translate-x-full');
            overlay.classList.toggle('hidden');
        }

        function toggleDarkMode() {
            document.documentElement.classList.toggle('dark');
            const isDark = document.documentElement.classList.contains('dark');
            document.getElementById('darkModeText').textContent = isDark ? 'Light Mode' : 'Dark Mode';
        }

        function toggleShelterModal(show) {
            document.getElementById('shelterModal').classList.toggle('hidden', !show);
        }

        function filterTable(tableId, inputId) {
            const input = document.getElementById(inputId);
            const filter = input.value.toLowerCase();
            const table = document.getElementById(tableId);
            if (!table) return;
            const tr = table.getElementsByTagName('tr');
            for (let i = 1; i < tr.length; i++) {
                const tds = tr[i].getElementsByTagName('td');
                let match = false;
                for (let j = 0; j < tds.length; j++) {
                    if (tds[j] && tds[j].textContent.toLowerCase().indexOf(filter) > -1) {
                        match = true;
                        break;
                    }
                }
                tr[i].style.display = match ? '' : 'none';
            }
        }

        function getCurrentLocation() {
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(function(position) {
                    document.getElementById('sh_lat').value = position.coords.latitude;
                    document.getElementById('sh_lon').value = position.coords.longitude;
                }, function() {
                    alert('โปรดกดอนุญาตสิทธิ์เบราว์เซอร์เพื่อดึงตำแหน่งพิกัด GPS');
                });
            } else {
                alert('เบราว์เซอร์ไม่รองรับการดึงพิกัด');
            }
        }
    </script>
</body>
</html>
"""


@dashboard_bp.route("/dashboard/update_status/<request_id>/<new_status>", methods=['GET'])
def update_status(request_id, new_status):
    """Legacy endpoint สำหรับรับเคสและปิดเคส"""
    sheets_client = bot_config.get_sheets_client()
    clean_sheet_id = bot_config.extract_sheet_id(bot_config.GOOGLE_SHEET_ID)
    if sheets_client:
        try:
            sheet = sheets_client.open_by_key(clean_sheet_id)
            sos_worksheet = sheet.worksheet("sos_requests")
            rows = sos_worksheet.get_all_records()
            for i, row in enumerate(rows, start=2):
                if str(row.get("request_id")) == request_id:
                    sos_worksheet.update_cell(i, 13, new_status)
                    break
        except Exception as e:
            print(f"Failed to update status: {e}")
    return redirect("/dashboard")


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
