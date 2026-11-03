/* Native disclosures remain usable without JavaScript. */
(() => {
  "use strict";
  const header = document.getElementById("site-header");
  const actions = document.querySelector(
    ".capture-panel > form > .form-actions, .register-form-panel > form > .form-actions"
  );
  if (window.ResizeObserver) {
    // Large navigation/actions must leave enough viewport space to use the form.
    const updateStickyBars = () => {
      const heightLimit = window.innerHeight / 4;
      if (header) {
        const height = header.getBoundingClientRect().height;
        const sticky = height <= heightLimit;
        header.classList.toggle("site-header-sticky", sticky);
        document.documentElement.style.setProperty(
          "--site-header-height", `${sticky ? height : 0}px`
        );
      }
      if (actions) {
        actions.classList.toggle(
          "form-actions-static", actions.getBoundingClientRect().height > heightLimit
        );
      }
    };
    const observer = new ResizeObserver(updateStickyBars);
    for (const bar of [header, actions]) if (bar) observer.observe(bar);
    window.addEventListener("resize", updateStickyBars);
    updateStickyBars();
  }
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
