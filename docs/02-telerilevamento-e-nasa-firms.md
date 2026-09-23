# Capitolo 02. Telerilevamento e NASA FIRMS

Questo capitolo spiega come un satellite arriva a dire "qui c'e' un fuoco", quanto vale davvero
quell'affermazione, e come Tagliafuoco trasforma righe di un CSV in focolai da pianificare.

Indice dei paragrafi:

1. Il problema concreto
2. Corpo nero, legge di Planck, legge di Wien
3. Finestre atmosferiche: perche' 4 e 11 micrometri
4. Rilevamento per contrasto con lo sfondo
5. Il pixel misto: 375 metri accesi da un fronte di due metri
6. La Fire Radiative Power
7. MODIS e VIIRS a confronto, e cosa cambia per il clustering
8. Il campo confidence e la sua normalizzazione
9. Falsi positivi e falsi negativi
10. Tempo di rivisita e latenza: perche' non e' un sistema di allarme
11. La API di FIRMS e come il progetto la usa
12. Cosa non si puo' sapere da FIRMS
13. Cosa devi saper spiegare
14. Per approfondire

---

## 1. Il problema concreto

Tagliafuoco deve sapere dove stanno i fuochi. In Calabria non esiste una rete di sensori a terra che
copra Sila, Pollino e Aspromonte, e le telefonate al 112 non arrivano in un formato che un solver
possa leggere. Resta il satellite, che passa, misura la radianza in alcune bande dell'infrarosso e
pubblica un elenco di punti caldi.

NASA FIRMS (Fire Information for Resource Management System) e' il servizio che distribuisce quei
punti: gratuito, mondiale, in CSV, con una serie storica di oltre vent'anni. Ma va capito cosa
consegna davvero, perche' il docstring di `Detection` in `src/tagliafuoco/domain/models.py` dice una
cosa da prendere alla lettera: "un rilevamento NON e' un incendio".

## 2. Corpo nero, legge di Planck, legge di Wien

Ogni corpo a temperatura maggiore dello zero assoluto emette radiazione elettromagnetica. Un corpo
nero e' l'emettitore ideale, e la fisica reale si descrive come corpo nero moltiplicato per
un'emissivita' fra 0 e 1 (vegetazione e suolo stanno intorno a 0,95 nell'infrarosso termico, quindi
l'approssimazione e' buona). La legge di Planck da' quanta energia esce per unita' di lunghezza
d'onda:

```
B(lambda, T) = (2 * h * c^2 / lambda^5) * 1 / (exp(h * c / (lambda * k * T)) - 1)
```

Simbolo per simbolo: `B` e' la radianza spettrale, cioe' la potenza emessa per unita' di area, di
angolo solido e di lunghezza d'onda; `lambda` e' la lunghezza d'onda; `T` e' la temperatura assoluta
in kelvin; `h` e' la costante di Planck, 6,626 per 10 alla meno 34 joule moltiplicati per secondi
(l'unita' e' J·s, un joule moltiplicato per un secondo, non J/s che sarebbe una potenza: e' un
errore di lettura comune); `c` e' la velocita' della luce, 2,998 per 10 all'ottava metri al secondo;
`k` e' la costante di Boltzmann, 1,381 per 10 alla meno 23 joule per kelvin.

Un'avvertenza sulle unita', perche' e' il punto in cui si sbaglia rifacendo i conti. Se metti
`lambda` in metri, la formula restituisce watt per metro quadrato per steradiante per metro di
lunghezza d'onda. Il telerilevamento pero' preferisce contare per micrometro, quindi quel risultato
va diviso per un milione. Tutti i valori di radianza di questo capitolo sono in watt per metro
quadrato per steradiante per micrometro.

La legge dello spostamento di Wien dice dove sta il massimo di quella curva:

```
lambda_max = b / T          con b = 2898 micrometri * kelvin
```

dove `lambda_max` e' la lunghezza d'onda del massimo in micrometri, `T` la temperatura in kelvin e
`b` la costante di spostamento di Wien. Sostituendo le temperature che ci interessano:

```
T =  300 K (superficie terrestre)   ->  lambda_max = 9,66 micrometri
T =  600 K (brace, covata)          ->  lambda_max = 4,83 micrometri
T =  800 K (fronte poco intenso)    ->  lambda_max = 3,62 micrometri
T = 1000 K (fiamma viva)            ->  lambda_max = 2,90 micrometri
```

Ecco la ragione fisica di tutta l'architettura di rilevamento: un fuoco fra 800 e 1000 kelvin ha il
massimo di emissione nell'infrarosso medio, intorno ai 3 o 4 micrometri, mentre la superficie
terrestre a circa 300 kelvin ha il massimo nell'infrarosso termico, intorno ai 10. Le due
popolazioni si separano nello spettro, e quanto si separino lo si vede mettendo i numeri nella
formula di Planck (in watt per metro quadrato per steradiante per micrometro):

```
                    300 K (sfondo)   1000 K (fiamma)   rapporto
a  4 micrometri          0,722           3.277,66        4.540
a 11 micrometri          9,573             274,04           28,6
```

Il rapporto fuoco/sfondo e' circa 4.540 a 4 micrometri e circa 28,6 a 11: l'infrarosso medio
amplifica il contrasto circa 159 volte piu' del termico. Con una fiamma piu' fredda, a 800 kelvin, i
due rapporti diventano 1.817 e 18,7, quindi il guadagno scende a circa 97 volte. Il vantaggio del
canale a 4 micrometri vale insomma fra le cento e le centosessanta volte secondo quanto e' calda la
fiamma, ed e' sempre grande abbastanza da decidere l'architettura: il canale a 4 micrometri vede il
fuoco, quello a 11 misura lo sfondo, le nuvole e la superficie.

Per completezza, la potenza totale emessa su tutte le lunghezze d'onda segue la legge di
Stefan-Boltzmann, `M = sigma * T^4`, dove `M` e' l'emittanza in watt per metro quadrato, `sigma` e'
la costante di Stefan-Boltzmann e vale 5,67 per 10 alla meno 8 watt per metro quadrato per kelvin
alla quarta, e `T` e' in kelvin: 459 watt per metro quadrato a 300 kelvin, 56.704 a 1000, un fattore
123. Nota che su tutto lo spettro il fuoco supera lo sfondo di 123 volte, mentre a 4 micrometri lo
supera di 4.540: e' la banda a fare il lavoro, non l'energia totale.

## 3. Finestre atmosferiche: perche' 4 e 11 micrometri

L'atmosfera non e' trasparente. Il vapore acqueo, l'anidride carbonica, il metano e l'ozono
assorbono l'infrarosso a bande. Le finestre atmosferiche sono gli intervalli in cui l'assorbimento
e' basso e un satellite riesce a vedere il suolo: una sta fra circa 3 e 5 micrometri, l'altra fra
circa 8 e 13.

Le bande operative si scelgono dentro quelle finestre, evitandone i guastafeste. A 4,3 micrometri
c'e' una forte banda di assorbimento dell'anidride carbonica, quindi i canali per il fuoco si
piazzano appena sotto, intorno ai 3,7 o 4,0 micrometri. Intorno a 9,6 micrometri c'e' l'ozono,
quindi il canale termico si piazza a 10,5 o 11,5 micrometri.

Nei dati che il progetto legge queste due bande si vedono in chiaro. Per VIIRS le colonne del CSV
sono `bright_ti4`, la temperatura di brillanza del canale I4 intorno ai 3,74 micrometri, e
`bright_ti5`, quella del canale I5 intorno agli 11,45; per MODIS sono `brightness`, a circa 3,96
micrometri, e `bright_t31`, a circa 11. Temperatura di brillanza significa la temperatura che
avrebbe un corpo nero capace di emettere quella radianza a quella lunghezza d'onda: non e' la
temperatura del fuoco, ed e' l'equivoco piu' comune. Il modello di dominio ne conserva una sola,
`Detection.brightness_k`, perche' il progetto non implementa un proprio algoritmo di rilevamento e
le serve come informazione diagnostica, non come ingrediente di calcolo.

## 4. Rilevamento per contrasto con lo sfondo

Un algoritmo che dicesse "e' fuoco se la temperatura di brillanza a 4 micrometri supera 320 kelvin"
fallirebbe due volte. Segnalerebbe mezzo Sahara a mezzogiorno, dove il suolo nudo arriva a quei
valori senza nessuna fiamma. E mancherebbe un fuoco notturno acceso su uno sfondo a 285 kelvin, che
a 315 kelvin di brillanza e' evidentissimo eppure resterebbe sotto soglia.

L'idea operativa e' quindi un'altra: un pixel e' caldo rispetto ai suoi vicini, non in assoluto.
L'algoritmo prende un pixel candidato, costruisce attorno a esso una finestra di pixel di sfondo
(escludendo nuvole, acqua e altri candidati), calcola media e deviazione media assoluta della
temperatura a 4 micrometri e della differenza fra i due canali, e dichiara fuoco quando il candidato
supera lo sfondo di un multiplo di quella deviazione. Le soglie assolute restano, ma come filtro
grossolano di ingresso, non come criterio.

Tre conseguenze vanno capite bene. La prima: la sensibilita' dipende dallo sfondo, quindi lo stesso
fronte e' piu' facile da rilevare di notte e su un versante ombreggiato che a mezzogiorno su un
pascolo bruciato dal sole, ed e' per questo che il campo `daynight` del CSV, conservato in
`Detection.daynight`, non e' decorativo. La seconda: la sensibilita' dipende dalla posizione nella
striscia, perche' al bordo del campo di vista il pixel e' molto piu' grande che al nadir, il fuoco
ne occupa una frazione minore e il contrasto si diluisce; per questo il CSV porta `scan` e `track`,
le dimensioni effettive del pixel, che in `Detection` diventano `scan_km` e `track_km` e alimentano
`pixel_area_km2`. La terza: non si rileva "un incendio", si rileva "un pixel anomalo", e il
passaggio dai pixel ai focolai e' un problema diverso, che nella pipeline risolve il clustering.

## 5. Il pixel misto: 375 metri accesi da un fronte di due metri

Questa e' la conseguenza pratica piu' importante del capitolo.

Un pixel VIIRS al nadir misura 375 metri di lato, cioe' 140.625 metri quadrati, cioe' 14,06 ettari.
Un fronte di fiamma reale e' lungo qualche decina o centinaio di metri ma profondo pochi metri: la
zona che sta davvero bruciando a 1000 kelvin e' una striscia sottile.

Mettiamo i numeri. Fronte lungo 50 metri e profondo 2, cioe' 100 metri quadrati, che sono lo 0,071
per cento del pixel. La radianza che il sensore misura e' la media pesata fra la parte in fiamma e
la parte di sfondo:

```
L = p * B(lambda, T_fuoco) + (1 - p) * B(lambda, T_sfondo)
```

dove `p` e' la frazione di pixel occupata dal fuoco, `T_fuoco` vale 1000 kelvin e `T_sfondo` 300.
Invertendo la formula di Planck per ottenere la temperatura di brillanza si ha:

```
a  4 micrometri:  BT4  = 341,0 K   (sfondo 300 K,  salto di 41,0 K)
a 11 micrometri:  BT11 = 301,3 K   (sfondo 300 K,  salto di  1,3 K)
```

Leggilo con calma. Lo 0,071 per cento della superficie del pixel sposta il canale a 4 micrometri di
41 kelvin e quello a 11 micrometri di poco piu' di un kelvin. Con lo stesso fronte a 800 kelvin il
salto a 4 micrometri scende a 22 kelvin, ancora ampiamente rilevabile.

Da qui discendono due affermazioni vere e una falsa. Vera: VIIRS vede fuochi molto piu' piccoli del
suo pixel. Vera: il canale a 4 micrometri e' necessario, perche' quello a 11 non distinguerebbe un
salto di un kelvin dal rumore e dalla variabilita' naturale del suolo. Falsa, e diffusissima: un
pixel acceso significa che quei 14 ettari stanno bruciando.

Il progetto convive con questa ambiguita' in modo dichiarato. `Detection.pixel_area_km2` restituisce
`scan_km * track_km`, l'area nominale del pixel, e il docstring dice a cosa serve: "stimare l'area
percorsa". E' una stima per eccesso della zona in fiamma e una stima molto incerta dell'area
percorsa, perche' la cicatrice dietro al fronte puo' essere piu' grande del pixel mentre la fiamma
e' sempre molto piu' piccola. Quando `FireCluster.area_km2` finisce nella formula del fabbisogno
d'acqua (capitolo 01, paragrafo 5), quell'incertezza si propaga per intero sui litri richiesti.

## 6. La Fire Radiative Power

La Fire Radiative Power, di solito abbreviata FRP, e' la potenza radiante emessa dal fuoco
nell'infrarosso, misurata in megawatt e riferita al pixel. Non e' una temperatura e non e' un'area:
e' una potenza, cioe' energia al secondo. Fisicamente e' la frazione dell'energia di combustione che
se ne va come radiazione, dell'ordine del dieci o venti per cento del calore totale rilasciato, con
un valore che dipende dal combustibile e dalla geometria della fiamma e che non riporto come numero
fermo.

L'interesse pratico e' che la FRP risulta approssimativamente proporzionale al tasso di consumo di
biomassa. La conversione empirica piu' usata sta intorno a 0,368 chilogrammi di biomassa secca al
secondo per megawatt, ricavata da esperimenti di combustione controllata e da riportare con la sua
incertezza: a 120 megawatt fanno 44,2 chilogrammi al secondo, cioe' 159 tonnellate all'ora.

Il metodo di stima sfrutta una proprieta' della curva di Planck a 4 micrometri, ed e' qui che si
sbaglia piu' spesso a raccontarla. Prendi la radianza a 4 micrometri e chiediti di quanto cresce se
la temperatura cresce dell'uno per cento: il rapporto fra le due crescite percentuali e' l'esponente
locale della legge di potenza, e si calcola come `x * e^x / (e^x - 1)` con `x = h*c/(lambda*k*T)`.
Quell'esponente non e' costante, dipende dalla temperatura, e i suoi valori a 4 micrometri sono
questi:

```
T = 320 K  ->  esponente 11,2      T =  600 K  ->  esponente 6,0
T = 400 K  ->  esponente  9,0      T =  800 K  ->  esponente 4,6
T = 500 K  ->  esponente  7,2      T = 1000 K  ->  esponente 3,7
```

L'esponente vicino a 8 non vale quindi alla temperatura della fiamma, dove vale poco meno di 4: vale
nell'intervallo delle temperature di brillanza che un pixel di fuoco mostra davvero, cioe' fra i 320
e i 500 kelvin circa, perche' la fiamma occupa solo una frazione minuscola del pixel e la brillanza
media resta molto piu' bassa della fiamma. Su quell'intervallo la radianza cresce grosso modo come
la potenza ottava o nona della temperatura di brillanza, ed e' esattamente la variabile su cui la
formula e' scritta. Da qui la forma usata nei prodotti MODIS:

```
FRP = 4,34e-19 * (T4^8 - T4b^8) * A_pixel
```

dove `FRP` e' in megawatt, `T4` e' la temperatura di brillanza del pixel di fuoco a 4 micrometri in
kelvin, `T4b` quella dello sfondo stimata attorno al pixel, `A_pixel` l'area del pixel in chilometri
quadrati, e la costante ha unita' di megawatt per kelvin alla meno ottava per chilometro quadrato
(la riporto dalla documentazione dell'algoritmo e va riverificata li').

La struttura della formula dice due cose. Primo, la FRP misura l'eccesso rispetto allo sfondo: se la
stima dello sfondo e' sbagliata, la FRP e' sbagliata. Secondo, la FRP scala con l'area del pixel,
il che porta a un risultato scomodo: con `T4 = 400 K` e `T4b = 300 K` un pixel MODIS da 1 chilometro
quadrato da' 256 megawatt e un pixel VIIRS da 0,1406 chilometri quadrati ne da' 36, a parita' esatta
di temperature misurate. Non e' un errore, perche' un pixel
grande a quella temperatura media contiene davvero piu' fuoco, ma confrontare le FRP di MODIS e
VIIRS sullo stesso incendio non e' immediato, e il progetto usa entrambe le sorgenti.

La FRP e' l'unica misura di intensita' disponibile, e in Tagliafuoco compare in due posti: nella
priorita' di base pesa 0,35, il piu' alto dei cinque, come `norm(frp) = min(1, frp / 200)`, e nel
fabbisogno d'acqua entra come `coeff_intensita = 1 + min(1, max_frp_mw / 200)`. La costante
`FRP_NORMALISATION_MW = 200.0` e' una taratura dichiarata, e la saturazione ha un effetto che va
detto:

```
FRP =  50 MW  ->  norm = 0,250  ->  contributo alla priorita' 0,0875  ->  coeff_intensita 1,25
FRP = 200 MW  ->  norm = 1,000  ->  contributo alla priorita' 0,3500  ->  coeff_intensita 2,00
FRP = 900 MW  ->  norm = 1,000  ->  contributo alla priorita' 0,3500  ->  coeff_intensita 2,00
```

Un incendio da 900 megawatt e uno da 200 ricevono lo stesso contributo di priorita' e lo stesso
coefficiente di intensita'. La scelta si difende, perche' sopra una certa intensita' il fronte non
si tiene comunque e mandare il doppio dei mezzi non cambia l'esito, ma resta una scelta e un
esaminatore ha ragione a chiedertene conto.

Un ultimo dettaglio. `FireCluster` porta due grandezze, `total_frp_mw` e `max_frp_mw`. Per il
fabbisogno d'acqua SPEC.md scrive esplicitamente il massimo; per la priorita' di base scrive solo
`norm(frp)` e lascia aperta la scelta, e il capitolo 01, paragrafo 6, spiega perche' le due letture
siano entrambe difendibili. Il massimo ha comunque un argomento forte a favore: con
`CLUSTER_EPS_KM = 1.5` un rilevamento MODIS e uno VIIRS dello stesso fronte finiscono nello stesso
focolaio, e sommare le loro FRP conterebbe due volte la stessa fiamma vista da due sensori, per di
piu' con due aree di pixel diverse. In compenso il massimo butta via informazione su un fronte lungo
che accende molti pixel, dove la somma direbbe qualcosa di vero sull'estensione.

## 7. MODIS e VIIRS a confronto, e cosa cambia per il clustering

MODIS vola su Terra (dal 1999, passaggio diurno verso le 10:30 locali) e su Aqua (dal 2002, verso le
13:30). Il pixel del prodotto incendi e' nominalmente di 1 chilometro al nadir ma cresce molto verso
il bordo della striscia, larga circa 2.330 chilometri. Il suo pregio e' la serie storica: oltre
vent'anni con lo stesso algoritmo, che e' cio' che rende possibili i confronti pluriennali.

VIIRS vola su Suomi-NPP (dal 2011) e su NOAA-20 e NOAA-21. Il prodotto incendi a 375 metri usa le
bande I, con uno schema di aggregazione lungo la scansione che limita la crescita del pixel verso il
bordo, e la striscia e' piu' larga, circa 3.060 chilometri. Il vantaggio e' la sensibilita': con un
pixel di area circa sette volte minore, la frazione occupata da un dato fronte e' sette volte
maggiore, quindi il contrasto e' molto piu' forte e VIIRS rileva fuochi piu' piccoli e piu' freddi.

Per il clustering la differenza conta. `CLUSTER_EPS_KM = 1.5` e' circa quattro pixel VIIRS ma solo
un pixel e mezzo di MODIS, e il commento in `config.py` lo dice: abbastanza per unire i pixel dello
stesso fronte, abbastanza poco da tenere separati due incendi distinti sulla stessa collina. Il
compromesso e' reale in entrambe le direzioni. Un rilevamento MODIS il cui centro e' spostato di
settecento metri rispetto al fronte vero continua ad aggregarsi correttamente; ma due incendi
genuinamente distinti a un chilometro l'uno dall'altro vengono fusi in un focolaio solo, e il
pianificatore li trattera' come un obiettivo unico.

Due altre scelte meritano una riga. `CLUSTER_MIN_SAMPLES = 1` significa che DBSCAN non produce
rumore: ogni rilevamento isolato diventa un focolaio a se'. In un contesto generico sarebbe un
errore, perche' si perde la capacita' di scartare i punti spuri; qui e' deliberato, perche' scartare
un pixel caldo isolato significherebbe decidere in silenzio di non mandare nessuno. E
`CLUSTER_TIME_WINDOW_HOURS = 12.0` impedisce che due passaggi a molte ore di distanza sullo stesso
punto vengano confusi per lo stesso evento continuo.

## 8. Il campo confidence e la sua normalizzazione

MODIS pubblica una confidenza numerica da 0 a 100, calcolata combinando l'entita' del contrasto, il
numero di pixel vicini di nuvola o acqua e la condizione giorno o notte. VIIRS a 375 metri pubblica
invece una classe: `l` per low, `n` per nominal, `h` per high, dove `n` e' il caso ordinario, `h`
indica un contrasto molto forte e `l` raccoglie casi sospetti, tipicamente geometrie favorevoli al
riflesso solare o rilevamenti ai margini della scansione.

Due scale diverse, e FIRMS mescola i due prodotti nello stesso flusso. Il progetto le normalizza in
`config.py`:

```python
MIN_CONFIDENCE = 30.0
CONFIDENCE_MAP = {"l": 30.0, "n": 60.0, "h": 90.0}
```

La ragione e' pratica: `Detection.confidence` e' un `float` unico e `FireCluster.mean_confidence` ne
fa una media su rilevamenti che possono venire da sensori diversi, quindi senza una scala comune
quella media non significherebbe niente.

Tre avvertenze oneste. Il valore 30 assegnato a `l` coincide esattamente con `MIN_CONFIDENCE`,
quindi il filtro conserva i rilevamenti low se il confronto e' `>=` e li elimina tutti se e' `>`: e'
un caso limite che chi implementa `ingest/` deve scegliere consapevolmente e coprire con un test. La
corrispondenza 30, 60, 90 e' una convenzione del progetto e non un'equivalenza documentata fra le
due scale, perche' quell'equivalenza non esiste. E la confidenza non e' una probabilita' che li' ci
sia un incendio: e' la documentazione di FIRMS a mettere in guardia contro l'uso della confidenza
come filtro cieco, e con `MIN_CONFIDENCE` a 30 il progetto di fatto non scarta quasi nulla, che per
un supporto alle decisioni e' la scelta prudente.

## 9. Falsi positivi e falsi negativi

I falsi positivi nascono tutti dalla stessa causa: qualcosa di caldo che non e' un incendio
boschivo, oppure un riflesso che sembra caldo. Le famiglie ricorrenti sono le torce di combustione
di raffinerie e impianti del gas, che bruciano stabilmente e compaiono nello stesso punto ogni
giorno; i forni industriali come fornaci, cementifici e acciaierie; i vulcani attivi; il riflesso
speculare del sole sull'acqua e su superfici metalliche o vetrate, che alza la radianza
nell'infrarosso medio senza nessun aumento di temperatura; e il suolo nudo molto caldo.

Per la Calabria vale la pena fare un conto concreto sulla bounding box del progetto:

```python
CALABRIA_BBOX = (15.55, 37.85, 17.25, 40.20)   # lon_min, lat_min, lon_max, lat_max
```

L'Etna, a 37,75 nord e 14,99 est, e' fuori sia per latitudine sia per longitudine. Stromboli, a
38,79 nord e 15,21 est, e' fuori per longitudine. E' una fortuna, non un progetto. Ma la stessa
verifica mostra che il riquadro include la punta nord-orientale della Sicilia intorno a Messina
(38,19 nord, 15,55 est, dentro per un soffio) e una porzione della Basilicata meridionale. Ne segue
un requisito preciso per `ingest/`: filtrare per confine amministrativo dopo aver scaricato per
riquadro, altrimenti nello scenario calabrese finiscono incendi che la Calabria non deve spegnere.

I falsi negativi hanno cause piu' varie. Una copertura nuvolosa spessa e' opaca nell'infrarosso e in
una giornata coperta il satellite non vede il suolo, mentre un velo di cirri sottili attenua e
basta; in entrambi i casi l'algoritmo maschera il pixel come nuvola e li' non cerca fuoco. Il fumo
denso attenua il segnale, meno del visibile ma non in modo trascurabile. Un fuoco sotto chioma
chiusa e' schermato dalla vegetazione, caso frequente nelle pinete dense di cui parla il capitolo
01. Un fuoco piccolo o in covatura
puo' stare sotto il limite di rilevabilita'. Un fuoco che si accende dopo un passaggio e si spegne
prima del successivo, per il satellite, non esiste. E infine i canali a 4 micrometri saturano: sui
fuochi piu' intensi l'algoritmo deve ripiegare su un canale meno sensibile, il che porta a
sottostimare la FRP proprio sugli incendi piu' gravi. I valori esatti di saturazione stanno nella
documentazione degli strumenti e non li riporto a memoria.

## 10. Tempo di rivisita e latenza: perche' non e' un sistema di allarme

I satelliti che portano MODIS e VIIRS stanno in orbita polare eliosincrona intorno ai 700 chilometri
di quota, con un periodo di poco piu' di 100 minuti e quindi circa 14 orbite al giorno. Alla
latitudine della Calabria ogni satellite offre grosso modo un paio di osservazioni diurne e un paio
notturne al giorno, e mettendo insieme Terra, Aqua, Suomi-NPP e NOAA-20 si arriva nell'ordine di sei
o otto occasioni quotidiane, non distribuite in modo uniforme ma addensate intorno agli orari di
passaggio delle orbite. Do questi conteggi come ordine di grandezza.

Alla rivisita si somma la latenza di elaborazione. I prodotti near real time, che FIRMS etichetta
`NRT`, sono disponibili circa tre ore dopo l'osservazione. Per Stati Uniti e Canada esiste una
catena a ricezione diretta con latenza di circa un minuto, ma non copre l'Italia. I prodotti
standard di archivio, con suffisso `SP` in `FIRMS_SOURCES_ARCHIVE`, arrivano mesi dopo con
geolocalizzazione e calibrazione definitive: sono la sorgente giusta per rigiocare una stagione
passata, non per lavorare sull'oggi.

Adesso il conto che chiude il discorso. Il capitolo 01 ha mostrato che un fronte a 30 metri al
minuto copre 6,36 ettari in 15 minuti e 25,45 in 30. Portiamo lo stesso modello ellittico a 180
minuti, la latenza dei prodotti NRT: l'asse maggiore arriva a 5.400 metri, il minore a 2.160, l'area
a 9,16 chilometri quadrati, cioe' 916 ettari, e il fabbisogno d'acqua con pineta e intensita'
saturata a circa 2,56 milioni di litri. Il capitolo 01 ha calcolato che un Canadair da Lamezia che
pesca in mare consegna al massimo 42.000 litri per missione: ne servirebbe una sessantina. Anche
pescando al Lago di Cecita, a pochi chilometri dal fuoco, dove la stessa missione consegna 180.000
litri, ne servirebbero quattordici. Il conteggio esatto dipende dai parametri del mezzo, l'ordine di
grandezza no: si passa da un lavoro per un mezzo a un lavoro per una flotta intera per un giorno.

Nell'istante in cui il dato FIRMS diventa disponibile, un incendio nato in quel momento non e' piu'
un problema di primo attacco: e' gia' attacco esteso, o e' gia' finito. Tagliafuoco quindi non puo'
essere un sistema di allarme e non pretende di esserlo. E' un supporto alla pianificazione:
distribuisce mezzi fra focolai gia' noti su un orizzonte di quattro ore e rigioca stagioni passate
con finestre di tre ore (`REPLAY_WINDOW_HOURS = 3`, che non a caso coincide con la latenza).
L'allarme vero, in Calabria come altrove, resta la telefonata.

## 11. La API di FIRMS e come il progetto la usa

La chiave si chiede gratuitamente sul sito di FIRMS, arriva per posta elettronica ed e' una stringa
esadecimale. Nel progetto sta in `FIRMS_MAP_KEY`, letta dall'ambiente in `config.py`, e il file
`.env.example` riporta l'indirizzo dove richiederla.

L'endpoint per area restituisce CSV e ha questa forma:

```
https://firms.modaps.eosdis.nasa.gov/api/area/csv/MAP_KEY/SOURCE/AREA/DAY_RANGE/DATE
```

`SOURCE` e' il prodotto, per esempio `VIIRS_SNPP_NRT` o `MODIS_SP`. `AREA` e' il riquadro nel
formato `ovest,sud,est,nord`, che e' esattamente l'ordine di `CALABRIA_BBOX`. `DAY_RANGE` e' il
numero di giorni, con un massimo di dieci per richiesta. `DATE` e' la data iniziale in formato
`AAAA-MM-GG` e si puo' omettere per ottenere i dati piu' recenti. Costruirlo e' banale:

```python
url = f"{FIRMS_BASE_URL}/{FIRMS_MAP_KEY}/{source}/{','.join(map(str, CALABRIA_BBOX))}/{days}/{date}"
```

Il CSV di VIIRS ha le colonne `latitude`, `longitude`, `bright_ti4`, `scan`, `track`, `acq_date`,
`acq_time`, `satellite`, `instrument`, `confidence`, `version`, `bright_ti5`, `frp`, `daynight`.
Quello di MODIS sostituisce `bright_ti4` con `brightness` e `bright_ti5` con `bright_t31`, e la
`confidence` e' numerica invece che categorica. La corrispondenza con `Detection` e' diretta:
`latitude` e `longitude` diventano un `GeoPoint`, `acq_date` piu' `acq_time` diventano
`acquired_at`, `frp` diventa `frp_mw`, `scan` e `track` diventano `scan_km` e `track_km`, e
`confidence` passa per `CONFIDENCE_MAP` quando e' una lettera.

Sui limiti di richiesta: la chiave ha una quota di transazioni su una finestra mobile di pochi
minuti, e FIRMS espone un endpoint per consultare quante ne restano. Non riporto il numero esatto
perche' e' cambiato nel tempo e non l'ho verificato adesso.

La regola di progetto che governa tutto questo sta in SPEC.md e non e' negoziabile: Tagliafuoco deve
girare interamente senza chiavi. Concretamente, `ingest/firms_client.py` legge da `data/fixtures/`
quando `config.has_firms_key()` e' falso, e nessun test unitario puo' richiedere rete. Oltre alla
comodita' c'e' una ragione di ingegneria: rende i risultati riproducibili, perche' la rigiocata
della stagione 2025 descritta in SPEC.md deve dare lo stesso esito oggi e fra sei mesi, e non puo'
dipendere da cosa risponde un servizio esterno.

## 12. Cosa non si puo' sapere da FIRMS

Chiudo con la domanda onesta: quali informazioni servirebbero per pianificare bene e FIRMS non da'.

Il perimetro esatto. FIRMS consegna punti con un'area nominale di pixel, non un poligono. Dove stia
la testa e dove i fianchi, quale versante sia gia' percorso: niente di tutto questo e' nel dato. I
prodotti di area bruciata e le perimetrazioni europee esistono e sono buoni, ma arrivano giorni o
settimane dopo, quindi servono per l'analisi retrospettiva e non per decidere.

La direzione di propagazione. Con due passaggi successivi si puo' intuire uno spostamento, ma la
rivisita e' troppo rada e il posizionamento troppo grossolano per ricavarne un vettore affidabile, e
cio' che determina davvero la direzione, cioe' vento locale e pendenza, non e' nel CSV.

La presenza di persone. Un satellite non vede una squadra gia' sul posto, un'auto bloccata su una
sterrata, una casa abitata o vuota, un campeggio. Il progetto sostituisce questa informazione con
due proxy tratti dai dati statici, `distance_to_wui_km` e `population_within_3km`, che misurano
l'esposizione potenziale e non la presenza effettiva: sono cose diverse e vanno chiamate con nomi
diversi. C'e' poi cio' che sta in mezzo: cosa stia bruciando davvero, approssimato da `land_cover` a
partire da cartografia che puo' avere anni; se l'incendio sia gia' presidiato; se una strada di
accesso sia percorribile.

Questo elenco e' esattamente la ragione per cui nell'architettura di Tagliafuoco c'e' un
coordinatore che parla. Il vincolo `ForbidAssignment` dello schema NLU esiste per accogliere la
frase "i Canadair non su quell'incendio", e il suo docstring dice il motivo senza giri di parole: a
volte il coordinatore ha informazioni che il sistema non ha. Il modello linguistico non serve a
decidere, serve a trasportare dentro un `ConstraintSet` tipizzato quello che il satellite non puo'
vedere.

## Cosa devi saper spiegare

**Perche' si guarda a 4 micrometri e non a 11 per trovare i fuochi?**
Per Planck e Wien. Un fuoco a 1000 kelvin ha il massimo di emissione a 2,9 micrometri, la superficie
terrestre a 300 kelvin lo ha a 9,7. Il rapporto fra radianza del fuoco e dello sfondo vale circa
4.540 a 4 micrometri e circa 28,6 a 11: l'infrarosso medio amplifica il contrasto circa 159 volte
piu' del termico, poco oltre i due ordini di grandezza, e il guadagno scende a circa 97 volte se la
fiamma e' a 800 kelvin invece che a 1000. Il canale a 11 micrometri non e' inutile: serve a misurare
lo sfondo e a riconoscere le nuvole.

**Perche' l'algoritmo usa il contrasto con i vicini e non una soglia assoluta?**
Perche' una soglia assoluta segnalerebbe il suolo nudo caldo di mezzogiorno e mancherebbe un fuoco
notturno su uno sfondo freddo. L'algoritmo stima media e dispersione della temperatura a 4
micrometri su una finestra di pixel di sfondo e dichiara fuoco quando il candidato le supera di un
multiplo. La conseguenza e' che la sensibilita' dipende dall'ora del giorno e dalla posizione nella
striscia di scansione.

**Un pixel VIIRS acceso significa che bruciano 14 ettari?**
No, ed e' la confusione piu' comune. Un fronte di 50 metri per 2, cioe' lo 0,071 per cento del
pixel, a 1000 kelvin alza la temperatura di brillanza a 4 micrometri di 41 kelvin, abbastanza per
far scattare il rilevamento. Il pixel dice "qui c'e' qualcosa di caldo". Nel progetto
`pixel_area_km2` da' l'area nominale del pixel, che e' una stima per eccesso della zona in fiamma, e
quell'incertezza si propaga sul fabbisogno d'acqua.

**Che cos'e' fisicamente la FRP e perche' il progetto la usa?**
E' la potenza radiante emessa dal fuoco nell'infrarosso, in megawatt, riferita al pixel. Si stima
dall'eccesso di radianza a 4 micrometri rispetto allo sfondo. Attenzione al passaggio che quasi
tutti raccontano male: la potenza ottava vale sulla temperatura di brillanza del pixel, che per un
pixel di fuoco sta fra i 320 e i 500 kelvin circa, non sulla temperatura della fiamma, dove
l'esponente locale della curva di Planck a 4 micrometri scende sotto 4. La FRP interessa perche' e'
approssimativamente proporzionale al tasso di consumo di biomassa, intorno a 0,368 chilogrammi al
secondo per megawatt, ed e' l'unica misura di intensita' presente nel dato.

**Qual e' il limite della normalizzazione della FRP nel progetto?**
`FRP_NORMALISATION_MW = 200.0` satura: un incendio da 200 megawatt e uno da 900 ricevono contributo
di priorita' identico, 0,35, e coefficiente di intensita' identico, 2,0. E' una taratura dichiarata
e difendibile, ma resta una scelta. Va aggiunto che le FRP di MODIS e di VIIRS non sono direttamente
confrontabili, perche' la formula scala con l'area del pixel e il progetto mescola le due sorgenti.

**Perche' la latenza di tre ore cambia la natura del progetto?**
Perche' un fronte a 30 metri al minuto, nel modello ellittico, dopo 180 minuti copre 916 ettari e
richiederebbe circa 2,56 milioni di litri, cioe' una sessantina di missioni di Canadair con
pescaggio in mare, o quattordici con acqua vicina al fuoco. Quando il dato
NRT diventa disponibile la finestra del primo attacco e' chiusa da un pezzo, quindi Tagliafuoco non
e' un sistema di allarme: distribuisce mezzi fra focolai gia' noti e rigioca stagioni passate con
finestre di tre ore che coincidono con quella latenza.

**Che differenza c'e' fra la confidence di MODIS e quella di VIIRS?**
MODIS pubblica un numero da 0 a 100, VIIRS a 375 metri una classe fra `l`, `n` e `h`. Il progetto
mescola le due sorgenti e `Detection.confidence` e' un solo `float` mediato in `mean_confidence`,
quindi serve una scala comune e `CONFIDENCE_MAP` mappa su 30, 60 e 90. E' una convenzione del
progetto, non un'equivalenza documentata, e la confidenza non e' una probabilita' che li' ci sia un
incendio.

**Se FIRMS non da' il perimetro, come fa il solver a sapere quanto e' grande il focolaio?**
Non lo sa davvero. `FireCluster.area_km2` si ricava dall'estensione spaziale dei rilevamenti
aggregati e dalle aree nominali dei pixel, quindi e' una stima molto incerta che il progetto propaga
fino ai litri richiesti. E' anche il motivo per cui `WATER_LITERS_PER_KM2` e' dichiarata come
taratura e non come verita' fisica.

## Per approfondire

Per la parte fisica, un manuale universitario di telerilevamento copre Planck, Wien,
Stefan-Boltzmann, emissivita' e finestre atmosferiche meglio di quanto possa fare un paragrafo:
leggine il capitolo sulle basi fisiche prima di quello sui sensori.

Per gli algoritmi, i documenti tecnici di base degli algoritmi MODIS e VIIRS per il rilevamento
degli incendi attivi sono pubblici, scaricabili dal sito della NASA e sorprendentemente leggibili.
Sono la fonte giusta per le soglie esatte, per la procedura di stima dello sfondo, per la
definizione operativa della confidenza e per la costante della formula della FRP del paragrafo 6.
Sul legame fra potenza radiante e consumo di biomassa, cerca la letteratura che lo ricava da
esperimenti di combustione controllata: e' li' che nasce il coefficiente intorno a 0,368 chilogrammi
al secondo per megawatt, e vedere come e' stato ricavato aiuta a capire quanta fiducia riporvi.

Per la parte pratica, la documentazione della API di FIRMS descrive endpoint, sorgenti, formati,
limiti e differenze fra prodotti NRT e standard, ed e' l'unico posto dove i numeri sono aggiornati.
Se vuoi confrontare le tue aggregazioni con perimetri veri, il sistema europeo di informazione sugli
incendi forestali ne pubblica di mappati, sovrapponibili ai focolai ricostruiti dal clustering: e'
l'esercizio migliore per capire quanto un pixel acceso somigli a un incendio.
