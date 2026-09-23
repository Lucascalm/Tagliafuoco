"""Normalizzazione delle righe CSV di NASA FIRMS in oggetti `Detection`.

Perche' questo modulo esiste: FIRMS non pubblica un formato solo, ne pubblica
uno per prodotto. MODIS e VIIRS hanno colonne diverse (`brightness` contro
`bright_ti4`), risoluzioni diverse (1 km contro 375 m) e soprattutto due
convenzioni incompatibili per la confidenza: MODIS la da' come numero da 0 a
100, VIIRS come classe fra `l`, `n` e `h`. Se questa differenza filtrasse nel
resto del sistema, ogni modulo a valle dovrebbe sapere da quale satellite
arriva il dato. Qui la differenza si ferma: da questo modulo esce una sola
entita', `Detection`, e chi sta a valle non deve piu' chiedersi chi l'ha vista.

Il secondo motivo e' la tolleranza. I CSV di FIRMS cambiano colonne fra le
versioni dei prodotti (la colonna `country_id` compare solo nell'API per area,
`bright_t31` esiste su MODIS e non su VIIRS). Un parser rigido si romperebbe al
primo cambio di collezione. Qui ogni colonna e' opzionale: se manca, il campo
resta a `None` e il rilevamento sopravvive. Si scartano soltanto le righe che
non hanno il minimo indispensabile, cioe' coordinate e istante.

Il terzo motivo e' la deduplica. Finestre di download che si sovrappongono
restituiscono le stesse righe piu' volte. `make_detection_id` costruisce un
identificatore deterministico dal contenuto fisico dell'osservazione
(posizione, istante, satellite), quindi lo stesso pixel scaricato due volte
produce due volte lo stesso id e la deduplica diventa una questione di insieme.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence

from tagliafuoco.config import CALABRIA_BBOX, CONFIDENCE_MAP, MIN_CONFIDENCE
from tagliafuoco.domain.models import BoundingBox, Detection, GeoPoint

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Alias delle colonne
# --------------------------------------------------------------------------

# Per ogni campo di `Detection` elenchiamo i nomi che FIRMS usa nei vari
# prodotti, in ordine di preferenza. Il primo nome presente e non vuoto vince.
# Tenere gli alias in una tabella invece che in una catena di `if` rende
# esplicito quali differenze fra prodotti stiamo assorbendo.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "long"),
    # MODIS: brightness (canale a 4 micrometri, pixel da 1 km).
    # VIIRS: bright_ti4 (banda I4, sempre 4 micrometri, ma pixel da 375 m).
    "brightness": ("bright_ti4", "brightness"),
    "scan": ("scan",),
    "track": ("track",),
    "acq_date": ("acq_date", "acquisition_date"),
    "acq_time": ("acq_time", "acquisition_time"),
    "satellite": ("satellite",),
    "instrument": ("instrument",),
    "confidence": ("confidence", "conf"),
    "version": ("version",),
    "frp": ("frp",),
    "daynight": ("daynight", "day_night"),
}

# Mappatura estesa della confidenza categorica: FIRMS scrive la lettera, ma
# alcune esportazioni e alcuni cruscotti scrivono la parola per esteso.
_CATEGORICAL_CONFIDENCE: dict[str, float] = dict(CONFIDENCE_MAP)
_CATEGORICAL_CONFIDENCE.update(
    {
        "low": CONFIDENCE_MAP["l"],
        "nominal": CONFIDENCE_MAP["n"],
        "high": CONFIDENCE_MAP["h"],
    }
)


# --------------------------------------------------------------------------
# Area di interesse
# --------------------------------------------------------------------------


def calabria_bounding_box() -> BoundingBox:
    """La bounding box della Calabria come entita' di dominio.

    `config.CALABRIA_BBOX` e' nell'ordine che vuole l'API di FIRMS
    (lon_min, lat_min, lon_max, lat_max), che non e' l'ordine dei campi di
    `BoundingBox`. La conversione sta qui una volta sola: e' esattamente il
    genere di inversione fra latitudine e longitudine che altrimenti si paga
    con un'ora di ricerca del difetto.
    """
    lon_min, lat_min, lon_max, lat_max = CALABRIA_BBOX
    return BoundingBox(
        min_lat=lat_min, min_lon=lon_min, max_lat=lat_max, max_lon=lon_max
    )


# --------------------------------------------------------------------------
# Lettura tollerante dei campi
# --------------------------------------------------------------------------


def _cell(row: Mapping[str, object], field: str) -> Optional[str]:
    """Restituisce il valore della prima colonna alias presente e non vuota."""
    for name in COLUMN_ALIASES.get(field, (field,)):
        if name in row:
            value = row[name]
            if value is None:
                continue
            text = str(value).strip()
            if text and text.lower() not in ("nan", "null", "none"):
                return text
    return None


def _as_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_confidence(raw: Optional[str]) -> Optional[float]:
    """Porta la confidenza su una sola scala da 0 a 100.

    MODIS pubblica un numero (percentuale stimata dall'algoritmo di
    rilevamento), VIIRS pubblica una classe: `l` low, `n` nominal, `h` high.
    Le classi si mappano sui valori di `config.CONFIDENCE_MAP` (30, 60, 90).
    Quei tre numeri sono una taratura dichiarata, non una conversione
    pubblicata dalla NASA: servono solo a poter applicare una sola soglia,
    `MIN_CONFIDENCE`, a prodotti diversi.
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _CATEGORICAL_CONFIDENCE:
        return _CATEGORICAL_CONFIDENCE[text]
    value = _as_float(text)
    if value is None:
        return None
    # Qualche esportazione esce dalla scala: si riporta nell'intervallo.
    return max(0.0, min(100.0, value))


def parse_acq_time(raw: Optional[str]) -> Optional[tuple[int, int]]:
    """Interpreta l'orario di acquisizione, che FIRMS scrive come HHMM.

    Attenzione allo zero iniziale: FIRMS lo omette, quindi le 00:48 UTC
    arrivano come `48` e le 01:05 come `105`. Leggere la stringa da sinistra
    sarebbe sbagliato, si legge il numero intero e lo si divide per 100.
    """
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    hours, minutes = divmod(int(digits), 100)
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours, minutes


def parse_acquired_at(
    acq_date: Optional[str], acq_time: Optional[str]
) -> Optional[datetime]:
    """Costruisce l'istante di acquisizione come datetime aware in UTC.

    FIRMS pubblica sempre in UTC. Restituire un datetime naive sarebbe un
    invito a confrontarlo per sbaglio con l'ora locale: qui il fuso e'
    esplicito e resta esplicito fino in fondo alla catena.

    Ora assente e ora illeggibile sono due casi diversi e vanno trattati in
    modo diverso. Se la colonna manca o e' vuota si assume mezzanotte, che e'
    la lettura convenzionale di una data senza ora. Se invece la colonna c'e'
    ma non si riesce a leggerla (per esempio `9999`), il rilevamento si
    scarta: assegnargli mezzanotte lo sposterebbe fino a ventiquattro ore
    indietro, cioe' in un'altra finestra di clustering e in un altro scenario,
    e il dato sbagliato sarebbe indistinguibile da uno buono.
    """
    if not acq_date:
        return None
    text = str(acq_date).strip().replace("/", "-")
    day: Optional[datetime] = None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y%m%d"):
        try:
            day = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if day is None:
        return None

    raw_time = None if acq_time is None else str(acq_time).strip()
    if not raw_time:
        hm = (0, 0)
    else:
        parsed = parse_acq_time(raw_time)
        if parsed is None:
            logger.debug("orario di acquisizione illeggibile, riga scartata: %r", raw_time)
            return None
        hm = parsed
    return day.replace(hour=hm[0], minute=hm[1], tzinfo=timezone.utc)


def make_detection_id(
    lat: float, lon: float, acquired_at: datetime, satellite: str
) -> str:
    """Identificatore stabile e deterministico di un rilevamento.

    Deve dipendere solo da cosa e' stato osservato, mai dall'ordine di lettura
    o dal momento del download: due scaricamenti con finestre sovrapposte
    devono produrre lo stesso id per lo stesso pixel, altrimenti la deduplica
    non funziona e lo stesso fronte di fiamma viene contato due volte.

    Le coordinate si arrotondano a cinque decimali (circa un metro) perche'
    FIRMS le pubblica gia' cosi': non arrotondare esporrebbe l'id a differenze
    di rappresentazione in virgola mobile fra una lettura e l'altra.
    """
    payload = "|".join(
        (
            f"{round(lat, 5):.5f}",
            f"{round(lon, 5):.5f}",
            acquired_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            (satellite or "?").strip().upper(),
        )
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"D{digest[:16]}"


def _infer_instrument(row: Mapping[str, object]) -> str:
    """Deduce lo strumento, anche quando la colonna `instrument` manca.

    L'indizio e' la colonna della temperatura di brillanza: `bright_ti4`
    esiste solo su VIIRS, `brightness` solo su MODIS.
    """
    declared = _cell(row, "instrument")
    if declared:
        upper = declared.upper()
        if "VIIRS" in upper:
            return "VIIRS"
        if "MODIS" in upper:
            return "MODIS"
        return upper
    if "bright_ti4" in row:
        return "VIIRS"
    if "brightness" in row:
        return "MODIS"
    return "UNKNOWN"


# --------------------------------------------------------------------------
# Da riga a Detection
# --------------------------------------------------------------------------


def detection_from_row(row: Mapping[str, object]) -> Optional[Detection]:
    """Trasforma una riga di CSV in un `Detection`, oppure restituisce None.

    Non filtra ne' per confidenza ne' per area: quelle sono decisioni di
    politica e stanno in `parse_rows`. Qui si decide soltanto se la riga e'
    leggibile. Serve il minimo indispensabile: latitudine, longitudine e data.
    """
    lat = _as_float(_cell(row, "latitude"))
    lon = _as_float(_cell(row, "longitude"))
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None

    acquired_at = parse_acquired_at(_cell(row, "acq_date"), _cell(row, "acq_time"))
    if acquired_at is None:
        return None

    satellite = _cell(row, "satellite") or "?"
    instrument = _infer_instrument(row)
    confidence = parse_confidence(_cell(row, "confidence"))
    if confidence is None:
        # Una riga senza confidenza non e' inutilizzabile, ma non deve
        # entrare di nascosto sopra la soglia: le si assegna il minimo, cosi'
        # il filtro la scarta a meno che qualcuno non abbassi la soglia apposta.
        confidence = 0.0

    daynight = (_cell(row, "daynight") or "D").upper()[:1]
    if daynight not in ("D", "N"):
        daynight = "D"

    return Detection(
        detection_id=make_detection_id(lat, lon, acquired_at, satellite),
        point=GeoPoint(lat=lat, lon=lon),
        acquired_at=acquired_at,
        satellite=satellite,
        instrument=instrument,
        confidence=confidence,
        frp_mw=_as_float(_cell(row, "frp")) or 0.0,
        brightness_k=_as_float(_cell(row, "brightness")),
        scan_km=_as_float(_cell(row, "scan")),
        track_km=_as_float(_cell(row, "track")),
        daynight=daynight,
        version=_cell(row, "version") or "",
    )


def parse_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    bbox: Optional[BoundingBox] = None,
    min_confidence: Optional[float] = None,
) -> list[Detection]:
    """Normalizza righe gia' lette, applicando i due filtri di politica.

    Si scartano i rilevamenti sotto `MIN_CONFIDENCE` (rumore: nubi calde,
    riflessi speculari, sorgenti industriali deboli) e quelli fuori dalla
    bounding box, perche' l'API per area restituisce il rettangolo pieno e sul
    lato ovest il rettangolo della Calabria e' in buona parte mar Tirreno.
    """
    area = bbox if bbox is not None else calabria_bounding_box()
    threshold = MIN_CONFIDENCE if min_confidence is None else min_confidence

    detections: list[Detection] = []
    unreadable = 0
    off_area = 0
    low_confidence = 0

    for row in rows:
        det = detection_from_row(row)
        if det is None:
            unreadable += 1
            continue
        if not area.contains(det.point):
            off_area += 1
            continue
        if det.confidence < threshold:
            low_confidence += 1
            continue
        detections.append(det)

    if unreadable or off_area or low_confidence:
        logger.debug(
            "normalizzazione: %d tenuti, %d illeggibili, %d fuori area, "
            "%d sotto la confidenza %.0f",
            len(detections),
            unreadable,
            off_area,
            low_confidence,
            threshold,
        )
    return detections


def looks_like_firms_csv(text: str) -> bool:
    """Vero se il testo ha l'aspetto di un CSV di FIRMS, cioe' se ha l'intestazione.

    FIRMS non risponde con un errore HTTP quando la chiave e' scaduta o la
    query e' malformata: risponde 200 con un messaggio in chiaro del tipo
    `Invalid MAP_KEY`. Senza questo controllo quel messaggio diventerebbe zero
    rilevamenti senza spiegazione, e peggio ancora finirebbe nella cache su
    disco, dove verrebbe riletto come se fosse un dato valido a ogni
    esecuzione successiva. Il controllo sta qui, in un posto solo, ed e' usato
    sia dal parser sia dal client prima di scrivere in cache.
    """
    if not text or not text.strip():
        return False
    first_line = text.lstrip("﻿").splitlines()[0].lower()
    return "latitude" in first_line or "lat" in first_line


def parse_csv(
    text: str,
    *,
    bbox: Optional[BoundingBox] = None,
    min_confidence: Optional[float] = None,
) -> list[Detection]:
    """Normalizza un CSV di FIRMS gia' in memoria.

    Il CSV puo' essere quello scaricato dall'API o quello della fixture
    locale: sono lo stesso formato apposta, cosi' il percorso con chiave e
    quello senza chiave esercitano esattamente lo stesso codice di parsing.
    """
    if not text or not text.strip():
        return []
    stripped = text.lstrip("﻿")
    if not looks_like_firms_csv(stripped):
        logger.warning(
            "risposta senza intestazione CSV attesa: %r",
            stripped.splitlines()[0][:120],
        )
        return []
    reader = csv.DictReader(io.StringIO(stripped))
    return parse_rows(reader, bbox=bbox, min_confidence=min_confidence)


def dedupe_detections(detections: Iterable[Detection]) -> list[Detection]:
    """Rimuove i doppioni tenendo il primo incontro di ogni `detection_id`.

    L'ordine di uscita e' deterministico (per id) e non dipende dall'ordine in
    cui i file sono stati letti: senza questo, due esecuzioni che scaricano i
    prodotti in ordine diverso potrebbero produrre cluster diversi a parita'
    di dati.
    """
    unique: dict[str, Detection] = {}
    for det in detections:
        unique.setdefault(det.detection_id, det)
    return sorted(unique.values(), key=lambda d: d.detection_id)


def as_utc(moment: datetime) -> datetime:
    """Porta un istante in UTC, interpretando come UTC quello senza fuso.

    Serve ovunque si confrontino istanti che arrivano da fonti diverse. Un
    `Detection` uscito da `parse_csv` ha sempre il fuso, ma uno costruito a
    mano in un test o letto da un altro formato puo' essere naive, e
    confrontare un datetime aware con uno naive solleva `TypeError`: un errore
    che comparirebbe solo su certi ingressi e a valle, dentro un ordinamento.
    La convenzione naive-uguale-UTC e' quella giusta qui perche' FIRMS
    pubblica in UTC, e sta scritta in un posto solo invece che ripetuta.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def filter_by_time(
    detections: Sequence[Detection], start: datetime, end: datetime
) -> list[Detection]:
    """Tiene i rilevamenti nell'intervallo [start, end], estremi inclusi.

    Estremi inclusi perche' la finestra si costruisce da mezzanotte a
    23:59:59.999999 dello stesso giorno: con l'estremo destro escluso
    l'ultimo minuto dell'ultimo giorno sparirebbe.
    """
    start_utc = as_utc(start)
    end_utc = as_utc(end)
    return [d for d in detections if start_utc <= as_utc(d.acquired_at) <= end_utc]
