# 🌌 Sankaku Automation Uploader (V2)

![UI](https://img.shields.io/badge/UI-PySide6-blue?style=for-the-badge)
![Automation](https://img.shields.io/badge/Automation-Playwright-green?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.12%2B-yellow?style=for-the-badge)

**Sankaku Automation Uploader** 是一个面向 Sankaku Complex 的桌面批量上传助手。它使用 PySide6 提供可视化任务队列，使用 Playwright 驱动浏览器完成上传、AI 标签读取、人工审核与提交，并支持普通批量上传和差分图（Diff Group）父子关联上传。

> 请只上传你有权发布的内容，并遵守 Sankaku Complex 的站点规则与当地法律法规。

---

## 功能概览

- **普通批量上传**：把多个文件加入队列后自动并发预取标签，再按任务顺序提交。
- **差分图模式（Diff Group）**：队列第 1 个文件作为根图；后续文件自动填写根帖子的 `parent` ID。也可以手动指定已有根帖子 ID。
- **AI 标签审核**：从上传页读取自动生成的标签，在桌面端实时显示、编辑、同步到网页并确认提交。
- **两种审核模式**：
  - **人工审核**：每个文件等待你确认、跳过或重试。
  - **快速通过**：检测到标签后自动提交，适合已确认流程稳定的批量任务。
- **任务持久化**：任务、队列、状态和设置会保存到本地 JSON，应用重启后可继续处理。
- **失败恢复**：支持暂停、恢复、重试失败项，并区分普通失败、标签错误和重复帖子。
- **浏览器 Profile 复用**：登录态保存在本地浏览器 Profile 中，避免每次重新登录。
- **代理与并发设置**：可配置代理服务器、浏览器通道、后台运行和并发预取页数。

---

## 运行环境

- Windows 10/11（推荐，仓库内置 `launcher.exe`）。
- Python **3.12+**。
- [`uv`](https://github.com/astral-sh/uv)（推荐的环境与依赖管理工具）。
- Playwright Chromium 浏览器引擎。
- 可访问 Sankaku Complex 的网络环境；如需要，请在应用设置中配置 HTTP/SOCKS 代理。

---

## 快速开始（Windows 推荐）

```powershell
git clone https://github.com/QR-0W/sankaku_uploader.git
cd sankaku_uploader
.\launcher.exe
```

`launcher.exe` 会自动完成：

1. 检查并安装 `uv`（如本机不存在）。
2. 同步 Python 依赖。
3. 安装 Playwright Chromium。
4. 启动桌面应用。

如果环境安装中断、虚拟环境损坏或依赖状态异常，可以强制重建：

```powershell
.\launcher.exe --rebuild
```

---

## 手动安装与启动

如果不使用启动器，也可以手动运行：

```powershell
# 安装/同步依赖
uv sync

# 安装 Playwright 浏览器引擎
uv run playwright install chromium

# 启动应用
uv run sankaku-uploader
```

使用传统 `pip` 也可以：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
playwright install chromium
sankaku-uploader
```

---

## 首次使用：登录与基础设置

首次上传前建议先完成以下设置：

1. 启动应用，进入左侧 **设置** 页。
2. 确认 **上传页 URL**，默认是：
   `https://www.sankakucomplex.com/en/posts/upload`
3. 确认 **浏览器 Profile** 路径。默认：
   `%USERPROFILE%\.sankaku-uploader\profile`
4. 首次登录建议取消勾选 **后台运行**，让浏览器可见。
5. 点击 **保存设置**。
6. 开始一个小测试任务。如果浏览器出现登录页，请在打开的浏览器窗口内手动登录并完成 2FA/验证。
7. 登录成功后，后续任务会复用同一个 Profile；需要无界面运行时再勾选 **后台运行**。

### 设置项说明

| 设置项 | 用途 | 建议 |
| --- | --- | --- |
| 上传页 URL | Sankaku 上传页面地址 | 保持默认英文上传页，便于解析标签与按钮 |
| 浏览器 Profile | 保存登录态、Cookie 和浏览器缓存 | 使用默认路径，除非需要多账号隔离 |
| 浏览器通道 | Playwright 使用的浏览器 channel | 默认 `msedge`；如不可用可留空使用 Playwright Chromium |
| 并发预取页数 | 普通批量上传时同时准备的页面数 | 默认 `8`；网络慢或机器性能弱时调低 |
| 代理服务器 | Playwright 浏览器代理 | 例如 `http://127.0.0.1:7890` 或 `socks5://127.0.0.1:1080` |
| 标签审核模式 | 人工审核 / 快速通过 | 新任务建议先用人工审核 |
| 后台运行 | 是否 headless 运行浏览器 | 首次登录关闭；稳定后可开启 |

---

## 使用流程

### 1. 创建上传队列

1. 进入左侧 **任务队列** 页。
2. 点击 **＋ 添加队列**。
3. 选择：
   - **普通队列 / Normal Batch**：互相独立的文件批量上传。
   - **差分队列 / Diff Group**：同一组差分图，自动建立父子关系。
4. 输入队列名称。

### 2. 添加文件

可以通过以下方式加入文件：

- 点击 **添加文件** 选择单个或多个文件。
- 点击 **添加文件夹** 批量导入文件夹内文件。
- 直接把文件拖拽到中间的上传队列区域。

队列支持拖拽排序。差分队列中排序非常重要：

- 第 1 个文件是根图（ROOT）。
- 第 2 个及之后的文件是子图（CHILD），会使用根图帖子 ID 作为 parent。

### 3. 配置差分图根帖子 ID（仅 Diff Group）

差分队列会显示 **差分模式 父/子 ID** 输入框：

- 留空：程序先上传第 1 个文件，并把生成的帖子 ID 作为后续文件的 parent。
- 填入已有帖子 ID：后续文件会直接挂到这个已有根帖子下。

### 4. 开始上传

1. 选中要运行的队列。
2. 点击 **开始**。
3. 应用会启动独立上传进程，并在左侧状态与日志页展示进度。
4. 普通批量模式下会并发打开多个页面预取 AI 标签；差分模式按顺序处理以保证父子关系正确。

### 5. 审核与编辑标签

在 **人工审核** 模式下，每个文件完成 AI 标签检测后会暂停等待处理：

1. 选中正在等待审核的文件。
2. 在右侧 **手动标签编辑** 中检查或修改标签。
   - 每行一个标签，或使用英文逗号分隔。
   - 空格会自动转换为下划线。
   - 重复标签会自动去重。
3. 点击 **应用标签** 可把本地编辑同步到网页。
4. 点击：
   - **确认提交**：使用当前标签提交。
   - **跳过**：跳过当前文件。
   - **重试**：重新处理当前文件。

界面会显示 `当前标签数 / 20`。少于 20 个标签时会提示风险；如果你确认仍要提交，可以在弹窗中继续。

### 6. 暂停、恢复与重试

- **暂停**：终止当前上传进程，并把未完成项重置为待处理状态。
- **恢复**：从当前队列的待处理/失败项继续上传。
- **重试失败项**：把失败、标签错误或重复项重置为待处理，便于再次运行。
- **上传全部队列**：按队列顺序依次运行全部队列。

---

## 本地数据位置

默认数据保存在用户目录下：

```text
%USERPROFILE%\.sankaku-uploader\
├─ profile\        # Playwright 持久化浏览器 Profile / 登录态
├─ v2\
│  ├─ tasks.json   # 队列、文件项、状态、标签和帖子 ID
│  └─ settings.json# 应用设置
└─ debug\          # 自动化失败时保存的调试页面/截图（如有）
```

如果需要切换账号，建议在设置页更换 **浏览器 Profile** 路径，而不是直接覆盖旧目录。

---

## 常见问题

### 提示需要登录或上传页一直停在登录界面

关闭 **后台运行**，重新开始任务，在弹出的浏览器中手动登录并完成验证。登录态保存到 Profile 后，再开启后台运行。

### 浏览器启动失败

- 默认浏览器通道是 `msedge`，请确认已安装 Microsoft Edge。
- 或在设置中清空 **浏览器通道**，让 Playwright 使用 `playwright install chromium` 安装的 Chromium。

### AI 标签长时间没有出现

- 检查网络和代理设置。
- 降低 **并发预取页数**。
- 切到日志页查看是否有上传页、按钮或标签解析错误。

### 代理/VPN 不稳定导致失败

在设置页填写稳定代理，例如：

```text
http://127.0.0.1:7890
socks5://127.0.0.1:1080
```

然后降低并发预取页数并重试失败项。

### 想完全重置依赖环境

```powershell
.\launcher.exe --rebuild
```

这会重建项目内 `.venv`。它不会删除 `%USERPROFILE%\.sankaku-uploader` 下的任务和登录 Profile。

---

## 开发与测试

```powershell
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest tests/ -v
```

项目入口：

```powershell
uv run sankaku-uploader
```

主要目录：

```text
src/sankaku_uploader/
├─ app.py                    # 应用入口
├─ domain/                   # 任务、文件项、状态机和设置模型
├─ application/              # 任务服务与上传进程控制
├─ infrastructure/           # Playwright 自动化与 JSON 持久化
└─ ui/                       # PySide6 桌面界面
```

---

## 许可证

本项目使用 [MIT License](LICENSE)。

<p align="center">Made with ❤️ for the Sankaku Community</p>
