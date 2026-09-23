"""Catena di ingestione: dai pixel caldi del satellite agli `Scenario`.

Questo pacchetto e' il solo punto del sistema che conosce il formato di NASA
FIRMS. Tutto cio' che sta a valle (solver, validatore, baseline, valutazione)
lavora sulle entita' di `domain/models.py` e non sa da quale prodotto
satellitare arrivino i dati, ne' se arrivino dalla rete o dalle fixture locali.

La catena, in ordine:

    firms_client   scarica il CSV, oppure legge le fixture se la chiave manca
    normalize      trasforma le righe in Detection, uniformando MODIS e VIIRS
    clustering     raggruppa i pixel in FireCluster nello spazio e nel tempo
    enrichment     aggiunge il contesto territoriale e le grandezze derivate
    scenario_builder  assembla focolai e risorse in uno Scenario

Qui si riesportano solo le funzioni che gli altri sottosistemi devono usare.
Importare direttamente i sottomoduli resta lecito, ma questa lista e' il
contratto: cio' che non compare qui puo' cambiare senza preavviso.
"""

from __future__ import annotations

from tagliafuoco.ingest.clustering import cluster_detections, haversine_km
from tagliafuoco.ingest.enrichment import (
    base_priority,
    classify_land_cover,
    enrich_fire,
    enrich_fires,
    severity_from_priority,
    water_demand_liters,
    wui_factor,
)
from tagliafuoco.ingest.firms_client import fetch_detections, fetch_from_fixtures
from tagliafuoco.ingest.normalize import (
    calabria_bounding_box,
    dedupe_detections,
    make_detection_id,
    parse_csv,
)
from tagliafuoco.ingest.scenario_builder import (
    build_scenario,
    build_scenarios_over_season,
    is_night_at,
    season_dates,
)

__all__ = [
    "base_priority",
    "build_scenario",
    "build_scenarios_over_season",
    "calabria_bounding_box",
    "classify_land_cover",
    "cluster_detections",
    "dedupe_detections",
    "enrich_fire",
    "enrich_fires",
    "fetch_detections",
    "fetch_from_fixtures",
    "haversine_km",
    "is_night_at",
    "make_detection_id",
    "parse_csv",
    "season_dates",
    "severity_from_priority",
    "water_demand_liters",
    "wui_factor",
]
