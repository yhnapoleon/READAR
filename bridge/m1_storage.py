"""M1: create the radar's database tables and knowledge base. Idempotent via radar_ids.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze  # noqa: E402

IDS_FILE = Path(__file__).resolve().parent.parent / "radar_ids.json"
T, N, D, F, B = 1, 2, 3, 4, 5  # FieldItemType: Text Number Date Float Boolean


def fld(name, typ, desc="", required=False):
    return {"name": name, "type": typ, "desc": desc, "must_required": required, "id": 0}


TABLES = {
    "project": ("被雷达服务的项目", [
        fld("name", T, "项目名", True), fld("repo_path", T, "本机仓库路径"), fld("stack", T, "技术栈"),
        fld("summary", T, "一段话概述"), fld("kb_doc_ids", T, "知识库文档 id，逗号分隔"), fld("updated_at", T)]),
    "pain_point": ("项目痛点清单，WF-A 产出", [
        fld("project_id", N, "project.id", True), fld("module", T, "所属模块"), fld("description", T, "痛点描述", True),
        fld("metric", T, "可观测指标，没有写 null"), fld("priority", N, "1 高 3 低"), fld("status", T, "open/closed")]),
    "paper": ("候选论文", [
        fld("arxiv_id", T, "去掉版本号", True), fld("title", T), fld("authors", T), fld("published", T),
        fld("abstract", T), fld("url", T), fld("code_url", T), fld("cited_by", N),
        fld("source", T, "search/expand/manual"), fld("first_seen", T)]),
    "mechanism": ("从论文拆出的可移植机制", [
        fld("arxiv_id", T, "paper.arxiv_id", True), fld("name", T, "机制名", True), fld("description", T),
        fld("target_module", T), fld("pain_point_id", N), fld("how_to_apply", T), fld("relevance", N, "0-10"),
        fld("relevance_reason", T), fld("tags", T, "loop_stage/agent_type/coordination JSON"),
        fld("feasibility_json", T), fld("feasibility_score", F, "0-1"), fld("status", T, "candidate/carded/rejected/tried")]),
    "experiment_card": ("交给编码 Agent 的实验卡", [
        fld("mechanism_id", N, "mechanism.id", True), fld("title", T, "", True), fld("hypothesis", T),
        fld("change_scope", T), fld("steps", T), fld("eval_method", T), fld("budget_min", N),
        fld("status", T, "new/running/done/failed/blocked"), fld("result_json", T), fld("verdict", T, "win/neutral/lose/blocked"),
        fld("note", T), fld("created_at", T), fld("finished_at", T)]),
}


def load_ids():
    return json.loads(IDS_FILE.read_text(encoding="utf-8")) if IDS_FILE.exists() else {"tables": {}, "fields": {}}


def save_ids(ids):
    IDS_FILE.write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    c = Coze()
    login_data = c.login()
    uid = str(login_data.get("user_id_str") or login_data.get("user_id") or c.acct.get("user_id") or "")
    ids = load_ids()
    for name, (desc, fields) in TABLES.items():
        if name in ids["tables"]:
            print("table exists:", name, ids["tables"][name]); continue
        body = c.post("/api/memory/database/add", {
            "creator_id": uid, "space_id": c.space_id(), "project_id": "0",
            "icon_uri": "default_icon/default_database_icon.png",
            "table_name": name, "table_desc": desc, "field_list": fields,
            "rw_mode": 3, "prompt_disabled": False})
        info = body["database_info"] if "database_info" in body else body["data"]["database_info"]
        ids["tables"][name] = str(info["id"])
        ids["fields"][name] = {f["name"]: str(f["id"]) for f in info.get("field_list", [])}
        save_ids(ids)
        print("created table", name, info["id"], "fields:", len(info.get("field_list", [])))
    if "kb" not in ids:
        body = c.post("/api/knowledge/create", {
            "name": "KB-radar", "description": "项目文档、分类框架、经验条目", "space_id": c.space_id(),
            "icon_uri": "default_icon/default_dataset_icon.png", "format_type": 0, "biz_id": "0", "project_id": "0"})
        ids["kb"] = str(body.get("dataset_id") or body["data"]["dataset_id"])
        save_ids(ids)
        print("created knowledge base", ids["kb"])
    else:
        print("kb exists:", ids["kb"])


if __name__ == "__main__":
    main()
