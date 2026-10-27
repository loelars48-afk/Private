# Thailand-agenten ✈️🇹🇭

Agenten leter automatisk etter billige tur/retur-billetter **Oslo Gardermoen → Thailand**
for **2 voksne**, og skriver resultatet til [`RAPPORT.md`](RAPPORT.md).

| Kriterium | Verdi |
|---|---|
| Utreise | fra 24. november 2026 |
| Hjemme | senest 22. desember 2026 |
| Lengde | 10–21 netter |
| Destinasjoner | Bangkok (BKK, DMK), Phuket (HKT), Krabi (KBV), Koh Samui (USM) |
| Makspris | 5 000 kr **per person** (nesten-treff vises opp til 6 000 kr) |
| Stopp | maks 2 |
| Bagasje | pris vises både uten og med innsjekket bagasje (estimat) |
| Kjøring | GitHub Actions kl. ca. 07 og 19 hver dag |

Alt endres i [`config.json`](config.json).

## Oppsett (5 minutter)

1. Lag en konto på **serpapi.com** og kopier API-nøkkelen fra *Dashboard*.
2. I GitHub-repoet går du til **Settings → Secrets and variables → Actions → New repository secret**.
   Kall den `SERPAPI_KEY` og lim inn nøkkelen.
3. Gå til **Actions → Thailand-agent → Run workflow** for å starte første søk med én gang.
4. Åpne `RAPPORT.md` i repoet. Rapporten vises også i oppsummeringen for hver kjøring under Actions.

## Slik virker den

- Det finnes **162 dato-kombinasjoner** (utreise × hjemreise) i reisevinduet.
  Google Flights krever ett søk per kombinasjon. Alle fem flyplassene dekkes i samme søk.
- Hver kjøring søker på noen få kombinasjoner og roterer. Usjekkede datoer kommer først,
  og deretter de som er sjekket for lengst siden. Første runde sprer seg over hele perioden.
- Det billigste alternativet per dato lagres i `data/funn.json`. Alle funn logges i
  `data/prishistorikk.csv`.
- Datoer som har passert, fjernes automatisk.

## API-kvote: velg tempo

`sok_per_kjoring` i `config.json` styrer forbruket. Med 2 kjøringer per dag og ca. 46 dager igjen
av søkevinduet:

| `sok_per_kjoring` | Søk per måned | Hele vinduet dekket på | Passer for |
|---:|---:|---|---|
| 1 | ~60 | ~81 dager (rekker ikke alt) | gratisnivå |
| **2 (standard)** | ~120 | ~41 dager | gratisnivå *hvis* kvoten er ≥ 120 |
| 4 | ~240 | ~20 dager | betalt nivå |
| 8 | ~480 | ~10 dager | betalt nivå, best dekning |

Sjekk gjeldende kvote og pris på serpapi.com/pricing. Når kvoten er brukt opp, stopper
agenten pent og skriver det i loggen nederst i rapporten.

## Viktige begrensninger

- **Restplasser og charter** (Ving, TUI, Apollo) finnes ikke i Google Flights.
  Rapporten har lenker til disse, men de må sjekkes manuelt.
- **Bagasje:** Google Flights oppgir ikke alltid prisen for innsjekket bagasje.
  Kolonnen «Med innsj. bag» legger til et *estimat* (600 kr per person per vei, kan endres i config).
- **Priser er øyeblikksbilder.** Lavpriser kan forsvinne i løpet av timer. Sjekk alltid
  «Sjekket»-tidspunktet, og bekreft prisen før du bestiller.
- Agenten **bestiller ingenting**. Den finner og rapporterer.

### Antakelser som bør bekreftes etter første ekte kjøring

Disse er ikke testet mot SerpApi live, fordi utviklingsmiljøet ikke hadde tilgang:

1. At `price` fra SerpApi er **totalt for begge** reisende (Google Flights viser totalpris).
   Sammenlign én pris i rapporten med Google Flights. Hvis prisen er per person, sett
   `"pris_fra_api_er_totalt_for_alle": false` i config.
2. At bagasjetekstene kommer på engelsk, for eksempel «Checked baggage for a fee».
   Hvis kolonnen bare viser «ukjent», må tolkingen justeres.

## Lokalt

```bash
python thailand_agent.py --dry-run          # vis neste søk
python thailand_agent.py --demo --data-dir /tmp/demo --rapport /tmp/demo.md   # testdata
SERPAPI_KEY=... python thailand_agent.py    # ekte søk
python -m unittest discover -s tests        # tester
```
