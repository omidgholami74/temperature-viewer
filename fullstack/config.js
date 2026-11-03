/*
 * Runtime configuration for the combined deployment.
 *
 * The UI and the API share this origin, so every API request is same-origin:
 * no CORS, no second hostname and no second TLS certificate. The API client
 * appends /api/... to this value.
 */
window.FURNACE_CONFIG = {
  apiBaseUrl: '',
};
