/* Small, page-local state only. No draft/photos are persisted in browser storage. */
(() => {
  "use strict";
  const form = document.getElementById("capture-form");
  if (!form || !window.htmx) return;
  const errors = document.getElementById("capture-errors");
  const status = document.getElementById("capture-status");
  const progress = document.getElementById("capture-progress");
  const save = document.getElementById("save-entry");
  const previews = document.getElementById("photo-previews");
  const count = document.getElementById("photo-count");
  const inputs = [document.getElementById("id_camera"), document.getElementById("id_library")];
  const selections = new Map(inputs.map(input => [input, []]));
  let urls = [];
  let busy = false;
  // Only use accumulated-selection mode when assigning FileLists is supported.
  let canAccumulate = false;
  try {
    const probe = new DataTransfer();
    const input = document.createElement("input");
    input.type = "file";
    input.files = probe.files;
    canAccumulate = true;
  } catch (_) { /* The native picker still works without accumulating selections. */ }

  function setBusy(value) {
    busy = value;
    save.disabled = value;
    document.getElementById("id_body").readOnly = value;
    document.getElementById("id_date").readOnly = value;
    inputs.forEach(input => { input.disabled = value; });
    previews.querySelectorAll("button").forEach(button => { button.disabled = value; });
    if (value) form.setAttribute("aria-busy", "true");
    else form.removeAttribute("aria-busy");
  }

  function showError(message) {
    errors.textContent = message;
    errors.focus();
  }

  function renderPreviews() {
    urls.forEach(url => URL.revokeObjectURL(url));
    urls = [];
    previews.replaceChildren();
    let total = 0;
    for (const input of inputs) {
      const files = selections.get(input);
      total += files.length;
      files.forEach((file, index) => {
        const figure = document.createElement("figure");
        const image = document.createElement("img");
        const url = URL.createObjectURL(file);
        urls.push(url);
        image.src = url;
        image.alt = file.name;
        const caption = document.createElement("figcaption");
        caption.textContent = file.name;
        image.addEventListener("error", () => {
          image.remove();
          const hint = document.createElement("p");
          hint.textContent = form.dataset.previewUnavailable;
          figure.prepend(hint);
        });
        figure.append(image, caption);
        if (canAccumulate) {
          const remove = document.createElement("button");
          remove.type = "button";
          remove.textContent = form.dataset.remove;
          remove.setAttribute("aria-label", `${form.dataset.remove}: ${file.name}`);
          remove.addEventListener("click", () => {
            if (busy) return;
            files.splice(index, 1);
            updateFiles(input);
            renderPreviews();
          });
          figure.append(remove);
        }
        previews.append(figure);
      });
    }
    count.textContent = total ? `${form.dataset.photosSelected} ${total}` : "";
  }

  function updateFiles(input) {
    const transfer = new DataTransfer();
    selections.get(input).forEach(file => transfer.items.add(file));
    input.files = transfer.files;
  }

  inputs.forEach(input => input.addEventListener("change", () => {
    const chosen = Array.from(input.files);
    selections.set(input, canAccumulate ? selections.get(input).concat(chosen) : chosen);
    if (canAccumulate) updateFiles(input);
    renderPreviews();
  }));

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
  form.addEventListener("htmx:afterSwap", () => errors.focus());
  form.addEventListener("htmx:afterRequest", () => {
    setBusy(false);
    progress.hidden = true;
    status.textContent = "";
  });
  ["htmx:sendError", "htmx:timeout"].forEach(name => {
    form.addEventListener(name, () => showError(form.dataset.networkError));
  });
  window.addEventListener("pagehide", () => urls.forEach(url => URL.revokeObjectURL(url)));
})();
