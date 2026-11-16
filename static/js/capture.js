/* Page-local upload state; drafts.js recovers text and save tokens, never photo bytes. */
(() => {
  "use strict";
  const form = document.getElementById("capture-form");
  if (!form || !window.growbookFormFeedback) return;

  window.growbookFormFeedback(form, {
    errors: document.getElementById("capture-errors"),
    status: document.getElementById("capture-status"),
    progress: document.getElementById("capture-progress"),
    successTarget: document.getElementById("quick-receipt"),
    onSuccess(event, submittedKey) {
      const saved = event.detail.target.querySelector("[data-capture-saved]");
      if (!saved || event.detail.xhr.status !== 200 ||
          event.detail.xhr.getResponseHeader("X-Growbook-Saved-Key") !== submittedKey ||
          !/^[1-9]\d*$/.test(saved.dataset.captureSaved) ||
          !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(saved.dataset.nextKey) ||
          saved.querySelector("a")?.getAttribute("href") !== "/entries/" + saved.dataset.captureSaved + "/") return false;
      form.dispatchEvent(new CustomEvent("draft:saved"));
      form.reset();
      const key = form.elements.namedItem("submission_key");
      key.value = key.defaultValue = saved.dataset.nextKey;
      saved.querySelector("a").focus();
      document.dispatchEvent(new CustomEvent("diary:saved", {
        detail: { entryId: saved.dataset.captureSaved }
      }));
      return true;
    },
    onIdle() {
      document.dispatchEvent(new CustomEvent("capture:idle"));
    }
  });
})();
