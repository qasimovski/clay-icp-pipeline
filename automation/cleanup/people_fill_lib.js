// Runs on app.clay.com. Locates the "Countries to include" filter field and
// fills it from a list, one country at a time, mirroring how a human would
// type a name, wait for the dropdown suggestion, and click it.
//
// Guarded against double injection: the manifest statically injects this on
// page load, but the popup also injects it on demand (via chrome.scripting)
// as a fallback for tabs that were already open before the extension loaded.
if (!window.__clayCountryFillerLoaded) {
window.__clayCountryFillerLoaded = true;

const FIELD_LABEL = "Countries to include";

// Hard-coded seniority levels, filled on demand. Match mode is always "Is
// exactly". These are the standard Clay seniority values the user always uses.
const SENIORITY_VALUES = [
  "Founder",
  "Owner",
  "Board Member",
  "Partner",
  "C-suite",
  "VP",
  "Director",
  "Head",
  "Manager",
  "Senior",
  "Mid-level",
];

// Countries filled for every build (always the same).
const LOCATION_VALUES = [
  "United Kingdom",
  "United Arab Emirates",
  "Saudi Arabia",
  "United States",
  "Netherlands",
  "Japan",
  "China",
];

// The three one-click builds. Seniority + Location are always the same; only
// the job title values and match mode differ. From Builds_Types.txt.
const BUILDS = [
  {
    name: "1st",
    jobTitleMode: "exact",
    jobTitles: [
      "CEO",
      "Chief Executive Officer",
      "MD",
      "Managing Director",
      "Managing Partner",
      "President",
      "Vice President",
      "Regional Vice President",
      "Founder",
      "GM",
      "CCO",
      "CMO",
      "Chief Strategy Officer",
      "Regional Director",
      "Country Manager",
      "BDM",
    ],
  },
  {
    name: "2nd",
    jobTitleMode: "contains",
    jobTitles: [
      "Executive Director",
      "General Manager",
      "COO",
      "Chief Operating Officer",
      "Chief Business Officer",
      "CTO",
      "Chief Technology Officer",
      "Owner",
    ],
  },
  {
    name: "3rd",
    jobTitleMode: "contains",
    jobTitles: [
      "Business Development",
      "Channel",
      "Commercial",
      "Distribution",
      "Events",
      "Exhibitions",
      "Go-to-market",
      "Growth",
      "Healthcare Solutions",
      "Marketing",
      "Partner",
      "Partnerships",
      "Product",
      "Sales",
      "Strategy",
      "Trade Show",
      "Middle East",
      "EMEA",
      "MENA",
      "MEA",
      "APAC",
      "Americas",
    ],
  },
];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jitter(min, max) {
  return min + Math.random() * (max - min);
}

const INPUT_SELECTOR =
  'input, [role="combobox"], [role="textbox"], [contenteditable="true"]';

// Find the deepest (leaf) element whose trimmed text satisfies `matcher`.
function findLeafByText(matcher) {
  const all = document.querySelectorAll("body *");
  for (const el of all) {
    if (el.children.length === 0 && matcher(el.textContent.trim())) {
      return el;
    }
  }
  return null;
}

function isVisible(el) {
  return el && el.offsetParent !== null;
}

// Among the input-like descendants of `container`, pick the visible one that
// comes after `afterEl` in document order (the value field usually follows its
// label). Prefer a plain text input over a combobox/select trigger.
function pickInput(container, afterEl) {
  const all = Array.from(container.querySelectorAll(INPUT_SELECTOR)).filter(isVisible);
  const after = afterEl
    ? all.filter(
        (el) =>
          afterEl.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING
      )
    : all;
  const pool = after.length ? after : all;
  const textFirst = pool.filter((el) =>
    el.matches('input, [contenteditable="true"], [role="textbox"]')
  );
  return textFirst[0] || pool[0] || null;
}

// Walk up from a label element a few levels, returning the nearest input-like
// descendant (visible, after the label) and the container it was found in.
function inputNear(labelEl, levels) {
  let container = labelEl && labelEl.parentElement;
  for (let i = 0; i < (levels || 4) && container; i++) {
    const input = pickInput(container, labelEl);
    if (input) return { input, container };
    container = container.parentElement;
  }
  return { input: null, container: null };
}

// Find the clickable accordion header for a section (e.g. "Job title",
// "Location"): the accordion trigger, i.e. an [aria-expanded] element whose
// label text is (or starts with) the section name. Clay renders these as
// <button aria-expanded> with the section name as its first text.
function findSectionTrigger(label) {
  const l = label.toLowerCase();
  const cands = Array.from(document.querySelectorAll("[aria-expanded]")).filter(
    isVisible
  );
  return (
    cands.find((b) => {
      const t = (b.getAttribute("aria-label") || b.textContent || "")
        .trim()
        .toLowerCase();
      return t === l || t.startsWith(l);
    }) || null
  );
}

// The clickable section header: prefer the real aria-expanded trigger, else
// fall back to the labelled leaf escalated to a toggle-ish ancestor.
function findSectionHeader(label) {
  const trigger = findSectionTrigger(label);
  if (trigger) return trigger;

  const leaf =
    findLeafByText((t) => t === label) ||
    findLeafByText((t) => t.toLowerCase() === label.toLowerCase()) ||
    findLeafByText((t) => t.toLowerCase().startsWith(label.toLowerCase()));
  if (!leaf) return null;
  let node = leaf;
  for (let i = 0; i < 6 && node; i++) {
    if (
      node.matches &&
      node.matches('button, [role="button"], [aria-expanded], [data-state]')
    ) {
      return node;
    }
    node = node.parentElement;
  }
  return leaf;
}

// Whether a section header reports itself as expanded, via aria-expanded /
// data-state (Radix-style accordions). Returns null if it exposes neither.
function headerExpandedState(header) {
  const toggle =
    (header && (header.closest("[aria-expanded]") || header.closest("[data-state]"))) ||
    header;
  if (!toggle) return null;
  const ae = toggle.getAttribute && toggle.getAttribute("aria-expanded");
  if (ae !== null && ae !== undefined) return ae === "true";
  const ds = toggle.getAttribute && toggle.getAttribute("data-state");
  if (ds) return ds === "open";
  return null;
}

// Locate the input for a filter field whose label text exactly matches `label`.
function locateField(label) {
  const labelEl = findLeafByText((t) => t === label);
  if (!labelEl) return null;
  return inputNear(labelEl, 4).input;
}

// Locate the Job title filter. The expanded "Job title" section (component
// JobTitleSeniorityFields) contains several combobox inputs — one for
// Seniority (aria-label "Seniority levels") and one for Job titles. We want
// the Job title one: the combobox input in the section that is NOT the
// seniority field (preferring one whose label mentions "title").
// Returns { input, container, labelEl }.
function describeInput(el) {
  return (
    (el.getAttribute("aria-label") || "") +
    " " +
    (el.getAttribute("placeholder") || "")
  ).toLowerCase();
}

// The "Job title" field label inside the section (a leaf whose text is
// "Job title"/"Job Title"), distinct from the accordion header trigger.
// Seniority and its "is exactly" dropdown sit ABOVE this label, so anchoring
// on it lets us ignore everything before it.
function jobTitleFieldLabel(section, trigger) {
  return (
    Array.from(section.querySelectorAll('label, [data-slot="label"], p, span')).find(
      (el) =>
        /^job\s*titles?$/i.test(el.textContent.trim()) &&
        !trigger.contains(el) &&
        isVisible(el)
    ) || null
  );
}

function afterInDoc(anchor, el) {
  return (
    !anchor ||
    Boolean(anchor.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)
  );
}

function locateJobTitle() {
  const trigger = findSectionTrigger("Job title");
  if (!trigger || !trigger.parentElement) {
    return { input: null, container: null, labelEl: null };
  }
  const section = trigger.parentElement;
  const anchor = jobTitleFieldLabel(section, trigger) || trigger;

  const combos = Array.from(
    section.querySelectorAll('input[role="combobox"], input[data-slot="input"]')
  ).filter(isVisible);

  // The Job title values input is the first combobox AFTER the Job title label
  // (everything before the label belongs to Seniority).
  const after = combos.filter((el) => afterInDoc(anchor, el));
  const input =
    after[0] ||
    combos.find((el) => !describeInput(el).includes("senior")) ||
    combos[combos.length - 1] ||
    null;

  return { input, container: section, labelEl: anchor };
}

// The Job title match-mode dropdown: a <button data-slot="control"
// aria-haspopup="listbox"> that sits AFTER the Job title label and BEFORE the
// Job title input — i.e. this field's own dropdown, not Seniority's.
function jobTitleModeButton(section, anchor, input) {
  let btns = Array.from(
    section.querySelectorAll('button[data-slot="control"][aria-haspopup="listbox"]')
  ).filter(isVisible);
  btns = btns.filter((b) => afterInDoc(anchor, b));
  if (input) {
    btns = btns.filter(
      (b) => input.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_PRECEDING
    );
  }
  return btns[btns.length - 1] || null;
}

// The filter panel is an accordion of collapsible sections ("Job title",
// "Location", ...). When collapsed, a section's inputs aren't rendered. Click
// the section header to expand it. Expansion is detected via the header's
// aria-expanded / data-state (reliable), falling back to `isReadyFn`.
async function ensureSectionExpanded(sectionLabel, isReadyFn) {
  const header = findSectionHeader(sectionLabel);

  const expanded = () => {
    const state = headerExpandedState(header);
    if (state !== null) return state && isReadyFn();
    return isReadyFn();
  };

  if (expanded()) return true;
  if (!header) return isReadyFn();

  // Click the header, escalating to ancestors, until the section opens.
  let node = header;
  for (let level = 0; level < 5 && node; level++) {
    robustClick(node);
    for (let i = 0; i < 14; i++) {
      await sleep(150);
      if (expanded()) return true;
      // If the header now reports open but the input isn't found yet, keep
      // waiting a bit more for the panel to render.
      if (headerExpandedState(header) === true && isReadyFn()) return true;
    }
    node = node.parentElement;
  }
  return expanded();
}

function countVisibleInputs() {
  return Array.from(document.querySelectorAll(INPUT_SELECTOR)).filter(isVisible).length;
}

// Open a section with a single click (avoids the toggle-open/toggle-closed
// churn of repeated clicks). Returns true if it looks open afterward.
async function openSectionOnce(label) {
  const header = findSectionHeader(label);
  if (!header) return false;
  if (headerExpandedState(header) === true) return true;
  const before = countVisibleInputs();
  robustClick(header);
  for (let i = 0; i < 12; i++) {
    await sleep(150);
    if (headerExpandedState(header) === true || countVisibleInputs() > before) {
      return true;
    }
  }
  return headerExpandedState(header) === true || countVisibleInputs() > before;
}

// Dump the Job title section's HTML (climbing to a container that wraps its
// panel) so its input/controls markup can be inspected.
function jobTitleSectionHtml() {
  const trigger = findSectionTrigger("Job title") || findSectionHeader("Job title");
  if (!trigger) return "(no 'Job title' header found)";
  // The accordion item wraps the trigger and (when open) its panel. Climb a
  // level or two if the immediate parent doesn't yet include an input.
  let wrapper = trigger.parentElement || trigger;
  for (let i = 0; i < 2; i++) {
    if (wrapper.querySelector(INPUT_SELECTOR)) break;
    if (wrapper.parentElement) wrapper = wrapper.parentElement;
  }
  return wrapper.outerHTML.replace(/\s+/g, " ").slice(0, 9000);
}

// A short snapshot of the filter panel's structure, for diagnostics when a
// field/section can't be located.
function panelDiagnostics() {
  const headers = Array.from(
    document.querySelectorAll('button, [role="button"], [aria-expanded], [data-state]')
  )
    .filter(isVisible)
    .map((e) => e.textContent.trim().replace(/\s+/g, " ").slice(0, 24))
    .filter(Boolean);
  const inputs = Array.from(document.querySelectorAll(INPUT_SELECTOR))
    .filter(isVisible)
    .map(
      (e) =>
        e.getAttribute("placeholder") ||
        e.getAttribute("aria-label") ||
        "(input)"
    );
  return {
    headers: Array.from(new Set(headers)).slice(0, 20),
    inputs: inputs.slice(0, 15),
  };
}

function setNativeValue(element, value) {
  if ("value" in element) {
    const proto = Object.getPrototypeOf(element);
    const desc =
      Object.getOwnPropertyDescriptor(proto, "value") ||
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    if (desc && desc.set) {
      desc.set.call(element, value);
    } else {
      element.value = value;
    }
  } else if (element.isContentEditable) {
    element.textContent = value;
  }
}

function getFieldValue(field) {
  if (field.isContentEditable) return field.textContent || "";
  return field.value || "";
}

async function typeIntoField(field, text) {
  field.focus();
  setNativeValue(field, "");
  field.dispatchEvent(new InputEvent("input", { bubbles: true }));
  await sleep(jitter(80, 200));

  let current = "";
  for (const ch of text) {
    current += ch;
    const keyOpts = { key: ch, bubbles: true, cancelable: true };
    field.dispatchEvent(new KeyboardEvent("keydown", keyOpts));
    setNativeValue(field, current);
    field.dispatchEvent(
      new InputEvent("input", { bubbles: true, data: ch, inputType: "insertText" })
    );
    field.dispatchEvent(new KeyboardEvent("keyup", keyOpts));
    await sleep(jitter(20, 60));
  }
}

function dispatchKey(field, key, keyCode) {
  const opts = {
    key: key,
    code: key,
    keyCode: keyCode,
    which: keyCode,
    bubbles: true,
    cancelable: true,
  };
  field.dispatchEvent(new KeyboardEvent("keydown", opts));
  field.dispatchEvent(new KeyboardEvent("keypress", opts));
  field.dispatchEvent(new KeyboardEvent("keyup", opts));
}

// Fire a full pointer/mouse sequence so components that listen on mousedown
// respond, followed by exactly ONE click. Firing a synthetic "click" event AND
// el.click() would produce two clicks, which silently toggles accordions and
// dropdown menus open-then-closed — so we emit the click only once.
function robustClick(el) {
  const opts = { bubbles: true, cancelable: true, view: window };
  el.dispatchEvent(new PointerEvent("pointerdown", opts));
  el.dispatchEvent(new MouseEvent("mousedown", opts));
  el.dispatchEvent(new PointerEvent("pointerup", opts));
  el.dispatchEvent(new MouseEvent("mouseup", opts));
  if (typeof el.click === "function") {
    el.click();
  } else {
    el.dispatchEvent(new MouseEvent("click", opts));
  }
}

// Broad set of selectors Clay's suggestion dropdown could use.
const OPTION_SELECTORS =
  '[role="option"], [role="menuitem"], [role="listbox"] li, [class*="option" i], [class*="menu-item" i], [class*="MenuItem" i], [class*="suggestion" i], [aria-selected]';

function candidateOptions() {
  return Array.from(document.querySelectorAll(OPTION_SELECTORS)).filter(
    (o) => o.offsetParent !== null && o.textContent.trim().length > 0
  );
}

// Find the best dropdown option for `value`: a visible element whose text
// contains the value, preferring an exact match, then the shortest text
// (most specific / leaf-most option rather than a big container).
function findMatchingOption(value, fuzzy) {
  const v = value.toLowerCase();
  const cands = candidateOptions();

  let exact = null;
  const contains = [];
  for (const o of cands) {
    const text = o.textContent.trim().toLowerCase();
    if (text === v) {
      if (!exact || o.textContent.length < exact.textContent.length) exact = o;
    } else if (text.includes(v)) {
      contains.push(o);
    }
  }
  if (exact) return exact;
  if (contains.length === 0) return null;

  // In non-fuzzy mode, still accept a "contains" match (Clay options often
  // render "Germany" alongside a flag/region), but pick the shortest text.
  contains.sort((a, b) => a.textContent.length - b.textContent.length);
  return contains[0];
}

async function waitForOption(value, fuzzy, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const opt = findMatchingOption(value, fuzzy);
    if (opt) return opt;
    await sleep(120);
  }
  return null;
}

// Small snapshot of what the dropdown currently shows, for diagnostics when
// nothing matched.
function dropdownSnapshot() {
  const texts = candidateOptions()
    .map((o) => o.textContent.trim().replace(/\s+/g, " "))
    .slice(0, 15);
  return Array.from(new Set(texts));
}

async function fillCountries(countries, fuzzy, onProgress) {
  // Expand the Location section if it's collapsed (single click, no toggling).
  if (!locateField(FIELD_LABEL)) {
    await openSectionOnce("Location");
    await sleep(300);
  }

  const field = locateField(FIELD_LABEL);
  if (!field) {
    throw new Error(
      `Could not locate the "${FIELD_LABEL}" field. Could not open the Location filter section automatically — try expanding it manually.`
    );
  }

  const added = [];
  const notFound = [];
  let diagnostics = null;

  for (const country of countries) {
    robustClick(field);
    field.focus();
    await sleep(jitter(60, 150));
    await typeIntoField(field, country);

    // Wait for a matching suggestion to appear, then click it. Fall back to
    // ArrowDown+Enter (highlight first suggestion, then commit).
    const option = await waitForOption(country, fuzzy, 2500);
    if (option) {
      robustClick(option);
    } else {
      field.focus();
      dispatchKey(field, "ArrowDown", 40);
      await sleep(150);
      dispatchKey(field, "Enter", 13);
    }

    // Poll (up to ~800ms) for the chip to commit — the input clears when it
    // does. Faster than a fixed wait and avoids false "not found" on slow commits.
    let committed = false;
    for (let _p = 0; _p < 8; _p++) {
      await sleep(100);
      if (getFieldValue(field).trim() === "") { committed = true; break; }
    }
    if (committed) {
      added.push(country);
    } else {
      notFound.push(country);
      if (!diagnostics) diagnostics = dropdownSnapshot();
      // Clear the leftover text before trying the next country.
      setNativeValue(field, "");
      field.dispatchEvent(new InputEvent("input", { bubbles: true }));
      dispatchKey(field, "Escape", 27);
    }

    if (onProgress) {
      onProgress({
        country,
        found: committed,
        done: added.length + notFound.length,
        total: countries.length,
      });
    }

    await sleep(jitter(100, 250));
  }

  return { added, notFound, diagnostics };
}

// --------------------------------------------------------------------------
// Job title filter
// --------------------------------------------------------------------------

// Set the Job title match mode by clicking its dropdown (a headless-ui
// listbox button showing "Contains"/"is exactly"), then clicking the option
// matching `mode`. Returns { ok, note }.
async function setJobTitleMatchMode(section, anchor, input, mode) {
  const wantExact = mode === "exact";
  const btn = jobTitleModeButton(section, anchor, input);
  if (!btn) {
    return { ok: false, note: "match-mode control not found" };
  }

  const cur = btn.textContent.trim().toLowerCase();
  if ((wantExact && cur.includes("exact")) || (!wantExact && cur.includes("contain"))) {
    return { ok: true, note: `already ${wantExact ? "Is exactly" : "Contains"}` };
  }

  robustClick(btn);
  await sleep(450);

  // The listbox options render as [role="option"] (headless-ui).
  const opts = Array.from(document.querySelectorAll('[role="option"]')).filter(isVisible);
  const target = opts.find((o) => {
    const t = o.textContent.trim().toLowerCase();
    return wantExact ? t.includes("exact") : t.includes("contain");
  });
  if (target) {
    robustClick(target);
    await sleep(300);
    return { ok: true, note: `set to ${wantExact ? "Is exactly" : "Contains"}` };
  }

  dispatchKey(btn, "Escape", 27);
  return {
    ok: false,
    note:
      "mode option not found; options seen: " +
      JSON.stringify(opts.map((o) => o.textContent.trim()).slice(0, 6)),
  };
}

// Add one value to a combobox-multiple input: type it, press Enter to commit
// a chip. Returns true if the input cleared (chip committed).
async function addOneValue(input, value) {
  input.focus();
  await sleep(120);
  setNativeValue(input, value);
  input.dispatchEvent(
    new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" })
  );
  await sleep(300);
  dispatchKey(input, "Enter", 13);
  await sleep(300);
  return getFieldValue(input).trim() === "";
}

// Fill the Job title field. First try pasting the whole comma-separated list +
// Enter (fast path); if that doesn't clear the box, fall back to adding each
// title one at a time.
async function fillJobTitles(titles, mode, onProgress) {
  let { input, container, labelEl } = locateJobTitle();
  if (!input) {
    await openSectionOnce("Job title");
    await sleep(400);
    ({ input, container, labelEl } = locateJobTitle());
  }
  if (!input) {
    throw new Error(
      "Could not locate the Job title field. Section HTML follows so the " +
        "selector can be fixed:\n\n" +
        jobTitleSectionHtml()
    );
  }

  const modeResult = await setJobTitleMatchMode(container, labelEl, input, mode);

  const added = [];
  const failed = [];

  // Fast path: paste everything at once.
  const joined = titles.join(", ");
  input.focus();
  await sleep(150);
  setNativeValue(input, joined);
  input.dispatchEvent(
    new InputEvent("input", { bubbles: true, data: joined, inputType: "insertFromPaste" })
  );
  await sleep(350);
  dispatchKey(input, "Enter", 13);
  await sleep(500);

  if (getFieldValue(input).trim() === "") {
    added.push(...titles);
  } else {
    // Fall back to one-at-a-time.
    setNativeValue(input, "");
    input.dispatchEvent(new InputEvent("input", { bubbles: true }));
    await sleep(150);
    for (const title of titles) {
      let ok = await addOneValue(input, title);
      if (ok) {
        added.push(title);
      } else {
        failed.push(title);
        setNativeValue(input, "");
        input.dispatchEvent(new InputEvent("input", { bubbles: true }));
        dispatchKey(input, "Escape", 27);
      }
      if (onProgress) {
        onProgress({
          country: title,
          found: ok,
          done: added.length + failed.length,
          total: titles.length,
        });
      }
      await sleep(jitter(200, 450));
    }
  }

  return { added, notFound: failed, modeNote: modeResult.note };
}

// --------------------------------------------------------------------------
// Seniority filter (within the Job title section, positioned ABOVE Job title)
// --------------------------------------------------------------------------

// The Seniority values combobox: the section's combobox whose label/placeholder
// mentions "senior" (aria-label "Seniority levels", placeholder
// "e.g. C-suite, Manager"). It's the first field in the section.
function locateSeniority() {
  const trigger = findSectionTrigger("Job title");
  if (!trigger || !trigger.parentElement) return { input: null, container: null };
  const section = trigger.parentElement;
  const combos = Array.from(
    section.querySelectorAll('input[role="combobox"], input[data-slot="input"]')
  ).filter(isVisible);
  const input =
    combos.find((el) => describeInput(el).includes("senior")) || combos[0] || null;
  return { input, container: section };
}

// The Seniority match-mode dropdown: the mode button that precedes the
// seniority input (the first field's dropdown).
function seniorityModeButton(section, input) {
  const btns = Array.from(
    section.querySelectorAll('button[data-slot="control"][aria-haspopup="listbox"]')
  ).filter(isVisible);
  if (!input) return btns[0] || null;
  const before = btns.filter(
    (b) => input.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_PRECEDING
  );
  return before[before.length - 1] || btns[0] || null;
}

// Choose "Is exactly"/"Contains" on a mode dropdown button. Returns {ok,note}.
async function chooseModeOption(btn, wantExact) {
  if (!btn) return { ok: false, note: "match-mode control not found" };
  const cur = btn.textContent.trim().toLowerCase();
  if ((wantExact && cur.includes("exact")) || (!wantExact && cur.includes("contain"))) {
    return { ok: true, note: `already ${wantExact ? "Is exactly" : "Contains"}` };
  }
  robustClick(btn);
  await sleep(450);
  const opts = Array.from(document.querySelectorAll('[role="option"]')).filter(isVisible);
  const target = opts.find((o) => {
    const t = o.textContent.trim().toLowerCase();
    return wantExact ? t.includes("exact") : t.includes("contain");
  });
  if (target) {
    robustClick(target);
    await sleep(300);
    return { ok: true, note: `set to ${wantExact ? "Is exactly" : "Contains"}` };
  }
  dispatchKey(btn, "Escape", 27);
  return {
    ok: false,
    note:
      "mode option not found; options seen: " +
      JSON.stringify(opts.map((o) => o.textContent.trim()).slice(0, 6)),
  };
}

// Add one value to a fixed-vocabulary combobox: type it, wait for the matching
// suggestion, click it; fall back to ArrowDown+Enter. Returns true if cleared.
async function commitFromDropdown(input, value) {
  input.focus();
  await sleep(120);
  setNativeValue(input, value);
  input.dispatchEvent(
    new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" })
  );
  await sleep(250);
  const option = await waitForOption(value, false, 1500);
  if (option) {
    robustClick(option);
  } else {
    dispatchKey(input, "ArrowDown", 40);
    await sleep(150);
    dispatchKey(input, "Enter", 13);
  }
  await sleep(350);
  return getFieldValue(input).trim() === "";
}

// Fill the Seniority field with the hard-coded SENIORITY_VALUES, match mode
// always "Is exactly". Seniority is a fixed-vocabulary combobox, so each value
// is typed and its dropdown suggestion clicked (ArrowDown+Enter fallback).
async function fillSeniority(onProgress) {
  let { input, container } = locateSeniority();
  if (!input) {
    await openSectionOnce("Job title");
    await sleep(400);
    ({ input, container } = locateSeniority());
  }
  if (!input) {
    throw new Error(
      "Could not locate the Seniority field. Section HTML follows:\n\n" +
        jobTitleSectionHtml()
    );
  }

  const modeResult = await chooseModeOption(seniorityModeButton(container, input), true);

  const added = [];
  const failed = [];

  // Seniority is a fixed-vocabulary picker — add one value at a time via its
  // suggestion dropdown (bulk comma-paste does NOT work for this field).
  for (const value of SENIORITY_VALUES) {
    let ok = await commitFromDropdown(input, value);
    if (ok) {
      added.push(value);
    } else {
      failed.push(value);
      setNativeValue(input, "");
      input.dispatchEvent(new InputEvent("input", { bubbles: true }));
      dispatchKey(input, "Escape", 27);
    }
    if (onProgress) {
      onProgress({
        country: value,
        found: ok,
        done: added.length + failed.length,
        total: SENIORITY_VALUES.length,
      });
    }
    await sleep(jitter(200, 450));
  }

  return { added, notFound: failed, modeNote: modeResult.note };
}

// --------------------------------------------------------------------------
// One-click builds: Seniority -> Job title -> Location, in that order.
// --------------------------------------------------------------------------

async function runBuild(index, onProgress) {
  const build = BUILDS[index];
  if (!build) throw new Error(`Unknown build index: ${index}`);

  // Tag each phase's progress so the popup can show which field is filling.
  const phased = (phase) => (p) =>
    onProgress && onProgress(Object.assign({ phase }, p));

  const result = { name: build.name };

  try {
    result.seniority = await fillSeniority(phased("Seniority"));
  } catch (err) {
    result.seniority = { error: String(err.message || err) };
  }

  try {
    result.jobTitle = await fillJobTitles(
      build.jobTitles,
      build.jobTitleMode,
      phased("Job title")
    );
  } catch (err) {
    result.jobTitle = { error: String(err.message || err) };
  }

  try {
    result.location = await fillCountries(LOCATION_VALUES, false, phased("Location"));
  } catch (err) {
    result.location = { error: String(err.message || err) };
  }

  return result;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "INSPECT_FIELD") {
    (async () => {
      // Only open the section if it isn't already open (so we never toggle a
      // manually-expanded section shut). Then dump its HTML.
      if (!locateJobTitle().input) {
        await openSectionOnce("Job title");
        await sleep(500);
      }
      const jtHtml = jobTitleSectionHtml();

      const field = locateField(FIELD_LABEL);
      const jt = locateJobTitle();
      sendResponse({
        found: Boolean(field),
        html: field ? field.outerHTML.slice(0, 1500) : null,
        jobTitleFound: Boolean(jt.input),
        jobTitleHtml: jtHtml,
      });
    })();
    return true; // async response
  }

  if (message.type === "FILL_COUNTRIES") {
    fillCountries(message.countries, message.fuzzy, (progress) => {
      chrome.runtime.sendMessage({ type: "FILL_PROGRESS", progress }).catch(() => {});
    })
      .then((result) => sendResponse({ ok: true, result }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true; // keep the message channel open for the async response
  }

  if (message.type === "FILL_JOB_TITLES") {
    fillJobTitles(message.titles, message.mode, (progress) => {
      chrome.runtime.sendMessage({ type: "FILL_PROGRESS", progress }).catch(() => {});
    })
      .then((result) => sendResponse({ ok: true, result }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  }

  if (message.type === "FILL_SENIORITY") {
    fillSeniority((progress) => {
      chrome.runtime.sendMessage({ type: "FILL_PROGRESS", progress }).catch(() => {});
    })
      .then((result) => sendResponse({ ok: true, result }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  }

  if (message.type === "FILL_BUILD") {
    runBuild(message.index, (progress) => {
      chrome.runtime.sendMessage({ type: "FILL_PROGRESS", progress }).catch(() => {});
    })
      .then((result) => sendResponse({ ok: true, result }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  }

  return false;
});

} // end double-injection guard
