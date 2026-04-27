// cptv client-side progressive enhancements.
// Everything the server can compute is already rendered by Jinja; this file
// only adds dual-stack detection, clock-skew warning, DNSSEC probe, and
// optional browser geolocation comparison. All of it degrades silently.

(() => {
  const qs = (s, root = document) => root.querySelector(s);
  const on = (el, ev, fn) => el && el.addEventListener(ev, fn);
  const ImageCtor = window.Image;

  // ---------- clock skew ----------
  const SKEW_THRESHOLD_SECONDS = 5;
  const SKEW_DISMISS_KEY = "cptv:clock-skew-dismissed:v1";

  function formatDuration(absSeconds) {
    if (absSeconds < 60) return `${absSeconds}s`;
    if (absSeconds < 3600) {
      const m = Math.floor(absSeconds / 60);
      const s = absSeconds % 60;
      return s ? `${m}m ${s}s` : `${m}m`;
    }
    if (absSeconds < 86400) {
      const h = Math.floor(absSeconds / 3600);
      const m = Math.floor((absSeconds % 3600) / 60);
      return m ? `${h}h ${m}m` : `${h}h`;
    }
    const d = Math.floor(absSeconds / 86400);
    const h = Math.floor((absSeconds % 86400) / 3600);
    return h ? `${d}d ${h}h` : `${d}d`;
  }

  function isSkewDismissed(diffSeconds) {
    try {
      const raw = window.localStorage.getItem(SKEW_DISMISS_KEY);
      if (!raw) return false;
      const data = JSON.parse(raw);
      // Suppress only if the dismissal was for a similar skew (within 60s).
      return (
        data &&
        typeof data.diff === "number" &&
        Math.abs(data.diff - diffSeconds) <= 60
      );
    } catch {
      return false;
    }
  }

  function dismissSkew(diffSeconds) {
    try {
      window.localStorage.setItem(
        SKEW_DISMISS_KEY,
        JSON.stringify({ diff: diffSeconds, at: new Date().toISOString() }),
      );
    } catch {
      /* ignore */
    }
  }

  function checkClockSkew() {
    const el = qs("#server-time");
    if (!el) return;
    const serverTs = el.getAttribute("data-server-ts");
    if (!serverTs) return;
    const server = Date.parse(serverTs);
    if (Number.isNaN(server)) return;
    const diffSeconds = Math.round((Date.now() - server) / 1000);
    if (Math.abs(diffSeconds) <= SKEW_THRESHOLD_SECONDS) return;
    if (isSkewDismissed(diffSeconds)) return;

    const warn = qs("#clock-skew-warning");
    if (!warn) return;
    warn.hidden = false;

    const direction = diffSeconds > 0 ? "ahead of" : "behind";
    const span = qs("#clock-skew-value");
    if (span) {
      span.textContent = `${formatDuration(Math.abs(diffSeconds))} ${direction}`;
    }
    const dismiss = qs("#clock-skew-dismiss");
    if (dismiss) {
      on(dismiss, "click", () => {
        dismissSkew(diffSeconds);
        warn.hidden = true;
      });
    }
  }

  // ---------- shared base-domain helper ----------
  // Peel off any subdomain prefix the app knows about to find the base
  // domain. Used by the dual-stack probe, the geolocation deep link,
  // and the per-protocol probe. Keep this list in sync with
  // SUBDOMAIN_PREFIXES in cptv/middleware.py.
  const KNOWN_SUBDOMAIN_PREFIXES = ["ipv4", "ipv6", "secure"];
  function getBaseDomain() {
    const host = window.location.hostname || "";
    const parts = host.split(".");
    if (KNOWN_SUBDOMAIN_PREFIXES.includes(parts[0])) {
      return parts.slice(1).join(".");
    }
    return host;
  }

  // ---------- dual-stack detection ----------
  async function detectDualStack() {
    const host = window.location.hostname;
    const port = window.location.port ? `:${window.location.port}` : "";
    if (!host || host === "localhost" || host === "127.0.0.1") return;

    const base = getBaseDomain();

    // Protocol-relative URLs let the browser pick the page's scheme:
    // http on the apex, https on secure.<domain>. Mixed-content
    // blocking is avoided either way as long as ipv4./ipv6. answer
    // both protocols (see README's nginx section).
    const probes = [
      ["ipv4", `//ipv4.${base}${port}/4?format=text`],
      ["ipv6", `//ipv6.${base}${port}/6?format=text`],
    ];
    for (const [stack, url] of probes) {
      try {
        const resp = await fetch(url, { cache: "no-store" });
        if (!resp.ok) continue;
        const text = (await resp.text()).trim();
        if (!text) continue;
        const el = document.querySelector(`[data-ds="${stack}"]`);
        if (el) el.textContent = text;
      } catch {
        // Silent — the dual-stack probe is best-effort. When the
        // user's network can reach only one stack (e.g. v4-only ISP
        // probing ipv6.<base>), the fetch fails at the network layer
        // and Firefox/Chrome log a generic "CORS request did not
        // succeed, status (null)" line in the console. The wording is
        // misleading: it is NOT a CORS misconfig — the response simply
        // never arrived. Expected; the page degrades silently and the
        // unreachable stack's row stays as "…".
      }
    }
  }

  // ---------- per-stack reverse DNS ----------
  // After dual-stack discovery has the IPs, fan out PTR lookups for
  // each stack and fill the [data-ds-rdns="ipv4|ipv6"] slots. The
  // server-side _collect() already populated the rDNS for the *current*
  // connection's IP; this fills in the *other* stack's rDNS once the
  // browser knows it.
  async function detectRdns() {
    const host = window.location.hostname;
    const port = window.location.port ? `:${window.location.port}` : "";
    if (!host || host === "localhost" || host === "127.0.0.1") return;

    const base = getBaseDomain();
    for (const stack of ["ipv4", "ipv6"]) {
      const ipEl = document.querySelector(`[data-ds="${stack}"]`);
      const ip = ipEl ? ipEl.textContent.trim() : "";
      // Skip when the dual-stack probe didn't find an IP for this stack
      // (placeholder is "…").
      if (!ip || ip === "…") continue;

      const cell = document.querySelector(`[data-ds-rdns="${stack}"]`);
      if (!cell) continue;

      const url = `//${stack}.${base}${port}/rdns/${encodeURIComponent(
        ip,
      )}?format=text`;
      try {
        const resp = await fetch(url, { cache: "no-store" });
        if (!resp.ok) continue;
        const text = (await resp.text()).trim();
        // Service emits "—" for "no PTR" — don't render that as a
        // hostname; just leave the slot empty.
        if (!text || text === "\u2014") continue;
        // Strip the negotiation hint comment that respond() appends to
        // text bodies (everything after the first '#tip:' marker).
        const hostname = text.split("\n")[0].trim();
        if (!hostname || hostname === "\u2014") continue;
        const codeEl = document.createElement("code");
        codeEl.textContent = hostname;
        cell.textContent = "\u21b3 ";
        cell.appendChild(codeEl);
      } catch {
        /* silent — rDNS is best-effort */
      }
    }
  }

  // ---------- per-stack ASN/GeoIP enrichment ----------
  // Once dual-stack discovery has the IPs, fetch /asn and /geoip from
  // both ipv4. and ipv6. so the GeoIP and ASN cards can show fresh
  // data per family. Equal values render once; differing values render
  // both labelled by stack.
  async function fetchStack(stack, base, port, path) {
    const url = `//${stack}.${base}${port}${path}?format=json`;
    try {
      const resp = await fetch(url, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      // Same situation as detectDualStack(): when the user's network
      // can reach only one stack, the cross-origin fetch to the other
      // stack's subdomain fails at the network layer and the browser
      // logs a "CORS request did not succeed, status (null)" line.
      // Not an actual CORS misconfig; just an unreachable host.
      return null;
    }
  }

  // Render the per-stack rows into a target container.
  //
  // When both stacks are present and `equalKeys` all match between v4
  // and v6, we hand BOTH dicts to mergedBuilder(ipv4, ipv6) so it can
  // render shared fields once and label only the fields that genuinely
  // differ. When v4 and v6 disagree on something material (or only one
  // stack is present), we fall back to one independent row per stack
  // via the same builder, passing only the relevant dict.
  function renderPerStack(target, perStack, mergedBuilder, equalKeys) {
    if (!target) return;
    target.replaceChildren();
    target.hidden = false;

    const ipv4 = perStack.ipv4;
    const ipv6 = perStack.ipv6;

    if (!ipv4 && !ipv6) {
      target.hidden = true;
      return;
    }

    const sharedShape =
      ipv4 &&
      ipv6 &&
      equalKeys.every(
        (k) => JSON.stringify(ipv4[k]) === JSON.stringify(ipv6[k]),
      );

    if (sharedShape) {
      // Both present, equalKeys match — show one merged row that the
      // builder can decorate with per-stack labels for the diverging
      // fields (e.g. prefix on ASN, coords on GeoIP).
      target.appendChild(mergedBuilder(ipv4, ipv6));
    } else {
      // Heading-prefixed rows for each present stack.
      if (ipv4) target.appendChild(mergedBuilder(ipv4, null, "IPv4"));
      if (ipv6) target.appendChild(mergedBuilder(null, ipv6, "IPv6"));
    }
  }

  // Helper: append a labelled-or-plain coords / prefix line to <ul>.
  // When the merged path passed both dicts and only this field differs,
  // we render one line per stack with a "(IPv4)" / "(IPv6)" suffix.
  function appendStackLine(ul, baseLabel, ipv4, ipv6, fmt) {
    const v4 = ipv4 ? fmt(ipv4) : null;
    const v6 = ipv6 ? fmt(ipv6) : null;
    if (v4 && v6 && v4.text === v6.text) {
      // Identical \u2014 one unlabelled line.
      const li = document.createElement("li");
      li.append(`${baseLabel}: `, ...v4.parts);
      ul.appendChild(li);
      return;
    }
    if (v4) {
      const li = document.createElement("li");
      const suffix = ipv6 ? " (IPv4)" : "";
      li.append(`${baseLabel}${suffix}: `, ...v4.parts);
      ul.appendChild(li);
    }
    if (v6) {
      const li = document.createElement("li");
      const suffix = ipv4 ? " (IPv6)" : "";
      li.append(`${baseLabel}${suffix}: `, ...v6.parts);
      ul.appendChild(li);
    }
  }

  function buildGeoipRow(ipv4, ipv6, headingLabel) {
    const a = ipv4 || ipv6;
    const ul = document.createElement("ul");
    if (headingLabel) {
      const head = document.createElement("li");
      head.innerHTML = `<strong>${headingLabel}</strong>`;
      ul.appendChild(head);
    }
    const country = document.createElement("li");
    country.textContent =
      `Country: ${a.country_code || "?"} ${a.country || ""}`.trimEnd();
    ul.appendChild(country);
    if (a.region) {
      const li = document.createElement("li");
      li.textContent = `Region: ${a.region}`;
      ul.appendChild(li);
    }
    const city = document.createElement("li");
    city.textContent = `City: ${a.city || "—"}`;
    ul.appendChild(city);
    // Coords \u2014 may differ slightly per stack, so use the labelled helper.
    appendStackLine(ul, "Coords", ipv4, ipv6, (d) => {
      if (d.latitude == null || d.longitude == null) return null;
      const text = `${d.latitude.toFixed(4)}, ${d.longitude.toFixed(4)}`;
      return { text, parts: [text] };
    });
    return ul;
  }

  function buildAsnRow(ipv4, ipv6, headingLabel) {
    const a = ipv4 || ipv6;
    const ul = document.createElement("ul");
    if (headingLabel) {
      const head = document.createElement("li");
      head.innerHTML = `<strong>${headingLabel}</strong>`;
      ul.appendChild(head);
    }
    const asn = document.createElement("li");
    asn.innerHTML = `ASN: <strong>AS${a.asn}</strong>`;
    ul.appendChild(asn);
    if (a.name) {
      const li = document.createElement("li");
      li.textContent = `Operator: ${a.name}`;
      ul.appendChild(li);
    }
    // Prefix \u2014 different per family; use the labelled helper.
    appendStackLine(ul, "Prefix", ipv4, ipv6, (d) => {
      if (!d.prefix) return null;
      const code = document.createElement("code");
      code.textContent = d.prefix;
      return { text: d.prefix, parts: [code] };
    });
    if (a.looking_glass) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = a.looking_glass;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "BGP looking glass ↗";
      li.appendChild(link);
      ul.appendChild(li);
    }
    return ul;
  }

  async function enrichDualStackInfo() {
    const host = window.location.hostname;
    if (!host || host === "localhost" || host === "127.0.0.1") return;
    const base = getBaseDomain();
    const port = window.location.port ? `:${window.location.port}` : "";

    const [g4, g6, a4, a6] = await Promise.all([
      fetchStack("ipv4", base, port, "/geoip"),
      fetchStack("ipv6", base, port, "/geoip"),
      fetchStack("ipv4", base, port, "/asn"),
      fetchStack("ipv6", base, port, "/asn"),
    ]);

    // Treat all-null GeoIP as "no data" so we don't render an empty row.
    const haveData = (d) =>
      d &&
      Object.values(d).some((v) => v !== null && v !== undefined && v !== "");

    const geoipStacks = qs("#geoip-stacks");
    const geoipFallback = qs("#geoip-fallback");
    const asnStacks = qs("#asn-stacks");
    const asnFallback = qs("#asn-fallback");

    const geo = { ipv4: haveData(g4) ? g4 : null, ipv6: haveData(g6) ? g6 : null };
    const asn = { ipv4: haveData(a4) ? a4 : null, ipv6: haveData(a6) ? a6 : null };

    if (geoipStacks && (geo.ipv4 || geo.ipv6)) {
      renderPerStack(geoipStacks, geo, buildGeoipRow, [
        "country_code",
        "country",
        "region",
        "city",
      ]);
      if (geoipFallback) geoipFallback.hidden = true;
      // SSR may have hidden the whole <article> (private client IP, no
      // GeoLite2 DB). Reveal it now that the per-stack probe found
      // usable data.
      qs("#geoip-section")?.removeAttribute("hidden");
      // Refresh the OSM pin with the freshest per-stack coords. Prefer
      // the v4 result when both are present and equal; either is fine
      // when they differ since the map is an approximate visual aid.
      const freshGeo = geo.ipv4 || geo.ipv6;
      if (freshGeo && freshGeo.latitude != null && freshGeo.longitude != null) {
        updateGeoIpPin(freshGeo.latitude, freshGeo.longitude);
      }
    }
    if (asnStacks && (asn.ipv4 || asn.ipv6)) {
      // Merge when ASN + operator match; the prefix line is rendered
      // per-stack inside buildAsnRow so divergent prefixes get labelled
      // (IPv4 / IPv6) instead of duplicating the whole card.
      renderPerStack(asnStacks, asn, buildAsnRow, ["asn", "name"]);
      if (asnFallback) asnFallback.hidden = true;
      qs("#asn-section")?.removeAttribute("hidden");
    }

    // Hand the freshly-fetched per-stack info to the history tracker.
    return { geo, asn };
  }

  // ---------- per-stack timing (end-to-end + TCP RTT/RTTvar/MSS) ----------
  // Replicates bgp.tools' "Timing" rows for each stack:
  //   * End-to-End:   median(K) of (responseEnd - startTime - X-Response-Time-Ms)
  //   * TCP Stack:    X-Tcp-Rtt-Us / X-Tcp-Rttvar-Us (nginx-injected)
  //   * TCP MSS:      X-Tcp-Mss (nginx-injected)
  // The TCP / MSS rows are populated only when the upstream nginx
  // exposes those headers; see README "Nginx configuration". The
  // end-to-end row works without any nginx changes.
  const TIMING_PROBE_SAMPLES = 5;

  // Median of an array of numbers (returns null on empty input).
  function median(values) {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2
      ? sorted[mid]
      : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function setTimingCell(slot, text, title) {
    const cell = document.querySelector(`[data-timing="${slot}"]`);
    if (!cell) return;
    cell.textContent = text;
    if (title) cell.title = title;
    const list = qs("#timing-stacks");
    if (list) list.hidden = false;
  }

  // Run K probes against //<stack>.<base>:port/timing/echo, recording
  // resource-timing entries and the X-Response-Time-Ms / X-Tcp-* headers
  // returned with each probe. Returns a summary or null when none of
  // the probes reached the server.
  async function probeStackTiming(stack, base, port) {
    const samples = [];
    let lastTcpHeaders = null;
    for (let i = 0; i < TIMING_PROBE_SAMPLES; i++) {
      const url = `//${stack}.${base}${port}/timing/echo?n=${i}-${Date.now()}`;
      let resp;
      try {
        resp = await fetch(url, {
          cache: "no-store",
          headers: { Accept: "text/plain" },
        });
      } catch {
        // Network-layer failure — most often a stack that the visitor
        // can't reach. Bail; the row stays "—".
        return null;
      }
      if (!resp.ok) continue;
      // Drain the body so the resource-timing entry's responseEnd is
      // populated before we read it.
      try {
        await resp.text();
      } catch {
        /* ignore */
      }
      const serverMs = parseFloat(resp.headers.get("X-Response-Time-Ms") || "");
      const entries = window.performance?.getEntriesByName?.(
        new URL(url, window.location.href).href,
      );
      const entry = entries && entries[entries.length - 1];
      if (entry && Number.isFinite(serverMs)) {
        const totalMs = entry.responseEnd - entry.startTime;
        const networkMs = totalMs - serverMs;
        if (Number.isFinite(networkMs) && networkMs >= 0) {
          samples.push(networkMs);
        }
      }
      // Capture TCP headers from the last successful probe; they're
      // identical across the burst because the connection is the same.
      // RTT/RTTvar and MSS are tracked independently so a deployment
      // that exposes only one of them (e.g. MSS via OpenResty Lua but
      // RTT via the default nginx path that only injects request
      // headers) still gets the available row populated. Earlier
      // versions gated MSS on RTT being present, which silently hid
      // MSS on operators who shipped only the Lua snippet.
      const rttUs = resp.headers.get("X-Tcp-Rtt-Us");
      const rttvarUs = resp.headers.get("X-Tcp-Rttvar-Us");
      const mss =
        resp.headers.get("X-Tcp-Mss-Server") ||
        resp.headers.get("X-Tcp-Mss");
      if (rttUs || rttvarUs || mss) {
        lastTcpHeaders = {
          rttUs: rttUs || null,
          rttvarUs: rttvarUs || null,
          mss: mss || null,
        };
      }
    }
    return { samples, tcp: lastTcpHeaders };
  }

  function formatMs(value) {
    return `${value.toFixed(1)}ms`;
  }

  function renderTimingForStack(stack, result) {
    if (!result) return;
    const e2e = median(result.samples);
    if (e2e !== null) {
      setTimingCell(`e2e-${stack}`, formatMs(e2e));
    }
    if (result.tcp) {
      // RTT and MSS render independently. Either may be present
      // without the other depending on what the operator wired into
      // nginx (proxy_set_header alone only feeds the request side; the
      // browser path requires add_header — see README).
      if (result.tcp.rttUs && result.tcp.rttvarUs) {
        const rttMs = parseInt(result.tcp.rttUs, 10) / 1000;
        const rttvarMs = parseInt(result.tcp.rttvarUs, 10) / 1000;
        if (Number.isFinite(rttMs) && Number.isFinite(rttvarMs)) {
          setTimingCell(
            `tcp-${stack}`,
            `${rttMs.toFixed(1)}ms [\u00b1${rttvarMs.toFixed(1)}ms]`,
          );
        }
      }
      if (result.tcp.mss) {
        const mss = parseInt(result.tcp.mss, 10);
        if (Number.isFinite(mss) && mss > 0) {
          setTimingCell(`mss-${stack}`, `${mss}b`);
        }
      }
    }
  }

  async function detectStackTimings() {
    const host = window.location.hostname;
    if (!host || host === "localhost" || host === "127.0.0.1") return;
    if (typeof window.performance?.getEntriesByName !== "function") return;
    const base = getBaseDomain();
    const port = window.location.port ? `:${window.location.port}` : "";

    const [v4, v6] = await Promise.all([
      probeStackTiming("ipv4", base, port),
      probeStackTiming("ipv6", base, port),
    ]);
    renderTimingForStack("ipv4", v4);
    renderTimingForStack("ipv6", v6);
  }

  // ---------- DNSSEC probe ----------
  // Loads an image from rhybar.cz (intentionally signed with an invalid
  // DNSSEC signature by CZ.NIC) and from a control host. If only the
  // control loads, the resolver validates DNSSEC.
  //
  // Each probe has three terminal states:
  //   * 'loaded' — confirmed reachable
  //   * 'errored' — confirmed unreachable
  //   * 'timeout' — no answer within DNSSEC_TIMEOUT_MS, treat as inconclusive
  // Crucially, a bogus-probe TIMEOUT is NOT the same as an explicit error:
  // a slow network shouldn't make us claim 'validating' when the resolver
  // might in fact return the bogus record.

  function checkDnssec() {
    const badge = qs("#dnssec-status");
    if (!badge) return;

    // 30 s gives slow validating resolvers (DNSSEC validation involves
    // signature checks + SERVFAIL chains) plenty of headroom. The badge
    // flashes 'validating…' until a verdict OR the timeout fires.
    const DNSSEC_TIMEOUT_MS = 30000;
    const results = { control: null, bogus: null }; // 'loaded' | 'errored' | 'timeout'

    const settle = (text) => {
      badge.textContent = text;
      badge.classList.remove("cptv-dnssec-pending");
    };

    const render = () => {
      // Conclusion table:
      //   control loaded  & bogus loaded   → 🔴 NOT OK
      //   control loaded  & bogus errored  → 🟢 OK
      //   control errored                 → ⚪ inconclusive (control unreachable)
      //   any timeout                     → ⚪ inconclusive (timeout)
      if (results.control === null || results.bogus === null) {
        // Still in progress: leave the flashing '⚪ validating…' label.
        return;
      }
      if (results.control === "timeout" || results.bogus === "timeout") {
        settle("⚪ inconclusive (probe timed out — your network may be slow)");
        return;
      }
      if (results.control === "errored") {
        settle("⚪ inconclusive (control unreachable)");
        return;
      }
      if (results.bogus === "loaded") {
        settle(
          "🔴 NOT OK — your resolver does not validate DNSSEC (bogus record accepted)",
        );
      } else {
        settle(
          "🟢 OK — your resolver validates DNSSEC (bogus rhybar.cz record rejected)",
        );
      }
    };

    const probe = (url, key) => {
      const img = new ImageCtor();
      let settled = false;
      const finish = (state) => {
        if (settled) return;
        settled = true;
        results[key] = state;
        render();
      };
      img.onload = () => finish("loaded");
      img.onerror = () => finish("errored");
      img.src = url;
      window.setTimeout(() => finish("timeout"), DNSSEC_TIMEOUT_MS);
    };

    probe(`https://www.iana.org/favicon.ico?t=${Date.now()}`, "control");
    // Apex rhybar.cz, not www. — both share the bogus DNSSEC signature.
    probe(`https://rhybar.cz/favicon.ico?t=${Date.now()}`, "bogus");
  }

  // ---------- resolver detection (client-side) ----------
  // Google operates the magic name `o-o.myaddr.l.google.com` whose TXT
  // record is the address of the recursive resolver that asked Google.
  // Querying it via DoH at dns.google reveals which resolver sits in
  // front of the visitor. This is the closest the web app can get to
  // server-side resolver detection without operating its own DNS zone.
  async function detectResolver() {
    const out = qs("#resolver-detected");
    if (!out) return;
    out.innerHTML = "<small>checking…</small>";
    try {
      const resp = await fetch(
        "https://dns.google/resolve?name=o-o.myaddr.l.google.com&type=TXT",
        { cache: "no-store", headers: { Accept: "application/dns-json" } },
      );
      if (!resp.ok) {
        out.innerHTML = `<small>resolver probe failed: HTTP ${resp.status}</small>`;
        return;
      }
      const data = await resp.json();
      const answers = (data.Answer || [])
        .map((a) => (a.data || "").replace(/^"|"$/g, ""))
        .filter(Boolean);
      if (answers.length === 0) {
        out.innerHTML = "<small>no TXT answer (resolver may block DoH probe)</small>";
        return;
      }
      out.innerHTML = answers
        .map((a) => `<code>${a}</code>`)
        .join(", ");
    } catch (err) {
      out.innerHTML = `<small>resolver probe failed (${err.message || "error"})</small>`;
    }
  }

  // ---------- theme toggle (Pico data-theme) ----------
  const THEME_KEY = "cptv:theme:v1";
  const THEMES = ["auto", "light", "dark"];

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }

  function readTheme() {
    try {
      const stored = window.localStorage.getItem(THEME_KEY);
      return THEMES.includes(stored) ? stored : "auto";
    } catch {
      return "auto";
    }
  }

  function writeTheme(theme) {
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }

  function wireThemeToggle() {
    const btn = qs("#theme-toggle");
    const label = qs("#theme-toggle-label");
    if (!btn) return;

    let current = readTheme();
    applyTheme(current);
    if (label) label.textContent = current;

    on(btn, "click", () => {
      const idx = THEMES.indexOf(current);
      current = THEMES[(idx + 1) % THEMES.length];
      applyTheme(current);
      writeTheme(current);
      if (label) label.textContent = current;
    });
  }

  // ---------- responsive nav toggle ----------
  // Replaces the previous <details>-based hamburger because Chromium
  // had quirks with display:contents on <details> hiding the children
  // even when the wrapper should be transparent. Plain <button> + JS
  // is boring and works everywhere.
  function wireNavToggle() {
    const nav = qs(".cptv-nav");
    const btn = qs(".cptv-nav-toggle");
    const menu = qs(".cptv-nav-menu");
    if (!nav || !btn || !menu) return;

    const setOpen = (open) => {
      if (open) nav.dataset.open = "true";
      else delete nav.dataset.open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    };

    on(btn, "click", (ev) => {
      ev.stopPropagation();
      setOpen(nav.dataset.open !== "true");
    });

    // Click outside the menu (or press Esc) closes the dropdown.
    on(document, "click", (ev) => {
      if (nav.dataset.open !== "true") return;
      if (nav.contains(ev.target)) return;
      setOpen(false);
    });
    on(document, "keydown", (ev) => {
      if (ev.key === "Escape") setOpen(false);
    });

    // If the viewport grows past the breakpoint, drop the open state so
    // resizing back to mobile starts fresh.
    const mql = window.matchMedia("(min-width: 721px)");
    const onResize = () => {
      if (mql.matches) setOpen(false);
    };
    mql.addEventListener?.("change", onResize);
  }

  // Apply stored theme as early as possible to avoid a flash of light/dark.
  applyTheme(readTheme());

  // ---------- traceroute SSE ----------
  // Two stream-capable panes (one per stack). The server-side endpoint
  // is the same; we hit ipv4.<base> / ipv6.<base> so the request lands
  // on the server with the matching family, and mtr traces it.
  // Stacks the dual-stack probe didn't actually see are hidden so the
  // user never sees a tab that can't possibly succeed.
  function wireTraceroutePane(pane, streamUrl) {
    const status = qs(".cptv-trace-status", pane);
    const tbody = qs(".cptv-trace-hops", pane);
    if (!status || !tbody || typeof window.EventSource !== "function") {
      return null;
    }

    const setStatus = (html) => {
      status.innerHTML = html;
    };

    const swapHop = (html) => {
      // The HTML is a single <tr id="hop-N">. Parse it, then replace any
      // existing row with the same id, otherwise append.
      const wrapper = document.createElement("tbody");
      wrapper.innerHTML = html.trim();
      const row = wrapper.firstElementChild;
      if (!row || row.tagName !== "TR" || !row.id) return;
      const existing = tbody.querySelector(`#${CSS.escape(row.id)}`);
      // OOB attribute is server-side noise here — strip it for cleanliness.
      row.removeAttribute("hx-swap-oob");
      if (existing) existing.replaceWith(row);
      else tbody.appendChild(row);
      // Briefly flash the row to draw the eye to fresh measurements.
      // Use double rAF to schedule the class add after the next paint
      // so we don't force layout (Firefox warns about that mid-load).
      row.classList.remove("cptv-hop-flash");
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          row.classList.add("cptv-hop-flash");
          window.setTimeout(() => row.classList.remove("cptv-hop-flash"), 700);
        });
      });
    };

    pane.classList.add("is-running");
    const stopPulse = () => pane.classList.remove("is-running");
    const source = new window.EventSource(streamUrl);

    let opened = false;
    source.addEventListener("open", () => {
      opened = true;
      setStatus(
        `<p><small>Connection established to ${streamUrl}; waiting for the first hop…</small></p>`,
      );
    });

    // If the browser never fires 'open' within this window, the SSE
    // connection probably failed (CORS, DNS, no route). Surface a clear
    // message instead of leaving the pane stuck on 'Connecting…'.
    window.setTimeout(() => {
      if (opened) return;
      setStatus(
        `<p><mark>\u26a0\ufe0f No response from <code>${streamUrl}</code> after 5 s. ` +
          "The stream may be blocked by CORS, the subdomain may not resolve, " +
          "or the server has no route to your address.</mark></p>",
      );
      stopPulse();
      source.close();
    }, 5000);

    source.addEventListener("status", (ev) => setStatus(ev.data));
    source.addEventListener("hop", (ev) => swapHop(ev.data));
    source.addEventListener("done", (ev) => {
      setStatus(ev.data);
      stopPulse();
      source.close();
    });
    source.addEventListener("error", (ev) => {
      // Browser fires a generic 'error' Event on connection problems with
      // empty data. Render the server-supplied message when present;
      // otherwise (CORS / network failure) the timeout above handles it.
      if (ev && typeof ev.data === "string" && ev.data) {
        setStatus(ev.data);
        stopPulse();
      }
    });
    return source;
  }

  function wireTracerouteTabs() {
    const card = qs("#traceroute-card");
    if (!card) return;

    const base = getBaseDomain();
    const port = window.location.port ? `:${window.location.port}` : "";

    const isLocalhost =
      !base || base === "localhost" || base === "127.0.0.1";

    // The current connection's stack is always traceable — the user is
    // literally connected on it. The SSR template intentionally omits
    // the [data-ds] element for the *current* stack (it renders only
    // the *other* stack in #dual-stack), so we can't rely on data-ds
    // alone for the current-connection stack. Read the protocol
    // attribute the server already exposed on #dual-stack.
    const currentProtocol = qs("#dual-stack")?.dataset.protocol || "";
    const currentStack =
      currentProtocol === "IPv4"
        ? "ipv4"
        : currentProtocol === "IPv6"
          ? "ipv6"
          : null;

    // probed() detects the *other* stack via the data-ds cell that
    // detectDualStack() populates asynchronously after page load.
    const probed = (stack) => {
      const el = document.querySelector(`[data-ds="${stack}"]`);
      if (!el) return false;
      const txt = el.textContent.trim();
      return Boolean(txt) && txt !== "…" && txt !== "—";
    };

    // Localhost dev convenience: probe and trace via the current origin
    // because there's no ipv4./ipv6. nginx routing in development.
    const baseFor = (stack) =>
      isLocalhost ? "" : `//${stack}.${base}${port}`;

    const stacks = [];
    for (const stack of ["ipv4", "ipv6"]) {
      if (isLocalhost || stack === currentStack || probed(stack)) {
        stacks.push(stack);
      }
    }

    if (stacks.length === 0) {
      // No detectable stack at all (no current protocol AND no probe
      // success — rare: private/loopback IP with both subdomains
      // unreachable). Keep the card visible with an explanatory note
      // instead of vanishing silently.
      card.replaceChildren();
      const p = document.createElement("p");
      const small = document.createElement("small");
      small.textContent =
        "Traceroute unavailable: neither IPv4 nor IPv6 was detected for this connection.";
      p.appendChild(small);
      card.appendChild(p);
      return;
    }

    // Prefer auto-starting the current-connection stack so the first
    // hop arrives instantly (the user is already connected on it).
    if (
      currentStack &&
      stacks.includes(currentStack) &&
      stacks[0] !== currentStack
    ) {
      stacks.splice(stacks.indexOf(currentStack), 1);
      stacks.unshift(currentStack);
    }

    const sources = {};
    let activeSource = null;
    const radios = card.querySelectorAll('input[name="trace-stack"]');

    const startStack = (stack) => {
      const pane = card.querySelector(`[data-trace-stack="${stack}"]`);
      if (!pane) return;
      // Show this pane, hide others.
      card
        .querySelectorAll(".cptv-trace-pane")
        .forEach((p) => (p.hidden = p !== pane));
      // Already streaming for this stack? Nothing to do.
      if (sources[stack]) {
        activeSource = sources[stack];
        return;
      }
      const url = `${baseFor(stack)}/traceroute/stream`;
      const source = wireTraceroutePane(pane, url);
      sources[stack] = source;
      activeSource = source;
    };

    // Reveal the labels for stacks we'll actually use, and bind change.
    stacks.forEach((stack, idx) => {
      const label = qs(`#trace-tab-${stack}-label`, card);
      if (label) label.hidden = false;
      const radio = card.querySelector(
        `input[name="trace-stack"][value="${stack}"]`,
      );
      if (radio && idx === 0) radio.checked = true;
    });

    radios.forEach((r) => {
      r.addEventListener("change", () => {
        if (r.checked) startStack(r.value);
      });
    });

    // Auto-start the first available stack (matches existing UX).
    startStack(stacks[0]);
  }

  // The dual-stack probe runs async; defer wiring until both probes
  // have had a chance to populate the [data-ds] elements (or give up).
  function wireTracerouteWhenStacksKnown() {
    // detectDualStack uses ~1 round-trip per stack; 1.2s is comfortable
    // headroom on broadband and still feels live on slower links.
    window.setTimeout(wireTracerouteTabs, 1200);
  }

  // ---------- anycast PoP detection ----------
  // Cloudflare's /cdn-cgi/trace returns plaintext key=value lines including
  // a `colo=` field naming the airport-style PoP code that served you. This
  // gives a quick read on which Cloudflare datacenter your network reaches.
  async function detectAnycastPop() {
    const list = qs("#anycast-results");
    if (!list) return;
    list.innerHTML = "";

    const probes = [
      {
        name: "Cloudflare",
        url: "https://1.1.1.1/cdn-cgi/trace",
        parse: (text) => {
          const map = {};
          for (const line of text.split("\n")) {
            const idx = line.indexOf("=");
            if (idx > 0) map[line.slice(0, idx)] = line.slice(idx + 1).trim();
          }
          if (!map.colo) return null;
          const parts = [`PoP <code>${map.colo}</code>`];
          if (map.loc) parts.push(`country <code>${map.loc}</code>`);
          if (map.ip) parts.push(`seen as <code>${map.ip}</code>`);
          return parts.join(" · ");
        },
      },
    ];

    for (const probe of probes) {
      const li = document.createElement("li");
      li.innerHTML = `<strong>${probe.name}</strong>: <small>checking…</small>`;
      list.appendChild(li);
      try {
        const resp = await fetch(probe.url, { cache: "no-store" });
        if (!resp.ok) {
          li.innerHTML = `<strong>${probe.name}</strong>: <small>HTTP ${resp.status}</small>`;
          continue;
        }
        const text = await resp.text();
        const summary = probe.parse(text);
        if (summary) {
          li.innerHTML = `<strong>${probe.name}</strong>: ${summary}`;
        } else {
          li.innerHTML = `<strong>${probe.name}</strong>: <small>no PoP info in response</small>`;
        }
      } catch (err) {
        li.innerHTML = `<strong>${probe.name}</strong>: <small>unreachable (${err.message || "error"})</small>`;
      }
    }
  }

  // ---------- session history (localStorage, never sent to server) ----------
  // v2 schema:
  //   { ip, protocol, asn_number, asn_name, city, first_seen, last_seen, count }
  // The v2 storage key is separate from v1 so we don't have to migrate
  // partial entries; old data coexists harmlessly until the user clears.
  const HISTORY_KEY = "cptv:history:v2";

  function readHistory() {
    try {
      const raw = window.localStorage.getItem(HISTORY_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function writeHistory(entries) {
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
    } catch {
      /* storage may be disabled or full — silently give up */
    }
  }

  // Record (or update) an IP, merging new fields onto the existing entry
  // when present. Empty / placeholder values are ignored so a transient
  // '…' from the dual-stack probe never overwrites a real IP.
  function recordSeen(ip, protocol, extras = {}) {
    if (!ip || ip === "…" || ip === "—" || ip === "unknown") return null;
    const now = new Date().toISOString();
    const entries = readHistory();
    const existing = entries.find((e) => e.ip === ip);
    if (existing) {
      existing.last_seen = now;
      existing.count = (existing.count || 1) + 1;
      if (protocol && !existing.protocol) existing.protocol = protocol;
      // Always overwrite enrichment fields with the freshest data.
      if (extras.asn_number != null) existing.asn_number = extras.asn_number;
      if (extras.asn_name) existing.asn_name = extras.asn_name;
      if (extras.city) existing.city = extras.city;
    } else {
      entries.push({
        ip,
        protocol: protocol || null,
        asn_number: extras.asn_number ?? null,
        asn_name: extras.asn_name ?? null,
        city: extras.city ?? null,
        first_seen: now,
        last_seen: now,
        count: 1,
      });
    }
    writeHistory(entries);
    return entries;
  }

  function renderHistory() {
    const list = qs("#ip-history");
    if (!list) return;
    const entries = readHistory();
    list.replaceChildren();
    if (entries.length === 0) {
      const empty = document.createElement("li");
      const small = document.createElement("small");
      small.textContent = "No history yet.";
      empty.appendChild(small);
      list.appendChild(empty);
      return;
    }
    // Most recently seen first.
    const sorted = [...entries].sort((a, b) =>
      (b.last_seen || "").localeCompare(a.last_seen || ""),
    );
    for (const entry of sorted) {
      const li = document.createElement("li");
      // Build via DOM API so cached localStorage values are never
      // reinterpreted as HTML (CodeQL js/xss-through-dom).
      const code = document.createElement("code");
      code.textContent = String(entry.ip || "");
      li.appendChild(code);

      const small = document.createElement("small");
      const bits = [];
      if (entry.protocol) bits.push(entry.protocol);
      if (entry.asn_number != null) {
        const asnLabel = entry.asn_name
          ? `AS${entry.asn_number} ${entry.asn_name}`
          : `AS${entry.asn_number}`;
        bits.push(asnLabel);
      }
      if (entry.city) bits.push(entry.city);
      if (entry.count > 1) bits.push(`seen ${entry.count}\u00d7`);
      const last = entry.last_seen ? String(entry.last_seen).replace("T", " ") : "?";
      bits.push(`last ${last}`);
      small.textContent = ` \u2014 ${bits.join(" \u00b7 ")}`;
      li.appendChild(small);
      list.appendChild(li);
    }
  }

  function wireHistoryClearButton() {
    const btn = qs("#history-clear");
    if (!btn) return;
    on(btn, "click", () => {
      try {
        window.localStorage.removeItem(HISTORY_KEY);
      } catch {
        /* ignore */
      }
      renderHistory();
    });
  }

  // Pull the per-stack ASN/city info that enrichDualStackInfo() fetched
  // and stamp it onto the matching entry. Falls through gracefully when
  // the enrichment is missing (offline, CORS-blocked, etc.).
  function extrasFor(stack, enriched) {
    if (!enriched) return {};
    const a = enriched.asn?.[stack];
    const g = enriched.geo?.[stack];
    return {
      asn_number: a?.asn ?? null,
      asn_name: a?.name ?? null,
      city: g?.city ?? null,
    };
  }

  function trackHistory(enriched) {
    // Pull current IP / protocol off the page (already rendered server-side).
    const currentEl = qs(".ip-current");
    const dualEl = qs("#dual-stack");
    const currentIp = currentEl ? currentEl.textContent.trim() : null;
    const protocol = dualEl ? dualEl.dataset.protocol || null : null;
    if (currentIp) {
      const stack = protocol === "IPv6" ? "ipv6" : protocol === "IPv4" ? "ipv4" : null;
      recordSeen(currentIp, protocol, stack ? extrasFor(stack, enriched) : {});
    }

    // Record any other-stack IP discovered by the dual-stack probe and
    // tag it with that stack's enrichment data.
    document.querySelectorAll("[data-ds]").forEach((el) => {
      const stack = el.dataset.ds;
      const ip = el.textContent.trim();
      const proto = stack === "ipv4" ? "IPv4" : stack === "ipv6" ? "IPv6" : null;
      recordSeen(ip, proto, extrasFor(stack, enriched));
    });

    renderHistory();
  }

  // ---------- OpenStreetMap rendering ----------
  // Renders a Leaflet map with the GeoIP pin and, optionally, a second
  // browser-geolocation pin when the user has opted in. The map module
  // (leaflet.js) is loaded lazily via a <script defer> in the template,
  // so window.L may not exist yet at DOMContentLoaded; initGeoMap()
  // gracefully no-ops in that case and the map simply stays blank.
  let _geoMap = null;
  let _geoIpMarker = null;
  let _geoBrowserMarker = null;

  function initGeoMap() {
    const el = document.getElementById("geo-map");
    if (!el || !window.L) return null;
    const lat = parseFloat(el.dataset.lat);
    const lon = parseFloat(el.dataset.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    // scrollWheelZoom off so the map doesn't hijack page scrolling on
    // mobile; users can still drag-pan and use the +/- controls.
    _geoMap = window.L.map(el, { zoomControl: true, scrollWheelZoom: false }).setView(
      [lat, lon],
      8,
    );
    // Leaflet's built-in attribution control (bottom-right of the map)
    // satisfies the OpenStreetMap Foundation tile-usage policy without
    // needing a separate <small> line under the map.
    // https://operations.osmfoundation.org/policies/tiles/
    window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',
    }).addTo(_geoMap);
    _geoIpMarker = window.L.marker([lat, lon], { title: "GeoIP estimate" })
      .addTo(_geoMap)
      .bindPopup("GeoIP estimate");
    // Container size may settle to its final width after the first
    // paint (CSS grid column resolution). invalidateSize() forces
    // Leaflet to recompute and re-render tiles so the map shows even
    // if the column briefly had zero width at init time — the classic
    // "Leaflet renders blank when initialised in a hidden / not-yet-
    // sized container" gotcha.
    requestAnimationFrame(() => _geoMap && _geoMap.invalidateSize());
    return _geoMap;
  }

  function updateGeoIpPin(lat, lon) {
    if (!_geoMap || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
    if (_geoIpMarker) _geoMap.removeLayer(_geoIpMarker);
    _geoIpMarker = window.L.marker([lat, lon], { title: "GeoIP estimate" })
      .addTo(_geoMap)
      .bindPopup("GeoIP estimate");
    _fitGeoBounds();
  }

  function setBrowserPin(lat, lon, accuracy) {
    if (!_geoMap || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
    if (_geoBrowserMarker) _geoMap.removeLayer(_geoBrowserMarker);
    // Distinguish the browser pin from the default GeoIP pin via a
    // coloured circle marker so the two are obviously different at a
    // glance, even without opening the popups.
    _geoBrowserMarker = window.L.circleMarker([lat, lon], {
      radius: 8,
      color: "#0050b8",
      weight: 2,
      fillOpacity: 0.5,
    })
      .addTo(_geoMap)
      .bindPopup(`Browser location (±${Math.round(accuracy)} m)`);
    _fitGeoBounds();
  }

  function _fitGeoBounds() {
    const layers = [_geoIpMarker, _geoBrowserMarker].filter(Boolean);
    if (layers.length < 2) return;
    const group = window.L.featureGroup(layers);
    _geoMap.fitBounds(group.getBounds().pad(0.3));
  }

  // ---------- browser geolocation (opt-in) ----------
  // Browsers refuse navigator.geolocation on insecure origins (cptv.cz is
  // intentionally HTTP). When that's the case we don't try the API at all
  // — instead the button deep-links to https://secure.<base>/?ask-location=1
  // which auto-runs the prompt. Visiting secure. directly never triggers
  // the prompt unless the query param is present, so the user always
  // initiates the request explicitly.
  function requestGeolocation(out) {
    if (!out) return;
    // Un-hide the placeholder now that we have something to render
    // (status / coords / error). Keeps the card clean before any click.
    out.hidden = false;
    out.textContent = "requesting…";
    // Hide the button on any final state (success or error). The user
    // has used the prompt; re-clicking would just re-trigger and
    // clutter the card. A page reload is the explicit retry path.
    const hideButton = () => {
      const btn = document.getElementById("request-geolocation");
      if (btn) btn.hidden = true;
    };
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        // Build with text nodes so untrusted future inputs can't escape.
        const code = document.createElement("code");
        code.textContent = `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`;
        out.replaceChildren(code, document.createTextNode(` (±${Math.round(accuracy)} m)`));
        // Drop a second pin on the OSM map (if it's been initialised) so
        // the user can visually compare the GeoIP estimate with their
        // real position.
        setBrowserPin(latitude, longitude, accuracy);
        hideButton();
      },
      (err) => {
        out.textContent = `denied or unavailable (${err.message})`;
        hideButton();
      },
      { timeout: 10000, maximumAge: 0 },
    );
  }

  function wireGeolocationButton() {
    const btn = qs("#request-geolocation");
    const out = qs("#browser-location");
    if (!btn || !out) return;

    if (!window.isSecureContext) {
      // Apex (HTTP). The Geolocation API is blocked on insecure origins,
      // so the click deep-links to secure.<base> with ?ask-location=1
      // and the prompt fires on arrival. Button label is set server-side
      // by Jinja so there's no flash of the wrong copy on slow JS.
      const base = getBaseDomain();
      const target = `https://secure.${base}/?ask-location=1`;
      btn.title = `Browser blocks geolocation on http://. Will redirect to ${target}`;
      on(btn, "click", () => {
        window.location.href = target;
      });
      return;
    }

    if (!navigator.geolocation) return;

    // Secure context: trigger the API on click. Auto-fire when the page
    // was opened via the apex deep link (?ask-location=1) so the round
    // trip works end-to-end; visiting secure. manually never prompts.
    on(btn, "click", () => requestGeolocation(out));
    const params = new URLSearchParams(window.location.search);
    const autoAsk = params.get("ask-location") === "1";

    if (autoAsk) {
      requestGeolocation(out);
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    checkClockSkew();
    checkDnssec();
    initGeoMap(); // No-op when Leaflet missing or coords absent.
    wireGeolocationButton();
    wireHistoryClearButton();
    detectAnycastPop();
    detectResolver();
    wireThemeToggle();
    wireNavToggle();
    renderHistory(); // Always render the history card from cached entries.

    // detectDualStack must finish before per-stack enrichment fires so we
    // know which IPs to enrich. Enrichment then feeds the history tracker
    // with fresh ASN/city values for each stack's IP.
    await detectDualStack();
    const enriched = await enrichDualStackInfo();
    trackHistory(enriched);
    wireTracerouteWhenStacksKnown();
    // PTR lookups for both stacks. Best-effort and runs after history
    // so the rest of the page is fully alive while DNS resolves.
    detectRdns();
    // Per-stack end-to-end / TCP / MSS timing rows. Fires last so the
    // probe burst doesn't compete with the dual-stack & enrichment
    // requests; degrades silently when nginx isn't injecting the
    // X-Tcp-* headers (only the End-to-End rows then populate).
    detectStackTimings();
  });
})();
