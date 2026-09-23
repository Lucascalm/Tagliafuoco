# Fondamenti di ricerca operativa: il problema di assegnamento e perche' e' difficile

Questo capitolo spiega la teoria sotto al modulo `optimize/`: che cosa significa "ottimizzare",
perche' decidere quale mezzo va su quale focolaio e' un problema computazionalmente duro, e
perche' la regola intuitiva "manda il mezzo libero piu' vicino" sbaglia in modo prevedibile.

## Indice

1. Il problema concreto: la finestra delle 14:20
2. Che cos'e' un problema di ottimizzazione
3. Il problema di assegnamento classico, e perche' e' facile
4. Perche' Tagliafuoco non e' quel problema
5. La famiglia a cui il problema appartiene davvero
6. Complessita': NP, NP-difficile, e cosa comporta
7. Il conto combinatorio con 26 mezzi e 30 focolai
8. Programmazione lineare, intera, rilassamento, branch and bound, gap
9. Euristiche e metaeuristiche
10. La baseline del progetto e perche' fallisce
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. Il problema concreto: la finestra delle 14:20

Pomeriggio di agosto in Calabria. Il satellite e' appena passato e lo `Scenario` che entra nel
pianificatore contiene una trentina di focolai attivi: alcuni sulla Sila, qualcuno in Aspromonte,
due sulla costa tirrenica a poche centinaia di metri dalle case. La flotta e' quella dichiarata in
`data/static/mezzi.json`, 26 mezzi distribuiti su 14 basi fra aeroporti, elisuperfici AIB e
comandi dei vigili del fuoco. L'orizzonte e' `DEFAULT_HORIZON_MINUTES = 240`.

La domanda e': quale mezzo va su quale focolaio, quando parte, quanti lanci d'acqua fa, da quale
sorgente si rifornisce.

La difficolta' non sta nel calcolare distanze e tempi di volo, che sono aritmetica elementare (le
formule stanno nella sezione 6 di `SPEC.md`). Sta nel fatto che le decisioni interagiscono:
mandare il Canadair di Lamezia sul focolaio di Tiriolo cambia cosa e' possibile fare su quello di
Cirella, e non puoi valutare la prima decisione senza conoscere gia' la seconda.

---

## 2. Che cos'e' un problema di ottimizzazione

Quattro componenti, che conviene saper nominare con precisione.

**Le variabili di decisione** sono le quantita' che il risolutore sceglie. Qui la principale e'
binaria, `x[v,f]` vale 1 se il mezzo `v` interviene sul focolaio `f`; poi ci sono variabili intere
come quanti lanci fa (`sorties` di `Assignment`) e a quale minuto parte (`departure_minute`). La
sorgente d'acqua invece non e' una variabile di decisione, anche se nel piano compare come
`water_source_id`: viene scelta prima, fuori dal modello, e il capitolo 05 spiega perche' e a che
prezzo.

**Il dominio** e' l'insieme dei valori ammessi per una variabile: `{0, 1}` per `x[v,f]`,
`[0, 240]` per `departure_minute`. E' la prima cosa che la programmazione a vincoli manipola, e il
capitolo 05 ci torna sopra.

**I vincoli** sono relazioni che una soluzione deve rispettare. Alcuni vengono dalla fisica (un
Canadair non vola oltre `endurance_minutes` senza rientrare), alcuni dall'organizzazione (la somma
delle missioni della giornata sta sotto `max_shift_minutes`), alcuni dall'ordine del coordinatore,
tradotti nel `ConstraintSet` di `nlu/schema.py`.

**La funzione obiettivo** misura quanto vale una soluzione. Qui e' una combinazione pesata di
cinque termini dichiarati in `config.py`:

```python
OBJECTIVE_WEIGHTS = {
    "coverage": 10.0,          # quanti focolai vengono coperti
    "wui_protection": 8.0,     # quanti focolai vicino alle case vengono coperti
    "response_time": 4.0,      # quanto presto arrivano i mezzi
    "water_fulfilment": 3.0,   # quanta acqua viene erogata sul fabbisogno
    "cost": 1.0,               # quante ore-mezzo vengono spese
}
```

Questi pesi sono una taratura dichiarata, non una verita'. Dicono che coprire un focolaio vale
dieci volte risparmiare un'ora di volo: difendibile come scelta, arbitrario nel numero esatto. Il
vincolo `ObjectiveWeight` esiste perche' il coordinatore possa ripesarli a voce.

Un peso ha senso solo se i termini che pesa vivono sulla stessa scala: la copertura si conta in
focolai, la risposta in minuti, l'acqua in litri, il costo in euro, e sommare unita' diverse
moltiplicate per pesi non significherebbe niente. `optimize/objective.py` divide ogni termine per
il suo massimo teorico, cosi' quel termine al massimo vale il proprio peso e il peso dice davvero
quanto quel criterio conta rispetto agli altri. L'unico che puo' sforare e' il ritardo, perche' due
mezzi in ritardo sullo stesso focolaio contano due volte, ed e' voluto. Poi ogni coefficiente viene
moltiplicato per `OBJECTIVE_PRECISION = 1_000_000` e arrotondato, perche' il modello CP-SAT lavora
su interi: la libreria accetterebbe anche coefficienti con la virgola e se li riscalerebbe da sola,
ma il limite e il gap che leggerai si riferiscono all'obiettivo che il risolutore ha davvero
dentro, quindi conviene che quel fattore lo scelga tu. Il capitolo 05 torna sul punto, il capitolo
06 sui denominatori.

Una **soluzione ammissibile** (*feasible*, realizzabile) rispetta tutti i vincoli e puo' essere
pessima: un piano che copre un focolaio su trenta e' ammissibile. Una **soluzione ottima** e' una
soluzione ammissibile che nessun'altra batte sulla funzione obiettivo. La conseguenza va
interiorizzata subito: l'ottimo e' ottimo rispetto alla funzione obiettivo che hai scritto, non
rispetto alla realta'. Se l'obiettivo pesa male la protezione delle case, il risolutore trovera'
con precisione matematica il piano che protegge male le case. Per questo il validatore non chiede
"e' ottimo?" ma "e' fisicamente possibile?".

Serve infine la distinzione fra **ottimo locale**, migliore di tutte le soluzioni raggiungibili
con una piccola modifica, e **ottimo globale**, migliore in assoluto: un metodo che sa solo
migliorare a piccoli passi si ferma sul primo ottimo locale e non sa quanto dista dal globale.

---

## 3. Il problema di assegnamento classico, e perche' e' facile

Hai `n` agenti e `n` compiti. Assegnare l'agente `i` al compito `j` costa `c[i][j]`. Ogni agente
prende esattamente un compito e ogni compito riceve esattamente un agente. Minimizza il costo
totale. Con `x[i][j]` binaria che vale 1 se l'agente `i` prende il compito `j`:

```
minimizza    somma su i, somma su j  di  c[i][j] * x[i][j]

soggetto a   somma su j di x[i][j] = 1        per ogni agente i
             somma su i di x[i][j] = 1        per ogni compito j
             x[i][j] appartiene a {0, 1}
```

Simbolo per simbolo: `c[i][j]` e' il costo, nel nostro caso il tempo di risposta in minuti. La
prima famiglia di vincoli dice che la riga `i` contiene esattamente un 1, cioe' l'agente fa un
compito solo; la seconda che la colonna `j` ne contiene esattamente uno; la terza che le decisioni
sono si-o-no, non frazioni.

Il problema si risolve in tempo polinomiale. L'**algoritmo ungherese** (*Hungarian algorithm*) lo
risolve in tempo proporzionale a `n^3`. Il nome rende omaggio a due matematici ungheresi sui cui
risultati di teoria dei grafi degli anni trenta il metodo si appoggia; la formulazione algoritmica
arriva negli anni cinquanta e nella sua prima versione costava `n^4`, poi ridotto a `n^3`. Per
`n = 1000` sono circa un miliardo di operazioni elementari, cioe' l'ordine del secondo su un
computer moderno.

La ragione profonda e' esattamente la proprieta' che il nostro problema perde. La matrice dei
vincoli e' **totalmente unimodulare**: ogni sua sottomatrice quadrata ha determinante 0, 1 o -1.
La conseguenza nota e' questa: se la matrice e' totalmente unimodulare e i termini noti sono
interi, allora ogni vertice del poliedro ammissibile ha coordinate intere. Siccome il simplesso
restituisce sempre un vertice, togliere il vincolo di interezza e lasciare `x[i][j]` libero fra 0
e 1 non cambia niente, perche' la soluzione che esce e' gia' intera. L'interezza, di solito la
fonte di tutta la difficolta', qui e' gratis.

### Un esempio che mostra dove sbaglia la regola intuitiva

Tre mezzi, tre focolai, costo in minuti di tempo di risposta. I nomi dei mezzi sono quelli veri di
`data/static/mezzi.json`, ma i minuti sono inventati e arrotondati: non corrispondono alle
distanze reali fra quelle basi e quei luoghi, servono solo a far vedere il meccanismo con numeri
che si seguono a mente. Il controesempio con la geografia vera arriva nella sezione 10.

|        | F1 Tiriolo | F2 Cirella | F3 Gambarie |
|--------|-----------|------------|-------------|
| CAN_01 |  9        | 10         | 40          |
| HEA_02 | 10        | 35         | 38          |
| MED_03 | 33        | 30         | 12          |

La regola intuitiva prende ogni volta la coppia col tempo piu' basso ancora disponibile: CAN_01 su
F1 (9, il minimo della tabella), poi MED_03 su F3 (12), infine quel che resta, HEA_02 su F2 (35).
Totale **56 minuti**. L'algoritmo ungherese trova CAN_01 su F2 (10), HEA_02 su F1 (10), MED_03 su
F3 (12): totale **32 minuti**, cioe' la regola intuitiva sbaglia del 75 per cento. Il meccanismo
dell'errore e' sempre lo stesso: la greedy guadagna un minuto su F1 e in cambio ne perde venticinque
su F2, perche' consuma l'unico mezzo che era decente anche altrove. Con tre mezzi puoi enumerare a
mano tutte e sei le assegnazioni possibili e verificare che 32 e' davvero il minimo. Con ventisei
mezzi non puoi.

La baseline del progetto e' una variante ancora piu' miope di questa, e conviene saperlo: non cerca
la coppia migliore sulla tabella intera, scorre i focolai in un ordine deciso prima (l'ora di
rilevamento) e per ciascuno prende il mezzo libero piu' vicino. Su questa stessa tabella, con i
focolai in ordine F1, F2, F3, sceglie CAN_01 su F1 (9), poi MED_03 su F2 (30, perche' CAN_01 e'
gia' impegnato), poi HEA_02 su F3 (38): **77 minuti**, peggio della greedy sulle coppie e piu' del
doppio dell'ottimo. Entrambe sbagliano per la stessa ragione, cioe' decidono guardando una riga
alla volta, ma l'ordine in cui guardano cambia di quanto sbagliano. La sezione 10 mostra perche'
nel progetto la baseline resta questa e non l'altra.

---

## 4. Perche' Tagliafuoco non e' quel problema

Ogni ipotesi violata corrisponde a un campo preciso dei modelli di dominio.

**Un mezzo serve piu' focolai.** La riga perde la somma 1: un Canadair che chiude un focolaio
piccolo in un'ora ha ancora tre ore di turno. `Plan.assignments_for_vehicle` restituisce una lista
ordinata per `departure_minute` proprio perche' puo' avere piu' di un elemento.

**Un focolaio vuole piu' mezzi.** Anche la colonna perde la somma 1, e il vincolo
`MinVehiclesOnFire` esiste perche' il coordinatore possa imporlo a voce.

**Il costo non e' un numero fisso.** Il costo di mandare CAN_01 su F2 dipende da cosa CAN_01 ha
fatto prima: il campo `available_from_minute` di `Vehicle` esiste per questo, e la sezione 6 di
`SPEC.md` aggiunge che fra due missioni il mezzo consuma anche il `turnaround_minutes` della sua
base. Quando il costo dipende dalla sequenza, la struttura matriciale salta e con essa la totale
unimodularita'. L'interezza smette di essere gratis, ed e' da qui che nasce tutta la difficolta'.

**C'e' una quantita' da dosare.** Il focolaio ha un `water_demand_liters`, il mezzo un
`water_capacity_liters`, e fra i due ci sono il numero di lanci e il tempo di ciclo per pescare e
tornare. Dire "CAN_01 va su F2" non basta.

**Ci sono autonomia e turni.** `endurance_minutes` limita la missione, `max_shift_minutes` la
giornata: risorse che si consumano nel tempo e legano decisioni lontane nell'orizzonte.

**I numeri non coincidono.** Ventisei mezzi, una trentina di focolai, e non tutti i mezzi sono
impiegabili su tutti i focolai: nessuna biiezione da trovare. Alcuni focolai resteranno scoperti
ed e' il sistema a scegliere quali, quindi `unassigned_fire_ids` non e' un errore ma un esito
legittimo.

---

## 5. La famiglia a cui il problema appartiene davvero

Il problema e' la sovrapposizione di tre famiglie classiche. Nessuna da sola lo descrive, ed e'
questa la ragione tecnica per cui serve un risolutore dichiarativo: non esiste una libreria per
l'unione delle tre.

**Set covering con risorse.** Hai elementi da coprire e sottoinsiemi con un costo, e scegli una
sottocollezione che copre tutto al costo minimo:

```
minimizza   somma su j di  c[j] * y[j]
soggetto a  somma su j tali che l'elemento i appartiene a S[j]  di  y[j]  >= 1   per ogni i
            y[j] appartiene a {0, 1}
```

`y[j]` vale 1 se scegli il sottoinsieme `j`, `S[j]` e' l'insieme degli elementi che copre, `c[j]`
quanto costa. Il `>= 1` invece del `= 1` e' la differenza chiave rispetto all'assegnamento:
coprire due volte non e' vietato, e' solo piu' caro. Qui gli elementi sono i focolai e i
sottoinsiemi sono, per ogni mezzo, i focolai che raggiunge in tempo utile; `MaxResponseTime`
restringe quegli insiemi. Cosa aggiunge: la copertura come obiettivo primario, le coperture
multiple ammesse, e la variante realistica della copertura massima a budget fisso, cioe' non puoi
coprire tutto e devi scegliere cosa. E' uno dei problemi storicamente dimostrati NP-difficili.

**Scheduling con risorse cumulative** (in letteratura *resource constrained project scheduling*).
Attivita' con una durata, risorse con una capacita', vincoli di precedenza: colloca ogni attivita'
nel tempo in modo che in nessun istante le attivita' in corso superino la capacita'. Qui ogni
missione occupa un mezzo da `departure_minute` a `release_minute` (proprieta' derivata
`engaged_minutes`). Il mezzo e' una risorsa unaria, una missione alla volta; il turno e' una
risorsa consumabile; anche la sorgente d'acqua lo e', se si modella il fatto che non infiniti
velivoli pescano insieme dallo stesso invaso. Cosa aggiunge: il tempo, cioe' il fatto che ogni
decisione vincola tutte le successive sullo stesso mezzo.

**Instradamento con piu' depositi e finestre temporali**, il *multi depot vehicle routing problem
with time windows* (MDVRPTW). Hai depositi, veicoli con capacita' `Q`, clienti con domanda `q[i]`
e finestra `[a[i], b[i]]`, e costruisci i giri minimizzando il costo. Gli ingredienti formali sono
la conservazione del flusso (se entro in un nodo devo uscirne), l'eliminazione dei sottogiri
(niente cicli che non toccano il deposito) e la propagazione dei tempi lungo il giro. La
corrispondenza e' quasi termine a termine: depositi uguale `Base`, veicoli uguale `Vehicle` con
`water_capacity_liters` al posto di `Q`, clienti uguale `FireCluster` con `water_demand_liters` al
posto di `q[i]`, finestre da `MaxResponseTime` e `TimeWindow`. C'e' una complicazione in piu', nel
routing chiamata instradamento con strutture di rifornimento intermedie: il velivolo fa la spola
fra focolaio e sorgente molte volte dentro la stessa missione, cioe' la formula di `SPEC.md`

```
t_ciclo_acqua = d(fuoco, sorgente) * 2 / v_crociera * 60 + t_pescaggio + t_sgancio
```

Cosa aggiunge: la sequenza. E' l'ingrediente che fa esplodere il conto combinatorio, perche' non
scegli solo chi con chi ma anche in che ordine.

---

## 6. Complessita': NP, NP-difficile, e cosa comporta

**P** e' la classe dei problemi di decisione (risposta si o no) risolvibili in tempo polinomiale
nella dimensione dell'input.

**NP** e' la classe dei problemi di decisione per cui, *data una soluzione candidata*, puoi
verificarla in tempo polinomiale. NP non significa "non polinomiale" ma "non deterministico
polinomiale", e la definizione riguarda la verifica, non la ricerca. Se ti consegno un piano,
controllare autonomia, turni e finestre e' veloce. Trovarlo e' un'altra faccenda.

**NP-difficile** (*NP-hard*) significa almeno tanto difficile quanto qualunque problema in NP:
ogni problema di NP si riduce a questo con una trasformazione polinomiale. Non e' tenuto ad
appartenere a NP ne' a essere un problema di decisione. **NP-completo** significa in NP *e*
NP-difficile.

La distinzione da saper dire al volo: i problemi di **ottimizzazione**, come il nostro, sono
NP-difficili e non NP-completi, perche' non sono problemi di decisione. La loro versione
decisionale ("esiste un piano con valore obiettivo almeno `k`?") e' invece NP-completa. Se `P` sia
uguale a `NP` resta un problema aperto: nessuno ha dimostrato che un algoritmo polinomiale esatto
non esista, ma dopo decenni di tentativi la convinzione diffusa e' che non esista.

**Cosa implica in pratica.** Devi scegliere una rinuncia. O pretendi la soluzione provata ottima e
accetti che su certe istanze il tempo esploda in modo non controllabile, il che va bene per gli
orari di una fabbrica la sera prima e non per un sistema che risponde mentre brucia. Oppure
accetti una soluzione non *provatamente* ottima, sfumatura importante: potrebbe essere ottima,
semplicemente il risolutore non ha finito di dimostrarlo. E' la scelta di Tagliafuoco,
`SOLVER_MAX_SECONDS = 20.0`.

**Perche' non significa irrisolvibile.** NP-difficile riguarda il caso peggiore al crescere della
dimensione, non una particolare istanza. Attenzione pero' a non cavarsela dicendo "tanto le nostre
istanze sono piccole": la difficolta' non cresce in modo ordinato con il numero di variabili, e
istanze costruite apposta con poche decine di variabili possono resistere per ore, mentre modelli
industriali con centinaia di migliaia di variabili si chiudono in pochi secondi. Conta la
struttura, non la taglia. Nel nostro caso la struttura aiuta per tre ragioni: la geografia e
l'autonomia rendono impossibili in partenza molte coppie mezzo-focolaio e il risolutore le elimina
prima di cercare; le finestre temporali strette ordinano le decisioni invece di lasciarle libere;
e ci si puo' fermare con una garanzia, perche' il risolutore restituisce anche un limite sul
valore della soluzione migliore possibile. Nessuna delle tre e' un teorema: sono proprieta' di
queste istanze, da misurare e non da assumere.

---

## 7. Il conto combinatorio con 26 mezzi e 30 focolai

Il modo piu' rapido di convincere qualcuno che non si puo' enumerare e' fare il conto davanti a
lui. Prendiamo i 26 mezzi di `data/static/mezzi.json` e uno scenario denso di 30 focolai. Nel
modello piu' semplice possibile, la sola variabile binaria `x[v,f]`:

```
26 * 30 = 780 variabili binarie
2^780   = circa 6,36 * 10^234 configurazioni
```

Il numero di atomi nell'universo osservabile e' stimato attorno a `10^80`: le configurazioni di
questo modellino sono circa `10^154` volte di piu'.

Obiezione giusta: quasi tutte sono assurde. Restringiamo in modo brutale, supponendo che ogni
mezzo vada su al massimo un focolaio, cioe' proprio l'ipotesi dell'assegnamento classico che il
problema non soddisfa. Ogni mezzo ha 31 scelte, i 30 focolai piu' l'opzione di restare a terra:

```
31^26 = circa 5,96 * 10^38
5,96 * 10^38 / 10^9 = 5,96 * 10^29 secondi = circa 1,9 * 10^22 anni
```

cioe' il tempo che impiegherebbe un computer capace di valutare un miliardo di piani al secondo.
L'universo ha circa `1,4 * 10^10` anni: servirebbero circa mille miliardi di volte la sua eta', e
questo per la versione *semplificata* del problema. Il conto sta in tre righe:

```python
mezzi, focolai = 26, 30
print(2 ** (mezzi * focolai))   # modello binario completo
print((focolai + 1) ** mezzi)   # al piu' un focolaio per mezzo
```

E ignora ancora quanti lanci fa ciascun mezzo, da quale sorgente pesca e in quale ordine visita i
focolai assegnati. L'enumerazione e' fuori discussione: serve un metodo che scarti interi rami
senza guardarli.

Vale la pena dire subito che il modello vero non arriva mai a 780 variabili di assegnamento.
`optimize/model.py` crea una variabile solo per le coppie mezzo-focolaio che sopravvivono a un
filtro preliminare: fuori restano i mezzi non impiegabili (stato non disponibile, volo notturno
senza abilitazione, vento oltre il massimo dichiarato), quelli che non si sa da dove far partire,
quelli senza nessuna sorgente d'acqua utilizzabile per quel focolaio e le missioni che non ci
starebbero comunque nell'autonomia, nel turno o nell'orizzonte. Poi limita a
`DEFAULT_MAX_FIRES_PER_VEHICLE = 2` quanti focolai lo stesso mezzo puo' servire nelle quattro ore.
Quel 2 non e' fisica ed e' dichiarato come parametro nel codice: con missioni che nell'esempio della
sezione 10 vanno da settantasei minuti a quasi quattro ore, un terzo intervento dentro l'orizzonte
e' quasi sempre irrealizzabile, e dichiararlo impossibile in partenza toglie variabili al
risolutore invece di fargliele scartare una per una. E' la stessa
mossa che la sezione 6 chiama "la struttura aiuta": non cambia la classe di complessita', cambia
l'istanza.

---

## 8. Programmazione lineare, intera, rilassamento, branch and bound, gap

**Programmazione lineare (PL).** Obiettivo e vincoli lineari, variabili continue:

```
minimizza    c1*x1 + c2*x2 + ... + cn*xn
soggetto a   a11*x1 + ... + a1n*xn  <=  b1
             ...
             x1, ..., xn >= 0
```

I coefficienti `c` pesano le variabili nell'obiettivo, i coefficienti `a` e i termini noti `b`
descrivono i vincoli. L'insieme ammissibile e' un poliedro convesso e, se un ottimo esiste, sta su
un vertice. Il **metodo del simplesso** sfrutta questo: parte da un vertice ammissibile, si sposta
lungo lo spigolo che migliora l'obiettivo, ripete finche' nessuno spigolo migliora, e a quel punto
ha finito perche' su un problema convesso un ottimo locale e' globale. Nel caso peggiore visita un
numero esponenziale di vertici, su famiglie di istanze costruite apposta, ma quel comportamento
quasi non si incontra nella pratica.

**Programmazione lineare intera (PLI).** Imponi che alcune variabili siano intere e il problema
diventa NP-difficile: l'insieme ammissibile smette di essere un poliedro convesso e diventa una
nuvola di punti isolati, quindi l'argomento "l'ottimo sta su un vertice" non vale piu'.
Tagliafuoco e' pieno di variabili intere.

**Il rilassamento continuo** butta via il vincolo di interezza e risolve il problema lineare che
resta. La soluzione e' di solito frazionaria e priva di senso operativo (`x[CAN_01, F2] = 0,63`
significherebbe mandare i due terzi di un Canadair), ma il punto non e' la soluzione: e' il
**limite** (*bound*). Avendo tolto un vincolo hai allargato l'insieme ammissibile, quindi il
valore ottenuto e' almeno buono quanto l'ottimo vero: in massimizzazione e' un limite superiore,
nessun piano intero fara' meglio. Questo rilegge la sezione 3: nell'assegnamento puro il
rilassamento restituisce gia' valori interi, limite e ottimo coincidono, ed ecco perche' e' facile.

**Branch and bound** (ramifica e delimita) trasforma il limite in un algoritmo esatto. Risolvi il
rilassamento; se viene intero hai finito. Se no prendi una variabile frazionaria e spezzi il
problema in due sottoproblemi (il *branch*): uno con `x[CAN_01, F2] = 0`, uno con
`x[CAN_01, F2] = 1`. Ogni soluzione intera sta in uno dei due, quindi non perdi nulla. La parte
importante e' il *bound*: tieni da parte la migliore soluzione intera trovata finora,
l'**incumbent**, e quando il limite di un sottoproblema risulta peggiore dell'incumbent butti via
quel sottoproblema senza esplorarlo, perche' li' dentro non c'e' niente di meglio. L'esplosione
della sezione 7 non sparisce, viene potata.

**Branch and cut** aggiunge un ingrediente: davanti a un punto frazionario, invece di ramificare
cerchi una disuguaglianza valida che quel punto viola ma che nessuna soluzione intera viola. La
aggiungi al modello (un **taglio**, *cutting plane*) e risolvi un rilassamento piu' stretto. E' il
motore dei risolutori lineari interi moderni, commerciali e liberi.

**Il gap di ottimalita'.** Il risolutore ha sempre due numeri: il valore della migliore soluzione
trovata, l'incumbent, che indichiamo con `O`, e il miglior limite dimostrato, `B`. Il gap relativo
e' la loro distanza normalizzata:

```
gap = | O - B | / max(1, |O|)
```

Il `max(1, |O|)` al denominatore e' il pavimento con cui CP-SAT evita la divisione per zero quando
l'obiettivo e' vicino a zero, ed e' documentato accanto al parametro `relative_gap_limit` nel file
dei parametri di OR-Tools; altri risolutori dividono per `|O|` e basta, e finche' l'obiettivo e'
grande le due formule danno lo stesso numero. C'e' poi una trappola che vale la pena conoscere
prima di confrontare due gap: siccome `O` sta al denominatore, il gap relativo dipende da dove hai
messo lo zero della tua funzione obiettivo. Aggiungere una costante a tutti i piani non cambia quale piano e' il
migliore, ma gonfia `O` e fa scendere il gap senza che sia migliorato niente. Un gap si confronta
con un altro gap solo se i due obiettivi hanno la stessa origine.

Con numeri: dopo dodici secondi hai un piano che vale 8140 punti di obiettivo e un limite di 8305.

```
gap = (8305 - 8140) / 8140 = 165 / 8140 = 0,0203 = 2,03 per cento
```

Significato preciso: *nessun piano puo' valere piu' di 8305, quindi quello che ho in mano e' al
massimo il 2 per cento peggiore del migliore possibile*. E' una dimostrazione, non una speranza.
Una nota di onesta' sullo stato del codice: CP-SAT quel limite lo espone con
`solver.BestObjectiveBound()`, ma oggi `optimize/solver.py` non lo legge e nel piano finiscono solo
il valore dell'obiettivo e lo stato. Finche' resta cosi', il gap si puo' calcolare ma non si puo'
citare leggendo un piano, ed e' una riga di codice che manca, non un limite del metodo.

Un avvertimento che conviene conoscere prima di leggere un log. Se imposti una soglia di gap e il
risolutore la raggiunge, CP-SAT chiude riportando lo stato `OPTIMAL` anche se il gap non e' zero:
lo stato dice "ho finito secondo il criterio che mi hai dato", non "il gap e' zero". Per sapere
quanto e' buono davvero il piano devi leggere il limite, non lo stato.

**Perche' il 2 per cento basta.** E' l'argomento piu' importante del capitolo, ed e' di onesta'
intellettuale piu' che di matematica. I numeri dentro l'obiettivo sono tarature dichiarate:
`WATER_LITERS_PER_KM2 = 100_000` e' segnalata in `config.py` come "taratura, non verita' fisica",
`ROAD_TORTUOSITY = 1.45` idem, i pesi `OBJECTIVE_WEIGHTS` sono scelte di progetto. Quanto siano
incerti il progetto non lo ha misurato e non sarebbe onesto inventare una percentuale, ma basta
guardare che cosa sono per capire che l'incertezza e' molto maggiore del 2 per cento. Ottimizzare
fino allo zero virgola su coefficienti cosi' incerti e' falsa precisione: il secondo piano in
classifica potrebbe essere il migliore nella realta'.

Il rovescio va detto con la stessa franchezza: il gap misura la distanza dall'ottimo *del
modello*, non dalla decisione giusta. Con un modello sbagliato, un gap dello zero per cento
certifica solo che hai risolto perfettamente il problema sbagliato. E' il buco che il validatore
indipendente esiste per coprire.

---

## 9. Euristiche e metaeuristiche

Una **euristica** e' una regola di costruzione ragionevole senza garanzia di qualita'. Una
**metaeuristica** e' uno schema generale che guida una ricerca, applicabile a problemi diversi.

**Greedy (avido).** Costruisci la soluzione una decisione alla volta prendendo ogni volta quella
che sembra migliore adesso. Velocissimo, spesso decente, senza garanzia nel caso generale. Esiste
una classe di problemi con struttura particolare (i matroidi) in cui il greedy e' esatto, e per il
set covering un risultato noto ne limita l'errore a un fattore logaritmico nel numero di elementi;
il nostro problema non ha ne' l'una ne' l'altra proprieta'.

**Ricerca locale.** Definisci un vicinato, cioe' le soluzioni raggiungibili con una piccola
modifica come scambiare due mezzi fra due focolai, e ti sposti finche' trovi miglioramenti. Ti
fermi su un ottimo locale senza sapere quanto dista il globale.

**Large neighborhood search (LNS), ricerca su vicinato ampio.** Vicinati enormi esplorati con un
metodo esatto invece che piccoli esplorati per enumerazione: distruggi una parte della soluzione,
per esempio le assegnazioni di un quinto dei mezzi, e ricostruisci quella parte in modo ottimo
tenendo fisso il resto. Un singolo passo riorganizza molte decisioni insieme, cosa che la ricerca
locale classica non sa fare. CP-SAT ne ha una versione integrata, come vedrai nel capitolo 05.

**Ricerca tabu (tabu search).** Ricerca locale che accetta anche mosse peggiorative per uscire
dagli ottimi locali, tenendo una lista breve di mosse vietate (la lista tabu) per non tornare
subito indietro.

**Ricottura simulata (simulated annealing).** Accetta una mossa peggiorativa con probabilita'
`exp(-delta / T)`, dove `delta` e' il peggioramento e `T` una temperatura che scende lentamente:
all'inizio la ricerca vaga quasi a caso, alla fine diventa una discesa pura. L'analogia e' il
raffreddamento lento di un metallo.

Si usano quando il problema e' troppo grande per un metodo esatto, o quando serve una risposta in
millisecondi. Cosa si perde: non necessariamente la qualita', perche' una buona metaeuristica su
un routing grande regge il confronto a parita' di tempo. Si perde **il limite**: non sai se sei
all'1 o al 40 per cento dall'ottimo, e non puoi saperlo. In un supporto alle decisioni che va
difeso, "questo piano e' al massimo il 2 per cento peggiore del migliore esistente sotto le
ipotesi dichiarate" e' verificabile; "il nostro algoritmo euristico ha dato questo" non lo e'.

---

## 10. La baseline del progetto e perche' fallisce

`SPEC.md` prescrive che a ogni finestra si producano due piani, quello di Tagliafuoco e quello
della regola banale "manda il mezzo libero piu' vicino", confrontando copertura, copertura in
interfaccia urbano-foresta, tempo di risposta medio e al novantesimo percentile, litri erogati sul
fabbisogno e ore-mezzo spese.

`baseline/nearest.py` ne implementa due, e la ragione e' di misura. La funzione `plan_nearest`
scorre i focolai in ordine di rilevamento: e' la sala operativa che risponde alle segnalazioni man
mano che arrivano, e non pianifica. La funzione `plan_priority` cambia una cosa sola, l'ordine in
cui li scorre, che diventa quello della priorita' calcolata. Le due scrivono in `Plan.producer` le
etichette `baseline_nearest` e `baseline_priority`, ed e' cosi' che si distinguono i piani nei
confronti. Con una baseline sola il guadagno del risolutore sarebbe la somma di due effetti
diversi, ordinare meglio e assegnare tutto insieme, senza sapere quanto pesa ciascuno; con due, la
differenza fra le baseline isola il primo e la differenza fra `plan_priority` e il risolutore isola
il secondo. Nel resto della sezione "la baseline" vuol dire `plan_nearest`.

Ai tre difetti gia' visti nella sezione 3 (decide un focolaio alla volta, ottimizza la sola
distanza mentre l'obiettivo pesa cinque criteri, consuma la risorsa migliore sul problema piu'
facile) se ne aggiunge uno che qui conta: non sa nulla della proprieta' `is_wui` di `FireCluster`,
cioe' della vicinanza alle case, che e' proprio la cosa che il coordinatore nomina piu' spesso.

Un chiarimento che conviene dare prima che lo chieda qualcun altro: la baseline non e' resa stupida
apposta. Usa la stessa geometria del risolutore, che importa da `optimize/` invece di riscriverla,
e rispetta autonomia, turno, capacita' del serbatoio, volo notturno e sorgenti davvero utilizzabili
da quel tipo di mezzo; manda al massimo `MAX_VEHICLES_PER_FIRE = 3` mezzi sullo stesso focolaio,
perche' senza quel tetto il primo focolaio della lista si prenderebbe tutta la flotta e la baseline
sarebbe artificialmente pessima.

Degli ordini del coordinatore obbedisce a quelli che tolgono qualcosa dal tavolo: `ReserveVehicle`,
`ExcludeVehicle`, `TimeWindow` e `ForbidAssignment`. Ignora invece, dichiarandolo nelle note del
piano con tanto di `constraint_id`, quelli che impongono una proprieta' del piano finito:
`MaxResponseTime`, `MinVehiclesOnFire`, `VehicleTypeQuota` e `ForceAssignment`. La ragione non e'
pigrizia: quei vincoli non si rispettano scartando un candidato alla volta, si rispettano
scegliendo le assegnazioni tutte insieme, che e' esattamente cio' che una sala operativa senza
strumenti non fa. La scelta e' scritta per esteso nel docstring di `baseline/nearest.py`, e ha una
conseguenza che vedrai subito: nel controesempio qui sotto la baseline produce un piano che poi il
validatore respinge, e non e' un incidente, e' il comportamento atteso.

### Il controesempio, con numeri veri della Calabria

I numeri che seguono non sono illustrativi: sono il piano che `plan_nearest` e `optimize.solve`
producono davvero su questo scenario, e il conto a mano che trovi accanto serve a far vedere da dove
escono. Riduciamo la finestra a due mezzi, presi tali e quali da `data/static/mezzi.json` e dalle
basi di `data/static/basi.json`, e supponiamo tutti gli altri impegnati altrove.

| Mezzo | Base | Posizione | Crociera | Capacita' | Setup | Pescaggio | Sgancio | Autonomia |
|---|---|---|---|---|---|---|---|---|
| CAN_01, Canadair CL-415 | Lamezia Terme | 38,9054 N 16,2423 E | 333 km/h | 6137 L | 12 min | 1,5 min | 0,5 min | 240 min |
| HEA_02, elicottero pesante S-64 | Crotone | 38,9972 N 17,0802 E | 185 km/h | 9000 L | 10 min | 2,5 min | 1 min | 150 min |

| Focolaio | Posizione | Copertura | Area | FRP max | WUI | Rilevato |
|---|---|---|---|---|---|---|
| F1 Tiriolo | 38,950 N 16,510 E | macchia | 0,2 km2 | 45 MW | no, 6 km dal costruito | alle 14:05 |
| F2 Cirella | 39,700 N 15,810 E | conifere | 1,4 km2 | 310 MW | si', 0,8 km dal costruito | alle 14:15 |

Le ore di rilevamento contano per la baseline, che serve i focolai nell'ordine in cui li ha visti, e
non contano per il risolutore: dentro una finestra di pianificazione lo `Scenario` e' una fotografia
sola, e il modello non ha nessun vincolo che leghi la partenza all'istante in cui il focolaio e'
stato visto. Se un focolaio comparisse davvero piu' tardi, a vederlo sarebbe la finestra successiva
della rigiocata, non questa.

Fabbisogno d'acqua con la formula della sezione 6 di `SPEC.md`, dove `coeff_copertura` vale 1,2 per
la macchia e 1,4 per le conifere e `coeff_intensita = 1 + min(1; frp_max / 200)`:

```
F1:  0,2 * 100000 * 1,2 * (1 + 45/200)       = 0,2 * 100000 * 1,2 * 1,225 =   29.400 litri
F2:  1,4 * 100000 * 1,4 * (1 + min(1; 1,55)) = 1,4 * 100000 * 1,4 * 2,0   =  392.000 litri
```

La priorita' di base, calcolata con la formula della stessa sezione, vale 0,297 per F1 e 0,756 per
F2: F2 pesa due volte e mezzo F1 in ogni termine dell'obiettivo pesato sulla priorita', cioe' la
copertura, l'acqua e il ritardo.

Distanze in linea d'aria (haversine, raggio 6371 km) e tempi. Il setup precede il volo, quindi
l'arrivo si conta dalla partenza come setup piu' volo:

| Tratta | Distanza | Volo | Arrivo dalla partenza |
|---|---|---|---|
| Lamezia verso F1 | 23,7 km | 4,3 min a 333 km/h | 16,3 min (12 di setup) |
| Crotone verso F1 | 49,6 km | 16,1 min a 185 km/h | 26,1 min (10 di setup) |
| Lamezia verso F2 | 95,9 km | 17,3 min a 333 km/h | 29,3 min |
| Crotone verso F2 | 134,3 km | 43,6 min a 185 km/h | 53,6 min |

Adesso l'acqua, e qui il dettaglio che sembra burocratico decide l'esito. Ogni sorgente di
`data/static/sorgenti_acqua.json` dichiara un campo `usable_by`, cioe' quali tipi di mezzo possono
davvero rifornirsi li', e un'affidabilita' stagionale.

| Sorgente | Distanza | Pescaggio dichiarato | Affidabilita' | Chi la puo' usare |
|---|---|---|---|---|
| Lago Passante | 13,6 km da F1 | 2,5 min | 0,70 | elicotteri, non Canadair |
| Lago Ampollino | 23,9 km da F1 | 2,0 min | 0,75 | anche Canadair |
| Golfo di Squillace | 25,6 km da F1 | 1,5 min | 1,00 | anche Canadair |
| Tirreno al largo di Scalea | 13,1 km da F2 | 1,5 min | 1,00 | anche Canadair |

Il Lago Passante, che e' il piu' vicino a F1, per un Canadair non esiste. Il criterio con cui
`scripts/genera_dati_statici.py` ha compilato quel campo sta scritto nel file: un CL-415 ammara e
imbarca in corsa, e per farlo gli servono circa 1300 metri di specchio libero e due metri di
pescaggio, quindi il mare e i tre invasi silani grandi si', un invaso stretto e incassato di
montagna no. E' un'assunzione del dataset, non una misura su quei bacini, ma e' l'assunzione con cui
il sistema lavora. Il tempo di pescaggio e' il peggiore fra quello dichiarato dal mezzo e quello
dichiarato dalla sorgente, perche' comanda il collo di bottiglia dei due: per CAN_01 all'Ampollino
vale `max(1,5; 2,0) = 2,0` minuti.

I due pianificatori scelgono l'acqua con criteri diversi, e vale la pena saperlo perche' e' una
differenza vera fra due moduli. La baseline prende la sorgente utilizzabile piu' vicina
(`_nearest_water_source`), quindi per CAN_01 su F1 sceglie l'Ampollino. Il risolutore usa
`best_water_source` di `optimize/geometry.py`, che ordina per ciclo diviso affidabilita' stagionale:
l'Ampollino ha un ciclo di 11,1 minuti ma affidabilita' 0,75, quindi segna 14,8, mentre il Golfo di
Squillace ha un ciclo di 11,2 minuti con affidabilita' 1,00 e vince con 11,2. Su un invaso silano a
ferragosto quella differenza non e' burocrazia: e' la probabilita' di trovare il lago in secca e di
dover rifare il giro a vuoto.

L'ordine del coordinatore, "sui focolai vicino alle case voglio un mezzo sopra entro quaranta
minuti", tradotto dal modulo `nlu/`:

```python
MaxResponseTime(
    constraint_id="c1",
    minutes=40,
    fire_selector=Selector(field=SelectorField.FIRE_IS_WUI, values=["true"]),
    enforcement=Enforcement.HARD,
    source_span="vicino alle case ... entro quaranta minuti",
)
```

Quel tetto si misura sull'`arrival_minute`, cioe' sull'orario di arrivo contato dall'inizio
dell'orizzonte e non sulla differenza arrivo meno partenza, con le conseguenze che la terza nota in
fondo alla sezione mette in fila.

**La baseline** comincia da F1, che ha visto per primo. Il mezzo libero piu' vicino e' CAN_01, a
23,7 km contro i 49,6 di HEA_02, e la missione con l'Ampollino viene cosi':

```
t_ciclo  = 2 * 23,9 / 333 * 60 + 2,0 + 0,5 = 8,6 + 2,5 = 11,1 minuti per lancio
lanci    = ceil(29.400 / 6137) = 5
missione = 12 (setup) + 4,3 (andata) + 5 * 11,1 + 4,3 (ritorno) = 76,1 minuti
```

CAN_01 arriva su F1 al minuto 16,3, rientra a Lamezia al minuto 76,1 e, con i quindici minuti di
riattrezzamento che la base dichiara in `Base.turnaround_minutes`, non riparte prima del minuto
91,6. Poi tocca a F2, e l'unico mezzo libero e' HEA_02, che ci arriva al minuto 53,6. Quando CAN_01
si libera la baseline manda su F2 anche lui, che arriva al minuto 120,9 e versa altri quindici
carichi, per un totale di 128.055 litri sul focolaio. Ma il numero che il vincolo del coordinatore
guarda e' il primo arrivo, cioe' 53,6: tredici minuti e mezzo oltre il tetto. `validate/rules.py`
emette una violazione bloccante e il piano viene respinto.

**Il risolutore** guarda i due focolai insieme e accetta di peggiorare su F1 per salvare F2. HEA_02
va su F1 e arriva al minuto 26,1, quasi dieci minuti piu' tardi della baseline; essendo un
elicottero puo' usare il Lago Passante, quindi il suo ciclo e'
`2 * 13,6 / 185 * 60 + 2,5 + 1,0 = 12,3` minuti, e i 9000 litri di capacita' gli bastano in 4 lanci
per coprire tutto il fabbisogno di F1, con una missione di 91,8 minuti dentro i 150 di autonomia.
CAN_01 parte subito verso F2 e arriva al minuto 29,3, dieci minuti e sette decimi dentro il tetto.
Con Scalea a 13,1 km il suo ciclo e' 6,7 minuti ed e' l'autonomia a decidere quanti ne fa: dodici
minuti di setup piu' 17,3 di andata e altrettanti di rientro fanno 46,6 minuti fissi, i 193 che
restano dei 240 divisi per il ciclo danno 28 lanci, cioe' 171.836 litri sui 392.000. La missione
chiude al minuto 237, appena dentro l'orizzonte di quattro ore.

| | Baseline (`plan_nearest`) | Risolutore (CP-SAT) |
|---|---|---|
| F1, chi ci va e quando arriva | CAN_01, minuto 16,3 | HEA_02, minuto 26,1 |
| F1, acqua erogata | 30.685 L su 29.400 richiesti | 36.000 L su 29.400 richiesti |
| F2, primo arrivo | HEA_02, minuto 53,6 | CAN_01, minuto 29,3 |
| F2, acqua erogata | 128.055 L su 392.000 (33 per cento) | 171.836 L su 392.000 (44 per cento) |
| Tetto duro dei quaranta minuti su F2 | violato di 13,6 minuti | rispettato con 10,7 di margine |
| Focolai coperti | 1 su 2 | 1 su 2 |
| Tempo medio di risposta | 34,9 minuti | 27,7 minuti |
| Minuti-mezzo spesi | 368,2 | 328,8 |
| Esito del validatore | piano respinto, violazione bloccante | nessuna violazione bloccante |

La riga da guardare con piu' attenzione e' quella della copertura, perche' dice 1 su 2 in tutti e
due i piani e sembra togliere al risolutore la sua vittoria. Conviene saperla spiegare, perche' a un
colloquio quella riga la notano. Nel progetto "coperto" e' una parola severa: `optimize/model.py`
accende la variabile `covered` solo quando i litri erogati raggiungono l'intero fabbisogno, ed
`eval/metrics.py` conta coperto un focolaio che riceve almeno una soglia dichiarata del suo
fabbisogno, oggi il 60 per cento (`COVERAGE_WATER_RATIO = 0.60`), un po' piu' larga della lettera di
`SPEC.md`, che chiede il fabbisogno pieno. Con la taratura da 100.000 litri per chilometro quadrato,
1,4 km2 di pineta chiedono 392.000 litri, cioe' sessantaquattro carichi di Canadair: in quattro ore
non li fa nessuno, quindi F2 non risulta coperto in nessuno dei due piani e su quel focolaio la
metrica di copertura e' cieca. Il segnale sta altrove, ed e' dove il progetto lo misura: un piano
rispetta l'ordine del coordinatore e supera il validatore, l'altro no; e sul focolaio che
nell'obiettivo pesa piu' del doppio, il risolutore versa un terzo d'acqua in piu' arrivando
ventiquattro minuti prima. Nell'obiettivo quel guadagno entra dal termine idrico, che da' credito
parziale litro per litro ed e' pesato sulla priorita', non dal termine di copertura, che qui vale
zero per entrambi i piani.

Tre note oneste su questo esempio.

La prima: le percentuali d'acqua sono basse per effetto della taratura da 100.000 litri per
chilometro quadrato, che per 1,4 km2 di pineta produce un fabbisogno che nessun singolo velivolo
copre in quattro ore, mentre nella realta' su un fronte del genere lavorano piu' mezzi insieme alle
squadre di terra. Il numero misura la taratura, non un difetto del risolutore.

La seconda riguarda gli arrotondamenti, e si vede meglio stringendo l'ordine. Con il tetto a
quaranta minuti il margine di CAN_01 su F2 e' comodo; con un tetto di mezz'ora l'arrivo al minuto
29,3 passerebbe per sette decimi di minuto, e li' il verso dell'arrotondamento deciderebbe l'esito.
Il modello lavora in decimi di minuto e arrotonda per eccesso ogni pezzo della missione: il ciclo da
6,704 minuti diventa 6,8, e la missione di HEA_02 su F1, che a mano fa 91,4 minuti, nel piano
risulta 91,8. L'errore si accumula sempre a sfavore del piano, che e' il lato sicuro, ed e' il
motivo per cui il capitolo 05 insiste tanto sul verso in cui si arrotonda.

La terza riguarda tre convenzioni fissate per iscritto nel progetto, che vale la pena saper citare
perche' sono il tipo di dettaglio su cui due moduli indipendenti divergono in silenzio. Il tetto di
`MaxResponseTime` si misura sull'`arrival_minute` assoluto e non sulla differenza arrivo meno
partenza che `Assignment.response_minutes` chiama tempo di risposta; la scelta va nella direzione
sicura, perche' `departure >= 0` implica `arrival >= arrival - departure`, quindi ogni piano che
rispetta il tetto sull'orario rispetta anche quello sulla differenza. Il risolutore e' poi piu'
severo del validatore: impone il tetto a ogni mezzo che assegna a quel focolaio, mentre
`_check_max_response` guarda solo il primo arrivo, e un focolaio lasciato scoperto soddisfa il
vincolo a vuoto nel modello mentre nel validatore diventa un avviso. Il tempo di pescaggio, infine,
compare sia su `Vehicle.refill_minutes` sia su `WaterSource.refill_minutes` e si legge come il
massimo dei due, in `optimize/geometry.py` e nel caso nominale di `validate/physics.py`; quando il
validatore deve dichiarare un tempo *impossibile* usa invece il minimo, perche' un allarme deve
nascere da una violazione della fisica e non da una differenza di modellazione.

Il punto qualitativo non dipende da nessuna di queste scelte: **la baseline ha speso il mezzo
migliore sul problema piu' facile**, e quando e' arrivato il problema difficile non aveva piu'
niente in mano. Nessun aggiustamento della regola "il piu' vicino" corregge questo, perche' il
difetto non sta nella metrica ma nel fatto che si decide guardando un focolaio alla volta. Con due
mezzi il danno e' visibile a mano; con ventisei cresce, perche' aumentano le occasioni di consumare
male una risorsa scarsa.

---

## 11. Cosa devi saper spiegare

**Perche' non usate l'algoritmo ungherese, visto che e' polinomiale?**
Risolve un problema diverso. L'ungherese richiede che ogni agente prenda esattamente un compito e
ogni compito riceva esattamente un agente, con costo fisso per ogni coppia. Qui un mezzo serve
piu' focolai in sequenza, un focolaio ne vuole piu' di uno, e il costo di una coppia dipende da
cosa quel mezzo ha fatto prima, compreso il riattrezzamento in base. Quando il costo dipende dalla
sequenza si perde la totale unimodularita', e con essa l'interezza gratuita che rendeva facile il
problema.

**Che cos'e' la totale unimodularita' e perche' dovrebbe interessarmi?**
E' una proprieta' della matrice dei vincoli: ogni sua sottomatrice quadrata ha determinante 0, 1 o
-1. Se vale e i termini noti sono interi, tutti i vertici del poliedro ammissibile hanno coordinate
intere, quindi il rilassamento continuo restituisce gia' una soluzione intera e non serve
ramificare. Interessa perche' spiega in una frase perche' un problema che sembra identico al
nostro costa mille volte di meno: e' la linea di confine fra gli assegnamenti facili e noi.

**Il vostro problema e' NP-completo? E allora come fate a risolverlo in venti secondi?**
E' NP-difficile, non NP-completo: NP-completo si dice dei problemi di decisione che stanno in NP e
sono NP-difficili, mentre il nostro e' di ottimizzazione e non e' un problema di decisione (la sua
versione decisionale, "esiste un piano con valore obiettivo almeno k?", e' invece NP-completa). E
NP-difficile riguarda il caso peggiore asintotico, non una particolare istanza. Non diciamo "le
nostre istanze sono piccole", perche' la difficolta' non cresce in modo ordinato con la taglia:
diciamo che la geografia e l'autonomia eliminano in partenza la maggioranza degli accoppiamenti,
che le finestre temporali ordinano le decisioni, e che questo va misurato istanza per istanza.

**Come fate a dire che il piano e' buono, se vi fermate prima di finire? E perche' il 2 per cento
vi basta?**
Il risolutore restituisce anche il miglior limite dimostrato sul valore ottimo, e la distanza
relativa fra i due numeri e' il gap. Un gap del 2 per cento significa che nessun piano esistente
puo' valere piu' del 2 per cento in piu' del nostro, ed e' una dimostrazione, non una stima. Va
detto che oggi quel limite si legge dal risolutore e non dal piano, perche' `optimize/solver.py`
non lo salva: e' una mancanza del codice da sistemare, non dell'argomento. Ci
basta perche' l'errore di modellazione e' molto piu' grande: la costante da 100.000 litri per
chilometro quadrato e' una taratura dichiarata, il fattore di tortuosita' 1,45 e' una taratura, i
pesi dell'obiettivo sono scelte di progetto. Il gap e' anche il motivo per cui non usiamo una
metaeuristica: potrebbe darci un piano altrettanto buono, ma non saprebbe dirci quanto e' buono.

**La baseline "manda il mezzo piu' vicino" non e' quello che fa anche un uomo?**
E' proprio per questo che la usiamo come riferimento: e' la regola implicita sotto pressione, e
batterla e' l'affermazione che il progetto deve sostenere. Sbaglia perche' decide un focolaio alla
volta e ottimizza una grandezza sola. Nell'esempio calabrese comincia dal focolaio visto per primo,
quello di macchia a Tiriolo, e ci manda il Canadair di Lamezia perche' e' a 23,7 km; lo impegna per
settantasei minuti piu' quindici di riattrezzamento, e sul focolaio in pineta a ottocento metri
dalle case resta solo l'elicottero di Crotone, che arriva al minuto 53,6 contro un tetto di
quaranta. Il validatore respinge quel piano. Da aggiungere per onesta': la baseline non e'
handicappata di proposito, usa la stessa geometria del risolutore e obbedisce ai divieti
dell'ordine (mezzo di riserva, mezzo escluso, coppia vietata, finestra oraria), mentre ignora per
scelta dichiarata i vincoli che riguardano il piano finito, come appunto il tetto sul tempo di
arrivo, perche' rispettarli richiede di guardare avanti. E ne esistono due versioni proprio per
separare il contributo dell'ordinamento da quello dell'assegnazione simultanea.

**Se il risolutore dice che il piano e' ottimo, il validatore non e' ridondante?**
Sono due domande diverse. Il risolutore dice "e' il migliore rispetto al modello che ti ho
scritto"; il validatore dice "e' fisicamente possibile", ricalcolando tempi di volo, cicli
d'acqua, autonomia e turni da zero senza importare nulla da `optimize/`. Con un vincolo di
autonomia scritto male il risolutore restituisce a gap zero un piano impossibile, e solo un
ricalcolo indipendente lo scopre (il capitolo 09 entra nel dettaglio). Vale la pena aggiungere che
lo stato `OPTIMAL` non garantisce nemmeno gap zero, se e' stata impostata una soglia di gap.

**Nel vostro controesempio la copertura e' 1 su 2 in entrambi i piani: allora che cosa ha guadagnato
il risolutore?**
Ha guadagnato le due cose che li' contano e che la metrica di copertura non vede. La prima e' la
validita': il piano della baseline viola un vincolo duro dell'ordine (primo arrivo al minuto 53,6
contro un tetto di quaranta) e il validatore lo respinge, quindi non e' un piano peggiore, e' un
piano che non si puo' usare. La seconda e' l'acqua sul focolaio che conta: 171.836 litri contro
128.055, arrivando ventiquattro minuti prima, su un focolaio che nell'obiettivo pesa piu' del
doppio dell'altro. La copertura resta 1 su 2 perche' nel progetto "coperto" vuol dire aver raggiunto
il fabbisogno d'acqua (`eval/metrics.py` accetta il 60 per cento come soglia dichiarata, il modello
chiede il fabbisogno intero), e i 392.000 litri che la taratura attribuisce a quel fronte nessun
mezzo li versa in quattro ore. E' un limite della taratura e della metrica, dichiarato, non un
pareggio fra i due piani.

**Un focolaio in `unassigned_fire_ids` e' un errore?**
No, e' un esito legittimo: con ventisei mezzi e trenta focolai nessuna assegnazione li copre
tutti, quindi la domanda giusta non e' "come li copro tutti" ma "quali scelgo di non coprire". Il
sistema rende esplicita quella scelta invece di nasconderla, e i pesi che la governano sono
dichiarati in `config.py`.

---

## 12. Per approfondire

Sui fondamenti, un buon manuale universitario di ricerca operativa copre nell'ordine formulazione,
dualita' lineare, simplesso e flussi su rete, e proprio da li' emerge perche' l'assegnamento e' un
caso particolare di flusso a costo minimo. Cerca un testo che tratti la totale unimodularita':
spiega tutta la sezione 3 in una riga.

Sulla complessita', il riferimento storico e' il libro che negli anni settanta ha raccolto e
catalogato i problemi NP-completi noti, utile perche' mostra quanti problemi di aspetto innocente
cadono nella stessa classe; per una trattazione moderna, qualunque manuale di teoria della
computazione dedica due o tre capitoli a P, NP, riduzioni e completezza.

Sul vehicle routing cerca le rassegne dedicate al problema con finestre temporali e piu' depositi,
e in particolare quelle sulle varianti con strutture di rifornimento intermedie, le piu' vicine al
caso dei velivoli che fanno la spola fra il fuoco e l'acqua. Sul dispiegamento di risorse
antincendio esiste un filone di ricerca operativa applicata alle emergenze, da leggere con
prudenza: molti lavori assumono dati che in Italia non sono pubblici, quindi cita cosa mostra un
lavoro e non il numero esatto che riporta. Sulle metaeuristiche cerca un testo comparato, che
mostri come condividano la stessa struttura e differiscano nel modo di sfuggire agli ottimi
locali. La documentazione di OR-Tools contiene infine una discussione pratica sul confronto fra
risolutori a vincoli e risolutori lineari interi, da leggere dopo il capitolo 05 e non prima.
