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
    const date = form.elements.namedItem("purchased_on");
    const minimum = date.min, maximum = date.max;
    const restorePurchase = () => {
      details.open = [...details.querySelectorAll("input, select")].some(input =>
        input.value !== "" && (input.name !== "category" || input.value !== "other"));
      date.min = minimum;
      date.max = maximum;
      if (!form.elements.namedItem("expected_updated_at").value && date.value &&
          (date.value < minimum || date.value > maximum)) {
        // A recovered create can retry a purchase saved before New Year.
        // The server checks the receipt before validating a new purchase's year.
        date.removeAttribute("min");
        date.removeAttribute("max");
      }
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
