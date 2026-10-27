#!/usr/bin/env python3
"""Thailand-agenten: leter etter billige tur/retur-billetter Oslo -> Thailand.

Agenten henter priser fra Google Flights via SerpApi. Hver kjøring sjekker
noen få dato-kombinasjoner (for å spare API-kvote) og roterer slik at alle
kombinasjoner innenfor reisevinduet blir sjekket etter hvert. Resultatene
lagres i data/ og oppsummeres i RAPPORT.md.

Bruk:
    SERPAPI_KEY=... python thailand_agent.py          # ekte søk
    python thailand_agent.py --demo                    # testdata, ingen nøkkel
    python thailand_agent.py --dry-run                 # vis hva som ville blitt søkt
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERPAPI_URL = "https://serpapi.com/search.json"
FIXTURE = ROOT / "tests" / "fixtures" / "serpapi_eksempel.json"

# SerpApi "stops": 1 = kun direkte, 2 = maks 1 stopp, 3 = maks 2 stopp
STOPS_PARAM = {0: 1, 1: 2, 2: 3}

FLYPLASSER = {
    "BKK": "Bangkok Suvarnabhumi",
    "DMK": "Bangkok Don Mueang",
    "HKT": "Phuket",
    "KBV": "Krabi",
    "USM": "Koh Samui",
}

# Restplass/charter finnes ikke i Google Flights og må sjekkes manuelt.
RESTPLASS_LENKER = [
    ("Ving", "https://www.ving.no"),
    ("TUI", "https://www.tui.no"),
    ("Apollo", "https://www.apollo.no"),
]


class KvoteFeil(Exception):
    """API-et nekter flere søk (kvote brukt opp eller ugyldig nøkkel)."""


# ---------------------------------------------------------------- konfig/lagring

def last_json(sti: Path, standard):
    if sti.exists():
        return json.loads(sti.read_text(encoding="utf-8"))
    return standard


def lagre_json(sti: Path, data) -> None:
    sti.parent.mkdir(parents=True, exist_ok=True)
    sti.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- dato-kombinasjoner

def dato_kombinasjoner(cfg: dict, idag: date) -> list[tuple[date, date]]:
    """Alle (utreise, hjemreise) som oppfyller reisevinduet og antall netter."""
    forste = max(date.fromisoformat(cfg["tidligste_utreise"]), idag + timedelta(days=1))
    siste_hjem = date.fromisoformat(cfg["seneste_hjemkomst"])
    kombinasjoner = []
    ut = forste
    while ut + timedelta(days=cfg["min_netter"]) <= siste_hjem:
        for netter in range(cfg["min_netter"], cfg["maks_netter"] + 1):
            hjem = ut + timedelta(days=netter)
            if hjem > siste_hjem:
                break
            kombinasjoner.append((ut, hjem))
        ut += timedelta(days=1)
    return kombinasjoner


def nokkel(ut: date, hjem: date) -> str:
    return f"{ut.isoformat()}|{hjem.isoformat()}"


def velg_neste_sok(kombinasjoner, sjekket: dict, antall: int):
    """Usjekkede først, deretter de som er sjekket for lengst siden.

    Innenfor samme prioritet stokkes rekkefølgen deterministisk (hash), slik at
    de første søkene sprer seg over hele perioden i stedet for å starte med
    alle varianter av første utreisedato.
    """
    def sortering(k):
        n = nokkel(*k)
        return (sjekket.get(n, ""), hashlib.sha1(n.encode()).hexdigest())

    return sorted(kombinasjoner, key=sortering)[:antall]


# ---------------------------------------------------------------- API

def sok_serpapi(cfg: dict, ut: date, hjem: date, api_key: str) -> dict:
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
    data.setdefault("search_metadata", {})["google_flights_url"] = "https://www.google.com/travel/flights"
    return data


# ---------------------------------------------------------------- tolking

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
    url = data.get("search_metadata", {}).get("google_flights_url", "")
    bag_per_reise = 2 * cfg["estimert_innsjekket_bagasje_per_person_per_vei_nok"]
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
                "utreise": ut.isoformat(),
                "hjemreise": hjem.isoformat(),
                "netter": (hjem - ut).days,
                "til": segmenter[-1].get("arrival_airport", {}).get("id", "?"),
                "flyselskap": " / ".join(dict.fromkeys(s.get("airline", "?") for s in segmenter)),
                "stopp": len(segmenter) - 1,
                "via": ", ".join(l.get("id", "?") for l in alt.get("layovers", [])),
                "reisetid_t": round(alt.get("total_duration", 0) / 60, 1),
                "pris_totalt": round(pris),
                "pris_per_person": round(per_person),
                "bagasje": bag,
                "pris_per_person_med_bag": round(per_person + (0 if bag == "inkludert" else bag_per_reise)),
                "lenke": url,
                "sjekket": tidspunkt,
            })
    return funn


# ---------------------------------------------------------------- rapport

def kr(belop: int) -> str:
    return f"{belop:,}".replace(",", " ") + " kr"


def bag_tekst(f: dict) -> str:
    if f["bagasje"] == "inkludert":
        return "inkl."
    if f["bagasje"] == "ikke inkludert":
        return f"{kr(f['pris_per_person_med_bag'])} (est.)"
    return f"{kr(f['pris_per_person_med_bag'])} (est., ukjent)"


def tabell(rader: list[dict]) -> str:
    if not rader:
        return "_Ingen._\n"
    linjer = [
        "| Pris p.p. | Med innsj. bag p.p. | Utreise | Hjem | Netter | Til | Selskap | Stopp | Reisetid | Sjekket | |",
        "|---:|---:|---|---|---:|---|---|---|---:|---|---|",
    ]
    for f in rader:
        stopp = f"{f['stopp']}" + (f" ({f['via']})" if f["via"] else "")
        linjer.append(
            f"| **{kr(f['pris_per_person'])}** | {bag_tekst(f)} | {f['utreise']} | {f['hjemreise']} "
            f"| {f['netter']} | {f['til']} | {f['flyselskap']} | {stopp} | {f['reisetid_t']} t "
            f"| {f['sjekket'][:16].replace('T', ' ')} | [Se]({f['lenke']}) |"
        )
    return "\n".join(linjer) + "\n"


def lag_rapport(cfg: dict, funn: dict, kombinasjoner, sjekket: dict, nå: datetime, logg: list[str]) -> str:
    gyldige = {nokkel(*k) for k in kombinasjoner}
    # Beste alternativ per dato-kombinasjon som fortsatt er aktuell
    beste = [f for n, f in funn.items() if n in gyldige]
    beste.sort(key=lambda f: f["pris_per_person"])
    maks = cfg["maks_pris_per_person_nok"]
    margin = cfg["nesten_treff_margin_nok"]
    treff = [f for f in beste if f["pris_per_person"] <= maks]
    nesten = [f for f in beste if maks < f["pris_per_person"] <= maks + margin]
    dekket = sum(1 for n in gyldige if n in sjekket)
    siste_dogn = sum(1 for n in gyldige if sjekket.get(n, "") >= (nå - timedelta(days=1)).isoformat())

    status = (f"✅ **{len(treff)} treff** innenfor {kr(maks)} per person." if treff
              else f"Ingen treff under {kr(maks)} per person ennå.")
    deler = [
        "# Thailand-agenten – rapport",
        "",
        f"Sist oppdatert: {nå.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        status,
        "",
        "## Søkekriterier",
        "",
        f"- {cfg['voksne']} voksne, tur/retur fra {cfg['fra_flyplass']} til "
        + ", ".join(f"{k} ({FLYPLASSER.get(k, k)})" for k in cfg["til_flyplasser"]),
        f"- Utreise fra {cfg['tidligste_utreise']}, hjemme senest {cfg['seneste_hjemkomst']}, "
        f"{cfg['min_netter']}–{cfg['maks_netter']} netter",
        f"- Makspris {kr(maks)} per person (flybillett), maks {cfg['maks_stopp']} stopp",
        f"- Dekning: {dekket} av {len(gyldige)} dato-kombinasjoner sjekket, "
        f"{siste_dogn} siste døgn",
        "",
        f"## Treff (≤ {kr(maks)} per person)",
        "",
        tabell(treff),
        f"## Nesten-treff ({kr(maks + 1)}–{kr(maks + margin)} per person)",
        "",
        tabell(nesten),
        "## 10 billigste totalt",
        "",
        tabell(beste[:10]),
        "## Slik leser du rapporten",
        "",
        "- **Pris p.p.** er flybillett per person med håndbagasje, hentet fra Google Flights.",
        f"- **Med innsj. bag p.p.** legger til et *estimat* på "
        f"{kr(cfg['estimert_innsjekket_bagasje_per_person_per_vei_nok'])} per person per vei "
        "når bagasje ikke er inkludert eller ikke er oppgitt. Sjekk faktisk pris hos selskapet.",
        "- Prisene er øyeblikksbilder. Sjekk «Sjekket»-tidspunktet. Lavpriser forsvinner raskt.",
        "- Lenken åpner Google Flights-søket. Selve bestillingen gjør du hos flyselskap/byrå.",
        "",
        "## Restplasser og charter (sjekkes manuelt)",
        "",
        "Charterfly fra turoperatørene vises ikke i Google Flights. Sjekk restplass-sidene her:",
        "",
        *[f"- [{navn}]({url})" for navn, url in RESTPLASS_LENKER],
        "",
        "## Logg fra siste kjøring",
        "",
        *[f"- {linje}" for linje in logg],
        "",
    ]
    return "\n".join(deler)


# ---------------------------------------------------------------- hovedløp

def kjor(args) -> int:
    cfg = last_json(ROOT / "config.json", None)
    data_dir = Path(args.data_dir)
    tilstand_sti = data_dir / "tilstand.json"
    funn_sti = data_dir / "funn.json"
    historikk_sti = data_dir / "prishistorikk.csv"

    nå = datetime.now(timezone.utc).replace(microsecond=0)
    tidspunkt = nå.isoformat()
    tilstand = last_json(tilstand_sti, {"sjekket": {}})
    funn = last_json(funn_sti, {})

    kombinasjoner = dato_kombinasjoner(cfg, nå.date())
    logg = []
    if not kombinasjoner:
        logg.append("Reisevinduet er passert – ingen kombinasjoner igjen å søke på.")
    antall = args.antall or cfg["sok_per_kjoring"]
    neste = velg_neste_sok(kombinasjoner, tilstand["sjekket"], antall)

    if args.dry_run:
        print(f"{len(kombinasjoner)} kombinasjoner totalt. Neste {len(neste)} søk:")
        for ut, hjem in neste:
            print(f"  {ut} -> {hjem} ({(hjem - ut).days} netter)")
        return 0

    if args.demo:
        sok, api_key = sok_demo, ""
    else:
        api_key = os.environ.get("SERPAPI_KEY", "")
        if not api_key:
            print("Mangler SERPAPI_KEY. Bruk --demo for å teste uten nøkkel.", file=sys.stderr)
            return 2
        sok = sok_serpapi

    nye_rader = []
    for ut, hjem in neste:
        n = nokkel(ut, hjem)
        try:
            data = sok(cfg, ut, hjem, api_key)
        except KvoteFeil as e:
            logg.append(f"Stoppet: API-kvote/nøkkel-feil ({e}).")
            break
        except Exception as e:  # nettverk o.l. – prøv igjen neste kjøring
            logg.append(f"{ut} → {hjem}: feil ({e}). Prøves igjen senere.")
            continue
        tilstand["sjekket"][n] = tidspunkt
        alternativer = tolk_resultat(cfg, data, ut, hjem, tidspunkt)
        if not alternativer:
            funn.pop(n, None)
            logg.append(f"{ut} → {hjem}: ingen flyvninger funnet.")
            continue
        billigst = min(alternativer, key=lambda f: f["pris_per_person"])
        funn[n] = billigst
        nye_rader.append(billigst)
        merke = " ✅ TREFF" if billigst["pris_per_person"] <= cfg["maks_pris_per_person_nok"] else ""
        logg.append(f"{ut} → {hjem}: billigst {kr(billigst['pris_per_person'])} p.p. "
                    f"til {billigst['til']} ({billigst['flyselskap']}){merke}")

    # Rydd bort datoer som ikke lenger er aktuelle
    gyldige = {nokkel(*k) for k in kombinasjoner}
    funn = {n: f for n, f in funn.items() if n in gyldige}
    tilstand["sjekket"] = {n: t for n, t in tilstand["sjekket"].items() if n in gyldige}

    lagre_json(tilstand_sti, tilstand)
    lagre_json(funn_sti, funn)
    if nye_rader:
        ny_fil = not historikk_sti.exists()
        with historikk_sti.open("a", newline="", encoding="utf-8") as fil:
            skriver = csv.DictWriter(fil, fieldnames=list(nye_rader[0].keys()))
            if ny_fil:
                skriver.writeheader()
            skriver.writerows(nye_rader)

    rapport = lag_rapport(cfg, funn, kombinasjoner, tilstand["sjekket"], nå, logg)
    Path(args.rapport).write_text(rapport, encoding="utf-8")
    print("\n".join(logg))
    print(f"Rapport skrevet til {args.rapport}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Leter etter billige reiser Oslo–Thailand.")
    p.add_argument("--demo", action="store_true", help="bruk testdata i stedet for SerpApi")
    p.add_argument("--dry-run", action="store_true", help="vis neste søk uten å søke")
    p.add_argument("--antall", type=int, help="antall søk denne kjøringen (overstyrer config)")
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--rapport", default=str(ROOT / "RAPPORT.md"))
    return kjor(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
