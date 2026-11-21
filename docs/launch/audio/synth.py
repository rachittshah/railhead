"""Synthesize the launch video's soundtrack in code (no samples, no generated audio).

A low pulse at 72 BPM that steps up a tone when the video turns from Railhead to Waybill, soft ticks on text
beats, paper thuds on the three stamps, and one held chord under the end card. Beat times come from the scenes'
window.EVENTS (audio/events.json, made with `motlab events`), placed at each scene's start.

    python3 audio/synth.py             # from docs/launch; writes audio/score.wav
    python3 audio/synth.py --refresh   # first re-reads every scene's EVENTS with `motlab events` (set MOTLAB
                                       # to the command if motlab is not on PATH)
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 48000
lib = Path(HERE, '..', 'scenes', 'lib.js').read_text()
SCENES = [float(x) for x in re.search(r'const SCENES = \[([^\]]+)\]', lib).group(1).split(',')]
JUNCTION = int(re.search(r'const JUNCTION = (\d+)', lib).group(1))
TOTAL = sum(SCENES)
starts = np.cumsum([0] + SCENES[:-1])
EVENTS = os.path.join(HERE, 'events.json')
if '--refresh' in sys.argv:
    motlab = shlex.split(os.environ.get('MOTLAB', 'motlab'))
    project = json.loads(Path(HERE, '..', 'project.json').read_text())
    merged = []
    with tempfile.TemporaryDirectory() as tmp:
        for scene in project['scenes']:
            out = os.path.join(tmp, 'ev.json')
            subprocess.run([*motlab, 'events', os.path.join(HERE, '..', scene), out], check=True, capture_output=True)
            merged.append({'scene': scene, 'events': json.loads(Path(out).read_text())})
    Path(EVENTS).write_text(json.dumps(merged, indent=1))
events = json.loads(Path(EVENTS).read_text())
n = int(TOTAL * SR)
out = np.zeros(n)
t_all = np.arange(n) / SR


def place(sig, at, gain):
    i = int(at * SR)
    if i >= n:
        return
    j = min(n, i + len(sig))
    out[i:j] += sig[:j - i] * gain


def env(dur, attack=0.004, decay=0.2):
    t = np.arange(int(dur * SR)) / SR
    return np.minimum(1, t / attack) * np.exp(-t / decay), t


def thump(f0, f1, dur=0.5):
    e, t = env(dur, 0.006, 0.16)
    f = f1 + (f0 - f1) * np.exp(-t / 0.05)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * e


def tick(freq=2400):
    e, t = env(0.06, 0.001, 0.012)
    rng = np.random.default_rng(int(freq))
    noise = np.convolve(rng.standard_normal(len(t)), np.ones(6) / 6, 'same')
    return (0.6 * np.sin(2 * np.pi * freq * t) + 0.4 * noise) * e


def stamp():
    e, t = env(0.25, 0.002, 0.05)
    rng = np.random.default_rng(7)
    noise = np.convolve(rng.standard_normal(len(t)), np.ones(30) / 30, 'same') * 4
    return (noise + 0.8 * np.sin(2 * np.pi * 120 * t)) * e


def chord(freqs, dur=4.0):
    t = np.arange(int(dur * SR)) / SR
    e = np.minimum(1, t / 0.6) * np.exp(-t / 1.8)
    return sum(np.sin(2 * np.pi * f * t) * (0.6 ** k) for k, f in enumerate(freqs)) * e


# the pulse: 72 BPM, from 0.2 s to the end card title; A1 for Railhead, B1 for Waybill
end_title = starts[-1] + next(e['t'] for e in events[-1]['events'] if e['name'] == 'title')
beat = 60 / 72
for k, at in enumerate(np.arange(0.2, end_title - 0.1, beat)):
    f = 55.0 if at < starts[JUNCTION] else 61.74
    place(thump(f * 1.6, f), at, 0.32 if k % 2 == 0 else 0.2)

pitch = {'title': 1800, 'sub': 2100, 'l1': 2000, 'l2': 2250, 'l3': 2500, 'verdict0': 1900, 'verdict1': 2100,
         'verdict2': 2300, 'verdict3': 2500, 'head': 1900, 'done': 2800, 'line': 2000, 'arcs': 2200, 'l1b': 2000,
         'form': 1700, 'quote': 1800, 'question': 2000, 'url': 2400}
for s0, sc in zip(starts, events):
    for e in sc['events']:
        at, name = s0 + e['t'], e['name']
        if name.startswith('stamp'):
            place(stamp(), at, 0.28)
        elif name == 'title' and sc is events[-1]:
            place(chord([110, 164.8, 220, 329.6]), at, 0.16)
        elif name == 'land':
            place(chord([55, 82.4, 110], 2.5), at, 0.14)
        elif name in pitch:
            place(tick(pitch[name]), at, 0.07)

# fades, then normalise to a -8 dBFS peak (about -29 LUFS): a quiet bed under a silent-first video
fade = np.minimum(1, t_all / 0.3) * np.minimum(1, (TOTAL - t_all) / 0.6)
out *= fade
out *= 10 ** (-8 / 20) / np.max(np.abs(out))
pcm = (np.clip(out, -1, 1) * 32767).astype('<i2')
with wave.open(os.path.join(HERE, 'score.wav'), 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print(f'score.wav: {TOTAL:.1f} s, junction at {starts[JUNCTION]:.2f} s, end chord at {end_title:.2f} s')
