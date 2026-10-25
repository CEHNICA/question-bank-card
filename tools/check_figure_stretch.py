"""Compare an入库 figure with the same rectangle cut from the original page.

The figure box in the record is normalised to 0..1000. Cutting that box out of
the scanned page and comparing its aspect ratio with the stored PNG shows
whether the picture was stretched on the way in.
"""

import os
import sys

from PIL import Image

ROOT = (
    r"C:\Users\17321\AppData\Local\QuestionBankCard\data"
    r"\220405b2-d314-406a-81da-5adc417ace41"
)
FIGURES = (
    r"C:\Users\17321\AppData\Local\QuestionBankCard\data\library"
    r"\33de480e-99bc-4ced-b776-6812978246d8"
)
OUT = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\17321\Downloads"

BOXES = [
    ("figure-1.png", [519.0, 228.0, 612.0, 334.0]),
    ("figure-2.png", [625.0, 228.0, 702.0, 334.0]),
    ("figure-3.png", [715.0, 238.0, 785.0, 349.0]),
]

with Image.open(os.path.join(ROOT, "pages", "page_3.png")) as page:
    page = page.convert("RGB")
    width, height = page.size
    print(f"original page: {width} x {height}")
    for name, box in BOXES:
        x0, y0, x1, y1 = box
        crop = page.crop(
            (round(x0 / 1000 * width), round(y0 / 1000 * height), round(x1 / 1000 * width), round(y1 / 1000 * height))
        )
        crop.save(os.path.join(OUT, "orig-" + name))
        with Image.open(os.path.join(FIGURES, name)) as stored:
            stored = stored.convert("RGB")
            stored.save(os.path.join(OUT, "stored-" + name))
        print(f"{name}")
        print(f"   cut from page : {crop.size[0]} x {crop.size[1]}   ratio w/h = {crop.size[0] / crop.size[1]:.3f}")
        print(
            f"   stored PNG    : {stored.size[0]} x {stored.size[1]}"
            f"   ratio w/h = {stored.size[0] / stored.size[1]:.3f}"
        )
        print(f"   difference    : {(stored.size[0] / stored.size[1]) / (crop.size[0] / crop.size[1]):.3f}x")
