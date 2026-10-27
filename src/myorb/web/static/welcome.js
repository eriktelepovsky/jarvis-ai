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

  // A row that opens as a whole (an email) opens over its details, so Back shows the list just
  // as it was. The view is a history entry: the phone's back gesture closes it too. A swipe (or
  // ← →) moves to the next or previous row the list shows.

  const viewDialog = () => document.getElementById("page-view-dialog");
  let viewing = null; // the row link shown

  // The rows the list shows, and the one viewed even if it left the list meanwhile (marked as
  // read): the neighbours are counted from it.
  function viewRows() {
    const list = viewing?.closest("dialog[data-detail]");
    if (!list) return [];
    return [...list.querySelectorAll(".modal-row a.row-link[data-view]")].filter(
      (link) => link === viewing || !link.closest(".modal-row").hidden,
    );
  }

  function showView(link, step = 0) {
    const dialog = viewDialog();
    const view = document.getElementById("page-view");
    if (!dialog || !view) return false;
    viewing = link;
    view.dataset.slide = step;
    view.classList.add("is-loading");
    if (!dialog.open) {
      view.replaceChildren();
      dialog.showModal();
      history.pushState({ myorbView: true }, "");
    }
    centreSwipe();
    labelGhosts();
    htmx.ajax("GET", link.getAttribute("href"), { target: "#page-view", swap: "innerHTML" }).then(() => {
      view.classList.remove("is-loading");
      view.scrollTop = 0;
      centreSwipe();
      link.closest(".modal-row")?.scrollIntoView({ block: "nearest" });
    });
    return true;
  }

  function stepView(step) {
    const rows = viewRows();
    const next = rows[rows.indexOf(viewing) + step];
    if (next) {
      showView(next, step);
      sound("click");
    }
  }

  function closeView() {
    if (history.state?.myorbView) history.back();
    else viewDialog()?.close();
  }

  // Caught on the way down, before hx-boost's own handler on the link takes the whole page there.
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a.row-link[data-view], a.ask-link[data-view]");
    if (!link || link.target || !link.getAttribute("href").startsWith("/")) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (showView(link)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-close-view]")) closeView();
  });

  window.addEventListener("popstate", () => {
    if (!history.state?.myorbView && viewDialog()?.open) viewDialog().close();
  });

  document.addEventListener("cancel", (event) => {
    if (event.target !== viewDialog()) return;
    event.preventDefault(); // Esc goes back through history, as Back does
    closeView();
  }, true);

  // The row an email left after it was marked as read in the view. The numbers above the list
  // reload once the view closes, so the details behind it keep their place until then.
  let rowsDone = false;
  document.body.addEventListener("myorb:row-done", (event) => {
    const href = event.detail?.value ?? event.detail;
    document.querySelectorAll(`a.row-link[href="${CSS.escape(href)}"]`).forEach((link) => {
      const row = link.closest(".modal-row");
      if (row) row.hidden = true;
    });
    rowsDone = true;
  });
  document.addEventListener("close", (event) => {
    if (event.target !== viewDialog() || !rowsDone) return;
    rowsDone = false;
    htmx.trigger(document.body, "myorb:refresh");
  }, true);

  // A button in the email (Mark as read, Show images under the conversation) answers with the
  // email again: it stays where it was scrolled. Its frames have their height only once they
  // load, so the place is put back then too.
  let viewTop = null;
  document.addEventListener("htmx:beforeSwap", (event) => {
    const view = document.getElementById("page-view");
    if (event.detail.target !== view || !view.contains(event.detail.requestConfig?.elt)) return;
    // The frames' heights too: the new ones get them at once, so the email is as long as
    // before from the first paint and is not seen at the top for a moment.
    const heights = [...view.querySelectorAll("[data-email-frame]")].map((frame) => frame.style.height);
    viewTop = { top: view.scrollTop, heights, until: Date.now() + 3000 };
  });
  document.addEventListener("htmx:afterSwap", (event) => {
    const view = document.getElementById("page-view");
    if (!viewTop || event.detail.target !== view) return;
    view.querySelectorAll("[data-email-frame]").forEach((frame, i) => {
      if (viewTop.heights[i]) frame.style.height = viewTop.heights[i];
    });
    view.scrollTop = viewTop.top;
  });
  function keepViewTop() {
    const view = document.getElementById("page-view");
    if (!viewTop || !view) return;
    if (Date.now() > viewTop.until) viewTop = null;
    else view.scrollTop = viewTop.top;
  }
  document.addEventListener("htmx:afterSettle", keepViewTop);
  document.addEventListener("load", (event) => {
    if (event.target.matches?.("[data-email-frame]")) requestAnimationFrame(keepViewTop);
  }, true);
  // Once the user scrolls, the place is theirs.
  ["wheel", "touchstart", "keydown"].forEach((type) =>
    document.addEventListener(type, () => (viewTop = null), { passive: true }),
  );

  // Swipes: the view sits between its neighbours in a sideways scroller that snaps to each.
  // Scrolling is native, so it also works over the email's frame (which runs no scripts, and
  // Safari lets no script of the page watch touches in it). Settled on a neighbour, the view
  // moves there and returns to the middle.
  const swiper = () => document.querySelector("[data-page-swipe]");

  function centreSwipe() {
    const box = swiper();
    if (box) box.scrollLeft = box.clientWidth;
  }

  function labelGhosts() {
    const rows = viewRows();
    const at = rows.indexOf(viewing);
    document.querySelectorAll("[data-ghost]").forEach((ghost) => {
      const row = rows[at + Number(ghost.dataset.ghost)];
      ghost.classList.toggle("is-none", !row);
      ghost.querySelector(".page-ghost-title").textContent = row
        ? row.querySelector(".trunc")?.textContent.trim() || ""
        : "No more emails";
    });
  }

  let swipeTimer = null;
  document.addEventListener("scroll", (event) => {
    const box = event.target;
    if (!box.matches?.("[data-page-swipe]")) return;
    clearTimeout(swipeTimer);
    swipeTimer = setTimeout(() => {
      const width = box.clientWidth;
      const step = box.scrollLeft < width * 0.25 ? -1 : box.scrollLeft > width * 1.75 ? 1 : 0;
      if (!step) return;
      const rows = viewRows();
      if (rows[rows.indexOf(viewing) + step]) stepView(step);
      else box.scrollTo({ left: width, behavior: "smooth" });
    }, 120);
  }, true);

  window.addEventListener("resize", () => viewDialog()?.open && centreSwipe());

  document.addEventListener("keydown", (event) => {
    if (!viewDialog()?.open || event.target.closest("input, textarea")) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      // Not also the browser's own: it would scroll the sideways scroller a little, and the
      // next email would sit off its place.
      event.preventDefault();
      event.stopImmediatePropagation();
      stepView(event.key === "ArrowLeft" ? -1 : 1);
    } else if (event.key.toLowerCase() === "m" && !event.ctrlKey && !event.metaKey && !event.altKey) {
      // M: marks the email as read and goes on to the next, as reading through the inbox.
      // One read already just goes on.
      event.preventDefault();
      event.stopImmediatePropagation();
      const form = document.querySelector("#page-view [data-mark-read]");
      if (!form) return stepView(1);
      form.addEventListener("htmx:afterRequest", (done) => done.detail.successful && stepView(1), { once: true });
      htmx.trigger(form, "submit");
    }
  }, true);

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
      row.dataset.text ??= fold(`${row.textContent} ${row.dataset.search || ""}`);
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
    } else if (dialog && event.target.closest("[data-step]")) {
      stepDialog(dialog, Number(event.target.closest("[data-step]").dataset.step));
      sound("click");
    } else if (event.target.closest("[data-open-releases]")) {
      dialogs().forEach((other) => other.open && other.close());
      const releases = document.getElementById("releases");
      releases.showModal();
      htmx.trigger(releases.querySelector(".releases-box"), "myorb:open");
      sound("open");
    } else if (event.target.closest("[data-open-report]")) {
      dialogs().forEach((other) => other.open && other.close());
      const report = document.getElementById("report");
      report.showModal();
      // As it is now: an event ignored or a task done since it was loaded changes it.
      htmx.trigger(report.querySelector(".report"), "myorb:open");
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

  // Esc leaves the command line, keeping what is typed (while listening, it cancels that).
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && event.target.id === "command-input" && !listening) event.target.blur();
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

  // While the user is about to ask (the chat opened, the question line or the microphone in
  // use), Claude reads the dashboard into its cache, so the answer starts sooner. Again after
  // WARM_AGAIN while nothing is asked, as the cache lasts five minutes.
  const WARM_AGAIN = 4 * 60 * 1000;
  let warming = null;
  let warmedAt = 0;
  function warmAsk() {
    if (!document.getElementById("ask") || warming || Date.now() - warmedAt < WARM_AGAIN) return;
    warmedAt = Date.now();
    const sent = ask;
    warming = fetch("/ask/warm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation: ask.conversation, messages: ask.messages }),
    })
      .then((response) => (response.ok ? response.json() : {}))
      .then((data) => {
        // Unless a new conversation began meanwhile.
        if (data.conversation && ask === sent) {
          ask.conversation = data.conversation;
          saveAsk();
        }
      })
      .catch(() => {})
      .finally(() => (warming = null));
  }
  document.addEventListener("focusin", (event) => {
    if (event.target.matches?.("#ask-input, .command-bar[data-ask] #command-input")) warmAsk();
  });

  const escapeHtml = (text) =>
    text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // Plain text with "- " lists, **bold** and links to the dashboard's items, as the prompt
  // asks; everything else is escaped. A link is [AR-16](L3fa2c1): the id of the item's address,
  // which the answer's stream sends at its end ({"links": …}) and the conversation keeps.
  // The label may hold a pair of brackets itself, e.g. "Vyzdvihnúť SPS [322653]".
  const LINK = /\[((?:[^\[\]\n]|\[[^\[\]\n]*\])+)\]\((L[0-9a-f]{6})\)/g;
  const NOTE = /^\s*\[(ok|warn|error)\]\s*/i;
  const NOTE_SIGNS = { ok: "✓ ", warn: "▲ ", error: "✕ " };

  function renderAnswer(text) {
    return text
      .split(/\n{2,}/)
      .map((block) => {
        const lines = block.split("\n").filter((line) => line.trim());
        const inline = (line) =>
          escapeHtml(line)
            .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
            .replace(LINK, (match, label, id) => {
              const url = ask.links?.[id];
              // An email opens over the chat, as from its list; the rest in a new tab.
              if (/^\/email\//.test(url || "")) {
                return `<a class="ask-link" href="${escapeHtml(url)}" data-view>${label}</a>`;
              }
              return /^(https?:\/\/|\/(?!\/))/.test(url || "")
                ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${label}</a>`
                : label;
            });
        // "[ok]", "[warn]" or "[error]" before a paragraph or a list item: shown in green,
        // yellow or red with its sign, so an outcome or a problem stands out.
        const note = (line) => {
          const found = line.match(NOTE);
          if (!found) return ["", line];
          const tone = found[1].toLowerCase();
          return [` class="ask-note ask-note-${tone}"`, NOTE_SIGNS[tone] + line.slice(found[0].length)];
        };
        // List items ("- ") make a list, e.g. under a line that introduces them; other lines
        // a paragraph.
        const ITEM = /^\s*[-•]\s/;
        const prose = (run) => {
          const parts = [];
          for (const line of run) {
            const item = ITEM.test(line);
            if (parts.at(-1)?.item === item) parts.at(-1).lines.push(line);
            else parts.push({ item, lines: [line] });
          }
          return parts
            .map(({ item, lines }) => {
              if (item) {
                return `<ul>${lines
                  .map((line) => {
                    const [marked, rest] = note(line.replace(ITEM, ""));
                    return `<li${marked}>${inline(rest)}</li>`;
                  })
                  .join("")}</ul>`;
              }
              const [marked, first] = note(lines[0]);
              return `<p${marked}>${[first, ...lines.slice(1)].map(inline).join("<br>")}</p>`;
            })
            .join("");
        };
        // "> " lines are a text to copy, e.g. a draft reply: shown without the marks, with
        // a Copy button for all of it.
        const quote = (run) =>
          `<div class="ask-quote"><div class="ask-quote-text">${run
            .map((line) => inline(line.replace(/^\s*>\s?/, "")))
            .join("<br>")}</div><button type="button" class="btn btn-small ask-copy" data-ask-copy>Copy</button></div>`;
        const runs = [];
        for (const line of lines) {
          const quoted = /^\s*>/.test(line);
          if (runs.at(-1)?.quoted === quoted) runs.at(-1).lines.push(line);
          else runs.push({ quoted, lines: [line] });
        }
        return runs.map((run) => (run.quoted ? quote(run.lines) : prose(run.lines))).join("");
      })
      .join("");
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-ask-copy]");
    if (!button) return;
    const text = button.closest(".ask-quote").querySelector(".ask-quote-text").innerText;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
    setTimeout(() => (button.textContent = "Copy"), 1500);
  });

  function askBubble(role, text, state = "", result = "") {
    if (role === "action") {
      const card = document.createElement("div");
      card.className = `ask-action is-${state === "done" ? "approved" : "cancelled"}`;
      card.innerHTML = `<span class="ask-action-text"></span><span class="ask-action-buttons"><span class="ask-action-status${state === "done" ? "" : ` is-${state === "error" ? "error" : "cancelled"}`}"></span></span>`;
      card.querySelector(".ask-action-text").textContent = text;
      card.querySelector(".ask-action-status").textContent = result;
      return card;
    }
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
    log.replaceChildren(...ask.messages.map((m) => askBubble(m.role, m.content, m.state, m.result)));
    if (!dialog.open) {
      dialogs().forEach((other) => other.open && other.close());
      dialog.showModal();
    }
    log.scrollTop = log.scrollHeight;
    return dialog;
  }

  // Reads a stream of JSON lines from /ask or /ask/decide into the log: the answer's text, the
  // actions Claude proposes (cards to confirm) and what the confirmed ones did.
  async function askStream(dialog, url, body) {
    const log = dialog.querySelector("[data-ask-log]");
    // What is going on until (and between) the answer's text, with a spinner.
    const working = (note) =>
      `<span class="ask-thinking"><span class="ask-spinner" aria-hidden="true"></span>${escapeHtml(note)}</span>`;
    let bubble = askBubble("assistant", "", "thinking");
    bubble.innerHTML = working("Looking at your dashboard…");
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
          if (data.links) {
            ask.links = { ...ask.links, ...data.links };
            if (text && bubble.isConnected) bubble.innerHTML = renderAnswer(text);
          }
          if (data.status) {
            if (!bubble.isConnected) {
              bubble = askBubble("assistant", "");
              log.append(bubble);
            }
            bubble.classList.toggle("is-thinking", !text);
            bubble.innerHTML = (text ? renderAnswer(text) : "") + working(data.status);
            log.scrollTop = log.scrollHeight;
          }
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
    // Kept with the conversation, so it shows again and Claude knows it ran, even when the
    // server no longer has the conversation (e.g. after a restart).
    const state = done.cancelled ? "cancelled" : done.error ? "error" : "done";
    ask.messages.push({ role: "action", content: card.querySelector(".ask-action-text").textContent, result: status.textContent, state });
    saveAsk();
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

  // An answer read aloud, with a Stop button in Ask's head while it lasts. Closing Ask, a new
  // question and the microphone stop it too.
  let speaking = 0; // the reading going on, so an earlier one ending does not hide the button
  let answerAudio = null; // read by Azure Speech
  const LANGUAGE_NAMES = { en: "English", sk: "Slovak", cs: "Czech", de: "German", fr: "French", es: "Spanish", it: "Italian", pl: "Polish", hu: "Hungarian" };
  async function speakAnswer(text, tag) {
    const dialog = document.getElementById("ask");
    const azure = dialog?.dataset.voice === "azure";
    if (!azure && !window.myorbSpeech) return;
    // A browser without a voice for the language reads it with another's: not at all, then.
    if (!azure && !window.myorbSpeech.voicesFor(tag).length) {
      const note = askBubble("assistant", "");
      note.className = "ask-message ask-voice-note";
      const name = LANGUAGE_NAMES[tag.slice(0, 2)] || tag;
      note.textContent = `Not read aloud: this browser has no ${name} voice. Add one in the system's settings (on a Mac: Accessibility → Spoken Content), or set up Azure Speech in Integrations → Voice.`;
      dialog?.querySelector("[data-ask-log]")?.append(note);
      return;
    }
    const turn = ++speaking;
    const stop = document.querySelector("[data-ask-stop]");
    if (stop) stop.hidden = false;
    if (azure) await speakAzure(text, tag);
    else await window.myorbSpeech.speak(text, tag);
    if (turn !== speaking) return;
    speaking = 0;
    if (stop) stop.hidden = true;
  }

  async function speakAzure(text, tag) {
    try {
      const response = await fetch("/voice/speech", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, language: tag }),
      });
      if (!response.ok) return;
      const url = URL.createObjectURL(await response.blob());
      answerAudio = new Audio(url);
      await new Promise((resolve) => {
        answerAudio.onended = answerAudio.onerror = answerAudio.onpause = resolve;
        answerAudio.play().catch(resolve);
      });
      URL.revokeObjectURL(url);
    } catch {
      // Not read: the answer is there to read.
    }
  }

  function stopSpeaking() {
    // Whatever is being read, even if the reading was started before this page knew of it.
    if ("speechSynthesis" in window && (speaking || speechSynthesis.speaking || speechSynthesis.pending)) {
      speechSynthesis.cancel();
    }
    answerAudio?.pause();
    answerAudio = null;
    speaking = 0;
    const stop = document.querySelector("[data-ask-stop]");
    if (stop) stop.hidden = true;
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-ask-stop]")) stopSpeaking();
  });
  document.getElementById("ask")?.addEventListener("close", stopSpeaking);

  async function askQuestion(question, { aloud = "" } = {}) {
    stopSpeaking();
    const dialog = showAsk();
    if (!dialog || dialog.dataset.busy) return;
    const log = dialog.querySelector("[data-ask-log]");
    ask.messages.push({ role: "user", content: question });
    log.append(askBubble("user", question));
    // Asked while Claude is still reading the dashboard: in that conversation, from the cache.
    if (warming) await warming;
    warmedAt = Date.now(); // the question keeps the cache warm too
    const { text, error } = await askStream(dialog, "/ask", {
      conversation: ask.conversation,
      messages: ask.messages,
    });
    // A spoken question gets a spoken answer, without the list markers, bold and links.
    if (aloud && text) speakAnswer(text.replace(/\*\*|^\s*- |^\s*>\s?|\[(ok|warn|error)\]\s*/gim, "").replace(LINK, "$1"), aloud);
    // Nothing answered: the question is taken back, so asking again does not repeat it.
    if (error && !text) {
      ask.messages = ask.messages.filter((m, i) => !(i === ask.messages.length - 1 && m.role === "user"));
      saveAsk();
    }
  }

  // The command line: with Claude connected, everything typed goes to Ask (Claude adds
  // tasks too); without, it adds a task.
  document.addEventListener("htmx:beforeRequest", (event) => {
    const form = event.detail.elt;
    if (!form.matches?.(".command-bar[data-ask]")) return;
    const input = form.querySelector("#command-input");
    event.preventDefault();
    const question = input.value.trim();
    form.reset();
    // Dictated (see dictate()): answered aloud, as a question heard by speech recognition.
    const aloud = dictating ? form.dataset.voiceLang || "" : "";
    dictating = false;
    if (question) askQuestion(question, { aloud });
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-ask-form]");
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector("input");
    if (!input.value.trim()) return;
    const question = input.value.trim();
    form.reset();
    // Dictated (see dictate()): answered aloud.
    const lang = document.querySelector(".command-bar[data-voice-lang]")?.dataset.voiceLang;
    const aloud = dictating ? lang || "" : "";
    dictating = false;
    askQuestion(question, { aloud });
  });

  // The language questions are heard in: the brief's, unless another was chosen in the Voice
  // panel (kept in this browser). Answers to spoken questions are read in it too.
  const VOICE_LANG_KEY = "myorb.voiceLang";
  function setVoiceLang(tag, keep = true) {
    const bar = document.querySelector(".command-bar[data-voice-lang]");
    if (!bar || !tag) return;
    bar.dataset.voiceLang = tag;
    document.querySelectorAll("[data-voice-lang-label]").forEach((label) => (label.textContent = tag));
    document.querySelectorAll("[data-voice-lang-choice]").forEach((option) => {
      option.setAttribute("aria-checked", String(option.dataset.voiceLangChoice === tag));
    });
    if (!keep) return;
    try {
      localStorage.setItem(VOICE_LANG_KEY, tag);
    } catch {
      // Storage unavailable: for this page only.
    }
  }
  try {
    const kept = localStorage.getItem(VOICE_LANG_KEY);
    if (kept && document.querySelector(`[data-voice-lang-choice="${CSS.escape(kept)}"]`)) setVoiceLang(kept, false);
    else setVoiceLang(document.querySelector(".command-bar[data-voice-lang]")?.dataset.voiceLang, false);
  } catch {
    // Storage unavailable: the brief's language.
  }
  document.addEventListener("click", (event) => {
    const option = event.target.closest("[data-voice-lang-choice]");
    const menu = document.querySelector("details.voice-lang[open]");
    if (option) {
      setVoiceLang(option.dataset.voiceLangChoice);
      sound("click");
    }
    if (menu && (option || !menu.contains(event.target))) menu.open = false;
  });

  // Voice: the browser's speech recognition hears a question, shown as it is heard in the
  // command line, and asks it when the speaker stops; the answer is read aloud. Esc or the
  // button again cancels. Hidden in browsers that cannot recognize speech.
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let listening = null;

  function stopListening() {
    listening?.abort();
  }

  // Installed on an iPhone's or iPad's home screen, myOrb gets no speech recognition from iOS
  // (only Safari does). There the button opens the keyboard for its own dictation instead,
  // and the answer to what is dictated and sent is read aloud all the same.
  const appleTouch =
    /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const installed = navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  const dictateOnly = appleTouch && installed;
  let dictating = false;

  function dictate(form, input) {
    dictating = true;
    input.focus(); // in the tap itself, else iOS shows no keyboard
    const result = document.getElementById("command-result");
    result.textContent = "Dictate with the microphone on the keyboard, then send.";
    warmAsk();
    input.addEventListener("blur", () => {
      result.textContent = "";
      // A send by the keyboard's Go may blur the line first: it still counts as dictated.
      setTimeout(() => (dictating = false), 1000);
    }, { once: true });
  }

  // In the chat, the question is heard into its own line; elsewhere into the command line.
  function listen() {
    const bar = document.querySelector(".command-bar[data-voice-lang]");
    const chat = document.getElementById("ask");
    const form = chat?.open ? chat.querySelector("[data-ask-form]") : bar;
    const input = chat?.open ? chat.querySelector("#ask-input") : bar?.querySelector("#command-input");
    if (!bar || !input) return;
    stopSpeaking(); // a new question interrupts the answer being read
    if (dictateOnly) return dictate(form, input);
    if (listening) return stopListening();
    const recognition = new Recognition();
    recognition.lang = bar.dataset.voiceLang;
    recognition.interimResults = true;
    const placeholder = input.placeholder;
    let heard = "";
    const buttons = document.querySelectorAll("[data-voice-ask]");
    const setState = (on) => {
      buttons.forEach((button) => button.classList.toggle("is-listening", on));
      form.classList.toggle("is-listening", on);
      input.placeholder = on ? "Listening… ask your question" : placeholder;
      document.querySelectorAll("[data-voice-state]").forEach((state) => {
        state.textContent = on ? "Listening…" : state.dataset.idle;
      });
    };
    recognition.onresult = (event) => {
      heard = Array.from(event.results, (result) => result[0].transcript).join("");
      input.value = heard;
    };
    recognition.onend = () => {
      listening = null;
      setState(false);
      input.value = "";
      if (heard.trim()) askQuestion(heard.trim(), { aloud: recognition.lang });
    };
    recognition.onerror = (event) => {
      heard = "";
      const result = document.getElementById("command-result");
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        result.textContent = "The microphone is not allowed for this page.";
      } else if (event.error !== "no-speech" && event.error !== "aborted") {
        result.textContent = `Speech recognition failed (${event.error}).`;
      }
    };
    listening = recognition;
    warmAsk();
    setState(true);
    recognition.start();
  }

  function showVoice() {
    document.querySelectorAll("[data-voice-ask], [data-voice-panel]").forEach((element) => {
      element.hidden = !Recognition && !dictateOnly;
    });
  }
  showVoice();
  document.addEventListener("htmx:afterSettle", showVoice);

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-voice-ask]")) listen();
  });

  // V asks by voice, as the microphone does, unless typing somewhere.
  document.addEventListener("keydown", (event) => {
    if (event.key.toLowerCase() !== "v" || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.repeat || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const button = document.querySelector("[data-voice-ask]:not([hidden])");
    if (!button) return;
    event.preventDefault();
    listen();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    // In the chat, Esc closes it, stops reading the answer and stops listening, all at once.
    const chat = document.getElementById("ask");
    if (chat?.open) {
      event.preventDefault();
      if (listening) stopListening();
      stopSpeaking();
      chat.close();
      return;
    }
    if (listening) {
      event.preventDefault();
      stopListening();
    }
  });

  // A quick action asks its question in a new conversation.
  document.addEventListener("click", (event) => {
    const quick = event.target.closest("[data-ask-quick]");
    if (!quick) return;
    ask = { conversation: null, messages: [] };
    saveAsk();
    askQuestion(quick.dataset.askQuick);
  });

  // Chat in the header: the conversation as it was left, ready for the next question.
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-ask-open]")) return;
    showAsk();
    document.getElementById("ask-input")?.focus();
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-ask-new]")) return;
    ask = { conversation: null, messages: [] };
    saveAsk();
    warmedAt = 0; // a new conversation reads the dashboard anew
    showAsk();
    document.getElementById("ask-input")?.focus();
  });

  // Narrower than the three columns, they are three screens side by side (welcome.css): the
  // orb's in the middle, shown first; swiped between, or chosen in the bar below them.
  const panesBox = document.querySelector(".welcome-main");
  const narrowPanes = () => matchMedia("(max-width: 1279px)").matches;
  const paneAt = () => (panesBox ? Math.round(panesBox.scrollLeft / Math.max(1, panesBox.clientWidth)) : 1);
  // A phone on its side lays them out otherwise: the chosen screen takes the orb's place, with
  // nothing to scroll sideways (welcome.css).
  const sideways = () => matchMedia("(orientation: landscape) and (max-height: 560px)").matches;
  function showPane(index, smooth = true) {
    if (!panesBox || !narrowPanes()) return;
    if (sideways()) {
      // Chosen in the bar: the screen shown slides out and the chosen one in after it, as
      // when swiped (and as upright, where the screens scroll).
      if (smooth && index !== paneShown && slideBetween(paneShown, index)) return;
      paneShown = index;
      return markPane(index);
    }
    panesBox.scrollTo({ left: index * panesBox.clientWidth, behavior: smooth ? "smooth" : "instant" });
  }
  function markPane(at = paneAt()) {
    // Which screen is shown, for what belongs to the orb's only (welcome.css).
    document.getElementById("welcome").dataset.pane = at;
    document.querySelectorAll("[data-pane]").forEach((button) => {
      const on = Number(button.dataset.pane) === at;
      button.classList.toggle("is-on", on);
      if (on) button.setAttribute("aria-current", "true");
      else button.removeAttribute("aria-current");
    });
  }
  let paneShown = 1;
  showPane(1, false);
  markPane(1);
  panesBox?.addEventListener("scroll", () => {
    if (sideways()) return;
    paneShown = paneAt();
    markPane();
  }, { passive: true });
  // A new width keeps the screen that was shown.
  window.addEventListener("resize", () => showPane(paneShown, false));
  // On a phone on its side nothing scrolls sideways: a swipe across the screen goes to the
  // screen beside, as one held upright does.
  // While dragged sideways, the screen shown follows the finger and the one beside comes in
  // after it, as when upright; let go far enough, it goes on to that screen, else back.
  // At the first or last screen the drag gives a little only.
  let swipeFrom = null;
  let dragged = null; // {el, next, width, sideways}
  const PANES = ["#welcome .side-left", "#welcome .orb", "#welcome .side-right"];
  const paneEl = (index) => document.querySelector(PANES[index]);
  // Between the two while dragged: the orb's circles reach past its box.
  const GAP = 72;
  const welcomeBox = document.getElementById("welcome");
  function place(el, x) {
    el.style.transform = x ? `translateX(${x}px)` : "";
    // In its own half: what passes its left edge is not drawn over the clock.
    el.style.clipPath = x < 0 ? `inset(0 0 0 ${-x}px)` : "";
  }
  // Slides the screen shown out and `to` in after it; false when there is nothing to slide.
  function slideBetween(from, to) {
    const el = paneEl(from);
    const next = paneEl(to);
    if (!el || !next || !welcomeBox) return false;
    const width = el.getBoundingClientRect().width;
    const out = (to > from ? -1 : 1) * (width + GAP);
    const state = { el, next };
    next.classList.add("is-peek");
    for (const pane of [el, next]) pane.style.transition = "none";
    place(next, -out);
    void next.offsetWidth; // from there, not from where it was
    for (const pane of [el, next]) pane.style.transition = "transform 0.28s ease-out";
    place(el, out);
    place(next, 0);
    setTimeout(() => {
      paneShown = to;
      markPane(to);
      endDrag(state);
    }, 290);
    return true;
  }
  function endDrag(state) {
    for (const el of [state.el, state.next]) {
      if (!el) continue;
      el.style.transition = "";
      el.style.transform = "";
      el.style.clipPath = "";
      el.classList.remove("is-peek");
    }
  }
  welcomeBox?.addEventListener("touchstart", (event) => {
    swipeFrom = sideways() && event.touches.length === 1 ? event.touches[0] : null;
    const el = swipeFrom ? paneEl(paneShown) : null;
    dragged = el ? { el, next: null, width: el.getBoundingClientRect().width, sideways: null } : null;
  }, { passive: true });
  welcomeBox?.addEventListener("touchmove", (event) => {
    if (!swipeFrom || !dragged) return;
    const touch = event.touches[0];
    const dx = touch.clientX - swipeFrom.clientX;
    const dy = touch.clientY - swipeFrom.clientY;
    if (dragged.sideways === null && Math.abs(dx) + Math.abs(dy) > 10) dragged.sideways = Math.abs(dx) > Math.abs(dy) * 1.5;
    if (!dragged.sideways) return;
    const index = paneShown + (dx < 0 ? 1 : -1);
    const next = index >= 0 && index <= 2 ? paneEl(index) : null;
    if (next !== dragged.next) {
      dragged.next?.classList.remove("is-peek");
      if (dragged.next) place(dragged.next, 0);
      next?.classList.add("is-peek");
      dragged.next = next;
      dragged.index = index;
    }
    const moved = next ? dx : dx / 4;
    dragged.el.style.transition = "none";
    place(dragged.el, moved);
    if (next) {
      next.style.transition = "none";
      place(next, moved + (dx < 0 ? 1 : -1) * (dragged.width + GAP));
    }
  }, { passive: true });
  welcomeBox?.addEventListener("touchend", (event) => {
    if (!swipeFrom) return;
    const end = event.changedTouches[0];
    const dx = end.clientX - swipeFrom.clientX;
    const state = dragged;
    swipeFrom = null;
    dragged = null;
    if (!state?.sideways) return;
    const goes = state.next && Math.abs(dx) >= 60;
    const out = (dx < 0 ? -1 : 1) * (state.width + GAP);
    for (const el of [state.el, state.next]) if (el) el.style.transition = "transform 0.22s ease-out";
    if (goes) {
      place(state.el, out);
      place(state.next, 0);
      sound("click");
      setTimeout(() => {
        showPane(state.index, false);
        endDrag(state);
      }, 230);
    } else {
      place(state.el, 0);
      if (state.next) place(state.next, -out);
      setTimeout(() => endDrag(state), 230);
    }
  }, { passive: true });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-pane]");
    if (!button) return;
    showPane(Number(button.dataset.pane));
    sound("click");
  });

  // Add in a side panel's head (Tasks): a small dialog with one line to write it in; added,
  // it closes and the panel loads again (the response says myorb:refresh).
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-add]");
    if (!button) return;
    const dialog = document.getElementById(button.dataset.add);
    dialog?.showModal();
    dialog?.querySelector("input")?.focus();
    sound("open");
  });
  document.addEventListener("htmx:afterRequest", (event) => {
    const form = event.detail.elt.closest?.("[data-add-form]");
    if (!form || !event.detail.successful) return;
    form.reset();
    form.closest("dialog")?.close();
  });

  // The activity list shows only as many events as fit on screen: under the clock, above the
  // YouTube window (or the music search) while it is shown, else above the dock's controls or
  // the window's edge. The rest, the oldest, fade out.
  // After the column's height has moved, check once more: the orb may have changed meanwhile.
  function fitActivity() {
    const list = document.querySelector('.side-right [hx-get="/activity"]');
    if (!list) return;
    // As a screen of its own (narrow windows), the column shows every event and scrolls.
    if (narrowPanes()) {
      const column = list.closest(".side-right");
      column.style.maxHeight = "";
      column.style.transform = "";
      list.querySelectorAll(".activity-item.is-cut").forEach((item) => item.classList.remove("is-cut"));
      return;
    }
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
    // The column may fill the room between the clock above (with the next event) and, below,
    // the YouTube window or the dock's controls (or the window's edge); events that do not
    // fit fade out (CSS animates the height).
    // Every part of the header above it: the next event's pill sits outside the clock's box.
    const above = [...document.querySelectorAll(".welcome-header *")]
      .map((el) => el.getBoundingClientRect())
      .filter(across);
    const top = Math.max(0, ...above.map((rect) => rect.bottom)) + 16;
    const controls = [...document.querySelectorAll("#media-dock .dock-controls")]
      .map((el) => el.getBoundingClientRect())
      .filter(across);
    const bottom = Math.min(window.innerHeight, ...[...covering, ...controls].map((rect) => rect.top)) - 16;
    // Where the grid centres it, without the shift (see below) as it is drawn now.
    const transform = getComputedStyle(column).transform;
    const moved = transform === "none" ? 0 : new DOMMatrix(transform).m42;
    const centre = box.top - moved + box.height / 2;
    const room = bottom - top;
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
    // Centred beside the orb while that fits, else moved up (or down) into the room.
    const half = height / 2;
    const shift = Math.min(0, bottom - (centre + half)) + Math.max(0, top - (centre - half));
    column.style.transform = shift ? `translateY(${Math.round(shift)}px)` : "";
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

  // Pull to refresh: on a touch screen, where the details have no Refresh button, pulling their
  // body down from the top loads new data, as the button does. In the daily brief's report it
  // writes a new brief, as its button does.
  const PULL = 70; // px to pull before letting go refreshes
  let pull = null;
  document.addEventListener("touchstart", (event) => {
    const body = event.target.closest?.("dialog[data-detail] .modal-body, #report .report-body");
    const dialog = body?.closest("dialog");
    if (!body || body.scrollTop > 0 || event.touches.length > 1) return;
    if (dialog.classList.contains("is-refreshing") || dialog.querySelector(".brief-writing.is-writing, .brief-writing.htmx-request")) return;
    pull = { body, dialog, y: event.touches[0].clientY, dy: 0, hint: null };
  }, { passive: true });
  document.addEventListener("touchmove", (event) => {
    if (!pull) return;
    const dy = event.touches[0].clientY - pull.y;
    if (dy <= 0 || pull.body.scrollTop > 0) {
      if (!pull.hint) pull = null; // scrolling the list, not pulling
      return;
    }
    event.preventDefault(); // no bounce of its own: the body follows the finger
    pull.dy = Math.min(dy * 0.5, PULL * 1.5);
    if (!pull.hint) {
      pull.hint = document.createElement("div");
      pull.hint.className = "label pull-hint";
      pull.body.before(pull.hint);
      pull.hint.style.top = `${pull.body.offsetTop}px`; // in the room above the pulled body
    }
    const ready = pull.dy >= PULL;
    pull.hint.textContent = ready ? "↻ Release to refresh" : "↓ Pull to refresh";
    pull.hint.classList.toggle("is-ready", ready);
    pull.hint.style.height = `${pull.dy}px`;
    pull.body.style.transform = `translateY(${pull.dy}px)`;
  }, { passive: false });
  function endPull() {
    if (!pull) return;
    const { body, dialog, dy, hint } = pull;
    pull = null;
    body.style.transition = "transform 0.2s";
    body.style.transform = "";
    setTimeout(() => (body.style.transition = ""), 200);
    hint?.remove();
    if (dy >= PULL) dialog.querySelector(".modal-head .refresh-btn, .modal-head .brief-generate")?.click();
  }
  document.addEventListener("touchend", endPull);
  document.addEventListener("touchcancel", endPull);

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
  // Where the reopened details were scrolled and what their search held, to be put back:
  // each scrolled element by its place in the dialog, which the new data keeps (one row less
  // comes after the list's own scroller).
  let kept = null;
  function keep(dialog) {
    const all = [dialog, ...dialog.querySelectorAll("*")];
    return {
      scrolled: all.flatMap((element, i) => (element.scrollTop > 0 ? [[i, element.scrollTop]] : [])),
      search: dialog.querySelector("[data-list-search]")?.value || "",
    };
  }
  function restore(dialog, { scrolled, search }) {
    const input = dialog.querySelector("[data-list-search]");
    if (input && search) {
      input.value = search;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    const all = [dialog, ...dialog.querySelectorAll("*")];
    scrolled.forEach(([i, top]) => {
      if (all[i]) all[i].scrollTop = top;
    });
  }
  document.addEventListener("htmx:beforeSwap", (event) => {
    if (!event.detail.target.matches?.("[data-slot]")) return;
    const slot = event.detail.target;
    const open = slot.querySelector("dialog[data-detail][open]");
    if (open && open.id === reopen) kept = keep(open);
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
        if (kept) restore(dialog, kept);
      }
      kept = null;
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
    // A link back from another page (an email's Back) names the details to open again.
    if (location.hash.startsWith("#detail-")) {
      const dialog = document.getElementById(location.hash.slice(1));
      if (dialog?.matches("dialog[data-detail]")) openDialog(dialog);
      else reopen = location.hash.slice(1);
      history.replaceState(history.state, "", location.pathname + location.search);
    }
    tick();
    clockTimer ??= setInterval(tick, 1000);
    startOrb();
  }

  document.addEventListener("DOMContentLoaded", initWelcome);
  // Coming back to the welcome screen from another page (hx-boost) or with the browser's Back
  // button loads no new page. (htmx:afterSettle would fire on the replaced, detached #app.)
  document.addEventListener("htmx:load", initWelcome);
  document.addEventListener("htmx:historyRestore", initWelcome);

  // htmx keeps a page left with open details as it was, open attribute and all; restored, such
  // a dialog would be part of the page rather than over it (under the orb). It opens again as
  // a dialog; an email over the details is left closed.
  document.addEventListener("htmx:historyRestore", () => {
    document.querySelectorAll("dialog[open]").forEach((dialog) => {
      if (dialog.matches(":modal")) return;
      dialog.removeAttribute("open");
      if (dialog.matches("[data-detail]")) openDialog(dialog);
    });
  });
})();
