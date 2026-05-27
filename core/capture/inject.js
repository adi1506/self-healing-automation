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
      aria_required: el.getAttribute("aria-required") || "",
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
    // For SELECT elements, capture the visible option list so the recording
    // editor can render a real dropdown (not just a free-text input). Each
    // entry is {value, label} — `value` is what the form submits, `label` is
    // what the user sees. Older recordings lack this key; the editor falls
    // back to a text input when it's missing.
    if (el.tagName === "SELECT") {
      try {
        attrs.select_options = Array.from(el.options).map(function (o) {
          return { value: o.value, label: (o.text || "").trim() };
        });
      } catch (_) {
        attrs.select_options = [];
      }
    }
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

  // --- Hover-chain detector ---------------------------------------
  // Reconstructs the hover events that preceded a click, by tracking which
  // DOM elements appeared post-arm and what the cursor was over at each
  // insertion. Filters out click-driven insertions (so click-toggled menus
  // don't generate phantom hovers) and walks the chain recursively so
  // cascading menus produce one hover step per level.
  //
  // Design + measured false-positive numbers: see tests/hover_prototype.py
  // and dogfood-output/hover_proto_results.md.
  const _hover = {
    armed: false,
    insertion_ts: new WeakMap(),  // element -> ms-since-arm; 0 means preexisting
    click_driven: new WeakMap(),  // element -> true if inserted within CLICK_ATTRIBUTION_MS of a click
    mutations: [],                // {ts, node, cursor_el}
    cursor_el: null,
    last_click_ts: 0,
    // Tunables — same constants validated by the prototype battery
    CLICK_ATTRIBUTION_MS: 250,
    INSERTION_WINDOW_MS: 2000,
  };

  const _INTERACTIVE_TAGS = new Set(["button", "a", "input", "select", "textarea"]);
  const _INTERACTIVE_ROLES = new Set([
    "button", "link", "menuitem", "menuitemcheckbox", "menuitemradio",
    "tab", "checkbox", "radio", "option", "switch",
    // combobox: Ant/MUI/HeadlessUI <Select> triggers are <div role=combobox>.
    // Without this the click that OPENS the dropdown is silently dropped
    // and the subsequent option click happens on a popup the replay
    // engine has no way to make appear. treeitem covers custom tree
    // controls that follow the same pattern.
    "combobox", "treeitem",
  ]);

  function _isInteractive(el) {
    if (!el || el.nodeType !== 1) return false;
    if (_INTERACTIVE_TAGS.has(el.tagName.toLowerCase())) return true;
    const role = (el.getAttribute && el.getAttribute("role") || "").toLowerCase();
    if (_INTERACTIVE_ROLES.has(role)) return true;
    if (el.hasAttribute && el.hasAttribute("onclick")) return true;
    return false;
  }

  function _nearestInteractive(el) {
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body) {
      if (_isInteractive(cur)) return cur;
      cur = cur.parentNode;
    }
    return el;
  }

  // Same walk as _nearestInteractive but returns null when no interactive
  // ancestor exists. Used by the click capture filter — we don't want to
  // record clicks on bare divs/spans, but the legacy filter (button,a,
  // [role=button],input[type=submit|button]) misses Ant Design Select
  // triggers (role=combobox), options (role=option), tabs, custom
  // checkboxes, etc. Using the same role set as _isInteractive keeps the
  // hover-chain and click-capture views of "what's interactive" aligned.
  function _nearestInteractiveOrNull(el) {
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body) {
      if (_isInteractive(cur)) return cur;
      cur = cur.parentNode;
    }
    return null;
  }

  // Input types that ARE clickable buttons, not form-value fields. A click on
  // these is a real user intent and must be recorded. Everything else under
  // `<input>` (text, email, tel, number, date, color, range, checkbox, radio,
  // password, search, url, time, ...) is a value field whose click is just
  // focus and is followed by a `change` event recorded as fill/check/uncheck.
  const _INPUT_BUTTON_TYPES = new Set(["submit", "button", "reset", "image", "file"]);

  function _isFormValueTarget(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName;
    if (tag === "TEXTAREA" || tag === "SELECT") return true;
    if (tag === "INPUT") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      return !_INPUT_BUTTON_TYPES.has(t);
    }
    return false;
  }

  function _armHoverDetector() {
    if (_hover.armed) return;
    if (!document.body) return;
    _hover.armed = true;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, null);
    let el = walker.currentNode;
    let count = 0;
    while (el) {
      _hover.insertion_ts.set(el, 0);  // 0 = preexisting at arm time
      count++;
      if (count > 20000) break;
      el = walker.nextNode();
    }
    // Cursor: precise updates on every element boundary (mouseenter); fallback mousemove.
    document.addEventListener("mouseenter", (e) => {
      if (e.target && e.target.nodeType === 1) _hover.cursor_el = e.target;
    }, true);
    document.addEventListener("mousemove", (e) => {
      const t = document.elementFromPoint(e.clientX, e.clientY);
      if (t) _hover.cursor_el = t;
    }, true);
    // Mutation observer: stamp insertion_ts; tag as click_driven if recent click
    const mo = new MutationObserver((records) => {
      const now = performance.now();
      const click_driven = _hover.last_click_ts > 0
        && (now - _hover.last_click_ts) <= _hover.CLICK_ATTRIBUTION_MS;
      for (const r of records) {
        if (r.type !== "childList") continue;
        for (const node of r.addedNodes) {
          if (node.nodeType !== 1) continue;
          const w = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT, null);
          let n = w.currentNode;
          let c = 0;
          while (n) {
            _hover.insertion_ts.set(n, now);
            if (click_driven) _hover.click_driven.set(n, true);
            c++;
            if (c > 500) break;
            n = w.nextNode();
          }
          _hover.mutations.push({ ts: now, node, cursor_el: _hover.cursor_el });
        }
      }
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  // Returns an array of trigger ELEMENTS that need to be hovered before the click,
  // outermost-first. Empty array = no hover needed.
  function _detectHoverChain(clickTarget) {
    if (!_hover.armed) return [];
    const now = performance.now();
    const chain = [];
    const seen = new Set();
    let current = _nearestInteractive(clickTarget);

    while (current && !seen.has(current)) {
      seen.add(current);
      const inserted_at = _hover.insertion_ts.get(current);
      if (inserted_at === undefined) break;          // untracked
      if (inserted_at === 0) break;                  // preexisting at arm time
      if ((now - inserted_at) > _hover.INSERTION_WINDOW_MS) break;
      if (_hover.click_driven.get(current) === true) break;

      // Find the mutation that introduced `current`
      let m = null;
      for (let i = _hover.mutations.length - 1; i >= 0; i--) {
        const mm = _hover.mutations[i];
        if (mm.node === current || (mm.node && mm.node.contains && mm.node.contains(current))) {
          m = mm; break;
        }
      }
      if (!m || !m.cursor_el) break;
      const trigger = _nearestInteractive(m.cursor_el);
      // Avoid emitting a hover whose trigger is the same as click target (degenerate)
      if (trigger === clickTarget) break;
      chain.unshift(trigger);
      current = trigger;
    }
    return chain;
  }

  function attachListeners() {
    if (window.__sha._attached) return;
    window.__sha._attached = true;
    _armHoverDetector();

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
      // Stamp click time IMMEDIATELY (before any downstream insertion) so the
      // MutationObserver can tag subsequent additions as click-driven and the
      // next click won't misattribute them to a hover.
      _hover.last_click_ts = performance.now();

      const el = _nearestInteractiveOrNull(ev.target);
      if (!el) return;

      // Reconstruct hover chain that preceded this click. Emit outermost
      // hover first, then proceed to the click. Filter: skip empty chains
      // (most clicks), skip self-referential triggers, dedupe consecutive.
      try {
        const chain = _detectHoverChain(el);
        const seenPaths = new Set();
        for (const trigger of chain) {
          if (!trigger || trigger === el) continue;
          // Dedupe by element identity to avoid double-emitting if the same
          // trigger appears twice in a degenerate chain.
          if (seenPaths.has(trigger)) continue;
          seenPaths.add(trigger);
          emit("hover", trigger, null);
        }
      } catch (e) {
        // Hover detection must never break click recording. Swallow & continue.
      }

      window.__sha._lastClickForm = el.closest ? el.closest("form") : null;
      window.__sha._lastClickAt = Date.now();

      // Suppress clicks on form-value targets. These elements (`<input>`
      // except submit/button/file/image/reset, `<textarea>`, `<select>`)
      // emit a `change` event when the user enters/picks a value, which
      // the recorder already captures as fill/select/check/uncheck on the
      // same element. The leading click is functionally just focus, and
      // recording it breaks heal on schema changes: the healer's
      // `is_action_compatible(action="click")` rejects selects and text
      // inputs as click targets, so a stray click step on those elements
      // can never be relocated when the page is refactored.
      // Real buttons, links, and `<input type=submit|button>` are still
      // recorded — those are real user intents, not focus side-effects.
      if (_isFormValueTarget(el)) return;

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
        if (fp) {
          // Annotate with is_required so callers (record-time capture,
          // replay-time schema diff) can tell required from optional
          // without re-walking the DOM.
          try {
            fp.attributes.is_required = isFieldRequired(el);
          } catch (_) {
            fp.attributes.is_required = false;
          }
          out.push(fp);
        }
      } catch (_) {
        // Skip elements that throw during fingerprinting — we'd rather
        // return a partial scan than fail the whole heal attempt.
      }
    }
    return out;
  }

  function isFieldRequired(el) {
    if (el.hasAttribute("required")) return true;
    if ((el.getAttribute("aria-required") || "").toLowerCase() === "true") return true;
    // Check for asterisk in nearest label
    const labelText = nearestLabelText(el);
    if (labelText && labelText.includes("*")) return true;
    // Class-name heuristic on the field or its container
    const className = (el.className || "") + " " +
      (el.closest(".form-group, .field, .form-field")?.className || "");
    if (/\b(required|mandatory|is-required)\b/i.test(className)) return true;
    // data-* attribute hints
    for (const attr of el.attributes) {
      if (/^data-/i.test(attr.name) && /required|mandatory|validation/i.test(attr.name + " " + attr.value)) {
        return true;
      }
    }
    return false;
  }

  function scanRequiredFields() {
    const selector = "input, select, textarea, [role=textbox], [role=combobox], [role=checkbox], [role=radio]";
    const out = [];
    document.querySelectorAll(selector).forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return; // invisible
      if (el.disabled) return;
      if (!isFieldRequired(el)) return;
      const fp = buildFingerprint(el);
      if (!fp) return;
      fp.is_empty = (el.value || "").trim() === "" &&
        !(el.tagName === "SELECT" && el.selectedIndex > 0) &&
        !(el.type === "checkbox" && el.checked);
      out.push(fp);
    });
    return out;
  }

  function scanPostSubmitErrors() {
    const errorSelectors = [
      "[role=alert]",
      "[aria-invalid=true]",
      ".error-message", ".field-error", ".form-error",
      "[id$='-error']", "[class*='error']", "[class*='invalid']",
    ];
    const errMsgRe = /required|mandatory|cannot be (empty|blank)|please (enter|select|fill|provide)/i;
    const out = [];
    document.querySelectorAll(errorSelectors.join(",")).forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      const text = (el.textContent || "").trim();
      if (!text) return;
      if (!errMsgRe.test(text)) return;
      // Try to associate with a field via aria-describedby / for / proximity
      let assoc = null;
      if (el.id) {
        assoc = document.querySelector(`[aria-describedby~='${el.id}']`);
      }
      if (!assoc && el.previousElementSibling && /^(input|select|textarea)$/i.test(el.previousElementSibling.tagName)) {
        assoc = el.previousElementSibling;
      }
      out.push({
        error_text: text,
        associated_field: assoc ? buildFingerprint(assoc) : null,
      });
    });
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
    scanRequiredFields,
    scanPostSubmitErrors,
  };

  // Auto-attach once the DOM is ready. Python can also call
  // window.__sha.attachListeners() explicitly after exposing __sha_record.
  if (document.readyState !== "loading") attachListeners();
  else document.addEventListener("DOMContentLoaded", attachListeners);
})();
