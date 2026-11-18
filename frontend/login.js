// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Login-/Registrier-Logik der statischen Login-Seite.
(function () {
  // URLs relativ zum <base href> (Subpfad-Deployment-fähig).
  const LOGIN_URL = new URL("api/auth/login", document.baseURI).href;
  const REGISTER_URL = new URL("api/auth/register", document.baseURI).href;
  const APP_URL = new URL("app.html", document.baseURI).href;
  const CONFIG_URL = new URL("api/auth/config", document.baseURI).href;
  const AZURE_LOGIN_URL = new URL("api/auth/azure/login", document.baseURI).href;

  let modus = "login"; // "login" | "register"

  const titel = document.getElementById("titel");
  const submitBtn = document.getElementById("submit");
  const toggleText = document.getElementById("toggle");
  const toggleLink = document.getElementById("toggle-link");
  const meldung = document.getElementById("meldung");
  const form = document.getElementById("form");
  const emailEl = document.getElementById("email");
  const passwortEl = document.getElementById("passwort");

  function zeigeMeldung(text, art) {
    meldung.textContent = text;
    meldung.className = "meldung " + art;
  }

  function setzeModus(neu) {
    modus = neu;
    meldung.className = "meldung";
    if (modus === "login") {
      titel.textContent = "Anmelden";
      submitBtn.textContent = "Anmelden";
      passwortEl.setAttribute("autocomplete", "current-password");
      toggleText.innerHTML = 'Noch kein Konto? <a id="toggle-link">Registrieren</a>';
    } else {
      titel.textContent = "Registrieren";
      submitBtn.textContent = "Registrieren";
      passwortEl.setAttribute("autocomplete", "new-password");
      toggleText.innerHTML = 'Bereits registriert? <a id="toggle-link">Anmelden</a>';
    }
    document.getElementById("toggle-link").addEventListener("click", () =>
      setzeModus(modus === "login" ? "register" : "login")
    );
  }

  toggleLink.addEventListener("click", () => setzeModus("register"));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    submitBtn.disabled = true;
    const koerper = { email: emailEl.value.trim(), passwort: passwortEl.value };
    try {
      const res = await fetch(modus === "login" ? LOGIN_URL : REGISTER_URL, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(koerper),
      });
      const daten = await res.json().catch(() => null);
      if (!res.ok) {
        zeigeMeldung((daten && daten.detail) || "Fehler bei der Anfrage.", "fehler");
        return;
      }
      if (modus === "login") {
        window.location.href = APP_URL;
      } else {
        setzeModus("login");
        zeigeMeldung(
          (daten && daten.message) ||
            "Registrierung erhalten. Bitte melden Sie sich nach der Freischaltung an.",
          "ok"
        );
      }
    } catch (err) {
      zeigeMeldung("Netzwerkfehler. Bitte erneut versuchen.", "fehler");
    } finally {
      submitBtn.disabled = false;
    }
  });

  // Verfügbare Anmeldeverfahren laden und UI entsprechend schalten.
  const msBtn = document.getElementById("ms-login");
  const trenner = document.getElementById("trenner");
  const pwBlock = document.getElementById("pw-block");
  msBtn.setAttribute("href", AZURE_LOGIN_URL);

  (async function initConfig() {
    let cfg = { azure_aktiv: false, passwort_aktiv: true };
    try {
      const res = await fetch(CONFIG_URL, { credentials: "include" });
      if (res.ok) cfg = await res.json();
    } catch (_) {
      /* Fallback: nur Passwort anzeigen */
    }
    msBtn.style.display = cfg.azure_aktiv ? "block" : "none";
    pwBlock.style.display = cfg.passwort_aktiv ? "block" : "none";
    trenner.style.display = cfg.azure_aktiv && cfg.passwort_aktiv ? "block" : "none";
  })();
})();
