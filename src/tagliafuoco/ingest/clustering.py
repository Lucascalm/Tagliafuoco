"""Dai pixel caldi ai focolai: clustering spaziotemporale con DBSCAN.

Perche' questo modulo esiste: un rilevamento non e' un incendio. Un fronte di
fiamma largo due chilometri produce venti pixel VIIRS, e lo stesso fronte visto
da SNPP e da NOAA-20 a dodici minuti di distanza ne produce quaranta. Il solver
non deve pianificare su quaranta oggetti: deve pianificare su un focolaio.

Perche' DBSCAN e non k-means. k-means vuole sapere in anticipo quanti gruppi
cercare, e quanti incendi ci siano e' esattamente la cosa che non sappiamo.
Inoltre k-means impone gruppi convessi e di dimensione simile, mentre un fronte
di fiamma e' allungato e irregolare, e la stagione alterna notti con un focolaio
a pomeriggi con quaranta. DBSCAN prende invece due parametri fisici e leggibili:
quanto devono essere vicini due pixel per essere lo stesso fuoco (`eps`) e
quanti pixel servono per fare un gruppo (`min_samples`). Con
`CLUSTER_MIN_SAMPLES = 1` nessun pixel viene buttato via come rumore: un pixel
isolato ad alta confidenza e' un focolaio piccolo, non un errore, e il filtro
sul rumore lo ha gia' fatto `normalize.py` con la soglia di confidenza.

Perche' la metrica haversine e i radianti. La distanza euclidea su gradi
sbaglia, e sbaglia in modo asimmetrico: alla latitudine della Calabria un grado
di longitudine vale circa 87 km e un grado di latitudine circa 111 km, quindi
un `eps` in gradi produrrebbe ellissi invece di cerchi. `scikit-learn` fornisce
la metrica `haversine`, che pero' pretende le coordinate in radianti e
restituisce distanze angolari: per questo `eps` si ottiene dividendo i
chilometri per il raggio terrestre.

Perche' prima il tempo e poi lo spazio. DBSCAN lavora su una metrica sola. Se
si mettessero latitudine, longitudine e tempo nello stesso spazio servirebbe un
cambio arbitrario fra chilometri e ore. Qui si fa la cosa piu' onesta: si taglia
la serie in finestre temporali di `CLUSTER_TIME_WINDOW_HOURS` e si raggruppa
nello spazio dentro ogni finestra. Due pixel nello stesso punto a venti ore di
distanza finiscono in finestre diverse e restano focolai distinti, che e' il
comportamento voluto: il satellite ripassa il giorno dopo e trova un incendio
nuovo, non lo stesso incendio.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

import numpy as np
from sklearn.cluster import DBSCAN

from tagliafuoco.config import (
    CLUSTER_EPS_KM,
    CLUSTER_MIN_SAMPLES,
    CLUSTER_TIME_WINDOW_HOURS,
    EARTH_RADIUS_KM,
)
from tagliafuoco.domain.models import Detection, FireCluster, GeoPoint

logger = logging.getLogger(__name__)


# Ancoraggio assoluto delle finestre temporali. Ancorare all'epoca invece che
# al primo rilevamento del lotto serve alla riproducibilita': lo stesso pixel
# cade sempre nella stessa finestra, che lo si analizzi da solo o dentro un
# lotto piu' grande. Il prezzo dichiarato e' l'artefatto di bordo: due pixel a
# un'ora di distanza ma a cavallo del confine di finestra finiscono separati.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Gradi di latitudine per chilometro, alla latitudine della Calabria. Valori
# medi della sfera di raggio `EARTH_RADIUS_KM`, usati solo per costruire la
# griglia di deduplica delle aree: non e' geodesia di precisione, e per
# decidere se due pixel si sovrappongono e' piu' che sufficiente.
_KM_PER_DEG_LAT = math.pi * EARTH_RADIUS_KM / 180.0


# --------------------------------------------------------------------------
# Geodesia minima
# --------------------------------------------------------------------------


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Distanza in linea d'aria fra due punti, su sfera di raggio 6371 km.

    E' la stessa formula che usano `optimize/` e `validate/`, ma qui serve per
    tutt'altro (raggio del focolaio e vicinanza ai centri abitati) e non viene
    condivisa con loro: SPEC.md vuole che il validatore ricalcoli i tempi con
    codice proprio, non che li erediti.
    """
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(
        dlon / 2.0
    ) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _as_utc(moment: datetime) -> datetime:
    """Un datetime naive viene interpretato come UTC, non come ora locale."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def time_bucket(moment: datetime, window_hours: float) -> int:
    """Indice della finestra temporale che contiene l'istante dato.

    La finestra e' ancorata all'epoca Unix, non al primo dato del lotto: e'
    quello che rende il clustering riproducibile, perche' lo stesso pixel cade
    sempre nella stessa finestra sia che lo si analizzi da solo sia dentro la
    stagione intera. Il prezzo dichiarato e' l'artefatto di bordo descritto in
    testa al modulo.

    Una finestra nulla o negativa non e' un caso limite da tollerare in
    silenzio: darebbe una divisione per zero dentro una comprensione, cioe' un
    errore che arriva lontano dal punto in cui e' stato commesso. Meglio
    fermarsi qui con un messaggio che dice quale parametro e' sbagliato.
    """
    if window_hours <= 0.0:
        raise ValueError(
            "la finestra temporale del clustering deve essere positiva, "
            f"ricevuto {window_hours}"
        )
    seconds = (_as_utc(moment) - _EPOCH).total_seconds()
    return int(math.floor(seconds / (window_hours * 3600.0)))


# --------------------------------------------------------------------------
# Grandezze di un cluster
# --------------------------------------------------------------------------


def weighted_centroid(detections: Sequence[Detection]) -> GeoPoint:
    """Centroide pesato sulla potenza radiativa (FRP) dei pixel.

    Perche' pesato e non media semplice: la FRP misura quanta energia il
    pixel sta irraggiando, quindi e' un indicatore diretto di dove il fuoco e'
    attivo adesso. Un fronte in avanzamento lascia dietro di se' una coda di
    pixel ancora caldi ma in raffreddamento, che nella media semplice pesano
    quanto la testa del fronte e tirano il centroide all'indietro. Pesando
    sulla FRP il centroide si sposta verso la parte viva dell'incendio, che e'
    il punto dove ha senso mandare i mezzi.

    Se la FRP totale e' nulla (dato mancante nel prodotto) si ricade sulla
    media semplice: meglio un centroide geometrico che una divisione per zero.

    Su una sequenza vuota si solleva `ValueError` invece di restituire un
    punto finto: il centroide di un insieme vuoto non esiste, e restituire
    (0, 0) metterebbe un focolaio fantasma nel golfo di Guinea.
    """
    if not detections:
        raise ValueError(
            "weighted_centroid richiede almeno un rilevamento: "
            "il centroide di un insieme vuoto non e' definito"
        )
    weights = [max(0.0, d.frp_mw) for d in detections]
    total = sum(weights)
    if total <= 0.0:
        n = float(len(detections))
        return GeoPoint(
            lat=sum(d.point.lat for d in detections) / n,
            lon=sum(d.point.lon for d in detections) / n,
        )
    lat = sum(d.point.lat * w for d, w in zip(detections, weights)) / total
    lon = sum(d.point.lon * w for d, w in zip(detections, weights)) / total
    # La media aritmetica delle longitudini va in crisi a cavallo
    # dell'antimeridiano. La Calabria sta fra 15 e 18 gradi est: il caso non si
    # presenta, e la semplificazione e' dichiarata qui invece che nascosta.
    return GeoPoint(lat=lat, lon=lon)


def _pixel_side_km(det: Detection) -> float:
    """Lato nominale del pixel, come radice dell'area. Serve alla griglia."""
    return math.sqrt(max(det.pixel_area_km2, 1e-9))


def burned_area_km2(detections: Sequence[Detection], centroid: GeoPoint) -> float:
    """Somma delle aree dei pixel, deduplicando le sovrapposizioni.

    Regola dichiarata, scelta per essere semplice piu' che esatta:

    1. si costruisce una griglia regolare il cui lato e' il pixel piu' grosso
       presente nel cluster (1 km se c'e' MODIS, 375 m se c'e' solo VIIRS);
    2. ogni rilevamento cade in una cella;
    3. di ogni cella si tiene una sola area, la maggiore fra quelle dei
       rilevamenti che ci cadono dentro;
    4. si sommano le aree tenute.

    Prendere il lato della griglia dal pixel piu' grosso e' il punto che evita
    l'errore peggiore: un pixel MODIS da 1 km copre circa sette pixel VIIRS, e
    con una griglia fine lo stesso fronte visto dai due strumenti verrebbe
    contato due volte. Resta una stima per eccesso quando il fronte occupa solo
    una parte del pixel: la risoluzione del sensore e' il limite del dato, non
    un difetto del codice. L'area vera percorsa si misura a terra o con
    Sentinel-2 dopo l'evento, non con i pixel caldi durante l'evento.
    """
    if not detections:
        return 0.0
    cell_km = max(_pixel_side_km(d) for d in detections)
    cell_lat_deg = cell_km / _KM_PER_DEG_LAT
    cos_lat = max(0.1, math.cos(math.radians(centroid.lat)))
    cell_lon_deg = cell_km / (_KM_PER_DEG_LAT * cos_lat)

    cells: dict[tuple[int, int], float] = {}
    for det in detections:
        key = (
            int(math.floor(det.point.lat / cell_lat_deg)),
            int(math.floor(det.point.lon / cell_lon_deg)),
        )
        cells[key] = max(cells.get(key, 0.0), det.pixel_area_km2)
    return sum(cells.values())


def make_fire_id(detections: Sequence[Detection]) -> str:
    """Identificatore del focolaio, derivato dal solo contenuto del cluster.

    Non dipende dall'ordine di arrivo ne' dalla posizione nella lista di
    uscita: gli id dei rilevamenti si ordinano e si riassumono in un digest.
    Stesso insieme di pixel, stesso `fire_id`, anche fra esecuzioni diverse e
    su macchine diverse. Senza questa proprieta' la rigiocata della stagione
    non sarebbe confrontabile con se stessa.
    """
    payload = "|".join(sorted(d.detection_id for d in detections))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"F{digest[:12]}"


def build_cluster(detections: Sequence[Detection]) -> FireCluster:
    """Costruisce un `FireCluster` a partire dai suoi rilevamenti.

    Gli istanti si portano tutti in UTC prima di ordinarli, e non per
    pignoleria: confrontare un datetime con fuso e uno senza solleva
    `TypeError`, e un `Detection` costruito a mano in un test o letto da un
    CSV di altra provenienza puo' benissimo essere naive. La convenzione e'
    quella di `_as_utc`: naive significa UTC, perche' FIRMS pubblica in UTC.

    I `Detection` in ingresso non vengono modificati. La conversione vive solo
    nella chiave di ordinamento e nei due estremi del focolaio: mutare
    l'ingresso farebbe sanguinare lo stato verso chi ha passato la lista, che
    e' il difetto che il registro dei dati statici evita con lo stesso
    ragionamento (vedi `registry.load_fleet`).
    """
    if not detections:
        raise ValueError(
            "build_cluster richiede almeno un rilevamento: un focolaio senza "
            "pixel non ha ne' centroide ne' istanti"
        )
    ordered = sorted(
        detections, key=lambda d: (_as_utc(d.acquired_at), d.detection_id)
    )
    centroid = weighted_centroid(ordered)
    radius = max((haversine_km(centroid, d.point) for d in ordered), default=0.0)
    frps = [max(0.0, d.frp_mw) for d in ordered]
    confidences = [d.confidence for d in ordered]

    return FireCluster(
        fire_id=make_fire_id(ordered),
        centroid=centroid,
        detections=list(ordered),
        first_seen=_as_utc(ordered[0].acquired_at),
        last_seen=_as_utc(ordered[-1].acquired_at),
        total_frp_mw=round(sum(frps), 3),
        max_frp_mw=round(max(frps), 3),
        mean_confidence=round(sum(confidences) / len(confidences), 3),
        area_km2=round(burned_area_km2(ordered, centroid), 6),
        radius_km=round(radius, 4),
    )


# --------------------------------------------------------------------------
# Funzione pubblica
# --------------------------------------------------------------------------


def cluster_detections(
    detections: Iterable[Detection],
    eps_km: Optional[float] = None,
    time_window_hours: Optional[float] = None,
    min_samples: Optional[int] = None,
) -> list[FireCluster]:
    """Raggruppa i rilevamenti in focolai. Deterministica per costruzione.

    Il determinismo non e' gratuito e si ottiene in tre punti:

    - i rilevamenti si deduplicano e si ordinano per `detection_id` prima di
      entrare in DBSCAN, cosi' l'ordine della lista in ingresso non conta;
    - le finestre temporali sono ancorate all'epoca, non al primo dato;
    - il `fire_id` e' un digest del contenuto, non un contatore.

    Serve alla valutazione: il confronto con la baseline ha senso solo se i
    due pianificatori vedono gli stessi focolai con gli stessi nomi.
    """
    eps = CLUSTER_EPS_KM if eps_km is None else eps_km
    window = (
        CLUSTER_TIME_WINDOW_HOURS if time_window_hours is None else time_window_hours
    )
    samples = CLUSTER_MIN_SAMPLES if min_samples is None else min_samples

    # I parametri si controllano qui, prima di toccare i dati. Lasciandoli
    # passare, `eps` nullo verrebbe rifiutato da scikit-learn con un messaggio
    # che parla di DBSCAN e non dice a chi legge che il valore sbagliato e'
    # arrivato dal chiamante, e `window` nullo darebbe una divisione per zero
    # dentro una comprensione, lontano dal punto in cui e' stato commesso
    # l'errore. Gli stessi controlli documentano quali sono le unita' di
    # misura attese: chilometri e ore, non metri e minuti.
    if eps <= 0.0:
        raise ValueError(f"eps_km deve essere positivo (chilometri), ricevuto {eps}")
    if window <= 0.0:
        raise ValueError(
            f"time_window_hours deve essere positivo (ore), ricevuto {window}"
        )
    if samples < 1:
        raise ValueError(f"min_samples deve essere almeno 1, ricevuto {samples}")

    unique: dict[str, Detection] = {}
    for det in detections:
        unique.setdefault(det.detection_id, det)
    ordered = sorted(unique.values(), key=lambda d: d.detection_id)
    if not ordered:
        return []

    # DBSCAN con metrica haversine vuole i radianti e restituisce distanze
    # angolari: eps in chilometri diviso il raggio terrestre da' l'angolo al
    # centro corrispondente. Sbagliare questa divisione e' l'errore classico e
    # non si vede subito, perche' il clustering continua a funzionare ma con
    # una soglia sbagliata di tre ordini di grandezza.
    eps_rad = eps / EARTH_RADIUS_KM

    by_window: dict[int, list[Detection]] = defaultdict(list)
    for det in ordered:
        by_window[time_bucket(det.acquired_at, window)].append(det)

    clusters: list[FireCluster] = []
    for bucket in sorted(by_window):
        group = by_window[bucket]
        coords = np.radians(
            np.array([[d.point.lat, d.point.lon] for d in group], dtype=float)
        )
        labels = DBSCAN(
            eps=eps_rad,
            min_samples=samples,
            metric="haversine",
            algorithm="ball_tree",
        ).fit_predict(coords)

        members: dict[int, list[Detection]] = defaultdict(list)
        for index, label in enumerate(labels):
            # Con min_samples = 1 l'etichetta -1 non compare mai. Se qualcuno
            # alza min_samples, il punto marcato come rumore diventa un
            # focolaio a se' invece di sparire: buttare via un pixel caldo ad
            # alta confidenza sarebbe una decisione operativa, non tecnica.
            key = int(label) if label >= 0 else -1_000_000 - index
            members[key].append(group[index])

        for key in sorted(members):
            clusters.append(build_cluster(members[key]))

    # `first_seen` esce da `build_cluster` sempre aware e in UTC, quindi qui
    # non si possono mescolare datetime con e senza fuso: la chiave e' sicura.
    # Il `fire_id` in coda rompe i pareggi in modo stabile fra esecuzioni.
    clusters.sort(key=lambda c: (c.first_seen, -c.total_frp_mw, c.fire_id))
    logger.debug(
        "clustering: %d rilevamenti, %d finestre da %.1f h, %d focolai",
        len(ordered),
        len(by_window),
        window,
        len(clusters),
    )
    return clusters
