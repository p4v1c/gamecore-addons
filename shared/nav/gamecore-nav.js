/*
 * GameCore — shared addon navigation bar.
 *
 * Drop this file (with gamecore-nav.css) in your addon's web root and add:
 *   <link rel="stylesheet" href="gamecore-nav.css">
 *   <script src="gamecore-nav.js" defer></script>
 *
 * It asks the GameCore core (port 8765 by convention) for the installed
 * addons and prepends a nav bar linking every web addon. Ports are an
 * implementation detail: links are built from location.hostname so the
 * user only ever sees one "site" with sections.
 *
 * Core port override (if the core ever moves): ?gcport=XXXX once, it is
 * persisted in localStorage.
 */
(function () {
  'use strict';

  var qp = new URLSearchParams(location.search).get('gcport');
  if (qp) { try { localStorage.setItem('gamecore.corePort', qp); } catch (e) { /* private mode */ } }
  var corePort = qp || (function () {
    try { return localStorage.getItem('gamecore.corePort'); } catch (e) { return null; }
  })() || '8765';

  var coreBase = location.protocol + '//' + location.hostname + ':' + corePort;

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

    nav.appendChild(link(coreBase + '/', 'Home', false));

    addons
      .filter(function (a) { return a.type === 'web' && a.port; })
      .forEach(function (a) {
        var href = location.protocol + '//' + location.hostname + ':' + a.port + '/';
        var active = String(a.port) === location.port;
        nav.appendChild(link(href, a.label || a.name, active));
      });

    document.body.prepend(nav);
    document.body.classList.add('gc-nav-present');
  }

  fetch(coreBase + '/api/addons')
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (addons) { render(Array.isArray(addons) ? addons : []); })
    .catch(function () { render([]); }); // core unreachable → bare bar, addon still usable
})();
