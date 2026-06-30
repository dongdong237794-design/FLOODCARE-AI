"""
FLOODCARE AI - Flask Application (Optimized & Localized)
=============================================
Main entry point with:
- Intent Classification (reduces Gemini calls by ~80%)
- Automatic Web Form UI (LIFF) response triggers for SOS, Needs, and Registration
- Clean, non-conversational workflow interface
- Localized timezone (Asia/Bangkok)
- Production error logger

Routes:
  POST /callback          - LINE Webhook
  GET  /liff/sos          - SOS LIFF page
  GET  /liff/need         - Needs LIFF page
  GET  /liff/register     - Registration LIFF page
  POST /api/sos/submit    - SOS form submission
  POST /api/need/submit   - Needs form submission
  POST /api/register/submit - Registration form submission
  GET  /debug/* - Debug endpoints
"""

import os
import json
import time
import datetime
from typing import Optional
from flask import Flask, request, abort, jsonify, render_template_string, render_template, g, session, redirect, url_for

# Imports configuration from bot_config (floodcare_ai_optimized_bot_config.py saved as bot_config.py)
import bot_config
from bot_config import (
    # Core systems
    Logger, cache, rate_limiter, sessions,
    IntentClassifier,
    # Time helper
    get_bangkok_time,
    # Utilities
    sanitize_text, extract_sheet_id, calculate_distance,
    generate_case_id, generate_need_id,
    # LINE
    line_bot_api, handler, show_loading_animation,
    # Flex builders
    build_sos_form_flex, build_ai_response_flex,
    build_language_selector_flex, build_water_level_flex_message,
    build_register_form_flex, build_snake_bite_flex, build_help_flex,
    build_need_form_flex, build_weather_flex, build_faq_response_flex,
    # Response handlers
    get_greeting_message, handle_emergency_response,
    build_sos_summary_text, build_needs_summary_text,
    calculate_sos_priority,
    # Services
    ask_gemini, ask_gemini_with_search, get_live_weather_scraper, get_live_weather_data, sheets_mgr,
    get_live_water_levels_from_api, assess_water_level_status, calculate_situation,
    # Legacy state
    USER_STATES, USER_DATA, update_legacy_state,
    # Config
    SOS_LIFF_URL, NEED_LIFF_URL,
    SOS_LIFF_ID, NEED_LIFF_ID,
    REGISTER_LIFF_URL, REGISTER_LIFF_ID,
    WATER_LEVEL_SOURCE_URL, SNAKE_BITE_HOTLINE, SNAKE_BITE_INFO_URL, TMD_SOURCE_URL,
    DASHBOARD_PASSWORD, FLASK_SECRET_KEY,
    LINE_CHANNEL_SECRET,
    WATER_DATA_MAX_AGE_MINUTES,
    GEMINI_API_KEY,
)

from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, ImageMessage, FollowEvent,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction,
    MessageAction, URIAction, FlexSendMessage
)

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY or os.urandom(32)


# =============================================================================
# PERFORMANCE MIDDLEWARE
# =============================================================================

@app.before_request
def before_request():
    request._start_time = time.time()


@app.after_request
def after_request(response):
    if hasattr(request, '_start_time'):
        elapsed = (time.time() - request._start_time) * 1000
        Logger.perf("HTTP", request.endpoint or request.path, elapsed,
                   {"status": response.status_code, "method": request.method})
    return response


# =============================================================================
# RATE LIMITING & HELPER FUNCTIONS
# =============================================================================

def check_rate_limit(user_id: str) -> bool:
    allowed, meta = rate_limiter.check(user_id)
    if not allowed:
        Logger.security("RateLimit", f"Blocked user", user_id,
                       {"retry_after": meta.get("retry_after", 60)})
    return allowed


def _push_save_confirmation(user_id: Optional[str], message: str) -> None:
    if not user_id or user_id == "unknown":
        Logger.info("Push", "Skipped confirmation push — no verified user_id")
        return
    try:
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        Logger.info("Push", f"Failed to send save-confirmation: {e}")


# =============================================================================
# LIFF API AUTH (Robust and Fallback Friendly)
# =============================================================================

import urllib.request as _urllib_request
import hmac as _hmac

def _verify_liff_token(id_token: str, liff_id: str) -> Optional[str]:
    if not id_token or not liff_id:
        return None
    # Filter out common string placeholders when LIFF is not initialized or runs on external browser
    if id_token.lower() in ("null", "undefined", "", "bearer", "bearer null", "bearer undefined"):
        Logger.info("Auth", "id_token has placeholder value — LIFF might be in standalone/test mode")
        return None
    try:
        channel_id = liff_id.split("-")[0] if "-" in liff_id else liff_id
        import urllib.parse
        payload = urllib.parse.urlencode({
            "id_token": id_token,
            "client_id": channel_id,
        }).encode()
        req = _urllib_request.Request(
            "https://api.line.me/oauth2/v2.1/verify",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with _urllib_request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
            return body.get("sub")
    except Exception as e:
        Logger.info("Auth", f"LIFF token verification failed on LINE API: {e}")
        return None


def _require_liff_auth(expected_liff_id: str):
    from functools import wraps
    from flask import g

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            # Skip token validation if the LIFF ID is not configured in the system environment
            if not expected_liff_id:
                Logger.info("Auth", f"LIFF_ID not configured — skipping token verify for {fn.__name__}")
                g.verified_user_id = "unknown"
                return fn(*args, **kwargs)

            auth_header = request.headers.get("Authorization", "")
            id_token = ""
            
            # Extract Bearer token safely
            if auth_header.startswith("Bearer ") and len(auth_header) > 7:
                id_token = auth_header[7:].strip()

            # Prevent passing literal placeholder strings to validation
            if id_token.lower() in ("null", "undefined", ""):
                id_token = ""

            user_id = None
            if id_token:
                user_id = _verify_liff_token(id_token, expected_liff_id)

            if user_id:
                # Flow A: Verified User successfully validated through LINE Server
                if not check_rate_limit(user_id):
                    return jsonify({"success": False, "error": "Rate limit exceeded"}), 429
                g.verified_user_id = user_id
                return fn(*args, **kwargs)

            # Flow B: Token invalid/absent (Fallback checking request body)
            try:
                body_data = request.get_json(silent=True) or {}
                fallback_uid = body_data.get("user_id", "")
                
                # Check for other potential parameters
                if not fallback_uid:
                    fallback_uid = body_data.get("userId", "")

                # Validate and clean fallback User ID
                if fallback_uid and str(fallback_uid).lower() not in ("null", "undefined", "", "unknown"):
                    Logger.info(
                        "Auth",
                        f"LIFF validation bypassed/failed for {fn.__name__} — "
                        f"accepting request body user_id: {fallback_uid} (unverified)."
                    )
                    g.verified_user_id = str(fallback_uid)
                    if not check_rate_limit(g.verified_user_id):
                        return jsonify({"success": False, "error": "Rate limit exceeded"}), 429
                    return fn(*args, **kwargs)
            except Exception as e:
                Logger.error("Auth", f"Fallback identification error: {e}")

            # Flow C: Fully unauthorized access (No valid token, no valid body UID)
            Logger.security("Auth", f"Denied access on {fn.__name__} - missing or invalid authorization keys", "unknown")
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return wrapped
    return decorator


def _require_debug_key():
    from functools import wraps
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            debug_key = os.environ.get("DEBUG_API_KEY", "")
            if not debug_key:
                return jsonify({"error": "Debug endpoints disabled. Set DEBUG_API_KEY to enable."}), 403
            provided = (
                request.args.get("key", "")
                or request.headers.get("X-Debug-Key", "")
            )
            if not _hmac.compare_digest(provided, debug_key):
                Logger.security("Auth", "Invalid debug key", "unknown", {})
                return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)
        return wrapped
    return decorator


# =============================================================================
# MAIN WEBHOOK ROUTE
# =============================================================================

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK', 200


# =============================================================================
# FOLLOW EVENT (First Open / Welcome User)
# =============================================================================

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    try:
        line_bot_api.reply_message(
            event.reply_token,
            [
                get_greeting_message("คุณ"),
                build_register_form_flex("คุณ"),
            ]
        )
    except Exception as e:
        Logger.error("Follow", f"Welcome message failed: {e}")


# =============================================================================
# TEXT MESSAGE HANDLER (Main Intelligence)
# =============================================================================

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    start_time = time.time()
    user_text = sanitize_text(event.message.text.strip())
    user_id = event.source.user_id
    timestamp = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")
    
    if not check_rate_limit(user_id):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ คุณส่งข้อความเร็วเกินไป กรุณารอสักครู่ครับ")
        )
        return
    
    session = sessions.get(user_id)
    current_state = session.state
    
    Logger.info("Message", f"Received: '{user_text[:50]}...' " if len(user_text) > 50 else f"Received: '{user_text}'",
               {"user": bot_config.hash_user_id(user_id), "state": current_state})
    
    # GLOBAL COMMANDS
    if user_text in ["เปลี่ยนภาษา", "change language", "lang"]:
        line_bot_api.reply_message(event.reply_token, build_language_selector_flex())
        return
    
    if user_text.startswith("ตั้งค่าภาษา: "):
        lang = user_text.replace("ตั้งค่าภาษา: ", "").strip()
        session.language = lang
        msgs = {"TH": "✅ ภาษาไทย", "EN": "✅ English", "JP": "✅ 日本語", "MY": "✅ Bahasa Melayu"}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=msgs.get(lang, f"✅ Language set to {lang}"))
        )
        return
    
    if user_text in ["ยกเลิก", "cancel", "หยุด", "stop"]:
        session.reset()
        update_legacy_state(user_id, "IDLE", {})
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนเรียบร้อยแล้วครับ คุณสามารถกดใช้งานเมนูหลักใหม่ได้ทันทีครับ")
        )
        return
    
    # INTENT CLASSIFICATION
    intent, confidence = IntentClassifier.classify(user_text)
    session.last_intent = intent
    
    Logger.info("Intent", f"Classified as {intent} (confidence: {confidence})",
               {"user": bot_config.hash_user_id(user_id)})
    
    # SNAKE BITE (First aid)
    if intent == "SNAKE_BITE":
        line_bot_api.reply_message(event.reply_token, build_snake_bite_flex())
        return

    # EMERGENCY
    if intent == "EMERGENCY":
        emergency_msg = handle_emergency_response(user_id)
        line_bot_api.reply_message(event.reply_token, emergency_msg)
        return
    
    # GREETING
    if intent == "GREETING":
        greeting_msg = get_greeting_message("คุณ")
        line_bot_api.reply_message(event.reply_token, greeting_msg)
        return

    # HELP / CAPABILITIES MENU
    if intent == "HELP":
        line_bot_api.reply_message(event.reply_token, build_help_flex())
        return

    # FAQ (Google search with grounding)
    if intent == "FAQ":
        _handle_faq_query(event, user_id, user_text, timestamp)
        return
    
    # CONTACT
    if intent == "CONTACT":
        _handle_contact_request(event)
        return
    
    # SHELTER
    if intent == "SHELTER":
        _handle_shelter_request(event, user_id)
        return
    
    # WATER LEVEL
    if intent == "WATER_LEVEL":
        _handle_water_level_request(event, user_id)
        return
    
    # WEATHER
    if intent == "WEATHER":
        _handle_weather_request(event, user_id)
        return
    
    # SOS -> Trigger LIFF Form Flex Message directly
    if intent == "SOS":
        _start_sos_flow(event, user_id)
        return
    
    # NEEDS -> Trigger LIFF Form Flex Message directly
    if intent == "NEEDS":
        _start_needs_flow(event, user_id)
        return
    
    # CANCEL
    if intent == "CANCEL":
        session.reset()
        update_legacy_state(user_id, "IDLE", {})
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="❌ ยกเลิกขั้นตอนเรียบร้อยแล้วครับ")
        )
        return
    
    # LANGUAGE
    if intent == "LANGUAGE":
        line_bot_api.reply_message(event.reply_token, build_language_selector_flex())
        return
    
    # REGISTRATION -> Trigger LIFF Form Flex Message directly
    if intent == "REGISTRATION":
        _start_registration(event, user_id)
        return
    
    # AI QUERY (Default)
    if intent == "AI_QUERY":
        _handle_ai_query(event, user_id, user_text, timestamp)
        return
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="🤔 ไม่เข้าใจคำถาม กรุณาลองใหม่หรือเลือกจากเมนูครับ")
    )


# =============================================================================
# LOCATION MESSAGE HANDLER
# =============================================================================

@app.route("/callback/location", methods=['POST']) # Endpoint mapping placeholder if needed
def handle_location_callback():
    return 'OK', 200

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    user_id = event.source.user_id
    lat = event.message.latitude
    lon = event.message.longitude
    timestamp = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")
    
    session = sessions.get(user_id)
    state = session.state
    
    Logger.info("Location", f"Received location: {lat}, {lon}",
               {"user": bot_config.hash_user_id(user_id), "state": state})
    
    if state == "waiting_shelter_location":
        _process_shelter_search(event, lat, lon, user_id)
        return
    
    if state == "waiting_water_location":
        _process_water_level(event, lat, lon, user_id, timestamp)
        return
    
    if state == "waiting_weather_location":
        _process_weather(event, lat, lon, user_id, timestamp)
        return
    
    session.reset()
    update_legacy_state(user_id, "IDLE", {})
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="📍 ได้รับพิกัดแล้วครับ หากต้องการใช้งาน กรุณาเลือกจากเมนูหลักครับ")
    )


# =============================================================================
# IMAGE MESSAGE HANDLER
# =============================================================================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="📸 ได้รับรูปภาพแล้วครับ หากต้องการส่งรูปเพื่อรายงานอุทกภัยหรือขอความช่วยเหลือกรุณาเลือกปุ่มเมนู SOS หรือ ขอความช่วยเหลือเรื่องสิ่งของ และส่งรูปในแบบฟอร์มเว็บไซต์ครับ")
    )


# =============================================================================
# AUTOMATIC WEB FORM TRIGGERS (LIFF)
# =============================================================================

def _start_sos_flow(event, user_id):
    if not SOS_LIFF_URL:
        Logger.info("SOS", "SOS_LIFF_URL not configured")
    line_bot_api.reply_message(event.reply_token, build_sos_form_flex("คุณ"))


def _start_needs_flow(event, user_id):
    if not NEED_LIFF_URL:
        Logger.info("Needs", "NEED_LIFF_URL not configured")
    line_bot_api.reply_message(event.reply_token, build_need_form_flex("คุณ"))


def _start_registration(event, user_id):
    if not REGISTER_LIFF_URL:
        Logger.info("Register", "REGISTER_LIFF_URL not configured")
    line_bot_api.reply_message(event.reply_token, build_register_form_flex("คุณ"))


def _handle_contact_request(event):
    records = sheets_mgr.get_all_records("Contacts")
    if records:
        contacts = []
        for r in records:
            contacts.append(f"🚨 {r.get('Name')}\n   📞 {r.get('Phone')}\n   📝 {r.get('Role', '')}")
        reply = "📞 เบอร์โทรฉุกเฉิน:\n\n" + "\n\n".join(contacts)
    else:
        reply = (
            "📞 เบอร์โทรฉุกเฉิน:\n\n"
            "🚨 ปภ. 1784\n"
            "🚨 สพฉ. 1669\n"
            "🚨 หน่วยกู้ชีพ 1554\n"
            "🚨 ตำรวจทางหลวง 1193"
        )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


def _handle_shelter_request(event, user_id):
    session = sessions.get(user_id)
    session.update(state="waiting_shelter_location")
    update_legacy_state(user_id, "waiting_shelter_location", session.data)
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดหาศูนย์พักพิง"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🏠 ค้นหาศูนย์พักพิง\n\nโปรดกดแชร์พิกัดด้านล่างเพื่อหาศูนย์พักพิงใกล้คุณที่สุดครับ:",
            quick_reply=quick_reply
        )
    )


def _handle_water_level_request(event, user_id):
    session = sessions.get(user_id)
    session.update(state="waiting_water_location")
    update_legacy_state(user_id, "waiting_water_location", session.data)
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กระดับน้ำ"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🌊 ตรวจสอบระดับน้ำจริง\n\nโปรดกดแชร์พิกัดเพื่อเช็กสถานีวัดระดับน้ำใกล้คุณ:",
            quick_reply=quick_reply
        )
    )


def _handle_weather_request(event, user_id):
    session = sessions.get(user_id)
    session.update(state="waiting_weather_location")
    update_legacy_state(user_id, "waiting_weather_location", session.data)
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=LocationAction(label="📍 แชร์พิกัดเช็กอากาศ"))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🌦️ ตรวจสอบสภาพอากาศปัจจุบัน\n\nโปรดกดแชร์พิกัดเพื่อรับรายงานสภาพอากาศจากกรมอุตุฯ:",
            quick_reply=quick_reply
        )
    )


# =============================================================================
# CHAT AI HANDLERS (With Typing Indicators)
# =============================================================================

def _handle_faq_query(event, user_id, user_text, timestamp):
    show_loading_animation(user_id, loading_seconds=30)

    result = ask_gemini_with_search(user_text, max_tokens=8192)
    answer = result.get("answer", "")
    sources = result.get("sources", [])

    if not answer:
        answer = "ขออภัยครับ ไม่พบข้อมูลเกี่ยวกับภัยพิบัติหรือความปลอดภัยในคำถามนี้ กรุณาลองสอบถามใหม่อีกครั้งครับ"

    flex_msg = build_faq_response_flex(answer, sources, user_text)
    line_bot_api.reply_message(event.reply_token, flex_msg)

    try:
        sheets_mgr.batch_append("AI_Logs", [[
            timestamp, user_id, "FAQ_SEARCH", user_text[:200],
            answer[:1000], str(len(sources))
        ]])
    except Exception as e:
        Logger.error("FAQ", f"Log error: {e}")


def _handle_ai_query(event, user_id, user_text, timestamp):
    show_loading_animation(user_id, loading_seconds=30)

    result = ask_gemini_with_search(
        "ประเมินตามกฎและเงื่อนไข System Instruction (น้องบอท): ตอบเฉพาะเรื่องน้ำท่วม สภาพอากาศ "
        "ความปลอดภัย และสุขภาพของผู้เจ็บป่วยหรือเครียดเท่านั้น ปฏิเสธเรื่องนอกขอบเขตอย่างสุภาพและอบอุ่น "
        "และตอบคำถามดังต่อไปนี้โดยไม่ใช้เครื่องหมายดอกจันเด็ดขาด:\n\n"
        f"คำถาม: {user_text}",
        max_tokens=8192
    )
    ai_response = result.get("answer", "")
    sources = result.get("sources", [])

    if not ai_response:
        ai_response = "น้องบอทไม่พร้อมใช้งานชั่วคราวครับ หากฉุกเฉินรบกวนโทร ปภ. 1784 ได้ทันทีครับ"

    if sources:
        flex_msg = build_faq_response_flex(ai_response, sources, user_text)
    else:
        flex_msg = build_ai_response_flex(ai_response, user_text)

    line_bot_api.reply_message(event.reply_token, flex_msg)

    try:
        sheets_mgr.batch_append("AI_Logs", [[
            timestamp, user_id, "AI_QUERY", user_text[:200],
            ai_response[:1000], str(len(sources))
        ]])
    except Exception as e:
        Logger.error("AI", f"Log error: {e}")


# =============================================================================
# LOCATION PROCESSORS
# =============================================================================

def _process_shelter_search(event, lat, lon, user_id):
    session = sessions.get(user_id)
    records = sheets_mgr.get_all_records("Shelters")
    
    shelters = []
    for r in records:
        if str(r.get("Status", "")).strip() == "ปิดทำการ":
            continue
        try:
            sh_lat = float(r.get("Latitude", 0))
            sh_lon = float(r.get("Longitude", 0))
            dist = calculate_distance(lat, lon, sh_lat, sh_lon)
            cap = int(r.get("Capacity", 100))
            occ = int(r.get("Occupancy", 0))
            remaining = max(0, cap - occ)
            
            if remaining <= 0:
                status = "🔴 เต็ม"
            elif occ >= cap * 0.8:
                status = f"🟡 ใกล้เต็ม (ว่าง {remaining})"
            else:
                status = f"🟢 มีที่ว่าง (ว่าง {remaining})"
            
            shelters.append({
                "name": r.get("Name", "ไม่ระบุ"),
                "distance": dist,
                "status": status,
                "lat": sh_lat,
                "lon": sh_lon
            })
        except (ValueError, TypeError):
            continue
    
    shelters.sort(key=lambda x: x["distance"])
    top = shelters[:3]
    
    if not top:
        reply = "📍 ไม่พบข้อมูลศูนย์พักพิงในระยะใกล้เคียงเลยครับ โปรดติดต่อสายด่วน ปภ. 1784 ครับ"
    else:
        reply = "📍 ศูนย์พักพิงใกล้บ้านคุณ:\n\n"
        for i, sh in enumerate(top, 1):
            reply += (
                f"{i}. {sh['name']}\n"
                f"   ห่างจากคุณ: {sh['distance']:.2f} กม.\n"
                f"   สถานะ: {sh['status']}\n"
                f"   🧭 ลิงก์นำทาง Google Maps:\n   https://www.google.com/maps/search/?api=1&query={sh['lat']},{sh['lon']}\n\n"
            )
        reply += "⚠️ โปรดใช้ความระมัดระวังสูงสุดในการเดินทางช่วงน้ำเชี่ยวครับ"
    
    session.reset()
    update_legacy_state(user_id, "IDLE", {})
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


def _process_water_level(event, lat, lon, user_id, timestamp):
    session = sessions.get(user_id)
    show_loading_animation(user_id, loading_seconds=10)
    
    # 1. First attempt to pull manual override records from Google Sheets
    records = []
    try:
        records = sheets_mgr.get_all_records("Water_Levels")
    except Exception as e:
        Logger.error("WaterLevel", f"Failed to retrieve data from Google Sheets: {e}")
        
    # 2. If Google Sheet has no records (empty sheet), fetch live telemetries directly from ThaiWater API
    if not records:
        Logger.info("WaterLevel", "Google Sheet is empty. Automatically fetching live data from official ThaiWater API...")
        records = get_live_water_levels_from_api()
    
    stations = []
    for r in records:
        try:
            st_lat = float(r.get("Lat", 0))
            st_lon = float(r.get("Lon", 0))
            if st_lat == 0 and st_lon == 0:
                continue
            dist = calculate_distance(lat, lon, st_lat, st_lon)
            
            wl_val = r.get("WaterLevel", "-")
            bl_val = r.get("BankLevel", "-")
            situation = r.get("Situation", "ปกติ")
            trend = r.get("Trend", "คงที่")
            
            stations.append({
                "stationName": r.get("Name", "ไม่ระบุ"),
                "provinceName": r.get("Location", ""),
                "riverName": r.get("River", ""),
                "latitude": st_lat,
                "longitude": st_lon,
                "distance_km": dist,
                "water_level": {"value": wl_val, "uom": "m"},
                "bank_level": bl_val,
                "situation": situation,
                "trend": trend,
                "measure_time": r.get("Time", "-"),
                "source": "api" if "StationCode" in r else "sheets"
            })
        except (ValueError, TypeError):
            continue
    
    stations.sort(key=lambda x: x["distance_km"])
    top_stations = stations[:3]
    
    try:
        flex_msg = build_water_level_flex_message(lat, lon, timestamp, top_stations)
        line_bot_api.reply_message(event.reply_token, flex_msg)
    except Exception as e:
        Logger.error("WaterLevel", f"Flex failed: {e}")
        lines = ["🌊 ระดับน้ำใกล้คุณ:\n"]
        for st in top_stations:
            wl = st.get("water_level", {})
            wl_val = wl.get("value", "-")
            lines.append(f"• {st['stationName']} (ห่าง {st['distance_km']:.1f} กม.): {wl_val} ม.")
        lines.append(f"\n🔗 ดูแผนที่ระดับน้ำทั้งประเทศ: {WATER_LEVEL_SOURCE_URL}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines)))
    
    session.reset()
    update_legacy_state(user_id, "IDLE", {})


def _process_weather(event, lat, lon, user_id, timestamp):
    session = sessions.get(user_id)
    show_loading_animation(user_id, loading_seconds=10)

    weather_data = get_live_weather_data(lat, lon)

    session.reset()
    update_legacy_state(user_id, "IDLE", {})

    try:
        flex_msg = build_weather_flex(lat, lon, weather_data, timestamp)
        line_bot_api.reply_message(event.reply_token, flex_msg)
    except Exception as e:
        Logger.error("Weather", f"Flex failed: {e}")
        weather_text = get_live_weather_scraper(lat, lon)
        reply = (
            f"📍 พิกัด: {lat:.4f}, {lon:.4f}\n"
            f"🕒 {timestamp}\n\n"
            f"{weather_text}\n\n"
            f"⚠️ ข้อมูลพยากรณ์เบื้องต้น โปรดสังเกตท้องฟ้าจริงประกอบ\n"
            f"🔗 ดูพยากรณ์เต็มรูปแบบ: {TMD_SOURCE_URL}"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


# =============================================================================
# LIFF ENDPOINTS
# =============================================================================

def _render_liff_page(template_name: str, page_label: str):
    try:
        # Dynamic LIFF ID injection to eliminate manual query parameter breaks
        liff_id = ""
        if "sos" in template_name:
            liff_id = SOS_LIFF_ID
        elif "need" in template_name:
            liff_id = NEED_LIFF_ID
        elif "register" in template_name:
            liff_id = REGISTER_LIFF_ID
            
        return render_template(template_name, liff_id=liff_id)
    except Exception as e:
        import traceback
        Logger.error(
            "LIFF",
            f"Failed to render {page_label} ({template_name}): {e}\n{traceback.format_exc()}"
        )
        return (
            f"{page_label} โหลดไม่สำเร็จ — กรุณาติดต่อผู้ดูแลระบบเพื่อแก้ไขปัญหา",
            500,
        )


@app.route("/liff/sos")
def sos_liff_page():
    return _render_liff_page("sos_liff.html", "SOS LIFF")


@app.route("/liff/need")
def need_liff_page():
    return _render_liff_page("need_liff.html", "Needs LIFF")


@app.route("/liff/register")
def register_liff_page():
    return _render_liff_page("register_liff.html", "Register LIFF")


# =============================================================================
# STAFF DASHBOARD
# =============================================================================

def _dashboard_logged_in() -> bool:
    return bool(session.get("dashboard_authed"))


@app.route("/dashboard/login", methods=["GET", "POST"])
def dashboard_login():
    if not DASHBOARD_PASSWORD:
        return (
            "Dashboard ยังไม่ได้ตั้งค่าระบบ",
            500,
        )

    error = None
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["dashboard_authed"] = True
            return redirect(url_for("dashboard_home"))
        error = "รหัสผ่านไม่ถูกต้อง"

    return render_template_string(_DASHBOARD_LOGIN_HTML, error=error)


@app.route("/dashboard/logout")
def dashboard_logout():
    session.pop("dashboard_authed", None)
    return redirect(url_for("dashboard_login"))


@app.route("/dashboard")
def dashboard_home():
    if not DASHBOARD_PASSWORD:
        return (
            "Dashboard ยังไม่ได้ตั้งค่าระบบ",
            500,
        )
    if not _dashboard_logged_in():
        return redirect(url_for("dashboard_login"))

    return _render_liff_page("dashboard.html", "Dashboard")


@app.route("/api/dashboard/data")
def api_dashboard_data():
    if not _dashboard_logged_in():
        return jsonify({"error": "unauthorized"}), 401

    try:
        sos_records = sheets_mgr.get_all_records("sos_requests") or []
        need_records = sheets_mgr.get_all_records("user_needs") or []
        user_records = sheets_mgr.get_all_records("users") or []
    except Exception as e:
        Logger.error("Dashboard", f"Sheets read error: {e}")
        sos_records, need_records, user_records = [], [], []

    def _is_pending(rec, status_field="status"):
        s = str(rec.get(status_field, "")).strip().upper()
        return s in ("", "PENDING", "NEW", "รอดำเนินการ")

    sos_pending = sum(1 for r in sos_records if _is_pending(r))
    need_pending = sum(1 for r in need_records if _is_pending(r))

    sos_sorted = list(reversed(sos_records))[:100]
    need_sorted = list(reversed(need_records))[:100]

    return jsonify({
        "stats": {
            "sos_total": len(sos_records),
            "sos_pending": sos_pending,
            "need_total": len(need_records),
            "need_pending": need_pending,
            "users_total": len(user_records),
        },
        "sos": sos_sorted,
        "needs": need_sorted,
    })


_DASHBOARD_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>เข้าสู่ระบบ - FLOODCARE Dashboard</title>
<style>
  :root{--bg:#F6F4EF;--surface:#FFFFFF;--ink:#15151A;--muted:#8C8980;--line:#EAE6DF;--primary:#2F6F8F;}
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Sukhumvit Set',sans-serif;background:var(--bg);
       min-height:100vh;display:flex;align-items:center;justify-content:center;color:var(--ink);}
  .box{background:var(--surface);border:1px solid var(--line);border-radius:24px;padding:40px 32px;width:320px;}
  h1{font-size:20px;font-weight:700;margin-bottom:6px;}
  p{font-size:13px;color:var(--muted);margin-bottom:24px;}
  input{width:100%;height:48px;border:1px solid var(--line);border-radius:12px;padding:0 14px;font-size:16px;margin-bottom:14px;}
  input:focus{outline:none;border-color:var(--primary);}
  button{width:100%;height:48px;border:none;border-radius:12px;background:var(--primary);color:#fff;font-size:16px;font-weight:600;cursor:pointer;}
  .error{color:#C2452F;font-size:13px;margin-bottom:14px;}
</style>
</head>
<body>
  <form class="box" method="post">
    <h1>FLOODCARE Dashboard</h1>
    <p>สำหรับเจ้าหน้าที่เท่านั้น</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <input type="password" name="password" placeholder="รหัสผ่าน" autofocus required>
    <button type="submit">เข้าสู่ระบบ</button>
  </form>
</body>
</html>
"""


# =============================================================================
# API SUBMISSIONS (With Bangkok Time Integration)
# =============================================================================

@app.route("/api/sos/submit", methods=['POST'])
@_require_liff_auth(SOS_LIFF_ID)
def api_sos_submit():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        verified_uid = g.get("verified_user_id")
        user_id = verified_uid or data.get("user_id") or data.get("userId") or "unknown"

        case_id = generate_case_id()
        timestamp = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")

        success = sheets_mgr.batch_append("sos_requests", [[
            case_id,
            user_id,
            timestamp,
            data.get("latitude", "0"),
            data.get("longitude", "0"),
            data.get("water_level_status", "-"),
            data.get("victim_count", "1"),
            data.get("vulnerable_groups", ""),
            data.get("group_types", ""),
            data.get("urgency_level", "ต่ำ"),
            data.get("details", "-"),
            data.get("photo_url", "-"),
            data.get("priority", "NORMAL"),
            "OPEN",
            "-", "-", "-", "-"
        ]])
        
        Logger.info("SOS_API", f"Submitted case {case_id}")

        if success:
            _push_save_confirmation(
                user_id,
                f"✅ บันทึกข้อมูลแจ้งเหตุเรียบร้อยแล้วครับ\n"
                f"เลขเคส: {case_id}\n\n"
                f"ทีมงานได้รับแจ้งเหตุอุทกภัยฉุกเฉินแล้ว กำลังประสานงานติดต่อกลับโดยเร็วที่สุดครับ "
                f"หากสถานการณ์เปลี่ยนแปลงหรือทวีความรุนแรงขึ้น พิมพ์ 'sos' เพื่อขอแบบฟอร์มส่งข้อมูลซ้ำได้ครับ"
            )

        return jsonify({"success": success, "case_id": case_id})
    except Exception as e:
        Logger.error("SOS_API", f"Submit error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/need/submit", methods=['POST'])
@_require_liff_auth(NEED_LIFF_ID)
def api_need_submit():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        verified_uid = g.get("verified_user_id")
        user_id = verified_uid or data.get("user_id") or data.get("userId") or "unknown"

        need_id = generate_need_id()
        timestamp = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")
        
        success = sheets_mgr.batch_append("user_needs", [[
            need_id,
            timestamp,
            user_id,
            data.get("latitude", "0"),
            data.get("longitude", "0"),
            data.get("categories", ""),
            data.get("details", "-"),
            data.get("urgency", "ไม่ด่วน"),
            "PENDING",
            data.get("halal", "FALSE"),
            "-", "-"
        ]])
        
        Logger.info("Need_API", f"Submitted need {need_id}")

        if success:
            _push_save_confirmation(
                user_id,
                f"✅ บันทึกข้อมูลความต้องการสิ่งของเรียบร้อยแล้วครับ\n"
                f"เลขที่รายการ: {need_id}\n\n"
                f"ทีมอาสาสมัครจะประสานจัดส่งสิ่งของบรรเทาทุกข์ให้เร็วที่สุดตามลำดับความเร่งด่วนครับ"
            )

        return jsonify({"success": success, "need_id": need_id})
    except Exception as e:
        Logger.error("Need_API", f"Submit error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/register/submit", methods=['POST'])
@_require_liff_auth(REGISTER_LIFF_ID)
def api_register_submit():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"}), 400

        verified_uid = g.get("verified_user_id")
        user_id = verified_uid or data.get("user_id") or data.get("userId") or "unknown"

        first_name = (data.get("first_name") or "ผู้แจ้ง").strip()
        last_name = (data.get("last_name") or "ทั่วไป").strip()
        phone = "".join(filter(str.isdigit, data.get("phone", "")))
        if len(phone) < 9 or len(phone) > 10:
            return jsonify({"success": False, "error": "เบอร์โทรไม่ถูกต้อง"}), 400

        register_date = get_bangkok_time().strftime("%Y-%m-%d")

        success = sheets_mgr.batch_append("users", [[
            user_id,
            first_name,
            last_name,
            phone,
            data.get("province", "-") or "-",
            data.get("district", "-") or "-",
            data.get("sub_district", "-") or "-",
            data.get("latitude", "0"),
            data.get("longitude", "0"),
            data.get("member_count", "1"),
            data.get("emergency_contact", "-") or "-",
            "TRUE",
            "TRUE" if data.get("consent_pdpa") else "FALSE",
            register_date,
            "ACTIVE",
        ]])

        cache.sheets.delete("sheets:users")
        Logger.info("Register_API", f"Registered user {bot_config.hash_user_id(user_id)}")

        if success:
            _push_save_confirmation(
                user_id,
                f"✅ ลงทะเบียนข้อมูลสำเร็จเรียบร้อยแล้วครับ\n"
                f"ยินดีต้อนรับคุณ {first_name} {last_name} เข้าสู่ฐานข้อมูลระบบเฝ้าระวังภัยพิบัติอุทกภัย\n\n"
                f"พิมพ์ 'sos' เพื่อขอแบบฟอร์มกู้ภัย หรือพิมพ์ 'ขอของ' เพื่อส่งคำขอสิ่งของบรรเทาทุกข์ได้ตลอดเวลาครับ"
            )

        return jsonify({"success": success})
    except Exception as e:
        Logger.error("Register_API", f"Submit error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# DEBUG ENDPOINTS
# =============================================================================

@app.route("/health")
def health_check():
    return jsonify({
        "status": "ok",
        "service": "FLOODCARE AI",
        "timestamp": get_bangkok_time().isoformat(),
    }), 200


@app.route("/debug/status")
@_require_debug_key()
def debug_status():
    return jsonify({
        "system": "FLOODCARE AI v2.2",
        "timestamp": get_bangkok_time().isoformat(),
        "gemini_ready": bot_config._gemini_initialized if hasattr(bot_config, '_gemini_initialized') else False,
        "sheets_connected": sheets_mgr.get_client() is not None,
        "rate_limiter": {
            "max_requests": bot_config.RATE_LIMIT_REQUESTS,
            "window_seconds": bot_config.RATE_LIMIT_WINDOW
        },
        "cache": cache.all_stats(),
        "sessions": sessions.stats(),
    })


# =============================================================================
# MAIN SYSTEM START
# =============================================================================

def _startup_self_check():
    required_templates = ["sos_liff.html", "need_liff.html", "register_liff.html", "dashboard.html"]
    templates_dir = os.path.join(app.root_path, app.template_folder or "templates")

    Logger.info("Startup", f"Checking LIFF templates in: {templates_dir}")
    all_ok = True
    for name in required_templates:
        path = os.path.join(templates_dir, name)
        if os.path.exists(path):
            Logger.info("Startup", f"  ✓ found {name}")
        else:
            all_ok = False
            Logger.error(
                "Startup",
                f"  ✗ MISSING {name} — /liff/* or /dashboard will return HTTP 500 until this "
                f"file is deployed. Check that templates/{name} is committed to git and not "
                f"excluded by .gitignore."
            )

    if not all_ok:
        Logger.error(
            "Startup",
            "⚠️ One or more LIFF templates are missing — see lines above. "
            "The bot's chat features will still work; only the LIFF pages are affected."
        )
    else:
        Logger.info("Startup", "All LIFF templates found OK.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    Logger.info("System", f"Starting FLOODCARE AI on port {port}")
    _startup_self_check()
    app.run(host="0.0.0.0", port=port)
