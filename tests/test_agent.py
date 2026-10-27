import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import thailand_agent as a  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class DatoTest(unittest.TestCase):
    def test_alle_kombinasjoner_innenfor_vinduet(self):
        komb = a.dato_kombinasjoner(CFG, date(2026, 10, 27))
        self.assertTrue(komb)
        for ut, hjem in komb:
            self.assertGreaterEqual(ut, date(2026, 11, 24))
            self.assertLessEqual(hjem, date(2026, 12, 22))
            self.assertTrue(10 <= (hjem - ut).days <= 21)
        self.assertEqual(komb[0], (date(2026, 11, 24), date(2026, 12, 4)))
        self.assertIn((date(2026, 12, 12), date(2026, 12, 22)), komb)
        self.assertNotIn((date(2026, 12, 13), date(2026, 12, 23)), komb)

    def test_passerte_datoer_faller_bort(self):
        komb = a.dato_kombinasjoner(CFG, date(2026, 12, 1))
        self.assertTrue(all(ut >= date(2026, 12, 2) for ut, _ in komb))
        self.assertEqual(a.dato_kombinasjoner(CFG, date(2026, 12, 20)), [])

    def test_rotasjon_prioriterer_usjekkede(self):
        komb = a.dato_kombinasjoner(CFG, date(2026, 10, 27))
        sjekket = {a.nokkel(*k): "2026-10-27T00:00:00+00:00" for k in komb[1:]}
        self.assertEqual(a.velg_neste_sok(komb, sjekket, 1), [komb[0]])
        # Første runde sprer seg over flere utreisedatoer
        forste = a.velg_neste_sok(komb, {}, 4)
        self.assertGreater(len({ut for ut, _ in forste}), 1)


class TolkingTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(a.FIXTURE.read_text(encoding="utf-8"))
        self.funn = a.tolk_resultat(CFG, self.data, date(2026, 11, 24), date(2026, 12, 8), "t")

    def test_pris_per_person_og_tomme_alternativer(self):
        self.assertEqual(len(self.funn), 3)
        pegasus = next(f for f in self.funn if f["flyselskap"] == "Pegasus")
        self.assertEqual(pegasus["pris_per_person"], 4600)
        self.assertEqual(pegasus["til"], "HKT")
        self.assertEqual(pegasus["stopp"], 1)

    def test_bagasje(self):
        status = {f["flyselskap"]: f for f in self.funn}
        self.assertEqual(status["Qatar Airways"]["bagasje"], "inkludert")
        self.assertEqual(status["Qatar Airways"]["pris_per_person_med_bag"], 5900)
        self.assertEqual(status["Pegasus"]["bagasje"], "ikke inkludert")
        self.assertEqual(status["Pegasus"]["pris_per_person_med_bag"], 4600 + 1200)
        self.assertEqual(status["Finnair"]["bagasje"], "ukjent")


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
            self.assertIn("## Treff", tekst)
            tilstand = json.loads((Path(tmp) / "tilstand.json").read_text())
            self.assertEqual(len(tilstand["sjekket"]), 2 * CFG["sok_per_kjoring"])


if __name__ == "__main__":
    unittest.main()
