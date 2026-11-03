# Fullstack deployment

A single-origin deployment of the Furnace Monitor. The dashboard and the PHP
API are served from the **same host and the same document root**, so the
browser does not make a cross-origin request. This removes the
`temp` / `apitemp` split, the CORS allow-list and the need for two matching TLS
certificates.

This folder is a thin application entry point. The canonical source stays in:

| Source | Purpose |
| --- | --- |
| `../ui/` | Dashboard, settings and admin pages |
| `../backend/` | PHP API, SQLite storage and firmware delivery |

Nothing is copied into `fullstack/`, so a dashboard change is deployed once and
the combined app cannot drift from the normal two-host deployment.

## Request flow

```text
Browser / ESP32-S3
        │
        ▼
https://furnace.example.com/
        │
        ├── /api/*  ──▶ fullstack/index.php ──▶ ../backend/index.php
        │                                      (same SQLite database)
        │
        └── /*      ──▶ fullstack/index.php ──▶ ../ui/
                                               (HTML, CSS, JS, Chart.js)
```

`fullstack/config.js` sets `apiBaseUrl: ''`; the existing UI client then calls
`/api/...` on the same origin. The API's normal CORS code still works, but it is
not involved in the dashboard request.

## Requirements

- PHP 8.1+ with `pdo_sqlite` and `json`.
- A web server that supports URL rewriting (the included `.htaccess` is for
  Apache/LiteSpeed).
- Writable `../backend/var/` for SQLite and OTA bundles.
- The `../ui/` and `../backend/` directories next to this folder.

## Local run

From the repository root:

```bash
php backend/bin/install.php

php -S 127.0.0.1:8090 \
  -t fullstack \
  fullstack/index.php
```

Open <http://127.0.0.1:8090>. The built-in server uses `fullstack/index.php`
as its router. A simulated board can post to the same origin:

```bash
# Edit API_URL at the top of module/simulator.py first:
#   API_URL = "http://127.0.0.1:8090"
python3 module/simulator.py
```

The simulator has no command-line arguments; its `API_URL` is a constant at
the top of the file.

## Apache / LiteSpeed

Set the document root to this folder:

```apache
<VirtualHost *:443>
    ServerName furnace.example.com
    DocumentRoot /srv/furnace/fullstack

    <Directory /srv/furnace/fullstack>
        AllowOverride All
        Require all granted
    </Directory>

    SSLEngine on
    SSLCertificateFile      /etc/letsencrypt/live/furnace.example.com/fullchain.pem
    SSLCertificateKeyFile   /etc/letsencrypt/live/furnace.example.com/privkey.pem
</VirtualHost>
```

Only one hostname needs a certificate now. The `backend/` and `ui/` directories
are **outside** the document root, and the wrapper refuses to serve paths
outside `ui/`.

## Nginx

`fullstack/index.php` also works as a front controller under Nginx:

```nginx
server {
    listen 443 ssl;
    server_name furnace.example.com;
    root /srv/furnace/fullstack;

    location / {
        try_files $uri /index.php?$query_string;
    }

    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root/index.php;
        fastcgi_pass unix:/run/php/php8.2-fpm.sock;
    }
}
```

Do not point `root` at the repository root: that would expose `backend/`,
including its SQLite file and firmware bundles.

## Firmware

The board and the browser must use the same origin, otherwise the browser will
again make a cross-origin request:

```python
# esp32-s3/common/config.py
API_URL = "https://furnace.example.com"
```

The firmware posts to `POST /api/ingest`; the admin page uploads bundles to
`POST /api/firmware/upload`; the board downloads OTA files from
`GET /api/firmware/...`. All of these are served by the wrapper.

## Security and caching

- `config.js`, HTML and `sw.js` are sent with `Cache-Control: no-cache`, so a
  new deployment is not hidden by an installed PWA.
- Static assets get five minutes of caching; adjust `.htaccess` or the wrapper
  if your CDN handles caching instead.
- The wrapper sets a same-origin CSP (`connect-src 'self'`), `nosniff`,
  `DENY` framing and a strict referrer policy.
- `admin.html` is still a public static page. If it must not be reachable,
  protect it with HTTP Basic Auth or an SSO proxy at the web server; the API
  admin key protects writes, not the page itself.

## Verification

With PHP installed:

```bash
bash fullstack/tests/smoke.sh
```

It uses a temporary SQLite database and port, then checks the health endpoint,
the dashboard HTML, the same-origin config, ingest, live data, history and a
static asset. If PHP is not installed, the shell test reports that clearly
instead of failing with a misleading HTTP error.

## Relationship to the two-host deployment

The existing `ui/` and `backend/` deployment remains unchanged. Use this folder
when you want one host and one certificate; use the root README when you need
the static UI and API on separate servers. Both share the same source and
database schema, so ingest keys, admin keys and firmware bundles behave the
same way.
