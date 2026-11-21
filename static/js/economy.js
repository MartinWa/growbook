/* Small local form choices; all calculations and save validation remain on the server. */
(() => {
  "use strict";
  const form = document.querySelector("[data-economy-form]");
  const share = document.querySelector(".economy-share");
  if (share) {
    const input = share.querySelector('input[type="range"]');
    const output = share.querySelector("[data-share-output]");
    const quantity = Number(share.dataset.quantity || "");
    const derived = share.querySelector("[data-share-quantity]");
    const format = new Intl.NumberFormat(document.documentElement.lang, {maximumFractionDigits: 3});
    const updateShare = () => {
      const percent = Number(input.value);
      output.textContent = `${format.format(percent)} %`;
      if (derived) derived.textContent = `${format.format(quantity * percent / 100)} ${share.dataset.unit}`;
    };
    input.addEventListener("input", updateShare);
    updateShare();
  }
  if (!form) return;
  const scope = () => {
    const group = form.querySelector('[data-choice-group="cycles"]');
    if (group) group.hidden = form.elements.namedItem("scope").value === "year";
  };
  form.querySelectorAll('[name="scope"]').forEach(input => input.addEventListener("change", scope));
  form.addEventListener("draft:restored", scope);
  form.addEventListener("draft:discarded", scope);
  // The year-filtered cycle picker is swapped in; keep the group's visibility in step.
  form.addEventListener("htmx:afterSwap", scope);
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
