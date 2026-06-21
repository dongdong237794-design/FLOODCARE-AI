import os
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, abort, jsonify
import bot_config
from dashboard import dashboard_bp

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction,
    MessageAction
)

app = Flask(__name__)

# Register Dashboard Blueprint
app.register_blueprint(dashboard_bp)

# =============================================================================
# AUTO-SYNC: Check water data freshness before serving
# =============================================================================
WATER_DATA_MAX_AGE_MINUTES = 12


def _ensure_water_data_fresh():
    """
    Check last sync time from Supabase sync_metadata.
    If stale or missing -> trigger sync from ThaiWater.
    """
    needs_sync = True
    try:
        last_sync = bot_config.get_last_sync_time()
        if last_sync:
            last_sync_time = datetime.datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
            now = datetime.datetime.now(datetime.timezone.utc)
            age_minutes = (now - last_sync_time).total_seconds() / 60
            print(f"[AutoSync] Water_Levels age: {age_minutes:.1f} min")
            if age_minutes <= WATER_DATA_MAX_AGE_MINUTES:
                needs_sync = False
    except Exception as e:
        print(f"[AutoSync] Could not read last sync, will sync: {e}")
    
    if needs_sync:
        print("[AutoSync] Water_Levels stale or missing -> triggering sync now...")
        try:
            success = bot_config.sync_water_levels_to_supabase()
            print(f"[AutoSync] Sync result: {success}")
        except Exception as e:
            print(f"[AutoSync] Sync attempt failed: {e}")
        return True
    
    return False


def _trigger_background_sync():
    """
    Fire-and-forget wrapper so _ensure_water_data_fresh() never blocks
    a request/response cycle (the LINE webhook reply or app boot).
    Safe to call multiple times; sync_water_levels_to_supabase() itself
    only does real work when data is actually stale/missing.
    """
    def _run():
        try:
            _ensure_water_data_fresh()
        except Exception as e:
            print(f"[AutoSync] Background sync thread error: {e}")
    threading.Thread(target=_run, daemon=True).start()


# Kick off a freshness check as soon as the app process boots, so a fresh
# deployment (empty water_levels table) gets populated automatically
# instead of staying empty until someone manually hits /debug/force-sync.
_trigger_background_sync()


# =============================================================================
# DEBUG ROUTES
# =============================================================================
@app.route("/debug/thaiwater", methods=['GET'])
def debug_thaiwater():
    result = {
        "v3_api_reachable": False,
        "v3_raw_sample": None,
        "v3_parsed_sample": None,
        "v3_total_count": 0,
        "error": None
    }
    try:
        raw_data = bot_config.fetch_waterlevel_v3()
        if raw_data:
            result["v3_api_reachable"] = True
            result["v3_total_count"] = len(raw_data)
            result["v3_raw_sample"] = raw_data[0] if len(raw_data) > 0 else None
            result["v3_parsed_sample"] = bot_config.parse_v3_station(raw_data[0]) if len(raw_data) > 0 else None
        else:
            result["error"] = "fetch_waterlevel_v3() returned None or empty list"
    except Exception as e:
        result["error"] = str(e)
    
    return jsonify(result)


@app.route("/debug/sync-status", methods=['GET'])
def debug_sync_status():
    supabase = bot_config.get_supabase_client()
    last_sync = bot_config.get_last_sync_time()
    
    result = {
        "supabase_connected": supabase is not None,
        "last_sync": last_sync,
        "record_count": 0
    }
    
    if supabase:
        try:
            response = supabase.table("water_levels").select("station_code", count="exact").execute()
            result["record_count"] = len(response.data) if response.data else 0
        except Exception as e:
            result["error"] = str(e)
    
    return jsonify(result)


@app.route("/debug/force-sync", methods=['POST'])
def debug_force_sync():
    try:
        success = bot_config.sync_water_levels_to_supabase()
        return jsonify({"success": success})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# WEBHOOK ROUTE FOR LINE
# =============================================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        bot_config.handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# =============================================================================
# TEXT MESSAGE HANDLER
# =============================================================================
@bot_config.handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    user_id = event.source.user_id
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    state = bot_config.USER_STATES.get(user_id)
    
    # ===========================
    # CANCEL FEATURE
    # ===========================
    if user_text == "ยกเลิก":
        bot_config.USER_STATES.pop(user_id, None)
        bot_config.USER_DATA.pop(user_id, None)
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนเรียบร้อยแล้วครับ คุณสามารถกดใช้งานเมนูหลักใหม่ได้ทันทีครับ")
        )
        return
    
    # ===========================
    # SOS LOCATION STATE
    # ===========================
    if state == "sos_location":
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 ส่งพิกัดตำแหน่งแจ้งเหตุ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="🚨 ระบบกำลังรอพิกัดของคุณครับ โปรดกดปุ่ม '📍 ส่งพิกัดตำแหน่งแจ้งเหตุ' ด้านล่าง หรือพิมพ์ 'ยกเลิก' เพื่อเริ่มต้นใหม่ครับ",
                quick_reply=location_quick_reply
            )
        )
        return
    
    # ===========================
    # NEEDS LOCATION STATE
    # ===========================
    if state == "needs_location":
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเพื่อรับสิ่งของ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📌 ระบบกำลังรอพิกัดของคุณครับ โปรดกดปุ่ม '📍 แชร์พิกัดเพื่อรับสิ่งของ' ด้านล่าง หรือพิมพ์ 'ยกเลิก' เพื่อยกเลิกครับ",
                quick_reply=location_quick_reply
            )
        )
        return
    
    # ===========================
    # USER REGISTRATION (3 Steps)
    # ===========================
    if state == "register_first_name":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["temp_first_name"] = user_text
        bot_config.USER_STATES[user_id] = "register_last_name"
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ขั้นตอนที่ 2: โปรดพิมพ์ 'นามสกุล' ของคุณครับ")
        )
        return
    
    elif state == "register_last_name":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["temp_last_name"] = user_text
        bot_config.USER_STATES[user_id] = "register_phone"
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ขั้นตอนที่ 3: โปรดพิมพ์ 'เบอร์โทรศัพท์' 9-10 หลักครับ (เช่น 0812345678)")
        )
        return
    
    elif state == "register_phone":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        clean_phone = bot_config.extract_number(user_text)
        if len(clean_phone) < 9 or len(clean_phone) > 10:
            bot_config.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ เบอร์โทรไม่ถูกต้องครับ! โปรดพิมพ์ตัวเลข 9-10 หลักใหม่อีกครับ")
            )
            return
        
        first_name = bot_config.USER_DATA[user_id].get("temp_first_name", "ผู้แจ้ง")
        last_name = bot_config.USER_DATA[user_id].get("temp_last_name", "ทั่วไป")
        
        # Save to Supabase
        success = bot_config.register_user(user_id, first_name, last_name, clean_phone)
        
        bot_config.USER_STATES.pop(user_id, None)
        
        if success:
            reply_text = (
                f"🎉 ยินดีต้อนรับครับ คุณ {first_name} {last_name}!\n"
                f"ระบบลงทะเบียนเรียบร้อยแล้วครับ\n\n"
                f"🛡️ กดปุ่มเมนูด้านล่างเพื่อใช้งานได้ทันทีครับ"
            )
        else:
            reply_text = (
                f"🎉 ลงทะเบียนสำเร็จ (ระบบชั่วคราว) คุณ {first_name} {last_name}!\n"
                f"⚠️ บันทึกลงฐานข้อมูลไม่สำเร็จ แต่ใช้งานได้ตามปกติครับ"
            )
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return
    
    # ===========================
    # SOS FLOW (4 Steps - No Photo)
    # ===========================
    if state and state.startswith("sos_"):
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        
        # ---- Step 2: Select victim groups ----
        if state == "sos_step2":
            if "group_types" not in bot_config.USER_DATA[user_id]:
                bot_config.USER_DATA[user_id]["group_types"] = []
            
            valid_options = {
                "👶 มีเด็กเล็ก/คนชรา": "เด็กเล็ก/คนชรา",
                "🚑 มีผู้ป่วยติดเตียง/พิการ": "ผู้ป่วยติดเตียง/พิการ",
                "🩸 มีผู้บาดเจ็บฉุกเฉิน": "ผู้บาดเจ็บฉุกเฉิน",
                "👨‍👩‍👧 ผู้ใหญ่ทั่วไป": "ผู้ใหญ่ทั่วไป",
                "🐶 มีสัตว์เลี้ยง": "สัตว์เลี้ยง"
            }
            
            if user_text in valid_options:
                selected = valid_options[user_text]
                if selected not in bot_config.USER_DATA[user_id]["group_types"]:
                    bot_config.USER_DATA[user_id]["group_types"].append(selected)
                
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                        QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                        QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                        QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                        QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น"))
                    ]
                )
                selected_text = ", ".join(bot_config.USER_DATA[user_id]["group_types"]) if bot_config.USER_DATA[user_id]["group_types"] else "ยังไม่ได้เลือก"
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text=f"👥 กลุ่มที่เลือก: {selected_text}\n\nเลือกเพิ่มหรือกด 'เสร็จสิ้น' เพื่อไปต่อครับ",
                        quick_reply=quick_reply
                    )
                )
                return
            elif user_text in ["เสร็จสิ้น", "➡️ เสร็จสิ้น"]:
                if not bot_config.USER_DATA[user_id].get("group_types"):
                    bot_config.USER_DATA[user_id]["group_types"] = ["ผู้ใหญ่ทั่วไป"]
                bot_config.USER_STATES[user_id] = "sos_step3"
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="🔴 วิกฤต (มิดหัว/ติดหลังคา)", text="🔴 วิกฤต (มิดหัว/ติดบนหลังคา)")),
                        QuickReplyButton(action=MessageAction(label="🟠 สูง (ระดับอก/เกิน 1 เมตร)", text="🟠 สูง (ระดับอก/เกิน 1 เมตร)")),
                        QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (ระดับเอว)", text="🟡 ปานกลาง (ระดับเอว)")),
                        QuickReplyButton(action=MessageAction(label="🟢 ต่ำ (ระดับหน้าแข้ง)", text="🟢 ต่ำ (ระดับหน้าแข้ง)")),
                        QuickReplyButton(action=MessageAction(label="💊 ขาดแคลนยา/อาหาร", text="💊 ขาดแคลนยา/อาหารหนัก"))
                    ]
                )
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="🌊 ระดับน้ำและสถานการณ์ปัจจุบัน\n\nโปรดเลือกระดับความรุนแรง:",
                        quick_reply=quick_reply
                    )
                )
                return
            else:
                if user_text:
                    bot_config.USER_DATA[user_id]["group_types"].append(user_text)
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                        QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                        QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                        QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                        QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น"))
                    ]
                )
                selected_text = ", ".join(bot_config.USER_DATA[user_id]["group_types"])
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text=f"👥 กลุ่มที่เลือก: {selected_text}\n\nเลือกเพิ่มหรือกด 'เสร็จสิ้น' เพื่อไปต่อครับ",
                        quick_reply=quick_reply
                    )
                )
                return
        
        # ---- Step 3: Urgency level ----
        elif state == "sos_step3":
            urgency_map = {
                "🔴 วิกฤต (มิดหัว/ติดบนหลังคา)": "วิกฤต",
                "🟠 สูง (ระดับอก/เกิน 1 เมตร)": "สูง",
                "🟡 ปานกลาง (ระดับเอว)": "ปานกลาง",
                "🟢 ต่ำ (ระดับหน้าแข้ง)": "ต่ำ",
                "💊 ขาดแคลนยา/อาหารหนัก": "ขาดแคลนยา"
            }
            bot_config.USER_DATA[user_id]["urgency_level"] = urgency_map.get(user_text, user_text)
            # SKIP Step 4 (photo) - go directly to confirm
            bot_config.USER_DATA[user_id]["photo_url"] = "-"
            bot_config.USER_STATES[user_id] = "sos_confirm"
            _send_sos_summary(event, user_id)
            return
        
        # ---- Step 4: Confirm ----
        elif state == "sos_confirm":
            if "ยืนยัน" in user_text:
                data = bot_config.USER_DATA.pop(user_id, {})
                bot_config.USER_STATES.pop(user_id, None)
                
                case_id = bot_config.generate_case_id()
                group_types = data.get("group_types", ["ผู้ใหญ่ทั่วไป"])
                urgency = data.get("urgency_level", "ต่ำ")
                priority_code = data.get("priority", "NORMAL")
                
                # Save to Supabase
                success = False
                supabase = bot_config.get_supabase_client()
                if supabase:
                    try:
                        supabase.table("sos_requests").insert({
                            "request_id": case_id,
                            "user_id": str(user_id),
                            "timestamp": timestamp,
                            "latitude": float(data.get("latitude", 0)),
                            "longitude": float(data.get("longitude", 0)),
                            "group_count": len(group_types),
                            "group_types": ", ".join(group_types),
                            "urgency_level": urgency,
                            "photo_url": "-",
                            "water_level": "-",
                            "note": data.get("note", "-"),
                            "priority": priority_code,
                            "status": "OPEN",
                            "responder_name": "-",
                            "responder_notes": "-",
                            "accepted_at": None,
                            "completed_at": None
                        }).execute()
                        print("[Supabase] SOS request saved successfully")
                        success = True
                    except Exception as e:
                        print(f"[Supabase] Failed to save SOS: {e}")
                
                if success:
                    reply_text = (
                        f"🚀 ส่งข้อมูลสำเร็จ! เลขเคส: {case_id}\n"
                        f"ทีมกู้ภัยกำลังจัดลำดับความสำคัญ\n\n"
                        f"🛡️ ระหว่างรอ:\n"
                        f"1. ตัดสะพานไฟในบ้านทันที\n"
                        f"2. พยายามอยู่บนที่สูง\n"
                        f"3. เตรียมไฟฉายหรือนกหวีด\n"
                        f"4. ประหยัดแบตเตอรี่มือถือ\n"
                        f"5. หากอันตรายถึงชีวิต โทร 1784"
                    )
                else:
                    reply_text = (
                        f"⚠️ บันทึกข้อมูลไม่สำเร็จ\n"
                        f"เลขเคส: {case_id}\n"
                        f"กรุณาโทร 1784 หรือ 1669 โดยตรงครับ"
                    )
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return
            else:
                bot_config.USER_STATES.pop(user_id, None)
                bot_config.USER_DATA.pop(user_id, None)
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ ยกเลิกเคสเรียบร้อยครับ กดปุ่ม SOS ใหม่ได้ทันทีครับ")
                )
                return
    
    # ===========================
    # USER NEEDS FLOW (5 Steps)
    # ===========================
    if state and state.startswith("needs_"):
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        
        # ---- Step 2: Category selection ----
        if state == "needs_step2":
            categories = {
                "🍲 อาหาร/น้ำดื่ม": "อาหาร/น้ำดื่ม",
                "💊 ยารักษาโรค/เวชภัณฑ์": "ยารักษาโรค/เวชภัณฑ์",
                "👶 ของใช้เด็กอ่อน": "ของใช้เด็กอ่อน",
                "🧼 ของใช้ส่วนตัว": "ของใช้ส่วนตัว",
                "🔦 อุปกรณ์ส่องสว่าง": "อุปกรณ์ส่องสว่าง",
                "📝 อื่นๆ (ระบุเอง)": "อื่นๆ"
            }
            
            if user_text in categories:
                if "need_categories" not in bot_config.USER_DATA[user_id]:
                    bot_config.USER_DATA[user_id]["need_categories"] = []
                bot_config.USER_DATA[user_id]["need_categories"].append(categories[user_text])
                
                quick_reply = QuickReply(
                    items=[
                        QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="🍲 อาหาร/น้ำดื่ม")),
                        QuickReplyButton(action=MessageAction(label="💊 ยา/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
                        QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็ก", text="👶 ของใช้เด็กอ่อน")),
                        QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
                        QuickReplyButton(action=MessageAction(label="🔦 ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
                        QuickReplyButton(action=MessageAction(label="📝 อื่นๆ", text="📝 อื่นๆ (ระบุเอง)")),
                        QuickReplyButton(action=MessageAction(label="➡️ เสร็จสิ้น", text="เสร็จสิ้น"))
                    ]
                )
                selected = ", ".join(bot_config.USER_DATA[user_id]["need_categories"])
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text=f"📦 หมวดหมู่ที่เลือก: {selected}\n\nเลือกเพิ่มหรือกด 'เสร็จสิ้น' เพื่อไปต่อครับ",
                        quick_reply=quick_reply
                    )
                )
                return
            elif user_text == "เสร็จสิ้น":
                if not bot_config.USER_DATA[user_id].get("need_categories"):
                    bot_config.line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="⚠️ กรุณาเลือกหมวดหมู่อย่างน้อย 1 รายการครับ")
                    )
                    return
                bot_config.USER_STATES[user_id] = "needs_step3"
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="📝 โปรดระบุรายละเอียดสั้นๆ\n\nเช่น จำนวนที่ต้องการ หรือยี่ห้อเฉพาะ\n(เช่น 'ขอน้ำดื่ม 2 แพ็ค และผ้าอนามัยครับ')"
                    )
                )
                return
            else:
                if "need_categories" not in bot_config.USER_DATA[user_id]:
                    bot_config.USER_DATA[user_id]["need_categories"] = []
                bot_config.USER_DATA[user_id]["need_categories"].append(f"อื่นๆ: {user_text}")
                bot_config.USER_STATES[user_id] = "needs_step3"
                bot_config.line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="📝 โปรดระบุรายละเอียดสั้นๆ\n\nเช่น จำนวนที่ต้องการ หรือยี่ห้อเฉพาะ\n(เช่น 'ขอน้ำดื่ม 2 แพ็ค และผ้าอนามัยครับ')"
                    )
                )
                return
        
        # ---- Step 3: Details ----
        elif state == "needs_step3":
            bot_config.USER_DATA[user_id]["need_details"] = user_text
            bot_config.USER_STATES[user_id] = "needs_step4"
            quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="🔴 ด่วนมาก (หมดแล้ว)", text="🔴 ด่วนมาก (หมดแล้ว)")),
                    QuickReplyButton(action=MessageAction(label="🟡 ปานกลาง (รอได้ 24 ชม.)", text="🟡 ปานกลาง (รอได้ 24 ชม.)")),
                    QuickReplyButton(action=MessageAction(label="🟢 ไม่ด่วน", text="🟢 ไม่ด่วน (แจ้งไว้ล่วงหน้า)"))
                ]
            )
            bot_config.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="⏳ ความต้องการนี้เร่งด่วนเพียงใด?\n\nโปรดเลือก:",
                    quick_reply=quick_reply
                )
            )
            return
        
        # ---- Step 4: Urgency ----
        elif state == "needs_step4":
            bot_config.USER_DATA[user_id]["need_urgency"] = user_text
            bot_config.USER_STATES[user_id] = "needs_confirm"
            _send_needs_summary(event, user_id)
            return
        
        # ---- Step 5: Confirm ----
        elif state == "needs_confirm":
            if "ยืนยัน" in user_text:
                data = bot_config.USER_DATA.pop(user_id, {})
                bot_config.USER_STATES.pop(user_id, None)
                
                success = bot_config.save_user_need(
                    user_id, timestamp,
                    data.get("need_latitude", "0"),
                    data.get("need_longitude", "0"),
                    ", ".join(data.get("need_categories", [])),
                    data.get("need_details", "-"),
                    data.get("need_urgency", "ไม่ด่วน")
                )
                
                if success:
                    reply_text = (
                        f"🟢 บันทึกความต้องการเรียบร้อยครับ!\n\n"
                        f"📦 หมวดหมู่: {', '.join(data.get('need_categories', []))}\n"
                        f"📝 รายละเอียด: {data.get('need_details', '-')}\n\n"
                        f"ทีมอาสาสมัครจะดำเนินการจัดส่งให้ครับ"
                    )
                else:
                    reply_text = (
                        f"🟢 บันทึกความต้องการสำเร็จ (ระบบชั่วคราว)\n\n"
                        f"⚠️ ฐานข้อมูลขัดข้อง แต่ข้อมูลถูกเก็บบนเซิร์ฟเวอร์แล้ว\n\n"
                        f"ทีมอาสาสมัครจะดำเนินการจัดส่งให้ครับ"
                    )
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return
            else:
                bot_config.USER_STATES.pop(user_id, None)
                bot_config.USER_DATA.pop(user_id, None)
                bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ยกเลิกรายการเรียบร้อยครับ"))
                return
    
    # ===========================
    # MAIN MENU (6 Buttons)
    # ===========================
    if user_text == "เบอร์โทรศัพท์ฉุกเฉิน":
        contacts_text = bot_config.get_emergency_contacts()
        reply_text = f"📞 เบอร์โทรฉุกเฉิน:\n\n{contacts_text}"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    
    elif user_text == "ศูนย์พักพิง":
        bot_config.USER_STATES[user_id] = "waiting_shelter_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดหาศูนย์พักพิง"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📍 โปรดกดแชร์พิกัด 'Location' ด้านล่าง หรือพิมพ์ชื่ออำเภอ/จังหวัดครับ",
                quick_reply=location_quick_reply
            )
        )
    
    elif user_text == "ตรวจสอบระดับน้ำ":
        bot_config.USER_STATES[user_id] = "waiting_water_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กระดับน้ำ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="🌊 โปรดกดแชร์พิกัด 'Location' เพื่อตรวจสอบระดับน้ำจากสถานี ThaiWater ใกล้คุณครับ",
                quick_reply=location_quick_reply
            )
        )
    
    elif user_text == "SOS ขอความช่วยเหลือ":
        # Check from Supabase
        is_reg, first_name, last_name, phone = bot_config.is_user_registered(user_id)
        
        if not is_reg:
            bot_config.USER_STATES[user_id] = "register_first_name"
            bot_config.USER_DATA[user_id] = {}
            reply_text = (
                "📝 คุณเข้าใช้งานเป็นครั้งแรก\n\n"
                "เพื่อประสานงานกู้ภัยได้อย่างมีประสิทธิภาพ\n"
                "โปรดพิมพ์ 'ชื่อจริง' ของคุณครับ (เช่น สมชาย)"
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        else:
            bot_config.USER_STATES[user_id] = "sos_location"
            bot_config.USER_DATA[user_id] = {
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone
            }
            location_quick_reply = QuickReply(
                items=[
                    QuickReplyButton(action=LocationAction(label="📍 ส่งพิกัดตำแหน่งแจ้งเหตุ"))
                ]
            )
            reply_text = (
                f"🚨 เริ่มขั้นตอนแจ้งเหตุฉุกเฉิน\n\n"
                f"สวัสดีครับคุณ {first_name}!\n"
                f"โปรดกดปุ่มด้านล่างเพื่อส่งพิกัดให้ทีมกู้ภัยครับ"
            )
            bot_config.line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text, quick_reply=location_quick_reply)
            )
    
    elif user_text == "แจ้งความต้องการเพิ่มเติม" or user_text == "ความต้องการ":
        bot_config.USER_STATES[user_id] = "needs_location"
        location_quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเพื่อรับสิ่งของ"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📌 แจ้งความต้องการสิ่งของบรรเทาทุกข์\n\nโปรดกดปุ่มด้านล่างเพื่อแชร์พิกัดครับ",
                quick_reply=location_quick_reply
            )
        )
    
    elif user_text == "ถาม AI เรื่องน้ำท่วม" or "ถาม-ตอบด้วย AI" in user_text or "ถาม–ตอบด้วย AI" in user_text:
        reply_text = "🤖 พิมพ์คำถามหรือข้อกังวลเกี่ยวกับภัยน้ำท่วมได้ทันทีครับ"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    
    # ===========================
    # GREETING DETECTION (Fast Path)
    # ===========================
    elif bot_config.is_greeting(user_text):
        bot_config.handle_greeting_logic(event)
    
    # ===========================
    # AI CHAT (With Typing Indicator)
    # ===========================
    else:
        # Show typing indicator FIRST (before any processing)
        # This gives instant visual feedback to user
        try:
            bot_config.show_loading_animation(user_id, loading_seconds=15)
        except Exception as e:
            print(f"[TypingIndicator] Skipped: {e}")
        
        # Generate AI response
        ai_response = ""
        try:
            # Use shorter timeout for faster response
            response = bot_config.gemini_model.generate_content(
                user_text,
                generation_config={"max_output_tokens": 800}  # เพิ่ม token เพื่อให้ตอบได้ครบถ้วนขึ้น (แก้ปัญหาตอบไม่ครบ)
            )
            ai_response = bot_config.clean_text_for_line(response.text.strip())
        except Exception as e:
            print(f"Gemini Error: {e}")
            ai_response = "⚠️ AI ขัดข้องชั่วคราว หากตกอยู่ในอันตราย โทร ปภ. 1784 ทันทีครับ"
        
        # Log to Supabase (fire-and-forget, don't wait)
        try:
            bot_config.log_ai_chat(user_id, user_text, ai_response, timestamp)
        except Exception as e:
            print(f"[AI Log] Error: {e}")
        
        # Send reply (this will dismiss the typing indicator automatically)
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_response))


# =============================================================================
# LOCATION MESSAGE HANDLER
# =============================================================================
@bot_config.handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    latitude = event.message.latitude
    longitude = event.message.longitude
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    state = bot_config.USER_STATES.pop(user_id, "default")
    
    # ===========================
    # Find nearest shelters
    # ===========================
    if state == "waiting_shelter_location":
        shelter_list = []
        
        # Load from Supabase
        supabase = bot_config.get_supabase_client()
        if supabase:
            try:
                response = supabase.table("shelters").select("*").execute()
                for row in (response.data or []):
                    if str(row.get("status", "")).strip().lower() in ["ปิดทำการ", "closed"]:
                        continue
                    shelter_list.append({
                        "name": row.get("name", row.get("Name", "ไม่ระบุชื่อ")),
                        "lat": float(row.get("lat", row.get("Latitude", 0)) or 0),
                        "lon": float(row.get("lon", row.get("Longitude", 0)) or 0),
                        "capacity": row.get("capacity", row.get("Capacity", 100)),
                        "occupancy": row.get("occupancy", row.get("Occupancy", 0)),
                        "status": row.get("status", row.get("Status", "ว่าง"))
                    })
                print(f"[Shelter] Loaded {len(shelter_list)} shelters from Supabase")
            except Exception as e:
                print(f"[Shelter] Supabase fetch error: {e}")
        
        if not shelter_list:
            reply_text = "⚠️ ไม่พบข้อมูลศูนย์พักพิง โปรดโทร ปภ. 1784 ทันทีครับ"
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return
        
        nearest_shelters = []
        for sh in shelter_list:
            distance = bot_config.calculate_distance(latitude, longitude, sh["lat"], sh["lon"])
            if distance <= 20.0:
                vacancy_status = bot_config.check_shelter_vacancy(sh["capacity"], sh["occupancy"])
                nearest_shelters.append({
                    "name": sh["name"],
                    "distance": distance,
                    "vacancy": vacancy_status,
                    "lat": sh["lat"],
                    "lon": sh["lon"]
                })
        
        nearest_shelters.sort(key=lambda x: x["distance"])
        top_shelters = nearest_shelters[:3]
        
        if not top_shelters:
            reply_text = "📍 ไม่พบศูนย์พักพิงในรัศมี 20 กม. โปรดติดต่อ ปภ. 1784 ครับ"
        else:
            reply_text = "📍 ศูนย์พักพิงใกล้คุณ (รัศมี 20 กม.):\n\n"
            for index, sh in enumerate(top_shelters, 1):
                reply_text += (
                    f"{index}. {sh['name']}\n"
                    f"   ห่าง: {sh['distance']:.2f} กม.\n"
                    f"   สถานะ: {sh['vacancy']}\n"
                    f"   🧭 นำทาง: https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
                )
            reply_text += "⚠️ โปรดใช้ความระมัดระวังในการเดินทางและสังเกตระดับน้ำจริงหน้างาน"
        
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    
    # ===========================
    # Check water levels
    # ===========================
    elif state == "waiting_water_location":
        # Keep Supabase warm: non-blocking freshness check/refresh on real usage,
        # in case the startup sync hasn't completed or previously failed.
        _trigger_background_sync()
        
        # Priority 1: Supabase
        thaiwater_stations = []
        try:
            supabase_stations = bot_config.get_water_data_from_supabase(latitude, longitude, limit=100)
            if supabase_stations:
                for s in supabase_stations:
                    thaiwater_stations.append({
                        "stationName": s.get("name", "ไม่ระบุ"),
                        "provinceName": s.get("location", ""),
                        "riverName": s.get("river", ""),
                        "latitude": s.get("latitude"),
                        "longitude": s.get("longitude"),
                        "distance_km": s.get("distance_km", 0),
                        "water_level": {"value": s.get("water_level"), "uom": "m"},
                        "bank_level": s.get("bank_level"),
                        "situation": s.get("situation", "ปกติ"),
                        "trend": s.get("trend", "คงที่"),
                        "measure_time": s.get("measure_time", "-"),
                        "source": "supabase"
                    })
                print(f"[WaterLevel] Loaded {len(thaiwater_stations)} stations from Supabase")
        except Exception as e:
            print(f"[WaterLevel] Supabase load failed: {e}")
        
        # Fallback: ThaiWater API direct
        if not thaiwater_stations:
            try:
                thaiwater_stations = bot_config.find_nearest_water_stations(
                    latitude, longitude, max_stations=3, max_distance_km=50
                )
                print(f"[WaterLevel] Fallback to API: {len(thaiwater_stations)} stations")
            except Exception as e:
                print(f"[WaterLevel] API fallback failed: {e}")
        
        # Parallel fetch: weather + flood forecast
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                weather_future = executor.submit(bot_config.get_live_weather_scraper, latitude, longitude)
                water_flow_future = executor.submit(bot_config.get_live_water_scraper, latitude, longitude)
                weather_info = weather_future.result()
                water_flow = water_flow_future.result()
        except Exception as e:
            print(f"[WaterLevel] Parallel fetch failed: {e}")
            weather_info = bot_config.get_live_weather_scraper(latitude, longitude)
            water_flow = bot_config.get_live_water_scraper(latitude, longitude)
        
        try:
            flex_msg = bot_config.build_water_level_flex_message(
                latitude, longitude, timestamp, thaiwater_stations, weather_info, water_flow
            )
            bot_config.line_bot_api.reply_message(event.reply_token, flex_msg)
            print("[WaterLevel] Sent Flex Message")
        except Exception as e:
            print(f"[WaterLevel] Flex failed: {e}, using text")
            text_report = bot_config.build_water_level_text_report(
                latitude, longitude, timestamp, thaiwater_stations, weather_info, water_flow
            )
            bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text_report))
    
    # ===========================
    # SOS Step 1: GPS
    # ===========================
    elif state == "sos_location":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["latitude"] = latitude
        bot_config.USER_DATA[user_id]["longitude"] = longitude
        
        bot_config.USER_STATES[user_id] = "sos_step2"
        
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="👶 เด็กเล็ก/คนชรา", text="👶 มีเด็กเล็ก/คนชรา")),
                QuickReplyButton(action=MessageAction(label="🚑 ผู้ป่วย/พิการ", text="🚑 มีผู้ป่วยติดเตียง/พิการ")),
                QuickReplyButton(action=MessageAction(label="🩸 ผู้บาดเจ็บ", text="🩸 มีผู้บาดเจ็บฉุกเฉิน")),
                QuickReplyButton(action=MessageAction(label="👨‍👩‍👧 ผู้ใหญ่", text="👨‍👩‍👧 ผู้ใหญ่ทั่วไป")),
                QuickReplyButton(action=MessageAction(label="🐶 สัตว์เลี้ยง", text="🐶 มีสัตว์เลี้ยง"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="👥 ระบุกลุ่มผู้ประสบภัย (เลือกได้หลายกลุ่ม กด 'เสร็จสิ้น' เมื่อเลือกครบ):",
                quick_reply=quick_reply
            )
        )
    
    # ===========================
    # User Needs Step 1: GPS
    # ===========================
    elif state == "needs_location":
        if user_id not in bot_config.USER_DATA:
            bot_config.USER_DATA[user_id] = {}
        bot_config.USER_DATA[user_id]["need_latitude"] = latitude
        bot_config.USER_DATA[user_id]["need_longitude"] = longitude
        
        bot_config.USER_STATES[user_id] = "needs_step2"
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="🍲 อาหาร/น้ำดื่ม", text="🍲 อาหาร/น้ำดื่ม")),
                QuickReplyButton(action=MessageAction(label="💊 ยา/เวชภัณฑ์", text="💊 ยารักษาโรค/เวชภัณฑ์")),
                QuickReplyButton(action=MessageAction(label="👶 ของใช้เด็ก", text="👶 ของใช้เด็กอ่อน")),
                QuickReplyButton(action=MessageAction(label="🧼 ของใช้ส่วนตัว", text="🧼 ของใช้ส่วนตัว")),
                QuickReplyButton(action=MessageAction(label="🔦 ส่องสว่าง", text="🔦 อุปกรณ์ส่องสว่าง")),
                QuickReplyButton(action=MessageAction(label="📝 อื่นๆ", text="📝 อื่นๆ (ระบุเอง)"))
            ]
        )
        bot_config.line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="📦 เลือกหมวดหมู่สิ่งของที่ต้องการ (เลือกได้หลายหมวด กด 'เสร็จสิ้น' เมื่อเลือกครบ):",
                quick_reply=quick_reply
            )
        )
    
    else:
        confirm_text = "📍 ได้รับพิกัดแล้วครับ หากต้องการแจ้ง SOS กรุณากดเมนู 'SOS ขอความช่วยเหลือ' ก่อนครับ"
        bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=confirm_text))


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def _send_sos_summary(event, user_id):
    """Create and send SOS summary (Step 4 - Confirm)"""
    data = bot_config.USER_DATA[user_id]
    group_types = data.get("group_types", ["ผู้ใหญ่ทั่วไป"])
    urgency = data.get("urgency_level", "ต่ำ")
    
    priority_label, priority_code = bot_config.calculate_sos_priority(group_types, urgency)
    bot_config.USER_DATA[user_id]["priority"] = priority_code
    bot_config.USER_DATA[user_id]["priority_label"] = priority_label
    
    lat = data.get("latitude", "0")
    lon = data.get("longitude", "0")
    maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    
    summary_text = (
        "📋 สรุปข้อมูลแจ้งเหตุ\n\n"
        f"📍 พิกัด: {maps_link}\n"
        f"👥 กลุ่ม: {', '.join(group_types)}\n"
        f"🌊 สถานการณ์: {urgency}\n"
        f"📝 รายละเอียด: {data.get('note', '-')}\n"
        f"📊 ระดับความเร่งด่วน: {priority_label}\n\n"
        f"ยืนยันการส่งข้อมูลแจ้งกู้ภัยหรือไม่?"
    )
    
    quick_reply = QuickReply(
        items=[
            QuickReplyButton(action=MessageAction(label="✅ ยืนยันแจ้งกู้ภัย", text="ยืนยันแจ้งกู้ภัย")),
            QuickReplyButton(action=MessageAction(label="❌ ยกเลิก/แก้ไข", text="ยกเลิกและแก้ไขใหม่"))
        ]
    )
    bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))


def _send_needs_summary(event, user_id):
    """Create and send needs summary"""
    data = bot_config.USER_DATA[user_id]
    lat = data.get("need_latitude", "0")
    lon = data.get("need_longitude", "0")
    maps_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    
    summary_text = (
        "✅ สรุปรายการความต้องการ:\n\n"
        f"📍 พิกัด: {maps_link}\n"
        f"📦 หมวดหมู่: {', '.join(data.get('need_categories', []))}\n"
        f"📝 รายละเอียด: {data.get('need_details', '-')}\n"
        f"⏳ ความเร่งด่วน: {data.get('need_urgency', '-')}\n\n"
        f"ยืนยันการส่งข้อมูลไปยังศูนย์อาสาสมัครหรือไม่?"
    )
    
    quick_reply = QuickReply(
        items=[
            QuickReplyButton(action=MessageAction(label="✅ ยืนยันการแจ้ง", text="ยืนยันการแจ้ง")),
            QuickReplyButton(action=MessageAction(label="❌ ยกเลิก/แก้ไข", text="ยกเลิก"))
        ]
    )
    bot_config.line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary_text, quick_reply=quick_reply))


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
