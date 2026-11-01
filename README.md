# SmartBatteryControl voor Home Assistant

Deze integratie brengt een lokale [SmartBatteryControl](#wat-is-smartbatterycontrol) in Home Assistant: de thuisbatterijen,
de plannen voor vandaag en morgen, de doorrekening daarvan, het gemeten rendement, de stroomprijzen, en alle cijfers van de
schermen van SmartBatteryControl (vandaag, 7 dagen, 30 dagen en dit jaar), de zonverwachting en de omvormer.
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
| Geladen / Ontladen (vandaag, 7 dagen, 30 dagen, dit jaar) | kWh (vandaag en dit jaar bruikbaar in het Energie-dashboard) |
| Opbrengst, Rendement, Cycli (per periode) | wat de batterij opleverde (de waarde van wat hij ontlaadde min wat laden kostte), ontladen ÷ geladen, en volle cycli |
| Gemiddelde prijs bij laden / bij ontladen (per periode) | wat een kWh laden gemiddeld kostte, en wat een kWh ontladen toen waard was |
| Planning vandaag / morgen | de geplande blokken als tekst met wat elk blok met het laadniveau doet (bijv. 12% → 100%), en de uren laden en ontladen als attribuut |
| Laadkosten, opbrengst ontladen en netto (vandaag en deze maand) | wat laden werkelijk kostte en ontladen opleverde, in euro |
| Eindstand vandaag / morgen | de dag zoals onderaan het scherm Plan: gemeten tot nu, daarna volgens het plan, met de kWh, euro's en gemiddelde prijs per kWh van laden en ontladen als attribuut |
| Laatste slot per kWh | het laatste laad- of ontlaadslot dat klaar is: wat een kWh gemiddeld kostte of opleverde, met het doel (bijv. 48% → 98%), het resultaat en wat er gepland was |
| Resultaat gisteren | de dagcontrole van gisteren (plan tegen werkelijkheid), met of het plan gevolgd is en het rendement |
| Fase | op welke fase de batterij zit (L1, L2 of L3), automatisch herkend of zelf ingesteld |
| Laadbijstelling (zelflerend) | met hoeveel procent van het laadvermogen de planner rekent (100% = geen bijstelling), met de laatste laadblokken |
| Laatste push naar de batterij | wanneer het schema voor het laatst naar de batterij ging |
| Maximale ontlading tot / Maximaal laden tot | de laadgrenzen van de batterij in SmartBatteryControl (%) |
| Capaciteit, maximaal laad- en ontlaadvermogen | de instellingen van de batterij |
| Alle sensoren uit de batterij | spanning, stroom, temperaturen, cellen, energietellers, status en instellingen (zoals SmartBatteryControl ze uitleest) |

Per batterij ook een **agenda** (Planning) met elk laad- en ontlaadblok van vandaag en morgen: te zien in de agenda van
Home Assistant en te gebruiken als trigger in een automatisering.

En aan/uit-sensoren: bereikbaar, sturen, zelfconsumptie actief (met zelfconsumptie of nul op de fase als attribuut), volgt
schema niet, schema gelijk aan plan (gelijk aan wat er naar de batterij gestuurd is), laadgrens bereikt, nieuw plan wacht op
push, bijladen tot vol en begrensd in de app (een Marstek die in de app op 800 W staat).

Per stekker (Tapo of HomeWizard) een apparaat met het vermogen, het verbruik en de kosten van vandaag en deze maand, en de
gemiddelde prijs per kWh deze maand (met de kosten per dag als attribuut).

Per omvormer (of andere zonnebron) een apparaat met het vermogen, het totaal opgewekt (bruikbaar in het Energie-dashboard),
de status en alle sensoren die SmartBatteryControl uitleest, zoals spanning en stroom per fase en de temperatuur.

De sensoren uit de batterij komen uit de cache van SmartBatteryControl (die ze zelf op de achtergrond vers houdt):
Home Assistant kost de batterij dus geen extra verzoeken.

Voor SmartBatteryControl zelf: net (ook per fase L1-L3), zon, stroomprijs en terugleverprijs nu (met alle kwartierprijzen
van vandaag en morgen als attribuut), laagste en hoogste prijs van vandaag, prijsbron en strategie, en wanneer de volgende
planning en de volgende push naar de batterijen en de volgende controle is (als dat volgens schema gaat, met de tijden per
weekdag als attribuut), en het controle-interval. Verder:

| Sensor | Wat |
|---|---|
| Cijfers per periode | vandaag, 7 dagen, 30 dagen en dit jaar, zoals op de schermen Opbrengst, Historie en Verbruik (zie hieronder) |
| Alle batterijen vandaag / morgen | alle batterijen samen: gemeten tot nu, daarna volgens het plan, en per batterij als attribuut |
| Alle batterijen laden / ontladen per kWh vandaag / morgen | de gemiddelde prijs van alle laad- en ontlaadmomenten samen |
| Zonverwachting vandaag / morgen / rest van vandaag | kWh volgens de voorspeller die SmartBatteryControl gebruikt, met de typische dag en de kwartieren als attribuut |
| Zonverwachting per voorspeller, en de score | Open-Meteo, Solcast en Forecast.Solar (alleen die aan staan) en hoe goed ze de afgelopen dagen waren; de beste voorspeller |
| Zon tegen een gewoon jaar, afwijking zonverwachting (30 dagen) | wat het dak deed tegen een typisch jaar (PVGIS), en hoeveel de verwachting gemiddeld afweek |
| Zonnepiek vandaag, typisch zonnejaar | het hoogste vermogen van vandaag (met het tijdstip), en de kWh van een gewoon jaar |
| Verwacht verbruik vandaag / morgen | uit het geleerde verbruikspatroon, met de kwartieren als attribuut |
| Controles met een probleem | hoeveel controles een waarschuwing of fout geven, met welke; ook als aan/uit-sensor |
| Versie, nieuwste versie | met een aan/uit-sensor Update beschikbaar (installeren doe je in SmartBatteryControl) |

De cijfers per periode: huisverbruik, afname van en teruglevering aan het net, van het net naar het huis, batterijen geladen
en ontladen, van het net in de batterijen en van de batterijen naar het net, de opbrengst van de batterijen, netkosten
(inkoop min teruglevering), de stroomrekening (met alle regels, ook de vaste kosten, als attribuut), zelfvoorzienend, het
deel van het huis uit de batterijen, het rendement van de batterijen, de gemiddelde prijs van laden uit het net en van
ontladen met het prijsverschil daartussen, en de gemiddelde stroom- en terugleverprijs. Met zonnepanelen ook: opgewekt, zon
direct in huis, in de batterijen, teruggeleverd en niet meteen teruggeleverd, de zonopbrengst in euro, het deel van het
huis direct uit de zon en hoeveel van de zon je zelf gebruikt.

Vandaag en dit jaar staan aan. 7 dagen en 30 dagen staan klaar maar uit: zet ze aan bij **Instellingen → Apparaten en
diensten → Entiteiten**. Energie en euro's van vandaag en dit jaar tellen op vanaf middernacht en 1 januari, dus Home
Assistant houdt er statistieken van bij; 7 en 30 dagen schuiven elke dag op en hebben daarom geen statistieken.

Een oudere SmartBatteryControl zonder deze cijfers werkt gewoon: dan komen alleen de sensoren die hij kan leveren.

## Wat is SmartBatteryControl?

Software op een Raspberry Pi die thuisbatterijen (zoals de Marstek Venus E 3.0) lokaal aanstuurt op basis van de
dynamische stroomprijzen, het eigen verbruik en de zon. SmartBatteryControl zelf is geen onderdeel van deze repository.

---

## English

This integration brings a local SmartBatteryControl into Home Assistant: home batteries with all their sensors, today's
and tomorrow's plans (also as a calendar) with a full calculation, what charging cost and discharging earned, measured
round-trip efficiency, and electricity prices. It also has every figure from the SmartBatteryControl screens for today, 7
days, 30 days and this year (house consumption, grid import and export, battery earnings, self-sufficiency, the
electricity bill, average prices, solar production and value), the day total of all batteries with the average price per
kWh, the last slot per battery, the solar forecast per forecaster, the expected consumption, the health checks and updates, and each inverter as a device with all its sensors. Today and this year are enabled; 7 and 30 days
are available but disabled. Everything stays on your own network.

Install through HACS as a custom repository, restart Home Assistant, create a read-only API token in SmartBatteryControl
(Settings → Security → API tokens) and add the integration with the address, port and token. To change the address,
port or token later, choose **⋮ → Reconfigure** on the integration (leave the token empty to keep the current one).

## Licentie / License

MIT
