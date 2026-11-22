"""Look up a home's energy certificate (design doc §5.2, Tier 3).

Service: MHCLG's "Get energy performance of buildings data" API.
    GET {API_BASE}/api/domestic/search?postcode=...            -> {"data": [...], "pagination": {...}}
    GET {API_BASE}/api/certificate?certificate_number=...     -> {"data": {...}}
    Header: Authorization: Bearer <EPC_API_KEY>
(Field names checked against a real response with scripts/epc_probe.py.)

Rules:
- Only look a home up at sign-up, with the person's (or family's) consent.
  Keep only the fields epc_to_profile_fields() returns, not the certificate.
- A postcode returns several homes. If the address doesn't match exactly one,
  find_epc() returns None: show search_epc()'s list and let the user pick,
  or fall back to the sign-up form. Never guess.
- Certificates over 10 years old: ask "has the home been insulated since?"
- Never raises. The model never depends on this API.
"""
import logging
import os
import re
from datetime import date
from pathlib import Path

import requests

from .homes import EPC_MAX_AGE_YEARS

API_BASE = "https://api.get-energy-performance-data.communities.gov.uk"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
REQUEST_TIMEOUT_S = 20

log = logging.getLogger("warmly.epc")


# --------------------------------------------------------------------------- #
# The key
# --------------------------------------------------------------------------- #
def api_key() -> str | None:
    """EPC_API_KEY from the environment, or from the repo's .env file
    (read with the standard library: no python-dotenv)."""
    key = os.environ.get("EPC_API_KEY")
    if key:
        return key.strip()
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "EPC_API_KEY" and value.strip():
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def headers(key: str) -> dict:
    """Request headers for the EPC API."""
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _get(path: str, params: dict) -> dict | None:
    """GET from the API. None (never an exception) on no key, network error or non-200."""
    key = api_key()
    if not key:
        return None
    try:
        response = requests.get(API_BASE + path, params=params, headers=headers(key),
                                timeout=REQUEST_TIMEOUT_S)
        if response.status_code != 200:
            log.warning("EPC API %s returned %s", path, response.status_code)
            return None
        return response.json()
    except Exception:   # network trouble or bad JSON: fall back to the sign-up form
        log.warning("EPC API %s failed", path, exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# Search and match
# --------------------------------------------------------------------------- #
def _words(text: str) -> list[str]:
    """Lower-case words without punctuation: "1, Example Road" -> ["1", "example", "road"]."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _address(record: dict) -> str:
    lines = [record.get(f"addressLine{i}") for i in range(1, 5)]
    return ", ".join(line for line in lines if line)


def matches(record: dict, address_line: str) -> bool:
    """Does a search result match the address the user typed? The first word
    (house number or name) must be equal, so "37" never matches "137", and
    every other typed word must appear in the address."""
    typed, found = _words(address_line), _words(_address(record))
    return bool(typed) and bool(found) and typed[0] == found[0] and set(typed) <= set(found)


def search_epc(postcode: str) -> list[dict]:
    """Every certificate at a postcode, newest first, for a "pick your home"
    list: [{"address", "certificate_number", "band", "registered"}]. [] on any problem."""
    data = _get("/api/domestic/search", {"postcode": postcode})
    records = (data or {}).get("data") or []
    results = [{"address": _address(r), "certificate_number": r.get("certificateNumber"),
                "band": r.get("currentEnergyEfficiencyBand"), "registered": r.get("registrationDate"),
                "uprn": r.get("uprn")} for r in records]
    return sorted(results, key=lambda r: r["registered"] or "", reverse=True)


def get_certificate(certificate_number: str) -> dict | None:
    """One full certificate (the "data" part of the response), or None."""
    data = _get("/api/certificate", {"certificate_number": certificate_number})
    cert = (data or {}).get("data")
    return cert if isinstance(cert, dict) else None


def find_epc(postcode: str, address_line: str) -> dict | None:
    """Search by postcode, pick the one home matching `address_line`, and
    fetch its newest certificate. None if there's no key, a network error,
    a non-200, or not exactly one matching home."""
    candidates = [r for r in search_epc(postcode) if matches(
        {"addressLine1": r["address"]}, address_line)]
    homes = {r["uprn"] or r["address"] for r in candidates}
    if len(homes) != 1:
        return None   # no clear match: let the user pick, or use the form
    return get_certificate(candidates[0]["certificate_number"])   # newest first


# --------------------------------------------------------------------------- #
# Certificate -> profile fields
# --------------------------------------------------------------------------- #
def _property_type(cert: dict) -> str:
    """Map the certificate onto our five property types, from its
    dwelling_type text (e.g. "Detached bungalow", "Mid-terrace house")."""
    text = (cert.get("dwelling_type") or "").lower()
    if "flat" in text or "maisonette" in text:
        return "flat"
    if "bungalow" in text:
        return "bungalow"
    if "semi" in text or "end-terrace" in text or "end terrace" in text:
        return "semi"         # an end terrace has one exposed side wall, like a semi
    if "terrace" in text:
        return "mid_terrace"
    if "detached" in text:
        return "detached"
    return "semi"             # unknown: a middle-of-the-road guess (the form can correct it)


def epc_to_profile_fields(cert: dict) -> dict:
    """The only fields Warmly keeps from a certificate:
    {"sap_score": int, "epc_band": str, "property_type": str,
     "floor_area_m2": float, "lodged": date}"""
    cert = cert.get("data", cert)   # accept the whole response or just "data"
    lodged = cert.get("registration_date") or cert.get("completion_date") or ""
    area = cert.get("total_floor_area")
    return {
        "sap_score": int(cert["energy_rating_current"]),
        "epc_band": str(cert["current_energy_efficiency_band"]).upper(),
        "property_type": _property_type(cert),
        "floor_area_m2": float(area) if area else None,
        "lodged": date.fromisoformat(str(lodged)[:10]) if lodged else None,
    }


def certificate_is_old(lodged: date | None, today: date) -> bool:
    """True if the certificate is over 10 years old: ask at sign-up whether
    the home has been insulated since, and let them override the rating."""
    if lodged is None:
        return True
    return (today - lodged).days > EPC_MAX_AGE_YEARS * 365.25
