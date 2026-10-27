"""Google Flights via SerpApi: ferske priser, ett søk per dato-kombinasjon."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from . import KvoteFeil, nokkel, prisfelt

SERPAPI_URL = "https://serpapi.com/search.json"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "serpapi_eksempel.json"

# SerpApi "stops": 1 = kun direkte, 2 = maks 1 stopp, 3 = maks 2 stopp
STOPS_PARAM = {0: 1, 1: 2, 2: 3}


def velg_neste_sok(kombinasjoner, sjekket: dict, antall: int, prioriterte: set[str] = frozenset()):
    """Velg hvilke dato-kombinasjoner som skal søkes nå.

    Prioriterte kombinasjoner (billige funn som må bekreftes eller sjekkes på
    nytt) går først, men minst ett søk per kjøring går til rotasjonen når
    antall >= 2, slik at resten av perioden også blir dekket. Rotasjonen tar
    usjekkede først, deretter de som er sjekket for lengst siden. Innenfor
    samme prioritet stokkes rekkefølgen deterministisk (hash), slik at søkene
    sprer seg over hele perioden.
    """
    def sortering(k):
        n = nokkel(*k)
        return (sjekket.get(n, ""), hashlib.sha1(n.encode()).hexdigest())

    prior = sorted((k for k in kombinasjoner if nokkel(*k) in prioriterte), key=sortering)
    rotasjon = sorted((k for k in kombinasjoner if nokkel(*k) not in prioriterte), key=sortering)
    plasser_prior = antall - 1 if antall >= 2 and rotasjon else antall
    valgt = prior[:plasser_prior]
    return valgt + rotasjon[:antall - len(valgt)]


def _hent(params: dict) -> dict:
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


def sok(cfg: dict, ut: date, hjem: date, api_key: str) -> dict:
    """Ett søk som dekker alle avreise- og ankomstflyplasser.

    Avviser SerpApi flere flyplasser i samme søk, prøves det på nytt med bare
    Gardermoen og Bangkok, og feilen logges, slik at det blir synlig.
    """
    params = {
        "engine": "google_flights",
        "departure_id": ",".join(f["kode"] for f in cfg["avreise_flyplasser"]),
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
    data = _hent(params)
    feil = data.get("error", "")
    # «hasn't returned any results» betyr bare at det ikke fantes flyvninger – ikke prøv på nytt.
    if feil and "," in params["departure_id"] + params["arrival_id"] and "result" not in feil.lower():
        data = _hent({**params, "departure_id": cfg["avreise_flyplasser"][0]["kode"], "arrival_id": "BKK"})
        data["_advarsel"] = f"flere flyplasser i samme søk ble avvist ({feil[:100]}); søkte bare OSL → BKK"
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


def sok_lenke(cfg: dict, fra: str, til: str, ut: date, hjem: date) -> str:
    """Google Flights-søk for akkurat denne ruten og disse datoene."""
    q = (f"Flights from {fra} to {til} on {ut.isoformat()} through {hjem.isoformat()} "
         f"for {cfg['voksne']} adults")
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode(
        {"q": q, "curr": cfg["valuta"], "hl": "no", "gl": "no"})


def tolk_resultat(cfg: dict, data: dict, ut: date, hjem: date, tidspunkt: str) -> list[dict]:
    """Gjør om SerpApi-svaret til en flat liste med reisealternativer."""
    sok_url = data.get("search_metadata", {}).get("google_flights_url")
    funn = []
    for gruppe in ("best_flights", "other_flights"):
        for alt in data.get(gruppe, []):
            pris = alt.get("price")
            segmenter = alt.get("flights", [])
            if not pris or not segmenter:
                continue
            per_person = pris / cfg["voksne"] if cfg["pris_fra_api_er_totalt_for_alle"] else pris
            fra = segmenter[0].get("departure_airport", {}).get("id", cfg["avreise_flyplasser"][0]["kode"])
            til = segmenter[-1].get("arrival_airport", {}).get("id", "?")
            notat = ""
            if per_person < cfg["mistenkelig_lav_pris_per_person_nok"]:
                notat = ("mistenkelig lav pris – kontroller om prisen er per person "
                         "(innstillingen pris_fra_api_er_totalt_for_alle)")
            funn.append({
                "kilde": "Google Flights",
                "type": "fly",
                "utreise": ut.isoformat(),
                "hjemreise": hjem.isoformat(),
                "netter": (hjem - ut).days,
                "til": til,
                "flyselskap": " / ".join(dict.fromkeys(s.get("airline", "?") for s in segmenter)),
                "stopp": len(segmenter) - 1,
                "via": ", ".join(l.get("id", "?") for l in alt.get("layovers", [])),
                "reisetid_t": round(alt.get("total_duration", 0) / 60, 1),
                **prisfelt(cfg, per_person, bagasje_status(alt), fra),
                # SerpApi-lenken gjenskaper nøyaktig søket som ga prisen; egen lenke er reserve.
                "lenke": sok_url or sok_lenke(cfg, fra, til, ut, hjem),
                "lenke_type": "søk med datoer",
                "sjekket": tidspunkt,
                "notat": notat,
            })
    return funn
