/* Calendar panel: a native modal dialog over server-rendered content. */
(() => {
  "use strict";

  function rememberCloseURL(url) {
    // The language form posts its target, so a dismissed panel must not come back.
    const languageNext = document.querySelector("[data-language-form] input[name=next]");
    if (languageNext) languageNext.value = url;
  }

  function returnTarget(dialog) {
    const id = dialog.dataset.returnFocus;
    const target = id ? document.getElementById(id) : null;
    if (target) return target;
    const date = dialog.dataset.date;
    const cell = date ? document.querySelector(`[data-date="${date}"]`) : null;
    return cell?.querySelector("a") ?? document.querySelector(".month-navigation a");
  }

  function initialize(dialog) {
    if (dialog.dataset.calendarReady) return;
    dialog.dataset.calendarReady = "true";
    const url = dialog.dataset.closeUrl;
    let finished = false;
    function finish() {
      if (finished) return;
      finished = true;
      if (url) {
        history.replaceState(null, "", url);
        rememberCloseURL(url);
      }
      returnTarget(dialog)?.focus();
    }
    function close() {
      if (dialog.open) dialog.close();
      else finish();
    }
    dialog.addEventListener("close", finish);
    dialog.querySelectorAll("[data-calendar-close]").forEach(link => {
      link.addEventListener("click", event => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        close();
      });
    });
    // The server renders the panel open for readers without JavaScript.
    dialog.removeAttribute("open");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    dialog.querySelector("[data-calendar-panel-focus]")?.focus();
  }

  document.querySelectorAll("[data-calendar-panel]").forEach(initialize);
  document.addEventListener("growbook:results", event => {
    const root = event.detail?.root || document;
    const query = root.dataset?.calendarNavigationQuery;
    if (query !== undefined) {
      for (const link of document.querySelectorAll(".month-navigation a")) {
        const url = new URL(link.href);
        url.search = query;
        link.href = url.href;
      }
    }
    root.querySelectorAll("[data-calendar-panel]").forEach(initialize);
  });
})();
