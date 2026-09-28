# Addon look

The addon pages are part of GameCore, so they borrow its identity
(`frontend/src/DESIGN.md` in GamecoreRenew): a quiet dark console where the
games carry the colour. Covers, icons and system logos are the only loud
things on a page; the chrome stays grey and gets out of the way.

Files here are copied into each addon's `web/` by its `install.sh`, so an
addon stays self-contained and still looks like the others.

| File | What |
|---|---|
| `gamecore-ui.css` | tokens and shared components |
| `gamecore-icons.js` | `gcIcon(name, cls)`, one drawn icon set |
| `fonts/` | Source Sans 3, variable, OFL |

## Type

Source Sans 3, self-hosted (the box may be offline). Body 16px, secondary
text 14px, nothing smaller. Titles are weight 700 in sentence case; no
tracked uppercase labels. Numbers are tabular so sizes and counts line up.

## Palette

One accent, ember `#b8501b`, for the primary action on a page and the
selected state (`--accent-soft` `#f2a46a` for thin marks and focus). Status
colours only carry meaning: green for "this game has its own settings",
red for anything that deletes, amber for warnings.

Contrast (WCAG), measured on the three surfaces `--bg` / `--surface` / `--raised`:

| Token | Ratio |
|---|---|
| `--ink` #fff | 19.9 / 18.6 / 17.3 |
| `--ink-2` 78 % | 12.0 / 11.5 / 10.9 |
| `--ink-3` 60 % | 7.3 / 7.1 / 6.9 |
| `--accent-soft` | 9.7 / 9.1 / 8.5 |
| `--danger` | 8.7 / 8.2 / 7.6 |
| white on `--accent` | 5.0 |

Every text pair passes AA; `--ink-3` is the floor for any text.

## Shape and depth

Controls 8px radius, cards and lists 12px, covers 6px. Depth comes from
three flat surface steps and 1px lines, not shadows; only toasts float.
A list is one bordered block with hairlines between rows, not a stack of
separate cards.

## Layout

A rail of systems or games on the left, the page beside it. Under 760px the
rail turns into a horizontal strip above the page. The shared nav bar sits
on top (48px) when the core's `/api/addons` answers.

## Motion

Almost none: a spinner while loading, a 150ms switch slide. The spinner
slows down under `prefers-reduced-motion`.

## Icons

Drawn SVG, 24px grid, 1.75 stroke, `currentColor`. Never emoji. Add a
missing one to `gamecore-icons.js` rather than inlining a one-off.

## Copy

Say what happens, in plain words: "Install an update or DLC", "Use global
settings", "Couldn't read the saves: ...". No em dashes or middle dots in
UI strings, no exclamation marks, no marketing adjectives. Errors name what
failed and pass the server's reason through.
