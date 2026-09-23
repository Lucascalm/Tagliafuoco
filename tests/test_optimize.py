"""Test del sottosistema di ottimizzazione.

Cosa si prova qui, e perche' proprio questo
-------------------------------------------
Un modello di ottimizzazione e' difficile da testare come si testa una
funzione: l'esito dipende da pesi, tarature e dati, e "il piano giusto" non e'
un valore che si possa scrivere a mano. Quindi non si verifica quale piano
esce, si verificano le PROPRIETA' che ogni piano deve avere comunque:

  - le leggi fisiche non sono violate (orizzonte, autonomia, non
    sovrapposizione degli interventi dello stesso mezzo);
  - i vincoli duri tradotti dall'ordine sono rispettati davvero, non solo
    accettati senza errore;
  - i vincoli morbidi spostano il piano nella direzione giusta (si confronta
    lo stesso scenario con e senza il vincolo);
  - a parita' di scenario e di seme, il piano e' identico.

L'ultima e' la piu' importante per il progetto: il confronto con la baseline
non significa niente se il solver risponde in modo diverso a ogni giro.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from ortools.sat.python import cp_model

from tagliafuoco.config import LATE_RESPONSE_MINUTES, OBJECTIVE_WEIGHTS
from tagliafuoco.domain.models import (
    Base,
    Detection,
    Domain,
    FireCluster,
    GeoPoint,
    LandCover,
    Plan,
    Scenario,
    Severity,
    Vehicle,
    VehicleKind,
    VehicleStatus,
    WaterSource,
    WaterSourceKind,
)
from tagliafuoco.nlu.schema import (
    ConstraintKind,
    ConstraintSet,
    Enforcement,
    ExcludeVehicle,
    ForbidAssignment,
    ForceAssignment,
    MaxResponseTime,
    MinVehiclesOnFire,
    ObjectiveTerm,
    ObjectiveWeight,
    PrioritizeZone,
    ProtectInfrastructure,
    ReserveVehicle,
    Selector,
    SelectorField,
    TimeWindow,
    Unsupported,
    VehicleTypeQuota,
    ZoneKind,
)
from tagliafuoco.optimize import geometry
from tagliafuoco.optimize import model as optimize_model
from tagliafuoco.optimize import objective as objective_module
from tagliafuoco.optimize import solver as solver_module
from tagliafuoco.optimize.model import build_model, vehicle_is_employable
from tagliafuoco.optimize.objective import (
    activation_cost_eur,
    assignment_cost_eur,
    build_terms,
)

# `_coeff` e' privato e si importa lo stesso: e' l'unico punto in cui la
# protezione contro l'arrotondamento a zero e' osservabile. Dall'esterno un
# coefficiente nullo e uno unitario producono espressioni CP-SAT
# indistinguibili, e il difetto si vedrebbe solo nei piani, mesi dopo, come un
# criterio che non conta mai. Vale la deroga.
from tagliafuoco.optimize.objective import _coeff
from tagliafuoco.optimize.solver import solve

# Limite di tempo basso: gli scenari dei test sono minuscoli e devono
# chiudere in pochi centesimi. Se un test ci mette di piu', e' un sintomo.
TEST_MAX_SECONDS = 5.0


# --------------------------------------------------------------------------
# Aiutanti
# --------------------------------------------------------------------------


def fingerprint(plan: Plan) -> list[tuple]:
    """Impronta confrontabile di un piano, senza i campi che variano sempre.

    `solve_seconds` cambia a ogni esecuzione per definizione, quindi non entra
    nel confronto: il determinismo riguarda le decisioni, non il cronometro.
    """
    return [
        (
            a.vehicle_id,
            a.fire_id,
            a.departure_minute,
            a.arrival_minute,
            a.release_minute,
            a.sorties,
            a.water_delivered_liters,
            a.water_source_id,
        )
        for a in plan.assignments
    ]


def liters_on(plan: Plan, fire_id: str) -> int:
    return sum(a.water_delivered_liters for a in plan.assignments_for_fire(fire_id))


def constraint_set(*constraints, set_id: str = "CS_TEST") -> ConstraintSet:
    return ConstraintSet(constraint_set_id=set_id, constraints=list(constraints))


def by_id(values: list[str]) -> Selector:
    return Selector(field=SelectorField.VEHICLE_ID, values=values)


# --------------------------------------------------------------------------
# Fixture locali: scenari costruiti su misura per una proprieta' sola
# --------------------------------------------------------------------------


@pytest.fixture
def lago_silano() -> WaterSource:
    """Lago vicino ai focolai di prova, pescabile dall'elicottero."""
    return WaterSource(
        source_id="WS_LAGO",
        name="Lago di prova",
        point=GeoPoint(lat=39.2800, lon=16.3200),
        kind=WaterSourceKind.LAKE,
        usable_by=[VehicleKind.ELICOTTERO_MEDIO],
        refill_minutes=1.5,
    )


@pytest.fixture
def idrante() -> WaterSource:
    """Idrante di paese, utilizzabile solo dal modulo terrestre."""
    return WaterSource(
        source_id="WS_IDRANTE",
        name="Idrante Rende",
        point=GeoPoint(lat=39.2900, lon=16.3100),
        kind=WaterSourceKind.HYDRANT,
        usable_by=[VehicleKind.MODULO_AIB, VehicleKind.AUTOBOTTE],
        refill_minutes=15.0,
    )


def _piccolo_focolaio(
    fire_id: str, lat: float, lon: float, instant: datetime, liters: int
) -> FireCluster:
    """Focolaio piccolo e vicino: serve a rendere fattibile la copertura piena.

    Gli scenari di `conftest.py` hanno fabbisogni realistici (decine di
    migliaia di litri) che la flotta di prova non riesce a coprire in quattro
    ore. Vanno benissimo per provare i compromessi, ma per provare la
    copertura e la sequenza di due missioni serve un caso piu' piccolo.
    """
    det = Detection(
        detection_id=f"D_{fire_id}",
        point=GeoPoint(lat=lat, lon=lon),
        acquired_at=instant,
        satellite="N",
        instrument="VIIRS",
        confidence=80.0,
        frp_mw=8.0,
        scan_km=0.375,
        track_km=0.375,
    )
    return FireCluster(
        fire_id=fire_id,
        centroid=det.point,
        detections=[det],
        first_seen=instant,
        last_seen=instant,
        total_frp_mw=8.0,
        max_frp_mw=8.0,
        mean_confidence=80.0,
        area_km2=0.05,
        radius_km=0.12,
        comune="Rende",
        provincia="Cosenza",
        land_cover=LandCover.GRASSLAND,
        distance_to_wui_km=6.0,
        severity=Severity.LOW,
        water_demand_liters=liters,
        base_priority=0.6,
    )


@pytest.fixture
def scenario_due_focolai_vicini(
    instant: datetime,
    base_cosenza: Base,
    elicottero: Vehicle,
    lago_silano: WaterSource,
) -> Scenario:
    """Un mezzo solo, due focolai piccoli: obbliga a due missioni in sequenza."""
    return Scenario(
        scenario_id="SC_DUE_VICINI",
        timestamp=instant,
        horizon_minutes=240,
        fires=[
            _piccolo_focolaio("F_A", 39.3000, 16.3000, instant, 3000),
            _piccolo_focolaio("F_B", 39.2500, 16.3500, instant, 3000),
        ],
        vehicles=[copy.deepcopy(elicottero)],
        bases=[base_cosenza],
        water_sources=[lago_silano],
        is_night=False,
    )


@pytest.fixture
def scenario_aria_o_terra(
    instant: datetime,
    base_cosenza: Base,
    elicottero: Vehicle,
    modulo_aib: Vehicle,
    lago_silano: WaterSource,
    idrante: WaterSource,
) -> Scenario:
    """Un focolaio raggiungibile sia dall'elicottero sia dal modulo a terra.

    Serve per la prova del volo notturno: di giorno vince il mezzo aereo, di
    notte deve restare a terra e il lavoro passa al terrestre. Senza un mezzo
    terrestre utilizzabile la prova direbbe solo "di notte non si fa niente",
    che e' molto piu' debole.
    """
    return Scenario(
        scenario_id="SC_ARIA_O_TERRA",
        timestamp=instant,
        horizon_minutes=240,
        fires=[_piccolo_focolaio("F_A", 39.3000, 16.3000, instant, 800)],
        vehicles=[copy.deepcopy(elicottero), copy.deepcopy(modulo_aib)],
        bases=[base_cosenza],
        water_sources=[lago_silano, idrante],
        is_night=False,
    )


# --------------------------------------------------------------------------
# Geometria e profilo di missione
# --------------------------------------------------------------------------


def test_haversine_e_strada_sono_coerenti(base_lamezia: Base, base_cosenza: Base):
    """La distanza in linea d'aria e quella stradale stanno nel rapporto atteso."""
    aria = geometry.haversine_km(base_lamezia.point, base_cosenza.point)
    strada = geometry.road_distance_km(base_lamezia.point, base_cosenza.point)

    # Lamezia Terme e Cosenza distano poco piu' di quaranta chilometri.
    assert 40.0 < aria < 50.0
    assert strada == pytest.approx(aria * 1.45)
    assert geometry.haversine_km(base_lamezia.point, base_lamezia.point) == 0.0


def test_il_mezzo_terrestre_paga_la_tortuosita(
    canadair: Vehicle, modulo_aib: Vehicle, base_cosenza: Base, fire_sila: FireCluster
):
    """Stesso segmento: l'aereo lo vola dritto, il terrestre lo percorre su strada."""
    aereo = geometry.travel_distance_km(
        canadair, base_cosenza.point, fire_sila.centroid
    )
    terra = geometry.travel_distance_km(
        modulo_aib, base_cosenza.point, fire_sila.centroid
    )
    assert terra > aereo
    assert terra == pytest.approx(aereo * 1.45)


def test_il_profilo_non_supera_mai_l_autonomia(
    canadair: Vehicle, base_lamezia: Base, fire_sila: FireCluster, sea_source: WaterSource
):
    """Il limite superiore di `sorties` deve essere davvero volabile.

    E' il patto fra `geometry` e `model`: il modello si fida del limite e non
    lo ricontrolla, quindi il limite deve essere calcolato giusto.
    """
    profile = geometry.build_mission_profile(
        canadair, base_lamezia, fire_sila, [sea_source], 240
    )
    assert profile is not None
    assert profile.max_sorties >= 1
    assert profile.water_source_id == sea_source.source_id

    durata = geometry.mission_minutes(
        canadair,
        base_lamezia.point,
        fire_sila,
        profile.cycle_minutes,
        profile.max_sorties,
    )
    assert durata <= canadair.endurance_minutes
    assert durata <= 240

    # Un ciclo in piu' sforerebbe: il limite e' stretto, non prudenziale.
    oltre = geometry.mission_minutes(
        canadair,
        base_lamezia.point,
        fire_sila,
        profile.cycle_minutes,
        profile.max_sorties + 1,
    )
    assert oltre > min(canadair.endurance_minutes, 240) or profile.max_sorties == (
        -(-fire_sila.water_demand_liters // canadair.water_capacity_liters)
    )


def test_la_sorgente_inaffidabile_viene_scartata(
    elicottero: Vehicle, fire_sila: FireCluster, sea_source: WaterSource
):
    """Una pozza che d'estate e' quasi sempre secca non e' un punto di pescaggio."""
    secca = copy.deepcopy(sea_source)
    secca.source_id = "WS_SECCA"
    secca.point = fire_sila.centroid  # vicinissima, ma inaffidabile
    secca.seasonal_reliability = 0.1

    scelta = geometry.best_water_source(elicottero, fire_sila, [secca, sea_source])
    assert scelta is not None
    assert scelta.source_id == sea_source.source_id


def test_il_mezzo_senza_sorgente_non_entra_nel_modello(scenario_minimo: Scenario):
    """Il modulo AIB non puo' pescare in mare: non deve avere variabili."""
    mv = build_model(scenario_minimo)
    assert mv.pairs_for_vehicle("AIB_01") == []
    assert mv.pairs_for_vehicle("CAN_01") != []


# --------------------------------------------------------------------------
# Scenario minimo
# --------------------------------------------------------------------------


def test_scenario_minimo_risolvibile(scenario_minimo: Scenario):
    """Il caso base produce un piano leggibile e sensato."""
    plan = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.producer == "cpsat"
    assert plan.assignments, "il piano non deve essere vuoto"
    assert plan.objective_value is not None

    for a in plan.assignments:
        assert scenario_minimo.vehicle_by_id(a.vehicle_id) is not None
        assert scenario_minimo.fire_by_id(a.fire_id) is not None
        assert a.sorties >= 1
        assert a.water_delivered_liters > 0
        assert a.water_source_id is not None
        assert a.estimated_cost_eur > 0


def test_i_tempi_stanno_dentro_l_orizzonte(scenario_minimo: Scenario):
    """Nessuna missione comincia prima di zero ne' finisce dopo l'orizzonte."""
    plan = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert plan.assignments

    for a in plan.assignments:
        vehicle = scenario_minimo.vehicle_by_id(a.vehicle_id)
        assert 0.0 <= a.departure_minute
        assert a.departure_minute <= a.arrival_minute <= a.release_minute
        assert a.release_minute <= scenario_minimo.horizon_minutes
        # Autonomia della singola missione.
        assert a.engaged_minutes <= vehicle.endurance_minutes


def test_il_turno_del_mezzo_non_viene_sforato(scenario_due_focolai_vicini: Scenario):
    """La somma degli impegni di un mezzo resta dentro il suo turno."""
    plan = solve(
        scenario_due_focolai_vicini, max_seconds=TEST_MAX_SECONDS, deterministic=True
    )
    for vehicle in scenario_due_focolai_vicini.vehicles:
        impegno = sum(
            a.engaged_minutes for a in plan.assignments_for_vehicle(vehicle.vehicle_id)
        )
        assert impegno <= vehicle.max_shift_minutes


def test_gli_interventi_dello_stesso_mezzo_non_si_sovrappongono(
    scenario_due_focolai_vicini: Scenario,
):
    """E' la proprieta' che NoOverlap sugli intervalli opzionali deve garantire."""
    plan = solve(
        scenario_due_focolai_vicini, max_seconds=TEST_MAX_SECONDS, deterministic=True
    )

    # Lo scenario e' costruito perche' convenga servirli entrambi.
    assert len(plan.assignments) == 2
    assert plan.unassigned_fire_ids == []

    per_mezzo = plan.assignments_for_vehicle("ELI_01")
    assert len(per_mezzo) == 2
    prima, dopo = per_mezzo
    assert prima.release_minute <= dopo.departure_minute


def test_il_tetto_di_focolai_per_mezzo_e_un_parametro(
    scenario_due_focolai_vicini: Scenario,
):
    """Con il tetto a uno, lo stesso mezzo non puo' prendersi due focolai."""
    plan = solve(
        scenario_due_focolai_vicini,
        max_seconds=TEST_MAX_SECONDS,
        deterministic=True,
        max_fires_per_vehicle=1,
    )
    assert len(plan.assignments_for_vehicle("ELI_01")) <= 1


# --------------------------------------------------------------------------
# Vincoli tradotti dall'ordine del coordinatore
# --------------------------------------------------------------------------


def test_prioritize_zone_sposta_l_acqua_sul_focolaio_wui(scenario_minimo: Scenario):
    """"Prima le zone vicino alle case" deve cambiare il piano, non solo le note.

    Nello scenario minimo la copertura piena e' fuori portata per entrambi i
    focolai: se il vincolo agisse solo sul termine di copertura non si
    vedrebbe nulla. Agisce anche sul termine idrico, che e' a credito
    parziale, quindi l'acqua si sposta davvero.
    """
    senza = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    cs = constraint_set(
        PrioritizeZone(
            constraint_id="pz1",
            zone_kind=ZoneKind.WUI,
            weight_multiplier=5.0,
            enforcement=Enforcement.SOFT,
            source_span="prima le zone vicino alle case",
        )
    )
    con = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert liters_on(con, "F_WUI") > liters_on(senza, "F_WUI")
    assert con.constraint_set_id == "CS_TEST"
    assert any("Priorita' moltiplicata" in n for n in con.notes)


def test_prioritize_zone_su_comune(scenario_minimo: Scenario):
    """La zona puo' essere un comune, non solo l'interfaccia urbano-foresta."""
    cs = constraint_set(
        PrioritizeZone(
            constraint_id="pz2",
            zone_kind=ZoneKind.COMUNE,
            zone_values=["Reggio Calabria"],
            weight_multiplier=6.0,
            enforcement=Enforcement.SOFT,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert liters_on(plan, "F_WUI") > 0


def test_reserve_vehicle_esclude_davvero_il_mezzo(scenario_minimo: Scenario):
    """Un mezzo di riserva non deve comparire in nessuna assegnazione."""
    senza = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert any(a.vehicle_id == "CAN_01" for a in senza.assignments), (
        "senza vincolo il Canadair viene usato: e' il presupposto del test"
    )

    cs = constraint_set(
        ReserveVehicle(
            constraint_id="rv1",
            selector=by_id(["CAN_01"]),
            enforcement=Enforcement.HARD,
            source_span="il Canadair resta di riserva",
        )
    )
    con = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert con.solver_status in ("OPTIMAL", "FEASIBLE")
    assert all(a.vehicle_id != "CAN_01" for a in con.assignments)
    assert con.assignments, "gli altri mezzi devono continuare a lavorare"


def test_exclude_vehicle_per_tipo(scenario_minimo: Scenario):
    """Il selettore lavora per attributo: "gli elicotteri", non le matricole."""
    cs = constraint_set(
        ExcludeVehicle(
            constraint_id="ev1",
            selector=Selector(
                field=SelectorField.VEHICLE_KIND, values=["elicottero_medio"]
            ),
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert all(a.vehicle_id != "ELI_01" for a in plan.assignments)


def test_max_response_time_rispettato(scenario_minimo: Scenario):
    """Nessun mezzo puo' arrivare oltre il tetto imposto dal coordinatore."""
    limite = 15
    cs = constraint_set(
        MaxResponseTime(
            constraint_id="mrt1",
            minutes=limite,
            enforcement=Enforcement.HARD,
            source_span="voglio tutti sul posto entro un quarto d'ora",
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.assignments, "il vincolo e' stretto ma non impossibile"
    for a in plan.assignments:
        assert a.arrival_minute <= limite

    # Il Canadair non ce la fa nemmeno partendo subito: setup piu' volo fanno
    # gia' piu' di un quarto d'ora. Deve essere sparito dal piano.
    assert all(a.vehicle_id != "CAN_01" for a in plan.assignments)


def test_forbid_assignment_vieta_la_coppia(scenario_minimo: Scenario):
    """Il divieto riguarda l'accoppiamento, non il mezzo."""
    cs = constraint_set(
        ForbidAssignment(
            constraint_id="fb1",
            vehicle_selector=by_id(["CAN_01"]),
            fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert not any(
        a.vehicle_id == "CAN_01" and a.fire_id == "F_SILA" for a in plan.assignments
    )


def test_min_vehicles_on_fire(scenario_minimo: Scenario):
    """Due mezzi sul focolaio in interfaccia, anche se l'obiettivo non lo vorrebbe."""
    cs = constraint_set(
        MinVehiclesOnFire(
            constraint_id="mv1",
            fire_selector=Selector(field=SelectorField.FIRE_IS_WUI, values=["true"]),
            count=2,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert len(plan.assignments_for_fire("F_WUI")) >= 2


def test_objective_weight_riscrive_il_peso(scenario_minimo: Scenario):
    """Azzerando il peso del costo il solver diventa piu' generoso con l'acqua."""
    base = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    cs = constraint_set(
        ObjectiveWeight(
            constraint_id="ow1",
            term=ObjectiveTerm.COST,
            weight=0.0,
            enforcement=Enforcement.SOFT,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert any("peso cost = 0" in n for n in plan.notes)
    acqua_base = sum(a.water_delivered_liters for a in base.assignments)
    acqua_ora = sum(a.water_delivered_liters for a in plan.assignments)
    assert acqua_ora >= acqua_base


def test_vincolo_non_supportato_finisce_nelle_note(scenario_minimo: Scenario):
    """L'astensione del traduttore deve arrivare fino al coordinatore."""
    cs = constraint_set(
        Unsupported(
            constraint_id="un1",
            reason="richiesta di coordinamento con la Protezione Civile",
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.assignments, "un vincolo non supportato non blocca la pianificazione"
    assert any("un1" in n and "non applicato" in n for n in plan.notes)


# --------------------------------------------------------------------------
# Notte
# --------------------------------------------------------------------------


def test_di_notte_i_mezzi_aerei_non_volano(scenario_aria_o_terra: Scenario):
    """Di notte l'elicottero sparisce dal modello, il modulo a terra resta.

    Si guardano due cose diverse e complementari. La prima e' strutturale: di
    giorno l'elicottero ha delle variabili nel modello, di notte non ne ha
    nessuna, perche' una coppia impossibile non viene proprio creata. La
    seconda e' sul piano: di notte il lavoro lo fa il mezzo terrestre, quindi
    la prova non si limita a dire "di notte non succede niente".
    """
    mv_giorno = build_model(scenario_aria_o_terra)
    assert mv_giorno.pairs_for_vehicle("ELI_01"), "di giorno l'elicottero puo' volare"

    notte = copy.deepcopy(scenario_aria_o_terra)
    notte.scenario_id = "SC_ARIA_O_TERRA_NOTTE"
    notte.is_night = True

    mv_notte = build_model(notte)
    assert mv_notte.pairs_for_vehicle("ELI_01") == []
    assert mv_notte.pairs_for_vehicle("AIB_01")

    piano = solve(notte, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    aerei = {v.vehicle_id for v in notte.vehicles if v.domain is Domain.AIR}
    assert all(a.vehicle_id not in aerei for a in piano.assignments)
    assert any(a.vehicle_id == "AIB_01" for a in piano.assignments), (
        "il mezzo terrestre deve poter lavorare anche di notte"
    )


def test_scenario_notturno_di_conftest_non_produce_voli(scenario_notturno: Scenario):
    """Stesso principio sulla fixture condivisa, dove gli aerei sono fuori turno."""
    plan = solve(scenario_notturno, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    aerei = {v.vehicle_id for v in scenario_notturno.vehicles if v.domain is Domain.AIR}
    assert all(a.vehicle_id not in aerei for a in plan.assignments)
    for v in scenario_notturno.vehicles:
        if v.domain is Domain.AIR:
            assert not vehicle_is_employable(v, scenario_notturno)


def test_vento_oltre_il_limite_inchioda_i_mezzi_aerei(scenario_minimo: Scenario):
    """Sopra il vento massimo dichiarato l'aereo resta a terra, come di notte."""
    burrasca = copy.deepcopy(scenario_minimo)
    burrasca.wind_kmh = 90.0
    plan = solve(burrasca, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert all(
        burrasca.vehicle_by_id(a.vehicle_id).domain is not Domain.AIR
        for a in plan.assignments
    )


# --------------------------------------------------------------------------
# Infattibilita' e rilassamento
# --------------------------------------------------------------------------


def test_ordine_contraddittorio_viene_rilassato_e_dichiarato(scenario_minimo: Scenario):
    """Un ordine che si contraddice deve produrre un piano piu' una spiegazione.

    "Il Canadair resta fermo" e "almeno un Canadair sulla Sila" non possono
    valere insieme. La risposta utile non e' una pagina bianca: e' un piano
    con scritto quale parte dell'ordine e' stata lasciata cadere.
    """
    cs = constraint_set(
        ExcludeVehicle(
            constraint_id="c_esclude",
            selector=by_id(["CAN_01"]),
            enforcement=Enforcement.HARD,
            source_span="il Canadair non si muove",
        ),
        ForceAssignment(
            constraint_id="c_forza",
            vehicle_selector=by_id(["CAN_01"]),
            fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
            min_count=1,
            enforcement=Enforcement.HARD,
            source_span="mandate il Canadair sulla Sila",
        ),
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.assignments, "il rilassamento deve produrre un piano, non il vuoto"
    testo = " ".join(plan.notes)
    assert "rilassat" in testo
    assert "c_esclude" in testo and "c_forza" in testo


def test_vincolo_impossibile_da_solo_viene_rilassato(scenario_minimo: Scenario):
    """Anche un ordine solo puo' essere impossibile: sei mezzi non esistono."""
    cs = constraint_set(
        MinVehiclesOnFire(
            constraint_id="c_sei",
            fire_selector=Selector(field=SelectorField.ALL),
            count=6,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert "c_sei" in " ".join(plan.notes)


def test_i_vincoli_morbidi_non_rendono_mai_infattibile(scenario_minimo: Scenario):
    """Lo stesso ordine impossibile, ma morbido: nessun rilassamento necessario."""
    cs = constraint_set(
        MinVehiclesOnFire(
            constraint_id="c_sei_soft",
            fire_selector=Selector(field=SelectorField.ALL),
            count=6,
            enforcement=Enforcement.SOFT,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert "rilassat" not in " ".join(plan.notes)


# --------------------------------------------------------------------------
# Determinismo
# --------------------------------------------------------------------------


def test_determinismo_stesso_scenario_stesso_piano(scenario_minimo: Scenario):
    """Due esecuzioni con lo stesso seme devono dare lo stesso piano.

    Senza questa proprieta' il confronto con la baseline non misura niente,
    perche' la differenza fra i due potrebbe venire dal rumore del solver.
    """
    uno = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    due = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert fingerprint(uno) == fingerprint(due)
    assert uno.objective_value == due.objective_value
    assert uno.unassigned_fire_ids == due.unassigned_fire_ids


def test_determinismo_con_vincoli(scenario_minimo: Scenario):
    """Vale anche quando c'e' un ordine tradotto in mezzo."""
    cs = constraint_set(
        PrioritizeZone(
            constraint_id="pz_det",
            zone_kind=ZoneKind.WUI,
            weight_multiplier=4.0,
            enforcement=Enforcement.SOFT,
        ),
        ReserveVehicle(
            constraint_id="rv_det",
            selector=Selector(field=SelectorField.VEHICLE_DOMAIN, values=["ground"]),
            enforcement=Enforcement.HARD,
        ),
    )
    uno = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    due = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert fingerprint(uno) == fingerprint(due)


def test_la_copia_profonda_dello_scenario_non_cambia_il_piano(
    scenario_minimo: Scenario,
):
    """Il piano dipende dai dati, non dall'identita' degli oggetti in memoria."""
    gemello = copy.deepcopy(scenario_minimo)
    uno = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    due = solve(gemello, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert fingerprint(uno) == fingerprint(due)


# --------------------------------------------------------------------------
# Casi degeneri
# --------------------------------------------------------------------------


def test_scenario_senza_focolai(scenario_minimo: Scenario):
    """Niente da spegnere: piano vuoto, nessuna eccezione."""
    vuoto = copy.deepcopy(scenario_minimo)
    vuoto.fires = []
    plan = solve(vuoto, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.assignments == []
    assert plan.unassigned_fire_ids == []
    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")


def test_scenario_senza_mezzi(scenario_minimo: Scenario):
    """Niente con cui spegnere: tutti i focolai restano scoperti, e si dice."""
    vuoto = copy.deepcopy(scenario_minimo)
    vuoto.vehicles = []
    plan = solve(vuoto, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {"F_SILA", "F_WUI"}


# ==========================================================================
# Casi limite: conversione dei tempi e geometria
# ==========================================================================
#
# Queste prove guardano l'aritmetica prima del solver. Sono le piu' noiose e
# le piu' redditizie: un errore di unita' di misura o un arrotondamento che
# cade dalla parte sbagliata non fa fallire nulla in modo rumoroso, produce
# solo piani leggermente troppo ottimisti che il validatore boccera' molto
# piu' tardi e senza dire perche'.


def test_le_durate_salgono_e_i_budget_scendono():
    """La regola di arrotondamento e' asimmetrica, e deve restare asimmetrica.

    Se le durate si arrotondassero per difetto, il modello si concederebbe
    fino a sei secondi di missione in piu' per ogni tratta. Su una missione
    con venti cicli sono due minuti di autonomia inventati.
    """
    # Valore esatto: nessuno dei due arrotondamenti lo sposta.
    assert geometry.to_units_up(24.0) == 240
    assert geometry.to_units_down(24.0) == 240

    # Valore non esatto: la durata sale, il budget scende.
    assert geometry.to_units_up(24.05) == 241
    assert geometry.to_units_down(24.05) == 240

    # Un centesimo di minuto basta a far salire una durata.
    assert geometry.to_units_up(0.01) == 1
    assert geometry.to_units_down(0.09) == 0

    # E il giro di andata e ritorno sulle unita' intere e' esatto.
    assert geometry.from_units(geometry.to_units_up(13.7)) == pytest.approx(13.7)


def test_l_epsilon_protegge_dagli_off_by_one_dei_float():
    """Un decimo non si rappresenta in binario, e l'arrotondamento non deve caderci.

    Senza l'epsilon in `to_units_up`, un valore che vale 24 minuti ma arriva
    come 24.000000000000004 diventerebbe 241 unita' invece di 240: un'unita'
    di tempo regalata a ogni chiamata, sempre nella stessa direzione. Con
    venti cicli per missione diventa un errore sistematico visibile, ed e' il
    tipo di errore che nessuno cerca perche' "i conti tornano".
    """
    # Somma di duecentoquaranta decimi di minuto: in aritmetica esatta fa 24.
    accumulato = sum([0.1] * 240)
    assert accumulato != 24.0
    assert math.ceil(accumulato * geometry.TIME_SCALE) == 241  # senza epsilon
    assert geometry.to_units_up(accumulato) == 240  # con epsilon
    assert geometry.to_units_down(accumulato) == 240

    # Lo stesso dalla parte opposta: un valore appena sotto il multiplo esatto
    # non deve far perdere un'unita' di budget.
    appena_sotto = 24.0 - 4e-15
    assert appena_sotto != 24.0
    assert math.floor(appena_sotto * geometry.TIME_SCALE) == 239  # senza epsilon
    assert geometry.to_units_down(appena_sotto) == 240  # con epsilon


def test_haversine_su_un_grado_di_latitudine():
    """Controllo dimensionale: il risultato deve essere in chilometri.

    Un grado di latitudine vale circa 111.19 km su una sfera di raggio 6371
    km. Se qualcuno confondesse gradi e radianti, o metri e chilometri, qui
    salterebbe fuori un numero di ordine di grandezza diverso.
    """
    d = geometry.haversine_km(GeoPoint(lat=39.0, lon=16.0), GeoPoint(lat=40.0, lon=16.0))
    assert d == pytest.approx(111.19, abs=0.05)

    # Simmetrica e nulla su se stessa.
    a = GeoPoint(lat=38.9, lon=16.2)
    b = GeoPoint(lat=39.3, lon=16.6)
    assert geometry.haversine_km(a, b) == geometry.haversine_km(b, a)
    assert geometry.haversine_km(a, a) == 0.0

    # Agli antipodi non deve uscire NaN: e' il caso in cui `asin` riceve un
    # argomento appena sopra 1 per errore di arrotondamento.
    mezzo_giro = geometry.haversine_km(
        GeoPoint(lat=0.0, lon=0.0), GeoPoint(lat=0.0, lon=180.0)
    )
    assert mezzo_giro == pytest.approx(20015.1, abs=1.0)


def test_travel_minutes_converte_le_ore_in_minuti(elicottero: Vehicle):
    """Un grado di latitudine a 240 km/h sono circa 27.8 minuti, non 0.46."""
    a = GeoPoint(lat=39.0, lon=16.0)
    b = GeoPoint(lat=40.0, lon=16.0)
    atteso = geometry.haversine_km(a, b) / elicottero.cruise_speed_kmh * 60.0
    assert geometry.travel_minutes(elicottero, a, b) == pytest.approx(atteso)
    assert geometry.travel_minutes(elicottero, a, b) == pytest.approx(27.8, abs=0.1)


def test_velocita_nulla_non_divide_per_zero(elicottero: Vehicle):
    """Dato statico sporco: un mezzo a velocita' zero non deve far esplodere nulla."""
    a = GeoPoint(lat=39.0, lon=16.0)
    b = GeoPoint(lat=39.1, lon=16.0)
    fermo = copy.deepcopy(elicottero)
    fermo.cruise_speed_kmh = 0.0

    minuti = geometry.travel_minutes(fermo, a, b)
    assert minuti > 0.0
    assert minuti == pytest.approx(
        geometry.haversine_km(a, b) / geometry.MIN_SPEED_KMH * 60.0
    )


def test_il_ciclo_acqua_prende_il_rifornimento_piu_lento(
    elicottero: Vehicle, fire_sila: FireCluster, sea_source: WaterSource
):
    """Il collo di bottiglia comanda: non si fa la media fra mezzo e sorgente."""
    lento = copy.deepcopy(sea_source)
    lento.refill_minutes = 9.0  # la sorgente e' piu' lenta del mezzo (2.0)

    tratta = geometry.travel_minutes(elicottero, fire_sila.centroid, lento.point)
    atteso = 2.0 * tratta + 9.0 + elicottero.drop_minutes
    assert geometry.water_cycle_minutes(elicottero, fire_sila, lento) == pytest.approx(
        atteso
    )


def test_sorgente_al_limite_di_affidabilita_viene_tenuta(
    elicottero: Vehicle, fire_sila: FireCluster, sea_source: WaterSource
):
    """Il confronto e' `<`, quindi il valore di soglia esatto resta ammesso.

    E' il tipo di confine che si sbaglia scrivendo `<=` per distrazione, e che
    nessuno nota finche' un dato statico non capita esattamente sulla soglia.
    """
    limite = copy.deepcopy(sea_source)
    limite.source_id = "WS_LIMITE"
    limite.point = fire_sila.centroid
    limite.seasonal_reliability = geometry.MIN_SEASONAL_RELIABILITY

    scelta = geometry.best_water_source(elicottero, fire_sila, [limite])
    assert scelta is not None and scelta.source_id == "WS_LIMITE"

    sotto = copy.deepcopy(limite)
    sotto.seasonal_reliability = geometry.MIN_SEASONAL_RELIABILITY - 1e-9
    assert geometry.best_water_source(elicottero, fire_sila, [sotto]) is None


def test_sorgente_senza_restrizioni_e_aperta_a_tutti(
    modulo_aib: Vehicle, fire_sila: FireCluster, sea_source: WaterSource
):
    """`usable_by` vuoto vuol dire "nessuna restrizione dichiarata", non "nessuno".

    E' la lettura prudente dei dati statici incompleti: meglio un piano in
    piu' da far bocciare al validatore che un mezzo escluso da un campo non
    compilato.
    """
    chiusa = copy.deepcopy(sea_source)  # elenca solo i mezzi aerei
    assert geometry.best_water_source(modulo_aib, fire_sila, [chiusa]) is None

    aperta = copy.deepcopy(sea_source)
    aperta.usable_by = []
    assert geometry.best_water_source(modulo_aib, fire_sila, [aperta]) is not None


def test_a_parita_di_punteggio_vince_l_identificativo_minore(
    elicottero: Vehicle, fire_sila: FireCluster, sea_source: WaterSource
):
    """Il pareggio si rompe in modo deterministico, non con l'ordine di iterazione.

    Senza una regola di spareggio, due esecuzioni potrebbero scegliere
    sorgenti diverse a parita' di merito e il piano non sarebbe riproducibile.
    """
    a = copy.deepcopy(sea_source)
    a.source_id = "WS_BBB"
    b = copy.deepcopy(sea_source)
    b.source_id = "WS_AAA"  # stesso punto, stessi tempi: pareggio perfetto

    assert geometry.best_water_source(elicottero, fire_sila, [a, b]).source_id == "WS_AAA"
    assert geometry.best_water_source(elicottero, fire_sila, [b, a]).source_id == "WS_AAA"


def test_nessuna_sorgente_niente_profilo(
    elicottero: Vehicle, base_cosenza: Base, fire_sila: FireCluster
):
    """Un mezzo che ha bisogno d'acqua e non ne trova non riceve variabili."""
    assert elicottero.requires_water_source
    assert (
        geometry.build_mission_profile(elicottero, base_cosenza, fire_sila, [], 240)
        is None
    )


def test_mezzo_autonomo_senza_sorgente_fa_un_solo_sgancio(
    elicottero: Vehicle, base_cosenza: Base, fire_sila: FireCluster
):
    """Senza rifornimento c'e' solo l'acqua di bordo: un lancio e si torna."""
    autonomo = copy.deepcopy(elicottero)
    autonomo.requires_water_source = False

    profilo = geometry.build_mission_profile(autonomo, base_cosenza, fire_sila, [], 240)
    assert profilo is not None
    assert profilo.water_source_id is None
    assert profilo.max_sorties == 1


def test_mezzo_non_collocabile_non_ha_profilo(
    elicottero: Vehicle, fire_sila: FireCluster, sea_source: WaterSource
):
    """Senza base e senza posizione corrente non si sa da dove parte: si scarta."""
    orfano = copy.deepcopy(elicottero)
    orfano.current_position = None
    assert (
        geometry.build_mission_profile(orfano, None, fire_sila, [sea_source], 240) is None
    )

    # Con una posizione corrente e' collocabile anche senza base.
    orfano.current_position = GeoPoint(lat=39.30, lon=16.40)
    assert (
        geometry.build_mission_profile(orfano, None, fire_sila, [sea_source], 240)
        is not None
    )


def test_mezzo_libero_dopo_l_orizzonte_non_ha_profilo(
    elicottero: Vehicle,
    base_cosenza: Base,
    fire_sila: FireCluster,
    sea_source: WaterSource,
):
    """`available_from_minute` oltre l'orizzonte azzera il tempo utile."""
    tardi = copy.deepcopy(elicottero)
    tardi.available_from_minute = 300.0  # l'orizzonte ne dura 240
    assert (
        geometry.build_mission_profile(tardi, base_cosenza, fire_sila, [sea_source], 240)
        is None
    )


def test_mezzo_a_fine_turno_non_ha_profilo(
    elicottero: Vehicle,
    base_cosenza: Base,
    fire_sila: FireCluster,
    sea_source: WaterSource,
):
    """Turno esaurito: il budget residuo e' zero, non un numero negativo."""
    esausto = copy.deepcopy(elicottero)
    esausto.minutes_flown_today = esausto.max_shift_minutes
    assert (
        geometry.build_mission_profile(
            esausto, base_cosenza, fire_sila, [sea_source], 240
        )
        is None
    )

    # Anche oltre il turno, senza che il budget diventi negativo e si riapra.
    esausto.minutes_flown_today = esausto.max_shift_minutes + 120.0
    assert (
        geometry.build_mission_profile(
            esausto, base_cosenza, fire_sila, [sea_source], 240
        )
        is None
    )


def test_il_tetto_massimo_di_cicli_e_rispettato(instant: datetime):
    """Con cicli brevissimi il dominio di `sorties` resterebbe enorme.

    Il tetto non e' fisica, e' igiene del modello: serve a non far esplodere
    il dominio di una variabile intera quando i dati permetterebbero
    centinaia di cicli che nessuno pianificherebbe davvero.
    """
    punto = GeoPoint(lat=39.30, lon=16.30)
    base = Base(base_id="B0", name="base", point=punto, provincia="Cosenza")
    sorgente = WaterSource(
        source_id="WS0",
        name="a bordo fuoco",
        point=punto,
        kind=WaterSourceKind.LAKE,
        refill_minutes=0.1,
    )
    lampo = Vehicle(
        vehicle_id="V0",
        name="mezzo velocissimo",
        kind=VehicleKind.ELICOTTERO_LEGGERO,
        domain=Domain.AIR,
        home_base_id="B0",
        cruise_speed_kmh=250.0,
        water_capacity_liters=1000,
        drop_minutes=0.1,
        refill_minutes=0.1,
        setup_minutes=0.0,
        endurance_minutes=600.0,
        max_shift_minutes=600.0,
    )
    det = Detection(
        detection_id="Dx",
        point=punto,
        acquired_at=instant,
        satellite="N",
        instrument="VIIRS",
        confidence=80.0,
        frp_mw=5.0,
    )
    enorme = FireCluster(
        fire_id="F_ENORME",
        centroid=punto,
        detections=[det],
        water_demand_liters=10_000_000,
        base_priority=0.9,
    )

    profilo = geometry.build_mission_profile(lampo, base, enorme, [sorgente], 600)
    assert profilo is not None
    assert profilo.max_sorties == geometry.MAX_SORTIES_HARD_CAP


# ==========================================================================
# Casi limite: scenari degeneri
# ==========================================================================


def test_scenario_del_tutto_vuoto(instant: datetime):
    """Niente focolai e niente mezzi: un piano vuoto, non un'eccezione.

    E' il caso che arriva davvero nella rigiocata della stagione: molte
    finestre di tre ore non contengono nessun rilevamento.
    """
    vuoto = Scenario(scenario_id="SC_VUOTO", timestamp=instant, horizon_minutes=240)
    plan = solve(vuoto, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.assignments == []
    assert plan.unassigned_fire_ids == []
    assert plan.objective_value == 0.0


def test_un_solo_mezzo_un_solo_focolaio(
    instant: datetime, base_cosenza: Base, elicottero: Vehicle, lago_silano: WaterSource
):
    """Il caso da un elemento solo: nessuna scelta da fare, ma va fatta bene."""
    sc = Scenario(
        scenario_id="SC_UNO",
        timestamp=instant,
        horizon_minutes=240,
        fires=[_piccolo_focolaio("F_UNICO", 39.3000, 16.3000, instant, 3000)],
        vehicles=[copy.deepcopy(elicottero)],
        bases=[base_cosenza],
        water_sources=[lago_silano],
    )
    plan = solve(sc, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert len(plan.assignments) == 1
    assert plan.unassigned_fire_ids == []
    a = plan.assignments[0]
    assert a.vehicle_id == "ELI_01" and a.fire_id == "F_UNICO"
    assert a.water_delivered_liters >= 3000


def test_orizzonte_nullo_non_produce_missioni(scenario_minimo: Scenario):
    """Orizzonte a zero: non c'e' tempo per niente, e i focolai restano scoperti."""
    fermo = copy.deepcopy(scenario_minimo)
    fermo.horizon_minutes = 0

    mv = build_model(fermo)
    assert mv.horizon_units == 0
    assert mv.pairs == {}

    plan = solve(fermo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {"F_SILA", "F_WUI"}


def test_orizzonte_negativo_degrada_senza_esplodere(scenario_minimo: Scenario):
    """Dato incoerente: si risponde con un piano vuoto, non con un modello invalido.

    Un orizzonte negativo renderebbe negativo il limite superiore dei domini
    delle variabili di tempo, e CP-SAT si troverebbe domini vuoti. Il
    pavimento a zero in `build_model` trasforma un errore di costruzione in un
    caso degenere leggibile.
    """
    assurdo = copy.deepcopy(scenario_minimo)
    assurdo.horizon_minutes = -10

    mv = build_model(assurdo)
    assert mv.horizon_units == 0

    plan = solve(assurdo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.assignments == []


def test_scenario_senza_sorgenti_d_acqua(scenario_minimo: Scenario):
    """Senza pescaggi i mezzi che ne hanno bisogno non entrano nel modello."""
    asciutto = copy.deepcopy(scenario_minimo)
    asciutto.water_sources = []

    mv = build_model(asciutto)
    assert mv.pairs == {}
    assert any("Nessuna coppia" in n for n in mv.notes)

    plan = solve(asciutto, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {"F_SILA", "F_WUI"}


@pytest.mark.parametrize(
    "stato",
    [VehicleStatus.RESERVE, VehicleStatus.MAINTENANCE, VehicleStatus.OUT_OF_SHIFT],
)
def test_i_mezzi_non_disponibili_restano_fuori_dal_modello(
    scenario_minimo: Scenario, stato: VehicleStatus
):
    """RESERVE, MAINTENANCE e OUT_OF_SHIFT sono decisioni gia' prese altrove.

    Il mezzo resta nello scenario, cosi' la demo puo' mostrarlo fermo a terra,
    ma non riceve nessuna variabile: e' il modo piu' economico di escluderlo,
    perche' il solver non deve nemmeno dimostrare che non conviene usarlo.
    """
    sc = copy.deepcopy(scenario_minimo)
    sc.vehicle_by_id("CAN_01").status = stato

    assert not vehicle_is_employable(sc.vehicle_by_id("CAN_01"), sc)
    mv = build_model(sc)
    assert mv.pairs_for_vehicle("CAN_01") == []
    assert "CAN_01" not in mv.used

    plan = solve(sc, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert all(a.vehicle_id != "CAN_01" for a in plan.assignments)


def test_lo_stato_assigned_resta_impiegabile(scenario_minimo: Scenario):
    """ASSIGNED vuol dire "gia' su un incendio", non "non disponibile".

    Toglierlo significherebbe non poter mai ripianificare una flotta in
    attivita', che e' esattamente il caso d'uso della rigiocata a finestre.
    """
    sc = copy.deepcopy(scenario_minimo)
    sc.vehicle_by_id("CAN_01").status = VehicleStatus.ASSIGNED
    assert vehicle_is_employable(sc.vehicle_by_id("CAN_01"), sc)
    assert build_model(sc).pairs_for_vehicle("CAN_01")


def test_focolaio_senza_fabbisogno_e_coperto_da_un_intervento(
    instant: datetime, base_cosenza: Base, elicottero: Vehicle, lago_silano: WaterSource
):
    """Fabbisogno zero (arricchimento non girato): copre chi ci va, non chi versa.

    Senza questa convenzione un focolaio con fabbisogno nullo risulterebbe
    coperto senza mandare nessuno, e l'obiettivo non avrebbe nessun motivo di
    intervenire.
    """
    sc = Scenario(
        scenario_id="SC_SENZA_FABBISOGNO",
        timestamp=instant,
        horizon_minutes=240,
        fires=[_piccolo_focolaio("F_ZERO", 39.3000, 16.3000, instant, 0)],
        vehicles=[copy.deepcopy(elicottero)],
        bases=[base_cosenza],
        water_sources=[lago_silano],
    )
    plan = solve(sc, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert len(plan.assignments) == 1
    assert plan.unassigned_fire_ids == []
    assert plan.assignments[0].sorties >= 1


def test_le_note_segnalano_i_mezzi_senza_missioni_praticabili(scenario_minimo: Scenario):
    """Un mezzo impiegabile ma inutilizzabile deve dirlo, non sparire in silenzio.

    Nello scenario minimo il modulo a terra non puo' pescare in mare: e'
    impiegabile ma non ha nessuna coppia. Nel piano finale sarebbe
    indistinguibile da un mezzo che il solver ha scelto di non usare.
    """
    mv = build_model(scenario_minimo)
    assert mv.pairs_for_vehicle("AIB_01") == []
    assert any("AIB_01" in n and "senza nessuna missione" in n for n in mv.notes)


# ==========================================================================
# Casi limite: vincoli tradotti dall'ordine
# ==========================================================================


def _finestra_oraria(
    from_minute: int,
    to_minute: int,
    enforcement: Enforcement,
    *,
    constraint_id: str = "tw",
    source_span: str = "",
) -> ConstraintSet:
    """Costruisce un TIME_WINDOW, ricordando che i due numeri sono ORARI.

    `TimeWindow.from_minute` e `TimeWindow.to_minute` sono minuti dalla
    MEZZANOTTE, non minuti di piano: lo dice il docstring dello schema ("gli
    aerei fino alle otto"), lo conferma l'intervallo ammesso 0..1440, e sulla
    stessa lettura sono costruiti il traduttore a regole, la baseline e il
    validatore indipendente.

    Tutte le fixture di questi test partono alle 11:30, cioe' dal minuto 690
    del giorno, quindi la regola pratica per leggere i numeri qui sotto e':

        minuto di piano = minuto del giorno - 690

    Questa funzione esiste proprio per tenere il conto visibile. Scrivere a
    mano `from_minute=30` intendendo "il minuto 30 del piano" e' l'errore di
    unita' che questo sottosistema aveva gia' commesso una volta: il modello
    resta ben formato, il solver produce un piano, e solo il validatore
    scopre che le missioni cadono fuori dall'orario richiesto.
    """
    return constraint_set(
        TimeWindow(
            constraint_id=constraint_id,
            selector=Selector(field=SelectorField.ALL),
            from_minute=from_minute,
            to_minute=to_minute,
            enforcement=enforcement,
            source_span=source_span,
        )
    )


def _finestra_fuori_orizzonte(enforcement: Enforcement) -> ConstraintSet:
    """Finestra che cade tutta dopo la fine dell'orizzonte: il caso limite dello schema.

    20:00-23:20 su un piano che comincia alle 11:30 e dura quattro ore: in
    minuti di piano la finestra e' [510, 710], mentre l'orizzonte finisce al
    minuto 240. Nessuna missione puo' starci dentro.

    Il caso e' interessante perche' `TimeWindow` ammette orari fino a 1440
    mentre l'orizzonte ne dura 240: l'apertura convertita vale 5100 unita' di
    tempo, cioe' piu' del doppio dell'intero orizzonte (2400 unita'). E' il
    numero che faceva traboccare la variabile di penalita' nella regressione
    provata qui sotto.
    """
    return _finestra_oraria(
        1200,
        1400,
        enforcement,
        constraint_id="tw_fuori",
        source_span="fateli lavorare dopo le otto di sera",
    )


def test_una_finestra_temporale_morbida_non_vieta_mai_l_assegnazione(
    scenario_minimo: Scenario,
):
    """Regressione: un vincolo MORBIDO non puo' rendere IMPOSSIBILE un'assegnazione.

    Il bug: lo sforamento in entrata vale `from_minute - partenza`, cioe' fino
    a 6000 unita' con una finestra che comincia al minuto 600. La variabile
    che lo misura era dimensionata sull'orizzonte (2400 unita'), quindi non
    poteva rappresentarlo, e l'unico modo di soddisfare il vincolo restava
    `assign = 0`. Un vincolo morbido diventava cosi' un divieto.

    La prova e' strutturale e non sul piano finale, ed e' voluto: un piano
    vuoto puo' essere la risposta legittima di un obiettivo che ha preferito
    pagare zero penalita', e non distinguerebbe il bug dalla scelta. Qui si
    chiede al modello una cosa sola: "assegnare resta AMMISSIBILE?". Prima
    della correzione la risposta era no.
    """
    mv = build_model(scenario_minimo)
    assert mv.pairs, "presupposto del test: qualche coppia praticabile c'e'"

    optimize_model.apply_constraints(
        mv.model, mv, scenario_minimo, _finestra_fuori_orizzonte(Enforcement.SOFT)
    )

    prima_coppia = mv.pairs[sorted(mv.pairs)[0]]
    mv.model.Add(prima_coppia.assign == 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = TEST_MAX_SECONDS
    solver.parameters.num_search_workers = 1
    assert solver.Solve(mv.model) in (cp_model.OPTIMAL, cp_model.FEASIBLE), (
        "con un vincolo morbido l'assegnazione deve restare possibile, anche "
        "se cara: il morbido penalizza, non proibisce"
    )


def test_una_finestra_temporale_morbida_lascia_lavorare_quando_conviene(
    scenario_due_focolai_vicini: Scenario,
):
    """Controprova sul piano: se il merito supera la penalita', si lavora lo stesso.

    La finestra e' 11:30-11:50, cioe' i primi venti minuti di piano: troppo
    corta perche' una missione completa ci stia dentro, quindi ogni
    assegnazione la sfora e paga. Da DURA lascerebbe lavorare un mezzo solo
    (la prova qui sotto); da MORBIDA il termine di copertura, che vale piu'
    della penalita' parziale, convince il solver a pagare e a servire
    entrambi i focolai.

    Si e' scelta una violazione PARZIALE e non una finestra lontanissima
    perche' e' il caso che il compromesso lo mostra davvero: con una finestra
    fuori orizzonte la penalita' della finestra e quella del tempo di risposta
    crescono in direzioni opposte quasi allo stesso ritmo, il rilassamento
    lineare resta piatto e il solver impiega tutto il limite di tempo senza
    dimostrare l'ottimalita'. Un test che aspetta il limite e' un test lento e
    dipendente dalla macchina.
    """
    plan = solve(
        scenario_due_focolai_vicini,
        _finestra_oraria(690, 710, Enforcement.SOFT, constraint_id="tw_corta"),
        max_seconds=TEST_MAX_SECONDS,
        deterministic=True,
    )

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert len(plan.assignments) == 2, "un vincolo morbido non puo' vietare"
    assert plan.unassigned_fire_ids == []
    assert "rilassat" not in " ".join(plan.notes)
    # La violazione c'e' davvero: almeno una missione rientra dopo la
    # chiusura. Senza questo controllo il test passerebbe anche se la
    # finestra non avesse morso su niente.
    assert any(a.release_minute > 20 for a in plan.assignments)


def test_la_stessa_finestra_corta_da_dura_taglia_le_missioni(
    scenario_due_focolai_vicini: Scenario,
):
    """Il contraltare stretto del test precedente: stessa finestra, esito diverso.

    Da DURA la finestra 11:30-11:50 ammette solo le missioni che rientrano
    entro il minuto 20 di piano, e ce ne sta una sola. Il piano non e' vuoto e
    non e' infattibile: e' piu' corto, ed e' esattamente la differenza fra un
    ordine e una preferenza.
    """
    plan = solve(
        scenario_due_focolai_vicini,
        _finestra_oraria(690, 710, Enforcement.HARD, constraint_id="tw_corta"),
        max_seconds=TEST_MAX_SECONDS,
        deterministic=True,
    )

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert "rilassat" not in " ".join(plan.notes)
    assert plan.assignments, "una missione ci sta dentro"
    for a in plan.assignments:
        assert a.departure_minute >= 0.0
        assert a.release_minute <= 20.0


def test_una_finestra_temporale_dura_fuori_orizzonte_svuota_il_piano(
    scenario_minimo: Scenario,
):
    """Il contraltare: da DURO lo stesso ordine deve davvero fermare tutti.

    E non e' infattibilita': "nessuno lavora" resta una soluzione ammissibile
    del modello, quindi non si passa dal rilassamento.
    """
    plan = solve(
        scenario_minimo,
        _finestra_fuori_orizzonte(Enforcement.HARD),
        max_seconds=TEST_MAX_SECONDS,
        deterministic=True,
    )

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {"F_SILA", "F_WUI"}
    assert "rilassat" not in " ".join(plan.notes)


def test_finestra_temporale_dentro_l_orizzonte_sposta_le_partenze(
    scenario_due_focolai_vicini: Scenario,
):
    """Caso normale: la finestra sta dentro l'orizzonte e vincola i tempi.

    Il piano comincia alle 11:30 (minuto 690 del giorno) e dura quattro ore.
    La finestra 12:00-14:50, cioe' 720-890 minuti dalla mezzanotte, cade
    quindi sui minuti di piano da 30 a 200: dentro l'orizzonte, ma piu'
    stretta di lui da tutte e due le parti.

    I due numeri attesi (30 e 200) sono ricalcolati nel test invece che
    scritti a mano, cosi' la prova fallirebbe anche se qualcuno cambiasse
    l'ora della fixture senza aggiornare il resto.
    """
    inizio_giorno = (
        scenario_due_focolai_vicini.timestamp.hour * 60
        + scenario_due_focolai_vicini.timestamp.minute
    )
    apertura, chiusura = 720, 890
    apertura_piano = apertura - inizio_giorno
    chiusura_piano = chiusura - inizio_giorno
    assert (apertura_piano, chiusura_piano) == (30, 200), (
        "presupposto del test: la finestra cade dentro l'orizzonte"
    )

    plan = solve(
        scenario_due_focolai_vicini,
        _finestra_oraria(apertura, chiusura, Enforcement.HARD, constraint_id="tw_ok"),
        max_seconds=TEST_MAX_SECONDS,
        deterministic=True,
    )
    assert plan.assignments
    assert "rilassat" not in " ".join(plan.notes)
    for a in plan.assignments:
        assert a.departure_minute >= apertura_piano
        assert a.release_minute <= chiusura_piano


def test_tempo_di_risposta_impossibile_non_assegna_nessuno(scenario_minimo: Scenario):
    """Valore al limite inferiore dell'intervallo: un minuto non basta a nessuno.

    Ogni mezzo dello scenario ha un tempo di preparazione di almeno sei
    minuti, quindi il tetto non e' raggiungibile. Il piano corretto e' vuoto,
    non infattibile.
    """
    cs = constraint_set(
        MaxResponseTime(
            constraint_id="mrt_uno", minutes=1, enforcement=Enforcement.HARD
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.assignments == []


def test_un_selettore_che_non_pesca_nulla_e_dichiarato_inapplicabile(
    scenario_due_focolai_vicini: Scenario,
):
    """Un ordine su mezzi che qui non esistono non e' un errore, ma va detto.

    Il coordinatore ha diritto di sapere che quella parte dell'ordine non ha
    toccato niente: senza la nota, "escludete i Canadair" sembrerebbe
    applicato anche in una provincia dove di Canadair non ce n'e' nessuno.
    """
    cs = constraint_set(
        ExcludeVehicle(
            constraint_id="ev_fantasma",
            selector=Selector(field=SelectorField.VEHICLE_KIND, values=["canadair"]),
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(
        scenario_due_focolai_vicini,
        cs,
        max_seconds=TEST_MAX_SECONDS,
        deterministic=True,
    )

    assert plan.assignments, "un ordine inapplicabile non blocca la pianificazione"
    assert any("ev_fantasma" in n and "non applicabile" in n for n in plan.notes)
    # E soprattutto: non si spaccia per un ordine rilassato.
    assert "rilassat" not in " ".join(plan.notes)


def test_riserva_con_ora_e_numero_dichiara_quale_lettura_ha_applicato(
    scenario_minimo: Scenario,
):
    """Lo schema permette entrambi i campi, il modello ne applica uno solo.

    Scartare in silenzio meta' di un ordine e' il modo piu' rapido di perdere
    la fiducia di chi lo ha dato. Se si scarta, si scrive.
    """
    cs = constraint_set(
        ReserveVehicle(
            constraint_id="rv_doppio",
            selector=by_id(["CAN_01"]),
            count=1,
            until_minute=60,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert any("rv_doppio" in n and "ignorato" in n for n in plan.notes)
    for a in plan.assignments_for_vehicle("CAN_01"):
        assert a.departure_minute >= 60


def test_riserva_fino_a_un_ora_ritarda_la_partenza(scenario_minimo: Scenario):
    """La lettura "non muove prima delle undici", da sola."""
    cs = constraint_set(
        ReserveVehicle(
            constraint_id="rv_ora",
            selector=Selector(field=SelectorField.ALL),
            until_minute=45,
            enforcement=Enforcement.HARD,
            source_span="nessuno si muove prima delle undici e tre quarti",
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.assignments
    for a in plan.assignments:
        assert a.departure_minute >= 45


def test_riserva_di_n_mezzi_lascia_liberi_gli_altri(scenario_minimo: Scenario):
    """"Tenete un mezzo aereo di riserva": uno fermo, l'altro puo' lavorare."""
    cs = constraint_set(
        ReserveVehicle(
            constraint_id="rv_conta",
            selector=Selector(field=SelectorField.VEHICLE_DOMAIN, values=["air"]),
            count=1,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    aerei_usati = {
        a.vehicle_id
        for a in plan.assignments
        if scenario_minimo.vehicle_by_id(a.vehicle_id).domain is Domain.AIR
    }
    assert len(aerei_usati) <= 1


def test_quota_di_tipo_al_limite_zero(scenario_minimo: Scenario):
    """`max_used = 0` e' il minimo dell'intervallo e vale "quel tipo non si usa"."""
    cs = constraint_set(
        VehicleTypeQuota(
            constraint_id="vq_zero",
            selector=Selector(field=SelectorField.VEHICLE_KIND, values=["canadair"]),
            max_used=0,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert all(a.vehicle_id != "CAN_01" for a in plan.assignments)
    assert plan.assignments, "gli altri tipi devono continuare a lavorare"


def test_quota_di_tipo_minima(scenario_minimo: Scenario):
    """Il bordo opposto: almeno un mezzo aereo deve essere impiegato."""
    cs = constraint_set(
        VehicleTypeQuota(
            constraint_id="vq_min",
            selector=Selector(field=SelectorField.VEHICLE_DOMAIN, values=["air"]),
            min_used=1,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    aerei = {
        a.vehicle_id
        for a in plan.assignments
        if scenario_minimo.vehicle_by_id(a.vehicle_id).domain is Domain.AIR
    }
    assert len(aerei) >= 1


def test_prioritize_zone_per_raggio(scenario_minimo: Scenario):
    """La zona puo' essere un cerchio su una coppia di coordinate."""
    wui = scenario_minimo.fire_by_id("F_WUI")
    cs = constraint_set(
        PrioritizeZone(
            constraint_id="pz_raggio",
            zone_kind=ZoneKind.RADIUS,
            center_lat=wui.centroid.lat,
            center_lon=wui.centroid.lon,
            radius_km=5.0,
            weight_multiplier=5.0,
            enforcement=Enforcement.SOFT,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    # Il cerchio contiene solo il focolaio in interfaccia: l'altro sta a
    # centocinquanta chilometri.
    assert any("su 1 focolai" in n for n in plan.notes)
    assert liters_on(plan, "F_WUI") > 0


def test_protect_infrastructure_senza_coordinate_non_si_applica(
    scenario_minimo: Scenario,
):
    """Senza latitudine e longitudine non si sa quali focolai siano vicini.

    Il campo e' opzionale nello schema perche' il traduttore puo' riconoscere
    il nome di un impianto senza saperlo geolocalizzare. Qui la risposta
    corretta e' astenersi e dirlo, non inventare un punto.
    """
    cs = constraint_set(
        ProtectInfrastructure(
            constraint_id="pi_senza_punto",
            name="Centrale di Altomonte",
            enforcement=Enforcement.SOFT,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert any(
        "Centrale di Altomonte" in n and "senza coordinate" in n for n in plan.notes
    )
    assert any("pi_senza_punto" in n and "non applicabile" in n for n in plan.notes)


def test_protect_infrastructure_con_coordinate_alza_la_priorita(
    scenario_minimo: Scenario,
):
    """Con il punto, il vincolo alza il peso dei focolai nel raggio dichiarato."""
    sila = scenario_minimo.fire_by_id("F_SILA")
    cs = constraint_set(
        ProtectInfrastructure(
            constraint_id="pi_con_punto",
            name="Ripetitore del Monte Botte Donato",
            lat=sila.centroid.lat,
            lon=sila.centroid.lon,
            buffer_km=10.0,
            weight_multiplier=4.0,
            enforcement=Enforcement.SOFT,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert any("Ripetitore" in n and "priorita' alzata" in n for n in plan.notes)


# ==========================================================================
# Contratto con il resto del progetto
# ==========================================================================


def test_il_solver_non_muta_lo_scenario(scenario_minimo: Scenario):
    """Lo scenario e' condiviso fra solver, baseline e validatore.

    Se il solver lo modificasse anche solo in un campo, il confronto della
    valutazione misurerebbe due cose diverse presentandole come la stessa. E'
    la classe di errore piu' difficile da vedere, perche' il piano resta
    plausibile.
    """
    prima = copy.deepcopy(scenario_minimo)
    solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert scenario_minimo.horizon_minutes == prima.horizon_minutes
    assert scenario_minimo.is_night == prima.is_night
    for dopo_v, prima_v in zip(scenario_minimo.vehicles, prima.vehicles):
        assert dopo_v.status == prima_v.status
        assert dopo_v.available_from_minute == prima_v.available_from_minute
        assert dopo_v.minutes_flown_today == prima_v.minutes_flown_today
        assert dopo_v.current_position == prima_v.current_position
    for dopo_f, prima_f in zip(scenario_minimo.fires, prima.fires):
        assert dopo_f.water_demand_liters == prima_f.water_demand_liters
        assert dopo_f.base_priority == prima_f.base_priority


def test_i_pesi_di_default_vengono_da_config_e_non_vengono_riscritti(
    scenario_minimo: Scenario,
):
    """I pesi si leggono da `config.OBJECTIVE_WEIGHTS`, su una copia.

    Un `OBJECTIVE_WEIGHT` riscrive il peso per QUESTA pianificazione. Se
    scrivesse sul dizionario di config, il peso resterebbe cambiato per tutte
    le pianificazioni successive dello stesso processo: un piano
    influenzerebbe il successivo, e il determinismo fra esecuzioni sarebbe
    finito.
    """
    originale = dict(OBJECTIVE_WEIGHTS)

    mv = build_model(scenario_minimo)
    assert mv.weights == originale
    assert mv.weights is not OBJECTIVE_WEIGHTS

    cs = constraint_set(
        ObjectiveWeight(
            constraint_id="ow_costo",
            term=ObjectiveTerm.COST,
            weight=42.0,
            enforcement=Enforcement.SOFT,
        )
    )
    solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert OBJECTIVE_WEIGHTS == originale


def test_optimize_non_importa_dal_validatore():
    """Il confine dichiarato in SPEC.md, verificato dentro il sottosistema.

    `tests/test_architettura.py` lo controlla gia' per tutto il repository.
    Questa copia locale esiste perche' la regola riguarda in primo luogo chi
    lavora qui: se qualcuno fosse tentato di riusare le formule del
    validatore, la prova deve fallire nel file che sta modificando.
    """
    pacchetto = Path(optimize_model.__file__).parent
    vietato = re.compile(r"(?:from|import)\s+tagliafuoco\.validate")
    colpevoli = [
        f.name
        for f in sorted(pacchetto.glob("*.py"))
        if vietato.search(f.read_text(encoding="utf-8"))
    ]
    assert colpevoli == [], (
        "optimize/ importa da validate/: le due implementazioni della fisica "
        "devono restare separate, altrimenti il validatore eredita l'errore "
        "che dovrebbe scoprire. File colpevoli: " + ", ".join(colpevoli)
    )


def test_il_ripiego_locale_dei_selettori_concorda_con_il_resolver_nlu(
    scenario_minimo: Scenario, monkeypatch: pytest.MonkeyPatch
):
    """Le due strade di risoluzione devono dare la stessa risposta.

    `optimize/` usa il resolver ufficiale di `nlu/` quando c'e', e ricade su
    una risoluzione locale scritta sullo schema chiuso di `Selector` quando
    manca. Il ripiego serve perche' i sottosistemi si scrivono in parallelo,
    ma se le due strade divergessero il piano dipenderebbe da quale modulo e'
    installato: un non determinismo travestito da robustezza.
    """
    selettori_mezzi = [
        Selector(field=SelectorField.ALL),
        Selector(field=SelectorField.VEHICLE_ID, values=["CAN_01"]),
        Selector(field=SelectorField.VEHICLE_KIND, values=["elicottero_medio"]),
        Selector(field=SelectorField.VEHICLE_DOMAIN, values=["ground"]),
        Selector(field=SelectorField.BASE_ID, values=["BASE_CS"]),
        Selector(field=SelectorField.VEHICLE_KIND, values=["autobotte"]),
    ]
    selettori_focolai = [
        Selector(field=SelectorField.ALL),
        Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
        Selector(field=SelectorField.FIRE_COMUNE, values=["Reggio Calabria"]),
        Selector(field=SelectorField.FIRE_PROVINCIA, values=["Cosenza"]),
        Selector(field=SelectorField.FIRE_SEVERITY, values=["high"]),
        Selector(field=SelectorField.FIRE_IS_WUI, values=["true"]),
        Selector(field=SelectorField.FIRE_IS_WUI, values=["false"]),
        Selector(field=SelectorField.FIRE_IN_PROTECTED_AREA, values=["true"]),
    ]

    con_resolver = (
        [optimize_model.resolve_vehicle_ids(s, scenario_minimo) for s in selettori_mezzi],
        [optimize_model.resolve_fire_ids(s, scenario_minimo) for s in selettori_focolai],
    )

    # Si finge che `nlu/resolver.py` non ci sia e si rifa' tutto col ripiego.
    monkeypatch.setattr(optimize_model, "_nlu_resolver", lambda: None)
    col_ripiego = (
        [optimize_model.resolve_vehicle_ids(s, scenario_minimo) for s in selettori_mezzi],
        [optimize_model.resolve_fire_ids(s, scenario_minimo) for s in selettori_focolai],
    )

    assert con_resolver == col_ripiego


def test_selettore_assente_significa_tutti(scenario_minimo: Scenario):
    """`None` non e' "nessuno": e' "non ho ristretto niente".

    Lo usa MIN_VEHICLES_ON_FIRE, dove `vehicle_selector` e' opzionale e
    assente vuol dire "due mezzi qualsiasi".
    """
    assert optimize_model.resolve_vehicle_ids(None, scenario_minimo) == [
        v.vehicle_id for v in scenario_minimo.vehicles
    ]
    assert optimize_model.resolve_fire_ids(None, scenario_minimo) == [
        f.fire_id for f in scenario_minimo.fires
    ]


# ==========================================================================
# Coerenza fra il piano scritto e l'obiettivo ottimizzato
# ==========================================================================


def test_i_litri_dichiarati_corrispondono_ai_cicli(scenario_minimo: Scenario):
    """Ogni ciclo versa una capacita' piena: il piano non puo' dire altro."""
    plan = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert plan.assignments

    for a in plan.assignments:
        capacita = scenario_minimo.vehicle_by_id(a.vehicle_id).water_capacity_liters
        assert a.water_delivered_liters == a.sorties * capacita


def test_il_costo_di_attivazione_si_paga_una_volta_per_mezzo(
    scenario_due_focolai_vicini: Scenario,
):
    """Due missioni dello stesso mezzo, un solo costo fisso di messa in moto.

    Se si pagasse a ogni missione, il piano dichiarerebbe una spesa diversa da
    quella che l'obiettivo ha minimizzato, e il confronto economico con la
    baseline sarebbe falsato.
    """
    plan = solve(
        scenario_due_focolai_vicini, max_seconds=TEST_MAX_SECONDS, deterministic=True
    )
    missioni = plan.assignments_for_vehicle("ELI_01")
    assert len(missioni) == 2

    mezzo = scenario_due_focolai_vicini.vehicle_by_id("ELI_01")
    prima, dopo = missioni

    # La seconda missione paga solo le ore di impegno.
    attesa_dopo = assignment_cost_eur(mezzo.cost_per_hour_eur, dopo.engaged_minutes)
    assert dopo.estimated_cost_eur == pytest.approx(attesa_dopo, abs=0.01)

    # La prima paga anche l'attivazione, una volta sola.
    attesa_prima = assignment_cost_eur(
        mezzo.cost_per_hour_eur, prima.engaged_minutes
    ) + activation_cost_eur(mezzo.cost_per_hour_eur)
    assert prima.estimated_cost_eur == pytest.approx(attesa_prima, abs=0.02)
    assert activation_cost_eur(mezzo.cost_per_hour_eur) > 0.0


def test_le_assegnazioni_escono_in_ordine_stabile(scenario_minimo: Scenario):
    """Ordinate per (partenza, mezzo, focolaio): confrontabili riga per riga."""
    plan = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    chiavi = [(a.departure_minute, a.vehicle_id, a.fire_id) for a in plan.assignments]
    assert chiavi == sorted(chiavi)


def test_i_termini_dell_obiettivo_esistono_tutti(scenario_minimo: Scenario):
    """Ogni termine dichiarato deve essere costruito, anche quando vale zero.

    Serve come rete contro le regressioni silenziose: un termine che sparisce
    dall'obiettivo non rompe niente, cambia solo i piani, e ce ne si accorge
    mesi dopo confrontando due valutazioni.
    """
    mv = build_model(scenario_minimo)
    terms = build_terms(mv)

    assert set(terms.details) == {
        "total_priority",
        "wui_fires",
        "water_denominator_liters",
        "cost_reference_eur",
        "penalty_terms",
    }
    assert float(terms.details["total_priority"]) > 0
    assert terms.details["wui_fires"] == "1"
    assert float(terms.details["cost_reference_eur"]) > 0


def test_senza_focolai_l_obiettivo_resta_costruibile(scenario_minimo: Scenario):
    """Denominatori a zero: i termini devono annullarsi, non dividere per zero."""
    vuoto = copy.deepcopy(scenario_minimo)
    vuoto.fires = []

    terms = build_terms(build_model(vuoto))
    assert terms.coverage == 0
    assert terms.wui_protection == 0
    assert terms.response_time == 0
    assert terms.water_fulfilment == 0
    assert float(terms.details["total_priority"]) == 0.0


# ==========================================================================
# Casi limite: orari del giorno contro minuti di piano
# ==========================================================================
#
# Questa sezione esiste per un errore vero, gia' commesso e gia' corretto in
# questo sottosistema: `TimeWindow.from_minute` e `TimeWindow.to_minute` erano
# stati letti come minuti di piano invece che come minuti dalla mezzanotte.
# E' l'errore di unita' di misura tipico, quello che non fa fallire niente:
# il modello resta ben formato, il solver risponde OPTIMAL, il piano sembra
# ragionevole, e solo il validatore indipendente (che legge quei campi come
# orari, insieme al traduttore a regole e alla baseline) scopre che le
# missioni cadono fuori dall'orario ordinato.
#
# Le prove qui sotto non passano dal solver: guardano l'aritmetica da sola,
# perche' e' li' che l'errore vive.


def test_i_minuti_dalla_mezzanotte_si_leggono_senza_toccare_il_fuso():
    """Stessa ora dell'orologio, stesso numero: con fuso, senza fuso, con altro fuso.

    La conversione legge i campi ora/minuto/secondo cosi' come sono scritti e
    non converte mai un fuso orario. Due ragioni, ed entrambe contano piu'
    della precisione astronomica.

    La prima e' che convertire significherebbe inventarsi un fuso: quale sia
    l'ora "vera" in Calabria dipende da come la pipeline ha costruito
    l'istante, non da qualcosa che il solver possa dedurre.

    La seconda e' che leggendo i campi non si confronta mai un `datetime` con
    un altro, quindi non esiste il punto in cui il programma possa esplodere
    con `TypeError: can't compare offset-naive and offset-aware datetimes`. E'
    il classico errore che compare in produzione mesi dopo, quando arriva il
    primo scenario costruito da una sorgente diversa.
    """
    naive = datetime(2025, 8, 12, 11, 30)
    utc = datetime(2025, 8, 12, 11, 30, tzinfo=timezone.utc)
    altro_fuso = datetime(2025, 8, 12, 11, 30, tzinfo=timezone(timedelta(hours=2)))

    assert geometry.minutes_from_midnight(naive) == 690.0
    assert geometry.minutes_from_midnight(utc) == 690.0
    assert geometry.minutes_from_midnight(altro_fuso) == 690.0

    # I secondi contano, i microsecondi no: l'unita' di tempo del modello e'
    # il decimo di minuto, sotto quella soglia non si pianifica niente.
    assert geometry.minutes_from_midnight(
        datetime(2025, 8, 12, 0, 0, 30)
    ) == pytest.approx(0.5)


def test_la_finestra_oraria_diventa_minuti_di_piano(instant: datetime):
    """La conversione da orologio a piano, caso per caso.

    `instant` e' le 11:30, cioe' il minuto 690 del giorno. La regola e'
    `minuto di piano = minuto del giorno - 690`, con due aggiustamenti: una
    finestra gia' aperta si taglia allo zero (il piano non puo' cominciare
    prima del proprio inizio) e una finestra gia' chiusa si cerca nella
    giornata successiva, perche' l'orario si ripete ogni ventiquattro ore.
    """
    # Finestra tutta dentro l'orizzonte: 12:00-14:50 -> minuti 30-200.
    assert geometry.daily_window_to_plan_minutes(instant, 720, 890, 240) == (30.0, 200.0)

    # Finestra gia' aperta quando il piano comincia: l'apertura si taglia a
    # zero invece di restare negativa, altrimenti il vincolo `departure >= lo`
    # diventerebbe un limite inferiore negativo e non vorrebbe dire niente.
    assert geometry.daily_window_to_plan_minutes(instant, 600, 900, 240) == (0.0, 210.0)

    # Finestra tutta dopo la fine dell'orizzonte: resta fuori, e il solver
    # dovra' concludere che nessuno puo' lavorare.
    assert geometry.daily_window_to_plan_minutes(instant, 1200, 1400, 240) == (
        510.0,
        710.0,
    )

    # Finestra gia' chiusa (00:00-01:00 su un piano che parte alle 11:30): si
    # prende l'occorrenza del giorno dopo, non si restituisce un intervallo a
    # rovescio. Cade comunque fuori dall'orizzonte di quattro ore.
    assert geometry.daily_window_to_plan_minutes(instant, 0, 60, 240) == (750.0, 810.0)


def test_la_finestra_oraria_e_una_identita_a_mezzanotte():
    """Caso degenere utile: se il piano comincia a mezzanotte le due scale coincidono.

    E' il controllo che smaschera una conversione scritta con il segno
    sbagliato: con `scenario.timestamp` a mezzanotte, orari e minuti di piano
    sono lo stesso numero, quindi una formula che sottraesse invece di
    sommare, o viceversa, qui darebbe ancora il risultato giusto solo se e'
    davvero una differenza.
    """
    mezzanotte = datetime(2025, 8, 12, 0, 0, tzinfo=timezone.utc)
    assert geometry.daily_window_to_plan_minutes(mezzanotte, 30, 200, 240) == (
        30.0,
        200.0,
    )
    assert geometry.daily_window_to_plan_minutes(mezzanotte, 0, 1440, 240) == (
        0.0,
        1440.0,
    )


def test_lo_stesso_scenario_con_istante_naive_o_con_fuso_da_lo_stesso_piano(
    scenario_due_focolai_vicini: Scenario,
):
    """Il ponte fra le due scale passa da `Scenario.timestamp`: non deve tremare.

    E' la prova di sistema che accompagna le tre prove aritmetiche qui sopra:
    lo stesso scenario, con lo stesso orario dell'orologio scritto in tre modi
    diversi, deve produrre lo stesso identico piano.
    """
    con_fuso = scenario_due_focolai_vicini
    naive = copy.deepcopy(con_fuso)
    naive.timestamp = con_fuso.timestamp.replace(tzinfo=None)
    altro_fuso = copy.deepcopy(con_fuso)
    altro_fuso.timestamp = con_fuso.timestamp.replace(
        tzinfo=timezone(timedelta(hours=2))
    )

    piani = [
        solve(
            sc,
            _finestra_oraria(720, 890, Enforcement.HARD, constraint_id="tw_fuso"),
            max_seconds=TEST_MAX_SECONDS,
            deterministic=True,
        )
        for sc in (con_fuso, naive, altro_fuso)
    ]

    assert piani[0].assignments, "presupposto: con quella finestra si lavora"
    assert fingerprint(piani[0]) == fingerprint(piani[1]) == fingerprint(piani[2])


# ==========================================================================
# Casi limite: aritmetica delle conversioni
# ==========================================================================


def test_le_conversioni_reggono_lo_zero_e_i_valori_negativi():
    """Zero e valori negativi non devono produrre arrotondamenti a sorpresa.

    Zero e' il valore che arriva davvero: un mezzo con `setup_minutes = 0`,
    un orizzonte azzerato, una finestra che apre al minuto zero. I negativi
    arrivano invece dai calcoli intermedi (turno residuo, orizzonte residuo) e
    non hanno senso operativo, ma devono restare coerenti: chi li produce
    applica un pavimento a zero a valle, e lo puo' fare solo se qui il segno
    e' prevedibile.
    """
    assert geometry.to_units_up(0.0) == 0
    assert geometry.to_units_down(0.0) == 0
    assert geometry.from_units(0) == 0.0

    # Entrambe si allontanano da zero nella propria direzione, anche sotto
    # zero: `up` verso l'alto, `down` verso il basso.
    assert geometry.to_units_up(-1.25) == -12
    assert geometry.to_units_down(-1.25) == -13
    assert geometry.to_units_up(-3.0) == -30
    assert geometry.to_units_down(-3.0) == -30


def test_il_ciclo_acqua_con_la_sorgente_sul_fuoco_e_solo_tempo_fermo(
    elicottero: Vehicle, fire_sila: FireCluster, sea_source: WaterSource
):
    """Caso limite geometrico: distanza zero, quindi nessun volo nel ciclo.

    Succede davvero con un incendio sulla costa e il mare a ridosso. Il ciclo
    si riduce ai due tempi fermi (pescaggio e sgancio) e non deve valere zero,
    altrimenti il calcolo del tetto sui cicli dividerebbe per zero.
    """
    addosso = copy.deepcopy(sea_source)
    addosso.point = fire_sila.centroid

    ciclo = geometry.water_cycle_minutes(elicottero, fire_sila, addosso)
    atteso = (
        max(elicottero.refill_minutes, addosso.refill_minutes)
        + elicottero.drop_minutes
    )
    assert ciclo == pytest.approx(atteso)
    assert ciclo > 0.0


def test_nessuna_sorgente_nell_elenco_vuoto(elicottero: Vehicle, fire_sila: FireCluster):
    """Input vuoto: `best_water_source` su una lista senza elementi da' None.

    E' il caso dello scenario non arricchito, dove i dati statici non sono
    stati caricati. Deve tornare None e non sollevare un `min()` su sequenza
    vuota. Si prova anche con un iteratore, perche' la firma accetta un
    `Iterable` e un iteratore esaurito e' il modo piu' silenzioso di avere
    zero elementi.
    """
    assert geometry.best_water_source(elicottero, fire_sila, []) is None
    assert geometry.best_water_source(elicottero, fire_sila, iter([])) is None


def test_la_soglia_di_ritardo_e_una_sola_per_modello_e_obiettivo():
    """Il modello e l'obiettivo devono usare LO STESSO numero, non due uguali.

    `lateness` e' definita in `model._build_pair` come lo sforamento oltre la
    soglia, e `objective.build_terms` normalizza quel ritardo sullo spazio che
    resta fra la soglia e la fine dell'orizzonte. Se i due punti convertissero
    la soglia in direzioni diverse (uno per eccesso e uno per difetto), il
    denominatore non corrisponderebbe piu' al massimo valore della variabile e
    il termine di ritardo potrebbe superare il proprio peso.

    Con un valore intero in `config` le due direzioni danno lo stesso numero,
    e questo e' precisamente il motivo per cui la costante va definita una
    volta sola: un errore che oggi non si vede resterebbe invisibile finche'
    qualcuno non scrivesse `LATE_RESPONSE_MINUTES = 45.5`.
    """
    assert optimize_model.LATE_THRESHOLD_UNITS == geometry.to_units_up(
        LATE_RESPONSE_MINUTES
    )
    assert objective_module.LATE_THRESHOLD_UNITS == optimize_model.LATE_THRESHOLD_UNITS


def test_il_ritardo_si_conta_solo_oltre_la_soglia(scenario_due_focolai_vicini: Scenario):
    """Prova strutturale sulla variabile: alla soglia zero, oltre la soglia lo scarto.

    Non si guarda il piano finale ma la variabile, perche' e' li' che vive la
    definizione: un piano dove nessuno arriva tardi non distinguerebbe una
    soglia giusta da una soglia sbagliata.
    """
    soglia = optimize_model.LATE_THRESHOLD_UNITS

    def ritardo_minimo(arrivo_units: int) -> int:
        mv = build_model(scenario_due_focolai_vicini)
        p = mv.pairs[sorted(mv.pairs)[0]]
        mv.model.Add(p.assign == 1)
        mv.model.Add(p.arrival == arrivo_units)
        mv.model.Minimize(p.lateness)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = TEST_MAX_SECONDS
        solver.parameters.num_search_workers = 1
        stato = solver.Solve(mv.model)
        assert stato in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        return int(solver.Value(p.lateness))

    assert ritardo_minimo(soglia) == 0
    assert ritardo_minimo(soglia - 100) == 0
    assert ritardo_minimo(soglia + 250) == 250


def test_il_coefficiente_non_evapora_su_denominatori_enormi():
    """Un criterio con denominatore gigantesco deve pesare poco, non sparire.

    Si prova direttamente la funzione privata perche' e' l'unico punto in cui
    il difetto e' osservabile: dall'esterno un termine con coefficiente zero e
    uno con coefficiente uno producono due espressioni CP-SAT indistinguibili
    a occhio, e la differenza si vede solo nei piani, mesi dopo, come un
    criterio che "non conta mai".

    Il caso realistico e' il fabbisogno idrico di un incendio grande: milioni
    di litri al denominatore, e il coefficiente esatto scende sotto mezzo
    punto.
    """
    assert _coeff(3.0, 1.0, 10_000_000.0) == 1, "il criterio pesa poco, non sparisce"

    # I casi davvero nulli restano nulli: peso zero, quota zero, denominatore
    # non positivo. Qui il pavimento a uno NON deve scattare, altrimenti un
    # criterio che il coordinatore ha azzerato tornerebbe in vita.
    assert _coeff(0.0, 1.0, 10.0) == 0
    assert _coeff(3.0, 0.0, 10.0) == 0
    assert _coeff(3.0, 1.0, 0.0) == 0
    assert _coeff(3.0, 1.0, -5.0) == 0


# ==========================================================================
# Casi limite: dati dello scenario malformati o al bordo
# ==========================================================================


def test_scenario_senza_basi_non_colloca_nessun_mezzo(scenario_minimo: Scenario):
    """Senza basi e senza posizione corrente non si sa da dove parte nessuno.

    Succede con i dati statici non caricati. La risposta giusta e' un piano
    vuoto con la nota che spiega perche', non un piano che parte da un punto
    inventato.
    """
    senza_basi = copy.deepcopy(scenario_minimo)
    senza_basi.bases = []

    mv = build_model(senza_basi)
    assert mv.pairs == {}
    assert any("Nessuna coppia" in n for n in mv.notes)

    plan = solve(senza_basi, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {"F_SILA", "F_WUI"}


def test_la_posizione_corrente_ha_la_precedenza_sulla_base(
    elicottero: Vehicle,
    base_cosenza: Base,
    fire_sila: FireCluster,
    sea_source: WaterSource,
):
    """Un mezzo gia' in volo parte da dove si trova, non dalla base.

    Serve alla simulazione a finestre: fra una finestra e l'altra il mezzo si
    e' spostato, e ripianificare dalla base gli attribuirebbe un tempo di volo
    che non deve fare.

    Senza base nello scenario il riattrezzamento vale zero, e non e' una
    scorciatoia: `turnaround_minutes` e' una proprieta' della BASE (rifornire,
    cambiare equipaggio, fare i controlli si fa a terra) e senza base quel
    dato non e' conoscibile. Inventarlo sarebbe peggio.
    """
    in_volo = copy.deepcopy(elicottero)
    in_volo.current_position = GeoPoint(lat=39.3100, lon=16.5500)  # a ridosso del fuoco

    vicino = geometry.build_mission_profile(
        in_volo, base_cosenza, fire_sila, [sea_source], 240
    )
    da_base = geometry.build_mission_profile(
        elicottero, base_cosenza, fire_sila, [sea_source], 240
    )
    assert vicino is not None and da_base is not None
    assert vicino.outbound_minutes < da_base.outbound_minutes

    # Il riattrezzamento resta quello della base finche' la base c'e'.
    assert vicino.turnaround_units == geometry.to_units_up(
        base_cosenza.turnaround_minutes
    )

    senza_base = geometry.build_mission_profile(
        in_volo, None, fire_sila, [sea_source], 240
    )
    assert senza_base is not None
    assert senza_base.turnaround_units == 0


def test_identificativi_di_focolaio_ripetuti_finiscono_nelle_note(
    scenario_minimo: Scenario,
):
    """Due focolai con lo stesso identificativo non rompono niente, ed e' il problema.

    Le chiavi del modello sono `(vehicle_id, fire_id)` e i dizionari per
    focolaio sono indicizzati sul solo `fire_id`: due omonimi si sovrascrivono
    a vicenda, il solver risponde OPTIMAL e il piano dichiara una copertura
    che non c'e'. Il modello non puo' aggiustare il dato (gli identificativi
    li assegna `ingest/`), ma puo' e deve dirlo.
    """
    doppio = copy.deepcopy(scenario_minimo)
    gemello = copy.deepcopy(doppio.fires[0])
    gemello.centroid = GeoPoint(lat=39.0, lon=16.4)
    doppio.fires.append(gemello)  # stesso fire_id del primo

    mv = build_model(doppio)
    assert any("F_SILA" in n and "ripetuti" in n for n in mv.notes)

    plan = solve(doppio, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert any("ripetuti" in n for n in plan.notes)


def test_identificativi_di_mezzo_ripetuti_finiscono_nelle_note(
    scenario_minimo: Scenario,
):
    """Stesso principio sui mezzi, dove la conseguenza e' piu' grave.

    La non sovrapposizione si impone per identificativo: due mezzi omonimi
    condividono le stesse variabili `used` e lo stesso NoOverlap, quindi il
    piano potrebbe farli lavorare insieme senza che il modello se ne accorga.
    """
    doppio = copy.deepcopy(scenario_minimo)
    doppio.vehicles.append(copy.deepcopy(doppio.vehicles[0]))

    mv = build_model(doppio)
    assert any("CAN_01" in n and "ripetuti" in n for n in mv.notes)


def test_un_focolaio_coperto_esattamente_da_una_cisterna(
    instant: datetime, base_cosenza: Base, elicottero: Vehicle, lago_silano: WaterSource
):
    """Valore al bordo della copertura: fabbisogno uguale alla capacita' del mezzo.

    La copertura e' reificata su `delivered >= demand`, scritta in CP-SAT come
    coppia di vincoli condizionati con `delivered <= demand - 1` sul ramo
    negativo. E' il punto in cui si sbaglia un `>` per un `>=` e nessuno se ne
    accorge, perche' con fabbisogni irregolari il caso di uguaglianza esatta
    non capita quasi mai.
    """
    capacita = elicottero.water_capacity_liters
    sc = Scenario(
        scenario_id="SC_ESATTO",
        timestamp=instant,
        horizon_minutes=240,
        fires=[_piccolo_focolaio("F_ESATTO", 39.3000, 16.3000, instant, capacita)],
        vehicles=[copy.deepcopy(elicottero)],
        bases=[base_cosenza],
        water_sources=[lago_silano],
    )
    plan = solve(sc, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert len(plan.assignments) == 1
    assert plan.assignments[0].sorties == 1
    assert plan.assignments[0].water_delivered_liters == capacita
    assert plan.unassigned_fire_ids == []

    # Un litro in piu' e serve un secondo ciclo: il bordo cade dalla parte
    # giusta, non uno sgancio prima.
    uno_in_piu = copy.deepcopy(sc)
    uno_in_piu.scenario_id = "SC_UNO_IN_PIU"
    uno_in_piu.fires[0].water_demand_liters = capacita + 1
    piano2 = solve(uno_in_piu, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    assert piano2.assignments and piano2.assignments[0].sorties == 2


# ==========================================================================
# Casi limite: selettori che non pescano niente
# ==========================================================================


def test_forzare_mezzi_inesistenti_e_inapplicabile_non_contraddittorio(
    scenario_minimo: Scenario,
):
    """Regressione: un selettore a vuoto non deve far scattare il rilassamento.

    "Mandate gli elicotteri leggeri sulla Sila" in uno scenario che di
    elicotteri leggeri non ne ha: la somma delle assegnazioni diventava la
    costante zero, il vincolo si riduceva a `0 >= 1`, il modello risultava
    infattibile per costruzione e il solver rispondeva con il rilassamento,
    scrivendo nel piano "ordine rilassato, il piano lo viola consapevolmente".
    E' una frase falsa: non c'era nessuna contraddizione fra ordini, c'era un
    ordine che parlava di mezzi assenti.

    Il danno non si fermava alla nota. Il rilassamento converte in penalita'
    TUTTI i vincoli duri dell'ordine insieme, quindi un solo selettore a vuoto
    bastava a declassare a preferenze anche gli ordini eseguibili che lo
    accompagnavano: qui il divieto sul Canadair, che il solver avrebbe potuto
    violare se il guadagno avesse superato la penalita'.
    """
    cs = constraint_set(
        ForceAssignment(
            constraint_id="fa_fantasma",
            vehicle_selector=Selector(
                field=SelectorField.VEHICLE_KIND, values=["elicottero_leggero"]
            ),
            fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
            min_count=1,
            enforcement=Enforcement.HARD,
        ),
        ExcludeVehicle(
            constraint_id="ev_canadair",
            selector=by_id(["CAN_01"]),
            enforcement=Enforcement.HARD,
        ),
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert "rilassat" not in " ".join(plan.notes)
    assert any("fa_fantasma" in n and "non applicabile" in n for n in plan.notes)
    # L'ordine eseguibile resta duro: il Canadair non lavora.
    assert all(a.vehicle_id != "CAN_01" for a in plan.assignments)
    assert plan.assignments, "gli altri mezzi devono continuare a lavorare"


def test_minimo_di_mezzi_con_selettore_a_vuoto_e_inapplicabile(
    scenario_minimo: Scenario,
):
    """Stessa lettura per MIN_VEHICLES_ON_FIRE, dove il selettore e' opzionale.

    Assente significa "due mezzi qualsiasi", quindi un selettore che risolve a
    vuoto ha un solo significato possibile: nello scenario non c'e' nessun
    mezzo che corrisponda. L'ordine e' inapplicabile, non contraddittorio.
    """
    cs = constraint_set(
        MinVehiclesOnFire(
            constraint_id="mv_fantasma",
            fire_selector=Selector(field=SelectorField.ALL),
            count=2,
            vehicle_selector=Selector(
                field=SelectorField.VEHICLE_KIND, values=["autobotte"]
            ),
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert "rilassat" not in " ".join(plan.notes)
    assert any("mv_fantasma" in n and "non applicabile" in n for n in plan.notes)
    assert plan.assignments


def test_mezzi_esistenti_ma_senza_missioni_restano_una_infattibilita_vera(
    scenario_minimo: Scenario,
):
    """Il caso vicino che NON va confuso con il selettore a vuoto.

    Il modulo AIB esiste ed e' impiegabile, ma non puo' pescare in mare: nel
    modello non ha nessuna coppia. Chiedergli di intervenire e' impossibile
    davvero, e la risposta giusta e' il rilassamento, non "ordine
    inapplicabile". La distinzione sta fra i mezzi dello SCENARIO e le coppie
    del MODELLO, ed e' proprio quella che il controllo sul selettore vuoto
    deve rispettare.
    """
    cs = constraint_set(
        ForceAssignment(
            constraint_id="fa_terrestre",
            vehicle_selector=by_id(["AIB_01"]),
            fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
            min_count=1,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    testo = " ".join(plan.notes)
    assert "rilassat" in testo and "fa_terrestre" in testo


def test_il_resolver_esterno_non_detta_l_ordine(
    scenario_minimo: Scenario, monkeypatch: pytest.MonkeyPatch
):
    """L'ordine del risultato e' quello dello scenario, non quello del resolver.

    Da questa sequenza discende l'ordine in cui i vincoli entrano nel modello,
    e due modelli costruiti in sequenza diversa possono far scegliere al
    solver piani diversi a parita' di valore obiettivo. Se l'ordine venisse
    dal resolver di `nlu/`, la riproducibilita' del piano dipenderebbe da un
    dettaglio di un altro sottosistema.
    """

    class ResolverAlContrario:
        @staticmethod
        def resolve_vehicles(selector, scenario):
            return list(reversed([v.vehicle_id for v in scenario.vehicles]))

        @staticmethod
        def resolve_fires(selector, scenario):
            return list(reversed([f.fire_id for f in scenario.fires]))

    monkeypatch.setattr(optimize_model, "_nlu_resolver", lambda: ResolverAlContrario)
    tutti = Selector(field=SelectorField.ALL)

    assert optimize_model.resolve_vehicle_ids(tutti, scenario_minimo) == [
        v.vehicle_id for v in scenario_minimo.vehicles
    ]
    assert optimize_model.resolve_fire_ids(tutti, scenario_minimo) == [
        f.fire_id for f in scenario_minimo.fires
    ]


def test_il_resolver_che_restituisce_un_insieme_viene_ignorato(
    scenario_minimo: Scenario, monkeypatch: pytest.MonkeyPatch
):
    """Un `set` si itera in un ordine che dipende dagli hash: si usa il ripiego.

    Non e' pignoleria teorica: da quell'ordine dipende la sequenza dei vincoli
    e quindi, a parita' di valore obiettivo, il piano scelto. Meglio il
    ripiego locale, che scorre lo scenario e quindi e' ordinato per
    costruzione.
    """

    class ResolverConInsiemi:
        @staticmethod
        def resolve_vehicles(selector, scenario):
            return {v.vehicle_id for v in scenario.vehicles}

        @staticmethod
        def resolve_fires(selector, scenario):
            return {f.fire_id for f in scenario.fires}

    monkeypatch.setattr(optimize_model, "_nlu_resolver", lambda: ResolverConInsiemi)
    tutti = Selector(field=SelectorField.ALL)

    assert optimize_model.resolve_vehicle_ids(tutti, scenario_minimo) == [
        v.vehicle_id for v in scenario_minimo.vehicles
    ]
    assert optimize_model.resolve_fire_ids(tutti, scenario_minimo) == [
        f.fire_id for f in scenario_minimo.fires
    ]


# ==========================================================================
# Casi limite: insiemi di vincoli degeneri
# ==========================================================================


def test_nessun_vincolo_e_insieme_vuoto_si_comportano_allo_stesso_modo(
    scenario_minimo: Scenario,
):
    """Un ordine che non ha prodotto vincoli deve valere quanto nessun ordine.

    E' il caso di riferimento dell'ablazione: il solver "senza vincoli" e il
    solver "con un ordine che non ha tradotto niente" devono produrre lo
    stesso piano, altrimenti il confronto misurerebbe la presenza
    dell'oggetto e non quella dei vincoli.
    """
    mv = build_model(scenario_minimo)
    vuoto = ConstraintSet(constraint_set_id="CS_VUOTO")
    assert vuoto.is_empty()

    report_none = optimize_model.apply_constraints(mv.model, mv, scenario_minimo, None)
    report_vuoto = optimize_model.apply_constraints(
        mv.model, mv, scenario_minimo, vuoto
    )
    for report in (report_none, report_vuoto):
        assert report.applied_ids == []
        assert report.relaxed_ids == []
        assert report.unsupported_ids == []
        assert report.notes == []

    senza = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    con_vuoto = solve(
        scenario_minimo, vuoto, max_seconds=TEST_MAX_SECONDS, deterministic=True
    )
    assert fingerprint(senza) == fingerprint(con_vuoto)
    # L'identificativo dell'insieme viaggia comunque nel piano: serve a
    # ricostruire quale ordine ha prodotto quale piano.
    assert senza.constraint_set_id is None
    assert con_vuoto.constraint_set_id == "CS_VUOTO"


def test_un_tetto_di_risposta_al_massimo_dello_schema_non_vincola_nulla(
    scenario_minimo: Scenario,
):
    """Valore al limite superiore dell'intervallo: 600 minuti su un orizzonte di 240.

    E' il bordo opposto a quello gia' provato con `minutes=1`. Un tetto piu'
    largo dell'orizzonte non puo' mordere, e il piano deve restare identico a
    quello senza vincoli: se cambiasse, vorrebbe dire che il vincolo tocca
    qualcosa che non dovrebbe toccare.
    """
    senza = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    cs = constraint_set(
        MaxResponseTime(
            constraint_id="mrt_max", minutes=600, enforcement=Enforcement.HARD
        )
    )
    con = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert fingerprint(senza) == fingerprint(con)
    assert "rilassat" not in " ".join(con.notes)


def test_quota_con_minimo_uguale_al_massimo(scenario_minimo: Scenario):
    """Il bordo dove i due estremi coincidono: "esattamente un mezzo aereo".

    Lo schema lo ammette (`min_used <= max_used`) e il modello deve produrre
    due vincoli che si chiudono su un valore solo, non un intervallo vuoto.
    """
    cs = constraint_set(
        VehicleTypeQuota(
            constraint_id="vq_esatta",
            selector=Selector(field=SelectorField.VEHICLE_DOMAIN, values=["air"]),
            min_used=1,
            max_used=1,
            enforcement=Enforcement.HARD,
        )
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    aerei = {
        a.vehicle_id
        for a in plan.assignments
        if scenario_minimo.vehicle_by_id(a.vehicle_id).domain is Domain.AIR
    }
    assert len(aerei) == 1


def test_un_moltiplicatore_di_priorita_unitario_non_cambia_niente(
    scenario_minimo: Scenario,
):
    """Valore al limite inferiore dello schema: `weight_multiplier = 1.0`.

    Uno e' l'elemento neutro della moltiplicazione, quindi il piano deve
    restare identico. E' il controllo che smaschera un moltiplicatore
    applicato per somma invece che per prodotto: con l'addizione, "priorita'
    per uno" sposterebbe i pesi e cambierebbe il piano senza che nessuno
    l'abbia chiesto.
    """
    senza = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)
    cs = constraint_set(
        PrioritizeZone(
            constraint_id="pz_neutro",
            zone_kind=ZoneKind.WUI,
            weight_multiplier=1.0,
            enforcement=Enforcement.SOFT,
        )
    )
    con = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert fingerprint(senza) == fingerprint(con)
    assert senza.objective_value == pytest.approx(con.objective_value)


def test_il_piano_dichiara_sempre_le_convenzioni_che_usa(scenario_minimo: Scenario):
    """Le note non sono decorazione: sono il contratto con chi legge il piano.

    Due cose devono esserci sempre, anche nel piano piu' banale. La prima e'
    la convenzione su `release_minute`, che e' il rientro in base e non la
    fine dell'ultimo sgancio: chi la legge male sottostima di un volo intero
    l'impegno di ogni mezzo. La seconda sono i pesi effettivamente usati,
    perche' il piano dipende da loro e confrontare due piani con pesi diversi
    non significa niente.
    """
    plan = solve(scenario_minimo, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    assert any("release_minute" in n and "rientro in base" in n for n in plan.notes)
    for nome in OBJECTIVE_WEIGHTS:
        assert any(n.startswith(f"peso {nome} = ") for n in plan.notes)
    assert any("deterministica" in n for n in plan.notes)


def test_un_vincolo_di_soli_pesi_non_viene_mai_dichiarato_rilassato(
    scenario_minimo: Scenario,
):
    """Regressione sulle note: non si puo' violare un ordine che non vieta niente.

    PRIORITIZE_ZONE, OBJECTIVE_WEIGHT e PROTECT_INFRASTRUCTURE agiscono solo
    sui pesi dell'obiettivo: qualunque piano li "rispetta", quindi non possono
    essere la causa di un'infattibilita'.

    Lo schema pero' permette di marcarli duri, perche' `enforcement` e' un
    campo comune a tutti i vincoli. Prima della correzione bastava un
    PRIORITIZE_ZONE duro accanto a un ordine davvero contraddittorio perche'
    la seconda passata lo elencasse fra i vincoli rilassati e il piano
    dichiarasse di violarlo consapevolmente: una frase falsa proprio nel
    momento in cui il coordinatore legge il piano per capire quale parte del
    suo ordine e' caduta.

    E' anche la stessa classificazione che usa il validatore indipendente, che
    per questi tre tipi non emette nessuna regola: su un singolo piano non
    esiste un criterio per dire se un peso e' stato rispettato.
    """
    cs = constraint_set(
        PrioritizeZone(
            constraint_id="pz_dura",
            zone_kind=ZoneKind.WUI,
            weight_multiplier=3.0,
            enforcement=Enforcement.HARD,
        ),
        ExcludeVehicle(
            constraint_id="ev_tutti",
            selector=Selector(field=SelectorField.ALL),
            enforcement=Enforcement.HARD,
        ),
        ForceAssignment(
            constraint_id="fa_impossibile",
            vehicle_selector=by_id(["CAN_01"]),
            fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
            min_count=1,
            enforcement=Enforcement.HARD,
        ),
    )
    plan = solve(scenario_minimo, cs, max_seconds=TEST_MAX_SECONDS, deterministic=True)

    testo = " ".join(plan.notes)
    assert "rilassat" in testo, "l'ordine e' davvero contraddittorio"
    assert "ev_tutti" in testo and "fa_impossibile" in testo
    assert "pz_dura" not in testo.split("Ordini rilassati")[-1], (
        "un vincolo di soli pesi non puo' comparire fra gli ordini violati"
    )
    # E il peso che chiedeva e' stato comunque applicato.
    assert any("Priorita' moltiplicata" in n for n in plan.notes)


def test_un_ordine_di_soli_pesi_non_paga_una_seconda_risoluzione(
    scenario_minimo: Scenario,
):
    """Controprova diretta sul filtro usato dal rilassamento.

    `_relaxable_ids` decide se vale la pena spendere una seconda risoluzione
    completa. Un ordine fatto di soli pesi non ha niente da rilassare: se
    comparisse in quella lista, ogni infattibilita' che viene dalla fisica (i
    mezzi non bastano) costerebbe il doppio del tempo per arrivare allo stesso
    piano vuoto.
    """
    cs = constraint_set(
        ObjectiveWeight(
            constraint_id="ow_duro",
            term=ObjectiveTerm.COVERAGE,
            weight=9.0,
            enforcement=Enforcement.HARD,
        ),
        PrioritizeZone(
            constraint_id="pz_dura2",
            zone_kind=ZoneKind.WUI,
            weight_multiplier=2.0,
            enforcement=Enforcement.HARD,
        ),
    )
    mv = build_model(scenario_minimo)
    report = optimize_model.apply_constraints(mv.model, mv, scenario_minimo, cs)
    assert set(report.applied_ids) == {"ow_duro", "pz_dura2"}
    assert solver_module._relaxable_ids(cs, report) == []


def test_tutti_i_tipi_di_vincolo_dello_schema_sono_tradotti(scenario_minimo: Scenario):
    """Il repertorio chiuso dello schema e quello del modello devono coincidere.

    Il docstring di `ConstraintKind` lo scrive come regola di progetto:
    aggiungere un valore all'enumerazione significa implementarlo in
    `optimize/`, verificarlo in `validate/` e aggiungere casi al benchmark.
    Nessun valore puo' esistere senza le tre cose.

    Questa prova copre il primo dei tre impegni, e serve perche' il difetto e'
    silenzioso: `_apply_one` finisce con un `return False`, quindi un tipo non
    implementato non solleva niente, si limita a diventare una nota "non
    applicabile a questo scenario". Il coordinatore leggerebbe che il suo
    ordine non si applicava a quei dati, quando la verita' e' che il solver
    non lo sa fare.

    Ogni vincolo gira nel proprio `ConstraintSet` per non interferire con gli
    altri: FORCE_ASSIGNMENT e FORBID_ASSIGNMENT sullo stesso accoppiamento si
    contraddirebbero, e il risultato direbbe piu' sul rilassamento che sulla
    copertura dei tipi. E non si risolve niente: qui interessa la traduzione,
    non il piano.
    """
    sila = scenario_minimo.fire_by_id("F_SILA")
    esemplari = {
        ConstraintKind.PRIORITIZE_ZONE: PrioritizeZone(
            constraint_id="k_pz", zone_kind=ZoneKind.WUI, weight_multiplier=2.0
        ),
        ConstraintKind.RESERVE_VEHICLE: ReserveVehicle(
            constraint_id="k_rv", selector=by_id(["CAN_01"])
        ),
        ConstraintKind.EXCLUDE_VEHICLE: ExcludeVehicle(
            constraint_id="k_ev", selector=by_id(["ELI_01"])
        ),
        ConstraintKind.FORCE_ASSIGNMENT: ForceAssignment(
            constraint_id="k_fa",
            vehicle_selector=by_id(["CAN_01"]),
            fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
        ),
        ConstraintKind.FORBID_ASSIGNMENT: ForbidAssignment(
            constraint_id="k_fb",
            vehicle_selector=by_id(["CAN_01"]),
            fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
        ),
        ConstraintKind.MAX_RESPONSE_TIME: MaxResponseTime(
            constraint_id="k_mrt", minutes=60
        ),
        ConstraintKind.MIN_VEHICLES_ON_FIRE: MinVehiclesOnFire(
            constraint_id="k_mv",
            fire_selector=Selector(field=SelectorField.ALL),
            count=1,
        ),
        ConstraintKind.VEHICLE_TYPE_QUOTA: VehicleTypeQuota(
            constraint_id="k_vq",
            selector=Selector(field=SelectorField.VEHICLE_DOMAIN, values=["air"]),
            max_used=2,
        ),
        ConstraintKind.TIME_WINDOW: TimeWindow(
            constraint_id="k_tw",
            selector=Selector(field=SelectorField.ALL),
            from_minute=720,
            to_minute=890,
        ),
        ConstraintKind.OBJECTIVE_WEIGHT: ObjectiveWeight(
            constraint_id="k_ow", term=ObjectiveTerm.COST, weight=2.0
        ),
        ConstraintKind.PROTECT_INFRASTRUCTURE: ProtectInfrastructure(
            constraint_id="k_pi",
            name="Ripetitore di prova",
            lat=sila.centroid.lat,
            lon=sila.centroid.lon,
            buffer_km=10.0,
        ),
        ConstraintKind.UNSUPPORTED: Unsupported(
            constraint_id="k_un", reason="fuori repertorio"
        ),
    }

    assert set(esemplari) == set(ConstraintKind), (
        "lo schema ha tipi di vincolo che questa prova non copre: "
        f"{sorted(k.value for k in set(ConstraintKind) - set(esemplari))}"
    )

    non_tradotti: list[str] = []
    for kind, vincolo in esemplari.items():
        mv = build_model(scenario_minimo)
        report = optimize_model.apply_constraints(
            mv.model, mv, scenario_minimo, constraint_set(vincolo, set_id=kind.value)
        )
        riconosciuto = bool(report.applied_ids) or bool(report.unsupported_ids)
        if not riconosciuto:
            non_tradotti.append(kind.value)

    assert non_tradotti == [], (
        "tipi di vincolo dello schema che il modello non traduce: "
        + ", ".join(non_tradotti)
        + ". Il difetto e' silenzioso: diventano una nota 'non applicabile' e "
        "il coordinatore crede che il suo ordine non riguardasse questi dati."
    )
