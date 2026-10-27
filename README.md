# Thailand-agenten ✈️🇹🇭

Agenten leter automatisk etter billige reiser **Oslo → Thailand** for **2 voksne** i flere kilder
samtidig, samler alt i [`RAPPORT.md`](RAPPORT.md) og **varsler med en GitHub-sak** når den finner noe nytt.

| Kriterium | Verdi |
|---|---|
| Avreise | Gardermoen, Stockholm Arlanda, København og Torp. For de tre siste legges et anslag for reisen dit til prisen (se under). |
| Utreise | fra 24. november 2026 |
| Hjemme | **landet i Oslo senest 22. desember**. Agenten krever derfor avreise fra Thailand senest 21.12 (nattfly lander dagen etter). |
| Lengde | 10–21 netter |
| Destinasjoner | Bangkok (BKK, DMK), Phuket (HKT), Krabi (KBV), Koh Samui (USM) + alle Thailand-tilbud på restplass-sidene |
| Grense, kun fly | 5 000 kr **per person** (nesten-treff opp til 6 000 kr) |
| Grense, pakkereise (fly + hotell) | 10 000 kr **per person** (nesten-treff opp til 11 000 kr) |
| Stopp | maks 2 |
| Bagasje | pris vises både uten og med innsjekket bagasje (estimat) |
| Kjøring | GitHub Actions kl. ca. 07 og 19 hver dag |

Prisen som sammenlignes med grensene er **totalpris per person = billett + anslått reise til
avreiseflyplassen**:

| Flyplass | Tillegg per person (tur/retur fra Oslo, anslag) |
|---|---:|
| Oslo Gardermoen | 0 kr |
| Sandefjord Torp | 400 kr |
| Stockholm Arlanda | 1 000 kr |
| København Kastrup | 1 000 kr |

Tilleggene er grove anslag. Juster dem i `config.json` (`tillegg_per_person_nok`).

## Varsling

Når agenten finner treff som er nye, eller som har blitt minst 5 % billigere siden forrige varsel,
oppretter den en **sak (issue) i repoet** med direkte lenker, og nevner deg (@-mention), slik at
GitHub sender varsel. Installer GitHub-appen på mobilen og slå på push-varsler for å få det raskt.
Det kommer ikke nytt varsel om det samme treffet, med mindre det forsvinner og dukker opp igjen.

Bare **ferske** funn regnes som treff: tilbud som er sjekket de siste 48 timene. Eldre funn under
grensen vises i en egen seksjon, og agenten sjekker billige datoer i Google Flights på nytt hvert døgn.

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

## Lenkene i rapporten

Øverst i `RAPPORT.md` står hver reise på én linje med en direkte lenke:

| Lenke | Hvor den går |
|---|---|
| **Åpne billetten** (Aviasales) | Den konkrete billetten, med 2 voksne utfylt. |
| **Åpne tilbudet** (restplass) | Det konkrete restplass-tilbudet eller pakken hos turoperatøren eller samlesiden. |
| **Åpne søket med disse datoene** (Google Flights) | Google Flights med rute og datoer utfylt. Den billigste flyvningen står øverst. Google Flights har ingen stabile lenker til én enkelt billett, så dette er det mest direkte som finnes. |
| **Åpne siden** (restplass) | Tilbudet hadde ingen egen lenke på siden. Du må finne det i listen. Raden er merket «fant ingen egen lenke». |

Lenkene til restplass-tilbudene hentes fra selve siden: agenten samler alle lenkene og teksten på
kortet hver lenke står i, og Claude velger hvilken lenke som hører til hvert tilbud. Claude kan bare
velge blant lenker som faktisk står på siden, så adressene blir aldri diktet opp.

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
- Når reisevinduet er passert, gjør agenten ingen søk (ingen kostnad). Slå da av workflowen under Actions.

### Antakelser som bør bekreftes etter første ekte kjøring

Utviklingsmiljøet hadde ikke tilgang til disse tjenestene, så de er testet med eksempeldata:

1. At SerpApi oppgir prisen **totalt for begge** reisende. Sammenlign med Google Flights. Er prisen
   per person, sett `"pris_fra_api_er_totalt_for_alle": false`. Agenten merker priser under
   2 500 kr per person som «mistenkelig lav» og skriver en advarsel i loggen, fordi det er tegn på
   at denne innstillingen er feil.
2. At SerpApi godtar flere flyplasser i ett søk (`OSL,ARN,CPH,TRF` → `BKK,DMK,HKT,KBV,USM`). Hvis ikke,
   faller agenten tilbake til OSL → BKK og skriver en advarsel (⚠️) i loggen.
3. At Aviasales-feltene (`price`, `departure_at`, `return_at`, `transfers`, `link`) heter det koden
   forventer. Viser kildetabellen «ok», men 0 treff over lengre tid, må dette sjekkes.
4. At restplass-sidene lar seg åpne fra GitHub Actions uten å blokkere. Se sidetabellen i rapporten.

## Lokalt

```bash
pip install -r requirements.txt && python -m playwright install chromium
python thailand_agent.py --demo --data-dir /tmp/demo --rapport /tmp/demo.md   # testdata, ingen nøkler
python thailand_agent.py --dry-run                                             # vis neste Google-søk
python -m unittest discover -s tests                                           # tester
```
