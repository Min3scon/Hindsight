/* Hindsight website: navigation, scroll progress, reveal-on-scroll, active section.
   No dependencies. Everything degrades to a plain, fully visible page without it. */
(function () {
  "use strict";

  var doc = document.documentElement;
  doc.classList.add("js");

  var reduceMotion = false;
  try {
    reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (e) { /* old browsers */ }

  /* ---------- Mobile section menu ---------- */
  var toggle = document.querySelector(".nav-toggle");
  var links = document.getElementById("nav-links");

  function setMenu(open, focusToggle) {
    if (!toggle || !links) return;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Close the section menu" : "Open the section menu");
    links.classList.toggle("is-open", open);
    if (!open && focusToggle) toggle.focus();
  }

  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") !== "true";
      setMenu(open, false);
      if (open) {
        var first = links.querySelector("a");
        if (first) first.focus({ preventScroll: true });
      }
    });
    links.addEventListener("click", function (ev) {
      if (ev.target.closest("a")) setMenu(false, false);
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") setMenu(false, true);
    });
    document.addEventListener("click", function (ev) {
      if (toggle.getAttribute("aria-expanded") !== "true") return;
      if (!ev.target.closest(".nav-menu") && !ev.target.closest(".nav-toggle")) setMenu(false, false);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 1120) setMenu(false, false);
    });
  }

  /* ---------- Scroll progress along the bottom of the nav (a page-sized scrubber) ---------- */
  var bar = document.querySelector(".nav-progress span");
  var ticking = false;
  function updateProgress() {
    ticking = false;
    if (!bar) return;
    var max = doc.scrollHeight - window.innerHeight;
    var p = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
    bar.style.setProperty("--p", p.toFixed(4));
  }
  window.addEventListener("scroll", function () {
    if (!ticking) { ticking = true; window.requestAnimationFrame(updateProgress); }
  }, { passive: true });
  window.addEventListener("resize", updateProgress);
  updateProgress();

  /* ---------- Reveal on scroll ---------- */
  var revealEls = Array.prototype.slice.call(document.querySelectorAll(".reveal"));
  // Stagger children of grids marked data-stagger, in rows of up to three.
  Array.prototype.forEach.call(document.querySelectorAll("[data-stagger]"), function (grid) {
    Array.prototype.forEach.call(grid.children, function (child, i) {
      child.style.setProperty("--d", ((i % 3) * 0.07).toFixed(2) + "s");
    });
  });

  if (!reduceMotion && "IntersectionObserver" in window && revealEls.length) {
    doc.classList.add("reveal-on");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    revealEls.forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) el.classList.add("is-in");
      else io.observe(el);
    });
    // Anything jumped past via an anchor link should not stay hidden.
    window.addEventListener("hashchange", function () {
      revealEls.forEach(function (el) {
        var r = el.getBoundingClientRect();
        if (r.top < window.innerHeight) el.classList.add("is-in");
      });
    });
  } else {
    revealEls.forEach(function (el) { el.classList.add("is-in"); });
  }

  /* ---------- Highlight the section in view ---------- */
  if ("IntersectionObserver" in window && links) {
    var navAnchors = {};
    Array.prototype.forEach.call(links.querySelectorAll('a[href^="#"]'), function (a) {
      navAnchors[a.getAttribute("href").slice(1)] = a;
    });
    var current = null;
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var a = navAnchors[entry.target.id];
        if (!a || a === current) return;
        if (current) current.removeAttribute("aria-current");
        a.setAttribute("aria-current", "true");
        current = a;
      });
    }, { rootMargin: "-45% 0px -50% 0px", threshold: 0 });
    Object.keys(navAnchors).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) spy.observe(el);
    });
  }
})();
