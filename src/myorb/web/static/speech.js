// The browser's voices: picking the most natural one, the per-device choice made on
// Integrations → Voice, and speaking. Used by the report's Read aloud and the Voice page.

(() => {
  "use strict";

  const VOICE_KEY = "myorb.voice";

  function voiceScore(voice) {
    const name = voice.name || "";
    let score = 0;
    if (/premium/i.test(name)) score += 50;
    if (/enhanced/i.test(name)) score += 40;
    if (/natural|neural|online/i.test(name)) score += 45;
    if (/google|compact|eloquence/i.test(name)) score -= 30;
    // macOS novelty voices
    if (/^(albert|bad news|bahh|bells|boing|bubbles|cellos|good news|jester|organ|superstar|trinoids|whisper|wobble|zarvox|fred|junior|ralph|kathy)\b/i.test(name)) score -= 100;
    if (voice.localService) score += 3;
    return score;
  }

  function voicesFor(tag) {
    if (!("speechSynthesis" in window)) return [];
    const prefix = tag.slice(0, 2).toLowerCase();
    return speechSynthesis
      .getVoices()
      .filter((voice) => voice.lang.toLowerCase().startsWith(prefix))
      .sort((a, b) => voiceScore(b) - voiceScore(a) || a.name.localeCompare(b.name));
  }

  function savedVoices() {
    try {
      return JSON.parse(localStorage.getItem(VOICE_KEY)) || {};
    } catch {
      return {};
    }
  }

  function saveVoice(code, name) {
    try {
      localStorage.setItem(VOICE_KEY, JSON.stringify({ ...savedVoices(), [code]: name }));
    } catch {
      // Without storage the choice lasts until the page is reloaded.
    }
  }

  function chosenVoice(tag) {
    const voices = voicesFor(tag);
    const saved = savedVoices()[tag.slice(0, 2)];
    return voices.find((voice) => voice.name === saved) || voices[0] || null;
  }

  // Sentence by sentence: some voices (Google's in Chrome) stop after about 15 seconds.
  // Resolves when the reading ends or is stopped.
  function speak(text, tag) {
    return new Promise((resolve) => {
      if (!("speechSynthesis" in window)) return resolve();
      speechSynthesis.cancel();
      const voice = chosenVoice(tag);
      const sentences = text.match(/[^.!?\n]+[.!?]*/g)?.map((part) => part.trim()).filter(Boolean) || [text];
      sentences.forEach((sentence, index) => {
        const utterance = new SpeechSynthesisUtterance(sentence);
        utterance.lang = voice?.lang || tag;
        if (voice) utterance.voice = voice;
        if (index === sentences.length - 1) utterance.onend = resolve;
        utterance.onerror = resolve;
        speechSynthesis.speak(utterance);
      });
    });
  }

  // Integrations → Voice: the browser's voices per language, on this device.

  function fillBrowserVoices() {
    document.querySelectorAll("[data-browser-voice]").forEach((row) => {
      const voices = voicesFor(row.dataset.tag);
      row.hidden = !voices.length;
      const current = chosenVoice(row.dataset.tag);
      row.querySelector("select").replaceChildren(
        ...voices.map((voice) => new Option(`${voice.name} · ${voice.lang}`, voice.name, false, voice === current)),
      );
    });
  }

  function showProviderSection() {
    const provider = document.querySelector("input[name='provider']:checked")?.value;
    document.querySelectorAll("[data-voice-section]").forEach((section) => {
      section.hidden = section.dataset.voiceSection !== provider;
    });
  }

  let sample = null;

  document.addEventListener("change", (event) => {
    const row = event.target.closest("[data-browser-voice]");
    if (row) saveVoice(row.dataset.browserVoice, event.target.value);
    if (event.target.matches("input[name='provider']")) showProviderSection();
  });

  document.addEventListener("click", (event) => {
    const browser = event.target.closest("[data-try-browser]");
    if (browser) {
      const row = browser.closest("[data-browser-voice]");
      speak(row.dataset.sample, row.dataset.tag);
      return;
    }
    const azure = event.target.closest("[data-try-azure]");
    if (azure) {
      const code = azure.dataset.tryAzure;
      const voice = document.querySelector(`[data-azure-voice="${code}"]`).value;
      sample?.pause();
      sample = new Audio(`/integrations/voice/sample?language=${code}&voice=${encodeURIComponent(voice)}`);
      sample.play().catch(() => {
        azure.textContent = "✕ Failed";
      });
    }
  });

  function init() {
    fillBrowserVoices();
    showProviderSection();
  }

  if ("speechSynthesis" in window) speechSynthesis.addEventListener("voiceschanged", fillBrowserVoices);
  document.addEventListener("DOMContentLoaded", init);
  document.addEventListener("htmx:load", init);

  window.myorbSpeech = { voicesFor, speak };
})();
