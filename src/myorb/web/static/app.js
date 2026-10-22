// Registers the service worker that makes myOrb installable as an app.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Without it myOrb still works, it just cannot be installed or show the offline page.
    });
  });
}

// Moving between pages (hx-boost on <body>) replaces only #app, so the media dock and its
// music keep playing. Only links are boosted: forms submit normally, so their error pages
// (HTTP 400) and redirects to other sites (OAuth) work as usual.
document.addEventListener("htmx:beforeProcessNode", (event) => {
  const form = event.target;
  if (form.tagName === "FORM" && !form.hasAttribute("hx-boost")) form.setAttribute("hx-boost", "false");
});

document.addEventListener("htmx:beforeSwap", (event) => {
  if (!event.detail.boosted) return;
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
