/* Native FileLists stay in the form; previews and accumulated choices are page-local. */
(() => {
  "use strict";
  for (const picker of document.querySelectorAll("[data-photo-picker]")) {
    const form = picker.closest("form");
    const inputs = [...picker.querySelectorAll("input[type=file]")];
    const nativePicker = inputs.find(input => !input.hasAttribute("capture"));
    const chooser = picker.dataset.photoChooserId
      ? document.getElementById(picker.dataset.photoChooserId)
      : picker.querySelector("[data-photo-chooser]");
    const previews = picker.querySelector("[data-photo-previews]");
    const count = picker.querySelector("[data-photo-count]");
    const warning = picker.querySelector("[data-photo-limit-warning]");
    const selections = new Map(inputs.map(input => [input, []]));
    let urls = [];
    let busy = false;
    let canAccumulate = false;
    try {
      const probe = document.createElement("input");
      probe.type = "file";
      probe.files = new DataTransfer().files;
      canAccumulate = true;
    } catch (_) { /* Keep native replacement selection when FileList assignment is unavailable. */ }

    function updateFiles(input) {
      const transfer = new DataTransfer();
      selections.get(input).forEach(file => transfer.items.add(file));
      input.files = transfer.files;
    }
    function updateCount() {
      const files = [...selections.values()].flat();
      const selected = files.length;
      const removed = form.querySelectorAll('input[name="remove_photos"]:checked').length;
      const remaining = Math.max(0, Number(picker.dataset.existingPhotos) - removed);
      count.textContent = selected ? picker.dataset.photosSelected
        .replaceAll("{selected}", String(selected)) : "";
      warning.hidden = selected + remaining <= Number(picker.dataset.photoLimit) &&
        files.every(file => file.size <= Number(picker.dataset.photoFileBytes)) &&
        files.reduce((total, file) => total + file.size, 0) <= Number(picker.dataset.photoTotalBytes);
    }
    function renderPreviews() {
      urls.forEach(url => URL.revokeObjectURL(url));
      urls = [];
      previews.replaceChildren();
      for (const input of inputs) {
        const files = selections.get(input);
        files.forEach((file, index) => {
          const figure = document.createElement("figure");
          const image = document.createElement("img");
          const url = URL.createObjectURL(file);
          image.alt = file.name;
          const caption = document.createElement("figcaption");
          caption.textContent = file.name;
          function showUnavailable() {
            image.remove();
            const hint = document.createElement("p");
            hint.textContent = picker.dataset.previewUnavailable;
            figure.prepend(hint);
          }
          image.addEventListener("error", showUnavailable, { once: true });
          figure.append(image, caption);
          // Allow only blob URLs without HTML metacharacters at the image source.
          if (url.startsWith("blob:") && !/["&<>]/.test(url)) {
            urls.push(url);
            image.src = url;
          } else {
            URL.revokeObjectURL(url);
            showUnavailable();
          }
          if (canAccumulate) {
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "btn btn-outline";
            remove.textContent = picker.dataset.remove;
            remove.setAttribute("aria-label", `${picker.dataset.remove}: ${file.name}`);
            remove.addEventListener("click", () => {
              if (busy) return;
              const position = [...previews.querySelectorAll("button")].indexOf(remove);
              files.splice(index, 1);
              updateFiles(input);
              renderPreviews();
              const remaining = [...previews.querySelectorAll("button")];
              (remaining[Math.min(position, remaining.length - 1)] || chooser).focus();
            });
            figure.append(remove);
          }
          previews.append(figure);
        });
      }
      if (!canAccumulate && previews.childElementCount) {
        const clear = document.createElement("button");
        clear.type = "button";
        clear.className = "btn btn-outline";
        clear.textContent = picker.dataset.clear;
        clear.addEventListener("click", () => {
          if (busy) return;
          inputs.forEach(input => {
            input.value = "";
            selections.set(input, []);
          });
          renderPreviews();
          chooser.focus();
        });
        previews.append(clear);
      }
      updateCount();
    }
    inputs.forEach(input => input.addEventListener("change", () => {
      const chosen = [...input.files];
      selections.set(input, canAccumulate ? selections.get(input).concat(chosen) : chosen);
      if (canAccumulate) updateFiles(input);
      renderPreviews();
    }));
    chooser.addEventListener("click", () => {
      if (busy) return;
      nativePicker.click();
    });
    chooser.setAttribute("aria-describedby",
      [...picker.querySelectorAll(".field-errors[id]")].map(slot => slot.id).join(" "));
    picker.querySelector("[data-native-photos]").hidden = true;
    chooser.hidden = false;

    form.addEventListener("change", event => {
      if (event.target.name === "remove_photos") updateCount();
    });
    form.addEventListener("reset", () => {
      inputs.forEach(input => selections.set(input, []));
      renderPreviews();
    });
    form.addEventListener("click", event => {
      const link = event.target.closest(".error-summary a");
      if (!link || !inputs.some(input => link.getAttribute("href") === "#" + input.id)) return;
      event.preventDefault();
      for (let parent = picker.parentElement; parent && parent !== form; parent = parent.parentElement) {
        if (parent.tagName === "DETAILS") parent.open = true;
      }
      chooser.focus();
    });
    function setBusy(value) {
      busy = value;
      chooser.disabled = value;
      previews.querySelectorAll("button").forEach(button => { button.disabled = value; });
    }
    form.addEventListener("htmx:beforeRequest", event => {
      if (event.detail.elt === form) setBusy(true);
    });
    form.addEventListener("htmx:afterRequest", event => {
      if (event.detail.elt === form) setBusy(false);
    });
    window.addEventListener("pagehide", () => urls.forEach(url => URL.revokeObjectURL(url)));
  }
})();
