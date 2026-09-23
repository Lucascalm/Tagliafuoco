"""Baseline: la regola banale contro cui si misura l'ottimizzatore.

PERCHE' QUESTO MODULO ESISTE
Un piano prodotto da un solver non dimostra niente finche' non lo si confronta
con quello che una sala operativa farebbe senza solver. Il numero che conta non
e' "copertura 0.81": e' "copertura 0.81 contro 0.62 della regola banale, sugli
stessi scenari e con la stessa fisica". Senza termine di paragone il risultato
non e' interpretabile.

PERCHE' DUE BASELINE E NON UNA
Fra la regola banale e il solver ci sono due differenze sovrapposte: il solver
ordina meglio i focolai (guarda la priorita', non l'orologio) e li assegna tutti
insieme invece che uno alla volta. Con una baseline sola si misura la somma dei
due effetti e non si sa quanto pesa ciascuno.

  - `baseline_nearest` ordina i focolai per ora di rilevamento. E' la sala
    operativa che risponde alle segnalazioni nell'ordine in cui arrivano: non
    pianifica, reagisce. E' il termine di paragone piu' onesto, perche' e'
    davvero quello che succede senza uno strumento di supporto.
  - `baseline_priority` cambia una sola cosa: ordina per priorita' invece che
    per tempo. Tutto il resto (scelta del mezzo piu' vicino, fisica, vincoli di
    autonomia) e' identico.

Cosi' la differenza fra le due baseline isola il contributo del solo
ordinamento, e la differenza fra `baseline_priority` e il solver isola il
contributo dell'ottimizzazione vera, cioe' l'assegnazione simultanea invece che
una alla volta. Sono due numeri diversi e vanno riportati separatamente: con una
baseline sola si saprebbe che il solver guadagna, non da dove.

ONESTA' DELLA BASELINE
Una baseline volutamente stupida non dimostra niente: basta farla zoppicare per
vincere il confronto. Quindi qui la regola banale e' quella che userebbe
davvero un coordinatore: manda il mezzo libero piu' vicino e compatibile,
continua a mandarne finche' il fabbisogno d'acqua non e' coperto (fino a un
tetto dichiarato), rispetta autonomia, turno, vento, notte e disponibilita'.
L'unica cosa che NON fa e' guardare avanti: ogni focolaio viene servito senza
sapere quali focolai vengono dopo. E' esattamente li' che l'ottimizzatore deve
guadagnare, e se non guadagna li' non serve a niente.

GEOMETRIA CONDIVISA CON optimize/ (e perche' qui e' corretto condividerla)
Il validatore non condivide codice con nessuno: se ricalcolasse i tempi con le
stesse funzioni del solver, un errore comune ai due resterebbe invisibile per
costruzione. La baseline invece la geometria la condivide con `optimize/`, e
deve farlo: baseline e solver devono vedere la STESSA fisica e differire solo
nella strategia, altrimenti il confronto misurerebbe due modelli fisici diversi
invece che due modi di decidere. Se `optimize/` non e' ancora importabile
(sviluppo in parallelo) si usa la copia di riserva qui sotto, che implementa le
stesse formule della sezione 6 di SPEC.md.

QUALI ORDINI DEL COORDINATORE LA BASELINE RISPETTA, E PERCHE' SOLO QUELLI
Questo e' il punto metodologico del sottosistema, quindi vale la pena scriverlo
per esteso invece di lasciarlo dedurre dal codice. I vincoli tradotti si
dividono in quattro gruppi e la baseline li tratta in quattro modi diversi.

1. VINCOLI DURI DI DIVIETO: li rispetta, tutti.
   Sono quelli che tolgono qualcosa dal tavolo senza chiedere niente in cambio,
   cioe' che restringono l'insieme dei mezzi impiegabili o delle coppie
   mezzo-focolaio ammissibili: RESERVE_VEHICLE, EXCLUDE_VEHICLE, TIME_WINDOW e
   FORBID_ASSIGNMENT. Rispettarli non richiede di guardare avanti: basta non
   scegliere cio' che l'ordine vieta, e il resto della regola avida non cambia
   di una virgola.

   La ragione non e' di completezza ma di misura. La valutazione confronta il
   risolutore e la baseline SUGLI STESSI scenari: se la baseline potesse usare
   mezzi che il risolutore non puo' usare, il confronto sarebbe falsato a suo
   favore e qualunque conclusione perderebbe valore. Oltre a questo, il
   validatore indipendente respinge in blocco un piano che viola un vincolo
   duro, e un piano respinto non entra nel confronto: confrontare un piano
   accettato con uno buttato non misura niente. Gli ordini del coordinatore
   valgono comunque, indipendentemente da chi pianifica.

2. VINCOLI MORBIDI: NON li insegue, di proposito, ed e' la scelta che rende il
   confronto significativo.
   PRIORITIZE_ZONE, OBJECTIVE_WEIGHT, PROTECT_INFRASTRUCTURE e qualunque altro
   vincolo marcato SOFT entrano nella funzione obiettivo del risolutore come
   penalita'. La baseline non ha una funzione obiettivo e non ne deve avere
   una: la differenza fra i due pianificatori deve restare "entrambi obbediscono
   agli ordini vincolanti, solo il risolutore sfrutta le preferenze". Se la
   baseline applicasse anche PRIORITIZE_ZONE staremmo misurando una cosa
   diversa, cioe' due euristiche di priorita' a confronto, e il guadagno
   dichiarato del risolutore si ridurrebbe per un motivo che non ha niente a
   che vedere con l'ottimizzazione. La distinzione duro/morbido e' presente nel
   linguaggio del coordinatore ("l'elicottero resta di riserva" contro "se
   possibile tieni un mezzo libero") ed e' gia' tipizzata nello schema: qui la
   si usa per quello che serve.

3. VINCOLI DURI DI RISULTATO: li IGNORA, e il piano lo dichiara.
   MAX_RESPONSE_TIME, MIN_VEHICLES_ON_FIRE, VEHICLE_TYPE_QUOTA e
   FORCE_ASSIGNMENT sono ordini veri quanto gli altri, ma non si rispettano
   scartando un candidato: si rispettano scegliendo le assegnazioni tutte
   insieme, perche' impongono una proprieta' del piano finito ("almeno due mezzi
   su quel focolaio", "tutti sul posto entro venti minuti"). Guardare avanti e'
   esattamente cio' che la baseline non fa, ed e' il punto su cui il risolutore
   deve guadagnare.

   La posizione presa qui e' che la baseline non li applichi: rappresenta cio'
   che si farebbe senza strumenti, e senza strumenti quei vincoli non si
   riescono a garantire. Fingere il contrario darebbe un termine di paragone
   che nessuna sala operativa potrebbe tenere davvero. La posizione ha pero' un
   prezzo, e va pagato alla luce del sole: `_greedy_plan` scrive nelle note del
   piano quali vincoli ha lasciato cadere, con tanto di identificativo, cosi'
   chi legge il confronto sa che una baseline respinta per uno di quelli e' il
   comportamento atteso e non un difetto. Nasconderlo significherebbe zavorrare
   la baseline di nascosto, che e' il modo piu' facile di vincere un confronto
   senza averlo vinto.

4. VINCOLI DURI CHE AGISCONO SUI PESI: non sono applicabili a nessun piano.
   Un PRIORITIZE_ZONE marcato HARD e' un errore di traduzione, non un ordine:
   su un singolo piano non esiste un criterio per dire se un peso e' stato
   rispettato. Il validatore lo segnala come avviso e la baseline lo tratta
   come un morbido.

L'esperimento 1 di SPEC.md sezione 7 si fa comunque senza vincoli tradotti, ed
e' li' che il paragone e' piu' pulito; il gruppo 1 serve perche' resti
interpretabile anche negli altri due esperimenti, dove i vincoli ci sono.

DOVE I VINCOLI DEL GRUPPO 1 ENTRANO DAVVERO NEL CICLO AVIDO
Dichiarare la politica non basta: un vincolo che il ciclo avido non consulta e'
un vincolo non applicato, per quanto bene sia descritto qui sopra. I quattro
divieti agiscono quindi in tre punti diversi e nessuno di essi e' facoltativo.

  - RESERVE_VEHICLE e EXCLUDE_VEHICLE tolgono il mezzo dalla flotta prima che il
    ciclo cominci (`_vehicles_blocked_by`).
  - FORBID_ASSIGNMENT toglie il mezzo dai candidati del singolo focolaio
    (`_forbidden_pairs`).
  - TIME_WINDOW non toglie niente a priori: restringe QUANDO la missione puo'
    stare, quindi agisce dentro la scelta del mezzo, come pavimento sulla
    partenza e tetto sul rientro (`_time_windows_by_vehicle` piu'
    `_plan_mission_within_windows`). Un mezzo le cui finestre non lasciano posto
    a nessuna missione semplicemente non viene scelto, che e' il modo in cui una
    sala operativa rispetta un'ora di chiusura senza pianificare niente.

Su chi sia il mezzo nominato da un ordine ci sono due letture: quella minima di
questo modulo, che ricalca campo per campo il validatore, e quella di
`nlu/resolver.py`, che riconosce anche nomi e sinonimi. Per un divieto secco si
prende la loro unione, perche' fermare un mezzo in piu' costa alla baseline un
po' di efficienza e basta. Per una riserva A QUOTA ("tienimene fermi due")
l'unione smette di essere prudente: li' non si sceglie SE fermare ma QUALI, e i
primi due di un insieme piu' largo possono essere entrambi mezzi che il giudice
non considera selezionati. La quota va quindi soddisfatta dentro ciascuna
lettura (`_vehicle_id_readings`), altrimenti la baseline crede di aver obbedito
e il validatore la respinge lo stesso.

CHE COSA SUCCEDE FRA DUE MISSIONI DELLO STESSO MEZZO
SPEC.md sezione 6 lo ha reso esplicito dopo che due moduli ne avevano dato due
letture diverse, ed e' il genere di ambiguita' che non produce un errore locale
ma un disaccordo al confine. Una missione TERMINA CON IL RIENTRO ALLA BASE:
`t_ritorno` e' dentro `t_missione`, quindi al minuto di rilascio il mezzo e' a
terra, non sopra l'incendio. Ne seguono le due regole che questo modulo applica.

  - Il trasferimento verso il focolaio successivo si misura dalla base, ed e'
    gia' contato dentro `t_andata` della missione nuova. Sommarlo anche come
    distacco fra le due missioni significherebbe contarlo due volte e produrre
    una baseline piu' lenta del vero per un errore di modello, non per una sua
    debolezza. Zavorrare la baseline e' un modo di vincere il confronto senza
    averlo vinto, quindi qui non si fa.
  - Il tempo che passa davvero fra un rientro e la ripartenza e' il
    riattrezzamento della base, `Base.turnaround_minutes`: rifornimento, cambio
    equipaggio, controlli. E' un vincolo vero e il validatore lo controlla come
    bloccante, quindi la baseline lo rispetta per costruzione
    (`_earliest_departure`).
  - Il punto da cui il mezzo parte e a cui rientra e' UNO SOLO per tutto il
    piano: la sua posizione corrente se lo scenario la dichiara, altrimenti la
    sua base. Non e' un dettaglio implementativo ma la stessa ipotesi che usa il
    validatore quando ricalcola l'avvicinamento di ogni assegnazione, e le due
    devono coincidere. Quando qui il mezzo rientrava "a casa" mentre era partito
    da altrove, la seconda missione risultava piu' rapida del minimo fisico
    misurato dal validatore e il piano veniva respinto in blocco: la baseline
    perdeva il confronto per un disaccordo di convenzione, non per la sua
    strategia.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from tagliafuoco.config import (
    DEFAULT_HORIZON_MINUTES,
    EARTH_RADIUS_KM,
    ROAD_TORTUOSITY,
)
from tagliafuoco.domain.models import (
    Assignment,
    Base,
    Domain,
    FireCluster,
    GeoPoint,
    Plan,
    Scenario,
    Vehicle,
    VehicleStatus,
    WaterSource,
)
from tagliafuoco.nlu.schema import (
    ConstraintKind,
    ConstraintSet,
    Enforcement,
    SelectorField,
)

PRODUCER_NEAREST = "baseline_nearest"
PRODUCER_PRIORITY = "baseline_priority"

# Quanti mezzi al massimo la baseline manda sullo stesso focolaio. E' una
# taratura, non un dato: serve solo a impedire che il primo focolaio della lista
# si prenda tutta la flotta, cosa che renderebbe la baseline artificialmente
# pessima e quindi disonesta. Tre e' il numero di mezzi che una sala operativa
# impegna su un focolaio medio prima di chiedere rinforzi.
MAX_VEHICLES_PER_FIRE = 3

# Margine sui confronti fra minuti. I tempi sono float e il validatore
# ricalcola per conto suo con le sue formule: invece di arrivare esattamente al
# limite di autonomia o di turno, la baseline si tiene mezzo minuto indietro.
SAFETY_MARGIN_MINUTES = 0.5

# Minuti in un giorno. Serve a riportare una finestra oraria del coordinatore
# ("gli aerei fino alle otto") sui minuti di piano, che invece partono
# dall'inizio dell'orizzonte.
MINUTES_PER_DAY = 1440.0

# Riattrezzamento usato quando la base di casa del mezzo non e' nello scenario:
# e' il valore di default dichiarato da `Base.turnaround_minutes` in
# domain/models.py. Non si sceglie zero, perche' zero significherebbe "il mezzo
# riparte istantaneamente", cioe' inventare una prestazione che non c'e'.
DEFAULT_TURNAROUND_MINUTES = 10.0

# I due gruppi di vincoli duri, per sapere sempre di quale si sta parlando.
# Il gruppo 1 del docstring: restringono l'insieme dei mezzi impiegabili o
# delle coppie ammissibili, e la baseline li rispetta.
ENFORCED_HARD_KINDS: tuple[ConstraintKind, ...] = (
    ConstraintKind.RESERVE_VEHICLE,
    ConstraintKind.EXCLUDE_VEHICLE,
    ConstraintKind.TIME_WINDOW,
    ConstraintKind.FORBID_ASSIGNMENT,
)

# Il gruppo 3 del docstring: impongono una proprieta' del piano finito, quindi
# si rispettano solo pianificando. La baseline li ignora e lo dichiara nelle
# note del piano.
UNENFORCED_HARD_KINDS: tuple[ConstraintKind, ...] = (
    ConstraintKind.MAX_RESPONSE_TIME,
    ConstraintKind.MIN_VEHICLES_ON_FIRE,
    ConstraintKind.VEHICLE_TYPE_QUOTA,
    ConstraintKind.FORCE_ASSIGNMENT,
)


# --------------------------------------------------------------------------
# Geometria: prima quella di optimize/, poi la copia di riserva
# --------------------------------------------------------------------------

# Lunghezza di un grado di longitudine all'equatore, in km. Serve come prova:
# una funzione trovata in optimize/ che non restituisce questo valore non e' la
# funzione che stiamo cercando (o ha un'altra firma), e in quel caso si
# preferisce la copia locale invece di usare a scatola chiusa qualcosa che non
# corrisponde al modello fisico dichiarato.
_ONE_DEGREE_KM = 111.19

_HAVERSINE: Optional[Callable[[GeoPoint, GeoPoint], float]] = None


def _haversine_local(a: GeoPoint, b: GeoPoint) -> float:
    """Distanza in linea d'aria su sfera di raggio EARTH_RADIUS_KM, in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _resolve_haversine() -> Callable[[GeoPoint, GeoPoint], float]:
    """Cerca la haversine di optimize/, la mette alla prova, altrimenti ripiega.

    L'import e' tardivo di proposito: `optimize/` viene scritto in parallelo e
    potrebbe non esistere ancora, ma la baseline deve restare eseguibile.
    """
    try:
        from tagliafuoco.optimize import geometry as og  # type: ignore
    except Exception:
        return _haversine_local

    for name in ("haversine_km", "great_circle_km", "air_distance_km", "distance_km"):
        fn = getattr(og, name, None)
        if not callable(fn):
            continue
        try:
            probe = float(fn(GeoPoint(0.0, 0.0), GeoPoint(0.0, 1.0)))
        except Exception:
            continue
        if abs(probe - _ONE_DEGREE_KM) < 1.0:
            return fn  # type: ignore[return-value]
    return _haversine_local


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Distanza in linea d'aria fra due punti, in km.

    La risoluzione (optimize/ oppure copia locale) si fa una volta sola e si
    ricorda: e' una scelta di modello, non deve cambiare a meta' di una
    rigiocata.
    """
    global _HAVERSINE
    if _HAVERSINE is None:
        _HAVERSINE = _resolve_haversine()
    return float(_HAVERSINE(a, b))


def reset_geometry_resolution() -> None:
    """Dimentica quale haversine e' stata scelta. Serve ai test."""
    global _HAVERSINE
    _HAVERSINE = None


def travel_km(a: GeoPoint, b: GeoPoint, domain: Domain) -> float:
    """Distanza percorsa davvero: linea d'aria in volo, strada tortuosa a terra.

    Il fattore ROAD_TORTUOSITY e' una taratura dichiarata (SPEC.md sezione 6):
    in Calabria la strada e' molto piu' lunga della linea d'aria.
    """
    d = haversine_km(a, b)
    return d if domain is Domain.AIR else d * ROAD_TORTUOSITY


# --------------------------------------------------------------------------
# Missione di un singolo mezzo su un singolo focolaio
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Mission:
    """Esito del calcolo fisico per una coppia mezzo-focolaio."""

    departure_minute: float
    arrival_minute: float
    release_minute: float
    sorties: int
    water_liters: int
    water_source_id: Optional[str]
    cost_eur: float

    @property
    def engaged_minutes(self) -> float:
        return self.release_minute - self.departure_minute


def reposition_minutes(vehicle: Vehicle, origin: GeoPoint, target: GeoPoint) -> float:
    """Minuti per spostare il mezzo fra due punti, alla sua velocita' di crociera."""
    speed = float(vehicle.cruise_speed_kmh)
    if speed <= 0.0:
        return float("inf")
    return travel_km(origin, target, vehicle.domain) / speed * 60.0


def _base_point(vehicle: Vehicle, bases: Sequence[Base]) -> Optional[GeoPoint]:
    for b in bases:
        if b.base_id == vehicle.home_base_id:
            return b.point
    return None


def _origin_point(vehicle: Vehicle, bases: Sequence[Base]) -> Optional[GeoPoint]:
    """Da dove parte il mezzo: dove si trova ora, altrimenti la sua base."""
    return vehicle.current_position or _base_point(vehicle, bases)


def _nearest_water_source(
    vehicle: Vehicle, fire: FireCluster, sources: Sequence[WaterSource]
) -> Optional[WaterSource]:
    """Punto d'acqua utilizzabile piu' vicino al focolaio.

    `usable_by` vuoto significa "nessuna restrizione dichiarata": e' il caso dei
    dati statici minimali, e rifiutare l'acqua in quel caso renderebbe la
    baseline incapace di operare per un motivo puramente formale.
    """
    usable = [s for s in sources if not s.usable_by or vehicle.kind in s.usable_by]
    if not usable:
        return None
    return min(
        usable,
        key=lambda s: (travel_km(fire.centroid, s.point, vehicle.domain), s.source_id),
    )


def _plan_mission(
    vehicle: Vehicle,
    fire: FireCluster,
    scenario: Scenario,
    *,
    origin: GeoPoint,
    earliest_minute: float,
    minutes_flown: float,
    remaining_liters: int,
    horizon_minutes: int,
    latest_release_minute: Optional[float] = None,
) -> Optional[_Mission]:
    """Calcola la missione secondo le formule di SPEC.md sezione 6.

        t_andata      = d(origine, fuoco) / v * 60
        t_ciclo_acqua = d(fuoco, sorgente) * 2 / v * 60 + t_pescaggio + t_sgancio
        t_missione    = t_setup + t_andata + n_lanci * t_ciclo_acqua + t_ritorno

    Restituisce None se il mezzo non riesce a fare nemmeno un lancio dentro
    l'autonomia, il turno residuo e l'orizzonte del piano. Restituire None e non
    una missione impossibile e' la differenza fra una baseline onesta e una che
    bara sui numeri.

    `latest_release_minute` e' un quarto tetto, facoltativo: l'istante entro cui
    la missione deve essere finita, rientro compreso. Lo usa TIME_WINDOW, che
    chiude l'operativita' di un mezzo a una certa ora. Non e' l'orizzonte
    travestito: l'orizzonte vale per tutti i mezzi e viene dallo scenario,
    questo vale per i soli mezzi nominati dall'ordine del coordinatore.
    """
    speed = float(vehicle.cruise_speed_kmh)
    if speed <= 0.0:
        return None

    t_out = travel_km(origin, fire.centroid, vehicle.domain) / speed * 60.0

    # Il rientro e' verso lo STESSO punto da cui il mezzo e' partito, e non
    # verso la base di casa. Nel caso normale i due coincidono, perche'
    # `_origin_point` restituisce gia' la base quando il mezzo non dichiara una
    # posizione propria. Quando invece la posizione corrente e' un'altra (mezzo
    # ereditato da una finestra precedente, oppure scenario costruito a mano),
    # rientrare "a casa" produceva un piano incoerente con se' stesso: la
    # missione successiva ripartiva dalla base mentre il validatore, che ricalcola
    # ogni missione dalla stessa origine (`physics.vehicle_origin`), continuava a
    # misurarla dalla posizione corrente. Con una base piu' vicina del punto di
    # partenza il piano dichiarava un arrivo piu' rapido del minimo fisico e
    # veniva respinto in blocco per `arrival_time`. Un piano respinto esce dal
    # confronto, quindi la baseline sceglie l'ipotesi che la tiene allineata al
    # proprio giudice: parte e torna sempre nello stesso posto.
    t_back = travel_km(fire.centroid, origin, vehicle.domain) / speed * 60.0

    source: Optional[WaterSource] = None
    if vehicle.requires_water_source:
        source = _nearest_water_source(vehicle, fire, scenario.water_sources)
        if source is None:
            # Senza acqua utilizzabile il mezzo non puo' fare il suo lavoro:
            # meglio non assegnarlo che produrre un piano che il validatore
            # respingerebbe.
            return None
        leg = travel_km(fire.centroid, source.point, vehicle.domain)
        # Pescaggio: si prende il piu' lento fra il tempo dichiarato dal mezzo e
        # quello dichiarato dalla sorgente. E' una scelta conservativa voluta:
        # il validatore ricalcola per conto suo e, sovrastimando qui, il piano
        # resta dentro i limiti anche se il validatore usa la convenzione piu'
        # stretta.
        refill = max(float(vehicle.refill_minutes), float(source.refill_minutes))
        cycle = 2.0 * leg / speed * 60.0 + refill + float(vehicle.drop_minutes)
    else:
        # Squadre che non pescano: il ciclo e' solo scarico piu' ricondizionamento.
        cycle = float(vehicle.drop_minutes) + float(vehicle.refill_minutes)

    if cycle <= 0.0:
        return None

    fixed = float(vehicle.setup_minutes) + t_out + t_back

    # Tre tetti indipendenti: autonomia del mezzo, turno residuo dell'equipaggio,
    # orizzonte del piano. Si prende il piu' stretto.
    budget_endurance = float(vehicle.endurance_minutes) - fixed - SAFETY_MARGIN_MINUTES
    budget_shift = (
        float(vehicle.max_shift_minutes) - minutes_flown - fixed - SAFETY_MARGIN_MINUTES
    )
    budget_horizon = (
        float(horizon_minutes) - float(earliest_minute) - fixed - SAFETY_MARGIN_MINUTES
    )
    budget = min(budget_endurance, budget_shift, budget_horizon)
    if latest_release_minute is not None:
        budget = min(
            budget,
            float(latest_release_minute)
            - float(earliest_minute)
            - fixed
            - SAFETY_MARGIN_MINUTES,
        )
    if budget < cycle:
        return None

    max_sorties = int(math.floor(budget / cycle))
    if max_sorties < 1:
        return None

    # Un mezzo che non dichiara capacita' non puo' consegnare acqua. Qui prima
    # c'era `max(1, capacita')`, che sembrava una guardia contro la divisione per
    # zero e invece faceva dichiarare al piano litri che il mezzo non ha: con
    # capacita' 0 il piano annunciava un litro per lancio, e il validatore lo
    # respingeva (`check_water_capacity` confronta i litri dichiarati con
    # capacita' per numero di lanci, che vale zero). Meglio non assegnarlo.
    capacity = int(vehicle.water_capacity_liters)
    if capacity <= 0:
        return None

    needed = max(1, math.ceil(max(0, remaining_liters) / capacity))
    sorties = min(needed, max_sorties)

    departure = float(earliest_minute)
    arrival = departure + float(vehicle.setup_minutes) + t_out
    release = departure + fixed + sorties * cycle
    engaged = release - departure
    return _Mission(
        departure_minute=round(departure, 3),
        arrival_minute=round(arrival, 3),
        release_minute=round(release, 3),
        sorties=sorties,
        water_liters=sorties * capacity,
        water_source_id=source.source_id if source is not None else None,
        cost_eur=round(engaged / 60.0 * float(vehicle.cost_per_hour_eur), 2),
    )


# --------------------------------------------------------------------------
# Disponibilita' e vincoli
# --------------------------------------------------------------------------


def _is_usable(vehicle: Vehicle, scenario: Scenario) -> bool:
    """Filtri che anche una sala operativa senza ottimizzatore applica."""
    if vehicle.status is not VehicleStatus.AVAILABLE:
        return False
    if scenario.wind_kmh > vehicle.max_wind_kmh:
        return False
    if scenario.is_night and vehicle.is_aerial and not vehicle.night_capable:
        # I mezzi aerei antincendio non operano fra tramonto e alba: non e' una
        # preferenza, e' un limite operativo.
        return False
    return True


def _norm(text: str) -> str:
    """Confronto insensibile a maiuscole e spazi.

    E' la stessa normalizzazione che usa il validatore. Se qui il confronto
    fosse sensibile alle maiuscole e nel validatore no, un ordine che dice
    "can_01" toglierebbe il Canadair per il validatore e non per la baseline: la
    baseline lo impiegherebbe, il piano verrebbe respinto e uscirebbe dal
    confronto. Una differenza di maiuscole che costa l'intero esperimento.
    """
    return str(text).strip().lower()


def _select_vehicle_ids_locally(scenario: Scenario, selector) -> set[str]:
    """Lettura minima di un selettore sui mezzi, senza dipendenze.

    Ricalca campo per campo la lettura del validatore, compresa la risposta
    vuota per un selettore che parla di focolai. Serve solo come rete quando
    `nlu/resolver.py` non e' importabile.
    """
    field = selector.field
    values = {_norm(v) for v in selector.values}
    out: set[str] = set()
    for v in scenario.vehicles:
        if field is SelectorField.ALL:
            out.add(v.vehicle_id)
        elif field is SelectorField.VEHICLE_ID and _norm(v.vehicle_id) in values:
            out.add(v.vehicle_id)
        elif field is SelectorField.VEHICLE_KIND and _norm(v.kind.value) in values:
            out.add(v.vehicle_id)
        elif field is SelectorField.VEHICLE_DOMAIN and _norm(v.domain.value) in values:
            out.add(v.vehicle_id)
        elif field is SelectorField.BASE_ID and _norm(v.home_base_id) in values:
            out.add(v.vehicle_id)
    return out


_TRUE_WORDS = frozenset({"true", "1", "si", "si'", "yes", "vero", "y"})


def _select_fire_ids_locally(scenario: Scenario, selector) -> set[str]:
    """Come sopra, per i focolai. Stessi campi e stessa lettura del validatore."""
    field = selector.field
    values = {_norm(v) for v in selector.values}
    flags = {_norm(v) in _TRUE_WORDS for v in selector.values}
    out: set[str] = set()
    for f in scenario.fires:
        if field is SelectorField.ALL:
            out.add(f.fire_id)
        elif field is SelectorField.FIRE_ID and _norm(f.fire_id) in values:
            out.add(f.fire_id)
        elif field is SelectorField.FIRE_COMUNE and f.comune and _norm(f.comune) in values:
            out.add(f.fire_id)
        elif (
            field is SelectorField.FIRE_PROVINCIA
            and f.provincia
            and _norm(f.provincia) in values
        ):
            out.add(f.fire_id)
        elif field is SelectorField.FIRE_SEVERITY and _norm(f.severity.value) in values:
            out.add(f.fire_id)
        elif field is SelectorField.FIRE_IS_WUI and f.is_wui in flags:
            out.add(f.fire_id)
        elif field is SelectorField.FIRE_IN_PROTECTED_AREA and f.in_protected_area in flags:
            out.add(f.fire_id)
    return out


def _select_vehicle_ids(scenario: Scenario, selector) -> set[str]:
    """Mezzi descritti dal selettore, secondo entrambe le letture disponibili.

    Il risolutore ufficiale sta in `nlu/resolver.py` e legge un selettore meglio
    di chiunque altro (nomi, accenti, sinonimi). L'import e' tardivo perche' i
    sottosistemi si scrivono in parallelo e la baseline deve restare eseguibile
    da sola.

    Il risultato e' l'UNIONE della lettura del risolutore e di quella minima qui
    sopra, e l'unione non e' pigrizia. Le due letture possono divergere sui casi
    limite; per una baseline le due divergenze non costano uguale. Escludere un
    mezzo in piu' le toglie un po' di efficienza, cioe' ricade a suo sfavore e
    non a favore del solver. Escluderne uno in meno le fa produrre un piano che
    il validatore respinge, e un piano respinto esce dal confronto: costa
    l'intero esperimento. Davanti a un dubbio si sceglie l'errore che si paga
    meno caro.
    """
    local = _select_vehicle_ids_locally(scenario, selector)
    try:
        from tagliafuoco.nlu import resolver as nlu_resolver  # type: ignore

        local |= {v.vehicle_id for v in nlu_resolver.resolve_vehicles(selector, scenario)}
    except Exception:
        pass
    return local


def _vehicle_id_readings(scenario: Scenario, selector) -> tuple[frozenset[str], ...]:
    """Le letture distinte del selettore sui mezzi, dalla piu' stretta alla piu' larga.

    `_select_vehicle_ids` fonde le due letture in una sola perche' per un
    divieto secco l'unione e' sempre prudente: un mezzo di troppo tolto dal
    tavolo costa alla baseline un po' di efficienza e basta. Per un vincolo a
    quota ("tienimene fermi due") l'unione smette di essere prudente, perche'
    li' non si sceglie SE bloccare ma QUALI bloccare, e i primi n di un insieme
    piu' largo possono essere tutti mezzi che il giudice non considera
    selezionati.

    Tenendo le letture separate si puo' invece soddisfare la quota dentro
    ciascuna: qualunque sia la lettura che il validatore applica, quella lettura
    vede i suoi n mezzi fermi. La lettura stretta e' la prima perche' e' quella
    che ricalca campo per campo `validate/rules.py`, ed e' quindi quella che nel
    sistema di oggi decide l'esito.
    """
    strict = frozenset(_select_vehicle_ids_locally(scenario, selector))
    broad = frozenset(_select_vehicle_ids(scenario, selector))
    return (strict,) if strict == broad else (strict, broad)


def _select_fire_ids(scenario: Scenario, selector) -> set[str]:
    """Focolai descritti dal selettore. Stessa logica di unione, stessa ragione."""
    local = _select_fire_ids_locally(scenario, selector)
    try:
        from tagliafuoco.nlu import resolver as nlu_resolver  # type: ignore

        local |= {f.fire_id for f in nlu_resolver.resolve_fires(selector, scenario)}
    except Exception:
        pass
    return local


def _vehicles_blocked_by(
    scenario: Scenario, constraint_set: Optional[ConstraintSet]
) -> set[str]:
    """Mezzi che la baseline non puo' usare per un ordine esplicito.

    La baseline non pianifica, ma non puo' nemmeno ignorare un ordine duro che
    toglie un mezzo dal tavolo: un piano che impiega un mezzo messo di riserva
    verrebbe respinto dal validatore, e il confronto fra un piano valido e uno
    respinto non misura niente. Tutti i vincoli che chiedono un risultato
    (priorita', quote, tempi massimi, pesi) vengono ignorati di proposito: e'
    esattamente cio' che distingue una baseline da un pianificatore, e il motivo
    sta nel docstring del modulo.

    Qui si trattano i due divieti che agiscono sull'intera flotta. Gli altri due
    del gruppo 1 restringono meno e quindi vivono altrove: FORBID_ASSIGNMENT in
    `_forbidden_pairs`, perche' vieta una coppia e non un mezzo, e TIME_WINDOW
    in `_time_windows_by_vehicle`, perche' vieta un orario e non un impiego.
    """
    if constraint_set is None:
        return set()
    blocked: set[str] = set()
    for c in constraint_set.constraints:
        if c.enforcement is not Enforcement.HARD:
            continue
        if c.kind is ConstraintKind.EXCLUDE_VEHICLE:
            blocked |= _select_vehicle_ids(scenario, c.selector)
        elif c.kind is ConstraintKind.RESERVE_VEHICLE:
            count = getattr(c, "count", None)
            # `until_minute` (riserva a tempo) viene letto come riserva piena:
            # la baseline non sa spostare una missione piu' in la', quindi
            # l'unico modo che ha di rispettare il vincolo e' non usare il
            # mezzo. L'errore prudente ricade a suo sfavore, che e' il verso
            # giusto.
            if count is None:
                # Riserva piena: si fermano tutti i mezzi selezionati, e qui
                # l'unione delle due letture e' la scelta prudente.
                blocked |= _select_vehicle_ids(scenario, c.selector)
                continue
            # Riserva a quota: il vincolo non dice di fermare tutti, dice
            # quanti fermarne. Qui la fusione delle due letture in un insieme
            # solo e' un errore, non una prudenza. I primi n di un'unione piu'
            # larga possono cadere tutti su mezzi che il giudice non considera
            # selezionati: la baseline crede di aver messo di riserva n mezzi,
            # il validatore ne conta fermi meno di n e respinge il piano con una
            # violazione bloccante. Un piano respinto esce dal confronto, che e'
            # esattamente il danno che questo gruppo di vincoli doveva evitare.
            #
            # Si soddisfa quindi la quota DENTRO CIASCUNA lettura: qualunque sia
            # quella che il validatore applica, quella lettura vede i suoi n
            # mezzi fermi. Si guarda prima ai mezzi su cui le letture sono
            # d'accordo, perche' fermarne uno di quelli vale per tutte e costa
            # un mezzo solo; solo se non bastano si ferma il primo n di ogni
            # lettura, e allora la baseline ne perde qualcuno in piu' del
            # necessario. Anche quel prezzo ricade a suo sfavore e non a favore
            # del risolutore, che e' il lato giusto su cui sbagliare.
            #
            # L'ordine e' sempre quello degli identificativi, per non dipendere
            # da come e' costruita la lista dei mezzi dello scenario.
            readings = _vehicle_id_readings(scenario, c.selector)
            common = readings[0].intersection(*readings[1:])
            if len(common) >= int(count):
                blocked |= set(sorted(common)[: int(count)])
            else:
                for reading in readings:
                    blocked |= set(sorted(reading)[: int(count)])
    return blocked


def _forbidden_pairs(
    scenario: Scenario, constraint_set: Optional[ConstraintSet]
) -> dict[str, frozenset[str]]:
    """Accoppiamenti mezzo-focolaio vietati da un ordine duro, per focolaio.

    FORBID_ASSIGNMENT ("l'autobotte non va sul focolaio in quota") e' l'altro
    vincolo che la baseline puo' rispettare senza pianificare: non chiede di
    ottenere niente, vieta e basta. Il validatore lo controlla come BLOCCANTE,
    quindi ignorarlo significherebbe consegnare un piano respinto.

    Il risultato e' indicizzato per focolaio perche' e' cosi' che lo consulta il
    ciclo avido: per ogni focolaio deve sapere in fretta quali mezzi saltare.
    """
    if constraint_set is None:
        return {}
    pairs: dict[str, set[str]] = {}
    for c in constraint_set.constraints:
        if c.enforcement is not Enforcement.HARD:
            continue
        if c.kind is not ConstraintKind.FORBID_ASSIGNMENT:
            continue
        vehicle_ids = _select_vehicle_ids(scenario, c.vehicle_selector)
        if not vehicle_ids:
            continue
        for fire_id in _select_fire_ids(scenario, c.fire_selector):
            pairs.setdefault(fire_id, set()).update(vehicle_ids)
    return {k: frozenset(v) for k, v in pairs.items()}


def _minutes_from_midnight(moment: datetime) -> float:
    """Minuti trascorsi dalla mezzanotte per l'istante dato.

    Si leggono i campi cosi' come sono, senza convertire fuso orario. E' la
    stessa convenzione del validatore indipendente, ed e' deliberata: l'ora del
    giorno di uno scenario e' quella che il suo `timestamp` dichiara. Se qui si
    convertisse e li' no, una finestra "fino alle otto" cadrebbe in due punti
    diversi e la baseline verrebbe respinta per un fuso orario inventato. La
    funzione e' riscritta invece che importata da `validate/` perche' la
    baseline non deve dipendere dal validatore: dipendere dal proprio giudice e'
    il modo migliore per non farsi mai contestare niente.
    """
    return moment.hour * 60.0 + moment.minute + moment.second / 60.0


def _clip_windows(
    windows: Sequence[tuple[float, float]]
) -> tuple[tuple[float, float], ...]:
    """Tiene solo gli intervalli che hanno ancora una durata positiva."""
    return tuple((lo, hi) for lo, hi in windows if hi > lo)


def _intersect_windows(
    left: Sequence[tuple[float, float]], right: Sequence[tuple[float, float]]
) -> tuple[tuple[float, float], ...]:
    """Intersezione di due elenchi di intervalli, in minuti di piano.

    Due finestre sullo stesso mezzo ("gli aerei dalle sei alle venti" e "i
    Canadair fino alle tredici") valgono entrambe: l'insieme ammesso e'
    l'intersezione, non l'unione. Prendere l'unione vorrebbe dire obbedire al
    piu' permissivo dei due ordini, che e' un modo di non obbedire a nessuno.
    """
    out: list[tuple[float, float]] = []
    for a_lo, a_hi in left:
        for b_lo, b_hi in right:
            lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
            if hi > lo:
                out.append((lo, hi))
    return tuple(sorted(out))


def _window_to_plan_minutes(
    scenario: Scenario, from_minute: int, to_minute: int, horizon_minutes: int
) -> tuple[tuple[float, float], ...]:
    """Traduce una finestra oraria negli intervalli di minuti del piano.

    `from_minute` e `to_minute` di TIME_WINDOW sono minuti dalla mezzanotte,
    cioe' orari: lo dice il docstring dello schema ("gli aerei fino alle otto")
    e lo conferma l'intervallo ammesso 0..1440. I tempi di un `Assignment` sono
    invece minuti dall'inizio dell'orizzonte. La conversione passa per
    `scenario.timestamp`, che e' l'istante a cui corrisponde il minuto zero.

    La finestra si ripete ogni giorno, quindi se ne emette una copia per ogni
    giornata toccata dall'orizzonte: un orizzonte che scavalca la mezzanotte
    deve poter usare anche la finestra del giorno dopo. Ogni copia sta dentro
    una sola giornata, e questo non e' un dettaglio: una missione che parte
    prima di mezzanotte e rientra dopo cade fuori dalla finestra del giorno in
    cui e' partita, ed e' proprio il piano che il vincolo vuole impedire.
    """
    day_start = _minutes_from_midnight(scenario.timestamp)
    horizon = float(max(0, horizon_minutes))
    days = int(horizon // MINUTES_PER_DAY) + 2
    out: list[tuple[float, float]] = []
    for day in range(days):
        opening = float(from_minute) - day_start + MINUTES_PER_DAY * day
        closing = float(to_minute) - day_start + MINUTES_PER_DAY * day
        # Una finestra gia' chiusa quando il piano comincia non serve a niente;
        # una gia' aperta si taglia allo zero, perche' il piano non puo' partire
        # prima del proprio inizio.
        if closing <= 0.0:
            continue
        out.append((max(0.0, opening), closing))
    return _clip_windows(out)


def _time_windows_by_vehicle(
    scenario: Scenario, constraint_set: Optional[ConstraintSet], horizon_minutes: int
) -> dict[str, tuple[tuple[float, float], ...]]:
    """Quando ciascun mezzo nominato da un TIME_WINDOW duro puo' operare.

    La chiave c'e' solo per i mezzi che almeno un vincolo nomina: assente
    significa "nessuna restrizione oraria", e una tupla vuota significa "questo
    mezzo non puo' operare affatto dentro l'orizzonte", che e' un esito
    legittimo di due finestre incompatibili.

    Perche' la baseline puo' permettersi di rispettare questo vincolo pur non
    pianificando: una finestra oraria non chiede un risultato, restringe
    l'insieme delle missioni ammissibili. Si traduce in un tetto sul rientro e
    in un pavimento sulla partenza, e la regola avida continua a scegliere il
    mezzo libero piu' vicino fra quelli che restano.
    """
    if constraint_set is None:
        return {}
    out: dict[str, tuple[tuple[float, float], ...]] = {}
    for c in constraint_set.constraints:
        if c.enforcement is not Enforcement.HARD:
            continue
        if c.kind is not ConstraintKind.TIME_WINDOW:
            continue
        windows = _window_to_plan_minutes(
            scenario, c.from_minute, c.to_minute, horizon_minutes
        )
        for vehicle_id in _select_vehicle_ids(scenario, c.selector):
            previous = out.get(vehicle_id)
            out[vehicle_id] = (
                windows if previous is None else _intersect_windows(previous, windows)
            )
    return out


def _unenforced_hard_constraints(
    constraint_set: Optional[ConstraintSet],
) -> list[str]:
    """Etichette dei vincoli duri che la baseline lascia cadere, in ordine stabile.

    Serve a scriverli nelle note del piano. Il gruppo 3 del docstring del modulo
    spiega perche' la baseline non li applica; qui si fa in modo che chi legge il
    confronto lo sappia senza dover leggere il codice. Una baseline zavorrata di
    nascosto e' peggio di una baseline zavorrata: la prima falsa il risultato
    senza lasciare traccia.
    """
    if constraint_set is None:
        return []
    return [
        f"{c.kind.value} {c.constraint_id}"
        for c in constraint_set.constraints
        if c.enforcement is Enforcement.HARD and c.kind in UNENFORCED_HARD_KINDS
    ]


def _turnaround_minutes(vehicle: Vehicle, bases: Sequence[Base]) -> float:
    """Riattrezzamento della base di casa del mezzo, in minuti.

    Rifornimento di carburante, cambio equipaggio, controlli: SPEC.md sezione 6
    lo impone fra due missioni consecutive dello stesso mezzo. Se la base non e'
    nello scenario si usa il default dichiarato in `domain/models.py` invece di
    zero, perche' zero sarebbe una prestazione inventata.
    """
    for b in bases:
        if b.base_id == vehicle.home_base_id:
            return max(0.0, float(b.turnaround_minutes))
    return DEFAULT_TURNAROUND_MINUTES


# --------------------------------------------------------------------------
# Ordinamenti: l'unica cosa che distingue le due baseline
# --------------------------------------------------------------------------


def _seen_stamp(fire: FireCluster) -> float:
    """Ora di rilevamento come numero, sempre letta in UTC.

    Un `datetime` senza fuso e' ambiguo, e `timestamp()` lo risolve assumendo il
    fuso della macchina: lo stesso focolaio scritto una volta con fuso e una
    volta senza finiva a due ore di distanza su questo portatile e a zero su una
    macchina impostata su UTC. L'ordine dei focolai, e quindi il piano, sarebbe
    dipeso dalle impostazioni del computer invece che dai dati, e due rigiocate
    su macchine diverse non sarebbero confrontabili. I rilevamenti FIRMS sono in
    UTC: una data ingenua si legge come UTC, non come ora locale.

    Un focolaio senza alcuna data finisce in fondo (piu' infinito) invece di
    fingere un'ora che non si conosce.
    """
    seen = fire.first_seen or fire.last_seen
    if not isinstance(seen, datetime):
        return float("inf")
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return seen.timestamp()


def _priority_stamp(fire: FireCluster) -> float:
    """Priorita' usabile come chiave di ordinamento.

    Un NaN in una chiave di ordinamento e' peggio di un valore sbagliato:
    qualunque confronto con NaN e' falso, quindi l'ordine finale dipenderebbe
    dall'ordine di partenza della lista e la rigiocata smetterebbe di essere
    riproducibile. Una priorita' non calcolabile vale zero, che e' il valore di
    default del campo in `domain/models.py`.
    """
    value = float(fire.base_priority)
    return value if math.isfinite(value) else 0.0


def _by_detection_time(fire: FireCluster) -> tuple:
    """Ordine di arrivo della segnalazione.

    Il `fire_id` spezza le parita': senza, l'ordine dipenderebbe da come e'
    costruita la lista dei focolai e la rigiocata non sarebbe riproducibile.
    """
    return (_seen_stamp(fire), fire.fire_id)


def _by_priority(fire: FireCluster) -> tuple:
    """Priorita' decrescente, poi ora di rilevamento, poi identificativo."""
    return (-_priority_stamp(fire), _seen_stamp(fire), fire.fire_id)


# --------------------------------------------------------------------------
# Motore avido condiviso dalle due baseline
# --------------------------------------------------------------------------


def _earliest_departure(
    vehicle: Vehicle,
    free_from: dict[str, float],
    turnaround: dict[str, float],
    missions_flown: dict[str, int],
) -> float:
    """Quando il mezzo puo' partire per il focolaio successivo.

    Alla prima missione del piano appena e' libero. Dalla seconda in poi vale la
    regola che SPEC.md sezione 6 ha reso esplicita:

        departure(missione k+1) >= release(missione k) + turnaround_minutes

    cioe' il mezzo, che al rilascio e' rientrato alla base, resta a terra il
    tempo di riattrezzamento della base prima di ripartire.

    Non si somma invece il volo dal focolaio precedente a quello nuovo. Quella
    lettura, che questo modulo usava prima che la specifica fosse estesa,
    contraddice la formula: `t_missione` comprende gia' `t_ritorno`, e la tratta
    verso il focolaio nuovo e' gia' dentro il `t_andata` della missione nuova,
    misurato dalla base. Contarla anche qui la conterebbe due volte e renderebbe
    la baseline piu' lenta del vero per un errore di modello invece che per la
    sua strategia: il confronto misurerebbe quell'errore, non l'ottimizzazione.

    Il mezzo mezzo minuto di margine serve solo a non arrivare esattamente sul
    limite: i minuti sono float e il validatore ricalcola per conto suo.
    """
    earliest = free_from[vehicle.vehicle_id]
    if missions_flown.get(vehicle.vehicle_id, 0) > 0:
        earliest += turnaround.get(vehicle.vehicle_id, DEFAULT_TURNAROUND_MINUTES)
        earliest += SAFETY_MARGIN_MINUTES
    return earliest


def _plan_mission_within_windows(
    vehicle: Vehicle,
    fire: FireCluster,
    scenario: Scenario,
    *,
    origin: GeoPoint,
    earliest_minute: float,
    minutes_flown: float,
    remaining_liters: int,
    horizon_minutes: int,
    windows: Optional[tuple[tuple[float, float], ...]],
) -> Optional[_Mission]:
    """Missione compatibile con le finestre orarie imposte al mezzo, se ce ne sono.

    `windows` a None significa "nessun TIME_WINDOW nomina questo mezzo", ed e'
    diverso da una tupla vuota, che significa "le finestre che lo nominano non
    lasciano nemmeno un minuto dentro l'orizzonte": nel primo caso si pianifica
    senza tetti aggiuntivi, nel secondo il mezzo non e' impiegabile e la
    funzione restituisce None.

    Quando le finestre ci sono, la missione deve stare INTERA dentro una di
    esse, partenza e rientro compresi. E' la stessa lettura del validatore, che
    ancora la finestra alla mezzanotte del giorno in cui il mezzo parte: una
    missione a cavallo della chiusura e' esattamente il piano che il vincolo
    vuole impedire, non un dettaglio di arrotondamento. Si prova la prima
    finestra utile e si tiene la prima missione che ci sta: la baseline non
    sceglie l'istante migliore, prende il primo che il vincolo le lascia.
    """
    if windows is None:
        return _plan_mission(
            vehicle,
            fire,
            scenario,
            origin=origin,
            earliest_minute=earliest_minute,
            minutes_flown=minutes_flown,
            remaining_liters=remaining_liters,
            horizon_minutes=horizon_minutes,
        )
    for opening, closing in windows:
        if closing <= earliest_minute:
            continue
        mission = _plan_mission(
            vehicle,
            fire,
            scenario,
            origin=origin,
            earliest_minute=max(earliest_minute, opening),
            minutes_flown=minutes_flown,
            remaining_liters=remaining_liters,
            horizon_minutes=horizon_minutes,
            latest_release_minute=closing,
        )
        if mission is not None:
            return mission
    return None


def _greedy_plan(
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet],
    *,
    order_key: Callable[[FireCluster], tuple],
    producer: str,
    max_vehicles_per_fire: int,
    plan_id: Optional[str],
) -> Plan:
    """Scorre i focolai nell'ordine dato e manda il mezzo libero piu' vicino.

    Lo scenario non viene mai modificato: la disponibilita' dei mezzi si tiene in
    dizionari locali. Se la baseline sporcasse lo scenario, il solver che gira
    dopo sulla stessa finestra partirebbe da uno stato diverso e il confronto
    sarebbe falsato.

    `constraint_set` e' l'ordine del coordinatore gia' tradotto, e puo' mancare:
    senza ordine la regola avida e' quella di sempre. Quando c'e', i quattro
    divieti del gruppo 1 restringono i candidati (mezzi tolti dal tavolo, coppie
    vietate, finestre orarie) e i vincoli duri di risultato che la baseline non
    sa onorare finiscono dichiarati nelle note del piano.
    """
    started = time.perf_counter()
    horizon = int(scenario.horizon_minutes or DEFAULT_HORIZON_MINUTES)
    # Un tetto negativo non vuol dire niente e un tetto zero vuol dire "non
    # mandare nessuno": si accetta il secondo e si normalizza il primo, invece
    # di lasciare che un parametro assurdo produca in silenzio un piano vuoto
    # che sembra un risultato.
    max_vehicles_per_fire = max(0, int(max_vehicles_per_fire))

    blocked = _vehicles_blocked_by(scenario, constraint_set)
    forbidden = _forbidden_pairs(scenario, constraint_set)
    windows = _time_windows_by_vehicle(scenario, constraint_set, horizon)
    dropped = _unenforced_hard_constraints(constraint_set)
    fleet = [
        v
        for v in scenario.vehicles
        if _is_usable(v, scenario) and v.vehicle_id not in blocked
    ]

    free_from: dict[str, float] = {}
    # Punto operativo di ciascun mezzo, fissato una volta per tutto il piano.
    # NON si aggiorna dopo una missione, e la ragione e' che una missione
    # termina dove e' cominciata (SPEC.md sezione 6: `t_missione` comprende
    # `t_ritorno`). Muovere l'origine di missione in missione faceva calcolare
    # alla baseline un avvicinamento diverso da quello che il validatore
    # ricalcola per la stessa assegnazione, con il risultato di far respingere
    # piani corretti: vedi il commento sul rientro in `_plan_mission`.
    origin_of: dict[str, GeoPoint] = {}
    flown: dict[str, float] = {}
    # Riattrezzamento della base di ciascun mezzo e quante missioni ha gia'
    # svolto dentro QUESTO piano: servono a far rispettare il tempo a terra fra
    # due missioni consecutive (SPEC.md sezione 6).
    turnaround: dict[str, float] = {}
    missions_flown: dict[str, int] = {}
    for v in fleet:
        origin = _origin_point(v, scenario.bases)
        if origin is None:
            # Mezzo senza posizione nota e senza base nello scenario: non
            # sappiamo da dove parte, quindi non lo si puo' impiegare onestamente.
            continue
        origin_of[v.vehicle_id] = origin
        free_from[v.vehicle_id] = max(0.0, float(v.available_from_minute))
        flown[v.vehicle_id] = float(v.minutes_flown_today)
        turnaround[v.vehicle_id] = _turnaround_minutes(v, scenario.bases)
        missions_flown[v.vehicle_id] = 0

    usable = [v for v in fleet if v.vehicle_id in origin_of]

    assignments: list[Assignment] = []
    unassigned: list[str] = []

    for fire in sorted(scenario.fires, key=order_key):
        remaining = int(max(0, fire.water_demand_liters))
        used_here: set[str] = set()
        vietati = forbidden.get(fire.fire_id, frozenset())
        while len(used_here) < max_vehicles_per_fire:
            # "Il piu' vicino" si misura dal punto operativo del mezzo, che nella
            # prima finestra puo' non essere la base di casa (stato ereditato
            # dalla finestra precedente) e che resta lo stesso per tutto il
            # piano, perche' ogni missione riporta il mezzo da dove e' partito.
            candidates = sorted(
                (
                    v
                    for v in usable
                    if v.vehicle_id not in used_here and v.vehicle_id not in vietati
                ),
                key=lambda v: (
                    travel_km(origin_of[v.vehicle_id], fire.centroid, v.domain),
                    v.vehicle_id,
                ),
            )
            chosen: Optional[tuple[Vehicle, _Mission]] = None
            for v in candidates:
                mission = _plan_mission_within_windows(
                    v,
                    fire,
                    scenario,
                    origin=origin_of[v.vehicle_id],
                    earliest_minute=_earliest_departure(
                        v, free_from, turnaround, missions_flown
                    ),
                    minutes_flown=flown[v.vehicle_id],
                    # Un focolaio senza fabbisogno dichiarato riceve comunque
                    # almeno un lancio: e' un focolaio, non un dato mancante.
                    remaining_liters=remaining if remaining > 0 else 1,
                    horizon_minutes=horizon,
                    windows=windows.get(v.vehicle_id),
                )
                if mission is not None:
                    chosen = (v, mission)
                    break
            if chosen is None:
                break

            vehicle, mission = chosen
            assignments.append(
                Assignment(
                    vehicle_id=vehicle.vehicle_id,
                    fire_id=fire.fire_id,
                    departure_minute=mission.departure_minute,
                    arrival_minute=mission.arrival_minute,
                    sorties=mission.sorties,
                    water_delivered_liters=mission.water_liters,
                    release_minute=mission.release_minute,
                    water_source_id=mission.water_source_id,
                    estimated_cost_eur=mission.cost_eur,
                    note=f"{producer}: mezzo disponibile piu' vicino",
                )
            )
            used_here.add(vehicle.vehicle_id)
            # `origin_of` non si tocca: la missione ha riportato il mezzo dove
            # era, quindi quella successiva riparte dallo stesso punto.
            free_from[vehicle.vehicle_id] = mission.release_minute
            flown[vehicle.vehicle_id] += mission.engaged_minutes
            missions_flown[vehicle.vehicle_id] += 1
            remaining -= mission.water_liters
            if remaining <= 0:
                break

        if not used_here:
            unassigned.append(fire.fire_id)

    elapsed = time.perf_counter() - started
    ordering = "priorita'" if producer == PRODUCER_PRIORITY else "ora di rilevamento"
    notes = [
        "Baseline avida: un focolaio alla volta, nessuno sguardo in avanti.",
        f"Ordine dei focolai: {ordering}.",
        f"Tetto di mezzi per focolaio: {max_vehicles_per_fire} (taratura dichiarata).",
    ]
    if blocked:
        notes.append("Mezzi esclusi da vincoli duri: " + ", ".join(sorted(blocked)))
    if forbidden:
        notes.append(
            "Accoppiamenti vietati da vincoli duri su "
            + str(len(forbidden))
            + " focolai."
        )
    if windows:
        notes.append(
            "Finestre orarie applicate a " + str(len(windows)) + " mezzi."
        )
    if dropped:
        # La nota non e' un abbellimento: e' il prezzo dichiarato della scelta
        # del gruppo 3. Senza, il confronto zavorrerebbe la baseline di nascosto
        # e chi legge non saprebbe distinguere un difetto da una politica.
        notes.append(
            "Vincoli duri di risultato non applicati (servono pianificazione, "
            "che e' cio' che la baseline non fa): " + ", ".join(dropped)
        )

    return Plan(
        plan_id=plan_id or f"{producer.upper()}_{scenario.scenario_id}",
        horizon_minutes=horizon,
        assignments=assignments,
        unassigned_fire_ids=sorted(unassigned),
        producer=producer,
        # La costruzione avida e' fattibile per costruzione: ogni missione entra
        # nel piano solo dopo che autonomia, turno e orizzonte sono stati
        # verificati. Lo stato si dichiara con lo stesso vocabolario del solver
        # perche' metriche e validatore trattino i due piani allo stesso modo.
        solver_status="FEASIBLE",
        solve_seconds=round(elapsed, 6),
        # Nessuna funzione obiettivo: la baseline non ottimizza niente e
        # scrivere un numero qui darebbe l'idea sbagliata.
        objective_value=None,
        constraint_set_id=(
            constraint_set.constraint_set_id if constraint_set is not None else None
        ),
        # Le metriche le calcola eval/: il pianificatore non si valuta da solo.
        metrics=None,
        notes=notes,
    )


# --------------------------------------------------------------------------
# Le due baseline pubbliche
# --------------------------------------------------------------------------


def plan_nearest(
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    *,
    max_vehicles_per_fire: int = MAX_VEHICLES_PER_FIRE,
    plan_id: Optional[str] = None,
) -> Plan:
    """Manda il mezzo libero piu' vicino, focolai in ordine di rilevamento.

    E' la regola banale del confronto: nessuna priorita', nessuno sguardo in
    avanti, si risponde alle segnalazioni nell'ordine in cui arrivano.

    `constraint_set` ha lo stesso posto e lo stesso significato che ha in
    `optimize.solver.solve`, ed e' voluto: il motore di rigiocata passa l'ordine
    del coordinatore a entrambi i pianificatori senza sapere quale sta
    invocando. Senza ordine il comportamento e' quello di sempre; con un ordine,
    la baseline obbedisce ai divieti duri (RESERVE_VEHICLE, EXCLUDE_VEHICLE,
    TIME_WINDOW, FORBID_ASSIGNMENT) e ignora di proposito le preferenze, che e'
    la distinzione su cui si regge il confronto. Il docstring del modulo la
    spiega per esteso, gruppo per gruppo.
    """
    return _greedy_plan(
        scenario,
        constraint_set,
        order_key=_by_detection_time,
        producer=PRODUCER_NEAREST,
        max_vehicles_per_fire=max_vehicles_per_fire,
        plan_id=plan_id,
    )


def plan_priority(
    scenario: Scenario,
    constraint_set: Optional[ConstraintSet] = None,
    *,
    max_vehicles_per_fire: int = MAX_VEHICLES_PER_FIRE,
    plan_id: Optional[str] = None,
) -> Plan:
    """Come `plan_nearest`, ma i focolai si servono in ordine di priorita'.

    Unica differenza rispetto alla baseline temporale: l'ordine. Serve a
    separare il guadagno dovuto al solo ordinamento da quello dovuto
    all'ottimizzazione vera.

    L'ordine e' quello della priorita' di base del focolaio, calcolata
    dall'arricchimento territoriale: non e' PRIORITIZE_ZONE, che resta un
    vincolo morbido e che nessuna delle due baseline applica.
    """
    return _greedy_plan(
        scenario,
        constraint_set,
        order_key=_by_priority,
        producer=PRODUCER_PRIORITY,
        max_vehicles_per_fire=max_vehicles_per_fire,
        plan_id=plan_id,
    )


#: Registro dei pianificatori banali, per nome del produttore. Il motore di
#: rigiocata accetta qualunque callable con questa firma, quindi le baseline si
#: passano al replay senza che il replay debba conoscerle.
BASELINE_PLANNERS: dict[str, Callable[..., Plan]] = {
    PRODUCER_NEAREST: plan_nearest,
    PRODUCER_PRIORITY: plan_priority,
}


def available_baselines() -> list[str]:
    """Nomi delle baseline disponibili, in ordine stabile."""
    return sorted(BASELINE_PLANNERS)
