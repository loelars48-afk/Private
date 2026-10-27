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

HISTORIKK_FELT = ["kilde", "type", "fra", "utreise", "hjemreise", "netter", "til", "flyselskap", "stopp",
                  "via", "reisetid_t", "pris_per_person", "tillegg_per_person", "totalpris_per_person",
                  "bagasje", "totalpris_med_bag", "lenke", "sjekket", "notat"]


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
    """Alle (utreise, hjemreise) som oppfyller reisevinduet og antall netter.

    hjemreise er avreisedatoen fra Thailand (slik flysøk bruker den). Nattfly
    lander dagen etter, derfor er grensen seneste_avreise_hjem og ikke
    datoen dere må være hjemme.
    """
    forste = max(date.fromisoformat(cfg["tidligste_utreise"]), idag + timedelta(days=1))
    siste_hjem = date.fromisoformat(cfg["seneste_avreise_hjem"])
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


# ---------------------------------------------------------------- grenser

def grense(cfg: dict, f: dict) -> int:
    """Makspris per person: egen (høyere) grense for pakkereiser, som inkluderer hotell."""
    if f["type"].startswith("pakkereise"):
        return cfg["maks_pris_pakke_per_person_nok"]
    return cfg["maks_pris_per_person_nok"]


def er_fersk(cfg: dict, f: dict, nå: datetime) -> bool:
    return f["sjekket"] >= (nå - timedelta(hours=cfg["treff_gyldig_timer"])).isoformat()


def varselnokkel(f: dict) -> str:
    return "|".join(str(f[k]) for k in ("kilde", "type", "fra", "til", "utreise", "hjemreise"))


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
    billigst = min((f["totalpris_per_person"] for f in funn["aviasales"].values()), default=None)
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
            # Siden er sjekket nå og har samme innhold, så tilbudene er bekreftet på nytt.
            for f in funn["restplass"].get(navn, []):
                f["sjekket"] = tidspunkt
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
    tak = cfg["maks_pris_per_person_nok"] + cfg["nesten_treff_margin_nok"]
    for_ett_dogn_siden = (nå - timedelta(days=1)).isoformat()
    # Billige datoer sjekkes (på nytt) i Google Flights minst én gang i døgnet:
    # Aviasales-priser som må bekreftes, og tidligere Google-treff som kan være borte.
    billige = {n for kilde in ("aviasales", "google_flights") for n, f in funn[kilde].items()
               if f["totalpris_per_person"] <= tak}
    prioriterte = {n for n in billige if sjekket.get(n, "") < for_ett_dogn_siden}
    neste = google_flights.velg_neste_sok(kombinasjoner, sjekket, antall, prioriterte)
    utfort = 0
    for ut, hjem in neste:
        n = nokkel(ut, hjem)
        grunn = " (ny sjekk av billig dato)" if n in prioriterte else ""
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
        if data.get("_advarsel"):
            logg.append(f"⚠️ Google Flights: {data['_advarsel']}")
        alternativer = google_flights.tolk_resultat(cfg, data, ut, hjem, tidspunkt)
        if not alternativer:
            funn["google_flights"].pop(n, None)
            logg.append(f"Google Flights {ut} → {hjem}: ingen flyvninger{grunn}.")
            continue
        billigst = min(alternativer, key=lambda f: f["totalpris_per_person"])
        funn["google_flights"][n] = billigst
        merke = " ✅ TREFF" if billigst["totalpris_per_person"] <= grense(cfg, billigst) else ""
        logg.append(f"Google Flights {ut} → {hjem}: {kr(billigst['totalpris_per_person'])} p.p. "
                    f"{billigst['fra']} → {billigst['til']} ({billigst['flyselskap']}){grunn}{merke}")
        if billigst["notat"].startswith("mistenkelig"):
            logg.append(f"⚠️ {ut} → {hjem}: {billigst['notat']}")
    return f"ok, {utfort} søk"


# ---------------------------------------------------------------- rapport

def kr(belop) -> str:
    return f"{round(belop):,}".replace(",", " ") + " kr"


def bag_tekst(f: dict) -> str:
    if f["bagasje"] == "inkludert":
        return "inkl."
    if f["bagasje"] == "ikke inkludert":
        return f"{kr(f['totalpris_med_bag'])} (est.)"
    return f"{kr(f['totalpris_med_bag'])} (est., ukjent)"


def pris_tekst(f: dict) -> str:
    """Totalpris per person, med billett og reise til flyplassen spesifisert når den ikke er OSL."""
    if not f["tillegg_per_person"]:
        return f"**{kr(f['totalpris_per_person'])} p.p.**"
    return (f"**{kr(f['totalpris_per_person'])} p.p.** (billett {kr(f['pris_per_person'])} "
            f"+ ca. {kr(f['tillegg_per_person'])} reise til {f['fra']})")


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
        detaljer = [f"{f['fra']} → {f['til']}", dato_tekst(f), f["type"]]
        if f["flyselskap"]:
            detaljer.append(f["flyselskap"])
        if f["stopp"] not in ("", None):
            detaljer.append("direkte" if f["stopp"] == 0 else f"{f['stopp']} stopp")
        linje = f"- {pris_tekst(f)} · " + " · ".join(str(d) for d in detaljer) + f" · {f['kilde']}"
        linje += f" → **{lenke(f, lang=True)}**"
        if f.get("notat"):
            linje += f"  \n  _{f['notat']}_"
        linjer.append(linje)
    return "\n".join(linjer) + "\n"


def tabell(rader: list[dict]) -> str:
    if not rader:
        return "_Ingen._\n"
    linjer = [
        "| Lenke | Totalpris p.p. | Billett p.p. | Med innsj. bag p.p. | Kilde | Type | Fra | Til | Utreise | Hjemreise | Netter | Selskap | Stopp | Sjekket | Merknad |",
        "|---|---:|---:|---:|---|---|---|---|---|---|---:|---|---|---|---|",
    ]
    for f in rader:
        stopp = f"{f['stopp']}" + (f" ({f['via']})" if f.get("via") else "")
        linjer.append(
            f"| {lenke(f)} | **{kr(f['totalpris_per_person'])}** | {kr(f['pris_per_person'])} | {bag_tekst(f)} "
            f"| {f['kilde']} | {f['type']} | {f['fra']} | {f['til']} "
            f"| {f['utreise'] or '?'} | {f['hjemreise'] or '?'} | {f['netter']} "
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
            k = (f["til"].lower(), f["fra"], f["utreise"], f["hjemreise"], f["pris_per_person"], f["type"])
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


def klassifiser(cfg: dict, funn: dict, nå: datetime) -> dict[str, list[dict]]:
    """Del tilbudene i treff, nesten-treff og eldre funn.

    Bare tilbud med dato som er sjekket innenfor treff_gyldig_timer regnes som
    treff. Eldre funn under grensen vises for seg, fordi de kan være borte.
    """
    alle = sorted(alle_tilbud(funn, nå.date()), key=lambda f: f["totalpris_per_person"])
    datert = [f for f in alle if f["utreise"] and f["hjemreise"]]
    margin = cfg["nesten_treff_margin_nok"]
    return {
        "alle": alle,
        "datert": datert,
        "treff": [f for f in datert if er_fersk(cfg, f, nå) and f["totalpris_per_person"] <= grense(cfg, f)],
        "nesten": [f for f in datert if er_fersk(cfg, f, nå)
                   and grense(cfg, f) < f["totalpris_per_person"] <= grense(cfg, f) + margin],
        "eldre": [f for f in datert if not er_fersk(cfg, f, nå) and f["totalpris_per_person"] <= grense(cfg, f)],
        "udatert": [f for f in alle if not (f["utreise"] and f["hjemreise"])],
        "restplass": [f for f in alle if f["kilde"] not in ("Google Flights", "Aviasales")],
    }


def lag_varsel(cfg: dict, treff: list[dict], tilstand: dict) -> tuple[str, str] | None:
    """Tittel og tekst til GitHub-varsel for treff som er nye eller har blitt
    minst 5 % billigere siden forrige varsel. None når det ikke er noe nytt."""
    forrige = tilstand.get("varslet", {})
    nye = [f for f in treff
           if varselnokkel(f) not in forrige or f["totalpris_per_person"] < 0.95 * forrige[varselnokkel(f)]]
    # Husk prisen ved siste varsel, og bare for treff som fortsatt er aktuelle, så
    # et treff som forsvinner og dukker opp igjen, varsles på nytt.
    tilstand["varslet"] = {varselnokkel(f): forrige[varselnokkel(f)] for f in treff if varselnokkel(f) in forrige}
    for f in nye:
        tilstand["varslet"][varselnokkel(f)] = f["totalpris_per_person"]
    if not nye:
        return None
    billigst = min(f["totalpris_per_person"] for f in nye)
    tittel = f"Thailand: {len(nye)} {'nytt treff' if len(nye) == 1 else 'nye treff'} fra {kr(billigst)} per person"
    tekst = "\n".join([
        f"Agenten har funnet {len(nye)} {'reise' if len(nye) == 1 else 'reiser'} innenfor grensen "
        f"({kr(cfg['maks_pris_per_person_nok'])} p.p. for fly, {kr(cfg['maks_pris_pakke_per_person_nok'])} "
        "p.p. for pakkereiser). Lavpriser forsvinner raskt – sjekk lenkene snart.",
        "",
        lenkeliste(nye),
        "Hele oversikten står i RAPPORT.md i repoet.",
    ])
    return tittel, tekst


def lag_rapport(cfg, funn, kombinasjoner, tilstand, kildestatus, nå, logg) -> str:
    k = klassifiser(cfg, funn, nå)
    maks, pakke = cfg["maks_pris_per_person_nok"], cfg["maks_pris_pakke_per_person_nok"]
    margin = cfg["nesten_treff_margin_nok"]
    gyldige = {nokkel(*x) for x in kombinasjoner}
    sjekket = tilstand["sjekket"]
    dekket = sum(1 for n in gyldige if n in sjekket)
    timer = cfg["treff_gyldig_timer"]

    status = (f"✅ **{len(k['treff'])} treff**." if k["treff"] else "Ingen ferske treff akkurat nå.")
    if k["eldre"]:
        status += f" {len(k['eldre'])} eldre funn under grensen må sjekkes på nytt (se egen seksjon)."
    if any(f["totalpris_per_person"] <= grense(cfg, f) for f in k["udatert"]):
        status += " Det finnes også restplass-tilbud under grensen uten oppgitt dato."

    kildelinjer = ["| Kilde | Status |", "|---|---|"]
    kildelinjer += [f"| {navn} | {st} |" for navn, st in kildestatus.items()]
    sidelinjer = ["| Side | Status | Sist sjekket |", "|---|---|---|"]
    for side in cfg["restplass_sider"]:
        st = tilstand.get("restplass", {}).get(side["navn"], {})
        sidelinjer.append(f"| [{side['navn']}]({side['url']}) | {st.get('status', 'ikke sjekket')} "
                          f"| {st.get('tidspunkt', '')[:16].replace('T', ' ')} |")
    flyplasser = ", ".join(f"{f['navn']} ({f['kode']}, +{kr(f['tillegg_per_person_nok'])})"
                           for f in cfg["avreise_flyplasser"])

    deler = [
        "# Thailand-agenten – rapport",
        "",
        f"Sist oppdatert: {nå.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        status,
        "",
        f"Grenser per person: **fly {kr(maks)}**, **pakkereise (fly + hotell) {kr(pakke)}**. "
        "Prisen som sammenlignes er totalpris: billett + anslått reise til avreiseflyplassen.",
        "",
        f"## Treff (sjekket siste {timer} timer)",
        "",
        lenkeliste(k["treff"]),
        f"## Nesten-treff (inntil {kr(margin)} over grensen)",
        "",
        lenkeliste(k["nesten"]),
        "## Eldre funn under grensen – kan være borte",
        "",
        f"Sist sjekket for mer enn {timer} timer siden. Agenten sjekker billige datoer på nytt automatisk.",
        "",
        lenkeliste(k["eldre"]),
        "## Restplass og pakkereiser – direkte lenker",
        "",
        lenkeliste(k["restplass"][:20]),
        "## Detaljer",
        "",
        "### Treff, nesten-treff og eldre funn",
        "",
        tabell(k["treff"] + k["nesten"] + k["eldre"]),
        "### 10 billigste totalt (med dato)",
        "",
        tabell(k["datert"][:10]),
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
        "det konkrete restplass-tilbudet eller pakken. «Søk» åpner Google Flights med rute og datoer "
        "utfylt – den billigste flyvningen står øverst. «Side» betyr at tilbudet ikke hadde egen lenke.",
        f"- **Avreiseflyplasser:** {flyplasser}. Tillegget er et anslag for reisen tur/retur fra Oslo "
        "og kan endres i config.json.",
        f"- **Datoer:** «Hjemreise» er avreisedatoen fra Thailand. Siste tillatte er "
        f"{date.fromisoformat(cfg['seneste_avreise_hjem']):%d.%m}, slik at nattfly lander i Oslo senest dagen etter. "
        "«Netter» er antall døgn mellom utreise og hjemreise; med nattfly blir det én natt mindre i Thailand.",
        "- **Google Flights** gir ferske priser for 2 voksne. **Aviasales** gir bufrede priser for 1 person, "
        "som kan være borte eller ikke gjelde for 2 seter. Billige datoer bekreftes i Google Flights automatisk.",
        "- **Restplass-tilbud** er lest av Claude fra sidene. Merknaden viser om prisen er antatt per "
        "person, mangler dato eller avreisested. Sjekk alltid tilbudet før du bestiller.",
        f"- **Med innsj. bag** legger til et *estimat* på "
        f"{kr(cfg['estimert_innsjekket_bagasje_per_person_per_vei_nok'])} per person per vei når bagasje "
        "ikke er inkludert eller ikke er oppgitt.",
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
    # Funn lagret av eldre versjoner mangler totalpris; de forkastes og hentes på nytt.
    for kilde in ("google_flights", "aviasales"):
        funn[kilde] = {n: f for n, f in funn[kilde].items() if "totalpris_per_person" in f}
    funn["restplass"] = {n: [f for f in l if "totalpris_per_person" in f] for n, l in funn["restplass"].items()}

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
        # Ingen aktuelle datoer igjen: ikke bruk API-kvote eller Claude.
        logg.append("Reisevinduet er passert – ingen søk utført. Slå av workflowen under Actions.")
        Path(args.rapport).write_text(lag_rapport(cfg, funn, kombinasjoner, tilstand, kildestatus, nå, logg),
                                      encoding="utf-8")
        print(logg[-1])
        return 0

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
    if args.demo or serp_key:
        kildestatus["Google Flights (SerpApi)"] = kjor_google(
            cfg, serp_key, args.demo, antall, funn, tilstand, kombinasjoner, tidspunkt, nå, logg)
    else:
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

    varsel = lag_varsel(cfg, klassifiser(cfg, funn, nå)["treff"], tilstand)
    lagre_json(tilstand_sti, tilstand)
    varsel_sti = Path(args.varsel)
    if varsel:
        varsel_sti.write_text(varsel[0] + "\n" + varsel[1], encoding="utf-8")
        logg.append(f"Varsel laget: {varsel[0]}")
    elif varsel_sti.exists():
        varsel_sti.unlink()

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
    p.add_argument("--varsel", default=str(ROOT / "VARSEL.md"),
                   help="fil med varsel om nye treff (første linje er tittel); slettes når det ikke er noe nytt")
    return kjor(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
