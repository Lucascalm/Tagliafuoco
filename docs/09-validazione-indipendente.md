# 09. La validazione indipendente

Questo capitolo spiega perche' in Tagliafuoco esiste un secondo programma il cui unico
compito e' dire di no a un piano appena prodotto dal primo, e che cosa quel no vale davvero.

Indice dei paragrafi:

1. Un piano che sembra giusto
2. Un test e un validatore non sono la stessa cosa
3. Il problema dell'oracolo
4. Quattro vie d'uscita
5. La programmazione a N versioni e il risultato scomodo
6. Il limite strutturale: quando sbaglia la specifica
    - 6.1 Una specifica ambigua fa divergere le due implementazioni: il caso vero
    - 6.1.1 E' successo una seconda volta, ed era peggio
    - 6.2 Il caso in cui le due implementazioni concordano, e come si tara la tolleranza
7. Che cosa rende indipendente il nostro validatore
8. Le regole di validazione e cosa protegge ciascuna
9. La tolleranza numerica
10. Difesa in profondita'
11. La verifica formale, e perche' qui non si usa
12. Cosa devi saper spiegare
13. Per approfondire

---

## 1. Un piano che sembra giusto

Il solver produce questo frammento di piano, alle quattordici di un pomeriggio di agosto.

```
vehicle_id              CAN-01 (Canadair, base Lamezia Terme)
fire_id                 F-2025-0731-SILA-03
departure_minute        0.0
arrival_minute          7.0
sorties                 12
water_delivered_liters  73644
release_minute          72.0
water_source_id         WS-LAGO-CECITA
```

Letto in fretta sembra ragionevole: un Canadair parte da Lamezia, arriva su un focolaio in
Sila, fa dodici sganci pescando al lago Cecita, si libera dopo poco piu' di un'ora.

Ricalcoliamo. Fra l'aeroporto di Lamezia Terme (38.905 Nord, 16.242 Est) e un focolaio nella
Sila Grande intorno a 39.350 Nord, 16.600 Est ci sono 58.33 km in linea d'aria, e a 333 km/h
di crociera il trasferimento costa `58.33 / 333 * 60 = 10.51 minuti`. Il piano dichiara di
arrivare al minuto 7.0, cioe' volando a 500 km/h, e a occhio nessuno se ne accorge perche' 7
e 10.5 sono entrambi plausibili. I 333 km/h di crociera e i 6137 litri per sgancio sono
valori nominali di riferimento per un Canadair, non dati certificati.

Il resto non quadra meglio. Il lago Cecita (39.383 Nord, 16.533 Est) dista 6.83 km dal
focolaio, quindi un ciclo d'acqua costa
`6.83 * 2 / 333 * 60 + 2.0 (pescaggio) + 1.0 (sgancio) = 5.46 minuti`, e dodici cicli ne
costano 65.53. Con setup (5), andata (10.51), cicli (65.53) e ritorno (10.51) la missione
dura 91.55 minuti, non i 72 dichiarati: il Canadair sarebbe ancora in volo per una ventina di
minuti quando il piano lo considera gia' rientrato.

Quale controllo lo prende non e' ovvio. La regola sui cicli d'acqua confronta il tempo passato
sul focolaio, 72 meno 7 cioe' 65 minuti, con i 65.53 ricalcolati: mancano 0.53 minuti, sotto
la tolleranza di un minuto, e non scatta niente. A prendere il piano sono le altre due: il
trasferimento dichiarato, 7 minuti contro 10.51, sfora di tre minuti e mezzo, e la missione
ricomposta, 72 contro 91.55, sfora di quasi venti. Le regole sono tante perche' ciascuna vede
una faccia dello stesso errore, e la faccia che si vede meglio non e' quella che si sospettava.

Nessun test scritto da chi ha costruito il solver avrebbe necessariamente scoperto questo,
perche' un test verifica i casi a cui il suo autore aveva pensato. Serve un programma che
prenda il piano finito, non sappia come e' stato prodotto, e ricalcoli tutto per conto suo.

## 2. Un test e un validatore non sono la stessa cosa

E' la domanda di colloquio piu' probabile su questo progetto, quindi va capita e non
memorizzata.

Un test unitario e' un esperimento fissato: chi scrive il codice sceglie un ingresso, scrive
a mano l'uscita attesa, verifica che il programma la produca. Gira in sviluppo, su dati
inventati, e la sua copertura e' limitata dall'immaginazione del suo autore. Non e' un
difetto, e' la definizione: un test e' un campione, e un campione scelto da una persona
eredita i punti ciechi di quella persona.

Un validatore e' un controllo permanente. Gira in produzione, su ogni uscita vera, inclusi
gli scenari che nessuno aveva previsto, e non confronta con un valore atteso: verifica una
proprieta' che deve valere sempre. Il criterio cambia di natura, da un valore atteso specifico
(questo mezzo arriva al minuto 10.5) a una proprieta' universale (nessun mezzo arriva prima di
distanza diviso velocita'). Non gli serve sapere quale risposta sia giusta, gli serve sapere
quali risposte sono impossibili.

Il validatore pero' non sostituisce i test: dice se il piano e' possibile, non se e' buono.
Se il solver peggiora la copertura da tre quarti a due terzi restando fisicamente coerente,
il validatore accetta tutto, e quel lavoro lo fanno i test e le metriche del capitolo 10.

## 3. Il problema dell'oracolo

Un oracolo, nel collaudo del software, e' il meccanismo che decide se un'uscita e' corretta.
Per una funzione che ordina una lista e' facile: il risultato deve essere ordinato e deve
essere una permutazione dell'ingresso. Per il nostro solver e' difficile per una ragione
strutturale: la risposta giusta al problema "come impiego questa flotta su questi focolai"
non esiste altrove, perche' calcolarla era il punto del programma. Se avessimo un secondo
modo semplice e sicuro di ottenerla, non ci servirebbe CP-SAT. Si chiama problema
dell'oracolo (test oracle problem) e riguarda compilatori, simulatori numerici, modelli di
apprendimento automatico e risolutori di ottimizzazione.

Qui ha due strati. La fattibilita' (il piano e' eseguibile, i tempi tornano, l'autonomia
regge, i litri non superano cio' che dodici sganci possono portare) ha una risposta oggettiva
e verificabile. L'ottimalita' (il piano e' il migliore possibile secondo la funzione
obiettivo) non ha una risposta verificabile a basso costo. Il validatore attacca il primo
strato e dichiara di non toccare il secondo.

## 4. Quattro vie d'uscita

### 4.1 Proprieta' invarianti

Invece di chiedere qual e' la risposta giusta, si chiede quali proprieta' deve avere
qualunque risposta giusta.

```python
# non e' un test: e' una proprieta' che deve valere su ogni piano prodotto
for a in plan.assignments:
    assert 0.0 <= a.departure_minute
    assert a.departure_minute <= a.arrival_minute <= a.release_minute
    assert a.release_minute <= plan.horizon_minutes
```

Un'invariante e' debole perche' non identifica la risposta giusta (un piano che assegna zero
mezzi le rispetta tutte) e fortissima perche' vale su infiniti casi e non costa
immaginazione.

### 4.2 L'asimmetria fra risolvere e verificare

Esistono problemi in cui trovare una soluzione costa molto e controllarne una proposta costa
poco. L'esempio da manuale e' il sudoku: riempire una griglia difficile richiede
ragionamento, controllarne una gia' riempita richiede di scorrere righe, colonne e blocchi
una volta. La classe NP formalizza l'asimmetria: un problema sta in NP se, data una soluzione
candidata (il certificato), esiste un algoritmo che la verifica in tempo polinomiale nella
dimensione dell'ingresso. La domanda aperta P contro NP chiede se tutto cio' che si verifica
in fretta si risolva anche in fretta; nessuno lo sa.

E' il motivo per cui un validatore piccolo puo' controllare un solver grande. CP-SAT esplora
un albero di ricerca, propaga vincoli, impara clausole; il validatore prende il risultato
finale e per ogni assegnazione fa qualche divisione, con un costo di millisecondi contro i
venti secondi concessi al solver (`SOLVER_MAX_SECONDS = 20.0`). Cosi' economico, gira sempre.

Il certificato corto pero' esiste per la fattibilita', non per l'ottimalita'. Dimostrare che
un piano e' il migliore possibile richiede di escludere l'esistenza di un piano migliore,
cioe' di riesplorare lo spazio delle soluzioni: e' una proprieta' di non esistenza, e quelle
non hanno certificati corti nello stesso senso. Per questo il validatore accetta dicendo
"eseguibile" e mai "ottimo".

### 4.3 Relazioni metamorfiche

Anche senza conoscere la risposta a un ingresso, posso conoscere il rapporto fra le risposte
a due ingressi imparentati: trasformo l'ingresso in un modo di cui conosco l'effetto atteso e
controllo che l'uscita si trasformi di conseguenza. Sono le relazioni metamorfiche
(metamorphic relations). Su Tagliafuoco: rimescolare `scenario.fires` non deve cambiare il
valore dell'obiettivo, altrimenti c'e' una dipendenza nascosta dall'ordine di inserimento;
aggiungere un mezzo non puo' abbassare la copertura ottima, perche' al limite resta a terra;
aggiungere un vincolo duro non puo' migliorare l'ottimo, perche' restringere lo spazio
ammissibile puo' solo peggiorare; raddoppiare le `cruise_speed_kmh` deve dimezzare ogni tempo
di volo lasciando fermi i tempi fissi, il che punta la relazione sul pezzo da verificare.
Vivono nei test, non in produzione, perche' richiedono di eseguire il solver piu' volte.

### 4.4 Implementazione indipendente

Scrivo due volte il calcolo, in due modi diversi, e confronto. E' la via che da' il nome al
capitolo, e porta con se' una brutta notizia.

## 5. La programmazione a N versioni e il risultato scomodo

La programmazione a N versioni (N-version programming) nasce negli anni settanta dalla
ricerca sulla tolleranza ai guasti del software, e viene adottata dove un guasto costa vite:
avionica e nucleare in testa. Si scrivono N implementazioni indipendenti della stessa
specifica, con squadre e linguaggi diversi, si eseguono tutte e si vota a maggioranza. Il
ragionamento e' probabilistico: se ogni versione sbaglia con probabilita' p e gli errori
fossero indipendenti, due versioni sbaglierebbero insieme con probabilita' p al quadrato,
cioe' con p dello 0.1 per cento una volta su un milione. Sembra un guadagno gratuito.

A meta' degli anni ottanta un esperimento molto citato ha messo alla prova quell'ipotesi. Un
gruppo di ricerca statunitense ha fatto scrivere alcune decine di versioni dello stesso
programma, da autori diversi in universita' diverse e dalla stessa specifica, poi le ha
eseguite su un gran numero di ingressi casuali. I fallimenti non erano indipendenti: quando
una versione sbagliava, la probabilita' che ne sbagliasse un'altra sullo stesso ingresso era
molto piu' alta del previsto, con uno scarto statisticamente netto. E' noto come esperimento
di Knight e Leveson, e conviene leggerlo per esteso invece di fidarsi di questo riassunto.

La spiegazione non e' misteriosa: chi programma condivide formazione, abitudini e soprattutto
intuizioni sbagliate, e i casi difficili sono difficili per tutti. Due autori indipendenti
cadono nella stessa buca perche' la buca sta nel problema, non nell'autore: chiunque scriva il
calcolo dei cicli d'acqua deve decidere se il viaggio verso la sorgente dopo l'ultimo sgancio
va contato.

Questo riduce il valore della tecnica, non lo azzera. Restano ben coperte le classi di errore
a bassa correlazione: battitura, costante scambiata, indice fuori di uno, campo letto dalla
struttura sbagliata, unita' non convertita. Sono errori banali e frequentissimi, e un secondo
calcolo indipendente li prende quasi tutti; cio' che non prende e' l'errore concettuale
condiviso, quello che nasce da una specifica ambigua. La contromisura e' la sezione 6 di
`SPEC.md`, dove il modello fisico sta scritto per esteso in un posto solo: una specifica meno
ambigua riduce l'indipendenza, perche' entrambe le implementazioni leggono lo stesso testo,
ma abbatte il rischio di divergenza accidentale. Si condivide la semantica, non il codice.

## 6. Il limite strutturale: quando sbaglia la specifica

Qui sta l'onesta' del capitolo, e conviene dirlo per primo a un colloquio invece di aspettare
che venga contestato: se la specifica e' sbagliata, entrambe le implementazioni sbagliano
allo stesso modo e il validatore tace.

`WATER_LITERS_PER_KM2 = 100_000` dice che servono centomila litri per chilometro quadrato a
copertura e intensita' di riferimento, e `config.py` dichiara che e' una taratura e non una
verita' fisica, perche' il fabbisogno vero dipende da carico di combustibile, umidita' del
combustibile fine, vento, pendenza e tecnica di attacco. Se per una pineta calabrese in agosto
il valore giusto fosse il triplo, solver e validatore calcolerebbero entrambi con centomila, i
numeri coinciderebbero e il piano passerebbe: internamente coerente e operativamente
insufficiente. Lo stesso vale per `ROAD_TORTUOSITY = 1.45`, per i coefficienti di copertura,
per i pesi della priorita' e per la soglia WUI di 2 km.

C'e' un secondo canale di correlazione: solver e validatore leggono gli stessi oggetti
`Vehicle` dallo stesso `Scenario`, quindi se la `cruise_speed_kmh` di un elicottero nel
catalogo e' sbagliata i due concordano sull'errore. Il punto di indipendenza e' il calcolo,
non il dato. Il validatore puo' quindi dire "questo piano viola la fisica che abbiamo
dichiarato", mai "questo piano e' una buona idea": la seconda affermazione richiede di sapere
se la fisica dichiarata descrive il mondo, ed e' una domanda empirica a cui nessun programma
risponde guardando un altro programma.

### 6.1 Una specifica sbagliata inganna entrambi, una specifica ambigua li fa divergere

C'e' pero' un caso intermedio che il ragionamento di sopra non copre, ed e' quello in cui
la tecnica ha davvero pagato in questo progetto. Se la specifica e' **incompleta**, cioe'
tace su un punto che entrambe le implementazioni devono decidere, le due persone che le
scrivono decidono in modo diverso, e il disaccordo salta fuori. Non e' un caso teorico:
e' successo qui, ed e' l'episodio piu' istruttivo che il progetto abbia prodotto.

La prima prova end to end fatta dopo che tutti i sottosistemi passavano i propri test ha
dato questo, dal validatore:

```
il mezzo CAN_01 si sposta da F1 a F0 in 0.0 minuti,
ma servono almeno 27.7 minuti (153.7 km a 333 km/h)
```

Un Canadair che si teletrasporta da Reggio alla Sila. La reazione istintiva e' che il
risolutore abbia un errore. Leggendo i due moduli, la verita' era un'altra.

Il risolutore, in `optimize/model.py`, scrive la catena dei tempi cosi':

```
rel[v,f] = arr[v,f] + cycle(v,f) * s[v,f] + return(v,f)
```

Il termine `return` c'e': la missione **comprende il rientro in base**, esattamente come
nella formula della sezione 6 di `SPEC.md`. Al minuto `rel` il mezzo e' in base, e
`NoOverlap` gli impedisce di iniziare la missione successiva prima. Nel mondo del
risolutore nessuno si teletrasporta.

Il validatore, in `validate/rules.py`, aveva invece assunto che al rilascio il mezzo si
trovasse **sul focolaio**, e pretendeva il tempo di volo diretto fra un focolaio e il
successivo.

Chi aveva ragione? Il risolutore, perche' seguiva la formula scritta. Ma la specifica non
diceva da nessuna parte dove si trovi un mezzo fra due missioni, e due lettori in buona
fede avevano letto due cose diverse. Il validatore non aveva trovato un errore di calcolo:
aveva trovato un buco nella specifica.

La correzione e' stata su tre fronti, e l'ordine conta. Prima si e' scritta la regola
mancante in `SPEC.md`, per esteso: una missione termina con il rientro in base, il
trasferimento verso il focolaio successivo si misura dalla base ed e' gia' contato dentro
l'avvicinamento della seconda missione, e prima di ripartire il mezzo consuma il tempo di
riattrezzamento `Base.turnaround_minutes`. Solo dopo si e' corretta la regola del
validatore. E in mezzo e' emerso un difetto vero che non aveva nessuno dei due moduli:
`turnaround_minutes` esisteva nel modello di dominio, i dati statici lo valorizzavano, e
nessuno lo usava, quindi un mezzo poteva ripartire senza rifornirsi.

Vale la pena isolare la lezione, perche' e' quella che rende utile la tecnica: il
disaccordo fra due implementazioni indipendenti non dice quale delle due sbaglia. Dice che
la specifica non e' abbastanza precisa da imporre una risposta sola, e quella e'
un'informazione che nessun test interno a uno dei due moduli avrebbe mai prodotto.
Entrambi i moduli passavano i propri test, e passavano proprio perche' ciascuno testava la
propria convenzione.

C'e' anche un corollario che conviene notare. Tre scarti apparentemente distinti fra
metriche dichiarate e ricalcolate (litri erogati, focolai coperti, tempo medio di risposta)
sono spariti da soli appena corretta la regola temporale. Erano conseguenze della stessa
causa, perche' il validatore esclude dal ricalcolo le assegnazioni che ha giudicato non
eseguibili, e quelle bloccate erano proprio le missioni con i volumi grandi. Prima di
inseguire quattro difetti conviene chiedersi se ce n'e' uno solo con quattro sintomi.

### 6.1.1 E' successo una seconda volta, ed era peggio

La prima ambiguita' si era manifestata come un falso allarme vistoso: un
Canadair che si teletrasporta. Si nota, si indaga, si corregge.

La seconda si e' manifestata in un modo molto piu' insidioso, ed e' venuta
fuori solo perche' a quel punto il progetto aveva l'abitudine di far girare la
catena intera e di leggere ogni contestazione invece di scorrerla.

`SPEC.md` non diceva dove stesse `setup_minutes`, cioe' il tempo di decollo o
di uscita dal distaccamento, rispetto al minuto di partenza. Il risolutore e le
baseline lo mettevano dentro la missione, e calcolavano
`arrivo = partenza + setup + andata`. Il validatore, nel calcolare il limite
inferiore sull'arrivo, contava il solo tempo di trasferimento.

L'effetto non era un errore occasionale su un piano particolare: era uno scarto
costante, pari esattamente a `setup_minutes`, su **ogni assegnazione di ogni
piano**. Dodici minuti per un Canadair, sei per un elicottero medio, otto per un
modulo AIB. Il tempo medio di risposta ricalcolato risultava quindi piu' basso
del dichiarato di circa il trenta per cento, contro una tolleranza del cinque,
e il validatore emetteva un avviso su qualunque piano, compresi quelli
perfettamente corretti.

Questo e' il modo tipico in cui un controllo automatico muore. Non viene
cancellato: comincia a lamentarsi sempre, chi lo legge impara che quell'avviso
non significa niente, e il giorno in cui segnala un problema vero nessuno lo
guarda. Un validatore rumoroso e' peggio di un validatore assente, perche'
costa lo stesso e da' in piu' l'illusione del controllo.

La cosa interessante e' che il difetto stava dalla parte del validatore, non del
risolutore: il limite inferiore era piu' basso del vero, quindi il controllo era
anche piu' **debole** del dovuto. Avrebbe accettato un piano che dichiarava di
arrivare prima di quanto la fisica permettesse, fino a dodici minuti prima.
L'avviso fastidioso e il buco nel controllo erano la stessa cosa vista da due
lati.

Anche qui la correzione e' partita dalla specifica e non dal codice. `SPEC.md`
ora dichiara dove sta il setup, e nel farlo ha reso necessaria una terza regola
che nessuno aveva scritto: quando il validatore confronta una grandezza
**ricalcolata come limite** con una dichiarata, il confronto deve essere a senso
unico. Il tempo di risposta ricalcolato e' un minimo fisico: un piano che
dichiara di essere piu' lento del minimo non sta mentendo, sta solo aspettando.
L'unica accusa sensata e' il contrario. Confrontare un limite inferiore con una
tolleranza simmetrica e' un errore di categoria, e produce rumore per
costruzione.

Due ambiguita' trovate nello stesso progetto, nello stesso punto di contatto,
in poche settimane. Chi vuole una conclusione ottimista puo' dire che la
tecnica funziona. Chi ne vuole una pessimista puo' dire che le specifiche
scritte in prosa sono piene di buchi che nessuno vede finche' due persone non
provano a implementarle separatamente. Sono la stessa conclusione.

### 6.2 Il caso in cui le due implementazioni concordano

L'altra meta' della verifica si misura in un pomeriggio ed e' altrettanto informativa. Su
cinquantamila coppie di punti estratti a caso dentro il riquadro della Calabria, la
distanza calcolata da `optimize/geometry.haversine_km` (emisenoverso con arcoseno) e quella
calcolata da `validate/physics.geodesic_km` (Vincenty sferica con arcotangente a due
argomenti) danno:

- risultati identici bit per bit nel 4,2 per cento dei casi;
- scarto assoluto massimo di 1,4 per 10 alla meno dodici chilometri, cioe' circa un
  nanometro e mezzo;
- scarto relativo massimo di 4,9 per 10 alla meno tredici.

Il 4,2 per cento di risultati identici e' il dato che conferma che le due formule sono
davvero diverse: se fossero la stessa scritta in due modi, concorderebbero quasi sempre
fino all'ultimo bit. Il nanometro e mezzo e' l'ampiezza del rumore della virgola mobile su
questo dominio.

Da qui esce la taratura della tolleranza sulle distanze, che il progetto fissa a 0,05 km,
cioe' cinquanta metri. Sta dieci ordini di grandezza sopra il rumore, quindi non produrra'
mai un falso allarme per motivi aritmetici, e resta molto sotto l'ampiezza di un errore
vero: scambiare latitudine e longitudine, confondere gradi e radianti, o sbagliare
un'unita' produce scarti di chilometri, non di nanometri. Una soglia utile sta in quel
divario, e il divario va misurato, non indovinato.

## 7. Che cosa rende indipendente il nostro validatore

L'indipendenza non e' una dichiarazione di intenti, sono decisioni verificabili.

Nessun import da `optimize/`. E' la regola di `SPEC.md` e si controlla meccanicamente, con un
test che ispeziona i moduli importati da `tagliafuoco.validate` e fallisce se compare
`tagliafuoco.optimize`. Un vincolo di architettura deve essere eseguibile, altrimenti prima o
poi qualcuno condividera' una funzione "tanto e' la stessa formula". La regola si estende
oltre la fisica: anche la risoluzione dei selettori dei vincoli ("gli elicotteri", "i mezzi
della base di Lamezia") e' riscritta dentro `validate/`, perche' un errore nella funzione
condivisa metterebbe d'accordo i due moduli su un piano sbagliato.

Geodesia riscritta per un'altra strada. Le due implementazioni calcolano lo stesso angolo al
centro di una sfera di raggio 6371 km, ma non con la stessa formula. Il solver segue la
lettera di `SPEC.md` e usa l'haversine; il validatore usa la forma sferica della formula di
Vincenty, che arriva allo stesso angolo passando per le componenti della direzione invece che
per il seno di meta' angolo.

```python
# haversine: h e' il quadrato del seno di meta' angolo
h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
d_haversine = 2 * R * asin(sqrt(h))

# forma sferica di Vincenty: arcotangente a due argomenti
x = cos(lat2) * sin(dlon)
y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
d_vincenty = R * atan2(sqrt(x * x + y * y),
                       sin(lat1) * sin(lat2) + cos(lat1) * cos(lat2) * cos(dlon))
```

`atan2(a, b)` e' l'arcotangente a due argomenti: restituisce l'angolo del punto di coordinate
(b, a) tenendo conto del quadrante, quindi copre il giro intero senza i casi speciali
dell'arcotangente a un argomento. La forma di Vincenty regge anche sui punti quasi
antipodali, dove l'haversine perde cifre perche' `asin` ha derivata infinita vicino a 1. Quale
formula regga su quale intervallo di distanze lo tratta il capitolo 12, e vale la pena
leggerlo: la scelta si verifica con due righe di calcolo, non per sentito dire.

Su scala calabrese la differenza fra le due e' invisibile. Sulla tratta Lamezia verso Sila
danno 58.327016392101086 km e 58.327016392100710 km, uno scarto relativo di 6.5 per 10 alla
meno quindici: gli ultimi due bit del risultato. Non e' li' che si trovano i bug, e conviene
dirlo invece di fingere che due formule diverse proteggano dall'arrotondamento. La riscrittura
protegge da altro, cioe' dal copia e incolla: chi deve ripensare la formula ricontrolla unita',
ordine degli argomenti (latitudine o longitudine per prima e' un classico), conversione da
gradi a radianti, raggio. Sono le classi di errore a bassa correlazione del paragrafo 5.

Ricalcolo autonomo. Il validatore non guarda nessun `t_ciclo_acqua` dichiarato: prende
`WaterSource.point`, `FireCluster.centroid`, `Vehicle.cruise_speed_kmh`, `refill_minutes` e
`drop_minutes` e rifa' il conto da zero, lo stesso per autonomia e turni. Ogni discrepanza
oltre tolleranza diventa una diagnostica con nome della regola, assegnazione, valore
dichiarato, valore ricalcolato e scarto.

## 8. Le regole di validazione e cosa protegge ciascuna

Le regole sono quelle che la sezione 6 di `SPEC.md` impone al validatore, e le raggruppo per
cio' che proteggono invece che per file: i nomi delle funzioni si muovono mentre il progetto
cresce, la ragione per cui esistono no.

Integrita' referenziale e coerenza temporale. Ogni `Assignment` cita `vehicle_id`, `fire_id`
e `water_source_id` esistenti nello `Scenario`, e vale
`0 <= departure_minute <= arrival_minute <= release_minute <= horizon_minutes`. Proteggono
dal piano che impiega un mezzo che non c'e', dai tempi negativi e dalle missioni fuori
orizzonte: controlli banali che trovano errori veri, perche' un segno sbagliato produce
esattamente questo.

Tempo di trasferimento. Vale `arrival - departure >= d(base, fuoco) / v * 60 - tolleranza`,
con `d` ricalcolata e `ROAD_TORTUOSITY` applicato quando `Vehicle.domain` vale `GROUND`.
Protegge dal caso del paragrafo 1, ed e' asimmetrica di proposito. Arrivare prima di distanza
diviso velocita' e' impossibile, quindi blocca il piano. Arrivare molto dopo e' possibile
(un'attesa, una deviazione, una coda al pescaggio), quindi non blocca, ma un ritardo che la
distanza non spiega diventa un avvertimento: di solito significa che il pianificatore ha
calcolato male, non che il mezzo abbia aspettato.

Autonomia, turno e non sovrapposizione. Valgono `t_missione <= endurance_minutes`, con
`t_missione` ricomposta come setup piu' andata piu' sortite per ciclo piu' ritorno, somma
degli `engaged_minutes` del mezzo entro `max_shift_minutes`, e intervalli
`[departure, release]` disgiunti per lo stesso mezzo. Proteggono dal Canadair tenuto in aria
oltre il possibile, dall'equipaggio sfruttato oltre il turno (errore facile se il vincolo e'
scritto per missione invece che per giornata) e dal mezzo ubiquo, bug comune quando il
modello usa booleane di assegnazione senza variabili di intervallo.

Riattrezzamento fra due missioni. Vale `departure(k+1) >= release(k) + turnaround_minutes`,
dove `Base.turnaround_minutes` copre rifornimento, cambio equipaggio e controlli. Qui
`SPEC.md` scrive anche cio' che il validatore non deve pretendere. Una missione finisce con
il rientro alla base, quindi al minuto `release` il mezzo e' a terra, non sopra l'incendio.
Chi ricalcola per conto suo e' tentato di pretendere, fra due missioni consecutive, il tempo
di volo diretto dal primo focolaio al secondo; quel tempo non esiste, perche' il trasferimento
verso il secondo focolaio sta gia' dentro la sua `t_andata`, che parte dalla base. Pretenderlo
produce un falso allarme su ogni mezzo che lavora due volte, e siccome arriva dal validatore
sembra un errore del solver. La convenzione sta nella specifica proprio perche' due moduli
l'avevano letta in due modi diversi: e' l'ambiguita' del paragrafo 5 colta sul fatto.

Capienza idrica e tempo dei cicli. Valgono
`water_delivered_liters <= sorties * water_capacity_liters` (fra interi, esatto) e
`release - arrival >= sorties * t_ciclo_acqua - tolleranza`, piu' il lato della sorgente, dato
che un serbatoio con capacita' dichiarata non puo' erogare piu' di quello che contiene.
Proteggono dai litri creati dal nulla e dal piano che dichiara dodici sganci in un tempo che
ne consente sei.

Sorgente, stato del mezzo e condizioni ambientali. `WaterSource.usable_by` contiene il `kind`
del mezzo, nessuna assegnazione usa mezzi in `MAINTENANCE`, `OUT_OF_SHIFT` o `RESERVE`, e
`Scenario.wind_kmh` non supera `Vehicle.max_wind_kmh`. Sul volo notturno ci sono due gravita',
e la distinzione dice molto sul mestiere del validatore: uno scenario dichiarato notturno con
un mezzo aereo non abilitato in volo blocca il piano, perche' quel dato il pianificatore ce
l'aveva sotto gli occhi; una missione diurna che sfora la finestra di luce di `config.py`
(`DAYLIGHT_START_MINUTE` e `DAYLIGHT_END_MINUTE`, orari medi di stagione e non effemeridi)
produce solo un avvertimento, perche' bloccare per dieci minuti dopo un tramonto convenzionale
significa accusare la taratura invece del piano.

Vincoli duri tradotti. Per ogni vincolo `HARD` del `ConstraintSet` il validatore verifica che
nessun mezzo escluso compaia, che i mezzi forzati siano sul focolaio indicato, che nessuna
coppia vietata compaia, che i tempi stiano sotto `MaxResponseTime.minutes`, che i mezzi
raggiungano `MinVehiclesOnFire.count`, che le quote stiano fra `min_used` e `max_used` e che
le assegnazioni cadano dentro la `TimeWindow`. Protegge dal caso piu' imbarazzante di tutti:
il coordinatore da' un ordine, il traduttore lo traduce bene, e il piano lo ignora perche' il
vincolo e' finito male nel modello. I vincoli `SOFT` non si validano, perche' influenzano
l'obiettivo e non l'ammissibilita'.

Coerenza dichiarativa. Il validatore ricalcola le `PlanMetrics` e mette il numero dichiarato
accanto al proprio: litri erogati, tempo medio di risposta, minuti-mezzo, costo totale
(ricavato da `engaged_minutes / 60 * cost_per_hour_eur`), mezzi impiegati, focolai coperti.
Protegge dalla dashboard che mostra numeri migliori di quelli veri. Uno scarto qui avverte e
non blocca, perche' puo' nascere da due cause che il resoconto deve tenere separate: il piano
dichiara male, oppure il validatore ha escluso dal ricalcolo un'assegnazione gia' giudicata
non eseguibile.

Le gravita' sono quindi due. Una bloccante respinge il piano, un avvertimento lo accompagna,
come quando un focolaio in interfaccia urbano-foresta resta scoperto. Se ogni discordanza
bloccasse, il validatore diventerebbe rumore e qualcuno lo spegnerebbe, che e' il modo tipico
in cui muoiono i controlli automatici.

## 9. La tolleranza numerica

Due implementazioni in virgola mobile non danno mai lo stesso bit, e chi confronta con
l'uguaglianza esatta costruisce un validatore che respinge tutto.

Un `float` di Python e' un binario a doppia precisione IEEE 754: 64 bit, di cui 53 effettivi
di mantissa. La mantissa e' binaria, quindi un numero e' esatto solo se e' della forma m per 2
elevato a e; un decimo non lo e', come un terzo non e' finito in decimale, e infatti
`0.1 + 0.2` vale `0.30000000000000004`. La risoluzione relativa e' circa 2.2 per 10 alla meno
sedici, la distanza fra 1.0 e il numero successivo rappresentabile, e l'aritmetica non e'
associativa: sommando in ordini diversi i sei addendi della missione del paragrafo 1 (setup,
andata, tre blocchi da quattro cicli, ritorno) le 720 permutazioni danno tre totali distinti,
che differiscono fra loro di 2.8 per 10 alla meno quattordici minuti. Due implementazioni
oneste della stessa formula non coincidono bit a bit.

Errore assoluto e relativo non sono la stessa cosa. L'assoluto, `|dichiarato - ricalcolato|`,
ha senso quando esiste una soglia naturale nell'unita' della grandezza: mezzo minuto resta
mezzo minuto su una missione da 10 come su una da 90. Il relativo ha senso quando i valori
spaziano su ordini di grandezza diversi: per una distanza da 6 a 110 km una soglia assoluta
e' troppo stretta in alto o troppo larga in basso. La scelta robusta usa entrambi, come
`math.isclose`, che accetta quando lo scarto sta sotto almeno uno dei due limiti e misura il
limite relativo sul maggiore dei due valori in modulo, cosi' resta simmetrico.

```python
def entro_tolleranza(dichiarato: float, ricalcolato: float,
                     rel: float = 1e-6, assoluta: float = 1.0) -> bool:
    """Vero se i due valori coincidono entro tolleranza mista.

    Il termine relativo si misura sul maggiore dei due moduli, cosi'
    entro_tolleranza(a, b) e entro_tolleranza(b, a) danno la stessa risposta.
    """
    scarto = abs(dichiarato - ricalcolato)
    return scarto <= assoluta or scarto <= rel * max(abs(dichiarato), abs(ricalcolato))
```

Il compromesso e' simmetrico. Una soglia troppo stretta produce falsi allarmi, gli operatori
imparano a ignorare il validatore, e un validatore ignorato vale zero. Una soglia troppo larga
nasconde errori, perche' uno scarto reale piu' piccolo della soglia passa inosservato per
sempre. Qui il margine e' comodo: il rumore sui tempi sta intorno a 10 alla meno quattordici
minuti, mentre usare 200 km/h invece di 333 sulla tratta Lamezia verso Sila sposta il
trasferimento da 10.51 a 17.50 minuti.

I valori stanno in un oggetto solo, passato a ogni regola, cosi' che nessuna se ne inventi
uno. Sui tempi vale un minuto, la grana con cui il solver arrotonda: un minuto a 333 km/h vale
5.5 km, molto meno del salto di un mezzo teletrasportato, mentre con dieci minuti se ne
nasconderebbero 55. Sulle distanze vale 50 metri, sotto l'incertezza del centroide di un
focolaio, che nasce da pixel satellitari da 375 metri. Sull'acqua vale il due per cento con un
fondo fisso di cento litri, perche' su un mezzo piccolo la sola percentuale si ridurrebbe
quasi a zero. Ai confronti fra dichiarato e ricalcolato servono soglie piu' larghe, il cinque
per cento sulle metriche e il trenta per cento sul fabbisogno d'acqua: producono avvertimenti,
mai blocchi.

C'e' pero' una divergenza sistematica che non e' rumore. CP-SAT lavora su interi, quindi il
solver discretizza i tempi mentre il validatore calcola in virgola mobile: con grana al minuto
lo scarto arriva a un minuto per termine, e con dodici cicli a dodici minuti, che nessuna
tolleranza ragionevole copre. Si puo' uscirne in due modi, e il progetto sceglie il secondo.
Il primo e' dire al validatore quale grana usa il solver e fargli confrontare valori
discretizzati allo stesso modo: funziona, ma spende indipendenza, perche' un errore nella
discretizzazione diventa invisibile in quanto condiviso. Il secondo e' rendere la
disuguaglianza unilaterale e pretendere che il solver arrotondi dalla parte prudente: non si
chiede l'uguaglianza, si chiede che il piano non si dichiari piu' veloce della fisica, cioe'
`dichiarato >= ricalcolato - tolleranza`. Se il solver arrotonda i tempi per eccesso e i litri
per difetto, ogni discretizzazione lo rende piu' conservativo, l'accumulo va dalla parte
innocua e al margine resta un solo granello di grana piu' il rumore. Ecco perche' un minuto
basta dove un confronto bilaterale ne chiederebbe dodici: la tolleranza e' grande quanto la
grana del solver, non quanto l'errore che il solver puo' accumulare.

## 10. Difesa in profondita'

Difesa in profondita' vuol dire mettere piu' barriere in serie, ciascuna scritta assumendo
che le precedenti possano fallire. L'immagine corrente e' il formaggio svizzero: ogni fetta ha
buchi, ma i buchi di fette diverse non sono allineati, quindi l'incidente deve attraversarle
tutte nello stesso punto.

Le fette qui sono cinque automatiche piu' una umana, e ognuna assume che la precedente abbia
sbagliato. Lo schema tipizzato chiuso di `nlu/schema.py` definisce un repertorio finito
(undici vincoli rappresentabili piu' `UNSUPPORTED`), quindi il modello linguistico non puo'
emettere un'assegnazione o un tipo inesistente: non c'e' nessun campo in cui scriverli. La
validazione Pydantic aggiunge unione discriminata su `kind`, enumerazioni chiuse,
`Field(ge=..., le=...)` su ogni numero e `model_config = {"extra": "forbid"}`, perche' un JSON
sintatticamente valido puo' contenere valori assurdi. La risoluzione dei selettori fa fallire
prima del solver un `Selector` che nomina un mezzo inesistente, assumendo che il modello
allucini nomi plausibili. I vincoli duri del solver fanno restituire `INFEASIBLE` su una
richiesta contraddittoria invece di un piano sbagliato. Il validatore indipendente assume che
il solver abbia un errore di modellazione e non gli crede. Chiude il coordinatore, che vede
ogni vincolo con l'etichetta duro o morbido e il frammento di ordine da cui viene
(`summary_lines()` e `source_span`) e puo' smentirlo prima che il solver parta.

Due osservazioni oneste. Gli strati si moltiplicano solo se sono indipendenti, e non lo sono
del tutto: `config.py` e' letto da tutti, quindi una `WUI_THRESHOLD_KM` sbagliata attraversa
ogni fetta, e quel punto di fallimento si elimina solo mettendo alla prova la costante contro
il mondo, cioe' con la valutazione del capitolo 10. E ogni strato costa: lo schema chiuso
riduce cio' che il coordinatore puo' esprimere.

## 11. La verifica formale, e perche' qui non si usa

La verifica formale dimostra matematicamente che un programma soddisfa una specifica per
tutti gli ingressi, invece di provarlo su alcuni. Le famiglie sono tre: il model checking,
che esplora esaustivamente lo spazio degli stati di un sistema finito e verifica proprieta'
temporali (qualcosa di brutto non accade mai, qualcosa di buono prima o poi accade); la
dimostrazione di teoremi dentro un assistente alla dimostrazione; i risolutori SMT, che
decidono la soddisfacibilita' di formule in teorie come l'aritmetica lineare sugli interi.
Funziona, con esempi celebri dove sbagliare costa vite: microkernel verificati, compilatori C
con dimostrazione di preservazione della semantica, analizzatori statici sul software di volo.
Non e' fantascienza, e' costosa.

Perche' non qui. Il collo di bottiglia e' la specifica e non la prova: dimostrare la coerenza
fra programma e specifica non aggiunge fiducia sul mondo quando la specifica contiene
`WATER_LITERS_PER_KM2 = 100_000`, un numero scelto a tavolino. E la proprieta' interessante
non e' formalizzabile: "il piano e' eseguibile" si', "il piano e' una buona scelta" no. Due
pezzi di formalita' ci sono comunque. CP-SAT, quando restituisce `INFEASIBLE`, non ha solo
fallito nel trovare una soluzione: ha stabilito che non ne esiste, conclusione diversa da
`UNKNOWN`, che restituisce quando il limite di tempo scade senza avere deciso. E il validatore
e' verifica a tempo di esecuzione (runtime verification), della stessa famiglia.

## 12. Cosa devi saper spiegare

**Qual e' la differenza fra un test e un validatore?**
Un test verifica casi scelti dall'autore, in sviluppo, contro un valore atteso; un validatore
gira in produzione su ogni uscita vera e verifica proprieta' che valgono sempre. Il test e'
limitato dall'immaginazione di chi lo scrive, il validatore no perche' esprime invarianti
invece di enumerare casi. Coprono fallimenti diversi: il validatore non vede una regressione
di qualita', il test non vede lo scenario reale che nessuno aveva previsto.

**Che cos'e' il problema dell'oracolo e come lo affronti qui?**
E' l'impossibilita' di sapere se un'uscita e' corretta quando calcolarla era il compito del
programma. Lo affronto separando fattibilita' e ottimalita': la prima ha un oracolo
praticabile, costruito con invarianti, con l'asimmetria fra risolvere e verificare, con
relazioni metamorfiche e con una seconda implementazione del calcolo fisico; la seconda no.

**Perche' controllare costa meno che calcolare? E fino a che punto e' vero?**
Perche' una soluzione candidata funziona da certificato verificabile in tempo polinomiale,
che e' la definizione di NP: pensa al sudoku, difficile da riempire e facile da controllare.
Il limite e' netto: vale per la fattibilita', non per l'ottimalita', perche' "non esiste un
piano migliore" e' una proprieta' di non esistenza e non ha certificato corto.

**La programmazione a N versioni non e' stata smentita sperimentalmente?**
In parte si'. Un esperimento molto citato di meta' anni ottanta ha mostrato che versioni
indipendenti non falliscono in modo indipendente, perche' gli autori condividono le
intuizioni sbagliate. Riduce il valore della tecnica, non lo azzera: restano ben coperte le
classi a bassa correlazione (costante scambiata, unita' non convertita, indice fuori di uno),
che una seconda geodesia intercetta.

**Qual e' il limite piu' serio del vostro validatore?**
Che se la specifica e' sbagliata entrambe le implementazioni sbagliano uguale e il validatore
tace: se il fabbisogno vero fosse il triplo di `WATER_LITERS_PER_KM2`, i due concorderebbero
su un piano insufficiente. C'e' anche correlazione residua sui dati, perche' leggono lo stesso
catalogo dei mezzi. La formulazione corretta e' "questo piano viola la fisica che abbiamo
dichiarato", non "questo piano e' una buona idea".

**Come scegli la tolleranza numerica, e cosa succede se sbagli?**
Tolleranza mista, assoluta oppure relativa come `math.isclose`. Troppo stretta produce falsi
allarmi e il validatore finisce ignorato; troppo larga nasconde errori veri. Il problema vero
e' la discretizzazione a interi di CP-SAT, che vale dodici minuti su dodici cicli: lo risolvo
con un confronto unilaterale, cosi' l'arrotondamento va dalla parte prudente e un minuto di
margine, la grana del solver, basta.

**Perche' alcune violazioni bloccano il piano e altre no?**
Perche' rispondono a due domande diverse. "Impossibile" blocca: un arrivo piu' veloce della
velocita' di crociera, un mezzo in due posti insieme, un mezzo di riserva che vola lo stesso.
"Strano" avverte: un ritardo non spiegato dalla distanza, una missione appena fuori dalla
finestra di luce tarata, uno scarto fra metrica dichiarata e ricalcolata. Se tutto bloccasse,
il validatore respingerebbe piani legittimi ogni volta che una taratura e' un po' stretta, e
verrebbe spento. Per lo stesso motivo usa sempre il limite piu' generoso che la fisica
consenta: fra due tempi di pescaggio discordanti prende il minore, e per dichiarare
impossibile un arrivo conta il solo volo e lascia fuori il setup.

**Perche' non usate la verifica formale, visto che si parla di sicurezza?**
Perche' il collo di bottiglia e' la specifica, non la prova: dimostrare la coerenza fra
programma e specifica non serve se la specifica poggia su tarature dichiarate, e la proprieta'
che vorremmo davvero, "il piano e' una buona scelta", non e' formalizzabile. Usiamo la
verifica a tempo di esecuzione, della stessa famiglia.

## 13. Per approfondire

Sul problema dell'oracolo esiste una rassegna molto usata in ingegneria del software, che
classifica gli oracoli in specificati, derivati, impliciti e parziali. Cercando "test oracle
problem survey" si trova il lavoro di riferimento, che mette in ordine il vocabolario prima
di proporre tecniche. Sul collaudo metamorfico cerca le rassegne su "metamorphic testing",
che raccolgono relazioni usate dove l'oracolo manca: servono a farsi l'occhio per
riconoscerne nel proprio dominio, che e' la parte difficile.

Sull'indipendenza fra versioni cerca l'esperimento di Knight e Leveson e leggilo per esteso:
la parte interessante e' il disegno sperimentale, cioe' come si costruisce un test statistico
sull'indipendenza dei fallimenti, insieme alle repliche e alle obiezioni che sono seguite.

Sulla virgola mobile il testo canonico e' l'articolo divulgativo di David Goldberg su cio' che
ogni informatico dovrebbe sapere sull'aritmetica in virgola mobile. La documentazione
ufficiale di Python ha un capitolo breve e onesto sugli aspetti sorprendenti dei `float`, e la
specifica di `math.isclose` spiega bene la tolleranza mista.

Sulla verifica a tempo di esecuzione cerca "runtime verification" insieme a "design by
contract": precondizioni, postcondizioni e invarianti di classe sono la parente diretta del
validatore, e vedere come sono formulate nel linguaggio Eiffel chiarisce perche' un'asserzione
sia diversa da un test. Infine, per l'asimmetria fra risolvere e verificare, un capitolo
introduttivo su P, NP e i certificati, preferendo la definizione di NP tramite verificatore
polinomiale.
