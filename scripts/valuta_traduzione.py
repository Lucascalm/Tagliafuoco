"""Accuratezza del traduttore sul corpus annotato: l'esperimento 2 di SPEC.md.

PERCHE' QUESTO SCRIPT ESISTE
----------------------------
Il principio del progetto e' che il modello linguistico non decide: traduce, e
basta. Un principio del genere si difende solo con un numero, e il numero e'
questo: quante volte la traduzione di un ordine in italiano produce esattamente
i vincoli che un essere umano aveva annotato a mano, quante volte ne inventa di
nuovi, e quante volte si astiene quando doveva.

Lo script esiste per rendere quel numero riproducibile con un comando e senza
chiavi. Il traduttore a regole gira sempre; il traduttore con modello
linguistico gira soltanto se `ANTHROPIC_API_KEY` c'e'. Il confronto fra i due e'
il risultato interessante, e la conclusione va scritta in entrambi i versi: se
le regole arrivano vicine, allora il modello linguistico aggiunge poco, e va
detto invece che nascosto.

IL CORPUS NON SI TOCCA
----------------------
`benchmarks/ordini_vocali.json` e' annotato a mano e questo script lo LEGGE.
Aggiungere casi dopo aver visto i risultati, o correggere un'annotazione perche'
il traduttore non ci arriva, trasformerebbe la misura in una profezia che si
autoavvera. Le uniche tolleranze ammesse sono quelle gia' dichiarate nelle note
del corpus, e stanno in `eval/nl_benchmark.CASE_TOLERANCES`, in un punto solo e
ispezionabile.

I FILTRI SONO UNA RESTRIZIONE DICHIARATA, NON UNA SCORCIATOIA
-------------------------------------------------------------
`--difficolta`, `--categoria` e `--casi` servono a girare in fretta durante lo
sviluppo e a guardare una famiglia di ordini per volta. Restringono il corpus,
quindi cambiano i numeri: il filtro applicato finisce nel resoconto e nel file
salvato, perche' una precisione dell'ottanta per cento sui soli casi facili non
e' la stessa cosa di una precisione dell'ottanta per cento sul corpus intero.

USO
---
    .venv/Scripts/python.exe scripts/valuta_traduzione.py
    .venv/Scripts/python.exe scripts/valuta_traduzione.py --difficolta facile
    .venv/Scripts/python.exe scripts/valuta_traduzione.py --categoria astensione
    .venv/Scripts/python.exe scripts/valuta_traduzione.py --casi 10 --peggiori 10
    .venv/Scripts/python.exe scripts/valuta_traduzione.py --solo-regole

Senza chiave lo script gira interamente e in pochi secondi: e' il caso normale,
non un ripiego.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.text import Text  # noqa: E402

from tagliafuoco import config  # noqa: E402
from tagliafuoco.eval.comparison import (  # noqa: E402
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_PERMUTATIONS,
    DEFAULT_SEED,
)
from tagliafuoco.eval.nl_benchmark import (  # noqa: E402
    CORPUS_PATH,
    BenchmarkCase,
    TranslatorComparison,
    compare_translators,
    load_corpus,
    run_benchmark,
)
from tagliafuoco.eval.report import (  # noqa: E402
    LIMITI,
    render_translator_comparison,
    save_json,
)

console = Console()
#: Spinner dell'attesa. Rich ne userebbe uno a caratteri braille, che su Windows
#: non sta nella pagina di codice della console: quando l'uscita viene rediretta
#: rich sceglie il rendering legacy, prova a scrivere quei caratteri in cp1252 e
#: solleva UnicodeEncodeError all'USCITA dal blocco, cioe' a valutazione gia'
#: conclusa. Sono minuti di solver buttati per un'animazione. Questo ne usa
#: quattro segni ASCII e non puo' fallire in nessuna codifica.
SPINNER = "line"



# --------------------------------------------------------------------------
# Argomenti
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Gli argomenti della riga di comando, in italiano perche' li legge una persona."""
    parser = argparse.ArgumentParser(
        prog="valuta_traduzione",
        description=(
            "Misura l'accuratezza del traduttore di ordini sul corpus annotato "
            "a mano. Gira senza chiavi: il traduttore a regole e' il "
            "riferimento, il modello linguistico si aggiunge se la chiave c'e'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "I risultati grezzi finiscono in data/processed/. Il corpus si "
            "legge e non si riscrive: aggiungere casi dopo aver visto i "
            "risultati non e' una valutazione."
        ),
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help=f"percorso del corpus (default: {CORPUS_PATH})",
    )
    parser.add_argument(
        "--difficolta",
        default="",
        help=(
            "tiene solo i casi di questa difficolta' (facile, media, "
            "difficile). Restringe il corpus, quindi cambia i numeri"
        ),
    )
    parser.add_argument(
        "--categoria",
        default="",
        help=(
            "tiene solo i casi di questa categoria (per esempio astensione, "
            "riserva, zona_geografica)"
        ),
    )
    parser.add_argument(
        "--casi",
        type=int,
        default=None,
        help=(
            "tiene solo i primi N casi rimasti dopo i filtri. Serve a provare "
            "la catena, non a pubblicare un risultato"
        ),
    )
    parser.add_argument(
        "--solo-regole",
        action="store_true",
        help=(
            "non esegue il traduttore con modello linguistico nemmeno se la "
            "chiave c'e'. Utile per misurare il caso che chiunque puo' "
            "riprodurre clonando il repository"
        ),
    )
    parser.add_argument(
        "--peggiori",
        type=int,
        default=6,
        help=(
            "quanti casi peggiori mostrare per esteso (default: %(default)s). "
            "Una media di F1 nasconde proprio cio' che interessa: su quali "
            "ordini il traduttore sbaglia e come"
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
        "--output",
        type=Path,
        default=None,
        help="cartella in cui salvare il JSON (default: data/processed/)",
    )
    parser.add_argument(
        "--nome",
        default="valutazione_traduzione",
        help="prefisso del file salvato (default: %(default)s)",
    )
    parser.add_argument(
        "--senza-salvataggio",
        action="store_true",
        help="stampa il resoconto senza scrivere niente su disco",
    )
    return parser


# --------------------------------------------------------------------------
# Selezione dei casi
# --------------------------------------------------------------------------


def select_cases(
    cases: Sequence[BenchmarkCase],
    *,
    difficolta: str,
    categoria: str,
    limit: Optional[int],
) -> tuple[list[BenchmarkCase], list[str]]:
    """Applica i filtri e restituisce anche come sono stati applicati.

    La seconda meta' del valore restituito non e' un dettaglio di interfaccia:
    e' cio' che verra' stampato accanto ai numeri e salvato nel file. Un
    punteggio calcolato su otto casi facili e uno calcolato su cinquanta casi
    non sono confrontabili, e senza la descrizione del filtro chi rilegge il
    JSON fra sei mesi non ha modo di accorgersene.
    """
    selected = list(cases)
    applied: list[str] = []

    if difficolta:
        wanted = difficolta.strip().lower()
        selected = [c for c in selected if c.difficolta.lower() == wanted]
        applied.append(f"difficolta' = {wanted}")
    if categoria:
        wanted = categoria.strip().lower()
        selected = [c for c in selected if c.categoria.lower() == wanted]
        applied.append(f"categoria = {wanted}")
    if limit is not None and limit > 0:
        if len(selected) > limit:
            applied.append(f"primi {limit} casi dei {len(selected)} rimasti")
        selected = selected[:limit]

    return selected, applied


def corpus_line(
    total: int, selected: int, applied: Sequence[str], path: Optional[Path]
) -> str:
    """Una riga che dice su quale corpus sono calcolati i numeri che seguono."""
    # Il percorso di default si chiede a `nl_benchmark`, che lo prende da
    # `config.BENCHMARKS_DIR`: riscriverlo a mano qui significherebbe che, il
    # giorno in cui il corpus si sposta, il resoconto dichiarerebbe un file
    # diverso da quello che ha letto davvero.
    where = str(path) if path is not None else str(CORPUS_PATH)
    if not applied:
        return f"Corpus intero: {selected} casi annotati da {where}."
    return (
        f"Corpus ristretto: {selected} casi su {total} da {where}. "
        "Filtri applicati: " + "; ".join(applied) + ". "
        "I numeri valgono per questo sottoinsieme, non per il corpus intero."
    )


def key_line(only_rules: bool) -> str:
    """Quale traduttore verra' eseguito, e perche'."""
    if only_rules:
        return (
            "Eseguo il solo traduttore a regole (--solo-regole): e' il caso "
            "riproducibile da chiunque, senza chiavi e senza rete."
        )
    if config.has_llm_key():
        return (
            "ANTHROPIC_API_KEY presente: eseguo regole e modello linguistico "
            "sugli stessi casi, e li confronto in modo appaiato caso per caso."
        )
    return (
        "ANTHROPIC_API_KEY assente: eseguo il solo traduttore a regole. Con la "
        "chiave mancante il traduttore con modello ricadrebbe sulle regole, e "
        "il confronto misurerebbe lo stesso traduttore contro se' stesso."
    )


# --------------------------------------------------------------------------
# Esecuzione
# --------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Esegue il benchmark e restituisce il codice di uscita del processo."""
    try:
        corpus = load_corpus(args.corpus)
    except FileNotFoundError:
        console.print(
            f"[red]Corpus non trovato[/red]: {args.corpus}. Il corpus di "
            f"riferimento e' {CORPUS_PATH}."
        )
        return 2
    except (ValueError, KeyError, TypeError) as exc:
        # Un corpus malformato va detto per quello che e'. Lasciando salire
        # l'eccezione chi lancia lo script vede una traccia di stack di
        # `json.loads` e crede di avere rotto il misuratore, mentre il file
        # rotto e' il suo: una diagnosi sbagliata costa piu' del guasto.
        console.print(
            f"[red]Corpus illeggibile[/red]: {args.corpus or CORPUS_PATH}. "
            f"{type(exc).__name__}: {exc}\n"
            "Il file deve essere un JSON con una lista 'casi', e ogni caso deve "
            "avere almeno 'id' e 'ordine'."
        )
        return 2

    cases, applied = select_cases(
        corpus,
        difficolta=args.difficolta,
        categoria=args.categoria,
        limit=args.casi,
    )
    if not cases:
        console.print(
            "[yellow]Nessun caso dopo i filtri.[/yellow] Le difficolta' sono "
            "facile, media e difficile; le categorie stanno nel campo "
            "'categoria' del corpus."
        )
        return 2

    console.print(
        Panel(
            Text(
                corpus_line(len(corpus), len(cases), applied, args.corpus)
                + "\n"
                + key_line(args.solo_regole)
            ),
            title="Che cosa sto per misurare",
            border_style="blue",
        )
    )

    started = time.perf_counter()
    if args.solo_regole:
        with console.status("Traduco gli ordini con le regole...", spinner=SPINNER):
            rules = run_benchmark(cases=cases, label="regole")
        comparison = TranslatorComparison(
            rules=rules,
            model=None,
            difference=None,
            skipped_reason=(
                "richiesto --solo-regole: il traduttore con modello "
                "linguistico non e' stato eseguito"
            ),
        )
    else:
        with console.status("Traduco gli ordini...", spinner=SPINNER):
            comparison = compare_translators(
                cases=cases,
                seed=args.seme,
                bootstrap_samples=args.campioni_bootstrap,
                permutations=args.permutazioni,
            )
    elapsed = time.perf_counter() - started

    render_translator_comparison(comparison, console, worst=max(0, args.peggiori))

    if applied:
        console.print(
            Panel(
                Text(corpus_line(len(corpus), len(cases), applied, args.corpus)),
                title="Attenzione: corpus ristretto",
                border_style="red",
            )
        )

    payload = {
        "esperimento": "accuratezza della traduzione sul corpus annotato",
        "generato_il": datetime.now(timezone.utc).isoformat(),
        "durata_secondi": round(elapsed, 2),
        "corpus": {
            "percorso": str(args.corpus) if args.corpus else str(CORPUS_PATH),
            "casi_totali": len(corpus),
            "casi_valutati": len(cases),
            "filtri": list(applied),
            "case_id": [c.case_id for c in cases],
        },
        "traduttori": {
            "regole": True,
            "modello": comparison.model is not None,
            "chiave_presente": config.has_llm_key(),
            "nota": key_line(args.solo_regole),
        },
        "impostazioni": {
            "seme": args.seme,
            "campioni_bootstrap": args.campioni_bootstrap,
            "permutazioni": args.permutazioni,
        },
        "limiti": list(LIMITI),
        "conclusione": comparison.conclusion_lines(),
        "confronto": comparison,
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
