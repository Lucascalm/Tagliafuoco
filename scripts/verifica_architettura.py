"""Verifica automatica dei confini architetturali.

Le regole di architettura che si controllano solo a occhio decadono. Questo
script le rende eseguibili: analizza l'albero sintattico di ogni modulo del
pacchetto, estrae le importazioni interne e controlla che rispettino le
dipendenze dichiarate in SPEC.md. Gira nei test e nella pipeline di
integrazione continua, quindi una violazione fa fallire la build invece di
restare in attesa che qualcuno se ne accorga.

La regola piu' importante e' la prima: `validate` non puo' importare da
`optimize`. Se la si viola, il validatore verifica il risolutore con il codice
del risolutore, e un errore condiviso viene confermato invece che scoperto.
Tutto il valore del progetto dipende da quella riga.

Uso:
    python scripts/verifica_architettura.py
    python scripts/verifica_architettura.py --verbose
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "tagliafuoco"
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / PACKAGE


# --------------------------------------------------------------------------
# Le regole
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """Un divieto di importazione fra due sottopacchetti.

    `motivo` non e' documentazione ornamentale: e' il messaggio che legge chi
    fa fallire la build, e deve bastargli per capire perche' la regola esiste
    senza andare a cercare la specifica.
    """

    source: str
    forbidden: str
    motivo: str
    exceptions: tuple[str, ...] = ()


RULES: tuple[Rule, ...] = (
    Rule(
        source="validate",
        forbidden="optimize",
        motivo=(
            "Il validatore deve essere una reimplementazione indipendente. Se "
            "importa dal risolutore, verifica il risolutore con il codice del "
            "risolutore: un errore condiviso viene confermato invece che "
            "scoperto. Riscrivi il calcolo in validate/physics.py."
        ),
    ),
    Rule(
        source="domain",
        forbidden="optimize",
        motivo=(
            "Il dominio sta al centro e non deve conoscere chi lo usa. Se "
            "dipendesse dal risolutore, cambiare risolutore significherebbe "
            "toccare le entita', e con esse tutto il resto."
        ),
    ),
    Rule(
        source="domain",
        forbidden="validate",
        motivo="Il dominio sta al centro e non deve conoscere chi lo usa.",
    ),
    Rule(
        source="domain",
        forbidden="ingest",
        motivo="Il dominio sta al centro e non deve conoscere chi lo usa.",
    ),
    Rule(
        source="domain",
        forbidden="nlu",
        motivo="Il dominio sta al centro e non deve conoscere chi lo usa.",
    ),
    Rule(
        source="domain",
        forbidden="api",
        motivo="Il dominio sta al centro e non deve conoscere gli adattatori.",
    ),
    Rule(
        source="optimize",
        forbidden="validate",
        motivo=(
            "La dipendenza e' vietata in entrambi i versi: il risolutore non "
            "deve poter adattare il proprio output a cio' che il validatore "
            "controlla, altrimenti il controllo diventa circolare."
        ),
    ),
    Rule(
        source="optimize",
        forbidden="api",
        motivo="Un caso d'uso non deve dipendere da un adattatore.",
    ),
    Rule(
        source="optimize",
        forbidden="cli",
        motivo="Un caso d'uso non deve dipendere da un adattatore.",
    ),
    Rule(
        source="validate",
        forbidden="api",
        motivo="Un caso d'uso non deve dipendere da un adattatore.",
    ),
    Rule(
        source="ingest",
        forbidden="optimize",
        motivo=(
            "L'acquisizione dei dati non deve sapere come verranno usati: "
            "deve produrre uno Scenario e fermarsi li'."
        ),
    ),
    Rule(
        source="ingest",
        forbidden="api",
        motivo="Un caso d'uso non deve dipendere da un adattatore.",
    ),
    Rule(
        source="nlu",
        forbidden="optimize",
        motivo=(
            "Il traduttore non deve conoscere il risolutore. Se lo conoscesse, "
            "prima o poi qualcuno gli farebbe produrre qualcosa di piu' di un "
            "ConstraintSet, e il confine tipizzato smetterebbe di esistere."
        ),
    ),
    Rule(
        source="nlu",
        forbidden="validate",
        motivo="Il traduttore non deve conoscere chi verifica i piani.",
    ),
)

# Moduli del dominio che possono toccare il disco. Registry legge i JSON
# statici: e' un accesso al filesystem dentro il centro, ed e' una deroga
# dichiarata in docs/11-architettura-del-software.md.
DOMAIN_IO_ALLOWLIST = {"domain.registry"}


# --------------------------------------------------------------------------
# Analisi
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Import:
    module: str          # modulo che importa, es. "validate.physics"
    target: str          # modulo importato, es. "optimize.geometry"
    lineno: int


def _module_name(path: Path) -> str:
    """Dal percorso del file al nome del modulo relativo al pacchetto."""
    rel = path.relative_to(SRC).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _resolve_relative(module: str, node: ast.ImportFrom) -> str:
    """Risolve un import relativo (from ..domain import models) in assoluto.

    Il livello 1 significa "il pacchetto che contiene questo modulo", quindi
    si risalgono `level - 1` segmenti oltre al modulo stesso.
    """
    parts = module.split(".")
    base = parts[: max(0, len(parts) - node.level)]
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)


def collect_imports(verbose: bool = False) -> list[Import]:
    """Estrae tutte le importazioni interne al pacchetto.

    Si usa l'albero sintattico invece di una ricerca testuale perche' un grep
    non distingue un import vero da una stringa o da un commento, e perche'
    deve risolvere gli import relativi.
    """
    out: list[Import] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover
            print(f"ATTENZIONE: {path} non e' analizzabile: {exc}")
            continue

        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(PACKAGE + "."):
                        out.append(
                            Import(module, alias.name[len(PACKAGE) + 1 :], node.lineno)
                        )
                continue
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    target = _resolve_relative(module, node)
                elif node.module and node.module.startswith(PACKAGE + "."):
                    target = node.module[len(PACKAGE) + 1 :]
                elif node.module == PACKAGE:
                    target = ""
            if target:
                out.append(Import(module, target, node.lineno))

    if verbose:
        for imp in out:
            print(f"  {imp.module}:{imp.lineno} -> {imp.target}")
    return out


def _top(module: str) -> str:
    return module.split(".")[0]


def check(imports: list[Import]) -> list[str]:
    """Applica le regole e restituisce i messaggi di violazione."""
    errors: list[str] = []
    for imp in imports:
        src_pkg = _top(imp.module)
        dst_pkg = _top(imp.target)
        for rule in RULES:
            if src_pkg != rule.source or dst_pkg != rule.forbidden:
                continue
            if imp.module in rule.exceptions:
                continue
            errors.append(
                f"{imp.module} (riga {imp.lineno}) importa {imp.target}\n"
                f"    Vietato: {rule.source} non puo' dipendere da {rule.forbidden}.\n"
                f"    {rule.motivo}"
            )
    return errors


def check_domain_purity() -> list[str]:
    """Il centro non deve fare input/output, salvo la deroga dichiarata."""
    errors: list[str] = []
    io_modules = {"json", "csv", "pathlib", "httpx", "requests", "os", "open"}
    for path in sorted((SRC / "domain").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(path)
        if module in DOMAIN_IO_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in io_modules:
                    errors.append(
                        f"{module} (riga {node.lineno}) importa {n}\n"
                        f"    Il dominio non deve fare input/output. Se serve "
                        f"davvero, aggiungi il modulo a DOMAIN_IO_ALLOWLIST "
                        f"dichiarando perche'."
                    )
    return errors


def run(verbose: bool = False) -> int:
    if not SRC.exists():
        print(f"Pacchetto non trovato in {SRC}")
        return 2

    imports = collect_imports(verbose=verbose)
    errors = check(imports) + check_domain_purity()

    interne = len(imports)
    print(f"Analizzate {interne} importazioni interne al pacchetto.")
    print(f"Regole verificate: {len(RULES)} divieti fra sottopacchetti, "
          f"piu' la purezza del dominio.")

    if errors:
        print()
        print(f"VIOLAZIONI: {len(errors)}")
        for e in errors:
            print()
            print(f"  {e}")
        print()
        print("I confini stanno in SPEC.md sezione 5 e in "
              "docs/11-architettura-del-software.md.")
        return 1

    print()
    print("Nessuna violazione. In particolare, validate non dipende da optimize: "
          "il validatore resta una reimplementazione indipendente.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="elenca tutte le importazioni interne trovate",
    )
    args = parser.parse_args()
    return run(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
