"""Adattatore HTTP di Tagliafuoco.

Il pacchetto contiene tre cose e nient'altro: i modelli di trasporto
(`schemas`), le rotte (`routes`) e il montaggio dell'applicazione (`app`).
Nessuna logica di dominio: chi cerca il modello fisico lo trova in `optimize/`
e, riscritto in modo indipendente, in `validate/`.

La versione sta qui e non in `config.py` perche' `config.py` appartiene a un
altro sottosistema e perche' la versione che interessa a un client e' quella
del pacchetto installato. Si legge dai metadati quando il pacchetto e'
installato; quando si lavora dal sorgente con il layout `src` quei metadati non
ci sono, e allora vale il numero dichiarato qui, tenuto allineato a mano con
`pyproject.toml`.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

#: Versione di ripiego quando il pacchetto non risulta installato.
FALLBACK_VERSION = "0.1.0"

try:
    __version__: str = _package_version("tagliafuoco")
except PackageNotFoundError:  # esecuzione dal sorgente, senza installazione
    __version__ = FALLBACK_VERSION

__all__ = ["FALLBACK_VERSION", "__version__"]
