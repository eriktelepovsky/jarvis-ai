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
