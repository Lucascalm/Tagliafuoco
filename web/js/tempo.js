/* La barra del tempo: istogramma della stagione, scorrevole, play e pausa.
 *
 * Perche' lo scorrevole porta un istogramma: senza, sarebbe una barra vuota su
 * cui trascinare alla cieca. Con la densita' dei rilevamenti disegnata sopra
 * si vede dove sta il picco della stagione prima ancora di premere play, e la
 * barra smette di essere un comando e diventa anche un dato. E' l'unico
 * motivo per cui occupa quello spazio.
 *
 * Sull'animazione: si usa requestAnimationFrame e si avanza in funzione del
 * tempo trascorso, non del numero di fotogrammi. Un'animazione che somma una
 * costante a ogni fotogramma va al doppio della velocita' su uno schermo a
 * centoventi hertz e rallenta quando la macchina e' carica. In piu' il salto
 * temporale si limita: chi lascia la scheda e torna dopo dieci minuti
 * riceverebbe un primo delta di seicentomila millisecondi, e il timelapse
 * salterebbe alla fine della stagione.
 *
 * Con `prefers-reduced-motion` attiva il timelapse non interpola: avanza a
 * scatti da una finestra di sei ore alla successiva. La pagina resta
 * pienamente usabile, perche' il timelapse non e' l'unico modo di leggere i
 * dati: lo scorrevole permette di fermarsi su un istante e leggerlo.
 */

(function () {
  "use strict";

  var TF = (window.TF = window.TF || {});

  var MESI = [
    "gen",
    "feb",
    "mar",
    "apr",
    "mag",
    "giu",
    "lug",
    "ago",
    "set",
    "ott",
    "nov",
    "dic",
  ];

  /* Limite superiore al passo temporale di un fotogramma, in millisecondi. */
  var DELTA_MASSIMO_MS = 100;

  function dueCifre(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function formattaIstante(data) {
    return (
      dueCifre(data.getUTCDate()) +
      " " +
      MESI[data.getUTCMonth()] +
      " " +
      data.getUTCFullYear() +
      ", " +
      dueCifre(data.getUTCHours()) +
      ":" +
      dueCifre(data.getUTCMinutes())
    );
  }

  function Tempo(nodi, stagione, scenari, opzioni) {
    this.nodi = nodi;
    this.stagione = stagione;
    this.scenari = scenari || [];
    this.opzioni = opzioni || {};
    this.origine = new Date(stagione.origine);
    this.durata = stagione.durata_min || 0;
    this.minuto = 0;
    this.minutoGrezzo = 0;
    this.inCorso = false;
    this.ultimoStamp = null;
    this.velocita = 1440;
    this.passoScatto = (stagione.istogramma && stagione.istogramma.passo_ore * 60) || 360;

    var preferenza = window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)")
      : null;
    this.movimentoRidotto = preferenza ? preferenza.matches : false;
    var self = this;
    if (preferenza && preferenza.addEventListener) {
      preferenza.addEventListener("change", function (e) {
        self.movimentoRidotto = e.matches;
      });
    }

    this.barreIstogramma = "";
    this.larghezzaIstogramma = 0;

    this.preparaScorrevole();
    this.disegnaIstogramma();
    this.collega();
  }

  /* ---------------------------------------------------------------- disegno */

  /* Lo scorrevole si configura per primo e fuori dal disegno.
   *
   * Prima stava in fondo a `disegnaIstogramma`, dopo un ritorno anticipato
   * sul caso "nessun conteggio": una stagione senza rilevamenti lasciava
   * quindi il campo con il massimo di fabbrica, cento, su una stagione lunga
   * centosettantaquattromila minuti. Lo scorrevole avrebbe coperto i primi
   * cento minuti e basta, senza che niente lo dicesse. Il fondo della barra
   * e' un comando: deve valere anche quando non c'e' niente da disegnarci
   * sopra. */
  Tempo.prototype.preparaScorrevole = function () {
    this.nodi.input.max = String(this.durata);
    this.nodi.input.step = "30";
  };

  Tempo.prototype.disegnaIstogramma = function () {
    var isto = this.stagione.istogramma || { conteggi: [], passo_ore: 6 };
    var conteggi = isto.conteggi || [];
    this.barreIstogramma = "";
    if (!conteggi.length) {
      this.disegnaFinestre();
      return;
    }

    var massimo = Math.max.apply(null, conteggi) || 1;
    this.nodi.istogramma.setAttribute("viewBox", "0 0 " + conteggi.length + " 100");
    this.larghezzaIstogramma = conteggi.length;

    var parti = [];
    conteggi.forEach(function (c, i) {
      if (!c) return;
      var h = (c / massimo) * 88;
      parti.push(
        '<rect x="' +
          i +
          '" y="' +
          (100 - h).toFixed(2) +
          '" width="0.9" height="' +
          h.toFixed(2) +
          '" fill="var(--ardesia)" opacity="0.55"></rect>'
      );
    });

    this.barreIstogramma = parti.join("");
    this.disegnaFinestre();
  };

  /* I tratti delle finestre pianificate, ridisegnabili.
   *
   * Sono separati dalle barre perche' l'elenco delle finestre cresce: quando
   * il servizio calcola un piano nuovo nasce una finestra in piu', e senza un
   * ridisegno resterebbe l'unica senza il suo tratto sulla barra. Le barre
   * dei rilevamenti invece non cambiano mai, quindi si calcolano una volta e
   * si riusano.
   *
   * Il tratto e' una posizione, non un colore, quindi resta leggibile anche
   * in bianco e nero. Gli stessi istanti sono raggiungibili da tastiera con i
   * bottoni degli esempi. */
  Tempo.prototype.disegnaFinestre = function () {
    var isto = this.stagione.istogramma || { passo_ore: 6 };
    var passo = (isto.passo_ore || 6) * 60;
    var larghezza = this.larghezzaIstogramma || 0;
    var parti = [];

    this.scenari.forEach(function (s) {
      var i = s.minuto / passo;
      /* Una finestra fuori dalla stagione disegnata non ha un posto sulla
       * barra: meglio nessun tratto che un tratto appoggiato al bordo, che
       * indicherebbe un istante sbagliato. */
      if (!isFinite(i) || i < 0 || (larghezza && i > larghezza)) return;
      parti.push(
        '<rect x="' +
          (i - 0.2).toFixed(2) +
          '" y="0" width="1.3" height="100" fill="var(--inchiostro)" opacity="0.28"></rect>'
      );
    });

    this.nodi.istogramma.innerHTML = (this.barreIstogramma || "") + parti.join("");
  };

  Tempo.prototype.aggiornaCursore = function () {
    var frazione = this.durata ? this.minuto / this.durata : 0;
    this.nodi.cursore.style.left = (frazione * 100).toFixed(3) + "%";
  };

  /* ---------------------------------------------------------------- comandi */

  Tempo.prototype.collega = function () {
    var self = this;

    this.nodi.input.addEventListener("input", function () {
      self.ferma();
      self.vaiA(Number(self.nodi.input.value));
    });

    this.nodi.gioca.addEventListener("click", function () {
      if (self.inCorso) self.ferma();
      else self.avvia();
    });

    this.nodi.velocita.addEventListener("change", function () {
      self.velocita = Number(self.nodi.velocita.value);
    });
    this.velocita = Number(this.nodi.velocita.value) || 1440;

    /* Quando la scheda torna visibile si azzera il riferimento temporale: il
     * fotogramma successivo deve misurare il tempo da adesso, non dal momento
     * in cui la scheda e' stata lasciata. */
    document.addEventListener("visibilitychange", function () {
      self.ultimoStamp = null;
    });
  };

  Tempo.prototype.avvia = function () {
    if (this.inCorso) return;
    if (this.minuto >= this.durata) this.vaiA(0);
    this.inCorso = true;
    this.ultimoStamp = null;
    this.nodi.gioca.setAttribute("aria-pressed", "true");
    this.nodi.gioca.setAttribute("aria-label", "Metti in pausa il timelapse");
    this.nodi.icona.setAttribute("d", "M1.5 1 H4.5 V12 H1.5 Z M7.5 1 H10.5 V12 H7.5 Z");
    var self = this;
    this.richiesta = window.requestAnimationFrame(function (t) {
      self.passo(t);
    });
  };

  Tempo.prototype.ferma = function () {
    if (!this.inCorso) return;
    this.inCorso = false;
    if (this.richiesta) window.cancelAnimationFrame(this.richiesta);
    this.richiesta = null;
    this.nodi.gioca.setAttribute("aria-pressed", "false");
    this.nodi.gioca.setAttribute("aria-label", "Avvia il timelapse");
    this.nodi.icona.setAttribute("d", "M1 1 L11 6.5 L1 12 Z");
  };

  Tempo.prototype.passo = function (stamp) {
    if (!this.inCorso) return;
    var self = this;

    if (this.ultimoStamp === null) {
      this.ultimoStamp = stamp;
    }
    var delta = Math.min(DELTA_MASSIMO_MS, stamp - this.ultimoStamp);
    this.ultimoStamp = stamp;

    this.minutoGrezzo += (this.velocita * delta) / 1000;

    if (this.minutoGrezzo >= this.durata) {
      this.minutoGrezzo = this.durata;
      this.applica();
      this.ferma();
      return;
    }

    this.applica();
    this.richiesta = window.requestAnimationFrame(function (t) {
      self.passo(t);
    });
  };

  Tempo.prototype.applica = function () {
    /* Con il movimento ridotto il valore visibile e' quello arrotondato alla
     * finestra: il tempo continua a scorrere internamente, ma la pagina si
     * aggiorna solo quando cambia finestra. */
    var visibile = this.movimentoRidotto
      ? Math.floor(this.minutoGrezzo / this.passoScatto) * this.passoScatto
      : this.minutoGrezzo;

    if (visibile === this.minuto) return;
    this.minuto = visibile;
    this.nodi.input.value = String(Math.round(this.minuto));
    this.pubblica();
  };

  Tempo.prototype.vaiA = function (minuto) {
    this.minutoGrezzo = Math.max(0, Math.min(this.durata, minuto));
    this.minuto = this.movimentoRidotto
      ? Math.floor(this.minutoGrezzo / this.passoScatto) * this.passoScatto
      : this.minutoGrezzo;
    this.nodi.input.value = String(Math.round(this.minuto));
    this.pubblica();
  };

  Tempo.prototype.istante = function () {
    return new Date(this.origine.getTime() + this.minuto * 60000);
  };

  /* Chiamata da chi aggiunge una finestra all'elenco dopo il montaggio. */
  Tempo.prototype.ridisegnaFinestre = function () {
    this.disegnaFinestre();
  };

  Tempo.prototype.pubblica = function () {
    this.nodi.data.textContent = formattaIstante(this.istante());
    this.aggiornaCursore();
    if (this.opzioni.suIstante) this.opzioni.suIstante(this.minuto);
  };

  TF.tempo = {
    crea: function (nodi, stagione, scenari, opzioni) {
      return new Tempo(nodi, stagione, scenari, opzioni);
    },
    formatta: formattaIstante,
  };
})();
