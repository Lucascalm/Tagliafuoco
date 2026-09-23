# Clustering spaziotemporale: dai pixel ai focolai

Come Tagliafuoco trasforma un elenco di pixel caldi satellitari in un elenco di focolai su cui
il solver puo' ragionare, perche' usa DBSCAN e non k-means, come sono tarate le due costanti
che lo governano e cosa il metodo non riesce a fare.

## Indice

1. Il problema: il solver non sa cosa farsene di un pixel
2. Cosa arriva davvero da FIRMS
3. Perche' non k-means
4. DBSCAN: la densita' al posto dei centroidi
5. L'algoritmo e l'ordine di visita
6. Costo computazionale
7. Scegliere eps: il grafico della distanza al k-esimo vicino
8. Le scelte di Tagliafuoco: eps 1.5 km, minPts 1
9. La metrica haversine dentro scikit-learn
10. La dimensione temporale
11. Alternative da conoscere
12. Il centroide pesato sulla FRP
13. Cosa devi saper spiegare
14. Per approfondire

---

## 1. Il problema: il solver non sa cosa farsene di un pixel

In `domain/models.py` stanno due classi vicine. `Detection` e' un pixel caldo: punto, istante,
satellite, confidenza, potenza radiativa. `FireCluster` e' un focolaio: centroide, area, litri
d'acqua, priorita', comune, copertura del suolo. `Scenario.fires` ha tipo `list[FireCluster]`
e `Assignment` porta un `fire_id`, non un `detection_id`.

Un incendio lungo dieci chilometri su un crinale della Sila genera venti o trenta pixel VIIRS
in un solo passaggio. Passarli al solver come trenta focolai rompe quattro cose.
`MinVehiclesOnFire` con `count=3` diventa una richiesta di novanta mezzi, e in Calabria non ce
ne sono novanta. La metrica `coverage_ratio` conta trenta focolai coperti dove ce n'e' uno. E
il coordinatore, che dice "l'incendio sopra Cirella", non ha nulla a cui agganciare quella
frase, perche' `nlu/resolver.py` mappa i nomi di luogo su `fire_id`.

La quarta cosa che si rompe merita il conto per esteso, perche' a colloquio si liquida come "i
numeri vengono sbagliati" senza saper dire in che verso. Il fabbisogno d'acqua di `SPEC.md`
vale `area_km2 * 100_000 * coeff_copertura * coeff_intensita`, con `coeff_intensita = 1 +
min(1.0, max_frp_mw / 200)`. Quel coefficiente dipende dal **massimo** della FRP sul focolaio,
non dalla somma, e satura a 2.0. Sul focolaio intero il massimo si applica a tutta l'area;
spezzandolo in trenta pezzi ognuno porta il proprio massimo, piu' basso, e la somma dei trenta
conti viene minore del conto giusto. In senso opposto le impronte dei pixel si sovrappongono,
fra passaggi successivi e fra sensori diversi, quindi sommare trenta aree nominali conta piu'
volte lo stesso terreno e gonfia il totale. I due errori non si cancellano: il primo dipende
dalla distribuzione della FRP, il secondo dalla geometria di osservazione, e chi guarda solo
il totale non ha modo di accorgersene.

C'e' anche un costo di calcolo. Il modello del capitolo 6 mette una booleana `x[v,f]` su ogni
coppia mezzo-focolaio ammissibile, e accanto a ognuna un numero di lanci e un intervallo
opzionale con il suo vincolo di non sovrapposizione. Trenta mezzi e centoventi pixel fanno
3600 coppie; se quei pixel sono in realta' sei focolai, le coppie diventano 180. Il conto e'
un tetto, perche' il modello pota in partenza le coppie impossibili, per esempio un mezzo che
non rientrerebbe mai in autonomia. Ma il problema vero non e' quante variabili ci sono: e' su
cosa il risolutore le spende, cioe' a decidere quale pixel dello stesso incendio servire per
primo, che non e' una domanda. Il clustering e' il punto in cui i dati smettono di essere
osservazioni e diventano oggetti di dominio.

## 2. Cosa arriva davvero da FIRMS

```python
FIRMS_SOURCES_NRT = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_NRT"]
```

VIIRS (Visible Infrared Imaging Radiometer Suite, sui satelliti Suomi NPP e NOAA-20) produce
il prodotto antincendio a 375 metri di lato al nadir, cioe' guardando dritto sotto di se'.
MODIS (Moderate Resolution Imaging Spectroradiometer, su Terra e Aqua) lavora a un chilometro.
Allontanandosi dal nadir il pixel si allarga, perche' la stessa apertura angolare taglia sul
terreno una impronta obliqua e piu' lunga, e i campi `scan_km` e `track_km` di `Detection`
registrano quanto. La crescita non e' uguale per i due sensori: il prodotto VIIRS a 375 metri
aggrega i campioni a bordo con uno schema che la tiene sotto controllo, e al bordo della
striscia il pixel resta di poco sotto il chilometro, mentre il pixel MODIS da un chilometro al
nadir arriva a qualche chilometro di lato. I valori esatti stanno nelle guide di prodotto; qui
conta l'ordine di grandezza, perche' al paragrafo 8 `eps` si giustifica contando lati di
pixel.

Il docstring di `Detection` dice la cosa importante: un rilevamento non e' un incendio, e' un
pixel la cui radianza nell'infrarosso medio supera quella dello sfondo. Puo' essere una torcia
di raffineria, un campo di stoppie, un tetto di lamiera che riflette il sole.

Tre conseguenze. Lo stesso incendio produce molti pixel nello stesso passaggio, perche' e'
piu' grande di un pixel. Ne produce da piu' piattaforme: NOAA-20 e Suomi NPP volano sullo
stesso piano orbitale a circa cinquanta minuti di distanza, quindi nella stessa ora lo stesso
fronte compare due volte, con i centri dei pixel spostati di qualche centinaio di metri.
Attenzione a non chiamarlo errore di puntamento: la geolocalizzazione VIIRS e' buona a una
frazione di pixel. Lo spostamento c'e' perche' i due passaggi campionano il terreno con
griglie diverse e da angoli diversi, quindi lo stesso fuoco cade in celle diverse. E il fronte
produce pixel anche nei passaggi successivi, dopo essersi mosso.

Il filtro di qualita' sta prima, in ingest, con `MIN_CONFIDENCE = 30.0`. E' il posto giusto:
il clustering fa geometria, non controllo qualita'.

## 3. Perche' non k-means

k-means minimizza la somma dei quadrati delle distanze dai centroidi:

```
J = somma su j da 1 a k di [ somma su x nel gruppo C_j di || x - mu_j ||^2 ]
```

dove `x` e' un punto, `mu_j` il centroide del gruppo `j`, `k` il numero di gruppi, `|| . ||`
la norma euclidea. L'algoritmo di Lloyd assegna ogni punto al centroide piu' vicino, ricalcola
i centroidi come media dei punti assegnati, ripete, e converge a un minimo locale che dipende
dall'inizializzazione.

Devi sapere `k`. Alle tre del mattino del 3 agosto nessuno sa quanti incendi ci sono in
Calabria: quella e' la risposta che il sistema deve produrre. Il metodo del gomito, la
silhouette e il criterio gap stimano `k`, ma richiedono molte esecuzioni e presuppongono
gruppi ben separati e compatti, l'ipotesi che qui non vale.

L'assegnazione al centroide piu' vicino partiziona lo spazio in celle di Voronoi, che sono
convesse, quindi k-means puo' produrre solo gruppi convessi. Un fronte di fuoco e' una linea
curva, lunga chilometri e larga poche centinaia di metri. Ho costruito un fronte di otto
rilevamenti equidistanti orientati da sud-ovest a nord-est, passo 1.408 km, da (39.2600 N,
16.3900 E) a (39.3230 N, 16.4705 E), lungo 9.85 km. Con scikit-learn 1.7:

```
KMeans(n_clusters=2, random_state=0, n_init=10) : [0 0 0 0 1 1 1 1]   inerzia 2.133e-03
KMeans(n_clusters=3, random_state=0, n_init=10) : [1 1 1 0 0 2 2 2]   inerzia 9.596e-04
DBSCAN(eps=1.5 km, min_samples=1)               : [0 0 0 0 0 0 0 0]
```

Le etichette sono nomi arbitrari e cambiano con il seme: cio' che non cambia e' la forma della
partizione. Con `k=2` il fronte viene spezzato a meta' in due segmenti contigui da quattro
punti, con `k=3` in tre segmenti da 3, 2 e 3. Non perche' l'algoritmo sia mal configurato, ma
perche' un oggetto allungato assegnato per prossimita' a `k` centroidi viene per forza
segmentato.

Quel `n_init=10` non e' contorno: e' il secondo difetto, distinto dal primo. `n_init` dice
quante inizializzazioni casuali k-means prova prima di tenere la migliore, e in scikit-learn
1.7 il default e' `"auto"`, che con `init="k-means++"` significa **una sola**. Con una sola,
seme 0 e `k=2`, lo stesso fronte da' `[1 1 1 0 0 0 0 0]`, un taglio 3-5 di inerzia 2.559e-03
invece di 2.133e-03: un minimo locale in cui Lloyd si e' fermato. Con `k=3` la partizione
balla al variare del seme (3-2-3 con il seme 0, 3-3-2 con il seme 3, un pessimo 4-3-1 da
1.493e-03 con il seme 4). Quindi k-means, oltre a segmentare per costruzione, risponde in modo
diverso a seconda di dove sono cadute le sementi, e difendersi alzando `n_init` costa
esecuzioni. DBSCAN non ha inizializzazione: questo problema non ce l'ha.

Restano quattro difetti. Le distanze sono al quadrato, quindi un falso positivo a venti
chilometri sposta il centroide e trascina i punti di confine. Non esiste il concetto di
rumore. I gruppi sono presupposti di dimensione simile, perche' la lettura probabilistica di
k-means e' una miscela di gaussiane sferiche con uguale varianza e uguali pesi, quindi un
incendio da quaranta pixel ruba i punti di confine a uno da due. E k-means richiede una media,
definita in uno spazio euclideo, mentre latitudine e longitudine in gradi non lo sono
(capitolo 12).

In breve, k-means risponde a "come divido questi punti in k gruppi compatti", mentre la
domanda operativa e' "quanti incendi ci sono e quali pixel appartengono a ciascuno". La
concessione da fare a colloquio: per ripartire i focolai fra dodici squadre `k` sarebbe noto e
la compattezza desiderabile, ma quello e' un problema di carico, e qui lo risolve il solver.

## 4. DBSCAN: la densita' al posto dei centroidi

DBSCAN (Density-Based Spatial Clustering of Applications with Noise, di Ester, Kriegel, Sander
e Xu, meta' anni novanta) non cerca centri: cerca regioni in cui i punti stanno fitti, e
chiama gruppo ogni regione fitta massimale. Ha due parametri: `eps`, un raggio nell'unita'
della metrica scelta, e `minPts`, quanti punti devono stare in quel raggio perche' la regione
si dica densa.

L'intorno eps di un punto `p` nel dataset `D` e'

```
N_eps(p) = { q appartenente a D : dist(p, q) <= eps }
```

e contiene `p` stesso, perche' `dist(p, p) = 0`. Il dettaglio conta al paragrafo 8.

Un punto e' **centrale** (core point) se `|N_eps(p)| >= minPts`. E' **di frontiera** (border
point) se non e' centrale ma sta nell'intorno di un punto centrale: abbastanza vicino da
appartenere alla regione densa, ma con i punti che si diradano attorno. Se non e' ne' l'uno
ne' l'altro e' **rumore** (noise), etichettato `-1`.

La **density-reachability**: `q` e' direttamente raggiungibile per densita' da `p` se `p` e'
centrale e `q` sta in `N_eps(p)`. La relazione e' asimmetrica, perche' un punto di frontiera
non propaga. `q` e' raggiungibile per densita' da `p` se esiste una catena `p = p_1, ..., p_n
= q` in cui ogni anello e' direttamente raggiungibile dal precedente: la catena permette a un
gruppo di allungarsi senza limite, ed e' per questo che DBSCAN cattura i fronti.

La **density-connectivity** ripara l'asimmetria: `p` e `q` sono connessi per densita' se
esiste un terzo punto `o` da cui entrambi sono raggiungibili. E' simmetrica, e un gruppo e' un
insieme massimale di punti connessi per densita'. Serve ai punti di frontiera, perche' due di
essi sui lati opposti di un gruppo possono distare molto piu' di `eps` senza che nessuno dei
due propaghi.

Cinque rilevamenti allineati su un crinale silano, piu' due isolati:

```
P1  39.3120 N 16.4410 E  18 MW     P5  39.3262 N 16.4631 E  12 MW
P2  39.3155 N 16.4465 E  42 MW     P6  39.2905 N 16.4890 E   9 MW
P3  39.3190 N 16.4520 E  77 MW     P7  39.2880 N 16.4935 E  14 MW
P4  39.3228 N 16.4578 E  35 MW

haversine: P1-P2 0.61, P2-P3 0.61, P3-P4 0.65, P4-P5 0.59 km
           P1-P5 2.47 km ; P6-P7 0.48 km
           salto minimo fra i due gruppi: P4-P6 4.48 km (P5-P6 4.55)
```

Con `eps = 1.5 km`, P1 e P5 distano 2.47 km e non sono vicini diretti, ma finiscono nello
stesso focolaio perche' sono connessi per densita' lungo la catena P1, P2, P3, P4, P5, a passi
di circa 0.6 km. P6 e P7 restano separati perche' il salto piu' corto verso l'altro gruppo,
che e' P4-P6 e non P5-P6, vale 4.48 km, tre volte `eps`. Verificato: `[0 0 0 0 0 1 1]`.

## 5. L'algoritmo e l'ordine di visita

Si scorrono i punti in un ordine qualsiasi. Per ogni `p` non etichettato si calcola
`N_eps(p)`, cioe' si fa una interrogazione di intorno (region query). Se `|N_eps(p)| <
minPts`, `p` si marca provvisoriamente rumore, perche' potrebbe poi cadere nell'intorno di un
punto centrale e diventare punto di frontiera. Altrimenti `p` e' centrale: si apre un gruppo e
tutto `N_eps(p)` entra in una coda. Per ogni `q` estratto: se era rumore si riassegna al
gruppo come punto di frontiera; se e' gia' etichettato si lascia stare; se non lo e' si
assegna al gruppo, e solo se anche `q` e' centrale i suoi vicini entrano in coda.

Ne segue la proprieta' che a colloquio si chiede sempre: **l'ordine di visita puo' cambiare
l'assegnazione dei punti di frontiera, e solo di quelli**. Un punto di frontiera nell'intorno
di due punti centrali di gruppi diversi non puo' fonderli, e finisce nel gruppo che lo
raggiunge per primo. I punti centrali sono deterministici, perche' la connessione per densita'
fra loro e' una relazione di equivalenza. La documentazione di scikit-learn lo dichiara.

## 6. Costo computazionale

Il costo e' dominato dalle interrogazioni di intorno, una per punto. Senza indice ognuna e'
una scansione lineare, `O(n)`, e il totale e' `O(n^2)`; precalcolando la matrice delle
distanze anche la memoria e' `O(n^2)`, e con centomila punti sono ottanta gigabyte. Con un
indice spaziale (R-tree, k-d tree, ball tree) una interrogazione costa in media `O(log n)` in
bassa dimensione su dati non patologici, e il totale scende a `O(n log n)`.

`O(n log n)` pero' e' un comportamento medio, non una garanzia: un indice ad albero degrada
quando i suoi rettangoli o le sue sfere si sovrappongono molto, e i rilevamenti di incendio
stanno tutti su pochi crinali e nulla in mezzo, che e' il caso sfavorevole.

La teoria dice qualcosa di piu' preciso, e per una volta gioca a favore. In due dimensioni
esiste un algoritmo esatto che chiude anche il caso peggiore in `O(n log n)`, e il clustering
di Tagliafuoco e' esattamente bidimensionale, perche' le coordinate sono due e il tempo si
tratta a parte (paragrafo 10). Da tre dimensioni in su la letteratura riporta invece una
barriera condizionata: nessun algoritmo esatto scende sostanzialmente sotto `n^(4/3)` a meno
di risolvere piu' in fretta un problema classico di geometria computazionale che si ritiene
difficile. Non raccontarla come "in alta dimensione DBSCAN e' quadratico": la barriera sta a
`n^(4/3)`, ben sotto il quadratico. Riporto il risultato a parole perche' non voglio
attribuire autori e anno a memoria.

Qui non incide. Il riquadro di interesse del progetto, `CALABRIA_BBOX`, e' largo circa 147 km
e lungo circa 261 km (capitolo 12), e in una giornata di agosto molto brutta i rilevamenti
sono nell'ordine delle centinaia. Con `n = 2000`, `n^2` fa quattro milioni di distanze,
qualche decina di millisecondi in NumPy. L'asintotica conta se estendi il sistema al
Mediterraneo.

## 7. Scegliere eps: il grafico della distanza al k-esimo vicino

Si fissa `k` uguale a `minPts` (o `minPts - 1`, la letteratura non e' uniforme e conviene
dichiarare la convenzione), per ogni punto si calcola la distanza al suo `k`-esimo vicino piu'
prossimo, si ordinano le `n` distanze in modo decrescente e si disegna la curva. La forma e'
caratteristica: alta e ripida a sinistra, dove stanno i punti isolati che hanno il `k`-esimo
vicino lontano; poi un ginocchio; poi un lungo tratto basso, dove stanno i punti interni ai
gruppi. Il ginocchio e' il valore da usare: sopra quella soglia classifichi come denso cio'
che e' sparso, sotto spezzi i gruppi veri. In pratica e' spesso poco netto, e le automazioni
(massima curvatura, massima distanza dalla retta fra gli estremi) restano euristiche.

Con `minPts = 1` il grafico degenera, perche' la distanza al primo vicino e' zero: il primo
vicino e' il punto stesso. Qui va disegnata la curva del secondo o terzo vicino, letta come
indicazione della scala spaziale tipica, e poi `eps` va deciso con l'argomento fisico.

## 8. Le scelte di Tagliafuoco: eps 1.5 km, minPts 1

```python
CLUSTER_EPS_KM = 1.5
CLUSTER_MIN_SAMPLES = 1
```

Il conto: `1.5 / 0.375 = 4.0` esatto rispetto al pixel VIIRS al nadir, e 1.5 volte il pixel
MODIS da un chilometro. Il ragionamento e' che due pixel dello stesso fronte non possono
distare molto piu' di qualche lato di pixel. Quattro lati al nadir diventano circa due lati al
bordo della striscia, dove il pixel VIIRS arriva quasi al chilometro (paragrafo 2), e restano
una volta e mezzo il pixel MODIS: il margine c'e' ma non e' generoso, e dichiararlo per quello
che e' vale piu' che chiamarlo "con ampio margine".

E' una taratura dichiarata, non un valore derivato: validarla vorrebbe dire confrontare i
focolai prodotti con un elenco di incendi effettivamente segnalati e prendere l'`eps` che
minimizza fusioni e frammentazioni indebite, e quel riferimento il progetto non ce l'ha. Sui
sette rilevamenti del paragrafo 4 la partizione resta `[0 0 0 0 0 1 1]` per ogni `eps` fra 1.0
e 4.48 km, cioe' su un fattore quattro e mezzo; a 0.5 km il fronte si sbriciola in sei
focolai, e a 4.5 km i due gruppi si fondono, perche' `eps` ha superato il salto P4-P6. Il test
giusto non e' "eps vale 1.5" ma "la risposta non cambia dentro l'intervallo ragionevole".

Ora `minPts = 1`. `N_eps(p)` contiene `p` stesso, e scikit-learn dichiara che `min_samples`
include il punto stesso, quindi ogni punto e' centrale: non esistono punti di frontiera e non
esiste rumore. DBSCAN degenera. Se tutti i punti sono centrali la raggiungibilita' per
densita' diventa simmetrica, coincide con la connessione per densita', e un gruppo e' una
componente connessa del grafo che ha per nodi i rilevamenti e per archi le coppie entro `eps`.
Questo e' il clustering a soglia di distanza, ed e' anche il gerarchico agglomerativo con
legame singolo tagliato a `eps`: verificato, `AgglomerativeClustering(linkage="single",
distance_threshold=1.5)` restituisce la stessa partizione.

La conseguenza buona: l'ambiguita' dei punti di frontiera sparisce e con essa la dipendenza
dall'ordine di visita, quindi il risultato e' funzione dei soli dati. Per un progetto il cui
`SPEC.md` impone che ogni non determinismo vada eliminato, e' una proprieta' da rivendicare.

Quella cattiva: l'effetto catena (chaining) in pieno. Una fila di rilevamenti a 1.4 km l'uno
dall'altro si fonde in un focolaio lunghissimo anche senza vera densita'. Sul fronte di otto
punti DBSCAN restituisce un unico gruppo lungo 9.85 km, che li' e' giusto, ma due incendi
distinti sui due versanti dello stesso vallone verrebbero fusi allo stesso modo. Il legame
completo non lo farebbe: sui sette punti produce `[0 0 0 2 2 1 1]`.

Perche' allora `minPts = 1`? Perche' qui un falso negativo, un incendio assente dall'elenco,
e' molto piu' grave di un falso positivo, un focolaio spurio a cui si manda un mezzo che non
trova niente. Un `minPts` maggiore scarta come rumore i rilevamenti isolati, e un rilevamento
isolato e' spesso l'incendio appena nato: con `minPts = 3` il risultato diventa `[0 0 0 0 0 -1
-1]`, e P6 e P7 scompaiono dallo `Scenario`, dove nessun ordine del coordinatore puo' piu'
recuperarli. La difesa dal rumore sta prima, nel filtro di confidenza, e dopo, nella priorita'
di base: il sistema non butta via informazione, la ordina.

## 9. La metrica haversine dentro scikit-learn

`metric="haversine"` pretende due cose che la firma non dichiara. Le coordinate devono essere
in **radianti**, nell'ordine **(latitudine, longitudine)**, l'opposto di GeoJSON e del formato
di bounding box che FIRMS richiede. E la distanza restituita e' l'**angolo al centro in
radianti sulla sfera unitaria**, non una lunghezza, quindi `eps` va diviso per il raggio
terrestre:

```python
coords = np.radians([[d.point.lat, d.point.lon] for d in detections])
labels = DBSCAN(
    eps=CLUSTER_EPS_KM / EARTH_RADIUS_KM,   # 1.5 / 6371 = 2.354418e-04 rad
    min_samples=CLUSTER_MIN_SAMPLES,
    metric="haversine",
    algorithm="ball_tree",
).fit_predict(coords)
```

Il `KDTree` di scikit-learn accetta solo metriche della famiglia di Minkowski (euclidea,
Manhattan, Chebyshev) e quindi non haversine, perche' taglia lo spazio con piani
perpendicolari agli assi e ha bisogno che la distanza si decomponga per coordinate. Il
`BallTree`, che partiziona con ipersfere annidate, chiede solo che la metrica rispetti la
disuguaglianza triangolare, e haversine la rispetta. E' il motivo per cui `algorithm="auto"`
finisce sul `BallTree`, ma solo quando i punti sono piu' di una decina: sotto quella soglia
sceglie la forza bruta, che su insiemi minuscoli costa meno che costruire l'albero.
Verificato: con sette punti `auto` sceglie `brute`, con venti sceglie `ball_tree`.

Se sbagli: gradi non convertiti con `eps` convertito danno `[0 1 2 3 4 5 6]`, sette focolai
singoli; coordinate convertite ma `eps` non diviso per il raggio danno un intorno di 1.5
radianti, cioe' 9556 km, e tutto si fonde in `[0 0 0 0 0 0 0]`. Nessuno dei due errori solleva
una eccezione: entrambi restituiscono un elenco di etichette della lunghezza giusta e
dall'aspetto plausibile, ed e' per questo che il difetto sopravvive nei progetti reali.
L'unica difesa e' un test su un caso costruito a mano con distanze note.

## 10. La dimensione temporale

Due pixel nello stesso punto a venti ore di distanza quasi certamente non sono lo stesso
focolaio: il satellite e' ripassato, l'incendio di ieri puo' essere stato spento, e quello di
oggi puo' essere una ripartenza o un incendio nuovo. Fonderli attribuisce al focolaio di oggi
FRP e area che includono il fuoco di ieri, gonfiando litri e priorita'.

La soluzione adottata e' `CLUSTER_TIME_WINDOW_HOURS = 12.0`: i rilevamenti si dividono in
finestre temporali e dentro ogni finestra si fa il clustering spaziale, quindi due pixel in
finestre diverse non finiscono mai nello stesso focolaio. Da non confondere con
`REPLAY_WINDOW_HOURS = 3`: le dodici ore sono la larghezza della finestra dentro cui due pixel
possono finire nello stesso focolaio, le tre ore sono il passo con cui la rigiocata della
stagione 2025 ripianifica. Sono due grandezze indipendenti, e non c'e' nessun motivo perche'
debbano coincidere. Il metodo e' deterministico e banale da testare. Il difetto e' l'effetto
di bordo: due rilevamenti separati da dieci minuti ma a cavallo di un confine finiscono in
focolai diversi, e capita ogni volta che un satellite passa vicino all'orario di taglio. Si
attenua con finestre scorrevoli sovrapposte, o con una passata di fusione a posteriori.

L'alternativa nota e' **ST-DBSCAN** (Spatio-Temporal DBSCAN, di Birant e Kut), che tratta il
tempo come seconda dimensione con la sua soglia:

```
N(p) = { q : dist_spaziale(p, q) <= eps1  E  |t(p) - t(q)| <= eps2 }
```

Le due condizioni si applicano simultaneamente, non in sequenza, e il bordo arbitrario
sparisce: due rilevamenti a dieci minuti sono sempre temporalmente vicini, ovunque cada il
confine. Gli svantaggi sono che non e' in scikit-learn, quindi va implementato o aggiunto come
dipendenza da dichiarare secondo `SPEC.md`, e che devi tarare due soglie invece di una, la
seconda arbitraria quanto la finestra che sostituisce.

Il limite che entrambi condividono e' la vera obiezione difficile: **nessuno dei due modella
la propagazione**. Il pixel delle 14:00 e quello delle 20:00 dello stesso incendio possono
distare chilometri non per errore di misura ma perche' il fronte e' avanzato. Un modello piu'
fedele e' un cono spaziotemporale:

```
dist_spaziale(p, q) <= eps0 + v_propagazione * |t(p) - t(q)|
```

dove `eps0` copre l'incertezza di misura. Le velocita' di propagazione nella macchia
mediterranea coprono ordini di grandezza molto diversi, da pochi metri al minuto in condizioni
calme fino a valori molto superiori in corse spinte dal vento su pendio: non fisso quel numero
e non lo invento. Ma anche un valore basso basta: a 3 metri al minuto, in dodici ore un fronte
avanza circa 2.2 km, gia' piu' di `eps`. La finestra temporale e `eps` sono quindi accoppiati,
e Tagliafuoco non implementa il cono: e' un limite dichiarato.

## 11. Alternative da conoscere

**OPTICS** (Ankerst, Breunig, Kriegel, Sander). Non fissa `eps`: produce un ordinamento con
una distanza di raggiungibilita' per punto, che disegnata da' un grafico a valli, una per
gruppo. Migliore se i focolai avessero densita' molto diverse nella stessa scena, per esempio
un fronte compattissimo piu' una pioggia di focolai sparsi.

**HDBSCAN** (Campello, Moulavi, Sander; in scikit-learn dalla 1.3). Costruisce la gerarchia di
tutti i DBSCAN al variare di `eps` e seleziona i gruppi piu' stabili, chiedendo solo una
dimensione minima. Migliore se non volessi tarare `eps` affatto, ma qui controindicato perche'
assegna rumore.

**Gerarchico agglomerativo con taglio a distanza.** Con legame singolo e' identico a quello
che il progetto fa oggi. Migliore con legame completo o medio, se volessi limitare
l'estensione massima di un focolaio accettando di spezzare i fronti lunghi.

**Mean shift** (Comaniciu e Meer, su un'idea di Fukunaga e Hostetler). Fa scorrere ogni punto
verso la moda locale della densita' stimata con un nucleo di ampiezza fissata, senza chiedere
`k`. Migliore se ti interessasse il nucleo caldo piu' dell'estensione, ma un fronte lungo con
un solo picco collassa in un punto e perdi l'area.

Merita una riga anche l'unione di due rilevamenti quando le loro impronte al suolo si toccano,
calcolate da `scan_km` e `track_km`: e' l'approccio piu' fedele alla fisica del dato, perche'
la soglia deriva dalla geometria di osservazione invece di essere scelta.

## 12. Il centroide pesato sulla FRP

`FireCluster.centroid` e' il punto su cui si calcolano tutte le distanze: base-fuoco per il
tempo di andata, fuoco-sorgente per il ciclo d'acqua, fuoco-costruito per l'interfaccia
urbano-foresta.

```
geometrico : lat_c = (1/n) * somma delle lat_i
pesato     : lat_c = (somma di frp_i * lat_i) / (somma di frp_i)
```

dove `frp_i` e' il campo `frp_mw` di `Detection` in megawatt e la somma corre sui rilevamenti
del focolaio; le stesse formule valgono per la longitudine. La FRP (Fire Radiative Power) si
stima dall'eccesso di radianza del pixel nell'infrarosso medio rispetto allo sfondo, tramite
una relazione empirica fra radianza in eccesso e potenza emessa, ed e' approssimativamente
proporzionale alla velocita' di combustione della biomassa.

Il geometrico e' il centro dell'estensione osservata, il pesato e' il centro dell'energia
rilasciata. Su un fronte simmetrico coincidono, ma con il vento la testa avanza e brucia
violentemente mentre la coda e' terreno gia' percorso che si raffredda: il geometrico finisce
in mezzo all'area bruciata, il pesato vicino alla testa, dove serve l'acqua.

Il conto sul fronte di otto rilevamenti del paragrafo 3, con la FRP che cresce verso nord-est:

```
        lat        lon    FRP           lat        lon    FRP
 F1  39.2600   16.3900      6     F5  39.2960   16.4360     12
 F2  39.2690   16.4015      8     F6  39.3050   16.4475     55
 F3  39.2780   16.4130      7     F7  39.3140   16.4590    130
 F4  39.2870   16.4245      9     F8  39.3230   16.4705    180
                                          somma FRP = 407 MW
```

Sulla latitudine, lavorando sugli scostamenti da 39.2600 in unita' di 0.0001 gradi (0, 90,
180, 270, 360, 450, 540, 630):

```
geometrico : (0+90+180+270+360+450+540+630) / 8 = 2520 / 8 = 315
             lat_c = 39.2600 + 0.0315 = 39.2915

pesato     : 6*0 + 8*90 + 7*180 + 9*270 + 12*360 + 55*450 + 130*540 + 180*630
             = 0 + 720 + 1260 + 2430 + 4320 + 24750 + 70200 + 113400 = 217080
             217080 / 407 = 533.37
             lat_c = 39.2600 + 0.053337 = 39.31334
```

Lo stesso sulla longitudine da' 16.43025 e 16.45815, e la distanza haversine fra i due
centroidi e' **3.415 km**. Con un punto di pescaggio indicativo nella Sila Grande a (39.30 N,
16.51 E), da sostituire con le coordinate vere degli invasi silani presenti nei dati statici,
la distanza focolaio-sorgente passa da 6.93 km (geometrico) a 4.70 km (pesato). Il ciclo
d'acqua di `SPEC.md` e' `d(fuoco, sorgente) * 2 / v_crociera * 60` piu' pescaggio e sgancio: a
200 km/h la parte di volo passa da 4.16 a 2.82 minuti, e su venti lanci sono **26.7 minuti
risparmiati**, contro un `endurance_minutes` che in `models.py` ha default 180. In senso
opposto il pesato costa poco: da Lamezia si passa da 45.89 a 49.03 km, cioe' 0.94 minuti in
piu' sull'andata, che si fa una volta sola.

Tre avvertenze. La media aritmetica di coordinate non e' rigorosa su una sfera (il modo
corretto media vettori unitari tridimensionali e rinormalizza): su pochi chilometri in
Calabria la differenza e' sotto il metro, ma non a cavallo dell'antimeridiano, dove la media
di 179 e -179 gradi da' zero invece di 180. Se la somma delle FRP e' zero il codice deve
ricadere esplicitamente sul geometrico invece di lasciar passare un NaN. E su un fronte
concavo il pesato puo' cadere dove non brucia nulla, perche' il centroide e' un riassunto e
non un punto di sgancio.

Questi attributi si propagano: `max_frp_mw` alimenta il coefficiente di intensita' dell'acqua
(`1 + min(1.0, max_frp_mw / 200)`) e il termine `0.35 * norm(frp)` della priorita' di base. Un
errore di clustering finisce nei litri, nella priorita' e da li' nell'obiettivo del solver.

---

## Cosa devi saper spiegare

**Perche' non k-means?**
Richiede `k` in anticipo, e il numero di incendi attivi e' proprio cio' che devo ricavare.
Assegnando al centroide piu' vicino partiziona in celle di Voronoi convesse, quindi taglia in
pezzi un fronte allungato: sul mio fronte sintetico lungo dieci chilometri,
`KMeans(n_clusters=2, n_init=10)` lo spacca in due segmenti contigui da quattro punti, e con
`k=3` in tre segmenti da 3, 2 e 3, mentre DBSCAN restituisce un gruppo solo. C'e' poi la
dipendenza dall'inizializzazione: con il `n_init` di default di scikit-learn 1.7, che e' una
sola esecuzione, lo stesso fronte con `k=2` e seme 0 finisce in un taglio 3-5 con inerzia
peggiore, cioe' in un minimo locale. Aggiungi le distanze al quadrato, l'assenza del concetto
di rumore, l'ipotesi di gruppi di dimensione simile, e la media non definita su latitudini e
longitudini.

**Punto centrale, punto di frontiera, rumore, e density-reachability contro density-connectivity?**
Centrale se nell'intorno di raggio `eps` ci sono almeno `minPts` punti, compreso se stesso; di
frontiera se non e' centrale ma sta nell'intorno di un centrale; rumore altrimenti. La
raggiungibilita' per densita' e' una catena in cui ogni anello sta nell'intorno di un punto
centrale precedente, ed e' asimmetrica perche' un punto di frontiera non propaga. La
connessione per densita' dice che due punti sono entrambi raggiungibili da uno stesso terzo
punto, e' simmetrica e definisce il gruppo.

**DBSCAN e' deterministico?**
Nel caso generale no, ma solo per i punti di frontiera: uno che ricade nell'intorno di due
centrali di gruppi diversi finisce nel gruppo che lo raggiunge per primo. I punti centrali
sono deterministici. In Tagliafuoco la questione non si pone, perche' con `minPts = 1` tutti i
punti sono centrali e il risultato dipende dai soli dati.

**Perche' `minPts` vale 1, e non e' un errore?**
Perche' l'intorno contiene il punto stesso, quindi ogni punto e' centrale e DBSCAN degenera in
clustering a soglia di distanza, equivalente al gerarchico con legame singolo tagliato a `eps`
(verificato: la partizione coincide). E' voluto perche' qui un falso negativo e' molto piu'
grave di un falso positivo: un rilevamento isolato e' spesso l'incendio appena nato, e
scartarlo come rumore lo cancella dallo `Scenario`. Il prezzo e' l'effetto catena.

**Come hai scelto `eps = 1.5 km`?**
Dalla geometria del dato: quattro volte il lato del pixel VIIRS al nadir, che al bordo della
striscia diventano circa due lati perche' il pixel cresce. Il margine c'e' ma non e' grande, e
lo dico invece di venderlo come ampio. Il metodo canonico sarebbe il
grafico della distanza al k-esimo vicino, leggendo il ginocchio della curva ordinata in modo
decrescente; con `minPts = 1` degenera e va letto sul secondo o terzo vicino. E' una taratura
dichiarata, non misurata. La difesa pratica e' la stabilita': sul mio caso di prova la
partizione non cambia per `eps` fra 1.0 e 4.48 km.

**Finestre temporali o ST-DBSCAN?**
Le finestre sono deterministiche e hanno un solo parametro, ma soffrono l'effetto di bordo.
ST-DBSCAN applica due soglie simultanee e il bordo sparisce, ma devi tarare due parametri e
implementarlo. Il limite vero e' comune: nessuno dei due modella il fatto che l'incendio si
muove, e servirebbe un cono spaziotemporale in cui la soglia spaziale cresce con la differenza
di tempo. A 3 metri al minuto, in dodici ore un fronte avanza circa 2.2 km, gia' piu' di
`eps`.

**Perche' il centroide pesato sulla FRP, e i due errori tipici con `metric="haversine"`?**
Il fronte e' asimmetrico: il geometrico cade dove il fuoco e' spesso gia' passato, il pesato
vicino alla testa. Sul mio fronte di prova distano 3.415 km, e il pesato e' 2.23 km piu'
vicino al punto di pescaggio, che su venti lanci a 200 km/h vale 26.7 minuti di volo. Sulla
metrica, gli errori sono passare i gradi invece dei radianti e passare `eps` in chilometri
invece di dividerlo per il raggio terrestre: il valore corretto e' `1.5 / 6371 =
2.354418e-04`. Nessuno dei due solleva una eccezione, e sul mio caso il primo produce sette
focolai singoli mentre il secondo ne produce uno solo.

---

## Per approfondire

Il lavoro originale su DBSCAN e' di Ester, Kriegel, Sander e Xu, presentato a meta' anni
novanta a una conferenza sul data mining: e' corto, e le definizioni di raggiungibilita' e
connessione per densita' nella forma originale sono piu' chiare di quasi tutte le riscritture.
Parte di quegli autori, insieme ad altri e a molti anni di distanza, e' tornata sull'argomento
con un articolo di riesame che affronta i fraintendimenti piu' comuni, incluso il rapporto fra
DBSCAN e legame singolo, il ruolo esatto di `minPts` e la questione della complessita' che il
paragrafo 6 riassume a parole. Cercalo per titolo, contiene "DBSCAN" e "revisited". Per OPTICS
cerca il lavoro di Ankerst, Breunig, Kriegel e Sander; per HDBSCAN quello di Campello, Moulavi
e Sander, con la pagina di scikit-learn su `sklearn.cluster.HDBSCAN` che spiega visivamente la
selezione per stabilita'; per ST-DBSCAN il lavoro di Birant e Kut.

Sulla parte satellitare, la documentazione di NASA FIRMS descrive per ciascun prodotto il
significato delle colonne, e la guida al prodotto VIIRS a 375 metri spiega lo schema di
aggregazione dei pixel e i limiti di rivelazione. Sulla FRP, cerca la letteratura sul metodo
della radianza nell'infrarosso medio associato al lavoro di Wooster e colleghi.

La pagina di scikit-learn sul clustering ha una tavola comparativa di una decina di algoritmi
su forme diverse, lune e anelli concentrici inclusi: cinque minuti li' chiariscono perche'
k-means fallisce sulle forme allungate. Nel repository: `SPEC.md` al paragrafo sul modello
fisico, `domain/models.py` per `Detection` e `FireCluster`, `config.py` per le tre costanti di
clustering. Il capitolo 12 riprende la metrica haversine.
