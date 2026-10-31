# WimsKrant

Rustige leesweergave van artikelen uit NRC en Trouw, gemaakt voor lezers met (beginnende) dementie.

- **Krant kiezen**: eerst kiest de lezer NRC of Trouw, daarna ziet die de artikelen van die krant per sectie,
  met foto. Klik er een aan om te lezen. (Trouw: de nieuwste stukken uit de RSS-feed.)
- **Rubrieken**: bovenaan de voorpagina van elke krant staat een snelmenu met de rubrieken
  (bij NRC de pagina's nrc.nl/index/…, bij Trouw de RSS-feed per rubriek).
- **Eerder gelezen**: op het keuzescherm staan alle gelezen artikelen, met krant en datum.
- **Uitleg op aanvraag**: woorden waarvoor uitleg klaarstaat zijn onderstreept. Tik erop en de uitleg
  verschijnt in een venster onderin het scherm (op iPhone, iPad en computer hetzelfde).
- **Geheugensteun**: algemene kennis wordt bekend verondersteld. Wat het artikel zelf vertelt
  (wie een persoon is, wat een afkorting betekent, wat er gebeurde) staat klaar als het later
  weer genoemd wordt ("Weet u nog?").
- **Vaktermen** die het artikel niet uitlegt, krijgen één keer uitleg en later een kort geheugensteuntje.
- **Zelf opzoeken**: tik op een ander woord (of selecteer een paar woorden). Claude legt het uit, en zegt erbij
  wat het artikel er eerder over vertelde.
- "In het kort" bovenaan, "Alle uitleg op een rij" onderaan, en grotere letters.

## Starten

```bash
python3 server.py
```

Open daarna http://localhost:8765.

### Op een iPad of iPhone (zelfde wifi)

Zet in `.env` de regel `WIMSKRANT_HOST=0.0.0.0` en start de server. Die toont dan het adres voor de iPad,
bijvoorbeeld `http://192.168.68.57:8765`. Open dat in Safari en kies Deel → **Zet op beginscherm**:
WimsKrant krijgt dan een eigen icoon en opent schermvullend. De Mac moet aan staan en de server moet draaien.

## Toegang met een code (voor hosting op internet)

Zet in `.env` één of meer persoonlijke codes, als `naam:code`, gescheiden door komma's:

```
WIMSKRANT_CODES=wim:kies-een-lange-code,anna:een-andere-lange-code
```

- Wie de site opent, ziet eerst een scherm "Vul de toegangscode in". Na een goede code onthoudt de browser
  dat een jaar lang. Op de iPad: open de site, vul de code in, en zet hem daarna op het beginscherm
  (een app op het beginscherm kan een eigen geheugen hebben; vul de code daar zo nodig nog één keer in).
- Iemand de toegang afnemen: haal zijn code weg of verander hem, en herstart de server. Zijn browser
  moet dan opnieuw een code invullen; de anderen merken niets.
- Na 5 foute pogingen moet dat adres een kwartier wachten (en na 30 foute pogingen in totaal iedereen).
- Gebruik lange codes (bijvoorbeeld drie woorden met streepjes) en host alleen via **https**. Achter een
  proxy van de hostingdienst zet de server de cookie dan vanzelf als `Secure`.
- De server ondertekent de cookies met een sleutel in `.geheim` (wordt vanzelf gemaakt). Deel dat bestand niet.
  Weggooien en herstarten = iedereen moet opnieuw een code invullen.

Zonder `WIMSKRANT_CODES` is de app voor iedereen open: prima thuis op de Mac, niet op internet.

## Hosten op Render

`render.yaml` beschrijft de hele opzet: een webdienst in Frankfurt (Starter, ca. $7 per maand) met een
schijf van 1 GB (ca. $0,25 per maand) waarop de uitleg en de cookiesleutel bewaard blijven.

1. **GitHub**: maak op github.com een nieuwe, **privé** repository `wimskrant` (zonder README) en zet de code erin:
   ```bash
   git add -A && git commit -m "WimsKrant" && git remote add origin https://github.com/<jouw-naam>/wimskrant.git && git push -u origin main
   ```
   `.gitignore` zorgt dat `.env`, `.geheim` en `begrippen/` niet meegaan.
2. **Render**: maak een account op render.com, kies **New → Blueprint** en koppel de repository.
   Render leest `render.yaml` en vraagt om twee waarden:
   - `ANTHROPIC_API_KEY`: je sleutel (dezelfde als in `.env`)
   - `WIMSKRANT_CODES`: de toegangscodes, bijv. `wim:een-lange-code` (dezelfde als in je `.env`)
3. Kies **Apply**. Na een paar minuten staat de site op `https://wimskrant.onrender.com` (of een
   vergelijkbaar adres; zie het dashboard).
4. **Controle**: open in het dashboard **Logs**. Vlak na het starten staat daar
   `Controle NRC: werkt …` en `Controle Trouw: werkt …`. Staat er `LUKT NIET` of `LET OP`, dan blokkeert
   die krant het datacenter van Render; zie hieronder.
5. **Bijwerken**: na `git commit` en `git push` zet Render de nieuwe versie er vanzelf op.

**Eerst gratis proberen?** Kies dan in plaats van stap 2 **New → Web Service**, plan **Free**, start-opdracht
`python3 server.py`, en zet bij Environment `WIMSKRANT_HOST=0.0.0.0`, `WIMSKRANT_CODES` en (voor de uitleg)
`ANTHROPIC_API_KEY`. De gratis versie heeft geen schijf en slaapt na een kwartier stilte, maar de
controleregels in de logs laten zien of NRC en Trouw werken. Verwijder hem daarna en ga verder met stap 2.

**Werkt een krant niet vanaf Render?** Dan kan WimsKrant thuis blijven draaien en via Tailscale Funnel of
Cloudflare Tunnel een https-adres krijgen; dan haalt de server de artikelen op vanaf je eigen internetaansluiting.

## Instellingen (`.env`)

```
ANTHROPIC_API_KEY=sk-ant-...      # nodig voor de geheugensteun en het opzoeken
WIMSKRANT_CODES=wim:...,anna:...  # toegangscodes, zie hierboven
NRC_COOKIE=...                    # optioneel, zie hieronder
WIMSKRANT_MODEL=claude-sonnet-5-5 # optioneel, standaard Sonnet 5.5
WIMSKRANT_DATA=/pad/naar/map      # optioneel: waar uitleg en .geheim bewaard worden (standaard deze map)
```

De geheugensteun wordt per artikel één keer gemaakt (ongeveer een minuut) en bewaard in
`begrippen/<sleutel>.json`; opgezochte woorden in `begrippen/<sleutel>-opgezocht.json`.
Deze bestanden zijn met de hand aan te passen.

### Inloggen bij NRC

NRC stuurt de volledige artikeltekst nu ook zonder inloggen mee. Werkt dat ooit niet meer, dan kan de server
je abonnement gebruiken via de cookie van je eigen ingelogde browser:

1. Log in op nrc.nl in Chrome.
2. Open de ontwikkelaarstools (⌥⌘I) → tabblad **Netwerk** → herlaad de pagina → klik op het eerste verzoek
   (`www.nrc.nl`) → kopieer bij **Request Headers** de waarde van `cookie`.
3. Zet die in `.env` als `NRC_COOKIE=...` en herstart de server.

Behandel deze cookie als een wachtwoord: deel `.env` met niemand.

### Trouw

Trouw (DPG Media) toont eerst een cookiekeuze. De server kiest daar alleen functionele cookies
(geen advertentietracking) en onthoudt die keuze zolang hij draait.
