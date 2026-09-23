"""Resoconto leggibile della validazione.

PERCHE' QUESTO FILE ESISTE
--------------------------
Un validatore che dice solo "respinto" non serve a niente: chi legge deve
poter capire quale regola ha reagito, su quale mezzo, di quanto si e' sforato
e cosa ci si aspettava. Per questo le violazioni portano con se' valore
atteso, valore trovato e scarto, e per questo qui si mostrano in tabella e non
in un elenco di frasi.

Ci sono due destinatari e due formati, ma un solo contenuto. Il terminale
(`render`) usa `rich` perche' la demo si fa a schermo e il colore distingue a
colpo d'occhio un blocco da un avviso. La API (`to_json`) serve lo stesso
oggetto in forma serializzabile, cosi' il resoconto mostrato nella pagina web
e quello stampato nel terminale non possono divergere: derivano entrambi da
`ValidationResult.to_dict()`.

Il tono e' quello del resto del progetto: niente esultanza quando il piano
passa, perche' passare significa soltanto che il piano e' coerente con il
modello dichiarato, non che funzionerebbe su un incendio vero.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tagliafuoco.validate.rules import ValidationSeverity, Violation
from tagliafuoco.validate.validator import ValidationResult

__all__ = ["format_report", "render", "to_json", "violations_table"]

_ETICHETTA_REGOLA = {
    "reference_integrity": "riferimenti",
    "assignment_wellformed": "forma dell'assegnazione",
    "horizon": "orizzonte",
    "vehicle_status": "stato del mezzo",
    "temporal_consistency": "coerenza temporale",
    "turnaround": "riattrezzamento",
    "arrival_time": "tempo di arrivo",
    "endurance": "autonomia",
    "shift": "turno",
    "water_capacity": "capacita' idrica",
    "water_source": "sorgente d'acqua",
    "night_flight": "volo notturno",
    "hard_constraint": "vincolo duro",
    "metrics_declaration": "metriche dichiarate",
    "water_demand": "fabbisogno idrico",
}


def _regola(nome: str) -> str:
    """Nome leggibile della regola. Gli identificatori restano in inglese."""
    return _ETICHETTA_REGOLA.get(nome, nome)


def _entita(violation: Violation) -> str:
    """Entita' coinvolte, compattate su una riga."""
    if not violation.entities:
        return "-"
    parti = []
    for chiave in (
        "vehicle_id",
        "fire_id",
        "water_source_id",
        "base_id",
        "constraint_id",
        "metric",
    ):
        if chiave in violation.entities:
            parti.append(violation.entities[chiave])
    if not parti:
        parti = [f"{k}={v}" for k, v in violation.entities.items()]
    return " / ".join(parti)


def _numero(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _migliaia(value: float) -> str:
    """Numero con il punto come separatore delle migliaia, come si scrive qui.

    Python separa le migliaia con la virgola, che in italiano e' il separatore
    dei decimali: "192,160 litri" si legge come centonovantadue litri e
    spiccioli, cioe' il contrario di quello che e'.
    """
    return f"{value:,.0f}".replace(",", ".")


# --------------------------------------------------------------------------
# Testo semplice
# --------------------------------------------------------------------------


def format_report(result: ValidationResult) -> str:
    """Resoconto in testo puro, senza colori e senza dipendenze di terminale.

    Serve ai log, ai file di esperimento e ai test: e' la forma che si puo'
    confrontare riga per riga senza codici di escape in mezzo.
    """
    righe: list[str] = []
    righe.append(result.summary_line())
    righe.append(
        f"Scenario {result.scenario_id} - validatore v{result.validator_version} "
        f"- tolleranza {result.tolerance.minutes:g} min"
    )

    m = result.metrics
    righe.append("")
    righe.append("Metriche ricalcolate dal validatore:")
    righe.append(
        f"  copertura reale        {m.fires_covered}/{m.fires_total} "
        f"({m.coverage_ratio:.0%}), dichiarata dal piano "
        f"{m.fires_covered_declared}/{m.fires_total}"
    )
    righe.append(
        f"  copertura WUI          {m.fires_wui_covered}/{m.fires_wui_total}"
    )
    righe.append(
        f"  litri erogabili        {_migliaia(m.water_deliverable_liters)} "
        f"su un fabbisogno di {_migliaia(m.water_demanded_liters)} "
        f"({m.water_fulfilment_ratio:.0%})"
    )
    righe.append(
        f"  tempo di risposta      medio {m.mean_response_minutes:.1f} min, "
        f"p90 {m.p90_response_minutes:.1f} min"
    )
    righe.append(
        f"  impiego                {m.vehicles_used} mezzi, "
        f"{m.total_vehicle_minutes:.0f} minuti-mezzo, "
        f"{_migliaia(m.total_cost_eur)} euro"
    )

    if result.comparisons:
        righe.append("")
        righe.append("Dichiarato dal piano contro ricalcolato dal validatore:")
        for c in result.comparisons:
            esito = "coincide" if c.agrees else "DIVERGE"
            righe.append(
                f"  {c.name:<26} dichiarato {_numero(c.declared):>12} | "
                f"ricalcolato {c.recomputed:>12.1f} {c.unit:<7} {esito}"
            )

    if not result.violations:
        righe.append("")
        righe.append("Nessuna violazione rilevata.")
        return "\n".join(righe)

    for severita, titolo in (
        (ValidationSeverity.BLOCKING, "Violazioni bloccanti"),
        (ValidationSeverity.WARNING, "Avvisi"),
    ):
        gruppo = [v for v in result.violations if v.severity is severita]
        if not gruppo:
            continue
        righe.append("")
        righe.append(f"{titolo} ({len(gruppo)}):")
        for v in gruppo:
            righe.append(f"  [{_regola(v.rule)}] {v.message}")
            if v.expected is not None or v.found is not None:
                scarto = f", scarto {v.delta:+.1f}" if v.delta is not None else ""
                righe.append(
                    f"      atteso {_numero(v.expected)}, "
                    f"trovato {_numero(v.found)}{scarto}"
                )
    return "\n".join(righe)


# --------------------------------------------------------------------------
# Terminale
# --------------------------------------------------------------------------


def violations_table(result: ValidationResult) -> Table:
    """Tabella delle violazioni, pronta per essere stampata con rich."""
    table = Table(
        title="Violazioni rilevate", show_lines=False, header_style="bold"
    )
    table.add_column("gravita'", no_wrap=True)
    table.add_column("regola", no_wrap=True)
    table.add_column("entita'", no_wrap=True)
    table.add_column("atteso", justify="right", no_wrap=True)
    table.add_column("trovato", justify="right", no_wrap=True)
    table.add_column("scarto", justify="right", no_wrap=True)
    table.add_column("messaggio")

    ordinate = sorted(result.violations, key=lambda v: (not v.is_blocking, v.rule))
    for v in ordinate:
        if v.is_blocking:
            gravita = Text("BLOCCO", style="bold red")
        else:
            gravita = Text("avviso", style="yellow")
        table.add_row(
            gravita,
            _regola(v.rule),
            _entita(v),
            _numero(v.expected),
            _numero(v.found),
            f"{v.delta:+.1f}" if v.delta is not None else "-",
            v.message,
        )
    return table


def _metrics_table(result: ValidationResult) -> Table:
    """Tabella del confronto fra dichiarato e ricalcolato."""
    table = Table(
        title="Dichiarato dal piano contro ricalcolato dal validatore",
        header_style="bold",
    )
    table.add_column("metrica", no_wrap=True)
    table.add_column("dichiarato", justify="right")
    table.add_column("ricalcolato", justify="right")
    table.add_column("scarto", justify="right")
    table.add_column("tolleranza", justify="right")
    table.add_column("esito", no_wrap=True)

    for c in result.comparisons:
        if c.declared is None:
            esito = Text("non dichiarato", style="dim")
        elif c.agrees:
            esito = Text("coincide", style="green")
        else:
            esito = Text("DIVERGE", style="bold red")
        table.add_row(
            f"{c.name} ({c.unit})" if c.unit else c.name,
            _numero(c.declared),
            f"{c.recomputed:.1f}",
            f"{c.delta:+.1f}" if c.delta is not None else "-",
            f"{c.tolerance:.1f}",
            esito,
        )
    return table


def render(result: ValidationResult, console: Optional[Console] = None) -> None:
    """Stampa il resoconto a schermo.

    Il pannello in testa dice subito l'unica cosa che conta per chi guarda la
    demo: il piano e' accettato o respinto. Il dettaglio viene dopo, perche' la
    diagnostica si legge solo quando serve.
    """
    console = console or Console()

    stile = "green" if result.is_valid else "red"
    titolo = "Piano accettato" if result.is_valid else "Piano respinto"
    console.print(
        Panel(
            Text(result.summary_line(), style=f"bold {stile}"),
            title=titolo,
            border_style=stile,
            subtitle=(
                f"scenario {result.scenario_id} - tolleranza "
                f"{result.tolerance.minutes:g} min"
            ),
        )
    )

    m = result.metrics
    riassunto = Table.grid(padding=(0, 2))
    riassunto.add_column(style="bold")
    riassunto.add_column()
    riassunto.add_row(
        "copertura reale",
        f"{m.fires_covered}/{m.fires_total} ({m.coverage_ratio:.0%})",
    )
    riassunto.add_row(
        "copertura WUI", f"{m.fires_wui_covered}/{m.fires_wui_total}"
    )
    riassunto.add_row(
        "litri erogabili",
        f"{_migliaia(m.water_deliverable_liters)} su "
        f"{_migliaia(m.water_demanded_liters)} "
        f"({m.water_fulfilment_ratio:.0%})",
    )
    riassunto.add_row(
        "tempi di risposta",
        f"medio {m.mean_response_minutes:.1f} min, p90 "
        f"{m.p90_response_minutes:.1f} min",
    )
    riassunto.add_row(
        "impiego",
        f"{m.vehicles_used} mezzi, {m.total_vehicle_minutes:.0f} minuti-mezzo, "
        f"{_migliaia(m.total_cost_eur)} euro",
    )
    console.print(Panel(riassunto, title="Metriche ricalcolate", border_style="blue"))

    if result.comparisons:
        console.print(_metrics_table(result))

    if result.violations:
        console.print(violations_table(result))
    else:
        console.print(
            Text(
                "Nessuna violazione rilevata. Il piano e' coerente con il modello "
                "dichiarato in SPEC, il che non dimostra che il modello sia giusto.",
                style="dim",
            )
        )


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def to_json(result: ValidationResult, indent: Optional[int] = 2) -> str:
    """Serializza l'esito per la API e per gli archivi degli esperimenti.

    `ensure_ascii=False` perche' i messaggi sono in italiano e vanno letti,
    non decodificati a mano.
    """
    return json.dumps(result.to_dict(), indent=indent, ensure_ascii=False)
