from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from analysis_modes import CUSTOM_MODE, AnalysisMode, get_analysis_mode
from reporting import write_pdf_report


BIN_DEFINITIONS = tuple(
    (item.low_um, item.high_um, item.label, item.color_bgr)
    for item in CUSTOM_MODE.bins
)

# 前端分桶展示信息 — 与 BIN_DEFINITIONS 一一对应
BIN_DISPLAY = tuple(
    {"label": label, "color": item.color_hex, "code": item.code}
    for label, item in zip(
        ("25–50 μm", "50–100 μm", "100–200 μm", "＞200 μm"),
        CUSTOM_MODE.bins,
    )
)

ALGORITHM_VERSION = "2.3.0"

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

# analyze_image 调校参数 — 针对白色滤膜深色颗粒的通用默认值
_ROI_EXPAND_LEFT = 3
_ROI_EXPAND_RIGHT = 4
_MIN_GUARD_PX = 4
_MIN_EFFECTIVE_RADIUS = 10
_GAUSSIAN_SIGMA_MIN = 3.0
_GAUSSIAN_SIGMA_MAX = 18.0
_GAUSSIAN_SIGMA_NOM = 55.0        # numerator in sigma = nom / um_per_px
_BKG_GRAY_MAX = 205               # background pixels must be darker than this
_SEED_GRAY_MAX = 190              # seed pixels must be darker than this
_MORPH_KERNEL = (3, 3)            # morphology close kernel size (ellipse)
_CONTOUR_THICKNESS = 5
_REGION_ELLIPSE_COLOR = (225, 105, 25)   # BGR blue
_REGION_ELLIPSE_THICKNESS = 9
_JPEG_QUALITY_ANNOTATED = 94
_JPEG_QUALITY_PREVIEW = 91
_PREVIEW_MAX_DIM = 1900.0


@dataclass
class AnalysisSettings:
    analysis_mode: str = "custom"
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
    """Return outer-edge spacing of the two yellow scale strokes."""
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
        left_outer = float(x + left[0])
        right_outer = float(x + right[1])
        gap = float(math.floor((right_outer - left_outer) + 0.5))
        if _YELLOW_MIN_GAP_PX <= gap <= width * _YELLOW_MAX_GAP_FRAC:
            score = area + x + y
            candidates.append(
                (
                    int(score),
                    float(gap),
                    (float(left_center), float(right_center)),
                    (left_outer, right_outer),
                    (int(x), int(y), int(w), int(h)),
                )
            )

    if not candidates:
        raise ValueError("未识别到右下角黄色比例尺，请手动填写两条黄线的外侧边缘间距。")

    _, gap, centers, outer_edges, bbox = max(candidates, key=lambda item: item[0])
    return float(gap), {
        "measurement": "outer_edges",
        "outer_edges_px": outer_edges,
        "line_centers_px": centers,
        "component_bbox": bbox,
    }


def _bin_index(length_um: float) -> int:
    size_bin = CUSTOM_MODE.classify(length_um)
    return CUSTOM_MODE.bins.index(size_bin)


def _display_label(mode: AnalysisMode, index: int) -> str:
    size_bin = mode.bins[index]
    if mode.key == "custom":
        return BIN_DISPLAY[index]["label"]
    if math.isinf(size_bin.high_um):
        range_label = f"≥{size_bin.low_um:g} μm"
    else:
        range_label = f"{size_bin.low_um:g}–{size_bin.high_um:g} μm"
    return f"{size_bin.code} · {range_label}"


def _display_bins(mode: AnalysisMode, counts: list[int]) -> list[dict]:
    return [
        {
            "code": size_bin.code,
            "label": _display_label(mode, index),
            "color": size_bin.color_hex,
            "count": count,
        }
        for index, (size_bin, count) in enumerate(zip(mode.bins, counts))
    ]


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


def _minimum_feret_diameter(contour: np.ndarray) -> float:
    """Measure the smallest distance between parallel supporting lines of a
    contour's convex hull — the Feret-min / particle width defined in VDA 19.1
    §8.2.2 Figure 8-8."""
    hull = cv2.convexHull(contour, returnPoints=True).reshape(-1, 2).astype(np.float64)
    if len(hull) <= 2:
        # A point or a line segment has negligible orthogonal extent.
        return 1.0

    min_width = float("inf")
    opposite = 1
    for index in range(len(hull)):
        next_index = (index + 1) % len(hull)
        edge = hull[next_index] - hull[index]
        edge_len_sq = float(edge[0] * edge[0] + edge[1] * edge[1])
        if edge_len_sq < 1e-12:
            continue

        def area_twice(point_index: int) -> float:
            offset = hull[point_index] - hull[index]
            return abs(float(edge[0] * offset[1] - edge[1] * offset[0]))

        while area_twice((opposite + 1) % len(hull)) > area_twice(opposite) + 1e-9:
            opposite = (opposite + 1) % len(hull)

        # Perpendicular distance from antipodal point to the edge line.
        candidates = {opposite}
        next_opposite = (opposite + 1) % len(hull)
        if abs(area_twice(next_opposite) - area_twice(opposite)) <= 1e-9:
            candidates.add(next_opposite)
        for candidate in candidates:
            width = area_twice(candidate) / math.sqrt(edge_len_sq)
            min_width = min(min_width, width)

    # Same pixel-extent convention as _maximum_feret_diameter.
    return max(1.0, min_width + 1.0)


def _max_inscribed_circle_diameter(mask: np.ndarray) -> float:
    """Diameter of the largest circle that fits entirely inside the particle.

    Uses Euclidean distance transform (L2 norm) so the result is rotation-
    invariant — the max distance from any foreground pixel to the background
    equals the inscribed circle radius.  A 1-pixel zero border is added so
    that particles that touch the bounding-box edges are measured correctly.
    VDA 19.1 §8.2.2 calls this the "maximum inscribed circle" and uses it
    as the particle width for fiber classification.
    """
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    dist = cv2.distanceTransform(padded, cv2.DIST_L2, 3)
    return float(dist.max() * 2)


def _stretched_length(mask: np.ndarray) -> float:
    """Approximate the stretched (skeleton) length of a particle.

    VDA 19.1 §8.2.2 prescribes stretched length for fibres because curled or
    wavy shapes can make Feret-max unreliable.  For thin elongated particles
    whose perimeter is dominated by the two long sides, half the perimeter
    approximates the medial-axis length faithfully while being simple and
    deterministic.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 1.0
    perimeter = cv2.arcLength(max(contours, key=lambda c: cv2.arcLength(c, False)), closed=True)
    return perimeter / 2.0


def _classify_as_fiber(particle_mask: np.ndarray, um_per_px: float) -> tuple[bool, dict]:
    """Return (is_fiber, metrics) for a single particle mask.

    VDA 19.1 §8.2.2 geometric criteria:
      - max inscribed circle diameter (width) ≤ 50 μm
      - stretched-length / max-inscribed-circle-diameter > 20
    """
    inscribed_diameter_px = _max_inscribed_circle_diameter(particle_mask)
    inscribed_diameter_um = inscribed_diameter_px * um_per_px
    if inscribed_diameter_um > 50.0 or inscribed_diameter_px < 0.5:
        return False, {"fiber_width_um": float(round(inscribed_diameter_um, 2))}

    stretched_px = _stretched_length(particle_mask)
    ratio = stretched_px / inscribed_diameter_px if inscribed_diameter_px > 0 else 0.0
    is_fiber = ratio > 20.0
    return is_fiber, {
        "fiber_ratio": float(round(ratio, 2)),
        "fiber_width_um": float(round(inscribed_diameter_um, 2)),
        "fiber_stretched_px": float(round(stretched_px, 3)),
    }


def _read_and_normalize(image_path: Path) -> np.ndarray:
    """读取图片并归一化为 uint8 BGR，兼容 16-bit 与灰度图。

    使用 np.frombuffer + cv2.imdecode 替代 cv2.imread，避免 Windows 上
    OpenCV C 运行时 fopen 对 UTF-8/Unicode 路径的不兼容问题。
    """
    data = image_path.read_bytes()
    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("图片无法读取，请使用 JPG、PNG、TIFF 或 BMP 文件。")
    if image.dtype == np.uint16:
        image = (image / 257).astype(np.uint8)
    elif image.dtype != np.uint8:
        raise ValueError(f"不支持的图片位深：{image.dtype}，请使用 8-bit 或 16-bit 图片。")
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 1:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    elif image.shape[2] != 3:
        raise ValueError(f"不支持的图片通道数：{image.shape[2]}。")
    return image


def _checked_imwrite(path: Path, image: np.ndarray, params: list[int]) -> None:
    """Write an image and turn OpenCV's False return into a visible failure.

    使用 cv2.imencode + write_bytes 替代 cv2.imwrite，避免 Windows 上
    OpenCV C 运行时 fopen 对 UTF-8/Unicode 路径的不兼容问题。
    """
    ext = path.suffix or ".jpg"
    success, buf = cv2.imencode(ext, image, params)
    if not success:
        raise OSError(f"OpenCV 未能编码图片：{path.name}")
    try:
        path.write_bytes(buf.tobytes())
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"写入图片失败：{path.name}：{exc}") from exc


def _make_ellipse_mask(
    shape: tuple[int, int],
    center: tuple[int, int],
    axes: tuple[int, int],
) -> np.ndarray:
    """创建二值椭圆蒙版，椭圆内为 1。"""
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1, cv2.FILLED)
    return mask


def _write_result_files(
    result_dir: Path,
    annotated: np.ndarray,
    records: list[dict],
    counts: list[int],
    result: dict,
    scale_px: float,
    width: int,
    height: int,
    settings: AnalysisSettings,
) -> None:
    """将所有分析结果写入磁盘（图片、CSV、JSON、ZIP）。"""
    annotated_path = result_dir / "annotated.jpg"
    preview_path = result_dir / "preview.jpg"
    summary_path = result_dir / "summary.csv"
    measurements_path = result_dir / "measurements.csv"
    metadata_path = result_dir / "analysis.json"
    bundle_path = result_dir / "result_bundle.zip"
    report_path = result_dir / "report.pdf"
    mode = get_analysis_mode(settings.analysis_mode)

    try:
        _checked_imwrite(
            annotated_path,
            annotated,
            [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY_ANNOTATED],
        )
        preview_scale = min(1.0, _PREVIEW_MAX_DIM / max(width, height))
        preview = cv2.resize(
            annotated,
            (0, 0),
            fx=preview_scale,
            fy=preview_scale,
            interpolation=cv2.INTER_AREA,
        )
        _checked_imwrite(
            preview_path,
            preview,
            [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY_PREVIEW],
        )
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"写入标注图片失败，请检查磁盘空间和目录权限：{exc}") from exc

    try:
        with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(["颗粒规格_um（最大长度）", "数量"])
            for count, size_bin in zip(counts, mode.bins):
                writer.writerow([size_bin.label, count])
            fiber_count = result.get("fiber_count", 0)
            if fiber_count:
                writer.writerow(["纤维（不计入尺寸分档）", fiber_count])
            writer.writerow(["合计", sum(counts)])
            writer.writerow([])
            writer.writerow(["分析模式", mode.name])
            writer.writerow(["比例换算", f"{scale_px:.2f} px = {settings.scale_um:g} um"])
            resolution = result.get("resolution_check", {})
            if mode.required_pixels is not None:
                writer.writerow(
                    [
                        "分辨率检查",
                        f"{mode.minimum_size_um:g} um = {resolution.get('minimum_particle_pixels')} px; "
                        f"要求 >= {mode.required_pixels} px; 通过",
                    ]
                )
            writer.writerow(["识别参数", f"边缘={settings.edge_threshold}; 深色核心={settings.seed_threshold}"])

        with measurements_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "编号",
                    "particle_id",
                    "class",
                    "source",
                    "center_x_px",
                    "center_y_px",
                    "length_px",
                    "length_um",
                    "width_px",
                    "width_um",
                    "pixel_area",
                    "bin",
                ],
            )
            writer.writeheader()
            for index, record in enumerate(
                sorted(records, key=lambda item: (item["center_y_px"], item["center_x_px"])), 1
            ):
                writer.writerow(
                    {
                        "编号": index,
                        "particle_id": record["id"],
                        "class": record.get("class", "particle"),
                        "source": record["source"],
                        "center_x_px": record["center_x_px"],
                        "center_y_px": record["center_y_px"],
                        "length_px": record["length_px"],
                        "length_um": record["length_um"],
                        "width_px": record.get("width_px", ""),
                        "width_um": record.get("width_um", ""),
                        "pixel_area": record["pixel_area"],
                        "bin": record["bin"],
                    }
                )
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"写入 CSV 结果文件失败，请检查磁盘空间和目录权限：{exc}") from exc

    try:
        metadata_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"写入分析元数据失败，请检查磁盘空间和目录权限：{exc}") from exc

    try:
        write_pdf_report(report_path, result, annotated)
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"生成 PDF 报告失败：{exc}") from exc

    try:
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
            source_path = result_dir / result["source"]["file"]
            for path in (
                source_path,
                annotated_path,
                preview_path,
                summary_path,
                measurements_path,
                metadata_path,
                report_path,
            ):
                archive.write(path, arcname=path.name)
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"打包结果文件失败，请检查磁盘空间和目录权限：{exc}") from exc


def analyze_image(
    image_path: Path,
    result_dir: Path,
    settings: AnalysisSettings,
    sample_metadata: Optional[dict] = None,
) -> dict:
    mode = get_analysis_mode(settings.analysis_mode)
    image = _read_and_normalize(image_path)

    result_dir.mkdir(parents=True, exist_ok=True)
    height, width = image.shape[:2]

    scale_meta = {"source": "manual"}
    if settings.scale_px and settings.scale_px > 0:
        scale_px = float(settings.scale_px)
    else:
        scale_px, detected = detect_yellow_scale_gap(image)
        scale_meta = {"source": "auto", **detected}
    um_per_px = settings.scale_um / scale_px
    resolution_check = mode.validate_resolution(um_per_px)

    cx = int(round(settings.center_x * width))
    cy = int(round(settings.center_y * height))
    rx = int(round(settings.radius_x * width))
    ry = int(round(settings.radius_y * height))
    guard_px = max(_MIN_GUARD_PX, int(round(settings.guard_um / um_per_px)))
    effective_rx = max(_MIN_EFFECTIVE_RADIUS, rx - guard_px)
    effective_ry = max(_MIN_EFFECTIVE_RADIUS, ry - guard_px)

    x0 = max(0, cx - effective_rx - _ROI_EXPAND_LEFT)
    y0 = max(0, cy - effective_ry - _ROI_EXPAND_LEFT)
    x1 = min(width, cx + effective_rx + _ROI_EXPAND_RIGHT)
    y1 = min(height, cy + effective_ry + _ROI_EXPAND_RIGHT)

    gray_full = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = gray_full[y0:y1, x0:x1]
    region = _make_ellipse_mask(
        gray.shape,
        (cx - x0, cy - y0),
        (effective_rx, effective_ry),
    )

    sigma_px = float(np.clip(_GAUSSIAN_SIGMA_NOM / um_per_px, _GAUSSIAN_SIGMA_MIN, _GAUSSIAN_SIGMA_MAX))
    background = cv2.GaussianBlur(gray, (0, 0), sigma_px)
    contrast = cv2.subtract(background, gray)

    low = (
        (contrast >= settings.edge_threshold)
        & (region > 0)
        & (gray <= _BKG_GRAY_MAX)
    ).astype(np.uint8)
    seed = (
        (contrast >= settings.seed_threshold)
        & (region > 0)
        & (gray <= _SEED_GRAY_MAX)
    )
    low = cv2.morphologyEx(
        low,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, _MORPH_KERNEL),
    )

    _, labels, stats, centroids = cv2.connectedComponentsWithStats(low, connectivity=8)
    selected_labels = np.unique(labels[seed])
    selected_labels = selected_labels[selected_labels != 0]

    records = []
    counts = [0] * len(mode.bins)
    fiber_count = 0
    annotated = image.copy()

    for label_id in selected_labels:
        x, y, component_width, component_height, pixel_area = stats[label_id]
        component_mask = (
            labels[y : y + component_height, x : x + component_width] == label_id
        ).astype(np.uint8)
        contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        contour = max(contours, key=lambda item: cv2.arcLength(item, False))
        length_px = _maximum_feret_diameter(contour)
        length_um = length_px * um_per_px
        width_px = _minimum_feret_diameter(contour)
        width_um = width_px * um_per_px
        if not mode.should_report(length_um, settings.min_size_um):
            continue

        is_fiber, fiber_meta = _classify_as_fiber(component_mask, um_per_px)
        particle_class = "fiber" if is_fiber else "particle"

        if is_fiber:
            fiber_count += 1
        else:
            size_bin = mode.classify(length_um)
            bin_index = mode.bins.index(size_bin)
            counts[bin_index] += 1

        contour_full = contour + np.array(
            [[[x + x0, y + y0]]], dtype=np.int32
        )
        if is_fiber:
            color_bgr = (255, 200, 0)  # cyan fiber outline
        else:
            size_bin = mode.classify(length_um)
            color_bgr = size_bin.color_bgr
        cv2.drawContours(annotated, [contour_full], -1, color_bgr, _CONTOUR_THICKNESS, cv2.LINE_AA)
        records.append(
            {
                "id": f"auto-{int(label_id)}",
                "source": "automatic",
                "class": particle_class,
                "center_x_px": int(round(centroids[label_id][0])) + x0,
                "center_y_px": int(round(centroids[label_id][1])) + y0,
                "length_px": float(round(length_px, 3)),
                "length_um": float(round(length_um, 2)),
                "width_px": float(round(width_px, 3)),
                "width_um": float(round(width_um, 2)),
                "pixel_area": int(pixel_area),
                "bin": "纤维" if is_fiber else size_bin.label,
                "contour_px": contour_full.reshape(-1, 2).tolist(),
                **fiber_meta,
            }
        )

    cv2.ellipse(
        annotated,
        (cx, cy),
        (effective_rx, effective_ry),
        0,
        0,
        360,
        _REGION_ELLIPSE_COLOR,
        _REGION_ELLIPSE_THICKNESS,
        cv2.LINE_AA,
    )

    result = {
        "algorithm_version": ALGORITHM_VERSION,
        "analysis_mode": {"key": mode.key, "name": mode.name},
        "compliance": {
            "status": "partial" if mode.key == "vda19_1" else "not_applicable",
            "notice": (
                "已启用 VDA 19.1 尺寸分档、分辨率门槛与纤维分类；标准分割配置、"
                "边缘重构及完整计量追溯尚未完成，不构成完整合规声明。"
                if mode.key == "vda19_1"
                else "自定义业务模式，不作为 VDA 19.1 合规声明。"
            ),
        },
        "image": {"width": width, "height": height, "name": image_path.name},
        "counts": counts,
        "bins": _display_bins(mode, counts),
        "total": sum(counts),
        "fiber_count": fiber_count,
        "scale_px": float(round(scale_px, 2)),
        "scale_um": float(settings.scale_um),
        "um_per_px": float(round(um_per_px, 5)),
        "resolution_check": resolution_check,
        "scale_meta": scale_meta,
        "region": {
            "center_x_px": cx,
            "center_y_px": cy,
            "radius_x_px": effective_rx,
            "radius_y_px": effective_ry,
        },
        "settings": asdict(settings),
        "particles": records,
        "review_audit": [],
        "sample": sample_metadata or {},
        "source": {
            "file": f"original{image_path.suffix.lower() or '.img'}",
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        },
    }
    shutil.copyfile(image_path, result_dir / result["source"]["file"])
    _checked_imwrite(
        result_dir / "source.png",
        image,
        [cv2.IMWRITE_PNG_COMPRESSION, 3],
    )
    _write_result_files(result_dir, annotated, records, counts, result, scale_px, width, height, settings)
    return result
