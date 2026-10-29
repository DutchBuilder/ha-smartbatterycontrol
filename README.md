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

En aan/uit-sensoren: bereikbaar, sturen, zelfconsumptie actief, volgt schema niet, schema gelijk aan plan.

Voor SmartBatteryControl zelf: net, zon, stroomprijs en terugleverprijs nu (met alle kwartierprijzen van vandaag en morgen
als attribuut), laagste en hoogste prijs van vandaag, prijsbron en strategie.

## Wat is SmartBatteryControl?

Software op een Raspberry Pi die thuisbatterijen (zoals de Marstek Venus E 3.0) lokaal aanstuurt op basis van de
dynamische stroomprijzen, het eigen verbruik en de zon. SmartBatteryControl zelf is geen onderdeel van deze repository.

---

## English

This integration brings a local SmartBatteryControl into Home Assistant: home batteries, today's and tomorrow's plans
with a full calculation, measured round-trip efficiency, and electricity prices. Everything stays on your own network.

Install through HACS as a custom repository, restart Home Assistant, create a read-only API token in SmartBatteryControl
(Settings → Security → API tokens) and add the integration with the address, port and token.

## Licentie / License

MIT
