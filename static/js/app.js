// Offerten-Assistent – Frontend
// STT/TTS serverseitig über OpenAI (Whisper + neuronales TTS): Audio wird im Browser
// per MediaRecorder aufgenommen bzw. als MP3 abgespielt. Chat-Stream via SSE.

(() => {
  "use strict";

  const sessionId = (crypto.randomUUID && crypto.randomUUID()) ||
    ("sess-" + Date.now() + "-" + Math.random().toString(16).slice(2));

  const el = {
    intro: document.getElementById("intro"),
    chat: document.getElementById("chat"),
    startBtn: document.getElementById("startBtn"),
    messages: document.getElementById("messages"),
    composer: document.getElementById("composer"),
    textInput: document.getElementById("textInput"),
    website: document.getElementById("website"),
    micBtn: document.getElementById("micBtn"),
    ttsToggle: document.getElementById("ttsToggle"),
    statusHint: document.getElementById("statusHint"),
    banner: document.getElementById("speechBanner"),
    overlay: document.getElementById("abschlussOverlay"),
    overlayCount: document.getElementById("overlayCount"),
    overlayBtn: document.getElementById("overlayBtn"),
  };

  const homepageUrl = document.body.dataset.homepage || "https://ralfwbalz.ch";
  let offerCreated = false;

  // --- Spracheingabe (STT, serverseitig via OpenAI) ---
  // Audio im Browser aufnehmen, an /chat/stt schicken, Transkript senden.
  const canRecord = !!(navigator.mediaDevices &&
                       navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  let mediaRecorder = null;
  let chunks = [];
  let recording = false;

  if (!canRecord) {
    el.banner.classList.remove("hidden");
    el.micBtn.disabled = true;
  }

  async function startRecording() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((tr) => tr.stop());
      const type = mediaRecorder.mimeType || "audio/webm";
      await transcribeAndSend(new Blob(chunks, { type }), type);
    };
    mediaRecorder.start();
    recording = true;
    el.micBtn.classList.add("active");
  }

  function stopRecording() {
    if (mediaRecorder && recording) {
      recording = false;
      el.micBtn.classList.remove("active");
      mediaRecorder.stop();
    }
  }

  async function transcribeAndSend(blob, type) {
    if (!blob.size) return;
    el.micBtn.disabled = true;
    const ext = (type.includes("mp4") || type.includes("mpeg") || type.includes("aac"))
      ? "mp4" : (type.includes("ogg") ? "ogg" : "webm");
    try {
      const fd = new FormData();
      fd.append("session_id", sessionId);
      fd.append("audio", blob, "aufnahme." + ext);
      const resp = await fetch("/chat/stt", { method: "POST", body: fd });
      if (resp.ok) {
        const data = await resp.json();
        const text = (data.text || "").trim();
        if (text) sendMessage(text);
      }
    } catch (_) { /* ignorieren */ }
    finally { el.micBtn.disabled = false; }
  }

  el.micBtn.addEventListener("click", async () => {
    if (!canRecord) return;
    if (recording) { stopRecording(); return; }
    cancelSpeak();
    try {
      await startRecording();
    } catch (_) {
      recording = false;
      el.micBtn.classList.remove("active");
    }
  });

  // --- Sprachausgabe (TTS, serverseitig via OpenAI) ---
  // Satzweise: Sobald ein Satz vorliegt, wird er synthetisiert und abgespielt, während
  // die folgenden Sätze schon parallel erzeugt werden. So beginnt die Stimme deutlich
  // früher, statt auf das fertige MP3 der ganzen Antwort zu warten.
  let currentAudio = null;
  let speakGen = 0;        // wird bei cancelSpeak erhöht → laufende/wartende Jobs verwerfen
  let ttsJobs = [];        // {gen, url: Promise<objectURL|null>} in Reihenfolge
  let ttsPumping = false;

  function ttsFetch(text, gen) {
    return fetch("/chat/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text }),
    })
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => (b && gen === speakGen ? URL.createObjectURL(b) : null))
      .catch(() => null);
  }

  function enqueueSpeak(text) {
    if (!el.ttsToggle.checked) return;
    text = (text || "").trim();
    if (!text) return;
    const gen = speakGen;
    ttsJobs.push({ gen, url: ttsFetch(text, gen) });  // Synthese startet sofort
    pumpTTS();
  }

  async function pumpTTS() {
    if (ttsPumping) return;
    ttsPumping = true;
    try {
      while (ttsJobs.length) {
        const job = ttsJobs.shift();
        const url = await job.url;
        if (!url || job.gen !== speakGen) { if (url) URL.revokeObjectURL(url); continue; }
        await playUrl(url, job.gen);
      }
    } finally {
      ttsPumping = false;
    }
  }

  function playUrl(url, gen) {
    return new Promise((resolve) => {
      if (gen !== speakGen) { URL.revokeObjectURL(url); resolve(); return; }
      const audio = new Audio(url);
      currentAudio = audio;
      const done = () => {
        URL.revokeObjectURL(url);
        if (currentAudio === audio) currentAudio = null;
        resolve();
      };
      audio.onended = done;
      audio.onerror = done;
      audio.play().catch(done);
    });
  }

  function cancelSpeak() {
    speakGen += 1;   // verwirft alle laufenden und wartenden Jobs
    ttsJobs = [];
    if (currentAudio) {
      try { currentAudio.pause(); } catch (_) { /* egal */ }
      currentAudio = null;
    }
  }

  // Zerlegt den Puffer in vollständige Sätze (Satzende + folgendes Leerzeichen) und gibt
  // den noch unvollständigen Rest zurück. Kurze Fragmente und einzelne Abkürzungsbuchstaben
  // (z. B. „z. B.") werden nicht vorzeitig abgetrennt.
  function takeSentences(buf) {
    const sents = [];
    let start = 0;
    for (let i = 0; i < buf.length; i++) {
      const c = buf[i];
      if (c !== "." && c !== "!" && c !== "?" && c !== "…" && c !== "\n") continue;
      let j = i + 1;
      while (j < buf.length && ".!?…".indexOf(buf[j]) !== -1) j++;
      const nxt = buf[j];
      const istGrenze = nxt === " " || nxt === "\n" || nxt === "\t";
      if (!istGrenze) continue;  // Satzende am Pufferende: auf mehr Text warten
      const seg = buf.slice(start, j).trim();
      // Abkürzungs-Heuristik: einzelner Buchstabe direkt vor dem Punkt → nicht trennen.
      const vor = buf[i - 1] || "";
      const davor = buf[i - 2] || " ";
      const istAbk = c === "." && /[A-Za-zÄÖÜäöü]/.test(vor) && !/[A-Za-zÄÖÜäöü]/.test(davor);
      if (seg.length < 30 || istAbk) continue;  // weiter sammeln
      sents.push(seg);
      start = j;
      i = j - 1;
    }
    return { sents, rest: buf.slice(start) };
  }

  // --- Chat-UI ---
  function addBubble(role, text) {
    const div = document.createElement("div");
    div.className = "bubble " + role;
    div.textContent = text;
    el.messages.appendChild(div);
    el.messages.scrollTop = el.messages.scrollHeight;
    return div;
  }

  let sending = false;

  async function sendMessage(text) {
    text = (text || "").trim();
    if (!text || sending) return;
    sending = true;
    el.textInput.value = "";
    el.statusHint.textContent = "";
    addBubble("user", text);

    const assistant = addBubble("assistant", "");
    assistant.classList.add("pending");
    let full = "";

    // Neue Antwort: laufende Sprachausgabe stoppen und Satz-Puffer aufsetzen.
    cancelSpeak();
    const wantTTS = el.ttsToggle.checked;
    let ttsBuf = "";
    function feedSpeak(chunk) {
      if (!wantTTS) return;
      ttsBuf += chunk;
      const { sents, rest } = takeSentences(ttsBuf);
      ttsBuf = rest;
      for (const s of sents) enqueueSpeak(s);
    }

    try {
      const resp = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, text, website: el.website.value }),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        assistant.textContent = err.error || "Es ist ein Fehler aufgetreten.";
        assistant.classList.remove("pending");
        sending = false;
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const chunk = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 2);
          if (!chunk.startsWith("data:")) continue;
          const payload = chunk.slice(5).trim();
          let ev;
          try { ev = JSON.parse(payload); } catch (_) { continue; }
          handleEvent(ev, assistant, () => full, (t) => { full = t; }, feedSpeak);
        }
      }
    } catch (_) {
      assistant.textContent = "Verbindungsfehler. Bitte erneut versuchen.";
    } finally {
      assistant.classList.remove("pending");
      sending = false;
      // Restlichen Satz (ohne abschliessendes Satzzeichen) noch vorlesen.
      if (wantTTS && ttsBuf.trim()) enqueueSpeak(ttsBuf.trim());
      ttsBuf = "";
      if (offerCreated) showAbschluss();
    }
  }

  // Nach erstellter Offerte: Abschluss-Overlay mit Countdown-Weiterleitung zeigen.
  function showAbschluss() {
    if (!el.overlay || !el.overlay.classList.contains("hidden")) return;
    el.overlayBtn.href = homepageUrl;
    el.overlayBtn.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.href = homepageUrl;
    });
    el.overlay.classList.remove("hidden");
    let rest = 8;
    el.overlayCount.textContent = rest;
    const timer = setInterval(() => {
      rest -= 1;
      el.overlayCount.textContent = rest;
      if (rest <= 0) {
        clearInterval(timer);
        window.location.href = homepageUrl;
      }
    }, 1000);
  }

  function handleEvent(ev, assistant, getFull, setFull, feedSpeak) {
    if (ev.type === "token") {
      setFull(getFull() + ev.text);
      assistant.textContent = getFull();
      assistant.classList.remove("pending");
      el.messages.scrollTop = el.messages.scrollHeight;
      if (feedSpeak) feedSpeak(ev.text);
    } else if (ev.type === "offer_created") {
      offerCreated = true;
      el.statusHint.textContent = "✓ Offerte erstellt und an Ralf gesendet.";
    } else if (ev.type === "limit") {
      assistant.textContent = ev.text;
    } else if (ev.type === "error") {
      if (!getFull()) assistant.textContent = ev.message || "Fehler.";
    }
    // 'done' braucht keine Behandlung – Abschluss erfolgt im finally.
  }

  // --- Start ---
  el.startBtn.addEventListener("click", () => {
    el.intro.classList.add("hidden");
    el.chat.classList.remove("hidden");
    sendMessage("Guten Tag, ich möchte ein IT-Projekt mit Ihnen besprechen.");
    el.textInput.focus();
  });

  el.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(el.textInput.value);
  });
})();
