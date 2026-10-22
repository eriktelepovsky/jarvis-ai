// Passkey sign-in and registration using the WebAuthn JSON helpers built into browsers.

function passkeysSupported() {
  return (
    window.PublicKeyCredential &&
    typeof PublicKeyCredential.parseCreationOptionsFromJSON === "function" &&
    typeof PublicKeyCredential.parseRequestOptionsFromJSON === "function"
  );
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Something went wrong. Try again.");
  }
  return data;
}

function showError(container, error) {
  const element = container.querySelector("[data-passkey-error]") ||
    container.parentElement.querySelector("[data-passkey-error]");
  // The browser reports a cancelled or timed-out dialog as NotAllowedError.
  element.textContent = error.name === "NotAllowedError"
    ? "The passkey request was cancelled."
    : error.message;
  element.hidden = false;
}

document.addEventListener("DOMContentLoaded", () => {
  const loginButton = document.querySelector("[data-passkey-login]");
  if (loginButton) {
    if (!passkeysSupported()) {
      loginButton.hidden = true;
    }
    loginButton.addEventListener("click", async () => {
      try {
        const options = await postJSON("/login/passkey/options");
        const credential = await navigator.credentials.get({
          publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(options),
        });
        const result = await postJSON("/login/passkey/verify", {
          credential: credential.toJSON(),
          next: loginButton.dataset.next,
        });
        window.location.assign(result.redirect);
      } catch (error) {
        showError(loginButton.parentElement, error);
      }
    });
  }

  const registerForm = document.querySelector("[data-passkey-register]");
  if (registerForm) {
    registerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        if (!passkeysSupported()) {
          throw new Error("This browser does not support passkeys.");
        }
        const options = await postJSON("/settings/security/passkeys/options");
        const credential = await navigator.credentials.create({
          publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(options),
        });
        await postJSON("/settings/security/passkeys", {
          name: registerForm.elements.name.value,
          credential: credential.toJSON(),
        });
        window.location.reload();
      } catch (error) {
        showError(registerForm, error);
      }
    });
  }
});
