# 项目结构

## 目录职责

```text
TeachingSystem/
├── webapp.py              # Flask 应用入口
├── webapp_core/           # Web 接口、问答编排和教学业务
├── agenticRAG/            # 检索、回答生成、模型配置和 LangGraph 检查点
├── frontend/              # React + TypeScript 前端
├── scripts/               # 启动、数据准备、索引和维护工具
├── tests/                 # 回归测试
├── docker/                # C 程序与安全实验运行环境
├── docs/                  # 本地使用说明和设计记录（不纳入 Git）
├── data/                  # 本地课程资料、题库和业务数据库（不纳入 Git）
│   └── backups/           # 历史数据与修改前文件备份
├── storage/               # 三门课程当前使用的检索索引
├── output/                # 文档成品、验收报告和维护记录
├── tmp/                   # 临时文件、运行日志和本地验证输出
├── pyproject.toml         # Python 依赖和打包配置
└── .env                   # 本地模型、服务和密钥配置
```

`.codex/` 保存本项目的 Codex 运行配置。`.vscode/` 属于本地编辑器配置。

## 业务目录

后端、前端组件和测试按业务归类。定位一个功能时，先进入对应业务目录，再找路由、服务、组件或测试；新增代码放入已有业务目录。

| 业务 | 后端 `webapp_core/` | 前端 `frontend/src/components/` | 测试 `tests/` |
| --- | --- | --- | --- |
| 问答、检索、代码分析、知识图谱 | `chat/` | `chat/` | `chat/` |
| 诊断、计划、训练、学习记录 | `learning/` | `learning/` | `learning/` |
| 个人助理、考试计划、助理记忆 | `assistant/` | `assistant/` | `assistant/` |
| 身份、班级、作业、备课、评测复核 | `school/` | `school/`、`auth/` | `school/` |
| 异步执行、会话存储、工作流状态 | `runtime/` | — | `runtime/` |
| 检索与回答底层能力 | `agenticRAG/`（项目根目录） | — | `agentic/` |
| 多业务共用的展示与交互 | — | `shared/` | — |

`webapp_core/config.py` 保留公共配置；`chat/auto_runtime.py` 是现有聊天与学习功能共用的模型客户端。前端 `shared/` 只放跨业务组件，目前包括 Markdown 展示和放弃修改确认。各业务的 API 和数据类型仍在 `frontend/src/` 原有位置。

保留原文件名，避免目录调整同时混入命名和业务逻辑变更。Python 使用完整包路径导入，前端使用直接相对路径导入，不保留旧路径转发模块。

## 测试入口

从项目根目录运行。`tests/fixtures/` 统一保存测试数据，各业务目录只放对应测试。

```bash
# 全部回归测试，使用独立聊天记录文件
WEB_CHAT_STORE_PATH=./tmp/test_web_chats.json python -m unittest discover -s tests -t .

# 单个业务目录
WEB_CHAT_STORE_PATH=./tmp/test_web_chats.json python -m unittest discover -s tests/learning -t .

# 单个测试模块
python -m unittest tests.chat.test_graph_service

# 前端类型检查和生产构建
npm --prefix frontend run build
```

## 工具入口

所有维护工具统一放在 `scripts/`，从项目根目录执行。Python 工具通过 `--help` 查看参数。

| 工具 | 用途 |
| --- | --- |
| `run.sh` | 启动后端；默认使用 conda teach，可通过 `PYTHON_BIN` 指定解释器 |
| `rebuild_embedding_indexes.py` | 重建三门课检索索引 |
| `build_question_bank_embeddings.py` | 重建题库向量索引 |
| `rebuild_operating_systems_from_pdf.py`、`extract_os_figures.py` | 操作系统教材文本、章节和插图准备 |
| `audit_question_bank.py` | 题库检查 |
| `graph_visual_with_neo4j.py` | 将检索图谱导入 Neo4j |
| `cleanup_workflows.py` | 预览或清理过期工作流记录 |
| `benchmark_auto_latency.py`、`check_qa_sources.py` | 问答耗时与引用来源检查 |
| `evaluate_problem_tutoring.py`、`evaluate_learning_diagnosis.py`、`evaluate_personalization_flow.py` | 辅导、诊断与个性化流程验收 |
| `replay_learning_demo.py` | 学习演示回放 |

旧的碎片清洗脚本依赖缺失模块，已移除；教材处理统一使用 PDF 重建工具。固定补齐到 300 题的旧生成脚本也已移除，避免重新引入去重时删除的题目。

## 数据与生成文件

- `data/`、`storage/` 是实际运行数据，不能作为缓存清理。`data/backups/` 单独保存可恢复的历史快照。
- `output/docs/` 保存论文等文档；`output/maintenance/` 保存目录整理记录。文档预览和制作辅助文件归入 `output/docs/`。
- `tmp/logs/` 放运行日志，`tmp/validation/` 放本地验证输出。删除运行中的日志不会停止服务，但会失去日志入口，应等进程结束后再清理。
- `__pycache__/`、`.pytest_cache/`、`.playwright-cli/` 是可再生缓存，已忽略版本控制。
- `frontend/dist/` 是前端构建产物，后端可能正在使用；`frontend/node_modules/` 是本地依赖。不要在服务运行时当作无用目录删除。
- `*.egg-info/` 是 Python 安装元数据，已忽略版本控制；当前可编辑安装可能依赖它。

安装和启动步骤见 [README.md](README.md)。`data/` 与 `docs/` 仅在本地保存，克隆仓库后需另行准备运行数据。
