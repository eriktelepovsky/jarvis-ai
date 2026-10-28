// The command palette (⌘K, Ctrl+K): everything the dashboard shows and does, found by typing.
// It searches what is already on the page (the rows in every number's details: emails, events,
// tasks, pull requests, issues), the numbers themselves, quick actions and the pages, and runs
// the chosen one as a click on it would. Enter without a match asks the assistant (or adds a
// task without Claude), as the command line does.
(() => {
  const LIMIT = 40;
  let dialog;
  let input;
  let list;
  let items = [];
  let shown = [];
  let active = 0;

  // Lower case without diacritics, so "zaznam" finds "Záznam" (as the details' search does).
  const fold = (text) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const textOf = (el) => (el?.textContent || "").replace(/\s+/g, " ").trim();
  const click = (selector) => () => document.querySelector(selector)?.click();
  // A page: through its link when the page has one (boosted, so the music plays on).
  const go = (url) => () => {
    const link = document.querySelector(`#app a[href="${url}"]:not([target])`);
    if (link) link.click();
    else location.href = url;
  };

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "palette";
    dialog.setAttribute("aria-label", "Command palette");
    dialog.innerHTML = `
      <div class="palette-head">
        <span class="palette-prompt" aria-hidden="true">›</span>
        <input class="palette-input" type="text" role="combobox" aria-expanded="true" aria-controls="palette-list" aria-autocomplete="list" autocomplete="off" spellcheck="false" placeholder="Search or run a command…">
        <button type="button" class="icon-btn palette-close" aria-label="Close" title="Close (Esc)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
      </div>
      <ul class="palette-list" id="palette-list" role="listbox"></ul>
      <div class="palette-foot label">↑ ↓ choose · Enter runs · Esc closes</div>`;
    document.body.append(dialog);
    input = dialog.querySelector("input");
    list = dialog.querySelector("ul");
    input.addEventListener("input", () => {
      active = 0;
      render();
    });
    input.addEventListener("keydown", keys);
    list.addEventListener("click", (event) => {
      const option = event.target.closest("[data-index]");
      if (option) run(shown[Number(option.dataset.index)]);
    });
    list.addEventListener("mousemove", (event) => {
      const option = event.target.closest("[data-index]");
      if (option && Number(option.dataset.index) !== active) {
        active = Number(option.dataset.index);
        mark();
      }
    });
    dialog.querySelector(".palette-close").addEventListener("click", () => dialog.close());
    // A click on the dialog element itself is a click on its backdrop.
    dialog.addEventListener("click", (event) => event.target === dialog && dialog.close());
  }

  // What can be found now: read from the page each time the palette opens.
  function collect() {
    const found = [];
    const add = (kind, title, detail, action, words = "", row = false) =>
      found.push({ kind, title, detail, action, row, text: fold(`${title} ${detail} ${words}`) });

    if (document.querySelector("[data-open-report]")) add("Brief", "Open the daily brief", "", click("[data-open-report]"), "report summary");
    document.querySelectorAll("[data-ask-quick]").forEach((button) => {
      add("Ask", textOf(button), button.dataset.askQuick, () => button.click(), "quick action");
    });
    if (document.querySelector("[data-ask-open]")) add("Ask", "Open the chat", "", click("[data-ask-open]"), "conversation ask");
    document.querySelectorAll("[data-add]").forEach((button) => {
      const label = document.getElementById(button.dataset.add)?.getAttribute("aria-label") || "Add";
      add("Do", label, "", () => button.click(), "new create");
    });
    if (document.querySelector("[data-toggle='music']")) add("Do", "Music on or off", "", click("[data-toggle='music']"), "play pause sound");
    const video = document.querySelector("[data-toggle='video']");
    if (video && !video.hidden) add("Do", "Show or hide the video", "", () => video.click(), "youtube player");
    if (document.querySelector("[data-open-releases]")) add("Go", "What's new in myOrb", "", click("[data-open-releases]"), "releases changelog version");

    // The numbers around the orb, and every row in their details.
    document.querySelectorAll(".node[data-open]").forEach((node) => {
      const details = document.getElementById(node.dataset.open);
      if (!details) return;
      const value = textOf(node.querySelector(".node-value"));
      const source = node.dataset.title;
      add(source, `${source} details`, `${value} ${textOf(node.querySelector(".node-label"))}`, () => node.click(), "number");
      details.querySelectorAll(".modal-row").forEach((row) => {
        const [title, subtitle] = [...row.querySelectorAll(".trunc")].map(textOf);
        const badge = textOf(row.querySelector(".badge"));
        add(source, title || textOf(row), [subtitle, badge].filter(Boolean).join(" · "), () => openRow(node, details, row), `${source} ${row.dataset.search || ""}`, true);
      });
    });

    const pages = [
      ["Home", "/"],
      ["Tasks", "/tasks"],
      ["Activity", "/activity"],
      ["Integrations", "/integrations"],
      ["Security", "/settings/security"],
    ];
    pages.forEach(([name, url]) => add("Go", name, url, go(url), "page open settings"));
    return found;
  }

  // A row: its details open with it in sight and highlighted; Enter then opens it.
  function openRow(node, details, row) {
    if (!details.open) node.click();
    const search = details.querySelector("[data-list-search]");
    if (search && search.value) {
      search.value = "";
      search.dispatchEvent(new Event("input", { bubbles: true }));
    }
    row.hidden = false;
    row.scrollIntoView({ block: "center" });
    row.classList.remove("is-found");
    void row.offsetWidth;
    row.classList.add("is-found");
    row.querySelector("a.row-link, a.row-btn, button")?.focus({ preventScroll: true });
  }

  // What is typed, when nothing else fits: a question for the assistant, or a task without Claude.
  function fallback(query) {
    const bar = document.querySelector(".command-bar");
    if (!bar || !query) return null;
    const asks = bar.hasAttribute("data-ask");
    const label = textOf(bar.querySelector("label"));
    return {
      kind: asks ? "Ask" : "Do",
      title: asks ? `${label}: “${query}”` : `Add a task: “${query}”`,
      detail: "",
      action: () => {
        const command = bar.querySelector("#command-input");
        command.value = query;
        bar.requestSubmit();
      },
    };
  }

  function render() {
    const words = fold(input.value).split(/\s+/).filter(Boolean);
    // Typed: everything that has every word. Not yet: what can be done, without the rows.
    const matches = words.length ? items.filter((item) => words.every((word) => item.text.includes(word))) : items.filter((item) => !item.row);
    shown = matches.slice(0, LIMIT);
    const extra = fallback(input.value.trim());
    if (extra) shown.push(extra);
    list.replaceChildren(
      ...shown.map((item, index) => {
        const option = document.createElement("li");
        option.className = "palette-item";
        option.id = `palette-${index}`;
        option.dataset.index = index;
        option.setAttribute("role", "option");
        const kind = document.createElement("span");
        kind.className = "label palette-kind";
        kind.textContent = item.kind;
        const title = document.createElement("span");
        title.className = "palette-title trunc";
        title.textContent = item.title;
        const detail = document.createElement("span");
        detail.className = "muted palette-detail trunc";
        detail.textContent = item.kind === "Go" ? "" : item.detail;
        option.append(kind, title, detail);
        return option;
      }),
    );
    if (!shown.length) {
      const empty = document.createElement("li");
      empty.className = "muted palette-empty";
      empty.textContent = "Nothing matches.";
      list.append(empty);
    }
    active = Math.min(active, Math.max(0, shown.length - 1));
    mark();
  }

  function mark() {
    list.querySelectorAll(".palette-item").forEach((option) => {
      const on = Number(option.dataset.index) === active;
      option.setAttribute("aria-selected", String(on));
      if (on) option.scrollIntoView({ block: "nearest" });
    });
    input.setAttribute("aria-activedescendant", shown.length ? `palette-${active}` : "");
  }

  function keys(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      active = (active + step + shown.length) % Math.max(1, shown.length);
      mark();
    } else if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      if (shown[active]) run(shown[active]);
    }
  }

  function run(item) {
    dialog.close();
    item.action();
  }

  function open() {
    if (!dialog) build();
    // Over a details dialog the palette would sit under it: those close first.
    document.querySelectorAll("dialog[open]").forEach((other) => other !== dialog && other.close());
    items = collect();
    input.value = "";
    active = 0;
    render();
    dialog.showModal();
    input.focus();
  }

  document.addEventListener("keydown", (event) => {
    if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
    event.preventDefault();
    if (dialog?.open) dialog.close();
    else open();
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-palette-open]")) return;
    open();
  });
})();
