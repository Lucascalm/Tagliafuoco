"""Configurazione centrale e costanti di taratura.

Tutti i numeri che governano il comportamento del sistema stanno qui, in un
posto solo, con accanto la ragione per cui valgono quello che valgono. Molti
sono tarature dichiarate, non costanti fisiche: tenerli sparsi nel codice
renderebbe impossibile capire quali risultati dipendono da una scelta e quali
dai dati.

Le chiavi API si leggono dall'ambiente e sono sempre opzionali. Il sistema
deve funzionare per intero senza nessuna chiave: senza `FIRMS_MAP_KEY` legge
i rilevamenti dalle fixture locali, senza `ANTHROPIC_API_KEY` traduce gli
ordini con il traduttore deterministico a regole.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # opzionale: comodo in locale, non necessario
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


# --------------------------------------------------------------------------
# Percorsi
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
STATIC_DIR = DATA_DIR / "static"
FIXTURES_DIR = DATA_DIR / "fixtures"
DOCS_DIR = PROJECT_ROOT / "docs"
WEB_DIR = PROJECT_ROOT / "web"
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"

for _d in (RAW_DIR, PROCESSED_DIR, STATIC_DIR, FIXTURES_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Chiavi API (sempre opzionali)
# --------------------------------------------------------------------------

FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

LLM_MODEL = os.getenv("TAGLIAFUOCO_LLM_MODEL", "claude-sonnet-5").strip()
LLM_MAX_TOKENS = int(os.getenv("TAGLIAFUOCO_LLM_MAX_TOKENS", "2048"))
LLM_TIMEOUT_S = float(os.getenv("TAGLIAFUOCO_LLM_TIMEOUT", "60"))


def has_firms_key() -> bool:
    return bool(FIRMS_MAP_KEY)


def has_llm_key() -> bool:
    return bool(ANTHROPIC_API_KEY)


# --------------------------------------------------------------------------
# Area di interesse
# --------------------------------------------------------------------------

# Bounding box della Calabria, con un margine di sicurezza sui bordi.
# FIRMS accetta l'area nel formato lon_min,lat_min,lon_max,lat_max.
CALABRIA_BBOX = (15.55, 37.85, 17.25, 40.20)

PROVINCE = ["Cosenza", "Catanzaro", "Reggio Calabria", "Vibo Valentia", "Crotone"]


# --------------------------------------------------------------------------
# Ingest: FIRMS
# --------------------------------------------------------------------------

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Sorgenti FIRMS usate. VIIRS ha pixel da 375 m, MODIS da 1 km: VIIRS vede
# focolai piu' piccoli, MODIS ha la serie storica piu' lunga. Si usano
# entrambe e si lascia al clustering il compito di unire i doppioni.
FIRMS_SOURCES_NRT = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_NRT"]
FIRMS_SOURCES_ARCHIVE = ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "MODIS_SP"]

# Confidenza minima per tenere un rilevamento. VIIRS classifica in low,
# nominal, high; qui sono mappati su 30, 60, 90.
MIN_CONFIDENCE = 30.0
CONFIDENCE_MAP = {"l": 30.0, "n": 60.0, "h": 90.0}


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------

# DBSCAN su distanza haversine. 1.5 km e' circa quattro volte il pixel VIIRS:
# abbastanza per unire i pixel dello stesso fronte, abbastanza poco da tenere
# separati due incendi distinti sulla stessa collina.
CLUSTER_EPS_KM = 1.5
CLUSTER_MIN_SAMPLES = 1

# Due rilevamenti oltre queste ore di distanza non appartengono allo stesso
# focolaio anche se sono nello stesso punto: il satellite ripassa e trova un
# incendio nuovo, non lo stesso.
CLUSTER_TIME_WINDOW_HOURS = 12.0


# --------------------------------------------------------------------------
# Modello fisico (tarature dichiarate)
# --------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0

# Rapporto fra percorso stradale e distanza in linea d'aria. In pianura vale
# circa 1.2, in montagna sale. La Calabria e' montuosa: Sila, Aspromonte e
# Pollino. 1.45 e' una taratura, non un dato misurato.
ROAD_TORTUOSITY = 1.45

# Litri d'acqua per km quadrato di superficie interessata, a intensita' e
# copertura di riferimento. Taratura, non verita' fisica.
WATER_LITERS_PER_KM2 = 100_000

LAND_COVER_WATER_COEFF = {
    "conifer": 1.4,
    "shrubland": 1.2,
    "mixed_forest": 1.0,
    "broadleaf": 0.9,
    "grassland": 0.6,
    "agriculture": 0.5,
    "urban": 0.8,
    "barren": 0.3,
}

# Un focolaio entro questa distanza dal costruito e' considerato in
# interfaccia urbano-foresta (WUI). Due chilometri e' la soglia usata in molti
# piani AIB regionali.
WUI_THRESHOLD_KM = 2.0
WUI_DECAY_KM = 10.0

# Pesi della priorita' di base. Sommano a 1.
PRIORITY_WEIGHTS = {
    "frp": 0.35,
    "wui": 0.25,
    "area": 0.20,
    "land_cover": 0.10,
    "protected": 0.10,
}

FRP_NORMALISATION_MW = 200.0
AREA_NORMALISATION_KM2 = 5.0


# --------------------------------------------------------------------------
# Pianificazione
# --------------------------------------------------------------------------

DEFAULT_HORIZON_MINUTES = 240
REPLAY_WINDOW_HOURS = 3

# Limite di tempo del solver. Oltre questo si prende la migliore soluzione
# trovata: per un supporto alle decisioni un piano buono subito vale piu' di
# un piano ottimo fra dieci minuti.
SOLVER_MAX_SECONDS = 20.0
SOLVER_RANDOM_SEED = 20250915
SOLVER_NUM_WORKERS = 8
SOLVER_NUM_WORKERS_DETERMINISTIC = 1

# Pesi di default dell'obiettivo, ripesabili dagli ordini del coordinatore
# tramite il vincolo OBJECTIVE_WEIGHT.
OBJECTIVE_WEIGHTS = {
    "coverage": 10.0,
    "wui_protection": 8.0,
    "response_time": 4.0,
    "water_fulfilment": 3.0,
    "cost": 1.0,
}

# Una risposta oltre questo tempo e' considerata tardiva nelle metriche.
LATE_RESPONSE_MINUTES = 45

# Volo notturno: la maggior parte dei mezzi aerei antincendio non opera fra il
# tramonto e l'alba. Orari medi della stagione, in minuti dalla mezzanotte.
DAYLIGHT_START_MINUTE = 5 * 60 + 30
DAYLIGHT_END_MINUTE = 20 * 60 + 30


# --------------------------------------------------------------------------
# Avvertenza
# --------------------------------------------------------------------------

DISCLAIMER = (
    "Tagliafuoco e' un simulatore a scopo di studio. Non e' uno strumento "
    "operativo, non e' certificato e non va usato per dirigere interventi "
    "reali di lotta agli incendi boschivi."
)
