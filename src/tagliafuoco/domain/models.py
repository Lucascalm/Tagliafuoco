"""Modelli di dominio di Tagliafuoco.

Questo modulo e' il CONTRATTO condiviso da tutti i sottosistemi (ingest, nlu,
optimize, validate, baseline, sim, eval, api). Nessun altro modulo puo'
ridefinire queste entita': tutti le importano da qui.

Regola di progetto: i modelli sono *dati*, non comportamento. L'unica logica
ammessa qui sono proprieta' derivate pure e deterministiche. Ogni calcolo
fisico (tempi di volo, cicli d'acqua, consumi) vive due volte e in due posti
separati: in `optimize/` per costruire il modello CP-SAT e in `validate/` per
ricalcolarlo in modo indipendente. Non si condivide quel codice: se le due
implementazioni divergono, il validatore deve poterlo scoprire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------
# Enumerazioni
# --------------------------------------------------------------------------


class VehicleKind(str, Enum):
    """Categoria operativa del mezzo.

    La distinzione aereo/terra non e' cosmetica: cambia il modello fisico.
    Un mezzo aereo fa cicli pescaggio-sgancio e ha autonomia in minuti di
    volo; un mezzo terrestre si sposta su strada, si rifornisce da idranti e
    ha un limite di turno in ore.
    """

    CANADAIR = "canadair"
    ELICOTTERO_PESANTE = "elicottero_pesante"
    ELICOTTERO_MEDIO = "elicottero_medio"
    ELICOTTERO_LEGGERO = "elicottero_leggero"
    AUTOBOTTE = "autobotte"
    MODULO_AIB = "modulo_aib"
    SQUADRA_TERRA = "squadra_terra"


class VehicleStatus(str, Enum):
    AVAILABLE = "available"
    ASSIGNED = "assigned"
    RESERVE = "reserve"
    MAINTENANCE = "maintenance"
    OUT_OF_SHIFT = "out_of_shift"


class Domain(str, Enum):
    """Dominio di movimento: determina quale modello fisico si applica."""

    AIR = "air"
    GROUND = "ground"


class WaterSourceKind(str, Enum):
    SEA = "sea"
    LAKE = "lake"
    RIVER = "river"
    HYDRANT = "hydrant"
    TANK = "tank"


class LandCover(str, Enum):
    """Classi semplificate di copertura del suolo (base CORINE aggregata)."""

    CONIFER = "conifer"
    BROADLEAF = "broadleaf"
    MIXED_FOREST = "mixed_forest"
    SHRUBLAND = "shrubland"
    GRASSLAND = "grassland"
    AGRICULTURE = "agriculture"
    URBAN = "urban"
    BARREN = "barren"


class Severity(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    EXTREME = "extreme"


# --------------------------------------------------------------------------
# Geografia
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GeoPoint:
    """Punto geografico in WGS84 (EPSG:4326). lat e lon in gradi decimali."""

    lat: float
    lon: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.lat, self.lon)


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def contains(self, p: GeoPoint) -> bool:
        return (
            self.min_lat <= p.lat <= self.max_lat
            and self.min_lon <= p.lon <= self.max_lon
        )


# --------------------------------------------------------------------------
# Rilevamenti satellitari e focolai
# --------------------------------------------------------------------------


@dataclass
class Detection:
    """Un singolo pixel caldo rilevato da un sensore satellitare.

    Corrisponde a una riga del CSV di NASA FIRMS. Un rilevamento NON e' un
    incendio: e' l'osservazione di un pixel la cui radianza nell'infrarosso
    medio supera quella dello sfondo. Serve il clustering per passare dai
    pixel ai focolai.
    """

    detection_id: str
    point: GeoPoint
    acquired_at: datetime
    satellite: str
    instrument: str
    confidence: float
    frp_mw: float
    brightness_k: Optional[float] = None
    scan_km: Optional[float] = None
    track_km: Optional[float] = None
    daynight: str = "D"
    version: str = ""

    @property
    def pixel_area_km2(self) -> float:
        """Area nominale del pixel. Serve a stimare l'area percorsa."""
        if self.scan_km and self.track_km:
            return self.scan_km * self.track_km
        return 1.0


@dataclass
class FireCluster:
    """Un focolaio: gruppo di rilevamenti vicini nello spazio e nel tempo.

    E' l'unita' di lavoro del solver. Le grandezze operative (fabbisogno
    d'acqua, priorita') derivano dagli attributi osservati piu' dal contesto
    territoriale (vicinanza alle case, copertura del suolo, area protetta).
    """

    fire_id: str
    centroid: GeoPoint
    detections: list[Detection] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    # --- attributi osservati --------------------------------------------
    total_frp_mw: float = 0.0
    max_frp_mw: float = 0.0
    mean_confidence: float = 0.0
    area_km2: float = 0.0
    radius_km: float = 0.0

    # --- contesto territoriale (riempito da ingest/enrichment.py) --------
    comune: Optional[str] = None
    provincia: Optional[str] = None
    land_cover: LandCover = LandCover.SHRUBLAND
    elevation_m: Optional[float] = None
    slope_deg: Optional[float] = None
    distance_to_wui_km: Optional[float] = None
    nearest_settlement: Optional[str] = None
    population_within_3km: int = 0
    in_protected_area: bool = False
    protected_area_name: Optional[str] = None

    # --- grandezze derivate (riempite da ingest/enrichment.py) -----------
    severity: Severity = Severity.MODERATE
    water_demand_liters: int = 0
    base_priority: float = 0.0

    @property
    def detection_count(self) -> int:
        return len(self.detections)

    @property
    def is_wui(self) -> bool:
        """Vero se il focolaio e' in interfaccia urbano-foresta.

        Soglia convenzionale: 2 km dal costruito. E' la variabile che il
        coordinatore cita piu' spesso ("prima le zone vicino alle case").
        """
        return self.distance_to_wui_km is not None and self.distance_to_wui_km <= 2.0


# --------------------------------------------------------------------------
# Risorse
# --------------------------------------------------------------------------


@dataclass
class WaterSource:
    """Punto di approvvigionamento idrico."""

    source_id: str
    name: str
    point: GeoPoint
    kind: WaterSourceKind
    usable_by: list[VehicleKind] = field(default_factory=list)
    refill_minutes: float = 2.0
    capacity_liters: Optional[int] = None
    seasonal_reliability: float = 1.0


@dataclass
class Base:
    """Base operativa: aeroporto, elisuperficie o distaccamento."""

    base_id: str
    name: str
    point: GeoPoint
    provincia: str
    supports: list[VehicleKind] = field(default_factory=list)
    has_night_capability: bool = False
    turnaround_minutes: float = 10.0


@dataclass
class Vehicle:
    """Mezzo antincendio.

    I parametri sono dichiarati qui una sola volta e letti sia dal solver sia
    dal validatore. Cio' che NON e' condiviso e' il modo di usarli: come si
    trasformano in tempi e litri e' responsabilita' separata dei due moduli.
    """

    vehicle_id: str
    name: str
    kind: VehicleKind
    domain: Domain
    home_base_id: str
    status: VehicleStatus = VehicleStatus.AVAILABLE

    # --- prestazioni -----------------------------------------------------
    cruise_speed_kmh: float = 200.0
    water_capacity_liters: int = 6000
    drop_minutes: float = 1.0
    refill_minutes: float = 2.0
    setup_minutes: float = 5.0
    endurance_minutes: float = 180.0
    max_shift_minutes: float = 480.0
    night_capable: bool = False
    cost_per_hour_eur: float = 1000.0

    # --- vincoli operativi -----------------------------------------------
    min_visibility_ok: bool = True
    max_wind_kmh: float = 50.0
    requires_water_source: bool = True

    # --- stato dinamico nella simulazione ---------------------------------
    current_position: Optional[GeoPoint] = None
    available_from_minute: float = 0.0
    minutes_flown_today: float = 0.0

    @property
    def is_aerial(self) -> bool:
        return self.domain == Domain.AIR


# --------------------------------------------------------------------------
# Piano
# --------------------------------------------------------------------------


@dataclass
class Assignment:
    """Assegnazione di un mezzo a un focolaio dentro un piano.

    I tempi sono in minuti dall'inizio dell'orizzonte di pianificazione, non
    in orari assoluti: rende il piano confrontabile fra finestre diverse e
    indipendente dal fuso.
    """

    vehicle_id: str
    fire_id: str
    departure_minute: float
    arrival_minute: float
    sorties: int
    water_delivered_liters: int
    release_minute: float
    water_source_id: Optional[str] = None
    estimated_cost_eur: float = 0.0
    note: str = ""

    @property
    def response_minutes(self) -> float:
        return self.arrival_minute - self.departure_minute

    @property
    def engaged_minutes(self) -> float:
        return self.release_minute - self.departure_minute


@dataclass
class PlanMetrics:
    """Metriche calcolate sul piano. Popolate da eval/metrics.py."""

    fires_total: int = 0
    fires_covered: int = 0
    fires_wui_total: int = 0
    fires_wui_covered: int = 0
    water_demanded_liters: int = 0
    water_delivered_liters: int = 0
    mean_response_minutes: float = 0.0
    p90_response_minutes: float = 0.0
    total_vehicle_minutes: float = 0.0
    total_cost_eur: float = 0.0
    vehicles_used: int = 0

    @property
    def coverage_ratio(self) -> float:
        return self.fires_covered / self.fires_total if self.fires_total else 0.0

    @property
    def wui_coverage_ratio(self) -> float:
        if not self.fires_wui_total:
            return 1.0
        return self.fires_wui_covered / self.fires_wui_total

    @property
    def water_fulfilment_ratio(self) -> float:
        if not self.water_demanded_liters:
            return 1.0
        return min(1.0, self.water_delivered_liters / self.water_demanded_liters)


@dataclass
class Plan:
    """Piano di impiego prodotto da un pianificatore (solver o baseline)."""

    plan_id: str
    horizon_minutes: int
    assignments: list[Assignment] = field(default_factory=list)
    unassigned_fire_ids: list[str] = field(default_factory=list)
    producer: str = "cpsat"
    solver_status: str = ""
    solve_seconds: float = 0.0
    objective_value: Optional[float] = None
    constraint_set_id: Optional[str] = None
    metrics: Optional[PlanMetrics] = None
    notes: list[str] = field(default_factory=list)

    def assignments_for_vehicle(self, vehicle_id: str) -> list[Assignment]:
        return sorted(
            (a for a in self.assignments if a.vehicle_id == vehicle_id),
            key=lambda a: a.departure_minute,
        )

    def assignments_for_fire(self, fire_id: str) -> list[Assignment]:
        return [a for a in self.assignments if a.fire_id == fire_id]


# --------------------------------------------------------------------------
# Scenario: la fotografia che entra nel pianificatore
# --------------------------------------------------------------------------


@dataclass
class Scenario:
    """Tutto cio' che serve per produrre un piano in un dato istante.

    Uno scenario e' immutabile per costruzione durante la pianificazione:
    solver, baseline e validatore devono vedere esattamente gli stessi dati,
    altrimenti il confronto non significa nulla.
    """

    scenario_id: str
    timestamp: datetime
    horizon_minutes: int
    fires: list[FireCluster] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)
    bases: list[Base] = field(default_factory=list)
    water_sources: list[WaterSource] = field(default_factory=list)
    is_night: bool = False
    wind_kmh: float = 15.0
    temperature_c: float = 30.0
    humidity_pct: float = 35.0

    def fire_by_id(self, fire_id: str) -> Optional[FireCluster]:
        return next((f for f in self.fires if f.fire_id == fire_id), None)

    def vehicle_by_id(self, vehicle_id: str) -> Optional[Vehicle]:
        return next((v for v in self.vehicles if v.vehicle_id == vehicle_id), None)

    def base_by_id(self, base_id: str) -> Optional[Base]:
        return next((b for b in self.bases if b.base_id == base_id), None)

    def water_source_by_id(self, source_id: str) -> Optional[WaterSource]:
        return next((w for w in self.water_sources if w.source_id == source_id), None)
