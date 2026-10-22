// Sound and music, on every page: interface sounds, the ambient pad, the YouTube player and
// the dock in the bottom-right corner that controls them. The dock is kept across page changes
// (hx-preserve with hx-boost), so the music keeps playing while the user moves around Jarvis.

(() => {
  "use strict";

  const STORAGE_KEY = "jarvis.welcome";
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function loadPrefs() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
    } catch {
      return {};
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
      // Storage may be unavailable (private mode); preferences then last for this page only.
    }
  }

  const prefs = { sounds: false, volume: 0.45, ...loadPrefs() };

  // Where the music comes from is set on Integrations → Music; read it when the music starts,
  // so a change applies without reloading the page.
  let musicConfig = { source: "ambient" };

  async function loadMusicConfig() {
    try {
      const response = await fetch("/music/config", { headers: { Accept: "application/json" } });
      if (response.ok) musicConfig = await response.json();
    } catch {
      // Offline or signed out: keep the last known source.
    }
    render();
    return musicConfig;
  }

  // Interface sounds: short synthesized tones, off by default

  const ui = { ctx: null, out: null };

  function uiContext() {
    if (!ui.ctx) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return null;
      ui.ctx = new AudioContext();
      ui.out = ui.ctx.createGain();
      ui.out.gain.value = 5.5;
      ui.out.connect(ui.ctx.destination);
    }
    if (ui.ctx.state === "suspended") ui.ctx.resume().catch(() => {});
    return ui;
  }

  function tone(f1, f2, start, duration, peak, type = "sine") {
    const { ctx, out } = ui;
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    const t0 = ctx.currentTime + start;
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(f1, t0);
    oscillator.frequency.exponentialRampToValueAtTime(f2, t0 + duration);
    gain.gain.setValueAtTime(0, t0);
    gain.gain.linearRampToValueAtTime(peak, t0 + 0.006);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
    oscillator.connect(gain);
    gain.connect(out);
    oscillator.start(t0);
    oscillator.stop(t0 + duration + 0.02);
  }

  function sound(kind) {
    if (!prefs.sounds) return;
    // Hover never starts audio by itself: browsers only allow it after a click.
    if (kind === "hover" ? !ui.ctx || ui.ctx.state !== "running" : !uiContext()) return;
    if (kind === "hover") {
      tone(2400, 2000, 0, 0.04, 0.02);
    } else if (kind === "open") {
      tone(523.25, 523.25, 0, 0.18, 0.035);
      tone(783.99, 783.99, 0.06, 0.2, 0.03);
      tone(1046.5, 1046.5, 0.12, 0.35, 0.028);
    } else if (kind === "close") {
      tone(1046.5, 1046.5, 0, 0.12, 0.025);
      tone(659.25, 659.25, 0.05, 0.22, 0.025);
    } else {
      tone(900, 1350, 0, 0.06, 0.035);
      tone(1800, 1800, 0.012, 0.05, 0.01, "triangle");
    }
  }

  // Ambient music: a slowly swelling pad with occasional chimes, synthesized in the browser

  let music = null;

  function startMusic() {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return false;
    const ctx = new AudioContext();
    const master = ctx.createGain();
    master.gain.setValueAtTime(0, ctx.currentTime);
    master.gain.linearRampToValueAtTime(prefs.volume * 0.5, ctx.currentTime + 4);
    master.connect(ctx.destination);

    const lowpass = ctx.createBiquadFilter();
    lowpass.type = "lowpass";
    lowpass.frequency.value = 900;
    lowpass.Q.value = 0.6;
    lowpass.connect(master);
    const sweep = ctx.createOscillator();
    const sweepAmount = ctx.createGain();
    sweep.frequency.value = 0.04;
    sweepAmount.gain.value = 450;
    sweep.connect(sweepAmount);
    sweepAmount.connect(lowpass.frequency);
    sweep.start();

    const delay = ctx.createDelay(2);
    const feedback = ctx.createGain();
    const delayFilter = ctx.createBiquadFilter();
    const wet = ctx.createGain();
    delay.delayTime.value = 0.48;
    feedback.gain.value = 0.5;
    delayFilter.type = "lowpass";
    delayFilter.frequency.value = 2200;
    wet.gain.value = 0.4;
    delay.connect(delayFilter);
    delayFilter.connect(feedback);
    feedback.connect(delay);
    delayFilter.connect(wet);
    wet.connect(master);

    [73.42, 110, 146.83, 174.61, 220, 261.63, 329.63].forEach((frequency, i) => {
      const gain = ctx.createGain();
      gain.gain.value = 0.05 / (1 + i * 0.15);
      const swell = ctx.createOscillator();
      const swellAmount = ctx.createGain();
      swell.frequency.value = 0.025 + Math.random() * 0.05;
      swellAmount.gain.value = gain.gain.value * 0.8;
      swell.connect(swellAmount);
      swellAmount.connect(gain.gain);
      swell.start();
      [-6, 6].forEach((detune, k) => {
        const oscillator = ctx.createOscillator();
        oscillator.type = k ? "triangle" : "sine";
        oscillator.frequency.value = frequency;
        oscillator.detune.value = detune;
        oscillator.connect(gain);
        oscillator.start();
      });
      gain.connect(lowpass);
      gain.connect(delay);
    });

    const bells = [587.33, 659.25, 698.46, 880, 1046.5, 1174.66];
    const chime = () => {
      if (!music) return;
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      const t0 = ctx.currentTime;
      oscillator.type = "sine";
      oscillator.frequency.value = bells[Math.floor(Math.random() * bells.length)];
      gain.gain.setValueAtTime(0, t0);
      gain.gain.linearRampToValueAtTime(0.03, t0 + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 3.5);
      oscillator.connect(gain);
      gain.connect(master);
      gain.connect(delay);
      oscillator.start(t0);
      oscillator.stop(t0 + 3.6);
      music.chime = setTimeout(chime, 2500 + Math.random() * 4500);
    };
    music = { ctx, master, chime: setTimeout(chime, 3000) };
    return true;
  }

  function stopMusic() {
    if (!music) return;
    const { ctx, master, chime } = music;
    music = null;
    clearTimeout(chime);
    const t = ctx.currentTime;
    master.gain.cancelScheduledValues(t);
    master.gain.setValueAtTime(master.gain.value, t);
    master.gain.linearRampToValueAtTime(0, t + 1.5);
    setTimeout(() => ctx.close().catch(() => {}), 1700);
  }

  // YouTube music: the official IFrame player API, loaded only when the music starts. YouTube
  // requires its player to stay visible, so it plays in the small player in the corner.

  const youtube = { player: null, api: null, key: null };

  function loadYouTubeApi() {
    if (!youtube.api) {
      youtube.api = new Promise((resolve) => {
        window.onYouTubeIframeAPIReady = () => resolve(window.YT);
        const script = document.createElement("script");
        script.src = "https://www.youtube.com/iframe_api";
        document.head.append(script);
      });
    }
    return youtube.api;
  }

  async function startYouTube(source) {
    document.querySelector(".music-player").hidden = false;
    document.getElementById("media-dock")?.classList.add("is-playing");
    music = { youtube: true };
    const key = `${source.video || ""}/${source.playlist || ""}`;
    if (youtube.player && youtube.key === key) {
      youtube.player.playVideo();
      return;
    }
    // Another video or playlist was chosen on Integrations → Music since the last time.
    if (youtube.player) {
      youtube.player.destroy();
      youtube.player = null;
      const placeholder = document.createElement("div");
      placeholder.id = "youtube-player";
      if (!document.getElementById("youtube-player")) document.querySelector(".music-player").append(placeholder);
    }
    youtube.key = key;
    const YT = await loadYouTubeApi();
    const playerVars = { autoplay: 1, playsinline: 1, rel: 0, modestbranding: 1 };
    if (source.playlist) {
      playerVars.listType = "playlist";
      playerVars.list = source.playlist;
    }
    youtube.player = new YT.Player("youtube-player", {
      host: "https://www.youtube-nocookie.com",
      videoId: source.video || undefined,
      playerVars,
      events: {
        onReady: (event) => {
          event.target.setVolume(Math.round(prefs.volume * 100));
          event.target.playVideo();
        },
      },
    });
  }

  function stopYouTube() {
    youtube.player?.pauseVideo();
    document.querySelector(".music-player").hidden = true;
    music = null;
  }


  // The dock

  function render() {
    const dock = document.getElementById("media-dock");
    if (!dock) return;
    dock.classList.toggle("is-playing", Boolean(music));
    const toggle = (name) => dock.querySelector(`[data-toggle="${name}"]`);
    toggle("sounds")?.setAttribute("aria-pressed", String(prefs.sounds));
    toggle("music")?.setAttribute("aria-pressed", String(Boolean(music)));
    dock.querySelector("[data-music-label]").textContent = `Music ${music ? "on" : "off"}`;
    dock.querySelector(".eq").classList.toggle("on", Boolean(music));
    // The slider opens from its own button, above the dock.
    if (!music) volumeOpen = false;
    dock.querySelector("[data-toggle='volume']").hidden = !music;
    dock.querySelector("[data-toggle='volume']").setAttribute("aria-expanded", String(volumeOpen));
    dock.querySelector(".volume").hidden = !volumeOpen;
    dock.querySelector("[data-volume]").value = Math.round(prefs.volume * 100);
    // ✕ on the player only hides it (visibility, so the music keeps playing); the dock's
    // video button brings it back.
    const youtubeOn = Boolean(music?.youtube);
    dock.querySelector(".music-player").hidden = !youtubeOn;
    dock.querySelector(".music-player").classList.toggle("is-hidden", Boolean(prefs.videoHidden));
    dock.querySelector(".dock-controls [data-toggle='video']").hidden = !(youtubeOn && prefs.videoHidden);
  }

  let volumeOpen = false;

  async function toggleMusic() {
    if (music?.youtube) return stopYouTube();
    if (music) return stopMusic();
    const config = await loadMusicConfig();
    if (config.source === "youtube" && (config.video || config.playlist)) startYouTube(config);
    else startMusic();
  }

  async function onToggle(name) {
    if (name === "sounds") {
      prefs.sounds = !prefs.sounds;
      if (prefs.sounds) uiContext();
    } else if (name === "volume") {
      volumeOpen = !volumeOpen;
    } else if (name === "video") {
      prefs.videoHidden = !prefs.videoHidden;
    } else if (name === "music") {
      await toggleMusic();
    }
    savePrefs();
    render();
  }

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("#media-dock [data-toggle]");
    if (!toggle) return;
    onToggle(toggle.dataset.toggle).then(() => sound("click"));
  });

  // A click elsewhere closes the volume slider.
  document.addEventListener("click", (event) => {
    if (!volumeOpen || event.target.closest(".music")) return;
    volumeOpen = false;
    render();
  });

  document.addEventListener("input", (event) => {
    if (!event.target.matches("[data-volume]")) return;
    prefs.volume = Number(event.target.value) / 100;
    savePrefs();
    if (music?.youtube) youtube.player?.setVolume(Math.round(prefs.volume * 100));
    else if (music) music.master.gain.setTargetAtTime(prefs.volume * 0.5, music.ctx.currentTime, 0.1);
  });

  document.addEventListener("DOMContentLoaded", () => loadMusicConfig());
  // After moving to another page, the dock is the same element; show the page's state.
  document.addEventListener("htmx:load", () => render());

  window.jarvisMedia = { prefs, sound };
})();
