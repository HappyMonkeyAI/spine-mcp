// Spine Design extraction script, v1.
//
// Run this in the page (your browser's evaluate / run-JS tool) BEFORE taking the
// screenshot, then pass the JSON it returns to `record_capture` with the screenshot.
//
// What it does, in order:
//   1. Freezes animations, transitions and the caret so two captures are comparable.
//   2. Masks private or volatile areas WITHOUT changing the page: it lays grey overlays
//      on top of inputs, textareas, contenteditable, canvas, [data-spine-mask] and any
//      extra selectors in window.SPINE_DESIGN_OPTIONS.mask. Your form values stay intact.
//   3. Measures computed styles and component candidates and returns them as JSON.
//      It returns no page text: only accessible names, truncated to 40 characters.
//
// Afterwards, window.__spineDesignUnmask() removes the overlays and the freeze.
// Masks are positioned for a viewport screenshot (not full-page).
(() => {
  const VERSION = 1;
  const opts = (typeof window.SPINE_DESIGN_OPTIONS === "object" && window.SPINE_DESIGN_OPTIONS) || {};
  const MAX_ELEMENTS = 5000;
  const MAX_COMPONENTS = 80;
  const MAX_BBOXES = 12;

  if (window.__spineDesignUnmask) window.__spineDesignUnmask();

  // 1. Freeze motion.
  const freeze = document.createElement("style");
  freeze.setAttribute("data-spine-design", "freeze");
  freeze.textContent =
    "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}";
  document.head.appendChild(freeze);

  // 2. Mask, non-destructively.
  const maskSelectors = [
    "input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button]):not([type=submit]):not([type=hidden])",
    "textarea",
    "[contenteditable]:not([contenteditable=false])",
    "canvas",
    "[data-spine-mask]",
    ...(Array.isArray(opts.mask) ? opts.mask : []),
  ];
  const overlayRoot = document.createElement("div");
  overlayRoot.setAttribute("data-spine-design", "masks");
  overlayRoot.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
  let masked = 0;
  const maskedSet = new Set();
  for (const selector of maskSelectors) {
    let found = [];
    try { found = document.querySelectorAll(selector); } catch (e) { continue; }
    for (const el of found) {
      if (maskedSet.has(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      maskedSet.add(el);
      const box = document.createElement("div");
      box.style.cssText =
        `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;background:#808080`;
      overlayRoot.appendChild(box);
      masked++;
    }
  }
  document.body.appendChild(overlayRoot);
  window.__spineDesignUnmask = () => {
    freeze.remove();
    overlayRoot.remove();
    delete window.__spineDesignUnmask;
  };

  // 3. Measure.
  const counts = {
    color: {}, background: {}, border: {}, font_family: {}, font_size: {},
    font_weight: {}, line_height: {}, spacing: {}, radius: {}, shadow: {},
  };
  const bump = (group, value) => {
    if (!value) return;
    counts[group][value] = (counts[group][value] || 0) + 1;
  };
  const transparent = (v) => !v || v === "transparent" || /rgba\([^)]*,\s*0\)$/.test(v);
  const px = (v) => (v && v !== "0px" && v !== "normal" && v !== "auto" ? v : null);
  const bbox = (r) => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  const visible = (el, cs, r) =>
    r.width > 0 && r.height > 0 && cs.display !== "none" && cs.visibility !== "hidden" && Number(cs.opacity) > 0 &&
    r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  const insideMask = (el) => {
    for (const m of maskedSet) if (m === el || m.contains(el)) return true;
    return false;
  };
  const accessibleName = (el) => {
    const label = el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("alt") || "";
    return label.trim().slice(0, 40);
  };
  const stylesOf = (cs) => ({
    color: cs.color,
    background: transparent(cs.backgroundColor) ? null : cs.backgroundColor,
    border: parseFloat(cs.borderTopWidth) > 0 && !transparent(cs.borderTopColor)
      ? `${cs.borderTopWidth} ${cs.borderTopStyle} ${cs.borderTopColor}` : null,
    radius: px(cs.borderTopLeftRadius),
    padding: cs.padding === "0px" ? null : cs.padding,
    gap: px(cs.gap),
    font_family: cs.fontFamily,
    font_size: cs.fontSize,
    font_weight: cs.fontWeight,
    shadow: cs.boxShadow === "none" ? null : cs.boxShadow,
  });

  const LANDMARKS = new Set(["banner", "navigation", "main", "complementary", "contentinfo", "dialog",
    "tablist", "tab", "toolbar", "menu", "menubar", "listbox", "tree", "grid", "search", "status", "alert"]);
  const IMPLICIT = { HEADER: "banner", NAV: "navigation", MAIN: "main", ASIDE: "complementary",
    FOOTER: "contentinfo", DIALOG: "dialog", BUTTON: "button" };
  const groups = new Map();

  let seen = 0;
  for (const el of document.body.querySelectorAll("*")) {
    if (seen >= MAX_ELEMENTS) break;
    if (overlayRoot.contains(el)) continue;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (!visible(el, cs, r)) continue;
    seen++;
    if (insideMask(el)) continue;

    const s = stylesOf(cs);
    if (el.childElementCount === 0 || el.textContent.trim()) bump("color", s.color);
    bump("background", s.background);
    if (s.border) bump("border", s.border.split(" ").slice(2).join(" "));
    bump("font_family", s.font_family);
    bump("font_size", s.font_size);
    bump("font_weight", s.font_weight);
    if (cs.lineHeight !== "normal") bump("line_height", cs.lineHeight);
    for (const v of [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft,
      cs.marginTop, cs.marginBottom, cs.rowGap, cs.columnGap]) bump("spacing", px(v));
    bump("radius", s.radius);
    bump("shadow", s.shadow);

    // Component candidates: explicit hooks first, then roles, custom elements, class signatures.
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || IMPLICIT[el.tagName] || "";
    const firstClass = (el.classList && el.classList[0]) || "";
    let key = el.getAttribute("data-component") || el.getAttribute("data-testid") || "";
    let selector = key
      ? (el.hasAttribute("data-component") ? `[data-component="${key}"]` : `[data-testid="${key}"]`)
      : "";
    if (!key && firstClass && !/^(css|sc|jsx|emotion|tw)-|^[a-z]{1,3}-[a-z0-9]{5,}$/i.test(firstClass)) {
      key = `${tag}.${firstClass}`;
      selector = `${tag}.${CSS.escape(firstClass)}`;
    } else if (!key && (LANDMARKS.has(role) || tag.includes("-"))) {
      key = tag.includes("-") ? tag : `${tag}[role=${role}]`;
      selector = tag.includes("-") ? tag : (el.getAttribute("role") ? `[role="${role}"]` : tag);
    }
    if (!key) continue;
    let g = groups.get(key);
    if (!g) {
      g = { key, selector, role, name: accessibleName(el), count: 0, bboxes: [], styles: s,
        classes: Array.from(el.classList).slice(0, 4) };
      groups.set(key, g);
    }
    g.count++;
    if (g.bboxes.length < MAX_BBOXES) g.bboxes.push(bbox(r));
  }

  const components = Array.from(groups.values())
    .filter((g) => g.count >= 2 || g.role || /^\[data-/.test(g.selector) || g.key.includes("-"))
    .sort((a, b) => b.count - a.count)
    .slice(0, MAX_COMPONENTS);

  // :root custom properties: the app's own token names.
  const rootVars = {};
  const rootStyle = getComputedStyle(document.documentElement);
  const collect = (rules) => {
    for (const rule of rules) {
      if (rule.cssRules && !rule.selectorText) { try { collect(rule.cssRules); } catch (e) {} continue; }
      if (!rule.selectorText || !/(^|,)\s*:root\b/.test(rule.selectorText)) continue;
      for (const name of rule.style) {
        if (name.startsWith("--")) rootVars[name] = rootStyle.getPropertyValue(name).trim();
      }
    }
  };
  for (const sheet of document.styleSheets) {
    try { collect(sheet.cssRules); } catch (e) { /* cross-origin stylesheet */ }
  }

  const root = document.documentElement;
  return {
    version: VERSION,
    url: location.origin + location.pathname,
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    theme: root.getAttribute("data-theme") || root.getAttribute("data-color-mode") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
    elements: seen,
    masked,
    root_vars: rootVars,
    counts,
    components,
  };
})();
