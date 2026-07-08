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
    build_shelter_flex_message, build_prep_guide_flex,
    # Shelter data
    find_nearest_shelters,
    # Response handlers
    get_greeting_message, handle_emergency_response,
    build_sos_summary_text, build_needs_summary_text,
    calculate_sos_priority,
    # Services
    ask_gemini, ask_gemini_with_search, get_live_weather_scraper, get_live_weather_data,
    get_live_water_levels_from_api, sheets_mgr,
    assess_water_level_status, calculate_situation,
    # Legacy state
    USER_STATES, USER_DATA, update_legacy_state,
    # Config
    SOS_LIFF_URL, NEED_LIFF_URL,
    SOS_LIFF_ID, NEED_LIFF_ID,
    REGISTER_LIFF_URL, REGISTER_LIFF_ID,
    WATER_LEVEL_SOURCE_URL, SNAKE_BITE_HOTLINE, SNAKE_BITE_INFO_URL, TMD_SOURCE_URL,
    DASHBOARD_PASSWORD, FLASK_SECRET_KEY, DASHBOARD_API_KEY,
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
# LIFF API AUTH
# =============================================================================

import urllib.request as _urllib_request
import hmac as _hmac

def _verify_liff_token(id_token: str, liff_id: str) -> Optional[str]:
    if not id_token or not liff_id:
        return None
    if id_token.lower() in ("null", "undefined", ""):
        Logger.info("Auth", "id_token is null/undefined — LIFF may not be fully initialized")
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
        Logger.info("Auth", f"LIFF token verify failed: {e}")
        return None


def _require_liff_auth(expected_liff_id: str):
    from functools import wraps
    from flask import g

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not expected_liff_id:
                Logger.info("Auth", f"LIFF_ID not configured — skipping token verify for {fn.__name__}")
                g.verified_user_id = None
                return fn(*args, **kwargs)

            auth_header = request.headers.get("Authorization", "")

            if not auth_header.startswith("Bearer ") or auth_header == "Bearer ":
                try:
                    body_data = request.get_json(silent=True) or {}
                    fallback_uid = body_data.get("user_id", "")
                    if fallback_uid and fallback_uid != "unknown":
                        Logger.info(
                            "Auth",
                            f"No valid Authorization header for {fn.__name__} — "
                            f"accepting payload user_id (unverified)."
                        )
                        g.verified_user_id = fallback_uid
                        if not check_rate_limit(fallback_uid):
                            return jsonify({"success": False, "error": "Rate limit exceeded"}), 429
                        return fn(*args, **kwargs)
                except Exception:
                    pass
                Logger.security("Auth", "Missing Authorization header and no user_id in body", "unknown", {})
                return jsonify({"success": False, "error": "Unauthorized"}), 401

            id_token = auth_header[len("Bearer "):]
            user_id = _verify_liff_token(id_token, expected_liff_id)
            if not user_id:
                try:
                    body_data = request.get_json(silent=True) or {}
                    fallback_uid = body_data.get("user_id", "")
                    if fallback_uid and fallback_uid != "unknown":
                        Logger.info(
                            "Auth",
                            f"LIFF token verify failed for {fn.__name__} — "
                            f"accepting payload user_id (unverified)."
                        )
                        g.verified_user_id = fallback_uid
                        if not check_rate_limit(fallback_uid):
                            return jsonify({"success": False, "error": "Rate limit exceeded"}), 429
                        return fn(*args, **kwargs)
                except Exception:
                    pass
                Logger.security("Auth", "Invalid LIFF token and no fallback user_id", "unknown", {})
                return jsonify({"success": False, "error": "Unauthorized"}), 401

            if not check_rate_limit(user_id):
                return jsonify({"success": False, "error": "Rate limit exceeded"}), 429

            g.verified_user_id = user_id
            return fn(*args, **kwargs)
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
        already_registered = bool(sheets_mgr.get_user_record(user_id))
        messages = [get_greeting_message("คุณ")]
        if not already_registered:
            messages.append(build_register_form_flex("คุณ"))
        line_bot_api.reply_message(event.reply_token, messages)
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
            TextSendMessage(text="⏳ ขออภัยครับ ตอนนี้มีข้อความเข้ามาเยอะ รบกวนรอสักครู่แล้วลองส่งใหม่อีกครั้งนะครับ หากเป็นเหตุฉุกเฉินร้ายแรง โทร ปภ. 1784 ได้ทันทีครับ")
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
            TextSendMessage(text="❌ ยกเลิกขั้นตอนก่อนหน้าเรียบร้อยแล้วครับ พิมพ์ 'เมนู' หรือเลือกจากเมนูหลักเพื่อเริ่มใช้งานใหม่ได้ทันทีครับ")
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

    # PREP GUIDE (วิธีเตรียมตัวก่อนน้ำท่วม)
    if intent == "PREP_GUIDE":
        _handle_prep_guide_request(event, user_id)
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
            TextSendMessage(text="❌ ยกเลิกขั้นตอนก่อนหน้าเรียบร้อยแล้วครับ พิมพ์ 'เมนู' หรือเลือกจากเมนูหลักเพื่อเริ่มใช้งานใหม่ได้ทันทีครับ")
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
        TextSendMessage(text="🤔 ขออภัยครับ น้องบอทยังไม่เข้าใจข้อความนี้ ลองพิมพ์ใหม่อีกครั้ง หรือพิมพ์ 'เมนู' เพื่อดูสิ่งที่ช่วยได้ครับ หากอยู่ในสถานการณ์ฉุกเฉิน พิมพ์ 'ช่วยด้วย' หรือโทร ปภ. 1784 ได้ทันทีครับ")
    )


# =============================================================================
# LOCATION MESSAGE HANDLER
# =============================================================================

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
        TextSendMessage(text="📍 ได้รับพิกัดของคุณแล้วครับ ตอนนี้ยังไม่ได้อยู่ในขั้นตอนที่ต้องใช้พิกัด ลองพิมพ์ 'ศูนย์พักพิง' 'เช็คน้ำ' หรือ 'สภาพอากาศ' เพื่อค้นหาข้อมูลใกล้ตำแหน่งนี้ได้เลยครับ")
    )


# =============================================================================
# IMAGE MESSAGE HANDLER
# =============================================================================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    user_id = event.source.user_id
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="📸 ได้รับรูปภาพของคุณแล้วครับ หากต้องการแนบรูปเพื่อแจ้งเหตุฉุกเฉินหรือขอความช่วยเหลือเรื่องสิ่งของ กรุณากดเมนู 'SOS' หรือ 'ขอความช่วยเหลือเรื่องสิ่งของ' แล้วแนบรูปในแบบฟอร์มได้เลยครับ")
    )


# =============================================================================
# AUTOMATIC WEB FORM TRIGGERS (LIFF)
# =============================================================================

def _require_registered(event, user_id) -> bool:
    """
    Checks the 'users' sheet for this LINE user_id before letting them use
    SOS / ขอความช่วยเหลือ. If they haven't registered yet, sends a single
    card with a button that opens the registration link directly — no
    extra text message first, just one clear tap to get to the form.
    """
    user_record = sheets_mgr.get_user_record(user_id)
    if user_record:
        return True

    line_bot_api.reply_message(event.reply_token, build_register_form_flex("คุณ"))
    return False


def _start_sos_flow(event, user_id):
    if not _require_registered(event, user_id):
        return
    if not SOS_LIFF_URL:
        Logger.info("SOS", "SOS_LIFF_URL not configured")
    line_bot_api.reply_message(event.reply_token, build_sos_form_flex("คุณ"))


def _start_needs_flow(event, user_id):
    if not _require_registered(event, user_id):
        return
    if not NEED_LIFF_URL:
        Logger.info("Needs", "NEED_LIFF_URL not configured")
    line_bot_api.reply_message(event.reply_token, build_need_form_flex("คุณ"))


def _start_registration(event, user_id):
    if not REGISTER_LIFF_URL:
        Logger.info("Register", "REGISTER_LIFF_URL not configured")
    line_bot_api.reply_message(event.reply_token, build_register_form_flex("คุณ"))


def _handle_prep_guide_request(event, user_id):
    """
    'วิธีเตรียมตัวก่อนน้ำท่วม' — quantities (e.g. drinking water) are
    personalized using the user's registered household member_count so the
    checklist reflects their real household size instead of a generic number.
    """
    member_count = 1
    user_record = sheets_mgr.get_user_record(user_id)
    if user_record:
        try:
            member_count = max(1, int(str(user_record.get("member_count", "1")).strip() or 1))
        except (TypeError, ValueError):
            member_count = 1

    line_bot_api.reply_message(event.reply_token, build_prep_guide_flex(member_count))


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

    shelters = find_nearest_shelters(lat, lon, limit=5)

    if not shelters:
        reply = (
            "📍 ขออภัยครับ ตอนนี้ยังไม่พบศูนย์พักพิงในระบบที่อยู่ใกล้ตำแหน่งของคุณ\n\n"
            "เพื่อความปลอดภัย รบกวนติดต่อสายด่วน ปภ. 1784 เพื่อสอบถามจุดอพยพที่ใกล้ที่สุดในพื้นที่ได้เลยครับ "
            "น้องบอทจะรีบอัปเดตข้อมูลศูนย์พักพิงเพิ่มเติมโดยเร็วที่สุดครับ"
        )
        session.reset()
        update_legacy_state(user_id, "IDLE", {})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    flex_msg = build_shelter_flex_message(lat, lon, shelters)

    session.reset()
    update_legacy_state(user_id, "IDLE", {})
    line_bot_api.reply_message(event.reply_token, flex_msg)


def _process_water_level(event, lat, lon, user_id, timestamp):
    session = sessions.get(user_id)
    show_loading_animation(user_id, loading_seconds=10)

    # Pull LIVE data from ThaiWater's API first — this was previously dead
    # code that was never called, so the bot was always showing whatever
    # was last manually imported into the 'Water_Levels' sheet (which can go
    # stale for weeks). Only fall back to the sheet if the live call fails.
    records = get_live_water_levels_from_api()
    source_label = "live_api"
    if not records:
        Logger.info("WaterLevel", "Live ThaiWater API unavailable — falling back to Water_Levels sheet")
        records = sheets_mgr.get_all_records("Water_Levels")
        source_label = "sheets_fallback"

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
                "source": source_label
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

        return render_template(template_name, liff_id=liff_id, register_liff_id=REGISTER_LIFF_ID)
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


def _dashboard_api_authed() -> bool:
    """
    Auth check shared by the JSON dashboard API. Accepts EITHER:
      - the existing session-cookie login (used by the built-in /dashboard HTML page), or
      - an 'Authorization: Bearer <DASHBOARD_API_KEY>' header (used by the separately
        hosted React dashboard, which lives on a different origin and can't rely on cookies).
    """
    if _dashboard_logged_in():
        return True
    if DASHBOARD_API_KEY:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and auth_header[7:] == DASHBOARD_API_KEY:
            return True
    return False


@app.before_request
def _cors_preflight():
    # Lets a separately-hosted SPA (different domain/port) call /api/dashboard/*
    # with an Authorization header. Only this API surface is opened up — everything
    # else (webhook, LIFF forms) is untouched.
    if request.path.startswith("/api/dashboard") and request.method == "OPTIONS":
        return ("", 204)


@app.after_request
def _cors_headers(response):
    if request.path.startswith("/api/dashboard"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


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
    if not _dashboard_api_authed():
        return jsonify({"error": "unauthorized"}), 401

    try:
        sos_records = sheets_mgr.get_all_records("sos_requests") or []
        need_records = sheets_mgr.get_all_records("user_needs") or []
        user_records = sheets_mgr.get_all_records("users") or []
    except Exception as e:
        Logger.error("Dashboard", f"Sheets read error: {e}")
        sos_records, need_records, user_records = [], [], []

    try:
        shelter_records = bot_config.get_shelters_from_sheet()
    except Exception as e:
        Logger.error("Dashboard", f"Shelters read error: {e}")
        shelter_records = []

    def _is_pending(rec, status_field="status"):
        s = str(rec.get(status_field, "")).strip().upper()
        return s in ("", "PENDING", "NEW", "รอดำเนินการ")

    sos_pending = sum(1 for r in sos_records if _is_pending(r))
    need_pending = sum(1 for r in need_records if _is_pending(r))

    sos_sorted = list(reversed(sos_records))[:100]
    need_sorted = list(reversed(need_records))[:100]

    # --- Normalized data for the React dashboard (artifacts/floodcare-dashboard) ---
    users_by_id = {str(u.get("user_id", "")): u for u in user_records}

    # Attach the reporter's name/phone to each need request so the dashboard
    # shows a real name instead of a raw LINE user_id. Newer rows already
    # store first_name/last_name/phone directly (written at submit time);
    # older rows fall back to joining against the 'users' sheet by user_id.
    URGENCY_TH_TO_EN = {"ด่วนมาก": "CRITICAL", "ปานกลาง": "HIGH", "ไม่ด่วน": "NORMAL"}
    for rec in need_sorted:
        stored_first = (rec.get("first_name") or "").strip()
        stored_last = (rec.get("last_name") or "").strip()
        if stored_first or stored_last:
            rec["reporter_name"] = f"{stored_first} {stored_last}".strip()
            rec["reporter_phone"] = rec.get("phone", "-") or "-"
        else:
            u = users_by_id.get(str(rec.get("user_id", "")), {})
            first = u.get("first_name", "") or ""
            last = u.get("last_name", "") or ""
            rec["reporter_name"] = f"{first} {last}".strip() or "ไม่ระบุชื่อ"
            rec["reporter_phone"] = u.get("phone", "-") or "-"

        # The dashboard filters/labels by English enum (CRITICAL/HIGH/NORMAL);
        # the sheet stores the Thai text the LIFF form sends. Keep the sheet
        # value in 'urgency_text' and expose the normalized enum as 'urgency'.
        raw_urgency = (rec.get("urgency") or "").strip()
        rec["urgency_text"] = raw_urgency
        rec["urgency"] = URGENCY_TH_TO_EN.get(raw_urgency, "NORMAL")

        status_val = str(rec.get("status", "PENDING")).strip().upper()
        if status_val not in ("PENDING", "IN_PROGRESS", "DELIVERED"):
            status_val = "PENDING"
        rec["status"] = status_val

    def _num(val, default=0):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _has_keyword(text: str, keywords) -> bool:
        text = (text or "").lower()
        return any(k in text for k in keywords)

    sos_cases = []
    for rec in sos_sorted:
        user = users_by_id.get(str(rec.get("user_id", "")), {})
        status = str(rec.get("status", "OPEN")).strip().upper() or "OPEN"
        if status not in ("OPEN", "IN_PROGRESS", "CLOSED"):
            status = "OPEN"
        priority = str(rec.get("priority", "NORMAL")).strip().upper()
        if priority not in ("CRITICAL", "HIGH", "NORMAL"):
            priority = "NORMAL"

        try:
            people_count = int(float(rec.get("people_count", 1) or 1))
        except (TypeError, ValueError):
            people_count = 1

        def _is_yes(val):
            return str(val or "").strip() in ("ใช่", "YES", "yes", "TRUE", "true", "1")

        sos_cases.append({
            "request_id": rec.get("request_id", "-"),
            "first_name": user.get("first_name", "") or "ไม่ระบุชื่อ",
            "last_name": user.get("last_name", "") or "",
            "phone": user.get("phone", "-") or "-",
            "people_count": people_count,
            "bedridden": "YES" if _is_yes(rec.get("bedridden")) else "NO",
            "pets": "YES" if _is_yes(rec.get("pets")) else "NO",
            "water_level": rec.get("water_level", "-") or "-",
            "note": rec.get("note", "-") or "-",
            "priority": priority,
            "status": status,
            "latitude": _num(rec.get("latitude")),
            "longitude": _num(rec.get("longitude")),
            "timestamp": rec.get("timestamp", "-"),
        })

    shelters_out = []
    for s in shelter_records:
        capacity = int(s.get("Capacity", 0) or 0)
        occupancy = int(s.get("Occupancy", 0) or 0)
        pct = (occupancy / capacity * 100) if capacity > 0 else 0
        status_label = "เต็ม" if pct >= 100 else "ใกล้เต็ม" if pct >= 80 else "ว่าง"
        facilities_raw = s.get("Facilities", "-") or "-"
        facilities = [f.strip() for f in facilities_raw.split(",") if f.strip() and f.strip() != "-"]

        shelters_out.append({
            "id": s.get("ShelterID", "-"),
            "name": s.get("Name", "ไม่ระบุชื่อ"),
            "province": s.get("Province", "-"),
            "district": s.get("District", "-"),
            "subdistrict": s.get("Subdistrict", "-"),
            "latitude": s.get("Latitude", 0),
            "longitude": s.get("Longitude", 0),
            "capacity": capacity,
            "occupancy": occupancy,
            "status": status_label,
            "contact": "-",
            "facilities": facilities,
        })

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
        # Normalized data consumed by the React dashboard:
        "sos_cases": sos_cases,
        "shelters": shelters_out,
    })


@app.route("/api/dashboard/sos/<case_id>/status", methods=["POST"])
def api_dashboard_update_sos_status(case_id):
    if not _dashboard_api_authed():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    new_status = str(data.get("status", "")).strip().upper()
    if new_status not in ("IN_PROGRESS", "CLOSED", "OPEN"):
        return jsonify({"error": "invalid status"}), 400

    responder_name = data.get("responder_name", "-") or "-"
    case_record = sheets_mgr.update_sos_status(case_id, new_status, responder_name)
    if not case_record:
        return jsonify({"success": False, "error": "case_not_found_or_sheet_error"}), 404

    # Notify the reporting user on LINE so they know someone is on the way.
    reporter_id = case_record.get("user_id")
    if reporter_id and reporter_id != "unknown":
        if new_status == "IN_PROGRESS":
            notify_text = (
                f"📣 อัปเดตเคส {case_id}\n\n"
                f"ทีมกู้ภัยได้รับเรื่องและกำลังเดินทางไปช่วยเหลือคุณแล้วครับ 🚤\n"
                f"กรุณาอยู่ในที่ปลอดภัยและรอการติดต่อจากเจ้าหน้าที่"
            )
            _push_save_confirmation(reporter_id, notify_text)
        elif new_status == "CLOSED":
            notify_text = (
                f"✅ เคส {case_id} เสร็จสิ้นแล้ว\n\n"
                f"ทีมงานได้ปิดเคสของคุณเรียบร้อยแล้วครับ หากยังต้องการความช่วยเหลือเพิ่มเติม "
                f"พิมพ์ 'sos' เพื่อแจ้งเหตุใหม่ได้ทันทีครับ"
            )
            _push_save_confirmation(reporter_id, notify_text)

    return jsonify({"success": True, "case_id": case_id, "status": new_status})


@app.route("/api/dashboard/shelters", methods=["POST"])
def api_dashboard_create_shelter():
    """Adds a new evacuation shelter row — used by the 'เพิ่มศูนย์พักพิง' form
    in the React dashboard (artifacts/floodcare-dashboard)."""
    if not _dashboard_api_authed():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}

    shelter_id = (data.get("id") or "").strip()
    name = (data.get("name") or "").strip()
    if not shelter_id or not name:
        return jsonify({"success": False, "error": "id และ name จำเป็นต้องระบุ"}), 400

    try:
        latitude = float(data.get("latitude", 0) or 0)
        longitude = float(data.get("longitude", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "latitude/longitude ไม่ถูกต้อง"}), 400

    try:
        capacity = int(data.get("capacity", 0) or 0)
        occupancy = int(data.get("occupancy", 0) or 0)
    except (TypeError, ValueError):
        capacity, occupancy = 0, 0

    pct = (occupancy / capacity * 100) if capacity > 0 else 0
    status_label = "เต็ม" if pct >= 100 else "ใกล้เต็ม" if pct >= 80 else "ว่าง"

    facilities = data.get("facilities", [])
    facilities_str = ", ".join(facilities) if isinstance(facilities, list) else str(facilities or "-")

    success = sheets_mgr.append_row_by_headers("Shelters", {
        "ShelterID": shelter_id,
        "Name": name,
        "Province": data.get("province", "-") or "-",
        "District": data.get("district", "-") or "-",
        "Subdistrict": data.get("subdistrict", "-") or "-",
        "Latitude": latitude,
        "Longitude": longitude,
        "Capacity": capacity,
        "Occupancy": occupancy,
        "Status": status_label,
        "Beds": "-",
        "Toilets": "-",
        "Parking": "-",
        "Facilities": facilities_str or "-",
    })

    if success:
        cache.sheets.delete("sheets:Shelters")
        cache.sheets.delete("sheets:shelters:normalized")

    return jsonify({"success": success, "id": shelter_id})


@app.route("/api/dashboard/needs/<need_id>/status", methods=["POST"])
def api_dashboard_update_need_status(need_id):
    if not _dashboard_api_authed():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    new_status = str(data.get("status", "")).strip()
    if not new_status:
        return jsonify({"error": "invalid status"}), 400

    record = sheets_mgr.update_need_status(need_id, new_status)
    if not record:
        return jsonify({"success": False, "error": "need_not_found_or_sheet_error"}), 404

    return jsonify({"success": True, "need_id": need_id, "status": new_status})


@app.route("/api/dashboard/shelters/<shelter_id>/occupancy", methods=["POST"])
def api_dashboard_update_shelter_occupancy(shelter_id):
    if not _dashboard_api_authed():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    try:
        new_occupancy = int(data.get("occupancy", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid occupancy"}), 400
    if new_occupancy < 0:
        new_occupancy = 0

    record = sheets_mgr.update_shelter_occupancy(shelter_id, new_occupancy)
    if not record:
        return jsonify({"success": False, "error": "shelter_not_found_or_sheet_error"}), 404

    return jsonify({"success": True, "id": shelter_id, "occupancy": new_occupancy})



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
        user_id = verified_uid or data.get("user_id", "unknown")

        timestamp = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")

        # Look up this reporter's household (only active once a 'household_id'
        # column exists in both 'users' and 'sos_requests' — safely inert
        # otherwise) so SOS reports fired by different members of the same
        # household within a short window get merged into a single case.
        user_record = sheets_mgr.get_user_record(user_id)

        if not user_record:
            Logger.info("SOS_API", f"Blocked unregistered SOS submission from {user_id}")
            return jsonify({
                "success": False,
                "error": "กรุณาลงทะเบียนข้อมูลส่วนตัวก่อนแจ้งเหตุ SOS ครับ เพื่อให้ทีมกู้ภัยติดต่อกลับและช่วยเหลือคุณได้ถูกต้อง",
                "need_register": True,
                "emergency_hotline": "1784",
            }), 403

        household_id = (user_record or {}).get("household_id", "-") or "-"

        new_people_count = int(str(data.get("victim_count", "1")).strip() or 1)
        new_priority = data.get("priority", "NORMAL")
        PRIORITY_RANK = {"NORMAL": 1, "HIGH": 2, "CRITICAL": 3}

        # Split the selected vulnerable-group checkboxes into their own
        # yes/no columns — this is how the live 'sos_requests' sheet is
        # actually laid out (separate 'children' / 'elderly' / 'bedridden' /
        # 'pets' columns rather than one combined text field).
        groups_text = str(data.get("vulnerable_groups", "")).lower()
        def _flag(*keywords):
            return "ใช่" if any(k in groups_text for k in keywords) else "-"
        children_flag = _flag("เด็ก")
        elderly_flag = _flag("สูงอายุ", "คนชรา")
        bedridden_flag = _flag("ติดเตียง")
        pets_flag = _flag("สัตว์เลี้ยง", "สัตว์")

        merge_row, existing_case = sheets_mgr.find_open_case_by_household(household_id, window_minutes=45)

        if merge_row and existing_case:
            # --- Merge into the existing household case, no duplicate row ---
            case_id = existing_case.get("request_id", "-")

            try:
                combined_people = int(str(existing_case.get("people_count", "1")).strip() or 1) + new_people_count
            except ValueError:
                combined_people = new_people_count

            def _merge_flag(old_val: str, new_val: str) -> str:
                return "ใช่" if "ใช่" in (old_val, new_val) else "-"

            old_priority = str(existing_case.get("priority", "NORMAL")).upper()
            combined_priority = old_priority if PRIORITY_RANK.get(old_priority, 1) >= PRIORITY_RANK.get(new_priority, 1) else new_priority

            old_note = str(existing_case.get("note", "-") or "-")
            new_note = data.get("details", "-") or "-"
            combined_note = (
                old_note if old_note != "-" else ""
            ) + f"\n[+รายงานซ้ำจากครัวเรือนเดียวกัน {timestamp}] {new_note}"
            combined_note = combined_note.strip() or "-"

            success = sheets_mgr.merge_sos_case(merge_row, {
                "people_count": combined_people,
                "children": _merge_flag(existing_case.get("children", "-"), children_flag),
                "elderly": _merge_flag(existing_case.get("elderly", "-"), elderly_flag),
                "bedridden": _merge_flag(existing_case.get("bedridden", "-"), bedridden_flag),
                "pets": _merge_flag(existing_case.get("pets", "-"), pets_flag),
                "priority": combined_priority,
                "note": combined_note,
            })

            Logger.info("SOS_API", f"Merged household report into existing case {case_id} (household {household_id})")

            if success:
                _push_save_confirmation(
                    user_id,
                    f"✅ ได้รับข้อมูลแจ้งเหตุแล้วครับ\n"
                    f"เลขเคส: {case_id} (รวมกับรายงานของสมาชิกในบ้านเดียวกันที่แจ้งไว้ก่อนหน้านี้)\n\n"
                    f"ระบบตรวจพบว่ามีรายงานเหตุจากบ้าน/ห้องเดียวกันอยู่แล้วในระบบ จึงรวมข้อมูลเป็นเคสเดียวกันเพื่อไม่ให้ทีมกู้ภัยได้รับแจ้งซ้ำซ้อน "
                    f"ทีมงานกำลังประสานงานติดต่อกลับโดยเร็วที่สุดครับ"
                )

            return jsonify({"success": success, "case_id": case_id, "merged": True})

        # --- No existing open household case — create a new one ---
        case_id = generate_case_id()

        success = sheets_mgr.append_row_by_headers("sos_requests", {
            "request_id": case_id,
            "household_id": household_id,
            "user_id": user_id,
            "timestamp": timestamp,
            "latitude": data.get("latitude", "0"),
            "longitude": data.get("longitude", "0"),
            "people_count": new_people_count,
            "children": children_flag,
            "elderly": elderly_flag,
            "bedridden": bedridden_flag,
            "pets": pets_flag,
            "water_level": data.get("water_level_status", "-"),
            "note": data.get("details", "-"),
            "priority": new_priority,
            "status": "OPEN",
        })
        
        Logger.info("SOS_API", f"Submitted case {case_id}")

        if success:
            _push_save_confirmation(
                user_id,
                f"✅ บันทึกข้อมูลแจ้งเหตุเรียบร้อยแล้วครับ\n"
                f"เลขเคส: {case_id}\n\n"
                f"ทีมงานได้รับแจ้งเหตุอุทกภัยฉุกเฉินแล้ว กำลังประสานงานติดต่อกลับโดยเร็วที่สุดครับ "
                f"หากสถานการณ์เปลี่ยนแปลงหรือทวีความรุนแรงขึ้น พิมพ์ 'sos' เพื่อขอแบบฟอร์มส่งข้อมูลซ้ำได้ครับ"
            )

        return jsonify({"success": success, "case_id": case_id, "merged": False})
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
        user_id = verified_uid or data.get("user_id", "unknown")

        user_record = sheets_mgr.get_user_record(user_id)
        if not user_record:
            return jsonify({"success": False, "error": "กรุณาลงทะเบียนข้อมูลส่วนตัวก่อนขอความช่วยเหลือ", "need_register": True}), 403

        need_id = generate_need_id()
        timestamp = get_bangkok_time().strftime("%Y-%m-%d %H:%M:%S")
        
        success = sheets_mgr.append_row_by_headers("user_needs", {
            "need_id": need_id,
            "timestamp": timestamp,
            "user_id": user_id,
            "first_name": user_record.get("first_name", "") or "-",
            "last_name": user_record.get("last_name", "") or "-",
            "phone": user_record.get("phone", "-") or "-",
            "latitude": data.get("latitude", "0"),
            "longitude": data.get("longitude", "0"),
            "categories": data.get("categories", ""),
            "details": data.get("details", "-"),
            "urgency": data.get("urgency", "ไม่ด่วน"),
            "status": "PENDING",
            "halal_required": data.get("halal", "FALSE"),
            "volunteer_name": "-",
            "delivered_at": "-",
        })
        
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
        user_id = verified_uid or data.get("user_id", "unknown")

        first_name = (data.get("first_name") or "ผู้แจ้ง").strip()
        last_name = (data.get("last_name") or "ทั่วไป").strip()
        phone = "".join(filter(str.isdigit, data.get("phone", "")))
        if len(phone) < 9 or len(phone) > 10:
            return jsonify({"success": False, "error": "เบอร์โทรไม่ถูกต้อง"}), 400

        if not data.get("consent_pdpa"):
            return jsonify({"success": False, "error": "กรุณายินยอมนโยบายคุ้มครองข้อมูลส่วนบุคคล (PDPA) ก่อนบันทึกข้อมูล"}), 400

        housing_type = (data.get("housing_type") or "house").strip().lower()
        house_no = (data.get("house_no") or "").strip()
        condo_floor = (data.get("condo_floor") or "").strip()
        condo_room = (data.get("condo_room") or "").strip()

        if housing_type == "condo":
            if not condo_floor or not condo_room:
                return jsonify({"success": False, "error": "กรุณาระบุชั้นและห้องของคอนโด"}), 400
        else:
            if not house_no:
                return jsonify({"success": False, "error": "กรุณาระบุบ้านเลขที่"}), 400

        province = data.get("province", "-") or "-"
        district = data.get("district", "-") or "-"
        sub_district = data.get("sub_district", "-") or "-"

        household_id = bot_config.generate_household_id(
            province, district, sub_district, housing_type,
            house_no=house_no, condo_floor=condo_floor, condo_room=condo_room
        )

        register_date = get_bangkok_time().strftime("%Y-%m-%d")

        success = sheets_mgr.append_row_by_headers("users", {
            "user_id": user_id,
            "household_id": household_id,
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "housing_type": housing_type,
            "house_no": house_no or "-",
            "condo_floor": condo_floor or "-",
            "condo_room": condo_room or "-",
            "province": province,
            "district": district,
            "sub_district": sub_district,
            "gps_lat": data.get("latitude", "0"),
            "gps_lon": data.get("longitude", "0"),
            "member_count": data.get("member_count", "1"),
            "emergency_contact": data.get("emergency_contact", "-") or "-",
            "sms_enabled": "TRUE",
            "consent_pdpa": "TRUE" if data.get("consent_pdpa") else "FALSE",
            "register_date": register_date,
            "status": "ACTIVE",
        })

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
