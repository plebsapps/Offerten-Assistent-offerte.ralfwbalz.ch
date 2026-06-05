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
  };

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
  let currentAudio = null;

  async function speak(text) {
    if (!el.ttsToggle.checked || !text) return;
    cancelSpeak();
    try {
      const resp = await fetch("/chat/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, text }),
      });
      if (!resp.ok) return;
      const url = URL.createObjectURL(await resp.blob());
      const audio = new Audio(url);
      currentAudio = audio;
      audio.onended = () => { URL.revokeObjectURL(url); if (currentAudio === audio) currentAudio = null; };
      audio.play().catch(() => {});
    } catch (_) { /* ignorieren */ }
  }

  function cancelSpeak() {
    if (currentAudio) {
      try { currentAudio.pause(); } catch (_) { /* egal */ }
      currentAudio = null;
    }
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
    sendMessage("Guten Tag, ich möchte ein IT-Projekt mit Ihnen besprechen.");
    el.textInput.focus();
  });

  el.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(el.textInput.value);
  });
})();
