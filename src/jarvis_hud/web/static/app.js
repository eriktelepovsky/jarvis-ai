// Registers the service worker that makes Jarvis installable as an app.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Without it Jarvis still works, it just cannot be installed or show the offline page.
    });
  });
}
