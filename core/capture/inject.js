// Capture engine — injected into every page of a recording context via
// playwright.Page.add_init_script(). Exposes window.__sha which Python
// drives via page.evaluate(). When __sha_record(payload) exists on
// window (Playwright exposes it via page.expose_function), interaction
// events stream to it.
(function () {
  if (window.__sha) return;

  // --- ID assignment + dedup ----------------------------------------
  // Same physical element -> same id across every event. Dedup key is
  // (xpath || css path) + a short hash of nearby siblings' tag+id+name.
  const idCache = new WeakMap();
  let idSeq = 0;
  function elementId(el) {
    let id = idCache.get(el);
    if (id) return id;
    const sig = xpathOf(el) + "|" + neighborhoodSignature(el);
    // First time we encounter this signature, mint a new id.
    if (!window.__sha._sigToId.has(sig)) {
      idSeq += 1;
      window.__sha._sigToId.set(sig, "el-" + idSeq);
    }
    id = window.__sha._sigToId.get(sig);
    idCache.set(el, id);
    return id;
  }

  // --- Locator strategies ------------------------------------------
  function pickPrimaryLocator(el) {
    if (el.id) return { strategy: "id", value: el.id };
    const dtid = el.getAttribute("data-testid");
    if (dtid) return { strategy: "data-testid", value: dtid };
    const name = el.getAttribute("name");
    if (name) return { strategy: "name", value: name };
    return { strategy: "css", value: cssPathOf(el) };
  }

  function fallbackLocators(el) {
    const out = [];
    if (el.id) out.push({ strategy: "id", value: el.id });
    const dtid = el.getAttribute("data-testid");
    if (dtid) out.push({ strategy: "data-testid", value: dtid });
    const name = el.getAttribute("name");
    if (name) out.push({ strategy: "name", value: name });
    out.push({ strategy: "css", value: cssPathOf(el) });
    out.push({ strategy: "xpath", value: xpathOf(el) });
    // Dedup against primary
    const primary = pickPrimaryLocator(el);
    return out.filter((x) => !(x.strategy === primary.strategy && x.value === primary.value));
  }

  // --- Path helpers -------------------------------------------------
  function cssPathOf(el) {
    if (!(el instanceof Element)) return "";
    const path = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      let part = cur.nodeName.toLowerCase();
      if (cur.id) {
        part += "#" + CSS.escape(cur.id);
        path.unshift(part);
        break;
      } else {
        let n = 1, sib = cur.previousElementSibling;
        while (sib) {
          if (sib.nodeName === cur.nodeName) n += 1;
          sib = sib.previousElementSibling;
        }
        part += ":nth-of-type(" + n + ")";
      }
      path.unshift(part);
      cur = cur.parentElement;
    }
    return path.join(" > ");
  }

  function xpathOf(el) {
    if (!(el instanceof Element)) return "";
    if (el.id) return "//*[@id='" + el.id + "']";
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
      let n = 1, sib = cur.previousElementSibling;
      while (sib) {
        if (sib.nodeName === cur.nodeName) n += 1;
        sib = sib.previousElementSibling;
      }
      parts.unshift(cur.nodeName.toLowerCase() + "[" + n + "]");
      cur = cur.parentElement;
    }
    return "/" + parts.join("/");
  }

  function neighborhoodSignature(el) {
    const parts = [];
    let sib = el.previousElementSibling;
    for (let i = 0; i < 3 && sib; i++) {
      parts.push(sib.nodeName.toLowerCase() + "[" + (sib.id || sib.getAttribute("name") || "") + "]");
      sib = sib.previousElementSibling;
    }
    sib = el.nextElementSibling;
    for (let i = 0; i < 3 && sib; i++) {
      parts.push(sib.nodeName.toLowerCase() + "[" + (sib.id || sib.getAttribute("name") || "") + "]");
      sib = sib.nextElementSibling;
    }
    return parts.join("|");
  }

  // --- Label / context discovery ------------------------------------
  function nearestLabelText(el) {
    if (el.id) {
      const lbl = document.querySelector("label[for='" + CSS.escape(el.id) + "']");
      if (lbl) return (lbl.textContent || "").trim();
    }
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++) {
      if (p.tagName === "LABEL") return (p.textContent || "").trim();
      p = p.parentElement;
    }
    return "";
  }

  function nearestLandmarkText(el) {
    let p = el.parentElement;
    while (p) {
      if (p.matches && p.matches("fieldset, section, [role=group], h1,h2,h3,h4,h5,h6")) {
        const legend = p.querySelector ? p.querySelector("legend, h1,h2,h3,h4,h5,h6") : null;
        return ((legend || p).textContent || "").trim().slice(0, 80);
      }
      p = p.parentElement;
    }
    return "";
  }

  // --- Fingerprint construction -------------------------------------
  function buildFingerprint(el) {
    if (!el) return null;
    const id = elementId(el);
    const rect = el.getBoundingClientRect();
    const attrs = {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute("type") || "",
      id: el.id || "",
      name: el.getAttribute("name") || "",
      class: el.getAttribute("class") || "",
      placeholder: el.getAttribute("placeholder") || "",
      aria_label: el.getAttribute("aria-label") || "",
      role: el.getAttribute("role") || "",
      text_content: (el.textContent || "").trim().slice(0, 80),
      nearest_label_text: nearestLabelText(el),
      nearest_landmark_text: nearestLandmarkText(el),
      bbox: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
      html5_constraints: {
        pattern: el.getAttribute("pattern") || "",
        required: el.hasAttribute("required"),
        maxlength: el.getAttribute("maxlength") || "",
        minlength: el.getAttribute("minlength") || "",
        min: el.getAttribute("min") || "",
        max: el.getAttribute("max") || "",
      },
      autocomplete: el.getAttribute("autocomplete") || "",
      xpath: xpathOf(el),
      css_path: cssPathOf(el),
      neighborhood_signature: neighborhoodSignature(el),
    };
    return {
      id,
      primary_locator: pickPrimaryLocator(el),
      fallback_locators: fallbackLocators(el),
      attributes: attrs,
      page_context: {
        url: location.href,
        section_label: nearestLandmarkText(el),
      },
    };
  }

  // --- Event handling -----------------------------------------------
  function emit(action, el, value) {
    if (!window.__sha_record) return;
    const fp = buildFingerprint(el);
    window.__sha_record({
      action,
      element: fp,
      value: value == null ? null : String(value),
      timestamp_ms: Date.now() - window.__sha._startTs,
      url: location.href,
    });
  }

  function attachListeners() {
    if (window.__sha._attached) return;
    window.__sha._attached = true;

    document.addEventListener("change", (ev) => {
      const el = ev.target;
      if (!(el instanceof HTMLElement)) return;
      const tag = el.tagName;
      if (tag === "INPUT") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        if (type === "checkbox") return emit(el.checked ? "check" : "uncheck", el, null);
        if (type === "radio") return emit("check", el, el.value);
        return emit("fill", el, el.value);
      }
      if (tag === "TEXTAREA") return emit("fill", el, el.value);
      if (tag === "SELECT") return emit("select", el, el.value);
    }, true);

    document.addEventListener("click", (ev) => {
      const el = ev.target.closest && ev.target.closest("button, a, [role=button], input[type=submit], input[type=button]");
      if (!el) return;
      window.__sha._lastClickForm = el.closest ? el.closest("form") : null;
      window.__sha._lastClickAt = Date.now();
      emit("click", el, null);
    }, true);

    window.addEventListener("submit", (ev) => {
      const form = ev.target;
      if (!form || form.tagName !== "FORM") return;
      // Suppress submit events that immediately follow a click on a descendant of
      // this same form — the click step alone replays the user's intent and will
      // trigger the form submission naturally. Without this, replay tries to
      // re-find the form after navigation and raises ElementNotFound.
      // Enter-key and programmatic submits (no preceding in-form click) still
      // emit, because _lastClickForm won't match.
      if (window.__sha._lastClickForm === form && (Date.now() - window.__sha._lastClickAt) < 500) return;
      emit("submit", form, null);
    }, true);
  }

  // --- Live-page scanning (for replay-time healing) -----------------
  // Enumerates every interactive element currently on the page and returns
  // a fingerprint for each. Used by the replay healer when a stored
  // fingerprint's locators all miss: candidates are scored against the
  // stored fingerprint's attributes and the best match wins.
  //
  // No state mutation, no listener attachment, no side effects. Safe to
  // call at any point during a replay run, including mid-flow on a
  // stateful page.
  const INTERACTIVE_SELECTOR = [
    "input:not([type=hidden])",
    "select",
    "textarea",
    "button",
    "a[href]",
    "[role=button]",
    "[role=textbox]",
    "[role=combobox]",
    "[role=checkbox]",
    "[role=radio]",
    "[role=link]",
  ].join(", ");

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (style && (style.display === "none" || style.visibility === "hidden")) return false;
    return true;
  }

  function scanAll() {
    const out = [];
    const seen = new Set();
    const nodes = document.querySelectorAll(INTERACTIVE_SELECTOR);
    for (const el of nodes) {
      if (!(el instanceof HTMLElement)) continue;
      if (!isVisible(el)) continue;
      if (seen.has(el)) continue;
      seen.add(el);
      try {
        const fp = buildFingerprint(el);
        if (fp) out.push(fp);
      } catch (_) {
        // Skip elements that throw during fingerprinting — we'd rather
        // return a partial scan than fail the whole heal attempt.
      }
    }
    return out;
  }

  window.__sha = {
    _sigToId: new Map(),
    _attached: false,
    _startTs: Date.now(),
    _lastClickForm: null,
    _lastClickAt: 0,
    buildFingerprint,
    attachListeners,
    scanAll,
  };

  // Auto-attach once the DOM is ready. Python can also call
  // window.__sha.attachListeners() explicitly after exposing __sha_record.
  if (document.readyState !== "loading") attachListeners();
  else document.addEventListener("DOMContentLoaded", attachListeners);
})();
