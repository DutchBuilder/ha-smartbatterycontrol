# SmartBatteryControl voor Home Assistant

Deze integratie brengt een lokale [SmartBatteryControl](#wat-is-smartbatterycontrol) in Home Assistant: de thuisbatterijen,
de plannen voor vandaag en morgen, de doorrekening daarvan, het gemeten rendement en de stroomprijzen.
Alles gaat over je eigen netwerk; er is geen cloud nodig.

*English below.*

## Installeren

1. Voeg in HACS deze repository toe als **aangepaste repository** (type: Integratie) en installeer **SmartBatteryControl**.
2. Herstart Home Assistant.
3. Maak in SmartBatteryControl een API-token: **Instellingen → Beveiliging → API-tokens → Token maken**.
   Laat "Mag ook sturen" uit; deze integratie leest alleen.
4. Ga in Home Assistant naar **Instellingen → Apparaten en diensten → Integratie toevoegen → SmartBatteryControl**.
   Vul het adres in (bijvoorbeeld `192.168.1.50`), poort `80` en plak het token.

### Adres, poort of token wijzigen

Kies bij de integratie **⋮ → Opnieuw configureren**. Pas het adres, de poort of het token aan; laat je het token leeg,
dan blijft het huidige. De nieuwe gegevens worden eerst gecontroleerd, daarna laadt de integratie opnieuw.

Verwijder je de integratie en voeg je hem opnieuw toe, dan komen alle apparaten en sensoren terug. Voeg je een
SmartBatteryControl toe die al gekoppeld is (bijvoorbeeld met een nieuw token), dan wordt die koppeling bijgewerkt.

### Updates

Elke nieuwe versie verschijnt als release; HACS zet hem dan bij **Instellingen → Updates**. HACS kijkt een paar keer
per dag; wil je meteen kijken, kies dan in HACS bij SmartBatteryControl **⋮ → Informatie bijwerken**. Herstart Home
Assistant na het bijwerken.

## Wat je krijgt

Per batterij een apparaat met:

| Sensor | Wat |
|---|---|
| Laadniveau | % |
| Vermogen | W aan de netkant: negatief = laden, positief = ontladen |
| Gepland vermogen | wat het schema op dit moment vraagt |
| Zelfconsumptie setpunt | het vermogen bij nul op de meter |
| Rendement (RTE, 30 dagen) | gemeten rendement heen en terug |
| Volgend blok | begintijd van het volgende laad- of ontlaadblok (met eindtijd en vermogen) |
| Plan vandaag / morgen (schatting planner) | verwachte opbrengst volgens de planner, met de blokken als attribuut |
| Plan vandaag / morgen (doorgerekend) | de volledige doorrekening: laden, ontladen, verliezen, slijtage en restwaarde |
| Geladen vandaag / Ontladen vandaag | kWh sinds middernacht (bruikbaar in het Energie-dashboard) |
| Planning vandaag / morgen | de geplande blokken als tekst, met de blokken en de uren laden en ontladen als attribuut |
| Laadkosten, opbrengst ontladen en netto (vandaag en deze maand) | wat laden werkelijk kostte en ontladen opleverde, in euro |
| Maximale ontlading tot / Maximaal laden tot | de laadgrenzen van de batterij in SmartBatteryControl (%) |
| Alle sensoren uit de batterij | spanning, stroom, temperaturen, cellen, energietellers, status en instellingen (zoals SmartBatteryControl ze uitleest) |

Per batterij ook een **agenda** (Planning) met elk laad- en ontlaadblok van vandaag en morgen: te zien in de agenda van
Home Assistant en te gebruiken als trigger in een automatisering.

En aan/uit-sensoren: bereikbaar, sturen, zelfconsumptie actief, volgt schema niet, schema gelijk aan plan (gelijk aan wat er
naar de batterij gestuurd is).

De sensoren uit de batterij komen uit de cache van SmartBatteryControl (die ze zelf op de achtergrond vers houdt):
Home Assistant kost de batterij dus geen extra verzoeken.

Voor SmartBatteryControl zelf: net, zon, stroomprijs en terugleverprijs nu (met alle kwartierprijzen van vandaag en morgen
als attribuut), laagste en hoogste prijs van vandaag, prijsbron en strategie, en wanneer de volgende planning en de
volgende push naar de batterijen is (als dat volgens schema gaat).

## Wat is SmartBatteryControl?

Software op een Raspberry Pi die thuisbatterijen (zoals de Marstek Venus E 3.0) lokaal aanstuurt op basis van de
dynamische stroomprijzen, het eigen verbruik en de zon. SmartBatteryControl zelf is geen onderdeel van deze repository.

---

## English

This integration brings a local SmartBatteryControl into Home Assistant: home batteries with all their sensors, today's
and tomorrow's plans (also as a calendar) with a full calculation, what charging cost and discharging earned, measured
round-trip efficiency, and electricity prices. Everything stays on your own network.

Install through HACS as a custom repository, restart Home Assistant, create a read-only API token in SmartBatteryControl
(Settings → Security → API tokens) and add the integration with the address, port and token. To change the address,
port or token later, choose **⋮ → Reconfigure** on the integration (leave the token empty to keep the current one).

## Licentie / License

MIT
