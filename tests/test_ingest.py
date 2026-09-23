"""Test del sottosistema di ingestione: FIRMS, normalizzazione, clustering.

Cosa si sta davvero verificando, modulo per modulo:

- `normalize`: che le differenze fra MODIS e VIIRS si fermino qui. E' il
  contratto del modulo, e se si rompe si rompe in silenzio, perche' un parser
  che sbaglia produce comunque numeri plausibili.
- `clustering`: che sia deterministico e che separi nel tempo. Il determinismo
  non e' pignoleria accademica: senza di esso il confronto fra il piano di
  Tagliafuoco e quello della baseline non misura i pianificatori, misura il
  rumore del clustering.
- `enrichment`: che le formule della sezione 6 di SPEC.md siano quelle e non
  altre, e che un focolaio vicino alle case risulti in interfaccia
  urbano-foresta.
- `scenario_builder`: che di notte i mezzi aerei senza capacita' notturna
  restino a terra. E' l'errore che una simulazione nasconde meglio: il piano
  notturno risulta ottimo e sarebbe impossibile da eseguire.

Nessun test tocca la rete. L'unico che la userebbe e' marcato `network` ed e'
escluso di default da `pyproject.toml`.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from tagliafuoco.config import (
    CLUSTER_EPS_KM,
    FIXTURES_DIR,
    MIN_CONFIDENCE,
    WATER_LITERS_PER_KM2,
    WUI_DECAY_KM,
    WUI_THRESHOLD_KM,
    has_firms_key,
)
from tagliafuoco.domain import registry
from tagliafuoco.domain.models import (
    BoundingBox,
    Detection,
    Domain,
    GeoPoint,
    LandCover,
    Severity,
    VehicleStatus,
)
from tagliafuoco.ingest import clustering, enrichment, firms_client, normalize
from tagliafuoco.ingest.scenario_builder import (
    build_scenario,
    build_scenarios_over_season,
    is_night_at,
    local_minute_of_day,
)


# --------------------------------------------------------------------------
# Costruttori di CSV nel formato esatto di FIRMS
# --------------------------------------------------------------------------

VIIRS_HEADER = (
    "country_id,latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,"
    "satellite,instrument,confidence,version,bright_ti5,frp,daynight"
)
MODIS_HEADER = (
    "country_id,latitude,longitude,brightness,scan,track,acq_date,acq_time,"
    "satellite,instrument,confidence,version,bright_t31,frp,daynight"
)


def viirs_csv(*rows: str) -> str:
    return "\n".join((VIIRS_HEADER, *rows)) + "\n"


def modis_csv(*rows: str) -> str:
    return "\n".join((MODIS_HEADER, *rows)) + "\n"


def viirs_row(
    lat: float,
    lon: float,
    confidence: str,
    frp: float = 12.0,
    acq_date: str = "2025-08-12",
    acq_time: str = "1148",
    satellite: str = "N",
    daynight: str = "D",
) -> str:
    return (
        f"ITA,{lat:.5f},{lon:.5f},334.2,0.41,0.38,{acq_date},{acq_time},"
        f"{satellite},VIIRS,{confidence},2.0,291.4,{frp:.1f},{daynight}"
    )


def modis_row(
    lat: float,
    lon: float,
    confidence: int,
    frp: float = 25.0,
    acq_date: str = "2025-08-12",
    acq_time: str = "1030",
    satellite: str = "Terra",
    daynight: str = "D",
) -> str:
    return (
        f"ITA,{lat:.5f},{lon:.5f},341.7,1.20,1.05,{acq_date},{acq_time},"
        f"{satellite},MODIS,{confidence},6.1,295.1,{frp:.1f},{daynight}"
    )


def detection(
    lat: float,
    lon: float,
    moment: datetime,
    frp: float = 10.0,
    satellite: str = "N",
    confidence: float = 90.0,
    scan: Optional[float] = 0.375,
    track: Optional[float] = 0.375,
) -> Detection:
    """Rilevamento sintetico con id calcolato come dal parser."""
    return Detection(
        detection_id=normalize.make_detection_id(lat, lon, moment, satellite),
        point=GeoPoint(lat=lat, lon=lon),
        acquired_at=moment,
        satellite=satellite,
        instrument="VIIRS",
        confidence=confidence,
        frp_mw=frp,
        scan_km=scan,
        track_km=track,
    )


# --------------------------------------------------------------------------
# Riferimenti territoriali finti (non dipendono da data/static/)
# --------------------------------------------------------------------------


@pytest.fixture
def comuni_finti() -> list[dict]:
    """Tre comuni inventati attorno alla Sila, con quote e popolazioni note.

    Non si usa `data/static/comuni.json` apposta: quel file e' di un altro
    sottosistema e questi test devono poter girare anche prima che esista.
    """
    return [
        {
            "nome": "Spezzano Finto",
            "provincia": "Cosenza",
            "lat": 39.2950,
            "lon": 16.5550,
            "popolazione": 8000,
            "altitudine_m": 1100.0,
        },
        {
            "nome": "Camigliati Finto",
            "provincia": "Cosenza",
            "lat": 39.3180,
            "lon": 16.5600,
            "popolazione": 1200,
            "altitudine_m": 1250.0,
        },
        {
            "nome": "Reggio Finta",
            "provincia": "Reggio Calabria",
            "lat": 38.1100,
            "lon": 15.6500,
            "popolazione": 170000,
            "altitudine_m": 30.0,
        },
    ]


@pytest.fixture
def aree_protette_finte() -> list[dict]:
    return [
        {
            "nome": "Parco Finto della Sila",
            "tipo": "parco_nazionale",
            "lat": 39.3000,
            "lon": 16.5600,
            "raggio_km": 20.0,
        }
    ]


# ==========================================================================
# normalize.py
# ==========================================================================


def test_parsing_modis_confidenza_numerica() -> None:
    """MODIS: colonna `brightness`, confidenza come numero da 0 a 100."""
    dets = normalize.parse_csv(modis_csv(modis_row(39.30, 16.56, 78, frp=25.0)))
    assert len(dets) == 1
    det = dets[0]
    assert det.instrument == "MODIS"
    assert det.satellite == "Terra"
    assert det.confidence == 78.0
    assert det.brightness_k == pytest.approx(341.7)
    assert det.frp_mw == pytest.approx(25.0)
    # Pixel MODIS: circa 1 km di lato, quindi circa 1 km quadrato di area.
    assert det.pixel_area_km2 == pytest.approx(1.26)
    assert det.acquired_at == datetime(2025, 8, 12, 10, 30, tzinfo=timezone.utc)


def test_parsing_viirs_colonna_bright_ti4() -> None:
    """VIIRS: colonna `bright_ti4`, pixel da 375 m, strumento dedotto giusto."""
    dets = normalize.parse_csv(viirs_csv(viirs_row(39.30, 16.56, "h", frp=12.0)))
    assert len(dets) == 1
    det = dets[0]
    assert det.instrument == "VIIRS"
    assert det.brightness_k == pytest.approx(334.2)
    # Il pixel VIIRS e' circa sette volte piu' piccolo di quello MODIS: e'
    # tutto il motivo per cui si usano due prodotti invece di uno.
    assert det.pixel_area_km2 < 0.2


@pytest.mark.parametrize(
    ("categoria", "atteso"),
    [("l", 30.0), ("n", 60.0), ("h", 90.0), ("low", 30.0), ("HIGH", 90.0)],
)
def test_confidenza_categorica_viirs(categoria: str, atteso: float) -> None:
    """VIIRS classifica in low, nominal, high: qui diventano 30, 60, 90."""
    dets = normalize.parse_csv(viirs_csv(viirs_row(39.30, 16.56, categoria)))
    assert len(dets) == 1
    assert dets[0].confidence == atteso


def test_confidenza_sotto_soglia_scartata() -> None:
    """Un pixel MODIS a confidenza 12 e' rumore, non un incendio."""
    assert MIN_CONFIDENCE == 30.0
    text = modis_csv(
        modis_row(39.30, 16.56, 12),
        modis_row(39.31, 16.57, 65),
    )
    dets = normalize.parse_csv(text)
    assert len(dets) == 1
    assert dets[0].confidence == 65.0


def test_rilevamento_fuori_bbox_scartato() -> None:
    """L'API per area restituisce il rettangolo pieno, mare e Sicilia compresi."""
    text = viirs_csv(
        viirs_row(39.30, 16.56, "h"),  # Sila, dentro
        viirs_row(37.50, 14.20, "h"),  # Etna, fuori
        viirs_row(41.10, 16.90, "h"),  # Murgia, fuori
    )
    dets = normalize.parse_csv(text)
    assert len(dets) == 1
    assert normalize.calabria_bounding_box().contains(dets[0].point)


def test_colonne_mancanti_non_fanno_esplodere_il_parser() -> None:
    """Il minimo indispensabile e' lat, lon e data: il resto puo' mancare."""
    minimal = "latitude,longitude,acq_date,acq_time,confidence,frp\n"
    minimal += "39.30000,16.56000,2025-08-12,1148,h,9.5\n"
    dets = normalize.parse_csv(minimal)
    assert len(dets) == 1
    det = dets[0]
    assert det.scan_km is None and det.track_km is None
    # Senza scan e track l'area del pixel ricade sul valore nominale di 1 km2.
    assert det.pixel_area_km2 == pytest.approx(1.0)
    assert det.satellite == "?"
    assert det.instrument == "UNKNOWN"
    assert det.daynight == "D"
    assert det.version == ""


def test_acq_time_senza_zero_iniziale() -> None:
    """FIRMS scrive le 00:48 come `48` e le 01:05 come `105`."""
    assert normalize.parse_acq_time("48") == (0, 48)
    assert normalize.parse_acq_time("105") == (1, 5)
    assert normalize.parse_acq_time("1148") == (11, 48)
    assert normalize.parse_acq_time("0") == (0, 0)
    assert normalize.parse_acq_time("") is None

    dets = normalize.parse_csv(
        viirs_csv(viirs_row(39.30, 16.56, "h", acq_time="48", daynight="N"))
    )
    assert dets[0].acquired_at == datetime(2025, 8, 12, 0, 48, tzinfo=timezone.utc)
    assert dets[0].acquired_at.tzinfo is not None
    assert dets[0].daynight == "N"


def test_detection_id_stabile_fra_download_sovrapposti() -> None:
    """Lo stesso pixel in due file diversi deve avere lo stesso id.

    E' la condizione che rende possibile deduplicare finestre di download
    sovrapposte senza confrontare riga per riga.
    """
    primo = normalize.parse_csv(viirs_csv(viirs_row(39.30, 16.56, "h")))
    # Stesso pixel, colonne in ordine diverso e con una colonna in meno.
    riordinato = (
        "acq_time,latitude,acq_date,longitude,confidence,satellite,frp,instrument\n"
        "1148,39.30000,2025-08-12,16.56000,h,N,12.0,VIIRS\n"
    )
    secondo = normalize.parse_csv(riordinato)
    assert primo[0].detection_id == secondo[0].detection_id

    unici = normalize.dedupe_detections(primo + secondo)
    assert len(unici) == 1


def test_detection_id_cambia_con_il_satellite() -> None:
    """Stesso punto, stesso istante, satellite diverso: sono due osservazioni."""
    moment = datetime(2025, 8, 12, 11, 48, tzinfo=timezone.utc)
    a = normalize.make_detection_id(39.30, 16.56, moment, "N")
    b = normalize.make_detection_id(39.30, 16.56, moment, "N20")
    assert a != b
    assert a == normalize.make_detection_id(39.30, 16.56, moment, "n")


def test_risposta_non_csv_non_produce_rilevamenti() -> None:
    """Chiave scaduta: FIRMS risponde in chiaro, non con un CSV."""
    assert normalize.parse_csv("Invalid MAP_KEY.") == []
    assert normalize.parse_csv("") == []


# ==========================================================================
# clustering.py
# ==========================================================================


def test_pixel_vicini_stesso_focolaio(instant: datetime) -> None:
    """Tre pixel entro mezzo chilometro nella stessa ora sono un focolaio solo."""
    dets = [
        detection(39.3000, 16.5600, instant),
        detection(39.3020, 16.5610, instant + timedelta(minutes=2)),
        detection(39.3035, 16.5595, instant + timedelta(minutes=2)),
    ]
    fires = clustering.cluster_detections(dets)
    assert len(fires) == 1
    assert fires[0].detection_count == 3
    assert fires[0].first_seen == instant
    assert fires[0].last_seen == instant + timedelta(minutes=2)


def test_pixel_lontani_focolai_distinti(instant: datetime) -> None:
    """Cinque chilometri sono molto piu' di CLUSTER_EPS_KM: due focolai."""
    assert CLUSTER_EPS_KM == 1.5
    dets = [
        detection(39.3000, 16.5600, instant),
        detection(39.3450, 16.5600, instant),  # circa 5 km piu' a nord
    ]
    fires = clustering.cluster_detections(dets)
    assert len(fires) == 2
    distanza = clustering.haversine_km(fires[0].centroid, fires[1].centroid)
    assert distanza == pytest.approx(5.0, abs=0.3)


def test_separazione_temporale_venti_ore(instant: datetime) -> None:
    """Stesso punto, venti ore dopo: il satellite ha trovato un altro incendio."""
    mattina = datetime(2025, 8, 12, 2, 0, tzinfo=timezone.utc)
    dets = [
        detection(39.3000, 16.5600, mattina),
        detection(39.3000, 16.5600, mattina + timedelta(hours=20)),
    ]
    fires = clustering.cluster_detections(dets)
    assert len(fires) == 2
    assert fires[0].fire_id != fires[1].fire_id
    assert all(f.detection_count == 1 for f in fires)

    # Con una finestra abbastanza larga tornano a essere lo stesso focolaio:
    # la separazione e' una scelta di taratura, non una proprieta' dei dati.
    uniti = clustering.cluster_detections(dets, time_window_hours=48.0)
    assert len(uniti) == 1


def test_stessa_finestra_temporale_unisce(instant: datetime) -> None:
    """Un'ora di distanza nella stessa finestra: stesso focolaio."""
    dets = [
        detection(39.3000, 16.5600, datetime(2025, 8, 12, 10, 0, tzinfo=timezone.utc)),
        detection(39.3000, 16.5600, datetime(2025, 8, 12, 11, 0, tzinfo=timezone.utc)),
    ]
    assert len(clustering.cluster_detections(dets)) == 1


def test_clustering_deterministico(instant: datetime) -> None:
    """Stesso input in ordine diverso: stessi focolai, stessi id, stesso ordine.

    Senza questa proprieta' la rigiocata della stagione non sarebbe
    confrontabile con se stessa, e il confronto con la baseline misurerebbe il
    rumore del clustering invece della qualita' dei piani.
    """
    dets = [
        detection(39.3000 + i * 0.0009, 16.5600, instant + timedelta(minutes=i), frp=5.0 + i)
        for i in range(12)
    ] + [
        detection(38.1800 + i * 0.0008, 15.9700, instant + timedelta(minutes=i), frp=20.0)
        for i in range(8)
    ]

    atteso = clustering.cluster_detections(dets)
    rng = random.Random(1234)
    for _ in range(5):
        mescolati = list(dets)
        rng.shuffle(mescolati)
        ottenuto = clustering.cluster_detections(mescolati)
        assert [f.fire_id for f in ottenuto] == [f.fire_id for f in atteso]
        assert [f.detection_count for f in ottenuto] == [
            f.detection_count for f in atteso
        ]
        assert [f.centroid.as_tuple() for f in ottenuto] == [
            f.centroid.as_tuple() for f in atteso
        ]

    # I doppioni non cambiano il risultato: la deduplica avviene dentro.
    assert [f.fire_id for f in clustering.cluster_detections(dets + dets)] == [
        f.fire_id for f in atteso
    ]


def test_centroide_pesato_sulla_frp(instant: datetime) -> None:
    """Il centroide va sulla testa del fronte, non nel mezzo geometrico.

    Due pixel: uno tiepido a sud, uno molto caldo a nord. La media semplice li
    metterebbe a meta' strada; la media pesata sulla potenza radiativa sposta
    il centroide sul pixel caldo, che e' dove il fuoco e' attivo e dove ha
    senso mandare i mezzi.
    """
    freddo = detection(39.0000, 16.5600, instant, frp=10.0)
    caldo = detection(39.0050, 16.5600, instant, frp=190.0)
    fires = clustering.cluster_detections([freddo, caldo])
    assert len(fires) == 1
    centroide = fires[0].centroid

    media_semplice = (39.0000 + 39.0050) / 2.0
    atteso = (39.0000 * 10.0 + 39.0050 * 190.0) / 200.0
    assert centroide.lat == pytest.approx(atteso, abs=1e-9)
    assert centroide.lat > media_semplice
    assert abs(centroide.lat - 39.0050) < abs(centroide.lat - 39.0000)


def test_centroide_senza_frp_ricade_sulla_media(instant: datetime) -> None:
    """FRP tutta a zero: si usa la media semplice invece di dividere per zero."""
    dets = [
        detection(39.0000, 16.5600, instant, frp=0.0),
        detection(39.0050, 16.5600, instant, frp=0.0),
    ]
    fires = clustering.cluster_detections(dets)
    assert fires[0].centroid.lat == pytest.approx(39.0025)


def test_grandezze_del_cluster(instant: datetime) -> None:
    """Somme, massimi, raggio e area del focolaio."""
    dets = [
        detection(39.0000, 16.5600, instant, frp=10.0, confidence=60.0),
        detection(39.0050, 16.5600, instant + timedelta(minutes=5), frp=30.0, confidence=90.0),
    ]
    fire = clustering.cluster_detections(dets)[0]
    assert fire.total_frp_mw == pytest.approx(40.0)
    assert fire.max_frp_mw == pytest.approx(30.0)
    assert fire.mean_confidence == pytest.approx(75.0)
    assert fire.first_seen == instant
    assert fire.last_seen == instant + timedelta(minutes=5)
    # Raggio: distanza massima dal centroide, che sta piu' vicino al pixel caldo.
    assert 0.1 < fire.radius_km < 0.6
    # Due pixel VIIRS distinti da 0.375 km di lato, in due celle diverse.
    assert fire.area_km2 == pytest.approx(2 * 0.375 * 0.375, abs=1e-6)


def test_area_dedupe_sovrapposizioni(instant: datetime) -> None:
    """Lo stesso punto visto da due satelliti non raddoppia l'area percorsa."""
    dets = [
        detection(39.0000, 16.5600, instant, frp=10.0, satellite="N"),
        detection(39.0000, 16.5600, instant + timedelta(minutes=12), frp=12.0, satellite="N20"),
    ]
    fire = clustering.cluster_detections(dets)[0]
    assert fire.detection_count == 2
    assert fire.area_km2 == pytest.approx(0.375 * 0.375, abs=1e-6)


def test_clustering_lista_vuota() -> None:
    assert clustering.cluster_detections([]) == []


# ==========================================================================
# enrichment.py
# ==========================================================================


def _fire(lat: float, lon: float, instant: datetime, frp: float = 40.0, area: float = 1.0):
    """Focolaio minimo su cui provare l'arricchimento."""
    fires = clustering.cluster_detections([detection(lat, lon, instant, frp=frp)])
    fire = fires[0]
    fire.area_km2 = area
    return fire


def test_arricchimento_focolaio_vicino_alle_case(
    instant: datetime, comuni_finti: list[dict], aree_protette_finte: list[dict]
) -> None:
    """Un focolaio a ridosso di un comune popoloso deve risultare in WUI."""
    fire = _fire(39.3000, 16.5560, instant)
    enrichment.enrich_fire(
        fire, comuni=comuni_finti, protected_areas=aree_protette_finte
    )

    assert fire.comune == "Spezzano Finto"
    assert fire.provincia == "Cosenza"
    assert fire.nearest_settlement == "Spezzano Finto"
    assert fire.distance_to_wui_km is not None and fire.distance_to_wui_km < 2.0
    assert fire.is_wui is True
    # Entrambi i comuni della Sila stanno entro tre chilometri.
    assert fire.population_within_3km == 9200
    assert fire.elevation_m == pytest.approx(1100.0)
    assert fire.in_protected_area is True
    assert fire.protected_area_name == "Parco Finto della Sila"


def test_arricchimento_focolaio_lontano_non_e_wui(
    instant: datetime, comuni_finti: list[dict]
) -> None:
    """Quindici chilometri dal comune piu' vicino: niente interfaccia."""
    fire = _fire(39.4400, 16.5600, instant)
    enrichment.enrich_fire(fire, comuni=comuni_finti, protected_areas=[])
    assert fire.distance_to_wui_km is not None and fire.distance_to_wui_km > 10.0
    assert fire.is_wui is False
    assert enrichment.wui_factor(fire.distance_to_wui_km) == 0.0
    assert fire.population_within_3km == 0
    assert fire.in_protected_area is False


def test_fattore_wui_decade_linearmente() -> None:
    """Uno fino a due chilometri, zero da dieci in poi, lineare in mezzo."""
    assert enrichment.wui_factor(0.0) == 1.0
    assert enrichment.wui_factor(2.0) == 1.0
    assert enrichment.wui_factor(6.0) == pytest.approx(0.5)
    assert enrichment.wui_factor(10.0) == 0.0
    assert enrichment.wui_factor(50.0) == 0.0
    assert enrichment.wui_factor(None) == 0.0


@pytest.mark.parametrize(
    ("quota", "provincia", "atteso"),
    [
        (1500.0, "Cosenza", LandCover.CONIFER),  # altopiano silano
        (950.0, "Crotone", LandCover.CONIFER),
        (1400.0, "Reggio Calabria", LandCover.CONIFER),  # abetina d'Aspromonte
        (1100.0, "Reggio Calabria", LandCover.MIXED_FOREST),  # sotto la soglia
        (700.0, "Cosenza", LandCover.BROADLEAF),  # faggete e castagneti
        (400.0, "Catanzaro", LandCover.MIXED_FOREST),
        (80.0, "Vibo Valentia", LandCover.SHRUBLAND),  # macchia costiera
        (None, "Cosenza", LandCover.SHRUBLAND),  # quota ignota: ramo prudente
    ],
)
def test_regola_copertura_del_suolo(
    quota: Optional[float], provincia: str, atteso: LandCover
) -> None:
    """La regola dichiarata su quota e provincia, surrogato di CORINE."""
    assert enrichment.classify_land_cover(quota, provincia) == atteso


def test_fabbisogno_acqua_cresce_con_area() -> None:
    """Formula della sezione 6 di SPEC.md, monotona nell'area."""
    assert WATER_LITERS_PER_KM2 == 100_000
    litri = [
        enrichment.water_demand_liters(a, 0.0, LandCover.SHRUBLAND)
        for a in (0.25, 0.5, 1.0, 2.0, 4.0)
    ]
    assert litri == sorted(litri)
    assert all(b > a for a, b in zip(litri, litri[1:]))
    # Raddoppiare l'area raddoppia il fabbisogno: la formula e' lineare.
    assert litri[3] == 2 * litri[2]
    # Valore esatto: 1 km2 * 100000 * 1.2 (macchia) * 1.0 (intensita' nulla).
    assert litri[2] == 120_000


def test_fabbisogno_acqua_cresce_con_intensita_e_copertura() -> None:
    """coeff_intensita = 1 + min(1, max_frp / 200); conifere bevono piu' macchia."""
    base = enrichment.water_demand_liters(1.0, 0.0, LandCover.SHRUBLAND)
    intenso = enrichment.water_demand_liters(1.0, 200.0, LandCover.SHRUBLAND)
    saturo = enrichment.water_demand_liters(1.0, 2000.0, LandCover.SHRUBLAND)
    assert intenso == 2 * base
    assert saturo == intenso  # il coefficiente satura a 2, non cresce oltre

    conifere = enrichment.water_demand_liters(1.0, 0.0, LandCover.CONIFER)
    prateria = enrichment.water_demand_liters(1.0, 0.0, LandCover.GRASSLAND)
    assert conifere == 140_000
    assert prateria == 60_000
    assert conifere > base > prateria


def test_priorita_premia_la_vicinanza_alle_case(
    instant: datetime, comuni_finti: list[dict]
) -> None:
    """A parita' di tutto il resto, il focolaio vicino alle case viene prima."""
    vicino = _fire(39.3000, 16.5560, instant, frp=40.0, area=1.0)
    lontano = _fire(39.4400, 16.5600, instant, frp=40.0, area=1.0)
    for fire in (vicino, lontano):
        enrichment.enrich_fire(fire, comuni=comuni_finti, protected_areas=[])
        # Si pareggia la copertura, cosi' resta in gioco solo il termine WUI.
        fire.land_cover = LandCover.SHRUBLAND
        fire.base_priority = enrichment.base_priority(fire)

    assert 0.0 <= lontano.base_priority <= vicino.base_priority <= 1.0
    # Il peso del termine WUI e' 0.25 e il fattore passa da 1 a 0.
    assert vicino.base_priority - lontano.base_priority == pytest.approx(0.25, abs=1e-6)


def test_priorita_premia_area_protetta(instant: datetime, comuni_finti: list[dict]) -> None:
    """Il termine `f_area_protetta` pesa 0.10 e vale 1 o 0."""
    fire = _fire(39.3000, 16.5560, instant)
    enrichment.enrich_fire(fire, comuni=comuni_finti, protected_areas=[])
    fuori = enrichment.base_priority(fire)
    fire.in_protected_area = True
    dentro = enrichment.base_priority(fire)
    assert dentro - fuori == pytest.approx(0.10, abs=1e-6)


def test_severita_dalle_soglie_dichiarate() -> None:
    assert enrichment.severity_from_priority(0.95) == Severity.EXTREME
    assert enrichment.severity_from_priority(0.70) == Severity.EXTREME
    assert enrichment.severity_from_priority(0.55) == Severity.HIGH
    assert enrichment.severity_from_priority(0.31) == Severity.MODERATE
    assert enrichment.severity_from_priority(0.05) == Severity.LOW


def test_arricchimento_senza_comuni_non_esplode(instant: datetime) -> None:
    """Territorio ignoto: il focolaio resta valido, senza contesto."""
    fire = _fire(39.3000, 16.5600, instant)
    enrichment.enrich_fire(fire, comuni=[], protected_areas=[])
    assert fire.comune is None
    assert fire.distance_to_wui_km is None
    assert fire.is_wui is False
    assert fire.land_cover == LandCover.SHRUBLAND
    assert fire.water_demand_liters > 0


@pytest.mark.skipif(
    not registry.static_data_available(),
    reason="data/static/ non ancora generato dal sottosistema dei dati statici",
)
def test_arricchimento_con_dati_statici_reali(instant: datetime) -> None:
    """Con i dati statici veri il focolaio in Sila trova un comune calabrese."""
    fire = _fire(39.3000, 16.5600, instant)
    enrichment.enrich_fire(fire)
    assert fire.comune
    assert fire.provincia in (
        "Cosenza",
        "Catanzaro",
        "Crotone",
        "Reggio Calabria",
        "Vibo Valentia",
    )
    assert fire.water_demand_liters > 0
    assert 0.0 <= fire.base_priority <= 1.0


# ==========================================================================
# scenario_builder.py
# ==========================================================================


@pytest.mark.parametrize(
    ("ora_utc", "notte"),
    [
        (datetime(2025, 8, 12, 11, 30, tzinfo=timezone.utc), False),  # 13:30 locali
        (datetime(2025, 8, 12, 23, 0, tzinfo=timezone.utc), True),  # 01:00 locali
        (datetime(2025, 8, 12, 3, 0, tzinfo=timezone.utc), True),  # 05:00 locali
        (datetime(2025, 8, 12, 3, 40, tzinfo=timezone.utc), False),  # 05:40 locali
        (datetime(2025, 8, 12, 18, 35, tzinfo=timezone.utc), True),  # 20:35 locali
    ],
)
def test_finestra_di_luce_operativa(ora_utc: datetime, notte: bool) -> None:
    """Dalle 5:30 alle 20:30 locali si vola, fuori no."""
    assert is_night_at(ora_utc) is notte


def test_scenario_notturno_mezzi_aerei_fermi(
    scenario_minimo, canadair, elicottero, modulo_aib, base_lamezia, base_cosenza, sea_source
) -> None:
    """Di notte i mezzi aerei senza capacita' notturna vanno in OUT_OF_SHIFT."""
    notte = datetime(2025, 8, 12, 23, 0, tzinfo=timezone.utc)
    assert not canadair.night_capable and not elicottero.night_capable

    scenario = build_scenario(
        scenario_minimo.fires,
        notte,
        240,
        fleet=[canadair, elicottero, modulo_aib],
        bases=[base_lamezia, base_cosenza],
        water_sources=[sea_source],
        enrich=False,
    )

    assert scenario.is_night is True
    assert scenario.vehicle_by_id("CAN_01").status == VehicleStatus.OUT_OF_SHIFT
    assert scenario.vehicle_by_id("ELI_01").status == VehicleStatus.OUT_OF_SHIFT
    # Il mezzo terrestre lavora di notte: e' anzi quando lavora meglio.
    assert scenario.vehicle_by_id("AIB_01").status == VehicleStatus.AVAILABLE
    assert all(
        v.status == VehicleStatus.OUT_OF_SHIFT
        for v in scenario.vehicles
        if v.domain == Domain.AIR
    )


def test_scenario_notturno_risparmia_i_mezzi_abilitati(
    scenario_minimo, canadair, elicottero, modulo_aib, base_lamezia, sea_source
) -> None:
    """Un mezzo aereo abilitato al volo notturno resta disponibile."""
    elicottero.night_capable = True
    scenario = build_scenario(
        scenario_minimo.fires,
        datetime(2025, 8, 12, 23, 0, tzinfo=timezone.utc),
        240,
        fleet=[canadair, elicottero, modulo_aib],
        bases=[base_lamezia],
        water_sources=[sea_source],
        enrich=False,
    )
    assert scenario.vehicle_by_id("ELI_01").status == VehicleStatus.AVAILABLE
    assert scenario.vehicle_by_id("CAN_01").status == VehicleStatus.OUT_OF_SHIFT


def test_scenario_diurno_tutti_disponibili(
    scenario_minimo, instant, canadair, elicottero, modulo_aib, base_lamezia, sea_source
) -> None:
    scenario = build_scenario(
        scenario_minimo.fires,
        instant,
        240,
        fleet=[canadair, elicottero, modulo_aib],
        bases=[base_lamezia],
        water_sources=[sea_source],
        enrich=False,
    )
    assert scenario.is_night is False
    assert all(v.status == VehicleStatus.AVAILABLE for v in scenario.vehicles)


def test_scenario_da_rilevamenti_grezzi(
    instant, comuni_finti, aree_protette_finte, canadair, base_lamezia, sea_source
) -> None:
    """Il percorso completo: rilevamenti, clustering, arricchimento, scenario."""
    dets = [
        detection(39.3000, 16.5560, instant, frp=80.0),
        detection(39.3010, 16.5570, instant, frp=60.0),
        detection(38.1150, 15.6550, instant, frp=15.0),
    ]
    scenario = build_scenario(
        dets,
        instant,
        240,
        fleet=[canadair],
        bases=[base_lamezia],
        water_sources=[sea_source],
        comuni=comuni_finti,
        protected_areas=aree_protette_finte,
    )

    assert len(scenario.fires) == 2
    assert all(f.water_demand_liters > 0 for f in scenario.fires)
    assert all(f.comune for f in scenario.fires)
    # I focolai escono ordinati per priorita' decrescente.
    priorita = [f.base_priority for f in scenario.fires]
    assert priorita == sorted(priorita, reverse=True)
    assert scenario.horizon_minutes == 240
    assert scenario.scenario_id.startswith("SC_20250812T1130_")

    # Riprodotto due volte da' lo stesso identificatore e gli stessi focolai.
    di_nuovo = build_scenario(
        list(reversed(dets)),
        instant,
        240,
        fleet=[canadair],
        bases=[base_lamezia],
        water_sources=[sea_source],
        comuni=comuni_finti,
        protected_areas=aree_protette_finte,
    )
    assert di_nuovo.scenario_id == scenario.scenario_id
    assert [f.fire_id for f in di_nuovo.fires] == [f.fire_id for f in scenario.fires]


# ==========================================================================
# firms_client.py
# ==========================================================================


def test_url_area_nella_forma_documentata() -> None:
    """base/chiave/prodotto/lon_min,lat_min,lon_max,lat_max/giorni/data."""
    url = firms_client.build_area_url(
        "VIIRS_SNPP_SP", 10, date(2025, 8, 1), map_key="CHIAVE_FINTA"
    )
    assert url.endswith(
        "/CHIAVE_FINTA/VIIRS_SNPP_SP/15.55,37.85,17.25,40.2/10/2025-08-01"
    )
    assert url.startswith("https://firms.modaps.eosdis.nasa.gov/api/area/csv/")


def test_intervallo_spezzato_in_blocchi_da_dieci_giorni() -> None:
    """L'API per area accetta al massimo dieci giorni per richiesta."""
    blocchi = firms_client._date_chunks(date(2025, 6, 1), date(2025, 6, 30))
    assert blocchi == [
        (date(2025, 6, 1), 10),
        (date(2025, 6, 11), 10),
        (date(2025, 6, 21), 10),
    ]
    assert firms_client._date_chunks(date(2025, 6, 1), date(2025, 6, 1)) == [
        (date(2025, 6, 1), 1)
    ]


def test_senza_chiave_non_si_tocca_la_rete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Senza FIRMS_MAP_KEY il client legge le fixture e basta.

    Il controllo e' brutale apposta: si sabota il download in modo che
    qualunque tentativo di rete faccia fallire il test.
    """

    def esplodi(*args: object, **kwargs: object) -> str:
        raise AssertionError("tentata una chiamata di rete senza chiave FIRMS")

    monkeypatch.setattr(firms_client, "_download", esplodi)
    monkeypatch.setattr(firms_client, "has_firms_key", lambda: False)

    dets = firms_client.fetch_detections("2025-08-01", "2025-08-10")
    assert dets
    assert all(d.acquired_at.year == 2025 for d in dets)
    assert all(d.acquired_at.month == 8 for d in dets)
    assert all(1 <= d.acquired_at.day <= 10 for d in dets)


def test_fixture_filtrate_per_prodotto() -> None:
    """Chiedere un prodotto solo legge un file solo."""
    solo_modis = firms_client.fetch_from_fixtures(
        "2025-07-01", "2025-07-31", ["MODIS_SP"]
    )
    assert solo_modis
    assert {d.instrument for d in solo_modis} == {"MODIS"}

    solo_viirs = firms_client.fetch_from_fixtures(
        "2025-07-01", "2025-07-31", ["VIIRS_SNPP_SP"]
    )
    assert {d.instrument for d in solo_viirs} == {"VIIRS"}
    assert {d.satellite for d in solo_viirs} == {"N"}


@pytest.mark.network
def test_download_reale_da_firms() -> None:
    """Scarica davvero da FIRMS. Escluso di default, serve la chiave."""
    if not has_firms_key():
        pytest.skip("FIRMS_MAP_KEY non impostata")
    oggi = date.today()
    dets = firms_client.fetch_detections(oggi - timedelta(days=3), oggi)
    assert isinstance(dets, list)
    bbox = normalize.calabria_bounding_box()
    assert all(bbox.contains(d.point) for d in dets)


# ==========================================================================
# Fixture della stagione 2025
# ==========================================================================


def test_fixture_presenti_e_dichiaratamente_sintetiche() -> None:
    """I CSV ci sono e il README dice a chiare lettere che sono generati."""
    csv_files = sorted(FIXTURES_DIR.glob("*_calabria_2025.csv"))
    assert len(csv_files) == 3
    assert (FIXTURES_DIR / "fixture_config.json").exists()
    assert (FIXTURES_DIR / "genera_fixture_2025.py").exists()

    readme = (FIXTURES_DIR / "README.md").read_text(encoding="utf-8").lower()
    assert "sintetic" in readme
    assert "non sono dati satellitari" in readme
    assert "seed" in readme


def test_fixture_hanno_la_forma_di_una_stagione() -> None:
    """Qualche migliaio di rilevamenti, con la stagionalita' giusta."""
    dets = firms_client.fetch_from_fixtures("2025-06-01", "2025-09-30")
    assert 2000 <= len(dets) <= 9000

    bbox = normalize.calabria_bounding_box()
    assert all(bbox.contains(d.point) for d in dets)
    assert all(d.confidence >= MIN_CONFIDENCE for d in dets)
    assert all(d.acquired_at.tzinfo is not None for d in dets)

    # Entrambi gli strumenti, quindi entrambi i rami del parser.
    assert {d.instrument for d in dets} == {"VIIRS", "MODIS"}

    # Picco a luglio e agosto.
    per_mese = {m: sum(1 for d in dets if d.acquired_at.month == m) for m in (6, 7, 8, 9)}
    assert per_mese[7] + per_mese[8] > 0.55 * len(dets)
    assert per_mese[8] > per_mese[6]

    # Piu' rilevamenti di giorno che di notte.
    diurni = sum(1 for d in dets if d.daynight == "D")
    assert diurni > len(dets) - diurni

    # Coda lunga della potenza radiativa: qualche evento grande fra molti piccoli.
    frps = sorted(d.frp_mw for d in dets)
    mediana = frps[len(frps) // 2]
    assert mediana < 20.0
    assert frps[-1] > 20.0 * mediana


def test_fixture_attraversano_tutta_la_catena() -> None:
    """Dalle fixture escono focolai arricchibili, non solo righe."""
    dets = firms_client.fetch_from_fixtures("2025-08-01", "2025-08-07")
    assert dets
    fires = clustering.cluster_detections(dets)
    assert fires
    assert all(f.detection_count >= 1 for f in fires)
    assert all(f.area_km2 > 0 for f in fires)
    assert all(f.first_seen <= f.last_seen for f in fires)
    # Nessun focolaio puo' durare piu' della finestra temporale di clustering.
    assert all(
        (f.last_seen - f.first_seen) <= timedelta(hours=12) for f in fires
    )
    # Gli identificatori sono unici.
    assert len({f.fire_id for f in fires}) == len(fires)


def test_generatore_di_fixture_e_riproducibile(tmp_path: Path) -> None:
    """Stesso seed, stessi file: e' la condizione per versionarli.

    Si rigenera in una cartella temporanea e si confronta con quello che sta
    nel repository. Se qualcuno tocca il generatore senza rigenerare i CSV,
    questo test lo dice.
    """
    import subprocess
    import sys

    script = FIXTURES_DIR / "genera_fixture_2025.py"
    esito = subprocess.run(
        [sys.executable, str(script), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert esito.returncode == 0, esito.stderr

    for atteso in sorted(FIXTURES_DIR.glob("*_calabria_2025.csv")):
        rigenerato = tmp_path / atteso.name
        assert rigenerato.exists(), f"il generatore non ha prodotto {atteso.name}"
        assert rigenerato.read_bytes() == atteso.read_bytes(), (
            f"{atteso.name} non corrisponde: rigenera le fixture con "
            f"python data/fixtures/genera_fixture_2025.py"
        )


# ==========================================================================
# Casi limite e regressioni
#
# Questa sezione raccoglie gli ingressi che un sottosistema di ingestione
# incontra prima o poi e che il percorso felice non esercita mai: insiemi
# vuoti, un solo elemento, valori esattamente sulle soglie, parametri fuori
# dominio, istanti senza fuso orario. Sono anche i casi in cui un difetto non
# si presenta come un'eccezione ma come un numero plausibile e sbagliato, che
# e' la ragione per cui vale la pena scriverli.
# ==========================================================================


# --------------------------------------------------------------------------
# normalize.py
# --------------------------------------------------------------------------


def test_csv_con_la_sola_intestazione() -> None:
    """Nessuna riga non e' un errore: e' una finestra in cui non bruciava niente."""
    assert normalize.parse_csv(VIIRS_HEADER + "\n") == []
    assert normalize.parse_rows([]) == []
    assert normalize.dedupe_detections([]) == []


def test_soglia_di_confidenza_esplicita_a_zero() -> None:
    """`min_confidence=0` deve tenere tutto, non ricadere sul default.

    E' il caso che distingue `if x is None` da `x or default`: con la seconda
    forma lo zero diventerebbe 30 e il filtro esplicito del chiamante verrebbe
    ignorato in silenzio.
    """
    text = modis_csv(modis_row(39.30, 16.56, 5))
    assert normalize.parse_csv(text) == []
    tenuti = normalize.parse_csv(text, min_confidence=0.0)
    assert len(tenuti) == 1
    assert tenuti[0].confidence == 5.0


def test_orario_illeggibile_scarta_la_riga_orario_assente_no() -> None:
    """Ora assente e ora corrotta sono due casi diversi.

    Una data senza ora vale mezzanotte, che e' la lettura convenzionale. Un
    orario che non si riesce a leggere invece fa scartare la riga: dargli
    mezzanotte lo sposterebbe fino a ventiquattro ore indietro, cioe' in
    un'altra finestra di clustering e in un altro scenario, e il dato
    sbagliato sarebbe indistinguibile da uno buono.
    """
    assert normalize.parse_acq_time("9999") is None
    assert normalize.parse_acquired_at("2025-08-12", "9999") is None
    assert normalize.parse_acquired_at("2025-08-12", "2400") is None

    assert normalize.parse_acquired_at("2025-08-12", None) == datetime(
        2025, 8, 12, 0, 0, tzinfo=timezone.utc
    )
    assert normalize.parse_acquired_at("2025-08-12", "") == datetime(
        2025, 8, 12, 0, 0, tzinfo=timezone.utc
    )

    scartata = normalize.parse_csv(
        viirs_csv(viirs_row(39.30, 16.56, "h", acq_time="9999"))
    )
    assert scartata == []
    tenuta = normalize.parse_csv(viirs_csv(viirs_row(39.30, 16.56, "h", acq_time="")))
    assert len(tenuta) == 1
    assert tenuta[0].acquired_at.hour == 0


def test_coordinate_fuori_dal_mondo_scartate() -> None:
    """Latitudine 95 non e' un punto: la riga e' corrotta, non fuori area."""
    text = viirs_csv(
        "ITA,95.00000,16.56000,334.2,0.41,0.38,2025-08-12,1148,N,VIIRS,h,2.0,291.4,12.0,D",
        "ITA,39.30000,999.00000,334.2,0.41,0.38,2025-08-12,1148,N,VIIRS,h,2.0,291.4,12.0,D",
        "ITA,non-un-numero,16.56000,334.2,0.41,0.38,2025-08-12,1148,N,VIIRS,h,2.0,291.4,12.0,D",
    )
    assert normalize.parse_csv(text) == []


def test_daynight_sconosciuto_ricade_su_diurno() -> None:
    """La colonna ammette solo D e N: qualunque altra cosa vale D."""
    dets = normalize.parse_csv(viirs_csv(viirs_row(39.30, 16.56, "h", daynight="X")))
    assert dets[0].daynight == "D"


def test_riconoscimento_della_risposta_non_csv() -> None:
    """Il controllo sull'intestazione sta in un posto solo ed e' usato due volte.

    Lo usa il parser per non restituire zero rilevamenti senza spiegazione, e
    lo usa il client per non scrivere in cache un messaggio di errore.
    """
    assert normalize.looks_like_firms_csv(VIIRS_HEADER) is True
    assert normalize.looks_like_firms_csv(MODIS_HEADER) is True
    assert normalize.looks_like_firms_csv("Invalid MAP_KEY.") is False
    assert normalize.looks_like_firms_csv("") is False
    assert normalize.looks_like_firms_csv("   \n  ") is False


def test_filter_by_time_tollera_istanti_senza_fuso(instant: datetime) -> None:
    """Un rilevamento naive si legge come UTC invece di far saltare il confronto.

    Confrontare un datetime con fuso e uno senza solleva `TypeError`, e il
    punto in cui salta (un ordinamento, a valle) non dice da dove arrivava il
    dato naive. La convenzione naive-uguale-UTC e' quella giusta qui perche'
    FIRMS pubblica in UTC.
    """
    senza_fuso = detection(39.30, 16.56, instant.replace(tzinfo=None))
    tenuti = normalize.filter_by_time(
        [senza_fuso], instant - timedelta(hours=1), instant + timedelta(hours=1)
    )
    assert len(tenuti) == 1

    # Anche gli estremi della finestra possono essere naive.
    tenuti = normalize.filter_by_time(
        [detection(39.30, 16.56, instant)],
        (instant - timedelta(hours=1)).replace(tzinfo=None),
        (instant + timedelta(hours=1)).replace(tzinfo=None),
    )
    assert len(tenuti) == 1


def test_filter_by_time_estremi_inclusi(instant: datetime) -> None:
    """La finestra e' chiusa da entrambi i lati: l'ultimo minuto non si perde."""
    det = detection(39.30, 16.56, instant)
    assert normalize.filter_by_time([det], instant, instant) == [det]
    assert (
        normalize.filter_by_time(
            [det], instant + timedelta(minutes=1), instant + timedelta(hours=1)
        )
        == []
    )


# --------------------------------------------------------------------------
# clustering.py
# --------------------------------------------------------------------------


def test_cluster_di_un_solo_pixel(instant: datetime) -> None:
    """Un pixel isolato e' un focolaio piccolo, non rumore da buttare.

    Con `CLUSTER_MIN_SAMPLES = 1` DBSCAN non marca niente come rumore: il
    filtro sul rumore l'ha gia' fatto la soglia di confidenza in normalize.
    """
    fires = clustering.cluster_detections([detection(39.30, 16.56, instant, frp=7.0)])
    assert len(fires) == 1
    fire = fires[0]
    assert fire.detection_count == 1
    assert fire.radius_km == 0.0
    assert fire.first_seen == fire.last_seen == instant
    assert fire.total_frp_mw == fire.max_frp_mw == pytest.approx(7.0)
    assert fire.centroid.as_tuple() == (39.30, 16.56)
    assert fire.area_km2 == pytest.approx(0.375 * 0.375, abs=1e-6)


def test_focolaio_senza_rilevamenti_e_un_errore_non_un_valore() -> None:
    """Meglio un'eccezione che un centroide finto.

    Restituire (0, 0) su un insieme vuoto metterebbe un focolaio fantasma nel
    golfo di Guinea, e il difetto si vedrebbe soltanto a valle, come un mezzo
    mandato in mezzo all'oceano.
    """
    with pytest.raises(ValueError):
        clustering.weighted_centroid([])
    with pytest.raises(ValueError):
        clustering.build_cluster([])


@pytest.mark.parametrize(
    "parametri",
    [
        {"eps_km": 0.0},
        {"eps_km": -1.5},
        {"time_window_hours": 0.0},
        {"time_window_hours": -3.0},
        {"min_samples": 0},
    ],
)
def test_parametri_di_clustering_fuori_dominio(
    instant: datetime, parametri: dict
) -> None:
    """I parametri si controllano all'ingresso, non dentro scikit-learn.

    Il messaggio dice anche l'unita' di misura attesa (chilometri, ore): e' la
    difesa piu' economica contro lo scambio fra chilometri e metri o fra ore e
    minuti, che non fa fallire niente e sposta il risultato di tre ordini di
    grandezza.
    """
    with pytest.raises(ValueError):
        clustering.cluster_detections([detection(39.30, 16.56, instant)], **parametri)


def test_clustering_tollera_istanti_senza_fuso(instant: datetime) -> None:
    """Rilevamenti con e senza fuso nello stesso lotto non fanno saltare il sort."""
    dets = [
        detection(39.3000, 16.5600, instant),
        detection(
            39.3010, 16.5605, instant.replace(tzinfo=None) + timedelta(minutes=3)
        ),
    ]
    fires = clustering.cluster_detections(dets)
    assert len(fires) == 1
    # Gli estremi del focolaio escono sempre con il fuso esplicito.
    assert fires[0].first_seen.tzinfo is not None
    assert fires[0].last_seen.tzinfo is not None
    assert fires[0].first_seen == instant


def test_confine_di_finestra_temporale_e_un_artefatto_dichiarato() -> None:
    """Le finestre sono ancorate all'epoca: due pixel vicini nel tempo si separano.

    Con finestre da 12 ore ancorate all'epoca i confini cadono a mezzanotte e
    a mezzogiorno UTC. Due pixel a due minuti di distanza ma a cavallo di
    mezzogiorno finiscono in due focolai: e' il prezzo dichiarato della
    riproducibilita', non un difetto, e questo test esiste per fissarlo e per
    accorgersi se qualcuno cambia l'ancoraggio.
    """
    prima = datetime(2025, 8, 12, 11, 59, tzinfo=timezone.utc)
    dopo = datetime(2025, 8, 12, 12, 1, tzinfo=timezone.utc)
    assert clustering.time_bucket(prima, 12.0) != clustering.time_bucket(dopo, 12.0)

    separati = clustering.cluster_detections(
        [detection(39.30, 16.56, prima), detection(39.30, 16.56, dopo)]
    )
    assert len(separati) == 2

    # Due pixel altrettanto distanti nel tempo ma dalla stessa parte del
    # confine restano invece lo stesso focolaio.
    insieme = clustering.cluster_detections(
        [
            detection(39.30, 16.56, dopo),
            detection(39.30, 16.56, dopo + timedelta(minutes=2)),
        ]
    )
    assert len(insieme) == 1


def test_area_usa_la_griglia_del_pixel_piu_grosso(instant: datetime) -> None:
    """MODIS e VIIRS sullo stesso punto: si conta il pixel MODIS, una volta sola.

    Un pixel MODIS da 1 km copre circa sette pixel VIIRS. Con una griglia
    fine lo stesso fronte visto dai due strumenti verrebbe contato due volte e
    l'area percorsa risulterebbe quasi doppia.
    """
    fires = clustering.cluster_detections(
        [
            detection(39.00, 16.56, instant, satellite="N", scan=0.375, track=0.375),
            detection(39.00, 16.56, instant, satellite="Terra", scan=1.2, track=1.05),
        ]
    )
    assert len(fires) == 1
    assert fires[0].detection_count == 2
    assert fires[0].area_km2 == pytest.approx(1.2 * 1.05, abs=1e-6)


def test_cluster_senza_scan_e_track_usa_l_area_nominale(instant: datetime) -> None:
    """Senza le colonne del pixel l'area ricade sul km quadrato nominale."""
    fire = clustering.cluster_detections(
        [detection(39.00, 16.56, instant, scan=None, track=None)]
    )[0]
    assert fire.area_km2 == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------
# enrichment.py
# --------------------------------------------------------------------------


def test_arricchimento_idempotente(
    instant: datetime, comuni_finti: list[dict], aree_protette_finte: list[dict]
) -> None:
    """Arricchire due volte con gli stessi riferimenti da' lo stesso risultato.

    Serve perche' `build_scenario` puo' riarricchire focolai gia' arricchiti:
    se l'arricchimento lasciasse in giro i valori della passata precedente, il
    risultato dipenderebbe dalla storia dell'oggetto invece che dai dati.
    """
    fire = _fire(39.3000, 16.5560, instant)
    campi = (
        "comune",
        "provincia",
        "nearest_settlement",
        "distance_to_wui_km",
        "elevation_m",
        "population_within_3km",
        "in_protected_area",
        "protected_area_name",
        "land_cover",
        "water_demand_liters",
        "base_priority",
        "severity",
    )

    enrichment.enrich_fire(
        fire, comuni=comuni_finti, protected_areas=aree_protette_finte
    )
    prima = {c: getattr(fire, c) for c in campi}
    enrichment.enrich_fire(
        fire, comuni=comuni_finti, protected_areas=aree_protette_finte
    )
    assert {c: getattr(fire, c) for c in campi} == prima


def test_contesto_azzerato_quando_i_riferimenti_spariscono(
    instant: datetime, comuni_finti: list[dict]
) -> None:
    """Senza comuni il contesto si azzera, non resta quello di prima."""
    fire = _fire(39.3000, 16.5560, instant)
    enrichment.enrich_fire(fire, comuni=comuni_finti, protected_areas=[])
    assert fire.comune == "Spezzano Finto"

    enrichment.enrich_fire(fire, comuni=[], protected_areas=[])
    assert fire.comune is None
    assert fire.provincia is None
    assert fire.nearest_settlement is None
    assert fire.distance_to_wui_km is None
    assert fire.elevation_m is None
    assert fire.population_within_3km == 0
    assert fire.is_wui is False


def test_comune_malformato_non_ferma_l_arricchimento(
    instant: datetime, comuni_finti: list[dict]
) -> None:
    """Una riga sbagliata nei dati statici non deve far cadere la stagione."""
    rotti = [
        {"nome": "Senza Coordinate", "provincia": "Cosenza", "popolazione": 100},
        {"nome": "Lat Non Numerica", "lat": "boh", "lon": 16.5, "popolazione": 100},
        *comuni_finti,
    ]
    fire = _fire(39.3000, 16.5560, instant)
    enrichment.enrich_fire(fire, comuni=rotti, protected_areas=[])
    assert fire.comune == "Spezzano Finto"

    # Se TUTTI i riferimenti sono malformati si finisce senza contesto, non
    # con un'eccezione.
    altro = _fire(39.3000, 16.5560, instant)
    enrichment.enrich_fire(altro, comuni=rotti[:2], protected_areas=[])
    assert altro.comune is None
    assert altro.water_demand_liters > 0


def test_area_protetta_malformata_ignorata(
    instant: datetime, comuni_finti: list[dict]
) -> None:
    """Un'area senza coordinate o senza raggio non puo' contenere niente."""
    fire = _fire(39.3000, 16.5600, instant)
    enrichment.enrich_fire(
        fire,
        comuni=comuni_finti,
        protected_areas=[
            {"nome": "Senza Raggio", "lat": 39.30, "lon": 16.56},
            {"nome": "Senza Punto", "raggio_km": 500.0},
        ],
    )
    assert fire.in_protected_area is False
    assert fire.protected_area_name is None


def test_fabbisogno_acqua_agli_estremi() -> None:
    """Area nulla, area negativa e intensita' negativa non devono dare assurdi."""
    assert enrichment.water_demand_liters(0.0, 50.0, LandCover.CONIFER) == 0
    assert enrichment.water_demand_liters(-3.0, 50.0, LandCover.CONIFER) == 0
    # Una FRP negativa non esiste, ma se arrivasse non deve portare il
    # fabbisogno sotto il valore a intensita' nulla.
    assert enrichment.water_demand_liters(
        1.0, -10.0, LandCover.SHRUBLAND
    ) == enrichment.water_demand_liters(1.0, 0.0, LandCover.SHRUBLAND)


def test_priorita_resta_fra_zero_e_uno(instant: datetime) -> None:
    """La formula e' una combinazione convessa: non puo' uscire da [0, 1]."""
    minimo = _fire(39.30, 16.56, instant, frp=0.0, area=0.0)
    minimo.total_frp_mw = 0.0
    minimo.distance_to_wui_km = None
    minimo.land_cover = LandCover.BARREN
    minimo.in_protected_area = False
    p_min = enrichment.base_priority(minimo)

    massimo = _fire(39.30, 16.56, instant, frp=0.0, area=0.0)
    massimo.total_frp_mw = 5_000.0
    massimo.area_km2 = 500.0
    massimo.distance_to_wui_km = 0.0
    massimo.land_cover = LandCover.CONIFER
    massimo.in_protected_area = True
    p_max = enrichment.base_priority(massimo)

    assert 0.0 <= p_min < p_max <= 1.0
    # Il massimo e' esattamente 1: i pesi sommano a uno e i termini saturano.
    assert p_max == pytest.approx(1.0)
    # Il minimo non e' zero perche' anche il suolo nudo ha un coefficiente.
    assert p_min == pytest.approx(0.10 * (0.3 / 1.4), abs=1e-4)


def test_wui_factor_appena_oltre_la_soglia() -> None:
    """Il fattore e' continuo sulle soglie: niente salto da 1 a 0."""
    assert enrichment.wui_factor(WUI_THRESHOLD_KM) == 1.0
    appena_oltre = enrichment.wui_factor(WUI_THRESHOLD_KM + 0.001)
    assert 0.999 < appena_oltre < 1.0
    appena_prima = enrichment.wui_factor(WUI_DECAY_KM - 0.001)
    assert 0.0 < appena_prima < 0.001


@pytest.mark.parametrize(
    ("priorita", "atteso"),
    [
        (1.0, Severity.EXTREME),
        (0.70, Severity.EXTREME),
        (0.6999, Severity.HIGH),
        (0.50, Severity.HIGH),
        (0.4999, Severity.MODERATE),
        (0.30, Severity.MODERATE),
        (0.2999, Severity.LOW),
        (0.0, Severity.LOW),
    ],
)
def test_severita_esattamente_sulle_soglie(priorita: float, atteso: Severity) -> None:
    """Le soglie sono inclusive verso l'alto: 0.70 esatto e' gia' estrema."""
    assert enrichment.severity_from_priority(priorita) == atteso


def test_arricchimento_di_una_lista_vuota() -> None:
    assert enrichment.enrich_fires([], comuni=[], protected_areas=[]) == []


# --------------------------------------------------------------------------
# scenario_builder.py
# --------------------------------------------------------------------------


def test_lo_scenario_non_muta_la_flotta_del_chiamante(
    canadair, base_lamezia, sea_source
) -> None:
    """Costruire uno scenario notturno non deve toccare i mezzi del chiamante.

    `apply_night_shift` cambia lo stato sul posto e non lo rimette a posto
    dopo. Se lo scenario non copiasse la flotta, il mezzo del chiamante
    resterebbe a terra per sempre.
    """
    notte = datetime(2025, 8, 12, 23, 0, tzinfo=timezone.utc)
    scenario = build_scenario(
        [], notte, 240, fleet=[canadair], bases=[base_lamezia], water_sources=[sea_source]
    )

    assert scenario.vehicle_by_id("CAN_01").status == VehicleStatus.OUT_OF_SHIFT
    assert canadair.status == VehicleStatus.AVAILABLE
    assert scenario.vehicles[0] is not canadair


def test_scenario_notturno_non_contamina_quello_diurno(
    instant: datetime, canadair, elicottero, modulo_aib, base_lamezia, sea_source
) -> None:
    """Regressione: dopo una finestra notturna, quella diurna torna a volare.

    E' il difetto piu' insidioso di tutto il sottosistema, perche' non fa
    fallire niente: il piano diurno resterebbe valido, solo senza mezzi
    aerei, e il confronto con la baseline misurerebbe una flotta che non
    esiste. Nella rigiocata della stagione le finestre notturne e diurne si
    alternano, quindi il difetto si propagherebbe a tutto l'esperimento 1.
    """
    flotta = [canadair, elicottero, modulo_aib]
    notte = build_scenario(
        [],
        datetime(2025, 8, 12, 23, 0, tzinfo=timezone.utc),
        240,
        fleet=flotta,
        bases=[base_lamezia],
        water_sources=[sea_source],
    )
    giorno = build_scenario(
        [], instant, 240, fleet=flotta, bases=[base_lamezia], water_sources=[sea_source]
    )

    assert notte.is_night is True
    assert giorno.is_night is False
    assert all(
        v.status == VehicleStatus.OUT_OF_SHIFT
        for v in notte.vehicles
        if v.domain == Domain.AIR
    )
    assert all(v.status == VehicleStatus.AVAILABLE for v in giorno.vehicles)


def test_turno_notturno_non_sovrascrive_gli_altri_stati(
    canadair, elicottero, base_lamezia, sea_source
) -> None:
    """Un mezzo gia' in manutenzione resta in manutenzione, non va OUT_OF_SHIFT.

    Sostituire lo stato perderebbe l'informazione sul perche' il mezzo non e'
    disponibile, che al mattino dopo e' esattamente quello che serve sapere.
    """
    canadair.status = VehicleStatus.MAINTENANCE
    elicottero.status = VehicleStatus.RESERVE
    scenario = build_scenario(
        [],
        datetime(2025, 8, 12, 23, 0, tzinfo=timezone.utc),
        240,
        fleet=[canadair, elicottero],
        bases=[base_lamezia],
        water_sources=[sea_source],
    )
    assert scenario.vehicle_by_id("CAN_01").status == VehicleStatus.MAINTENANCE
    assert scenario.vehicle_by_id("ELI_01").status == VehicleStatus.RESERVE


def test_scenario_senza_focolai(
    instant: datetime, canadair, base_lamezia, sea_source
) -> None:
    """Una finestra senza incendi e' normale, non un errore.

    Lo scenario esce valido e con un id stabile: la rigiocata della stagione
    deve poterlo attraversare senza casi speciali.
    """
    scenario = build_scenario(
        [],
        instant,
        240,
        fleet=[canadair],
        bases=[base_lamezia],
        water_sources=[sea_source],
    )
    assert scenario.fires == []
    assert scenario.vehicles
    assert scenario.horizon_minutes == 240
    assert scenario.scenario_id.startswith("SC_20250812T1130_")
    di_nuovo = build_scenario(
        [],
        instant,
        240,
        fleet=[canadair],
        bases=[base_lamezia],
        water_sources=[sea_source],
    )
    assert di_nuovo.scenario_id == scenario.scenario_id


def test_scenario_senza_mezzi(instant: datetime, scenario_minimo) -> None:
    """Flotta vuota: lo scenario si costruisce lo stesso e il piano sara' vuoto.

    Serve all'ablazione, che confronta la pipeline con risorse ridotte fino al
    caso degenere. Una flotta vuota non e' la stessa cosa di una flotta non
    specificata, che invece si legge dal registro dei dati statici.
    """
    scenario = build_scenario(
        scenario_minimo.fires,
        instant,
        240,
        fleet=[],
        bases=[],
        water_sources=[],
        enrich=False,
    )
    assert scenario.vehicles == []
    assert scenario.bases == []
    assert scenario.water_sources == []
    assert len(scenario.fires) == 2


def test_stagione_finestre_e_confini(canadair, base_lamezia) -> None:
    """Le finestre sono semiaperte a destra e l'ultima contiene l'ultimo dato.

    Un rilevamento esattamente sul confine appartiene alla finestra che
    comincia li', non a quella che finisce li': senza questa regola lo stesso
    pixel entrerebbe in due scenari consecutivi. E l'ultimo blocco va incluso,
    altrimenti le ultime ore della stagione sparirebbero in silenzio.
    """
    dets = [
        detection(39.3000, 16.5600, datetime(2025, 8, 12, 10, 0, tzinfo=timezone.utc)),
        detection(39.3010, 16.5610, datetime(2025, 8, 12, 12, 0, tzinfo=timezone.utc)),
    ]
    scenari = build_scenarios_over_season(
        dets,
        window_hours=3.0,
        fleet=[canadair],
        bases=[base_lamezia],
        water_sources=[],
        enrich=False,
    )
    assert [s.timestamp for s in scenari] == [
        datetime(2025, 8, 12, 12, 0, tzinfo=timezone.utc),
        datetime(2025, 8, 12, 15, 0, tzinfo=timezone.utc),
    ]

    # Il rilevamento delle 12:00 non sta nella finestra che finisce alle 12:00.
    assert sum(f.detection_count for f in scenari[0].fires) == 1
    # Sta in quella successiva, insieme a quello delle 10:00 (lookback 6 ore).
    assert sum(f.detection_count for f in scenari[1].fires) == 2


def test_stagione_flotta_non_contaminata_fra_finestre(canadair, base_lamezia) -> None:
    """Regressione sulla rigiocata: la finestra diurna dopo una notturna vola.

    E' lo stesso difetto del test sugli scenari singoli, ma visto dove fa
    davvero danno: `build_scenarios_over_season` passa la stessa flotta a
    tutte le finestre della stagione.
    """
    dets = [
        detection(39.3000, 16.5600, datetime(2025, 8, 12, 22, 0, tzinfo=timezone.utc)),
        detection(39.3010, 16.5610, datetime(2025, 8, 13, 10, 0, tzinfo=timezone.utc)),
    ]
    scenari = build_scenarios_over_season(
        dets,
        window_hours=3.0,
        fleet=[canadair],
        bases=[base_lamezia],
        water_sources=[],
        enrich=False,
    )
    notturni = [s for s in scenari if s.is_night]
    diurni = [s for s in scenari if not s.is_night]
    assert notturni and diurni

    assert all(
        s.vehicle_by_id("CAN_01").status == VehicleStatus.OUT_OF_SHIFT for s in notturni
    )
    assert all(
        s.vehicle_by_id("CAN_01").status == VehicleStatus.AVAILABLE for s in diurni
    )
    assert canadair.status == VehicleStatus.AVAILABLE


def test_stagione_senza_rilevamenti_e_parametri_non_validi(instant: datetime) -> None:
    """Nessun dato non e' un errore; una finestra nulla o negativa si'."""
    assert build_scenarios_over_season([], 3.0) == []
    with pytest.raises(ValueError):
        build_scenarios_over_season([detection(39.3, 16.56, instant)], 0.0)
    with pytest.raises(ValueError):
        build_scenarios_over_season(
            [detection(39.3, 16.56, instant)], 3.0, lookback_hours=-1.0
        )


def test_minuto_locale_e_confine_di_mezzanotte() -> None:
    """Lo scarto di due ore sull'UTC non deve sforare le ventiquattro ore."""
    # 23:00 UTC sono l'01:00 locale del giorno dopo, cioe' 60 minuti.
    assert local_minute_of_day(datetime(2025, 8, 12, 23, 0, tzinfo=timezone.utc)) == 60
    assert local_minute_of_day(datetime(2025, 8, 12, 22, 0, tzinfo=timezone.utc)) == 0
    # Un istante naive si legge come gia' locale.
    assert local_minute_of_day(datetime(2025, 8, 12, 14, 30)) == 14 * 60 + 30


# --------------------------------------------------------------------------
# firms_client.py
# --------------------------------------------------------------------------


def test_blocchi_di_giorni_con_estremi_invertiti_e_non_validi() -> None:
    """Estremi invertiti si raddrizzano; un blocco di zero giorni e' un errore.

    Con `max_days = 0` il cursore non avanzerebbe mai: sarebbe un blocco, non
    un'eccezione, cioe' il genere di difetto che si presenta come un test che
    non torna piu' indietro.
    """
    assert firms_client._date_chunks(
        date(2025, 6, 30), date(2025, 6, 1)
    ) == firms_client._date_chunks(date(2025, 6, 1), date(2025, 6, 30))
    with pytest.raises(ValueError):
        firms_client._date_chunks(date(2025, 6, 1), date(2025, 6, 30), 0)


def test_fixture_con_estremi_invertiti() -> None:
    """Le due porte d'ingresso rispondono allo stesso modo allo stesso errore."""
    dritto = firms_client.fetch_from_fixtures("2025-08-01", "2025-08-07")
    rovescio = firms_client.fetch_from_fixtures("2025-08-07", "2025-08-01")
    assert dritto
    assert [d.detection_id for d in dritto] == [d.detection_id for d in rovescio]


def test_fixture_finestra_vuota() -> None:
    """Un giorno fuori stagione non ha rilevamenti: lista vuota, non errore."""
    assert firms_client.fetch_from_fixtures("2025-01-15", "2025-01-15") == []


def test_risposta_non_csv_non_finisce_in_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIRMS segnala la chiave scaduta con un 200 e un messaggio in chiaro.

    Salvarlo in cache lo trasformerebbe in un guasto permanente: da li' in poi
    ogni esecuzione rileggerebbe il messaggio dal disco senza mai riprovare la
    rete, anche dopo aver rimesso a posto la chiave.
    """
    monkeypatch.setattr(
        firms_client, "_download", lambda url, redacted: "Invalid MAP_KEY."
    )
    testo = firms_client.fetch_csv(
        "VIIRS_SNPP_SP", date(2025, 8, 1), 1, cache_dir=tmp_path, map_key="FINTA"
    )
    assert testo == "Invalid MAP_KEY."
    assert list(tmp_path.glob("*.csv")) == []
    assert normalize.parse_csv(testo) == []


def test_risposta_csv_valida_va_in_cache_e_la_cache_evita_la_rete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il CSV buono si salva, e alla seconda chiamata la rete non si tocca."""
    contenuto = viirs_csv(viirs_row(39.30, 16.56, "h"))
    monkeypatch.setattr(firms_client, "_download", lambda url, redacted: contenuto)
    primo = firms_client.fetch_csv(
        "VIIRS_SNPP_SP", date(2025, 8, 1), 1, cache_dir=tmp_path, map_key="FINTA"
    )
    assert primo == contenuto
    assert len(list(tmp_path.glob("*.csv"))) == 1

    def esplodi(*args: object, **kwargs: object) -> str:
        raise AssertionError("rete usata pur avendo la cache")

    monkeypatch.setattr(firms_client, "_download", esplodi)
    secondo = firms_client.fetch_csv(
        "VIIRS_SNPP_SP", date(2025, 8, 1), 1, cache_dir=tmp_path, map_key="FINTA"
    )
    assert secondo == contenuto
    assert len(normalize.parse_csv(secondo)) == 1


def test_cache_separata_per_area_di_interesse(tmp_path: Path) -> None:
    """Cambiare bounding box non deve leggere il file dell'area precedente."""
    calabria = firms_client._cache_path(
        "VIIRS_SNPP_SP", date(2025, 8, 1), 10, None, tmp_path
    )
    altra = firms_client._cache_path(
        "VIIRS_SNPP_SP",
        date(2025, 8, 1),
        10,
        BoundingBox(min_lat=44.0, min_lon=7.0, max_lat=46.0, max_lon=9.0),
        tmp_path,
    )
    assert calabria != altra
