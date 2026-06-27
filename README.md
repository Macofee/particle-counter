# 粒径计数台

本地处理清洁度滤膜照片，自动读取右下角黄色比例尺，并按颗粒最大长度统计：

- 25＜n≤50 μm
- 50＜n≤100 μm
- 100＜n≤200 μm
- n＞200 μm

## 启动

### macOS

双击 `启动颗粒度计数台.command`。浏览器会自动打开 `http://127.0.0.1:8765`。

### Windows

双击 `启动颗粒度计数台.bat`。首次运行会自动安装依赖。浏览器打开 `http://127.0.0.1:8765`。

### 命令行

```bash
cd 项目目录
pip install -r requirements.txt
python app.py
```

## 使用

1. 拖入原始图片。
2. 确认比例尺标注长度，默认 500 μm。黄线像素间距可留空自动识别。
3. 可将当前校准、区域和灵敏度保存为相机/滤膜参数模板，下次直接选用。
4. 调整蓝色椭圆，使其位于圆形滤膜边缘内侧。
5. 使用图片左上角的缩放工具检查细节；支持 25%–400%，也可按住 Command/Ctrl 滚动鼠标滚轮。
6. 点击“开始统计”。
7. 下载标注原图、汇总 CSV、逐颗粒明细或完整结果包。

分析完成后可进入人工复核：点击删除误检颗粒、按指定尺寸新增漏检颗粒，或通过三次点击把粘连颗粒拆成两颗；所有操作均可撤销。修正会重新生成结果文件，并记录在 `analysis.json` 的 `review_audit` 中。

填写样品编号、批次、检测人员和日期后，结果包会包含原始上传文件、SHA-256 摘要、中文 PDF 报告以及完整参数和人工复核记录。

颗粒尺寸按轮廓凸包的最大 Feret 径（最大点间距）计算。

所有图片和结果只保存在本机 `data` 目录内，不会上传到网络。

## 计量验证

使用人工确认的黄金数据集检查精确率、召回率、分档正确率、计数偏差、比例尺误差与重复性：

```bash
python3 validation.py validation/manifest.json --output validation-report.json
```

清单格式和建集方法见 `validation/README.md`。验收门槛由项目检测人员确定。

## 批量处理

同一套参数批量处理一个文件夹，输出逐图结果、汇总 CSV 和批次 ZIP：

```bash
python3 batch.py 原图文件夹 新的空输出文件夹 \
  --settings 参数.json --batch-id B-001 --operator 张三
```

为保护已有数据，输出文件夹非空时程序会停止，不会覆盖或删除文件。

## 构建桌面应用

PyInstaller **不支持交叉编译**，需在目标平台上执行。

### macOS

```bash
python3 -m pip install -r requirements-build.txt
./packaging/build_macos_app.command
```

产物位于 `dist/颗粒度计数台.app`，以无终端窗口模式运行。打包的应用数据保存在 `~/Library/Application Support/ParticleCounter`。

### Windows

在 Windows 终端（cmd 或 PowerShell）中：

```bat
pip install -r requirements-build.txt
packaging\build_windows_app.bat
```

或者直接双击 `packaging\build_windows_app.bat`。

产物位于 `dist\颗粒度计数台.exe`。打包的应用数据保存在 `%APPDATA%\ParticleCounter`。

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

#### 第四轮（提交 `d49dcdf`）

| 文件 | 修复内容 |
|------|----------|
| `static/styles.css` | 新增 `.zoom-toolbar` 控件样式 |
| `static/index.html` | stage 区域加入缩放工具条（− / 百分比 / ＋） |
| `static/app.js` | 实现 25%–400% 步进缩放、Command/Ctrl+滚轮缩放、重置适配窗口 |

#### 第五轮（提交 `1cc264f`）

| 文件 | 修复内容 |
|------|----------|
| `app.py` | 新增 `_get_app_data_dir()`，按 Windows/macOS/Linux 选择应用数据目录 |
| `reporting.py` | 字体候选列表添加 4 个 Windows 中文字体（微软雅黑、黑体、宋体） |
| `启动颗粒度计数台.bat` | 新建 Windows 启动脚本（UTF-8 编码，自动安装依赖） |
| `packaging/build_windows_app.bat` | 新建 Windows PyInstaller 打包脚本 |
| `README.md` | 启动与构建章节增加 Windows 说明 |

#### 其他

| 提交 | 说明 |
|------|------|
| `f8b7c6b` / `cb318d7` | `.gitignore` 补充 `.pytest_cache`、`venv`、`.vscode`、`.idea`、`.env`、`*.log` |
| `f6e63ee` / `d4f357e` / `8d282d3` / `8782fc0` / `012eb62` | 人工复核、PDF 报告、批量处理、macOS 打包（由其他会话完成） |

测试覆盖：当前共 **27** 个测试全部通过，覆盖引擎核心、黄色检测、图像归一化、复核流程、批量处理和计量验证。
