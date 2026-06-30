# 粒径计数台

本地处理清洁度滤膜照片，自动读取右下角黄色比例尺，并提供两种独立分析模式。

自定义模式沿用现有分档：

- 25＜n≤50 μm
- 50＜n≤100 μm
- 100＜n≤200 μm
- n＞200 μm

VDA 19.1 模式（阶段 1）提供：

- E–N 标准尺寸等级，采用 `[下限, 上限)` 边界
- 50 μm 最小报告尺寸
- 50 μm 至少覆盖 10 像素的强制分辨率检查
- Feret max 颗粒长度与 Feret min 颗粒宽度同时测量
- 纤维自动分类：拉伸长度/最大内切圆直径 > 20 且宽度 ≤ 50 μm
- 纤维不计入尺寸分档，单独统计并在 CSV 中标记
- 在 JSON、CSV、PDF 和批处理结果中记录分析模式及分辨率证据
- 禁止通过手工输入尺寸新增或拆分颗粒，避免把替代轮廓误作标准测量

> 当前 VDA 模式是标准化工作的第一阶段。标准分割配置、边缘颗粒重构和完整计量追溯尚未完成，因此软件不会把当前结果声明为完整符合 VDA 19.1。

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
2. 选择自定义模式或 VDA 19.1 模式。
3. 确认比例尺标注长度，默认 500 μm。两条黄线最外侧边缘的像素间距可留空自动识别。
4. 可将当前模式、校准、区域和灵敏度保存为相机/滤膜参数模板，下次直接选用。
5. 调整蓝色椭圆，使其位于圆形滤膜边缘内侧。
6. 使用图片左上角的缩放工具检查细节；支持 25%–400%，也可按住 Command/Ctrl 滚动鼠标滚轮。
7. 点击“开始统计”。
8. 下载标注原图、汇总 CSV、逐颗粒明细或完整结果包。

分析完成后可进入人工复核。自定义模式支持删除、按指定尺寸新增和拆分颗粒；VDA 模式阶段 1 仅允许删除误检颗粒和撤销。修正会重新生成结果文件，并记录在 `analysis.json` 的 `review_audit` 中。

填写样品编号、批次、检测人员和日期后，结果包会包含原始上传文件、SHA-256 摘要、中文 PDF 报告以及完整参数和人工复核记录。

颗粒尺寸按轮廓凸包的最大 Feret 径（最大点间距）计算。

所有图片和结果只保存在本机 `data` 目录内，不会上传到网络。

## 项目文档

- [项目方案与技术口径](PROJECT_PLAN.md)
- [计量验证集说明](validation/README.md)
- [更新日志](CHANGELOG.md)

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

#### 第六轮 — 英文 Windows 兼容性修复（分支 `fix/english-windows-image-import`）

> **背景**：在英文 Windows 操作系统中，导入图片后点击"开始统计"无任何响应。经排查发现三类根因：OpenCV 在 Windows 上不支持 UTF-8 路径、HTTP 异常处理存在双重故障逃逸、前端缺少超时和空值防护。

| 文件 | 修复内容 |
|------|----------|
| `engine.py` | `cv2.imread` → `np.frombuffer` + `cv2.imdecode`；`cv2.imwrite` → `cv2.imencode` + `write_bytes`，避免 Windows C 运行时 `fopen` 对 Unicode 路径不兼容 |
| `engine.py` | `analyze_image` 返回值中 `round(numpy_float64, N)` 显式转为 Python `float()`，避免 numpy < 1.24 时 `json.dumps` 抛 `TypeError` |
| `app.py` | `send_json` 内部捕获 `ConnectionError`/`OSError`，防止客户端断连时异常逃逸出 `do_POST` 导致 HTTP 线程静默死亡 |
| `app.py` | 模块级 `mkdir` 移入 `_ensure_data_dirs()` 函数，由 `main()` 显式调用，失败时打印错误并以非零退出码退出 |
| `app.py` | `do_GET` 添加 `try/except` 保护；`main()` 中 Windows 控制台适配 UTF-8 编码，防止 `print()` 中文导致 `UnicodeEncodeError` |
| `static/app.js` | 分析请求添加 2 分钟超时（`AbortController`）；修正 `response.json()` 调用顺序（先检查 `ok` 再解析）；`submitReview` 添加 30 秒超时 |
| `static/app.js` | `renderResult` 添加 `data`/`files`/`bins` null 安全检查；`setFile` 同时检查扩展名和 MIME 类型，兼容 TIFF/BMP |
| `启动颗粒度计数台.bat` | 移除 `2>nul`，添加 pip 依赖安装错误检查；设置 `PYTHONUTF8=1` 环境变量 |
| `tests/test_engine.py` | 更新 `test_false_imencode_result_raises_os_error`：mock 从 `cv2.imwrite` 改为 `cv2.imencode` |

测试覆盖：仍为 **27** 个测试全部通过，回归验证通过。

### 2026-06-30 — v1.0.1 发布

当前软件版本：`1.0.1`，分析算法版本：`2.0.0`。

本次合并 `fix/english-windows-image-import` 分支到 `main` 并发布 `v1.0.1`，主要变更：

| 类别 | 内容 |
|------|------|
| UI 修复 | 扫描动画拆分暗色遮罩与扫描线层：遮罩覆盖整张放大后的图片，扫描线保持 100% 视图时的视觉比例并填满显示窗口 |
| 测试 | 新增 `test_unicode_path_read_and_write`，覆盖中文/Unicode 路径下的图片读取与结果写入 |
| 构建 | 跟踪 `颗粒度计数台.spec`，修正 `.gitignore` 对 `*.spec` 的错误忽略；忽略自动生成的 `skills-lock.json` |
| 版本 | `SOFTWARE_VERSION` 更新为 `1.0.1` |

测试覆盖：当前共 **28** 个测试全部通过，覆盖引擎核心、黄色检测、图像归一化、Unicode 路径、复核流程、批量处理和计量验证。

#### 其他

| 提交 | 说明 |
|------|------|
| `f8b7c6b` / `cb318d7` | `.gitignore` 补充 `.pytest_cache`、`venv`、`.vscode`、`.idea`、`.env`、`*.log` |
| `f6e63ee` / `d4f357e` / `8d282d3` / `8782fc0` / `012eb62` | 人工复核、PDF 报告、批量处理、macOS 打包（由其他会话完成） |

测试覆盖：当前共 **28** 个测试全部通过，覆盖引擎核心、黄色检测、图像归一化、Unicode 路径、复核流程、批量处理和计量验证。
