"""Configurazione comune dei test.

Aggiunge `src/` al path (il progetto usa il layout src senza richiedere
l'installazione) e mette a disposizione alcune fixture condivise, in modo che
ogni sottosistema non debba ricostruirsi uno scenario da zero.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tagliafuoco.domain.models import (  # noqa: E402
    Base,
    Detection,
    Domain,
    FireCluster,
    GeoPoint,
    LandCover,
    Scenario,
    Severity,
    Vehicle,
    VehicleKind,
    VehicleStatus,
    WaterSource,
    WaterSourceKind,
)


@pytest.fixture
def instant() -> datetime:
    """Istante fisso di riferimento: i test non devono dipendere dall'orologio."""
    return datetime(2025, 8, 12, 11, 30, tzinfo=timezone.utc)


@pytest.fixture
def base_lamezia() -> Base:
    return Base(
        base_id="BASE_SUF",
        name="Lamezia Terme",
        point=GeoPoint(lat=38.9054, lon=16.2423),
        provincia="Catanzaro",
        supports=[VehicleKind.CANADAIR, VehicleKind.ELICOTTERO_MEDIO],
        turnaround_minutes=12.0,
    )


@pytest.fixture
def base_cosenza() -> Base:
    return Base(
        base_id="BASE_CS",
        name="Cosenza",
        point=GeoPoint(lat=39.2983, lon=16.2533),
        provincia="Cosenza",
        supports=[VehicleKind.ELICOTTERO_MEDIO, VehicleKind.MODULO_AIB],
    )


@pytest.fixture
def sea_source() -> WaterSource:
    return WaterSource(
        source_id="WS_TIRRENO_LAMEZIA",
        name="Golfo di Sant'Eufemia",
        point=GeoPoint(lat=38.9200, lon=16.1500),
        kind=WaterSourceKind.SEA,
        usable_by=[
            VehicleKind.CANADAIR,
            VehicleKind.ELICOTTERO_MEDIO,
            VehicleKind.ELICOTTERO_PESANTE,
            VehicleKind.ELICOTTERO_LEGGERO,
        ],
        refill_minutes=1.5,
    )


@pytest.fixture
def canadair(base_lamezia: Base) -> Vehicle:
    return Vehicle(
        vehicle_id="CAN_01",
        name="Canadair 01",
        kind=VehicleKind.CANADAIR,
        domain=Domain.AIR,
        home_base_id=base_lamezia.base_id,
        cruise_speed_kmh=333.0,
        water_capacity_liters=6137,
        drop_minutes=0.5,
        refill_minutes=1.5,
        setup_minutes=12.0,
        endurance_minutes=240.0,
        max_shift_minutes=480.0,
        cost_per_hour_eur=13000.0,
    )


@pytest.fixture
def elicottero(base_cosenza: Base) -> Vehicle:
    return Vehicle(
        vehicle_id="ELI_01",
        name="Elicottero Cosenza",
        kind=VehicleKind.ELICOTTERO_MEDIO,
        domain=Domain.AIR,
        home_base_id=base_cosenza.base_id,
        cruise_speed_kmh=240.0,
        water_capacity_liters=1500,
        drop_minutes=0.8,
        refill_minutes=2.0,
        setup_minutes=6.0,
        endurance_minutes=150.0,
        cost_per_hour_eur=3200.0,
    )


@pytest.fixture
def modulo_aib(base_cosenza: Base) -> Vehicle:
    return Vehicle(
        vehicle_id="AIB_01",
        name="Modulo AIB Cosenza",
        kind=VehicleKind.MODULO_AIB,
        domain=Domain.GROUND,
        home_base_id=base_cosenza.base_id,
        cruise_speed_kmh=55.0,
        water_capacity_liters=400,
        drop_minutes=6.0,
        refill_minutes=15.0,
        setup_minutes=8.0,
        endurance_minutes=480.0,
        max_shift_minutes=600.0,
        cost_per_hour_eur=180.0,
    )


@pytest.fixture
def fire_sila(instant: datetime) -> FireCluster:
    """Focolaio in Sila, lontano dalle case, copertura a conifere."""
    det = Detection(
        detection_id="D1",
        point=GeoPoint(lat=39.3100, lon=16.5600),
        acquired_at=instant,
        satellite="N",
        instrument="VIIRS",
        confidence=90.0,
        frp_mw=48.0,
        scan_km=0.375,
        track_km=0.375,
    )
    return FireCluster(
        fire_id="F_SILA",
        centroid=det.point,
        detections=[det],
        first_seen=instant,
        last_seen=instant,
        total_frp_mw=48.0,
        max_frp_mw=48.0,
        mean_confidence=90.0,
        area_km2=0.8,
        radius_km=0.5,
        comune="Spezzano della Sila",
        provincia="Cosenza",
        land_cover=LandCover.CONIFER,
        distance_to_wui_km=7.5,
        in_protected_area=True,
        protected_area_name="Parco Nazionale della Sila",
        severity=Severity.HIGH,
        water_demand_liters=120_000,
        base_priority=0.55,
    )


@pytest.fixture
def fire_wui(instant: datetime) -> FireCluster:
    """Focolaio in interfaccia urbano-foresta: il caso che il coordinatore cita."""
    det = Detection(
        detection_id="D2",
        point=GeoPoint(lat=38.1100, lon=15.6800),
        acquired_at=instant,
        satellite="N",
        instrument="VIIRS",
        confidence=85.0,
        frp_mw=22.0,
        scan_km=0.375,
        track_km=0.375,
    )
    return FireCluster(
        fire_id="F_WUI",
        centroid=det.point,
        detections=[det],
        first_seen=instant,
        last_seen=instant,
        total_frp_mw=22.0,
        max_frp_mw=22.0,
        mean_confidence=85.0,
        area_km2=0.4,
        radius_km=0.35,
        comune="Reggio Calabria",
        provincia="Reggio Calabria",
        land_cover=LandCover.SHRUBLAND,
        distance_to_wui_km=0.6,
        nearest_settlement="Gallina",
        population_within_3km=4200,
        severity=Severity.MODERATE,
        water_demand_liters=52_000,
        base_priority=0.72,
    )


@pytest.fixture
def scenario_minimo(
    instant: datetime,
    base_lamezia: Base,
    base_cosenza: Base,
    sea_source: WaterSource,
    canadair: Vehicle,
    elicottero: Vehicle,
    modulo_aib: Vehicle,
    fire_sila: FireCluster,
    fire_wui: FireCluster,
) -> Scenario:
    """Scenario piccolo ma non banale: due focolai diversi, tre mezzi diversi."""
    return Scenario(
        scenario_id="SC_TEST",
        timestamp=instant,
        horizon_minutes=240,
        fires=[fire_sila, fire_wui],
        vehicles=[canadair, elicottero, modulo_aib],
        bases=[base_lamezia, base_cosenza],
        water_sources=[sea_source],
        is_night=False,
        wind_kmh=18.0,
        temperature_c=33.0,
        humidity_pct=28.0,
    )


@pytest.fixture
def scenario_notturno(scenario_minimo: Scenario) -> Scenario:
    """Stesso scenario di notte: i mezzi aerei non devono poter volare."""
    import copy

    sc = copy.deepcopy(scenario_minimo)
    sc.scenario_id = "SC_TEST_NOTTE"
    sc.is_night = True
    for v in sc.vehicles:
        if v.domain == Domain.AIR:
            v.status = VehicleStatus.OUT_OF_SHIFT
    return sc
