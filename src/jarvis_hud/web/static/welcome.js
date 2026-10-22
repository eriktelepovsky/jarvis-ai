// Welcome screen: clock, the animated point-cloud orb, details dialogs and the report. Data
// comes from the server (HTMX); this only adds behavior. Sound and music are in media.js.
// Loaded on every page: with hx-boost, the welcome screen can appear without a page load.

(() => {
  "use strict";

  const media = () => window.jarvisMedia;
  const sound = (kind) => media()?.sound(kind);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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

  let orbTimer = null;

  function startOrb() {
    // The welcome screen may be shown again without a page load: bind to its new paths.
    orb.paths = {};
    document.querySelectorAll("[data-orb]").forEach((path) => {
      orb.paths[path.dataset.orb] = path;
    });
    orb.model ??= buildOrb();
    drawOrb();
    if (reducedMotion || orbTimer) return;
    orbTimer = setInterval(() => {
      if (document.hidden || !document.getElementById("welcome")) return;
      stepSignals();
      orb.angle = (orb.angle + 0.006) % (Math.PI * 2);
      orb.time += 0.05;
      drawOrb();
    }, 50);
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
    const opener = event.target.closest("[data-open]");
    const dialog = event.target.closest("dialog[data-detail]");
    if (event.target.closest("#media-dock")) {
      return;
    } else if (opener && document.getElementById(opener.dataset.open)) {
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

  // "/" jumps to the command line, as in many apps.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const input = document.getElementById("command-input");
    if (!input || document.querySelector("dialog[open]")) return;
    event.preventDefault();
    input.focus();
  });

  // The confirmation under the command line fades after a while.
  let commandTimer = null;
  document.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target.id !== "command-result") return;
    clearTimeout(commandTimer);
    commandTimer = setTimeout(() => {
      event.detail.target.textContent = "";
    }, 6000);
  });

  document.addEventListener("keydown", (event) => {
    const dialog = document.querySelector("dialog[data-detail][open]");
    if (!dialog || event.target.closest("input, textarea")) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      stepDialog(dialog, event.key === "ArrowLeft" ? -1 : 1);
      sound("click");
    }
  });

  // Read aloud: Azure Speech's recording of the summary when it is set up on Integrations →
  // Voice, otherwise the browser's own voices (see speech.js), in the language chosen.

  // The language chosen to read in, as a speech tag ("sk-SK").
  function readingLanguage() {
    const option = document.querySelector("[data-read-language]")?.selectedOptions[0];
    return { code: option?.value || "en", tag: option?.dataset.tag || "en-GB" };
  }

  // With the browser's voices, offer only languages this device has a voice for.
  function filterLanguages() {
    const button = document.querySelector("[data-read-aloud]");
    if (!button || button.dataset.voice === "azure" || !window.jarvisSpeech) return;
    document.querySelectorAll("[data-read-language] option").forEach((option) => {
      option.hidden =
        option.value !== button.dataset.briefLanguage && !window.jarvisSpeech.voicesFor(option.dataset.tag).length;
    });
  }

  if ("speechSynthesis" in window) speechSynthesis.addEventListener("voiceschanged", filterLanguages);
  document.addEventListener("htmx:load", filterLanguages);

  let reading = null; // { stop() } while reading

  function stopReading() {
    reading?.stop();
    reading = null;
  }

  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-read-language]")) stopReading();
  });

  // The summary in the chosen language: from the page, or translated by Claude (once per
  // brief and language; Jarvis keeps the translation).
  async function textToRead(button, language) {
    if (language.code === button.dataset.briefLanguage) {
      return document.querySelector("[data-report-text]")?.innerText;
    }
    const body = new URLSearchParams({ language: language.code });
    const response = await fetch(`/brief/${button.dataset.brief}/translation`, { method: "POST", body });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The translation did not work.");
    return data.text;
  }

  async function readAloud(button) {
    const reset = () => {
      button.setAttribute("aria-pressed", "false");
      button.textContent = "▶ Read aloud";
      reading = null;
    };
    const showError = (message) => {
      reset();
      document.querySelector("[data-read-error]")?.replaceChildren(message);
    };
    if (reading) {
      stopReading();
      return reset();
    }
    document.querySelector("[data-read-error]")?.replaceChildren();
    const language = readingLanguage();
    button.setAttribute("aria-pressed", "true");
    button.textContent = "… Preparing";

    if (button.dataset.voice === "azure") {
      const audio = new Audio(`/brief/${button.dataset.brief}/audio?language=${language.code}`);
      reading = { stop: () => audio.pause() };
      audio.addEventListener("playing", () => {
        button.textContent = "■ Stop reading";
      });
      audio.addEventListener("ended", reset);
      audio.addEventListener("error", () => showError("Azure Speech could not read the brief. Check Integrations → Voice."));
      audio.play().catch(() => {});
      return;
    }

    reading = { stop: () => speechSynthesis.cancel() };
    let text;
    try {
      text = await textToRead(button, language);
    } catch (error) {
      return showError(error.message);
    }
    if (!reading || !text) return reset();
    button.textContent = "■ Stop reading";
    await window.jarvisSpeech.speak(text, language.tag);
    if (reading) reset();
  }



  document.addEventListener(
    "close",
    (event) => {
      if (event.target.id === "report") {
        stopReading();
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
    if (!document.getElementById("orb-widgets")) return;
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

  let clockTimer = null;
  let welcomePage = null;

  function initWelcome() {
    const page = document.getElementById("welcome");
    // A page restored from history is a new element, even with the same id.
    if (!page || page === welcomePage) return;
    welcomePage = page;
    tick();
    clockTimer ??= setInterval(tick, 1000);
    startOrb();
  }

  document.addEventListener("DOMContentLoaded", initWelcome);
  // Coming back to the welcome screen from another page (hx-boost) or with the browser's Back
  // button loads no new page. (htmx:afterSettle would fire on the replaced, detached #app.)
  document.addEventListener("htmx:load", initWelcome);
  document.addEventListener("htmx:historyRestore", initWelcome);
})();
