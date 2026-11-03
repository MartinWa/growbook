/* Native disclosures remain usable without JavaScript. */
(() => {
  "use strict";
  const menus = document.querySelectorAll(".disclosure-menu");
  document.addEventListener("click", event => {
    for (const menu of menus) {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    for (const menu of menus) {
      if (menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
        event.preventDefault();
      }
    }
  });
  for (const form of document.querySelectorAll("[data-language-form]")) {
    form.querySelector("select").addEventListener("change", () => form.requestSubmit());
    form.querySelector("button").hidden = true;
  }
  for (const form of document.querySelectorAll("form[data-auto-submit]")) {
    form.addEventListener("change", () => form.requestSubmit());
    form.querySelector("button[type=submit]").hidden = true;
  }
})();
