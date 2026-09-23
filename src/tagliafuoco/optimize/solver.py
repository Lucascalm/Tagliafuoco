"""Esecuzione del solver e traduzione della soluzione in un `Plan`.

Perche' questo modulo e' separato da `model.py`
-----------------------------------------------
Costruire il modello e risolverlo sono due mestieri diversi. Il costruttore
deve preoccuparsi di scrivere vincoli corretti; il risolutore deve
preoccuparsi di cosa fare quando la risposta non arriva, o arriva e dice
"impossibile". Tenerli separati permette di provare il modello senza
eseguirlo (i test possono ispezionare le variabili) e di cambiare politica di
rilassamento senza toccare i vincoli.

Gli stati di CP-SAT, uno per uno
---------------------------------
- OPTIMAL: il solver ha trovato una soluzione e ha dimostrato che non ne
  esiste una migliore. Il piano si scrive e si dichiara ottimo.
- FEASIBLE: ha trovato una soluzione ma il tempo e' scaduto prima di provare
  che fosse la migliore. Il piano si scrive lo stesso, annotando che e' il
  migliore trovato entro il limite. Per un supporto alle decisioni va benone.
- INFEASIBLE: ha dimostrato che nessuna soluzione soddisfa tutti i vincoli.
  Qui sta la parte interessante di questo modulo, vedi sotto.
- MODEL_INVALID: il modello e' malformato (domini incoerenti, overflow di
  interi). E' un errore di programmazione, non un esito: si annota il
  messaggio di validazione e si restituisce un piano vuoto che lo dice.
- UNKNOWN: tempo scaduto senza nessuna soluzione trovata e senza prova di
  infattibilita'. Si restituisce un piano vuoto che invita ad alzare il
  limite di tempo.

Il rilassamento su INFEASIBLE
------------------------------
Un piano infattibile per colpa di un ordine contraddittorio deve dirlo, non
restituire il vuoto. Se il coordinatore chiede "tutti i Canadair fermi" e
insieme "almeno due Canadair sull'incendio di Cirella", il modello e'
insoddisfacibile e la risposta utile non e' una pagina bianca: e' "il tuo
ordine si contraddice, ecco quale parte ho dovuto lasciar cadere".

La seconda passata ricostruisce il modello da zero trasformando i vincoli
DURI provenienti dal traduttore in penalita' molto care (vedi
`objective.RELAXED_HARD_WEIGHT`). I vincoli STRUTTURALI, cioe' la fisica
(autonomia, turno, non sovrapposizione, volo notturno), non si rilassano mai:
se l'infattibilita' viene da li', il problema e' che i mezzi non bastano, e
raccontare il contrario sarebbe una bugia comoda.

Determinismo
------------
La valutazione confronta Tagliafuoco con la baseline sugli stessi scenari:
qualunque non determinismo renderebbe il confronto privo di significato. In
modalita' `deterministic` il solver gira con un solo lavoratore
(`SOLVER_NUM_WORKERS_DETERMINISTIC`), seme fissato e limite di tempo
deterministico oltre a quello a orologio. Con piu' lavoratori la ricerca e'
concorrente e l'esito dipende da chi arriva primo, quindi seme fissato o no
il piano puo' cambiare da un'esecuzione all'altra.
"""

from __future__ import annotations

import time
from typing import Optional

from ortools.sat.python import cp_model

from tagliafuoco.config import (
    SOLVER_MAX_SECONDS,
    SOLVER_NUM_WORKERS,
    SOLVER_NUM_WORKERS_DETERMINISTIC,
    SOLVER_RANDOM_SEED,
)
from tagliafuoco.domain.models import Assignment, Plan, Scenario
from tagliafuoco.nlu.schema import ConstraintKind, ConstraintSet, Enforcement
from tagliafuoco.optimize.geometry import from_units
from tagliafuoco.optimize.model import (
    DEFAULT_MAX_FIRES_PER_VEHICLE,
    WEIGHT_ONLY_KINDS,
    ConstraintApplication,
    ModelVars,
    apply_constraints,
    build_model,
)
from tagliafuoco.optimize.objective import (
    OBJECTIVE_PRECISION,
    ObjectiveTerms,
    activation_cost_eur,
    assignment_cost_eur,
    describe_weights,
    install_objective,
)

PRODUCER = "cpsat"

# Stati in cui esiste una soluzione da leggere.
_SOLVED = (cp_model.OPTIMAL, cp_model.FEASIBLE)


def solve(
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    max_seconds: Optional[float] = None,
    deterministic: bool = False,
    *,
    max_fires_per_vehicle: int = DEFAULT_MAX_FIRES_PER_VEHICLE,
) -> Plan:
    """Produce un piano di impiego per lo scenario dato.

    `constraint_set` e' il risultato della traduzione dell'ordine del
    coordinatore, e puo' mancare: senza ordine si ottimizza con i soli pesi di
    default, che e' il caso di riferimento dell'ablazione.

    `max_seconds` e' un limite, non un obiettivo: il solver si ferma prima se
    dimostra l'ottimalita'. Se scade, si prende la migliore soluzione trovata
    e lo stato diventa FEASIBLE invece di OPTIMAL. Per un supporto alle
    decisioni e' il compromesso giusto, ma va letto nelle note del piano,
    perche' un piano FEASIBLE non e' necessariamente il migliore che esista.

    `deterministic` serve alla valutazione, non alla produzione: fa girare un
    solo lavoratore invece di otto, quindi e' piu' lento, in cambio due
    esecuzioni sullo stesso scenario danno lo stesso piano. Con piu'
    lavoratori la ricerca e' concorrente e l'esito dipende da chi arriva
    primo, quindi seme fissato o no il piano puo' cambiare. Vedi le avvertenze
    in `_make_solver` su cosa il seme garantisce e cosa no.

    `max_fires_per_vehicle` e' una taratura del modello, non una regola
    operativa: alzarlo allarga il modello e lo rallenta, abbassarlo puo'
    togliere piani validi. Vedi `model.DEFAULT_MAX_FIRES_PER_VEHICLE`.

    Restituisce sempre un `Plan`, anche quando non c'e' niente da assegnare:
    il piano vuoto con le note che spiegano perche' e' un risultato, il
    sollevare un'eccezione no. Il chiamante e' spesso un ciclo che scorre
    un'intera stagione, e una finestra senza focolai o senza mezzi disponibili
    non deve interromperlo.
    """
    budget = float(SOLVER_MAX_SECONDS if max_seconds is None else max_seconds)
    started = time.perf_counter()

    mv, terms, report = _compose(
        scenario, constraint_set, max_fires_per_vehicle, relax_hard=False
    )
    solver = _make_solver(budget, deterministic)
    status = solver.Solve(mv.model)

    relaxed_ids: list[str] = []
    extra_notes: list[str] = []

    if status == cp_model.INFEASIBLE and _relaxable_ids(constraint_set, report):
        # Seconda passata: gli ordini diventano preferenze carissime.
        extra_notes.append(
            "Prima passata infattibile: i vincoli duri dell'ordine sono stati "
            "trasformati in penalita' e il modello e' stato risolto di nuovo."
        )
        # Il pavimento di un secondo e' deliberato e puo' sforare di poco il
        # budget richiesto: una seconda passata con zero tempo restituirebbe
        # UNKNOWN, cioe' proprio la pagina bianca che il rilassamento esiste
        # per evitare. Meglio un secondo in piu' che una risposta inutile.
        remaining = max(1.0, budget - (time.perf_counter() - started))
        mv, terms, report = _compose(
            scenario, constraint_set, max_fires_per_vehicle, relax_hard=True
        )
        solver = _make_solver(remaining, deterministic)
        status = solver.Solve(mv.model)
        if status in _SOLVED:
            # `relaxed_ids` si valorizza solo se la seconda passata ha davvero
            # prodotto un piano. Altrimenti non c'e' nessun piano che violi
            # quegli ordini, e dire "il piano li viola consapevolmente"
            # sarebbe una nota falsa su un risultato vuoto.
            relaxed_ids = list(report.relaxed_ids)
            extra_notes.append(
                ("Vincoli rilassati: " + ", ".join(relaxed_ids))
                if relaxed_ids
                else "Nessun vincolo duro da rilassare."
            )
        else:
            extra_notes.append(
                "Anche con gli ordini rilassati non si ottiene un piano: "
                "l'infattibilita' non veniva dall'ordine del coordinatore."
            )

    elapsed = time.perf_counter() - started
    return _build_plan(
        mv=mv,
        terms=terms,
        report_notes=report.notes,
        relaxed_ids=relaxed_ids,
        extra_notes=extra_notes,
        solver=solver,
        status=status,
        elapsed=elapsed,
        constraint_set=constraint_set,
        deterministic=deterministic,
    )


# --------------------------------------------------------------------------
# Montaggio e configurazione
# --------------------------------------------------------------------------


def _compose(
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet],
    max_fires_per_vehicle: int,
    *,
    relax_hard: bool,
):
    """Modello + vincoli tradotti + obiettivo, nell'ordine giusto.

    L'ordine conta: `apply_constraints` puo' cambiare i pesi e i moltiplicatori
    di priorita', quindi l'obiettivo si installa per ultimo, quando i pesi
    definitivi sono noti.
    """
    mv = build_model(scenario, max_fires_per_vehicle=max_fires_per_vehicle)
    report = apply_constraints(
        mv.model, mv, scenario, constraint_set, relax_hard=relax_hard
    )
    terms = install_objective(mv)
    return mv, terms, report


def _make_solver(max_seconds: float, deterministic: bool) -> cp_model.CpSolver:
    """Configura il risolutore. Tutti i parametri vengono da `config.py`."""
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, float(max_seconds))
    solver.parameters.random_seed = SOLVER_RANDOM_SEED
    solver.parameters.log_search_progress = False
    if deterministic:
        solver.parameters.num_search_workers = SOLVER_NUM_WORKERS_DETERMINISTIC
        # Limite deterministico oltre a quello a orologio: il tempo di parete
        # dipende dal carico della macchina, quindi da solo non basta a
        # garantire che due esecuzioni si fermino nello stesso punto.
        #
        # Limite dichiarato, e va saputo. I due budget hanno lo stesso valore
        # numerico ma NON la stessa unita': `max_time_in_seconds` sono secondi
        # di orologio, `max_deterministic_time` e' una misura astratta di
        # lavoro svolto. Se il solver chiude la ricerca prima di consumare uno
        # dei due, il risultato e' identico su qualunque macchina e il
        # determinismo e' pieno. Se invece la ricerca viene troncata, a
        # troncarla puo' essere l'uno o l'altro a seconda di quanto e' veloce
        # la macchina, e il piano FEASIBLE che ne esce puo' cambiare da
        # macchina a macchina.
        #
        # Concretamente: il determinismo e' garantito quando il solver
        # risponde OPTIMAL, ed e' solo probabile quando risponde FEASIBLE. Per
        # le valutazioni riproducibili conviene quindi alzare il limite finche'
        # gli scenari non chiudono in OPTIMAL, oppure archiviare accanto ai
        # risultati il limite usato. Alzare qui il solo tempo di orologio
        # renderebbe deterministico il troncamento, ma trasformerebbe
        # `max_seconds` in un valore indicativo, e un supporto alle decisioni
        # che non rispetta il proprio limite di tempo e' peggio di uno che
        # tronca.
        solver.parameters.max_deterministic_time = max(0.01, float(max_seconds))
    else:
        solver.parameters.num_search_workers = SOLVER_NUM_WORKERS
    return solver


def _relaxable_ids(
    constraint_set: Optional[ConstraintSet], report: ConstraintApplication
) -> list[str]:
    """Ordini duri che sono stati DAVVERO tradotti in vincoli, e solo quelli.

    Non basta che il `ConstraintSet` contenga un vincolo duro: conta che
    `apply_constraints` lo abbia applicato. Un ordine il cui selettore non
    pesca nulla in questo scenario ("escludete gli anfibi" dove di anfibi non
    ce n'e') resta fuori dal modello, e rilassarlo non cambierebbe una virgola.

    Senza questo filtro si pagherebbe una seconda risoluzione completa per
    scoprire di non avere niente da rilassare, e soprattutto si scriverebbe
    nel piano che l'infattibilita' viene dall'ordine quando invece viene
    dalla fisica. E' la differenza fra "il tuo ordine si contraddice" e "i
    mezzi non bastano", che sono due risposte diverse per il coordinatore.

    Due famiglie non contano mai, e per due ragioni diverse.

    Gli `UNSUPPORTED` perche' per definizione non diventano vincoli, anche nel
    caso limite in cui il traduttore ne emetta uno marcato duro.

    I vincoli che agiscono solo sui pesi (`model.WEIGHT_ONLY_KINDS`) perche'
    non vietano e non impongono niente: qualunque piano li "rispetta", quindi
    non possono essere la causa di un'infattibilita' e rilassarli non
    cambierebbe una virgola. Senza questo filtro un ordine fatto di sole
    priorita' avrebbe fatto spendere una seconda risoluzione intera per
    scoprire di non avere niente da rilassare, e il piano avrebbe dichiarato
    di aver violato un ordine che non era violabile.
    """
    if constraint_set is None:
        return []
    applied = set(report.applied_ids)
    return [
        c.constraint_id
        for c in constraint_set.constraints
        if c.enforcement is Enforcement.HARD
        and c.kind is not ConstraintKind.UNSUPPORTED
        and c.kind not in WEIGHT_ONLY_KINDS
        and c.constraint_id in applied
    ]


# --------------------------------------------------------------------------
# Lettura della soluzione
# --------------------------------------------------------------------------


def _build_plan(
    *,
    mv: ModelVars,
    terms: ObjectiveTerms,
    report_notes: list[str],
    relaxed_ids: list[str],
    extra_notes: list[str],
    solver: cp_model.CpSolver,
    status: int,
    elapsed: float,
    constraint_set: Optional[ConstraintSet],
    deterministic: bool,
) -> Plan:
    """Trasforma lo stato del solver in un piano, qualunque sia lo stato."""
    scenario = mv.scenario
    plan = Plan(
        plan_id=f"{scenario.scenario_id}__{PRODUCER}",
        horizon_minutes=scenario.horizon_minutes,
        producer=PRODUCER,
        solver_status=solver.StatusName(status),
        solve_seconds=round(elapsed, 4),
        constraint_set_id=(
            constraint_set.constraint_set_id if constraint_set is not None else None
        ),
    )

    notes: list[str] = list(mv.notes)
    notes.extend(report_notes)
    notes.extend(extra_notes)

    if status in _SOLVED:
        plan.assignments = _extract_assignments(mv, solver)
        plan.objective_value = solver.ObjectiveValue() / OBJECTIVE_PRECISION
        if status == cp_model.OPTIMAL:
            notes.append("Soluzione ottima dimostrata entro il limite di tempo.")
        else:
            notes.append(
                "Tempo scaduto prima della prova di ottimalita': questo e' il "
                "miglior piano trovato, non necessariamente il migliore che esista."
            )
    elif status == cp_model.INFEASIBLE:
        notes.append(
            "Nessun piano soddisfa i vincoli. Se non c'erano ordini da "
            "rilassare, l'infattibilita' viene dalla fisica: i mezzi "
            "disponibili non bastano per l'orizzonte richiesto."
        )
    elif status == cp_model.MODEL_INVALID:
        notes.append(
            "Modello non valido, e' un errore di costruzione: "
            + (mv.model.Validate() or "nessun dettaglio disponibile")
        )
    else:  # UNKNOWN
        notes.append(
            "Nessuna soluzione trovata entro il limite di tempo e nessuna prova "
            "di infattibilita'. Conviene alzare il limite o ridurre lo scenario."
        )

    assigned = {a.fire_id for a in plan.assignments}
    plan.unassigned_fire_ids = [
        f.fire_id for f in scenario.fires if f.fire_id not in assigned
    ]

    if relaxed_ids:
        notes.append(
            "Ordini rilassati per rendere il piano fattibile: "
            + ", ".join(relaxed_ids)
            + ". Il piano li viola consapevolmente."
        )

    notes.append(
        "release_minute e' il rientro in base, cioe' il momento in cui il "
        "mezzo torna disponibile, non la fine dell'ultimo sgancio."
    )
    notes.extend(describe_weights(mv))
    notes.append(
        "modalita' deterministica" if deterministic else "modalita' concorrente"
    )
    plan.notes = notes
    return plan


def _extract_assignments(mv: ModelVars, solver: cp_model.CpSolver) -> list[Assignment]:
    """Legge le variabili risolte e scrive le assegnazioni.

    I tempi tornano da unita' intere a minuti qui e solo qui. Le assegnazioni
    sono ordinate per (partenza, mezzo, focolaio): un ordine stabile e'
    necessario perche' due esecuzioni con lo stesso seme producano piani
    confrontabili riga per riga.
    """
    out: list[Assignment] = []
    scenario = mv.scenario

    for (vehicle_id, fire_id), p in sorted(mv.pairs.items()):
        if not solver.Value(p.assign):
            continue
        vehicle = scenario.vehicle_by_id(vehicle_id)
        if vehicle is None:  # pragma: no cover - lo scenario e' coerente
            continue

        sorties = int(solver.Value(p.sorties))
        departure = from_units(int(solver.Value(p.departure)))
        arrival = from_units(int(solver.Value(p.arrival)))
        release = from_units(int(solver.Value(p.release)))

        out.append(
            Assignment(
                vehicle_id=vehicle_id,
                fire_id=fire_id,
                departure_minute=departure,
                arrival_minute=arrival,
                sorties=sorties,
                water_delivered_liters=sorties * p.profile.liters_per_sortie,
                release_minute=release,
                water_source_id=p.profile.water_source_id,
                estimated_cost_eur=round(
                    assignment_cost_eur(vehicle.cost_per_hour_eur, release - departure),
                    2,
                ),
                note=(
                    f"{sorties} cicli da {p.profile.liters_per_sortie} litri, "
                    f"ciclo di {p.profile.cycle_minutes:.1f} min"
                    + (
                        f" su {p.profile.water_source_id}"
                        if p.profile.water_source_id
                        else " senza rifornimento"
                    )
                ),
            )
        )

    # Il costo fisso di attivazione si paga una volta per mezzo: lo si somma
    # alla prima assegnazione di ciascuno, cosi' la somma dei costi delle
    # assegnazioni coincide con il costo che l'obiettivo ha minimizzato.
    seen: set[str] = set()
    for a in sorted(out, key=lambda x: (x.departure_minute, x.vehicle_id, x.fire_id)):
        if a.vehicle_id in seen:
            continue
        seen.add(a.vehicle_id)
        vehicle = scenario.vehicle_by_id(a.vehicle_id)
        if vehicle is not None:
            a.estimated_cost_eur = round(
                a.estimated_cost_eur + activation_cost_eur(vehicle.cost_per_hour_eur), 2
            )

    out.sort(key=lambda a: (a.departure_minute, a.vehicle_id, a.fire_id))
    return out
