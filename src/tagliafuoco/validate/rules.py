"""Le singole regole di validazione, una funzione pura ciascuna.

PERCHE' QUESTO FILE ESISTE
--------------------------
Il validatore non e' un blocco monolitico che dice si' o no: e' una lista di
domande separate, ognuna rispondibile da sola e ognuna leggibile da sola. Ogni
regola ha la stessa firma, `(plan, scenario, constraint_set, tol)`, non tocca
lo stato e restituisce una lista di `Violation`. Da qui tre proprieta' utili:
si puo' testare una regola senza costruire l'intero risultato, si puo'
aggiungerne una senza toccare le altre, e il messaggio di errore resta
attaccato alla regola che lo ha prodotto invece di sciogliersi in un booleano.

La gravita' e' binaria per scelta. `BLOCKING` significa "il piano non e'
eseguibile": la fisica lo vieta oppure un vincolo duro del coordinatore lo
vieta. `WARNING` significa "il piano e' eseguibile ma qualcosa non torna":
una divergenza fra quello che il piano dichiara e quello che il validatore
ricalcola, oppure un vincolo che per natura non si puo' verificare come duro.
Solo le violazioni bloccanti rendono il piano non valido, altrimenti il
validatore diventerebbe rumore e verrebbe ignorato, che e' il modo tipico in
cui muoiono i controlli automatici.

Come il resto di `validate/`, questo modulo non importa niente da `optimize/`.
Anche la risoluzione dei selettori dei vincoli e' riscritta qui e non presa
da `nlu/resolver.py`: se il solver e il validatore risolvessero "gli
elicotteri" con la stessa funzione, un errore in quella funzione renderebbe
d'accordo i due moduli su un piano sbagliato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import floor, isfinite
from typing import Any, Callable, Optional

from tagliafuoco.domain.models import (
    Assignment,
    FireCluster,
    Plan,
    Scenario,
    Vehicle,
    VehicleStatus,
)
from tagliafuoco.nlu.schema import (
    ConstraintKind,
    ConstraintSet,
    Enforcement,
    Selector,
    SelectorField,
)
from tagliafuoco.validate import physics


# --------------------------------------------------------------------------
# Gravita' e violazioni
# --------------------------------------------------------------------------


class ValidationSeverity(str, Enum):
    """Quanto pesa una violazione.

    BLOCKING: il piano non e' eseguibile cosi' com'e'.
    WARNING: il piano resta eseguibile, ma c'e' una discordanza da mostrare.
    """

    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass
class Violation:
    """Una singola contestazione del validatore.

    I campi numerici `expected` e `found` sono tenuti separati dal messaggio
    perche' servono a due pubblici diversi: il messaggio si legge a schermo,
    la coppia di numeri si aggrega, si ordina e si mette in una tabella. Lo
    `delta` si calcola da solo quando entrambi sono numerici, cosi' chi crea
    una violazione non puo' sbagliarne il segno: e' sempre trovato meno
    atteso, cioe' positivo quando il piano eccede.
    """

    rule: str
    severity: ValidationSeverity
    message: str
    entities: dict[str, str] = field(default_factory=dict)
    expected: Optional[Any] = None
    found: Optional[Any] = None
    delta: Optional[float] = None

    def __post_init__(self) -> None:
        if self.delta is None and _is_number(self.expected) and _is_number(self.found):
            self.delta = float(self.found) - float(self.expected)

    @property
    def is_blocking(self) -> bool:
        return self.severity is ValidationSeverity.BLOCKING

    def to_dict(self) -> dict[str, Any]:
        """Forma serializzabile, usata dal report JSON e dalla API."""
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "entities": dict(self.entities),
            "expected": _jsonable(self.expected),
            "found": _jsonable(self.found),
            "delta": _jsonable(self.delta),
        }


def _is_number(value: Any) -> bool:
    """Vero per i numeri finiti. I booleani sono esclusi di proposito."""
    if isinstance(value, bool) or value is None:
        return False
    return isinstance(value, (int, float)) and isfinite(float(value))


def _jsonable(value: Any) -> Any:
    """Rende serializzabile un valore: infinito e NaN non stanno nel JSON."""
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, Enum):
        return value.value
    return value


# --------------------------------------------------------------------------
# Tolleranza numerica
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tolerance:
    """Quanto disaccordo si accetta prima di gridare al lupo.

    Due implementazioni in virgola mobile della stessa formula non danno mai
    gli stessi bit, e qui la differenza non e' nemmeno solo di arrotondamento:
    CP-SAT lavora su interi, quindi il solver arrotonda i minuti e i litri
    prima ancora di scrivere il piano. Una tolleranza troppo stretta
    trasformerebbe quell'arrotondamento in una pioggia di falsi allarmi, e un
    validatore che sbaglia spesso viene spento. Una tolleranza troppo larga fa
    il danno opposto: nasconde gli errori veri dentro il margine.

    I valori scelti:

    - `minutes = 1.0`. E' esattamente la dimensione dell'arrotondamento a
      minuti interi che ci si aspetta dal solver. Costa poco in termini di
      errori nascosti: un minuto per un Canadair a 333 km/h vale 5,5 km, cioe'
      molto meno del salto che farebbe un mezzo teletrasportato. Con dieci
      minuti si nasconderebbero 55 km e il controllo diventerebbe inutile.
    - `liters_ratio = 0.02` con `liters_floor = 100`. I litri sono sempre
      interi e la capacita' dei serbatoi e' un dato dichiarato: il due per
      cento copre gli arrotondamenti di un modello che lavora in ettolitri,
      il fondo fisso evita che sui mezzi piccoli la tolleranza relativa si
      annulli.
    - `distance_km = 0.05`. Cinquanta metri: sotto questa soglia la differenza
      fra due formule geodetiche non e' distinguibile dall'incertezza dei
      centroidi dei cluster, che derivano da pixel satellitari da 375 m.
    - `demand_ratio = 0.30`. Solo per confrontare il fabbisogno d'acqua
      dichiarato dall'arricchimento con quello ricalcolato: e' volutamente
      larga, perche' la differenza li' nasce da come si e' stimata l'area del
      focolaio, non da un errore di pianificazione. Sopra quella soglia si
      emette comunque un avviso, mai un blocco.
    - `metric_ratio = 0.05`. Confronto fra le metriche dichiarate nel piano e
      quelle ricalcolate: cinque per cento distingue un arrotondamento da una
      metrica calcolata su un'altra base.
    """

    minutes: float = 1.0
    liters_ratio: float = 0.02
    liters_floor: int = 100
    distance_km: float = 0.05
    demand_ratio: float = 0.30
    metric_ratio: float = 0.05

    def __post_init__(self) -> None:
        """Una tolleranza negativa e' rifiutata subito, non tollerata.

        Le regole sono scritte nella forma `trovato + tol < limite`: con un
        margine negativo quella somma diventa una sottrazione e il validatore
        comincia a respingere piani corretti per uno scarto che non esiste,
        senza che nulla lo dica. E' il tipo di errore che si scopre dopo aver
        gia' scritto i risultati di un esperimento, quindi si preferisce
        l'eccezione al minuto zero.
        """
        for nome in (
            "minutes",
            "liters_ratio",
            "liters_floor",
            "distance_km",
            "demand_ratio",
            "metric_ratio",
        ):
            valore = float(getattr(self, nome))
            if not isfinite(valore) or valore < 0.0:
                raise ValueError(
                    f"la tolleranza '{nome}' deve essere un numero finito non "
                    f"negativo, ricevuto {getattr(self, nome)!r}"
                )

    def liters_slack(self, reference: float) -> float:
        """Margine assoluto ammesso su una quantita' d'acqua.

        Il valore assoluto sul riferimento serve a non regalare un margine
        negativo se un giorno arrivasse una quantita' negativa: il margine
        deve restare un margine anche sui dati sporchi.
        """
        return max(float(self.liters_floor), abs(reference) * self.liters_ratio)


DEFAULT_TOLERANCE = Tolerance()


# --------------------------------------------------------------------------
# Risoluzione dei selettori (riscritta, non importata da nlu/resolver.py)
# --------------------------------------------------------------------------


def _norm(text: str) -> str:
    return text.strip().lower()


_TRUE_WORDS = {"true", "1", "si", "si'", "yes", "vero", "y"}


def _as_bool(text: str) -> bool:
    return _norm(text) in _TRUE_WORDS


def vehicle_matches(selector: Selector, vehicle: Vehicle) -> bool:
    """Vero se il mezzo appartiene all'insieme descritto dal selettore.

    Il confronto e' insensibile a maiuscole e spazi: il traduttore puo'
    produrre "CAN_01" o "can_01" e il coordinatore non deve accorgersene. Un
    selettore che parla di focolai non seleziona mezzi, e viceversa: in quel
    caso si risponde falso invece di sollevare un errore, cosi' un vincolo
    malformato non fa cadere l'intera validazione.
    """
    values = {_norm(v) for v in selector.values}
    f = selector.field
    if f is SelectorField.ALL:
        return True
    if f is SelectorField.VEHICLE_ID:
        return _norm(vehicle.vehicle_id) in values
    if f is SelectorField.VEHICLE_KIND:
        return _norm(vehicle.kind.value) in values
    if f is SelectorField.VEHICLE_DOMAIN:
        return _norm(vehicle.domain.value) in values
    if f is SelectorField.BASE_ID:
        return _norm(vehicle.home_base_id) in values
    return False


def fire_matches(selector: Selector, fire: FireCluster) -> bool:
    """Vero se il focolaio appartiene all'insieme descritto dal selettore."""
    values = {_norm(v) for v in selector.values}
    f = selector.field
    if f is SelectorField.ALL:
        return True
    if f is SelectorField.FIRE_ID:
        return _norm(fire.fire_id) in values
    if f is SelectorField.FIRE_COMUNE:
        return fire.comune is not None and _norm(fire.comune) in values
    if f is SelectorField.FIRE_PROVINCIA:
        return fire.provincia is not None and _norm(fire.provincia) in values
    if f is SelectorField.FIRE_SEVERITY:
        return _norm(fire.severity.value) in values
    if f is SelectorField.FIRE_IS_WUI:
        return any(_as_bool(v) == fire.is_wui for v in selector.values)
    if f is SelectorField.FIRE_IN_PROTECTED_AREA:
        return any(_as_bool(v) == fire.in_protected_area for v in selector.values)
    return False


def select_vehicles(selector: Selector, scenario: Scenario) -> list[Vehicle]:
    """Risolve il selettore contro la flotta dello scenario.

    L'ordine e' quello dello scenario, non quello dei valori scritti nel
    vincolo: cosi' due vincoli che descrivono lo stesso insieme con i valori
    elencati in ordine diverso producono la stessa sequenza di violazioni, e
    il resoconto non cambia da un'esecuzione all'altra.
    """
    return [v for v in scenario.vehicles if vehicle_matches(selector, v)]


def select_fires(selector: Selector, scenario: Scenario) -> list[FireCluster]:
    """Risolve il selettore contro i focolai dello scenario, nell'ordine dello scenario."""
    return [f for f in scenario.fires if fire_matches(selector, f)]


# --------------------------------------------------------------------------
# Utilita' interne
# --------------------------------------------------------------------------


def _entities(
    index: Optional[int] = None,
    assignment: Optional[Assignment] = None,
    **extra: str,
) -> dict[str, str]:
    """Etichette delle entita' coinvolte, per risalire dalla violazione ai dati."""
    out: dict[str, str] = {}
    if index is not None:
        out["assignment_index"] = str(index)
    if assignment is not None:
        out["vehicle_id"] = assignment.vehicle_id
        out["fire_id"] = assignment.fire_id
        if assignment.water_source_id:
            out["water_source_id"] = assignment.water_source_id
    out.update({k: str(v) for k, v in extra.items() if v is not None})
    return out


def _indexed(plan: Plan) -> list[tuple[int, Assignment]]:
    return list(enumerate(plan.assignments))


def assignment_numbers_are_finite(assignment: Assignment) -> bool:
    """Vero se i numeri dell'assegnazione sono tutti finiti.

    Un NaN o un infinito in un minuto non e' un piano sbagliato, e' un piano
    illeggibile: ogni confronto con NaN e' falso, quindi un'assegnazione cosi'
    attraversa indenne tutte le regole scritte nella forma `trovato < limite`
    e viene dichiarata eseguibile proprio perche' non si riesce a esaminarla.
    E' il caso peggiore per un validatore, perche' il silenzio coincide con
    l'assoluzione.
    """
    return all(
        _is_number(valore)
        for valore in (
            assignment.departure_minute,
            assignment.arrival_minute,
            assignment.release_minute,
            assignment.sorties,
            assignment.water_delivered_liters,
        )
    )


def _resolved(
    plan: Plan, scenario: Scenario
) -> list[tuple[int, Assignment, Vehicle, FireCluster]]:
    """Assegnazioni di cui esistono sia il mezzo sia il focolaio, con numeri leggibili.

    Le altre sono gia' segnalate da `check_reference_integrity` e da
    `check_assignment_wellformed`: le regole fisiche le saltano invece di
    inventarsi dei dati, altrimenti lo stesso errore comparirebbe travestito
    da cinque violazioni diverse.

    Il filtro sui numeri finiti non e' teorico. Il docstring di
    `check_assignment_wellformed` promette alle regole successive di poter
    dare per scontata la coerenza dei tempi, e finche' un NaN arrivava fin
    qui quella promessa era falsa: `check_night_flight` formattava l'orario
    di rientro e `int(round(nan))` faceva cadere l'intera validazione con un
    `ValueError`. Un validatore che solleva un'eccezione e' peggio di uno che
    assolve: non dice ne' si' ne' no, e chi lo chiama (la API, la CLI) non ha
    un piano respinto da mostrare, ha un errore di programma.
    """
    out = []
    for i, a in _indexed(plan):
        vehicle = scenario.vehicle_by_id(a.vehicle_id)
        fire = scenario.fire_by_id(a.fire_id)
        if vehicle is not None and fire is not None and assignment_numbers_are_finite(a):
            out.append((i, a, vehicle, fire))
    return out


def _source_for(
    assignment: Assignment, vehicle: Vehicle, fire: FireCluster, scenario: Scenario
):
    """Sorgente d'acqua usata dall'assegnazione.

    Se il piano ne dichiara una, si usa quella (e' cio' che il piano afferma).
    Altrimenti il validatore prende la piu' vicina compatibile: e' l'ipotesi
    piu' favorevole al piano, e un validatore deve accusare solo quando
    nemmeno l'ipotesi piu' favorevole regge.
    """
    if assignment.water_source_id:
        declared = scenario.water_source_by_id(assignment.water_source_id)
        if declared is not None:
            return declared
    nearest, _ = physics.nearest_usable_source(vehicle, fire, scenario)
    return nearest


def _origin_floor(
    vehicle: Vehicle,
    fire: FireCluster,
    scenario: Scenario,
) -> Optional[tuple[float, float]]:
    """Tempo e distanza minimi per arrivare sul focolaio, partendo dalla base.

    L'origine e' sempre il punto operativo del mezzo: la posizione corrente se
    lo scenario la dichiara, altrimenti la sua base. NON e' il focolaio della
    missione precedente, e la distinzione e' il cuore della correzione
    descritta in `check_temporal_consistency`: SPEC.md sezione 6 stabilisce
    che una missione finisce con il rientro in base, quindi al minuto di
    rilascio il mezzo e' a terra e non sopra l'incendio. Ogni missione parte
    da li', anche la seconda e la terza della giornata.

    Il tempo restituito comprende `setup_minutes`, cioe' il decollo o l'uscita
    dal distaccamento. SPEC.md sezione 6 lo colloca dentro la missione, fra il
    minuto di partenza e l'inizio del trasferimento:

        arrival_minute = departure_minute + setup_minutes + t_andata

    Ometterlo qui e' stata la seconda ambiguita' di specifica trovata dal
    confronto fra le due implementazioni, dopo quella sulla posizione del mezzo
    fra due missioni. L'effetto era sistematico e non occasionale: il limite
    risultava piu' basso del vero esattamente di `setup_minutes` su ogni
    assegnazione di ogni piano, quindi il validatore emetteva un avviso sul
    tempo medio di risposta su qualunque piano, anche perfettamente corretto.
    Un controllo che si lamenta sempre e' un controllo che qualcuno spegne.

    Restituisce None quando non si sa da dove il mezzo parta (nessuna
    posizione corrente e base assente dallo scenario). None non e' zero: zero
    significherebbe "qualsiasi arrivo istantaneo e' lecito" e sarebbe una
    bugia comoda, mentre None dice che di quell'assegnazione non si puo'
    affermare niente, e chi chiama la salta.
    """
    origin = physics.vehicle_origin(vehicle, scenario)
    if origin is None:
        return None
    setup = max(0.0, float(vehicle.setup_minutes))
    return (
        setup + physics.leg_minutes(vehicle, origin, fire.centroid),
        physics.travel_distance_km(vehicle, origin, fire.centroid),
    )


def assignments_in_departure_order(
    plan: Plan, vehicle_id: str
) -> list[tuple[int, Assignment]]:
    """Le assegnazioni di un mezzo con il loro indice nel piano, in ordine di partenza.

    `Plan.assignments_for_vehicle` ordina gia' per minuto di partenza ma perde
    l'indice, e l'indice serve per legare una violazione all'assegnazione
    esatta. A parita' di minuto di partenza vince l'ordine in cui il piano le
    elenca: due missioni che partono nello stesso istante devono essere
    esaminate sempre nello stesso ordine, altrimenti la diagnostica cambia da
    un'esecuzione all'altra e gli esperimenti non si riproducono.
    """
    coppie = [(i, a) for i, a in enumerate(plan.assignments) if a.vehicle_id == vehicle_id]
    coppie.sort(key=lambda coppia: (coppia[1].departure_minute, coppia[0]))
    return coppie


def arrival_floors(plan: Plan, scenario: Scenario) -> dict[int, tuple[float, float]]:
    """Per ogni assegnazione, il minimo fisico (minuti, km) per raggiungere il focolaio.

    Esiste come funzione a se' perche' ha due utenti: la regola sui tempi di
    arrivo, che lo usa come limite da non violare, e il ricalcolo delle
    metriche, che lo usa come tempo di risposta ricostruito. Se i due lo
    calcolassero ognuno per conto suo finirebbero per divergere, e il
    validatore accetterebbe un piano dichiarandolo contemporaneamente
    incoerente con le proprie metriche. E' successo davvero, ed e' la ragione
    di questa funzione.

    Il limite non dipende dall'ordine delle missioni: ogni missione parte
    dalla base, quindi la sequenza non cambia l'origine (SPEC.md sezione 6).
    Cio' che la sequenza vincola sono i tempi morti fra una missione e la
    successiva, e di quelli si occupano `check_temporal_consistency` per la
    sovrapposizione e `check_turnaround` per il tempo a terra.

    Le assegnazioni che puntano a entita' inesistenti, o di cui non si conosce
    l'origine, non compaiono nel risultato: chi legge deve distinguere "zero
    minuti" da "non calcolabile".
    """
    out: dict[int, tuple[float, float]] = {}
    for i, a in _indexed(plan):
        vehicle = scenario.vehicle_by_id(a.vehicle_id)
        fire = scenario.fire_by_id(a.fire_id)
        if vehicle is None or fire is None:
            continue
        floor_pair = _origin_floor(vehicle, fire, scenario)
        if floor_pair is not None:
            out[i] = floor_pair
    return out


# --------------------------------------------------------------------------
# Tarature locali delle regole
# --------------------------------------------------------------------------

# Quando un arrivo e' cosi' in ritardo rispetto al tempo di percorrenza da far
# sospettare un errore di calcolo nel pianificatore, e non semplicemente
# un'attesa. Sono due numeri arbitrari e vanno letti come tali: il fattore
# moltiplicativo serve alle tratte lunghe, il fondo fisso serve alle tratte
# corte, dove triplicare cinque minuti non vuol dire niente. Restano qui e non
# in `config` perche' riguardano solo la soglia di rumore di questo avviso, non
# il modello fisico che il solver deve condividere.
_RITARDO_FATTORE = 3.0
_RITARDO_SOGLIA_MIN = 30.0


# --------------------------------------------------------------------------
# Regole
# --------------------------------------------------------------------------


def check_reference_integrity(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Ogni assegnazione punta a un mezzo e a un focolaio che esistono davvero.

    Sembra pedante, ma e' il controllo che intercetta la classe di errori piu'
    insidiosa: un identificativo sbagliato fa sembrare coperto un focolaio che
    nessuno sta spegnendo, e tutte le metriche a valle diventano false.
    """
    out: list[Violation] = []
    for i, a in _indexed(plan):
        if scenario.vehicle_by_id(a.vehicle_id) is None:
            out.append(
                Violation(
                    rule="reference_integrity",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} usa il mezzo '{a.vehicle_id}', "
                        f"che non esiste nello scenario"
                    ),
                    entities=_entities(i, a),
                    expected="mezzo presente nello scenario",
                    found=a.vehicle_id,
                )
            )
        if scenario.fire_by_id(a.fire_id) is None:
            out.append(
                Violation(
                    rule="reference_integrity",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} punta al focolaio '{a.fire_id}', "
                        f"che non esiste nello scenario"
                    ),
                    entities=_entities(i, a),
                    expected="focolaio presente nello scenario",
                    found=a.fire_id,
                )
            )
        if a.water_source_id and scenario.water_source_by_id(a.water_source_id) is None:
            out.append(
                Violation(
                    rule="reference_integrity",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} dichiara la sorgente "
                        f"'{a.water_source_id}', che non esiste nello scenario"
                    ),
                    entities=_entities(i, a),
                    expected="sorgente presente nello scenario",
                    found=a.water_source_id,
                )
            )
    return out


def check_assignment_wellformed(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """I numeri di un'assegnazione hanno un senso preso da soli.

    Nessun tempo che scorre all'indietro, nessun lancio negativo, nessun
    litro negativo, nessun numero che non sia un numero. Sono controlli di
    forma, ma tengono pulite le regole successive, che possono dare per
    scontata la coerenza dell'ordine dei tempi.

    Il controllo sui valori finiti viene per primo e per l'assegnazione che
    non lo supera gli altri si saltano. Con un NaN in mezzo ogni confronto e'
    falso, quindi le disuguaglianze successive tacerebbero tutte e il
    messaggio direbbe "arriva (minuto nan) prima di partire (minuto nan)" o
    non direbbe niente: in entrambi i casi chi legge non saprebbe qual e' il
    numero rotto. Meglio una violazione sola che lo nomina.
    """
    out: list[Violation] = []
    for i, a in _indexed(plan):
        if not assignment_numbers_are_finite(a):
            rotti = [
                nome
                for nome, valore in (
                    ("minuto di partenza", a.departure_minute),
                    ("minuto di arrivo", a.arrival_minute),
                    ("minuto di rilascio", a.release_minute),
                    ("numero di lanci", a.sorties),
                    ("litri erogati", a.water_delivered_liters),
                )
                if not _is_number(valore)
            ]
            out.append(
                Violation(
                    rule="assignment_wellformed",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} dichiara un valore non numerico "
                        f"per: {', '.join(rotti)}. Il piano non e' esaminabile "
                        f"finche' quel numero non e' un numero"
                    ),
                    entities=_entities(i, a),
                    expected="valori numerici finiti",
                    found=", ".join(rotti),
                )
            )
            continue
        if a.arrival_minute + tol.minutes < a.departure_minute:
            out.append(
                Violation(
                    rule="assignment_wellformed",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} arriva (minuto {a.arrival_minute:.1f}) "
                        f"prima di partire (minuto {a.departure_minute:.1f})"
                    ),
                    entities=_entities(i, a),
                    expected=a.departure_minute,
                    found=a.arrival_minute,
                )
            )
        if a.release_minute + tol.minutes < a.arrival_minute:
            out.append(
                Violation(
                    rule="assignment_wellformed",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} si libera (minuto {a.release_minute:.1f}) "
                        f"prima di arrivare (minuto {a.arrival_minute:.1f})"
                    ),
                    entities=_entities(i, a),
                    expected=a.arrival_minute,
                    found=a.release_minute,
                )
            )
        if a.sorties < 0:
            out.append(
                Violation(
                    rule="assignment_wellformed",
                    severity=ValidationSeverity.BLOCKING,
                    message=f"l'assegnazione {i} dichiara {a.sorties} lanci",
                    entities=_entities(i, a),
                    expected=0,
                    found=a.sorties,
                )
            )
        if a.water_delivered_liters < 0:
            out.append(
                Violation(
                    rule="assignment_wellformed",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} dichiara "
                        f"{a.water_delivered_liters} litri"
                    ),
                    entities=_entities(i, a),
                    expected=0,
                    found=a.water_delivered_liters,
                )
            )
        if a.sorties == 0 and a.water_delivered_liters > 0:
            out.append(
                Violation(
                    rule="assignment_wellformed",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} eroga "
                        f"{a.water_delivered_liters} litri senza nessun lancio"
                    ),
                    entities=_entities(i, a),
                    expected=0,
                    found=a.water_delivered_liters,
                )
            )
    return out


def check_horizon(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """I tempi del piano stanno dentro l'orizzonte di pianificazione.

    L'orizzonte del piano e quello dello scenario possono differire: si prende
    il piu' stretto, perche' un piano non puo' pianificare oltre la finestra
    per cui lo scenario e' stato costruito (i focolai oltre quella soglia non
    sono piu' descritti dai dati che si hanno).
    """
    horizon = min(plan.horizon_minutes, scenario.horizon_minutes)
    out: list[Violation] = []
    for i, a in _indexed(plan):
        if a.departure_minute < -tol.minutes:
            out.append(
                Violation(
                    rule="horizon",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} parte al minuto "
                        f"{a.departure_minute:.1f}, prima dell'inizio dell'orizzonte"
                    ),
                    entities=_entities(i, a),
                    expected=0.0,
                    found=a.departure_minute,
                )
            )
        if a.release_minute > horizon + tol.minutes:
            out.append(
                Violation(
                    rule="horizon",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"l'assegnazione {i} si conclude al minuto "
                        f"{a.release_minute:.1f}, oltre l'orizzonte di "
                        f"{horizon} minuti"
                    ),
                    entities=_entities(i, a),
                    expected=float(horizon),
                    found=a.release_minute,
                )
            )
    return out


def check_vehicle_status(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Nessun mezzo in riserva, in manutenzione o fuori turno viene impiegato.

    Lo stato RESERVE e' il punto in cui si vede se il vincolo del coordinatore
    e' arrivato fino in fondo: il mezzo resta nello scenario, quindi un solver
    distratto puo' usarlo lo stesso, e senza questa regola nessuno se ne
    accorgerebbe guardando il piano.
    """
    vietati = {
        VehicleStatus.RESERVE: "e' tenuto di riserva",
        VehicleStatus.MAINTENANCE: "e' in manutenzione",
        VehicleStatus.OUT_OF_SHIFT: "e' fuori turno",
    }
    out: list[Violation] = []
    for i, a, vehicle, _fire in _resolved(plan, scenario):
        if vehicle.status in vietati:
            out.append(
                Violation(
                    rule="vehicle_status",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} ({vehicle.name}) "
                        f"{vietati[vehicle.status]} ma il piano lo impiega "
                        f"sul focolaio {a.fire_id}"
                    ),
                    entities=_entities(i, a),
                    expected=VehicleStatus.AVAILABLE.value,
                    found=vehicle.status.value,
                )
            )
    return out


def check_temporal_consistency(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Le missioni di uno stesso mezzo si susseguono senza sovrapporsi.

    CHE COSA SUCCEDE FRA DUE MISSIONI, E PERCHE' QUI C'ERA UN ERRORE
    ----------------------------------------------------------------
    Questa regola pretendeva, fra due missioni consecutive, il tempo di volo
    diretto da un focolaio all'altro: dava cioe' per scontato che al minuto di
    rilascio il mezzo fosse ancora sopra l'incendio. Il risolutore aveva letto
    la stessa formula di SPEC.md in modo opposto, e scriveva
    `release == arrival + cicli + t_ritorno`: per lui al rilascio il mezzo era
    gia' rientrato in base. Nessuna delle due letture era sbagliata finche' la
    specifica taceva, e nessuno dei due moduli aveva un difetto al proprio
    interno: il difetto stava nel confine fra i due. Il risultato era un
    validatore che annunciava teletrasporti inesistenti su piani perfettamente
    eseguibili, del tipo "il mezzo CAN_01 si sposta da F1 a F0 in 0.0 minuti,
    ma servono almeno 27.7 minuti".

    E' il caso piu' istruttivo prodotto da questo progetto, e vale la pena
    dirlo per esteso. La programmazione a N versioni viene di solito
    presentata come una rete contro gli errori di battitura; qui ha fatto
    molto di piu', ha costretto la specifica a dichiarare una cosa che aveva
    lasciato implicita. Con una sola implementazione quell'ambiguita' sarebbe
    rimasta invisibile: il codice avrebbe scelto per conto suo, e nessuno
    avrebbe avuto modo di accorgersi che c'era una scelta da fare. SPEC.md
    sezione 6 ora lo dice a chiare lettere: una missione termina con il
    rientro alla base di partenza.

    Ne segue che qui resta un controllo solo, la sovrapposizione: la missione
    successiva non puo' partire prima che la precedente abbia rilasciato il
    mezzo. Il trasferimento verso il focolaio successivo non va sommato,
    perche' e' gia' dentro l'avvicinamento della missione successiva e lo
    verifica `check_arrival_times` misurandolo dalla base: contarlo anche qui
    sarebbe contarlo due volte. Il tempo fermo a terra fra un rientro e la
    ripartenza esiste ed e' un vincolo vero, ma e' un'altra cosa e ha una
    regola sua, `check_turnaround`.
    """
    out: list[Violation] = []
    for vehicle in scenario.vehicles:
        missioni = assignments_in_departure_order(plan, vehicle.vehicle_id)
        if not missioni:
            continue

        first_index, first = missioni[0]
        if first.departure_minute + tol.minutes < vehicle.available_from_minute:
            out.append(
                Violation(
                    rule="temporal_consistency",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} parte al minuto "
                        f"{first.departure_minute:.1f} ma e' libero solo dal minuto "
                        f"{vehicle.available_from_minute:.1f}"
                    ),
                    entities=_entities(first_index, first),
                    expected=vehicle.available_from_minute,
                    found=first.departure_minute,
                )
            )

        for (_, prev), (index, cur) in zip(missioni, missioni[1:]):
            if cur.departure_minute + tol.minutes < prev.release_minute:
                out.append(
                    Violation(
                        rule="temporal_consistency",
                        severity=ValidationSeverity.BLOCKING,
                        message=(
                            f"il mezzo {vehicle.vehicle_id} e' in due posti nello "
                            f"stesso momento: impegnato sul focolaio {prev.fire_id} "
                            f"fino al minuto {prev.release_minute:.1f} e gia' in "
                            f"partenza per {cur.fire_id} al minuto "
                            f"{cur.departure_minute:.1f}"
                        ),
                        entities=_entities(
                            index, cur, previous_fire_id=prev.fire_id
                        ),
                        expected=prev.release_minute,
                        found=cur.departure_minute,
                    )
                )
    return out


def check_turnaround(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Fra un rientro e la ripartenza il mezzo si riattrezza.

    SPEC.md sezione 6, punto 2:

        departure(missione k+1) >= release(missione k) + turnaround_minutes

    `Base.turnaround_minutes` e' il tempo a terra che serve a rimettere in
    volo il mezzo: rifornimento, cambio equipaggio, controlli. I dati statici
    lo valorizzano base per base, dagli 8 minuti di un distaccamento ai 15 di
    un aeroporto che serve i Canadair, e fino a oggi nessuno lo leggeva: un
    mezzo poteva ripartire nello stesso istante in cui atterrava.

    La regola nasce insieme alla correzione di `check_temporal_consistency`, e
    non e' un caso. Finche' si credeva che al rilascio il mezzo fosse ancora
    sul focolaio, l'intervallo fra due missioni sembrava gia' occupato dal
    volo fra i due incendi, e non restava spazio per chiedersi che cosa
    succedesse a terra. Una volta stabilito che il mezzo rientra, quel volo
    sparisce da qui e resta scoperto il vincolo vero, che e' il tempo fermo in
    base. Correggere una convenzione sbagliata ha fatto comparire un controllo
    mancante: e' il modo tipico in cui questi difetti si presentano in coppia.

    Il riattrezzamento e' una proprieta' della BASE, non del mezzo, e si legge
    da `scenario.base_by_id(vehicle.home_base_id)`. Se quella base non e'
    nello scenario il tempo non e' conoscibile: la regola non inventa un
    valore di comodo e non blocca il piano, perche' sarebbe accusare il piano
    di una lacuna dei dati statici, ma nemmeno tace, perche' un controllo
    saltato in silenzio sembra un controllo superato. Emette un avviso, e chi
    legge sa che di quel mezzo il tempo a terra non e' stato verificato.
    """
    out: list[Violation] = []
    for vehicle in scenario.vehicles:
        missioni = assignments_in_departure_order(plan, vehicle.vehicle_id)
        if len(missioni) < 2:
            continue

        base = scenario.base_by_id(vehicle.home_base_id)
        if base is None:
            out.append(
                Violation(
                    rule="turnaround",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} svolge {len(missioni)} "
                        f"missioni di fila, ma la sua base "
                        f"'{vehicle.home_base_id}' non e' nello scenario: il tempo "
                        f"di riattrezzamento non e' verificabile"
                    ),
                    entities=_entities(
                        vehicle_id=vehicle.vehicle_id, base_id=vehicle.home_base_id
                    ),
                    expected="base presente nello scenario",
                    found=vehicle.home_base_id,
                )
            )
            continue

        richiesto = float(base.turnaround_minutes)
        if richiesto <= 0.0:
            continue

        for (_, prev), (index, cur) in zip(missioni, missioni[1:]):
            pausa = cur.departure_minute - prev.release_minute
            if pausa + tol.minutes < richiesto:
                out.append(
                    Violation(
                        rule="turnaround",
                        severity=ValidationSeverity.BLOCKING,
                        message=(
                            f"il mezzo {vehicle.vehicle_id} rientra dal focolaio "
                            f"{prev.fire_id} al minuto {prev.release_minute:.1f} e "
                            f"riparte per {cur.fire_id} al minuto "
                            f"{cur.departure_minute:.1f}: {pausa:.1f} minuti a "
                            f"terra, mentre la base {base.base_id} ({base.name}) "
                            f"ne chiede {richiesto:.0f} per il riattrezzamento"
                        ),
                        entities=_entities(
                            index,
                            cur,
                            previous_fire_id=prev.fire_id,
                            base_id=base.base_id,
                        ),
                        expected=richiesto,
                        found=pausa,
                    )
                )
    return out


def check_arrival_times(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """I tempi di arrivo sono compatibili con la distanza e con la velocita'.

    Si confronta il tempo di risposta dichiarato (arrivo meno partenza) con il
    tempo di volo o di percorrenza ricalcolato. Il confronto e' asimmetrico di
    proposito: arrivare troppo presto e' fisicamente impossibile, quindi e'
    bloccante; arrivare molto piu' tardi e' possibile (attese, code, cambi di
    rotta) e diventa solo un avviso quando lo scarto e' tanto grande da far
    sospettare un errore di calcolo nel pianificatore.
    """
    out: list[Violation] = []
    for i, (limite, distanza) in sorted(arrival_floors(plan, scenario).items()):
        a = plan.assignments[i]
        vehicle = scenario.vehicle_by_id(a.vehicle_id)
        if vehicle is None:  # pragma: no cover - arrival_floors li ha gia' esclusi
            continue
        response = a.arrival_minute - a.departure_minute
        if response + tol.minutes < limite:
            out.append(
                Violation(
                    rule="arrival_time",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} arriva sul focolaio "
                        f"{a.fire_id} in {response:.1f} minuti, ma servono almeno "
                        f"{limite:.1f} minuti ({distanza:.1f} km a "
                        f"{vehicle.cruise_speed_kmh:.0f} km/h)"
                    ),
                    entities=_entities(i, a),
                    expected=limite,
                    found=response,
                )
            )
        elif limite > 0.0 and response > limite * _RITARDO_FATTORE + _RITARDO_SOGLIA_MIN:
            out.append(
                Violation(
                    rule="arrival_time",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} impiega {response:.1f} "
                        f"minuti per un tragitto che ne richiede {limite:.1f}: "
                        f"il ritardo non e' spiegato dalla distanza"
                    ),
                    entities=_entities(i, a),
                    expected=limite,
                    found=response,
                )
            )
    return out


def check_endurance(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """L'autonomia del mezzo non viene superata.

    SPEC fissa `t_missione <= endurance_minutes` con
    `t_missione = t_setup + t_andata + n_lanci * t_ciclo + t_ritorno`.
    Il validatore controlla due cose: che i tempi dichiarati dal piano stiano
    dentro l'autonomia, e che ci stia anche la missione ricostruita da zero.
    La ricostruzione usa l'ipotesi ottimistica sui tempi di pescaggio, cosi'
    una differenza di modellazione non diventa un falso allarme: se sfora
    anche nell'ipotesi piu' favorevole, sfora davvero.
    """
    out: list[Violation] = []
    for i, a, vehicle, fire in _resolved(plan, scenario):
        declared = a.engaged_minutes
        if declared > vehicle.endurance_minutes + tol.minutes:
            out.append(
                Violation(
                    rule="endurance",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} resta impegnato "
                        f"{declared:.1f} minuti sul focolaio {a.fire_id}, oltre "
                        f"l'autonomia di {vehicle.endurance_minutes:.0f} minuti"
                    ),
                    entities=_entities(i, a),
                    expected=vehicle.endurance_minutes,
                    found=declared,
                )
            )
            continue

        origin = physics.vehicle_origin(vehicle, scenario)
        if origin is None:
            continue
        source = _source_for(a, vehicle, fire, scenario)
        timing = physics.mission_timing(
            vehicle, origin, fire.centroid, source, a.sorties, optimistic=True
        )
        if timing.total_minutes > vehicle.endurance_minutes + tol.minutes:
            out.append(
                Violation(
                    rule="endurance",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"la missione ricalcolata del mezzo {vehicle.vehicle_id} sul "
                        f"focolaio {a.fire_id} dura {timing.total_minutes:.1f} minuti "
                        f"(setup {timing.setup_minutes:.1f} + andata "
                        f"{timing.outbound_minutes:.1f} + {a.sorties} cicli da "
                        f"{timing.cycle_minutes:.1f} + rientro "
                        f"{timing.return_minutes:.1f}), oltre l'autonomia di "
                        f"{vehicle.endurance_minutes:.0f} minuti"
                    ),
                    entities=_entities(i, a),
                    expected=vehicle.endurance_minutes,
                    found=timing.total_minutes,
                )
            )
    return out


def check_shift(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Il turno dell'equipaggio non viene superato.

    La somma delle missioni del giorno deve stare dentro `max_shift_minutes`,
    e nella somma entra anche quanto quel mezzo ha gia' volato prima di questa
    finestra di pianificazione (`minutes_flown_today`): il turno e' una
    proprieta' della giornata dell'equipaggio, non della singola finestra.
    """
    out: list[Violation] = []
    for vehicle in scenario.vehicles:
        assegnate = plan.assignments_for_vehicle(vehicle.vehicle_id)
        if not assegnate:
            continue
        dichiarati = sum(a.engaged_minutes for a in assegnate)
        totale = vehicle.minutes_flown_today + dichiarati
        if totale > vehicle.max_shift_minutes + tol.minutes:
            pregresso = (
                f" (di cui {vehicle.minutes_flown_today:.0f} gia' volati oggi)"
                if vehicle.minutes_flown_today
                else ""
            )
            out.append(
                Violation(
                    rule="shift",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} accumula {totale:.1f} minuti "
                        f"di impiego{pregresso}, oltre il turno di "
                        f"{vehicle.max_shift_minutes:.0f} minuti"
                    ),
                    entities=_entities(vehicle_id=vehicle.vehicle_id),
                    expected=vehicle.max_shift_minutes,
                    found=totale,
                )
            )
    return out


def check_water_capacity(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """I litri dichiarati sono compatibili con capacita' per numero di cicli.

    E' il controllo piu' semplice e uno dei piu' efficaci: un pianificatore che
    conta male i cicli, o che assegna acqua a un mezzo senza fargli fare i
    lanci, viene scoperto qui. Si controlla anche il lato della sorgente: un
    serbatoio con capacita' dichiarata non puo' erogare piu' di quello che
    contiene.
    """
    out: list[Violation] = []
    prelievo_per_sorgente: dict[str, float] = {}

    for i, a, vehicle, _fire in _resolved(plan, scenario):
        massimo = physics.deliverable_liters(vehicle, a.sorties)
        if a.water_delivered_liters > massimo + tol.liters_slack(massimo):
            out.append(
                Violation(
                    rule="water_capacity",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il mezzo {vehicle.vehicle_id} dichiara "
                        f"{a.water_delivered_liters} litri sul focolaio {a.fire_id} "
                        f"con {a.sorties} lanci, ma la sua capacita' e' "
                        f"{vehicle.water_capacity_liters} litri per lancio: al "
                        f"massimo {massimo} litri"
                    ),
                    entities=_entities(i, a),
                    expected=float(massimo),
                    found=float(a.water_delivered_liters),
                )
            )
        if a.water_source_id:
            prelievo_per_sorgente[a.water_source_id] = (
                prelievo_per_sorgente.get(a.water_source_id, 0.0)
                + float(a.water_delivered_liters)
            )

    for source_id, litri in prelievo_per_sorgente.items():
        source = scenario.water_source_by_id(source_id)
        if source is None or source.capacity_liters is None:
            continue
        if litri > source.capacity_liters + tol.liters_slack(source.capacity_liters):
            out.append(
                Violation(
                    rule="water_capacity",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"dalla sorgente {source_id} ({source.name}) il piano preleva "
                        f"{litri:.0f} litri, ma la sua capacita' dichiarata e' "
                        f"{source.capacity_liters} litri"
                    ),
                    entities=_entities(water_source_id=source_id),
                    expected=float(source.capacity_liters),
                    found=litri,
                )
            )
    return out


def check_water_source(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Per ogni missione esiste una sorgente raggiungibile e compatibile.

    Compatibile: la sorgente deve essere utilizzabile da quel tipo di mezzo
    (un Canadair non pesca da un idrante, un'autobotte non si riempie in mare).
    Raggiungibile: il solo andirivieni fino alla sorgente deve stare dentro
    l'autonomia, altrimenti il mezzo non chiude nemmeno un ciclo.
    """
    out: list[Violation] = []
    for i, a, vehicle, fire in _resolved(plan, scenario):
        if not vehicle.requires_water_source:
            continue
        if a.sorties <= 0:
            continue

        if a.water_source_id:
            dichiarata = scenario.water_source_by_id(a.water_source_id)
            if dichiarata is not None and not physics.source_is_usable(
                vehicle, dichiarata
            ):
                out.append(
                    Violation(
                        rule="water_source",
                        severity=ValidationSeverity.BLOCKING,
                        message=(
                            f"il mezzo {vehicle.vehicle_id} ({vehicle.kind.value}) non "
                            f"puo' attingere dalla sorgente {a.water_source_id} "
                            f"({dichiarata.kind.value}): non e' compatibile"
                        ),
                        entities=_entities(i, a),
                        expected=f"sorgente utilizzabile da {vehicle.kind.value}",
                        found=dichiarata.source_id,
                    )
                )
                continue

        source = _source_for(a, vehicle, fire, scenario)
        if source is None:
            out.append(
                Violation(
                    rule="water_source",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"nessuna sorgente d'acqua compatibile con il mezzo "
                        f"{vehicle.vehicle_id} ({vehicle.kind.value}) per il focolaio "
                        f"{a.fire_id}, ma il piano gli attribuisce {a.sorties} lanci"
                    ),
                    entities=_entities(i, a),
                    expected="almeno una sorgente compatibile",
                    found="nessuna",
                )
            )
            continue

        andirivieni = physics.leg_minutes(vehicle, fire.centroid, source.point) * 2.0
        if andirivieni > vehicle.endurance_minutes + tol.minutes:
            km = physics.travel_distance_km(vehicle, fire.centroid, source.point)
            out.append(
                Violation(
                    rule="water_source",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"la sorgente piu' vicina al focolaio {a.fire_id} utilizzabile "
                        f"dal mezzo {vehicle.vehicle_id} e' a {km:.1f} km: il solo "
                        f"andata e ritorno costa {andirivieni:.1f} minuti, oltre "
                        f"l'autonomia di {vehicle.endurance_minutes:.0f} minuti"
                    ),
                    entities=_entities(i, a, water_source_id=source.source_id),
                    expected=vehicle.endurance_minutes,
                    found=andirivieni,
                )
            )
    return out


def check_night_flight(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Di notte non volano i mezzi non abilitati.

    Due controlli con gravita' diversa. Se lo scenario si dichiara notturno e
    un mezzo aereo senza abilitazione notturna vola, e' bloccante: e' il dato
    che il pianificatore aveva sotto gli occhi. Se invece lo scenario e'
    diurno ma l'orizzonte sfora la finestra di luce tarata in `config`, e' un
    avviso: quelle soglie sono orari medi di stagione, non effemeridi, e un
    piano che finisce dieci minuti dopo il tramonto convenzionale non e'
    necessariamente sbagliato.
    """
    out: list[Violation] = []
    for i, a, vehicle, _fire in _resolved(plan, scenario):
        if not vehicle.is_aerial:
            continue

        if scenario.is_night and not vehicle.night_capable:
            out.append(
                Violation(
                    rule="night_flight",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"scenario notturno: il mezzo aereo {vehicle.vehicle_id} "
                        f"({vehicle.name}) non e' abilitato al volo notturno ma il "
                        f"piano lo manda sul focolaio {a.fire_id}"
                    ),
                    entities=_entities(i, a),
                    expected="mezzo abilitato al volo notturno",
                    found=f"{vehicle.vehicle_id} non abilitato",
                )
            )
            continue

        if scenario.is_night and vehicle.night_capable:
            base = scenario.base_by_id(vehicle.home_base_id)
            if base is not None and not base.has_night_capability:
                out.append(
                    Violation(
                        rule="night_flight",
                        severity=ValidationSeverity.WARNING,
                        message=(
                            f"il mezzo {vehicle.vehicle_id} e' abilitato al volo "
                            f"notturno ma la base {base.base_id} ({base.name}) non "
                            f"ha operativita' notturna dichiarata"
                        ),
                        entities=_entities(i, a, base_id=base.base_id),
                        expected="base con operativita' notturna",
                        found=base.base_id,
                    )
                )
            continue

        if vehicle.night_capable:
            continue
        partenza = physics.clock_minute(scenario.timestamp, a.departure_minute)
        rientro = physics.clock_minute(scenario.timestamp, a.release_minute)
        if not physics.is_daylight(partenza) or not physics.is_daylight(rientro):
            out.append(
                Violation(
                    rule="night_flight",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"la missione del mezzo {vehicle.vehicle_id} sul focolaio "
                        f"{a.fire_id} cade fuori dalla finestra di luce tarata "
                        f"(partenza alle {_hhmm(partenza)}, rientro alle "
                        f"{_hhmm(rientro)}) e il mezzo non e' abilitato al volo "
                        f"notturno"
                    ),
                    entities=_entities(i, a),
                    expected="missione dentro la finestra di luce",
                    found=f"{_hhmm(partenza)}-{_hhmm(rientro)}",
                )
            )
    return out


def _hhmm(clock: float) -> str:
    """Minuti dalla mezzanotte in formato hh:mm, per i messaggi.

    Il resto sul giorno serve perche' qui arrivano anche minuti oltre le
    ventiquattro ore (un orizzonte lungo, una finestra che scavalca la
    mezzanotte): senza, si leggerebbe "25:10", che non e' un orario.

    Il minuto 1440 fa eccezione e si scrive "24:00" invece di "00:00": e' il
    valore massimo che lo schema ammette per la chiusura di una finestra
    temporale, e "gli aerei fino alle 00:00" si legge come un divieto totale
    mentre significa il contrario, cioe' fino a fine giornata.

    Un valore non finito non ha un orario e non ne prende uno di comodo: si
    scrive "--:--". E' l'ultima difesa, non la prima. Chi chiama dovrebbe
    avere gia' scartato l'assegnazione illeggibile, ma questa funzione serve
    solo a comporre un messaggio, e un messaggio che non si riesce a scrivere
    non puo' essere la ragione per cui l'intera validazione non risponde.
    """
    if not isfinite(clock):
        return "--:--"
    minuti = int(round(clock))
    if minuti == int(physics.MINUTES_PER_DAY):
        return "24:00"
    total = minuti % int(physics.MINUTES_PER_DAY)
    return f"{total // 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------
# Vincoli duri del ConstraintSet
# --------------------------------------------------------------------------


def check_hard_constraints(
    plan: Plan,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    tol: Tolerance = DEFAULT_TOLERANCE,
) -> list[Violation]:
    """Tutti i vincoli duri tradotti dall'ordine del coordinatore sono rispettati.

    Qui si chiude il cerchio del progetto: il modello linguistico ha tradotto
    un ordine in vincoli tipizzati, il solver li ha (si spera) applicati, e
    questa regola verifica sul piano prodotto che ci siano davvero. E' l'unico
    punto in cui si misura se la catena ha tenuto da un capo all'altro.

    I vincoli che agiscono solo sui pesi dell'obiettivo (priorita' di zona,
    protezione di un'infrastruttura, ripesatura) non sono verificabili su un
    singolo piano: se arrivano marcati come duri si emette un avviso, perche'
    e' un errore di traduzione, non un piano sbagliato.
    """
    if constraint_set is None:
        return []

    out: list[Violation] = []
    for constraint in constraint_set.constraints:
        if constraint.kind is ConstraintKind.UNSUPPORTED:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"parte dell'ordine non e' stata applicata: "
                        f"{getattr(constraint, 'reason', '')}"
                    ),
                    entities=_entities(constraint_id=constraint.constraint_id),
                    expected="vincolo rappresentabile",
                    found=ConstraintKind.UNSUPPORTED.value,
                )
            )
            continue

        if constraint.enforcement is not Enforcement.HARD:
            continue

        handler = _HARD_HANDLERS.get(constraint.kind)
        if handler is None:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"il vincolo {constraint.constraint_id} "
                        f"({constraint.kind.value}) agisce sui pesi dell'obiettivo: "
                        f"non e' verificabile come vincolo duro su un singolo piano"
                    ),
                    entities=_entities(constraint_id=constraint.constraint_id),
                    expected=Enforcement.SOFT.value,
                    found=Enforcement.HARD.value,
                )
            )
            continue
        out.extend(handler(constraint, plan, scenario, tol))
    return out


def _c_entities(constraint: Any, **extra: str) -> dict[str, str]:
    base = {"constraint_id": constraint.constraint_id, "kind": constraint.kind.value}
    base.update({k: str(v) for k, v in extra.items() if v is not None})
    return base


def _used_vehicle_ids(plan: Plan) -> set[str]:
    return {a.vehicle_id for a in plan.assignments}


def _check_reserve(constraint, plan, scenario, tol) -> list[Violation]:
    """RESERVE_VEHICLE: i mezzi tenuti di riserva non compaiono nel piano.

    Con `until_minute` la riserva e' temporanea: e' violata solo da una
    partenza precedente a quell'istante. Con `count` il coordinatore chiede di
    tenerne fermi almeno N fra quelli selezionati, non tutti. I due parametri
    possono arrivare insieme ("tienimene due fermi fino alle undici"): in quel
    caso conta fermo il mezzo che non parte prima di quell'istante, non il
    mezzo che non parte mai, altrimenti la riserva temporanea verrebbe letta
    come permanente e si respingerebbero piani leciti.

    Se il selettore non trova nessun mezzo nello scenario la regola tace. E'
    una scelta discutibile e va detta: un vincolo che parla di un mezzo
    inesistente e' un errore di traduzione, ma non e' un difetto del piano, e
    contestarlo qui darebbe la colpa all'imputato sbagliato.
    """
    out: list[Violation] = []
    selezionati = select_vehicles(constraint.selector, scenario)
    if not selezionati:
        return out
    usati = _used_vehicle_ids(plan)

    if constraint.count is not None:
        if constraint.until_minute is None:
            fermi = [v for v in selezionati if v.vehicle_id not in usati]
        else:
            fermi = [
                v
                for v in selezionati
                if all(
                    a.departure_minute >= constraint.until_minute
                    for a in plan.assignments_for_vehicle(v.vehicle_id)
                )
            ]
        if len(fermi) < constraint.count:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il vincolo {constraint.constraint_id} chiede di tenere di "
                        f"riserva almeno {constraint.count} mezzi fra "
                        f"{constraint.selector.describe()}"
                        + (
                            f" fino al minuto {constraint.until_minute}"
                            if constraint.until_minute is not None
                            else ""
                        )
                        + f", ma ne restano fermi {len(fermi)}"
                    ),
                    entities=_c_entities(constraint),
                    expected=float(constraint.count),
                    found=float(len(fermi)),
                )
            )
        return out

    for vehicle in selezionati:
        for a in plan.assignments_for_vehicle(vehicle.vehicle_id):
            if (
                constraint.until_minute is not None
                and a.departure_minute >= constraint.until_minute
            ):
                continue
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il vincolo {constraint.constraint_id} tiene di riserva "
                        f"{constraint.selector.describe()}, ma il mezzo "
                        f"{vehicle.vehicle_id} e' impiegato sul focolaio {a.fire_id} "
                        f"dal minuto {a.departure_minute:.1f}"
                    ),
                    entities=_c_entities(
                        constraint, vehicle_id=vehicle.vehicle_id, fire_id=a.fire_id
                    ),
                    expected="mezzo non impiegato",
                    found=f"impiegato su {a.fire_id}",
                )
            )
    return out


def _check_exclude(constraint, plan, scenario, tol) -> list[Violation]:
    """EXCLUDE_VEHICLE: il mezzo escluso non compare in nessuna assegnazione."""
    out: list[Violation] = []
    usati = _used_vehicle_ids(plan)
    for vehicle in select_vehicles(constraint.selector, scenario):
        if vehicle.vehicle_id in usati:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il vincolo {constraint.constraint_id} esclude "
                        f"{constraint.selector.describe()}, ma il piano impiega il "
                        f"mezzo {vehicle.vehicle_id}"
                    ),
                    entities=_c_entities(constraint, vehicle_id=vehicle.vehicle_id),
                    expected="mezzo non impiegato",
                    found="impiegato",
                )
            )
    return out


def _check_force(constraint, plan, scenario, tol) -> list[Violation]:
    """FORCE_ASSIGNMENT: sul focolaio indicato ci sono almeno N mezzi richiesti."""
    out: list[Violation] = []
    fuochi = select_fires(constraint.fire_selector, scenario)
    ammessi = {v.vehicle_id for v in select_vehicles(constraint.vehicle_selector, scenario)}
    for fire in fuochi:
        presenti = {
            a.vehicle_id
            for a in plan.assignments_for_fire(fire.fire_id)
            if a.vehicle_id in ammessi
        }
        if len(presenti) < constraint.min_count:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il vincolo {constraint.constraint_id} impone almeno "
                        f"{constraint.min_count} mezzi fra "
                        f"{constraint.vehicle_selector.describe()} sul focolaio "
                        f"{fire.fire_id}, ma ce ne sono {len(presenti)}"
                    ),
                    entities=_c_entities(constraint, fire_id=fire.fire_id),
                    expected=float(constraint.min_count),
                    found=float(len(presenti)),
                )
            )
    return out


def _check_forbid(constraint, plan, scenario, tol) -> list[Violation]:
    """FORBID_ASSIGNMENT: l'accoppiamento vietato non compare nel piano."""
    out: list[Violation] = []
    vietati = {v.vehicle_id for v in select_vehicles(constraint.vehicle_selector, scenario)}
    fuochi = {f.fire_id for f in select_fires(constraint.fire_selector, scenario)}
    for a in plan.assignments:
        if a.vehicle_id in vietati and a.fire_id in fuochi:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il vincolo {constraint.constraint_id} vieta di mandare "
                        f"{constraint.vehicle_selector.describe()} su "
                        f"{constraint.fire_selector.describe()}, ma il piano assegna "
                        f"{a.vehicle_id} al focolaio {a.fire_id}"
                    ),
                    entities=_c_entities(
                        constraint, vehicle_id=a.vehicle_id, fire_id=a.fire_id
                    ),
                    expected="accoppiamento assente",
                    found=f"{a.vehicle_id} su {a.fire_id}",
                )
            )
    return out


def _check_max_response(constraint, plan, scenario, tol) -> list[Violation]:
    """MAX_RESPONSE_TIME: il primo arrivo sul focolaio sta sotto il tetto.

    Il tempo controllato e' `arrival_minute`, cioe' l'istante di arrivo
    misurato dall'inizio dell'orizzonte: e' la lettura che corrisponde alla
    frase del coordinatore ("entro venti minuti da adesso"). Un focolaio
    selezionato ma scoperto produce un avviso e non un blocco, perche' il
    vincolo parla di quanto ci si mette ad arrivare, non impone di arrivare.
    """
    out: list[Violation] = []
    for fire in select_fires(constraint.fire_selector, scenario):
        arrivi = [a.arrival_minute for a in plan.assignments_for_fire(fire.fire_id)]
        if not arrivi:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"il vincolo {constraint.constraint_id} fissa un tetto di "
                        f"{constraint.minutes} minuti sul focolaio {fire.fire_id}, "
                        f"che pero' resta scoperto"
                    ),
                    entities=_c_entities(constraint, fire_id=fire.fire_id),
                    expected=float(constraint.minutes),
                    found=None,
                )
            )
            continue
        primo = min(arrivi)
        if primo > constraint.minutes + tol.minutes:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il vincolo {constraint.constraint_id} impone l'arrivo entro "
                        f"{constraint.minutes} minuti sul focolaio {fire.fire_id}, ma "
                        f"il primo mezzo arriva al minuto {primo:.1f}"
                    ),
                    entities=_c_entities(constraint, fire_id=fire.fire_id),
                    expected=float(constraint.minutes),
                    found=primo,
                )
            )
    return out


def _check_min_vehicles(constraint, plan, scenario, tol) -> list[Violation]:
    """MIN_VEHICLES_ON_FIRE: numero minimo di mezzi distinti su ogni focolaio scelto."""
    out: list[Violation] = []
    ammessi: Optional[set[str]] = None
    if constraint.vehicle_selector is not None:
        ammessi = {
            v.vehicle_id for v in select_vehicles(constraint.vehicle_selector, scenario)
        }
    for fire in select_fires(constraint.fire_selector, scenario):
        presenti = {
            a.vehicle_id
            for a in plan.assignments_for_fire(fire.fire_id)
            if ammessi is None or a.vehicle_id in ammessi
        }
        if len(presenti) < constraint.count:
            out.append(
                Violation(
                    rule="hard_constraint",
                    severity=ValidationSeverity.BLOCKING,
                    message=(
                        f"il vincolo {constraint.constraint_id} impone almeno "
                        f"{constraint.count} mezzi sul focolaio {fire.fire_id}, ma il "
                        f"piano ne assegna {len(presenti)}"
                    ),
                    entities=_c_entities(constraint, fire_id=fire.fire_id),
                    expected=float(constraint.count),
                    found=float(len(presenti)),
                )
            )
    return out


def _check_quota(constraint, plan, scenario, tol) -> list[Violation]:
    """VEHICLE_TYPE_QUOTA: quanti mezzi di quel tipo sono impiegati."""
    out: list[Violation] = []
    usati = _used_vehicle_ids(plan)
    selezionati = select_vehicles(constraint.selector, scenario)
    impiegati = [v for v in selezionati if v.vehicle_id in usati]
    quanti = len(impiegati)

    if constraint.min_used is not None and quanti < constraint.min_used:
        out.append(
            Violation(
                rule="hard_constraint",
                severity=ValidationSeverity.BLOCKING,
                message=(
                    f"il vincolo {constraint.constraint_id} chiede almeno "
                    f"{constraint.min_used} mezzi fra "
                    f"{constraint.selector.describe()}, ma il piano ne impiega "
                    f"{quanti}"
                ),
                entities=_c_entities(constraint),
                expected=float(constraint.min_used),
                found=float(quanti),
            )
        )
    if constraint.max_used is not None and quanti > constraint.max_used:
        out.append(
            Violation(
                rule="hard_constraint",
                severity=ValidationSeverity.BLOCKING,
                message=(
                    f"il vincolo {constraint.constraint_id} ammette al piu' "
                    f"{constraint.max_used} mezzi fra "
                    f"{constraint.selector.describe()}, ma il piano ne impiega "
                    f"{quanti}"
                ),
                entities=_c_entities(constraint),
                expected=float(constraint.max_used),
                found=float(quanti),
            )
        )
    return out


def _check_time_window(constraint, plan, scenario, tol) -> list[Violation]:
    """TIME_WINDOW: i mezzi selezionati operano solo dentro la finestra.

    Interpretazione adottata: `from_minute` e `to_minute` sono minuti
    dall'inizio del giorno, cioe' orari ("gli aerei fino alle otto"), coerente
    con il docstring dello schema e con l'intervallo ammesso 0..1440. I tempi
    del piano sono invece minuti dall'inizio dell'orizzonte, quindi vengono
    riportati sull'orologio usando `scenario.timestamp`. Se il solver leggesse
    quei due numeri come minuti di piano, le due implementazioni
    divergerebbero: e' una divergenza che questo validatore fa emergere invece
    di nasconderla, ed e' segnalata nel messaggio.

    Il confronto si fa sui minuti assoluti e non sull'ora del giorno, e questa
    non e' pignoleria. Con l'ora del giorno una missione che parte alle 23:00
    e rientra all'01:00 diventa "dalle 1380 alle 60": il rientro sembra
    precedere la partenza e una finestra 06:00-12:00 risulterebbe rispettata,
    cioe' esattamente il piano piu' sbagliato passerebbe. Ancorando la finestra
    al giorno in cui il mezzo parte, il rientro del giorno dopo cade oltre la
    chiusura e viene contestato.
    """
    out: list[Violation] = []
    giorno_iniziale = physics.minutes_from_midnight(scenario.timestamp)
    for vehicle in select_vehicles(constraint.selector, scenario):
        for a in plan.assignments_for_vehicle(vehicle.vehicle_id):
            # Un'assegnazione con tempi illeggibili la contesta
            # `check_assignment_wellformed`. Qui si salta, perche' il calcolo
            # della mezzanotte passa per `floor`, che su un NaN non torna
            # indietro con un numero ma con un'eccezione.
            if not assignment_numbers_are_finite(a):
                continue
            assoluta_partenza = giorno_iniziale + a.departure_minute
            assoluto_rientro = giorno_iniziale + a.release_minute
            # La finestra si ripete ogni giorno: la si ancora alla mezzanotte
            # del giorno in cui il mezzo parte.
            mezzanotte = (
                floor(assoluta_partenza / physics.MINUTES_PER_DAY)
                * physics.MINUTES_PER_DAY
            )
            apertura = mezzanotte + constraint.from_minute
            chiusura = mezzanotte + constraint.to_minute
            partenza = physics.clock_minute(scenario.timestamp, a.departure_minute)
            rientro = physics.clock_minute(scenario.timestamp, a.release_minute)
            fuori = (
                assoluta_partenza + tol.minutes < apertura
                or assoluto_rientro > chiusura + tol.minutes
            )
            if fuori:
                out.append(
                    Violation(
                        rule="hard_constraint",
                        severity=ValidationSeverity.BLOCKING,
                        message=(
                            f"il vincolo {constraint.constraint_id} ammette "
                            f"{constraint.selector.describe()} solo fra "
                            f"{_hhmm(constraint.from_minute)} e "
                            f"{_hhmm(constraint.to_minute)}, ma il mezzo "
                            f"{vehicle.vehicle_id} opera dalle {_hhmm(partenza)} "
                            f"alle {_hhmm(rientro)} (orari ricavati da "
                            f"scenario.timestamp)"
                        ),
                        entities=_c_entities(
                            constraint, vehicle_id=vehicle.vehicle_id, fire_id=a.fire_id
                        ),
                        expected=(
                            f"{_hhmm(constraint.from_minute)}-"
                            f"{_hhmm(constraint.to_minute)}"
                        ),
                        found=f"{_hhmm(partenza)}-{_hhmm(rientro)}",
                    )
                )
    return out


# Tabella dei vincoli duri verificabili su un piano. I tipi che agiscono solo
# sui pesi dell'obiettivo (PRIORITIZE_ZONE, OBJECTIVE_WEIGHT,
# PROTECT_INFRASTRUCTURE) non sono qui: su un singolo piano non esiste un
# criterio per dire se un peso e' stato rispettato, e fingere di controllarli
# darebbe una falsa sicurezza.
_HARD_HANDLERS: dict[ConstraintKind, Callable[..., list[Violation]]] = {
    ConstraintKind.RESERVE_VEHICLE: _check_reserve,
    ConstraintKind.EXCLUDE_VEHICLE: _check_exclude,
    ConstraintKind.FORCE_ASSIGNMENT: _check_force,
    ConstraintKind.FORBID_ASSIGNMENT: _check_forbid,
    ConstraintKind.MAX_RESPONSE_TIME: _check_max_response,
    ConstraintKind.MIN_VEHICLES_ON_FIRE: _check_min_vehicles,
    ConstraintKind.VEHICLE_TYPE_QUOTA: _check_quota,
    ConstraintKind.TIME_WINDOW: _check_time_window,
}


# --------------------------------------------------------------------------
# Registro delle regole
# --------------------------------------------------------------------------

Rule = Callable[[Plan, Scenario, Optional[ConstraintSet], Tolerance], list[Violation]]

ALL_RULES: tuple[Rule, ...] = (
    check_reference_integrity,
    check_assignment_wellformed,
    check_horizon,
    check_vehicle_status,
    check_temporal_consistency,
    check_turnaround,
    check_arrival_times,
    check_endurance,
    check_shift,
    check_water_capacity,
    check_water_source,
    check_night_flight,
    check_hard_constraints,
)
