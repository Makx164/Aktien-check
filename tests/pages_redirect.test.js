var assert = require("assert");
var fs = require("fs");
var path = require("path");
var test = require("node:test");
var logic = require("./pages_redirect_logic.js");
var build = require("./build_pages_redirect.js");

var root = path.join(__dirname, "..");
var target = logic.sidequestTarget;

function at(pathname, search, hash) {
  return target({ pathname: pathname, search: search || "", hash: hash || "" });
}

test("ticker list matches config/tickers.json and the live page", function () {
  var configured = JSON.parse(fs.readFileSync(path.join(root, "config/tickers.json"), "utf8"))
    .map(function (row) { return row.ticker; });
  assert.deepStrictEqual(logic.SIDEQUEST_TICKERS, configured);
  var html = fs.readFileSync(path.join(root, "index.html"), "utf8");
  var match = html.match(/var TICKERS = \[([^\]]+)\]/);
  assert.ok(match, "TICKERS array");
  var tickers = match[1].match(/[A-Z]+/g);
  assert.deepStrictEqual(tickers, configured);
  assert.strictEqual(html.indexOf("C_01"), -1);
});

test("home, hash routes and open query keep the matching page", function () {
  assert.strictEqual(at("/Aktien-check/"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check/index.html"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check/index.html", "", "#/PLTR"), "https://yoursidequest.org/#/PLTR");
  assert.strictEqual(at("/Aktien-check/", "", "#/pltr"), "https://yoursidequest.org/#/PLTR");
  assert.strictEqual(at("/Aktien-check/", "", "#/NOPE"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check/", "", "#/PLTR/extra"), "https://yoursidequest.org/#/PLTR");
  assert.strictEqual(at("/Aktien-check/", "?open=TSLA-C_05"), "https://yoursidequest.org/#/TSLA");
  assert.strictEqual(at("/Aktien-check/", "?open=hims-c_02"), "https://yoursidequest.org/#/HIMS");
  assert.strictEqual(at("/Aktien-check/", "?open=TSLA-C_99"), "https://yoursidequest.org/#/TSLA");
  assert.strictEqual(at("/Aktien-check/", "?evil=https://evil.example&open=MU-C_01", "#/MU"), "https://yoursidequest.org/#/MU");
  assert.strictEqual(at("/Aktien-check/", "?open=https://evil.example"), "https://yoursidequest.org/");
  assert.strictEqual(at("/", "", "#/SIVE"), "https://yoursidequest.org/");
  assert.strictEqual(at("/", "", "#/PYPL"), "https://yoursidequest.org/");
  assert.strictEqual(at("/", "", "#/SPCX"), "https://yoursidequest.org/");
});

test("legal pages, ticker paths and unknown paths", function () {
  assert.strictEqual(at("/Aktien-check/impressum.html"), "https://yoursidequest.org/impressum");
  assert.strictEqual(at("/Aktien-check/impressum"), "https://yoursidequest.org/impressum");
  assert.strictEqual(at("/Aktien-check/Impressum/"), "https://yoursidequest.org/impressum");
  assert.strictEqual(at("/Aktien-check/datenschutz.html"), "https://yoursidequest.org/datenschutz");
  assert.strictEqual(at("/Aktien-check/datenschutz"), "https://yoursidequest.org/datenschutz");
  assert.strictEqual(at("/Aktien-check/PLTR"), "https://yoursidequest.org/#/PLTR");
  assert.strictEqual(at("/Aktien-check/pltr.html"), "https://yoursidequest.org/#/PLTR");
  assert.strictEqual(at("/Aktien-check/PLTR/"), "https://yoursidequest.org/#/PLTR");
  assert.strictEqual(at("/Aktien-check/ticker/AMZN"), "https://yoursidequest.org/#/AMZN");
  assert.strictEqual(at("/Aktien-check/aktie/IREN/"), "https://yoursidequest.org/#/IREN");
  assert.strictEqual(at("/Aktien-check/data/PLTR/score.json"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check/fonts/space-grotesk.woff2"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check/config/tickers.json"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check/does-not-exist"), "https://yoursidequest.org/");
  assert.strictEqual(at("/Aktien-check//evil.example"), "https://yoursidequest.org/");
});

test("targets stay on yoursidequest.org", function () {
  var samples = [
    at("/Aktien-check/", "?open=PLTR-C_01", "#/PLTR"),
    at("/Aktien-check/impressum.html", "?next=https://evil.example"),
    at("/Aktien-check/%2F%2Fevil.example")
  ];
  samples.forEach(function (url) {
    assert.strictEqual(url.indexOf("https://yoursidequest.org/"), 0);
    assert.strictEqual(url.indexOf("evil.example"), -1);
  });
});

test("docs pages are generated from the mapper and do not touch the Vercel site", function () {
  var docs = path.join(root, "docs");
  var expected = [
    "index.html", "impressum.html", "datenschutz.html", "404.html", ".nojekyll", "sw.js", "service-worker.js"
  ].concat(logic.SIDEQUEST_TICKERS.map(function (ticker) { return ticker + ".html"; }));
  var actual = fs.readdirSync(docs).sort();
  assert.deepStrictEqual(actual, expected.slice().sort());
  assert.strictEqual(fs.readFileSync(path.join(docs, "sw.js"), "utf8"), fs.readFileSync(path.join(root, "sw.js"), "utf8"));
  assert.strictEqual(fs.readFileSync(path.join(docs, "service-worker.js"), "utf8"), fs.readFileSync(path.join(root, "service-worker.js"), "utf8"));
  assert.strictEqual(fs.readFileSync(path.join(docs, ".nojekyll"), "utf8"), "");
  var ignore = fs.readFileSync(path.join(root, ".vercelignore"), "utf8");
  assert.ok(ignore.indexOf("/docs") !== -1);
  assert.ok(ignore.indexOf("/.vercelignore") !== -1);
  ["index.html", "impressum.html", "datenschutz.html", "404.html", "PLTR.html"].forEach(function (name) {
    var html = fs.readFileSync(path.join(docs, name), "utf8");
    assert.ok(html.indexOf('<meta http-equiv="refresh" content="0; url=') !== -1);
    assert.ok(html.indexOf('<link rel="canonical" href="') !== -1);
    assert.ok(html.indexOf('<meta name="robots" content="noindex">') !== -1);
    assert.ok(html.indexOf("location.replace(target);") !== -1);
    assert.ok(html.indexOf("<a href=\"") !== -1);
    assert.ok(html.indexOf("function sidequestTarget") !== -1);
  });
  var home = fs.readFileSync(path.join(docs, "index.html"), "utf8");
  assert.ok(home.indexOf("https://yoursidequest.org/") !== -1);
  var impressum = fs.readFileSync(path.join(docs, "impressum.html"), "utf8");
  assert.ok(impressum.indexOf('content="0; url=https://yoursidequest.org/impressum"') !== -1);
  var pltr = fs.readFileSync(path.join(docs, "PLTR.html"), "utf8");
  assert.ok(pltr.indexOf('content="0; url=https://yoursidequest.org/#/PLTR"') !== -1);
  assert.strictEqual(fs.existsSync(path.join(root, "index.html")), true);
  assert.ok(fs.readFileSync(path.join(root, "index.html"), "utf8").indexOf("location.replace(target)") === -1);
});

test("builder is deterministic", function () {
  var os = require("os");
  var dir = fs.mkdtempSync(path.join(os.tmpdir(), "pages-redirect-"));
  build.writePages(dir);
  ["index.html", "404.html", "OSS.html", "sw.js"].forEach(function (name) {
    assert.strictEqual(
      fs.readFileSync(path.join(dir, name), "utf8"),
      fs.readFileSync(path.join(root, "docs", name), "utf8")
    );
  });
});
