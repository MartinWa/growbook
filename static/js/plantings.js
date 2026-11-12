/* Dependent variety choices use HTMX; preserve drafts and keyboard focus. */
(() => {
  "use strict";
  const form = document.querySelector("[data-register-form] #id_crop")?.closest("form");
  const status = form?.querySelector("#variety-status");
  if (!form || !status || !window.htmx) return;
  const crop = form.querySelector("#id_crop");
  const label = form.querySelector("#id_label");
  const generated = form.querySelector("#id_generated_label");
  let generatedLabel = generated.value;
  let customLabel = label.value !== generatedLabel;
  function updateLabel() {
    if (!form.hasAttribute("data-new-planting") || customLabel) return;
    const cropName = crop.selectedOptions[0]?.dataset.recordName || "";
    // Keep the draft when an archived or deleted crop is offered as unavailable.
    if (crop.value && !cropName) return;
    const variety = form.querySelector("#id_variety");
    const varietyName = variety.selectedOptions[0]?.dataset.recordName || "";
    if (variety.value && !varietyName) return;
    generatedLabel = cropName + (varietyName ? `-${varietyName}` : "");
    label.value = generatedLabel;
    generated.value = generatedLabel;
  }
  label.addEventListener("input", () => {
    customLabel = label.value !== generatedLabel;
  });
  form.addEventListener("change", event => {
    if (event.target.id === "id_variety") updateLabel();
  });
  updateLabel();
  const lifespan = form.querySelector("#id_lifespan");
  const endYear = form.querySelector("#id_end_year");
  const endField = endYear.closest("[data-field-name]");
  const endHelp = endField.querySelector("[data-annual-end-help]");
  function updateEndYear() {
    const annual = lifespan.value === "annual";
    // Preserve a typed end year so the user can correct it instead of silently losing it.
    endField.hidden = annual && !endYear.value && endYear.getAttribute("aria-invalid") !== "true";
    endHelp.hidden = !annual || !endYear.value;
  }
  lifespan.addEventListener("change", updateEndYear);
  endYear.addEventListener("input", updateEndYear);
  updateEndYear();
  const retry = form.querySelector("[data-variety-retry]");
  retry.addEventListener("click", () => {
    crop.focus();
    window.htmx.trigger(crop, "change");
  });

  // Remember each offered variety's crop so an incompatible selection is cleared immediately.
  function markChoices() {
    form.querySelectorAll("#id_variety option[value]:not([value=''])").forEach(option => {
      option.dataset.crop = crop.value;
    });
  }
  markChoices();
  crop.addEventListener("change", () => {
    const variety = form.querySelector("#id_variety");
    if (variety.selectedOptions[0]?.dataset.crop !== crop.value) variety.value = "";
    variety.querySelectorAll("option[value]:not([value=''])").forEach(option => {
      if (option.dataset.crop !== crop.value) option.remove();
    });
    updateLabel();
  });
  form.addEventListener("htmx:configRequest", event => {
    if (event.detail.elt !== form) return;
    const pending = status.hasAttribute("aria-busy");
    window.htmx.trigger(crop, "htmx:abort");
    // If saving fails, let the user reload the choices cancelled by that save.
    if (pending) retry.hidden = false;
  });
  form.addEventListener("htmx:beforeRequest", event => {
    if (event.detail.elt === crop) {
      status.setAttribute("aria-busy", "true");
      status.textContent = "";
      retry.hidden = true;
    }
  });
  form.addEventListener("htmx:beforeSwap", event => {
    if (event.detail.target?.id !== "id_variety") return;
    if (event.detail.xhr.status >= 400) {
      status.closest("details").open = true;
      event.detail.shouldSwap = false;
      retry.hidden = false;
      status.textContent = event.detail.xhr.status === 401 || event.detail.xhr.status === 403
        ? form.dataset.sessionError : status.dataset.choicesError;
    }
  });
  form.addEventListener("htmx:afterSwap", event => {
    if (event.detail.target?.id !== "id_variety") return;
    markChoices();
    updateLabel();
    status.textContent = status.dataset.choicesReady;
  });
  form.addEventListener("htmx:afterRequest", event => {
    if (event.detail.elt === crop) {
      status.removeAttribute("aria-busy");
      if (event.detail.failed) retry.hidden = false;
    }
  });
  ["htmx:sendError", "htmx:timeout"].forEach(name => {
    form.addEventListener(name, event => {
      if (event.detail.elt === crop) {
        status.closest("details").open = true;
        status.textContent = status.dataset.choicesError;
        retry.hidden = false;
      }
    });
  });
})();
