"""Client di NASA FIRMS, con ricaduta obbligatoria sulle fixture locali.

Perche' questo modulo esiste: FIRMS (Fire Information for Resource Management
System) pubblica i pixel caldi rilevati da MODIS e VIIRS entro poche ore
dall'acquisizione. E' il dato di ingresso del sistema, ma e' anche l'unico
pezzo che richiede una chiave e una connessione, cioe' le due cose che SPEC.md
vieta di rendere obbligatorie. Qui la regola si applica alla lettera: senza
`FIRMS_MAP_KEY` non si prova nemmeno a chiamare la rete, si leggono le fixture
locali, e lo si scrive nel log a chiare lettere, perche' un utente che vede
numeri plausibili deve sapere se sono dati veri o dati generati.

Sulla latenza, che cambia cosa si puo' chiedere al dato:

- i prodotti NRT (near real time) sono disponibili circa tre ore dopo
  l'acquisizione. Per una simulazione di supporto alle decisioni va bene: il
  ciclo decisionale di una sala operativa AIB e' dello stesso ordine. Per
  l'intervento immediato no, e infatti Tagliafuoco non e' uno strumento
  operativo;
- i prodotti SP (standard processing, l'archivio) arrivano con qualche giorno
  di ritardo ma sono rielaborati: geolocalizzazione corretta con le effemeridi
  definitive e confidenza ricalcolata. Per rigiocare una stagione passata si
  usano questi, e non gli NRT, perche' sono il dato migliore disponibile e
  perche' non cambiano piu' sotto i piedi fra un'esecuzione e l'altra.

Sulla cache: l'API accetta al massimo dieci giorni per richiesta, quindi una
stagione sono una dozzina di chiamate per prodotto. Sono risposte immutabili
(l'archivio non cambia), quindi si scrivono su disco in `data/raw/` e non si
riscaricano. La cache si chiude anche sulla bounding box, altrimenti cambiare
area di interesse leggerebbe il file sbagliato senza accorgersene.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime, time as time_of_day, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence, Union

from tagliafuoco.config import (
    CALABRIA_BBOX,
    FIRMS_BASE_URL,
    FIRMS_MAP_KEY,
    FIRMS_SOURCES_ARCHIVE,
    FIXTURES_DIR,
    RAW_DIR,
    has_firms_key,
)
from tagliafuoco.domain.models import BoundingBox, Detection
from tagliafuoco.ingest import normalize

logger = logging.getLogger(__name__)


# Limite dell'API per area: una richiesta copre al massimo dieci giorni.
FIRMS_MAX_DAY_RANGE = 10

# Rete: tempi contenuti e pochi tentativi. Se FIRMS e' giu', il progetto deve
# degradare sulle fixture in fretta, non restare appeso. L'attesa fra i
# tentativi raddoppia (0.8 s, 1.6 s): e' un backoff esponenziale minimo, giusto
# per non insistere su un errore transitorio.
HTTP_TIMEOUT_S = 30.0
HTTP_RETRIES = 3
HTTP_BACKOFF_S = 0.8

DateLike = Union[str, date, datetime]


class FirmsError(RuntimeError):
    """FIRMS ha risposto male e non c'e' una ricaduta utilizzabile."""


# --------------------------------------------------------------------------
# Date e URL
# --------------------------------------------------------------------------


def _as_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


def _bbox_tuple(bbox: Optional[BoundingBox]) -> tuple[float, float, float, float]:
    """Porta una bounding box nell'ordine che vuole FIRMS."""
    if bbox is None:
        return CALABRIA_BBOX
    return (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)


def build_area_url(
    source: str,
    day_range: int,
    start: date,
    *,
    bbox: Optional[BoundingBox] = None,
    map_key: Optional[str] = None,
) -> str:
    """Compone l'URL dell'API per area.

    Forma documentata da FIRMS:
    `{base}/{MAP_KEY}/{source}/{lon_min},{lat_min},{lon_max},{lat_max}/{day_range}/{date}`

    La data e' il PRIMO giorno della finestra e `day_range` quanti giorni
    prenderne a partire da li'. La chiave finisce dentro l'URL, non in un
    header: e' una scelta di FIRMS, non nostra, ed e' il motivo per cui l'URL
    completo non va mai stampato nel log.
    """
    lon_min, lat_min, lon_max, lat_max = _bbox_tuple(bbox)
    key = map_key if map_key is not None else FIRMS_MAP_KEY
    area = f"{lon_min},{lat_min},{lon_max},{lat_max}"
    return (
        f"{FIRMS_BASE_URL}/{key}/{source}/{area}/{day_range}/{start.isoformat()}"
    )


def _date_chunks(
    start: date, end: date, max_days: int = FIRMS_MAX_DAY_RANGE
) -> list[tuple[date, int]]:
    """Spezza l'intervallo in blocchi accettati dall'API.

    Ogni blocco e' una coppia (primo giorno, quanti giorni), che e' la forma
    che vuole l'URL per area. Gli estremi invertiti si raddrizzano invece di
    dare una lista vuota: e' l'errore di battitura piu' comune e non merita
    una risposta silenziosamente sbagliata.

    `max_days` deve essere almeno 1. Con zero il cursore non avanzerebbe mai e
    il ciclo non finirebbe: e' un blocco, non un'eccezione, cioe' il genere di
    difetto che si manifesta come un test che non torna piu' indietro.
    """
    if max_days < 1:
        raise ValueError(
            f"max_days deve essere almeno 1 giorno, ricevuto {max_days}"
        )
    if end < start:
        start, end = end, start
    chunks: list[tuple[date, int]] = []
    cursor = start
    while cursor <= end:
        span = min(max_days, (end - cursor).days + 1)
        chunks.append((cursor, span))
        cursor = cursor + timedelta(days=span)
    return chunks


# --------------------------------------------------------------------------
# Cache su disco
# --------------------------------------------------------------------------


def _cache_path(
    source: str,
    start: date,
    day_range: int,
    bbox: Optional[BoundingBox],
    cache_dir: Optional[Path],
) -> Path:
    """Percorso del file di cache per una singola richiesta.

    Nel nome entra un digest della bounding box: due aree diverse non devono
    condividere il file, e mettere le coordinate per esteso darebbe nomi
    illeggibili e problemi con i separatori su Windows.
    """
    area = ",".join(f"{v:.4f}" for v in _bbox_tuple(bbox))
    digest = hashlib.sha1(area.encode("utf-8")).hexdigest()[:8]
    directory = cache_dir or RAW_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"firms_{source}_{digest}_{start.isoformat()}_{day_range}d.csv"


# --------------------------------------------------------------------------
# Rete
# --------------------------------------------------------------------------


def _download(url: str, redacted: str) -> str:
    """Scarica un CSV con pochi tentativi e tempi contenuti.

    `httpx` si importa qui dentro e non in testa al modulo: chi usa solo le
    fixture non deve pagare l'import, e soprattutto il modulo resta importabile
    anche se `httpx` non e' installato. Il progetto deve girare senza rete, e
    "senza rete" e' bene che includa "senza la libreria per la rete".
    """
    import httpx

    last_error: Optional[Exception] = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            response = httpx.get(url, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001 - qualsiasi errore vale un ritentativo
            last_error = exc
            if attempt < HTTP_RETRIES:
                pause = HTTP_BACKOFF_S * (2 ** (attempt - 1))
                logger.warning(
                    "FIRMS %s: tentativo %d fallito (%s), riprovo fra %.1f s",
                    redacted,
                    attempt,
                    type(exc).__name__,
                    pause,
                )
                time.sleep(pause)
    raise FirmsError(f"FIRMS non raggiungibile per {redacted}: {last_error}")


def fetch_csv(
    source: str,
    start: date,
    day_range: int,
    *,
    bbox: Optional[BoundingBox] = None,
    use_cache: bool = True,
    map_key: Optional[str] = None,
    cache_dir: Optional[Path] = None,
) -> str:
    """Restituisce il CSV grezzo di una singola richiesta, dalla cache o dalla rete.

    In cache finisce solo cio' che ha l'aspetto di un CSV. FIRMS segnala la
    chiave scaduta o la query malformata con una risposta 200 che contiene un
    messaggio in chiaro: salvarla sul disco la trasformerebbe in un guasto
    permanente, perche' da li' in poi ogni esecuzione rileggerebbe il
    messaggio dalla cache senza mai riprovare la rete, anche dopo aver messo a
    posto la chiave. E' un errore che si nota settimane dopo averlo commesso.
    """
    path = _cache_path(source, start, day_range, bbox, cache_dir)
    if use_cache and path.exists():
        logger.debug("FIRMS %s %s: letto dalla cache %s", source, start, path.name)
        return path.read_text(encoding="utf-8")

    url = build_area_url(source, day_range, start, bbox=bbox, map_key=map_key)
    # Nel log non finisce mai l'URL completo: conterrebbe la chiave.
    redacted = f"{source} {start.isoformat()} +{day_range}g"
    text = _download(url, redacted)
    if use_cache:
        if normalize.looks_like_firms_csv(text):
            path.write_text(text, encoding="utf-8")
            logger.debug("FIRMS %s: risposta salvata in %s", redacted, path.name)
        else:
            logger.warning(
                "FIRMS %s: risposta non CSV (%r), non la metto in cache",
                redacted,
                text.strip()[:120],
            )
    return text


# --------------------------------------------------------------------------
# Fixture locali
# --------------------------------------------------------------------------


def _fixture_files(
    sources: Optional[Sequence[str]], fixtures_dir: Optional[Path]
) -> list[Path]:
    """CSV di fixture da leggere, filtrati per prodotto quando richiesto.

    Le fixture si chiamano come il prodotto FIRMS a cui corrispondono
    (`VIIRS_SNPP_SP_calabria_2025.csv`), cosi' il filtro per sorgente e' un
    confronto sul nome del file e non serve reinterpretare il contenuto.
    """
    directory = fixtures_dir or FIXTURES_DIR
    if not directory.exists():
        return []
    files = sorted(p for p in directory.glob("*.csv") if p.is_file())
    if not sources:
        return files
    wanted = {s.upper() for s in sources}
    selected = [p for p in files if any(p.name.upper().startswith(s) for s in wanted)]
    if not selected:
        logger.warning(
            "nessuna fixture per le sorgenti %s in %s: uso tutte quelle presenti",
            ", ".join(sorted(wanted)),
            directory,
        )
        return files
    return selected


def fetch_from_fixtures(
    start: DateLike,
    end: DateLike,
    sources: Optional[Sequence[str]] = None,
    *,
    bbox: Optional[BoundingBox] = None,
    fixtures_dir: Optional[Path] = None,
) -> list[Detection]:
    """Legge i rilevamenti dalle fixture sintetiche, senza toccare la rete.

    Passa per lo stesso `normalize.parse_csv` del percorso di rete: e' il
    motivo per cui le fixture sono nel formato esatto di FIRMS. Se il parser
    si rompesse sui dati veri, si romperebbe anche qui, e i test lo vedrebbero.

    Gli estremi invertiti si raddrizzano come in `fetch_detections`: le due
    porte d'ingresso del modulo devono rispondere allo stesso modo allo stesso
    errore, altrimenti la presenza o l'assenza della chiave cambierebbe il
    comportamento e il percorso senza chiave smetterebbe di essere una prova
    attendibile di quello con chiave.
    """
    first, last = _as_date(start), _as_date(end)
    if last < first:
        first, last = last, first
    files = _fixture_files(sources, fixtures_dir)
    if not files:
        logger.error(
            "nessuna fixture in %s: rigenerale con "
            "python data/fixtures/genera_fixture_2025.py",
            fixtures_dir or FIXTURES_DIR,
        )
        return []

    detections: list[Detection] = []
    for path in files:
        detections.extend(
            normalize.parse_csv(path.read_text(encoding="utf-8"), bbox=bbox)
        )

    window_start = datetime.combine(first, time_of_day.min, tzinfo=timezone.utc)
    window_end = datetime.combine(last, time_of_day.max, tzinfo=timezone.utc)
    detections = normalize.filter_by_time(detections, window_start, window_end)
    return normalize.dedupe_detections(detections)


# --------------------------------------------------------------------------
# Funzione pubblica
# --------------------------------------------------------------------------


def fetch_detections(
    start_date: DateLike,
    end_date: DateLike,
    sources: Optional[Sequence[str]] = None,
    use_cache: bool = True,
    *,
    bbox: Optional[BoundingBox] = None,
    map_key: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    fixtures_dir: Optional[Path] = None,
) -> list[Detection]:
    """Rilevamenti normalizzati e deduplicati nell'intervallo di date.

    Se la chiave FIRMS manca, non si tenta la rete: si leggono le fixture. La
    decisione e' presa una volta sola, qui, e dichiarata nel log. Tutto il
    resto del sistema chiama questa funzione e non sa se sta guardando dati
    veri o dati generati, che e' esattamente cio' che permette di sviluppare e
    valutare senza chiave.

    `sources` accetta i nomi dei prodotti FIRMS; di default si usa l'archivio
    (SP), perche' il caso d'uso principale e' rigiocare una stagione passata e
    l'archivio e' rielaborato e stabile.
    """
    first, last = _as_date(start_date), _as_date(end_date)
    if last < first:
        first, last = last, first
    products = list(sources) if sources else list(FIRMS_SOURCES_ARCHIVE)

    if not has_firms_key() and map_key is None:
        logger.info(
            "FIRMS_MAP_KEY assente: nessuna chiamata di rete, leggo i "
            "rilevamenti dalle fixture sintetiche in %s (dati generati, non "
            "osservati: vedi il README della cartella)",
            fixtures_dir or FIXTURES_DIR,
        )
        return fetch_from_fixtures(
            first, last, products, bbox=bbox, fixtures_dir=fixtures_dir
        )

    detections: list[Detection] = []
    failures: list[str] = []
    for product in products:
        for chunk_start, span in _date_chunks(first, last):
            try:
                text = fetch_csv(
                    product,
                    chunk_start,
                    span,
                    bbox=bbox,
                    use_cache=use_cache,
                    map_key=map_key,
                    cache_dir=cache_dir,
                )
            except FirmsError as exc:
                failures.append(str(exc))
                continue
            detections.extend(normalize.parse_csv(text, bbox=bbox))

    if failures and not detections:
        logger.error(
            "FIRMS non ha restituito nulla (%d richieste fallite): ricado "
            "sulle fixture sintetiche",
            len(failures),
        )
        return fetch_from_fixtures(
            first, last, products, bbox=bbox, fixtures_dir=fixtures_dir
        )

    if failures:
        # Dati parziali: qualche blocco di giorni manca. Non si ricade sulle
        # fixture, perche' mescolare dati veri e dati generati nella stessa
        # lista sarebbe molto peggio di un buco; ma tacere sarebbe altrettanto
        # sbagliato, perche' un buco nella serie si legge come "in quei giorni
        # non e' bruciato niente" e falserebbe la rigiocata della stagione.
        logger.warning(
            "FIRMS: %d richieste su %d fallite, la serie restituita ha dei "
            "buchi (prima: %s)",
            len(failures),
            len(products) * len(_date_chunks(first, last)),
            failures[0],
        )

    unique = normalize.dedupe_detections(detections)
    logger.info(
        "FIRMS %s - %s: %d rilevamenti dopo deduplica su %d prodotti",
        first.isoformat(),
        last.isoformat(),
        len(unique),
        len(products),
    )
    return unique
