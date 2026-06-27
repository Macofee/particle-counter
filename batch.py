from __future__ import annotations

import argparse
import csv
import json
import uuid
import zipfile
from dataclasses import fields
from datetime import date
from pathlib import Path

from engine import ALGORITHM_VERSION, AnalysisSettings, analyze_image


SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def _load_settings(path: Path = None) -> AnalysisSettings:
    if path is None:
        return AnalysisSettings()
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {item.name for item in fields(AnalysisSettings)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"批处理设置含未知字段：{', '.join(unknown)}")
    return AnalysisSettings(**payload)


def run_batch(
    input_dir: Path,
    output_dir: Path,
    settings: AnalysisSettings,
    batch_id: str = "",
    operator: str = "",
    inspection_date: str = "",
) -> dict:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"输入文件夹不存在：{input_dir}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("输出文件夹非空。为保护已有结果，请选择新的空文件夹。")
    output_dir.mkdir(parents=True, exist_ok=True)
    images = sorted(
        path for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not images:
        raise ValueError("输入文件夹中没有支持的图片。")

    items = []
    for image_path in images:
        case_id = f"{image_path.stem[:50]}-{uuid.uuid4().hex[:8]}"
        result_dir = output_dir / case_id
        try:
            result = analyze_image(
                image_path,
                result_dir,
                settings,
                {
                    "sample_id": image_path.stem,
                    "batch_id": batch_id,
                    "operator": operator,
                    "inspection_date": inspection_date,
                    "notes": "批量处理",
                },
            )
            items.append(
                {
                    "name": image_path.name,
                    "status": "ok",
                    "result_dir": case_id,
                    "counts": result["counts"],
                    "total": result["total"],
                    "scale_px": result["scale_px"],
                }
            )
        except Exception as error:
            items.append({"name": image_path.name, "status": "error", "error": str(error)})

    summary_path = output_dir / "batch_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["文件", "状态", "25<n<=50", "50<n<=100", "100<n<=200", "n>200", "合计", "比例尺_px", "错误"])
        for item in items:
            counts = item.get("counts", ["", "", "", ""])
            writer.writerow(
                [
                    item["name"],
                    item["status"],
                    *counts,
                    item.get("total", ""),
                    item.get("scale_px", ""),
                    item.get("error", ""),
                ]
            )

    report = {
        "algorithm_version": ALGORITHM_VERSION,
        "batch_id": batch_id,
        "operator": operator,
        "inspection_date": inspection_date,
        "total_files": len(items),
        "successful_files": sum(item["status"] == "ok" for item in items),
        "failed_files": sum(item["status"] == "error" for item in items),
        "items": items,
    }
    report_path = output_dir / "batch_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    bundle_path = output_dir / "batch_bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(summary_path, arcname=summary_path.name)
        archive.write(report_path, arcname=report_path.name)
        for item in items:
            if item["status"] != "ok":
                continue
            result_bundle = output_dir / item["result_dir"] / "result_bundle.zip"
            archive.write(result_bundle, arcname=f"{item['result_dir']}/result_bundle.zip")
    report["summary"] = str(summary_path)
    report["bundle"] = str(bundle_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="批量处理文件夹内的颗粒度图片。")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--settings", type=Path, help="AnalysisSettings JSON 文件")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--operator", default="")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    try:
        report = run_batch(
            args.input_dir,
            args.output_dir,
            _load_settings(args.settings),
            args.batch_id,
            args.operator,
            args.date,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"批处理失败：{error}")
        return 2
    print(f"完成：成功 {report['successful_files']}，失败 {report['failed_files']}")
    print(f"汇总：{report['summary']}")
    print(f"结果包：{report['bundle']}")
    return 0 if report["failed_files"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
