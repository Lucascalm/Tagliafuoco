"""Test del validatore indipendente.

PERCHE' QUESTI TEST SONO SCRITTI COSI'
--------------------------------------
Un validatore che non respinge niente e' indistinguibile da un validatore che
non funziona: entrambi dicono sempre di si'. Quindi qui la prova non e' che il
validatore accetti un piano corretto (quello e' l'ultimo test, ed e' il piu'
facile da superare per sbaglio), ma che RESPINGA piani costruiti a mano per
essere sbagliati, uno per ogni regola, e che dica quale regola ha reagito.

I piani di prova sono scritti a mano e non prodotti dal solver: servono casi
patologici che un solver corretto non genererebbe mai, e servono numeri noti.
Le distanze e i tempi citati nei commenti sono calcolati una volta sulle
fixture di `conftest.py` (Lamezia, Cosenza, Sila, Reggio, Golfo di
Sant'Eufemia): se qualcuno cambia quelle coordinate i test cadono, ed e'
giusto che cadano, perche' i numeri scritti qui non sarebbero piu' veri.
"""

from __future__ import annotations

import ast
import dataclasses
import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from tagliafuoco.domain.models import (
    Assignment,
    Domain,
    FireCluster,
    GeoPoint,
    Plan,
    PlanMetrics,
    Scenario,
    Vehicle,
    VehicleKind,
    VehicleStatus,
    WaterSource,
    WaterSourceKind,
)
from tagliafuoco.nlu.schema import (
    ConstraintSet,
    Enforcement,
    ExcludeVehicle,
    ForbidAssignment,
    ForceAssignment,
    MaxResponseTime,
    MinVehiclesOnFire,
    PrioritizeZone,
    ReserveVehicle,
    Selector,
    SelectorField,
    TimeWindow,
    Unsupported,
    VehicleTypeQuota,
    ZoneKind,
)
from tagliafuoco.validate import physics
from tagliafuoco.validate.report import format_report, render, to_json
from tagliafuoco.validate.rules import Tolerance, ValidationSeverity, arrival_floors
from tagliafuoco.validate.validator import (
    ValidationResult,
    _percentile_nearest_rank,
    validate_plan,
)


# --------------------------------------------------------------------------
# Aiutanti
# --------------------------------------------------------------------------


def _assegnazione(
    vehicle_id: str,
    fire_id: str,
    departure: float,
    arrival: float,
    release: float,
    sorties: int,
    liters: int,
    source_id: str | None = "WS_TIRRENO_LAMEZIA",
) -> Assignment:
    return Assignment(
        vehicle_id=vehicle_id,
        fire_id=fire_id,
        departure_minute=departure,
        arrival_minute=arrival,
        release_minute=release,
        sorties=sorties,
        water_delivered_liters=liters,
        water_source_id=source_id,
    )


def _piano(*assegnazioni: Assignment, plan_id: str = "P_TEST") -> Plan:
    return Plan(
        plan_id=plan_id,
        horizon_minutes=240,
        assignments=list(assegnazioni),
        producer="test",
    )


def piano_corretto() -> Plan:
    """Un piano eseguibile, con tempi coerenti con la fisica delle fixture.

    Canadair da Lamezia sul focolaio di Reggio: 101,1 km in linea d'aria, a
    333 km/h sono 18,2 minuti, piu' 12 di decollo e uscita dalla base, quindi
    non puo' arrivare prima del minuto 30,2 e il piano dichiara il 31. Due lanci da 6137
    litri, con pescaggio in mare nel Golfo di Sant'Eufemia (98,9 km dal
    focolaio, 37,6 minuti a ciclo): la missione ricalcolata dura circa 124
    minuti, dentro le 4 ore di autonomia.

    Elicottero da Cosenza sul focolaio della Sila: 26,4 km, 6,6 minuti a 240
    km/h, piu' 6 di decollo: non prima del minuto 12,6, e il piano dichiara
    il 13. Un lancio da 1500 litri, missione di circa 50
    minuti contro 150 di autonomia.
    """
    return _piano(
        _assegnazione("CAN_01", "F_WUI", 0.0, 31.0, 124.0, 2, 12_274),
        _assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_500),
    )


def _regole_bloccanti(result: ValidationResult) -> set[str]:
    return {v.rule for v in result.blocking}


def _regole_avviso(result: ValidationResult) -> set[str]:
    return {v.rule for v in result.warnings}


def _cs(*constraints) -> ConstraintSet:
    return ConstraintSet(constraint_set_id="CS_TEST", constraints=list(constraints))


def _sel(field: SelectorField, *values: str) -> Selector:
    return Selector(field=field, values=list(values))


# --------------------------------------------------------------------------
# Il piano corretto deve passare (controllo dei falsi allarmi)
# --------------------------------------------------------------------------


def test_piano_corretto_viene_accettato(scenario_minimo: Scenario) -> None:
    """Nessun allarme su un piano coerente: e' la prova contro i falsi positivi.

    Si pretende zero violazioni, non solo zero blocchi: un validatore che
    borbotta a ogni piano valido viene disattivato dopo una settimana.
    """
    result = validate_plan(piano_corretto(), scenario_minimo)

    assert result.is_valid, format_report(result)
    assert result.violations == [], format_report(result)
    assert result.metrics.fires_covered == 2
    assert result.metrics.coverage_ratio == pytest.approx(1.0)
    assert result.metrics.fires_wui_covered == 1
    assert result.metrics.vehicles_used == 2


def test_piano_vuoto_e_valido_ma_non_copre(scenario_minimo: Scenario) -> None:
    """Un piano senza assegnazioni non viola nulla: semplicemente non copre.

    Distinguere "non eseguibile" da "inutile" e' compito di chi legge le
    metriche, non del validatore: qui interessa che non confonda le due cose.
    """
    result = validate_plan(_piano(), scenario_minimo)

    assert result.is_valid
    assert result.metrics.fires_covered == 0
    assert result.metrics.coverage_ratio == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Piani deliberatamente sbagliati
# --------------------------------------------------------------------------


def test_mezzo_teletrasportato(scenario_minimo: Scenario) -> None:
    """Il Canadair dichiara di essere sul fuoco al minuto 2.

    Da Lamezia a Reggio ci sono 101 km, che a 333 km/h sono 18,21 minuti di
    volo, piu' i 12 minuti di decollo e uscita dalla base: il minimo e' 30,21
    minuti dalla partenza. Dichiararne 2 significa dichiarare una velocita'
    impossibile e un decollo istantaneo."""
    piano = _piano(_assegnazione("CAN_01", "F_WUI", 0.0, 2.0, 124.0, 2, 12_274))

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "arrival_time" in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "arrival_time")
    assert violazione.expected == pytest.approx(30.21, abs=0.05)
    assert violazione.found == pytest.approx(2.0)
    assert violazione.delta is not None and violazione.delta < 0
    assert "teletrasport" not in violazione.message.lower()  # messaggio tecnico
    assert "servono almeno" in violazione.message


def _piano_due_missioni(
    release_prima: float = 54.0,
    partenza_seconda: float = 66.0,
) -> Plan:
    """Due missioni di fila del Canadair, con i numeri delle fixture.

    Missione uno, Lamezia verso la Sila: 52,7 km, 9,5 minuti a 333 km/h piu'
    12 di decollo, quindi arrivo non prima del minuto 21,5; un lancio, rientro
    compreso in 53,2 minuti. Missione due, Lamezia verso Reggio: 101,1 km,
    18,2 minuti piu' 12 di decollo, arrivo non prima del 30,2; un lancio,
    86,1 minuti in tutto.

    I due parametri sono quello che i test spostano: il minuto in cui la prima
    missione rilascia il mezzo e quello in cui la seconda parte. Tutto il resto
    resta fermo, cosi' la differenza fra un piano accettato e uno respinto sta
    in quei due numeri e non in un secondo piano scritto a parte, che
    finirebbe per divergere su qualche dettaglio senza che nessuno se ne
    accorga.
    """
    return _piano(
        _assegnazione("CAN_01", "F_SILA", 0.0, 22.0, release_prima, 1, 6_137),
        _assegnazione(
            "CAN_01",
            "F_WUI",
            partenza_seconda,
            partenza_seconda + 31.0,
            partenza_seconda + 87.0,
            1,
            6_137,
        ),
    )


def test_due_missioni_di_fila_ben_distanziate_sono_accettate(
    scenario_minimo: Scenario,
) -> None:
    """Il caso che il validatore respingeva per errore, ed e' il cuore di D1.

    Il Canadair rientra a Lamezia al minuto 54, sta a terra i 12 minuti di
    riattrezzamento che la base dichiara e riparte al minuto 66 verso Reggio,
    dove arriva 19 minuti dopo: 101,1 km a 333 km/h ne chiedono 18,2, quindi
    il conto torna.

    Fino alla correzione questo piano veniva respinto. La regola temporale
    pretendeva fra le due missioni il volo diretto Sila-Reggio, 153,7 km cioe'
    27,7 minuti, perche' dava per scontato che al minuto di rilascio il mezzo
    fosse ancora sopra il primo incendio. SPEC.md sezione 6 dice il contrario:
    la missione comprende il rientro, al rilascio il mezzo e' a Lamezia, e da
    Lamezia a Reggio ci sono i 18,2 minuti che il piano dichiara. Quel volo fra
    i due focolai non esiste e non va preteso: senza questa prova il falso
    allarme tornerebbe alla prima riscrittura della regola.
    """
    result = validate_plan(_piano_due_missioni(), scenario_minimo)

    assert result.is_valid, format_report(result)
    assert result.violations == [], format_report(result)
    assert result.metrics.fires_covered == 2
    assert result.metrics.assignments_feasible == 2


def test_seconda_missione_prima_del_rientro_respinta(
    scenario_minimo: Scenario,
) -> None:
    """Partire al minuto 40 da una missione che rilascia al 54 e' impossibile.

    E' cio' che resta a carico della regola temporale dopo la correzione: non
    il volo fra due focolai, ma la sovrapposizione fra due missioni dello
    stesso mezzo.
    """
    piano = _piano_due_missioni(release_prima=54.0, partenza_seconda=40.0)

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "temporal_consistency" in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "temporal_consistency")
    assert violazione.expected == pytest.approx(54.0)
    assert violazione.found == pytest.approx(40.0)
    assert "in due posti nello stesso momento" in violazione.message


def test_ripartenza_senza_riattrezzamento_respinta(
    scenario_minimo: Scenario,
) -> None:
    """Quattro minuti a terra dove la base ne chiede dodici.

    Il controllo non esisteva: `Base.turnaround_minutes` era valorizzato nei
    dati statici e non lo leggeva nessuno, quindi un mezzo poteva atterrare e
    ripartire nello stesso istante. Qui le missioni non si sovrappongono (58 e'
    dopo 54), quindi la regola temporale tace ed e' solo il riattrezzamento a
    respingere il piano: se un giorno i due controlli si confondessero, questa
    coppia di asserzioni lo direbbe.
    """
    piano = _piano_due_missioni(release_prima=54.0, partenza_seconda=58.0)

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "turnaround" in _regole_bloccanti(result)
    assert "temporal_consistency" not in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "turnaround")
    assert violazione.expected == pytest.approx(12.0)
    assert violazione.found == pytest.approx(4.0)
    assert "riattrezzamento" in violazione.message


def test_riattrezzamento_non_verificabile_senza_base(
    scenario_minimo: Scenario,
) -> None:
    """Base assente dallo scenario: avviso, non blocco e non silenzio.

    Il tempo a terra e' una proprieta' della base. Se la base non c'e' il dato
    manca, e accusare il piano di una lacuna dei dati statici darebbe la colpa
    all'imputato sbagliato. Tacere sarebbe peggio: un controllo saltato in
    silenzio e' indistinguibile da un controllo superato.
    """
    scenario_minimo.bases = [
        b for b in scenario_minimo.bases if b.base_id != "BASE_SUF"
    ]
    canadair = scenario_minimo.vehicle_by_id("CAN_01")
    assert canadair is not None
    canadair.current_position = GeoPoint(lat=38.9054, lon=16.2423)

    result = validate_plan(_piano_due_missioni(), scenario_minimo)

    assert "turnaround" not in _regole_bloccanti(result)
    assert "turnaround" in _regole_avviso(result)
    avviso = next(v for v in result.warnings if v.rule == "turnaround")
    assert "non e' nello scenario" in avviso.message


def test_una_sola_missione_non_ha_riattrezzamento_da_verificare(
    scenario_minimo: Scenario,
) -> None:
    """Con una missione sola il tempo a terra non esiste, e la regola tace.

    L'avviso "base non nello scenario" serve a non far passare per superato un
    controllo che e' stato saltato. Ma dove non c'e' niente da controllare
    l'avviso diventa rumore, e il rumore e' il modo in cui un validatore si fa
    ignorare. La stessa base mancante dello scenario precedente, qui con una
    missione sola, non deve produrre una riga.
    """
    scenario_minimo.bases = [
        b for b in scenario_minimo.bases if b.base_id != "BASE_SUF"
    ]
    canadair = scenario_minimo.vehicle_by_id("CAN_01")
    assert canadair is not None
    canadair.current_position = GeoPoint(lat=38.9054, lon=16.2423)
    piano = _piano(_assegnazione("CAN_01", "F_SILA", 0.0, 22.0, 54.0, 1, 6_137))

    result = validate_plan(piano, scenario_minimo)

    assert "turnaround" not in {v.rule for v in result.violations}


def _piano_tre_missioni(partenza_terza: float = 165.0) -> Plan:
    """Tre missioni di fila del Canadair: Sila, Reggio, Sila.

    Serve a guardare oltre la prima coppia. Le regole sulla sequenza scorrono
    le missioni a due a due, e un difetto che si ferma alla prima coppia
    passerebbe inosservato su un piano a due missioni, che e' l'unico caso che
    i test coprivano.

    I tempi: rientro al 54, dodici minuti a terra, seconda partenza al 66 con
    arrivo al 97 (12 di decollo piu' 18,2 di volo fanno 30,2 di minimo),
    rientro al 153. Poi di nuovo dodici minuti e la terza parte, per default
    al 165.
    """
    return _piano(
        _assegnazione("CAN_01", "F_SILA", 0.0, 22.0, 54.0, 1, 6_137),
        _assegnazione("CAN_01", "F_WUI", 66.0, 97.0, 153.0, 1, 6_137),
        _assegnazione(
            "CAN_01",
            "F_SILA",
            partenza_terza,
            partenza_terza + 22.0,
            partenza_terza + 54.0,
            1,
            6_137,
        ),
    )


def test_tre_missioni_di_fila_restano_ancorate_alla_base(
    scenario_minimo: Scenario,
) -> None:
    """Anche la terza missione parte da Lamezia, non dal focolaio precedente.

    E' l'estensione del caso a due missioni, e senza la correzione fallisce due
    volte invece di una: la vecchia regola pretendeva i 27,7 minuti di volo
    Sila-Reggio fra la prima e la seconda missione e di nuovo fra la seconda e
    la terza, mentre il mezzo quei tragitti non li fa, perche' passa dalla
    base. Con l'origine ancorata alla base i limiti sono 9,5 minuti per le due
    missioni in Sila e 18,2 per quella su Reggio, indipendenti dall'ordine.
    Ai tempi di volo si somma il decollo, 12 minuti per il Canadair, quindi i
    limiti sull'arrivo valgono 21,49 e 30,21 (SPEC.md sezione 6, paragrafo
    "Dove sta il tempo di uscita dalla base").
    """
    result = validate_plan(_piano_tre_missioni(), scenario_minimo)

    assert result.is_valid, format_report(result)
    assert result.violations == [], format_report(result)
    assert result.metrics.assignments_feasible == 3
    floors = arrival_floors(_piano_tre_missioni(), scenario_minimo)
    assert floors[0][0] == pytest.approx(21.49, abs=0.01)
    assert floors[1][0] == pytest.approx(30.21, abs=0.01)
    assert floors[2][0] == pytest.approx(floors[0][0])


def test_il_riattrezzamento_guarda_tutte_le_coppie_non_solo_la_prima(
    scenario_minimo: Scenario,
) -> None:
    """La pausa corta sta fra la seconda e la terza missione, e va vista.

    Un controllo che si fermasse alla prima coppia direbbe di si' a questo
    piano, perche' fra la prima e la seconda missione i dodici minuti ci sono
    tutti. La violazione va anche attribuita alla missione giusta: e' la
    terza, indice 2, quella che parte troppo presto.

    L'asserzione sul silenzio della coerenza temporale non e' decorativa: le
    due missioni non si sovrappongono (158 viene dopo 153), quindi se quella
    regola parlasse vorrebbe dire che ha ripreso a pretendere il volo diretto
    fra due focolai, cioe' che la correzione e' stata disfatta.
    """
    result = validate_plan(_piano_tre_missioni(partenza_terza=158.0), scenario_minimo)

    assert not result.is_valid
    assert "temporal_consistency" not in _regole_bloccanti(result)
    violazioni = [v for v in result.blocking if v.rule == "turnaround"]
    assert len(violazioni) == 1
    assert violazioni[0].expected == pytest.approx(12.0)
    assert violazioni[0].found == pytest.approx(5.0)
    assert violazioni[0].entities["assignment_index"] == "2"


def test_riattrezzamento_nullo_non_viene_inventato(
    scenario_minimo: Scenario,
) -> None:
    """Una base che dichiara zero minuti di riattrezzamento va creduta.

    `Base.turnaround_minutes` ha un default di dieci minuti nel modello di
    dominio, e la tentazione di riusarlo quando il dato vale zero e' forte.
    Sarebbe pero' il validatore a decidere un parametro operativo al posto dei
    dati statici, cioe' ad accusare un piano sulla base di un numero che
    nessuno ha dichiarato. Qui il mezzo riparte nell'istante in cui rientra e
    il piano resta pulito.
    """
    lamezia = scenario_minimo.base_by_id("BASE_SUF")
    assert lamezia is not None
    lamezia.turnaround_minutes = 0.0
    piano = _piano_due_missioni(release_prima=54.0, partenza_seconda=54.0)

    result = validate_plan(piano, scenario_minimo)

    assert result.is_valid, format_report(result)
    assert result.violations == [], format_report(result)


def test_doppia_assegnazione_sovrapposta(scenario_minimo: Scenario) -> None:
    """Lo stesso mezzo su due focolai negli stessi minuti."""
    piano = _piano(
        _assegnazione("CAN_01", "F_SILA", 0.0, 22.0, 120.0, 1, 6_137),
        _assegnazione("CAN_01", "F_WUI", 40.0, 60.0, 180.0, 1, 6_137),
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    messaggi = [v.message for v in result.blocking if v.rule == "temporal_consistency"]
    assert any("in due posti nello stesso momento" in m for m in messaggi)


def test_autonomia_sforata(scenario_minimo: Scenario) -> None:
    """L'elicottero ha 150 minuti di autonomia, il piano lo tiene fuori 200."""
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 200.0, 1, 1_500))

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "endurance" in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "endurance")
    assert violazione.expected == pytest.approx(150.0)
    assert violazione.found == pytest.approx(200.0)
    assert violazione.delta == pytest.approx(50.0)


def test_autonomia_sforata_dal_ricalcolo(scenario_minimo: Scenario) -> None:
    """I tempi dichiarati tornano, ma i cicli d'acqua richiesti no.

    Sei lanci dal focolaio di Reggio costano 6 per 37,6 minuti a ciclo, piu'
    andata, rientro e setup: oltre 260 minuti contro i 240 di autonomia del
    Canadair. Il piano non lo dice, il validatore lo ricalcola.
    """
    piano = _piano(_assegnazione("CAN_01", "F_WUI", 0.0, 31.0, 200.0, 6, 36_822))

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "endurance")
    assert "missione ricalcolata" in violazione.message
    assert violazione.found > 240.0


def test_turno_sforato(scenario_minimo: Scenario) -> None:
    """Il turno e' della giornata, non della finestra: conta il pregresso."""
    canadair = scenario_minimo.vehicle_by_id("CAN_01")
    assert canadair is not None
    canadair.minutes_flown_today = 440.0  # turno massimo 480 minuti

    result = validate_plan(piano_corretto(), scenario_minimo)

    assert not result.is_valid
    assert "shift" in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "shift")
    assert violazione.expected == pytest.approx(480.0)
    assert violazione.found == pytest.approx(564.0)
    assert "gia' volati oggi" in violazione.message


def test_litri_impossibili(scenario_minimo: Scenario) -> None:
    """Un elicottero da 1500 litri non ne eroga 50.000 con un lancio solo."""
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 50_000))

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "water_capacity" in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "water_capacity")
    assert violazione.expected == pytest.approx(1_500.0)
    assert violazione.found == pytest.approx(50_000.0)


def test_litri_senza_lanci(scenario_minimo: Scenario) -> None:
    """Acqua erogata senza nessun ciclo: conteggio dei cicli sbagliato."""
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 0, 1_500))

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "assignment_wellformed" in _regole_bloccanti(result)


@pytest.mark.parametrize(
    "stato",
    [VehicleStatus.RESERVE, VehicleStatus.MAINTENANCE, VehicleStatus.OUT_OF_SHIFT],
)
def test_mezzo_non_impiegabile(scenario_minimo: Scenario, stato: VehicleStatus) -> None:
    """Riserva, manutenzione e fuori turno: tre stati, nessuno impiegabile."""
    canadair = scenario_minimo.vehicle_by_id("CAN_01")
    assert canadair is not None
    canadair.status = stato

    result = validate_plan(piano_corretto(), scenario_minimo)

    assert not result.is_valid
    assert "vehicle_status" in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "vehicle_status")
    assert violazione.found == stato.value


def test_volo_notturno_di_mezzo_non_abilitato(scenario_notturno: Scenario) -> None:
    """Di notte i mezzi aerei non abilitati restano a terra.

    Nella fixture notturna i mezzi aerei sono anche marcati fuori turno,
    quindi ci si aspettano due regole distinte che reagiscono allo stesso
    piano: e' il comportamento voluto, ogni regola dice la sua.
    """
    result = validate_plan(piano_corretto(), scenario_notturno)

    assert not result.is_valid
    bloccanti = _regole_bloccanti(result)
    assert "night_flight" in bloccanti
    assert "vehicle_status" in bloccanti


def test_volo_notturno_anche_con_mezzo_disponibile(scenario_notturno: Scenario) -> None:
    """Isola la regola notturna: mezzi disponibili, ma scenario notturno."""
    for vehicle in scenario_notturno.vehicles:
        vehicle.status = VehicleStatus.AVAILABLE

    result = validate_plan(piano_corretto(), scenario_notturno)

    assert not result.is_valid
    assert _regole_bloccanti(result) == {"night_flight"}


def test_volo_notturno_ammesso_se_il_mezzo_e_abilitato(
    scenario_notturno: Scenario,
) -> None:
    """Un mezzo abilitato al volo notturno non viene bloccato."""
    for vehicle in scenario_notturno.vehicles:
        vehicle.status = VehicleStatus.AVAILABLE
        vehicle.night_capable = True

    result = validate_plan(piano_corretto(), scenario_notturno)

    assert result.is_valid, format_report(result)
    # La base non ha operativita' notturna dichiarata: resta un avviso.
    assert "night_flight" in _regole_avviso(result)


def test_riferimenti_inesistenti(scenario_minimo: Scenario) -> None:
    """Un identificativo sbagliato fa sembrare coperto un focolaio che non lo e'."""
    piano = _piano(
        _assegnazione("MEZZO_FANTASMA", "F_SILA", 0.0, 13.0, 52.0, 1, 1_500),
        _assegnazione("ELI_01", "F_INESISTENTE", 0.0, 13.0, 52.0, 1, 1_500),
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "reference_integrity" in _regole_bloccanti(result)
    assert len([v for v in result.blocking if v.rule == "reference_integrity"]) == 2
    # Nessun focolaio e' davvero coperto, anche se il piano ha due assegnazioni.
    assert result.metrics.fires_covered == 0
    assert result.metrics.fires_covered_declared == 1


def test_sorgente_inesistente(scenario_minimo: Scenario) -> None:
    """Anche la sorgente dichiarata deve esistere nello scenario."""
    piano = _piano(
        _assegnazione(
            "ELI_01", "F_SILA", 0.0, 7.0, 52.0, 1, 1_500, source_id="WS_FANTASMA"
        )
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "reference_integrity" in _regole_bloccanti(result)


def test_fuori_orizzonte(scenario_minimo: Scenario) -> None:
    """L'orizzonte e' di 240 minuti: un rientro al minuto 999 non ci sta."""
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 999.0, 1, 1_500))

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    bloccanti = _regole_bloccanti(result)
    assert "horizon" in bloccanti
    violazione = next(v for v in result.blocking if v.rule == "horizon")
    assert violazione.expected == pytest.approx(240.0)


def test_nessuna_sorgente_compatibile(scenario_minimo: Scenario) -> None:
    """Il modulo AIB non pesca in mare: per lui non c'e' acqua in questo scenario.

    Tempi di percorrenza terrestri: 26,4 km in linea d'aria per 1,45 di
    tortuosita' sono 38,3 km di strada, cioe' 41,8 minuti a 55 km/h.
    """
    piano = _piano(
        _assegnazione("AIB_01", "F_SILA", 0.0, 50.0, 200.0, 2, 800, source_id=None)
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "water_source" in _regole_bloccanti(result)
    violazione = next(v for v in result.blocking if v.rule == "water_source")
    assert "nessuna sorgente" in violazione.message.lower()


def test_sorgente_dichiarata_incompatibile(scenario_minimo: Scenario) -> None:
    """Un Canadair non si riempie a un idrante di paese."""
    scenario_minimo.water_sources.append(
        WaterSource(
            source_id="WS_IDRANTE_SPEZZANO",
            name="Idrante Spezzano",
            point=GeoPoint(lat=39.3050, lon=16.5500),
            kind=WaterSourceKind.HYDRANT,
            usable_by=[VehicleKind.AUTOBOTTE, VehicleKind.MODULO_AIB],
            refill_minutes=10.0,
        )
    )
    piano = _piano(
        _assegnazione(
            "CAN_01",
            "F_SILA",
            0.0,
            10.0,
            120.0,
            1,
            6_137,
            source_id="WS_IDRANTE_SPEZZANO",
        )
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "water_source")
    assert "non e' compatibile" in violazione.message


def test_capacita_della_sorgente_esaurita(scenario_minimo: Scenario) -> None:
    """Una cisterna con capacita' dichiarata non eroga piu' di quel che contiene."""
    sorgente = scenario_minimo.water_source_by_id("WS_TIRRENO_LAMEZIA")
    assert sorgente is not None
    sorgente.capacity_liters = 5_000

    result = validate_plan(piano_corretto(), scenario_minimo)

    assert not result.is_valid
    violazione = next(
        v
        for v in result.blocking
        if v.rule == "water_capacity" and "sorgente" in v.message
    )
    assert violazione.expected == pytest.approx(5_000.0)


# --------------------------------------------------------------------------
# Vincoli duri del ConstraintSet
# --------------------------------------------------------------------------


def test_vincolo_hard_mezzo_escluso(scenario_minimo: Scenario) -> None:
    cs = _cs(
        ExcludeVehicle(
            constraint_id="C_EX",
            selector=_sel(SelectorField.VEHICLE_ID, "CAN_01"),
            source_span="il Canadair e' fermo per un guasto",
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    assert "hard_constraint" in _regole_bloccanti(result)
    assert any("C_EX" in v.message for v in result.blocking)


def test_vincolo_hard_mezzo_di_riserva(scenario_minimo: Scenario) -> None:
    """La riserva si verifica sul piano, non sullo stato del mezzo.

    E' il caso che rende visibile la catena completa: il coordinatore ha
    detto "l'elicottero resta di riserva", il traduttore lo ha tipizzato, e
    qui si controlla che nel piano prodotto quel mezzo non compaia.
    """
    cs = _cs(
        ReserveVehicle(
            constraint_id="C_RES",
            selector=_sel(SelectorField.VEHICLE_KIND, "elicottero_medio"),
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert violazione.entities["vehicle_id"] == "ELI_01"


def test_vincolo_hard_riserva_temporanea_rispettata(scenario_minimo: Scenario) -> None:
    """Con `until_minute` la riserva scade: una partenza dopo non la viola."""
    cs = _cs(
        ReserveVehicle(
            constraint_id="C_RES2",
            selector=_sel(SelectorField.VEHICLE_ID, "ELI_01"),
            until_minute=0,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert result.is_valid, format_report(result)


def test_vincolo_hard_assegnazione_imposta(scenario_minimo: Scenario) -> None:
    cs = _cs(
        ForceAssignment(
            constraint_id="C_FORCE",
            vehicle_selector=_sel(SelectorField.VEHICLE_KIND, "canadair"),
            fire_selector=_sel(SelectorField.FIRE_ID, "F_SILA"),
            min_count=1,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert violazione.expected == pytest.approx(1.0)
    assert violazione.found == pytest.approx(0.0)


def test_vincolo_hard_assegnazione_vietata(scenario_minimo: Scenario) -> None:
    cs = _cs(
        ForbidAssignment(
            constraint_id="C_FORBID",
            vehicle_selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            fire_selector=_sel(SelectorField.FIRE_IS_WUI, "true"),
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert violazione.entities["fire_id"] == "F_WUI"


def test_vincolo_hard_tempo_massimo_di_risposta(scenario_minimo: Scenario) -> None:
    cs = _cs(
        MaxResponseTime(
            constraint_id="C_MAX",
            minutes=5,
            fire_selector=_sel(SelectorField.FIRE_ID, "F_WUI"),
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert violazione.expected == pytest.approx(5.0)
    assert violazione.found == pytest.approx(31.0)


def test_vincolo_hard_minimo_di_mezzi(scenario_minimo: Scenario) -> None:
    cs = _cs(
        MinVehiclesOnFire(
            constraint_id="C_MIN",
            fire_selector=_sel(SelectorField.FIRE_ID, "F_WUI"),
            count=3,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert violazione.found == pytest.approx(1.0)


def test_vincolo_hard_quota_per_tipo(scenario_minimo: Scenario) -> None:
    cs = _cs(
        VehicleTypeQuota(
            constraint_id="C_QUOTA",
            selector=_sel(SelectorField.VEHICLE_KIND, "canadair"),
            max_used=0,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    assert any("C_QUOTA" in v.message for v in result.blocking)


def test_vincolo_hard_finestra_temporale(scenario_minimo: Scenario) -> None:
    """Lo scenario e' delle 11:30, il piano finisce alle 13:34: fuori finestra.

    La finestra e' letta come orario del giorno, coerentemente con lo schema
    dei vincoli ("gli aerei fino alle otto"), e il messaggio dichiara
    l'interpretazione usata.
    """
    cs = _cs(
        TimeWindow(
            constraint_id="C_WIN",
            selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            from_minute=6 * 60,
            to_minute=12 * 60,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert "scenario.timestamp" in violazione.message


def test_vincolo_soft_non_blocca(scenario_minimo: Scenario) -> None:
    """Un vincolo morbido violato non rende il piano non eseguibile.

    La distinzione duro/morbido arriva dal linguaggio del coordinatore e il
    validatore deve rispettarla: un ordine e' un ordine, una preferenza no.
    """
    cs = _cs(
        ExcludeVehicle(
            constraint_id="C_SOFT",
            selector=_sel(SelectorField.VEHICLE_ID, "CAN_01"),
            enforcement=Enforcement.SOFT,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert result.is_valid, format_report(result)


def test_vincolo_sui_pesi_dichiarato_duro_da_avviso(scenario_minimo: Scenario) -> None:
    """Una priorita' di zona non e' verificabile su un singolo piano.

    Dire di si' sarebbe una falsa sicurezza, dire di no un falso allarme: si
    dice che il vincolo e' stato tradotto male, ed e' un avviso.
    """
    cs = _cs(
        PrioritizeZone(
            constraint_id="C_ZONE",
            zone_kind=ZoneKind.WUI,
            enforcement=Enforcement.HARD,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert result.is_valid
    assert "hard_constraint" in _regole_avviso(result)


def test_parte_dell_ordine_non_tradotta_da_avviso(scenario_minimo: Scenario) -> None:
    """UNSUPPORTED va mostrato: il coordinatore deve sapere cosa non si e' fatto."""
    cs = _cs(
        Unsupported(
            constraint_id="C_UNS",
            reason="chiede di coordinarsi con i mezzi della Basilicata",
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert result.is_valid
    avviso = next(v for v in result.warnings if v.rule == "hard_constraint")
    assert "Basilicata" in avviso.message


# --------------------------------------------------------------------------
# Dichiarato contro ricalcolato
# --------------------------------------------------------------------------


def test_metriche_gonfiate_danno_avviso(scenario_minimo: Scenario) -> None:
    """Un piano eseguibile puo' comunque mentire sulle proprie metriche.

    Non e' un blocco, ma va detto: quelle cifre sono quelle che finirebbero
    nel confronto con la baseline, e un confronto su numeri non verificati non
    dimostra niente.
    """
    piano = piano_corretto()
    piano.metrics = PlanMetrics(
        fires_total=2,
        fires_covered=2,
        water_demanded_liters=200_000,
        water_delivered_liters=90_000,  # il piano ne eroga 13.774
        mean_response_minutes=13.0,
        vehicles_used=2,
        total_vehicle_minutes=174.0,
        total_cost_eur=30_000.0,
    )

    result = validate_plan(piano, scenario_minimo)

    assert result.is_valid
    assert "metrics_declaration" in _regole_avviso(result)
    divergenze = {c.name for c in result.disagreements}
    assert "litri erogati" in divergenze


def test_fabbisogno_idrico_divergente_da_avviso(scenario_minimo: Scenario) -> None:
    """Se l'arricchimento dichiara un fabbisogno lontano dalla formula, si avvisa."""
    fuoco = scenario_minimo.fire_by_id("F_SILA")
    assert fuoco is not None
    fuoco.water_demand_liters = 1_000_000

    result = validate_plan(piano_corretto(), scenario_minimo)

    assert result.is_valid
    assert "water_demand" in _regole_avviso(result)


def test_copertura_reale_esclude_le_assegnazioni_impossibili(
    scenario_minimo: Scenario,
) -> None:
    """La copertura del validatore conta solo le assegnazioni che reggono.

    E' la metrica che il piano non puo' gonfiare: un mezzo teletrasportato
    resta nel conteggio dichiarato e sparisce da quello ricalcolato.
    """
    piano = _piano(
        _assegnazione("CAN_01", "F_WUI", 0.0, 1.0, 124.0, 2, 12_274),
        _assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_500),
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert result.metrics.fires_covered_declared == 2
    assert result.metrics.fires_covered == 1
    assert result.metrics.fires_wui_covered == 0


# --------------------------------------------------------------------------
# Tolleranza
# --------------------------------------------------------------------------


def test_la_tolleranza_e_un_parametro(scenario_minimo: Scenario) -> None:
    """Lo stesso piano passa o cade a seconda della tolleranza dichiarata.

    L'arrivo e' dichiarato al minuto 30 contro i 30,21 richiesti dalla fisica
    (12 minuti di uscita dalla base piu' 18,21 di volo):
    un quinto di minuto, cioe' esattamente il tipo di scarto che nasce dagli
    arrotondamenti a interi del solver. Con la tolleranza di default passa,
    con tolleranza nulla no. Serve a rendere esplicito che il valore e' una
    scelta e non una verita'.
    """
    piano = _piano(_assegnazione("CAN_01", "F_WUI", 0.0, 30.0, 124.0, 2, 12_274))

    assert validate_plan(piano, scenario_minimo).is_valid
    assert not validate_plan(piano, scenario_minimo, tolerance=0.0).is_valid
    assert validate_plan(piano, scenario_minimo, tolerance=Tolerance()).is_valid


# --------------------------------------------------------------------------
# Modello fisico
# --------------------------------------------------------------------------


def test_geodetica_concorda_con_haversine() -> None:
    """Le due formule devono dare lo stesso numero: se divergono, c'e' un errore.

    Qui l'haversine e' scritta dentro il test, cosi' il confronto non passa
    dal codice di nessuno dei due moduli di produzione.
    """
    from math import asin, cos, radians, sin, sqrt

    coppie = [
        (GeoPoint(38.9054, 16.2423), GeoPoint(39.3100, 16.5600)),
        (GeoPoint(38.1100, 15.6800), GeoPoint(38.9200, 16.1500)),
        (GeoPoint(39.2983, 16.2533), GeoPoint(38.1100, 15.6800)),
    ]
    for a, b in coppie:
        f1, f2 = radians(a.lat), radians(b.lat)
        df, dl = f2 - f1, radians(b.lon - a.lon)
        h = sin(df / 2) ** 2 + cos(f1) * cos(f2) * sin(dl / 2) ** 2
        haversine = 2 * 6371.0 * asin(sqrt(h))
        assert physics.geodesic_km(a, b) == pytest.approx(haversine, rel=1e-9)


def test_geodetica_simmetrica_e_nulla_su_se_stessa() -> None:
    a = GeoPoint(39.0, 16.5)
    b = GeoPoint(38.2, 15.9)
    assert physics.geodesic_km(a, a) == pytest.approx(0.0, abs=1e-9)
    assert physics.geodesic_km(a, b) == pytest.approx(physics.geodesic_km(b, a))


def test_distanza_terrestre_applica_la_tortuosita(
    modulo_aib, canadair
) -> None:
    """Il mezzo terrestre percorre 1,45 volte la linea d'aria, l'aereo no."""
    a = GeoPoint(39.2983, 16.2533)
    b = GeoPoint(39.3100, 16.5600)
    retta = physics.geodesic_km(a, b)

    assert physics.travel_distance_km(canadair, a, b) == pytest.approx(retta)
    assert physics.travel_distance_km(modulo_aib, a, b) == pytest.approx(retta * 1.45)


def test_fabbisogno_idrico_segue_la_formula_di_spec(fire_sila) -> None:
    """0,8 km2 di conifere con 48 MW: 0,8 * 100.000 * 1,4 * 1,24 = 138.880 litri."""
    assert physics.water_demand_liters(fire_sila) == pytest.approx(138_880.0)


def test_coefficiente_di_intensita_satura(fire_sila) -> None:
    """Oltre i 200 MW il coefficiente non cresce piu': e' una taratura di SPEC."""
    fire_sila.max_frp_mw = 200.0
    a_duecento = physics.water_demand_liters(fire_sila)
    fire_sila.max_frp_mw = 5_000.0
    assert physics.water_demand_liters(fire_sila) == pytest.approx(a_duecento)


def test_capacita_e_cicli(canadair) -> None:
    assert physics.deliverable_liters(canadair, 3) == 3 * 6137
    assert physics.deliverable_liters(canadair, 0) == 0
    assert physics.required_sorties(canadair, 6_137) == 1
    assert physics.required_sorties(canadair, 6_138) == 2
    assert physics.required_sorties(canadair, 0) == 0


def test_ciclo_acqua_segue_spec(canadair, sea_source, fire_sila) -> None:
    """t_ciclo = 2 * d / v * 60 + pescaggio + sgancio, con i numeri delle fixture."""
    atteso = (
        physics.geodesic_km(fire_sila.centroid, sea_source.point) * 2 / 333.0 * 60
        + 1.5
        + 0.5
    )
    calcolato = physics.water_cycle_minutes(
        canadair, fire_sila.centroid, sea_source.point, sea_source
    )
    assert calcolato == pytest.approx(atteso)


def test_finestra_di_luce(instant) -> None:
    """Le 11:30 piu' 60 minuti sono le 12:30, dentro la luce; piu' 600 no."""
    assert physics.clock_minute(instant, 60.0) == pytest.approx(12 * 60 + 30)
    assert physics.is_daylight(physics.clock_minute(instant, 60.0))
    assert not physics.is_daylight(physics.clock_minute(instant, 600.0))


# --------------------------------------------------------------------------
# Resoconto
# --------------------------------------------------------------------------


def test_report_testuale_dice_l_esito(scenario_minimo: Scenario) -> None:
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 1.0, 52.0, 1, 1_500))

    respinto = format_report(validate_plan(piano, scenario_minimo))
    accettato = format_report(validate_plan(piano_corretto(), scenario_minimo))

    assert "RESPINTO" in respinto
    assert "Violazioni bloccanti" in respinto
    assert "atteso" in respinto and "trovato" in respinto
    assert "ACCETTATO" in accettato
    assert "Nessuna violazione rilevata" in accettato


def test_report_json_serializzabile(scenario_minimo: Scenario) -> None:
    """La API deve poter restituire l'esito senza perdere pezzi per strada."""
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 1.0, 999.0, 1, 50_000))

    payload = json.loads(to_json(validate_plan(piano, scenario_minimo)))

    assert payload["is_valid"] is False
    assert payload["counts"]["blocking"] >= 2
    assert payload["tolerance"]["minutes"] == 1.0
    assert {v["rule"] for v in payload["violations"]} >= {"horizon", "water_capacity"}
    assert payload["metrics"]["fires_total"] == 2


def test_report_a_schermo_non_esplode(scenario_minimo: Scenario) -> None:
    """Il resoconto con rich deve stampare anche quando ci sono violazioni."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=200, color_system=None)

    render(validate_plan(piano_corretto(), scenario_minimo), console)
    render(
        validate_plan(
            _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 1.0, 999.0, 1, 50_000)),
            scenario_minimo,
        ),
        console,
    )

    testo = buffer.getvalue()
    assert "Piano accettato" in testo
    assert "Piano respinto" in testo
    assert "Violazioni rilevate" in testo


# --------------------------------------------------------------------------
# Indipendenza dal solver
# --------------------------------------------------------------------------


def test_il_validatore_non_importa_nulla_dal_solver() -> None:
    """La regola metodologica del progetto, verificata dal codice e non a parole.

    Se un giorno qualcuno importasse una funzione di `optimize/` per comodita',
    il validatore smetterebbe di essere una seconda implementazione e
    diventerebbe un'eco della prima. Questo test e' l'unico modo per
    accorgersene subito invece che a valle, quando i risultati sperimentali
    sono gia' stati scritti.
    """
    pacchetto = Path(__file__).resolve().parents[1] / "src" / "tagliafuoco" / "validate"
    moduli = sorted(pacchetto.glob("*.py"))
    assert moduli, "il pacchetto validate non contiene moduli"

    for modulo in moduli:
        albero = ast.parse(modulo.read_text(encoding="utf-8"), filename=str(modulo))
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.Import):
                nomi = [alias.name for alias in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                nomi = [nodo.module or ""]
            else:
                continue
            for nome in nomi:
                assert "optimize" not in nome, (
                    f"{modulo.name} importa '{nome}': il validatore deve restare "
                    f"indipendente dal solver"
                )


def test_le_regole_sono_pure(scenario_minimo: Scenario) -> None:
    """Validare non modifica ne' il piano ne' lo scenario.

    Serve perche' il validatore gira dentro la rigiocata della stagione, in
    mezzo a centinaia di finestre: se sporcasse lo stato, il confronto con la
    baseline diventerebbe dipendente dall'ordine di esecuzione.
    """
    piano = piano_corretto()
    prima_piano = [
        (a.vehicle_id, a.fire_id, a.departure_minute, a.release_minute, a.sorties)
        for a in piano.assignments
    ]
    prima_mezzi = [(v.vehicle_id, v.status, v.minutes_flown_today) for v in scenario_minimo.vehicles]

    validate_plan(piano, scenario_minimo)
    validate_plan(piano, scenario_minimo)

    assert [
        (a.vehicle_id, a.fire_id, a.departure_minute, a.release_minute, a.sorties)
        for a in piano.assignments
    ] == prima_piano
    assert [
        (v.vehicle_id, v.status, v.minutes_flown_today) for v in scenario_minimo.vehicles
    ] == prima_mezzi


def test_severita_e_validita_sono_coerenti(scenario_minimo: Scenario) -> None:
    """Solo le violazioni bloccanti rendono un piano non valido."""
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 1.0, 52.0, 1, 1_500))
    result = validate_plan(piano, scenario_minimo)

    assert result.is_valid == (not result.blocking)
    assert all(v.severity is ValidationSeverity.BLOCKING for v in result.blocking)
    assert all(v.severity is ValidationSeverity.WARNING for v in result.warnings)
    assert len(result.violations) == len(result.blocking) + len(result.warnings)


# --------------------------------------------------------------------------
# Casi limite: scenari vuoti o degenerati
# --------------------------------------------------------------------------
#
# Un validatore lo si scrive guardando il caso tipico, e il caso tipico ha
# sempre almeno un focolaio e almeno un mezzo. I bordi arrivano dopo, dalla
# rigiocata della stagione: alle sei del mattino di un giorno di pioggia lo
# scenario e' vuoto, e un validatore che li' solleva un'eccezione o divide per
# zero ferma l'esperimento a meta'.


def _scenario_vuoto(instant) -> Scenario:
    """Scenario senza niente dentro: nessun focolaio, nessun mezzo, nessuna acqua."""
    return Scenario(scenario_id="SC_VUOTO", timestamp=instant, horizon_minutes=240)


def test_scenario_vuoto_e_piano_vuoto(instant) -> None:
    """Niente da spegnere e niente con cui spegnere: e' valido, non e' un errore.

    I rapporti che dividono per un totale nullo devono avere una convenzione
    dichiarata, non un'eccezione: copertura 0 (non si e' coperto niente),
    copertura WUI e soddisfacimento idrico 1 (non c'era niente da coprire, e
    contare come fallimento un fabbisogno inesistente falserebbe le medie
    della stagione verso il basso).
    """
    result = validate_plan(_piano(), _scenario_vuoto(instant))

    assert result.is_valid
    assert result.violations == []
    assert result.metrics.fires_total == 0
    assert result.metrics.coverage_ratio == pytest.approx(0.0)
    assert result.metrics.wui_coverage_ratio == pytest.approx(1.0)
    assert result.metrics.water_fulfilment_ratio == pytest.approx(1.0)
    assert result.metrics.p90_response_minutes == pytest.approx(0.0)


def test_report_su_scenario_vuoto_non_esplode(instant) -> None:
    """Le tre forme del resoconto reggono anche quando non c'e' niente da dire."""
    result = validate_plan(_piano(), _scenario_vuoto(instant))
    buffer = io.StringIO()

    testo = format_report(result)
    payload = json.loads(to_json(result))
    render(result, Console(file=buffer, width=200, color_system=None))

    assert "Nessuna violazione rilevata" in testo
    assert payload["metrics"]["fires_total"] == 0
    assert "Piano accettato" in buffer.getvalue()


def test_piano_che_parla_di_uno_scenario_vuoto(instant) -> None:
    """Un piano pieno su uno scenario vuoto: ogni riferimento e' fantasma."""
    result = validate_plan(piano_corretto(), _scenario_vuoto(instant))

    assert not result.is_valid
    assert _regole_bloccanti(result) == {"reference_integrity"}
    assert result.metrics.fires_covered == 0
    assert result.metrics.fires_covered_declared == 0


def test_scenario_senza_mezzi_disponibili(scenario_minimo: Scenario) -> None:
    """Tutti i mezzi in manutenzione: nessuna assegnazione regge, copertura nulla."""
    for vehicle in scenario_minimo.vehicles:
        vehicle.status = VehicleStatus.MAINTENANCE

    result = validate_plan(piano_corretto(), scenario_minimo)

    assert not result.is_valid
    assert _regole_bloccanti(result) == {"vehicle_status"}
    assert result.metrics.fires_covered == 0
    assert result.metrics.fires_covered_declared == 2


def test_scenario_senza_sorgenti_dacqua(scenario_minimo: Scenario) -> None:
    """Senza acqua nello scenario nessun ciclo e' eseguibile, e si deve dire."""
    scenario_minimo.water_sources.clear()
    piano = _piano(
        _assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_500, source_id=None)
    )

    fuoco = scenario_minimo.fire_by_id("F_SILA")
    elicottero = scenario_minimo.vehicle_by_id("ELI_01")
    assert fuoco is not None and elicottero is not None
    sorgente, distanza = physics.nearest_usable_source(
        elicottero, fuoco, scenario_minimo
    )
    result = validate_plan(piano, scenario_minimo)

    assert sorgente is None
    assert distanza == float("inf")
    assert not result.is_valid
    assert "water_source" in _regole_bloccanti(result)


def test_una_sola_assegnazione(scenario_minimo: Scenario) -> None:
    """Campione di dimensione uno: media e novantesimo percentile coincidono.

    E' il caso che rompe i percentili scritti con l'interpolazione o con un
    indice calcolato a mano, ed e' anche il caso piu' frequente in una
    finestra di tre ore con un solo focolaio.
    """
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_500))

    result = validate_plan(piano, scenario_minimo)

    assert result.is_valid, format_report(result)
    assert result.metrics.assignments_feasible == 1
    assert result.metrics.mean_response_minutes == pytest.approx(12.605, abs=0.01)
    assert result.metrics.p90_response_minutes == pytest.approx(
        result.metrics.mean_response_minutes
    )


def test_un_numero_che_non_e_un_numero_respinge_senza_far_cadere_tutto(
    scenario_minimo: Scenario,
) -> None:
    """Un NaN fra i minuti va respinto, non deve far esplodere la validazione.

    Un tempo non finito e' il caso peggiore per un validatore scritto a
    disuguaglianze: ogni confronto con NaN e' falso, quindi l'assegnazione
    attraversa indenne ogni regola e viene dichiarata eseguibile proprio
    perche' non la si riesce a esaminare. Il silenzio coincide con
    l'assoluzione.

    Prima di questa correzione succedeva anche di peggio: le regole
    tacevano tutte tranne `check_night_flight`, che formattava l'orario di
    rientro e moriva su `int(round(nan))` con un `ValueError`. Chi chiamava il
    validatore, cioe' la API e la CLI, non riceveva un piano respinto con la
    diagnosi ma un errore di programma, che e' il modo piu' rumoroso di non
    rispondere. Qui si pretendono tutte e tre le cose: il piano e' respinto,
    la regola che lo respinge e' quella di forma, e il messaggio dice quale
    numero e' rotto.
    """
    piano = _piano(
        _assegnazione("CAN_01", "F_SILA", 0.0, 22.0, float("nan"), 1, 6_137),
        _assegnazione("CAN_01", "F_WUI", 66.0, 97.0, 153.0, 1, 6_137),
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    assert "assignment_wellformed" in _regole_bloccanti(result)
    violazione = next(
        v for v in result.blocking if v.rule == "assignment_wellformed"
    )
    assert "minuto di rilascio" in violazione.message
    assert result.metrics.declared_mean_response_minutes == pytest.approx(59.5)
    json.loads(to_json(result))

    # Con il NaN nel minuto di ARRIVO la media non puo' includerlo: un solo
    # numero illeggibile cancellerebbe anche i tempi validi del resto del
    # piano, e il resoconto direbbe "nan" al posto di una metrica.
    con_arrivo_rotto = _piano(
        _assegnazione("CAN_01", "F_SILA", 0.0, float("nan"), 54.0, 1, 6_137),
        _assegnazione("CAN_01", "F_WUI", 66.0, 97.0, 153.0, 1, 6_137),
    )

    altro = validate_plan(con_arrivo_rotto, scenario_minimo)

    assert not altro.is_valid
    assert altro.metrics.declared_mean_response_minutes == pytest.approx(97.0)
    json.loads(to_json(altro))


def test_finestra_temporale_non_esplode_su_un_tempo_non_finito(
    scenario_minimo: Scenario,
) -> None:
    """Anche il controllo dei vincoli duri sopravvive a un minuto illeggibile.

    `_check_time_window` ancora la finestra alla mezzanotte del giorno di
    partenza, e lo fa con `floor`: su un NaN quella funzione non torna con un
    numero strano, torna con un'eccezione. La regola di forma contesta il
    piano, questa lo salta, e il validatore risponde.
    """
    cs = _cs(
        TimeWindow(
            constraint_id="C_FINESTRA",
            selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            from_minute=6 * 60,
            to_minute=20 * 60,
        )
    )
    piano = _piano(
        _assegnazione("CAN_01", "F_SILA", float("nan"), 10.0, 54.0, 1, 6_137)
    )

    result = validate_plan(piano, scenario_minimo, cs)

    assert not result.is_valid
    assert "assignment_wellformed" in _regole_bloccanti(result)


def test_focolaio_senza_area_non_divide_per_zero(scenario_minimo: Scenario) -> None:
    """Un focolaio di area nulla ha fabbisogno nullo, e nessuno se ne lamenta.

    Il rapporto fra litri erogabili e fabbisogno ha zero al denominatore: la
    convenzione e' 1, cioe' "tutto il fabbisogno e' soddisfatto", perche' il
    fabbisogno era nessuno.
    """
    for fuoco in scenario_minimo.fires:
        fuoco.area_km2 = 0.0
        fuoco.water_demand_liters = 0

    result = validate_plan(_piano(), scenario_minimo)

    assert result.is_valid
    assert physics.water_demand_liters(scenario_minimo.fires[0]) == pytest.approx(0.0)
    assert result.metrics.water_demanded_liters == pytest.approx(0.0)
    assert result.metrics.water_fulfilment_ratio == pytest.approx(1.0)
    assert "water_demand" not in _regole_avviso(result)


def test_selettore_che_non_seleziona_nulla_resta_muto(
    scenario_minimo: Scenario,
) -> None:
    """Un vincolo su un mezzo inesistente non accusa il piano.

    E' un limite dichiarato, non una dimenticanza: quel vincolo e' un errore
    di traduzione, ma il piano non c'entra, e dare la colpa all'imputato
    sbagliato e' il modo piu' rapido per far perdere fiducia nel validatore.
    """
    cs = _cs(
        ExcludeVehicle(
            constraint_id="C_FANTASMA",
            selector=_sel(SelectorField.VEHICLE_ID, "MEZZO_CHE_NON_ESISTE"),
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert result.is_valid, format_report(result)


def test_insieme_di_vincoli_vuoto_equivale_a_nessun_vincolo(
    scenario_minimo: Scenario,
) -> None:
    """Un ConstraintSet senza vincoli non cambia una virgola dell'esito."""
    vuoto = ConstraintSet(constraint_set_id="CS_VUOTO")

    senza = validate_plan(piano_corretto(), scenario_minimo).to_dict()
    con = validate_plan(piano_corretto(), scenario_minimo, vuoto).to_dict()

    assert senza == con


# --------------------------------------------------------------------------
# Valori esattamente al limite
# --------------------------------------------------------------------------
#
# Ogni regola e' un confronto, e ogni confronto ha un bordo. Qui si fissa da
# che parte del bordo sta l'uguaglianza: al limite il piano passa, un passo
# oltre no. Si usa tolleranza nulla dove il margine di default nasconderebbe
# il bordo che si vuole misurare.


def test_arrivo_esattamente_al_minimo_fisico(scenario_minimo: Scenario) -> None:
    """Il modulo AIB non puo' stare sulla Sila prima del minuto 49,79.

    Otto minuti per uscire dal distaccamento piu' 41,79 di strada (38,3 km in
    linea d'aria per il fattore di tortuosita' 1,45, a 55 km/h). Al limite
    49,80 bastano, 49,78 no."""
    al_limite = _piano(
        _assegnazione("AIB_01", "F_SILA", 0.0, 49.80, 200.0, 0, 0, source_id=None)
    )
    sotto = _piano(
        _assegnazione("AIB_01", "F_SILA", 0.0, 49.78, 200.0, 0, 0, source_id=None)
    )

    assert "arrival_time" not in _regole_bloccanti(
        validate_plan(al_limite, scenario_minimo, tolerance=0.0)
    )
    assert "arrival_time" in _regole_bloccanti(
        validate_plan(sotto, scenario_minimo, tolerance=0.0)
    )


def test_riattrezzamento_esattamente_sufficiente(scenario_minimo: Scenario) -> None:
    """Lamezia chiede dodici minuti a terra: dodici bastano, 11,9 no.

    Il bordo di questa regola e' l'uguaglianza, e va fissato da che parte sta,
    perche' e' esattamente il valore che il risolutore produce: CP-SAT lavora
    su interi e arrotonda il riattrezzamento per eccesso, quindi i suoi piani
    atterrano sul bordo e non un minuto oltre. Se l'uguaglianza cadesse dalla
    parte sbagliata, il validatore respingerebbe ogni piano con due missioni
    di fila dello stesso mezzo.
    """
    al_limite = _piano_due_missioni(release_prima=54.0, partenza_seconda=66.0)
    sotto = _piano_due_missioni(release_prima=54.0, partenza_seconda=65.9)

    assert "turnaround" not in _regole_bloccanti(
        validate_plan(al_limite, scenario_minimo, tolerance=0.0)
    )
    assert "turnaround" in _regole_bloccanti(
        validate_plan(sotto, scenario_minimo, tolerance=0.0)
    )


def test_rientro_esattamente_sull_orizzonte(scenario_minimo: Scenario) -> None:
    """L'orizzonte e' un istante incluso: al minuto 240 si e' ancora dentro."""
    dentro = _piano(
        _assegnazione("AIB_01", "F_SILA", 0.0, 50.0, 240.0, 0, 0, source_id=None)
    )
    fuori = _piano(
        _assegnazione("AIB_01", "F_SILA", 0.0, 50.0, 241.0, 0, 0, source_id=None)
    )

    assert validate_plan(dentro, scenario_minimo, tolerance=0.0).is_valid
    assert "horizon" in _regole_bloccanti(
        validate_plan(fuori, scenario_minimo, tolerance=0.0)
    )


def test_impegno_esattamente_pari_all_autonomia(scenario_minimo: Scenario) -> None:
    """150 minuti di autonomia significa che 150 si possono usare tutti."""
    al_limite = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 150.0, 1, 1_500))
    oltre = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 151.0, 1, 1_500))

    assert "endurance" not in _regole_bloccanti(
        validate_plan(al_limite, scenario_minimo, tolerance=0.0)
    )
    assert "endurance" in _regole_bloccanti(
        validate_plan(oltre, scenario_minimo, tolerance=0.0)
    )


def test_litri_al_limite_della_tolleranza(scenario_minimo: Scenario) -> None:
    """1500 litri di capacita' piu' 100 di fondo tollerato: 1600 passa, 1601 no.

    Il fondo fisso serve ai mezzi piccoli, dove il due per cento varrebbe
    trenta litri e qualsiasi arrotondamento diventerebbe una violazione.
    """
    al_limite = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_600))
    oltre = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_601))

    assert "water_capacity" not in _regole_bloccanti(
        validate_plan(al_limite, scenario_minimo)
    )
    assert "water_capacity" in _regole_bloccanti(validate_plan(oltre, scenario_minimo))


def test_turno_esattamente_pari_al_massimo(scenario_minimo: Scenario) -> None:
    """480 minuti di turno si possono consumare fino all'ultimo."""
    canadair = scenario_minimo.vehicle_by_id("CAN_01")
    assert canadair is not None

    canadair.minutes_flown_today = 356.0  # 356 + 124 = 480 esatti
    al_limite = validate_plan(piano_corretto(), scenario_minimo, tolerance=0.0)
    canadair.minutes_flown_today = 357.0
    oltre = validate_plan(piano_corretto(), scenario_minimo, tolerance=0.0)

    assert "shift" not in _regole_bloccanti(al_limite)
    assert "shift" in _regole_bloccanti(oltre)


def test_tetto_di_risposta_esattamente_rispettato(scenario_minimo: Scenario) -> None:
    """Arrivo al minuto 31 con tetto a 31 minuti: rispettato, non violato.

    Il 31 non e' scelto a caso: e' il primo intero sopra i 30,21 minuti che
    servono al Canadair per uscire da Lamezia (12) e arrivare su Reggio
    (18,21). Il bordo della regola sta sull'uguaglianza."""
    rispettato = _cs(
        MaxResponseTime(
            constraint_id="C_MAX_OK",
            minutes=31,
            fire_selector=_sel(SelectorField.FIRE_ID, "F_WUI"),
        )
    )
    violato = _cs(
        MaxResponseTime(
            constraint_id="C_MAX_NO",
            minutes=29,
            fire_selector=_sel(SelectorField.FIRE_ID, "F_WUI"),
        )
    )

    assert validate_plan(piano_corretto(), scenario_minimo, rispettato).is_valid
    assert not validate_plan(piano_corretto(), scenario_minimo, violato).is_valid


def test_orizzonte_piu_stretto_fra_piano_e_scenario(scenario_minimo: Scenario) -> None:
    """Se il piano dichiara una finestra piu' corta dello scenario vince la piu' corta.

    Un piano non puo' pianificare oltre la finestra per cui lo scenario e'
    stato costruito, ma puo' benissimo restringerla, e in quel caso e' lui che
    si e' dato la regola.
    """
    piano = Plan(
        plan_id="P_CORTO",
        horizon_minutes=60,
        assignments=[_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 100.0, 1, 1_500)],
        producer="test",
    )

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "horizon")
    assert violazione.expected == pytest.approx(60.0)


# --------------------------------------------------------------------------
# Regressioni: difetti trovati rileggendo il codice
# --------------------------------------------------------------------------


def test_missione_con_velocita_nulla_non_sparisce_nel_nan(
    canadair, sea_source, fire_sila, base_lamezia
) -> None:
    """Velocita' di crociera nulla: il totale deve essere infinito, mai NaN.

    E' il difetto piu' insidioso trovato in questo modulo. Con velocita' zero
    il tempo di ciclo vale infinito, e senza lanci il prodotto "infinito per
    zero" vale NaN in virgola mobile. Ogni confronto con un NaN e' falso,
    quindi la regola sull'autonomia avrebbe taciuto proprio sul mezzo con i
    dati piu' rotti. Ora i cicli assenti valgono zero minuti e l'infinito
    resta dove deve stare, nel tempo di volo.
    """
    canadair.cruise_speed_kmh = 0.0

    timing = physics.mission_timing(
        canadair, base_lamezia.point, fire_sila.centroid, sea_source, sorties=0
    )

    assert timing.water_minutes == 0.0
    assert timing.total_minutes == float("inf")
    assert timing.total_minutes > canadair.endurance_minutes


def test_mezzo_fermo_viene_respinto(scenario_minimo: Scenario) -> None:
    """Un mezzo con velocita' dichiarata nulla non puo' arrivare da nessuna parte."""
    elicottero = scenario_minimo.vehicle_by_id("ELI_01")
    assert elicottero is not None
    elicottero.cruise_speed_kmh = 0.0
    piano = _piano(_assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_500))

    result = validate_plan(piano, scenario_minimo)

    assert not result.is_valid
    bloccanti = _regole_bloccanti(result)
    assert "arrival_time" in bloccanti
    assert "endurance" in bloccanti


def test_velocita_nulla_non_solleva_divisione_per_zero(canadair) -> None:
    """La divisione per la velocita' e' protetta a monte, in un posto solo."""
    canadair.cruise_speed_kmh = 0.0

    assert physics.travel_minutes(10.0, 0.0) == float("inf")
    assert physics.travel_minutes(10.0, -5.0) == float("inf")
    assert physics.leg_minutes(
        canadair, GeoPoint(39.0, 16.0), GeoPoint(38.0, 16.0)
    ) == float("inf")


def test_lanci_necessari_arrotondano_per_eccesso(canadair) -> None:
    """Mezzo lancio non esiste: un litro di residuo costa un ciclo intero.

    Il troncamento a intero che c'era prima rispondeva "un lancio" a 6137,5
    litri e "nessun lancio" a mezzo litro: in entrambi i casi il validatore
    avrebbe considerato sufficiente meno acqua di quella richiesta.
    """
    assert physics.required_sorties(canadair, 6_137.5) == 2
    assert physics.required_sorties(canadair, 0.5) == 1
    assert physics.required_sorties(canadair, 12_274) == 2
    assert physics.required_sorties(canadair, 12_275) == 3
    assert physics.required_sorties(canadair, -10) == 0
    assert physics.required_sorties(canadair, float("inf")) == 0


def test_litri_erogabili_non_sono_mai_negativi(canadair) -> None:
    """Un massimo negativo renderebbe colpevole qualsiasi piano, anche quello vuoto."""
    canadair.water_capacity_liters = -100

    assert physics.deliverable_liters(canadair, -3) == 0
    assert physics.deliverable_liters(canadair, 2) == 0
    assert physics.required_sorties(canadair, 1_000) == 0


def test_finestra_temporale_che_scavalca_la_mezzanotte(
    scenario_minimo: Scenario,
) -> None:
    """Una missione dalle 23:00 all'01:00 non rispetta una finestra 06:00-12:00.

    Confrontando le ore del giorno il rientro diventava "l'una", cioe' un
    numero piu' piccolo della partenza, e la finestra risultava rispettata:
    esattamente il piano piu' sbagliato passava. Il confronto ora si fa sui
    minuti assoluti, ancorati al giorno in cui il mezzo parte.

    Il piano qui ha una sola missione, e scavalca la mezzanotte. Serve che sia
    una sola: con due missioni ne basterebbe una che rientra prima delle
    ventiquattro per far scattare il vincolo comunque, e il test passerebbe
    anche con il confronto sbagliato.
    """
    scenario_minimo.timestamp = scenario_minimo.timestamp.replace(hour=23, minute=0)
    piano = _piano(_assegnazione("CAN_01", "F_WUI", 0.0, 31.0, 124.0, 2, 12_274))
    cs = _cs(
        TimeWindow(
            constraint_id="C_MEZZANOTTE",
            selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            from_minute=6 * 60,
            to_minute=12 * 60,
        )
    )

    result = validate_plan(piano, scenario_minimo, cs)

    assert not result.is_valid
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert violazione.entities["vehicle_id"] == "CAN_01"
    assert "C_MEZZANOTTE" in violazione.message
    # Partenza alle 23:00 del giorno dello scenario, rientro all'01:04 del
    # giorno dopo: sono i due orari che il confronto sbagliato invertiva.
    assert "dalle 23:00 alle 01:04" in violazione.message


def test_finestra_temporale_rispettata_non_blocca(scenario_minimo: Scenario) -> None:
    """Controprova: dentro la finestra il vincolo deve tacere.

    Senza questa meta' il test precedente sarebbe soddisfatto anche da un
    validatore che contesta qualunque finestra temporale.
    """
    cs = _cs(
        TimeWindow(
            constraint_id="C_WIN_OK",
            selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            from_minute=11 * 60,
            to_minute=14 * 60,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    assert result.is_valid, format_report(result)


def test_ogni_missione_si_misura_dalla_base_anche_la_seconda(
    scenario_minimo: Scenario,
) -> None:
    """Il limite fisico di arrivo non dipende da dove il mezzo e' stato prima.

    Una missione termina con il rientro, quindi la seconda parte dalla base
    come la prima: Lamezia-Sila sono 9,5 minuti di volo piu' 12 di decollo,
    Lamezia-Reggio 18,2 piu' 12, e la partenza al minuto 66 della seconda
    missione non sposta il suo limite di percorrenza. Si controllano insieme le due cose che devono restare
    d'accordo, il limite usato per accusare e il tempo usato per la metrica,
    perche' sono lo stesso numero e devono continuare a esserlo.
    """
    piano = _piano_due_missioni()
    floors = arrival_floors(piano, scenario_minimo)

    assert floors[0][0] == pytest.approx(21.49, abs=0.05)
    assert floors[1][0] == pytest.approx(30.21, abs=0.05)
    assert floors[1][1] == pytest.approx(101.08, abs=0.05)

    result = validate_plan(piano, scenario_minimo)

    assert result.is_valid, format_report(result)
    # Primo arrivo per focolaio, uno per incendio: (0 + 21,49) e (66 + 30,21).
    assert result.metrics.mean_response_minutes == pytest.approx(58.85, abs=0.05)


def test_il_tempo_medio_e_per_focolaio_non_per_assegnazione(
    scenario_minimo: Scenario,
) -> None:
    """La convenzione dichiarata: conta il primo mezzo che tocca il fuoco.

    Due Canadair sullo stesso incendio non fanno media fra i loro arrivi: il
    secondo rinforza lo spegnimento, non la prontezza. Qui l'elicottero arriva
    sulla Sila al minuto 13 e il Canadair al 22: la risposta della Sila e'
    12,61 minuti, cioe' il limite del piu' veloce ad arrivare (6,61 di volo
    piu' 6 di decollo), non la media dei due.
    """
    piano = _piano(
        _assegnazione("ELI_01", "F_SILA", 0.0, 13.0, 52.0, 1, 1_500),
        _assegnazione("CAN_01", "F_SILA", 0.0, 22.0, 54.0, 1, 6_137),
    )

    result = validate_plan(piano, scenario_minimo)

    assert result.is_valid, format_report(result)
    # Un solo focolaio servito: la media coincide con quell'unica osservazione.
    assert result.metrics.mean_response_minutes == pytest.approx(12.61, abs=0.05)
    assert result.metrics.p90_response_minutes == pytest.approx(12.61, abs=0.05)


def test_tolleranza_negativa_rifiutata(scenario_minimo: Scenario) -> None:
    """Un margine negativo capovolge i confronti: meglio l'eccezione subito.

    Le regole sono scritte come "trovato piu' tolleranza sotto il limite": con
    un numero negativo il validatore comincia a respingere piani corretti e
    nessun messaggio lo spiega.
    """
    with pytest.raises(ValueError):
        Tolerance(minutes=-1.0)
    with pytest.raises(ValueError):
        Tolerance(liters_ratio=-0.01)
    with pytest.raises(ValueError):
        validate_plan(piano_corretto(), scenario_minimo, tolerance=-1.0)


def test_riserva_con_conteggio_e_scadenza(scenario_minimo: Scenario) -> None:
    """Riserva temporanea e riserva parziale insieme: la scadenza non si ignora.

    "Tienimene uno fermo fino al minuto X" non e' "tienimene uno fermo per
    sempre": se nessuna partenza precede X il vincolo e' rispettato, anche se
    piu' tardi tutti i mezzi decollano.
    """
    scaduta = _cs(
        ReserveVehicle(
            constraint_id="C_RES_SCADUTA",
            selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            count=1,
            until_minute=0,
        )
    )
    in_vigore = _cs(
        ReserveVehicle(
            constraint_id="C_RES_VIVA",
            selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            count=1,
            until_minute=30,
        )
    )

    assert validate_plan(piano_corretto(), scenario_minimo, scaduta).is_valid
    non_valido = validate_plan(piano_corretto(), scenario_minimo, in_vigore)
    assert not non_valido.is_valid
    assert "fino al minuto 30" in non_valido.blocking[0].message


def test_percentile_al_rango_piu_vicino() -> None:
    """Il p90 di dieci valori e' il nono, non il decimo e non un interpolato.

    L'epsilon nel calcolo del rango esiste perche' 0,9 non e' rappresentabile
    in binario: senza, per certe lunghezze del campione l'arrotondamento per
    eccesso scatta di un posto e il percentile si sposta silenziosamente.
    """
    valori = [float(x) for x in range(1, 11)]

    assert _percentile_nearest_rank(valori, 0.9) == pytest.approx(9.0)
    assert _percentile_nearest_rank(valori, 1.0) == pytest.approx(10.0)
    assert _percentile_nearest_rank([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.0)
    assert _percentile_nearest_rank([7.0], 0.9) == pytest.approx(7.0)
    assert _percentile_nearest_rank([], 0.9) == pytest.approx(0.0)


def test_la_tolleranza_finisce_intera_nel_json(scenario_minimo: Scenario) -> None:
    """Chi rilegge un esperimento deve poter sapere con quale margine e' passato."""
    payload = json.loads(to_json(validate_plan(piano_corretto(), scenario_minimo)))

    attesi = {campo.name for campo in dataclasses.fields(Tolerance)}
    assert set(payload["tolerance"]) == attesi


# --------------------------------------------------------------------------
# Determinismo
# --------------------------------------------------------------------------


def test_due_validazioni_danno_lo_stesso_resoconto(scenario_minimo: Scenario) -> None:
    """Stesso piano, stesso scenario, stesso testo: nessuna iterazione disordinata.

    La rigiocata della stagione confronta Tagliafuoco e baseline sugli stessi
    scenari: se l'ordine delle violazioni o la scelta di una sorgente
    cambiassero da un'esecuzione all'altra, la differenza fra i due sistemi
    non sarebbe piu' attribuibile ai due sistemi.
    """
    piano = _piano(
        _assegnazione("ELI_01", "F_SILA", 0.0, 1.0, 999.0, 1, 50_000),
        _assegnazione("CAN_01", "F_WUI", 0.0, 2.0, 400.0, 9, 90_000),
    )

    primo = to_json(validate_plan(piano, scenario_minimo))
    secondo = to_json(validate_plan(piano, scenario_minimo))

    assert primo == secondo
    assert format_report(validate_plan(piano, scenario_minimo)) == format_report(
        validate_plan(piano, scenario_minimo)
    )


def test_le_missioni_si_ordinano_per_partenza_non_per_indice(
    scenario_minimo: Scenario,
) -> None:
    """L'ordine di elencazione nel piano non cambia il limite fisico calcolato.

    Un piano puo' elencare le missioni in qualunque ordine: e' la sequenza
    temporale a dire da dove parte il mezzo, e il validatore deve ricostruirla
    invece di fidarsi dell'ordine della lista.
    """
    prima = _piano(
        _assegnazione("CAN_01", "F_SILA", 0.0, 22.0, 60.0, 1, 6_137),
        _assegnazione("CAN_01", "F_WUI", 90.0, 120.0, 180.0, 1, 6_137),
    )
    invertito = _piano(
        _assegnazione("CAN_01", "F_WUI", 90.0, 120.0, 180.0, 1, 6_137),
        _assegnazione("CAN_01", "F_SILA", 0.0, 22.0, 60.0, 1, 6_137),
    )

    limiti_prima = arrival_floors(prima, scenario_minimo)
    limiti_invertito = arrival_floors(invertito, scenario_minimo)

    # Indici scambiati, ma il focolaio della Sila resta il primo raggiunto e
    # quindi l'origine del secondo salto in entrambi i casi.
    assert limiti_prima[1][0] == pytest.approx(limiti_invertito[0][0])
    assert limiti_prima[0][0] == pytest.approx(limiti_invertito[1][0])


# --------------------------------------------------------------------------
# Geometria e orologio ai bordi
# --------------------------------------------------------------------------


def test_geodetica_su_punti_antipodali() -> None:
    """Meta' circonferenza terrestre, senza NaN.

    E' la ragione dichiarata per cui qui si usa Vincenty sferica al posto
    dell'haversine: sugli antipodi la forma con arcoseno perde precisione e
    puo' finire fuori dal dominio, quella con arcotangente a due argomenti no.
    La Calabria e' piccola e questo caso non capitera' mai, ma una formula che
    regge i suoi estremi e' una formula di cui ci si puo' fidare in mezzo.
    """
    from math import pi

    a = GeoPoint(lat=0.0, lon=0.0)
    b = GeoPoint(lat=0.0, lon=180.0)

    assert physics.geodesic_km(a, b) == pytest.approx(pi * 6371.0, rel=1e-9)
    assert physics.geodesic_km(b, a) == pytest.approx(physics.geodesic_km(a, b))


def test_orologio_oltre_le_ventiquattro_ore(instant) -> None:
    """Un orizzonte lungo riporta l'ora del giorno, non un numero oltre il giorno."""
    assert physics.clock_minute(instant, 0.0) == pytest.approx(11 * 60 + 30)
    assert physics.clock_minute(instant, 1440.0) == pytest.approx(11 * 60 + 30)
    assert physics.clock_minute(instant, 1500.0) == pytest.approx(12 * 60 + 30)
    # Anche un minuto di piano negativo (piano malformato) resta leggibile.
    assert 0.0 <= physics.clock_minute(instant, -2000.0) < physics.MINUTES_PER_DAY


def test_finestra_fino_a_fine_giornata(scenario_minimo: Scenario) -> None:
    """Il minuto 1440 si scrive 24:00, non 00:00.

    Lo schema ammette 1440 come chiusura di una finestra, ed e' il valore che
    il traduttore produce per "gli aerei fino a sera". Scriverlo "00:00"
    invertirebbe il senso della frase agli occhi di chi legge il resoconto.
    """
    cs = _cs(
        TimeWindow(
            constraint_id="C_GIORNATA",
            selector=_sel(SelectorField.VEHICLE_DOMAIN, "air"),
            from_minute=13 * 60,
            to_minute=1440,
        )
    )

    result = validate_plan(piano_corretto(), scenario_minimo, cs)

    # Le 11:30 sono prima delle 13:00: la finestra non e' ancora aperta.
    violazione = next(v for v in result.blocking if v.rule == "hard_constraint")
    assert "fra 13:00 e 24:00" in violazione.message


# --------------------------------------------------------------------------
# Determinismo
# --------------------------------------------------------------------------


def test_due_validazioni_identiche_danno_lo_stesso_esito(
    scenario_minimo: Scenario,
) -> None:
    """Stesso piano, stesso scenario, stesso resoconto fino all'ultima cifra.

    SPEC sezione 7 pretende che la rigiocata della stagione confronti piani
    sugli stessi scenari: se il validatore cambiasse anche solo l'ordine delle
    violazioni fra due esecuzioni, due archivi dello stesso esperimento non
    sarebbero confrontabili e non si saprebbe se la differenza viene dal piano
    o dal contatore. Le regole sulla sequenza sono il punto piu' esposto,
    perche' ordinano le missioni per minuto di partenza e a parita' di minuto
    devono rompere il pareggio sempre allo stesso modo. Il confronto e' sul
    JSON intero, non su `is_valid`: e' li' che un ordine instabile si vede.
    """
    piano = _piano_tre_missioni(partenza_terza=158.0)

    assert to_json(validate_plan(piano, scenario_minimo)) == to_json(
        validate_plan(piano, scenario_minimo)
    )
    pulito = _piano_tre_missioni()
    assert to_json(validate_plan(pulito, scenario_minimo)) == to_json(
        validate_plan(pulito, scenario_minimo)
    )
