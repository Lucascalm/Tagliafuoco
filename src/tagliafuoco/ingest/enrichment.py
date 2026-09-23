"""Contesto territoriale e grandezze derivate di un focolaio.

Perche' questo modulo esiste: il satellite dice dove brucia e quanto irraggia,
e non dice nient'altro. Ma la domanda che il coordinatore si pone non e' "quanti
megawatt", e' "ci sono case vicino, e' dentro un parco, che cosa sta bruciando,
quanta acqua serve". Tutte informazioni che non stanno nel dato satellitare e
vanno incrociate con il territorio. Questo modulo e' il posto dove il pixel
diventa un problema operativo.

Ogni valore prodotto qui e' una stima dichiarata, e vale la pena essere espliciti
su quanto sono grossolane, perche' finiscono dentro la priorita' che guida il
solver:

- `distance_to_wui_km` e' la distanza dal centroide del comune piu' vicino. La
  WUI vera (wildland-urban interface) si calcola sul costruito, cioe' sui
  poligoni degli edifici, non sul centro geometrico del comune. Per un comune
  compatto l'errore e' di qualche centinaio di metri, per un comune sparso con
  frazioni lontane puo' essere di diversi chilometri, e sempre nel verso
  prudenziale sbagliato (dice piu' lontano di quanto sia).
- `elevation_m` e' l'altitudine del municipio del comune piu' vicino, usata come
  procura della quota del focolaio. Un DEM la darebbe giusta al metro.
- `land_cover` e' una regola su quota e provincia, non CORINE Land Cover. E'
  un surrogato: riproduce la zonazione altitudinale della vegetazione calabrese
  a grandi linee e sbaglia sui casi particolari (un rimboschimento a pini sulla
  costa viene classificato macchia).

Le formule del fabbisogno d'acqua e della priorita' sono quelle della sezione 6
di SPEC.md e i coefficienti si leggono da `config.py`: qui non ci sono numeri
liberi, solo soglie di classificazione dichiarate in testa al modulo.
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional, Sequence

from tagliafuoco.config import (
    AREA_NORMALISATION_KM2,
    FRP_NORMALISATION_MW,
    LAND_COVER_WATER_COEFF,
    PRIORITY_WEIGHTS,
    WATER_LITERS_PER_KM2,
    WUI_DECAY_KM,
    WUI_THRESHOLD_KM,
)
from tagliafuoco.domain import registry
from tagliafuoco.domain.models import FireCluster, GeoPoint, LandCover, Severity
from tagliafuoco.ingest.clustering import haversine_km

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Tarature dichiarate di questo modulo
# --------------------------------------------------------------------------

# Raggio entro cui si somma la popolazione. Tre chilometri e' la distanza che
# un fronte con vento moderato puo' percorrere in poche ore: e' il numero che
# risponde alla domanda "quanta gente ha questo incendio potenzialmente
# addosso", non "quanta gente vive nel comune".
POPULATION_RADIUS_KM = 3.0

# Zonazione altitudinale della vegetazione calabrese, surrogato di CORINE.
# Le quote sono lette sulla letteratura forestale regionale e arrotondate:
# sono soglie di classificazione, non misure.
CONIFER_ELEVATION_M = 900.0  # altopiano silano: pino laricio sopra i 900 m
ASPROMONTE_CONIFER_ELEVATION_M = 1300.0  # abetina dell'Aspromonte, piu' in alto
BROADLEAF_ELEVATION_M = 600.0  # faggete e castagneti di media montagna
MIXED_ELEVATION_M = 300.0  # collina interna, bosco misto
# Sotto i 300 m: macchia mediterranea e pinete costiere.

# Province che affacciano sull'altopiano silano e sul Pollino, dove la conifera
# di quota e' la copertura dominante.
CONIFER_PROVINCE = ("Cosenza", "Crotone", "Catanzaro")

# Massimo dei coefficienti di copertura, per normalizzare a 1 il termine
# `f_copertura` della priorita'. Si ricava dalla tabella di config invece che
# essere scritto a mano, cosi' resta corretto se la tabella cambia.
_MAX_LAND_COVER_COEFF = max(LAND_COVER_WATER_COEFF.values())

# Soglie di severita' sulla priorita' di base. Sono una taratura: servono a
# dare un'etichetta leggibile a un numero continuo, e nessun calcolo del
# sistema dipende dall'etichetta. Cambiarle cambia le parole, non i piani.
SEVERITY_THRESHOLDS: tuple[tuple[float, Severity], ...] = (
    (0.70, Severity.EXTREME),
    (0.50, Severity.HIGH),
    (0.30, Severity.MODERATE),
)


# --------------------------------------------------------------------------
# Caricamento dei riferimenti territoriali
# --------------------------------------------------------------------------


def _load_reference_data(
    comuni: Optional[Sequence[Mapping[str, object]]],
    protected_areas: Optional[Sequence[Mapping[str, object]]],
    static_dir: Optional[str],
) -> tuple[tuple, tuple]:
    """Restituisce (comuni, aree protette), da argomento o dal registro.

    L'iniezione esplicita serve ai test, che non devono dipendere dai file
    statici generati da un altro sottosistema, e alla simulazione, che puo'
    voler lavorare su un sottoinsieme del territorio.
    """
    loaded_comuni = (
        tuple(comuni) if comuni is not None else registry.load_comuni(static_dir)
    )
    loaded_areas = (
        tuple(protected_areas)
        if protected_areas is not None
        else registry.load_protected_areas(static_dir)
    )
    return loaded_comuni, loaded_areas


def _point_of(record: Mapping[str, object]) -> Optional[GeoPoint]:
    """Coordinate di un record di riferimento, oppure None se e' malformato.

    Un comune senza coordinate e' un difetto dei dati statici, non
    dell'arricchimento: si salta quel record e si continua. Sollevare
    `KeyError` qui farebbe fallire l'intera rigiocata della stagione per una
    riga sbagliata in un file di un altro sottosistema, che e' una reazione
    sproporzionata a un dato mancante.
    """
    try:
        return GeoPoint(lat=float(record["lat"]), lon=float(record["lon"]))
    except (KeyError, TypeError, ValueError):
        logger.warning(
            "record territoriale senza coordinate utilizzabili, ignorato: %r",
            record.get("nome", record),
        )
        return None


# --------------------------------------------------------------------------
# Regole territoriali
# --------------------------------------------------------------------------


def classify_land_cover(
    elevation_m: Optional[float], provincia: Optional[str]
) -> LandCover:
    """Copertura del suolo stimata da quota e provincia.

    E' un surrogato dichiarato di CORINE Land Cover, non un dato osservato. La
    regola riproduce la zonazione altitudinale della vegetazione calabrese:

    - sopra i 900 m sull'arco Sila-Pollino (province di Cosenza, Crotone,
      Catanzaro) domina il pino laricio, quindi conifere;
    - in Aspromonte la conifera di quota (abete bianco) comincia piu' in alto,
      intorno ai 1300 m, e sotto prevale il bosco misto di faggio e castagno;
    - fra 600 e 900 m faggete e castagneti, cioe' latifoglie;
    - fra 300 e 600 m collina interna a bosco misto;
    - sotto i 300 m macchia mediterranea e pinete costiere.

    Il verso dell'errore conta piu' dell'errore: le conifere hanno il
    coefficiente d'acqua piu' alto (1.4) e la macchia il secondo (1.2), quindi
    classificare male fra queste due sposta poco il fabbisogno, mentre
    classificare un bosco come prateria lo dimezzerebbe. Per questo il ramo di
    default e' la macchia e non la prateria, e per lo stesso motivo una
    provincia sconosciuta (o assente) prende la soglia piu' alta, quella
    dell'Aspromonte: in dubbio si classifica bosco misto invece di conifere,
    cioe' si sceglie la classe che non gonfia il fabbisogno d'acqua.
    """
    if elevation_m is None:
        return LandCover.SHRUBLAND
    conifer_threshold = (
        CONIFER_ELEVATION_M
        if provincia in CONIFER_PROVINCE
        else ASPROMONTE_CONIFER_ELEVATION_M
    )
    if elevation_m >= conifer_threshold:
        return LandCover.CONIFER
    if elevation_m >= CONIFER_ELEVATION_M:
        # Quota da conifere ma fuori dall'arco silano: bosco misto.
        return LandCover.MIXED_FOREST
    if elevation_m >= BROADLEAF_ELEVATION_M:
        return LandCover.BROADLEAF
    if elevation_m >= MIXED_ELEVATION_M:
        return LandCover.MIXED_FOREST
    return LandCover.SHRUBLAND


def wui_factor(distance_km: Optional[float]) -> float:
    """Fattore di interfaccia urbano-foresta, fra 0 e 1.

    Vale 1 fino a `WUI_THRESHOLD_KM` (2 km) e decade linearmente fino a 0 a
    `WUI_DECAY_KM` (10 km), come prescrive la sezione 6 di SPEC.md. Il decadimento
    lineare e' una scelta dichiarata: non c'e' una legge fisica che leghi la
    distanza al rischio per il costruito, c'e' il fatto che oltre i dieci
    chilometri un incendio non minaccia direttamente le case e sotto i due le
    minaccia comunque.

    Distanza ignota vale 0: in assenza di informazione non si alza la priorita'.
    """
    if distance_km is None:
        return 0.0
    if distance_km <= WUI_THRESHOLD_KM:
        return 1.0
    if distance_km >= WUI_DECAY_KM:
        return 0.0
    span = WUI_DECAY_KM - WUI_THRESHOLD_KM
    return (WUI_DECAY_KM - distance_km) / span


def water_demand_liters(
    area_km2: float, max_frp_mw: float, land_cover: LandCover
) -> int:
    """Fabbisogno d'acqua stimato, formula della sezione 6 di SPEC.md.

        litri = area_km2 * WATER_LITERS_PER_KM2 * coeff_copertura * coeff_intensita
        coeff_intensita = 1 + min(1, max_frp_mw / FRP_NORMALISATION_MW)

    I 100.000 litri per km quadrato sono una taratura dichiarata: il fabbisogno
    reale dipende da carico di combustibile, umidita' del combustibile fine e
    vento, che qui non abbiamo. Si usa `max_frp_mw` e non la FRP totale perche'
    l'intensita' che conta per l'efficacia dei lanci e' quella del punto piu'
    caldo: sul fronte di testa l'acqua evapora prima di arrivare al suolo.
    """
    coeff_cover = LAND_COVER_WATER_COEFF.get(land_cover.value, 1.0)
    coeff_intensity = 1.0 + min(1.0, max(0.0, max_frp_mw) / FRP_NORMALISATION_MW)
    return int(
        round(max(0.0, area_km2) * WATER_LITERS_PER_KM2 * coeff_cover * coeff_intensity)
    )


def base_priority(fire: FireCluster) -> float:
    """Priorita' di base del focolaio, formula della sezione 6 di SPEC.md.

        p = 0.35 * norm(frp) + 0.25 * f_wui + 0.20 * norm(area)
          + 0.10 * f_copertura + 0.10 * f_area_protetta

    I pesi si leggono da `config.PRIORITY_WEIGHTS` e sommano a 1, quindi il
    risultato sta fra 0 e 1. Scelte dichiarate dentro la formula:

    - `norm(frp)` usa la FRP totale del focolaio e non il massimo: il massimo
      dice quanto e' violento il punto peggiore, il totale dice quanto grande
      e' l'incendio nel complesso, ed e' il totale a determinare quanti mezzi
      servono. Il massimo entra invece nel fabbisogno d'acqua, dove e'
      l'intensita' locale a contare;
    - `f_copertura` e' il coefficiente d'acqua della copertura riportato a 1,
      cosi' le conifere (1.4) danno 1.0 e il suolo nudo (0.3) circa 0.21. E' un
      riuso dichiarato: la stessa tabella misura quanto una copertura brucia
      male, sia per l'acqua sia per la priorita'.

    Questa priorita' e' "di base" perche' e' quella che deriva dai soli dati.
    Gli ordini del coordinatore tradotti in `ConstraintSet` la possono
    scavalcare: e' il solver a decidere, non questo numero.
    """
    norm_frp = min(1.0, max(0.0, fire.total_frp_mw) / FRP_NORMALISATION_MW)
    norm_area = min(1.0, max(0.0, fire.area_km2) / AREA_NORMALISATION_KM2)
    f_wui = wui_factor(fire.distance_to_wui_km)
    f_cover = (
        LAND_COVER_WATER_COEFF.get(fire.land_cover.value, 1.0) / _MAX_LAND_COVER_COEFF
    )
    f_protected = 1.0 if fire.in_protected_area else 0.0

    score = (
        PRIORITY_WEIGHTS["frp"] * norm_frp
        + PRIORITY_WEIGHTS["wui"] * f_wui
        + PRIORITY_WEIGHTS["area"] * norm_area
        + PRIORITY_WEIGHTS["land_cover"] * f_cover
        + PRIORITY_WEIGHTS["protected"] * f_protected
    )
    return round(min(1.0, max(0.0, score)), 4)


def severity_from_priority(priority: float) -> Severity:
    """Etichetta di severita' a partire dalla priorita' di base."""
    for threshold, level in SEVERITY_THRESHOLDS:
        if priority >= threshold:
            return level
    return Severity.LOW


# --------------------------------------------------------------------------
# Funzioni pubbliche
# --------------------------------------------------------------------------


def enrich_fire(
    fire: FireCluster,
    *,
    comuni: Optional[Sequence[Mapping[str, object]]] = None,
    protected_areas: Optional[Sequence[Mapping[str, object]]] = None,
    static_dir: Optional[str] = None,
) -> FireCluster:
    """Riempie il contesto territoriale e le grandezze derivate del focolaio.

    Modifica il `FireCluster` sul posto e lo restituisce, cosi' la funzione si
    puo' usare sia in stile imperativo sia dentro una comprensione. Il focolaio
    e' un oggetto di lavoro dell'ingest, non e' ancora entrato nello scenario:
    a quel punto diventera' immutabile per convenzione.

    L'ordine dei passaggi non e' arbitrario: la copertura dipende dalla quota,
    che dipende dal comune piu' vicino; il fabbisogno dipende dalla copertura;
    la priorita' dipende da tutto il resto; la severita' dipende dalla
    priorita'.

    La funzione e' idempotente: ogni campo che scrive lo scrive sempre, anche
    quando l'informazione manca (nel qual caso lo azzera). Arricchire due
    volte lo stesso focolaio con gli stessi riferimenti da' quindi lo stesso
    risultato. Serve perche' `build_scenario` puo' riarricchire focolai gia'
    arricchiti, e un arricchimento che lasciasse in giro i valori della
    passata precedente renderebbe il risultato dipendente dalla storia
    dell'oggetto invece che dai dati.
    """
    ref_comuni, ref_areas = _load_reference_data(comuni, protected_areas, static_dir)

    # 1. e 2. Comune piu' vicino e popolazione esposta, in una passata sola.
    #
    #    Una passata invece di tre: la distanza da ogni comune si calcola una
    #    volta e serve sia a trovare il minimo sia a sommare le popolazioni nel
    #    raggio. Con qualche centinaio di comuni e cinquecento focolai per
    #    stagione la differenza si sente, e soprattutto sparisce il rischio che
    #    le due passate usino per sbaglio criteri diversi.
    #
    #    Il pareggio si rompe sul primo incontrato, cioe' sull'ordine del file
    #    dei comuni, che e' fisso: il confronto e' `<` e non `<=` apposta. Non
    #    e' un dettaglio estetico, e' la stessa regola di determinismo del
    #    clustering, perche' il comune scelto decide quota, copertura e quindi
    #    priorita' del focolaio.
    nearest: Optional[Mapping[str, object]] = None
    nearest_km = float("inf")
    exposed_population = 0
    usable_comuni = 0

    for record in ref_comuni:
        point = _point_of(record)
        if point is None:
            continue
        usable_comuni += 1
        distance = haversine_km(fire.centroid, point)
        if distance < nearest_km:
            nearest_km = distance
            nearest = record
        #    Somma di popolazioni comunali intere, quindi sovrastima quando il
        #    comune e' grande e il centro abitato sta tutto dall'altra parte.
        if distance <= POPULATION_RADIUS_KM:
            try:
                exposed_population += int(record.get("popolazione") or 0)
            except (TypeError, ValueError):
                pass

    if nearest is not None:
        fire.comune = str(nearest.get("nome") or "")
        fire.provincia = str(nearest.get("provincia") or "") or None
        fire.nearest_settlement = fire.comune
        fire.distance_to_wui_km = round(nearest_km, 3)
        elevation = nearest.get("altitudine_m")
        try:
            fire.elevation_m = float(elevation) if elevation is not None else None
        except (TypeError, ValueError):
            fire.elevation_m = None
        fire.population_within_3km = exposed_population
    else:
        # Nessun riferimento utilizzabile: si AZZERA il contesto invece di
        # lasciare quello che c'era. Cosi' arricchire due volte lo stesso
        # focolaio da' lo stesso risultato a parita' di riferimenti, e un
        # focolaio senza contesto non porta in giro i dati del focolaio
        # precedente.
        fire.comune = None
        fire.provincia = None
        fire.nearest_settlement = None
        fire.distance_to_wui_km = None
        fire.elevation_m = None
        fire.population_within_3km = 0
        logger.warning(
            "nessun comune di riferimento utilizzabile (%d record letti): il "
            "focolaio %s resta senza contesto territoriale",
            usable_comuni,
            fire.fire_id,
        )

    # 3. Aree protette. La geometria e' un cerchio (vedi registry): basta
    #    sapere se il focolaio ci ricade dentro, non serve il poligono esatto.
    fire.in_protected_area = False
    fire.protected_area_name = None
    for area in ref_areas:
        point = _point_of(area)
        if point is None:
            continue
        try:
            radius = float(area.get("raggio_km") or 0.0)
        except (TypeError, ValueError):
            continue
        if radius > 0.0 and haversine_km(fire.centroid, point) <= radius:
            fire.in_protected_area = True
            fire.protected_area_name = str(area.get("nome") or "")
            break

    # 4. Copertura del suolo, poi fabbisogno, poi priorita', poi severita'.
    fire.land_cover = classify_land_cover(fire.elevation_m, fire.provincia)
    fire.water_demand_liters = water_demand_liters(
        fire.area_km2, fire.max_frp_mw, fire.land_cover
    )
    fire.base_priority = base_priority(fire)
    fire.severity = severity_from_priority(fire.base_priority)
    return fire


def enrich_fires(
    fires: Sequence[FireCluster],
    *,
    comuni: Optional[Sequence[Mapping[str, object]]] = None,
    protected_areas: Optional[Sequence[Mapping[str, object]]] = None,
    static_dir: Optional[str] = None,
) -> list[FireCluster]:
    """Arricchisce una lista di focolai leggendo i riferimenti una volta sola.

    Caricare comuni e aree protette a ogni focolaio costerebbe una lettura di
    file per focolaio nel caso peggiore (il registro ha la cache, ma non e'
    ragionevole contarci): qui si caricano una volta e si passano a valle.
    """
    ref_comuni, ref_areas = _load_reference_data(comuni, protected_areas, static_dir)
    return [
        enrich_fire(f, comuni=ref_comuni, protected_areas=ref_areas) for f in fires
    ]
