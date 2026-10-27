"""Travelpayouts / Aviasales Data API: gratis, bufrede lavpriser for hele måneder.

Prisene kommer fra søk andre Aviasales-brukere har gjort de siste døgnene. De er
derfor ikke garantert fortsatt tilgjengelige, men gir et billig overblikk over
hvilke datoer som er verdt å bekrefte i Google Flights.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from . import KvoteFeil, med_bagasje, nokkel

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "travelpayouts_eksempel.json"


def maaneder(fra: date, til: date) -> list[str]:
    ut, mnd = [], date(fra.year, fra.month, 1)
    while mnd <= til:
        ut.append(mnd.strftime("%Y-%m"))
        mnd = (mnd + timedelta(days=32)).replace(day=1)
    return ut


def sporringer(cfg: dict) -> list[dict]:
    """Én spørring per destinasjon og (utreisemåned, hjemreisemåned)."""
    forste = date.fromisoformat(cfg["tidligste_utreise"])
    siste = date.fromisoformat(cfg["seneste_hjemkomst"])
    ut_mnd = maaneder(forste, siste - timedelta(days=cfg["min_netter"]))
    hjem_mnd = maaneder(forste + timedelta(days=cfg["min_netter"]), siste)
    return [
        {"destination": dest, "departure_at": u, "return_at": h}
        for dest in cfg["travelpayouts_destinasjoner"]
        for u in ut_mnd for h in hjem_mnd if u <= h
    ]


def hent(cfg: dict, sporring: dict, token: str) -> list[dict]:
    params = {
        "origin": cfg["fra_flyplass"],
        "one_way": "false",
        "direct": "false",
        "currency": cfg["valuta"].lower(),
        "sorting": "price",
        "unique": "false",
        "limit": 1000,
        "token": token,
        **sporring,
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=60) as svar:
            data = json.load(svar)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise KvoteFeil(f"HTTP {e.code}") from e
        raise
    if not data.get("success", False):
        raise RuntimeError(data.get("error") or "ukjent feil")
    return data.get("data", [])


def hent_demo(cfg: dict, sporring: dict, token: str) -> list[dict]:
    rader = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
    return [r for r in rader if r["destination"] == sporring["destination"]
            and r["departure_at"].startswith(sporring["departure_at"])
            and r["return_at"].startswith(sporring["return_at"])]


def tolk(cfg: dict, rader: list[dict], gyldige: set[str], tidspunkt: str) -> dict[str, dict]:
    """Billigste tilbud per dato-kombinasjon som oppfyller kriteriene."""
    beste: dict[str, dict] = {}
    for r in rader:
        try:
            ut = date.fromisoformat(r["departure_at"][:10])
            hjem = date.fromisoformat(r["return_at"][:10])
            pris = float(r["price"])
        except (KeyError, TypeError, ValueError):
            continue
        n = nokkel(ut, hjem)
        stopp = max(r.get("transfers", 0) or 0, r.get("return_transfers", 0) or 0)
        if n not in gyldige or stopp > cfg["maks_stopp"]:
            continue
        lenke = r.get("link") or ""
        if lenke.startswith("/"):
            lenke = "https://www.aviasales.com" + lenke
        tilbud = {
            "kilde": "Aviasales",
            "type": "fly",
            "utreise": ut.isoformat(),
            "hjemreise": hjem.isoformat(),
            "netter": (hjem - ut).days,
            "til": r.get("destination_airport") or r.get("destination", "?"),
            "flyselskap": r.get("airline", "?"),
            "stopp": stopp,
            "via": "",
            "reisetid_t": round((r.get("duration_to") or r.get("duration") or 0) / 60, 1),
            "pris_per_person": round(pris),  # Aviasales oppgir pris for én voksen
            "bagasje": "ukjent",
            "pris_per_person_med_bag": med_bagasje(cfg, pris, "ukjent"),
            "lenke": lenke or "https://www.aviasales.com",
            "sjekket": tidspunkt,
            "notat": "bufret pris, bekreft før bestilling",
        }
        if n not in beste or tilbud["pris_per_person"] < beste[n]["pris_per_person"]:
            beste[n] = tilbud
    return beste
