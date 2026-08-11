/* Shared interaction layer for the v0.28 documentation shell. */
(function () {
  "use strict";

  var root = document.documentElement;

  function currentTheme() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function storeTheme(theme) {
    try { localStorage.setItem("bsq-theme", theme); } catch (error) { /* storage may be blocked */ }
  }

  function buildSidebarShell() {
    var sidebar = document.querySelector(".sidebar");
    var brand = sidebar && sidebar.querySelector(".brand");
    var menu = sidebar && sidebar.querySelector(".menu-toggle");
    var nav = sidebar && sidebar.querySelector("nav");
    if (!sidebar || !brand || !menu || !nav) return null;

    var header = document.createElement("div");
    header.className = "sidebar-header";
    sidebar.insertBefore(header, brand);
    header.appendChild(brand);

    var actions = document.createElement("div");
    actions.className = "sidebar-actions";
    header.appendChild(actions);

    var theme = document.createElement("button");
    theme.type = "button";
    theme.className = "theme-toggle";
    actions.appendChild(theme);
    actions.appendChild(menu);

    return { sidebar: sidebar, menu: menu, navList: nav.querySelector(".nav"), theme: theme };
  }

  function initTheme(button) {
    function update() {
      var dark = currentTheme() === "dark";
      var destination = dark ? "light" : "dark";
      button.innerHTML = '<span class="theme-glyph" aria-hidden="true">◐</span><span class="theme-label">' + (dark ? "Light theme" : "Dark theme") + "</span>";
      button.setAttribute("aria-label", "Switch to " + destination + " theme");
      button.setAttribute("aria-pressed", String(dark));
      button.title = "Switch to " + destination + " theme";
    }

    update();
    button.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      if (next === "dark") root.setAttribute("data-theme", "dark");
      else root.removeAttribute("data-theme");
      storeTheme(next);
      update();
    });
  }

  function initMenu(shell) {
    var button = shell.menu;
    var nav = shell.navList;
    if (!nav) return;

    if (!nav.id) nav.id = "documentation-navigation";
    button.textContent = "Menu";
    button.setAttribute("aria-controls", nav.id);
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "Open documentation menu");

    function setOpen(open, returnFocus) {
      nav.classList.toggle("open", open);
      shell.sidebar.classList.toggle("nav-expanded", open);
      button.setAttribute("aria-expanded", String(open));
      button.setAttribute("aria-label", (open ? "Close" : "Open") + " documentation menu");
      if (!open && returnFocus) button.focus();
    }

    button.addEventListener("click", function () {
      setOpen(!nav.classList.contains("open"), false);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && nav.classList.contains("open")) {
        event.preventDefault();
        setOpen(false, true);
      }
    });

    nav.addEventListener("click", function (event) {
      if (event.target.closest("a") && window.matchMedia("(max-width: 880px)").matches) setOpen(false, false);
    });

    var wide = window.matchMedia("(min-width: 881px)");
    function closeAtDesktop(event) {
      if (event.matches) setOpen(false, false);
    }
    if (typeof wide.addEventListener === "function") wide.addEventListener("change", closeAtDesktop);
    else if (typeof wide.addListener === "function") wide.addListener(closeAtDesktop);
  }

  function markCurrentPage() {
    var current = (location.pathname.split("/").pop() || "index.html").toLowerCase();
    var links = document.querySelectorAll(".nav a");
    for (var i = 0; i < links.length; i++) {
      var target = (links[i].getAttribute("href") || "").split("#")[0].toLowerCase();
      if (target === current) links[i].setAttribute("aria-current", "page");
      else links[i].removeAttribute("aria-current");
    }
  }

  function prepareFigures() {
    var figures = document.querySelectorAll("figure");
    for (var i = 0; i < figures.length; i++) {
      var image = figures[i].querySelector("img");
      if (!image) continue;
      var source = image.getAttribute("src") || "";
      if (source.indexOf("screenshot-") !== -1) figures[i].classList.add("app-capture");
      if (!image.hasAttribute("loading")) image.setAttribute("loading", "lazy");
      image.setAttribute("decoding", "async");
    }
  }

  function buildFaqAccordions() {
    var faq = document.querySelector("#faq");
    if (!faq) return;
    var questions = Array.prototype.slice.call(faq.querySelectorAll(":scope > h3, :scope > h4"));
    for (var i = 0; i < questions.length; i++) {
      var heading = questions[i];
      var parent = heading.parentNode;
      if (!parent) continue;

      var details = document.createElement("details");
      details.className = "faq-item";
      var summary = document.createElement("summary");
      summary.textContent = heading.textContent;
      var answer = document.createElement("div");
      answer.className = "faq-answer";

      parent.insertBefore(details, heading);
      details.appendChild(summary);
      details.appendChild(answer);

      var node = heading.nextSibling;
      heading.remove();
      while (node) {
        var next = node.nextSibling;
        if (node.nodeType === 1 && /^(H2|H3|H4)$/.test(node.tagName)) break;
        answer.appendChild(node);
        node = next;
      }
    }
  }

  function init() {
    var main = document.querySelector("main.content");
    if (main) main.setAttribute("tabindex", "-1");
    var shell = buildSidebarShell();
    if (shell) {
      initTheme(shell.theme);
      initMenu(shell);
    }
    markCurrentPage();
    prepareFigures();
    buildFaqAccordions();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
