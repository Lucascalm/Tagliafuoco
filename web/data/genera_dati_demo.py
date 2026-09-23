"""Precalcolo dei dati della demo cartografica.

Perche' questo script esiste: la pagina in `web/` deve poter funzionare come
sito statico, senza nessun processo Python acceso e senza chiavi. Rigiocare la
stagione davanti all'utente non e' possibile (il risolutore da solo impiega
decine di secondi per finestra), quindi il lavoro si fa una volta qui e il
risultato finisce in JSON accanto alla pagina.

La regola che governa questo file e' una sola: nessun dato inventato. Ogni
numero che la demo mostra esce dalla pipeline vera, nell'ordine vero:
rilevamenti dalle fixture, clustering, arricchimento territoriale, traduzione
dell'ordine, risolutore CP-SAT, baseline golosa, validatore indipendente. Se
una finestra produce un piano respinto, il JSON riporta il piano respinto: la
demo mostra anche i fallimenti, perche' il validatore esiste proprio per
quelli.

Due scelte di coerenza meritano una riga.

La prima: il clustering si esegue una volta sola sull'intera stagione, e le
finestre pianificate riusano quei focolai filtrandoli per visibilita'. Cosi' un
focolaio ha lo stesso identificativo nel livello della stagione e dentro lo
scenario, e la pagina puo' collegare una riga del piano al cerchio giusto sulla
mappa. Raggruppare di nuovo dentro la finestra produrrebbe identificativi
diversi per gli stessi pixel.

La seconda: il filtro di visibilita' guarda solo il passato. Uno scenario
datato alle 13:00 vede i focolai rilevati prima delle 13:00, mai quelli
rilevati dopo. E' la stessa regola che la rigiocata usa per non far filtrare
informazione dal futuro nella valutazione; qui vale anche per la demo, perche'
una mappa che mostra un focolaio prima del suo rilevamento e' una mappa che
mente.

Uso:
    .venv/Scripts/python.exe web/data/genera_dati_demo.py
    .venv/Scripts/python.exe web/data/genera_dati_demo.py --secondi 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tagliafuoco.baseline.nearest import plan_nearest  # noqa: E402
from tagliafuoco.config import (  # noqa: E402
    CALABRIA_BBOX,
    CLUSTER_TIME_WINDOW_HOURS,
    DEFAULT_HORIZON_MINUTES,
    DISCLAIMER,
)
from tagliafuoco.domain import registry  # noqa: E402
from tagliafuoco.domain.models import Assignment, FireCluster, Plan, Scenario  # noqa: E402
from tagliafuoco.ingest.clustering import cluster_detections  # noqa: E402
from tagliafuoco.ingest.enrichment import enrich_fires  # noqa: E402
from tagliafuoco.ingest.firms_client import fetch_detections  # noqa: E402
from tagliafuoco.ingest.scenario_builder import build_scenario, season_dates  # noqa: E402
from tagliafuoco.nlu import resolver  # noqa: E402
from tagliafuoco.nlu.schema import ConstraintKind, ConstraintSet  # noqa: E402
from tagliafuoco.nlu.translator import translate  # noqa: E402
from tagliafuoco.optimize.solver import solve  # noqa: E402
from tagliafuoco.validate.validator import validate_plan  # noqa: E402

logger = logging.getLogger("genera_dati_demo")


# --------------------------------------------------------------------------
# Tarature del precalcolo
# --------------------------------------------------------------------------

#: Anno della stagione rigiocata. Le fixture coprono il 2025.
SEASON_YEAR = 2025

#: Passo dell'istogramma della barra del tempo, in ore. Sei ore danno circa
#: cinquecento barre su una stagione di quattro mesi: abbastanza fitte da
#: mostrare i picchi, abbastanza larghe da restare disegnabili su uno schermo
#: stretto.
HISTOGRAM_STEP_HOURS = 6

#: Quanto a lungo un focolaio resta disegnato sulla mappa dopo l'ultimo
#: rilevamento. E' la stessa finestra che il clustering usa per decidere se due
#: pixel appartengono allo stesso incendio, e non e' una stima di durata reale:
#: il satellite non dice quando un incendio si spegne.
FIRE_VISIBILITY_HOURS = CLUSTER_TIME_WINDOW_HOURS

#: Secondi concessi al risolutore per ogni finestra precalcolata.
SOLVER_SECONDS = 20.0

#: Decimali tenuti sulle coordinate. Cinque decimali valgono circa un metro
#: alla latitudine della Calabria: piu' di quanto serva a una mappa regionale,
#: e molto meno peso della doppia precisione completa.
COORD_DECIMALS = 5


# --------------------------------------------------------------------------
# Gli ordini della demo
# --------------------------------------------------------------------------

#: Ogni voce e' una finestra della stagione con l'ordine che il coordinatore
#: avrebbe potuto dare in quel momento. Le date non sono scelte a caso: sono i
#: giorni con piu' rilevamenti, cioe' i momenti in cui la flotta non basta e il
#: piano deve scegliere. Gli ordini coprono tipi di vincolo diversi, perche' la
#: demo deve mostrare il repertorio, non lo stesso vincolo cinque volte.
ORDINI_DEMO: list[dict[str, Any]] = [
    {
        "etichetta": "Priorita' alle case",
        "giorno": "2025-08-19",
        "ora": 13,
        "testo": (
            "Priorita' alle zone vicino alle case, "
            "i Canadair non oltre quaranta minuti di risposta."
        ),
    },
    {
        "etichetta": "Un elicottero di riserva",
        "giorno": "2025-07-13",
        "ora": 12,
        "testo": (
            "Tieni un elicottero di riserva a Cosenza. "
            "Sugli incendi in Sila servono almeno due mezzi."
        ),
    },
    {
        "etichetta": "Canadair fermi per il vento",
        "giorno": "2025-08-05",
        "ora": 14,
        "testo": (
            "I Canadair restano a terra per il vento. "
            "Mandate i mezzi terrestri sui focolai in provincia di Cosenza."
        ),
    },
    {
        "etichetta": "Aspromonte e orario dei voli",
        "giorno": "2025-08-12",
        "ora": 11,
        "testo": (
            "Concentrate lo sforzo sull'Aspromonte. "
            "Gli aerei volano fino alle venti."
        ),
    },
    {
        "etichetta": "Conta arrivare presto",
        "giorno": "2025-09-02",
        "ora": 15,
        "testo": (
            "Conta piu' arrivare presto che risparmiare ore di volo. "
            "Proteggete l'ospedale di Lamezia Terme."
        ),
    },
    {
        "etichetta": "Ordine fuori repertorio",
        "giorno": "2025-06-24",
        "ora": 12,
        "testo": "Fate del vostro meglio e chiamate il sindaco di Rende.",
    },
]


# --------------------------------------------------------------------------
# Utilita' di serializzazione
# --------------------------------------------------------------------------


def r5(value: float) -> float:
    """Coordinata arrotondata, per tenere il JSON leggero."""
    return round(float(value), COORD_DECIMALS)


def r1(value: Optional[float]) -> Optional[float]:
    """Un decimale: basta per minuti, chilometri e percentuali."""
    return None if value is None else round(float(value), 1)


def as_utc(moment: datetime) -> datetime:
    """Istante in UTC. Le fixture arrivano gia' con il fuso, i test no."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def minutes_since(origin: datetime, moment: datetime) -> int:
    """Minuti interi dall'origine della stagione.

    Il tempo viaggia come intero e non come stringa ISO: su cinquecento
    focolai con due istanti ciascuno la differenza di peso e' reale, e il
    browser deve comunque riconvertire in millisecondi per animare.
    """
    return int(round((as_utc(moment) - origin).total_seconds() / 60.0))


# --------------------------------------------------------------------------
# Livello della stagione
# --------------------------------------------------------------------------


def fire_record(fire: FireCluster, origin: datetime) -> dict[str, Any]:
    """Un focolaio nella forma compatta che la mappa consuma.

    Le chiavi sono corte di proposito: cinquecento focolai per venti chiavi
    lunghe sono decine di migliaia di byte spesi per ripetere la parola
    latitudine. Il significato di ogni chiave sta nel README di questa
    cartella.
    """
    first = as_utc(fire.first_seen) if fire.first_seen else origin
    last = as_utc(fire.last_seen) if fire.last_seen else first
    return {
        "id": fire.fire_id,
        "lat": r5(fire.centroid.lat),
        "lon": r5(fire.centroid.lon),
        "t0": minutes_since(origin, first),
        "t1": minutes_since(origin, last),
        "frp": round(fire.max_frp_mw, 1),
        "frp_tot": round(fire.total_frp_mw, 1),
        "area": round(fire.area_km2, 3),
        "wui": 1 if fire.is_wui else 0,
        "d_wui": r1(fire.distance_to_wui_km),
        "comune": fire.comune or "",
        "prov": fire.provincia or "",
        "cop": fire.land_cover.value,
        "sev": fire.severity.value,
        "prio": round(fire.base_priority, 3),
        "litri": int(fire.water_demand_liters),
        "prot": fire.protected_area_name or None,
        "det": fire.detection_count,
    }


def histogram(
    detections: Sequence[Any], origin: datetime, step_hours: int
) -> dict[str, Any]:
    """Densita' dei rilevamenti nel tempo, gia' pronta per la barra del tempo.

    E' l'unico aggregato che la pagina non potrebbe ricavarsi da sola senza
    caricare tutti i rilevamenti: cinquemila righe scaricate per disegnare
    cinquecento barrette sarebbero banda spesa male.
    """
    step_min = step_hours * 60
    counts: Counter = Counter()
    for det in detections:
        counts[minutes_since(origin, det.acquired_at) // step_min] += 1
    if not counts:
        return {"passo_ore": step_hours, "conteggi": []}
    last_bucket = max(counts)
    return {
        "passo_ore": step_hours,
        "conteggi": [counts.get(i, 0) for i in range(last_bucket + 1)],
    }


def static_layers() -> dict[str, Any]:
    """Basi, flotta, sorgenti d'acqua e aree protette dal registro statico."""
    bases = registry.load_bases()
    fleet = registry.load_fleet()
    waters = registry.load_water_sources()
    areas = registry.load_protected_areas()

    return {
        "basi": [
            {
                "id": b.base_id,
                "nome": b.name,
                "lat": r5(b.point.lat),
                "lon": r5(b.point.lon),
                "prov": b.provincia,
                "notte": bool(b.has_night_capability),
            }
            for b in bases
        ],
        "mezzi": [
            {
                "id": v.vehicle_id,
                "nome": v.name,
                "tipo": v.kind.value,
                "dom": v.domain.value,
                "base": v.home_base_id,
                "litri": int(v.water_capacity_liters),
                "kmh": round(v.cruise_speed_kmh, 1),
                "notte": bool(v.night_capable),
            }
            for v in fleet
        ],
        "sorgenti": [
            {
                "id": w.source_id,
                "nome": w.name,
                "lat": r5(w.point.lat),
                "lon": r5(w.point.lon),
                "tipo": w.kind.value,
            }
            for w in waters
        ],
        "aree_protette": [
            {
                "nome": str(a.get("nome") or ""),
                "tipo": str(a.get("tipo") or ""),
                "lat": r5(float(a.get("lat") or 0.0)),
                "lon": r5(float(a.get("lon") or 0.0)),
                "raggio_km": round(float(a.get("raggio_km") or 0.0), 2),
            }
            for a in areas
        ],
    }


# --------------------------------------------------------------------------
# Scenari pianificati
# --------------------------------------------------------------------------


def visible_fires(
    fires: Sequence[FireCluster], instant: datetime, lookback_hours: float
) -> list[FireCluster]:
    """I focolai gia' rilevati a quell'istante e non ancora scaduti.

    Il confronto per l'ingresso e' su `first_seen`, non su `last_seen`: un
    focolaio entra nello scenario appena il satellite lo vede la prima volta.
    Usare `last_seen` per decidere l'ingresso significherebbe giustificare con
    un rilevamento futuro la presenza di un focolaio adesso.
    """
    horizon = timedelta(hours=lookback_hours)
    out: list[FireCluster] = []
    for fire in fires:
        first = as_utc(fire.first_seen) if fire.first_seen else None
        if first is None or first > instant:
            continue
        last = as_utc(fire.last_seen) if fire.last_seen else first
        if instant - last > horizon:
            continue
        out.append(fire)
    return sorted(out, key=lambda f: f.fire_id)


def assignment_record(
    assignment: Assignment, scenario: Scenario, origin_minute: int
) -> dict[str, Any]:
    """Una riga del piano, con quel tanto di geometria che serve ad animarla.

    La posizione del mezzo lungo la rotta non e' un dato del piano: il piano
    dice quando parte e quando arriva, non dove si trova a meta' strada. Qui si
    esportano gli estremi (base e focolaio) e i tre istanti, e l'interpolazione
    resta una ricostruzione fatta nel browser e dichiarata nella pagina.
    """
    vehicle = scenario.vehicle_by_id(assignment.vehicle_id)
    base = scenario.base_by_id(vehicle.home_base_id) if vehicle else None
    fire = scenario.fire_by_id(assignment.fire_id)
    source = (
        scenario.water_source_by_id(assignment.water_source_id)
        if assignment.water_source_id
        else None
    )
    return {
        "mezzo": assignment.vehicle_id,
        "focolaio": assignment.fire_id,
        "partenza": r1(assignment.departure_minute),
        "arrivo": r1(assignment.arrival_minute),
        "rilascio": r1(assignment.release_minute),
        "lanci": int(assignment.sorties),
        "litri": int(assignment.water_delivered_liters),
        "sorgente": assignment.water_source_id,
        "costo": r1(assignment.estimated_cost_eur),
        "nota": assignment.note,
        "da": [r5(base.point.lat), r5(base.point.lon)] if base else None,
        "a": [r5(fire.centroid.lat), r5(fire.centroid.lon)] if fire else None,
        "acqua": [r5(source.point.lat), r5(source.point.lon)] if source else None,
        "minuto_assoluto": origin_minute,
    }


def metrics_record(result: Any) -> dict[str, Any]:
    """Le metriche del piano, nelle chiavi che la pagina sa leggere.

    I numeri sono quelli RICALCOLATI dal validatore, non quelli dichiarati dal
    piano, e la scelta va dichiarata perche' cambia cosa significano. Il
    validatore esclude dal conto le assegnazioni che ha giudicato non
    eseguibili: i suoi numeri dicono quanto del piano regge davvero, non quanto
    il piano prometteva. Sono anche gli unici disponibili, perche' il
    risolutore lascia `Plan.metrics` a `None` (le metriche dichiarate le
    riempie `eval/metrics.py`, che qui non entra nella catena).

    La pagina riceve insieme il campo `fonte_metriche`, cosi' puo' scriverlo
    sotto la tabella invece di lasciare a chi legge il compito di indovinarlo.

    Esce anche `assegnazioni_valide`, che non e' un dettaglio contabile. Il
    validatore, quando non ha nessuna assegnazione eseguibile da misurare,
    lascia i tempi di risposta a zero: e' il valore neutro di una somma vuota,
    non un arrivo istantaneo. Senza quel conteggio la pagina non ha modo di
    distinguere i due casi, e finirebbe per scrivere "0,0 min" dove la verita'
    e' "nessun mezzo e' arrivato".
    """
    metrics = result.metrics
    return {
        "focolai": metrics.fires_total,
        "coperti": metrics.fires_covered,
        "focolai_wui": metrics.fires_wui_total,
        "coperti_wui": metrics.fires_wui_covered,
        "litri_richiesti": int(metrics.water_demanded_liters),
        "litri_erogati": int(metrics.water_deliverable_liters),
        "risposta_media": r1(metrics.mean_response_minutes),
        "risposta_p90": r1(metrics.p90_response_minutes),
        "minuti_mezzo": r1(metrics.total_vehicle_minutes),
        "costo": r1(metrics.total_cost_eur),
        "mezzi_usati": metrics.vehicles_used,
        "assegnazioni": metrics.assignments_total,
        "assegnazioni_valide": metrics.assignments_feasible,
    }


def plan_record(
    plan: Plan, scenario: Scenario, origin_minute: int, validation: Any = None
) -> dict[str, Any]:
    """Il piano in forma serializzabile, con le metriche del suo validatore.

    Il verdetto entra qui perche' le metriche che la pagina mostra sono le sue.
    Tenerle fuori avrebbe lasciato il riquadro vuoto: il risolutore non riempie
    `Plan.metrics`, e un riquadro vuoto non dice ne' che il dato manca ne'
    perche'.
    """
    ordered = sorted(plan.assignments, key=lambda a: (a.arrival_minute, a.vehicle_id))
    return {
        "id": plan.plan_id,
        "produttore": plan.producer,
        "stato": plan.solver_status,
        "secondi": round(plan.solve_seconds, 2),
        "obiettivo": r1(plan.objective_value),
        "assegnazioni": [assignment_record(a, scenario, origin_minute) for a in ordered],
        "non_assegnati": list(plan.unassigned_fire_ids),
        "note": list(plan.notes),
        "metriche": None if validation is None else metrics_record(validation),
        "fonte_metriche": "validatore" if validation is not None else "",
    }


def validation_record(result: Any) -> dict[str, Any]:
    """L'esito del validatore, separando cio' che blocca da cio' che avvisa.

    La distinzione e' l'informazione principale del riquadro: un piano con
    cinque avvisi resta accettato, un piano con una sola violazione bloccante
    no.
    """
    return {
        "valido": bool(result.is_valid),
        "riassunto": result.summary_line(),
        "versione": result.validator_version,
        "bloccanti": [v.to_dict() for v in result.blocking],
        "avvisi": [v.to_dict() for v in result.warnings],
        "metriche": result.metrics.to_dict(),
        "disaccordi": [c.to_dict() for c in result.disagreements],
    }


def constraint_record(cs: ConstraintSet) -> dict[str, Any]:
    """Il ConstraintSet come lo emette Pydantic, niente di piu'.

    Non si rimodella: la pagina deve mostrare esattamente cio' che ha
    attraversato il confine fra il linguaggio naturale e il solver, compresi i
    campi `source_span` e `confidence`, che servono al coordinatore per
    smentire una traduzione sbagliata prima di lanciare il risolutore.
    """
    return cs.model_dump(mode="json")


def vehicle_states(scenario: Scenario, cs: ConstraintSet, plan: Plan) -> dict[str, str]:
    """Stato di ogni mezzo dello scenario, ordine del coordinatore compreso.

    Lo stato di partenza e' quello che lo scenario ha gia' calcolato (fuori
    turno di notte, in manutenzione, disponibile). Sopra si innestano i due
    vincoli che tolgono un mezzo dal tavolo, riserva ed esclusione, risolti con
    lo stesso risolutore di selettori che usa il solver. La demo non prova a
    indovinare dal testo dell'ordine quali mezzi siano coinvolti: chiederebbe
    al browser di rifare, peggio, un lavoro gia' fatto in Python.

    Un punto va dichiarato perche' non e' ovvio. "Tieni un elicottero di
    riserva" e' un vincolo di conteggio: il modello impone che uno degli
    undici elicotteri resti libero, non dice quale. Disegnarli tutti e undici
    tratteggiati direbbe una cosa falsa. Qui si guarda il piano, che e' il
    dato: fra i mezzi selezionati e rimasti senza assegnazione se ne
    etichettano `count`, in ordine di matricola. Quali siano e' una scelta di
    rappresentazione; che siano fermi e' un fatto.
    """
    states = {v.vehicle_id: v.status.value for v in scenario.vehicles}
    impiegati = {a.vehicle_id for a in plan.assignments}

    for constraint in cs.constraints:
        if constraint.kind is ConstraintKind.EXCLUDE_VEHICLE:
            try:
                colpiti = resolver.resolve_vehicles(constraint.selector, scenario)
            except Exception:  # noqa: BLE001 - un selettore irrisolvibile non ferma la demo
                continue
            for vehicle in colpiti:
                states[vehicle.vehicle_id] = "excluded"

    for constraint in cs.constraints:
        if constraint.kind is not ConstraintKind.RESERVE_VEHICLE:
            continue
        try:
            colpiti = resolver.resolve_vehicles(constraint.selector, scenario)
        except Exception:  # noqa: BLE001
            continue
        fermi = sorted(
            v.vehicle_id
            for v in colpiti
            if v.vehicle_id not in impiegati and states.get(v.vehicle_id) == "available"
        )
        quanti = constraint.count if constraint.count is not None else len(fermi)
        for vehicle_id in fermi[: max(0, int(quanti))]:
            states[vehicle_id] = "reserve"

    return states


def build_one(
    ordine: dict[str, Any],
    season_fires: Sequence[FireCluster],
    origin: datetime,
    solver_seconds: float,
) -> Optional[dict[str, Any]]:
    """Una finestra completa: scenario, ordine, vincoli, due piani, due verdetti."""
    giorno = datetime.strptime(str(ordine["giorno"]), "%Y-%m-%d")
    instant = datetime(
        giorno.year, giorno.month, giorno.day, int(ordine["ora"]), 0, tzinfo=timezone.utc
    )
    fires = visible_fires(season_fires, instant, FIRE_VISIBILITY_HOURS)
    if not fires:
        logger.warning(
            "%s: nessun focolaio visibile alle %s, finestra saltata",
            ordine["etichetta"],
            instant,
        )
        return None

    scenario = build_scenario(
        fires,
        instant,
        DEFAULT_HORIZON_MINUTES,
        enrich=False,
        scenario_id="DEMO_" + instant.strftime("%Y%m%dT%H%M"),
    )
    constraint_set = translate(str(ordine["testo"]), scenario)

    started = time.perf_counter()
    plan = solve(scenario, constraint_set, max_seconds=solver_seconds, deterministic=True)
    baseline = plan_nearest(scenario, constraint_set)
    validation = validate_plan(plan, scenario, constraint_set)
    validation_baseline = validate_plan(baseline, scenario, constraint_set)
    logger.info(
        "%s: %d focolai, piano %s con %d assegnazioni, validatore %s (%.1fs)",
        ordine["etichetta"],
        len(fires),
        plan.solver_status,
        len(plan.assignments),
        "accettato" if validation.is_valid else "respinto",
        time.perf_counter() - started,
    )

    origin_minute = minutes_since(origin, instant)
    available = sum(1 for v in scenario.vehicles if v.status.value == "available")
    return {
        "id": scenario.scenario_id,
        "etichetta": ordine["etichetta"],
        "istante": instant.isoformat().replace("+00:00", "Z"),
        "minuto": origin_minute,
        "orizzonte_min": scenario.horizon_minutes,
        "notte": bool(scenario.is_night),
        "meteo": {
            "vento_kmh": scenario.wind_kmh,
            "temp_c": scenario.temperature_c,
            "umidita_pct": scenario.humidity_pct,
        },
        "focolai": [f.fire_id for f in fires],
        "mezzi_disponibili": available,
        "mezzi_totali": len(scenario.vehicles),
        "stati_mezzi": vehicle_states(scenario, constraint_set, plan),
        "ordine": str(ordine["testo"]),
        "vincoli": constraint_record(constraint_set),
        "piano": plan_record(plan, scenario, origin_minute, validation),
        "baseline": plan_record(baseline, scenario, origin_minute, validation_baseline),
        "validazione": validation_record(validation),
        "validazione_baseline": validation_record(validation_baseline),
    }


# --------------------------------------------------------------------------
# Scrittura
# --------------------------------------------------------------------------


def write_json(path: Path, payload: dict[str, Any]) -> int:
    """Scrive il JSON compatto e ne restituisce la dimensione in byte."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def write_js_shim(path: Path, global_name: str, payload: dict[str, Any]) -> int:
    """Scrive lo stesso contenuto come assegnazione a una variabile globale.

    Serve a un caso preciso: la pagina aperta con doppio clic, cioe' con schema
    `file:`. In quella condizione il browser rifiuta `fetch` verso un file
    locale, perche' l'origine e' opaca, quindi il JSON non si puo' leggere. Un
    normale tag script invece si carica, perche' e' la via che il browser
    permette da sempre.

    Il file e' generato dallo stesso dizionario del JSON, quindi i due non
    possono divergere: se divergono e' perche' qualcuno ne ha modificato uno a
    mano, e non va fatto.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    body = "window." + global_name + " = " + text + ";\n"
    path.write_text(body, encoding="utf-8")
    return len(body.encode("utf-8"))


def emit(path_json: Path, global_name: str, payload: dict[str, Any]) -> None:
    """Scrive la coppia JSON piu' shim e ne registra il peso."""
    size_json = write_json(path_json, payload)
    path_js = path_json.with_suffix(".js")
    size_js = write_js_shim(path_js, global_name, payload)
    logger.info(
        "scritto %s (%.0f kB) e %s (%.0f kB)",
        path_json.name,
        size_json / 1024,
        path_js.name,
        size_js / 1024,
    )


# --------------------------------------------------------------------------
# Programma principale
# --------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Precalcola i dati della demo.")
    parser.add_argument(
        "--secondi",
        type=float,
        default=SOLVER_SECONDS,
        help="secondi concessi al risolutore per finestra (prova rapida: 5)",
    )
    parser.add_argument("--anno", type=int, default=SEASON_YEAR, help="anno della stagione")
    parser.add_argument(
        "--uscita", type=Path, default=HERE, help="cartella di destinazione dei JSON"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args.uscita.mkdir(parents=True, exist_ok=True)

    first, last = season_dates(args.anno)
    logger.info("stagione %s, dal %s al %s", args.anno, first, last)

    detections = fetch_detections(first, last)
    if not detections:
        logger.error("nessun rilevamento: le fixture non ci sono o sono vuote")
        return 1
    logger.info("rilevamenti letti: %d", len(detections))

    fires = enrich_fires(cluster_detections(detections))
    logger.info("focolai dopo clustering e arricchimento: %d", len(fires))

    origin = min(as_utc(d.acquired_at) for d in detections).replace(
        minute=0, second=0, microsecond=0
    )
    fine = max(as_utc(d.acquired_at) for d in detections)

    lon_min, lat_min, lon_max, lat_max = CALABRIA_BBOX
    provenienza = {
        "rilevamenti": "NASA FIRMS, prodotti di archivio VIIRS e MODIS",
        "fixture": "fixture sintetiche in data/fixtures, generate senza chiave FIRMS",
        "statici": "data/static: basi, flotta, sorgenti d'acqua, comuni, aree protette",
        "pipeline": (
            "clustering DBSCAN, arricchimento territoriale, traduzione dell'ordine, "
            "CP-SAT, validatore indipendente"
        ),
    }

    stagione: dict[str, Any] = {
        "generato_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "avvertenza": DISCLAIMER,
        "anno": args.anno,
        "origine": origin.isoformat().replace("+00:00", "Z"),
        "durata_min": minutes_since(origin, fine),
        "bbox": [lat_min, lon_min, lat_max, lon_max],
        "visibilita_ore": FIRE_VISIBILITY_HOURS,
        "provenienza": provenienza,
        "rilevamenti_totali": len(detections),
        "focolai": [fire_record(f, origin) for f in fires],
        "istogramma": histogram(detections, origin, HISTOGRAM_STEP_HOURS),
    }
    stagione.update(static_layers())
    emit(args.uscita / "stagione.json", "TF_STAGIONE", stagione)

    scenari: list[dict[str, Any]] = []
    for ordine in ORDINI_DEMO:
        record = build_one(ordine, fires, origin, args.secondi)
        if record is not None:
            scenari.append(record)

    if not scenari:
        logger.error("nessuno scenario prodotto")
        return 1

    emit(
        args.uscita / "scenari.json",
        "TF_SCENARI",
        {
            "generato_il": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "avvertenza": DISCLAIMER,
            "origine": origin.isoformat().replace("+00:00", "Z"),
            "traduttore": str(scenari[0]["vincoli"].get("translator", "")),
            "scenari": scenari,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
