# Glossario

Ogni termine che compare nel progetto, spiegato una volta sola e in modo
autosufficiente. Le voci sono in ordine alfabetico. Dove una voce ha un capitolo
dedicato, il capitolo e' indicato alla fine della definizione.

---

**Ablazione (ablation study).** Esperimento in cui si toglie un pezzo del sistema
e si rimisura, per capire quanto quel pezzo contribuiva. Dice una cosa diversa
dal confronto con la baseline: la baseline dice se l'insieme e' migliore di non
avere niente, l'ablazione dice quale pezzo porta il miglioramento. Vedi
`10-metodologia-di-valutazione.md`.

**AIB.** Antincendio boschivo. In Italia indica sia l'attivita' sia i mezzi e le
squadre dedicati. Un "modulo AIB" e' tipicamente un fuoristrada con una cisterna
da qualche centinaio di litri, una motopompa e una squadra.

**Allucinazione.** Output di un modello linguistico che e' formalmente corretto e
sostanzialmente falso. Nel progetto il caso temuto e' il vincolo inventato: il
coordinatore non ha mai chiesto di dare priorita' a un comune, e il traduttore lo
emette lo stesso. E' peggio di un'omissione, perche' un vincolo assente si nota e
uno di troppo no.

**Arc consistency (consistenza d'arco).** Proprieta' di un vincolo binario per
cui, per ogni valore rimasto nel dominio di una variabile, esiste almeno un
valore compatibile nel dominio dell'altra. I propagatori di un risolutore a
vincoli lavorano per raggiungerla, eliminando i valori che non possono far parte
di nessuna soluzione. Vedi `05-constraint-programming-e-cpsat.md`.

**Assignment problem.** Problema classico di ricerca operativa: assegnare n
lavoratori a n compiti minimizzando il costo totale, un compito per lavoratore.
Si risolve in tempo polinomiale con l'algoritmo ungherese. Il problema di
Tagliafuoco non e' questo, perche' un mezzo puo' servire piu' focolai, i tempi
contano e le capacita' sono limitate. Vedi `04-ricerca-operativa-fondamenti.md`.

**Backtesting.** Valutare una strategia rigiocandola su dati storici. Il rischio
principale e' la fuga di informazione dal futuro. Il termine viene dalla finanza,
il problema e' identico a quello della rigiocata della stagione 2025.

**Baseline.** Il metodo semplice con cui si confronta il metodo proposto. Deve
essere cio' che si farebbe davvero senza il sistema, non un fantoccio costruito
per perdere. Tagliafuoco ne usa due: "manda il mezzo libero piu' vicino" ordinando
i focolai per ora di rilevamento, e la stessa regola ordinando per priorita'.

**Bounding box.** Rettangolo definito da latitudine e longitudine minima e
massima. Quella della Calabria usata nel progetto e' (15.55, 37.85, 17.25, 40.20)
nel formato longitudine e latitudine. Serve a filtrare i rilevamenti e a
interrogare la API di FIRMS.

**Branch and bound.** Tecnica esatta per l'ottimizzazione combinatoria: si divide
lo spazio delle soluzioni in sottoinsiemi (branch) e si scarta un sottoinsieme
quando si dimostra che non puo' contenere una soluzione migliore di quella gia'
trovata (bound). Il confine si ottiene di solito rilassando i vincoli di
interezza.

**Brightness temperature (temperatura di brillanza).** Temperatura che avrebbe un
corpo nero perfetto per emettere la radianza misurata dal sensore a una certa
lunghezza d'onda. Non e' la temperatura reale della superficie, e nel caso di un
pixel parzialmente in fiamme e' molto piu' bassa della temperatura della fiamma.
Vedi `02-telerilevamento-e-nasa-firms.md`.

**Canadair.** Nome comune degli aerei anfibi antincendio della serie CL-215 e
CL-415. Pescano acqua in mare o in lago planando sulla superficie, trasportano
circa 6000 litri e volano a circa 330 km/h. Non spengono l'incendio: raffreddano
e rallentano il fronte.

**CDCL (conflict driven clause learning).** Tecnica dei risolutori SAT moderni:
quando la ricerca arriva a una contraddizione, si analizza la causa e si aggiunge
una clausola che impedisce di ripetere quella combinazione di scelte. E' uno dei
motivi per cui CP-SAT e' efficace.

**Clustering.** Raggruppare elementi simili senza sapere in anticipo quali siano i
gruppi. Nel progetto serve a passare dai pixel caldi ai focolai. Vedi
`03-clustering-spaziotemporale.md`.

**Confidence (nei prodotti FIRMS).** Indicatore di quanto l'algoritmo di
rilevamento sia sicuro che il pixel sia davvero un incendio. MODIS la esprime come
numero da 0 a 100, VIIRS come categoria (low, nominal, high). Il progetto le
normalizza sulla stessa scala tramite `CONFIDENCE_MAP` in `config.py`.

**ConstraintSet.** L'oggetto che il traduttore produce e l'unico che attraversa il
confine fra il modello linguistico e il resto del sistema. Contiene una lista di
vincoli tipizzati, l'identificativo, il testo originale e i metadati della
traduzione. Definito in `src/tagliafuoco/nlu/schema.py`.

**CORINE Land Cover.** Programma europeo di classificazione della copertura del
suolo. Il progetto non lo usa davvero: deriva la copertura da quota e provincia
con una regola dichiarata, che e' un surrogato esplicito.

**CP-SAT.** Il risolutore a vincoli di OR-Tools. Traduce il modello in forma SAT e
usa lazy clause generation, apprendimento delle clausole di conflitto, presolve e
ricerca a portafoglio. Lavora solo su interi. Vedi
`05-constraint-programming-e-cpsat.md`.

**Cumulative (vincolo).** Vincolo globale che impone che, in ogni istante, la
somma delle risorse richieste dalle attivita' in corso non superi la capacita'
disponibile. E' il vincolo giusto quando piu' attivita' possono sovrapporsi fino a
un limite.

**DBSCAN.** Algoritmo di clustering basato sulla densita', con due parametri: eps
(il raggio di vicinato) e minPts (quanti punti servono perche' un punto sia
centrale). Non richiede di sapere quanti gruppi ci sono e trova gruppi di forma
qualsiasi, il che e' esattamente cio' che serve per un fronte di fiamma allungato.
Vedi `03-clustering-spaziotemporale.md`.

**Determinismo.** Proprieta' per cui lo stesso input produce sempre lo stesso
output. Nel progetto e' un requisito di correttezza della valutazione, non una
comodita'. Vedi `14-testing-e-determinismo.md`.

**Difesa in profondita'.** Principio per cui si mettono piu' controlli
indipendenti in serie, ciascuno progettato assumendo che i precedenti possano
fallire. In Tagliafuoco gli strati sono: schema tipizzato, validazione Pydantic,
vincoli duri nel risolutore, validatore indipendente, ispezione umana dei vincoli
estratti.

**Endurance (autonomia).** Tempo massimo di missione continuativa di un mezzo,
limitato dal carburante. Va distinta dal limite di turno dell'equipaggio, che e'
piu' lungo e si consuma su tutta la giornata.

**Enforcement (duro o morbido).** Un vincolo duro rende infattibile il piano che
lo viola. Un vincolo morbido entra nella funzione obiettivo come penalita'. La
distinzione esiste nel linguaggio del coordinatore ("l'elicottero resta di
riserva" contro "se possibile tenetene uno libero") e il traduttore deve
riconoscerla.

**eps.** Il raggio di vicinato di DBSCAN. Nel progetto vale 1.5 km, circa quattro
volte il lato di un pixel VIIRS. E' una taratura dichiarata.

**Fattibilita' (soluzione ammissibile).** Una soluzione che rispetta tutti i
vincoli, indipendentemente da quanto sia buona. Un problema e' infattibile quando
non ne esiste nessuna.

**FIRMS.** Fire Information for Resource Management System, il servizio della NASA
che distribuisce i rilevamenti di incendi attivi da satellite. Richiede una chiave
gratuita e fornisce i dati near real time con circa tre ore di latenza. Vedi
`02-telerilevamento-e-nasa-firms.md`.

**Few-shot.** Tecnica di prompting in cui si mostrano al modello alcuni esempi
completi del compito prima di chiedergli il caso nuovo. Nel progetto gli esempi
servono soprattutto a insegnare l'astensione, non a insegnare i casi facili.

**FRP (Fire Radiative Power).** Potenza radiante emessa dal fuoco, misurata in
megawatt. E' proporzionale al tasso di consumo di biomassa, quindi e' la migliore
misura di intensita' disponibile da satellite. Il progetto la usa come componente
principale della priorita'.

**Fuga di informazione dal futuro (data leakage).** Errore metodologico per cui la
valutazione usa informazione che al momento della decisione non era disponibile.
Nel progetto si evita costruendo ogni finestra con i soli rilevamenti acquisiti
fino a quell'istante.

**Gap di ottimalita'.** Differenza relativa fra la migliore soluzione trovata e il
miglior limite dimostrato. Un gap dell'uno per cento significa che la soluzione e'
al massimo dell'uno per cento peggiore dell'ottimo, anche se l'ottimo non e' stato
trovato.

**Greedy (algoritmo goloso).** Strategia che a ogni passo prende la scelta
migliore sul momento senza considerare le conseguenze future. La baseline del
progetto e' un algoritmo goloso, e il suo limite e' esattamente quello: mandare
il mezzo piu' vicino al primo focolaio puo' rendere impossibile coprire il secondo
e piu' grave.

**Haversine.** Formula per la distanza sulla superficie di una sfera, numericamente
stabile anche per distanze piccole. Il nome viene dalla funzione
"half versed sine". Vedi `12-geodesia-e-calcolo-spaziale.md`.

**Interfaccia urbano-foresta.** Vedi WUI.

**Lazy clause generation.** Tecnica ibrida fra programmazione a vincoli e SAT: le
clausole booleane che spiegano le deduzioni dei propagatori vengono generate solo
quando servono a risolvere un conflitto, invece che tutte in anticipo. E' il
meccanismo centrale di CP-SAT.

**LNS (large neighborhood search).** Metaeuristica che parte da una soluzione,
ne distrugge una porzione e la ricostruisce cercando di migliorarla. CP-SAT la usa
internamente insieme alla ricerca esatta.

**MDVRPTW.** Multi-depot vehicle routing problem with time windows: instradamento
di veicoli con piu' depositi e finestre temporali. E' la famiglia di problemi a cui
il problema di Tagliafuoco assomiglia di piu'.

**MODIS.** Sensore a bordo dei satelliti Terra e Aqua. Pixel di circa 1 km nei
canali usati per il rilevamento degli incendi. Serie storica lunga, minore
sensibilita' ai fuochi piccoli rispetto a VIIRS.

**NoOverlap.** Vincolo globale che impone che un insieme di intervalli non si
sovrapponga nel tempo. Nel progetto impone che un mezzo non sia impegnato in due
missioni contemporaneamente.

**NP-difficile.** Classe di problemi almeno difficili quanto i piu' difficili in
NP. In pratica significa che nessuno conosce un algoritmo esatto che li risolva in
tempo polinomiale, quindi o si accettano tempi che esplodono al crescere
dell'istanza, o si accettano soluzioni senza garanzia di ottimalita'. Non significa
che le istanze reali siano irrisolvibili. Vedi
`04-ricerca-operativa-fondamenti.md`.

**NRT (near real time).** I prodotti FIRMS distribuiti con poche ore di latenza,
prima della rielaborazione definitiva. I prodotti standard di archivio arrivano
dopo giorni ma sono rielaborati con geolocalizzazione migliore.

**Oracolo (problema dell').** In test di software, la difficolta' di sapere quale
sia l'output corretto quando calcolarlo era proprio il punto del programma. Vedi
`14-testing-e-determinismo.md`.

**OptionalIntervalVar.** Variabile intervallo opzionale di CP-SAT: ha inizio,
durata, fine e un letterale di presenza. Se il letterale e' falso, l'intervallo
non esiste e i vincoli che lo coinvolgono lo ignorano. E' il costrutto con cui si
modella "questo mezzo potrebbe fare questa missione, oppure no".

**Presolve.** Fase di semplificazione automatica del modello prima della ricerca:
elimina variabili ridondanti, stringe i domini, riconosce strutture. Spesso riduce
il modello in modo drastico.

**Programmazione a N versioni.** Scrivere piu' implementazioni indipendenti della
stessa specifica e confrontarne i risultati. La sperimentazione ha mostrato che le
implementazioni non sbagliano in modo indipendente quanto si sperava, perche' i
programmatori condividono intuizioni sbagliate sugli stessi casi difficili. Vedi
`09-validazione-indipendente.md`.

**Propagazione (dei vincoli).** Il processo con cui un risolutore a vincoli
restringe i domini delle variabili eliminando i valori che non possono far parte
di nessuna soluzione, ripetendo finche' non cambia piu' niente.

**Pydantic.** Libreria Python per la validazione di dati a partire dalle
annotazioni di tipo. Il progetto la usa per i vincoli, dove il dato arriva da un
componente non affidabile, e non per le entita' di dominio. Vedi
`11-architettura-del-software.md`.

**Rilassamento continuo.** Versione di un problema intero in cui si permette alle
variabili di assumere valori frazionari. Si risolve molto piu' facilmente e
fornisce un limite al valore ottimo del problema originale.

**Selector.** Nello schema dei vincoli, il modo di descrivere un insieme di mezzi o
di focolai per attributo invece che per elenco. Il coordinatore dice "gli
elicotteri", non le matricole, e il selettore conserva quella forma finche' non
viene risolto contro uno scenario concreto.

**Set covering.** Problema di scegliere il minor numero di insiemi che coprano
tutti gli elementi. E' uno dei problemi NP-difficili classici e una delle
strutture presenti nel modello di Tagliafuoco.

**Source span.** Il frammento letterale dell'ordine da cui un vincolo e' stato
tratto. Serve a mostrare al coordinatore da dove viene ogni vincolo e a misurare
nel benchmark se un vincolo e' fondato o inventato.

**Structured output (output strutturato).** Far produrre a un modello linguistico
dati conformi a uno schema invece che testo libero. Le tre vie, in ordine di
robustezza, sono: chiederlo nel prompt, usare la chiamata a strumenti con uno
schema dichiarato, vincolare il decoding a una grammatica. Vedi
`07-modelli-linguistici-e-output-strutturato.md`.

**Tool use (chiamata a strumenti).** Meccanismo per cui un modello linguistico, al
posto di rispondere in testo, emette una chiamata a una funzione con argomenti
conformi a uno schema dichiarato. Garantisce la forma dell'output, non il suo
contenuto.

**Tortuosita' stradale.** Rapporto fra la lunghezza del percorso su strada e la
distanza in linea d'aria. In pianura vale circa 1.2, in montagna sale. Il progetto
usa 1.45 per la Calabria, ed e' una taratura dichiarata.

**UNSUPPORTED.** Il tipo di vincolo che il traduttore emette quando l'ordine e'
comprensibile ma non rappresentabile nel repertorio. Non e' un ripiego: un
traduttore che non sa astenersi produce vincoli plausibili e falsi.

**Validatore.** Il secondo programma che ricalcola in modo indipendente tempi,
acqua e autonomia di un piano e lo respinge se non stanno in piedi. Non condivide
codice con il risolutore. Vedi `09-validazione-indipendente.md`.

**VIIRS.** Sensore a bordo dei satelliti Suomi-NPP, NOAA-20 e NOAA-21. Pixel di
circa 375 m nei canali usati per gli incendi, quindi piu' sensibile ai fuochi
piccoli rispetto a MODIS.

**Vincolo globale.** Vincolo che coinvolge molte variabili insieme e porta con se'
un algoritmo di propagazione specializzato, piu' efficace della congiunzione dei
vincoli elementari equivalenti. AllDifferent, Cumulative, NoOverlap, Element e
Circuit sono i piu' usati.

**WGS84.** Il sistema di riferimento geodetico usato dal GPS e dalla quasi totalita'
dei dati geografici moderni. Nel mondo dei dati spaziali e' identificato dal codice
EPSG:4326 quando le coordinate sono latitudine e longitudine in gradi.

**WUI (wildland urban interface, interfaccia urbano-foresta).** La fascia in cui il
costruito e la vegetazione si compenetrano. E' dove gli incendi boschivi diventano
un problema di protezione civile e non solo forestale, ed e' la variabile che
domina le priorita' operative. Il progetto usa la soglia convenzionale di 2 km
dal costruito, che e' una convenzione e non una legge fisica. Vedi
`01-incendi-boschivi-e-aib.md`.
