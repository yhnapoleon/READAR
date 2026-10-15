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
