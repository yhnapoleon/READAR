# 论文→实践雷达：构建方案

> 2026-10-15 初稿。目标读者是我们自己，不是导师。导师看的内容后面从这里提炼进 stage1 第 7、8 节。

## 0. 一句话

给定一个正在做的项目，雷达去找"相关论文里的具体机制"，判断哪些能以半天内的改动移植进项目，生成可执行、可测量的实验卡，交给编码 Agent 试，结果回写，下一轮据此调整。

**边界：** Coze Studio 只负责"找、判、出卡、记结果"；执行留在本机脚本和项目仓库里。

## 1. 总体结构

```
项目仓库 (README / docs / 架构说明 / 已知问题)
        │  bridge/push_project.py
        ▼
┌─ Coze Studio (localhost:8888) ──────────────────────────────────┐
│  知识库 KB-project   项目文档、分类框架(stage1 §1-2)、经验条目      │
│  数据库 5 张表       project / pain_point / paper / mechanism /    │
│                      experiment_card                             │
│  工作流                                                          │
│    WF-A 项目画像     文档 → 模块清单 + 痛点清单                     │
│    WF-B 发散检索     痛点 → 多 query → arXiv → 引用扩展 → 机制判定   │
│    WF-C 可行性+出卡  机制 → 多准则可行性 → 实验卡 → 飞书推送          │
│    WF-D 结果回写     执行结果 → 更新卡状态 → 写经验条目               │
│  智能体 Radar        意图路由 WF-B/C/D + 库内问答(NL2SQL + 检索)     │
└──────────────────────────────────────────────────────────────────┘
        │  bridge/pull_cards.py        ▲ bridge/report_result.py
        ▼                              │
  cards/<id>.md ──► bridge/run_card.py ─► 编码 Agent 在项目 worktree 里跑
                                         产出 result.json + diff
        │
        └──► 飞书自定义机器人 webhook（日报 / 新卡通知）
```

**外部定时：** 开源版没有调度器。Windows 任务计划每周触发 `bridge/weekly.py`，依次调 OpenAPI 跑 WF-B、WF-C。

## 2. 数据模型（Coze 数据库）

字段类型只用 String / Integer / Number / Boolean / Time。`待核：` 开源版单表字段上限，建表时如果超了就把 JSON 塞进一个 String 字段。

| 表 | 字段 | 说明 |
|---|---|---|
| `project` | id, name, repo_path, stack, summary, kb_doc_ids, updated_at | 一行一个项目 |
| `pain_point` | id, project_id, module, description, metric, priority(1-3), status(open/closed) | WF-A 产出，用户可改 |
| `paper` | arxiv_id, title, authors, published, abstract, url, code_url, cited_by, source(search/expand/manual), first_seen | 去重键 arxiv_id |
| `mechanism` | id, paper_id, name, description, target_module, pain_point_id, relevance(0-10), relevance_reason, feasibility_json, feasibility_score(0-1), status(candidate/carded/rejected/tried) | 一篇论文可拆出多条机制 |
| `experiment_card` | id, mechanism_id, title, hypothesis, change_scope, steps, eval_method, budget_min, status(new/running/done/failed), result_json, verdict(win/neutral/lose/blocked), note, created_at, finished_at | 交给编码 Agent 的单元 |

## 3. 知识库

一个知识库 `KB-radar`，三类文档，用元数据或标题前缀区分：

1. **项目文档**：README、架构说明、模块说明、已知问题。由 `push_project.py` 上传，项目变动时重传。
2. **分类框架**：stage1 第 1、2 节（闭环环节、单/多 Agent、中心化/去中心化），给判定节点当参考。
3. **经验条目**：WF-D 每次回写一条"机制 X 移植到模块 P：结果、原因"，下一轮 WF-B 判定时检索到，避免重复、学会哪类机制搬得动。

分段策略用按段落、500 字左右，重叠 50。

## 4. 工作流详细设计

节点名用 Coze Studio 的类型：Entry / Exit / LLM / HTTPRequester / CodeRunner / Selector(条件) / Batch / Loop / Database* / Knowledge* / IntentDetector / SubWorkflow。

**已确认的平台限制：**
- 代码节点跑在 Deno + Pyodide 沙箱，默认不允许网络 → 所有外部请求走 HTTP 节点，代码节点只做解析和计算。Python 标准库（`xml.etree`、`json`、`re`）可用。
- arXiv 限 3 秒 1 次 → 一次 WF-B 只发 1 到 2 个 arXiv 请求（多个 query 用 OR 合并），引用扩展走 OpenAlex。
- 没有定时器 → 外部触发。

### WF-A 项目画像

| 步 | 节点 | 输入 → 输出 |
|---|---|---|
| 1 | Entry | project_id, docs_text（bridge 拼好的文档全文，上限 3 万字） |
| 2 | LLM（结构化输出） | 抽 `modules[]{name, responsibility, files}`、`pain_points[]{module, description, metric, priority}`。prompt 要求"每个痛点必须写出可观测的指标，写不出的标 metric=null" |
| 3 | Batch → DatabaseInsert | 写 pain_point |
| 4 | DatabaseUpdate | 更新 project.summary |
| 5 | Exit | 返回清单供用户确认 |

用户在界面或对话里改 priority、删掉不要的痛点。**痛点清单是整条链路的锚，一定人工过一遍。**

### WF-B 发散检索（核心）

| 步 | 节点 | 做什么 | 借自 |
|---|---|---|---|
| 1 | Entry | pain_point_id（或自由文本 query） | |
| 2 | DatabaseQuery | 取痛点、所属模块、项目 summary | |
| 3 | KnowledgeRetriever | 检索经验条目 + 项目文档，top 5，拼进上下文 | 自己的 |
| 4 | LLM（结构化输出） | 痛点 → 3-5 条检索 query，每条带 intent、年份约束、排除词；合成一个 arXiv `search_query`（`abs:` 前缀 + OR） | Paper Finder、SPAR query evolver |
| 5 | HTTPRequester | `export.arxiv.org/api/query?search_query=...&max_results=50&sortBy=relevance` | |
| 6 | CodeRunner | 解析 Atom XML → `papers[]{arxiv_id,title,abstract,published,authors,pdf}`；去掉 v 号 | |
| 7 | DatabaseQuery + CodeRunner | 查已有 paper，过滤已 tried / rejected 的 mechanism 对应论文 | |
| 8 | Batch(并发 5) → LLM | **Selector 判定**：输入 痛点 + 模块职责 + 论文标题摘要。输出 `decision(true/false)` 放最前，然后 `mechanisms[]{name, description, target_module, how_to_apply, relevance 0-10, reason}`。prompt 明确："判断的不是论文是否相关，是论文里有没有一个可以单独移植的机制" | PaSa Selector（先决策再理由） |
| 9 | Selector | relevance ≥ 7 的进入扩展 | |
| 10 | Batch(最多 5 篇) → HTTPRequester | OpenAlex 单条查询 `api.openalex.org/works/https://doi.org/10.48550/arXiv.<id>`（免费不限），取 `referenced_works[]`、`cited_by_api_url` | PaSa Expand、PaperQA2 引用遍历 |
| 11 | HTTPRequester | `api.openalex.org/works?filter=openalex_id:W1\|W2\|...&per-page=50&select=id,title,publication_date,locations,abstract_inverted_index,cited_by_count` | |
| 12 | CodeRunner | 还原 abstract，筛出有 arxiv 链接的，去重，最多 30 篇 | |
| 13 | Batch → LLM | 同第 8 步的 Selector，**不再扩展**（只扩一层） | PaperQA2 限 1 层 |
| 14 | Batch → DatabaseInsert | 写 paper、mechanism（status=candidate） | |
| 15 | Exit | 返回机制清单（按 relevance 排序）和 token 用量 | |

**成本估算：** 50 + 30 篇 × 一次结构化判定 ≈ 80 次 LLM 调用，每次约 1.5k token 输入；gpt-5 下一轮几美元以内。

### WF-C 可行性与实验卡

| 步 | 节点 | 做什么 | 借自 |
|---|---|---|---|
| 1 | Entry | mechanism_ids[]（默认取全部 candidate 且 relevance ≥ 7） | |
| 2 | Batch → HTTPRequester | `huggingface.co/api/papers/<arxiv_id>` 取 githubRepo / githubStars；没有再搜 `api.github.com/search/repositories?q=<arxiv_id>+in:readme`（10 次/分，只在前者为空时调） | cv-arxiv-daily 代码回查 |
| 3 | HTTPRequester | `arxiv.org/html/<id>`，失败回退 `ar5iv.labs.arxiv.org/html/<id>` | |
| 4 | CodeRunner | 去标签，只留 Introduction、Method、Implementation/Experimental Setup 段，截 2 万字 | |
| 5 | LLM（结构化输出） | **多准则可行性**，每项给 `value` + `evidence`（原文句子）：`needs_training`、`needs_external_data`、`framework_dependency`、`has_reference_impl`、`change_scope(S/M/L)`、`eval_feasible_in_project`、`single_mechanism_transplantable` | Elicit 多准则列、AI-Researcher 资源过滤 |
| 6 | CodeRunner | 加权合成 feasibility_score，权重写在代码里便于调；硬否决：needs_training=true 或 change_scope=L → rejected | |
| 7 | DatabaseUpdate | 写 feasibility_json、score、status | |
| 8 | Selector | score ≥ 0.6 进入出卡 | |
| 9 | LLM（结构化输出） | 生成实验卡（第 5 节模板的 JSON 形式）。要求：假设必须含指标名和预期方向；steps 每步可独立验证；eval_method 必须引用项目里已有的测试或数据，没有就写"前置：需先建评测集"并把 status 设为 blocked | |
| 10 | DatabaseInsert | 写 experiment_card | |
| 11 | HTTPRequester | 飞书 webhook，`msg_type=post`，列出新卡标题、机制、来源论文、score | |
| 12 | Exit | | |

### WF-D 结果回写

| 步 | 节点 | 做什么 |
|---|---|---|
| 1 | Entry | card_id, verdict, result_json, note |
| 2 | DatabaseUpdate | experiment_card 状态、结果；mechanism.status=tried |
| 3 | LLM | 把结果压成一条经验："在 <项目> 的 <模块> 移植 <机制>：<verdict>，原因 <一句>，适用条件 <一句>" |
| 4 | KnowledgeIndexer | 写进 KB-radar 经验条目 |
| 5 | Exit | |

### 智能体 Radar

- 人设：研究工程助手，只回答库里有依据的内容，引用 arxiv_id。
- IntentDetector 四路：发散（WF-B）/ 出卡（WF-C）/ 回写（WF-D）/ 问答。
- 问答路：KnowledgeRetriever + DatabaseQuery(NL2SQL)，回答"去中心化相关的候选机制有几条""上次试 X 为什么失败"。
- 绑定：KB-radar、5 张表、4 条工作流。

## 5. 实验卡模板

```markdown
# CARD-<id>: <机制名> → <项目模块>

来源: arXiv:<id> <标题> | 代码: <url 或 无> | 可行性: <score>
痛点: <pain_point.description>（指标: <metric>）

## 假设
把 <机制> 用在 <模块> 的 <位置>，预期 <指标> 从 <基线> 变为 <方向/幅度>。

## 改动范围
涉及文件/模块: ...  预计新增/修改 <N> 处，不改接口。

## 步骤
1. 建分支 radar/card-<id>，跑一次基线评测，记录 <指标>。
2. 实现 ...（引用论文第 x 节 / 参考实现的 y 文件）
3. 跑同一评测，记录。
4. 产出 result.json: {metric, baseline, after, runs, notes}

## 评测方法
数据: <项目里已有的测试集/样例>  指标: <...>  重复: 3 次取均值

## 预算与停止
最多 <budget_min> 分钟。出现以下情况停止并标 blocked: 需要训练 / 需要外部数据 / 评测跑不起来。

## 回写
调用 bridge/report_result.py --card <id> --verdict win|neutral|lose|blocked --result result.json
```

## 6. Bridge 脚本（本机，`coze-builds/paper-radar/bridge/`）

| 脚本 | 做什么 | 调用的 Coze 接口 |
|---|---|---|
| `coze_client.py` | 登录拿 session_key；封装内部接口和 OpenAPI（PAT）；所有凭据从 `local-account.txt` 读 | `/api/passport/web/email/login/`、`/v1/workflow/run` |
| `push_project.py <repo>` | 收集 README、docs/*.md、架构文件 → 上传知识库 → 调 WF-A | `/api/knowledge/document/create`、WF-A |
| `pull_cards.py` | 查 status=new 的卡 → 写 `cards/CARD-<id>.md` | WF 查询或 `/api/memory/database/...` |
| `run_card.py <id>` | 在项目仓库建 worktree，调用编码 Agent CLI，加超时；收集 result.json、diff、日志 | 无 |
| `report_result.py` | 调 WF-D | WF-D |
| `weekly.py` | 对所有 open 痛点跑 WF-B，再跑 WF-C；发飞书汇总 | WF-B、WF-C |

`run_card.py` 的护栏：固定预算、只允许在 worktree 内改、评测跑不起来直接 blocked、不自动合并。

## 7. 判定节点的 prompt 要点

- **Selector（WF-B 第 8、13 步）**：决策 token 放最前；要求 how_to_apply 写到"改哪个模块的哪一步"；给反例说明什么不算机制（"提出了一个 benchmark"不算，"用 X 替代 Y 的调度策略"算）。附分类框架：每条机制顺手标闭环环节 / 单多 / 中心化，第 8 节要用。
- **可行性（WF-C 第 5 步）**：每项必须给原文证据句；没证据写 unknown，不猜。
- **出卡（WF-C 第 9 步）**：输入只给机制、论文方法段、项目模块说明；输出严格按模板；评测方法只能引用项目里存在的东西。

## 8. 搭建顺序和每步的验收

| 里程碑 | 做什么 | 验收 |
|---|---|---|
| M0 环境 | 脚本填 Key、重建 server；注册测试账号；拿 space_id、PAT | `docker logs coze-server` 有 "sync old model ... to db success"；界面建一个空智能体能对话 |
| M1 存储 | 建 5 张表、KB-radar；导入 stage1 §1-2 | 界面能看到表和知识库文档已向量化 |
| M2 WF 骨架 | 用"开始 → LLM → 结束"跑通 create / save / test_run / publish 四个接口 | test_run 返回 LLM 输出 |
| M3 WF-B | 按第 4 节逐节点加，每加一个节点 test_run 一次 | 对一个痛点返回 ≥ 5 条机制，relevance 分布合理，人工抽 10 条看判定是否靠谱 |
| M4 WF-C | 同上 | 至少出 2 张卡，卡里的评测方法指向项目里真实存在的文件 |
| M5 WF-A、WF-D、智能体 | | 对话里问"候选机制有哪些"能查库回答 |
| M6 Bridge | 五个脚本 | `weekly.py` 一次跑通，飞书群收到汇总 |
| M7 首次端到端 | 选一个项目，跑 WF-A → B → C → 编码 Agent 执行 1 张卡 → WF-D | 数据库里有一条 tried 记录，知识库里有一条经验 |

M0 到 M2 一个工作日内；M3 是最费时间的，画布 JSON 的字段细节靠 `backend/domain/workflow/internal/canvas/examples/` 的示例对照。

## 9. 风险与待核

| 项 | 处理 |
|---|---|
| 目标项目没有评测集 | 出卡时标 blocked；第一个项目先建最小评测集，哪怕 20 条样例 |
| 画布 JSON 字段多、无文档 | 先最简骨架再加节点；必要时在界面上拖一次，对照前端发出的 save 请求 |
| LLM 判定"机制可移植"过于乐观 | WF-C 硬否决项 + 人工在出卡前过一遍 candidate 列表 |
| OpenAlex 无 Key 每天约 1000 次列表查询 | 单条查询免费，列表查询每轮 ≤ 10 次，够用；要多就申请免费 Key |
| Semantic Scholar 匿名 429 | v1 不依赖；要 TL;DR 和正负样本推荐再申请 |
| arXiv HTML 转换缺失 | 回退 ar5iv，再回退只用摘要并在可行性里标 unknown |
| 开源版单表字段上限 | 待核；超了把 JSON 塞 String |
| 执行成本 | 每卡预算上限，默认 60 分钟；一周最多 3 张 |

## 10. 待你决定

1. **第一个目标项目。** 你说会议 Agent 不合适。候选：你手头任何有代码、能跑测试的项目；或者先用一个你熟悉的开源 Agent 框架做演练（好处是有测试集，坏处是改动不归你）。
2. **实验卡预算**：默认一卡一个机制、60 分钟。
3. **推送群**：需要你在某个飞书群里加一个自定义机器人，把 webhook URL 给我（放 `local-account.txt`，不进聊天）。
4. **S2 Key** 先不申请，v1 走 OpenAlex。同意就不动。

## 11. 实施记录（2026-10-15）

与第 4 节设计的差异，均已验证：

- **WF-C 不抓全文。** 代码节点把输入当命令行参数传给沙箱，arXiv HTML 约 800KB 会报 "argument list too long"。v1 可行性只用 paper 表里的标题摘要加 HF 的代码链接；全文留到 v2 走知识库切片 + 检索节点。
- **机制 id 不让 LLM 回填。** 19 位整数会丢精度，导致多条评估映射到同一机制。WF-C 给机制局部序号 idx，代码节点用 id_map 映射回真实 id。
- **出卡的评测命令由 WF-A 提供。** 知识库检索会把 UI 回归脚本排到 agent 测试命令前面；现在 WF-C 的 Entry 多一个 eval_commands，卡只能从里面选。
- **卡的范围写死：** ≤3 文件、≤6 步、预算 ≤45 分钟、第 1 步和最后一步固定为跑同一条命令。
- **WF-B 去重按论文：** 论文已在库里就连机制一起跳过，避免重复入库。
- **WF-D 的经验条目由 bridge 写知识库**，知识库写入节点只收文件，不收文本。
- **数据库有两套 id：** 工作流节点用上线 id，bind_to_bot / list_records 用草稿 id；带数据库节点的 test_run 必须传 bot_id。
- **gpt-5 的 maxTokens 要 8192 起**，推理 token 算在内；每篇论文判定约 2k 输出 token，WF-B 25 篇一轮约 7.7 万 token。
- **WF-A 从文档抽出的痛点偏运维**（worker 不自动拉起、启动器没 stop），研究味的痛点要人工补。第一张卡用的痛点是手写的。
- **第一张卡的指标是"Agent 单元测试通过数 +1"**，验证的是"机制能否无损接入"，不是有效性；有效性要靠 simulation/ 做故障注入，v2 再做。

脚本清单：`bridge/` 下 coze_client、canvas、workflow_api、m1_storage、m5_agent、push_project、wfa_build、wfb_build、wfc_build、wfd_build、sql_admin、pull_cards、run_card、report_result。
