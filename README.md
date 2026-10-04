# AgenticRAG

AgenticRAG 是一个面向三门固定课程的 Web 学习助手，当前覆盖：

- `C_program`：C 语言
- `operating_systems`：操作系统
- `cybersec_lab`：网络安全实验

当前主路径是 `Flask + LangGraph 工作流 + LightRAG/OpenAI + Pydantic 结构化输出`。DeepSearch、互动诊断和学习计划使用 LangGraph 编排，普通业务继续使用现有服务。前端使用 `React + TypeScript` 工作台展示聊天、执行流程和 Neo4j 局部知识图谱。

## 主要能力

- `Instant / DeepSearch` 两种回答模式：Instant 直接流式回答，不检索；DeepSearch 在所选课程内拆题、检索、评审证据后回答。历史 Auto 会话按 Instant 打开，聊天记录保留。
- 回答流程显示服务端首段文字、评审和回答总耗时。已生成文本及时显示，不额外逐字播放；要求简短回答时不强制展开完整章节模板。
- DeepSearch 中断后可点击“继续上次回答”，复用已完成步骤的检查点；进度默认保存在 `data/learning_workflows.sqlite3`，删除聊天时同步清理。Instant 保持直接流式调用。
- 基于学科路由和检索网关的多课程问答
- 回答引用直接对应本次检索的课程原文，可展开核对文件及完整片段；无对应原文的引用编号明确标记。图谱关联资料与回答出处分开展示。
- 题目辅导与过程化解题
- 训练中心：FIFO/LRU、FCFS/RR 过程作答、首错核验、分级提示、订正、新题复测与学习记录
- C 代码分析、编译诊断与受限执行
- Web 会话管理、SSE 流式输出、摘要记忆
- React 工作台、Workflow 节点状态展示、Neo4j 局部知识图谱展示

## 目录说明

- `webapp.py`：Flask 入口，负责创建应用、注册路由和启动 Web 服务
- `webapp_core/`：Web 编排层，包含路由判断、流式输出、会话管理、题目辅导、代码分析等模块
- `webapp_core/auto_runtime.py`：聊天与学习功能共用的模型客户端
- `webapp_core/config.py`：Web 三种模式的超时、阈值和功能开关
- `webapp_core/graph_service.py`：Neo4j 图谱查询服务，供前端局部子图面板使用
- `agenticRAG/`：检索运行时、回答辅助函数、短时记忆与结构化 schema
- `frontend/`：React + TypeScript 可视化前端
- `data/`：本地课程资料、题库数据和聊天持久化文件，不随仓库分发
- `storage/`：每门课程对应的 LightRAG working dir，运行时直接读取
- `scripts/`：启动、课程资料准备、索引构建、维护和验收工具
- `tests/`：当前行为边界最清晰的回归测试
- `docs/`：本地设计与使用记录，不随仓库分发

完整目录职责和工具入口见 [项目结构](PROJECT_STRUCTURE.md)。

## 运行前提

- Python `3.11`
- 可用的 OpenAI 兼容接口
- 已准备好的课程知识库 working dir，默认在 `./storage`

可选能力：

- C 编译器：`clang`、`gcc`、`cc` 或 `cl`
- Neo4j：仅用于图谱导入/可视化脚本，不是当前 Web 主运行时依赖
- Node.js：仅在开发或重新构建 React 前端时需要

## 快速启动

1. 创建并激活 Python 3.11 环境。

```bash
conda create -n py311 python=3.11 -y
conda activate py311
```

2. 安装项目依赖。

```bash
pip install -e .
```

如果还需要 Neo4j / 图可视化等工具脚本：

```bash
pip install -e ".[tools]"
```

3. 准备环境变量。

```bash
cp .env.example .env
```

至少需要补上：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`（如果你走兼容网关）
- `OPENAI_MODEL`：所有 LLM 调用共用的模型，包括问答、推理、路由、评审、摘要和资料处理；修改后重启后端生效。未配置时启动会提示错误。
- `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL`：embedding 独立使用的密钥与兼容接口地址。
- `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION`：知识库和题库共用的向量模型与维度，当前使用 `qwen3.7-text-embedding`、1536 维。请求会显式传入维度；更换模型或维度后须重建向量索引。
- `EMBEDDING_BATCH_SIZE`：每次 embedding 请求的文本条数，默认 20，适用于当前千问模型。

常用变量：

- `WEB_HOST` / `WEB_PORT`：Web 服务地址，默认 `127.0.0.1:7860`
- `WEB_STORAGE_ROOT`：课程知识库根目录，默认 `./storage`
- `WEB_CHAT_STORE_PATH`：聊天记录持久化文件，默认 `./data/web_chats.json`
- `WEB_LEARNING_STORE_PATH`：训练记录 SQLite 文件，默认 `./data/learning.sqlite3`
- `WEB_CODE_ANALYSIS_COMPILER`：指定代码分析使用的编译器
- `QUESTION_BANK_EMBED_INDEX_PATH`：题库 embedding 索引路径

4. 确认运行时数据目录可用。

默认要求存在：

```text
storage/
  C_program/
  operating_systems/
  cybersec_lab/
```

如果这些目录缺失，检索链无法正常工作。

5. 启动 Web 应用。

```bash
python webapp.py
```

或使用仓库内启动脚本：

```bash
bash scripts/run.sh
```

脚本默认使用 `/opt/anaconda3/envs/teach/bin/python`，可通过 `PYTHON_BIN` 指定其他解释器。端口被占用时会提示退出，不会终止已有进程。

启动后访问 [http://127.0.0.1:7860](http://127.0.0.1:7860)。
## React 前端

开发前端：

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

Vite 开发服务默认把 `/api/*` 代理到 Flask 的 `127.0.0.1:7860`。

构建并交给 Flask 托管：

```bash
npm --prefix frontend run build
python webapp.py
```

完成前端构建后，Flask 根路径 `/` 统一返回 React 前端。

## 训练中心

在新版侧栏进入“训练中心”。当前内置 67 道结构化练习：操作系统 FIFO、LRU、OPT、FCFS、RR、SJF、SRTF 共 35 道，银行家算法安全性检查 5 道；C 语言循环、数组指针、函数传参跟踪及返回语句订正共 12 道；网络安全实验 DH 计算、角色权限矩阵、模拟日志证据各 5 道。过程题每次提交定位首个错误；返回语句订正检查限定输入范围并显示失败用例。OS 与 DH 规则计算与聊天中的题目辅导共用求解器，训练核验无需调用模型。

作答自动保存；提示、查看解析、每次提交及复测关联写入 SQLite。学习记录按登录账号隔离，教师只能通过班级入口查看所属班级的学生记录。首次独立通过、订正后通过、新题独立复测通过分别统计；重复题、已使用提示或已查看解析的作答不计为首次独立通过。

首次访问新版页面时，在服务器本机初始化管理员；管理员创建教师账号，学生自行注册并凭加入码入班。内置 67 道训练定义和 107 条规范问答题初始为草稿，教师核查后审核、发布，学生只看到已发布训练，问答参考题也只使用已发布版本。教师可预览草稿；旧版无账号记录仅管理员可读取。训练保留开始时的题目版本及评分规则版本，不随新版本发布而更换。

“班级与题库 → 作业与测验”支持教师布置已发布训练题、截止和补交、限次测验，以及实验报告和代码材料；测验结果在结束后公布，交卷后作答和材料锁定。“班级学情”提供学生、题目和章节统计、只读证据、教师评分评语及 CSV 导出。

学生训练侧栏提供“错因确认与针对性补练”：AI 根据作答追问判断依据，结合回答给出简短对比讲解，最多三轮后回到原题订正。订正通过后，按诊断重点优先推荐同题型、尚未练习的新题，并在新题展示独立验证结果。诊断重点仅能从题库已有训练标签中选择；错因仍是假设，AI 不参与判分。没有可用新题时明确提示，辅导或订正通过不算首次独立通过。

“课程进度”和“错题复习”提供章节资料、阅读标记、知识点作答证据、到期间隔复测与下一步建议。问答与训练往返保留作答和出处；作答问答计入辅助行为，阅读与独立训练证据分开统计。

独立构造题评测：`python scripts/evaluate_learning_diagnosis.py`。结果输出到 `output/learning_evaluation/`，参考轨迹保存在 `tests/fixtures/learning_diagnosis_v1.json`，与内置训练题分开。

`python scripts/replay_learning_demo.py` 可通过训练接口自动回放模拟作答，使用临时数据库。

## Neo4j 图谱展示

Neo4j 数据需要预先导入，前端运行时只查询和展示，不负责导入或清库：

```bash
python scripts/graph_visual_with_neo4j.py --subjects C_program operating_systems cybersec_lab
```

需要在 `.env` 中配置 `NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD` 和可选的 `NEO4J_DATABASE`。未配置 Neo4j 时聊天功能仍可用，图谱面板会显示降级状态。

## 数据与索引

这个仓库里要分清三类东西：

1. 源码
   `webapp.py`、`webapp_core/`、`agenticRAG/`、`tests/`

2. 原始课程资料 / 题库
   例如 `data/subject_chapters/`、`data/tutoring_question_bank/`

3. 生成后的检索索引
   `storage/<subject>/` 下的 `vdb_*.json`、`kv_store_*.json`、`graph_chunk_entity_relation.graphml` 等文件

`storage/` 中的内容是运行时必需的，但它们更像“可重建的检索工作目录”，而不是适合放进普通 Git 历史的大型源码文件。团队协作时更推荐：

- 由脚本重建
- 单独打包分发
- 通过 Docker volume / 共享目录挂载

不建议直接把大体积 `storage/` 文件长期塞进普通 Git 历史。

## 题库 embedding 索引

`data/` 不纳入版本管理，部署前需单独准备课程资料和题库。向量索引可在题库原文就绪后生成。题目辅导模块默认读取：

- `data/tutoring_question_bank/questions.jsonl`
- `data/tutoring_question_bank/questions.embedding_index.json`

如果需要重建题库 embedding 索引：

```bash
python scripts/build_question_bank_embeddings.py
```

更换 embedding 模型或维度时，需同时重建三个学科和题库的向量索引。脚本复用现有文本块、实体与关系描述，不重新解析教材或抽取知识图谱。

先停止后端，再执行：

```bash
python scripts/rebuild_embedding_indexes.py --apply
```

脚本读取 `.env` 中的 `EMBEDDING_*` 配置，默认每批 20 条、4 个并发请求。完成全部校验后，备份旧索引并替换，随后重启后端。中断后运行同一命令会复用 `tmp/embedding_rebuild/batches/` 中已完成的向量批次。

只检查配置和条数可加 `--dry-run`；不加 `--apply` 时只生成暂存索引，不替换正式文件。

## 测试

当前测试以 `unittest` 为主，推荐直接跑：

```bash
WEB_CHAT_STORE_PATH=./tmp/test_web_chats.json python -m unittest discover -s tests
```

如果你使用的是本地 `py311` Conda 环境，也可以：

```bash
WEB_CHAT_STORE_PATH=./tmp/test_web_chats.json conda run -n py311 python -m unittest discover -s tests
```

## 当前实现边界

- 这是三门固定课程的专用学习助手，不是开放域聊天机器人
- Web 运行时当前依赖本地 `LightRAG working_dir`
- Neo4j 只承担图谱查询和可视化支撑，不替代当前 `storage/` 检索运行时
- 代码分析使用的是后端可用编译器环境，不是浏览器本地环境

## 参考文档

- [PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md)


## 个人助理与 STS 异步记忆

侧栏“个人助理”提供按账号隔离的连续对话，读取自己的课外练习进展和有权限查看的班级列表，帮助拟定目标、复习和教学安排。支持考前复习草稿、确认采用、每日资料与练习任务，以及根据真实作答证据跟进进展；不会自动发布班级任务、修改成绩或发送通知。

安装固定提交版本的 STS 依赖：

```bash
pip install -e ".[assistant]"
```

使用现有 `OPENAI_*` 和 `EMBEDDING_*` 配置，除了 Flask 后端，还须在项目根目录独立运行：

```bash
python scripts/run_assistant_worker.py
```

`--once` 处理一个可运行批次后退出，处理失败返回非零；`--store /absolute/path/learning.sqlite3` 可指定数据库，必须与后端 `WEB_LEARNING_STORE_PATH` 指向同一文件。worker 不运行时聊天仍可用，记忆显示“后台更新中”。页面只轮询状态，不在请求内启动后台工作。可用进程管理器同时维护后端与 worker。

- 记忆使用 [Scope-Time-State](https://github.com/heiyi42/Scope-Time-State/tree/c8fb75680b126f258990a9599ae7a91591f9f6ea) 的 Scope/Claim 抽取、图谱关系和 State/时间分组构建，版本固定为 `c8fb756`。用户消息、课外练习提交和已确认的复习计划调整分别作为来源事件；助手自己生成的回复不作为用户事实写入。
- 消息/练习结果与 outbox 事件在同一个 SQLite 事务中保存。worker 按账号领取批次，离线构建新的完整快照，成功后原子发布；期间前台使用上一快照和本轮上下文。失败退避重试，五次失败后可在“助理记忆”手动重试。
- 当前快照保存于学习数据库的 `assistant_profiles`，事件与队列分别在 `assistant_events`、`assistant_jobs`。STS 不使用课程 Neo4j 数据库。第一版按账号重建完整快照，适合先验证小规模使用；大量历史时仍需增加局部更新与容量管理。
- 检索采用中文字符/双字分词的 BM25 与 embedding 排名融合，再附带 STS 的 Scope、State 和时间证据。向量服务不可用时降级为关键词检索。抽取前筛除寒暄和纯知识请求，但中文记忆质量仍需真实模型评估。
- “助理记忆”支持查看来源、关闭自动记忆、按来源删除、清空。关闭后不读写长期记忆，当前聊天仍保留；删除记忆也清除助理对话，避免旧上下文带回已删除内容。同源其他记忆会一并移除，其余来源由后台重建。课程问答与业务成绩不删除，未来新的练习仍可产生新事件。
- 删除标记、账号 epoch 和 worker token 防止旧任务和重复事件恢复已删除记忆。测验/作业结果暂不接入此通道，避免绕过延迟公布规则。原始业务记录仍为成绩和进度的权威来源。
- 上游评测抽取器会打印提示和响应，独立 worker 已抑制这些输出，错误日志仅记录异常类型。真实模型接口未配置时，不能验证端到端生成质量。

针对性验证（使用隔离数据库和模型桩，不访问真实模型）：

```bash
OPENAI_MODEL=test OPENAI_API_KEY=test OPENAI_BASE_URL=http://localhost:9 \
EMBEDDING_API_KEY=test EMBEDDING_BASE_URL=http://localhost:9 \
python -m unittest tests.test_personal_assistant
```


### 考前复习任务

在个人助理的“考前复习”卡片选择课程、考试日期、章节范围和每日时间，生成草稿后点击“采用计划”。计划生成使用已发布题库、章节资料可用性和个人课外作答记录，不依赖模型调用；支持明天至90天内的考试，默认每天15—120分钟。一次保留一份正在执行的计划，采用新计划会归档原计划，保留原有作答记录。

- AI 对话可通过工具生成复习草稿、提出“今天只有20分钟/今天休息”的建议；均须用户在卡片确认，模型没有采用计划、修改成绩或发布作业的工具。范围或日期缺失时应先询问，用户也可直接使用表单。
- 每日任务可直接打开章节资料或创建/继续真实训练。资料支持分段阅读和显式标记已读；这只是阅读进度，不作为掌握证据。训练首次独立通过才完成该训练目标；订正或辅助通过后提供未做过的新题复测。
- 任务进展从真实阅读标记与课外提交读取，不提供手工勾选“独立掌握”。其他入口产生的新独立作答也可满足同类目标。此前已有最近独立通过证据的内容减少重复训练。
- 当天完成任务的预计时长计入当天预算，避免每完成一道题就继续塞入明天的任务。时长为估计，不是实际在线计时。失败/订正所需额外时间可通过当天预算调整。
- “今天休息”设为0分钟，其他日期保持原预算；未完成任务从当天起重新排入考试日前的剩余时段。时间不足、题目用尽、资料缺失和没有已发布训练会明确显示，不能把这类缺口算作掌握。考试当天停止启动计划任务。
- 日期按 `Asia/Shanghai` 计算。正式计划在 `assistant_exam_plans` 持久化，修改有版本校验。采用/调整计划与异步记忆事件同事务写入；未确认草稿不写作已确认目标。清空助理记忆不会删除正式复习计划、练习或成绩记录。

真实模型联调工具：

```bash
# 只检查配置是否存在，不打印密钥，也不调用模型
python scripts/check_assistant_live.py
# 配置好 OPENAI_* 和 EMBEDDING_* 后，使用临时数据库及合成数据调用真实服务
python scripts/check_assistant_live.py --run
```

联调检查“原来50分钟 → 这周25分钟”的记忆构建、无聊天历史的重新检索和删除后的旧任务拦截。缺少配置返回非零并显示缺失变量名，不将模拟响应当成真实验证。
