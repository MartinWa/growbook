/* Shared selected details, transient navigation state and bounded form feedback. */
(() => {
  "use strict";
  if (!window.htmx) return;
  const expansions = new WeakMap();

  function initializeRows(root) {
    root.querySelectorAll("[data-record-workspace]").forEach(workspace => {
      const browser = workspace.querySelector("[data-record-browser]");
      const pane = workspace.querySelector("[data-record-detail-pane]");
      const returnButton = pane.querySelector("[data-record-return]");
      for (const row of browser.querySelectorAll(".register-expanded")) pane.insertBefore(row, returnButton);
      pane.hidden = false;
      workspace.classList.add("record-workspace-enhanced");
      if (returnButton.dataset.registerReady) return;
      returnButton.dataset.registerReady = "true";
      returnButton.addEventListener("click", () => {
        const selected = workspace.querySelector('[data-register-toggle][aria-expanded="true"]');
        if (!selected) return;
        for (let parent = selected.parentElement; parent && parent !== workspace; parent = parent.parentElement) {
          if (parent.tagName === "DETAILS" && !parent.querySelector(":scope > summary")?.contains(selected)) {
            parent.open = true;
          }
        }
        selected.focus({ preventScroll: true });
        selected.scrollIntoView({ block: "center" });
      });
    });
    root.querySelectorAll("[data-register-toggle]").forEach(link => {
      if (link.dataset.registerReady) return;
      link.dataset.registerReady = "true";
      const row = document.getElementById(link.getAttribute("aria-controls"));
      const details = row.querySelector(".register-details");
      const workspace = link.closest("[data-record-workspace]");
      const placeholder = workspace?.querySelector("[data-record-placeholder]");
      const returnButton = workspace?.querySelector("[data-record-return]");
      details.tabIndex = -1;
      let loaded = false;
      let loading;
      let finishLoad;
      link.setAttribute("role", "button");
      link.addEventListener("keydown", event => {
        if (event.key === " ") {
          event.preventDefault();
          link.click();
        }
      });
      function expand(expanded) {
        if (expanded && workspace) {
          for (const other of workspace.querySelectorAll('[data-register-toggle][aria-expanded="true"]')) {
            if (other !== link) expansions.get(other)?.(false);
          }
        }
        link.setAttribute("aria-expanded", String(expanded));
        row.hidden = !expanded;
        if (placeholder) {
          const selected = !!workspace.querySelector('[data-register-toggle][aria-expanded="true"]');
          placeholder.hidden = selected;
          returnButton.hidden = !selected;
        }
        if (!expanded || loaded) return Promise.resolve();
        if (loading) return loading;
        loading = new Promise(resolve => { finishLoad = resolve; }).finally(() => {
          loading = null;
          finishLoad = null;
          details.removeAttribute("aria-busy");
        });
        details.setAttribute("aria-busy", "true");
        details.textContent = link.dataset.loading;
        window.htmx.ajax("GET", link.dataset.detailsUrl, {
          target: details,
          swap: "innerHTML",
        }).catch(() => {
          details.textContent = link.dataset.loadError;
          finishLoad?.();
        });
        return loading;
      }
      expansions.set(link, expand);
      link.addEventListener("click", event => {
        // Preserve ordinary open-in-new-tab/window shortcuts and the no-JS link fallback.
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const expanded = link.getAttribute("aria-expanded") !== "true";
        expand(expanded);
        if (expanded && workspace) {
          const top = details.getBoundingClientRect().top;
          const headerBottom = document.querySelector(".site-header-sticky")?.getBoundingClientRect().bottom || 0;
          const lineHeight = parseFloat(getComputedStyle(details).lineHeight);
          // Navigate at activation time; late responses and history restoration must not steal focus.
          if (top < headerBottom || top + lineHeight > innerHeight) {
            details.focus({ preventScroll: true });
            details.scrollIntoView({ block: "start" });
          }
        }
      });
      details.addEventListener("htmx:afterSwap", () => {
        loaded = true;
        finishLoad?.();
      });
      details.addEventListener("htmx:beforeSwap", event => {
        if (event.detail.xhr.status >= 400) {
          event.detail.shouldSwap = false;
          details.textContent = link.dataset.loadError;
          finishLoad?.();
        }
      });
    });
  }
  initializeRows(document);

  function archiveState(root) {
    const toggle = document.querySelector("[data-archive-toggle]");
    if (!toggle || root.id !== "register-results") return;
    toggle.value = root.dataset.archived === "true" ? "0" : "1";
    toggle.dataset.pressed = root.dataset.archived;
    toggle.setAttribute("aria-pressed", root.dataset.archived);
    const field = document.querySelector('.variety-filter input[name="archived"]');
    if (field) field.value = root.dataset.archived === "true" ? "1" : "";
  }
  const registerResults = document.getElementById("register-results");
  if (registerResults) archiveState(registerResults);

  function rememberList() {
    const results = document.getElementById("register-results");
    if (!results) return;
    // Keep list navigation metadata; private HTML, notes and photos are never cached.
    const state = {
      url: location.pathname + location.search,
      disclosures: [...results.querySelectorAll("details[id]")].map(item => [item.id, item.open]),
      expanded: [...results.querySelectorAll('[data-register-toggle][aria-expanded="true"]')]
        .map(link => link.getAttribute("aria-controls")),
      focus: document.activeElement.closest?.(".register-expanded")?.id,
      scroll: [scrollX, scrollY]
    };
    history.replaceState({ ...history.state, garden: state }, "");
  }

  async function restoreList(root, url) {
    const state = history.state?.garden;
    if (root.id !== "register-results" || state?.url !== url) return;
    function disclosures() {
      for (const [id, open] of state.disclosures) {
        const item = document.getElementById(id);
        if (item && root.contains(item)) item.open = open;
      }
    }
    disclosures();
    await Promise.all(state.expanded.map(id => {
      const link = root.querySelector(`[aria-controls="${id}"]`);
      return link ? expansions.get(link)?.(true) : null;
    }));
    if (!root.isConnected || location.pathname + location.search !== url) return;
    disclosures();
    const selected = root.querySelector(`[aria-controls="${state.focus}"]`);
    selected?.focus({ preventScroll: true });
    requestAnimationFrame(() => scrollTo(...state.scroll));
  }
  window.addEventListener("pagehide", rememberList);
  window.addEventListener("pageshow", () => {
    const root = document.getElementById("register-results");
    if (root) restoreList(root, location.pathname + location.search);
  });
  document.addEventListener("growbook:results", event => {
    initializeRows(event.detail.root);
    archiveState(event.detail.root);
    restoreList(event.detail.root, event.detail.url);
  });
  document.querySelector("[data-register-cancel]")?.addEventListener("click", event => {
    // A new edit tab can have a list referrer without an entry to return to.
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
        !document.referrer || history.length < 2) return;
    const previous = new URL(document.referrer);
    if (previous.origin === location.origin &&
        ["/garden/areas/", "/garden/varieties/"].includes(previous.pathname)) {
      event.preventDefault();
      history.back();
    }
  });

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
