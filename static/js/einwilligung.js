// Google Ads: Einwilligungs-Banner, Widerruf und Conversion.
// Nur auf Seiten mit templates/_google_tag.html (dort entsteht window.googleAds).
// Ohne Zustimmung wird gtag.js gar nicht geladen; die Wahl liegt in einem Cookie auf
// ralfwbalz.ch und gilt damit auch für ralfwbalz.ch selbst (Lesen und Schreiben in
// _google_tag.html). Der Footer-Link "Cookie-Einstellungen" öffnet das Banner wieder.
(function () {
  if (!window.googleAds) return;

  const einwilligung = window.googleAds.einwilligung;

  // Aufruf aus app.js, sobald der Assistent eine Offerte erstellt hat.
  window.googleAdsKonversion = function () {
    if (!window.googleAds.konversion || einwilligung() !== "ja") return;
    try { gtag("event", "conversion", { send_to: window.googleAds.konversion }); } catch (e) { /* nie stören */ }
  };

  function setzen(wert) {
    window.googleAds.einwilligungMerken(wert);
    document.getElementById("einwilligung")?.remove();
    if (wert === "ja") {
      window.googleAdsLaden();
    } else {
      // Ablehnung oder Widerruf: Google-Cookies entfernen (gtag setzt sie auf .ralfwbalz.ch,
      // also für beide Websites) und, falls gtag.js schon läuft, ohne es neu starten
      document.cookie.split(";").map(c => c.split("=")[0].trim())
        .filter(n => n.startsWith("_gcl") || n.startsWith("_ga"))
        .forEach(n => {
          document.cookie = n + "=; Max-Age=0; path=/";
          document.cookie = n + "=; Max-Age=0; path=/; domain=." + location.hostname;
          document.cookie = n + "=; Max-Age=0; path=/; domain=.ralfwbalz.ch";
        });
      if (window.googleAds.geladen) location.reload();
    }
  }

  function banner() {
    if (document.getElementById("einwilligung")) return;
    const b = document.createElement("div");
    b.id = "einwilligung";
    b.className = "einwilligung";
    b.setAttribute("role", "dialog");
    b.setAttribute("aria-label", "Einwilligung zu Werbe-Cookies");
    b.innerHTML = `
      <p>
        Dürfen ralfwbalz.ch und offerte.ralfwbalz.ch Google Ads laden? Damit sehe ich,
        ob eine Anzeige zu einer Anfrage oder Offerte geführt hat. Google setzt dafür
        Cookies. Ihre Wahl gilt für beide Websites.
        <a href="/impressum#google-ads">Mehr dazu</a>
      </p>
      <div class="einwilligung-knoepfe">
        <button type="button" class="einwilligung-knopf" data-wahl="nein">Ablehnen</button>
        <button type="button" class="einwilligung-knopf" data-wahl="ja">Zustimmen</button>
      </div>`;
    b.querySelectorAll("button").forEach(k =>
      k.addEventListener("click", () => setzen(k.dataset.wahl)));
    document.body.appendChild(b);
  }

  if (!einwilligung()) banner();
  // Der Link steht fest in templates/_footer.html und zeigt ohne JavaScript bzw. auf Seiten
  // ohne Google-Tag auf den Cookie-Abschnitt im Impressum; hier öffnet er das Banner.
  const link = document.querySelector("[data-cookie-einstellungen]");
  if (link) link.addEventListener("click", e => { e.preventDefault(); banner(); });
})();
