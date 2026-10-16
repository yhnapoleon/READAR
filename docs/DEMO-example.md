# 论文→实践雷达 演示报告

项目 ShopSteward · 2026-10-15 21:12

## 1. 痛点（WF-A 从项目文档抽出）

- 模块：agent/
- 描述：Agent 运行每次最多8轮模型与12次工具、90秒窗口；崩溃时可能重发同一逻辑轮次产生额外用量
- 指标：null

## 2–3. 发散检索与可行性（从库里读取，WF-B / WF-C 已在上一次运行完成）

- 命中论文 13 篇，拆出机制 54 条，过线并出卡 27 条

前 20 条机制：
- [2607.03702] 关键转折点自反馈局部重试（Pivotal Local Retry） → LangGraph 运行时 + 检查点与记忆/Skill | relevance 10 | feasibility 1 | carded
- [2606.01416] 预算感知调度与截止前降级策略 → LangGraph 运行时 | relevance 10 | feasibility 1 | carded
- [2607.11098] 匹配-回放、偏离-走实 的工具调用缓存与重放 → LangGraph 运行时 | relevance 10 | feasibility 1 | carded
- [2608.21159] 授权-到-效果闭环：预留+提交时复核 → agent/src/shopsteward_agent/ (受限工具协议) | relevance 10 | feasibility 1 | carded
- [2607.19297] 确定性工具与幂等键去重缓存 → 受限工具协议 + 检查点与记忆/Skill (agent/src/shopsteward_agent/) | relevance 10 | feasibility 1 | carded
- [2511.03094] 版本化执行日志与可恢复检查点 → 检查点与记忆/Skill | relevance 10 | feasibility 1 | carded
- [2608.01710] CapLease 事务化授权（Issue-Prepare-Commit） → 受限工具协议（agent/src/shopsteward_agent/） | relevance 10 | feasibility 1 | carded
- [2507.01628] 步级微检查点与幂等重放抑制 → 检查点与记忆/Skill | relevance 10 | feasibility 1 | carded
- [2607.20499] 子进程隔离的沙箱执行与墙钟超时 → 受限工具协议 | relevance 9 | feasibility 1 | carded
- [2607.19297] 时间窗感知的路由与暂停 → LangGraph 运行时 (agent/, agent/src/shopsteward_agent/) | relevance 9 | feasibility 1 | carded
- [2607.03702] 预算感知的局部重试调度（Budget-Aware Pivot Retry Scheduler） → LangGraph 运行时 | relevance 9 | feasibility 1 | carded
- [2603.20625] ACRFence 效应账本与幂等动作ID → 受限工具协议 + 检查点与记忆/Skill | relevance 9 | feasibility 1 | carded
- [2607.20499] 有界重试预算（全局与节点级） → agent/: LangGraph 运行时；检查点与记忆/Skill | relevance 9 | feasibility 1 | carded
- [2606.01416] 受限工具调用包装（Schema修复与超时退避） → 受限工具协议 | relevance 9 | feasibility 1 | carded
- [2608.21159] 交付栅栏与“认证无效果”查询 → agent/src/shopsteward_agent/cases/bundles/ | relevance 9 | feasibility 1 | carded
- [2606.01416] 幂等步去重与崩溃安全重放（Step-ID/哈希） → 检查点与记忆/Skill | relevance 9 | feasibility 1 | carded
- [2511.03094] 回路守卫与配额阈值（轮次/工具/时长） → LangGraph 运行时 | relevance 9 | feasibility 1 | carded
- [2511.03094] 依赖感知的局部修复（最小影响区重执行） → LangGraph 运行时 | relevance 9 | feasibility 1 | carded
- [2608.26225] LLM 调用的提示+上下文哈希幂等缓存 → 模型适配 | relevance 8 | feasibility 1 | carded
- [2603.20625] Replay-or-Fork 恢复策略 → LangGraph 运行时 + 检查点与记忆/Skill | relevance 8 | feasibility 1 | carded

## 4. 选中的实验卡

```markdown
# CARD-7696846390902652928: Agent 工具子进程沙箱与墙钟超时（最小）

来源: arXiv:2607.20499 | 代码: 无 | 可行性: 1 | 机制: 子进程隔离的沙箱执行与墙钟超时
痛点: Agent 运行每次最多8轮模型与12次工具、90秒窗口；崩溃时可能重发同一逻辑轮次产生额外用量

## 假设
把 仅对1个内置工具的子进程隔离执行+2秒墙钟超时（超时/崩溃返回ToolError而不抛出） 用在 agent/src/shopsteward_agent/ 的 工具调用适配位置，预期 ..\.venv\Scripts\python.exe -m pytest tests ../agent/tests -c pyproject.toml -q 的通过数 相对基线 不下降

## 改动范围
agent/src/shopsteward_agent/ 新增1个模块文件；agent/src/shopsteward_agent/ 工具适配处插入一次性包装；agent/README.md 增补开关说明（合计≤3处改动）

## 步骤
1. 在 backend/ 目录运行：..\.venv\Scripts\python.exe -m pytest tests ../agent/tests -c pyproject.toml -q，记录通过/失败数作为基线
2. 在 agent/src/shopsteward_agent/ 新增最小沙箱执行器模块：用 multiprocessing/subprocess 启动子进程执行目标工具；设置2秒墙钟超时；捕获stdout/stderr与返回码；超时/崩溃即杀子进程并返回结构化错误对象（不抛出）
3. 在 agent/src/shopsteward_agent/ 的工具调用适配位置，只对一个内置只读工具挂接沙箱执行器；默认保持原路径；当环境变量 AGENT_TOOL_SANDBOX=1 时启用；将超时/崩溃映射为受限工具协议的错误返回，主流程不中断
4. 在 agent/README.md 补充 AGENT_TOOL_SANDBOX 开关与超时说明，标注仅覆盖1个工具、默认关闭
5. 设置环境变量 AGENT_TOOL_SANDBOX=1，确保评测时走沙箱路径
6. 在 backend/ 目录再次运行同一命令：..\.venv\Scripts\python.exe -m pytest tests ../agent/tests -c pyproject.toml -q；将本次与基线通过数写入 radar_result.json

## 评测方法
..\.venv\Scripts\python.exe -m pytest tests ../agent/tests -c pyproject.toml -q

## 预算与停止
最多 24 分钟。出现以下情况停止并标 blocked: 需要训练 / 需要外部数据 / 评测跑不起来 / 需要改接口。
只在当前分支工作，不要合并、不要推送、不要改 .env 或 Key 文件。

## 回写
完成后在仓库根目录写 radar_result.json: {"card_id": "7696846390902652928", "verdict": "win|neutral|lose|blocked", "metric": "...", "baseline": "...", "after": "...", "notes": "..."}

```

## 5. 编码 Agent 执行结果

- verdict：**neutral**
- 指标：pytest pass count: ..\.venv\Scripts\python.exe -m pytest tests ../agent/tests -c pyproject.toml -q (run from backend/)
- 基线：514 passed, 318 skipped, 2 warnings, 82 errors
- 改后：514 passed, 318 skipped, 2 warnings, 82 errors
- 改动文件：agent/src/shopsteward_agent/sandbox.py, agent/src/shopsteward_agent/cases/frozen.py, agent/README.md
- 用时 4.3 分钟，34 轮，约 1.25 美元
- 分支：`radar/card-7696846390902652928`（aacd7aa radar: Agent 工具子进程沙箱与墙钟超时（最小））

**执行说明：**

Added opt-in subprocess sandbox (python -I, credential-free env, 2s wall clock) for the single read-only frozen-case tool read_forecast_profile, gated by AGENT_TOOL_SANDBOX=1, default off. Timeout/crash return {ok:false, error:TOOL_SANDBOX_TIMEOUT|TOOL_SANDBOX_CRASH, references:[]} and never raise; stderr/returncode kept on the ToolError object, not sent to the model. Pass count unchanged; the 82 errors are pre-existing (same set as baseline: DB-backed and knowledge_storage fixtures, plus test_seed_bundle_is_hash_bound...). CAVEAT: step 5 not done for the full-suite run. The permission layer required approval for the env-prefixed command (AGENT_TOOL_SANDBOX=1 ...), approval was not available, and it was not worked around. So the 'after' run is the card's literal command on the default (sandbox-off) path. The sandbox-on path was checked separately by calling the code directly: success returns data identical to the default path (child start ~0.15s); a crashing child maps to TOOL_SANDBOX_CRASH; a 5s sleeper is killed at 2s and maps to TOOL_SANDBOX_TIMEOUT. The only suite test that reaches this tool (agent/tests/test_case_bundle.py::test_frozen_run_is_bound_to_its_bundle_and_logs_each_evidence_read) only asserts on tool_log, so it would not detect a sandbox regression either way. To finish step 5, run from backend/: $env:AGENT_TOOL_SANDBOX='1'; ..\.venv\Scripts\python.exe -m pytest tests ../agent/tests -c pyproject.toml -q

**最终回复：**

结果是 **neutral**：改动前后通过数都是 514，没有下降。但改动后那一轮是在沙箱关闭的默认路径上跑的，因为我没能按第 5 步设置 `AGENT_TOOL_SANDBOX=1`。已提交到 `radar/card-7696846390902652928`（aacd7aa），没有 push。

| | 结果 |
|---|---|
| 基线 | 514 passed, 318 skipped, 82 errors |
| 改动后 | 514 passed, 318 skipped, 82 errors |

82 个 errors 改动前就有，前后完全相同，主要是数据库相关和 `test_knowledge_storage` 的测试。

**改了什么（3 个文件）**
- **新增 `agent/src/shopsteward_agent/sandbox.py`**：在独立子进程里跑工具，不带 API Key 等凭证环境变量，墙钟上限 2 秒。超时或崩溃时杀掉子进程，返回 `TOOL_SANDBOX_TIMEOUT` / `TOOL_SANDBOX_CRASH` 错误，不抛异常。stderr 和返回码留在调用方，不会传给模型。
- **`cases/frozen.py`**：只有只读工具 `read_forecast_profile` 接了沙箱，且只在 `AGENT_TOOL_SANDBOX=1` 时启用，默认走原路径。
- **`agent/README.md`**：补充了开关说明，注明默认关闭、只覆盖这一个工具。

**第 5 步没做到**
- **原因**：带环境变量前缀的评测命令被权限层要求人工审批，而这次没人能审批。不带前缀的原命令可以正常运行。我没有换写法绕过这个审批。
- **另外做的检查**：我写了临时脚本直接调用代码，没有重跑整套评测：
  - 正常情况下，沙箱返回的数据和默认路径完全一样，子进程启动约 0.15 秒。
  - 子进程崩溃时返回 `TOOL_SANDBOX_CRASH`。
  - 一个要睡 5 秒的子进程在 2 秒时被杀掉，返回 `TOOL_SANDBOX_TIMEOUT`。
- **测试覆盖的局限**：整套测试里只有 `test_frozen_run_is_bound_to_its_bundle_and_logs_each_evidence_read` 会用到这个工具，但它只检查调用日志。所以就算打开开关重跑，沙箱出问题时它很可能也报不出来。

要补完第 5 步，可以在 `backend/` 下运行：
```powershell
$env:AGENT_TOOL_SANDBOX='1'; ..\.venv\Scripts\python.exe -m pytest tests ../agent/tests -c pyproject.toml -q
```

以上都已写进 `radar_result.json` 的 notes。

## 6. 回写（WF-D）

经验条目已写入知识库：

> 在ShopSteward的受限工具协议移植子进程沙箱+2s超时（arXiv:2607.20499）：neutral。指标pytest通过数从514/318/2/82到同前。原因：回归未启沙箱，单测等效。局限：未跑启沙箱的整套回归。适用条件：仅单一只读工具，需AGENT_TOOL_SANDBOX=1，且2s时限可接受。


---
总用时 5.1 分钟。数据：Coze Studio 数据库 5 张表；画布见 canvases/，原始运行结果见 runs/。
