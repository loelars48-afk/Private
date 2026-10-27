"""Datakilder for Thailand-agenten. Hver kilde gir reisetilbud i samme format."""

from datetime import date


class KvoteFeil(Exception):
    """API-et nekter flere søk (kvote brukt opp eller ugyldig nøkkel)."""


def nokkel(ut: date, hjem: date) -> str:
    return f"{ut.isoformat()}|{hjem.isoformat()}"


def med_bagasje(cfg: dict, per_person: float, bagasje: str) -> int:
    """Pris per person inkludert estimert innsjekket bagasje tur/retur."""
    tillegg = 0 if bagasje == "inkludert" else 2 * cfg["estimert_innsjekket_bagasje_per_person_per_vei_nok"]
    return round(per_person + tillegg)
