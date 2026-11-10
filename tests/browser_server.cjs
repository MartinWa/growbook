// Use the server bundled with the Python package so client/server versions match.
const { chromium } = require(process.argv[2]);

(async () => {
  const server = await chromium.launchServer({
    channel: process.argv[3],
    host: "127.0.0.1",
  });
  const close = async () => {
    await server.close();
    process.exit(0);
  };
  process.once("SIGTERM", close);
  process.once("SIGINT", close);
  console.log(server.wsEndpoint());
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
