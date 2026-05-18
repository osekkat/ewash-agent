(function () {
  const BOOKINGS_STORAGE_KEY = ["ewash.bookings", "token"].join("_");
  const PHONE_STORAGE_KEY = "ewash.phone";
  const NAME_STORAGE_KEY = "ewash.name";
  const BOOTSTRAP_CACHE_KEY = "ewash.bootstrap_cache.v1";
  const BOOTSTRAP_CACHE_VERSION = 1;
  const BOOTSTRAP_CACHE_TTL_MS = 24 * 60 * 60 * 1000;
  const DEFAULT_TIMEOUT_MS = 10000;
  const LOG_RING_LIMIT = 200;

  function _makeSessionId() {
    const cryptoObj = window.crypto || window.msCrypto;
    if (cryptoObj && cryptoObj.getRandomValues) {
      const bytes = new Uint8Array([0, 0, 0, 0]);
      cryptoObj.getRandomValues(bytes);
      return Array.from(bytes, function (b) {
        return b.toString(16).padStart(2, "0");
      }).join("");
    }
    return Math.random().toString(16).slice(2, 10).padEnd(8, "0").slice(0, 8);
  }

  function _createLogger() {
    const ring = [];
    const debugMode = new URLSearchParams(location.search).has("debug");
    const sessionId = _makeSessionId();

    function _push(level, scope, payload) {
      const fullScope = scope.indexOf("ewash.") === 0 ? scope : "ewash." + scope;
      const entry = Object.assign(
        {
          t: new Date().toISOString(),
          level: level,
          scope: fullScope,
          session: sessionId,
        },
        payload || {}
      );
      ring.push(entry);
      if (ring.length > LOG_RING_LIMIT) ring.shift();

      const suffix = level === "error" ? ".fatal" : level === "warn" ? ".warn" : "";
      const label = "[" + fullScope + suffix + "]";
      if (level === "error") console.error(label, entry);
      else if (level === "warn") console.warn(label, entry);
      else console.info(label, entry);

      try {
        window.dispatchEvent(new CustomEvent("ewashlog", { detail: entry }));
      } catch (_) {
        // Logging must never break the customer flow.
      }
    }

    async function hash(value) {
      if (!value) return "";
      try {
        const digest = await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(String(value))
        );
        return Array.from(new Uint8Array(digest))
          .slice(0, 4)
          .map(function (b) {
            return b.toString(16).padStart(2, "0");
          })
          .join("");
      } catch (_) {
        return "";
      }
    }

    return {
      info: function (scope, payload) { _push("info", scope, payload); },
      warn: function (scope, payload) { _push("warn", scope, payload); },
      fatal: function (scope, payload) { _push("error", scope, payload); },
      snapshot: function () { return ring.slice(); },
      hash: hash,
      sessionId: sessionId,
      debugMode: debugMode,
    };
  }

  const EwashLog = window.EwashLog || _createLogger();
  window.EwashLog = EwashLog;
  if (EwashLog.debugMode) {
    EwashLog.info("lifecycle.boot", {
      url: location.href,
      ua: navigator.userAgent,
      api_base: window.EWASH_API_BASE || "",
    });
  }

  function _base() {
    return window.EWASH_API_BASE || "";
  }

  function _durationMs(startedAt) {
    return (performance.now() - startedAt).toFixed(0);
  }

  function _jsonErrorBody(resp) {
    return resp.json().catch(function () {
      return {
        error_code: "non_json",
        message: resp.statusText,
      };
    });
  }

  function _setDuration(err, duration) {
    if (err && typeof err === "object") {
      try {
        err.duration_ms = +duration;
      } catch (_) {
        // Some browser error objects are not extensible.
      }
    }
  }

  function _setRetryAfter(err, resp, errBody) {
    if (!err || typeof err !== "object") return;
    const header = resp && resp.headers ? resp.headers.get("Retry-After") : null;
    const raw = header || (errBody && (errBody.retry_after || errBody.retry_after_seconds));
    const parsed = Number(raw);
    if (Number.isFinite(parsed) && parsed > 0) err.retry_after = parsed;
  }

  function _apiScope(path) {
    const cleanPath = path.split("?")[0].replace(/^\/api\/v1\/?/, "");
    const scope = cleanPath || "root";
    return "api." + scope.replace(/^\//, "").replace(/\//g, "_").replace(/-/g, "_");
  }

  async function _fetch(path, options) {
    options = options || {};

    const method = options.method || "GET";
    const headers = options.headers || {};
    const body = Object.prototype.hasOwnProperty.call(options, "body") ? options.body : null;
    const timeout = Object.prototype.hasOwnProperty.call(options, "timeout")
      ? options.timeout
      : DEFAULT_TIMEOUT_MS;
    const includeResponseMeta = options.include_response_meta === true;

    const controller = new AbortController();
    const timeoutId = setTimeout(function () {
      controller.abort();
    }, timeout);
    const startedAt = performance.now();
    const retryCount = options.retry_count || 0;
    const scope = _apiScope(path);

    EwashLog.info(scope, {
      path: path,
      method: method,
      retry_count: retryCount,
    });

    try {
      const resp = await fetch(_base() + path, {
        method: method,
        headers: Object.assign(
          {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          headers
        ),
        body: body !== null ? JSON.stringify(body) : null,
        signal: controller.signal,
        mode: "cors",
      });
      const duration = _durationMs(startedAt);

      if (resp.status === 204 || resp.status === 304) {
        EwashLog.info(scope, {
          path: path,
          method: method,
          status: resp.status,
          duration_ms: +duration,
          retry_count: retryCount,
        });
        if (includeResponseMeta) {
          return { status: resp.status, headers: resp.headers, body: null };
        }
        return null;
      }

      if (!resp.ok) {
        const errBody = await _jsonErrorBody(resp);
        const err = new Error(errBody.message || errBody.detail || resp.statusText);
        err.error_code = errBody.error_code || "http_" + resp.status;
        err.status = resp.status;
        err.field = errBody.field || errBody.loc || null;
        _setRetryAfter(err, resp, errBody);
        _setDuration(err, duration);
        err._ewashLogged = true;
        const payload = {
          path: path,
          method: method,
          status: resp.status,
          duration_ms: +duration,
          error_code: err.error_code,
          retry_count: retryCount,
        };
        if (resp.status >= 500) EwashLog.fatal(scope, payload);
        else EwashLog.warn(scope, payload);
        throw err;
      }

      EwashLog.info(scope, {
        path: path,
        method: method,
        status: resp.status,
        duration_ms: +duration,
        retry_count: retryCount,
      });
      const data = await resp.json();
      if (includeResponseMeta) {
        return { status: resp.status, headers: resp.headers, body: data };
      }
      return data;
    } catch (err) {
      if (err && !err.error_code) {
        err.error_code = err.name === "AbortError" ? "timeout" : "network_error";
      }
      if (!err || !err._ewashLogged) {
        const failedDuration = _durationMs(startedAt);
        _setDuration(err, failedDuration);
        EwashLog.fatal(scope, {
          path: path,
          method: method,
          status: 0,
          duration_ms: +failedDuration,
          error_code: (err && err.error_code) || "network_error",
          retry_count: retryCount,
        });
      }
      throw err;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async function _fetchWithRetry(path, options, retryConfig) {
    const cfg = retryConfig || {};
    const retries = cfg.retries === undefined ? 2 : cfg.retries;
    const backoffMs = cfg.backoffMs === undefined ? 500 : cfg.backoffMs;

    let lastErr;
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        return await _fetch(path, Object.assign({}, options || {}, { retry_count: attempt }));
      } catch (err) {
        lastErr = err;
        // 4xx is deterministic — retrying changes nothing, so surface immediately.
        if (err && typeof err.status === "number" && err.status >= 400 && err.status < 500) {
          throw err;
        }
        // 5xx / network errors / aborts → backoff and retry while attempts remain.
        if (attempt < retries) {
          const delay = backoffMs * Math.pow(2, attempt);
          EwashLog.info("api.retry", {
            path: path,
            method: (options && options.method) || "GET",
            delay_ms: delay,
            retry_count: attempt + 1,
            error_code: (err && err.error_code) || (err && err.name) || "error",
          });
          await new Promise(function (resolve) {
            setTimeout(resolve, delay);
          });
        }
      }
    }
    throw lastErr;
  }

  function _getToken() {
    try {
      return localStorage.getItem(BOOKINGS_STORAGE_KEY) || "";
    } catch (_) {
      // Private mode, storage disabled — treat as "no token", caller decides.
      EwashLog.warn("localstorage.error", { op: "get", key: "bookings_token" });
      return "";
    }
  }

  function _saveToken(token) {
    if (!token) return;
    try {
      localStorage.setItem(BOOKINGS_STORAGE_KEY, token);
    } catch (_) {
      // Storage quota / private mode — best-effort persistence only.
      EwashLog.warn("localstorage.error", { op: "set", key: "bookings_token" });
    }
  }

  function _savePhone(phone) {
    if (!phone) return;
    try {
      localStorage.setItem(PHONE_STORAGE_KEY, phone);
    } catch (_) {
      // ditto.
      EwashLog.warn("localstorage.error", { op: "set", key: "phone" });
    }
  }

  function _saveName(name) {
    if (!name) return;
    try {
      localStorage.setItem(NAME_STORAGE_KEY, name);
    } catch (_) {
      EwashLog.warn("localstorage.error", { op: "set", key: "name" });
    }
  }

  function _bootstrapCacheStorageKey(params) {
    const category = params && params.category ? String(params.category).trim().toUpperCase() : "";
    const promo = params && params.promo ? String(params.promo).trim().toUpperCase() : "";
    return "category=" + category + "&promo=" + promo;
  }

  function _readBootstrapCacheEntries() {
    try {
      const raw = localStorage.getItem(BOOTSTRAP_CACHE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      if (!parsed || parsed.version !== BOOTSTRAP_CACHE_VERSION || typeof parsed.entries !== "object") {
        return {};
      }
      return parsed.entries || {};
    } catch (_) {
      EwashLog.warn("localstorage.error", { op: "get", key: "bootstrap_cache" });
      return {};
    }
  }

  function _writeBootstrapCacheEntries(entries) {
    try {
      localStorage.setItem(
        BOOTSTRAP_CACHE_KEY,
        JSON.stringify({
          version: BOOTSTRAP_CACHE_VERSION,
          saved_at: Date.now(),
          entries: entries || {},
        })
      );
    } catch (_) {
      EwashLog.warn("localstorage.error", { op: "set", key: "bootstrap_cache" });
    }
  }

  function _getBootstrapCacheEntry(cacheKey) {
    const entries = _readBootstrapCacheEntries();
    const entry = entries[cacheKey];
    const storedAt = entry ? Number(entry.stored_at) : 0;
    if (!entry || !entry.etag || !entry.body || !Number.isFinite(storedAt)) {
      return null;
    }
    if (Date.now() - storedAt > BOOTSTRAP_CACHE_TTL_MS) {
      delete entries[cacheKey];
      _writeBootstrapCacheEntries(entries);
      EwashLog.info("bootstrap.cache_expired", { cache_key: cacheKey });
      return null;
    }
    return entry;
  }

  function _saveBootstrapCacheEntry(cacheKey, etag, body) {
    if (!etag || !body) return;
    const entries = _readBootstrapCacheEntries();
    entries[cacheKey] = {
      etag: etag,
      body: body,
      stored_at: Date.now(),
    };
    _writeBootstrapCacheEntries(entries);
  }

  function _pad2(value) {
    return String(value).padStart(2, "0");
  }

  function _dateParts(dateIso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateIso || "");
    if (!match) return null;
    return {
      year: Number(match[1]),
      month: Number(match[2]),
      day: Number(match[3]),
    };
  }

  function _dateStamp(dateIso, hour) {
    const parts = _dateParts(dateIso);
    if (!parts) return "";
    const totalHours = Number(hour);
    if (!Number.isFinite(totalHours) || totalHours < 0) return "";
    const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
    date.setUTCDate(date.getUTCDate() + Math.floor(totalHours / 24));
    const localHour = totalHours % 24;
    return [
      date.getUTCFullYear(),
      _pad2(date.getUTCMonth() + 1),
      _pad2(date.getUTCDate()),
    ].join("") + "T" + _pad2(localHour) + "0000";
  }

  function _hourOrNull(value) {
    const hour = Number(value);
    if (!Number.isFinite(hour) || hour < 0) return null;
    return hour;
  }

  function _slotHours(booking) {
    let start = _hourOrNull(booking && booking.slot_start_hour);
    let end = _hourOrNull(booking && booking.slot_end_hour);
    if (start !== null && end !== null && end > start) return { start: start, end: end };

    const slotId = (booking && booking.slot_id) || "";
    let match = /^slot_(\d{1,2})_(\d{1,2})$/.exec(slotId);
    if (!match) {
      match = /(\d{1,2})(?::\d{2})?\s*[–-]\s*(\d{1,2})(?::\d{2})?/.exec((booking && booking.slot_label) || "");
    }
    if (match) {
      start = Number(match[1]);
      end = Number(match[2]);
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        return { start: start, end: end };
      }
    }
    if (start !== null) return { start: start, end: start + 2 };
    return null;
  }

  function _nowIcs() {
    return new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  }

  function _escapeIcs(value) {
    return String(value || "")
      .replace(/\\/g, "\\\\")
      .replace(/\r\n|\n|\r/g, "\\n")
      .replace(/;/g, "\\;")
      .replace(/,/g, "\\,");
  }

  function _foldIcsLine(line) {
    const folded = [];
    let rest = String(line);
    const firstLimit = 74;
    const nextLimit = 73;
    folded.push(rest.slice(0, firstLimit));
    rest = rest.slice(firstLimit);
    while (rest.length > 0) {
      folded.push(" " + rest.slice(0, nextLimit));
      rest = rest.slice(nextLimit);
    }
    return folded;
  }

  function _ics(lines) {
    return lines.reduce(function (acc, line) {
      return acc.concat(_foldIcsLine(line));
    }, []).join("\r\n") + "\r\n";
  }

  function _calendarStatus(status) {
    const confirmed = [
      "confirmed",
      "technician_en_route",
      "arrived",
      "in_progress",
      "completed",
      "completed_with_issue",
    ];
    return confirmed.includes(status) ? "CONFIRMED" : "TENTATIVE";
  }

  function _calendarDescription(booking) {
    return [
      "Ref: " + ((booking && booking.ref) || ""),
      "Véhicule: " + ((booking && booking.vehicle_label) || ""),
      "Total: " + ((booking && booking.total_price_dh) || 0) + " DH",
    ].join("\n");
  }

  function _calendarFilename(ref) {
    const safeRef = String(ref || "booking").replace(/[^A-Za-z0-9_-]/g, "-");
    return "ewash-" + safeRef + ".ics";
  }

  function canExportCalendar(booking) {
    return !!(booking && booking.date_iso && _slotHours(booking));
  }

  function generateIcs(booking) {
    const hours = _slotHours(booking);
    const dtStart = hours ? _dateStamp(booking.date_iso, hours.start) : "";
    const dtEnd = hours ? _dateStamp(booking.date_iso, hours.end) : "";
    if (!dtStart || !dtEnd) {
      const err = new Error("Booking is missing calendar date fields");
      err.error_code = "calendar_missing_date";
      throw err;
    }
    const ref = (booking && booking.ref) || "EWASH";
    const summary = "Ewash · " + ((booking && booking.service_label) || ref);
    const lines = [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//Ewash//Booking//FR",
      "CALSCALE:GREGORIAN",
      "METHOD:PUBLISH",
      "BEGIN:VEVENT",
      "UID:" + _escapeIcs(ref + "@ewash"),
      "DTSTAMP:" + _nowIcs(),
      "DTSTART;TZID=Africa/Casablanca:" + dtStart,
      "DTEND;TZID=Africa/Casablanca:" + dtEnd,
      "SUMMARY:" + _escapeIcs(summary),
      "LOCATION:" + _escapeIcs((booking && booking.location_label) || ""),
      "DESCRIPTION:" + _escapeIcs(_calendarDescription(booking)),
      "STATUS:" + _calendarStatus(booking && booking.status),
      "BEGIN:VALARM",
      "TRIGGER:-PT2H",
      "ACTION:DISPLAY",
      "DESCRIPTION:" + _escapeIcs("Préparez votre véhicule pour le lavage Ewash dans 2h"),
      "END:VALARM",
      "END:VEVENT",
      "END:VCALENDAR",
    ];
    return _ics(lines);
  }

  function downloadIcs(booking, locale) {
    const ics = generateIcs(booking);
    const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = _calendarFilename(booking && booking.ref);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () { URL.revokeObjectURL(url); }, 0);
    EwashLog.info("calendar.export", {
      ref: booking && booking.ref,
      status: booking && booking.status,
      locale: locale || "",
    });
  }

  async function getBootstrap(options) {
    const params = options || {};
    const qs = new URLSearchParams();
    if (params.category) qs.set("category", params.category);
    if (params.promo) qs.set("promo", params.promo);
    const query = qs.toString();
    const path = "/api/v1/bootstrap" + (query ? "?" + query : "");
    const cacheKey = _bootstrapCacheStorageKey(params);
    const cached = _getBootstrapCacheEntry(cacheKey);
    const headers = cached ? { "If-None-Match": cached.etag } : {};
    const response = await _fetchWithRetry(path, {
      headers: headers,
      include_response_meta: true,
    });

    if (response && response.status === 304 && cached) {
      EwashLog.info("bootstrap.cache_hit", { cache_key: cacheKey, status: 304 });
      return cached.body;
    }

    if (response && response.status === 200) {
      const etag = response.headers && response.headers.get ? response.headers.get("ETag") : "";
      _saveBootstrapCacheEntry(cacheKey, etag, response.body);
      EwashLog.info("bootstrap.cache_store", { cache_key: cacheKey, has_etag: !!etag });
      return response.body;
    }

    return response ? response.body : null;
  }

  async function validatePromo(params) {
    return await _fetch("/api/v1/promos/validate", {
      method: "POST",
      body: { code: params.code, category: params.category },
    });
  }

  async function submitBooking(payload) {
    // Auto-inject any existing bookings_token from localStorage so the server
    // can echo it back on a returning customer (and so a retry of the same
    // client_request_id remains idempotent on the same device).
    const existingToken = _getToken();
    const requestBody = Object.assign(
      {},
      payload,
      existingToken ? { bookings_token: existingToken } : {}
    );
    const phoneHash = await EwashLog.hash(payload && payload.phone);
    EwashLog.info("booking.confirm", {
      phone_hash: phoneHash,
      category: payload && payload.category,
      service: payload && payload.service_id,
      total_dh: payload && payload.total_dh,
      has_promo: !!(payload && payload.promo_code),
      addon_count: payload && payload.addon_ids ? payload.addon_ids.length : 0,
      client_request_id: payload && payload.client_request_id,
    });
    const response = await _fetch("/api/v1/bookings", {
      method: "POST",
      body: requestBody,
    });

    // Server always returns the canonical bookings_token (echoed when reused,
    // freshly minted on first contact). Persist it once so subsequent reads
    // on `getMyBookings` can authenticate.
    if (response && response.bookings_token) _saveToken(response.bookings_token);
    if (payload && payload.phone) _savePhone(payload.phone);
    if (payload && payload.name) _saveName(payload.name);

    if (response) {
      EwashLog.info("booking.confirmed", {
        ref: response.ref,
        total_dh: response.total_dh,
        token_changed: existingToken !== response.bookings_token,
        duration_ms: response.duration_ms,
        is_idempotent_replay: response.is_idempotent_replay === true,
      });
    }
    return response;
  }

  async function getMyBookings() {
    const token = _getToken();
    if (!token) {
      // Surface as a structured error so the Bookings tab can render an
      // "open a booking first to enable history" empty state instead of a
      // generic network failure.
      const err = new Error("No bookings_token in localStorage");
      err.error_code = "no_local_token";
      throw err;
    }
    return await _fetchWithRetry("/api/v1/bookings", {
      headers: { "X-Ewash-Token": token },
    });
  }

  async function getMyVehicles() {
    // Returns past vehicles for the token bearer so the booking flow can
    // offer one-tap re-selection. Returns null (not an error) for first-
    // time users with no token — caller treats null as "no history, show
    // the manual flow" rather than rendering an error.
    const token = _getToken();
    if (!token) return null;
    try {
      return await _fetchWithRetry("/api/v1/me/vehicles", {
        headers: { "X-Ewash-Token": token },
      });
    } catch (err) {
      // Token might have been revoked server-side or be from a previous
      // dev environment. Don't escalate — fall back to the manual flow.
      if (err && (err.status === 401 || err.error_code === "invalid_token")) {
        EwashLog.info("me.vehicles.invalid_token_silent", {});
        return null;
      }
      throw err;
    }
  }

  async function revokeToken(params) {
    const token = _getToken();
    if (!token) {
      const err = new Error("No bookings_token in localStorage");
      err.error_code = "no_local_token";
      throw err;
    }
    const scope = params && params.scope ? params.scope : "current";
    const response = await _fetch("/api/v1/tokens/revoke", {
      method: "POST",
      headers: { "X-Ewash-Token": token },
      body: { scope: scope },
    });
    if (response && response.new_token) _saveToken(response.new_token);
    return response;
  }

  async function deleteMe(params) {
    const token = _getToken();
    if (!token) {
      const err = new Error("No bookings_token in localStorage");
      err.error_code = "no_local_token";
      throw err;
    }
    return await _fetch("/api/v1/me", {
      method: "DELETE",
      headers: { "X-Ewash-Token": token },
      body: { confirm: params && params.confirm ? params.confirm : "" },
    });
  }

  window.EwashCalendar = {
    canExport: canExportCalendar,
    generateIcs: generateIcs,
    download: downloadIcs,
  };

  window.EwashAPI = {
    _fetch: _fetch,
    _TOKEN_KEY: BOOKINGS_STORAGE_KEY,
    _PHONE_KEY: PHONE_STORAGE_KEY,
    _NAME_KEY: NAME_STORAGE_KEY,
    _BOOTSTRAP_CACHE_KEY: BOOTSTRAP_CACHE_KEY,
    _getToken: _getToken,
    getBootstrap: getBootstrap,
    validatePromo: validatePromo,
    submitBooking: submitBooking,
    getMyBookings: getMyBookings,
    getMyVehicles: getMyVehicles,
    revokeToken: revokeToken,
    deleteMe: deleteMe,
  };
})();
