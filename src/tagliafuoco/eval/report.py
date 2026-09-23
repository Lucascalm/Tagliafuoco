"""Resoconto della valutazione: i numeri con accanto la loro incertezza.

PERCHE' QUESTO MODULO ESISTE
----------------------------
Una valutazione finisce in un numero, e un numero stampato da solo viene
creduto. Qui la regola e' che un numero nudo non si stampa mai: accanto a ogni
misura ci stanno il denominatore, l'intervallo di confidenza o il valore p, a
seconda di che cosa la rende fragile. Se un dato non ha incertezza da mostrare
(un conteggio, per esempio) allora si mostra il conteggio e non la percentuale
che lo nasconde.

Due formati, un solo contenuto. Il terminale usa `rich` perche' e' il modo in
cui la valutazione si guarda mentre gira; il JSON serve a riaprirla fra sei mesi
e a rifarci sopra un grafico. Entrambi derivano dagli stessi oggetti, quindi non
possono divergere.

L'AVVERTENZA NON STA IN FONDO
-----------------------------
Tagliafuoco e' un simulatore. La valutazione dice che cosa il sistema avrebbe
PROPOSTO su dati passati, non che cosa sarebbe SUCCESSO: non modelliamo la
propagazione del fuoco ne' l'effetto dell'intervento, quindi "copertura 0.81"
significa "avrebbe mandato abbastanza acqua su 81 focolai su 100 secondo la
nostra stima del fabbisogno", non "ne avrebbe spenti 81". Chi legge un resoconto
deve incontrare questa frase prima dei numeri, non dopo: in fondo a una pagina
la leggono in pochi, e chi la legge lo fa quando si e' gia' convinto.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tagliafuoco.config import DISCLAIMER, PROCESSED_DIR
from tagliafuoco.eval.ablation import AblationResult
from tagliafuoco.eval.comparison import (
    ComparisonResult,
    PairedDifference,
    methodological_warnings,
)
from tagliafuoco.eval.metrics import describe_conventions
from tagliafuoco.eval.nl_benchmark import BenchmarkResult, TranslatorComparison

__all__ = [
    "LIMITI",
    "format_comparison",
    "render_ablations",
    "render_benchmark",
    "render_comparison",
    "render_translator_comparison",
    "save_json",
    "to_json",
]


#: I limiti di cio' che questa valutazione puo' dire. Vanno stampati in testa.
LIMITI: tuple[str, ...] = (
    "La valutazione dice che cosa il sistema avrebbe PROPOSTO, non che cosa "
    "sarebbe SUCCESSO: non e' modellata ne' la propagazione del fuoco ne' "
    "l'effetto dell'intervento.",
    "Un focolaio 'coperto' e' un focolaio a cui il piano manda abbastanza acqua "
    "secondo una stima del fabbisogno che e' essa stessa una taratura "
    "dichiarata, non una misura.",
    "I rilevamenti vengono da FIRMS o, senza chiave, da fixture sintetiche: nel "
    "secondo caso i numeri descrivono un mondo verosimile e inventato.",
    "Nessun confronto qui dentro e' con l'operato reale dei mezzi antincendio: "
    "il termine di paragone e' una regola banale, non la sala operativa.",
)


def _console(console: Optional[Console]) -> Console:
    return console if console is not None else Console()


def header_panel() -> Panel:
    """Il pannello che apre ogni resoconto: cos'e' questo strumento e cosa no."""
    text = Text()
    text.append(DISCLAIMER + "\n\n", style="bold yellow")
    for limite in LIMITI:
        text.append("- " + limite + "\n")
    return Panel(text, title="Prima dei numeri", border_style="yellow")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _rate_cell(rate: dict[str, Any]) -> str:
    """Un tasso con il suo intervallo e i suoi conteggi, mai il solo tasso."""
    low, high = rate.get("ci95", [0.0, 1.0])
    return (
        f"{_pct(rate.get('rate', 0.0))} "
        f"[{_pct(low)}-{_pct(high)}] "
        f"({rate.get('successes', 0)}/{rate.get('trials', 0)})"
    )


def _difference_cell(d: PairedDifference) -> str:
    """Differenza media appaiata, intervallo bootstrap e valore p, insieme."""
    if d.n_pairs == 0:
        return "nessuna coppia"
    digits = 3
    return (
        f"{d.mean_difference:+.{digits}f} "
        f"[{d.interval.low:+.{digits}f}, {d.interval.high:+.{digits}f}] "
        f"p={d.test.p_value:.3f}"
    )


# --------------------------------------------------------------------------
# Confronto fra pianificatori
# --------------------------------------------------------------------------


def comparison_tables(result: ComparisonResult) -> tuple[Table, Table]:
    """Due tabelle: dove stanno i pianificatori, e quanto distano davvero.

    Sono separate apposta. La prima e' contesto e si legge per colonne; la
    seconda e' il risultato e si legge per righe. Mettere le medie accanto alle
    differenze inviterebbe a sottrarre le medie, che e' proprio il calcolo che
    il confronto appaiato sostituisce.
    """
    aggregate = result.aggregate()

    context = Table(
        title=(
            f"Dove stanno i pianificatori ({len(result.scenario_ids)} scenari "
            "appaiati). Somme di stagione, NON i termini della differenza. "
            "'raggiunti' = almeno un mezzo sul focolaio; 'coperti' = almeno il "
            f"{result.coverage_threshold:.0%} del fabbisogno d'acqua."
        ),
        header_style="bold",
    )
    context.add_column("pianificatore", overflow="fold")
    context.add_column("raggiunti", justify="right")
    context.add_column("coperti", justify="right")
    context.add_column("cop. WUI", justify="right")
    context.add_column("acqua", justify="right")
    context.add_column("risposta media / p90", justify="right")
    context.add_column("ore-mezzo", justify="right")
    context.add_column("piani validi", justify="right")
    for name in result.planner_names:
        row = aggregate[name]
        validated = row.get("plans_validated", 0)
        context.add_row(
            name + (" (rif.)" if name == result.reference else ""),
            f"{row['fires_attended']}/{row['fires_total']} "
            f"({_pct(row['attendance_ratio'])})",
            f"{row['fires_covered']}/{row['fires_total']} "
            f"({_pct(row['coverage_ratio'])})",
            f"{row['fires_wui_covered']}/{row['fires_wui_total']}",
            _pct(row["water_fulfilment_ratio"]),
            f"{row['mean_response_minutes']:.1f} / "
            f"{row['p90_response_minutes']:.1f} min (n={row['responses_counted']})",
            f"{row['total_vehicle_minutes'] / 60.0:.1f}",
            f"{row.get('plans_valid', 0)}/{validated}" if validated else "non validati",
        )

    diffs = Table(
        title=(
            f"Differenze appaiate contro '{result.reference}': media della "
            f"differenza per scenario, intervallo bootstrap al "
            f"{result.confidence_level:.0%} e test di permutazione (seme "
            f"{result.seed})"
        ),
        header_style="bold",
    )
    # Le tre colonne di testo si piegano invece di essere tagliate: su ottanta
    # colonne "non distinguibile dal rumore" diventava "non distinguib...", e
    # una lettura troncata e' esattamente il numero letto male che tutto questo
    # modulo esiste per impedire.
    diffs.add_column("metrica", overflow="fold")
    diffs.add_column("pianificatore", overflow="fold")
    diffs.add_column("differenza media [IC] p", justify="right")
    diffs.add_column("coppie", justify="right")
    diffs.add_column("vinte/perse/pari", justify="right", overflow="fold")
    diffs.add_column("lettura", overflow="fold")
    for d in result.differences:
        # La lettura guarda intervallo e valore p insieme: l'intervallo da solo
        # dichiarerebbe "distinguibile dal rumore" anche dove il test sul segno
        # non puo' arrivare alla soglia perche' le coppie sono troppo poche.
        proven = d.interval.excludes_zero and d.test.significant()
        style = None if proven else "dim"
        diffs.add_row(
            d.label,
            d.planner_a,
            _difference_cell(d),
            f"{d.n_pairs}" + (f" (-{d.pairs_dropped})" if d.pairs_dropped else ""),
            f"{d.wins_a}/{d.wins_b}/{d.ties}",
            d.reading(),
            style=style,
        )
    return context, diffs


def render_comparison(
    result: ComparisonResult, console: Optional[Console] = None
) -> None:
    """Stampa il confronto a schermo, avvertenze comprese."""
    out = _console(console)
    out.print(header_panel())
    out.print(
        Panel(
            Text("\n".join("- " + c for c in describe_conventions())),
            title="Convenzioni di misura usate per questi numeri",
            border_style="blue",
        )
    )
    context, diffs = comparison_tables(result)
    out.print(context)
    out.print(diffs)

    warnings = methodological_warnings(result)
    if warnings:
        out.print(
            Panel(
                Text("\n".join("- " + w for w in warnings)),
                title="Avvertenze su questo risultato",
                border_style="red",
            )
        )
    if result.notes:
        out.print(
            Panel(
                Text("\n".join("- " + n for n in result.notes)),
                title="Note di esecuzione",
                border_style="magenta",
            )
        )


def format_comparison(result: ComparisonResult) -> str:
    """Il confronto in testo semplice, per i log e per chi non ha un terminale.

    Non e' una seconda implementazione del resoconto: e' lo stesso `rich` che
    scrive su una console senza colori, quindi il contenuto non puo' divergere
    da quello mostrato a schermo.
    """
    buffer = Console(record=True, width=120, no_color=True, file=io.StringIO())
    render_comparison(result, buffer)
    return buffer.export_text()


# --------------------------------------------------------------------------
# Benchmark della traduzione
# --------------------------------------------------------------------------


def benchmark_tables(result: BenchmarkResult) -> list[Table]:
    """Tabelle del benchmark: sintesi, per tipo di vincolo, confusione."""
    data = result.to_dict()

    summary = Table(
        title=f"Traduttore '{result.label}' su {result.n_cases} ordini annotati",
        header_style="bold",
    )
    summary.add_column("misura")
    summary.add_column("valore [IC95] (successi/prove)", justify="right")
    summary.add_row("corrispondenza esatta dell'insieme", _rate_cell(data["exact_set_match"]))
    summary.add_row(
        "corrispondenza esatta con i parametri",
        _rate_cell(data["exact_match_with_params"]),
    )
    summary.add_row("tasso di allucinazione", _rate_cell(data["hallucination"]))
    summary.add_row(
        "astensione corretta quando attesa", _rate_cell(data["abstention"]["correct"])
    )
    summary.add_row(
        "astensione a sproposito", _rate_cell(data["abstention"]["spurious"])
    )
    summary.add_row(
        "parametri esatti a tipo giusto", _rate_cell(data["parameter_accuracy"])
    )
    micro = data["micro"]
    summary.add_row(
        "precisione (micro)",
        f"{_pct(micro['precision'])} "
        f"[{_pct(micro['precision_ci95'][0])}-{_pct(micro['precision_ci95'][1])}] "
        f"({micro['true_positives']}/{micro['true_positives'] + micro['false_positives']})",
    )
    summary.add_row(
        "richiamo (micro)",
        f"{_pct(micro['recall'])} "
        f"[{_pct(micro['recall_ci95'][0])}-{_pct(micro['recall_ci95'][1])}] "
        f"({micro['true_positives']}/{micro['true_positives'] + micro['false_negatives']})",
    )
    summary.add_row("F1 micro / macro", f"{micro['f1']:.3f} / {data['macro_f1']:.3f}")

    by_kind = Table(title="Per tipo di vincolo", header_style="bold")
    # Piegare invece di tagliare: un tipo tagliato a "FORBID_ASSIGN..." non si
    # distingue da "FORCE_ASSIGN..." su una console stretta, ed e' proprio la
    # coppia su cui questa tabella dice la cosa piu' interessante.
    by_kind.add_column("tipo", overflow="fold")
    by_kind.add_column("attesi", justify="right")
    by_kind.add_column("corretti", justify="right")
    by_kind.add_column("precisione", justify="right")
    by_kind.add_column("richiamo", justify="right")
    by_kind.add_column("F1", justify="right")
    for kind, score in sorted(
        data["by_kind"].items(), key=lambda kv: (-kv[1]["support"], kv[0])
    ):
        by_kind.add_row(
            kind,
            str(score["support"]),
            str(score["true_positives"]),
            f"{score['precision']:.2f} [{score['precision_ci'][0]:.2f}-{score['precision_ci'][1]:.2f}]",
            f"{score['recall']:.2f} [{score['recall_ci'][0]:.2f}-{score['recall_ci'][1]:.2f}]",
            f"{score['f1']:.2f}",
        )

    matrix = data["confusion_matrix"]
    columns = sorted({c for row in matrix.values() for c in row})
    confusion = Table(
        title=(
            "Matrice di confusione: righe attese, colonne emesse. Le sigle "
            "sono sciolte nella tabella che segue."
        ),
        header_style="bold",
    )
    # `overflow="fold"` e non il taglio di default: su una console stretta il
    # taglio sostituirebbe la coda della sigla con un puntino, e due tipi
    # diversi tornerebbero a chiamarsi allo stesso modo proprio nella tabella
    # che serve a distinguerli. Piegando, l'etichetta si spezza su due righe ma
    # non perde nessuna lettera.
    confusion.add_column("att. \\ em.", overflow="fold")
    for col in columns:
        confusion.add_column(_short(col), justify="right", overflow="fold")
    for row_name, row in matrix.items():
        confusion.add_row(
            _short(row_name), *[str(row.get(col, 0)) or "" for col in columns]
        )

    # La legenda non e' un ornamento: senza di essa una sigla di due lettere non
    # e' leggibile da chi apre il resoconto per la prima volta, ed e' la
    # tabella che decide se il traduttore sbaglia per omissione o per scambio.
    legend = Table(title="Sigle usate nella matrice", header_style="bold")
    legend.add_column("sigla")
    legend.add_column("tipo di vincolo")
    for name in sorted({*columns, *matrix.keys()}):
        legend.add_row(_short(name), name)

    difficulty = Table(title="Corrispondenza esatta per difficolta'", header_style="bold")
    difficulty.add_column("difficolta'")
    difficulty.add_column("esatte [IC95] (su casi)", justify="right")
    for key, rate in data["by_difficulty"].items():
        difficulty.add_row(key, _rate_cell(rate))

    return [summary, by_kind, confusion, legend, difficulty]


def _abbreviate(kind: str, extra: int = 0) -> str:
    """Forma abbreviata di un tipo di vincolo, allungabile di `extra` lettere.

    Regola base: l'iniziale di ogni parola, cioe' due o tre lettere. Le sigle
    stanno corte perche' la matrice di confusione ha una colonna per tipo e su
    una console da ottanta colonne una sigla lunga viene tagliata dal
    formattatore: un'etichetta tagliata e' una collisione che ritorna a livello
    di pixel, dopo essere stata risolta a livello di dato.

    `extra` allunga la PRIMA parola, perche' e' li' che i tipi quasi omonimi si
    distinguono (FORCE contro FORBID). Allungare l'ultima non li separerebbe
    mai, visto che l'ultima parola e' la stessa.
    """
    if kind.startswith("("):
        return kind
    parts = kind.split("_")
    if len(parts) == 1:
        return parts[0][: 3 + extra]
    head = parts[0][: 1 + extra]
    return head + "".join(p[0] for p in parts[1:])


def _build_short_names() -> dict[str, str]:
    """Sigle uniche per tutti i tipi di vincolo del repertorio.

    La regola ingenua mandava FORCE_ASSIGNMENT e FORBID_ASSIGNMENT sulla stessa
    sigla, 'FASSI'. Nella matrice di confusione quello significava due righe e
    due colonne con la stessa etichetta, cioe' un lettore che non puo'
    distinguere "ha imposto un mezzo dove andava vietato" da "ha vietato un
    mezzo dove andava imposto": proprio l'errore che la matrice esiste per
    mostrare, e per giunta un errore sistematico, perche' i due tipi sono l'uno
    la negazione dell'altro.

    Le sigle si calcolano qui una volta sola su TUTTO il repertorio dello
    schema, allungando SOLO quelle che collidono: le altre restano corte come
    prima, cosi' la matrice continua a starci in larghezza e le tabelle gia'
    lette non cambiano etichetta sotto gli occhi di chi le confronta. Un tipo
    aggiunto in futuro a `nlu/schema.ConstraintKind` non puo' reintrodurre una
    collisione in silenzio, che e' il modo in cui questa era arrivata.
    """
    from tagliafuoco.nlu.schema import ConstraintKind

    kinds = sorted(k.value for k in ConstraintKind)
    extras: dict[str, int] = {k: 0 for k in kinds}
    for _round in range(12):
        names = {k: _abbreviate(k, extras[k]) for k in kinds}
        taken: dict[str, list[str]] = {}
        for kind, short in names.items():
            taken.setdefault(short, []).append(kind)
        colliding = [group for group in taken.values() if len(group) > 1]
        if not colliding:
            return names
        for group in colliding:
            for kind in group:
                extras[kind] += 1
    # Irraggiungibile con i nomi dello schema, ma un fallback che collide in
    # silenzio sarebbe peggio di uno lungo: si usa il nome per intero.
    return {k: k for k in kinds}


#: Sigle dei tipi di vincolo, calcolate una volta sola all'import.
_SHORT_NAMES: dict[str, str] = _build_short_names()


def _short(kind: str) -> str:
    """Abbrevia i nomi dei tipi perche' la matrice stia in larghezza.

    I tipi del repertorio hanno una sigla precalcolata e garantita unica; un
    nome che non e' del repertorio (una voce come "(nessuno)", o un tipo emesso
    da un traduttore sperimentale) si abbrevia con la regola generale.
    """
    return _SHORT_NAMES.get(kind) or _abbreviate(kind)


def render_benchmark(
    result: BenchmarkResult, console: Optional[Console] = None, *, worst: int = 6
) -> None:
    """Stampa il benchmark della traduzione, con i casi peggiori in chiaro.

    I casi peggiori si mostrano sempre: una media di F1 nasconde esattamente
    cio' che interessa a chi deve fidarsi del traduttore, cioe' su quali ordini
    sbaglia e come.
    """
    out = _console(console)
    out.print(header_panel())
    for table in benchmark_tables(result):
        out.print(table)

    # Tutte le tolleranze che hanno cambiato il conteggio, non solo le letture
    # alternative: una tolleranza che abbassa il tasso di allucinazione senza
    # comparire qui rende quel tasso piu' bello di quanto il corpus autorizzi.
    tolerances = result.tolerances_applied()
    if tolerances:
        lines = [
            "Queste tolleranze vengono dalle note del corpus, non da chi ha "
            "scritto il misuratore, e hanno cambiato il punteggio dei casi "
            "elencati.",
            "",
        ]
        lines.extend(
            f"{case_id}: " + "; ".join(reasons)
            for case_id, reasons in tolerances.items()
        )
        out.print(
            Panel(
                Text("\n".join(lines)),
                title=f"Tolleranze applicate ({len(tolerances)} casi)",
                border_style="blue",
            )
        )

    worst_cases = result.hardest_cases(worst)
    if worst_cases:
        table = Table(title=f"I {len(worst_cases)} casi peggiori", header_style="bold")
        table.add_column("caso")
        table.add_column("ordine")
        table.add_column("atteso")
        table.add_column("emesso")
        table.add_column("F1", justify="right")
        for case in worst_cases:
            table.add_row(
                case.case_id,
                (case.ordine[:48] + "...") if len(case.ordine) > 48 else case.ordine or "(vuoto)",
                ", ".join(_short(k) for k in case.expected_kinds) or "(niente)",
                ", ".join(_short(k) for k in case.produced_kinds) or "(niente)",
                f"{case.f1:.2f}",
            )
        out.print(table)

    if result.notes:
        out.print(
            Panel(
                Text("\n".join("- " + n for n in result.notes)),
                title="Note di esecuzione",
                border_style="magenta",
            )
        )


def render_translator_comparison(
    comparison: TranslatorComparison,
    console: Optional[Console] = None,
    *,
    worst: int = 6,
) -> None:
    """Regole contro modello linguistico, con la conclusione scritta per esteso.

    `worst` si propaga a entrambi i benchmark perche' i casi peggiori dei due
    traduttori vanno guardati con lo stesso metro: mostrarne sei di uno e due
    dell'altro darebbe l'impressione che il secondo sbagli meno.
    """
    out = _console(console)
    render_benchmark(comparison.rules, out, worst=worst)
    if comparison.model is not None:
        render_benchmark(comparison.model, out, worst=worst)
    out.print(
        Panel(
            Text("\n".join(comparison.conclusion_lines())),
            title="Conclusione sul confronto fra traduttori",
            border_style="green",
        )
    )


# --------------------------------------------------------------------------
# Ablazioni
# --------------------------------------------------------------------------


def render_ablations(
    results: Sequence[AblationResult], console: Optional[Console] = None
) -> None:
    """Stampa le ablazioni, ognuna con la domanda a cui risponde."""
    out = _console(console)
    for ablation in results:
        body = Text()
        body.append("Domanda: ", style="bold")
        body.append(ablation.question + "\n")
        body.append("Metodo: ", style="bold")
        body.append(ablation.method + "\n")
        out.print(Panel(body, title=f"Ablazione: {ablation.name}", border_style="cyan"))

        if ablation.comparison is not None:
            _, diffs = comparison_tables(ablation.comparison)
            out.print(diffs)

        if ablation.findings:
            out.print(
                Panel(
                    Text("\n".join("- " + f for f in ablation.findings)),
                    title="Cosa risulta",
                    border_style="green",
                )
            )
        if ablation.limits:
            out.print(
                Panel(
                    Text("\n".join("- " + limit for limit in ablation.limits)),
                    title="Cosa questo esperimento non dice",
                    border_style="red",
                )
            )
        extra = ablation.extra.get("per_pianificatore")
        if isinstance(extra, dict) and extra:
            table = Table(title="Piani respinti dal validatore", header_style="bold")
            table.add_column("pianificatore")
            table.add_column("piani invalidi", justify="right")
            table.add_column("violazioni bloccanti", justify="right")
            table.add_column("regole scattate")
            for name, row in extra.items():
                table.add_row(
                    name,
                    f"{row['piani_invalidi']}/{row['scenari']}",
                    str(row["violazioni_bloccanti"]),
                    ", ".join(row["regole_scattate"]) or "nessuna",
                )
            out.print(table)


# --------------------------------------------------------------------------
# Serializzazione
# --------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Riduce a tipi JSON cio' che i risultati della valutazione contengono."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        # Gli insiemi si ordinano prima di scriverli. `json.dumps(sort_keys=True)`
        # ordina le chiavi dei dizionari ma non gli elementi delle liste: senza
        # questo passaggio due esecuzioni identiche produrrebbero due file
        # diversi, e chi li confronta con diff vedrebbe differenze che non
        # esistono. Se i valori non sono confrontabili fra loro si ripiega sulla
        # loro forma testuale, che un ordine ce l'ha sempre.
        try:
            ordered = sorted(value)
        except TypeError:  # pragma: no cover - insiemi eterogenei
            ordered = sorted(value, key=repr)
        return [_jsonable(v) for v in ordered]
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def to_json(payload: Any, *, indent: Optional[int] = 2) -> str:
    """Serializza un risultato di valutazione, oggetti compresi."""
    return json.dumps(
        _jsonable(payload), ensure_ascii=False, indent=indent, sort_keys=True
    )


def save_json(
    payload: Any, name: str, *, directory: Optional[Path] = None, stamp: bool = True
) -> Path:
    """Scrive il risultato grezzo sotto `data/processed/` e restituisce il percorso.

    Il nome porta la data e l'ora in UTC perche' due valutazioni dello stesso
    giorno non si sovrascrivano a vicenda: un risultato perso e' un esperimento
    da rifare, e questi esperimenti costano minuti di solver.
    """
    target = Path(directory) if directory is not None else PROCESSED_DIR
    target.mkdir(parents=True, exist_ok=True)
    suffix = (
        "_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") if stamp else ""
    )
    path = target / f"{name}{suffix}.json"
    path.write_text(to_json(payload), encoding="utf-8")
    return path


def summary_lines(result: ComparisonResult) -> list[str]:
    """Il confronto in poche righe, per chi legge solo la fine di un log."""
    lines = [
        f"Confronto '{result.label}' su {len(result.scenario_ids)} scenari "
        f"appaiati, riferimento '{result.reference}'.",
    ]
    lines.extend(d.verdict() for d in result.differences)
    lines.extend(methodological_warnings(result))
    lines.append(
        "Questi numeri dicono cosa il sistema avrebbe proposto, non cosa sarebbe "
        "successo."
    )
    return lines
