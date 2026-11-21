# Launch video

Source of `docs/video/railhead-launch.mp4` (35.6 s, 1920x1080, 30 fps) and the README preview
`docs/img/railhead-launch.webp`. Video as code with [motlab](https://github.com/staryone/motlab): each scene is a
canvas page with `renderAt(t)`, rendered in headless Chrome. No generated images, video or audio: the footage and
stills are the repository's own replay renders, everything else is drawn in code.

| Path | What |
|---|---|
| `project.json` | scene order, `qp`, the soundtrack |
| `scenes/01-title.html` ... `08-end.html` | one page per shot; beat times in the `B` block at the top of each |
| `scenes/lib.js` | site tokens (`site/DESIGN.md`), easing, type helpers, the rail along the bottom, scene lengths (`SCENES`) |
| `scenes/shared.js` | timelapse crop and the brief column (scenes 2 and 3) |
| `audio/synth.py` | synthesizes `audio/score.wav` from `audio/events.json` (numpy only) |
| `assets/fable-caption-patch.png` | a strip of the timelapse from before a caption appears, laid over that caption in scenes 2 and 3 |
| `img`, `video`, `data` | symlinks to `../img`, `../video`, `../data`, so the pages use the repo's files without copies |

Fonts load from Fontshare (General Sans) and Google Fonts (IBM Plex Mono), so rendering needs the network; a page
fails rather than render in a fallback face.

## Render

```bash
cd docs/launch
# after changing beat times: re-read every scene's EVENTS and rebuild the soundtrack
python3 audio/synth.py --refresh                               # MOTLAB="node <engine>/bin/motlab.js" if not on PATH
motlab render . ../video/railhead-launch.mp4 --scale 1        # --only N re-renders one scene
motlab stills scenes/03-verdicts.html /tmp/stills 1 3 5        # check frames
```

## Preview

```bash
ffmpeg -i ../video/railhead-launch.mp4 -vf "fps=12,scale=1280:-2:flags=lanczos" /tmp/wf/f%04d.png
img2webp -loop 0 -kmin 4 -kmax 6 -lossy -q 58 -m 4 -d 83 /tmp/wf/f*.png -o ../img/railhead-launch.webp
```

Keep a key frame every few frames (`-kmin 4 -kmax 6`): with the default, the encoder leaves blocks of the
previous scene behind on the dark background after a fade. ffmpeg's own WebP encoder is not needed.
