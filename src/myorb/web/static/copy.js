// Copy buttons: <button data-copy="#element-id"> copies the element's text (a field's value).
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) {
    return;
  }
  const source = document.querySelector(button.dataset.copy);
  await navigator.clipboard.writeText((source.value ?? source.textContent).trim());
  const label = button.textContent;
  button.textContent = "Copied";
  setTimeout(() => {
    button.textContent = label;
  }, 1500);
});

// Generate buttons: <button data-generate="#field-id"> fills the field with a new random secret
// (32 bytes, URL-safe base64) and shows it, so it can be copied into the user's own app too.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-generate]");
  if (!button) {
    return;
  }
  const field = document.querySelector(button.dataset.generate);
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  field.value = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  field.type = "text";
  field.dispatchEvent(new Event("input", { bubbles: true }));
});
