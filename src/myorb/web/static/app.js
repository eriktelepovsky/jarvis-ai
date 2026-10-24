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

// The Slovak keyboard on an iPhone writes "ť" as "t" plus a separate caron, which the fonts do
// not show: the letter looked like a plain "t". Each letter and its accents become one
// character as they are typed, with the cursor where it was.
function composeAccents(event) {
  const field = event.target;
  if (event.isComposing || !field.matches?.('input:not([type]), input[type="text"], input[type="search"], textarea')) return;
  const text = field.value.normalize("NFC");
  if (text === field.value) return;
  const cursor = field.value.slice(0, field.selectionEnd ?? field.value.length).normalize("NFC").length;
  field.value = text;
  field.setSelectionRange(cursor, cursor);
}
document.addEventListener("input", composeAccents);
document.addEventListener("compositionend", composeAccents);

// Menus (<details class="menu">): one open at a time, closed by a click elsewhere or Escape.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll("details.menu[open]").forEach((menu) => (menu.open = false));
});
