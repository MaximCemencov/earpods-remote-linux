import unittest
from pathlib import Path

import numpy as np

from applemote import detect

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = detect.load_templates(ROOT / "templates/lenovo-83dj-alc287-earpods.npz")
with np.load(ROOT / "tests/data/lenovo-83dj-earpods-clips.npz") as f:
    CLIPS = {k: f[k].astype(np.float64) * float(f[k + "_scale"]) for k in f.files if not k.endswith("_scale")}


def keys(x, templates=TEMPLATES):
    return [e["key"] for e in detect.run(x, templates) if e["key"]]


def joined(names, fade=240):
    """Concatenate clips with short crossfades so the joins are not edges."""
    out = CLIPS[names[0]].copy()
    ramp = np.linspace(0, 1, fade)
    for n in names[1:]:
        c = CLIPS[n]
        out[-fade:] = out[-fade:] * (1 - ramp) + c[:fade] * ramp
        out = np.concatenate([out, c[fade:]])
    return out


class Detect(unittest.TestCase):
    def test_presses(self):
        for name, clip in CLIPS.items():
            if name.startswith("UP_"):
                self.assertEqual(keys(clip), ["VOLUMEUP"], name)
            elif name.startswith("DOWN_"):
                self.assertEqual(keys(clip), ["VOLUMEDOWN"], name)

    def test_noise_and_centre_button_ignored(self):
        for name, clip in CLIPS.items():
            if name.startswith("NOISE_"):
                self.assertEqual(keys(clip), [], name)

    def test_streaming_block_size_does_not_matter(self):
        x = joined([f"UP_PAIR_{i}" for i in range(5)])
        for block in (64, 480, 4096):
            det = detect.Detector(TEMPLATES)
            got = [e["key"] for s in range(0, len(x), block) for e in det.process(x[s:s + block]) if e["key"]]
            self.assertEqual(got, ["VOLUMEUP"] * 5, block)


class Learn(unittest.TestCase):
    def test_learns_shipped_templates_from_a_recording(self):
        up = joined([f"UP_PAIR_{i}" for i in range(5)])
        down = joined([f"DOWN_PAIR_{i}" for i in range(5)])
        x = np.concatenate([up, down])
        split = len(up) / detect.RATE
        segments = {"UP": (0, split), "DOWN": (split, len(x) / detect.RATE)}
        learned, report = detect.learn_templates(x, segments)
        self.assertEqual(report["UP"]["presses"], 5)
        self.assertEqual(report["DOWN"]["presses"], 5)
        for cls in detect.CLASSES:
            padded = np.pad(learned[cls], detect.LAG)
            self.assertGreater(detect.best_match(padded, TEMPLATES[cls])[0], 0.95, cls)
        result = detect.validate(x, learned, segments)
        self.assertEqual(result["UP"], {"VOLUMEUP": 5, "VOLUMEDOWN": 0})
        self.assertEqual(result["DOWN"], {"VOLUMEUP": 0, "VOLUMEDOWN": 5})


if __name__ == "__main__":
    unittest.main()
