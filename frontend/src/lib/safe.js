/* V4.7 rendering-safety helpers.
 *
 * The dashboard renders values that come from a research database that is years
 * old in places: nodes without a genome, legacy rows, partially populated
 * backtests, null columns, arrays where a scalar was expected. React renders
 * `undefined`, `null` and `[object Object]` as literal text and crashes on
 * `.map` of a non-array, so every uncontrolled value goes through these helpers
 * instead.
 *
 * They never invent data: an unusable value renders as the app's N/A marker.
 */

export const NA_TEXT = "N/A";

/** Parse a JSON-encoded container that arrived as text ("[0, 2]" / '{"a":1}').
 *  Some endpoints return a SQLite JSON column verbatim; an array rendered from
 *  such a string would silently look empty, so decode it here instead. */
function fromJsonText(v) {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!t || (t[0] !== "[" && t[0] !== "{")) return v;
  try {
    const parsed = JSON.parse(t);
    return parsed === null || parsed === undefined ? v : parsed;
  } catch {
    return v;
  }
}

export function isPlainObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** Text for display: strings/numbers pass through, objects never become
 *  "[object Object]", null/undefined/NaN become the N/A marker. */
export function txt(v, fallback = NA_TEXT) {
  if (v === null || v === undefined) return fallback;
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : fallback;
  if (typeof v === "string") return v.length ? v : fallback;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return v.length ? v.map((x) => txt(x, "")).filter(Boolean).join(", ") || fallback : fallback;
  if (isPlainObject(v)) {
    for (const key of ["name", "label", "value", "text", "message", "id", "note"]) {
      if (v[key] !== undefined && v[key] !== null) return txt(v[key], fallback);
    }
    try {
      const s = JSON.stringify(v);
      return s && s !== "{}" ? s : fallback;
    } catch {
      return fallback;
    }
  }
  try {
    const s = String(v);
    return s && s !== "[object Object]" ? s : fallback;
  } catch {
    return fallback;
  }
}

/** Always an array: arrays pass through (copied), null becomes [], a scalar
 *  becomes a single-element array, an object becomes its entries list. */
export function arr(v) {
  const value = fromJsonText(v);
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined) return [];
  if (isPlainObject(value)) return Object.entries(value).map(([k, val]) => ({ key: k, value: val }));
  return [value];
}

/** A finite number or null (never NaN, and never a silent zero).
 *
 *  `Number(null)`, `Number("")`, `Number(" ")` and `Number(false)` are all 0,
 *  which turned *missing* values into the very concrete number zero: a node with
 *  no quote showed an entry price of 0.00, an unspecified risk became $0, and a
 *  panel could "calculate" a lot size from nothing. Missing means missing here —
 *  only real numbers and numeric strings pass through. */
export function numOrNull(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string") {
    const t = v.trim();
    if (t === "") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;                       // null, undefined, boolean, object, array
}

/** Object or null - for nested payloads that may be missing entirely. */
export function objOrNull(v) {
  const value = fromJsonText(v);
  return isPlainObject(value) ? value : null;
}

/** An array of usable objects: drops null/undefined/scalar entries.
 *
 *  List payloads can contain a null element (a partially written row, a stub, an
 *  older backend). `.map(r => r.id)` on those crashes the whole page, so lists of
 *  records go through this helper instead of `arr()`. */
export function rows(v) {
  return arr(v).filter((r) => isPlainObject(r));
}
