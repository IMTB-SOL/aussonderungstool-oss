// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Ergebnisse-View — Bewertungsvorschläge groß und scanbar; Erschließung editierbar
// An das echte Backend angebunden: /api/bewertung/vorgaenge/{jobId} + /api/job/{jobId}/ergebnis
const { useState: useStateE, useEffect: useEffectE } = React;

// Mappt einen Backend-Vorgang (VorgangBewertung) auf die Render-Struktur des Designs.
function mappeVorgang(v) {
  const klass = ["A", "B", "V"].includes(v.klassifikation_final)
    ? v.klassifikation_final
    : (["A", "B", "V"].includes(v.klassifikation_vorschlag) ? v.klassifikation_vorschlag : "B");
  return {
    id: v.id,
    backendId: v.id,
    vorgangId: v.vorgang_id || null,
    az: v.aktenzeichen || "–",
    titel: v.betreff || "Ohne Betreff",
    klass,
    klassVorschlag: ["A", "B", "V"].includes(v.klassifikation_vorschlag) ? v.klassifikation_vorschlag : klass,
    klassFinal: ["A", "B", "V"].includes(v.klassifikation_final) ? v.klassifikation_final : null,
    overrideBegruendung: v.override_begruendung || "",
    score: v.finaler_score != null ? v.finaler_score : (v.rohscore != null ? v.rohscore : 0),
    dokumente: (v.dokumente || []).length,
    dokumenteListe: v.dokumente || [],
    zeitraum: "—",
    behoerde: "—",
    ki_text: v.llm_begruendung || "Keine KI-Begründung verfügbar.",
    tiefenpruefung_gemacht: !!v.tiefenpruefung_gemacht,
    indizien: (v.treffer || []).map((t) => ({
      label: t.bezeichnung || t.schluessel,
      erfuellt: !!t.erfuellt,
      punkte: t.punkte_beitrag || 0,
      typ: t.typ || "boost",
      evidenz: t.evidenz || "",
    })),
  };
}

function ErgebnisseView({ jobId, onNew, onHistorie }) {
  const [vorgaenge, setVorgaenge] = useStateE([]);
  const [erschErgebnis, setErschErgebnis] = useStateE(null); // Erschließungsdaten aus /ergebnis
  const [alleErgebnisse, setAlleErgebnisse] = useStateE(null); // alle Agenten-Ergebnisse
  const [laden, setLaden] = useStateE(true);
  const [fehler, setFehler] = useStateE(null);
  const [filter, setFilter] = useStateE("all");
  const [search, setSearch] = useStateE("");
  const [expanded, setExpanded] = useStateE(null);
  const [overrides, setOverrides] = useStateE({}); // {id: 'A'|'B'|'V'} — lokal noch nicht gespeicherte Auswahl
  const [erschId, setErschId] = useStateE(null); // currently editing erschliessung

  // Vorgänge + Erschließungsdaten laden
  useEffectE(() => {
    if (!jobId) { setLaden(false); return; }
    let aktiv = true;
    setLaden(true);
    setFehler(null);
    Promise.all([
      window.Api.get("bewertung/vorgaenge/" + jobId),
      window.Api.get("job/" + jobId + "/ergebnis").catch(() => null),
    ])
      .then(([roh, ergebnis]) => {
        if (!aktiv) return;
        const liste = (roh || []).map(mappeVorgang);
        setVorgaenge(liste);
        if (ergebnis && ergebnis.ergebnisse) {
          setErschErgebnis(ergebnis.ergebnisse.erschliessung || null);
          setAlleErgebnisse(ergebnis.ergebnisse);
        }
        if (liste.length > 0) setExpanded(liste[0].id);
      })
      .catch((e) => { if (aktiv) setFehler(e.message || "Vorgänge konnten nicht geladen werden."); })
      .finally(() => { if (aktiv) setLaden(false); });
    return () => { aktiv = false; };
  }, [jobId]);

  // Erschließung pro Einheit: Map einheit_id → Erschließungsfelder.
  const erschEinheiten = (erschErgebnis && erschErgebnis.einheiten) || [];
  const erschMap = {};
  erschEinheiten.forEach((e) => { if (e && e.einheit_id) erschMap[e.einheit_id] = e; });
  const erschVon = (v) => (v.vorgangId && erschMap[v.vorgangId]) || null;

  // Effektive Klassifikation: lokaler Override > finale Backend-Entscheidung > Vorschlag
  const klassOf = (v) => overrides[v.id] || v.klassFinal || v.klass;

  const counts = {
    all: vorgaenge.length,
    A: vorgaenge.filter((v) => klassOf(v) === "A").length,
    B: vorgaenge.filter((v) => klassOf(v) === "B").length,
    V: vorgaenge.filter((v) => klassOf(v) === "V").length,
    low: vorgaenge.filter((v) => !v.tiefenpruefung_gemacht && v.klass === "B").length,
  };

  const list = vorgaenge.filter((v) => {
    if (filter !== "all" && filter !== "low") {
      if (klassOf(v) !== filter) return false;
    }
    if (filter === "low" && !(!v.tiefenpruefung_gemacht && v.klass === "B")) return false;
    if (search && !(v.titel + " " + v.az).toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  // Override an das Backend senden
  const speichereEntscheidung = async (v, neueKlasse) => {
    const istOverride = neueKlasse !== v.klassVorschlag;
    let begruendung = "";
    if (istOverride) {
      begruendung = window.prompt(
        "Begründung für die abweichende Klassifikation (" + v.klassVorschlag + " → " + neueKlasse + "):",
        v.overrideBegruendung || ""
      );
      if (begruendung === null) return; // abgebrochen
      if (!begruendung.trim()) {
        window.alert("Bei abweichender Klassifikation ist eine Begründung Pflicht.");
        return;
      }
    }
    try {
      await window.Api.post("bewertung/vorgang/" + v.backendId + "/entscheidung", {
        klassifikation_final: neueKlasse,
        begruendung: begruendung || "",
      });
      setOverrides((o) => ({ ...o, [v.id]: neueKlasse }));
      setVorgaenge((vs) => vs.map((x) =>
        x.id === v.id ? { ...x, klassFinal: neueKlasse, overrideBegruendung: begruendung || "" } : x
      ));
    } catch (e) {
      window.alert("Speichern fehlgeschlagen: " + (e.message || e));
    }
  };

  // Tiefenprüfung anstoßen
  const starteTiefenpruefung = async (v) => {
    try {
      await window.Api.post("bewertung/vorgang/" + v.backendId + "/tiefenpruefung", {});
      // Vorgänge neu laden, damit Dokumente + Score aktualisiert erscheinen
      const roh = await window.Api.get("bewertung/vorgaenge/" + jobId);
      setVorgaenge((roh || []).map(mappeVorgang));
    } catch (e) {
      window.alert("Tiefenprüfung fehlgeschlagen: " + (e.message || e));
    }
  };

  if (laden) {
    return (
      <div className="view-enter">
        <h1 className="view-title">Ergebnisübersicht</h1>
        <p className="view-sub">Vorgänge werden geladen…</p>
      </div>
    );
  }
  if (fehler) {
    return (
      <div className="view-enter">
        <h1 className="view-title">Ergebnisübersicht</h1>
        <div className="badge badge--err" style={{ marginTop: 12 }}>
          <Icons.AlertTriangle size={11} /> {fehler}
        </div>
      </div>
    );
  }

  return (
    <div className="view-enter">
      <div className="flex justify-between items-start mb-4" style={{ gap: 16 }}>
        <div>
          <h1 className="view-title">Ergebnisübersicht</h1>
          <p className="view-sub" style={{ marginBottom: 0 }}>
            <strong style={{ color: "var(--imtb-anthrazit)" }}>{counts.all} Vorgänge</strong> ·
            <strong style={{ color: "var(--imtb-anthrazit)" }}> {counts.A} A</strong> ·
            <strong style={{ color: "var(--imtb-anthrazit)" }}> {counts.B} B</strong> ·
            <strong style={{ color: "var(--imtb-anthrazit)" }}> {counts.V} V</strong>
          </p>
        </div>
        <div className="btn-row">
          {jobId && (
            <a className="btn btn--primary" href={window.Api.downloadUrl(jobId)} download>
              <Icons.Download size={14} /> Geprüftes ZIP herunterladen
            </a>
          )}
          <button className="btn btn--secondary" onClick={() => window.print && window.print()}>
            <Icons.Printer size={13} /> Drucken
          </button>
          {onHistorie && (
            <button className="btn btn--secondary" onClick={onHistorie}>
              <Icons.History size={13} /> Historie
            </button>
          )}
          {onNew && (
            <button className="btn btn--secondary" onClick={onNew}>
              <Icons.Plus size={13} /> Neue Aussonderung
            </button>
          )}
        </div>
      </div>

      {/* Agenten-Statusübersicht: alle Prüfschritte konsolidiert */}
      {alleErgebnisse && <AgentStatusUebersicht ergebnisse={alleErgebnisse} />}

      {/* Stats-Strip */}
      <div className="stats-strip mb-4">
        <Stat label="Vorgänge" value={counts.all} />
        <Stat label="Archivieren" value={counts.A} klass="klass--a" />
        <Stat label="Bewerten" value={counts.B} klass="klass--b" />
        <Stat label="Vernichten" value={counts.V} klass="klass--v" />
      </div>

      {/* Toolbar */}
      <div className="toolbar">
        <div className="toolbar__search">
          <Icons.Search size={14} />
          <input
            className="input"
            placeholder="Nach Aktenzeichen oder Titel suchen…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="chip-group">
          {[
            { id: "all", label: "Alle", count: counts.all },
            { id: "A",   label: "A",    count: counts.A },
            { id: "B",   label: "B",    count: counts.B },
            { id: "V",   label: "V",    count: counts.V },
            { id: "low", label: "Tiefenprüfung offen", count: counts.low, icon: <Icons.AlertTriangle size={11} /> },
          ].map((c) => (
            <button
              key={c.id}
              className={"chip" + (filter === c.id ? " is-active" : "")}
              onClick={() => setFilter(c.id)}
            >
              {c.icon}
              {c.label}
              <span className="chip__count">{c.count}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Liste */}
      <div className="flex-col gap-3">
        {list.map((v) => (
          <VorgangCard
            key={v.id}
            vorgang={v}
            klassEff={klassOf(v)}
            ersch={erschVon(v)}
            expanded={expanded === v.id}
            readOnly={true}
            onToggle={() => setExpanded(expanded === v.id ? null : v.id)}
            onOverride={(k) => speichereEntscheidung(v, k)}
            onTiefenpruefung={() => starteTiefenpruefung(v)}
            onEditErsch={() => setErschId(v.id)}
          />
        ))}
        {list.length === 0 && (
          <div style={{ padding: 60, textAlign: "center", color: "var(--fg-muted)" }}>
            Keine Vorgänge entsprechen dem Filter.
          </div>
        )}
      </div>

      {/* Erschließung wird je Einheit in den Karten oben angezeigt/bearbeitet. */}

      {/* Erschließungs-Drawer — bearbeitet die Erschließung EINER Einheit. */}
      {erschId && (() => {
        const vg = vorgaenge.find((v) => v.id === erschId);
        if (!vg) return null;
        return (
          <ErschliessungDrawer
            jobId={jobId}
            vorgang={vg}
            einheitId={vg.vorgangId}
            ersch={erschVon(vg)}
            onClose={() => setErschId(null)}
            onSaved={(neuEinheit) => setErschErgebnis((prev) => {
              const ein = ((prev && prev.einheiten) || []).slice();
              const idx = ein.findIndex((e) => e.einheit_id === vg.vorgangId);
              const eintrag = { einheit_id: vg.vorgangId, betreff: vg.titel, ...neuEinheit };
              if (idx >= 0) ein[idx] = { ...ein[idx], ...eintrag };
              else ein.push(eintrag);
              return { ...(prev || {}), einheiten: ein };
            })}
          />
        );
      })()}
    </div>
  );
}

function Stat({ label, value, sub, klass }) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {klass && <span className={"klass " + klass} style={{ fontSize: 13, padding: "2px 8px" }}>
          {klass.endsWith("a") ? "A" : klass.endsWith("b") ? "B" : "V"}
        </span>}
        {value}
      </div>
      {sub && <div className="stat__sub">{sub}</div>}
    </div>
  );
}

// Konsolidierte Statusübersicht aller Prüfschritte (Gesamtansicht am Ende).
const AGENT_TITEL = {
  formal: "Formale Prüfung",
  format: "Formatprüfung",
  inhalt: "Inhaltsvergleich",
  bewertung: "Archivbewertung",
  erschliessung: "Erschließung",
};

function agentZusammenfassung(agent, erg) {
  if (!erg) return "Nicht ausgeführt.";
  switch (agent) {
    case "formal": {
      const v = erg.xdomea_version ? `XDOMEA ${erg.xdomea_version}` : "Version unbekannt";
      const ra = erg.referenz_abgleich || {};
      const fehlend = Object.values(ra).reduce((n, r) => n + ((r && r.fehlend) || []).length, 0);
      if (erg.status === "ok") return `${v} — Struktur und Vollständigkeit geprüft.`;
      return `${v} — ${fehlend > 0 ? fehlend + " fehlende Schriftstücke" : "Hinweise"} (Details im Schritt-Protokoll).`;
    }
    case "format": {
      const d = erg.dateien || [];
      const ok = d.filter((x) => x.archivfaehig).length;
      return d.length ? `${ok} von ${d.length} Dateien langzeitarchivfähig.` : "Formate geprüft.";
    }
    case "inhalt": {
      const p = erg.anzahl_paare != null ? erg.anzahl_paare : (erg.paare || []).length;
      return p ? `${p} Dokumentpaar(e) verglichen.` : "Keine Vergleichspaare gefunden.";
    }
    case "bewertung":
      return `${erg.anzahl_vorgaenge != null ? erg.anzahl_vorgaenge : "—"} Bewertungseinheit(en) bewertet.`;
    case "erschliessung":
      return erg.status === "ok" ? "Erschließung erstellt." : "Erschließung mit Hinweisen.";
    default:
      return erg.status || "—";
  }
}

// Erschließungsergebnis kompakt anzeigen (Teil der Endübersicht) + Bearbeiten.
function ErschliessungUebersicht({ ersch, vorhanden, onEdit }) {
  const alsText = (w) => Array.isArray(w) ? w.join(", ") : (w || "");
  const e = ersch || {};
  const hatInhalt = e.titel || e.inhaltsangabe || (e.schlagworte && e.schlagworte.length);
  const zeilen = [
    ["Titel", e.titel],
    ["Inhaltsangabe", e.inhaltsangabe],
    ["Schlagworte", alsText(e.schlagworte)],
    ["Personen", alsText(e.personen)],
    ["Zeitraum", e.zeitraum],
    ["Aktenzeichen", e.aktenzeichen],
  ].filter(([, w]) => w);
  return (
    <div className="surface mb-4" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px", borderBottom: "1px solid var(--rule)" }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--fg-strong)" }}>Erschließung</span>
        <span className="ki-marker"><Icons.Sparkles size={9} /> KI</span>
        <button className="btn btn--ghost btn--sm" style={{ marginLeft: "auto" }} onClick={onEdit}>
          <Icons.Edit size={12} /> Bearbeiten
        </button>
      </div>
      {hatInhalt ? (
        <dl className="meta-grid" style={{ padding: "12px 14px", margin: 0 }}>
          {zeilen.map(([label, wert]) => (
            <React.Fragment key={label}>
              <dt>{label}</dt>
              <dd className={label === "Aktenzeichen" ? "mono" : ""} style={{ whiteSpace: "pre-line" }}>{wert}</dd>
            </React.Fragment>
          ))}
        </dl>
      ) : (
        <div className="text-sm text-muted" style={{ padding: "12px 14px" }}>
          {vorhanden
            ? `Noch keine Erschließungsdaten erfasst — über „Bearbeiten“ ergänzen.`
            : "Erschließungs-Agent wurde nicht ausgeführt."}
        </div>
      )}
    </div>
  );
}

function AgentStatusUebersicht({ ergebnisse }) {
  const reihenfolge = ["formal", "format", "inhalt", "bewertung", "erschliessung"];
  const vorhanden = reihenfolge.filter((a) => ergebnisse[a]);
  if (vorhanden.length === 0) return null;
  return (
    <div className="surface mb-4" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--rule)", fontWeight: 600, fontSize: 13, color: "var(--fg-strong)" }}>
        Prüfschritte im Überblick
      </div>
      {vorhanden.map((a) => {
        const erg = ergebnisse[a];
        const status = (erg && erg.status) || "ausstehend";
        const label = status === "ok" ? "Bestanden" : status === "fehler" ? "Mit Befund" : status === "warnung" ? "Hinweise" : "Ausstehend";
        const punktFarbe = status === "ok" ? "var(--ok)" : status === "fehler" ? "var(--err)" : status === "warnung" ? "var(--warn)" : "var(--fg-muted)";
        return (
          <div key={a} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", borderBottom: "1px solid var(--rule)" }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: punktFarbe, flexShrink: 0, display: "inline-block" }}></span>
            <strong style={{ fontSize: 13, minWidth: 140, color: "var(--imtb-anthrazit)" }}>{AGENT_TITEL[a] || a}</strong>
            <span className={"badge " + (status === "ok" ? "badge--ok" : status === "fehler" ? "badge--err" : "badge--warn")} style={{ flexShrink: 0 }}>{label}</span>
            <span className="text-xs text-muted" style={{ flex: 1 }}>{agentZusammenfassung(a, erg)}</span>
          </div>
        );
      })}
    </div>
  );
}

function VorgangCard({ vorgang: v, klassEff, ersch, expanded, onToggle, onOverride, onTiefenpruefung, onEditErsch, readOnly }) {
  const wasOverridden = klassEff !== v.klassVorschlag;
  const erschSchlagworte = ersch && Array.isArray(ersch.schlagworte) ? ersch.schlagworte : [];
  return (
    <div className="vorgang-card">
      <div className="vorgang-card__head" onClick={onToggle}>
        <div className="vorgang-card__klass">
          <span className={"klass klass--lg klass--" + klassEff.toLowerCase()}>{klassEff}</span>
        </div>
        <div>
          <h3 className="vorgang-card__title">
            {v.titel}
            <span className="vorgang-card__az">{v.az}</span>
            {wasOverridden && (
              <span className="badge" style={{ background: "var(--accent)", color: "var(--imtb-weiss)" }}>
                <Icons.Edit size={9} /> Manuell geändert
              </span>
            )}
            {!v.tiefenpruefung_gemacht && v.klass === "B" && (
              <span className="badge badge--warn">
                <Icons.AlertTriangle size={10} /> Tiefenprüfung empfohlen
              </span>
            )}
          </h3>
          <div className="vorgang-card__sub">
            <span><Icons.File size={11} /> {v.dokumente} Dokumente</span>
            {v.tiefenpruefung_gemacht && (
              <span><Icons.CheckCircle size={11} /> Tiefenprüfung erfolgt</span>
            )}
          </div>
        </div>
        <div className="vorgang-card__score">
          <div className="vorgang-card__score-num">{v.score}</div>
          <div className="vorgang-card__score-label">Score</div>
          <Icons.ChevronDown size={14} style={{ color: "var(--fg-muted)", transform: expanded ? "rotate(180deg)" : "none", transition: "transform .2s" }} />
        </div>
      </div>
      {expanded && (
        <div className="vorgang-card__expand" onClick={(e) => e.stopPropagation()}>
          <div>
            <h4 className="vorgang-card__expand-h">
              {v.tiefenpruefung_gemacht ? "Sammelbegründung (Tiefenprüfung)" : "KI-Begründung"}
            </h4>
            <div className="ki-vorschlag" style={{ whiteSpace: "pre-line" }}>
              <Icons.Sparkles size={11} style={{ color: "var(--accent)", marginRight: 4, verticalAlign: -1 }} />
              {v.ki_text}
            </div>

            {v.overrideBegruendung && (
              <>
                <h4 className="vorgang-card__expand-h" style={{ marginTop: 16 }}>Begründung der Änderung</h4>
                <div className="text-sm" style={{ color: "var(--imtb-anthrazit)", lineHeight: 1.55 }}>
                  {v.overrideBegruendung}
                </div>
              </>
            )}

            <h4 className="vorgang-card__expand-h" style={{ marginTop: 16 }}>
              Erschließung
              <span className="ki-marker" style={{ marginLeft: 6 }}><Icons.Sparkles size={9} /> KI</span>
            </h4>
            {ersch && (ersch.titel || ersch.inhaltsangabe || erschSchlagworte.length) ? (
              <dl className="meta-grid" style={{ margin: 0 }}>
                {ersch.titel && <><dt>Titel</dt><dd>{ersch.titel}</dd></>}
                {ersch.inhaltsangabe && <><dt>Inhaltsangabe</dt><dd style={{ whiteSpace: "pre-line" }}>{ersch.inhaltsangabe}</dd></>}
                {erschSchlagworte.length > 0 && <><dt>Schlagworte</dt><dd>{erschSchlagworte.join(", ")}</dd></>}
                {ersch.zeitraum && <><dt>Zeitraum</dt><dd className="mono">{ersch.zeitraum}</dd></>}
              </dl>
            ) : (
              <div className="text-sm text-muted">Noch keine Erschließung für diese Einheit.</div>
            )}
            <button className="btn btn--ghost btn--sm" style={{ marginTop: 10, marginLeft: -8 }} onClick={onEditErsch}>
              <Icons.Edit size={12} /> Erschließung bearbeiten
            </button>
          </div>

          <div>
            <h4 className="vorgang-card__expand-h">Indizien-Score</h4>
            <ul className="indizien-list">
              {v.indizien.map((ind, i) => (
                <li key={i} style={{ opacity: ind.erfuellt ? 1 : 0.6 }}>
                  <span className={"ind-bullet " + (ind.erfuellt ? "pos" : "neu")}>
                    {ind.erfuellt ? <Icons.Check size={9} /> : null}
                  </span>
                  <span title={ind.evidenz || (ind.typ === "ko_deckel" ? "KO-Deckel" : ind.typ === "ok_boden" ? "OK-Boden" : "Boost")}>
                    {ind.label}
                    {ind.typ === "ok_boden" && ind.erfuellt && (
                      <span className="text-xs text-muted" style={{ marginLeft: 6 }}>· OK-Boden → A</span>
                    )}
                    {ind.typ === "ko_deckel" && !ind.erfuellt && (
                      <span className="text-xs" style={{ marginLeft: 6, color: "var(--warn)" }}>· KO-Deckel → max. B</span>
                    )}
                  </span>
                  <span className="ind-val" style={{
                    color: ind.erfuellt ? "var(--ok)" : "var(--fg-muted)"
                  }}>
                    {ind.erfuellt ? (ind.punkte >= 0 ? "+" : "") + ind.punkte : "—"}
                  </span>
                </li>
              ))}
              {v.indizien.length === 0 && (
                <li className="text-sm text-muted">Keine Indizien-Treffer.</li>
              )}
            </ul>

            {readOnly ? (
              <div className="override-row" style={{ alignItems: "center" }}>
                <span>Klassifikation:</span>
                <span className={"klass klass--" + klassEff.toLowerCase()} style={{ fontSize: 12, padding: "2px 8px", fontWeight: 700 }}>{klassEff}</span>
                {wasOverridden
                  ? <span className="text-xs text-muted">manuell geändert (Vorschlag war {v.klassVorschlag})</span>
                  : <span className="text-xs text-muted">wie KI-Vorschlag</span>}
                <span className="text-xs text-muted" style={{ marginLeft: "auto" }}>
                  Änderungen am Bewertungsschritt
                </span>
              </div>
            ) : (
              <>
                <div className="override-row">
                  <span>Bewertung ändern:</span>
                  {["A","B","V"].map((k) => (
                    <button
                      key={k}
                      className={"chip" + (klassEff === k ? " is-active" : "")}
                      style={{ fontWeight: 700 }}
                      onClick={() => onOverride(k)}
                    >
                      {k}
                    </button>
                  ))}
                  {wasOverridden && (
                    <button className="btn btn--ghost btn--sm" onClick={() => onOverride(v.klassVorschlag)} style={{ marginLeft: "auto" }}>
                      <Icons.Refresh size={11} /> Zurücksetzen
                    </button>
                  )}
                </div>

                {!v.tiefenpruefung_gemacht && (
                  <div style={{ marginTop: 10 }}>
                    <button className="btn btn--secondary btn--sm" onClick={onTiefenpruefung}>
                      <Icons.Search size={12} /> Tiefer prüfen
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ErschliessungDrawer({ jobId, vorgang: v, einheitId, ersch, onClose, onSaved }) {
  // Erschließung EINER Einheit (titel, inhaltsangabe, schlagworte[], …) auf die
  // Formularfelder mappen.
  const e = ersch || {};
  const alsText = (wert) => Array.isArray(wert) ? wert.join(", ") : (wert || "");
  const [form, setForm] = useStateE({
    titel: e.titel || v.titel || "",
    regest: e.inhaltsangabe || "",
    schlagworte: alsText(e.schlagworte),
    personen: alsText(e.personen),
    zeitraum: e.zeitraum || "",
    aktenzeichen: e.aktenzeichen || v.az || "",
    bezuege: alsText(e.bezuege),
  });
  const [savedAt, setSavedAt] = useStateE(null);
  const [speichern, setSpeichern] = useStateE(false);
  const [fehler, setFehler] = useStateE(null);

  const save = async () => {
    setSpeichern(true);
    setFehler(null);
    try {
      const payload = {
        titel: form.titel,
        inhaltsangabe: form.regest,
        schlagworte: form.schlagworte.split(",").map((s) => s.trim()).filter(Boolean),
        personen: form.personen.split(",").map((s) => s.trim()).filter(Boolean),
        zeitraum: form.zeitraum,
        aktenzeichen: form.aktenzeichen,
        bezuege: form.bezuege,
      };
      // Welche Einheit bearbeitet wird (Erschließung pro Ebene).
      const antwort = await window.Api.post("job/" + jobId + "/erschliessung", einheitId ? { ...payload, einheit_id: einheitId } : payload);
      setSavedAt(new Date().toLocaleTimeString("de-DE").slice(0, 5));
      if (onSaved) onSaved(payload);
      if (antwort && antwort.validierung_warnung) {
        setFehler(antwort.validierung_warnung);
      } else {
        setTimeout(() => setSavedAt(null), 3000);
      }
    } catch (err) {
      setFehler(err.message || "Speichern fehlgeschlagen.");
    } finally {
      setSpeichern(false);
    }
  };

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 40,
      background: "rgba(33, 36, 52, 0.32)",
      display: "flex", justifyContent: "flex-end",
    }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 560, maxWidth: "100%", height: "100%",
        background: "var(--surface)",
        boxShadow: "-12px 0 32px rgba(33,36,52,.16)",
        display: "flex", flexDirection: "column",
        animation: "drawerIn .26s cubic-bezier(.2,0,0,1)",
      }}>
        <div style={{
          padding: "18px 22px",
          borderBottom: "1px solid var(--rule)",
          display: "flex", justifyContent: "space-between", alignItems: "flex-start",
        }}>
          <div>
            <div className="text-xs text-muted" style={{ letterSpacing: ".06em", textTransform: "uppercase", fontWeight: 600 }}>
              Erschließung
            </div>
            <h3 style={{
              margin: "4px 0 0",
              fontFamily: "var(--font-display)",
              fontSize: 18, fontWeight: 700, color: "var(--fg-strong)",
            }}>{v.titel}</h3>
            <div className="text-xs text-muted mt-1 mono">{v.az}</div>
          </div>
          <button className="btn btn--ghost btn--icon" onClick={onClose}><Icons.X size={16} /></button>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: "22px" }}>
          <div className="text-xs" style={{
            background: "var(--page-deep)",
            padding: "8px 10px", borderRadius: 6,
            display: "flex", alignItems: "center", gap: 6,
            color: "var(--fg-muted)", marginBottom: 16,
          }}>
            <Icons.Sparkles size={12} style={{ color: "var(--accent)" }} />
            Automatisch erstellt — bitte prüfen und ggf. anpassen.
          </div>
          <div className="ersch-grid">
            <div className="field col-12">
              <label className="field__label">
                Titel
                <span className="ki-marker" style={{ marginLeft: 6 }}><Icons.Sparkles size={9} /> KI</span>
              </label>
              <input className="input" value={form.titel} onChange={(e) => setForm({ ...form, titel: e.target.value })} />
            </div>
            <div className="field col-12">
              <label className="field__label">
                Inhaltsangabe (Regest)
                <span className="ki-marker" style={{ marginLeft: 6 }}><Icons.Sparkles size={9} /> KI</span>
              </label>
              <textarea className="textarea" rows="4" value={form.regest} onChange={(e) => setForm({ ...form, regest: e.target.value })}></textarea>
            </div>
            <div className="field col-12">
              <label className="field__label">Schlagworte</label>
              <input className="input" value={form.schlagworte} onChange={(e) => setForm({ ...form, schlagworte: e.target.value })} />
              <span className="field__hint">Komma-getrennt; 5–10 Begriffe empfohlen.</span>
            </div>
            <div className="field col-12">
              <label className="field__label">Beteiligte Personen</label>
              <input className="input" value={form.personen} onChange={(e) => setForm({ ...form, personen: e.target.value })} />
            </div>
            <div className="field col-6">
              <label className="field__label">Zeitraum</label>
              <input className="input" value={form.zeitraum} onChange={(e) => setForm({ ...form, zeitraum: e.target.value })} />
            </div>
            <div className="field col-6">
              <label className="field__label">Aktenzeichen</label>
              <input className="input mono" value={form.aktenzeichen} onChange={(e) => setForm({ ...form, aktenzeichen: e.target.value })} />
            </div>
            <div className="field col-12">
              <label className="field__label">Bezüge / Folgeakten</label>
              <input className="input mono" value={form.bezuege} onChange={(e) => setForm({ ...form, bezuege: e.target.value })} />
            </div>
          </div>
          {fehler && (
            <div className="badge badge--warn" style={{ marginTop: 14 }}>
              <Icons.AlertTriangle size={11} /> {fehler}
            </div>
          )}
        </div>

        <div style={{
          padding: "14px 22px",
          borderTop: "1px solid var(--rule)",
          background: "#FBFAF7",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span className="text-xs text-muted">
            {savedAt ? <><Icons.Check size={11} style={{ color: "var(--ok)" }} /> Gespeichert um {savedAt}</> : "Änderungen werden ins XDOMEA-XML übernommen."}
          </span>
          <div className="btn-row">
            <button className="btn btn--secondary" onClick={onClose}>Schließen</button>
            <button className="btn btn--primary" onClick={save} disabled={speichern}>
              <Icons.Save size={13} /> {speichern ? "Speichert…" : "Speichern"}
            </button>
          </div>
        </div>
      </div>
      <style>{`@keyframes drawerIn { from { transform: translateX(40px); opacity: .4; } to { transform: translateX(0); opacity: 1; } }`}</style>
    </div>
  );
}

Object.assign(window, { ErgebnisseView });
