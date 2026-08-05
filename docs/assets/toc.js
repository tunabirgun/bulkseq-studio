/* BulkSeq Studio docs — in-page "On this page" navigation.
   Vanilla JS, no dependencies, no external requests. Collects the current page's
   <h2>/<h3> elements inside <main class="content">, gives any that are missing an id
   a slugified one, and builds a nested list inserted into the sidebar below the main
   nav (and the external-links block). The section in view is highlighted with
   IntersectionObserver — no scroll listeners. Pages with fewer than 3 headings get
   no TOC at all. */
(function () {
  "use strict";

  function slugify(text) {
    var slug = (text || "")
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return slug || "section";
  }

  // Returns a version of `base` not already present in `used`, reserving it as a side effect.
  function reserveId(base, used) {
    var id = base, n = 2;
    while (used[id]) { id = base + "-" + n; n++; }
    used[id] = true;
    return id;
  }

  function collectHeadings(content) {
    var nodes = content.querySelectorAll("h2, h3");
    if (nodes.length < 3) return [];

    var used = {};
    var existing = document.querySelectorAll("[id]");
    for (var i = 0; i < existing.length; i++) used[existing[i].id] = true;

    var headings = [];
    for (var j = 0; j < nodes.length; j++) {
      var h = nodes[j];
      if (h.id) {
        used[h.id] = true;
      } else {
        h.id = reserveId(slugify(h.textContent), used);
      }
      headings.push({ el: h, level: h.tagName === "H2" ? 2 : 3, id: h.id, text: h.textContent || "" });
    }
    return headings;
  }

  function buildList(headings) {
    var list = document.createElement("ul");
    list.className = "toc-list";

    var topLi = null, subUl = null;
    var links = [];

    for (var i = 0; i < headings.length; i++) {
      var h = headings[i];
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#" + h.id;
      a.textContent = h.text;
      li.appendChild(a);
      links.push({ id: h.id, a: a });

      if (h.level === 2) {
        list.appendChild(li);
        topLi = li;
        subUl = null;
      } else if (topLi) {
        if (!subUl) {
          subUl = document.createElement("ul");
          topLi.appendChild(subUl);
        }
        subUl.appendChild(li);
      } else {
        // defensive: an h3 before any h2 on the page still gets a place in the list
        list.appendChild(li);
      }
    }
    return { list: list, links: links };
  }

  function observeSections(headings, links) {
    if (!("IntersectionObserver" in window) || !headings.length) return;

    var visible = {};

    function setActive(id) {
      for (var i = 0; i < links.length; i++) {
        var isActive = links[i].id === id;
        links[i].a.classList.toggle("toc-active", isActive);
        if (isActive) links[i].a.setAttribute("aria-current", "true");
        else links[i].a.removeAttribute("aria-current");
      }
    }

    var observer = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        visible[entries[i].target.id] = entries[i].isIntersecting;
      }
      // the current section is the LAST heading (in document order) still inside the
      // trigger band — i.e. the most recent heading you've scrolled past.
      var activeId = null;
      for (var j = 0; j < headings.length; j++) {
        if (visible[headings[j].id]) activeId = headings[j].id;
      }
      if (activeId) setActive(activeId);
    }, {
      root: null,
      rootMargin: "0px 0px -70% 0px", // trigger band = top ~30% of the viewport
      threshold: 0
    });

    for (var k = 0; k < headings.length; k++) observer.observe(headings[k].el);
  }

  function init() {
    var content = document.querySelector(".content");
    var nav = document.querySelector(".sidebar nav");
    if (!content || !nav) return;

    var headings = collectHeadings(content);
    if (!headings.length) return; // fewer than 3 headings — render nothing

    var built = buildList(headings);

    var toc = document.createElement("div");
    toc.className = "toc";
    var title = document.createElement("p");
    title.className = "toc-title";
    title.textContent = "On this page";
    toc.appendChild(title);
    toc.appendChild(built.list);

    var ext = nav.querySelector(".ext");
    if (ext) ext.insertAdjacentElement("afterend", toc);
    else nav.appendChild(toc);

    observeSections(headings, built.links);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
