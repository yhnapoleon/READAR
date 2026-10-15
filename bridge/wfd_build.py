"""WF-D 结果回写: card result -> update experiment_card + mechanism.status -> LLM one-line experience.
The experience text is returned to the bridge, which stores it in KB-radar (report_result.py).

usage: python wfd_build.py [--no-run]   (build/save only; report_result.py runs it)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze, save_account  # noqa: E402
import canvas as cv  # noqa: E402
from workflow_api import create_workflow, save_workflow  # noqa: E402

E = cv.ENTRY_ID
ROOT = Path(__file__).resolve().parent.parent
IDS = json.loads((ROOT / "radar_ids.json").read_text(encoding="utf-8"))
T = IDS["tables_online"]

SQL_CARD = "select id, mechanism_id, title, hypothesis, eval_method from experiment_card where id = {{card_id}}"
SQL_UPDATE_CARD = ("update experiment_card set status = '{{status}}', verdict = '{{verdict}}', result_json = '{{result_json}}', "
                   "note = '{{note}}', finished_at = '{{finished_at}}' where id = {{card_id}}")
SQL_MECH = "select id, arxiv_id, name, target_module, how_to_apply from mechanism where id = {{mechanism_id}}"
SQL_UPDATE_MECH = "update mechanism set status = 'tried' where id = {{mechanism_id}}"

CODE_PREP = r'''
import json, datetime
async def main(args: Args) -> Output:
    p = args.params
    cards = p.get("cards") or []
    c = cards[0] if cards else {}
    verdict = str(p.get("verdict") or "blocked")
    status = "done" if verdict in ("win", "neutral", "lose") else "blocked"
    def s(v): return str(v or "").replace("'", "’")
    rj = json.dumps({"metric": p.get("metric"), "baseline": p.get("baseline"), "after": p.get("after"),
                     "changed_files": p.get("changed_files"), "elapsed_min": p.get("elapsed_min")}, ensure_ascii=False)
    return {"mechanism_id": int(c.get("mechanism_id") or 0), "title": s(c.get("title")), "hypothesis": s(c.get("hypothesis")),
            "status": status, "result_json": s(rj), "note": s(p.get("notes"))[:1500],
            "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
'''

EXP_SYSTEM = """把一次机制移植实验的结果压成一条可复用的经验，给以后的论文判定做参考。中文，不超过 160 字，格式固定：
"在 <项目> 的 <模块> 移植 <机制>（arXiv:<id>）：<verdict>。指标 <metric> 从 <baseline> 到 <after>。原因：<一句>。局限：<备注里提到的最重要的一条，没有写无>。适用条件：<一句>。"
只输出 JSON：{"experience": "..."}"""
EXP_PROMPT = """项目：{{project_name}}
机制：{{mech_name}}（arXiv:{{arxiv_id}}），目标模块：{{target_module}}
实验卡：{{title}}；假设：{{hypothesis}}
结果：verdict={{verdict}}，metric={{metric}}，baseline={{baseline}}，after={{after}}
备注：{{notes}}"""

CODE_PICK_MECH = r'''
async def main(args: Args) -> Output:
    rows = args.params.get("rows") or []
    m = rows[0] if rows else {}
    return {"arxiv_id": str(m.get("arxiv_id") or ""), "mech_name": str(m.get("name") or ""), "target_module": str(m.get("target_module") or "")}
'''

CARD_SCHEMA = [cv.out("id", "integer"), cv.out("mechanism_id", "integer"), cv.out("title"), cv.out("hypothesis"), cv.out("eval_method")]
MECH_SCHEMA = [cv.out("id", "integer"), cv.out("arxiv_id"), cv.out("name"), cv.out("target_module"), cv.out("how_to_apply")]


def build() -> str:
    q_id, prep_id, upd_id, mq_id, pick_id, mupd_id, llm_id = (cv.new_id() for _ in range(7))
    ins = ["card_id", "verdict", "metric", "baseline", "after", "notes", "changed_files", "elapsed_min", "project_name"]
    nodes = [cv.entry([cv.out(k) for k in ins])]
    nodes.append(cv.custom_sql(q_id, "读卡", T["experiment_card"], SQL_CARD, [cv.param("card_id", cv.ref(E, "card_id"))], CARD_SCHEMA, pos=(400, 0)))
    nodes.append(cv.code(prep_id, "整理结果",
                         [cv.param("cards", cv.ref(q_id, "outputList", "list<object>", schema={"type": "object", "schema": CARD_SCHEMA}))] +
                         [cv.param(k, cv.ref(E, k)) for k in ["verdict", "metric", "baseline", "after", "notes", "changed_files", "elapsed_min"]],
                         [cv.out("mechanism_id", "integer"), cv.out("title"), cv.out("hypothesis"), cv.out("status"), cv.out("result_json"),
                          cv.out("note"), cv.out("finished_at")], CODE_PREP, pos=(800, 0)))
    nodes.append(cv.custom_sql(upd_id, "更新卡", T["experiment_card"], SQL_UPDATE_CARD,
                               [cv.param("card_id", cv.ref(E, "card_id")), cv.param("status", cv.ref(prep_id, "status")), cv.param("verdict", cv.ref(E, "verdict")),
                                cv.param("result_json", cv.ref(prep_id, "result_json")), cv.param("note", cv.ref(prep_id, "note")),
                                cv.param("finished_at", cv.ref(prep_id, "finished_at"))], [], pos=(1200, 0)))
    nodes.append(cv.custom_sql(mq_id, "读机制", T["mechanism"], SQL_MECH, [cv.param("mechanism_id", cv.ref(prep_id, "mechanism_id", "integer"))], MECH_SCHEMA, pos=(1600, 0)))
    nodes.append(cv.code(pick_id, "取机制", [cv.param("rows", cv.ref(mq_id, "outputList", "list<object>", schema={"type": "object", "schema": MECH_SCHEMA}))],
                         [cv.out("arxiv_id"), cv.out("mech_name"), cv.out("target_module")], CODE_PICK_MECH, pos=(2000, 0)))
    nodes.append(cv.custom_sql(mupd_id, "机制置为 tried", T["mechanism"], SQL_UPDATE_MECH, [cv.param("mechanism_id", cv.ref(prep_id, "mechanism_id", "integer"))], [], pos=(2400, 0)))
    nodes.append(cv.llm(llm_id, "压成经验", EXP_PROMPT, EXP_SYSTEM,
                        params=[cv.param("project_name", cv.ref(E, "project_name")), cv.param("mech_name", cv.ref(pick_id, "mech_name")),
                                cv.param("arxiv_id", cv.ref(pick_id, "arxiv_id")), cv.param("target_module", cv.ref(pick_id, "target_module")),
                                cv.param("title", cv.ref(prep_id, "title")), cv.param("hypothesis", cv.ref(prep_id, "hypothesis"))] +
                               [cv.param(k, cv.ref(E, k)) for k in ["verdict", "metric", "baseline", "after", "notes"]],
                        outputs=[cv.out("experience")], max_tokens=8192, pos=(2800, 0)))
    nodes.append(cv.exit_node([cv.param("experience", cv.ref(llm_id, "experience")), cv.param("status", cv.ref(prep_id, "status")),
                               cv.param("mechanism_id", cv.ref(prep_id, "mechanism_id", "integer")), cv.param("arxiv_id", cv.ref(pick_id, "arxiv_id"))], pos=(3200, 0)))
    edges = [cv.edge(E, q_id), cv.edge(q_id, prep_id), cv.edge(prep_id, upd_id), cv.edge(upd_id, mq_id), cv.edge(mq_id, pick_id),
             cv.edge(pick_id, mupd_id), cv.edge(mupd_id, llm_id), cv.edge(llm_id, cv.EXIT_ID)]
    return cv.canvas(nodes, edges)


def ensure(c: Coze) -> str:
    wid = c.acct.get("wf_d_id") or create_workflow(c, "radar_wf_d_report", "WF-D 结果回写：更新卡与机制，压成经验")
    c.acct["wf_d_id"] = wid
    save_account(c.acct)
    schema = build()
    (ROOT / "canvases").mkdir(exist_ok=True)
    (ROOT / "canvases" / "wf_d.json").write_text(schema, encoding="utf-8")
    r = save_workflow(c, wid, schema)
    print("WF-D workflow_id", wid, "save ->", r.get("code"), r.get("msg"))
    return wid


if __name__ == "__main__":
    c = Coze()
    c.login()
    ensure(c)
