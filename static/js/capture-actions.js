/* Keep local drafts while loading only a selected cycle's recorded details. */
(() => {
  "use strict";
  const form = document.getElementById("capture-form");
  if (!form) return;
  const disclosure = form.querySelector("[data-planting-action]");
  const action = form.elements.namedItem("planting_action");
  const planting = form.elements.namedItem("action_planting");
  const date = form.elements.namedItem("date");
  const status = form.querySelector("[data-action-status]");
  const load = form.querySelector("[data-action-load]");
  const save = document.getElementById("save-entry");
  let controller;
  let loaded = form.elements.namedItem("cycle-expected_updated_at")?.value ? planting.value : "";
  let loading = false;
  const defaults = new Map();
  const overrides = form.elements.namedItem("date_overrides");
  const overridden = new Set(overrides.value.split(",").filter(Boolean));
  function follow(input, year = false) {
    if (!input || defaults.has(input)) return;
    defaults.set(input, {year, automatic: !overridden.has(input.name) && (!input.value || input.value === (year ? date.value.slice(0, 4) : date.value))});
    input.addEventListener("input", () => {
      defaults.get(input).automatic = false;
      overridden.add(input.name);
      overrides.value = [...overridden].join(",");
    });
  }
  function newDefaults() {
    follow(form.elements.namedItem("planting-start_year"), true);
    follow(form.elements.namedItem("planting-started_on"));
    follow(form.elements.namedItem("planting-completed_on"));
    follow(form.elements.namedItem("event-actual_date"));
  }
  function cycleDefaults() {
    for (const name of ["cycle-started_on", "cycle-completed_on"]) {
      const input = form.elements.namedItem(name);
      if (input) {
        defaults.set(input, {year: false, automatic: false});
        input.addEventListener("input", () => { defaults.get(input).automatic = false; });
      }
    }
  }
  newDefaults();
  cycleDefaults();
  function updateDefaults() {
    defaults.forEach((state, input) => {
      if (state.automatic) input.value = state.year ? date.value.slice(0, 4) : date.value;
    });
  }
  date.addEventListener("change", updateDefaults);
  function update() {
    form.querySelectorAll("[data-cycle-presets]").forEach(group => { group.hidden = false; });
    form.querySelectorAll("[data-capture-action]").forEach(section => {
      section.hidden = section.dataset.captureAction !== action.value;
      section.querySelectorAll("input, select, textarea, button").forEach(input => { input.disabled = section.hidden; });
    });
    form.querySelector("[data-existing-planting]").hidden = !["event", "cycle"].includes(action.value);
    planting.disabled = !["event", "cycle"].includes(action.value);
    load.hidden = action.value !== "cycle";
    save.disabled = loading || (action.value === "cycle" && loaded !== planting.value);
    document.dispatchEvent(new CustomEvent("capture:action"));
  }
  async function loadCycle() {
    if (action.value !== "cycle" || !planting.value) { update(); return; }
    controller?.abort();
    controller = new AbortController();
    const current = controller;
    const selected = planting.value;
    loading = true;
    status.textContent = "";
    status.setAttribute("aria-busy", "true");
    update();
    const data = new FormData(form);
    const cookie = document.cookie.split("; ").find(value => value.startsWith("csrftoken="));
    if (cookie) data.set("csrfmiddlewaretoken", decodeURIComponent(cookie.split("=")[1]));
    for (const [name, value] of [...data]) if (value instanceof File) data.delete(name);
    data.set("choose_action", "1");
    try {
      const response = await fetch(form.action, {method: "POST", body: data,
        headers: {"HX-Request": "true"}, signal: current.signal});
      if (!response.ok) throw new Error("choices");
      const html = await response.text();
      if (current !== controller || selected !== planting.value) return;
      const template = document.createElement("template");
      template.innerHTML = html;
      const section = form.querySelector('[data-capture-action="cycle"]');
      if (!template.content.querySelector("[data-cycle-action-fields]")) throw new Error("choices");
      section.replaceChildren(template.content);
      loaded = selected;
      cycleDefaults();
      document.dispatchEvent(new CustomEvent("capture:choices"));
    } catch (error) {
      if (error.name !== "AbortError") status.textContent = status.dataset.error;
    } finally {
      if (current === controller) {
        loading = false;
        status.removeAttribute("aria-busy");
        update();
      }
    }
  }
  load.addEventListener("click", event => { event.preventDefault(); loadCycle(); });
  planting.addEventListener("change", () => { loaded = ""; loadCycle(); });
  action.addEventListener("change", () => {
    update();
    if (action.value === "cycle" && loaded !== planting.value) loadCycle();
  });
  function supplyCycleDate(state) {
    const name = state === "completed" ? "cycle-completed_on" : "cycle-started_on";
    const input = form.elements.namedItem(name);
    if (state !== "planned" && !input.value) {
      input.value = date.value;
      defaults.get(input).automatic = true;
    }
  }
  form.addEventListener("click", event => {
    const preset = event.target.closest("[data-cycle-preset]")?.dataset.cyclePreset;
    if (!preset) return;
    const state = form.elements.namedItem("cycle-status");
    state.value = preset === "complete" ? "completed" : preset === "planned" ? "planned" : "ongoing";
    supplyCycleDate(state.value);
    state.dispatchEvent(new Event("change"));
  });
  form.addEventListener("change", event => {
    if (event.target.name === "cycle-status") supplyCycleDate(event.target.value);
  });
  const crop = form.elements.namedItem("planting-crop");
  const variety = form.elements.namedItem("planting-variety");
  const label = form.elements.namedItem("planting-label");
  const generated = form.elements.namedItem("planting-generated_label");
  let customLabel = Boolean(label.value && label.value !== generated.value);
  label.addEventListener("input", () => { customLabel = true; });
  function updateVarieties() {
    for (const option of variety.options) {
      option.hidden = Boolean(crop.value && option.value && option.dataset.crop && option.dataset.crop !== crop.value);
      option.disabled = option.hidden;
    }
    if (!customLabel) {
      const cropName = crop.selectedOptions[0]?.dataset.recordName || "";
      const varietyName = variety.selectedOptions[0]?.dataset.recordName || "";
      if (crop.value && !cropName || variety.value && !varietyName) return;
      label.value = generated.value = cropName + (varietyName ? `-${varietyName}` : "");
    }
  }
  crop.addEventListener("change", () => {
    if (variety.selectedOptions[0]?.dataset.crop !== crop.value) variety.value = "";
    updateVarieties();
  });
  variety.addEventListener("change", () => {
    if (!crop.value && variety.selectedOptions[0]?.dataset.crop) crop.value = variety.selectedOptions[0].dataset.crop;
    updateVarieties();
  });
  document.addEventListener("diary:saved", () => {
    action.value = "";
    disclosure.open = false;
    loaded = "";
    customLabel = false;
    defaults.clear();
    overridden.clear();
    overrides.value = "";
    newDefaults();
    updateDefaults();
    updateVarieties();
    update();
  });
  updateVarieties();
  updateDefaults();
  update();
  document.addEventListener("capture:idle", update);
})();
