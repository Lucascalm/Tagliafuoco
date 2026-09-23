"""Metriche di un piano: le convenzioni di misura, dichiarate una volta sola.

PERCHE' QUESTO MODULO ESISTE
----------------------------
Un piano si giudica con dei numeri, e i numeri dipendono da come li si conta.
Due persone che misurano "il tempo di risposta" dello stesso piano possono
ottenere valori diversi senza che nessuna delle due sbagli: una media per
assegnazione, l'altra per focolaio. Finche' la convenzione resta implicita, il
confronto fra Tagliafuoco e la baseline non significa niente, perche' nessuno
sa se la differenza viene dal pianificatore o dal contatore.

Questo modulo fissa le convenzioni, le scrive qui sopra in chiaro, e le applica
allo stesso modo a ogni pianificatore. Non e' il posto dove si decide se un
piano e' buono: e' il posto dove si decide che cosa vuol dire misurarlo.

LE CONVENZIONI, UNA PER UNA
---------------------------
1. Tempo di risposta: si media PER FOCOLAIO, non per assegnazione, prendendo
   l'arrivo del PRIMO mezzo. La ragione e' operativa: su un incendio conta
   quando arriva il primo getto d'acqua; il secondo e il terzo mezzo cambiano
   l'esito dello spegnimento, non il tempo di risposta. Mediare per
   assegnazione premierebbe inoltre chi manda tanti mezzi vicini sullo stesso
   focolaio e penalizzerebbe chi ne manda uno solo su un focolaio lontano,
   cioe' misurerebbe la distribuzione della flotta invece della prontezza.
   Nota di divergenza dichiarata: `validate/validator.py` ricalcola la stessa
   grandezza per assegnazione e con il percentile a rango piu' vicino. Non e'
   un errore di nessuno dei due: il validatore verifica la coerenza interna di
   un piano, la valutazione confronta pianificatori diversi. I due numeri
   possono differire e non vanno mescolati nella stessa tabella.

2. Copertura: un focolaio e' coperto quando riceve almeno
   `COVERAGE_WATER_RATIO` del suo fabbisogno d'acqua dichiarato (oggi 0.60).
   Non basta che un mezzo ci passi sopra. La soglia e' una taratura, non una
   verita' idraulica: nessuno sa quale frazione del fabbisogno stimato serva
   davvero a domare un fronte, perche' il fabbisogno stesso e' una stima
   (SPEC.md sezione 6, la costante dei 100.000 litri per km quadrato). Sta in
   una costante di modulo proprio perche' chi legge un numero di copertura
   possa cambiarla e vedere quanto il risultato ne dipende. Con la soglia a
   zero la copertura torna a essere "almeno un mezzo assegnato", che e' la
   convenzione piu' generosa possibile e viene comunque riportata a parte come
   `fires_attended`.
   Un focolaio con fabbisogno dichiarato nullo si considera coperto se ha
   ricevuto almeno un'assegnazione: senza fabbisogno la frazione non e'
   definita, e scartarlo falserebbe il denominatore.

3. Acqua consegnata: si conta per focolaio e si tronca al fabbisogno di quel
   focolaio. Un eccesso di litri su un incendio non compensa una mancanza su
   un altro, perche' l'acqua non e' trasferibile a posteriori. E' la stessa
   convenzione del riassunto minimo di `sim/replay.py`, scelta apposta per non
   creare una terza definizione.
   Divergenza dichiarata rispetto a SPEC.md sezione 6, che per `water_delivered_
   liters` chiede la somma cruda senza taglio, perche' un piano che versa il
   doppio del necessario lo deve mostrare. Le due richieste sono incompatibili
   in un campo solo, quindi qui ci sono due numeri e nessuno dei due e'
   nascosto: `PlanMetrics.water_delivered_liters` porta la somma troncata,
   quella che rende onesto `water_fulfilment_ratio` (senza troncamento
   l'eccesso su un focolaio maschererebbe la carenza su un altro proprio nel
   rapporto), e `MetricsDetail.water_declared_liters` porta la somma cruda di
   SPEC, con lo spreco visibile nella differenza fra le due.

4. Percentile: interpolazione lineare fra i due ranghi adiacenti, cioe' il
   metodo "linear" di numpy e il default piu' diffuso. Con una sola
   osservazione restituisce quella osservazione; con nessuna restituisce il
   valore di default (zero), che va letto come "non misurato", mai come
   "istantaneo". Il metodo e' dichiarato perche' su campioni piccoli le
   convenzioni divergono anche del venti per cento.

5. Minuti-mezzo: somma degli intervalli fra partenza e rilascio, troncati a
   zero se un piano malformato dichiarasse un rilascio precedente alla
   partenza. Il troncamento e' difensivo: le assegnazioni impossibili le
   segnala il validatore, non il contatore.

6. Costo: si somma il costo dichiarato dall'assegnazione. Quando il campo non
   e' positivo (assente, zero, o negativo, che e' comunque una dichiarazione
   impossibile) lo si ricostruisce dal costo orario del mezzo per i minuti di
   impiego, cosi' un pianificatore che non compila il campo non risulta
   gratuito. Il numero di ricostruzioni finisce in `costs_reconstructed`,
   perche' un costo ricostruito e' una stima di chi misura, non una
   dichiarazione di chi pianifica.
   Resta un terzo caso, quello in cui non si puo' ne' leggere ne' ricostruire:
   l'assegnazione non dichiara il costo e cita un mezzo che nello scenario non
   c'e', quindi non esiste nemmeno un costo orario. Quel caso si conta in
   `costs_unknown` e il totale risultante e' dichiarato sottostima. La
   tentazione opposta, saltarlo in silenzio, produce un costo piu' basso del
   vero senza che nessuno possa accorgersene: e' l'unico modo in cui un
   contatore riesce a mentire pur non sbagliando nessuna somma.

7. Denominatore: e' sempre lo SCENARIO, mai il piano. Le assegnazioni che
   citano un focolaio assente dallo scenario contro cui il piano viene misurato
   non entrano in copertura, tempi di risposta e acqua, e si contano in
   `assignments_off_scenario`. Senza questa regola un piano prodotto per
   un'altra finestra farebbe salire `responses_counted` sopra il numero di
   focolai esistenti, e la media dei tempi di risposta conterrebbe incendi che
   in quello scenario non ci sono. Minuti-mezzo e costo restano invece calcolati
   su tutto il piano, perche' misurano cio' che il piano IMPEGNA e non cio' che
   il piano OTTIENE, e un mezzo mandato dove non serve lo si e' impegnato
   ugualmente.

COSA QUESTO MODULO NON FA
-------------------------
Non scrive le metriche dentro il piano. `Plan.metrics` e' cio' che il
pianificatore dichiara di se' e il validatore lo confronta con il proprio
ricalcolo: scriverci dentro le metriche di valutazione trasformerebbe una
misura esterna in un'autodichiarazione, e il validatore segnalerebbe come
discordanza una semplice differenza di convenzione (vedi il punto 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from tagliafuoco.config import LATE_RESPONSE_MINUTES
from tagliafuoco.domain.models import Plan, PlanMetrics, Scenario

__all__ = [
    "COVERAGE_WATER_RATIO",
    "MetricDefinition",
    "MetricsDetail",
    "METRIC_DEFINITIONS",
    "compute_detail",
    "compute_metrics",
    "metric_value",
    "metrics_to_dict",
    "percentile",
    "response_minutes_by_fire",
    "water_received_by_fire",
]


#: Frazione del fabbisogno d'acqua sotto la quale un focolaio non si considera
#: coperto. Taratura dichiarata, non soglia fisica: vedi il punto 2 del
#: docstring di modulo. Cambiarla cambia tutte le coperture riportate, quindi
#: chi pubblica un numero di copertura deve pubblicare anche questo valore.
COVERAGE_WATER_RATIO: float = 0.60

#: Percentile riportato accanto alla media dei tempi di risposta. Il novantesimo
#: e' quello chiesto da SPEC.md sezione 7: dice come va quando va male, che e'
#: la domanda operativa, mentre la media nasconde le code.
RESPONSE_PERCENTILE: float = 0.90


# --------------------------------------------------------------------------
# Percentile
# --------------------------------------------------------------------------


def percentile(values: Sequence[float], q: float, *, default: float = 0.0) -> float:
    """Percentile `q` (fra 0 e 1) con interpolazione lineare fra ranghi adiacenti.

    Metodo dichiarato: ordinati i valori x[0..n-1], si calcola la posizione
    continua `h = (n - 1) * q` e si interpola fra `x[floor(h)]` e `x[ceil(h)]`.
    E' il metodo "linear" di numpy, il default di gran parte degli strumenti
    statistici.

    Casi degeneri, entrambi gestiti e nessuno dei due mascherato:
    - nessuna osservazione: si restituisce `default` (zero), che significa "non
      misurato". Chi stampa il numero deve dire quante osservazioni c'erano,
      altrimenti uno zero da lista vuota sembra un tempo di risposta perfetto.
    - una sola osservazione: si restituisce quella, per qualunque `q`. Con un
      solo dato non esistono code, e inventare un'interpolazione fra un punto e
      se' stesso non aggiungerebbe informazione.

    Perche' interpolare invece di prendere il rango piu' vicino: qui i
    percentili si confrontano fra pianificatori diversi sugli stessi scenari, e
    il rango piu' vicino e' una funzione a gradini del numero di osservazioni.
    Due piani con un numero diverso di focolai serviti finirebbero su gradini
    diversi per pura aritmetica del campione. L'interpolazione e' continua e
    quella differenza sparisce.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"il percentile va fra 0 e 1, ricevuto {q}")
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    if n == 0:
        return float(default)
    if n == 1:
        return ordered[0]
    h = (n - 1) * q
    low = int(h)
    high = min(low + 1, n - 1)
    weight = h - low
    return ordered[low] + (ordered[high] - ordered[low]) * weight


# --------------------------------------------------------------------------
# Estrazioni elementari dal piano
# --------------------------------------------------------------------------


def response_minutes_by_fire(plan: Plan) -> dict[str, float]:
    """Tempo di risposta di ogni focolaio servito: arrivo del primo mezzo.

    L'ordine delle chiavi e' quello alfabetico degli identificativi, non quello
    di iterazione del piano, cosi' due esecuzioni producono lo stesso
    dizionario e i JSON di due valutazioni si possono confrontare riga a riga.
    """
    first: dict[str, float] = {}
    for a in plan.assignments:
        arrival = float(a.arrival_minute)
        current = first.get(a.fire_id)
        if current is None or arrival < current:
            first[a.fire_id] = arrival
    return {k: first[k] for k in sorted(first)}


def water_received_by_fire(plan: Plan) -> dict[str, int]:
    """Litri dichiarati consegnati su ogni focolaio, sommati fra le assegnazioni.

    I litri negativi si troncano a zero, con lo stesso troncamento difensivo del
    punto 5 del docstring di modulo e per la stessa ragione. Non e' pignoleria:
    `PlanMetrics.water_fulfilment_ratio` e' un `min(1.0, erogati / richiesti)`
    senza pavimento, quindi una sola assegnazione con litri negativi produrrebbe
    un rapporto negativo, e il resoconto stamperebbe "acqua erogata sul
    fabbisogno: -300%" a una persona. Un piano che dichiara litri negativi e'
    malformato e lo deve dire il validatore; il contatore non deve tradurlo in
    una percentuale impossibile.
    """
    received: dict[str, int] = {}
    for a in plan.assignments:
        liters = max(0, int(a.water_delivered_liters))
        received[a.fire_id] = received.get(a.fire_id, 0) + liters
    return {k: received[k] for k in sorted(received)}


def _engaged_minutes(plan: Plan) -> float:
    """Minuti-mezzo totali, con il troncamento difensivo del punto 5."""
    return sum(max(0.0, float(a.engaged_minutes)) for a in plan.assignments)


# --------------------------------------------------------------------------
# Risultato esteso
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricsDetail:
    """`PlanMetrics` piu' cio' che serve a interpretarla senza rileggere il piano.

    `PlanMetrics` e' un contratto condiviso e non si tocca (SPEC.md sezione 3),
    ma da sola non basta a leggere i propri numeri: una media di zero minuti
    puo' voler dire "istantaneo" oppure "nessun focolaio servito", e le due cose
    sono opposte. I campi aggiunti qui servono esattamente a distinguere i due
    casi e a rendere il confronto appaiato onesto.
    """

    scenario_id: str
    plan_id: str
    producer: str
    metrics: PlanMetrics
    coverage_threshold: float
    fires_attended: int
    fires_wui_attended: int
    responses_counted: int
    late_fires: int
    assignments_total: int
    costs_reconstructed: int
    water_declared_liters: int = 0
    costs_unknown: int = 0
    assignments_off_scenario: int = 0
    response_minutes_by_fire: dict[str, float] = field(default_factory=dict)
    water_received_by_fire: dict[str, int] = field(default_factory=dict)

    @property
    def has_responses(self) -> bool:
        """Vero se esiste almeno un tempo di risposta misurato.

        Senza questo controllo un pianificatore che non assegna niente vince
        ogni confronto sui tempi, perche' la sua media e' zero.
        """
        return self.responses_counted > 0

    @property
    def water_overshoot_liters(self) -> int:
        """Litri versati oltre il fabbisogno del focolaio che li ha ricevuti.

        E' lo spreco che SPEC.md sezione 6 vuole visibile: la differenza fra la
        somma cruda delle assegnazioni e la somma troncata focolaio per focolaio.
        Zero significa che nessun focolaio ha ricevuto piu' del suo fabbisogno,
        non che il piano sia efficiente.
        """
        return max(0, self.water_declared_liters - self.metrics.water_delivered_liters)

    @property
    def late_ratio(self) -> float:
        """Frazione di focolai serviti oltre la soglia di tardivita'."""
        if not self.responses_counted:
            return 0.0
        return self.late_fires / self.responses_counted

    def caveats(self) -> list[str]:
        """Cio' che rende incompleto QUESTO numero, in righe da stampare.

        Sono i due modi in cui la misura puo' essere reticente senza che si
        veda: un costo che non si e' potuto ricostruire, e un'assegnazione che
        punta fuori dallo scenario contro cui il piano viene misurato. In
        entrambi i casi il totale stampato e' piu' basso del vero, e un totale
        piu' basso del vero senza una riga accanto e' un numero inventato per
        omissione. La lista vuota significa che non c'e' niente da dichiarare,
        non che non si sia guardato.
        """
        out: list[str] = []
        total = self.assignments_total
        if self.costs_unknown:
            uno = self.costs_unknown == 1
            quante = "1 assegnazione" if uno else f"{self.costs_unknown} assegnazioni"
            verbi = "dichiara un costo e cita" if uno else "dichiarano un costo e citano"
            out.append(
                f"Costo incompleto: {quante} su {total} non {verbi} un mezzo che "
                "nello scenario non c'e', quindi non esiste un costo orario da "
                "cui ricostruirlo. Il totale stampato e' una sottostima di quella "
                "parte."
            )
        if self.assignments_off_scenario:
            uno = self.assignments_off_scenario == 1
            quante = (
                "1 assegnazione"
                if uno
                else f"{self.assignments_off_scenario} assegnazioni"
            )
            punta = "punta" if uno else "puntano"
            esclusa = "E' esclusa" if uno else "Sono escluse"
            out.append(
                f"Piano e scenario non combaciano: {quante} su {total} {punta} a "
                f"focolai che in questo scenario non esistono. {esclusa} dalla "
                "copertura, dai tempi di risposta e dall'acqua, perche' misurare "
                "un piano contro uno scenario diverso da quello per cui e' stato "
                "prodotto non da' un numero interpretabile. Restano invece nei "
                "minuti-mezzo e nel costo, che misurano cio' che il piano "
                "impegna e non cio' che il piano ottiene."
            )
        return out

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            "plan_id": self.plan_id,
            "producer": self.producer,
            "coverage_threshold": self.coverage_threshold,
            "fires_attended": self.fires_attended,
            "fires_wui_attended": self.fires_wui_attended,
            "responses_counted": self.responses_counted,
            "late_fires": self.late_fires,
            "late_response_minutes": LATE_RESPONSE_MINUTES,
            "assignments_total": self.assignments_total,
            "assignments_off_scenario": self.assignments_off_scenario,
            "costs_reconstructed": self.costs_reconstructed,
            "costs_unknown": self.costs_unknown,
            "water_declared_liters": self.water_declared_liters,
            "water_overshoot_liters": self.water_overshoot_liters,
            "caveats": self.caveats(),
            "metrics": metrics_to_dict(self.metrics),
        }
        return out


def metrics_to_dict(metrics: PlanMetrics) -> dict[str, Any]:
    """Forma serializzabile di `PlanMetrics`, rapporti derivati compresi.

    I rapporti sono proprieta' e non campi: chi serializza solo i campi perde
    proprio i numeri che si leggono nel resoconto.
    """
    return {
        "fires_total": metrics.fires_total,
        "fires_covered": metrics.fires_covered,
        "fires_wui_total": metrics.fires_wui_total,
        "fires_wui_covered": metrics.fires_wui_covered,
        "water_demanded_liters": metrics.water_demanded_liters,
        "water_delivered_liters": metrics.water_delivered_liters,
        "mean_response_minutes": round(metrics.mean_response_minutes, 3),
        "p90_response_minutes": round(metrics.p90_response_minutes, 3),
        "total_vehicle_minutes": round(metrics.total_vehicle_minutes, 3),
        "total_cost_eur": round(metrics.total_cost_eur, 2),
        "vehicles_used": metrics.vehicles_used,
        "coverage_ratio": round(metrics.coverage_ratio, 6),
        "wui_coverage_ratio": round(metrics.wui_coverage_ratio, 6),
        "water_fulfilment_ratio": round(metrics.water_fulfilment_ratio, 6),
    }


# --------------------------------------------------------------------------
# Calcolo
# --------------------------------------------------------------------------


def compute_detail(
    plan: Plan,
    scenario: Scenario,
    *,
    coverage_threshold: float = COVERAGE_WATER_RATIO,
) -> MetricsDetail:
    """Calcola le metriche del piano sullo scenario, con i campi di contesto.

    Il piano non viene modificato: la misura e' una lettura, come la
    validazione. Chi vuole le sole `PlanMetrics` usa `compute_metrics`, che e'
    un involucro di questa funzione.

    Il denominatore di ogni grandezza e' lo SCENARIO, non il piano. Le
    assegnazioni che citano un focolaio assente da questo scenario vengono
    escluse da tempi, copertura e acqua, e contate a parte in
    `assignments_off_scenario`. Senza quel filtro un piano prodotto per un'altra
    finestra farebbe salire `responses_counted` sopra il numero di focolai
    esistenti, e la media dei tempi di risposta conterrebbe incendi che in
    questo scenario non ci sono: un numero che non e' ne' giusto ne'
    riconoscibile come sbagliato. Escluderle in silenzio sarebbe altrettanto
    disonesto, per questo il conteggio finisce nel risultato e nei `caveats`.
    """
    if not 0.0 <= coverage_threshold <= 1.0:
        raise ValueError(
            f"la soglia di copertura va fra 0 e 1, ricevuta {coverage_threshold}"
        )

    fire_ids = {f.fire_id for f in scenario.fires}
    all_responses = response_minutes_by_fire(plan)
    all_received = water_received_by_fire(plan)
    responses = {k: v for k, v in all_responses.items() if k in fire_ids}
    received = {k: v for k, v in all_received.items() if k in fire_ids}
    off_scenario = sum(1 for a in plan.assignments if a.fire_id not in fire_ids)

    fires_total = len(scenario.fires)
    fires_wui = [f for f in scenario.fires if f.is_wui]

    covered: set[str] = set()
    attended: set[str] = set()
    delivered_capped = 0
    demanded = 0

    for fire in scenario.fires:
        demand = int(fire.water_demand_liters)
        demanded += demand
        got = int(received.get(fire.fire_id, 0))
        has_assignment = fire.fire_id in responses
        if has_assignment:
            attended.add(fire.fire_id)
        # Punto 3: l'acqua si tronca al fabbisogno del singolo focolaio.
        delivered_capped += min(got, demand) if demand > 0 else got
        # Punto 2: la soglia decide la copertura; senza fabbisogno dichiarato
        # la frazione non e' definita e vale la presenza del mezzo.
        if demand > 0:
            if got >= demand * coverage_threshold and has_assignment:
                covered.add(fire.fire_id)
        elif has_assignment:
            covered.add(fire.fire_id)

    response_values = [responses[k] for k in sorted(responses)]
    mean_response = (
        sum(response_values) / len(response_values) if response_values else 0.0
    )

    total_cost, reconstructed, unknown_cost = _total_cost(plan, scenario)

    metrics = PlanMetrics(
        fires_total=fires_total,
        fires_covered=len(covered),
        fires_wui_total=len(fires_wui),
        fires_wui_covered=sum(1 for f in fires_wui if f.fire_id in covered),
        water_demanded_liters=demanded,
        water_delivered_liters=delivered_capped,
        mean_response_minutes=round(mean_response, 3),
        p90_response_minutes=round(percentile(response_values, RESPONSE_PERCENTILE), 3),
        total_vehicle_minutes=round(_engaged_minutes(plan), 3),
        total_cost_eur=round(total_cost, 2),
        vehicles_used=len({a.vehicle_id for a in plan.assignments}),
    )

    return MetricsDetail(
        scenario_id=scenario.scenario_id,
        plan_id=plan.plan_id,
        producer=plan.producer,
        metrics=metrics,
        coverage_threshold=float(coverage_threshold),
        fires_attended=len(attended),
        fires_wui_attended=sum(1 for f in fires_wui if f.fire_id in attended),
        responses_counted=len(response_values),
        late_fires=sum(1 for v in response_values if v > LATE_RESPONSE_MINUTES),
        assignments_total=len(plan.assignments),
        costs_reconstructed=reconstructed,
        costs_unknown=unknown_cost,
        assignments_off_scenario=off_scenario,
        # La somma cruda si ferma anch'essa ai focolai dello scenario: altrimenti
        # lo spreco (cruda meno troncata) conterebbe litri versati su incendi che
        # qui non esistono, e sembrerebbe uno spreco del pianificatore.
        water_declared_liters=sum(all_received[k] for k in received),
        response_minutes_by_fire=dict(responses),
        water_received_by_fire=dict(received),
    )


def compute_metrics(
    plan: Plan,
    scenario: Scenario,
    *,
    coverage_threshold: float = COVERAGE_WATER_RATIO,
) -> PlanMetrics:
    """Le metriche ufficiali del confronto, nella forma del contratto di dominio.

    E' la funzione che `sim/replay.py` cerca per sostituire il proprio riassunto
    minimo, quindi la firma `(plan, scenario)` non va cambiata senza guardare
    `replay.default_metrics`.
    """
    return compute_detail(
        plan, scenario, coverage_threshold=coverage_threshold
    ).metrics


def _total_cost(plan: Plan, scenario: Scenario) -> tuple[float, int, int]:
    """Costo totale, quante volte e' stato ricostruito e quante non si e' potuto.

    Vedi il punto 6 del docstring di modulo: un campo costo non positivo (zero,
    assente, o negativo, che e' comunque una dichiarazione impossibile) non
    rende gratuito un pianificatore, ma la ricostruzione va contata, perche' il
    numero che ne esce e' una stima di chi misura.

    Il terzo valore restituito e' il caso peggiore dei tre: l'assegnazione non
    dichiara il costo E cita un mezzo che nello scenario non c'e', quindi non
    esiste nemmeno un costo orario da cui ricostruirlo. Prima quell'assegnazione
    veniva saltata in silenzio e il totale usciva piu' basso del vero senza che
    nessuna riga lo dicesse. Ora si conta, perche' "questo pezzo di costo non lo
    so" e' un'informazione, mentre un totale reticente e' un numero inventato.
    """
    total = 0.0
    reconstructed = 0
    unknown = 0
    for a in plan.assignments:
        declared = float(a.estimated_cost_eur)
        if declared > 0.0:
            total += declared
            continue
        vehicle = scenario.vehicle_by_id(a.vehicle_id)
        if vehicle is None:
            unknown += 1
            continue
        minutes = max(0.0, float(a.engaged_minutes))
        total += minutes / 60.0 * float(vehicle.cost_per_hour_eur)
        reconstructed += 1
    return total, reconstructed, unknown


# --------------------------------------------------------------------------
# Repertorio delle metriche confrontabili
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricDefinition:
    """Una metrica con tutto cio' che serve per confrontarla e per stamparla.

    Il verso (`higher_is_better`) sta qui e non nel codice del confronto perche'
    e' la fonte piu' comune di errori di lettura: una differenza di meno tre
    minuti sul tempo di risposta e' un miglioramento, una differenza di meno tre
    punti di copertura e' un peggioramento, e il segno da solo non lo dice.

    Il getter prende un `MetricsDetail` e non una `PlanMetrics` perche' alcune
    grandezze confrontabili (la presenza di un mezzo sul focolaio, la quota di
    interventi tardivi) non stanno nel contratto di dominio e non possono
    starci: `PlanMetrics` e' condivisa da tutti i sottosistemi e non si
    estende per comodita' della valutazione.

    `needs_responses` marca le metriche che esistono solo se qualche focolaio e'
    stato servito. Un piano vuoto ha media zero e percentile zero: senza questa
    bandiera vincerebbe ogni confronto sui tempi senza aver spento niente.
    """

    key: str
    label: str
    unit: str
    higher_is_better: bool
    getter: Callable[["MetricsDetail"], float]
    needs_responses: bool = False
    digits: int = 3


METRIC_DEFINITIONS: dict[str, MetricDefinition] = {
    "coverage_ratio": MetricDefinition(
        key="coverage_ratio",
        label="copertura dei focolai",
        unit="frazione",
        higher_is_better=True,
        getter=lambda d: d.metrics.coverage_ratio,
    ),
    "attendance_ratio": MetricDefinition(
        key="attendance_ratio",
        label="focolai raggiunti da almeno un mezzo",
        unit="frazione",
        higher_is_better=True,
        getter=lambda d: (
            d.fires_attended / d.metrics.fires_total if d.metrics.fires_total else 0.0
        ),
    ),
    "wui_attendance_ratio": MetricDefinition(
        key="wui_attendance_ratio",
        label="focolai WUI raggiunti da almeno un mezzo",
        unit="frazione",
        higher_is_better=True,
        getter=lambda d: (
            d.fires_wui_attended / d.metrics.fires_wui_total
            if d.metrics.fires_wui_total
            else 1.0
        ),
    ),
    "late_ratio": MetricDefinition(
        key="late_ratio",
        label="interventi tardivi fra quelli effettuati",
        unit="frazione",
        higher_is_better=False,
        getter=lambda d: d.late_ratio,
        needs_responses=True,
    ),
    "wui_coverage_ratio": MetricDefinition(
        key="wui_coverage_ratio",
        label="copertura in interfaccia urbano-foresta",
        unit="frazione",
        higher_is_better=True,
        getter=lambda d: d.metrics.wui_coverage_ratio,
    ),
    "water_fulfilment_ratio": MetricDefinition(
        key="water_fulfilment_ratio",
        label="acqua erogata sul fabbisogno",
        unit="frazione",
        higher_is_better=True,
        getter=lambda d: d.metrics.water_fulfilment_ratio,
    ),
    "mean_response_minutes": MetricDefinition(
        key="mean_response_minutes",
        label="tempo di risposta medio",
        unit="minuti",
        higher_is_better=False,
        getter=lambda d: d.metrics.mean_response_minutes,
        needs_responses=True,
        digits=2,
    ),
    "p90_response_minutes": MetricDefinition(
        key="p90_response_minutes",
        label="tempo di risposta al novantesimo percentile",
        unit="minuti",
        higher_is_better=False,
        getter=lambda d: d.metrics.p90_response_minutes,
        needs_responses=True,
        digits=2,
    ),
    "total_vehicle_minutes": MetricDefinition(
        key="total_vehicle_minutes",
        label="minuti-mezzo impiegati",
        unit="minuti",
        higher_is_better=False,
        getter=lambda d: d.metrics.total_vehicle_minutes,
        digits=1,
    ),
    "total_cost_eur": MetricDefinition(
        key="total_cost_eur",
        label="costo stimato",
        unit="euro",
        higher_is_better=False,
        getter=lambda d: d.metrics.total_cost_eur,
        digits=0,
    ),
    "vehicles_used": MetricDefinition(
        key="vehicles_used",
        label="mezzi impiegati",
        unit="mezzi",
        higher_is_better=False,
        getter=lambda d: float(d.metrics.vehicles_used),
        digits=1,
    ),
}

#: Metriche riportate di default nel confronto. Sono quelle chieste da SPEC.md
#: sezione 7: copertura, copertura WUI, tempi, acqua, ore-mezzo.
DEFAULT_METRIC_KEYS: tuple[str, ...] = (
    "coverage_ratio",
    "attendance_ratio",
    "wui_coverage_ratio",
    "water_fulfilment_ratio",
    "mean_response_minutes",
    "p90_response_minutes",
    "total_vehicle_minutes",
)


def metric_value(detail: MetricsDetail, key: str) -> Optional[float]:
    """Valore di una metrica, oppure None quando per quel piano non e' definita.

    Restituire None invece di zero e' la scelta che rende onesto il confronto
    appaiato: la coppia in cui uno dei due piani non ha servito nessun focolaio
    viene scartata e il conteggio delle coppie scartate finisce nel resoconto,
    invece di entrare nella media come uno zero immeritato.
    """
    definition = METRIC_DEFINITIONS.get(key)
    if definition is None:
        raise KeyError(f"metrica sconosciuta: {key}")
    if definition.needs_responses and not detail.has_responses:
        return None
    return float(definition.getter(detail))


def describe_conventions() -> list[str]:
    """Le convenzioni in forma di righe, per stamparle accanto ai numeri.

    Il resoconto le mostra sempre: un numero di copertura senza la soglia che lo
    definisce non e' verificabile da chi legge.
    """
    return [
        "tempo di risposta: per focolaio, arrivo del primo mezzo",
        f"copertura: almeno il {COVERAGE_WATER_RATIO:.0%} del fabbisogno d'acqua "
        "del focolaio",
        "acqua: troncata al fabbisogno di ogni focolaio, nessuna compensazione "
        "fra focolai; la somma cruda resta in water_declared_liters e lo spreco "
        "nella differenza",
        "percentile: interpolazione lineare fra ranghi adiacenti (metodo "
        "'linear')",
        f"tardivita': arrivo oltre {LATE_RESPONSE_MINUTES} minuti",
        "focolai: il denominatore e' lo scenario, non il piano; le assegnazioni "
        "a focolai assenti dallo scenario sono escluse e contate a parte",
    ]


def summarise_details(details: Iterable[MetricsDetail]) -> Mapping[str, Any]:
    """Somme di stagione su piu' scenari, con i denominatori a vista.

    La copertura aggregata si calcola sui conteggi (focolai coperti sul totale)
    e non come media delle coperture di scenario: uno scenario con due focolai
    non pesa quanto uno con quaranta. E' la stessa convenzione di
    `sim/replay.ReplayResult.aggregate`, ripetuta qui apposta perche' due
    aggregazioni diverse dello stesso dato sono un errore che nessuno vede.
    """
    rows = list(details)
    fires = sum(d.metrics.fires_total for d in rows)
    covered = sum(d.metrics.fires_covered for d in rows)
    wui = sum(d.metrics.fires_wui_total for d in rows)
    wui_covered = sum(d.metrics.fires_wui_covered for d in rows)
    demanded = sum(d.metrics.water_demanded_liters for d in rows)
    delivered = sum(d.metrics.water_delivered_liters for d in rows)
    responses: list[float] = []
    for d in rows:
        responses.extend(d.response_minutes_by_fire[k] for k in sorted(d.response_minutes_by_fire))
    return {
        "scenarios": len(rows),
        "fires_total": fires,
        "fires_covered": covered,
        "fires_attended": sum(d.fires_attended for d in rows),
        "attendance_ratio": round(sum(d.fires_attended for d in rows) / fires, 6)
        if fires
        else 0.0,
        "fires_wui_attended": sum(d.fires_wui_attended for d in rows),
        "coverage_ratio": round(covered / fires, 6) if fires else 0.0,
        "fires_wui_total": wui,
        "fires_wui_covered": wui_covered,
        "wui_coverage_ratio": round(wui_covered / wui, 6) if wui else 1.0,
        "water_demanded_liters": demanded,
        "water_delivered_liters": delivered,
        "water_declared_liters": sum(d.water_declared_liters for d in rows),
        "water_overshoot_liters": sum(d.water_overshoot_liters for d in rows),
        "water_fulfilment_ratio": round(min(1.0, delivered / demanded), 6)
        if demanded
        else 1.0,
        "responses_counted": len(responses),
        "mean_response_minutes": round(sum(responses) / len(responses), 3)
        if responses
        else 0.0,
        "p90_response_minutes": round(percentile(responses, RESPONSE_PERCENTILE), 3),
        "total_vehicle_minutes": round(
            sum(d.metrics.total_vehicle_minutes for d in rows), 3
        ),
        "total_cost_eur": round(sum(d.metrics.total_cost_eur for d in rows), 2),
        "late_fires": sum(d.late_fires for d in rows),
        # I due conteggi di reticenza viaggiano con la somma di stagione, non
        # solo con il singolo scenario: un costo mancante su una finestra sola
        # sparisce dentro un totale di otto finestre, ed e' proprio li' che
        # nessuno lo vedrebbe piu'.
        "costs_reconstructed": sum(d.costs_reconstructed for d in rows),
        "costs_unknown": sum(d.costs_unknown for d in rows),
        "assignments_off_scenario": sum(d.assignments_off_scenario for d in rows),
    }
