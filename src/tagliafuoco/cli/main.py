"""Riga di comando di Tagliafuoco: la catena intera, un pezzo per comando.

Serve a due cose diverse, e vale la pena separarle. La prima e' lavorare in
locale senza accendere niente: raggruppare i focolai di tre giorni, vedere come
viene tradotto un ordine, far girare il risolutore su un istante e leggere cosa
ne dice il validatore. La seconda e' produrre i dati precalcolati che la demo
consuma, cioe' la rigiocata della stagione, che dura minuti e non puo' stare
dietro a una richiesta HTTP.

Come la API, questo e' un adattatore: legge argomenti, chiama i casi d'uso,
stampa tabelle. Nessuna formula, nessuna regola di priorita', nessuna soglia
decisa qui. Se un numero mostrato a schermo non si trova in `optimize/`, in
`validate/` o in `ingest/`, e' stato calcolato nel posto sbagliato.

Quattro scelte di comportamento.

**L'assenza di una chiave non e' un errore.** All'avvio, se `FIRMS_MAP_KEY` o
`ANTHROPIC_API_KEY` mancano, il programma lo dice in una riga e spiega cosa usa
al loro posto: le fixture locali e il traduttore a regole. Poi prosegue. Il
progetto e' fatto per girare senza chiavi, e un avvio che si interrompe
chiedendole tradirebbe la premessa.

**L'avvertenza sta in testa, non in fondo.** Ogni comando comincia dichiarando
che Tagliafuoco e' un simulatore. Un piano di impiego stampato in una tabella
ordinata sembra un ordine di servizio; la riga che dice che non lo e' deve
essere la prima che si legge, non una nota a pie' di pagina.

**Un piano respinto non e' un comando fallito.** `pianifica` esce con codice
zero anche quando il validatore boccia il piano, perche' il piano bocciato e' un
risultato ed e' quello piu' interessante da guardare. Chi vuole il contrario in
uno script lo chiede con `--esci-con-errore`.

**Un numero non si accorcia mai, e un numero che non esiste si dichiara.** Le
due regole hanno la stessa radice. La prima governa le tabelle: a ottanta
colonne, che e' la larghezza normale di un terminale e di qualunque uscita
rediretta su file, rich accorciava tutte le colonne allo stesso modo e "234.5"
diventava un puntino. Qui i numeri prenotano la loro larghezza per intero e, se
non basta, sparisce una colonna intera dichiarandolo nella didascalia: un nome
accorciato resta un nome, un numero accorciato e' una bugia. La seconda governa
i rapporti: copertura WUI e acqua sul fabbisogno valgono 1.0 per convenzione
quando il denominatore e' zero, e al loro posto compare `n.d.`, perche' "100%
dei focolai in interfaccia protetti" quando non ce n'era nessuno e' un numero
inventato.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Annotated, Callable, Optional, Sequence

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tagliafuoco import config
from tagliafuoco.baseline.nearest import BASELINE_PLANNERS, available_baselines
from tagliafuoco.domain import registry
from tagliafuoco.domain.models import Detection, FireCluster, Plan, Scenario
from tagliafuoco.ingest.clustering import cluster_detections
from tagliafuoco.ingest.enrichment import enrich_fires
from tagliafuoco.ingest.firms_client import fetch_detections
from tagliafuoco.ingest.normalize import filter_by_time
from tagliafuoco.ingest.scenario_builder import build_scenario
from tagliafuoco.nlu import translator
from tagliafuoco.nlu.schema import ConstraintSet
from tagliafuoco.optimize.solver import solve
from tagliafuoco.sim.replay import ReplayError, ReplayResult, replay_season
from tagliafuoco.sim.timeline import TimelineError
from tagliafuoco.validate.validator import RecomputedMetrics, ValidationResult, validate_plan

console = Console()

#: Pianificatore predefinito quando un comando ne accetta uno per nome.
SOLVER_NAME = "cpsat"

#: Testo che sostituisce un numero che non esiste. Non e' un trattino
#: decorativo: distingue "zero misurato" da "nessuna misura", che nelle
#: metriche di questo progetto sono due cose diverse e si somigliano troppo.
NOT_AVAILABLE = "n.d."


def count(quantity: int, singular: str, plural: str) -> str:
    """Accorda il sostantivo al numero: un vincolo, due vincoli.

    I messaggi di questo programma li legge una persona, non un parser di log,
    e "1 vincoli" e' il genere di sciatteria che fa dubitare anche dei numeri
    scritti bene.
    """
    return f"{quantity} {singular if quantity == 1 else plural}"


def _can_encode(text: str) -> bool:
    """Se il terminale sa scrivere questi caratteri.

    La console di Windows lavora spesso in cp1252, dove i puntini di
    sospensione tipografici escono come punti interrogativi. Un nome accorciato
    che finisce con un punto interrogativo sembra un dato incerto invece che un
    dato tagliato, quindi il segno di taglio si sceglie in base a cio' che lo
    stream sa rappresentare.
    """
    try:
        text.encode(console.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


#: Livelli di log che uvicorn accetta. Sono elencati qui e non lasciati
#: passare cosi' come sono perche' un livello sbagliato fa morire il processo
#: dentro la configurazione del logging, con una traccia che non nomina
#: l'opzione sbagliata.
LOG_LEVELS: tuple[str, ...] = ("critical", "error", "warning", "info", "debug", "trace")

#: Indirizzi che tengono il servizio sulla sola macchina locale.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

#: Segno che marca un testo accorciato.
CUT_MARK = "\u2026" if _can_encode("\u2026") else ".."

app = typer.Typer(
    name="tagliafuoco",
    help=(
        "Simulatore di supporto alle decisioni per l'impiego dei mezzi "
        "antincendio boschivo in Calabria. Non e' uno strumento operativo."
    ),
    no_args_is_help=True,
    add_completion=False,
)


# --------------------------------------------------------------------------
# Intestazione e stato delle chiavi
# --------------------------------------------------------------------------


def keys_notice() -> str:
    """La riga che dice quali chiavi mancano e cosa si usa al loro posto."""
    firms = config.has_firms_key()
    llm = config.has_llm_key()
    if firms and llm:
        return "Chiavi presenti: rilevamenti da NASA FIRMS, ordini tradotti dal modello."
    parts: list[str] = []
    if not firms:
        parts.append("senza FIRMS_MAP_KEY i rilevamenti vengono dalle fixture locali")
    if not llm:
        parts.append(
            "senza ANTHROPIC_API_KEY gli ordini li traduce il traduttore a regole"
        )
    return "Configurazione senza chiavi: " + ", ".join(parts) + "."


@app.callback()
def intestazione(
    silenzioso: Annotated[
        bool,
        typer.Option(
            "--silenzioso",
            help="Non stampare l'intestazione con l'avvertenza e lo stato delle chiavi.",
        ),
    ] = False,
) -> None:
    """Stampa l'avvertenza e lo stato delle chiavi prima di ogni comando."""
    if silenzioso:
        return
    console.print(
        "[bold]Tagliafuoco[/bold] - SIMULATORE a scopo di studio: non e' uno "
        "strumento operativo e non va usato per dirigere interventi reali.",
        style="yellow",
    )
    console.print(keys_notice(), style="dim")
    console.print()


# --------------------------------------------------------------------------
# Lettura degli argomenti
# --------------------------------------------------------------------------


def parse_date(value: str, nome: str) -> date:
    """Data in formato ISO, con un messaggio in italiano quando non lo e'."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{nome}: '{value}' non e' una data valida, serve il formato "
            "AAAA-MM-GG (per esempio 2025-08-06).",
        ) from exc


def parse_instant(value: str) -> datetime:
    """Istante ISO. Una data nuda vale mezzogiorno UTC di quel giorno.

    Mezzogiorno e non mezzanotte: a mezzanotte i mezzi aerei non volano, e un
    utente che scrive solo la data quasi mai intende chiedere uno scenario
    notturno in cui il piano e' vuoto per costruzione.
    """
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        try:
            moment = datetime.combine(date.fromisoformat(value.strip()), dtime(12, 0))
        except ValueError as exc:
            raise typer.BadParameter(
                f"istante: '{value}' non e' leggibile, serve il formato "
                "AAAA-MM-GG oppure AAAA-MM-GGTHH:MM.",
            ) from exc
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def check_period(start: date, end: date) -> None:
    """Rifiuta un periodo invertito prima di leggere qualunque file."""
    if end < start:
        raise typer.BadParameter(
            f"il periodo e' invertito: --da {start.isoformat()} viene dopo "
            f"--a {end.isoformat()}."
        )


def resolve_planner(name: str, max_seconds: float) -> Callable[..., Plan]:
    """Il pianificatore chiesto per nome: il risolutore o una baseline.

    Il limite di tempo si lega qui con `partial`, cosi' il motore di rigiocata
    riceve un semplice `(scenario, constraints) -> Plan` e non deve sapere che
    uno dei due pianificatori ha un budget e l'altro no.
    """
    if name == SOLVER_NAME:
        return partial(solve, max_seconds=max_seconds, deterministic=True)
    if name in BASELINE_PLANNERS:
        return BASELINE_PLANNERS[name]
    raise typer.BadParameter(
        f"pianificatore '{name}' sconosciuto. Disponibili: "
        + ", ".join([SOLVER_NAME, *available_baselines()])
    )


# --------------------------------------------------------------------------
# Costruzione dei dati
# --------------------------------------------------------------------------


def load_detections(start: date, end: date) -> list[Detection]:
    """Rilevamenti del periodo, con una riga che dice da dove arrivano."""
    source = "NASA FIRMS" if config.has_firms_key() else "fixture locali"
    with console.status(f"Leggo i rilevamenti da {source}..."):
        detections = fetch_detections(start, end)
    return detections


def fires_at(instant: datetime, lookback_hours: float) -> list[FireCluster]:
    """Focolai gia' rilevati a quell'istante, raggruppati e arricchiti.

    Il filtro temporale si applica ai rilevamenti prima del clustering, non ai
    focolai dopo: raggruppare tutto e poi scartare vorrebbe dire costruire i
    centroidi con pixel che a quell'ora nessuno aveva ancora visto.
    """
    window_start = instant - timedelta(hours=lookback_hours)
    detections = load_detections(window_start.date(), instant.date())
    visible = filter_by_time(detections, window_start, instant)
    fires = enrich_fires(cluster_detections(visible))
    fires.sort(key=lambda f: (-f.base_priority, f.fire_id))
    return fires


def scenario_at(
    instant: datetime, horizon_minutes: int, lookback_hours: float, max_fires: int
) -> tuple[Scenario, list[str]]:
    """Lo scenario di un istante, piu' le note su cosa e' stato lasciato fuori."""
    notes: list[str] = []
    fires = fires_at(instant, lookback_hours)
    if len(fires) > max_fires:
        quanti = (
            "al focolaio piu' prioritario"
            if max_fires == 1
            else f"ai {max_fires} focolai piu' prioritari"
        )
        notes.append(
            f"Scenario troncato {quanti} su {len(fires)}: alza --max-focolai "
            "per includerli tutti."
        )
        fires = fires[:max_fires]
    try:
        scenario = build_scenario(
            fires, instant, horizon_minutes=horizon_minutes, enrich=False
        )
    except registry.StaticDataError as exc:
        console.print(
            "[red]Dati statici mancanti[/red]: esegui "
            "scripts/genera_dati_statici.py per rigenerarli.",
        )
        console.print(f"[dim]{exc}[/dim]")
        raise typer.Exit(code=2) from exc
    return scenario, notes


# --------------------------------------------------------------------------
# Numeri che esistono e numeri che non esistono
# --------------------------------------------------------------------------


def format_eur(value: float) -> str:
    """Euro interi, con le migliaia separate da uno spazio.

    Lo spazio e non il punto. Nella stessa tabella convivono numeri decimali
    scritti all'inglese ("36.5 ore-mezzo") e importi a sei cifre: con il punto
    delle migliaia all'italiana, "80.693" si legge come ottanta virgola
    seicentonovantatre. Uno spazio non si confonde con niente.
    """
    return f"{value:,.0f}".replace(",", " ")


def ratio_text(value: float, denominator: float) -> str:
    """Una percentuale, oppure `n.d.` quando il denominatore e' zero.

    Un rapporto senza denominatore non vale zero e non vale uno: non esiste.
    Il validatore e il motore di rigiocata restituiscono 1.0 per la copertura
    WUI quando non c'e' nessun focolaio in interfaccia e 1.0 per l'acqua quando
    il fabbisogno e' nullo. Sono convenzioni che servono a far girare i
    confronti senza dividere per zero, non misure da mostrare a una persona:
    "copertura WUI 100%" quando non c'era niente da proteggere e' un numero
    inventato, e questa funzione e' il punto in cui si smette di inventarlo.
    """
    if not denominator:
        return NOT_AVAILABLE
    return f"{value:.1%}"


def minutes_text(value: float, observations: int) -> str:
    """Minuti, oppure `n.d.` quando non c'e' stata nessuna osservazione.

    SPEC sezione 6 lo chiede per nome: con nessuna osservazione il percentile
    vale zero, e quello zero va distinto dal caso "risposta istantanea".
    """
    if not observations:
        return NOT_AVAILABLE
    return f"{value:.1f}"


def undefined_metrics_notes(
    fires_total: int,
    fires_wui_total: int,
    water_demanded_liters: float,
    responses_counted: int,
) -> list[str]:
    """Le avvertenze sui rapporti che in questo caso non hanno denominatore.

    Servono dove il numero passa comunque, cioe' nei riassunti scritti da altri
    moduli (il validatore, il motore di rigiocata) che la riga di comando
    stampa cosi' come sono. Li' il posto per dire "questo 100% non e' una
    misura" e' una riga accanto, non dentro il numero.
    """
    notes: list[str] = []
    if not fires_total:
        notes.append(
            "Nessun focolaio in questo scenario: tutte le metriche di copertura "
            "sono convenzioni, non misure."
        )
        return notes
    if not fires_wui_total:
        notes.append(
            "Nessun focolaio in interfaccia urbano-foresta: la copertura WUI "
            "vale 100% per convenzione, non perche' sia stato protetto qualcosa."
        )
    if not water_demanded_liters:
        notes.append(
            "Fabbisogno d'acqua nullo: il rapporto acqua su fabbisogno vale "
            "100% per convenzione, non perche' l'acqua sia bastata."
        )
    if not responses_counted:
        notes.append(
            "Nessuna assegnazione eseguibile: i tempi di risposta valgono zero "
            "perche' non c'e' niente da misurare, non perche' la risposta sia "
            "immediata."
        )
    return notes


# --------------------------------------------------------------------------
# Tabelle che stanno dentro il terminale
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """Una colonna, con quanto e' sacrificabile se il terminale e' stretto.

    Esiste per risolvere un problema concreto e non estetico. Quando le colonne
    non ci stanno, rich le accorcia tutte allo stesso modo: a ottanta colonne,
    che e' la larghezza predefinita di un terminale e di qualunque output
    rediretto su file, "234.5" diventava un puntino solo e il piano stampato
    non conteneva piu' nessun numero leggibile.

    La regola applicata qui e' che un nome accorciato resta un nome, mentre un
    numero accorciato e' una bugia. Percio' le larghezze dei numeri si
    prenotano per intero e, se non bastano, si toglie una colonna intera
    dichiarandolo nella didascalia. Una tabella che nasconde una colonna senza
    dirlo sarebbe il male peggiore di tutti.

    `droppable` a zero vuol dire che la colonna resta sempre; piu' il numero e'
    alto, prima la colonna viene tolta.
    """

    header: str
    justify: str = "left"
    droppable: int = 0
    #: Testo libero: va a capo invece di essere accorciato, quindi non prenota
    #: una larghezza esatta ma soltanto un minimo, e si prende lo spazio che
    #: avanza.
    flowing: bool = False
    #: Larghezza minima prenotata da una colonna di testo libero.
    flowing_width: int = 12
    #: Oltre questa larghezza il contenuto viene accorciato, con il segno che
    #: lo dichiara. Vale solo per i nomi, mai per i numeri.
    max_width: Optional[int] = None


def shorten(text: str, width: Optional[int]) -> str:
    """Accorcia un testo dichiarando il taglio, senza mai toccare un numero."""
    if width is None or len(text) <= width:
        return text
    if width <= len(CUT_MARK):
        return text[:width]
    return text[: width - len(CUT_MARK)] + CUT_MARK


def _reserved_widths(
    columns: Sequence[Column], rows: Sequence[Sequence[str]]
) -> list[int]:
    """Quanto spazio prenota ogni colonna: il suo contenuto piu' lungo."""
    widths: list[int] = []
    for index, column in enumerate(columns):
        needed = max(
            [len(column.header)] + [len(row[index]) for row in rows], default=0
        )
        widths.append(min(needed, column.flowing_width) if column.flowing else needed)
    return widths


def _shortened_rows(
    columns: Sequence[Column], rows: Sequence[Sequence[str]]
) -> list[list[str]]:
    """Applica il tetto di larghezza dichiarato da ciascuna colonna.

    Si fa qui, prima di misurare, perche' il tetto e' parte della definizione
    della colonna: una colonna dei comuni larga sedici lo e' anche quando in
    quella pagina capita solo "Cutro". Solo le colonne che dichiarano
    `max_width` vengono toccate, e sono per costruzione quelle di nomi.
    """
    return [
        [shorten(cell, column.max_width) for cell, column in zip(row, columns)]
        for row in rows
    ]


def _framed_width(widths: Sequence[int]) -> int:
    """Larghezza della cornice di rich: un bordo, piu' tre caratteri a colonna."""
    return 1 + sum(w + 3 for w in widths)


def build_table(
    title: str,
    columns: Sequence[Column],
    rows: Sequence[Sequence[str]],
    *,
    caption: str = "",
    width: Optional[int] = None,
) -> Table:
    """Costruisce la tabella piu' larga che entra nel terminale.

    Toglie le colonne sacrificabili una alla volta, dalla piu' sacrificabile in
    giu', finche' la cornice non entra nella larghezza disponibile. Le colonne
    tolte vengono elencate nella didascalia insieme alla larghezza che le ha
    escluse, cosi' chi legge sa che esistono e sa cosa fare per vederle.
    """
    available = width if width is not None else console.width
    cells = _shortened_rows(columns, rows)
    kept = list(range(len(columns)))
    dropped: list[str] = []
    while True:
        widths = _reserved_widths(
            [columns[i] for i in kept], [[r[i] for i in kept] for r in cells]
        )
        if _framed_width(widths) <= available:
            break
        candidates = [i for i in kept if columns[i].droppable > 0]
        if not candidates:
            break
        victim = max(candidates, key=lambda i: (columns[i].droppable, i))
        kept.remove(victim)
        dropped.append(columns[victim].header)

    notes = [caption] if caption else []
    if dropped:
        notes.append(
            f"Colonne omesse per stare in {available} colonne di terminale: "
            + ", ".join(dropped)
            + ". Allarga la finestra per vederle."
        )
    table = Table(title=title, caption=" ".join(notes) or None)
    for position, index in enumerate(kept):
        column = columns[index]
        if column.flowing:
            table.add_column(column.header, justify=column.justify, overflow="fold")
        else:
            table.add_column(
                column.header,
                justify=column.justify,
                no_wrap=True,
                width=widths[position],
            )
    for row in cells:
        table.add_row(*[row[i] for i in kept])
    return table


def table_detections(detections: list[Detection], limit: int) -> Table:
    """I rilevamenti piu' potenti del periodo. La FRP e' la colonna che conta."""
    columns = (
        Column("id"),
        Column("acquisito"),
        Column("lat", justify="right"),
        Column("lon", justify="right"),
        Column("FRP MW", justify="right"),
        Column("conf", justify="right", droppable=1),
        Column("sensore", droppable=2),
    )
    rows = [
        [
            det.detection_id,
            det.acquired_at.strftime("%Y-%m-%d %H:%M"),
            f"{det.point.lat:.4f}",
            f"{det.point.lon:.4f}",
            f"{det.frp_mw:.1f}",
            f"{det.confidence:.0f}",
            f"{det.instrument}/{det.satellite}",
        ]
        for det in sorted(detections, key=lambda d: -d.frp_mw)[:limit]
    ]
    return build_table(
        f"Rilevamenti piu' intensi (primi {min(limit, len(detections))} su "
        f"{len(detections)})",
        columns,
        rows,
        caption="Gli orari sono UTC, come li pubblica FIRMS.",
    )


def table_fires(fires: list[FireCluster], limit: int) -> Table:
    """I focolai in ordine di priorita'.

    Il fabbisogno e' in metri cubi perche' in litri sono sei cifre che nessuno
    legge. I nomi di comune troppo lunghi per la colonna vengono accorciati con
    il segno che lo dichiara; i numeri no, mai: quando non ci stanno tutti, la
    colonna meno importante sparisce e la didascalia dice quale.
    """
    columns = (
        Column("focolaio"),
        Column("comune", max_width=16),
        Column("pix", justify="right", droppable=1),
        Column("FRP MW", justify="right"),
        Column("km2", justify="right"),
        Column("copertura", droppable=4),
        Column("WUI", droppable=2),
        Column("acqua m3", justify="right"),
        Column("gravita'", droppable=3),
        Column("prio", justify="right"),
    )
    rows = [
        [
            fire.fire_id,
            fire.comune or "-",
            str(fire.detection_count),
            f"{fire.max_frp_mw:.1f}",
            f"{fire.area_km2:.2f}",
            fire.land_cover.value,
            "si" if fire.is_wui else "no",
            f"{fire.water_demand_liters / 1000.0:.0f}",
            fire.severity.value,
            f"{fire.base_priority:.2f}",
        ]
        for fire in fires[:limit]
    ]
    return build_table(
        f"Focolai (primi {min(limit, len(fires))} su {len(fires)})",
        columns,
        rows,
    )


def table_constraints(constraint_set: ConstraintSet) -> Table:
    """I vincoli estratti, con il frammento dell'ordine da cui vengono.

    La colonna `frammento` e' il punto del comando: mostra `source_span`, cioe'
    la parte letterale dell'ordine che ha prodotto il vincolo. Serve a
    smentirlo prima che entri nel solver, che e' l'unico controllo umano
    previsto su cio' che il traduttore ha capito. Per questo e' l'ultima cosa
    che si sacrifica: su un terminale stretto sparisce prima la motivazione,
    che e' un commento, non una prova.
    """
    columns = (
        Column("id"),
        Column("tipo"),
        Column("forza"),
        Column("conf", justify="right"),
        Column("frammento dell'ordine", flowing=True),
        Column("motivazione", flowing=True, droppable=1),
    )
    rows = [
        [
            c.constraint_id,
            c.kind.value,
            "duro" if c.enforcement.value == "hard" else "morbido",
            f"{c.confidence:.2f}",
            c.source_span or "-",
            c.rationale or "-",
        ]
        for c in constraint_set.constraints
    ]
    return build_table("Vincoli estratti dall'ordine", columns, rows)


def table_plan(plan: Plan, scenario: Scenario) -> Table:
    """Le assegnazioni del piano, in ordine di partenza."""
    names = {v.vehicle_id: v.name for v in scenario.vehicles}
    comuni = {f.fire_id: f.comune for f in scenario.fires}
    columns = (
        Column("mezzo", max_width=18),
        Column("focolaio"),
        Column("comune", max_width=14, droppable=3),
        Column("part.", justify="right"),
        Column("arrivo", justify="right"),
        Column("rilascio", justify="right", droppable=1),
        Column("lanci", justify="right"),
        Column("acqua m3", justify="right"),
        Column("costo EUR", justify="right", droppable=2),
    )
    rows = [
        [
            names.get(a.vehicle_id, a.vehicle_id),
            a.fire_id,
            comuni.get(a.fire_id) or "-",
            f"{a.departure_minute:.0f}",
            f"{a.arrival_minute:.0f}",
            f"{a.release_minute:.0f}",
            str(a.sorties),
            f"{a.water_delivered_liters / 1000.0:.1f}",
            format_eur(a.estimated_cost_eur),
        ]
        for a in sorted(
            plan.assignments, key=lambda x: (x.departure_minute, x.vehicle_id)
        )
    ]
    return build_table(
        f"Piano {plan.plan_id} ({plan.producer}, {plan.solver_status})",
        columns,
        rows,
        caption=(
            "I minuti sono contati dall'inizio dell'orizzonte, non sono orari; "
            "il rilascio e' il rientro in base."
        ),
    )


def print_validation(result: ValidationResult) -> None:
    """L'esito della validazione, con le violazioni bloccanti per esteso."""
    verdict = "ACCETTATO" if result.is_valid else "RESPINTO"
    style = "green" if result.is_valid else "red"
    console.print(
        Panel(
            result.summary_line(),
            title=f"Validatore indipendente: piano {verdict}",
            border_style=style,
        )
    )
    print_notes(metrics_notes(result.metrics))
    if not result.violations:
        return
    columns = (
        Column("regola"),
        Column("gravita'"),
        Column("messaggio", flowing=True),
    )
    rows = [
        [v.rule, "BLOCCANTE" if v.is_blocking else "avviso", v.message]
        for v in result.violations
    ]
    console.print(build_table("Contestazioni del validatore", columns, rows))


def metrics_notes(metrics: RecomputedMetrics) -> list[str]:
    """Le avvertenze sui rapporti senza denominatore di queste metriche."""
    return undefined_metrics_notes(
        fires_total=metrics.fires_total,
        fires_wui_total=metrics.fires_wui_total,
        water_demanded_liters=metrics.water_demanded_liters,
        responses_counted=metrics.fires_covered,
    )


def table_scores(
    rows: list[tuple[str, Plan, ValidationResult]],
) -> Table:
    """Confronto fra piani sulle metriche ricalcolate dal validatore.

    Tutte le righe vengono dalla stessa fonte, il validatore. Confrontare un
    piano sulle metriche che dichiara contro un altro misurato da fuori sarebbe
    un confronto truccato a favore del primo.

    Dove il rapporto non ha denominatore compare `n.d.` invece della
    convenzione numerica: vedi `ratio_text`.
    """
    columns = [Column("metrica")] + [
        Column(label, justify="right") for label, _, _ in rows
    ]
    body: list[list[str]] = []

    def add(name: str, fmt: Callable[[ValidationResult], str]) -> None:
        body.append([name] + [fmt(result) for _, _, result in rows])

    add("copertura", lambda r: ratio_text(r.metrics.coverage_ratio, r.metrics.fires_total))
    add(
        "copertura WUI",
        lambda r: ratio_text(r.metrics.wui_coverage_ratio, r.metrics.fires_wui_total),
    )
    add(
        "risposta media (min)",
        lambda r: minutes_text(r.metrics.mean_response_minutes, r.metrics.fires_covered),
    )
    add(
        "risposta p90 (min)",
        lambda r: minutes_text(r.metrics.p90_response_minutes, r.metrics.fires_covered),
    )
    add(
        "acqua sul fabbisogno",
        lambda r: ratio_text(
            r.metrics.water_fulfilment_ratio, r.metrics.water_demanded_liters
        ),
    )
    add("ore-mezzo", lambda r: f"{r.metrics.total_vehicle_minutes / 60.0:.1f}")
    add("costo EUR", lambda r: format_eur(r.metrics.total_cost_eur))
    add("mezzi impiegati", lambda r: str(r.metrics.vehicles_used))
    add("assegnazioni valide", lambda r: str(r.metrics.assignments_feasible))
    add("piano valido", lambda r: "si" if r.is_valid else "no")
    return build_table(
        "Confronto (metriche ricalcolate dal validatore)",
        columns,
        body,
        caption=(
            f"{NOT_AVAILABLE} = il rapporto non ha denominatore in questo "
            "scenario, quindi non e' una misura."
        ),
    )


def print_notes(notes: list[str]) -> None:
    """Le note del comando: cosa ha deciso il programma per conto suo."""
    for note in notes:
        console.print(f"[dim]nota: {note}[/dim]")


# --------------------------------------------------------------------------
# Comandi
# --------------------------------------------------------------------------


@app.command()
def scarica(
    da: Annotated[str, typer.Option(help="Primo giorno incluso, AAAA-MM-GG")] = "2025-08-05",
    a: Annotated[str, typer.Option(help="Ultimo giorno incluso, AAAA-MM-GG")] = "2025-08-06",
    limite: Annotated[
        int, typer.Option(min=1, help="Quanti rilevamenti mostrare")
    ] = 10,
    esporta: Annotated[
        Optional[Path],
        typer.Option(help="Percorso di un file JSON in cui salvare i rilevamenti"),
    ] = None,
) -> None:
    """Scarica i rilevamenti satellitari del periodo.

    Con `FIRMS_MAP_KEY` interroga NASA FIRMS; senza chiave legge le fixture
    sintetiche in data/fixtures, che hanno lo stesso formato CSV e passano per
    lo stesso parser. E' voluto: il percorso con chiave e quello senza
    esercitano lo stesso codice.
    """
    start, end = parse_date(da, "--da"), parse_date(a, "--a")
    check_period(start, end)
    detections = load_detections(start, end)

    source = "NASA FIRMS" if config.has_firms_key() else "fixture sintetiche locali"
    console.print(
        count(len(detections), "rilevamento", "rilevamenti")
        + f" fra {start.isoformat()} e {end.isoformat()}, sorgente: {source}."
    )
    if not detections:
        console.print(
            "[yellow]Nessun rilevamento nel periodo.[/yellow] Senza chiave le "
            "fixture coprono la stagione 2025 (giugno-settembre)."
        )
        return

    console.print(table_detections(detections, limite))

    if esporta is not None:
        payload = [
            {
                "detection_id": d.detection_id,
                "lat": d.point.lat,
                "lon": d.point.lon,
                "acquired_at": d.acquired_at.isoformat(),
                "satellite": d.satellite,
                "instrument": d.instrument,
                "confidence": d.confidence,
                "frp_mw": d.frp_mw,
                "daynight": d.daynight,
            }
            for d in detections
        ]
        esporta.parent.mkdir(parents=True, exist_ok=True)
        esporta.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        console.print(f"Salvati in {esporta}.")


@app.command()
def focolai(
    da: Annotated[str, typer.Option(help="Primo giorno incluso, AAAA-MM-GG")] = "2025-08-05",
    a: Annotated[str, typer.Option(help="Ultimo giorno incluso, AAAA-MM-GG")] = "2025-08-06",
    limite: Annotated[int, typer.Option(min=1, help="Quanti focolai mostrare")] = 20,
) -> None:
    """Raggruppa i rilevamenti in focolai e li arricchisce con il territorio.

    Il clustering e' DBSCAN su distanza haversine, dentro finestre temporali:
    due pixel nello stesso punto ma a dodici ore di distanza non sono lo stesso
    incendio. L'arricchimento aggiunge comune, copertura del suolo, distanza dal
    costruito, area protetta, fabbisogno d'acqua e priorita'.
    """
    start, end = parse_date(da, "--da"), parse_date(a, "--a")
    check_period(start, end)
    detections = load_detections(start, end)
    fires = enrich_fires(cluster_detections(detections))
    fires.sort(key=lambda f: (-f.base_priority, f.fire_id))

    console.print(
        count(len(detections), "rilevamento raggruppato", "rilevamenti raggruppati")
        + " in "
        + count(len(fires), "focolaio", "focolai")
        + f" fra {start.isoformat()} e {end.isoformat()}."
    )
    if not fires:
        console.print("[yellow]Nessun focolaio nel periodo.[/yellow]")
        return
    console.print(table_fires(fires, limite))
    wui = sum(1 for f in fires if f.is_wui)
    protetti = sum(1 for f in fires if f.in_protected_area)
    verbo = "e'" if wui == 1 else "sono"
    console.print(
        f"Di questi, {wui} {verbo} in interfaccia urbano-foresta (entro "
        f"{config.WUI_THRESHOLD_KM:.0f} km dal costruito) e {protetti} in area "
        "protetta."
    )


@app.command()
def traduci(
    ordine: Annotated[str, typer.Argument(help="L'ordine del coordinatore, in italiano")],
    istante: Annotated[
        Optional[str],
        typer.Option(
            help=(
                "Istante di riferimento: con questo l'ordine viene tradotto "
                "avendo sotto gli occhi lo scenario di quel momento"
            )
        ),
    ] = None,
    orizzonte: Annotated[
        int, typer.Option(min=30, max=1440, help="Orizzonte del piano in minuti")
    ] = 240,
    max_focolai: Annotated[
        int, typer.Option(min=1, help="Tetto ai focolai dello scenario")
    ] = 40,
) -> None:
    """Traduce un ordine in vincoli tipizzati e mostra da dove viene ciascuno.

    Il modello linguistico non decide: produce soltanto istanze del repertorio
    chiuso definito in nlu/schema.py. Senza chiave traduce il traduttore
    deterministico a regole, e la riga di intestazione dice quale dei due ha
    lavorato.
    """
    scenario: Optional[Scenario] = None
    if istante is not None:
        scenario, notes = scenario_at(
            parse_instant(istante), orizzonte, 12.0, max_focolai
        )
        print_notes(notes)

    constraint_set = translator.translate(ordine, scenario)

    console.print(
        f"Traduttore usato: [bold]{constraint_set.translator}[/bold]"
        + (f" (modello {constraint_set.model_name})" if constraint_set.model_name else "")
        + f", {constraint_set.translation_ms:.0f} ms."
    )
    console.print(table_constraints(constraint_set))

    unsupported = constraint_set.unsupported
    if unsupported:
        quante = count(
            len(unsupported), "parte dell'ordine non e'", "parti dell'ordine non sono"
        )
        console.print(
            f"[yellow]{quante} rappresentabile[/yellow] e non verra' applicata. "
            "L'astensione e' la risposta corretta quando l'ordine esce dal "
            "repertorio: un vincolo inventato sarebbe peggio."
        )
    for warning in constraint_set.warnings:
        console.print(f"[dim]avviso: {warning}[/dim]")


@app.command()
def pianifica(
    ordine: Annotated[str, typer.Option(help="L'ordine del coordinatore")] = "",
    istante: Annotated[
        str, typer.Option(help="Istante da pianificare, AAAA-MM-GG o AAAA-MM-GGTHH:MM")
    ] = "2025-08-06T12:00",
    orizzonte: Annotated[
        int, typer.Option(min=30, max=1440, help="Orizzonte del piano in minuti")
    ] = 240,
    max_secondi: Annotated[
        float, typer.Option(min=0.1, help="Limite di tempo del risolutore")
    ] = 10.0,
    max_focolai: Annotated[
        int, typer.Option(min=1, help="Tetto ai focolai dello scenario")
    ] = 20,
    deterministico: Annotated[
        bool,
        typer.Option(help="Un solo thread e seed fissato: piu' lento, riproducibile"),
    ] = True,
    esci_con_errore: Annotated[
        bool,
        typer.Option(help="Esci con codice 1 se il validatore respinge il piano"),
    ] = False,
) -> None:
    """Costruisce lo scenario, traduce l'ordine, risolve, valida e stampa.

    E' la catena intera in un comando. Il piano viene stampato anche quando il
    validatore lo respinge: un piano respinto con la diagnostica accanto e' il
    caso piu' istruttivo, perche' e' la prova che il validatore non e' decorativo.
    """
    moment = parse_instant(istante)
    scenario, notes = scenario_at(moment, orizzonte, 12.0, max_focolai)
    print_notes(notes)

    constraint_set = translator.translate(ordine, scenario)
    console.print(
        f"Scenario [bold]{scenario.scenario_id}[/bold] del "
        f"{scenario.timestamp.isoformat()}: {len(scenario.fires)} focolai, "
        f"{len(scenario.vehicles)} mezzi, "
        + ("notte" if scenario.is_night else "giorno")
        + "."
    )
    if ordine:
        quanti = len(constraint_set.constraints)
        console.print(
            f"Ordine tradotto da [bold]{constraint_set.translator}[/bold] in "
            + (f"{quanti} vincolo." if quanti == 1 else f"{quanti} vincoli.")
        )
        console.print(table_constraints(constraint_set))
    else:
        console.print(
            "[dim]Nessun ordine: si ottimizza con i soli pesi di default, che e' "
            "il caso di riferimento dell'ablazione.[/dim]"
        )

    with console.status(f"Risolvo (fino a {max_secondi:.0f} s)..."):
        plan = solve(
            scenario,
            constraint_set,
            max_seconds=max_secondi,
            deterministic=deterministico,
        )
    console.print(
        f"Risolutore: stato {plan.solver_status}, {plan.solve_seconds:.2f} s, "
        + count(len(plan.assignments), "assegnazione", "assegnazioni")
        + ", "
        + count(
            len(plan.unassigned_fire_ids),
            "focolaio senza mezzi",
            "focolai senza mezzi",
        )
        + "."
    )
    for note in plan.notes:
        console.print(f"[dim]risolutore: {note}[/dim]")

    if plan.assignments:
        console.print(table_plan(plan, scenario))

    result = validate_plan(plan, scenario, constraint_set)
    print_validation(result)

    if esci_con_errore and not result.is_valid:
        raise typer.Exit(code=1)


@app.command()
def confronta(
    istante: Annotated[
        Optional[str], typer.Option(help="Istante singolo da confrontare")
    ] = None,
    da: Annotated[Optional[str], typer.Option(help="Primo giorno di un periodo")] = None,
    a: Annotated[Optional[str], typer.Option(help="Ultimo giorno di un periodo")] = None,
    baseline: Annotated[
        str, typer.Option(help="Quale regola banale usare come termine di paragone")
    ] = "baseline_nearest",
    orizzonte: Annotated[
        int, typer.Option(min=30, max=1440, help="Orizzonte del piano in minuti")
    ] = 240,
    max_secondi: Annotated[
        float, typer.Option(min=0.1, help="Limite di tempo del risolutore")
    ] = 10.0,
    max_focolai: Annotated[
        int, typer.Option(min=1, help="Tetto ai focolai dello scenario")
    ] = 20,
    finestra: Annotated[
        float, typer.Option(min=0.1, help="Ampiezza della finestra, in ore")
    ] = 3.0,
    max_finestre: Annotated[
        Optional[int],
        typer.Option(min=1, help="Quante finestre al massimo, sul periodo"),
    ] = None,
    ordine: Annotated[str, typer.Option(help="Ordine da applicare a entrambi")] = "",
) -> None:
    """Mette il risolutore contro la regola banale, sugli stessi dati.

    Su un istante confronta due piani. Su un periodo rigioca le finestre due
    volte, una per pianificatore, partendo dallo stesso stato del mondo: e' il
    primo dei tre esperimenti della sezione 7 di SPEC.md.

    Il confronto vale solo se i due vedono esattamente lo stesso scenario e
    ricevono lo stesso ordine. Per questo `--ordine` si applica a entrambi: dare
    i vincoli al solver e non alla baseline sarebbe un confronto truccato.
    """
    if baseline not in BASELINE_PLANNERS:
        raise typer.BadParameter(
            f"baseline '{baseline}' sconosciuta. Disponibili: "
            + ", ".join(available_baselines())
        )

    if da is not None or a is not None:
        if da is None or a is None:
            raise typer.BadParameter("sul periodo servono sia --da sia --a.")
        _confronta_periodo(
            parse_date(da, "--da"),
            parse_date(a, "--a"),
            baseline=baseline,
            orizzonte=orizzonte,
            max_secondi=max_secondi,
            finestra=finestra,
            max_finestre=max_finestre,
            ordine=ordine,
        )
        return

    moment = parse_instant(istante or "2025-08-06T12:00")
    scenario, notes = scenario_at(moment, orizzonte, 12.0, max_focolai)
    print_notes(notes)
    constraint_set = translator.translate(ordine, scenario)

    with console.status("Risolvo con CP-SAT..."):
        solver_plan = solve(
            scenario, constraint_set, max_seconds=max_secondi, deterministic=True
        )
    baseline_plan = BASELINE_PLANNERS[baseline](scenario, constraint_set)

    rows = [
        (SOLVER_NAME, solver_plan, validate_plan(solver_plan, scenario, constraint_set)),
        (baseline, baseline_plan, validate_plan(baseline_plan, scenario, constraint_set)),
    ]
    console.print(
        f"Scenario [bold]{scenario.scenario_id}[/bold]: {len(scenario.fires)} "
        f"focolai, {len(scenario.vehicles)} mezzi."
    )
    console.print(table_scores(rows))
    console.print(
        "[dim]Nessuna colonna dice quale piano e' migliore: dipende da cosa si "
        "sta ottimizzando, e sceglierlo e' un giudizio, non un calcolo.[/dim]"
    )


def _confronta_periodo(
    start: date,
    end: date,
    *,
    baseline: str,
    orizzonte: int,
    max_secondi: float,
    finestra: float,
    max_finestre: Optional[int],
    ordine: str,
) -> None:
    """Rigioca il periodo due volte e mette i totali uno accanto all'altro."""
    check_period(start, end)
    detections = load_detections(start, end)
    if not detections:
        console.print("[yellow]Nessun rilevamento nel periodo.[/yellow]")
        return

    constraint_set = translator.translate(ordine) if ordine else None
    first = datetime.combine(start, dtime(0, 0), tzinfo=timezone.utc)
    last = datetime.combine(end, dtime(0, 0), tzinfo=timezone.utc) + timedelta(days=1)

    results: list[ReplayResult] = []
    for label in (SOLVER_NAME, baseline):
        planner = resolve_planner(label, max_secondi)
        with console.status(f"Rigioco il periodo con {label}..."):
            results.append(
                _run_replay(
                    first,
                    last,
                    planner,
                    detections=detections,
                    constraints=constraint_set,
                    finestra=finestra,
                    orizzonte=orizzonte,
                    max_finestre=max_finestre,
                    label=label,
                    save=False,
                    output=None,
                )
            )

    aggregates = [r.aggregate() for r in results]
    columns = [Column("metrica")] + [
        Column(result.label, justify="right") for result in results
    ]
    # Ogni riga dichiara su quale denominatore poggia: dove quel denominatore e'
    # zero il rapporto non vale zero ne' uno, non esiste, e al suo posto va
    # detto che non c'e'. Vedi `ratio_text`.
    body = [
        ["finestre"] + [f"{agg['windows']:.0f}" for agg in aggregates],
        ["copertura"]
        + [ratio_text(agg["coverage_ratio"], agg["fires_total"]) for agg in aggregates],
        ["copertura WUI"]
        + [
            ratio_text(agg["wui_coverage_ratio"], agg["fires_wui_total"])
            for agg in aggregates
        ],
        ["risposta media (min)"]
        + [
            minutes_text(agg["mean_response_minutes"], agg["responses_counted"])
            for agg in aggregates
        ],
        ["risposta p90 (min)"]
        + [
            minutes_text(agg["p90_response_minutes"], agg["responses_counted"])
            for agg in aggregates
        ],
        ["acqua sul fabbisogno"]
        + [
            ratio_text(agg["water_fulfilment_ratio"], agg["water_demanded_liters"])
            for agg in aggregates
        ],
        ["ore-mezzo"] + [f"{agg['total_vehicle_hours']:.1f}" for agg in aggregates],
        ["piani validi"]
        + [
            f"{agg['plans_valid']:.0f}/{agg['plans_validated']:.0f}"
            for agg in aggregates
        ],
    ]
    console.print(
        build_table(
            f"Rigiocata {start.isoformat()} - {end.isoformat()}",
            columns,
            body,
            caption=(
                "I focolai si contano per coppia focolaio-finestra: un incendio "
                "attivo in tre finestre pesa tre volte, per entrambi i "
                f"pianificatori. {NOT_AVAILABLE} = rapporto senza denominatore."
            ),
        )
    )


def _run_replay(
    first: datetime,
    last: datetime,
    planner: Callable[..., Plan],
    *,
    detections: list[Detection],
    constraints: Optional[ConstraintSet],
    finestra: float,
    orizzonte: int,
    max_finestre: Optional[int],
    label: str,
    save: bool,
    output: Optional[Path],
) -> ReplayResult:
    """Una rigiocata, con gli errori del motore tradotti in un'uscita pulita.

    Si catturano due eccezioni e non una perche' il motore ne ha due: il
    `ReplayError` di `sim/replay.py` quando mancano le risorse, e il
    `TimelineError` di `sim/timeline.py` quando i parametri temporali non
    costruiscono nessuna finestra. Sono errori d'uso, non guasti, e meritano
    una riga di spiegazione invece di una traccia di stack di trenta righe.
    """
    try:
        return replay_season(
            first,
            last,
            planner,
            detections=detections,
            constraints=constraints,
            window_hours=finestra,
            horizon_minutes=orizzonte,
            max_windows=max_finestre,
            label=label,
            save=save,
            output_dir=output,
        )
    except (ReplayError, TimelineError) as exc:
        console.print(f"[red]Rigiocata non eseguibile[/red]: {exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def rigioca(
    da: Annotated[str, typer.Option(help="Primo giorno della rigiocata")] = "2025-08-05",
    a: Annotated[str, typer.Option(help="Ultimo giorno della rigiocata")] = "2025-08-06",
    pianificatore: Annotated[
        str, typer.Option(help="cpsat oppure una delle baseline")
    ] = SOLVER_NAME,
    finestra: Annotated[
        float, typer.Option(min=0.1, help="Ogni quante ore si ripianifica")
    ] = 3.0,
    orizzonte: Annotated[
        int, typer.Option(min=30, max=1440, help="Quanto avanti guarda il piano")
    ] = 240,
    max_secondi: Annotated[
        float, typer.Option(min=0.1, help="Limite del risolutore per finestra")
    ] = 10.0,
    max_finestre: Annotated[
        Optional[int], typer.Option(min=1, help="Ferma la rigiocata dopo N finestre")
    ] = None,
    ordine: Annotated[str, typer.Option(help="Ordine valido per tutta la stagione")] = "",
    output: Annotated[
        Optional[Path], typer.Option(help="Cartella in cui salvare il registro JSON")
    ] = None,
    salva: Annotated[bool, typer.Option(help="Scrivi il registro su disco")] = True,
) -> None:
    """Rigioca il periodo a finestre e salva il registro per il timelapse.

    L'ampiezza della finestra e l'orizzonte del piano sono grandezze diverse e
    restano separate: si ripianifica ogni tre ore ma si pianifica su quattro,
    perche' una missione che parte adesso finisce dopo la fine della finestra.

    Il registro JSON che esce di qui e' quello che `/api/rigiocata` serve alla
    demo: la stagione si calcola una volta, non a ogni richiesta.
    """
    start, end = parse_date(da, "--da"), parse_date(a, "--a")
    check_period(start, end)
    planner = resolve_planner(pianificatore, max_secondi)

    detections = load_detections(start, end)
    if not detections:
        console.print(
            "[yellow]Nessun rilevamento nel periodo: non c'e' niente da "
            "rigiocare.[/yellow]"
        )
        return

    constraint_set = translator.translate(ordine) if ordine else None
    first = datetime.combine(start, dtime(0, 0), tzinfo=timezone.utc)
    last = datetime.combine(end, dtime(0, 0), tzinfo=timezone.utc) + timedelta(days=1)

    with console.status(f"Rigioco con {pianificatore}..."):
        result = _run_replay(
            first,
            last,
            planner,
            detections=detections,
            constraints=constraint_set,
            finestra=finestra,
            orizzonte=orizzonte,
            max_finestre=max_finestre,
            label=pianificatore,
            save=salva,
            output=output,
        )

    console.print(
        Panel(
            "\n".join(result.summary_lines()),
            title=f"Rigiocata {start.isoformat()} - {end.isoformat()}",
            border_style="cyan",
        )
    )
    # Il riassunto lo scrive `sim/replay.py` e stampa le sue convenzioni come
    # numeri ("copertura WUI 1.000" anche quando non c'era nessun focolaio in
    # interfaccia). Qui non si riscrive quel testo, gli si mette accanto cosa
    # significa quando il denominatore manca.
    aggregate = result.aggregate()
    print_notes(
        undefined_metrics_notes(
            fires_total=int(aggregate["fires_total"]),
            fires_wui_total=int(aggregate["fires_wui_total"]),
            water_demanded_liters=float(aggregate["water_demanded_liters"]),
            responses_counted=int(aggregate["responses_counted"]),
        )
    )
    if result.output_path is not None:
        console.print(f"Registro salvato in {result.output_path}.")
        console.print(
            "[dim]E' il file che /api/rigiocata serve alla mappa per il "
            "timelapse.[/dim]"
        )


@app.command()
def servizio(
    host: Annotated[str, typer.Option(help="Interfaccia su cui ascoltare")] = "127.0.0.1",
    porta: Annotated[
        int, typer.Option(min=1, max=65535, help="Porta del servizio")
    ] = 8000,
    ricarica: Annotated[
        bool, typer.Option(help="Riavvia a ogni modifica dei sorgenti")
    ] = False,
    livello_log: Annotated[
        str,
        typer.Option(
            help="Livello di log di uvicorn: " + ", ".join(LOG_LEVELS)
        ),
    ] = "info",
) -> None:
    """Avvia il servizio HTTP della demo.

    L'indirizzo di ascolto e' 127.0.0.1 di proposito: il servizio ha CORS
    aperto verso le origini locali e nessuna autenticazione, perche' e' pensato
    per una demo sulla propria macchina. Esporlo su 0.0.0.0 significa metterlo
    in rete cosi' com'e', e non e' cio' per cui e' scritto: da qui non si puo'
    impedire, ma si puo' almeno dirlo nel momento in cui succede, invece di
    lasciarlo scritto in un docstring che chi digita il comando non legge.

    Gli errori di avvio piu' comuni sono la porta gia' occupata e il permesso
    negato sull'interfaccia. Uvicorn li scrive nel proprio log e chiude il
    processo senza far salire l'eccezione, quindi qui si intercetta l'uscita
    per aggiungere la riga che manca: cosa fare, non solo cosa e' successo.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - uvicorn e' fra le dipendenze
        console.print(
            "[red]uvicorn non e' installato[/red]: installa le dipendenze del "
            "progetto con 'pip install -e .'."
        )
        raise typer.Exit(code=2) from exc

    livello = livello_log.strip().lower()
    if livello not in LOG_LEVELS:
        raise typer.BadParameter(
            f"--livello-log: '{livello_log}' non e' un livello di uvicorn. "
            "Disponibili: " + ", ".join(LOG_LEVELS) + "."
        )

    if host not in LOOPBACK_HOSTS:
        console.print(
            f"[yellow]Attenzione: ascolto su {host}, non su 127.0.0.1.[/yellow] "
            "Questo servizio non ha autenticazione, non ha limiti di frequenza "
            "e accetta le origini locali via CORS: esposto in rete e' "
            "raggiungibile da chiunque arrivi a questa macchina.",
        )

    console.print(
        f"Servizio su http://{host}:{porta} - documentazione su "
        f"http://{host}:{porta}/api/docs"
    )
    console.print(
        "[dim]Le risposte contengono sempre il campo 'simulation': la API "
        "descrive una simulazione, non operazioni reali.[/dim]"
    )
    try:
        uvicorn.run(
            "tagliafuoco.api.app:app",
            host=host,
            port=porta,
            reload=ricarica,
            log_level=livello,
        )
    except OSError as exc:
        console.print(
            f"[red]Il servizio non e' partito[/red]: {exc}. La causa piu' "
            f"frequente e' la porta {porta} gia' occupata da un altro "
            "processo: prova con --porta su un numero libero."
        )
        raise typer.Exit(code=2) from exc
    except SystemExit as exc:
        # uvicorn non lascia salire l'errore di bind: lo scrive nel proprio log
        # e chiude il processo da se'. Il suo messaggio dice cosa e' successo
        # ma non cosa fare, e chi ha digitato il comando vuole sapere la
        # seconda cosa. Intercettare l'uscita e' l'unico punto in cui si puo'
        # aggiungere quella riga.
        code = exc.code if isinstance(exc.code, int) else 1
        if not code:
            raise
        console.print(
            "[red]Il servizio si e' chiuso con un errore.[/red] Se non era "
            f"ancora partito, la causa piu' frequente e' la porta {porta} "
            "gia' occupata da un altro processo: prova con --porta su un "
            "numero libero. Il dettaglio e' nella riga scritta da uvicorn qui "
            "sopra."
        )
        raise typer.Exit(code=2) from exc


def main() -> None:
    """Punto d'ingresso per chi importa il modulo invece di eseguirlo.

    Il comando installato da `pyproject.toml` punta direttamente all'oggetto
    Typer `app`, che e' gia' invocabile; questa funzione resta per `python -m
    tagliafuoco.cli.main` e per chi preferisce un nome a una variabile.
    """
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
