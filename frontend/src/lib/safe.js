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
  if (Array.isArray(v)) return v;
  if (v === null || v === undefined) return [];
  if (isPlainObject(v)) return Object.entries(v).map(([k, val]) => ({ key: k, value: val }));
  return [v];
}

/** A finite number or null (never NaN). */
export function numOrNull(v) {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Object or null - for nested payloads that may be missing entirely. */
export function objOrNull(v) {
  return isPlainObject(v) ? v : null;
}
