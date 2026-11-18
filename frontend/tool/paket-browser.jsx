// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Paket-Browser — XDOMEA-Strukturansicht
// Tree (Paket > Akten > Vorgänge > Dokumente) + Detail-Panel, inkl. Vorhandensein-Check pro Datei.
// Lädt die echte Struktur über GET /api/job/{jobId}/struktur.
const { useState: useStateP, useMemo: useMemoP, useEffect: useEffectP } = React;

function PaketBrowser({ jobId }) {
  // selected: {type:'paket'|'akte'|'vorgang'|'dok', id, parentId?}
  const [selected, setSelected] = useStateP({ type: "paket" });
  const [openAkte, setOpenAkte] = useStateP({});
  const [openVorgang, setOpenVorgang] = useStateP({});
  const [struktur, setStruktur] = useStateP(null);
  const [laeuft, setLaeuft] = useStateP(false);
  const [fehler, setFehler] = useStateP(null);

  // Echte Paketstruktur vom Backend laden, sobald eine jobId vorliegt.
  useEffectP(() => {
    if (!jobId) return;
    let abgebrochen = false;
    setLaeuft(true);
    setFehler(null);
    window.Api.get(`job/${jobId}/struktur`)
      .then((daten) => {
        if (abgebrochen) return;
        setStruktur(daten);
        // Erste Akte und deren erste(n) Vorgang/Vorgänge standardmäßig aufklappen.
        const ersteAkte = daten && daten.akten && daten.akten[0];
        if (ersteAkte) {
          setOpenAkte({ [ersteAkte.id]: true });
          const ersterVorgang = (ersteAkte.vorgaenge || [])[0];
          if (ersterVorgang) setOpenVorgang({ [ersterVorgang.id]: true });
        }
        setSelected({ type: "paket" });
      })
      .catch((e) => {
        if (abgebrochen) return;
        setFehler(e && e.message ? e.message : "Paketstruktur konnte nicht geladen werden.");
      })
      .finally(() => {
        if (!abgebrochen) setLaeuft(false);
      });
    return () => { abgebrochen = true; };
  }, [jobId]);

  const akten = (struktur && struktur.akten) || [];
  const meta = (struktur && struktur.meta) || {};
  // "akten" = echte Akten-Ebene mit Vorgang-Kindern; "vorgaenge" = klassisches XDOMEA 2.x ohne Akten-Wrapper.
  const ebene = (struktur && struktur.ebene) || "leer";

  // Ein Dokument kann mehrere Dateien (Versionen/Formate) bündeln — daher
  // separate Zählung von Dokumenten und Dateien.
  const dokDateien = (d) => d.dateien || (d.datei ? [{ datei: d.datei, format: d.format, exist: d.exist }] : []);
  const stats = useMemoP(() => {
    let dokCount = 0, dateiCount = 0, missing = 0, vgCount = 0, taCount = 0;
    const zaehleDok = (d) => {
      dokCount++;
      dokDateien(d).forEach((f) => { dateiCount++; if (!f.exist) missing++; });
    };
    const zaehleVorgang = (v) => { vgCount++; (v.dokumente || []).forEach(zaehleDok); };
    akten.forEach((a) => {
      (a.dokumente || []).forEach(zaehleDok);
      (a.vorgaenge || []).forEach(zaehleVorgang);
      (a.teilakten || []).forEach((t) => {
        taCount++;
        (t.dokumente || []).forEach(zaehleDok);
        (t.vorgaenge || []).forEach(zaehleVorgang);
      });
    });
    return { aktenCount: akten.length, vgCount, taCount, dokCount, dateiCount, missing };
  }, [struktur]);

  // Beim Klick auf eine Akte: AZ kopieren wirkt nicht, daher Hilfsfunktion zum Auffinden.
  const findeDokument = (dokId) => {
    for (const a of akten) {
      const direkt = (a.dokumente || []).find((d) => d.id === dokId);
      if (direkt) return { dok: direkt, akte: a, vorgang: null };
      for (const v of a.vorgaenge || []) {
        const treffer = (v.dokumente || []).find((d) => d.id === dokId);
        if (treffer) return { dok: treffer, akte: a, vorgang: v };
      }
      for (const t of a.teilakten || []) {
        const direktT = (t.dokumente || []).find((d) => d.id === dokId);
        if (direktT) return { dok: direktT, akte: a, vorgang: t };
        for (const v of t.vorgaenge || []) {
          const treffer = (v.dokumente || []).find((d) => d.id === dokId);
          if (treffer) return { dok: treffer, akte: a, vorgang: v };
        }
      }
    }
    return null;
  };
  const findeVorgang = (vId) => {
    for (const a of akten) {
      const v = (a.vorgaenge || []).find((x) => x.id === vId);
      if (v) return { vorgang: v, akte: a };
      for (const t of a.teilakten || []) {
        const tv = (t.vorgaenge || []).find((x) => x.id === vId);
        if (tv) return { vorgang: tv, akte: a };
      }
    }
    return null;
  };
  // Einzelne Datei (innerhalb eines Dokuments) anhand ihrer ID auffinden.
  const findeDatei = (dateiId) => {
    const dokId = dateiId.replace(/-f\d+$/, "");
    const treffer = findeDokument(dokId);
    if (!treffer) return null;
    const datei = (treffer.dok.dateien || []).find((f) => f.id === dateiId);
    return datei ? { ...treffer, datei } : null;
  };

  // Rendert die Hierarchie Dokument → Schriftstück(e).
  // Ein Dokument ist immer ein eigener Knoten (Dokumentname); die zugehörigen
  // Schriftstücke (Dateien) erscheinen darunter mit ihrem DATEINAMEN — auch bei
  // nur einer Datei. Bei einer Datei direkt sichtbar, bei mehreren aufklappbar.
  const renderDokument = (d, pad) => {
    const dateien = dokDateien(d).map((f, i) => ({ id: `${d.id}-f${i}`, ...f }));
    const okDot = (exist) => exist
      ? <span className="tree-node__dot tree-node__dot--ok" title="Schriftstück im Paket vorhanden"></span>
      : <span className="tree-node__dot tree-node__dot--err" title="Schriftstück fehlt im Paket"></span>;

    // Schriftstück-Zeile (Datei mit Dateinamen).
    const schriftstueck = (f) => {
      const fSel = selected.type === "datei" && selected.id === f.id;
      return (
        <button
          key={f.id}
          className={"tree-node tree-node--dok" + (fSel ? " is-selected" : "")}
          onClick={() => setSelected({ type: "datei", id: f.id })}
          style={{ paddingLeft: pad + 22 }}
        >
          <span className="badge badge--info" style={{ marginRight: 4 }}>{f.format}</span>
          <span className="tree-node__label">{f.datei ? f.datei.split("/").pop() : "—"}</span>
          {okDot(f.exist)}
        </button>
      );
    };

    const dSel = selected.type === "dok" && selected.id === d.id;

    // Dokument ohne Schriftstück: nur der Dokument-Knoten.
    if (dateien.length === 0) {
      return (
        <button
          key={d.id}
          className={"tree-node tree-node--dok" + (dSel ? " is-selected" : "")}
          onClick={() => setSelected({ type: "dok", id: d.id })}
          style={{ paddingLeft: pad }}
        >
          <Icons.File size={11} />
          <span className="tree-node__label">{d.betreff}</span>
          <span className="text-xs text-muted" style={{ marginLeft: 6 }}>(ohne Schriftstück)</span>
        </button>
      );
    }

    // Bei einer Datei: Schriftstück immer sichtbar. Bei mehreren: aufklappbar.
    const aufklappbar = dateien.length > 1;
    const offen = aufklappbar ? !!openVorgang[d.id] : true;
    return (
      <React.Fragment key={d.id}>
        <button
          className={"tree-node tree-node--dok" + (dSel ? " is-selected" : "")}
          onClick={() => {
            setSelected({ type: "dok", id: d.id });
            if (aufklappbar) setOpenVorgang((o) => ({ ...o, [d.id]: !o[d.id] }));
          }}
          style={{ paddingLeft: pad }}
        >
          {aufklappbar
            ? <Icons.ChevronRight size={11} className="tree-chev" style={{ transform: offen ? "rotate(90deg)" : "none" }} />
            : <Icons.File size={11} />}
          <span className="tree-node__label">{d.betreff}</span>
          <span className="tree-node__count">{dateien.length}</span>
        </button>
        {offen && dateien.map(schriftstueck)}
      </React.Fragment>
    );
  };

  // Lade-/Fehler-/Leerzustände — Design-Optik beibehalten.
  if (!jobId) {
    return (
      <section>
        <h2 className="section-title"><span>Paket-Struktur</span></h2>
        <div className="surface"><div className="surface__body">
          <div className="text-muted text-sm">Noch kein Vorgang geladen.</div>
        </div></div>
      </section>
    );
  }
  if (laeuft) {
    return (
      <section>
        <h2 className="section-title"><span>Paket-Struktur</span></h2>
        <div className="surface"><div className="surface__body">
          <div className="text-muted text-sm">Paketstruktur wird geladen…</div>
        </div></div>
      </section>
    );
  }
  if (fehler) {
    return (
      <section>
        <h2 className="section-title">
          <span>Paket-Struktur</span>
          <span className="badge badge--err"><Icons.AlertTriangle size={10} /> Fehler</span>
        </h2>
        <div className="surface"><div className="surface__body">
          <div className="text-sm" style={{ color: "var(--err)" }}>{fehler}</div>
        </div></div>
      </section>
    );
  }
  // XDOMEA nicht parsebar — flache Dateiliste anzeigen.
  if (struktur && !struktur.xdomea) {
    const dateien = struktur.dateien || [];
    return (
      <section>
        <h2 className="section-title">
          <span>Paket-Struktur</span>
          <span className="eyebrow-tag">{dateien.length} Dateien</span>
        </h2>
        <div className="surface"><div className="surface__body">
          <div className="text-muted text-sm" style={{ marginBottom: 8 }}>
            Keine XDOMEA-Struktur erkannt — Dateiliste:
          </div>
          <div className="dok-table">
            {dateien.map((d, i) => (
              <div key={i} className="dok-row" style={{ cursor: "default" }}>
                <span className="dok-row__datei mono text-xs">{d}</span>
              </div>
            ))}
          </div>
        </div></div>
      </section>
    );
  }

  return (
    <section>
      <h2 className="section-title">
        <span>Paket-Struktur</span>
        <span className="eyebrow-tag">
          {stats.aktenCount} {ebene === "vorgaenge" ? "Vorgänge" : "Akten"}
          {stats.taCount > 0 ? ` · ${stats.taCount} Teilakten` : ""}
          {stats.vgCount > 0 ? ` · ${stats.vgCount} Vorgänge` : ""}
          {" · "}{stats.dokCount} Dokumente
          {" · "}{stats.dateiCount} Schriftstücke
        </span>
        {stats.missing > 0
          ? <span className="badge badge--warn"><Icons.AlertTriangle size={10} /> {stats.missing} Schriftstück{stats.missing > 1 ? "e" : ""} fehlt{stats.missing > 1 ? "en" : ""}</span>
          : <span className="badge badge--ok"><Icons.Check size={10} /> Alle referenzierten Schriftstücke vorhanden</span>}
      </h2>
      <div className="surface" style={{ overflow: "hidden" }}>
        <div className="paket-browser">
          {/* Tree */}
          <div className="paket-tree">
            <button
              className={"tree-node tree-node--root" + (selected.type === "paket" ? " is-selected" : "")}
              onClick={() => setSelected({ type: "paket" })}
            >
              <Icons.Archive size={13} />
              <span>Paket</span>
              <span className="tree-node__count">{stats.aktenCount}</span>
            </button>
            {akten.map((a) => {
              const open = !!openAkte[a.id];
              const sel = selected.type === "akte" && selected.id === a.id;
              const hatVorgaenge = (a.vorgaenge || []).length > 0;
              return (
                <React.Fragment key={a.id}>
                  <button
                    className={"tree-node tree-node--akte" + (sel ? " is-selected" : "")}
                    onClick={() => {
                      setSelected({ type: "akte", id: a.id });
                      setOpenAkte((o) => ({ ...o, [a.id]: !o[a.id] }));
                    }}
                  >
                    <Icons.ChevronRight size={11} className="tree-chev" style={{ transform: open ? "rotate(90deg)" : "none" }} />
                    <Icons.Folder size={13} />
                    <span className="tree-node__label">{a.az}</span>
                  </button>
                  {open && (a.vorgaenge || []).map((v) => {
                    const vOpen = !!openVorgang[v.id];
                    const vSel = selected.type === "vorgang" && selected.id === v.id;
                    const vLeer = (v.dokumente || []).length === 0;
                    return (
                      <React.Fragment key={v.id}>
                        <button
                          className={"tree-node tree-node--vorgang" + (vSel ? " is-selected" : "")}
                          onClick={() => {
                            setSelected({ type: "vorgang", id: v.id });
                            setOpenVorgang((o) => ({ ...o, [v.id]: !o[v.id] }));
                          }}
                          style={{ paddingLeft: 28, opacity: vLeer ? 0.6 : 1 }}
                          title={vLeer ? "Vorgang ohne Schriftstücke (so im Paket angelegt)" : undefined}
                        >
                          {vLeer
                            ? <span style={{ width: 11, display: "inline-block" }}></span>
                            : <Icons.ChevronRight size={11} className="tree-chev" style={{ transform: vOpen ? "rotate(90deg)" : "none" }} />}
                          <Icons.Folder size={12} />
                          <span className="tree-node__label">{v.az}</span>
                          {vLeer
                            ? <span className="text-xs text-muted" style={{ fontStyle: "italic" }}>leer</span>
                            : <span className="tree-node__count">{(v.dokumente || []).length}</span>}
                        </button>
                        {vOpen && !vLeer && (v.dokumente || []).map((d) => renderDokument(d, 48))}
                      </React.Fragment>
                    );
                  })}
                  {/* Teilakten (3-stufige Akten, z.B. regisafe): Teilakte → Dokumente/Vorgänge */}
                  {open && (a.teilakten || []).map((t) => {
                    const tOpen = !!openVorgang[t.id];
                    return (
                      <React.Fragment key={t.id}>
                        <button
                          className="tree-node tree-node--vorgang"
                          onClick={() => setOpenVorgang((o) => ({ ...o, [t.id]: !o[t.id] }))}
                          style={{ paddingLeft: 28 }}
                        >
                          <Icons.ChevronRight size={11} className="tree-chev" style={{ transform: tOpen ? "rotate(90deg)" : "none" }} />
                          <Icons.Folder size={12} />
                          <span className="tree-node__label">{t.betreff || t.az}</span>
                          <span className="tree-node__count">
                            {(t.dokumente || []).length + (t.vorgaenge || []).reduce((n, v) => n + (v.dokumente || []).length, 0)}
                          </span>
                        </button>
                        {tOpen && (t.dokumente || []).map((d) => renderDokument(d, 48))}
                        {tOpen && (t.vorgaenge || []).map((v) => (
                          <React.Fragment key={v.id}>
                            <button
                              className="tree-node tree-node--vorgang"
                              onClick={() => setSelected({ type: "vorgang", id: v.id })}
                              style={{ paddingLeft: 48 }}
                            >
                              <Icons.Folder size={12} />
                              <span className="tree-node__label">{v.betreff || v.az}</span>
                              <span className="tree-node__count">{(v.dokumente || []).length}</span>
                            </button>
                            {(v.dokumente || []).map((d) => renderDokument(d, 68))}
                          </React.Fragment>
                        ))}
                      </React.Fragment>
                    );
                  })}
                  {/* Lose Dokumente direkt unter der Akte (außerhalb von Vorgängen) */}
                  {open && (a.dokumente || []).map((d) => renderDokument(d, 28))}
                </React.Fragment>
              );
            })}
          </div>

          {/* Detail */}
          <div className="paket-detail">
            {selected.type === "paket" && <PaketDetail stats={stats} meta={meta} ebene={ebene} />}
            {selected.type === "akte" && (() => {
              const akte = akten.find((a) => a.id === selected.id);
              return akte ? (
                <AkteDetail
                  akte={akte}
                  onPickDok={(id) => setSelected({ type: "dok", id })}
                  onPickVorgang={(id) => {
                    setSelected({ type: "vorgang", id });
                    setOpenVorgang((o) => ({ ...o, [id]: true }));
                  }}
                />
              ) : null;
            })()}
            {selected.type === "vorgang" && (() => {
              const treffer = findeVorgang(selected.id);
              return treffer
                ? <VorgangDetail vorgang={treffer.vorgang} akte={treffer.akte} onPick={(id) => setSelected({ type: "dok", id })} />
                : null;
            })()}
            {selected.type === "dok" && (() => {
              const treffer = findeDokument(selected.id);
              return treffer
                ? <DokDetail dok={treffer.dok} akte={treffer.akte} vorgang={treffer.vorgang} jobId={jobId}
                    onPickDatei={(id) => setSelected({ type: "datei", id })} />
                : null;
            })()}
            {selected.type === "datei" && (() => {
              const treffer = findeDatei(selected.id);
              return treffer
                ? <DateiDetail datei={treffer.datei} dok={treffer.dok} akte={treffer.akte} vorgang={treffer.vorgang} jobId={jobId} />
                : null;
            })()}
          </div>
        </div>
      </div>
    </section>
  );
}

function PaketDetail({ stats, meta, ebene }) {
  const m = meta || {};
  const platzhalter = "—";
  const topLabel = ebene === "vorgaenge" ? "Vorgänge" : "Akten";
  return (
    <div>
      <div className="paket-detail__head">
        <Icons.Archive size={18} />
        <div>
          <div className="paket-detail__eyebrow">Paket</div>
          <h3 className="paket-detail__title">
            {m.version ? `XDOMEA ${m.version}` : "XDOMEA-Paket"}
          </h3>
        </div>
      </div>
      <dl className="meta-grid">
        <dt>Version</dt><dd>{m.version || platzhalter}</dd>
        <dt>ProzessID</dt><dd className="mono text-xs">{m.prozess_id || platzhalter}</dd>
        <dt>Erstellt</dt><dd>{m.erstellt || platzhalter}</dd>
        <dt>Absender</dt><dd>{m.absender || platzhalter}</dd>
        <dt>Empfänger</dt><dd>{m.empfaenger || platzhalter}</dd>
      </dl>
      <div className="paket-detail__statbar">
        <div><strong>{stats.aktenCount}</strong> {topLabel}</div>
        {stats.taCount > 0 && <div><strong>{stats.taCount}</strong> Teilakten</div>}
        {stats.vgCount > 0 && <div><strong>{stats.vgCount}</strong> Vorgänge</div>}
        <div><strong>{stats.dokCount}</strong> Dokumente</div>
        <div><strong>{stats.dateiCount}</strong> Schriftstücke</div>
        <div>{stats.missing === 0
          ? <span style={{ color: "var(--ok)" }}><Icons.Check size={11} /> ZIP vollständig</span>
          : <span style={{ color: "var(--err)" }}><Icons.AlertTriangle size={11} /> {stats.missing} fehlt</span>}</div>
      </div>
    </div>
  );
}

function AkteDetail({ akte, onPickDok, onPickVorgang }) {
  const vorgaenge = akte.vorgaenge || [];
  const teilakten = akte.teilakten || [];
  const direkteDokumente = akte.dokumente || [];
  const teilaktenDok = teilakten.reduce(
    (n, t) => n + (t.dokumente || []).length + (t.vorgaenge || []).reduce((m, v) => m + (v.dokumente || []).length, 0),
    0,
  );
  const dokAnzahl = direkteDokumente.length
    + vorgaenge.reduce((n, v) => n + (v.dokumente || []).length, 0)
    + teilaktenDok;
  return (
    <div>
      <div className="paket-detail__head">
        <Icons.Folder size={18} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="paket-detail__eyebrow">Akte · {akte.az}</div>
          <h3 className="paket-detail__title">{akte.betreff}</h3>
        </div>
      </div>
      <dl className="meta-grid">
        <dt>Aktenzeichen</dt><dd className="mono">{akte.az}</dd>
        <dt>Laufzeitbeginn</dt><dd>{akte.laufzeit}</dd>
        <dt>Behörde</dt><dd>{akte.behoerde}</dd>
        {teilakten.length > 0 && <><dt>Teilakten</dt><dd>{teilakten.length}</dd></>}
        <dt>Vorgänge</dt><dd>{vorgaenge.length}</dd>
        <dt>Dokumente</dt><dd>{dokAnzahl}</dd>
      </dl>
      {teilakten.length > 0 && (
        <>
          <div className="paket-detail__sub">Teilakten</div>
          <div className="dok-table">
            {teilakten.map((t) => (
              <div key={t.id} className="dok-row" style={{ cursor: "default" }}>
                <Icons.Folder size={12} />
                <span className="dok-row__betreff">{t.betreff}</span>
                <span className="dok-row__datei mono text-xs">{t.az || "—"}</span>
                <span className="dok-row__size text-xs">
                  {(t.dokumente || []).length + (t.vorgaenge || []).reduce((n, v) => n + (v.dokumente || []).length, 0)} Dok.
                </span>
              </div>
            ))}
          </div>
        </>
      )}
      {vorgaenge.length > 0 && (
        <>
          <div className="paket-detail__sub">Vorgänge</div>
          <div className="dok-table">
            {vorgaenge.map((v) => (
              <button key={v.id} className="dok-row" onClick={() => onPickVorgang(v.id)}>
                <Icons.Folder size={12} />
                <span className="dok-row__betreff">{v.betreff}</span>
                <span className="dok-row__datei mono text-xs">{v.az || "—"}</span>
                <span className="dok-row__size text-xs">{(v.dokumente || []).length} Dok.</span>
                <Icons.ChevronRight size={12} style={{ color: "var(--text-muted)" }} />
              </button>
            ))}
          </div>
        </>
      )}
      {direkteDokumente.length > 0 && (
        <>
          <div className="paket-detail__sub">
            {vorgaenge.length > 0 ? "Dokumente direkt unter der Akte" : "Dokumente"}
          </div>
          <div className="dok-table">
            {direkteDokumente.map((d) => {
              const nDat = (d.dateien || []).length || (d.datei ? 1 : 0);
              return (
                <button key={d.id} className="dok-row" onClick={() => onPickDok(d.id)}>
                  <span className="badge badge--info">{d.format}</span>
                  <span className="dok-row__betreff">{d.betreff}</span>
                  <span className="dok-row__datei mono text-xs">
                    {nDat > 1 ? `${nDat} Schriftstücke` : (d.datei || "—")}
                  </span>
                  <span className="dok-row__size text-xs">{d.typ || ""}</span>
                  {d.exist
                    ? <Icons.Check size={12} style={{ color: "var(--ok)" }} />
                    : <Icons.X size={12} style={{ color: "var(--err)" }} />}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function VorgangDetail({ vorgang, akte, onPick }) {
  const dokumente = vorgang.dokumente || [];
  return (
    <div>
      <div className="paket-detail__head">
        <Icons.Folder size={18} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="paket-detail__eyebrow">Vorgang · {akte.az}</div>
          <h3 className="paket-detail__title">{vorgang.betreff}</h3>
        </div>
      </div>
      <dl className="meta-grid">
        <dt>Vorgangs-AZ</dt><dd className="mono">{vorgang.az}</dd>
        <dt>Akte</dt><dd className="mono">{akte.az}</dd>
        <dt>Laufzeitbeginn</dt><dd>{vorgang.laufzeit || "—"}</dd>
        <dt>Behörde</dt><dd>{vorgang.behoerde || akte.behoerde || "—"}</dd>
        <dt>Dokumente</dt><dd>{dokumente.length}</dd>
      </dl>
      <div className="paket-detail__sub">Dokumente</div>
      {dokumente.length === 0 ? (
        <div className="text-muted text-sm" style={{ padding: 8 }}>
          Keine Dokumente in diesem Vorgang.
        </div>
      ) : (
        <div className="dok-table">
          {dokumente.map((d) => {
            const nDat = (d.dateien || []).length || (d.datei ? 1 : 0);
            return (
              <button key={d.id} className="dok-row" onClick={() => onPick(d.id)}>
                <span className="badge badge--info">{d.format}</span>
                <span className="dok-row__betreff">{d.betreff}</span>
                <span className="dok-row__datei mono text-xs">
                  {nDat > 1 ? `${nDat} Schriftstücke` : (d.datei || "—")}
                </span>
                <span className="dok-row__size text-xs">{d.typ || ""}</span>
                {d.exist
                  ? <Icons.Check size={12} style={{ color: "var(--ok)" }} />
                  : <Icons.X size={12} style={{ color: "var(--err)" }} />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Datei-URL aus dem ZIP — nur wenn vorhanden.
function _dateiUrl(jobId, datei, exist) {
  return (exist && datei && jobId)
    ? window.Api.url(`job/${jobId}/datei?name=${encodeURIComponent(datei)}`)
    : null;
}

// Inline-Vorschau einer einzelnen Datei (PDF/Bild) — sonst Download-Hinweis.
function DateiVorschau({ url, format, exist, titel }) {
  const fmt = (format || "").toUpperCase();
  const istBild = ["JPG", "JPEG", "PNG", "GIF", "TIFF", "TIF", "BMP", "WEBP"].includes(fmt);
  if (!exist) {
    return (
      <div className="pdf-preview pdf-preview--notice" style={{ color: "var(--err)" }}>
        <Icons.AlertTriangle size={14} />
        Schriftstück nicht im Paket enthalten — wird in der formalen Prüfung als Fehler aufgeführt.
      </div>
    );
  }
  if (fmt === "PDF") {
    return (
      <div className="pdf-preview">
        <div className="pdf-preview__toolbar">
          <span className="text-xs text-muted">Vorschau</span>
          <div style={{ flex: 1 }}></div>
          <a className="btn btn--ghost btn--sm" href={url} target="_blank" rel="noopener">
            <Icons.Eye size={13} /> In neuem Tab öffnen
          </a>
        </div>
        <iframe
          src={url}
          title={titel}
          style={{ width: "100%", height: 460, border: "none", display: "block", background: "#fff" }}
        ></iframe>
      </div>
    );
  }
  if (istBild) {
    return (
      <div className="pdf-preview">
        <div className="pdf-preview__toolbar"><span className="text-xs text-muted">Vorschau</span></div>
        <div className="pdf-preview__page" style={{ textAlign: "center" }}>
          <img src={url} alt={titel} style={{ maxWidth: "100%", height: "auto" }} />
        </div>
      </div>
    );
  }
  return (
    <div className="pdf-preview pdf-preview--notice">
      <Icons.Info size={14} />
      Keine Inline-Vorschau für {format}.
      <a href={url} target="_blank" rel="noopener" style={{ marginLeft: 6 }}>Datei öffnen</a>
    </div>
  );
}

function DokDetail({ dok, akte, vorgang, jobId, onPickDatei }) {
  const dateien = (dok.dateien && dok.dateien.length)
    ? dok.dateien
    : (dok.datei ? [{ id: dok.id + "-f0", datei: dok.datei, format: dok.format, exist: dok.exist }] : []);
  const einzeln = dateien.length === 1;
  return (
    <div>
      <div className="paket-detail__head">
        <Icons.File size={18} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="paket-detail__eyebrow">Dokument · {vorgang ? vorgang.az : akte.az}</div>
          <h3 className="paket-detail__title">{dok.betreff}</h3>
        </div>
        {dok.exist
          ? <span className="badge badge--ok"><Icons.Check size={10} /> Vollständig</span>
          : <span className="badge badge--err"><Icons.X size={10} /> Schriftstück(e) fehlen</span>}
      </div>
      <dl className="meta-grid">
        <dt>Typ</dt><dd>{dok.typ || "—"}</dd>
        <dt>Schriftstücke</dt><dd>{dateien.length}</dd>
        <dt>Akte</dt><dd className="mono">{akte.az}</dd>
        {vorgang && <><dt>Vorgang</dt><dd className="mono">{vorgang.az}</dd></>}
      </dl>
      <div className="paket-detail__sub">{dateien.length > 1 ? `Schriftstücke (${dateien.length})` : "Schriftstück"}</div>
      <div className="dok-table">
        {dateien.map((f) => (
          <button key={f.id} className="dok-row" onClick={() => onPickDatei(f.id)}>
            <span className="badge badge--info">{f.format}</span>
            <span className="dok-row__datei mono text-xs">{f.datei ? f.datei.split("/").pop() : "—"}</span>
            {f.exist
              ? <Icons.Check size={12} style={{ color: "var(--ok)" }} />
              : <Icons.X size={12} style={{ color: "var(--err)" }} />}
            <Icons.ChevronRight size={12} style={{ color: "var(--text-muted)" }} />
          </button>
        ))}
      </div>
      {einzeln && (
        <div style={{ marginTop: 10 }}>
          <DateiVorschau
            url={_dateiUrl(jobId, dateien[0].datei, dateien[0].exist)}
            format={dateien[0].format}
            exist={dateien[0].exist}
            titel={dok.betreff}
          />
        </div>
      )}
    </div>
  );
}

function DateiDetail({ datei, dok, akte, vorgang, jobId }) {
  const name = datei.datei ? datei.datei.split("/").pop() : "—";
  return (
    <div>
      <div className="paket-detail__head">
        <Icons.File size={18} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="paket-detail__eyebrow">Schriftstück · {dok.betreff}</div>
          <h3 className="paket-detail__title">{name}</h3>
        </div>
        {datei.exist
          ? <span className="badge badge--ok"><Icons.Check size={10} /> Im ZIP</span>
          : <span className="badge badge--err"><Icons.X size={10} /> Fehlt</span>}
      </div>
      <dl className="meta-grid">
        <dt>Dateiname</dt><dd className="mono text-xs">{datei.datei || "—"}</dd>
        <dt>Format</dt><dd>{datei.format}</dd>
        <dt>Dokument</dt><dd>{dok.betreff}</dd>
        <dt>Akte</dt><dd className="mono">{akte.az}</dd>
        {vorgang && <><dt>Vorgang</dt><dd className="mono">{vorgang.az}</dd></>}
      </dl>
      <div style={{ marginTop: 10 }}>
        <DateiVorschau
          url={_dateiUrl(jobId, datei.datei, datei.exist)}
          format={datei.format}
          exist={datei.exist}
          titel={name}
        />
      </div>
    </div>
  );
}

Object.assign(window, { PaketBrowser });
