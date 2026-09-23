"""Confronto appaiato fra pianificatori, con l'incertezza sempre allegata.

PERCHE' IL CONFRONTO E' APPAIATO
--------------------------------
La tentazione e' eseguire il solver su cento finestre, eseguire la baseline su
cento finestre, fare due medie e sottrarle. E' sbagliato, e lo e' in modo
quantificabile.

La varianza fra scenari e' enorme: una finestra con due focolai vicini a una
base e una finestra con quaranta focolai sparsi su tre province non sono la
stessa prova. Nel campione tipico di una stagione quella variabilita' e' molto
piu' grande della differenza fra due pianificatori. Se si confrontano le due
medie, la differenza fra metodi finisce sepolta sotto il rumore di quale
finestra e' capitata nel campione, e il risultato dipende dalla stagione invece
che dal metodo.

Il confronto appaiato elimina proprio quella componente. Sullo STESSO scenario
si esegue ogni pianificatore, si calcola la differenza scenario per scenario, e
solo dopo si aggregano le differenze. Cio' che varia da finestra a finestra
(quanti focolai, dove, con quale flotta disponibile) e' identico nei due
termini della sottrazione e si cancella. Quello che resta e' la differenza
attribuibile al metodo. E' lo stesso motivo per cui in medicina si preferisce,
quando si puo', misurare lo stesso paziente prima e dopo invece di confrontare
due gruppi diversi.

Formalmente: si passa da `media(A) - media(B)`, la cui varianza contiene la
varianza fra scenari due volte, a `media(A - B)`, la cui varianza contiene solo
quella della differenza. Le due quantita' hanno lo stesso valore atteso; non
hanno affatto la stessa precisione.

PERCHE' SERVE L'INCERTEZZA, E DUE MISURE INVECE DI UNA
-----------------------------------------------------
Riportare "il solver copre il 4 per cento di focolai in piu'" senza dire su
quante finestre e con quale dispersione non e' un risultato: e' un aneddoto.
Con poche decine di finestre una differenza piccola non e' distinguibile dal
rumore, e chi legge deve poterlo vedere dal numero stesso.

Qui ci sono due misure, complementari, entrambe scritte a mano e senza scipy:

1. Intervallo di confidenza bootstrap sulla differenza media appaiata. Risponde
   a "quanto vale la differenza, con che precisione". Si ricampionano con
   reinserimento le differenze appaiate, si calcola la media di ogni
   ricampionamento, e si prendono i percentili della distribuzione ottenuta
   (metodo percentile). Non assume la normalita' dei dati, ma assume che il
   campione rappresenti la popolazione: con meno di una ventina di coppie
   l'intervallo tende a essere troppo stretto, e va detto.

2. Test di permutazione sul segno delle differenze appaiate. Risponde a "questa
   differenza si puo' spiegare con il caso". L'ipotesi nulla e' che i due
   pianificatori siano equivalenti: in quel caso su ogni scenario il segno
   della differenza sarebbe arbitrario, e invertirlo non cambierebbe la
   distribuzione. Si enumerano (o si campionano) le assegnazioni di segno e si
   guarda quanto spesso una media almeno cosi' estrema viene fuori per caso.
   L'assunzione e' la scambiabilita' del segno, cioe' che sotto l'ipotesi nulla
   la differenza sia simmetrica intorno allo zero.

Le due cose non si sostituiscono: un valore p piccolo dice che la differenza
probabilmente non e' zero, non che sia grande. Un intervallo stretto intorno a
una differenza minuscola dice che il metodo e' misurabilmente diverso e
praticamente irrilevante. Servono entrambi, e vanno stampati insieme.

DETERMINISMO
------------
Tutto il campionamento passa da un `random.Random` con seme fisso, creato qui
dentro e mai condiviso: due esecuzioni con gli stessi ingressi danno gli stessi
intervalli. Il seme finisce nel risultato serializzato, perche' un intervallo
bootstrap senza il seme che lo ha generato non e' riproducibile.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Mapping as AbcMapping
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from tagliafuoco.config import SOLVER_RANDOM_SEED
from tagliafuoco.domain.models import Plan, Scenario
from tagliafuoco.eval.metrics import (
    COVERAGE_WATER_RATIO,
    DEFAULT_METRIC_KEYS,
    METRIC_DEFINITIONS,
    MetricsDetail,
    compute_detail,
    metric_value,
)
from tagliafuoco.nlu.schema import ConstraintSet

__all__ = [
    "BootstrapInterval",
    "ComparisonResult",
    "PairedDifference",
    "PermutationTest",
    "ScenarioOutcome",
    "bootstrap_mean_ci",
    "compare_planners",
    "paired_difference",
    "permutation_sign_test",
    "wilson_interval",
]


#: Seme unico della valutazione. E' lo stesso del solver, non per eleganza: cosi'
#: una rigiocata e la sua analisi si citano con un numero solo.
DEFAULT_SEED: int = SOLVER_RANDOM_SEED

#: Ricampionamenti del bootstrap. Duemila e' il compromesso abituale: il
#: rumore Monte Carlo sull'estremo dell'intervallo e' gia' sotto la terza cifra,
#: e il costo resta trascurabile rispetto a una rigiocata.
DEFAULT_BOOTSTRAP_SAMPLES: int = 2000

#: Permutazioni casuali quando l'enumerazione esatta e' troppo grande.
DEFAULT_PERMUTATIONS: int = 2000

#: Fino a questo numero di coppie il test di permutazione si fa per
#: enumerazione completa (2^n assegnazioni di segno): il valore p e' esatto e
#: non dipende dal seme. Oltre, si campiona.
EXACT_PERMUTATION_MAX_PAIRS: int = 12

#: Sotto questo numero di coppie l'intervallo bootstrap va letto come
#: indicativo: il percentile bootstrap su campioni minuscoli sottostima
#: l'incertezza invece di sovrastimarla, che e' il verso sbagliato per sbagliare.
MIN_PAIRS_FOR_TRUST: int = 20

#: Soglia convenzionale del valore p. E' una convenzione, non una legge: 0.05
#: non separa il vero dal falso, separa "questo campione da solo non basta" da
#: "questo campione da solo qualcosa la dice". Sta in una costante perche' chi
#: legge un verdetto possa cambiarla e vedere quanto il verdetto ne dipende.
SIGNIFICANCE_ALPHA: float = 0.05

#: Tolleranza nei confronti fra medie permutate e media osservata. Serve a non
#: dichiarare "piu' estremo" un valore che differisce solo per l'aritmetica in
#: virgola mobile.
_EPS: float = 1e-12


# --------------------------------------------------------------------------
# Incertezza: gli attrezzi, scritti a mano
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapInterval:
    """Intervallo di confidenza percentile sulla media, con il suo contesto."""

    mean: float
    low: float
    high: float
    level: float
    samples: int
    n: int

    @property
    def excludes_zero(self) -> bool:
        """Vero se l'intervallo non contiene lo zero.

        Non e' un verdetto di importanza: dice soltanto che, con questo
        campione, la differenza nulla non e' fra i valori plausibili.
        """
        return (self.low > 0.0 and self.high > 0.0) or (
            self.low < 0.0 and self.high < 0.0
        )

    @property
    def trustworthy(self) -> bool:
        """Falso quando le coppie sono troppo poche perche' l'intervallo regga."""
        return self.n >= MIN_PAIRS_FOR_TRUST

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": round(self.mean, 6),
            "low": round(self.low, 6),
            "high": round(self.high, 6),
            "level": self.level,
            "samples": self.samples,
            "n": self.n,
            "excludes_zero": self.excludes_zero,
            "trustworthy": self.trustworthy,
        }


@dataclass(frozen=True)
class PermutationTest:
    """Esito del test di permutazione sul segno."""

    p_value: float
    permutations: int
    exact: bool
    n: int
    observed_mean: float

    @property
    def min_achievable_p(self) -> float:
        """Il valore p piu' piccolo che questo campione puo' produrre.

        Serve a non farsi ingannare dal numero stampato. Con `n` coppie il test
        esatto sul segno dispone di 2^n assegnazioni, e le due piu' estreme
        (tutti i segni come osservati, tutti invertiti) danno la stessa media in
        valore assoluto: il minimo bilaterale e' quindi `2 / 2^n`. Con quattro
        coppie vale 0.125, cioe' NESSUNA differenza, per quanto netta, puo'
        risultare significativa al cinque per cento. Non e' un difetto del test:
        e' la quantita' di prove che quel campione contiene.

        Nel caso campionato il minimo e' `1 / (1 + B)`, per la correzione di
        continuita' che impedisce a un valore p di valere zero.
        """
        if self.n <= 0:
            return 1.0
        if self.exact:
            return min(1.0, 2.0 / float(2 ** self.n))
        return 1.0 / (1.0 + self.permutations)

    def significant(self, alpha: float = SIGNIFICANCE_ALPHA) -> bool:
        """Vero se il valore p sta sotto la soglia. Nient'altro."""
        return self.p_value <= alpha

    def underpowered(self, alpha: float = SIGNIFICANCE_ALPHA) -> bool:
        """Vero se il campione non puo' raggiungere la soglia nemmeno al meglio.

        Quando e' vero, un valore p alto non e' una notizia sul divario fra i
        pianificatori: e' una notizia sul numero di finestre che si sono
        rigiocate. Riportarlo come "nessuna differenza" sarebbe una lettura
        sbagliata, e tacerlo sarebbe peggio.
        """
        return self.min_achievable_p > alpha

    def to_dict(self) -> dict[str, Any]:
        return {
            "p_value": round(self.p_value, 6),
            "permutations": self.permutations,
            "exact": self.exact,
            "n": self.n,
            "observed_mean": round(self.observed_mean, 6),
            "alpha": SIGNIFICANCE_ALPHA,
            "min_achievable_p": round(self.min_achievable_p, 6),
            "significant": self.significant(),
            "underpowered": self.underpowered(),
        }


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    level: float = 0.95,
    rng: Optional[random.Random] = None,
) -> BootstrapInterval:
    """Intervallo di confidenza percentile sulla media, per ricampionamento.

    Procedura, per intero: dal campione di n differenze si estraggono n valori
    con reinserimento, se ne calcola la media, e si ripete `samples` volte. La
    distribuzione delle medie cosi' ottenute approssima la distribuzione
    campionaria della media. Gli estremi dell'intervallo sono i percentili
    `(1 - level) / 2` e `1 - (1 - level) / 2` di quella distribuzione.

    Casi degeneri, tutti e tre gestiti in modo esplicito:
    - nessun valore: intervallo `(0, 0)` con media zero e `n = 0`. Chi stampa
      deve mostrare `n`, altrimenti un intervallo vuoto sembra una certezza.
    - un solo valore: l'intervallo collassa su quel valore. E' corretto e
      inutile: con una coppia sola non si stima nessuna dispersione, e il campo
      `trustworthy` resta falso.
    - valori tutti identici: l'intervallo collassa sul valore. Non e' un errore
      del bootstrap, e' cio' che dice il campione.

    Il generatore si passa dall'esterno perche' due intervalli calcolati nello
    stesso resoconto devono provenire dallo stesso flusso di numeri, altrimenti
    il resoconto non e' riproducibile riga per riga.
    """
    data = [float(v) for v in values]
    n = len(data)
    if n == 0:
        return BootstrapInterval(0.0, 0.0, 0.0, level, 0, 0)
    mean = sum(data) / n
    if n == 1 or samples <= 0:
        return BootstrapInterval(mean, mean, mean, level, max(0, samples), n)

    generator = rng if rng is not None else random.Random(DEFAULT_SEED)
    means: list[float] = []
    for _ in range(samples):
        total = 0.0
        for _ in range(n):
            total += data[generator.randrange(n)]
        means.append(total / n)
    means.sort()

    alpha = (1.0 - level) / 2.0
    return BootstrapInterval(
        mean=mean,
        low=_percentile_sorted(means, alpha),
        high=_percentile_sorted(means, 1.0 - alpha),
        level=level,
        samples=samples,
        n=n,
    )


def _percentile_sorted(ordered: Sequence[float], q: float) -> float:
    """Percentile con interpolazione lineare su una sequenza gia' ordinata.

    Stessa convenzione di `eval/metrics.percentile`, riscritta qui su tre righe
    per non importare quel modulo solo per una formula: se le due divergessero
    il test di `test_eval.py` che le confronta fallirebbe.
    """
    n = len(ordered)
    if n == 0:
        return 0.0
    if n == 1:
        return float(ordered[0])
    h = (n - 1) * q
    low = int(h)
    high = min(low + 1, n - 1)
    return float(ordered[low] + (ordered[high] - ordered[low]) * (h - low))


def permutation_sign_test(
    values: Sequence[float],
    *,
    permutations: int = DEFAULT_PERMUTATIONS,
    rng: Optional[random.Random] = None,
) -> PermutationTest:
    """Valore p bilaterale per la media di differenze appaiate, per permutazione.

    L'ipotesi nulla e' l'equivalenza dei due pianificatori. Sotto quell'ipotesi
    la differenza osservata su uno scenario avrebbe potuto presentarsi con il
    segno opposto: la coppia di piani sarebbe la stessa, a cambiare sarebbe solo
    quale dei due si chiama A. Si costruisce allora la distribuzione della media
    su tutte le assegnazioni di segno e si conta quante volte raggiunge o supera
    in valore assoluto la media osservata.

    Con al piu' `EXACT_PERMUTATION_MAX_PAIRS` coppie si enumerano tutte le 2^n
    assegnazioni: il valore p e' esatto e non dipende dal seme. Oltre, si
    campionano `permutations` assegnazioni e si usa la stima corretta
    `(1 + conteggio) / (1 + B)`. L'aggiunta di uno non e' un abbellimento: senza
    di essa un campionamento finito puo' produrre p uguale a zero, che
    significherebbe "impossibile per caso" e non e' mai cio' che un campione
    puo' dimostrare.

    Casi degeneri: nessuna coppia, oppure differenze tutte nulle, danno p uguale
    a 1. Non c'e' niente da distinguere, e dichiararlo e' meglio che
    restituire un numero piccolo per un artefatto aritmetico.
    """
    data = [float(v) for v in values]
    n = len(data)
    if n == 0:
        return PermutationTest(1.0, 0, True, 0, 0.0)

    observed = abs(sum(data) / n)
    if observed <= _EPS and all(abs(v) <= _EPS for v in data):
        return PermutationTest(1.0, 0, True, n, 0.0)

    if n <= EXACT_PERMUTATION_MAX_PAIRS:
        extreme = 0
        total = 0
        for signs in product((1.0, -1.0), repeat=n):
            total += 1
            candidate = abs(sum(s * v for s, v in zip(signs, data)) / n)
            if candidate + _EPS >= observed:
                extreme += 1
        return PermutationTest(extreme / total, total, True, n, observed)

    generator = rng if rng is not None else random.Random(DEFAULT_SEED)
    extreme = 0
    for _ in range(permutations):
        total = 0.0
        for v in data:
            total += v if generator.random() < 0.5 else -v
        if abs(total / n) + _EPS >= observed:
            extreme += 1
    return PermutationTest(
        (1 + extreme) / (1 + permutations), permutations, False, n, observed
    )


def wilson_interval(
    successes: int, trials: int, *, z: float = 1.96
) -> tuple[float, float]:
    """Intervallo di Wilson per una proporzione.

    Serve al benchmark della traduzione, dove le grandezze sono tassi (quante
    volte su cinquanta il traduttore ha indovinato). L'intervallo normale
    ingenuo `p +- z * sqrt(p(1-p)/n)` su questi numeri e' inservibile: con p
    vicino a zero o a uno produce estremi fuori dall'intervallo [0, 1] e, quel
    che e' peggio, larghezza zero quando il conteggio e' 0 su n oppure n su n,
    dichiarando certezza dove ci sono solo pochi dati.

    Wilson risolve entrambi i difetti invertendo il test invece di approssimare
    l'errore standard, e resta scrivibile in cinque righe. Con `trials = 0`
    restituisce l'intervallo `(0, 1)`: senza prove non si sa niente, e questa e'
    l'unica risposta onesta.
    """
    if trials <= 0:
        return (0.0, 1.0)
    p = successes / trials
    denom = 1.0 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = (
        z
        * math.sqrt(p * (1.0 - p) / trials + z * z / (4 * trials * trials))
        / denom
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


# --------------------------------------------------------------------------
# Differenza appaiata su una metrica
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PairedDifference:
    """Differenza appaiata fra due pianificatori su una metrica.

    Convenzione del segno, dichiarata una volta: la differenza e' sempre
    `A - B`, dove A e' il pianificatore in esame e B il riferimento. Il campo
    `higher_is_better` dice come leggerla, perche' su "minuti di risposta" una
    differenza negativa e' un miglioramento e su "copertura" e' un
    peggioramento.
    """

    metric: str
    label: str
    unit: str
    higher_is_better: bool
    planner_a: str
    planner_b: str
    differences: tuple[float, ...]
    pairs_dropped: int
    mean_a: Optional[float]
    mean_b: Optional[float]
    interval: BootstrapInterval
    test: PermutationTest
    wins_a: int
    wins_b: int
    ties: int

    @property
    def n_pairs(self) -> int:
        return len(self.differences)

    @property
    def mean_difference(self) -> float:
        return self.interval.mean

    @property
    def improvement(self) -> Optional[bool]:
        """Vero se A migliora su B, falso se peggiora, None se sono pari.

        Non tiene conto dell'incertezza di proposito: e' la direzione, non il
        verdetto. Il verdetto lo da' `verdict()`, che l'incertezza la guarda.
        """
        if abs(self.mean_difference) <= _EPS:
            return None
        better = self.mean_difference > 0
        return better if self.higher_is_better else not better

    @property
    def identical(self) -> bool:
        """Vero se su OGNI scenario i due hanno prodotto lo stesso valore.

        E' un caso diverso da "differenza non distinguibile dal rumore", e
        confonderli e' una bugia per imprecisione: il primo dice che non c'e'
        niente da misurare, il secondo che c'e' qualcosa ma il campione e'
        troppo piccolo per vederlo. Sulla copertura WUI di una rigiocata in cui
        nessuna finestra ha focolai in interfaccia, per esempio, i due
        pianificatori sono identici per costruzione, e stampare "servirebbero
        piu' finestre" manderebbe a raccogliere dati che non cambierebbero
        niente.
        """
        return bool(self.differences) and all(
            abs(d) <= _EPS for d in self.differences
        )

    def reading(self, alpha: float = SIGNIFICANCE_ALPHA) -> str:
        """Come va letta questa riga, in tre parole, guardando ENTRAMBE le misure.

        L'intervallo bootstrap e il test di permutazione rispondono a domande
        diverse e possono non concordare. Il caso che conta e' proprio quello:
        su poche finestre l'intervallo puo' escludere lo zero mentre il test sul
        segno non arriva nemmeno a poter essere significativo, perche' con `n`
        coppie il valore p non puo' scendere sotto `2 / 2^n`. Leggere solo
        l'intervallo, in quel caso, significa stampare "distinguibile dal
        rumore" accanto a un p di 0.125 e sperare che nessuno guardi.
        """
        if self.n_pairs == 0:
            return "nessuna coppia"
        if self.identical:
            return "valori identici"
        if not self.interval.excludes_zero:
            return "non distinguibile dal rumore"
        if self.test.significant(alpha):
            return "distinguibile dal rumore"
        if self.test.underpowered(alpha):
            # La cifra esatta del minimo sta nel verdetto e nelle avvertenze:
            # qui la colonna e' stretta e una riga spezzata in cinque non si
            # legge. La parola "insufficienti" e' il punto.
            return "coppie insufficienti"
        return "indizio, non prova"

    def verdict(self) -> str:
        """Una riga in italiano che dice cosa si puo' concludere, e cosa no."""
        if self.n_pairs == 0:
            return (
                f"{self.label}: nessuna coppia confrontabile, "
                "niente da concludere"
            )
        if self.identical:
            return (
                f"{self.label}: {self.planner_a} e {self.planner_b} hanno dato "
                f"lo stesso valore su tutte e {self.n_pairs} le finestre. Non "
                "e' una differenza troppo piccola per il campione: non c'e' "
                "nessuna differenza, e altre finestre non ne farebbero comparire "
                "una su questa metrica"
            )
        # "pari" regge la preposizione "a", "meglio" e "peggio" reggono "di":
        # la riga la legge una persona, e "sembra pari di tempo" non e' italiano.
        direction, prep = {
            True: ("meglio", "di"),
            False: ("peggio", "di"),
            None: ("pari", "a"),
        }[self.improvement]
        if not self.interval.excludes_zero:
            return (
                f"{self.label}: {self.planner_a} sembra {direction} {prep} "
                f"{self.planner_b}, ma l'intervallo di confidenza contiene lo "
                f"zero su {self.n_pairs} scenari: differenza non distinguibile "
                "dal rumore"
            )
        if self.test.underpowered():
            return (
                f"{self.label}: {self.planner_a} risulta {direction} {prep} "
                f"{self.planner_b} su tutte e {self.n_pairs} le coppie, ma con "
                f"{self.n_pairs} coppie il valore p non puo' scendere sotto "
                f"{self.test.min_achievable_p:.3f}: la direzione e' chiara, la "
                "prova statistica non c'e' e servono piu' finestre"
            )
        if not self.test.significant():
            return (
                f"{self.label}: {self.planner_a} risulta {direction} {prep} "
                f"{self.planner_b}, l'intervallo esclude lo zero ma il test sul "
                f"segno da' p={self.test.p_value:.3f}: e' un indizio, non una "
                "prova"
            )
        if not self.interval.trustworthy:
            return (
                f"{self.label}: {self.planner_a} risulta {direction} {prep} "
                f"{self.planner_b}, su sole {self.n_pairs} coppie (p="
                f"{self.test.p_value:.3f}): la direzione e' chiara, la "
                "dimensione va presa con prudenza"
            )
        return (
            f"{self.label}: {self.planner_a} risulta {direction} {prep} "
            f"{self.planner_b} su {self.n_pairs} scenari (p="
            f"{self.test.p_value:.3f})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "label": self.label,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "planner_a": self.planner_a,
            "planner_b": self.planner_b,
            "n_pairs": self.n_pairs,
            "pairs_dropped": self.pairs_dropped,
            "mean_a": None if self.mean_a is None else round(self.mean_a, 6),
            "mean_b": None if self.mean_b is None else round(self.mean_b, 6),
            "mean_difference": round(self.mean_difference, 6),
            "bootstrap": self.interval.to_dict(),
            "permutation": self.test.to_dict(),
            "wins_a": self.wins_a,
            "wins_b": self.wins_b,
            "ties": self.ties,
            "reading": self.reading(),
            "verdict": self.verdict(),
            "differences": [round(d, 6) for d in self.differences],
        }


def paired_difference(
    metric: str,
    planner_a: str,
    planner_b: str,
    values_a: Sequence[Optional[float]],
    values_b: Sequence[Optional[float]],
    *,
    seed: int = DEFAULT_SEED,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    permutations: int = DEFAULT_PERMUTATIONS,
    confidence_level: float = 0.95,
    higher_is_better: Optional[bool] = None,
    label: str = "",
    unit: str = "",
) -> PairedDifference:
    """Analizza una metrica confrontando i valori scenario per scenario.

    Le due sequenze devono essere allineate: la posizione i e' lo stesso
    scenario per entrambi i pianificatori. Una posizione in cui uno dei due
    valori e' None (metrica non definita per quel piano, per esempio il tempo di
    risposta di un piano che non assegna niente) viene scartata e conteggiata in
    `pairs_dropped`, mai sostituita con uno zero: uno zero inventato in una
    metrica dove meno e' meglio e' un premio a chi non ha fatto niente.
    """
    if len(values_a) != len(values_b):
        raise ValueError(
            "le due sequenze devono avere la stessa lunghezza: sono lo stesso "
            f"insieme di scenari ({len(values_a)} contro {len(values_b)})"
        )
    definition = METRIC_DEFINITIONS.get(metric)
    if higher_is_better is None:
        if definition is None:
            raise KeyError(
                f"metrica sconosciuta: {metric}. Passa higher_is_better per "
                "confrontare una grandezza non nel repertorio."
            )
        higher_is_better = definition.higher_is_better

    kept_a: list[float] = []
    kept_b: list[float] = []
    dropped = 0
    for a, b in zip(values_a, values_b):
        if a is None or b is None:
            dropped += 1
            continue
        kept_a.append(float(a))
        kept_b.append(float(b))

    diffs = [a - b for a, b in zip(kept_a, kept_b)]
    rng = random.Random(seed)
    interval = bootstrap_mean_ci(
        diffs, samples=bootstrap_samples, level=confidence_level, rng=rng
    )
    test = permutation_sign_test(diffs, permutations=permutations, rng=rng)

    sign = 1.0 if higher_is_better else -1.0
    wins_a = sum(1 for d in diffs if d * sign > _EPS)
    wins_b = sum(1 for d in diffs if d * sign < -_EPS)

    return PairedDifference(
        metric=metric,
        label=label or (definition.label if definition else metric),
        unit=unit or (definition.unit if definition else ""),
        higher_is_better=bool(higher_is_better),
        planner_a=planner_a,
        planner_b=planner_b,
        differences=tuple(diffs),
        pairs_dropped=dropped,
        mean_a=sum(kept_a) / len(kept_a) if kept_a else None,
        mean_b=sum(kept_b) / len(kept_b) if kept_b else None,
        interval=interval,
        test=test,
        wins_a=wins_a,
        wins_b=wins_b,
        ties=len(diffs) - wins_a - wins_b,
    )


# --------------------------------------------------------------------------
# Esecuzione dei pianificatori sugli stessi scenari
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioOutcome:
    """Che cosa ha prodotto un pianificatore su uno scenario."""

    planner: str
    scenario_id: str
    detail: MetricsDetail
    plan_seconds: float
    solver_status: str
    is_valid: Optional[bool]
    blocking_violations: int
    violated_rules: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "planner": self.planner,
            "scenario_id": self.scenario_id,
            "plan_seconds": round(self.plan_seconds, 4),
            "solver_status": self.solver_status,
            "is_valid": self.is_valid,
            "blocking_violations": self.blocking_violations,
            "violated_rules": list(self.violated_rules),
            **self.detail.to_dict(),
        }
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class ComparisonResult:
    """Il confronto completo: esiti per scenario, differenze appaiate, contesto."""

    label: str
    scenario_ids: list[str]
    planner_names: list[str]
    reference: str
    outcomes: dict[str, list[ScenarioOutcome]]
    differences: list[PairedDifference]
    seed: int
    bootstrap_samples: int
    permutations: int
    confidence_level: float
    coverage_threshold: float
    constraint_set_id: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def difference(self, metric: str, planner: str) -> Optional[PairedDifference]:
        """La differenza appaiata di un pianificatore contro il riferimento."""
        for d in self.differences:
            if d.metric == metric and d.planner_a == planner:
                return d
        return None

    def aggregate(self) -> dict[str, dict[str, Any]]:
        """Riassunto di stagione per pianificatore, sui conteggi e non sulle medie.

        Serve per la tabella di contesto: dice dove stanno i due metodi, mentre
        il giudizio sul loro divario sta nelle differenze appaiate. Non si
        sottraggono queste medie per ottenere la differenza, perche' e'
        esattamente il calcolo che il confronto appaiato sostituisce.
        """
        from tagliafuoco.eval.metrics import summarise_details

        out: dict[str, dict[str, Any]] = {}
        for name in self.planner_names:
            rows = self.outcomes.get(name, [])
            summary = dict(summarise_details(o.detail for o in rows))
            validated = [o.is_valid for o in rows if o.is_valid is not None]
            summary["plans_validated"] = len(validated)
            summary["plans_valid"] = sum(1 for v in validated if v)
            summary["plans_rejected"] = sum(1 for v in validated if not v)
            summary["plan_seconds_total"] = round(
                sum(o.plan_seconds for o in rows), 3
            )
            out[name] = summary
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "scenarios": len(self.scenario_ids),
            "scenario_ids": list(self.scenario_ids),
            "planners": list(self.planner_names),
            "reference": self.reference,
            "constraint_set_id": self.constraint_set_id,
            "settings": {
                "seed": self.seed,
                "bootstrap_samples": self.bootstrap_samples,
                "permutations": self.permutations,
                "confidence_level": self.confidence_level,
                "coverage_threshold": self.coverage_threshold,
            },
            "aggregate": self.aggregate(),
            "differences": [d.to_dict() for d in self.differences],
            "outcomes": {
                name: [o.to_dict() for o in rows]
                for name, rows in self.outcomes.items()
            },
            "notes": list(self.notes),
        }


def _call_planner(
    planner: Callable[..., Plan],
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet],
) -> Plan:
    """Invoca il pianificatore adattandosi alla sua firma.

    Si riusa l'adattatore gia' scritto in `sim/replay.py` invece di
    riscriverlo: se le due regole di invocazione divergessero, il confronto
    passerebbe i vincoli al solver e non alla baseline, e la baseline perderebbe
    per un motivo che non ha niente a che vedere con la sua qualita'.
    """
    from tagliafuoco.sim.replay import call_planner

    return call_planner(planner, scenario, constraint_set)


def _validate(
    plan: Plan, scenario: Scenario, constraint_set: Optional[ConstraintSet]
) -> tuple[Optional[bool], int, tuple[str, ...], str]:
    """Esito della validazione, ridotto a cio' che serve al confronto.

    Un validatore che esplode non deve fermare una valutazione di cento
    scenari: si registra il guasto e si prosegue con `is_valid` a None, che
    significa "non verificato" e non "verificato e accettato".
    """
    try:
        from tagliafuoco.validate.validator import validate_plan

        result = validate_plan(plan, scenario, constraint_set)
    except Exception as exc:  # noqa: BLE001 - il guasto si registra, non si alza
        return None, 0, (), f"{type(exc).__name__}: {exc}"
    blocking = result.blocking
    rules = tuple(sorted({v.rule for v in blocking}))
    return result.is_valid, len(blocking), rules, ""


def compare_planners(
    scenarios: Sequence[Scenario],
    planners: Mapping[str, Callable[..., Plan]] | Sequence[tuple[str, Callable[..., Plan]]],
    *,
    constraint_set: Optional[ConstraintSet] = None,
    metric_keys: Sequence[str] = DEFAULT_METRIC_KEYS,
    reference: Optional[str] = None,
    seed: int = DEFAULT_SEED,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    permutations: int = DEFAULT_PERMUTATIONS,
    confidence_level: float = 0.95,
    coverage_threshold: float = COVERAGE_WATER_RATIO,
    validate: bool = True,
    label: str = "",
) -> ComparisonResult:
    """Esegue ogni pianificatore su OGNI scenario e confronta a coppie appaiate.

    `planners` e' una mappa nome -> callable con firma `(scenario, constraints)`
    oppure `(scenario)`. Il riferimento del confronto e' l'ultimo pianificatore
    dichiarato, salvo indicarne un altro con `reference`: la convenzione e'
    "metodo nuovo contro baseline", e la baseline si scrive per ultima perche'
    e' quella che si legge come termine di paragone.

    Lo stesso `constraint_set` va a tutti i pianificatori. Darlo solo al solver
    significherebbe confrontare due sistemi che rispondono a ordini diversi, e
    il confronto misurerebbe l'ordine invece del metodo.

    Ogni piano viene validato (salvo `validate=False`) e l'esito entra nel
    risultato: un pianificatore che vince sulle metriche producendo piani che il
    validatore respinge non ha vinto niente, e il resoconto lo deve dire.
    """
    items = (
        list(planners.items())
        if isinstance(planners, AbcMapping)
        else list(planners)
    )
    if not items:
        raise ValueError("serve almeno un pianificatore da confrontare")
    if len(items) < 2:
        raise ValueError(
            "il confronto appaiato richiede almeno due pianificatori: con uno "
            "solo non c'e' nessuna differenza da misurare"
        )
    names = [name for name, _ in items]
    if len(set(names)) != len(names):
        raise ValueError(f"nomi di pianificatore ripetuti: {names}")
    ref = reference or names[-1]
    if ref not in names:
        raise ValueError(f"il riferimento '{ref}' non e' fra i pianificatori {names}")
    if not scenarios:
        raise ValueError("serve almeno uno scenario")

    unknown = [k for k in metric_keys if k not in METRIC_DEFINITIONS]
    if unknown:
        raise KeyError(f"metriche sconosciute: {unknown}")

    outcomes: dict[str, list[ScenarioOutcome]] = {name: [] for name in names}
    notes: list[str] = []

    for scenario in scenarios:
        for name, planner in items:
            started = time.perf_counter()
            try:
                plan = _call_planner(planner, scenario, constraint_set)
            except Exception as exc:  # noqa: BLE001
                elapsed = time.perf_counter() - started
                notes.append(
                    f"{name} non ha prodotto un piano su {scenario.scenario_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                plan = Plan(
                    plan_id=f"ERR_{name}_{scenario.scenario_id}",
                    horizon_minutes=scenario.horizon_minutes,
                    producer=name,
                    solver_status="ERROR",
                    notes=[f"{type(exc).__name__}: {exc}"],
                )
                outcomes[name].append(
                    ScenarioOutcome(
                        planner=name,
                        scenario_id=scenario.scenario_id,
                        detail=compute_detail(
                            plan, scenario, coverage_threshold=coverage_threshold
                        ),
                        plan_seconds=elapsed,
                        solver_status="ERROR",
                        is_valid=None,
                        blocking_violations=0,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            elapsed = time.perf_counter() - started

            detail = compute_detail(
                plan, scenario, coverage_threshold=coverage_threshold
            )
            if validate:
                is_valid, blocking, rules, error = _validate(
                    plan, scenario, constraint_set
                )
            else:
                is_valid, blocking, rules, error = None, 0, (), ""
            outcomes[name].append(
                ScenarioOutcome(
                    planner=name,
                    scenario_id=scenario.scenario_id,
                    detail=detail,
                    plan_seconds=elapsed,
                    solver_status=plan.solver_status,
                    is_valid=is_valid,
                    blocking_violations=blocking,
                    violated_rules=rules,
                    error=error,
                )
            )

    differences: list[PairedDifference] = []
    for metric in metric_keys:
        ref_values = [metric_value(o.detail, metric) for o in outcomes[ref]]
        for name in names:
            if name == ref:
                continue
            values = [metric_value(o.detail, metric) for o in outcomes[name]]
            differences.append(
                paired_difference(
                    metric,
                    name,
                    ref,
                    values,
                    ref_values,
                    seed=seed,
                    bootstrap_samples=bootstrap_samples,
                    permutations=permutations,
                    confidence_level=confidence_level,
                )
            )

    return ComparisonResult(
        label=label or "confronto appaiato",
        scenario_ids=[s.scenario_id for s in scenarios],
        planner_names=names,
        reference=ref,
        outcomes=outcomes,
        differences=differences,
        seed=seed,
        bootstrap_samples=bootstrap_samples,
        permutations=permutations,
        confidence_level=confidence_level,
        coverage_threshold=coverage_threshold,
        constraint_set_id=(
            constraint_set.constraint_set_id if constraint_set is not None else None
        ),
        notes=notes,
    )


def methodological_warnings(result: ComparisonResult) -> list[str]:
    """Le avvertenze che vanno stampate accanto a QUESTO risultato.

    Non sono generiche: si accendono guardando i numeri del confronto appena
    fatto. Una valutazione che non sa dire quando i propri numeri sono deboli
    e' peggio di una che non ne produce.
    """
    out: list[str] = []
    n = len(result.scenario_ids)
    if n < MIN_PAIRS_FOR_TRUST:
        out.append(
            f"Solo {n} scenari appaiati: con poche decine di finestre una "
            "differenza piccola non e' distinguibile dal rumore, e gli "
            "intervalli bootstrap vanno letti come indicativi."
        )
    underpowered = [d for d in result.differences if d.test.underpowered()]
    if underpowered:
        floor = max(d.test.min_achievable_p for d in underpowered)
        fewest = min(d.n_pairs for d in underpowered)
        out.append(
            f"{len(underpowered)} differenze su {len(result.differences)} hanno "
            f"troppe poche coppie appaiate (la peggiore ne ha {fewest}) perche' "
            "il test di permutazione sul segno possa scendere sotto "
            f"p={floor:.3f}: su quelle righe l'intervallo bootstrap dice la "
            "direzione, non la prova. Le coppie possono essere meno delle "
            f"{n} finestre quando la metrica non e' definita per uno dei due "
            "piani."
        )
    # Le righe in cui i due hanno prodotto ESATTAMENTE lo stesso valore si
    # contano a parte: metterle insieme alle altre gonfierebbe il numero delle
    # metriche "non distinguibili" con metriche su cui non c'e' proprio niente
    # da distinguere, e farebbe sembrare il campione piu' povero di quello che e'.
    identical = [d for d in result.differences if d.identical]
    if identical:
        out.append(
            f"{len(identical)} differenze su {len(result.differences)} sono "
            "nulle su ogni finestra: su quelle metriche i pianificatori hanno "
            "prodotto lo stesso valore, e non e' una questione di campione. "
            "Succede quando la metrica non ha materia su cui variare, per "
            "esempio la copertura WUI in finestre senza focolai in interfaccia."
        )
    indistinguishable = [
        d
        for d in result.differences
        if not d.interval.excludes_zero and not d.identical
    ]
    if indistinguishable:
        out.append(
            f"{len(indistinguishable)} differenze su {len(result.differences)} "
            "hanno l'intervallo di confidenza che contiene lo zero pur non "
            "essendo nulle: su quelle metriche i pianificatori non si "
            "distinguono con questo numero di finestre."
        )
    dropped = sum(d.pairs_dropped for d in result.differences)
    if dropped:
        out.append(
            f"{dropped} coppie scartate perche' la metrica non era definita per "
            "almeno uno dei due piani (tipicamente un piano senza assegnazioni). "
            "Sono escluse dalle medie, non sostituite con zeri."
        )
    for name, summary in result.aggregate().items():
        rejected = summary.get("plans_rejected", 0)
        if rejected:
            out.append(
                f"{name}: {rejected} piani su {summary.get('plans_validated', 0)} "
                "sono stati respinti dal validatore. Le loro metriche restano "
                "nel confronto, ma sono metriche di piani non eseguibili."
            )
        # Le due reticenze della misura, riportate per pianificatore: un costo
        # non ricostruibile abbassa la colonna del costo, un'assegnazione fuori
        # scenario sparisce dalle altre. Entrambe renderebbero un pianificatore
        # piu' bello di quello che e', ed entrambe sono mute se non si dicono.
        unknown = summary.get("costs_unknown", 0)
        if unknown:
            out.append(
                f"{name}: {unknown} assegnazioni non dichiarano il costo e "
                "citano un mezzo assente dallo scenario, quindi il loro costo "
                "non e' ricostruibile. Il costo riportato per questo "
                "pianificatore e' una sottostima."
            )
        off = summary.get("assignments_off_scenario", 0)
        if off:
            out.append(
                f"{name}: {off} assegnazioni puntano a focolai che non stanno "
                "nello scenario su cui sono misurate. Sono escluse da copertura, "
                "tempi di risposta e acqua: un piano e uno scenario che non "
                "combaciano sono un difetto del pianificatore o della rigiocata, "
                "non un risultato."
            )
    return out
