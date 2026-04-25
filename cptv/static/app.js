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
  // Peel off any "ipv4." / "ipv6." / "secure." prefix to find the base domain.
  // Used by the dual-stack probe and the geolocation deep link.
  function getBaseDomain() {
    const host = window.location.hostname || "";
    const parts = host.split(".");
    if (["ipv4", "ipv6", "secure"].includes(parts[0])) {
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
    const scheme = window.location.protocol.replace(":", "");
    for (const [stack, url] of probes) {
      try {
        const resp = await fetch(url, { cache: "no-store" });
        if (!resp.ok) continue;
        const text = (await resp.text()).trim();
        if (!text) continue;
        const el = document.querySelector(`[data-ds="${stack}"]`);
        if (el) el.textContent = text;
        // Tag the row with the actual scheme used so a curious user
        // can see whether the probe used http or https. Tiny, inline.
        const badge = document.querySelector(`[data-ds-via="${stack}"]`);
        if (badge) badge.textContent = `via ${scheme}`;
      } catch {
        /* silent — dual-stack probe is best-effort */
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
    }
    if (asnStacks && (asn.ipv4 || asn.ipv6)) {
      // Merge when ASN + operator match; the prefix line is rendered
      // per-stack inside buildAsnRow so divergent prefixes get labelled
      // (IPv4 / IPv6) instead of duplicating the whole card.
      renderPerStack(asnStacks, asn, buildAsnRow, ["asn", "name"]);
      if (asnFallback) asnFallback.hidden = true;
    }

    // Hand the freshly-fetched per-stack info to the history tracker.
    return { geo, asn };
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

    // Detect which stacks the dual-stack probe actually saw. We rely on
    // the [data-ds] elements that detectDualStack() populates.
    const seen = (stack) => {
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
    if (isLocalhost || seen("ipv4")) stacks.push("ipv4");
    if (isLocalhost || seen("ipv6")) stacks.push("ipv6");

    if (stacks.length === 0) {
      // No stack we could probe successfully. Hide the whole card.
      const article = qs("#traceroute-section");
      if (article) article.hidden = true;
      return;
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

  // ---------- browser geolocation (opt-in) ----------
  // Browsers refuse navigator.geolocation on insecure origins (cptv.cz is
  // intentionally HTTP). When that's the case we don't try the API at all
  // — instead the button deep-links to https://secure.<base>/?ask-location=1
  // which auto-runs the prompt. Visiting secure. directly never triggers
  // the prompt unless the query param is present, so the user always
  // initiates the request explicitly.
  function requestGeolocation(out) {
    if (!out) return;
    out.textContent = "requesting…";
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude, accuracy } = pos.coords;
        // Build with text nodes so untrusted future inputs can't escape.
        const code = document.createElement("code");
        code.textContent = `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`;
        out.replaceChildren(code, document.createTextNode(` (±${Math.round(accuracy)} m)`));
      },
      (err) => {
        out.textContent = `denied or unavailable (${err.message})`;
      },
      { timeout: 10000, maximumAge: 0 },
    );
  }

  function wireGeolocationButton() {
    const btn = qs("#request-geolocation");
    const out = qs("#browser-location");
    if (!btn || !out) return;

    if (!window.isSecureContext) {
      // Insecure origin: API would always be blocked. Repurpose the button
      // as a deep link to the secure subdomain.
      const base = getBaseDomain();
      const target = `https://secure.${base}/?ask-location=1`;
      btn.textContent = "Open on secure. to share location";
      btn.title = `Browser blocks geolocation on http://. Will redirect to ${target}`;
      out.innerHTML =
        "<small>Browser geolocation is only allowed on secure (https) origins. " +
        "Click the button to continue on <code>secure.</code>.</small>";
      on(btn, "click", () => {
        window.location.href = target;
      });
      return;
    }

    if (!navigator.geolocation) return;

    // Secure context: only auto-trigger when the query param is set, so
    // visiting secure. manually never prompts unless the user requested it.
    const params = new URLSearchParams(window.location.search);
    const autoAsk = params.get("ask-location") === "1";

    on(btn, "click", () => requestGeolocation(out));

    if (autoAsk) {
      requestGeolocation(out);
    }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    checkClockSkew();
    checkDnssec();
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
  });
})();
