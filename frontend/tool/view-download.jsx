// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Download- / Abschluss-View — Prüfbericht + Downloads
// An das echte Backend angebunden: /api/job/{jobId}/ergebnis + /audit + /download
const { useState: useStateD, useEffect: useEffectD } = React;

// Anzeigenamen + Reihenfolge der Prüfschritte
const AGENT_REIHENFOLGE = [
  { id: "bewertung", name: "Archivbewertung", hint: "Indizien + KI" },
  { id: "formal", name: "Formale Prüfung", hint: "XDOMEA · XSD" },
  { id: "format", name: "Formatprüfung", hint: "PDF/A · veraPDF" },
  { id: "inhalt", name: "Inhaltsvergleich", hint: "DOCX vs. PDF" },
  { id: "erschliessung", name: "Erschließung", hint: "Metadaten in XDOMEA" },
];

// Versucht aus einem Agentenergebnis ein Status-Badge abzuleiten.
function ergebnisBadge(id, ergebnis) {
  if (!ergebnis) {
    return { cls: "badge--info", label: "Übersprungen", icon: "Info" };
  }
  // Häufige Statusfelder in den Agentenergebnissen prüfen
  const status = (ergebnis.status || ergebnis.gesamtstatus || ergebnis.ergebnis || "").toString().toLowerCase();
  if (status.includes("fehler") || status.includes("invalid") || ergebnis.fehler) {
    return { cls: "badge--err", label: "Fehler", icon: "AlertTriangle" };
  }
  if (status.includes("warn") || status.includes("hinweis") || status.includes("bedingt")) {
    return { cls: "badge--warn", label: "Mit Hinweisen", icon: "AlertTriangle" };
  }
  return { cls: "badge--ok", label: "Abgeschlossen", icon: "Check" };
}

// Kurze Bemerkung aus dem Agentenergebnis ableiten.
function ergebnisBemerkung(ergebnis) {
  if (!ergebnis) return "Prüfschritt wurde nicht ausgeführt.";
  return (
    ergebnis.zusammenfassung ||
    ergebnis.begruendung ||
    ergebnis.meldung ||
    ergebnis.message ||
    "Abgeschlossen."
  );
}

function DownloadView({ jobId, onNew, onHistorie }) {
  const [ergebnis, setErgebnis] = useStateD(null);
  const [vorgaenge, setVorgaenge] = useStateD([]);
  const [audit, setAudit] = useStateD([]);
  const [laden, setLaden] = useStateD(true);
  const [fehler, setFehler] = useStateD(null);
  const heute = new Date().toLocaleDateString("de-DE", { day: "2-digit", month: "long", year: "numeric" });

  useEffectD(() => {
    if (!jobId) { setLaden(false); return; }
    let aktiv = true;
    setLaden(true);
    setFehler(null);
    Promise.all([
      window.Api.get("job/" + jobId + "/ergebnis"),
      window.Api.get("bewertung/vorgaenge/" + jobId).catch(() => []),
      window.Api.get("job/" + jobId + "/audit").catch(() => ({ audit_log: [] })),
    ])
      .then(([erg, vorg, aud]) => {
        if (!aktiv) return;
        setErgebnis(erg);
        setVorgaenge(vorg || []);
        setAudit((aud && aud.audit_log) || []);
      })
      .catch((e) => { if (aktiv) setFehler(e.message || "Prüfbericht konnte nicht geladen werden."); })
      .finally(() => { if (aktiv) setLaden(false); });
    return () => { aktiv = false; };
  }, [jobId]);

  const klassOf = (v) => {
    const k = v.klassifikation_final || v.klassifikation_vorschlag;
    return ["A", "B", "V"].includes(k) ? k : "?";
  };
  const counts = {
    A: vorgaenge.filter((v) => klassOf(v) === "A").length,
    B: vorgaenge.filter((v) => klassOf(v) === "B").length,
    V: vorgaenge.filter((v) => klassOf(v) === "V").length,
  };
  const dokAnzahl = vorgaenge.reduce((s, v) => s + ((v.dokumente || []).length), 0);

  const agentErgebnisse = (ergebnis && ergebnis.ergebnisse) || {};
  const anmerkungen = (ergebnis && ergebnis.anmerkungen) || {};
  const ersch = agentErgebnisse.erschliessung || null;

  if (laden) {
    return (
      <div className="view-enter">
        <h1 className="view-title">Prüfung abgeschlossen</h1>
        <p className="view-sub">Prüfbericht wird geladen…</p>
      </div>
    );
  }
  if (fehler) {
    return (
      <div className="view-enter">
        <h1 className="view-title">Prüfung abgeschlossen</h1>
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
          <h1 className="view-title">Prüfung abgeschlossen</h1>
          <p className="view-sub" style={{ marginBottom: 0 }}>
            Archiv mit aktualisierten Metadaten bereit.
          </p>
        </div>
        <div className="btn-row">
          <button className="btn btn--secondary" onClick={() => window.print && window.print()}>
            <Icons.Printer size={13} /> Drucken
          </button>
          <button className="btn btn--secondary" onClick={onHistorie}>
            <Icons.History size={13} /> Historie
          </button>
          <button className="btn btn--secondary" onClick={onNew}>
            <Icons.Plus size={13} /> Neue Aussonderung
          </button>
        </div>
      </div>

      {/* Download-Strip */}
      <div className="surface" style={{ padding: 22, marginBottom: 18, display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 22, alignItems: "center" }}>
        <div style={{
          width: 64, height: 64,
          background: "var(--imtb-nachtblau)",
          color: "var(--imtb-weiss)",
          borderRadius: 10,
          display: "grid", placeItems: "center",
        }}>
          <Icons.Archive size={28} />
        </div>
        <div>
          <div style={{ fontSize: 11, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--fg-muted)", fontWeight: 600, marginBottom: 3 }}>
            Bereit zum Download
          </div>
          <div style={{
            fontFamily: "var(--font-display)",
            fontSize: 20, fontWeight: 700, color: "var(--fg-strong)",
            letterSpacing: "-.01em",
          }}>
            Geprüftes Archiv (ZIP)
          </div>
          <div className="text-sm text-muted mt-1" style={{ display: "flex", gap: 16 }}>
            <span>{vorgaenge.length} Vorgänge</span>
            <span>{dokAnzahl} Dokumente</span>
            <span>XDOMEA</span>
          </div>
        </div>
        {jobId ? (
          <a className="btn btn--primary btn--lg" href={window.Api.downloadUrl(jobId)} download>
            <Icons.Download size={14} /> ZIP herunterladen
          </a>
        ) : (
          <button className="btn btn--primary btn--lg" disabled>
            <Icons.Download size={14} /> ZIP herunterladen
          </button>
        )}
      </div>

      {/* Prüfbericht */}
      <article className="report">
        <header className="report__head">
          <div>
            <div className="text-xs text-muted" style={{ letterSpacing: ".08em", textTransform: "uppercase", fontWeight: 600, marginBottom: 4 }}>
              Prüfbericht
            </div>
            <h1 className="report__title">Aussonderung — Prüfbericht</h1>
            <div className="text-sm text-muted mt-1">
              {vorgaenge.length} Vorgänge • {dokAnzahl} Dokumente
            </div>
          </div>
          <div className="report__meta">
            <div><strong style={{ color: "var(--imtb-anthrazit)" }}>Job</strong> {jobId || "–"}</div>
            <div>Datum: {heute}</div>
            <div>Status: {(ergebnis && ergebnis.status) || "abgeschlossen"}</div>
          </div>
        </header>

        <section className="report__section">
          <h2 className="report__section-title">1 — Prüfschritte</h2>
          <table className="report__table">
            <thead><tr><th>Schritt</th><th>Ergebnis</th><th>Bemerkung</th></tr></thead>
            <tbody>
              {AGENT_REIHENFOLGE.map((a) => {
                const erg = agentErgebnisse[a.id];
                const badge = ergebnisBadge(a.id, erg);
                const BadgeIco = window.Icons[badge.icon] || Icons.Info;
                const bemerkung = anmerkungen[a.id] || ergebnisBemerkung(erg);
                return (
                  <tr key={a.id}>
                    <td><strong>{a.name}</strong><div className="text-xs text-muted">{a.hint}</div></td>
                    <td><span className={"badge " + badge.cls}><BadgeIco size={10} /> {badge.label}</span></td>
                    <td>{bemerkung}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="report__section">
          <h2 className="report__section-title">2 — Bewertungsentscheidungen</h2>
          <table className="report__table">
            <thead>
              <tr>
                <th style={{ width: 56 }}>Klass.</th>
                <th>Vorgang</th>
                <th>Aktenzeichen</th>
                <th>Score</th>
                <th>Tiefenprüfung</th>
              </tr>
            </thead>
            <tbody>
              {vorgaenge.map((v) => {
                const k = klassOf(v);
                return (
                  <tr key={v.id}>
                    <td>
                      <span className={"klass klass--" + k.toLowerCase()} style={{ fontSize: 13 }}>{k}</span>
                    </td>
                    <td>
                      <strong>{v.betreff || "Ohne Betreff"}</strong>
                      {v.klassifikation_final && v.klassifikation_final !== v.klassifikation_vorschlag && (
                        <div className="text-xs text-muted">Manuell geändert (Vorschlag: {v.klassifikation_vorschlag})</div>
                      )}
                    </td>
                    <td className="mono text-sm">{v.aktenzeichen || "–"}</td>
                    <td className="mono text-sm">{v.finaler_score != null ? v.finaler_score : "–"}</td>
                    <td>
                      <span className={"badge " + (v.tiefenpruefung_gemacht ? "badge--ok" : "badge--info")}>
                        {v.tiefenpruefung_gemacht ? "erfolgt" : "—"}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {vorgaenge.length === 0 && (
                <tr><td colSpan={5} className="text-muted">Keine Bewertungsvorgänge vorhanden.</td></tr>
              )}
            </tbody>
          </table>
        </section>

        {ersch && ersch.titel && (
          <section className="report__section">
            <h2 className="report__section-title">3 — Erschließung</h2>
            <table className="report__table">
              <tbody>
                <tr><td style={{ width: 160 }}><strong>Titel</strong></td><td>{ersch.titel}</td></tr>
                {ersch.inhaltsangabe && <tr><td><strong>Inhaltsangabe</strong></td><td>{ersch.inhaltsangabe}</td></tr>}
                {ersch.schlagworte && (ersch.schlagworte.length > 0) && (
                  <tr><td><strong>Schlagworte</strong></td><td>{(ersch.schlagworte || []).join(", ")}</td></tr>
                )}
                {ersch.zeitraum && <tr><td><strong>Zeitraum</strong></td><td className="mono">{ersch.zeitraum}</td></tr>}
                {ersch.aktenzeichen && <tr><td><strong>Aktenzeichen</strong></td><td className="mono">{ersch.aktenzeichen}</td></tr>}
              </tbody>
            </table>
          </section>
        )}

        {audit.length > 0 && (
          <section className="report__section">
            <h2 className="report__section-title">{ersch && ersch.titel ? "4" : "3"} — Audit-Log</h2>
            <table className="report__table">
              <thead><tr><th>Zeitpunkt</th><th>Aktion</th><th>Details</th></tr></thead>
              <tbody>
                {audit.map((e, i) => (
                  <tr key={i}>
                    <td className="mono text-sm">{e.zeitpunkt || e.zeit || "–"}</td>
                    <td>{e.aktion || "–"}</td>
                    <td className="text-sm">{e.details || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <div className="report__signoff">
          <span>Aussonderungstool · generiert am {heute}</span>
          <span>Job {jobId || "–"}</span>
        </div>
      </article>
    </div>
  );
}

Object.assign(window, { DownloadView });
