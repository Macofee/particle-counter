# 粒径计数台

本地处理清洁度滤膜照片，自动读取右下角黄色比例尺，并按颗粒最大长度统计：

- 25＜n≤50 μm
- 50＜n≤100 μm
- 100＜n≤200 μm
- n＞200 μm

## 启动

在 macOS 上双击 `启动颗粒度计数台.command`。浏览器会自动打开 `http://127.0.0.1:8765`。

也可在终端运行：

```bash
cd /Users/p.zhang/Documents/Codex/2026-06-27/wo/outputs/particle_counter
python3 app.py
```

## 使用

1. 拖入原始图片。
2. 确认比例尺标注长度，默认 500 μm。黄线像素间距可留空自动识别。
3. 可将当前校准、区域和灵敏度保存为相机/滤膜参数模板，下次直接选用。
4. 调整蓝色椭圆，使其位于圆形滤膜边缘内侧。
5. 点击“开始统计”。
6. 下载标注原图、汇总 CSV、逐颗粒明细或完整结果包。

颗粒尺寸按轮廓凸包的最大 Feret 径（最大点间距）计算。

所有图片和结果只保存在本机 `data` 目录内，不会上传到网络。

## 开发记录

### 2026-06-27 — 代码审查与修复

当前分析算法版本：`2.0.0`。结果包中的 `analysis.json` 会记录该版本。

#### 第一轮（提交 `5bb85a3`）

| 文件 | 修复内容 |
|------|----------|
| `engine.py` | `analyze_image` 入口增加 16-bit → uint8 和灰度图 → BGR 归一化，防止 HSV/黄色检测崩溃 |
| `app.py` | `uuid.uuid4().hex[:12]` → 完整 32 位 hex，`/files/` 路由正则同步更新 |
| `engine.py` | 黄色比例尺检测的 11 个魔法数（面积阈值、HSV 范围、掩码分区等）提取为 `_YELLOW_*` 模块级常量 |
| `engine.py` | 结果文件写入（图片、CSV、JSON、ZIP）包裹 try/except，统一转为 `OSError` |

#### 第二轮（提交 `19626fd`）

| 文件 | 修复内容 |
|------|----------|
| `engine.py` | `_bin_index` 对超出分桶范围的颗粒静默错分至 "n>200" 桶 → 改为抛 `ValueError` |
| `tests/test_engine.py` | 新增 10 个测试：`_runs`(5) + `detect_yellow_scale_gap`(2) + `analyze_image`(2) + `_bin_index` 越界边界(1)，总计 15/15 通过 |
| `static/index.html` | `scalePx` 输入框 `min="1"` → `min="0"`，与留空自动识别语义一致 |
| `app.py` | POST `/api/analyze` 增加 `Sec-Fetch-Site` 轻量 CSRF 检查 |

#### 第三轮（提交 `673f88b`）

| 文件 | 修复内容 |
|------|----------|
| `static/app.js` | 分桶卡片与图例改为动态渲染（后端 `bins` 字段驱动），消除 4 号硬编码崩溃风险 |
| `static/app.js` | `calibrationReadout` 用 `textContent` + DOM 替换 `innerHTML`，消除 XSS 向量 |
| `engine.py` | 15 个分析参数魔法数（sigma、灰度阈值、JPEG 质量、ROI 边距等）提取为 `_*` 模块级常量 |
| `engine.py` | `analyze_image` 拆分为 `_read_and_normalize` + `_make_ellipse_mask` + `_write_result_files`，主函数 230→134 行 |
| `app.py` | 分析完成后 `finally` 清理上传原图，避免磁盘占用增长 |
| `requirements.txt` | `numpy>=1.24,<3` / `opencv-python>=4.8,<5` 添加版本上限 |
