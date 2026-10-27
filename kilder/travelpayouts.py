"""Travelpayouts / Aviasales Data API: gratis, bufrede lavpriser for hele måneder.

Prisene kommer fra søk andre Aviasales-brukere har gjort de siste døgnene. De er
derfor ikke garantert fortsatt tilgjengelige, men gir et billig overblikk over
hvilke datoer som er verdt å bekrefte i Google Flights.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from . import KvoteFeil, nokkel, prisfelt

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "travelpayouts_eksempel.json"


def maaneder(fra: date, til: date) -> list[str]:
    ut, mnd = [], date(fra.year, fra.month, 1)
    while mnd <= til:
        ut.append(mnd.strftime("%Y-%m"))
        mnd = (mnd + timedelta(days=32)).replace(day=1)
    return ut


def sporringer(cfg: dict) -> list[dict]:
    """Én spørring per avreiseflyplass, destinasjon og (utreisemåned, hjemreisemåned)."""
    forste = date.fromisoformat(cfg["tidligste_utreise"])
    siste = date.fromisoformat(cfg["seneste_avreise_hjem"])
    ut_mnd = maaneder(forste, siste - timedelta(days=cfg["min_netter"]))
    hjem_mnd = maaneder(forste + timedelta(days=cfg["min_netter"]), siste)
    return [
        {"origin": fra["kode"], "destination": dest, "departure_at": u, "return_at": h}
        for fra in cfg["avreise_flyplasser"]
        for dest in cfg["travelpayouts_destinasjoner"]
        for u in ut_mnd for h in hjem_mnd if u <= h
    ]


def hent(cfg: dict, sporring: dict, token: str) -> list[dict]:
    params = {
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
    return [r for r in rader if r["origin"] == sporring["origin"] and r["destination"] == sporring["destination"]
            and r["departure_at"].startswith(sporring["departure_at"])
            and r["return_at"].startswith(sporring["return_at"])]


def lenke_for_voksne(lenke: str, voksne: int) -> str:
    """Aviasales-lenken gjelder 1 voksen. Søkekoden slutter med antall voksne
    (OSL2611BKK0912 + 1), så den byttes til riktig antall. Billett-id-en (?t=)
    beholdes, slik at lenken fortsatt åpner akkurat denne billetten."""
    if lenke.startswith("/"):
        lenke = "https://www.aviasales.com" + lenke
    return re.sub(r"(/search/[A-Z]{3}\d{4}[A-Z]{3}(?:\d{4})?)\d", rf"\g<1>{voksne}", lenke, count=1)


def tolk(cfg: dict, rader: list[dict], gyldige: set[str], tidspunkt: str) -> dict[str, dict]:
    """Billigste tilbud per dato-kombinasjon (målt på totalpris inkl. reise til
    avreiseflyplassen) som oppfyller kriteriene."""
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
        lenke = lenke_for_voksne(r.get("link") or "", cfg["voksne"])
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
            # Aviasales oppgir pris for én voksen; at det finnes 2 seter er ikke sjekket.
            **prisfelt(cfg, pris, "ukjent", r.get("origin", "OSL")),
            "lenke": lenke or "https://www.aviasales.com",
            "lenke_type": "billett" if lenke else "oversiktsside",
            "sjekket": tidspunkt,
            "notat": "bufret pris for 1 person – bekreft pris for 2 før bestilling",
        }
        if n not in beste or tilbud["totalpris_per_person"] < beste[n]["totalpris_per_person"]:
            beste[n] = tilbud
    return beste
