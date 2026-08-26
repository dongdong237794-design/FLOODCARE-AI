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
    IntentClassifier, classify_intent_ai, classify_and_maybe_answer,
    compose_water_level_reply, compose_shelter_reply,
    # Time helper
    get_bangkok_time,
    # Utilities
    sanitize_text, extract_sheet_id, calculate_distance,
    generate_case_id, generate_need_id,
    # LINE
    line_bot_api, handler, show_loading_animation,
    # Flex builders
    build_ai_response_flex,
    build_language_selector_flex, build_water_level_flex_message,
    build_register_form_flex, build_snake_bite_flex, build_help_flex, build_full_guide_flex,
    build_weather_flex, build_faq_response_flex,
    build_shelter_flex_message, build_prep_guide_flex, build_accident_flex_message,
    MORE_SHELTERS_TRIGGERS,
    build_contact_flex_message, build_my_cases_flex_message,
    # Shelter data
    find_nearest_shelters,
    # Response handlers
    get_greeting_message, handle_emergency_response,
    calculate_sos_priority,
    # Services
    ask_gemini, ask_gemini_with_search, get_live_weather_scraper, get_live_weather_data,
    get_live_water_levels_from_api, sheets_mgr,
    assess_water_level_status, is_water_data_stale,
    SOS_STATUS_LABELS_TH, need_status_label_th,
    # Legacy state
    USER_STATES, USER_DATA, update_legacy_state,
    # Config
    SOS_LIFF_URL, NEED_LIFF_URL,
    SOS_LIFF_ID, NEED_LIFF_ID, TRACK_LIFF_ID,
    REGISTER_LIFF_URL, REGISTER_LIFF_ID,
    WATER_LEVEL_SOURCE_URL, SNAKE_BITE_HOTLINE, SNAKE_BITE_INFO_URL, TMD_SOURCE_URL,
    DASHBOARD_PASSWORD, FLASK_SECRET_KEY, DASHBOARD_API_KEY,
    LINE_CHANNEL_SECRET,
    WATER_DATA_MAX_AGE_MINUTES,
    GEMINI_API_KEY,
    PUBLIC_BASE_URL,
)

from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, LocationMessage, ImageMessage, FollowEvent,
    TextSendMessage, QuickReply, QuickReplyButton, LocationAction,
    MessageAction, URIAction, FlexSendMessage
)

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY or os.urandom(32)

SHELTER_NOT_FOUND_TEXT = {
    "TH": (
        "📍 ขออภัยครับ ตอนนี้ยังไม่พบศูนย์พักพิงในระบบที่อยู่ใกล้ตำแหน่งของคุณ\n\n"
        "เพื่อความปลอดภัย รบกวนติดต่อสายด่วน ปภ. 1784 เพื่อสอบถามจุดอพยพที่ใกล้ที่สุดในพื้นที่ได้เลยครับ "
        "น้องบอทจะรีบอัปเดตข้อมูลศูนย์พักพิงเพิ่มเติมโดยเร็วที่สุดครับ"
    ),
    "EN": (
        "📍 Sorry, we couldn't find any shelters in our system near your location right now.\n\n"
        "For your safety, please call the DDPM hotline 1784 to ask about the nearest evacuation "
        "point in your area. We'll update our shelter database as soon as possible."
    ),
    "MY": (
        "📍 Maaf, tiada tempat perlindungan dijumpai berhampiran lokasi anda buat masa ini.\n\n"
        "Demi keselamatan anda, sila hubungi talian hotline DDPM 1784 untuk bertanya lokasi "
        "pemindahan terdekat di kawasan anda. Kami akan mengemas kini pangkalan data secepat mungkin."
    ),
}


def _get_display_name(user_id: str) -> str:
    """
    Resolves the name to greet a user by:
    1. If they've registered (has a row in the 'users' sheet), use their
       registered first_name (+ last_name if present) — this is the name
       they gave us on purpose, so it takes priority.
    2. Otherwise, fall back to their LINE display name via get_profile().
    3. If both are unavailable (e.g. LINE API error, blocked bot), fall
       back to the generic "คุณ" as a last resort so the bot never breaks.
    """
    try:
        user_record = sheets_mgr.get_user_record(user_id)
        if user_record:
            first_name = str(user_record.get("first_name", "")).strip()
            last_name = str(user_record.get("last_name", "")).strip()
            full_name = f"{first_name} {last_name}".strip()
            if full_name:
                return full_name
    except Exception as e:
        Logger.error("DisplayName", f"users sheet lookup failed: {e}")

    try:
        profile = line_bot_api.get_profile(user_id)
        if profile and profile.display_name:
            return profile.display_name
    except Exception as e:
        Logger.error("DisplayName", f"get_profile failed: {e}")

    return "คุณ"

# Exact-match whitelist used to detect Rich Menu taps, Quick Reply buttons,
# and literal typed commands (e.g. "สภาพอากาศ", "ศูนย์พักพิง") — these bypass
# AI intent analysis entirely and keep using the old deterministic
# keyword classifier + existing Flex Message UI, unchanged. Only messages
# that are NOT an exact match (i.e. real free-text sentences) go through
# the new AI intent analysis.
_ALL_KEYWORD_TRIGGERS = set()
for _kw_list in IntentClassifier.PATTERNS.values():
    for _kw in _kw_list:
        _ALL_KEYWORD_TRIGGERS.add(_kw.strip().lower())


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


def _push_save_confirmation(user_id: Optional[str], message: str, track_id: Optional[str] = None) -> None:
    if not user_id or user_id == "unknown":
        Logger.info("Push", "Skipped confirmation push — no verified user_id")
        return
    try:
        quick_reply = None
        if track_id:
            base = PUBLIC_BASE_URL or "https://floodcare-ai-2.onrender.com"
            track_url = f"{base}/liff/track?id={track_id}"
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=URIAction(label="ติดตามเคสนี้", uri=track_url))
            ])
        line_bot_api.push_message(user_id, TextSendMessage(text=message, quick_reply=quick_reply))
    except Exception as e:
        Logger.info("Push", f"Failed to send save-confirmation: {e}")


def broadcast_alert_to_all_users(message: str) -> dict:
    """
    Admin-triggered flood alert broadcast (dashboard button, not automatic).
    Pulls every distinct user_id from the 'users' sheet and pushes the
    message via LINE's multicast API, chunked at 500 recipients per call
    since that's LINE's hard limit per multicast request.
    Returns {"sent": n, "failed_chunks": n} so the dashboard can show a
    clear result instead of a silent success/failure.
    """
    if not line_bot_api:
        return {"sent": 0, "failed_chunks": 0, "error": "line_bot_api not configured"}

    try:
        users = sheets_mgr.get_all_records("users")
    except Exception as e:
        Logger.error("Broadcast", f"Failed to load users sheet: {e}")
        return {"sent": 0, "failed_chunks": 0, "error": "failed_to_load_users"}

    user_ids = sorted({
        str(u.get("user_id", "")).strip() for u in users
        if str(u.get("user_id", "")).strip() and str(u.get("user_id", "")).strip() != "unknown"
    })
    if not user_ids:
        return {"sent": 0, "failed_chunks": 0, "error": "no_users"}

    sent, failed_chunks = 0, 0
    chunk_size = 500  # LINE multicast hard limit per call
    for i in range(0, len(user_ids), chunk_size):
        chunk = user_ids[i:i + chunk_size]
        try:
            line_bot_api.multicast(chunk, TextSendMessage(text=message))
            sent += len(chunk)
        except Exception as e:
            failed_chunks += 1
            Logger.error("Broadcast", f"multicast chunk failed ({len(chunk)} recipients): {e}")

    Logger.info("Broadcast", f"Alert sent to {sent}/{len(user_ids)} users ({failed_chunks} chunk(s) failed)")
    return {"sent": sent, "total_users": len(user_ids), "failed_chunks": failed_chunks}


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
        display_name = _get_display_name(user_id)
        messages = [get_greeting_message(display_name)]
        if not already_registered:
            messages.append(build_register_form_flex(display_name))
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
        if lang in ("TH", "MY", "EN"):
            session.language = lang
            try:
                sheets_mgr.update_row_by_id("users", "user_id", user_id, {"preferred_language": lang})
            except Exception as e:
                Logger.error("Language", f"Could not persist preferred_language: {e}")
            line_bot_api.reply_message(event.reply_token, get_greeting_message(_get_display_name(user_id), lang))
        else:
            lang_names = {"JP": "日本語"}
            reply_text = (
                f"ขออภัยครับ {lang_names.get(lang, lang)} ยังอยู่ระหว่างการพัฒนา "
                f"ตอนนี้ระบบให้บริการเต็มรูปแบบในภาษาไทย มลายู และอังกฤษครับ"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        return
    
    if user_text in MORE_SHELTERS_TRIGGERS.values():
        pending = session.data.get("more_shelters") or []
        if not pending:
            no_more_text = {
                "TH": "ไม่มีศูนย์พักพิงเพิ่มเติมในระบบแล้วครับ",
                "EN": "There are no more shelters in the system.",
                "MY": "Tiada lagi tempat perlindungan dalam sistem.",
            }.get(session.language, "ไม่มีศูนย์พักพิงเพิ่มเติมในระบบแล้วครับ")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=no_more_text))
            return
        shown, remaining = pending[:3], pending[3:]
        flex_msg = build_shelter_flex_message(None, None, shown, lang=session.language, more_count=len(remaining))
        line_bot_api.reply_message(event.reply_token, flex_msg)
        session.data["more_shelters"] = remaining
        session.updated_at = time.time()
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
    # Exact matches of a known menu/button keyword (Rich Menu taps, Quick
    # Reply buttons, or literally typing "สภาพอากาศ") keep the old
    # deterministic keyword path so their behavior/Flex UI never changes.
    # Any other free-text sentence is analyzed by AI instead of guessing
    # from keywords, per the updated spec.
    text_clean_for_menu = user_text.strip().lower().strip("!.,😊🙏👋🆘 ")
    is_menu_trigger = text_clean_for_menu in _ALL_KEYWORD_TRIGGERS
    pre_answer = None  # set below if the combined call already drafted a usable answer

    if is_menu_trigger:
        intent, confidence = IntentClassifier.classify(user_text)
        scope = "MENU"
    else:
        # Try the single-round-trip combined call first (classify + draft
        # answer together) — cuts typical latency roughly in half for FAQ/
        # AI_QUERY messages. Falls back to the original two-step flow below
        # if the model's output couldn't be parsed for any reason.
        combined = classify_and_maybe_answer(user_text, session.language)
        if combined:
            intent = combined.get("intent", "AI_QUERY")
            confidence = combined.get("confidence", 0.5)
            scope = combined.get("scope", "GENERAL")
            if combined.get("answer"):
                pre_answer = combined
        else:
            ai_result = classify_intent_ai(user_text)
            intent = ai_result.get("intent", "AI_QUERY")
            confidence = ai_result.get("confidence", 0.5)
            scope = ai_result.get("scope", "GENERAL")

    session.last_intent = intent

    Logger.info(
        "Intent",
        f"Classified as {intent} (confidence: {confidence}, scope: {scope}, "
        f"via: {'menu_keyword' if is_menu_trigger else ('combined' if pre_answer else 'ai')})",
        {"user": bot_config.hash_user_id(user_id)}
    )
    
    # SNAKE BITE (First aid)
    if intent == "SNAKE_BITE":
        line_bot_api.reply_message(event.reply_token, build_snake_bite_flex())
        return

    # ACCIDENT (อุบัติเหตุ/บาดเจ็บทั่วไป เช่น ขาหัก แขนหัก รถชน — ไม่ใช่เหตุน้ำท่วมโดยตรง)
    if intent == "ACCIDENT":
        line_bot_api.reply_message(event.reply_token, build_accident_flex_message())
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
        greeting_msg = get_greeting_message(_get_display_name(user_id), session.language)
        line_bot_api.reply_message(event.reply_token, greeting_msg)
        return

    # HELP / CAPABILITIES MENU (cover card — invites the tap that opens the full guide below)
    if intent == "HELP":
        line_bot_api.reply_message(event.reply_token, build_help_flex(lang=session.language))
        return

    # FULL_GUIDE — the actual detailed manual, opened from the cover card's button.
    # Previously build_full_guide_flex() was defined but never wired to anything,
    # so tapping "เปิดคู่มือ"/"open guide" fell through to the AI fallback instead
    # of showing the guide it promised.
    if intent == "FULL_GUIDE":
        line_bot_api.reply_message(event.reply_token, build_full_guide_flex(lang=session.language))
        return

    # FAQ (Google search with grounding)
    if intent == "FAQ":
        _handle_faq_query(event, user_id, user_text, timestamp, pre_answer=pre_answer)
        return
    
    # CONTACT
    if intent == "CONTACT":
        _handle_contact_request(event)
        return
    
    # SHELTER
    if intent == "SHELTER":
        if scope == "GENERAL":
            # Regional/overview question (e.g. "ภาคเหนือมีศูนย์พักพิงกี่ที่")
            # doesn't need the user's own location — answer directly.
            _handle_faq_query(event, user_id, user_text, timestamp, pre_answer=pre_answer)
        else:
            _handle_shelter_request(event, user_id, ai_mode=(scope == "NEARBY"))
        return
    
    # WATER LEVEL
    if intent == "WATER_LEVEL":
        if scope == "GENERAL":
            _handle_faq_query(event, user_id, user_text, timestamp, pre_answer=pre_answer)
        else:
            _handle_water_level_request(event, user_id, ai_mode=(scope == "NEARBY"))
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

    # TRACK -> Link to the case-tracking page
    if intent == "TRACK":
        _start_track_flow(event, user_id)
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
        _handle_ai_query(event, user_id, user_text, timestamp, pre_answer=pre_answer)
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

    if state == "waiting_shelter_location_ai":
        _process_shelter_search_ai(event, lat, lon, user_id)
        return
    
    if state == "waiting_water_location":
        _process_water_level(event, lat, lon, user_id, timestamp)
        return

    if state == "waiting_water_location_ai":
        _process_water_level_ai(event, lat, lon, user_id, timestamp)
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

def _reply_liff_link(event, header: str, body: str, button_label: str, liff_url: str):
    """
    Shared helper: sends a short text message — with the LIFF link shown as
    plain text (tappable in LINE) plus a Quick Reply button as a shortcut —
    that opens the given LIFF page directly. Used for SOS / Needs /
    Registration. Deliberately static/deterministic (no AI call) so these
    time-sensitive replies are instant and never blocked by an AI hiccup;
    now that the LIFF pages are fully self-contained, we no longer need an
    intermediate Flex "form" card or a chat-side registration gate either.
    """
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=URIAction(label=button_label, uri=liff_url))
    ])
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"{header}\n\n{body}\n{liff_url}", quick_reply=quick_reply)
    )


def _start_sos_flow(event, user_id):
    _reply_liff_link(
        event,
        header="🆘 แจ้งเหตุฉุกเฉิน SOS",
        body="กดปุ่มด้านล่างเพื่อเปิดแบบฟอร์มแจ้งเหตุฉุกเฉินได้เลยครับ",
        button_label="📋 เปิดแบบฟอร์ม SOS",
        liff_url=SOS_LIFF_URL
    )


def _start_needs_flow(event, user_id):
    _reply_liff_link(
        event,
        header="📦 ขอความช่วยเหลือเรื่องสิ่งของ",
        body="กดปุ่มด้านล่างเพื่อเปิดแบบฟอร์มขอความช่วยเหลือเรื่องสิ่งของได้เลยครับ",
        button_label="📋 เปิดแบบฟอร์มขอของ",
        liff_url=NEED_LIFF_URL
    )


def _start_registration(event, user_id):
    _reply_liff_link(
        event,
        header="📝 ลงทะเบียนข้อมูลของคุณ",
        body="กดปุ่มด้านล่างเพื่อเปิดแบบฟอร์มลงทะเบียนได้เลยครับ",
        button_label="📋 เปิดแบบฟอร์มลงทะเบียน",
        liff_url=REGISTER_LIFF_URL
    )


def _relative_time_th(timestamp_str: str) -> str:
    """Converts a stored 'YYYY-MM-DD HH:MM:SS' timestamp into a short Thai relative-time string."""
    try:
        ts = datetime.datetime.strptime(timestamp_str.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return timestamp_str or "-"
    delta = get_bangkok_time().replace(tzinfo=None) - ts
    seconds = delta.total_seconds()
    if seconds < 60:
        return "เมื่อสักครู่"
    if seconds < 3600:
        return f"{int(seconds // 60)} นาทีที่แล้ว"
    if seconds < 86400:
        return f"{int(seconds // 3600)} ชั่วโมงที่แล้ว"
    days = int(seconds // 86400)
    if days < 30:
        return f"{days} วันที่แล้ว"
    return ts.strftime("%d/%m/%Y")


PRIORITY_LABELS_TH = {"CRITICAL": "วิกฤต", "HIGH": "เร่งด่วน", "NORMAL": "ปกติ"}


def _start_track_flow(event, user_id):
    base = PUBLIC_BASE_URL or "https://floodcare-ai-2.onrender.com"

    # A user's SOS case may have been merged into a shared household case
    # filed by someone else in the same house — match on household_id too
    # (if they're registered) so they still see it as "their" case.
    user_record = sheets_mgr.get_user_record(user_id)
    household_id = user_record.get("household_id", "") if user_record else ""

    cases = []

    for r in sheets_mgr.get_all_records("sos_requests"):
        if str(r.get("user_id", "")) == user_id or (household_id and str(r.get("household_id", "")) == household_id):
            status = str(r.get("status", "OPEN")).upper()
            priority = str(r.get("priority", "NORMAL")).upper()
            people_count = r.get("people_count", "")
            tags = [PRIORITY_LABELS_TH.get(priority, priority)]
            if people_count:
                tags.append(f"{people_count} คน")
            cases.append({
                "case_id": r.get("request_id", ""),
                "kind": "sos",
                "status_label": SOS_STATUS_LABELS_TH.get(status, status),
                "date": _relative_time_th(str(r.get("timestamp", ""))),
                "summary": r.get("note", ""),
                "tags": tags,
                "_sort_key": str(r.get("timestamp", "")),
            })

    for r in sheets_mgr.get_all_records("user_needs"):
        if str(r.get("user_id", "")) == user_id:
            categories_raw = str(r.get("categories", "")).strip()
            tags = [t.strip() for t in categories_raw.split(",") if t.strip()][:4]
            cases.append({
                "case_id": r.get("need_id", ""),
                "kind": "need",
                "status_label": need_status_label_th(r.get("status", "")),
                "date": _relative_time_th(str(r.get("timestamp", ""))),
                "summary": r.get("details", "") or categories_raw,
                "tags": tags,
                "_sort_key": str(r.get("timestamp", "")),
            })

    cases.sort(key=lambda c: c["_sort_key"], reverse=True)

    if not cases:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="ยังไม่พบเคสที่คุณเคยแจ้งไว้ในระบบครับ ถ้าเพิ่งแจ้งไปหมาดๆ ลองรออีกสักครู่แล้วพิมพ์ 'ติดตามเคส' อีกครั้งได้ครับ\n\n"
                     f"หรือถ้ามีเลขเคสของคนอื่นที่อยากเช็คแทน ค้นหาได้ที่ {base}/liff/track"
            )
        )
        return

    line_bot_api.reply_message(
        event.reply_token,
        [
            build_my_cases_flex_message(cases, base),
            TextSendMessage(text=f"มีเลขเคสของคนอื่นที่อยากเช็คแทนไหมครับ ค้นหาด้วยเลขเคสได้ที่ {base}/liff/track")
        ]
    )


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
    if not records:
        records = [
            {"Name": "ปภ. — แจ้งเหตุอุทกภัย", "Phone": "1784", "Role": "สายด่วนกรมป้องกันและบรรเทาสาธารณภัย 24 ชม."},
            {"Name": "สพฉ. — กู้ชีพฉุกเฉิน", "Phone": "1669", "Role": "เจ็บป่วย/อุบัติเหตุฉุกเฉินทางการแพทย์"},
            {"Name": "หน่วยกู้ชีพ", "Phone": "1554", "Role": "หน่วยกู้ชีพร่วมกตัญญู"},
            {"Name": "ตำรวจทางหลวง", "Phone": "1193", "Role": "เส้นทางที่ได้รับผลกระทบจากน้ำท่วม"},
        ]
    line_bot_api.reply_message(event.reply_token, build_contact_flex_message(records))


def _handle_shelter_request(event, user_id, ai_mode=False):
    session = sessions.get(user_id)
    state = "waiting_shelter_location_ai" if ai_mode else "waiting_shelter_location"
    data = {"original_question": event.message.text} if ai_mode else {}
    session.update(state=state, data=data)
    update_legacy_state(user_id, state, session.data)
    
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


def _handle_water_level_request(event, user_id, ai_mode=False):
    session = sessions.get(user_id)
    state = "waiting_water_location_ai" if ai_mode else "waiting_water_location"
    data = {"original_question": event.message.text} if ai_mode else {}
    session.update(state=state, data=data)
    update_legacy_state(user_id, state, session.data)
    
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

def _handle_faq_query(event, user_id, user_text, timestamp, pre_answer=None):
    lang = sessions.get(user_id).language

    if pre_answer and pre_answer.get("answer"):
        # Already drafted in the combined classify+answer call — skip the
        # second Gemini round-trip entirely.
        answer = pre_answer["answer"]
        sources = pre_answer.get("sources", [])
    else:
        show_loading_animation(user_id, loading_seconds=30)
        result = ask_gemini_with_search(user_text, max_tokens=600, lang=lang)
        answer = result.get("answer", "")
        sources = result.get("sources", [])

    if not answer:
        if lang == "MY":
            answer = "Maaf, tiada maklumat berkaitan bencana atau keselamatan untuk soalan ini. Sila cuba tanya semula."
        elif lang == "EN":
            answer = "Sorry, no disaster or safety information was found for this question. Please try asking again."
        else:
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


def _handle_ai_query(event, user_id, user_text, timestamp, pre_answer=None):
    lang = sessions.get(user_id).language

    if pre_answer and pre_answer.get("answer"):
        ai_response = pre_answer["answer"]
        sources = pre_answer.get("sources", [])
    else:
        show_loading_animation(user_id, loading_seconds=30)
        result = ask_gemini_with_search(
            "ประเมินตามกฎและเงื่อนไข System Instruction (น้องบอท): ตอบเฉพาะเรื่องน้ำท่วม สภาพอากาศ "
            "ความปลอดภัย และสุขภาพของผู้เจ็บป่วยหรือเครียดเท่านั้น ปฏิเสธเรื่องนอกขอบเขตอย่างสุภาพและอบอุ่น "
            "และตอบคำถามดังต่อไปนี้โดยไม่ใช้เครื่องหมายดอกจันเด็ดขาด:\n\n"
            f"คำถาม: {user_text}",
            max_tokens=600,
            lang=lang
        )
        ai_response = result.get("answer", "")
        sources = result.get("sources", [])

    if not ai_response:
        if lang == "MY":
            ai_response = "Maaf, sistem tidak tersedia buat masa ini. Jika kecemasan, sila hubungi 1784 dengan serta-merta."
        elif lang == "EN":
            ai_response = "The bot is temporarily unavailable. If this is an emergency, please call the DDPM hotline 1784 right away."
        else:
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
    lang = session.language

    # Fetch a larger pool once, but only show the first 3 up front (per the
    # agreed design) — the rest sit in session.data so a tap on the "see
    # more" bubble can page through them without re-querying location.
    shelters = find_nearest_shelters(lat, lon, limit=9)

    if not shelters:
        reply = SHELTER_NOT_FOUND_TEXT.get(lang, SHELTER_NOT_FOUND_TEXT["TH"])
        session.reset()
        update_legacy_state(user_id, "IDLE", {})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    shown, remaining = shelters[:3], shelters[3:]

    try:
        flex_msg = build_shelter_flex_message(lat, lon, shown, lang=lang, more_count=len(remaining))
        line_bot_api.reply_message(event.reply_token, flex_msg)
    except Exception as e:
        Logger.error("Shelter", f"Flex failed: {e}")
        lines = ["ศูนย์พักพิงใกล้คุณ:\n"]
        for sh in shown:
            lines.append(f"• {sh.get('Name', 'ไม่ระบุชื่อ')} (ห่าง {sh.get('distance_km', 0):.1f} กม.) — {sh.get('Status', 'เปิดรับ')}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines)))

    session.state = "IDLE"
    session.data = {"more_shelters": remaining, "more_shelters_lang": lang} if remaining else {}
    session.updated_at = time.time()
    update_legacy_state(user_id, "IDLE", {})


def _build_nearest_water_stations(lat, lon, limit=3):
    """
    Shared lookup used by both the Rich-Menu water-level flow (Flex Message)
    and the new AI-intent water-level flow (natural-language reply). 'Water_Levels'
    is kept fresh automatically by a background job that pulls from ThaiWater's
    API every 10 minutes (see water_level_refresh_loop in bot_config.py), so
    normal requests just read the sheet directly — fast, and doesn't hit
    ThaiWater on every single user request. Falls back to a direct live call
    if the sheet is empty (e.g. right at first boot) OR if the background
    job hasn't refreshed it in longer than WATER_DATA_MAX_AGE_MINUTES (e.g.
    ThaiWater's API has been down or sheet writes have been silently failing).
    """
    records = sheets_mgr.get_all_records("Water_Levels")
    source_label = "sheets"
    if not records:
        Logger.info("WaterLevel", "Water_Levels sheet empty — falling back to direct ThaiWater API call")
        records = get_live_water_levels_from_api()
        source_label = "live_api_fallback"
    elif is_water_data_stale():
        Logger.info("WaterLevel", f"Water_Levels sheet older than {WATER_DATA_MAX_AGE_MINUTES} min — falling back to direct ThaiWater API call")
        records = get_live_water_levels_from_api()
        source_label = "live_api_fallback_stale"
        source_label = "live_api_fallback"

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
                "stationCode": r.get("StationCode", ""),
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
    return stations[:limit]


def _water_record_to_public(r: dict, source_label: str) -> dict:
    """Shapes one Water_Levels row into the field names the Public Water Map / API consumers use."""
    return {
        "station_code": r.get("StationCode", ""),
        "station_name": r.get("Name", "ไม่ระบุ"),
        "river": r.get("River", ""),
        "province": r.get("Location", ""),
        "latitude": float(r.get("Lat", 0) or 0),
        "longitude": float(r.get("Lon", 0) or 0),
        "water_level": r.get("WaterLevel", "-"),
        "bank_level": r.get("BankLevel", "-"),
        "situation": r.get("Situation", "ปกติ"),
        "trend": r.get("Trend", "คงที่"),
        "measure_time": r.get("Time", "-"),
        "updated_at": r.get("Time", "-"),
        "source": source_label,
    }


def _load_water_records_for_public_api():
    """
    Same sheet-first / stale-fallback pattern as _build_nearest_water_stations,
    factored out so all 3 public water-station endpoints (list/nearest/detail)
    share one code path instead of three copies. Never calls ThaiWater from
    the browser — only this server-side function does, and only as a
    fallback when the sheet itself is empty or stale (spec §4.4 / §4.8).
    """
    records = sheets_mgr.get_all_records("Water_Levels")
    source_label = "sheets"
    if not records:
        records = get_live_water_levels_from_api()
        source_label = "live_api_fallback"
    elif is_water_data_stale():
        records = get_live_water_levels_from_api()
        source_label = "live_api_fallback_stale"
    return records, source_label


@app.route("/api/water-stations", methods=["GET"])
def api_water_stations_list():
    """
    All stations for the Public Water Map, sourced only from Water_Levels
    (never ThaiWater directly from a browser — spec §4.4/acceptance #2).
    Optional ?situation=มาก to filter by current status.
    """
    records, source_label = _load_water_records_for_public_api()
    situation_filter = request.args.get("situation", "").strip()
    out = []
    for r in records:
        try:
            item = _water_record_to_public(r, source_label)
            if item["latitude"] == 0 and item["longitude"] == 0:
                continue
            if situation_filter and item["situation"] != situation_filter:
                continue
            out.append(item)
        except (ValueError, TypeError):
            continue
    return jsonify({"success": True, "count": len(out), "stations": out, "source": source_label})


@app.route("/api/water-stations/nearest", methods=["GET"])
def api_water_stations_nearest():
    """?lat=&lon=&limit= — stations closest to a given point, for the "สถานีใกล้ฉัน" feature."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "lat and lon query params are required"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", 10)), 50))
    except (TypeError, ValueError):
        limit = 10

    stations = _build_nearest_water_stations(lat, lon, limit=limit)
    out = [{
        "station_code": s.get("stationCode", ""),
        "station_name": s.get("stationName"),
        "river": s.get("riverName"),
        "province": s.get("provinceName"),
        "latitude": s.get("latitude"),
        "longitude": s.get("longitude"),
        "water_level": (s.get("water_level") or {}).get("value"),
        "bank_level": s.get("bank_level"),
        "situation": s.get("situation"),
        "trend": s.get("trend"),
        "measure_time": s.get("measure_time"),
        "distance_km": round(s.get("distance_km", 0), 2),
        "source": s.get("source"),
    } for s in stations]
    return jsonify({"success": True, "count": len(out), "stations": out})


@app.route("/api/water-stations/<station_code>", methods=["GET"])
def api_water_station_detail(station_code):
    """Single-station lookup by StationCode, for a map popup's "ดูรายละเอียด" or a shared deep link."""
    records, source_label = _load_water_records_for_public_api()
    for r in records:
        if str(r.get("StationCode", "")).strip() == station_code.strip():
            return jsonify({"success": True, "station": _water_record_to_public(r, source_label)})
    return jsonify({"success": False, "error": "station_not_found"}), 404


@app.route("/map")
def public_water_map_page():
    """Public Water Map — no login required, same as the LIFF pages but reachable from any browser."""
    return render_template("water_map.html")


def _process_water_level(event, lat, lon, user_id, timestamp):
    session = sessions.get(user_id)
    show_loading_animation(user_id, loading_seconds=10)

    top_stations = _build_nearest_water_stations(lat, lon, limit=3)

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


def _process_water_level_ai(event, lat, lon, user_id, timestamp):
    """
    AI-intent path: same nearest-station lookup as the Rich-Menu flow
    (_build_nearest_water_stations — distances/values all pre-computed, the
    AI never calculates or guesses them), but the reply is a natural
    conversational sentence composed by Gemini instead of a Flex card, per
    spec item 5.
    """
    session = sessions.get(user_id)
    show_loading_animation(user_id, loading_seconds=10)

    original_question = session.data.get("original_question", "ระดับน้ำใกล้ตัวผู้ใช้")
    top_stations = _build_nearest_water_stations(lat, lon, limit=3)

    reply_text = compose_water_level_reply(original_question, top_stations)
    if not reply_text:
        reply_text = "ขออภัยครับ ระบบไม่สามารถสรุปข้อมูลระดับน้ำได้ในขณะนี้ ลองพิมพ์ 'เช็คน้ำ' อีกครั้งได้ครับ"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    session.reset()
    update_legacy_state(user_id, "IDLE", {})


def _process_shelter_search_ai(event, lat, lon, user_id):
    """
    AI-intent path for shelters — reuses find_nearest_shelters() (same
    Google Sheets lookup + distance calculation as the Rich-Menu flow), then
    has Gemini phrase the result as a natural sentence instead of a Flex card.
    """
    session = sessions.get(user_id)
    original_question = session.data.get("original_question", "ศูนย์พักพิงใกล้ตัวผู้ใช้")

    shelters = find_nearest_shelters(lat, lon, limit=3)
    reply_text = compose_shelter_reply(original_question, shelters)
    if not reply_text:
        reply_text = "ขออภัยครับ ระบบไม่สามารถสรุปข้อมูลศูนย์พักพิงได้ในขณะนี้ ลองพิมพ์ 'ศูนย์พักพิง' อีกครั้งได้ครับ"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

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
        elif "track" in template_name:
            liff_id = TRACK_LIFF_ID

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


@app.route("/liff/track")
def track_liff_page():
    return _render_liff_page("track_liff.html", "Track LIFF")


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
            rec["reporter_phone"] = bot_config.format_phone_th(rec.get("phone", "-") or "-")
        else:
            u = users_by_id.get(str(rec.get("user_id", "")), {})
            first = u.get("first_name", "") or ""
            last = u.get("last_name", "") or ""
            rec["reporter_name"] = f"{first} {last}".strip() or "ไม่ระบุชื่อ"
            rec["reporter_phone"] = bot_config.format_phone_th(u.get("phone", "-") or "-")

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
            "phone": bot_config.format_phone_th(user.get("phone", "-") or "-"),
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

    if responder_name != "-" and new_status == "IN_PROGRESS":
        existing = sheets_mgr.get_all_records("sos_requests")
        current = next((r for r in existing if str(r.get("request_id", "")) == case_id), None)
        current_responder = str((current or {}).get("responder_name", "") or "").strip()
        if current_responder and current_responder not in ("-", responder_name):
            return jsonify({
                "success": False, "error": "already_claimed",
                "responder_name": current_responder
            }), 409

    case_record = sheets_mgr.update_sos_status(case_id, new_status, responder_name)
    if not case_record:
        return jsonify({"success": False, "error": "case_not_found_or_sheet_error"}), 404

    # Notify the reporting user on LINE so they know someone is on the way.
    reporter_id = case_record.get("user_id")
    if reporter_id and reporter_id != "unknown":
        if new_status == "IN_PROGRESS":
            notify_text = (
                f"อัปเดตเคส {case_id}\n\n"
                f"ทีมกู้ภัยได้รับเรื่องและกำลังเดินทางไปช่วยเหลือคุณแล้วครับ\n"
                f"กรุณาอยู่ในที่ปลอดภัยและรอการติดต่อจากเจ้าหน้าที่"
            )
            _push_save_confirmation(reporter_id, notify_text, track_id=case_id)
        elif new_status == "CLOSED":
            notify_text = (
                f"เคส {case_id} เสร็จสิ้นแล้ว\n\n"
                f"ทีมงานได้ปิดเคสของคุณเรียบร้อยแล้วครับ หากยังต้องการความช่วยเหลือเพิ่มเติม "
                f"พิมพ์ 'sos' เพื่อแจ้งเหตุใหม่ได้ทันทีครับ"
            )
            _push_save_confirmation(reporter_id, notify_text, track_id=case_id)
        # Mark this status as already-notified so the background
        # status_notification_loop (bot_config.py) doesn't detect the same
        # change again on its next 3-minute sweep and push a duplicate.
        sheets_mgr.update_row_by_id("sos_requests", "request_id", case_id, {"last_notified_status": new_status})

    return jsonify({"success": True, "case_id": case_id, "status": new_status})


@app.route("/api/dashboard/alerts/broadcast", methods=["POST"])
def api_dashboard_broadcast_alert():
    """
    Admin manually composes and sends a flood-alert message to every
    registered user — nothing automatic here by design (see feature
    discussion): the admin decides what to say and when to send it.
    """
    if not _dashboard_api_authed():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": "empty_message"}), 400
    if len(message) > 1000:
        return jsonify({"success": False, "error": "message_too_long"}), 400

    result = broadcast_alert_to_all_users(f"📢 แจ้งเตือนจากทีมงาน FLOODCARE AI\n\n{message}")
    if result.get("error"):
        return jsonify({"success": False, **result}), 500
    return jsonify({"success": True, **result})


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
        "Beds": "TRUE" if data.get("beds") else "-",
        "Toilets": "TRUE" if data.get("toilets") else "-",
        "Parking": "TRUE" if data.get("parking") else "-",
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

    volunteer_name = (data.get("volunteer_name") or "").strip() or None

    # If this is a claim (PENDING -> IN_PROGRESS with a volunteer name attached),
    # guard against two volunteers tapping "รับเคส" on the same request at
    # nearly the same time — whoever's claim actually lands in the sheet
    # first wins; the second tap is told it's already taken instead of
    # silently overwriting the first volunteer's name.
    if volunteer_name and new_status.upper() == "IN_PROGRESS":
        existing = sheets_mgr.get_all_records("user_needs")
        current = next((r for r in existing if str(r.get("need_id", "")) == need_id), None)
        current_volunteer = str((current or {}).get("volunteer_name", "") or "").strip()
        if current_volunteer and current_volunteer not in ("-", volunteer_name):
            return jsonify({
                "success": False, "error": "already_claimed",
                "volunteer_name": current_volunteer
            }), 409

    record = sheets_mgr.update_need_status(need_id, new_status, volunteer_name)
    if not record:
        return jsonify({"success": False, "error": "need_not_found_or_sheet_error"}), 404

    return jsonify({"success": True, "need_id": need_id, "status": new_status, "volunteer_name": volunteer_name or "-"})


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

        # Force a fresh (uncached) registration check for SOS specifically —
        # this is the highest-stakes gate in the app, so we don't want a
        # stale 5-minute cache of the 'users' sheet to be able to affect it
        # either way.
        cache.sheets.delete("sheets:users")
        user_record = sheets_mgr.get_user_record(user_id)
        Logger.info("SOS_API", f"Registration check for user_id={user_id}: {'FOUND' if user_record else 'NOT FOUND'}")

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
        new_priority_client = str(data.get("priority", "NORMAL")).strip().upper()
        PRIORITY_RANK = {"NORMAL": 1, "HIGH": 2, "CRITICAL": 3}
        if new_priority_client not in PRIORITY_RANK:
            new_priority_client = "NORMAL"

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

        # Server-side priority check: never trust the LIFF form's priority
        # value alone (a client bug or bad network retry could under-report
        # it) — recompute from the actual submitted household details and
        # take whichever of the two is more urgent.
        group_types_for_priority = []
        if children_flag == "ใช่":
            group_types_for_priority.append("เด็ก")
        if elderly_flag == "ใช่":
            group_types_for_priority.append("ผู้สูงอายุ")
        if bedridden_flag == "ใช่":
            group_types_for_priority.append("ผู้ป่วยติดเตียง")
        urgency_signal = f"{data.get('water_level_status', '')} {data.get('details', '')}"
        _, computed_priority = calculate_sos_priority(group_types_for_priority, urgency_signal)

        new_priority = (
            new_priority_client
            if PRIORITY_RANK.get(new_priority_client, 1) >= PRIORITY_RANK.get(computed_priority, 1)
            else computed_priority
        )

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
                    f"ได้รับข้อมูลแจ้งเหตุแล้วครับ\n"
                    f"เลขเคส: {case_id} (รวมกับรายงานของสมาชิกในบ้านเดียวกันที่แจ้งไว้ก่อนหน้านี้)\n\n"
                    f"ระบบตรวจพบว่ามีรายงานเหตุจากบ้าน/ห้องเดียวกันอยู่แล้วในระบบ จึงรวมข้อมูลเป็นเคสเดียวกันเพื่อไม่ให้ทีมกู้ภัยได้รับแจ้งซ้ำซ้อน "
                    f"ทีมงานกำลังประสานงานติดต่อกลับโดยเร็วที่สุดครับ",
                    track_id=case_id
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
        
        Logger.info("SOS_API", f"Submitted case {case_id} for user_id={user_id}")

        if success:
            _push_save_confirmation(
                user_id,
                f"บันทึกข้อมูลแจ้งเหตุเรียบร้อยแล้วครับ\n"
                f"เลขเคส: {case_id}\n\n"
                f"ทีมงานได้รับแจ้งเหตุอุทกภัยฉุกเฉินแล้ว กำลังประสานงานติดต่อกลับโดยเร็วที่สุดครับ "
                f"หากสถานการณ์เปลี่ยนแปลงหรือทวีความรุนแรงขึ้น พิมพ์ 'sos' เพื่อขอแบบฟอร์มส่งข้อมูลซ้ำได้ครับ",
                track_id=case_id
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
            "phone": bot_config.format_phone_th(user_record.get("phone", "-") or "-"),
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
                f"บันทึกข้อมูลความต้องการสิ่งของเรียบร้อยแล้วครับ\n"
                f"เลขที่รายการ: {need_id}\n\n"
                f"ทีมอาสาสมัครจะประสานจัดส่งสิ่งของบรรเทาทุกข์ให้เร็วที่สุดตามลำดับความเร่งด่วนครับ",
                track_id=need_id
            )

        return jsonify({"success": success, "need_id": need_id})
    except Exception as e:
        Logger.error("Need_API", f"Submit error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/my-cases", methods=["GET"])
@_require_liff_auth(TRACK_LIFF_ID)
def api_my_cases():
    """
    Lists the requesting LINE user's own SOS + need-request cases, newest
    first, so /liff/track can show a tappable list instead of making them
    remember/paste a case ID. Only returns anything meaningful once
    TRACK_LIFF_ID is configured — with no LIFF ID, _require_liff_auth can't
    verify who's asking, so g.verified_user_id stays None and we return an
    empty list (frontend falls back to manual entry, unchanged).
    """
    user_id = getattr(g, "verified_user_id", None)
    if not user_id or user_id == "unknown":
        return jsonify({"success": True, "cases": []})

    cases = []
    for r in sheets_mgr.get_all_records("sos_requests"):
        if str(r.get("user_id", "")).strip() == user_id:
            status = str(r.get("status", "OPEN")).upper()
            cases.append({
                "case_id": r.get("request_id", ""), "type": "sos",
                "status_label": {"OPEN": "รอดำเนินการ", "IN_PROGRESS": "ทีมกำลังช่วยเหลือ", "CLOSED": "ช่วยเหลือสำเร็จ"}.get(status, status),
                "timestamp": r.get("timestamp", ""),
                "closed": status == "CLOSED",
            })
    for r in sheets_mgr.get_all_records("user_needs"):
        if str(r.get("user_id", "")).strip() == user_id:
            status = str(r.get("status", "PENDING")).upper()
            delivered = status in ("DELIVERED", "DONE", "COMPLETED")
            cases.append({
                "case_id": r.get("need_id", ""), "type": "need",
                "status_label": "ส่งมอบแล้ว" if delivered else ("กำลังจัดเตรียม" if status not in ("PENDING", "") else "รอดำเนินการ"),
                "timestamp": r.get("timestamp", ""),
                "closed": delivered,
            })

    cases.sort(key=lambda c: str(c.get("timestamp", "")), reverse=True)
    return jsonify({"success": True, "cases": cases})


@app.route("/api/track", methods=['GET'])
def api_track_case():
    """
    Self-serve case lookup for the /liff/track page — no LIFF auth needed,
    since the case_id itself (SOS-YYYYMMDD-XXXXXX / NEED-YYYYMMDD-XXXXXX,
    6 random hex chars) is the lookup key, the same trust model a shipping
    tracking number uses. Returns a normalized {steps: [...]} timeline the
    frontend renders directly, so SOS and Needs cases share one UI.
    """
    case_id = (request.args.get("id") or "").strip().upper()
    if not case_id:
        return jsonify({"success": False, "error": "missing_id"}), 400

    if case_id.startswith("SOS-"):
        records = sheets_mgr.get_all_records("sos_requests")
        rec = next((r for r in records if str(r.get("request_id", "")).upper() == case_id), None)
        if not rec:
            return jsonify({"success": False, "error": "not_found"}), 404

        status = str(rec.get("status", "OPEN")).upper()
        status_label = {"OPEN": "รอดำเนินการ", "IN_PROGRESS": "ทีมกำลังช่วยเหลือ", "CLOSED": "ช่วยเหลือสำเร็จ"}.get(status, status)
        in_progress_or_done = status in ("IN_PROGRESS", "CLOSED")
        done = status == "CLOSED"

        steps = [
            {"label": "แจ้งเหตุแล้ว", "done": True, "time": rec.get("timestamp", "-")},
            {"label": "ทีมกู้ภัยกำลังเดินทาง", "done": in_progress_or_done, "time": rec.get("accepted_at", "") if in_progress_or_done else ""},
            {"label": "ช่วยเหลือสำเร็จ", "done": done, "time": rec.get("completed_at", "") if done else ""},
        ]

        return jsonify({
            "success": True, "type": "sos", "case_id": case_id,
            "status": status, "status_label": status_label,
            "priority": rec.get("priority", "NORMAL"),
            "people_count": rec.get("people_count", "-"),
            "responder_name": rec.get("responder_name", "-"),
            "note": rec.get("note", "-"),
            "latitude": rec.get("latitude", ""), "longitude": rec.get("longitude", ""),
            "steps": steps,
        })

    elif case_id.startswith("NEED-"):
        records = sheets_mgr.get_all_records("user_needs")
        rec = next((r for r in records if str(r.get("need_id", "")).upper() == case_id), None)
        if not rec:
            return jsonify({"success": False, "error": "not_found"}), 404

        status = str(rec.get("status", "PENDING")).upper()
        delivered = status in ("DELIVERED", "DONE", "COMPLETED")
        in_progress = status not in ("PENDING", "")
        status_label = "ส่งมอบแล้ว" if delivered else ("กำลังจัดเตรียม" if in_progress else "รอดำเนินการ")

        steps = [
            {"label": "ส่งคำขอแล้ว", "done": True, "time": rec.get("timestamp", "-")},
            {"label": "อาสาสมัครกำลังจัดเตรียม", "done": in_progress or delivered, "time": ""},
            {"label": "ส่งมอบแล้ว", "done": delivered, "time": rec.get("delivered_at", "") if delivered else ""},
        ]

        return jsonify({
            "success": True, "type": "need", "case_id": case_id,
            "status": status, "status_label": status_label,
            "categories": rec.get("categories", "-"),
            "responder_name": rec.get("volunteer_name", "-"),
            "note": rec.get("details", "-"),
            "latitude": rec.get("latitude", ""), "longitude": rec.get("longitude", ""),
            "steps": steps,
        })

    else:
        return jsonify({"success": False, "error": "invalid_id_format"}), 400


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
            # Water Alert Engine opt-in — on by default (matches sms_enabled's
            # existing default-on convention); a future settings UI can flip
            # this off per-user. Radius left blank so the engine falls back
            # to the global WATER_ALERT_RADIUS_KM until per-user tuning exists.
            "water_alert_enabled": "TRUE",
            "water_alert_radius_km": "",
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


@app.route("/debug/water-alert/run-now", methods=["POST"])
@_require_debug_key()
def debug_run_water_alert_now():
    """
    Forces one full water-refresh + alert cycle immediately instead of
    waiting for the background loop's next 10-minute tick — for testing the
    Water Alert Engine on demand. Protected the same way as /debug/status
    (DEBUG_KEY). See the testing guide for how to use this alongside
    Water_Alert_State edits to simulate a station crossing into มาก/ล้นตลิ่ง.
    """
    stations = get_live_water_levels_from_api()
    if not stations:
        return jsonify({"success": False, "error": "ThaiWater fetch returned no stations"}), 502

    written = sheets_mgr.overwrite_water_levels(stations)
    if not written:
        return jsonify({"success": False, "error": "Failed to write Water_Levels sheet"}), 500

    result = bot_config.run_water_alert_engine(stations)
    return jsonify({
        "success": True,
        "stations_fetched": len(stations),
        "alert_result": result,
    })


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
    required_templates = ["sos_liff.html", "need_liff.html", "register_liff.html", "dashboard.html", "track_liff.html", "water_map.html"]
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
