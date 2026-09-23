# Demo di Tagliafuoco: piano di design

Questo documento va letto prima di scrivere una riga di HTML o di CSS. Fissa le
scelte visive e le motiva. Chi implementa la demo non reinventa la palette e non
sceglie i font a sentimento: applica quanto sta scritto qui.

## Principio guida

La mappa e' lo strumento, non la decorazione. La pagina imita una carta
topografica operativa appoggiata sul tavolo di una sala: fondo di carta,
inchiostro scuro, segni che trasportano informazione e nient'altro. Ogni
elemento che non aiuta a capire dove brucia, cosa si sta muovendo e perche',
non entra.

Il secondo principio e' l'onesta'. La demo mostra una simulazione. Lo dice
subito, in alto, non in una riga di piede.

## Palette

Sei colori, nessuno scelto per abitudine. Il riferimento e' la cartografia
topografica italiana e i colori reali del soggetto: la carta, il bosco, la
brace, la terra bruciata.

| Nome | Hex | Uso |
|---|---|---|
| `carta` | `#EFE9DC` | fondo della pagina, avorio di carta stampata |
| `inchiostro` | `#17150F` | testo principale, nero caldo da stampa |
| `bosco` | `#2E4636` | aree boscate, stato coperto, conferme |
| `brace` | `#A8331D` | focolai attivi, terracotta bruciata |
| `ocra` | `#B3801F` | interfaccia urbano-foresta, allerta intermedia |
| `ardesia` | `#55606B` | mezzi, griglia, linee, testo secondario |

Note vincolanti. Il fondo non e' bianco puro. Il rosso dei focolai non e' un
rosso di segnalazione saturo: e' il colore della terra bruciata, cosi' che una
mappa con trenta focolai resti leggibile invece di vibrare. Non ci sono
gradienti multicolore, non ci sono aloni radiali sfocati, non c'e' viola.

Variante notturna. Il turno di notte e' parte del dominio (i mezzi aerei si
fermano, quelli a terra no), quindi una variante scura e' giustificata e non
decorativa: fondo `#14171A`, inchiostro `#E3DED2`, gli altri colori schiariti
di un passo per tenere il contrasto. Si attiva con `prefers-color-scheme` e con
un interruttore esplicito, perche' in sala la luce non segue le preferenze del
sistema operativo.

Contrasto: ogni coppia testo su fondo deve superare 4.5 a 1. Verificalo, non
darlo per scontato.

## Tipografia

Due famiglie, entrambe motivate dal soggetto.

**Archivo** per l'interfaccia, i titoli e le etichette sulla mappa. E' una
grotesca costruita per lavorare piccola e densa, con un taglio stretto
disponibile: le etichette su una carta devono stare strette per non coprire il
territorio che descrivono. La gerarchia si costruisce con il peso e con la
larghezza, non con il colore.

**IBM Plex Mono** per i numeri: orari, minuti di risposta, litri, coordinate,
identificativi dei mezzi. Ha le cifre a larghezza fissa, quindi una colonna di
tempi resta allineata mentre il timelapse li aggiorna. Un numero che salta di
mezzo carattere a ogni fotogramma e' illeggibile.

Divieti: niente Inter, Geist, Space Grotesk. Niente em dash nel testo. Niente
etichetta in maiuscolo sopra le sezioni. Niente parola singola evidenziata nei
titoli. Righe di testo sotto gli 80 caratteri.

## Layout

Una sola schermata, senza scorrimento della pagina. Non e' una landing page:
e' una console.

```
+--------------------------------------------------------------+
| Tagliafuoco   simulatore, non strumento operativo   [notte]   |
+-----------------------------------------------+--------------+
|                                               | ORDINE       |
|                                               | campo di     |
|                                               | testo        |
|                                               |--------------|
|               mappa della Calabria            | VINCOLI      |
|               focolai, mezzi, rotte           | estratti dal |
|                                               | traduttore   |
|                                               |--------------|
|                                               | PIANO        |
|                                               | assegnazioni |
|                                               |--------------|
|                                               | VALIDATORE   |
|                                               | esito        |
+-----------------------------------------------+--------------+
| [play] 12 lug 2025, 14:00   |....|||.||||....|  densita'      |
+--------------------------------------------------------------+
```

La colonna destra e' larga circa 380 px e si chiama registro operativo. Segue
l'ordine in cui accadono le cose: prima l'ordine del coordinatore, poi i vincoli
che ne sono stati estratti, poi il piano, poi il verdetto del validatore. Questa
sequenza e' reale, quindi qui la numerazione progressiva sarebbe legittima; i
vincoli estratti invece non sono una sequenza, quindi vanno etichettati con il
loro tipo e non numerati.

La barra in basso e' il comando del timelapse. Lo scorrevole non e' una barra
vuota: porta l'istogramma dei rilevamenti nel tempo, cosi' si vede subito dove
sta il picco della stagione prima ancora di premere play. E' un elemento
strutturale che trasporta informazione, ed e' il motivo per cui esiste.

Divieti di layout: niente bento grid, niente tre riquadri in fila, niente card
identiche, niente striscia colorata a sinistra, niente griglia di puntini sullo
sfondo, niente ombre diffuse, niente vetro liquido, niente finta finestra di
terminale.

Separazioni: linee da 1 px in `ardesia` al 25 per cento, come i tratti di una
carta. Nessuna ombra.

Raggi degli angoli: 0 per i pannelli, che sono riquadri di carta; 2 px per i
comandi, che sono oggetti da premere; cerchi pieni per i segnaposto sulla mappa.
Il raggio segue la gerarchia, non un valore unico per decreto.

## Segni sulla mappa

I segnaposto codificano dati, non gusto.

- Focolaio: cerchio pieno in `brace`. Il raggio cresce con la radice quadrata
  della potenza radiante, non linearmente, perche' l'occhio confronta le aree.
  Il bordo diventa `ocra` quando il focolaio e' in interfaccia urbano-foresta.
  Trasparenza legata all'eta' del rilevamento: quelli vecchi sbiadiscono.
- Base: quadrato vuoto in `ardesia`, con l'etichetta del nome.
- Mezzo: triangolo per i mezzi aerei orientato nella direzione di marcia,
  quadratino per i mezzi a terra. Riempito quando e' in missione, vuoto quando
  e' fermo, contorno tratteggiato quando e' tenuto di riserva.
- Rotta: linea sottile in `ardesia` fra base e focolaio, con il tratto continuo
  per il percorso gia' fatto e tratteggiato per quello previsto.
- Area protetta: campitura leggerissima in `bosco` al 10 per cento, senza bordo
  marcato, perche' e' contesto e non deve competere con i focolai.

Una legenda fissa e' obbligatoria. Una mappa senza legenda non e' leggibile.

## Movimento

Un solo momento di movimento orchestrato: il timelapse della stagione. Tutto il
resto risponde a un'azione dell'utente e dura meno di 150 ms.

Niente animazione al passaggio del mouse su ogni elemento. Niente comparsa in
dissolvenza delle sezioni allo scorrimento. Durante il caricamento dei dati della
stagione si usano riquadri scheletro al posto del contenuto, non una rotellina.

Il rispetto di `prefers-reduced-motion` non e' facoltativo: con quella
preferenza attiva il timelapse avanza a scatti fra le finestre invece di
interpolare, e resta comunque usabile.

## Copy

Si descrive cosa fa il sistema. Non lo si vende.

Divieti: niente costruzione "non e' X, e' Y", niente elenchi con la spunta,
niente numeri gonfiati, niente loghi o testimonianze. I nomi delle azioni restano
gli stessi in tutto il flusso: se il pulsante dice "Pianifica", lo stato dopo
dice "Pianificato".

L'avvertenza va in alto, accanto al nome, con lo stesso peso tipografico del
resto dell'intestazione: "simulatore, non strumento operativo". Non e' una nota
legale nascosta, e' una informazione sul prodotto.

Servono anche le pagine che un sito vero ha, almeno come stub dichiarato: una
nota sulle fonti dei dati e sui limiti della simulazione, e una nota su cosa la
pagina memorizza nel browser (se memorizza qualcosa).

## Dati veri

La demo mostra i dati che il progetto produce davvero: i focolai raggruppati
dalla catena di ingest, i piani prodotti dal risolutore, il verdetto del
validatore. Niente segnaposto decorativi, niente numeri inventati per riempire i
riquadri. Se un dato non c'e', il riquadro dice che non c'e'.

## Verifica finale

Prima di considerare finita la pagina, rileggi questo piano e chiediti se lo
avresti scritto identico per un progetto qualsiasi. Se la risposta e' si', hai
sbagliato qualcosa.

Poi togli una cosa: l'elemento piu' decorativo fra quelli rimasti, quello che
non serve ne' a capire ne' a usare la pagina. Scrivi nel resoconto che cosa hai
tolto.
