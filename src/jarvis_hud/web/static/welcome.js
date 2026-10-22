// Welcome screen: clock, the animated point-cloud orb, details dialogs, interface sounds,
// ambient music and pause. Data comes from the server (HTMX); this only adds behavior.

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

  const prefs = { sounds: false, volume: 0.45, paused: false, ...loadPrefs() };

  // Clock and greeting

  const pad = (n) => String(n).padStart(2, "0");
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function greetingFor(hour) {
    if (hour >= 5 && hour < 12) return "Good morning";
    if (hour >= 12 && hour < 18) return "Good afternoon";
    return "Good evening";
  }

  function tick() {
    const now = new Date();
    const set = (selector, text) => {
      const element = document.querySelector(selector);
      if (element && element.textContent !== text) element.textContent = text;
    };
    set("[data-clock-hhmm]", `${pad(now.getHours())}:${pad(now.getMinutes())}`);
    set("[data-clock-ss]", `:${pad(now.getSeconds())}`);
    set("[data-clock-date]", `${DAYS[now.getDay()]} · ${now.getDate()} ${MONTHS[now.getMonth()]} ${now.getFullYear()}`);
    set("[data-greeting]", greetingFor(now.getHours()));
  }

  // The orb: a deterministic 3D point cloud, slowly turning, with signals running along edges

  function buildOrb() {
    let seed = 20261021;
    const random = () => {
      seed |= 0;
      seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const points = [];
    for (let i = 0; i < 850; i++) {
      const u = random() * 2 - 1;
      const theta = random() * Math.PI * 2;
      const shell = random() < 0.6;
      const radius = shell ? 94 * (0.9 + 0.1 * random()) : 94 * Math.pow(random(), 0.75);
      const q = Math.sqrt(1 - u * u);
      const amplitude = 2 + random() * 5;
      points.push({
        x: radius * q * Math.cos(theta), y: radius * u, z: radius * q * Math.sin(theta),
        ax: amplitude * (random() * 2 - 1), ay: amplitude * (random() * 2 - 1), az: amplitude * (random() * 2 - 1),
        fx: 0.25 + random() * 0.9, fy: 0.25 + random() * 0.9, fz: 0.25 + random() * 0.9,
        px: random() * 6.283, py: random() * 6.283, pz: random() * 6.283,
      });
    }
    const edges = [];
    const maxDistance = 15 * 15;
    points.forEach((a, i) => {
      const near = [];
      for (let j = i + 1; j < points.length; j++) {
        const b = points[j];
        const d2 = (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;
        if (d2 < maxDistance) near.push({ j, d2 });
      }
      near.sort((m, n) => m.d2 - n.d2);
      for (let k = 0; k < Math.min(3, near.length); k++) edges.push([i, near[k].j]);
    });
    const neighbors = points.map(() => []);
    edges.forEach(([i, j]) => {
      neighbors[i].push(j);
      neighbors[j].push(i);
    });
    return { points, edges, neighbors };
  }

  const orb = { model: null, angle: 0.6, time: 0, signals: [], flashes: [], paths: {} };

  function stepSignals() {
    const { neighbors } = orb.model;
    const pick = (list) => list[Math.floor(Math.random() * list.length)];
    const signals = orb.signals;
    for (let k = signals.length - 1; k >= 0; k--) {
      const signal = signals[k];
      signal.p += signal.v;
      if (signal.p < 1) continue;
      orb.flashes.push({ n: signal.b, age: 0 });
      const next = neighbors[signal.b].filter((n) => n !== signal.a);
      if (next.length && signal.hops < 8 && Math.random() < 0.82) {
        signals[k] = { a: signal.b, b: pick(next), p: 0, v: signal.v, hops: signal.hops + 1 };
        if (Math.random() < 0.12 && next.length > 1) {
          signals.push({ a: signal.b, b: pick(next), p: 0, v: signal.v * 0.9, hops: signal.hops + 2 });
        }
      } else {
        signals.splice(k, 1);
      }
    }
    let tries = 0;
    while (signals.length < 14 && tries++ < 50) {
      const a = Math.floor(Math.random() * neighbors.length);
      if (!neighbors[a].length) continue;
      signals.push({ a, b: pick(neighbors[a]), p: 0, v: 0.018 + Math.random() * 0.02, hops: 0 });
      orb.flashes.push({ n: a, age: 0 });
    }
    for (let k = orb.flashes.length - 1; k >= 0; k--) {
      orb.flashes[k].age += 1;
      if (orb.flashes[k].age > 16) orb.flashes.splice(k, 1);
    }
  }

  function drawOrb() {
    const { points, edges } = orb.model;
    const ca = Math.cos(orb.angle);
    const sa = Math.sin(orb.angle);
    const tilt = 0.38;
    const ct = Math.cos(tilt);
    const st = Math.sin(tilt);
    const t = orb.time;
    const projected = points.map((p) => {
      const x = p.x + p.ax * Math.sin(t * p.fx + p.px);
      const y = p.y + p.ay * Math.sin(t * p.fy + p.py);
      const z = p.z + p.az * Math.sin(t * p.fz + p.pz);
      const x1 = x * ca + z * sa;
      const z1 = -x * sa + z * ca;
      return { x: x1, y: y * ct - z1 * st, z: y * st + z1 * ct };
    });
    const f = (n) => n.toFixed(1);
    const dot = (p) => `M${f(p.x)} ${f(p.y)}h.01`;
    const d = { backDots: "", frontDots: "", hotDots: "", backLines: "", frontLines: "", trails: "", heads: "", flashNew: "", flashOld: "" };
    projected.forEach((p, i) => {
      if (p.z < 0) {
        d.backDots += dot(p);
      } else {
        d.frontDots += dot(p);
        if (i % 29 === 0) d.hotDots += dot(p);
      }
    });
    edges.forEach(([i, j]) => {
      const a = projected[i];
      const b = projected[j];
      const line = `M${f(a.x)} ${f(a.y)}L${f(b.x)} ${f(b.y)}`;
      if (a.z + b.z < 0) d.backLines += line;
      else d.frontLines += line;
    });
    orb.signals.forEach((s) => {
      const a = projected[s.a];
      const b = projected[s.b];
      const x = a.x + (b.x - a.x) * s.p;
      const y = a.y + (b.y - a.y) * s.p;
      d.trails += `M${f(a.x)} ${f(a.y)}L${f(x)} ${f(y)}`;
      d.heads += `M${f(x)} ${f(y)}h.01`;
    });
    orb.flashes.forEach((flash) => {
      const p = projected[flash.n];
      if (flash.age < 6) d.flashNew += dot(p);
      else d.flashOld += dot(p);
    });
    for (const [name, value] of Object.entries(d)) {
      orb.paths[name]?.setAttribute("d", value);
    }
  }

  function startOrb() {
    document.querySelectorAll("[data-orb]").forEach((path) => {
      orb.paths[path.dataset.orb] = path;
    });
    orb.model = buildOrb();
    drawOrb();
    if (reducedMotion) return;
    setInterval(() => {
      if (prefs.paused || document.hidden) return;
      stepSignals();
      orb.angle = (orb.angle + 0.006) % (Math.PI * 2);
      orb.time += 0.05;
      drawOrb();
    }, 50);
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

  const youtube = { player: null, api: null };

  function musicSource() {
    const button = document.querySelector('button[data-toggle="music"][data-source]');
    return button?.dataset.source === "youtube" ? button.dataset : null;
  }

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
    music = { youtube: true };
    if (youtube.player) {
      youtube.player.playVideo();
      return;
    }
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

  // Header toggles

  function render() {
    const page = document.getElementById("welcome");
    page.classList.toggle("paused", prefs.paused);
    const toggle = (name) => document.querySelector(`[data-toggle="${name}"]`);
    toggle("sounds").setAttribute("aria-pressed", String(prefs.sounds));
    toggle("pause").setAttribute("aria-pressed", String(prefs.paused));
    document.querySelector("[data-pause-label]").textContent = prefs.paused ? "Resume" : "Pause";
    toggle("music").setAttribute("aria-pressed", String(Boolean(music)));
    const name = musicSource() ? "YouTube" : "Ambient";
    document.querySelector("[data-music-label]").textContent = `${name} ${music ? "on" : "off"}`;
    document.querySelector(".eq").classList.toggle("on", Boolean(music));
    document.querySelector(".volume").hidden = !music;
  }

  function onToggle(name) {
    if (name === "sounds") {
      prefs.sounds = !prefs.sounds;
      if (prefs.sounds) uiContext();
    } else if (name === "pause") {
      prefs.paused = !prefs.paused;
    } else if (name === "music" && musicSource()) {
      if (music) stopYouTube();
      else startYouTube(musicSource());
    } else if (name === "music") {
      if (music) stopMusic();
      else startMusic();
    }
    savePrefs();
    render();
  }

  // Details dialogs

  function dialogs() {
    return [...document.querySelectorAll("dialog[data-detail]")];
  }

  function openDialog(dialog) {
    dialogs().forEach((other) => other !== dialog && other.open && other.close());
    document.querySelectorAll(".node[aria-expanded]").forEach((node) => node.removeAttribute("aria-expanded"));
    const node = document.querySelector(`[data-open="${dialog.id}"]`);
    node?.setAttribute("aria-expanded", "true");
    if (!dialog.open) dialog.showModal();
  }

  function stepDialog(current, step) {
    const all = dialogs();
    const next = all[(all.indexOf(current) + step + all.length) % all.length];
    if (next && next !== current) openDialog(next);
  }

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle]");
    const opener = event.target.closest("[data-open]");
    const dialog = event.target.closest("dialog[data-detail]");
    if (toggle) {
      onToggle(toggle.dataset.toggle);
      sound("click");
    } else if (opener) {
      openDialog(document.getElementById(opener.dataset.open));
      sound("open");
    } else if (event.target.closest("[data-step]")) {
      stepDialog(dialog, Number(event.target.closest("[data-step]").dataset.step));
      sound("click");
    } else if (event.target.closest("[data-open-report]")) {
      dialogs().forEach((other) => other.open && other.close());
      document.getElementById("report").showModal();
      sound("open");
    } else if (event.target.closest("[data-read-aloud]")) {
      readAloud(event.target.closest("[data-read-aloud]"));
    } else if (event.target.closest("[data-close]") || event.target.matches("dialog")) {
      // A click on the dialog element itself is a click on its backdrop.
      event.target.closest("dialog").close();
    } else if (event.target.closest("a, button")) {
      sound("click");
    }
  });

  document.addEventListener("keydown", (event) => {
    const dialog = document.querySelector("dialog[data-detail][open]");
    if (!dialog || event.target.closest("input, textarea")) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      stepDialog(dialog, event.key === "ArrowLeft" ? -1 : 1);
      sound("click");
    }
  });

  // Read aloud: the browser's own speech synthesis reads the brief's summary.

  function readAloud(button) {
    if (!("speechSynthesis" in window)) return;
    if (speechSynthesis.speaking) {
      speechSynthesis.cancel();
      return;
    }
    const text = document.querySelector("[data-report-text]")?.innerText;
    if (!text) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = button.dataset.lang || "en-GB";
    const reset = () => {
      button.setAttribute("aria-pressed", "false");
      button.textContent = "▶ Read aloud";
    };
    utterance.onend = reset;
    utterance.onerror = reset;
    speechSynthesis.speak(utterance);
    button.setAttribute("aria-pressed", "true");
    button.textContent = "■ Stop reading";
  }

  document.addEventListener(
    "close",
    (event) => {
      if (event.target.id === "report") {
        if ("speechSynthesis" in window) speechSynthesis.cancel();
        sound("close");
        return;
      }
      if (!event.target.matches("dialog[data-detail]")) return;
      if (!document.querySelector("dialog[data-detail][open]")) {
        document.querySelectorAll(".node[aria-expanded]").forEach((node) => node.removeAttribute("aria-expanded"));
        sound("close");
      }
    },
    true,
  );

  let lastHover = null;
  document.addEventListener("mouseover", (event) => {
    const target = event.target.closest("[data-sfx], .btn, .icon-btn");
    if (target && target !== lastHover) sound("hover");
    lastHover = target;
  });

  document.addEventListener("input", (event) => {
    if (!event.target.matches("[data-volume]")) return;
    prefs.volume = Number(event.target.value) / 100;
    savePrefs();
    if (music?.youtube) youtube.player?.setVolume(Math.round(prefs.volume * 100));
    else if (music) music.master.gain.setTargetAtTime(prefs.volume * 0.5, music.ctx.currentTime, 0.1);
  });

  // Periodic refreshes must not replace the numbers while their details are open...
  let reopen = null;
  document.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target.id !== "orb-widgets") return;
    const open = document.querySelector("dialog[data-detail][open]");
    if (open && reopen === null) event.detail.shouldSwap = false;
  });

  // ...but after a change made in a dialog (a task added or done), the widgets reload and
  // the same dialog opens again with the new data.
  document.body.addEventListener("jarvis:refresh", () => {
    const open = document.querySelector("dialog[data-detail][open]");
    reopen = open ? open.id : "";
    const focused = document.activeElement?.id;
    htmx.ajax("GET", "/welcome/widgets", { target: "#orb-widgets", swap: "innerHTML" }).then(() => {
      const dialog = reopen && document.getElementById(reopen);
      reopen = null;
      if (!dialog) return;
      openDialog(dialog);
      if (focused) document.getElementById(focused)?.focus();
    });
  });

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelector("[data-volume]").value = Math.round(prefs.volume * 100);
    render();
    tick();
    setInterval(tick, 1000);
    startOrb();
  });
})();
