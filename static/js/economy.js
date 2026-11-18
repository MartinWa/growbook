/* Small local form choices; all calculations and save validation remain on the server. */
(() => {
  "use strict";
  const form = document.querySelector("[data-economy-form]");
  if (!form) return;
  const scope = () => {
    const group = form.querySelector('[data-field-name="cycle_ids"]');
    if (group) group.hidden = form.elements.namedItem("scope").value === "year";
  };
  form.querySelectorAll('[name="scope"]').forEach(input => input.addEventListener("change", scope));
  form.addEventListener("draft:restored", scope);
  form.addEventListener("draft:discarded", scope);
  scope();
  const details = form.querySelector("[data-expense-details]");
  if (details) {
    const restorePurchase = () => {
      details.open = [...details.querySelectorAll("input, select")].some(input =>
        input.value !== "" && (input.name !== "category" || input.value !== "other"));
    };
    form.addEventListener("draft:restored", restorePurchase);
    form.addEventListener("draft:discarded", restorePurchase);
  }
  const sourceData = document.getElementById("cycle-sources");
  if (sourceData) {
    const sources = JSON.parse(sourceData.textContent);
    form.elements.namedItem("cycle").addEventListener("change", event => {
      const source = sources[event.target.value];
      if (!source) return;
      const variety = form.elements.namedItem("variety");
      const selected = source.varieties.map(String);
      if (source.default_variety !== null) variety.value = String(source.default_variety);
      else if (!selected.includes(variety.value)) variety.value = "";
      variety.dispatchEvent(new Event("change", {bubbles: true}));
    });
  }
})();
