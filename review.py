from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from analysis_modes import AnalysisMode, get_analysis_mode
from engine import (
    _CONTOUR_THICKNESS,
    _REGION_ELLIPSE_COLOR,
    _REGION_ELLIPSE_THICKNESS,
    AnalysisSettings,
    _display_bins,
    _read_and_normalize,
    _write_result_files,
)


def _inside_region(x: float, y: float, region: dict) -> bool:
    rx = float(region["radius_x_px"])
    ry = float(region["radius_y_px"])
    if rx <= 0 or ry <= 0:
        return False
    return ((x - region["center_x_px"]) / rx) ** 2 + ((y - region["center_y_px"]) / ry) ** 2 <= 1


def _manual_particle(
    x: float,
    y: float,
    length_um: float,
    um_per_px: float,
    mode: AnalysisMode,
) -> dict:
    length_px = length_um / um_per_px
    radius = max(1, int(round(length_px / 2)))
    contour = cv2.ellipse2Poly(
        (int(round(x)), int(round(y))),
        (radius, radius),
        0,
        0,
        360,
        20,
    )
    size_bin = mode.classify(length_um)
    return {
        "id": f"manual-{uuid.uuid4().hex}",
        "source": "manual",
        "center_x_px": int(round(x)),
        "center_y_px": int(round(y)),
        "length_px": round(length_px, 3),
        "length_um": round(length_um, 2),
        "pixel_area": int(round(math.pi * radius * radius)),
        "bin": size_bin.label,
        "contour_px": contour.tolist(),
    }


def _render_annotated(source: np.ndarray, result: dict, mode: AnalysisMode) -> np.ndarray:
    annotated = source.copy()
    labels = [size_bin.label for size_bin in mode.bins]
    for particle in result["particles"]:
        try:
            bin_index = labels.index(particle["bin"])
        except ValueError as error:
            raise ValueError(f"未知颗粒分档：{particle['bin']}") from error
        contour = np.asarray(particle["contour_px"], dtype=np.int32).reshape(-1, 1, 2)
        cv2.drawContours(
            annotated,
            [contour],
            -1,
            mode.bins[bin_index].color_bgr,
            _CONTOUR_THICKNESS,
            cv2.LINE_AA,
        )
    region = result["region"]
    cv2.ellipse(
        annotated,
        (int(region["center_x_px"]), int(region["center_y_px"])),
        (int(region["radius_x_px"]), int(region["radius_y_px"])),
        0,
        0,
        360,
        _REGION_ELLIPSE_COLOR,
        _REGION_ELLIPSE_THICKNESS,
        cv2.LINE_AA,
    )
    return annotated


def _recount(result: dict, mode: AnalysisMode) -> list[int]:
    labels = [size_bin.label for size_bin in mode.bins]
    counts = [0] * len(labels)
    for particle in result["particles"]:
        try:
            counts[labels.index(particle["bin"])] += 1
        except ValueError as error:
            raise ValueError(f"未知颗粒分档：{particle['bin']}") from error
    result["counts"] = counts
    result["bins"] = _display_bins(mode, counts)
    result["total"] = sum(counts)
    return counts


def apply_review_action(result_dir: Path, action: dict, actor: str = "操作员") -> dict:
    metadata_path = result_dir / "analysis.json"
    source_path = result_dir / "source.png"
    if not metadata_path.is_file() or not source_path.is_file():
        raise ValueError("该结果不支持人工复核，请重新分析原图。")
    result = json.loads(metadata_path.read_text(encoding="utf-8"))
    mode = get_analysis_mode(result.get("settings", {}).get("analysis_mode", "custom"))
    particles = result.setdefault("particles", [])
    audit = result.setdefault("review_audit", [])
    action_type = action.get("type")
    if mode.key == "vda19_1" and action_type in {"add", "split"}:
        raise ValueError(
            "VDA 19.1 模式下新增或拆分颗粒必须基于真实轮廓重新测量，"
            "当前版本不接受手工输入尺寸。"
        )
    audit_item = {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": actor.strip()[:80] or "操作员",
        "type": action_type,
    }

    if action_type == "add":
        x = float(action["x_px"])
        y = float(action["y_px"])
        length_um = float(action["length_um"])
        if not all(math.isfinite(value) for value in (x, y, length_um)):
            raise ValueError("人工颗粒参数不是有效数字。")
        if length_um > 100000:
            raise ValueError("人工颗粒尺寸不能大于 100000 μm。")
        if not _inside_region(x, y, result["region"]):
            raise ValueError("人工颗粒必须位于统计区域内。")
        particle = _manual_particle(x, y, length_um, float(result["um_per_px"]), mode)
        particles.append(particle)
        audit_item["particle"] = particle
    elif action_type == "remove":
        particle_id = str(action.get("particle_id", ""))
        index = next((i for i, item in enumerate(particles) if item["id"] == particle_id), None)
        if index is None:
            raise ValueError("未找到要删除的颗粒。")
        audit_item["particle"] = particles.pop(index)
    elif action_type == "split":
        particle_id = str(action.get("particle_id", ""))
        index = next((i for i, item in enumerate(particles) if item["id"] == particle_id), None)
        if index is None:
            raise ValueError("未找到要拆分的颗粒。")
        replacements_payload = action.get("particles")
        if not isinstance(replacements_payload, list) or len(replacements_payload) != 2:
            raise ValueError("拆分操作必须提供两颗新颗粒。")
        replacements = []
        for payload in replacements_payload:
            x = float(payload["x_px"])
            y = float(payload["y_px"])
            length_um = float(payload["length_um"])
            if not all(math.isfinite(value) for value in (x, y, length_um)):
                raise ValueError("拆分颗粒参数不是有效数字。")
            if length_um > 100000:
                raise ValueError("拆分后的颗粒尺寸不能大于 100000 μm。")
            if not _inside_region(x, y, result["region"]):
                raise ValueError("拆分后的颗粒必须位于统计区域内。")
            replacements.append(
                _manual_particle(x, y, length_um, float(result["um_per_px"]), mode)
            )
        audit_item["particle"] = particles.pop(index)
        audit_item["replacements"] = replacements
        particles.extend(replacements)
    elif action_type == "undo":
        target = next((item for item in reversed(audit) if not item.get("undone")), None)
        if target is None:
            raise ValueError("没有可撤销的人工操作。")
        particle = target["particle"]
        if target["type"] == "add":
            particles[:] = [item for item in particles if item["id"] != particle["id"]]
        elif target["type"] == "remove":
            particles.append(particle)
        elif target["type"] == "split":
            replacement_ids = {item["id"] for item in target["replacements"]}
            particles[:] = [item for item in particles if item["id"] not in replacement_ids]
            particles.append(particle)
        else:
            raise ValueError("上一项操作无法撤销。")
        target["undone"] = True
        audit_item["target_audit_id"] = target["id"]
        audit_item["particle"] = particle
        audit_item["undone"] = True
    else:
        raise ValueError("未知的人工复核操作。")

    audit.append(audit_item)
    counts = _recount(result, mode)
    source = _read_and_normalize(source_path)
    annotated = _render_annotated(source, result, mode)
    settings = AnalysisSettings(**result["settings"])
    _write_result_files(
        result_dir,
        annotated,
        result["particles"],
        counts,
        result,
        float(result["scale_px"]),
        int(result["image"]["width"]),
        int(result["image"]["height"]),
        settings,
    )
    return result
