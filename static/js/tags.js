/* Shared progressive enhancement. Typed checkbox IDs remain the submitted payload. */
(() => {
  "use strict";
  if (!("showPopover" in HTMLElement.prototype)) return;
  document.querySelectorAll("[data-tag-picker]").forEach(picker => {
    const form = picker.closest("form");
    const trigger = picker.querySelector("[data-tag-trigger]");
    const actions = form.querySelector("[data-capture-tools], .capture-actions");
    if (actions) actions.prepend(trigger);
    const popover = picker.querySelector("[data-tag-popover]");
    const search = picker.querySelector("[data-tag-search]");
    const type = picker.querySelector("[data-tag-type]");
    const groups = picker.querySelector("[data-tag-groups]");
    const chips = picker.querySelector("[data-tag-chips]");
    const context = picker.querySelector("[data-tag-context]");
    const options = [...picker.querySelectorAll("[data-tag-option]")];
    const checks = options.map(row => row.querySelector("input"));
    const periods = [...groups.querySelectorAll("[data-tag-period]")];
    let anchor;
    let busy = false;

    function textNode(tag, text, className) {
      const node = document.createElement(tag);
      node.textContent = text;
      if (className) node.className = className;
      return node;
    }
    function isOpen() { return popover.matches(":popover-open"); }
    function close(returnFocus = false) {
      if (isOpen()) popover.hidePopover();
      if (returnFocus) trigger.focus({ preventScroll: true });
    }
    function position() {
      const viewport = window.visualViewport;
      const topEdge = viewport?.offsetTop || 0;
      const leftEdge = viewport?.offsetLeft || 0;
      const heightAvailable = viewport?.height || innerHeight;
      const widthAvailable = viewport?.width || innerWidth;
      const below = topEdge + heightAvailable - anchor.bottom - 18;
      const above = anchor.top - topEdge - 18;
      const width = Math.min(Math.max(anchor.width, 360), widthAvailable - 24);
      // Leave room for actual choices below the search/help and above Done, even
      // when the trigger sits near the middle of a short viewport.
      const height = Math.min(520, Math.max(400, above, below), heightAvailable - 24);
      const openBelow = below >= Math.min(360, height) || below >= above;
      const top = openBelow ? anchor.bottom + 6 : anchor.top - height - 6;
      popover.style.width = `${width}px`;
      popover.style.height = `${height}px`;
      popover.style.left = `${Math.max(leftEdge + 12, Math.min(anchor.left, leftEdge + widthAvailable - width - 12))}px`;
      popover.style.top = `${Math.max(topEdge + 12, Math.min(top, topEdge + heightAvailable - height - 12))}px`;
    }
    function rank() {
      const year = Number(form.elements.namedItem("date").value.slice(0, 4));
      for (const row of options.filter(row => row.dataset.kind === "planting")) {
        const start = Number(row.dataset.start);
        const end = Number(row.dataset.end);
        const inYear = start === year || (row.dataset.persistent === "true" && start <= year && (!end || end >= year));
        const period = periods.find(group => group.dataset.tagPeriod === (inYear ? "current" : "other"));
        const subgroup = [...period.querySelectorAll("[data-tag-subgroup]")]
          .find(group => group.dataset.groupKey === row.dataset.groupKey);
        subgroup.querySelector("[data-tag-bucket]").append(row);
      }
      filter();
    }
    function filter() {
      const terms = search.value.normalize("NFC").toLocaleLowerCase(document.documentElement.lang).trim().split(/\s+/).filter(Boolean);
      let count = 0;
      for (const row of options) {
        const text = row.textContent.normalize("NFC").toLocaleLowerCase(document.documentElement.lang);
        row.hidden = (type.value !== "all" && row.dataset.kind !== type.value) || !terms.every(term => text.includes(term));
        if (!row.hidden) count++;
      }
      for (const detail of groups.querySelectorAll("details")) {
        detail.hidden = ![...detail.querySelectorAll("[data-tag-option]")].some(row => !row.hidden);
        detail.open = terms.length > 0 || (detail.hasAttribute("data-tag-root") && detail.dataset.tagRoot === type.value);
      }
      for (const period of periods) {
        period.hidden = ![...period.querySelectorAll("[data-tag-option]")].some(row => !row.hidden);
      }
      counts();
      picker.querySelector("[data-tag-no-results]").hidden = count > 0;
      picker.querySelector("[data-tag-count]").textContent = `${count} ${picker.dataset.matches}`;
    }
    function counts() {
      for (const detail of groups.querySelectorAll("details")) {
        const rows = [...detail.querySelectorAll("[data-tag-option]")];
        const summary = detail.querySelector(":scope > summary");
        summary.querySelector("[data-tag-group-count]").textContent = `(${rows.filter(row => !row.hidden).length})`;
        const selected = rows.filter(row => row.querySelector("input").checked).length;
        summary.querySelector("[data-tag-selected-count]").textContent = selected ? `· ${picker.dataset.selected} ${selected}` : "";
      }
    }
    function syncSelection() {
      chips.replaceChildren();
      const derived = new Set();
      let hasPlanting = false;
      for (const row of options) {
        const input = row.querySelector("input");
        row.classList.toggle("tag-option-selected", input.checked);
        if (!input.checked) continue;
        const title = row.querySelector(".tag-title").textContent;
        const meta = row.querySelector(".tag-meta")?.textContent;
        const label = [title, meta].filter(Boolean).join(" · ");
        const chip = document.createElement("li");
        chip.append(textNode("span", label));
        const remove = textNode("button", "×");
        remove.type = "button";
        remove.disabled = busy;
        remove.setAttribute("aria-label", `${picker.dataset.remove}: ${label}`);
        remove.addEventListener("click", () => {
          input.checked = false;
          input.dispatchEvent(new Event("change", { bubbles: true }));
          trigger.focus({ preventScroll: true });
        });
        chip.append(remove);
        chips.append(chip);
        if (row.dataset.derived) derived.add(row.dataset.derived);
        if (row.dataset.kind === "planting") hasPlanting = true;
      }
      chips.hidden = !chips.children.length;
      context.hidden = !derived.size;
      context.textContent = derived.size ? `${picker.dataset.found} ${[...derived].join("; ")}` : "";
      picker.querySelector("[data-tag-current]").hidden = !hasPlanting;
      counts();
    }
    function show(keyboard) {
      // Snapshot the anchor once: chip changes must not move an open picker.
      anchor = trigger.getBoundingClientRect();
      position();
      popover.showPopover();
      (keyboard ? search : trigger).focus({ preventScroll: true });
    }
    trigger.addEventListener("click", event => {
      if (busy) return;
      if (isOpen()) close();
      else show(event.detail === 0);
    });
    form.addEventListener("click", event => {
      const link = event.target.closest(".error-summary a");
      if (!link || busy) return;
      const group = groups.querySelector(link.getAttribute("href"));
      if (!group) return;
      event.preventDefault();
      search.value = "";
      type.value = "all";
      filter();
      trigger.scrollIntoView({ block: "nearest" });
      show(true);
      const input = checks.find(input => input.name === group.id.slice(3));
      if (input) {
        for (let parent = input.parentElement; parent && parent !== groups; parent = parent.parentElement) {
          if (parent.tagName === "DETAILS") parent.open = true;
        }
        input.focus();
      }
    });
    popover.addEventListener("beforetoggle", event => {
      trigger.setAttribute("aria-expanded", String(event.newState === "open"));
    });
    popover.addEventListener("mousedown", event => {
      // A result's text otherwise focuses the page's tabindex=-1 ancestor before
      // its label activates the checkbox, closing the choices mid-click.
      if (event.button === 0 && event.target.closest("[data-tag-option]")) event.preventDefault();
    });
    form.addEventListener("keydown", event => {
      if (event.key === "Escape" && isOpen()) { event.preventDefault(); close(true); }
    });
    form.addEventListener("focusout", event => {
      // Label activation briefly clears focus; a null relatedTarget must not close it.
      if (event.relatedTarget && event.relatedTarget !== trigger && !picker.contains(event.relatedTarget)) close();
    });
    options.forEach(row => row.querySelector("input").addEventListener("change", event => {
      syncSelection();
      picker.querySelector("[data-tag-status]").textContent = `${event.target.checked ? picker.dataset.selected : picker.dataset.removed} ${row.querySelector(".tag-title").textContent}`;
    }));
    search.addEventListener("input", filter);
    type.addEventListener("change", filter);
    search.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        // Enter opens the first matching group when compact browsing has no visible choice.
        const rows = [...groups.querySelectorAll("[data-tag-option]")];
        const first = rows.find(row => {
          for (let parent = row; parent && parent !== groups; parent = parent.parentElement) {
            if (parent.hidden || (parent.tagName === "DETAILS" && !parent.open)) return false;
          }
          return true;
        })
          || rows.find(row => !row.hidden && (search.value.trim() || !row.closest('[data-tag-period="other"]')));
        if (first) {
          for (let parent = first.parentElement; parent && parent !== groups; parent = parent.parentElement) {
            if (parent.tagName === "DETAILS") parent.open = true;
          }
          first.querySelector("input").focus({ preventScroll: true });
        }
      }
    });
    picker.querySelector("[data-tag-done]").addEventListener("click", () => close(true));
    form.elements.namedItem("date").addEventListener("change", rank);
    function resized() { if (isOpen()) position(); }
    window.addEventListener("resize", resized);
    window.visualViewport?.addEventListener("resize", resized);
    window.visualViewport?.addEventListener("scroll", resized);
    form.addEventListener("htmx:beforeRequest", event => {
      if (event.detail.elt !== form || event.defaultPrevented) return;
      close();
      busy = true;
      trigger.disabled = true;
      picker.querySelectorAll("button, input[type=checkbox]").forEach(input => { input.disabled = true; });
      search.readOnly = true;
      type.disabled = true;
    });
    form.addEventListener("htmx:afterRequest", event => {
      if (event.detail.elt !== form) return;
      busy = false;
      trigger.disabled = false;
      picker.querySelectorAll("button, input[type=checkbox]").forEach(input => { input.disabled = false; });
      search.readOnly = false;
      type.disabled = false;
    });
    form.addEventListener("reset", () => {
      // A successful quick save starts a fresh draft, including initially checked tags.
      close();
      checks.forEach(input => { input.defaultChecked = false; });
      setTimeout(() => { search.value = ""; type.value = "all"; syncSelection(); rank(); }, 0);
    });
    popover.setAttribute("popover", "auto");
    picker.classList.add("tags-enhanced");
    picker.querySelectorAll("[data-tag-search-panel], [data-tag-footer]").forEach(node => { node.hidden = false; });
    trigger.hidden = false;
    rank();
    syncSelection();
  });
})();
