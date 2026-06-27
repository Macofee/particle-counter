from __future__ import annotations

import csv
import json
import math
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


BIN_DEFINITIONS = (
    (25.0, 50.0, "25<n<=50", (35, 181, 106)),
    (50.0, 100.0, "50<n<=100", (25, 158, 234)),
    (100.0, 200.0, "100<n<=200", (42, 54, 222)),
    (200.0, math.inf, "n>200", (180, 51, 170)),
)

# Yellow scale-gap detection thresholds — tuned for the standard yellow
# double-stroke printed in the lower-right corner of microscope images.
_YELLOW_HSV_LOWER = (20, 130, 135)
_YELLOW_HSV_UPPER = (45, 255, 255)
_YELLOW_MASK_TOP_FRAC = 0.60   # ignore upper portion of image
_YELLOW_MASK_LEFT_FRAC = 0.55  # ignore left portion of image
_YELLOW_MIN_AREA = 300
_YELLOW_MIN_HEIGHT = 40
_YELLOW_MIN_WIDTH = 20
_YELLOW_TALL_COL_FLOOR = 10       # minimum column count when max is small
_YELLOW_TALL_COL_FRAC = 0.72      # fraction of max column count for "tall"
_YELLOW_MIN_COLUMN_RUNS = 2       # need at least 2 distinct vertical strokes
_YELLOW_MIN_GAP_PX = 12
_YELLOW_MAX_GAP_FRAC = 0.10       # gap relative to image width


@dataclass
class AnalysisSettings:
    scale_um: float = 500.0
    scale_px: Optional[float] = None
    center_x: float = 0.49
    center_y: float = 0.49
    radius_x: float = 0.47
    radius_y: float = 0.46
    edge_threshold: int = 20
    seed_threshold: int = 40
    guard_um: float = 130.0
    min_size_um: float = 25.0


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    result: list[list[int]] = []
    for value in values.tolist():
        if not result or value > result[-1][1] + 1:
            result.append([value, value])
        else:
            result[-1][1] = value
    return [(item[0], item[1]) for item in result]


def detect_yellow_scale_gap(image: np.ndarray) -> tuple[float, dict]:
    """Return center-to-center spacing of the two yellow scale strokes."""
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(
        hsv,
        np.array(_YELLOW_HSV_LOWER),
        np.array(_YELLOW_HSV_UPPER),
    )
    yellow[: int(height * _YELLOW_MASK_TOP_FRAC), :] = 0
    yellow[:, : int(width * _YELLOW_MASK_LEFT_FRAC)] = 0

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(yellow)
    candidates = []
    for component_id in range(1, component_count):
        x, y, w, h, area = stats[component_id]
        if area < _YELLOW_MIN_AREA or h < _YELLOW_MIN_HEIGHT or w < _YELLOW_MIN_WIDTH:
            continue
        component = labels[y : y + h, x : x + w] == component_id
        column_counts = component.sum(axis=0)
        tall_columns = np.where(
            column_counts
            >= max(_YELLOW_TALL_COL_FLOOR, int(column_counts.max() * _YELLOW_TALL_COL_FRAC))
        )[0]
        column_runs = [
            run
            for run in _runs(tall_columns)
            if run[1] - run[0] + 1 >= _YELLOW_MIN_COLUMN_RUNS
        ]
        if len(column_runs) < _YELLOW_MIN_COLUMN_RUNS:
            continue
        left, right = column_runs[-2], column_runs[-1]
        left_center = x + (left[0] + left[1]) / 2.0
        right_center = x + (right[0] + right[1]) / 2.0
        gap = float(math.floor((right_center - left_center) + 0.5))
        if _YELLOW_MIN_GAP_PX <= gap <= width * _YELLOW_MAX_GAP_FRAC:
            score = area + x + y
            candidates.append(
                (
                    int(score),
                    float(gap),
                    (float(left_center), float(right_center)),
                    (int(x), int(y), int(w), int(h)),
                )
            )

    if not candidates:
        raise ValueError("未识别到右下角黄色比例尺，请手动填写两条黄线的像素间距。")

    _, gap, centers, bbox = max(candidates, key=lambda item: item[0])
    return float(gap), {"line_centers_px": centers, "component_bbox": bbox}


def _bin_index(length_um: float) -> int:
    for index, (low, high, _, _) in enumerate(BIN_DEFINITIONS):
        if low < length_um <= high:
            return index
    return len(BIN_DEFINITIONS) - 1


def _maximum_feret_diameter(contour: np.ndarray) -> float:
    """Measure the largest distance between points on a contour's convex hull."""
    hull = cv2.convexHull(contour, returnPoints=True).reshape(-1, 2).astype(np.float64)
    if len(hull) <= 1:
        return 1.0

    if len(hull) == 2:
        largest_squared = float(np.sum((hull[0] - hull[1]) ** 2))
    else:
        # Rotating calipers finds the exact convex-polygon diameter in linear
        # time, so scratches with detailed outlines do not cause quadratic work.
        largest_squared = 0.0
        opposite = 1
        for index in range(len(hull)):
            next_index = (index + 1) % len(hull)
            edge = hull[next_index] - hull[index]

            def area_twice(point_index: int) -> float:
                offset = hull[point_index] - hull[index]
                return abs(float(edge[0] * offset[1] - edge[1] * offset[0]))

            while area_twice((opposite + 1) % len(hull)) > area_twice(opposite) + 1e-9:
                opposite = (opposite + 1) % len(hull)

            candidates = {opposite}
            next_opposite = (opposite + 1) % len(hull)
            if abs(area_twice(next_opposite) - area_twice(opposite)) <= 1e-9:
                candidates.add(next_opposite)
            for candidate in candidates:
                for endpoint in (index, next_index):
                    distance_squared = float(np.sum((hull[endpoint] - hull[candidate]) ** 2))
                    largest_squared = max(largest_squared, distance_squared)

    # Contour coordinates describe pixel centers. The extra pixel preserves
    # the previous pixel-extent convention used by the classification model.
    return math.sqrt(largest_squared) + 1.0


def analyze_image(
    image_path: Path,
    result_dir: Path,
    settings: AnalysisSettings,
) -> dict:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError("图片无法读取，请使用 JPG、PNG、TIFF 或 BMP 文件。")

    # Normalize 16-bit and grayscale images so downstream color operations
    # (HSV conversion, yellow detection) are safe.
    if image.dtype == np.uint16:
        image = (image / 257).astype(np.uint8)
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 1:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

    result_dir.mkdir(parents=True, exist_ok=True)
    height, width = image.shape[:2]

    scale_meta = {"source": "manual"}
    if settings.scale_px and settings.scale_px > 0:
        scale_px = float(settings.scale_px)
    else:
        scale_px, detected = detect_yellow_scale_gap(image)
        scale_meta = {"source": "auto", **detected}
    um_per_px = settings.scale_um / scale_px

    cx = int(round(settings.center_x * width))
    cy = int(round(settings.center_y * height))
    rx = int(round(settings.radius_x * width))
    ry = int(round(settings.radius_y * height))
    guard_px = max(4, int(round(settings.guard_um / um_per_px)))
    effective_rx = max(10, rx - guard_px)
    effective_ry = max(10, ry - guard_px)

    x0 = max(0, cx - effective_rx - 3)
    y0 = max(0, cy - effective_ry - 3)
    x1 = min(width, cx + effective_rx + 4)
    y1 = min(height, cy + effective_ry + 4)

    gray_full = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = gray_full[y0:y1, x0:x1]
    region = np.zeros(gray.shape, dtype=np.uint8)
    cv2.ellipse(
        region,
        (cx - x0, cy - y0),
        (effective_rx, effective_ry),
        0,
        0,
        360,
        1,
        cv2.FILLED,
    )

    sigma_px = float(np.clip(55.0 / um_per_px, 3.0, 18.0))
    background = cv2.GaussianBlur(gray, (0, 0), sigma_px)
    contrast = cv2.subtract(background, gray)

    low = (
        (contrast >= settings.edge_threshold)
        & (region > 0)
        & (gray <= 205)
    ).astype(np.uint8)
    seed = (
        (contrast >= settings.seed_threshold)
        & (region > 0)
        & (gray <= 190)
    )
    low = cv2.morphologyEx(
        low,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )

    _, labels, stats, centroids = cv2.connectedComponentsWithStats(low, connectivity=8)
    selected_labels = np.unique(labels[seed])
    selected_labels = selected_labels[selected_labels != 0]

    records = []
    counts = [0, 0, 0, 0]
    annotated = image.copy()
    contour_shift = np.array([[[x0, y0]]], dtype=np.int32)

    for label_id in selected_labels:
        x, y, component_width, component_height, pixel_area = stats[label_id]
        component = (
            labels[y : y + component_height, x : x + component_width] == label_id
        ).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=lambda item: cv2.arcLength(item, False))
        length_px = _maximum_feret_diameter(contour)
        length_um = length_px * um_per_px
        if length_um <= settings.min_size_um:
            continue

        bin_index = _bin_index(length_um)
        counts[bin_index] += 1
        contour_full = contour + np.array(
            [[[x + x0, y + y0]]], dtype=np.int32
        )
        color_bgr = BIN_DEFINITIONS[bin_index][3]
        cv2.drawContours(annotated, [contour_full], -1, color_bgr, 5, cv2.LINE_AA)
        records.append(
            {
                "center_x_px": int(round(centroids[label_id][0])) + x0,
                "center_y_px": int(round(centroids[label_id][1])) + y0,
                "length_px": round(length_px, 3),
                "length_um": round(length_um, 2),
                "pixel_area": int(pixel_area),
                "bin": BIN_DEFINITIONS[bin_index][2],
            }
        )

    cv2.ellipse(
        annotated,
        (cx, cy),
        (effective_rx, effective_ry),
        0,
        0,
        360,
        (225, 105, 25),
        9,
        cv2.LINE_AA,
    )

    annotated_path = result_dir / "annotated.jpg"
    preview_path = result_dir / "preview.jpg"
    summary_path = result_dir / "summary.csv"
    measurements_path = result_dir / "measurements.csv"
    metadata_path = result_dir / "analysis.json"
    bundle_path = result_dir / "result_bundle.zip"

    try:
        cv2.imwrite(str(annotated_path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 94])
        preview_scale = min(1.0, 1900.0 / max(width, height))
        preview = cv2.resize(
            annotated,
            (0, 0),
            fx=preview_scale,
            fy=preview_scale,
            interpolation=cv2.INTER_AREA,
        )
        cv2.imwrite(str(preview_path), preview, [cv2.IMWRITE_JPEG_QUALITY, 91])
    except Exception as exc:
        raise OSError(f"写入标注图片失败，请检查磁盘空间和目录权限：{exc}") from exc

    try:
        with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(["颗粒规格_um（最大长度）", "数量"])
            for count, (_, _, label, _) in zip(counts, BIN_DEFINITIONS):
                writer.writerow([label, count])
            writer.writerow(["合计", sum(counts)])
            writer.writerow([])
            writer.writerow(["比例换算", f"{scale_px:.2f} px = {settings.scale_um:g} um"])
            writer.writerow(["识别参数", f"边缘={settings.edge_threshold}; 深色核心={settings.seed_threshold}"])

        with measurements_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["编号", "center_x_px", "center_y_px", "length_px", "length_um", "pixel_area", "bin"],
            )
            writer.writeheader()
            for index, record in enumerate(sorted(records, key=lambda item: (item["center_y_px"], item["center_x_px"])), 1):
                writer.writerow({"编号": index, **record})
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"写入 CSV 结果文件失败，请检查磁盘空间和目录权限：{exc}") from exc

    result = {
        "image": {"width": width, "height": height, "name": image_path.name},
        "counts": counts,
        "total": sum(counts),
        "scale_px": round(scale_px, 2),
        "scale_um": settings.scale_um,
        "um_per_px": round(um_per_px, 5),
        "scale_meta": scale_meta,
        "region": {
            "center_x_px": cx,
            "center_y_px": cy,
            "radius_x_px": effective_rx,
            "radius_y_px": effective_ry,
        },
        "settings": asdict(settings),
    }
    try:
        metadata_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"写入分析元数据失败，请检查磁盘空间和目录权限：{exc}") from exc

    try:
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in (annotated_path, preview_path, summary_path, measurements_path, metadata_path):
                archive.write(path, arcname=path.name)
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"打包结果文件失败，请检查磁盘空间和目录权限：{exc}") from exc

    return result
