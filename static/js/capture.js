/* Small, page-local state only. No draft/photos are persisted in browser storage. */
(() => {
  "use strict";
  const form = document.getElementById("capture-form");
  if (!form || !window.htmx) return;
  const errors = document.getElementById("capture-errors");
  const status = document.getElementById("capture-status");
  const progress = document.getElementById("capture-progress");
  const save = document.getElementById("save-entry");
  const inputs = [document.getElementById("id_camera"), document.getElementById("id_library")];
  let busy = false;

  function syncFieldErrors() {
    for (const name of ["date", "title", "body", "camera", "library"]) {
      const input = form.elements.namedItem(name);
      const target = document.getElementById(`capture-${name}-errors`);
      const source = errors.querySelector(`[data-field-errors="${name}"]`);
      target.replaceChildren();
      if (source) {
        // Keep field feedback beside the input when HTMX replaces only the summary.
        for (const item of source.querySelectorAll("li")) {
          const message = document.createElement("p");
          message.textContent = item.textContent;
          target.append(message);
        }
        input.setAttribute("aria-invalid", "true");
      } else input.removeAttribute("aria-invalid");
    }
    for (const slot of form.querySelectorAll("[data-register-field-errors]")) {
      const name = slot.dataset.registerFieldErrors;
      const source = errors.querySelector(`[data-field-errors="${name}"]`);
      slot.replaceChildren();
      if (source) slot.append(source.cloneNode(true));
      form.querySelectorAll(`input[name="${name}"]`).forEach(input => {
        if (source) input.setAttribute("aria-invalid", "true");
        else input.removeAttribute("aria-invalid");
      });
    }
  }

  function setBusy(value) {
    busy = value;
    save.disabled = value;
    document.getElementById("id_body").readOnly = value;
    document.getElementById("id_title").readOnly = value;
    document.getElementById("id_date").readOnly = value;
    inputs.forEach(input => { input.disabled = value; });
    if (value) form.setAttribute("aria-busy", "true");
    else form.removeAttribute("aria-busy");
  }

  function showError(message) {
    errors.textContent = message;
    errors.focus();
  }

  form.addEventListener("htmx:configRequest", event => {
    // A login in another tab rotates Django's CSRF cookie. Refresh the submitted token
    // without discarding the original save key, note or selected files.
    const cookie = document.cookie.split("; ").find(value => value.startsWith("csrftoken="));
    if (cookie) event.detail.parameters.csrfmiddlewaretoken = decodeURIComponent(cookie.split("=")[1]);
  });
  form.addEventListener("htmx:beforeRequest", event => {
    if (event.detail.elt !== form) return;
    if (busy) { event.preventDefault(); return; }
    setBusy(true);
    errors.replaceChildren();
    syncFieldErrors();
    progress.hidden = false;
    progress.value = 0;
    status.textContent = form.dataset.uploading;
  });
  form.addEventListener("htmx:xhr:progress", event => {
    if (event.detail.lengthComputable) {
      progress.value = 100 * event.detail.loaded / event.detail.total;
      if (progress.value >= 100) status.textContent = form.dataset.processing;
    }
  });
  form.addEventListener("htmx:beforeSwap", event => {
    const code = event.detail.xhr.status;
    if ([409, 422, 503].includes(code)) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    } else if (code === 401 || code === 403) {
      event.detail.shouldSwap = false;
      showError(form.dataset.sessionError);
    } else if (code >= 400) {
      event.detail.shouldSwap = false;
      showError(form.dataset.genericError);
    }
  });
  document.addEventListener("htmx:afterSwap", event => {
    if (event.detail.target === errors) {
      syncFieldErrors();
      errors.focus();
    } else if (event.detail.target?.id === "quick-receipt") {
      const saved = event.detail.target.querySelector("[data-capture-saved]");
      if (!saved) return;
      form.reset();
      const key = form.elements.namedItem("submission_key");
      key.value = key.defaultValue = saved.dataset.nextKey;
      errors.replaceChildren();
      syncFieldErrors();
      saved.querySelector("a").focus();
      document.dispatchEvent(new CustomEvent("diary:saved", {
        detail: { entryId: saved.dataset.captureSaved }
      }));
    }
  });
  form.addEventListener("htmx:afterRequest", () => {
    setBusy(false);
    progress.hidden = true;
    status.textContent = "";
  });
  ["htmx:sendError", "htmx:timeout"].forEach(name => {
    form.addEventListener(name, () => showError(form.dataset.networkError));
  });
})();
