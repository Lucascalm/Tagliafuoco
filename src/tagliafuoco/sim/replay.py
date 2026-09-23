"""Motore di rigiocata: si riattraversa la stagione una finestra alla volta.

PERCHE' QUESTO MODULO ESISTE
E' l'esperimento numero uno di SPEC.md sezione 7. Serve a rispondere a una sola
domanda: sugli stessi identici scenari, il piano dell'ottimizzatore e' migliore
di quello della regola banale, e di quanto. Tutto qui dentro e' costruito per
rendere quel confronto difendibile.

Tre proprieta', e nessuna e' gratuita.

1. Nessuna fuga di informazione dal futuro. Gli scenari li costruisce
   `sim/timeline.py`, che e' l'unico punto autorizzato a decidere che cosa era
   visibile a che ora.

2. Nessuna fuga di stato fra rigiocate. Lo stato del mondo e' un oggetto che
   `sim/state.py` fa avanzare restituendone uno nuovo, mai modificandolo. La
   rigiocata del solver e quella della baseline partono dallo stesso oggetto e
   non se lo sporcano a vicenda.

3. Il pianificatore e' un parametro. Il motore non sa se sta invocando il
   modello CP-SAT, una baseline o qualunque altro callable: gli passa uno
   `Scenario` e un `ConstraintSet` e riceve un `Plan`. E' questo che rende il
   confronto simmetrico. Se il motore conoscesse il solver, prima o poi gli
   farebbe un favore senza che nessuno se ne accorga.

Il risultato finisce in JSON sotto `data/processed/`, con un nome che dice chi
ha pianificato e su quale intervallo. Gira senza rete e senza chiavi.

DIPENDENZE TARDIVE
`validate/` ed `eval/` vengono scritti in parallelo a questo modulo. Il motore
li cerca al momento dell'uso e, se non ci sono, prosegue: senza validatore
registra la validazione come non disponibile, senza `eval/metrics.py` calcola un
riassunto minimo interno. Il riassunto interno e' un ripiego dichiarato e va
sostituito dalle metriche vere appena esistono, perche' le metriche ufficiali
del confronto sono quelle di `eval/`, non queste.
"""

from __future__ import annotations

import inspect
import json
import math
import time
from dataclasses import dataclass, field, is_dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from tagliafuoco.config import (
    CLUSTER_TIME_WINDOW_HOURS,
    DEFAULT_HORIZON_MINUTES,
    PROCESSED_DIR,
    REPLAY_WINDOW_HOURS,
)
from tagliafuoco.domain.models import (
    Base,
    Detection,
    FireCluster,
    Plan,
    PlanMetrics,
    Scenario,
    Vehicle,
    VehicleStatus,
    WaterSource,
)
from tagliafuoco.nlu.schema import ConstraintSet
from tagliafuoco.sim.state import WorldState
from tagliafuoco.sim.timeline import (
    ReplayWindow,
    as_utc,
    build_windows,
    is_night_at,
    visible_detections,
    visible_fires,
)


class ReplayError(RuntimeError):
    """La rigiocata non puo' partire: mancano risorse o l'intervallo e' assurdo."""


#: Sentinella per distinguere "non me ne hai parlato, cercalo tu" da "non lo
#: voglio". `validator=None` disattiva la validazione, `validator=AUTO` la cerca.
AUTO: Any = object()


# --------------------------------------------------------------------------
# Invocazione difensiva delle dipendenze scritte da altri
# --------------------------------------------------------------------------


def _positional_names(fn: Callable[..., Any]) -> Optional[list[str]]:
    """Nomi dei parametri posizionali, oppure None se la firma non e' leggibile."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover - callable esotici
        return None
    return [
        p.name
        for p in sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]


def call_planner(
    planner: Callable[..., Plan], scenario: Scenario, constraints: Optional[ConstraintSet]
) -> Plan:
    """Invoca il pianificatore adattandosi alla sua firma.

    Il contratto e' `planner(scenario, constraints) -> Plan`, ma un
    pianificatore che i vincoli non li usa ha il diritto di non dichiararli.
    Si guarda quanti parametri posizionali accetta invece di provare e
    intercettare TypeError: un TypeError sollevato dentro al pianificatore non
    si distinguerebbe da uno dovuto alla firma, e verrebbe nascosto.
    """
    names = _positional_names(planner)
    if names is None or len(names) >= 2:
        return planner(scenario, constraints)
    return planner(scenario)


def _call_two(fn: Callable[..., Any], plan: Plan, scenario: Scenario) -> Any:
    """Chiama una funzione che vuole un piano e uno scenario, in qualunque ordine.

    Validatore e metriche sono di altri sottosistemi e l'ordine dei loro due
    argomenti non e' fissato in SPEC.md. Si deduce dai nomi dei parametri; se i
    nomi non dicono niente si usa l'ordine (piano, scenario), che e' quello con
    cui sono scritte le firme nella documentazione interna.
    """
    names = _positional_names(fn)
    if names and len(names) >= 2:
        first = names[0].lower()
        if "scenario" in first:
            return fn(scenario, plan)
    return fn(plan, scenario)


def _call_validator(
    validator: Callable[..., Any],
    plan: Plan,
    scenario: Scenario,
    constraints: Optional[ConstraintSet],
) -> Any:
    """Come `_call_two`, ma passa anche i vincoli se il validatore li accetta.

    Senza i vincoli il validatore verificherebbe solo la fisica e non saprebbe
    dire se gli ordini del coordinatore sono stati rispettati, che e' meta' del
    suo lavoro.
    """
    names = _positional_names(validator)
    if constraints is not None and names is not None and len(names) >= 3:
        if "scenario" in names[0].lower():
            return validator(scenario, plan, constraints)
        return validator(plan, scenario, constraints)
    return _call_two(validator, plan, scenario)


def _find_callable(module_names: Sequence[str], attr_names: Sequence[str]) -> Optional[Callable[..., Any]]:
    """Primo callable trovato fra moduli e nomi indicati. Import tardivo."""
    import importlib

    for mod_name in module_names:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        for attr in attr_names:
            fn = getattr(mod, attr, None)
            if callable(fn):
                return fn
    return None


def default_validator() -> Optional[Callable[..., Any]]:
    """Il validatore indipendente, se `validate/` e' gia' stato scritto."""
    return _find_callable(
        ("tagliafuoco.validate.validator", "tagliafuoco.validate"),
        ("validate_plan", "validate", "check_plan"),
    )


def default_metrics() -> Optional[Callable[..., Any]]:
    """Le metriche ufficiali, se `eval/` e' gia' stato scritto."""
    return _find_callable(
        ("tagliafuoco.eval.metrics", "tagliafuoco.eval"),
        ("compute_metrics", "plan_metrics", "evaluate_plan"),
    )


# --------------------------------------------------------------------------
# Metriche di ripiego (da sostituire con quelle di eval/)
# --------------------------------------------------------------------------


def _nearest_rank_percentile(values: Sequence[float], q: float) -> float:
    """Percentile con il metodo del rango piu' vicino.

    E' la definizione piu' semplice da spiegare e da riprodurre a mano, e qui e'
    il ripiego: si usa solo quando `eval/metrics.py` non e' importabile.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, math.ceil(q * len(ordered)) - 1)
    return float(ordered[min(idx, len(ordered) - 1)])


def _percentile(values: Sequence[float], q: float) -> float:
    """Percentile di stagione, con la stessa convenzione delle metriche ufficiali.

    Il percentile non e' una grandezza sola: rango piu' vicino e interpolazione
    lineare danno numeri diversi, e su campioni piccoli la differenza arriva al
    venti per cento. `eval/metrics.py` interpola linearmente. Se qui si usasse
    comunque il rango piu' vicino, nello stesso file JSON finirebbero un p90 di
    finestra calcolato in un modo e un p90 di stagione calcolato in un altro, e
    chi legge il resoconto non avrebbe nessun modo di accorgersene: i due numeri
    si assomigliano, non si contraddicono.

    Quindi si chiede la funzione a `eval/` se c'e', e si ripiega sul rango piu'
    vicino solo quando non c'e'. In quel caso ripiegano anche le metriche di
    finestra, che sono `basic_metrics`: il resoconto resta coerente con se'
    stesso in tutti e due i mondi, che e' la proprieta' che serve.
    """
    fn = _find_callable(("tagliafuoco.eval.metrics",), ("percentile",))
    if fn is not None:
        try:
            return float(fn(list(values), q))
        except Exception:
            pass
    return _nearest_rank_percentile(values, q)


def basic_metrics(plan: Plan, scenario: Scenario) -> PlanMetrics:
    """Riassunto minimo di un piano. Ripiego finche' `eval/metrics.py` non esiste.

    Convenzione dichiarata: l'acqua consegnata si conta per focolaio e si
    tronca al suo fabbisogno, cosi' un eccesso su un incendio non compensa una
    mancanza su un altro. Il tempo di risposta di un focolaio e' l'arrivo del
    primo mezzo.

    ATTENZIONE, la divergenza che conta. Qui un focolaio e' COPERTO quando ha
    ricevuto almeno un mezzo. In `eval/metrics.py` e' coperto quando ha ricevuto
    almeno una frazione dichiarata del suo fabbisogno d'acqua, e cio' che qui si
    chiama copertura li' si chiama `fires_attended`. Sono due numeri diversi
    dello stesso piano, e sulle rigiocate vere differiscono di molto: su una
    settimana di fixture il ripiego dice copertura vicina a uno e le metriche
    ufficiali dicono meno di un decimo.

    Non si e' allineata la definizione qui perche' la soglia e' una taratura di
    `eval/` e duplicarla creerebbe una terza convenzione da tenere in sincrono.
    Si e' invece reso visibile QUALE delle due ha prodotto i numeri:
    `WindowOutcome.metrics_source` lo dichiara in ogni finestra e finisce nel
    JSON. Un confronto fra una rigiocata misurata col ripiego e una misurata con
    `eval/` non si puo' fare, e da quel campo si vede subito.
    """
    responses: list[float] = []
    delivered_capped = 0
    covered: set[str] = set()

    for fire in scenario.fires:
        rows = plan.assignments_for_fire(fire.fire_id)
        if not rows:
            continue
        covered.add(fire.fire_id)
        responses.append(min(float(a.arrival_minute) for a in rows))
        delivered = sum(int(a.water_delivered_liters) for a in rows)
        demand = max(0, int(fire.water_demand_liters))
        # Il taglio vale SEMPRE, anche quando il fabbisogno dichiarato e' zero.
        # Prima un focolaio senza fabbisogno contribuiva con tutti i suoi litri
        # al numeratore e con zero al denominatore, e bastavano due focolai non
        # arricchiti perche' `water_fulfilment_ratio` sbattesse contro il suo
        # tetto di 1.0 e annunciasse un fabbisogno soddisfatto per intero anche
        # dove non era stato coperto niente. Questo contatore si legge solo come
        # rapporto sul fabbisogno, e un focolaio che non dichiara un fabbisogno
        # non ha un denominatore: contribuisce zero a entrambi i lati. I litri
        # erogati per davvero, senza alcun taglio, sono quelli che SPEC.md
        # sezione 6 chiede alle metriche ufficiali di `eval/`.
        delivered_capped += min(delivered, demand)

    wui_total = [f for f in scenario.fires if f.is_wui]
    return PlanMetrics(
        fires_total=len(scenario.fires),
        fires_covered=len(covered),
        fires_wui_total=len(wui_total),
        fires_wui_covered=sum(1 for f in wui_total if f.fire_id in covered),
        water_demanded_liters=sum(int(f.water_demand_liters) for f in scenario.fires),
        water_delivered_liters=delivered_capped,
        mean_response_minutes=round(sum(responses) / len(responses), 3) if responses else 0.0,
        p90_response_minutes=round(_percentile(responses, 0.9), 3),
        total_vehicle_minutes=round(
            sum(max(0.0, float(a.engaged_minutes)) for a in plan.assignments), 3
        ),
        total_cost_eur=round(sum(float(a.estimated_cost_eur) for a in plan.assignments), 2),
        vehicles_used=len({a.vehicle_id for a in plan.assignments}),
    )


# --------------------------------------------------------------------------
# Serializzazione
# --------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Riduce a tipi JSON qualunque cosa arrivi dagli altri sottosistemi.

    Il validatore restituisce un oggetto di cui questo modulo non conosce la
    forma: dataclass, modello Pydantic o altro. Invece di imporre un tipo si
    prova a ridurlo, e in ultima istanza se ne salva la rappresentazione
    testuale. Perdere un dettaglio nel JSON e' meglio che far fallire una
    rigiocata di mille finestre sull'ultima riga.
    """
    # L'enumerazione va prima dei tipi semplici: le enum di questo progetto
    # ereditano da `str`, quindi un controllo su `str` le catturerebbe e nel
    # JSON finirebbe l'oggetto invece del suo valore.
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f: _jsonable(getattr(value, f)) for f in value.__dataclass_fields__}
    dump = getattr(value, "model_dump", None)  # Pydantic v2
    if callable(dump):
        try:
            return _jsonable(dump(mode="json"))
        except Exception:
            pass
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _metrics_dict(metrics: Optional[PlanMetrics]) -> dict:
    """Metriche piu' i rapporti derivati, che sono proprieta' e non campi."""
    if metrics is None:
        return {}
    out = _jsonable(metrics)
    out["coverage_ratio"] = round(metrics.coverage_ratio, 6)
    out["wui_coverage_ratio"] = round(metrics.wui_coverage_ratio, 6)
    out["water_fulfilment_ratio"] = round(metrics.water_fulfilment_ratio, 6)
    return out


#: Nomi con cui un esito di validazione puo' dichiarare il proprio verdetto.
#: L'ordine conta: si prende il primo che esiste ed e' booleano.
_VERDICT_NAMES = ("ok", "is_valid", "valid", "accepted", "passed")


def _validation_dict(report: Any) -> dict:
    """Normalizza l'esito della validazione in una forma stabile.

    Serve un campo booleano che si possa contare, qualunque sia il tipo che il
    validatore restituisce. Si cercano i nomi piu' probabili e, se nessuno c'e',
    si dichiara sconosciuto invece di inventare un verdetto.

    Si guarda sia negli attributi sia nelle chiavi, perche' un validatore puo'
    legittimamente restituire un dizionario. Prima si guardava solo negli
    attributi: con un validatore che restituiva `{"ok": True}` il verdetto
    risultava sconosciuto, la finestra spariva dal conteggio `plans_validated` e
    il controllo "nessun piano respinto" passava senza aver controllato niente.
    Un test verde che non verifica nulla e' peggio di un test rosso.
    """
    if report is None:
        return {"available": False, "ok": None, "report": None}
    ok: Optional[bool] = None
    if isinstance(report, bool):
        ok = report
    else:
        for name in _VERDICT_NAMES:
            got = (
                report.get(name)
                if isinstance(report, Mapping)
                else getattr(report, name, None)
            )
            if isinstance(got, bool):
                ok = got
                break
    return {"available": True, "ok": ok, "report": _jsonable(report)}


# --------------------------------------------------------------------------
# Contesto: le risorse che non cambiano da una finestra all'altra
# --------------------------------------------------------------------------


@dataclass
class ReplayContext:
    """Tutto cio' che serve a costruire uno scenario, tranne il tempo.

    I mezzi qui dentro sono il modello di partenza e non vengono mai modificati:
    ogni finestra ne riceve copie fresche su cui lo stato del mondo viene
    innestato.
    """

    vehicles: list[Vehicle]
    bases: list[Base]
    water_sources: list[WaterSource]
    fires: Optional[list[FireCluster]] = None
    detections: Optional[list[Detection]] = None
    fires_provider: Optional[Callable[..., list[FireCluster]]] = None
    #: Orizzonte dichiarato dal chiamante. ATTENZIONE: non e' questo il numero
    #: che finisce negli scenari. L'orizzonte di una finestra e' una proprieta'
    #: della finestra (`ReplayWindow.horizon_minutes`), perche' e' li' che vive
    #: il tempo, e `build_scenario` legge da li'. Il campo resta perche'
    #: `build_context` lo accetta e perche' chi costruisce le finestre a mano ha
    #: bisogno di sapere con quale orizzonte il contesto era stato pensato, ma
    #: cambiarlo dopo la costruzione non cambia nessuno scenario.
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES
    lookback_hours: float = CLUSTER_TIME_WINDOW_HOURS
    weather: Mapping[str, float] = field(default_factory=dict)

    def fires_visible_at(self, instant: datetime) -> list[FireCluster]:
        """I focolai che a quell'istante risultavano gia' rilevati.

        Due modi, stessa regola di visibilita'. Con i focolai gia' raggruppati
        si filtra il gruppo; con i rilevamenti grezzi si filtrano i rilevamenti e
        si raggruppa solo cio' che e' passato dal filtro, cosi' nemmeno il
        clustering vede il futuro.
        """
        if self.fires is not None:
            return visible_fires(self.fires, instant, lookback_hours=self.lookback_hours)
        if self.detections is not None:
            visible = visible_detections(
                self.detections, instant, lookback_hours=self.lookback_hours
            )
            provider = self.fires_provider or _default_fires_provider()
            if provider is None:
                raise ReplayError(
                    "servono focolai gia' raggruppati (fires=...) oppure una funzione "
                    "di clustering (fires_provider=...): ingest/clustering non e' "
                    "importabile"
                )
            return sorted(_call_provider(provider, visible, instant), key=lambda f: f.fire_id)
        return []


def _default_fires_provider() -> Optional[Callable[..., Any]]:
    return _find_callable(
        ("tagliafuoco.ingest.clustering", "tagliafuoco.ingest"),
        ("cluster_detections", "build_clusters", "cluster"),
    )


#: Nomi con cui un parametro dichiara di volere l'istante di decisione. Il
#: confronto e' su nomi interi e non su frammenti: `cluster_detections` ha come
#: secondo parametro `eps_km`, e un raggio non e' un orario.
_INSTANT_PARAM_NAMES = frozenset(
    {"as_of", "instant", "timestamp", "moment", "now", "when", "at", "reference"}
)


def _call_provider(
    provider: Callable[..., Any], detections: Sequence[Detection], instant: datetime
) -> list[FireCluster]:
    """Chiama il clustering passandogli l'istante solo se lo dichiara.

    Il filtro sul futuro e' gia' stato applicato ai rilevamenti: al clustering
    l'istante serve solo se lo usa per conto suo, e passarglielo al posto di un
    altro parametro sarebbe peggio che non passarlo.
    """
    names = _positional_names(provider)
    if names is not None and len(names) >= 2 and names[1].lower() in _INSTANT_PARAM_NAMES:
        return list(provider(detections, instant))
    return list(provider(detections))


def build_context(
    *,
    vehicles: Optional[Iterable[Vehicle]] = None,
    bases: Optional[Iterable[Base]] = None,
    water_sources: Optional[Iterable[WaterSource]] = None,
    fires: Optional[Iterable[FireCluster]] = None,
    detections: Optional[Iterable[Detection]] = None,
    fires_provider: Optional[Callable[..., list[FireCluster]]] = None,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    lookback_hours: float = CLUSTER_TIME_WINDOW_HOURS,
    weather: Optional[Mapping[str, float]] = None,
) -> ReplayContext:
    """Raccoglie le risorse, leggendo dai dati statici cio' che non e' stato passato.

    Passare le risorse a mano non e' solo comodo nei test: serve all'ablazione,
    dove si rigioca la stessa stagione con una flotta ridotta.
    """
    if vehicles is None or bases is None or water_sources is None:
        from tagliafuoco.domain import registry

        try:
            vehicles = list(vehicles) if vehicles is not None else registry.load_fleet()
            bases = list(bases) if bases is not None else registry.load_bases()
            water_sources = (
                list(water_sources)
                if water_sources is not None
                else registry.load_water_sources()
            )
        except Exception as exc:  # dati statici mancanti o malformati
            raise ReplayError(f"non riesco a caricare i dati statici: {exc}") from exc

    return ReplayContext(
        vehicles=list(vehicles),
        bases=list(bases),
        water_sources=list(water_sources),
        fires=list(fires) if fires is not None else None,
        detections=list(detections) if detections is not None else None,
        fires_provider=fires_provider,
        horizon_minutes=int(horizon_minutes),
        lookback_hours=float(lookback_hours),
        weather=dict(weather or {}),
    )


# --------------------------------------------------------------------------
# Esito di una finestra e della stagione
# --------------------------------------------------------------------------


#: Valori possibili di `WindowOutcome.metrics_source`.
METRICS_OFFICIAL = "eval"
METRICS_FALLBACK = "basic"
METRICS_NONE = "none"


@dataclass
class WindowOutcome:
    """Che cosa e' successo in una finestra. E' la riga del registro.

    `metrics_source` dice chi ha prodotto i numeri: le metriche ufficiali di
    `eval/` oppure il riassunto minimo di questo modulo. Non e' un dettaglio di
    servizio. Le due contano la copertura in modo diverso (vedi
    `basic_metrics`), quindi due rigiocate con sorgenti diverse non sono
    confrontabili, e senza questo campo la differenza sarebbe invisibile: i
    numeri restano plausibili, cambia solo cosa significano.
    """

    window: ReplayWindow
    scenario_id: str
    fires_total: int
    vehicles_available: int
    plan: Plan
    metrics: Optional[PlanMetrics]
    validation: dict
    plan_seconds: float
    state_after: dict
    metrics_source: str = METRICS_FALLBACK

    @property
    def valid(self) -> Optional[bool]:
        return self.validation.get("ok")

    def to_dict(self, *, include_timings: bool = True) -> dict:
        plan = _jsonable(self.plan)
        if not include_timings:
            # Il tempo di calcolo e' l'unica cosa che cambia fra due esecuzioni
            # identiche: va tolta prima di confrontare due rigiocate.
            plan.pop("solve_seconds", None)
        out = {
            "window": self.window.to_dict(),
            "scenario_id": self.scenario_id,
            "fires_total": self.fires_total,
            "vehicles_available": self.vehicles_available,
            "plan": plan,
            "metrics": _metrics_dict(self.metrics),
            "metrics_source": self.metrics_source,
            "validation": self.validation,
            "state_after": self.state_after,
        }
        if include_timings:
            out["plan_seconds"] = round(self.plan_seconds, 6)
        return out


@dataclass
class ReplayResult:
    """Il registro completo di una rigiocata, pronto per il confronto."""

    label: str
    producer: str
    start: datetime
    end: datetime
    window_hours: float
    horizon_minutes: int
    outcomes: list[WindowOutcome] = field(default_factory=list)
    output_path: Optional[Path] = None

    # -- riassunti ------------------------------------------------------

    @property
    def windows_count(self) -> int:
        return len(self.outcomes)

    @property
    def total_plan_seconds(self) -> float:
        return sum(o.plan_seconds for o in self.outcomes)

    def response_minutes_pool(self) -> list[float]:
        """Tutti i tempi di risposta della stagione, uno per focolaio servito.

        Il tempo di risposta di un focolaio e' l'arrivo del primo mezzo. Tenerli
        tutti invece di conservare solo le medie di finestra serve al
        percentile: il novantesimo percentile di una stagione non e' la media
        dei novantesimi percentili delle sue finestre, e riportarlo come tale
        sarebbe un numero sbagliato.
        """
        pool: list[float] = []
        for o in self.outcomes:
            per_fire: dict[str, float] = {}
            for a in o.plan.assignments:
                arrivo = float(a.arrival_minute)
                if a.fire_id not in per_fire or arrivo < per_fire[a.fire_id]:
                    per_fire[a.fire_id] = arrivo
            pool.extend(per_fire[k] for k in sorted(per_fire))
        return pool

    def aggregate(self) -> dict:
        """Somme e medie sull'intera stagione.

        Tre avvertenze sulla lettura dei numeri.

        Che cosa voglia dire "coperto" non lo decide questo metodo: qui si
        sommano i conteggi che ha prodotto la funzione di metriche, e quella e'
        un parametro del motore. Con `eval/metrics.py` un focolaio e' coperto se
        ha ricevuto una frazione dichiarata del suo fabbisogno; con il riassunto
        minimo di ripiego basta che gli sia arrivato un mezzo. Sono numeri che si
        leggono in modo diverso, e `metrics_source` in ogni finestra dice quale
        dei due si sta leggendo. Aggregare finestre con sorgenti diverse non ha
        senso, e infatti `replay_season` sceglie la sorgente una volta sola.

        La copertura si aggrega sui conteggi (focolai coperti sul totale) e non
        come media delle coperture di finestra: una finestra con due focolai non
        vale quanto una con quaranta.

        Il totale dei focolai conta le coppie focolaio-finestra, non i focolai
        distinti: un incendio ancora attivo in tre finestre consecutive viene
        contato tre volte, perche' tre volte e' stata presa una decisione su di
        lui. E' una convenzione, va dichiarata, e vale identica per il solver e
        per la baseline, che e' cio' che rende il confronto leggibile.
        """
        fires = sum(o.metrics.fires_total for o in self.outcomes if o.metrics is not None)
        covered = sum(o.metrics.fires_covered for o in self.outcomes if o.metrics is not None)
        wui = sum(o.metrics.fires_wui_total for o in self.outcomes if o.metrics is not None)
        wui_covered = sum(o.metrics.fires_wui_covered for o in self.outcomes if o.metrics is not None)
        demanded = sum(o.metrics.water_demanded_liters for o in self.outcomes if o.metrics is not None)
        delivered = sum(o.metrics.water_delivered_liters for o in self.outcomes if o.metrics is not None)
        minutes = sum(o.metrics.total_vehicle_minutes for o in self.outcomes if o.metrics is not None)
        cost = sum(o.metrics.total_cost_eur for o in self.outcomes if o.metrics is not None)

        # Media e percentile si calcolano sull'insieme di tutti i tempi di
        # risposta, non sulle medie di finestra: vedi `response_minutes_pool`.
        responses = self.response_minutes_pool()

        validated = [o.valid for o in self.outcomes if o.valid is not None]
        return {
            "windows": self.windows_count,
            "fires_total": fires,
            "fires_covered": covered,
            "coverage_ratio": round(covered / fires, 6) if fires else 0.0,
            "fires_wui_total": wui,
            "fires_wui_covered": wui_covered,
            "wui_coverage_ratio": round(wui_covered / wui, 6) if wui else 1.0,
            "water_demanded_liters": demanded,
            "water_delivered_liters": delivered,
            "water_fulfilment_ratio": round(min(1.0, delivered / demanded), 6)
            if demanded
            else 1.0,
            "responses_counted": len(responses),
            "mean_response_minutes": round(sum(responses) / len(responses), 3)
            if responses
            else 0.0,
            "p90_response_minutes": round(_percentile(responses, 0.9), 3),
            "total_vehicle_minutes": round(minutes, 3),
            "total_vehicle_hours": round(minutes / 60.0, 3),
            "total_cost_eur": round(cost, 2),
            "plans_validated": len(validated),
            "plans_valid": sum(1 for v in validated if v),
            "plans_rejected": sum(1 for v in validated if not v),
        }

    def to_dict(self, *, include_timings: bool = True) -> dict:
        out = {
            "label": self.label,
            "producer": self.producer,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "window_hours": self.window_hours,
            "horizon_minutes": self.horizon_minutes,
            "aggregate": self.aggregate(),
            "windows": [o.to_dict(include_timings=include_timings) for o in self.outcomes],
        }
        if include_timings:
            out["total_plan_seconds"] = round(self.total_plan_seconds, 6)
        return out

    def summary_lines(self) -> list[str]:
        """Riassunto leggibile a schermo, per la riga di comando."""
        agg = self.aggregate()
        return [
            f"rigiocata {self.label or self.producer}: {agg['windows']} finestre",
            f"copertura {agg['coverage_ratio']:.3f} "
            f"({agg['fires_covered']}/{agg['fires_total']} focolai)",
            f"copertura WUI {agg['wui_coverage_ratio']:.3f}",
            f"risposta media {agg['mean_response_minutes']:.1f} min, "
            f"p90 {agg['p90_response_minutes']:.1f} min",
            f"acqua {agg['water_fulfilment_ratio']:.3f} del fabbisogno",
            f"ore-mezzo {agg['total_vehicle_hours']:.1f}",
            f"piani validi {agg['plans_valid']}/{agg['plans_validated']}",
        ]


# --------------------------------------------------------------------------
# Costruzione dello scenario e rigiocata di una finestra
# --------------------------------------------------------------------------


def build_scenario(
    window: ReplayWindow, context: ReplayContext, state: WorldState
) -> Scenario:
    """Lo scenario di una finestra: dati visibili a quell'ora piu' stato ereditato.

    I mezzi sono copie: lo stato del mondo viene scritto dentro queste copie e
    non tocca il modello di flotta tenuto nel contesto, che deve restare
    identico per la rigiocata successiva.
    """
    fires = [
        f
        for f in context.fires_visible_at(window.start)
        if f.fire_id not in state.extinguished_fire_ids
    ]
    vehicles = [replace(v) for v in context.vehicles]
    weather = context.weather
    scenario = Scenario(
        scenario_id=f"RPL_{window.index:04d}_{window.label}",
        timestamp=window.start,
        horizon_minutes=window.horizon_minutes,
        fires=fires,
        vehicles=vehicles,
        bases=list(context.bases),
        water_sources=list(context.water_sources),
        is_night=is_night_at(window.start),
        wind_kmh=float(weather.get("wind_kmh", 15.0)),
        temperature_c=float(weather.get("temperature_c", 30.0)),
        humidity_pct=float(weather.get("humidity_pct", 35.0)),
    )
    state.apply_to(scenario)
    if scenario.is_night:
        # Di notte i mezzi aerei non abilitati al volo notturno sono a terra.
        # Lo si dichiara nello stato del mezzo, cosi' lo vedono allo stesso modo
        # il pianificatore, il validatore e la demo.
        for v in scenario.vehicles:
            if v.is_aerial and not v.night_capable and v.status is VehicleStatus.AVAILABLE:
                v.status = VehicleStatus.OUT_OF_SHIFT
    return scenario


def replay_window(
    window: ReplayWindow,
    planner: Callable[..., Plan],
    context: ReplayContext,
    state: WorldState,
    *,
    constraints: Optional[ConstraintSet] = None,
    validator: Any = AUTO,
    metrics_fn: Any = AUTO,
) -> tuple[WindowOutcome, WorldState]:
    """Rigioca una sola finestra. Restituisce l'esito e lo stato per la successiva.

    E' pubblica di proposito: quando una rigiocata lunga produce un numero
    strano, si vuole poter rieseguire la singola finestra sospetta senza
    ripercorrere la stagione.
    """
    if validator is AUTO:
        validator = default_validator()
    if metrics_fn is AUTO:
        metrics_fn = default_metrics() or basic_metrics

    scenario = build_scenario(window, context, state)

    started = time.perf_counter()
    plan = call_planner(planner, scenario, constraints)
    elapsed = time.perf_counter() - started
    if not isinstance(plan, Plan):
        raise ReplayError(
            f"il pianificatore ha restituito {type(plan).__name__} invece di Plan"
        )

    report = None
    if validator is not None:
        try:
            report = _call_validator(validator, plan, scenario, constraints)
        except Exception as exc:
            # Un validatore che esplode non deve fermare la rigiocata: se ne
            # registra il guasto e si prosegue, altrimenti si perde anche cio'
            # che era gia' stato calcolato.
            report = f"validatore non eseguibile: {type(exc).__name__}: {exc}"

    metrics: Optional[PlanMetrics] = None
    metrics_source = METRICS_NONE
    if metrics_fn is not None:
        try:
            computed = _call_two(metrics_fn, plan, scenario)
        except Exception:
            # Le metriche di eval/ possono avere una firma diversa da quella
            # attesa mentre il sottosistema e' in scrittura: meglio il riassunto
            # minimo che una rigiocata interrotta. Il ripiego pero' non resta
            # muto: `metrics_source` lo dichiara, altrimenti una rigiocata
            # misurata con la convenzione sbagliata sarebbe indistinguibile da
            # una misurata con quella giusta.
            computed = None
        if isinstance(computed, PlanMetrics):
            metrics = computed
            metrics_source = (
                METRICS_FALLBACK if metrics_fn is basic_metrics else METRICS_OFFICIAL
            )
        else:
            metrics = basic_metrics(plan, scenario)
            metrics_source = METRICS_FALLBACK

    # Le metriche restano nell'esito e non vengono scritte dentro il piano. Il
    # piano dichiara quello che il pianificatore ha deciso; le metriche sono un
    # giudizio su quella decisione, e il validatore le confronta con le proprie.
    # Scriverle nel piano trasformerebbe una misura in una dichiarazione del
    # pianificatore, e il validatore segnalerebbe come discordanza una semplice
    # differenza di convenzione fra chi misura.

    next_state = state.advance(
        plan,
        scenario,
        window_minutes=window.duration_minutes,
        next_timestamp=window.end,
    )

    outcome = WindowOutcome(
        window=window,
        scenario_id=scenario.scenario_id,
        fires_total=len(scenario.fires),
        vehicles_available=sum(
            1 for v in scenario.vehicles if v.status is VehicleStatus.AVAILABLE
        ),
        plan=plan,
        metrics=metrics,
        validation=_validation_dict(report),
        plan_seconds=elapsed,
        state_after=next_state.to_dict(),
        metrics_source=metrics_source,
    )
    return outcome, next_state


# --------------------------------------------------------------------------
# Rigiocata della stagione
# --------------------------------------------------------------------------


def replay_season(
    start: datetime,
    end: datetime,
    planner: Callable[..., Plan],
    *,
    context: Optional[ReplayContext] = None,
    vehicles: Optional[Iterable[Vehicle]] = None,
    bases: Optional[Iterable[Base]] = None,
    water_sources: Optional[Iterable[WaterSource]] = None,
    fires: Optional[Iterable[FireCluster]] = None,
    detections: Optional[Iterable[Detection]] = None,
    fires_provider: Optional[Callable[..., list[FireCluster]]] = None,
    constraints: Optional[ConstraintSet] = None,
    window_hours: float = REPLAY_WINDOW_HOURS,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    lookback_hours: float = CLUSTER_TIME_WINDOW_HOURS,
    weather: Optional[Mapping[str, float]] = None,
    initial_state: Optional[WorldState] = None,
    validator: Any = AUTO,
    metrics_fn: Any = AUTO,
    max_windows: Optional[int] = None,
    label: str = "",
    save: bool = True,
    output_dir: Optional[Path] = None,
) -> ReplayResult:
    """Scorre l'intervallo a finestre e accumula i risultati.

    `planner` e' qualunque callable con la firma `(scenario, constraints) -> Plan`:
    il modello CP-SAT, una baseline, un pianificatore inventato per un'ablazione.
    Il motore non sa e non deve sapere quale sia.

    `max_windows=1` esegue una finestra sola, che e' il modo di lavorare quando
    si sta cercando un difetto.

    Le risorse si passano in due modi alternativi e non mescolabili: o un
    `context` gia' costruito, oppure i singoli argomenti (`vehicles`, `bases`,
    `water_sources`, `fires`, `detections`, `fires_provider`, `lookback_hours`,
    `weather`), da cui il contesto viene costruito qui. Con un `context` passato,
    quegli argomenti NON vengono riletti: il contesto li contiene gia' e vince
    lui. `window_hours` e `horizon_minutes` fanno eccezione, perche' descrivono
    il tempo e non le risorse: governano sempre le finestre, anche con un
    contesto passato.

    Deterministico per costruzione: nessuna sorgente casuale, iterazione su
    sequenze ordinate, e lo stato iniziale non viene modificato, quindi due
    esecuzioni con gli stessi parametri producono lo stesso registro (a meno dei
    tempi di calcolo, che `to_dict(include_timings=False)` esclude).
    """
    windows = build_windows(
        start, end, window_hours=window_hours, horizon_minutes=horizon_minutes
    )
    if max_windows is not None:
        if int(max_windows) <= 0:
            raise ReplayError(
                f"max_windows deve essere almeno 1, ricevuto {max_windows}: "
                "con zero finestre non c'e' niente da rigiocare"
            )
        windows = windows[: int(max_windows)]
    if not windows:  # pragma: no cover - build_windows solleva gia' sull'intervallo vuoto
        raise ReplayError("nessuna finestra da rigiocare nell'intervallo indicato")

    ctx = context or build_context(
        vehicles=vehicles,
        bases=bases,
        water_sources=water_sources,
        fires=fires,
        detections=detections,
        fires_provider=fires_provider,
        horizon_minutes=horizon_minutes,
        lookback_hours=lookback_hours,
        weather=weather,
    )

    # Validatore e metriche si cercano una volta sola, non a ogni finestra. Non
    # e' per la velocita': e' perche' una stagione dev'essere misurata tutta con
    # lo stesso metro. Risolvendoli dentro il ciclo, un modulo che diventa
    # importabile a meta' rigiocata (o che smette di esserlo) produrrebbe un
    # registro con le prime finestre contate in un modo e le ultime in un altro.
    if validator is AUTO:
        validator = default_validator()
    if metrics_fn is AUTO:
        metrics_fn = default_metrics() or basic_metrics

    # Lo stato iniziale ricevuto dal chiamante non si tocca: se ne prende una
    # copia. E' la difesa concreta contro lo stato che sanguina fra rigiocate,
    # perche' chi confronta solver e baseline passa lo stesso oggetto a entrambe.
    state = (
        initial_state.copy()
        if initial_state is not None
        else WorldState.initial(windows[0].start, ctx.vehicles, ctx.bases)
    )

    outcomes: list[WindowOutcome] = []
    for window in windows:
        outcome, state = replay_window(
            window,
            planner,
            ctx,
            state,
            constraints=constraints,
            validator=validator,
            metrics_fn=metrics_fn,
        )
        outcomes.append(outcome)

    producer = outcomes[0].plan.producer if outcomes else _planner_name(planner)
    result = ReplayResult(
        label=label or producer,
        producer=producer,
        start=as_utc(start),
        end=as_utc(end),
        window_hours=float(window_hours),
        horizon_minutes=int(horizon_minutes),
        outcomes=outcomes,
    )
    if save:
        result.output_path = save_result(result, output_dir=output_dir)
    return result


def _planner_name(planner: Callable[..., Plan]) -> str:
    return getattr(planner, "__name__", planner.__class__.__name__)


def save_result(result: ReplayResult, *, output_dir: Optional[Path] = None) -> Path:
    """Scrive il registro in JSON con un nome che dice che cosa contiene.

    Il nome e' `replay_<etichetta>_<inizio>_<fine>.json`, e l'etichetta vale il
    nome del produttore quando il chiamante non ne passa una: chi trova il file
    fra sei mesi capisce da solo chi ha pianificato e su quale intervallo, senza
    aprirlo. L'ora fa parte del nome perche' due rigiocate dello stesso giorno
    su intervalli diversi non devono sovrascriversi a vicenda.

    Due rigiocate con la stessa etichetta sullo stesso intervallo finiscono
    invece sullo stesso file, e la seconda sovrascrive la prima. E' voluto:
    rieseguire lo stesso esperimento deve aggiornare il suo risultato, non
    riempire la cartella di copie quasi identiche fra cui scegliere a mano.
    """
    directory = Path(output_dir) if output_dir is not None else PROCESSED_DIR
    directory.mkdir(parents=True, exist_ok=True)
    name = (
        f"replay_{_slug(result.label)}_"
        f"{result.start.strftime('%Y%m%dT%H%M')}_"
        f"{result.end.strftime('%Y%m%dT%H%M')}.json"
    )
    path = directory / name
    payload = result.to_dict(include_timings=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def _slug(text: str) -> str:
    """Nome di file prudente: lettere, cifre, trattini bassi."""
    cleaned = [c if c.isalnum() else "_" for c in text.strip().lower()]
    slug = "".join(cleaned).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "piano"
