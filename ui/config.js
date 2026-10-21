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
 * The scheme is copied from the page instead of being hard-coded. Browsers
 * refuse to call an `http://` API from an `https://` page (mixed content), and
 * they refuse an `https://` API whose certificate is missing or does not match
 * the host. Matching the page scheme means:
 *
 *   https dashboard -> https API  (works once both hosts have a real cert)
 *   http  dashboard -> http  API  (works while the hosts only serve http)
 *
 * If the two hosts ever terminate TLS differently, replace the `apiBaseUrl`
 * expression with a literal origin such as 'https://apitemp.example.com', or
 * with '' when a reverse proxy serves the API on the dashboard's own origin.
 *
 * CORS: whichever origin the page is opened from must be listed in the API's
 * FURNACE_ALLOWED_ORIGINS, including the scheme. `http` and `https` are two
 * different origins.
 */
const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
const API_HOST = 'apitemp.futurefounderco.com';

// `file:` and other non-web schemes are not served over http, so default to
// https there instead of building an unusable `file://` URL.
const pageScheme = window.location.protocol === 'http:' ? 'http:' : 'https:';

window.FURNACE_CONFIG = {
  apiBaseUrl: isLocalHost
    ? 'http://localhost:8080'
    : `${pageScheme}//${API_HOST}`,
};
