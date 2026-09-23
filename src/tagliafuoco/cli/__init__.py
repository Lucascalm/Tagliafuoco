"""Riga di comando di Tagliafuoco.

Il pacchetto contiene un modulo solo, `main`, e non espone niente altro: i
comandi sono adattatori sottili sopra i casi d'uso, e un adattatore che cresce
fino a meritare un pacchetto e' un adattatore che ha cominciato a decidere.

L'oggetto Typer si importa da `tagliafuoco.cli.main`, non da qui: importarlo in
questo `__init__` farebbe caricare Typer, rich, il risolutore e tutto il resto
ogni volta che qualcuno tocca il pacchetto, anche per un `--help`.
"""

from __future__ import annotations
