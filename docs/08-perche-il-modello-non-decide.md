# Perche' il modello non decide

La scelta di progetto piu' importante di Tagliafuoco: il modello linguistico
traduce e basta, non pianifica, non stima e non scrive il modello matematico.
Questo capitolo serve a difendere quella scelta con argomenti e a conoscerne i
limiti prima che te li faccia notare qualcun altro.

Indice dei paragrafi:

1. La domanda a cui questo capitolo risponde
2. Quattro modi di mettere un modello linguistico dentro un ottimizzatore
3. Le ragioni che motivano la scelta
4. Restringere lo spazio degli errori possibili
5. Perche' UNSUPPORTED non e' una resa
6. La catena di responsabilita'
7. L'operatore smentisce i vincoli, non valida il piano
8. Autocritica: dove questo approccio non arriva
9. Cosa devi saper spiegare
10. Per approfondire

---

## 1. La domanda a cui questo capitolo risponde

A un colloquio, davanti a questo progetto, la domanda arriva presto:

> "Hai un modello linguistico e un problema di assegnazione. Perche' non chiedi
> direttamente al modello di fare il piano?"

E' ragionevole. Un modello moderno, messo davanti a dodici incendi e otto mezzi,
produce un piano in pochi secondi, scritto bene, con le motivazioni accanto a
ogni scelta. Rispondere "perche' non e' affidabile" non basta, perche' e'
generico e non dice dove esattamente diventa inaffidabile. La risposta buona ha
tre pezzi: esistono almeno quattro modi di usare un modello linguistico dentro
un sistema di ottimizzazione e falliscono in punti diversi; c'e' una ragione
misurabile per cui il progetto ha scelto il quarto; e quella scelta cambia la
natura degli errori possibili in un modo che rende il sistema ispezionabile.

Prima fissiamo il vocabolario, perche' meta' della confusione nasce da qui. Un
problema di ottimizzazione ha due parti separate. La formulazione traduce il
problema del mondo reale in oggetti matematici: quali sono le variabili di
decisione, quali vincoli le legano, cosa si massimizza. La risoluzione e' il
calcolo che trova i valori che rispettano i vincoli e ottimizzano l'obiettivo.
In Tagliafuoco la formulazione sta in `optimize/`, scritta a mano, e la
risoluzione la fa CP-SAT: il modello linguistico non tocca ne' l'una ne'
l'altra.

## 2. Quattro modi di mettere un modello linguistico dentro un ottimizzatore

### 2.1 Il modello come decisore diretto

Il modello produce il piano: riceve lo stato del mondo e restituisce le
assegnazioni, quale mezzo su quale focolaio, a che ora parte, quanti lanci fa.
Promette la via piu' corta, niente modello matematico da scrivere e niente
solver da configurare, con una capacita' di ragionamento apparentemente
generale.

Rompe sull'aritmetica e sulla combinatoria, che sono le due cose che questo
problema richiede. Facciamo i conti con dati veri del progetto. Il mezzo e'
`CAN_01` di `data/static/mezzi.json`, un Canadair basato all'aeroporto di
Lamezia Terme (`BASE_LMZ`): crociera 333 km/h, 6.137 litri di capacita', 12
minuti di preparazione, 1,5 di pescaggio, 0,5 di sgancio, 240 minuti di
autonomia. Il file assegna a questo mezzo il profilo `CL415` e quelle cifre
sono vicine a quelle che si trovano comunemente per l'aeromobile reale, ma
restano parametri del progetto e non numeri presi da un manuale di volo: a un
colloquio vanno presentati cosi'. Il focolaio sta sul Pollino, a 39,894 nord e
16,193 est, e l'haversine dalla base misura 110,0 km.

```
t_andata  = 110,0 km / 333 km/h * 60 = 19,8 minuti
t_ritorno = 19,8 minuti
tempo per i lanci = 240 - 12 - 19,8 - 19,8 = 188,4 minuti
```

La scelta della sorgente d'acqua non si fa a occhio sulla mappa, e qui si vede
bene. Il punto d'acqua piu' vicino al focolaio, in
`data/static/sorgenti_acqua.json`, e' l'idrante dei vigili del fuoco di
Castrovillari, a 9,1 km, ma il suo campo `usable_by` elenca soltanto autobotti e
moduli AIB: un Canadair non pesca da un idrante. Filtrando sui punti che il file
dichiara pescabili da un Canadair ne restano dieci in tutta la regione, sette
tratti di mare e tre laghi della Sila, e il piu' vicino a questo focolaio dista
quasi quattro volte l'idrante: lo Ionio del Golfo di Corigliano, alla foce del
Crati, a 34,7 km, seguito dai 40,9 km del Tirreno al largo di Scalea. I laghi
non entrano nemmeno in gara, perche' il primo utile e' il Cecita a 61,7 km.

```
t_ciclo = 34,7 km * 2 / 333 km/h * 60 + 1,5 + 0,5 = 14,5 minuti per lancio
lanci possibili = parte intera di 188,4 / 14,5 = 12
acqua erogata   = 12 * 6.137 = 73.644 litri
```

Quei sei chilometri di differenza fra i due mari non sono un dettaglio.
Rifacendo lo stesso conto con Scalea il ciclo sale a 16,7 minuti, e i lanci che
entrano nei 188,4 minuti disponibili scendono da 12 a 11: 6.137 litri in meno
per un mezzo, un focolaio e un pomeriggio, solo per aver scelto l'altra costa.

C'e' un'altra cosa che questi numeri rendono visibile. L'elisuperficie AIB di
Mormanno sta a 17,1 km da questo focolaio, contro i 110,0 km di Lamezia, quindi
la regola "manda il mezzo libero piu' vicino", che in `SPEC.md` e' la baseline
di confronto, manderebbe l'AW139 di Mormanno. Ma quell'elicottero porta 1.500
litri per lancio contro i 6.137 del Canadair, cioe' un quarto scarso: "piu'
vicino" e "piu' utile" non coincidono, e stabilire quale dei due conta di piu'
in questo scenario e' esattamente il confronto che il solver fa esplicitamente e
che a occhio non si fa.

Il fabbisogno, con la formula della sezione 6 di `SPEC.md`, per un focolaio di
2,1 km quadrati su conifere con FRP massima di 180 MW:

```
litri = 2,1 * 100.000 * 1,4 * (1 + min(1; 180/200)) = 558.600 litri
```

Simbolo per simbolo: 2,1 e' l'area in km quadrati; 100.000 sono i litri per km
quadrato che `config.py` dichiara in `WATER_LITERS_PER_KM2`; 1,4 e' il
coefficiente delle conifere, il piu' alto della tabella perche' le resine
bruciano forte; l'ultimo fattore vale 1,9 perche' 180 MW divisi per la
normalizzazione di 200 MW danno 0,9, il minimo con 1,0 lascia passare 0,9 e si
somma a 1.

Vale la pena chiudere il conto, perche' serve a vedere quanto e' stretto il
margine:

```
t_missione = 12 + 19,8 + 12 * 14,5 + 19,8 = 225,6 minuti
```

Tre ore e quarantasei minuti su 240 di autonomia dichiarata. Il piano sta dentro
il vincolo, ma con quattordici minuti di margine, che e' meno di un ciclo
d'acqua. Il margine e' piu' fragile di quanto sembri: bastano tre chilometri e
mezzo in piu' fra il focolaio e il mare perche' il ciclo salga a 15,8 minuti e
il dodicesimo lancio non ci stia piu'.

Con quei 73.644 litri il Canadair copre il 13,2 per cento del fabbisogno. Per
coprirlo tutto ne servirebbero otto contemporaneamente, e nello scenario del
progetto di Canadair ce ne sono due: in un caso cosi' l'obiettivo realistico non
e' spegnere, e' contenere finche' il fronte non perde forza da solo.

Due avvertenze sul numero otto. Eredita per intero l'arbitrarieta' della
costante di 100.000 litri per km quadrato, che `config.py` e `SPEC.md`
dichiarano come taratura e non come verita' fisica: se quella costante fosse
sbagliata di un fattore due, otto diventerebbero quattro o sedici. Ed e' un
conto interno al modello del progetto, non una stima di quanti mezzi la lotta
reale impiegherebbe su un incendio del genere, che dipende da vento, orografia e
da cosa succede a terra.

Nessuno di questi passaggi e' difficile, ma ognuno e' una moltiplicazione o una
divisione incatenata alla precedente, e un modello linguistico esegue aritmetica
come sottoprodotto della previsione del token successivo, non come calcolo:
sbagliare 188,4 diviso 14,5 non gli costa niente, perche' dentro di lui non c'e'
niente che verifichi il risultato. E il margine di quattordici minuti appena
calcolato dice che un errore piccolo non resta piccolo: un lancio di troppo
sfora l'autonomia.

La combinatoria e' anche peggio. Con otto mezzi e dodici focolai le coppie
mezzo-focolaio sono 96, e un piano e' un modo di accendere o spegnere ciascuna
di quelle coppie: 2 elevato a 96, cioe' circa 7,9 per 10 alla ventottesima,
prima ancora di aggiungere gli orari di partenza e il numero di lanci. Un solver
non enumera quello spazio, lo pota con la propagazione dei vincoli e con i
limiti sull'obiettivo (capitolo 05); un modello linguistico non lo esplora
affatto, produce la continuazione piu' plausibile.

Il modo in cui rompe e' il peggiore possibile: produce un piano che sembra
ragionevole, dove nessuna riga e' assurda e l'autonomia sforata di quaranta
minuti non si vede a occhio. Dell'errore non risponde nessuno, perche' non
esiste nessun artefatto ispezionabile fra la richiesta e il piano: non puoi
chiedere "perche' questo mezzo su questo incendio" e ottenere una risposta
verificabile, dato che la motivazione scritta e' testo generato insieme al
piano, non la sua causa.

### 2.2 Il modello come stimatore

Il modello non fa il piano, ma fornisce i numeri che il piano usa: quante
squadre servono su questo fronte, quanta acqua chiede questo incendio, quanto ci
mette un'autobotte ad arrivare. Promette di colmare i buchi dei dati, e l'idea
e' allettante perche' i modelli sembrano sapere molte cose sul mondo.

Rompe nell'invisibilita' della stima dentro il risultato. Se il modello dice
"questo incendio richiede circa 400.000 litri", quel numero entra nel solver
come un dato qualunque e determina l'intero piano, ma non e' un dato, e' una
plausibilita' linguistica: non ha un intervallo di confidenza, non deriva da una
formula ispezionabile, non e' riproducibile se cambia il modello. Peggio, e'
dell'ordine di grandezza giusto, quindi nessuno lo guardera' con sospetto. La
formula di Tagliafuoco e' probabilmente meno accurata della stima di un esperto
vero, ma se il risultato e' sbagliato puoi risalire al coefficiente responsabile
e cambiarlo, e la modifica si propaga in modo prevedibile: un numero sbagliato
che sai da dove viene e' riparabile, uno di cui nessuno conosce l'origine no.
Dell'errore risponde formalmente chi ha scritto il codice, praticamente nessuno,
perche' non c'e' modo di distinguere un errore della stima da un errore del
piano.

### 2.3 Il modello come formulatore

Il modello scrive il modello matematico: gli descrivi il problema a parole e lui
produce variabili, vincoli e funzione obiettivo. Promette il colpo grosso,
perche' il collo di bottiglia della ricerca operativa applicata non e' il
calcolo ma il fatto che formulare richiede una persona che sappia fare ricerca
operativa, e ce ne sono poche.

Rompe nel modo descritto dal paragrafo 3. Anticipo la forma del problema: un
vincolo formulato male non produce un errore di sintassi, produce un modello che
gira e restituisce una soluzione. Se manchi un vincolo ottieni una soluzione
migliore del vero ottimo, che e' la cosa piu' pericolosa perche' un risultato
migliore del previsto non desta sospetti; se ne aggiungi uno di troppo ottieni
una soluzione peggiore o nessuna soluzione, e passi il pomeriggio a chiederti se
il problema sia davvero infattibile. Dell'errore risponde, in teoria, chi rivede
il codice generato; in pratica, per rivedere seriamente un modello CP-SAT
generato da una macchina devi saperlo scrivere tu, e se sai scriverlo tu il
vantaggio di generarlo si assottiglia parecchio. E' il paradosso centrale di
questo ruolo.

### 2.4 Il modello come traduttore

Il modello riconosce, dentro una frase, quale vincolo fra quelli gia'
implementati il coordinatore sta chiedendo, e con quali parametri. Non scrive
codice, non produce numeri liberi, non decide niente: sceglie dentro un
repertorio chiuso e riempie campi tipizzati. Promette molto meno degli altri
tre, e questo e' il punto, perche' la formulazione resta lavoro umano e si
sposta una cosa sola, l'interfaccia, dal modulo al parlato. Rompe sulla
mappatura, cioe' puo' scegliere il tipo sbagliato fra due simili, sbagliare un
sinonimo, non risolvere un riferimento che dipende dal contesto, e il paragrafo
8 li elenca tutti senza sconti.

Qui pero' la risposta su chi risponde dell'errore e' precisa, ed e' la ragione
della scelta. L'errore e' un `ConstraintSet` tipizzato, cioe' un oggetto che si
puo' serializzare, mostrare, registrare e confrontare con quello atteso, e che
porta dentro di se' il frammento di testo da cui e' nato, nel campo
`source_span`. Il coordinatore lo vede prima che il solver parta, il benchmark
lo confronta con l'annotazione di riferimento: se sbaglia, si sa quale vincolo,
da quale frase e con quale conseguenza.

| ruolo | cosa produce | dove rompe | errore ispezionabile? |
|---|---|---|---|
| decisore | il piano | aritmetica, combinatoria, vincoli fisici | no, il piano e' l'unico artefatto |
| stimatore | i numeri in ingresso | plausibilita' senza provenienza | no, la stima sparisce nel risultato |
| formulatore | il modello matematico | vincoli mancanti o di troppo | solo da chi saprebbe scriverlo |
| traduttore | vincoli tipizzati | mappatura fra frase e repertorio | si', dodici righe con la frase accanto |

## 3. Le ragioni che motivano la scelta

Il ruolo del formulatore merita un paragrafo suo perche' e' il piu' seducente e
perche' la scelta di Tagliafuoco si regge sul non averlo preso.

Una nota prima di cominciare. La prima versione di questo capitolo citava una
cifra presa dalla letteratura sui benchmark di formulazione automatica. L'ho
tolta: non ne avevo verificato la fonte, e i modelli di frontiera di oggi
formulano molto meglio di quanto quella cifra lasciasse intendere. In alcune
prove fatte a novembre 2026 su un problema di turni con undici regole, un
modello recente ha scritto e risolto il modello CP-SAT al primo tentativo.

La scelta di progetto quindi non si regge su "il modello sbaglia", ma su quanto
e' difficile verificare un modello matematico generato ogni volta, soprattutto
su un problema reale. Cinque ragioni.

La distribuzione di addestramento. I problemi da manuale (lo zaino, il
trasporto, l'assegnazione, lo scheduling di officina) compaiono migliaia di
volte nel corpus, con la stessa struttura e spesso con la soluzione scritta
accanto, quindi un modello che li formula bene fa un lavoro molto piu' vicino al
recupero che alla costruzione: riconosce lo stampo e lo riempie. Il problema
realistico non ha uno stampo nel corpus.

La forma canonica. Un esercizio da manuale arriva gia' ripulito: i dati sono
tutti e soltanto quelli che servono, le unita' sono coerenti, l'obiettivo e' uno
solo. Guarda invece cosa deve tenere insieme `optimize/`: mezzi aerei e
terrestri con modelli fisici diversi, autonomia in minuti di volo e turni in
ore, cicli d'acqua che dipendono dalla sorgente scelta, finestre di volo diurno,
e cinque termini d'obiettivo in conflitto (copertura, protezione
dell'interfaccia urbano-foresta, tempo di risposta, soddisfacimento idrico,
costo) con i pesi elencati in `config.py`.

I vincoli impliciti. In un problema reale le regole piu' importanti sono quelle
che nessuno scrive perche' tutti le sanno: un elicottero non pesca da una
sorgente che non lo ammette, un mezzo che ha gia' volato sette ore non ne fa
un'altra, gli aerei non volano di notte (la ragione per cui esistono
`DAYLIGHT_START_MINUTE` e `DAYLIGHT_END_MINUTE`). Un formulatore che non li
conosce produce un modello che gira benissimo e pianifica lanci notturni.

La natura silenziosa del fallimento. Un modello mal formulato non solleva
eccezioni, produce una soluzione: se hai dimenticato il vincolo di autonomia, il
solver trovera' un piano piu' efficiente di qualunque piano reale, e sembrera'
un successo.

La severita' della metrica. Formulare correttamente vuol dire azzeccare tutti i
vincoli insieme, non la maggior parte. Se un problema realistico ne ha quindici
e la probabilita' di sbagliarne uno e' anche solo del dieci per cento, la
probabilita' di azzeccarli tutti e' 0,9 elevato a 15, cioe' circa il 20,6 per
cento. Non serve nessun fallimento drammatico per arrivare a un numero basso:
bastano una competenza per vincolo del novanta per cento e una metrica che non
fa sconti sul parziale.

## 4. Restringere lo spazio degli errori possibili

Il passaggio logico che tiene in piedi il progetto e' questo: se la formulazione
e' la parte che un modello linguistico sbaglia, allora la formulazione non
gliela diamo. La scrive una persona, una volta sola, e la si sottopone a test.
E' cio' che accade in `optimize/`, dove il modello CP-SAT esiste come codice, ha
una suite di test, e quando cambia il cambiamento e' un diff che qualcuno
rivede.

Al modello linguistico resta il riconoscimento dell'intento, cioe' capire che
"l'elicottero resta di riserva" appartiene a `RESERVE_VEHICLE` e non a
`EXCLUDE_VEHICLE`. E' un compito di classificazione con estrazione di argomenti,
e i modelli ci sono davvero bravi, non per accidente ma perche' e' vicinissimo a
cio' che l'addestramento ottimizza: riconoscere che due modi diversi di dire la
stessa cosa vogliono dire la stessa cosa.

L'effetto si misura in termini di spazio degli errori. Con il formulatore,
l'insieme degli output sbagliati e' "qualunque modello matematico sbagliato":
infinito e non enumerabile, perche' puoi sbagliare un segno, una sommatoria, un
indice, un vincolo intero, e ogni variante fallisce in modo diverso. Con il
traduttore, l'insieme e' "un vincolo sbagliato fra dodici tipi noti, con
parametri dentro intervalli dichiarati", e ha tre proprieta' che il primo non
ha.

E' enumerabile. Dodici tipi vuol dire che puoi scrivere, per ciascuno, i casi in
cui si confonde con gli altri e metterli nel benchmark: la confusione fra
`ReserveVehicle` ed `ExcludeVehicle` non e' un rischio generico, e' una casella
di una matrice di confusione che puoi guardare. Una matrice di confusione e' la
tabella che incrocia il tipo atteso con il tipo prodotto, con le risposte giuste
sulla diagonale; qui e' dodici per dodici, cioe' una cosa che si stampa e si
legge in una schermata.

E' ispezionabile prima dell'esecuzione. `summary_lines()` produce una riga per
vincolo con il tipo, se e' duro o morbido, e la motivazione o il frammento di
origine: un ordine tipico ne produce una manciata, e si leggono in venti
secondi.

Ed e' vincolato per costruzione. `MaxResponseTime.minutes` vive fra 1 e 600 e
`PrioritizeZone.weight_multiplier` fra 1,0 e 10,0, quindi un traduttore
impazzito non puo' produrre un moltiplicatore pari a diecimila: Pydantic lo
rifiuta prima che tocchi il solver. Nel caso del formulatore non esiste un
equivalente di questo controllo, perche' non c'e' niente da tipizzare.

C'e' un prezzo, e va detto subito perche' torna al paragrafo 8. Questo
restringimento e' anche una perdita di generalita': il formulatore, quando
indovina, gestisce un ordine che nessuno aveva previsto, mentre il traduttore,
davanti a un ordine fuori repertorio, non puo' fare altro che dichiarare di non
saperlo fare. Il progetto considera questo un buon affare. Non e' un affare
gratis.

## 5. Perche' UNSUPPORTED non e' una resa

Nel repertorio c'e' un tipo che non corrisponde a nessun vincolo del solver:
`Unsupported`. A prima vista sembra la dichiarazione di una mancanza.

Il ragionamento parte da un fatto sul comportamento dei modelli. Un modello
addestrato a essere utile, davanti a una richiesta che non sa soddisfare, tende
a produrre la cosa piu' vicina che sa produrre. Non mente per malizia: cerca la
continuazione piu' plausibile, e una risposta plausibile e' sempre piu'
probabile di un rifiuto, perche' nel corpus le risposte sono molto piu'
frequenti dei rifiuti. Adesso mettiamo quel comportamento dentro il nostro
sistema:

> "Fate un controfuoco sul crinale prima che il fronte passi la strada."

Il controfuoco, cioe' appiccare deliberatamente un fuoco che consuma il
combustibile davanti al fronte principale, e' una tecnica reale, e non e'
rappresentabile in nessuno dei dodici tipi, perche' il solver assegna mezzi a
focolai e non sa nulla di operazioni sul terreno. Un traduttore che non sa
astenersi produrra' qualcosa di vicino: magari un `ForceAssignment` di squadre
di terra sul focolaio piu' prossimo al crinale, con una `rationale` che suona
persino sensata.

Quel vincolo ha tre proprieta' pessime: e' plausibile, quindi chi legge l'elenco
non si insospettisce; e' falso, perche' non e' quello che e' stato ordinato; ed
e' invisibile, perche' niente lo distingue da un vincolo tradotto correttamente.
Un `Unsupported` con `reason` che dice "il sistema non rappresenta le operazioni
di controfuoco, questa parte dell'ordine non e' stata applicata" ha le tre
proprieta' opposte: e' visibile in sala operativa, e' onesto, ed e' misurabile,
perche' il benchmark lo conta nel tasso di astensione corretta.

Da qui la formulazione da imparare: un vincolo plausibile e falso e' peggiore di
nessun vincolo, perche' nessun vincolo si vede e un vincolo falso no. Il costo
di un'astensione e' che una parte dell'ordine non viene applicata, e il
coordinatore lo sa e puo' agire; il costo di un'allucinazione e' che una parte
dell'ordine viene applicata storta, e nessuno lo sa. Due dettagli di progetto
seguono da qui: `Unsupported` ha `enforcement` con default `SOFT`, perche' e'
una nota per l'operatore e non deve mai rendere infattibile un piano; e `reason`
e' obbligatorio, perche' un'astensione muta sarebbe di nuovo un'informazione
mancante, solo in un altro posto.

## 6. La catena di responsabilita'

Un piano di impiego dei mezzi antincendio, nel mondo reale, lo firma qualcuno:
c'e' una persona che risponde della scelta di mandare un equipaggio su un fronte
invece che su un altro davanti a un'inchiesta, se le cose vanno male. E' la
ragione per cui `SPEC.md` insiste che Tagliafuoco e' un simulatore a scopo di
studio e per cui `config.py` contiene una costante `DISCLAIMER`.

La domanda di progetto resta valida anche per un simulatore, perche' determina
se un sistema di questo tipo potrebbe mai essere usato: un sistema che non sa
spiegare da quale frase e' nato un vincolo puo' stare in una sala operativa? No,
per una ragione pratica prima ancora che etica. Quando il piano proposto non
convince, la domanda che parte non e' "il sistema e' affidabile in media?", e'
"perche' questo elicottero e' fermo?": una domanda puntuale, fatta sotto
pressione, che richiede una risposta puntuale in pochi secondi. Un sistema che
puo' rispondere solo "il modello ha valutato lo scenario nel suo complesso" ha
perso la fiducia di quella sala, e a ragione.

Il campo `source_span` risponde esattamente a questo. Sta in `BaseConstraint`,
quindi ogni vincolo lo ha, e contiene il frammento letterale dell'ordine da cui
il vincolo e' stato tratto, cosi' la catena diventa percorribile all'indietro:

```
l'elicottero MED_02 (AW139, base di Lorica) e' fermo
  <- perche' un vincolo RESERVE_VEHICLE lo ha messo in riserva
  <- perche' quel vincolo ha source_span "l'elicottero di Lorica resta di riserva"
  <- perche' quella frase sta nell'ordine registrato alle 14:32
  <- perche' il coordinatore l'ha detta
```

Ogni anello e' verificabile: il primo si legge nel piano, il secondo nel
`ConstraintSet`, il terzo confrontando il frammento con il testo dell'ordine, il
quarto e' responsabilita' umana e li' il sistema si ferma, che e' esattamente
dove deve fermarsi. Confronta con la stessa domanda posta al decisore del
paragrafo 2.1: la risposta sarebbe un testo generato che descrive un
ragionamento plausibile ma non e' la causa della decisione, perche' e' stato
prodotto dallo stesso processo nello stesso passaggio, e non c'e' modo di
verificare che corrisponda. Una spiegazione che non si puo' falsificare non e'
una spiegazione, e' una rassicurazione.

Nota infine che `source_span` serve anche a misurare le allucinazioni nel
benchmark, perche' se il frammento dichiarato non compare nel testo dell'ordine
il vincolo e' sospetto. Lo stesso campo che rende il sistema spiegabile in sala
operativa lo rende misurabile in laboratorio, e la coincidenza non e' casuale:
un sistema spiegabile e' un sistema le cui affermazioni si possono confrontare
con qualcosa.

## 7. L'operatore smentisce i vincoli, non valida il piano

C'e' una differenza che sembra sottile e non lo e', e riguarda il momento in cui
si chiede all'essere umano di intervenire. Progetto A: il sistema produce un
piano completo e lo mostra all'operatore, che lo approva o lo respinge. Progetto
B, quello di Tagliafuoco: il sistema mostra i vincoli estratti dall'ordine prima
che il solver parta, l'operatore li conferma o li smentisce, poi il solver gira.

Nel progetto A chiedi di validare un piano. Ma un piano e' il risultato di
un'ottimizzazione su uno spazio combinatorio: per giudicarlo davvero
bisognerebbe rifare il calcolo, e nessuno puo' rifare a mente l'assegnazione di
otto mezzi a dodici focolai con vincoli di autonomia e cicli d'acqua. Quindi
l'operatore non validera' il piano: lo guardera' cercando qualcosa che stona, e
se non stona niente approvera'. Questo comportamento ha un nome nella
letteratura sui fattori umani, si chiama compiacenza verso l'automazione, e
consiste nel fatto che chi supervisiona un sistema che di solito ha ragione
smette progressivamente di supervisionare. Il risultato e' un'approvazione che
non aggiunge sicurezza ma distribuisce la responsabilita', il peggiore dei mondi
possibili: qualcuno firma senza aver controllato.

Nel progetto B chiedi una cosa diversa, cioe' "ho capito bene quello che hai
detto?". E' una domanda a cui la persona puo' rispondere davvero, perche' ha
l'informazione necessaria (cio' che intendeva quando ha parlato), ed e' una
domanda sul testo e non sul calcolo. Concretamente guarda questo:

```
[RESERVE_VEHICLE]     (duro)    "l'elicottero di Lorica resta di riserva"
[TIME_WINDOW]         (duro)    "gli aerei non volano dopo le otto"
[PRIORITIZE_ZONE]     (morbido) "prima le zone vicino alle case"
```

Tre righe. Se la seconda dicesse `EXCLUDE_VEHICLE` invece di `TIME_WINDOW`,
l'operatore se ne accorgerebbe subito, perche' sa cosa ha detto: il tempo
richiesto e' quello di leggere tre righe, non di ricalcolare un piano. C'e'
anche un argomento sul momento: nel progetto A, se l'operatore respinge, il
calcolo e' gia' stato fatto e va rifatto, mentre nel progetto B la correzione
arriva prima del solver e costa un'interazione, non un ciclo di calcolo.

Un'ultima osservazione, che e' anche un limite: il progetto B non sostituisce il
controllo a valle, lo affianca. Il validatore indipendente continua a
ricalcolare tempi, cicli d'acqua, autonomia e turni per conto suo, respingendo i
piani impossibili. Le due difese coprono cose diverse, perche' l'operatore
controlla che il sistema abbia capito l'ordine e il validatore controlla che il
piano sia fisicamente eseguibile: nessuna copre l'altra, ed e' per questo che ci
sono entrambe.

## 8. Autocritica: dove questo approccio non arriva

Se difendi questo progetto a un colloquio, la domanda che segue tutte le altre
e' "e cosa non funziona?". Rispondere "niente" e' la risposta sbagliata.

Il repertorio chiuso non copre tutto, e per costruzione non puo'. Dodici tipi
sono un ritaglio del linguaggio operativo di una sala antincendio, e chiunque ci
abbia lavorato ne conosce altri venti: il controfuoco del paragrafo 5, le
richieste di risorse esterne alla flotta, il coordinamento con mezzi di altre
regioni, i vincoli meteorologici espressi a voce ("con questo vento non li
mando"), le priorita' che dipendono da informazioni che il sistema non ha ("li'
c'e' una casa di riposo"). Il sistema li segnala con `Unsupported`, che e'
meglio che inventare, ma segnalare non e' risolvere: un ordine composto per
meta' di elementi fuori repertorio produce un piano che risponde a meta'
dell'ordine.

Aggiungere un vincolo costa lavoro umano, e il conto e' esplicito nel docstring
di `ConstraintKind`: implementarlo in `optimize/`, implementarne la verifica
indipendente in `validate/`, aggiungere casi al benchmark, l'ultimo dei quali
richiede di annotare a mano nuovi ordini di esempio. Il sistema non si estende
da solo e non si estende in fretta. Per un progetto di questa scala va benissimo,
perche' il numero di tipi e' fissato; per un sistema che dovesse crescere con
l'uso e' il collo di bottiglia principale.

La mappatura fra sinonimi puo' sbagliare, e in italiano operativo i sinonimi
abbondano. "Il Canadair", "l'anfibio", "il 415" possono indicare lo stesso
mezzo; "tenere di riserva", "tenere fermo", "non impiegare" indicano cose vicine
ma non identiche, e il confine fra `RESERVE_VEHICLE` ed `EXCLUDE_VEHICLE` passa
proprio li'. I nomi dei luoghi sono peggio: frazioni, localita' assenti da ogni
elenco ufficiale, nomi dialettali, comuni omonimi in province diverse. Il
progetto ha un `resolver.py` che affronta questo pezzo, ma nessun risolutore di
entita' e' perfetto, e ogni errore produce un selettore che seleziona la cosa
sbagliata o non seleziona niente.

La traduzione dipende dal contesto dello scenario che le passiamo, quindi
eredita i difetti di quel contesto. Se l'arricchimento territoriale ha
attribuito un focolaio al comune sbagliato, "l'incendio di Cirella" viene
risolto male e il vincolo finisce sul focolaio sbagliato, e la traduzione
risultera' corretta a ogni controllo perche' il traduttore ha fatto bene il suo
lavoro su dati sbagliati. E' un errore che attraversa il confine fra
sottosistemi, il tipo piu' difficile da attribuire.

Il repertorio influenza cio' che gli operatori dicono, ed e' un effetto che
nessuna metrica cattura. Quando le persone imparano quali frasi il sistema
capisce cominciano a usare quelle: in parte e' il normale adattamento a uno
strumento, in parte e' una perdita, perche' il sistema smette di ricevere gli
ordini che non sa tradurre, il tasso di `Unsupported` scende, e sembra un
miglioramento mentre e' solo il segno che le richieste fuori repertorio hanno
smesso di essere formulate. E' la ragione per cui il benchmark deve restare un
corpus annotato a mano e fissato, non un campione del traffico.

Il modello resta un componente esterno e mutevole. Il progetto gira senza chiave
API, e questo e' un pregio architetturale reale, ma quando la chiave c'e' il
comportamento dipende da un servizio che non controlli: cambia il modello,
cambiano i risultati del benchmark, e cambia senza che tu abbia toccato una
riga. Il traduttore a regole mitiga il rischio operativo, non quello
scientifico: due esecuzioni del benchmark a sei mesi di distanza non sono
confrontabili se il modello sotto e' cambiato, e per questo `ConstraintSet` ha
il campo `model_name`, che va registrato insieme ai risultati.

Infine il limite piu' onesto: la separazione fra traduzione e decisione e' netta
sulla carta e un po' meno nella pratica. Un `ObjectiveWeight` che ripesa il
termine `response_time` da 4,0 a 20,0 non decide nulla formalmente, ma sposta
l'ottimo abbastanza da cambiare il piano, e il valore ammesso arriva fino a 100.
Il modello non decide, ma sceglie un numero che pesa parecchio sulla decisione.
Le difese ci sono (l'intervallo e' dichiarato, il vincolo e' visibile
nell'elenco che l'operatore conferma, l'effetto e' sull'obiettivo e non sulla
fattibilita'), ma "il modello non decide mai" resta una semplificazione utile e
non un teorema, e presentarla come un teorema e' il modo piu' rapido per farsi
smontare da un intervistatore attento.

## 9. Cosa devi saper spiegare

**Perche' non fai produrre il piano direttamente al modello?** Perche' il
compito richiede aritmetica e combinatoria, le due cose che un modello
linguistico non fa in modo affidabile. Per sapere se il Canadair di Lamezia puo'
operare su un focolaio del Pollino a 110,0 km servono un tempo di andata (19,8
minuti ai 333 km/h dichiarati in `data/static/mezzi.json`), uno di ritorno, il
residuo di autonomia (188,4 minuti) e un ciclo d'acqua che dipende da quale
punto d'acqua il piano sceglie, che in quel caso e' lo Ionio e non il Tirreno.
Ogni passo e' una moltiplicazione incatenata alla precedente. La combinatoria e'
ancora peggio: otto mezzi e dodici focolai fanno 96 coppie, cioe' 2 elevato a 96
modi di accenderle e spegnerle. Un modello che sbaglia produce un piano che
sembra ragionevole e viola l'autonomia senza che si veda, e non resta nessun
artefatto ispezionabile con cui accorgersene.

**Quali sono i quattro ruoli, e dove rompe ciascuno?** Decisore: produce il
piano, rompe su aritmetica e combinatoria, e nessuno risponde dell'errore
perche' non c'e' nulla da ispezionare. Stimatore: fornisce i numeri in ingresso,
rompe perche' produce cifre plausibili senza provenienza, che entrano nel
calcolo come dati veri. Formulatore: scrive il modello matematico, rompe perche'
un vincolo mancante non genera errori ma una soluzione troppo buona, e per
rivedere il codice generato serve saperlo scrivere. Traduttore: mappa la frase
su un repertorio chiuso, rompe sulla mappatura, ma l'errore e' un oggetto
tipizzato con la frase di origine accanto, quindi si vede e si misura.

**Un modello di frontiera oggi sa formulare un problema di ottimizzazione:
perche' non farglielo fare?** Perche' il punto non e' quanto e' bravo, ma quanto
si puo' verificare quello che produce. Un modello matematico generato a ogni ordine non si puo' collaudare una
volta per tutte, e un vincolo dimenticato non produce un errore
ma un piano troppo buono.
I problemi reali hanno vincoli impliciti che nessuno scrive, dati sporchi,
obiettivi in conflitto, e una metrica che richiede di azzeccarli tutti insieme.

**Perche' l'errore del traduttore e' meno grave di quello del formulatore, se
sbagliano entrambi?** Perche' cambia l'insieme degli errori possibili. Il
formulatore puo' produrre qualunque modello matematico sbagliato: insieme
infinito, non enumerabile, dove ogni variante fallisce in modo diverso. Il
traduttore puo' produrre un vincolo sbagliato fra dodici tipi noti, con
parametri dentro intervalli dichiarati, e quell'insieme e' enumerabile
(benchmark bilanciato per tipo, matrice di confusione), ispezionabile prima
dell'esecuzione (dodici righe lette in venti secondi) e vincolato per
costruzione (Pydantic rifiuta un moltiplicatore fuori intervallo). Nel caso del
formulatore nessuna delle tre proprieta' esiste.

**Perche' UNSUPPORTED non e' un'ammissione di debolezza?** Perche' l'alternativa
e' peggiore. Un modello addestrato a essere utile, davanti a una richiesta fuori
repertorio, produce la cosa piu' vicina che sa produrre, e quella cosa e'
plausibile, falsa e invisibile. Un vincolo plausibile e falso e' peggiore di
nessun vincolo, perche' nessun vincolo si vede e un vincolo falso no.
`Unsupported` rende visibile il buco, obbliga a dire perche' (`reason` e'
obbligatorio), ed e' `soft` per default cosi' non rende mai infattibile un
piano. E' anche l'unica cosa che rende misurabile l'astensione corretta.

**Perche' mostri i vincoli prima del solver invece del piano dopo?** Perche'
sono due richieste diverse fatte alla stessa persona. Validare un piano
significherebbe rifare a mente un'ottimizzazione combinatoria, che nessuno puo'
fare, quindi l'operatore cerca solo qualcosa che stona e approva se non trova
niente: si chiama compiacenza verso l'automazione e produce una firma senza
controllo, cioe' responsabilita' distribuita e sicurezza zero. Confermare i
vincoli e' invece una domanda sul testo, "ho capito bene quello che hai detto?",
a cui la persona puo' rispondere davvero perche' sa cosa intendeva. Sono tre
righe invece di un piano, e la correzione arriva prima del calcolo.

**Se il modello non decide, perche' puo' cambiare i pesi dell'obiettivo?** E' il
punto debole della formulazione netta, e conviene ammetterlo. Un
`ObjectiveWeight` che porta il peso del tempo di risposta da 4,0 a un valore
molto piu' alto non decide formalmente nulla, ma sposta l'ottimo abbastanza da
cambiare il piano. Le difese ci sono: il valore sta in un intervallo dichiarato,
il vincolo e' visibile nell'elenco che l'operatore conferma, e l'effetto e'
sull'obiettivo e non sulla fattibilita', quindi non puo' far scartare un piano
valido. Ma "il modello non decide mai" resta una semplificazione utile, non un
teorema.

**Qual e' il limite piu' serio di questo approccio?** Il costo di estensione:
aggiungere un tipo richiede tre interventi (implementazione in `optimize/`,
verifica indipendente in `validate/`, casi nel benchmark con annotazione
manuale), quindi il sistema non cresce da solo ne' in fretta. Il secondo, piu'
sottile, e' che il repertorio influenza cio' che gli operatori dicono: imparano
quali frasi il sistema capisce e usano quelle, il tasso di `Unsupported` scende,
e sembra un miglioramento mentre e' solo il segno che le richieste fuori
repertorio hanno smesso di essere formulate. Per questo il benchmark deve
restare un corpus fissato e annotato a mano.

## 10. Per approfondire

Sul ruolo dei modelli linguistici nell'ottimizzazione, cerca i lavori recenti
sui benchmark di formulazione automatica, quelli che danno a un modello la
descrizione testuale di un problema e valutano il modello matematico prodotto.
Guarda con attenzione come e' costruito l'insieme di valutazione, perche' la
differenza fra i risultati alti e quelli bassi dipende quasi interamente da
quanto i problemi assomigliano agli esercizi da manuale, e questo si vede
leggendo gli esempi, non i numeri riassuntivi. Interessa anche il criterio di
correttezza, perche' "il modello gira" e "il modello e' giusto" sono due soglie
molto diverse.

Sulla differenza fra formulazione e risoluzione, un manuale introduttivo di
ricerca operativa resta il modo migliore di capire perche' la prima e' il collo
di bottiglia. La parte da leggere non sono gli algoritmi ma la modellazione, in
particolare i capitoli che mostrano lo stesso problema formulato in due modi
diversi, uno che il solver risolve in un secondo e uno che non termina.

Sui limiti del ragionamento aritmetico dei modelli, cerca la letteratura sulla
loro capacita' di calcolo e sulla differenza fra eseguire un'operazione e
recuperare un risultato visto durante l'addestramento: spiega un fenomeno che
altrimenti sembra contraddittorio, cioe' che lo stesso modello che risolve un
problema di matematica in forma classica sbaglia una moltiplicazione a tre cifre
presentata in forma insolita.

Sul fattore umano, cerca la compiacenza verso l'automazione e la letteratura sui
sistemi di supporto alle decisioni in contesti ad alto rischio come l'aviazione
e la medicina. La domanda ricorrente e' sempre la stessa: quando si chiede a una
persona di supervisionare una macchina, cosa esattamente le si chiede di
verificare, e ha l'informazione per farlo? Sulla spiegabilita', distingui due
cose spesso confuse, cioe' rendere interpretabile a posteriori un modello opaco
e costruire un sistema in cui la catena causale e' esplicita per costruzione:
Tagliafuoco fa la seconda, che e' molto piu' semplice e molto piu' solida.

Sul dominio, infine, vale la pena leggere qualche piano antincendio boschivo
regionale, che sono documenti pubblici. Servono a capire quale linguaggio usano
davvero gli operatori, che e' l'input del traduttore, e a vedere quanti vincoli
operativi reali cadono fuori dai dodici tipi del repertorio, che e' la misura
onesta del paragrafo 8.
