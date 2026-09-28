// GameCore addons — one icon set, 24 px grid, 1.75 stroke (see shared/ui/DESIGN.md).
// gcIcon(name, extraClass) returns inline SVG markup; no emoji is used as an icon.
(function () {
  const P = {
    download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    upload: '<path d="M12 20V9"/><path d="m7 14 5-5 5 5"/><path d="M5 4h14"/>',
    trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    restore: '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v5h5"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    save: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4"/><path d="M8 20v-6h8v6"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    card: '<path d="M6 3h9l3 3v15H6z"/><path d="M9 3v4"/><path d="M12 3v4"/><path d="M15 3v4"/>',
    state: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    history: '<path d="M12 7v5l3 2"/><circle cx="12" cy="12" r="8"/>',
    gamepad: '<path d="M7 8h10a4 4 0 0 1 4 4v1a3 3 0 0 1-5.4 1.8L14 13h-4l-1.6 1.8A3 3 0 0 1 3 13v-1a4 4 0 0 1 4-4z"/><path d="M7 11v2"/><path d="M6 12h2"/><path d="M16 11h.01"/><path d="M18 13h.01"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    alert: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
    book: '<path d="M5 4h9a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h9"/>',
    sliders: '<path d="M4 7h10"/><path d="M18 7h2"/><circle cx="16" cy="7" r="2"/><path d="M4 17h2"/><path d="M10 17h10"/><circle cx="8" cy="17" r="2"/>',
    patch: '<path d="M8 3h8v5l3 3v10H5V11l3-3z"/><path d="M9 14h6"/><path d="M12 11v6"/>',
    image: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 17-5-5-9 7"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/>',
    close: '<path d="m6 6 12 12"/><path d="M18 6 6 18"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
  };
  window.gcIcon = function (name, cls) {
    const body = P[name] || P.alert;
    return `<svg class="icon${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
  };
})();
