from flask import Blueprint, render_template_string
import config

# สร้างระบบ Blueprint สำหรับครอบหน้าต่างเว็บแดชบอร์ด
dashboard_bp = Blueprint('dashboard', __name__)
# หน้าหลักเช็กสถานะการรันเซิร์ฟเวอร์ แผนภูมิวินิจฉัยฐานข้อมูลกลาง (Diagnostic Control Panel)
@dashboard_bp.route("/", methods=['GET'])
def index():
    config.get_sheets_client()
    db_status = f"<span style='color: #10b981; font-weight: bold;'>🟢 {config.LAST_SHEETS_ERROR}</span>" if config.SHEETS_INITIALIZED else f"<span style='color: #ef4444; font-weight: bold;'>🔴 เชื่อมต่อล้มเหลว (สาเหตุ: {config.LAST_SHEETS_ERROR})</span>"
    
    # ดึงเส้นทางแบบแบนราบของระบบ
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

# Command Center Web Dashboard สำหรับหน่วยงานกู้ภัย
@dashboard_bp.route("/dashboard", methods=['GET'])
def dashboard():
    sheets_client = config.get_sheets_client()
    clean_sheet_id = config.extract_sheet_id(config.GOOGLE_SHEET_ID)
    sos_cases = []
    shelters = []
    error_msg = ""
    
    if not sheets_client:
        error_msg = f"⚠️ ระบบตรวจพบข้อขัดข้องในการเรียกสิทธิ์: {config.LAST_SHEETS_ERROR}"
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
    critical_count = sum(1 for c in sos_cases if "🔴" in str(c.get("priority", "")))
    high_count = sum(1 for c in sos_cases if "🟠" in str(c.get("priority", "")))
    bedridden_count = sum(1 for c in sos_cases if "YES" in str(c.get("bedridden", "")) or "ใช่" in str(c.get("bedridden", "")))
    
    html_template = """
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>COMMAND CENTER — FLOODCARE AI</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen font-sans">
        <div class="container mx-auto p-4 md:p-6">
            <header class="flex flex-col md:flex-row justify-between items-center pb-6 mb-6 border-b border-slate-800">
                <div class="flex items-center space-x-3">
                    <span class="text-4xl">🚨</span>
                    <div>
                        <h1 class="text-2xl font-bold tracking-wide">FLOODCARE AI</h1>
                        <p class="text-sm text-slate-400">ศูนย์ประสานงานและรายงานเหตุภัยอุทกภัยอัจฉริยะ ( COMMAND CENTER )</p>
                    </div>
                </div>
                <div class="mt-4 md:mt-0 bg-slate-800 px-4 py-2 rounded-lg border border-slate-700 text-sm">
                    🟢 ดึงข้อมูลแบบเรียลไทม์สำเร็จ
                </div>
            </header>

            {% if error_msg %}
            <div class="bg-red-900/50 border border-red-500 text-red-200 p-4 rounded-lg mb-6">
                {{ error_msg }}
            </div>
            {% endif %}

            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
                <div class="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <p class="text-sm text-slate-400">เคสแจ้งเหตุทั้งหมด</p>
                    <p class="text-3xl font-extrabold text-blue-400 mt-1">{{ total_cases }} <span class="text-lg font-normal">เคส</span></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-red-900/50 bg-red-950/20">
                    <p class="text-sm text-red-300">🔴 เคสเร่งด่วนมาก</p>
                    <p class="text-3xl font-extrabold text-red-500 mt-1">{{ critical_count }} <span class="text-lg font-normal">เคส</span></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-orange-900/50 bg-orange-950/20">
                    <p class="text-sm text-orange-300">🟠 เคสระดับปานกลาง</p>
                    <p class="text-3xl font-extrabold text-orange-500 mt-1">{{ high_count }} <span class="text-lg font-normal">เคส</span></p>
                </div>
                <div class="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <p class="text-sm text-slate-400">ผู้ป่วยติดเตียงที่ต้องการช่วย</p>
                    <p class="text-3xl font-extrabold text-purple-400 mt-1">{{ bedridden_count }} <span class="text-lg font-normal">ราย</span></p>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div class="lg:col-span-2 bg-slate-800 rounded-xl border border-slate-700 p-4 overflow-hidden">
                    <div class="flex justify-between items-center mb-4">
                        <h2 class="text-lg font-semibold flex items-center space-x-2">
                            <span>📋</span> <span>รายการขอความช่วยเหลือฉุกเฉิน (SOS)</span>
                        </h2>
                        <input id="searchInput" onkeyup="filterCases()" type="text" placeholder="🔍 ค้นหาพื้นที่..." class="bg-slate-900 border border-slate-700 text-sm px-3 py-1.5 rounded-lg text-slate-200 focus:outline-none focus:border-blue-500">
                    </div>
                    
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse text-sm">
                            <thead>
                                <tr class="border-b border-slate-700 text-slate-400">
                                    <th class="py-3 px-2">เลขเคส / ระดับภัย</th>
                                    <th class="py-3 px-2">ผู้ประสบภัย</th>
                                    <th class="py-3 px-2">ข้อมูลความเร่งด่วน</th>
                                    <th class="py-3 px-2">ระดับน้ำ / รายละเอียด</th>
                                    <th class="py-3 px-2">การนำทาง</th>
                                </tr>
                            </thead>
                            <tbody id="sosTable">
                                {% for case in sos_cases %}
                                <tr class="border-b border-slate-700/50 hover:bg-slate-750/30 transition duration-150 py-3">
                                    <td class="py-3 px-2">
                                        <p class="font-bold text-slate-200 text-xs">{{ case.get('request_id', 'SOS-MOCK') }}</p>
                                        <p class="mt-1 font-semibold text-xs">{{ case.get('priority', '🟢 NORMAL') }}</p>
                                    </td>
                                    <td class="py-3 px-2">
                                        <p class="font-semibold text-slate-200">{{ case.get('first_name', '') }} {{ case.get('last_name', '') }}</p>
                                        <p class="text-xs text-blue-300 mt-1">📞 {{ case.get('phone', '-') }}</p>
                                    </td>
                                    <td class="py-3 px-2">
                                        <p class="text-slate-300">จำนวน: <b>{{ case.get('people_count', '1') }}</b> คน (เด็ก: {{ case.get('children', '-') }}, ชรา: {{ case.get('elderly', '-') }})</p>
                                        <p class="text-xs text-purple-300 mt-1">ติดเตียง: {{ case.get('bedridden', 'NO') }} | สัตว์เลี้ยง: {{ case.get('pets', 'NO') }}</p>
                                    </td>
                                    <td class="py-3 px-2">
                                        <p class="font-semibold text-sky-400 text-xs">🌊 {{case.get('water_level', '-')}}</p>
                                        <p class="text-xs text-slate-400 mt-1 max-w-xs truncate">{{ case.get('note', '-') }}</p>
                                    </td>
                                    <td class="py-3 px-2">
                                        <a href="https://www.google.com/maps/search/?api=1&query={{ case.get('latitude', 0) }},{{ case.get('longitude', 0) }}" target="_blank" class="inline-flex items-center px-3 py-1.5 bg-red-600 hover:bg-red-700 transition font-bold text-xs text-white rounded-lg shadow-md shadow-red-950/20">
                                            🗺️ แผนที่นำทาง
                                        </a>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="bg-slate-800 rounded-xl border border-slate-700 p-4">
                    <h2 class="text-lg font-semibold flex items-center space-x-2 mb-4">
                        <span>🏠</span> <span>สถานะศูนย์อพยพจริงในระบบ</span>
                    </h2>
                    <div class="space-y-4">
                        {% for sh in shelters %}
                        <div class="bg-slate-900/60 p-4 rounded-lg border border-slate-750">
                            <div class="flex justify-between items-start mb-2">
                                <div>
                                    <p class="font-bold text-slate-200">{{ sh.get('Name', 'ไม่ระบุ') }}</p>
                                    <p class="text-xs text-slate-500 mt-0.5">{{ sh.get('District', '') }} จ.{{ sh.get('Province', '') }}</p>
                                </div>
                                <span class="px-2 py-0.5 text-xs font-semibold rounded {{ 'bg-red-950 text-red-400' if sh.get('Status') == 'เต็ม' else 'bg-green-950 text-green-400' }}">
                                    {{ sh.get('Status', 'ว่าง') }}
                                </span>
                            </div>
                            <div class="w-full bg-slate-800 rounded-full h-2 mt-3">
                                <div class="bg-blue-500 h-2 rounded-full" style="width: {{ (sh.get('Occupancy', 0)|int / sh.get('Capacity', 100)|int * 100)|round|int if sh.get('Capacity', 100)|int > 0 else 0 }}%"></div>
                            </div>
                            <div class="flex justify-between items-center text-xs text-slate-400 mt-2">
                                <span>เข้าพัก: {{ sh.get('Occupancy', 0) }} / {{ sh.get('Capacity', 100) }} คน</span>
                                <span>ติดต่อ: {{ sh.get('Contact', '-') }}</span>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>

        <script>
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
    return render_template_string(html_template, sos_cases=sos_cases, shelters=shelters, error_msg=error_msg, total_cases=total_cases, urgent_count=critical_count, high_count=high_count, bedridden_count=bedridden_count)
