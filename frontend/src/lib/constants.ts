/**
 * Kept in lockstep with `CSP` in backend/app/artifacts/sanitize.py.
 *
 * The backend sets this header on `/api/artifacts/{id}/render`; the frontend
 * injects the same policy into the iframe document. Both matter: the header
 * covers direct navigation to an artifact URL, the meta tag covers the
 * `srcdoc` iframe, which has no response headers of its own.
 */
export const CSP_TEXT =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
