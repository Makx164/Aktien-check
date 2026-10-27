/* Schreibt die statischen Weiterleitungsseiten nach docs/. */
var fs = require("fs");
var path = require("path");

var root = path.join(__dirname, "..");
var logicPath = path.join(__dirname, "pages_redirect_logic.js");
var logic = fs.readFileSync(logicPath, "utf8").replace(/\n*if \(typeof module[\s\S]*$/, "\n");
var tickers = require(logicPath).SIDEQUEST_TICKERS;

function page(opts) {
  var href = opts.href;
  return "<!DOCTYPE html>\n" +
    "<html lang=\"de\">\n" +
    "<head>\n" +
    "  <meta charset=\"utf-8\">\n" +
    "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n" +
    "  <script>\n" +
    logic +
    "  (function () {\n" +
    "    var target = \"https://yoursidequest.org/\";\n" +
    "    try { target = sidequestTarget(location); } catch (e) {}\n" +
    "    if (typeof target !== \"string\" || target.indexOf(\"https://yoursidequest.org/\") !== 0) {\n" +
    "      target = \"https://yoursidequest.org/\";\n" +
    "    }\n" +
    "    try {\n" +
    "      if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {\n" +
    "        navigator.serviceWorker.getRegistrations().then(function (regs) {\n" +
    "          regs.forEach(function (reg) { reg.unregister(); });\n" +
    "        }).catch(function () {});\n" +
    "      }\n" +
    "    } catch (e) {}\n" +
    "    location.replace(target);\n" +
    "  })();\n" +
    "  </script>\n" +
    "  <title>" + opts.title + "</title>\n" +
    "  <meta http-equiv=\"refresh\" content=\"0; url=" + href + "\">\n" +
    "  <link rel=\"canonical\" href=\"" + href + "\">\n" +
    "  <meta name=\"robots\" content=\"noindex\">\n" +
    "  <style>\n" +
    "    body{margin:0;min-height:100vh;display:grid;place-items:center;background:#c6c7c1;color:#111214;font:16px/1.5 system-ui,sans-serif}\n" +
    "    main{max-width:32rem;padding:24px}\n" +
    "    a{color:#c41c3a}\n" +
    "  </style>\n" +
    "</head>\n" +
    "<body>\n" +
    "  <main>\n" +
    "    <h1>Diese Seite ist umgezogen</h1>\n" +
    "    <p>" + opts.text + " <a href=\"" + href + "\">" + opts.link + "</a></p>\n" +
    "  </main>\n" +
    "</body>\n" +
    "</html>\n";
}

function writePages(dir) {
  fs.mkdirSync(dir, { recursive: true });
  var pages = [
    ["index.html", {
      href: "https://yoursidequest.org/",
      title: "Weiterleitung · sidequest",
      text: "sidequest liegt jetzt unter",
      link: "yoursidequest.org"
    }],
    ["impressum.html", {
      href: "https://yoursidequest.org/impressum",
      title: "Impressum · Weiterleitung · sidequest",
      text: "Das Impressum liegt jetzt unter",
      link: "yoursidequest.org/impressum"
    }],
    ["datenschutz.html", {
      href: "https://yoursidequest.org/datenschutz",
      title: "Datenschutz · Weiterleitung · sidequest",
      text: "Die Datenschutzerklärung liegt jetzt unter",
      link: "yoursidequest.org/datenschutz"
    }],
    ["404.html", {
      href: "https://yoursidequest.org/",
      title: "Seite nicht gefunden · Weiterleitung · sidequest",
      text: "Diese Adresse gibt es hier nicht mehr. Weiter geht es auf",
      link: "yoursidequest.org"
    }]
  ];
  tickers.forEach(function (ticker) {
    pages.push([ticker + ".html", {
      href: "https://yoursidequest.org/#/" + ticker,
      title: ticker + " · Weiterleitung · sidequest",
      text: ticker + " liegt jetzt unter",
      link: "yoursidequest.org/#/" + ticker
    }]);
  });
  pages.forEach(function (entry) {
    fs.writeFileSync(path.join(dir, entry[0]), page(entry[1]));
  });
  fs.writeFileSync(path.join(dir, ".nojekyll"), "");
  fs.copyFileSync(path.join(root, "sw.js"), path.join(dir, "sw.js"));
  fs.copyFileSync(path.join(root, "service-worker.js"), path.join(dir, "service-worker.js"));
}

if (require.main === module) {
  writePages(path.join(root, "docs"));
}

module.exports = { writePages: writePages, page: page };
