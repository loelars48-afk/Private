# Thailand-agenten ✈️🇹🇭

Agenten leter automatisk etter billige reiser **Oslo Gardermoen → Thailand** for **2 voksne**
i flere kilder samtidig, og samler alt i [`RAPPORT.md`](RAPPORT.md).

| Kriterium | Verdi |
|---|---|
| Utreise | fra 24. november 2026 |
| Hjemme | senest 22. desember 2026 |
| Lengde | 10–21 netter |
| Destinasjoner | Bangkok (BKK, DMK), Phuket (HKT), Krabi (KBV), Koh Samui (USM) + alle Thailand-tilbud på restplass-sidene |
| Makspris | 5 000 kr **per person** (nesten-treff vises opp til 6 000 kr) |
| Stopp | maks 2 |
| Bagasje | pris vises både uten og med innsjekket bagasje (estimat) |
| Kjøring | GitHub Actions kl. ca. 07 og 19 hver dag |

Alt endres i [`config.json`](config.json).

## Kildene

| Kilde | Hva den gir | Nøkkel (GitHub-secret) | Kostnad |
|---|---|---|---|
| **Aviasales / Travelpayouts** | Bufrede lavpriser for *alle* datoer i perioden. Finner billige datoer raskt. | `TRAVELPAYOUTS_TOKEN` | Gratis |
| **Google Flights** (SerpApi) | Ferske priser. Bekrefter først billige Aviasales-datoer, så roterer den gjennom resten. | `SERPAPI_KEY` | Gratisnivå eller betalt |
| **Restplass- og chartersider** | Restplasser og charter (kun fly og pakkereiser) fra sidene under. Claude leser sidene. | `ANTHROPIC_API_KEY` | Etter forbruk, se under |

Restplass-sidene som sjekkes (listen `restplass_sider` i `config.json`):

- [restplass.no – Thailand](https://www.restplass.no/charter/thailand) (samler Apollo, Ving, TUI m.fl.)
- [Ving – restplasser Thailand](https://www.ving.no/thailand/restplasser)
- [Travelmarket – restplasser Thailand](https://www.travelmarket.no/thailand/restplasser.cfm)
- [Lastminutecharter – Thailand](https://lastminutecharter.eu/restplasser/thailand)
- Reiseguiden, TUI og Apollo: adressene er **ikke bekreftet**. Rapportens kildetabell viser om de virker.
  Bytt adresse i `config.json` hvis en side viser «kunne ikke åpne» eller «nevner ikke Thailand».

Hver kilde er valgfri: mangler en nøkkel, hoppes den kilden over, og rapporten sier det.

**Ikke tatt med (bevisst):** Momondo, Kayak og Skyscanner blokkerer roboter, og Skyscanners API er bare
for partnere. De samme flyselskapene dekkes i praksis av Google Flights og Aviasales.

## Oppsett

Legg inn nøklene i GitHub-repoet under **Settings → Secrets and variables → Actions → New repository secret**:

1. `TRAVELPAYOUTS_TOKEN`: lag en gratis konto på travelpayouts.com. Tokenet ligger under
   *Profile → API token* (eventuelt *Tools → API*).
2. `SERPAPI_KEY`: konto på serpapi.com. Nøkkelen ligger på dashbordet.
3. `ANTHROPIC_API_KEY`: konto på console.anthropic.com, legg inn betalingskort og lag en API-nøkkel.
   Sett gjerne en månedlig utgiftsgrense der.

Start deretter første kjøring: **Actions → Thailand-agent → Run workflow**.

## Slik virker den

1. **Aviasales** hentes for hele perioden (8 kall, gratis). Det gir priser på mange datoer.
2. **Restplass-sidene** åpnes i en ekte nettleser (Playwright). Nevner siden Thailand og har innholdet
   endret seg siden sist, leser Claude teksten og returnerer tilbudene som strukturerte data:
   destinasjon, datoer, pris, per person eller totalt, kun fly eller pakkereise. Agenten filtrerer
   deretter på reisevinduet og avreise fra Oslo. Like tilbud fra flere samlesider slås sammen.
3. **Google Flights** søker på noen få dato-kombinasjoner per kjøring. Datoer Aviasales har
   funnet under 6 000 kr per person sjekkes først, slik at bufrede priser blir bekreftet med ferske.
4. Alt samles i `RAPPORT.md`. Historikken lagres i `data/prishistorikk.csv`.

## Kostnader

| Kilde | Per måned (anslag) | Kommentar |
|---|---|---|
| Aviasales | 0 kr | |
| Google Flights | 0 kr på gratisnivå | ~120 søk/mnd med standardinnstilling (`sok_per_kjoring: 2`). Sjekk serpapi.com/pricing. |
| Claude (`claude-opus-5-5`) | ca. 50–200 kr | Avhenger av hvor store sidene er og hvor ofte de endres. Claude brukes bare når en side er endret. |
| Claude (`claude-haiku-5-5`) | under 10 kr | Billigere modell. Sannsynligvis god nok til å lese prislister, men ikke testet her. |

Modellen velges med `claude_modell` i `config.json`.

## Viktige begrensninger

- **Priser er øyeblikksbilder.** Aviasales-priser kan være flere døgn gamle. Sjekk «Sjekket»-tidspunktet
  og bekreft prisen før du bestiller.
- **Restplass-tilbud tolkes av Claude.** Står det ikke tydelig om prisen er per person, merkes raden
  «antatt pris per person». Tilbud uten dato merkes «dato ukjent» og vises bare i restplass-seksjonen.
- **Pakkereiser** inkluderer hotell og er derfor ikke direkte sammenlignbare med kun fly.
- **Bagasje:** kolonnen «Med innsj. bag» bruker et *estimat* (600 kr per person per vei) når bagasje
  ikke er oppgitt.
- Agenten **bestiller ingenting**.

### Antakelser som bør bekreftes etter første ekte kjøring

Utviklingsmiljøet hadde ikke tilgang til disse tjenestene, så de er testet med eksempeldata:

1. At SerpApi oppgir prisen **totalt for begge** reisende. Sammenlign med Google Flights. Er prisen
   per person, sett `"pris_fra_api_er_totalt_for_alle": false`.
2. At Aviasales-feltene (`price`, `departure_at`, `return_at`, `transfers`, `link`) heter det koden
   forventer. Viser kildetabellen «ok», men 0 treff over lengre tid, må dette sjekkes.
3. At restplass-sidene lar seg åpne fra GitHub Actions uten å blokkere. Se sidetabellen i rapporten.

## Lokalt

```bash
pip install -r requirements.txt && python -m playwright install chromium
python thailand_agent.py --demo --data-dir /tmp/demo --rapport /tmp/demo.md   # testdata, ingen nøkler
python thailand_agent.py --dry-run                                             # vis neste Google-søk
python -m unittest discover -s tests                                           # tester
```
