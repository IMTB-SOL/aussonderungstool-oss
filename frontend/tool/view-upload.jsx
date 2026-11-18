// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Upload-View — Vorgang hochladen + Prüfschritte auswählen
const { useState: useStateU, useRef: useRefU } = React;

function UploadView({ onStart }) {
  const { AGENTS } = window.AUSSONDERUNG_DATA;
  // file: Anzeige-Metadaten (name/size). echteDatei: das tatsächliche File-Objekt für den Upload.
  const [file, setFile] = useStateU(null);
  const [echteDatei, setEchteDatei] = useStateU(null);
  const [drag, setDrag] = useStateU(false);
  const [enabled, setEnabled] = useStateU({
    formal: true, format: true, inhalt: true, bewertung: true, erschliessung: true,
  });
  const REQUIRED = {};
  const [bewertungsmodus, setBewertungsmodus] = useStateU("metadaten");
  // Echte Backend-Job-ID nach dem Upload; Lade-/Fehlerzustand.
  const [jobId, setJobId] = useStateU(null);
  const [laeuft, setLaeuft] = useStateU(false);
  const [progress, setProgress] = useStateU(0);          // 0–100 (gerundeter Prozent)
  const [uploadStats, setUploadStats] = useStateU(null); // {loaded, total, speed} oder null
  const [fehler, setFehler] = useStateU(null);
  const fileRef = useRefU(null);
  const uploadStartRef = useRefU(0);

  // Datei wird sofort hochgeladen — danach erscheint die Paketstruktur.
  const handleFile = async (f) => {
    if (!f) return;
    // Nur echte ZIP-Dateien akzeptieren.
    const istZip = /\.zip$/i.test(f.name || "");
    if (!istZip) {
      setFehler("Bitte eine ZIP-Datei auswählen.");
      return;
    }
    setFehler(null);
    setEchteDatei(f);
    setFile({
      name: f.name,
      size: f.size || 0,
      lastModified: f.lastModified || Date.now(),
    });
    setJobId(null);
    setLaeuft(true);
    setProgress(0);
    setUploadStats(null);
    uploadStartRef.current = 0;
    try {
      const antwort = await window.Api.upload(f, (stats) => {
        // Erste Progress-Event setzt die Startzeit für die Geschwindigkeits-Schätzung.
        if (!uploadStartRef.current) uploadStartRef.current = performance.now();
        const dauerSekunden = (performance.now() - uploadStartRef.current) / 1000;
        // Geschwindigkeit erst nach 0,5 s einblenden, sonst springt sie wild.
        const speed = dauerSekunden > 0.5 ? stats.loaded / dauerSekunden : 0;
        setUploadStats({ loaded: stats.loaded, total: stats.total, speed });
        setProgress(Math.round((stats.loaded / Math.max(stats.total, 1)) * 100));
      });
      const id = antwort && antwort.job_id;
      if (!id) throw new Error("Der Server hat keine Job-ID zurückgegeben.");
      setJobId(id);
    } catch (e) {
      setFehler(e && e.message ? e.message : "Upload fehlgeschlagen.");
      setFile(null);
      setEchteDatei(null);
    } finally {
      setLaeuft(false);
    }
  };

  const entferneDatei = () => {
    setFile(null);
    setEchteDatei(null);
    setJobId(null);
    setFehler(null);
    setProgress(0);
    setUploadStats(null);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer?.files?.[0];
    handleFile(f);
  };

  const onChoose = (e) => handleFile(e.target.files?.[0]);

  const enabledCount = Object.values(enabled).filter(Boolean).length;

  // Job ist bereits hochgeladen — startet nur noch den Prüflauf.
  const start = () => {
    if (!jobId || laeuft) return;
    onStart({ jobId, fileName: file ? file.name : "", enabled, bewertungsmodus });
  };

  return (
    <div className="view-enter">
      <h1 className="view-title">Vorgang hochladen</h1>
      <p className="view-sub">
        XDOMEA-Archiv hochladen, Prüfschritte wählen, Lauf starten.
      </p>

      <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) 360px", gap: 24 }}>
        {/* LEFT */}
        <div className="flex-col" style={{ gap: 18 }}>
          {/* Dropzone */}
          <section>
            <h2 className="section-title">
              <span>1 — Archivdatei</span>
              <span className="eyebrow-tag">.zip</span>
            </h2>
            {!file ? (
              <div
                className={"dropzone" + (drag ? " is-drag" : "")}
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                onDragLeave={() => setDrag(false)}
                onDrop={onDrop}
                onClick={() => fileRef.current?.click()}
              >
                <div className="dropzone__icon"><Icons.Upload size={22} /></div>
                <div className="dropzone__title">ZIP-Datei hier ablegen</div>
                <div className="dropzone__hint">
                  <a onClick={(e) => { e.stopPropagation(); fileRef.current?.click(); }} style={{ color: "var(--imtb-nachtblau)", textDecoration: "underline", cursor: "pointer" }}>Auswählen</a>
                  &nbsp;· XDOMEA 2.1 – 3.0 · max. 2 GB
                </div>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".zip"
                  hidden
                  onChange={onChoose}
                />
              </div>
            ) : (
              <div className="dropzone is-loaded">
                <div className="file-card">
                  <div className="file-card__icon"><Icons.Archive size={20} /></div>
                  <div className="file-card__main">
                    <div className="file-card__name">{file.name}</div>
                    <div className="file-card__meta">
                      <span>{formatBytes(file.size)}</span>
                      <span>ZIP-Archiv</span>
                      <span>
                        {laeuft
                          ? (progress < 100
                              ? (uploadStats
                                  ? `Wird hochgeladen… ${formatBytes(uploadStats.loaded)} von ${formatBytes(uploadStats.total)}${uploadStats.speed > 0 ? " · " + formatBytes(uploadStats.speed) + "/s" : ""} · ${progress} %`
                                  : "Verbindung wird aufgebaut…")
                              : "Wird verarbeitet…")
                          : jobId ? "Hochgeladen" : "—"}
                      </span>
                    </div>
                  </div>
                  <button className="btn btn--ghost btn--sm" onClick={entferneDatei} disabled={laeuft}>
                    <Icons.X size={14} /> Entfernen
                  </button>
                </div>
                {laeuft && (
                  <div style={{
                    height: 4, marginTop: 10, borderRadius: 2,
                    background: "rgba(0,0,0,0.06)", overflow: "hidden",
                  }}>
                    {/* Balken auf raw-Verhältnis basiert (nicht auf gerundeter Prozent),
                        damit auch unter 1 % sichtbar etwas läuft. */}
                    <div style={{
                      height: "100%",
                      width: (uploadStats && uploadStats.total > 0
                              ? Math.min(100, (uploadStats.loaded / uploadStats.total) * 100)
                              : 0) + "%",
                      background: "var(--imtb-nachtblau)",
                      transition: "width .2s ease",
                    }} />
                  </div>
                )}
              </div>
            )}
          </section>

          {/* Paketstruktur — erscheint nach dem Upload zwischen Archivdatei und Prüfschritten */}
          {jobId && (
            <section>
              <h2 className="section-title">
                <span>Paketstruktur</span>
                <span className="eyebrow-tag">XDOMEA</span>
              </h2>
              <PaketBrowser jobId={jobId} />
            </section>
          )}

          {/* Agenten */}
          <section>
            <h2 className="section-title">
              <span>2 — Prüfschritte</span>
              <span className="eyebrow-tag">{enabledCount} / 5 aktiv</span>
            </h2>
            <div className="surface">
              <div className="surface__body" style={{ padding: 0 }}>
                <div className="agent-pipeline">
                  {AGENTS.map((a, idx) => {
                    const Ico = window.Icons[a.icon];
                    const checked = !!enabled[a.id];
                    const required = !!REQUIRED[a.id];
                    return (
                      <React.Fragment key={a.id}>
                        <div className="agent-row" style={{
                          padding: "14px 18px",
                          display: "grid",
                          gridTemplateColumns: "28px 32px minmax(0,1fr) auto auto",
                          gap: 14,
                          alignItems: "center",
                          opacity: checked ? 1 : 0.62,
                          transition: "opacity .15s",
                        }}>
                          <div style={{
                            width: 22, height: 22, borderRadius: 4,
                            background: checked ? "var(--imtb-nachtblau)" : "var(--page-deep)",
                            color: checked ? "var(--imtb-weiss)" : "var(--fg-muted)",
                            display: "grid", placeItems: "center",
                            fontSize: 11, fontWeight: 700,
                          }}>{a.nr}</div>
                          <div style={{
                            width: 32, height: 32, borderRadius: 6,
                            background: "var(--page-deep)",
                            color: "var(--imtb-nachtblau)",
                            display: "grid", placeItems: "center",
                          }}>
                            <Ico size={16} />
                          </div>
                          <div>
                            <div style={{ fontWeight: 600, fontSize: 13.5, color: "var(--imtb-anthrazit)", display: "flex", alignItems: "center", gap: 8 }}>
                              {a.titel}
                              {required && (
                                <span className="badge" style={{ background: "var(--imtb-nachtblau)", color: "var(--imtb-weiss)", fontSize: 10 }}>
                                  Pflicht
                                </span>
                              )}
                            </div>
                            <div className="text-muted text-xs">{a.kurz}</div>
                            {a.id === "bewertung" && checked && (
                              <div style={{ marginTop: 10, display: "flex", gap: 6, padding: "4px", background: "var(--page-deep)", borderRadius: 6, width: "fit-content" }}>
                                {[
                                  { v: "metadaten", label: "Nur Metadaten" },
                                  { v: "volltext",  label: "+ Dokumentinhalt" },
                                ].map((opt) => (
                                  <button
                                    key={opt.v}
                                    className={"chip" + (bewertungsmodus === opt.v ? " is-active" : "")}
                                    onClick={() => setBewertungsmodus(opt.v)}
                                    style={{ fontSize: 11, whiteSpace: "nowrap" }}
                                  >
                                    {opt.label}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                          <div className="text-xs text-muted" style={{ whiteSpace: "nowrap" }}>{a.dauer}</div>
                          <button
                            className={"switch" + (checked ? " is-on" : "")}
                            onClick={() => {
                              if (required) return;
                              setEnabled((s) => ({ ...s, [a.id]: !s[a.id] }));
                            }}
                            aria-label={a.titel}
                            role="switch"
                            aria-checked={checked}
                            disabled={required}
                            title={required ? "Pflichtschritt — nicht abwählbar" : ""}
                            style={required ? { opacity: 0.55, cursor: "not-allowed" } : undefined}
                          ></button>
                        </div>
                        {idx < AGENTS.length - 1 && (
                          <div className="agent-connector" aria-hidden="true">
                            <div className="agent-connector__line"></div>
                            <div className="agent-connector__arrow">
                              <Icons.ChevronDown size={11} />
                            </div>
                          </div>
                        )}
                      </React.Fragment>
                    );
                  })}
                </div>
              </div>
            </div>
          </section>
        </div>

        {/* RIGHT */}
        <aside className="flex-col" style={{ gap: 14, position: "sticky", top: 20, alignSelf: "start" }}>
          <div className="surface">
            <div className="surface__head">
              <div className="surface__title">
                <Icons.ListChecks size={14} />
                Zusammenfassung
              </div>
            </div>
            <div className="surface__body">
              <div className="flex-col gap-3">
                <Row label="Archivdatei" value={file ? file.name : <span className="text-muted">—</span>} mono={!!file} />
                <Row label="Volumen" value={file ? formatBytes(file.size) : "—"} />
                <Row label="Schritte" value={`${enabledCount} / 5`} />
                <Row label="Bewertung" value={bewertungsmodus === "metadaten" ? "Metadaten" : "+ Inhalt"} />
                <Row label="Laufzeit" value={enabled.bewertung && bewertungsmodus === "volltext" ? "~12 – 18 min" : "~6 – 10 min"} />
              </div>
              <div className="divider-h"></div>
              {fehler && (
                <div className="badge badge--err" style={{ width: "100%", justifyContent: "center", marginBottom: 8, whiteSpace: "normal", textAlign: "center" }}>
                  <Icons.AlertTriangle size={12} /> {fehler}
                </div>
              )}
              <button className="btn btn--primary btn--lg" disabled={!jobId || laeuft} onClick={start} style={{ width: "100%", justifyContent: "center" }}>
                {laeuft ? "Wird hochgeladen…" : "Prüfung starten"}
                {!laeuft && <Icons.ArrowRight size={14} />}
              </button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Row({ label, value, mono }) {
  return (
    <div className="flex justify-between items-baseline" style={{ gap: 8, minWidth: 0 }}>
      <span className="text-xs text-muted" style={{ flexShrink: 0, maxWidth: "45%" }}>{label}</span>
      <span style={{ fontSize: 12.5, textAlign: "right", fontFamily: mono ? "JetBrains Mono, monospace" : "inherit", color: "var(--imtb-anthrazit)", fontWeight: 500, minWidth: 0, flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {value}
      </span>
    </div>
  );
}

function formatBytes(bytes) {
  if (!bytes) return "—";
  const u = ["B","KB","MB","GB"];
  let i = 0; let n = bytes;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(n < 10 ? 1 : 0) + " " + u[i];
}

Object.assign(window, { UploadView, formatBytes });
