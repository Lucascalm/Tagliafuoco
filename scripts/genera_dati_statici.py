"""Generatore dei dati statici della Calabria usati da `domain/registry.py`.

PERCHE' ESISTE
Basi, mezzi, punti d'acqua, comuni e aree protette non cambiano da una finestra
di pianificazione all'altra: sono l'ossatura fissa del problema. Tenerli in
cinque JSON versionati, prodotti da un unico script leggibile, ha tre vantaggi
rispetto a scriverli a mano dentro i moduli Python:

1. il registro (`domain/registry.py`) resta l'unico punto che conosce il
   formato dei file, e nessun altro modulo si porta dentro numeri cablati;
2. le scelte e le approssimazioni stanno scritte accanto ai dati, in italiano,
   e si possono discutere;
3. rigenerare e' deterministico: nessun `random`, nessuna chiamata di rete,
   nessuna dipendenza dall'orologio. Due esecuzioni producono byte identici.

ONESTA' SUI DATI (si legga prima di usarli per qualsiasi cosa)
Questi NON sono l'inventario reale del servizio antincendio boschivo della
Regione Calabria, ne' della flotta dello Stato, ne' del Corpo nazionale dei
vigili del fuoco. Sono dati PLAUSIBILI A SCOPO DI SIMULAZIONE:

- le basi sono luoghi veri (aeroporti, elisuperfici, comandi e distaccamenti
  VVF) con coordinate reali, ma il fatto che una certa base ospiti davvero un
  certo mezzo in una certa stagione non e' verificato;
- la composizione della flotta imita per tipologie e ordini di grandezza il
  dispositivo aereo nazionale e regionale, ma targhe, numeri e dislocazioni
  sono inventati;
- i parametri prestazionali (velocita', capacita', autonomia, costo orario)
  sono valori di manuale arrotondati, non dati certificati del costruttore;
- popolazione e altitudine dei comuni sono cifre ISTAT arrotondate alle
  centinaia e quote del capoluogo comunale, non superfici abitate;
- le aree protette sono approssimate a cerchi (vedere sotto).

FONTI DI RIFERIMENTO (consultate come ordine di grandezza, non copiate)
- coordinate di aeroporti ed elisuperfici: dati aeronautici pubblici;
- perimetri e superfici dei parchi: Elenco Ufficiale delle Aree Protette;
- popolazione e quote dei comuni: censimento e anagrafe ISTAT;
- prestazioni dei mezzi: schede tecniche pubbliche dei costruttori
  (Canadair CL-415, Erickson S-64, AW139, AB-412, AS-350).

APPROSSIMAZIONI DICHIARATE
- Geometria delle aree protette: un cerchio (lat, lon, raggio_km) al posto del
  poligono. Il raggio e' quello del cerchio di pari area della superficie
  ufficiale. Per parchi allungati come Sila e Pollino il cerchio sbaglia sia
  per eccesso sia per difetto ai bordi: al sistema serve rispondere "questo
  focolaio e' in area tutelata?" per pesare la priorita', non calcolare
  indennizzi catastali.
- Fiumi: non ce ne sono fra i punti d'acqua. Le fiumare calabresi d'estate
  sono in secca proprio quando servirebbero, quindi come punto di pescaggio
  non valgono nulla. I corsi d'acqua utili sono invasi, e come invasi sono
  censiti.
- `capacity_liters` non e' il volume del bacino: e' il volume che si puo'
  prelevare in una giornata operativa senza compromettere l'uso primario
  (potabile, irriguo, idroelettrico). Per il mare e per i grandi laghi silani
  e' praticamente illimitato, e vale `null`.

COSA GARANTISCE LA SCRITTURA
- si valida tutto PRIMA di scrivere qualsiasi cosa: se un record e' incoerente
  non viene toccato un solo file, e `data/static/` non resta mai in uno stato
  misto con due file nuovi e tre vecchi che non si parlano piu';
- ogni file passa da un file di appoggio e da un rename atomico, quindi
  un'interruzione a meta' lascia il file vecchio intero invece di un JSON
  troncato, che a valle si manifesterebbe come un errore di parsing oscuro
  dentro `registry.py`;
- l'output e' riproducibile byte per byte: nessun `random`, nessun timestamp,
  nessuna iterazione su insiemi o su dizionari costruiti dinamicamente.

USO
    .venv/Scripts/python.exe scripts/genera_dati_statici.py
    .venv/Scripts/python.exe scripts/genera_dati_statici.py --check

Con `--check` non scrive niente: valida le costanti e riporta i conteggi.
Utile in una verifica rapida prima di committare.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tagliafuoco.config import CALABRIA_BBOX, PROVINCE, STATIC_DIR  # noqa: E402
from tagliafuoco.domain.models import (  # noqa: E402
    Domain,
    VehicleKind,
    VehicleStatus,
    WaterSourceKind,
)

# --------------------------------------------------------------------------
# Scorciatoie di leggibilita'
# --------------------------------------------------------------------------

CANADAIR = VehicleKind.CANADAIR.value
ELI_PESANTE = VehicleKind.ELICOTTERO_PESANTE.value
ELI_MEDIO = VehicleKind.ELICOTTERO_MEDIO.value
ELI_LEGGERO = VehicleKind.ELICOTTERO_LEGGERO.value
AUTOBOTTE = VehicleKind.AUTOBOTTE.value
MODULO_AIB = VehicleKind.MODULO_AIB.value
SQUADRA_TERRA = VehicleKind.SQUADRA_TERRA.value

TUTTI_AEREI = [CANADAIR, ELI_PESANTE, ELI_MEDIO, ELI_LEGGERO]
SOLO_ELICOTTERI = [ELI_PESANTE, ELI_MEDIO, ELI_LEGGERO]
SOLO_TERRA = [AUTOBOTTE, MODULO_AIB]

# Box approssimati per provincia, usati SOLO come rete di sicurezza contro gli
# errori di battitura nelle coordinate: se scrivo Crotone a lon 16.1 il
# controllo se ne accorge. Non sono confini amministrativi, sono rettangoli
# generosi che si sovrappongono fra loro.
PROVINCE_APPROX_BOX = {
    # provincia: (lat_min, lat_max, lon_min, lon_max)
    "Cosenza": (39.05, 40.20, 15.60, 17.05),
    "Catanzaro": (38.45, 39.12, 15.95, 16.95),
    "Crotone": (38.90, 39.45, 16.55, 17.25),
    "Vibo Valentia": (38.45, 38.82, 15.82, 16.45),
    "Reggio Calabria": (37.88, 38.56, 15.58, 16.62),
}


# --------------------------------------------------------------------------
# 1. Basi operative
# --------------------------------------------------------------------------
# Tre livelli, che nella realta' hanno ruoli diversi e qui hanno parametri
# diversi:
#   - aeroporti: le uniche piste da cui possono operare i Canadair, dotate di
#     assistenza al volo notturno (`has_night_capability`), rifornimento piu'
#     lento perche' la macchina e' grande (turnaround 14-15 minuti);
#   - elisuperfici del servizio antincendio regionale, distribuite sulle aree
#     boscate che bruciano davvero (Pollino, Sila, Serre, Aspromonte, alto
#     ionio crotonese): riarmo rapido, niente volo notturno;
#   - sedi VVF: da qui partono autobotti, moduli AIB e squadre a terra, che
#     lavorano anche di notte e quindi la notte la sede la supporta.
#
# `turnaround_minutes` e' il tempo a terra fra un rientro e la ripartenza
# (rifornimento carburante, cambio equipaggio, controlli). E' una taratura.

BASI: list[dict[str, Any]] = [
    {
        "base_id": "BASE_LMZ",
        "name": "Aeroporto di Lamezia Terme",
        "lat": 38.9054,
        "lon": 16.2423,
        "provincia": "Catanzaro",
        "supports": [CANADAIR, ELI_PESANTE, ELI_MEDIO, ELI_LEGGERO],
        "has_night_capability": True,
        "turnaround_minutes": 15.0,
        "note": "Scalo principale della regione, baricentrico fra Tirreno e Ionio.",
    },
    {
        "base_id": "BASE_REG",
        "name": "Aeroporto di Reggio Calabria Tito Minniti",
        "lat": 38.0712,
        "lon": 15.6516,
        "provincia": "Reggio Calabria",
        "supports": [CANADAIR, ELI_MEDIO, ELI_LEGGERO],
        "has_night_capability": True,
        "turnaround_minutes": 14.0,
        "note": "Copre Aspromonte e Stretto, pista corta stretta fra mare e collina.",
    },
    {
        "base_id": "BASE_KRO",
        "name": "Aeroporto di Crotone Pitagora",
        "lat": 38.9972,
        "lon": 17.0802,
        "provincia": "Crotone",
        "supports": [CANADAIR, ELI_PESANTE, ELI_MEDIO],
        "has_night_capability": True,
        "turnaround_minutes": 14.0,
        "note": "Unico scalo sul versante ionico, utile per la Sila orientale.",
    },
    {
        "base_id": "BASE_ELI_MOR",
        "name": "Elisuperficie AIB di Mormanno",
        "lat": 39.8880,
        "lon": 15.9930,
        "provincia": "Cosenza",
        "supports": [ELI_MEDIO, ELI_LEGGERO],
        "has_night_capability": False,
        "turnaround_minutes": 10.0,
        "note": "Presidio del versante calabrese del Pollino.",
    },
    {
        "base_id": "BASE_ELI_LOR",
        "name": "Elisuperficie AIB di Lorica",
        "lat": 39.2606,
        "lon": 16.5397,
        "provincia": "Cosenza",
        "supports": [ELI_MEDIO, ELI_LEGGERO, MODULO_AIB],
        "has_night_capability": False,
        "turnaround_minutes": 10.0,
        "note": "Cuore della Sila Grande, a pochi minuti dai laghi Arvo e Cecita.",
    },
    {
        "base_id": "BASE_ELI_CEN",
        "name": "Elisuperficie AIB di Cenadi",
        "lat": 38.7817,
        "lon": 16.4300,
        "provincia": "Catanzaro",
        "supports": [ELI_MEDIO, ELI_LEGGERO],
        "has_night_capability": False,
        "turnaround_minutes": 10.0,
        "note": "Presidio dell'istmo e del versante nord delle Serre.",
    },
    {
        "base_id": "BASE_ELI_GAM",
        "name": "Elisuperficie AIB di Gambarie d'Aspromonte",
        "lat": 38.1675,
        "lon": 15.8375,
        "provincia": "Reggio Calabria",
        "supports": [ELI_MEDIO, ELI_LEGGERO, MODULO_AIB],
        "has_night_capability": False,
        "turnaround_minutes": 11.0,
        "note": "Quota 1300 m: densita' dell'aria ridotta, carico utile ridotto.",
    },
    {
        "base_id": "BASE_ELI_CIR",
        "name": "Elisuperficie AIB di Ciro' Marina",
        "lat": 39.3705,
        "lon": 17.1290,
        "provincia": "Crotone",
        "supports": [ELI_MEDIO, ELI_LEGGERO],
        "has_night_capability": False,
        "turnaround_minutes": 10.0,
        "note": "Alto ionio crotonese, macchia mediterranea e interfaccia costiera.",
    },
    {
        "base_id": "BASE_VVF_CS",
        "name": "Comando VVF di Cosenza",
        "lat": 39.3125,
        "lon": 16.2472,
        "provincia": "Cosenza",
        "supports": [AUTOBOTTE, MODULO_AIB, SQUADRA_TERRA],
        "has_night_capability": True,
        "turnaround_minutes": 8.0,
        "note": "Valle del Crati, la provincia piu' estesa e piu' boscata.",
    },
    {
        "base_id": "BASE_VVF_CZ",
        "name": "Comando VVF di Catanzaro",
        "lat": 38.9052,
        "lon": 16.5877,
        "provincia": "Catanzaro",
        "supports": [AUTOBOTTE, MODULO_AIB, SQUADRA_TERRA],
        "has_night_capability": True,
        "turnaround_minutes": 8.0,
        "note": "Copre Sila Piccola e golfo di Squillace.",
    },
    {
        "base_id": "BASE_VVF_RC",
        "name": "Comando VVF di Reggio Calabria",
        "lat": 38.1130,
        "lon": 15.6540,
        "provincia": "Reggio Calabria",
        "supports": [AUTOBOTTE, MODULO_AIB, SQUADRA_TERRA],
        "has_night_capability": True,
        "turnaround_minutes": 8.0,
        "note": "Interfaccia urbano-foresta fra le piu' critiche d'Italia.",
    },
    {
        "base_id": "BASE_VVF_VV",
        "name": "Comando VVF di Vibo Valentia",
        "lat": 38.6760,
        "lon": 16.1020,
        "provincia": "Vibo Valentia",
        "supports": [AUTOBOTTE, MODULO_AIB, SQUADRA_TERRA],
        "has_night_capability": True,
        "turnaround_minutes": 8.0,
        "note": "Serre e costa degli dei, forte pressione turistica estiva.",
    },
    {
        "base_id": "BASE_VVF_KR",
        "name": "Comando VVF di Crotone",
        "lat": 39.0790,
        "lon": 17.1180,
        "provincia": "Crotone",
        "supports": [AUTOBOTTE, MODULO_AIB, SQUADRA_TERRA],
        "has_night_capability": True,
        "turnaround_minutes": 8.0,
        "note": "Marchesato: poca acqua, molto vento, molto incolto.",
    },
    {
        "base_id": "BASE_VVF_CAS",
        "name": "Distaccamento VVF di Castrovillari",
        "lat": 39.8130,
        "lon": 16.2010,
        "provincia": "Cosenza",
        "supports": [AUTOBOTTE, MODULO_AIB, SQUADRA_TERRA],
        "has_night_capability": True,
        "turnaround_minutes": 9.0,
        "note": "Unico presidio terrestre rapido per il versante sud del Pollino.",
    },
]


# --------------------------------------------------------------------------
# 2. Flotta
# --------------------------------------------------------------------------
# I profili sono per MODELLO: due Canadair hanno le stesse prestazioni, cambia
# solo dove stanno di casa. Separare profilo e dislocazione evita di ripetere
# quindici numeri per ogni macchina e rende evidente che una eventuale
# ritaratura vale per tutta la classe.
#
# Nota sui campi temporali, perche' non sono intercambiabili:
#   setup_minutes   tempo dall'ordine al decollo (avviamento, rullaggio) o,
#                   a terra, il tempo di vestizione e partenza della squadra;
#   drop_minutes    durata di un singolo lancio o di un singolo attacco;
#   refill_minutes  durata del rifornimento d'acqua (per il Canadair e' la
#                   manovra completa di scooping, non i dodici secondi di
#                   contatto con l'acqua);
#   endurance_minutes  autonomia di una singola missione senza rientro per
#                   carburante: e' il vincolo che spezza le missioni lunghe;
#   max_shift_minutes  limite di impiego nella giornata, dettato dai turni
#                   dell'equipaggio piu' che dalla macchina.
#
# Gli ordini di grandezza sono quelli veri: un CL-415 vola a circa 333 km/h e
# porta 6137 litri ma non sta in aria otto ore; un pick-up con modulo AIB sta
# fuori tutto il giorno ma viaggia a 55 km/h su strade di montagna e porta
# quattro ettolitri.

PROFILI_MEZZI: dict[str, dict[str, Any]] = {
    "CL415": {
        "kind": CANADAIR,
        "domain": Domain.AIR.value,
        "cruise_speed_kmh": 333.0,
        "water_capacity_liters": 6137,
        "drop_minutes": 0.5,
        "refill_minutes": 1.5,
        "setup_minutes": 12.0,
        "endurance_minutes": 240.0,
        "max_shift_minutes": 480.0,
        "night_capable": False,
        "cost_per_hour_eur": 13000.0,
        "max_wind_kmh": 65.0,
        "requires_water_source": True,
    },
    "S64": {
        # Erickson S-64 Air Crane: serbatoio ventrale, si riempie in hovering
        # anche da specchi d'acqua piccoli dove un Canadair non ammara.
        "kind": ELI_PESANTE,
        "domain": Domain.AIR.value,
        "cruise_speed_kmh": 185.0,
        "water_capacity_liters": 9000,
        "drop_minutes": 1.0,
        "refill_minutes": 2.5,
        "setup_minutes": 10.0,
        "endurance_minutes": 150.0,
        "max_shift_minutes": 420.0,
        "night_capable": False,
        "cost_per_hour_eur": 9000.0,
        "max_wind_kmh": 60.0,
        "requires_water_source": True,
    },
    "AW139": {
        "kind": ELI_MEDIO,
        "domain": Domain.AIR.value,
        "cruise_speed_kmh": 260.0,
        "water_capacity_liters": 1500,
        "drop_minutes": 0.8,
        "refill_minutes": 2.0,
        "setup_minutes": 6.0,
        "endurance_minutes": 150.0,
        "max_shift_minutes": 450.0,
        "night_capable": False,
        "cost_per_hour_eur": 3400.0,
        "max_wind_kmh": 55.0,
        "requires_water_source": True,
    },
    "AB412": {
        "kind": ELI_MEDIO,
        "domain": Domain.AIR.value,
        "cruise_speed_kmh": 225.0,
        "water_capacity_liters": 1500,
        "drop_minutes": 0.8,
        "refill_minutes": 2.0,
        "setup_minutes": 6.0,
        "endurance_minutes": 140.0,
        "max_shift_minutes": 450.0,
        "night_capable": False,
        "cost_per_hour_eur": 3000.0,
        "max_wind_kmh": 55.0,
        "requires_water_source": True,
    },
    "AS350": {
        "kind": ELI_LEGGERO,
        "domain": Domain.AIR.value,
        "cruise_speed_kmh": 220.0,
        "water_capacity_liters": 800,
        "drop_minutes": 0.7,
        "refill_minutes": 1.8,
        "setup_minutes": 5.0,
        "endurance_minutes": 130.0,
        "max_shift_minutes": 420.0,
        "night_capable": False,
        "cost_per_hour_eur": 1500.0,
        "max_wind_kmh": 50.0,
        "requires_water_source": True,
    },
    "PICKUP_AIB": {
        # Pick-up 4x4 con modulo antincendio: vasca, motopompa, naspo.
        # Il tempo di rifornimento e' lungo perche' si riempie da idrante o da
        # vasca, non in volo.
        "kind": MODULO_AIB,
        "domain": Domain.GROUND.value,
        "cruise_speed_kmh": 55.0,
        "water_capacity_liters": 400,
        "drop_minutes": 6.0,
        "refill_minutes": 15.0,
        "setup_minutes": 8.0,
        "endurance_minutes": 480.0,
        "max_shift_minutes": 600.0,
        "night_capable": True,
        "cost_per_hour_eur": 180.0,
        "max_wind_kmh": 90.0,
        "requires_water_source": True,
    },
    "PICKUP_AIB_LEGGERO": {
        "kind": MODULO_AIB,
        "domain": Domain.GROUND.value,
        "cruise_speed_kmh": 60.0,
        "water_capacity_liters": 300,
        "drop_minutes": 5.0,
        "refill_minutes": 12.0,
        "setup_minutes": 8.0,
        "endurance_minutes": 480.0,
        "max_shift_minutes": 600.0,
        "night_capable": True,
        "cost_per_hour_eur": 160.0,
        "max_wind_kmh": 90.0,
        "requires_water_source": True,
    },
    "APS_VVF": {
        # Autopompa serbatoio / autobotte: tanta acqua, poca agilita'.
        # 50 km/h di media e' gia' ottimistico sulle provinciali silane.
        "kind": AUTOBOTTE,
        "domain": Domain.GROUND.value,
        "cruise_speed_kmh": 50.0,
        "water_capacity_liters": 3000,
        "drop_minutes": 8.0,
        "refill_minutes": 20.0,
        "setup_minutes": 10.0,
        "endurance_minutes": 600.0,
        "max_shift_minutes": 720.0,
        "night_capable": True,
        "cost_per_hour_eur": 320.0,
        "max_wind_kmh": 90.0,
        "requires_water_source": True,
    },
    "ABP_VVF": {
        "kind": AUTOBOTTE,
        "domain": Domain.GROUND.value,
        "cruise_speed_kmh": 48.0,
        "water_capacity_liters": 4000,
        "drop_minutes": 9.0,
        "refill_minutes": 22.0,
        "setup_minutes": 10.0,
        "endurance_minutes": 600.0,
        "max_shift_minutes": 720.0,
        "night_capable": True,
        "cost_per_hour_eur": 360.0,
        "max_wind_kmh": 90.0,
        "requires_water_source": True,
    },
    "SQUADRA": {
        # Squadra a terra: taglio, controfuoco, bonifica. Non porta acqua
        # propria oltre a zaini e naspi, e infatti `requires_water_source` e'
        # falso: non deve andare a pescare da nessuna parte per lavorare.
        # I 200 litri non sono un serbatoio: sono la somma degli estintori
        # spalleggiati. Sono tenuti sopra lo zero di proposito, perche' un
        # valore nullo diventa una divisione per zero in qualunque modulo che
        # calcoli "quante sortite servono per coprire il fabbisogno".
        "kind": SQUADRA_TERRA,
        "domain": Domain.GROUND.value,
        "cruise_speed_kmh": 45.0,
        "water_capacity_liters": 200,
        "drop_minutes": 12.0,
        "refill_minutes": 30.0,
        "setup_minutes": 10.0,
        "endurance_minutes": 600.0,
        "max_shift_minutes": 720.0,
        "night_capable": True,
        "cost_per_hour_eur": 95.0,
        "max_wind_kmh": 100.0,
        "requires_water_source": False,
    },
}

# (vehicle_id, nome leggibile, profilo, base di appartenenza)
DISLOCAZIONE_MEZZI: list[tuple[str, str, str, str]] = [
    # Componente pesante: i due Canadair stanno a Lamezia perche' e' l'unico
    # scalo baricentrico e perche' il Golfo di Sant'Eufemia e' a cinque minuti
    # di volo, quindi il ciclo di pescaggio e' corto da qualunque parte si vada.
    ("CAN_01", "Canadair CL-415 01", "CL415", "BASE_LMZ"),
    ("CAN_02", "Canadair CL-415 02", "CL415", "BASE_LMZ"),
    ("HEA_01", "Erickson S-64 Sila", "S64", "BASE_LMZ"),
    ("HEA_02", "Erickson S-64 Marchesato", "S64", "BASE_KRO"),
    # Componente media: una macchina per ciascuna area boscata importante.
    ("MED_01", "AW139 Pollino", "AW139", "BASE_ELI_MOR"),
    ("MED_02", "AW139 Sila Grande", "AW139", "BASE_ELI_LOR"),
    ("MED_03", "AB-412 Serre", "AB412", "BASE_ELI_CEN"),
    ("MED_04", "AB-412 Aspromonte", "AB412", "BASE_ELI_GAM"),
    ("MED_05", "AB-412 Alto Ionio", "AB412", "BASE_ELI_CIR"),
    ("MED_06", "AW139 Lamezia", "AW139", "BASE_LMZ"),
    # Componente leggera: ricognizione e primo attacco sui focolai piccoli.
    ("LGT_01", "AS-350 Stretto", "AS350", "BASE_REG"),
    ("LGT_02", "AS-350 Pollino", "AS350", "BASE_ELI_MOR"),
    ("LGT_03", "AS-350 Istmo", "AS350", "BASE_ELI_CEN"),
    # Moduli AIB: uno per sede provinciale piu' il distaccamento del Pollino.
    ("AIB_01", "Modulo AIB Cosenza", "PICKUP_AIB", "BASE_VVF_CS"),
    ("AIB_02", "Modulo AIB Castrovillari", "PICKUP_AIB", "BASE_VVF_CAS"),
    ("AIB_03", "Modulo AIB Catanzaro", "PICKUP_AIB", "BASE_VVF_CZ"),
    ("AIB_04", "Modulo AIB Reggio Calabria", "PICKUP_AIB", "BASE_VVF_RC"),
    ("AIB_05", "Modulo AIB Vibo Valentia", "PICKUP_AIB_LEGGERO", "BASE_VVF_VV"),
    ("AIB_06", "Modulo AIB Crotone", "PICKUP_AIB_LEGGERO", "BASE_VVF_KR"),
    # Autobotti: le due maggiori sulle province con piu' interfaccia.
    ("ABT_01", "Autobotte VVF Cosenza", "ABP_VVF", "BASE_VVF_CS"),
    ("ABT_02", "Autobotte VVF Catanzaro", "APS_VVF", "BASE_VVF_CZ"),
    ("ABT_03", "Autobotte VVF Reggio Calabria", "ABP_VVF", "BASE_VVF_RC"),
    ("ABT_04", "Autobotte VVF Crotone", "APS_VVF", "BASE_VVF_KR"),
    # Squadre a terra: bonifica e taglio, il lavoro che tiene spento un
    # incendio dopo che gli aerei hanno smesso di volare.
    ("SQT_01", "Squadra a terra Cosenza", "SQUADRA", "BASE_VVF_CS"),
    ("SQT_02", "Squadra a terra Catanzaro", "SQUADRA", "BASE_VVF_CZ"),
    ("SQT_03", "Squadra a terra Reggio Calabria", "SQUADRA", "BASE_VVF_RC"),
]


# --------------------------------------------------------------------------
# 3. Punti d'acqua
# --------------------------------------------------------------------------
# `seasonal_reliability` e' un numero fra 0 e 1 che risponde a: in piena
# stagione di incendi (luglio-agosto) quanto posso contare su questo punto?
#
# Il mare vale 1.0 e non si discute. I laghi silani NO, e la ragione e'
# strutturale: Cecita, Arvo, Ampollino, Passante e Ariamacina non sono laghi
# naturali, sono invasi costruiti fra gli anni venti e gli anni cinquanta per
# produrre energia elettrica. D'estate l'ente gestore li svasa, il livello cala
# di metri, le rive si allontanano e le piazzole di pescaggio si trovano
# all'asciutto. Chi pianifica contando su un lago silano a ferragosto come se
# fosse il mare sta pianificando su un dato del mese di aprile. I valori qui
# sotto (0.60-0.85) sono una taratura dichiarata, non una misura: servono a
# far si' che un pianificatore prudente preferisca il mare quando la distanza
# e' comparabile.
#
# `usable_by` e' la parte che conta di piu' per il solver. Un CL-415 ammara e
# imbarca in corsa: gli servono circa 1300 metri di specchio libero e due
# metri di pescaggio, quindi puo' usare il mare e i tre invasi silani grandi
# (Cecita, Arvo, Ampollino), ma non un invaso stretto di montagna e men che
# meno una vasca antincendio. Un elicottero con benna o serbatoio ventrale
# si riempie in hovering e va bene quasi ovunque. I mezzi terrestri usano
# idranti e vasche e non toccano mai il mare.
#
# Due cose che questo elenco NON rappresenta, e vanno sapute prima di leggere
# i risultati:
#   - la rete di idranti vera e' molto piu' fitta: qui ce ne sono tre, presso
#     altrettante sedi VVF. E' un sottocampionamento voluto, per tenere il file
#     leggibile, e ha un effetto sistematico: i tempi di rifornimento dei mezzi
#     terrestri risultano PESSIMISTICI. Va tenuto presente quando si confronta
#     la componente terrestre con quella aerea.
#   - i punti di tipo `sea` sono CAMPIONI di una costa continua, non i soli
#     posti dove si puo' pescare. La Calabria ha circa 780 km di costa e un
#     mezzo aereo ammara o cala la benna dove gli conviene. Sette punti lasciano
#     buchi fino a una trentina di chilometri, e su quel tratto il ciclo
#     d'acqua calcolato risulta piu' lungo del vero. Chi scrive `optimize/` e
#     `validate/` puo' trattare questi punti come campioni e, se serve piu'
#     fedelta', proiettare il focolaio sulla costa piu' vicina.
#   - le squadre a terra non compaiono in nessun `usable_by`, ed e' corretto:
#     hanno `requires_water_source` falso perche' lavorano di taglio e bonifica.
#     Legarle a un punto di pescaggio significherebbe farle competere per una
#     risorsa che non consumano.

SORGENTI_ACQUA: list[dict[str, Any]] = [
    # --- Tirreno ---
    {
        "source_id": "WS_TIR_SCALEA",
        "name": "Tirreno al largo di Scalea",
        "lat": 39.8000,
        "lon": 15.7300,
        "kind": WaterSourceKind.SEA.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 1.5,
        "capacity_liters": None,
        "seasonal_reliability": 1.0,
        "note": "Riviera dei Cedri, copre il versante tirrenico del Pollino.",
    },
    {
        "source_id": "WS_TIR_SEUFEMIA",
        "name": "Golfo di Sant'Eufemia",
        "lat": 38.9200,
        "lon": 16.1500,
        "kind": WaterSourceKind.SEA.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 1.5,
        "capacity_liters": None,
        "seasonal_reliability": 1.0,
        "note": "Otto km da Lamezia, un minuto e mezzo di volo per un CL-415: "
        "ciclo d'acqua completo sotto i cinque minuti.",
    },
    {
        "source_id": "WS_TIR_SCILLA",
        "name": "Stretto di Messina al largo di Scilla",
        "lat": 38.2600,
        "lon": 15.6800,
        "kind": WaterSourceKind.SEA.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 2.0,
        "capacity_liters": None,
        "seasonal_reliability": 1.0,
        "note": "Correnti e traffico navale: pescaggio meno comodo, 2 minuti.",
    },
    # --- Ionio ---
    {
        "source_id": "WS_ION_SIBARI",
        "name": "Golfo di Corigliano, foce del Crati",
        "lat": 39.7200,
        "lon": 16.5300,
        "kind": WaterSourceKind.SEA.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 1.5,
        "capacity_liters": None,
        "seasonal_reliability": 1.0,
        "note": "Serve la piana di Sibari e la Sila Greca.",
    },
    {
        "source_id": "WS_ION_CROTONE",
        "name": "Ionio al largo di Crotone",
        "lat": 39.0700,
        "lon": 17.1600,
        "kind": WaterSourceKind.SEA.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 1.5,
        "capacity_liters": None,
        "seasonal_reliability": 1.0,
        "note": "Marchesato, dove d'acqua dolce non ce n'e'.",
    },
    {
        "source_id": "WS_ION_SQUILLACE",
        "name": "Golfo di Squillace",
        "lat": 38.7400,
        "lon": 16.6300,
        "kind": WaterSourceKind.SEA.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 1.5,
        "capacity_liters": None,
        "seasonal_reliability": 1.0,
        "note": "Versante ionico dell'istmo e Sila Piccola meridionale.",
    },
    {
        "source_id": "WS_ION_LOCRI",
        "name": "Ionio al largo di Locri",
        "lat": 38.2300,
        "lon": 16.2900,
        "kind": WaterSourceKind.SEA.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 1.5,
        "capacity_liters": None,
        "seasonal_reliability": 1.0,
        "note": "Locride e versante ionico dell'Aspromonte.",
    },
    # --- Laghi e invasi silani ---
    {
        "source_id": "WS_LAG_CECITA",
        "name": "Lago di Cecita",
        "lat": 39.4000,
        "lon": 16.5200,
        "kind": WaterSourceKind.LAKE.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 2.0,
        "capacity_liters": None,
        "seasonal_reliability": 0.75,
        "note": "Il piu' grande dei silani, abbastanza lungo per un CL-415.",
    },
    {
        "source_id": "WS_LAG_ARVO",
        "name": "Lago Arvo",
        "lat": 39.2200,
        "lon": 16.4800,
        "kind": WaterSourceKind.LAKE.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 2.0,
        "capacity_liters": None,
        "seasonal_reliability": 0.80,
        "note": "A Lorica, accanto all'elisuperficie: ciclo di pescaggio cortissimo.",
    },
    {
        "source_id": "WS_LAG_AMPOLLINO",
        "name": "Lago Ampollino",
        "lat": 39.1400,
        "lon": 16.6400,
        "kind": WaterSourceKind.LAKE.value,
        "usable_by": TUTTI_AEREI,
        "refill_minutes": 2.0,
        "capacity_liters": None,
        "seasonal_reliability": 0.75,
        "note": "Al confine fra le tre province silane, pinete tutto intorno.",
    },
    {
        "source_id": "WS_LAG_PASSANTE",
        "name": "Lago Passante",
        "lat": 39.0700,
        "lon": 16.5400,
        "kind": WaterSourceKind.LAKE.value,
        "usable_by": SOLO_ELICOTTERI,
        "refill_minutes": 2.5,
        "capacity_liters": 5_000_000,
        "seasonal_reliability": 0.70,
        "note": "Stretto e incassato: benne si', ammaraggio no.",
    },
    {
        "source_id": "WS_LAG_ARIAMACINA",
        "name": "Lago Ariamacina",
        "lat": 39.3000,
        "lon": 16.5500,
        "kind": WaterSourceKind.LAKE.value,
        "usable_by": SOLO_ELICOTTERI,
        "refill_minutes": 2.5,
        "capacity_liters": 3_000_000,
        "seasonal_reliability": 0.65,
        "note": "Piccolo e molto soggetto a svaso, il meno affidabile dei silani.",
    },
    {
        "source_id": "WS_LAG_ANGITOLA",
        "name": "Lago Angitola",
        "lat": 38.7400,
        "lon": 16.2100,
        "kind": WaterSourceKind.LAKE.value,
        "usable_by": SOLO_ELICOTTERI,
        "refill_minutes": 2.5,
        "capacity_liters": 4_000_000,
        "seasonal_reliability": 0.60,
        "note": "Oasi protetta: prelievo tollerato ma non illimitato.",
    },
    {
        "source_id": "WS_INV_MENTA",
        "name": "Invaso del Menta",
        "lat": 38.1500,
        "lon": 15.9200,
        "kind": WaterSourceKind.LAKE.value,
        "usable_by": SOLO_ELICOTTERI,
        "refill_minutes": 2.5,
        "capacity_liters": 8_000_000,
        "seasonal_reliability": 0.85,
        "note": "Quota 1400 m in Aspromonte: uso potabile, quindi ben alimentato.",
    },
    # --- Punti per i mezzi terrestri ---
    {
        "source_id": "WS_IDR_CASTROVILLARI",
        "name": "Idrante VVF di Castrovillari",
        "lat": 39.8130,
        "lon": 16.2050,
        "kind": WaterSourceKind.HYDRANT.value,
        "usable_by": SOLO_TERRA,
        "refill_minutes": 15.0,
        "capacity_liters": 250_000,
        "seasonal_reliability": 0.95,
        "note": "Rete acquedottistica: cala un po' nei picchi di consumo estivi.",
    },
    {
        "source_id": "WS_IDR_LAMEZIA",
        "name": "Idrante VVF di Lamezia Terme",
        "lat": 38.9400,
        "lon": 16.2700,
        "kind": WaterSourceKind.HYDRANT.value,
        "usable_by": SOLO_TERRA,
        "refill_minutes": 15.0,
        "capacity_liters": 250_000,
        "seasonal_reliability": 0.95,
        "note": "Punto di rifornimento baricentrico per i mezzi terrestri.",
    },
    {
        "source_id": "WS_IDR_CROTONE",
        "name": "Idrante VVF di Crotone",
        "lat": 39.0790,
        "lon": 17.1180,
        "kind": WaterSourceKind.HYDRANT.value,
        "usable_by": SOLO_TERRA,
        "refill_minutes": 15.0,
        "capacity_liters": 200_000,
        "seasonal_reliability": 0.90,
        "note": "Senza questo punto il Marchesato resterebbe a oltre un'ora di "
        "strada dal rifornimento piu' vicino.",
    },
    {
        "source_id": "WS_VAS_GAMBARIE",
        "name": "Vasca antincendio di Gambarie",
        "lat": 38.1650,
        "lon": 15.8380,
        "kind": WaterSourceKind.TANK.value,
        # L'elicottero pesante e' escluso di proposito, ed e' l'unica eccezione
        # alla regola "gli elicotteri pescano quasi ovunque": una benna da 9000
        # litri svuoterebbe la vasca in dieci riempimenti, e questa e' la
        # riserva del primo attacco locale in Aspromonte, non un punto di
        # rifornimento continuo.
        "usable_by": [ELI_MEDIO, ELI_LEGGERO, AUTOBOTTE, MODULO_AIB],
        "refill_minutes": 10.0,
        "capacity_liters": 90_000,
        "seasonal_reliability": 0.80,
        "note": "Novanta metri cubi: una sessantina di benne da elicottero "
        "medio, poi va rialimentata dall'acquedotto.",
    },
]


# --------------------------------------------------------------------------
# 4. Comuni
# --------------------------------------------------------------------------
# Selezione, non elenco completo: la Calabria ha 404 comuni e portarli tutti
# non aggiungerebbe niente alla simulazione. Il criterio e' doppio:
#   - i comuni piu' popolosi, perche' sono quelli che pesano nel calcolo di
#     `population_within_3km` e quindi nella priorita' dei focolai;
#   - i comuni montani interni, spesso di poche centinaia di abitanti, perche'
#     e' li' che il bosco tocca le case e l'interfaccia urbano-foresta conta
#     davvero. Un algoritmo tarato solo sui capoluoghi non vedrebbe mai
#     Alessandria del Carretto o Roccaforte del Greco, che sono esattamente i
#     posti dove un incendio diventa un problema di protezione civile.
#
# Formato: (nome, lat, lon, popolazione, altitudine_m).
# Le coordinate sono del centro abitato principale. La popolazione e'
# arrotondata alle centinaia: le cifre esatte cambiano ogni anno e la falsa
# precisione, in un simulatore, e' peggio dell'arrotondamento dichiarato.
#
# Convenzione di scrittura: dentro ogni provincia i comuni stanno in ordine di
# popolazione decrescente. Non serve al codice, che non guarda l'ordine, serve
# a chi rilegge: un record inserito nel posto sbagliato salta all'occhio, e con
# lui l'eventuale zero di troppo nella popolazione. Un nuovo comune va inserito
# al suo posto, non aggiunto in fondo.

COMUNI_PER_PROVINCIA: dict[str, list[tuple[str, float, float, int, int]]] = {
    "Cosenza": [
        ("Corigliano-Rossano", 39.5758, 16.6339, 74000, 219),
        ("Cosenza", 39.2983, 16.2533, 66000, 238),
        ("Rende", 39.3333, 16.1833, 35000, 474),
        ("Castrovillari", 39.8167, 16.2000, 21000, 350),
        ("Acri", 39.4917, 16.3833, 20000, 720),
        ("Montalto Uffugo", 39.4033, 16.1600, 19000, 468),
        ("Cassano all'Ionio", 39.7833, 16.3167, 17000, 250),
        ("San Giovanni in Fiore", 39.2550, 16.6970, 16000, 1049),
        ("Paola", 39.3600, 16.0400, 15500, 94),
        ("Amantea", 39.1333, 16.0750, 13500, 50),
        ("Scalea", 39.8133, 15.7930, 10000, 22),
        ("Cetraro", 39.5150, 15.9420, 9700, 120),
        ("Bisignano", 39.5100, 16.2800, 9700, 350),
        ("Crosia", 39.5700, 16.7700, 9600, 140),
        ("Castrolibero", 39.3100, 16.2000, 9500, 350),
        ("Casali del Manco", 39.2670, 16.3600, 9500, 800),
        ("Mendicino", 39.2600, 16.1900, 9400, 450),
        ("Luzzi", 39.4500, 16.2900, 8800, 450),
        ("Trebisacce", 39.8700, 16.5300, 8700, 61),
        ("Belvedere Marittimo", 39.6180, 15.8630, 8700, 150),
        ("Cariati", 39.4950, 16.9500, 8100, 55),
        ("Fuscaldo", 39.4150, 16.0300, 7800, 250),
        ("San Marco Argentano", 39.5600, 16.1200, 7300, 480),
        ("Roggiano Gravina", 39.6200, 16.1600, 6700, 285),
        ("Praia a Mare", 39.8950, 15.7830, 6500, 8),
        ("Rogliano", 39.1800, 16.3200, 5500, 650),
        ("Diamante", 39.6800, 15.8200, 5300, 14),
        ("Villapiana", 39.8450, 16.4600, 5300, 200),
        ("Spezzano della Sila", 39.3000, 16.3300, 4500, 872),
        ("Torano Castello", 39.5000, 16.1700, 4500, 350),
        ("Morano Calabro", 39.8430, 16.1370, 4300, 694),
        ("Dipignano", 39.2300, 16.2600, 4300, 570),
        ("Altomonte", 39.7000, 16.1300, 4200, 455),
        ("Lattarico", 39.4500, 16.1400, 3900, 350),
        ("Fagnano Castello", 39.5700, 16.0600, 3700, 510),
        ("Saracena", 39.7800, 16.1600, 3600, 606),
        ("San Demetrio Corone", 39.5700, 16.3700, 3200, 521),
        ("Rocca Imperiale", 40.1120, 16.5800, 3200, 210),
        ("Mormanno", 39.8880, 15.9930, 3100, 840),
        ("Longobucco", 39.4500, 16.6100, 3000, 770),
        ("Verbicaro", 39.7550, 15.9130, 2900, 400),
        ("Celico", 39.3100, 16.3500, 2800, 750),
        ("Aprigliano", 39.2300, 16.3400, 2700, 750),
        ("Amendolara", 39.9500, 16.5800, 2700, 230),
        ("San Fili", 39.3400, 16.1400, 2600, 555),
        ("Santa Sofia d'Epiro", 39.5400, 16.3300, 2600, 550),
        ("Cerchiara di Calabria", 39.8500, 16.3800, 2300, 650),
        ("Lungro", 39.7400, 16.1200, 2300, 600),
        ("Frascineto", 39.8300, 16.2700, 2100, 488),
        ("Oriolo", 40.0500, 16.4400, 2100, 450),
        ("Malvito", 39.6000, 16.0800, 1800, 420),
        ("Bocchigliero", 39.4200, 16.7500, 1300, 870),
        ("Orsomarso", 39.8000, 15.9000, 1200, 120),
        ("Acquaformosa", 39.7200, 16.1200, 1000, 780),
        ("Civita", 39.8250, 16.3150, 900, 450),
        ("Papasidero", 39.8500, 15.9100, 800, 210),
        ("San Lorenzo Bellizzi", 39.8400, 16.3400, 700, 830),
        ("Alessandria del Carretto", 39.9700, 16.3800, 400, 1000),
    ],
    "Catanzaro": [
        ("Catanzaro", 38.9098, 16.5877, 84000, 320),
        ("Lamezia Terme", 38.9650, 16.3080, 70000, 216),
        ("Soverato", 38.6870, 16.5510, 8700, 47),
        ("Sellia Marina", 38.8700, 16.7300, 7300, 12),
        ("Curinga", 38.8300, 16.3100, 6600, 350),
        ("Girifalco", 38.8200, 16.4300, 5800, 470),
        ("Chiaravalle Centrale", 38.6800, 16.4100, 5400, 550),
        ("Botricello", 38.9300, 16.8500, 4900, 5),
        ("Gizzeria", 38.9800, 16.2000, 4600, 500),
        ("Nocera Terinese", 39.0300, 16.1500, 4500, 240),
        ("Simeri Crichi", 38.9200, 16.6700, 4500, 330),
        ("Sersale", 39.0100, 16.7300, 4400, 750),
        ("Maida", 38.8500, 16.3600, 4400, 300),
        ("Guardavalle", 38.5044, 16.4996, 4300, 350),
        ("Cropani", 38.9600, 16.7800, 4300, 370),
        ("Tiriolo", 38.9400, 16.5100, 3700, 690),
        ("Falerna", 39.0000, 16.1700, 3700, 500),
        ("Squillace", 38.7800, 16.5200, 3500, 344),
        ("Soveria Mannelli", 39.0800, 16.3700, 3100, 750),
        ("Decollatura", 39.0400, 16.3500, 3000, 750),
        ("Badolato", 38.5700, 16.5900, 3000, 240),
        ("Taverna", 39.0100, 16.5800, 2600, 528),
        ("Santa Caterina dello Ionio", 38.5400, 16.5200, 2000, 355),
        ("Cortale", 38.8300, 16.4200, 2000, 430),
        ("Zagarise", 38.9800, 16.6500, 1600, 570),
        ("Montauro", 38.7500, 16.5100, 1500, 260),
        ("Conflenti", 39.0700, 16.2800, 1300, 700),
        ("Magisano", 39.0000, 16.6000, 1200, 760),
        ("Petrizzi", 38.6800, 16.4700, 1100, 420),
        ("Albi", 39.0200, 16.5500, 900, 710),
    ],
    "Crotone": [
        ("Crotone", 39.0808, 17.1270, 62000, 8),
        ("Isola di Capo Rizzuto", 38.9600, 17.0900, 17000, 90),
        ("Ciro' Marina", 39.3700, 17.1300, 14000, 11),
        ("Cutro", 39.0300, 16.9800, 9700, 218),
        ("Petilia Policastro", 39.1100, 16.7900, 8500, 436),
        ("Strongoli", 39.2700, 17.0500, 6400, 342),
        ("Mesoraca", 39.0700, 16.7800, 6300, 425),
        ("Rocca di Neto", 39.2000, 17.0000, 5500, 160),
        ("Melissa", 39.3100, 17.0200, 3200, 256),
        ("Ciro'", 39.3800, 17.0600, 2800, 340),
        ("Casabona", 39.2500, 16.9500, 2600, 350),
        ("Santa Severina", 39.1500, 16.9200, 2200, 326),
        ("Verzino", 39.2400, 16.8600, 1700, 550),
        ("Caccuri", 39.2200, 16.7800, 1500, 645),
        ("Savelli", 39.3200, 16.7800, 1000, 1000),
    ],
    "Vibo Valentia": [
        ("Vibo Valentia", 38.6757, 16.1005, 32000, 476),
        ("Pizzo", 38.7350, 16.1600, 9000, 107),
        ("Mileto", 38.6070, 16.0670, 6600, 355),
        ("Serra San Bruno", 38.5720, 16.3290, 6300, 790),
        ("Tropea", 38.6790, 15.8980, 6200, 61),
        ("Nicotera", 38.5520, 15.9350, 6100, 210),
        ("Filadelfia", 38.7800, 16.2900, 5400, 550),
        ("Ricadi", 38.6200, 15.8500, 4500, 200),
        ("Briatico", 38.7200, 16.0300, 4000, 50),
        ("Soriano Calabro", 38.5500, 16.2300, 2400, 320),
        ("Dinami", 38.5300, 16.1400, 2200, 420),
        ("Fabrizia", 38.4900, 16.3000, 2000, 935),
        ("Spilinga", 38.6300, 15.9200, 1400, 300),
        ("Arena", 38.5500, 16.2100, 1400, 500),
        ("Mongiana", 38.5100, 16.3200, 700, 921),
    ],
    "Reggio Calabria": [
        ("Reggio Calabria", 38.1113, 15.6473, 170000, 31),
        ("Gioia Tauro", 38.4250, 15.8990, 18000, 8),
        ("Palmi", 38.3580, 15.8520, 17500, 220),
        ("Siderno", 38.2690, 16.2960, 17500, 18),
        ("Taurianova", 38.3540, 16.0120, 15000, 210),
        ("Rosarno", 38.4870, 15.9800, 14000, 61),
        ("Villa San Giovanni", 38.2200, 15.6350, 13000, 6),
        ("Locri", 38.2380, 16.2600, 12000, 5),
        ("Melito di Porto Salvo", 37.9200, 15.7800, 11000, 10),
        ("Polistena", 38.4050, 16.0740, 10300, 254),
        ("Cittanova", 38.3540, 16.0810, 9600, 407),
        ("Bagnara Calabra", 38.2870, 15.8080, 9500, 50),
        ("Bovalino", 38.1500, 16.1800, 8800, 10),
        ("Caulonia", 38.3800, 16.4200, 6700, 285),
        ("Cinquefrondi", 38.4200, 16.0900, 6300, 275),
        ("Oppido Mamertina", 38.2920, 15.9850, 4900, 350),
        ("Condofuri", 37.9500, 15.8600, 4900, 300),
        ("Scilla", 38.2540, 15.7150, 4700, 73),
        ("San Luca", 38.1450, 16.0630, 3800, 250),
        ("Sant'Eufemia d'Aspromonte", 38.2600, 15.8500, 3700, 500),
        ("Plati'", 38.2200, 16.0500, 3500, 310),
        ("Brancaleone", 37.9650, 16.1000, 3400, 5),
        ("Monasterace", 38.4500, 16.5500, 3300, 100),
        ("Delianuova", 38.2400, 15.9200, 3200, 600),
        ("Africo", 38.0400, 16.1400, 3100, 10),
        ("Mammola", 38.3600, 16.2400, 2800, 250),
        ("Gerace", 38.2700, 16.2200, 2500, 500),
        ("Stilo", 38.4750, 16.4650, 2500, 400),
        ("Molochio", 38.3100, 16.0300, 2300, 310),
        ("Cardeto", 38.0600, 15.7700, 1600, 700),
        ("Antonimina", 38.2700, 16.1500, 1300, 330),
        ("Bivongi", 38.4800, 16.4500, 1300, 280),
        ("Santo Stefano in Aspromonte", 38.1700, 15.8000, 1200, 710),
        ("Roghudi", 38.0200, 15.9000, 1100, 500),
        ("Ferruzzano", 38.0200, 16.0800, 700, 500),
        ("Roccaforte del Greco", 38.0300, 15.9100, 500, 900),
        ("Bova", 37.9900, 15.9300, 400, 820),
        ("Staiti", 37.9800, 16.0400, 200, 550),
    ],
}


# --------------------------------------------------------------------------
# 5. Aree protette
# --------------------------------------------------------------------------
# Formato: (nome, tipo, lat, lon, raggio_km).
#
# La geometria vera di un parco e' un poligono con centinaia di vertici,
# spesso in piu' pezzi staccati. Qui ogni area e' UN CERCHIO, centrato sul
# baricentro approssimato e con raggio pari a quello del cerchio di pari area
# della superficie ufficiale: r = sqrt(superficie_km2 / pi). E' la migliore
# approssimazione a cerchio singolo nel senso che almeno conserva l'area, e
# quindi non gonfia la frazione di territorio che il sistema considera
# tutelata.
#
# Il prezzo si paga ai bordi, e va detto: la Sila reale e' fatta di tre
# tronconi (Greca, Grande, Piccola) distesi su una settantina di chilometri,
# il Pollino sconfina in Basilicata e qui e' contato solo per la parte
# calabrese. Il cerchio prende dentro pezzi che il parco non comprende e
# lascia fuori pezzi che comprende. Per l'uso che ne fa il sistema, cioe'
# pesare per il dieci per cento la priorita' di un focolaio, l'errore e'
# accettabile. Se un giorno servisse la risposta esatta, la sostituzione e'
# locale: cambia il formato del file e la funzione che lo legge, non il resto.

AREE_PROTETTE: list[tuple[str, str, float, float, float]] = [
    ("Parco Nazionale della Sila", "parco_nazionale", 39.3200, 16.5600, 15.3),
    ("Parco Nazionale dell'Aspromonte", "parco_nazionale", 38.1800, 15.9500, 14.3),
    ("Parco Nazionale del Pollino (versante calabrese)", "parco_nazionale", 39.9000, 16.1500, 16.7),
    ("Parco Naturale Regionale delle Serre", "parco_regionale", 38.5500, 16.3000, 7.5),
    ("Riserva Naturale Valle del Fiume Lao", "riserva_statale", 39.8400, 15.8800, 4.1),
    ("Riserva Naturale Valle del Fiume Argentino", "riserva_statale", 39.7600, 15.9500, 3.6),
    ("Riserva Naturale Gole del Raganello", "riserva_statale", 39.8300, 16.2800, 2.3),
    ("Riserva Naturale Marchesale", "riserva_statale", 38.5200, 16.2500, 2.0),
    ("Riserva Naturale Cropani Micone", "riserva_statale", 38.5800, 16.3000, 0.8),
    ("Riserva Naturale Golia Corvo", "riserva_statale", 38.6300, 16.4200, 1.6),
    ("Riserva Naturale Regionale Lago di Tarsia", "riserva_regionale", 39.6200, 16.2800, 1.2),
    ("Riserva Naturale Regionale Foce del Crati", "riserva_regionale", 39.7100, 16.5200, 1.0),
    ("Oasi WWF Lago Angitola", "oasi", 38.7400, 16.2100, 1.7),
]


# --------------------------------------------------------------------------
# Costruzione dei record
# --------------------------------------------------------------------------


def _record_indipendente(record: dict[str, Any]) -> dict[str, Any]:
    """Copia un record in modo che non condivida liste con nessun altro.

    Serve perche' i record di questo modulo contengono liste (`supports`,
    `usable_by`) e due copie ingenue non bastano:

    - `dict(r)` e' una copia superficiale: il record nuovo punta ALLA STESSA
      lista della costante del modulo. Chi ci aggiungesse un elemento
      cambierebbe i dati di tutte le chiamate successive nello stesso processo;
    - `copy.deepcopy(elenco)` risolve quel problema ma ne lascia un altro meno
      ovvio: la copia profonda conserva la condivisione INTERNA. Dieci sorgenti
      che partivano dalla stessa costante di comodo (`TUTTI_AEREI`) nella copia
      continuano a condividere una lista sola, e modificarne una le cambia
      tutte e dieci.

    Qui ogni lista viene ricostruita elemento per elemento, quindi ogni record
    e' padrone dei suoi. E' la stessa cura che `registry.load_fleet` mette nel
    restituire mezzi nuovi a ogni chiamata.
    """
    return {k: (list(v) if isinstance(v, list) else v) for k, v in record.items()}


def build_bases() -> list[dict[str, Any]]:
    """Costruisce i record delle basi nel formato letto da `load_bases`."""
    return [_record_indipendente(b) for b in BASI]


def build_fleet() -> list[dict[str, Any]]:
    """Espande profilo piu' dislocazione nei record letti da `load_fleet`.

    Il profilo passa da `_record_indipendente` per la ragione spiegata li':
    oggi i suoi valori sono tutti scalari, ma il giorno in cui un profilo
    contenesse una lista (per dire, i tipi di sorgente compatibili) le ventisei
    macchine se la ritroverebbero condivisa senza che nessuno l'abbia deciso.
    """
    out: list[dict[str, Any]] = []
    for vehicle_id, name, profilo, base_id in DISLOCAZIONE_MEZZI:
        if profilo not in PROFILI_MEZZI:
            raise ValueError(f"{vehicle_id}: profilo sconosciuto {profilo!r}")
        record: dict[str, Any] = {
            "vehicle_id": vehicle_id,
            "name": name,
            "home_base_id": base_id,
            # Lo stato e' sempre "available" nel registro: lo stato dinamico
            # (assegnato, in manutenzione, fuori turno) appartiene allo
            # scenario in corso, non ai dati statici. E' la stessa ragione per
            # cui `load_fleet` restituisce copie fresche.
            "status": VehicleStatus.AVAILABLE.value,
            "profile": profilo,
        }
        record.update(_record_indipendente(PROFILI_MEZZI[profilo]))
        out.append(record)
    return out


def build_water_sources() -> list[dict[str, Any]]:
    """Costruisce i record dei punti d'acqua.

    `usable_by` non e' soltanto una lista: e' spesso LO STESSO oggetto
    (`TUTTI_AEREI`, `SOLO_ELICOTTERI`, `SOLO_TERRA`) riusato da piu' sorgenti
    per non ripetere l'elenco. Vedere `_record_indipendente` per il motivo per
    cui qui non basta nessuna delle due copie ovvie.
    """
    return [_record_indipendente(w) for w in SORGENTI_ACQUA]


def build_comuni() -> list[dict[str, Any]]:
    """Appiattisce la mappa provincia -> comuni in un elenco unico.

    L'ordine e' quello di `PROVINCE` in `config.py` e, dentro ogni provincia,
    quello della costante: nessuna iterazione su insiemi, nessun ordinamento
    che dipenda dalla localizzazione. E' una delle condizioni per cui due
    rigenerazioni producono gli stessi byte.

    I record sono costruiti da tuple di scalari, quindi qui non c'e' niente da
    copiare in profondita': ogni chiamata crea dizionari nuovi.
    """
    # `PROVINCE` arriva da config.py, `COMUNI_PER_PROVINCIA` e' qui: se i due
    # elenchi divergessero, una provincia sparirebbe dal file senza che nessuno
    # lo noti (o si prenderebbe un KeyError nudo, che non dice che fare).
    ignote = sorted(set(COMUNI_PER_PROVINCIA) - set(PROVINCE))
    senza_comuni = sorted(set(PROVINCE) - set(COMUNI_PER_PROVINCIA))
    if ignote or senza_comuni:
        raise ValueError(
            "elenco delle province disallineato con config.PROVINCE: "
            f"qui in piu' {ignote}, qui mancanti {senza_comuni}"
        )

    out: list[dict[str, Any]] = []
    for provincia in PROVINCE:
        for nome, lat, lon, pop, alt in COMUNI_PER_PROVINCIA[provincia]:
            out.append(
                {
                    "nome": nome,
                    "provincia": provincia,
                    "lat": lat,
                    "lon": lon,
                    "popolazione": pop,
                    "altitudine_m": alt,
                }
            )
    return out


def build_protected_areas() -> list[dict[str, Any]]:
    """Costruisce i record delle aree protette (cerchi approssimati)."""
    return [
        {"nome": nome, "tipo": tipo, "lat": lat, "lon": lon, "raggio_km": raggio}
        for nome, tipo, lat, lon, raggio in AREE_PROTETTE
    ]


# --------------------------------------------------------------------------
# Validazione: il generatore controlla se stesso prima di scrivere
# --------------------------------------------------------------------------
# Un JSON sbagliato non si vede a occhio e rompe il sottosistema di qualcun
# altro tre ore dopo. Meglio fallire qui, con un messaggio che dice quale
# record e' sbagliato.

# `CALABRIA_BBOX` sta in config.py nell'ordine di FIRMS, cioe' LON prima di
# LAT. Lo si spacchetta una volta sola e qui: invertire i due assi e' l'errore
# che non da' nessun sintomo (la Calabria a lat 16, lon 39 e' in mezzo all'Asia)
# e che poi fa scartare per "coordinate fuori bbox" dati perfettamente buoni.
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = CALABRIA_BBOX

# Intervalli di plausibilita'. Sono tarature dichiarate, scelte larghe di
# proposito: non devono dire se un numero e' esatto, devono intercettare lo
# zero, la virgola sbagliata e l'ordine di grandezza fuori posto.
VELOCITA_AEREO_KMH = (150.0, 400.0)
VELOCITA_TERRA_KMH = (30.0, 90.0)
# Oltre i 40 km di raggio un'area protetta coprirebbe mezza regione e il
# criterio "il focolaio e' in area tutelata" smetterebbe di discriminare.
RAGGIO_AREA_MAX_KM = 40.0
# Reggio Calabria, il comune piu' popoloso, sta intorno ai 170 mila abitanti.
POPOLAZIONE_MAX = 200_000
# `altitudine_m` e' la quota del capoluogo comunale, non della cima piu' alta
# del territorio. Il comune piu' alto della Calabria e' San Giovanni in Fiore,
# intorno ai 1050 m, e nessun centro abitato supera i 1400 (Gambarie sta a
# 1300). Il limite e' tenuto a 1600 m: lascia margine abbondante e intercetta
# comunque lo zero di troppo (219 scritto 2190).
ALTITUDINE_MAX_M = 1600

# Tipi che volano. Il dominio non e' un attributo indipendente dal tipo: un
# Canadair terrestre e' un dato incoerente, non una scelta di modellazione.
TIPI_AEREI = frozenset({CANADAIR, ELI_PESANTE, ELI_MEDIO, ELI_LEGGERO})

# Chiavi che ogni record deve avere perche' `domain/registry.py` lo sappia
# leggere. Quelle marcate come numeriche devono anche essere numeri: un "39.5"
# fra virgolette, tipico di un JSON ritoccato a mano, farebbe fallire i
# confronti con un TypeError invece che con un messaggio comprensibile.
CHIAVI_BASE = ("base_id", "name", "lat", "lon", "provincia", "supports",
               "has_night_capability", "turnaround_minutes")
NUMERI_BASE = ("lat", "lon", "turnaround_minutes")
CHIAVI_MEZZO = ("vehicle_id", "name", "home_base_id", "kind", "domain",
                "cruise_speed_kmh", "water_capacity_liters", "drop_minutes",
                "refill_minutes", "setup_minutes", "endurance_minutes",
                "max_shift_minutes", "night_capable", "cost_per_hour_eur",
                "max_wind_kmh", "requires_water_source")
NUMERI_MEZZO = ("cruise_speed_kmh", "water_capacity_liters", "drop_minutes",
                "refill_minutes", "setup_minutes", "endurance_minutes",
                "max_shift_minutes", "cost_per_hour_eur", "max_wind_kmh")
CHIAVI_SORGENTE = ("source_id", "name", "lat", "lon", "kind", "usable_by",
                   "refill_minutes", "seasonal_reliability")
NUMERI_SORGENTE = ("lat", "lon", "refill_minutes", "seasonal_reliability")
CHIAVI_COMUNE = ("nome", "provincia", "lat", "lon", "popolazione", "altitudine_m")
NUMERI_COMUNE = ("lat", "lon", "popolazione", "altitudine_m")
CHIAVI_AREA = ("nome", "tipo", "lat", "lon", "raggio_km")
NUMERI_AREA = ("lat", "lon", "raggio_km")


def _in_bbox(lat: float, lon: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def _check_unique(values: list[str], etichetta: str, errors: list[str]) -> None:
    """Segnala gli identificativi ripetuti.

    Un id duplicato non rompe il caricamento e per questo e' insidioso: rompe
    il solver, che indicizza le variabili per identificativo e si ritrova due
    mezzi diversi sulla stessa variabile. L'insieme `visti` serve solo a
    rispondere "l'ho gia' incontrato?" e non viene mai iterato: iterare su un
    insieme darebbe un ordine imprevedibile e i messaggi d'errore
    cambierebbero ordine da un'esecuzione all'altra.
    """
    visti: set[str] = set()
    for v in values:
        if v in visti:
            errors.append(f"{etichetta}: identificativo duplicato {v!r}")
        visti.add(v)


def _record_leggibile(
    record: dict[str, Any],
    chiavi: tuple[str, ...],
    numeri: tuple[str, ...],
    etichetta: str,
    errors: list[str],
) -> bool:
    """Vero se il record ha tutte le chiavi e i numeri sono numeri.

    Serve a rendere `validate` TOTALE: senza questo controllo un record a cui
    manca una chiave farebbe uscire un `KeyError` dal validatore, cioe' proprio
    dalla funzione il cui compito e' elencare i problemi invece di esplodere.
    Il chiamante salta gli altri controlli sul record incompleto, altrimenti
    otterrebbe una cascata di messaggi che parlano tutti dello stesso difetto.
    """
    ok = True
    mancanti = [k for k in chiavi if k not in record]
    if mancanti:
        errors.append(f"{etichetta}: chiavi mancanti {mancanti}")
        ok = False
    for k in numeri:
        if k not in record:
            continue
        v = record[k]
        # `isinstance(True, int)` e' vero in Python: un booleano passerebbe per
        # un numero valido e diventerebbe una latitudine pari a 1.
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append(f"{etichetta}: {k} non e' un numero ({v!r})")
            ok = False
    return ok


def validate(
    bases: list[dict[str, Any]],
    fleet: list[dict[str, Any]],
    water: list[dict[str, Any]],
    comuni: list[dict[str, Any]],
    areas: list[dict[str, Any]],
) -> list[str]:
    """Ritorna la lista dei problemi trovati. Lista vuota significa tutto bene.

    Va chiamata sui CINQUE elenchi insieme, perche' una parte dei controlli e'
    incrociata: un mezzo che punta a una base inesistente si vede solo avendo
    sotto gli occhi anche le basi. Cinque liste vuote sono un caso lecito e non
    producono errori: un insieme di dati vuoto e' incompleto, non incoerente,
    e chi lo usa se ne accorge subito.

    La funzione non solleva eccezioni e non modifica gli elenchi che riceve:
    e' un rapporto, non un'azione. Chi deve decidere se scrivere su disco e'
    `genera`.
    """
    errors: list[str] = []

    kinds = {k.value for k in VehicleKind}
    domains = {d.value for d in Domain}
    water_kinds = {k.value for k in WaterSourceKind}

    # --- basi ---
    basi_ok: list[dict[str, Any]] = []
    for b in bases:
        etichetta = f"base {b.get('base_id', '<senza id>')}"
        if _record_leggibile(b, CHIAVI_BASE, NUMERI_BASE, etichetta, errors):
            basi_ok.append(b)
    _check_unique([b["base_id"] for b in basi_ok], "basi", errors)
    for b in basi_ok:
        etichetta = f"base {b['base_id']}"
        if not _in_bbox(b["lat"], b["lon"]):
            errors.append(f"{etichetta}: coordinate fuori dalla bbox Calabria")
        if b["provincia"] not in PROVINCE:
            errors.append(f"{etichetta}: provincia ignota {b['provincia']!r}")
        else:
            # Stessa rete di sicurezza usata per i comuni: intercetta la
            # coordinata spostata di un grado, cioe' di un centinaio di km.
            riquadro = PROVINCE_APPROX_BOX.get(b["provincia"])
            if riquadro is None:
                errors.append(f"{etichetta}: manca il riquadro di {b['provincia']!r}")
            else:
                lat_min, lat_max, lon_min, lon_max = riquadro
                if not (lat_min <= b["lat"] <= lat_max and lon_min <= b["lon"] <= lon_max):
                    errors.append(
                        f"{etichetta}: coordinate fuori dal riquadro di {b['provincia']}"
                    )
        if not b["supports"]:
            # Una base che non supporta niente e' peso morto: nessun mezzo puo'
            # averci casa e nessun mezzo puo' rientrarci.
            errors.append(f"{etichetta}: nessun tipo di mezzo supportato")
        for k in b["supports"]:
            if k not in kinds:
                errors.append(f"{etichetta}: tipo mezzo ignoto {k!r}")
        if b["turnaround_minutes"] < 0:
            errors.append(f"{etichetta}: turnaround negativo")

    base_by_id = {b["base_id"]: b for b in basi_ok}

    # --- mezzi ---
    mezzi_ok: list[dict[str, Any]] = []
    for v in fleet:
        etichetta = f"mezzo {v.get('vehicle_id', '<senza id>')}"
        if _record_leggibile(v, CHIAVI_MEZZO, NUMERI_MEZZO, etichetta, errors):
            mezzi_ok.append(v)
    _check_unique([v["vehicle_id"] for v in mezzi_ok], "mezzi", errors)
    for v in mezzi_ok:
        vid = v["vehicle_id"]
        if v["kind"] not in kinds:
            errors.append(f"mezzo {vid}: tipo ignoto {v['kind']!r}")
        if v["domain"] not in domains:
            errors.append(f"mezzo {vid}: dominio ignoto {v['domain']!r}")
        elif v["kind"] in kinds:
            # Il dominio discende dal tipo: se i due si contraddicono, il mezzo
            # verrebbe pianificato con il modello fisico sbagliato (rotta in
            # linea d'aria invece che su strada, o viceversa).
            atteso = Domain.AIR.value if v["kind"] in TIPI_AEREI else Domain.GROUND.value
            if v["domain"] != atteso:
                errors.append(
                    f"mezzo {vid}: dominio {v['domain']!r} incoerente con il tipo "
                    f"{v['kind']!r} (atteso {atteso!r})"
                )
        base = base_by_id.get(v["home_base_id"])
        if base is None:
            errors.append(f"mezzo {vid}: base inesistente {v['home_base_id']!r}")
        elif v["kind"] not in base["supports"]:
            errors.append(
                f"mezzo {vid}: la base {base['base_id']} non supporta {v['kind']}"
            )
        # Plausibilita' fisica: un aereo non va a 40 km/h, un pick-up non va a 200.
        if v["domain"] == Domain.AIR.value and not (
            VELOCITA_AEREO_KMH[0] <= v["cruise_speed_kmh"] <= VELOCITA_AEREO_KMH[1]
        ):
            errors.append(f"mezzo {vid}: velocita' implausibile per un mezzo aereo")
        if v["domain"] == Domain.GROUND.value and not (
            VELOCITA_TERRA_KMH[0] <= v["cruise_speed_kmh"] <= VELOCITA_TERRA_KMH[1]
        ):
            errors.append(f"mezzo {vid}: velocita' implausibile per un mezzo terrestre")
        # Il controllo sullo zero vale anche se il dominio e' sconosciuto e i
        # due intervalli qui sopra non sono stati applicati: la velocita' sta
        # al denominatore del tempo di percorrenza in ogni modulo a valle.
        if v["cruise_speed_kmh"] <= 0:
            errors.append(f"mezzo {vid}: velocita' non positiva")
        if v["endurance_minutes"] > v["max_shift_minutes"]:
            errors.append(f"mezzo {vid}: autonomia maggiore del turno massimo")
        # Tutti i tempi e i costi devono essere positivi, non solo non negativi.
        # Uno zero non e' un dato mancante che si propaga innocuo: e' una
        # missione che dura zero minuti, un lancio istantaneo, un mezzo gratis.
        for campo in ("drop_minutes", "refill_minutes", "setup_minutes",
                      "endurance_minutes", "max_shift_minutes",
                      "cost_per_hour_eur", "max_wind_kmh"):
            if v[campo] <= 0:
                errors.append(f"mezzo {vid}: {campo} non positivo")
        # La capacita' finisce al DENOMINATORE del conto "quante sortite servono
        # per coprire il fabbisogno". Zero non e' un mezzo senz'acqua, e' una
        # divisione per zero: chi non porta acqua ha comunque i suoi estintori
        # spalleggiati (vedere il profilo SQUADRA).
        if v["water_capacity_liters"] <= 0:
            errors.append(f"mezzo {vid}: capacita' d'acqua non positiva")

    # --- acqua ---
    sorgenti_ok: list[dict[str, Any]] = []
    for w in water:
        etichetta = f"sorgente {w.get('source_id', '<senza id>')}"
        if _record_leggibile(w, CHIAVI_SORGENTE, NUMERI_SORGENTE, etichetta, errors):
            sorgenti_ok.append(w)
    _check_unique([w["source_id"] for w in sorgenti_ok], "sorgenti", errors)
    for w in sorgenti_ok:
        sid = w["source_id"]
        if not _in_bbox(w["lat"], w["lon"]):
            errors.append(f"sorgente {sid}: coordinate fuori dalla bbox Calabria")
        if w["kind"] not in water_kinds:
            errors.append(f"sorgente {sid}: tipo ignoto {w['kind']!r}")
        if not w["usable_by"]:
            errors.append(f"sorgente {sid}: nessun mezzo compatibile")
        for k in w["usable_by"]:
            if k not in kinds:
                errors.append(f"sorgente {sid}: tipo mezzo ignoto {k!r}")
        if not 0.0 < w["seasonal_reliability"] <= 1.0:
            errors.append(f"sorgente {sid}: affidabilita' stagionale fuori da (0, 1]")
        if w["refill_minutes"] <= 0:
            errors.append(f"sorgente {sid}: tempo di rifornimento non positivo")
        # `None` vuol dire "praticamente illimitato" (il mare). Zero o negativo
        # vorrebbero dire una sorgente da cui non si preleva niente, e allora
        # non e' una sorgente.
        capienza = w.get("capacity_liters")
        if capienza is not None and (
            isinstance(capienza, bool)
            or not isinstance(capienza, (int, float))
            or capienza <= 0
        ):
            errors.append(f"sorgente {sid}: capacita' non positiva ({capienza!r})")
        if w["kind"] == WaterSourceKind.TANK.value and CANADAIR in w["usable_by"]:
            errors.append(f"sorgente {sid}: un Canadair non pesca da una vasca")

    # --- incrocio mezzi/acqua ---
    # Un tipo di mezzo che dichiara di aver bisogno di una sorgente e non ha
    # nessuna sorgente che lo accetti puo' fare una sola sortita e poi resta a
    # secco per il resto della giornata. E' un dato incoerente fra due file,
    # cioe' esattamente il difetto che nessuno dei due file rivela da solo.
    if mezzi_ok and sorgenti_ok:
        serviti = {k for w in sorgenti_ok for k in w["usable_by"]}
        assetati = sorted(
            {
                v["kind"]
                for v in mezzi_ok
                if v["requires_water_source"] and v["kind"] not in serviti
            }
        )
        for k in assetati:
            errors.append(f"tipo {k}: nessuna sorgente d'acqua lo accetta")

    # --- comuni ---
    comuni_ok: list[dict[str, Any]] = []
    for c in comuni:
        etichetta = f"comune {c.get('nome', '<senza nome>')}"
        if _record_leggibile(c, CHIAVI_COMUNE, NUMERI_COMUNE, etichetta, errors):
            comuni_ok.append(c)
    _check_unique(
        [f"{c['nome']}|{c['provincia']}" for c in comuni_ok], "comuni", errors
    )
    for c in comuni_ok:
        etichetta = f"comune {c['nome']}"
        if not _in_bbox(c["lat"], c["lon"]):
            errors.append(f"{etichetta}: coordinate fuori dalla bbox Calabria")
        if c["provincia"] not in PROVINCE:
            errors.append(f"{etichetta}: provincia ignota {c['provincia']!r}")
        else:
            riquadro = PROVINCE_APPROX_BOX.get(c["provincia"])
            if riquadro is None:
                errors.append(f"{etichetta}: manca il riquadro di {c['provincia']!r}")
            else:
                lat_min, lat_max, lon_min, lon_max = riquadro
                if not (lat_min <= c["lat"] <= lat_max and lon_min <= c["lon"] <= lon_max):
                    errors.append(
                        f"{etichetta}: coordinate fuori dal riquadro di {c['provincia']}"
                    )
        if c["popolazione"] <= 0:
            errors.append(f"{etichetta}: popolazione non positiva")
        if c["popolazione"] > POPOLAZIONE_MAX:
            errors.append(f"{etichetta}: popolazione implausibile ({c['popolazione']})")
        if c["altitudine_m"] < 0:
            errors.append(f"{etichetta}: altitudine negativa")
        if c["altitudine_m"] > ALTITUDINE_MAX_M:
            errors.append(f"{etichetta}: altitudine implausibile ({c['altitudine_m']} m)")

    # --- aree protette ---
    aree_ok: list[dict[str, Any]] = []
    for a in areas:
        etichetta = f"area {a.get('nome', '<senza nome>')}"
        if _record_leggibile(a, CHIAVI_AREA, NUMERI_AREA, etichetta, errors):
            aree_ok.append(a)
    _check_unique([a["nome"] for a in aree_ok], "aree protette", errors)
    for a in aree_ok:
        etichetta = f"area {a['nome']}"
        if not _in_bbox(a["lat"], a["lon"]):
            errors.append(f"{etichetta}: coordinate fuori dalla bbox Calabria")
        if a["raggio_km"] <= 0:
            errors.append(f"{etichetta}: raggio non positivo")
        elif a["raggio_km"] > RAGGIO_AREA_MAX_KM:
            errors.append(f"{etichetta}: raggio implausibile ({a['raggio_km']} km)")
        if not a["tipo"]:
            errors.append(f"{etichetta}: tipo vuoto")

    return errors


# --------------------------------------------------------------------------
# Scrittura
# --------------------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> None:
    """Scrive con indentazione fissa, UTF-8 e newline finale.

    `ensure_ascii=False` serve a tenere leggibili i nomi accentati nel file, e
    `newline="\\n"` a scrivere terminatori di riga LF anche su Windows: senza
    quello Python tradurrebbe ogni `\\n` in `\\r\\n`, il file rigenerato su una
    macchina diversa avrebbe byte diversi a parita' di contenuto e il test di
    determinismo fallirebbe per un motivo che non c'entra niente con i dati.

    La scrittura passa da un file temporaneo nella stessa cartella e poi da
    `os.replace`, che e' atomico: se il processo muore a meta' scrittura, sul
    disco resta il file vecchio e intero invece di un JSON troncato. Un JSON
    troncato si manifesta a valle come un errore di parsing oscuro dentro
    `registry.py`, lontanissimo da qui.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def genera(target_dir: Optional[Path] = None, check_only: bool = False) -> dict[str, int]:
    """Costruisce, valida e (se non `check_only`) scrive i cinque JSON.

    L'ordine conta: si valida TUTTO prima di scrivere QUALSIASI cosa. Se la
    validazione fallisce non viene toccato un solo file, quindi la cartella
    `data/static/` non resta mai in uno stato misto, con due file nuovi e tre
    vecchi che non si parlano piu' fra loro.

    Fallisce con `SystemExit` e non con una eccezione ordinaria perche' questo
    e' uno script: il messaggio elenca i problemi e il processo esce con stato
    diverso da zero, che e' quello che serve a chi lo lancia a mano o dentro
    una verifica prima di committare. Chi lo richiama da Python e vuole
    intercettare il fallimento deve prendere `SystemExit`, che discende da
    `BaseException` e non da `Exception`.

    Ritorna i conteggi per file, cosi' chi lo richiama da un altro script sa
    quante righe ha prodotto senza doverle rileggere dal disco.
    """
    out_dir = target_dir or STATIC_DIR

    bases = build_bases()
    fleet = build_fleet()
    water = build_water_sources()
    comuni = build_comuni()
    areas = build_protected_areas()

    errors = validate(bases, fleet, water, comuni, areas)
    if errors:
        raise SystemExit(
            "dati statici non validi, niente e' stato scritto:\n  - "
            + "\n  - ".join(errors)
        )

    if not check_only:
        _write_json(out_dir / "basi.json", bases)
        _write_json(out_dir / "mezzi.json", fleet)
        _write_json(out_dir / "sorgenti_acqua.json", water)
        _write_json(out_dir / "comuni.json", comuni)
        _write_json(out_dir / "aree_protette.json", areas)

    return {
        "basi": len(bases),
        "mezzi": len(fleet),
        "sorgenti_acqua": len(water),
        "comuni": len(comuni),
        "aree_protette": len(areas),
    }


def main() -> None:
    """Interfaccia da riga di comando.

    L'avvertenza sui dati viene ristampata a ogni esecuzione, e non e' una
    formalita': questi file hanno l'aspetto di un inventario reale e il
    rischio che qualcuno li prenda per tale cresce ogni volta che cambiano
    mano. Un file di dati non si porta dietro il contesto in cui e' nato, lo
    dice solo chi lo genera.
    """
    parser = argparse.ArgumentParser(
        description="Rigenera i dati statici della Calabria (deterministico, senza rete)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="valida le costanti e stampa i conteggi senza scrivere nulla",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="cartella di destinazione (default: data/static del progetto)",
    )
    args = parser.parse_args()

    conteggi = genera(target_dir=args.out, check_only=args.check)
    destinazione = args.out or STATIC_DIR
    azione = "verificati" if args.check else f"scritti in {destinazione}"
    print(f"Dati statici {azione}:")
    for nome, quanti in conteggi.items():
        print(f"  {nome:<16} {quanti:>4}")
    print(
        "\nAvvertenza: dati plausibili a scopo di simulazione, non l'inventario "
        "reale del servizio antincendio."
    )


if __name__ == "__main__":
    main()
