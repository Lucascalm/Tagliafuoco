"""Ablazioni: togliere un pezzo per volta e misurare cosa se ne va con lui.

PERCHE' QUESTO MODULO ESISTE
----------------------------
Un sistema composto da piu' pezzi produce un numero finale, e quel numero non
dice a chi vada il merito. Tagliafuoco mette in fila un traduttore, un
risolutore a vincoli e un validatore indipendente: se il piano finale e'
migliore della regola banale, la domanda seria e' quale dei tre pezzi stia
producendo il guadagno, e se qualcuno non ne produca affatto.

L'ablazione risponde a questa domanda nel modo piu' diretto: si toglie un pezzo,
si tiene tutto il resto identico, e si guarda cosa cambia. E' la stessa idea del
gruppo di controllo. Il valore sta nel "tutto il resto identico": stessi scenari,
stesso seme, stesso limite di tempo. Un'ablazione in cui cambia anche qualcos'
altro non isola niente.

Ogni funzione qui dentro restituisce un `AblationResult` che porta con se' la
domanda a cui risponde (`question`) e il modo in cui la risponde (`method`). Un
esperimento che non sa dichiarare cosa isola non e' un esperimento: e' una
tabella.

LE TRE ABLAZIONI DI SPEC.md SEZIONE 7
--------------------------------------
1. Risolutore con e senza i vincoli tradotti. Isola il contributo del canale
   linguistico: quanto cambia il piano per il fatto che il coordinatore ha
   parlato. Si misura in due modi, e servono entrambi, perche' rispondono a due
   domande diverse: quanto cambiano le metriche operative, e quante volte il
   piano rispetta l'ordine ricevuto. Il secondo si ottiene facendo validare
   ENTRAMBI i piani contro lo stesso `ConstraintSet`: il piano prodotto
   ignorando l'ordine viene giudicato come se l'ordine ci fosse, ed e' la misura
   pulita di cosa il traduttore ha davvero aggiunto.

2. Pipeline con e senza validatore. Il validatore non cambia il piano: lo
   accetta o lo respinge. Toglierlo non produce piani diversi, produce piani
   diversi ACCETTATI. La misura e' quindi quanti piani invalidi passerebbero, e
   quali regole li avrebbero fermati.

3. Baseline per tempo contro baseline per priorita'. Le due regole banali
   differiscono soltanto per l'ordine in cui servono i focolai. Il confronto
   isola quanto del vantaggio di un pianificatore venga dal solo ordinamento
   sensato, che e' la parte che chiunque puo' scrivere in un pomeriggio, e
   quanto invece richieda l'ottimizzazione vera.

DETERMINISMO
------------
Le ablazioni chiamano il solver in modalita' deterministica (un lavoratore,
seme fissato): due bracci che differiscono per il numero di lavoratori
differirebbero anche per caso, e il confronto misurerebbe la concorrenza.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from tagliafuoco.domain.models import Plan, Scenario
from tagliafuoco.eval.comparison import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_PERMUTATIONS,
    DEFAULT_SEED,
    ComparisonResult,
    ScenarioOutcome,
    compare_planners,
)
from tagliafuoco.eval.metrics import DEFAULT_METRIC_KEYS
from tagliafuoco.nlu.schema import ConstraintSet

__all__ = [
    "AblationResult",
    "ablation_baseline_ordering",
    "ablation_translated_constraints",
    "ablation_validator",
    "run_all_ablations",
]


#: Limite di tempo del solver dentro le ablazioni. Piu' stretto del default di
#: produzione perche' un'ablazione esegue il solver due volte per scenario, e
#: perche' cio' che si confronta sono due bracci con lo STESSO budget: il valore
#: assoluto conta meno del fatto che sia identico nei due.
ABLATION_SOLVER_SECONDS: float = 5.0

#: Ordine di riferimento per l'ablazione sul canale linguistico. E' un ordine
#: tipico di sala operativa, scelto perche' tocca tre vincoli diversi (priorita'
#: di zona, riserva di un mezzo, tetto al tempo di risposta) e perche' compare
#: nel corpus del benchmark: cosi' l'ablazione usa un ordine di cui si conosce
#: gia' la qualita' della traduzione.
DEFAULT_ORDER_TEXT: str = (
    "Prima le zone vicino alle case, l'elicottero grande resta di riserva e "
    "nessuno sopra i venticinque minuti."
)


@dataclass
class AblationResult:
    """Un esperimento di ablazione, con dichiarato cosa isola e cosa no."""

    name: str
    question: str
    method: str
    comparison: Optional[ComparisonResult] = None
    findings: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "question": self.question,
            "method": self.method,
            "findings": list(self.findings),
            "limits": list(self.limits),
            "extra": self.extra,
            "comparison": self.comparison.to_dict() if self.comparison else None,
        }


def _not_run(
    name: str, question: str, reason: str, *, extra: Optional[dict[str, Any]] = None
) -> AblationResult:
    """Un'ablazione dichiarata NON eseguita, con il motivo al posto dei numeri.

    Esiste perche' il modo peggiore di gestire un'ablazione impossibile e'
    restituire un risultato vuoto che assomiglia a un pareggio: "nessuna
    differenza misurata" e "differenza misurata pari a zero" si stampano quasi
    uguali e significano l'opposto. Qui il risultato mancante viene detto, con
    la ragione, e il campo `comparison` resta a None perche' non c'e' nessun
    confronto da mostrare.
    """
    return AblationResult(
        name=name,
        question=question,
        method=f"Non eseguita: {reason}.",
        findings=[f"Ablazione non eseguita: {reason}."],
        limits=[
            "Il risultato mancante non e' un risultato nullo: su questo insieme "
            "non si sa che cosa aggiunga il pezzo ablato, perche' la misura non "
            "e' stata fatta."
        ],
        extra=dict(extra or {}),
    )


def _solver_planner(
    max_seconds: float, constraint_set: Optional[ConstraintSet]
) -> Callable[..., Plan]:
    """Un pianificatore CP-SAT con vincoli e budget fissati in chiusura.

    Il solver si importa qui dentro e non in testa al modulo: importare ortools
    costa, e le ablazioni che non lo usano (per esempio quella sulle due
    baseline) non devono pagarlo.
    """
    from tagliafuoco.optimize.solver import solve

    def planner(scenario: Scenario, _constraints: Optional[ConstraintSet] = None) -> Plan:
        # I vincoli sono quelli del braccio, non quelli passati dal motore: e'
        # tutto il punto dell'ablazione, e un braccio che li prendesse da fuori
        # non sarebbe piu' distinguibile dall'altro.
        return solve(
            scenario,
            constraint_set,
            max_seconds=max_seconds,
            deterministic=True,
        )

    return planner


# --------------------------------------------------------------------------
# 1. Vincoli tradotti: con e senza
# --------------------------------------------------------------------------


def ablation_translated_constraints(
    scenarios: Sequence[Scenario],
    *,
    order_text: str = DEFAULT_ORDER_TEXT,
    constraint_set: Optional[ConstraintSet] = None,
    translate_fn: Optional[Callable[..., ConstraintSet]] = None,
    max_seconds: float = ABLATION_SOLVER_SECONDS,
    seed: int = DEFAULT_SEED,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    permutations: int = DEFAULT_PERMUTATIONS,
) -> AblationResult:
    """Solver con i vincoli tradotti contro solver senza, sugli stessi scenari.

    Cosa isola: il contributo del canale linguistico. I due bracci condividono
    scenari, seme, budget e modello matematico; l'unica differenza e' che uno
    riceve il `ConstraintSet` tradotto dall'ordine e l'altro riceve `None`.

    Il numero che conta di piu' non e' fra le metriche operative: e' quante
    volte il piano rispetta l'ordine ricevuto. Entrambi i bracci vengono
    validati CONTRO LO STESSO insieme di vincoli, quindi il braccio "senza
    ordine" viene giudicato come se l'ordine ci fosse. Se il canale linguistico
    serve a qualcosa, e' li' che si vede: il braccio senza ordine viola cio' che
    non gli e' stato detto.

    Sulle metriche operative il verso del risultato non e' scontato e non deve
    esserlo: un ordine e' un vincolo, e vincolare un'ottimizzazione di norma
    peggiora l'obiettivo. Una copertura piu' bassa con l'ordine attivo non e' un
    difetto del traduttore, e' il costo di aver obbedito. Va letto cosi' e non
    va nascosto.
    """
    if not scenarios:
        return _not_run(
            "vincoli_tradotti",
            "Che cosa aggiunge il canale linguistico? Il piano cambia perche' "
            "il coordinatore ha parlato, e cambia nella direzione che ha "
            "chiesto?",
            "nessuno scenario su cui eseguire i due bracci",
            extra={"order_text": order_text},
        )

    if translate_fn is None and constraint_set is None:
        from tagliafuoco.nlu import rule_translator

        translate_fn = rule_translator.translate

    cs = constraint_set
    if cs is None:
        assert translate_fn is not None
        try:
            cs = translate_fn(order_text, scenarios[0] if scenarios else None)
        except Exception as exc:  # noqa: BLE001 - il guasto si dichiara, non si alza
            # Un traduttore rotto non deve portarsi via una rigiocata gia'
            # conclusa: l'ablazione che dipende da lui si dichiara non eseguita,
            # con il motivo, e le altre proseguono. E' la stessa regola di
            # `nl_benchmark.run_benchmark`, dove un guasto su un caso e' un dato
            # e non una ragione per perdere gli altri quarantanove.
            return AblationResult(
                name="vincoli_tradotti",
                question=(
                    "Che cosa aggiunge il canale linguistico? Il piano cambia "
                    "perche' il coordinatore ha parlato, e cambia nella "
                    "direzione che ha chiesto?"
                ),
                method=(
                    "Non eseguita: senza traduzione dell'ordine i due bracci "
                    "sarebbero identici, e un'ablazione fra due bracci uguali "
                    "non isola niente."
                ),
                findings=[
                    "Ablazione non eseguita: il traduttore ha sollevato "
                    f"{type(exc).__name__}: {exc}"
                ],
                limits=[
                    "Il risultato mancante non e' un risultato nullo: su questi "
                    "scenari non si sa che cosa aggiunga il canale linguistico, "
                    "perche' la misura non e' stata fatta."
                ],
                extra={
                    "order_text": order_text,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    comparison = compare_planners(
        scenarios,
        [
            ("con_ordine", _solver_planner(max_seconds, cs)),
            ("senza_ordine", _solver_planner(max_seconds, None)),
        ],
        constraint_set=cs,
        metric_keys=DEFAULT_METRIC_KEYS,
        reference="senza_ordine",
        seed=seed,
        bootstrap_samples=bootstrap_samples,
        permutations=permutations,
        label="ablazione: vincoli tradotti",
    )

    hard_rule = "hard_constraint"
    obedience: dict[str, dict[str, Any]] = {}
    for arm, rows in comparison.outcomes.items():
        offending = [o for o in rows if hard_rule in o.violated_rules]
        obedience[arm] = {
            "scenari": len(rows),
            "piani_che_violano_l_ordine": len(offending),
            "scenari_coinvolti": [o.scenario_id for o in offending],
            "violazioni_bloccanti_totali": sum(o.blocking_violations for o in rows),
        }

    findings = [
        f"Ordine tradotto: {len(cs.constraints)} vincoli "
        f"({len(cs.hard)} duri, {len(cs.soft)} morbidi), traduttore "
        f"'{cs.translator}'.",
        "Piani che violano almeno un vincolo duro dell'ordine: "
        f"con ordine {obedience.get('con_ordine', {}).get('piani_che_violano_l_ordine', 0)}, "
        f"senza ordine {obedience.get('senza_ordine', {}).get('piani_che_violano_l_ordine', 0)} "
        f"su {len(scenarios)} scenari.",
    ]
    findings.extend(d.verdict() for d in comparison.differences)

    return AblationResult(
        name="vincoli_tradotti",
        question=(
            "Che cosa aggiunge il canale linguistico? Il piano cambia perche' "
            "il coordinatore ha parlato, e cambia nella direzione che ha chiesto?"
        ),
        method=(
            "Stessi scenari, stesso seme, stesso budget di tempo. Un braccio "
            "riceve il ConstraintSet tradotto dall'ordine, l'altro riceve None. "
            "Entrambi i piani vengono validati contro lo stesso ConstraintSet, "
            "cosi' l'obbedienza all'ordine si misura anche su chi l'ordine non "
            "lo ha ricevuto."
        ),
        comparison=comparison,
        findings=findings,
        limits=[
            "Un ordine solo: il risultato dipende da quale ordine si sceglie. "
            "Con un ordine che non morde (per esempio una priorita' di zona in "
            "una finestra dove tutti i focolai sono gia' serviti) l'ablazione "
            "mostra correttamente che non cambia niente.",
            "Vincolare un'ottimizzazione di norma peggiora l'obiettivo: una "
            "metrica operativa piu' bassa con l'ordine attivo e' il prezzo "
            "dell'obbedienza, non un difetto della traduzione.",
        ],
        extra={
            "order_text": order_text,
            "constraint_set_id": cs.constraint_set_id,
            "constraint_kinds": [c.kind.value for c in cs.constraints],
            "obedience": obedience,
        },
    )


# --------------------------------------------------------------------------
# 2. Validatore: con e senza
# --------------------------------------------------------------------------


def ablation_validator(
    scenarios: Sequence[Scenario],
    planners: Mapping[str, Callable[..., Plan]]
    | Sequence[tuple[str, Callable[..., Plan]]],
    *,
    constraint_set: Optional[ConstraintSet] = None,
) -> AblationResult:
    """Quanti piani invalidi passerebbero se il validatore non ci fosse.

    Cosa isola: il valore del secondo programma. Il validatore non modifica
    nessun piano, quindi non si puo' misurare il suo effetto guardando le
    metriche: senza di lui i piani sarebbero identici, cambierebbe solo che
    verrebbero consegnati. La misura giusta e' allora il conteggio dei piani che
    verrebbero consegnati pur contenendo una violazione bloccante, con il
    dettaglio di quali regole li avrebbero fermati.

    Questo e' anche l'unico esperimento del progetto che puo' dare un risultato
    scomodo al progetto stesso: se il solver producesse solo piani validi, il
    validatore non avrebbe fermato niente su questo campione, e andrebbe scritto
    cosi'. Resterebbe comunque utile come rete di sicurezza contro le
    regressioni, ma su questi scenari non avrebbe prodotto nessun guadagno
    misurabile, e sostenere il contrario sarebbe barare.
    """
    if not scenarios:
        return _not_run(
            "validatore",
            "Che cosa aggiunge il validatore indipendente? Quanti piani non "
            "eseguibili verrebbero consegnati se non ci fosse?",
            "nessuno scenario su cui produrre e validare piani",
        )

    from tagliafuoco.validate.validator import validate_plan

    items = (
        list(planners.items())
        if hasattr(planners, "items")
        else list(planners)  # type: ignore[arg-type]
    )
    rows: dict[str, dict[str, Any]] = {}
    findings: list[str] = []

    for name, planner in items:
        invalid = 0
        rule_counts: dict[str, int] = {}
        offending: list[str] = []
        assignments_blocked = 0
        total_assignments = 0
        for scenario in scenarios:
            from tagliafuoco.sim.replay import call_planner

            plan = call_planner(planner, scenario, constraint_set)
            total_assignments += len(plan.assignments)
            result = validate_plan(plan, scenario, constraint_set)
            if result.is_valid:
                continue
            invalid += 1
            offending.append(scenario.scenario_id)
            for v in result.blocking:
                rule_counts[v.rule] = rule_counts.get(v.rule, 0) + 1
                assignments_blocked += 1
        rows[name] = {
            "scenari": len(scenarios),
            "piani_invalidi": invalid,
            "quota_invalidi": round(invalid / len(scenarios), 4) if scenarios else 0.0,
            "violazioni_bloccanti": assignments_blocked,
            "assegnazioni_totali": total_assignments,
            "regole_scattate": dict(sorted(rule_counts.items())),
            "scenari_coinvolti": offending,
        }
        if invalid:
            regole = ", ".join(sorted(rule_counts)) or "nessuna regola registrata"
            findings.append(
                f"{name}: senza validatore sarebbero passati {invalid} piani su "
                f"{len(scenarios)} con almeno una violazione bloccante "
                f"(regole: {regole})."
            )
        else:
            findings.append(
                f"{name}: su questi {len(scenarios)} scenari il validatore non "
                "ha fermato nessun piano. Non dimostra che sia inutile, "
                "dimostra che su questo campione non ha trovato niente."
            )

    return AblationResult(
        name="validatore",
        question=(
            "Che cosa aggiunge il validatore indipendente? Quanti piani non "
            "eseguibili verrebbero consegnati se non ci fosse?"
        ),
        method=(
            "Si producono i piani una volta sola e si contano quelli che il "
            "validatore respinge. Il braccio 'senza validatore' non e' una "
            "seconda esecuzione: e' lo stesso piano senza il controllo, perche' "
            "il validatore non modifica niente."
        ),
        findings=findings,
        limits=[
            "Il validatore ricalcola la fisica con formule proprie: un piano "
            "respinto significa che i due calcoli non concordano, e la colpa "
            "puo' stare da entrambe le parti. Il conteggio dice quante volte "
            "hanno litigato, non chi aveva ragione.",
            "Le violazioni non bloccanti (avvisi) non entrano in questo "
            "conteggio: un piano con soli avvisi viene consegnato anche con il "
            "validatore attivo.",
        ],
        extra={"per_pianificatore": rows},
    )


# --------------------------------------------------------------------------
# 3. Ordinamento della baseline: per tempo contro per priorita'
# --------------------------------------------------------------------------


def ablation_baseline_ordering(
    scenarios: Sequence[Scenario],
    *,
    constraint_set: Optional[ConstraintSet] = None,
    seed: int = DEFAULT_SEED,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    permutations: int = DEFAULT_PERMUTATIONS,
) -> AblationResult:
    """Baseline per tempo di rilevamento contro baseline per priorita'.

    Cosa isola: il valore del solo ordinamento. Le due baseline condividono
    tutto (stessa regola golosa, stesso calcolo dei tempi, stesso limite di
    mezzi per focolaio) e differiscono soltanto per l'ordine in cui i focolai
    vengono serviti: cronologico nella prima, per priorita' nella seconda.

    Serve a rendere onesto il confronto principale. Se l'ottimizzatore battesse
    la baseline cronologica ma non quella per priorita', allora il guadagno
    verrebbe dall'aver ordinato i focolai in modo sensato e non
    dall'ottimizzazione, e il progetto dovrebbe dirlo.
    """
    if not scenarios:
        return _not_run(
            "ordinamento_baseline",
            "Quanto del vantaggio si ottiene con il solo ordinamento dei "
            "focolai, senza nessuna ottimizzazione?",
            "nessuno scenario su cui eseguire i due bracci",
        )

    from tagliafuoco.baseline.nearest import plan_nearest, plan_priority

    comparison = compare_planners(
        scenarios,
        [("priorita", plan_priority), ("tempo", plan_nearest)],
        constraint_set=constraint_set,
        reference="tempo",
        seed=seed,
        bootstrap_samples=bootstrap_samples,
        permutations=permutations,
        label="ablazione: ordine di servizio della baseline",
    )
    return AblationResult(
        name="ordinamento_baseline",
        question=(
            "Quanto del vantaggio si ottiene con il solo ordinamento dei "
            "focolai, senza nessuna ottimizzazione?"
        ),
        method=(
            "Stessa regola golosa in entrambi i bracci, stessi scenari. "
            "L'unica differenza e' la chiave di ordinamento dei focolai: "
            "istante di rilevamento contro priorita' di base."
        ),
        comparison=comparison,
        findings=[d.verdict() for d in comparison.differences],
        limits=[
            "La priorita' di base e' una formula tarata a mano (SPEC.md sezione "
            "6): questa ablazione misura quella formula, non l'idea generale di "
            "ordinare per gravita'.",
        ],
    )


def run_all_ablations(
    scenarios: Sequence[Scenario],
    *,
    order_text: str = DEFAULT_ORDER_TEXT,
    max_seconds: float = ABLATION_SOLVER_SECONDS,
    seed: int = DEFAULT_SEED,
    include_solver: bool = True,
) -> list[AblationResult]:
    """Esegue le tre ablazioni di SPEC.md sezione 7 sullo stesso insieme di scenari.

    `include_solver=False` salta le ablazioni che richiedono CP-SAT: serve a
    ottenere un risultato rapido durante lo sviluppo e nei test, dove il solver
    costerebbe alcuni secondi per scenario senza aggiungere niente a cio' che si
    sta verificando.
    """
    from tagliafuoco.baseline.nearest import plan_nearest, plan_priority

    results: list[AblationResult] = []
    planners: list[tuple[str, Callable[..., Plan]]] = [
        ("tempo", plan_nearest),
        ("priorita", plan_priority),
    ]

    if include_solver:
        results.append(
            ablation_translated_constraints(
                scenarios, order_text=order_text, max_seconds=max_seconds, seed=seed
            )
        )
        from tagliafuoco.optimize.solver import solve

        def cpsat(scenario: Scenario, constraints: Optional[ConstraintSet] = None) -> Plan:
            return solve(
                scenario, constraints, max_seconds=max_seconds, deterministic=True
            )

        planners.insert(0, ("cpsat", cpsat))

    results.append(ablation_validator(scenarios, planners))
    results.append(ablation_baseline_ordering(scenarios, seed=seed))
    return results
