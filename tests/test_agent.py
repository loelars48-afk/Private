import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import thailand_agent as a  # noqa: E402
from kilder import google_flights, nokkel, restplass, travelpayouts  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
IDAG = date(2026, 10, 27)


class DatoTest(unittest.TestCase):
    def test_alle_kombinasjoner_innenfor_vinduet(self):
        komb = a.dato_kombinasjoner(CFG, IDAG)
        self.assertEqual(len(komb), 162)
        for ut, hjem in komb:
            self.assertGreaterEqual(ut, date(2026, 11, 24))
            self.assertLessEqual(hjem, date(2026, 12, 22))
            self.assertTrue(10 <= (hjem - ut).days <= 21)
        self.assertNotIn((date(2026, 12, 13), date(2026, 12, 23)), komb)

    def test_passerte_datoer_faller_bort(self):
        komb = a.dato_kombinasjoner(CFG, date(2026, 12, 1))
        self.assertTrue(all(ut >= date(2026, 12, 2) for ut, _ in komb))
        self.assertEqual(a.dato_kombinasjoner(CFG, date(2026, 12, 20)), [])


class GoogleFlightsTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(google_flights.FIXTURE.read_text(encoding="utf-8"))
        self.funn = google_flights.tolk_resultat(CFG, self.data, date(2026, 11, 24), date(2026, 12, 8), "t")

    def test_pris_per_person(self):
        self.assertEqual(len(self.funn), 3)
        pegasus = next(f for f in self.funn if f["flyselskap"] == "Pegasus")
        self.assertEqual(pegasus["pris_per_person"], 4600)
        self.assertEqual((pegasus["til"], pegasus["stopp"]), ("HKT", 1))

    def test_bagasje(self):
        f = {x["flyselskap"]: x for x in self.funn}
        self.assertEqual((f["Qatar Airways"]["bagasje"], f["Qatar Airways"]["pris_per_person_med_bag"]),
                         ("inkludert", 5900))
        self.assertEqual((f["Pegasus"]["bagasje"], f["Pegasus"]["pris_per_person_med_bag"]),
                         ("ikke inkludert", 5800))
        self.assertEqual(f["Finnair"]["bagasje"], "ukjent")

    def test_lenke(self):
        self.assertEqual(self.funn[0]["lenke"], "https://www.google.com/travel/flights")
        uten_url = dict(self.data, search_metadata={})
        f = google_flights.tolk_resultat(CFG, uten_url, date(2026, 11, 24), date(2026, 12, 8), "t")[0]
        self.assertIn("OSL+to+BKK+on+2026-11-24+through+2026-12-08+for+2+adults", f["lenke"])
        self.assertEqual(f["lenke_type"], "søk med datoer")

    def test_rotasjon(self):
        komb = a.dato_kombinasjoner(CFG, IDAG)
        sjekket = {nokkel(*k): "2026-10-27T00:00:00+00:00" for k in komb[1:]}
        self.assertEqual(google_flights.velg_neste_sok(komb, sjekket, 1), [komb[0]])
        self.assertGreater(len({ut for ut, _ in google_flights.velg_neste_sok(komb, {}, 4)}), 1)
        # Datoer Aviasales har funnet billige går foran usjekkede
        prior = nokkel(*komb[50])
        self.assertEqual(google_flights.velg_neste_sok(komb, {}, 1, {prior}), [komb[50]])


class AviasalesTest(unittest.TestCase):
    def test_sporringer_dekker_maanedene(self):
        sp = travelpayouts.sporringer(CFG)
        self.assertEqual({(s["departure_at"], s["return_at"]) for s in sp},
                         {("2026-11", "2026-12"), ("2026-12", "2026-12")})
        self.assertEqual(len(sp), 2 * len(CFG["travelpayouts_destinasjoner"]))

    def test_tolk_filtrerer(self):
        rader = json.loads(travelpayouts.FIXTURE.read_text(encoding="utf-8"))["data"]
        gyldige = {nokkel(*k) for k in a.dato_kombinasjoner(CFG, IDAG)}
        funn = travelpayouts.tolk(CFG, rader, gyldige, "t")
        # for tidlig utreise og for mange stopp faller bort
        self.assertEqual(set(funn), {"2026-11-26|2026-12-09", "2026-12-03|2026-12-18"})
        bkk = funn["2026-11-26|2026-12-09"]
        self.assertEqual(bkk["pris_per_person"], 4380)
        # Lenken åpner samme billett, men for 2 voksne
        self.assertEqual(bkk["lenke"], "https://www.aviasales.com/search/OSL2611BKK09122?t=demo")
        self.assertEqual(bkk["lenke_type"], "billett")

    def test_lenke_for_voksne(self):
        self.assertEqual(travelpayouts.lenke_for_voksne("/search/OSL2611BKK1?t=a", 2),
                         "https://www.aviasales.com/search/OSL2611BKK2?t=a")


class FalskKlient:
    """Erstatter anthropic.Anthropic i testene og husker forespørselen."""

    def __init__(self, svar, stop_reason="end_turn"):
        self.kall = None
        tekst = SimpleNamespace(type="text", text=json.dumps(svar))
        melding = SimpleNamespace(stop_reason=stop_reason, content=[tekst])

        def create(**kw):
            self.kall = kw
            return melding
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


class RestplassTest(unittest.TestCase):
    def setUp(self):
        self.svar = json.loads(restplass.FIXTURE.read_text(encoding="utf-8"))

    def test_claude_kall(self):
        klient = FalskKlient(self.svar)
        lenker = [{"href": "https://x/phuket", "tekst": "Phuket 4 995,-"}]
        rader = restplass.les_med_claude(CFG, "Ving", "https://x", "Phuket 4 995,-", lenker, IDAG, klient)
        self.assertEqual(len(rader), 5)
        kw = klient.kall
        self.assertEqual(kw["model"], CFG["claude_modell"])
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertIn("<sidetekst>\nPhuket 4 995,-\n</sidetekst>", kw["messages"][0]["content"])
        self.assertIn("1: Phuket 4 995,- → https://x/phuket", kw["messages"][0]["content"])
        self.assertIn("lenke_nr", kw["output_config"]["format"]["schema"]["properties"]["tilbud"]["items"]["required"])

    def test_avslag_gir_feil(self):
        with self.assertRaises(RuntimeError):
            restplass.les_med_claude(CFG, "Ving", "https://x", "t", [], IDAG, FalskKlient({}, "refusal"))

    def test_tolk_filtrerer_og_regner_per_person(self):
        tilbud = restplass.tolk(CFG, "Ving", "https://x", self.svar["tilbud"], "t")
        self.assertEqual([t["til"] for t in tilbud], ["Phuket", "Krabi", "Khao Lak"])
        phuket, krabi, khao = tilbud
        self.assertEqual((phuket["hjemreise"], phuket["pris_per_person"]), ("2026-12-12", 4995))
        self.assertEqual(krabi["pris_per_person"], 11990)  # 23 980 totalt for 2
        self.assertIn("dato ukjent", khao["notat"])
        self.assertIn("antatt pris per person", khao["notat"])

    def test_lenker_kobles_til_tilbud(self):
        lenker = restplass.demo_side("https://ving.no/r")["lenker"]
        tilbud = restplass.tolk(CFG, "Ving", "https://ving.no/r", self.svar["tilbud"], "t", lenker)
        phuket, krabi, khao = tilbud
        self.assertEqual((phuket["lenke"], phuket["lenke_type"]), ("https://ving.no/r/phuket/flystol-2811", "tilbud"))
        self.assertEqual(krabi["lenke"], "https://ving.no/r/krabi/ao-nang-hotel")
        # uten lenke_nr: oversiktssiden, tydelig merket
        self.assertEqual((khao["lenke"], khao["lenke_type"]), ("https://ving.no/r", "oversiktsside"))
        self.assertIn("fant ingen egen lenke", khao["notat"])

    def test_ugyldig_lenke_nr_gir_oversiktsside(self):
        rad = dict(self.svar["tilbud"][0], lenke_nr=99)
        (t,) = restplass.tolk(CFG, "Ving", "https://ving.no/r", [rad], "t", [])
        self.assertEqual(t["lenke"], "https://ving.no/r")

    def test_relevante_lenker(self):
        lenker = restplass.demo_side("https://x")["lenker"] + [
            {"href": "https://x/thailand/khao-lak", "tekst": "Les mer"},
            {"href": "https://x/kontakt", "tekst": "Kontakt oss"}]
        hrefs = [l["href"] for l in restplass.relevante_lenker(lenker)]
        self.assertEqual(hrefs, ["https://x/phuket/flystol-2811", "https://x/krabi/ao-nang-hotel",
                                 "https://x/thailand/khao-lak"])

    def test_lenker_uten_nettleser(self):
        html = ('<html><body><a href="/phuket/hotel-x?d=2811">Phuket <b>Hotel X</b> 9 995,-</a>'
                '<a href="https://ving.no/kontakt">Kontakt</a><script>var x=1;</script></body></html>')
        from unittest import mock
        svar = mock.MagicMock()
        svar.__enter__.return_value.read.return_value = html.encode()
        with mock.patch("urllib.request.urlopen", return_value=svar):
            side = restplass._hent_uten_nettleser("https://www.ving.no/thailand/restplasser")
        self.assertEqual(side["lenker"][0], {"href": "https://www.ving.no/phuket/hotel-x?d=2811",
                                             "tekst": "Phuket Hotel X 9 995,-"})
        self.assertNotIn("var x", side["tekst"])

    def test_thailand_sjekk_og_hash(self):
        self.assertTrue(restplass.nevner_thailand("Restplass til PHUKET"))
        self.assertFalse(restplass.nevner_thailand("Gran Canaria og Kreta"))
        self.assertEqual(restplass.tekst_hash("a\n12\nb"), restplass.tekst_hash("a\n13\nb"))
        self.assertNotEqual(restplass.tekst_hash("Phuket 4995"), restplass.tekst_hash("Phuket 4495"))


class EndeTilEndeTest(unittest.TestCase):
    def test_demo_kjoring_lager_rapport(self):
        with tempfile.TemporaryDirectory() as tmp:
            rapport = Path(tmp) / "R.md"
            for _ in range(2):
                r = subprocess.run(
                    [sys.executable, str(ROOT / "thailand_agent.py"), "--demo",
                     "--data-dir", tmp, "--rapport", str(rapport)],
                    capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            tekst = rapport.read_text(encoding="utf-8")
            for seksjon in ("## Treff", "## Restplass og charter", "## Kilder", "Aviasales", "Khao Lak"):
                self.assertIn(seksjon, tekst)
            funn = json.loads((Path(tmp) / "funn.json").read_text())
            self.assertEqual(set(funn), {"google_flights", "aviasales", "restplass"})
            # Billige Aviasales-datoer bekreftes i Google Flights
            self.assertIn("2026-11-26|2026-12-09", funn["google_flights"])
            self.assertTrue((Path(tmp) / "prishistorikk.csv").exists())


if __name__ == "__main__":
    unittest.main()
