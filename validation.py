from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from engine import ALGORITHM_VERSION, AnalysisSettings, analyze_image


SCHEMA_VERSION = 1


def _load_measurements(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return [
            {
                "center_x_px": float(row["center_x_px"]),
                "center_y_px": float(row["center_y_px"]),
                "length_um": float(row["length_um"]),
                "bin": row["bin"],
            }
            for row in csv.DictReader(file)
        ]


def _match_particles(expected: list[dict], actual: list[dict], position_tolerance_px: float) -> list[tuple[int, int]]:
    candidates = []
    for expected_index, expected_particle in enumerate(expected):
        for actual_index, actual_particle in enumerate(actual):
            distance = math.hypot(
                expected_particle["center_x_px"] - actual_particle["center_x_px"],
                expected_particle["center_y_px"] - actual_particle["center_y_px"],
            )
            if distance <= position_tolerance_px:
                candidates.append((distance, expected_index, actual_index))

    matches = []
    used_expected = set()
    used_actual = set()
    for _, expected_index, actual_index in sorted(candidates):
        if expected_index in used_expected or actual_index in used_actual:
            continue
        used_expected.add(expected_index)
        used_actual.add(actual_index)
        matches.append((expected_index, actual_index))
    return matches


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _settings_from_case(case: dict) -> AnalysisSettings:
    allowed = {item.name for item in fields(AnalysisSettings)}
    supplied = case.get("settings", {})
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise ValueError(f"验证用例 {case.get('id', '?')} 含未知设置：{', '.join(unknown)}")
    return AnalysisSettings(**supplied)


def _evaluate_case(case: dict, manifest_dir: Path, acceptance: dict) -> dict:
    case_id = str(case.get("id", "")).strip()
    if not case_id:
        raise ValueError("每个验证用例都必须提供 id。")
    image_path = (manifest_dir / case["image"]).resolve()
    if not image_path.is_file():
        raise ValueError(f"验证图片不存在：{image_path}")

    expected = case.get("expected", {})
    expected_particles = expected.get("particles", [])
    position_tolerance = float(case.get("tolerances", {}).get("position_px", 8.0))
    settings = _settings_from_case(case)

    with tempfile.TemporaryDirectory(prefix=f"particle-validation-{case_id}-") as tmpdir:
        first_dir = Path(tmpdir) / "first"
        second_dir = Path(tmpdir) / "second"
        first = analyze_image(image_path, first_dir, settings)
        second = analyze_image(image_path, second_dir, settings)
        actual_particles = _load_measurements(first_dir / "measurements.csv")
        repeated_particles = _load_measurements(second_dir / "measurements.csv")

    matches = _match_particles(expected_particles, actual_particles, position_tolerance)
    true_positives = len(matches)
    false_positives = len(actual_particles) - true_positives
    false_negatives = len(expected_particles) - true_positives
    precision = _safe_ratio(true_positives, true_positives + false_positives)
    recall = _safe_ratio(true_positives, true_positives + false_negatives)
    bin_correct = sum(
        expected_particles[expected_index].get("bin") == actual_particles[actual_index]["bin"]
        for expected_index, actual_index in matches
    )
    bin_accuracy = _safe_ratio(bin_correct, true_positives)
    length_errors = [
        abs(
            float(expected_particles[expected_index]["length_um"])
            - actual_particles[actual_index]["length_um"]
        )
        for expected_index, actual_index in matches
        if "length_um" in expected_particles[expected_index]
    ]

    expected_counts = expected.get("counts")
    count_errors = None
    max_abs_count_error = 0
    if expected_counts is not None:
        if len(expected_counts) != len(first["counts"]):
            raise ValueError(f"验证用例 {case_id} 的 expected.counts 桶数不匹配。")
        count_errors = [actual - wanted for actual, wanted in zip(first["counts"], expected_counts)]
        max_abs_count_error = max((abs(value) for value in count_errors), default=0)

    expected_scale = expected.get("scale_px")
    scale_error_px = abs(first["scale_px"] - float(expected_scale)) if expected_scale is not None else None
    repeatable = (
        first["counts"] == second["counts"]
        and first["scale_px"] == second["scale_px"]
        and actual_particles == repeated_particles
    )

    checks = {
        "precision": precision >= float(acceptance["min_precision"]),
        "recall": recall >= float(acceptance["min_recall"]),
        "bin_accuracy": bin_accuracy >= float(acceptance["min_bin_accuracy"]),
        "count_error": count_errors is None
        or max_abs_count_error <= int(acceptance["max_abs_count_error_per_bin"]),
        "scale_error": scale_error_px is None
        or scale_error_px <= float(acceptance["max_scale_error_px"]),
        "repeatability": repeatable or not bool(acceptance["require_repeatability"]),
    }
    return {
        "id": case_id,
        "image": str(image_path),
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": {
            "expected_particles": len(expected_particles),
            "detected_particles": len(actual_particles),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "bin_accuracy": round(bin_accuracy, 6),
            "mean_abs_length_error_um": round(sum(length_errors) / len(length_errors), 4)
            if length_errors
            else None,
            "count_errors": count_errors,
            "scale_error_px": round(scale_error_px, 4) if scale_error_px is not None else None,
            "repeatable": repeatable,
        },
    }


def validate_manifest(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"验证清单 schema_version 必须为 {SCHEMA_VERSION}。")
    acceptance = manifest.get("acceptance")
    required = {
        "min_precision",
        "min_recall",
        "min_bin_accuracy",
        "max_abs_count_error_per_bin",
        "max_scale_error_px",
        "require_repeatability",
    }
    if not isinstance(acceptance, dict) or required - set(acceptance):
        missing = ", ".join(sorted(required - set(acceptance or {})))
        raise ValueError(f"验证清单缺少 acceptance 字段：{missing}")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("验证清单至少需要一个 cases 用例。")

    case_reports = [_evaluate_case(case, manifest_path.parent, acceptance) for case in cases]
    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "passed": all(case["passed"] for case in case_reports),
        "acceptance": acceptance,
        "cases": case_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行颗粒度计数台黄金数据集验证。")
    parser.add_argument("manifest", type=Path, help="验证清单 JSON 路径")
    parser.add_argument("--output", type=Path, default=Path("validation-report.json"))
    args = parser.parse_args()
    try:
        report = validate_manifest(args.manifest)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"验证失败：{error}")
        return 2

    print(f"验证{'通过' if report['passed'] else '未通过'}：{len(report['cases'])} 个用例")
    print(f"报告：{args.output.resolve()}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
