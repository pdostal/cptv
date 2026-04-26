# cptv — CaPTiVe

Self-hosted, nerdy network diagnostics. Shows visitors detailed real-time info about their connection — IP, geolocation, ASN, DNS, traceroute, clock skew, DNSSEC validation.

Served over plain HTTP on purpose so captive portals (hotel Wi-Fi, airport networks) can intercept and redirect it. The `secure.<domain>` prefix has TLS enforced; `ipv4.<domain>` and `ipv6.<domain>` are protocol-forcing endpoints reachable over both HTTP and HTTPS so the dual-stack probe works from either context. The `http1.<domain>`, `http2.<domain>`, and `http3.<domain>` subdomains are dedicated probes pinned to a single HTTP version via nginx ALPN, so `curl --http2 https://http2.<domain>/protocol` (and the matching `--http1.1` / `--http3` flags) verify protocol negotiation directly.

The application is **domain-agnostic** — nothing about `cptv.cz` is hardcoded. The base domain is read from the `X-Base-Domain` nginx header at request time. See `PLAN.md` for the full specification.

> **Status:** early implementation. Scaffolding and CI are in place; many features from `PLAN.md` §4 are still pending. See `CLAUDE.md` for what works today vs. what's still to build.

## Table of contents

- [Running the container](#running-the-container)
  - [Required at deploy time](#required-at-deploy-time)
  - [Configuration (environment variables)](#configuration-environment-variables)
- [Production deployment with Podman Quadlet](#production-deployment-with-podman-quadlet)
  - [1. Quadlet unit files](#1-quadlet-unit-files)
  - [2. Load and start the units](#2-load-and-start-the-units)
  - [3. Enable Podman auto-update](#3-enable-podman-auto-update)
- [nginx reverse proxy](#nginx-reverse-proxy)
  - [`/etc/nginx/snippets/cptv-proxy.conf`](#etcnginxsnippetscptv-proxyconf)
  - [`/etc/nginx/sites-available/cptv.conf`](#etcnginxsites-availablecptvconf)
  - [HTTP/3 / QUIC prerequisites](#http3--quic-prerequisites)
- [Certbot (nginx plugin)](#certbot-nginx-plugin)
- [Protocol probes](#protocol-probes)
- [Building locally](#building-locally)
  - [Running Valkey locally for development](#running-valkey-locally-for-development)
  - [Building the container image](#building-the-container-image)
- [GeoLite2 setup](#geolite2-setup)
  - [1. Sign up and get a license key](#1-sign-up-and-get-a-license-key)
  - [2. Initial download](#2-initial-download)
  - [3. Systemd timer for weekly refresh](#3-systemd-timer-for-weekly-refresh)
- [Publishing](#publishing)
- [Security + quality scanning](#security--quality-scanning)
- [Privacy](#privacy)
- [Repository](#repository)

## Running the container

Images are published to `ghcr.io/pdostal/cptv` — container distribution only (no PyPI).

```sh
podman run --rm -p 8000:8000 \
  -e CPTV_QUICK_LINKS='[{"label":"Looking Glass","url":"https://lg.example.net","icon":"🔭"}]' \
  ghcr.io/pdostal/cptv:latest
```

The container expects [Valkey](https://valkey.io/) to be reachable for traceroute caching and rate limiting. A Podman-native deployment groups the app and Valkey containers in a shared **Pod** Quadlet so they reach each other on `localhost`, fronted by nginx with Certbot handling TLS for `secure.<domain>`.

### Required at deploy time

- An nginx reverse proxy that injects `X-Base-Domain`, `X-Forwarded-For`, and `X-Forwarded-Proto`.
- A Valkey instance reachable from the app container.
- GeoLite2 MMDB files on the host, bind-mounted into the container (see [GeoLite2 setup](#geolite2-setup) below).

### Configuration (environment variables)

| Variable                     | Purpose                                                                                                                    | Default                                   |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `CPTV_QUICK_LINKS`           | JSON array of `{label,url,icon?,description?}` objects rendered as a "Quick Links" section. Empty/unset hides the section. | unset                                     |
| `CPTV_GEOIP_CITY_DB`         | Path to `GeoLite2-City.mmdb`.                                                                                              | `/app/vendor/geolite2/GeoLite2-City.mmdb` |
| `CPTV_GEOIP_ASN_DB`          | Path to `GeoLite2-ASN.mmdb`.                                                                                               | `/app/vendor/geolite2/GeoLite2-ASN.mmdb`  |
| `CPTV_VALKEY_HOST`           | Valkey server hostname.                                                                                                    | `localhost`                               |
| `CPTV_VALKEY_PORT`           | Valkey server port.                                                                                                        | `6379`                                    |
| `CPTV_TRACEROUTE_CACHE_TTL`  | Traceroute result cache TTL in seconds.                                                                                    | `3600`                                    |
| `CPTV_MTR_PATH`              | Path to the `mtr` binary.                                                                                                  | `mtr`                                     |
| `CPTV_MTR_COUNT`             | Number of ICMP probes per hop.                                                                                             | `5`                                       |

All behaviour is configurable via env vars — no hardcoded constants in code.

---

## Production deployment with Podman Quadlet

The recommended production setup uses **systemd Quadlet** units to run the
app and Valkey as rootless Podman containers grouped inside a single
**Pod**. Pod members share a network namespace, so the app reaches
Valkey at `127.0.0.1:6379` with no user-defined network and no
container-name DNS to wire up. The pod is fronted by nginx with Certbot
for TLS on `secure.<domain>`.

### 1. Quadlet unit files

Place these files in `~/.config/containers/systemd/` (rootless) or
`/etc/containers/systemd/` (rootful). Quadlet generates one systemd
service per file.

There are two equally valid network setups. **Pick one.**

#### Recipe A — custom Podman network with a /80 carved from the host /64 (recommended for IPv6 traceroute)

A purpose-built Podman network with both an IPv4 RFC 6598 (CGNAT) range
and a globally-routable IPv6 /80 carved from the host's /64. Containers
get real, public v6 addresses, so `ping`, `mtr` and `traceroute` reach
the public IPv6 internet directly with no NAT66 jiggery-pokery.

`cptv.network` (Quadlet — drops in next to the `.pod` and `.container`
files):

```ini
[Unit]
Description=CPTV network

[Network]
NetworkName=cptv
Subnet=100.64.5.0/24
Gateway=100.64.5.1
IPv6=true
# Replace 2001:db8:abcd:1234:: with an /80 carved from your host /64.
Subnet=2001:db8:abcd:1234::/80
Gateway=2001:db8:abcd:1234::1
IPAMDriver=host-local
InterfaceName=podman-cptv
Label=app=cptv

[Install]
WantedBy=multi-user.target
```

`cptv.pod`:

```ini
[Unit]
Description=cptv pod (app + valkey share localhost)
After=network-online.target

[Pod]
PodName=cptv
# Only the app port is published to the host loopback; nginx is the
# only thing that talks to it. Valkey stays internal to the pod.
PublishPort=127.0.0.1:8000:8000
# Attach to the cptv network with fixed v4/v6 addresses so nginx and
# DNS records can refer to them stably.
Network=cptv:ip=100.64.5.2,ip=2001:db8:abcd:1234::2
Label=app=cptv

[Install]
WantedBy=multi-user.target
```

> **Why the /80?**
> Linux assigns one /128 from the configured subnet to each container.
> A /80 leaves 48 bits of host space — plenty for cptv plus future
> pods. Adjust the prefix length to taste; smaller (e.g. /112) is fine.
> Make sure your host's neighbour-discovery responds for the carved
> range — for most VPS setups this is automatic because the entire /64
> is on-link.

#### Recipe B — pasta (simplest, no extra .network file)

Pasta is the rootless network helper Podman ships with. `--ipv6`
lets the pod reach IPv6 destinations using the host's /64 directly,
without any sub-prefix delegation. Containers get a "fake" v6
address that pasta NATs through the host.

```ini
[Unit]
Description=cptv pod (app + valkey share localhost)
After=network-online.target

[Pod]
PodName=cptv
PublishPort=127.0.0.1:8000:8000
Network=pasta:--ipv6

[Install]
WantedBy=default.target
```

> Verify either recipe works with:
>
> ```sh
> podman exec cptv ping -c 1 -6 2606:4700:4700::1111
> ```
>
> If that fails but the host can reach the same address, check that
> `/proc/sys/net/ipv6/conf/all/forwarding` is `1` (set in
> `/etc/sysctl.d/`) and that the host firewall isn't dropping
> outbound ICMPv6 from the container interface.

#### `cptv-valkey.container`

```ini
[Unit]
Description=Valkey cache for cptv
Requires=cptv-pod.service
After=cptv-pod.service

[Container]
Image=docker.io/valkey/valkey:8-alpine
ContainerName=cptv-valkey
Pod=cptv.pod
AutoUpdate=registry

[Install]
WantedBy=default.target
```

#### `cptv.container`

```ini
[Unit]
Description=cptv network diagnostics
Requires=cptv-pod.service cptv-valkey.service
After=cptv-pod.service cptv-valkey.service

[Container]
Image=ghcr.io/pdostal/cptv:latest
ContainerName=cptv
Pod=cptv.pod
Volume=%h/.local/share/cptv/geolite2:/app/vendor/geolite2:ro,z
# mtr-packet uses raw ICMP sockets. Rootless Podman does not honour the
# file capability set on the binary inside the image, so the cap must
# be granted to the container explicitly. Without this you'll see
# "mtr-packet: Failure to start mtr-packet: Invalid argument".
AddCapability=CAP_NET_RAW
# Pod members share the network namespace, so Valkey is on localhost.
Environment=CPTV_VALKEY_HOST=127.0.0.1
Environment=CPTV_VALKEY_PORT=6379
AutoUpdate=registry

# Optional: a Quick Links section on the home page. CPTV_QUICK_LINKS is
# JSON; embedding it inline as Environment=… requires escaping every "
# (systemd's quoted-string parser eats unescaped ones), so the cleanest
# pattern is to source it from a separate env file. EnvironmentFile=
# below points at ~/.config/cptv/quick-links.env which contains:
#
#   CPTV_QUICK_LINKS_TITLE=Operator tools
#   CPTV_QUICK_LINKS=[{"label":"Status page","url":"https://status.example.net","icon":"\ud83d\udfe2"},{"label":"Internal wiki","url":"https://wiki.example.net","icon":"\ud83d\udcd6"}]
#
# (one variable per line, no quoting needed — systemd reads env files
# the same way Docker does, line-by-line VAR=value).
EnvironmentFile=-%h/.config/cptv/quick-links.env

[Install]
WantedBy=default.target
```

> **Quick Links live on the .container, not the .pod.** Quadlet
> doesn't forward `[Pod]` `Environment=` lines into individual
> container processes. After editing the env file:
>
> ```sh
> systemctl --user restart cptv.service
> curl -s http://cptv.example.com/?format=json | jq .quick_links
> ```
>
> The leading `-` on `EnvironmentFile=-...` means "ignore if missing",
> so the unit still starts when no Quick Links are configured.

### 2. Load and start the units

```sh
systemctl --user daemon-reload
systemctl --user start cptv-pod.service cptv-valkey.service cptv.service
systemctl --user enable cptv-pod.service cptv-valkey.service cptv.service
```

### 3. Enable Podman auto-update

Podman auto-update pulls newer images from the registry and restarts containers that have `AutoUpdate=registry` set.

```sh
# Enable the systemd timer (rootless)
systemctl --user enable --now podman-auto-update.timer

# Or run a one-off update check
podman auto-update
```

The timer runs daily by default. To customise the schedule, override the timer:

```sh
systemctl --user edit podman-auto-update.timer
```

```ini
[Timer]
OnCalendar=*-*-* 04:00:00
```

---

## nginx reverse proxy

nginx handles TLS termination, subdomain routing, and header injection. The base domain is passed to the app via the `X-Base-Domain` header — this is how the app stays domain-agnostic.

### `/etc/nginx/snippets/cptv-proxy.conf`

The same `proxy_pass` + `proxy_set_header` block lives in every cptv vhost. Extract it into a snippet so each vhost is one `include` line:

```nginx
# Shared upstream config for cptv. Include this from every vhost
# location block. The connection-protocol headers are essential —
# uvicorn always sees HTTP/1.1 from the loopback hop, so the app
# reads them to populate data["protocol"] and the /protocol endpoint.
proxy_pass         http://127.0.0.1:8000;
proxy_set_header   Host                       $host;
proxy_set_header   X-Base-Domain              $host_base_domain;
proxy_set_header   X-Forwarded-For            $remote_addr;
proxy_set_header   X-Forwarded-Proto          $scheme;
proxy_set_header   X-Forwarded-HTTP-Version   $server_protocol;
proxy_set_header   X-Forwarded-TLS-Version    $ssl_protocol;
proxy_set_header   X-Forwarded-TLS-Cipher     $ssl_cipher;
proxy_set_header   X-Forwarded-ALPN           $ssl_alpn_protocol;
```

### `/etc/nginx/sites-available/cptv.conf`

Replace `cptv.example.com` with your actual domain throughout.

```nginx
# Extract the base domain (strip www. prefix if present)
map $host $host_base_domain {
    default         cptv.example.com;
    ~^www\.(.+)$    $1;
}

# ---- Plain HTTP: apex + www + ipv4 + ipv6 ----
server {
    listen 80;
    listen [::]:80;
    server_name cptv.example.com www.cptv.example.com
                ipv4.cptv.example.com ipv6.cptv.example.com;

    location / {
        include snippets/cptv-proxy.conf;
    }
}

# ---- HTTPS on apex → redirect down to HTTP (captive-portal-friendly) ----
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name cptv.example.com www.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;

    return 301 http://$host$request_uri;
}

# ---- ipv4. / ipv6.: HTTPS mirror of the HTTP server above ----
# The home page on secure.<domain> probes ipv4./ipv6. via JavaScript.
# Without HTTPS here the browser blocks those requests as mixed content
# and the dual-stack section silently stays blank. Both names share the
# same Let's Encrypt certificate (see Certbot section below).
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ipv4.cptv.example.com ipv6.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;

    location / {
        include snippets/cptv-proxy.conf;
    }
}

# ---- secure.<domain>: HTTP → HTTPS redirect ----
server {
    listen 80;
    listen [::]:80;
    server_name secure.cptv.example.com;

    return 301 https://$host$request_uri;
}

# ---- secure.<domain>: HTTPS (TLS enforced) ----
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name secure.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;

    location / {
        include snippets/cptv-proxy.conf;
    }
}

# ---- http1.<domain>: pin the connection to HTTP/1.1 only ----
# `ssl_alpn http/1.1;` requires nginx >= 1.21.4. Clients that insist
# on HTTP/2 or HTTP/3 in their ALPN list will fail the handshake; this
# is the desired behaviour so curl --http2 demonstrably can't use this
# subdomain.
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    # NOTE: deliberately NO `http2 on;` here.
    server_name http1.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;
    ssl_alpn http/1.1;

    location / {
        include snippets/cptv-proxy.conf;
    }
}

# ---- http2.<domain>: prefer HTTP/2 with HTTP/1.1 fallback ----
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name http2.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;
    ssl_alpn h2 http/1.1;

    location / {
        include snippets/cptv-proxy.conf;
    }
}

# ---- http3.<domain>: HTTP/3 (QUIC) with h2 + h1 fallback ----
# Requires nginx >= 1.25 built with QUIC support (mainline nginx ships
# this since 1.25.0; Debian/Ubuntu's stock nginx may not). Clients
# discover h3 via the Alt-Svc header on prior responses.
#
# Firewall: open UDP/443 in addition to TCP/443 — HTTP/3 rides QUIC
# over UDP. Without it the browser silently falls back to h2 and the
# probe shows "❌ got h2".
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    listen 443 quic reuseport;
    listen [::]:443 quic reuseport;
    http2 on;
    http3 on;
    server_name http3.cptv.example.com;

    ssl_certificate     /etc/letsencrypt/live/cptv.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cptv.example.com/privkey.pem;
    ssl_alpn h3 h2 http/1.1;

    add_header Alt-Svc 'h3=":443"; ma=86400' always;

    location / {
        include snippets/cptv-proxy.conf;
    }
}
```

### HTTP/3 / QUIC prerequisites

- **nginx ≥ 1.25** built with QUIC support. Mainline nginx 1.25+ ships QUIC out of the box; older or distro-pinned builds may not. Check with `nginx -V 2>&1 | grep -o quic`.
- **`ssl_alpn` directive** requires nginx ≥ 1.21.4. Without it the `http1.<domain>` ALPN pinning won't work and `curl --http2` will succeed against `http1.<domain>`.
- **UDP/443 must be open** through host and cloud firewalls. HTTP/3 uses QUIC over UDP; without it the browser silently falls back to HTTP/2 and the capability table shows ❌.
- **`Alt-Svc` advertisement** is what tells browsers "I also speak h3 here". The first request still uses h2; subsequent ones may upgrade. Cold reloads will look like h2 until the cache is warm.

Enable the site:

```sh
ln -s /etc/nginx/sites-available/cptv.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

---

## Certbot (nginx plugin)

Obtain and auto-renew TLS certificates for `secure.<domain>` using Certbot with the nginx plugin. The certificates are also used by the apex HTTPS-to-HTTP redirect block.

```sh
# Install certbot + nginx plugin
apt install certbot python3-certbot-nginx    # Debian/Ubuntu
# dnf install certbot python3-certbot-nginx  # Fedora

# Obtain certificate (covers all subdomains in one cert)
certbot --nginx \
  -d cptv.example.com \
  -d www.cptv.example.com \
  -d secure.cptv.example.com \
  -d ipv4.cptv.example.com \
  -d ipv6.cptv.example.com \
  -d http1.cptv.example.com \
  -d http2.cptv.example.com \
  -d http3.cptv.example.com

# Verify auto-renewal timer is active
systemctl status certbot.timer
```

Certbot's nginx plugin will automatically update the `ssl_certificate` / `ssl_certificate_key` paths in your nginx config and set up a systemd timer for renewal.

---

## Protocol probes

`/protocol` reports the negotiated HTTP version, TLS version, and ALPN token for the current connection. The home page renders a capability table that lights up which of the `httpN.<domain>` probes the user's browser successfully negotiated; curl users can hit each one directly:

```sh
curl --http1.1 https://http1.cptv.example.com/protocol
# HTTP/1.1  TLSv1.3  http/1.1  encrypted

curl --http2   https://http2.cptv.example.com/protocol
# HTTP/2  TLSv1.3  h2  encrypted

curl --http3   https://http3.cptv.example.com/protocol
# HTTP/3  TLSv1.3  h3  encrypted
```

Output is a single tab-separated line — `cut -f1` pulls just the HTTP version. JSON (`?format=json` or `Accept: application/json`) and HTML formats are also available.

If the capability table shows ❌ for a row, check (in order):

1. The matching `httpN.<domain>` is reachable on TCP/443 (or UDP/443 for HTTP/3).
2. nginx version supports the required directive (`ssl_alpn` ≥ 1.21.4, QUIC ≥ 1.25).
3. For HTTP/3: UDP/443 is open through firewalls, and the `Alt-Svc` header is being advertised on prior responses.
4. The browser actually supports HTTP/3 (Safari needs the experimental flag in some versions).

---

## Building locally

Requires Python 3.12+, `uv`, Node 22+, and `podman` (or Docker).

```sh
# Python deps + test
uv sync --dev --frozen
uv run pytest

# JS/CSS assets
npm install
npm run build      # copies htmx + pico into cptv/static/vendor/

# Run dev server (no Valkey, no GeoIP — feature-dependent endpoints will degrade)
uv run uvicorn cptv.main:app --reload
```

### Running Valkey locally for development

```sh
podman run --rm -d --name cptv-valkey -p 6379:6379 docker.io/valkey/valkey:8-alpine
```

The app defaults to `localhost:6379` and will connect automatically. Stop it with `podman stop cptv-valkey`.

### Building the container image

```sh
podman build -f Containerfile -t cptv:dev .
```

GeoLite2 databases are **not** included in the image — mount them at runtime (see below).

## GeoLite2 setup

The MaxMind EULA prohibits redistributing GeoLite2 databases, so the container image ships without them. You download the databases to the host and bind-mount them into the container. A systemd timer keeps them fresh (MaxMind requires updates at least every 30 days).

### 1. Sign up and get a license key

1. Sign up at <https://www.maxmind.com/en/geolite2/signup>.
2. Generate a license key in the account portal.

### 2. Initial download

```sh
export MAXMIND_LICENSE_KEY=...     # your key
scripts/download-geolite2.sh       # writes ~/.local/share/cptv/geolite2/*.mmdb
```

Or download manually and place `GeoLite2-City.mmdb` and `GeoLite2-ASN.mmdb` in `~/.local/share/cptv/geolite2/`.

### 3. Systemd timer for weekly refresh

Create `~/.config/systemd/user/cptv-geolite2-refresh.service`:

```ini
[Unit]
Description=Refresh GeoLite2 databases for cptv

[Service]
Type=oneshot
Environment=MAXMIND_LICENSE_KEY=<your-key-here>
ExecStart=%h/.local/bin/cptv-download-geolite2.sh
```

Create `~/.config/systemd/user/cptv-geolite2-refresh.timer`:

```ini
[Unit]
Description=Weekly GeoLite2 refresh for cptv

[Timer]
OnCalendar=Mon *-*-* 05:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Copy the download script and enable the timer:

```sh
mkdir -p ~/.local/bin
cp scripts/download-geolite2.sh ~/.local/bin/cptv-download-geolite2.sh
chmod +x ~/.local/bin/cptv-download-geolite2.sh

systemctl --user daemon-reload
systemctl --user enable --now cptv-geolite2-refresh.timer
```

After a GeoLite2 refresh, restart the app container to pick up the new databases:

```sh
systemctl --user restart cptv.service
```

## Publishing

- **`release.yml`** — fires on `v*` tag push (or manual `workflow_dispatch`). Builds `linux/amd64` + `linux/arm64` image, pushes `latest` / `<version>` tags to GHCR with provenance attestations and SBOM, then creates a GitHub Release with auto-generated notes.

`GITHUB_TOKEN` is scoped to `contents: write` + `packages: write` only. No `MAXMIND_LICENSE_KEY` is needed — GeoLite2 databases are not in the image.

## Security + quality scanning

All run on every push and pull request (except OSSF Scorecard which runs weekly):

| Tool                                | Workflow        | Blocks on                              |
| ----------------------------------- | --------------- | -------------------------------------- |
| `ruff` (lint + format)              | `lint.yml`      | any finding                            |
| `pytest`                            | `test.yml`      | any failure                            |
| Lighthouse CI                       | `test.yml`      | accessibility or best-practices < 0.90 |
| `pip-audit`                         | `security.yml`  | any HIGH/CRITICAL CVE                  |
| `npm audit --audit-level=high`      | `security.yml`  | any HIGH+ CVE                          |
| `bandit -ll`                        | `security.yml`  | any HIGH-severity finding              |
| `ESLint` + `eslint-plugin-security` | `security.yml`  | any violation                          |
| `trivy` (container image)           | `security.yml`  | any CRITICAL OS/library CVE            |
| OSSF Scorecard                      | `scorecard.yml` | — (reports posture, uploads SARIF)     |

Scan jobs have **no repository secrets** and `contents: read` only — the principle of least privilege is enforced in the workflow permission blocks.

Dependabot watches `pip`, `npm`, `github-actions`, and `docker` ecosystems weekly.

## Privacy

The server logs **only the client IP** — no user-agent, no cookies, no session IDs, no fingerprinting. Traceroute results are cached in Valkey for up to 1 hour keyed by IP (IPv4) or `/64` prefix (IPv6), then expire. Nothing is sold, shared, or sent to third parties. Full statement in `PLAN.md` §13.

## Repository

<https://github.com/pdostal/cptv>
