/* Da dove arrivano i dati della pagina, e cosa si fa quando non arrivano.
 *
 * Perche' questo file esiste: la demo ha due sorgenti, non una, e servono a
 * due cose diverse. Il timelapse della stagione legge file precalcolati,
 * perche' rigiocare quattro mesi richiede minuti e non si fa mentre qualcuno
 * guarda. Il pannello dell'ordine parla con il servizio HTTP, perche' li' il
 * punto e' proprio vedere la catena lavorare su una frase nuova.
 *
 * La regola comune alle due strade e' che un guasto si dichiara. Un errore
 * mostrato e' informazione; un caricamento che non finisce mai e' un guasto
 * travestito da attesa. Per questo ogni chiamata ha una scadenza, e ogni
 * fallimento ha un messaggio in italiano da mettere a schermo.
 *
 * Qui sta anche l'unico punto in cui si conosce il formato del servizio. Il
 * contratto e' quello di src/tagliafuoco/api/: percorsi in italiano sotto
 * /api, campi JSON in inglese. Il resto della pagina lavora sulla forma dei
 * file precalcolati, quindi la risposta del servizio viene tradotta in quella
 * forma qui dentro e non altrove. Il vantaggio non e' estetico: il registro e
 * la mappa non devono sapere se stanno mostrando un esempio salvato o un
 * calcolo appena fatto, e infatti non lo sanno.
 */

(function () {
  "use strict";

  var TF = (window.TF = window.TF || {});

  /* ------------------------------------------------------------ memoria */

  /* Tutto cio' che la pagina ricorda sta qui dentro, cosi' l'elenco nella
   * pagina delle note si scrive leggendo una funzione sola. Ogni accesso e'
   * protetto: in una finestra anonima, o con i dati dei siti bloccati,
   * localStorage esiste ma lancia, e la pagina deve funzionare lo stesso. */
  var CHIAVI = {
    tema: "tagliafuoco.tema",
    servizio: "tagliafuoco.servizio",
  };

  var memoria = {
    chiavi: CHIAVI,
    leggi: function (chiave, difetto) {
      try {
        var v = window.localStorage.getItem(chiave);
        return v === null ? difetto : v;
      } catch (e) {
        return difetto;
      }
    },
    scrivi: function (chiave, valore) {
      try {
        window.localStorage.setItem(chiave, valore);
        return true;
      } catch (e) {
        return false;
      }
    },
    dimentica: function (chiave) {
      try {
        window.localStorage.removeItem(chiave);
      } catch (e) {
        /* niente da fare: la pagina non dipende da questo */
      }
    },
  };

  /* ------------------------------------------------------------ file locali */

  /* Il file JSON e' la forma canonica; lo shim .js contiene lo stesso
   * contenuto assegnato a una variabile globale. Serve al caso del doppio
   * clic: con schema `file:` il browser rifiuta fetch verso un file locale,
   * perche' l'origine e' opaca, mentre un normale tag script si carica. Non
   * sono due verita' diverse: li genera lo stesso script Python dallo stesso
   * dizionario. */
  function caricaScript(url) {
    return new Promise(function (risolvi, rifiuta) {
      var el = document.createElement("script");
      el.src = url;
      el.async = false;
      el.onload = function () {
        risolvi();
      };
      el.onerror = function () {
        rifiuta(new Error("non si carica " + url));
      };
      document.head.appendChild(el);
    });
  }

  function conScadenza(promessa, millisecondi, messaggio) {
    return new Promise(function (risolvi, rifiuta) {
      var scaduto = false;
      var timer = window.setTimeout(function () {
        scaduto = true;
        rifiuta(new Error(messaggio));
      }, millisecondi);
      promessa.then(
        function (v) {
          if (!scaduto) {
            window.clearTimeout(timer);
            risolvi(v);
          }
        },
        function (e) {
          if (!scaduto) {
            window.clearTimeout(timer);
            rifiuta(e);
          }
        }
      );
    });
  }

  function caricaBlocco(nomeFile, nomeGlobale) {
    var daFile = window.location.protocol === "file:";

    function viaScript() {
      if (window[nomeGlobale]) return Promise.resolve(window[nomeGlobale]);
      return caricaScript("data/" + nomeFile + ".js").then(function () {
        if (!window[nomeGlobale]) {
          throw new Error("data/" + nomeFile + ".js non ha definito " + nomeGlobale);
        }
        return window[nomeGlobale];
      });
    }

    if (daFile) return viaScript();

    return conScadenza(
      fetch("data/" + nomeFile + ".json", { cache: "no-cache" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status + " su " + nomeFile + ".json");
        return r.json();
      }),
      15000,
      "il file data/" + nomeFile + ".json non risponde"
    ).catch(viaScript);
  }

  /* ------------------------------------------------------------ servizio */

  /* Indirizzo del servizio HTTP. Di default si cerca sulla stessa origine
   * della pagina, che e' il caso di chi lancia uvicorn e apre la demo da li'.
   * Chi tiene la pagina su un server statico e il servizio su un'altra porta
   * lo dice una volta con ?api=... e la scelta resta memorizzata. */
  function baseServizio() {
    var parametri = new URLSearchParams(window.location.search);
    var dalUrl = parametri.get("api");
    if (dalUrl !== null) {
      var pulito = dalUrl.replace(/\/+$/, "");
      if (pulito === "") {
        memoria.dimentica(CHIAVI.servizio);
      } else {
        memoria.scrivi(CHIAVI.servizio, pulito);
        return pulito;
      }
    }
    var salvato = memoria.leggi(CHIAVI.servizio, "");
    if (salvato) return salvato;
    if (window.location.protocol === "file:") return "";
    return window.location.origin + "/api";
  }

  /* Una richiesta sola, con scadenza. Non si provano percorsi alternativi: il
   * servizio sta in questo stesso repository e il suo contratto e' scritto,
   * quindi un 404 non e' un nome da indovinare, e' un guasto da riferire. Due
   * richieste andate male per tentativo lascerebbero anche due errori nella
   * console del browser, che e' il posto dove si va a cercare i guasti veri. */
  function chiama(percorso, opzioni, millisecondi) {
    var base = baseServizio();
    if (!base) {
      return Promise.reject(new Error("nessun indirizzo per il servizio"));
    }
    var controllo =
      typeof AbortController === "function" ? new AbortController() : null;
    var conf = Object.assign({}, opzioni || {});
    if (controllo) conf.signal = controllo.signal;

    var timer = window.setTimeout(function () {
      if (controllo) controllo.abort();
    }, millisecondi);

    return fetch(base + percorso, conf).then(
      function (r) {
        window.clearTimeout(timer);
        if (!r.ok) {
          /* Il servizio risponde agli errori con un corpo di forma fissa
           * (error, message, detail). Il messaggio e' gia' scritto in
           * italiano per una persona, quindi si mostra quello invece di un
           * codice numerico. */
          return r
            .json()
            .catch(function () {
              return null;
            })
            .then(function (corpo) {
              var dettaglio = corpo && corpo.detail;
              var messaggio =
                (dettaglio && dettaglio.message) ||
                (corpo && corpo.message) ||
                /* Un 404 senza corpo non viene dal servizio: viene da
                 * qualcos'altro che sta a quell'indirizzo, di solito il
                 * server statico che serve questa stessa pagina. Scrivere
                 * "il servizio ha risposto 404" direbbe il falso, perche'
                 * farebbe credere che il servizio ci sia. */
                (r.status === 404
                  ? "404: li' c'e' qualcos'altro, di solito il server che " +
                    "serve questa pagina"
                  : "risposta " + r.status);
              throw new Error(messaggio);
            });
        }
        return r.json();
      },
      function (e) {
        window.clearTimeout(timer);
        if (e && e.name === "AbortError") {
          throw new Error("il servizio non ha risposto entro il tempo previsto");
        }
        throw e;
      }
    );
  }

  /* --------------------------------------------- traduzione della risposta */

  /* I file precalcolati usano chiavi italiane e minuti gia' arrotondati; il
   * servizio usa le chiavi del dominio, in inglese. Le tre funzioni che
   * seguono portano la risposta nella prima forma. Non si perde niente per
   * strada: quello che il servizio aggiunge (nome del mezzo, comune del
   * focolaio) viene tenuto, perche' risparmia al registro una ricerca. */

  function unDecimale(v) {
    return v === null || v === undefined ? null : Math.round(Number(v) * 10) / 10;
  }

  function adattaAssegnazione(a) {
    return {
      mezzo: a.vehicle_id,
      nome_mezzo: a.vehicle_name || "",
      focolaio: a.fire_id,
      comune: a.fire_comune || "",
      partenza: unDecimale(a.departure_minute),
      arrivo: unDecimale(a.arrival_minute),
      rilascio: unDecimale(a.release_minute),
      lanci: a.sorties,
      litri: a.water_delivered_liters,
      sorgente: a.water_source_id,
      costo: unDecimale(a.estimated_cost_eur),
      nota: a.note || "",
      da: null,
      a: null,
    };
  }

  /* Le metriche del piano calcolato ora sono quelle del validatore, non
   * quelle dichiarate dal risolutore. La differenza e' sostanziale e va
   * dichiarata a chi legge: il validatore esclude dal conto le assegnazioni
   * che ha giudicato non eseguibili, quindi i suoi numeri dicono quanto del
   * piano regge davvero, non quanto il piano prometteva. Il campo
   * `fonte_metriche` serve al registro per scriverlo sotto la tabella. */
  /* `assegnazioni_valide` non e' un dettaglio contabile: e' quello che
   * distingue "nessun mezzo e' mai arrivato" da "sono arrivati tutti subito".
   * Il validatore, quando non ha nessuna assegnazione eseguibile da misurare,
   * lascia i tempi di risposta a zero, perche' e' il valore neutro di una
   * somma vuota. Chi legge un riquadro con scritto "0,0 min" capisce il
   * contrario di quello che e' successo, quindi la pagina ha bisogno di
   * sapere quante assegnazioni hanno davvero contribuito. */
  function adattaMetriche(m) {
    if (!m) return null;
    return {
      focolai: m.fires_total,
      coperti: m.fires_covered,
      focolai_wui: m.fires_wui_total,
      coperti_wui: m.fires_wui_covered,
      litri_richiesti: Math.round(m.water_demanded_liters || 0),
      litri_erogati: Math.round(m.water_deliverable_liters || 0),
      risposta_media: unDecimale(m.mean_response_minutes),
      risposta_p90: unDecimale(m.p90_response_minutes),
      minuti_mezzo: unDecimale(m.total_vehicle_minutes),
      costo: unDecimale(m.total_cost_eur),
      mezzi_usati: m.vehicles_used,
      assegnazioni: m.assignments_total,
      assegnazioni_valide: m.assignments_feasible,
    };
  }

  function adattaPiano(piano, validazione) {
    if (!piano) return null;
    return {
      id: piano.plan_id,
      produttore: piano.producer,
      stato: piano.solver_status || "",
      secondi: piano.solve_seconds,
      obiettivo: unDecimale(piano.objective_value),
      assegnazioni: (piano.assignments || []).map(adattaAssegnazione),
      non_assegnati: piano.unassigned_fire_ids || [],
      note: piano.notes || [],
      metriche: adattaMetriche(validazione && validazione.metrics),
      fonte_metriche: "validatore",
    };
  }

  function adattaValidazione(v) {
    if (!v) return null;
    var tutte = v.violations || [];
    function diGravita(livello) {
      return tutte.filter(function (x) {
        return x.severity === livello;
      });
    }
    return {
      valido: !!v.is_valid,
      riassunto: v.summary_line || "",
      versione: v.validator_version || "",
      bloccanti: diGravita("blocking"),
      avvisi: diGravita("warning"),
      disaccordi: v.disagreements || [],
    };
  }

  /* Il focolaio nella forma che la mappa consuma. I due istanti diventano
   * minuti dall'origine della stagione, perche' e' cosi' che la barra del
   * tempo misura tutto: tenere due unita' diverse nella stessa pagina e' il
   * modo piu' rapido di sbagliare un confronto. */
  function adattaFocolaio(f, origineMs) {
    function minuti(iso, difetto) {
      if (!iso) return difetto;
      var t = new Date(iso).getTime();
      return isNaN(t) ? difetto : Math.round((t - origineMs) / 60000);
    }
    var t0 = minuti(f.first_seen, 0);
    return {
      id: f.fire_id,
      lat: f.centroid.lat,
      lon: f.centroid.lon,
      t0: t0,
      t1: minuti(f.last_seen, t0),
      frp: unDecimale(f.max_frp_mw),
      frp_tot: unDecimale(f.total_frp_mw),
      area: Math.round((f.area_km2 || 0) * 1000) / 1000,
      wui: f.is_wui ? 1 : 0,
      d_wui: unDecimale(f.distance_to_wui_km),
      comune: f.comune || "",
      prov: f.provincia || "",
      cop: f.land_cover,
      sev: f.severity,
      prio: f.base_priority,
      litri: f.water_demand_liters,
      prot: f.protected_area_name || null,
      det: f.detection_count,
    };
  }

  /* --------------------------------------------- interfaccia del servizio */

  var servizio = {
    indirizzo: function () {
      return baseServizio();
    },

    /* GET /api/salute. Dice anche in quale configurazione gira il servizio:
     * con o senza chiavi, quindi con il traduttore a regole o con il modello
     * linguistico. E' una differenza che chi guarda la demo deve vedere. */
    sonda: function () {
      return chiama("/salute", { method: "GET" }, 4000).then(function (r) {
        var chiavi = r.keys || {};
        return {
          versione: r.version || "",
          traduttore: chiavi.translator || "",
          sorgente: chiavi.detections_source || "",
          staticiPresenti: !!r.static_data_available,
          tettoSecondi: r.solver_seconds_cap,
        };
      });
    },

    /* POST /api/pianifica. Una chiamata sola percorre tutta la catena: ordine,
     * traduzione, scenario, risolutore, validatore indipendente, baseline. Il
     * limite di tempo e' basso di proposito: chi guarda la demo aspetta una
     * decina di secondi, non trenta, e il servizio riduce comunque al proprio
     * tetto qualunque valore piu' alto. */
    pianifica: function (ordine, istante, secondi) {
      var corpo = {
        order: ordine,
        scenario: {
          instant: istante || null,
          horizon_minutes: 240,
          max_fires: 30,
        },
        max_seconds: secondi || 8,
        deterministic: true,
        include_baseline: true,
      };
      return chiama(
        "/pianifica",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(corpo),
        },
        60000
      ).then(function (r) {
        var confronto = r.comparison || null;
        return {
          ordine: r.order || ordine,
          istante: (r.scenario && r.scenario.timestamp) || istante,
          orizzonte_min: (r.scenario && r.scenario.horizon_minutes) || 240,
          vincoli: (r.translation && r.translation.constraint_set) || {
            constraints: [],
          },
          traduttore: (r.translation && r.translation.translator) || "",
          focolaiGrezzi: r.fires || [],
          piano: adattaPiano(r.plan, r.validation),
          validazione: adattaValidazione(r.validation),
          baseline: confronto
            ? adattaPiano(confronto.baseline_plan, confronto.baseline_validation)
            : null,
          validazione_baseline: confronto
            ? adattaValidazione(confronto.baseline_validation)
            : null,
          secondi_usati: r.solver_seconds_used,
          note: r.notes || [],
        };
      });
    },

    /* La conversione dei focolai resta esposta perche' ha bisogno di sapere
     * da quale istante conta i minuti, e quello lo sa solo chi ha in mano i
     * dati della stagione. */
    adattaFocolai: function (grezzi, origineIso) {
      var origineMs = new Date(origineIso).getTime();
      return (grezzi || []).map(function (f) {
        return adattaFocolaio(f, origineMs);
      });
    },
  };

  /* ------------------------------------------------------------ pubblico */

  TF.dati = {
    memoria: memoria,
    servizio: servizio,
    carica: function () {
      return Promise.all([
        caricaBlocco("stagione", "TF_STAGIONE"),
        caricaBlocco("scenari", "TF_SCENARI"),
      ]).then(function (parti) {
        return { stagione: parti[0], scenari: parti[1] };
      });
    },
  };
})();
