from __future__ import annotations


# constantes de reglas y funciones reutilizables
PRICING_RULES = {
    "global": {
        "round_to": 5,
        "fixed_charges_aed": 25.0,
        "gate_in_out_aed": 15.0
    },
    "vehicle_minimums": {
        "van": 200.0,
        "3t_truck": 220.0,
        "7t_truck": 250.0,
        "flatbed": 300.0,
        "reefer_truck": 320.0
    }
}

def apply_minimum(rate: float, vehicle_type: str, rules: dict) -> float:
    vehicle_min = rules["vehicle_minimums"].get(vehicle_type, 0.0)
    return max(rate, vehicle_min)

def add_fixed_charges(rate: float, rules: dict) -> float:
    fixed = rules["global"].get("fixed_charges_aed", 0.0)
    gate = rules["global"].get("gate_in_out_aed", 0.0)
    return rate + fixed + gate

def round_to_multiple(value: float, multiple: int) -> float:
    return round(value / multiple) * multiple

def postprocess_rate(raw_rate: float, vehicle_type: str, rules: dict) -> dict:
    steps = {}
    steps["raw_rate"] = round(raw_rate, 2)

    r = apply_minimum(raw_rate, vehicle_type, rules)
    steps["after_minimum"] = round(r, 2)

    r = add_fixed_charges(r, rules)
    steps["after_fixed_charges"] = round(r, 2)

    multiple = rules["global"].get("round_to", 5)
    r = round_to_multiple(r, multiple)
    steps["final_rate"] = round(r, 2)
    steps["rounded_multiple"] = multiple

    steps["fixed_charges"] = {
        "doc_fee": rules["global"].get("fixed_charges_aed", 0.0),
        "gate_in_out": rules["global"].get("gate_in_out_aed", 0.0)
    }
    steps["vehicle_minimum_applied"] = rules["vehicle_minimums"].get(vehicle_type, 0.0)
    return steps


import json, os
from pathlib import Path
from typing import Any, Dict, Optional

# Defaults (por si falta algún campo en el JSON del cliente)
DEFAULT_RULES: Dict[str, Any] = {
    "global": {"round_to": 5, "fixed_charges_aed": 25.0, "gate_in_out_aed": 15.0},
    "vehicle_minimums": {"van": 200.0, "3t_truck": 220.0, "7t_truck": 250.0, "flatbed": 300.0, "reefer_truck": 320.0}
}

CLIENTS_DIR = Path(os.environ.get("CLIENTS_DIR", Path(__file__).resolve().parents[1] / "clients"))

_cache: Dict[str, Dict[str, Any]] = {}  # client_id -> {"rules": dict, "mtime": float}

def _safe_merge(base: Dict[str, Any], inc: Dict[str, Any]) -> Dict[str, Any]:
    out = base.copy()
    for k, v in inc.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _safe_merge(out[k], v)
        else:
            out[k] = v
    return out

def get_rules_for_client(client_id: str) -> Dict[str, Any]:
    """Lee clients/<client_id>/pricing_rules.json con caché y mezcla defaults."""
    rules_path = CLIENTS_DIR / client_id / "pricing_rules.json"
    if not rules_path.exists():
        # Fallback: si no existe, usa defaults (o levanta error en app.py si prefieres)
        return DEFAULT_RULES

    mtime = rules_path.stat().st_mtime
    cached = _cache.get(client_id)
    if cached and cached.get("mtime") == mtime:
        return cached["rules"]

    data = json.loads(rules_path.read_text(encoding="utf-8"))
    merged = _safe_merge(DEFAULT_RULES, data)
    _cache[client_id] = {"rules": merged, "mtime": mtime}
    return merged

# ——— utilidades de postprocesado (igual que ya tenías) ———
def apply_minimum(rate: float, vehicle_type: str, rules: Dict[str, Any]) -> float:
    return max(rate, float(rules.get("vehicle_minimums", {}).get(vehicle_type, 0.0)))

def add_fixed_charges(rate: float, rules: Dict[str, Any]) -> float:
    g = rules.get("global", {})
    return rate + float(g.get("fixed_charges_aed", 0.0)) + float(g.get("gate_in_out_aed", 0.0))

def round_to_multiple(value: float, multiple: int) -> float:
    return round(value / multiple) * multiple  # si quieres “ceil”, cámbialo

def postprocess_rate(raw_rate: float, vehicle_type: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    r = float(round(raw_rate, 2))
    after_min = apply_minimum(r, vehicle_type, rules)
    after_fixed = add_fixed_charges(after_min, rules)
    multiple = int(rules.get("global", {}).get("round_to", 5))
    final = round_to_multiple(after_fixed, multiple)
    return {
        "raw_rate": round(r, 2),
        "after_minimum": round(after_min, 2),
        "after_fixed_charges": round(after_fixed, 2),
        "final_rate": round(final, 2),
        "rounded_multiple": multiple,
        "fixed_charges": {
            "doc_fee": float(rules.get("global", {}).get("fixed_charges_aed", 0.0)),
            "gate_in_out": float(rules.get("global", {}).get("gate_in_out_aed", 0.0)),
        },
        "vehicle_minimum_applied": float(rules.get("vehicle_minimums", {}).get(vehicle_type, 0.0)),
    }