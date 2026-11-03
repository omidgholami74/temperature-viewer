# HTTPS for the dashboard and API

The dashboard and the API are two separate HTTPS origins:

- `https://temp.futurefounderco.com`
- `https://apitemp.futurefounderco.com`

`ui/config.js` copies the page scheme, so an HTTPS dashboard calls the HTTPS
API. That is the intended setup: it removes the browser's **Not secure** warning
and lets the PWA/service worker be installed.

## Current failure mode

The host is serving its shared default certificate:

```text
CN = *.web-hosting.com
SAN: DNS:*.web-hosting.com, DNS:web-hosting.com
```

That certificate is valid, but it does not match either furnace hostname. The
browser therefore shows a certificate interstitial on HTTPS, and its fetch
calls to the API fail with the same opaque error. The board itself keeps
working because the ESP32 uploads over its own HTTP/TLS connection; that is why
the admin can show **online** while the browser chart says offline.

An HTTP page can still work, but browsers increasingly upgrade it to HTTPS and
always mark it **Not secure**. Do not disable HTTPS checks or ask users to click
through a certificate warning. Issue a certificate for both names.

## 1. Issue the certificate

In cPanel:

1. Open **SSL/TLS Status**.
2. Select **both** `temp.futurefounderco.com` and
   `apitemp.futurefounderco.com`.
3. Choose **Run AutoSSL** (some hosts expose this as **Let's Encrypt SSL**).
4. Wait for the status to become **Valid** for both names.

AutoSSL needs the names to resolve to the hosting account and the HTTP
challenge path to be reachable. If it fails, check DNS and any redirect that
intercepts `/.well-known/acme-challenge/`, then run AutoSSL again. A certificate
for only one of the two names is not enough.

## 2. Verify the certificate

Run these from a machine outside the hosting account; do **not** use `curl -k`,
which hides exactly the failure we are fixing:

```bash
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' \
  https://temp.futurefounderco.com/
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' \
  https://apitemp.futurefounderco.com/api/health
```

Both should print `200 0`. Inspect the names too:

```bash
for host in temp.futurefounderco.com apitemp.futurefounderco.com; do
  echo | openssl s_client -connect "$host:443" -servername "$host" 2>/dev/null \
    | openssl x509 -noout -subject -ext subjectAltName
done
```

The SAN must contain each hostname, not `*.web-hosting.com`.

## 3. Make HTTPS the only public scheme

Only after both checks pass, add this to the UI `.htaccess` (the API host has
its own document root):

```apache
<IfModule mod_rewrite.c>
    RewriteEngine On
    RewriteCond %{HTTPS} !=on
    RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
</IfModule>

<IfModule mod_headers.c>
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"
</IfModule>
```

Do not deploy the redirect before the certificate is valid: it would send every
visitor to the certificate error. `includeSubDomains` is safe here only when
both hosts have the matching certificate.

## 4. Check the browser path

1. Open `https://temp.futurefounderco.com/` directly; there must be no warning.
2. Confirm the status line says **Live** and the chart receives samples.
3. Open `https://temp.futurefounderco.com/admin.html` and save the admin key;
   issue one harmless command (for example **Refresh**) and check it succeeds.
4. If the admin is on HTTPS and the API call still fails, check the API CORS
   origin for the exact scheme:

```bash
curl -sS -D - -o /dev/null \
  -H 'Origin: https://temp.futurefounderco.com' \
  https://apitemp.futurefounderco.com/api/live \
  | grep -i '^access-control-allow-origin'
```

The default backend allow-list already contains both the HTTP and HTTPS UI
origins. If the server overrides `FURNACE_ALLOWED_ORIGINS`, it must include the
HTTPS origin; `http://` and `https://` are different origins.
