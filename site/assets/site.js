/* Small page behaviour: nav border on scroll, gentle reveals, lazy videos and demo, lightbox. */
(function () {
  "use strict";
  var doc = document.documentElement;
  doc.classList.remove("no-js");
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var nav = document.querySelector(".nav");
  if (nav) {
    var onScroll = function () { nav.classList.toggle("scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  if (!("IntersectionObserver" in window)) {
    Array.prototype.forEach.call(document.querySelectorAll(".reveal"), function (el) { el.classList.add("in"); });
  } else {
    var ro = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); ro.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    Array.prototype.forEach.call(document.querySelectorAll(".reveal"), function (el) { ro.observe(el); });

    // Looping clips play only while on screen, and never with reduced motion.
    var vo = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var v = e.target;
        if (e.isIntersecting) {
          if (!v.dataset.loaded) {
            Array.prototype.forEach.call(v.querySelectorAll("source[data-src]"), function (s) { s.src = s.dataset.src; });
            v.load();
            v.dataset.loaded = "1";
          }
          if (!reduced) { var p = v.play(); if (p && p.catch) p.catch(function () {}); }
        } else if (!v.paused) v.pause();
      });
    }, { rootMargin: "200px 0px" });
    Array.prototype.forEach.call(document.querySelectorAll("video[data-lazy]"), function (v) { vo.observe(v); });

    // The demo script loads when the demo is about to scroll into view.
    var demo = document.getElementById("demo");
    if (demo) {
      var load = function () {
        if (demo.dataset.loaded) return;
        demo.dataset.loaded = "1";
        var s = document.createElement("script");
        s.src = demo.getAttribute("data-script");
        s.defer = true;
        document.body.appendChild(s);
      };
      // Start WebGL only when the demo is about to be seen, and when the browser is idle.
      var idle = window.requestIdleCallback || function (f) { return setTimeout(f, 1); };
      var dobs = new IntersectionObserver(function (es) {
        if (es[0].isIntersecting) { idle(load, { timeout: 400 }); dobs.disconnect(); }
      }, { rootMargin: "0px 0px 160px 0px" });
      dobs.observe(demo);
      if (location.hash === "#demo") load();
    }
  }

  // Lightbox for the screenshot gallery.
  var box = document.querySelector(".lightbox");
  if (box) {
    var img = box.querySelector("img"), cap = box.querySelector("p"), closeBtn = box.querySelector(".close");
    var opener = null;
    var close = function () {
      box.classList.remove("open");
      box.setAttribute("aria-hidden", "true");
      box.setAttribute("inert", "");
      if (opener) opener.focus();
    };
    Array.prototype.forEach.call(document.querySelectorAll("[data-zoom]"), function (b) {
      b.addEventListener("click", function () {
        opener = b;
        img.src = b.getAttribute("data-zoom");
        img.alt = b.querySelector("img").alt;
        img.width = +b.getAttribute("data-w");
        img.height = +b.getAttribute("data-h");
        cap.textContent = b.getAttribute("data-caption") || "";
        box.removeAttribute("inert");
        box.classList.add("open");
        box.setAttribute("aria-hidden", "false");
        closeBtn.focus();
      });
    });
    closeBtn.addEventListener("click", close);
    box.addEventListener("click", function (e) { if (e.target === box) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && box.classList.contains("open")) close(); });
  }

  // Docs sidebar: mark the section being read.
  var links = document.querySelectorAll(".doc aside a[href^='#']");
  if (links.length && "IntersectionObserver" in window) {
    var map = {};
    Array.prototype.forEach.call(links, function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var so = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && map[e.target.id]) {
          Array.prototype.forEach.call(links, function (a) { a.classList.remove("here"); });
          map[e.target.id].classList.add("here");
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    Object.keys(map).forEach(function (id) { var el = document.getElementById(id); if (el) so.observe(el); });
  }
})();
