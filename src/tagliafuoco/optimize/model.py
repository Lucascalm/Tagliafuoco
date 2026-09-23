"""Costruzione del modello CP-SAT che assegna i mezzi ai focolai.

Cos'e' un modello CP-SAT, spiegato una volta sola
-------------------------------------------------
CP-SAT e' il risolutore di programmazione a vincoli di OR-Tools. Il suo motore
e' un SAT solver (lo stesso tipo di macchina che decide la soddisfacibilita'
di formule booleane) esteso con propagatori per i vincoli interi. Si lavora in
tre passi:

1. si dichiarano delle VARIABILI, ognuna con un dominio finito di interi
   (`assign` sta in {0,1}, `arrival` sta in [0, 2400], e cosi' via);
2. si dichiarano dei VINCOLI, cioe' relazioni che le variabili devono
   rispettare (questa somma non supera quel numero, questi due intervalli non
   si sovrappongono);
3. si dichiara una FUNZIONE OBIETTIVO da massimizzare o minimizzare.

Poi il risolutore cerca. La parte interessante e' che non enumera: ogni volta
che fissa una variabile, i propagatori restringono i domini delle altre
(propagazione dei vincoli), e quando un dominio si svuota il ramo e' morto e
si torna indietro imparando una clausola che impedisce di ricadere nello
stesso errore. Per questo conviene consegnargli domini gia' stretti: il limite
superiore di `sorties` lo calcoliamo noi dall'autonomia invece di lasciarglielo
scoprire, e le coppie mezzo-focolaio impossibili non ricevono nemmeno una
variabile.

Perche' CP-SAT e non un risolutore lineare continuo: qui quasi tutto e'
discreto e logico. Il numero di lanci e' un intero, l'assegnazione e' un si' o
un no, "gli interventi di questo mezzo non si sovrappongono" e' una disgiunzione
e non una disuguaglianza. Un risolutore lineare la esprime solo a colpi di
"big-M", cioe' costanti grandi che rendono il rilassamento continuo slabbrato e
la ricerca lenta. CP-SAT ha quei costrutti come cittadini di prima classe.

Perche' variabili intervallo opzionali e NoOverlap
---------------------------------------------------
Un mezzo puo' intervenire su piu' focolai nell'orizzonte, ma non nello stesso
momento: deve finire una missione e rientrare prima di cominciarne un'altra.
Scritto a mano diventerebbe, per ogni coppia di missioni dello stesso mezzo,
una disgiunzione "o la prima finisce prima che inizi la seconda, o viceversa",
con una variabile booleana di ordinamento in piu' per ogni coppia. Sono
O(n^2) vincoli deboli.

CP-SAT ha il costrutto giusto: `NewOptionalIntervalVar(start, size, end,
is_present)` crea un intervallo che esiste solo se il letterale `is_present` e'
vero, e `AddNoOverlap` su una lista di intervalli attiva un propagatore
specializzato (edge finding, disgiuntivo) che ragiona su tutto l'insieme
insieme. Il letterale di presenza e' esattamente la nostra `assign[v][f]`: se
il mezzo non e' assegnato a quel focolaio l'intervallo semplicemente non c'e' e
non occupa la macchina. E' la stessa struttura che si usa nello scheduling di
officina, dove ogni mezzo e' una macchina e ogni missione un job opzionale.

Perche' il problema e' NP-difficile
------------------------------------
Il nucleo del problema contiene, come casi particolari, due problemi classici.
Se si ignora il tempo e si guarda solo "quali mezzi mandare per coprire il
fabbisogno d'acqua di ogni focolaio al minimo costo", si ottiene un set
covering (o, nella versione con capacita', un bin packing generalizzato):
NP-difficile. Se si fissano le assegnazioni e si guarda solo "in che ordine
questo mezzo fa le sue missioni senza sovrapporle", si ottiene uno scheduling
disgiuntivo con tempi di trasferimento, parente del job shop: NP-difficile
anche quello. Noi li risolviamo insieme.

La conseguenza pratica non e' "impossibile", e' "senza garanzia di tempo". Il
solver gira con un limite di tempo (`SOLVER_MAX_SECONDS`) e se non chiude la
prova di ottimalita' restituisce la migliore soluzione trovata, con stato
FEASIBLE invece che OPTIMAL. Per un supporto alle decisioni va bene: un piano
buono adesso vale piu' di un piano ottimo fra dieci minuti.

Fattore di scala
----------------
Tutti i tempi sono interi in decimi di minuto (`geometry.TIME_SCALE = 10`);
tutti i volumi sono interi in litri, senza scala. La conversione e
l'arrotondamento stanno tutti in `geometry.py`, in un posto solo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

from ortools.sat.python import cp_model

from tagliafuoco.config import (
    LATE_RESPONSE_MINUTES,
    OBJECTIVE_WEIGHTS,
)
from tagliafuoco.domain.models import (
    Domain,
    FireCluster,
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
    ZoneKind,
)
from tagliafuoco.optimize.geometry import (
    MissionProfile,
    build_mission_profile,
    daily_window_to_plan_minutes,
    haversine_km,
    to_units_down,
    to_units_up,
)

# --------------------------------------------------------------------------
# Tarature locali del costruttore del modello
# --------------------------------------------------------------------------

# Quanti focolai puo' servire lo stesso mezzo nell'orizzonte. Due e' il valore
# di default: con un orizzonte di quattro ore e missioni da un'ora e mezza
# abbondante, tre sarebbero quasi sempre irrealizzabili e allargherebbero il
# modello per niente. E' un parametro, non una costante fisica.
DEFAULT_MAX_FIRES_PER_VEHICLE = 2

# Stati dai quali un mezzo puo' essere impiegato. RESERVE, MAINTENANCE e
# OUT_OF_SHIFT restano nello scenario (la demo li mostra fermi a terra) ma non
# entrano nel modello: sono decisioni gia' prese fuori dal solver.
USABLE_STATUSES = (VehicleStatus.AVAILABLE, VehicleStatus.ASSIGNED)

# Vincoli che agiscono SOLTANTO sui pesi dell'obiettivo. Non vietano niente,
# non impongono niente e non possono rendere infattibile nessun piano: al
# massimo spostano il compromesso fra criteri.
#
# Serve saperlo in due punti. Il primo e' il rilassamento: questi tipi non
# hanno niente da rilassare, quindi non vanno contati fra i motivi per cui
# vale la pena spendere una seconda risoluzione, e soprattutto non vanno
# elencati nel piano fra gli "ordini violati consapevolmente", che sarebbe una
# frase falsa su un ordine che nessuno puo' violare. Il secondo e' la
# coerenza con il validatore indipendente, che per la stessa ragione non
# verifica questi tre tipi: su un singolo piano non esiste un criterio per
# dire se un peso e' stato rispettato, e fingere di controllarlo darebbe una
# falsa sicurezza.
#
# Lo schema permette di marcarli HARD (il campo `enforcement` e' comune a
# tutti i vincoli) anche se il loro docstring dice che sono morbidi per
# costruzione: e' proprio quel caso limite che questa costante copre.
WEIGHT_ONLY_KINDS = (
    ConstraintKind.OBJECTIVE_WEIGHT,
    ConstraintKind.PRIORITIZE_ZONE,
    ConstraintKind.PROTECT_INFRASTRUCTURE,
)

# Pavimento della priorita' di un focolaio. Se l'arricchimento non ha girato,
# `base_priority` vale 0 e tutti i termini pesati sulla priorita' si
# annullerebbero, obiettivo compreso. Con un pavimento il focolaio conta poco
# ma conta.
MIN_PRIORITY = 0.05

# Soglia di risposta tardiva, gia' convertita in unita' intere di tempo.
#
# Sta qui, calcolata una volta sola, perche' serve in due posti che devono
# concordare: `_build_pair`, che definisce la variabile `lateness` come lo
# sforamento oltre questa soglia, e `objective._build_terms`, che normalizza
# il termine di ritardo sullo spazio che resta fra la soglia e la fine
# dell'orizzonte. Se i due la arrotondassero in direzioni diverse (una per
# eccesso e l'altra per difetto) il denominatore dell'obiettivo non
# corrisponderebbe piu' al massimo valore che la variabile puo' assumere, e il
# termine di ritardo potrebbe superare il proprio peso senza che nulla si
# rompa in modo visibile. Con un valore intero in `config` le due direzioni
# coincidono, il che rende l'errore ancora piu' facile da non vedere: e'
# proprio per questo che la soglia viene definita una volta sola.
#
# Direzione scelta: per ECCESSO, come tutte le durate (vedi il docstring di
# `geometry`). Una soglia arrotondata in su e' una soglia leggermente piu'
# indulgente, cioe' il solver non si attribuisce ritardi che non ha.
LATE_THRESHOLD_UNITS = to_units_up(LATE_RESPONSE_MINUTES)


# --------------------------------------------------------------------------
# Strutture di appoggio
# --------------------------------------------------------------------------


@dataclass
class PenaltyTerm:
    """Una penalita' che finira' nella funzione obiettivo.

    I vincoli morbidi non diventano vincoli veri: diventano un addendo
    negativo nell'obiettivo. Per poterli confrontare con i termini di merito
    (copertura, acqua, costo) servono due informazioni: quanto pesa una
    violazione piena (`unit_weight`, sulla stessa scala dei pesi di
    `OBJECTIVE_WEIGHTS`) e qual e' il valore massimo che l'espressione puo'
    assumere (`max_value`), cosi' `objective.py` puo' normalizzare.
    """

    label: str
    constraint_id: str
    expr: Any
    unit_weight: float
    max_value: int


@dataclass
class PairVars:
    """Le variabili di una coppia mezzo-focolaio praticabile.

    Una coppia impraticabile (mezzo senz'acqua, missione piu' lunga
    dell'autonomia, aereo di notte) non ha nessuna di queste variabili: non
    esiste proprio nel modello.
    """

    vehicle_id: str
    fire_id: str
    profile: MissionProfile

    assign: Any  # BoolVar: il mezzo interviene su questo focolaio
    sorties: Any  # IntVar: numero di cicli pescaggio-sgancio
    departure: Any  # IntVar: minuto di partenza, in unita' di tempo
    arrival: Any  # IntVar: minuto di arrivo sul focolaio
    release: Any  # IntVar: rientro in base, il mezzo torna disponibile
    work: Any  # IntVar: durata totale dell'impegno (0 se non assegnato)
    lateness: Any  # IntVar: ritardo oltre LATE_RESPONSE_MINUTES
    # IntervalVar opzionale usata dal NoOverlap del mezzo. Attenzione: NON
    # coincide con la missione, dura `work + turnaround` perche' include il
    # tempo fermo in base che deve precedere la partenza successiva. Vedi
    # `_build_pair`.
    occupancy: Any


@dataclass
class ModelVars:
    """Tutto il modello in un oggetto solo, per poterlo passare in giro.

    Contiene anche i pesi dell'obiettivo e i moltiplicatori di priorita',
    perche' `apply_constraints` li modifica: un `PRIORITIZE_ZONE` non aggiunge
    vincoli, alza dei pesi, e un `OBJECTIVE_WEIGHT` riscrive direttamente un
    peso. Tenerli qui dentro evita di passare tre dizionari separati.
    """

    model: cp_model.CpModel
    scenario: Scenario
    horizon_units: int
    max_fires_per_vehicle: int

    pairs: dict[tuple[str, str], PairVars] = field(default_factory=dict)
    used: dict[str, Any] = field(default_factory=dict)
    covered: dict[str, Any] = field(default_factory=dict)
    delivered: dict[str, Any] = field(default_factory=dict)
    unmet_liters: dict[str, Any] = field(default_factory=dict)

    weights: dict[str, float] = field(default_factory=dict)
    priority_multiplier: dict[str, float] = field(default_factory=dict)
    penalties: list[PenaltyTerm] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def pairs_for_vehicle(self, vehicle_id: str) -> list[PairVars]:
        """Coppie di quel mezzo, in ordine alfabetico di focolaio.

        Il `sorted` non e' cosmetico. Queste liste decidono in che sequenza i
        vincoli entrano nel modello, e due modelli costruiti in sequenza
        diversa possono far scegliere al solver piani diversi a parita' di
        valore obiettivo. Ordinare sulla chiave `(vehicle_id, fire_id)`, che
        e' fatta di stringhe, rende quella sequenza indipendente
        dall'inserimento e quindi riproducibile.
        """
        return [p for (v, _f), p in sorted(self.pairs.items()) if v == vehicle_id]

    def pairs_for_fire(self, fire_id: str) -> list[PairVars]:
        """Coppie su quel focolaio, in ordine alfabetico di mezzo.

        Stesso motivo di `pairs_for_vehicle`: ordine stabile, piano
        riproducibile.
        """
        return [p for (_v, f), p in sorted(self.pairs.items()) if f == fire_id]

    def priority_of(self, fire: FireCluster) -> float:
        """Priorita' effettiva del focolaio: quella di base per il moltiplicatore."""
        base = max(MIN_PRIORITY, float(fire.base_priority))
        return base * self.priority_multiplier.get(fire.fire_id, 1.0)


@dataclass
class ConstraintApplication:
    """Esito della traduzione di un `ConstraintSet` in vincoli CP-SAT.

    Le tre liste di identificativi non sono ridondanti fra loro e vanno tenute
    distinte perche' rispondono a tre domande diverse del coordinatore.

    - `applied_ids`: quali ordini hanno davvero morso su questo scenario. E'
      la lista che `solver._relaxable_ids` usa per decidere se una seconda
      passata ha senso: rilassare un ordine che non e' mai entrato nel modello
      costerebbe una risoluzione intera per non cambiare niente.
    - `unsupported_ids`: quali parti dell'ordine il traduttore ha dichiarato
      fuori repertorio. Non sono un errore, sono un'astensione, e il
      coordinatore deve vederle per sapere cosa NON e' stato applicato.
    - `relaxed_ids`: quali ordini duri sono stati declassati a penalita' per
      ottenere un piano. E' l'unica lista che ammette una violazione
      consapevole, quindi e' anche l'unica che va scritta nel piano in modo
      esplicito.

    `notes` raccoglie le righe gia' pronte per l'utente. Sta qui e non in
    `ModelVars.notes` perche' riguarda la traduzione dell'ordine, non la
    costruzione del modello: quando il solver ricostruisce tutto per la
    seconda passata, queste note vanno rifatte e quelle del modello no.
    """

    applied_ids: list[str] = field(default_factory=list)
    unsupported_ids: list[str] = field(default_factory=list)
    relaxed_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Risoluzione dei selettori
# --------------------------------------------------------------------------
#
# Il resolver ufficiale e' `nlu/resolver.py` ed e' di proprieta' del
# sottosistema NLU. Qui lo si usa se c'e' e se espone una funzione
# riconoscibile; altrimenti si ricade su una risoluzione locale scritta
# direttamente sullo schema di `Selector`, che e' chiuso e quindi non lascia
# margini di interpretazione. Il ripiego serve perche' i sottosistemi vengono
# scritti in parallelo e `optimize/` non puo' restare bloccato in attesa.


_VEHICLE_RESOLVER_NAMES = (
    "resolve_vehicles",
    "resolve_vehicle_selector",
    "select_vehicles",
    "vehicles_for_selector",
)
_FIRE_RESOLVER_NAMES = (
    "resolve_fires",
    "resolve_fire_selector",
    "select_fires",
    "fires_for_selector",
)


def _nlu_resolver() -> Any:
    try:  # pragma: no cover - dipende da quale sottosistema e' gia' scritto
        from tagliafuoco.nlu import resolver as _resolver

        return _resolver
    except Exception:
        return None


def _ids_from(result: Any, attribute: str) -> Optional[list[str]]:
    """Normalizza il risultato del resolver in una lista di identificativi.

    Restituire None significa "non ho capito questo risultato, usa il ripiego
    locale". Succede in due casi, e il secondo e' il piu' insidioso.

    Il primo: gli elementi non sono ne' stringhe ne' oggetti con l'attributo
    giusto, quindi non c'e' niente da leggere.

    Il secondo: il risultato e' un insieme. Un `set` si itera in un ordine che
    dipende dagli hash, quindi due esecuzioni possono restituire gli stessi
    identificativi in sequenza diversa. Da qui quella sequenza determina
    l'ordine in cui i vincoli entrano nel modello, e un modello costruito in
    ordine diverso puo' far scegliere al solver un piano diverso a parita' di
    valore obiettivo. Sarebbe non determinismo introdotto da un dettaglio
    invisibile, e la valutazione contro la baseline non lo tollera: meglio il
    ripiego locale, che scorre lo scenario e quindi e' ordinato per
    costruzione.
    """
    if result is None:
        return None
    if isinstance(result, (set, frozenset)):
        return None
    out: list[str] = []
    try:
        for item in result:
            if isinstance(item, str):
                out.append(item)
            elif hasattr(item, attribute):
                out.append(str(getattr(item, attribute)))
            else:
                return None
    except TypeError:
        return None
    return out


def _try_external_resolver(
    names: Sequence[str], selector: Selector, scenario: Scenario, attribute: str
) -> Optional[list[str]]:
    module = _nlu_resolver()
    if module is None:
        return None
    for name in names:
        fn = getattr(module, name, None)
        if not callable(fn):
            continue
        for args in ((selector, scenario), (scenario, selector)):
            try:
                return _ids_from(fn(*args), attribute)
            except TypeError:
                continue
            except Exception:
                return None
    return None


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def _wanted_true(values: Iterable[str]) -> bool:
    """Interpreta i valori booleani di un selettore ("true", "si", "1", ...).

    I campi booleani dello schema (`FIRE_IS_WUI`, `FIRE_IN_PROTECTED_AREA`)
    arrivano come stringhe, perche' `Selector.values` e' una lista di stringhe
    per tutti i campi e non si e' voluto un tipo speciale per due casi.

    Lista vuota significa vero, e la convenzione va difesa: il validatore di
    `Selector` impone almeno un valore per ogni campo diverso da `ALL`, quindi
    una lista vuota non puo' arrivare da una traduzione valida. Se ci arriva,
    viene da un `Selector` costruito a mano nel codice, e la lettura utile e'
    "i focolai in interfaccia urbano-foresta", cioe' il caso affermativo, non
    "nessun focolaio", che sarebbe un selettore senza scopo.

    Un valore non riconosciuto ("forse") vale falso: fra inventare un vero e
    restringere la selezione, la seconda e' la risposta prudente, perche' un
    vincolo che morde meno del dovuto lascia che sia il validatore a dirlo,
    mentre uno che morde di piu' toglie al coordinatore mezzi che aveva.
    """
    truthy = {"true", "1", "si", "si'", "yes", "y", "vero"}
    vals = [_norm(v) for v in values]
    if not vals:
        return True
    return any(v in truthy for v in vals)


def resolve_vehicle_ids(selector: Optional[Selector], scenario: Scenario) -> list[str]:
    """Identificativi dei mezzi selezionati, nell'ordine dello scenario.

    Tre cose da sapere, tutte e tre deliberate.

    `None` non vuol dire "nessuno", vuol dire "non ho ristretto niente": lo
    usa `MIN_VEHICLES_ON_FIRE`, dove il selettore dei mezzi e' opzionale e
    assente significa "due mezzi qualsiasi".

    L'ordine e' quello dello scenario, che e' una lista. Non e' un dettaglio
    estetico: da questa sequenza discende l'ordine in cui i vincoli entrano
    nel modello, e un modello costruito in sequenza diversa puo' far scegliere
    al solver un piano diverso a parita' di valore obiettivo.

    Il risultato del resolver esterno viene RIFILTRATO contro lo scenario.
    Serve a due cose: proteggere da un resolver che restituisse
    identificativi di una finestra precedente (nel replay della stagione lo
    scenario cambia a ogni finestra) e garantire che l'ordine finale sia
    sempre quello dello scenario, qualunque ordine usi il resolver.
    """
    if selector is None:
        return [v.vehicle_id for v in scenario.vehicles]

    external = _try_external_resolver(
        _VEHICLE_RESOLVER_NAMES, selector, scenario, "vehicle_id"
    )
    if external is not None:
        # Si scorre lo SCENARIO e si tiene chi il resolver ha nominato, invece
        # di scorrere la risposta del resolver. Il risultato e' lo stesso
        # insieme, ma nell'ordine dello scenario e senza ripetizioni, e
        # l'ordine e' proprio cio' che qui conta: cosi' il resolver ufficiale
        # e il ripiego locale non possono divergere sulla sequenza nemmeno se
        # un giorno `nlu/resolver.py` cambiasse il proprio modo di iterare.
        # Filtrare e basta avrebbe conservato l'ordine del resolver, cioe'
        # avrebbe legato la riproducibilita' del piano a un dettaglio di un
        # altro sottosistema.
        wanted = set(external)
        return [v.vehicle_id for v in scenario.vehicles if v.vehicle_id in wanted]

    values = [_norm(v) for v in selector.values]
    out: list[str] = []
    for v in scenario.vehicles:
        if selector.field is SelectorField.ALL:
            out.append(v.vehicle_id)
        elif selector.field is SelectorField.VEHICLE_ID and _norm(v.vehicle_id) in values:
            out.append(v.vehicle_id)
        elif selector.field is SelectorField.VEHICLE_KIND and _norm(v.kind.value) in values:
            out.append(v.vehicle_id)
        elif (
            selector.field is SelectorField.VEHICLE_DOMAIN
            and _norm(v.domain.value) in values
        ):
            out.append(v.vehicle_id)
        elif selector.field is SelectorField.BASE_ID and _norm(v.home_base_id) in values:
            out.append(v.vehicle_id)
    return out


def resolve_fire_ids(selector: Optional[Selector], scenario: Scenario) -> list[str]:
    """Identificativi dei focolai selezionati, nell'ordine dello scenario.

    Valgono le tre note scritte in `resolve_vehicle_ids`: `None` significa
    "tutti", l'ordine e' quello dello scenario perche' da li' discende
    l'ordine di costruzione del modello, e il risultato del resolver esterno
    viene riportato nell'ordine dello scenario invece di essere solo filtrato.
    """
    if selector is None:
        return [f.fire_id for f in scenario.fires]

    external = _try_external_resolver(
        _FIRE_RESOLVER_NAMES, selector, scenario, "fire_id"
    )
    if external is not None:
        wanted = set(external)
        return [f.fire_id for f in scenario.fires if f.fire_id in wanted]

    values = [_norm(v) for v in selector.values]
    want_true = _wanted_true(selector.values)
    out: list[str] = []
    for f in scenario.fires:
        if selector.field is SelectorField.ALL:
            out.append(f.fire_id)
        elif selector.field is SelectorField.FIRE_ID and _norm(f.fire_id) in values:
            out.append(f.fire_id)
        elif selector.field is SelectorField.FIRE_COMUNE and _norm(f.comune) in values:
            out.append(f.fire_id)
        elif (
            selector.field is SelectorField.FIRE_PROVINCIA
            and _norm(f.provincia) in values
        ):
            out.append(f.fire_id)
        elif (
            selector.field is SelectorField.FIRE_SEVERITY
            and _norm(f.severity.value) in values
        ):
            out.append(f.fire_id)
        elif selector.field is SelectorField.FIRE_IS_WUI and f.is_wui is want_true:
            out.append(f.fire_id)
        elif (
            selector.field is SelectorField.FIRE_IN_PROTECTED_AREA
            and f.in_protected_area is want_true
        ):
            out.append(f.fire_id)
    return out


# --------------------------------------------------------------------------
# Costruzione del modello
# --------------------------------------------------------------------------


def vehicle_is_employable(vehicle: Vehicle, scenario: Scenario) -> bool:
    """Filtro preliminare sui mezzi, prima ancora di guardare i focolai.

    Sono condizioni che non dipendono dal focolaio, quindi conviene applicarle
    una volta sola invece che per ogni coppia.
    """
    if vehicle.status not in USABLE_STATUSES:
        return False
    if vehicle.domain is Domain.AIR:
        # Di notte i mezzi aerei non volano. Fanno eccezione quelli dichiarati
        # abilitati al volo notturno, e solo se la loro base lo e' a sua volta:
        # senza illuminazione a terra non si decolla comunque.
        if scenario.is_night and not vehicle.night_capable:
            return False
        if scenario.is_night:
            base = scenario.base_by_id(vehicle.home_base_id)
            if base is None or not base.has_night_capability:
                return False
        # Oltre il vento massimo dichiarato il mezzo aereo resta a terra.
        if scenario.wind_kmh > vehicle.max_wind_kmh:
            return False
    return True


def build_model(
    scenario: Scenario,
    *,
    max_fires_per_vehicle: int = DEFAULT_MAX_FIRES_PER_VEHICLE,
) -> ModelVars:
    """Costruisce il modello con i soli vincoli STRUTTURALI.

    Strutturali vuol dire: fisica e regole operative che valgono sempre, a
    prescindere dall'ordine del coordinatore. Autonomia, turno, non
    sovrapposizione, volo notturno, disponibilita' dell'acqua. Questi vincoli
    non si rilassano mai: se il piano e' infattibile per colpa loro, il
    problema e' che i mezzi non bastano, non che l'ordine era contraddittorio.

    I vincoli che vengono dal traduttore si aggiungono dopo, con
    `apply_constraints`, e quelli si' possono essere rilassati.
    """
    model = cp_model.CpModel()
    # Il `max(0, ...)` non e' decorativo: `horizon_units` diventa il limite
    # superiore del dominio di quasi tutte le variabili di tempo, e un
    # orizzonte negativo (dato sporco, finestra di rigiocata calcolata male)
    # produrrebbe domini vuoti e un modello invalido invece di un piano vuoto
    # con le note. Il caso degenere deve degradare, non esplodere.
    horizon_units = max(0, to_units_down(scenario.horizon_minutes))

    mv = ModelVars(
        model=model,
        scenario=scenario,
        horizon_units=horizon_units,
        max_fires_per_vehicle=max(1, int(max_fires_per_vehicle)),
        weights=dict(OBJECTIVE_WEIGHTS),
        priority_multiplier={f.fire_id: 1.0 for f in scenario.fires},
    )

    _warn_duplicated_ids(mv)

    employable = [v for v in scenario.vehicles if vehicle_is_employable(v, scenario)]
    skipped = len(scenario.vehicles) - len(employable)
    if skipped:
        mv.notes.append(
            f"{skipped} mezzi esclusi a monte (stato non disponibile, volo "
            f"notturno o vento oltre il limite)."
        )

    # ---- variabili per ogni coppia praticabile --------------------------
    # L'ordine di iterazione e' quello dello scenario, che e' una lista: il
    # modello viene quindi costruito sempre nella stessa sequenza e le
    # variabili ricevono sempre gli stessi nomi. Iterare su un insieme o su un
    # dizionario costruito altrove sarebbe bastato a rendere il piano non
    # riproducibile, ed e' esattamente il tipo di non determinismo che la
    # valutazione contro la baseline non puo' permettersi.
    without_pairs: list[str] = []
    for vehicle in employable:
        base = scenario.base_by_id(vehicle.home_base_id)
        created = 0
        for fire in scenario.fires:
            profile = build_mission_profile(
                vehicle, base, fire, scenario.water_sources, scenario.horizon_minutes
            )
            if profile is None:
                continue
            created += 1
            mv.pairs[(vehicle.vehicle_id, fire.fire_id)] = _build_pair(
                mv, vehicle, fire, profile
            )
        if created == 0 and scenario.fires:
            without_pairs.append(vehicle.vehicle_id)

    if without_pairs:
        # Diagnostica, non un vincolo: un mezzo impiegabile che non compare in
        # nessuna assegnazione e' indistinguibile, a valle, da un mezzo che il
        # solver ha scelto di non usare. Dirlo qui risparmia mezz'ora di
        # caccia a chi legge il piano.
        mv.notes.append(
            "Mezzi impiegabili ma senza nessuna missione praticabile "
            f"({len(without_pairs)}): {', '.join(without_pairs)}. Cause "
            "possibili: nessuna sorgente d'acqua utilizzabile, focolaio fuori "
            "portata di autonomia, turno o orizzonte, mezzo non collocabile."
        )

    if not mv.pairs:
        mv.notes.append(
            "Nessuna coppia mezzo-focolaio praticabile: nessun mezzo puo' "
            "raggiungere un focolaio entro autonomia, turno e orizzonte."
        )

    _build_vehicle_constraints(mv, employable)
    _build_fire_constraints(mv)
    return mv


def _repeated(ids: Iterable[str]) -> list[str]:
    """Identificativi che compaiono piu' di una volta, in ordine di prima apparizione.

    L'ordine di apparizione, e non quello alfabetico, perche' la nota che ne
    esce finisce nel piano e il piano deve essere confrontabile riga per riga
    fra due esecuzioni. Qui basterebbe `sorted(set(...))`, ma la regola di
    questo sottosistema e' che nessun elenco destinato al piano dipenda
    dall'ordine di iterazione di un insieme, e le eccezioni a memoria sono
    proprio il modo in cui il non determinismo rientra dalla finestra.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in ids:
        if value in seen and value not in out:
            out.append(value)
        seen.add(value)
    return out


def _warn_duplicated_ids(mv: ModelVars) -> None:
    """Segnala gli identificativi ripetuti nello scenario.

    Perche' vale la pena spendere dieci righe su un caso che "non dovrebbe
    succedere". Le chiavi di questo modello sono `(vehicle_id, fire_id)`, e i
    dizionari per focolaio (`covered`, `delivered`, `unmet_liters`) sono
    indicizzati sul solo `fire_id`. Due focolai con lo stesso identificativo
    non fanno fallire niente: si sovrascrivono a vicenda, il modello resta ben
    formato, il solver risponde OPTIMAL e il piano copre un focolaio solo
    dichiarando di averne coperti due. E' esattamente la classe di errore che
    non si scopre mai leggendo un piano plausibile.

    Qui non si corregge il dato, perche' gli identificativi li assegna
    `ingest/` e non e' questo il posto per rattopparli: si scrive nelle note,
    cosi' chi legge il piano sa che quei numeri non valgono. Il validatore
    indipendente, che ricalcola per focolaio, arrivera' alla stessa
    conclusione per conto suo.
    """
    fires = _repeated(f.fire_id for f in mv.scenario.fires)
    if fires:
        mv.notes.append(
            "Identificativi di focolaio ripetuti nello scenario "
            f"({', '.join(fires)}): il modello ne tiene uno solo per "
            "identificativo, quindi copertura e litri di quei focolai non "
            "sono attendibili. Il difetto sta nei dati, non nel piano."
        )
    vehicles = _repeated(v.vehicle_id for v in mv.scenario.vehicles)
    if vehicles:
        mv.notes.append(
            "Identificativi di mezzo ripetuti nello scenario "
            f"({', '.join(vehicles)}): le missioni di quei mezzi possono "
            "sovrapporsi senza che il modello se ne accorga, perche' la non "
            "sovrapposizione si impone per identificativo."
        )


def _build_pair(
    mv: ModelVars,
    vehicle: Vehicle,
    fire: FireCluster,
    profile: MissionProfile,
) -> PairVars:
    """Crea variabili e vincoli di una singola coppia mezzo-focolaio."""
    model = mv.model
    H = mv.horizon_units
    tag = f"{vehicle.vehicle_id}_{fire.fire_id}"

    assign = model.NewBoolVar(f"assign_{tag}")
    sorties = model.NewIntVar(0, profile.max_sorties, f"sorties_{tag}")
    departure = model.NewIntVar(0, H, f"dep_{tag}")
    arrival = model.NewIntVar(0, H, f"arr_{tag}")
    release = model.NewIntVar(0, H, f"rel_{tag}")

    max_work = profile.fixed_units + profile.max_sorties * profile.cycle_units
    work = model.NewIntVar(0, min(max_work, H), f"work_{tag}")
    lateness = model.NewIntVar(0, H, f"late_{tag}")

    # Legame fra assegnazione e numero di cicli: se il mezzo interviene fa
    # almeno un lancio, se non interviene non ne fa nessuno. Senza questo
    # legame il solver potrebbe "assegnare" un mezzo che non versa acqua.
    model.Add(sorties >= 1).OnlyEnforceIf(assign)
    model.Add(sorties == 0).OnlyEnforceIf(assign.Not())

    # Catena dei tempi, imposta solo quando l'assegnazione c'e'. E' il modo
    # idiomatico in CP-SAT: OnlyEnforceIf rende il vincolo condizionato a un
    # letterale, senza bisogno di costanti big-M.
    model.Add(departure >= profile.earliest_start_units).OnlyEnforceIf(assign)
    model.Add(
        arrival == departure + profile.setup_units + profile.outbound_units
    ).OnlyEnforceIf(assign)
    model.Add(
        release
        == arrival + profile.cycle_units * sorties + profile.return_units
    ).OnlyEnforceIf(assign)
    model.Add(work == release - departure).OnlyEnforceIf(assign)

    # Quando l'assegnazione non c'e' i tempi non significano niente. Fissarli a
    # zero elimina un mare di soluzioni equivalenti che rallenterebbero la
    # ricerca, e rende univoca la lettura del piano a fine corsa.
    model.Add(departure == 0).OnlyEnforceIf(assign.Not())
    model.Add(arrival == 0).OnlyEnforceIf(assign.Not())
    model.Add(release == 0).OnlyEnforceIf(assign.Not())
    model.Add(work == 0).OnlyEnforceIf(assign.Not())

    # Autonomia della singola missione. E' gia' implicita nel limite superiore
    # di `sorties`, ma scriverla esplicitamente costa niente e dichiara
    # l'intenzione a chi legge il modello.
    model.Add(work <= to_units_down(vehicle.endurance_minutes))

    # Ritardo oltre la soglia di risposta tardiva. Serve all'obiettivo: e'
    # scritto con >= perche' l'obiettivo lo minimizza, quindi il solver lo
    # schiaccia da solo sul suo minimo.
    model.Add(lateness >= arrival - LATE_THRESHOLD_UNITS).OnlyEnforceIf(assign)
    model.Add(lateness == 0).OnlyEnforceIf(assign.Not())

    # L'intervallo opzionale: esiste solo se `assign` e' vero. E' cio' che il
    # propagatore di NoOverlap manipolera' per sequenziare le missioni.
    #
    # L'intervallo NON finisce al rientro, finisce al rientro PIU' il tempo di
    # riattrezzamento della base. SPEC.md sezione 6 punto 2 chiede che valga
    #
    #     departure(missione k+1) >= release(missione k) + turnaround_minutes
    #
    # e un NoOverlap su intervalli che finiscono al rientro garantisce solo il
    # ">= release", cioe' permette al mezzo di ripartire nell'istante stesso
    # in cui atterra. Allungando l'intervallo della costante di
    # riattrezzamento il vincolo giusto si ottiene senza aggiungere ne'
    # variabili ne' disgiunzioni: e' il propagatore disgiuntivo, che gia'
    # c'era, a farlo rispettare.
    #
    # Il tempo di riattrezzamento resta fuori da `work`, e questo e' il punto
    # da saper difendere: `work` e' l'impegno del mezzo e finisce in tre
    # posti (autonomia della missione, somma del turno, costo
    # dell'assegnazione). Un mezzo fermo in base non consuma autonomia e non
    # costa ore di volo, quindi contarlo li' gonfierebbe tre grandezze che il
    # validatore ricalcola per conto suo, e il piano verrebbe respinto per
    # eccesso invece che per difetto.
    #
    # Il termine finale puo' superare l'orizzonte (un mezzo che rientra
    # all'ultimo minuto si riattrezza dopo la fine del periodo pianificato),
    # ed e' corretto che sia cosi': il vincolo di orizzonte riguarda il
    # rientro, non il riattrezzamento successivo.
    occupancy = model.NewOptionalIntervalVar(
        departure,
        work + profile.turnaround_units,
        release + profile.turnaround_units,
        assign,
        f"occ_{tag}",
    )

    return PairVars(
        vehicle_id=vehicle.vehicle_id,
        fire_id=fire.fire_id,
        profile=profile,
        assign=assign,
        sorties=sorties,
        departure=departure,
        arrival=arrival,
        release=release,
        work=work,
        lateness=lateness,
        occupancy=occupancy,
    )


def _build_vehicle_constraints(mv: ModelVars, employable: Sequence[Vehicle]) -> None:
    """Vincoli che riguardano un mezzo alla volta."""
    model = mv.model
    for vehicle in employable:
        pairs = mv.pairs_for_vehicle(vehicle.vehicle_id)
        used = model.NewBoolVar(f"used_{vehicle.vehicle_id}")
        mv.used[vehicle.vehicle_id] = used

        if not pairs:
            model.Add(used == 0)
            continue

        assigns = [p.assign for p in pairs]
        # `used` e' vero se e solo se il mezzo e' assegnato da qualche parte.
        # Serve per il costo fisso di attivazione e per le quote di tipo.
        model.AddMaxEquality(used, assigns)

        # Al piu' N focolai nell'orizzonte.
        model.Add(sum(assigns) <= mv.max_fires_per_vehicle)

        # Turno: la somma degli impegni della giornata non supera il residuo.
        shift_left = to_units_down(
            max(0.0, float(vehicle.max_shift_minutes) - float(vehicle.minutes_flown_today))
        )
        model.Add(sum(p.work for p in pairs) <= shift_left)

        # Gli interventi dello stesso mezzo non si sovrappongono nel tempo, e
        # fra l'uno e l'altro ci sta il riattrezzamento della base: lo
        # garantisce la durata degli intervalli, vedi `_build_pair`.
        model.AddNoOverlap([p.occupancy for p in pairs])


def _build_fire_constraints(mv: ModelVars) -> None:
    """Variabili e vincoli che riguardano un focolaio alla volta.

    Qui si decide cosa vuol dire "coperto", ed e' una definizione severa:
    `covered[f]` e' vero solo quando l'acqua erogata raggiunge l'INTERO
    fabbisogno del focolaio. Mezza copertura non conta nulla per questo
    termine.

    La conseguenza va saputa prima di leggere i numeri di un piano. Con
    fabbisogni realistici (decine di migliaia di litri) e una flotta piccola
    su un orizzonte di quattro ore, la copertura piena puo' essere
    irraggiungibile per ogni focolaio: in quello scenario i termini
    `coverage` e `wui_protection` dell'obiettivo valgono zero per costruzione,
    e a guidare il piano restano il termine idrico (che da' credito parziale,
    litro per litro) e quelli di costo e di ritardo. Il valore dell'obiettivo
    risulta allora negativo, e non e' un errore.

    Perche' tenerla severa invece di premiare la copertura parziale due volte:
    `covered` e' lo specchio esatto di `PlanMetrics.fires_covered`, che conta i
    focolai spenti, non quelli bagnati. Se qui si ammorbidisse la definizione,
    il solver ottimizzerebbe una grandezza diversa da quella che la
    valutazione poi misura, e il confronto con la baseline perderebbe senso.
    Il credito parziale esiste gia' ed e' il termine idrico.
    """
    model = mv.model
    for fire in mv.scenario.fires:
        pairs = mv.pairs_for_fire(fire.fire_id)
        demand = max(0, int(fire.water_demand_liters))

        upper = sum(p.profile.liters_per_sortie * p.profile.max_sorties for p in pairs)
        delivered = model.NewIntVar(0, upper, f"delivered_{fire.fire_id}")
        if pairs:
            model.Add(
                delivered
                == sum(p.profile.liters_per_sortie * p.sorties for p in pairs)
            )
        else:
            model.Add(delivered == 0)
        mv.delivered[fire.fire_id] = delivered

        covered = model.NewBoolVar(f"covered_{fire.fire_id}")
        mv.covered[fire.fire_id] = covered

        unmet = model.NewIntVar(0, demand, f"unmet_{fire.fire_id}")
        mv.unmet_liters[fire.fire_id] = unmet

        if demand > 0:
            # Acqua non erogata = fabbisogno meno l'acqua erogata, senza
            # scendere sotto zero: versare piu' del necessario non e' un
            # merito, quindi il surplus viene tagliato con un minimo.
            capped = model.NewIntVar(0, demand, f"capped_{fire.fire_id}")
            model.AddMinEquality(capped, [delivered, model.NewConstant(demand)])
            model.Add(unmet == demand - capped)

            # Copertura: doppia implicazione scritta con due vincoli
            # condizionati. E' il modo canonico di reificare una disuguaglianza
            # in CP-SAT.
            model.Add(delivered >= demand).OnlyEnforceIf(covered)
            model.Add(delivered <= demand - 1).OnlyEnforceIf(covered.Not())
        else:
            # Fabbisogno nullo (dato non arricchito): il focolaio si considera
            # coperto quando almeno un mezzo ci interviene.
            model.Add(unmet == 0)
            if pairs:
                model.Add(sum(p.assign for p in pairs) >= 1).OnlyEnforceIf(covered)
                model.Add(sum(p.assign for p in pairs) == 0).OnlyEnforceIf(covered.Not())
            else:
                model.Add(covered == 0)


# --------------------------------------------------------------------------
# Traduzione dei vincoli del coordinatore
# --------------------------------------------------------------------------


def apply_constraints(
    model: cp_model.CpModel,
    mv: ModelVars,
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet],
    *,
    relax_hard: bool = False,
) -> ConstraintApplication:
    """Traduce ogni vincolo del `ConstraintSet` in vincoli CP-SAT o in pesi.

    Le tre strade possibili:

    - `HARD` diventa un vincolo vero, che rende il modello infattibile se
      violato. A meno che `relax_hard` sia vero: in quel caso diventa anch'esso
      una penalita', ma con un peso molto piu' alto di quello dei morbidi,
      cosi' il solver lo viola soltanto se non ha alternativa. E' la seconda
      passata del risolutore, quella che spiega perche' un ordine era
      contraddittorio invece di restituire il vuoto.
    - `SOFT` diventa un addendo negativo nell'obiettivo.
    - `UNSUPPORTED` non diventa niente, ma viene registrato nelle note del
      piano: il coordinatore deve vedere che quella parte dell'ordine non e'
      stata applicata.

    `model` e' lo stesso oggetto di `mv.model`: e' nella firma perche' la
    funzione e' pensata per essere leggibile da sola.
    """
    report = ConstraintApplication()
    if constraint_set is None or constraint_set.is_empty():
        return report

    from tagliafuoco.optimize.objective import (
        RELAXED_HARD_WEIGHT,
        SOFT_CONSTRAINT_WEIGHT,
    )

    for c in constraint_set.constraints:
        is_hard = c.enforcement is Enforcement.HARD
        relaxing = is_hard and relax_hard
        # Un vincolo duro rilassato pesa molto piu' di un morbido: e' un ordine
        # che stiamo violando, non una preferenza che stiamo trascurando.
        weight = RELAXED_HARD_WEIGHT if relaxing else SOFT_CONSTRAINT_WEIGHT
        weight *= max(0.05, float(c.confidence))
        as_constraint = is_hard and not relaxing

        # La funzione si ridefinisce a ogni giro perche' cattura `c` e
        # `weight`, che cambiano a ogni vincolo. E' il caso che i linter
        # segnalano come "variabile del ciclo catturata da una chiusura", e
        # qui e' innocuo per un motivo preciso: la chiusura viene invocata
        # dentro la stessa iterazione, da `_apply_one`, e non sopravvive al
        # giro. Se un giorno la si salvasse in una lista per chiamarla dopo,
        # tutte le copie vedrebbero l'ultimo vincolo e le penalita' finirebbero
        # attribuite all'ordine sbagliato.
        def penalise(expr: Any, max_value: int, label: str) -> None:
            # `max_value` e' il denominatore con cui `objective` normalizza la
            # penalita': a zero non c'e' niente da normalizzare (e la
            # divisione salterebbe), e soprattutto vuol dire che
            # l'espressione non puo' assumere nessun valore diverso da zero,
            # cioe' che quella penalita' non esiste.
            if max_value <= 0:
                return
            mv.penalties.append(
                PenaltyTerm(
                    label=label,
                    constraint_id=c.constraint_id,
                    expr=expr,
                    unit_weight=weight,
                    max_value=max_value,
                )
            )

        handled = _apply_one(
            mv, scenario, c, as_constraint=as_constraint, penalise=penalise
        )

        if c.kind is ConstraintKind.UNSUPPORTED:
            report.unsupported_ids.append(c.constraint_id)
            report.notes.append(
                f"[{c.kind.value}] {c.constraint_id}: non applicato. "
                f"{getattr(c, 'reason', '')}".strip()
            )
            continue

        if not handled:
            report.notes.append(
                f"[{c.kind.value}] {c.constraint_id}: non applicabile a questo "
                f"scenario (selettore vuoto o punto non localizzato)."
            )
            continue

        report.applied_ids.append(c.constraint_id)
        # Un vincolo che agisce solo sui pesi non e' stato "rilassato": e'
        # stato applicato esattamente come sarebbe stato applicato nella prima
        # passata, perche' non c'era niente da rilassare. Elencarlo fra gli
        # ordini violati consapevolmente sarebbe una nota falsa, e falsa
        # proprio nel momento in cui il coordinatore legge il piano per capire
        # quale parte del suo ordine e' caduta.
        if relaxing and c.kind not in WEIGHT_ONLY_KINDS:
            report.relaxed_ids.append(c.constraint_id)
            report.notes.append(
                f"[{c.kind.value}] {c.constraint_id}: rilassato da duro a "
                f"morbido per rendere il piano fattibile."
            )

    return report


def _apply_one(
    mv: ModelVars,
    scenario: Scenario,
    c: Any,
    *,
    as_constraint: bool,
    penalise: Callable[[Any, int, str], None],
) -> bool:
    """Applica un singolo vincolo. Ritorna False se non ha morso su nulla."""
    model = mv.model
    kind = c.kind

    # ---- vincoli che cambiano solo i pesi -------------------------------

    if kind is ConstraintKind.OBJECTIVE_WEIGHT:
        mv.weights[c.term.value] = float(c.weight)
        mv.notes.append(
            f"Peso del termine '{c.term.value}' portato a {c.weight} "
            f"dall'ordine del coordinatore."
        )
        return True

    if kind is ConstraintKind.PRIORITIZE_ZONE:
        fire_ids = _fires_in_zone(mv, scenario, c)
        if not fire_ids:
            return False
        for fid in fire_ids:
            mv.priority_multiplier[fid] = (
                mv.priority_multiplier.get(fid, 1.0) * float(c.weight_multiplier)
            )
        mv.notes.append(
            f"Priorita' moltiplicata per {c.weight_multiplier} su "
            f"{len(fire_ids)} focolai della zona richiesta."
        )
        return True

    if kind is ConstraintKind.PROTECT_INFRASTRUCTURE:
        if c.lat is None or c.lon is None:
            mv.notes.append(
                f"Infrastruttura '{c.name}' senza coordinate: impossibile "
                f"alzare la priorita' dei focolai vicini."
            )
            return False
        fire_ids = _fires_within(mv, scenario, c.lat, c.lon, c.buffer_km)
        if not fire_ids:
            return False
        for fid in fire_ids:
            mv.priority_multiplier[fid] = (
                mv.priority_multiplier.get(fid, 1.0) * float(c.weight_multiplier)
            )
        mv.notes.append(
            f"Difesa di '{c.name}': priorita' alzata su {len(fire_ids)} "
            f"focolai entro {c.buffer_km} km."
        )
        return True

    if kind is ConstraintKind.UNSUPPORTED:
        return True

    # ---- vincoli sui mezzi ----------------------------------------------

    if kind is ConstraintKind.EXCLUDE_VEHICLE:
        vehicle_ids = resolve_vehicle_ids(c.selector, scenario)
        if not vehicle_ids:
            return False
        # Un mezzo selezionato ma senza coppie praticabili non ha variabili da
        # azzerare: il vincolo e' comunque soddisfatto, perche' quel mezzo non
        # puo' essere usato in nessun caso. Quindi si ritorna True, non False:
        # l'ordine e' stato applicato, non e' rimasto inapplicabile.
        _forbid(
            mv,
            _pairs_of_vehicles(mv, vehicle_ids),
            as_constraint,
            penalise,
            "mezzo escluso",
        )
        return True

    if kind is ConstraintKind.RESERVE_VEHICLE:
        return _apply_reserve(mv, scenario, c, as_constraint, penalise)

    if kind is ConstraintKind.VEHICLE_TYPE_QUOTA:
        vehicle_ids = resolve_vehicle_ids(c.selector, scenario)
        if not vehicle_ids:
            return False
        used = [mv.used[v] for v in vehicle_ids if v in mv.used]
        total = sum(used) if used else 0
        if c.min_used is not None:
            if as_constraint:
                model.Add(total >= c.min_used)
            else:
                short = model.NewIntVar(0, c.min_used, f"quota_min_{c.constraint_id}")
                model.Add(short >= c.min_used - total)
                penalise(short, c.min_used, "quota minima di tipo")
        if c.max_used is not None:
            if as_constraint:
                model.Add(total <= c.max_used)
            else:
                over = model.NewIntVar(0, len(vehicle_ids), f"quota_max_{c.constraint_id}")
                model.Add(over >= total - c.max_used)
                penalise(over, len(vehicle_ids), "quota massima di tipo")
        return True

    if kind is ConstraintKind.TIME_WINDOW:
        vehicle_ids = resolve_vehicle_ids(c.selector, scenario)
        if not vehicle_ids:
            return False
        pairs = _pairs_of_vehicles(mv, vehicle_ids)
        # `from_minute` e `to_minute` sono ORARI (minuti dalla mezzanotte),
        # non minuti di piano: la conversione sta tutta in geometry.py, dove
        # ne e' spiegato il perche'. Leggerli come minuti di piano faceva
        # passare al solver piani che il validatore respinge come bloccanti,
        # perche' validatore, baseline e traduttore a regole li leggono tutti
        # e tre come orari.
        open_minutes, close_minutes = daily_window_to_plan_minutes(
            scenario.timestamp, c.from_minute, c.to_minute, scenario.horizon_minutes
        )
        # Apertura e chiusura delimitano un PERMESSO, cioe' un budget di
        # tempo: si stringe da tutte e due le parti (apertura per eccesso,
        # chiusura per difetto) cosi' il solver non si concede mai un minuto
        # fuori orario che il validatore poi gli contesta.
        lo = max(0, to_units_up(open_minutes))
        hi = to_units_down(close_minutes)
        # Quanto puo' valere al massimo lo sforamento della finestra. Lo
        # schema ammette orari fino a 1440 mentre l'orizzonte ne dura di
        # solito 240, quindi `lo` puo' essere molto piu' grande di
        # `horizon_units` e lo sforamento in entrata (`lo - departure`) puo'
        # superare l'orizzonte; una finestra gia' chiusa da' `hi` negativo e
        # lo sforamento in uscita (`release - hi`) supera anch'esso
        # l'orizzonte. Dimensionare la variabile di penalita' sul solo
        # orizzonte era un bug vero: la variabile non riusciva a rappresentare
        # lo sforamento, il vincolo diventava insoddisfacibile e l'unico modo
        # di rispettarlo restava `assign = 0`. Cosi' un vincolo MORBIDO si
        # comportava come uno DURO, vietando l'assegnazione invece di
        # penalizzarla.
        slack_bound = max(1, lo, mv.horizon_units - hi)
        for i, p in enumerate(pairs):
            if as_constraint:
                model.Add(p.departure >= lo).OnlyEnforceIf(p.assign)
                model.Add(p.release <= hi).OnlyEnforceIf(p.assign)
            else:
                out = model.NewIntVar(0, slack_bound, f"tw_{c.constraint_id}_{i}")
                model.Add(out >= lo - p.departure).OnlyEnforceIf(p.assign)
                model.Add(out >= p.release - hi).OnlyEnforceIf(p.assign)
                model.Add(out == 0).OnlyEnforceIf(p.assign.Not())
                penalise(out, slack_bound, "finestra temporale")
        return True

    # ---- vincoli sulle coppie mezzo-focolaio ----------------------------

    if kind is ConstraintKind.FORBID_ASSIGNMENT:
        vehicle_ids = resolve_vehicle_ids(c.vehicle_selector, scenario)
        fire_ids = resolve_fire_ids(c.fire_selector, scenario)
        if not vehicle_ids or not fire_ids:
            return False
        pairs = _pairs_of(mv, vehicle_ids, fire_ids)
        _forbid(mv, pairs, as_constraint, penalise, "accoppiamento vietato")
        return True

    if kind is ConstraintKind.FORCE_ASSIGNMENT:
        vehicle_ids = resolve_vehicle_ids(c.vehicle_selector, scenario)
        fire_ids = resolve_fire_ids(c.fire_selector, scenario)
        # Selettore che non pesca niente: l'ordine e' INAPPLICABILE, non
        # impossibile, e la differenza cambia la risposta al coordinatore.
        #
        # Il caso e' "mandate i Canadair su Cirella" dove di Canadair non ce
        # n'e' nessuno. Senza questo controllo `_pairs_of` restituiva una lista
        # vuota, la somma delle assegnazioni diventava la costante zero e il
        # vincolo si riduceva a `0 >= min_count`, cioe' a un modello
        # infattibile per costruzione. Il solver passava allora al
        # rilassamento e scriveva nel piano "ordine rilassato, il piano lo
        # viola consapevolmente": una frase falsa, perche' non c'era nessuna
        # contraddizione fra ordini, c'era un ordine che parlava di mezzi
        # assenti.
        #
        # E il danno non si fermava alla nota. Il rilassamento e' globale:
        # converte in penalita' TUTTI i vincoli duri dell'ordine, non solo
        # quello impossibile. Un solo vincolo con il selettore a vuoto bastava
        # quindi a declassare a preferenze anche gli ordini perfettamente
        # eseguibili che lo accompagnavano, e il solver poteva violarli se il
        # guadagno superava la penalita'. Con il controllo, l'ordine viene
        # dichiarato inapplicabile (`return False`, che il chiamante annota) e
        # gli altri restano duri.
        #
        # E' la stessa lettura che gia' applicavano EXCLUDE_VEHICLE,
        # RESERVE_VEHICLE, VEHICLE_TYPE_QUOTA, TIME_WINDOW e
        # FORBID_ASSIGNMENT: qui era rimasta indietro.
        if not vehicle_ids or not fire_ids:
            return False
        for fid in fire_ids:
            pairs = _pairs_of(mv, vehicle_ids, [fid])
            total = sum(p.assign for p in pairs) if pairs else 0
            if as_constraint:
                model.Add(total >= c.min_count)
            else:
                short = model.NewIntVar(
                    0, c.min_count, f"force_{c.constraint_id}_{fid}"
                )
                model.Add(short >= c.min_count - total)
                penalise(short, c.min_count, "assegnazione imposta")
        return True

    if kind is ConstraintKind.MIN_VEHICLES_ON_FIRE:
        fire_ids = resolve_fire_ids(c.fire_selector, scenario)
        # `vehicle_selector` qui e' opzionale: assente significa "due mezzi
        # qualsiasi", quindi `resolve_vehicle_ids(None, ...)` restituisce tutta
        # la flotta. Un selettore vuoto ha percio' un solo significato
        # possibile, "nello scenario non c'e' nessun mezzo che corrisponda", e
        # vale lo stesso ragionamento scritto sopra per FORCE_ASSIGNMENT:
        # l'ordine e' inapplicabile, non contraddittorio.
        #
        # Attenzione a non confondere i due casi vicini: selettore vuoto
        # (nessun mezzo esiste) e' inapplicabile, mentre mezzi che esistono ma
        # non hanno nessuna missione praticabile (niente acqua, focolaio fuori
        # portata) e' una vera infattibilita' fisica e deve continuare a
        # passare dal rilassamento. Il controllo qui sotto distingue il primo
        # dal secondo, perche' guarda i mezzi dello scenario e non le coppie
        # del modello.
        vehicle_ids = resolve_vehicle_ids(c.vehicle_selector, scenario)
        if not fire_ids or not vehicle_ids:
            return False
        for fid in fire_ids:
            pairs = _pairs_of(mv, vehicle_ids, [fid])
            total = sum(p.assign for p in pairs) if pairs else 0
            if as_constraint:
                model.Add(total >= c.count)
            else:
                short = model.NewIntVar(0, c.count, f"minveh_{c.constraint_id}_{fid}")
                model.Add(short >= c.count - total)
                penalise(short, c.count, "mezzi minimi sul focolaio")
        return True

    if kind is ConstraintKind.MAX_RESPONSE_TIME:
        # Il tetto e' imposto su `arrival`, cioe' sull'ORARIO di arrivo
        # contato dall'inizio dell'orizzonte, non sulla differenza
        # arrivo-partenza che `Assignment.response_minutes` chiama tempo di
        # risposta. Le due letture coincidono solo per i mezzi che partono
        # subito.
        #
        # La scelta e' deliberata e va nella direzione sicura: siccome
        # `departure >= 0`, vale sempre `arrival >= arrival - departure`,
        # quindi ogni piano che rispetta il tetto sull'orario rispetta anche
        # il tetto sulla differenza. Il validatore, che e' indipendente e puo'
        # leggere il vincolo nell'altro modo, non trovera' mai una violazione
        # che il solver si era concesso. Al contrario, limitare la differenza
        # avrebbe permesso a un mezzo di partire al minuto 200 e "rispondere
        # in dieci minuti" arrivando al minuto 210 su un incendio scoppiato
        # all'inizio dell'orizzonte, che operativamente non e' una risposta
        # entro dieci minuti.
        fire_ids = resolve_fire_ids(c.fire_selector, scenario)
        if not fire_ids:
            return False
        # Arrotondamento per DIFETTO, cioe' verso il vincolo piu' stretto: il
        # tetto concesso al solver non e' mai piu' largo di quello scritto
        # nell'ordine. Vale la stessa regola usata per la finestra temporale e
        # per la riserva a tempo (dove invece si arrotonda per eccesso, perche'
        # li' il numero e' un pavimento e non un tetto): il senso non e'
        # "sempre in giu'", e' "sempre dalla parte che il validatore non puo'
        # contestare".
        limit = to_units_down(c.minutes)
        for i, p in enumerate(_pairs_of(mv, None, fire_ids)):
            if as_constraint:
                model.Add(p.arrival <= limit).OnlyEnforceIf(p.assign)
            else:
                over = model.NewIntVar(0, mv.horizon_units, f"mrt_{c.constraint_id}_{i}")
                model.Add(over >= p.arrival - limit).OnlyEnforceIf(p.assign)
                model.Add(over == 0).OnlyEnforceIf(p.assign.Not())
                penalise(over, mv.horizon_units, "tempo di risposta massimo")
        return True

    return False


def _apply_reserve(
    mv: ModelVars,
    scenario: Scenario,
    c: Any,
    as_constraint: bool,
    penalise: Callable[[Any, int, str], None],
) -> bool:
    """RESERVE_VEHICLE: tiene un mezzo fuori dal piano, del tutto o fino a un'ora.

    Tre letture, tutte presenti nel linguaggio del coordinatore:
      - "l'elicottero resta di riserva": il mezzo non si usa;
      - "tenete due mezzi di riserva": ne bastano due fra quelli selezionati;
      - "l'elicottero non muove prima delle undici": vincolo sulla partenza.

    Le tre letture si escludono a vicenda e l'ordine di precedenza e'
    `until_minute`, poi `count`, poi il divieto pieno. La ragione e' che
    `until_minute` e' la lettura piu' specifica: quando il coordinatore dice
    un'ora, e' quella che vuole. Lo schema pero' permette di valorizzare
    entrambi i campi, e in quel caso uno dei due viene ignorato: siccome
    stiamo scartando in silenzio un pezzo di un ordine, lo si scrive nelle
    note del piano. Applicarli insieme sarebbe peggio che sbagliato, sarebbe
    piu' restrittivo di entrambe le letture ("nessuno parte prima delle
    undici" E "due mezzi fermi tutto il giorno"), cioe' un terzo ordine che il
    coordinatore non ha dato.

    Convenzione sui minuti: `until_minute` e' letto come minuti dall'INIZIO
    DELL'ORIZZONTE, la stessa convenzione dichiarata in `Assignment`, non come
    minuti dalla mezzanotte. Qui la lettura e' diversa da quella di
    `TIME_WINDOW`, che invece e' oraria, e la differenza non e' una svista:
    una finestra temporale parla dell'orologio ("gli aerei fino alle otto"),
    una riserva a tempo parla del piano ("questo mezzo non muove nella prima
    mezz'ora"). Il validatore indipendente confronta `until_minute` con
    `Assignment.departure_minute`, cioe' legge anche lui minuti di piano: le
    due implementazioni concordano, ed e' questo che rende la convenzione
    affidabile e non solo dichiarata.
    """
    model = mv.model
    vehicle_ids = resolve_vehicle_ids(c.selector, scenario)
    if not vehicle_ids:
        return False

    if c.until_minute is not None and c.count is not None:
        mv.notes.append(
            f"[RESERVE_VEHICLE] {c.constraint_id}: indicati sia un'ora "
            f"({c.until_minute} min) sia un numero di mezzi ({c.count}). "
            f"Applicata solo l'ora; il numero di mezzi di riserva e' stato "
            f"ignorato."
        )

    if c.until_minute is not None:
        # Per ECCESSO: qui il numero e' un PAVIMENTO sulla partenza, quindi
        # arrotondare in su e' il lato stretto, cioe' quello che il validatore
        # (che confronta `until_minute` con `Assignment.departure_minute`) non
        # potra' mai contestare.
        lo = to_units_up(c.until_minute)
        for i, p in enumerate(_pairs_of_vehicles(mv, vehicle_ids)):
            if as_constraint:
                model.Add(p.departure >= lo).OnlyEnforceIf(p.assign)
            else:
                early = model.NewIntVar(0, lo, f"res_{c.constraint_id}_{i}")
                model.Add(early >= lo - p.departure).OnlyEnforceIf(p.assign)
                model.Add(early == 0).OnlyEnforceIf(p.assign.Not())
                penalise(early, lo, "riserva fino a un'ora")
        return True

    if c.count is not None:
        used = [mv.used[v] for v in vehicle_ids if v in mv.used]
        spare = len(vehicle_ids) - int(c.count)
        total = sum(used) if used else 0
        if as_constraint:
            model.Add(total <= max(0, spare))
        else:
            over = model.NewIntVar(0, len(vehicle_ids), f"res_n_{c.constraint_id}")
            model.Add(over >= total - max(0, spare))
            penalise(over, len(vehicle_ids), "numero di mezzi di riserva")
        return True

    _forbid(
        mv,
        _pairs_of_vehicles(mv, vehicle_ids),
        as_constraint,
        penalise,
        "mezzo di riserva",
    )
    # Anche se il mezzo non ha coppie praticabili il vincolo e' stato
    # "applicato": il mezzo non verra' usato, che e' quello che chiedeva.
    return True


def _forbid(
    mv: ModelVars,
    pairs: Sequence[PairVars],
    as_constraint: bool,
    penalise: Callable[[Any, int, str], None],
    label: str,
) -> None:
    """Vieta un insieme di assegnazioni, come vincolo o come penalita'.

    Da duro si azzera ogni `assign` una per una invece di scrivere
    `sum(assign) == 0`: per il solver e' la stessa cosa, ma un vincolo per
    variabile si propaga subito e fissa il dominio a {0}, mentre una somma
    nulla ci arriva in un passaggio in piu'.

    Da morbido si penalizza la SOMMA delle assegnazioni vietate, non ciascuna
    separatamente, cosi' `max_value` vale il numero di coppie e la penalita'
    e' piena quando sono violate tutte. Con una penalita' per coppia il totale
    varrebbe `numero di coppie` volte il peso del criterio, cioe' un ordine
    morbido su molti mezzi peserebbe piu' di uno su pochi mezzi solo perche'
    tocca piu' mezzi.

    Lista vuota: non c'e' niente da vietare e non si aggiunge nulla. Chi
    chiama decide se questo significhi "ordine applicato" (il mezzo non e'
    usabile comunque) o "ordine inapplicabile"; qui non si puo' sapere.
    """
    if not pairs:
        return
    if as_constraint:
        for p in pairs:
            mv.model.Add(p.assign == 0)
    else:
        penalise(sum(p.assign for p in pairs), len(pairs), label)


def _pairs_of_vehicles(mv: ModelVars, vehicle_ids: Sequence[str]) -> list[PairVars]:
    """Tutte le coppie dei mezzi indicati, in ordine stabile.

    L'insieme serve solo per il test di appartenenza, che deve restare O(1)
    perche' si scorrono tutte le coppie; l'ordine del risultato viene dal
    `sorted` sulle chiavi, non dall'insieme, che come tutti gli insiemi si
    itererebbe in un ordine dipendente dagli hash.
    """
    wanted = set(vehicle_ids)
    return [p for (v, _f), p in sorted(mv.pairs.items()) if v in wanted]


def _pairs_of(
    mv: ModelVars, vehicle_ids: Optional[Sequence[str]], fire_ids: Sequence[str]
) -> list[PairVars]:
    """Coppie che incrociano i mezzi e i focolai indicati, in ordine stabile.

    `vehicle_ids=None` significa "qualunque mezzo", e non "nessun mezzo": e'
    la lettura che serve a MAX_RESPONSE_TIME, che parla dei focolai e non dei
    mezzi. Una lista vuota invece significa davvero "nessun mezzo" e produce
    un risultato vuoto: sono due casi diversi ed e' il chiamante a doverli
    distinguere prima di arrivare qui.
    """
    fires = set(fire_ids)
    vehicles = None if vehicle_ids is None else set(vehicle_ids)
    return [
        p
        for (v, f), p in sorted(mv.pairs.items())
        if f in fires and (vehicles is None or v in vehicles)
    ]


def _fires_in_zone(mv: ModelVars, scenario: Scenario, c: Any) -> list[str]:
    """Focolai che ricadono nella zona descritta da un PRIORITIZE_ZONE.

    Il risultato e' deduplicato ma tenuto nell'ORDINE DELLO SCENARIO, non in
    ordine alfabetico: cosi' due esecuzioni sullo stesso scenario applicano i
    moltiplicatori di priorita' nella stessa sequenza. Qui in realta' la
    moltiplicazione e' commutativa, ma la regola vale per tutto il
    sottosistema e non conviene farne eccezioni a memoria.

    Il caso RADIUS non e' una proprieta' del singolo focolaio ma una query
    geometrica su tutti: si tratta prima e si esce, senza entrare nel ciclo.
    """
    if c.zone_kind is ZoneKind.RADIUS:
        # I tre campi sono garantiti non nulli dal validatore di
        # `PrioritizeZone`, che rifiuta zone_kind=radius senza centro e raggio.
        return _fires_within(mv, scenario, c.center_lat, c.center_lon, c.radius_km)

    values = [_norm(v) for v in c.zone_values]
    out: list[str] = []
    for f in scenario.fires:
        if c.zone_kind is ZoneKind.WUI and f.is_wui:
            out.append(f.fire_id)
        elif c.zone_kind is ZoneKind.COMUNE and _norm(f.comune) in values:
            out.append(f.fire_id)
        elif c.zone_kind is ZoneKind.PROVINCIA and _norm(f.provincia) in values:
            out.append(f.fire_id)
        elif c.zone_kind is ZoneKind.LAND_COVER and _norm(f.land_cover.value) in values:
            out.append(f.fire_id)
        elif c.zone_kind is ZoneKind.PROTECTED_AREA and f.in_protected_area:
            if not values or _norm(f.protected_area_name) in values:
                out.append(f.fire_id)

    # Deduplica conservando la prima occorrenza. `dict.fromkeys` lo fa in un
    # passaggio solo; l'alternativa `sorted(set(...), key=lista.index)` era
    # quadratica e rifaceva l'elenco degli identificativi a ogni confronto.
    return list(dict.fromkeys(out))


def _fires_within(
    mv: ModelVars, scenario: Scenario, lat: float, lon: float, radius_km: float
) -> list[str]:
    """Focolai entro un raggio da un punto."""
    from tagliafuoco.domain.models import GeoPoint

    center = GeoPoint(lat=float(lat), lon=float(lon))
    return [
        f.fire_id
        for f in scenario.fires
        if haversine_km(f.centroid, center) <= float(radius_km)
    ]
