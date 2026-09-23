"""Sottosistema di valutazione: la parte che rende il progetto difendibile.

Qui non si pianifica e non si valida: si MISURA. Il sottosistema risponde alle
tre domande di SPEC.md sezione 7, e per ciascuna dichiara il metodo prima del
risultato.

- `metrics`: le convenzioni di misura di un piano, scritte una volta sola.
  Tempo di risposta per focolaio, soglia di copertura, troncamento dell'acqua,
  metodo del percentile. Un numero prodotto qui e' leggibile solo insieme alla
  convenzione che lo definisce, quindi la convenzione viaggia con il numero.
- `comparison`: il confronto appaiato fra pianificatori sugli stessi scenari,
  con intervallo bootstrap e test di permutazione. Nessuna differenza viene
  riportata senza la sua incertezza.
- `nl_benchmark`: l'accuratezza del traduttore sul corpus annotato a mano, con
  allucinazioni e astensioni misurate separatamente.
- `ablation`: cosa aggiunge ciascun pezzo, tolto un pezzo per volta.
- `report`: il resoconto in italiano e la serializzazione, con l'avvertenza sui
  limiti in testa e non in fondo.

L'ordine degli import qui dentro e' anche l'ordine di dipendenza fra i moduli:
`metrics` non dipende da nessuno, `comparison` da `metrics`, gli altri da
entrambi. `ablation` importa il solver solo dentro le funzioni, cosi' chi usa
solo le metriche non paga il caricamento di ortools.
"""

from __future__ import annotations

from tagliafuoco.eval.comparison import (
    ComparisonResult,
    PairedDifference,
    ScenarioOutcome,
    bootstrap_mean_ci,
    compare_planners,
    paired_difference,
    permutation_sign_test,
    wilson_interval,
)
from tagliafuoco.eval.metrics import (
    COVERAGE_WATER_RATIO,
    MetricsDetail,
    compute_detail,
    compute_metrics,
    percentile,
)
from tagliafuoco.eval.nl_benchmark import (
    BenchmarkResult,
    compare_translators,
    load_corpus,
    run_benchmark,
)

__all__ = [
    "BenchmarkResult",
    "COVERAGE_WATER_RATIO",
    "ComparisonResult",
    "MetricsDetail",
    "PairedDifference",
    "ScenarioOutcome",
    "bootstrap_mean_ci",
    "compare_planners",
    "compare_translators",
    "compute_detail",
    "compute_metrics",
    "load_corpus",
    "paired_difference",
    "percentile",
    "permutation_sign_test",
    "run_benchmark",
    "wilson_interval",
]
