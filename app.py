from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from engine import AnalysisSettings, analyze_image
from review import apply_review_action


ROOT = Path(__file__).resolve().parent
STATIC = Path(getattr(sys, "_MEIPASS", ROOT)) / "static"
DATA_ROOT = (
    Path.home() / "Library" / "Application Support" / "ParticleCounter"
    if getattr(sys, "frozen", False)
    else ROOT / "data"
)
UPLOADS = DATA_ROOT / "uploads"
RESULTS = DATA_ROOT / "results"
UPLOADS.mkdir(parents=True, exist_ok=True)
RESULTS.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 350 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
MAX_REVIEW_BYTES = 64 * 1024
JOB_LOCKS: dict[str, threading.Lock] = {}


def result_files(job_id: str, result: dict = None) -> dict[str, str]:
    files = {
        "preview": f"/files/{job_id}/preview.jpg",
        "annotated": f"/files/{job_id}/annotated.jpg",
        "summary": f"/files/{job_id}/summary.csv",
        "measurements": f"/files/{job_id}/measurements.csv",
        "report": f"/files/{job_id}/report.pdf",
        "bundle": f"/files/{job_id}/result_bundle.zip",
    }
    if result and result.get("source", {}).get("file"):
        files["original"] = f"/files/{job_id}/{result['source']['file']}"
    return files


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], dict]:
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    fields: dict[str, str] = {}
    files: dict[str, dict] = {}
    if not message.is_multipart():
        return fields, files
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            files[name] = {
                "filename": Path(filename).name,
                "content_type": part.get_content_type(),
                "data": payload,
            }
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "ParticleCounter/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, download: bool = False) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path.startswith("/files/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3 or not re.fullmatch(r"[a-f0-9]{32}", parts[1]):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            candidate = (RESULTS / parts[1] / Path(parts[2]).name).resolve()
            if RESULTS.resolve() not in candidate.parents:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            self.send_file(candidate, download=parts[2] != "preview.jpg")
            return
        static_name = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = (STATIC / static_name).resolve()
        if STATIC.resolve() not in candidate.parents and candidate != STATIC.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_file(candidate)

    def do_POST(self) -> None:
        if self.path not in {"/api/analyze", "/api/review"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        # 轻量 CSRF 防护：拒绝跨站 POST 请求
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_json({"error": "不允许的外部请求"}, status=403)
            return
        if self.path == "/api/review":
            self.handle_review()
            return
        image_path = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                raise ValueError("文件为空或超过 350 MB。")
            body = self.rfile.read(length)
            fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
            upload = files.get("image")
            if not upload:
                raise ValueError("请选择一张图片。")

            job_id = uuid.uuid4().hex
            suffix = Path(upload["filename"]).suffix.lower() or ".jpg"
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise ValueError("文件格式不支持，请使用 JPG、PNG、TIFF 或 BMP 图片。")
            image_path = UPLOADS / f"{job_id}{suffix}"
            image_path.write_bytes(upload["data"])

            def number(name: str, fallback: float) -> float:
                value = fields.get(name, "").strip()
                result = float(value) if value else fallback
                if not math.isfinite(result):
                    raise ValueError(f"参数 {name} 不是有效数字。")
                return result

            scale_px = number("scale_px", 0)
            settings = AnalysisSettings(
                scale_um=number("scale_um", 500),
                scale_px=scale_px if scale_px > 0 else None,
                center_x=number("center_x", 49) / 100.0,
                center_y=number("center_y", 49) / 100.0,
                radius_x=number("radius_x", 47) / 100.0,
                radius_y=number("radius_y", 46) / 100.0,
                edge_threshold=int(number("edge_threshold", 20)),
                seed_threshold=int(number("seed_threshold", 40)),
                guard_um=number("guard_um", 130),
            )
            if not (0 < settings.scale_um < 100000):
                raise ValueError("比例尺长度不合理。")
            if settings.scale_px is not None and not (1 <= settings.scale_px <= 100000):
                raise ValueError("黄线间距不合理。")
            if not (0 < settings.center_x < 1 and 0 < settings.center_y < 1):
                raise ValueError("统计区域中心必须位于图片内。")
            if not (0.01 <= settings.radius_x <= 0.5 and 0.01 <= settings.radius_y <= 0.5):
                raise ValueError("统计区域半径不合理。")
            if not (
                settings.center_x - settings.radius_x >= 0
                and settings.center_x + settings.radius_x <= 1
                and settings.center_y - settings.radius_y >= 0
                and settings.center_y + settings.radius_y <= 1
            ):
                raise ValueError("统计区域不能超出图片边界。")
            if not (0 <= settings.edge_threshold <= 255 and 0 <= settings.seed_threshold <= 255):
                raise ValueError("识别阈值必须在 0–255 之间。")
            if settings.edge_threshold >= settings.seed_threshold:
                raise ValueError("边缘阈值必须小于深色核心阈值。")
            if not (0 <= settings.guard_um < 100000):
                raise ValueError("圆边内缩值不合理。")

            def text_field(name: str, limit: int) -> str:
                return fields.get(name, "").strip()[:limit]

            sample_metadata = {
                "sample_id": text_field("sample_id", 80),
                "batch_id": text_field("batch_id", 80),
                "operator": text_field("operator", 80),
                "inspection_date": text_field("inspection_date", 20),
                "notes": text_field("notes", 500),
            }
            result = analyze_image(image_path, RESULTS / job_id, settings, sample_metadata)
            result["job_id"] = job_id
            result["files"] = result_files(job_id, result)
            self.send_json(result)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except Exception as error:
            self.send_json({"error": f"分析失败：{error}"}, status=500)
        finally:
            if image_path is not None:
                try:
                    image_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def handle_review(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REVIEW_BYTES:
                raise ValueError("人工复核请求为空或过大。")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            job_id = str(payload.get("job_id", ""))
            if not re.fullmatch(r"[a-f0-9]{32}", job_id):
                raise ValueError("结果任务编号无效。")
            action = payload.get("action")
            if not isinstance(action, dict):
                raise ValueError("人工复核操作无效。")
            result_dir = RESULTS / job_id
            if not result_dir.is_dir():
                raise ValueError("未找到待复核的分析结果。")
            lock = JOB_LOCKS.setdefault(job_id, threading.Lock())
            with lock:
                result = apply_review_action(result_dir, action, str(payload.get("actor", "操作员")))
            result["job_id"] = job_id
            result["files"] = result_files(job_id, result)
            self.send_json(result)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, status=400)
        except Exception as error:
            self.send_json({"error": f"人工复核失败：{error}"}, status=500)


def open_browser(port: int) -> None:
    time.sleep(0.8)
    webbrowser.open(f"http://127.0.0.1:{port}")


def main() -> None:
    port = int(os.environ.get("PARTICLE_COUNTER_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if os.environ.get("PARTICLE_COUNTER_NO_BROWSER") != "1":
        threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    print(f"颗粒度计数台已启动：http://127.0.0.1:{port}")
    print("按 Control-C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
