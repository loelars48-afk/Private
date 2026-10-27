"""Datakilder for Thailand-agenten. Hver kilde gir reisetilbud i samme format."""

from datetime import date


class KvoteFeil(Exception):
    """API-et nekter flere søk (kvote brukt opp eller ugyldig nøkkel)."""


def nokkel(ut: date, hjem: date) -> str:
    return f"{ut.isoformat()}|{hjem.isoformat()}"


def flyplass(cfg: dict, kode: str) -> dict:
    for f in cfg["avreise_flyplasser"]:
        if f["kode"] == kode:
            return f
    return {"kode": kode, "navn": kode, "tillegg_per_person_nok": 0, "sokeord": []}


def flyplass_fra_tekst(cfg: dict, tekst: str) -> str | None:
    """Avreiseflyplass ut fra fritekst («Oslo Torp», «Kastrup» ...). None = ikke aktuell.

    Mer spesifikke navn sjekkes først, slik at «Oslo Torp» blir TRF og ikke OSL.
    """
    lav = tekst.lower()
    treff = [(len(ord_), f["kode"]) for f in cfg["avreise_flyplasser"] for ord_ in f["sokeord"] if ord_ in lav]
    return max(treff)[1] if treff else None


def prisfelt(cfg: dict, per_person: float, bagasje: str, fra: str) -> dict:
    """Felles prisfelt for alle kilder.

    totalpris_per_person = billett + anslått reise til avreiseflyplassen.
    Det er denne prisen som sammenlignes med grensene i config.
    """
    tillegg = flyplass(cfg, fra)["tillegg_per_person_nok"]
    bag = 0 if bagasje == "inkludert" else 2 * cfg["estimert_innsjekket_bagasje_per_person_per_vei_nok"]
    return {
        "fra": fra,
        "pris_per_person": round(per_person),
        "tillegg_per_person": tillegg,
        "totalpris_per_person": round(per_person + tillegg),
        "bagasje": bagasje,
        "totalpris_med_bag": round(per_person + tillegg + bag),
    }
