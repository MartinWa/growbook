/* Shared list filters replace only results; drafts and native FileLists stay in the form. */
(() => {
  "use strict";
  const page = document.querySelector("[data-filter-page]");
  if (!page) return;
  const resultsId = page.dataset.resultsId;
  const disclosure = page.querySelector("[data-filter-disclosure]");
  const panel = document.getElementById("filter-panel");
  const form = page.querySelector("[data-filter-form]");
  const open = document.getElementById("open-filters");
  const close = panel.querySelector("[data-dialog-close]");
  const status = document.getElementById("filter-status");
  const drawerStatus = document.getElementById("filter-drawer-status");
  const capture = document.getElementById("capture-form");
  const wide = matchMedia("(min-width: 900px)");
  const dialog = document.createElement("dialog");
  dialog.id = "filter-dialog";
  dialog.className = "filter-drawer";
  dialog.setAttribute("aria-labelledby", "filter-title");
  document.body.append(dialog);
  function layout() {
    if (dialog.open) dialog.close();
    if (wide.matches) {
      disclosure.append(panel);
      disclosure.open = true;
    } else {
      dialog.append(panel);
      disclosure.open = false;
    }
    open.hidden = wide.matches;
    close.hidden = wide.matches;
  }
  page.classList.add("filters-enhanced");
  wide.addEventListener("change", layout);
  layout();
  open.addEventListener("click", () => {
    dialog.showModal();
    form.querySelector("input:not([type=hidden]), select").focus();
  });
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const controls = [...dialog.querySelectorAll(
      "button, input, select, textarea, a[href], summary, [tabindex]"
    )].filter(control => !control.disabled && control.tabIndex >= 0 && control.getClientRects().length);
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  dialog.addEventListener("close", () => {
    if (!wide.matches) open.focus();
  });
  dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  let controller;
  let debounce;
  function setFields(url) {
    for (const field of form.querySelectorAll("input[name], select[name]")) {
      if (field.type === "checkbox") field.checked = url.searchParams.has(field.name);
      else field.value = url.searchParams.get(field.name) || "";
    }
  }
  function filterState(url) {
    const state = new URLSearchParams(url.searchParams);
    // A new observation belongs on page 1 even when the quick form sits on a later page.
    state.delete("page");
    return state.toString();
  }
  function setLinks(url) {
    if (capture) {
      const query = capture.elements.namedItem("filter_query");
      query.value = query.defaultValue = filterState(url);
    }
    const languageNext = document.querySelector("[data-language-form] input[name=next]");
    if (languageNext) languageNext.value = url.pathname + url.search;
  }
  function fieldInputs(name) {
    const control = form.elements.namedItem(name);
    if (!control) return [];
    return control instanceof Element ? [control] : [...control];
  }
  function syncFieldFeedback(results, invalid) {
    for (const slot of form.querySelectorAll("[data-filter-field-errors]")) {
      const name = slot.dataset.filterFieldErrors;
      const source = invalid ? results.querySelector(`[data-field-errors="${name}"]`) : null;
      slot.replaceChildren();
      if (source) {
        for (const item of source.querySelectorAll("li")) {
          const message = document.createElement("p");
          message.textContent = item.textContent;
          slot.append(message);
        }
      }
      for (const input of fieldInputs(name)) {
        if (source) input.setAttribute("aria-invalid", "true");
        else input.removeAttribute("aria-invalid");
      }
    }
  }
  function formURL() {
    const url = new URL(form.action);
    for (const [name, value] of new FormData(form)) {
      if (value) url.searchParams.append(name, value);
    }
    return url;
  }
  async function load(url, { push = true, replace = false, focusEntry } = {}) {
    clearTimeout(debounce);
    controller?.abort();
    const request = new AbortController();
    controller = request;
    form.setAttribute("aria-busy", "true");
    status.textContent = "";
    drawerStatus.textContent = "";
    try {
      const response = await fetch(url, {
        headers: { "HX-Request": "true", "HX-Target": resultsId },
        signal: request.signal
      });
      if (response.status === 401 || response.status === 403 || response.redirected) {
        throw new Error(page.dataset.sessionError);
      }
      if (!response.ok && response.status !== 422) throw new Error(page.dataset.filterError);
      const invalid = response.status === 422;
      const documentFragment = new DOMParser().parseFromString(await response.text(), "text/html");
      const results = documentFragment.getElementById(resultsId);
      if (!results) throw new Error(page.dataset.filterError);
      if (request.signal.aborted) return;
      const year = form.elements.namedItem("year");
      const options = results.querySelector("[data-year-options]");
      if (options) {
        const selectedYear = year.value;
        year.replaceChildren(options.content.cloneNode(true));
        year.value = selectedYear;
      }
      if (results.hasAttribute("data-year")) year.value = results.dataset.year;
      document.getElementById(resultsId).replaceWith(results);
      syncFieldFeedback(results, invalid);
      if (invalid) {
        // The results summary sits behind the modal drawer; repeat it where the fields are.
        const message = page.dataset.invalidFilters;
        if (dialog.open) drawerStatus.textContent = message;
        else status.textContent = message;
      }
      if (results.dataset.heading) {
        document.querySelector(".page-heading h1").textContent = results.dataset.heading;
        document.title = results.dataset.heading + " · growbook";
        const create = document.querySelector("[data-filter-create]");
        create.href = results.dataset.createUrl;
        create.hidden = results.dataset.hasRecords === "false";
      }
      document.dispatchEvent(new CustomEvent("growbook:results", { detail: { root: results } }));
      // No private HTML is cached in history or browser storage.
      if (url.href !== location.href) {
        if (replace) history.replaceState(null, "", url);
        else if (push) history.pushState(null, "", url);
      }
      setLinks(url);
      if (focusEntry) document.getElementById(`entry-${focusEntry}`)?.querySelector("h2").focus();
    } catch (error) {
      if (error.name !== "AbortError") {
        status.textContent = error.message === page.dataset.sessionError
          ? page.dataset.sessionError : page.dataset.filterError;
        drawerStatus.textContent = status.textContent;
      }
    } finally {
      if (controller === request) form.removeAttribute("aria-busy");
    }
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    load(formURL());
  });
  form.addEventListener("change", event => {
    // Search is handled by its debounced input listener and the submit path.
    if (event.target.name !== "q") load(formURL());
  });
  form.elements.namedItem("q")?.addEventListener("input", event => {
    clearTimeout(debounce);
    if (!event.isComposing) debounce = setTimeout(() => load(formURL()), 300);
  });
  form.querySelector("[data-filter-submit]").hidden = true;
  document.addEventListener("click", event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const errorLink = event.target.closest(`#${resultsId} .error-summary a`);
    if (errorLink) {
      const field = form.querySelector(errorLink.getAttribute("href"));
      if (field) {
        event.preventDefault();
        if (!wide.matches && !dialog.open) dialog.showModal();
        field.focus();
      }
      return;
    }
    const link = event.target.closest("a[data-filter-link]");
    if (!link) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== form.action.replace(location.origin, "")) return;
    event.preventDefault();
    setFields(url);
    const fromResults = !!link.closest("#" + resultsId);
    load(url).then(() => {
      if (fromResults) document.getElementById(resultsId).querySelector("[data-result-focus]").focus();
    });
  });
  window.addEventListener("popstate", () => {
    const url = new URL(location.href);
    setFields(url);
    load(url, { push: false });
  });
  document.addEventListener("diary:saved", event => {
    // Selected filters may still be loading and not yet appear in the current URL.
    const url = formURL();
    url.search = filterState(url);
    load(url, { push: false, replace: true, focusEntry: event.detail.entryId });
  });
})();
