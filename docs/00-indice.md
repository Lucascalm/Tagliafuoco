# La teoria dietro Tagliafuoco

Questa cartella non e' la documentazione d'uso del progetto: quella sta nel
README. Qui c'e' un capitolo per ciascun argomento che serve per capire il
codice e per giustificare le scelte fatte, dal telerilevamento alla
programmazione a vincoli.

Ogni capitolo segue la stessa struttura. Parte da un problema concreto del
progetto, introduce la teoria che lo risolve, torna a mostrare come il progetto
la usa, e si chiude con due sezioni fisse: le domande ricorrenti con la
risposta, e le indicazioni per approfondire.

---

## Come leggerli

Per capire il progetto, leggili in ordine: seguono il percorso del dato, dal
satellite al piano validato.

Se hai poco tempo, i tre che contengono le decisioni piu' importanti sono il 05
(come funziona davvero un risolutore a vincoli), l'08 (perche' il modello
linguistico non decide) e il 09 (perche' esiste un secondo programma che dice di
no).

## Il dominio

**[01. Incendi boschivi e lotta attiva](01-incendi-boschivi-e-aib.md)**
Come si comporta un incendio boschivo, cosa cambia fra un lancio dall'alto e una
squadra a terra, come e' organizzata la lotta attiva in Italia e in Calabria, e
cosa significa interfaccia urbano-foresta. Da qui vengono le cifre che motivano il
progetto.

**[02. Telerilevamento e NASA FIRMS](02-telerilevamento-e-nasa-firms.md)**
Come fa un satellite a vedere un incendio: corpo nero, legge di Wien, finestre
atmosferiche, rilevamento per contrasto con lo sfondo. Cos'e' la potenza radiante
del fuoco, che differenza c'e' fra MODIS e VIIRS, perche' la latenza di tre ore
decide che tipo di strumento puoi costruire, e cosa da FIRMS non si puo' sapere.

## Dal dato grezzo allo scenario

**[03. Clustering spaziotemporale](03-clustering-spaziotemporale.md)**
Come si passa dai pixel caldi ai focolai. DBSCAN per intero, perche' non k-means,
come si sceglie eps, perche' il tempo va trattato a parte dallo spazio, e perche'
il centroide e' pesato sulla potenza radiante.

**[12. Geodesia e calcolo spaziale](12-geodesia-e-calcolo-spaziale.md)**
Calcolare distanze sulla Terra senza sbagliare. WGS84, perche' latitudine e
longitudine non sono un piano cartesiano, la formula dell'haversine e perche'
esiste, le proiezioni e cosa deformano, gli indici spaziali, il sistema di tile
delle mappe web.

## La decisione

**[04. Fondamenti di ricerca operativa](04-ricerca-operativa-fondamenti.md)**
Che cos'e' un problema di ottimizzazione, perche' questo non e' un semplice
problema di assegnamento, cosa significa NP-difficile e cosa non significa,
programmazione lineare intera, branch and bound, euristiche, e perche' una regola
golosa fallisce.

**[05. Constraint programming e CP-SAT](05-constraint-programming-e-cpsat.md)**
Come funziona un risolutore a vincoli: domini, propagatori, consistenza, vincoli
globali. Cosa fa CP-SAT sotto il cofano, perche' lavora solo su interi, cosa
significa ciascuno stato di uscita, e perche' il determinismo si paga.

**[06. Il modello matematico di Tagliafuoco](06-il-modello-matematico.md)**
Le variabili, i vincoli e la funzione obiettivo effettivamente scritti nel
progetto, con la formulazione per esteso e la spiegazione di ogni scelta di
modellazione.

## Il linguaggio

**[07. Modelli linguistici e output strutturato](07-modelli-linguistici-e-output-strutturato.md)**
Come si fa produrre a un modello linguistico un dato che un programma puo'
usare. Le tre strategie in ordine di robustezza, cosa garantisce la chiamata a
strumenti e cosa no, come si valuta un traduttore.

**[08. Perche' il modello non decide](08-perche-il-modello-non-decide.md)**
La scelta di progetto piu' importante. I quattro modi in cui oggi si mette un
modello linguistico dentro un sistema di ottimizzazione, il dato che motiva la
scelta fatta qui, e una sezione di autocritica sui limiti di questo approccio.

## Il controllo

**[09. Validazione indipendente](09-validazione-indipendente.md)**
Perche' esiste un secondo programma che ricalcola tutto e dice di no. La
differenza fra un test e un validatore, il problema dell'oracolo, la
programmazione a N versioni e i suoi limiti sperimentali, la tolleranza numerica.

**[14. Testing e determinismo](14-testing-e-determinismo.md)**
Come si prova che il sistema funziona quando non conosci l'output giusto. Test di
proprieta', test metamorfici, le trappole del non determinismo in Python, cosa
misura davvero la copertura.

## La prova

**[10. Metodologia di valutazione](10-metodologia-di-valutazione.md)**
Come si dimostra che il sistema serve a qualcosa. La scelta di una baseline
onesta, la rigiocata della stagione e i suoi limiti, la fuga di informazione dal
futuro, le metriche e come si possono ingannare, la significativita', e come non
barare.

## Il software

**[11. L'architettura del software](11-architettura-del-software.md)**
Perche' i moduli sono divisi cosi', quali dipendenze sono ammesse, la regola dura
che il validatore non importa dal risolutore, perche' dataclass per il dominio e
Pydantic per i vincoli, e cosa cambierei se il progetto crescesse.

**[13. La mappa e il frontend](13-la-mappa-e-il-frontend.md)**
Come si disegna una mappa interattiva nel browser, la differenza fra SVG e
canvas quando i punti diventano molti, come si fa un timelapse che non salta, e
le scelte visive della demo con le loro motivazioni.

## Riferimento

**[15. Glossario](15-glossario.md)**
Ogni termine del progetto definito una volta sola, in ordine alfabetico.
