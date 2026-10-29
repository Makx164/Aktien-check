/* Zielzuordnung für die alte GitHub-Pages-Adresse.
   Nur https://yoursidequest.org, nur bekannte Ticker und Seiten. */

var SIDEQUEST_ORIGIN = "https://yoursidequest.org";
var SIDEQUEST_TICKERS = ["PLTR", "OSS", "AMZN", "TSLA", "HIMS", "LMND", "IBRX", "MU", "IREN", "GRAB"];

function sidequestTickerMap() {
  var map = {};
  for (var i = 0; i < SIDEQUEST_TICKERS.length; i++) map[SIDEQUEST_TICKERS[i]] = 1;
  return map;
}

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

function sidequestHash(loc, tickers) {
  var raw = String(loc && loc.hash || "");
  if (raw.charAt(0) === "#") raw = raw.slice(1);
  try { raw = decodeURIComponent(raw); } catch (e) { return ""; }
  var part = raw.split("/").filter(Boolean)[0] || "";
  var ticker = part.toUpperCase();
  if (!tickers[ticker]) return "";
  return "#/" + ticker;
}

function sidequestOpenTicker(loc, tickers) {
  var raw = String(loc && loc.search || "");
  if (raw.charAt(0) === "?") raw = raw.slice(1);
  if (!raw) return "";
  var open = "";
  var pairs = raw.split("&");
  for (var i = 0; i < pairs.length; i++) {
    var pair = pairs[i];
    var eq = pair.indexOf("=");
    var key = eq === -1 ? pair : pair.slice(0, eq);
    var value = eq === -1 ? "" : pair.slice(eq + 1);
    if (key !== "open") continue;
    try { open = decodeURIComponent(value.replace(/\+/g, " ")); } catch (e) { open = ""; }
  }
  var ticker = String((open.split("-")[0] || "")).trim().toUpperCase();
  return tickers[ticker] ? ticker : "";
}

function sidequestTarget(loc) {
  var tickers = sidequestTickerMap();
  var path = sidequestNormalizePath(loc && loc.pathname);
  if (path === "/") {
    var openTicker = sidequestOpenTicker(loc, tickers);
    if (openTicker) return SIDEQUEST_ORIGIN + "/#/" + openTicker;
    return SIDEQUEST_ORIGIN + "/" + sidequestHash(loc, tickers);
  }
  if (path === "/impressum") return SIDEQUEST_ORIGIN + "/impressum";
  if (path === "/datenschutz") return SIDEQUEST_ORIGIN + "/datenschutz";
  var segs = path.split("/").filter(Boolean);
  if (segs.length === 1 && tickers[segs[0].toUpperCase()]) {
    return SIDEQUEST_ORIGIN + "/#/" + segs[0].toUpperCase();
  }
  if (segs.length === 2 && (segs[0] === "ticker" || segs[0] === "aktie") && tickers[segs[1].toUpperCase()]) {
    return SIDEQUEST_ORIGIN + "/#/" + segs[1].toUpperCase();
  }
  return SIDEQUEST_ORIGIN + "/";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    sidequestTarget: sidequestTarget,
    SIDEQUEST_TICKERS: SIDEQUEST_TICKERS,
    SIDEQUEST_ORIGIN: SIDEQUEST_ORIGIN
  };
}
