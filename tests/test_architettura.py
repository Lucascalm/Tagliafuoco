"""I confini architetturali come test, non come buone intenzioni.

La regola che il validatore non importa dal risolutore e' il cuore del
progetto: se decade, il validatore continua a girare, continua ad approvare
piani e smette di significare qualcosa. E' esattamente il tipo di regola che
decade in silenzio, perche' violarla non rompe niente il giorno in cui accade.

Per questo sta qui, dove una violazione fa fallire la suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verifica_architettura import (  # noqa: E402
    RULES,
    check,
    check_domain_purity,
    collect_imports,
)


def test_nessuna_violazione_dei_confini():
    """Tutte le regole di dipendenza dichiarate in SPEC.md sono rispettate."""
    violazioni = check(collect_imports())
    assert violazioni == [], "Confini architetturali violati:\n\n" + "\n\n".join(
        violazioni
    )


def test_il_validatore_non_importa_dal_risolutore():
    """La regola che non ha eccezioni, verificata da sola.

    E' ridondante rispetto al test precedente, ed e' voluto: se qualcuno
    indebolisse l'elenco delle regole, questo test continuerebbe a fallire e
    il messaggio direbbe esattamente cosa si e' rotto.
    """
    interni = collect_imports()
    colpevoli = [
        f"{i.module}:{i.lineno} -> {i.target}"
        for i in interni
        if i.module.split(".")[0] == "validate"
        and i.target.split(".")[0] == "optimize"
    ]
    assert colpevoli == [], (
        "Il validatore importa dal risolutore:\n  "
        + "\n  ".join(colpevoli)
        + "\n\nCosi' il validatore verifica il risolutore con il codice del "
        "risolutore, e un errore condiviso viene confermato invece che "
        "scoperto. Riscrivi il calcolo dentro validate/physics.py."
    )


def test_il_dominio_non_fa_input_output():
    """Il centro resta puro, salvo la deroga dichiarata per il registry."""
    violazioni = check_domain_purity()
    assert violazioni == [], "Il dominio fa input/output:\n\n" + "\n\n".join(
        violazioni
    )


def test_le_regole_sono_ben_formate():
    """Ogni regola deve spiegare perche' esiste, non solo cosa vieta.

    Un divieto senza motivazione viene rimosso dal primo che ci inciampa.
    """
    for rule in RULES:
        assert rule.source != rule.forbidden
        assert len(rule.motivo) > 40, f"regola {rule.source}->{rule.forbidden} senza motivo"
