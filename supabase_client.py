# ฟังก์ชันบันทึกข้อมูลระดับน้ำ
def save_water_levels(data_list):
    try:
        supabase.table("water_levels").insert(data_list).execute()
        print(f"✅ บันทึก {len(data_list)} สถานีสำเร็จ")
    except Exception as e:
        print(f"❌ บันทึกข้อมูลล้มเหลว: {e}")

# ฟังก์ชันดึงสถานีใกล้ผู้ใช้
def get_nearest_stations(user_lat, user_lon, limit=3):
    try:
        response = supabase.table("water_levels").select("*").execute()
        stations = response.data
        
        # คำนวณระยะทาง
        for s in stations:
            s["distance_km"] = ((float(s["latitude"]) - user_lat)**2 + 
                               (float(s["longitude"]) - user_lon)**2)**0.5
        
        stations.sort(key=lambda x: x["distance_km"])
        return stations[:limit]
    except Exception as e:
        print(f"❌ ดึงข้อมูลล้มเหลว: {e}")
        return []
