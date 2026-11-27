# 10. La metodologia di valutazione

Questo capitolo spiega come si dimostra che Tagliafuoco serve a qualcosa, e altrettanto come
si evita di dimostrarlo per sbaglio quando non e' vero.

Indice dei paragrafi:

1. Un numero da solo non dice niente
2. La baseline: cosa faresti davvero senza il sistema
3. La rigiocata della stagione, e il suo limite piu' serio
4. La fuga di informazione dal futuro
5. Le metriche, una per una, e come si ingannano
6. L'ablazione
7. La valutazione del traduttore come compito separato
8. Riproducibilita'
9. Significativita'
10. Come non barare
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. Un numero da solo non dice niente

Mettiamo che la rigiocata della stagione 2025 produca questo risultato: Tagliafuoco copre
l'82 per cento dei focolai, con un tempo medio di risposta di 23 minuti.

E' un buon risultato? Non si puo' sapere. Forse in quella stagione i focolai erano pochi e
vicini alle basi, e chiunque avrebbe coperto il 90 per cento mandando il mezzo libero piu'
vicino. Forse invece l'82 per cento e' straordinario perche' la flotta era mezza in
manutenzione. Il numero da solo non contiene l'informazione che serve, perche' misura la
difficolta' dello scenario insieme alla bravura del pianificatore, e non permette di
separarle.

Serve un secondo pianificatore che giri sugli stessi identici scenari. Allora la differenza
fra i due risultati non contiene piu' la difficolta' dello scenario, che si elide, e resta
solo cio' che distingue i due modi di decidere. Questo secondo pianificatore si chiama
baseline, e sceglierlo bene e' la decisione piu' importante di tutta la valutazione: un
risultato vale esattamente quanto vale la baseline contro cui e' misurato.

## 2. La baseline: cosa faresti davvero senza il sistema

Una baseline onesta risponde a una domanda precisa: se questo sistema non esistesse, cosa
succederebbe? Non "qual e' il modo peggiore di fare questo lavoro", che e' la domanda a cui
risponde un fantoccio costruito per perdere (straw man).

La differenza si vede con un esempio. Una baseline che assegna i mezzi a caso farebbe fare a
Tagliafuoco una figura magnifica e non direbbe nulla, perche' nessun coordinatore assegna i
mezzi a caso. La baseline giusta e' la regola che un essere umano sotto pressione applica
davvero: manda sul focolaio il mezzo disponibile piu' vicino, e passa al prossimo. Quella
regola non e' stupida, e' la scelta localmente ottima, e batterla richiede di guardare piu'
in la' del singolo focolaio, che e' esattamente cio' che un solver combinatorio dovrebbe
saper fare. Se non ci riesce, il progetto non serve, e la valutazione deve poterlo dire.

Il progetto usa due baseline perche' rispondono a due domande diverse. La prima ordina i
focolai per istante di rilevamento e li serve in quell'ordine, assegnando a ciascuno il mezzo
libero piu' vicino: e' il primo arrivato, primo servito, cioe' cio' che accade quando le
chiamate entrano una alla volta e nessuno ha una visione d'insieme. La seconda ordina i
focolai per `base_priority` decrescente e poi assegna sempre il mezzo libero piu' vicino: e'
un coordinatore esperto che, prima di muovere qualcuno, si e' preso trenta secondi per
decidere quali fuochi contano di piu'. Le due differiscono per una cosa sola, l'ordine in cui
i focolai vengono serviti, e questo e' voluto: se differissero anche per altro, la distanza
fra loro non si potrebbe attribuire alla priorita'.

La distanza dalla prima misura tutto il guadagno messo insieme, cioe' aver capito che alcuni
focolai contano piu' di altri e aver risolto bene l'accoppiamento fra mezzi e focolai. La
distanza dalla seconda misura solo il secondo pezzo, perche' la priorita' quella baseline ce
l'ha gia'. Conviene riportarli entrambi, perche' il piu' onesto dei due e' il secondo: se
Tagliafuoco batte la baseline ingenua ma non quella ordinata per priorita', il valore del
progetto sta nell'aver formalizzato la priorita' e non nell'ottimizzazione, ed e'
un'informazione da dire, non da nascondere.

Entrambe le baseline devono rispettare gli stessi vincoli fisici duri: autonomia, turni,
compatibilita' fra mezzo e sorgente d'acqua, volo notturno. Una baseline che produce piani
impossibili e' un fantoccio, e per questo il validatore indipendente del capitolo 9 gira
anche sui suoi piani.

## 3. La rigiocata della stagione, e il suo limite piu' serio

La rigiocata (replay) e' un esperimento controfattuale: si prendono i dati veri della
stagione, si riavvolge il nastro, e a ogni finestra di `REPLAY_WINDOW_HOURS` ore si chiede ai
pianificatori cosa avrebbero proposto in quell'istante. Con una stagione da giugno a
settembre, 122 giorni per otto finestre da tre ore, si arriva a 976 finestre possibili, di
cui solo una parte contiene focolai. Quante siano davvero utilizzabili dipende dai dati FIRMS
e va contato, non stimato a priori. Per dare un ordine di grandezza a cosa si sta rigiocando,
il committente del progetto riporta 143 chilometri quadrati percorsi dal fuoco in Calabria al
15 settembre 2025: e' la cifra che ci e' stata fornita, non una misura che abbiamo fatto noi.

Ora il limite, ed e' il piu' serio di tutta la valutazione. La rigiocata dice che cosa il
sistema avrebbe proposto. Non dice che cosa sarebbe successo.

La differenza non e' sottile. Per sapere cosa sarebbe successo servirebbe un modello di
propagazione del fuoco (quanto cresce un fronte in tre ore con questo vento, questa pendenza,
questo combustibile) e un modello della reazione del fuoco all'intervento (quanto rallenta un
fronte dopo dodici sganci da 6000 litri). Tagliafuoco non ha ne' l'uno ne' l'altro: i focolai
nella rigiocata sono fotografie immobili, con l'area e la FRP che il satellite ha osservato,
e non cambiano perche' qualcuno li ha spenti o non li ha spenti.

Le conseguenze sono concrete. Non possiamo affermare che il sistema avrebbe bruciato meno
ettari, perche' gli ettari non sono nel modello. Non possiamo affermare che avrebbe salvato
case, perche' la protezione dell'interfaccia urbano-foresta la misuriamo come "un mezzo e'
stato assegnato entro il tempo x", non come "il fuoco non ha raggiunto il costruito". E non
cogliamo gli effetti di secondo ordine, per esempio che spegnere presto un focolaio libera
mezzi per il successivo.

Come si dichiara tutto questo senza minimizzarlo. Si dice che la valutazione misura la
qualita' delle proposte rispetto a criteri dichiarati (quanti focolai riceviamo, quanto in
fretta, con quanta acqua, a quale costo), che sono variabili operative intermedie e non esiti
sul territorio, e che per arrivare agli esiti servirebbe accoppiare un simulatore di
propagazione, cioe' un progetto a se'. E si evita il verbo "prevenire" in ogni frase che
descrive i risultati.

## 4. La fuga di informazione dal futuro

E' l'errore piu' comune e piu' subdolo nelle valutazioni su serie temporali, e ha un nome:
fuga di informazione (data leakage). Accade quando il sistema, mentre decide per l'istante t,
usa informazione che nell'istante t non poteva avere.

E' subdolo perche' non fallisce: migliora i risultati. Un sistema che sbircia il futuro
produce metriche eccellenti sulla rigiocata e fallisce il giorno in cui gira davvero, quando
il futuro non c'e' ancora. Non c'e' nessun test che diventa rosso, nessuna eccezione, nessun
avvertimento. C'e' solo un numero che sembra ottimo.

La regola operativa in Tagliafuoco e' una sola riga, e ogni scorciatoia la viola:
lo `Scenario` costruito per l'istante t contiene solo `Detection` con `acquired_at <= t`.

I modi concreti in cui la si viola qui, tutti plausibili e tutti da cercare attivamente.

Il clustering che raccoglie troppo. `CLUSTER_TIME_WINDOW_HOURS` vale 12: se il clustering
gira sull'intera stagione e poi si filtra per data, un focolaio a t contiene rilevamenti
arrivati dopo t. Va rifatto dentro ogni finestra, sui soli rilevamenti gia' visibili.

L'arricchimento con dati finali. `FireCluster.area_km2` e `max_frp_mw` calcolati su tutti i
rilevamenti del focolaio significano che a t conosciamo gia' quanto diventera' grande. Da
questi due valori discendono `water_demand_liters` e `base_priority`, quindi la fuga si
propaga nel punto peggiore, la decisione su cosa conta di piu'.

Le costanti tarate sul risultato. `FRP_NORMALISATION_MW = 200` e `AREA_NORMALISATION_KM2 = 5`
normalizzano rispetto a una scala: se quella scala e' il massimo osservato nella stagione, il
pianificatore usa a giugno una statistica di settembre. Vanno fissate a priori, come in
`config.py`, oppure calcolate su una stagione precedente.

Lo stato della flotta. Se `available_from_minute` o `VehicleStatus` derivano da cio' che i
mezzi hanno effettivamente fatto dopo, il pianificatore sa in anticipo chi sara' libero.

Il parallelo utile e' il backtesting finanziario, dove lo stesso errore si chiama look-ahead
bias: una strategia che compra usando il prezzo di chiusura di domani guadagna sempre nel
backtest e perde sempre in borsa. Da li' arriva anche la tecnica giusta, l'analisi
walk-forward, cioe' avanzare una finestra alla volta decidendo solo con cio' che e' gia'
accaduto. La validazione incrociata su serie temporali fa lo stesso: non rimescola gli
esempi, perche' rimescolare metterebbe nel passato pezzi di futuro, ma taglia in blocchi
ordinati in cui l'addestramento precede sempre la prova.

## 5. Le metriche, una per una, e come si ingannano

Ogni metrica e' una misura di qualcosa e un invito a barare su qualcos'altro. Conviene
conoscere l'invito prima che lo accetti qualcun altro al posto tuo. Le metriche riportate
sono le sei della sezione 7 di `SPEC.md`, e il numero sei tornera' utile nel paragrafo 9.

Copertura, `fires_covered / fires_total`. La definizione ingenua sarebbe "il focolaio ha
ricevuto almeno un'assegnazione", e si inganna in un secondo: basta mandare un mezzo qualunque
per un minuto su ogni focolaio, la copertura vola al 100 per cento e nessun incendio viene
spento. Per questo `SPEC.md` non la usa: un focolaio conta come coperto quando i litri che ha
ricevuto raggiungono il suo fabbisogno, e la regola generosa dell'intervento presente vale
solo per i focolai senza fabbisogno dichiarato, dove la frazione non sarebbe definita e
scartarli falserebbe il denominatore. L'inganno pero' non sparisce, si sposta: adesso la
copertura dipende dal fabbisogno, che viene da `WATER_LITERS_PER_KM2`, una taratura. Chi
pubblica un numero di copertura deve pubblicare accanto la soglia che ha usato.

Copertura in interfaccia urbano-foresta, `fires_wui_covered / fires_wui_total`. Misura la
stessa cosa sul sottoinsieme che conta di piu', i focolai entro `WUI_THRESHOLD_KM` (2 km) dal
costruito. Si inganna spostando la soglia: portandola a 5 km il denominatore si gonfia di
focolai lontani e facili, e il rapporto migliora senza che nessuna casa sia piu' sicura. La
soglia va fissata prima e dichiarata.

Tempo medio di risposta. La convenzione e' meno ovvia di quanto sembri, e `SPEC.md` la fissa
per esteso perche' moduli diversi la calcolano e devono ottenere lo stesso numero: si misura
per focolaio e non per assegnazione, e vale il minuto di arrivo del primo mezzo che lo
raggiunge, contato dall'inizio dell'orizzonte. Le due precisazioni pesano entrambe. Mediare
per assegnazione premierebbe chi manda quattro mezzi sullo stesso focolaio e penalizzerebbe
chi ne manda uno solo su un focolaio lontano, cioe' misurerebbe come la flotta e' distribuita
invece di quanto e' pronta, e il secondo mezzo che arriva un'ora dopo non peggiora la
risposta, la rinforza. Contare dall'inizio dell'orizzonte invece che dalla partenza importa
perche' un mezzo alla seconda missione parte tardi: cio' che il focolaio ha aspettato
comprende l'attesa, non solo il volo.

La media ha due problemi. Il primo e' che nasconde la coda: dieci interventi da 10 minuti e
uno da 120 danno una media di 20, che suona bene mentre quel singolo focolaio e' rimasto solo
due ore, ed e' li' che un fuoco diventa incontrollabile. Per questo si riporta anche il
novantesimo percentile, il valore che il 90 per cento dei tempi non supera: descrive il caso
brutto tipico senza farsi spostare dal singolo estremo come farebbe il massimo. Anche il
metodo di calcolo del percentile va dichiarato, perche' su campioni piccoli le convenzioni
divergono parecchio: `SPEC.md` fissa il rango piu' vicino, e due numeri calcolati con metodi
diversi non vanno mai messi nella stessa tabella, altrimenti si legge come differenza fra
pianificatori cio' che e' differenza fra contatori. Da qui anche `LATE_RESPONSE_MINUTES = 45`,
taratura dichiarata per contare le risposte tardive.

Il secondo problema e' una forma di selezione del sopravvissuto: il tempo di risposta esiste
solo per i focolai serviti, quindi chi ignora i focolai lontani ottiene un tempo medio
eccellente proprio perche' ha rinunciato ai casi difficili. O si riportano sempre insieme
copertura e tempo, o si assegna al focolaio scoperto un tempo convenzionale pari
all'orizzonte.

Rapporto fra litri erogati e fabbisogno, tagliato a 1.0 da
`PlanMetrics.water_fulfilment_ratio`. Il taglio rende invisibile l'eccesso, quindi in questo
rapporto chi spreca cinque volte l'acqua su un focolaio facile ha lo stesso punteggio di chi
ne porta il giusto. E' il motivo per cui `SPEC.md` pretende che la metrica dei litri erogati
resti senza taglio: il rapporto dice quanto del fabbisogno e' stato soddisfatto, il totale
grezzo dice quanto se ne e' versato, e tagliando entrambi lo spreco sparisce dal resoconto e
riappare solo nelle ore-mezzo. Resta il limite di fondo: i litri sono dichiarati dal piano,
non misurati sul campo, e il fabbisogno viene da una taratura, per cui il rapporto misura la
coerenza fra piano e fabbisogno stimato, mai l'efficacia reale.

Ore-mezzo spese e costo. `total_vehicle_minutes` e `total_cost_eur` sono risorse consumate,
quindi piu' basse e' meglio, ma sono anche la cosa piu' facile da ottimizzare: chi non fa
volare nessuno ha costo zero. Diventano informative solo a parita' di copertura e di acqua.
Il modo corretto di leggerle e' il confronto a coppie dominanti: un piano e' migliore di un
altro se non e' peggiore in nessuna metrica ed e' migliore in almeno una. Quando nessuno dei
due domina siamo davanti a uno scambio, e si dichiara il tasso di scambio accettato invece di
fingere che esista un ordine totale. Il solver un ordine totale ce l'ha, dato dai pesi in
`OBJECTIVE_WEIGHTS`, ma quei pesi sono una scelta: riportare i risultati metrica per metrica
evita di nasconderla dentro un numero solo.

## 6. L'ablazione

Il confronto con la baseline dice se l'insieme funziona meglio dell'alternativa. Non dice
quale pezzo stia facendo il lavoro, e puo' capitare benissimo che un pezzo di cui vai fiero
non contribuisca nulla.

L'ablazione risponde a questa seconda domanda: si toglie un componente, si lascia tutto il
resto identico, e si misura quanto peggiora il risultato. Il nome viene dalla biologia
sperimentale, dove si rimuove una struttura per vedere quale funzione si perde. Il vincolo
metodologico e' la parola "identico": stessi scenari, stesso seed, stessi limiti di tempo,
una sola variabile cambiata per volta.

Solver senza vincoli tradotti. Si esegue la rigiocata iniettando gli ordini del coordinatore
e poi ignorandoli, cioe' passando al solver un `ConstraintSet` vuoto. Se le metriche non
cambiano, o gli ordini del corpus sono irrilevanti oppure i vincoli non arrivano al modello,
e in entrambi i casi la scoperta e' importante. Attenzione a cosa si misura: i vincoli del
coordinatore spesso peggiorano le metriche generali, perche' esprimono conoscenza che le
metriche non contengono ("lasciate stare quel fronte, ci sono linee elettriche"). L'ablazione
va quindi letta come "quanto cambia il piano", per esempio con la frazione di assegnazioni
diverse, non solo come "quanto migliora la copertura".

Pipeline senza validatore. Si esegue tutto lasciando girare il validatore in sola
osservazione, senza che respinga nulla, e si conta quanti piani sarebbero stati mostrati come
validi pur violando una regola dura, con quale regola e con quale scarto. Il risultato utile
non e' una metrica di qualita' ma un conteggio di fallimenti evitati. Se e' zero su tutta la
stagione va detto: significa che il solver e' corretto sui casi incontrati, e il validatore
resta come garanzia sui casi non incontrati.

## 7. La valutazione del traduttore come compito separato

Il traduttore non va valutato guardando i piani finali, perche' un errore di traduzione puo'
non cambiare il piano (il vincolo non mordeva) e una traduzione perfetta puo' accompagnare un
piano mediocre. Sono due compiti diversi e vogliono due valutazioni diverse.

Vale la pena ricordare perche' il traduttore ha un compito cosi' stretto, visto che e' la
scelta di progetto che questa valutazione deve mettere alla prova. Un modello matematico generato a ogni ordine non si puo' collaudare una volta per tutte, e un vincolo dimenticato non produce un errore ma un piano troppo buono. La conseguenza e' che il modello matematico si scrive a mano
una volta sola e al traduttore resta il compito di riconoscere quale vincolo gia' implementato
l'ordine sta chiedendo: e' quel compito, e non altro, che va misurato.

Il corpus. Si raccolgono ordini in italiano come li direbbe un coordinatore, compresi quelli
brutti: frasi interrotte, doppie negazioni, nomi di localita' scritti male, richieste
impossibili. Per ciascuno si annota a mano il `ConstraintSet` atteso.

L'annotazione va fatta prima di guardare cosa produce il modello, per un effetto cognitivo
ben documentato: vedere una risposta plausibile sposta il giudizio su cosa fosse la risposta
giusta, e chi annota dopo ratifica l'uscita del modello invece di valutarla, producendo un
punteggio gonfiato e senza valore predittivo. Se puoi, fai annotare a due persone
separatamente e misura quanto concordano: se due esseri umani non concordano su cosa voglia
dire un ordine, nessun punteggio sul modello sara' interpretabile. Se il corpus lo scrivi tu
che scrivi anche il prompt, dichiaralo come limite.

Corrispondenza esatta dell'insieme. La frazione di ordini in cui il `ConstraintSet` prodotto
coincide con quello atteso. E' severa e utile, ma richiede di definire cosa significhi
coincidere: `constraint_id`, `source_span`, `rationale`, `confidence` e l'ordine della lista
non devono contare, mentre `kind`, `enforcement` e i parametri devono. Serve quindi una forma
canonica che riduca due insiemi equivalenti alla stessa rappresentazione, e scriverla e' parte
del lavoro.

Precisione e richiamo per tipo di vincolo. Sui dodici valori di `ConstraintKind` (undici
vincoli piu' `UNSUPPORTED`), la precisione e' la frazione di vincoli emessi di quel tipo che
erano corretti, il richiamo la frazione di vincoli attesi che sono stati emessi. Separarli
conta perche' i due errori hanno costi diversi: mancare un `RESERVE_VEHICLE` fa volare un
mezzo che doveva restare a terra, inventarne uno immobilizza un mezzo senza motivo.

Tasso di allucinazione. La frazione di vincoli emessi che non corrispondono a nulla
nell'ordine. Il campo `source_span` serve a renderlo verificabile: un vincolo il cui
frammento citato non compare nel testo e' allucinato per costruzione.

Tasso di astensione corretta. Sugli ordini deliberatamente fuori repertorio, quante volte il
traduttore emette `UNSUPPORTED` invece di inventare un vincolo plausibile. Senza ordini fuori
repertorio nel corpus, il numero non e' basso, e' indefinito.

Matrice di confusione fra tipi. Una tabella dodici per dodici con il tipo atteso in riga e
quello prodotto in colonna, per vedere quali coppie si scambiano. Le coppie confondibili sono
prevedibili: `RESERVE_VEHICLE` contro `EXCLUDE_VEHICLE` (tenere di riserva non e' escludere),
`FORCE_ASSIGNMENT` contro `MIN_VEHICLES_ON_FIRE`, `PRIORITIZE_ZONE` contro
`PROTECT_INFRASTRUCTURE`. Una confusione sistematica si aggiusta chiarendo il prompt o lo
schema, e senza matrice resta invisibile dentro una media.

## 8. Riproducibilita'

Un risultato che non si riproduce non e' un risultato: e' un aneddoto. Se rieseguendo lo
stesso comando ottieni numeri diversi, non puoi distinguere un miglioramento vero dal rumore
della tua macchina, e non puoi nemmeno sapere quale versione del codice ha prodotto il numero
che stai mostrando.

Le sorgenti di non determinismo qui, e cosa si fa.

Il solver. CP-SAT esplora in parallelo con piu' strategie, e la prima che trova la soluzione
vince: con piu' worker l'esito dipende da come il sistema operativo ha schedulato i thread.
Per questo le valutazioni girano con `SOLVER_NUM_WORKERS_DETERMINISTIC = 1` e
`SOLVER_RANDOM_SEED = 20250915`. Resta un punto da sapere: anche con un solo worker, un
limite espresso in secondi di orologio non e' deterministico, perche' su una macchina carica
il solver esplora meno nodi e restituisce un piano diverso. OR-Tools offre anche un limite di
tempo deterministico, `max_deterministic_time`, che non conta secondi ma unita' di lavoro
interne: a parita' di modello il solver si ferma sempre nello stesso punto, su qualunque
macchina e sotto qualunque carico. E' quello che il progetto usa nelle valutazioni, tenendo
`SOLVER_MAX_SECONDS` come ordine di grandezza del budget.

Il clustering. DBSCAN e' deterministico nell'insieme dei cluster densi, ma i punti di
frontiera possono finire nell'uno o nell'altro a seconda dell'ordine di scansione. Qui
`CLUSTER_MIN_SAMPLES = 1` rende ogni punto un punto centrale, quindi non esistono punti di
frontiera e i cluster sono esattamente le componenti connesse del grafo delle vicinanze: un
insieme che non dipende dall'ordine. Resta che le etichette numeriche dipendono dall'ordine di
ingresso, quindi gli identificativi dei focolai vanno derivati da qualcosa di stabile, per
esempio il rilevamento piu' antico del gruppo.

Le dipendenze. `ortools==9.11.4210` e' bloccata, e non per pignoleria: la 9.15 va in
segmentation fault su questa macchina. Cambiare versione del solver puo' cambiare i piani
anche a parita' di seed, perche' cambiano le euristiche interne.

L'ambiente dell'esperimento. Nessuna rete durante la valutazione, perche' i dati FIRMS
cambiano nel tempo: si parte dalle fixture in `data/fixtures/`. E insieme a ogni risultato si
salvano l'impronta dei dati di ingresso, il commit del codice e i valori di `config.py`
usati, cosi' che un numero letto fra sei mesi si possa ricollegare a cio' che lo ha prodotto.

## 9. Significativita'

Con poche decine di finestre, una differenza piccola non significa niente: anche due
pianificatori identici, valutati su venti scenari, daranno numeri leggermente diversi, e la
domanda e' se la differenza osservata sia piu' grande di quella che il caso produce da solo.

Il confronto giusto qui e' appaiato. Ogni finestra viene pianificata due volte, da
Tagliafuoco e dalla baseline, sugli stessi identici dati. La coppia (stesso scenario, due
pianificatori) elimina la variabilita' dovuta alla difficolta' dello scenario, che e' enorme:
una finestra con due focolai vicini alla base e una con diciotto sparsi sull'Aspromonte non
sono confrontabili fra loro, ma i due piani per la stessa finestra si'. Non si confrontano
due medie, si analizza la lista delle differenze finestra per finestra.

Il test di permutazione non richiede nessuna ipotesi sulla forma della distribuzione. Se i
due pianificatori fossero equivalenti, l'etichetta "Tagliafuoco" o "baseline" dentro ogni
coppia sarebbe intercambiabile, quindi il segno di ogni differenza sarebbe arbitrario. Si
enumerano allora tutti i modi di ribaltare i segni, si ricalcola ogni volta la media, e si
guarda quanto spesso il caso produce una media estrema quanto quella osservata.

Un esempio svolto. Su dodici finestre, la differenza fra il novantesimo percentile del tempo
di risposta della baseline e quello di Tagliafuoco (positiva quando Tagliafuoco e' meglio)
vale in minuti: 6.2, -1.4, 3.8, 0.9, 11.5, -0.3, 4.1, 2.7, -2.2, 7.0, 1.1, 5.4. La somma e'
38.8 e la media 3.23 minuti. Le combinazioni di segni sono 2 elevato a 12, cioe' 4096, e si
enumerano tutte: in 60 di esse la media in valore assoluto raggiunge o supera 3.23, quindi il
valore p vale 60 diviso 4096, circa 0.015. Per vedere che il metodo sa anche dire di no,
prendi una lista che oscilla intorno allo zero, per esempio 0.8, -1.1, 0.4, -0.6, 1.3, -0.9,
0.2, -0.4, 1.0, -1.2, 0.5, -0.7: la media vale -0.06 minuti, e lo stesso conteggio trova 3442
casi su 4096, cioe' p circa 0.84, che e' il modo formale di dire "queste differenze sono
quelle che il caso produce comunque".

L'intervallo di confidenza bootstrap risponde alla domanda piu' utile, cioe' quanto e' grande
la differenza. Il bootstrap ricampiona le dodici finestre con reimmissione, ricalcola la
media su ogni ricampionamento, e legge i percentili 2.5 e 97.5 della distribuzione ottenuta.
Sul primo esempio l'intervallo al 95 per cento va da circa 1.2 a circa 5.5 minuti: il
guadagno esiste, ma potrebbe essere di poco piu' di un minuto, e dirlo cosi' e' piu' onesto
che dire "migliora di 3.2 minuti".

Il confronto multiplo. Le metriche sono sei, e provandole tutte al 5 per cento la
probabilita' che almeno una risulti significativa per caso e' 1 meno 0.95 elevato a 6, circa
il 26 per cento. Cercare fra sei metriche quella che funziona e riportare solo quella e' un
modo garantito di ingannarsi. La difesa e' dichiarare prima la metrica principale (qui la
copertura in interfaccia urbano-foresta e' la candidata naturale), riportare comunque tutte
le altre, e trattare le secondarie come indicazioni e non come prove.

## 10. Come non barare

Le quattro scorciatoie che rendono un risultato privo di valore, tutte facili da imboccare
senza cattive intenzioni.

Non scegliere le finestre a posteriori. Il criterio di inclusione (per esempio: almeno tre
focolai e almeno due mezzi disponibili) si fissa prima di guardare i risultati e si applica
uguale ai due pianificatori. Scartare dopo le finestre in cui la baseline vince e' barare
anche quando la giustificazione sembra tecnica.

Non tarare i parametri sugli stessi dati su cui riporti il risultato. I pesi dell'obiettivo,
le soglie e i limiti di tempo sono scelte: se le fai guardando la copertura del 2025 e poi
riporti la copertura del 2025, stai riportando quanto bene hai tarato. La difesa e' dividere,
tarando su una parte della stagione (o su quella precedente) e riportando sull'altra.

Non peggiorare la baseline. Ogni volta che una scelta di implementazione la rende un po' piu'
scema, il risultato migliora gratis. Merita la stessa cura del sistema: stessi dati, stessi
vincoli fisici, stesso validatore, e una lettura del codice fatta apposta per cercare
svantaggi involontari.

Non riportare solo la metrica che conviene. Se la copertura migliora e il costo raddoppia, il
titolo onesto non e' "copertura migliorata", e' "copertura migliorata a costo raddoppiato".
Vale anche per le ablazioni: quella che mostra che un componente non serve va riportata come
le altre, e un lettore tecnico si fida molto di piu' di un lavoro che dichiara cosa non ha
funzionato.

## 11. Cosa devi saper spiegare

**Perche' una baseline e' necessaria, e come ne scegli una onesta?**
Perche' un numero da solo misura insieme la difficolta' dello scenario e la bravura del
pianificatore; il confronto sugli stessi scenari elide la difficolta'. Onesta significa che
la baseline e' cio' che si farebbe davvero senza il sistema, cioe' mandare il mezzo libero
piu' vicino, non una procedura costruita per perdere, e che rispetta gli stessi vincoli
fisici: il validatore indipendente gira anche sui suoi piani.

**Perche' due baseline e non una?**
Perche' misurano due cose. Quella ordinata per istante di rilevamento e' il primo arrivato,
primo servito, e la distanza da essa misura tutto il guadagno insieme. Quella ordinata per
priorita' incorpora gia' l'idea che alcuni focolai contino di piu', quindi la distanza da
essa isola il contributo dell'ottimizzazione combinatoria. Se batto la prima ma non la
seconda, il valore del progetto sta nell'aver formalizzato la priorita', ed e' giusto dirlo.

**Qual e' il limite piu' grave della vostra valutazione?**
Che sappiamo cosa il sistema avrebbe proposto, non cosa sarebbe successo. Non modelliamo la
propagazione del fuoco ne' la sua reazione all'intervento, quindi i focolai nella rigiocata
sono fotografie immobili. Misuriamo variabili operative intermedie e non esiti sul
territorio: non posso dire che avremmo bruciato meno ettari, perche' gli ettari non sono nel
modello.

**Che cos'e' la fuga di informazione dal futuro e come la eviti qui?**
E' usare, per decidere all'istante t, informazione non disponibile a t. E' subdola perche'
migliora i risultati invece di rompere qualcosa: niente diventa rosso, il numero sembra solo
ottimo. La regola e' che lo `Scenario` a t contenga solo rilevamenti con `acquired_at <= t`,
e i punti da sorvegliare sono il clustering che raccoglie oltre t, l'arricchimento che usa
l'area finale del focolaio, le costanti di normalizzazione tarate sull'intera stagione e lo
stato della flotta dedotto da cio' che e' accaduto dopo. E' lo stesso errore del backtesting
finanziario, con la stessa soluzione: avanzare una finestra alla volta.

**Come definite "coperto", e perche' non basta "almeno un mezzo assegnato"?**
Perche' quella definizione si inganna in un secondo: un mezzo per un minuto su ogni focolaio
porta la copertura al 100 per cento senza spegnere niente. `SPEC.md` definisce coperto il
focolaio che ha ricevuto i litri del suo fabbisogno, e lascia la regola generosa solo ai
focolai senza fabbisogno stimato, dove la frazione non sarebbe definita. L'obiezione giusta
e' che cosi' la copertura dipende da una taratura, `WATER_LITERS_PER_KM2`, e la risposta e'
che quella soglia va pubblicata insieme al numero.

**Perche' riporti il novantesimo percentile e non solo la media?**
Perche' la media nasconde la coda: dieci interventi da 10 minuti e uno da 120 fanno una media
di 20, mentre quel focolaio e' rimasto solo due ore, ed e' li' che un fuoco diventa
incontrollabile. Il novantesimo percentile descrive il caso brutto tipico senza farsi
spostare da un singolo estremo. Inoltre il tempo di risposta esiste solo per i focolai
serviti, quindi chi ignora i casi difficili ottiene tempi eccellenti: va letto sempre insieme
alla copertura, e con il metodo di calcolo del percentile dichiarato.

**Che differenza c'e' fra ablazione e confronto con la baseline?**
La baseline dice se l'insieme e' migliore dell'alternativa; l'ablazione dice quanto
contribuisce ciascun pezzo, togliendolo e lasciando identico tutto il resto. Qui tolgo i
vincoli tradotti (passando un `ConstraintSet` vuoto) e il validatore (lasciandolo osservare
senza respingere). Per i vincoli conta misurare quanto cambia il piano e non solo se le
metriche migliorano, perche' un ordine del coordinatore spesso le peggiora portando conoscenza
che le metriche non contengono.

**Con trenta finestre, come fai a dire che la differenza non e' rumore?**
Con un test appaiato, perche' la variabilita' fra scenari e' enorme mentre i due piani per la
stessa finestra sono confrontabili. Uso un test di permutazione sui segni delle differenze:
se i due pianificatori fossero equivalenti il segno sarebbe arbitrario, quindi enumero i
2 elevato a n ribaltamenti e conto quanto spesso il caso raggiunge la differenza osservata.
Su dodici finestre con media 3.23 minuti ottengo p circa 0.015, e affianco un intervallo
bootstrap che va da 1.2 a 5.5 minuti. Infine dichiaro prima la metrica principale, perche'
con sei metriche al 5 per cento la probabilita' che almeno una sembri significativa per caso
e' circa il 26 per cento.

## 12. Per approfondire

Sulla fuga di informazione la letteratura piu' utile non e' quella accademica ma quella sul
backtesting quantitativo, dove l'errore costa soldi ed e' quindi documentato bene. Cerca
"look-ahead bias" e "survivorship bias" insieme a "walk-forward analysis": il vocabolario e'
finanziario, il problema e' identico al nostro. Sul versante dell'apprendimento automatico
cerca i lavori recenti sulla fuga di informazione come causa di risultati non riproducibili
in molte discipline applicate, che raccolgono casi concreti e li classificano. Per la parte
pratica, la documentazione di scikit-learn spiega bene, con figure, perche' la validazione
incrociata ordinaria non si usa sulle serie storiche e come funziona il taglio in blocchi
ordinati.

Sui test di permutazione e sul bootstrap cerca i testi divulgativi che li presentano come
metodi di ricampionamento, di solito sotto l'etichetta "statistica computazionale". Il punto
da cogliere e' che entrambi sostituiscono un'ipotesi sulla forma della distribuzione con
lavoro di calcolo, il che li rende adatti ai campioni piccoli come il nostro. Cerca anche la
distinzione fra test appaiato e non appaiato, perche' usare quello sbagliato e' l'errore piu'
frequente.

Sul confronto multiplo cerca "multiple comparisons problem" insieme alle correzioni di
Bonferroni e di Benjamini-Hochberg, e leggi anche la critica a quelle correzioni: il rimedio
metodologicamente piu' solido non e' aggiustare i valori p ma dichiarare in anticipo la
metrica principale.

Sulla valutazione di sistemi con esseri umani nel percorso, cerca la letteratura sull'accordo
fra annotatori (statistica kappa di Cohen e affini) e sugli effetti di ancoraggio: serve a
capire perche' annotare prima e guardare il modello dopo non e' una formalita'. Infine, sulla
valutazione controfattuale, cerca il vocabolario dell'inferenza causale (esito potenziale,
controfattuale, confondente): non serve la matematica completa per trarne il beneficio
principale, che e' imparare a dire con precisione cosa si sta misurando e cosa no.
