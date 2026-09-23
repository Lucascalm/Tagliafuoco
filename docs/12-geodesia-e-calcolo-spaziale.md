# Geodesia e calcolo spaziale: distanze sulla Terra senza sbagliare

Da dove vengono i chilometri che Tagliafuoco trasforma in minuti di volo: la forma della
Terra, la formula dell'haversine e la sua derivazione, quando serve l'ellissoide, da dove
viene il fattore di tortuosita' 1.45, cosa deforma la mappa su cui la demo disegna, e come si
cercano punti in fretta quando diventano tanti.

## Indice

1. Il problema: ogni minuto del piano nasce da una distanza
2. La forma della Terra: geoide, ellissoide, sfera
3. WGS84 ed EPSG:4326, e la trappola dell'ordine degli assi
4. Latitudine e longitudine non sono un piano cartesiano
5. La legge dei coseni sferica e perche' non basta
6. L'haversine: nome, derivazione, forma numerica
7. Perche' atan2 e non asin
8. Vincenty e l'ellissoide: quando lo 0.5 per cento conta
9. Un esempio calabrese, e da dove viene il fattore 1.45
10. Proiezioni: cosa conservano e cosa rompono
11. Web Mercator, EPSG:3857, e perche' non ci si misurano le aree
12. Bounding box e contenimento punto in poligono
13. Indici spaziali: R-tree, quadtree, geohash, celle S2
14. Il sistema di tile z/x/y e la demo del progetto
15. Cosa devi saper spiegare
16. Per approfondire

---

## 1. Il problema: ogni minuto del piano nasce da una distanza

Il modello fisico di `SPEC.md`:

```
t_andata      = d(base, fuoco) / v_crociera * 60
t_ciclo_acqua = d(fuoco, sorgente) * 2 / v_crociera * 60 + t_pescaggio + t_sgancio
t_missione    = t_setup + t_andata + n_lanci * t_ciclo_acqua + t_ritorno
```

Tutto cio' che il solver decide dipende da `d`: il vincolo di autonomia `t_missione <=
endurance_minutes`, il vincolo di turno, il termine `response_time` nell'obiettivo, il vincolo
`MaxResponseTime` che il coordinatore impone a voce, la metrica `p90_response_minutes`.

Un errore del 5 per cento su 50 km a 200 km/h vale 0.75 minuti, trascurabile. Un errore del 29
per cento no: e' quello che si commette trattando latitudine e longitudine come un piano
quando ci si muove verso est, e sui 43 km della tratta est-ovest del paragrafo 4 sono 12.45
km, cioe' 3.7 minuti a tratta. Siccome il ciclo d'acqua percorre la stessa distanza due volte,
su venti cicli fanno due ore e mezza di volo inventate o cancellate, contro un
`endurance_minutes` che in `models.py` ha default 180.

Un'avvertenza architetturale. `SPEC.md` impone che il validatore ricalcoli tutto per conto
suo, senza importare niente da `optimize/`. Questa indipendenza protegge dagli errori di
implementazione, ma non da un modello geometrico sbagliato condiviso da entrambi: se tutti e
due usano la stessa formula errata, l'errore e' invisibile. Per questo la geodesia va capita,
non copiata.

## 2. La forma della Terra: geoide, ellissoide, sfera

Tre superfici, sempre piu' semplici e sempre meno fedeli.

Il **geoide** e' una superficie equipotenziale del campo di gravita' terrestre, grosso modo il
livello medio del mare esteso sotto i continenti. Non e' liscio: dove c'e' piu' massa la
superficie si alza, e le ondulazioni rispetto all'ellissoide vanno all'incirca da meno cento a
piu' ottanta metri. E' cio' a cui si riferiscono le quote, quindi il campo `elevation_m` di
`FireCluster`, ma non serve per le distanze orizzontali.

L'**ellissoide** e' uno sferoide oblato, definito da due numeri. Per WGS84 il semiasse
maggiore vale `a = 6 378 137` metri esatti per definizione, e lo schiacciamento `f = 1 /
298.257223563`, da cui

```
b = a * (1 - f) = 6 356 752.314 m
```

La differenza fra i semiassi e' di 21.385 km: la Terra e' piu' larga che alta di un terzo di
punto percentuale.

La **sfera** e' l'approssimazione del progetto, con `EARTH_RADIUS_KM = 6371.0`. Non e' un
numero arbitrario ma il raggio medio aritmetico,

```
R = (2a + b) / 3 = (2 * 6378.137 + 6356.752) / 3 = 6371.009 km
```

e coincide quasi esattamente anche con il raggio della sfera di pari superficie e di pari
volume.

Perche' fermarsi alla sfera? Perche' sulle tratte calabresi l'errore misurato al paragrafo 8
sta fra lo 0.1 e lo 0.25 per cento, dentro un modello che corregge del 45 per cento per la
tortuosita' stradale: raffinare li' la geometria e' ingegneria mal distribuita.

## 3. WGS84 ed EPSG:4326, e la trappola dell'ordine degli assi

**WGS84** (World Geodetic System 1984) e' un *datum*, non una proiezione: un ellissoide piu'
la sua origine, il suo orientamento e la sua scala rispetto alla Terra reale. E' il sistema
del GPS. **EPSG:4326** e' il codice del registro EPSG per "WGS84 geographic 2D", cioe'
coordinate angolari su quell'ellissoide in gradi decimali, ed e' quello che il docstring di
`GeoPoint` dichiara.

La trappola: la definizione EPSG di 4326 fissa l'ordine degli assi come (latitudine,
longitudine), mentre GeoJSON, il formato WKT dei database spaziali, Mapbox GL e l'API di FIRMS
usano (longitudine, latitudine). Non e' una divisione netta fra "lo standard" e "il web":
Leaflet, che e' la libreria su cui gira la demo del progetto, torna a (latitudine,
longitudine), e `L.circle([lat, lon])` vuole i numeri in quell'ordine. Il punto dolente e'
proprio li': un `GeoJSON` letto e passato a Leaflet senza invertire i due valori e' il modo
piu' comune di far finire i punti dall'altra parte del mondo. Nel progetto convivono entrambe
le convenzioni, ciascuna giusta nel suo contesto:

```python
GeoPoint(lat=38.9054, lon=16.2423)                 # lat prima
BoundingBox(min_lat, min_lon, max_lat, max_lon)    # lat prima
CALABRIA_BBOX = (15.55, 37.85, 17.25, 40.20)       # lon prima: lo vuole FIRMS
```

Da qui la regola: non far attraversare a una tupla nuda il confine fra due moduli. Se una
funzione riceve `(38.9, 16.2)` non puo' sapere cosa sia; se riceve un `GeoPoint`, si'.
Scambiare i due numeri in Calabria sposta il punto dal Mediterraneo alla Turchia, e siccome
`(16.2, 38.9)` e' una coordinata valida nessuna eccezione avvisa. L'unico modo di accorgersene
e' guardare la mappa, e su una mappa senza sfondo (il caso della demo quando i tile non
arrivano) non si vede nemmeno quello.

Una nota di onesta': WGS84 ha avuto varie realizzazioni successive e la placca euroasiatica si
muove di qualche centimetro l'anno, quindi a rigore una coordinata ha anche un'epoca.
Irrilevante qui, decisivo in topografia.

## 4. Latitudine e longitudine non sono un piano cartesiano

Un grado di latitudine e' un arco di meridiano e vale circa 111 km ovunque. Un grado di
longitudine si accorcia salendo verso i poli, perche' i paralleli si stringono, con il coseno
della latitudine. Le lunghezze ellissoidiche esatte:

```
lat  0      :  1 grado di longitudine = 111.319 km
lat 37.85 N :  1 grado di longitudine =  88.011 km
lat 39.00 N :  1 grado di longitudine =  86.626 km ,  1 grado di latitudine = 111.015 km
lat 40.20 N :  1 grado di longitudine =  85.144 km
lat 45.00 N :  1 grado di longitudine =  78.847 km
```

La verifica a mente si fa cosi': a 39 gradi `cos(39) = 0.7772`, e `111.319 * 0.7772 = 86.51`.
La tabella dice 86.626, cioe' 0.13 per cento in piu'. Lo scarto non e' un errore di conto: il
conto a mente usa una sfera, mentre la tabella usa l'ellissoide, dove il raggio di curvatura
in direzione est-ovest a 39 gradi e' piu' grande del raggio medio. Torna nel paragrafo 8, con
lo stesso segno.

L'errore che si commette trattando i gradi come metri. Due punti alla stessa latitudine,
(39.00 N, 16.00 E) e (39.00 N, 16.50 E): la formula ingenua `sqrt(dlat^2 + dlon^2) * 111.32`
da' `sqrt(0 + 0.25) * 111.32 = 55.660 km`, mentre la distanza vera, haversine su sfera da 6371
km, e' **43.207 km**. Un errore di 12.45 km, cioe' **+28.8 per cento**. Gli stessi 0.5 gradi
in direzione nord-sud, da (38.90 N, 16.20 E) a (39.40 N, 16.20 E), danno 55.597 km veri contro
55.660 ingenui, cioe' **+0.11 per cento**: lo stesso mezzo grado vale 43 km verso est e 55.6
km verso nord.

Per questo il difetto sopravvive: l'errore dipende dalla direzione ed e' nullo verso nord.
Dall'aeroporto di Lamezia Terme (circa 38.9054 N, 16.2423 E) a Camigliatello Silano (circa
39.3167 N, 16.4500 E) la formula ingenua da' 51.293 km contro 49.120 veri, solo il 4.4 per
cento, perche' lo spostamento e' soprattutto verso nord.

Una via di mezzo economica e' l'approssimazione equirettangolare:

```
x = R * dlambda * cos(phi_medio)
y = R * dphi
d = sqrt(x^2 + y^2)
```

dove `phi_medio` e' la semisomma delle due latitudini, `dphi` e `dlambda` le differenze in
radianti. Su Lamezia-Camigliatello da' 49.11999 km contro 49.11994 dell'haversine, quattro
centimetri di scarto. Tagliafuoco usa comunque l'haversine, perche' e' una formula sola valida
ovunque, senza la clausola "purche' i punti siano vicini e lontani dai poli" che qualcuno, due
anni dopo, violera' riusandola altrove.

## 5. La legge dei coseni sferica e perche' non basta

La distanza su una sfera e' `R` per l'angolo al centro `c`, e la legge dei coseni sferica lo
da' direttamente:

```
cos(c) = sin(phi_1) * sin(phi_2) + cos(phi_1) * cos(phi_2) * cos(dlambda)
c      = arccos( sin(phi_1)*sin(phi_2) + cos(phi_1)*cos(phi_2)*cos(dlambda) )
d      = R * c
```

dove `phi_1` e `phi_2` sono le latitudini in radianti, `dlambda` la differenza di longitudine
in radianti, `c` l'angolo al centro fra i due raggi che escono dal centro della Terra e
passano per i due punti, e `d` la lunghezza dell'arco che sottende quell'angolo.

E' corretta in matematica esatta e fragile in virgola mobile. Per `c` piccolo, `cos(c)` vale
circa `1 - c^2/2`: si sottraggono due numeri quasi uguali e si perdono cifre significative
(cancellazione), poi `arccos` amplifica il residuo, perche' la sua derivata `1 / sqrt(1 -
x^2)` diverge quando l'argomento tende a uno.

Da qui si ricava il pavimento di errore, e vale la pena rifarlo perche' e' una riga sola.
Chiamo `eps` l'epsilon di macchina, cioe' la distanza fra 1.0 e il numero rappresentabile
successivo. L'errore assoluto su `cos(c)` e' dell'ordine di `eps`; siccome `cos(c)` si
discosta da 1 di `c^2/2`, il piu' piccolo `c` che resta distinguibile da zero e' quello per
cui `c^2/2` vale `eps`, cioe' `c` dell'ordine di `sqrt(eps)`. Moltiplicato per il raggio,
il pavimento e' circa `R * sqrt(eps)`: in doppia precisione `sqrt(2.22e-16) * 6 371 000 m`,
cioe' 9.5 cm, in singola `sqrt(1.19e-7) * 6 371 000 m`, cioe' 2.2 km.

Il confronto misurato in `float64`. Prendo il punto (39.0000 N, 16.0000 E) e lo sposto verso
nord di un offset nominale, convertendo metri in gradi con 111 320 metri per grado;
l'haversine fa da riferimento, e per questo restituisce 998.876 metri dove l'offset nominale
dice 1000 (il fattore di conversione e' arrotondato, la distanza no):

```
offset nominale   haversine        legge dei coseni    scarto
   1000 m         998.876452 m     998.876454 m        2 micrometri
      1 m           0.998876 m       1.000205 m        1.3 mm
    0.1 m           0.099888 m       0.134259 m        3.4 cm
   0.01 m           0.009989 m       0.000000 m        tutto
```

A un centimetro la legge dei coseni restituisce esattamente zero. Tagliafuoco non ne soffre,
perche' la distanza piu' corta che calcola e' fra due pixel VIIRS, centinaia di metri. La
lezione e' che il pavimento passa da 9.5 cm a 2.2 km se qualcuno salva le coordinate in
`float32`, cosa che capita comprimendo un dataset o calcolando su GPU.

## 6. L'haversine: nome, derivazione, forma numerica

Il nome viene dalla trigonometria della navigazione ottocentesca. La *versine* e'
`versin(theta) = 1 - cos(theta)`, e l'*haversine* e' la sua meta':

```
hav(theta) = (1 - cos(theta)) / 2 = sin^2(theta / 2)
```

cioe' "half versed sine", mezzo seno verso. Queste funzioni venivano tabulate, e tabulare
l'haversine invece del coseno evitava sia i valori negativi sia la perdita di cifre nel
calcolare `1 - cos` su angoli piccoli. La ragione per cui sopravvive oggi e' la stessa,
trasferita dalla tavola alla virgola mobile.

La derivazione parte dalla legge dei coseni, sostituendo `cos(c) = 1 - 2*hav(c)` e
`cos(dlambda) = 1 - 2*hav(dlambda)`:

```
1 - 2*hav(c) = sin(phi_1)*sin(phi_2) + cos(phi_1)*cos(phi_2)*(1 - 2*hav(dlambda))
             = [sin(phi_1)*sin(phi_2) + cos(phi_1)*cos(phi_2)] - 2*cos(phi_1)*cos(phi_2)*hav(dlambda)
             = cos(phi_2 - phi_1) - 2*cos(phi_1)*cos(phi_2)*hav(dlambda)
             = 1 - 2*hav(dphi) - 2*cos(phi_1)*cos(phi_2)*hav(dlambda)
```

da cui, semplificando l'uno e dividendo per meno due,

```
hav(c) = hav(dphi) + cos(phi_1) * cos(phi_2) * hav(dlambda)
```

Riscritta con `hav(theta) = sin^2(theta/2)`, e' la forma che si implementa:

```
a = sin^2(dphi / 2) + cos(phi_1) * cos(phi_2) * sin^2(dlambda / 2)
c = 2 * atan2( sqrt(a), sqrt(1 - a) )
d = R * c
```

Simbolo per simbolo: `phi_1` e `phi_2` sono le latitudini in radianti; `dphi = phi_2 - phi_1`;
`dlambda` e' la differenza di longitudine in radianti; `a` e' l'haversine dell'angolo al
centro, fra 0 e 1, adimensionale; `c` e' l'angolo al centro in radianti; `R` e' il raggio,
6371 km; `d` e' la distanza lungo il grande cerchio.

E' numericamente migliore perche' `a` e' una somma di quadrati di seni di mezzi angoli, tutti
non negativi e tutti piccoli quando i punti sono vicini: non si sottrae mai niente, quindi non
c'e' cancellazione.

Il calcolo completo da Lamezia a Camigliatello:

```
dphi     = 0.007178539 rad          dlambda  = 0.003625049 rad
sin^2(dphi/2)     = 1.2882801e-05
cos(phi_1)        = 0.7781840       cos(phi_2) = 0.7736556
sin^2(dlambda/2)  = 3.2852412e-06   -> secondo termine = 1.9778675e-06
a        = 1.2882801e-05 + 1.9778675e-06 = 1.4860668e-05
sqrt(a)  = 0.0038549538             sqrt(1-a) = 0.9999925696
c        = 2 * atan2(0.0038549538, 0.9999925696) = 0.0077099266 rad
d        = 6371 * 0.0077099266 = 49.120 km
```

## 7. Perche' atan2 e non asin

L'identita' `c = 2 * asin(sqrt(a))` e' equivalente, e sulla stessa coppia restituisce
`0.007709926610489911` contro `0.007709926610489910`: identica a meno dell'ultimo bit. Perche'
preferire `atan2`?

Perche' `asin` si comporta male dove `a` si avvicina a uno, cioe' per punti quasi antipodali:
la derivata `1 / sqrt(1 - x^2)` diverge in `x = 1`, e un errore relativo minuscolo su
`sqrt(a)` diventa un errore grande su `c`. La forma `atan2(sqrt(a), sqrt(1-a))` usa entrambe
le componenti, e quando `a` tende a uno la quantita' piccola e' `sqrt(1-a)`, calcolata come
numeratore invece che ricavata per differenza.

In Calabria `a` vale al massimo qualche millesimo e la differenza e' nulla, ma la stessa
funzione prima o poi calcolera' distanze intercontinentali. In entrambi i casi conviene
limitare `a` all'intervallo `[0, 1]` prima della radice, perche' l'accumulo in virgola mobile
puo' produrre `1.0000000002` e far restituire NaN: `sqrt(1 - a)` con `a` di poco maggiore di
uno chiede la radice di un negativo, e `asin` di un argomento maggiore di uno e' fuori
dominio.

Per come lo difendi a colloquio: se in un modulo trovi scritto `2 * asin(min(1.0, sqrt(a)))`
non e' un errore, e' la stessa cosa con il taglio applicato dove serve. La differenza fra le
due forme si vede solo vicino ai punti antipodali, e il taglio e' obbligatorio in tutte e due.

## 8. Vincenty e l'ellissoide: quando lo 0.5 per cento conta

Su un ellissoide la linea piu' breve e' una geodetica, che non giace su un piano passante per
il centro e non ha formula chiusa: si risolve per iterazione. La soluzione classica di
Vincenty, degli anni settanta, converge a precisione submillimetrica ma puo' non convergere
per punti quasi antipodali; gli algoritmi piu' recenti di Karney sono incondizionatamente
convergenti, e sono quelli di PROJ e `pyproj.Geod`. Ho implementato Vincenty e l'ho
confrontato con l'haversine a 6371 km:

```
tratta                              haversine    Vincenty     differenza
Lamezia - Camigliatello              49.120 km    49.068 km    -0.105 %   (-52 m)
Reggio Calabria - Trebisacce        209.909 km   209.682 km    -0.108 %  (-226 m)
est-ovest puro, 39 N, 0.5 gradi      43.207 km    43.313 km    +0.245 %  (+106 m)
nord-sud puro, 16.2 E, 0.5 gradi     55.597 km    55.509 km    -0.159 %   (-88 m)
```

I punti sono presi al centro abitato, con coordinate approssimate: Lamezia aeroporto (38.9054
N, 16.2423 E), Camigliatello (39.3167 N, 16.4500 E), Reggio Calabria (38.1113 N, 15.6473 E),
Trebisacce (39.8700 N, 16.5300 E). Spostare il punto di qualche centinaio di metri cambia il
terzo decimale dei chilometri e non tocca la percentuale, che e' il numero che interessa qui.

Nota il segno che cambia: sulle tratte nord-sud la sfera da 6371 km sovrastima, su quelle
est-ovest sottostima. Non e' un caso, perche' i raggi di curvatura locali dell'ellissoide sono
diversi nelle due direzioni e un raggio medio unico non puo' andar bene per entrambe.

Lo 0.5 per cento che si cita come scarto massimo fra sfera ed ellissoide e' un limite globale,
fra tratte equatoriali e polari. In Calabria misuro fra 0.105 e 0.245 per cento: sulla tratta
Lamezia-Camigliatello sono 52 metri su 49 km, che a 200 km/h valgono meno di un secondo, e
sulla piu' lunga delle quattro, 226 metri, valgono quattro secondi. Conta per topografia,
catasto, confini e navigazione aerea su tratte lunghe, non per un simulatore che corregge del
45 per cento per la tortuosita' stradale. Se servisse, `pyproj.Geod(ellps="WGS84").inv(lon1,
lat1, lon2, lat2)` lo da' in una riga, longitudine per prima, ma `pyproj` non e' fra le
dipendenze del progetto.

## 9. Un esempio calabrese, e da dove viene il fattore 1.45

`SPEC.md` dice che i mezzi terrestri usano l'haversine moltiplicata per un fattore di
tortuosita' stradale, e `config.py` lo fissa a `ROAD_TORTUOSITY = 1.45`, con il commento "in
pianura vale circa 1.2, in montagna sale. 1.45 e' una taratura, non un dato misurato". Il
concetto ha un nome nella letteratura sui trasporti: fattore di circuitazione, o indice di
deviazione, il rapporto fra percorso stradale e distanza in linea d'aria.

Le distanze in linea d'aria qui sotto sono calcolate con l'haversine fra centri abitati presi
a coordinate approssimate; **le distanze stradali sono mie stime a memoria, non misure, e
vanno rifatte con un motore di routing su dati OpenStreetMap prima di essere citate come
prove**. Conta la dispersione, non il singolo numero.

```
tratta                              aria      strada (stima)   rapporto
Lamezia aeroporto - Vibo Valentia   28.4 km        33 km          1.16
Lamezia aeroporto - Catanzaro       29.9 km        38 km          1.27
Lamezia aeroporto - Crotone         78.9 km       105 km          1.33
Cosenza - San Giovanni in Fiore     38.5 km        55 km          1.43
Lamezia aeroporto - Cosenza         43.7 km        68 km          1.56
Reggio Calabria - Gambarie          17.4 km        32 km          1.84
Lamezia aeroporto - Camigliatello   49.1 km       100 km          2.04
```

Sulle tratte costiere, dove c'e' l'autostrada o una litoranea, il rapporto sta fra 1.15 e
1.35; su quelle che salgono in Sila o in Aspromonte arriva a 1.8 o 2.0, perche' la strada deve
aggirare i valloni e guadagnare quota a tornanti. Il valore 1.45 sta in mezzo: piu' alto
dell'1.2-1.3 che si cita per la pianura, piu' basso di quel che servirebbe per l'interno
silano.

Il vero limite non e' il numero ma il fatto che sia **uno solo** per ogni tratta di ogni mezzo
terrestre, mentre il fattore reale dipende dalla coppia di punti e soprattutto dal dislivello.
La via d'uscita e' precalcolare una matrice origine-destinazione con un motore di routing
(OSRM o Valhalla su un estratto OSM) sulle sole basi e sorgenti d'acqua, che sono poche e
fisse, e versionarla come dato statico: si calcola una volta sola, quindi rispetta la regola
di `SPEC.md` che vieta la rete ai test.

Quanto conta il numero? Un'autobotte a 25 km in linea d'aria, a 60 km/h. Il tempo di risposta
non e' solo il viaggio: il capitolo 6 scrive l'arrivo come `arr = dep + setup + outbound`, e
`Vehicle.setup_minutes` ha default 5, cioe' il tempo per uscire dalla base. Quindi:

```
tortuosita'   strada    viaggio    + setup 5'   arrivo
   1.20      30.0 km    30.0 min                35.0 min
   1.45      36.2 km    36.2 min                41.2 min
   1.70      42.5 km    42.5 min                47.5 min
   2.00      50.0 km    50.0 min                55.0 min
```

In `config.py` `LATE_RESPONSE_MINUTES = 45`: con 1.45 quel focolaio risulta servito in tempo,
con 1.70 risulta in ritardo, e non e' cambiato niente del mondo, solo una costante di
taratura. Notare che senza il termine di setup il confine si sposta di cinque minuti e il
verdetto cambia di nuovo: e' il motivo per cui `SPEC.md` fissa per esteso cosa entra nel tempo
di risposta invece di lasciarlo al buon senso di chi scrive il modulo. Una costante che da
sola ribalta una metrica va dichiarata in documentazione e fatta variare nell'analisi di
sensibilita', altrimenti il confronto con la baseline misura una scelta e non un risultato.

## 10. Proiezioni: cosa conservano e cosa rompono

Una mappa e' piatta, la Terra no. Il teorema egregio di Gauss dice che la curvatura gaussiana
e' intrinseca a una superficie: la sfera ce l'ha positiva, il piano nulla, quindi non si puo'
appiattire la sfera senza deformare qualcosa. Ogni proiezione e' una scelta su cosa
sacrificare.

Le **conformi** conservano gli angoli in piccolo, quindi le forme locali, e gonfiano le aree
lontano dalle linee di contatto: Mercator, la trasversa di Mercator (da cui UTM), la conica
conforme di Lambert. Servono per navigare. Le **equivalenti** conservano le aree e deformano
le forme: conica di Albers, azimutale equivalente di Lambert, Mollweide, Equal Earth. Servono
per rispondere a "quanti ettari sono bruciati". Le **equidistanti** conservano le distanze
solo lungo certe linee o da certi punti: l'azimutale equidistante dal suo centro, la plate
carree lungo i meridiani e il parallelo standard.

Per misurare la superficie percorsa dal fuoco in Calabria serve quindi una proiezione
equivalente. Lo standard europeo e' **EPSG:3035**, ETRS89 azimutale equivalente di Lambert,
quella su cui Copernicus e CORINE calcolano le statistiche di copertura del suolo: siccome
l'enumerazione `LandCover` di `models.py` e' dichiarata come CORINE aggregata, usare 3035
tiene coerenti i due conti. In alternativa va bene una conica di Albers con paralleli standard
vicini a 38.3 e 39.8 gradi.

Vale la pena conoscere anche **EPSG:32633**, UTM zona 33 nord, che copre le longitudini fra 12
e 18 gradi est e quindi tutta la Calabria: conforme, fattore di scala 0.9996 sul meridiano
centrale, deformazione di area trascurabile su una regione piccola. E conviene diffidare dei
sistemi nazionali storici: Monte Mario / Italy zone 2 (EPSG:3004) sta sul datum Roma40, e
passare a WGS84 sposta i punti di decine o centinaia di metri nel Sud Italia, piu' di un pixel
VIIRS.

## 11. Web Mercator, EPSG:3857, e perche' non ci si misurano le aree

La proiezione di quasi tutte le mappe web:

```
x = R * (lambda - lambda_0)
y = R * ln( tan(pi/4 + phi/2) )
```

con `phi` e `lambda` in radianti e `R = 6 378 137` metri fisso. EPSG:3857 prende coordinate
WGS84, che sono ellissoidiche, e le tratta come se fossero su una sfera di raggio pari al
semiasse maggiore: questa incoerenza e' il motivo per cui la proiezione rimase a lungo senza
codice EPSG ufficiale e circolava come EPSG:900913. A 39 gradi la `y` sferica e quella
ellissoidica differiscono di circa 27 km in unita' di mappa. E' coerente dentro l'ecosistema
delle mappe web, ma morde appena si converte verso una proiezione ellissoidica seria.

Web Mercator e' conforme, e per questo le forme sembrano giuste. Non conserva le aree: il
fattore di scala locale e' `1 / cos(phi)`, quindi le aree scalano come `1 / cos^2(phi)`.

```
a 38 N :  scala 1.2690 ,  area 1.6104
a 39 N :  scala 1.2868 ,  area 1.6558
a 40 N :  scala 1.3054 ,  area 1.7041
```

Un incendio da 100 ettari, misurato applicando una formula di area alle coordinate EPSG:3857 a
39 gradi, risulta di **165.6 ettari**. Un errore del 65.6 per cento su una grandezza che nel
progetto diventa litri, secondo `area_km2 * 100_000 * coeff`: 100 ettari sono un chilometro
quadrato, quindi il fabbisogno passerebbe da 100 000 litri di base a 165 600, e con lo stesso
errore si gonfierebbe anche il termine `norm(area)` della priorita'. Da notare che l'errore
cresce con la latitudine e non e' costante: a 38 gradi e' il 61 per cento, a 40 il 70. Un
fattore correttivo unico non lo sistema, perche' non e' un fattore unico.

Web Mercator non rappresenta i poli, perche' `y` diverge, e lo schema standard dei tile taglia
a `85.0511287798` gradi, la latitudine per cui `y` vale esattamente `pi * R` e il mondo
diventa un quadrato.

La regola sta in una riga: **si usa 3857 per disegnare, mai per misurare**. In Tagliafuoco la
demo web disegna su tile, quindi in Web Mercator, ma ogni numero mostrato e' calcolato a monte
da coordinate WGS84 con l'haversine: la mappa e' una superficie di visualizzazione, non uno
strumento di misura.

## 12. Bounding box e contenimento punto in poligono

Il **bounding box** e' il filtro piu' economico che esista, quattro confronti, ed e' quello
che fa `BoundingBox.contains` in `models.py`: la fase di filtro di uno schema
filtra-e-raffina, in cui si scarta in fretta quasi tutto e si paga il test costoso solo sui
pochi sopravvissuti.

Un bounding box in gradi e' pero' un rettangolo nel piano latitudine-longitudine, non sul
terreno: il bordo sud e' piu' largo di quello nord. Per la Calabria di `config.py`, 1.70 gradi
di longitudine valgono 149.6 km a 37.85 N e 144.7 km a 40.20 N, mentre i 2.35 gradi di
latitudine valgono 260.9 km. C'e' anche il caso dell'antimeridiano, dove `min_lon > max_lon` e
il confronto ingenuo fallisce.

Il **contenimento punto in poligono** riempie i campi `comune` e `provincia` di `FireCluster`.
L'algoritmo del raggio si appoggia al teorema della curva di Jordan: si traccia una semiretta
dal punto, si contano gli attraversamenti dei lati, e il punto e' interno se il conto e'
dispari.

```python
def punto_in_poligono(lat, lon, vertici):
    """Algoritmo del raggio. Il raggio parte dal punto verso longitudini crescenti."""
    dentro = False
    n = len(vertici)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = vertici[i]
        lat_j, lon_j = vertici[j]
        if (lat_i > lat) != (lat_j > lat):          # il lato attraversa la quota del punto
            lon_taglio = lon_i + (lat - lat_i) * (lon_j - lon_i) / (lat_j - lat_i)
            if lon < lon_taglio:
                dentro = not dentro
        j = i
    return dentro
```

Il punto delicato e' il test `(lat_i > lat) != (lat_j > lat)`, che rende il lato semiaperto
rispetto alla latitudine contando l'estremo inferiore e non quello superiore: senza questa
accortezza, un raggio che passa esattamente per un vertice conterebbe due attraversamenti
invece di uno e il risultato si ribalterebbe. Resta la convenzione pari-dispari, che su un
poligono autointersecante decide diversamente dall'algoritmo del numero di avvolgimento, piu'
robusto e piu' costoso.

Il costo e' lineare nei vertici, a ogni interrogazione: qualche centinaio di comuni calabresi
da migliaia di vertici ciascuno, per ogni focolaio e per ogni finestra della rigiocata
stagionale. E' qui che serve un indice.

## 13. Indici spaziali: R-tree, quadtree, geohash, celle S2

Un **R-tree** e' un albero bilanciato di rettangoli minimi contenitori annidati: le foglie
tengono gli oggetti, i nodi interni il rettangolo che racchiude i figli, e una interrogazione
scende solo nei rami il cui rettangolo interseca la finestra di ricerca, con costo medio
logaritmico che degrada verso il lineare quando i rettangoli si sovrappongono molto. E'
l'indice dei database spaziali (in PostGIS con GiST) e di `STRtree` in Shapely, e indicizza
forme qualsiasi: e' quindi quello giusto per i poligoni comunali.

Un **quadtree** suddivide ricorsivamente un quadrato in quattro quadranti finche' ogni cella
contiene pochi punti: la geometria non dipende dai dati, il che lo rende prevedibile ma
fragile quando la distribuzione e' molto disuniforme, come i rilevamenti di incendio. Un **k-d
tree** taglia alternando gli assi ed e' ottimo per i punti in bassa dimensione, ma in
scikit-learn supporta solo le metriche di Minkowski; il **ball tree**, che partiziona con
ipersfere annidate, supporta anche l'haversine, ed e' per questo che il DBSCAN del capitolo 3
finisce a usarlo.

Un **geohash** interlaccia i bit di latitudine e longitudine e li codifica in base 32. Il
prefisso identifica una cella, e piu' e' lungo piu' la cella e' piccola: all'equatore cinque
caratteri danno circa 4.9 km di lato, sei circa 1.2 per 0.6 km, sette circa 153 metri. Il
pregio e' che e' solo una stringa, quindi qualunque indice di database la ordina e la ricerca
per prefisso diventa una ricerca spaziale grossolana; il difetto e' che due punti a un metro
possono cadere in celle con prefissi diversi, quindi ogni ricerca di prossimita' deve
controllare anche le otto celle vicine.

Le **celle S2** proiettano la sfera sulle sei facce di un cubo inscritto e ordinano le celle
lungo una curva di Hilbert, cosi' che una cella sia un intero a 64 bit e la vicinanza spaziale
diventi in larga parte vicinanza fra interi, interrogabile come intervallo. Al livello 12 una
cella copre circa 5 km quadrati, e rispetto al geohash si comporta molto meglio vicino ai
poli. H3 e' l'analogo esagonale.

Alla scala calabrese i punti sono centinaia o poche migliaia e la forza bruta basta, mentre
per i poligoni comunali un R-tree costruito una volta e riusato per tutta la rigiocata
stagionale ripaga subito. S2 e geohash servirebbero estendendo il sistema al Mediterraneo o
mettendo i rilevamenti in un database.

## 14. Il sistema di tile z/x/y e la demo del progetto

Le mappe web sono fatte di quadrati precalcolati. Al livello di zoom `z` il mondo e' una
griglia di `2^z` per `2^z` tile da 256 pixel di lato, indirizzati `z/x/y`, dove `y` si conta
dall'alto, cioe' da nord:

```
x = floor( (lambda_gradi + 180) / 360 * 2^z )
y = floor( (1 - asinh(tan(phi_radianti)) / pi) / 2 * 2^z )
```

La `y` e' quella di Web Mercator normalizzata sull'intervallo del mondo, e `asinh(tan(phi))`
e' un modo compatto di scrivere `ln(tan(phi) + sec(phi))`. Verificato sui due punti usati in
tutto il capitolo:

```
Lamezia       (38.9054, 16.2423) :  z=8 -> 139/97 ,  z=10 -> 558/391 ,  z=12 -> 2232/1566
Camigliatello (39.3167, 16.4500) :  z=8 -> 139/97 ,  z=10 -> 558/390 ,  z=12 -> 2235/1560
```

A zoom 8 cadono nello stesso tile: a quella scala sono lo stesso posto.

La risoluzione al suolo vale `156543.03 * cos(phi) / 2^z` metri per pixel. A 39 gradi:

```
z = 8  :  475.22 m/px  ->  tile largo 121.7 km  ;  pixel VIIRS =  0.79 px
z = 10 :  118.81 m/px  ->  tile largo  30.4 km  ;  pixel VIIRS =  3.16 px
z = 12 :   29.70 m/px  ->  tile largo   7.6 km  ;  pixel VIIRS = 12.63 px
z = 14 :    7.43 m/px  ->  tile largo   1.9 km  ;  pixel VIIRS = 50.50 px
```

Ne seguono due regole di disegno per la demo, che sono aritmetica e non opinione. Sotto lo
zoom 9 l'impronta di un pixel VIIRS occupa meno di un pixel schermo, quindi va disegnato un
simbolo e non un rettangolo al suolo. Dallo zoom 12 in su occupa una dozzina di pixel e si
puo' mostrare com'e' davvero, allargata fuori nadir secondo `scan_km` e `track_km`. E il
raggio di clustering `eps = 1.5 km` vale 50.5 pixel a zoom 12: disegnarne la circonferenza
attorno a un rilevamento fa vedere all'operatore perche' due pixel sono finiti nello stesso
focolaio, il modo piu' diretto di rendere ispezionabile la scelta del capitolo 3.

Due note pratiche. `SPEC.md` impone che tutto giri senza rete, quindi la demo deve degradare a
uno sfondo neutro quando i tile non arrivano. E i tile "retina" da 512 pixel spostano tutti i
numeri di un livello di zoom, quindi le soglie vanno legate ai metri per pixel, non al numero
`z`.

---

## Cosa devi saper spiegare

**Perche' non si possono trattare latitudine e longitudine come coordinate cartesiane?**
Perche' un grado di latitudine vale sempre circa 111 km, mentre un grado di longitudine si
accorcia con il coseno della latitudine: a 39 gradi vale 86.6 km. Due punti alla stessa
latitudine distanti 0.5 gradi di longitudine, a 39 N, distano 43.207 km, mentre la formula
ingenua `sqrt(dlat^2+dlon^2)*111.32` da' 55.660 km, il 28.8 per cento in piu'. Gli stessi 0.5
gradi verso nord danno un errore dello 0.11 per cento, ed e' per questo che il difetto
sopravvive: sulle tratte nord-sud sembra corretto.

**Scrivi la formula dell'haversine e spiega perche' esiste.**
`a = sin^2(dphi/2) + cos(phi_1)*cos(phi_2)*sin^2(dlambda/2)`, `c = 2*atan2(sqrt(a),
sqrt(1-a))`, `d = R*c`, con latitudini e differenza di longitudine in radianti. Esiste perche'
la legge dei coseni sferica calcola `cos(c)`, che per angoli piccoli vale circa `1 - c^2/2`:
si sottraggono due numeri quasi uguali, si perdono cifre, e `arccos` amplifica il resto.
L'haversine somma solo quadrati di seni di mezzi angoli e non sottrae mai. Il pavimento
d'errore della legge dei coseni e' di circa 9.5 cm in `float64` e 2.2 km in `float32`.

**Perche' `atan2` invece di `asin`, e quando serve Vincenty?**
Sono algebricamente equivalenti, ma la derivata di `asin(x)` e' `1/sqrt(1-x^2)` e diverge in
`x = 1`, cioe' per punti quasi antipodali dove `a` tende a uno, mentre `atan2(sqrt(a),
sqrt(1-a))` usa entrambe le componenti e calcola direttamente la quantita' piccola. In
Calabria non cambia nulla, ma e' l'abitudine giusta; conviene comunque limitare `a` a `[0,
1]`, perche' l'accumulo in virgola mobile puo' farlo uscire e produrre NaN. Vincenty, cioe' la
geodetica sull'ellissoide, serve quando conta lo 0.1 per cento: topografia, confini, tratte
aeree lunghe. Sulle tratte calabresi ho misurato scarti fra lo 0.105 e lo 0.245 per cento, e
il segno cambia con la direzione perche' un raggio medio unico non puo' andar bene sia per la
curvatura meridiana sia per quella normale.

**Da dove viene il fattore 1.45 e come lo difendi?**
E' una taratura dichiarata, e `config.py` lo dice per esteso. Il concetto e' il fattore di
circuitazione, il rapporto fra percorso stradale e linea d'aria. Sulle tratte calabresi che ho
stimato va da 1.16 sulla costa a 2.04 per salire a Camigliatello dall'aeroporto di Lamezia, e
1.45 sta in mezzo; le distanze in aria sono calcolate, quelle stradali sono stime mie e le
dichiaro come tali. Il limite non e' il valore ma il fatto che sia uno solo per tutte le
tratte: la soluzione corretta e' una matrice origine-destinazione precalcolata con un motore
di routing su OSM e versionata come dato statico. E conta: su 25 km in linea d'aria a 60 km/h,
con i 5 minuti di setup, 1.45 porta l'arrivo a 41 minuti e 1.70 a 47, mentre
`LATE_RESPONSE_MINUTES` vale 45. La stessa assegnazione passa da puntuale a tardiva per via di
una costante, non di un dato.

**Perche' non si misurano le aree in Web Mercator?**
Perche' e' conforme, non equivalente: conserva gli angoli e gonfia le aree con `1/cos^2(phi)`.
A 39 gradi il fattore vale 1.6558, quindi un incendio da 100 ettari ne risulta 165.6, il 65.6
per cento in piu', e nel progetto quell'area diventa litri. Il fattore cresce con la
latitudine, quindi non lo si corregge con una costante. Per misurare davvero la superficie
percorsa in Calabria si
usa una proiezione equivalente, per esempio EPSG:3035, quella su cui Copernicus e CORINE
calcolano le statistiche di copertura, coerente con l'enumerazione `LandCover`. In alternativa
UTM 33 nord, conforme ma con deformazione trascurabile su una regione piccola.

**Come funziona l'algoritmo del raggio, e perche' convivono due ordini degli assi?**
Si traccia una semiretta dal punto e si contano gli attraversamenti dei lati: dispari
significa dentro, per il teorema della curva di Jordan. Il caso che rompe le implementazioni
ingenue e' la semiretta che passa esattamente per un vertice, perche' i due lati adiacenti
verrebbero contati entrambi; si risolve rendendo il lato semiaperto rispetto alla quota, con
il test `(lat_i > lat) != (lat_j > lat)`, che conta l'estremo inferiore e non quello
superiore. Sugli assi: EPSG:4326 definisce l'ordine (latitudine, longitudine), che `GeoPoint`
e `BoundingBox` seguono, mentre GeoJSON, il WKT dei database spaziali e l'API di FIRMS usano
(longitudine, latitudine), motivo per cui `CALABRIA_BBOX` e' scritto `(15.55, 37.85, 17.25,
40.20)`. E non e' una divisione fra standard e web: Leaflet, su cui gira la demo, torna a
(latitudine, longitudine), quindi passargli un GeoJSON senza invertire e' il modo classico di
sbagliare. La difesa e' non far passare tuple nude fra i moduli, perche' in Calabria invertire
i due valori sposta il punto in Turchia senza sollevare nessuna eccezione.

---

## Per approfondire

Sulle proiezioni, il testo di riferimento accessibile e' il manuale di John Snyder pubblicato
dallo United States Geological Survey, che per ogni proiezione da' formule dirette e inverse,
deformazione e uso storico. Sulla parte geodetica, il lavoro di Thaddeus Vincenty degli anni
settanta introduce la soluzione iterativa su ellissoide, e i lavori piu' recenti di Charles
Karney ne risolvono i casi di non convergenza: la documentazione di GeographicLib spiega il
metodo in modo leggibile e confronta i due. Sull'haversine, la nota classica che ne difende
l'uso contro la legge dei coseni e' quella di Sinnott, di meta' anni ottanta, comparsa su una
rivista di astronomia amatoriale.

Per la pratica, il sito Movable Type Scripts di Chris Veness raccoglie haversine, rotta
iniziale e destinazione data rotta e distanza, con codice funzionante e derivazioni accanto.
La documentazione di PROJ e di `pyproj` spiega la differenza fra datum, sistema di riferimento
e proiezione meglio di quasi tutti i manuali, e il registro EPSG online da' la definizione
formale di 4326, 3857, 3035 e 32633, ordine degli assi incluso. Sugli indici spaziali, la
documentazione di PostGIS sugli indici GiST spiega gli R-tree dal lato pratico, e il sito del
progetto S2 Geometry ha un'esposizione visiva della proiezione su cubo e della curva di
Hilbert. Sul sistema di tile, la pagina "Slippy map tilenames" del wiki di OpenStreetMap
contiene formule, codice e tabelle di risoluzione per livello di zoom.

Nel repository: `SPEC.md` al paragrafo sul modello fisico, `config.py` per `EARTH_RADIUS_KM`,
`ROAD_TORTUOSITY` e `CALABRIA_BBOX`, `domain/models.py` per `GeoPoint` e `BoundingBox`. Il
capitolo 3 usa la metrica haversine dentro DBSCAN.
