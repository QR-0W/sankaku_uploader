# 🌌 Sankaku Automation Uploader (V2)

![Premium Interface](https://img.shields.io/badge/UI-Modern_%26_Sleek-blueviolet?style=for-the-badge)
![Tech Stack](https://img.shields.io/badge/Stack-Python_%7C_Playwright_%7C_PySide6-blue?style=for-the-badge)
![Status](https://img.shields.io/badge/Status-Optimized-success?style=for-the-badge)

**Sankaku Automation Uploader** 是一款专为 Sankaku Complex 打造的高效、智能的自动化上传工具。针对大批量操作与复杂的差分图（Diff Group）逻辑进行了深度优化，集成了 AI 标签自动提取、实时人工审核与多线程并发预取功能。

---

## ✨ 核心特性

- **🚀 并发加速预取**：支持在大批量上传前，并发打开多个浏览器 Page 预加载 AI 标签，极大缩短等待时间。
- **🤖 智能 AI 标签隔离**：通过底层隔离与时序优化，确保在高并发环境下每张图片的 AI 标签获取均准确无误。
- **🎭 双模式无缝切换**：
  - **普通模式（Normal Batch）**：一键并行预取，顺序提交，支持极速上传。
  - **差分模式（Diff Group）**：智能关联主帖 ID，自动建立父子关系，完美处理系列图。
- **🛡️ 状态持久化**：采用本地 JSON 数据库存储任务进度，支持断点续传，不惧闪退。
- **🔧 极致容错与纠错**：
  - 自动检测并分类 **Tag 冲突** 与 **重复帖子（Duplicate）**。
  - 针对后台 Tab 休眠导致的 DOM 僵死，内置“强行唤醒”与“宽限期”机制。
  - 自动注入 `en-US` 语言环境变量，强制英文 UI 确保解析一致性。

---

## 📸 界面预览

> [!NOTE]
> 界面采用深色系电报风设计，支持拖拽文件、实时日志滚动与标签实时编辑同步。

---

## 🛠️ 技术架构

项目基于 **领域驱动设计（DDD）** 思想重构：

- **`domain/`**：核心业务逻辑、状态机模型及业务规则。
- **`application/`**：应用服务层，负责任务编排与并发控制。
- **`infrastructure/`**：基础设施层，涵盖 Playwright 自动化引擎与持久化仓储。
- **`ui/`**：基于 PySide6 构建的高性能桌面交互界面。

---

### 1. 极速启动 (Windows)

1. **克隆仓库**：
   ```bash
   git clone https://github.com/QR-0W/sankaku_auto_uploader.git
   cd sankaku_auto_uploader
   ```
2. **启动程序**：
   双击 **`launcher.exe`**。
   - 程序会自动检测环境，并完成所有必要的配置（如下载 Python、同步依赖库、安装浏览器引擎）。
   - 一切就绪后，应用将自动开启。

> [!TIP]
> **遇到环境问题？**
> 如果安装过程中断或环境损坏，你可以通过命令行运行 `launcher.exe --rebuild` 来强制重置并重新安装环境。

### 2. 手动安装 (通用)

如果你不使用启动器，可以完全手动执配置：

```bash
# 安装 uv (推荐) 并运行
uv run sankaku-uploader
```

```bash
# 创建并激活虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

# 安装依赖
pip install -e .[dev]

# 安装 Playwright 浏览器
playwright install chromium
```

---

## 🧪 开发与测试

本项目内置了完备的单元测试与集成测试覆盖，确保逻辑稳定性：

```bash
# 运行 pytest
pytest tests/ -v
```

---

## 📌 参与贡献

欢迎通过 Issue 提交反馈，或直接发起 Pull Request。

- **开发者**：Antigravity (AI Assistant) & USER
- **许可证**：MIT License

---

<p align="center">Made with ❤️ for the Sankaku Community</p>
