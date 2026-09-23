"""Le rotte HTTP: un adattatore, non un pezzo di sistema.

Questo modulo non calcola niente. Non ci sono formule di tempo di volo, non ci
sono cicli d'acqua, non c'e' una regola di priorita'. Tutto quello che fa e'
tradurre una richiesta HTTP in una chiamata ai casi d'uso gia' scritti
(`ingest`, `nlu`, `optimize`, `validate`, `baseline`, `sim`) e tradurre il
risultato nei modelli di trasporto di `schemas.py`. Se in questo file comparisse
una moltiplicazione fra chilometri e velocita', sarebbe finita nel posto
sbagliato: il modello fisico vive due volte, in `optimize/` e in `validate/`, e
una terza copia dentro l'adattatore non sarebbe una terza opinione, sarebbe un
errore in attesa di divergere.

Quattro decisioni che riguardano solo l'adattatore e vanno dette qui.

**Il tetto sul tempo del risolutore.** Ogni richiesta puo' chiedere il proprio
limite di tempo, ma il servizio lo riduce al proprio tetto e lo dichiara nelle
note della risposta. Un endpoint che puo' restare appeso per minuti non e' un
endpoint: e' un modo di far scadere il timeout del client senza dirgli niente.
Il taglio viene annunciato invece che applicato in silenzio, perche' un numero
diverso da quello richiesto senza spiegazione e' peggio di un errore.

**Il tetto sul numero di focolai.** Lo scenario si tronca ai primi N focolai in
ordine di priorita'. Non e' un giudizio operativo, e' un limite di costo: i
focolai scartati finiscono nelle note, cosi' chi legge sa che il piano non li
ha nemmeno considerati.

**Il tetto sulle pianificazioni contemporanee.** Il limite di tempo di CP-SAT e'
tempo di orologio: due richieste insieme non durano il doppio, tornano tutte
dopo lo stesso tetto con piani peggiori, e la risposta continua a dire
FEASIBLE. Un peggioramento che non si vede e' peggio di un rifiuto, quindi le
pianificazioni in eccesso aspettano e, se il posto non si libera, ricevono 503.

**La cache dei rilevamenti.** Leggere e normalizzare i CSV costa quasi un
secondo, e la demo chiede lo stesso periodo in continuazione. I rilevamenti di
un intervallo di date si tengono in memoria: sono dati di sola lettura per tutto
cio' che sta a valle (il clustering li legge, l'arricchimento scrive solo dentro
i `FireCluster`), quindi condividerli fra richieste e' sicuro. La cache scade
solo quando c'e' la chiave FIRMS, cioe' solo quando la sorgente puo' cambiare
sotto il servizio: senza chiave si leggono file fermi, e farla scadere
significherebbe rileggerli per niente.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tagliafuoco import config
from tagliafuoco.api import __version__
from tagliafuoco.api.schemas import (
    BaseOut,
    BaselineComparisonOut,
    FireIn,
    FireOut,
    FiresOut,
    HealthOut,
    KeysOut,
    PlanIn,
    PlanOut,
    PlanResponse,
    ReplayIndexEntry,
    ReplayOut,
    ScenarioIn,
    ScenarioOut,
    StaticsOut,
    TranslateIn,
    TranslationOut,
    ValidationOut,
    VehicleOut,
    WaterSourceOut,
)
from tagliafuoco.baseline.nearest import BASELINE_PLANNERS, available_baselines
from tagliafuoco.domain import registry
from tagliafuoco.domain.models import Detection, FireCluster, GeoPoint, Scenario, Vehicle
from tagliafuoco.ingest.clustering import cluster_detections
from tagliafuoco.ingest.enrichment import enrich_fires
from tagliafuoco.ingest.firms_client import fetch_detections
from tagliafuoco.ingest.normalize import filter_by_time
from tagliafuoco.ingest.scenario_builder import build_scenario
from tagliafuoco.nlu import translator
from tagliafuoco.nlu.schema import ConstraintSet
from tagliafuoco.optimize.solver import solve
from tagliafuoco.validate.validator import ValidationResult, validate_plan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tagliafuoco"])


# --------------------------------------------------------------------------
# Impostazioni del servizio
# --------------------------------------------------------------------------

#: Istante di riferimento quando il client non ne indica uno e non c'e' la
#: chiave FIRMS. E' un giorno di ondata di calore delle fixture sintetiche
#: (vedi data/fixtures/fixture_config.json): una demo aperta senza parametri
#: deve mostrare qualcosa, non una mappa vuota.
DEFAULT_FIXTURE_INSTANT = datetime(2025, 8, 6, 12, 0, tzinfo=timezone.utc)

#: Tetto assoluto al tempo del risolutore per una richiesta HTTP.
SOLVER_SECONDS_CAP = 30.0

#: Ampiezza massima del periodo interrogabile in una sola richiesta.
MAX_QUERY_DAYS = 31

#: Per quanti secondi i rilevamenti letti da FIRMS restano buoni in cache.
#: Senza chiave la cache non scade mai, perche' le fixture sono un file che non
#: cambia mentre il servizio gira; con la chiave si legge da una sorgente viva,
#: e una cache eterna servirebbe per ore i dati della prima richiesta
#: spacciandoli per gli ultimi disponibili. Dieci minuti e' una taratura: piu'
#: corto del tempo con cui FIRMS aggiorna il quasi-real-time, lungo abbastanza
#: da non rileggere i CSV a ogni gesto sulla mappa.
FIRMS_CACHE_TTL_SECONDS = 600.0

#: Margine oltre il riquadro della Calabria entro cui si accetta un focolaio
#: dichiarato a mano, in gradi. Serve a non rifiutare un punto appena fuori dal
#: riquadro per un arrotondamento, senza aprire al resto del mondo.
DECLARED_FIRE_MARGIN_DEG = 0.5

#: Quante pianificazioni si accetta di far girare insieme.
#:
#: Il limite di tempo di CP-SAT e' tempo di orologio, non tempo di CPU: dieci
#: richieste contemporanee non durano dieci volte tanto, tornano tutte dopo lo
#: stesso tetto con piani peggiori, e nella risposta non si vede. E' il modo
#: piu' silenzioso di peggiorare un risultato, perche' il campo `solver_status`
#: continua a dire FEASIBLE. Meglio un rifiuto esplicito con un codice che una
#: risposta buona all'apparenza, quindi le pianificazioni in eccesso aspettano
#: il loro turno e, se non arriva, ricevono 503.
MAX_CONCURRENT_SOLVES = 2

_solver_slots = threading.BoundedSemaphore(MAX_CONCURRENT_SOLVES)


@contextmanager
def _solver_slot(settings: "Settings") -> Iterator[None]:
    """Un posto fra quelli disponibili, o un 503 che dice di riprovare."""
    if not _solver_slots.acquire(timeout=settings.solver_seconds_cap):
        raise _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "risolutore_occupato",
            "Il risolutore sta gia' lavorando su "
            f"{MAX_CONCURRENT_SOLVES} pianificazioni e non si e' liberato in "
            f"{settings.solver_seconds_cap:.0f} s. Riprova fra poco: far "
            "girare piu' modelli insieme non li rende piu' veloci, li rende "
            "solo peggiori senza dirlo.",
            {"max_concurrent": MAX_CONCURRENT_SOLVES},
        )
    try:
        yield
    finally:
        _solver_slots.release()


@dataclass(frozen=True)
class Settings:
    """Cio' che cambia fra il servizio vero e un servizio sotto test.

    Sta in un oggetto iniettato invece che in costanti lette direttamente per
    un motivo pratico: i test devono poter puntare la cartella delle rigiocate
    su una cartella temporanea e stringere il tetto del risolutore, senza
    toccare `config.py`, che appartiene a un altro sottosistema.
    """

    processed_dir: Path = config.PROCESSED_DIR
    solver_seconds_default: float = config.SOLVER_MAX_SECONDS
    solver_seconds_cap: float = SOLVER_SECONDS_CAP
    max_query_days: int = MAX_QUERY_DAYS
    default_instant: Optional[datetime] = None


_settings = Settings()


def get_settings() -> Settings:
    """Dipendenza FastAPI. I test la sostituiscono con `dependency_overrides`."""
    return _settings


def default_instant(settings: Settings) -> datetime:
    """L'istante da usare quando il client non ne indica uno.

    Con la chiave FIRMS ha senso guardare adesso, perche' i dati freschi ci
    sono. Senza chiave si legge dalle fixture, che coprono la stagione 2025: in
    quel caso "adesso" darebbe sempre zero focolai, che e' il modo piu' rapido
    di far sembrare rotta una demo che funziona.
    """
    if settings.default_instant is not None:
        return settings.default_instant
    if config.has_firms_key():
        return datetime.now(timezone.utc)
    return DEFAULT_FIXTURE_INSTANT


# --------------------------------------------------------------------------
# Errori
# --------------------------------------------------------------------------


def _fail(
    code: int, error: str, message: str, detail: Optional[Any] = None
) -> HTTPException:
    """Costruisce un errore HTTP con un messaggio in italiano.

    Il corpo ha sempre la stessa forma di `schemas.ErrorOut`: chi scrive un
    client deve poter gestire gli errori in un posto solo, non caso per caso.
    """
    return HTTPException(
        status_code=code,
        detail={"error": error, "message": message, "detail": detail},
    )


# --------------------------------------------------------------------------
# Rilevamenti, focolai, scenario
# --------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _detections_cached(start: date, end: date, freshness: int) -> tuple[Detection, ...]:
    """Rilevamenti di un intervallo di date, tenuti in memoria fra richieste.

    Tupla e non lista per due motivi: `lru_cache` vuole un risultato che non
    inviti a modificarlo, e chi la riceve la tratta come sola lettura. Il
    contenuto e' condiviso, quindi nessuno a valle deve scrivere dentro un
    `Detection`: oggi nessuno lo fa, e questo commento esiste perche' resti
    vero.

    `freshness` non si usa nel corpo e non e' un residuo: e' la parte della
    chiave che fa scadere la cache. `lru_cache` non ha una scadenza, quindi la
    scadenza si ottiene cambiando chiave. Senza chiave FIRMS vale sempre zero e
    la cache non scade mai, che e' giusto perche' le fixture sono file fermi;
    con la chiave cambia ogni `FIRMS_CACHE_TTL_SECONDS` e la voce vecchia
    smette di essere trovata.
    """
    return tuple(fetch_detections(start, end))


def _freshness_bucket() -> int:
    """La parte della chiave di cache che scade, zero quando non deve scadere."""
    if not config.has_firms_key():
        return 0
    return int(time.time() // FIRMS_CACHE_TTL_SECONDS)


def _detections(start: date, end: date) -> tuple[Detection, ...]:
    """Rilevamenti del periodo, dalla cache quando sono ancora buoni."""
    return _detections_cached(start, end, _freshness_bucket())


def clear_caches() -> None:
    """Svuota la cache dei rilevamenti. Serve ai test e al cambio di fixture."""
    _detections_cached.cache_clear()


def _detections_source() -> str:
    """Da dove arrivano i rilevamenti, detto in una parola."""
    return "firms" if config.has_firms_key() else "fixtures"


def _fires_in_period(start: date, end: date) -> tuple[int, list[FireCluster]]:
    """Raggruppa e arricchisce i rilevamenti di un periodo.

    Restituisce anche il numero di rilevamenti di partenza, perche' "quindici
    focolai" senza "da ottantuno rilevamenti" non dice quanto e' fitto il dato.
    """
    detections = _detections(start, end)
    fires = cluster_detections(detections)
    fires = enrich_fires(fires)
    fires.sort(key=lambda f: (-f.base_priority, f.fire_id))
    return len(detections), fires


def _fires_visible_at(instant: datetime, lookback_hours: float) -> list[FireCluster]:
    """I focolai gia' rilevati a quell'istante, senza guardare nel futuro.

    Il filtro si applica ai rilevamenti prima del clustering, non ai focolai
    dopo: se si raggruppasse tutto e poi si filtrasse, il centroide di un
    focolaio conterrebbe pixel che a quell'ora nessuno aveva ancora visto. E'
    la stessa regola che `sim/replay.py` applica alla rigiocata, ed e' il punto
    in cui e' piu' facile lasciar entrare informazione dal futuro.
    """
    window_start = instant - timedelta(hours=lookback_hours)
    detections = _detections(window_start.date(), instant.date())
    visible = filter_by_time(list(detections), window_start, instant)
    fires = cluster_detections(visible)
    fires = enrich_fires(fires)
    fires.sort(key=lambda f: (-f.base_priority, f.fire_id))
    return fires


def _check_inside_area(item: FireIn, index: int) -> None:
    """Rifiuta un focolaio dichiarato fuori dall'area che il simulatore modella.

    Non e' pignoleria sui confini. L'arricchimento territoriale assegna a
    qualunque punto il comune calabrese piu' vicino, senza un tetto sulla
    distanza: un focolaio dichiarato a latitudine zero tornava indietro con
    comune "Melito di Porto Salvo" e una distanza dal costruito di
    quattromilacinquecento chilometri. Sono numeri inventati con l'aspetto di
    dati, e il posto dove fermarli e' il confine della API, prima che entrino.
    """
    lon_min, lat_min, lon_max, lat_max = config.CALABRIA_BBOX
    margin = DECLARED_FIRE_MARGIN_DEG
    inside = (
        lat_min - margin <= item.lat <= lat_max + margin
        and lon_min - margin <= item.lon <= lon_max + margin
    )
    if inside:
        return
    raise _fail(
        status.HTTP_400_BAD_REQUEST,
        "focolaio_fuori_area",
        f"Il focolaio {index} e' a lat {item.lat}, lon {item.lon}, fuori "
        "dall'area simulata (la Calabria). Tagliafuoco conosce solo le basi, "
        "i mezzi e il territorio di quella regione: su un punto fuori "
        "restituirebbe un comune e una distanza dal costruito inventati.",
        {
            "lat": item.lat,
            "lon": item.lon,
            "bbox_lon_min": lon_min,
            "bbox_lat_min": lat_min,
            "bbox_lon_max": lon_max,
            "bbox_lat_max": lat_max,
            "margin_deg": margin,
        },
    )


def _fire_from_input(item: FireIn, instant: datetime, index: int) -> FireCluster:
    """Trasforma un focolaio dichiarato dal client in un `FireCluster`.

    Nessun `Detection` sintetico: un focolaio dichiarato a mano non viene da un
    satellite, e inventargli dei pixel lo farebbe sembrare un dato osservato.
    Resta con zero rilevamenti, e chi guarda `detection_count` lo vede.
    """
    _check_inside_area(item, index)
    return FireCluster(
        fire_id=item.fire_id or f"F_MANUAL_{index:03d}",
        centroid=GeoPoint(lat=item.lat, lon=item.lon),
        detections=[],
        first_seen=instant,
        last_seen=instant,
        total_frp_mw=item.frp_mw,
        max_frp_mw=item.frp_mw,
        mean_confidence=item.confidence,
        area_km2=item.area_km2,
        radius_km=(item.area_km2 / math.pi) ** 0.5,
    )


def _fleet_for(vehicle_ids: Optional[list[str]]) -> Optional[list[Vehicle]]:
    """La flotta ridotta al sottoinsieme richiesto, se ne e' stato richiesto uno."""
    if vehicle_ids is None:
        return None
    wanted = set(vehicle_ids)
    fleet = [v for v in registry.load_fleet() if v.vehicle_id in wanted]
    missing = wanted - {v.vehicle_id for v in fleet}
    if missing:
        raise _fail(
            status.HTTP_400_BAD_REQUEST,
            "mezzo_sconosciuto",
            "Alcuni mezzi richiesti non esistono nella flotta: "
            + ", ".join(sorted(missing)),
            {"unknown_vehicle_ids": sorted(missing)},
        )
    if not fleet:
        raise _fail(
            status.HTTP_400_BAD_REQUEST,
            "flotta_vuota",
            "La selezione dei mezzi non lascia nessun mezzo disponibile.",
        )
    return fleet


def _as_utc(moment: datetime) -> datetime:
    """Un istante senza fuso e' ambiguo: qui si assume UTC e lo si dichiara."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def build_scenario_from_request(
    spec: ScenarioIn, settings: Settings
) -> tuple[Scenario, list[str]]:
    """Costruisce lo scenario descritto dalla richiesta e le note da riportare.

    Le note non sono diagnostica: raccontano a chi legge la risposta cosa il
    servizio ha deciso per suo conto (quale istante ha preso, quanti focolai ha
    lasciato fuori). Sono la parte della risposta che rende la demo onesta.
    """
    notes: list[str] = []
    instant = (
        _as_utc(spec.instant) if spec.instant is not None else default_instant(settings)
    )
    if spec.instant is None:
        notes.append(
            f"Nessun istante indicato: uso {instant.isoformat()} "
            f"(sorgente dei rilevamenti: {_detections_source()})."
        )

    if spec.fires is not None:
        declared = [
            _fire_from_input(item, instant, i) for i, item in enumerate(spec.fires, 1)
        ]
        notes.append(
            f"Scenario costruito sui {len(declared)} focolai indicati nella "
            "richiesta, non sui rilevamenti satellitari."
        )
        fires = enrich_fires(declared)
        fires.sort(key=lambda f: (-f.base_priority, f.fire_id))
    else:
        fires = _fires_visible_at(instant, spec.lookback_hours)

    if len(fires) > spec.max_fires:
        dropped = [f.fire_id for f in fires[spec.max_fires :]]
        tenuti = (
            "al focolaio piu' prioritario"
            if spec.max_fires == 1
            else f"ai {spec.max_fires} focolai piu' prioritari"
        )
        esclusi = (
            "1 focolaio non e' stato considerato"
            if len(dropped) == 1
            else f"{len(dropped)} focolai non sono stati considerati"
        )
        notes.append(
            f"Lo scenario e' stato troncato {tenuti}: {esclusi} dal piano. "
            "Alza max_fires per includerli."
        )
        fires = fires[: spec.max_fires]

    try:
        scenario = build_scenario(
            fires,
            instant,
            horizon_minutes=spec.horizon_minutes,
            fleet=_fleet_for(spec.vehicle_ids),
            is_night=spec.is_night,
            enrich=False,
        )
    except registry.StaticDataError as exc:
        raise _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dati_statici_mancanti",
            "I dati statici (basi, mezzi, sorgenti d'acqua) non sono "
            "disponibili: esegui scripts/genera_dati_statici.py.",
            str(exc),
        ) from exc

    if not scenario.fires:
        notes.append(
            "Nessun focolaio rilevato in questa finestra: il piano sara' vuoto, "
            "e questo e' un risultato, non un guasto."
        )
    return scenario, notes


# --------------------------------------------------------------------------
# GET /api/salute
# --------------------------------------------------------------------------


@router.get(
    "/salute",
    response_model=HealthOut,
    summary="Stato del servizio, chiavi presenti, versione",
)
def salute(settings: Annotated[Settings, Depends(get_settings)]) -> HealthOut:
    """Dice se il servizio risponde e in quale configurazione sta girando.

    Le chiavi non sono un prerequisito: l'endpoint dichiara quali ci sono e che
    conseguenza ha la loro assenza, senza trattarla come un errore.
    """
    return HealthOut(
        version=__version__,
        keys=KeysOut(
            firms=config.has_firms_key(),
            llm=config.has_llm_key(),
            detections_source=_detections_source(),
            translator="llm" if config.has_llm_key() else "rules",
        ),
        static_data_available=registry.static_data_available(),
        replays_available=len(_replay_files(settings)),
        solver_seconds_default=settings.solver_seconds_default,
        solver_seconds_cap=settings.solver_seconds_cap,
    )


# --------------------------------------------------------------------------
# GET /api/statici
# --------------------------------------------------------------------------


@router.get(
    "/statici",
    response_model=StaticsOut,
    summary="Basi, mezzi e sorgenti d'acqua",
)
def statici() -> StaticsOut:
    """Quello che la mappa disegna una volta sola e non cambia piu'."""
    try:
        bases = registry.load_bases()
        fleet = registry.load_fleet()
        water = registry.load_water_sources()
    except registry.StaticDataError as exc:
        raise _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "dati_statici_mancanti",
            "I dati statici non sono disponibili: esegui "
            "scripts/genera_dati_statici.py per rigenerarli.",
            str(exc),
        ) from exc

    return StaticsOut(
        bases=[BaseOut.from_domain(b) for b in bases],
        vehicles=[VehicleOut.from_domain(v) for v in fleet],
        water_sources=[WaterSourceOut.from_domain(w) for w in water],
        counts={
            "bases": len(bases),
            "vehicles": len(fleet),
            "water_sources": len(water),
        },
    )


# --------------------------------------------------------------------------
# GET /api/focolai
# --------------------------------------------------------------------------


@router.get(
    "/focolai",
    response_model=FiresOut,
    summary="Focolai raggruppati e arricchiti in un periodo",
)
def focolai(
    settings: Annotated[Settings, Depends(get_settings)],
    da: Annotated[Optional[date], Query(description="Primo giorno incluso")] = None,
    a: Annotated[Optional[date], Query(description="Ultimo giorno incluso")] = None,
    limite: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> FiresOut:
    """Rilevamenti del periodo, raggruppati in focolai e arricchiti.

    Senza date si guarda il giorno di riferimento del servizio, che senza
    chiave FIRMS e' un giorno delle fixture: cosi' una chiamata nuda restituisce
    qualcosa invece di una lista vuota che sembra un guasto.
    """
    reference = default_instant(settings).date()
    end = a or reference
    start = da or (end - timedelta(days=1))

    if start > end:
        raise _fail(
            status.HTTP_400_BAD_REQUEST,
            "periodo_invertito",
            f"Il primo giorno ({start.isoformat()}) viene dopo l'ultimo "
            f"({end.isoformat()}).",
        )
    span = (end - start).days + 1
    if span > settings.max_query_days:
        raise _fail(
            status.HTTP_400_BAD_REQUEST,
            "periodo_troppo_lungo",
            f"Il periodo richiesto copre {span} giorni: il massimo per una "
            f"sola richiesta e' {settings.max_query_days}. Spezzalo, oppure "
            "usa la rigiocata precalcolata per vedere la stagione intera.",
            {"days_requested": span, "days_max": settings.max_query_days},
        )

    detections_total, fires = _fires_in_period(start, end)
    notes: list[str] = []
    if not detections_total:
        vuoto = "Nessun rilevamento in questo periodo: e' un risultato, non un guasto."
        if not config.has_firms_key():
            vuoto += (
                " Senza chiave FIRMS i dati vengono dalle fixture locali, che "
                "coprono la stagione 2025 da giugno a settembre."
            )
        notes.append(vuoto)
    elif not fires:
        notes.append(
            f"{detections_total} rilevamenti letti, nessuno sopravvissuto al "
            "raggruppamento: controlla la soglia di confidenza."
        )
    if len(fires) > limite:
        notes.append(
            f"Elenco troncato ai primi {limite} focolai in ordine di "
            f"priorita' su {len(fires)}: alza 'limite' per vederli tutti."
        )
    return FiresOut(
        start=start.isoformat(),
        end=end.isoformat(),
        detections_total=detections_total,
        fires_total=len(fires),
        fires_returned=min(len(fires), limite),
        source=_detections_source(),
        fires=[FireOut.from_domain(f) for f in fires[:limite]],
        notes=notes,
    )


# --------------------------------------------------------------------------
# POST /api/traduci
# --------------------------------------------------------------------------


@router.post(
    "/traduci",
    response_model=TranslationOut,
    summary="Traduce l'ordine del coordinatore in vincoli tipizzati",
)
def traduci(
    payload: TranslateIn, settings: Annotated[Settings, Depends(get_settings)]
) -> TranslationOut:
    """Dal testo dell'ordine al `ConstraintSet`, con i frammenti di origine.

    Il campo `translator` dichiara quale via e' stata usata. Non e' un dettaglio
    interno: e' la differenza fra "un modello linguistico ha letto la frase" e
    "un traduttore deterministico ha riconosciuto delle espressioni", e chi
    guarda la demo ha il diritto di sapere quale delle due sta guardando.
    """
    scenario: Optional[Scenario] = None
    if payload.instant is not None:
        scenario, _ = build_scenario_from_request(
            ScenarioIn(
                instant=payload.instant, horizon_minutes=payload.horizon_minutes
            ),
            settings,
        )
    constraint_set = translator.translate(payload.order, scenario)
    return TranslationOut.from_domain(
        constraint_set, llm_available=config.has_llm_key()
    )


# --------------------------------------------------------------------------
# POST /api/pianifica
# --------------------------------------------------------------------------


def _clamp_solver_seconds(
    requested: Optional[float], settings: Settings
) -> tuple[float, list[str]]:
    """Il limite di tempo effettivo, con la nota che spiega un eventuale taglio."""
    if requested is None:
        return settings.solver_seconds_default, []
    if requested > settings.solver_seconds_cap:
        return settings.solver_seconds_cap, [
            f"Limite di tempo richiesto {requested:.1f} s, ridotto al tetto del "
            f"servizio di {settings.solver_seconds_cap:.1f} s."
        ]
    return requested, []


def _undefined_metric_notes(validation: ValidationResult) -> list[str]:
    """Dice quali rapporti della risposta sono convenzioni e non misure.

    Il validatore, per non dividere per zero, restituisce 1.0 per la copertura
    WUI quando non c'e' nessun focolaio in interfaccia, 1.0 per l'acqua quando
    il fabbisogno e' nullo e 0.0 per i tempi di risposta quando non c'e' nessuna
    assegnazione eseguibile. Sono scelte corrette dentro il validatore e
    fuorvianti dentro una risposta: "copertura WUI 100%" letto su una mappa vuol
    dire che qualcosa e' stato protetto. I numeri restano quelli che sono,
    perche' un client che fa medie deve continuare a trovarli, ma accanto ci va
    scritto che denominatore non ce n'era.
    """
    m = validation.metrics
    notes: list[str] = []
    if not m.fires_total:
        return [
            "Nessun focolaio nello scenario: le metriche di copertura sono "
            "convenzioni per non dividere per zero, non misure."
        ]
    if not m.fires_wui_total:
        notes.append(
            "Nessun focolaio in interfaccia urbano-foresta: "
            "wui_coverage_ratio vale 1.0 per convenzione, non perche' sia "
            "stato protetto qualcosa."
        )
    if not m.water_demanded_liters:
        notes.append(
            "Fabbisogno d'acqua nullo: water_fulfilment_ratio vale 1.0 per "
            "convenzione, non perche' l'acqua sia bastata."
        )
    if not m.fires_covered:
        notes.append(
            "Nessuna assegnazione eseguibile: mean_response_minutes e "
            "p90_response_minutes valgono 0 perche' non c'e' niente da "
            "misurare, non perche' la risposta sia immediata."
        )
    return notes


@router.post(
    "/pianifica",
    response_model=PlanResponse,
    summary="Ordine, vincoli, piano, validazione e confronto in una risposta",
)
def pianifica(
    payload: PlanIn, settings: Annotated[Settings, Depends(get_settings)]
) -> PlanResponse:
    """La catena intera in una chiamata sola.

    Traduce l'ordine (o accetta i vincoli gia' tradotti), costruisce lo
    scenario, risolve, fa validare il piano da un programma indipendente e
    calcola lo stesso scenario con la regola banale per il confronto.

    Il piano viene restituito anche quando la validazione lo respinge. Un piano
    respinto con la diagnostica accanto e' il caso piu' interessante da
    mostrare: e' la prova che il validatore esiste davvero.
    """
    if payload.baseline not in BASELINE_PLANNERS:
        raise _fail(
            status.HTTP_400_BAD_REQUEST,
            "baseline_sconosciuta",
            f"Baseline '{payload.baseline}' sconosciuta. Disponibili: "
            + ", ".join(available_baselines()),
            {"available": available_baselines()},
        )

    scenario, notes = build_scenario_from_request(payload.scenario, settings)

    if payload.constraint_set is not None:
        constraint_set: ConstraintSet = payload.constraint_set
        notes.append(
            "Vincoli ricevuti gia' tradotti: il traduttore non e' stato chiamato."
        )
    else:
        constraint_set = translator.translate(payload.order, scenario)

    budget, budget_notes = _clamp_solver_seconds(payload.max_seconds, settings)
    notes.extend(budget_notes)

    with _solver_slot(settings):
        plan = solve(
            scenario,
            constraint_set,
            max_seconds=budget,
            deterministic=payload.deterministic,
        )
    validation = validate_plan(plan, scenario, constraint_set)

    comparison = None
    if payload.include_baseline:
        baseline_plan = BASELINE_PLANNERS[payload.baseline](scenario, constraint_set)
        baseline_validation = validate_plan(baseline_plan, scenario, constraint_set)
        comparison = BaselineComparisonOut.build(
            baseline_name=payload.baseline,
            solver_plan=plan,
            solver_validation=validation,
            baseline_plan=baseline_plan,
            baseline_validation=baseline_validation,
            scenario=scenario,
        )

    if not validation.is_valid:
        notes.append(
            "Il validatore indipendente ha respinto il piano: le violazioni "
            "bloccanti sono in validation.violations."
        )
    notes.extend(_undefined_metric_notes(validation))

    return PlanResponse(
        order=constraint_set.source_text,
        translation=TranslationOut.from_domain(
            constraint_set, llm_available=config.has_llm_key()
        ),
        scenario=ScenarioOut.from_domain(scenario),
        fires=[FireOut.from_domain(f) for f in scenario.fires],
        plan=PlanOut.from_domain(plan, scenario),
        validation=ValidationOut.from_domain(validation),
        comparison=comparison,
        solver_seconds_requested=payload.max_seconds,
        solver_seconds_used=budget,
        notes=notes,
    )


# --------------------------------------------------------------------------
# GET /api/rigiocata
# --------------------------------------------------------------------------


def _replay_files(settings: Settings) -> list[Path]:
    """Le rigiocate salvate, dalla piu' recente alla piu' vecchia."""
    directory = settings.processed_dir
    if not directory.exists():
        return []
    return sorted(
        (p for p in directory.glob("replay_*.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _stamp_from_name(text: str) -> Optional[str]:
    """Legge un `20250805T0000` dal nome del file e lo rende ISO.

    Restituisce None su tutto cio' che non corrisponde esattamente a quel
    formato: un nome che non e' stato scritto da `sim.replay.save_result` non
    deve produrre una data plausibile ma sbagliata.
    """
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M").replace(
            tzinfo=timezone.utc
        ).isoformat()
    except ValueError:
        return None


def _replay_entry(
    path: Path, payload: Optional[dict[str, Any]] = None
) -> ReplayIndexEntry:
    """Riga dell'indice delle rigiocate.

    Per il file che si sta servendo si usa il contenuto, che e' gia' in
    memoria. Per gli altri ci si ferma al nome, che `sim.replay.save_result`
    costruisce apposta come `replay_<etichetta>_<inizio>_<fine>.json`: aprire
    tutti i registri della cartella per compilare un elenco significherebbe
    leggere decine di megabyte a ogni chiamata.

    Dal nome si ricavano l'etichetta e l'intervallo, e si ricavano davvero. Chi
    ha pianificato e quante finestre ci sono dentro no, e restano nulli:
    l'etichetta e il produttore coincidono spesso ma non sempre, e spacciare la
    prima per il secondo vorrebbe dire riempire un campo con un valore che
    nessuno ha verificato.
    """
    stat = path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    if payload is not None:
        windows = payload.get("windows")
        return ReplayIndexEntry(
            name=path.name,
            label=str(payload.get("label") or path.stem),
            producer=str(payload["producer"]) if payload.get("producer") else None,
            start=payload.get("start"),
            end=payload.get("end"),
            windows=len(windows) if isinstance(windows, list) else None,
            size_bytes=stat.st_size,
            modified_at=modified_at,
        )

    parts = path.stem.split("_")
    if len(parts) > 3:
        label = "_".join(parts[1:-2])
        start = _stamp_from_name(parts[-2])
        end = _stamp_from_name(parts[-1])
    else:
        label, start, end = path.stem, None, None
    return ReplayIndexEntry(
        name=path.name,
        label=label or path.stem,
        producer=None,
        start=start,
        end=end,
        windows=None,
        size_bytes=stat.st_size,
        modified_at=modified_at,
    )


def _read_replay(path: Path) -> dict[str, Any]:
    """Legge il registro JSON di una rigiocata."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "rigiocata_illeggibile",
            f"Il file della rigiocata '{path.name}' non e' leggibile: "
            "rigeneralo con 'tagliafuoco rigioca'.",
            str(exc),
        ) from exc
    if not isinstance(data, dict):
        raise _fail(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "rigiocata_malformata",
            f"Il file della rigiocata '{path.name}' non ha la forma attesa.",
        )
    return data


@router.get(
    "/rigiocata",
    response_model=ReplayOut,
    summary="Risultati precalcolati della stagione, per il timelapse",
)
def rigiocata(
    settings: Annotated[Settings, Depends(get_settings)],
    nome: Annotated[
        Optional[str],
        Query(description="Nome del file da servire; senza, la piu' recente"),
    ] = None,
    includi_finestre: Annotated[bool, Query()] = True,
    max_finestre: Annotated[Optional[int], Query(ge=1, le=5000)] = None,
) -> ReplayOut:
    """Serve una rigiocata gia' calcolata.

    La stagione non si rigioca mentre qualcuno guarda: ci vogliono minuti, e un
    endpoint che li impiega e' un endpoint rotto. Si calcola prima con
    `tagliafuoco rigioca` e si serve il risultato da qui.
    """
    files = _replay_files(settings)
    if not files:
        raise _fail(
            status.HTTP_404_NOT_FOUND,
            "nessuna_rigiocata",
            "Nessuna rigiocata precalcolata in "
            f"{settings.processed_dir}. Eseguine una con "
            "'tagliafuoco rigioca --da 2025-08-01 --a 2025-08-03'.",
            {"directory": str(settings.processed_dir)},
        )

    if nome is None:
        path = files[0]
    else:
        # Il nome arriva dalla rete: si accetta solo un file gia' elencato,
        # cosi' un '..' nel parametro non puo' far leggere altro.
        match = next((p for p in files if p.name == nome), None)
        if match is None:
            raise _fail(
                status.HTTP_404_NOT_FOUND,
                "rigiocata_sconosciuta",
                f"Nessuna rigiocata si chiama '{nome}'.",
                {"available": [p.name for p in files]},
            )
        path = match

    data = _read_replay(path)
    windows = data.get("windows")
    windows_list = windows if isinstance(windows, list) else []
    served: Optional[list[dict[str, Any]]] = None
    if includi_finestre:
        served = windows_list[:max_finestre] if max_finestre else windows_list

    available: list[ReplayIndexEntry] = []
    for candidate in files:
        payload = data if candidate == path else None
        available.append(_replay_entry(candidate, payload))

    aggregate = data.get("aggregate")
    return ReplayOut(
        name=path.name,
        label=str(data.get("label") or path.stem),
        producer=str(data.get("producer") or "sconosciuto"),
        start=data.get("start"),
        end=data.get("end"),
        window_hours=data.get("window_hours"),
        horizon_minutes=data.get("horizon_minutes"),
        aggregate=aggregate if isinstance(aggregate, dict) else {},
        windows_count=len(windows_list),
        windows_returned=len(served) if served is not None else 0,
        windows=served,
        available=available,
    )
