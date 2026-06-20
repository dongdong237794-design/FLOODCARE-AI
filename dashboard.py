"""
Dashboard Blueprint (Minimal Stub)
====================================
This is a minimal stub for the dashboard module.
You can expand this with a full web dashboard later.

To add a full dashboard, implement routes for:
- /dashboard - Main dashboard view
- /dashboard/sos - SOS requests management
- /dashboard/water-levels - Water level monitoring
- /dashboard/shelters - Shelter management
"""
from flask import Blueprint, jsonify
import bot_config

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


@dashboard_bp.route('/', methods=['GET'])
def dashboard_home():
    """Dashboard home - returns basic stats"""
    supabase = bot_config.get_supabase_client()
    stats = {
        "supabase_connected": supabase is not None,
        "active_sos": 0,
        "pending_needs": 0,
        "total_shelters": 0,
        "water_stations": 0
    }
    
    if supabase:
        try:
            # Count active SOS
            resp = supabase.table("sos_requests").select("*", count="exact").eq("status", "OPEN").execute()
            stats["active_sos"] = len(resp.data) if resp.data else 0
        except:
            pass
        
        try:
            # Count pending needs
            resp = supabase.table("user_needs").select("*", count="exact").eq("status", "PENDING").execute()
            stats["pending_needs"] = len(resp.data) if resp.data else 0
        except:
            pass
        
        try:
            # Count shelters
            resp = supabase.table("shelters").select("*", count="exact").execute()
            stats["total_shelters"] = len(resp.data) if resp.data else 0
        except:
            pass
        
        try:
            # Count water stations
            resp = supabase.table("water_levels").select("*", count="exact").execute()
            stats["water_stations"] = len(resp.data) if resp.data else 0
        except:
            pass
    
    return jsonify({
        "message": "FLOODCARE Dashboard API",
        "stats": stats,
        "endpoints": [
            "/dashboard/ - This info",
            "/dashboard/sos - Active SOS requests",
            "/dashboard/needs - Pending user needs"
        ]
    })


@dashboard_bp.route('/sos', methods=['GET'])
def dashboard_sos():
    """Get all SOS requests"""
    supabase = bot_config.get_supabase_client()
    if not supabase:
        return jsonify({"error": "Supabase not connected"}), 500
    
    try:
        response = supabase.table("sos_requests").select("*").order("timestamp", desc=True).limit(100).execute()
        return jsonify({"sos_requests": response.data or []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dashboard_bp.route('/needs', methods=['GET'])
def dashboard_needs():
    """Get all user needs"""
    supabase = bot_config.get_supabase_client()
    if not supabase:
        return jsonify({"error": "Supabase not connected"}), 500
    
    try:
        response = supabase.table("user_needs").select("*").order("timestamp", desc=True).limit(100).execute()
        return jsonify({"user_needs": response.data or []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dashboard_bp.route('/water-levels', methods=['GET'])
def dashboard_water_levels():
    """Get current water levels"""
    supabase = bot_config.get_supabase_client()
    if not supabase:
        return jsonify({"error": "Supabase not connected"}), 500
    
    try:
        response = supabase.table("water_levels").select("*").order("water_level", desc=True).limit(50).execute()
        return jsonify({"water_levels": response.data or []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
