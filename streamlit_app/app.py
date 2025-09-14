# Streamlit client portal (multi-tenant) with login and per-client pricing rules
# ============================================

# ---- Path setup to import src/ and app/ from Streamlit ----
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # project root
APP_DIR = ROOT / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# ---- Streamlit config (must be the first Streamlit call) ----
import streamlit as st
st.set_page_config(page_title="Freight Rate Prediction", page_icon="🚚", layout="centered")

# ---- App imports ----
import streamlit_authenticator as stauth
from PIL import Image
import requests
import pydeck as pdk  # map/route render

from src.inference import predict_one
# Dynamic per-client rules (reads clients/<client_id>/pricing_rules.json)
from pricing_rules import get_rules_for_client, postprocess_rate


# =========================
#   Helpers (pallet metrics)
# =========================
def compute_volume_m3(length_cm: float, width_cm: float, height_cm: float, count: int) -> float:
    return (length_cm * width_cm * height_cm) / 1e6 * count  # cm^3 -> m^3

def compute_density_kg_m3(weight_tons: float, volume_m3: float):
    weight_kg = weight_tons * 1000.0
    if volume_m3 and volume_m3 > 0:
        return weight_kg / volume_m3
    return None

def estimate_pallet_positions(count: int, stackable: bool) -> int:
    per_position = 2 if stackable else 1
    return int((count + per_position - 1) // per_position)


# =========================
#   Authentication (0.4.x)
# =========================
def build_auth_objects():
    # Lee bloques de secrets con fallback
    auth_cfg = st.secrets.get("auth")
    users = st.secrets.get("users", [])

    if not auth_cfg or not users:
        st.error(
            "Missing `[auth]` or `[[users]]` in Streamlit **Secrets**.\n\n"
            "Ve a *Manage app → Settings → Secrets* y pega tu `.streamlit/secrets.toml`."
        )
        st.stop()

    credentials = {"usernames": {}}
    username_to_client = {}
    username_to_role = {}

    for u in users:
        username = u["username"]
        credentials["usernames"][username] = {
            "name": u["name"],
            "password": u["password"],  # hash
        }
        username_to_client[username] = u.get("client_id", "demo")
        username_to_role[username] = u.get("role", "viewer")

    authenticator = stauth.Authenticate(
        credentials,
        auth_cfg["cookie_name"],
        auth_cfg["cookie_key"],
        auth_cfg.get("cookie_expiry_days", 7),
    )
    return authenticator, username_to_client, username_to_role


# ------ Login ------
authenticator, username_to_client, username_to_role = build_auth_objects()
with st.sidebar:
    st.header("Sign in")
authenticator.login(location="sidebar")

auth_status = st.session_state.get("authentication_status", None)
username    = st.session_state.get("username", None)
name        = st.session_state.get("name", None)

if auth_status is False:
    st.error("Invalid credentials")
    st.stop()
elif auth_status is None:
    st.info("Please log in")
    st.stop()

# Authenticated
client_id = username_to_client.get(username, "demo")
user_role = username_to_role.get(username, "viewer")

with st.sidebar:
    st.write(f"👋 {name} • Client: **{client_id}** • Role: **{user_role}**")
    authenticator.logout("Logout", "sidebar")


# =========================
#   Branding per client
# =========================
def load_client_logo(client: str):
    path = ROOT / "clients" / client / "logo.png"
    return Image.open(path) if path.exists() else None

logo = load_client_logo(client_id)
if logo:
    st.image(logo, width=72)

st.title(f"🚚 Freight Rate Prediction — {client_id.capitalize()}")
st.caption("Model raw vs. final rate (per-client business rules: minimums, fixed charges, rounding).")


# =========================
#   API config (for route features)
# =========================
API_URL = st.secrets.get("api", {}).get("url", "http://127.0.0.1:8000")
API_KEY = st.secrets.get("api", {}).get("key", "ACME_SECRET_123")


# =========================
#   Presets (sidebar)
# =========================
PRESETS = {
    "Jebel Ali → Al Quoz (dry, 30km)": {
        "client_type":"retailer","origin":"Jebel Ali Port","destination":"Al Quoz",
        "distance_km":30.0,"load_type":"dry","load_weight_tons":3.2,"vehicle_type":"7t_truck",
        "fuel_price_aed_per_litre":3.1,"salik_gates":2,"salik_charges_aed":8.0,
        "customs_fees_aed":60.0,"waiting_time_hours":1.5,"contract_type":"spot",
        "backhaul_available":0,"month":8,"season":"summer","weather":"hot","peak_demand_factor":1.06,
        # Pallets defaults
        "pallet_length_cm":120.0,"pallet_width_cm":100.0,"pallet_height_cm":150.0,
        "pallet_count":18,"pallet_stackable":True
    },
    "Jebel Ali → Al Quoz (oversized, 50km)": {
        "client_type":"retailer","origin":"Jebel Ali Port","destination":"Al Quoz",
        "distance_km":50.0,"load_type":"oversized","load_weight_tons":3.2,"vehicle_type":"7t_truck",
        "fuel_price_aed_per_litre":3.1,"salik_gates":2,"salik_charges_aed":8.0,
        "customs_fees_aed":60.0,"waiting_time_hours":2.0,"contract_type":"spot",
        "backhaul_available":0,"month":8,"season":"summer","weather":"hot","peak_demand_factor":1.06,
        "pallet_length_cm":120.0,"pallet_width_cm":100.0,"pallet_height_cm":150.0,
        "pallet_count":20,"pallet_stackable":False
    },
    "Dubai South → Abu Dhabi (reefer, 130km)": {
        "client_type":"distributor","origin":"Dubai South","destination":"Abu Dhabi",
        "distance_km":130.0,"load_type":"reefer","load_weight_tons":8.5,"vehicle_type":"reefer_truck",
        "fuel_price_aed_per_litre":3.1,"salik_gates":4,"salik_charges_aed":16.0,
        "customs_fees_aed":80.0,"waiting_time_hours":1.0,"contract_type":"contract",
        "backhaul_available":1,"month":11,"season":"autumn","weather":"clear","peak_demand_factor":1.02,
        "pallet_length_cm":120.0,"pallet_width_cm":100.0,"pallet_height_cm":160.0,
        "pallet_count":24,"pallet_stackable":True
    },
}

with st.sidebar:
    st.header("Presets")
    preset = st.selectbox("Choose a scenario", list(PRESETS.keys()))
    if st.button("Load preset"):
        for k, v in PRESETS[preset].items():
            st.session_state[k] = v
        st.success("Preset loaded. Adjust values and click Predict.")


# =========================
#   Defaults + suggest state
# =========================
defaults = PRESETS["Jebel Ali → Al Quoz (dry, 30km)"]
for key, value in defaults.items():
    st.session_state.setdefault(key, value)

st.session_state.setdefault("origin_suggestions", [])
st.session_state.setdefault("destination_suggestions", [])


# =========================
#   Input form (NO buttons dentro del form)
# =========================
st.subheader("Input")
with st.form("quote"):
    c1, c2 = st.columns(2)
    with c1:
        client_type = st.selectbox(
            "Client type",
            ["retailer","manufacturer","distributor","freight_forwarder","3pl_partner"],
            key="client_type"
        )
        origin = st.text_input("Origin", value=st.session_state.get("origin","Jebel Ali Port"), key="origin")
        destination = st.text_input("Destination", value=st.session_state.get("destination","Al Quoz"), key="destination")
        distance_km = st.number_input(
            "Distance (km)", min_value=1.0, max_value=1000.0,
            value=float(st.session_state.get("distance_km",30.0)), step=1.0, key="distance_km"
        )
        load_type = st.selectbox(
            "Load type", ["dry","reefer","hazardous","oversized"],
            index=["dry","reefer","hazardous","oversized"].index(st.session_state.get("load_type","dry")),
            key="load_type"
        )
        load_weight_tons = st.number_input(
            "Load weight (tons)", min_value=0.1, max_value=50.0,
            value=float(st.session_state.get("load_weight_tons",3.2)), step=0.1, key="load_weight_tons"
        )
        vehicle_type = st.selectbox(
            "Vehicle type", ["van","3t_truck","7t_truck","flatbed","reefer_truck"],
            index=["van","3t_truck","7t_truck","flatbed","reefer_truck"].index(st.session_state.get("vehicle_type","7t_truck")),
            key="vehicle_type"
        )
    with c2:
        fuel_price_aed_per_litre = st.number_input(
            "Fuel price (AED/L)", min_value=1.0, max_value=10.0,
            value=float(st.session_state.get("fuel_price_aed_per_litre",3.1)), step=0.01, key="fuel_price_aed_per_litre"
        )
        salik_gates = st.number_input(
            "SALIK gates", min_value=0, max_value=20,
            value=int(st.session_state.get("salik_gates",2)), step=1, key="salik_gates"
        )
        salik_charges_aed = st.number_input(
            "SALIK charges (AED)", min_value=0.0, max_value=200.0,
            value=float(st.session_state.get("salik_charges_aed",8.0)), step=0.5, key="salik_charges_aed"
        )
        customs_fees_aed = st.number_input(
            "Customs/handling (AED)", min_value=0.0, max_value=1000.0,
            value=float(st.session_state.get("customs_fees_aed",60.0)), step=1.0, key="customs_fees_aed"
        )
        waiting_time_hours = st.number_input(
            "Waiting time (hours)", min_value=0.0, max_value=24.0,
            value=float(st.session_state.get("waiting_time_hours",1.5)), step=0.25, key="waiting_time_hours"
        )
        contract_type = st.selectbox(
            "Contract type", ["spot","contract"],
            index=["spot","contract"].index(st.session_state.get("contract_type","spot")), key="contract_type"
        )
        backhaul_available = st.selectbox(
            "Backhaul available", [0,1],
            index=[0,1].index(st.session_state.get("backhaul_available",0)), key="backhaul_available"
        )
        month = st.number_input(
            "Month", min_value=1, max_value=12,
            value=int(st.session_state.get("month",8)), step=1, key="month"
        )
        season = st.selectbox(
            "Season", ["winter","spring","summer","autumn"],
            index=["winter","spring","summer","autumn"].index(st.session_state.get("season","summer")), key="season"
        )
        weather = st.selectbox(
            "Weather", ["clear","hot","sandstorm","rain"],
            index=["clear","hot","sandstorm","rain"].index(st.session_state.get("weather","hot")), key="weather"
        )
        peak_demand_factor = st.number_input(
            "Peak demand factor", min_value=0.5, max_value=2.0,
            value=float(st.session_state.get("peak_demand_factor",1.06)), step=0.01, key="peak_demand_factor"
        )

    # ---- Pallets (dentro del form, sin botones) ----
    with st.expander("Pallets"):
        col1, col2, col3, col4 = st.columns(4)
        col1.number_input("Length (cm)", min_value=1.0,
                          value=float(st.session_state.get("pallet_length_cm",120.0)), key="pallet_length_cm")
        col2.number_input("Width (cm)", min_value=1.0,
                          value=float(st.session_state.get("pallet_width_cm",100.0)), key="pallet_width_cm")
        col3.number_input("Height (cm)", min_value=1.0,
                          value=float(st.session_state.get("pallet_height_cm",150.0)), key="pallet_height_cm")
        col4.number_input("Count", min_value=1,
                          value=int(st.session_state.get("pallet_count",18)), step=1, key="pallet_count")
        st.checkbox("Stackable", value=bool(st.session_state.get("pallet_stackable",True)), key="pallet_stackable")

    submitted = st.form_submit_button("Predict")  # <-- ÚNICO botón dentro del form


# =========================
#   SUGERENCIAS y RUTA (fuera del form)
# =========================
st.markdown("—")
cA, cB, cC, cD = st.columns([1, 1, 1, 3])
with cA:
    suggest_o_btn = st.button("Suggest Origin")
with cB:
    suggest_d_btn = st.button("Suggest Destination")
with cC:
    trace_btn = st.button("Trace route")
with cD:
    st.caption("Use these to pre-fill addresses and visualize the route. You can still edit values before predicting.")

# ORIGIN SUGGEST
if suggest_o_btn:
    try:
        r = requests.get(f"{API_URL}/v1/geo/suggest", params={"q": st.session_state.get("origin","")}, timeout=20)
        r.raise_for_status()
        st.session_state["origin_suggestions"] = r.json().get("suggestions", [])[:5]
        if st.session_state["origin_suggestions"]:
            chosen = st.selectbox("Pick an origin", st.session_state["origin_suggestions"], key="origin_pick")
            if chosen:
                st.session_state["origin"] = chosen
                st.success("Origin selected.")
        else:
            st.info("No origin suggestions.")
    except Exception as e:
        st.error(f"Origin suggest failed: {e}")

# DESTINATION SUGGEST
if suggest_d_btn:
    try:
        r = requests.get(f"{API_URL}/v1/geo/suggest", params={"q": st.session_state.get("destination","")}, timeout=20)
        r.raise_for_status()
        st.session_state["destination_suggestions"] = r.json().get("suggestions", [])[:5]
        if st.session_state["destination_suggestions"]:
            chosen = st.selectbox("Pick a destination", st.session_state["destination_suggestions"], key="destination_pick")
            if chosen:
                st.session_state["destination"] = chosen
                st.success("Destination selected.")
        else:
            st.info("No destination suggestions.")
    except Exception as e:
        st.error(f"Destination suggest failed: {e}")

# TRACE ROUTE + KPIs + MAP
def _extract_coords_from_route(route_json):
    coords = []
    try:
        legs = route_json.get("legs", [])
        for leg in legs:
            if "coordinates" in leg and isinstance(leg["coordinates"], list):
                coords.extend(leg["coordinates"])  # [[lon,lat],...]
            elif "geometry_polyline" in leg and isinstance(leg["geometry_polyline"], str):
                try:
                    import polyline as _pline  # opcional (añade 'polyline' a requirements si lo usas)
                    decoded = _pline.decode(leg["geometry_polyline"], precision=6)
                    coords.extend([[lon, lat] for lat, lon in decoded])
                except Exception:
                    pass
    except Exception:
        pass
    return coords

if trace_btn:
    try:
        params = {"origin": st.session_state.get("origin",""), "destination": st.session_state.get("destination","")}
        if not params["origin"] or not params["destination"]:
            st.warning("Provide Origin and Destination first.")
        else:
            r = requests.get(f"{API_URL}/v1/geo/route", params=params, timeout=40)
            r.raise_for_status()
            route = r.json()

            # KPIs de la ruta (incluye tolls si el backend los calcula)
            dist = route.get("distance_km", None)
            dur  = route.get("duration_min", None)
            tolls_count = route.get("tolls_count", None)
            tolls_cost  = route.get("tolls_cost", None)

            k1, k2, k3, k4 = st.columns(4)
            if dist is not None: k1.metric("Distance (km)", f"{dist:,.1f}")
            if dur  is not None: k2.metric("Duration (min)", f"{dur:,.0f}")
            if tolls_count is not None: k3.metric("Tolls", f"{tolls_count}")
            if tolls_cost  is not None: k4.metric("Tolls cost (AED)", f"{tolls_cost:,.2f}")

            # Dibujo del mapa con pydeck
            coords = _extract_coords_from_route(route)
            if coords:
                clons = [c[0] for c in coords]
                clats = [c[1] for c in coords]
                center = [sum(clons)/len(clons), sum(clats)/len(clats)]
                path_data = [{"path": coords}]
                layer = pdk.Layer("PathLayer", path_data, get_path="path",
                                  width_scale=3, width_min_pixels=3, pickable=False)
                view_state = pdk.ViewState(latitude=center[1], longitude=center[0], zoom=9)
                # Basemap (opcional) — requiere token público en secrets.toml
                # pdk.settings.mapbox_api_key = st.secrets.get("mapbox", {}).get("token", None)
                st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, map_style=None))
            else:
                st.info("Route received but no drawable geometry was found.")
    except Exception as e:
        st.error(f"Trace route failed: {e}")


# =========================
#   Pallet KPIs (chips) en tiempo real (fuera del form)
# =========================
try:
    _vol_m3 = compute_volume_m3(
        st.session_state.get("pallet_length_cm", 120.0),
        st.session_state.get("pallet_width_cm", 100.0),
        st.session_state.get("pallet_height_cm", 150.0),
        int(st.session_state.get("pallet_count", 18)),
    )
    _dens = compute_density_kg_m3(float(st.session_state.get("load_weight_tons", 3.2)), _vol_m3)
    _positions = estimate_pallet_positions(
        int(st.session_state.get("pallet_count", 18)),
        bool(st.session_state.get("pallet_stackable", True))
    )
    st.subheader("Pallet metrics")
    m1, m2, m3 = st.columns(3)
    m1.metric("Volume (m³)", f"{_vol_m3:,.3f}")
    m2.metric("Density (kg/m³)", f"{_dens:,.1f}" if _dens else "—")
    m3.metric("Pallet positions (est.)", f"{_positions}")
except Exception:
    pass


# =========================
#   Prediction + client rules
# =========================
if submitted:
    payload = {
        "client_type": st.session_state["client_type"],
        "origin": st.session_state["origin"],
        "destination": st.session_state["destination"],
        "distance_km": float(st.session_state["distance_km"]),
        "load_type": st.session_state["load_type"],
        "load_weight_tons": float(st.session_state["load_weight_tons"]),
        "vehicle_type": st.session_state["vehicle_type"],
        "fuel_price_aed_per_litre": float(st.session_state["fuel_price_aed_per_litre"]),
        "salik_gates": int(st.session_state["salik_gates"]),
        "salik_charges_aed": float(st.session_state["salik_charges_aed"]),
        "customs_fees_aed": float(st.session_state["customs_fees_aed"]),
        "waiting_time_hours": float(st.session_state["waiting_time_hours"]),
        "contract_type": st.session_state["contract_type"],
        "backhaul_available": int(st.session_state["backhaul_available"]),
        "month": int(st.session_state["month"]),
        "season": st.session_state["season"],
        "weather": st.session_state["weather"],
        "peak_demand_factor": float(st.session_state["peak_demand_factor"]),
        # Pallets (alineado con la API)
        "pallets": {
            "count": int(st.session_state.get("pallet_count", 18)),
            "dimensions_cm": {
                "length_cm": float(st.session_state.get("pallet_length_cm", 120.0)),
                "width_cm":  float(st.session_state.get("pallet_width_cm", 100.0)),
                "height_cm": float(st.session_state.get("pallet_height_cm", 150.0)),
            },
            "stackable": bool(st.session_state.get("pallet_stackable", True)),
        },
    }

    try:
        raw = predict_one(payload)
        rules = get_rules_for_client(client_id)
        pp = postprocess_rate(raw, payload["vehicle_type"], rules)

        st.subheader("Results")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Model Raw (AED)", f"{pp['raw_rate']:,.2f}")
            st.caption("Direct model output")
        with col2:
            st.metric("Final Rate (AED)", f"{pp['final_rate']:,.2f}")
            st.caption("Minimums + fixed charges + rounding (client rules)")

        if user_role in ("admin", "commercial"):
            with st.expander("Breakdown details"):
                st.json({"client_id": client_id, "rules_in_use": rules, "calc": pp})
        else:
            st.caption("Contact your administrator to view the breakdown.")

        st.success("Prediction complete.")
    except Exception as e:
        st.error(f"Prediction failed: {e}")
else:
    st.info("Select a preset on the sidebar or fill the form and click **Predict**.")


# =========================
#   Sidebar: Market Data (fuel / toll fee)
# =========================
with st.sidebar:
    st.subheader("Market Data")
    try:
        fuel = requests.get(f"{API_URL}/v1/market/fuel", params={"country":"AE"}, timeout=20).json()
        prices = fuel.get("prices", [])
        if prices:
            st.caption(f"Effective from: {prices[0].get('effective_from','—')}")
            for p in prices:
                st.write(f"- {p.get('product','?')}: **{p.get('price_per_liter','?')} AED/L**")
        else:
            st.info("No fuel prices available.")
    except Exception as e:
        st.error(f"Fuel data error: {e}")
