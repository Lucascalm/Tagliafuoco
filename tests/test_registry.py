"""Test dei dati statici e del loro caricamento.

PERCHE' ESISTE
I cinque JSON sotto `data/static/` sono l'unico pezzo del sistema che nessun
algoritmo controlla. Un mezzo con `home_base_id` sbagliato non fa eccezione:
fa un piano in cui un elicottero parte da una base che non esiste, e il difetto
si vede solo a valle, dentro una metrica che sembra strana. Questi test sono la
rete che si mette sotto i dati, perche' i dati sbagliati non falliscono da soli.

I controlli sono di cinque famiglie:
  1. integrita' referenziale: ogni riferimento fra file punta a qualcosa;
  2. plausibilita' fisica: un Canadair non sta in volo otto ore, un pick-up
     non viaggia a 200 km/h, un Canadair non pesca da una vasca da 90 metri
     cubi. Sono i controlli che un revisore umano farebbe a occhio;
  3. copertura minima: la flotta e il territorio ci sono davvero, con il mix
     e i numeri che il progetto dichiara;
  4. il validatore del generatore messo alla prova: si inietta un difetto alla
     volta nei dati e si verifica che venga riconosciuto. Una rete di sicurezza
     che nessuno prova non e' una rete di sicurezza, e qui il validatore e'
     l'unica cosa che separa un refuso nelle costanti da cinque JSON incoerenti
     sul disco;
  5. casi limite: cinque elenchi vuoti, un solo record per famiglia, valori
     esattamente sul confine degli intervalli ammessi, file statici assenti e
     file statici vuoti.

Nota di metodo: i riquadri provinciali qui sotto sono RISCRITTI, non importati
dal generatore. E' voluto, ed e' lo stesso principio per cui il validatore del
piano non importa niente da `optimize/`: un test che chiede al codice sotto
esame quale sia la risposta giusta non verifica niente.
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

from tagliafuoco.config import (
    CALABRIA_BBOX,
    EARTH_RADIUS_KM,
    PROVINCE,
    ROAD_TORTUOSITY,
    STATIC_DIR,
)
from tagliafuoco.domain.models import (
    Base,
    BoundingBox,
    Domain,
    GeoPoint,
    Vehicle,
    VehicleKind,
    VehicleStatus,
    WaterSource,
    WaterSourceKind,
)
from tagliafuoco.domain.registry import (
    StaticDataError,
    clear_cache,
    load_bases,
    load_comuni,
    load_fleet,
    load_protected_areas,
    load_water_sources,
    static_data_available,
)

FILE_STATICI = (
    "basi.json",
    "mezzi.json",
    "sorgenti_acqua.json",
    "comuni.json",
    "aree_protette.json",
)

# CALABRIA_BBOX e' nell'ordine di FIRMS (lon_min, lat_min, lon_max, lat_max),
# mentre BoundingBox vuole le latitudini per prime: la conversione va fatta una
# volta sola e qui, altrimenti prima o poi qualcuno la sbaglia.
_LON_MIN, _LAT_MIN, _LON_MAX, _LAT_MAX = CALABRIA_BBOX
CALABRIA = BoundingBox(
    min_lat=_LAT_MIN, min_lon=_LON_MIN, max_lat=_LAT_MAX, max_lon=_LON_MAX
)

# Riquadri approssimati per provincia: (lat_min, lat_max, lon_min, lon_max).
# Non sono confini amministrativi. Servono a intercettare l'errore di battitura
# che sposta un comune di un grado, cioe' di un centinaio di chilometri, e per
# questo sono volutamente generosi e sovrapposti fra loro.
RIQUADRI_PROVINCIA: dict[str, tuple[float, float, float, float]] = {
    "Cosenza": (39.05, 40.20, 15.60, 17.05),
    "Catanzaro": (38.45, 39.12, 15.95, 16.95),
    "Crotone": (38.90, 39.45, 16.55, 17.25),
    "Vibo Valentia": (38.45, 38.82, 15.82, 16.45),
    "Reggio Calabria": (37.88, 38.56, 15.58, 16.62),
}

AEREI = {
    VehicleKind.CANADAIR,
    VehicleKind.ELICOTTERO_PESANTE,
    VehicleKind.ELICOTTERO_MEDIO,
    VehicleKind.ELICOTTERO_LEGGERO,
}


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def registro_pulito() -> None:
    """Svuota le cache del registro.

    `registry` memorizza i file letti con `lru_cache`. Se un altro modulo di
    test ha caricato dati finti da una cartella temporanea, senza questa
    pulizia leggeremmo i suoi.
    """
    clear_cache()


@pytest.fixture(scope="module")
def bases() -> list[Base]:
    return load_bases()


@pytest.fixture(scope="module")
def fleet() -> list[Vehicle]:
    return load_fleet()


@pytest.fixture(scope="module")
def water() -> list[WaterSource]:
    return load_water_sources()


@pytest.fixture(scope="module")
def comuni() -> tuple[dict[str, Any], ...]:
    return load_comuni()


@pytest.fixture(scope="module")
def aree() -> tuple[dict[str, Any], ...]:
    return load_protected_areas()


# --------------------------------------------------------------------------
# 1. I cinque file esistono e si caricano
# --------------------------------------------------------------------------


def test_tutti_i_file_statici_sono_presenti() -> None:
    assert static_data_available(), (
        "mancano dei file in data/static: rigenerali con "
        ".venv/Scripts/python.exe scripts/genera_dati_statici.py"
    )


def test_i_cinque_caricamenti_producono_le_entita_attese(
    bases: list[Base],
    fleet: list[Vehicle],
    water: list[WaterSource],
    comuni: tuple[dict[str, Any], ...],
    aree: tuple[dict[str, Any], ...],
) -> None:
    assert all(isinstance(b, Base) for b in bases)
    assert all(isinstance(v, Vehicle) for v in fleet)
    assert all(isinstance(w, WaterSource) for w in water)
    assert all(isinstance(c, dict) for c in comuni)
    assert all(isinstance(a, dict) for a in aree)


def test_le_quantita_stanno_negli_intervalli_dichiarati(
    bases: list[Base],
    fleet: list[Vehicle],
    water: list[WaterSource],
    comuni: tuple[dict[str, Any], ...],
    aree: tuple[dict[str, Any], ...],
) -> None:
    assert 10 <= len(bases) <= 14
    assert 18 <= len(fleet) <= 26
    assert 12 <= len(water) <= 18
    assert len(comuni) >= 120
    assert len(aree) >= 5


# --------------------------------------------------------------------------
# 2. Nessun identificativo duplicato
# --------------------------------------------------------------------------
# Un id duplicato non rompe il caricamento: rompe il solver, che indicizza le
# variabili per id e si ritrova due mezzi diversi sulla stessa variabile.


def _duplicati(valori: list[str]) -> list[str]:
    visti: set[str] = set()
    doppi: list[str] = []
    for v in valori:
        if v in visti and v not in doppi:
            doppi.append(v)
        visti.add(v)
    return doppi


def test_identificativi_unici(
    bases: list[Base],
    fleet: list[Vehicle],
    water: list[WaterSource],
    comuni: tuple[dict[str, Any], ...],
    aree: tuple[dict[str, Any], ...],
) -> None:
    assert _duplicati([b.base_id for b in bases]) == []
    assert _duplicati([v.vehicle_id for v in fleet]) == []
    assert _duplicati([w.source_id for w in water]) == []
    assert _duplicati([f"{c['nome']}|{c['provincia']}" for c in comuni]) == []
    assert _duplicati([a["nome"] for a in aree]) == []


def test_anche_i_nomi_leggibili_dei_mezzi_sono_unici(fleet: list[Vehicle]) -> None:
    """Il nome finisce nei report e nella mappa: due mezzi omonimi confondono."""
    assert _duplicati([v.name for v in fleet]) == []


# --------------------------------------------------------------------------
# 3. Integrita' referenziale: i mezzi e le basi
# --------------------------------------------------------------------------


def test_ogni_mezzo_punta_a_una_base_esistente(
    fleet: list[Vehicle], bases: list[Base]
) -> None:
    noti = {b.base_id for b in bases}
    orfani = [v.vehicle_id for v in fleet if v.home_base_id not in noti]
    assert orfani == [], f"mezzi con base inesistente: {orfani}"


def test_la_base_di_casa_supporta_il_tipo_del_mezzo(
    fleet: list[Vehicle], bases: list[Base]
) -> None:
    """Un Canadair non puo' avere casa su un'elisuperficie di montagna."""
    per_id = {b.base_id: b for b in bases}
    sbagliati = [
        (v.vehicle_id, v.kind.value, v.home_base_id)
        for v in fleet
        if v.kind not in per_id[v.home_base_id].supports
    ]
    assert sbagliati == [], f"mezzi su una base che non li supporta: {sbagliati}"


def test_ogni_base_dichiara_almeno_un_tipo_supportato(bases: list[Base]) -> None:
    vuote = [b.base_id for b in bases if not b.supports]
    assert vuote == []


def test_ogni_provincia_ha_almeno_una_base(bases: list[Base]) -> None:
    """Serve al confronto con la baseline: senza basi in una provincia, la
    regola del mezzo libero piu' vicino sarebbe penalizzata per costruzione."""
    coperte = {b.provincia for b in bases}
    assert set(PROVINCE) <= coperte, f"province senza base: {set(PROVINCE) - coperte}"


def test_nel_registro_nessun_mezzo_nasce_gia_impegnato(fleet: list[Vehicle]) -> None:
    """Lo stato dinamico appartiene allo scenario, non ai dati statici."""
    assert all(v.status is VehicleStatus.AVAILABLE for v in fleet)


# --------------------------------------------------------------------------
# 4. Coordinate dentro la bounding box della Calabria
# --------------------------------------------------------------------------


def test_le_basi_cadono_nella_bounding_box(bases: list[Base]) -> None:
    fuori = [b.base_id for b in bases if not CALABRIA.contains(b.point)]
    assert fuori == [], f"basi fuori dalla Calabria: {fuori}"


def test_le_sorgenti_cadono_nella_bounding_box(water: list[WaterSource]) -> None:
    """Vale anche per i tratti di mare: sono al largo, ma dentro il riquadro."""
    fuori = [w.source_id for w in water if not CALABRIA.contains(w.point)]
    assert fuori == [], f"sorgenti fuori dalla Calabria: {fuori}"


def test_i_comuni_cadono_nella_bounding_box(
    comuni: tuple[dict[str, Any], ...],
) -> None:
    fuori = [
        c["nome"]
        for c in comuni
        if not CALABRIA.contains(GeoPoint(lat=c["lat"], lon=c["lon"]))
    ]
    assert fuori == [], f"comuni fuori dalla Calabria: {fuori}"


def test_le_aree_protette_cadono_nella_bounding_box(
    aree: tuple[dict[str, Any], ...],
) -> None:
    fuori = [
        a["nome"]
        for a in aree
        if not CALABRIA.contains(GeoPoint(lat=a["lat"], lon=a["lon"]))
    ]
    assert fuori == [], f"aree protette fuori dalla Calabria: {fuori}"


# --------------------------------------------------------------------------
# 5. Punti d'acqua
# --------------------------------------------------------------------------


def test_ogni_sorgente_ha_almeno_un_mezzo_compatibile(
    water: list[WaterSource],
) -> None:
    """Una sorgente senza `usable_by` e' peso morto nel modello: il solver la
    considera, la scarta per ogni mezzo e ha speso variabili per niente."""
    inutili = [w.source_id for w in water if not w.usable_by]
    assert inutili == [], f"sorgenti senza mezzi compatibili: {inutili}"


def test_ogni_tipo_di_mezzo_che_usa_acqua_ha_dove_rifornirsi(
    fleet: list[Vehicle], water: list[WaterSource]
) -> None:
    """Invariante incrociata fra due file: se esiste un mezzo di un tipo che
    richiede una sorgente, deve esistere almeno una sorgente che lo accetta."""
    serviti = {k for w in water for k in w.usable_by}
    mancanti = sorted(
        {v.kind.value for v in fleet if v.requires_water_source and v.kind not in serviti}
    )
    assert mancanti == [], f"tipi di mezzo senza nessuna sorgente utile: {mancanti}"


def test_una_vasca_non_e_pescabile_da_un_canadair(water: list[WaterSource]) -> None:
    """Un CL-415 imbarca in corsa e ha bisogno di oltre un chilometro di
    specchio libero: da una vasca antincendio non prende niente."""
    sbagliate = [
        w.source_id
        for w in water
        if w.kind is WaterSourceKind.TANK and VehicleKind.CANADAIR in w.usable_by
    ]
    assert sbagliate == []


def test_il_mare_non_serve_i_mezzi_terrestri(water: list[WaterSource]) -> None:
    terrestri = {VehicleKind.AUTOBOTTE, VehicleKind.MODULO_AIB, VehicleKind.SQUADRA_TERRA}
    sbagliate = [
        w.source_id
        for w in water
        if w.kind is WaterSourceKind.SEA and terrestri & set(w.usable_by)
    ]
    assert sbagliate == []


def test_idranti_e_vasche_servono_i_mezzi_terrestri(water: list[WaterSource]) -> None:
    """Se nessun idrante accettasse autobotti e moduli, la componente terrestre
    farebbe una sola sortita e poi resterebbe a secco."""
    terrestri = {VehicleKind.AUTOBOTTE, VehicleKind.MODULO_AIB}
    a_terra = [
        w
        for w in water
        if w.kind in (WaterSourceKind.HYDRANT, WaterSourceKind.TANK)
        and terrestri & set(w.usable_by)
    ]
    assert len(a_terra) >= 2


def test_affidabilita_stagionale_nellintervallo(water: list[WaterSource]) -> None:
    fuori = [
        w.source_id for w in water if not 0.0 < w.seasonal_reliability <= 1.0
    ]
    assert fuori == []


def test_il_mare_e_affidabile_e_i_laghi_no(water: list[WaterSource]) -> None:
    """I laghi silani sono invasi idroelettrici: d'estate vengono svasati e il
    livello cala. Contare su di loro come sul mare e' un errore di modello."""
    mare = [w for w in water if w.kind is WaterSourceKind.SEA]
    laghi = [w for w in water if w.kind is WaterSourceKind.LAKE]
    assert mare and laghi
    assert all(w.seasonal_reliability == 1.0 for w in mare)
    assert all(w.seasonal_reliability < 1.0 for w in laghi)


def test_il_mare_e_su_entrambi_i_versanti(water: list[WaterSource]) -> None:
    """Un focolaio sullo Ionio non deve essere costretto a pescare sul Tirreno:
    la Calabria e' stretta ma la traversata costa comunque minuti di volo."""
    mare = [w for w in water if w.kind is WaterSourceKind.SEA]
    tirreno = [w for w in mare if w.point.lon < 16.2]
    ionio = [w for w in mare if w.point.lon >= 16.2]
    assert len(tirreno) >= 2 and len(ionio) >= 2


def test_i_laghi_silani_reali_sono_presenti(water: list[WaterSource]) -> None:
    nomi = " | ".join(w.name.lower() for w in water)
    for atteso in ("cecita", "ampollino", "arvo", "passante", "ariamacina", "angitola"):
        assert atteso in nomi, f"manca il lago {atteso}"


def test_capacita_dichiarata_positiva_o_assente(water: list[WaterSource]) -> None:
    """`None` vuol dire praticamente illimitato (mare, grandi laghi). Zero o
    un negativo vorrebbero dire una sorgente inutile, ed e' un errore."""
    sbagliate = [
        w.source_id
        for w in water
        if w.capacity_liters is not None and w.capacity_liters <= 0
    ]
    assert sbagliate == []


# --------------------------------------------------------------------------
# 6. Comuni
# --------------------------------------------------------------------------


def test_ogni_comune_ha_i_campi_attesi(comuni: tuple[dict[str, Any], ...]) -> None:
    attesi = {"nome", "provincia", "lat", "lon", "popolazione", "altitudine_m"}
    for c in comuni:
        assert attesi <= set(c), f"campi mancanti in {c.get('nome')}: {attesi - set(c)}"


def test_le_popolazioni_sono_positive(comuni: tuple[dict[str, Any], ...]) -> None:
    """La popolazione pesa in `population_within_3km` e quindi nella priorita':
    uno zero o un negativo la falserebbe in silenzio."""
    sbagliati = [c["nome"] for c in comuni if c["popolazione"] <= 0]
    assert sbagliati == []


def test_le_altitudini_sono_non_negative(comuni: tuple[dict[str, Any], ...]) -> None:
    sbagliati = [c["nome"] for c in comuni if c["altitudine_m"] < 0]
    assert sbagliati == []


def test_le_province_sono_quelle_di_config(comuni: tuple[dict[str, Any], ...]) -> None:
    ignote = {c["provincia"] for c in comuni} - set(PROVINCE)
    assert ignote == set()


def test_ogni_comune_cade_nel_riquadro_della_sua_provincia(
    comuni: tuple[dict[str, Any], ...],
) -> None:
    sbagliati: list[str] = []
    for c in comuni:
        lat_min, lat_max, lon_min, lon_max = RIQUADRI_PROVINCIA[c["provincia"]]
        if not (lat_min <= c["lat"] <= lat_max and lon_min <= c["lon"] <= lon_max):
            sbagliati.append(f"{c['nome']} ({c['provincia']}) @ {c['lat']},{c['lon']}")
    assert sbagliati == [], f"comuni fuori dalla loro provincia: {sbagliati}"


def test_tutte_le_province_sono_rappresentate(
    comuni: tuple[dict[str, Any], ...],
) -> None:
    per_provincia = {p: 0 for p in PROVINCE}
    for c in comuni:
        per_provincia[c["provincia"]] += 1
    assert all(n >= 10 for n in per_provincia.values()), per_provincia


def test_ci_sono_comuni_montani_interni(comuni: tuple[dict[str, Any], ...]) -> None:
    """Sono quelli dove l'interfaccia bosco-abitato conta di piu': se la
    selezione avesse solo capoluoghi di costa, il sistema non vedrebbe mai il
    caso piu' difficile."""
    montani = [c for c in comuni if c["altitudine_m"] >= 600]
    assert len(montani) >= 20


def test_i_capoluoghi_di_provincia_ci_sono(comuni: tuple[dict[str, Any], ...]) -> None:
    nomi = {c["nome"] for c in comuni}
    assert set(PROVINCE) <= nomi, f"capoluoghi mancanti: {set(PROVINCE) - nomi}"


# --------------------------------------------------------------------------
# 7. Aree protette
# --------------------------------------------------------------------------


def test_i_parchi_nazionali_calabresi_ci_sono(
    aree: tuple[dict[str, Any], ...],
) -> None:
    nomi = " | ".join(a["nome"].lower() for a in aree)
    for atteso in ("sila", "aspromonte", "pollino", "serre"):
        assert atteso in nomi, f"manca il parco: {atteso}"


def test_le_aree_hanno_un_raggio_positivo_e_plausibile(
    aree: tuple[dict[str, Any], ...],
) -> None:
    """Il raggio e' quello del cerchio di pari area del parco. Oltre i 40 km
    coprirebbe mezza regione e renderebbe inutile il criterio."""
    for a in aree:
        assert 0.0 < a["raggio_km"] <= 40.0, a


def test_ogni_area_ha_un_tipo(aree: tuple[dict[str, Any], ...]) -> None:
    assert all(a.get("tipo") for a in aree)


# --------------------------------------------------------------------------
# 8. Composizione e plausibilita' della flotta
# --------------------------------------------------------------------------


def _conta(fleet: list[Vehicle], kind: VehicleKind) -> int:
    return sum(1 for v in fleet if v.kind is kind)


def test_il_mix_della_flotta_rispetta_le_quantita_dichiarate(
    fleet: list[Vehicle],
) -> None:
    assert _conta(fleet, VehicleKind.CANADAIR) == 2
    assert 1 <= _conta(fleet, VehicleKind.ELICOTTERO_PESANTE) <= 2
    assert 5 <= _conta(fleet, VehicleKind.ELICOTTERO_MEDIO) <= 7
    assert 2 <= _conta(fleet, VehicleKind.ELICOTTERO_LEGGERO) <= 3
    assert 5 <= _conta(fleet, VehicleKind.MODULO_AIB) <= 7
    assert 3 <= _conta(fleet, VehicleKind.AUTOBOTTE) <= 4
    assert 2 <= _conta(fleet, VehicleKind.SQUADRA_TERRA) <= 3


def test_il_dominio_corrisponde_al_tipo(fleet: list[Vehicle]) -> None:
    for v in fleet:
        atteso = Domain.AIR if v.kind in AEREI else Domain.GROUND
        assert v.domain is atteso, f"{v.vehicle_id}: dominio {v.domain} per {v.kind}"
        assert v.is_aerial == (atteso is Domain.AIR)


def test_le_velocita_sono_plausibili(fleet: list[Vehicle]) -> None:
    """Il controllo che un revisore farebbe a occhio: un pick-up non va a 200,
    un elicottero non va a 40."""
    for v in fleet:
        if v.is_aerial:
            assert 150.0 <= v.cruise_speed_kmh <= 400.0, v.vehicle_id
        else:
            assert 30.0 <= v.cruise_speed_kmh <= 90.0, v.vehicle_id


def test_il_canadair_ha_i_numeri_del_canadair(fleet: list[Vehicle]) -> None:
    """Velocita' e capacita' del CL-415 sono note: 333 km/h e 6137 litri.
    L'autonomia di una missione sta nell'ordine delle quattro ore, non otto."""
    canadair = [v for v in fleet if v.kind is VehicleKind.CANADAIR]
    assert canadair
    for v in canadair:
        assert 300.0 <= v.cruise_speed_kmh <= 360.0
        assert 5500 <= v.water_capacity_liters <= 6500
        assert 120.0 <= v.endurance_minutes <= 300.0
        assert v.cost_per_hour_eur >= 8000.0


def test_gli_elicotteri_pesanti_portano_molta_acqua(fleet: list[Vehicle]) -> None:
    pesanti = [v for v in fleet if v.kind is VehicleKind.ELICOTTERO_PESANTE]
    assert pesanti
    for v in pesanti:
        assert 7000 <= v.water_capacity_liters <= 11000


def test_la_capacita_cresce_con_la_classe(fleet: list[Vehicle]) -> None:
    """Ordinamento atteso: leggero < medio < pesante. Se si invertisse, il
    solver preferirebbe la macchina sbagliata per ragioni che sembrano giuste."""

    def massimo(kind: VehicleKind) -> int:
        return max(v.water_capacity_liters for v in fleet if v.kind is kind)

    assert (
        massimo(VehicleKind.ELICOTTERO_LEGGERO)
        < massimo(VehicleKind.ELICOTTERO_MEDIO)
        < massimo(VehicleKind.ELICOTTERO_PESANTE)
    )


def test_i_mezzi_terrestri_portano_poca_acqua(fleet: list[Vehicle]) -> None:
    """E' la ragione fisica per cui non bastano da soli: un modulo AIB porta
    quattro ettolitri, un Canadair sessantuno."""
    moduli = [v for v in fleet if v.kind is VehicleKind.MODULO_AIB]
    autobotti = [v for v in fleet if v.kind is VehicleKind.AUTOBOTTE]
    assert all(300 <= v.water_capacity_liters <= 500 for v in moduli)
    assert all(2500 <= v.water_capacity_liters <= 4000 for v in autobotti)


def test_le_squadre_a_terra_non_dipendono_da_una_sorgente(
    fleet: list[Vehicle],
) -> None:
    """Fanno bonifica e taglio: la loro utilita' non passa dai litri, e il
    solver non deve legarle a un punto di pescaggio."""
    squadre = [v for v in fleet if v.kind is VehicleKind.SQUADRA_TERRA]
    assert squadre
    for v in squadre:
        assert v.requires_water_source is False
        assert v.water_capacity_liters <= 500


def test_solo_le_squadre_sono_indipendenti_dallacqua(fleet: list[Vehicle]) -> None:
    indipendenti = {v.kind for v in fleet if not v.requires_water_source}
    assert indipendenti <= {VehicleKind.SQUADRA_TERRA}


def test_lautonomia_non_supera_il_turno(fleet: list[Vehicle]) -> None:
    """`endurance_minutes` e' una singola missione, `max_shift_minutes` la
    giornata: se la prima superasse la seconda, il vincolo di turno non
    morderebbe mai e il modello sarebbe sbagliato senza dare errore."""
    for v in fleet:
        assert v.endurance_minutes <= v.max_shift_minutes, v.vehicle_id


def test_i_tempi_e_i_costi_sono_positivi(fleet: list[Vehicle]) -> None:
    for v in fleet:
        assert v.drop_minutes > 0
        assert v.refill_minutes > 0
        assert v.setup_minutes > 0
        assert v.cost_per_hour_eur > 0
        assert v.max_wind_kmh > 0


def test_volare_costa_piu_che_andare_su_strada(fleet: list[Vehicle]) -> None:
    """Non e' un dettaglio contabile: il costo entra nell'obiettivo, e se i
    rapporti fossero invertiti il piano manderebbe i Canadair sui fuocherelli."""
    costo_aereo_minimo = min(v.cost_per_hour_eur for v in fleet if v.is_aerial)
    costo_terra_massimo = max(v.cost_per_hour_eur for v in fleet if not v.is_aerial)
    assert costo_terra_massimo < costo_aereo_minimo


def test_i_mezzi_aerei_non_volano_di_notte(fleet: list[Vehicle]) -> None:
    """Il lancio notturno con mezzi ad ala fissa non si fa. I mezzi terrestri
    invece lavorano, ed e' spesso di notte che si chiude il fronte."""
    assert all(not v.night_capable for v in fleet if v.is_aerial)
    assert any(v.night_capable for v in fleet if not v.is_aerial)


def test_ogni_provincia_ha_un_mezzo_di_casa(
    fleet: list[Vehicle], bases: list[Base]
) -> None:
    per_id = {b.base_id: b for b in bases}
    province_con_mezzi = {per_id[v.home_base_id].provincia for v in fleet}
    assert set(PROVINCE) <= province_con_mezzi


# --------------------------------------------------------------------------
# 9. Il registro restituisce copie fresche
# --------------------------------------------------------------------------


def test_load_fleet_non_condivide_lo_stato_fra_due_chiamate() -> None:
    """E' il contratto dichiarato in `registry.py`. Se venisse a mancare, lo
    stato di una simulazione colerebbe nella successiva e i confronti con la
    baseline diventerebbero silenziosamente falsi."""
    prima = load_fleet()
    prima[0].minutes_flown_today = 123.0
    prima[0].status = VehicleStatus.MAINTENANCE
    prima[0].current_position = GeoPoint(lat=39.0, lon=16.0)

    dopo = load_fleet()
    assert dopo[0].vehicle_id == prima[0].vehicle_id
    assert dopo[0].minutes_flown_today == 0.0
    assert dopo[0].status is VehicleStatus.AVAILABLE
    assert dopo[0].current_position is None


# --------------------------------------------------------------------------
# 10. I JSON sul disco sono davvero quelli che il generatore produce
# --------------------------------------------------------------------------


def _carica_generatore() -> ModuleType:
    """Importa lo script del generatore, che non e' un pacchetto installato."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import genera_dati_statici  # type: ignore[import-not-found]

    return genera_dati_statici


def test_il_generatore_e_deterministico_e_i_file_sono_aggiornati(
    tmp_path: Path,
) -> None:
    """Rigenera in una cartella temporanea e confronta byte a byte.

    Cattura due difetti in un colpo solo: una modifica a mano ai JSON che
    nessuno ha riportato nel generatore, e un generatore non deterministico
    (un `set` iterato, un timestamp, un `random` senza seme).
    """
    gen = _carica_generatore()
    gen.genera(target_dir=tmp_path)

    for nome in FILE_STATICI:
        atteso = (tmp_path / nome).read_bytes()
        effettivo = (STATIC_DIR / nome).read_bytes()
        assert effettivo == atteso, (
            f"{nome} non corrisponde all'output del generatore: "
            "rigeneralo con scripts/genera_dati_statici.py"
        )


def test_i_json_sono_liste_di_oggetti() -> None:
    """Il registro si aspetta una lista in testa a ogni file: se qualcuno la
    incapsulasse in un dizionario, il messaggio d'errore sarebbe oscuro."""
    for nome in FILE_STATICI:
        with (STATIC_DIR / nome).open(encoding="utf-8") as fh:
            dati = json.load(fh)
        assert isinstance(dati, list) and dati, nome
        assert all(isinstance(r, dict) for r in dati), nome


# --------------------------------------------------------------------------
# 11. Le chiavi dei JSON sono esattamente quelle attese
# --------------------------------------------------------------------------
# Questo controllo sembra pedante e non lo e'. `registry.py` legge quasi tutti
# i campi con `d.get(nome, default)`: se qualcuno rinominasse
# `endurance_minutes` in `endurance_min`, il caricamento non darebbe nessun
# errore e ogni mezzo si ritroverebbe l'autonomia di default di 180 minuti.
# Il piano cambierebbe, i test fisici continuerebbero a passare, e la causa
# sarebbe invisibile. Gli insiemi qui sotto sono riscritti a mano di proposito,
# come i riquadri provinciali: sono il contratto atteso, non quello prodotto.

CHIAVI_ATTESE: dict[str, set[str]] = {
    "basi.json": {
        "base_id", "name", "lat", "lon", "provincia", "supports",
        "has_night_capability", "turnaround_minutes", "note",
    },
    "mezzi.json": {
        "vehicle_id", "name", "home_base_id", "status", "profile", "kind",
        "domain", "cruise_speed_kmh", "water_capacity_liters", "drop_minutes",
        "refill_minutes", "setup_minutes", "endurance_minutes",
        "max_shift_minutes", "night_capable", "cost_per_hour_eur",
        "max_wind_kmh", "requires_water_source",
    },
    "sorgenti_acqua.json": {
        "source_id", "name", "lat", "lon", "kind", "usable_by",
        "refill_minutes", "capacity_liters", "seasonal_reliability", "note",
    },
    "comuni.json": {"nome", "provincia", "lat", "lon", "popolazione", "altitudine_m"},
    "aree_protette.json": {"nome", "tipo", "lat", "lon", "raggio_km"},
}


def test_le_chiavi_dei_json_sono_esattamente_quelle_dichiarate() -> None:
    for nome, attese in CHIAVI_ATTESE.items():
        dati = json.loads((STATIC_DIR / nome).read_text(encoding="utf-8"))
        for record in dati:
            effettive = set(record)
            assert effettive == attese, (
                f"{nome}: chiavi diverse dal contratto. "
                f"mancano {sorted(attese - effettive)}, "
                f"in piu' {sorted(effettive - attese)}"
            )


def test_i_mezzi_con_lo_stesso_profilo_hanno_gli_stessi_parametri() -> None:
    """Il campo `profile` dice che due macchine sono lo stesso modello.

    Se due CL-415 avessero autonomie diverse, uno dei due record e' stato
    ritoccato a mano invece che nel profilo del generatore, ed e' il tipo di
    divergenza che poi si legge come se fosse una scelta di modellazione.
    """
    mezzi = json.loads((STATIC_DIR / "mezzi.json").read_text(encoding="utf-8"))
    prestazioni = CHIAVI_ATTESE["mezzi.json"] - {
        "vehicle_id", "name", "home_base_id", "status", "profile",
    }
    per_profilo: dict[str, dict[str, Any]] = {}
    for m in mezzi:
        riferimento = per_profilo.setdefault(m["profile"], m)
        diversi = {
            k: (riferimento[k], m[k]) for k in prestazioni if riferimento[k] != m[k]
        }
        assert diversi == {}, (
            f"{m['vehicle_id']} e {riferimento['vehicle_id']} hanno profilo "
            f"{m['profile']} ma parametri diversi: {diversi}"
        )


# --------------------------------------------------------------------------
# 12. Il validatore del generatore: provare a passargli dati rotti
# --------------------------------------------------------------------------
# `genera()` valida prima di scrivere, quindi il validatore e' l'unica cosa che
# separa un refuso nelle costanti da cinque JSON incoerenti sul disco. Una rete
# di sicurezza che nessuno prova non e' una rete di sicurezza: qui si inietta
# un difetto alla volta e si verifica che venga riconosciuto.


def _dataset() -> list[list[dict[str, Any]]]:
    """I cinque elenchi appena costruiti, pronti da guastare.

    `build_*` restituisce copie profonde, quindi guastarne uno non contamina
    ne' le costanti del generatore ne' il test successivo.
    """
    gen = _carica_generatore()
    return [
        gen.build_bases(),
        gen.build_fleet(),
        gen.build_water_sources(),
        gen.build_comuni(),
        gen.build_protected_areas(),
    ]


def _valida(dati: list[list[dict[str, Any]]]) -> list[str]:
    return _carica_generatore().validate(*dati)


def test_il_validatore_approva_i_dati_del_repository() -> None:
    assert _valida(_dataset()) == []


def test_il_validatore_accetta_cinque_elenchi_vuoti() -> None:
    """Caso limite: nessun dato.

    Un insieme vuoto e' incompleto, non incoerente. Il validatore non deve
    inventarsi errori che non ci sono, ne' inciampare su un minimo o una media
    calcolati su una lista vuota.
    """
    assert _carica_generatore().validate([], [], [], [], []) == []


def test_il_validatore_accetta_un_insieme_minimo() -> None:
    """Caso limite opposto: un solo record per famiglia, scelto coerente.

    Un Canadair a Lamezia con il mare davanti e' il sistema piu' piccolo che
    abbia ancora senso.
    """
    bases, fleet, water, comuni, aree = _dataset()
    minimo = [
        [b for b in bases if b["base_id"] == "BASE_LMZ"],
        [v for v in fleet if v["vehicle_id"] == "CAN_01"],
        [w for w in water if w["source_id"] == "WS_TIR_SEUFEMIA"],
        comuni[:1],
        aree[:1],
    ]
    assert _valida(minimo) == []


Mutazione = Callable[[list[list[dict[str, Any]]]], None]


def _guasta(indice: int, chiave: str, valore: Any) -> Mutazione:
    """Mutazione che scrive `valore` nel primo record di uno dei cinque elenchi."""

    def applica(dati: list[list[dict[str, Any]]]) -> None:
        dati[indice][0][chiave] = valore

    return applica


def _togli_chiave(indice: int, chiave: str) -> Mutazione:
    def applica(dati: list[list[dict[str, Any]]]) -> None:
        del dati[indice][0][chiave]

    return applica


def _duplica_primo(indice: int) -> Mutazione:
    def applica(dati: list[list[dict[str, Any]]]) -> None:
        dati[indice].append(copy.deepcopy(dati[indice][0]))

    return applica


def _canadair_sulla_vasca(dati: list[list[dict[str, Any]]]) -> None:
    for w in dati[2]:
        if w["kind"] == "tank":
            w["usable_by"].append("canadair")


def _togli_i_punti_a_terra(dati: list[list[dict[str, Any]]]) -> None:
    dati[2] = [w for w in dati[2] if w["kind"] not in ("hydrant", "tank")]


# (descrizione, mutazione, frammento che deve comparire nel messaggio)
DIFETTI: list[tuple[str, Mutazione, str]] = [
    ("base senza latitudine", _togli_chiave(0, "lat"), "chiavi mancanti"),
    ("latitudine scritta come stringa", _guasta(0, "lat", "39.0"), "non e' un numero"),
    ("latitudine booleana", _guasta(0, "lat", True), "non e' un numero"),
    ("base fuori dalla Calabria", _guasta(0, "lat", 45.0), "fuori dalla bbox"),
    ("base nella provincia sbagliata", _guasta(0, "provincia", "Crotone"), "riquadro"),
    ("base che non supporta niente", _guasta(0, "supports", []), "nessun tipo"),
    ("base con un tipo inventato",
     _guasta(0, "supports", ["elicottero_a_pedali"]), "ignoto"),
    ("identificativo di base duplicato", _duplica_primo(0), "duplicato"),
    ("mezzo su una base inesistente",
     _guasta(1, "home_base_id", "BASE_CHE_NON_CE"), "base inesistente"),
    ("dominio in contrasto con il tipo", _guasta(1, "domain", "ground"), "incoerente"),
    ("Canadair a velocita' da pick-up", _guasta(1, "cruise_speed_kmh", 55.0), "implausibile"),
    ("velocita' nulla", _guasta(1, "cruise_speed_kmh", 0.0), "non positiva"),
    ("capacita' d'acqua nulla",
     _guasta(1, "water_capacity_liters", 0), "capacita' d'acqua non positiva"),
    ("tempo di sgancio nullo", _guasta(1, "drop_minutes", 0.0), "drop_minutes non positivo"),
    ("costo orario nullo", _guasta(1, "cost_per_hour_eur", 0.0), "cost_per_hour_eur"),
    ("autonomia oltre il turno", _guasta(1, "endurance_minutes", 9999.0), "autonomia maggiore"),
    ("sorgente senza mezzi compatibili", _guasta(2, "usable_by", []), "nessun mezzo compatibile"),
    ("affidabilita' stagionale nulla", _guasta(2, "seasonal_reliability", 0.0), "fuori da (0, 1]"),
    ("affidabilita' stagionale sopra uno",
     _guasta(2, "seasonal_reliability", 1.5), "fuori da (0, 1]"),
    ("capacita' della sorgente nulla", _guasta(2, "capacity_liters", 0), "capacita' non positiva"),
    ("Canadair dichiarato compatibile con una vasca",
     _canadair_sulla_vasca, "non pesca da una vasca"),
    ("nessun punto d'acqua per i mezzi terrestri",
     _togli_i_punti_a_terra, "nessuna sorgente d'acqua lo accetta"),
    ("comune spopolato", _guasta(3, "popolazione", 0), "popolazione non positiva"),
    ("popolazione con uno zero di troppo",
     _guasta(3, "popolazione", 740_000), "popolazione implausibile"),
    ("altitudine negativa", _guasta(3, "altitudine_m", -5), "altitudine negativa"),
    ("altitudine con uno zero di troppo",
     _guasta(3, "altitudine_m", 2190), "altitudine implausibile"),
    ("provincia inventata", _guasta(3, "provincia", "Salerno"), "provincia ignota"),
    ("area protetta senza raggio", _guasta(4, "raggio_km", 0.0), "raggio non positivo"),
    ("area protetta grande come mezza regione",
     _guasta(4, "raggio_km", 100.0), "raggio implausibile"),
    ("area protetta senza tipo", _guasta(4, "tipo", ""), "tipo vuoto"),
]


@pytest.mark.parametrize(
    "descrizione,mutazione,frammento",
    DIFETTI,
    ids=[d[0].replace(" ", "_") for d in DIFETTI],
)
def test_il_validatore_trova_il_difetto_iniettato(
    descrizione: str, mutazione: Mutazione, frammento: str
) -> None:
    dati = _dataset()
    mutazione(dati)
    errori = _valida(dati)
    assert any(frammento in e for e in errori), (
        f"{descrizione}: il validatore non se n'e' accorto. Errori: {errori}"
    )


def test_il_validatore_non_esplode_su_un_record_vuoto() -> None:
    """Regressione.

    Prima, elencare i problemi di un record senza chiavi faceva uscire un
    KeyError dal validatore, cioe' proprio dalla funzione che esiste per
    trasformare i dati rotti in un elenco di messaggi leggibili.
    """
    gen = _carica_generatore()
    errori = gen.validate([{}], [{}], [{}], [{}], [{}])
    assert len(errori) == 5
    assert all("chiavi mancanti" in e for e in errori), errori


def test_il_validatore_non_modifica_gli_elenchi_che_riceve() -> None:
    """E' un rapporto, non un'azione.

    Se normalizzasse o ripulisse i dati, `genera` scriverebbe su disco qualcosa
    di diverso da cio' che ha validato, e la validazione non varrebbe piu' per
    il file prodotto.
    """
    dati = _dataset()
    prima = copy.deepcopy(dati)
    _valida(dati)
    assert dati == prima


def test_i_costruttori_non_condividono_le_liste_con_le_costanti() -> None:
    """Regressione su una aliasing accaduta davvero.

    `build_*` restituiva copie superficiali: il campo `supports` di una base e
    il campo `usable_by` di dieci sorgenti erano LO STESSO oggetto lista della
    costante del modulo. Bastava che un chiamante ci aggiungesse un elemento
    per cambiare i dati di tutte le chiamate successive nello stesso processo.
    """
    gen = _carica_generatore()
    basi = gen.build_bases()
    acqua = gen.build_water_sources()
    basi[0]["supports"].append("mezzo_inventato")
    acqua[0]["usable_by"].append("mezzo_inventato")

    assert "mezzo_inventato" not in gen.build_bases()[0]["supports"]
    assert "mezzo_inventato" not in gen.build_water_sources()[0]["usable_by"]
    # E le sorgenti non devono condividere la lista neanche fra loro: dieci di
    # esse partono dalla stessa costante di comodo (TUTTI_AEREI).
    fresche = gen.build_water_sources()
    fresche[0]["usable_by"].append("mezzo_inventato")
    assert all("mezzo_inventato" not in w["usable_by"] for w in fresche[1:])


# --- valori esattamente agli estremi degli intervalli ---------------------
# Gli estremi sono il posto dove i confronti si sbagliano: un `<` al posto di
# un `<=` sposta il confine di un valore e nessuno se ne accorge, finche' un
# dato legittimo non ci finisce sopra.


def test_i_valori_agli_estremi_degli_intervalli_sono_accettati() -> None:
    gen = _carica_generatore()
    bases, fleet, water, comuni, aree = _dataset()

    aereo = next(v for v in fleet if v["domain"] == "air")
    aereo["cruise_speed_kmh"] = 400.0  # estremo superiore ammesso
    terrestre = next(v for v in fleet if v["domain"] == "ground")
    terrestre["cruise_speed_kmh"] = 30.0  # estremo inferiore ammesso
    # Autonomia esattamente pari al turno: una sola missione lunga quanto la
    # giornata e' ammessa, cio' che non si ammette e' superare il turno.
    aereo["endurance_minutes"] = aereo["max_shift_minutes"]
    # Affidabilita' 1.0 e' l'estremo incluso: e' il valore del mare.
    water[0]["seasonal_reliability"] = 1.0
    # Un angolo esatto della bounding box e' dentro, non fuori.
    lon_min, lat_min, lon_max, lat_max = CALABRIA_BBOX
    aree[0]["lat"], aree[0]["lon"] = lat_min, lon_min
    aree[1]["lat"], aree[1]["lon"] = lat_max, lon_max

    assert gen.validate(bases, fleet, water, comuni, aree) == []


def test_un_soffio_fuori_dagli_estremi_e_rifiutato() -> None:
    gen = _carica_generatore()
    bases, fleet, water, comuni, aree = _dataset()
    aereo = next(v for v in fleet if v["domain"] == "air")
    aereo["cruise_speed_kmh"] = 400.1
    aereo["endurance_minutes"] = aereo["max_shift_minutes"] + 0.1
    lon_min, lat_min, _, _ = CALABRIA_BBOX
    aree[0]["lat"] = lat_min - 0.001

    errori = gen.validate(bases, fleet, water, comuni, aree)
    assert any("implausibile per un mezzo aereo" in e for e in errori), errori
    assert any("autonomia maggiore" in e for e in errori), errori
    assert any("fuori dalla bbox" in e for e in errori), errori


def test_la_bounding_box_e_letta_con_le_coordinate_nel_verso_giusto() -> None:
    """`CALABRIA_BBOX` e' nell'ordine di FIRMS: lon, lat, lon, lat.

    Scambiare i due assi e' l'errore che non da' sintomi evidenti: un punto a
    lat 16 e lon 39 sta in Asia centrale e verrebbe accettato, mentre mezza
    Calabria verrebbe scartata come "fuori dalla bbox". Il controllo costa una
    riga e chiude il caso.
    """
    gen = _carica_generatore()
    assert gen._in_bbox(39.0, 16.0) is True
    assert gen._in_bbox(16.0, 39.0) is False


# --------------------------------------------------------------------------
# 13. Il generatore come script: scrive tutto o non scrive niente
# --------------------------------------------------------------------------


def test_con_check_non_viene_scritto_niente(tmp_path: Path) -> None:
    gen = _carica_generatore()
    conteggi = gen.genera(target_dir=tmp_path, check_only=True)
    assert conteggi["basi"] > 0 and conteggi["mezzi"] > 0
    assert list(tmp_path.iterdir()) == [], "con --check non si tocca il disco"


def test_se_i_dati_non_sono_validi_non_viene_scritto_niente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La scrittura e' tutto o niente.

    Se i file venissero scritti mano a mano e la validazione fallisse a meta',
    `data/static/` resterebbe con due file nuovi e tre vecchi: un insieme che
    non e' mai esistito e che nessuno ha mai validato.
    """
    gen = _carica_generatore()
    monkeypatch.setattr(
        gen,
        "DISLOCAZIONE_MEZZI",
        [("FANT_01", "Mezzo fantasma", "CL415", "BASE_CHE_NON_ESISTE")],
    )
    with pytest.raises(SystemExit) as errore:
        gen.genera(target_dir=tmp_path)
    assert "base inesistente" in str(errore.value)
    assert list(tmp_path.iterdir()) == []


def test_due_rigenerazioni_consecutive_danno_gli_stessi_byte(tmp_path: Path) -> None:
    """Determinismo misurato, non dichiarato.

    Niente `random`, niente orologio, niente iterazione su insiemi: due
    esecuzioni devono produrre gli stessi byte. Senza questa garanzia il
    confronto con la baseline diventerebbe un confronto fra dati diversi.
    """
    gen = _carica_generatore()
    prima, seconda = tmp_path / "a", tmp_path / "b"
    gen.genera(target_dir=prima)
    gen.genera(target_dir=seconda)
    for nome in FILE_STATICI:
        assert (prima / nome).read_bytes() == (seconda / nome).read_bytes(), nome


def test_la_scrittura_non_lascia_file_temporanei(tmp_path: Path) -> None:
    """I JSON si scrivono su un file di appoggio e si rinominano.

    Cosi' un'interruzione a meta' lascia il file vecchio intero invece di uno
    troncato. Finita la scrittura, del file di appoggio non deve restare
    traccia: `registry.py` legge per nome, ma una cartella sporca confonde chi
    guarda, e un `.tmp` rimasto vuol dire che il rename non e' avvenuto.
    """
    gen = _carica_generatore()
    gen.genera(target_dir=tmp_path)
    residui = sorted(p.name for p in tmp_path.iterdir() if p.suffix != ".json")
    assert residui == [], f"file temporanei rimasti: {residui}"


# --------------------------------------------------------------------------
# 14. Casi limite del registro: dati assenti e dati vuoti
# --------------------------------------------------------------------------


def test_il_registro_dice_chiaramente_che_manca_un_file(tmp_path: Path) -> None:
    """Il messaggio deve dire come rimediare: chi clona il repository e trova
    la cartella vuota non ha modo di indovinare il nome dello script."""
    clear_cache()
    try:
        assert static_data_available(tmp_path) is False
        with pytest.raises(StaticDataError) as errore:
            load_bases(tmp_path)
        assert "genera_dati_statici" in str(errore.value)
    finally:
        clear_cache()


def test_il_registro_regge_i_file_vuoti(tmp_path: Path) -> None:
    """Caso limite: i file ci sono ma dentro non c'e' niente.

    E' lo scenario "nessun mezzo disponibile" visto dal lato dei dati. Deve
    produrre elenchi vuoti, non eccezioni: un piano senza mezzi e' un risultato
    legittimo (nessuna assegnazione, tutti i focolai scoperti), un caricamento
    che esplode no.
    """
    for nome in FILE_STATICI:
        (tmp_path / nome).write_text("[]\n", encoding="utf-8")
    clear_cache()
    try:
        assert static_data_available(tmp_path) is True
        assert load_bases(tmp_path) == []
        assert load_fleet(tmp_path) == []
        assert load_water_sources(tmp_path) == []
        # Nota: `load_comuni` e `load_protected_areas` dichiarano il parametro
        # come stringa, mentre gli altri tre caricamenti vogliono un Path.
        assert load_comuni(str(tmp_path)) == ()
        assert load_protected_areas(str(tmp_path)) == ()
    finally:
        clear_cache()


def test_il_registro_legge_davvero_dalla_cartella_che_gli_si_passa(
    tmp_path: Path,
) -> None:
    """Se ignorasse il parametro e leggesse sempre da `data/static`, i test che
    montano dati finti passerebbero per il motivo sbagliato."""
    gen = _carica_generatore()
    gen.genera(target_dir=tmp_path)
    basi = json.loads((tmp_path / "basi.json").read_text(encoding="utf-8"))
    basi = [b for b in basi if b["base_id"] == "BASE_LMZ"]
    (tmp_path / "basi.json").write_text(
        json.dumps(basi, ensure_ascii=False), encoding="utf-8"
    )
    clear_cache()
    try:
        caricate = load_bases(tmp_path)
        assert [b.base_id for b in caricate] == ["BASE_LMZ"]
        assert len(load_bases()) > 1, "la cartella di default non deve cambiare"
    finally:
        clear_cache()


# --------------------------------------------------------------------------
# 15. Geometria: le distanze dichiarate sono davvero quelle
# --------------------------------------------------------------------------
# Le lacune note dei dati (pochi idranti, mare campionato a tratti) sono
# dichiarate nel generatore. Qui vengono MISURATE, cosi' se un domani qualcuno
# togliesse un punto d'acqua il peggioramento si vedrebbe subito, invece di
# restare nascosto dentro una metrica.


def _haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Distanza in linea d'aria su sfera di raggio `EARTH_RADIUS_KM`.

    Riscritta qui e non importata da nessuno: e' lo stesso principio per cui il
    validatore del piano non condivide le formule con il solver. In piu' questo
    e' il punto in cui si confondono gradi e radianti, e un test che riusasse
    la conversione del codice sotto esame non se ne accorgerebbe mai.
    """
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def test_haversine_di_controllo() -> None:
    """Il metro va tarato prima di usarlo per misurare.

    Reggio Calabria e Cosenza distano circa 142 km in linea d'aria (per strada
    sono quasi duecento, ed e' esattamente il divario che il fattore di
    tortuosita' cerca di rappresentare), e due punti coincidenti distano zero.
    Se la formula scambiasse gradi e radianti, il primo numero sarebbe fuori
    scala di un fattore cinquantasette.
    """
    reggio = GeoPoint(lat=38.1113, lon=15.6473)
    cosenza = GeoPoint(lat=39.2983, lon=16.2533)
    assert 135.0 <= _haversine_km(reggio, cosenza) <= 150.0
    assert _haversine_km(reggio, reggio) == pytest.approx(0.0, abs=1e-9)


# Soglia dichiarata: nessun mezzo deve avere il rifornimento compatibile piu'
# vicino oltre i 50 km in linea d'aria. Il peggiore oggi sta a 41.5 km (i mezzi
# terrestri di Cosenza, che si riforniscono all'idrante di Lamezia), ed e' una
# lacuna nota e scritta nel generatore: la rete di idranti vera e' molto piu'
# fitta di quella censita qui.
DISTANZA_MASSIMA_DAL_RIFORNIMENTO_KM = 50.0

# La stessa cosa detta in minuti: andata e ritorno verso l'acqua non devono
# mangiare piu' di questa frazione dell'autonomia, altrimenti il mezzo passa la
# missione a viaggiare e il piano che lo impiega sta in piedi solo sulla carta.
FRAZIONE_MASSIMA_DI_AUTONOMIA = 0.40


def test_ogni_mezzo_ha_un_rifornimento_a_distanza_ragionevole(
    fleet: list[Vehicle], bases: list[Base], water: list[WaterSource]
) -> None:
    per_id = {b.base_id: b for b in bases}
    lontani: list[str] = []
    for v in fleet:
        if not v.requires_water_source:
            continue  # le squadre a terra non vanno a pescare da nessuna parte
        casa = per_id[v.home_base_id].point
        compatibili = [
            _haversine_km(casa, w.point) for w in water if v.kind in w.usable_by
        ]
        assert compatibili, f"{v.vehicle_id}: nessuna sorgente compatibile"
        if min(compatibili) > DISTANZA_MASSIMA_DAL_RIFORNIMENTO_KM:
            lontani.append(f"{v.vehicle_id} a {min(compatibili):.1f} km")
    assert lontani == [], f"mezzi troppo lontani dall'acqua: {lontani}"


def test_il_viaggio_verso_lacqua_non_mangia_lautonomia(
    fleet: list[Vehicle], bases: list[Base], water: list[WaterSource]
) -> None:
    """Il conto e' fatto qui in chiaro, badando alle unita'.

    La distanza e' in km e la velocita' in km/h, quindi il rapporto e' in ORE e
    va moltiplicato per 60 prima di confrontarlo con `endurance_minutes`, che
    e' in MINUTI. Per i mezzi terrestri la linea d'aria viene corretta con il
    fattore di tortuosita' stradale preso da `config.py`: in Calabria la strada
    e' quasi meta' piu' lunga della linea retta.
    """
    per_id = {b.base_id: b for b in bases}
    critici: list[str] = []
    for v in fleet:
        if not v.requires_water_source:
            continue
        casa = per_id[v.home_base_id].point
        linea_aria = min(
            _haversine_km(casa, w.point) for w in water if v.kind in w.usable_by
        )
        percorso_km = linea_aria if v.is_aerial else linea_aria * ROAD_TORTUOSITY
        minuti_andata_e_ritorno = 2.0 * percorso_km / v.cruise_speed_kmh * 60.0
        frazione = minuti_andata_e_ritorno / v.endurance_minutes
        if frazione > FRAZIONE_MASSIMA_DI_AUTONOMIA:
            critici.append(f"{v.vehicle_id} usa il {frazione:.0%} dell'autonomia")
    assert critici == [], f"mezzi che viaggerebbero piu' di quanto lavorano: {critici}"


def test_ogni_base_cade_nel_riquadro_della_sua_provincia(bases: list[Base]) -> None:
    """La stessa rete tesa sotto i comuni, tesa anche sotto le basi: intercetta
    la coordinata spostata di un grado, che sono un centinaio di chilometri."""
    sbagliate: list[str] = []
    for b in bases:
        lat_min, lat_max, lon_min, lon_max = RIQUADRI_PROVINCIA[b.provincia]
        if not (lat_min <= b.point.lat <= lat_max and lon_min <= b.point.lon <= lon_max):
            sbagliate.append(
                f"{b.base_id} ({b.provincia}) @ {b.point.lat},{b.point.lon}"
            )
    assert sbagliate == [], f"basi fuori dalla loro provincia: {sbagliate}"


def test_un_mezzo_notturno_sta_su_una_base_aperta_di_notte(
    fleet: list[Vehicle], bases: list[Base]
) -> None:
    """Convenzione dei dati, dichiarata qui perche' non venga letta a rovescio:
    `has_night_capability` sulla base vuol dire che di notte la base e'
    presidiata e il mezzo puo' uscire. Un mezzo notturno su una elisuperficie
    chiusa dal tramonto sarebbe notturno solo sulla carta, e il piano lo
    impiegherebbe in un orario in cui nella realta' non parte nessuno.
    """
    per_id = {b.base_id: b for b in bases}
    bloccati = [
        v.vehicle_id
        for v in fleet
        if v.night_capable and not per_id[v.home_base_id].has_night_capability
    ]
    assert bloccati == [], f"mezzi notturni su basi chiuse di notte: {bloccati}"


def test_lelenco_dei_comuni_copre_esattamente_le_province_di_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le province stanno in `config.py`, i comuni nel generatore.

    Se i due elenchi divergessero, una provincia sparirebbe dal file senza che
    nessuno se ne accorga: `comuni.json` resterebbe un file valido, solo con un
    quinto di Calabria in meno. Meglio un errore esplicito in fase di
    generazione che un buco silenzioso nei dati.
    """
    gen = _carica_generatore()
    assert set(gen.COMUNI_PER_PROVINCIA) == set(PROVINCE)

    monkeypatch.setattr(gen, "PROVINCE", list(PROVINCE) + ["Matera"])
    with pytest.raises(ValueError) as errore:
        gen.build_comuni()
    assert "Matera" in str(errore.value)
