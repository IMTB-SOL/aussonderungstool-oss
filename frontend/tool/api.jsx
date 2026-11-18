// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Zentrale Backend-Anbindung für die Tool-SPA.
// Reines JS (kein JSX) — über window.Api global verfügbar.

(function () {
  // API-Basis aus dem <base href> ableiten — funktioniert auch im Subpfad-Deployment.
  const API_BASE = new URL("api/", document.baseURI).href;
  const LOGIN_URL = new URL("login.html", document.baseURI).href;

  function _url(path) {
    return API_BASE + String(path).replace(/^\/+/, "");
  }

  async function _request(path, opts) {
    const res = await fetch(_url(path), { credentials: "include", ...(opts || {}) });
    if (res.status === 401) {
      window.location.href = LOGIN_URL;
      throw new Error("Nicht angemeldet.");
    }
    return res;
  }

  async function _json(res) {
    const text = await res.text();
    let daten = null;
    try {
      daten = text ? JSON.parse(text) : null;
    } catch (_) {
      daten = null;
    }
    if (!res.ok) {
      const detail = (daten && daten.detail) || `Server-Fehler (${res.status})`;
      throw new Error(detail);
    }
    return daten;
  }

  const Api = {
    base: API_BASE,

    url: _url,

    async get(path) {
      return _json(await _request(path));
    },

    async post(path, body) {
      return _json(
        await _request(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body || {}),
        })
      );
    },

    async put(path, body) {
      return _json(
        await _request(path, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body || {}),
        })
      );
    },

    async del(path) {
      return _json(await _request(path, { method: "DELETE" }));
    },

    // Datei-Upload (multipart). Liefert {job_id}.
    // onProgress(stats): stats = {loaded, total} in Bytes — die UI rechnet daraus
    // Prozent und Geschwindigkeit. So sieht der User auch bei großen Dateien
    // sofort Bytes/Geschwindigkeit, statt minutenlang "0 %" zu sehen, bis sich
    // der gerundete Prozentwert weiterbewegt.
    async upload(file, onProgress) {
      return new Promise((resolve, reject) => {
        const form = new FormData();
        form.append("datei", file);
        const xhr = new XMLHttpRequest();
        xhr.open("POST", _url("upload"));
        xhr.withCredentials = true;
        if (onProgress) {
          xhr.upload.addEventListener("progress", (e) => {
            if (e.lengthComputable) onProgress({ loaded: e.loaded, total: e.total });
          });
        }
        xhr.onload = () => {
          if (xhr.status === 401) {
            window.location.href = LOGIN_URL;
            return reject(new Error("Nicht angemeldet."));
          }
          let daten = null;
          try {
            daten = JSON.parse(xhr.responseText);
          } catch (_) {
            daten = null;
          }
          if (xhr.status >= 200 && xhr.status < 300) resolve(daten);
          else reject(new Error((daten && daten.detail) || `Upload-Fehler (${xhr.status})`));
        };
        xhr.onerror = () => reject(new Error("Netzwerkfehler beim Upload."));
        xhr.send(form);
      });
    },

    // Server-Sent-Events für den Job-Status. Gibt die EventSource zurück.
    sse(jobId, onEvent) {
      const quelle = new EventSource(_url(`job/${jobId}/status`), { withCredentials: true });
      quelle.onmessage = (e) => {
        try {
          onEvent(JSON.parse(e.data));
        } catch (_) {
          /* ignore */
        }
      };
      return quelle;
    },

    // Download-URL für das fertige ZIP.
    downloadUrl(jobId) {
      return _url(`job/${jobId}/download`);
    },
  };

  window.Api = Api;
})();
