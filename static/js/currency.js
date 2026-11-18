/* Currency is a browser-local label; numeric text and stored values stay intact. */
(() => {
  "use strict";
  const labels = ["SEK", "EUR", "USD", "GBP", "NOK", "DKK"];
  const root = document.documentElement;
  const update = () => {
    document.querySelectorAll("span[data-currency-label]").forEach(span => {
      span.textContent = root.dataset.currencyLabel;
    });
  };
  const choose = label => {
    if (!labels.includes(label)) return;
    root.dataset.currencyLabel = label;
    document.cookie = `growbook_currency=${label}; Path=/; Max-Age=31536000; SameSite=Lax` +
      (location.protocol === "https:" ? "; Secure" : "");
    update();
  };
  document.querySelectorAll("[data-currency-form]").forEach(form => {
    const select = form.querySelector("select");
    select.addEventListener("change", () => choose(select.value));
    form.addEventListener("submit", event => {
      event.preventDefault();
      choose(select.value);
    });
    form.querySelector("button").hidden = true;
  });
  document.addEventListener("htmx:afterSwap", update);
})();
