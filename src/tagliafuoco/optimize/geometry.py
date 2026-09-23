"""Geometria e fisica di missione, versione del SOLVER.

Perche' questo modulo esiste come file a se'
--------------------------------------------
Il progetto ha due implementazioni separate della stessa fisica: una qui, in
`optimize/`, che serve a costruire il modello CP-SAT, e una in `validate/`,
che ricalcola tutto da zero sul piano gia' prodotto. Le due non si parlano e
non si importano a vicenda. Se condividessero il codice, un errore di formula
verrebbe commesso due volte nello stesso modo e il validatore direbbe "tutto
a posto" su un piano sbagliato. Un validatore che eredita l'errore che deve
scoprire non valida niente.

Quindi: ogni funzione di distanza o di tempo usata dal solver sta qui dentro,
e il solver non ne usa altre.

Il fattore di scala dei tempi
-----------------------------
CP-SAT e' un risolutore su interi: non esistono variabili continue. Tutti i
tempi vengono percio' convertiti in unita' intere. L'unita' scelta e' il
decimo di minuto (6 secondi), cioe' `TIME_SCALE = 10`. E' abbastanza fine da
non perdere informazione utile (nessuno pianifica un Canadair al secondo) e
abbastanza grossa da tenere i domini piccoli: un orizzonte di 240 minuti
diventa 2400 unita'.

Regola di arrotondamento, sempre la stessa: le DURATE si arrotondano per
eccesso e i BUDGET (autonomia, turno, orizzonte) per difetto. Cosi' il
modello non si concede mai un minuto che nella realta' non avrebbe, e un
piano dichiarato fattibile dal solver resta fattibile agli occhi del
validatore anche se questo arrotonda in modo leggermente diverso.

L'asimmetria e' voluta e va saputa difendere: arrotondare tutto allo stesso
modo (per esempio sempre al piu' vicino) farebbe guadagnare al solver fino a
tre secondi per durata e altrettanti per budget, e su una missione con venti
cicli l'errore si accumula nella direzione sbagliata. Con questa regola
l'errore si accumula sempre a sfavore del piano, che e' il lato sicuro.

Niente orologio di sistema, nessun confronto fra datetime
----------------------------------------------------------
In tutto il sottosistema `optimize/` non si legge mai l'ora corrente: niente
`datetime.now()`, niente dipendenza dal momento in cui gira il codice. E'
quello che rende il piano riproducibile: un test lanciato a mezzanotte e uno
lanciato a mezzogiorno devono ottenere lo stesso risultato.

`Scenario.timestamp` invece si legge, ma in un posto solo e per un motivo
solo: due campi dello schema dei vincoli (`TimeWindow.from_minute` e
`TimeWindow.to_minute`) sono ORARI, cioe' minuti dalla mezzanotte, mentre
tutti i tempi di un `Assignment` sono minuti dall'inizio dell'orizzonte.
Convertire fra le due scale richiede di sapere a che ora del giorno comincia
l'orizzonte, e quell'informazione sta solo li'. La conversione vive in
`minutes_from_midnight` e `daily_window_to_plan_minutes`, sotto.

Due precauzioni che vanno sapute difendere:

1. `Scenario.timestamp` e' un DATO dello scenario, non l'orologio della
   macchina: leggerlo non introduce non determinismo, perche' due esecuzioni
   sullo stesso scenario leggono lo stesso istante.
2. Qui non si confronta mai un `datetime` con un altro e non si converte mai
   un fuso orario: si leggono i campi ora/minuto/secondo cosi' come sono
   scritti. E' la stessa lettura letterale che fa il validatore, per conto
   suo. Cosi' uno scenario con istante naive e uno con istante consapevole del
   fuso attraversano il solver allo stesso modo, e il classico
   `TypeError: can't compare offset-naive and offset-aware datetimes` non puo'
   proprio presentarsi.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

from tagliafuoco.config import EARTH_RADIUS_KM, ROAD_TORTUOSITY
from tagliafuoco.domain.models import (
    Base,
    Domain,
    FireCluster,
    GeoPoint,
    Vehicle,
    WaterSource,
)

# --------------------------------------------------------------------------
# Tarature locali del solver
# --------------------------------------------------------------------------

# Unita' di tempo del modello: decimi di minuto. Vedi il docstring del modulo.
TIME_SCALE = 10

# Minuti in un giorno. Serve alla sola conversione delle finestre orarie, che
# si ripetono ogni ventiquattro ore.
MINUTES_PER_DAY = 1440.0

# Sotto questa affidabilita' stagionale una sorgente non viene nemmeno
# considerata: un laghetto che ad agosto e' in secca sette volte su dieci non
# e' un punto di pescaggio su cui costruire un piano. Taratura dichiarata,
# non un valore misurato.
MIN_SEASONAL_RELIABILITY = 0.25

# Tetto al numero di cicli per singola assegnazione. Non e' fisica: serve a
# tenere piccolo il dominio delle variabili intere. Con cicli da pochi minuti
# (elicottero leggero su sorgente vicina) l'autonomia da sola permetterebbe
# domini molto ampi e il modello si gonfierebbe senza guadagno pratico.
MAX_SORTIES_HARD_CAP = 40

# Velocita' minima ammessa, per non dividere per zero su dati sporchi.
MIN_SPEED_KMH = 1.0


# --------------------------------------------------------------------------
# Conversione fra minuti e unita' intere
# --------------------------------------------------------------------------


def to_units_up(minutes: float) -> int:
    """Converte minuti in unita' intere arrotondando per ECCESSO.

    Da usare per le durate: meglio sovrastimare di sei secondi il tempo di
    una missione che sottostimarlo.

    L'epsilon di 1e-9 serve contro la rappresentazione binaria dei float: un
    valore che vale "esattamente" 24.0 minuti puo' arrivare qui come
    24.000000000000004, e senza epsilon `ceil` lo porterebbe a 241 invece di
    240. Sarebbe un off-by-one silenzioso che si propaga su ogni ciclo di una
    missione.

    Perche' proprio 1e-9, e perche' non e' un numero magico. Deve stare in
    mezzo fra due grandezze molto distanti. Sopra c'e' l'errore di
    rappresentazione dei float sulle grandezze di questo problema: il valore
    piu' grande che passa di qui e' un'intera giornata, 1440 minuti, cioe'
    14400 unita', dove l'epsilon di macchina vale circa 3e-12. Sotto c'e'
    l'arrotondamento vero piu' piccolo che vogliamo conservare, che e' di
    un'unita' intera. L'epsilon e' mille volte sopra il rumore dei float e un
    miliardo di volte sotto l'unita' di tempo: non puo' ne' lasciar passare il
    rumore ne' mangiarsi un arrotondamento vero.
    """
    return int(math.ceil(minutes * TIME_SCALE - 1e-9))


def to_units_down(minutes: float) -> int:
    """Converte minuti in unita' intere arrotondando per DIFETTO.

    Da usare per i budget (autonomia, turno, orizzonte): meglio concedersi
    sei secondi in meno di autonomia che sei in piu'. L'epsilon ha lo stesso
    ruolo che ha in `to_units_up`, con il segno opposto.

    Attenzione: su un valore negativo questa funzione resta coerente con se'
    stessa (floor, quindi si allontana da zero), ma un budget negativo non ha
    senso operativo. Chi la chiama su grandezze che potrebbero essere
    negative (orizzonte, turno residuo) applica un `max(0, ...)` a valle.
    """
    return int(math.floor(minutes * TIME_SCALE + 1e-9))


def from_units(units: int) -> float:
    """Riporta le unita' intere a minuti, per scrivere il piano."""
    return units / float(TIME_SCALE)


# --------------------------------------------------------------------------
# Orari del giorno contro minuti di piano
# --------------------------------------------------------------------------


def minutes_from_midnight(moment: datetime) -> float:
    """Minuti trascorsi dalla mezzanotte per l'istante dato.

    Si leggono i campi cosi' come sono, senza convertire fuso orario. Due
    ragioni, ed entrambe contano piu' della precisione astronomica.

    La prima e' che convertire significherebbe inventarsi un fuso: lo scenario
    dichiara un istante, e quale sia l'ora "vera" in Calabria dipende da come
    la pipeline ha costruito quell'istante, non da qualcosa che questo modulo
    possa dedurre. Un solver che aggiusta i dati di nascosto e' peggio di uno
    che li legge alla lettera.

    La seconda e' che leggendo i campi non si confronta mai un `datetime` con
    un altro, quindi un istante naive e uno con fuso si comportano allo stesso
    modo e non esiste il punto in cui il programma possa esplodere con
    `TypeError: can't compare offset-naive and offset-aware datetimes`.

    Il validatore, che e' scritto da tutt'altra parte e non condivide una riga
    con questo file, e' arrivato alla stessa convenzione. Il fatto che due
    implementazioni indipendenti concordino e' l'unica garanzia che abbiamo:
    se divergessero, un piano fattibile per il solver verrebbe respinto dal
    validatore e ci accorgeremmo subito di quale dei due ha torto.
    """
    return moment.hour * 60.0 + moment.minute + moment.second / 60.0


def daily_window_to_plan_minutes(
    scenario_start: datetime,
    from_minute: int,
    to_minute: int,
    horizon_minutes: int,
) -> tuple[float, float]:
    """Traduce una finestra ORARIA in minuti dall'inizio dell'orizzonte.

    Perche' serve una conversione, e perche' e' il posto dove si sbagliano le
    unita'. `TimeWindow.from_minute` e `TimeWindow.to_minute` sono minuti
    dalla MEZZANOTTE: lo dice il docstring dello schema ("gli aerei fino alle
    otto"), lo conferma l'intervallo ammesso 0..1440, lo scrive nero su bianco
    il traduttore a regole quando compila quei campi, e sulla stessa lettura
    sono costruiti il validatore e la baseline. I tempi di un `Assignment`
    sono invece minuti dall'INIZIO DELL'ORIZZONTE. Sono due scale diverse e
    l'unico ponte fra le due e' `Scenario.timestamp`, che dice a che ora del
    giorno cade il minuto zero del piano.

    Leggere quei due campi come minuti di piano, senza conversione, e' un
    errore di unita' che non si vede: il modello resta ben formato, il solver
    produce un piano, e solo il validatore indipendente scopre che le missioni
    cadono fuori dall'orario richiesto. E' esattamente il difetto che questo
    progetto esiste per far emergere.

    La finestra si ripete ogni giorno: qui si restituisce la PRIMA occorrenza
    non ancora chiusa quando il piano comincia. Se l'orizzonte scavalca la
    mezzanotte, l'occorrenza del giorno dopo non viene modellata e il solver
    resta piu' severo del necessario (perde soluzioni, non ne produce di
    illecite). E' una limitazione dichiarata, non una svista: modellare piu'
    occorrenze vorrebbe dire una disgiunzione, cioe' variabili booleane in
    piu' per ogni coppia, e con l'orizzonte di riferimento del progetto
    (`config.DEFAULT_HORIZON_MINUTES`, quattro ore) il caso non si presenta.

    Restituisce `(apertura, chiusura)` in minuti di piano. L'apertura non e'
    mai negativa, perche' il piano non puo' cominciare prima del proprio
    inizio. La chiusura puo' esserlo soltanto nel caso degenere in cui nessuna
    occorrenza sia ancora aperta, che qui non puo' accadere: il ciclo cerca
    finche' non ne trova una.
    """
    day_start = minutes_from_midnight(scenario_start)
    horizon = float(max(0, int(horizon_minutes)))
    # Quante giornate puo' toccare l'orizzonte, piu' una di margine perche' la
    # finestra di oggi puo' essere gia' chiusa quando il piano comincia.
    days = int(horizon // MINUTES_PER_DAY) + 2
    for day in range(days):
        opening = float(from_minute) - day_start + MINUTES_PER_DAY * day
        closing = float(to_minute) - day_start + MINUTES_PER_DAY * day
        if closing > 0.0:
            return (max(0.0, opening), closing)
    # Irraggiungibile con `to_minute >= 0` e `days >= 2`, ma un ritorno
    # esplicito e' meglio di un None che il chiamante dovrebbe controllare.
    return (0.0, -1.0)


# --------------------------------------------------------------------------
# Distanze
# --------------------------------------------------------------------------


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Distanza in linea d'aria su sfera, formula dell'emisenoverso.

    Si approssima la Terra a una sfera di raggio `EARTH_RADIUS_KM`. L'errore
    rispetto all'ellissoide WGS84 e' dell'ordine dello 0.3 per cento: del
    tutto trascurabile davanti alle incertezze vere di questo problema (dove
    sia esattamente il fronte di fiamma, quanto vento tira).
    """
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def road_distance_km(a: GeoPoint, b: GeoPoint) -> float:
    """Distanza stradale stimata: linea d'aria per il fattore di tortuosita'.

    `ROAD_TORTUOSITY` vale 1.45 perche' la Calabria e' montuosa (Sila,
    Aspromonte, Pollino) e la strada che collega due punti e' molto piu'
    lunga del segmento che li unisce. E' una taratura presa dalla letteratura
    sui trasporti, non una misura su questa rete stradale.
    """
    return haversine_km(a, b) * ROAD_TORTUOSITY


def travel_distance_km(vehicle: Vehicle, a: GeoPoint, b: GeoPoint) -> float:
    """Distanza che il mezzo percorre davvero fra due punti.

    I mezzi aerei volano in linea retta, quelli terrestri seguono la strada.
    """
    if vehicle.domain is Domain.AIR:
        return haversine_km(a, b)
    return road_distance_km(a, b)


def travel_minutes(vehicle: Vehicle, a: GeoPoint, b: GeoPoint) -> float:
    """Tempo di percorrenza in minuti alla velocita' di crociera del mezzo."""
    speed = max(MIN_SPEED_KMH, float(vehicle.cruise_speed_kmh))
    return travel_distance_km(vehicle, a, b) / speed * 60.0


# --------------------------------------------------------------------------
# Acqua
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WaterOption:
    """Una sorgente d'acqua gia' valutata per una coppia mezzo-focolaio."""

    source_id: str
    distance_km: float
    cycle_minutes: float
    reliability: float
    score: float


def source_usable_by(source: WaterSource, vehicle: Vehicle) -> bool:
    """Vero se il mezzo puo' rifornirsi a quella sorgente.

    Convenzione: `usable_by` vuoto significa "nessuna restrizione dichiarata",
    quindi la sorgente e' aperta a tutti. E' la lettura piu' prudente per i
    dati statici incompleti: meglio un piano in piu' da far bocciare al
    validatore che un mezzo escluso per un campo non compilato.
    """
    if not source.usable_by:
        return True
    return vehicle.kind in source.usable_by


def water_cycle_minutes(
    vehicle: Vehicle, fire: FireCluster, source: WaterSource
) -> float:
    """Durata di un ciclo completo pescaggio-sgancio.

    Il mezzo parte dal focolaio, va alla sorgente, carica, torna, sgancia:
    andata e ritorno piu' i due tempi fermi. Il tempo di caricamento e' il
    PEGGIORE fra quello dichiarato dal mezzo e quello dichiarato dalla
    sorgente, perche' e' il collo di bottiglia dei due a comandare, non la
    media dei due.
    """
    leg = travel_minutes(vehicle, fire.centroid, source.point)
    refill = max(float(vehicle.refill_minutes), float(source.refill_minutes))
    return 2.0 * leg + refill + float(vehicle.drop_minutes)


def best_water_source(
    vehicle: Vehicle,
    fire: FireCluster,
    sources: Iterable[WaterSource],
) -> Optional[WaterOption]:
    """Sceglie la sorgente migliore per una coppia mezzo-focolaio.

    Il criterio non e' la sola distanza: una sorgente vicina ma stagionalmente
    incerta vale meno di una un po' piu' lontana ma sicura. Il punteggio e'
    `ciclo / affidabilita'`, cioe' il tempo di ciclo "atteso" se con
    probabilita' `1 - affidabilita'` il pescaggio va a vuoto e il mezzo deve
    ripetere il giro. E' una lettura grossolana ma monotona e facile da
    difendere: piu' incerta e' la sorgente, piu' caro si paga il suo ciclo.

    La scelta e' fatta QUI, prima del modello, e non lasciata al solver. Se la
    sorgente fosse una variabile decisionale il modello crescerebbe di un
    fattore pari al numero di sorgenti senza quasi mai cambiare la risposta,
    perche' la sorgente migliore per una coppia mezzo-focolaio non dipende
    dalle altre assegnazioni. E' una riduzione dichiarata del problema, non
    una svista.
    """
    best: Optional[WaterOption] = None
    for src in sources:
        if not source_usable_by(src, vehicle):
            continue
        reliability = float(src.seasonal_reliability)
        if reliability < MIN_SEASONAL_RELIABILITY:
            continue
        cycle = water_cycle_minutes(vehicle, fire, src)
        option = WaterOption(
            source_id=src.source_id,
            distance_km=travel_distance_km(vehicle, fire.centroid, src.point),
            cycle_minutes=cycle,
            reliability=reliability,
            score=cycle / max(reliability, MIN_SEASONAL_RELIABILITY),
        )
        # A parita' di punteggio si ordina per source_id. Il piano deve essere
        # riproducibile, quindi niente dipendenze dall'ordine di iterazione.
        if best is None or (option.score, option.source_id) < (
            best.score,
            best.source_id,
        ):
            best = option
    return best


# --------------------------------------------------------------------------
# Profilo di missione
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MissionProfile:
    """Tutto cio' che il modello CP-SAT deve sapere su una coppia mezzo-focolaio.

    E' il ponte fra la fisica (questo modulo) e la combinatoria (`model.py`).
    I campi che finiscono in `_units` sono gia' in unita' intere di tempo: il
    costruttore del modello non deve piu' fare nessun arrotondamento, e quindi
    non puo' sbagliarlo in un modo diverso da qui.

    `max_sorties` e' il campo importante: e' il numero massimo di cicli che
    quel mezzo puo' fare su quel focolaio senza violare autonomia, turno e
    orizzonte. Viene calcolato prima e passato al solver come limite superiore
    del dominio. Dare al solver un limite che potrebbe dedurre da solo sembra
    ridondante, ma non lo e': la propagazione dei vincoli parte da domini gia'
    stretti e taglia molto spazio di ricerca, invece di doverlo scoprire
    ramo per ramo.
    """

    vehicle_id: str
    fire_id: str
    water_source_id: Optional[str]

    setup_units: int
    outbound_units: int
    return_units: int
    cycle_units: int

    # Tempo fermo in base fra il rientro di una missione e la partenza della
    # successiva. Non fa parte della missione (non entra ne' nell'autonomia
    # ne' nel turno ne' nel costo): serve solo a distanziare due missioni
    # consecutive dello stesso mezzo. Vale zero quando la base non e' nello
    # scenario, perche' il dato non e' conoscibile e inventarlo sarebbe
    # peggio.
    turnaround_units: int

    earliest_start_units: int
    max_sorties: int
    liters_per_sortie: int

    # Valori in minuti, tenuti per la diagnostica e per le note del piano.
    outbound_minutes: float
    cycle_minutes: float
    water_distance_km: float

    @property
    def fixed_units(self) -> int:
        """Parte della missione che non dipende dal numero di cicli."""
        return self.setup_units + self.outbound_units + self.return_units


def mission_minutes(
    vehicle: Vehicle,
    origin: GeoPoint,
    fire: FireCluster,
    cycle_minutes: float,
    sorties: int,
) -> float:
    """Durata totale della missione, come da modello fisico di riferimento.

        t_missione = t_setup + t_andata + n_lanci * t_ciclo_acqua + t_ritorno

    Il ritorno e' calcolato dal focolaio al PUNTO DI PARTENZA (`origin`), che
    normalmente e' la base ma nella simulazione puo' essere la posizione
    corrente del mezzo. Dopo l'ultimo sgancio il mezzo si trova sopra il
    fuoco, non alla sorgente: per questo la tratta di rientro e' la stessa
    dell'andata e non quella sorgente-base.

    Assumere andata e ritorno uguali e' una semplificazione dichiarata. Nella
    realta' il mezzo potrebbe essere dirottato su un'altra base, e un mezzo
    gia' in volo rientra dove gli dice la centrale, non da dove e' partito.
    Qui conta che sia la STESSA ipotesi usata per calcolare `max_sorties`,
    altrimenti il tetto sui cicli e la durata della missione non tornerebbero.
    """
    leg = travel_minutes(vehicle, origin, fire.centroid)
    return float(vehicle.setup_minutes) + leg + sorties * cycle_minutes + leg


def origin_point(vehicle: Vehicle, base: Optional[Base]) -> Optional[GeoPoint]:
    """Da dove parte il mezzo.

    Se la simulazione gli ha gia' assegnato una posizione corrente si parte da
    li', altrimenti dalla base di appartenenza. Se non c'e' ne' l'una ne'
    l'altra il mezzo non e' collocabile e va scartato.
    """
    if vehicle.current_position is not None:
        return vehicle.current_position
    if base is not None:
        return base.point
    return None


def build_mission_profile(
    vehicle: Vehicle,
    base: Optional[Base],
    fire: FireCluster,
    sources: Sequence[WaterSource],
    horizon_minutes: int,
) -> Optional[MissionProfile]:
    """Costruisce il profilo di missione, oppure None se la coppia e' impossibile.

    Restituire None e' importante quanto restituire un profilo: le coppie
    impossibili non entrano proprio nel modello. Ogni variabile che non viene
    creata e' un ramo che il solver non dovra' mai esplorare, ed e' anche un
    vincolo in meno da scrivere (un mezzo senza sorgente d'acqua non ha
    bisogno di un vincolo che lo inchiodi a zero: semplicemente non ha
    variabili).

    Una coppia e' impossibile quando:
      - il mezzo non e' collocabile (niente base, niente posizione corrente);
      - il mezzo ha bisogno d'acqua e non ha nessuna sorgente utilizzabile;
      - nemmeno un singolo ciclo sta dentro autonomia, turno e orizzonte.
    """
    origin = origin_point(vehicle, base)
    if origin is None:
        return None

    option = best_water_source(vehicle, fire, sources)
    if option is None and vehicle.requires_water_source:
        return None

    outbound = travel_minutes(vehicle, origin, fire.centroid)

    if option is not None:
        cycle = option.cycle_minutes
        source_id: Optional[str] = option.source_id
        water_distance = option.distance_km
    else:
        # Mezzo autonomo senza sorgente raggiungibile: ha solo l'acqua che si
        # porta dietro, quindi un unico sgancio e nessun rifornimento.
        cycle = float(vehicle.drop_minutes)
        source_id = None
        water_distance = 0.0

    setup_units = to_units_up(vehicle.setup_minutes)
    outbound_units = to_units_up(outbound)
    return_units = to_units_up(outbound)
    cycle_units = max(1, to_units_up(cycle))

    # Riattrezzamento: SPEC.md sezione 6 punto 2 lo prescrive fra due missioni
    # consecutive dello stesso mezzo. E' una proprieta' della BASE (rifornire,
    # cambiare equipaggio, fare i controlli si fa a terra, e quanto ci vuole
    # dipende dal sedime), quindi si legge da `Base.turnaround_minutes` e vale
    # zero se la base non e' nello scenario.
    #
    # Arrotondato per ECCESSO come tutte le durate: e' tempo che il mezzo deve
    # passare fermo, quindi concedersene meno vorrebbe dire far ripartire il
    # mezzo prima di quanto la base permetta.
    turnaround_units = max(
        0, to_units_up(float(base.turnaround_minutes)) if base is not None else 0
    )

    earliest_start_units = max(0, to_units_up(vehicle.available_from_minute))

    # Budget di tempo: il piu' stretto fra autonomia della singola missione,
    # turno residuo della giornata e orizzonte di pianificazione rimasto.
    # Tutti e tre arrotondati per DIFETTO perche' sono budget, non durate.
    endurance_units = max(0, to_units_down(vehicle.endurance_minutes))
    shift_left_units = to_units_down(
        max(0.0, float(vehicle.max_shift_minutes) - float(vehicle.minutes_flown_today))
    )
    # L'orizzonte residuo parte dal primo istante in cui il mezzo e' libero:
    # se `available_from_minute` e' 30 su un orizzonte di 240, il mezzo ha 210
    # minuti, non 240. Puo' venire negativo (mezzo che si libera dopo la fine
    # dell'orizzonte) e allora `room_units` sara' negativo e la coppia viene
    # scartata dal controllo qui sotto, che e' esattamente il comportamento
    # voluto.
    horizon_left_units = max(0, to_units_down(horizon_minutes)) - earliest_start_units

    budget_units = min(endurance_units, shift_left_units, horizon_left_units)
    room_units = budget_units - (setup_units + outbound_units + return_units)
    if room_units < cycle_units:
        return None

    # `cycle_units` e' garantito >= 1 poco sopra, quindi la divisione intera
    # non puo' spaccarsi su un mezzo con tempi di ciclo nulli nei dati.
    max_sorties = room_units // cycle_units

    # Non serve a nessuno versare piu' acqua del fabbisogno del focolaio: il
    # dominio si stringe anche da questa parte.
    #
    # Il `max(1, ...)` sulla capacita' e' una rete contro i dati sporchi (un
    # mezzo dichiarato con capacita' 0 o negativa): senza di esso la divisione
    # per eccesso qui sotto dividerebbe per zero. Per un mezzo che davvero non
    # porta acqua il numero di litri scritto nel piano risulta simbolico, e il
    # validatore, che ricalcola dalla capacita' vera, lo segnalera': e' il
    # comportamento giusto, perche' il problema sta nei dati statici.
    liters_per_sortie = max(1, int(vehicle.water_capacity_liters))
    demand = max(0, int(fire.water_demand_liters))
    if demand > 0:
        needed = -(-demand // liters_per_sortie)  # divisione intera per eccesso
        max_sorties = min(max_sorties, needed)

    if source_id is None:
        # Senza rifornimento il mezzo puo' scaricare una volta sola.
        max_sorties = min(max_sorties, 1)

    max_sorties = int(min(max_sorties, MAX_SORTIES_HARD_CAP))
    if max_sorties < 1:
        return None

    return MissionProfile(
        vehicle_id=vehicle.vehicle_id,
        fire_id=fire.fire_id,
        water_source_id=source_id,
        setup_units=setup_units,
        outbound_units=outbound_units,
        return_units=return_units,
        cycle_units=cycle_units,
        turnaround_units=turnaround_units,
        earliest_start_units=earliest_start_units,
        max_sorties=max_sorties,
        liters_per_sortie=liters_per_sortie,
        outbound_minutes=outbound,
        cycle_minutes=cycle,
        water_distance_km=water_distance,
    )
