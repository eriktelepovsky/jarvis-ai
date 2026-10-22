// Copy buttons: <button data-copy="#element-id"> copies the element's text.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) {
    return;
  }
  const source = document.querySelector(button.dataset.copy);
  await navigator.clipboard.writeText(source.textContent.trim());
  const label = button.textContent;
  button.textContent = "Copied";
  setTimeout(() => {
    button.textContent = label;
  }, 1500);
});
