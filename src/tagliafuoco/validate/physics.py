"""Modello fisico del validatore, riscritto da zero.

PERCHE' QUESTO FILE ESISTE
--------------------------
In `optimize/` esiste gia' un modello fisico: tempi di volo, cicli d'acqua,
autonomia, turni. Qui lo stesso modello e' scritto una seconda volta, da capo,
partendo dalla sezione 6 di SPEC.md e non dal codice del solver. Questo modulo
NON importa nulla da `optimize/`, nemmeno una funzione di distanza, e la
regola vale anche per le utility: se il validatore riusasse il codice del
solver verificherebbe il solver con se stesso, e un errore di modellazione
resterebbe invisibile.

L'idea si chiama programmazione a N versioni (N-version programming): si
scrivono due implementazioni indipendenti della stessa specifica e si
confrontano le uscite. Il valore non sta nel fatto che due implementazioni
siano piu' affidabili di una, sta nel fatto che sbagliano in modi DIVERSI. Un
errore di battitura, un'unita' di misura sbagliata, un fattore dimenticato:
sono difetti che raramente si ripetono identici in due stesure separate.
Quindi il disaccordo fra le due implementazioni e' un segnale, e il silenzio
del validatore e' una conferma debole ma reale.

IL LIMITE ONESTO DELLA TECNICA
------------------------------
La tecnica verifica la conformita' a una specifica, non la verita' della
specifica. Se SPEC.md sbaglia, per esempio se la costante di 100.000 litri per
km quadrato e' lontana dalla realta' o se il fattore di tortuosita' 1.45 non
descrive le strade della Sila, allora solver e validatore sbagliano allo
stesso modo, si trovano d'accordo, e il validatore non se ne accorge. La
letteratura su N-version programming dice anche di piu': gli errori delle
versioni indipendenti non sono statisticamente indipendenti, perche' chi
scrive parte dallo stesso documento e inciampa negli stessi punti difficili.
Un validatore verde significa "il piano e' coerente con il modello
dichiarato", mai "il piano funzionerebbe davvero su un incendio vero".

SCELTE DI SCRITTURA DELIBERATAMENTE DIVERSE
-------------------------------------------
La distanza geodetica e' calcolata con la formula di Vincenty su sfera
(arcotangente a due argomenti) invece che con l'haversine (arcoseno) indicata
in SPEC. Le due formule descrivono la stessa grandezza sulla stessa sfera di
raggio 6371 km e devono dare lo stesso numero: se lo danno e' una conferma
incrociata, se divergono uno dei due moduli ha un errore. La diversita' e'
voluta, non e' una svista.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import atan2, ceil, cos, isfinite, radians, sin, sqrt
from typing import Optional

from tagliafuoco.config import (
    DAYLIGHT_END_MINUTE,
    DAYLIGHT_START_MINUTE,
    EARTH_RADIUS_KM,
    FRP_NORMALISATION_MW,
    LAND_COVER_WATER_COEFF,
    ROAD_TORTUOSITY,
    WATER_LITERS_PER_KM2,
)
from tagliafuoco.domain.models import (
    Domain,
    FireCluster,
    GeoPoint,
    Scenario,
    Vehicle,
    WaterSource,
)

# Minuti in un giorno. E' scritto qui e non in `config` perche' non e' una
# taratura: e' una definizione. Sta in una costante e non ripetuto a mano nel
# codice perche' il numero 1440 comparso tre volte di fila e' il posto tipico
# in cui si scrive 1400 per sbaglio e nessuno se ne accorge.
MINUTES_PER_DAY = 1440.0

# --------------------------------------------------------------------------
# Distanza
# --------------------------------------------------------------------------


def geodesic_km(a: GeoPoint, b: GeoPoint) -> float:
    """Distanza sulla sfera fra due punti, in chilometri.

    Formula di Vincenty nel caso sferico. SPEC prescrive l'haversine; qui si
    usa la forma con arcotangente a due argomenti, che calcola lo stesso
    angolo per una strada diversa:

        num = sqrt( (cos f2 sin dl)^2 + (cos f1 sin f2 - sin f1 cos f2 cos dl)^2 )
        den = sin f1 sin f2 + cos f1 cos f2 cos dl
        angolo = atan2(num, den)

    L'haversine e' numericamente fragile sui punti antipodali, questa forma
    no; sulle distanze calabresi le due coincidono fino all'ultima cifra utile
    (la differenza relativa e' dell'ordine di 1e-12). Proprio per questo il
    confronto e' informativo: se un giorno le due implementazioni divergessero
    di chilometri, la causa non sarebbe la matematica ma un errore di unita' o
    di argomenti invertiti da qualche parte.
    """
    f1 = radians(a.lat)
    f2 = radians(b.lat)
    dl = radians(b.lon - a.lon)

    sin_f1, cos_f1 = sin(f1), cos(f1)
    sin_f2, cos_f2 = sin(f2), cos(f2)
    sin_dl, cos_dl = sin(dl), cos(dl)

    x = cos_f2 * sin_dl
    y = cos_f1 * sin_f2 - sin_f1 * cos_f2 * cos_dl
    numerator = sqrt(x * x + y * y)
    denominator = sin_f1 * sin_f2 + cos_f1 * cos_f2 * cos_dl

    return EARTH_RADIUS_KM * atan2(numerator, denominator)


def travel_distance_km(vehicle: Vehicle, a: GeoPoint, b: GeoPoint) -> float:
    """Distanza che il mezzo percorre davvero fra due punti.

    I mezzi aerei volano in linea retta, quindi la distanza e' quella
    geodetica. I mezzi terrestri seguono le strade: la geodetica viene
    moltiplicata per il fattore di tortuosita' letto da `config`
    (ROAD_TORTUOSITY, taratura dichiarata a 1.45 per territorio montano).
    """
    straight = geodesic_km(a, b)
    if vehicle.domain is Domain.AIR:
        return straight
    return straight * ROAD_TORTUOSITY


# --------------------------------------------------------------------------
# Tempi
# --------------------------------------------------------------------------


def travel_minutes(distance_km: float, speed_kmh: float) -> float:
    """Minuti per coprire una distanza a velocita' costante.

    Velocita' nulla o negativa non e' un caso fisico: si restituisce infinito
    cosi' che qualunque confronto con un tempo dichiarato risulti violato,
    invece di sollevare una divisione per zero dentro una regola.
    """
    if speed_kmh <= 0.0:
        return float("inf")
    return distance_km / speed_kmh * 60.0


def leg_minutes(vehicle: Vehicle, a: GeoPoint, b: GeoPoint) -> float:
    """Minuti di una singola tratta a-b per questo mezzo."""
    return travel_minutes(travel_distance_km(vehicle, a, b), vehicle.cruise_speed_kmh)


def pickup_minutes(
    vehicle: Vehicle,
    source: Optional[WaterSource],
    optimistic: bool = False,
) -> float:
    """Minuti di pescaggio o rifornimento a una sorgente.

    Il tempo dipende da due dati che possono discordare: `vehicle.refill_minutes`
    (quanto ci mette quel mezzo a caricare) e `source.refill_minutes` (quanto
    quella sorgente permette di caricare). Il modello nominale prende il
    massimo dei due, perche' nessuno dei due limiti si puo' aggirare.

    Con `optimistic=True` si prende il minimo. Serve per le regole che devono
    dichiarare IMPOSSIBILE un tempo: li' il validatore deve usare il limite
    inferiore piu' generoso possibile, altrimenti segnala come errore una
    semplice differenza di modellazione (il solver potrebbe aver considerato
    solo il dato del mezzo). Un allarme deve nascere da una violazione della
    fisica, non da una scelta di modello diversa.
    """
    if source is None:
        return vehicle.refill_minutes
    if optimistic:
        return min(vehicle.refill_minutes, source.refill_minutes)
    return max(vehicle.refill_minutes, source.refill_minutes)


def water_cycle_minutes(
    vehicle: Vehicle,
    fire_point: GeoPoint,
    source_point: Optional[GeoPoint],
    source: Optional[WaterSource] = None,
    optimistic: bool = False,
) -> float:
    """Durata di un ciclo d'acqua completo, secondo SPEC sezione 6.

    Il ciclo e': dal focolaio alla sorgente, pescaggio, ritorno al focolaio,
    sgancio. Da cui il fattore 2 sulla distanza focolaio-sorgente.

        t_ciclo = d(fuoco, sorgente) * 2 / v * 60 + t_pescaggio + t_sgancio

    Se non c'e' sorgente il mezzo scarica solo il carico che ha a bordo: resta
    il tempo di sgancio, senza spostamento ne' pescaggio.
    """
    drop = vehicle.drop_minutes
    if source_point is None:
        return drop
    hop = leg_minutes(vehicle, fire_point, source_point) * 2.0
    return hop + pickup_minutes(vehicle, source, optimistic=optimistic) + drop


@dataclass(frozen=True)
class MissionTiming:
    """Scomposizione dei tempi di una missione, ricalcolata dal validatore.

    Tenere separati i pezzi invece di restituire un solo numero serve alla
    diagnostica: quando il totale non torna, il messaggio di violazione puo'
    dire quale pezzo e' fuori posto.
    """

    setup_minutes: float
    outbound_minutes: float
    cycle_minutes: float
    sorties: int
    return_minutes: float
    distance_to_fire_km: float
    distance_to_source_km: float

    @property
    def water_minutes(self) -> float:
        """Minuti spesi nei cicli d'acqua.

        Il caso "nessun lancio" e' trattato a parte invece di lasciar fare la
        moltiplicazione per zero. La ragione non e' estetica: `cycle_minutes`
        vale infinito quando la velocita' di crociera dichiarata e' nulla, e
        in virgola mobile `inf * 0` vale NaN. Un NaN si propaga nel totale, e
        ogni confronto con un NaN e' falso: la regola sull'autonomia
        smetterebbe di reagire proprio sul mezzo con i dati piu' rotti, cioe'
        nel caso in cui serve di piu'.
        """
        if self.sorties <= 0:
            return 0.0
        return self.cycle_minutes * self.sorties

    @property
    def total_minutes(self) -> float:
        """t_missione = t_setup + t_andata + n_lanci * t_ciclo + t_ritorno."""
        return (
            self.setup_minutes
            + self.outbound_minutes
            + self.water_minutes
            + self.return_minutes
        )

    @property
    def minimum_arrival_minutes(self) -> float:
        """Minuti minimi fra partenza e arrivo, contando il solo volo.

        Il setup e' escluso di proposito: SPEC lo mette dentro t_missione ma
        non dice se cade prima o dopo l'istante chiamato `departure_minute`.
        Per dichiarare impossibile un arrivo si usa il limite piu' generoso,
        cioe' il solo tempo di percorrenza.
        """
        return self.outbound_minutes


def vehicle_origin(vehicle: Vehicle, scenario: Scenario) -> Optional[GeoPoint]:
    """Da dove parte il mezzo: posizione corrente se nota, altrimenti la base.

    Nella simulazione un mezzo puo' essere gia' in volo o rientrato altrove:
    `current_position` ha la precedenza sulla base di appartenenza.
    """
    if vehicle.current_position is not None:
        return vehicle.current_position
    base = scenario.base_by_id(vehicle.home_base_id)
    return base.point if base is not None else None


def mission_timing(
    vehicle: Vehicle,
    origin: GeoPoint,
    fire_point: GeoPoint,
    source: Optional[WaterSource],
    sorties: int,
    return_point: Optional[GeoPoint] = None,
    optimistic: bool = False,
) -> MissionTiming:
    """Ricostruisce i tempi di una missione dalle sole grandezze fisiche.

    Il rientro e' calcolato dal focolaio al punto di rientro (per default la
    stessa origine): dopo l'ultimo sgancio il mezzo e' sopra l'incendio, non
    sulla sorgente.
    """
    source_point = source.point if source is not None else None
    back_to = return_point if return_point is not None else origin
    return MissionTiming(
        setup_minutes=vehicle.setup_minutes,
        outbound_minutes=leg_minutes(vehicle, origin, fire_point),
        cycle_minutes=water_cycle_minutes(
            vehicle, fire_point, source_point, source, optimistic=optimistic
        ),
        sorties=sorties,
        return_minutes=leg_minutes(vehicle, fire_point, back_to),
        distance_to_fire_km=travel_distance_km(vehicle, origin, fire_point),
        distance_to_source_km=(
            travel_distance_km(vehicle, fire_point, source_point)
            if source_point is not None
            else 0.0
        ),
    )


# --------------------------------------------------------------------------
# Acqua
# --------------------------------------------------------------------------


def deliverable_liters(vehicle: Vehicle, sorties: int) -> int:
    """Litri massimi che un mezzo puo' erogare in un numero dato di lanci.

    Un lancio scarica al piu' la capacita' del serbatoio: nessun modello puo'
    tirare fuori piu' acqua di capacita' per numero di cicli. Il risultato non
    scende mai sotto zero nemmeno con dati statici assurdi (capacita'
    negativa): questo numero finisce in un confronto "il piano dichiara piu'
    di quanto sia possibile", e un massimo negativo renderebbe colpevole
    qualsiasi piano, compresi quelli che non erogano niente.
    """
    if sorties <= 0:
        return 0
    return max(0, vehicle.water_capacity_liters) * sorties


def required_sorties(vehicle: Vehicle, liters: float) -> int:
    """Numero minimo di lanci per erogare una quantita' d'acqua.

    Si arrotonda sempre per eccesso: mezzo lancio non esiste, e un fabbisogno
    residuo di un litro costa comunque un ciclo intero. Il conto si fa sugli
    interi quando la quantita' e' intera, perche' la divisione in virgola
    mobile su numeri grandi puo' restituire 2.0000000000000004 dove il
    risultato esatto e' 2 e far comparire un lancio che non serve.

    Capacita' nulla o negativa (dato statico rotto) restituisce 0: il numero
    di lanci "giusto" sarebbe infinito, e un intero infinito non esiste. Chi
    chiama deve trattare la capacita' non positiva come un caso a parte.
    """
    if not isfinite(liters) or liters <= 0:
        return 0
    capacity = vehicle.water_capacity_liters
    if capacity <= 0:
        return 0
    if float(liters).is_integer():
        quotient, remainder = divmod(int(liters), capacity)
        return quotient + (1 if remainder else 0)
    return ceil(liters / capacity)


def water_demand_liters(fire: FireCluster) -> float:
    """Fabbisogno d'acqua del focolaio, ricalcolato dalle formule di SPEC.

        litri = area_km2 * 100_000 * coeff_copertura * coeff_intensita
        coeff_intensita = 1 + min(1.0, max_frp_mw / 200)

    I coefficienti di copertura e le due costanti si leggono da `config`: sono
    dati di taratura condivisi, non logica, quindi leggerli da li' non rompe
    l'indipendenza del validatore. Quello che non si condivide e' il modo di
    combinarli, ed e' riscritto qui.

    Il coefficiente di intensita' satura a 2.0: oltre i 200 MW di potenza
    radiativa il modello smette di crescere. E' una scelta di taratura di
    SPEC, non una legge fisica, e va letta come tale.
    """
    cover_coeff = LAND_COVER_WATER_COEFF.get(fire.land_cover.value, 1.0)
    intensity_coeff = 1.0 + min(1.0, fire.max_frp_mw / FRP_NORMALISATION_MW)
    return fire.area_km2 * WATER_LITERS_PER_KM2 * cover_coeff * intensity_coeff


def source_is_usable(vehicle: Vehicle, source: WaterSource) -> bool:
    """Vero se quel mezzo puo' attingere da quella sorgente.

    Una lista `usable_by` vuota e' letta come "nessuna restrizione dichiarata":
    e' il caso dei dati statici incompleti, e trattarla come "nessuno puo'
    usarla" produrrebbe una valanga di falsi allarmi sui dati di fixture.
    """
    if not source.usable_by:
        return True
    return vehicle.kind in source.usable_by


def nearest_usable_source(
    vehicle: Vehicle,
    fire: FireCluster,
    scenario: Scenario,
) -> tuple[Optional[WaterSource], float]:
    """Sorgente compatibile piu' vicina al focolaio, con la sua distanza.

    Restituisce (None, inf) se non ne esiste nessuna: e' il caso che la regola
    sull'acqua deve segnalare, perche' un mezzo che richiede rifornimento e
    non ha dove rifornirsi non puo' fare i cicli che il piano gli attribuisce.

    A parita' di distanza vince la prima sorgente nell'ordine in cui lo
    scenario le elenca (il confronto e' stretto, non vale il minore o uguale).
    Sembra un dettaglio e non lo e': la rigiocata della stagione confronta
    Tagliafuoco con la baseline sugli stessi scenari, e una scelta che cambia
    da un'esecuzione all'altra renderebbe i numeri non riproducibili. Per lo
    stesso motivo qui non si itera mai su un insieme o su un dizionario di
    passaggio, che in Python non garantiscono un ordine legato ai dati.
    """
    best: Optional[WaterSource] = None
    best_km = float("inf")
    for source in scenario.water_sources:
        if not source_is_usable(vehicle, source):
            continue
        km = travel_distance_km(vehicle, fire.centroid, source.point)
        if km < best_km:
            best, best_km = source, km
    return best, best_km


# --------------------------------------------------------------------------
# Orologio e luce del giorno
# --------------------------------------------------------------------------


def minutes_from_midnight(moment: datetime) -> float:
    """Minuti trascorsi dalla mezzanotte, per l'istante dato.

    Si leggono i campi dell'oggetto cosi' come sono, senza convertire fuso
    orario: l'ora del giorno di uno `Scenario` e' quella che il suo
    `timestamp` dichiara. La conseguenza va detta, perche' e' una trappola:
    se la pipeline costruisce gli scenari in UTC, le soglie di luce lette da
    `config` (che sono orari locali medi di stagione) risultano spostate di
    un'ora o due rispetto al sole vero della Calabria. La scelta di non
    convertire e' deliberata. Convertire qui significherebbe inventarsi un
    fuso, e un validatore che aggiusta i dati di nascosto e' peggio di uno che
    li legge alla lettera: chi cambia convenzione deve vederlo subito sul
    confine fra i moduli, non scoprirlo dentro una regola.

    Tutto il pacchetto `validate` legge i datetime solo attraverso questa
    funzione e non ne confronta mai due fra loro: cosi' un istante naive e uno
    con fuso possono convivere nello stesso scenario senza far esplodere il
    validatore con un TypeError a meta' validazione.
    """
    return moment.hour * 60.0 + moment.minute + moment.second / 60.0


def clock_minute(scenario_start: datetime, plan_minute: float) -> float:
    """Converte un minuto del piano nell'ora del giorno corrispondente.

    I tempi del piano sono minuti dall'inizio dell'orizzonte (vedi
    `Assignment`), non orari assoluti. Per sapere se un volo cade dentro la
    luce del giorno serve riportarli sull'orologio, e il modulo sul giorno
    gestisce gli orizzonti che scavalcano la mezzanotte. Il modulo di Python
    restituisce un valore non negativo anche su argomenti negativi, quindi un
    minuto di piano negativo (piano malformato) da' comunque un'ora del
    giorno leggibile invece di un numero assurdo.

    Attenzione a cosa si perde: dopo il modulo non si sa piu' in quale giorno
    si e' finiti. Le regole che devono distinguere "alle dieci" da "alle dieci
    di domani" (la finestra temporale) non possono usare questa funzione per
    il confronto e lavorano sui minuti assoluti; qui resta l'uso per cui serve,
    che e' dire un orario a chi legge.
    """
    return (minutes_from_midnight(scenario_start) + plan_minute) % MINUTES_PER_DAY


def is_daylight(clock: float) -> bool:
    """Vero se l'ora del giorno cade nella finestra di luce tarata in config.

    DAYLIGHT_START_MINUTE e DAYLIGHT_END_MINUTE sono orari medi di stagione,
    non effemeridi: sono una taratura, e il validatore lo dice nei messaggi.
    """
    return DAYLIGHT_START_MINUTE <= clock <= DAYLIGHT_END_MINUTE
