"""Valutazione del traduttore sul corpus di ordini annotato a mano.

PERCHE' QUESTO MODULO ESISTE
----------------------------
Il traduttore e' l'unico punto del sistema in cui entra del linguaggio, ed e'
quindi l'unico punto che puo' sbagliare in modo plausibile. Un solver che
sbaglia produce un piano infattibile e il validatore lo respinge; un traduttore
che sbaglia produce un vincolo ben formato, tipizzato, accettato da tutti i
controlli a valle, e semplicemente falso. Per questo il traduttore non si
verifica: si misura, su un corpus scritto prima di guardare che cosa il
traduttore produce.

Il corpus e' `benchmarks/ordini_vocali.json` e non si tocca. La sua nota
metodologica dice perche': annotare dopo aver visto le risposte trasforma la
misura in una conferma, perche' si finisce per chiamare corretto cio' che il
modello ha prodotto. Questo modulo legge quel file, non ne aggiunge casi e non
ne corregge le attese.

CHE COSA SI MISURA, E PERCHE' NON BASTA UN NUMERO SOLO
-------------------------------------------------------
- Corrispondenza esatta dell'insieme: tutti e soli i vincoli attesi, per tipo.
  E' la metrica piu' severa e la piu' vicina all'uso reale, perche' il
  coordinatore riceve l'insieme intero e non un vincolo alla volta.
- Precisione, richiamo e F1 per tipo di vincolo, con la matrice di confusione.
  Servono a vedere DOVE si sbaglia: un traduttore che scambia sempre
  MAX_RESPONSE_TIME per PRIORITIZE_ZONE ha un problema diverso da uno che
  omette e basta.
- Tasso di allucinazione: vincoli emessi che non corrispondono a nessun atteso.
  E' la metrica che conta di piu' in un sistema di supporto alle decisioni: un
  vincolo inventato entra nel modello, cambia il piano e nessuno se ne accorge,
  perche' e' sintatticamente perfetto.
- Tasso di astensione corretta: quanto spesso UNSUPPORTED viene emesso quando
  era la risposta giusta. Lo schema dice che l'astensione non e' un ripiego:
  un traduttore che non sa astenersi e' peggiore di uno che copre meno casi.
  Si misura anche il verso opposto, l'astensione a sproposito, perche' un
  traduttore che si astiene sempre avrebbe astensione corretta perfetta.
- Accuratezza dei parametri quando il tipo e' giusto: riconoscere che l'ordine
  chiede un tetto al tempo di risposta e sbagliare i minuti non serve a niente.

COME SI TRATTANO I CASI CON PIU' RISPOSTE CORRETTE
---------------------------------------------------
Alcune note del corpus dichiarano che piu' di una risposta e' accettabile. Se
si ignorassero, la misura punirebbe risposte che l'autore dell'annotazione ha
esplicitamente definito corrette, e il numero finale sarebbe falso per
costruzione. Se si accettasse qualunque cosa, il benchmark non misurerebbe
niente.

La via scelta e' la terza: ogni tolleranza e' scritta esplicitamente nella
tabella `CASE_TOLERANCES`, citando la frase del corpus che la autorizza, e vale
solo per il caso indicato. Le forme ammesse sono tre, e tutte e tre compaiono
nel resoconto in modo separato dai successi pieni:

1. lettura alternativa: un intero insieme atteso diverso, ugualmente corretto
   (B033, B035, B041);
2. atteso facoltativo: un vincolo la cui omissione la nota definisce richiamo
   parziale invece che errore (B023);
3. emissione tollerata: un vincolo in piu' che la nota ammette e che quindi non
   conta allucinazione (B044).

Quando un caso ha piu' letture accettate si valuta contro tutte e si tiene la
migliore, dichiarando nel risultato quale ha vinto. Le note che NON concedono
tolleranza (per esempio B003, B013, B022, B042, B046, che spiegano al contrario
perche' una certa aggiunta sarebbe un errore) non compaiono in tabella: leggerle
come permessi sarebbe il modo piu' silenzioso di gonfiare il risultato.

REGOLE CONTRO MODELLO
---------------------
Il benchmark gira sul traduttore a regole senza nessuna chiave, che e' il caso
di riferimento, e sul traduttore con modello linguistico quando la chiave c'e'.
Il confronto fra i due e' il risultato interessante del progetto: se le regole
arrivano vicine, la conclusione onesta e' che il modello linguistico aggiunge
poco, e va scritta nel resoconto invece di essere lasciata dedurre. Senza chiave
`nlu/translator.translate` ricade sulle regole: in quel caso il confronto non si
esegue affatto, perche' confrontare un metodo con se' stesso e chiamarlo pareggio
sarebbe il piu' banale dei risultati inventati.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from tagliafuoco import config
from tagliafuoco.domain.models import Scenario
from tagliafuoco.eval.comparison import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_PERMUTATIONS,
    DEFAULT_SEED,
    PairedDifference,
    paired_difference,
    wilson_interval,
)
from tagliafuoco.nlu.schema import ConstraintKind, ConstraintSet

__all__ = [
    "BenchmarkCase",
    "BenchmarkResult",
    "CaseResult",
    "CASE_TOLERANCES",
    "CORPUS_PATH",
    "TranslatorComparison",
    "compare_translators",
    "evaluate_case",
    "load_corpus",
    "run_benchmark",
]


#: Percorso del corpus. Il file e' di proprieta' del sottosistema eval ma e'
#: dato di ingresso, non uscita: si legge e non si riscrive.
CORPUS_PATH: Path = config.BENCHMARKS_DIR / "ordini_vocali.json"

#: Etichetta della riga e della colonna "niente" nella matrice di confusione.
#: Una matrice di confusione senza questa voce non distingue una sostituzione
#: (ho emesso il tipo sbagliato) da una omissione piu' una allucinazione.
NOTHING: str = "(nessuno)"

#: Tolleranza sui confronti numerici fra parametro atteso e parametro emesso.
_NUM_EPS: float = 1e-9


# --------------------------------------------------------------------------
# Tolleranze dichiarate, una per caso, ciascuna con la sua citazione
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseTolerance:
    """Le risposte che il corpus dichiara accettabili oltre all'annotazione.

    `nota` riporta la frase del corpus che autorizza la tolleranza. Non e'
    ornamento: e' la prova che la tolleranza viene dall'annotatore e non da chi
    ha scritto il misuratore dopo aver visto i risultati.
    """

    nota: str
    optional_indexes: tuple[int, ...] = ()
    tolerated_extra_kinds: tuple[str, ...] = ()
    alternatives: tuple[tuple[Mapping[str, Any], ...], ...] = ()


CASE_TOLERANCES: dict[str, CaseTolerance] = {
    "B023": CaseTolerance(
        nota=(
            "Accettabile anche l'emissione del solo termine response_time, va "
            "contata come richiamo parziale."
        ),
        # Indice 1 dell'atteso: OBJECTIVE_WEIGHT sul termine cost.
        optional_indexes=(1,),
    ),
    "B033": CaseTolerance(
        nota=(
            "Accettabile anche EXCLUDE_VEHICLE sul dominio aereo. Si contano "
            "corrette entrambe le letture."
        ),
        alternatives=(({"kind": "EXCLUDE_VEHICLE"},),),
    ),
    "B035": CaseTolerance(
        nota=(
            "Se il traduttore non ha il contesto, l'astensione con UNSUPPORTED "
            "e' una risposta accettabile e va contata come corretta."
        ),
        alternatives=(({"kind": "UNSUPPORTED"},),),
    ),
    "B041": CaseTolerance(
        nota=(
            "Un ConstraintSet vuoto e' la risposta corretta; anche un singolo "
            "UNSUPPORTED e' accettabile."
        ),
        alternatives=(({"kind": "UNSUPPORTED"},),),
    ),
    "B044": CaseTolerance(
        nota=(
            "Accettabile anche l'astensione sulla parte quello sopra il paese "
            "se il contesto non basta."
        ),
        tolerated_extra_kinds=("UNSUPPORTED",),
    ),
}


# --------------------------------------------------------------------------
# Il corpus, letto e non riscritto
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedConstraint:
    """Un vincolo atteso: il tipo piu' i soli parametri che il corpus annota.

    La convenzione del corpus e' che i parametri non elencati sono liberi.
    Quindi qui `params` e' l'insieme MINIMO da verificare, non la descrizione
    completa del vincolo.
    """

    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    optional: bool = False


@dataclass(frozen=True)
class AcceptedReading:
    """Una delle letture corrette di un caso."""

    expected: tuple[ExpectedConstraint, ...]
    tolerated_extra_kinds: frozenset[str] = frozenset()
    origin: str = "annotazione del corpus"

    @property
    def required(self) -> tuple[ExpectedConstraint, ...]:
        return tuple(e for e in self.expected if not e.optional)


@dataclass(frozen=True)
class BenchmarkCase:
    """Un caso del corpus, con tutte le sue letture accettate."""

    case_id: str
    categoria: str
    difficolta: str
    ordine: str
    readings: tuple[AcceptedReading, ...]
    nota: str = ""

    @property
    def primary(self) -> AcceptedReading:
        """La lettura dell'annotazione, cioe' quella scritta nel corpus."""
        return self.readings[0]

    @property
    def expects_abstention(self) -> bool:
        """Vero se astenersi e' una risposta corretta per questo caso."""
        return any(
            any(e.kind == ConstraintKind.UNSUPPORTED.value for e in r.expected)
            for r in self.readings
        )


def load_corpus(path: Optional[Path] = None) -> list[BenchmarkCase]:
    """Legge il corpus e vi innesta le tolleranze dichiarate.

    Il file resta la fonte delle attese; questo modulo aggiunge soltanto le
    letture alternative gia' autorizzate dalle note, e lo fa qui in un punto
    solo perche' sia ispezionabile.
    """
    corpus_path = Path(path) if path is not None else CORPUS_PATH
    raw = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases: list[BenchmarkCase] = []
    for item in raw.get("casi", []):
        case_id = str(item["id"])
        tolerance = CASE_TOLERANCES.get(case_id)
        annotated = tuple(
            ExpectedConstraint(
                kind=str(entry["kind"]),
                params={k: v for k, v in entry.items() if k != "kind"},
                optional=bool(tolerance and i in tolerance.optional_indexes),
            )
            for i, entry in enumerate(item.get("atteso", []))
        )
        extra = frozenset(tolerance.tolerated_extra_kinds) if tolerance else frozenset()
        readings = [
            AcceptedReading(expected=annotated, tolerated_extra_kinds=extra)
        ]
        if tolerance:
            for alternative in tolerance.alternatives:
                readings.append(
                    AcceptedReading(
                        expected=tuple(
                            ExpectedConstraint(
                                kind=str(entry["kind"]),
                                params={
                                    k: v for k, v in entry.items() if k != "kind"
                                },
                            )
                            for entry in alternative
                        ),
                        tolerated_extra_kinds=extra,
                        origin=f"tolleranza dichiarata: {tolerance.nota}",
                    )
                )
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                categoria=str(item.get("categoria", "")),
                difficolta=str(item.get("difficolta", "")),
                ordine=str(item.get("ordine", "")),
                readings=tuple(readings),
                nota=str(item.get("nota", "")),
            )
        )
    return cases


# --------------------------------------------------------------------------
# Confronto fra un vincolo atteso e un vincolo emesso
# --------------------------------------------------------------------------


def _resolve(obj: Any, path: str) -> Any:
    """Segue una notazione puntata tipo "selector.field" dentro il vincolo.

    Restituisce `None` quando il percorso non esiste: per il confronto e'
    indistinguibile da un valore assente, ed e' cio' che si vuole, perche' un
    parametro che non c'e' e' un parametro sbagliato.
    """
    current: Any = obj
    for part in path.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return _plain(current)


def _plain(value: Any) -> Any:
    """Riduce enum e liste a tipi confrontabili con il JSON del corpus."""
    if value is None or isinstance(value, bool):
        return value
    enum_value = getattr(value, "value", None)
    if enum_value is not None and isinstance(enum_value, (str, int, float)):
        return enum_value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _same_scalar(expected: Any, found: Any) -> bool:
    """Confronto fra un valore atteso e uno emesso, con le tolleranze dichiarate.

    Numeri: uguaglianza a meno dell'epsilon (il corpus annota interi, il modello
    puo' emettere float).

    Stringhe: confronto senza distinzione di maiuscole e con contenimento nei
    due versi. E' una tolleranza vera e va dichiarata: il corpus annota il nucleo
    identificante ("Altomonte") mentre il traduttore puo' emettere la forma
    estesa ("impianto di Altomonte"). Il contenimento accetta la forma estesa e
    rifiuta un nome diverso, che e' il comportamento voluto; accetterebbe anche
    una stringa assurda che contenga per caso quella attesa, ed e' il prezzo
    noto di questa scelta.
    """
    if expected is None:
        return found is None
    if isinstance(expected, bool) or isinstance(found, bool):
        return bool(expected) == bool(found)
    if isinstance(expected, (int, float)) and isinstance(found, (int, float)):
        return abs(float(expected) - float(found)) <= _NUM_EPS
    a = str(expected).strip().casefold()
    b = str(found).strip().casefold()
    if not a or not b:
        return a == b
    return a == b or a in b or b in a


def _param_ok(expected: Any, found: Any) -> bool:
    """Un parametro atteso e' soddisfatto dal valore emesso.

    Sulle liste la regola e' il contenimento: ogni valore atteso deve comparire
    fra quelli emessi, i valori in piu' non contano errore. E' coerente con la
    convenzione del corpus, dove l'annotazione elenca i parametri che contano e
    lascia liberi gli altri: chiedere l'uguaglianza esatta delle liste
    significherebbe annotare anche cio' che l'autore ha deciso di non annotare.
    """
    if isinstance(expected, (list, tuple)):
        found_list = found if isinstance(found, (list, tuple)) else [found]
        return all(
            any(_same_scalar(e, f) for f in found_list) for e in expected
        )
    if isinstance(found, (list, tuple)):
        return any(_same_scalar(expected, f) for f in found)
    return _same_scalar(expected, found)


@dataclass(frozen=True)
class ParamCheck:
    """Verifica di un singolo parametro annotato."""

    path: str
    expected: Any
    found: Any
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "expected": self.expected,
            "found": self.found,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class MatchedPair:
    """Un vincolo atteso accoppiato a un vincolo emesso dello stesso tipo."""

    kind: str
    constraint_id: str
    checks: tuple[ParamCheck, ...]
    optional: bool

    @property
    def params_total(self) -> int:
        return len(self.checks)

    @property
    def params_correct(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def params_all_ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "constraint_id": self.constraint_id,
            "optional": self.optional,
            "params": [c.to_dict() for c in self.checks],
        }


def _checks_for(expected: ExpectedConstraint, produced: Any) -> tuple[ParamCheck, ...]:
    out: list[ParamCheck] = []
    for path, value in sorted(expected.params.items()):
        found = _resolve(produced, path)
        out.append(ParamCheck(path, value, found, _param_ok(value, found)))
    return tuple(out)


# --------------------------------------------------------------------------
# Valutazione di un caso
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    """Esito della valutazione di un caso contro la lettura che ha vinto."""

    case_id: str
    categoria: str
    difficolta: str
    ordine: str
    reading_index: int
    reading_origin: str
    expected_kinds: tuple[str, ...]
    produced_kinds: tuple[str, ...]
    matched: tuple[MatchedPair, ...]
    substitutions: tuple[tuple[str, str], ...]
    omissions: tuple[str, ...]
    hallucinations: tuple[str, ...]
    tolerated_missing: tuple[str, ...]
    tolerated_extra: tuple[str, ...]
    expects_abstention: bool
    emitted_abstention: bool
    translator: str
    translation_ms: float
    warnings: tuple[str, ...] = ()
    error: str = ""

    @property
    def true_positives(self) -> int:
        return len(self.matched)

    @property
    def false_negatives(self) -> int:
        return len(self.omissions) + len(self.substitutions)

    @property
    def false_positives(self) -> int:
        return len(self.hallucinations) + len(self.substitutions)

    @property
    def exact_set_match(self) -> bool:
        """Tutti e soli i vincoli attesi, per tipo.

        Le omissioni tollerate e le emissioni tollerate non la rompono: sono
        risposte che l'annotatore ha dichiarato accettabili.
        """
        return not (self.omissions or self.hallucinations or self.substitutions)

    @property
    def exact_match_with_params(self) -> bool:
        """Come sopra, ma con tutti i parametri annotati corretti."""
        return self.exact_set_match and all(m.params_all_ok for m in self.matched)

    @property
    def params_total(self) -> int:
        return sum(m.params_total for m in self.matched)

    @property
    def params_correct(self) -> int:
        return sum(m.params_correct for m in self.matched)

    @property
    def f1(self) -> float:
        """F1 del singolo caso, usato per il confronto appaiato fra traduttori.

        Convenzione dichiarata per i casi senza attesi: se non era atteso niente
        e non e' stato emesso niente, il caso vale 1 (astensione perfetta); se
        non era atteso niente ed e' stato emesso qualcosa, vale 0.
        """
        tp, fp, fn = self.true_positives, self.false_positives, self.false_negatives
        if tp == 0 and fp == 0 and fn == 0:
            return 1.0
        if tp == 0:
            return 0.0
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        return 2 * precision * recall / (precision + recall)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "categoria": self.categoria,
            "difficolta": self.difficolta,
            "ordine": self.ordine,
            "reading_index": self.reading_index,
            "reading_origin": self.reading_origin,
            "expected_kinds": list(self.expected_kinds),
            "produced_kinds": list(self.produced_kinds),
            "matched": [m.to_dict() for m in self.matched],
            "substitutions": [list(s) for s in self.substitutions],
            "omissions": list(self.omissions),
            "hallucinations": list(self.hallucinations),
            "tolerated_missing": list(self.tolerated_missing),
            "tolerated_extra": list(self.tolerated_extra),
            "expects_abstention": self.expects_abstention,
            "emitted_abstention": self.emitted_abstention,
            "exact_set_match": self.exact_set_match,
            "exact_match_with_params": self.exact_match_with_params,
            "params_total": self.params_total,
            "params_correct": self.params_correct,
            "f1": round(self.f1, 6),
            "translator": self.translator,
            "translation_ms": round(self.translation_ms, 3),
            "warnings": list(self.warnings),
            "error": self.error,
        }


def _evaluate_reading(
    case: BenchmarkCase,
    reading: AcceptedReading,
    reading_index: int,
    produced_set: ConstraintSet,
) -> CaseResult:
    """Valuta i vincoli emessi contro UNA lettura accettata.

    L'accoppiamento avviene in due fasi, e la distinzione conta.

    Fase 1, stesso tipo: fra gli attesi e gli emessi dello stesso tipo si
    sceglie l'abbinamento che massimizza i parametri corretti. E' un problema di
    assegnamento minuscolo (di rado piu' di due elementi per tipo) e si risolve
    con una selezione golosa sulle coppie ordinate per punteggio, deterministica
    a parita' di punteggio grazie agli indici.

    Fase 2, tipi diversi: cio' che resta spaiato si accoppia in ordine per
    registrare le SOSTITUZIONI, cioe' i casi in cui il traduttore ha capito che
    c'era un vincolo ma ha scelto il tipo sbagliato. Senza questa fase la
    matrice di confusione avrebbe solo la diagonale, e una sostituzione
    apparirebbe come una omissione piu' una allucinazione scollegate fra loro.
    """
    produced = list(produced_set.constraints)
    produced_kinds = tuple(c.kind.value for c in produced)
    expected = list(reading.expected)

    # --- fase 1: accoppiamento per tipo -------------------------------
    scored: list[tuple[float, int, int]] = []
    for ei, exp in enumerate(expected):
        for pi, prod in enumerate(produced):
            if prod.kind.value != exp.kind:
                continue
            checks = _checks_for(exp, prod)
            score = (
                sum(1 for c in checks if c.ok) / len(checks) if checks else 1.0
            )
            scored.append((score, ei, pi))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    used_expected: set[int] = set()
    used_produced: set[int] = set()
    matched: list[MatchedPair] = []
    for _score, ei, pi in scored:
        if ei in used_expected or pi in used_produced:
            continue
        used_expected.add(ei)
        used_produced.add(pi)
        exp = expected[ei]
        prod = produced[pi]
        matched.append(
            MatchedPair(
                kind=exp.kind,
                constraint_id=prod.constraint_id,
                checks=_checks_for(exp, prod),
                optional=exp.optional,
            )
        )

    leftover_expected = [i for i in range(len(expected)) if i not in used_expected]
    leftover_produced = [i for i in range(len(produced)) if i not in used_produced]

    # --- tolleranze sulle emissioni in piu' ---------------------------
    tolerated_extra: list[str] = []
    still_produced: list[int] = []
    for pi in leftover_produced:
        kind = produced[pi].kind.value
        if kind in reading.tolerated_extra_kinds:
            tolerated_extra.append(kind)
        else:
            still_produced.append(pi)

    # --- tolleranze sulle omissioni -----------------------------------
    tolerated_missing: list[str] = []
    still_expected: list[int] = []
    for ei in leftover_expected:
        if expected[ei].optional:
            tolerated_missing.append(expected[ei].kind)
        else:
            still_expected.append(ei)

    # --- fase 2: sostituzioni ------------------------------------------
    substitutions: list[tuple[str, str]] = []
    for ei, pi in zip(still_expected, still_produced):
        substitutions.append((expected[ei].kind, produced[pi].kind.value))
    omissions = [
        expected[ei].kind for ei in still_expected[len(substitutions) :]
    ]
    hallucinations = [
        produced[pi].kind.value for pi in still_produced[len(substitutions) :]
    ]

    return CaseResult(
        case_id=case.case_id,
        categoria=case.categoria,
        difficolta=case.difficolta,
        ordine=case.ordine,
        reading_index=reading_index,
        reading_origin=reading.origin,
        expected_kinds=tuple(e.kind for e in expected),
        produced_kinds=produced_kinds,
        matched=tuple(matched),
        substitutions=tuple(substitutions),
        omissions=tuple(omissions),
        hallucinations=tuple(hallucinations),
        tolerated_missing=tuple(tolerated_missing),
        tolerated_extra=tuple(tolerated_extra),
        expects_abstention=case.expects_abstention,
        emitted_abstention=any(
            c.kind is ConstraintKind.UNSUPPORTED for c in produced
        ),
        translator=produced_set.translator,
        translation_ms=float(produced_set.translation_ms),
        warnings=tuple(produced_set.warnings),
    )


def evaluate_case(case: BenchmarkCase, produced_set: ConstraintSet) -> CaseResult:
    """Valuta un caso contro tutte le letture accettate e tiene la migliore.

    "Migliore" e' definito nell'ordine: corrispondenza esatta con i parametri,
    poi corrispondenza esatta dei tipi, poi piu' vincoli corretti, poi meno
    allucinazioni, poi meno omissioni, poi piu' parametri esatti. L'ordine e'
    esplicito perche' scegliere la lettura piu' favorevole e' legittimo solo se
    il criterio e' dichiarato e uguale per tutti i traduttori.
    """
    candidates = [
        _evaluate_reading(case, reading, i, produced_set)
        for i, reading in enumerate(case.readings)
    ]
    return max(
        candidates,
        key=lambda r: (
            r.exact_match_with_params,
            r.exact_set_match,
            r.true_positives,
            -len(r.hallucinations),
            -len(r.omissions),
            r.params_correct,
        ),
    )


# --------------------------------------------------------------------------
# Esecuzione del benchmark
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KindScore:
    """Precisione, richiamo e F1 di un tipo di vincolo, con l'incertezza."""

    kind: str
    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def support(self) -> int:
        """Quante volte il tipo era atteso. E' il denominatore del richiamo."""
        return self.true_positives + self.false_negatives

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict[str, Any]:
        p_low, p_high = wilson_interval(
            self.true_positives, self.true_positives + self.false_positives
        )
        r_low, r_high = wilson_interval(self.true_positives, self.support)
        return {
            "kind": self.kind,
            "support": self.support,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "precision_ci": [round(p_low, 4), round(p_high, 4)],
            "recall": round(self.recall, 4),
            "recall_ci": [round(r_low, 4), round(r_high, 4)],
            "f1": round(self.f1, 4),
        }


@dataclass
class BenchmarkResult:
    """Il risultato completo del benchmark per un traduttore."""

    label: str
    translator: str
    cases: list[CaseResult]
    corpus_path: str = ""
    notes: list[str] = field(default_factory=list)

    # -- conteggi di base ------------------------------------------------

    @property
    def n_cases(self) -> int:
        return len(self.cases)

    @property
    def exact_set_matches(self) -> int:
        return sum(1 for c in self.cases if c.exact_set_match)

    @property
    def exact_matches_with_params(self) -> int:
        return sum(1 for c in self.cases if c.exact_match_with_params)

    @property
    def produced_total(self) -> int:
        return sum(len(c.produced_kinds) for c in self.cases)

    @property
    def hallucinations_total(self) -> int:
        return sum(len(c.hallucinations) + len(c.substitutions) for c in self.cases)

    def rate(self, successes: int, trials: int) -> dict[str, Any]:
        """Un tasso con il suo intervallo di Wilson e i suoi denominatori.

        Non esiste un tasso senza denominatore in questo modulo: quattro su
        cinque e quaranta su cinquanta sono lo stesso ottanta per cento e non
        sono lo stesso risultato.
        """
        low, high = wilson_interval(successes, trials)
        return {
            "successes": successes,
            "trials": trials,
            "rate": round(successes / trials, 4) if trials else 0.0,
            "ci95": [round(low, 4), round(high, 4)],
        }

    # -- metriche aggregate ----------------------------------------------

    def kind_scores(self) -> dict[str, KindScore]:
        """Precisione e richiamo per tipo di vincolo.

        Una sostituzione conta due volte, e deve: un falso negativo sul tipo
        atteso (quel vincolo non e' stato prodotto) e un falso positivo sul tipo
        emesso (quel vincolo non era richiesto).
        """
        tp: dict[str, int] = {}
        fp: dict[str, int] = {}
        fn: dict[str, int] = {}
        for case in self.cases:
            for m in case.matched:
                tp[m.kind] = tp.get(m.kind, 0) + 1
            for kind in case.omissions:
                fn[kind] = fn.get(kind, 0) + 1
            for kind in case.hallucinations:
                fp[kind] = fp.get(kind, 0) + 1
            for expected_kind, produced_kind in case.substitutions:
                fn[expected_kind] = fn.get(expected_kind, 0) + 1
                fp[produced_kind] = fp.get(produced_kind, 0) + 1
        kinds = sorted(set(tp) | set(fp) | set(fn))
        return {
            k: KindScore(k, tp.get(k, 0), fp.get(k, 0), fn.get(k, 0)) for k in kinds
        }

    def confusion_matrix(self) -> dict[str, dict[str, int]]:
        """Matrice attesi (righe) contro emessi (colonne), con la voce "(nessuno)".

        La riga `(nessuno)` raccoglie le allucinazioni: non era atteso niente ed
        e' stato emesso un vincolo. La colonna `(nessuno)` raccoglie le
        omissioni. La diagonale sono i successi per tipo.
        """
        matrix: dict[str, dict[str, int]] = {}

        def bump(row: str, col: str) -> None:
            matrix.setdefault(row, {})
            matrix[row][col] = matrix[row].get(col, 0) + 1

        for case in self.cases:
            for m in case.matched:
                bump(m.kind, m.kind)
            for expected_kind, produced_kind in case.substitutions:
                bump(expected_kind, produced_kind)
            for kind in case.omissions:
                bump(kind, NOTHING)
            for kind in case.hallucinations:
                bump(NOTHING, kind)
        return {row: dict(sorted(cols.items())) for row, cols in sorted(matrix.items())}

    def micro_scores(self) -> dict[str, Any]:
        """Precisione, richiamo e F1 aggregati su tutti i vincoli."""
        tp = sum(c.true_positives for c in self.cases)
        fp = sum(c.false_positives for c in self.cases)
        fn = sum(c.false_negatives for c in self.cases)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        p_low, p_high = wilson_interval(tp, tp + fp)
        r_low, r_high = wilson_interval(tp, tp + fn)
        return {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 4),
            "precision_ci95": [round(p_low, 4), round(p_high, 4)],
            "recall": round(recall, 4),
            "recall_ci95": [round(r_low, 4), round(r_high, 4)],
            "f1": round(f1, 4),
        }

    def macro_f1(self) -> float:
        """F1 media sui tipi attesi almeno una volta.

        La macro pesa ogni tipo allo stesso modo, anche quello che compare in un
        caso solo: dice come va sul repertorio, non sul traffico. La micro dice
        come va sul traffico. Si riportano entrambe perche' un traduttore che va
        bene solo sui tipi frequenti ha micro alta e macro bassa.
        """
        scores = [s for s in self.kind_scores().values() if s.support > 0]
        return sum(s.f1 for s in scores) / len(scores) if scores else 0.0

    def abstention(self) -> dict[str, Any]:
        """Astensione corretta e astensione a sproposito, una accanto all'altra.

        Riportare la prima da sola premierebbe il traduttore che si astiene
        sempre, che avrebbe astensione corretta del cento per cento e sarebbe
        inutile.
        """
        should = [c for c in self.cases if c.expects_abstention]
        should_not = [c for c in self.cases if not c.expects_abstention]
        return {
            "correct": self.rate(
                sum(1 for c in should if c.emitted_abstention), len(should)
            ),
            "spurious": self.rate(
                sum(1 for c in should_not if c.emitted_abstention), len(should_not)
            ),
        }

    def parameter_accuracy(self) -> dict[str, Any]:
        """Parametri annotati corretti, fra quelli dei vincoli col tipo giusto."""
        total = sum(c.params_total for c in self.cases)
        correct = sum(c.params_correct for c in self.cases)
        return self.rate(correct, total)

    def by_field(self, field_name: str) -> dict[str, dict[str, Any]]:
        """Corrispondenza esatta raggruppata per difficolta' o per categoria."""
        groups: dict[str, list[CaseResult]] = {}
        for case in self.cases:
            groups.setdefault(getattr(case, field_name), []).append(case)
        return {
            key: self.rate(sum(1 for c in rows if c.exact_set_match), len(rows))
            for key, rows in sorted(groups.items())
        }

    def hardest_cases(self, limit: int = 8) -> list[CaseResult]:
        """I casi peggiori, per guardarli invece di leggerne solo la media."""
        return sorted(self.cases, key=lambda c: (c.f1, c.case_id))[:limit]

    def tolerances_applied(self) -> dict[str, list[str]]:
        """Dove una tolleranza dichiarata ha davvero cambiato il conteggio.

        Serve perche' le tre forme di tolleranza (lettura alternativa, omissione
        tollerata, emissione tollerata) non lasciano tutte la stessa traccia.
        Solo la prima cambia `reading_index`; le altre due agiscono dentro la
        lettura dell'annotazione e sono invisibili da fuori. Il resoconto
        dichiarava quindi le sole letture alternative, e un caso come B044, in
        cui un UNSUPPORTED in piu' NON viene contato allucinazione perche' la
        nota lo ammette, finiva nel tasso di allucinazione senza comparire fra
        le tolleranze. Un tasso piu' basso grazie a una tolleranza taciuta e' un
        numero gonfiato, anche quando la tolleranza e' legittima.

        Le chiavi sono ordinate, cosi' due esecuzioni producono lo stesso
        resoconto e lo stesso JSON.
        """
        out: dict[str, list[str]] = {}
        for case in sorted(self.cases, key=lambda c: c.case_id):
            reasons: list[str] = []
            if case.reading_index > 0:
                reasons.append("lettura alternativa accettata dalla nota")
            if case.tolerated_missing:
                reasons.append(
                    "omissione non contata errore: "
                    + ", ".join(sorted(case.tolerated_missing))
                )
            if case.tolerated_extra:
                reasons.append(
                    "emissione in piu' non contata allucinazione: "
                    + ", ".join(sorted(case.tolerated_extra))
                )
            if reasons:
                out[case.case_id] = reasons
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "translator": self.translator,
            "corpus": self.corpus_path,
            "n_cases": self.n_cases,
            "exact_set_match": self.rate(self.exact_set_matches, self.n_cases),
            "exact_match_with_params": self.rate(
                self.exact_matches_with_params, self.n_cases
            ),
            "hallucination": self.rate(
                self.hallucinations_total, self.produced_total
            ),
            "abstention": self.abstention(),
            "parameter_accuracy": self.parameter_accuracy(),
            "micro": self.micro_scores(),
            "macro_f1": round(self.macro_f1(), 4),
            "by_kind": {k: s.to_dict() for k, s in self.kind_scores().items()},
            "confusion_matrix": self.confusion_matrix(),
            "by_difficulty": self.by_field("difficolta"),
            "by_category": self.by_field("categoria"),
            "translation_ms_total": round(
                sum(c.translation_ms for c in self.cases), 3
            ),
            # Tutte e tre le forme, non la sola lettura alternativa: vedi
            # `tolerances_applied`.
            "tolerances_used": sorted(self.tolerances_applied()),
            "tolerances_applied": self.tolerances_applied(),
            "notes": list(self.notes),
            "case_results": [c.to_dict() for c in self.cases],
        }


def _default_translate() -> Callable[..., ConstraintSet]:
    from tagliafuoco.nlu import rule_translator

    return rule_translator.translate


def run_benchmark(
    translate_fn: Optional[Callable[..., ConstraintSet]] = None,
    *,
    cases: Optional[Sequence[BenchmarkCase]] = None,
    scenario: Optional[Scenario] = None,
    label: str = "",
    corpus_path: Optional[Path] = None,
) -> BenchmarkResult:
    """Esegue il traduttore su ogni caso del corpus e ne misura le risposte.

    `translate_fn` ha la firma `(order_text, scenario) -> ConstraintSet`, cioe'
    quella di `nlu/rule_translator.translate` e di `nlu/translator.translate`.
    Senza argomento si usa il traduttore a regole, che e' il caso di riferimento
    perche' gira senza chiave e senza rete.

    Un traduttore che solleva un'eccezione su un caso non interrompe il
    benchmark: quel caso viene valutato contro un insieme vuoto e l'errore
    finisce nel risultato. Un guasto che azzera il punteggio di un caso e' un
    dato, non un motivo per perdere gli altri quarantanove.
    """
    fn = translate_fn or _default_translate()
    corpus = list(cases) if cases is not None else load_corpus(corpus_path)
    results: list[CaseResult] = []
    translators: set[str] = set()
    notes: list[str] = []

    for case in corpus:
        error = ""
        try:
            produced = fn(case.ordine, scenario)
        except Exception as exc:  # noqa: BLE001 - il guasto e' un dato
            error = (
                f"{case.case_id}: il traduttore ha sollevato "
                f"{type(exc).__name__}: {exc}"
            )
            notes.append(error)
            produced = ConstraintSet(
                constraint_set_id=f"ERR_{case.case_id}",
                source_text=case.ordine,
                constraints=[],
                translator="errore",
            )
        result = evaluate_case(case, produced)
        if error:
            result = replace(result, error=error)
        translators.add(produced.translator)
        results.append(result)

    return BenchmarkResult(
        label=label or "traduttore",
        translator="+".join(sorted(translators)) if translators else "",
        cases=results,
        corpus_path=str(corpus_path or CORPUS_PATH),
        notes=notes,
    )


# --------------------------------------------------------------------------
# Regole contro modello linguistico
# --------------------------------------------------------------------------


@dataclass
class TranslatorComparison:
    """Il confronto fra due traduttori sullo stesso corpus, appaiato per caso."""

    rules: BenchmarkResult
    model: Optional[BenchmarkResult]
    difference: Optional[PairedDifference]
    skipped_reason: str = ""

    def conclusion_lines(self) -> list[str]:
        """La conclusione, scritta e non lasciata dedurre.

        Questa e' la parte del progetto in cui e' piu' facile barare senza
        accorgersene, perche' il risultato atteso (il modello vince) e' anche
        quello che fa piu' bella figura. Se le regole arrivano vicine, qui ci
        deve stare scritto che il modello aggiunge poco.
        """
        if self.model is None:
            return [
                "Confronto non eseguito: " + (self.skipped_reason or "motivo ignoto"),
                "Il traduttore a regole resta il riferimento, e senza chiave e' "
                "anche l'unico traduttore che il sistema usa davvero.",
            ]

        rules_rate = self.rules.exact_set_matches / max(1, self.rules.n_cases)
        model_rate = self.model.exact_set_matches / max(1, self.model.n_cases)
        r_low, r_high = wilson_interval(self.rules.exact_set_matches, self.rules.n_cases)
        m_low, m_high = wilson_interval(self.model.exact_set_matches, self.model.n_cases)
        overlap = not (m_low > r_high or r_low > m_high)

        lines = [
            f"Corrispondenza esatta: regole {rules_rate:.1%} "
            f"(IC95 {r_low:.1%}-{r_high:.1%}), modello {model_rate:.1%} "
            f"(IC95 {m_low:.1%}-{m_high:.1%}), su {self.rules.n_cases} casi.",
        ]
        if self.difference is not None:
            d = self.difference
            lines.append(
                f"Differenza appaiata di F1 per caso: {d.mean_difference:+.3f} "
                f"(IC95 {d.interval.low:+.3f}, {d.interval.high:+.3f}; "
                f"p={d.test.p_value:.3f} su {d.n_pairs} casi)."
            )
        if overlap:
            lines.append(
                "Gli intervalli si sovrappongono: su questo corpus il modello "
                "linguistico non mostra un vantaggio misurabile rispetto alle "
                "regole. La conclusione onesta e' che, per il repertorio di "
                "vincoli di questo progetto, il modello aggiunge poco, e il "
                "sistema resta utilizzabile senza chiave."
            )
        elif model_rate > rules_rate:
            lines.append(
                "Il modello linguistico fa meglio delle regole di una quantita' "
                "misurabile su questo corpus. Resta vero che il suo unico canale "
                "di uscita e' il ConstraintSet: il guadagno e' sulla "
                "comprensione dell'ordine, non sulla decisione."
            )
        else:
            lines.append(
                "Le regole fanno meglio del modello su questo corpus. E' un "
                "risultato plausibile: il corpus e' scritto sul repertorio, e le "
                "regole sono state costruite sullo stesso repertorio."
            )
        lines.append(
            "Cinquanta casi restano pochi: qualunque differenza sotto i dieci "
            "punti percentuali va considerata non dimostrata."
        )
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": self.rules.to_dict(),
            "model": self.model.to_dict() if self.model else None,
            "difference": self.difference.to_dict() if self.difference else None,
            "skipped_reason": self.skipped_reason,
            "conclusion": self.conclusion_lines(),
        }


def compare_translators(
    *,
    cases: Optional[Sequence[BenchmarkCase]] = None,
    scenario: Optional[Scenario] = None,
    force_model: bool = False,
    seed: int = DEFAULT_SEED,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    permutations: int = DEFAULT_PERMUTATIONS,
) -> TranslatorComparison:
    """Esegue il benchmark sulle regole e, se ha senso, sul modello linguistico.

    Senza `ANTHROPIC_API_KEY` il confronto non si fa, e il motivo viene scritto:
    `nlu/translator.translate` senza chiave ricade sul traduttore a regole, per
    cui le due colonne sarebbero lo stesso traduttore e il pareggio sarebbe un
    artefatto. `force_model=True` lo esegue comunque, e serve solo ai test, che
    verificano il meccanismo con un finto traduttore.
    """
    corpus = list(cases) if cases is not None else load_corpus()
    rules_result = run_benchmark(cases=corpus, scenario=scenario, label="regole")

    if not config.has_llm_key() and not force_model:
        return TranslatorComparison(
            rules=rules_result,
            model=None,
            difference=None,
            skipped_reason=(
                "ANTHROPIC_API_KEY assente: il traduttore con modello ricade "
                "sulle regole, quindi il confronto misurerebbe lo stesso "
                "traduttore contro se' stesso"
            ),
        )

    from tagliafuoco.nlu import translator as llm_translator

    model_result = run_benchmark(
        llm_translator.translate, cases=corpus, scenario=scenario, label="modello"
    )
    difference = paired_difference(
        "f1_per_caso",
        "modello",
        "regole",
        [c.f1 for c in model_result.cases],
        [c.f1 for c in rules_result.cases],
        seed=seed,
        bootstrap_samples=bootstrap_samples,
        permutations=permutations,
        higher_is_better=True,
        label="F1 per caso",
        unit="frazione",
    )
    return TranslatorComparison(
        rules=rules_result, model=model_result, difference=difference
    )
