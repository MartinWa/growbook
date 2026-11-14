/* Shared list filters replace only results; drafts and native FileLists stay in the form. */
(() => {
  "use strict";
  const page = document.querySelector("[data-filter-page]");
  if (!page) return;
  const resultsId = page.dataset.resultsId;
  const disclosure = page.querySelector("[data-filter-disclosure]");
  const form = page.querySelector("[data-filter-form]");
  const status = document.getElementById("filter-status");
  const retry = form.querySelector("[data-filter-retry]");
  const capture = document.getElementById("capture-form");
  disclosure?.addEventListener("keydown", event => {
    if (event.key !== "Escape" || !disclosure.open) return;
    // Preserve a search input's value when closing the filters.
    event.preventDefault();
    disclosure.open = false;
    disclosure.querySelector(":scope > summary").focus();
  });
  let controller;
  let debounce;
  let retryURL;
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
  async function load(url, { push = true, replace = false, focusEntry, focusResults = false } = {}) {
    clearTimeout(debounce);
    controller?.abort();
    const request = new AbortController();
    controller = request;
    if (retry && document.activeElement === retry) {
      form.querySelector("input:not([type=hidden]), select")?.focus();
    }
    const focusOrigin = document.activeElement;
    form.setAttribute("aria-busy", "true");
    status.textContent = "";
    retryURL = null;
    if (retry) retry.hidden = true;
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
        if (disclosure) disclosure.open = true;
        status.textContent = page.dataset.invalidFilters;
      }
      if (results.dataset.heading) {
        document.querySelector(".page-heading h1").textContent = results.dataset.heading;
        document.title = results.dataset.heading + " · growbook";
        const create = document.querySelector("[data-filter-create]");
        create.href = results.dataset.createUrl;
        create.hidden = !create.hasAttribute("data-always-visible") && results.dataset.hasRecords === "false";
        const categoryCreate = document.querySelector("[data-filter-category-create]");
        if (categoryCreate) categoryCreate.href = results.dataset.categoryCreateUrl;
        const categoryManage = document.querySelector("[data-filter-category-manage]");
        if (categoryManage) categoryManage.href = results.dataset.categoryManageUrl;
      }
      document.dispatchEvent(new CustomEvent("growbook:results", {
        detail: { root: results, url: url.pathname + url.search }
      }));
      // No private HTML is cached in history or browser storage.
      if (url.href !== location.href) {
        if (replace) history.replaceState(null, "", url);
        else if (push) history.pushState(null, "", url);
      }
      setLinks(url);
      // Only a completed, current request may move focus, and only if the user has
      // not moved elsewhere while it was loading. Removed paging links leave focus on body.
      if (document.activeElement === focusOrigin ||
          (!focusOrigin.isConnected && document.activeElement === document.body)) {
        if (focusEntry) document.getElementById(`entry-${focusEntry}`)?.querySelector("h2").focus();
        else if (focusResults) results.querySelector("[data-result-focus]")?.focus();
      }
    } catch (error) {
      if (!request.signal.aborted && error.name !== "AbortError") {
        status.textContent = error.message === page.dataset.sessionError
          ? page.dataset.sessionError : page.dataset.filterError;
        // Action buttons can change the attempted URL without changing the fields.
        retryURL = url;
        if (retry) retry.hidden = false;
        if (disclosure) disclosure.open = true;
      }
    } finally {
      if (controller === request) form.removeAttribute("aria-busy");
    }
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (retry && event.submitter === retry && retryURL) {
      load(retryURL);
      return;
    }
    const url = formURL();
    if (event.submitter?.hasAttribute("data-archive-toggle")) {
      if (event.submitter.value === "1") url.searchParams.set("archived", "1");
      else url.searchParams.delete("archived");
    }
    load(url);
  });
  form.addEventListener("change", event => {
    if (event.target.name === "area" && !event.target.value) {
      const areaOnly = form.elements.namedItem("area_only");
      if (areaOnly) areaOnly.value = "";
    }
    // Search is handled by its debounced input listener and the submit path.
    if (event.target.name !== "q") load(formURL());
  });
  form.elements.namedItem("q")?.addEventListener("input", event => {
    clearTimeout(debounce);
    retryURL = null;
    if (retry) retry.hidden = true;
    // A changed draft invalidates the previous request during the debounce too.
    controller?.abort();
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
        if (disclosure) disclosure.open = true;
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
    load(url, { focusResults: fromResults });
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
