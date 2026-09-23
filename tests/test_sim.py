"""Test del sottosistema baseline e rigiocata.

Non verificano che il codice giri: verificano le tre proprieta' su cui si regge
l'esperimento di SPEC.md sezione 7, e che sono facili da rompere senza
accorgersene.

1. Il pianificatore non vede il futuro. Se lo vedesse, i risultati della
   rigiocata sarebbero irreali e nessuno se ne accorgerebbe guardando i numeri,
   perche' sarebbero semplicemente piu' belli.
2. Lo stato non sanguina fra una rigiocata e l'altra. Due esecuzioni della
   stessa rigiocata devono dare lo stesso risultato, e la seconda non deve
   ereditare niente dalla prima.
3. La baseline e' un termine di paragone onesto: produce piani che rispettano la
   fisica (autonomia, turno, orizzonte, un mezzo alla volta), non piani finti
   che perdono il confronto per costruzione.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from tagliafuoco.baseline.nearest import (
    ENFORCED_HARD_KINDS,
    PRODUCER_NEAREST,
    PRODUCER_PRIORITY,
    UNENFORCED_HARD_KINDS,
    plan_nearest,
    plan_priority,
    reposition_minutes,
)
from tagliafuoco.domain.models import (
    Assignment,
    Detection,
    Domain,
    FireCluster,
    GeoPoint,
    Plan,
    Scenario,
    VehicleKind,
    VehicleStatus,
    WaterSource,
    WaterSourceKind,
)
from tagliafuoco.nlu.schema import (
    ConstraintKind,
    ConstraintSet,
    ExcludeVehicle,
    ForbidAssignment,
    MaxResponseTime,
    ReserveVehicle,
    Selector,
    SelectorField,
    TimeWindow,
)
from tagliafuoco.sim.replay import (
    ReplayError,
    ReplayResult,
    basic_metrics,
    build_context,
    build_scenario,
    replay_season,
    replay_window,
    save_result,
)
from tagliafuoco.sim.state import (
    WorldState,
    fires_still_burning,
    fresh_fleet_snapshot,
)
from tagliafuoco.sim.timeline import (
    ReplayWindow,
    TimelineError,
    build_windows,
    is_night_at,
    span_of,
    visible_detections,
    visible_fires,
)

WINDOW_HOURS = 3


# --------------------------------------------------------------------------
# Materiale di prova
# --------------------------------------------------------------------------


def _shift_fire(source: FireCluster, fire_id: str, seen: datetime) -> FireCluster:
    """Copia di un focolaio con un altro identificativo e un'altra ora."""
    fire = copy.deepcopy(source)
    fire.fire_id = fire_id
    fire.first_seen = seen
    fire.last_seen = seen
    for i, det in enumerate(fire.detections):
        det.detection_id = f"{fire_id}_D{i}"
        det.acquired_at = seen
    return fire


@pytest.fixture
def fires_scaglionati(fire_sila, fire_wui, instant) -> list[FireCluster]:
    """Quattro focolai che compaiono in ore diverse, due per finestra.

    Serve a rendere osservabile la fuga di informazione dal futuro: se la prima
    finestra vedesse anche gli ultimi due, il difetto si noterebbe subito.
    """
    return [
        _shift_fire(fire_sila, "F_A", instant),
        _shift_fire(fire_wui, "F_B", instant + timedelta(minutes=30)),
        _shift_fire(fire_wui, "F_C", instant + timedelta(hours=4)),
        _shift_fire(fire_sila, "F_D", instant + timedelta(hours=7)),
    ]


@pytest.fixture
def contesto(fires_scaglionati, canadair, elicottero, modulo_aib, base_lamezia, base_cosenza, sea_source):
    """Contesto di rigiocata costruito a mano, senza dati statici su disco."""
    return build_context(
        vehicles=[canadair, elicottero, modulo_aib],
        bases=[base_lamezia, base_cosenza],
        water_sources=[sea_source],
        fires=fires_scaglionati,
    )


# --------------------------------------------------------------------------
# 1. Le finestre non vedono il futuro
# --------------------------------------------------------------------------


def test_le_finestre_coprono_l_intervallo_senza_buchi(instant):
    windows = build_windows(
        instant, instant + timedelta(hours=9), window_hours=WINDOW_HOURS
    )
    assert [w.index for w in windows] == [0, 1, 2]
    assert windows[0].start == instant
    assert windows[-1].end == instant + timedelta(hours=9)
    for prima, dopo in zip(windows, windows[1:]):
        assert prima.end == dopo.start
        assert prima.duration_minutes == pytest.approx(WINDOW_HOURS * 60)


def test_intervallo_invertito_e_un_errore(instant):
    with pytest.raises(TimelineError):
        build_windows(instant, instant - timedelta(hours=1))


def test_i_rilevamenti_visibili_non_includono_il_futuro(instant):
    """La regola e' `acquired_at <= t`: l'istante esatto e' noto, un secondo dopo no."""
    detections = [
        Detection(
            detection_id=f"D{i}",
            point=GeoPoint(lat=39.0 + i * 0.01, lon=16.0),
            acquired_at=instant + timedelta(hours=i),
            satellite="N",
            instrument="VIIRS",
            confidence=80.0,
            frp_mw=10.0,
        )
        for i in range(6)
    ]
    as_of = instant + timedelta(hours=2)

    visible = visible_detections(detections, as_of)

    assert [d.detection_id for d in visible] == ["D0", "D1", "D2"]
    assert all(d.acquired_at <= as_of for d in visible)
    # Un secondo dopo l'istante di decisione il rilevamento non esiste ancora.
    appena_dopo = visible_detections(detections, as_of - timedelta(seconds=1))
    assert [d.detection_id for d in appena_dopo] == ["D0", "D1"]


def test_i_rilevamenti_troppo_vecchi_escono_dalla_finestra(instant):
    """Oltre la finestra di clustering un rilevamento non descrive piu' lo stesso focolaio."""
    vecchio = Detection(
        detection_id="OLD",
        point=GeoPoint(lat=39.0, lon=16.0),
        acquired_at=instant - timedelta(hours=30),
        satellite="N",
        instrument="VIIRS",
        confidence=80.0,
        frp_mw=10.0,
    )
    recente = Detection(
        detection_id="NEW",
        point=GeoPoint(lat=39.0, lon=16.0),
        acquired_at=instant - timedelta(hours=1),
        satellite="N",
        instrument="VIIRS",
        confidence=80.0,
        frp_mw=10.0,
    )
    visible = visible_detections([vecchio, recente], instant, lookback_hours=12.0)
    assert [d.detection_id for d in visible] == ["NEW"]


def test_i_focolai_futuri_non_entrano_negli_scenari(contesto, fires_scaglionati, instant):
    """Il controllo che conta: nessuno scenario contiene un focolaio non ancora rilevato."""
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    windows = build_windows(
        instant, instant + timedelta(hours=9), window_hours=WINDOW_HOURS
    )
    visti_per_finestra = []
    for window in windows:
        scenario = build_scenario(window, contesto, state)
        visti_per_finestra.append({f.fire_id for f in scenario.fires})
        for fire in scenario.fires:
            assert fire.first_seen <= window.start

    # Progressione attesa: alle 11:30 si conosce solo il focolaio gia' rilevato,
    # quello delle 12:00 compare alla finestra dopo, e cosi' via. Il focolaio
    # delle 18:30 non deve comparire in nessuna delle tre finestre.
    assert visti_per_finestra[0] == {"F_A"}
    assert visti_per_finestra[1] == {"F_A", "F_B"}
    assert visti_per_finestra[2] == {"F_A", "F_B", "F_C"}
    assert all("F_D" not in visti for visti in visti_per_finestra)

    # L'insieme dei focolai visibili puo' solo crescere man mano che il tempo
    # passa: se si restringesse, vorrebbe dire che una finestra ha visto e poi
    # dimenticato qualcosa.
    for prima, dopo in zip(visti_per_finestra, visti_per_finestra[1:]):
        assert prima <= dopo


def test_nemmeno_il_clustering_vede_il_futuro(instant, canadair, base_lamezia, sea_source):
    """Con i rilevamenti grezzi il filtro sta PRIMA del raggruppamento.

    Se il clustering vedesse tutta la stagione, i focolai delle prime finestre
    nascerebbero gia' con la forma che avranno dopo: una fuga di informazione
    piu' sottile di quella sugli scenari, e altrettanto fatale.
    """
    clustering = pytest.importorskip(
        "tagliafuoco.ingest.clustering", reason="ingest non e' ancora disponibile"
    )
    detections = [
        Detection(
            detection_id=f"D{i}",
            point=GeoPoint(lat=39.30 + i * 0.001, lon=16.55),
            acquired_at=instant + timedelta(hours=i),
            satellite="N",
            instrument="VIIRS",
            confidence=85.0,
            frp_mw=25.0,
            scan_km=0.375,
            track_km=0.375,
        )
        for i in range(7)
    ]
    contesto = build_context(
        vehicles=[canadair],
        bases=[base_lamezia],
        water_sources=[sea_source],
        detections=detections,
        fires_provider=clustering.cluster_detections,
    )
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    for window in build_windows(
        instant, instant + timedelta(hours=9), window_hours=WINDOW_HOURS
    ):
        scenario = build_scenario(window, contesto, state)
        for fire in scenario.fires:
            for det in fire.detections:
                assert det.acquired_at <= window.start


def test_visible_fires_accetta_istanti_senza_fuso(fires_scaglionati, instant):
    """Le date scritte a mano sono spesso ingenue: non devono far esplodere il confronto."""
    naive = instant.replace(tzinfo=None) + timedelta(hours=1)
    visible = visible_fires(fires_scaglionati, naive)
    assert {f.fire_id for f in visible} == {"F_A", "F_B"}


def test_la_notte_si_riconosce_dall_orario():
    giorno = datetime(2025, 8, 12, 11, 0, tzinfo=timezone.utc)
    notte = datetime(2025, 8, 12, 22, 0, tzinfo=timezone.utc)
    assert is_night_at(giorno) is False
    assert is_night_at(notte) is True


# --------------------------------------------------------------------------
# 2. La baseline produce piani che stanno in piedi
# --------------------------------------------------------------------------


def _assert_piano_coerente(plan: Plan, scenario: Scenario) -> None:
    """Controlli di fisica e di coerenza che valgono per qualunque pianificatore.

    Non sostituiscono il validatore indipendente di `validate/`: sono la rete
    minima perche' questo sottosistema non consegni piani assurdi mentre il
    validatore vero e' in scrittura.
    """
    assert plan.horizon_minutes == scenario.horizon_minutes

    impegno: dict[str, list[tuple[float, float]]] = {}
    for a in plan.assignments:
        vehicle = scenario.vehicle_by_id(a.vehicle_id)
        assert vehicle is not None, f"mezzo inesistente: {a.vehicle_id}"
        assert scenario.fire_by_id(a.fire_id) is not None
        assert vehicle.status is VehicleStatus.AVAILABLE

        assert a.departure_minute >= vehicle.available_from_minute - 1e-6
        assert a.departure_minute <= a.arrival_minute <= a.release_minute
        assert a.release_minute <= plan.horizon_minutes + 1e-6
        assert a.engaged_minutes <= vehicle.endurance_minutes + 1e-6
        assert a.sorties >= 1
        assert a.water_delivered_liters == a.sorties * vehicle.water_capacity_liters
        if a.water_source_id is not None:
            assert scenario.water_source_by_id(a.water_source_id) is not None
        impegno.setdefault(a.vehicle_id, []).append(
            (a.departure_minute, a.release_minute)
        )

    # Un mezzo non puo' essere in due posti insieme.
    for vehicle_id, intervalli in impegno.items():
        intervalli.sort()
        for (_, fine), (inizio, _) in zip(intervalli, intervalli[1:]):
            assert inizio >= fine - 1e-6, f"{vehicle_id} impegnato due volte insieme"

    # Fra due missioni consecutive deve esserci il riattrezzamento della base.
    # Questo controllo prima pretendeva invece il volo diretto fra i due
    # focolai, ed era la lettura che SPEC.md sezione 6 ha poi escluso: una
    # missione finisce con il rientro in base, quindi quel trasferimento e' gia'
    # dentro l'andata della missione successiva e pretenderlo anche qui lo
    # conterebbe due volte. Cio' che resta scoperto, e che ora si verifica, e' il
    # tempo fermo a terra.
    for vehicle_id in impegno:
        vehicle = scenario.vehicle_by_id(vehicle_id)
        base = scenario.base_by_id(vehicle.home_base_id)
        if base is None:
            continue
        sequenza = plan.assignments_for_vehicle(vehicle_id)
        for prima, dopo in zip(sequenza, sequenza[1:]):
            gap = dopo.departure_minute - prima.release_minute
            assert gap + 1e-6 >= base.turnaround_minutes, (
                f"{vehicle_id} riparte dopo {gap:.1f} minuti a terra, ma la base "
                f"{base.base_id} ne chiede {base.turnaround_minutes:.1f}"
            )

    # Il turno complessivo non sfora.
    for vehicle_id, intervalli in impegno.items():
        vehicle = scenario.vehicle_by_id(vehicle_id)
        totale = sum(fine - inizio for inizio, fine in intervalli)
        assert totale + vehicle.minutes_flown_today <= vehicle.max_shift_minutes + 1e-6

    # Ogni focolaio o e' servito o e' dichiarato non servito: nessuno sparisce.
    serviti = {a.fire_id for a in plan.assignments}
    assert serviti.isdisjoint(plan.unassigned_fire_ids)
    assert serviti | set(plan.unassigned_fire_ids) == {f.fire_id for f in scenario.fires}


def test_la_baseline_produce_un_piano_coerente(scenario_minimo):
    plan = plan_nearest(scenario_minimo)
    assert plan.producer == PRODUCER_NEAREST
    assert plan.solver_status == "FEASIBLE"
    assert plan.assignments, "una baseline che non assegna niente non e' un paragone"
    _assert_piano_coerente(plan, scenario_minimo)


def test_anche_la_baseline_per_priorita_produce_un_piano_coerente(scenario_minimo):
    plan = plan_priority(scenario_minimo)
    assert plan.producer == PRODUCER_PRIORITY
    _assert_piano_coerente(plan, scenario_minimo)


def test_la_baseline_non_tocca_lo_scenario(scenario_minimo):
    """Se la baseline sporcasse lo scenario, il solver che gira dopo partirebbe
    da uno stato diverso e il confronto non varrebbe piu' niente."""
    prima = copy.deepcopy(scenario_minimo)
    plan_nearest(scenario_minimo)
    for atteso, trovato in zip(prima.vehicles, scenario_minimo.vehicles):
        assert atteso.available_from_minute == trovato.available_from_minute
        assert atteso.minutes_flown_today == trovato.minutes_flown_today
        assert atteso.current_position == trovato.current_position
        assert atteso.status is trovato.status


def test_le_due_baseline_differiscono_solo_nell_ordine(scenario_minimo, instant):
    """Il focolaio piu' urgente e' anche quello rilevato per ultimo: le due regole
    devono servirli in ordine opposto."""
    sila = scenario_minimo.fire_by_id("F_SILA")
    wui = scenario_minimo.fire_by_id("F_WUI")
    wui.first_seen = instant + timedelta(hours=1)
    wui.last_seen = wui.first_seen
    assert wui.base_priority > sila.base_priority

    per_tempo = plan_nearest(scenario_minimo)
    per_priorita = plan_priority(scenario_minimo)

    assert per_tempo.assignments[0].fire_id == "F_SILA"
    assert per_priorita.assignments[0].fire_id == "F_WUI"


def test_la_baseline_rispetta_i_vincoli_duri_sui_mezzi(scenario_minimo):
    """Un piano che impiega un mezzo escluso verrebbe respinto dal validatore, e
    un confronto fra un piano valido e uno respinto non misura niente."""
    constraints = ConstraintSet(
        constraint_set_id="CS_TEST",
        constraints=[
            ExcludeVehicle(
                constraint_id="C1",
                selector=Selector(field=SelectorField.VEHICLE_ID, values=["CAN_01"]),
            )
        ],
    )
    plan = plan_nearest(scenario_minimo, constraints)
    assert "CAN_01" not in {a.vehicle_id for a in plan.assignments}
    assert plan.constraint_set_id == "CS_TEST"


def test_di_notte_i_mezzi_aerei_restano_a_terra(scenario_notturno):
    plan = plan_nearest(scenario_notturno)
    aerei = {
        v.vehicle_id for v in scenario_notturno.vehicles if v.domain is Domain.AIR
    }
    assert aerei.isdisjoint({a.vehicle_id for a in plan.assignments})


def _validate_plan_or_skip(plan: Plan, scenario: Scenario, constraint_set=None):
    """Invoca il validatore indipendente, saltando il test se non c'e' ancora.

    `constraint_set` e' facoltativo e va passato quando il piano nasce da un
    ordine del coordinatore: senza, il validatore controlla solo la fisica e i
    vincoli duri tradotti non verrebbero verificati affatto, che e' proprio il
    controllo che qui interessa.
    """
    validator = pytest.importorskip(
        "tagliafuoco.validate.validator",
        reason="il validatore indipendente non e' ancora disponibile",
    )
    validate_plan = getattr(validator, "validate_plan", None)
    if not callable(validate_plan):
        pytest.skip("validate_plan non e' ancora esposto")
    return validate_plan(plan, scenario, constraint_set)


def _verdetto(report) -> bool | None:
    for attr in ("ok", "is_valid", "valid", "accepted"):
        got = getattr(report, attr, None)
        if isinstance(got, bool):
            return got
    return None


@pytest.mark.parametrize("planner", [plan_nearest, plan_priority])
def test_il_piano_della_baseline_passa_il_validatore_indipendente(planner, scenario_minimo):
    """L'unico giudizio che conta davvero: quello di chi ricalcola tutto da capo.

    Se la baseline producesse piani respinti, il confronto con il solver non
    starebbe in piedi: si confronterebbe un piano accettato con uno buttato.
    """
    plan = planner(scenario_minimo)
    report = _validate_plan_or_skip(plan, scenario_minimo)
    assert _verdetto(report) is not False, f"piano respinto: {report}"


# --------------------------------------------------------------------------
# La baseline davanti agli ordini duri del coordinatore
#
# Qui si difende il presupposto dell'esperimento di SPEC.md sezione 7: il
# confronto fra risolutore e baseline si fa sugli STESSI scenari e con gli
# stessi ordini. Se la baseline potesse impiegare mezzi che il risolutore non
# puo' impiegare, il confronto sarebbe falsato a suo favore; e comunque il
# validatore respingerebbe il suo piano, e un piano respinto esce dal
# confronto. I test che seguono non guardano il codice: guardano il verdetto
# del validatore indipendente, che e' l'unico giudizio che conta.
# --------------------------------------------------------------------------


@pytest.fixture
def scenario_con_squadriglia(scenario_minimo) -> Scenario:
    """Lo scenario minimo con sei elicotteri al posto di uno.

    Con un elicottero solo, una baseline che ignora la riserva produce una
    violazione sola e si puo' scambiare per un caso limite. Con sei il
    validatore ne emette sei e il piano viene respinto in blocco, che e'
    esattamente il difetto trovato nella prova end to end: la baseline
    impiegava i mezzi che l'ordine del coordinatore teneva fermi.
    """
    sc = copy.deepcopy(scenario_minimo)
    sc.scenario_id = "SC_SQUADRIGLIA"
    modello = next(v for v in sc.vehicles if v.kind is VehicleKind.ELICOTTERO_MEDIO)
    altri = [v for v in sc.vehicles if v.vehicle_id != modello.vehicle_id]
    squadriglia = []
    for i in range(6):
        clone = copy.deepcopy(modello)
        clone.vehicle_id = f"ELI_{i:02d}"
        clone.name = f"Elicottero {i:02d}"
        squadriglia.append(clone)
    sc.vehicles = altri + squadriglia
    return sc


def _ordine_di_riserva() -> ConstraintSet:
    """L'ordine "l'elicottero resta di riserva", tradotto."""
    return ConstraintSet(
        constraint_set_id="CS_RISERVA",
        source_text="l'elicottero resta di riserva",
        constraints=[
            ReserveVehicle(
                constraint_id="R1",
                selector=Selector(
                    field=SelectorField.VEHICLE_KIND, values=["elicottero_medio"]
                ),
            )
        ],
    )


def test_la_baseline_lascia_a_terra_i_mezzi_messi_di_riserva(scenario_con_squadriglia):
    """Senza vincolo la baseline usa gli elicotteri, con il vincolo nessuno.

    Il secondo piano, quello senza ordine, non e' un di piu': serve a rendere il
    test non vacuo. Se la squadriglia restasse a terra anche senza vincolo (per
    autonomia, vento o distanza), il primo assert passerebbe senza dimostrare
    niente e la regressione tornerebbe senza che nessuno se ne accorga.
    """
    di_riserva = {
        v.vehicle_id
        for v in scenario_con_squadriglia.vehicles
        if v.kind is VehicleKind.ELICOTTERO_MEDIO
    }
    assert di_riserva

    senza_ordine = plan_nearest(scenario_con_squadriglia)
    assert di_riserva & {a.vehicle_id for a in senza_ordine.assignments}

    con_ordine = plan_nearest(scenario_con_squadriglia, _ordine_di_riserva())
    assert di_riserva.isdisjoint({a.vehicle_id for a in con_ordine.assignments})
    assert con_ordine.constraint_set_id == "CS_RISERVA"


@pytest.mark.parametrize("planner", [plan_nearest, plan_priority])
def test_il_piano_della_baseline_con_mezzi_di_riserva_passa_il_validatore(
    planner, scenario_con_squadriglia
):
    """Il piano della baseline, sotto un ordine che riserva mezzi, resta valido.

    E' il test che il difetto avrebbe fatto fallire: prima della correzione il
    validatore respingeva il piano con una violazione bloccante per ciascun
    elicottero impiegato, e un piano respinto non entra nel confronto.
    """
    ordine = _ordine_di_riserva()
    plan = planner(scenario_con_squadriglia, ordine)
    report = _validate_plan_or_skip(plan, scenario_con_squadriglia, ordine)
    bloccanti = [
        v for v in getattr(report, "violations", []) if getattr(v, "is_blocking", False)
    ]
    assert _verdetto(report) is not False, f"piano respinto: {bloccanti}"
    assert not bloccanti, f"violazioni bloccanti: {bloccanti}"
    # Una baseline che rispetta l'ordine restando a mani in mano non sarebbe un
    # termine di paragone: deve continuare a impiegare cio' che le resta.
    assert plan.assignments


def test_i_litri_dichiarati_coincidono_con_quelli_ricalcolati(scenario_con_squadriglia):
    """Su un piano accettato le due contabilita' dell'acqua tornano.

    Lo scarto visto nella prova end to end non era un errore di conteggio: il
    validatore ricalcola escludendo le assegnazioni che ha bloccato (SPEC.md
    sezione 6, convenzioni sulle metriche), quindi finche' il piano veniva
    respinto le due cifre non potevano coincidere. Tolte le violazioni, devono
    tornare: se un giorno divergessero di nuovo su un piano valido, allora si'
    che ci sarebbe un errore di dichiarazione.
    """
    ordine = _ordine_di_riserva()
    plan = plan_nearest(scenario_con_squadriglia, ordine)
    report = _validate_plan_or_skip(plan, scenario_con_squadriglia, ordine)
    metrics = getattr(report, "metrics", None)
    if metrics is None:
        pytest.skip("il validatore non espone metriche ricalcolate")

    dichiarati = sum(a.water_delivered_liters for a in plan.assignments)
    assert dichiarati > 0
    assert metrics.water_declared_liters == pytest.approx(dichiarati)
    assert metrics.water_deliverable_liters == pytest.approx(dichiarati)


def test_la_riserva_a_quota_ferma_i_mezzi_che_il_validatore_conta(scenario_minimo):
    """Con `count` il vincolo non dice di fermare tutti, dice quanti fermarne.

    Le due letture del selettore che la baseline ha a disposizione (quella
    minima, che ricalca il validatore, e quella di `nlu/resolver.py`, che
    riconosce anche i nomi) possono descrivere insiemi diversi. Per un divieto
    secco la loro unione e' prudente. Per una quota no: fermare i primi n
    dell'unione puo' significare fermare mezzi che il validatore non considera
    selezionati, e allora lui ne conta fermi meno di n e respinge il piano.

    Qui il selettore nomina un mezzo per matricola e uno per nome, cosi' le due
    letture divergono davvero, ed e' il validatore a dire se la baseline ha
    contato come lui.
    """
    quota = ConstraintSet(
        constraint_set_id="CS_QUOTA",
        source_text="tienimene fermo uno",
        constraints=[
            ReserveVehicle(
                constraint_id="R1",
                selector=Selector(
                    field=SelectorField.VEHICLE_ID, values=["ELI_01", "Canadair 01"]
                ),
                count=1,
            )
        ],
    )
    plan = plan_nearest(scenario_minimo, quota)
    report = _validate_plan_or_skip(plan, scenario_minimo, quota)
    assert _verdetto(report) is not False, f"piano respinto: {report}"
    # La prudenza non deve diventare paralisi: fermare l'intera flotta
    # rispetterebbe il vincolo e renderebbe la baseline un termine di paragone
    # inutile, cioe' zavorrato di nascosto.
    assert plan.assignments


def test_la_baseline_rispetta_divieti_di_coppia_e_finestre_orarie(scenario_minimo):
    """Gli altri due divieti del gruppo duro, verificati dal validatore.

    FORBID_ASSIGNMENT toglie un mezzo dai candidati di un focolaio solo;
    TIME_WINDOW non toglie nessun mezzo ma restringe quando puo' operare. Ne'
    l'uno ne' l'altro chiede di guardare avanti, quindi la baseline li puo'
    rispettare restando quello che e'.
    """
    ordine = ConstraintSet(
        constraint_set_id="CS_DIVIETI",
        source_text="il Canadair non sull'incendio in Sila, gli aerei fino alle otto",
        constraints=[
            ForbidAssignment(
                constraint_id="F1",
                vehicle_selector=Selector(
                    field=SelectorField.VEHICLE_ID, values=["CAN_01"]
                ),
                fire_selector=Selector(
                    field=SelectorField.FIRE_ID, values=["F_SILA"]
                ),
            ),
            TimeWindow(
                constraint_id="W1",
                selector=Selector(
                    field=SelectorField.VEHICLE_DOMAIN, values=["air"]
                ),
                from_minute=6 * 60,
                to_minute=20 * 60,
            ),
        ],
    )
    plan = plan_nearest(scenario_minimo, ordine)
    coppie = {(a.vehicle_id, a.fire_id) for a in plan.assignments}
    assert ("CAN_01", "F_SILA") not in coppie

    report = _validate_plan_or_skip(plan, scenario_minimo, ordine)
    assert _verdetto(report) is not False, f"piano respinto: {report}"


def test_i_vincoli_duri_che_la_baseline_non_applica_sono_dichiarati(scenario_minimo):
    """La scelta di lasciar cadere i vincoli di risultato va pagata alla luce del sole.

    MAX_RESPONSE_TIME impone una proprieta' del piano finito, e per rispettarlo
    bisogna scegliere le assegnazioni tutte insieme: e' esattamente cio' che la
    baseline non fa. La posizione presa e' che non lo applichi, ma allora deve
    dirlo nelle note, altrimenti chi legge il confronto non distingue una
    politica dichiarata da un difetto e la baseline resta zavorrata di nascosto.
    """
    ordine = ConstraintSet(
        constraint_set_id="CS_RISULTATO",
        source_text="tutti sul posto entro venti minuti",
        constraints=[MaxResponseTime(constraint_id="T1", minutes=20)],
    )
    plan = plan_nearest(scenario_minimo, ordine)
    note = " | ".join(plan.notes)
    assert "MAX_RESPONSE_TIME" in note
    assert "T1" in note


def _ordine_che_morde(kind: ConstraintKind, scenario: Scenario):
    """Per ciascun divieto del gruppo 1, un ordine che restringe davvero.

    Restituisce l'ordine tradotto e la proprieta' che il piano deve mostrare
    quando l'ordine e' stato applicato. Non c'e' un ramo di riserva, ed e'
    voluto: un divieto aggiunto a `ENFORCED_HARD_KINDS` senza una prova qui fa
    fallire il test invece di passare in silenzio, che e' esattamente il
    servizio che questa funzione deve rendere.
    """
    if kind is ConstraintKind.RESERVE_VEHICLE:
        elicotteri = {
            v.vehicle_id
            for v in scenario.vehicles
            if v.kind is VehicleKind.ELICOTTERO_MEDIO
        }
        ordine = ConstraintSet(
            constraint_set_id="CS_G1_RISERVA",
            constraints=[
                ReserveVehicle(
                    constraint_id="G1",
                    selector=Selector(
                        field=SelectorField.VEHICLE_KIND, values=["elicottero_medio"]
                    ),
                )
            ],
        )
        return ordine, lambda p: elicotteri.isdisjoint(
            {a.vehicle_id for a in p.assignments}
        )

    if kind is ConstraintKind.EXCLUDE_VEHICLE:
        ordine = ConstraintSet(
            constraint_set_id="CS_G1_ESCLUSO",
            constraints=[
                ExcludeVehicle(
                    constraint_id="G1",
                    selector=Selector(
                        field=SelectorField.VEHICLE_ID, values=["CAN_01"]
                    ),
                )
            ],
        )
        return ordine, lambda p: "CAN_01" not in {a.vehicle_id for a in p.assignments}

    if kind is ConstraintKind.TIME_WINDOW:
        # Lo scenario comincia alle 11:30, quindi la chiusura delle 14:00 cade
        # al minuto 150 del piano, rientro compreso.
        ordine = ConstraintSet(
            constraint_set_id="CS_G1_ORARIO",
            constraints=[
                TimeWindow(
                    constraint_id="G1",
                    selector=Selector(field=SelectorField.ALL),
                    from_minute=6 * 60,
                    to_minute=14 * 60,
                )
            ],
        )
        return ordine, lambda p: all(
            a.release_minute <= 150.0 + 1e-6 for a in p.assignments
        )

    if kind is ConstraintKind.FORBID_ASSIGNMENT:
        ordine = ConstraintSet(
            constraint_set_id="CS_G1_COPPIA",
            constraints=[
                ForbidAssignment(
                    constraint_id="G1",
                    vehicle_selector=Selector(
                        field=SelectorField.VEHICLE_ID, values=["CAN_01"]
                    ),
                    fire_selector=Selector(
                        field=SelectorField.FIRE_ID, values=["F_SILA"]
                    ),
                )
            ],
        )
        return ordine, lambda p: ("CAN_01", "F_SILA") not in {
            (a.vehicle_id, a.fire_id) for a in p.assignments
        }

    raise AssertionError(
        f"{kind.value} e' dichiarato fra i divieti applicati ma qui non c'e' "
        f"nessuna prova che lo verifichi: o si scrive la prova, o il vincolo "
        f"non appartiene a ENFORCED_HARD_KINDS"
    )


def test_i_due_elenchi_di_vincoli_duri_non_si_sovrappongono():
    """Un vincolo non puo' essere insieme applicato e dichiarato non applicato.

    I due elenchi sono la politica del sottosistema scritta in forma di codice
    (gruppo 1 e gruppo 3 del docstring del modulo). Se un giorno lo stesso
    `kind` finisse in entrambi, le note del piano direbbero al lettore del
    confronto il contrario di quello che il ciclo avido fa, ed e' il genere di
    incoerenza che nessun altro test guarderebbe.
    """
    assert set(ENFORCED_HARD_KINDS).isdisjoint(set(UNENFORCED_HARD_KINDS))
    assert len(set(ENFORCED_HARD_KINDS)) == len(ENFORCED_HARD_KINDS)
    assert len(set(UNENFORCED_HARD_KINDS)) == len(UNENFORCED_HARD_KINDS)


@pytest.mark.parametrize("kind", ENFORCED_HARD_KINDS, ids=lambda k: k.value)
def test_ogni_divieto_dichiarato_applicato_e_davvero_applicato(kind, scenario_minimo):
    """`ENFORCED_HARD_KINDS` dichiara quali divieti la baseline rispetta: qui si
    verifica che li rispetti davvero, uno per uno.

    La costante era documentazione e basta: nessuna riga del modulo la
    consultava, quindi politica e implementazione potevano divergere senza che
    niente se ne accorgesse. E' proprio il modo in cui il difetto trovato nella
    prova end to end era passato inosservato: il docstring diceva che i quattro
    divieti del gruppo 1 entravano nel ciclo avido, e il ciclo avido non li
    guardava. Un vincolo che il codice non consulta e' un vincolo non
    applicato, per quanto bene sia descritto.

    Ogni caso si misura due volte. Prima senza ordine, per accertare che la
    baseline farebbe davvero la cosa vietata: senza questo controllo il test
    passerebbe anche su un pianificatore che non assegna niente, e non
    verificherebbe piu' niente. Poi con l'ordine, e il verdetto finale non e'
    del test ma del validatore indipendente, che ricalcola tutto da capo.
    """
    ordine, rispettato = _ordine_che_morde(kind, scenario_minimo)

    senza_ordine = plan_nearest(scenario_minimo)
    assert senza_ordine.assignments
    assert not rispettato(senza_ordine), (
        f"senza l'ordine la baseline rispetta gia' {kind.value}: la prova e' "
        f"vacua e non accorgerebbe di una regressione"
    )

    con_ordine = plan_nearest(scenario_minimo, ordine)
    assert rispettato(con_ordine), f"il divieto {kind.value} non e' stato applicato"

    report = _validate_plan_or_skip(con_ordine, scenario_minimo, ordine)
    bloccanti = [
        v for v in getattr(report, "violations", []) if getattr(v, "is_blocking", False)
    ]
    assert not bloccanti, f"violazioni bloccanti: {bloccanti}"



def test_la_rigiocata_non_produce_piani_respinti(contesto, instant, tmp_path):
    """Lo stesso controllo, ma su tutta la rigiocata e con lo stato che si accumula."""
    pytest.importorskip(
        "tagliafuoco.validate.validator",
        reason="il validatore indipendente non e' ancora disponibile",
    )
    result = replay_season(
        instant,
        instant + timedelta(hours=12),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        output_dir=tmp_path,
    )
    respinti = [o for o in result.outcomes if o.valid is False]
    assert not respinti, [o.validation["report"] for o in respinti]
    assert result.aggregate()["plans_validated"] == result.windows_count


# --------------------------------------------------------------------------
# 3. Lo stato non sanguina
# --------------------------------------------------------------------------


def test_advance_restituisce_uno_stato_nuovo(contesto, instant):
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    prima = state.to_dict()

    window = build_windows(instant, instant + timedelta(hours=3), window_hours=3)[0]
    scenario = build_scenario(window, contesto, state)
    plan = plan_nearest(scenario)
    dopo = state.advance(
        plan, scenario, window_minutes=window.duration_minutes, next_timestamp=window.end
    )

    assert dopo is not state
    assert state.to_dict() == prima, "advance ha modificato lo stato di partenza"
    assert dopo.timestamp == window.end


def test_il_turno_riparte_al_cambio_di_giornata(contesto, instant):
    """`max_shift_minutes` e' un limite della giornata dell'equipaggio, non della
    finestra: a mezzanotte il contatore riparte."""
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    window = build_windows(instant, instant + timedelta(hours=3), window_hours=3)[0]
    scenario = build_scenario(window, contesto, state)
    plan = plan_nearest(scenario)
    assert plan.assignments, "senza assegnazioni il test non verifica niente"
    impiegato = plan.assignments[0].vehicle_id

    stessa_giornata = state.advance(
        plan, scenario, window_minutes=180.0, next_timestamp=window.end
    )
    assert stessa_giornata.vehicles[impiegato].minutes_flown_today > 0.0

    giorno_dopo = state.advance(
        plan,
        scenario,
        window_minutes=180.0,
        next_timestamp=window.start + timedelta(days=1),
    )
    assert giorno_dopo.vehicles[impiegato].minutes_flown_today == 0.0


def test_lo_stato_passa_alla_finestra_successiva(contesto, instant, tmp_path):
    result = replay_season(
        instant,
        instant + timedelta(hours=6),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        max_windows=2,
        output_dir=tmp_path,
    )
    impiegati = {a.vehicle_id for a in result.outcomes[0].plan.assignments}
    assert impiegati, "senza assegnazioni il test non verifica niente"

    stato_dopo = {
        v["vehicle_id"]: v for v in result.outcomes[0].state_after["vehicles"]
    }
    for vehicle_id in impiegati:
        assert stato_dopo[vehicle_id]["minutes_flown_today"] > 0.0


def test_due_rigiocate_identiche_danno_lo_stesso_risultato(contesto, instant, tmp_path):
    """Determinismo: e' la precondizione del confronto con la baseline."""
    stato_iniziale = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    impronta = stato_iniziale.to_dict()

    prima = replay_season(
        instant,
        instant + timedelta(hours=9),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        initial_state=stato_iniziale,
        output_dir=tmp_path,
    )
    seconda = replay_season(
        instant,
        instant + timedelta(hours=9),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        initial_state=stato_iniziale,
        output_dir=tmp_path,
    )

    assert stato_iniziale.to_dict() == impronta, "la rigiocata ha sporcato lo stato"
    assert prima.to_dict(include_timings=False) == seconda.to_dict(include_timings=False)


def test_la_baseline_e_il_confronto_partono_dallo_stesso_stato(contesto, instant, tmp_path):
    """Due pianificatori diversi sullo stesso stato iniziale devono vedere gli
    stessi scenari: se cambiano i focolai visti, il confronto e' falsato."""
    stato = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    a = replay_season(
        instant,
        instant + timedelta(hours=9),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        initial_state=stato,
        output_dir=tmp_path,
    )
    b = replay_season(
        instant,
        instant + timedelta(hours=9),
        plan_priority,
        context=contesto,
        window_hours=WINDOW_HOURS,
        initial_state=stato,
        output_dir=tmp_path,
    )
    assert [o.fires_total for o in a.outcomes] == [o.fires_total for o in b.outcomes]
    assert [o.scenario_id for o in a.outcomes] == [o.scenario_id for o in b.outcomes]
    assert a.producer != b.producer


# --------------------------------------------------------------------------
# 4. La rigiocata termina e produce numeri
# --------------------------------------------------------------------------


def test_rigiocata_corta_termina_e_produce_metriche(contesto, instant, tmp_path):
    result = replay_season(
        instant,
        instant + timedelta(hours=9),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        label="test_baseline",
        output_dir=tmp_path,
    )

    assert result.windows_count == 3
    for outcome in result.outcomes:
        assert outcome.metrics is not None
        assert outcome.metrics.fires_total == outcome.fires_total
        _assert_piano_coerente(outcome.plan, build_scenario_for(outcome, contesto))

    agg = result.aggregate()
    assert agg["windows"] == 3
    assert agg["fires_total"] > 0
    assert 0.0 <= agg["coverage_ratio"] <= 1.0
    assert agg["total_vehicle_minutes"] >= 0.0
    assert result.summary_lines()


def build_scenario_for(outcome, contesto):
    """Ricostruisce lo scenario di una finestra per riverificarne il piano.

    Si riparte dallo stato iniziale della finestra, che il registro conserva,
    cosi' il controllo non dipende dall'ordine in cui girano i test.
    """
    state = WorldState(
        timestamp=outcome.window.start,
        vehicles={},
        shift_day=outcome.window.start.date(),
    )
    return build_scenario(outcome.window, contesto, state)


def test_il_registro_finisce_in_json(contesto, instant, tmp_path):
    result = replay_season(
        instant,
        instant + timedelta(hours=6),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        label="baseline_nearest",
        output_dir=tmp_path,
    )
    assert result.output_path is not None
    assert result.output_path.exists()
    assert result.output_path.name.startswith("replay_baseline_nearest_")

    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["producer"] == PRODUCER_NEAREST
    assert len(payload["windows"]) == 2
    assert "coverage_ratio" in payload["aggregate"]


def test_una_sola_finestra_per_il_debug(contesto, instant, tmp_path):
    result = replay_season(
        instant,
        instant + timedelta(hours=9),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        max_windows=1,
        save=False,
        output_dir=tmp_path,
    )
    assert result.windows_count == 1
    assert result.output_path is None


def test_replay_window_si_puo_invocare_da_sola(contesto, instant):
    window = build_windows(instant, instant + timedelta(hours=3), window_hours=3)[0]
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    outcome, dopo = replay_window(window, plan_nearest, contesto, state)
    assert outcome.plan.producer == PRODUCER_NEAREST
    assert dopo.timestamp == window.end


def test_intervallo_vuoto_solleva_un_errore_parlante(contesto, instant):
    with pytest.raises((ReplayError, TimelineError)):
        replay_season(
            instant,
            instant,
            plan_nearest,
            context=contesto,
            window_hours=WINDOW_HOURS,
            save=False,
        )


def test_il_contesto_si_costruisce_dai_dati_statici(fires_scaglionati, instant, tmp_path):
    """Senza risorse esplicite il motore legge basi, mezzi e acqua dal disco.

    E' la prova che la rigiocata gira come la userebbe chi clona il repository:
    senza chiavi, senza rete e senza costruire la flotta a mano.
    """
    from tagliafuoco.domain import registry

    if not registry.static_data_available():
        pytest.skip("i dati statici non sono ancora stati generati")

    contesto = build_context(fires=fires_scaglionati)
    assert contesto.vehicles and contesto.bases and contesto.water_sources

    result = replay_season(
        instant,
        instant + timedelta(hours=6),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        output_dir=tmp_path,
    )
    assert result.windows_count == 2
    assert result.aggregate()["fires_total"] > 0


def test_metriche_di_ripiego_contano_quel_che_dicono(scenario_minimo):
    plan = plan_nearest(scenario_minimo)
    metrics = basic_metrics(plan, scenario_minimo)

    serviti = {a.fire_id for a in plan.assignments}
    assert metrics.fires_total == len(scenario_minimo.fires)
    assert metrics.fires_covered == len(serviti)
    assert metrics.vehicles_used == len({a.vehicle_id for a in plan.assignments})
    assert metrics.water_demanded_liters == sum(
        f.water_demand_liters for f in scenario_minimo.fires
    )
    # L'acqua consegnata si tronca al fabbisogno: un eccesso su un focolaio non
    # deve compensare una mancanza su un altro.
    assert metrics.water_delivered_liters <= metrics.water_demanded_liters
    assert 0.0 <= metrics.coverage_ratio <= 1.0
    if serviti:
        assert metrics.mean_response_minutes > 0.0
        assert metrics.p90_response_minutes >= metrics.mean_response_minutes - 1e-6
    assert math.isfinite(metrics.total_cost_eur)


# --------------------------------------------------------------------------
# 5. Casi limite del tempo e della visibilita'
#
# Un parametro sbagliato qui non produce un errore: produce numeri plausibili e
# sbagliati (una stagione senza incendi, un piano sempre vuoto, una rigiocata
# che non finisce mai). Sono i casi che nessuno prova a mano perche' "non
# capitano", ed e' esattamente il motivo per cui hanno un test.
# --------------------------------------------------------------------------


def test_una_finestra_sola_quando_l_intervallo_e_piu_corto_del_passo(instant):
    """L'ultima finestra si taglia a `end` invece di sconfinare oltre l'esperimento."""
    windows = build_windows(
        instant, instant + timedelta(hours=1), window_hours=WINDOW_HOURS
    )
    assert len(windows) == 1
    assert windows[0].end == instant + timedelta(hours=1)
    assert windows[0].duration_minutes == pytest.approx(60.0)


def test_orizzonte_non_positivo_e_un_errore(instant):
    """Un orizzonte nullo non sbaglia rumorosamente: produce piani vuoti a ogni
    finestra, cioe' una copertura zero che si legge come un risultato."""
    for horizon in (0, -240):
        with pytest.raises(TimelineError):
            build_windows(
                instant, instant + timedelta(hours=9), horizon_minutes=horizon
            )


def test_un_passo_troppo_fine_viene_rifiutato_prima_di_allocare(instant):
    """Minuti scambiati per ore: senza controllo la chiamata riempie la memoria
    invece di dire quale parametro e' sbagliato."""
    with pytest.raises(TimelineError):
        build_windows(instant, instant + timedelta(days=120), window_hours=0.001)


def test_una_stagione_intera_resta_dentro_il_tetto_delle_finestre(instant):
    """Il controllo precedente non deve vietare l'esperimento vero: la stagione
    2025 a finestre di tre ore sta larghissima dentro il tetto."""
    windows = build_windows(
        instant, instant + timedelta(days=122), window_hours=WINDOW_HOURS
    )
    assert len(windows) == 976


def test_finestra_con_estremi_invertiti_e_un_errore(instant):
    """Anche costruita a mano, una finestra che finisce prima di cominciare non
    deve entrare in circolo: propagherebbe durate negative fino allo stato."""
    with pytest.raises(TimelineError):
        ReplayWindow(index=0, start=instant, end=instant - timedelta(minutes=1))


def test_la_finestra_normalizza_gli_estremi_ingenui(instant):
    """Le date scritte a mano sono spesso senza fuso: il confronto non deve
    esplodere con "can't compare offset-naive and offset-aware datetimes"."""
    naive = instant.replace(tzinfo=None)
    window = ReplayWindow(index=0, start=naive, end=naive + timedelta(hours=3))
    assert window.start.tzinfo is not None
    assert window.contains(instant) is True
    # Estremo destro escluso: la finestra successiva comincia esattamente li',
    # e un istante non puo' appartenere a due finestre.
    assert window.contains(instant + timedelta(hours=3)) is False


def test_visibilita_su_elenchi_vuoti(instant):
    """Nessun rilevamento e nessun focolaio non sono un errore: sono una finestra
    tranquilla, e devono dare una lista vuota senza sollevare."""
    assert visible_detections([], instant) == []
    assert visible_fires([], instant) == []


def test_un_solo_rilevamento_e_visibile_nell_istante_esatto(instant):
    """Il confronto e' `<=`: il rilevamento acquisito adesso e' gia' noto."""
    det = Detection(
        detection_id="D",
        point=GeoPoint(lat=39.0, lon=16.0),
        acquired_at=instant,
        satellite="N",
        instrument="VIIRS",
        confidence=80.0,
        frp_mw=10.0,
    )
    assert [d.detection_id for d in visible_detections([det], instant)] == ["D"]
    assert visible_detections([det], instant - timedelta(seconds=1)) == []


def test_un_lookback_non_positivo_tiene_tutto_il_passato(instant):
    """Zero o negativo vuol dire "nessun limite all'indietro".

    Prima un valore negativo metteva il limite nel futuro e scartava in silenzio
    ogni rilevamento: una stagione intera che spariva senza un messaggio, e che
    si leggeva come una stagione senza incendi.
    """
    vecchio = Detection(
        detection_id="OLD",
        point=GeoPoint(lat=39.0, lon=16.0),
        acquired_at=instant - timedelta(days=30),
        satellite="N",
        instrument="VIIRS",
        confidence=80.0,
        frp_mw=10.0,
    )
    for lookback in (0.0, -12.0):
        visti = visible_detections([vecchio], instant, lookback_hours=lookback)
        assert [d.detection_id for d in visti] == ["OLD"]


def test_i_confini_della_notte_sono_chiusi_a_sinistra_e_aperti_a_destra():
    """Alba e tramonto sono in ora locale (+60 minuti su UTC): il confine cade
    alle 04:30Z e alle 19:30Z.

    Un errore di un minuto qui si vede su una finestra ogni tanto ed e' il tipo
    di differenza che si scambia per rumore, quindi il confine va fissato.
    """

    def z(hh: int, mm: int) -> datetime:
        return datetime(2025, 8, 12, hh, mm, tzinfo=timezone.utc)

    assert is_night_at(z(4, 29)) is True
    assert is_night_at(z(4, 30)) is False  # 05:30 locali: si vola gia'
    assert is_night_at(z(19, 29)) is False
    assert is_night_at(z(19, 30)) is True  # 20:30 locali: non si vola piu'


def test_span_of_su_sequenza_vuota_e_su_finestre_vere(instant):
    """Su una sequenza vuota si restituisce (None, None) invece di sollevare:
    chi stampa un riassunto non deve avvolgere la chiamata in un try."""
    assert span_of([]) == (None, None)
    windows = build_windows(
        instant, instant + timedelta(hours=9), window_hours=WINDOW_HOURS
    )
    assert span_of(windows) == (instant, instant + timedelta(hours=9))


def test_un_focolaio_senza_date_resta_visibile(fire_sila, instant):
    """Non si sa quando sia nato: scartarlo farebbe sparire un incendio vero
    dall'esperimento per un dato mancante, che e' la colpa sbagliata."""
    muto = copy.deepcopy(fire_sila)
    muto.fire_id = "F_MUTO"
    muto.first_seen = None
    muto.last_seen = None
    muto.detections = []
    assert [f.fire_id for f in visible_fires([muto], instant)] == ["F_MUTO"]


# --------------------------------------------------------------------------
# 6. Casi limite della baseline
#
# Il valore di una baseline sta nel non essere zoppa per costruzione. Questi
# test fissano che cosa fa quando i dati sono al limite, perche' la differenza
# fra "non assegna perche' non puo'" e "non assegna per un difetto" e'
# esattamente cio' che il confronto con il solver deve misurare.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("planner", [plan_nearest, plan_priority])
def test_uno_scenario_senza_focolai_da_un_piano_vuoto(planner, scenario_minimo):
    """Nessun incendio e' un esito legittimo di una finestra, non un errore.

    Conta anche che le metriche di ripiego non dividano per zero: su una
    stagione lunga le finestre senza incendi sono la maggioranza.
    """
    vuoto = copy.deepcopy(scenario_minimo)
    vuoto.fires = []
    plan = planner(vuoto)
    assert plan.assignments == []
    assert plan.unassigned_fire_ids == []
    assert plan.horizon_minutes == vuoto.horizon_minutes
    _assert_piano_coerente(plan, vuoto)

    metrics = basic_metrics(plan, vuoto)
    assert metrics.fires_total == 0
    assert metrics.coverage_ratio == 0.0
    assert metrics.mean_response_minutes == 0.0
    assert metrics.p90_response_minutes == 0.0


def test_senza_mezzi_utilizzabili_tutti_i_focolai_sono_dichiarati_non_serviti(
    scenario_minimo,
):
    """Nessun focolaio deve sparire in silenzio: o e' servito o e' dichiarato non
    servito, e la somma dei due insiemi e' lo scenario."""
    fermo = copy.deepcopy(scenario_minimo)
    for v in fermo.vehicles:
        v.status = VehicleStatus.MAINTENANCE
    plan = plan_nearest(fermo)
    assert plan.assignments == []
    assert plan.unassigned_fire_ids == sorted(f.fire_id for f in fermo.fires)
    _assert_piano_coerente(plan, fermo)


def test_uno_scenario_senza_nessun_mezzo_non_fa_esplodere_il_piano(scenario_minimo):
    """Flotta vuota: e' l'ablazione spinta al limite, e deve restare eseguibile."""
    senza = copy.deepcopy(scenario_minimo)
    senza.vehicles = []
    plan = plan_nearest(senza)
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {f.fire_id for f in senza.fires}


@pytest.mark.parametrize("tetto", [0, -3])
def test_un_tetto_di_zero_mezzi_per_focolaio_non_assegna_niente(tetto, scenario_minimo):
    """Zero vuol dire "non mandare nessuno" e un tetto negativo si normalizza a
    zero: un parametro assurdo non deve produrre in silenzio numeri che
    sembrano un risultato."""
    plan = plan_nearest(scenario_minimo, max_vehicles_per_fire=tetto)
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {f.fire_id for f in scenario_minimo.fires}
    assert any("Tetto di mezzi per focolaio: 0" in n for n in plan.notes)


def test_un_solo_mezzo_per_focolaio_e_un_tetto_rispettato(scenario_minimo):
    """Il valore al limite inferiore utile: uno."""
    plan = plan_nearest(scenario_minimo, max_vehicles_per_fire=1)
    per_focolaio: dict[str, int] = {}
    for a in plan.assignments:
        per_focolaio[a.fire_id] = per_focolaio.get(a.fire_id, 0) + 1
    assert per_focolaio, "senza assegnazioni il test non verifica niente"
    assert max(per_focolaio.values()) == 1


@pytest.mark.parametrize(
    "campo, valore", [("water_capacity_liters", 0), ("cruise_speed_kmh", 0.0)]
)
def test_un_mezzo_senza_capacita_o_senza_velocita_non_viene_impiegato(
    campo, valore, scenario_minimo
):
    """Sono due divisioni per zero travestite da dati.

    Con capacita' zero il piano dichiarerebbe litri che il mezzo non ha e il
    validatore lo respingerebbe; con velocita' zero il tempo di volo non
    esiste. In entrambi i casi non assegnare il mezzo e' l'unica risposta
    onesta: una guardia che sostituisce il valore con uno inventato fa passare
    il calcolo e sposta la bugia piu' avanti.
    """
    rotto = copy.deepcopy(scenario_minimo)
    for v in rotto.vehicles:
        setattr(v, campo, valore)
    plan = plan_nearest(rotto)
    assert plan.assignments == []


def test_senza_sorgente_utilizzabile_il_mezzo_non_parte(scenario_minimo):
    """Un mezzo che deve pescare e non ha dove pescare non e' impiegabile: meglio
    dichiararlo non servito che consegnare un piano che il validatore respinge."""
    asciutto = copy.deepcopy(scenario_minimo)
    asciutto.water_sources = [
        WaterSource(
            source_id="WS_SOLO_AUTOBOTTI",
            name="Idrante riservato",
            point=GeoPoint(lat=38.92, lon=16.15),
            kind=WaterSourceKind.HYDRANT,
            usable_by=[VehicleKind.AUTOBOTTE],
        )
    ]
    plan = plan_nearest(asciutto)
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {f.fire_id for f in asciutto.fires}


def test_l_ordine_di_ingresso_dei_focolai_non_cambia_il_piano(scenario_minimo):
    """Determinismo: il piano dipende dai dati, non da come e' costruita la lista.

    Senza uno spareggio esplicito nelle chiavi di ordinamento (focolai per
    `fire_id`, candidati per distanza e `vehicle_id`) due rigiocate della stessa
    stagione darebbero piani diversi, e il confronto con il solver smetterebbe
    di essere riproducibile.
    """
    rovesciato = copy.deepcopy(scenario_minimo)
    rovesciato.fires = list(reversed(rovesciato.fires))
    rovesciato.vehicles = list(reversed(rovesciato.vehicles))

    def impronta(plan: Plan) -> list[tuple]:
        return [
            (a.vehicle_id, a.fire_id, a.departure_minute, a.release_minute, a.sorties)
            for a in plan.assignments
        ]

    assert impronta(plan_nearest(scenario_minimo)) == impronta(plan_nearest(rovesciato))
    assert impronta(plan_priority(scenario_minimo)) == impronta(
        plan_priority(rovesciato)
    )


def test_un_focolaio_senza_data_finisce_in_fondo_alla_coda(scenario_minimo, instant):
    """Una data mancante non si inventa: il focolaio va servito per ultimo, non
    per primo come farebbe un `None` letto come zero."""
    sc = copy.deepcopy(scenario_minimo)
    sc.fires[1].first_seen = None
    sc.fires[1].last_seen = None
    plan = plan_nearest(sc, max_vehicles_per_fire=1)
    serviti = [a.fire_id for a in plan.assignments]
    assert serviti, "senza assegnazioni il test non verifica niente"
    assert serviti[0] == sc.fires[0].fire_id


# --- il tempo fra due missioni e il punto di partenza ----------------------


def _scenario_due_focolai_vicini(
    instant, base_lamezia, sea_source, canadair
) -> Scenario:
    """Due focolai a poche centinaia di metri l'uno dall'altro, un solo mezzo.

    Serve a rendere osservabile il tempo a terra fra due missioni: il volo fra i
    due focolai e' quasi nullo, quindi l'unico distacco possibile e' il
    riattrezzamento della base. Con due focolai lontani il test passerebbe anche
    con la regola sbagliata, e non verificherebbe niente.
    """

    def fuoco(fire_id: str, lat: float, lon: float, minuti: int) -> FireCluster:
        seen = instant + timedelta(minutes=minuti)
        det = Detection(
            detection_id=f"{fire_id}_D",
            point=GeoPoint(lat=lat, lon=lon),
            acquired_at=seen,
            satellite="N",
            instrument="VIIRS",
            confidence=90.0,
            frp_mw=30.0,
        )
        return FireCluster(
            fire_id=fire_id,
            centroid=det.point,
            detections=[det],
            first_seen=seen,
            last_seen=seen,
            area_km2=0.2,
            water_demand_liters=6000,
            base_priority=0.5,
        )

    return Scenario(
        scenario_id="SC_VICINI",
        timestamp=instant,
        horizon_minutes=240,
        fires=[fuoco("F_UNO", 38.95, 16.30, 0), fuoco("F_DUE", 38.951, 16.301, 1)],
        vehicles=[copy.deepcopy(canadair)],
        bases=[base_lamezia],
        water_sources=[sea_source],
        wind_kmh=15.0,
    )


def test_fra_due_missioni_consecutive_c_e_il_riattrezzamento_della_base(
    instant, base_lamezia, sea_source, canadair
):
    """SPEC.md sezione 6, punto 2: `departure(k+1) >= release(k) + turnaround`.

    Non si pretende invece il volo fra i due focolai: quella lettura contava due
    volte lo stesso trasferimento, perche' la missione nuova parte gia' dalla
    base. Il validatore controlla il tempo a terra come bloccante, quindi un
    piano che lo salta esce dal confronto.
    """
    scenario = _scenario_due_focolai_vicini(instant, base_lamezia, sea_source, canadair)
    plan = plan_nearest(scenario, max_vehicles_per_fire=1)
    sequenza = plan.assignments_for_vehicle(canadair.vehicle_id)
    assert len(sequenza) == 2, "il test ha senso solo se il mezzo vola due volte"
    gap = sequenza[1].departure_minute - sequenza[0].release_minute
    assert gap + 1e-6 >= base_lamezia.turnaround_minutes
    _assert_piano_coerente(plan, scenario)
    assert _verdetto(_validate_plan_or_skip(plan, scenario)) is not False


def test_un_mezzo_lontano_da_casa_non_produce_un_piano_respinto(
    instant, base_lamezia, sea_source, canadair
):
    """Regressione: il punto di partenza e' uno solo per tutto il piano.

    Quando il mezzo dichiara una posizione diversa dalla sua base (e' il caso
    normale dello stato ereditato da una finestra precedente), la seconda
    missione veniva calcolata come se partisse da casa, mentre il validatore
    continuava a misurarla dalla posizione dichiarata. Con la base piu' vicina
    ai focolai del punto di partenza, il piano annunciava un arrivo piu' rapido
    del minimo fisico e veniva respinto in blocco: la baseline usciva dal
    confronto per un disaccordo di convenzione, non per la sua strategia.
    """
    scenario = _scenario_due_focolai_vicini(instant, base_lamezia, sea_source, canadair)
    # La posizione dichiarata e' a un centinaio di chilometri dai focolai,
    # mentre la base di casa ne dista sette. Il divario non e' decorativo: la
    # scorciatoia si vede solo se il volo dalla base e' piu' breve del volo dal
    # punto di partenza di un margine maggiore del tempo di approntamento, che
    # altrimenti la nasconde. Con la base a sette chilometri il vecchio calcolo
    # dichiarava 13.3 minuti di risposta contro un minimo fisico di 17.6, ed era
    # una violazione bloccante.
    scenario.vehicles[0].current_position = GeoPoint(lat=39.80, lon=16.60)
    plan = plan_nearest(scenario, max_vehicles_per_fire=1)
    assert len(plan.assignments) == 2, "il test ha senso solo con due missioni"

    # Nessuna missione puo' arrivare prima del volo dal punto di partenza
    # dichiarato: e' lo stesso minimo che il validatore ricalcola per conto suo.
    mezzo = scenario.vehicles[0]
    for a in plan.assignments:
        fuoco = scenario.fire_by_id(a.fire_id)
        minimo = reposition_minutes(mezzo, mezzo.current_position, fuoco.centroid)
        assert a.response_minutes + 1e-6 >= minimo

    assert _verdetto(_validate_plan_or_skip(plan, scenario)) is not False


# --- vincoli duri del gruppo 1 --------------------------------------------


def test_la_baseline_rispetta_la_finestra_oraria_del_coordinatore(scenario_minimo):
    """TIME_WINDOW e' un divieto, non un obiettivo: si rispetta senza pianificare.

    `from_minute` e `to_minute` sono orari (minuti dalla mezzanotte), i tempi del
    piano sono minuti dall'inizio dell'orizzonte: la conversione passa da
    `scenario.timestamp`. Lo scenario comincia alle 11:30, quindi una chiusura
    alle 14:00 cade al minuto 150 del piano, rientro compreso.
    """
    constraints = ConstraintSet(
        constraint_set_id="CS_ORARIO",
        constraints=[
            TimeWindow(
                constraint_id="T1",
                selector=Selector(field=SelectorField.ALL),
                from_minute=6 * 60,
                to_minute=14 * 60,
            )
        ],
    )
    plan = plan_nearest(scenario_minimo, constraints)
    assert plan.assignments, "senza assegnazioni il test non verifica niente"
    for a in plan.assignments:
        assert a.release_minute <= 150.0 + 1e-6
    assert any("Finestre orarie applicate" in n for n in plan.notes)
    assert _verdetto(_validate_plan_or_skip(plan, scenario_minimo)) is not False


def test_una_finestra_oraria_che_chiude_subito_ferma_il_mezzo(scenario_minimo):
    """Una finestra che si chiude un quarto d'ora dopo l'inizio del piano non
    lascia spazio a nessuna missione: il mezzo non si impiega. Non e' un difetto,
    e' il modo in cui una sala operativa rispetta un'ora di chiusura."""
    constraints = ConstraintSet(
        constraint_set_id="CS_CHIUSO",
        constraints=[
            TimeWindow(
                constraint_id="T2",
                selector=Selector(field=SelectorField.ALL),
                from_minute=6 * 60,
                to_minute=11 * 60 + 45,
            )
        ],
    )
    plan = plan_nearest(scenario_minimo, constraints)
    assert plan.assignments == []
    assert set(plan.unassigned_fire_ids) == {f.fire_id for f in scenario_minimo.fires}


def test_la_baseline_rispetta_un_accoppiamento_vietato(scenario_minimo):
    """FORBID_ASSIGNMENT vieta una coppia, non un mezzo: il mezzo resta
    impiegabile altrove. Il validatore lo controlla come bloccante, quindi
    ignorarlo significherebbe consegnare un piano respinto."""
    constraints = ConstraintSet(
        constraint_set_id="CS_COPPIA",
        constraints=[
            ForbidAssignment(
                constraint_id="F1",
                vehicle_selector=Selector(
                    field=SelectorField.VEHICLE_ID, values=["CAN_01"]
                ),
                fire_selector=Selector(field=SelectorField.FIRE_ID, values=["F_SILA"]),
            )
        ],
    )
    plan = plan_nearest(scenario_minimo, constraints)
    coppie = {(a.vehicle_id, a.fire_id) for a in plan.assignments}
    assert ("CAN_01", "F_SILA") not in coppie
    assert any("Accoppiamenti vietati" in n for n in plan.notes)
    assert _verdetto(_validate_plan_or_skip(plan, scenario_minimo)) is not False


def test_la_riserva_con_conteggio_non_ferma_tutta_la_flotta(scenario_minimo):
    """"Tienimene uno di riserva" non e' "fermali tutti".

    Il test verifica la proprieta' che il validatore controlla (almeno un mezzo
    aereo resta fermo) e non quale mezzo venga scelto: quale sia e' una
    convenzione interna, che almeno uno resti fermo e' l'ordine del
    coordinatore.
    """
    constraints = ConstraintSet(
        constraint_set_id="CS_RISERVA",
        constraints=[
            ReserveVehicle(
                constraint_id="R1",
                selector=Selector(field=SelectorField.VEHICLE_DOMAIN, values=["air"]),
                count=1,
            )
        ],
    )
    plan = plan_nearest(scenario_minimo, constraints)
    usati = {a.vehicle_id for a in plan.assignments}
    aerei = {v.vehicle_id for v in scenario_minimo.vehicles if v.domain is Domain.AIR}
    assert len(aerei - usati) >= 1, "nessun mezzo aereo e' rimasto di riserva"
    assert aerei & usati, "la riserva di uno non deve fermare tutta la flotta"
    assert _verdetto(_validate_plan_or_skip(plan, scenario_minimo)) is not False


def test_il_piano_dichiara_i_vincoli_duri_che_non_applica(scenario_minimo):
    """Il prezzo della scelta del gruppo 3 si paga alla luce del sole.

    La baseline non sa garantire un tetto sul tempo di risposta, perche' e' una
    proprieta' del piano finito e non di una singola scelta. Lo dichiara nelle
    note con l'identificativo del vincolo, cosi' chi legge il confronto sa che
    una baseline respinta per quel motivo e' il comportamento atteso e non un
    difetto. Una baseline zavorrata di nascosto falsa il risultato senza
    lasciare traccia.
    """
    constraints = ConstraintSet(
        constraint_set_id="CS_TETTO",
        constraints=[MaxResponseTime(constraint_id="M1", minutes=5)],
    )
    plan = plan_nearest(scenario_minimo, constraints)
    dichiarazione = [n for n in plan.notes if "non applicati" in n]
    assert dichiarazione
    assert "MAX_RESPONSE_TIME M1" in dichiarazione[0]


# --------------------------------------------------------------------------
# 7. Casi limite dello stato e del motore
# --------------------------------------------------------------------------


def test_lo_stato_iniziale_mette_i_mezzi_nella_loro_base(contesto, instant):
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    per_base = {b.base_id: b.point for b in contesto.bases}
    for v in contesto.vehicles:
        st = state.state_for(v.vehicle_id)
        assert st is not None
        assert st.position == per_base[v.home_base_id]
        assert st.available_from_minute == 0.0
        assert st.minutes_flown_today == 0.0
    assert state.busy_vehicle_ids() == []
    assert state.state_for("MEZZO_CHE_NON_ESISTE") is None


def test_advance_con_un_piano_vuoto_sposta_solo_l_orologio(contesto, instant):
    """Una finestra senza incendi non deve consumare turno ne' spostare mezzi."""
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    window = build_windows(instant, instant + timedelta(hours=3), window_hours=3)[0]
    scenario = build_scenario(window, contesto, state)
    vuoto = Plan(plan_id="P_VUOTO", horizon_minutes=scenario.horizon_minutes)

    dopo = state.advance(
        vuoto, scenario, window_minutes=180.0, next_timestamp=window.end
    )
    assert dopo.timestamp == window.end
    for vid, st in dopo.vehicles.items():
        assert st.minutes_flown_today == 0.0
        assert st.available_from_minute == 0.0
        assert st.position == state.vehicles[vid].position


def test_il_riattrezzamento_passa_alla_finestra_successiva(contesto, instant):
    """Due missioni restano consecutive anche a cavallo di due finestre.

    Il confine delle finestre e' una scelta di chi simula, non un fatto del
    mondo: senza questo termine un mezzo rientrato alla fine di una finestra
    ripartirebbe al minuto zero della successiva, cioe' farebbe rifornimento e
    cambio equipaggio in un istante. Il validatore non se ne accorgerebbe,
    perche' guarda un piano alla volta.
    """
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    window = build_windows(instant, instant + timedelta(hours=3), window_hours=3)[0]
    scenario = build_scenario(window, contesto, state)
    plan = plan_nearest(scenario)
    assert plan.assignments, "senza assegnazioni il test non verifica niente"

    impiegato = plan.assignments[0].vehicle_id
    rilascio = max(
        a.release_minute for a in plan.assignments_for_vehicle(impiegato)
    )
    mezzo = scenario.vehicle_by_id(impiegato)
    base = scenario.base_by_id(mezzo.home_base_id)

    # Finestra corta apposta, cosi' il riattrezzamento resta visibile nel
    # riporto invece di essere assorbito dal cambio di origine dei minuti.
    dopo = state.advance(
        plan, scenario, window_minutes=60.0, next_timestamp=window.end
    )
    atteso = rilascio + base.turnaround_minutes - 60.0
    assert dopo.vehicles[impiegato].available_from_minute == pytest.approx(atteso)


def test_un_focolaio_coperto_entra_fra_gli_spenti_e_non_torna(
    contesto, instant, fire_sila
):
    """Semplificazione dichiarata: un focolaio e' spento quando ha ricevuto i
    litri che il modello gli attribuisce. Serve solo a impedire che lo stesso
    focolaio venga ripianificato all'infinito, e vale identica per solver e
    baseline."""
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    window = build_windows(instant, instant + timedelta(hours=3), window_hours=3)[0]
    scenario = build_scenario(window, contesto, state)
    assert scenario.fires, "senza focolai il test non verifica niente"

    bersaglio = scenario.fires[0]
    piano = Plan(
        plan_id="P_PIENO",
        horizon_minutes=scenario.horizon_minutes,
        assignments=[
            Assignment(
                vehicle_id=scenario.vehicles[0].vehicle_id,
                fire_id=bersaglio.fire_id,
                departure_minute=0.0,
                arrival_minute=10.0,
                sorties=1,
                water_delivered_liters=bersaglio.water_demand_liters,
                release_minute=30.0,
            )
        ],
    )
    dopo = state.advance(
        piano, scenario, window_minutes=180.0, next_timestamp=window.end
    )
    assert bersaglio.fire_id in dopo.extinguished_fire_ids
    assert bersaglio.fire_id not in dopo.active_fire_ids

    # E non torna: la finestra successiva non lo deve nemmeno vedere.
    prossima = build_windows(
        instant, instant + timedelta(hours=6), window_hours=3
    )[1]
    assert bersaglio.fire_id not in {
        f.fire_id for f in build_scenario(prossima, contesto, dopo).fires
    }


def test_un_focolaio_non_piu_visibile_non_e_uno_spento(contesto, instant):
    """"Non lo vedo piu'" e "l'ho spento" sono due cose diverse: confonderle
    gonfierebbe la copertura di entrambi i pianificatori."""
    state = WorldState.initial(instant, contesto.vehicles, contesto.bases)
    window = build_windows(instant, instant + timedelta(hours=3), window_hours=3)[0]
    scenario = build_scenario(window, contesto, state)
    vuoto = Plan(plan_id="P_VUOTO", horizon_minutes=scenario.horizon_minutes)
    dopo = state.advance(
        vuoto, scenario, window_minutes=180.0, next_timestamp=window.end
    )
    assert dopo.extinguished_fire_ids == set()
    assert dopo.active_fire_ids == {f.fire_id for f in scenario.fires}


def test_fresh_fleet_snapshot_non_tocca_gli_originali(canadair, base_lamezia):
    """Serve a chi costruisce uno scenario a mano: i mezzi non devono portarsi
    dietro posizione e minuti volati di una simulazione precedente."""
    sporco = copy.deepcopy(canadair)
    sporco.available_from_minute = 42.0
    sporco.minutes_flown_today = 120.0
    sporco.current_position = GeoPoint(lat=39.9, lon=16.9)

    puliti = fresh_fleet_snapshot([sporco], [base_lamezia])
    assert puliti[0].available_from_minute == 0.0
    assert puliti[0].minutes_flown_today == 0.0
    assert puliti[0].current_position == base_lamezia.point
    # L'originale resta com'era: la copia non e' un effetto collaterale.
    assert sporco.available_from_minute == 42.0
    assert sporco.minutes_flown_today == 120.0


def test_fires_still_burning_esclude_gli_spenti(fire_sila, fire_wui, instant):
    state = WorldState(timestamp=instant, extinguished_fire_ids={fire_wui.fire_id})
    rimasti = fires_still_burning([fire_wui, fire_sila], state)
    assert [f.fire_id for f in rimasti] == [fire_sila.fire_id]
    assert fires_still_burning([], state) == []


def test_max_windows_a_zero_e_un_errore(contesto, instant):
    """Con zero finestre non c'e' niente da rigiocare: meglio un errore parlante
    di un risultato vuoto che sembra una stagione senza incendi."""
    with pytest.raises(ReplayError):
        replay_season(
            instant,
            instant + timedelta(hours=9),
            plan_nearest,
            context=contesto,
            window_hours=WINDOW_HOURS,
            max_windows=0,
            save=False,
        )


def test_un_pianificatore_che_non_restituisce_un_piano_e_un_errore(contesto, instant):
    """Un `None` che scivola dentro il registro produrrebbe metriche a zero
    indistinguibili da una finestra senza assegnazioni."""

    def pianificatore_rotto(scenario, constraint_set=None):
        return None

    with pytest.raises(ReplayError):
        replay_season(
            instant,
            instant + timedelta(hours=3),
            pianificatore_rotto,
            context=contesto,
            window_hours=WINDOW_HOURS,
            save=False,
        )


def test_il_motore_accetta_un_pianificatore_che_ignora_i_vincoli(contesto, instant):
    """Il contratto e' `planner(scenario, constraints)`, ma chi i vincoli non li
    usa ha il diritto di non dichiararli: la firma si legge, non si indovina
    intercettando TypeError."""

    def pianificatore_semplice(scenario):
        return Plan(
            plan_id="P_SEMPLICE",
            horizon_minutes=scenario.horizon_minutes,
            producer="finto",
            unassigned_fire_ids=sorted(f.fire_id for f in scenario.fires),
        )

    result = replay_season(
        instant,
        instant + timedelta(hours=3),
        pianificatore_semplice,
        context=contesto,
        window_hours=WINDOW_HOURS,
        save=False,
    )
    assert result.producer == "finto"
    assert result.windows_count == 1


def test_la_rigiocata_non_modifica_il_contesto(contesto, instant, tmp_path):
    """Il contesto e' il modello di partenza e viene riusato dalla rigiocata del
    solver: se una rigiocata lo sporcasse, la successiva partirebbe da dati
    diversi e il confronto non misurerebbe piu' niente."""
    prima_mezzi = copy.deepcopy(contesto.vehicles)
    prima_focolai = copy.deepcopy(contesto.fires)

    replay_season(
        instant,
        instant + timedelta(hours=9),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        output_dir=tmp_path,
    )

    for atteso, trovato in zip(prima_mezzi, contesto.vehicles):
        assert atteso.available_from_minute == trovato.available_from_minute
        assert atteso.minutes_flown_today == trovato.minutes_flown_today
        assert atteso.current_position == trovato.current_position
        assert atteso.status is trovato.status
    for atteso, trovato in zip(prima_focolai, contesto.fires):
        assert atteso.fire_id == trovato.fire_id
        assert atteso.water_demand_liters == trovato.water_demand_liters
        assert atteso.first_seen == trovato.first_seen


def test_un_validatore_che_esplode_non_ferma_la_rigiocata(contesto, instant):
    """Se ne registra il guasto e si prosegue: interrompere farebbe perdere anche
    cio' che era gia' stato calcolato, e una stagione lunga non si rilancia per
    un modulo altrui che solleva."""

    def validatore_rotto(plan, scenario):
        raise RuntimeError("guasto simulato")

    result = replay_season(
        instant,
        instant + timedelta(hours=3),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        validator=validatore_rotto,
        save=False,
    )
    outcome = result.outcomes[0]
    assert outcome.valid is None
    assert "guasto simulato" in str(outcome.validation["report"])
    # Un verdetto sconosciuto non entra nel conteggio: un test verde che non
    # verifica niente e' peggio di un test rosso.
    assert result.aggregate()["plans_validated"] == 0


def test_la_validazione_si_puo_disattivare(contesto, instant):
    """`validator=None` e' "non lo voglio", `validator=AUTO` e' "cercalo tu": la
    sentinella serve a non confondere le due cose."""
    result = replay_season(
        instant,
        instant + timedelta(hours=3),
        plan_nearest,
        context=contesto,
        window_hours=WINDOW_HOURS,
        validator=None,
        save=False,
    )
    validazione = result.outcomes[0].validation
    assert validazione["available"] is False
    assert validazione["ok"] is None


def test_metriche_di_ripiego_su_un_piano_vuoto(scenario_minimo):
    """Zero focolai serviti non deve diventare una divisione per zero."""
    vuoto = Plan(plan_id="P_VUOTO", horizon_minutes=scenario_minimo.horizon_minutes)
    metrics = basic_metrics(vuoto, scenario_minimo)
    assert metrics.fires_covered == 0
    assert metrics.coverage_ratio == 0.0
    assert metrics.mean_response_minutes == 0.0
    assert metrics.p90_response_minutes == 0.0
    assert metrics.vehicles_used == 0
    assert metrics.water_delivered_liters == 0


def test_l_acqua_di_un_focolaio_senza_fabbisogno_non_gonfia_il_rapporto(
    scenario_minimo,
):
    """Il taglio al fabbisogno vale anche quando il fabbisogno e' zero.

    Prima un focolaio non arricchito contribuiva con tutti i suoi litri al
    numeratore e con zero al denominatore, e bastavano due focolai cosi' perche'
    `water_fulfilment_ratio` sbattesse contro il suo tetto di 1.0 e annunciasse
    un fabbisogno soddisfatto per intero dove non era stato coperto niente.
    """
    sc = copy.deepcopy(scenario_minimo)
    for f in sc.fires:
        f.water_demand_liters = 0
    plan = plan_nearest(sc)
    assert plan.assignments, "senza assegnazioni il test non verifica niente"

    metrics = basic_metrics(plan, sc)
    assert metrics.water_demanded_liters == 0
    assert metrics.water_delivered_liters == 0
    assert metrics.water_delivered_liters <= metrics.water_demanded_liters
    # Un focolaio senza fabbisogno resta comunque servito: sono due conti
    # diversi e devono restare diversi.
    assert metrics.fires_covered == len({a.fire_id for a in plan.assignments})


def test_aggregate_di_una_rigiocata_senza_finestre(instant):
    """Un registro vuoto si deve poter riassumere senza sollevare: e' il caso di
    chi confronta due rigiocate e una delle due non ha prodotto niente."""
    result = ReplayResult(
        label="vuota",
        producer="nessuno",
        start=instant,
        end=instant + timedelta(hours=3),
        window_hours=3.0,
        horizon_minutes=240,
    )
    agg = result.aggregate()
    assert agg["windows"] == 0
    assert agg["coverage_ratio"] == 0.0
    assert agg["wui_coverage_ratio"] == 1.0
    assert agg["p90_response_minutes"] == 0.0
    assert agg["plans_validated"] == 0
    assert result.response_minutes_pool() == []
    assert result.summary_lines()


def test_il_nome_del_file_resiste_a_un_etichetta_strana(instant, tmp_path):
    """Il nome dice chi ha pianificato e su quale intervallo: chi trova il file
    fra sei mesi lo capisce senza aprirlo."""
    result = ReplayResult(
        label="Rigiocata *** di prova!!",
        producer="baseline_nearest",
        start=instant,
        end=instant + timedelta(hours=3),
        window_hours=3.0,
        horizon_minutes=240,
    )
    path = save_result(result, output_dir=tmp_path)
    assert path.name == "replay_rigiocata_di_prova_20250812T1130_20250812T1430.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["producer"] == "baseline_nearest"
    assert payload["windows"] == []
