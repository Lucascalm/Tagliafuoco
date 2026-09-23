# Constraint programming e come funziona CP-SAT

Questo capitolo spiega che cos'e' la programmazione a vincoli, come un risolutore a vincoli
ragiona davvero, e perche' Tagliafuoco usa CP-SAT di OR-Tools invece di un risolutore lineare
intero. Serve a saper difendere quella scelta, non solo a ripeterla.

## Indice

1. Il problema: perche' non basta scrivere disuguaglianze
2. Che cos'e' la programmazione a vincoli, e in cosa differisce dalla PLI
3. Domini, propagatori e il ciclo propagazione-ricerca
4. Un esempio svolto a mano, passo per passo
5. I vincoli globali, e perche' sono il vero valore della CP
6. CP-SAT sotto il cofano
7. Interi: la scalatura di tempi, litri e pesi
8. Stati di uscita, limite di tempo, determinismo
9. Infattibilita': diagnosi e strategia del progetto
10. Quando invece sceglieresti un MILP
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. Il problema: perche' non basta scrivere disuguaglianze

Il capitolo 04 ha stabilito che il problema di Tagliafuoco e' NP-difficile e che serve un metodo
che pota lo spazio di ricerca invece di enumerarlo. La programmazione lineare intera e' uno di
quei metodi. Proviamo a usarla e vediamo dove fa attrito.

Il coordinatore dice: "il Canadair di Lamezia o va su Cirella o va su Tiriolo, non su tutti e
due". In PLI si scrive come somma di due binarie minore o uguale a uno, nessun problema. Poi dice:
"le missioni dello stesso mezzo non si sovrappongono nel tempo". Qui l'attrito comincia, perche'
la non sovrapposizione fra due intervalli e' una disgiunzione: *o* la prima missione finisce prima
che la seconda cominci, *o* viceversa. Una disgiunzione non e' una disuguaglianza lineare, e per
esprimerla serve la tecnica del **big M**: si introduce una binaria `z` e una costante grande `M`,
e si scrive

```
fine_1  <=  inizio_2 + M * (1 - z)
fine_2  <=  inizio_1 + M * z
```

Quando `z = 1` la prima disuguaglianza e' attiva e la seconda diventa vuota, perche' `M` la rende
sempre vera; con `z = 0` succede il contrario. Funziona, ma costa caro. Nel rilassamento continuo
`z` non e' costretta a valere 0 o 1: con `z = 0,5` entrambe le disuguaglianze si allentano di
`M/2`, cioe' nessuna delle due morde e gli intervalli sono liberi di sovrapporsi. Il rilassamento
di un modello pieno di big M e' quindi debolissimo, e senza un limite stretto il branch and bound
del capitolo 04 perde la sua arma principale.

Con ventisei mezzi e trenta focolai le disgiunzioni sono migliaia: ogni coppia di missioni sullo
stesso mezzo, ogni "se questo mezzo e' escluso allora nessuna assegnazione lo usa", e ogni
alternativa fra due sorgenti d'acqua nel momento in cui si volesse lasciarla decidere al
risolutore, cosa che il progetto oggi non fa (sezione 5). Un risolutore che parla solo disuguaglianze lineari passa il tempo a
combattere contro la propria codifica. La programmazione a vincoli nasce per dichiarare quelle
relazioni direttamente, e per ragionarci sopra con algoritmi fatti apposta.

---

## 2. Che cos'e' la programmazione a vincoli, e in cosa differisce dalla PLI

La **programmazione a vincoli** (*constraint programming*, CP) e' un modo di risolvere problemi in
cui dichiari le variabili con il loro dominio finito e le relazioni che devono valere fra loro, e
lasci al motore il compito di trovare valori che soddisfano tutto. Tre differenze rispetto alla
programmazione lineare intera, da tenere separate.

**Si dichiarano relazioni, non solo disuguaglianze lineari.** In CP puoi scrivere
`AllDifferent(a, b, c)`, `NoOverlap(missioni)`, `y = array[x]` con `x` variabile, "questi
intervalli non consumano mai piu' di due posti di pescaggio contemporaneamente". Sono relazioni di
prima classe, non codifiche.

**I domini sono finiti.** Ogni variabile ha un insieme esplicito di valori possibili. Sembra una
limitazione ed e' la fonte del potere del metodo: se il dominio e' finito, il motore puo'
rimuoverne valori mentre ragiona, e ogni valore rimosso e' un intero ramo di ricerca che scompare.

**Il motore ragiona per propagazione e ricerca.** Un risolutore PLI risolve un problema continuo
rilassato per ottenere un limite, poi ramifica. Un risolutore CP classico non rilassa niente:
deduce direttamente sui domini finiti, restringendoli finche' puo', e solo quando non riesce piu'
a dedurre fa una scelta e prova. Tieni pero' a mente questa frase con una riserva, perche' la
sezione 6 la corregge: CP-SAT non e' un risolutore CP classico e un rilassamento continuo lo
costruisce eccome.

I due metodi sono quindi bravi in posti diversi. La PLI eccelle quando il rilassamento continuo e'
stretto, cioe' quando la soluzione frazionaria e' gia' quasi intera; la CP quando la struttura e'
disgiuntiva e il rilassamento continuo non dice niente di utile. La sezione 10 torna sul confronto.

---

## 3. Domini, propagatori e il ciclo propagazione-ricerca

Il motore CP lavora su tre oggetti.

Il **dominio** di una variabile e' l'insieme dei valori che puo' ancora assumere, dato quello che
il motore ha dedotto finora. All'inizio e' l'insieme dichiarato; durante la ricerca si restringe;
se diventa vuoto, quel ramo e' impossibile.

Un **propagatore** e' l'algoritmo associato a un vincolo. Ha un mestiere solo: guardare i domini
correnti delle variabili che il vincolo tocca e rimuovere i valori che non possono far parte di
nessuna soluzione di *quel* vincolo. Non risolve il problema, taglia.

Il **ciclo** e' questo. Si propaga finche' nessun propagatore rimuove piu' niente, cioe' finche'
si raggiunge un punto fisso. Se un dominio e' vuoto, si torna indietro. Se tutti i domini hanno un
solo valore, si ha una soluzione. Altrimenti si sceglie una variabile ancora indecisa, si prova un
valore (o si spezza il dominio in due) e si ricomincia a propagare dentro quel ramo.

Le **consistenze** sono i livelli di forza a cui un propagatore puo' lavorare, e sono un
compromesso fra quanto taglia e quanto costa.

La **consistenza di nodo** riguarda i vincoli su una variabile sola: se hai dichiarato
`arrivo <= 40`, togli subito dal dominio di `arrivo` tutto cio' che supera 40. Banale e gratuita.

La **consistenza d'arco** (*arc consistency*) riguarda i vincoli fra due variabili. Un vincolo fra
`x` e `y` e' arc consistent quando per ogni valore rimasto nel dominio di `x` esiste almeno un
valore nel dominio di `y` che lo rende soddisfatto, e viceversa. Quel valore di `y` si chiama
**supporto**, e un valore di `x` senza nessun supporto si puo' togliere senza perdere soluzioni.

La **consistenza di limite** (*bound consistency*) e' la versione economica: invece di ogni
singolo valore del dominio, controlla solo il minimo e il massimo e restringe l'intervallo. Costa
molto meno e taglia un po' meno. Sulle variabili numeriche con domini grandi, come i minuti di un
orizzonte di 240, e' quasi sempre la scelta giusta.

---

## 4. Un esempio svolto a mano, passo per passo

Due esempi piccoli, con numeri veri del progetto. Seguili con carta e penna.

### Consistenza di limite sui tempi

CAN_01 e' il Canadair basato all'aeroporto di Lamezia Terme: crociera 333 km/h e
`setup_minutes = 12`, secondo `data/static/mezzi.json`. Il focolaio F2 e' a Cirella, a 95,9 km in
linea d'aria: sono 17,3 minuti di volo, piu' i 12 di setup, cioe' 29,3 minuti dalla partenza
all'arrivo, che arrotondati per eccesso fanno 30 minuti interi. Lavoriamo in minuti interi per
poter seguire i conti a occhio; il modello vero usa i decimi di minuto, come spiega la sezione 7.

CAN_01 non e' libero all'istante zero: sta rientrando da un'altra missione e torna disponibile al
minuto 10. Nel modello quel dato non e' un'invenzione narrativa, e' il campo
`Vehicle.available_from_minute`, che la simulazione riempie e che `optimize/geometry.py` converte
nel pavimento `earliest_start_units` della partenza. Il coordinatore ha poi imposto un tetto di
quaranta minuti sui focolai vicino alle case. Nel progetto quel tetto si misura sull'orario di
arrivo contato dall'inizio dell'orizzonte, non sulla differenza arrivo meno partenza: lo fissano il
commento di `optimize/model.py` e la funzione `_check_max_response` di `validate/rules.py`, e il
capitolo 04 spiega perche' e' la lettura sicura. Quindi il vincolo del coordinatore diventa
`arrivo <= 40`. Dichiariamo due variabili sull'orizzonte di 240 minuti:

```
partenza  con dominio  [0, 240]
arrivo    con dominio  [0, 240]
vincolo:  arrivo = partenza + 30
vincoli unari:  partenza >= 10   e   arrivo <= 40
```

Passo 1, consistenza di nodo sui due vincoli unari: `partenza` diventa `[10, 240]` e `arrivo`
diventa `[0, 40]`.

Passo 2, il propagatore di `arrivo = partenza + 30` guarda il minimo di `partenza`, che ora e' 10,
e deduce che `arrivo` non scende sotto 40. Dominio di `arrivo`: `[40, 40]`, cioe' un valore solo.

Passo 3, lo stesso propagatore guarda il massimo di `arrivo`, che e' 40, e deduce
`partenza <= 10`. Dominio di `partenza`: `[10, 10]`.

Passo 4, nessun propagatore rimuove altro: punto fisso. Senza aver provato un solo valore, il
motore ha fissato entrambe le variabili. Non e' un caso fortunato: e' quello che succede quando i
vincoli sono stretti, ed e' esattamente la situazione dei focolai in interfaccia urbano-foresta.

Adesso arriva un'informazione nuova, la stessa del controesempio del capitolo 04: CAN_01 non e'
libero al minuto 10 come avevamo supposto, perche' sta chiudendo la missione su F1 e rientra a
Lamezia al minuto 76,1. La sezione 6 di `SPEC.md` impone di aggiungere i `turnaround_minutes` della
base, che per Lamezia sono 15, quindi prima del minuto 91,6 non riparte: a minuti interi il
pavimento sulla partenza sale a 92. Ma la propagazione aveva gia' ridotto `partenza` a `[10, 10]`,
e un pavimento a 92 su un dominio che finisce a 10 lascia l'insieme vuoto: il motore dichiara il
ramo infattibile senza esplorare niente.
Questa e' la potenza della propagazione: non e' un'ottimizzazione della ricerca, e' ricerca che non
avviene. Una precisazione sul modello vero, che vale la pena saper dire: quel pavimento non e'
scritto come vincolo unario. Nasce dal `NoOverlap` sulle missioni dello stesso mezzo, perche'
`optimize/model.py` allunga l'intervallo di occupazione di ogni missione del tempo di
riattrezzamento, e il propagatore disgiuntivo deduce da solo che la missione successiva non puo'
cominciare prima. L'effetto sul dominio e' identico a quello che hai appena visto a mano, ma lo
ottiene un vincolo globale che c'era gia', senza aggiungere ne' variabili ne' disgiunzioni.

### Consistenza d'arco e il limite della decomposizione

Tre mezzi, tre focolai, un mezzo per focolaio e un focolaio per mezzo. Le variabili sono `a` (il
focolaio di CAN_01), `b` (dell'elicottero medio MED_03), `c` (dell'elicottero leggero LGT_03),
tutte con dominio iniziale `{F1, F2, F3}`.

Due vincoli arrivano dal coordinatore tramite `nlu/`: un `ForbidAssignment` che tiene i Canadair
fuori da F3, quindi `a != F3`, e un `MaxResponseTime` di venti minuti su F3 che MED_03 non riesce
a rispettare dalla sua base, quindi `b != F3`. Il terzo vincolo e' strutturale,
`AllDifferent(a, b, c)`.

Passo 1, consistenza di nodo sui due vincoli unari:

```
a: {F1, F2}
b: {F1, F2}
c: {F1, F2, F3}
```

Passo 2, proviamo con la decomposizione di `AllDifferent` in tre vincoli binari, `a != b`,
`a != c`, `b != c`, e applichiamo la consistenza d'arco. Il valore `F1` nel dominio di `c` ha un
supporto in `a` (basta `a = F2`) e uno in `b` (basta `b = F2`); stessa cosa per `F2`.
**La decomposizione non deduce niente.**

Passo 3, usiamo invece il propagatore globale di `AllDifferent`. Questo guarda l'insieme `{a, b}`
e nota che le due variabili insieme dispongono di due soli valori, `F1` e `F2`: due variabili che
devono prendere valori distinti da un insieme di due li esauriscono entrambi, quindi nessun'altra
variabile puo' prenderli. Il dominio di `c` diventa `{F3}`:

```
a: {F1, F2}
b: {F1, F2}
c: {F3}
```

Questo e' il punto centrale del capitolo: lo stesso vincolo logico, espresso in due modi, produce
deduzioni diverse. Il vincolo globale ha visto qualcosa che la somma dei vincoli binari non poteva
vedere.

---

## 5. I vincoli globali, e perche' sono il vero valore della CP

Un **vincolo globale** e' un vincolo su un numero arbitrario di variabili che porta con se' un
algoritmo di propagazione specializzato. Non e' zucchero sintattico sopra una decomposizione: la
decomposizione esiste ed e' piu' debole, come hai appena visto.

**AllDifferent.** Tutte le variabili prendono valori distinti. L'algoritmo di propagazione
classico lo vede come un problema di **matching bipartito**, cioe' di accoppiamento su un grafo a
due strati: variabili a sinistra, valori a destra, un arco quando quel valore e' ancora nel
dominio di quella variabile. Una soluzione del vincolo e' un accoppiamento che satura tutte le
variabili. Si calcola un matching massimo con un algoritmo a cammini aumentanti (il metodo
classico costa nell'ordine del numero di archi per la radice del numero di nodi), e poi si rimuove
ogni arco che non appartiene a nessun matching massimo, perche' corrisponde a un valore che non
puo' stare in nessuna soluzione; quegli archi si individuano orientando il grafo rispetto al
matching e guardando le componenti fortemente connesse. Nell'esempio della sezione 4, nessun
matching massimo assegna `F1` o `F2` a `c`.

**NoOverlap.** Un insieme di intervalli non si sovrappone: e' la risorsa unaria, cioe' le missioni
dello stesso mezzo. La decomposizione richiederebbe una disgiunzione con big M per ogni coppia di
intervalli, cioe' un numero quadratico di variabili aggiuntive e il rilassamento pessimo della
sezione 1.

**Cumulative.** Un insieme di intervalli, ciascuno con una richiesta, non supera mai una capacita'
in nessun istante. E' il vincolo che servirebbe per dire quanti velivoli possono pescare insieme
dallo stesso invaso. I propagatori buoni ragionano sull'energia, cioe' sul prodotto durata per
richiesta dentro una finestra temporale: se l'energia richiesta da un gruppo di attivita' eccede
quella disponibile nella finestra, qualcosa va spostato, e il propagatore lo deduce senza provare.

**Element.** Lega un indice variabile a un valore preso da una lista, `y = lista[x]` con `x` a sua
volta variabile. Si scriverebbe `t_ciclo = tempi_ciclo[indice_sorgente]`, e il motore propaga in
entrambi i sensi, perche' restringere `t_ciclo` restringe le sorgenti possibili e viceversa.

**Circuit.** Dato un insieme di archi booleani su un grafo, impone che gli archi scelti formino un
unico circuito che visita tutti i nodi. E' il vincolo delle rotte. Il suo valore sta nel "unico":
porta con se' la propagazione che vieta i sottogiri, cioe' i cicli chiusi che non passano dal
deposito, e che in PLI richiede famiglie di disuguaglianze aggiunte dinamicamente.

In OR-Tools si chiamano `AddAllDifferent`, `AddNoOverlap`, `AddCumulative`, `AddElement` e
`AddCircuit`.

### Quali di questi usa davvero Tagliafuoco

Conviene dirlo con precisione, perche' a un colloquio la domanda arriva e l'elenco dei vincoli
disponibili non e' l'elenco dei vincoli usati.

`optimize/model.py` usa un costrutto solo di questa famiglia: `NewOptionalIntervalVar` accoppiato a
`AddNoOverlap`. Un intervallo opzionale esiste soltanto se un letterale di presenza e' vero, e quel
letterale e' esattamente la variabile di assegnamento `assign[v][f]`: se il mezzo non va su quel
focolaio l'intervallo non c'e' e non occupa la macchina. Per ogni mezzo si mette un `AddNoOverlap`
sui suoi intervalli, ed e' cosi' che si dice "una missione alla volta" senza scrivere una sola
disgiunzione a mano. E' la struttura classica dello scheduling di officina, dove ogni mezzo e' una
macchina e ogni missione un lavoro opzionale.

```python
from ortools.sat.python import cp_model

model = cp_model.CpModel()
# una missione esiste solo se il mezzo e' assegnato a quel focolaio
missioni = [
    model.NewOptionalIntervalVar(inizio[i], durata[i], fine[i], assign[i], f"missione_{i}")
    for i in range(n)
]
model.AddNoOverlap(missioni)   # questo mezzo: una missione alla volta
```

Gli altri quattro non compaiono, ciascuno per una ragione diversa che vale la pena saper dire.

`AllDifferent` non c'e' perche' nel modello non esiste nessun gruppo di variabili che debba
prendere valori tutti distinti: un mezzo puo' servire due focolai e un focolaio puo' ricevere piu'
mezzi, cioe' proprio le ipotesi dell'assegnamento classico che il capitolo 04 smonta una per una.
L'esempio della sezione 4 lo usa perche' e' il modo piu' pulito di far vedere la differenza fra un
propagatore globale e la sua decomposizione, non perche' stia nel codice: saperlo distinguere e'
esattamente cio' che separa chi ha capito il meccanismo da chi ha imparato l'elenco.

`Cumulative` non c'e' perche' il modello non rappresenta la contesa sulla sorgente d'acqua: due
velivoli che pescano nello stesso lago nello stesso minuto, qui, non si disturbano. E' una
semplificazione dichiarata, non una svista, e ha un verso preciso: rende il piano piu' ottimista
del vero, quindi va ricordata quando si leggono i risultati. Il turno non usa `Cumulative` perche'
non ne ha bisogno: i minuti di turno consumati da un mezzo sono una somma di durate confrontata con
`max_shift_minutes`, cioe' una disuguaglianza lineare, e per una risorsa che non ha un profilo nel
tempo il vincolo cumulativo sarebbe piu' grosso e non piu' forte.

`Element` non c'e' perche' la sorgente d'acqua non e' una variabile di decisione.
`optimize/geometry.py` sceglie in anticipo la sorgente migliore per ogni coppia mezzo-focolaio, con
il criterio `tempo di ciclo diviso affidabilita' stagionale`, e passa al modello un tempo di ciclo
gia' fissato. La riduzione e' legittima perche' la sorgente migliore per una coppia non dipende da
come sono assegnate le altre coppie: lasciarla al solver moltiplicherebbe le variabili per il
numero di sorgenti senza quasi mai cambiare la risposta. Diventerebbe una decisione vera, e li'
servirebbe `Element`, solo nel momento in cui il modello rappresentasse la contesa sulla sorgente,
cioe' insieme a `Cumulative`.

`Circuit` non c'e' perche' nel modello non esiste un giro da ordinare. Per la sezione 6 di
`SPEC.md` ogni missione comincia e finisce in base, quindi le missioni di un mezzo sono intervalli
separati e non le tappe di un percorso: `NoOverlap` piu' i tempi di riattrezzamento dicono tutto
quello che c'e' da dire sulla sequenza. `Circuit` tornerebbe utile se un mezzo potesse andare da un
focolaio al successivo senza rientrare, cioe' in un modello di routing vero.

Una nota di modellazione che vale anche fuori da questo progetto: `AddCumulative` con capacita' 1 e
richieste tutte pari a 1 esprime la stessa cosa di `AddNoOverlap`, ma il propagatore di `NoOverlap`
e' specializzato per quel caso e taglia di piu'. Scegliere il vincolo piu' stretto che descrive il
problema e' una delle poche decisioni di modellazione che cambia davvero i tempi di risoluzione.

---

## 6. CP-SAT sotto il cofano

CP-SAT e' il risolutore a vincoli di OR-Tools, ed e' **ibrido**: verso chi scrive il modello parla
il linguaggio della programmazione a vincoli, ma internamente traduce buona parte del lavoro in
**SAT**, il problema di soddisfacibilita' booleana, e usa un risolutore SAT moderno per la ricerca.

**CDCL.** Il motore SAT e' di tipo *conflict driven clause learning*, apprendimento di clausole
guidato dai conflitti. Il ciclo e': assegna un letterale, propaga le conseguenze immediate, e
quando arriva a una contraddizione analizza il grafo delle implicazioni che l'ha causata,
costruisce una **clausola di conflitto** che riassume la combinazione di scelte responsabile, la
aggiunge in modo permanente al problema, e salta indietro non di un livello ma fino al punto in
cui quella clausola diventa informativa. L'effetto e' che lo stesso errore non viene rifatto in un
altro ramo: e' memoria della ricerca.

**Lazy clause generation.** Tradurre in anticipo un vincolo di alto livello in clausole booleane
produrrebbe troppe clausole: descrivere per esteso un `Cumulative` su 240 minuti e' un'esplosione.
La generazione pigra risolve cosi': il vincolo resta un propagatore, e ogni volta che deduce
qualcosa produce anche una **spiegazione**, cioe' l'insieme di letterali che hanno causato la
deduzione. La spiegazione diventa una clausola e va al motore CDCL. Le clausole si generano solo
quando servono, e per di piu' entrano nel meccanismo di apprendimento: e' la ragione tecnica per
cui CP-SAT e' molto piu' forte di un risolutore CP classico.

**Anche un rilassamento lineare, contro quanto dice la sezione 2.** CP-SAT costruisce e risolve un
rilassamento continuo di una parte del modello e ne genera tagli, esattamente come farebbe un
branch and cut. Non e' un dettaglio nascosto: i parametri del risolutore espongono un
`linearization_level` che decide quanto modello linearizzare, e una famiglia di interruttori per
le classi di tagli (`add_mir_cuts`, `add_clique_cuts`, `add_cg_cuts` e altri). E' il motivo per
cui CP-SAT dispone di un limite dimostrato sul valore ottimo, quindi di un gap, cosa che un
risolutore CP puro fatica a dare. Quando dici a un colloquio che CP-SAT "non rilassa niente" stai
descrivendo la CP degli anni novanta, non CP-SAT.

**Presolve.** Prima di cercare, CP-SAT riscrive il modello: elimina variabili fissate, fonde
variabili equivalenti, rafforza i coefficienti, rimuove vincoli ridondanti, individua simmetrie.
Su un modello scritto in modo ingenuo riduce spesso le dimensioni in modo drastico, ed e' il
motivo per cui conviene scrivere il modello in modo leggibile e lasciare la pulizia al risolutore.

**Ricerca a portafoglio.** Con piu' di un worker, CP-SAT non spezza lo spazio di ricerca in parti
da distribuire: lancia **strategie diverse in parallelo** sullo stesso problema. Un worker cerca
soluzioni buone in fretta, un altro alza il limite per dimostrare l'ottimalita', altri usano
ordini di ramificazione diversi, e tutti si scambiano clausole apprese e limiti.

**Large neighborhood search integrata.** Alcuni worker fanno LNS: prendono la migliore soluzione
nota, ne congelano una parte grande e riottimizzano il resto in modo esatto, ripetendo con parti
diverse. E' la metaeuristica descritta nel capitolo 04, qui dentro un risolutore esatto, e spiega
perche' CP-SAT trova soluzioni molto buone molto presto.

---

## 7. Interi: la scalatura di tempi, litri e pesi

Le variabili e i vincoli di CP-SAT sono **soltanto interi**: booleane o intere, coefficienti
interi, nessuna variabile continua. E' la restrizione che rende possibile tutto il ragionamento
delle sezioni precedenti, cioe' domini finiti, rimozione di valori, spiegazioni booleane, e ha
conseguenze concrete sul modello di Tagliafuoco.

**I tempi vanno scalati.** Il tempo di ciclo di CAN_01 che pesca al largo di Scalea per il
focolaio di Cirella, calcolato con le formule di `SPEC.md`, e' 6,704 minuti: non 6 e non 7. In
minuti interi si perde precisione dove conta, perche' l'errore si moltiplica per il numero di
lanci: sui ventotto lanci del controesempio del capitolo 04, arrotondare sette decimi per difetto
regalerebbe al solver quasi venti minuti di autonomia che il mezzo non ha. Il progetto ha fissato
l'unita' in `optimize/geometry.py` e sono i decimi di minuto, cioe' sei secondi:

```python
import math

TIME_SCALE = 10                                  # si lavora in decimi di minuto
t_ciclo_scalato = math.ceil(6.7 * TIME_SCALE)    # 67 decimi, arrotondato per eccesso
```

Un dettaglio che sembra pedanteria e non lo e'. Una durata che vale esattamente 24,0 minuti puo'
arrivare alla conversione come 24,000000000000004, per come i numeri in virgola mobile sono
rappresentati in binario, e `ceil` la porterebbe a 241 decimi invece di 240. Il modulo sottrae
percio' un epsilon di 10^-9 prima di arrotondare per eccesso: e' un miliardesimo di unita' di
tempo, cioe' abbastanza grande da mangiarsi l'errore di rappresentazione e troppo piccolo per
mangiarsi un arrotondamento vero, che vale sempre almeno qualche millesimo di unita'.

**La direzione dell'arrotondamento non e' indifferente.** La regola e': arrotonda sempre dalla
parte che rende il piano piu' difficile. Le durate per eccesso (`to_units_up`), i budget come
autonomia, turno e orizzonte per difetto (`to_units_down`). Se sbagli verso, il solver produce
piani che il validatore di `validate/` respinge, e il sintomo si vede come un tasso di rifiuto che
sale senza motivo apparente. Il controesempio del capitolo 04 fa vedere quanto puo' essere stretto
il margine: il Canadair arriva sul focolaio di Cirella al minuto 29,3, e se l'ordine avesse
imposto mezz'ora invece di quaranta minuti quel piano passerebbe per sette decimi. Arrotondando
nel verso sbagliato passerebbe un piano che nella realta' non ce la fa.

**I litri sono gia' interi**, e non a caso: in `models.py` i campi sono dichiarati
`water_demand_liters: int` e `water_capacity_liters: int`. La formula del fabbisogno produce un
float e la conversione avviene una volta sola, a monte, non dentro il solver.

**I pesi dell'obiettivo sono float, e qui va fatta una precisazione.** `OBJECTIVE_WEIGHTS`
contiene 10.0, 8.0, 4.0, 3.0, 1.0. Non e' vero che CP-SAT rifiuti un obiettivo con coefficienti
non interi: la versione usata dal progetto, la 9.11.4210, accetta `model.Minimize(2.5 * x + 1.3 * y)`
senza protestare e riscrive internamente quell'obiettivo in uno intero, scalandolo. Il progetto
sceglie comunque di scalare per conto suo, per tenersi il controllo: la conversione automatica
decide da sola il fattore e il verso dell'arrotondamento, mentre il limite e il gap che leggerai si
riferiscono all'obiettivo che il risolutore ha davvero dentro. Meglio che quell'obiettivo sia
quello che hai scritto tu.

Il fattore e' dichiarato in `optimize/objective.py`:

```python
OBJECTIVE_PRECISION = 1_000_000
```

Ogni coefficiente diventa `peso * OBJECTIVE_PRECISION * quota / denominatore`, arrotondato a
intero, dove il denominatore e' il massimo teorico di quel termine. Un termine normalizzato al
massimo vale quindi `peso * 1_000_000`: con pesi che arrivano a 10 si resta sotto i 10^7 per
termine, molto dentro l'intero a 64 bit anche moltiplicando per domini da qualche migliaio. Il
valore dell'obiettivo che finisce in `Plan.objective_value` viene ridiviso per lo stesso milione,
cosi' chi legge il piano vede il numero nella scala in cui i pesi sono scritti. Il capitolo 06
discute i denominatori insieme al resto del modello.

---

## 8. Stati di uscita, limite di tempo, determinismo

### Gli stati

`solver.Solve(model)` restituisce uno di cinque stati, e un sistema serio ne tratta cinque.

**OPTIMAL.** Il risolutore ha finito secondo il criterio che gli hai dato. Nel caso normale
significa che esiste una soluzione ed e' dimostrato che nessuna e' migliore. Attenzione pero': se
hai impostato `relative_gap_limit` o `absolute_gap_limit` e la soglia viene raggiunta, CP-SAT
riporta comunque `OPTIMAL` con un gap non nullo. Per sapere quanto e' buono il piano leggi il
limite, non lo stato. In ogni caso la soluzione va passata al validatore indipendente: ottimale
rispetto al modello non significa fisicamente possibile, e se il modello ha un errore di
formulazione questo e' proprio il caso in cui non te ne accorgi.

**FEASIBLE.** C'e' una soluzione ma il tempo e' scaduto prima della dimostrazione. E' l'esito
normale con `SOLVER_MAX_SECONDS = 20.0`. Qui va detto con precisione cosa fa il codice oggi:
`optimize/solver.py` scrive nel piano il valore dell'obiettivo (`Plan.objective_value`, riportato
alla scala dei pesi dividendo per `OBJECTIVE_PRECISION`), lo stato e i secondi impiegati, e annota
che il piano e' il migliore trovato entro il limite. Non legge pero' `solver.BestObjectiveBound()`,
quindi il gap non arriva a chi legge il piano. E' una riga che vale la pena aggiungere, perche' il
gap e' l'unica informazione che rende difendibile un piano interrotto: senza, la garanzia esiste
dentro il risolutore e muore li'.

**INFEASIBLE.** Non esiste nessuna soluzione che rispetta i vincoli duri. Non e' un fallimento
della ricerca ma una dimostrazione, e la sezione 9 dice cosa farne.

**MODEL_INVALID.** Il modello e' malformato: un intervallo con durata negativa, un dominio vuoto
alla costruzione, un coefficiente fuori dall'intervallo rappresentabile. E' un errore del
programmatore, non una condizione operativa: va trattato come guasto interno e coperto da test in
`tests/test_optimize.py`.

**UNKNOWN.** Il tempo e' scaduto senza nessuna soluzione e senza prova di infattibilita'. Qui
conviene dire esattamente cosa fa il codice, perche' la differenza fra quello che fa e quello che
si potrebbe fare e' proprio il tipo di cosa che a un colloquio si sbaglia. `optimize/solver.py`
restituisce un `Plan` vuoto con `producer = "cpsat"` e una nota che invita ad alzare il limite di
tempo: non inventa un piano e non ne prende uno da un'altra parte. Il ripiego sulla baseline di
`baseline/` e' una decisione di chi chiama il risolutore, non del risolutore, e se viene presa deve
comparire in `Plan.producer`, perche' un piano che sembra venire dal solver e non ci viene e' il
peggiore degli errori di progetto. La stessa regola vale per `MODEL_INVALID` e per `INFEASIBLE`
senza ordini rilassabili: si restituisce un piano vuoto che dice perche' e' vuoto, e restituire un
risultato e' sempre preferibile a sollevare un'eccezione.

### Il limite di tempo

`SOLVER_MAX_SECONDS = 20.0` e' una scelta dichiarata, con la motivazione scritta accanto in
`config.py`, e l'argomento e' operativo prima che matematico: mentre il risolutore cerca l'ottimo,
la situazione cambia. Il satellite ripassa, il vento gira. Un piano buono disponibile al secondo
ventesimo entra nel processo decisionale; un piano ottimo disponibile al minuto dieci arriva
quando lo scenario su cui era ottimo non esiste piu'.

CP-SAT offre anche `relative_gap_limit`, che ferma la ricerca quando il gap scende sotto una
soglia invece che quando scade il tempo. Il limite di tempo garantisce il momento della risposta,
il limite di gap ne garantisce la qualita': in un sistema con vincoli di latenza vince il primo.

### Il determinismo

Con `SOLVER_NUM_WORKERS = 8` due esecuzioni sullo stesso identico modello possono restituire piani
diversi. La ragione e' il portafoglio della sezione 6: i worker corrono davvero in parallelo,
quale trovi per primo una soluzione dipende dallo scheduling dei thread del sistema operativo, e
quella soluzione cambia il limite e le clausole condivise con gli altri, quindi cambia la loro
ricerca. Entrambi i piani sono validi e hanno valore simile, ma non sono lo stesso piano. Per
inciso, il valore 0 del parametro non significa "nessun worker" ma "usane quanti sono i core della
macchina", che e' il modo piu' rapido per rendere i risultati dipendenti dall'hardware.

Per la valutazione questo e' inaccettabile. La sezione 7 di `SPEC.md` prescrive di confrontare
Tagliafuoco e la baseline sugli stessi scenari con lo stesso seed: se il numero che pubblichi
cambia quando rilanci, non hai misurato un miglioramento, hai misurato il rumore.

Il progetto elimina il non determinismo partendo da due costanti gia' dichiarate in `config.py`:

```python
SOLVER_RANDOM_SEED = 20250915
SOLVER_NUM_WORKERS = 8                  # uso interattivo
SOLVER_NUM_WORKERS_DETERMINISTIC = 1    # valutazioni riproducibili
```

Il seed fissa le scelte pseudo-casuali interne, un solo worker elimina la corsa fra thread. Serve
pero' un terzo accorgimento, facile da dimenticare: un limite a orologio non e' riproducibile,
perche' quanti nodi il risolutore esplora in venti secondi dipende da quanto e' carica la
macchina. CP-SAT espone per questo un tempo *deterministico*, `max_deterministic_time`, che conta
lavoro svolto invece di secondi. La funzione `_make_solver` di `optimize/solver.py` lo imposta
insieme al limite a orologio, ma solo in modalita' deterministica: nell'uso interattivo conta il
tempo di parete, perche' li' il vincolo e' quanto aspetta una persona.

Un dettaglio da sapere prima di leggere il codice: il parametro che il progetto scrive si chiama
`num_search_workers`, che e' il nome storico, mentre nelle versioni recenti di OR-Tools quello in
uso e' `num_workers`. Nella 9.11 esistono tutti e due, entrambi con default zero, e scrivere il
vecchio funziona. E' il tipo di eredita' che conviene riconoscere prima di trovarsela in mezzo a un
log.

Il prezzo si paga in qualita': un worker e' molto piu' lento di otto, quindi a parita' di tempo il
gap sara' peggiore. Vale la pena pagarlo, perche' riguarda i numeri che il progetto pubblica.

Esiste un'alternativa che conviene conoscere, il parametro `interleave_search`: e' presentato come
sperimentale e fa correre le strategie a lotti sincronizzati invece che libere, cosa che toglie di
mezzo la corsa fra thread senza rinunciare ai worker. Il progetto non lo usa, perche' preferisce
una garanzia che non dipenda da un parametro marcato sperimentale in una versione di libreria che
`SPEC.md` vieta di aggiornare.

---

## 9. Infattibilita': diagnosi e strategia del progetto

**Che cosa significa INFEASIBLE.** Non significa che il risolutore non ha trovato niente:
significa che ha dimostrato che non esiste nulla. Ogni possibile assegnazione viola almeno un
vincolo duro.

Succede piu' spesso di quanto sembri, e quasi sempre per una ragione sensata. Il coordinatore dice
"tutti i focolai vicino alle case entro quindici minuti" e in quel momento ci sono quattro focolai
in interfaccia urbano-foresta distribuiti fra Pollino e Aspromonte, a duecento chilometri l'uno
dall'altro: nessuna flotta reale ce la fa. L'ordine e' ragionevole come intenzione e impossibile
come vincolo.

**Come si diagnostica.** Lo strumento teorico e' il **sottoinsieme minimale infattibile**: un
insieme di vincoli che e' infattibile e che diventa fattibile se ne togli uno qualunque. E' la
risposta alla domanda "quale ordine ha rotto il piano", perche' identifica il gruppo di richieste
incompatibili invece di dire genericamente che qualcosa non torna.

In CP-SAT lo strumento nativo sono le **assunzioni**. Si associa a ogni vincolo sospetto un
letterale di attivazione, si rende il vincolo condizionato a quel letterale, e si dichiara il
letterale come assunzione:

```python
attivatori = {}                                    # indice del letterale -> constraint_id
for c in vincoli_sospetti:
    lit = model.NewBoolVar(f"attiva_{c.constraint_id}")
    model.Add(arrivo[c.fire_id] <= c.minutes).OnlyEnforceIf(lit)
    model.AddAssumption(lit)
    attivatori[lit.Index()] = c.constraint_id

if solver.Solve(model) == cp_model.INFEASIBLE:
    colpevoli = [attivatori[i] for i in solver.SufficientAssumptionsForInfeasibility()]
```

Nota il dizionario: `SufficientAssumptionsForInfeasibility` non restituisce le variabili che hai
creato ma una sequenza di indici interi dei letterali dentro il modello, quindi la mappa dagli
indici ai `constraint_id` va tenuta tu. Senza quella mappa hai in mano dei numeri e non dei nomi
da mostrare al coordinatore.

Il sottoinsieme restituito e' sufficiente a rendere il modello infattibile, ma non e' garantito
minimale: per arrivare al minimale si itera togliendo un'assunzione alla volta e ririsolvendo,
tecnica che si chiama filtraggio per cancellazione. Siccome i vincoli tradotti sono pochi, il
costo sarebbe accettabile.

**Quello che fa Tagliafuoco, che non e' questo.** Conviene dirlo chiaramente, perche' l'elenco di
cio' che un risolutore offre non e' l'elenco di cio' che un progetto usa. `optimize/solver.py` non
chiama `AddAssumption`. Quando la prima risoluzione torna `INFEASIBLE` e ci sono ordini duri
davvero applicati, il modulo ricostruisce il modello da zero con quegli ordini trasformati in
penalita' carissime (`RELAXED_HARD_WEIGHT = 200.0`, cioe' molto piu' della somma di tutti i pesi
dell'obiettivo, che fa 26) e risolve una seconda volta.

La ragione della scelta e' che le assunzioni diagnosticano e basta: ti dicono quali ordini
litigano, e poi ti serve comunque un'altra risoluzione per avere un piano da mostrare. La seconda
passata fa le due cose insieme: restituisce un piano e, siccome il risolutore paga una penalita'
ogni volta che viola un ordine, viola il meno possibile e dichiara in `Plan.notes` quali ordini ha
dovuto lasciar cadere. Il prezzo e' che la lista degli ordini rilassati non e' un sottoinsieme
minimale dimostrato, ma l'insieme di quelli che erano candidati al rilassamento. Se servisse la
diagnosi precisa, cioe' "questi due ordini insieme sono incompatibili, uno per uno no", le
assunzioni sarebbero la strada.

Due dettagli di implementazione che il codice cura e che sono facili da sbagliare. Un ordine duro
il cui selettore non pesca niente in questo scenario ("escludete gli anfibi" dove di anfibi non ce
n'e') non e' diventato un vincolo, quindi non si conta fra i rilassabili: senza quel filtro si
pagherebbe una seconda risoluzione per scoprire di non avere niente da rilassare, e si scriverebbe
nel piano che la colpa e' dell'ordine quando invece e' della fisica. E la lista dei rilassati si
scrive solo se la seconda passata ha davvero prodotto un piano: annotare "il piano viola
consapevolmente questi ordini" sotto un risultato vuoto sarebbe una nota falsa.

**La strategia del progetto.** La regola e' asimmetrica, e l'asimmetria e' il punto.

I vincoli che vengono dalla fisica e dall'organizzazione (autonomia, turni, capacita' del
serbatoio, volo diurno) restano **sempre duri**. Rilassarli produrrebbe piani impossibili, e un
simulatore che produce piani impossibili non e' ottimista, e' rotto.

I vincoli che vengono dal traduttore, cioe' quelli che nel `ConstraintSet` hanno
`Enforcement.HARD`, vengono **trasformati in morbidi** quando il modello risulta infattibile. Il
vincolo non sparisce: entra nella funzione obiettivo con una penalita' alta, cosi' il risolutore
lo rispetta ogni volta che puo' e lo viola solo dove e' costretto. Lo schema prevede gia' questa
distinzione con l'enumerazione `Enforcement` e le proprieta' `ConstraintSet.hard` e `.soft`.

Le ragioni dell'asimmetria sono due. Un ordine impossibile e' un'informazione da restituire al
coordinatore, non un errore da nascondere: sapere che il tetto dei quindici minuti non si puo'
rispettare su tre focolai su quattro e' precisamente cio' che serve per riformulare l'ordine. E
quell'ordine e' passato per un modello linguistico, quindi la possibilita' che sia stato tradotto
in modo piu' stretto dell'intenzione e' reale.

**La degradazione va dichiarata.** Il rilassamento finisce in `Plan.notes` e deve arrivare fino
all'interfaccia, con il `source_span` del vincolo che dice al coordinatore quale pezzo del suo
ordine non e' stato applicato. Un sistema che rilassa in silenzio e' peggiore di uno che si
rifiuta di rispondere, perche' restituisce un piano che sembra obbediente e non lo e'.

---

## 10. Quando invece sceglieresti un MILP

Un confronto onesto richiede di dire anche quando CP-SAT non e' la scelta giusta.

**Sceglieresti un risolutore lineare intero** come CBC, HiGHS o Gurobi quando il modello contiene
variabili continue vere (quantita', flussi, miscele), perche' li' la restrizione agli interi di
CP-SAT ti costringerebbe a discretizzare qualcosa che non e' discreto. Lo sceglieresti quando il
rilassamento continuo e' stretto, perche' in quel caso il branch and cut pota benissimo. E lo
sceglieresti quando ti servono le informazioni duali: prezzi ombra, analisi di sensitivita',
generazione di colonne. Qui la sfumatura e' precisa: CP-SAT un rilassamento lineare lo risolve,
come dice la sezione 6, ma non te ne espone i duali, mentre un MILP ti dice quanto varrebbe
rilassare di un'unita' un vincolo, e in certi contesti quell'informazione vale piu' della
soluzione. Infine, su istanze enormi ma poco combinatorie, come i grandi problemi di trasporto o
di flusso, un buon MILP e' difficile da battere.

**Sceglieresti CP-SAT**, come fa questo progetto, quando tutte le decisioni sono intere e i domini
finiti; quando la struttura e' disgiuntiva, piena di alternative e attivazioni condizionate;
quando c'e' scheduling con risorse, perche' `NoOverlap` e `Cumulative` incorporano decenni di
lavoro algoritmico che in PLI dovresti ricostruire a mano con i big M; e quando serve una buona
soluzione in fretta piu' che un ottimo certificato.

Tagliafuoco cade con chiarezza nella seconda colonna: le decisioni sono tutte intere (chi va dove,
quanti lanci, da quale sorgente, a che minuto), la struttura e' scheduling con risorse, e i
vincoli del coordinatore sono disgiunzioni quasi per definizione. Si aggiunge una ragione pratica
per un progetto come questo: OR-Tools si installa con `pip` ed e' gratuito, mentre i risolutori
commerciali richiedono licenze che chi clona il repository non ha.

Una nota tecnica: `SPEC.md` fissa `ortools==9.11.4210` e vieta l'aggiornamento perche' la 9.15 va
in segmentation fault su questa macchina durante `Solve()`. Sapere che una dipendenza e' bloccata
e sapere *perche'* sono due cose diverse, e solo la seconda e' difendibile a un colloquio.

Se un giorno il modello guadagnasse variabili continue vere, per esempio litri frazionari o costi
continui, la scelta andrebbe rivista: non e' presa una volta per sempre, e' presa per questo
modello.

---

## 11. Cosa devi saper spiegare

**Perche' CP-SAT e non un risolutore lineare intero?**
Perche' il nostro modello e' interamente intero e pieno di disgiunzioni. In PLI ogni "o questa
missione prima o quella prima" richiede la tecnica del big M, che indebolisce il rilassamento
continuo e rende inutile il limite su cui il branch and bound si regge. CP-SAT ha vincoli globali
come `NoOverlap` e `Cumulative` con propagatori specializzati, e lavora per propagazione sui
domini. Su un modello con variabili continue vere, o dove servono i duali, la risposta sarebbe
l'opposta.

**Che cos'e' la propagazione, e perche' AllDifferent non e' zucchero sintattico?**
La propagazione e' la rimozione dai domini dei valori che non possono stare in nessuna soluzione
di un vincolo, ripetuta finche' non si deduce piu' niente. `AllDifferent` non e' zucchero perche'
il suo propagatore globale deduce cose che la decomposizione in disuguaglianze binarie non deduce:
se due variabili hanno solo i valori F1 e F2, il propagatore globale toglie F1 e F2 a tutte le
altre, mentre la consistenza d'arco sui vincoli binari non toglie niente perche' ogni valore ha
ancora un supporto. L'algoritmo classico lo fa riducendo il vincolo a un matching bipartito
massimo e rimuovendo gli archi che non appartengono a nessun matching massimo. Da dire subito dopo,
perche' e' la domanda che segue: nel nostro modello `AllDifferent` non compare, perche' nessun
gruppo di variabili deve prendere valori tutti distinti. Lo usiamo come esempio didattico, e
l'unico vincolo globale che il modello usa davvero e' `NoOverlap` sulle missioni di ciascun mezzo.

**Che cos'e' la lazy clause generation?**
CP-SAT usa internamente un risolutore SAT di tipo CDCL. Codificare in anticipo tutti i vincoli di
alto livello in clausole booleane produrrebbe un'esplosione, quindi i vincoli restano propagatori,
e ogni volta che uno deduce qualcosa produce anche la spiegazione della deduzione, cioe' i
letterali che l'hanno causata. Quella spiegazione diventa una clausola, generata solo quando
serve, e cosi' le deduzioni di alto livello entrano nel meccanismo di apprendimento dei conflitti.

**Un risolutore a vincoli non fa rilassamenti continui: come fa CP-SAT a darvi un gap?**
Questa e' la domanda con la trappola dentro, perche' la premessa e' falsa per CP-SAT. Il CP
classico non rilassa, ma CP-SAT e' ibrido e costruisce un rilassamento lineare di parte del
modello, ci genera tagli, e da li' ricava il limite dimostrato che confrontiamo con l'incumbent
per calcolare il gap. Lo si vede dai suoi stessi parametri, che espongono un livello di
linearizzazione e diverse famiglie di tagli.

**Perche' con otto worker il risultato cambia fra esecuzioni, e come lo togliete?**
Perche' CP-SAT in parallelo non suddivide lo spazio di ricerca: lancia strategie diverse che
competono e si scambiano clausole e limiti. Quale worker trovi per primo una soluzione dipende
dallo scheduling dei thread, e quella soluzione cambia la ricerca degli altri. Per la valutazione
lo eliminiamo con `random_seed` fissato, un solo worker e un limite di tempo deterministico
accanto a quello a orologio, perche' i secondi di parete dipendono dal carico della macchina.
Paghiamo in qualita' a parita' di tempo, ma senza determinismo il confronto con la baseline non
sarebbe riproducibile, e un numero che cambia quando rilanci non e' una misura.

**Il solver restituisce INFEASIBLE: e' un bug?**
Quasi mai. Significa che il risolutore ha dimostrato che nessun piano rispetta tutti i vincoli
duri, e di solito la causa e' un ordine ragionevole come intenzione e impossibile come vincolo. La
reazione e' asimmetrica: i vincoli fisici restano duri, perche' rilassarli produrrebbe piani
impossibili, mentre quelli che vengono dal traduttore vengono ricostruiti come penalita' molto
care e il modello si risolve una seconda volta, dichiarando nelle note del piano quali ordini sono
stati lasciati cadere. Non usiamo le assunzioni di CP-SAT, che pure sono lo strumento nativo per
questo: loro dicono quali vincoli litigano ma non producono un piano, e a noi serve rispondere al
coordinatore con qualcosa in mano. Se servisse la diagnosi esatta, cioe' il sottoinsieme minimale
infattibile, quella e' la strada da prendere.

**Perche' vi fermate a venti secondi, e cosa fate se non trovate niente?**
Perche' mentre il risolutore cerca, lo scenario cambia: il satellite ripassa, il vento gira. Un
piano buono al secondo ventesimo entra nella decisione, un piano ottimo al minuto dieci arriva
quando lo scenario non esiste piu'. Se allo scadere del tempo abbiamo una soluzione lo stato e'
FEASIBLE e pubblichiamo il piano dicendo che e' il migliore trovato entro il limite, non il
migliore che esista. Il numero che rende difendibile quella frase e' il gap, e oggi va letto dal
risolutore perche' il piano non lo porta ancora con se'. Se non ne abbiamo nessuna lo stato e' UNKNOWN, e
il risolutore restituisce un piano vuoto che dice perche' e' vuoto: il ripiego sulla baseline e'
una scelta di chi lo chiama, e quando viene fatta deve comparire in `Plan.producer`, perche' un
piano che sembra venire dal solver e non ci viene e' peggio di nessun piano.

---

## 12. Per approfondire

Il riferimento piu' utile e' la documentazione ufficiale di CP-SAT in OR-Tools, che oltre ai
manuali d'uso contiene una guida alla modellazione scritta da chi il risolutore lo sviluppa, con
consigli concreti su come scrivere i vincoli perche' presolve e propagatori lavorino bene.
Leggila dopo questo capitolo: senza il vocabolario delle sezioni 3 e 5 sembra un elenco di
funzioni. Accanto alla documentazione tieni aperto il file dei parametri del risolutore,
`sat_parameters.proto`, che sta nel codice sorgente di OR-Tools: ogni parametro ha il suo commento,
ed e' la fonte piu' affidabile per domande come "che cosa significa esattamente questo gap" o
"questo parametro e' sperimentale". Nel pacchetto installato con `pip` ne trovi solo la versione
compilata, `sat_parameters_pb2.py`, che i commenti non li porta: i nomi e i valori di default si'
(li puoi stampare da Python), le spiegazioni no.

Sui fondamenti della programmazione a vincoli, cerca un manuale che tratti nell'ordine domini,
propagatori, livelli di consistenza e vincoli globali, e che dedichi un capitolo ai propagatori di
`AllDifferent` e `Cumulative`. La costruzione di `AllDifferent` basata sul matching bipartito e'
un risultato classico della disciplina, spiegato per esteso in qualunque testo serio.

Sui risolutori SAT vale la pena leggere una trattazione di CDCL che spieghi il grafo delle
implicazioni, l'analisi del conflitto e il backjumping non cronologico: e' la parte che rende
CP-SAT diverso dai risolutori CP classici, ed e' anche la piu' facile da raccontare male. Sullo
scheduling con risorse cerca invece la letteratura sui propagatori basati sull'energia e sul
metodo detto *edge finding*, che e' il cuore di `Cumulative`.

Infine, sulla diagnosi dell'infattibilita', il concetto da cercare e' il sottoinsieme minimale
infattibile, chiamato anche sistema infattibile irriducibile nella letteratura sulla
programmazione matematica. Le tecniche di estrazione sono le stesse in CP e in MILP.
