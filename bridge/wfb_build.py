"""WF-B 发散检索: pain point -> queries -> arXiv -> parse -> batch mechanism selector -> summary.

Step 1 (this file): no database nodes yet, so it can test_run without a bound agent.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze, save_account  # noqa: E402
import canvas as cv  # noqa: E402
from workflow_api import create_workflow, save_workflow, test_run, summarize_process  # noqa: E402

E = cv.ENTRY_ID

QUERY_SYSTEM = """你是科研检索助手。任务：把一个工程项目的痛点改写成 arXiv 检索式。
规则：
1. 先判断痛点背后的研究问题是什么（意图：方法改进 / 评测 / 架构 / 数据），再给 3 到 5 条英文检索 query，每条 2 到 6 个词，覆盖不同措辞（同义词、上位词、相邻子领域）。
2. 把这些 query 合成一条 arXiv API 的 search_query：只用 abs: 前缀和 OR/AND，括号分组，总长度不超过 300 字符。结构固定为两组 AND：第一组是"对象"（3 到 6 个 OR 项），第二组是"问题"（3 到 6 个 OR 项）。每个 OR 项是 1 到 2 个词，两个词时用英文双引号包起来；不要用 3 个词以上的短语，不要用连字符复合词。例如 (abs:"tool calling" OR abs:"function calling" OR abs:agent OR abs:agentic) AND (abs:recovery OR abs:"error handling" OR abs:failure OR abs:robustness OR abs:"self-healing")。
3. 不要加日期、不要加 cat: 过滤。
只输出 JSON，字段：queries(list of string), arxiv_query(string), research_question(string)。"""

QUERY_PROMPT = """项目概述：{{project_summary}}
相关模块：{{module_text}}
痛点：{{pain_text}}"""

SELECT_SYSTEM = """你是严谨的研究工程师，判断一篇论文里有没有可以单独移植到给定项目模块的"机制"。
机制 = 一个可以独立实现的技术做法（例如：一种调度策略、一种重试/恢复逻辑、一种上下文压缩方法、一种评测指标）。
不算机制：提出 benchmark 但没有方法；需要重新训练模型；整篇只有分析和讨论。
输出 JSON（字段顺序固定，decision 必须在最前面）：
{"decision": true/false,
 "arxiv_id": "<原样回填>",
 "relevance": 0-10 的整数（对这个痛点的帮助程度；没有机制时为 0）,
 "reason": "一句话，先说判断再说依据",
 "mechanisms": [ {"name": "...", "description": "两句话说清做法", "target_module": "项目里的哪个模块",
                  "how_to_apply": "改哪一步、怎么改，一到两句", "relevance": 0-10,
                  "loop_stage": "perceive/plan/act/reflect 之一", "agent_type": "single/multi/na",
                  "coordination": "centralized/decentralized/na"} ] }
decision 为 false 时 mechanisms 为空数组。"""

SELECT_PROMPT = """项目模块：{{module_text}}
痛点：{{pain_text}}

论文：
{{item}}"""

CODE_BUILD_URL = r'''
import urllib.parse
async def main(args: Args) -> Output:
    p = args.params
    q = (p.get("arxiv_query") or "").strip().replace("\n", " ")
    n = int(p.get("max_results") or 40)
    url = ("http://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q, safe="()") +
           "&sortBy=relevance&sortOrder=descending&max_results=" + str(n))
    return {"url": url, "query": q}
'''

CODE_PARSE_ATOM = r'''
import re
import xml.etree.ElementTree as ET
NS = {"a": "http://www.w3.org/2005/Atom"}
async def main(args: Args) -> Output:
    body = args.params.get("body") or ""
    texts, ids, n = [], [], 0
    try:
        root = ET.fromstring(body)
    except Exception as e:
        return {"paper_texts": [], "arxiv_ids": [], "count": 0, "error": "xml parse failed: " + str(e)[:200]}
    for e in root.findall("a:entry", NS):
        raw = (e.findtext("a:id", default="", namespaces=NS) or "").rsplit("/", 1)[-1]
        aid = re.sub(r"v\d+$", "", raw)
        title = re.sub(r"\s+", " ", e.findtext("a:title", default="", namespaces=NS) or "").strip()
        abstract = re.sub(r"\s+", " ", e.findtext("a:summary", default="", namespaces=NS) or "").strip()
        published = (e.findtext("a:published", default="", namespaces=NS) or "")[:10]
        authors = [a.findtext("a:name", default="", namespaces=NS) for a in e.findall("a:author", NS)]
        if not aid or not title:
            continue
        n += 1
        ids.append(aid)
        texts.append("arxiv_id: " + aid + "\npublished: " + published + "\nauthors: " + ", ".join(authors[:5]) +
                     "\ntitle: " + title + "\nabstract: " + abstract[:1800])
    return {"paper_texts": texts, "arxiv_ids": ids, "count": n, "error": ""}
'''

CODE_PACK = r'''
import json
async def main(args: Args) -> Output:
    p = args.params
    rec = {"decision": bool(p.get("decision")), "arxiv_id": p.get("arxiv_id"), "relevance": p.get("relevance"),
           "reason": p.get("reason"), "mechanisms": p.get("mechanisms") or []}
    return {"result_json": json.dumps(rec, ensure_ascii=False)}
'''

CODE_SUMMARY = r'''
import json
async def main(args: Args) -> Output:
    items = args.params.get("result_json_list") or []
    threshold = int(args.params.get("threshold") or 7)
    kept, seen = [], 0
    for s in items:
        try:
            r = json.loads(s)
        except Exception:
            continue
        seen += 1
        if not r.get("decision"):
            continue
        mechs = [m for m in (r.get("mechanisms") or []) if int(m.get("relevance") or 0) >= threshold]
        if not mechs:
            continue
        r["mechanisms"] = mechs
        kept.append(r)
    kept.sort(key=lambda r: -max(int(m.get("relevance") or 0) for m in r["mechanisms"]))
    lines = ["judged: %d, kept: %d (threshold %d)" % (seen, len(kept), threshold), ""]
    for r in kept:
        for m in r["mechanisms"]:
            lines.append("- [%s] %s -> %s (rel %s): %s" % (r["arxiv_id"], m.get("name"), m.get("target_module"),
                                                            m.get("relevance"), m.get("how_to_apply")))
    return {"kept_json": [json.dumps(r, ensure_ascii=False) for r in kept], "kept_count": len(kept),
            "judged": seen, "summary_md": "\n".join(lines)}
'''

CODE_FLATTEN = r'''
import json, datetime
async def main(args: Args) -> Output:
    p = args.params
    existing = set()
    for row in (p.get("existing") or []):
        if isinstance(row, dict) and row.get("arxiv_id"):
            existing.add(row["arxiv_id"])
    pid = str(p.get("pain_point_id") or "0")
    today = datetime.date.today().isoformat()
    paper_rows, mech_rows = [], []
    for s in (p.get("kept_json") or []):
        try:
            r = json.loads(s)
        except Exception:
            continue
        aid = r.get("arxiv_id") or ""
        meta = {}
        for t in (p.get("paper_texts") or []):
            if t.startswith("arxiv_id: " + aid + "\n"):
                for line in t.split("\n", 4):
                    if ": " in line:
                        k, v = line.split(": ", 1)
                        meta[k] = v
                abstract = t.split("abstract: ", 1)[1] if "abstract: " in t else ""
                meta["abstract"] = abstract
                break
        if not aid or aid in existing:
            continue  # paper already processed in an earlier run: skip its mechanisms too
        existing.add(aid)
        if True:
            paper_rows.append(json.dumps({"arxiv_id": aid, "title": meta.get("title", ""), "authors": meta.get("authors", ""),
                                          "published": meta.get("published", ""), "abstract": meta.get("abstract", "")[:3000],
                                          "url": "https://arxiv.org/abs/" + aid, "source": "search", "first_seen": today}, ensure_ascii=False))
        for m in r.get("mechanisms") or []:
            tags = json.dumps({"loop_stage": m.get("loop_stage"), "agent_type": m.get("agent_type"), "coordination": m.get("coordination")}, ensure_ascii=False)
            mech_rows.append(json.dumps({"arxiv_id": aid, "name": m.get("name", "")[:120], "description": m.get("description", ""),
                                         "target_module": m.get("target_module", ""), "pain_point_id": pid,
                                         "how_to_apply": m.get("how_to_apply", ""), "relevance": int(m.get("relevance") or 0),
                                         "relevance_reason": r.get("reason", ""), "tags": tags, "status": "candidate"}, ensure_ascii=False))
    return {"paper_rows": paper_rows, "mech_rows": mech_rows, "new_papers": len(paper_rows), "new_mechs": len(mech_rows)}
'''

CODE_UNPACK_PAPER = r'''
import json
async def main(args: Args) -> Output:
    r = json.loads(args.params.get("row") or "{}")
    def s(k): return str(r.get(k) or "").replace("'", "’")
    return {"arxiv_id": s("arxiv_id"), "title": s("title"), "authors": s("authors"), "published": s("published"),
            "abstract": s("abstract"), "url": s("url"), "source": s("source"), "first_seen": s("first_seen")}
'''

CODE_UNPACK_MECH = r'''
import json
async def main(args: Args) -> Output:
    r = json.loads(args.params.get("row") or "{}")
    def s(k): return str(r.get(k) or "").replace("'", "’")
    return {"arxiv_id": s("arxiv_id"), "name": s("name"), "description": s("description"), "target_module": s("target_module"),
            "pain_point_id": int(r.get("pain_point_id") or 0), "how_to_apply": s("how_to_apply"), "relevance": int(r.get("relevance") or 0),
            "relevance_reason": s("relevance_reason"), "tags": s("tags"), "status": s("status")}
'''

SQL_SELECT_EXISTING = "select arxiv_id from paper"
SQL_INSERT_PAPER = ("insert into paper (arxiv_id, title, authors, published, abstract, url, source, first_seen) values "
                    "('{{arxiv_id}}', '{{title}}', '{{authors}}', '{{published}}', '{{abstract}}', '{{url}}', '{{source}}', '{{first_seen}}')")
SQL_INSERT_MECH = ("insert into mechanism (arxiv_id, name, description, target_module, pain_point_id, how_to_apply, relevance, relevance_reason, tags, status) values "
                   "('{{arxiv_id}}', '{{name}}', '{{description}}', '{{target_module}}', {{pain_point_id}}, '{{how_to_apply}}', {{relevance}}, '{{relevance_reason}}', '{{tags}}', '{{status}}')")

MECH_SCHEMA = [cv.out("name"), cv.out("description"), cv.out("target_module"), cv.out("how_to_apply"),
               cv.out("relevance", "integer"), cv.out("loop_stage"), cv.out("agent_type"), cv.out("coordination")]

IDS = json.loads((Path(__file__).resolve().parent.parent / "radar_ids.json").read_text(encoding="utf-8"))


def build() -> str:
    q_id, url_id, http_id, parse_id, batch_id, sel_id, pack_id, sum_id = (cv.new_id() for _ in range(8))
    sql_sel_id, flat_id, bp_id, bp_code, bp_sql, bm_id, bm_code, bm_sql = (cv.new_id() for _ in range(8))

    nodes = [
        cv.entry([cv.out("pain_text"), cv.out("module_text"), cv.out("project_summary"), cv.out("max_results"), cv.out("threshold"),
                  cv.out("pain_point_id")]),
        cv.llm(q_id, "生成检索式", QUERY_PROMPT, QUERY_SYSTEM,
               params=[cv.param("pain_text", cv.ref(E, "pain_text")), cv.param("module_text", cv.ref(E, "module_text")),
                       cv.param("project_summary", cv.ref(E, "project_summary"))],
               outputs=[cv.out("queries", "list", schema={"type": "string"}), cv.out("arxiv_query"), cv.out("research_question")],
               max_tokens=8192, pos=(400, 0)),
        cv.code(url_id, "构造 arXiv URL",
                [cv.param("arxiv_query", cv.ref(q_id, "arxiv_query")), cv.param("max_results", cv.ref(E, "max_results"))],
                [cv.out("url"), cv.out("query")], CODE_BUILD_URL, pos=(800, 0)),
        cv.http_get(http_id, "arXiv 查询", "{{block_output_%s.url}}" % url_id, timeout_s=60, pos=(1200, 0)),
        cv.code(parse_id, "解析 Atom", [cv.param("body", cv.ref(http_id, "body"))],
                [cv.out("paper_texts", "list", schema={"type": "string"}), cv.out("arxiv_ids", "list", schema={"type": "string"}),
                 cv.out("count", "integer"), cv.out("error")], CODE_PARSE_ATOM, pos=(1600, 0)),
    ]
    # --- batch: selector LLM -> pack code
    inner_llm = cv.llm(sel_id, "机制判定", SELECT_PROMPT, SELECT_SYSTEM,
                       params=[cv.param("item", cv.ref(batch_id, "items")), cv.param("pain_text", cv.ref(E, "pain_text")),
                               cv.param("module_text", cv.ref(E, "module_text"))],
                       outputs=[cv.out("decision", "boolean"), cv.out("arxiv_id"), cv.out("relevance", "integer"), cv.out("reason"),
                                cv.out("mechanisms", "list", schema={"type": "object", "schema": MECH_SCHEMA})],
                       max_tokens=8192, pos=(200, 100))
    inner_pack = cv.code(pack_id, "打包",
                         [cv.param("decision", cv.ref(sel_id, "decision", "boolean")), cv.param("arxiv_id", cv.ref(sel_id, "arxiv_id")),
                          cv.param("relevance", cv.ref(sel_id, "relevance", "integer")), cv.param("reason", cv.ref(sel_id, "reason")),
                          cv.param("mechanisms", cv.ref(sel_id, "mechanisms", "list<object>", schema={"type": "object", "schema": MECH_SCHEMA}))],
                         [cv.out("result_json")], CODE_PACK, pos=(600, 100))
    nodes.append(cv.batch(batch_id, "逐篇判定", cv.ref(parse_id, "paper_texts", "list<string>"),
                          [inner_llm, inner_pack], cv.batch_edges(batch_id, [sel_id, pack_id]),
                          outputs=[{"name": "result_json_list", "input": cv.ref(pack_id, "result_json", "list<string>")}],
                          batch_size=60, concurrent=5, pos=(2000, 0)))
    nodes.append(cv.code(sum_id, "汇总过滤",
                         [cv.param("result_json_list", cv.ref(batch_id, "result_json_list", "list<string>")),
                          cv.param("threshold", cv.ref(E, "threshold"))],
                         [cv.out("kept_json", "list", schema={"type": "string"}), cv.out("kept_count", "integer"),
                          cv.out("judged", "integer"), cv.out("summary_md")], CODE_SUMMARY, pos=(2400, 0)))
    # --- persist: dedupe against paper table, then two batches of custom-SQL inserts
    T = IDS["tables_online"]  # workflow DB nodes reference the online table id; bind_to_bot uses the draft id
    nodes.append(cv.custom_sql(sql_sel_id, "已有论文", T["paper"], SQL_SELECT_EXISTING, [], [cv.out("arxiv_id")], pos=(2800, 0)))
    nodes.append(cv.code(flat_id, "拆平入库行",
                         [cv.param("kept_json", cv.ref(sum_id, "kept_json", "list<string>")),
                          cv.param("paper_texts", cv.ref(parse_id, "paper_texts", "list<string>")),
                          cv.param("existing", cv.ref(sql_sel_id, "outputList", "list<object>", schema={"type": "object", "schema": [cv.out("arxiv_id")]})),
                          cv.param("pain_point_id", cv.ref(E, "pain_point_id"))],
                         [cv.out("paper_rows", "list", schema={"type": "string"}), cv.out("mech_rows", "list", schema={"type": "string"}),
                          cv.out("new_papers", "integer"), cv.out("new_mechs", "integer")], CODE_FLATTEN, pos=(3200, 0)))
    paper_fields = ["arxiv_id", "title", "authors", "published", "abstract", "url", "source", "first_seen"]
    bp_inner = [cv.code(bp_code, "解包论文", [cv.param("row", cv.ref(bp_id, "items"))], [cv.out(f) for f in paper_fields], CODE_UNPACK_PAPER, pos=(200, 100)),
                cv.custom_sql(bp_sql, "写入 paper", T["paper"], SQL_INSERT_PAPER, [cv.param(f, cv.ref(bp_code, f)) for f in paper_fields], [], pos=(600, 100))]
    nodes.append(cv.batch(bp_id, "写入论文表", cv.ref(flat_id, "paper_rows", "list<string>"), bp_inner, cv.batch_edges(bp_id, [bp_code, bp_sql]),
                          outputs=[{"name": "rows", "input": cv.ref(bp_sql, "rowNum", "list<string>")}], batch_size=100, concurrent=3, pos=(3600, 0)))
    mech_fields_s = ["arxiv_id", "name", "description", "target_module", "how_to_apply", "relevance_reason", "tags", "status"]
    bm_outs = [cv.out(f) for f in mech_fields_s] + [cv.out("pain_point_id", "integer"), cv.out("relevance", "integer")]
    bm_params = [cv.param(f, cv.ref(bm_code, f)) for f in mech_fields_s] + \
                [cv.param("pain_point_id", cv.ref(bm_code, "pain_point_id", "integer")), cv.param("relevance", cv.ref(bm_code, "relevance", "integer"))]
    bm_inner = [cv.code(bm_code, "解包机制", [cv.param("row", cv.ref(bm_id, "items"))], bm_outs, CODE_UNPACK_MECH, pos=(200, 100)),
                cv.custom_sql(bm_sql, "写入 mechanism", T["mechanism"], SQL_INSERT_MECH, bm_params, [], pos=(600, 100))]
    nodes.append(cv.batch(bm_id, "写入机制表", cv.ref(flat_id, "mech_rows", "list<string>"), bm_inner, cv.batch_edges(bm_id, [bm_code, bm_sql]),
                          outputs=[{"name": "rows", "input": cv.ref(bm_sql, "rowNum", "list<string>")}], batch_size=100, concurrent=3, pos=(4000, 0)))
    nodes.append(cv.exit_node([cv.param("summary_md", cv.ref(sum_id, "summary_md")),
                               cv.param("kept_count", cv.ref(sum_id, "kept_count", "integer")),
                               cv.param("judged", cv.ref(sum_id, "judged", "integer")),
                               cv.param("new_papers", cv.ref(flat_id, "new_papers", "integer")),
                               cv.param("new_mechs", cv.ref(flat_id, "new_mechs", "integer")),
                               cv.param("arxiv_query", cv.ref(q_id, "arxiv_query")),
                               cv.param("parse_error", cv.ref(parse_id, "error"))], pos=(4400, 0)))
    edges = [cv.edge(E, q_id), cv.edge(q_id, url_id), cv.edge(url_id, http_id), cv.edge(http_id, parse_id),
             cv.edge(parse_id, batch_id), cv.edge(batch_id, sum_id, "batch-output"), cv.edge(sum_id, sql_sel_id),
             cv.edge(sql_sel_id, flat_id), cv.edge(flat_id, bp_id), cv.edge(bp_id, bm_id, "batch-output"),
             cv.edge(bm_id, cv.EXIT_ID, "batch-output")]
    return cv.canvas(nodes, edges)


SAMPLE = {
    "project_summary": "ShopSteward：零售门店备货协作系统。FastAPI 业务后端 + LangGraph Mission Agent（工具循环、澄清与恢复、持久检查点、记忆与任务 Skill）+ 七日需求预测 + 经营模拟器。",
    "module_text": "agent/：LangGraph 运行时，Mission Agent 的工具循环、澄清、恢复 Case、Context Engineering（输入准入、TaskFrame、上下文预算、调用账本）。",
    "pain_text": "Agent 在多步工具调用中遇到工具返回异常或数据缺失时，恢复策略单一（重试或直接澄清），缺少按失败类型选择恢复动作的机制；希望降低任务中断率并保持可解释。",
    "max_results": "25",
    "threshold": "7",
    "pain_point_id": "0",
}


def main():
    c = Coze()
    c.login()
    wid = c.acct.get("wf_b_id") or create_workflow(c, "radar_wf_b_discover", "WF-B 发散检索：痛点→arXiv→机制判定")
    c.acct["wf_b_id"] = wid
    save_account(c.acct)
    schema = build()
    Path(__file__).resolve().parent.parent.joinpath("canvases").mkdir(exist_ok=True)
    Path(__file__).resolve().parent.parent.joinpath("canvases", "wf_b.json").write_text(schema, encoding="utf-8")
    r = save_workflow(c, wid, schema)
    print("workflow_id", wid, "save ->", r.get("code"), r.get("msg"))
    if "--no-run" in sys.argv:
        return
    sample = dict(SAMPLE)
    if "--small" in sys.argv:
        sample["max_results"] = "6"
    d = test_run(c, wid, sample, bot_id=c.acct.get("bot_id"), timeout_s=900)
    print(summarize_process(d))
    Path(__file__).resolve().parent.parent.joinpath("runs").mkdir(exist_ok=True)
    Path(__file__).resolve().parent.parent.joinpath("runs", "wf_b_last.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
