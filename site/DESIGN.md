# Site design notes

The GitHub Pages site for Railhead and Waybill. Static HTML, CSS and JS in `site/`; no build step.

## Preview locally

```bash
cd site && python3 -m http.server 8000   # then open http://localhost:8000
```

`site/assets/{img,video,data}` are symlinks to `docs/{img,video,data}` for local work only.
The Pages workflow (`.github/workflows/pages.yml`) replaces them with real copies of an allowlist of files.
Reference media only through `assets/...` (or `img/...` for the small derived WebP files in `site/img/`).

## Subject and job

OpenTTD as a lab where AI agents run companies. Two halves of equal weight:
Railhead (one company, scored by replay from a sealed start) and Waybill (four companies, binding contracts
settled by replay). The page is a research article you can play with: every figure is real data from
`docs/data` or a real rule from `src/railhead/exchange`, and every interactive explains one mechanism.

## Tokens

| Token | Hex | Role |
|---|---|---|
| ballast | `#141513` | page |
| sleeper | `#1C1E1B` | raised panels, figure plates |
| sleeper-2 | `#242722` | hover, inputs, active pills |
| hairline | `#2E312C` | rules, grid lines |
| chalk | `#EEEDE7` | text, selected series |
| gravel | `#A8AB9F` | secondary text (7.9:1 on ballast) |
| copper | `#D98A4E` | Railhead accent (UI only: links, active state, focus, volume label) |
| signal | `#D8AE66` | Waybill accent (switches in with `.vol-waybill`) |

The page is dark only (`color-scheme: dark`): the two reference sites the owner made are dark,
and the replay video and the cinema still are dark-theme renders.

Data colours (validated with the dataviz validator on `#141513` and `#1C1E1B`, `--pairs all`):
claude-opus-5-5 `#5b93dc`, gpt-6-astra `#d9733f`, gpt-6-sol `#2fa07a` (worst CVD ΔE 8.5, normal 17.1).
All other models are `#5E6159` and become chalk when selected. Only three hues, because the seed-game dot plot
needs them all-pairs, and no fourth hue clears the floors next to blue and orange.
Transport modes appear only in the route maps and the video (their own legends); the fleet figure uses
one neutral hue with labels, so mode hues never compete with model hues.
Status (verification table, promise outcomes) always pairs colour with a word and a glyph.

## Type

- General Sans (Fontshare) 400/500/600 for everything readable. Display: 500, tracking -0.045em, line-height 0.95.
- IBM Plex Mono 400/500 only for numbers in figures, code, figure numbers, and the volume labels.
- Scale (1.25, from 17px body): 13.6 / 17 / 21.25 / 26.5 / 33 / 41.5 / 52 / 80 (h1, clamp).
- Agent voice: General Sans italic, 19px, with a 2px left rule in the volume accent.
- Sentence case everywhere. The mono label with a short rule ("—— Railhead") marks the two volumes only: in the rail, on the two entry cards and at the head of each volume (six places, two words).

## Layout

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │ Railhead & Waybill                                         GitHub ↗    │  top bar (hairline)
 ├──────────┬─────────────────────────────────────────────────────────────┤
 │          │ Title, dek                                                  │
 │ Contents │ ┌───────────────────────────────┬───────────────┐           │
 │ (sticky) │ │ replay video (16:9)           │ agent panel   │  hero     │
 │ Railhead │ └───────────────────────────────┴───────────────┘           │
 │  1 ...   │ scrubber · phases · Pause · Watch again                     │
 │ Waybill  │ meta row (Author / Updated / Code / License)                │
 │  7 ...   │ reading column 680px, left-aligned                          │
 │          │ ┌─ wide figure (full content width) ─────────────────────┐  │
 └──────────┴─────────────────────────────────────────────────────────────┘
```

Text is left-aligned; figures break out to the full content width (max 1080px).
Below 1024px the rail is hidden and the same contents open from a "Contents" disclosure in the top bar; below 760px the gutter is 16px and the replay goes full bleed.

## Components

- Replay panel (hero): video + panel synced to `video.currentTime` through the renderer's own schedule
  (`timelapse.schedule`: 1 s hold, linear in ticks for 55 s, 4 s hold).
- Segmented toggle, pill tabs with a dot, step tracker, slider with value; one style each, reused.
- Figure: number in mono ("Fig. 3"), title, plot, caption, `<details>` data table.
- Waybill document: a contract rendered as a shipping form (paper tone `#E9E4D6`, ink `#1E1F1C`), the one paper object on the page.

## Derived images (`site/img/`)

| File | From | Treatment |
|---|---|---|
| `arena2-poster.webp` | `docs/img/arena2-timelapse-end-dark.png` | resized to 1600 x 900, WebP q80 (the video poster) |
| `map-<model>.webp` | `docs/img/arena2-map-<model>.png` | crop box (34, 114, 1007, 1087), the map area without the title and legend; 880 x 880, WebP q82 |

The Waybill still and the video are used as published, from `assets/`.

## Removed accessory

A pulsing ring on the "Campaign running" dot: continuous motion that answered no action of the reader.

## Principles

1. One orchestrated moment: the hero replay. Everything else moves only when the reader acts.
2. Railway, not dashboard: the score pipeline is a line with stations; the accent changes at the "junction" between the volumes.
3. Every number on the page traces to a file in `docs/` or a function in `src/`.

## Review against the anti-defaults

- Rejected: cream page with a serif display (default 1). The owner's dark ballast palette and General Sans carry it.
- Rejected: an all-caps eyebrow over every heading. The mono label names only the two volumes, never a section.
- Rejected: identical rounded cards. Figures sit on a plate with a hairline; only controls are rounded; the waybill form is square-cornered paper.
- Rejected: big-number hero with a gradient. The hero is the replay itself.
- Kept on purpose: monospace for figure numbers and data (the brief asks for it), middle dots nowhere in meta rows (labels above values instead).
