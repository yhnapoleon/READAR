"""M5: configure the Radar agent: persona, model, four workflows, knowledge base (databases are bound via bind_to_bot)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS = json.loads((ROOT / "radar_ids.json").read_text(encoding="utf-8"))

PERSONA = """# 角色
你是「论文→实践雷达」的研究工程助手。服务对象是一个正在做工程项目的研究者。你的库里有：项目（project）、痛点（pain_point）、候选论文（paper）、从论文拆出的可移植机制（mechanism）、交给编码 Agent 的实验卡（experiment_card），以及知识库里的项目文档和历次实验的经验条目。

# 能力与路由
- 用户说"针对某个痛点找论文/机制"→ 调用工作流 radar_wf_b_discover，需要 pain_text、module_text、project_summary（从库里查），max_results 默认 25，threshold 默认 7。
- 用户说"评估可行性/出实验卡"→ 调用 radar_wf_c_feasibility。
- 用户说"记录实验结果/回写"→ 调用 radar_wf_d_report。
- 用户说"给项目建画像"→ 调用 radar_wf_a_profile。
- 其他问题（有哪些候选机制、某篇论文的机制是什么、上次实验为什么失败、某个痛点对应哪些卡）→ 先查数据库，再查知识库，引用 arxiv_id 和卡 id 回答。

# 规则
- 只说库里有依据的内容；库里没有就说没有，不要编论文或结果。
- 回答用中文，简洁，先结论后依据。列机制时带 arxiv_id、relevance 和 status。
- 不替用户决定要不要跑实验，只给数据和建议。"""


def main():
    c = Coze()
    c.login()
    bot_id = c.acct["bot_id"]
    cur = c.post("/api/playground_api/draftbot/get_draft_bot_info", {"bot_id": bot_id})["data"]["bot_info"]
    wfs = []
    for key, name, desc in [("wf_a_id", "radar_wf_a_profile", "项目画像：文档→模块与痛点"),
                            ("wf_b_id", "radar_wf_b_discover", "发散检索：痛点→arXiv→机制判定→入库"),
                            ("wf_c_id", "radar_wf_c_feasibility", "可行性评估与实验卡"),
                            ("wf_d_id", "radar_wf_d_report", "结果回写与经验沉淀")]:
        wid = c.acct[key]
        wfs.append({"workflow_id": wid, "plugin_id": wid, "workflow_name": name, "desc": desc, "flow_mode": 0})
    model_info = dict(cur.get("model_info") or {})
    model_info.update({"model_id": "100001", "max_tokens": 8192, "temperature": 1,
                       "short_memory_policy": {"context_mode": 2, "history_round": 6}})
    bot_info = {
        "bot_id": bot_id,
        "name": "Radar 论文雷达",
        "description": "论文→实践雷达：找机制、判可行、出实验卡、记结果",
        "prompt_info": {"prompt": PERSONA},
        "model_info": model_info,
        "workflow_info_list": wfs,
        "knowledge": {"knowledge_info": [{"id": IDS["kb"], "name": "KB-radar"}], "top_k": 5, "min_score": 0.3, "auto": True,
                      "search_strategy": 1, "show_source": True,
                      "recall_strategy": {"use_rerank": False, "use_rewrite": True, "use_nl2sql": True}},
        "onboarding_info": {"prologue": "我是论文→实践雷达。可以：为痛点找可移植机制、评估可行性并出实验卡、记录实验结果。先告诉我项目和痛点，或者问我库里有什么。",
                            "suggested_questions": ["ShopSteward 现在有哪些候选机制？", "针对工具调用失败恢复，找新论文", "实验卡里哪些是 blocked，缺什么？"]},
    }
    r = c.post("/api/playground_api/draftbot/update_draft_bot_info", {"bot_info": bot_info})
    print("update ->", r.get("code"), r.get("msg"), str(r.get("data"))[:200])
    after = c.post("/api/playground_api/draftbot/get_draft_bot_info", {"bot_id": bot_id})["data"]["bot_info"]
    print("workflows bound:", [w.get("workflow_name") for w in after.get("workflow_info_list") or []])
    print("knowledge:", json.dumps(after.get("knowledge", {}).get("knowledge_info"), ensure_ascii=False))
    print("prompt chars:", len((after.get("prompt_info") or {}).get("prompt", "")))
    print("databases:", [d.get("table_name") for d in after.get("database_list") or []])


if __name__ == "__main__":
    main()
