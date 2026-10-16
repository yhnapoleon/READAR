"""WF-C 可行性与实验卡: candidate mechanisms -> per-paper evidence (HF code link, arXiv HTML method text)
-> multi-criteria feasibility -> write scores -> cards for passers.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze, save_account  # noqa: E402
import canvas as cv  # noqa: E402
from workflow_api import create_workflow, save_workflow, test_run, summarize_process  # noqa: E402

E = cv.ENTRY_ID
IDS = json.loads((Path(__file__).resolve().parent.parent / "radar_ids.json").read_text(encoding="utf-8"))
T = IDS["tables_online"]

SQL_CANDIDATES = ("select id, arxiv_id, name, description, target_module, how_to_apply, relevance from mechanism "
                  "where status = 'candidate' and relevance >= {{min_relevance}} and ({{pain_point_id}} = 0 or pain_point_id = {{pain_point_id}}) "
                  "order by relevance desc limit {{limit}}")
SQL_PAPERS = "select arxiv_id, title, abstract from paper"
SQL_UPDATE_MECH = ("update mechanism set feasibility_json = '{{feasibility_json}}', feasibility_score = {{score}}, status = '{{status}}' "
                   "where id = {{mid}}")
SQL_INSERT_CARD = ("insert into experiment_card (mechanism_id, title, hypothesis, change_scope, steps, eval_method, budget_min, status, note, created_at) "
                   "values ({{mechanism_id}}, '{{title}}', '{{hypothesis}}', '{{change_scope}}', '{{steps}}', '{{eval_method}}', {{budget_min}}, '{{status}}', '{{note}}', '{{created_at}}')")

CODE_GROUP = r'''
import json
async def main(args: Args) -> Output:
    rows = args.params.get("rows") or []
    papers = {}
    for pr in (args.params.get("papers") or []):
        if isinstance(pr, dict) and pr.get("arxiv_id"):
            papers[str(pr["arxiv_id"])] = pr
    by = {}
    for r in rows:
        aid = str(r.get("arxiv_id") or "")
        if not aid:
            continue
        by.setdefault(aid, []).append({"id": int(r.get("id") or 0), "name": r.get("name"), "description": r.get("description"),
                                       "target_module": r.get("target_module"), "how_to_apply": r.get("how_to_apply"),
                                       "relevance": int(r.get("relevance") or 0)})
    items = [json.dumps({"arxiv_id": k, "mechanisms": v, "title": (papers.get(k) or {}).get("title") or "",
                         "abstract": (papers.get(k) or {}).get("abstract") or ""}, ensure_ascii=False) for k, v in by.items()]
    return {"paper_items": items, "paper_count": len(items), "mech_count": len(rows)}
'''

CODE_PREP = r'''
import json
async def main(args: Args) -> Output:
    d = json.loads(args.params.get("item") or "{}")
    aid = d.get("arxiv_id") or ""
    method_text = ("title: " + (d.get("title") or "") + chr(10) + "abstract: " + (d.get("abstract") or "")).strip()
    # LLMs lose precision on 19-digit ids: expose a small idx and keep the real ids in id_map_json
    mechs, id_map = [], {}
    for i, m in enumerate(d.get("mechanisms") or [], start=1):
        id_map[str(i)] = int(m.get("id") or 0)
        mechs.append({"idx": i, "name": m.get("name"), "description": m.get("description"), "target_module": m.get("target_module"),
                      "how_to_apply": m.get("how_to_apply"), "relevance": m.get("relevance")})
    return {"arxiv_id": aid, "mechanisms_json": json.dumps(mechs, ensure_ascii=False), "id_map_json": json.dumps(id_map),
            "hf_url": "https://huggingface.co/api/papers/" + aid, "method_text": method_text}
'''

CODE_EXTRACT = r'''
import json
async def main(args: Args) -> Output:
    hf_raw = args.params.get("hf_body") or ""
    code_url, stars, upvotes = "", 0, 0
    try:
        hf = json.loads(hf_raw)
        if isinstance(hf, dict) and not hf.get("error"):
            code_url = hf.get("githubRepo") or ""
            stars = int(hf.get("githubStars") or 0)
            upvotes = int(hf.get("upvotes") or 0)
    except Exception:
        pass
    return {"code_url": code_url, "github_stars": stars, "hf_upvotes": upvotes}
'''

FEAS_SYSTEM = """你是严谨的研究工程师，评估"把论文中的某个机制移植进一个现有项目"的可行性。
你会拿到：项目概述、目标模块说明、该论文已拆出的机制列表（每条有 idx 序号）、论文正文片段（可能为空）、代码链接（可能为空）。
对列表里的每条机制恰好输出一条评估，mechanism_idx 填该机制的 idx；每项都要给 evidence：引用论文正文或机制描述里的原话（不超过 40 字），没有依据写 "unknown"，不要猜。
字段含义：
- needs_training: 机制生效是否需要训练或微调模型（用现成 API 模型做不到）
- needs_external_data: 是否需要项目里没有的数据集或标注
- framework_dependency: 依赖的特定框架或模型内部访问（如需要隐藏层、logits、特定训练框架）；没有写 "none"
- has_reference_impl: 有没有可参考的开源实现
- change_scope: S（≤3 个文件、不改接口）/ M（一个模块内多处）/ L（跨模块或改接口）
- eval_feasible_in_project: 能否只用项目现有的测试、模拟器或样例数据评测它
- single_mechanism_transplantable: 能否不带整篇论文的其他部分、单独移植
只输出 JSON：{"assessments": [ {"mechanism_idx": <int>, "needs_training": bool, "needs_external_data": bool, "framework_dependency": "...",
 "has_reference_impl": bool, "change_scope": "S|M|L", "eval_feasible_in_project": bool, "single_mechanism_transplantable": bool,
 "evidence": "...", "notes": "一句话补充"} ] }"""

FEAS_PROMPT = """项目概述：{{project_summary}}
目标模块：{{module_text}}
代码链接：{{code_url}}

机制列表（JSON）：
{{mechanisms_json}}

论文标题与摘要（v1 没有全文，判定依据不足时写 unknown）：
{{method_text}}"""

CODE_SCORE = r'''
import json
W = {"needs_training": -1.0, "needs_external_data": -0.35, "has_reference_impl": 0.15, "eval_feasible_in_project": 0.25,
     "single_mechanism_transplantable": 0.25}
SCOPE = {"S": 0.35, "M": 0.15, "L": -1.0}
async def main(args: Args) -> Output:
    p = args.params
    mechs, id_map = {}, {}
    try:
        for m in json.loads(p.get("mechanisms_json") or "[]"):
            mechs[int(m.get("idx") or 0)] = m
        id_map = {int(k): int(v) for k, v in json.loads(p.get("id_map_json") or "{}").items()}
    except Exception:
        pass
    code_url = p.get("code_url") or ""
    out, seen = [], set()
    for a in (p.get("assessments") or []):
        idx = int(a.get("mechanism_idx") or 0)
        mid = id_map.get(idx, 0)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        a["mechanism_id"] = mid
        base = 0.4
        hard_reject = bool(a.get("needs_training")) or (a.get("change_scope") == "L")
        fd = str(a.get("framework_dependency") or "none").lower()
        if any(k in fd for k in ("hidden", "logit", "weight", "internal", "隐藏", "权重")):
            hard_reject = True
        s = base
        for k, w in W.items():
            if bool(a.get(k)):
                s += w
        s += SCOPE.get(a.get("change_scope"), 0)
        if code_url:
            s += 0.05
        s = 0.0 if hard_reject else max(0.0, min(1.0, s))
        m = mechs.get(idx, {})
        out.append({"mechanism_id": mid, "arxiv_id": p.get("arxiv_id"), "score": round(s, 2), "hard_reject": hard_reject,
                    "feasibility": a, "code_url": code_url, "name": m.get("name"), "description": m.get("description"),
                    "target_module": m.get("target_module"), "how_to_apply": m.get("how_to_apply"), "relevance": m.get("relevance")})
    return {"assess_json": json.dumps(out, ensure_ascii=False)}
'''

CODE_FLATTEN = r'''
import json
async def main(args: Args) -> Output:
    thr = float(args.params.get("threshold") or 0.6)
    allrows, passers = [], []
    for s in (args.params.get("assess_json_list") or []):
        try:
            for r in json.loads(s):
                allrows.append(r)
                if (not r.get("hard_reject")) and float(r.get("score") or 0) >= thr:
                    passers.append(r)
        except Exception:
            continue
    upd = []
    for r in allrows:
        is_pass = (not r.get("hard_reject")) and float(r.get("score") or 0) >= thr
        upd.append(json.dumps({"mid": r["mechanism_id"], "score": r["score"], "status": "carded" if is_pass else "rejected",
                               "feasibility_json": json.dumps(r.get("feasibility"), ensure_ascii=False).replace("'", "’")}, ensure_ascii=False))
    lines = ["assessed: %d, pass: %d (threshold %.2f)" % (len(allrows), len(passers), thr)]
    for r in sorted(allrows, key=lambda x: -float(x.get("score") or 0)):
        f = r.get("feasibility") or {}
        lines.append("- %.2f %s [%s] %s | train=%s data=%s scope=%s eval=%s single=%s dep=%s" % (
            float(r.get("score") or 0), "PASS" if r in passers else "rej ", r.get("arxiv_id"), r.get("name"),
            f.get("needs_training"), f.get("needs_external_data"), f.get("change_scope"), f.get("eval_feasible_in_project"),
            f.get("single_mechanism_transplantable"), f.get("framework_dependency")))
    return {"updates": upd, "passers": [json.dumps(r, ensure_ascii=False) for r in passers],
            "assessed": len(allrows), "passed": len(passers), "summary_md": "\n".join(lines)}
'''

CODE_UNPACK_UPD = r'''
import json
async def main(args: Args) -> Output:
    r = json.loads(args.params.get("row") or "{}")
    return {"mid": int(r.get("mid") or 0), "score": float(r.get("score") or 0), "status": str(r.get("status") or ""),
            "feasibility_json": str(r.get("feasibility_json") or "")}
'''

CARD_SYSTEM = """你是研究工程师，为编码 Agent 写一张**最小规模**的实验卡：验证一个机制值不值得深入，不是完整实现。
硬性要求：
1. 范围：最多改 3 个文件，不改公开接口、不改数据库迁移、不加依赖。只做机制的最小版本（例如只覆盖一种失败类型、只接一个工具、只做一个策略表）。
2. hypothesis 格式固定："把 <机制的最小版本> 用在 <模块> 的 <位置>，预期 <指标> 相对基线 <方向/幅度>"。指标必须是下面"项目资料片段"里能跑出来的东西（某条测试命令的通过数、某个评测脚本的输出）。
3. steps 不超过 6 步。第 1 步永远是"运行 <原样抄录的命令> 记录基线"，最后一步永远是"再次运行同一命令，写 radar_result.json"。中间步骤写清改哪个目录、加什么。
4. eval_method 必须包含一条从"项目资料片段"里原样抄录的可运行命令。资料里找不到任何可运行命令时：blocked=true，block_reason 写缺什么，eval_method 写"前置：需先建评测集"。
5. budget_min 不超过 45。
6. 不要写资料里没出现过的文件名或命令。
只输出 JSON：{"title": "...", "hypothesis": "...", "change_scope": "改哪些目录/文件类型，预计改动数", "steps": ["...", "..."],
 "eval_method": "...", "budget_min": <int>, "blocked": bool, "block_reason": "..."}"""

CARD_PROMPT = """项目概述：{{project_summary}}
目标模块：{{module_text}}

可用评测命令（eval_method 和第 1 步只能从这里原样选一条，选与目标模块最相关的；写明在哪个目录下运行）：
{{eval_commands}}

项目资料片段（真实目录、文件以此为准）：
{{snippets}}

机制（来自 arXiv:{{arxiv_id}}，代码：{{code_url}}）：
名称：{{name}}
做法：{{description}}
怎么用到项目里：{{how_to_apply}}
可行性评估：{{feasibility_json}}"""

CODE_UNPACK_PASS = r'''
import json
async def main(args: Args) -> Output:
    r = json.loads(args.params.get("row") or "{}")
    def s(k): return str(r.get(k) or "")
    return {"mechanism_id": int(r.get("mechanism_id") or 0), "arxiv_id": s("arxiv_id"), "name": s("name"), "description": s("description"),
            "how_to_apply": s("how_to_apply"), "code_url": s("code_url") or "无",
            "feasibility_json": json.dumps(r.get("feasibility") or {}, ensure_ascii=False),
            "kb_query": (s("target_module") + " " + s("name") + " 测试命令 pytest 评测 验证")[:300]}
'''

CODE_SNIPPETS = r'''
async def main(args: Args) -> Output:
    rows = args.params.get("rows") or []
    parts = []
    for r in rows:
        t = (r.get("output") if isinstance(r, dict) else str(r)) or ""
        if t:
            parts.append("- " + t.strip()[:1500])
    return {"snippets": chr(10).join(parts)[:9000] or "（知识库没有检索到项目资料）", "snippet_count": len(parts)}
'''

CODE_PACK_CARD = r'''
import json, datetime
async def main(args: Args) -> Output:
    p = args.params
    def s(v): return str(v or "").replace("'", "’")
    steps = p.get("steps") or []
    steps_txt = "\n".join("%d. %s" % (i + 1, st) for i, st in enumerate(steps))
    blocked = bool(p.get("blocked"))
    return {"mechanism_id": int(p.get("mechanism_id") or 0), "title": s(p.get("title"))[:200], "hypothesis": s(p.get("hypothesis")),
            "change_scope": s(p.get("change_scope")), "steps": s(steps_txt), "eval_method": s(p.get("eval_method")),
            "budget_min": min(int(p.get("budget_min") or 60), 60), "status": "blocked" if blocked else "new",
            "note": s(p.get("block_reason")), "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
'''

ASSESS_SCHEMA = [cv.out("mechanism_idx", "integer"), cv.out("needs_training", "boolean"), cv.out("needs_external_data", "boolean"),
                 cv.out("framework_dependency"), cv.out("has_reference_impl", "boolean"), cv.out("change_scope"),
                 cv.out("eval_feasible_in_project", "boolean"), cv.out("single_mechanism_transplantable", "boolean"),
                 cv.out("evidence"), cv.out("notes")]
PAPER_SCHEMA = [cv.out("arxiv_id"), cv.out("title"), cv.out("abstract")]
CAND_SCHEMA = [cv.out("id", "integer"), cv.out("arxiv_id"), cv.out("name"), cv.out("description"), cv.out("target_module"),
               cv.out("how_to_apply"), cv.out("relevance", "integer")]


def build() -> str:
    sql_id, sqlp_id, grp_id, bp_id, prep_id, hf_id, ext_id, feas_id, score_id, flat_id = (cv.new_id() for _ in range(10))
    bu_id, bu_code, bu_sql, bc_id, bc_code, bc_llm, bc_pack, bc_sql = (cv.new_id() for _ in range(8))
    bc_kb, bc_snip = cv.new_id(), cv.new_id()

    nodes = [cv.entry([cv.out("project_summary"), cv.out("module_text"), cv.out("min_relevance"), cv.out("limit"), cv.out("threshold"),
                       cv.out("eval_commands"), cv.out("pain_point_id")])]
    nodes.append(cv.custom_sql(sql_id, "读候选机制", T["mechanism"], SQL_CANDIDATES,
                               [cv.param("min_relevance", cv.ref(E, "min_relevance")), cv.param("limit", cv.ref(E, "limit")),
                                cv.param("pain_point_id", cv.ref(E, "pain_point_id"))],
                               CAND_SCHEMA, pos=(400, 0)))
    nodes.append(cv.custom_sql(sqlp_id, "读论文摘要", T["paper"], SQL_PAPERS, [], PAPER_SCHEMA, pos=(600, 0)))
    nodes.append(cv.code(grp_id, "按论文分组",
                         [cv.param("rows", cv.ref(sql_id, "outputList", "list<object>", schema={"type": "object", "schema": CAND_SCHEMA})),
                          cv.param("papers", cv.ref(sqlp_id, "outputList", "list<object>", schema={"type": "object", "schema": PAPER_SCHEMA}))],
                         [cv.out("paper_items", "list", schema={"type": "string"}), cv.out("paper_count", "integer"), cv.out("mech_count", "integer")],
                         CODE_GROUP, pos=(800, 0)))
    # --- per-paper batch
    inner = [
        cv.code(prep_id, "准备", [cv.param("item", cv.ref(bp_id, "items"))],
                [cv.out("arxiv_id"), cv.out("mechanisms_json"), cv.out("id_map_json"), cv.out("hf_url"), cv.out("method_text")], CODE_PREP, pos=(100, 100)),
        cv.http_get(hf_id, "HF 论文信息", "{{block_output_%s.hf_url}}" % prep_id, timeout_s=30,
                    default_on_error={"body": "{}", "statusCode": 0, "headers": ""}, pos=(400, 100)),
        cv.code(ext_id, "解析 HF 信息", [cv.param("hf_body", cv.ref(hf_id, "body"))],
                [cv.out("code_url"), cv.out("github_stars", "integer"), cv.out("hf_upvotes", "integer")], CODE_EXTRACT, pos=(1000, 100)),
        cv.llm(feas_id, "可行性判定", FEAS_PROMPT, FEAS_SYSTEM,
               params=[cv.param("project_summary", cv.ref(E, "project_summary")), cv.param("module_text", cv.ref(E, "module_text")),
                       cv.param("code_url", cv.ref(ext_id, "code_url")), cv.param("mechanisms_json", cv.ref(prep_id, "mechanisms_json")),
                       cv.param("method_text", cv.ref(prep_id, "method_text"))],
               outputs=[cv.out("assessments", "list", schema={"type": "object", "schema": ASSESS_SCHEMA})], max_tokens=8192, pos=(1300, 100)),
        cv.code(score_id, "打分打包",
                [cv.param("assessments", cv.ref(feas_id, "assessments", "list<object>", schema={"type": "object", "schema": ASSESS_SCHEMA})),
                 cv.param("mechanisms_json", cv.ref(prep_id, "mechanisms_json")), cv.param("id_map_json", cv.ref(prep_id, "id_map_json")),
                 cv.param("code_url", cv.ref(ext_id, "code_url")), cv.param("arxiv_id", cv.ref(prep_id, "arxiv_id"))],
                [cv.out("assess_json")], CODE_SCORE, pos=(1600, 100)),
    ]
    nodes.append(cv.batch(bp_id, "逐篇评估", cv.ref(grp_id, "paper_items", "list<string>"), inner,
                          cv.batch_edges(bp_id, [prep_id, hf_id, ext_id, feas_id, score_id]),
                          outputs=[{"name": "assess_json_list", "input": cv.ref(score_id, "assess_json", "list<string>")}],
                          batch_size=50, concurrent=3, pos=(1200, 0)))
    nodes.append(cv.code(flat_id, "拆平评估",
                         [cv.param("assess_json_list", cv.ref(bp_id, "assess_json_list", "list<string>")), cv.param("threshold", cv.ref(E, "threshold"))],
                         [cv.out("updates", "list", schema={"type": "string"}), cv.out("passers", "list", schema={"type": "string"}),
                          cv.out("assessed", "integer"), cv.out("passed", "integer"), cv.out("summary_md")], CODE_FLATTEN, pos=(1600, 0)))
    # --- write scores back
    bu_inner = [cv.code(bu_code, "解包", [cv.param("row", cv.ref(bu_id, "items"))],
                        [cv.out("mid", "integer"), cv.out("score", "float"), cv.out("status"), cv.out("feasibility_json")], CODE_UNPACK_UPD, pos=(200, 100)),
                cv.custom_sql(bu_sql, "更新 mechanism", T["mechanism"], SQL_UPDATE_MECH,
                              [cv.param("mid", cv.ref(bu_code, "mid", "integer")), cv.param("score", cv.ref(bu_code, "score", "float")),
                               cv.param("status", cv.ref(bu_code, "status")), cv.param("feasibility_json", cv.ref(bu_code, "feasibility_json"))], [], pos=(600, 100))]
    nodes.append(cv.batch(bu_id, "写回可行性", cv.ref(flat_id, "updates", "list<string>"), bu_inner, cv.batch_edges(bu_id, [bu_code, bu_sql]),
                          outputs=[{"name": "rows", "input": cv.ref(bu_sql, "rowNum", "list<string>")}], batch_size=100, concurrent=3, pos=(2000, 0)))
    # --- cards for passers
    card_fields = ["mechanism_id", "title", "hypothesis", "change_scope", "steps", "eval_method", "budget_min", "status", "note", "created_at"]
    bc_inner = [
        cv.code(bc_code, "解包机制", [cv.param("row", cv.ref(bc_id, "items"))],
                [cv.out("mechanism_id", "integer"), cv.out("arxiv_id"), cv.out("name"), cv.out("description"), cv.out("how_to_apply"),
                 cv.out("code_url"), cv.out("feasibility_json"), cv.out("kb_query")], CODE_UNPACK_PASS, pos=(100, 100)),
        cv.knowledge_retriever(bc_kb, "检索项目资料", [IDS["kb"]], cv.ref(bc_code, "kb_query"), top_k=6, min_score=0.2, pos=(300, 100)),
        cv.code(bc_snip, "拼接资料",
                [cv.param("rows", cv.ref(bc_kb, "outputList", "list<object>", schema={"type": "object", "schema": [cv.out("output")]}))],
                [cv.out("snippets"), cv.out("snippet_count", "integer")], CODE_SNIPPETS, pos=(400, 100)),
        cv.llm(bc_llm, "生成实验卡", CARD_PROMPT, CARD_SYSTEM,
               params=[cv.param("project_summary", cv.ref(E, "project_summary")), cv.param("module_text", cv.ref(E, "module_text")),
                       cv.param("snippets", cv.ref(bc_snip, "snippets")), cv.param("eval_commands", cv.ref(E, "eval_commands"))] +
                      [cv.param(f, cv.ref(bc_code, f)) for f in ["arxiv_id", "code_url", "name", "description", "how_to_apply", "feasibility_json"]],
               outputs=[cv.out("title"), cv.out("hypothesis"), cv.out("change_scope"), cv.out("steps", "list", schema={"type": "string"}),
                        cv.out("eval_method"), cv.out("budget_min", "integer"), cv.out("blocked", "boolean"), cv.out("block_reason")],
               max_tokens=8192, pos=(500, 100)),
        cv.code(bc_pack, "打包卡",
                [cv.param("mechanism_id", cv.ref(bc_code, "mechanism_id", "integer")), cv.param("title", cv.ref(bc_llm, "title")),
                 cv.param("hypothesis", cv.ref(bc_llm, "hypothesis")), cv.param("change_scope", cv.ref(bc_llm, "change_scope")),
                 cv.param("steps", cv.ref(bc_llm, "steps", "list<string>")), cv.param("eval_method", cv.ref(bc_llm, "eval_method")),
                 cv.param("budget_min", cv.ref(bc_llm, "budget_min", "integer")), cv.param("blocked", cv.ref(bc_llm, "blocked", "boolean")),
                 cv.param("block_reason", cv.ref(bc_llm, "block_reason"))],
                [cv.out("mechanism_id", "integer"), cv.out("title"), cv.out("hypothesis"), cv.out("change_scope"), cv.out("steps"),
                 cv.out("eval_method"), cv.out("budget_min", "integer"), cv.out("status"), cv.out("note"), cv.out("created_at")], CODE_PACK_CARD, pos=(900, 100)),
        cv.custom_sql(bc_sql, "写入 experiment_card", T["experiment_card"], SQL_INSERT_CARD,
                      [cv.param(f, cv.ref(bc_pack, f, "integer" if f in ("mechanism_id", "budget_min") else "string")) for f in card_fields], [], pos=(1300, 100)),
    ]
    nodes.append(cv.batch(bc_id, "生成并写入实验卡", cv.ref(flat_id, "passers", "list<string>"), bc_inner,
                          cv.batch_edges(bc_id, [bc_code, bc_kb, bc_snip, bc_llm, bc_pack, bc_sql]),
                          outputs=[{"name": "rows", "input": cv.ref(bc_sql, "rowNum", "list<string>")},
                                   {"name": "titles", "input": cv.ref(bc_pack, "title", "list<string>")}],
                          batch_size=50, concurrent=3, pos=(2400, 0)))
    nodes.append(cv.exit_node([cv.param("summary_md", cv.ref(flat_id, "summary_md")), cv.param("assessed", cv.ref(flat_id, "assessed", "integer")),
                               cv.param("passed", cv.ref(flat_id, "passed", "integer")), cv.param("mech_count", cv.ref(grp_id, "mech_count", "integer")),
                               cv.param("card_titles", cv.ref(bc_pack, "title", "list<string>"))], pos=(2800, 0)))
    # the exit's card_titles must come from the batch output, not the inner node
    nodes[-1]["data"]["inputs"]["inputParameters"][-1] = cv.param("card_titles", cv.ref(bc_id, "titles", "list<string>"))
    edges = [cv.edge(E, sql_id), cv.edge(sql_id, sqlp_id), cv.edge(sqlp_id, grp_id), cv.edge(grp_id, bp_id), cv.edge(bp_id, flat_id, "batch-output"),
             cv.edge(flat_id, bu_id), cv.edge(bu_id, bc_id, "batch-output"), cv.edge(bc_id, cv.EXIT_ID, "batch-output")]
    return cv.canvas(nodes, edges)


SAMPLE = {
    "project_summary": "ShopSteward：零售门店备货协作系统。FastAPI 业务后端 + LangGraph Mission Agent（工具循环、澄清与恢复、持久检查点、记忆与任务 Skill）+ 七日需求预测 + 经营模拟器（SC01 与可配置 SANDBOX，提供可重复的经营事件）。有新版离线评测（D1–D6 / Q1–Q6、关键失败覆盖、三态结果），目前九个示例用于开发验证。",
    "module_text": "agent/：LangGraph 运行时，Mission Agent 的工具循环、澄清、恢复 Case、Context Engineering（输入准入、TaskFrame、上下文预算、调用账本）。tests/ 下有 pytest；simulation/ 提供可重复的经营事件与外部执行结果。",
    "min_relevance": "7",
    "limit": "40",
    "threshold": "0.6",
    "pain_point_id": "0",
    "eval_commands": "\n".join([
        "在 backend/ 目录下：..\\.venv\\Scripts\\python.exe -m pytest ../agent/tests -c pyproject.toml -q   （Agent 单元测试，基线 148 passed / 3 skipped）",
        "在 backend/ 目录下：..\\.venv\\Scripts\\python.exe -m pytest tests/unit tests/api -c pyproject.toml -q   （后端单元与 API 测试）",
        "在仓库根目录：.venv/Scripts/python.exe -m pytest ml/tests -q   （ML 运行时测试，与 Agent 无关）",
    ]),
}


def main():
    c = Coze()
    c.login()
    wid = c.acct.get("wf_c_id") or create_workflow(c, "radar_wf_c_feasibility", "WF-C 可行性评估与实验卡")
    c.acct["wf_c_id"] = wid
    save_account(c.acct)
    schema = build()
    root = Path(__file__).resolve().parent.parent
    root.joinpath("canvases").mkdir(exist_ok=True)
    root.joinpath("canvases", "wf_c.json").write_text(schema, encoding="utf-8")
    r = save_workflow(c, wid, schema)
    print("workflow_id", wid, "save ->", r.get("code"), r.get("msg"))
    if "--no-run" in sys.argv:
        return
    sample = dict(SAMPLE)
    if "--small" in sys.argv:
        sample["limit"] = "6"
    d = test_run(c, wid, sample, bot_id=c.acct.get("bot_id"), timeout_s=1500)
    print(summarize_process(d))
    root.joinpath("runs").mkdir(exist_ok=True)
    root.joinpath("runs", "wf_c_last.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
