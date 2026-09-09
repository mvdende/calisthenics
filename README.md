# Calisthenics

Wekelijkse bodyweight-workouts, automatisch in je Garmin Connect-agenda én als
begeleider op je telefoon — met uitleg en animatie per oefening, naast je
Garmin epix Pro.

## Hoe het in elkaar zit

```
garmin_calisthenics.py  →  Garmin Connect-agenda (horloge)
                        ↘
                          plan.json  →  workout.html (telefoon, PWA)
```

Eén Python-script is de enige bron van waarheid: het kiest elke week
afwisselende oefeningen, zet ze als krachttraining in je Garmin-agenda, én
schrijft dezelfde workout weg als `plan.json`. `workout.html` leest dat
JSON-bestand en loodst je er stap voor stap doorheen — met een Nederlandse
uitleg en een inline SVG-animatie per oefening. Er is geen live verbinding
tussen de app en je horloge; de app toont alleen hetzelfde plan, jij bepaalt
zelf het tempo.

## Bestanden

| Bestand | Wat |
|---|---|
| `garmin_calisthenics.py` | Genereert workouts, plant ze in Garmin Connect, exporteert `plan.json` |
| `workout.html` | De webapp: één zelfstandig bestand, alles inline, geen build-stap |
| `plan.json` | Het huidige weekplan — wordt door het script overschreven |
| `sw.js` | Service worker (cachet de app voor offline gebruik) |
| `oefeningen.html` | Losse naslagpagina met alle 47 oefeningen en hun uitleg |

## Aan de slag

```bash
python3 -m venv .venv
.venv/bin/pip install garminconnect pydantic

export GARMIN_EMAIL="jij@example.com"
export GARMIN_PASSWORD="..."
```

De eerste keer inloggen vraagt Garmin om een MFA-code; daarna wordt het token
gecached in `~/.garminconnect` en hoef je niet opnieuw in te loggen.

### Workouts genereren en inplannen

```bash
.venv/bin/python garmin_calisthenics.py --days 7 --dry-run   # eerst kijken
.venv/bin/python garmin_calisthenics.py --days 7              # aanmaken + inplannen
.venv/bin/python garmin_calisthenics.py --days 7 --push       # ook direct naar het horloge
```

Elke run schrijft ook `plan.json` (uit dezelfde workout-objecten die naar
Garmin gaan, dus horloge en telefoon lopen nooit uit de pas). `--dry-run`
werkt zonder Garmin-login, dus je kunt `plan.json` bijwerken zonder
`GARMIN_EMAIL`/`GARMIN_PASSWORD` te zetten:

```bash
.venv/bin/python garmin_calisthenics.py --days 7 --dry-run --export-json plan.json
```

Belangrijkste opties: `--sets`, `--rest`, `--minutes` (max. sessieduur),
`--no-warmup`, `--seed` (reproduceerbare selectie), `--export-json PAD`
(leeg om te skippen). Zie `--help` voor de volledige lijst.

### plan.json baseren op wat er al in de agenda staat

In plaats van lokaal iets nieuws te verzinnen, kun je ook teruglezen wat er
al écht in je Garmin-agenda staat (handig als je iets met de hand hebt
aangepast in Garmin Connect):

```bash
.venv/bin/python garmin_calisthenics.py --from-calendar --days 7
```

Dit vereist wel `GARMIN_EMAIL`/`GARMIN_PASSWORD` (de agenda staat op Garmins
servers) en genereert niets — het leest alleen. `--sets`/`--rest`/`--minutes`/
`--no-warmup`/`--seed` worden in deze modus genegeerd.

### De app draaien

`workout.html` haalt `plan.json` op met `fetch()`. Browsers blokkeren dat
wanneer je de pagina rechtstreeks als bestand opent (dubbelklikken) — draai
daarom een simpele lokale server:

```bash
python3 -m http.server 8000
```

en open `http://localhost:8000/workout.html`. Zonder server toont de app
een duidelijke banner en valt hij terug op ingebouwde voorbeelddata, zodat
je nooit in de veronderstelling blijft dat je je échte plan ziet.

Eenmaal geladen is de app installeerbaar als PWA (manifest + service
worker) en werkt hij daarna ook offline.

## plan.json-formaat

```json
{
  "generated": "2026-09-09",
  "days": [
    {
      "date": "2026-09-10",
      "scheme": "Full body",
      "estimated_minutes": 13,
      "warmup": [
        { "name": "Walking High Knees", "key": "WARM_UP/WALKING_HIGH_KNEES",
          "kind": "time", "target": 40, "sets": 1 }
      ],
      "exercises": [
        { "name": "Air Squat", "key": "SQUAT/AIR_SQUAT",
          "kind": "reps", "target": 20, "sets": 3, "rest": 30 },
        { "name": "Plank", "key": "PLANK/PLANK",
          "kind": "time", "target": 45, "sets": 3, "rest": 30 }
      ]
    }
  ]
}
```

`kind` is `reps` of `time` (dan is `target` het aantal seconden). `key` is
de Garmin-categorie/oefening-combinatie en wordt in `workout.html` gebruikt
om de Nederlandse uitleg, spiergroep en animatie op te zoeken (zie de
`EXINFO`-tabel bovenin het `<script>`-blok).

## Bekende beperkingen

- **Geen live koppeling met het horloge.** De app kan niet uitlezen waar je
  op je Garmin bent — "hetzelfde plan tonen" is het enige dat mogelijk is.
- **Animaties zijn gedeeld per bewegingsfamilie** (push-up, squat, lunge,
  plank, crunch, sprong) in plaats van 47 unieke animaties.
- **iOS Safari**: Wake Lock werkt pas vanaf iOS 16.4, en het SVG-manifesticoon
  wordt genegeerd (geen showstopper, gewoon een minder mooi thuisscherm-icoon).
- **`--from-calendar` gebruikt onofficiële Garmin-endpoints** (net als de
  rest van `garminconnect`); test met `--dry-run` op een dag met een bekende
  workout voordat je erop vertrouwt.
