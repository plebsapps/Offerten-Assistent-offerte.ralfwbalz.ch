// Offerten-Assistent – Frontend
// STT/TTS über die Web Speech API (im Browser, ohne Server-Kosten), Chat-Stream via SSE.

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
  };

  // --- Spracherkennung (STT) ---
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let listening = false;

  if (SR) {
    recognition = new SR();
    recognition.lang = "de-CH";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (e) => {
      const transcript = e.results[0][0].transcript.trim();
      if (transcript) {
        el.textInput.value = transcript;
        sendMessage(transcript);
      }
    };
    recognition.onend = () => { listening = false; el.micBtn.classList.remove("active"); };
    recognition.onerror = () => { listening = false; el.micBtn.classList.remove("active"); };
  } else {
    el.banner.classList.remove("hidden");
    el.micBtn.disabled = true;
  }

  el.micBtn.addEventListener("click", () => {
    if (!recognition) return;
    if (listening) { recognition.stop(); return; }
    cancelSpeak();
    try {
      recognition.start();
      listening = true;
      el.micBtn.classList.add("active");
    } catch (_) { /* start() bei schnellem Doppelklick ignorieren */ }
  });

  // --- Sprachausgabe (TTS) ---
  function pickGermanVoice() {
    const voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
    return voices.find(v => /de-CH/i.test(v.lang)) ||
           voices.find(v => /^de/i.test(v.lang)) || null;
  }
  function speak(text) {
    if (!window.speechSynthesis || !el.ttsToggle.checked || !text) return;
    cancelSpeak();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "de-CH";
    const v = pickGermanVoice();
    if (v) u.voice = v;
    window.speechSynthesis.speak(u);
  }
  function cancelSpeak() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
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
          handleEvent(ev, assistant, () => full, (t) => { full = t; });
        }
      }
    } catch (_) {
      assistant.textContent = "Verbindungsfehler. Bitte erneut versuchen.";
    } finally {
      assistant.classList.remove("pending");
      sending = false;
      if (full.trim()) speak(full.trim());
    }
  }

  function handleEvent(ev, assistant, getFull, setFull) {
    if (ev.type === "token") {
      setFull(getFull() + ev.text);
      assistant.textContent = getFull();
      assistant.classList.remove("pending");
      el.messages.scrollTop = el.messages.scrollHeight;
    } else if (ev.type === "offer_created") {
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
    // Browser-TTS „aufwecken“, damit getVoices() befüllt ist.
    if (window.speechSynthesis) window.speechSynthesis.getVoices();
    sendMessage("Guten Tag, ich möchte ein IT-Projekt mit Ihnen besprechen.");
    el.textInput.focus();
  });

  el.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(el.textInput.value);
  });
})();
