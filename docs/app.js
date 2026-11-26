/* Progressive enhancement only — strictly zero CSS/style manipulation */
(function () {
  "use strict";

  var REPO = "alexanderfrit/tiktok-streak";
  var API_LATEST = "https://api.github.com/repos/" + REPO + "/releases/latest";

  function loadWebFonts() {
    if (typeof window.FontFace !== "function" || !document.fonts || typeof document.fonts.add !== "function") {
      return;
    }
    var fonts = [
      new FontFace("Google Sans", "url(fonts/google-sans-latin.woff2)", {
        weight: "400 700",
        style: "normal",
        display: "swap"
      }),
      new FontFace("Google Sans Code", "url(fonts/google-sans-code-latin.woff2)", {
        weight: "300 800",
        style: "normal",
        display: "swap"
      })
    ];
    for (var i = 0; i < fonts.length; i++) {
      (function (face) {
        face
          .load()
          .then(function (loaded) {
            document.fonts.add(loaded);
          })
          .catch(function () {
            /* Fall back gracefully to system HTML font stack. */
          });
      })(fonts[i]);
    }
  }

  function applyCodeFontTags() {
    var nodes = document.getElementsByTagName("code");
    for (var i = 0; i < nodes.length; i++) {
      var codeEl = nodes[i];
      if (codeEl.querySelector("font")) {
        continue;
      }
      var wrapper = document.createElement("font");
      wrapper.setAttribute("face", "Google Sans Code, Consolas, 'Courier New', monospace");
      while (codeEl.firstChild) {
        wrapper.appendChild(codeEl.firstChild);
      }
      codeEl.appendChild(wrapper);
    }
  }

  loadWebFonts();

  function formatBytesMB(bytes) {
    if (!bytes || isNaN(bytes)) {
      return "";
    }
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function hydrateLatestRelease() {
    var linkEl = document.getElementById("release-link");
    var verEl = document.getElementById("release-version");
    var metaEl = document.getElementById("release-meta");
    var shaEl = document.getElementById("release-sha256");
    if (!linkEl || typeof window.fetch !== "function") {
      return;
    }

    fetch(API_LATEST, {
      headers: { Accept: "application/vnd.github+json" }
    })
      .then(function (resp) {
        if (!resp.ok) {
          return null;
        }
        return resp.json();
      })
      .then(function (data) {
        if (!data || !data.tag_name) {
          return;
        }
        var tag = String(data.tag_name).trim();
        var cleanVer = tag.replace(/^[vV]/, "");
        if (verEl) {
          verEl.textContent = "v" + cleanVer;
        }
        var exeAsset = null;
        var assets = Array.isArray(data.assets) ? data.assets : [];
        for (var i = 0; i < assets.length; i++) {
          if (assets[i] && assets[i].name === "TikTokStreakSetup.exe") {
            exeAsset = assets[i];
            break;
          }
        }
        if (exeAsset && exeAsset.browser_download_url) {
          linkEl.href = exeAsset.browser_download_url;
          var parts = [];
          if (exeAsset.size) {
            parts.push(formatBytesMB(exeAsset.size));
          }
          if (typeof exeAsset.download_count === "number") {
            parts.push(exeAsset.download_count.toLocaleString() + " downloads");
          }
          if (data.published_at) {
            parts.push("Published " + String(data.published_at).slice(0, 10));
          }
          if (metaEl && parts.length > 0) {
            metaEl.textContent = parts.join(" \u00b7 ");
          }
          if (shaEl && exeAsset.digest) {
            shaEl.textContent = String(exeAsset.digest).replace(/^sha256:/i, "");
          }
        }
      })
      .catch(function () {
        /* Fail silently; static v1.4.0 HTML links remain intact. */
      });
  }

  function convertTimesToUtcCrons(rawTimes, offsetMinutes) {
    var tokens = String(rawTimes || "")
      .split(/[,\s;]+/)
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);

    var byMinute = {};
    var parsedTimes = [];
    var hasTopOfHour = false;

    for (var i = 0; i < tokens.length; i++) {
      var m = /^(\d{1,2}):(\d{2})$/.exec(tokens[i]);
      if (!m) {
        continue;
      }
      var hh = parseInt(m[1], 10);
      var mm = parseInt(m[2], 10);
      if (hh < 0 || hh > 23 || mm < 0 || mm > 59) {
        continue;
      }
      if (mm === 0) {
        hasTopOfHour = true;
      }
      var totalUtc = ((hh * 60 + mm - offsetMinutes) % 1440 + 1440) % 1440;
      var utcH = Math.floor(totalUtc / 60);
      var utcM = totalUtc % 60;
      if (!byMinute[utcM]) {
        byMinute[utcM] = [];
      }
      if (byMinute[utcM].indexOf(utcH) === -1) {
        byMinute[utcM].push(utcH);
      }
      parsedTimes.push(
        String(hh).padStart(2, "0") + ":" + String(mm).padStart(2, "0")
      );
    }

    var minutes = Object.keys(byMinute)
      .map(function (k) {
        return parseInt(k, 10);
      })
      .sort(function (a, b) {
        return a - b;
      });

    var crons = [];
    for (var j = 0; j < minutes.length; j++) {
      var minKey = minutes[j];
      var hrs = byMinute[minKey].slice().sort(function (a, b) {
        return a - b;
      });
      crons.push(minKey + " " + hrs.join(",") + " * * *");
    }

    return {
      parsed: parsedTimes,
      crons: crons,
      hasTopOfHour: hasTopOfHour
    };
  }

  function initCronCalculator() {
    var timesInput = document.getElementById("calc-times");
    var tzSelect = document.getElementById("calc-tz");
    var outEl = document.getElementById("calc-output");
    var warnEl = document.getElementById("calc-warning");
    if (!timesInput || !tzSelect || !outEl) {
      return;
    }

    function update() {
      var offsetMin = parseInt(tzSelect.value || "420", 10);
      var res = convertTimesToUtcCrons(timesInput.value, offsetMin);
      if (res.crons.length === 0) {
        outEl.textContent = "Enter valid HH:MM times (e.g. 08:15, 20:15)";
        if (warnEl) {
          warnEl.textContent = "";
        }
        return;
      }
      var lines = ["on:", "  schedule:"];
      for (var i = 0; i < res.crons.length; i++) {
        lines.push("    - cron: '" + res.crons[i] + "'");
      }
      outEl.textContent = lines.join("\n");
      if (warnEl) {
        warnEl.textContent = res.hasTopOfHour
          ? "Warning: Top-of-hour (:00) GitHub cron schedules often delay 15m to 4+ hours. Prefer :15 or :17."
          : "Converted " + res.parsed.join(", ") + " to off-peak UTC GitHub Actions cron.";
      }
    }

    timesInput.addEventListener("input", update);
    tzSelect.addEventListener("change", update);
    update();
  }

  function initFaqControls() {
    var toggleBtn = document.getElementById("faq-toggle-all");
    var faqSection = document.getElementById("faq");
    if (!toggleBtn || !faqSection) {
      return;
    }
    var allExpanded = false;
    toggleBtn.addEventListener("click", function () {
      var details = faqSection.getElementsByTagName("details");
      allExpanded = !allExpanded;
      for (var i = 0; i < details.length; i++) {
        details[i].open = allExpanded;
      }
      toggleBtn.textContent = allExpanded ? "Collapse All Answers" : "Expand All Answers";
    });
  }

  function initCopyButtons() {
    var buttons = document.querySelectorAll("[data-copy-target]");
    for (var i = 0; i < buttons.length; i++) {
      (function (btn) {
        btn.addEventListener("click", function () {
          var targetId = btn.getAttribute("data-copy-target");
          var targetEl = targetId ? document.getElementById(targetId) : null;
          if (!targetEl) {
            return;
          }
          var txt = targetEl.textContent || "";
          if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
            navigator.clipboard.writeText(txt).then(function () {
              var orig = btn.textContent;
              btn.textContent = "Copied!";
              setTimeout(function () {
                btn.textContent = orig;
              }, 1600);
            });
          }
        });
      })(buttons[i]);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyCodeFontTags();
    hydrateLatestRelease();
    initCronCalculator();
    initFaqControls();
    initCopyButtons();
  });
})();
