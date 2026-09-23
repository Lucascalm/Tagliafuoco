"""Funzione obiettivo del solver: come si mettono d'accordo cinque criteri.

Perche' una somma pesata e non un'ottimizzazione lessicografica pura
--------------------------------------------------------------------
Il problema ha cinque criteri che tirano in direzioni diverse: coprire piu'
focolai possibile, proteggere prima le zone vicino alle case, arrivare presto,
versare l'acqua che serve, non bruciare ore-mezzo. Ci sono due modi classici
di trattarli.

Il primo e' lessicografico: si ordinano i criteri per importanza, si ottimizza
il primo, si congela il suo valore ottimo come vincolo e si ottimizza il
secondo, e cosi' via. E' onesto quando l'ordine di importanza e' davvero
assoluto. Qui non lo e'. Un ordine assoluto direbbe che un guadagno
infinitesimo di copertura vale piu' di qualunque risparmio di costo, e che due
piani con la stessa copertura sono indistinguibili al primo stadio anche se
uno arriva in venti minuti e l'altro in due ore. Non e' come ragiona un
coordinatore: lui scambia, dice "questo lo lascio andare per prendere
quell'altro".

Il secondo e' la somma pesata, che e' quello che usiamo. Ha due difetti noti e
dichiarati: (a) non puo' raggiungere i punti non supportati della frontiera di
Pareto, cioe' certi compromessi restano invisibili qualunque peso si scelga, e
(b) i pesi hanno senso solo se i termini sono normalizzati sulla stessa scala.
Il punto (b) qui e' risolto: ogni termine e' diviso per il suo massimo teorico,
quindi vive in [0, 1] e il peso dice davvero quanto conta quel criterio
rispetto agli altri. Il punto (a) si accetta: in cambio si ha un solo
passaggio di risoluzione, un limite di tempo unico, e pesi che il coordinatore
puo' cambiare a parole tramite il vincolo OBJECTIVE_WEIGHT.

Come si otterrebbe quella lessicografica, se servisse
-----------------------------------------------------
Si risolve a stadi. Stadio 1: si massimizza il solo termine di copertura e si
legge il valore ottimo `z1`. Stadio 2: si ricostruisce lo stesso modello, si
aggiunge il vincolo `copertura >= z1` (oppure `>= z1 - epsilon`, per lasciare
un margine di tolleranza ed evitare che l'ottimalita' del primo stadio renda
infattibile il secondo per un'inezia) e si massimizza il termine successivo.
Si ripete fino all'ultimo criterio. Costa tanti `Solve()` quanti sono i
criteri, e ogni stadio eredita un modello piu' stretto del precedente. Il
codice qui sotto e' gia' predisposto: `build_terms` restituisce i termini
separati, quindi uno stadio puo' massimizzarne uno solo e vincolare gli altri.

Ogni termine e' lo specchio di una metrica
-------------------------------------------
Scelta deliberata: ogni addendo dell'obiettivo corrisponde a una proprieta' di
`PlanMetrics`, cosi' quando si leggono i risultati della valutazione si sta
guardando la stessa cosa che il solver stava ottimizzando, e non due grandezze
imparentate alla lontana.

    coverage         <-> PlanMetrics.coverage_ratio (pesato sulla priorita')
    wui_protection   <-> PlanMetrics.wui_coverage_ratio
    response_time    <-> ritardi oltre LATE_RESPONSE_MINUTES
    water_fulfilment <-> PlanMetrics.water_fulfilment_ratio
    cost             <-> PlanMetrics.total_cost_eur

Aritmetica intera
-----------------
CP-SAT non conosce i numeri con la virgola: tutti i coefficienti sono interi.
Ogni coefficiente viene quindi calcolato come
`peso * OBJECTIVE_PRECISION * quota / denominatore` e arrotondato. Con il
milione, un termine normalizzato al massimo del suo peso vale
`peso * 1_000_000`: i pesi di default arrivano a 10, quindi si resta sotto i
10^7 per termine, ben dentro l'intero a 64 bit anche moltiplicando per domini
da qualche migliaio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tagliafuoco.optimize.geometry import TIME_SCALE
from tagliafuoco.optimize.model import LATE_THRESHOLD_UNITS, ModelVars

# --------------------------------------------------------------------------
# Tarature locali dell'obiettivo
# --------------------------------------------------------------------------

# Fattore di conversione da pesi reali a coefficienti interi.
OBJECTIVE_PRECISION = 1_000_000

# Quanto pesa la violazione piena di un vincolo MORBIDO tradotto dall'ordine.
# Sta sulla stessa scala dei pesi di OBJECTIVE_WEIGHTS (che arrivano a 10):
# una preferenza del coordinatore vale circa mezzo termine di copertura.
# Taratura dichiarata.
SOFT_CONSTRAINT_WEIGHT = 5.0

# Quanto pesa la violazione di un vincolo DURO che e' stato rilassato per
# rendere il piano fattibile. Molto piu' alto della somma di tutti i pesi di
# default (10+8+4+3+1 = 26), cosi' il solver viola un ordine solo quando non ha
# davvero alternativa, e ne viola il meno possibile.
RELAXED_HARD_WEIGHT = 200.0

# Costo fisso di attivazione di un mezzo, espresso in frazione di ora al suo
# costo orario. Modella cio' che si paga comunque quando si mette in moto un
# mezzo (equipaggio, preparazione, usura del decollo) e scoraggia i piani che
# spargono dieci mezzi per fare il lavoro di tre. Taratura dichiarata.
ACTIVATION_HOURS = 0.25


@dataclass
class ObjectiveTerms:
    """I termini dell'obiettivo tenuti separati.

    Servono separati per due motivi: si possono ispezionare uno per uno nei
    test, e sono il mattone su cui si costruirebbe la risoluzione a stadi
    descritta nel docstring del modulo.
    """

    coverage: Any = 0
    wui_protection: Any = 0
    response_time: Any = 0
    water_fulfilment: Any = 0
    cost: Any = 0
    penalties: Any = 0

    details: dict[str, str] = field(default_factory=dict)

    def total(self) -> Any:
        """Somma da massimizzare. I termini di penalita' hanno gia' il segno."""
        return (
            self.coverage
            + self.wui_protection
            + self.response_time
            + self.water_fulfilment
            + self.cost
            + self.penalties
        )


def _coeff(weight: float, share: float, denominator: float) -> int:
    """Coefficiente intero di un addendo normalizzato.

    `share / denominator` e' la frazione di termine che quell'addendo porta,
    `weight` e' il peso del criterio, `OBJECTIVE_PRECISION` la conversione in
    intero.

    Il caso limite da guardare e' l'arrotondamento a zero: se il denominatore
    e' molto grande (per esempio milioni di litri di fabbisogno) il
    coefficiente esatto puo' valere 0.4 e sparire nell'arrotondamento,
    facendo evaporare in silenzio un intero criterio. Quando succede si tiene
    1 invece di 0: il criterio pesa poco ma non scompare.
    """
    if denominator <= 0 or weight <= 0 or share <= 0:
        return 0
    exact = weight * OBJECTIVE_PRECISION * share / denominator
    return max(1, int(round(exact)))


def build_terms(mv: ModelVars) -> ObjectiveTerms:
    """Costruisce i termini pesati dell'obiettivo, senza ancora installarlo.

    I pesi vengono letti da `mv.weights`, che parte da
    `config.OBJECTIVE_WEIGHTS` e puo' essere stato riscritto da un vincolo
    OBJECTIVE_WEIGHT prima di arrivare qui.
    """
    scenario = mv.scenario
    fires = scenario.fires
    terms = ObjectiveTerms()

    w = mv.weights
    priority = {f.fire_id: mv.priority_of(f) for f in fires}
    total_priority = sum(priority.values())

    # ---- 1. copertura, pesata sulla priorita' del focolaio ---------------
    # Denominatore: la somma delle priorita'. Coprire tutti i focolai porta il
    # termine al suo massimo, cioe' a `peso * PRECISION`.
    coverage_parts = [
        _coeff(w.get("coverage", 0.0), priority[f.fire_id], total_priority)
        * mv.covered[f.fire_id]
        for f in fires
        if f.fire_id in mv.covered
    ]
    terms.coverage = sum(coverage_parts) if coverage_parts else 0

    # ---- 2. protezione dell'interfaccia urbano-foresta -------------------
    # Specchio di `wui_coverage_ratio`: quanti dei focolai vicini alle case
    # sono stati coperti. Se non ce ne sono il termine non esiste, coerente
    # con la convenzione della metrica (che in quel caso vale 1).
    wui_fires = [f for f in fires if f.is_wui and f.fire_id in mv.covered]
    wui_parts = [
        _coeff(w.get("wui_protection", 0.0), 1.0, len(wui_fires)) * mv.covered[f.fire_id]
        for f in wui_fires
    ]
    terms.wui_protection = sum(wui_parts) if wui_parts else 0

    # ---- 3. tempo di risposta -------------------------------------------
    # Si penalizza solo il ritardo OLTRE LATE_RESPONSE_MINUTES: arrivare in
    # venti minuti invece che in trenta non cambia l'esito di un attacco
    # iniziale, arrivare in due ore si'. La soglia e' una taratura di config.
    # Normalizzazione: un mezzo che arriva a fine orizzonte su ogni focolaio
    # porta il termine al suo peso pieno. Con piu' mezzi in ritardo sullo
    # stesso focolaio il termine puo' superarlo, ed e' voluto: ogni ritardo
    # conta per se'.
    # Massimo ritardo rappresentabile: dalla soglia di tardivita' alla fine
    # dell'orizzonte. Il `max(1, ...)` copre gli orizzonti piu' corti della
    # soglia stessa (una finestra di mezz'ora con soglia a quarantacinque
    # minuti): li' nessun ritardo e' possibile per costruzione, e senza il
    # pavimento si dividerebbe per zero o per un numero negativo.
    #
    # La soglia si importa da `model` e non si riconverte qui: e' la stessa
    # costante con cui `_build_pair` definisce la variabile `lateness`. Se i
    # due punti la arrotondassero in direzioni diverse, il denominatore non
    # corrisponderebbe piu' al valore massimo della variabile e il termine di
    # ritardo potrebbe superare il proprio peso. Vedi `model.LATE_THRESHOLD_UNITS`.
    late_span = max(1, mv.horizon_units - LATE_THRESHOLD_UNITS)
    response_parts = [
        _coeff(
            w.get("response_time", 0.0),
            priority[p.fire_id],
            total_priority * late_span,
        )
        * p.lateness
        for p in mv.pairs.values()
        if p.fire_id in priority
    ]
    terms.response_time = -sum(response_parts) if response_parts else 0

    # ---- 4. soddisfacimento idrico --------------------------------------
    # Specchio di `water_fulfilment_ratio`, ma pesato sulla priorita': mille
    # litri non erogati su un focolaio vicino alle case pesano piu' di mille
    # litri non erogati in mezzo al bosco. E' qui che PRIORITIZE_ZONE si fa
    # sentire anche quando la copertura piena e' fuori portata per tutti,
    # perche' e' un termine a credito parziale, non tutto o niente.
    water_denominator = sum(
        priority[f.fire_id] * max(0, int(f.water_demand_liters))
        for f in fires
        if f.fire_id in priority
    )
    water_parts = [
        _coeff(w.get("water_fulfilment", 0.0), priority[f.fire_id], water_denominator)
        * mv.unmet_liters[f.fire_id]
        for f in fires
        if f.fire_id in mv.unmet_liters
    ]
    terms.water_fulfilment = -sum(water_parts) if water_parts else 0

    # ---- 5. costo --------------------------------------------------------
    cost_expr, cost_reference = _build_cost_term(mv, w.get("cost", 0.0))
    terms.cost = cost_expr

    # ---- 6. penalita' dei vincoli morbidi --------------------------------
    penalty_parts = [
        _coeff(pt.unit_weight, 1.0, pt.max_value) * pt.expr for pt in mv.penalties
    ]
    terms.penalties = -sum(penalty_parts) if penalty_parts else 0

    terms.details = {
        "total_priority": f"{total_priority:.3f}",
        "wui_fires": str(len(wui_fires)),
        "water_denominator_liters": str(int(water_denominator)),
        "cost_reference_eur": f"{cost_reference:.0f}",
        "penalty_terms": str(len(mv.penalties)),
    }
    return terms


def _build_cost_term(mv: ModelVars, weight: float) -> tuple[Any, float]:
    """Termine di costo, gia' normalizzato, piu' il costo di riferimento.

    Due addendi: il costo orario per i minuti di impegno effettivo, e il costo
    fisso di attivazione di ogni mezzo usato. Il secondo e' quello che
    distingue "tre mezzi impegnati due ore" da "sei mezzi impegnati un'ora":
    senza costo fisso i due piani costerebbero uguale, e nella realta' non e'
    cosi'.

    Il riferimento e' il costo di impiegare TUTTA la flotta per l'INTERO
    turno. Cosi' `cost = 1` significa: spendere la giornata intera di tutti i
    mezzi pesa quanto un punto pieno di copertura. E' un denominatore
    dichiarato e non ovvio: sceglierne uno piu' piccolo (per esempio
    l'orizzonte invece del turno) renderebbe il costo molto piu' aggressivo a
    parita' di peso.
    """
    scenario = mv.scenario
    reference = 0.0
    # Si sommano TUTTI i mezzi dello scenario, anche quelli non impiegabili
    # (fermi, in manutenzione, a terra per vento). E' voluto: il riferimento
    # deve descrivere la flotta, non l'esito del filtro di impiegabilita'.
    # Normalizzare sui soli mezzi usabili renderebbe il termine di costo piu'
    # o meno aggressivo a seconda di quanti mezzi sono fermi quel giorno, e i
    # piani di due finestre diverse smetterebbero di essere confrontabili.
    #
    # Controllo delle unita': [euro/ora] * ([minuti]/[minuti per ora] + [ore])
    # = euro. Il termine dell'obiettivo divide per questo, quindi e'
    # adimensionale, come tutti gli altri.
    for vehicle in scenario.vehicles:
        reference += float(vehicle.cost_per_hour_eur) * (
            float(vehicle.max_shift_minutes) / 60.0 + ACTIVATION_HOURS
        )
    # Flotta a costo zero (o peso azzerato dal coordinatore): non c'e' niente
    # da minimizzare e soprattutto non si puo' dividere per il riferimento.
    if reference <= 0 or weight <= 0:
        return 0, reference

    parts: list[Any] = []

    for p in mv.pairs.values():
        vehicle = scenario.vehicle_by_id(p.vehicle_id)
        if vehicle is None:
            continue
        # Euro spesi per ogni unita' di tempo di impegno. La catena di
        # divisioni e' la conversione delle unita', e va letta da sinistra:
        # [euro/ora] / [minuti per ora] = [euro/minuto], poi / [unita' per
        # minuto] = [euro/unita']. Moltiplicata per `work`, che e' in unita',
        # torna euro. Sbagliare uno di questi due passaggi e' il modo piu'
        # facile di ottenere un termine di costo dieci o sessanta volte fuori
        # scala senza che niente si rompa in modo visibile.
        eur_per_unit = float(vehicle.cost_per_hour_eur) / 60.0 / float(TIME_SCALE)
        coefficient = _coeff(weight, eur_per_unit, reference)
        if coefficient:
            parts.append(coefficient * p.work)

    for vehicle_id, used in mv.used.items():
        vehicle = scenario.vehicle_by_id(vehicle_id)
        if vehicle is None:
            continue
        activation_eur = float(vehicle.cost_per_hour_eur) * ACTIVATION_HOURS
        coefficient = _coeff(weight, activation_eur, reference)
        if coefficient:
            parts.append(coefficient * used)

    expr = -sum(parts) if parts else 0
    return expr, reference


def assignment_cost_eur(cost_per_hour_eur: float, engaged_minutes: float) -> float:
    """Costo di una singola assegnazione, per riempire il piano.

    Sta qui e non in `solver.py` perche' deve restare coerente con il termine
    di costo dell'obiettivo: se un giorno si cambia il modo di contare i
    soldi, si cambia in un posto solo.
    """
    return float(cost_per_hour_eur) * float(engaged_minutes) / 60.0


def activation_cost_eur(cost_per_hour_eur: float) -> float:
    """Costo fisso di messa in moto di un mezzo, coerente con l'obiettivo."""
    return float(cost_per_hour_eur) * ACTIVATION_HOURS


def install_objective(mv: ModelVars) -> ObjectiveTerms:
    """Costruisce i termini e li installa come obiettivo da MASSIMIZZARE.

    Si massimizza, e non si minimizza, perche' i due termini di merito
    (copertura e protezione dell'interfaccia urbano-foresta) sono i piu'
    importanti e vanno letti in positivo: raddoppiare la copertura deve
    raddoppiare il valore dell'obiettivo. Costi, ritardi, acqua non erogata e
    penalita' dei vincoli morbidi entrano quindi gia' con il segno meno, come
    si vede in `build_terms`.

    Va sapendo che il valore risultante puo' essere NEGATIVO, e non e' un
    errore: quando la copertura piena e' irraggiungibile per ogni focolaio
    (fabbisogni realistici, flotta piccola, orizzonte di quattro ore) i due
    termini di merito valgono zero per costruzione e restano solo i termini
    negativi. Il piano e' comunque il migliore possibile, e a guidarlo e' il
    termine idrico, che da' credito parziale litro per litro.

    Va chiamata per ULTIMA, dopo `apply_constraints`: i pesi e i moltiplicatori
    di priorita' che legge possono essere stati riscritti da un
    OBJECTIVE_WEIGHT o da un PRIORITIZE_ZONE.
    """
    terms = build_terms(mv)
    mv.model.Maximize(terms.total())
    return terms


def describe_weights(mv: ModelVars) -> list[str]:
    """Righe leggibili con i pesi effettivamente usati, per le note del piano.

    Finiscono nel piano perche' il piano dipende da loro: confrontare due
    piani prodotti con pesi diversi non significa niente, e senza queste righe
    chi legge non ha modo di accorgersene. E' anche l'unico posto in cui si
    vede l'effetto di un OBJECTIVE_WEIGHT del coordinatore.

    Ordinate per nome, come tutti gli elenchi che entrano nel piano: due
    esecuzioni devono produrre note confrontabili riga per riga.
    """
    return [f"peso {name} = {value:g}" for name, value in sorted(mv.weights.items())]
