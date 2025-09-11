import os
import math
import requests
from typing import Dict, Tuple, List

# --- Simple SALIK model (placeholder) ---
# TODO: Replace with accurate geofences for each gate
SALIK_GATES = [
    (25.0977, 55.1720),  # Al Barsha (approx)
    (25.2285, 55.2896),  # Al Garhoud
    (25.2430, 55.3426),  # Al Maktoum Bridge
    (25.2521, 55.3400),  # Airport Tunnel
]

def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    R = 6371.0
    lat1, lon1 = a
    lat2, lon2 = b
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    lat1 = math.radians(lat1); lat2 = math.radians(lat2)
    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(x))

def count_salik_on_route(polyline_latlon: List[Tuple[float, float]], threshold_km: float = 0.25) -> int:
    count = 0
    for gate in SALIK_GATES:
        near = any(haversine_km((lat, lon), gate) <= threshold_km for lat, lon in polyline_latlon)
        if near:
            count += 1
    return count

def mapbox_geocode(q: str, token: str) -> Dict[str, float]:
    # Forward geocoding
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{requests.utils.quote(q)}.json"
    params = {"access_token": token, "limit": 1, "language": "en"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("features"):
        raise ValueError(f"Address not found: {q}")
    # center: [lon, lat]
    lon, lat = data["features"][0]["center"]
    return {"lat": float(lat), "lon": float(lon)}

def mapbox_route(origin: Dict[str, float], destination: Dict[str, float], token: str) -> Dict:
    # Driving route with full geometry
    # Docs: https://docs.mapbox.com/api/navigation/directions/
    base = "https://api.mapbox.com/directions/v5/mapbox/driving"
    coords = f"{origin['lon']},{origin['lat']};{destination['lon']},{destination['lat']}"
    params = {
        "access_token": token,
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "false",
        "language": "en",
    }
    r = requests.get(f"{base}/{coords}", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("routes"):
        raise ValueError("No route found")
    return data["routes"][0]

def compute_route_features(origin_text: str, destination_text: str) -> Dict:
    token = os.environ.get("MAPBOX_TOKEN")
    if not token:
        raise RuntimeError("MAPBOX_TOKEN is not set in environment variables")

    # 1) Geocode
    o = mapbox_geocode(origin_text, token)
    d = mapbox_geocode(destination_text, token)

    # 2) Route
    route = mapbox_route(o, d, token)
    distance_km = float(route["distance"]) / 1000.0

    # 3) Polyline coords to (lat, lon)
    coords = route["geometry"]["coordinates"]  # [ [lon,lat], ... ]
    poly_latlon = [(lat, lon) for lon, lat in coords]

    # 4) SALIK heuristic
    salik_gates = count_salik_on_route(poly_latlon, threshold_km=0.25)
    salik_charges_aed = round(salik_gates * 4.0, 2)  # simple assumption: 4 AED/gate

    return {
        "origin": {"lat": o["lat"], "lon": o["lon"], "label": origin_text},
        "destination": {"lat": d["lat"], "lon": d["lon"], "label": destination_text},
        "distance_km": round(distance_km, 2),
        "salik_gates": int(salik_gates),
        "salik_charges_aed": salik_charges_aed,
        "provider": "mapbox:geocoding+directions",
    }