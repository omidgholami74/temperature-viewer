/**
 * Dashboard runtime configuration.
 *
 * The UI and the PHP API are deployed on separate hosts, so the browser needs
 * the API origin explicitly. No trailing slash.
 *
 * Production:
 *   UI  https://temp.futurefounderco.com
 *   API https://apitemp.futurefounderco.com
 *
 * Local development keeps working without edits: the localhost UI talks to
 * the `php -S 0.0.0.0:8080` backend. Edit this file when deploying the UI to
 * another host, or set apiBaseUrl to '' when both share one reverse-proxy
 * origin.
 */
const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);

window.FURNACE_CONFIG = {
  apiBaseUrl: isLocalHost
    ? 'http://localhost:8080'
    : 'https://apitemp.futurefounderco.com',
};
