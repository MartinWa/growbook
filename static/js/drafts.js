/* Tab-session text recovery. Files stay in native inputs; save receipts remain authoritative. */
(() => {
  "use strict";
  const prefix = "growbook:draft:v1:";
  const user = document.body.dataset.draftUser;
  const messages = document.body.dataset;
  const states = new Set();
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const member = /^(?:cycle-)?(?:evaluation|member_version)_[1-9]\d{0,18}$/;
  const planting = "crop start_year variety area lifespan end_year label generated_label quantity notes evaluation status started_on completed_on category_ids".split(" ");
  const event = "kind planned_date actual_date note".split(" ");
  const cycle = "status started_on completed_on category_ids".split(" ");
  const tags = "link_areas link_crops link_varieties link_plantings".split(" ");
  const fields = {
    capture: ["date", "title", "body", "show_in_calendar", "date_overrides", "default_date", "planting_action", "action_planting", ...tags,
      ...planting.map(name => `planting-${name}`), ...event.map(name => `event-${name}`), ...event.map(name => `first_event-${name}`),
      ...cycle.map(name => `cycle-${name}`)],
    entry: ["date", "title", "body", "show_in_calendar", "remove_photos", ...tags],
    crop: ["name", "notes", "category"],
    variety: ["crop", "name", "description", "source", "experiences"],
    area: ["name", "parent", "area_type", "length_cm", "width_cm", "description", "notes"],
    planting: [...planting, "batch_planting"],
    event,
    cycle,
    "cycle-transition": ["date"],
    task: ["planned_date", "title", "note", "link"],
    expense: ["description", "purchased_on", "total_price", "category", "purchased_quantity", "unit", "crop", "variety", "useful_life_years"],
    allocation: ["growing_year", "amount", "share", "quantity_used", "scope", "cycle_ids", "method"],
    sale: ["description", "sold_on", "quantity", "unit_price", "total_price", "cycle", "variety"],
  };
  const files = {capture: ["camera", "library", "planting-photos"], entry: ["photos"], variety: ["photos"], area: ["photos"], planting: ["photos"]};
  const limit = 1024 * 1024; // Reject oversized snapshots whole; never shorten writing or evict other drafts.
  let warningAttached = false;
  let loggedOut = false;

  function beforeLeave(event) {
    event.preventDefault();
    event.returnValue = "";
  }
  function updateWarning() {
    const dirty = !loggedOut && [...states].some(state => state.dirty);
    if (dirty === warningAttached) return;
    warningAttached = dirty;
    window[dirty ? "addEventListener" : "removeEventListener"]("beforeunload", beforeLeave);
  }
  function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function same(left, right) { return JSON.stringify(left) === JSON.stringify(right); }
  function controls(form, name) { return [...form.elements].filter(input => input.name === name); }
  function values(form, allowed) {
    const result = {};
    for (const name of [...new Set([...form.elements].map(input => input.name))].filter(allowed).sort()) {
      const inputs = controls(form, name);
      result[name] = inputs.flatMap(input => {
        if (["checkbox", "radio"].includes(input.type)) return input.checked ? [input.value] : [];
        if (input.tagName === "SELECT") return [...input.selectedOptions].map(option => option.value);
        return [input.value];
      }).sort();
    }
    return result;
  }
  function uploadCounts(form, kind) {
    return Object.fromEntries((files[kind] || []).map(name =>
      [name, controls(form, name).reduce((count, input) => count + (input.files?.length || 0), 0)]));
  }
  function versionName(name) { return name === "expected_updated_at" || name === "expected_expense_updated_at" || name === "cycle-expected_updated_at" || /^(?:cycle-)?member_version_/.test(name); }

  function setup(form) {
    const kind = form.dataset.draftKind;
    if (!Object.hasOwn(fields, kind)) return;
    const batch = kind === "planting" ? form.elements.namedItem("batch_planting")?.value || "" : "";
    const identity = `${kind}:${new URL(form.action).pathname}${batch ? `:batch:${batch}` : ""}`;
    const key = `${prefix}${user}:${identity}`;
    const allowField = name => fields[kind].includes(name) || (["capture", "cycle", "cycle-transition"].includes(kind) && member.test(name) && name.includes("evaluation_"));
    const allowToken = name => name === "submission_key" || name === "expected_updated_at" || kind === "allocation" && name === "expected_expense_updated_at" || kind === "capture" && name === "cycle-expected_updated_at" ||
      ["capture", "cycle", "cycle-transition"].includes(kind) && member.test(name) && name.includes("member_version_");
    const notice = form.querySelector("[data-draft-notice]");
    const status = notice.querySelector("[data-draft-status]");
    const storageMessage = notice.querySelector("[data-draft-storage]");
    const photoMessage = notice.querySelector("[data-draft-photos]");
    const copy = notice.querySelector("[data-draft-copy]");
    const omit = notice.querySelector("[data-draft-omit]");
    const discard = notice.querySelector("[data-draft-discard]");
    const state = {dirty: false};
    states.add(state);
    let baseline = values(form, allowField);
    let initialTokens = values(form, allowToken);
    let retained = form.dataset.draftBound === "true";
    let attempted = retained;
    let restoring = false;
    let confirmed = false;
    let archived = [];
    const editedDuringRestore = new Set();
    let expectedUploads = uploadCounts(form, kind);
    let recoveringUploads = false;
    let requestKey;
    let storageFailed = false;

    // A failed POST's rendered defaultValue is the submitted text, not the saved text.
    if (retained) {
      const defaults = JSON.parse(form.querySelector("#draft-defaults").textContent);
      for (const name of Object.keys(baseline)) {
        if (!Object.hasOwn(defaults, name)) continue;
        baseline[name] = defaults[name];
        const input = controls(form, name)[0];
        if (input?.tagName === "SELECT" && !input.multiple && baseline[name][0] === "" &&
            ![...input.options].some(option => option.value === "")) baseline[name] = [input.options[0]?.value || ""];
      }
      for (const name of Object.keys(initialTokens)) if (Object.hasOwn(defaults, name)) initialTokens[name] = defaults[name];
      status.textContent = messages.draftRetained;
    }
    function storage(action) {
      try {
        const result = action(window.sessionStorage);
        storageFailed = false;
        storageMessage.hidden = true;
        return result;
      } catch (_) {
        storageFailed = true;
        storageMessage.hidden = false;
        notice.hidden = false;
        return undefined;
      }
    }
    function read() {
      const raw = storage(store => store.getItem(key));
      if (!raw) return;
      try {
        if (raw.length > limit) throw new Error("size");
        const draft = JSON.parse(raw);
        if (!object(draft) || draft.previous !== undefined && (!Array.isArray(draft.previous) || draft.previous.length > 2)) throw new Error("history");
        for (const snapshot of [draft, ...(draft.previous || [])]) validate(snapshot);
        return draft;
      } catch (_) {
        storage(store => store.removeItem(key));
        status.textContent = messages.draftInvalid;
        notice.hidden = false;
      }
    }
    function validate(draft) {
        if (!object(draft) || draft.version !== 1 || draft.identity !== identity || draft.user !== user ||
            !object(draft.fields) || !object(draft.tokens) || !object(draft.uploads) || typeof draft.attempted !== "boolean" || typeof draft.unsaved !== "boolean") throw new Error("format");
        if (kind === "planting" && !same(draft.fields.batch_planting, [batch])) throw new Error("batch");
        for (const [map, allow] of [[draft.fields, allowField], [draft.tokens, allowToken]]) {
          if (Object.keys(map).length > 256) throw new Error("fields");
          for (const [name, entries] of Object.entries(map)) {
            if (!allow(name) || !Array.isArray(entries) || entries.length > 512 || entries.some(value => typeof value !== "string")) throw new Error("field");
          }
        }
        if (draft.tokens.submission_key?.length !== 1 || !uuid.test(draft.tokens.submission_key[0])) throw new Error("key");
        for (const [name, count] of Object.entries(draft.uploads)) {
          if (!(files[kind] || []).includes(name) || !Number.isSafeInteger(count) || count < 0 || count > 1000) throw new Error("uploads");
        }
        // Scalar controls must never silently drop extra values from an untrusted snapshot.
        for (const [name, entries] of Object.entries({...draft.fields, ...draft.tokens})) {
          const inputs = controls(form, name);
          const multi = tags.includes(name) || name.endsWith("category_ids") || name === "remove_photos" || name === "cycle_ids";
          // One checkbox or radio stores no value when cleared, so 0 and 1 are both valid.
          const choice = inputs.some(input => ["checkbox", "radio"].includes(input.type));
          if (!multi && (choice ? entries.length > 1 : entries.length !== 1)) throw new Error("scalar");
          if (allowToken(name) && (entries.length !== 1 || entries[0].length > 100)) throw new Error("token");
          if (inputs.length === 1 && ["date", "number"].includes(inputs[0].type)) {
            const probe = inputs[0].cloneNode();
            probe.value = entries[0] || "";
            if (probe.value !== (entries[0] || "")) throw new Error("value");
          }
        }
    }
    function missingPhotos() {
      const counts = uploadCounts(form, kind);
      if (kind === "capture") {
        return (expectedUploads.camera || 0) + (expectedUploads.library || 0) > counts.camera + counts.library ||
          (expectedUploads["planting-photos"] || 0) > counts["planting-photos"];
      }
      return Object.entries(expectedUploads).some(([name, count]) => count > (counts[name] || 0));
    }
    function update() {
      if (restoring || loggedOut) return;
      state.dirty = !confirmed && (retained || archived.length > 0 || missingPhotos() || !same(values(form, allowField), baseline) ||
        attempted || Object.values(uploadCounts(form, kind)).some(Boolean));
      photoMessage.hidden = !missingPhotos();
      omit.hidden = photoMessage.hidden;
      // A silently stored draft needs no notice; it appears with a restored draft,
      // an uncertain submission or a storage problem, and the discard button with it.
      const announced = Boolean(storageFailed || status.textContent || !photoMessage.hidden);
      notice.hidden = !announced;
      discard.hidden = !announced;
      updateWarning();
    }
    function persist(force = false) {
      if (restoring || loggedOut || confirmed) return;
      update();
      if (!state.dirty && !force && !storage(store => store.getItem(key))) return;
      const counts = uploadCounts(form, kind);
      const uploads = Object.fromEntries(Object.keys(counts).map(name => [name, Math.max(counts[name], expectedUploads[name] || 0)]));
      if (kind === "capture") {
        // Camera and library are interchangeable sources for the same diary uploads.
        uploads.camera = counts.camera;
        uploads.library = Math.max(counts.camera + counts.library, (expectedUploads.camera || 0) + (expectedUploads.library || 0)) - counts.camera;
      }
      const draft = {version: 1, identity, user, fields: values(form, allowField), tokens: values(form, allowToken),
        uploads, attempted, unsaved: state.dirty};
      // Preserve old writing while also recovering new work on the current server values.
      if (archived.length) draft.previous = archived;
      const serialized = JSON.stringify(draft);
      storage(store => {
        if (serialized.length > limit || archived.length > 2) throw new Error("size");
        validate(draft);
        if (!store.getItem(key) && Object.keys(store).filter(name => name.startsWith(prefix)).length >= 30) throw new Error("count");
        store.setItem(key, serialized);
      });
    }
    function setField(name, entries) {
      if (restoring && editedDuringRestore.has(name)) return;
      const inputs = controls(form, name);
      if (!inputs.length && attempted && member.test(name)) {
        // Deleted members still belong to an uncertain submission's receipt payload.
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = entries[0] || "";
        input.setAttribute("data-draft-retry-member", "");
        form.append(input);
        return;
      }
      if (!inputs.length && !(tags.includes(name) || name.endsWith("category_ids"))) return;
      if (!inputs.length && !form.querySelector(`#id_${name}`)) return;
      const input = inputs[0];
      if (input?.tagName === "SELECT") {
        for (const value of entries) {
          if (![...input.options].some(option => option.value === value)) {
            const option = new Option(`${messages.draftUnavailable} (${value})`, value);
            option.dataset.draftUnavailable = "true";
            input.add(option);
          }
        }
        for (const option of input.options) option.selected = entries.includes(option.value);
      } else if (inputs.some(item => ["checkbox", "radio"].includes(item.type)) || tags.includes(name) || name.endsWith("category_ids") || name === "remove_photos") {
        for (const value of entries) {
          if (inputs.some(item => item.value === value)) continue;
          const row = document.createElement("label");
          row.dataset.draftUnavailable = "true";
          const check = document.createElement("input");
          check.type = "checkbox";
          check.name = name;
          check.value = value;
          check.checked = true;
          const title = document.createElement("span");
          title.className = "tag-title";
          title.textContent = `${messages.draftUnavailable} (${value})`;
          row.append(check, title);
          if (tags.includes(name)) {
            row.className = "tag-option";
            row.setAttribute("data-tag-option", "");
            row.dataset.kind = {link_areas: "area", link_crops: "crop", link_varieties: "variety", link_plantings: "planting"}[name];
          }
          (form.querySelector(`#id_${name}`) || input.parentElement).append(row);
          inputs.push(check);
        }
        inputs.forEach(item => { if (item.type !== "hidden") item.checked = entries.includes(item.value); });
      } else input.value = entries[0] || "";
    }
    function showCopy(draft) {
      const {previous, ...snapshot} = draft;
      if (!archived.some(item => same(item, snapshot))) archived.push(snapshot);
      status.textContent = messages.draftChanged;
      copy.hidden = false;
      copy.replaceChildren();
      archived.forEach((item, index) => { for (const [name, entries] of Object.entries(item.fields)) {
        if (!entries.length || entries.every(value => !value)) continue;
        const input = controls(form, name)[0];
        if (input && input.type === "hidden") continue;
        const field = document.createElement("div");
        field.className = "field";
        const label = document.createElement("label");
        const text = document.createElement("textarea");
        text.id = `draft-copy-${index ? `${index}-` : ""}${name}`;
        text.readOnly = true;
        text.rows = 3;
        text.value = entries.join("\n");
        label.htmlFor = text.id;
        label.textContent = input?.labels?.[0]?.textContent.trim()
          ? `${messages.draftCopyLabel}: ${input.labels[0].textContent.trim()}` : messages.draftCopyLabel;
        field.append(label, text);
        copy.append(field);
      } });
    }
    function compatible(draft) {
      const current = values(form, allowToken);
      const oldVersions = Object.fromEntries(Object.entries(draft.tokens).filter(([name]) => versionName(name)));
      const newVersions = Object.fromEntries(Object.entries(current).filter(([name]) => versionName(name)));
      return same(oldVersions, newVersions);
    }
    function restoreMembers(draft) {
      const original = {...draft.fields, ...draft.tokens};
      for (const input of [...form.elements].filter(input => member.test(input.name))) {
        if (Object.hasOwn(original, input.name)) continue;
        // A member added later must not change an identical retry's fingerprint.
        input.dataset.draftMemberName = input.name;
        input.name = "";
        const field = input.closest("[data-field-name]");
        if (field) {
          field.dataset.draftMemberHidden = String(field.hidden);
          field.hidden = true;
        }
      }
    }
    function resetMembers() {
      form.querySelectorAll("[data-draft-retry-member]").forEach(input => input.remove());
      for (const input of form.querySelectorAll("[data-draft-member-name]")) {
        input.name = input.dataset.draftMemberName;
        delete input.dataset.draftMemberName;
        const field = input.closest("[data-field-name]");
        if (field) {
          field.hidden = field.dataset.draftMemberHidden === "true";
          delete field.dataset.draftMemberHidden;
        }
      }
    }
    async function prepare(snapshot) {
      const detail = {fields: snapshot, restoreField: name => {
        if (Object.hasOwn(snapshot, name)) setField(name, snapshot[name]);
      }, ready: Promise.resolve(true)};
      form.dispatchEvent(new CustomEvent("draft:prepare", {detail}));
      return await detail.ready;
    }
    async function restore(draft) {
      restoring = true;
      state.dirty = true;
      updateWarning();
      try {
        (draft.previous || []).forEach(showCopy);
        if (!retained && ["capture", "planting", "allocation"].includes(kind)) {
          // Choice failures cannot replace an uncertain submission's original save key.
          if (!await prepare(draft.fields) && !draft.attempted) { showCopy(draft); return; }
        }
        if (retained) {
          // A bound response already contains the authoritative submitted values and tokens.
          if (same(draft.tokens.submission_key, values(form, allowToken).submission_key)) {
            if (draft.attempted) restoreMembers(draft);
            expectedUploads = draft.uploads;
            recoveringUploads = missingPhotos();
          }
          else showCopy(draft);
          status.textContent = messages.draftRetained;
        } else if (!compatible(draft) && !draft.attempted) {
          showCopy(draft);
        } else {
          attempted = draft.attempted;
          if (draft.attempted) {
            if (!compatible(draft)) showCopy(draft);
            restoreMembers(draft);
          }
          for (const [name, entries] of Object.entries(draft.fields)) setField(name, entries);
          for (const [name, entries] of Object.entries(draft.tokens)) setField(name, entries);
          expectedUploads = draft.uploads;
          recoveringUploads = missingPhotos();
          retained = draft.unsaved;
          status.textContent = draft.attempted ? messages.draftRetained : archived.length ? messages.draftChanged : messages.draftRestored;
          form.dispatchEvent(new CustomEvent("draft:restored", {detail: {retry: draft.attempted}}));
        }
      } catch (_) {
        showCopy(draft);
      } finally {
        restoring = false;
        editedDuringRestore.clear();
        update();
      }
    }
    function clear() {
      confirmed = true;
      retained = false;
      attempted = false;
      archived = [];
      expectedUploads = {};
      recoveringUploads = false;
      resetMembers();
      copy.hidden = true;
      copy.replaceChildren();
      storage(store => store.removeItem(key));
      state.dirty = false;
      notice.hidden = !storageFailed;
      updateWarning();
    }
    discard.addEventListener("click", async () => {
      if (restoring || form.hasAttribute("aria-busy")) return;
      clear();
      restoring = true;
      editedDuringRestore.clear();
      form.reset();
      // Restore saved/default state, including initially linked tags and failed POSTs.
      form.querySelectorAll("[data-draft-unavailable]").forEach(node => node.remove());
      for (const [name, entries] of Object.entries(baseline)) setField(name, entries);
      for (const [name, entries] of Object.entries(initialTokens)) setField(name, entries);
      form.dispatchEvent(new CustomEvent("draft:discarded"));
      try {
        // Reset helpers finish before dependent choices and their saved selections return.
        await new Promise(resolve => setTimeout(resolve, 0));
        await prepare(baseline);
        for (const [name, entries] of Object.entries(baseline)) setField(name, entries);
        form.dispatchEvent(new CustomEvent("draft:restored"));
      } finally {
        status.textContent = "";
        attempted = false;
        confirmed = false;
        restoring = false;
        const defaults = values(form, allowField);
        for (const name of editedDuringRestore) {
          if (Object.hasOwn(baseline, name)) defaults[name] = baseline[name];
          else delete defaults[name];
        }
        baseline = defaults;
        editedDuringRestore.clear();
        update();
        persist();
        const focus = [...form.elements].find(input => allowField(input.name) && input.type !== "hidden" && !input.disabled && input.getClientRects().length);
        focus?.focus();
      }
    });
    omit.addEventListener("click", () => {
      expectedUploads = uploadCounts(form, kind);
      recoveringUploads = false;
      persist();
    });
    function changed(event) {
      if (restoring && allowField(event.target.name)) editedDuringRestore.add(event.target.name);
      if (confirmed || restoring) return;
      // Selections removed deliberately on the current page are no longer missing.
      if (recoveringUploads && !missingPhotos()) recoveringUploads = false;
      if (!recoveringUploads) expectedUploads = uploadCounts(form, kind);
      persist();
    }
    form.addEventListener("input", changed);
    form.addEventListener("change", changed);
    form.addEventListener("draft:changed", changed);
    form.addEventListener("reset", () => {
      setTimeout(() => {
        if (confirmed || restoring) return;
        expectedUploads = uploadCounts(form, kind);
        persist();
      }, 0);
    });
    function beforeSubmit(event) {
      if (restoring || missingPhotos()) {
        event.preventDefault();
        event.stopImmediatePropagation();
        update();
        if (missingPhotos()) { notice.hidden = false; omit.focus(); }
        return;
      }
      attempted = true;
      requestKey = form.elements.namedItem("submission_key")?.value;
      persist(true);
    }
    form.addEventListener("submit", beforeSubmit, true);
    form.addEventListener("htmx:configRequest", event => {
      if (event.detail.elt === form) beforeSubmit(event);
    }, true);
    // HX-Redirect is processed before beforeSwap; only an explicit matching save receipt clears drafts.
    form.addEventListener("draft:saved", clear);
    if (kind === "capture") document.addEventListener("capture:choices", resetMembers);
    form.addEventListener("htmx:beforeOnLoad", event => {
      if (event.detail.elt !== form) return;
      const xhr = event.detail.xhr;
      if (xhr.status === 204 && xhr.getResponseHeader("HX-Redirect") &&
          xhr.getResponseHeader("X-Growbook-Saved-Key") === requestKey) clear();
    });
    // A rejected or lost submission keeps its writing; make it visible and discardable.
    form.addEventListener("htmx:afterRequest", event => {
      if (event.detail.elt !== form || (event.detail.xhr?.status || 0) < 400) return;
      status.textContent = messages.draftRetained;
      update();
    });
    form.addEventListener("htmx:sendError", event => {
      if (event.detail.elt !== form) return;
      status.textContent = messages.draftRetained;
      update();
    });
    document.addEventListener("diary:saved", () => {
      if (kind !== "capture") return;
      setTimeout(() => {
        // Quick-save helpers finish resetting dependent fields before the next baseline.
        baseline = values(form, allowField);
        initialTokens = values(form, allowToken);
        confirmed = false;
        attempted = false;
        status.textContent = "";
        update();
      }, 0);
    });
    window.addEventListener("pagehide", () => persist());
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") persist(); });
    const draft = read();
    if (draft?.unsaved) restore(draft);
    else { update(); if (retained) persist(); }
    form.dataset.draftReady = "true";
  }

  function initialize() {
    if (user) document.querySelectorAll("form[data-draft-kind]").forEach(setup);
  }
  // Deferred helpers run before DOMContentLoaded; HTMX attaches choice listeners on that event.
  if (document.readyState !== "complete") document.addEventListener("DOMContentLoaded", initialize, {once: true});
  else initialize();
  let channel;
  try { channel = new BroadcastChannel("growbook-draft-logout"); } catch (_) { /* This tab still clears on logout. */ }
  function logout() {
    loggedOut = true;
    try {
      const store = window.sessionStorage;
      Object.keys(store).filter(key => key.startsWith(prefix)).forEach(key => store.removeItem(key));
    } catch (_) { /* Storage may be disabled; normal logout must still work. */ }
    updateWarning();
  }
  if (channel) channel.onmessage = event => { if (event.data === "logout") logout(); };
  document.querySelector("[data-draft-logout]")?.addEventListener("submit", () => {
    logout();
    channel?.postMessage("logout");
  });
})();
