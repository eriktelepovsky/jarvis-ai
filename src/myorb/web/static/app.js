// Registers the service worker that makes myOrb installable as an app.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Without it myOrb still works, it just cannot be installed or show the offline page.
    });
  });
}

// Moving between pages and sending forms (hx-boost on <body>) replaces only #app, so the
// media dock and its music keep playing. Forms that lead to another site (OAuth) or change who
// is signed in have hx-boost="false" and submit as usual.
document.addEventListener("htmx:beforeSwap", (event) => {
  if (!event.detail.boosted) return;
  // A page without #app (e.g. sign-in, after the session ended): opened as a whole.
  const { xhr } = event.detail;
  if (xhr.status < 500 && !xhr.responseText.includes('id="app"')) {
    event.detail.shouldSwap = false;
    window.location.href = xhr.responseURL;
    return;
  }
  // A boosted form's error page (HTTP 400, e.g. a wrong password) is shown as it would be.
  if (event.detail.xhr.status === 400) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
  event.detail.target = document.getElementById("app");
  event.detail.selectOverride = "#app";
  event.detail.swapOverride = "outerHTML";
});

// Forms that wait for an answer (connecting a service checks it, saving a key verifies it):
// the button shows that something is happening and cannot be clicked twice.
document.addEventListener("submit", (event) => {
  const form = event.target;
  const button = event.submitter || form.querySelector("[type=submit]");
  if (!button || form.hasAttribute("hx-post")) return;
  // A boosted form: htmx sends it, and its button waits while htmx-request is on the form.
  if (form.closest("[hx-boost]")?.getAttribute("hx-boost") === "true") return;
  // Afterwards: other scripts may handle the form themselves (passkeys, search), and the
  // browser has read the button's name and value for the request by then.
  setTimeout(() => {
    if (event.defaultPrevented) return;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
  });
});

// Coming back with the browser's Back button shows the page as it was left: not loading.
window.addEventListener("pageshow", () => {
  document.querySelectorAll(".is-loading").forEach((button) => {
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    button.disabled = false;
  });
});

// Menus (<details class="menu">): one open at a time, closed by a click elsewhere or Escape.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll("details.menu[open]").forEach((menu) => (menu.open = false));
});

// On an iPhone or iPad, a link to a Gmail inbox opens the Gmail app, through the app's own
// googlegmail:// address; if the app does not open within a moment (not installed), the web
// inbox opens as before. Links to one email stay on the web: the app's addresses cannot open
// an email (tried), only its inbox.
const appleTouch =
  /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
document.addEventListener("click", (event) => {
  if (!appleTouch || event.defaultPrevented) return;
  const link = event.target.closest('a[href^="https://mail.google.com/"]');
  if (!link || !/^#inbox$/.test(new URL(link.href).hash)) return;
  event.preventDefault();
  const fallback = setTimeout(() => (location.href = link.href), 1500);
  document.addEventListener("visibilitychange", () => clearTimeout(fallback), { once: true });
  location.href = "googlegmail://";
});

// Safari on an iPhone or iPad shows Gmail's mobile site, which ignores the desktop address of
// an email (#inbox/<thread>): a link to one goes to the mobile site's address for it instead
// (tried on an iPhone). Before the click handlers below, which then open the new address.
document.addEventListener(
  "click",
  (event) => {
    if (!appleTouch) return;
    const link = event.target.closest('a[href^="https://mail.google.com/mail/u/"]');
    const url = link && new URL(link.href);
    const thread = url?.hash.match(/^#inbox\/([^/]+)$/)?.[1];
    if (thread) link.href = `https://mail.google.com/mail/mu/mp/${url.search}#cv/Inbox/${thread}`;
  },
  true,
);

// Installed on an iPhone's home screen, myOrb opens other sites in its own browser window,
// which does not share Safari's sign-ins (Google, Jira, GitHub…). Such links open in Safari
// itself instead, through its x-safari-https:// address (iOS 17 and later); on older iOS the
// link opens as before.
const installed = navigator.standalone || matchMedia("(display-mode: standalone)").matches;
document.addEventListener("click", (event) => {
  if (!appleTouch || !installed || event.defaultPrevented) return;
  const link = event.target.closest("a[href]");
  if (!link || link.origin === location.origin || !/^https?:$/.test(link.protocol)) return;
  event.preventDefault();
  const fallback = setTimeout(() => (location.href = link.href), 1500);
  document.addEventListener("visibilitychange", () => clearTimeout(fallback), { once: true });
  location.href = `x-safari-${link.href}`;
});

// The reply and forward panels open and close as an accordion: the panel grows from no height
// (or shrinks to none) and what follows moves with it, from where it sits without the panel.
function foldDraft(panel, open) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches || !panel.animate) return Promise.resolve();
  const next = panel.nextElementSibling;
  const style = getComputedStyle(panel);
  const full = {
    height: `${panel.offsetHeight}px`,
    paddingTop: style.paddingTop,
    paddingBottom: style.paddingBottom,
    marginTop: style.marginTop,
    marginBottom: style.marginBottom,
    borderTopWidth: style.borderTopWidth,
    opacity: 1,
  };
  const none = { height: "0px", paddingTop: "0px", paddingBottom: "0px", marginTop: "0px", marginBottom: "0px", borderTopWidth: "0px", opacity: 0 };
  if (next) {
    // The space the messages keep from the head differs with the panel there or not.
    panel.style.display = "none";
    const without = next.getBoundingClientRect().top;
    panel.style.display = "";
    Object.assign(panel.style, none);
    none.marginBottom = `${without - next.getBoundingClientRect().top}px`;
    for (const key of Object.keys(none)) panel.style[key] = "";
  }
  panel.style.overflow = "hidden";
  const fold = panel.animate(open ? [none, full] : [full, none], { duration: 300, easing: "ease-out", fill: "forwards" });
  return fold.finished.then(() => {
    if (open) {
      fold.cancel();
      panel.style.overflow = "";
    }
  });
}

document.addEventListener("htmx:beforeSwap", (event) => {
  if (event.detail.target?.matches?.(".email-draft-place")) event.detail.target.dataset.unfold = "";
});
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target?.dataset?.unfold === undefined) return;
  const panel = document.getElementById("email-draft");
  if (panel?.matches(".email-draft")) foldDraft(panel, true);
});

// Closing an email's reply panel leaves its place for the Reply button to fill again.
document.addEventListener("click", (event) => {
  const panel = event.target.closest("[data-draft-close]")?.closest("#email-draft");
  if (!panel || panel.dataset.closing) return;
  panel.dataset.closing = "true";
  foldDraft(panel, false).then(() => {
    const place = document.createElement("div");
    place.id = "email-draft";
    place.className = "email-draft-place";
    panel.replaceWith(place);
  });
});

// An email's frame is as tall as its content, so the page scrolls rather than the frame; a
// folded message is sized when it is opened, and a frame follows its content as it changes
// (late styles, fonts, images shown).
const emailFrameWatch = new WeakMap();
function sizeEmailFrame(frame) {
  const page = frame.contentDocument?.documentElement;
  if (!page || !frame.offsetWidth) return;
  // What scrolls around the frame (the view over the details, else the page) keeps its place:
  // the frame at no height for a moment would take it back to the top.
  const scroller = frame.closest(".page-view") || document.scrollingElement;
  const top = scroller.scrollTop;
  // Measured at no height, else a frame made wider could not get shorter. The extra pixel
  // covers a fractional height, which would leave the frame a pixel to scroll on its own.
  frame.style.height = "0px";
  // An email laid out wider than the frame (a fixed 600–700px newsletter) is scaled down to
  // fit, as Gmail's app does, rather than scrolled sideways.
  page.style.zoom = "";
  page.style.overflowX = "";
  const wide = page.scrollWidth / page.clientWidth;
  if (wide > 1.01) page.style.zoom = String(Math.max(0.4, 1 / wide));
  // What fits does not scroll sideways: Safari on iOS would otherwise scroll the frame under a
  // swipe to the next email, and leave the email cut off at its left.
  if (wide <= 2.5) page.style.overflowX = "hidden";
  page.scrollLeft = 0;
  let height = Math.ceil(Math.max(page.scrollHeight, frame.contentDocument.body?.scrollHeight || 0) * (Number(page.style.zoom) || 1)) + 1;
  frame.style.height = `${height}px`;
  // Browsers differ in whether a zoomed page reports its sizes zoomed. Content and frame are
  // compared in the same units, and the frame grows until nothing is cut off.
  for (let tries = 0; tries < 3 && page.scrollHeight > page.clientHeight + 1; tries += 1) {
    height = Math.ceil((height * page.scrollHeight) / page.clientHeight) + 1;
    frame.style.height = `${height}px`;
  }
  scroller.scrollTop = top;
  const body = frame.contentDocument.body;
  if (body && emailFrameWatch.get(frame)?.body !== body && "ResizeObserver" in window) {
    emailFrameWatch.get(frame)?.observer.disconnect();
    let last = 0;
    const observer = new ResizeObserver(([entry]) => {
      const height = Math.round(entry.contentRect.height);
      if (height === last) return;
      last = height;
      requestAnimationFrame(() => sizeEmailFrame(frame));
    });
    observer.observe(body);
    emailFrameWatch.set(frame, { body, observer });
  }
}
const sizeEmailFrames = () => document.querySelectorAll("[data-email-frame]").forEach(sizeEmailFrame);
document.addEventListener("DOMContentLoaded", sizeEmailFrames);
document.addEventListener("htmx:afterSettle", sizeEmailFrames);
document.addEventListener("load", (event) => {
  if (event.target.matches?.("[data-email-frame]")) sizeEmailFrame(event.target);
}, true);
document.addEventListener("toggle", (event) => {
  event.target.querySelectorAll?.("[data-email-frame]").forEach(sizeEmailFrame);
}, true);
window.addEventListener("resize", sizeEmailFrames);


// The day at a glance: each event's label goes above or below the track, wherever it does not
// run into the label before it; where neither side is free, the label before it is cut short
// (or this one, when it ends at its event). A meeting too close to the one before it for even
// its time, e.g. two at 14:00, gets a line in its label, under it (above the track, the label
// grows upwards). Laid out again whenever the track's width changes,
// e.g. once the report is opened.
const GLANCE_GAP = 10;
const GLANCE_MIN = 44; // room for the time at least
function layoutGlance(track) {
  if (!track.offsetWidth) return;
  const lanes = [{ end: -Infinity, last: null }, { end: -Infinity, last: null }]; // above, below
  let previous = 1;
  track.querySelectorAll(".glance-item").forEach((item) => {
    const label = item.querySelector(".glance-label");
    if (!label) return;
    label.dataset.text ??= label.innerHTML;
    label.innerHTML = label.dataset.text;
    label.hidden = false;
    label.style.maxWidth = "";
    let width = label.offsetWidth;
    const toEnd = item.classList.contains("end");
    const anchor = toEnd ? item.offsetLeft + item.offsetWidth : item.offsetLeft;
    let start = toEnd ? anchor - width : anchor;
    const fits = (which) => lanes[which].end + GLANCE_GAP <= start;
    let which = 1 - previous;
    if (!fits(which)) which = fits(previous) ? previous : lanes[0].end <= lanes[1].end ? 0 : 1;
    const lane = lanes[which];
    if (!fits(which) && toEnd) {
      width = Math.max(GLANCE_MIN, anchor - lane.end - GLANCE_GAP);
      label.style.maxWidth = `${width}px`;
      start = anchor - width;
    }
    // Only meetings join: lunch and focus blocks never start where one does.
    const meeting = !item.matches(".lunch, .focus");
    const joined = !fits(which) && meeting && lanes.find(({ last }) => last?.meeting && start - GLANCE_GAP - last.start < GLANCE_MIN);
    if (joined) {
      const line = document.createElement("span");
      line.className = "glance-line";
      line.innerHTML = label.innerHTML;
      joined.last.label.append(line);
      joined.last.label.style.maxWidth = "";
      label.hidden = true;
      joined.end = Math.max(joined.end, joined.last.start + joined.last.label.offsetWidth);
      return;
    }
    // Lunch and focus blocks make way: their outline says what they are.
    if (!fits(which) && !meeting) {
      label.hidden = true;
      return;
    }
    if (!fits(which) && lane.last && !lane.last.toEnd) {
      lane.last.label.style.maxWidth = `${Math.max(GLANCE_MIN, start - GLANCE_GAP - lane.last.start)}px`;
    }
    item.classList.toggle("below", which === 1);
    lane.end = start + width;
    lane.last = { label, start, toEnd, meeting: meeting && !toEnd };
    previous = which;
  });
}
const glanceWatch = "ResizeObserver" in window ? new ResizeObserver((entries) => entries.forEach((entry) => layoutGlance(entry.target))) : null;
document.addEventListener("htmx:load", (event) => {
  event.target.querySelectorAll?.(".glance-track").forEach((track) => (glanceWatch ? glanceWatch.observe(track) : layoutGlance(track)));
});
document.addEventListener("DOMContentLoaded", () => document.querySelectorAll(".glance-track").forEach((track) => (glanceWatch ? glanceWatch.observe(track) : layoutGlance(track))));

// Esc on a page of its own (Integrations, Security, …) goes back to the welcome screen, unless
// it closes something first or is typed into a field.
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || event.defaultPrevented || location.pathname === "/") return;
  if (document.querySelector("dialog[open]") || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
  // Through the menu's Home link (signed in only), boosted: the music plays on.
  const home = document.querySelector('nav.nav a[href="/"]');
  if (!home) return;
  event.preventDefault();
  home.click();
});

// A menu behind ⋮ (e.g. an email's other actions) closes on a click elsewhere or on its items.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.email-more[open]").forEach((menu) => {
    if (!menu.contains(event.target) || event.target.closest(".email-more-menu .btn")) menu.open = false;
  });
});

// After an upgrade, a page loaded before it has the old styles and scripts: what it is sent
// now may need the new ones (e.g. a menu shown as plain buttons). It loads again instead: an
// email opened over the list opens as a page of its own, anything else reloads the page.
document.addEventListener("htmx:beforeSwap", (event) => {
  const running = event.detail.xhr?.getResponseHeader("X-Myorb-Version");
  const loaded = document.querySelector('meta[name="myorb-version"]')?.content;
  if (!running || !loaded || running === loaded) return;
  event.detail.shouldSwap = false;
  const config = event.detail.requestConfig;
  if (config?.verb === "get" && event.detail.target?.id === "page-view") location.href = config.path;
  else location.reload();
});

// Changing a task (its Edit link, or its title in the side panel) opens it in a dialog over
// the page, laid out as the Add dialog, rather than the task list. Saved, the dialog closes;
// the welcome screen loads its numbers and panels again, the task list itself.
document.addEventListener("click", (event) => {
  const link = event.target.closest?.('a[href^="/tasks?edit="]');
  if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey) return;
  const id = new URL(link.href, location.href).searchParams.get("edit");
  if (!/^\d+$/.test(id || "")) return;
  event.preventDefault();
  event.stopPropagation();
  let dialog = document.getElementById("task-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "task-dialog";
    dialog.className = "modal modal-add";
    dialog.setAttribute("aria-label", "Change the task");
    document.body.append(dialog);
  }
  htmx.ajax("GET", `/tasks/${id}/form`, { target: dialog, swap: "innerHTML" }).then(() => {
    // Over any dialog open (e.g. the tasks' details), on top of it.
    if (!dialog.open) dialog.showModal();
    dialog.querySelector("input[name=title]")?.focus();
  });
}, true);
document.addEventListener("htmx:afterRequest", (event) => {
  const form = event.detail.elt.closest?.("[data-task-form]");
  if (!form || !event.detail.successful) return;
  form.closest("dialog")?.close();
  if (location.pathname === "/tasks") htmx.ajax("GET", "/tasks", { target: "#app", select: "#app", swap: "outerHTML" });
});
