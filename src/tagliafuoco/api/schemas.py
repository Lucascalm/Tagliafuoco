"""Modelli di trasporto della API: il formato che esce, non le entita' interne.

Questo modulo esiste per una ragione sola, e vale la pena dirla subito perche'
e' la scelta piu' contestata di un adattatore HTTP: le dataclass di dominio non
escono mai dalla API cosi' come sono. Non si serializza un `Plan`, non si
serializza uno `Scenario`, non si serializza un `Vehicle`.

Il motivo e' il verso della dipendenza. `domain/models.py` e' il contratto
interno del progetto e cambia quando cambia il modello del mondo: si aggiunge
un campo al mezzo, si rinomina un attributo del focolaio, si sposta una
grandezza derivata in `eval/`. Se quelle dataclass fossero anche il formato di
rete, ogni modifica interna diventerebbe una modifica di protocollo, e chi
consuma la API (la mappa nel browser, uno script, un altro servizio) si
romperebbe per una rifattorizzazione che non lo riguarda.

Con un modello di trasporto separato la rottura si sposta dove va gestita: in
questo file, dentro una funzione di conversione che i test coprono. Il costo e'
scrivere i convertitori. Il guadagno e' che il formato pubblico diventa una
decisione esplicita invece di un effetto collaterale.

Due deroghe, entrambe dichiarate.

1. `ConstraintSet` di `nlu/schema.py` attraversa il confine per intero, in
   ingresso e in uscita. Non e' una dataclass di dominio: e' gia' un modello
   Pydantic ed e' per costruzione l'unico oggetto pensato per attraversare i
   confini (lo dice il docstring di quel file). Riemetterlo tale e quale
   permette il giro completo: si chiama `/api/traduci`, si guarda il risultato,
   eventualmente si corregge un vincolo a mano, e lo si rimanda a
   `/api/pianifica` senza tradurre una seconda volta.
2. I valori delle enumerazioni viaggiano come stringhe semplici e non come tipi
   chiusi, cosi' aggiungere una categoria di mezzo in `domain/models.py` non
   invalida i client gia' scritti.

Convenzione sui nomi. Le rotte e tutti i testi rivolti a una persona sono in
italiano, come il resto del progetto; i nomi dei campi JSON restano in inglese e
coincidono con quelli del dominio, perche' sono identificatori di programma e
perche' cosi' la conversione resta leggibile a colpo d'occhio.

Ogni risposta porta due campi fissi, `simulation` e `disclaimer`. Tagliafuoco e'
un simulatore di studio, e chi legge la risposta deve saperlo dal primo campo,
non da una nota in fondo a una pagina.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from tagliafuoco.config import DEFAULT_HORIZON_MINUTES, DISCLAIMER
from tagliafuoco.domain.models import (
    Assignment,
    Base,
    FireCluster,
    GeoPoint,
    Plan,
    Scenario,
    Vehicle,
    VehicleStatus,
    WaterSource,
)
from tagliafuoco.nlu.schema import AnyConstraint, ConstraintSet
from tagliafuoco.validate.rules import Violation
from tagliafuoco.validate.validator import (
    MetricComparison,
    RecomputedMetrics,
    ValidationResult,
)

#: Campi che ogni vincolo possiede. Nella risposta stanno in testa, e cio' che
#: resta finisce in `params`: cosi' un vincolo nuovo allarga `params` invece di
#: cambiare la forma della risposta.
_CONSTRAINT_COMMON_FIELDS = frozenset(
    {"kind", "constraint_id", "enforcement", "confidence", "source_span", "rationale"}
)


# --------------------------------------------------------------------------
# Base comune
# --------------------------------------------------------------------------


class Envelope(BaseModel):
    """Campi presenti in ogni risposta della API.

    `simulation` e' sempre vero e non e' pleonastico: un piano di impiego di
    Canadair che arriva via HTTP e' esattamente il genere di dato che qualcuno
    puo' scambiare per operativo. Il campo lo nega all'origine.
    """

    simulation: bool = True
    disclaimer: str = DISCLAIMER


class Point(BaseModel):
    """Punto in WGS84, nello stesso ordine lat, lon del dominio."""

    lat: float
    lon: float

    @classmethod
    def from_domain(cls, p: GeoPoint) -> "Point":
        return cls(lat=p.lat, lon=p.lon)


# --------------------------------------------------------------------------
# Errori
# --------------------------------------------------------------------------


class ErrorOut(Envelope):
    """Corpo di ogni risposta di errore.

    `message` e' in italiano ed e' scritto per una persona; `detail` porta il
    dettaglio tecnico quando c'e'. Tenerli separati evita di dover scegliere fra
    un messaggio inutile e uno illeggibile.
    """

    error: str
    message: str
    detail: Optional[Any] = None


# --------------------------------------------------------------------------
# Salute del servizio
# --------------------------------------------------------------------------


class KeysOut(BaseModel):
    """Quali chiavi opzionali ci sono, e cosa cambia quando mancano."""

    firms: bool
    llm: bool
    detections_source: str
    translator: str


class HealthOut(Envelope):
    """Stato del servizio. E' la prima chiamata che fa qualunque client."""

    status: str = "ok"
    version: str
    keys: KeysOut
    static_data_available: bool
    replays_available: int
    solver_seconds_default: float
    solver_seconds_cap: float


# --------------------------------------------------------------------------
# Dati statici
# --------------------------------------------------------------------------


class BaseOut(BaseModel):
    """Base operativa."""

    base_id: str
    name: str
    point: Point
    provincia: str
    supports: list[str]
    has_night_capability: bool
    turnaround_minutes: float

    @classmethod
    def from_domain(cls, b: Base) -> "BaseOut":
        return cls(
            base_id=b.base_id,
            name=b.name,
            point=Point.from_domain(b.point),
            provincia=b.provincia,
            supports=[k.value for k in b.supports],
            has_night_capability=b.has_night_capability,
            turnaround_minutes=b.turnaround_minutes,
        )


class VehicleOut(BaseModel):
    """Mezzo, con i soli parametri che servono a disegnarlo e a spiegarlo.

    Non escono i campi di stato dinamico della simulazione interna
    (`available_from_minute`, `minutes_flown_today`): appartengono al motore di
    rigiocata, non al protocollo.
    """

    vehicle_id: str
    name: str
    kind: str
    domain: str
    home_base_id: str
    status: str
    cruise_speed_kmh: float
    water_capacity_liters: int
    endurance_minutes: float
    max_shift_minutes: float
    night_capable: bool
    cost_per_hour_eur: float

    @classmethod
    def from_domain(cls, v: Vehicle) -> "VehicleOut":
        return cls(
            vehicle_id=v.vehicle_id,
            name=v.name,
            kind=v.kind.value,
            domain=v.domain.value,
            home_base_id=v.home_base_id,
            status=v.status.value,
            cruise_speed_kmh=v.cruise_speed_kmh,
            water_capacity_liters=v.water_capacity_liters,
            endurance_minutes=v.endurance_minutes,
            max_shift_minutes=v.max_shift_minutes,
            night_capable=v.night_capable,
            cost_per_hour_eur=v.cost_per_hour_eur,
        )


class WaterSourceOut(BaseModel):
    """Punto di approvvigionamento idrico."""

    source_id: str
    name: str
    point: Point
    kind: str
    usable_by: list[str]
    refill_minutes: float

    @classmethod
    def from_domain(cls, w: WaterSource) -> "WaterSourceOut":
        return cls(
            source_id=w.source_id,
            name=w.name,
            point=Point.from_domain(w.point),
            kind=w.kind.value,
            usable_by=[k.value for k in w.usable_by],
            refill_minutes=w.refill_minutes,
        )


class StaticsOut(Envelope):
    """Tutto cio' che la mappa disegna una volta sola, all'avvio."""

    bases: list[BaseOut]
    vehicles: list[VehicleOut]
    water_sources: list[WaterSourceOut]
    counts: dict[str, int]


# --------------------------------------------------------------------------
# Focolai
# --------------------------------------------------------------------------


class FireOut(BaseModel):
    """Focolaio raggruppato e arricchito.

    `is_wui` viaggia gia' risolto: il client non deve conoscere la soglia dei
    due chilometri, che e' una taratura del progetto e puo' cambiare.
    """

    fire_id: str
    centroid: Point
    detection_count: int
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    total_frp_mw: float
    max_frp_mw: float
    mean_confidence: float
    area_km2: float
    radius_km: float
    comune: Optional[str]
    provincia: Optional[str]
    land_cover: str
    elevation_m: Optional[float]
    distance_to_wui_km: Optional[float]
    is_wui: bool
    population_within_3km: int
    in_protected_area: bool
    protected_area_name: Optional[str]
    severity: str
    water_demand_liters: int
    base_priority: float

    @classmethod
    def from_domain(cls, f: FireCluster) -> "FireOut":
        return cls(
            fire_id=f.fire_id,
            centroid=Point.from_domain(f.centroid),
            detection_count=f.detection_count,
            first_seen=f.first_seen,
            last_seen=f.last_seen,
            total_frp_mw=round(f.total_frp_mw, 3),
            max_frp_mw=round(f.max_frp_mw, 3),
            mean_confidence=round(f.mean_confidence, 2),
            area_km2=round(f.area_km2, 4),
            radius_km=round(f.radius_km, 4),
            comune=f.comune,
            provincia=f.provincia,
            land_cover=f.land_cover.value,
            elevation_m=f.elevation_m,
            distance_to_wui_km=f.distance_to_wui_km,
            is_wui=f.is_wui,
            population_within_3km=f.population_within_3km,
            in_protected_area=f.in_protected_area,
            protected_area_name=f.protected_area_name,
            severity=f.severity.value,
            water_demand_liters=f.water_demand_liters,
            base_priority=round(f.base_priority, 4),
        )


class FiresOut(Envelope):
    """Focolai di un periodo, con il conto dei rilevamenti da cui vengono.

    `notes` non e' diagnostica: dice a chi legge cosa significa il numero che
    sta guardando. Una lista vuota di focolai puo' voler dire "in quel periodo
    non e' bruciato niente" oppure "stai interrogando un periodo che le fixture
    non coprono", e sono due cose diverse che dal solo `fires_total: 0` non si
    distinguono.
    """

    start: str
    end: str
    detections_total: int
    fires_total: int
    fires_returned: int
    source: str
    fires: list[FireOut]
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Traduzione dell'ordine
# --------------------------------------------------------------------------


class TranslateIn(BaseModel):
    """Richiesta di traduzione di un ordine del coordinatore."""

    model_config = ConfigDict(extra="forbid")

    order: str = Field(min_length=1, max_length=4000)
    instant: Optional[datetime] = Field(
        default=None,
        description=(
            "Istante di riferimento. Se c'e', l'ordine viene tradotto avendo "
            "sotto gli occhi lo scenario di quel momento, cosi' frasi come "
            "'l'elicottero di Cosenza' si risolvono su mezzi reali."
        ),
    )
    horizon_minutes: int = Field(default=DEFAULT_HORIZON_MINUTES, ge=30, le=1440)


class ConstraintOut(BaseModel):
    """Un vincolo tradotto, nella forma in cui va mostrato al coordinatore.

    I campi comuni stanno in testa perche' sono quelli che si leggono; il
    contenuto specifico finisce in `params` come oggetto libero. Cosi' il
    formato non cambia quando il repertorio si allarga, e `source_span` resta
    sempre in vista: e' il frammento dell'ordine da cui il vincolo e' stato
    tratto, ed e' cio' che permette al coordinatore di smentirlo prima che
    entri nel solver.
    """

    constraint_id: str
    kind: str
    enforcement: str
    confidence: float
    source_span: str
    rationale: str
    params: dict[str, Any]

    @classmethod
    def from_domain(cls, c: AnyConstraint) -> "ConstraintOut":
        dumped = c.model_dump(mode="json")
        params = {k: v for k, v in dumped.items() if k not in _CONSTRAINT_COMMON_FIELDS}
        return cls(
            constraint_id=c.constraint_id,
            kind=c.kind.value,
            enforcement=c.enforcement.value,
            confidence=c.confidence,
            source_span=c.source_span,
            rationale=c.rationale,
            params=params,
        )


class TranslationOut(Envelope):
    """Esito della traduzione.

    `translator` dice da dove viene il risultato ed e' informazione di prima
    classe, non diagnostica. Senza `ANTHROPIC_API_KEY` la traduzione arriva dal
    traduttore a regole, e chi guarda la demo deve poterlo vedere invece di
    credere che stia parlando un modello linguistico.

    `constraint_set` e' il `ConstraintSet` completo, riemesso tale e quale
    perche' possa tornare a `/api/pianifica` senza essere ritradotto.
    """

    order: str
    translator: str
    model_name: str
    translation_ms: float
    llm_available: bool
    constraint_count: int
    unsupported_count: int
    warnings: list[str]
    summary_lines: list[str]
    constraints: list[ConstraintOut]
    constraint_set: ConstraintSet

    @classmethod
    def from_domain(cls, cs: ConstraintSet, *, llm_available: bool) -> "TranslationOut":
        return cls(
            order=cs.source_text,
            translator=cs.translator,
            model_name=cs.model_name,
            translation_ms=cs.translation_ms,
            llm_available=llm_available,
            constraint_count=len(cs.constraints),
            unsupported_count=len(cs.unsupported),
            warnings=list(cs.warnings),
            summary_lines=cs.summary_lines(),
            constraints=[ConstraintOut.from_domain(c) for c in cs.constraints],
            constraint_set=cs,
        )


# --------------------------------------------------------------------------
# Scenario in ingresso
# --------------------------------------------------------------------------


class FireIn(BaseModel):
    """Focolaio dichiarato dal client, per scenari costruiti a mano.

    Serve alla demo che vuole far comparire un incendio dove decide l'utente, e
    alle prove: e' il modo di ottenere uno scenario riproducibile senza
    dipendere da quello che c'era nelle fixture quel giorno. Il contesto
    territoriale lo riempie l'arricchimento, come per un focolaio satellitare.
    """

    model_config = ConfigDict(extra="forbid")

    fire_id: Optional[str] = Field(default=None, max_length=64)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    frp_mw: float = Field(default=20.0, ge=0.0, le=10_000.0)
    area_km2: float = Field(default=0.5, gt=0.0, le=500.0)
    confidence: float = Field(default=80.0, ge=0.0, le=100.0)


class ScenarioIn(BaseModel):
    """Come costruire lo scenario da pianificare.

    Due modi alternativi: si danno i focolai, oppure si da' un istante e i
    focolai li mette il sistema raggruppando i rilevamenti visibili fino a quel
    momento. Il secondo e' il caso della demo sulla stagione, il primo e' il
    caso di chi vuole studiare una configurazione precisa.
    """

    model_config = ConfigDict(extra="forbid")

    instant: Optional[datetime] = None
    horizon_minutes: int = Field(default=DEFAULT_HORIZON_MINUTES, ge=30, le=1440)
    lookback_hours: float = Field(default=12.0, gt=0.0, le=168.0)
    fires: Optional[list[FireIn]] = Field(default=None, max_length=200)
    vehicle_ids: Optional[list[str]] = Field(
        default=None,
        max_length=200,
        description=(
            "Sottoinsieme della flotta da rendere disponibile. Serve alle "
            "ablazioni: stessa giornata, meno mezzi."
        ),
    )
    is_night: Optional[bool] = Field(
        default=None,
        description="Forza la condizione notturna invece di dedurla dall'ora.",
    )
    max_fires: int = Field(
        default=40,
        ge=1,
        le=200,
        description=(
            "Quanti focolai al massimo entrano nello scenario, presi in ordine "
            "di priorita'. E' un tetto sul costo della richiesta, non un "
            "giudizio: i focolai scartati sono elencati nelle note."
        ),
    )


class ScenarioOut(BaseModel):
    """Riassunto dello scenario effettivamente pianificato.

    Non contiene i focolai: quelli stanno accanto, in `fires`, perche' la stessa
    lista serve alla mappa e ripeterla annidata non aiuta nessuno.
    """

    scenario_id: str
    timestamp: datetime
    horizon_minutes: int
    fires_total: int
    fires_wui: int
    vehicles_total: int
    vehicles_available: int
    bases_total: int
    water_sources_total: int
    is_night: bool
    wind_kmh: float
    temperature_c: float
    humidity_pct: float

    @classmethod
    def from_domain(cls, s: Scenario) -> "ScenarioOut":
        return cls(
            scenario_id=s.scenario_id,
            timestamp=s.timestamp,
            horizon_minutes=s.horizon_minutes,
            fires_total=len(s.fires),
            fires_wui=sum(1 for f in s.fires if f.is_wui),
            vehicles_total=len(s.vehicles),
            vehicles_available=sum(
                1 for v in s.vehicles if v.status is VehicleStatus.AVAILABLE
            ),
            bases_total=len(s.bases),
            water_sources_total=len(s.water_sources),
            is_night=s.is_night,
            wind_kmh=s.wind_kmh,
            temperature_c=s.temperature_c,
            humidity_pct=s.humidity_pct,
        )


# --------------------------------------------------------------------------
# Piano
# --------------------------------------------------------------------------


class AssignmentOut(BaseModel):
    """Una riga del piano.

    `vehicle_name` e `fire_comune` sono ridondanti rispetto agli
    identificatori, e lo sono apposta: chi disegna una tabella non deve
    incrociare tre risposte per scrivere "Canadair 01 su Longobucco".
    """

    vehicle_id: str
    vehicle_name: str
    fire_id: str
    fire_comune: Optional[str]
    departure_minute: float
    arrival_minute: float
    release_minute: float
    response_minutes: float
    engaged_minutes: float
    sorties: int
    water_delivered_liters: int
    water_source_id: Optional[str]
    estimated_cost_eur: float
    note: str

    @classmethod
    def from_domain(
        cls,
        a: Assignment,
        *,
        vehicle_names: dict[str, str],
        fire_comuni: dict[str, Optional[str]],
    ) -> "AssignmentOut":
        return cls(
            vehicle_id=a.vehicle_id,
            vehicle_name=vehicle_names.get(a.vehicle_id, a.vehicle_id),
            fire_id=a.fire_id,
            fire_comune=fire_comuni.get(a.fire_id),
            departure_minute=round(a.departure_minute, 2),
            arrival_minute=round(a.arrival_minute, 2),
            release_minute=round(a.release_minute, 2),
            response_minutes=round(a.response_minutes, 2),
            engaged_minutes=round(a.engaged_minutes, 2),
            sorties=a.sorties,
            water_delivered_liters=a.water_delivered_liters,
            water_source_id=a.water_source_id,
            estimated_cost_eur=round(a.estimated_cost_eur, 2),
            note=a.note,
        )


class PlanOut(BaseModel):
    """Piano di impiego, nel formato di rete."""

    plan_id: str
    producer: str
    solver_status: str
    solve_seconds: float
    objective_value: Optional[float]
    horizon_minutes: int
    constraint_set_id: Optional[str]
    assignments: list[AssignmentOut]
    unassigned_fire_ids: list[str]
    vehicles_used: int
    notes: list[str]

    @classmethod
    def from_domain(cls, plan: Plan, scenario: Scenario) -> "PlanOut":
        vehicle_names = {v.vehicle_id: v.name for v in scenario.vehicles}
        fire_comuni: dict[str, Optional[str]] = {
            f.fire_id: f.comune for f in scenario.fires
        }
        return cls(
            plan_id=plan.plan_id,
            producer=plan.producer,
            solver_status=plan.solver_status,
            solve_seconds=round(plan.solve_seconds, 4),
            objective_value=plan.objective_value,
            horizon_minutes=plan.horizon_minutes,
            constraint_set_id=plan.constraint_set_id,
            assignments=[
                AssignmentOut.from_domain(
                    a, vehicle_names=vehicle_names, fire_comuni=fire_comuni
                )
                for a in plan.assignments
            ],
            unassigned_fire_ids=list(plan.unassigned_fire_ids),
            vehicles_used=len({a.vehicle_id for a in plan.assignments}),
            notes=list(plan.notes),
        )


# --------------------------------------------------------------------------
# Validazione
# --------------------------------------------------------------------------


class ViolationOut(BaseModel):
    """Una contestazione del validatore."""

    rule: str
    severity: str
    message: str
    entities: dict[str, str]
    expected: Optional[Any]
    found: Optional[Any]
    delta: Optional[float]

    @classmethod
    def from_domain(cls, v: Violation) -> "ViolationOut":
        d = v.to_dict()
        return cls(
            rule=d["rule"],
            severity=d["severity"],
            message=d["message"],
            entities=d["entities"],
            expected=d["expected"],
            found=d["found"],
            delta=d["delta"],
        )


class MetricsOut(BaseModel):
    """Le metriche del piano ricalcolate dal validatore, non quelle dichiarate.

    E' una scelta e va dichiarata: la API non pubblica i numeri che il
    pianificatore dice di aver ottenuto, pubblica quelli che un secondo
    programma ha ricostruito da zero. Se i due divergono, la divergenza si legge
    in `disagreements` invece di sparire.
    """

    fires_total: int
    fires_covered: int
    coverage_ratio: float
    fires_wui_total: int
    fires_wui_covered: int
    wui_coverage_ratio: float
    water_demanded_liters: float
    water_deliverable_liters: float
    water_fulfilment_ratio: float
    mean_response_minutes: float
    p90_response_minutes: float
    total_vehicle_minutes: float
    total_cost_eur: float
    vehicles_used: int
    assignments_total: int
    assignments_feasible: int

    @classmethod
    def from_domain(cls, m: RecomputedMetrics) -> "MetricsOut":
        d = m.to_dict()
        return cls(**{name: d[name] for name in cls.model_fields})


class ComparisonOut(BaseModel):
    """Una metrica dichiarata dal piano, messa accanto a quella ricalcolata."""

    name: str
    declared: Optional[float]
    recomputed: float
    delta: Optional[float]
    tolerance: float
    unit: str
    agrees: bool

    @classmethod
    def from_domain(cls, c: MetricComparison) -> "ComparisonOut":
        return cls(**c.to_dict())


class ValidationOut(BaseModel):
    """Esito della validazione indipendente."""

    plan_id: str
    scenario_id: str
    is_valid: bool
    validator_version: str
    summary_line: str
    blocking_count: int
    warning_count: int
    rules_run: list[str]
    violations: list[ViolationOut]
    metrics: MetricsOut
    disagreements: list[ComparisonOut]

    @classmethod
    def from_domain(cls, r: ValidationResult) -> "ValidationOut":
        return cls(
            plan_id=r.plan_id,
            scenario_id=r.scenario_id,
            is_valid=r.is_valid,
            validator_version=r.validator_version,
            summary_line=r.summary_line(),
            blocking_count=len(r.blocking),
            warning_count=len(r.warnings),
            rules_run=list(r.rules_run),
            violations=[ViolationOut.from_domain(v) for v in r.violations],
            metrics=MetricsOut.from_domain(r.metrics),
            disagreements=[ComparisonOut.from_domain(c) for c in r.disagreements],
        )


# --------------------------------------------------------------------------
# Confronto con la baseline
# --------------------------------------------------------------------------


class ScoreOut(BaseModel):
    """Il pugno di numeri su cui si confrontano due piani.

    Vengono tutti dal validatore, per entrambi i piani. Confrontare un piano
    sulle metriche che dichiara di aver ottenuto contro un altro misurato da
    fuori sarebbe un confronto truccato a favore del primo.
    """

    producer: str
    coverage_ratio: float
    wui_coverage_ratio: float
    mean_response_minutes: float
    p90_response_minutes: float
    water_fulfilment_ratio: float
    total_vehicle_minutes: float
    total_cost_eur: float
    vehicles_used: int
    assignments_feasible: int
    is_valid: bool

    @classmethod
    def from_domain(cls, plan: Plan, result: ValidationResult) -> "ScoreOut":
        m = result.metrics
        return cls(
            producer=plan.producer,
            coverage_ratio=round(m.coverage_ratio, 4),
            wui_coverage_ratio=round(m.wui_coverage_ratio, 4),
            mean_response_minutes=round(m.mean_response_minutes, 2),
            p90_response_minutes=round(m.p90_response_minutes, 2),
            water_fulfilment_ratio=round(m.water_fulfilment_ratio, 4),
            total_vehicle_minutes=round(m.total_vehicle_minutes, 2),
            total_cost_eur=round(m.total_cost_eur, 2),
            vehicles_used=m.vehicles_used,
            assignments_feasible=m.assignments_feasible,
            is_valid=result.is_valid,
        )


#: Metriche su cui si calcola la differenza fra solver e baseline.
COMPARED_METRICS: tuple[str, ...] = (
    "coverage_ratio",
    "wui_coverage_ratio",
    "mean_response_minutes",
    "p90_response_minutes",
    "water_fulfilment_ratio",
    "total_vehicle_minutes",
    "total_cost_eur",
)


class BaselineComparisonOut(BaseModel):
    """Solver contro regola banale, sugli stessi dati e con lo stesso seed.

    `delta` e' sempre solver meno baseline. Il segno che conviene guardare
    dipende dalla metrica: su copertura e acqua si vuole positivo, su tempo di
    risposta, ore-mezzo e costo si vuole negativo. Un campo `better` non c'e'
    apposta: dire quale piano e' migliore e' un giudizio, e un adattatore HTTP
    non e' il posto dove darlo.
    """

    baseline_name: str
    solver: ScoreOut
    baseline: ScoreOut
    delta: dict[str, float]
    baseline_plan: PlanOut
    baseline_validation: ValidationOut

    @classmethod
    def build(
        cls,
        *,
        baseline_name: str,
        solver_plan: Plan,
        solver_validation: ValidationResult,
        baseline_plan: Plan,
        baseline_validation: ValidationResult,
        scenario: Scenario,
    ) -> "BaselineComparisonOut":
        solver_score = ScoreOut.from_domain(solver_plan, solver_validation)
        baseline_score = ScoreOut.from_domain(baseline_plan, baseline_validation)
        delta = {
            name: round(
                getattr(solver_score, name) - getattr(baseline_score, name), 4
            )
            for name in COMPARED_METRICS
        }
        return cls(
            baseline_name=baseline_name,
            solver=solver_score,
            baseline=baseline_score,
            delta=delta,
            baseline_plan=PlanOut.from_domain(baseline_plan, scenario),
            baseline_validation=ValidationOut.from_domain(baseline_validation),
        )


# --------------------------------------------------------------------------
# Pianificazione
# --------------------------------------------------------------------------


class PlanIn(BaseModel):
    """Richiesta completa di pianificazione.

    L'ordine si puo' dare in due modi: come testo, e allora viene tradotto qui,
    oppure gia' tradotto in `constraint_set`, e allora il traduttore non viene
    nemmeno chiamato. Il secondo modo serve al caso in cui il coordinatore ha
    corretto a mano un vincolo dopo averlo visto.
    """

    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioIn = Field(default_factory=ScenarioIn)
    order: str = Field(default="", max_length=4000)
    constraint_set: Optional[ConstraintSet] = None
    baseline: str = Field(default="baseline_nearest")
    max_seconds: Optional[float] = Field(
        default=None,
        gt=0.0,
        le=600.0,
        description=(
            "Limite di tempo del risolutore per questa richiesta. Viene "
            "comunque ridotto al tetto del servizio: un endpoint che puo' "
            "bloccarsi per minuti non e' un endpoint."
        ),
    )
    deterministic: bool = Field(
        default=True,
        description=(
            "Con il determinismo il risolutore usa un solo thread e un seed "
            "fissato: piu' lento, ma due chiamate identiche danno lo stesso "
            "piano. Per una demo confrontabile e' cio' che si vuole."
        ),
    )
    include_baseline: bool = True


class PlanResponse(Envelope):
    """La risposta che tiene insieme tutta la catena.

    E' l'endpoint centrale della demo, e per questo restituisce in un colpo solo
    l'ordine, i vincoli che ne sono stati tratti, lo scenario, il piano, l'esito
    della validazione indipendente e il confronto con la baseline. Spezzarlo in
    cinque chiamate costringerebbe il client a ricostruire una sequenza che ha
    senso solo tutta intera, e a gestire il caso in cui la terza fallisce dopo
    che le prime due sono riuscite.
    """

    order: str
    translation: TranslationOut
    scenario: ScenarioOut
    fires: list[FireOut]
    plan: PlanOut
    validation: ValidationOut
    comparison: Optional[BaselineComparisonOut]
    solver_seconds_requested: Optional[float]
    solver_seconds_used: float
    notes: list[str]


# --------------------------------------------------------------------------
# Rigiocata della stagione
# --------------------------------------------------------------------------


class ReplayIndexEntry(BaseModel):
    """Una rigiocata presente sul disco, senza il suo contenuto.

    `producer` e `windows` sono nulli per le rigiocate che l'indice non ha
    aperto: il nome del file porta l'etichetta e l'intervallo, non chi ha
    pianificato ne' quante finestre contiene. Restano nulli invece di ereditare
    l'etichetta, che e' un'altra cosa: chi sceglie una rigiocata dall'indice
    deve poter distinguere "prodotta da cpsat" da "non lo so, aprila".
    """

    name: str
    label: str
    producer: Optional[str]
    start: Optional[str]
    end: Optional[str]
    windows: Optional[int]
    size_bytes: int
    modified_at: datetime


class ReplayOut(Envelope):
    """I risultati precalcolati di una rigiocata della stagione.

    La stagione non si rigioca mentre qualcuno guarda: ci vogliono minuti. La si
    calcola una volta con `tagliafuoco rigioca` e la si serve da qui, che e' la
    stessa divisione fra dati precalcolati e API viva descritta in
    `docs/13-la-mappa-e-il-frontend.md`.

    `windows` e' la serie che il timelapse scorre, e puo' essere grossa: esiste
    `includi_finestre=false` per avere solo i totali. `windows_count` e'
    sempre quanto la rigiocata contiene, `windows_returned` quanto questa
    risposta porta: senza il secondo, un client che ha chiesto un tetto non ha
    modo di sapere se sta guardando la stagione intera o il suo inizio.
    """

    name: str
    label: str
    producer: str
    start: Optional[str]
    end: Optional[str]
    window_hours: Optional[float]
    horizon_minutes: Optional[int]
    aggregate: dict[str, Any]
    windows_count: int
    windows_returned: int
    windows: Optional[list[dict[str, Any]]]
    available: list[ReplayIndexEntry]
