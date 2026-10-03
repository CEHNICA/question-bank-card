"""Original-page question bodies: real ordered crops, never OCR placeholders."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path

from django.conf import settings
from PIL import Image

from . import imaging


def is_image(question) -> bool:
    return question.body_mode == "source_image"


def valid_regions(paper, regions) -> bool:
    if not isinstance(regions, list) or not 1 <= len(regions) <= 12:
        return False
    pages = {item["page_idx"] for item in paper.pages}
    for region in regions:
        if not isinstance(region, dict) or type(region.get("page_idx")) is not int or region["page_idx"] not in pages:
            return False
        box = region.get("bbox")
        if not isinstance(box, list) or len(box) != 4 or any(
            type(v) not in {float, int} or not math.isfinite(v) or not 0 <= v <= 1000 for v in box
        ) or box[2] - box[0] < 3 or box[3] - box[1] < 3:
            return False
    return True


def source_identity(paper) -> tuple[Path, str, str]:
    source = Path(paper.render_path or paper.source_path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return source, "pdf" if source.suffix.lower() == ".pdf" else paper.kind, digest.hexdigest()


def assets(question) -> list[dict]:
    if not valid_regions(question.paper, question.regions):
        raise ValueError("原图题需要有效的原卷范围")
    source, kind, source_hash = source_identity(question.paper)
    folder = Path(settings.DATA_ROOT) / str(question.paper_id) / "question-images"
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for index, region in enumerate(question.regions):
        key = hashlib.sha256(json.dumps([source_hash, region], sort_keys=True).encode()).hexdigest()[:32]
        target = folder / f"q{question.pk}-{key}.png"
        if not target.is_file():
            image = imaging.render_source_page(source, kind, region["page_idx"])
            cropped = image.crop(imaging.to_pixels(region["bbox"], image.size))
            # Concurrent previews must not read a partially written PNG.
            temporary = folder / f".{key}-{uuid.uuid4().hex}.png"
            try:
                cropped.save(temporary, format="PNG", optimize=True)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        with Image.open(target) as image:
            image.load()  # A readable PNG header does not prove intact pixels.
            width, height = image.size
        image_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        result.append({"page_idx": region["page_idx"], "bbox": list(region["bbox"]), "order": index,
                       "source": "manual", "render_sha256": source_hash, "image_sha256": image_hash,
                       "width": width, "height": height, "file": target.name,
                       "url": f"/api/questions/{question.pk}/question-images/{index}?v={image_hash[:16]}"})
    return result


def asset_file(question, index: int) -> Path:
    items = assets(question)
    if type(index) is not int or not 0 <= index < len(items):
        raise IndexError("原图题裁片不存在")
    return Path(settings.DATA_ROOT) / str(question.paper_id) / "question-images" / items[index]["file"]


def body_valid(question) -> bool:
    if not is_image(question):
        return bool(question.stem.strip())
    try:
        return bool(assets(question))
    except (OSError, ValueError, IndexError, RuntimeError):
        return False


def review(question) -> dict:
    if is_image(question):
        return {"status": "ok", "source": "manual", "reason": "正文保留所选原卷范围的完整图像，请对照原卷确认范围完整。"}
    from .figure_policy import stored_or_derived_review
    return stored_or_derived_review(question)
