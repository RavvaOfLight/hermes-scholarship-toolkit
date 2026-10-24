// In-page half of the companion. Injected only when you press a button in the side panel.
// It reads the form's boxes, lets you point at or circle them, shows numbered tags, and
// types the values the panel sends. It never clicks buttons or links and never submits; the
// panel also arms a page-level submit blocker while it fills. The only presses it makes are on
// a dropdown you asked it to fill: opening it, choosing the matching option, and Escape to
// close it again if nothing matched.
(() => {
  const VERSION = 2;
  if (window.__scholarshipAutofill?.version === VERSION && window.__scholarshipAutofill.alive()) return;
  document.querySelectorAll("[data-sa-companion]").forEach((n) => n.remove()); // leftovers from an older copy

  const registry = new Map(); // ref -> {el, kind, options?: [el], frames: [iframe...], labelEl}
  const refOf = new WeakMap();
  let refSeq = 0;
  let lastScan = null;
  let dirty = true;
  let pickState = null;
  let tagRows = [];
  const batches = new Map();
  let guardLog = []; // submits the page tried while the blocker was on (the panel reads and clears it)
  document.addEventListener("sa-guard-blocked", (e) => guardLog.push(String(e.detail || "submit")));
  const alive = () => { try { return !!chrome.runtime?.id; } catch (e) { return false; } };
  window.__scholarshipAutofill = { version: VERSION, alive };

  // ---------------------------------------------------------------- overlay (closed shadow root)
  const host = document.createElement("div");
  host.setAttribute("data-sa-companion", "");
  host.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:none;contain:strict;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    :host { all: initial; }
    * { box-sizing: border-box; font-family: "Segoe UI", system-ui, sans-serif; }
    .catcher { position: fixed; inset: 0; pointer-events: auto; cursor: crosshair; background: rgba(15, 23, 42, .04); display: none; touch-action: none; }
    .catcher.on { display: block; }
    .ring { position: fixed; border-radius: 7px; pointer-events: none; transition: all .08s ease-out; }
    .hover { border: 2px solid #2563eb; background: rgba(37, 99, 235, .10); box-shadow: 0 0 0 4px rgba(37, 99, 235, .18); display: none; }
    .tip { position: fixed; pointer-events: none; background: #0f172a; color: #fff; font-size: 12px; line-height: 1.3; padding: 5px 8px; border-radius: 7px; max-width: 320px; display: none; box-shadow: 0 4px 14px rgba(0,0,0,.25); }
    .tip b { color: #93c5fd; font-weight: 600; }
    .banner { position: fixed; top: 10px; left: 50%; transform: translateX(-50%); background: #0f172a; color: #fff; font-size: 13px; padding: 8px 10px 8px 14px; border-radius: 999px; display: none; align-items: center; gap: 10px; pointer-events: auto; box-shadow: 0 6px 20px rgba(0,0,0,.28); }
    .banner.on { display: flex; }
    .banner button { all: unset; cursor: pointer; background: #334155; color: #fff; font-size: 12px; padding: 4px 10px; border-radius: 999px; }
    .banner button:hover { background: #475569; }
    svg { position: fixed; inset: 0; width: 100vw; height: 100vh; pointer-events: none; }
    path { fill: rgba(37, 99, 235, .10); stroke: #2563eb; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; stroke-dasharray: 7 5; }
    .tag { position: fixed; pointer-events: none; font-size: 11.5px; font-weight: 600; line-height: 1; padding: 4px 7px; border-radius: 999px; color: #fff; white-space: nowrap; max-width: 260px; overflow: hidden; text-overflow: ellipsis; box-shadow: 0 2px 8px rgba(0,0,0,.2); }
    .box { position: fixed; pointer-events: none; border-radius: 6px; border: 2px solid; }
    .t-ready, .t-chosen { background: #2563eb; } .b-ready, .b-chosen { border-color: #2563eb; background: rgba(37,99,235,.06); }
    .t-check, .t-has_value, .t-choose { background: #b45309; } .b-check, .b-has_value, .b-choose { border-color: #d97706; border-style: dashed; }
    .t-filled { background: #15803d; } .b-filled { border-color: #16a34a; background: rgba(22,163,74,.07); }
    .t-failed { background: #b91c1c; } .b-failed { border-color: #dc2626; }
    .t-blocked, .t-none, .t-missing, .t-done { background: #475569; } .b-blocked, .b-none, .b-missing, .b-done { border-color: #94a3b8; border-style: dotted; }
    .live { border: 2px solid #2563eb; background: rgba(37, 99, 235, .12); }
    .flash { animation: flash 1.1s ease-out 1; }
    @keyframes flash { 0% { box-shadow: 0 0 0 0 rgba(37,99,235,.6); } 100% { box-shadow: 0 0 0 14px rgba(37,99,235,0); } }
  </style>
  <div class="catcher"></div>
  <svg><path d=""></path></svg>
  <div class="layer"></div>
  <div class="ring hover"></div>
  <div class="tip"></div>
  <div class="banner"><span class="msg"></span><button type="button">Stop</button></div>`;
  (document.documentElement || document.body).appendChild(host);
  const $ = (s) => root.querySelector(s);
  const catcher = $(".catcher"), hoverRing = $(".hover"), tip = $(".tip"), banner = $(".banner"), layer = $(".layer"), lasso = $("path");
  $(".banner button").addEventListener("click", (e) => { e.stopPropagation(); stopPick(true); });

  // ---------------------------------------------------------------- reading the page
  const SKIP_TYPES = new Set(["hidden", "submit", "reset", "button", "image", "search", "color", "range", "time", "week", "datetime-local"]);
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();

  function textOf(node, limit = 220) {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) return clean(node.textContent).slice(0, limit);
    if (node.nodeType !== Node.ELEMENT_NODE || node === host) return "";
    if ((node.textContent || "").length > 3000) return "";
    const tag = node.tagName;
    if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG"].includes(tag.toUpperCase())) return "";
    if (["INPUT", "SELECT", "TEXTAREA", "BUTTON", "OPTION"].includes(tag)) return "";
    let out = "";
    for (const child of node.childNodes) {
      const t = textOf(child, limit);
      if (t) out += (out ? " " : "") + t;
      if (out.length > limit) break;
    }
    return clean(out).slice(0, limit);
  }

  const hasControl = (node) => node.nodeType === Node.ELEMENT_NODE && (node.matches("input:not([type=hidden]),select,textarea,[role=radio],[role=checkbox],[role=textbox],[role=combobox]") || !!node.querySelector("input:not([type=hidden]),select,textarea,[role=radio],[role=checkbox],[role=textbox],[role=combobox]"));

  function byIds(el, attr) {
    const ids = (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean);
    const doc = el.getRootNode();
    return clean(ids.map((id) => (doc.getElementById ? doc.getElementById(id) : el.ownerDocument.getElementById(id))).filter((n) => n && n !== el).map((n) => textOf(n)).join(" "));
  }

  // Text right before the control in its row: "Name: [box]", a label cell, or the question above a group.
  function nearestText(el) {
    let node = el;
    for (let depth = 0; depth < 4 && node && node.parentElement; depth++) {
      let sib = node.previousSibling, hops = 0;
      while (sib && hops < 4) {
        if (sib.nodeType === Node.ELEMENT_NODE && hasControl(sib)) return "";
        const t = textOf(sib, 160);
        if (t && t.length > 1) return t.length <= 150 ? t : "";
        sib = sib.previousSibling; hops++;
      }
      node = node.parentElement;
      if (["FORM", "BODY", "FIELDSET"].includes(node.tagName)) break;
    }
    return "";
  }

  function visible(el) {
    if (!el.isConnected) return false;
    if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width >= 3 && r.height >= 3;
  }

  function optionLabel(el) {
    const own = byIds(el, "aria-labelledby") || clean(el.getAttribute("aria-label"));
    if (own) return own;
    const labels = el.labels ? [...el.labels].map((l) => textOf(l)).filter(Boolean) : [];
    if (labels.length) return labels[0];
    const next = el.nextSibling && textOf(el.nextSibling, 120);
    return next || clean(el.value) || textOf(el, 120);
  }

  function nameHint(el) {
    return clean(`${el.getAttribute("name") || ""} ${el.id || ""}`.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[_\-.[\]]+/g, " ").replace(/\d+/g, (d) => ` ${d} `));
  }

  // ---------------------------------------------------------------- custom dropdowns
  const norm = (text) => String(text ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/&/g, " and ")
    .replace(/['‘’`]/g, "").replace(/[^a-z0-9]+/g, " ").trim(); // the same as matcher.js normalize()
  // "Select…", "Select a state", "Choose one", "-- Pick --": a prompt, not a choice
  const PLACEHOLDER = /^(?:(?:please )?(?:select|choose|pick)(?: (?:a|an|one|your|the|from))?(?: [a-z]+){0,3}|none selected|nothing selected|)$/;
  const PROXIES = [
    (s) => s.nextElementSibling?.matches?.(".select2, .select2-container") && s.nextElementSibling,
    (s) => (s.id && s.ownerDocument.getElementById(`${s.id}_chosen`)) || (s.nextElementSibling?.matches?.(".chosen-container") && s.nextElementSibling),
    (s) => s.parentElement?.matches?.(".bootstrap-select") && s.parentElement.querySelector(".dropdown-toggle"),
    (s) => s.nextElementSibling?.matches?.(".selectize-control, .ts-wrapper, .nice-select, .ss-main") && s.nextElementSibling,
    (s) => s.closest?.(".choices")?.querySelector(".choices__inner"),
  ];
  function proxyFor(select) {
    for (const find of PROXIES) { const p = find(select); if (p) return p; }
    return null;
  }
  function comboGeo(input) {
    let n = input;
    for (let d = 0; n && d < 5; d++, n = n.parentElement) {
      const r = n.getBoundingClientRect();
      if (r.width >= 40 && r.height >= 16) return n;
    }
    return null;
  }
  function comboLabel(el) {
    return byIds(el, "aria-labelledby") || clean(el.getAttribute("aria-label")) || (el.labels && [...el.labels].map((l) => textOf(l)).filter(Boolean)[0]) || nearestText(el);
  }
  function optionText(o) { return clean(o.getAttribute("aria-label")) || clean(o.textContent).slice(0, 120); }
  // What a custom dropdown shows as chosen right now ("" for a placeholder like "Select…").
  function comboText(entry) {
    const { el } = entry;
    const sel = el.getAttribute?.("role") === "listbox" && el.querySelector("[role=option][aria-selected=true]");
    let text = "";
    if (sel) text = optionText(sel);
    else {
      const host = entry.geo || el;
      const copy = host.cloneNode(true);
      copy.querySelectorAll("[role=listbox], [role=option], input, select, [class*=placeholder], [class*=Placeholder], [aria-hidden=true], .chosen-drop, .dropdown-menu").forEach((n) => n.remove());
      text = clean(copy.textContent);
      if (entry.typeahead && el.value) text = clean(`${text} ${el.value}`);
    }
    return PLACEHOLDER.test(norm(text)) || /^\s*-{2,}/.test(text) ? "" : text.slice(0, 160);
  }
  function proxyShows(proxy, optionTextWanted) {
    const copy = proxy.cloneNode(true);
    copy.querySelectorAll("[role=listbox], ul, .chosen-drop, .dropdown-menu, .select2-dropdown, .ts-dropdown, .selectize-dropdown, .list").forEach((n) => n.remove());
    const shown = norm(copy.textContent), want = norm(optionTextWanted);
    return want !== "" && (shown === want || ` ${shown} `.includes(` ${want} `));
  }

  // Walk the page in order, through open shadow roots and same-origin frames, keeping a heading outline.
  function scan() {
    registry.clear();
    const fields = [], frames = [];
    const outline = []; // [{level, text, id}]
    let hseq = 0, prevLabel = "";
    const radioGroups = new Map();
    const claimed = new WeakSet(); // a custom dropdown's own parts, so they aren't read twice
    const isClaimed = (el) => { for (let n = el; n; n = n.parentElement) if (claimed.has(n)) return true; return false; };

    const headingLevel = (el) => {
      const m = /^H([1-6])$/.exec(el.tagName);
      if (m) return Number(m[1]);
      if (el.getAttribute("role") === "heading") return Number(el.getAttribute("aria-level")) || 2;
      return 0;
    };
    const groupContexts = (el) => {
      const out = [];
      let n = el.parentElement;
      for (let d = 0; n && d < 12; d++, n = n.parentElement || (n.getRootNode() instanceof ShadowRoot ? n.getRootNode().host : null)) {
        if (n.tagName === "FIELDSET") {
          const lg = [...n.children].find((c) => c.tagName === "LEGEND");
          const t = lg && textOf(lg);
          if (t) out.push([t, `lg:${uid(lg)}`]);
        } else if (["group", "radiogroup"].includes(n.getAttribute?.("role"))) {
          const t = byIds(n, "aria-labelledby") || clean(n.getAttribute("aria-label"));
          if (t) out.push([t, `g:${uid(n)}`]);
        }
      }
      return out;
    };
    const ids = new WeakMap();
    const uid = (node) => { if (!ids.has(node)) ids.set(node, ++hseq); return ids.get(node); };

    const addField = (el, kind, frameChain, extra = {}) => {
      if (!refOf.has(el)) refOf.set(el, `f${++refSeq}`);
      const ref = refOf.get(el); // the same box keeps its number across rescans
      const label = extra.label ?? (byIds(el, "aria-labelledby") || (el.labels && [...el.labels].map((l) => textOf(l)).filter(Boolean)[0]) || clean(el.getAttribute("aria-label")) || nearestText(el));
      const near = nearestText(el);
      const alt = [el.getAttribute("placeholder"), el.getAttribute("title"), byIds(el, "aria-describedby")].map(clean).filter(Boolean);
      const groups = groupContexts(el);
      const heads = outline.slice().reverse().map((h) => [h.text, `h:${h.id}`]);
      const ctx = [...(near && near !== label ? [[near, `n:${ref}`]] : []), ...groups, ...heads];
      const type = (el.getAttribute("type") || (el.tagName === "TEXTAREA" ? "textarea" : el.tagName === "SELECT" ? "select" : "text")).toLowerCase();
      const d = {
        ref, kind, type, label: clean(label).slice(0, 220), alt, name: el.getAttribute("name") || "", id: el.id || "",
        autocomplete: el.getAttribute("autocomplete") || "", placeholder: el.getAttribute("placeholder") || "",
        pattern: el.getAttribute("pattern") || "", maxlength: Number(el.getAttribute("maxlength")) > 0 ? Number(el.getAttribute("maxlength")) : 0,
        min: el.getAttribute("min") ?? "", max: el.getAttribute("max") ?? "", step: el.getAttribute("step") ?? "",
        required: el.required === true || el.getAttribute("aria-required") === "true",
        readonly: el.readOnly === true || el.getAttribute("aria-readonly") === "true",
        disabled: el.disabled === true || el.getAttribute("aria-disabled") === "true",
        value: "value" in el && kind !== "select" ? String(el.value ?? "").slice(0, 400) : "", checked: !!el.checked,
        contexts: ctx.map((c) => c[0]), contextIds: ctx.map((c) => c[1]), prev: prevLabel, nameHint: nameHint(el),
        inFrame: frameChain.length > 0, signIn: !!el.form?.querySelector("input[type=password]"), ...extra,
      };
      if (kind === "select") {
        d.options = [...el.options].slice(0, 400).map((o) => ({ text: clean(o.textContent).slice(0, 120), value: o.value, disabled: o.disabled }));
        d.selectedIndex = el.selectedIndex;
        d.selectDefault = ![...el.options].some((o) => o.defaultSelected) && el.selectedIndex === 0;
      }
      registry.set(ref, { el, kind, frames: frameChain, options: extra._options || null, labelEl: el.labels?.[0] || null,
        geo: extra._geo || null, typeahead: !!extra.typeahead, comboOptions: extra._comboOptions || null });
      delete d._options; delete d._geo; delete d._comboOptions;
      fields.push(d);
      prevLabel = d.label || prevLabel;
      return d;
    };

    const visit = (node, frameChain) => {
      for (let child = node.firstElementChild; child; child = child.nextElementSibling) {
        if (child === host) continue;
        const level = headingLevel(child);
        if (level) {
          const t = textOf(child, 160);
          if (t) {
            while (outline.length && outline[outline.length - 1].level >= level) outline.pop();
            outline.push({ level, text: t, id: uid(child) });
          }
        }
        handle(child, frameChain);
        if (child.shadowRoot) visit(child.shadowRoot, frameChain);
        if (child.tagName === "IFRAME" || child.tagName === "FRAME") {
          let doc = null;
          try { doc = child.contentDocument; } catch (e) { doc = null; }
          const r = child.getBoundingClientRect();
          if (doc && doc.documentElement) visit(doc.documentElement, [...frameChain, child]);
          else if (r.width > 160 && r.height > 120 && visible(child)) frames.push({ src: child.src || "", rect: { x: r.x, y: r.y, w: r.width, h: r.height } });
          continue;
        }
        if (!["SCRIPT", "STYLE", "TEMPLATE", "SVG", "SELECT"].includes(child.tagName.toUpperCase())) visit(child, frameChain);
      }
    };

    const handle = (el, frameChain) => {
      const tag = el.tagName;
      const role = el.getAttribute("role");
      if (tag === "INPUT") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        if (SKIP_TYPES.has(type)) return;
        if (type === "radio") {
          const labelEl = el.labels?.[0];
          if (!visible(el) && !(labelEl && visible(labelEl))) return;
          const key = `${uid(el.form || el.getRootNode())}|${el.name || uid(el.parentElement)}`;
          let g = radioGroups.get(key);
          if (!g) {
            g = addField(el, "radio", frameChain, { label: groupLabel(el), options: [], _options: [] });
            radioGroups.set(key, g);
          }
          g.options.push({ text: optionLabel(el), value: el.value, checked: el.checked, disabled: el.disabled });
          registry.get(g.ref).options.push(el);
          return;
        }
        if (type === "checkbox") {
          const labelEl = el.labels?.[0];
          if (!visible(el) && !(labelEl && visible(labelEl))) return;
          addField(el, "checkbox", frameChain, { label: optionLabel(el) || groupLabel(el) });
          return;
        }
        if (el.getAttribute("aria-hidden") === "true" || el.closest("[aria-hidden=true]") || isClaimed(el)) return;
        const auto = (el.getAttribute("aria-autocomplete") || "").toLowerCase();
        if (role === "combobox" || ((auto === "list" || auto === "both") && !el.hasAttribute("list"))) {
          const geo = comboGeo(el);
          if (!geo || !visible(geo)) return;
          claimed.add(geo);
          addField(el, "combo", frameChain, { typeahead: true, _geo: geo, value: comboText({ el, geo, typeahead: true }),
            label: comboLabel(el) || comboLabel(geo) });
          return;
        }
        if (!visible(el)) return;
        addField(el, type === "password" ? "secret" : type === "file" ? "file" : "text", frameChain);
        return;
      }
      if (tag === "TEXTAREA") { if (visible(el)) addField(el, "textarea", frameChain); return; }
      if (tag === "SELECT") {
        if (visible(el)) { addField(el, el.multiple ? "widget" : "select", frameChain); return; }
        // Select2, Chosen, bootstrap-select…: the real dropdown is hidden behind a look-alike
        const proxy = !el.multiple && proxyFor(el);
        if (proxy && visible(proxy)) {
          claimed.add(proxy);
          addField(el, "select", frameChain, { _geo: proxy, label: (el.labels && [...el.labels].map((l) => textOf(l)).filter(Boolean)[0]) || byIds(el, "aria-labelledby") || clean(el.getAttribute("aria-label")) || nearestText(proxy) || nearestText(el) });
        }
        return;
      }
      if (isClaimed(el)) return;
      const popup = (el.getAttribute("aria-haspopup") || "").toLowerCase();
      if (role === "combobox" || (popup === "listbox" && !["menu", "menubar"].includes(role)) || (role === "listbox" && el.hasAttribute("aria-expanded"))) {
        if (!visible(el)) return;
        claimed.add(el);
        const optsEls = role === "listbox" ? [...el.querySelectorAll("[role=option]")] : [];
        addField(el, "combo", frameChain, { label: comboLabel(el), value: comboText({ el }),
          ...(optsEls.length ? { options: optsEls.map((o) => ({ text: optionText(o), value: o.getAttribute("data-value") || "", disabled: o.getAttribute("aria-disabled") === "true" })), _comboOptions: optsEls } : {}) });
        return;
      }
      if (role === "radio") {
        if (!visible(el)) return;
        const group = el.closest("[role=radiogroup]") || el.parentElement;
        const key = `aria|${uid(group)}`;
        let g = radioGroups.get(key);
        if (!g) {
          const label = (group.getAttribute && (byIds(group, "aria-labelledby") || clean(group.getAttribute("aria-label")))) || nearestText(group);
          g = addField(el, "aria-radio", frameChain, { label, options: [], _options: [] });
          radioGroups.set(key, g);
        }
        g.options.push({ text: clean(el.getAttribute("aria-label")) || textOf(el, 120) || clean(el.getAttribute("data-value")), value: el.getAttribute("data-value") || "", checked: el.getAttribute("aria-checked") === "true", disabled: el.getAttribute("aria-disabled") === "true" });
        registry.get(g.ref).options.push(el);
        return;
      }
      if (role === "checkbox" || role === "switch") {
        if (visible(el)) addField(el, "aria-checkbox", frameChain, { label: clean(el.getAttribute("aria-label")) || byIds(el, "aria-labelledby") || textOf(el, 160), checked: el.getAttribute("aria-checked") === "true" });
        return;
      }
      if (el.isContentEditable && !el.parentElement?.isContentEditable && visible(el)) { addField(el, "widget", frameChain); return; }
      if (["textbox", "spinbutton"].includes(role) && visible(el)) addField(el, "widget", frameChain);
    };

    const groupLabel = (el) => {
      const fs = el.closest("fieldset");
      const lg = fs && [...fs.children].find((c) => c.tagName === "LEGEND");
      if (lg) return textOf(lg);
      const rg = el.closest("[role=radiogroup],[role=group]");
      if (rg) { const t = byIds(rg, "aria-labelledby") || clean(rg.getAttribute("aria-label")); if (t) return t; }
      // the question above the group: text before the smallest container holding every option
      let box = el.parentElement;
      const name = el.name;
      while (box && box.parentElement && name && box.querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`).length < (el.form || el.ownerDocument).querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`).length) box = box.parentElement;
      return nearestText(box || el);
    };

    visit(document.documentElement, []);
    lastScan = { url: location.href, title: document.title, fields, frames, at: Date.now() };
    dirty = false;
    return lastScan;
  }

  const observer = new MutationObserver((list) => {
    if (list.some((m) => m.target !== host && !host.contains(m.target))) { dirty = true; tagsDirty = true; }
  });
  observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["style", "class", "hidden", "disabled"] });

  function freshScan() { return dirty || !lastScan ? scan() : lastScan; }

  // ---------------------------------------------------------------- geometry
  function frameOffset(chain) {
    let x = 0, y = 0;
    for (const f of chain) { const r = f.getBoundingClientRect(); x += r.left + f.clientLeft; y += r.top + f.clientTop; }
    return { x, y };
  }
  function rectOf(ref) {
    const entry = registry.get(ref);
    if (!entry || !entry.el.isConnected) return null;
    const off = frameOffset(entry.frames);
    const els = entry.geo ? [entry.geo] : entry.options?.length ? entry.options.map((o) => (o.getBoundingClientRect().width < 3 && o.labels?.[0]) || o) : [entry.el];
    let r = null;
    for (const e of els) {
      let b = e.getBoundingClientRect();
      if ((b.width < 3 || b.height < 3) && e.labels?.[0]) b = e.labels[0].getBoundingClientRect();
      if (b.width < 1 && b.height < 1) continue;
      r = r ? { left: Math.min(r.left, b.left), top: Math.min(r.top, b.top), right: Math.max(r.right, b.right), bottom: Math.max(r.bottom, b.bottom) } : { left: b.left, top: b.top, right: b.right, bottom: b.bottom };
    }
    if (!r) return null;
    return { x: r.left + off.x, y: r.top + off.y, w: r.right - r.left, h: r.bottom - r.top };
  }
  function labelRect(ref) {
    const entry = registry.get(ref);
    if (!entry?.labelEl || !entry.labelEl.isConnected) return null;
    const off = frameOffset(entry.frames);
    const b = entry.labelEl.getBoundingClientRect();
    return { x: b.left + off.x, y: b.top + off.y, w: b.width, h: b.height };
  }
  const inside = (r, x, y, pad = 0) => r && x >= r.x - pad && x <= r.x + r.w + pad && y >= r.y - pad && y <= r.y + r.h + pad;

  function fieldAt(x, y) {
    let best = null, bestArea = Infinity;
    for (const f of lastScan?.fields || []) {
      const r = rectOf(f.ref);
      if (inside(r, x, y, 3) && r.w * r.h < bestArea) { best = f; bestArea = r.w * r.h; }
    }
    if (best) return best;
    for (const f of lastScan?.fields || []) if (inside(labelRect(f.ref), x, y)) return f;
    return null;
  }

  function pointInPolygon(x, y, pts) {
    let inPoly = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i], [xj, yj] = pts[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inPoly = !inPoly;
    }
    return inPoly;
  }
  function fieldsInLasso(pts) {
    const out = [];
    for (const f of lastScan?.fields || []) {
      const r = rectOf(f.ref);
      if (!r) continue;
      const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
      if (pointInPolygon(cx, cy, pts) || (r.w > 120 && pointInPolygon(r.x + 12, cy, pts) && pointInPolygon(r.x + Math.min(r.w, 160) - 12, cy, pts))) out.push(f.ref);
    }
    return out;
  }

  // ---------------------------------------------------------------- pointing and circling
  function send(msg) {
    try { chrome.runtime.sendMessage({ ...msg, url: location.href }).catch(() => {}); } catch (e) { teardown(); }
  }

  function startPick(mode, hint) {
    stopPick(false);
    const s = freshScan();
    pickState = { mode, hint, points: null };
    catcher.classList.add("on");
    banner.classList.add("on");
    banner.querySelector(".msg").textContent = mode === "circle" ? "Draw a circle around the boxes to fill · Esc to stop"
      : mode === "place" ? `Click the box for ${hint} · Esc to cancel` : "Click a box to fill it · Esc to stop";
    window.addEventListener("keydown", onKey, true);
    return s;
  }

  function stopPick(notify) {
    if (!pickState) return;
    const mode = pickState.mode;
    pickState = null;
    catcher.classList.remove("on");
    banner.classList.remove("on");
    hoverRing.style.display = "none";
    tip.style.display = "none";
    lasso.setAttribute("d", "");
    layer.querySelectorAll(".live").forEach((n) => n.remove());
    window.removeEventListener("keydown", onKey, true);
    if (notify) send({ event: "pick-stopped", mode });
  }

  function onKey(e) {
    if (e.key === "Escape" && pickState) { e.preventDefault(); e.stopImmediatePropagation(); stopPick(true); }
  }

  function showHover(f, x, y) {
    if (!f) { hoverRing.style.display = "none"; tip.style.display = "none"; return; }
    const r = rectOf(f.ref);
    if (!r) return;
    Object.assign(hoverRing.style, { display: "block", left: `${r.x - 3}px`, top: `${r.y - 3}px`, width: `${r.w + 6}px`, height: `${r.h + 6}px` });
    const name = f.label || f.alt?.[0] || f.name || "Unlabelled box";
    tip.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = name.length > 70 ? `${name.slice(0, 70)}…` : name;
    tip.append(b);
    if (pickState?.mode === "place") tip.append(document.createTextNode(` ← ${pickState.hint}`));
    tip.style.display = "block";
    const tx = Math.min(x + 14, innerWidth - 330), ty = y + 18 > innerHeight - 40 ? y - 36 : y + 18;
    Object.assign(tip.style, { left: `${Math.max(6, tx)}px`, top: `${ty}px` });
  }

  catcher.addEventListener("pointermove", (e) => {
    if (!pickState) return;
    if (dirty) scan();
    if (pickState.mode === "circle" && pickState.points) {
      const last = pickState.points[pickState.points.length - 1];
      if (Math.hypot(e.clientX - last[0], e.clientY - last[1]) < 3) return;
      pickState.points.push([e.clientX, e.clientY]);
      lasso.setAttribute("d", `M${pickState.points.map((p) => p.join(",")).join("L")}Z`);
      liveLasso();
      return;
    }
    showHover(fieldAt(e.clientX, e.clientY), e.clientX, e.clientY);
  });
  let liveTimer = 0;
  function liveLasso() {
    if (liveTimer) return;
    liveTimer = requestAnimationFrame(() => {
      liveTimer = 0;
      if (!pickState?.points || pickState.points.length < 3) return;
      layer.querySelectorAll(".live").forEach((n) => n.remove());
      for (const ref of fieldsInLasso(pickState.points)) {
        const r = rectOf(ref);
        const box = document.createElement("div");
        box.className = "box live";
        Object.assign(box.style, { left: `${r.x - 3}px`, top: `${r.y - 3}px`, width: `${r.w + 6}px`, height: `${r.h + 6}px` });
        layer.append(box);
      }
    });
  }
  catcher.addEventListener("pointerdown", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!pickState) return;
    if (dirty) scan();
    if (pickState.mode === "circle") {
      pickState.points = [[e.clientX, e.clientY]];
      try { catcher.setPointerCapture(e.pointerId); } catch (err) { /* synthetic pointer */ }
      hoverRing.style.display = "none"; tip.style.display = "none";
    }
  });
  catcher.addEventListener("pointerup", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (!pickState) return;
    if (pickState.mode === "circle" && pickState.points) {
      const pts = pickState.points;
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const tiny = Math.max(...xs) - Math.min(...xs) < 12 && Math.max(...ys) - Math.min(...ys) < 12;
      const refs = tiny ? [fieldAt(e.clientX, e.clientY)?.ref].filter(Boolean) : fieldsInLasso(pts);
      stopPick(false);
      send({ event: "picked", mode: "circle", refs, scan: lastScan });
      return;
    }
    const f = fieldAt(e.clientX, e.clientY);
    if (!f) return;
    flash(f.ref);
    const mode = pickState.mode;
    if (mode === "place") stopPick(false);
    send({ event: "picked", mode, refs: [f.ref], scan: lastScan });
  });
  catcher.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); });
  catcher.addEventListener("contextmenu", (e) => { e.preventDefault(); stopPick(true); });
  catcher.addEventListener("wheel", (e) => {
    // scroll whatever is under the pointer, as if the overlay weren't there
    e.preventDefault();
    const under = document.elementsFromPoint(e.clientX, e.clientY).find((n) => n !== host);
    let n = under;
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 1) { n.scrollBy(0, e.deltaY); return; }
      n = n.parentElement;
    }
    window.scrollBy(0, e.deltaY);
  }, { passive: false });

  // ---------------------------------------------------------------- tags on the page
  let tagLoop = 0, tagsDirty = true;
  document.addEventListener("scroll", () => { tagsDirty = true; }, { capture: true, passive: true });
  function drawTags() {
    layer.querySelectorAll(".tag, .box:not(.live)").forEach((n) => n.remove());
    for (const row of tagRows) {
      const r = rectOf(row.ref);
      if (!r || r.y + r.h < 0 || r.y > innerHeight) continue;
      const box = document.createElement("div");
      box.className = `box b-${row.tone}`;
      Object.assign(box.style, { left: `${r.x - 3}px`, top: `${r.y - 3}px`, width: `${r.w + 6}px`, height: `${r.h + 6}px` });
      const tag = document.createElement("div");
      tag.className = `tag t-${row.tone}`;
      tag.textContent = row.text ? `${row.n} · ${row.text}` : String(row.n);
      Object.assign(tag.style, { left: `${Math.max(2, r.x - 2)}px`, top: `${Math.max(2, r.y - 21)}px` });
      layer.append(box, tag);
    }
  }
  function setTags(rows) {
    tagRows = rows || [];
    cancelAnimationFrame(tagLoop);
    let lastKey = "";
    const loop = () => {
      const key = `${scrollX},${scrollY},${innerWidth},${innerHeight}`;
      if (key !== lastKey || tagsDirty) { drawTags(); lastKey = key; tagsDirty = false; }
      if (tagRows.length) tagLoop = requestAnimationFrame(loop);
    };
    drawTags();
    if (tagRows.length) tagLoop = requestAnimationFrame(loop);
  }

  function flash(ref) {
    const r = rectOf(ref);
    if (!r) return;
    const box = document.createElement("div");
    box.className = "box b-ready flash";
    Object.assign(box.style, { left: `${r.x - 3}px`, top: `${r.y - 3}px`, width: `${r.w + 6}px`, height: `${r.h + 6}px` });
    layer.append(box);
    setTimeout(() => box.remove(), 1200);
  }

  // ---------------------------------------------------------------- filling (values come only from the panel)
  const proto = (el, prop) => {
    const P = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    return Object.getOwnPropertyDescriptor(P, prop).set;
  };
  const fire = (el, value) => {
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertReplacementText", data: value ?? null }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const BAD_VALIDITY = ["typeMismatch", "patternMismatch", "tooLong", "rangeOverflow", "rangeUnderflow", "stepMismatch", "badInput"];
  const digits = (s) => String(s).replace(/\D/g, "");

  function snapshot(entry) {
    const { el, kind, options } = entry;
    if (kind === "select") return { selectedIndex: el.selectedIndex };
    if (kind === "radio") return { checked: options.findIndex((o) => o.checked) };
    if (kind === "aria-radio") return { checked: options.findIndex((o) => o.getAttribute("aria-checked") === "true") };
    if (kind === "checkbox") return { on: el.checked };
    if (kind === "aria-checkbox") return { on: el.getAttribute("aria-checked") === "true" };
    if (kind === "combo") return { comboText: comboText(entry) };
    return { value: el.value };
  }

  function setText(el, value) {
    el.focus({ preventScroll: true });
    proto(el, "value").call(el, value);
    fire(el, value);
    el.blur();
  }

  async function applyOne(action) {
    const entry = registry.get(action.ref);
    if (!entry || !entry.el.isConnected) return { ref: action.ref, ok: false, status: "gone", why: "The box is no longer on the page" };
    const { el, kind, options } = entry;
    if (el.disabled || el.readOnly) return { ref: action.ref, ok: false, status: "failed", why: "The box is locked" };
    const before = snapshot(entry);
    try {
      if (action.kind === "text" && ["text", "textarea"].includes(kind)) {
        const validBefore = el.validity ? el.validity.valid || el.value === "" : true;
        setText(el, action.value);
        const now = el.value;
        const same = now === action.value;
        const equivalent = !same && ((digits(now) && digits(now) === digits(action.value) && /^[\d\s().+\-/]+$/.test(action.value)) || now.trim().toLowerCase() === action.value.trim().toLowerCase());
        const bad = el.validity && BAD_VALIDITY.find((k) => el.validity[k]);
        if ((!same && !equivalent) || (bad && validBefore)) {
          setText(el, before.value);
          return { ref: action.ref, ok: false, status: "failed", why: bad ? `The box rejected this (${bad.replace(/([A-Z])/g, " $1").toLowerCase()})` : `The box changed it to "${now.slice(0, 40)}", so I put back what was there`, before };
        }
        return { ref: action.ref, ok: true, status: same ? "filled" : "reformatted", now, before, want: action.value };
      }
      if (action.kind === "select" && kind === "select") {
        el.focus({ preventScroll: true });
        proto(el, "selectedIndex").call(el, action.index);
        fire(el);
        if (entry.geo) for (const t of ["chosen:updated", "liszt:updated"]) el.dispatchEvent(new Event(t, { bubbles: true }));
        el.blur();
        if (el.selectedIndex !== action.index) {
          proto(el, "selectedIndex").call(el, before.selectedIndex); fire(el);
          return { ref: action.ref, ok: false, status: "failed", why: "The dropdown didn't keep the choice", before };
        }
        if (entry.geo) {
          await new Promise((r) => setTimeout(r, 80));
          if (!proxyShows(entry.geo, el.options[action.index]?.textContent || "")) {
            proto(el, "selectedIndex").call(el, before.selectedIndex); fire(el);
            for (const t of ["chosen:updated", "liszt:updated"]) el.dispatchEvent(new Event(t, { bubbles: true }));
            return { ref: action.ref, ok: false, status: "failed", why: "This dropdown didn't show the new choice, so I put it back. Pick it yourself.", before };
          }
        }
        return { ref: action.ref, ok: true, status: "filled", now: clean(el.options[action.index]?.textContent), before, want: action.index };
      }
      if (action.kind === "radio" && (kind === "radio" || kind === "aria-radio")) {
        const opt = options[action.option];
        if (!opt || !opt.isConnected) return { ref: action.ref, ok: false, status: "gone", why: "That choice is gone" };
        const isOn = () => (kind === "radio" ? opt.checked : opt.getAttribute("aria-checked") === "true");
        if (!isOn()) opt.click();
        await new Promise((r) => setTimeout(r, 60));
        if (!isOn()) return { ref: action.ref, ok: false, status: "failed", why: "The choice didn't stay selected", before };
        return { ref: action.ref, ok: true, status: "filled", now: action.option, before, want: action.option };
      }
      if (action.kind === "check" && (kind === "checkbox" || kind === "aria-checkbox")) {
        const isOn = () => (kind === "checkbox" ? el.checked : el.getAttribute("aria-checked") === "true");
        if (isOn() !== action.checked) el.click();
        await new Promise((r) => setTimeout(r, 60));
        if (isOn() !== action.checked) return { ref: action.ref, ok: false, status: "failed", why: "The checkbox didn't change", before };
        return { ref: action.ref, ok: true, status: "filled", now: action.checked, before, want: action.checked };
      }
      return { ref: action.ref, ok: false, status: "failed", why: "This kind of box can't take that answer" };
    } catch (err) {
      return { ref: action.ref, ok: false, status: "failed", why: `Error: ${String(err.message || err).slice(0, 80)}`, before };
    }
  }

  function readNow(ref) {
    const entry = registry.get(ref);
    if (!entry || !entry.el.isConnected) return undefined;
    const s = snapshot(entry);
    return s.value ?? s.selectedIndex ?? s.checked ?? s.on ?? s.comboText;
  }

  async function fill(actions, batchId) {
    const blocked = [];
    const onBlocked = (e) => blocked.push(String(e.detail || "submit"));
    const leaving = { yes: false };
    const onLeave = () => { leaving.yes = true; };
    document.addEventListener("sa-guard-blocked", onBlocked);
    window.addEventListener("beforeunload", onLeave);
    window.addEventListener("pagehide", onLeave);
    const startUrl = location.href;
    const results = [];
    for (const a of actions) results.push(await applyOne(a));
    await new Promise((r) => setTimeout(r, 600));
    // a second look: some sites put their old value back a moment later
    for (const res of results) {
      if (!res.ok) continue;
      const now = readNow(res.ref);
      const want = res.status === "reformatted" ? res.now : res.want;
      if (now === undefined) { Object.assign(res, { ok: false, status: "gone", why: "The box disappeared after filling" }); continue; }
      if (now !== want) {
        Object.assign(res, { ok: false, status: "changed", why: typeof now === "string" ? `The site changed it to "${now.slice(0, 40)}" — check it` : "The site undid this — check it" });
      }
    }
    document.removeEventListener("sa-guard-blocked", onBlocked);
    window.removeEventListener("beforeunload", onLeave);
    window.removeEventListener("pagehide", onLeave);
    batches.set(batchId, results.filter((r) => r.before).map((r) => ({ ref: r.ref, before: r.before })));
    dirty = true;
    return { results: results.map(({ before, want, ...r }) => r), blocked, left: leaving.yes || location.href !== startUrl };
  }

  // ---------------------------------------------------------------- custom dropdowns (asked for by the panel at Fill time)
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const unsafeToPress = (n) => !!n.closest?.("a[href], input[type=submit], input[type=image], input[type=reset]") ||
    !!(n.closest?.("button") && n.closest("button").form && !/^(button|reset)$/i.test(n.closest("button").getAttribute("type") || "submit") && n.closest("button").getAttribute("role") !== "option");
  function press(el) {
    const r = el.getBoundingClientRect();
    const o = { bubbles: true, cancelable: true, composed: true, clientX: r.left + Math.min(r.width / 2, 24), clientY: r.top + r.height / 2,
      button: 0, buttons: 1, pointerType: "mouse", isPrimary: true, view: window };
    el.dispatchEvent(new PointerEvent("pointerdown", o));
    el.dispatchEvent(new MouseEvent("mousedown", o));
    el.focus?.({ preventScroll: true });
    el.dispatchEvent(new PointerEvent("pointerup", { ...o, buttons: 0 }));
    el.dispatchEvent(new MouseEvent("mouseup", { ...o, buttons: 0 }));
    // a dropdown button that is also a form's submit button gets no click: that could submit
    if (!unsafeToPress(el)) el.dispatchEvent(new MouseEvent("click", { ...o, buttons: 0 }));
  }
  const expanded = (entry) => [entry.el, entry.geo, ...(entry.geo ? entry.geo.querySelectorAll("[aria-expanded]") : [])].some((n) => n?.getAttribute?.("aria-expanded") === "true");
  const listboxes = () => [...document.querySelectorAll("[role=listbox]")].filter((b) => visible(b) || [...b.querySelectorAll("[role=option]")].some(visible));
  function openList(entry, before) {
    const { el } = entry;
    if (el.getAttribute("role") === "listbox") {
      const opts = [...el.querySelectorAll("[role=option]")].filter(visible);
      return opts.length > 1 || el.getAttribute("aria-expanded") === "true" ? { box: el, opts } : null;
    }
    const owners = [el, ...(entry.geo ? entry.geo.querySelectorAll("[aria-controls],[aria-owns]") : [])];
    const ids = owners.flatMap((n) => `${n.getAttribute("aria-controls") || ""} ${n.getAttribute("aria-owns") || ""}`.split(/\s+/)).filter(Boolean);
    let boxes = [...new Set(ids.map((id) => el.ownerDocument.getElementById(id)))].filter((b) => b && (b.getAttribute("role") === "listbox" || b.querySelector("[role=option]")));
    if (!boxes.length) boxes = listboxes().filter((b) => !before.has(b) && !b.contains(el));
    boxes = boxes.filter((b) => [...b.querySelectorAll("[role=option]")].some(visible) || b.getAttribute("role") === "option");
    if (boxes.length !== 1) return null; // no list, or several: never guess which one belongs to this box
    return { box: boxes[0], opts: [...boxes[0].querySelectorAll("[role=option]")].filter(visible) };
  }
  async function comboOpen(ref, typeText) {
    const entry = registry.get(ref);
    if (!entry || !entry.el.isConnected) return { ok: false, status: "gone", why: "The box is no longer on the page" };
    if (entry.comboBefore === undefined) entry.comboBefore = comboText(entry);
    const before = new Set(listboxes());
    if (entry.typeahead) {
      if (typeText == null) return { ok: false, status: "failed", why: "Nothing to search for" };
      entry.el.focus({ preventScroll: true });
      proto(entry.el, "value").call(entry.el, typeText);
      entry.el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: typeText }));
    } else if (!expanded(entry)) {
      if (unsafeToPress(entry.el) && !entry.el.matches("button")) return { ok: false, status: "failed", why: "This dropdown is also a link; pick it yourself" };
      press(entry.el);
    }
    let open = null;
    for (let t = 0; t < 1800 && !open?.opts.length; t += 60) {
      await sleep(60);
      if (entry.el.hasAttribute("aria-expanded") && !expanded(entry) && !entry.typeahead) continue;
      open = openList(entry, before);
    }
    if (!open?.opts.length) { await comboClose(ref); return { ok: false, status: "failed", why: "The dropdown didn't open, or I couldn't tell which list was its own. Pick it yourself." }; }
    entry.open = open;
    return { ok: true, options: open.opts.map((o) => ({ text: optionText(o), value: o.getAttribute("data-value") || "", disabled: o.getAttribute("aria-disabled") === "true" })) };
  }
  async function comboClose(ref, restoreText = true) {
    const entry = registry.get(ref);
    if (!entry) return { ok: true };
    if (entry.typeahead && restoreText && entry.el.value) {
      proto(entry.el, "value").call(entry.el, "");
      entry.el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "deleteContentBackward", data: null }));
    }
    if (expanded(entry) || entry.open) {
      const target = document.activeElement && document.activeElement !== document.body ? document.activeElement : entry.el;
      for (const type of ["keydown", "keyup"]) target.dispatchEvent(new KeyboardEvent(type, { key: "Escape", code: "Escape", keyCode: 27, bubbles: true, cancelable: true, composed: true }));
    }
    entry.open = null;
    await sleep(60);
    return { ok: true };
  }
  async function comboPick(ref, index, batchId) {
    const entry = registry.get(ref);
    const opt = entry?.open?.opts[index];
    if (!opt || !opt.isConnected) { await comboClose(ref); return { ref, ok: false, status: "gone", why: "That choice is gone" }; }
    if (unsafeToPress(opt) && opt.getAttribute("role") !== "option") { await comboClose(ref); return { ref, ok: false, status: "failed", why: "That choice is a link; pick it yourself" }; }
    const want = optionText(opt);
    press(opt);
    let now = "";
    for (let t = 0; t < 900; t += 60) {
      await sleep(60);
      now = comboText(entry);
      if (norm(now) === norm(want) || ` ${norm(now)} `.includes(` ${norm(want)} `)) break;
    }
    const ok = norm(now) === norm(want) || ` ${norm(now)} `.includes(` ${norm(want)} `);
    if (expanded(entry)) await comboClose(ref, false);
    entry.open = null;
    const list = batches.get(batchId) || [];
    list.push({ ref, before: { comboText: entry.comboBefore ?? "" } });
    batches.set(batchId, list);
    entry.comboBefore = undefined;
    dirty = true;
    return ok ? { ref, ok: true, status: "filled", now: want }
      : { ref, ok: false, status: "changed", why: now ? `The dropdown shows "${now.slice(0, 40)}" — check it` : "The dropdown didn't take the choice — check it" };
  }

  async function undo(batchId) {
    const saved = batches.get(batchId);
    if (!saved) return { ok: false, why: "Nothing to undo" };
    let restored = 0, stuck = 0;
    for (const { ref, before } of saved.slice().reverse()) {
      const entry = registry.get(ref);
      if (!entry || !entry.el.isConnected) continue;
      const { el, kind, options } = entry;
      if ("comboText" in before) {
        // put a custom dropdown back by choosing the exact choice it showed; an empty one can't be emptied again
        if (!before.comboText) { stuck++; continue; }
        const opened = await comboOpen(ref, entry.typeahead ? before.comboText : null);
        const i = opened.ok ? opened.options.findIndex((o) => norm(o.text) === norm(before.comboText)) : -1;
        if (i >= 0 && (await comboPick(ref, i, `undo-${batchId}`)).ok) restored++;
        else { stuck++; await comboClose(ref); }
        batches.delete(`undo-${batchId}`);
        continue;
      }
      if ("value" in before) setText(el, before.value);
      else if ("selectedIndex" in before) {
        proto(el, "selectedIndex").call(el, before.selectedIndex); fire(el);
        if (entry.geo) for (const t of ["chosen:updated", "liszt:updated"]) el.dispatchEvent(new Event(t, { bubbles: true }));
      }
      else if ("checked" in before) {
        if (before.checked >= 0) options[before.checked].click();
        else if (kind === "radio") options.forEach((o) => { if (o.checked) { o.checked = false; fire(o); } });
      } else if ("on" in before) {
        const isOn = kind === "checkbox" ? el.checked : el.getAttribute("aria-checked") === "true";
        if (isOn !== before.on) el.click();
      }
      restored++;
    }
    batches.delete(batchId);
    dirty = true;
    return { ok: true, restored, stuck };
  }

  function focusField(ref) {
    const entry = registry.get(ref);
    if (!entry || !entry.el.isConnected) return false;
    const target = entry.options?.[0] || entry.el;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    setTimeout(() => flash(ref), 350);
    return true;
  }

  function teardown() {
    stopPick(false);
    tagRows = [];
    observer.disconnect();
    host.remove();
  }

  // ---------------------------------------------------------------- messages from the panel
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (sender.id !== chrome.runtime.id || !msg || !msg.cmd) return false;
    (async () => {
      switch (msg.cmd) {
        case "ping": return { ok: true, version: VERSION, url: location.href };
        case "scan": return { ok: true, scan: scan() };
        case "pick": return { ok: true, scan: startPick(msg.mode, msg.hint || "") };
        case "stopPick": stopPick(false); return { ok: true };
        case "tags": setTags(msg.rows); return { ok: true };
        case "fill": return { ok: true, ...(await fill(msg.actions || [], msg.batch)) };
        case "undo": return await undo(msg.batch);
        case "guardLog": { const out = guardLog; guardLog = []; return { ok: true, blocked: out }; }
        case "comboOpen": return await comboOpen(msg.ref, msg.type ?? null);
        case "comboPick": return await comboPick(msg.ref, msg.index, msg.batch);
        case "comboClose": return await comboClose(msg.ref);
        case "focus": return { ok: focusField(msg.ref) };
        case "clear": stopPick(false); setTags([]); return { ok: true };
        default: return { ok: false, error: "unknown command" };
      }
    })().then(reply, (err) => reply({ ok: false, error: String(err?.message || err) }));
    return true;
  });
})();
