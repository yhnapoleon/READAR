"""WF-A 项目画像: docs_text -> LLM (summary, modules, pain points, eval commands) -> project + pain_point tables.

usage: python wfa_build.py <project_name> <repo_path> <stack> <docs_text_file> [--no-run]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze, save_account  # noqa: E402
import canvas as cv  # noqa: E402
from workflow_api import create_workflow, save_workflow, test_run, summarize_process  # noqa: E402

E = cv.ENTRY_ID
ROOT = Path(__file__).resolve().parent.parent
IDS = json.loads((ROOT / "radar_ids.json").read_text(encoding="utf-8"))
T = IDS["tables_online"]

PROFILE_SYSTEM = """你是资深工程师，阅读一个软件项目的文档，产出项目画像。只依据文档原文，不要臆造文件名或命令。
输出 JSON：
{"summary": "150 字以内：项目做什么、技术栈、当前状态",
 "eval_commands": ["文档里明确写出的、可直接运行的测试或评测命令，原样抄录，最多 6 条；没有就空数组"],
 "modules": [{"name": "目录或模块名（用文档里的写法）", "responsibility": "一句话", "paths": "文档提到的路径，逗号分隔"}],
 "pain_points": [{"module": "所属模块", "description": "一个具体的、工程上可改进的问题（来自文档里写明的边界、未完成项、已知问题、待验证项）",
                  "metric": "能观测它的指标或测试；文档里找不到就写 null", "priority": 1|2|3}]}
pain_points 给 5 到 8 条，优先选 Agent 运行时、工具调用、恢复、上下文、评测相关的；每条必须能在文档里找到依据。"""

PROFILE_PROMPT = """项目名：{{project_name}}
技术栈：{{stack}}

文档：
{{docs_text}}"""

CODE_FLAT = r'''
import json, datetime
async def main(args: Args) -> Output:
    p = args.params
    name = str(p.get("project_name") or "")
    mods = p.get("modules") or []
    pains = p.get("pain_points") or []
    def s(v): return str(v or "").replace("'", "’")
    summary = s(p.get("summary"))
    cmds = p.get("eval_commands") or []
    stack = s(p.get("stack"))
    mod_lines = "; ".join("%s: %s [%s]" % (m.get("name"), m.get("responsibility"), m.get("paths")) for m in mods)
    full_summary = (summary + chr(10) + "模块: " + mod_lines + chr(10) + "评测命令: " + " || ".join(cmds))[:6000]
    rows = []
    for q in pains:
        rows.append(json.dumps({"module": s(q.get("module")), "description": s(q.get("description")),
                                "metric": s(q.get("metric") if q.get("metric") not in (None, "null") else "null"),
                                "priority": int(q.get("priority") or 2)}, ensure_ascii=False))
    return {"summary": s(full_summary), "pain_rows": rows, "pain_count": len(rows), "module_count": len(mods),
            "eval_commands": json.dumps(cmds, ensure_ascii=False), "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
'''

CODE_UNPACK = r'''
import json
async def main(args: Args) -> Output:
    r = json.loads(args.params.get("row") or "{}")
    return {"module": str(r.get("module") or ""), "description": str(r.get("description") or ""),
            "metric": str(r.get("metric") or "null"), "priority": int(r.get("priority") or 2),
            "project_id": int(args.params.get("project_id") or 0)}
'''

CODE_PICK_ID = r'''
async def main(args: Args) -> Output:
    rows = args.params.get("rows") or []
    pid = 0
    for r in rows:
        try:
            pid = int(r.get("id") or 0); break
        except Exception:
            pass
    return {"project_id": pid}
'''

SQL_INSERT_PROJECT = ("insert into project (name, repo_path, stack, summary, kb_doc_ids, updated_at) values "
                      "('{{name}}', '{{repo_path}}', '{{stack}}', '{{summary}}', '{{kb_doc_ids}}', '{{updated_at}}')")
SQL_FIND_PROJECT = "select id from project where name = '{{name}}' order by id desc limit 1"
SQL_INSERT_PAIN = ("insert into pain_point (project_id, module, description, metric, priority, status) values "
                   "({{project_id}}, '{{module}}', '{{description}}', '{{metric}}', {{priority}}, 'open')")

MOD_SCHEMA = [cv.out("name"), cv.out("responsibility"), cv.out("paths")]
PAIN_SCHEMA = [cv.out("module"), cv.out("description"), cv.out("metric"), cv.out("priority", "integer")]


def build() -> str:
    llm_id, flat_id, ins_id, find_id, pick_id, b_id, b_code, b_sql = (cv.new_id() for _ in range(8))
    nodes = [cv.entry([cv.out("project_name"), cv.out("repo_path"), cv.out("stack"), cv.out("docs_text"), cv.out("kb_doc_ids")])]
    nodes.append(cv.llm(llm_id, "项目画像", PROFILE_PROMPT, PROFILE_SYSTEM,
                        params=[cv.param("project_name", cv.ref(E, "project_name")), cv.param("stack", cv.ref(E, "stack")),
                                cv.param("docs_text", cv.ref(E, "docs_text"))],
                        outputs=[cv.out("summary"), cv.out("eval_commands", "list", schema={"type": "string"}),
                                 cv.out("modules", "list", schema={"type": "object", "schema": MOD_SCHEMA}),
                                 cv.out("pain_points", "list", schema={"type": "object", "schema": PAIN_SCHEMA})],
                        max_tokens=16000, pos=(400, 0)))
    nodes.append(cv.code(flat_id, "拆平",
                         [cv.param("project_name", cv.ref(E, "project_name")), cv.param("stack", cv.ref(E, "stack")),
                          cv.param("summary", cv.ref(llm_id, "summary")),
                          cv.param("eval_commands", cv.ref(llm_id, "eval_commands", "list<string>")),
                          cv.param("modules", cv.ref(llm_id, "modules", "list<object>", schema={"type": "object", "schema": MOD_SCHEMA})),
                          cv.param("pain_points", cv.ref(llm_id, "pain_points", "list<object>", schema={"type": "object", "schema": PAIN_SCHEMA}))],
                         [cv.out("summary"), cv.out("pain_rows", "list", schema={"type": "string"}), cv.out("pain_count", "integer"),
                          cv.out("module_count", "integer"), cv.out("eval_commands"), cv.out("updated_at")], CODE_FLAT, pos=(800, 0)))
    nodes.append(cv.custom_sql(ins_id, "写入 project", T["project"], SQL_INSERT_PROJECT,
                               [cv.param("name", cv.ref(E, "project_name")), cv.param("repo_path", cv.ref(E, "repo_path")),
                                cv.param("stack", cv.ref(E, "stack")), cv.param("summary", cv.ref(flat_id, "summary")),
                                cv.param("kb_doc_ids", cv.ref(E, "kb_doc_ids")), cv.param("updated_at", cv.ref(flat_id, "updated_at"))], [], pos=(1200, 0)))
    nodes.append(cv.custom_sql(find_id, "查 project id", T["project"], SQL_FIND_PROJECT, [cv.param("name", cv.ref(E, "project_name"))],
                               [cv.out("id", "integer")], pos=(1600, 0)))
    nodes.append(cv.code(pick_id, "取 id", [cv.param("rows", cv.ref(find_id, "outputList", "list<object>", schema={"type": "object", "schema": [cv.out("id", "integer")]}))],
                         [cv.out("project_id", "integer")], CODE_PICK_ID, pos=(2000, 0)))
    inner = [cv.code(b_code, "解包痛点", [cv.param("row", cv.ref(b_id, "items")), cv.param("project_id", cv.ref(pick_id, "project_id", "integer"))],
                     [cv.out("module"), cv.out("description"), cv.out("metric"), cv.out("priority", "integer"), cv.out("project_id", "integer")], CODE_UNPACK, pos=(200, 100)),
             cv.custom_sql(b_sql, "写入 pain_point", T["pain_point"], SQL_INSERT_PAIN,
                           [cv.param("project_id", cv.ref(b_code, "project_id", "integer")), cv.param("module", cv.ref(b_code, "module")),
                            cv.param("description", cv.ref(b_code, "description")), cv.param("metric", cv.ref(b_code, "metric")),
                            cv.param("priority", cv.ref(b_code, "priority", "integer"))], [], pos=(600, 100))]
    nodes.append(cv.batch(b_id, "写入痛点", cv.ref(flat_id, "pain_rows", "list<string>"), inner, cv.batch_edges(b_id, [b_code, b_sql]),
                          outputs=[{"name": "rows", "input": cv.ref(b_sql, "rowNum", "list<string>")}], batch_size=50, concurrent=3, pos=(2400, 0)))
    nodes.append(cv.exit_node([cv.param("project_id", cv.ref(pick_id, "project_id", "integer")), cv.param("summary", cv.ref(flat_id, "summary")),
                               cv.param("pain_count", cv.ref(flat_id, "pain_count", "integer")), cv.param("eval_commands", cv.ref(flat_id, "eval_commands")),
                               cv.param("pain_rows", cv.ref(flat_id, "pain_rows", "list<string>"))], pos=(2800, 0)))
    edges = [cv.edge(E, llm_id), cv.edge(llm_id, flat_id), cv.edge(flat_id, ins_id), cv.edge(ins_id, find_id), cv.edge(find_id, pick_id),
             cv.edge(pick_id, b_id), cv.edge(b_id, cv.EXIT_ID, "batch-output")]
    return cv.canvas(nodes, edges)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    name, repo, stack, docs_file = args[0], args[1], args[2], args[3]
    c = Coze()
    c.login()
    wid = c.acct.get("wf_a_id") or create_workflow(c, "radar_wf_a_profile", "WF-A 项目画像：文档→模块与痛点")
    c.acct["wf_a_id"] = wid
    save_account(c.acct)
    schema = build()
    (ROOT / "canvases").mkdir(exist_ok=True)
    (ROOT / "canvases" / "wf_a.json").write_text(schema, encoding="utf-8")
    r = save_workflow(c, wid, schema)
    print("workflow_id", wid, "save ->", r.get("code"), r.get("msg"))
    if "--no-run" in sys.argv:
        return
    docs_text = Path(docs_file).read_text(encoding="utf-8")[:60000]
    kb_docs = ",".join(IDS.get("projects", {}).get(name, {}).get("kb_docs", {}).values())
    d = test_run(c, wid, {"project_name": name, "repo_path": repo, "stack": stack, "docs_text": docs_text, "kb_doc_ids": kb_docs},
                 bot_id=c.acct.get("bot_id"), timeout_s=900)
    print(summarize_process(d))
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "wf_a_last.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
