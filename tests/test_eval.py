"""Test del sottosistema di valutazione.

Un misuratore sbagliato e' peggio di nessun misuratore: produce numeri, i numeri
finiscono in un resoconto e nessuno li mette in dubbio, perche' hanno l'aria di
essere stati calcolati. Questi test esistono per quello.

Il criterio di scrittura e' uno solo: ogni verifica deve avere una risposta
calcolabile a mente. I piani sono costruiti a mano con tempi e litri tondi, i
percentili si verificano su casi di cui si conosce il valore per definizione,
il confronto appaiato si prova su dati sintetici in cui il vincitore e' ovvio, e
il bootstrap su una distribuzione di cui si conosce la media. Dove la risposta
non e' calcolabile a mente si verifica una proprieta' (determinismo, monotonia,
simmetria) e non un valore.

Nessun test qui dentro chiama il solver: CP-SAT costa secondi per scenario e non
aggiungerebbe niente a cio' che si sta verificando, cioe' il misuratore. I
pianificatori usati sono la baseline e due finti pianificatori scritti apposta
per avere un esito noto.
"""

from __future__ import annotations

import copy
import json
import math
import random

import pytest

from tagliafuoco.domain.models import (
    Assignment,
    Plan,
    Scenario,
)
from tagliafuoco.eval import ablation, comparison, metrics, nl_benchmark, report
from tagliafuoco.eval.comparison import (
    bootstrap_mean_ci,
    compare_planners,
    paired_difference,
    permutation_sign_test,
    wilson_interval,
)
from tagliafuoco.eval.metrics import (
    COVERAGE_WATER_RATIO,
    compute_detail,
    compute_metrics,
    metric_value,
    percentile,
)
from tagliafuoco.nlu.schema import (
    ConstraintSet,
    MaxResponseTime,
    PrioritizeZone,
    Selector,
    SelectorField,
    Unsupported,
    ZoneKind,
)


# --------------------------------------------------------------------------
# Materiale di prova: piani costruiti a mano, con numeri tondi
# --------------------------------------------------------------------------


def _plan_a_mano() -> Plan:
    """Due assegnazioni su due focolai, tutti i numeri scelti per essere tondi.

    F_SILA chiede 120.000 litri e ne riceve 72.000, cioe' esattamente il 60 per
    cento: e' il caso di confine della soglia di copertura, ed e' li' che si
    vuole verificare che la soglia sia inclusiva.
    F_WUI chiede 52.000 litri e ne riceve 10.000: raggiunto, non coperto.
    """
    return Plan(
        plan_id="PLAN_MANO",
        horizon_minutes=240,
        producer="finto",
        assignments=[
            Assignment(
                vehicle_id="CAN_01",
                fire_id="F_SILA",
                departure_minute=0.0,
                arrival_minute=20.0,
                sorties=12,
                water_delivered_liters=72_000,
                release_minute=100.0,
                estimated_cost_eur=1000.0,
            ),
            Assignment(
                vehicle_id="ELI_01",
                fire_id="F_WUI",
                departure_minute=10.0,
                arrival_minute=30.0,
                sorties=7,
                water_delivered_liters=10_000,
                release_minute=60.0,
                estimated_cost_eur=500.0,
            ),
        ],
    )


def _scenari(scenario_minimo: Scenario, quanti: int) -> list[Scenario]:
    """Copie indipendenti dello scenario di prova, con identificativi distinti."""
    out = []
    for i in range(quanti):
        sc = copy.deepcopy(scenario_minimo)
        sc.scenario_id = f"SC_{i:02d}"
        out.append(sc)
    return out


def _piano_vuoto(scenario: Scenario, constraints=None) -> Plan:
    """Pianificatore che non assegna niente. Serve a provare che non vince."""
    return Plan(
        plan_id=f"VUOTO_{scenario.scenario_id}",
        horizon_minutes=scenario.horizon_minutes,
        producer="vuoto",
        unassigned_fire_ids=[f.fire_id for f in scenario.fires],
    )


def _piano_impossibile(scenario: Scenario, constraints=None) -> Plan:
    """Pianificatore che assegna un mezzo inesistente: il validatore deve bocciarlo."""
    return Plan(
        plan_id=f"ROTTO_{scenario.scenario_id}",
        horizon_minutes=scenario.horizon_minutes,
        producer="rotto",
        assignments=[
            Assignment(
                vehicle_id="NON_ESISTE",
                fire_id=scenario.fires[0].fire_id,
                departure_minute=0.0,
                arrival_minute=5.0,
                sorties=1,
                water_delivered_liters=1_000,
                release_minute=30.0,
            )
        ],
    )


# --------------------------------------------------------------------------
# Metriche su un piano calcolabile a mente
# --------------------------------------------------------------------------


def test_metriche_di_un_piano_costruito_a_mano(scenario_minimo: Scenario):
    """Ogni campo ha un valore che si ottiene a mente dai numeri del piano."""
    m = compute_metrics(_plan_a_mano(), scenario_minimo)

    assert m.fires_total == 2
    # F_SILA riceve il 60 per cento esatto: la soglia e' inclusiva.
    assert m.fires_covered == 1
    assert m.fires_wui_total == 1  # solo F_WUI sta sotto i 2 km dal costruito
    assert m.fires_wui_covered == 0  # raggiunto, ma con il 19 per cento dell'acqua
    assert m.water_demanded_liters == 172_000
    assert m.water_delivered_liters == 82_000
    # Tempi di risposta per focolaio: 20 e 30 minuti.
    assert m.mean_response_minutes == pytest.approx(25.0)
    # p90 di due osservazioni con interpolazione lineare: 20 + (30-20)*0.9.
    assert m.p90_response_minutes == pytest.approx(29.0)
    # Minuti-mezzo: (100-0) + (60-10).
    assert m.total_vehicle_minutes == pytest.approx(150.0)
    assert m.total_cost_eur == pytest.approx(1500.0)
    assert m.vehicles_used == 2


def test_la_copertura_dipende_dalla_soglia_dichiarata(scenario_minimo: Scenario):
    """Cambiare la soglia cambia la copertura, ed e' il motivo per cui e' una costante."""
    plan = _plan_a_mano()
    severa = compute_metrics(plan, scenario_minimo, coverage_threshold=0.9)
    permissiva = compute_metrics(plan, scenario_minimo, coverage_threshold=0.0)
    assert severa.fires_covered == 0
    assert permissiva.fires_covered == 2
    assert compute_metrics(plan, scenario_minimo).fires_covered == 1
    with pytest.raises(ValueError):
        compute_metrics(plan, scenario_minimo, coverage_threshold=1.5)


def test_focolaio_raggiunto_non_significa_coperto(scenario_minimo: Scenario):
    """Le due convenzioni convivono e vanno riportate insieme."""
    detail = compute_detail(_plan_a_mano(), scenario_minimo)
    assert detail.fires_attended == 2
    assert detail.metrics.fires_covered == 1
    assert detail.fires_wui_attended == 1
    assert detail.responses_counted == 2
    assert detail.late_fires == 0  # 20 e 30 minuti, sotto la soglia di tardivita'
    assert detail.coverage_threshold == COVERAGE_WATER_RATIO


def test_il_tempo_di_risposta_e_del_primo_mezzo(scenario_minimo: Scenario):
    """Due mezzi sullo stesso focolaio: conta chi arriva prima, e i litri si sommano."""
    plan = Plan(
        plan_id="DUE_SULLO_STESSO",
        horizon_minutes=240,
        assignments=[
            Assignment("CAN_01", "F_SILA", 0.0, 40.0, 1, 60_000, 80.0),
            Assignment("ELI_01", "F_SILA", 0.0, 10.0, 1, 30_000, 50.0),
        ],
    )
    detail = compute_detail(plan, scenario_minimo)
    assert detail.response_minutes_by_fire["F_SILA"] == 10.0
    assert detail.metrics.mean_response_minutes == pytest.approx(10.0)
    # 90.000 litri su 120.000 richiesti: il 75 per cento, sopra la soglia.
    assert detail.water_received_by_fire["F_SILA"] == 90_000
    assert detail.metrics.fires_covered == 1
    # Una sola osservazione: il percentile e' quell'osservazione.
    assert detail.metrics.p90_response_minutes == pytest.approx(10.0)


def test_l_acqua_non_si_compensa_fra_focolai(scenario_minimo: Scenario):
    """Un eccesso su un incendio non copre la mancanza su un altro."""
    plan = Plan(
        plan_id="ECCESSO",
        horizon_minutes=240,
        assignments=[
            # Cinque volte il fabbisogno di F_SILA, niente su F_WUI.
            Assignment("CAN_01", "F_SILA", 0.0, 10.0, 99, 600_000, 200.0),
        ],
    )
    m = compute_metrics(plan, scenario_minimo)
    assert m.water_delivered_liters == 120_000  # troncato al fabbisogno del focolaio
    assert m.water_demanded_liters == 172_000
    assert m.water_fulfilment_ratio == pytest.approx(120_000 / 172_000)


def test_lo_spreco_d_acqua_resta_visibile(scenario_minimo: Scenario):
    """Il troncamento rende onesto il rapporto, non deve nascondere l'eccesso.

    SPEC.md sezione 6 chiede che un piano che versa il doppio del necessario lo
    mostri. Il campo del contratto porta la somma troncata, perche' senza
    troncamento l'eccesso su un focolaio maschererebbe la carenza su un altro
    proprio dentro `water_fulfilment_ratio`; la somma cruda e lo spreco stanno
    accanto, nel dettaglio, e nessuno dei due e' nascosto.
    """
    plan = Plan(
        plan_id="ECCESSO",
        horizon_minutes=240,
        assignments=[
            Assignment("CAN_01", "F_SILA", 0.0, 10.0, 99, 600_000, 200.0),
        ],
    )
    detail = compute_detail(plan, scenario_minimo)
    assert detail.metrics.water_delivered_liters == 120_000
    assert detail.water_declared_liters == 600_000
    assert detail.water_overshoot_liters == 480_000
    assert detail.to_dict()["water_overshoot_liters"] == 480_000
    # Un piano senza eccessi non inventa spreco.
    assert compute_detail(_plan_a_mano(), scenario_minimo).water_overshoot_liters == 0


def test_il_costo_si_ricostruisce_quando_il_piano_non_lo_dichiara(
    scenario_minimo: Scenario,
):
    """Un pianificatore che non compila il costo non deve risultare gratuito."""
    plan = Plan(
        plan_id="SENZA_COSTO",
        horizon_minutes=240,
        assignments=[
            # Il canadair costa 13.000 euro l'ora: due ore di impiego, 26.000.
            Assignment("CAN_01", "F_SILA", 0.0, 10.0, 1, 6_000, 120.0)
        ],
    )
    detail = compute_detail(plan, scenario_minimo)
    assert detail.costs_reconstructed == 1
    assert detail.metrics.total_cost_eur == pytest.approx(26_000.0)


def test_il_piano_non_viene_modificato_dalla_misura(scenario_minimo: Scenario):
    """Misurare e' una lettura: `Plan.metrics` resta cio' che il piano dichiara."""
    plan = _plan_a_mano()
    assert plan.metrics is None
    compute_metrics(plan, scenario_minimo)
    assert plan.metrics is None


# --------------------------------------------------------------------------
# Percentile
# --------------------------------------------------------------------------


def test_percentile_su_casi_noti():
    """Valori che si ricavano dalla definizione, senza eseguire il codice."""
    # h = (n-1)*q = 1.5, fra 2 e 3, a meta'.
    assert percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    # h = 8.1, fra x[8]=9 e x[9]=10, un decimo.
    assert percentile(list(range(1, 11)), 0.9) == pytest.approx(9.1)
    assert percentile([5, 1, 3], 0.0) == pytest.approx(1.0)
    assert percentile([5, 1, 3], 1.0) == pytest.approx(5.0)
    # L'ordine di ingresso non conta.
    assert percentile([3, 1, 2], 0.5) == percentile([1, 2, 3], 0.5)


def test_percentile_casi_degeneri():
    """Una sola osservazione e nessuna osservazione, entrambe dichiarate."""
    assert percentile([7.0], 0.9) == 7.0
    assert percentile([7.0], 0.1) == 7.0
    assert percentile([], 0.9) == 0.0
    assert percentile([], 0.9, default=-1.0) == -1.0
    with pytest.raises(ValueError):
        percentile([1, 2], 1.5)


def test_percentile_coerente_fra_i_due_moduli():
    """`comparison` ha una copia della formula: se divergesse, qui si vedrebbe."""
    valori = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
    ordinati = sorted(valori)
    for q in (0.0, 0.25, 0.5, 0.9, 1.0):
        assert comparison._percentile_sorted(ordinati, q) == pytest.approx(
            percentile(valori, q)
        )


# --------------------------------------------------------------------------
# Bootstrap, permutazione, Wilson
# --------------------------------------------------------------------------


def test_bootstrap_su_distribuzione_nota():
    """Su 1..100 la media e' 50.5 e l'intervallo deve contenerla, stretto."""
    valori = [float(v) for v in range(1, 101)]
    ci = bootstrap_mean_ci(valori, samples=500, rng=random.Random(7))
    assert ci.mean == pytest.approx(50.5)
    assert ci.low < 50.5 < ci.high
    # L'errore standard della media e' circa 2.9: l'intervallo al 95 per cento
    # sta largamente sotto i venti punti di ampiezza, contro un'escursione dei
    # dati di 99.
    assert (ci.high - ci.low) < 20.0
    assert ci.n == 100
    assert ci.trustworthy


def test_bootstrap_casi_degeneri():
    """Vuoto, singolo e valori tutti identici: tre risposte dichiarate."""
    vuoto = bootstrap_mean_ci([], rng=random.Random(1))
    assert (vuoto.mean, vuoto.low, vuoto.high, vuoto.n) == (0.0, 0.0, 0.0, 0)
    assert not vuoto.trustworthy

    singolo = bootstrap_mean_ci([4.0], rng=random.Random(1))
    assert singolo.low == singolo.high == 4.0
    assert not singolo.trustworthy

    costante = bootstrap_mean_ci([5.0] * 30, samples=200, rng=random.Random(1))
    assert costante.low == costante.high == 5.0
    assert costante.excludes_zero
    assert costante.trustworthy


def test_bootstrap_simmetrico_contiene_lo_zero():
    """Differenze simmetriche intorno a zero: l'intervallo deve ammettere lo zero."""
    valori = [1.0] * 25 + [-1.0] * 25
    ci = bootstrap_mean_ci(valori, samples=400, rng=random.Random(3))
    assert ci.low <= 0.0 <= ci.high
    assert not ci.excludes_zero


def test_bootstrap_deterministico_a_parita_di_seme():
    """Due esecuzioni con lo stesso seme danno lo stesso intervallo."""
    valori = [0.1, -0.3, 0.7, 0.2, -0.1, 0.4, 0.9, -0.2]
    a = bootstrap_mean_ci(valori, samples=300, rng=random.Random(42))
    b = bootstrap_mean_ci(valori, samples=300, rng=random.Random(42))
    assert (a.low, a.high) == (b.low, b.high)
    c = bootstrap_mean_ci(valori, samples=300, rng=random.Random(43))
    assert (a.low, a.high) != (c.low, c.high)


def test_permutazione_valore_p_esatto():
    """Dieci differenze tutte uguali: solo due assegnazioni di segno su 1024 sono
    estreme quanto quella osservata, quindi p vale 2/1024."""
    test = permutation_sign_test([2.0] * 10)
    assert test.exact
    assert test.permutations == 1024
    assert test.p_value == pytest.approx(2 / 1024)


def test_permutazione_casi_in_cui_non_si_puo_concludere():
    """Differenze nulle, una sola coppia, differenze simmetriche: sempre p alto."""
    assert permutation_sign_test([]).p_value == 1.0
    assert permutation_sign_test([0.0, 0.0, 0.0]).p_value == 1.0
    # Con una coppia sola, invertire il segno da' la stessa media in valore
    # assoluto: nessun campione di dimensione uno puo' essere significativo.
    assert permutation_sign_test([3.0]).p_value == 1.0
    assert permutation_sign_test([1.0, -1.0]).p_value == 1.0


def test_permutazione_campionata_oltre_la_soglia_esatta():
    """Sopra la soglia si campiona, e il valore p resta strettamente positivo."""
    valori = [1.0] * 20
    test = permutation_sign_test(valori, permutations=200, rng=random.Random(5))
    assert not test.exact
    assert 0.0 < test.p_value <= 1.0
    assert test.p_value == pytest.approx(1 / 201)


def test_wilson_non_dichiara_certezze_che_non_ha():
    """Zero prove, zero successi e successi pieni: i tre casi in cui l'intervallo
    normale ingenuo mente."""
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.0 < high < 0.5
    low, high = wilson_interval(10, 10)
    assert high == 1.0 and 0.5 < low < 1.0
    stretto = wilson_interval(500, 1000)
    largo = wilson_interval(5, 10)
    assert (stretto[1] - stretto[0]) < (largo[1] - largo[0])


# --------------------------------------------------------------------------
# Confronto appaiato
# --------------------------------------------------------------------------


def test_confronto_appaiato_su_dati_sintetici_con_risposta_ovvia():
    """A e' meglio di B di un punto fisso su ogni scenario: non c'e' niente da
    interpretare, e il confronto deve dirlo senza esitazioni."""
    base = [0.30, 0.55, 0.12, 0.80, 0.44, 0.61, 0.25, 0.70, 0.33, 0.50]
    migliorato = [v + 0.10 for v in base]
    d = paired_difference(
        "coverage_ratio", "solver", "baseline", migliorato, base
    )
    assert d.n_pairs == 10
    assert d.mean_difference == pytest.approx(0.10)
    assert d.wins_a == 10 and d.wins_b == 0
    assert d.interval.excludes_zero
    assert d.test.p_value == pytest.approx(2 / 1024)
    assert d.improvement is True
    assert "meglio" in d.verdict()


def test_il_confronto_appaiato_vede_cio_che_le_medie_nascondono():
    """La varianza fra scenari e' enorme, la differenza per scenario e' piccola e
    costante: e' il caso che motiva tutto il modulo."""
    base = [10.0, 90.0, 15.0, 85.0, 20.0, 80.0, 25.0, 75.0, 30.0, 70.0]
    migliorato = [v - 2.0 for v in base]
    d = paired_difference(
        "mean_response_minutes", "solver", "baseline", migliorato, base
    )
    assert d.mean_difference == pytest.approx(-2.0)
    assert d.improvement is True  # meno minuti e' meglio
    assert d.interval.excludes_zero
    # La deviazione dei valori grezzi e' circa trenta volte la differenza: un
    # confronto fra medie indipendenti non la distinguerebbe.
    dispersione = math.sqrt(
        sum((v - sum(base) / len(base)) ** 2 for v in base) / (len(base) - 1)
    )
    assert dispersione > 15 * abs(d.mean_difference)


def test_una_differenza_di_segno_alterno_non_e_distinguibile():
    """Se A vince meta' delle volte e perde l'altra meta', il verdetto deve dirlo."""
    base = [0.4] * 10
    alterno = [0.4 + (0.05 if i % 2 else -0.05) for i in range(10)]
    d = paired_difference("coverage_ratio", "a", "b", alterno, base)
    assert d.mean_difference == pytest.approx(0.0, abs=1e-9)
    assert not d.interval.excludes_zero
    assert "non distinguibile dal rumore" in d.verdict()


def test_il_valore_p_minimo_dipende_da_quante_coppie_ci_sono():
    """Con poche coppie il test sul segno non puo' essere significativo, e lo dice.

    Il test esatto dispone di 2^n assegnazioni di segno e le due piu' estreme
    danno la stessa media in valore assoluto: il minimo bilaterale e' 2/2^n. Con
    quattro coppie vale 0.125, quindi nemmeno quattro vittorie su quattro
    possono scendere sotto la soglia del cinque per cento. Chiamare "non
    significativa" quella differenza sarebbe leggere il numero di finestre e
    credere di leggere il divario fra i pianificatori.
    """
    poche = permutation_sign_test([2.0] * 4)
    assert poche.p_value == pytest.approx(2 / 16)
    assert poche.min_achievable_p == pytest.approx(0.125)
    assert poche.underpowered()
    assert not poche.significant()

    tante = permutation_sign_test([2.0] * 10)
    assert tante.min_achievable_p == pytest.approx(2 / 1024)
    assert not tante.underpowered()
    assert tante.significant()

    campionato = permutation_sign_test([1.0] * 20, permutations=200, rng=random.Random(5))
    assert campionato.min_achievable_p == pytest.approx(1 / 201)
    assert not campionato.underpowered()


def test_la_lettura_non_dichiara_prova_dove_il_test_non_puo_darla():
    """Intervallo e valore p possono non concordare: vince la lettura piu' cauta.

    Quattro scenari in cui A vince sempre dello stesso margine: l'intervallo
    bootstrap esclude lo zero, ma il valore p non puo' scendere sotto 0.125.
    Stampare "distinguibile dal rumore" accanto a quel p sarebbe una lettura
    sbagliata dei propri stessi numeri.
    """
    base = [0.30, 0.55, 0.12, 0.80]
    migliorato = [v + 0.10 for v in base]
    poche = paired_difference("coverage_ratio", "solver", "baseline", migliorato, base)
    assert poche.interval.excludes_zero
    assert not poche.test.significant()
    assert "coppie insufficienti" in poche.reading()
    assert "distinguibile dal rumore" not in poche.reading()
    assert "la prova statistica non c'e'" in poche.verdict()

    lunga_base = [0.30, 0.55, 0.12, 0.80, 0.44, 0.61, 0.25, 0.70, 0.33, 0.50]
    lunga = paired_difference(
        "coverage_ratio", "solver", "baseline", [v + 0.10 for v in lunga_base], lunga_base
    )
    assert lunga.reading() == "distinguibile dal rumore"

    # E la stessa cautela deve comparire fra le avvertenze del confronto,
    # che devono contare le COPPIE e non le finestre: una metrica non definita
    # su un piano toglie una coppia senza togliere una finestra.
    assert comparison.SIGNIFICANCE_ALPHA == 0.05
    result = comparison.ComparisonResult(
        label="finto",
        scenario_ids=[f"SC_{i}" for i in range(8)],
        planner_names=["a", "b"],
        reference="b",
        outcomes={"a": [], "b": []},
        differences=[poche],
        seed=1,
        bootstrap_samples=10,
        permutations=10,
        confidence_level=0.95,
        coverage_threshold=0.6,
    )
    avvertenze = comparison.methodological_warnings(result)
    assert any("la peggiore ne ha 4" in a for a in avvertenze)
    assert any("8 finestre" in a for a in avvertenze)


def test_le_coppie_senza_metrica_si_scartano_non_si_azzerano():
    """Un piano senza assegnazioni non ha tempo di risposta: la coppia va tolta."""
    d = paired_difference(
        "mean_response_minutes", "a", "b", [10.0, None, 12.0], [14.0, 20.0, None]
    )
    assert d.n_pairs == 1
    assert d.pairs_dropped == 2
    assert d.mean_difference == pytest.approx(-4.0)


def test_lunghezze_diverse_sono_un_errore():
    """Sequenze di lunghezza diversa non sono lo stesso insieme di scenari."""
    with pytest.raises(ValueError):
        paired_difference("coverage_ratio", "a", "b", [0.1, 0.2], [0.1])


def test_il_piano_vuoto_non_vince_sui_tempi(scenario_minimo: Scenario):
    """Zero minuti di risposta perche' non si e' fatto niente non e' un record."""
    detail_vuoto = compute_detail(_piano_vuoto(scenario_minimo), scenario_minimo)
    assert detail_vuoto.metrics.mean_response_minutes == 0.0
    assert metric_value(detail_vuoto, "mean_response_minutes") is None
    assert metric_value(detail_vuoto, "coverage_ratio") == 0.0

    detail_pieno = compute_detail(_plan_a_mano(), scenario_minimo)
    assert metric_value(detail_pieno, "mean_response_minutes") == pytest.approx(25.0)


def test_compare_planners_confronta_sugli_stessi_scenari(scenario_minimo: Scenario):
    """La baseline contro il pianificatore che non fa niente: esito noto in anticipo."""
    from tagliafuoco.baseline.nearest import plan_nearest

    scenari = _scenari(scenario_minimo, 6)
    result = compare_planners(
        scenari,
        [("nearest", plan_nearest), ("vuoto", _piano_vuoto)],
        reference="vuoto",
        bootstrap_samples=200,
        permutations=200,
        label="prova",
    )
    assert result.scenario_ids == [s.scenario_id for s in scenari]
    assert len(result.outcomes["nearest"]) == 6
    assert len(result.outcomes["vuoto"]) == 6

    raggiunti = result.difference("attendance_ratio", "nearest")
    assert raggiunti is not None
    assert raggiunti.mean_difference > 0
    assert raggiunti.improvement is True

    # Sui tempi non c'e' confronto possibile: il piano vuoto non ne ha.
    tempi = result.difference("mean_response_minutes", "nearest")
    assert tempi is not None
    assert tempi.n_pairs == 0
    assert tempi.pairs_dropped == 6

    aggregato = result.aggregate()
    assert aggregato["vuoto"]["fires_attended"] == 0
    assert aggregato["nearest"]["fires_attended"] > 0


def test_compare_planners_rifiuta_un_confronto_che_non_e_un_confronto(
    scenario_minimo: Scenario,
):
    """Un pianificatore solo, nomi ripetuti, riferimento inesistente: tutti errori."""
    from tagliafuoco.baseline.nearest import plan_nearest

    scenari = _scenari(scenario_minimo, 2)
    with pytest.raises(ValueError):
        compare_planners(scenari, [("solo", plan_nearest)])
    with pytest.raises(ValueError):
        compare_planners(
            scenari, [("a", plan_nearest), ("a", _piano_vuoto)]
        )
    with pytest.raises(ValueError):
        compare_planners(
            scenari,
            [("a", plan_nearest), ("b", _piano_vuoto)],
            reference="c",
        )
    with pytest.raises(KeyError):
        compare_planners(
            scenari,
            [("a", plan_nearest), ("b", _piano_vuoto)],
            metric_keys=("metrica_inventata",),
        )


def test_il_confronto_e_riproducibile(scenario_minimo: Scenario):
    """Stesso seme, stessi scenari, stessi intervalli: il resoconto e' rifacibile."""
    from tagliafuoco.baseline.nearest import plan_nearest, plan_priority

    scenari = _scenari(scenario_minimo, 4)
    args = dict(bootstrap_samples=150, permutations=150, validate=False)
    a = compare_planners(
        scenari, [("p", plan_priority), ("n", plan_nearest)], **args
    )
    b = compare_planners(
        scenari, [("p", plan_priority), ("n", plan_nearest)], **args
    )
    assert [d.to_dict() for d in a.differences] == [
        d.to_dict() for d in b.differences
    ]


def test_avvertenze_metodologiche_si_accendono_sui_numeri(scenario_minimo: Scenario):
    """Le avvertenze non sono decorative: nascono dal risultato appena calcolato."""
    from tagliafuoco.baseline.nearest import plan_nearest

    scenari = _scenari(scenario_minimo, 3)
    result = compare_planners(
        scenari,
        [("nearest", plan_nearest), ("vuoto", _piano_vuoto)],
        bootstrap_samples=100,
        permutations=100,
    )
    avvertenze = comparison.methodological_warnings(result)
    assert any("3 scenari" in a for a in avvertenze)
    assert any("coppie scartate" in a for a in avvertenze)


# --------------------------------------------------------------------------
# Ablazioni
# --------------------------------------------------------------------------


def test_ablazione_validatore_conta_i_piani_che_passerebbero(
    scenario_minimo: Scenario,
):
    """Un pianificatore che assegna un mezzo inesistente deve essere respinto ogni
    volta, e l'ablazione deve dire quante volte."""
    scenari = _scenari(scenario_minimo, 3)
    result = ablation.ablation_validator(
        scenari, [("rotto", _piano_impossibile), ("vuoto", _piano_vuoto)]
    )
    righe = result.extra["per_pianificatore"]
    assert righe["rotto"]["piani_invalidi"] == 3
    assert righe["rotto"]["regole_scattate"]
    assert righe["vuoto"]["piani_invalidi"] == 0
    assert any("non ha fermato nessun piano" in f for f in result.findings)
    assert result.question and result.method
    assert result.to_dict()["name"] == "validatore"


def test_ablazione_ordinamento_baseline(scenario_minimo: Scenario):
    """Le due baseline differiscono solo per l'ordine: l'esperimento deve girare e
    dichiarare cosa isola."""
    scenari = _scenari(scenario_minimo, 4)
    result = ablation.ablation_baseline_ordering(
        scenari, bootstrap_samples=100, permutations=100
    )
    assert result.comparison is not None
    assert result.comparison.reference == "tempo"
    assert result.findings
    assert "ordinamento" in result.question.lower() or "ordinamento" in result.name


# --------------------------------------------------------------------------
# Benchmark della traduzione
# --------------------------------------------------------------------------


def _corpus_minuscolo() -> list[nl_benchmark.BenchmarkCase]:
    """Quattro casi scelti dal corpus: uno facile, due con tolleranza, uno severo."""
    voluti = {"B001", "B009", "B041", "B046"}
    return [c for c in nl_benchmark.load_corpus() if c.case_id in voluti]


def _set(constraints, translator: str = "finto") -> ConstraintSet:
    return ConstraintSet(
        constraint_set_id="CS_TEST",
        source_text="",
        constraints=constraints,
        translator=translator,
    )


def test_il_corpus_si_legge_e_non_si_riscrive():
    """Cinquanta casi annotati, ognuno con almeno una lettura accettata."""
    corpus = nl_benchmark.load_corpus()
    assert len(corpus) == 50
    assert all(c.readings for c in corpus)
    ids = [c.case_id for c in corpus]
    assert len(ids) == len(set(ids))


def test_le_tolleranze_citano_casi_che_esistono_davvero():
    """Una tolleranza su un identificativo sbagliato non si accorgerebbe di nulla."""
    ids = {c.case_id for c in nl_benchmark.load_corpus()}
    assert set(nl_benchmark.CASE_TOLERANCES) <= ids
    for case_id, tolleranza in nl_benchmark.CASE_TOLERANCES.items():
        assert tolleranza.nota, f"la tolleranza su {case_id} non cita la nota"


def test_traduzione_perfetta_su_un_caso_facile():
    """Vincolo giusto, parametro giusto: corrispondenza esatta con i parametri."""
    caso = next(c for c in _corpus_minuscolo() if c.case_id == "B009")
    prodotto = _set(
        [MaxResponseTime(constraint_id="M1", minutes=20)]
    )
    esito = nl_benchmark.evaluate_case(caso, prodotto)
    assert esito.exact_set_match
    assert esito.exact_match_with_params
    assert esito.params_total == 1 and esito.params_correct == 1
    assert esito.f1 == pytest.approx(1.0)


def test_tipo_giusto_parametro_sbagliato():
    """Riconoscere il vincolo e sbagliare i minuti non e' una traduzione riuscita."""
    caso = next(c for c in _corpus_minuscolo() if c.case_id == "B009")
    prodotto = _set([MaxResponseTime(constraint_id="M1", minutes=30)])
    esito = nl_benchmark.evaluate_case(caso, prodotto)
    assert esito.exact_set_match  # il tipo c'e'
    assert not esito.exact_match_with_params  # il numero no
    assert esito.params_correct == 0


def test_sostituzione_di_tipo_finisce_nella_matrice_di_confusione():
    """Emettere il tipo sbagliato non e' una omissione piu' una allucinazione."""
    caso = next(c for c in _corpus_minuscolo() if c.case_id == "B009")
    prodotto = _set(
        [PrioritizeZone(constraint_id="P1", zone_kind=ZoneKind.WUI)]
    )
    esito = nl_benchmark.evaluate_case(caso, prodotto)
    assert esito.substitutions == (("MAX_RESPONSE_TIME", "PRIORITIZE_ZONE"),)
    assert not esito.omissions and not esito.hallucinations
    risultato = nl_benchmark.BenchmarkResult(
        label="finto", translator="finto", cases=[esito]
    )
    matrice = risultato.confusion_matrix()
    assert matrice["MAX_RESPONSE_TIME"]["PRIORITIZE_ZONE"] == 1
    punteggi = risultato.kind_scores()
    assert punteggi["MAX_RESPONSE_TIME"].false_negatives == 1
    assert punteggi["PRIORITIZE_ZONE"].false_positives == 1


def test_tolleranza_dichiarata_su_una_lettura_alternativa():
    """B041 ammette sia l'insieme vuoto sia una singola astensione."""
    caso = next(c for c in _corpus_minuscolo() if c.case_id == "B041")
    vuoto = nl_benchmark.evaluate_case(caso, _set([]))
    assert vuoto.exact_set_match
    assert vuoto.reading_index == 0

    astensione = nl_benchmark.evaluate_case(
        caso, _set([Unsupported(constraint_id="U1", reason="fuori repertorio")])
    )
    assert astensione.exact_set_match
    assert astensione.reading_index == 1
    assert "tolleranza dichiarata" in astensione.reading_origin


def test_dove_la_nota_non_concede_tolleranza_non_si_tollera():
    """B046 (ordine vuoto) dice esplicitamente: nessun UNSUPPORTED inventato."""
    caso = next(c for c in _corpus_minuscolo() if c.case_id == "B046")
    esito = nl_benchmark.evaluate_case(
        caso, _set([Unsupported(constraint_id="U1", reason="ordine vuoto")])
    )
    assert not esito.exact_set_match
    assert esito.hallucinations == ("UNSUPPORTED",)


def test_astensione_corretta_e_astensione_a_sproposito():
    """I due versi si misurano separatamente, altrimenti astenersi sempre vincerebbe."""
    casi = _corpus_minuscolo()

    def sempre_astenuto(order_text, scenario=None, constraint_set_id=None):
        return _set([Unsupported(constraint_id="U1", reason="non traduco niente")])

    risultato = nl_benchmark.run_benchmark(sempre_astenuto, cases=casi)
    astensione = risultato.abstention()
    # Nessuno dei quattro casi scelti attende UNSUPPORTED come unica risposta
    # obbligata, ma B041 lo ammette: li' l'astensione e' corretta.
    assert astensione["correct"]["successes"] == astensione["correct"]["trials"]
    assert astensione["spurious"]["successes"] >= 2
    assert astensione["spurious"]["rate"] > 0.0


def test_benchmark_su_un_sottoinsieme_minuscolo_del_corpus():
    """Il traduttore a regole gira senza chiave e produce un risultato leggibile."""
    casi = _corpus_minuscolo()
    risultato = nl_benchmark.run_benchmark(cases=casi, label="regole")
    assert risultato.n_cases == 4
    assert not risultato.notes  # nessun guasto del traduttore
    dati = risultato.to_dict()
    assert dati["exact_set_match"]["trials"] == 4
    assert 0.0 <= dati["exact_set_match"]["rate"] <= 1.0
    low, high = dati["exact_set_match"]["ci95"]
    assert low <= dati["exact_set_match"]["rate"] <= high
    assert dati["micro"]["precision"] <= 1.0
    # Su quattro casi l'intervallo deve essere largo: e' il punto.
    assert (high - low) > 0.3


def test_un_traduttore_che_esplode_non_ferma_il_benchmark():
    """Il guasto su un caso e' un dato, non un motivo per perdere gli altri."""
    casi = _corpus_minuscolo()

    def rotto(order_text, scenario=None, constraint_set_id=None):
        if order_text.startswith("Voglio"):
            raise RuntimeError("guasto simulato")
        return _set([])

    risultato = nl_benchmark.run_benchmark(rotto, cases=casi)
    assert risultato.n_cases == 4
    assert len(risultato.notes) == 1
    assert any(c.error for c in risultato.cases)


def test_confronto_fra_traduttori_senza_chiave_si_astiene():
    """Senza chiave il traduttore con modello e' il traduttore a regole: non si
    confronta un metodo con se' stesso."""
    casi = _corpus_minuscolo()
    confronto = nl_benchmark.compare_translators(cases=casi)
    if confronto.model is None:
        assert confronto.skipped_reason
        assert any("non eseguito" in r for r in confronto.conclusion_lines())
    else:  # pragma: no cover - solo su una macchina con la chiave
        assert confronto.difference is not None


# --------------------------------------------------------------------------
# Resoconto
# --------------------------------------------------------------------------


def test_il_resoconto_mostra_l_avvertenza_e_le_incertezze(scenario_minimo: Scenario):
    """Un numero nudo non deve poter uscire dal resoconto."""
    from rich.console import Console
    import io

    from tagliafuoco.baseline.nearest import plan_nearest

    scenari = _scenari(scenario_minimo, 4)
    result = compare_planners(
        scenari,
        [("nearest", plan_nearest), ("vuoto", _piano_vuoto)],
        bootstrap_samples=100,
        permutations=100,
    )
    console = Console(record=True, width=200, no_color=True, file=io.StringIO())
    report.render_comparison(result, console)
    testo = console.export_text()
    assert "simulatore" in testo
    assert "avrebbe PROPOSTO" in testo
    assert "p=" in testo  # il valore p accanto a ogni differenza
    assert "IC" in testo or "intervallo" in testo.lower()


def test_il_resoconto_del_benchmark_si_stampa():
    """Il benchmark ha tabelle proprie: devono reggere anche su quattro casi."""
    from rich.console import Console
    import io

    risultato = nl_benchmark.run_benchmark(cases=_corpus_minuscolo())
    console = Console(record=True, width=200, no_color=True, file=io.StringIO())
    report.render_benchmark(risultato, console)
    testo = console.export_text()
    assert "allucinazione" in testo
    assert "astensione" in testo


def test_serializzazione_e_salvataggio(tmp_path, scenario_minimo: Scenario):
    """Il risultato grezzo si riapre: senza questo, un esperimento va rifatto."""
    import json

    from tagliafuoco.baseline.nearest import plan_nearest

    result = compare_planners(
        _scenari(scenario_minimo, 3),
        [("nearest", plan_nearest), ("vuoto", _piano_vuoto)],
        bootstrap_samples=50,
        permutations=50,
    )
    percorso = report.save_json(result, "prova", directory=tmp_path, stamp=False)
    assert percorso.exists()
    riletto = json.loads(percorso.read_text(encoding="utf-8"))
    assert riletto["scenarios"] == 3
    assert riletto["settings"]["seed"] == comparison.DEFAULT_SEED
    assert riletto["differences"]


# --------------------------------------------------------------------------
# Gli script eseguibili
# --------------------------------------------------------------------------
#
# Gli script sono la parte della valutazione che una persona lancia davvero, e
# sono anche la parte che nessuno prova finche' non serve. Qui non si verifica
# il risultato numerico (lo fanno i test sopra, sui moduli): si verifica che la
# catena regga, che i filtri vengano dichiarati e che le due situazioni in cui
# un pianificatore non produce un piano restino distinguibili.


def _scripts_sul_path() -> None:
    """Aggiunge `scripts/` al path, come fa `tests/test_architettura.py`."""
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))


def test_i_filtri_del_corpus_si_dichiarano():
    """Restringere il corpus cambia i numeri, quindi il filtro va detto."""
    _scripts_sul_path()
    import valuta_traduzione

    corpus = nl_benchmark.load_corpus()
    scelti, applicati = valuta_traduzione.select_cases(
        corpus, difficolta="facile", categoria="", limit=None
    )
    assert scelti, "il corpus ha casi facili"
    assert all(c.difficolta == "facile" for c in scelti)
    assert any("facile" in riga for riga in applicati)

    primi, applicati_due = valuta_traduzione.select_cases(
        corpus, difficolta="", categoria="", limit=3
    )
    assert len(primi) == 3
    assert any("primi 3" in riga for riga in applicati_due)

    interi, nessuno = valuta_traduzione.select_cases(
        corpus, difficolta="", categoria="", limit=None
    )
    assert len(interi) == len(corpus)
    assert nessuno == [], "senza filtri non si dichiara nessuna restrizione"


def test_lo_script_della_traduzione_gira_senza_chiave(tmp_path):
    """Il percorso completo dello script deve funzionare con le sole regole."""
    _scripts_sul_path()
    import json

    import valuta_traduzione

    codice = valuta_traduzione.main(
        [
            "--casi",
            "4",
            "--peggiori",
            "2",
            "--output",
            str(tmp_path),
            "--nome",
            "prova_traduzione",
            "--solo-regole",
        ]
    )
    assert codice == 0
    salvati = list(tmp_path.glob("prova_traduzione*.json"))
    assert len(salvati) == 1
    riletto = json.loads(salvati[0].read_text(encoding="utf-8"))
    assert riletto["corpus"]["casi_valutati"] == 4
    assert riletto["traduttori"]["modello"] is False
    assert riletto["confronto"]["rules"]["n_cases"] == 4
    # L'avvertenza sui limiti viaggia con i numeri, anche nel file.
    assert any("PROPOSTO" in riga for riga in riletto["limiti"])


def test_lo_script_della_traduzione_rifiuta_un_filtro_vuoto():
    """Un filtro che non lascia nessun caso e' un errore, non un risultato di zero."""
    _scripts_sul_path()
    import valuta_traduzione

    assert valuta_traduzione.main(["--categoria", "non_esiste"]) == 2


def test_lo_script_della_stagione_rifiuta_un_periodo_rovesciato():
    """Un periodo al contrario si ferma subito: nessun dato, nessun numero."""
    _scripts_sul_path()
    import valuta_stagione

    assert valuta_stagione.main(["--da", "2025-08-06", "--a", "2025-08-05"]) == 2


def test_lo_script_distingue_il_pianificatore_rotto_da_quello_lento(
    scenario_minimo: Scenario,
):
    """Piano mancante e piano vuoto non sono la stessa notizia.

    Un pianificatore che solleva un'eccezione ha un difetto; uno che non chiude
    entro il budget ha bisogno di piu' tempo. Confonderli nella stessa riga del
    resoconto farebbe passare un difetto per lentezza.
    """
    _scripts_sul_path()
    import valuta_stagione

    def _esplode(scenario: Scenario, constraints=None) -> Plan:
        raise RuntimeError("modello incoerente")

    def _non_conclude(scenario: Scenario, constraints=None) -> Plan:
        return Plan(
            plan_id=f"UNK_{scenario.scenario_id}",
            horizon_minutes=scenario.horizon_minutes,
            producer="lento",
            solver_status="UNKNOWN",
        )

    result = compare_planners(
        _scenari(scenario_minimo, 3),
        [("rotto", _esplode), ("lento", _non_conclude)],
        bootstrap_samples=50,
        permutations=50,
        validate=False,
    )
    note = valuta_stagione.solver_notes(result)
    testo = "\n".join(note)
    assert "eccezione" in testo
    assert "budget" in testo
    rotte = [n for n in note if n.startswith("rotto")]
    lente = [n for n in note if n.startswith("lento")]
    assert len(rotte) == 1 and "eccezione" in rotte[0]
    assert len(lente) == 1 and "budget" in lente[0]


# --------------------------------------------------------------------------
# Irrobustimento: regressioni su difetti trovati eseguendo davvero
# --------------------------------------------------------------------------
#
# Ogni test di questa sezione nasce da un guasto osservato lanciando gli script,
# non leggendo il codice. Il criterio resta quello del resto del file: la
# risposta si calcola a mente, e dove non si puo' si verifica una proprieta'.


def test_le_sigle_dei_tipi_di_vincolo_sono_tutte_diverse():
    """Due tipi diversi non possono chiamarsi allo stesso modo in una tabella.

    La regola precedente mandava FORCE_ASSIGNMENT e FORBID_ASSIGNMENT sulla
    stessa sigla. Nella matrice di confusione quello voleva dire due righe
    omonime, cioe' l'impossibilita' di distinguere lo scambio fra un vincolo e
    la sua negazione: esattamente la confusione che la matrice serve a mostrare.
    """
    from tagliafuoco.nlu.schema import ConstraintKind

    sigle = {k.value: report._short(k.value) for k in ConstraintKind}
    assert len(set(sigle.values())) == len(sigle), sigle
    assert sigle["FORCE_ASSIGNMENT"] != sigle["FORBID_ASSIGNMENT"]
    # La voce "(nessuno)" della matrice non e' un tipo e resta leggibile.
    assert report._short(nl_benchmark.NOTHING) == nl_benchmark.NOTHING


def test_litri_negativi_non_producono_una_percentuale_impossibile(
    scenario_minimo: Scenario,
):
    """Un piano malformato non deve far stampare "acqua erogata: -300%".

    `PlanMetrics.water_fulfilment_ratio` e' un `min(1.0, ...)` senza pavimento:
    senza il troncamento difensivo qui, una sola assegnazione con litri negativi
    uscirebbe dal resoconto come una percentuale negativa.
    """
    plan = Plan(
        plan_id="LITRI_NEGATIVI",
        horizon_minutes=240,
        assignments=[
            Assignment("CAN_01", "F_SILA", 0.0, 10.0, 1, -300_000, 60.0),
        ],
    )
    detail = compute_detail(plan, scenario_minimo)
    assert detail.metrics.water_delivered_liters == 0
    assert detail.water_declared_liters == 0
    assert 0.0 <= detail.metrics.water_fulfilment_ratio <= 1.0


def test_le_assegnazioni_fuori_scenario_si_escludono_e_si_contano(
    scenario_minimo: Scenario,
):
    """Il denominatore e' lo scenario: un focolaio che non c'e' non entra nei tempi.

    Senza questo filtro `responses_counted` poteva superare `fires_total` e la
    media dei tempi conteneva incendi assenti dallo scenario, cioe' un numero
    sbagliato e non riconoscibile come tale.
    """
    plan = Plan(
        plan_id="FUORI_SCENARIO",
        horizon_minutes=240,
        assignments=[
            Assignment("CAN_01", "F_SILA", 0.0, 10.0, 1, 72_000, 60.0, None, 100.0),
            Assignment(
                "CAN_01", "F_ALTROVE", 0.0, 200.0, 1, 50_000, 260.0, None, 100.0
            ),
        ],
    )
    detail = compute_detail(plan, scenario_minimo)
    assert detail.assignments_total == 2
    assert detail.assignments_off_scenario == 1
    assert detail.responses_counted == 1
    assert detail.responses_counted <= detail.metrics.fires_total
    # Dieci minuti soli: il focolaio inesistente non tira su la media.
    assert detail.metrics.mean_response_minutes == pytest.approx(10.0)
    assert "F_ALTROVE" not in detail.response_minutes_by_fire
    assert any("non combaciano" in riga for riga in detail.caveats())
    # I minuti-mezzo restano su tutto il piano: sono cio' che il piano impegna.
    assert detail.metrics.total_vehicle_minutes == pytest.approx(320.0)


def test_un_costo_non_ricostruibile_si_dichiara_invece_di_sparire(
    scenario_minimo: Scenario,
):
    """Saltare in silenzio un costo che non si sa produce un totale reticente.

    L'assegnazione non dichiara il costo e cita un mezzo che nello scenario non
    c'e': non esiste un costo orario da cui ricostruirlo. Il totale che ne esce
    e' una sottostima, e il resoconto lo deve dire.
    """
    plan = Plan(
        plan_id="COSTO_IGNOTO",
        horizon_minutes=240,
        assignments=[
            Assignment("MEZZO_FANTASMA", "F_SILA", 0.0, 10.0, 1, 72_000, 130.0),
        ],
    )
    detail = compute_detail(plan, scenario_minimo)
    assert detail.costs_unknown == 1
    assert detail.costs_reconstructed == 0
    assert detail.metrics.total_cost_eur == 0.0
    assert any("sottostima" in riga for riga in detail.caveats())
    assert detail.to_dict()["costs_unknown"] == 1
    assert metrics.summarise_details([detail])["costs_unknown"] == 1


def test_l_avvertenza_sul_costo_incompleto_arriva_al_confronto(
    scenario_minimo: Scenario,
):
    """Un conteggio che resta dentro il singolo scenario non lo legge nessuno."""

    def _costo_ignoto(scenario: Scenario, constraints=None) -> Plan:
        return Plan(
            plan_id=f"IGNOTO_{scenario.scenario_id}",
            horizon_minutes=scenario.horizon_minutes,
            producer="ignoto",
            assignments=[
                Assignment(
                    "MEZZO_FANTASMA",
                    scenario.fires[0].fire_id,
                    0.0,
                    10.0,
                    1,
                    1_000,
                    60.0,
                )
            ],
        )

    result = compare_planners(
        _scenari(scenario_minimo, 3),
        [("ignoto", _costo_ignoto), ("vuoto", _piano_vuoto)],
        bootstrap_samples=50,
        permutations=50,
        validate=False,
    )
    avvertenze = "\n".join(comparison.methodological_warnings(result))
    assert "sottostima" in avvertenze


def test_valori_identici_non_si_leggono_come_rumore():
    """Nessuna differenza e differenza invisibile sono notizie opposte.

    Con due pianificatori che danno lo stesso valore su ogni finestra, dire
    "servirebbero piu' finestre" manda a raccogliere dati che non cambierebbero
    niente.
    """
    uguali = paired_difference(
        "coverage_ratio",
        "A",
        "B",
        [0.5] * 6,
        [0.5] * 6,
        bootstrap_samples=50,
        permutations=50,
    )
    assert uguali.identical
    assert uguali.reading() == "valori identici"
    assert "stesso valore" in uguali.verdict()

    diversi = paired_difference(
        "coverage_ratio",
        "A",
        "B",
        [0.5, 0.6, 0.4],
        [0.5, 0.5, 0.5],
        bootstrap_samples=50,
        permutations=50,
    )
    assert not diversi.identical
    assert diversi.reading() == "non distinguibile dal rumore"


def test_il_verdetto_usa_la_preposizione_giusta():
    """Il verdetto lo legge una persona, e "sembra pari di tempo" non e' italiano."""
    pari = paired_difference(
        "coverage_ratio",
        "A",
        "B",
        [0.5, 0.6, 0.4],
        [0.5, 0.5, 0.5],
        bootstrap_samples=50,
        permutations=50,
    )
    assert "pari a B" in pari.verdict()
    assert "pari di" not in pari.verdict()

    meglio = paired_difference(
        "mean_response_minutes",
        "A",
        "B",
        [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
        [20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0],
        bootstrap_samples=50,
        permutations=50,
    )
    assert "meglio di B" in meglio.verdict()


def test_tutte_le_tolleranze_applicate_finiscono_nel_resoconto():
    """Una tolleranza che abbassa il tasso di allucinazione va dichiarata.

    Solo la lettura alternativa cambia `reading_index`; l'omissione tollerata e
    l'emissione tollerata agiscono dentro la lettura dell'annotazione e non
    lasciano traccia. Il resoconto dichiarava quindi le prime e taceva le altre.
    """
    caso = nl_benchmark.BenchmarkCase(
        case_id="X001",
        categoria="prova",
        difficolta="facile",
        ordine="ordine di prova",
        readings=(
            nl_benchmark.AcceptedReading(
                expected=(
                    nl_benchmark.ExpectedConstraint(kind="MAX_RESPONSE_TIME"),
                ),
                tolerated_extra_kinds=frozenset({"UNSUPPORTED"}),
            ),
        ),
    )
    prodotto = ConstraintSet(
        constraint_set_id="CS_X001",
        source_text="ordine di prova",
        constraints=[
            MaxResponseTime(constraint_id="M1", minutes=20),
            Unsupported(constraint_id="U1", reason="parte non risolvibile"),
        ],
        translator="finto",
    )
    esito = nl_benchmark.evaluate_case(caso, prodotto)
    assert esito.tolerated_extra == ("UNSUPPORTED",)
    assert esito.reading_index == 0, "la tolleranza non cambia la lettura scelta"
    assert esito.hallucinations == (), "la nota del corpus ammette quell'aggiunta"

    risultato = nl_benchmark.BenchmarkResult(
        label="finto", translator="finto", cases=[esito]
    )
    applicate = risultato.tolerances_applied()
    assert "X001" in applicate
    assert any("allucinazione" in r for r in applicate["X001"])
    assert "X001" in risultato.to_dict()["tolerances_used"]


def test_sul_corpus_vero_la_tolleranza_di_b044_e_dichiarata():
    """La stessa cosa sul corpus reale, perche' li' il difetto si era visto."""
    applicate = nl_benchmark.run_benchmark().tolerances_applied()
    assert "B044" in applicate, (
        "B044 tollera un UNSUPPORTED in piu': se la tolleranza vale, va detta"
    )


def test_un_ablazione_senza_scenari_si_dichiara_non_eseguita():
    """Zero scenari non e' un pareggio: e' una misura che non e' stata fatta."""
    risultati = ablation.run_all_ablations([], include_solver=False)
    assert risultati, "le ablazioni restituiscono comunque il loro esito"
    for r in risultati:
        assert r.comparison is None
        assert r.method.startswith("Non eseguita")
        assert any("non eseguita" in f.lower() for f in r.findings)


def test_il_piano_vuoto_ma_risolto_non_passa_sotto_silenzio(
    scenario_minimo: Scenario,
):
    """Tre notizie diverse, tre righe diverse.

    Osservato sulla rigiocata di default: tre finestre notturne in cui CP-SAT
    chiude OPTIMAL con zero assegnazioni. Lo stato e' fra quelli risolti, quindi
    nessuna riga lo segnalava, e chi leggeva vedeva soltanto un solver che
    raggiunge la meta' dei focolai della baseline.
    """
    _scripts_sul_path()
    import valuta_stagione

    def _vuoto_ma_ottimo(scenario: Scenario, constraints=None) -> Plan:
        return Plan(
            plan_id=f"OPT_VUOTO_{scenario.scenario_id}",
            horizon_minutes=scenario.horizon_minutes,
            producer="ottimo_vuoto",
            solver_status="OPTIMAL",
            unassigned_fire_ids=[f.fire_id for f in scenario.fires],
        )

    def _assegna(scenario: Scenario, constraints=None) -> Plan:
        return Plan(
            plan_id=f"PIENO_{scenario.scenario_id}",
            horizon_minutes=scenario.horizon_minutes,
            producer="pieno",
            solver_status="OPTIMAL",
            assignments=[
                Assignment(
                    "CAN_01", scenario.fires[0].fire_id, 0.0, 10.0, 1, 1_000, 60.0
                )
            ],
        )

    result = compare_planners(
        _scenari(scenario_minimo, 3),
        [("ottimo_vuoto", _vuoto_ma_ottimo), ("pieno", _assegna)],
        bootstrap_samples=50,
        permutations=50,
        validate=False,
    )
    note = valuta_stagione.solver_notes(result)
    vuote = [n for n in note if n.startswith("ottimo_vuoto")]
    assert len(vuote) == 1
    assert "VUOTO" in vuote[0]
    assert "budget" in vuote[0], "va detto che non e' una questione di tempo"
    assert not [n for n in note if n.startswith("pieno")]


def test_lo_spinner_degli_script_e_solo_ascii():
    """Lo spinner di default di rich uccideva gli script su console non utf-8.

    I caratteri braille dello spinner non stanno in cp1252: con l'uscita
    rediretta, rich passava al rendering legacy di Windows e sollevava
    UnicodeEncodeError all'uscita dal blocco `console.status`, cioe' a
    valutazione gia' conclusa. Questo test fissa l'invariante che conta: i
    fotogrammi dello spinner devono essere codificabili in qualunque codifica a
    byte singolo.
    """
    _scripts_sul_path()
    from rich.spinner import SPINNERS

    import valuta_stagione
    import valuta_traduzione

    for modulo in (valuta_stagione, valuta_traduzione):
        for frame in SPINNERS[modulo.SPINNER]["frames"]:
            frame.encode("cp1252")
            assert frame.isascii(), frame


def test_lo_script_della_stagione_rifiuta_argomenti_senza_senso():
    """--max-finestre 0 non vuol dire "tutte": valeva zero e ne rigiocava otto."""
    _scripts_sul_path()
    import valuta_stagione

    assert valuta_stagione.main(["--max-finestre", "0", "--senza-solver"]) == 2
    assert valuta_stagione.main(["--orizzonte", "0", "--senza-solver"]) == 2
    assert valuta_stagione.main(["--finestra", "-1", "--senza-solver"]) == 2


def test_il_periodo_senza_rilevamenti_si_ferma_senza_inventare_numeri():
    """Gennaio: le fixture coprono la stagione estiva, qui non c'e' niente."""
    _scripts_sul_path()
    import valuta_stagione

    codice = valuta_stagione.main(
        ["--da", "2025-01-01", "--a", "2025-01-02", "--senza-solver"]
    )
    assert codice == 2


def test_gli_insiemi_si_serializzano_in_ordine():
    """Due esecuzioni identiche devono produrre lo stesso file, byte per byte.

    `json.dumps(sort_keys=True)` ordina le chiavi dei dizionari ma non gli
    elementi delle liste: un insieme scritto nel suo ordine di iterazione
    farebbe comparire differenze che non esistono a chi confronta due
    valutazioni con diff.
    """
    payload = {"regole": {"turnaround", "endurance", "shift"}}
    primo = report.to_json(payload)
    assert primo == report.to_json(payload)
    riletto = json.loads(primo)
    assert riletto["regole"] == ["endurance", "shift", "turnaround"]
