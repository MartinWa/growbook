/* Keep lifecycle dates visible only for the selected status; native forms also work. */
(() => {
  "use strict";
  function setup() { document.querySelectorAll("[data-cycle-fields]").forEach(section => {
    if (section.dataset.ready) return;
    section.dataset.ready = "true";
    const status = section.querySelector("[name$='status']");
    const fields = [section.querySelector("[name$='started_on']"), section.querySelector("[name$='completed_on']")];
    function update() {
      fields.forEach((input, index) => {
        const visible = index === 0 ? status.value !== "planned" : status.value === "completed";
        input.closest("[data-field-name]").hidden = !visible;
        input.disabled = !visible || section.closest("[data-capture-action]")?.hidden === true;
      });
      const evaluations = section.parentElement.querySelector("[data-cycle-evaluations]");
      if (evaluations) {
        evaluations.hidden = status.value !== "completed";
        evaluations.querySelectorAll("textarea").forEach(input => {
          input.disabled = evaluations.hidden || section.closest("[data-capture-action]")?.hidden === true;
        });
      }
    }
    status.addEventListener("change", update);
    section.addEventListener("cycle:visibility", update);
    update();
  }); }
  setup();
  document.addEventListener("capture:choices", setup);
  document.addEventListener("capture:action", () => {
    document.querySelectorAll("[data-cycle-fields]").forEach(section => section.dispatchEvent(new Event("cycle:visibility")));
  });
})();
