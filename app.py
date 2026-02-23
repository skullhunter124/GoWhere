import streamlit as st
import google.generativeai as genai
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import folium
from streamlit_folium import st_folium
import json
import re
import datetime
import sqlite3
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="GoWhere",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

with open("style.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ── DB ────────────────────────────────────────────────────────────────────────
DB_PATH = "gowhere.db"

def init_db():
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("""CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, mode TEXT, query TEXT, result TEXT)""")
        con.commit(); con.close()
    except Exception: pass

def save_to_db(mode, query, result):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO history (ts, mode, query, result) VALUES (?,?,?,?)",
                    (datetime.datetime.now().isoformat(), mode, query, result))
        con.commit(); con.close()
    except Exception: pass

def load_history():
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT ts, mode, query FROM history ORDER BY id DESC LIMIT 20").fetchall()
        con.close(); return rows
    except Exception: return []

init_db()

# ── Session defaults ──────────────────────────────────────────────────────────
for k, v in {"map_data": None, "mode": None, "selected_idx": None}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Gemini ────────────────────────────────────────────────────────────────────
def get_gemini(api_key):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash-lite")

# ── Geocoding ─────────────────────────────────────────────────────────────────
# Per-thread Nominatim instances (not thread-safe to share one)
_geo_local = threading.local()

def _get_geo():
    if not hasattr(_geo_local, "geo"):
        _geo_local.geo = Nominatim(
            user_agent=f"gowhere_v5_{id(threading.current_thread())}", timeout=8)
    return _geo_local.geo

def geocode_city(place: str):
    """Geocode a city. Returns (lat, lng, address, country_code, country_name)."""
    geo = _get_geo()
    try:
        # First try with addressdetails for country info
        r = geo.geocode(place, exactly_one=True, addressdetails=True)
        if r:
            try:
                addr    = r.raw.get("address", {})
                cc      = addr.get("country_code", "").lower()
                country = addr.get("country", "")
            except Exception:
                cc, country = "", ""
            return r.latitude, r.longitude, r.address, cc, country
    except Exception: pass

    try:
        # Fallback: without addressdetails
        r = geo.geocode(place, exactly_one=True)
        if r:
            return r.latitude, r.longitude, r.address, "", ""
    except Exception: pass

    return None, None, None, "", ""

def geocode_poi(name: str, city: str, city_lat: float, city_lng: float,
                max_dist_km: float, country_code: str = ""):
    """
    Geocode one POI name near a city center.
    Uses Nominatim's countrycodes param to restrict results to the right country.
    Validates result is within max_dist_km of city center.
    Falls back to progressively looser queries.
    """
    geo = _get_geo()
    cc  = country_code.lower() if country_code else None

    # Build a bounding box hint (soft — not bounded, just a preference hint)
    deg = max(max_dist_km / 111.0, 0.3)
    vb  = f"{city_lng - deg},{city_lat - deg},{city_lng + deg},{city_lat + deg}"

    def _check(r):
        if not r: return None, None
        dist = geodesic((city_lat, city_lng), (r.latitude, r.longitude)).km
        return (r.latitude, r.longitude) if dist <= max_dist_km else (None, None)

    cc_kwargs = {"countrycodes": cc} if cc else {}

    # --- Strategy 1: "Name, City" + country restriction + soft viewbox ---
    try:
        r = geo.geocode(f"{name}, {city}", exactly_one=True,
                        viewbox=vb, bounded=False, **cc_kwargs)
        lat, lng = _check(r)
        if lat: return lat, lng
    except Exception: pass

    # --- Strategy 2: name alone + country restriction + bounded viewbox ---
    try:
        r = geo.geocode(name, exactly_one=True,
                        viewbox=vb, bounded=True, **cc_kwargs)
        lat, lng = _check(r)
        if lat: return lat, lng
    except Exception: pass

    # --- Strategy 3: "Name, City" no restrictions (international fallback) ---
    try:
        r = geo.geocode(f"{name}, {city}", exactly_one=True)
        lat, lng = _check(r)
        if lat: return lat, lng
    except Exception: pass

    return None, None


def batch_geocode(pois: list, city: str, city_lat: float, city_lng: float,
                  max_dist_km: float, country_code: str = "",
                  max_workers: int = 8) -> list:
    """
    Geocode all POIs in parallel.
    Returns list of (poi_dict, lat, lng) — preserves original order.
    """
    results = [None] * len(pois)

    def _job(idx, p):
        lat, lng = geocode_poi(p["name"], city, city_lat, city_lng,
                               max_dist_km, country_code)
        return idx, lat, lng

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_job, i, p): i for i, p in enumerate(pois)}
        for fut in as_completed(futures):
            idx, lat, lng = fut.result()
            if lat is not None:
                results[idx] = (pois[idx], lat, lng)

    return [(p, lat, lng) for entry in results
            if entry is not None
            for p, lat, lng in [entry]]


# ── AI Prompts ────────────────────────────────────────────────────────────────
POI_PROMPT = """You are GoWhere — a hyper-local discovery engine.

Target: {city}, {country} (lat {lat:.4f}, lng {lng:.4f})

Generate exactly 25 Points of Interest within {max_km}km of {city}.
STRICT: Every single place MUST be within {max_km}km of {city}, {country}.
Do NOT suggest places in other municipalities or towns unless literally adjacent.

Cover many different categories:
- Cafés, bars, local restaurants (no chains)
- Parks, nature, viewpoints, lakes, rivers, forests, hiking
- Museums, galleries, cultural centers, manor houses, ruins, chapels
- Architecture, sculptures, historic buildings, monuments
- Markets, farm shops, bakeries, local stores
- Sports: swimming, cycling routes, sports fields, recreational areas
- Quirky: mills, bridges, cemeteries, train stations, water towers
- Community: village squares, cultural houses, libraries, springs

CRITICAL: Use the exact official name as it appears on OpenStreetMap for {country}.
Real places only — no invented names.

Return ONLY valid JSON, no markdown fences:
{{"pois":[{{"name":"Exact Name","category":"emoji word","why":"One sentence."}}]}}"""

VIBE_PROMPT = """You are GoWhere — a spontaneous travel planner.

User is in: {city}, {country} (lat {lat:.4f}, lng {lng:.4f})
Vibe request: "{vibe}"
Search radius: {radius_km}km

Generate exactly 20 destinations matching the vibe, ALL within {radius_km}km of {city}.
STRICT: Every place must be in {country} and within {radius_km}km. No exceptions.
Mix distances: some nearby, some toward the edge of the radius.
Include villages, nature spots, and attractions — not just city-centre places.

CRITICAL: Exact official place names as on OpenStreetMap for {country} only.

Return ONLY valid JSON, no markdown fences:
{{"destinations":[{{"name":"Exact Name","vibe_match":"Why it fits.","insider_tip":"One tip."}}]}}"""


def parse_json(text):
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    return json.loads(text)

def get_pois(model, city, country, lat, lng, max_km):
    prompt = POI_PROMPT.format(city=city, country=country,
                               lat=lat, lng=lng, max_km=max_km)
    return parse_json(model.generate_content(prompt).text)

def get_vibe_destinations(model, city, country, lat, lng, vibe, radius_km):
    prompt = VIBE_PROMPT.format(city=city, country=country,
                                lat=lat, lng=lng, vibe=vibe, radius_km=radius_km)
    return parse_json(model.generate_content(prompt).text)


# ── Map builder ───────────────────────────────────────────────────────────────
TILE   = "CartoDB dark_matter"
COLORS = ["#FF6B6B","#4ECDC4","#FFE66D","#A8E6CF","#FF8B94",
          "#C3A6FF","#FFA07A","#87CEEB","#DDA0DD","#98FB98",
          "#F0A500","#ADFF2F","#FF69B4","#00CED1","#FF4500"]

def build_map(clat, clng, items, zoom=14, user_marker=False,
              selected_idx=None, radius_km=None):
    m = folium.Map(location=[clat, clng], zoom_start=zoom, tiles=TILE)

    if radius_km and user_marker:
        folium.Circle(
            [clat, clng], radius=radius_km * 1000,
            color="#4ECDC4", weight=1.2, fill=True,
            fill_color="#4ECDC4", fill_opacity=0.04, dash_array="8 4",
        ).add_to(m)

    if user_marker:
        folium.CircleMarker(
            [clat, clng], radius=9, color="#FFFFFF",
            fill=True, fill_color="#FFFFFF", fill_opacity=1,
            tooltip=folium.Tooltip("<b>📍 You are here</b>", sticky=True),
        ).add_to(m)

    for i, item in enumerate(items):
        color    = item["color"]
        is_sel   = (selected_idx == i)

        if is_sel:
            folium.CircleMarker(
                [item["lat"], item["lng"]], radius=28,
                color=color, fill=True, fill_color=color,
                fill_opacity=0.2, weight=0,
            ).add_to(m)

        if user_marker:
            folium.PolyLine(
                [[clat, clng], [item["lat"], item["lng"]]],
                color=color, weight=1.5, opacity=0.28, dash_array="5 4",
            ).add_to(m)

        folium.CircleMarker(
            [item["lat"], item["lng"]],
            radius=14 if is_sel else 11,
            color=color, fill=True, fill_color=color,
            fill_opacity=1.0 if is_sel else 0.9, weight=2,
            tooltip=folium.Tooltip(f"<b>{item['name']}</b>", sticky=True),
            popup=folium.Popup(item["popup_html"], max_width=300),
        ).add_to(m)

        folium.map.Marker(
            [item["lat"], item["lng"]],
            icon=folium.DivIcon(
                html=(f"<div style='background:{color};color:#000;font-size:9px;"
                      f"font-weight:900;text-align:center;line-height:18px;"
                      f"width:18px;height:18px;border-radius:50%;"
                      f"margin-left:-9px;margin-top:-9px;pointer-events:none'>"
                      f"{item['label']}</div>"),
                icon_size=(18, 18), icon_anchor=(9, 9),
            ),
        ).add_to(m)

    return m


# ── Card renderers ────────────────────────────────────────────────────────────
def render_poi_card(i, p, sel):
    is_sel = (sel == i)
    border = p["color"] if is_sel else "var(--border)"
    bg     = "rgba(255,255,255,0.035)" if is_sel else "var(--surface)"
    col_card, col_btn = st.columns([11, 1])
    with col_card:
        st.markdown(f"""
        <div style="background:{bg};border:1.5px solid {border};border-radius:14px;
             padding:14px 18px;margin-bottom:8px">
          <div style="display:flex;align-items:flex-start;gap:14px">
            <div style="font-family:'Syne',sans-serif;font-size:1.7rem;font-weight:900;
                 color:{p['color']};line-height:1;flex-shrink:0;min-width:28px">{i+1}</div>
            <div>
              <div style="font-size:0.72rem;color:{p['color']};font-weight:700;
                   letter-spacing:0.06em;text-transform:uppercase;margin-bottom:2px">{p['category']}</div>
              <div style="font-family:'Syne',sans-serif;font-size:0.95rem;font-weight:700;
                   color:#F0F0F5;margin-bottom:5px">{p['name']}</div>
              <div style="font-size:0.83rem;color:#7A7A9A;line-height:1.5">{p['why']}</div>
            </div>
          </div>
        </div>""", unsafe_allow_html=True)
    with col_btn:
        lbl = "✅" if is_sel else "📍"
        if st.button(lbl, key=f"focus_{i}", help=f"Show on map"):
            st.session_state.selected_idx = None if is_sel else i
            st.rerun()


def render_vibe_card(i, dest, sel):
    is_sel = (sel == i)
    border = dest["color"] if is_sel else "var(--border)"
    bg     = "rgba(255,255,255,0.035)" if is_sel else "var(--surface)"
    col_card, col_btn = st.columns([11, 1])
    with col_card:
        st.markdown(f"""
        <div style="background:{bg};border:1.5px solid {border};border-radius:14px;
             padding:16px 18px;margin-bottom:10px">
          <div style="display:flex;align-items:center;justify-content:space-between;
               margin-bottom:8px;flex-wrap:wrap;gap:8px">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-family:'Syne',sans-serif;font-size:1.4rem;
                    font-weight:900;color:{dest['color']}">{i+1}</span>
              <span style="font-family:'Syne',sans-serif;font-size:0.95rem;
                    font-weight:700;color:#F0F0F5">{dest['name']}</span>
            </div>
            <span style="font-size:0.75rem;color:{dest['color']};font-weight:700;
                  background:rgba(78,205,196,0.1);padding:2px 10px;
                  border-radius:20px;white-space:nowrap">~{dest['distance_km']} km</span>
          </div>
          <div style="font-size:0.85rem;color:#F0F0F5;line-height:1.5;margin-bottom:8px">
            {dest['vibe_match']}</div>
          <div style="font-size:0.8rem;color:#7A7A9A;line-height:1.5;
               border-top:1px solid #2A2A3A;padding-top:8px">💡 {dest['insider_tip']}</div>
        </div>""", unsafe_allow_html=True)
    with col_btn:
        lbl = "✅" if is_sel else "📍"
        if st.button(lbl, key=f"focus_{i}", help=f"Show on map"):
            st.session_state.selected_idx = None if is_sel else i
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div id="gw-map-anchor"></div>', unsafe_allow_html=True)

st.markdown("""
<div class="gw-header">
  <div class="gw-logo">🧭</div>
  <div>
    <h1 class="gw-title">GoWhere</h1>
    <p class="gw-sub">Turn your vibe into a destination</p>
  </div>
</div>""", unsafe_allow_html=True)

# ── API Key ───────────────────────────────────────────────────────────────────
api_key = os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    with st.expander("🔑 Enter your Gemini API Key", expanded=True):
        api_key = st.text_input("key", type="password", placeholder="AIza...",
                                label_visibility="collapsed")
        st.caption("Free key at [aistudio.google.com](https://aistudio.google.com). Session only.")

if not api_key:
    st.markdown('<div class="gw-hint">👆 Add your API key above to get started</div>',
                unsafe_allow_html=True)
    st.stop()

model = get_gemini(api_key)

# ── Mode buttons ──────────────────────────────────────────────────────────────
st.markdown('<div class="gw-section-label">What do you need?</div>', unsafe_allow_html=True)
c1, c2 = st.columns(2)
with c1:
    if st.button("📍 Show me what's here", use_container_width=True, key="btn_poi"):
        st.session_state.mode = "poi"
        st.session_state.map_data = None
        st.session_state.selected_idx = None
with c2:
    if st.button("✨ Match my vibe", use_container_width=True, key="btn_vibe"):
        st.session_state.mode = "vibe"
        st.session_state.map_data = None
        st.session_state.selected_idx = None

mode = st.session_state.mode

# ── POI Mode ──────────────────────────────────────────────────────────────────
if mode == "poi":
    st.markdown('<div class="gw-card">', unsafe_allow_html=True)
    st.markdown("#### 📍 Local POI Radar")
    city = st.text_input("Where are you?",
        placeholder="e.g. Ljutomer, Murska Sobota, Prenzlauer Berg Berlin...")
    st.caption("💡 For small villages try: 'Velika Polana, Slovenia'")
    go = st.button("Discover this place →", type="primary", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if go and city:
        st.session_state.selected_idx = None
        prog = st.progress(0, text="Locating your place...")
        lat, lng, address, cc, country = geocode_city(city)
        if not lat:
            prog.empty()
            st.error("Couldn't find that location. Try adding country: 'Ljutomer, Slovenia'")
        else:
            poi_radius = 25  # km
            prog.progress(15, text=f"Asking AI about {city}...")
            try:
                data  = get_pois(model, city, country, lat, lng, max_km=poi_radius)
                raw   = data.get("pois", [])
                prog.progress(30, text=f"Locating {len(raw)} places in parallel...")

                verified = batch_geocode(raw, city, lat, lng,
                                         max_dist_km=poi_radius,
                                         country_code=cc,
                                         max_workers=8)

                prog.progress(88, text="Building map...")
                items = []
                for p, plat, plng in verified:
                    color = COLORS[len(items) % len(COLORS)]
                    items.append({
                        "lat": plat, "lng": plng,
                        "name": p["name"], "category": p["category"],
                        "why": p["why"], "color": color,
                        "label": len(items) + 1,
                        "popup_html": (
                            f"<div style='font-family:sans-serif;padding:4px'>"
                            f"<b style='font-size:13px;color:#111'>{p['name']}</b><br>"
                            f"<span style='color:{color};font-size:11px'>{p['category']}</span>"
                            f"<hr style='margin:5px 0;border-color:#ddd'>"
                            f"<span style='font-size:12px;color:#333'>{p['why']}</span></div>"
                        ),
                    })
                # Show ALL verified results — no cap

                prog.progress(95, text="Rendering map...")
                if not items:
                    prog.empty()
                    st.error("No locations found. Try adding the country: 'Ljutomer, Slovenia'")
                else:
                    prog.progress(100); prog.empty()
                    st.session_state.map_data = {
                        "type": "poi", "items": items,
                        "city": city, "center_lat": lat, "center_lng": lng,
                    }
                    save_to_db("poi", city, json.dumps(
                        [{"name": x["name"], "lat": x["lat"], "lng": x["lng"]} for x in items]))
            except Exception as e:
                prog.empty()
                st.error(f"Something went wrong: {e}")

# ── Vibe Mode ─────────────────────────────────────────────────────────────────
elif mode == "vibe":
    st.markdown('<div class="gw-card">', unsafe_allow_html=True)
    st.markdown("#### ✨ Vibe-to-Map Engine")
    city = st.text_input("Your starting point",
        placeholder="e.g. Ljubljana, Ljutomer, Amsterdam Centrum...")
    st.caption("💡 For small villages try: 'Velika Polana, Slovenia'")
    vibe = st.text_area("Describe your vibe",
        placeholder='"Hidden spot with a view, quiet, not touristy"', height=80)

    st.markdown(
        '<div style="margin:16px 0 4px;font-size:0.85rem;font-weight:600;color:#F0F0F5">'
        '📡 Search radius</div>', unsafe_allow_html=True)
    radius_km = st.slider("radius", min_value=1, max_value=150, value=20,
                          step=1, format="%d km", label_visibility="collapsed")

    if   radius_km <= 3:  rl = "🚶 On foot"
    elif radius_km <= 10: rl = "🚲 Cycling distance"
    elif radius_km <= 30: rl = "🚗 Short drive"
    elif radius_km <= 60: rl = "🛣️ Day trip"
    else:                 rl = "✈️ Weekend adventure"
    st.markdown(f'<div style="font-size:0.78rem;color:#4ECDC4;margin-bottom:14px">'
                f'{rl} · up to <b>{radius_km} km</b></div>', unsafe_allow_html=True)

    go = st.button("Find my vibe match →", type="primary", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if go and city and vibe:
        st.session_state.selected_idx = None
        prog = st.progress(0, text="Locating your place...")
        lat, lng, address, cc, country = geocode_city(city)
        if not lat:
            prog.empty()
            st.error("Couldn't find that location. Try: 'Ljutomer, Slovenia'")
        else:
            prog.progress(15, text="AI is matching your vibe...")
            try:
                data = get_vibe_destinations(model, city, country, lat, lng, vibe, radius_km)
                raw  = data.get("destinations", [])
                prog.progress(30, text=f"Locating {len(raw)} places in parallel...")

                verified = batch_geocode(raw, city, lat, lng,
                                         max_dist_km=radius_km,
                                         country_code=cc,
                                         max_workers=8)

                prog.progress(88, text="Building map...")
                items = []
                for d_item, plat, plng in verified:
                    dist_km = round(geodesic((lat, lng), (plat, plng)).km, 1)
                    if dist_km > radius_km:
                        continue
                    color = COLORS[len(items) % len(COLORS)]
                    items.append({
                        "lat": plat, "lng": plng,
                        "name": d_item["name"],
                        "vibe_match": d_item["vibe_match"],
                        "insider_tip": d_item["insider_tip"],
                        "distance_km": dist_km, "color": color,
                        "label": len(items) + 1,
                        "popup_html": (
                            f"<div style='font-family:sans-serif;padding:4px'>"
                            f"<b style='font-size:13px;color:#111'>{d_item['name']}</b><br>"
                            f"<span style='color:{color};font-size:11px'>~{dist_km} km away</span>"
                            f"<hr style='margin:5px 0;border-color:#ddd'>"
                            f"<b style='font-size:11px;color:#555'>Why it fits:</b><br>"
                            f"<span style='font-size:12px;color:#333'>{d_item['vibe_match']}</span><br><br>"
                            f"<b style='font-size:11px;color:#555'>💡 Tip:</b><br>"
                            f"<span style='font-size:12px;color:#333'>{d_item['insider_tip']}</span></div>"
                        ),
                    })
                # Show ALL verified results within radius — no cap

                # Sort by distance, fix labels + colors
                items.sort(key=lambda x: x["distance_km"])
                for idx, item in enumerate(items):
                    item["label"] = idx + 1
                    item["color"] = COLORS[idx % len(COLORS)]
                    # Rebuild popup with final color
                    item["popup_html"] = (
                        f"<div style='font-family:sans-serif;padding:4px'>"
                        f"<b style='font-size:13px;color:#111'>{item['name']}</b><br>"
                        f"<span style='color:{item['color']};font-size:11px'>~{item['distance_km']} km away</span>"
                        f"<hr style='margin:5px 0;border-color:#ddd'>"
                        f"<b style='font-size:11px;color:#555'>Why it fits:</b><br>"
                        f"<span style='font-size:12px;color:#333'>{item['vibe_match']}</span><br><br>"
                        f"<b style='font-size:11px;color:#555'>💡 Tip:</b><br>"
                        f"<span style='font-size:12px;color:#333'>{item['insider_tip']}</span></div>"
                    )

                zoom = (15 if radius_km <= 3 else 13 if radius_km <= 10
                        else 11 if radius_km <= 30 else 9 if radius_km <= 60 else 8)

                prog.progress(95, text="Rendering map...")
                prog.progress(100); prog.empty()

                if not items:
                    st.warning(f"No verified places within {radius_km} km. "
                               f"Try a larger radius or add country to your city.")
                else:
                    st.session_state.map_data = {
                        "type": "vibe", "items": items,
                        "city": city, "vibe": vibe,
                        "center_lat": lat, "center_lng": lng,
                        "radius_km": radius_km, "map_zoom": zoom,
                    }
                    save_to_db("vibe", f"{city} | {vibe}", json.dumps(
                        [{"name": x["name"], "lat": x["lat"], "lng": x["lng"]} for x in items]))
            except Exception as e:
                prog.empty()
                st.error(f"Something went wrong: {e}")

# ── Results ───────────────────────────────────────────────────────────────────
if st.session_state.map_data:
    d     = st.session_state.map_data
    items = d["items"]
    sel   = st.session_state.selected_idx
    is_vibe = (d["type"] == "vibe")
    clat, clng = d["center_lat"], d["center_lng"]

    st.markdown("---")

    if sel is not None and 0 <= sel < len(items):
        map_center = [items[sel]["lat"], items[sel]["lng"]]
        map_zoom   = 17
    else:
        map_center = [clat, clng]
        map_zoom   = d.get("map_zoom", 11) if is_vibe else 14

    live_map = build_map(
        map_center[0], map_center[1], items,
        zoom=map_zoom, user_marker=is_vibe,
        selected_idx=sel,
        radius_km=d.get("radius_km") if sel is None else None,
    )
    live_map.location = map_center

    map_result = st_folium(
        live_map, use_container_width=True, height=450,
        returned_objects=["last_object_clicked_popup"],
        key="main_map",
    )

    # Map click → highlight card
    if map_result:
        clicked = map_result.get("last_object_clicked_popup")
        if clicked:
            txt = str(clicked)
            for i, item in enumerate(items):
                if item["name"] in txt:
                    if st.session_state.selected_idx != i:
                        st.session_state.selected_idx = i
                        st.rerun()
                    break

    # ── Cards ──────────────────────────────────────────────────────────────
    if d["type"] == "poi":
        count = len(items)
        st.markdown(
            f'<div class="gw-results-label">{count} spots in <b>{d["city"]}</b>'
            f' &nbsp;·&nbsp; <span style="color:var(--muted);font-weight:400;font-size:0.7rem">'
            f'click a marker · or tap 📍 to zoom in</span></div>',
            unsafe_allow_html=True)
        for i, p in enumerate(items):
            render_poi_card(i, p, sel)

    elif d["type"] == "vibe":
        count    = len(items)
        rkm_txt  = f' within <b>{d.get("radius_km","?")} km</b>' if "radius_km" in d else ""
        st.markdown(
            f'<div class="gw-results-label">{count} vibe spots near <b>{d["city"]}</b>{rkm_txt}'
            f' &nbsp;·&nbsp; <span style="color:var(--muted);font-weight:400;font-size:0.7rem">'
            f'click a marker · or tap 📍 to zoom in</span></div>',
            unsafe_allow_html=True)
        for i, dest in enumerate(items):
            render_vibe_card(i, dest, sel)

# ── Sidebar history ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🗺️ Adventure History")
    rows = load_history()
    if rows:
        for ts, mode_val, query in rows:
            ts_short = ts[:16].replace("T", " ")
            icon = "📍" if mode_val == "poi" else "✨"
            st.markdown(f"""
            <div class="gw-history-item">
              <span class="gw-hist-icon">{icon}</span>
              <div>
                <div class="gw-hist-query">{query[:40]}{'...' if len(query)>40 else ''}</div>
                <div class="gw-hist-ts">{ts_short}</div>
              </div>
            </div>""", unsafe_allow_html=True)
    else:
        st.caption("No adventures yet — go explore!")
    st.markdown("---")
    st.caption("All history stored locally on your machine.")