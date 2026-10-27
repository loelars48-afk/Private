#!/usr/bin/env python3
"""Thailand-agenten: leter etter billige reiser Oslo -> Thailand i flere kilder.

Kilder (hver brukes bare hvis nøkkelen er satt):
  1. Aviasales/Travelpayouts (TRAVELPAYOUTS_TOKEN): gratis, bufrede lavpriser for hele perioden.
  2. Google Flights via SerpApi (SERPAPI_KEY): ferske priser. Bekrefter først datoene
     Aviasales har funnet billige, og roterer deretter gjennom resten.
  3. Restplass- og chartersider (ANTHROPIC_API_KEY): sidene åpnes i nettleser og
     Claude plukker ut Thailand-tilbudene.

Resultatene lagres i data/ og oppsummeres i RAPPORT.md.

Bruk:
    python thailand_agent.py              # ekte søk med nøklene som er satt
    python thailand_agent.py --demo       # testdata, ingen nøkler eller nett
    python thailand_agent.py --dry-run    # vis neste Google Flights-søk
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kilder import KvoteFeil, google_flights, nokkel, restplass, travelpayouts

ROOT = Path(__file__).resolve().parent

FLYPLASSER = {
    "BKK": "Bangkok Suvarnabhumi",
    "DMK": "Bangkok Don Mueang",
    "HKT": "Phuket",
    "KBV": "Krabi",
    "USM": "Koh Samui",
}

HISTORIKK_FELT = ["kilde", "type", "utreise", "hjemreise", "netter", "til", "flyselskap", "stopp", "via",
                  "reisetid_t", "pris_per_person", "bagasje", "pris_per_person_med_bag", "lenke",
                  "sjekket", "notat"]


# ---------------------------------------------------------------- lagring

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


# ---------------------------------------------------------------- kildene

def kjor_aviasales(cfg, token, demo, funn, gyldige, tidspunkt, logg) -> str:
    hent = travelpayouts.hent_demo if demo else travelpayouts.hent
    rader, feil = [], []
    for sporring in travelpayouts.sporringer(cfg):
        try:
            rader += hent(cfg, sporring, token)
        except KvoteFeil as e:
            feil.append(f"nøkkel/kvote ({e})")
            break
        except Exception as e:
            feil.append(f"{sporring['destination']} {sporring['departure_at']}: {e}")
    if feil and not rader:
        logg.append("Aviasales: " + "; ".join(feil))
        return "feil: " + feil[0]
    funn["aviasales"] = travelpayouts.tolk(cfg, rader, gyldige, tidspunkt)
    billigst = min((f["pris_per_person"] for f in funn["aviasales"].values()), default=None)
    logg.append(f"Aviasales: {len(funn['aviasales'])} dato-kombinasjoner med pris"
                + (f", billigst {kr(billigst)} p.p." if billigst else "")
                + (f" ({len(feil)} delspørringer feilet)" if feil else ""))
    return "ok" + (f", {len(feil)} feil" if feil else "")


def kjor_restplass(cfg, demo, funn, tilstand, idag, tidspunkt, logg) -> str:
    les = restplass.les_med_claude_demo if demo else restplass.les_med_claude
    sidestatus = tilstand.setdefault("restplass", {})
    ok = 0
    for side in cfg["restplass_sider"]:
        navn, url = side["navn"], side["url"]
        forrige = sidestatus.get(navn, {})
        try:
            side_data = restplass.demo_side(url) if demo else restplass.hent_side(url)
            tekst = side_data["tekst"]
        except Exception as e:
            sidestatus[navn] = {**forrige, "status": f"kunne ikke åpne siden ({str(e)[:120]})", "tidspunkt": tidspunkt}
            logg.append(f"{navn}: kunne ikke åpne siden ({str(e)[:120]})")
            continue
        if not restplass.nevner_thailand(tekst):
            funn["restplass"][navn] = []
            sidestatus[navn] = {"hash": "", "status": "ingen Thailand-tilbud på siden", "tidspunkt": tidspunkt}
            logg.append(f"{navn}: siden nevner ikke Thailand nå.")
            ok += 1
            continue
        h = restplass.tekst_hash(tekst)
        if h == forrige.get("hash") and not demo:
            sidestatus[navn] = {**forrige, "status": "uendret siden forrige kjøring", "tidspunkt": tidspunkt}
            logg.append(f"{navn}: uendret, Claude ikke brukt.")
            ok += 1
            continue
        try:
            lenker = restplass.relevante_lenker(side_data["lenker"])
            rader = les(cfg, navn, url, tekst, lenker, idag)
        except Exception as e:
            sidestatus[navn] = {**forrige, "status": f"Claude-feil ({str(e)[:120]})", "tidspunkt": tidspunkt}
            logg.append(f"{navn}: Claude-feil ({str(e)[:120]})")
            continue
        tilbud = restplass.tolk(cfg, navn, url, rader, tidspunkt, lenker)
        funn["restplass"][navn] = tilbud
        sidestatus[navn] = {"hash": h, "status": f"{len(rader)} Thailand-tilbud, {len(tilbud)} passer kriteriene",
                            "tidspunkt": tidspunkt}
        logg.append(f"{navn}: {len(rader)} Thailand-tilbud lest, {len(tilbud)} passer kriteriene.")
        ok += 1
    return f"{ok} av {len(cfg['restplass_sider'])} sider lest"


def kjor_google(cfg, api_key, demo, antall, funn, tilstand, kombinasjoner, tidspunkt, nå, logg) -> str:
    sok = google_flights.sok_demo if demo else google_flights.sok
    sjekket = tilstand["sjekket"]
    grense = cfg["maks_pris_per_person_nok"] + cfg["nesten_treff_margin_nok"]
    for_ett_dogn_siden = (nå - timedelta(days=1)).isoformat()
    prioriterte = {n for n, f in funn["aviasales"].items()
                   if f["pris_per_person"] <= grense and sjekket.get(n, "") < for_ett_dogn_siden}
    neste = google_flights.velg_neste_sok(kombinasjoner, sjekket, antall, prioriterte)
    utfort = 0
    for ut, hjem in neste:
        n = nokkel(ut, hjem)
        grunn = " (bekrefter Aviasales-pris)" if n in prioriterte else ""
        try:
            data = sok(cfg, ut, hjem, api_key)
        except KvoteFeil as e:
            logg.append(f"Google Flights stoppet: kvote/nøkkel-feil ({e}).")
            return "kvote brukt opp eller ugyldig nøkkel"
        except Exception as e:
            logg.append(f"Google Flights {ut} → {hjem}: feil ({e}). Prøves igjen senere.")
            continue
        utfort += 1
        sjekket[n] = tidspunkt
        alternativer = google_flights.tolk_resultat(cfg, data, ut, hjem, tidspunkt)
        if not alternativer:
            funn["google_flights"].pop(n, None)
            logg.append(f"Google Flights {ut} → {hjem}: ingen flyvninger{grunn}.")
            continue
        billigst = min(alternativer, key=lambda f: f["pris_per_person"])
        funn["google_flights"][n] = billigst
        merke = " ✅ TREFF" if billigst["pris_per_person"] <= cfg["maks_pris_per_person_nok"] else ""
        logg.append(f"Google Flights {ut} → {hjem}: {kr(billigst['pris_per_person'])} p.p. "
                    f"til {billigst['til']} ({billigst['flyselskap']}){grunn}{merke}")
    return f"ok, {utfort} søk"


# ---------------------------------------------------------------- rapport

def kr(belop) -> str:
    return f"{round(belop):,}".replace(",", " ") + " kr"


def bag_tekst(f: dict) -> str:
    if f["bagasje"] == "inkludert":
        return "inkl."
    if f["bagasje"] == "ikke inkludert":
        return f"{kr(f['pris_per_person_med_bag'])} (est.)"
    return f"{kr(f['pris_per_person_med_bag'])} (est., ukjent)"


LENKETEKST = {
    "billett": ("Billett", "Åpne billetten"),
    "tilbud": ("Tilbud", "Åpne tilbudet"),
    "søk med datoer": ("Søk", "Åpne søket med disse datoene"),
    "oversiktsside": ("Side", "Åpne siden (finn tilbudet i listen)"),
}


def lenke(f: dict, lang: bool = False) -> str:
    kort, langt = LENKETEKST.get(f.get("lenke_type", "oversiktsside"), LENKETEKST["oversiktsside"])
    return f"[{langt if lang else kort}]({f['lenke']})"


def dato_tekst(f: dict) -> str:
    if not (f["utreise"] and f["hjemreise"]):
        return "dato ukjent"
    ut, hjem = date.fromisoformat(f["utreise"]), date.fromisoformat(f["hjemreise"])
    return f"{ut:%d.%m}–{hjem:%d.%m} ({f['netter']} netter)"


def lenkeliste(rader: list[dict]) -> str:
    """Én linje per reise med direkte lenke – hovedleveransen i rapporten."""
    if not rader:
        return "_Ingen._\n"
    linjer = []
    for f in rader:
        detaljer = [f["til"], dato_tekst(f), f["type"]]
        if f["flyselskap"]:
            detaljer.append(f["flyselskap"])
        if f["stopp"] not in ("", None):
            detaljer.append("direkte" if f["stopp"] == 0 else f"{f['stopp']} stopp")
        linjer.append(f"- **{kr(f['pris_per_person'])} p.p.** · " + " · ".join(str(d) for d in detaljer)
                      + f" · {f['kilde']} → **{lenke(f, lang=True)}**")
    return "\n".join(linjer) + "\n"


def tabell(rader: list[dict]) -> str:
    if not rader:
        return "_Ingen._\n"
    linjer = [
        "| Lenke | Pris p.p. | Med innsj. bag p.p. | Kilde | Type | Utreise | Hjem | Netter | Til | Selskap | Stopp | Sjekket | Merknad |",
        "|---|---:|---:|---|---|---|---|---:|---|---|---|---|---|",
    ]
    for f in rader:
        stopp = f"{f['stopp']}" + (f" ({f['via']})" if f.get("via") else "")
        linjer.append(
            f"| {lenke(f)} | **{kr(f['pris_per_person'])}** | {bag_tekst(f)} | {f['kilde']} | {f['type']} "
            f"| {f['utreise'] or '?'} | {f['hjemreise'] or '?'} | {f['netter']} | {f['til']} "
            f"| {f['flyselskap']} | {stopp} | {f['sjekket'][:16].replace('T', ' ')} "
            f"| {f.get('notat', '')} |"
        )
    return "\n".join(linjer) + "\n"


def slaa_sammen_restplass(funn: dict) -> list[dict]:
    """Samlesider (restplass.no, Travelmarket ...) viser ofte de samme tilbudene som
    turoperatørene selv. Like tilbud slås sammen til én rad med alle kildene."""
    unike: dict[tuple, dict] = {}
    for liste in funn["restplass"].values():
        for f in liste:
            k = (f["til"].lower(), f["utreise"], f["hjemreise"], f["pris_per_person"], f["type"])
            if k in unike:
                if f["kilde"] not in unike[k]["kilde"]:
                    unike[k]["kilde"] += f", {f['kilde']}"
                # Foretrekk en lenke til selve tilbudet framfor en oversiktsside
                if unike[k].get("lenke_type") != "tilbud" and f.get("lenke_type") == "tilbud":
                    unike[k]["lenke"], unike[k]["lenke_type"] = f["lenke"], "tilbud"
            else:
                unike[k] = dict(f)
    return list(unike.values())


def alle_tilbud(funn: dict, idag: date) -> list[dict]:
    rader = list(funn["google_flights"].values()) + list(funn["aviasales"].values())
    rader += slaa_sammen_restplass(funn)
    # Fjern tilbud med utreise som har passert
    return [f for f in rader if not f["utreise"] or date.fromisoformat(f["utreise"]) > idag]


def lag_rapport(cfg, funn, kombinasjoner, tilstand, kildestatus, nå, logg) -> str:
    idag = nå.date()
    alle = sorted(alle_tilbud(funn, idag), key=lambda f: f["pris_per_person"])
    datert = [f for f in alle if f["utreise"] and f["hjemreise"]]
    udatert = [f for f in alle if not (f["utreise"] and f["hjemreise"])]
    restplass_alle = [f for f in alle if f["kilde"] not in ("Google Flights", "Aviasales")]
    maks = cfg["maks_pris_per_person_nok"]
    margin = cfg["nesten_treff_margin_nok"]
    treff = [f for f in datert if f["pris_per_person"] <= maks]
    nesten = [f for f in datert if maks < f["pris_per_person"] <= maks + margin]
    gyldige = {nokkel(*k) for k in kombinasjoner}
    sjekket = tilstand["sjekket"]
    dekket = sum(1 for n in gyldige if n in sjekket)

    status = (f"✅ **{len(treff)} treff** innenfor {kr(maks)} per person." if treff
              else f"Ingen treff under {kr(maks)} per person ennå.")
    if udatert and any(f["pris_per_person"] <= maks for f in udatert):
        status += " Det finnes også restplass-tilbud under makspris uten oppgitt dato – se egen seksjon."

    kildelinjer = ["| Kilde | Status |", "|---|---|"]
    kildelinjer += [f"| {k} | {s} |" for k, s in kildestatus.items()]
    sidelinjer = ["| Side | Status | Sist sjekket |", "|---|---|---|"]
    for side in cfg["restplass_sider"]:
        s = tilstand.get("restplass", {}).get(side["navn"], {})
        sidelinjer.append(f"| [{side['navn']}]({side['url']}) | {s.get('status', 'ikke sjekket')} "
                          f"| {s.get('tidspunkt', '')[:16].replace('T', ' ')} |")

    deler = [
        "# Thailand-agenten – rapport",
        "",
        f"Sist oppdatert: {nå.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        status,
        "",
        f"## Treff (≤ {kr(maks)} per person)",
        "",
        lenkeliste(treff),
        f"## Nesten-treff ({kr(maks + 1)}–{kr(maks + margin)} per person)",
        "",
        lenkeliste(nesten),
        "## Restplass og pakkereiser – direkte lenker",
        "",
        lenkeliste(restplass_alle[:20]),
        "## Detaljer",
        "",
        "### Treff og nesten-treff",
        "",
        tabell(treff + nesten),
        "### Restplass og charter",
        "",
        "Alle Thailand-tilbud fra restplass-sidene som passer reisevinduet, også uten oppgitt dato.",
        "Pakkereiser inkluderer hotell, så prisen er ikke direkte sammenlignbar med kun fly.",
        "",
        tabell(restplass_alle[:20]),
        "### 10 billigste totalt (med dato)",
        "",
        tabell(datert[:10]),
        "## Kilder",
        "",
        *kildelinjer,
        "",
        f"Google Flights-dekning: {dekket} av {len(gyldige)} dato-kombinasjoner sjekket.",
        "",
        *sidelinjer,
        "",
        "## Slik leser du rapporten",
        "",
        "- **Lenkene:** «Billett» åpner den konkrete billetten hos Aviasales (for 2 voksne). «Tilbud» åpner "
        "det konkrete restplass-tilbudet eller pakken hos turoperatøren/samlesiden. «Søk» åpner Google "
        "Flights med rute og datoer ferdig utfylt – den billigste flyvningen står øverst. «Side» betyr at "
        "tilbudet ikke hadde egen lenke, og at du må finne det i listen på siden.",
        "- **Pris p.p.** er pris per person slik kilden oppgir den.",
        "- **Google Flights** gir ferske priser. **Aviasales** gir bufrede priser fra de siste døgnene, "
        "som kan være borte. Agenten bekrefter billige Aviasales-datoer i Google Flights automatisk.",
        "- **Restplass-tilbud** er lest av Claude fra sidene. Merknaden viser om prisen er antatt per "
        "person eller mangler dato. Sjekk alltid tilbudet på siden før du bestiller.",
        f"- **Med innsj. bag p.p.** legger til et *estimat* på "
        f"{kr(cfg['estimert_innsjekket_bagasje_per_person_per_vei_nok'])} per person per vei når bagasje "
        "ikke er inkludert eller ikke er oppgitt.",
        "- Prisene er øyeblikksbilder. Lavpriser forsvinner raskt.",
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
    tilstand = last_json(tilstand_sti, {})
    tilstand.setdefault("sjekket", {})
    funn = last_json(funn_sti, {})
    if "google_flights" not in funn:  # eldre format: bare Google Flights, nøklet på datoer
        funn = {"google_flights": funn}
    funn.setdefault("aviasales", {})
    funn.setdefault("restplass", {})

    kombinasjoner = dato_kombinasjoner(cfg, nå.date())
    gyldige = {nokkel(*k) for k in kombinasjoner}
    antall = args.antall or cfg["sok_per_kjoring"]

    if args.dry_run:
        neste = google_flights.velg_neste_sok(kombinasjoner, tilstand["sjekket"], antall)
        print(f"{len(kombinasjoner)} kombinasjoner totalt. Neste {len(neste)} Google Flights-søk:")
        for ut, hjem in neste:
            print(f"  {ut} -> {hjem} ({(hjem - ut).days} netter)")
        return 0

    logg, kildestatus = [], {}
    if not kombinasjoner:
        logg.append("Reisevinduet er passert – ingen kombinasjoner igjen å søke på.")

    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
    if args.demo or tp_token:
        kildestatus["Aviasales (Travelpayouts)"] = kjor_aviasales(
            cfg, tp_token, args.demo, funn, gyldige, tidspunkt, logg)
    else:
        kildestatus["Aviasales (Travelpayouts)"] = "av – mangler TRAVELPAYOUTS_TOKEN"

    if args.demo or os.environ.get("ANTHROPIC_API_KEY"):
        kildestatus["Restplass-sider (Claude)"] = kjor_restplass(
            cfg, args.demo, funn, tilstand, nå.date(), tidspunkt, logg)
    else:
        kildestatus["Restplass-sider (Claude)"] = "av – mangler ANTHROPIC_API_KEY"

    serp_key = os.environ.get("SERPAPI_KEY", "")
    if (args.demo or serp_key) and kombinasjoner:
        kildestatus["Google Flights (SerpApi)"] = kjor_google(
            cfg, serp_key, args.demo, antall, funn, tilstand, kombinasjoner, tidspunkt, nå, logg)
    elif not serp_key:
        kildestatus["Google Flights (SerpApi)"] = "av – mangler SERPAPI_KEY"

    # Rydd bort datoer og sider som ikke lenger er aktuelle
    sider = {s["navn"] for s in cfg["restplass_sider"]}
    funn["restplass"] = {n: l for n, l in funn["restplass"].items() if n in sider}
    funn["google_flights"] = {n: f for n, f in funn["google_flights"].items() if n in gyldige}
    funn["aviasales"] = {n: f for n, f in funn["aviasales"].items() if n in gyldige}
    tilstand["sjekket"] = {n: t for n, t in tilstand["sjekket"].items() if n in gyldige}

    lagre_json(tilstand_sti, tilstand)
    lagre_json(funn_sti, funn)
    nye = [f for f in alle_tilbud(funn, nå.date()) if f["sjekket"] == tidspunkt]
    if nye:
        ny_fil = not historikk_sti.exists()
        with historikk_sti.open("a", newline="", encoding="utf-8") as fil:
            skriver = csv.DictWriter(fil, fieldnames=HISTORIKK_FELT, extrasaction="ignore")
            if ny_fil:
                skriver.writeheader()
            skriver.writerows(nye)

    rapport = lag_rapport(cfg, funn, kombinasjoner, tilstand, kildestatus, nå, logg)
    Path(args.rapport).write_text(rapport, encoding="utf-8")
    print("\n".join(logg))
    print(f"Rapport skrevet til {args.rapport}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Leter etter billige reiser Oslo–Thailand.")
    p.add_argument("--demo", action="store_true", help="bruk testdata i stedet for ekte kilder")
    p.add_argument("--dry-run", action="store_true", help="vis neste Google Flights-søk uten å søke")
    p.add_argument("--antall", type=int, help="antall Google Flights-søk (overstyrer config)")
    p.add_argument("--data-dir", default=str(ROOT / "data"))
    p.add_argument("--rapport", default=str(ROOT / "RAPPORT.md"))
    return kjor(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
