/* Render the OpenAPI document with the self-hosted Swagger UI on the API docs page. */
(() => {
  "use strict";
  const mount = document.getElementById("api-docs-swagger");
  if (mount && window.SwaggerUIBundle) {
    window.SwaggerUIBundle({
      url: mount.dataset.schemaUrl,
      dom_id: "#api-docs-swagger",
      deepLinking: true,
      validatorUrl: null,
      presets: [window.SwaggerUIBundle.presets.apis],
      layout: "BaseLayout",
    });
  }
})();
