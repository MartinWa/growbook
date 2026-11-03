/* Independent row expansion and bounded form feedback. No browser persistence or inline eval. */
(() => {
  "use strict";
  if (!window.htmx) return;

  function initializeRows(root) {
    root.querySelectorAll("[data-register-toggle]").forEach(link => {
      if (link.dataset.registerReady) return;
      link.dataset.registerReady = "true";
      const row = document.getElementById(link.getAttribute("aria-controls"));
      const details = row.querySelector(".register-details");
      let loaded = false;
      let loading = false;
      link.setAttribute("role", "button");
      link.addEventListener("keydown", event => {
        if (event.key === " ") {
          event.preventDefault();
          link.click();
        }
      });
      link.addEventListener("click", event => {
        // Preserve ordinary open-in-new-tab/window shortcuts and the no-JS link fallback.
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const expanded = link.getAttribute("aria-expanded") !== "true";
        link.setAttribute("aria-expanded", String(expanded));
        row.hidden = !expanded;
        if (!expanded || loaded || loading) return;
        loading = true;
        details.setAttribute("aria-busy", "true");
        details.textContent = link.dataset.loading;
        window.htmx.ajax("GET", link.dataset.detailsUrl, {
          target: details,
          swap: "innerHTML",
        }).catch(() => {
          details.textContent = link.dataset.loadError;
        }).finally(() => {
          loading = false;
          details.removeAttribute("aria-busy");
        });
      });
      details.addEventListener("htmx:afterSwap", () => { loaded = true; });
      details.addEventListener("htmx:beforeSwap", event => {
        if (event.detail.xhr.status >= 400) {
          event.detail.shouldSwap = false;
          details.textContent = link.dataset.loadError;
        }
      });
    });
  }
  initializeRows(document);
  document.addEventListener("growbook:results", event => initializeRows(event.detail.root));

  document.querySelectorAll("[data-register-form]").forEach(form => {
    const errors = form.querySelector('[role="alert"]');
    const status = form.querySelector("[data-register-status]");
    const progress = form.querySelector("[data-register-progress]");
    let busy = false;
    let disabled = [];
    let readOnly = [];

    function revealInput(input) {
      for (let parent = input.parentElement; parent && parent !== form; parent = parent.parentElement) {
        if (parent.tagName === "DETAILS") parent.open = true;
      }
      const field = input.closest("[data-field-name]");
      if (field) field.hidden = false;
    }
    form.addEventListener("invalid", event => revealInput(event.target), true);

    function fieldInputs(name) {
      const control = form.elements.namedItem(name);
      if (!control) return [];
      return control instanceof Element ? [control] : [...control];
    }

    function revealFieldErrors() {
      for (const slot of form.querySelectorAll("[data-register-field-errors]")) {
        const name = slot.dataset.registerFieldErrors;
        const error = errors.querySelector(`[data-field-errors="${name}"]`);
        slot.replaceChildren();
        if (error) slot.append(error.cloneNode(true));
        for (const input of fieldInputs(name)) {
          if (error) input.setAttribute("aria-invalid", "true");
          else input.removeAttribute("aria-invalid");
          const descriptions = new Set((input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
          if (error) descriptions.add(slot.id);
          else descriptions.delete(slot.id);
          if (descriptions.size) input.setAttribute("aria-describedby", [...descriptions].join(" "));
          else input.removeAttribute("aria-describedby");
        }
      }
      for (const error of errors.querySelectorAll("[data-field-errors]")) {
        fieldInputs(error.dataset.fieldErrors).forEach(revealInput);
      }
    }

    function showError(message) {
      errors.textContent = message;
      errors.focus();
    }

    function setBusy(value) {
      busy = value;
      if (value) {
        disabled = Array.from(form.querySelectorAll("button, select, input[type=file], input[type=checkbox]"))
          .filter(input => !input.disabled);
        readOnly = Array.from(form.querySelectorAll("textarea, input:not([type=hidden]):not([type=file]):not([type=checkbox])"))
          .filter(input => !input.readOnly);
        disabled.forEach(input => { input.disabled = true; });
        readOnly.forEach(input => { input.readOnly = true; });
        form.setAttribute("aria-busy", "true");
      } else {
        disabled.forEach(input => { input.disabled = false; });
        readOnly.forEach(input => { input.readOnly = false; });
        form.removeAttribute("aria-busy");
      }
    }

    form.addEventListener("htmx:configRequest", event => {
      if (event.detail.elt !== form) return;
      const cookie = document.cookie.split("; ").find(value => value.startsWith("csrftoken="));
      if (cookie) event.detail.parameters.csrfmiddlewaretoken = decodeURIComponent(cookie.split("=")[1]);
    });
    form.addEventListener("htmx:beforeRequest", event => {
      if (event.detail.elt !== form) return;
      if (busy) { event.preventDefault(); return; }
      setBusy(true);
      errors.replaceChildren();
      revealFieldErrors();
      progress.hidden = false;
      progress.value = 0;
      status.textContent = form.dataset.uploading;
    });
    form.addEventListener("htmx:xhr:progress", event => {
      if (!busy) return;
      if (event.detail.lengthComputable) {
        progress.value = 100 * event.detail.loaded / event.detail.total;
        if (progress.value >= 100) status.textContent = form.dataset.processing;
      }
    });
    form.addEventListener("htmx:beforeSwap", event => {
      if (event.detail.target !== errors) return;
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
    form.addEventListener("htmx:afterSwap", event => {
      if (event.detail.target === errors) {
        revealFieldErrors();
        errors.focus();
      }
    });
    form.addEventListener("htmx:afterRequest", event => {
      if (event.detail.elt !== form) return;
      setBusy(false);
      progress.hidden = true;
      status.textContent = "";
    });
    ["htmx:sendError", "htmx:timeout"].forEach(name => {
      form.addEventListener(name, event => {
        if (event.detail.elt === form) showError(form.dataset.networkError);
      });
    });
    if (errors.textContent.trim()) {
      revealFieldErrors();
      errors.focus();
    }
  });
})();
