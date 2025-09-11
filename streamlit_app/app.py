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
import json
import requests

from src.inference import predict_one
# Dynamic per-client rules (reads clients/<client_id>/pricing_rules.json)
from pricing_rules import get_rules_for_client, postprocess_rate


# =========================
#   Authentication (0.4.x)
# =========================
def build_auth_objects():
    """
    Build credentials dict for streamlit-authenticator 0.4.x
    from .streamlit/secrets.toml.
    """
    auth_cfg = st.secrets["auth"]
    users = st.secrets.get("users", [])  # [[users]] blocks in secrets.toml

    credentials = {"usernames": {}}
    username_to_client = {}
    username_to_role = {}

    for u in users:
        username = u["username"]
        credentials["usernames"][username] = {
            "name": u["name"],
            "password": u["password"],  # hashed (bcrypt/pbkdf2)
            # "email": u.get("email"),  # optional
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


# ------ Login (0.4.2 writes to st.session_state) ------
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
        "backhaul_available":0,"month":8,"season":"summer","weather":"hot","peak_demand_factor":1.06
    },
    "Jebel Ali → Al Quoz (oversized, 50km)": {
        "client_type":"retailer","origin":"Jebel Ali Port","destination":"Al Quoz",
        "distance_km":50.0,"load_type":"oversized","load_weight_tons":3.2,"vehicle_type":"7t_truck",
        "fuel_price_aed_per_litre":3.1,"salik_gates":2,"salik_charges_aed":8.0,
        "customs_fees_aed":60.0,"waiting_time_hours":2.0,"contract_type":"spot",
        "backhaul_available":0,"month":8,"season":"summer","weather":"hot","peak_demand_factor":1.06
    },
    "Dubai South → Abu Dhabi (reefer, 130km)": {
        "client_type":"distributor","origin":"Dubai South","destination":"Abu Dhabi",
        "distance_km":130.0,"load_type":"reefer","load_weight_tons":8.5,"vehicle_type":"reefer_truck",
        "fuel_price_aed_per_litre":3.1,"salik_gates":4,"salik_charges_aed":16.0,
        "customs_fees_aed":80.0,"waiting_time_hours":1.0,"contract_type":"contract",
        "backhaul_available":1,"month":11,"season":"autumn","weather":"clear","peak_demand_factor":1.02
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
#   Ensure default session state for inputs
# =========================
defaults = PRESETS["Jebel Ali → Al Quoz (dry, 30km)"]
for key, value in defaults.items():
    st.session_state.setdefault(key, value)


# =========================
#   Input form
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

    submitted = st.form_submit_button("Predict")


# =========================
#   Route features (Mapbox) button outside the form
# =========================
st.markdown("—")
colA, colB = st.columns([1, 3])
with colA:
    auto_btn = st.button("Auto-compute distance & SALIK")
with colB:
    st.caption("Use this to pre-fill distance and tolls from origin/destination. You can still edit values before predicting.")

if auto_btn:
    try:
        headers = {"x-client-id": client_id, "x-api-key": API_KEY}
        params = {"origin": st.session_state.get("origin",""), "destination": st.session_state.get("destination","")}
        if not params["origin"] or not params["destination"]:
            st.warning("Please provide both Origin and Destination before auto-computing.")
        else:
            r = requests.get(f"{API_URL}/v1/route_features", headers=headers, params=params, timeout=30)
            r.raise_for_status()
            feat = r.json()
            st.session_state["distance_km"] = feat["distance_km"]
            st.session_state["salik_gates"] = feat["salik_gates"]
            st.session_state["salik_charges_aed"] = feat["salik_charges_aed"]
            st.success("Route features computed and applied. Review the inputs and click Predict.")
    except Exception as e:
        st.error(f"Failed to compute route features: {e}")


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
    }

    try:
        # Model prediction (raw)
        raw = predict_one(payload)

        # Per-client rules (dynamic JSON under clients/<client_id>/pricing_rules.json)
        rules = get_rules_for_client(client_id)

        # Post-processing (minimums, fixed charges, rounding)
        pp = postprocess_rate(raw, payload["vehicle_type"], rules)

        st.subheader("Results")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Model Raw (AED)", f"{pp['raw_rate']:,.2f}")
            st.caption("Direct model output")
        with col2:
            st.metric("Final Rate (AED)", f"{pp['final_rate']:,.2f}")
            st.caption("Minimums + fixed charges + rounding (client rules)")

        # Role-based breakdown visibility
        if user_role in ("admin", "commercial"):
            with st.expander("Breakdown details"):
                st.json({
                    "client_id": client_id,
                    "rules_in_use": rules,
                    "calc": pp
                })
        else:
            st.caption("Contact your administrator to view the breakdown.")

        st.success("Prediction complete.")

    except Exception as e:
        st.error(f"Prediction failed: {e}")
else:
    st.info("Select a preset on the sidebar or fill the form and click **Predict**.")