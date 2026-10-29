"""Constanten voor de SmartBatteryControl-integratie."""
from datetime import timedelta

DOMAIN = "smartbatterycontrol"
DEFAULT_PORT = 80
STATE_INTERVAL = timedelta(seconds=15)  # batterijen, meter, zon, prijzen
DETAIL_INTERVAL = timedelta(minutes=5)  # doorrekening van de plannen, kosten en opbrengst, kerncijfers van vandaag
SENSOR_INTERVAL = timedelta(minutes=1)  # alle sensoren van de batterijen (uit de cache van SmartBatteryControl)
MANUFACTURER = "Marstek"
