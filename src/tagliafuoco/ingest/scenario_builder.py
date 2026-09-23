"""Costruzione dello `Scenario`, la fotografia che entra nel pianificatore.

Perche' questo modulo esiste: solver, baseline e validatore devono vedere
esattamente gli stessi dati, altrimenti il confronto fra i piani non misura la
qualita' dei pianificatori ma la differenza fra i loro ingressi. Avere un solo
punto che assembla lo scenario e' quello che rende il confronto onesto.

Lo scenario mette insieme tre cose che arrivano da posti diversi: i focolai (dal
clustering e dall'arricchimento), le risorse (dal registro dei dati statici) e
le condizioni al contorno, di cui l'unica che qui viene decisa e' il giorno o la
notte.

Sul giorno e la notte: la maggior parte dei mezzi aerei antincendio non opera
al buio. Non e' una regola del simulatore, e' come funziona la lotta aerea agli
incendi boschivi: il lancio richiede volo a vista a bassa quota sopra terreno
accidentato e con visibilita' ridotta dal fumo. I mezzi aerei senza capacita'
notturna, di notte, vanno in `OUT_OF_SHIFT`, e il solver non li vede neanche.
Se si dimenticasse questo passaggio il piano notturno risulterebbe ottimo e
sarebbe fisicamente impossibile: e' il genere di errore che una simulazione
rende invisibile finche' qualcuno non guarda gli orari.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping, Optional, Sequence, Union

from tagliafuoco.config import (
    DAYLIGHT_END_MINUTE,
    DAYLIGHT_START_MINUTE,
    DEFAULT_HORIZON_MINUTES,
)
from tagliafuoco.domain import registry
from tagliafuoco.domain.models import (
    Base,
    Detection,
    Domain,
    FireCluster,
    Scenario,
    Vehicle,
    VehicleStatus,
    WaterSource,
)
from tagliafuoco.ingest import normalize
from tagliafuoco.ingest.clustering import cluster_detections
from tagliafuoco.ingest.enrichment import enrich_fires

logger = logging.getLogger(__name__)


# Scarto fra ora UTC e ora locale in Calabria durante la stagione AIB. La
# stagione va da giugno a settembre ed e' interamente in ora legale (CEST,
# UTC+2), quindi un offset fisso basta e non serve tirarsi dentro un database
# dei fusi. E' una semplificazione dichiarata: fuori stagione (UTC+1) il
# confine fra giorno e notte risulterebbe spostato di un'ora.
LOCAL_UTC_OFFSET_HOURS = 2


def local_minute_of_day(moment: datetime) -> int:
    """Minuti dalla mezzanotte locale.

    Un datetime aware viene portato in UTC e poi spostato di
    `LOCAL_UTC_OFFSET_HOURS`. Un datetime naive si considera gia' ora locale:
    e' la lettura meno sorprendente per chi scrive a mano un orario in un test.
    """
    if moment.tzinfo is not None:
        local = moment.astimezone(timezone.utc)
        minutes = local.hour * 60 + local.minute + LOCAL_UTC_OFFSET_HOURS * 60
        return minutes % (24 * 60)
    return moment.hour * 60 + moment.minute


def is_night_at(moment: datetime) -> bool:
    """Vero se l'istante cade fuori dalla finestra di luce operativa.

    La finestra e' `DAYLIGHT_START_MINUTE` - `DAYLIGHT_END_MINUTE`, cioe' dalle
    5:30 alle 20:30 locali. Sono orari medi di stagione, non effemeridi: il
    tramonto a Reggio Calabria a fine settembre e' quasi un'ora prima che a
    fine giugno. E' una taratura dichiarata.
    """
    minute = local_minute_of_day(moment)
    return not (DAYLIGHT_START_MINUTE <= minute < DAYLIGHT_END_MINUTE)


def apply_night_shift(vehicles: Sequence[Vehicle], is_night: bool) -> list[Vehicle]:
    """Mette in `OUT_OF_SHIFT` i mezzi aerei non abilitati al volo notturno.

    Si tocca solo chi era `AVAILABLE`: un mezzo gia' in manutenzione o gia'
    riservato deve restare nello stato in cui era, altrimenti l'informazione
    sul perche' non e' disponibile va persa.

    ATTENZIONE, ed e' il motivo per cui questa avvertenza sta qui e non in
    fondo: la funzione modifica i `Vehicle` SUL POSTO e restituisce una lista
    nuova che contiene gli stessi oggetti. L'operazione non e' invertibile,
    perche' allo scenario diurno successivo non c'e' nessuno che rimetta i
    mezzi in `AVAILABLE`. Chi la chiama deve quindi possedere i mezzi che le
    passa: `build_scenario` le da' una copia apposta (vedi li' il perche'), e
    chi la usa direttamente deve fare altrettanto se la stessa flotta serve
    anche altrove.
    """
    if not is_night:
        return list(vehicles)
    grounded = 0
    for vehicle in vehicles:
        if (
            vehicle.domain == Domain.AIR
            and not vehicle.night_capable
            and vehicle.status == VehicleStatus.AVAILABLE
        ):
            vehicle.status = VehicleStatus.OUT_OF_SHIFT
            grounded += 1
    if grounded:
        logger.debug("scenario notturno: %d mezzi aerei fermi a terra", grounded)
    return list(vehicles)


def make_scenario_id(timestamp: datetime, fires: Sequence[FireCluster]) -> str:
    """Identificatore riproducibile: istante piu' digest dei focolai.

    Due scenari costruiti sugli stessi focolai allo stesso istante hanno lo
    stesso id, anche fra esecuzioni diverse. Serve a poter ritrovare nel
    confronto fra piani a quale finestra della stagione ci si riferisce.
    """
    stamp = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp
    payload = "|".join(sorted(f.fire_id for f in fires))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]
    return f"SC_{stamp.strftime('%Y%m%dT%H%M')}_{digest}"


def build_scenario(
    detections_or_fires: Iterable[Union[Detection, FireCluster]],
    timestamp: datetime,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    fleet: Optional[Sequence[Vehicle]] = None,
    *,
    bases: Optional[Sequence[Base]] = None,
    water_sources: Optional[Sequence[WaterSource]] = None,
    comuni: Optional[Sequence[Mapping[str, object]]] = None,
    protected_areas: Optional[Sequence[Mapping[str, object]]] = None,
    static_dir: Optional[str] = None,
    scenario_id: Optional[str] = None,
    is_night: Optional[bool] = None,
    enrich: bool = True,
    eps_km: Optional[float] = None,
    time_window_hours: Optional[float] = None,
    wind_kmh: float = 15.0,
    temperature_c: float = 30.0,
    humidity_pct: float = 35.0,
) -> Scenario:
    """Assembla uno `Scenario` a partire da rilevamenti oppure da focolai.

    Accetta le due forme perche' servono due percorsi diversi: la rigiocata
    della stagione parte dai rilevamenti grezzi della finestra e vuole passare
    per clustering e arricchimento, mentre un test o una demo partono da
    focolai gia' pronti e non devono ripetere il lavoro.

    Se `fleet`, `bases` o `water_sources` non sono passati si leggono dal
    registro dei dati statici. Passarli esplicitamente serve ai test e alle
    ablazioni che vogliono ridurre la flotta. La flotta passata viene comunque
    copiata: lo scenario possiede i propri mezzi e non ne condivide lo stato
    con il chiamante (il perche' e' commentato nel corpo, accanto alla copia).

    `is_night` si deduce dall'ora se non lo si impone. Imporlo serve a studiare
    lo stesso scenario in due condizioni diverse tenendo tutto il resto uguale.

    Se si passano insieme rilevamenti e focolai gia' pronti con `enrich=True`,
    anche i focolai gia' pronti vengono riarricchiti e i loro attributi
    territoriali sovrascritti. E' voluto: uno scenario deve essere coerente al
    suo interno, e due focolai arricchiti con riferimenti diversi non lo
    sarebbero. Chi ha gia' arricchito i propri focolai passa `enrich=False`.
    """
    items = list(detections_or_fires)
    detections = [x for x in items if isinstance(x, Detection)]
    fires: list[FireCluster] = [x for x in items if isinstance(x, FireCluster)]

    if detections:
        fires.extend(
            cluster_detections(
                detections, eps_km=eps_km, time_window_hours=time_window_hours
            )
        )

    if enrich and fires:
        fires = enrich_fires(
            fires,
            comuni=comuni,
            protected_areas=protected_areas,
            static_dir=static_dir,
        )

    # Ordine deterministico e operativamente sensato: prima i piu' prioritari.
    # Il `fire_id` in coda alla chiave rompe i pareggi in modo stabile.
    fires.sort(key=lambda f: (-f.base_priority, f.fire_id))

    static_path = static_dir
    # La flotta si COPIA, sempre, anche quando arriva dal chiamante.
    #
    # `apply_night_shift` piu' sotto cambia lo stato dei mezzi sul posto, e lo
    # stato non torna indietro da solo. Senza la copia, costruire uno scenario
    # notturno e poi uno diurno dalla stessa lista di mezzi lascerebbe gli
    # aerei in `OUT_OF_SHIFT` anche di giorno: il piano diurno risulterebbe
    # senza mezzi aerei e nessuno se ne accorgerebbe, perche' un piano con
    # meno mezzi e' comunque un piano valido, solo peggiore. E' esattamente il
    # caso della rigiocata della stagione (sezione 7 di SPEC.md), dove
    # `build_scenarios_over_season` passa la stessa flotta a tutte le finestre
    # e le finestre notturne e diurne si alternano.
    #
    # Lo stesso ragionamento e' gia' scritto in `registry.load_fleet`, che
    # restituisce copie fresche per non far sanguinare lo stato dinamico da
    # una simulazione all'altra. Qui la regola diventa incondizionata: uno
    # `Scenario` possiede i propri mezzi, chiunque glieli abbia dati.
    #
    # Basi e sorgenti d'acqua non si copiano perche' nessuno le modifica: sono
    # dati di riferimento, non stato.
    resolved_fleet = (
        copy.deepcopy(list(fleet))
        if fleet is not None
        else registry.load_fleet(static_path)
    )
    resolved_bases = (
        list(bases) if bases is not None else registry.load_bases(static_path)
    )
    resolved_water = (
        list(water_sources)
        if water_sources is not None
        else registry.load_water_sources(static_path)
    )

    night = is_night_at(timestamp) if is_night is None else is_night
    apply_night_shift(resolved_fleet, night)

    return Scenario(
        scenario_id=scenario_id or make_scenario_id(timestamp, fires),
        timestamp=timestamp,
        horizon_minutes=horizon_minutes,
        fires=fires,
        vehicles=resolved_fleet,
        bases=resolved_bases,
        water_sources=resolved_water,
        is_night=night,
        wind_kmh=wind_kmh,
        temperature_c=temperature_c,
        humidity_pct=humidity_pct,
    )


def build_scenarios_over_season(
    detections: Sequence[Detection],
    window_hours: float,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    *,
    lookback_hours: Optional[float] = None,
    **kwargs: object,
) -> list[Scenario]:
    """Scorre la stagione a finestre e produce uno scenario per finestra.

    Serve all'esperimento 1 della sezione 7 di SPEC.md. Ogni finestra vede i
    rilevamenti delle ultime `lookback_hours` ore (di default il doppio della
    finestra, cosi' un incendio acceso poco prima dell'inizio resta visibile),
    e lo scenario e' datato alla fine della finestra, che e' l'istante in cui
    il coordinatore avrebbe davvero quei dati.

    Le finestre sono ancorate all'epoca come quelle del clustering: cosi' la
    stessa stagione tagliata in due tronconi produce le stesse finestre.

    Sugli estremi della finestra: si tiene `start <= t < end`, semiaperta a
    destra. E' la convenzione che garantisce che nessun rilevamento cada in
    due finestre consecutive per via del confine, ed e' la stessa che usa
    `time_bucket` nel clustering. L'ultima finestra e' quella che contiene
    l'ultimo rilevamento, e ci entra perche' il suo `end` e' il confine
    successivo, non quello su cui sta il dato: e' il punto dove un `- 1` di
    troppo nel calcolo del blocco finale farebbe sparire in silenzio le ultime
    ore della stagione.

    La flotta eventuale in `kwargs` non va copiata dal chiamante: ci pensa
    `build_scenario`, che ne prende una copia per ogni finestra. Senza quella
    copia le finestre notturne lascerebbero i mezzi aerei a terra anche in
    quelle diurne successive.
    """
    if not detections:
        return []
    if window_hours <= 0.0:
        raise ValueError(
            f"window_hours deve essere positivo (ore), ricevuto {window_hours}"
        )
    lookback = lookback_hours if lookback_hours is not None else window_hours * 2.0
    if lookback <= 0.0:
        raise ValueError(
            f"lookback_hours deve essere positivo (ore), ricevuto {lookback}"
        )
    step = window_hours * 3600.0
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    # Gli istanti si portano in UTC prima di ordinarli e di confrontarli con
    # i confini delle finestre: mescolare datetime con e senza fuso solleva
    # `TypeError`, e un `Detection` puo' arrivare naive da una fonte che non
    # sia `normalize.parse_csv`. La convenzione e' la stessa di tutta la
    # catena: naive significa UTC.
    stamped = sorted(
        ((normalize.as_utc(d.acquired_at), d) for d in detections),
        key=lambda pair: (pair[0], pair[1].detection_id),
    )
    first_bucket = int((stamped[0][0] - epoch).total_seconds() // step)
    last_bucket = int((stamped[-1][0] - epoch).total_seconds() // step)

    scenarios: list[Scenario] = []
    for bucket in range(first_bucket, last_bucket + 1):
        end = epoch + timedelta(seconds=(bucket + 1) * step)
        start = end - timedelta(hours=lookback)
        window = [det for moment, det in stamped if start <= moment < end]
        if not window:
            continue
        scenarios.append(
            build_scenario(window, end, horizon_minutes, **kwargs)  # type: ignore[arg-type]
        )
    return scenarios


def season_dates(year: int) -> tuple[date, date]:
    """Estremi convenzionali della stagione antincendio boschivo in Calabria.

    Il periodo di massima pericolosita' dichiarato dalla Regione va dal 15
    giugno al 30 settembre. Qui si allarga al primo giugno perche' i primi
    focolai di stoppie arrivano prima dell'inizio formale della campagna.
    """
    return date(year, 6, 1), date(year, 9, 30)
