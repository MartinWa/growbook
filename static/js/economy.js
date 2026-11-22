/* Small local form choices; all calculations and save validation remain on the server. */
(() => {
  "use strict";
  const form = document.querySelector("[data-economy-form]");
  const share = document.querySelector(".economy-share");
  if (share) {
    const input = share.querySelector('input[type="range"]');
    const output = share.querySelector("[data-share-output]");
    const valueOut = share.querySelector("[data-share-value]");
    const percentOut = share.querySelector("[data-share-percent]");
    // The server sends unlocalized numbers so every locale parses the same way.
    const purchased = Number(share.dataset.quantity ?? "");
    const measured = Number.isFinite(purchased) && purchased > 0;
    const format = new Intl.NumberFormat(document.documentElement.lang, {maximumFractionDigits: 3});
    const updateShare = () => {
      const value = Number(input.value);
      if (valueOut) valueOut.textContent = format.format(value);
      else if (output) output.textContent = `${format.format(value)} %`;
      if (percentOut && measured) {
        const percent = Math.round((value / purchased) * 1000) / 10;
        percentOut.textContent = `${format.format(percent)} %`;
      }
    };
    input.addEventListener("input", updateShare);
    // A restored draft or a rejected submission can change the value without an input event.
    share.closest("form")?.addEventListener("draft:restored", updateShare);
    share.closest("form")?.addEventListener("draft:discarded", updateShare);
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
  // A restored draft can name another growing year than the rendered picker, which would
  // drop its selected cycles. Load that year's choices before the draft applies them.
  form.addEventListener("draft:prepare", event => {
    const yearInput = form.elements.namedItem("growing_year");
    const year = event.detail.fields.growing_year?.[0];
    const url = yearInput?.getAttribute("hx-get");
    if (!year || !url || year === yearInput.value) return;
    const separator = url.includes("?") ? "&" : "?";
    event.detail.ready = htmx
      .ajax("GET", `${url}${separator}growing_year=${encodeURIComponent(year)}`, {
        target: "#id_cycle_ids_group",
        swap: "outerHTML",
      })
      .then(() => true, () => true);
  });
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
