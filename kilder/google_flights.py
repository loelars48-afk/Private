"""Google Flights via SerpApi: ferske priser, ett søk per dato-kombinasjon."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from . import KvoteFeil, med_bagasje, nokkel

SERPAPI_URL = "https://serpapi.com/search.json"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "serpapi_eksempel.json"

# SerpApi "stops": 1 = kun direkte, 2 = maks 1 stopp, 3 = maks 2 stopp
STOPS_PARAM = {0: 1, 1: 2, 2: 3}


def velg_neste_sok(kombinasjoner, sjekket: dict, antall: int, prioriterte: set[str] = frozenset()):
    """Velg hvilke dato-kombinasjoner som skal søkes nå.

    Rekkefølge: kombinasjoner andre kilder har funnet billige (prioriterte) og
    som ikke er bekreftet ennå, deretter usjekkede, deretter de som er sjekket
    for lengst siden. Innenfor samme prioritet stokkes rekkefølgen
    deterministisk (hash), slik at søkene sprer seg over hele perioden.
    """
    def sortering(k):
        n = nokkel(*k)
        return (n not in prioriterte, sjekket.get(n, ""), hashlib.sha1(n.encode()).hexdigest())

    return sorted(kombinasjoner, key=sortering)[:antall]


def sok(cfg: dict, ut: date, hjem: date, api_key: str) -> dict:
    params = {
        "engine": "google_flights",
        "departure_id": cfg["fra_flyplass"],
        "arrival_id": ",".join(cfg["til_flyplasser"]),
        "outbound_date": ut.isoformat(),
        "return_date": hjem.isoformat(),
        "type": 1,
        "adults": cfg["voksne"],
        "currency": cfg["valuta"],
        "hl": "en",  # engelsk gir forutsigbare bagasjetekster å tolke
        "gl": "no",
        "stops": STOPS_PARAM[cfg["maks_stopp"]],
        "api_key": api_key,
    }
    url = SERPAPI_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=90) as svar:
            data = json.load(svar)
    except urllib.error.HTTPError as e:
        tekst = e.read().decode("utf-8", "replace")
        if e.code in (401, 403, 429):
            raise KvoteFeil(f"HTTP {e.code}: {tekst[:200]}") from e
        try:
            data = json.loads(tekst)
        except ValueError:
            raise RuntimeError(f"HTTP {e.code}: {tekst[:200]}") from e
    feil = data.get("error", "")
    if feil and any(o in feil.lower() for o in ("run out", "limit", "invalid api key")):
        raise KvoteFeil(feil)
    return data


def sok_demo(cfg: dict, ut: date, hjem: date, api_key: str) -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Varier prisen litt per dato så demo-rapporten ser realistisk ut.
    justering = int(hashlib.sha1(nokkel(ut, hjem).encode()).hexdigest()[:4], 16) % 4000 - 1500
    for gruppe in ("best_flights", "other_flights"):
        for f in data.get(gruppe, []):
            f["price"] = f["price"] + justering
    return data


def bagasje_status(alternativ: dict) -> str:
    """'inkludert', 'ikke inkludert' eller 'ukjent' for innsjekket bagasje."""
    tekster = list(alternativ.get("extensions", []))
    for seg in alternativ.get("flights", []):
        tekster += seg.get("extensions", [])
    tekst = " ".join(tekster).lower()
    if "checked bag" not in tekst and "checked baggage" not in tekst:
        return "ukjent"
    if any(o in tekst for o in ("for a fee", "not included", "checked baggage fee")):
        return "ikke inkludert"
    if any(o in tekst for o in ("free checked", "checked bag included", "checked baggage included")):
        return "inkludert"
    return "ukjent"


def tolk_resultat(cfg: dict, data: dict, ut: date, hjem: date, tidspunkt: str) -> list[dict]:
    """Gjør om SerpApi-svaret til en flat liste med reisealternativer."""
    url = data.get("search_metadata", {}).get("google_flights_url") or "https://www.google.com/travel/flights"
    funn = []
    for gruppe in ("best_flights", "other_flights"):
        for alt in data.get(gruppe, []):
            pris = alt.get("price")
            segmenter = alt.get("flights", [])
            if not pris or not segmenter:
                continue
            per_person = pris / cfg["voksne"] if cfg["pris_fra_api_er_totalt_for_alle"] else pris
            bag = bagasje_status(alt)
            funn.append({
                "kilde": "Google Flights",
                "type": "fly",
                "utreise": ut.isoformat(),
                "hjemreise": hjem.isoformat(),
                "netter": (hjem - ut).days,
                "til": segmenter[-1].get("arrival_airport", {}).get("id", "?"),
                "flyselskap": " / ".join(dict.fromkeys(s.get("airline", "?") for s in segmenter)),
                "stopp": len(segmenter) - 1,
                "via": ", ".join(l.get("id", "?") for l in alt.get("layovers", [])),
                "reisetid_t": round(alt.get("total_duration", 0) / 60, 1),
                "pris_per_person": round(per_person),
                "bagasje": bag,
                "pris_per_person_med_bag": med_bagasje(cfg, per_person, bag),
                "lenke": url,
                "sjekket": tidspunkt,
                "notat": "",
            })
    return funn
