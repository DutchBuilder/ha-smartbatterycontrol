"""Constanten voor de SmartBatteryControl-integratie."""
from datetime import timedelta

DOMAIN = "smartbatterycontrol"
PREFIX = "sbc_"  # elke entity-id begint hiermee: sensor.sbc_venus_thuis_laadniveau
DEFAULT_PORT = 80
STATE_INTERVAL = timedelta(seconds=15)  # batterijen, meter, zon, prijzen
DETAIL_INTERVAL = timedelta(minutes=5)  # doorrekening van de plannen, kosten en opbrengst, kerncijfers van vandaag
SENSOR_INTERVAL = timedelta(minutes=1)  # alle sensoren van de batterijen (uit de cache van SmartBatteryControl)
PV_INTERVAL = timedelta(minutes=15)  # het zonmodel: verandert langzaam, en kost de Pi het meeste rekenwerk
MANUFACTURER = "Marstek"
