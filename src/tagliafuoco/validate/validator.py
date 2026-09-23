"""Punto di ingresso del validatore indipendente.

PERCHE' QUESTO FILE ESISTE
--------------------------
Un test verifica che il codice faccia quello che l'autore del codice si
aspettava. Un validatore indipendente verifica che il RISULTATO sia coerente
con la specifica, chiunque lo abbia prodotto e in qualunque modo. La
differenza conta perche' il solver e' una scatola: CP-SAT restituisce OPTIMAL
anche quando il modello che gli e' stato dato e' sbagliato, e quella parola
non e' una garanzia sul mondo, e' una garanzia sul modello. Il validatore e'
il posto dove si controlla il mondo.

`validate_plan` fa tre cose in fila:

1. esegue tutte le regole di `rules.py` e raccoglie le violazioni;
2. ricalcola le metriche del piano da zero, usando solo `physics.py`;
3. confronta cio' che il piano DICHIARA con cio' che il validatore CALCOLA.

Il terzo punto e' quello che i test non fanno mai: un piano puo' essere
perfettamente eseguibile e dichiarare metriche gonfiate, e sono le metriche a
finire nella valutazione sperimentale. Separare "il piano e' eseguibile" da
"il piano dice il vero su se stesso" e' la ragione per cui esistono due
famiglie di violazioni, bloccanti e avvisi.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil, isfinite
from typing import Any, Optional

from tagliafuoco.domain.models import Plan, Scenario
from tagliafuoco.nlu.schema import ConstraintSet
from tagliafuoco.validate import physics
from tagliafuoco.validate.rules import (
    ALL_RULES,
    DEFAULT_TOLERANCE,
    Tolerance,
    ValidationSeverity,
    Violation,
    arrival_floors,
)

__all__ = [
    "MetricComparison",
    "RecomputedMetrics",
    "Tolerance",
    "DEFAULT_TOLERANCE",
    "ValidationResult",
    "ValidationSeverity",
    "Violation",
    "validate_plan",
]

VALIDATOR_VERSION = "1.0"


# --------------------------------------------------------------------------
# Metriche ricalcolate
# --------------------------------------------------------------------------


@dataclass
class RecomputedMetrics:
    """Le metriche del piano, ricostruite dal validatore senza fidarsi di nulla.

    Ogni campo ha un gemello dichiarato dal piano, e i due non coincidono per
    forza. La copertura ne e' l'esempio piu' chiaro: il piano conta coperto un
    focolaio a cui ha assegnato un mezzo, il validatore lo conta coperto solo
    se quell'assegnazione sopravvive alle regole. Un mezzo che arriva
    teletrasportandosi non copre niente.

    Due avvertenze su come leggere questi numeri, perche' senza sono
    fuorvianti.

    CONVENZIONE DICHIARATA SUL TEMPO DI RISPOSTA. Si misura il PRIMO ARRIVO
    DI CIASCUN FOCOLAIO, e la media e' sui focolai serviti, non sulle
    assegnazioni. Il tempo di risposta di un incendio e' l'istante in cui il
    primo mezzo lo tocca: il secondo e il terzo mezzo cambiano l'esito dello
    spegnimento, non la prontezza. Mediare per assegnazione premierebbe chi
    accumula mezzi sullo stesso focolaio e penalizzerebbe chi ne manda uno
    solo lontano, cioe' misurerebbe la distribuzione della flotta al posto del
    tempo di intervento. E' anche la lettura che `_check_max_response` applica
    ai vincoli del coordinatore ("il primo mezzo arriva al minuto X") e quella
    che `eval/metrics.py` e `sim/replay.py` usano gia': una grandezza sola,
    con un nome solo, in tutto il sistema.

    Il numero ricalcolato e' `partenza dichiarata + tempo di percorrenza
    minimo dalla base`: il primo istante in cui quel mezzo potrebbe
    fisicamente essere sul fuoco, date le partenze che il piano dichiara. E'
    un limite inferiore, non una previsione: un piano puo' legittimamente
    dichiarare arrivi piu' tardi, e infatti il confronto con
    `declared_mean_response_minutes` ammette una tolleranza. Resta una
    differenza dichiarata con `eval/metrics.py`: li' il percentile si
    interpola linearmente, qui si prende il rango piu' vicino, perche' un
    validatore deve citare un tempo che qualcuno ha davvero misurato.

    I tempi ricalcolati contano solo le assegnazioni eseguibili, mentre quelli
    dichiarati contano tutto cio' che il piano scrive. Su un piano accettato le
    due basi coincidono; su un piano respinto no, e la differenza va letta
    sapendolo, non come un'ulteriore accusa.

    `total_vehicle_minutes` e `total_cost_eur` invece contano ogni
    assegnazione, anche quelle respinte, e ognuna come una missione completa
    base-focolaio-base secondo la formula di SPEC sezione 6. Questa asimmetria
    con i tempi di risposta e' voluta e va detta: il tempo di risposta e' un
    limite che serve ad accusare, e allora si prende l'ipotesi piu' favorevole
    al piano; il costo e' una grandezza da riportare, e allora si segue alla
    lettera il modello dichiarato invece di ricostruire una catena di missioni
    che SPEC non descrive. La conseguenza pratica e' che su un piano dove lo
    stesso mezzo incatena piu' interventi il costo ricalcolato e' un
    sovrastimato (conta un rientro e un setup in piu' per ogni intervento), e
    il confronto con il costo dichiarato puo' divergere per questo motivo e non
    per un errore del pianificatore.
    """

    fires_total: int = 0
    fires_covered: int = 0
    fires_covered_declared: int = 0
    fires_wui_total: int = 0
    fires_wui_covered: int = 0
    water_demanded_liters: float = 0.0
    water_declared_liters: float = 0.0
    water_deliverable_liters: float = 0.0
    mean_response_minutes: float = 0.0
    p90_response_minutes: float = 0.0
    declared_mean_response_minutes: float = 0.0
    total_vehicle_minutes: float = 0.0
    total_cost_eur: float = 0.0
    vehicles_used: int = 0
    assignments_total: int = 0
    assignments_feasible: int = 0

    @property
    def coverage_ratio(self) -> float:
        """Copertura reale: focolai con almeno un'assegnazione eseguibile."""
        return self.fires_covered / self.fires_total if self.fires_total else 0.0

    @property
    def declared_coverage_ratio(self) -> float:
        return (
            self.fires_covered_declared / self.fires_total if self.fires_total else 0.0
        )

    @property
    def wui_coverage_ratio(self) -> float:
        if not self.fires_wui_total:
            return 1.0
        return self.fires_wui_covered / self.fires_wui_total

    @property
    def water_fulfilment_ratio(self) -> float:
        """Quota del fabbisogno ricalcolato che i litri erogabili coprono."""
        if not self.water_demanded_liters:
            return 1.0
        return min(1.0, self.water_deliverable_liters / self.water_demanded_liters)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fires_total": self.fires_total,
            "fires_covered": self.fires_covered,
            "fires_covered_declared": self.fires_covered_declared,
            "fires_wui_total": self.fires_wui_total,
            "fires_wui_covered": self.fires_wui_covered,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "declared_coverage_ratio": round(self.declared_coverage_ratio, 4),
            "wui_coverage_ratio": round(self.wui_coverage_ratio, 4),
            "water_demanded_liters": round(self.water_demanded_liters, 1),
            "water_declared_liters": round(self.water_declared_liters, 1),
            "water_deliverable_liters": round(self.water_deliverable_liters, 1),
            "water_fulfilment_ratio": round(self.water_fulfilment_ratio, 4),
            "mean_response_minutes": round(self.mean_response_minutes, 2),
            "p90_response_minutes": round(self.p90_response_minutes, 2),
            "declared_mean_response_minutes": round(
                self.declared_mean_response_minutes, 2
            ),
            "total_vehicle_minutes": round(self.total_vehicle_minutes, 2),
            "total_cost_eur": round(self.total_cost_eur, 2),
            "vehicles_used": self.vehicles_used,
            "assignments_total": self.assignments_total,
            "assignments_feasible": self.assignments_feasible,
        }


@dataclass
class MetricComparison:
    """Confronto fra una metrica dichiarata dal piano e la stessa ricalcolata.

    `tolerance` e' il margine assoluto entro cui la differenza si considera
    rumore di arrotondamento. Tenerlo dentro l'oggetto, e non nascosto dentro
    la funzione che confronta, permette di mostrarlo nel report: chi legge
    deve poter vedere non solo che due numeri differiscono, ma anche di quanto
    gli era permesso differire.
    """

    name: str
    declared: Optional[float]
    recomputed: float
    tolerance: float
    unit: str = ""

    @property
    def delta(self) -> Optional[float]:
        if self.declared is None:
            return None
        return self.declared - self.recomputed

    @property
    def agrees(self) -> bool:
        d = self.delta
        if d is None:
            return True
        return abs(d) <= self.tolerance

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "declared": _round_or_none(self.declared),
            "recomputed": round(self.recomputed, 3),
            "delta": _round_or_none(self.delta),
            "tolerance": round(self.tolerance, 3),
            "unit": self.unit,
            "agrees": self.agrees,
        }


def _round_or_none(value: Optional[float]) -> Optional[float]:
    if value is None or not isfinite(value):
        return None
    return round(value, 3)


# --------------------------------------------------------------------------
# Risultato
# --------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Esito completo della validazione di un piano.

    Non e' un booleano per una ragione di metodo. Un validatore che
    restituisce vero o falso costringe chi lo usa a rilanciarlo per capire
    perche', e nella pratica smette di essere usato: nessuno accetta un "no"
    senza motivazione da un programma che decide se un piano antincendio e'
    eseguibile. Qui l'esito porta con se' le violazioni con i numeri che le
    hanno prodotte, le metriche ricalcolate, il confronto con quelle
    dichiarate e la tolleranza usata, cosi' la stessa istanza serve il
    terminale, la API, l'archivio dell'esperimento e il test.

    `is_valid` riassume solo le violazioni bloccanti. Gli avvisi non tolgono
    validita' al piano per scelta esplicita: sono cose vere da sapere, non
    ragioni per fermare un intervento.
    """

    plan_id: str
    scenario_id: str
    is_valid: bool
    violations: list[Violation] = field(default_factory=list)
    metrics: RecomputedMetrics = field(default_factory=RecomputedMetrics)
    comparisons: list[MetricComparison] = field(default_factory=list)
    tolerance: Tolerance = DEFAULT_TOLERANCE
    rules_run: list[str] = field(default_factory=list)
    validator_version: str = VALIDATOR_VERSION

    @property
    def blocking(self) -> list[Violation]:
        return [v for v in self.violations if v.is_blocking]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if not v.is_blocking]

    @property
    def disagreements(self) -> list[MetricComparison]:
        """Metriche su cui il piano e il validatore non vanno d'accordo."""
        return [c for c in self.comparisons if not c.agrees]

    def violations_by_rule(self) -> dict[str, list[Violation]]:
        out: dict[str, list[Violation]] = {}
        for v in self.violations:
            out.setdefault(v.rule, []).append(v)
        return out

    def summary_line(self) -> str:
        """Una riga sola, quella che si legge nei log e in cima al report."""
        esito = "ACCETTATO" if self.is_valid else "RESPINTO"
        return (
            f"Piano {self.plan_id}: {esito} "
            f"({len(self.blocking)} violazioni bloccanti, "
            f"{len(self.warnings)} avvisi, "
            f"copertura ricalcolata {self.metrics.coverage_ratio:.0%})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Forma serializzabile per la API e per gli archivi degli esperimenti."""
        return {
            "plan_id": self.plan_id,
            "scenario_id": self.scenario_id,
            "is_valid": self.is_valid,
            "validator_version": self.validator_version,
            # La tolleranza si serializza per intero e non campo per campo: e'
            # il parametro che decide chi viene respinto, quindi deve finire
            # negli archivi degli esperimenti tutta, anche se un giorno
            # qualcuno le aggiunge una voce e si dimentica di questo punto.
            "tolerance": asdict(self.tolerance),
            "counts": {
                "blocking": len(self.blocking),
                "warnings": len(self.warnings),
                "rules_run": len(self.rules_run),
            },
            "violations": [v.to_dict() for v in self.violations],
            "metrics": self.metrics.to_dict(),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "rules_run": list(self.rules_run),
        }


# --------------------------------------------------------------------------
# Funzione principale
# --------------------------------------------------------------------------


def validate_plan(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tolerance: Tolerance | float = DEFAULT_TOLERANCE,
) -> ValidationResult:
    """Valida un piano contro lo scenario e, se c'e', contro i vincoli tradotti.

    `tolerance` accetta sia un oggetto `Tolerance` sia un numero, letto in quel
    caso come tolleranza in minuti (la grandezza su cui si sbaglia piu'
    spesso). Il valore di default e' documentato in `rules.Tolerance`: un
    minuto, cioe' la dimensione dell'arrotondamento a interi che CP-SAT
    introduce per costruzione. Stringerlo produce falsi allarmi su ogni piano,
    allargarlo nasconde errori veri, e in mezzo non c'e' un valore "giusto":
    c'e' una scelta, che va dichiarata e mostrata nel report.

    Il piano non viene mai modificato: la validazione e' una lettura.
    """
    tol = tolerance if isinstance(tolerance, Tolerance) else Tolerance(minutes=float(tolerance))

    violations: list[Violation] = []
    rules_run: list[str] = []
    for rule in ALL_RULES:
        rules_run.append(rule.__name__)
        violations.extend(rule(plan, scenario, constraint_set, tol))

    metrics = _recompute_metrics(plan, scenario, violations, tol)
    violations.extend(_check_water_demand_declaration(scenario, tol))
    comparisons = _build_comparisons(plan, metrics, tol)
    violations.extend(_check_declared_metrics(plan, comparisons))

    is_valid = not any(v.is_blocking for v in violations)
    return ValidationResult(
        plan_id=plan.plan_id,
        scenario_id=scenario.scenario_id,
        is_valid=is_valid,
        violations=violations,
        metrics=metrics,
        comparisons=comparisons,
        tolerance=tol,
        rules_run=rules_run,
    )


# --------------------------------------------------------------------------
# Ricalcolo delle metriche
# --------------------------------------------------------------------------


def _blocked_assignment_indices(plan: Plan, violations: list[Violation]) -> set[int]:
    """Indici delle assegnazioni toccate da almeno una violazione bloccante.

    Le violazioni riportano le entita' coinvolte in modi diversi a seconda
    della regola: alcune conoscono l'indice esatto dell'assegnazione, altre
    solo la coppia mezzo-focolaio, altre ancora il solo mezzo (il turno e' una
    proprieta' del mezzo, non di una missione). Qui le tre forme vengono
    riportate a un unico insieme di indici, perche' la copertura reale deve
    contare solo le assegnazioni che restano in piedi.

    Quando la violazione non porta l'indice il criterio e' deliberatamente
    largo: un blocco sul solo mezzo abbatte tutte le sue missioni, un blocco
    sulla coppia mezzo-focolaio abbatte tutte le missioni di quel mezzo su
    quel focolaio anche se fossero due. Si sbaglia dalla parte della prudenza
    perche' questo insieme alimenta la copertura ricalcolata, che e' la cifra
    che il piano non deve poter gonfiare: contare coperto un focolaio grazie a
    una missione che forse e' quella bocciata sarebbe il tipo di ottimismo che
    rende inutile l'intera metrica.

    Le violazioni dei vincoli duri che parlano solo di un focolaio (per
    esempio "servivano tre mezzi e ce ne sono due") non abbattono niente: il
    piano e' respinto lo stesso, ma le missioni che ci sono restano
    eseguibili, e non c'e' ragione di dichiararle impossibili.
    """
    blocked: set[int] = set()
    for v in violations:
        if not v.is_blocking:
            continue
        ent = v.entities
        idx = ent.get("assignment_index")
        if idx is not None and idx.isdigit():
            blocked.add(int(idx))
            continue
        vehicle_id = ent.get("vehicle_id")
        fire_id = ent.get("fire_id")
        if vehicle_id is None:
            continue
        for i, a in enumerate(plan.assignments):
            if a.vehicle_id != vehicle_id:
                continue
            if fire_id is None or a.fire_id == fire_id:
                blocked.add(i)
    return blocked


def _percentile_nearest_rank(values: list[float], percentile: float) -> float:
    """Percentile con il metodo del rango piu' vicino.

    Nessuna interpolazione: su campioni piccoli (una finestra ha pochi
    interventi) interpolare inventa un valore che nessun mezzo ha misurato.
    Il rango piu' vicino restituisce sempre un tempo davvero osservato, che e'
    quello che serve per dire "il novanta per cento degli arrivi sta sotto".

    L'epsilon prima dell'arrotondamento per eccesso non e' superstizione: 0.9
    non e' rappresentabile in binario, quindi per certe lunghezze del campione
    `0.9 * n` vale un pelo piu' dell'intero atteso e `ceil` salterebbe al rango
    successivo. E' un off-by-one che si manifesta solo per alcuni valori di n,
    cioe' il tipo di difetto che in un confronto sperimentale non si nota mai e
    non si spiega piu'.
    """
    if not values:
        return 0.0
    ordinati = sorted(values)
    rank = max(1, ceil(percentile * len(ordinati) - 1e-9))
    return ordinati[min(rank, len(ordinati)) - 1]


def _recompute_metrics(
    plan: Plan,
    scenario: Scenario,
    violations: list[Violation],
    tol: Tolerance,
) -> RecomputedMetrics:
    """Ricostruisce copertura, litri, tempi e costi dai soli dati dello scenario."""
    blocked = _blocked_assignment_indices(plan, violations)
    # Stessa funzione che usa la regola sui tempi di arrivo: il tempo di
    # risposta ricalcolato e il limite fisico che il piano deve rispettare
    # devono essere lo stesso numero, altrimenti il validatore accetta un piano
    # e poi lo accusa di dichiarare tempi che lui stesso considera leciti.
    floors = arrival_floors(plan, scenario)

    m = RecomputedMetrics()
    m.fires_total = len(scenario.fires)
    m.fires_wui_total = sum(1 for f in scenario.fires if f.is_wui)
    m.assignments_total = len(plan.assignments)
    m.water_demanded_liters = sum(
        physics.water_demand_liters(f) for f in scenario.fires
    )

    coperti_reali: set[str] = set()
    coperti_dichiarati: set[str] = set()
    mezzi_usati: set[str] = set()
    # Un tempo per focolaio, non uno per assegnazione: vedi la convenzione
    # dichiarata in `RecomputedMetrics`. Le chiavi sono gli identificativi dei
    # focolai, cosi' il secondo mezzo mandato sullo stesso incendio non entra
    # due volte nella media.
    arrivo_ricalcolato: dict[str, float] = {}
    arrivo_dichiarato: dict[str, float] = {}

    for i, a in enumerate(plan.assignments):
        coperti_dichiarati.add(a.fire_id)
        mezzi_usati.add(a.vehicle_id)
        m.water_declared_liters += float(a.water_delivered_liters)
        _tieni_il_primo(arrivo_dichiarato, a.fire_id, float(a.arrival_minute))

        vehicle = scenario.vehicle_by_id(a.vehicle_id)
        fire = scenario.fire_by_id(a.fire_id)
        if vehicle is None or fire is None:
            continue

        origin = physics.vehicle_origin(vehicle, scenario)
        source = None
        if a.water_source_id:
            source = scenario.water_source_by_id(a.water_source_id)
        if source is None:
            source, _ = physics.nearest_usable_source(vehicle, fire, scenario)

        if origin is not None:
            timing = physics.mission_timing(
                vehicle, origin, fire.centroid, source, a.sorties
            )
            m.total_vehicle_minutes += timing.total_minutes
            m.total_cost_eur += (
                timing.total_minutes / 60.0 * vehicle.cost_per_hour_eur
            )

        if i in blocked:
            continue

        m.assignments_feasible += 1
        coperti_reali.add(a.fire_id)
        m.water_deliverable_liters += min(
            float(a.water_delivered_liters),
            float(physics.deliverable_liters(vehicle, a.sorties)),
        )
        if i in floors:
            _tieni_il_primo(
                arrivo_ricalcolato,
                a.fire_id,
                float(a.departure_minute) + floors[i][0],
            )

    m.fires_covered = sum(1 for f in scenario.fires if f.fire_id in coperti_reali)
    m.fires_covered_declared = sum(
        1 for f in scenario.fires if f.fire_id in coperti_dichiarati
    )
    m.fires_wui_covered = sum(
        1 for f in scenario.fires if f.is_wui and f.fire_id in coperti_reali
    )
    m.vehicles_used = len(mezzi_usati)
    # L'ordinamento per identificativo di focolaio non cambia media ne'
    # percentile, ma rende la somma in virgola mobile identica da
    # un'esecuzione all'altra: la rigiocata della stagione confronta piani
    # sugli stessi scenari e l'ultima cifra deve coincidere.
    tempi_ricalcolati = [arrivo_ricalcolato[k] for k in sorted(arrivo_ricalcolato)]
    tempi_dichiarati = [arrivo_dichiarato[k] for k in sorted(arrivo_dichiarato)]
    if tempi_ricalcolati:
        m.mean_response_minutes = sum(tempi_ricalcolati) / len(tempi_ricalcolati)
        m.p90_response_minutes = _percentile_nearest_rank(tempi_ricalcolati, 0.9)
    if tempi_dichiarati:
        m.declared_mean_response_minutes = sum(tempi_dichiarati) / len(
            tempi_dichiarati
        )
    return m


def _tieni_il_primo(arrivi: dict[str, float], fire_id: str, minuto: float) -> None:
    """Registra il minuto solo se e' il piu' basso visto finora per quel focolaio.

    Sta in una funzione perche' le due mappe dei tempi, quella dichiarata e
    quella ricalcolata, devono aggregare allo stesso modo. Scritto due volte a
    mano, uno dei due prima o poi diventa un `setdefault`, cioe' "il primo
    della lista" invece di "il primo ad arrivare", e la divergenza fra le due
    colonne del confronto verrebbe attribuita al piano.

    Un minuto non finito non entra. Il confronto `minuto < corrente` e' falso
    per un NaN, quindi un NaN che arrivasse per primo resterebbe dentro (non
    c'e' ancora niente con cui confrontarlo) e da li' in poi contaminerebbe la
    media di tutto il piano: una grandezza sola, illeggibile, cancellerebbe le
    altre venti che sono in ordine. L'assegnazione che porta quel numero e'
    gia' contestata da `check_assignment_wellformed`, e il posto per dirlo e'
    la violazione, non la metrica.
    """
    if not isfinite(minuto):
        return
    corrente = arrivi.get(fire_id)
    if corrente is None or minuto < corrente:
        arrivi[fire_id] = minuto


# --------------------------------------------------------------------------
# Confronto fra dichiarato e ricalcolato
# --------------------------------------------------------------------------


def _declared_totals(plan: Plan) -> dict[str, Optional[float]]:
    """Cosa il piano dichiara di se stesso.

    Se il piano porta un blocco `metrics` si usa quello, perche' e' la cifra
    che finira' nei confronti sperimentali. Altrimenti si sommano le
    assegnazioni: anche senza metriche esplicite, un piano dichiara comunque
    dei litri e dei tempi.

    Il tempo medio si ricava con la convenzione dichiarata in
    `RecomputedMetrics`, cioe' il primo arrivo su ciascun focolaio. Deve essere
    la stessa del ricalcolo: confrontare una media per focolaio con una media
    per assegnazione produrrebbe una divergenza che non appartiene al piano ma
    al contatore, ed e' il tipo di falso allarme che fa spegnere un
    validatore.
    """
    liters = float(sum(a.water_delivered_liters for a in plan.assignments))
    arrivi: dict[str, float] = {}
    for a in plan.assignments:
        _tieni_il_primo(arrivi, a.fire_id, float(a.arrival_minute))
    media = sum(arrivi[k] for k in sorted(arrivi)) / len(arrivi) if arrivi else 0.0
    out: dict[str, Optional[float]] = {
        "litri erogati": liters,
        "tempo medio di risposta": media,
        "mezzi impiegati": float(len({a.vehicle_id for a in plan.assignments})),
        "focolai coperti": float(len({a.fire_id for a in plan.assignments})),
        "minuti-mezzo": None,
        "costo": None,
    }
    if plan.metrics is not None:
        pm = plan.metrics
        out["litri erogati"] = float(pm.water_delivered_liters)
        out["tempo medio di risposta"] = float(pm.mean_response_minutes)
        out["mezzi impiegati"] = float(pm.vehicles_used)
        out["focolai coperti"] = float(pm.fires_covered)
        out["minuti-mezzo"] = float(pm.total_vehicle_minutes)
        out["costo"] = float(pm.total_cost_eur)
    return out


def _build_comparisons(
    plan: Plan, metrics: RecomputedMetrics, tol: Tolerance
) -> list[MetricComparison]:
    """Costruisce la tabella dichiarato/ricalcolato."""
    dichiarato = _declared_totals(plan)
    return [
        MetricComparison(
            name="focolai coperti",
            declared=dichiarato["focolai coperti"],
            recomputed=float(metrics.fires_covered),
            tolerance=0.0,
            unit="focolai",
        ),
        MetricComparison(
            name="litri erogati",
            declared=dichiarato["litri erogati"],
            recomputed=metrics.water_deliverable_liters,
            tolerance=tol.liters_slack(metrics.water_deliverable_liters),
            unit="litri",
        ),
        MetricComparison(
            name="tempo medio di risposta",
            declared=dichiarato["tempo medio di risposta"],
            recomputed=metrics.mean_response_minutes,
            tolerance=max(
                tol.minutes, metrics.mean_response_minutes * tol.metric_ratio
            ),
            unit="minuti",
        ),
        MetricComparison(
            name="minuti-mezzo",
            declared=dichiarato["minuti-mezzo"],
            recomputed=metrics.total_vehicle_minutes,
            tolerance=max(
                tol.minutes, metrics.total_vehicle_minutes * tol.metric_ratio
            ),
            unit="minuti",
        ),
        MetricComparison(
            name="costo",
            declared=dichiarato["costo"],
            recomputed=metrics.total_cost_eur,
            tolerance=max(1.0, metrics.total_cost_eur * tol.metric_ratio),
            unit="euro",
        ),
        MetricComparison(
            name="mezzi impiegati",
            declared=dichiarato["mezzi impiegati"],
            recomputed=float(metrics.vehicles_used),
            tolerance=0.0,
            unit="mezzi",
        ),
    ]


def _check_declared_metrics(
    plan: Plan, comparisons: list[MetricComparison]
) -> list[Violation]:
    """Segnala le metriche dichiarate che il validatore non conferma.

    Sono avvisi, non blocchi: un piano che dichiara male le proprie metriche
    resta eseguibile. Va detto lo stesso, perche' quelle cifre sono quelle che
    finiscono nel confronto con la baseline, e un confronto su numeri non
    verificati non dimostra niente.
    """
    out: list[Violation] = []
    for c in comparisons:
        if c.agrees or c.declared is None:
            continue
        out.append(
            Violation(
                rule="metrics_declaration",
                severity=ValidationSeverity.WARNING,
                message=(
                    f"il piano dichiara {c.declared:.1f} {c.unit} per "
                    f"'{c.name}', il validatore ne ricalcola "
                    f"{c.recomputed:.1f} (tolleranza {c.tolerance:.1f})"
                ),
                entities={"plan_id": plan.plan_id, "metric": c.name},
                expected=c.recomputed,
                found=c.declared,
            )
        )
    return out


def _check_water_demand_declaration(
    scenario: Scenario, tol: Tolerance
) -> list[Violation]:
    """Confronta il fabbisogno d'acqua dichiarato con quello ricalcolato.

    Il fabbisogno arriva dall'arricchimento in `ingest/`, non dal piano: se
    diverge molto dalla formula di SPEC il piano non e' colpevole, ma chi
    legge le metriche deve saperlo, perche' il denominatore della copertura
    idrica dipende da quel numero. Soglia larga (30 per cento) e gravita'
    di solo avviso, per le ragioni spiegate in `rules.Tolerance`.
    """
    out: list[Violation] = []
    for fire in scenario.fires:
        if fire.water_demand_liters <= 0:
            continue
        atteso = physics.water_demand_liters(fire)
        if atteso <= 0:
            continue
        scarto = abs(fire.water_demand_liters - atteso)
        if scarto > atteso * tol.demand_ratio:
            out.append(
                Violation(
                    rule="water_demand",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"il focolaio {fire.fire_id} dichiara un fabbisogno di "
                        f"{fire.water_demand_liters} litri, la formula di SPEC ne "
                        f"calcola {atteso:.0f} (area {fire.area_km2:.2f} km2, "
                        f"copertura {fire.land_cover.value}, "
                        f"FRP massima {fire.max_frp_mw:.0f} MW)"
                    ),
                    entities={"fire_id": fire.fire_id},
                    expected=atteso,
                    found=float(fire.water_demand_liters),
                )
            )
    return out
