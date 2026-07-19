/*
 * GameCore — shared addon navigation bar.
 *
 * Drop this file (with gamecore-nav.css) in your addon's web root and add:
 *   <link rel="stylesheet" href="gamecore-nav.css">
 *   <script src="gamecore-nav.js" defer></script>
 *
 * Everything is served on ONE origin behind the Caddy reverse-proxy
 * (https://box:8443): the core answers /gc/addons and each web addon
 * lives under its own path prefix (addon.json "path", e.g. /roms).
 * Links and the registry fetch are origin-relative — no host, no port.
 *
 * Accessed directly on the addon's loopback port (dev), /gc/addons does
 * not exist: the bar renders bare and the addon stays usable.
 */
(function () {
  'use strict';

  function link(href, label, active) {
    var a = document.createElement('a');
    a.href = href;
    a.textContent = label;
    a.className = 'gc-nav-item' + (active ? ' active' : '');
    return a;
  }

  function render(addons) {
    var nav = document.createElement('nav');
    nav.className = 'gc-nav';

    var brand = document.createElement('span');
    brand.className = 'gc-nav-brand';
    brand.textContent = 'GameCore';
    nav.appendChild(brand);

    nav.appendChild(link('/', 'Home', false));

    addons
      .filter(function (a) { return a.type === 'web' && a.path; })
      .forEach(function (a) {
        var href = a.path.replace(/\/+$/, '') + '/';
        var active = location.pathname === href || location.pathname.indexOf(href) === 0;
        nav.appendChild(link(href, a.label || a.name, active));
      });

    document.body.prepend(nav);
    document.body.classList.add('gc-nav-present');
  }

  fetch('/gc/addons')
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (addons) { render(Array.isArray(addons) ? addons : []); })
    .catch(function () { render([]); }); // registry unreachable → bare bar, addon still usable
})();
