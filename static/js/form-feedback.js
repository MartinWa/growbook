/* Shared save feedback; only the originating form request owns its controls. */
(() => {
  "use strict";
  const readOnlyTypes = new Set([
    "text", "search", "url", "tel", "email", "password", "number",
    "date", "month", "week", "time", "datetime-local"
  ]);

  window.growbookFormFeedback = (form, { errors, status, progress, successTarget, onSuccess, onIdle }) => {
    if (!window.htmx) return;
    let request;

    function fromForm(event) {
      return (event.detail.requestConfig?.elt || event.detail.elt) === form;
    }

    function currentRequest(event) {
      return fromForm(event) && request && event.detail.xhr === request.xhr;
    }

    function fieldInputs(name) {
      const control = form.elements.namedItem(name);
      if (!control) return [];
      return control instanceof Element ? [control] : [...control];
    }

    function revealInput(input) {
      for (let parent = input.parentElement; parent && parent !== form; parent = parent.parentElement) {
        if (parent.tagName === "DETAILS") parent.open = true;
      }
      const field = input.closest("[data-field-name]");
      if (field) field.hidden = false;
    }
    form.addEventListener("invalid", event => revealInput(event.target), true);

    function syncFieldErrors() {
      for (const slot of form.querySelectorAll("[data-form-field-errors]")) {
        const name = slot.dataset.formFieldErrors;
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
          if (error) revealInput(input);
        }
      }
    }

    function showError(message) {
      errors.textContent = message;
      errors.focus();
    }

    function lockControls() {
      return [...form.querySelectorAll("button, input:not([type=hidden]), select, textarea")].map(input => {
        const property = input instanceof HTMLTextAreaElement ||
          input instanceof HTMLInputElement && readOnlyTypes.has(input.type) ? "readOnly" : "disabled";
        const value = input[property];
        input[property] = true;
        return { input, property, value };
      });
    }

    form.addEventListener("htmx:configRequest", event => {
      if (!fromForm(event) || event.detail.target !== errors) return;
      // A login in another tab rotates Django's CSRF cookie without changing this draft.
      const cookie = document.cookie.split("; ").find(value => value.startsWith("csrftoken="));
      if (cookie) event.detail.parameters.csrfmiddlewaretoken = decodeURIComponent(cookie.split("=")[1]);
    });
    form.addEventListener("htmx:beforeRequest", event => {
      if (!fromForm(event) || event.detail.target !== errors) return;
      if (event.defaultPrevented || request?.pending) event.preventDefault();
    });
    form.addEventListener("htmx:beforeSend", event => {
      if (!fromForm(event) || event.detail.target !== errors) return;
      // Lock only after all cancellable guards have accepted the request.
      const current = request = {
        xhr: event.detail.xhr,
        submissionKey: event.detail.requestConfig.parameters.submission_key,
        pending: true,
        controls: lockControls()
      };
      form.setAttribute("aria-busy", "true");
      form.dispatchEvent(new CustomEvent("form:busy"));
      errors.replaceChildren();
      syncFieldErrors();
      progress.hidden = false;
      progress.value = 0;
      status.textContent = form.dataset.uploading;
      // Native upload events retain their XHR identity, including late progress events.
      current.xhr.upload.addEventListener("progress", upload => {
        if (request !== current || !current.pending || !upload.lengthComputable) return;
        progress.value = 100 * upload.loaded / upload.total;
        if (progress.value >= 100) status.textContent = form.dataset.processing;
      });
    });
    document.addEventListener("htmx:beforeSwap", event => {
      if (!currentRequest(event) ||
          (event.detail.target !== errors && event.detail.target !== successTarget)) return;
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
      if (!currentRequest(event)) return;
      if (event.detail.target === errors) {
        syncFieldErrors();
        errors.focus();
      } else if (event.detail.target === successTarget && onSuccess?.(event, request.submissionKey)) {
        errors.replaceChildren();
        syncFieldErrors();
      }
    });
    form.addEventListener("htmx:afterRequest", event => {
      if (!currentRequest(event) || !request.pending) return;
      request.pending = false;
      for (const { input, property, value } of request.controls) input[property] = value;
      form.removeAttribute("aria-busy");
      progress.hidden = true;
      status.textContent = "";
      form.dispatchEvent(new CustomEvent("form:idle"));
      onIdle?.();
    });
    for (const name of ["htmx:sendError", "htmx:timeout"]) {
      form.addEventListener(name, event => {
        if (currentRequest(event)) showError(form.dataset.networkError);
      });
    }
    if (errors.textContent.trim()) {
      syncFieldErrors();
      errors.focus();
    }
  };
})();
