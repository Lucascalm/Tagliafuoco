/* I segni sulla carta: focolai, basi, mezzi, rotte, aree protette.
 *
 * Perche' questo file esiste separato dal resto: qui sta l'unica parte della
 * demo che dipende da Leaflet, e tenerla confinata significa che il giorno in
 * cui la mappa cambiasse libreria non cambierebbe niente altro.
 *
 * I segnaposto sono in SVG e non su canvas. La scelta non e' di gusto: in una
 * finestra di poche ore la Calabria ha qualche decina di focolai e la flotta
 * conta ventisei mezzi. Con numeri cosi' bassi l'SVG regala il focus da
 * tastiera, gli eventi e la leggibilita' per gli assistenti vocali, mentre il
 * canvas costringerebbe a riscrivere a mano tutto quanto. La soglia in cui la
 * scelta si rovescerebbe e' il disegno di tutti i rilevamenti della stagione
 * insieme, che sono qualche migliaio: li' servirebbe il canvas.
 *
 * Ogni canale visivo porta una informazione e una sola. Il raggio del cerchio
 * cresce con la radice quadrata della potenza radiante, perche' l'occhio
 * confronta le aree e non i raggi. Il colore non codifica l'intensita': dice
 * soltanto se il focolaio e' in interfaccia urbano-foresta, e lo dice insieme
 * allo spessore dell'anello, cosi' resta leggibile anche a chi non distingue
 * quei due colori.
 */

(function () {
  "use strict";

  var TF = (window.TF = window.TF || {});

  /* Fondo cartografico. Si usano i tile standard di OpenStreetMap perche'
   * sono gli unici, fra quelli provati, che funzionano senza chiave: il
   * progetto ha la regola di girare senza chiavi, e un fondo che chiede una
   * chiave la violerebbe proprio nella pagina che lo dimostra. Lo stile
   * standard e' piu' colorato di quanto serva, e viene desaturato e scaldato
   * con un filtro CSS per portarlo sul tono della carta. L'attribuzione e'
   * una condizione d'uso, non una formalita': resta visibile in basso. */
  var TILE = {
    chiaro: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    scuro: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  };
  var ATTRIBUZIONE =
    'Fondo cartografico &copy; ' +
    '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, ' +
    "contributori";

  /* Raggio minimo di un focolaio, in pixel. Sotto i tre pixel e mezzo un
   * cerchio non si clicca e non si vede: e' un pavimento di leggibilita',
   * dichiarato, che rompe la proporzionalita' esatta sui focolai piccolissimi.
   *
   * Il tetto serve al problema opposto. La potenza radiante nella stagione va
   * da meno di un megawatt a oltre millecinquecento: tre ordini di grandezza.
   * Con la sola radice quadrata il focolaio piu' grande avrebbe un raggio di
   * quarantacinque pixel e coprirebbe mezza provincia, e la mappa
   * racconterebbe solo quello. Il tetto e' una rottura dichiarata della
   * proporzionalita' sui casi estremi, ed e' scritto nella legenda. */
  var RAGGIO_MINIMO = 3.5;
  var RAGGIO_MASSIMO = 20;
  var RAGGIO_FATTORE = 1.15;

  /* Prefissi che tutti i nomi delle basi condividono. Toglierli non perde
   * informazione (la forma del segnaposto dice gia' che e' una base) e libera
   * meta' della larghezza di ogni etichetta sulla carta. */
  var PREFISSI_BASE = [
    "Aeroporto di ",
    "Elisuperficie AIB di ",
    "Distaccamento VVF di ",
    "Comando VVF di ",
  ];

  function etichettaBase(nome) {
    var corto = nome;
    for (var i = 0; i < PREFISSI_BASE.length; i++) {
      if (corto.indexOf(PREFISSI_BASE[i]) === 0) {
        corto = corto.slice(PREFISSI_BASE[i].length);
        break;
      }
    }
    return corto.length > 20 ? corto.slice(0, 19) + "…" : corto;
  }

  var COLORI = ["--brace", "--ocra", "--ardesia", "--bosco", "--carta", "--inchiostro"];

  function tinta(nome) {
    return getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
  }

  function elementoDi(strato) {
    /* Leaflet espone getElement solo su alcuni strati. Per i tracciati SVG il
     * nodo sta in _path: e' privato, ma e' la via nota per arrivare al nodo e
     * renderlo raggiungibile da tastiera. */
    if (typeof strato.getElement === "function") {
      var el = strato.getElement();
      if (el) return el;
    }
    return strato._path || strato._icon || null;
  }

  function Mappa(contenitore, stagione) {
    this.stagione = stagione;
    this.tavolozza = {};
    this.focolai = {};
    this.mezzi = {};
    this.rotte = [];
    this.evidenziato = null;
    this.suFocolaio = null;
    this.tileVivi = true;

    var bbox = stagione.bbox || [37.85, 15.55, 40.2, 17.25];
    this.mappa = L.map(contenitore, {
      zoomControl: true,
      attributionControl: true,
      preferCanvas: false,
      zoomSnap: 0.5,
      minZoom: 6,
      maxZoom: 13,
    });
    this.mappa.fitBounds(
      [
        [bbox[0], bbox[1]],
        [bbox[2], bbox[3]],
      ],
      { padding: [12, 12] }
    );
    this.mappa.attributionControl.setPrefix("");

    this.stratoAree = L.layerGroup().addTo(this.mappa);
    this.stratoRotte = L.layerGroup().addTo(this.mappa);
    this.stratoFocolai = L.layerGroup().addTo(this.mappa);
    this.stratoBasi = L.layerGroup().addTo(this.mappa);
    this.stratoMezzi = L.layerGroup().addTo(this.mappa);

    this.leggiTinte();
    this.disegnaAreeProtette();
    this.disegnaBasi();
    this.preparaMezzi();
  }

  Mappa.prototype.leggiTinte = function () {
    var self = this;
    COLORI.forEach(function (nome) {
      self.tavolozza[nome] = tinta(nome);
    });
  };

  /* ---------------------------------------------------------------- tile */

  Mappa.prototype.montaTile = function (scuro, quandoSpenti) {
    var self = this;
    if (this.tile) {
      this.mappa.removeLayer(this.tile);
      this.tile = null;
    }
    var caricati = 0;
    var errori = 0;

    this.indirizzoTile = scuro ? TILE.scuro : TILE.chiaro;
    this.tile = L.tileLayer(this.indirizzoTile, {
      attribution: ATTRIBUZIONE,
      maxZoom: 13,
      crossOrigin: true,
    });

    function spegni(motivo) {
      if (!self.tileVivi) return;
      self.tileVivi = false;
      if (self.tile) {
        self.mappa.removeLayer(self.tile);
        self.tile = null;
      }
      if (quandoSpenti) quandoSpenti(motivo);
    }

    this.tile.on("tileload", function () {
      caricati += 1;
    });
    this.tile.on("tileerror", function () {
      errori += 1;
      if (caricati === 0 && errori >= 4) spegni("errori di rete sui tile");
    });

    this.tileVivi = true;
    this.tile.addTo(this.mappa);
    this.tile.bringToBack();

    /* Nessun tile arrivato entro sette secondi vuol dire che il fondo non
     * arrivera'. Non si aspetta oltre: la mappa resta usabile con i soli
     * segnaposto sopra un fondo neutro, e la pagina lo dice. */
    window.setTimeout(function () {
      if (caricati === 0) spegni("nessun tile ricevuto entro sette secondi");
    }, 7000);
  };

  /* Il cambio di tema non rimonta i tile se il fondo e' lo stesso.
   *
   * Oggi le due voci di TILE puntano allo stesso servizio, perche' e' l'unico
   * fondo che funziona senza chiave, e il tono notturno si ottiene con un
   * filtro CSS. Rimontare lo strato in quel caso non cambia niente di
   * visibile, ricarica i riquadri e soprattutto riarma la scadenza dei sette
   * secondi: chi accende e spegne il tema con la rete lenta si vedrebbe
   * comparire l'avviso "nessun tile ricevuto" su una mappa che i tile ce li
   * ha. Il ramo con l'indirizzo diverso resta, perche' il giorno in cui il
   * fondo notturno arrivera' da un altro servizio andra' rimontato davvero. */
  Mappa.prototype.cambiaTema = function (scuro, quandoSpenti) {
    this.leggiTinte();
    var indirizzo = scuro ? TILE.scuro : TILE.chiaro;
    if (this.tileVivi && indirizzo !== this.indirizzoTile) {
      this.montaTile(scuro, quandoSpenti);
    }
    this.ridipingiColori();
  };

  /* ---------------------------------------------------------------- strati fissi */

  Mappa.prototype.disegnaAreeProtette = function () {
    var self = this;
    (this.stagione.aree_protette || []).forEach(function (area) {
      /* Campitura leggerissima e nessun bordo marcato: l'area protetta e'
       * contesto, non deve competere con i focolai. */
      var cerchio = L.circle([area.lat, area.lon], {
        radius: area.raggio_km * 1000,
        color: self.tavolozza["--bosco"],
        weight: 1,
        opacity: 0.22,
        fillColor: self.tavolozza["--bosco"],
        fillOpacity: 0.09,
        interactive: false,
      });
      cerchio.addTo(self.stratoAree);
    });
  };

  Mappa.prototype.disegnaBasi = function () {
    var self = this;
    (this.stagione.basi || []).forEach(function (base) {
      var icona = L.divIcon({
        className: "",
        html:
          '<svg class="segno segno-base" width="140" height="24" viewBox="0 0 140 24">' +
          '<rect x="4" y="6" width="11" height="11"/>' +
          '<text x="20" y="15.5">' +
          etichettaBase(base.nome).replace(/&/g, "&amp;").replace(/</g, "&lt;") +
          "</text></svg>",
        iconSize: [140, 24],
        iconAnchor: [9.5, 11.5],
      });
      var segno = L.marker([base.lat, base.lon], {
        icon: icona,
        keyboard: true,
        alt: "Base " + base.nome,
        title: "Base " + base.nome,
        riseOnHover: true,
      });
      segno.bindPopup(
        "<b>" +
          base.nome +
          "</b><br>Base operativa, provincia di " +
          base.prov +
          "<br>" +
          (base.notte ? "abilitata al volo notturno" : "senza volo notturno")
      );
      segno.addTo(self.stratoBasi);
    });
  };

  /* ---------------------------------------------------------------- mezzi */

  function formaMezzo(aereo) {
    /* Triangolo per i mezzi aerei, quadratino per i terrestri. La forma dice
     * il dominio, il riempimento dice lo stato, l'orientamento dice la
     * direzione di marcia. Tre canali, tre informazioni. */
    if (aereo) {
      return '<path class="corpo" d="M9 2.5 L13.5 15 L9 12.4 L4.5 15 Z"/>';
    }
    return '<rect class="corpo" x="5.5" y="5.5" width="7" height="7"/>';
  }

  Mappa.prototype.preparaMezzi = function () {
    var self = this;
    var basi = {};
    (this.stagione.basi || []).forEach(function (b) {
      basi[b.id] = b;
    });
    this.basiPerId = basi;

    /* Piu' mezzi condividono la stessa base. Se restassero sovrapposti si
     * vedrebbe un segnaposto solo: si sparpagliano su un anello attorno alla
     * base, in ordine stabile, cosi' la posizione a riposo e' finta ma
     * leggibile, e il mezzo torna sul punto esatto quando e' in volo. */
    var conta = {};
    (this.stagione.mezzi || []).forEach(function (mezzo) {
      conta[mezzo.base] = (conta[mezzo.base] || 0) + 1;
    });
    var indice = {};

    (this.stagione.mezzi || []).forEach(function (mezzo) {
      var base = basi[mezzo.base];
      if (!base) return;
      var i = indice[mezzo.base] || 0;
      indice[mezzo.base] = i + 1;
      var n = conta[mezzo.base];
      var angolo = (2 * Math.PI * i) / Math.max(1, n);
      var raggio = n > 1 ? 0.035 : 0;
      var aereo = mezzo.dom === "air";

      var icona = L.divIcon({
        className: "",
        html:
          '<svg class="segno segno-mezzo" width="18" height="18" viewBox="0 0 18 18">' +
          '<g class="rot" transform="rotate(0 9 9)">' +
          formaMezzo(aereo) +
          "</g></svg>",
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });

      var segno = L.marker([base.lat + raggio * Math.cos(angolo), base.lon + raggio * Math.sin(angolo)], {
        icon: icona,
        keyboard: true,
        alt: mezzo.nome,
        title: mezzo.nome,
      });
      segno.addTo(self.stratoMezzi);

      self.mezzi[mezzo.id] = {
        dati: mezzo,
        aereo: aereo,
        segno: segno,
        riposo: [base.lat + raggio * Math.cos(angolo), base.lon + raggio * Math.sin(angolo)],
        stato: "",
        rotazione: null,
      };
    });
  };

  Mappa.prototype.aggiornaMezzi = function (posizioni) {
    var self = this;
    Object.keys(this.mezzi).forEach(function (id) {
      var voce = self.mezzi[id];
      var stato = posizioni && posizioni[id];
      var lat, lon, classe, rotta, testo;

      if (stato && stato.lat !== undefined) {
        lat = stato.lat;
        lon = stato.lon;
        classe = stato.classe || "in-missione";
        rotta = stato.rotta;
        testo = stato.descrizione;
      } else {
        lat = voce.riposo[0];
        lon = voce.riposo[1];
        classe = (stato && stato.classe) || "fermo";
        rotta = null;
        testo = voce.dati.nome + ", fermo alla base";
      }

      voce.segno.setLatLng([lat, lon]);
      var nodo = elementoDi(voce.segno);
      if (!nodo) return;
      var svg = nodo.querySelector("svg");
      if (svg) {
        var nuova = "segno segno-mezzo " + classe;
        if (svg.getAttribute("class") !== nuova) svg.setAttribute("class", nuova);
        /* Il centro di rotazione e' il centro del riquadro, 9 su 18, cioe' lo
         * stesso punto che Leaflet ancora sulla coordinata. Ruotare attorno a
         * un altro punto sposterebbe il segnaposto rispetto alla posizione che
         * dichiara, e il triangolo scivolerebbe via dalla sua rotta. */
        var g = svg.querySelector(".rot");
        if (g) {
          var gradi = rotta === null || rotta === undefined ? 0 : rotta;
          g.setAttribute("transform", "rotate(" + gradi.toFixed(0) + " 9 9)");
        }
      }
      nodo.setAttribute("aria-label", testo);
      nodo.setAttribute("title", testo);
    });
  };

  /* ---------------------------------------------------------------- focolai */

  function raggioFocolaio(frp) {
    /* Radice quadrata, non proporzionale: confrontando due cerchi l'occhio
     * giudica l'area, quindi e' l'area che deve crescere con il dato. */
    var r = RAGGIO_FATTORE * Math.sqrt(Math.max(0, frp));
    return Math.min(RAGGIO_MASSIMO, Math.max(RAGGIO_MINIMO, r));
  }

  /* Un dato mancante non diventa un numero.
   *
   * La distanza dal costruito e la potenza radiante possono non esserci: il
   * focolaio arriva dall'arricchimento territoriale, e l'arricchimento non
   * trova sempre un comune vicino. Scrivere "a null km dal costruito" o "0
   * megawatt" al posto del dato assente sarebbe peggio di non scrivere
   * niente, perche' un segnaposto sembra una misura. */
  function misura(valore, unita) {
    if (valore === null || valore === undefined || isNaN(Number(valore))) {
      return null;
    }
    return valore + " " + unita;
  }

  function descriviFocolaio(f) {
    var parti = ["Focolaio " + f.id, "comune di " + (f.comune || "non attribuito")];
    var potenza = misura(f.frp, "megawatt");
    parti.push(potenza ? "potenza radiante " + potenza : "potenza radiante non nota");
    var area = misura(f.area, "chilometri quadrati");
    if (area) parti.push("area stimata " + area);
    parti.push(f.wui ? "in interfaccia urbano-foresta" : "lontano dal costruito");
    if (f.prot) parti.push("dentro " + f.prot);
    return parti.join(", ");
  }

  Mappa.prototype.aggiornaFocolai = function (visibili) {
    var self = this;
    var vivi = {};

    visibili.forEach(function (voce) {
      var f = voce.focolaio;
      vivi[f.id] = true;
      var cerchio = self.focolai[f.id];

      if (!cerchio) {
        cerchio = L.circleMarker([f.lat, f.lon], {
          radius: raggioFocolaio(f.frp),
          weight: f.wui ? 2.4 : 0.8,
          color: f.wui ? self.tavolozza["--ocra"] : self.tavolozza["--brace"],
          fillColor: self.tavolozza["--brace"],
          bubblingMouseEvents: false,
        });
        cerchio.addTo(self.stratoFocolai);
        cerchio.bindPopup(function () {
          var distanza = misura(f.d_wui, "km");
          var litri =
            f.litri === null || f.litri === undefined
              ? null
              : Number(f.litri).toLocaleString("it-IT");
          return (
            "<b>" +
            (f.comune || "comune non attribuito") +
            "</b><br>" +
            '<span class="cifre">' +
            (misura(f.frp, "MW") || "potenza non nota") +
            "</span> di potenza radiante, " +
            '<span class="cifre">' +
            (f.area === null || f.area === undefined ? "area n.d." : f.area) +
            "</span> km&sup2;<br>" +
            (distanza
              ? (f.wui ? "In interfaccia urbano-foresta (" : "A ") +
                distanza +
                (f.wui ? " dal costruito)" : " dal costruito")
              : "Distanza dal costruito non calcolata") +
            (litri
              ? '<br>Fabbisogno stimato <span class="cifre">' + litri + "</span> litri"
              : "<br>Fabbisogno non stimato") +
            (f.prot ? "<br>" + f.prot : "") +
            '<br><span class="cifre">' +
            f.det +
            "</span> rilevamenti, gravita' " +
            f.sev
          );
        });
        cerchio.on("click", function () {
          if (self.suFocolaio) self.suFocolaio(f.id);
        });
        var nodo = elementoDi(cerchio);
        if (nodo) {
          nodo.setAttribute("tabindex", "0");
          nodo.setAttribute("role", "button");
          nodo.setAttribute("aria-label", descriviFocolaio(f));
          /* Invio e barra spaziatrice fanno quello che fa il clic. Un
           * segnaposto raggiungibile con Tab ma che non risponde ai tasti e'
           * raggiungibile e basta, non usabile. */
          nodo.addEventListener("keydown", function (e) {
            if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
            e.preventDefault();
            cerchio.openPopup();
            if (self.suFocolaio) self.suFocolaio(f.id);
          });
        }
        self.focolai[f.id] = cerchio;
      }

      var acceso = self.evidenziato === f.id;
      cerchio.setStyle({
        fillOpacity: voce.alfa,
        opacity: Math.min(1, voce.alfa + 0.25),
        weight: acceso ? 3.2 : f.wui ? 2.4 : 0.8,
        color: acceso
          ? self.tavolozza["--inchiostro"]
          : f.wui
          ? self.tavolozza["--ocra"]
          : self.tavolozza["--brace"],
      });
    });

    Object.keys(this.focolai).forEach(function (id) {
      if (!vivi[id]) {
        self.stratoFocolai.removeLayer(self.focolai[id]);
        delete self.focolai[id];
      }
    });
  };

  Mappa.prototype.ridipingiColori = function () {
    var self = this;
    Object.keys(this.focolai).forEach(function (id) {
      var c = self.focolai[id];
      c.setStyle({ fillColor: self.tavolozza["--brace"] });
    });
    this.stratoAree.eachLayer(function (l) {
      l.setStyle({ color: self.tavolozza["--bosco"], fillColor: self.tavolozza["--bosco"] });
    });
  };

  /* ---------------------------------------------------------------- rotte */

  Mappa.prototype.aggiornaRotte = function (elenco) {
    var self = this;
    this.stratoRotte.clearLayers();
    this.rotte = [];

    elenco.forEach(function (rotta) {
      /* Tratto continuo per il percorso gia' fatto, tratteggiato per quello
       * previsto. E' la convenzione delle carte operative, e distingue un
       * dato osservato da una intenzione. */
      if (rotta.fatto && rotta.fatto.length > 1) {
        L.polyline(rotta.fatto, {
          color: self.tavolozza["--ardesia"],
          weight: rotta.acceso ? 2.2 : 1.3,
          opacity: rotta.acceso ? 0.95 : 0.65,
          interactive: false,
        }).addTo(self.stratoRotte);
      }
      if (rotta.previsto && rotta.previsto.length > 1) {
        L.polyline(rotta.previsto, {
          color: self.tavolozza["--ardesia"],
          weight: rotta.acceso ? 2.2 : 1.3,
          opacity: rotta.acceso ? 0.8 : 0.45,
          dashArray: "4 4",
          interactive: false,
        }).addTo(self.stratoRotte);
      }
    });
  };

  /* ---------------------------------------------------------------- selezione */

  Mappa.prototype.evidenzia = function (idFocolaio) {
    this.evidenziato = idFocolaio || null;
  };

  Mappa.prototype.centraSu = function (idFocolaio) {
    var cerchio = this.focolai[idFocolaio];
    if (!cerchio) return false;
    this.mappa.panTo(cerchio.getLatLng(), { animate: false });
    cerchio.openPopup();
    return true;
  };

  Mappa.prototype.ridimensiona = function () {
    this.mappa.invalidateSize();
  };

  /* ---------------------------------------------------------------- pubblico */

  TF.mappa = {
    disponibile: function () {
      return typeof window.L !== "undefined";
    },
    crea: function (contenitore, stagione) {
      return new Mappa(contenitore, stagione);
    },
  };
})();
