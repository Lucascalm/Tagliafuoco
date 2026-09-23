"""Rigiocata della stagione e confronto appaiato: l'esperimento 1 di SPEC.md.

PERCHE' QUESTO SCRIPT ESISTE
----------------------------
Il sottosistema di valutazione e' una libreria: sa misurare un piano, appaiare
due pianificatori, stimare l'incertezza. Non sa da solo quale stagione
rigiocare, con che finestra, con quale ordine del coordinatore. Questo script
e' il punto in cui quelle scelte vengono fatte una volta, scritte in chiaro e
salvate insieme ai numeri, cosi' che la stessa valutazione si possa rifare fra
sei mesi ottenendo le stesse cifre.

E' anche il punto in cui la valutazione diventa eseguibile da chi non legge il
codice: un comando solo, nessuna chiave API, nessuna rete. Senza `FIRMS_MAP_KEY`
i rilevamenti vengono dalle fixture sintetiche locali, e il resoconto lo dice
prima dei numeri invece di lasciarlo scoprire.

COSA FA, IN ORDINE
------------------
1. Legge i rilevamenti del periodo (FIRMS con la chiave, fixture senza).
2. Taglia il periodo in finestre e costruisce uno `Scenario` per finestra.
3. Esegue OGNI pianificatore su OGNI scenario, valida i piani e misura.
4. Calcola le differenze appaiate scenario per scenario, con intervallo
   bootstrap e test di permutazione.
5. Stampa il resoconto in italiano e salva il risultato grezzo in
   `data/processed/`.

PERCHE' APPAIATO, E PERCHE' DETERMINISTICO
------------------------------------------
Il confronto non fa la media del solver, la media della baseline e poi la
sottrazione: calcola la differenza su ogni singola finestra e solo dopo
aggrega. La varianza fra finestre (due focolai vicini a una base contro
quaranta sparsi su tre province) e' molto piu' grande della differenza fra
metodi, e confrontando le medie quella differenza finisce sepolta sotto il
rumore di quale finestra e' capitata nel campione. Il ragionamento per esteso
sta nel docstring di `eval/comparison.py`.

Il solver gira sempre in modalita' deterministica (un lavoratore, seme fissato)
come chiede SPEC.md sezione 7: due bracci che differissero anche per il numero
di lavoratori misurerebbero la concorrenza insieme al metodo.

QUESTA E' UNA SIMULAZIONE
-------------------------
I numeri dicono cosa il sistema avrebbe PROPOSTO, non cosa sarebbe SUCCESSO:
la propagazione del fuoco e l'effetto dell'intervento non sono modellati. Il
resoconto apre con questa avvertenza, non la mette in fondo.

USO
---
    .venv/Scripts/python.exe scripts/valuta_stagione.py
    .venv/Scripts/python.exe scripts/valuta_stagione.py --da 2025-08-05 --a 2025-08-08
    .venv/Scripts/python.exe scripts/valuta_stagione.py --senza-solver --max-finestre 20
    .venv/Scripts/python.exe scripts/valuta_stagione.py --ordine "prima le zone vicino alle case"

Il periodo di default e' corto apposta: una rigiocata completa della stagione
costa minuti di solver, e uno script che nessuno aspetta non viene eseguito.
Chi vuole la stagione intera allarga il periodo e alza `--max-secondi`.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.text import Text  # noqa: E402

from tagliafuoco import config  # noqa: E402
from tagliafuoco.domain.models import Detection, Plan, Scenario  # noqa: E402
from tagliafuoco.eval.comparison import (  # noqa: E402
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_PERMUTATIONS,
    DEFAULT_SEED,
    ComparisonResult,
    compare_planners,
    methodological_warnings,
)
from tagliafuoco.eval.metrics import COVERAGE_WATER_RATIO, describe_conventions  # noqa: E402
from tagliafuoco.eval.report import (  # noqa: E402
    LIMITI,
    render_ablations,
    render_comparison,
    save_json,
    summary_lines,
)
from tagliafuoco.ingest.firms_client import fetch_detections  # noqa: E402
from tagliafuoco.ingest.scenario_builder import build_scenarios_over_season  # noqa: E402
from tagliafuoco.nlu.schema import ConstraintSet  # noqa: E402

console = Console()
#: Spinner dell'attesa. Rich ne userebbe uno a caratteri braille, che su Windows
#: non sta nella pagina di codice della console: quando l'uscita viene rediretta
#: rich sceglie il rendering legacy, prova a scrivere quei caratteri in cp1252 e
#: solleva UnicodeEncodeError all'USCITA dal blocco, cioe' a valutazione gia'
#: conclusa. Sono minuti di solver buttati per un'animazione. Questo ne usa
#: quattro segni ASCII e non puo' fallire in nessuna codifica.
SPINNER = "line"


#: Nome del pianificatore in esame. Sta per primo nella lista dei pianificatori
#: perche' la convenzione del confronto e' "metodo nuovo contro riferimento", e
#: il riferimento si scrive per ultimo.
SOLVER_NAME = "cpsat"

#: Riferimento di default del confronto: la regola banale del mezzo libero piu'
#: vicino servito in ordine di rilevamento. E' la baseline onesta, cioe' cio'
#: che accadrebbe davvero senza il sistema (docs/10, paragrafo 2).
REFERENCE_NAME = "baseline_tempo"

#: Stati CP-SAT che corrispondono a una soluzione trovata. Gli altri sono
#: finestre in cui il solver non ha concluso entro il budget: non sono un
#: errore, ma vanno contate, perche' un piano vuoto entra nel confronto.
SOLVED_STATUSES = frozenset({"OPTIMAL", "FEASIBLE"})


# --------------------------------------------------------------------------
# Argomenti
# --------------------------------------------------------------------------


def parse_day(value: str) -> date:
    """Una data in formato ISO, con un messaggio comprensibile se non lo e'."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - percorso di sola interfaccia
        raise argparse.ArgumentTypeError(
            f"data non valida: '{value}'. Serve il formato AAAA-MM-GG."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Gli argomenti della riga di comando, in italiano perche' li legge una persona."""
    parser = argparse.ArgumentParser(
        prog="valuta_stagione",
        description=(
            "Rigioca un periodo della stagione e confronta Tagliafuoco con le "
            "baseline sugli stessi scenari. Funziona senza chiavi API."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "I risultati grezzi finiscono in data/processed/. La valutazione "
            "dice cosa il sistema avrebbe proposto, non cosa sarebbe successo."
        ),
    )
    parser.add_argument(
        "--da",
        type=parse_day,
        default=date(2025, 8, 5),
        help="primo giorno del periodo (default: 2025-08-05)",
    )
    parser.add_argument(
        "--a",
        type=parse_day,
        default=date(2025, 8, 6),
        help="ultimo giorno del periodo, compreso (default: 2025-08-06)",
    )
    parser.add_argument(
        "--finestra",
        type=float,
        default=float(config.REPLAY_WINDOW_HOURS),
        help="ogni quante ore si ripianifica (default: %(default)s)",
    )
    parser.add_argument(
        "--orizzonte",
        type=int,
        default=config.DEFAULT_HORIZON_MINUTES,
        help="quanto avanti guarda ogni piano, in minuti (default: %(default)s)",
    )
    parser.add_argument(
        "--max-finestre",
        type=int,
        default=None,
        help=(
            "ferma la rigiocata dopo N finestre. E' il modo piu' rapido di "
            "restringere il costo senza cambiare il periodo"
        ),
    )
    parser.add_argument(
        "--max-secondi",
        type=float,
        default=10.0,
        help=(
            "budget del solver per finestra (default: %(default)s). Un budget "
            "basso non falsa il confronto, lo rende solo piu' severo con il "
            "solver: e' comunque parte del metodo e finisce nel salvataggio"
        ),
    )
    parser.add_argument(
        "--senza-solver",
        action="store_true",
        help=(
            "confronta solo le due baseline fra loro. Serve a provare la "
            "catena in pochi secondi, senza caricare CP-SAT"
        ),
    )
    parser.add_argument(
        "--senza-validatore",
        action="store_true",
        help=(
            "salta la validazione dei piani. Rende il confronto piu' veloce e "
            "meno informativo: senza validatore non si sa quanti piani siano "
            "eseguibili"
        ),
    )
    parser.add_argument(
        "--ordine",
        default="",
        help=(
            "ordine del coordinatore, valido per tutte le finestre. Viene "
            "tradotto una volta e passato a TUTTI i pianificatori: darlo solo "
            "al solver misurerebbe l'ordine invece del metodo"
        ),
    )
    parser.add_argument(
        "--ablazioni",
        action="store_true",
        help=(
            "esegue anche le ablazioni di SPEC.md sezione 7. Costa una seconda "
            "passata di solver su ogni scenario"
        ),
    )
    parser.add_argument(
        "--seme",
        type=int,
        default=DEFAULT_SEED,
        help="seme del bootstrap e delle permutazioni (default: %(default)s)",
    )
    parser.add_argument(
        "--campioni-bootstrap",
        type=int,
        default=DEFAULT_BOOTSTRAP_SAMPLES,
        help="ricampionamenti del bootstrap (default: %(default)s)",
    )
    parser.add_argument(
        "--permutazioni",
        type=int,
        default=DEFAULT_PERMUTATIONS,
        help="permutazioni del test sul segno (default: %(default)s)",
    )
    parser.add_argument(
        "--soglia-copertura",
        type=float,
        default=COVERAGE_WATER_RATIO,
        help=(
            "frazione del fabbisogno d'acqua oltre la quale un focolaio conta "
            "come coperto (default: %(default)s). E' una taratura dichiarata, "
            "non una verita' idraulica: cambiarla cambia tutte le coperture"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="cartella in cui salvare il JSON (default: data/processed/)",
    )
    parser.add_argument(
        "--nome",
        default="valutazione_stagione",
        help="prefisso del file salvato (default: %(default)s)",
    )
    parser.add_argument(
        "--senza-salvataggio",
        action="store_true",
        help="stampa il resoconto senza scrivere niente su disco",
    )
    return parser


# --------------------------------------------------------------------------
# Preparazione degli scenari
# --------------------------------------------------------------------------


def data_source_line() -> str:
    """Da dove vengono i rilevamenti, detto prima dei numeri e non dopo."""
    if config.has_firms_key():
        return (
            "Rilevamenti da NASA FIRMS (FIRMS_MAP_KEY presente): dati osservati, "
            "con la latenza e i buchi di copertura del satellite."
        )
    return (
        "Rilevamenti dalle fixture sintetiche locali (FIRMS_MAP_KEY assente): "
        "dati generati, non osservati. I numeri descrivono un mondo verosimile "
        "e inventato, ed e' esattamente cio' che serve per provare il metodo."
    )


def load_scenarios(
    start: date,
    end: date,
    *,
    window_hours: float,
    horizon_minutes: int,
    max_windows: Optional[int],
) -> tuple[list[Scenario], list[Detection], int]:
    """Rilevamenti del periodo, scenari per finestra e quante finestre c'erano.

    Il terzo valore serve al resoconto: se `--max-finestre` ha tagliato, chi
    legge deve sapere su quale frazione del periodo sono calcolati i numeri.
    Un troncamento silenzioso trasformerebbe "due giorni" in "le prime tre ore
    di due giorni" senza che nessuno se ne accorga.
    """
    detections = fetch_detections(start, end)
    if not detections:
        return [], [], 0
    scenarios = build_scenarios_over_season(
        detections, window_hours, horizon_minutes
    )
    available = len(scenarios)
    if max_windows is not None and max_windows > 0:
        scenarios = scenarios[:max_windows]
    return scenarios, detections, available


def translate_order(order_text: str) -> Optional[ConstraintSet]:
    """Traduce l'ordine una volta sola, oppure restituisce None se non c'e'.

    Si usa `nlu.translator.translate`, che senza `ANTHROPIC_API_KEY` ricade da
    solo sul traduttore a regole. Lo stesso `ConstraintSet` andra' a tutti i
    pianificatori: e' il confronto a doverlo condividere, altrimenti misura
    l'ordine invece del metodo.
    """
    if not order_text.strip():
        return None
    from tagliafuoco.nlu import translator

    try:
        return translator.translate(order_text)
    except Exception as exc:  # noqa: BLE001 - il guasto si dichiara, non si alza
        # Se la traduzione fallisce la rigiocata prosegue senza ordine, e il
        # fatto viene detto: un confronto senza vincoli resta un confronto
        # valido, un confronto in cui l'ordine e' sparito in silenzio no.
        console.print(
            Panel(
                Text(
                    f"Ordine: {order_text}\n"
                    f"La traduzione e' fallita ({type(exc).__name__}: {exc}).\n"
                    "La rigiocata prosegue SENZA ordine: i pianificatori "
                    "ricevono tutti lo stesso insieme vuoto di vincoli, quindi "
                    "il confronto resta appaiato, ma non misura l'effetto "
                    "dell'ordine."
                ),
                title="Ordine non applicato",
                border_style="red",
            )
        )
        return None


def build_planners(
    *, max_seconds: float, with_solver: bool
) -> list[tuple[str, Callable[..., Plan]]]:
    """I pianificatori da confrontare, riferimento per ultimo.

    Il solver si importa qui dentro e non in testa: con `--senza-solver` non si
    paga il caricamento di ortools, che sono alcuni secondi prima ancora di
    cominciare a misurare.
    """
    from tagliafuoco.baseline.nearest import plan_nearest, plan_priority

    planners: list[tuple[str, Callable[..., Plan]]] = []
    if with_solver:
        from tagliafuoco.optimize.solver import solve

        def cpsat(
            scenario: Scenario, constraints: Optional[ConstraintSet] = None
        ) -> Plan:
            # deterministic=True non e' negoziabile in valutazione: SPEC.md
            # sezione 7 chiede seme fissato e un solo lavoratore, altrimenti
            # due esecuzioni della stessa rigiocata danno numeri diversi.
            return solve(
                scenario, constraints, max_seconds=max_seconds, deterministic=True
            )

        planners.append((SOLVER_NAME, cpsat))
    planners.append(("baseline_priorita", plan_priority))
    planners.append((REFERENCE_NAME, plan_nearest))
    return planners


def _window_list(scenario_ids: Sequence[str], limit: int = 4) -> str:
    """Elenco abbreviato di finestre, per non allagare il resoconto."""
    shown = ", ".join(scenario_ids[:limit])
    return shown + (", ..." if len(scenario_ids) > limit else "")


def solver_notes(result: ComparisonResult) -> list[str]:
    """Quante finestre non hanno prodotto un piano, e per quale dei due motivi.

    Non e' un dettaglio di esecuzione: una finestra chiusa in stato UNKNOWN
    produce un piano vuoto, e un piano vuoto entra nel confronto come una
    copertura zero. Chi legge un risultato deve poter distinguere "il metodo ha
    fatto peggio" da "il metodo non ha avuto tempo".

    I tre motivi restano separati apposta. Un budget troppo stretto e' una
    scelta di chi valuta e si corregge con `--max-secondi`; un pianificatore che
    solleva un'eccezione e' un difetto del pianificatore; un piano vuoto chiuso
    in stato OPTIMAL e' il caso opposto, cioe' il pianificatore che ha
    DIMOSTRATO che in quella finestra non c'era nessuna missione praticabile.
    Nasconderli dentro la stessa riga li farebbe passare tutti per lentezza.

    Il terzo caso e' quello che si e' visto davvero sulla rigiocata di default:
    tre finestre notturne su otto, con tutti i mezzi aerei a terra, in cui CP-SAT
    chiude OPTIMAL con zero assegnazioni mentre le baseline assegnano lo stesso.
    Lo stato e' fra quelli risolti, quindi nessuna riga lo segnalava, e chi
    leggeva vedeva soltanto un solver che raggiunge la meta' dei focolai della
    baseline. Quel divario non e' un divario di qualita': e' una finestra in cui
    i due rispondono a domande diverse, e va scritto accanto alla tabella.
    """
    notes: list[str] = []
    for name, outcomes in result.outcomes.items():
        crashed = [o.scenario_id for o in outcomes if o.error]
        unsolved = [
            o.scenario_id
            for o in outcomes
            if not o.error
            and o.solver_status
            and o.solver_status not in SOLVED_STATUSES
        ]
        empty = [
            o.scenario_id
            for o in outcomes
            if not o.error
            and o.detail.assignments_total == 0
            and o.detail.metrics.fires_total > 0
            and (not o.solver_status or o.solver_status in SOLVED_STATUSES)
        ]
        if crashed:
            notes.append(
                f"{name}: {len(crashed)} finestre su {len(outcomes)} in cui il "
                f"pianificatore ha sollevato un'eccezione ({_window_list(crashed)}). "
                "Non sono piani peggiori, sono piani mancanti: il confronto su "
                "quelle finestre non misura niente. Il messaggio di errore e' "
                "nelle note di esecuzione."
            )
        if unsolved:
            notes.append(
                f"{name}: {len(unsolved)} finestre su {len(outcomes)} chiuse "
                f"senza soluzione entro il budget ({_window_list(unsolved)}). "
                "I loro piani sono vuoti e pesano nel confronto come copertura "
                "nulla: alza --max-secondi prima di concludere che il metodo "
                "sia peggiore."
            )
        if empty:
            fires = sum(
                o.detail.metrics.fires_total
                for o in outcomes
                if o.scenario_id in set(empty)
            )
            notes.append(
                f"{name}: {len(empty)} finestre su {len(outcomes)} chiuse con un "
                f"piano VUOTO pur avendo {fires} focolai da servire "
                f"({_window_list(empty)}). Lo stato del pianificatore dice che "
                "la soluzione e' stata trovata, quindi non e' una questione di "
                "budget: su quelle finestre nessuna missione era praticabile "
                "secondo il suo modello (tipicamente la notte, con i mezzi aerei "
                "a terra). Entrano nel confronto come copertura nulla e "
                "abbassano tutte le medie di questo pianificatore: prima di "
                "leggerle come una sconfitta, guarda le note del piano, perche' "
                "un pianificatore che assegna comunque su quelle finestre non "
                "sta facendo meglio, sta rispondendo a un'altra domanda."
            )
    return notes


# --------------------------------------------------------------------------
# Esecuzione
# --------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Esegue la valutazione e restituisce il codice di uscita del processo."""
    if args.a < args.da:
        console.print(
            "[red]Periodo rovesciato[/red]: --a precede --da. "
            "Scambia i due estremi."
        )
        return 2
    if args.finestra <= 0:
        console.print("[red]--finestra deve essere positiva[/red].")
        return 2
    if args.orizzonte <= 0:
        console.print(
            "[red]--orizzonte deve essere positivo[/red]: un piano che non "
            "guarda avanti nemmeno un minuto non puo' contenere nessuna "
            "missione."
        )
        return 2
    # Zero non vuol dire "nessun limite": chi scrive --max-finestre 0 sta
    # chiedendo qualcosa che non ha senso, e finora si vedeva restituire la
    # rigiocata intera senza un avviso, cioe' il numero che non aveva chiesto.
    if args.max_finestre is not None and args.max_finestre < 1:
        console.print(
            "[red]--max-finestre deve valere almeno 1[/red]. Per rigiocare "
            "tutte le finestre del periodo basta non passare l'opzione."
        )
        return 2
    if args.campioni_bootstrap < 0 or args.permutazioni < 0:
        console.print(
            "[red]--campioni-bootstrap e --permutazioni non possono essere "
            "negativi[/red]."
        )
        return 2

    console.print(
        Panel(
            Text(
                f"Periodo {args.da.isoformat()} - {args.a.isoformat()}, finestre "
                f"da {args.finestra:g} ore, orizzonte {args.orizzonte} minuti.\n"
                + data_source_line()
            ),
            title="Che cosa sto per rigiocare",
            border_style="blue",
        )
    )

    started = time.perf_counter()
    with console.status("Leggo i rilevamenti e costruisco gli scenari...", spinner=SPINNER):
        scenarios, detections, available = load_scenarios(
            args.da,
            args.a,
            window_hours=args.finestra,
            horizon_minutes=args.orizzonte,
            max_windows=args.max_finestre,
        )
    if not scenarios:
        # I due vuoti non sono lo stesso vuoto e non vanno detti con la stessa
        # riga: "non ho trovato rilevamenti" manda a correggere il periodo,
        # "ho trovato rilevamenti ma nessuna finestra" manda a guardare la
        # finestra o il costruttore di scenari. Dire il primo quando vale il
        # secondo spedisce chi legge dalla parte sbagliata.
        if not detections:
            console.print(
                "[yellow]Nessun rilevamento nel periodo: non c'e' niente da "
                "valutare.[/yellow] Prova ad allargare --da e --a: le fixture "
                "coprono la stagione 2025 (giugno-settembre)."
            )
        else:
            console.print(
                f"[yellow]{len(detections)} rilevamenti letti, ma nessuna "
                "finestra di pianificazione ne e' uscita.[/yellow] Non e' un "
                "periodo vuoto: e' il taglio in finestre che non ha prodotto "
                f"niente con --finestra {args.finestra:g} ore. Prova una "
                "finestra diversa."
            )
        return 2

    constraint_set = translate_order(args.ordine)
    if constraint_set is not None:
        console.print(
            Panel(
                Text(
                    f"Ordine: {args.ordine}\n"
                    f"Tradotto da '{constraint_set.translator}' in "
                    f"{len(constraint_set.constraints)} vincoli:\n"
                    + "\n".join("  " + r for r in constraint_set.summary_lines())
                ),
                title="Ordine del coordinatore, uguale per tutti i pianificatori",
                border_style="cyan",
            )
        )

    planners = build_planners(
        max_seconds=args.max_secondi, with_solver=not args.senza_solver
    )
    console.print(
        f"Confronto {len(planners)} pianificatori su {len(scenarios)} scenari "
        f"appaiati (finestre disponibili nel periodo: {available})."
    )

    with console.status(
        f"Eseguo {len(planners)} pianificatori su {len(scenarios)} scenari...", spinner=SPINNER
    ):
        result = compare_planners(
            scenarios,
            planners,
            constraint_set=constraint_set,
            reference=REFERENCE_NAME,
            seed=args.seme,
            bootstrap_samples=args.campioni_bootstrap,
            permutations=args.permutazioni,
            coverage_threshold=args.soglia_copertura,
            validate=not args.senza_validatore,
            label=f"rigiocata {args.da.isoformat()} - {args.a.isoformat()}",
        )
    elapsed = time.perf_counter() - started

    render_comparison(result, console)

    extra_notes = solver_notes(result)
    if args.max_finestre is not None and available > len(scenarios):
        extra_notes.append(
            f"Rigiocate {len(scenarios)} finestre su {available} disponibili nel "
            "periodo (--max-finestre). I numeri descrivono quel sottoinsieme, "
            "non il periodo intero."
        )
    if args.senza_validatore:
        extra_notes.append(
            "Validazione disattivata (--senza-validatore): non si sa quanti di "
            "questi piani siano eseguibili, e un piano non eseguibile che vince "
            "sulle metriche non ha vinto niente."
        )
    if extra_notes:
        console.print(
            Panel(
                Text("\n".join("- " + n for n in extra_notes)),
                title="Da sapere su questa esecuzione",
                border_style="red",
            )
        )

    ablations = []
    if args.ablazioni:
        from tagliafuoco.eval.ablation import run_all_ablations

        with console.status("Eseguo le ablazioni...", spinner=SPINNER):
            ablations = run_all_ablations(
                scenarios,
                max_seconds=args.max_secondi,
                seed=args.seme,
                include_solver=not args.senza_solver,
            )
        render_ablations(ablations, console)

    payload = {
        "esperimento": "rigiocata della stagione, confronto appaiato",
        "generato_il": datetime.now(timezone.utc).isoformat(),
        "durata_secondi": round(elapsed, 2),
        "periodo": {
            "da": args.da.isoformat(),
            "a": args.a.isoformat(),
            "finestra_ore": args.finestra,
            "orizzonte_minuti": args.orizzonte,
            "finestre_disponibili": available,
            "finestre_usate": len(scenarios),
        },
        "dati": {
            "rilevamenti": len(detections),
            "fonte": "FIRMS" if config.has_firms_key() else "fixture sintetiche",
            "nota_fonte": data_source_line(),
        },
        "ordine": {
            "testo": args.ordine,
            # Il `ConstraintSet` e' un modello Pydantic e non ha `to_dict`: si
            # serializza con il suo `model_dump`, altrimenti il salvataggio
            # generico lo ridurrebbe alla sua rappresentazione testuale e
            # l'ordine non sarebbe piu' rileggibile.
            "constraint_set": (
                constraint_set.model_dump(mode="json")
                if constraint_set is not None
                else None
            ),
        },
        "impostazioni": {
            "max_secondi_solver": args.max_secondi,
            "solver_deterministico": True,
            "validazione": not args.senza_validatore,
            "seme": args.seme,
            "campioni_bootstrap": args.campioni_bootstrap,
            "permutazioni": args.permutazioni,
            "soglia_copertura": args.soglia_copertura,
        },
        "limiti": list(LIMITI),
        "convenzioni": describe_conventions(),
        "avvertenze": methodological_warnings(result) + extra_notes,
        "sintesi": summary_lines(result),
        "confronto": result,
        "ablazioni": ablations,
    }

    if not args.senza_salvataggio:
        path = save_json(payload, args.nome, directory=args.output)
        console.print(f"\nRisultato grezzo salvato in [bold]{path}[/bold]")
    console.print(f"Valutazione completata in {elapsed:.1f} secondi.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:  # pragma: no cover - percorso interattivo
        console.print("\n[yellow]Interrotto.[/yellow] Niente e' stato salvato.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
