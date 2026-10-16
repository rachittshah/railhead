# Charts and maps

This directory contains the images of the documents.
The text follows ASD-STE100. Refer to [../STE.md](../STE.md).

| Files | Source | Command |
|---|---|---|
| `arena*-networth*.svg`, `arena*-growth*.svg`, `arena*-fleet*.svg` | `docs/data/arena*.json` | `railhead figures docs/data/arena2.json docs/data/arena1.json --out docs/img` |
| `arena2-map-*.png` | The live daemon of each player | `railhead render <state_dir> <out.png>` |

Each chart has a light version and a dark version (`-dark.svg`).
The README shows the version that agrees with the color scheme of the reader.

## Colors

The colors come from a validated categorical palette.
Each set of colors was examined for color-vision deficiency, contrast, and lightness, in the light mode and in the dark mode.

| Function | Light | Dark |
|---|---|---|
| Claude players | `#2a78d6` | `#3987e5` |
| OpenAI players | `#eb6834` | `#d95926` |
| Road | `#1baf7a` | `#199e70` |
| Rail | `#eda100` | `#c98500` |
| Air | `#e87ba4` | `#d55181` |
| Certified baselines | `#c9c8c1` | `#4a4944` |

The road, rail, and air colors have less than 3:1 contrast on the light surface.
Because of this, each segment of the fleet chart has a label with its value, and the README has the same values in tables.
