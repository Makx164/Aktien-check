/* Zielzuordnung für die alte GitHub-Pages-Adresse.
   Nur https://yoursidequest.org. Jeder andere Pfad endet auf der Startseite. */

var SIDEQUEST_ORIGIN = "https://yoursidequest.org";

function sidequestNormalizePath(pathname) {
  var path = String(pathname || "/");
  try { path = decodeURIComponent(path); } catch (e) {}
  var lower = path.toLowerCase();
  var base = "/aktien-check";
  if (lower === base) path = "/";
  else if (lower.indexOf(base + "/") === 0) path = path.slice(base.length) || "/";
  lower = path.toLowerCase();
  if (lower.length > 1 && lower.charAt(lower.length - 1) === "/") lower = lower.slice(0, -1);
  if (lower.slice(-11) === "/index.html") lower = lower.slice(0, -11);
  else if (lower === "index.html" || lower === "/index.html") lower = "/";
  else if (lower.slice(-5) === ".html") lower = lower.slice(0, -5);
  if (lower.slice(-6) === "/index") lower = lower.slice(0, -6);
  if (lower === "" || lower === "/") return "/";
  return lower;
}

function sidequestTarget(loc) {
  var path = sidequestNormalizePath(loc && loc.pathname);
  if (path === "/impressum") return SIDEQUEST_ORIGIN + "/impressum";
  if (path === "/datenschutz") return SIDEQUEST_ORIGIN + "/datenschutz";
  return SIDEQUEST_ORIGIN + "/";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    sidequestTarget: sidequestTarget,
    SIDEQUEST_ORIGIN: SIDEQUEST_ORIGIN
  };
}
