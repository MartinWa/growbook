/* Apply a browser-local preference without replacing drafts or selected photos. */
(() => {
  "use strict";
  const root = document.documentElement;
  const forms = document.querySelectorAll("[data-season-form]");
  const modes = ["auto", "spring", "summer", "autumn", "winter"];
  const seasons = ["winter", "spring", "summer", "autumn"];
  const calendar = new Intl.DateTimeFormat("en", {
    timeZone: root.dataset.gardenTimezone, month: "numeric"
  });

  const update = () => {
    const mode = root.dataset.seasonMode;
    const month = Number(calendar.format(new Date()));
    root.dataset.season = mode === "auto" ? seasons[Math.floor(month % 12 / 3)] : mode;
  };
  const choose = mode => {
    if (!modes.includes(mode)) return;
    root.dataset.seasonMode = mode;
    document.cookie = `growbook_season=${mode}; Path=/; Max-Age=31536000; SameSite=Lax` +
      (location.protocol === "https:" ? "; Secure" : "");
    update();
  };
  for (const form of forms) {
    const select = form.querySelector("select");
    select.addEventListener("change", () => choose(select.value));
    form.addEventListener("submit", event => {
      event.preventDefault();
      choose(select.value);
    });
    form.querySelector("button").hidden = true;
  }
  // Recalculate after midnight or a sleeping tab, using the garden's timezone.
  window.addEventListener("focus", update);
  document.addEventListener("visibilitychange", update);
  window.setInterval(update, 60000);
  update();
})();
