import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
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
        self.assertEqual(len(komb), 150)
        for ut, hjem in komb:
            self.assertGreaterEqual(ut, date(2026, 11, 24))
            # Avreise fra Thailand senest 21.12, så nattfly lander senest 22.12
            self.assertLessEqual(hjem, date(2026, 12, 21))
            self.assertTrue(10 <= (hjem - ut).days <= 21)
        self.assertIn((date(2026, 12, 11), date(2026, 12, 21)), komb)
        self.assertNotIn((date(2026, 12, 12), date(2026, 12, 22)), komb)

    def test_passerte_datoer_faller_bort(self):
        komb = a.dato_kombinasjoner(CFG, date(2026, 12, 1))
        self.assertTrue(all(ut >= date(2026, 12, 2) for ut, _ in komb))
        self.assertEqual(a.dato_kombinasjoner(CFG, date(2026, 12, 11)), [])


class GoogleFlightsTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(google_flights.FIXTURE.read_text(encoding="utf-8"))
        self.funn = google_flights.tolk_resultat(CFG, self.data, date(2026, 11, 24), date(2026, 12, 8), "t")

    def test_pris_per_person(self):
        self.assertEqual(len(self.funn), 3)
        pegasus = next(f for f in self.funn if f["flyselskap"] == "Pegasus")
        self.assertEqual(pegasus["pris_per_person"], 4600)
        self.assertEqual((pegasus["fra"], pegasus["til"], pegasus["stopp"]), ("OSL", "HKT", 1))
        self.assertEqual(pegasus["totalpris_per_person"], 4600)  # ingen tillegg fra OSL

    def test_annen_avreiseflyplass_gir_tillegg(self):
        data = json.loads(json.dumps(self.data))
        data["other_flights"][0]["flights"][0]["departure_airport"]["id"] = "CPH"
        pegasus = next(f for f in google_flights.tolk_resultat(CFG, data, date(2026, 11, 24), date(2026, 12, 8), "t")
                       if f["flyselskap"] == "Pegasus")
        self.assertEqual((pegasus["fra"], pegasus["tillegg_per_person"], pegasus["totalpris_per_person"]),
                         ("CPH", 1000, 5600))

    def test_mistenkelig_lav_pris_merkes(self):
        data = json.loads(json.dumps(self.data))
        data["other_flights"][0]["price"] = 3000  # 1 500 kr p.p. tur/retur Thailand er ikke realistisk
        pegasus = next(f for f in google_flights.tolk_resultat(CFG, data, date(2026, 11, 24), date(2026, 12, 8), "t")
                       if f["flyselskap"] == "Pegasus")
        self.assertIn("mistenkelig lav pris", pegasus["notat"])

    def test_ingen_resultater_gir_ikke_nytt_sok(self):
        kall = []

        def falsk_hent(params):
            kall.append(params)
            return {"error": "Google Flights hasn't returned any results for this query."}
        with mock.patch.object(google_flights, "_hent", falsk_hent):
            google_flights.sok(CFG, date(2026, 11, 24), date(2026, 12, 8), "n")
        self.assertEqual(len(kall), 1)

    def test_reserve_naar_flere_flyplasser_avvises(self):
        kall = []

        def falsk_hent(params):
            kall.append(params)
            return {"error": "Invalid departure_id"} if len(kall) == 1 else {"best_flights": []}
        with mock.patch.object(google_flights, "_hent", falsk_hent):
            data = google_flights.sok(CFG, date(2026, 11, 24), date(2026, 12, 8), "n")
        self.assertEqual(kall[0]["departure_id"], "OSL,ARN,CPH,TRF")
        self.assertEqual((kall[1]["departure_id"], kall[1]["arrival_id"]), ("OSL", "BKK"))
        self.assertIn("avvist", data["_advarsel"])

    def test_bagasje(self):
        f = {x["flyselskap"]: x for x in self.funn}
        self.assertEqual((f["Qatar Airways"]["bagasje"], f["Qatar Airways"]["totalpris_med_bag"]),
                         ("inkludert", 5900))
        self.assertEqual((f["Pegasus"]["bagasje"], f["Pegasus"]["totalpris_med_bag"]),
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
        # Billige datoer går foran usjekkede ...
        prior = {nokkel(*komb[50]), nokkel(*komb[60]), nokkel(*komb[70])}
        self.assertEqual(google_flights.velg_neste_sok(komb, {}, 1, prior)[0] in (komb[50], komb[60], komb[70]), True)
        # ... men med 2+ søk går minst ett til rotasjonen, så resten av perioden også dekkes
        valgt = google_flights.velg_neste_sok(komb, {}, 2, prior)
        self.assertEqual(sum(nokkel(*k) in prior for k in valgt), 1)


class AviasalesTest(unittest.TestCase):
    def test_sporringer_dekker_maanedene(self):
        sp = travelpayouts.sporringer(CFG)
        self.assertEqual({(s["departure_at"], s["return_at"]) for s in sp},
                         {("2026-11", "2026-12"), ("2026-12", "2026-12")})
        self.assertEqual({s["origin"] for s in sp}, {"OSL", "ARN", "CPH", "TRF"})
        self.assertEqual(len(sp), 2 * 4 * len(CFG["travelpayouts_destinasjoner"]))

    def test_tolk_filtrerer(self):
        rader = json.loads(travelpayouts.FIXTURE.read_text(encoding="utf-8"))["data"]
        gyldige = {nokkel(*k) for k in a.dato_kombinasjoner(CFG, IDAG)}
        funn = travelpayouts.tolk(CFG, rader, gyldige, "t")
        # for tidlig utreise og for mange stopp faller bort
        self.assertEqual(set(funn), {"2026-11-26|2026-12-09", "2026-12-03|2026-12-18"})
        bkk = funn["2026-11-26|2026-12-09"]
        # ARN 3 200 + 1 000 i tillegg = 4 200 slår OSL 4 380 på samme datoer
        self.assertEqual((bkk["fra"], bkk["totalpris_per_person"]), ("ARN", 4200))

    def test_billett_lenke_for_2(self):
        rader = [r for r in json.loads(travelpayouts.FIXTURE.read_text(encoding="utf-8"))["data"] if r["origin"] == "OSL"]
        gyldige = {nokkel(*k) for k in a.dato_kombinasjoner(CFG, IDAG)}
        bkk = travelpayouts.tolk(CFG, rader, gyldige, "t")["2026-11-26|2026-12-09"]
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
        self.assertEqual(len(rader), len(self.svar["tilbud"]))
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
        # Ut: hjemreise 29.12 (for sent) og avreise fra Bergen (ikke aktuell flyplass)
        self.assertEqual([(t["til"], t["fra"]) for t in tilbud],
                         [("Phuket", "OSL"), ("Krabi", "OSL"), ("Khao Lak", "OSL"), ("Phuket", "ARN"), ("Phuket", "TRF")])
        phuket, krabi, khao, arn, torp = tilbud
        self.assertEqual(arn["totalpris_per_person"], 4500)  # 3 500 + 1 000 reise til Stockholm
        self.assertIn("avreisested ikke oppgitt", khao["notat"])
        # Charter hjem 22.12 tillates (lander ofte samme dag), men merkes
        self.assertIn("sjekk at flyet lander samme dag", torp["notat"])
        self.assertEqual((phuket["hjemreise"], phuket["pris_per_person"]), ("2026-12-12", 4995))
        self.assertEqual(krabi["pris_per_person"], 11990)  # 23 980 totalt for 2
        self.assertIn("dato ukjent", khao["notat"])
        self.assertIn("antatt pris per person", khao["notat"])

    def test_lenker_kobles_til_tilbud(self):
        lenker = restplass.demo_side("https://ving.no/r")["lenker"]
        tilbud = restplass.tolk(CFG, "Ving", "https://ving.no/r", self.svar["tilbud"], "t", lenker)
        phuket, krabi, khao = tilbud[:3]
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


NÅ = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def tilbud(**kw):
    f = {"kilde": "Google Flights", "type": "fly", "fra": "OSL", "til": "BKK", "utreise": "2026-11-26",
         "hjemreise": "2026-12-09", "netter": 13, "flyselskap": "QR", "stopp": 1, "via": "", "reisetid_t": 15,
         "pris_per_person": 4500, "tillegg_per_person": 0, "totalpris_per_person": 4500, "bagasje": "ukjent",
         "totalpris_med_bag": 5700, "lenke": "https://g", "lenke_type": "søk med datoer",
         "sjekket": NÅ.isoformat(), "notat": ""}
    f.update(kw)
    return f


class KlassifiseringTest(unittest.TestCase):
    def funn(self, *rader):
        return {"google_flights": {str(i): r for i, r in enumerate(rader)}, "aviasales": {}, "restplass": {}}

    def test_gamle_funn_er_ikke_treff(self):
        gammel = tilbud(sjekket=(NÅ - timedelta(hours=49)).isoformat(), utreise="2026-11-27")
        k = a.klassifiser(CFG, self.funn(tilbud(), gammel), NÅ)
        self.assertEqual([f["utreise"] for f in k["treff"]], ["2026-11-26"])
        self.assertEqual([f["utreise"] for f in k["eldre"]], ["2026-11-27"])

    def test_pakkereise_har_egen_grense(self):
        pakke = tilbud(type="pakkereise (fly + hotell)", totalpris_per_person=9500, kilde="Ving")
        fly = tilbud(totalpris_per_person=9500, utreise="2026-11-27")
        k = a.klassifiser(CFG, self.funn(pakke, fly), NÅ)
        self.assertEqual([f["type"] for f in k["treff"]], ["pakkereise (fly + hotell)"])

    def test_totalpris_med_tillegg_avgjor(self):
        # Billetten er under 5 000, men med reise til København blir den over
        cph = tilbud(fra="CPH", pris_per_person=4500, tillegg_per_person=1000, totalpris_per_person=5500)
        k = a.klassifiser(CFG, self.funn(cph), NÅ)
        self.assertEqual((k["treff"], len(k["nesten"])), ([], 1))


class SammendragTest(unittest.TestCase):
    def sammendrag(self, *rader, forrige=None):
        funn = {"google_flights": {str(i): r for i, r in enumerate(rader)}, "aviasales": {}, "restplass": {}}
        k = a.klassifiser(CFG, funn, NÅ)
        tekst = "\n".join(a.lag_sammendrag(CFG, k, a.beste_per_kategori(CFG, k, NÅ), forrige or {},
                                           {"Google Flights (SerpApi)": "ok, 2 søk"}))
        return tekst

    def test_treff_med_stedsnavn_og_pris_for_to(self):
        t = self.sammendrag(tilbud(til="HKT", totalpris_per_person=4500))
        self.assertIn("1 reise innenfor grensen", t)
        self.assertIn("Phuket", t)
        self.assertNotIn("HKT (", t)
        self.assertIn("**9 000 kr**", t)  # pris for 2
        self.assertIn("✅ innenfor", t)

    def test_ingen_treff_viser_avstand_til_grensen(self):
        t = self.sammendrag(tilbud(totalpris_per_person=7450))
        self.assertIn("Ingen reiser innenfor grensen", t)
        self.assertIn("2 450 kr over grensen", t)

    def test_ingen_data(self):
        self.assertIn("ikke funnet noen reiser ennå", self.sammendrag())

    def test_endring_siden_sist_og_anbefaling(self):
        pakke = tilbud(type="pakkereise (fly + hotell)", kilde="Ving", totalpris_per_person=8800, netter=14,
                       utreise="2026-12-06", hjemreise="2026-12-20")
        t = self.sammendrag(tilbud(totalpris_per_person=7400), pakke,
                            forrige={"Kun fly": 7700, "Fly + hotell": 8800})
        self.assertIn("↓ 300 kr billigere", t)
        self.assertIn("uendret", t)
        self.assertIn("1 400 kr mer per person", t)
        self.assertIn("ca. 100 kr per natt", t)


class VarselTest(unittest.TestCase):
    def test_varsler_bare_nye_og_billigere(self):
        tilstand = {}
        f = tilbud()
        tittel, tekst = a.lag_varsel(CFG, [f], tilstand)
        self.assertIn("1 nytt treff fra 4 500 kr", tittel)
        self.assertIn("[Åpne søket med disse datoene](https://g)", tekst)
        self.assertIsNone(a.lag_varsel(CFG, [f], tilstand))  # samme treff: ingen nytt varsel
        # Små prisfall summerer seg ikke stille: referansen er prisen ved forrige varsel
        self.assertIsNone(a.lag_varsel(CFG, [tilbud(totalpris_per_person=4400)], tilstand))
        self.assertIsNone(a.lag_varsel(CFG, [tilbud(totalpris_per_person=4300)], tilstand))
        self.assertIsNotNone(a.lag_varsel(CFG, [tilbud(totalpris_per_person=4200)], tilstand))

    def test_forsvunnet_treff_varsles_igjen(self):
        tilstand = {}
        a.lag_varsel(CFG, [tilbud()], tilstand)
        a.lag_varsel(CFG, [], tilstand)
        self.assertIsNotNone(a.lag_varsel(CFG, [tilbud()], tilstand))


class EndeTilEndeTest(unittest.TestCase):
    def test_gamle_lagrede_data_krasjer_ikke(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "funn.json").write_text(json.dumps(
                {"2026-11-26|2026-12-09": {"pris_per_person": 4000, "utreise": "2026-11-26"}}))
            r = subprocess.run([sys.executable, str(ROOT / "thailand_agent.py"), "--data-dir", tmp,
                                "--rapport", str(Path(tmp) / "R.md"), "--varsel", str(Path(tmp) / "V.md")],
                               capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_demo_kjoring_lager_rapport(self):
        with tempfile.TemporaryDirectory() as tmp:
            rapport, varsel = Path(tmp) / "R.md", Path(tmp) / "V.md"
            for _ in range(2):
                r = subprocess.run(
                    [sys.executable, str(ROOT / "thailand_agent.py"), "--demo",
                     "--data-dir", tmp, "--rapport", str(rapport), "--varsel", str(varsel)],
                    capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            tekst = rapport.read_text(encoding="utf-8")
            for seksjon in ("## Kort fortalt", "Billigste kun fly", "## Treff", "## Eldre funn", "## Restplass og pakkereiser", "## Kilder",
                            "Aviasales", "Khao Lak", "reise til ARN"):
                self.assertIn(seksjon, tekst)
            funn = json.loads((Path(tmp) / "funn.json").read_text())
            self.assertEqual(set(funn), {"google_flights", "aviasales", "restplass"})
            # Billige Aviasales-datoer bekreftes i Google Flights
            self.assertIn("2026-11-26|2026-12-09", funn["google_flights"])
            self.assertTrue((Path(tmp) / "prishistorikk.csv").exists())
            self.assertIn("varslet", json.loads((Path(tmp) / "tilstand.json").read_text()))


if __name__ == "__main__":
    unittest.main()
