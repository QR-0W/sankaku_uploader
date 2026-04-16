# Sankaku Uploader (V2)

基于新版需求文档重构的 Sankaku 批量上传辅助工具。

## 目标

- 支持普通批量与差分图任务
- 支持任务状态持久化与恢复
- 支持 AI 标签读取、人工确认、失败重试
- 采用分层结构（UI / 应用服务 / 站点交互 / 存储）

## 快速开始

```bash
pip install -e .[dev]
pytest
sankaku-uploader
```

## 项目结构

```text
src/sankaku_uploader/
  domain/           # 数据模型、状态机、业务规则
  application/      # 任务编排与服务层
  infrastructure/   # 本地存储与站点自动化
  ui/               # PySide6 桌面界面
```
