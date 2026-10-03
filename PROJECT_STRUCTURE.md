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

## 后端模块定位

| 位置 | 职责 |
| --- | --- |
| `webapp_core/chat_*.py` | 问答入口、路由、Auto 编排、检索适配与流式输出 |
| `webapp_core/workflow_runs.py` | 执行状态、恢复、取消、并发保护和过期记录清理 |
| `agenticRAG/workflow_checkpoint.py` | LangGraph 持久化检查点 |
| `webapp_core/learning_dialogue.py`、`learning_plan.py`、`learning_workflow.py` | 互动诊断、个性化计划与学习工作流 |
| `webapp_core/learning_store.py`、`learning_service.py`、`learning_routes.py` | 学习记录、训练业务和接口 |
| `webapp_core/learning_c*.py`、`learning_program.py` | C 语言训练和程序评测 |
| `webapp_core/learning_os_foundations.py`、`learning_banker.py`、`learning_pv.py`、`learning_readers_writers.py` | 操作系统训练 |
| `webapp_core/learning_security*.py` | 安全训练与实验 |
| `webapp_core/learning_curriculum.py`、`learning_path.py`、`learning_followup.py`、`learning_memory.py` | 课程关系、学习路径、推荐效果与学习记忆 |
| `webapp_core/school_*.py`、`task_service.py`、`grading_review.py` | 身份、班级、作业测验与评测复核 |

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
