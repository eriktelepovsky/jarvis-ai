// Welcome screen: clock, the animated point-cloud orb, details dialogs and the report. Data
// comes from the server (HTMX); this only adds behavior. Sound and music are in media.js.
// Loaded on every page: with hx-boost, the welcome screen can appear without a page load.

(() => {
  "use strict";

  const media = () => window.myorbMedia;
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

  // A number in the details filters the list next to it; clicking it again shows everything.
  // ...and the list can be narrowed to one group (a Jira workspace, a repository); both apply.
  // Lower case without diacritics, so "zaznam" finds "Záznam".
  const fold = (text) => text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

  function applyFilters(dialog) {
    const pressed = dialog.querySelector('[data-filter][aria-pressed="true"]');
    const chosen = dialog.querySelector('[data-group-value][aria-pressed="true"]');
    const group = chosen?.dataset.groupValue || "";
    const subgroup = chosen?.dataset.subgroupValue || "";
    const words = fold(dialog.querySelector("[data-list-search]")?.value || "").split(/\s+/).filter(Boolean);
    let shown = 0;
    // The numbers follow the other filters: a group counts what the pressed number and the
    // search leave, a number what the chosen group and the search leave.
    const groupCounts = new Map();
    const tagCounts = new Map();
    dialog.querySelectorAll(".modal-row").forEach((row) => {
      const tags = row.dataset.tags.split(" ");
      const tagged = !pressed || tags.includes(pressed.dataset.filter);
      row.dataset.text ??= fold(row.textContent);
      const found = words.every((word) => row.dataset.text.includes(word));
      const inGroup = (!group || row.dataset.group === group) && (!subgroup || row.dataset.subgroup === subgroup);
      row.hidden = !tagged || !inGroup || !found;
      if (!row.hidden) shown += 1;
      if (tagged && found) {
        for (const key of ["", row.dataset.group, `${row.dataset.group}\n${row.dataset.subgroup}`]) {
          groupCounts.set(key, (groupCounts.get(key) || 0) + 1);
        }
      }
      if (inGroup && found) tags.forEach((tag) => tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1));
    });
    dialog.querySelectorAll("[data-group-value]").forEach((button) => {
      const { groupValue, subgroupValue } = button.dataset;
      const count = groupCounts.get(subgroupValue ? `${groupValue}\n${subgroupValue}` : groupValue) || 0;
      button.querySelector(".group-count").textContent = count;
      button.classList.toggle("is-empty", !count);
    });
    // Without a group or search, a number shows what the service said (it may count more
    // than the list holds, e.g. Gmail's unread beyond the newest listed).
    const narrowed = Boolean(group || words.length);
    dialog.querySelectorAll("[data-filter]").forEach((button) => {
      const number = button.querySelector(".num");
      number.dataset.total ??= number.textContent;
      number.textContent = narrowed ? tagCounts.get(button.dataset.filter) || 0 : number.dataset.total;
    });
    const empty = dialog.querySelector("[data-list-empty]");
    if (empty) empty.hidden = shown > 0;
    const head = dialog.querySelector("[data-list-title]");
    if (head) {
      const label = pressed?.querySelector(".label").textContent;
      head.textContent = label ? `${label} · click again to show all` : head.dataset.listTitle;
    }
  }

  function filterRows(button) {
    const dialog = button.closest("dialog");
    const active = button.getAttribute("aria-pressed") !== "true";
    dialog.querySelectorAll("[data-filter]").forEach((other) => other.setAttribute("aria-pressed", "false"));
    button.setAttribute("aria-pressed", String(active));
    applyFilters(dialog);
  }

  document.addEventListener("input", (event) => {
    if (event.target.matches("[data-list-search]")) applyFilters(event.target.closest("dialog"));
  });

  function filterGroup(button) {
    const dialog = button.closest("dialog");
    dialog.querySelectorAll("[data-group-value]").forEach((other) => other.setAttribute("aria-pressed", "false"));
    button.setAttribute("aria-pressed", "true");
    applyFilters(dialog);
  }

  document.addEventListener("click", (event) => {
    // A link that also changes something here (opening a video marks it seen): refresh after.
    if (event.target.closest("a[data-refresh-after]")) {
      setTimeout(() => htmx.trigger(document.body, "myorb:refresh"), 1500);
      return;
    }
    const filter = event.target.closest("dialog [data-filter]");
    if (filter) {
      filterRows(filter);
      sound("click");
      return;
    }
    const group = event.target.closest("dialog [data-group-value]");
    if (group) {
      filterGroup(group);
      sound("click");
      return;
    }
  });

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

  // "/" jumps to the command line, as in many apps; in open details, to the list's search.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const dialog = document.querySelector("dialog[data-detail][open]");
    const input = dialog ? dialog.querySelector("[data-list-search]") : document.getElementById("command-input");
    if (!input || (!dialog && document.querySelector("dialog[open]"))) return;
    event.preventDefault();
    input.focus();
  });

  // Ask: a question in the command line (ending or starting with "?") opens the Ask dialog,
  // where Claude answers from the dashboard as it writes. The conversation is kept for the tab.
  const ASK_KEY = "myorb.ask";
  let ask = { conversation: null, messages: [] };
  try {
    ask = JSON.parse(sessionStorage.getItem(ASK_KEY)) || ask;
  } catch {
    // Storage unavailable: a new conversation.
  }
  const saveAsk = () => {
    try {
      sessionStorage.setItem(ASK_KEY, JSON.stringify(ask));
    } catch {
      // Storage unavailable: kept until the page reloads.
    }
  };
  const isQuestion = (text) => /^\?|\?\s*$/.test(text.trim());

  const escapeHtml = (text) =>
    text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // Plain text with "- " lists and **bold**, as the prompt asks; everything else is escaped.
  function renderAnswer(text) {
    return text
      .split(/\n{2,}/)
      .map((block) => {
        const lines = block.split("\n").filter((line) => line.trim());
        const inline = (line) => escapeHtml(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        if (lines.length && lines.every((line) => /^\s*[-•]\s/.test(line))) {
          return `<ul>${lines.map((line) => `<li>${inline(line.replace(/^\s*[-•]\s/, ""))}</li>`).join("")}</ul>`;
        }
        return `<p>${lines.map(inline).join("<br>")}</p>`;
      })
      .join("");
  }

  function askBubble(role, text, state = "") {
    const bubble = document.createElement("div");
    bubble.className = `ask-message ask-${role}${state ? ` is-${state}` : ""}`;
    if (role === "user") bubble.textContent = text;
    else bubble.innerHTML = text ? renderAnswer(text) : "";
    return bubble;
  }

  function showAsk() {
    const dialog = document.getElementById("ask");
    if (!dialog) return null;
    const log = dialog.querySelector("[data-ask-log]");
    log.replaceChildren(...ask.messages.map((m) => askBubble(m.role, m.content)));
    if (!dialog.open) {
      dialogs().forEach((other) => other.open && other.close());
      dialog.showModal();
    }
    log.scrollTop = log.scrollHeight;
    return dialog;
  }

  // A line from the command line that was meant as a task (it just ends with "?").
  function offerTask(log, text) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ask-as-task";
    button.textContent = "Add as a task instead";
    button.addEventListener("click", async () => {
      const body = new FormData();
      body.set("text", text);
      const response = await fetch("/command", { method: "POST", body, headers: { "HX-Request": "true" } });
      button.disabled = true;
      button.textContent = response.ok ? "Added as a task." : "The task could not be added.";
      if (response.ok) htmx.trigger(document.body, "myorb:refresh");
    });
    log.append(button);
  }

  // Reads a stream of JSON lines from /ask or /ask/decide into the log: the answer's text, the
  // actions Claude proposes (cards to confirm) and what the confirmed ones did.
  async function askStream(dialog, url, body) {
    const log = dialog.querySelector("[data-ask-log]");
    let bubble = askBubble("assistant", "", "thinking");
    bubble.innerHTML = '<span class="ask-thinking">Looking at your dashboard…</span>';
    log.append(bubble);
    log.scrollTop = log.scrollHeight;
    dialog.dataset.busy = "true";
    let text = "";
    let error = null;
    const proposals = [];
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines.filter(Boolean)) {
          const data = JSON.parse(line);
          if (data.conversation) ask.conversation = data.conversation;
          if (data.error) error = data.error;
          if (data.refresh) htmx.trigger(document.body, "myorb:refresh");
          if (data.done) showDone(dialog, data.done);
          if (data.proposal) proposals.push(data.proposal);
          if (data.text) {
            if (!bubble.isConnected) {
              bubble = askBubble("assistant", "");
              log.append(bubble);
            }
            text += data.text;
            bubble.classList.remove("is-thinking");
            bubble.innerHTML = renderAnswer(text);
            log.scrollTop = log.scrollHeight;
          }
        }
      }
    } catch {
      error = "The answer could not be loaded. Try again.";
    }
    delete dialog.dataset.busy;
    if (!text) bubble.remove();
    if (text) ask.messages.push({ role: "assistant", content: text });
    if (error) {
      const note = askBubble("error", "");
      note.className = "ask-message ask-error";
      note.textContent = error;
      log.append(note);
    }
    if (proposals.length) showProposals(dialog, proposals);
    saveAsk();
    log.scrollTop = log.scrollHeight;
    dialog.querySelector("#ask-input")?.focus();
    return { text, error };
  }

  // An action Claude wants to take: the user does it or cancels it. When every card of the
  // turn is decided, the decisions go back and Claude goes on.
  function showProposals(dialog, proposals) {
    const log = dialog.querySelector("[data-ask-log]");
    // Claude took it as something to do: "Add as a task instead" would only repeat it.
    log.querySelectorAll(".ask-as-task").forEach((offer) => offer.remove());
    const decided = new Map();
    for (const proposal of proposals) {
      const card = document.createElement("div");
      card.className = "ask-action";
      card.dataset.proposal = proposal.id;
      card.innerHTML = `<span class="ask-action-text"></span>
        <span class="ask-action-buttons">
          <button type="button" class="btn btn-primary btn-small" data-run>Do it</button>
          <button type="button" class="btn btn-small" data-cancel>Cancel</button>
        </span>`;
      card.querySelector(".ask-action-text").textContent = proposal.summary;
      card.addEventListener("click", async (event) => {
        const run = event.target.closest("[data-run]");
        if (!run && !event.target.closest("[data-cancel]")) return;
        card.querySelectorAll("button").forEach((button) => (button.disabled = true));
        card.classList.add(run ? "is-approved" : "is-cancelled");
        decided.set(proposal.id, Boolean(run));
        if (decided.size < proposals.length) return;
        const approved = [...decided].filter(([, yes]) => yes).map(([id]) => id);
        await askStream(dialog, "/ask/decide", { conversation: ask.conversation, approved });
      });
      log.append(card);
    }
  }

  // What a confirmed action did, on its card; with Undo when it can be undone.
  function showDone(dialog, done) {
    const card = dialog.querySelector(`[data-proposal="${CSS.escape(done.id)}"]`);
    if (!card) return;
    const buttons = card.querySelector(".ask-action-buttons");
    const status = document.createElement("span");
    status.className = `ask-action-status${done.error ? " is-error" : done.cancelled ? " is-cancelled" : ""}`;
    status.textContent = done.cancelled ? "Cancelled" : done.error ? done.message : `✓ ${done.message}`;
    buttons.replaceChildren(status);
    if (done.undo) {
      const undo = document.createElement("button");
      undo.type = "button";
      undo.className = "btn btn-small";
      undo.textContent = "Undo";
      undo.addEventListener("click", async () => {
        undo.disabled = true;
        const response = await fetch("/ask/undo", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation: ask.conversation, id: done.undo }),
        });
        const data = await response.json().catch(() => ({}));
        status.textContent = data.message ? `↶ ${data.message}` : data.error || "Undoing it failed.";
        undo.remove();
        if (data.message) htmx.trigger(document.body, "myorb:refresh");
      });
      buttons.append(undo);
    }
  }

  async function askQuestion(question, { fromCommand = false } = {}) {
    const dialog = showAsk();
    if (!dialog || dialog.dataset.busy) return;
    const log = dialog.querySelector("[data-ask-log]");
    ask.messages.push({ role: "user", content: question });
    log.append(askBubble("user", question));
    if (fromCommand) offerTask(log, question);
    const { text, error } = await askStream(dialog, "/ask", {
      conversation: ask.conversation,
      messages: ask.messages,
    });
    // Nothing answered: the question is taken back, so asking again does not repeat it.
    if (error && !text) {
      ask.messages = ask.messages.filter((m, i) => !(i === ask.messages.length - 1 && m.role === "user"));
      saveAsk();
    }
  }

  // The command line: a question goes to Ask instead of becoming a task.
  document.addEventListener("htmx:beforeRequest", (event) => {
    const form = event.detail.elt;
    if (!form.matches?.(".command-bar[data-ask]")) return;
    const input = form.querySelector("#command-input");
    if (!isQuestion(input.value)) return;
    event.preventDefault();
    const question = input.value.trim().replace(/^\?\s*/, "");
    form.reset();
    askQuestion(question, { fromCommand: true });
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-ask-form]");
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector("input");
    if (!input.value.trim()) return;
    const question = input.value.trim();
    form.reset();
    askQuestion(question);
  });

  // A quick action asks its question in a new conversation.
  document.addEventListener("click", (event) => {
    const quick = event.target.closest("[data-ask-quick]");
    if (!quick) return;
    ask = { conversation: null, messages: [] };
    saveAsk();
    askQuestion(quick.dataset.askQuick);
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-ask-new]")) return;
    ask = { conversation: null, messages: [] };
    saveAsk();
    showAsk();
    document.getElementById("ask-input")?.focus();
  });

  // The activity list shows only as many events as fit on screen: under the clock, above the
  // YouTube window (or the music search) while it is shown, else above the dock's controls or
  // the window's edge. The rest, the oldest, fade out.
  // After the column's height has moved, check once more: the orb may have changed meanwhile.
  function fitActivity() {
    const list = document.querySelector('.side-right [hx-get="/activity"]');
    if (!list) return;
    const items = [...list.querySelectorAll(".activity-item")];
    const column = list.closest(".side-right");
    // Shown or not: the video is only made invisible (the music plays on), the search removed.
    const panels = [...document.querySelectorAll("#media-dock .music-player, #media-dock .music-finder")]
      .filter((panel) => getComputedStyle(panel).visibility !== "hidden")
      .map((panel) => panel.getBoundingClientRect())
      .filter((rect) => rect.height);
    const box = column.getBoundingClientRect();
    const across = (rect) => rect.height && rect.left < box.right && rect.right > box.left;
    const covering = panels.filter(across);
    // The column is centred beside the orb: it may grow as far as the nearer of the clock
    // above (with the next event) and, below, the YouTube window or the dock's controls (or
    // the window's edge); events that do not fit fade out (CSS animates the height).
    // Every part of the header above it: the next event's pill sits outside the clock's box.
    const above = [...document.querySelectorAll(".welcome-header *")]
      .map((el) => el.getBoundingClientRect())
      .filter(across);
    const top = Math.max(0, ...above.map((rect) => rect.bottom)) + 16;
    const controls = [...document.querySelectorAll("#media-dock .dock-controls")]
      .map((el) => el.getBoundingClientRect())
      .filter(across);
    const bottom = Math.min(window.innerHeight, ...[...covering, ...controls].map((rect) => rect.top)) - 16;
    const centre = box.top + box.height / 2;
    const room = 2 * Math.min(bottom - centre, centre - top);
    const padding = parseFloat(getComputedStyle(column).paddingBottom) || 0;
    let height = column.scrollHeight;
    if (height > room) {
      const fitting = items.filter((item) => item.getBoundingClientRect().bottom - box.top + padding <= room);
      const last = fitting.at(-1);
      height = last ? last.getBoundingClientRect().bottom - box.top + padding : list.getBoundingClientRect().top - box.top;
    }
    items.forEach((item) => {
      item.classList.toggle("is-cut", item.getBoundingClientRect().bottom - box.top + padding > height + 1);
    });
    column.style.maxHeight = `${Math.ceil(height)}px`;
  }

  document.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target.matches?.('[hx-get="/activity"]')) fitActivity();
  });
  window.addEventListener("resize", fitActivity);
  document.querySelector(".side-right")?.addEventListener("transitionend", (event) => {
    if (event.propertyName === "max-height") fitActivity();
  });
  document.fonts?.ready.then(fitActivity);
  // The YouTube window appears once its video loads, and is hidden and shown from the dock
  // (by a class: its size stays).
  const dockPanels = document.querySelectorAll("#media-dock, #media-dock .music-player, #media-dock .music-finder");
  if ("ResizeObserver" in window) {
    const watch = new ResizeObserver(fitActivity);
    dockPanels.forEach((el) => watch.observe(el));
  }
  const toggles = new MutationObserver(fitActivity);
  dockPanels.forEach((el) => toggles.observe(el, { attributes: true, attributeFilter: ["class", "hidden"] }));

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
    if (!button || button.dataset.voice === "azure" || !window.myorbSpeech) return;
    document.querySelectorAll("[data-read-language] option").forEach((option) => {
      option.hidden =
        option.value !== button.dataset.briefLanguage && !window.myorbSpeech.voicesFor(option.dataset.tag).length;
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
  // brief and language; myOrb keeps the translation).
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
    await window.myorbSpeech.speak(text, language.tag);
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

  // The numbers around the orb load one by one (each slot on its own). After each arrives,
  // the places around the orb, the dialogs' "n / m" and the status line are set again.
  function layoutOrb() {
    const widgets = document.getElementById("orb-widgets");
    if (!widgets) return;
    const places = [...widgets.querySelectorAll(".place")];
    const all = dialogs();
    all.forEach((dialog, index) => {
      const counter = dialog.querySelector("[data-counter]");
      if (counter) counter.textContent = `${index + 1} / ${all.length}`;
    });
    const loading = widgets.querySelectorAll(".node-loading").length;
    const nodes = [...widgets.querySelectorAll(".node[data-key]")];
    // Larger numbers than the others make a circle a little larger, up to a fifth for the
    // largest (on a log scale, so 134 emails do not dwarf 2 pull requests). What needs
    // attention (sized by the server) stays the largest.
    const numbers = nodes.map((node) => Math.max(0, Number(node.dataset.number) || 0));
    const most = Math.log1p(Math.max(0, ...numbers));
    nodes.forEach((node, index) => {
      const bonus = most ? (0.2 * Math.log1p(numbers[index])) / most : 0;
      node.style.setProperty("--s", Math.min(1.5, Number(node.dataset.size) * (1 + bonus)).toFixed(3));
    });
    // Each circle gets a share of the ring as large as itself, so large neighbours (several
    // that need attention) do not overlap while small ones sit closer. The first stays on top.
    const sizes = places.map((place) => Number(place.querySelector(".node")?.style.getPropertyValue("--s")) || 1);
    const total = sizes.reduce((sum, size) => sum + size, 0);
    let along = 0;
    places.forEach((place, index) => {
      place.style.setProperty("--angle", `${(360 * along) / total}deg`);
      along += (sizes[index] + (sizes[index + 1] ?? sizes[0])) / 2;
    });
    const loaded = nodes.filter((node) => node.dataset.hasValue === "true").length;
    const needs = nodes.filter((node) => node.dataset.needs === "true").length;
    const sources = needs === 1 ? "source needs" : "sources need";
    widgets.toggleAttribute("aria-busy", loading > 0);
    const status = document.getElementById("core-status");
    if (status) status.textContent = loading ? "Loading sources…" : `Online · ${loaded}/${nodes.length} sources`;
    const line = document.getElementById("core-line");
    if (line && !loading) {
      line.textContent = !loaded
        ? "Connect a service to get started"
        : needs
          ? `${needs} ${sources} attention`
          : "All systems nominal";
    }
    // The header says that something needs attention; a click lists what and how to fix it.
    const attention = document.getElementById("attention");
    if (attention && !loading) {
      attention.hidden = !needs;
      attention.textContent = `▲ ${needs} ${sources} attention`;
    }
  }

  function showAttention() {
    const dialog = document.getElementById("attention-dialog");
    const troubled = [...document.querySelectorAll('#orb-widgets .node[data-needs="true"]')];
    if (!dialog || !troubled.length) return false;
    const list = dialog.querySelector("[data-attention-list]");
    list.replaceChildren(
      ...troubled.map((node) => {
        const item = document.createElement("div");
        item.className = `attention-item ${node.dataset.tone === "fail" ? "is-fail" : "is-warn"}`;
        item.innerHTML = `<div class="attention-text"><strong></strong><span class="muted"></span>
          <span class="label attention-note"></span></div><div class="attention-actions"></div>`;
        item.querySelector("strong").textContent = node.dataset.title;
        item.querySelector(".muted").textContent = node.dataset.problem;
        item.querySelector(".attention-note").textContent =
          node.dataset.tone === "fail" ? "No data from it right now" : "Showing its last data";
        const actions = item.querySelector(".attention-actions");
        const details = document.createElement("button");
        details.type = "button";
        details.className = "btn btn-small";
        details.textContent = "Open details";
        details.addEventListener("click", () => {
          dialog.close();
          openDialog(document.getElementById(`detail-${node.dataset.key}`));
        });
        actions.append(details);
        if (node.dataset.fixUrl) {
          const fix = document.createElement("a");
          fix.className = "btn btn-primary btn-small";
          fix.href = node.dataset.fixUrl;
          fix.textContent = node.dataset.fixLabel || "Fix it";
          actions.append(fix);
        }
        return item;
      }),
    );
    dialogs().forEach((other) => other.open && other.close());
    dialog.showModal();
    return true;
  }

  // In the capture phase, before htmx follows the link (boosted) to Integrations.
  document.addEventListener(
    "click",
    (event) => {
      if (!event.target.closest("#attention")) return;
      if (!showAttention()) return; // nothing to list: the link goes to Integrations
      event.preventDefault();
      event.stopPropagation();
    },
    true,
  );

  // Refresh in a dialog: it shows loading until the new data replaces the dialog (the class
  // goes with the old one); if the request fails, the dialog is usable again.
  document.addEventListener("htmx:beforeRequest", (event) => {
    event.detail.elt.closest?.(".refresh-btn")?.closest("dialog")?.classList.add("is-refreshing");
  });
  document.addEventListener("htmx:afterRequest", (event) => {
    if (event.detail.successful) return;
    event.detail.elt.closest?.(".refresh-btn")?.closest("dialog")?.classList.remove("is-refreshing");
  });

  // Each number refreshes at its own pace (its widget's `refresh`): the node's border fills up
  // as its data gets older, and when it is full the number is fetched again (`due`).
  const ringTimers = new WeakMap();

  function startRing(slot, { due = false } = {}) {
    const node = slot.querySelector(".node[data-refresh]");
    clearTimeout(ringTimers.get(slot));
    if (!node) return;
    const refresh = Number(node.dataset.refresh) || 300;
    let elapsed = Math.max(0, Number(node.dataset.age) || 0);
    if (elapsed >= refresh) {
      // Older than its time: after a refresh that failed (the old data stays), wait a whole
      // round before trying again; otherwise (e.g. the page was closed a while) refresh soon.
      elapsed = due ? 0 : Math.max(0, refresh - 3);
    }
    node.style.setProperty("--ring-time", `${refresh}s`);
    node.style.setProperty("--ring-delay", `${-elapsed}s`);
    node.classList.add("has-ring");
    ringTimers.set(
      slot,
      setTimeout(() => {
        slot.dataset.due = "true";
        htmx.trigger(slot, "myorb:due");
      }, (refresh - elapsed) * 1000),
    );
  }

  // A due load asks the server to fetch the data again now, not to answer from memory.
  document.addEventListener("htmx:configRequest", (event) => {
    const slot = event.detail.elt;
    if (slot.matches?.("[data-slot]") && slot.dataset.due === "true") event.detail.parameters.due = "true";
  });

  // Periodic refreshes must not replace a number while its details are open...
  let reopen = null;
  document.addEventListener("htmx:beforeSwap", (event) => {
    if (!event.detail.target.matches?.("[data-slot]")) return;
    const slot = event.detail.target;
    const open = slot.querySelector("dialog[data-detail][open]");
    if (open && open.id !== reopen) {
      event.detail.shouldSwap = false;
      delete slot.dataset.due;
      startRing(slot, { due: true }); // the next round, with the details still showing
    }
  });

  // ...but after a change made in a dialog (a task added, Refresh), the numbers reload and the
  // same dialog opens again with the new data.
  document.body.addEventListener("myorb:refresh", () => {
    reopen = document.querySelector("dialog[data-detail][open]")?.id || null;
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    if (!event.detail.target.matches?.("[data-slot]")) return;
    const slot = event.detail.target;
    const due = slot.dataset.due === "true";
    delete slot.dataset.due;
    startRing(slot, { due });
    layoutOrb();
    fitActivity(); // the activity column is centred beside the orb, which may have grown
    if (reopen) {
      const dialog = event.detail.target.querySelector(`#${CSS.escape(reopen)}`);
      if (dialog) {
        reopen = null;
        openDialog(dialog);
      }
    }
  });

  // A load that failed (an error from the server, or no answer) swaps nothing, so without this
  // a first load would spin forever and a loaded number would never refresh again. What is
  // shown stays; a number tries again after its next round, a spinner after a short wait.
  document.addEventListener("htmx:afterRequest", (event) => {
    const slot = event.detail.elt;
    if (event.detail.successful || !slot.matches?.("[data-slot]")) return;
    delete slot.dataset.due;
    if (slot.querySelector(".node[data-refresh]")) {
      startRing(slot, { due: true });
      return;
    }
    clearTimeout(ringTimers.get(slot));
    ringTimers.set(
      slot,
      setTimeout(() => htmx.trigger(slot, "myorb:due"), 15000),
    );
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
