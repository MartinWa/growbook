/* Keep lifecycle dates visible only for the selected status; native forms also work. */
(() => {
  "use strict";
  document.querySelectorAll("[data-cycle-fields]").forEach(section => {
    const status = section.querySelector("#id_status");
    const fields = [section.querySelector("#id_started_on"), section.querySelector("#id_completed_on")];
    function update() {
      fields.forEach((input, index) => {
        const visible = index === 0 ? status.value !== "planned" : status.value === "completed";
        input.closest("[data-field-name]").hidden = !visible;
        input.disabled = !visible;
      });
    }
    status.addEventListener("change", update);
    update();
  });
})();
