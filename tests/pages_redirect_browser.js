/* Lokaler Check: die Seiten unter /Aktien-check/ leiten wie GitHub Pages weiter. */
var http = require("http");
var fs = require("fs");
var os = require("os");
var path = require("path");
var { spawn } = require("child_process");

var docs = path.join(__dirname, "..", "docs");
var base = "/Aktien-check";

function contentType(file) {
  if (file.endsWith(".html")) return "text/html; charset=utf-8";
  if (file.endsWith(".js")) return "application/javascript; charset=utf-8";
  return "application/octet-stream";
}

function startServer() {
  var server = http.createServer(function (req, res) {
    var url = new URL(req.url, "http://127.0.0.1");
    var pathname = decodeURIComponent(url.pathname);
    if (pathname !== base && pathname.indexOf(base + "/") !== 0) {
      res.writeHead(404, { "content-type": "text/plain" });
      res.end("missing base");
      return;
    }
    var rel = pathname.slice(base.length);
    if (rel === "" || rel.endsWith("/")) rel += "index.html";
    var file = path.normalize(path.join(docs, rel));
    if (file.indexOf(docs) !== 0) {
      res.writeHead(400);
      res.end("bad path");
      return;
    }
    if (!fs.existsSync(file) && fs.existsSync(file + ".html")) file += ".html";
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404, { "content-type": "text/html; charset=utf-8" });
      res.end(fs.readFileSync(path.join(docs, "404.html")));
      return;
    }
    res.writeHead(200, { "content-type": contentType(file) });
    res.end(fs.readFileSync(file));
  });
  return new Promise(function (resolve) {
    server.listen(0, "127.0.0.1", function () {
      resolve({ server: server, port: server.address().port });
    });
  });
}

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

async function waitFor(fn) {
  var last = null;
  for (var i = 0; i < 40; i++) {
    try { return await fn(); } catch (error) { last = error; }
    await sleep(100);
  }
  throw last || new Error("timeout");
}

async function withChrome(fn) {
  var profile = fs.mkdtempSync(path.join(os.tmpdir(), "pages-chrome-"));
  var chrome = spawn("google-chrome", [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--user-data-dir=" + profile,
    "--remote-debugging-port=0",
    "about:blank"
  ], { stdio: ["ignore", "pipe", "pipe"] });
  var log = "";
  function take(chunk) { log += chunk; }
  chrome.stdout.on("data", take);
  chrome.stderr.on("data", take);
  var port = await new Promise(function (resolve, reject) {
    var timer = setTimeout(function () { reject(new Error("chrome port\n" + log)); }, 10000);
    function check() {
      var match = log.match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)/);
      if (!match) return;
      clearTimeout(timer);
      resolve(match[1]);
    }
    chrome.stdout.on("data", check);
    chrome.stderr.on("data", check);
  });
  try {
    return await fn(port);
  } finally {
    chrome.kill("SIGKILL");
    fs.rmSync(profile, { recursive: true, force: true });
  }
}

function rpc(ws, method, params) {
  return new Promise(function (resolve, reject) {
    var id = rpc.next++;
    function onMessage(event) {
      var message = JSON.parse(event.data);
      if (message.id !== id) return;
      ws.removeEventListener("message", onMessage);
      if (message.error) reject(new Error(method + " " + JSON.stringify(message.error)));
      else resolve(message.result || {});
    }
    ws.addEventListener("message", onMessage);
    ws.send(JSON.stringify({ id: id, method: method, params: params || {} }));
  });
}
rpc.next = 1;

async function openPage(port) {
  var list = await waitFor(function () {
    return fetch("http://127.0.0.1:" + port + "/json/list").then(function (res) { return res.json(); });
  });
  var page = list.filter(function (item) { return item.type === "page"; })[0];
  var ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(function (resolve, reject) {
    ws.addEventListener("open", resolve);
    ws.addEventListener("error", reject);
  });
  await rpc(ws, "Page.enable");
  await rpc(ws, "Runtime.enable");
  return ws;
}

async function finalHref(ws, url, javascript) {
  await rpc(ws, "Emulation.setScriptExecutionDisabled", { value: !javascript });
  await rpc(ws, "Page.navigate", { url: url });
  var href = "";
  for (var i = 0; i < 40; i++) {
    await sleep(150);
    var evaluated = await rpc(ws, "Runtime.evaluate", {
      expression: "location.href",
      returnByValue: true
    });
    href = evaluated.result && evaluated.result.value || "";
    if (href.indexOf("https://yoursidequest.org") === 0) return href;
    if (!javascript && href.indexOf(url.split("#")[0].split("?")[0]) === -1 && href.indexOf("http://127.0.0.1") !== 0) return href;
  }
  return href;
}

async function main() {
  var started = await startServer();
  var origin = "http://127.0.0.1:" + started.port + base;
  var cases = [
    [origin + "/", true, "https://yoursidequest.org/"],
    [origin + "/#/PLTR", true, "https://yoursidequest.org/#/PLTR"],
    [origin + "/index.html#/mu", true, "https://yoursidequest.org/#/MU"],
    [origin + "/?open=HIMS-C_02", true, "https://yoursidequest.org/#/HIMS"],
    [origin + "/impressum", true, "https://yoursidequest.org/impressum"],
    [origin + "/datenschutz.html", true, "https://yoursidequest.org/datenschutz"],
    [origin + "/PLTR", true, "https://yoursidequest.org/#/PLTR"],
    [origin + "/data/PLTR/score.json", true, "https://yoursidequest.org/"],
    [origin + "/gibt-es-nicht", true, "https://yoursidequest.org/"],
    [origin + "/#/PLTR", false, "https://yoursidequest.org/"],
    [origin + "/impressum.html", false, "https://yoursidequest.org/impressum"],
    [origin + "/OSS.html", false, "https://yoursidequest.org/#/OSS"],
    [origin + "/unbekannt", false, "https://yoursidequest.org/"]
  ];
  try {
    await withChrome(async function (port) {
      var ws = await openPage(port);
      for (var i = 0; i < cases.length; i++) {
        var item = cases[i];
        var href = await finalHref(ws, item[0], item[1]);
        var extra = href.indexOf(item[2]) === 0 ? href.slice(item[2].length) : null;
        var ok = href === item[2] || (extra !== null && /^#\/[A-Z0-9]+$/.test(extra));
        console.log((ok ? "OK  " : "FAIL") + " js=" + item[1] + " " + item[0].replace(origin, "") + " -> " + href);
        if (!ok) throw new Error("expected " + item[2] + " got " + href);
      }
      ws.close();
    });
  } finally {
    started.server.close();
  }
}

main().catch(function (error) {
  console.error(error);
  process.exit(1);
});
