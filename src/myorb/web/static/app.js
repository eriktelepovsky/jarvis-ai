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

// An email's frame is as tall as its content, so the page scrolls rather than the frame; a
// folded message is sized when it is opened, and a frame follows its content as it changes
// (late styles, fonts, images shown).
const emailFrameWatch = new WeakMap();
function sizeEmailFrame(frame) {
  const page = frame.contentDocument?.documentElement;
  if (!page || !frame.offsetWidth) return;
  // Measured at no height, else a frame made wider could not get shorter. The extra pixel
  // covers a fractional height, which would leave the frame a pixel to scroll on its own.
  frame.style.height = "0px";
  // An email laid out wider than the frame (a fixed 600–700px newsletter) is scaled down to
  // fit, as Gmail's app does, rather than scrolled sideways.
  page.style.zoom = "";
  const wide = page.scrollWidth / page.clientWidth;
  if (wide > 1.01) page.style.zoom = String(Math.max(0.4, 1 / wide));
  let height = Math.ceil(Math.max(page.scrollHeight, frame.contentDocument.body?.scrollHeight || 0) * (Number(page.style.zoom) || 1)) + 1;
  frame.style.height = `${height}px`;
  // Browsers differ in whether a zoomed page reports its sizes zoomed. Content and frame are
  // compared in the same units, and the frame grows until nothing is cut off.
  for (let tries = 0; tries < 3 && page.scrollHeight > page.clientHeight + 1; tries += 1) {
    height = Math.ceil((height * page.scrollHeight) / page.clientHeight) + 1;
    frame.style.height = `${height}px`;
  }
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

