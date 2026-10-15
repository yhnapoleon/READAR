"""One-shot demo: pain point -> WF-B (papers, mechanisms) -> WF-C (feasibility, cards) -> Claude Code runs one card
-> WF-D (write-back, experience). Writes demo/DEMO-<timestamp>.md.

usage: python demo.py --project ShopSteward --repo <path> --pain-point-id <id> [--max-results 15] [--budget-min 20] [--skip-cc]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze  # noqa: E402
from workflow_api import test_run  # noqa: E402
from pull_cards import fetch_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS = json.loads((ROOT / "radar_ids.json").read_text(encoding="utf-8"))
PY = sys.executable


def node_output(d: dict, name: str) -> dict:
    for n in d.get("nodeResults", []):
        if n.get("NodeName") == name and n.get("output"):
            try:
                return json.loads(n["output"])
            except Exception:
                return {}
    return {}


def run(cmd: list[str], timeout: int = 3600) -> str:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       env={**__import__("os").environ, "PYTHONUTF8": "1"})
    return p.stdout + ("\n[stderr]\n" + p.stderr if p.returncode else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pain-point-id", required=True)
    ap.add_argument("--max-results", default="15")
    ap.add_argument("--budget-min", type=int, default=20)
    ap.add_argument("--skip-cc", action="store_true")
    ap.add_argument("--from-cards", action="store_true", help="skip WF-B/WF-C; rebuild sections 2-3 from the DB and continue")
    a = ap.parse_args()
    t_start = time.time()
    c = Coze()
    c.login()
    bot = c.acct["bot_id"]
    report = [f"# 论文→实践雷达 演示报告\n\n项目 {a.project} · {time.strftime('%Y-%m-%d %H:%M')}\n"]

    # 0. context from DB
    proj = next((p for p in fetch_all(c, "project") if p.get("name") == a.project), None)
    pain = next((p for p in fetch_all(c, "pain_point") if str(p.get("bstudio_id")) == str(a.pain_point_id)), None)
    if not proj or not pain:
        sys.exit("project or pain point not found in DB")
    summary = proj.get("summary", "")
    eval_cmds = summary.split("评测命令: ", 1)[1] if "评测命令: " in summary else ""
    module_line = next((m for m in summary.split("模块: ", 1)[-1].split("; ") if m.startswith(pain.get("module", "").rstrip("/"))), pain.get("module", ""))
    report.append(f"## 1. 痛点（WF-A 从项目文档抽出）\n\n- 模块：{pain.get('module')}\n- 描述：{pain.get('description')}\n- 指标：{pain.get('metric')}\n")
    print("pain point:", pain.get("description")[:80])

    if a.from_cards:
        mechs_all = [m for m in fetch_all(c, "mechanism") if str(m.get("pain_point_id")) == str(a.pain_point_id)]
        papers = {m["arxiv_id"] for m in mechs_all}
        mechs_all.sort(key=lambda m: (-float(m.get("feasibility_score") or 0), -int(m.get("relevance") or 0)))
        lines = [f"- [{m['arxiv_id']}] {m['name']} → {m.get('target_module')} | relevance {m.get('relevance')} | feasibility {m.get('feasibility_score')} | {m.get('status')}"
                 for m in mechs_all[:20]]
        report.append(f"## 2–3. 发散检索与可行性（从库里读取，WF-B / WF-C 已在上一次运行完成）\n\n- 命中论文 {len(papers)} 篇，拆出机制 {len(mechs_all)} 条，"
                      f"过线并出卡 {sum(1 for m in mechs_all if m.get('status') == 'carded')} 条\n\n前 20 条机制：\n" + "\n".join(lines) + "\n")
        print(f"from DB: papers {len(papers)} mechanisms {len(mechs_all)}")

    # 1. WF-B
    print("WF-B ...") if not a.from_cards else None
    d = {} if a.from_cards else test_run(c, c.acct["wf_b_id"], {
        "pain_text": pain["description"], "module_text": module_line, "project_summary": summary.split(chr(10))[0],
        "max_results": a.max_results, "threshold": "7", "pain_point_id": str(a.pain_point_id)}, bot_id=bot, timeout_s=1500)
    if not a.from_cards:
        end = node_output(d, "结束")
        report.append(f"## 2. 发散检索（WF-B）\n\n- 检索式：`{end.get('arxiv_query')}`\n- 判定 {end.get('judged')} 篇，留下 {end.get('kept_count')} 篇，"
                      f"新入库论文 {end.get('new_papers')}，新机制 {end.get('new_mechs')}\n\n```\n{node_output(d, '汇总过滤').get('summary_md', '')}\n```\n")
        print(f"WF-B done: judged {end.get('judged')} kept {end.get('kept_count')} new_mechs {end.get('new_mechs')} ({d.get('workflowExeCost')})")

        # 2. WF-C
        print("WF-C ...")
        d = test_run(c, c.acct["wf_c_id"], {
            "project_summary": summary.split(chr(10))[0], "module_text": module_line, "min_relevance": "7", "limit": "12",
            "threshold": "0.6", "eval_commands": eval_cmds.replace(" || ", chr(10)), "pain_point_id": str(a.pain_point_id)}, bot_id=bot, timeout_s=1800)
        flat = node_output(d, "拆平评估")
        titles = node_output(d, "结束").get("card_titles") or []
        report.append(f"## 3. 可行性与实验卡（WF-C）\n\n```\n{flat.get('summary_md', '')}\n```\n\n生成实验卡 {len(titles)} 张：\n" +
                      "\n".join(f"- {t}" for t in titles) + "\n")
        print(f"WF-C done: assessed {flat.get('assessed')} passed {flat.get('passed')} cards {len(titles)} ({d.get('workflowExeCost')})")

    # 3. pick a card and run it with Claude Code
    print(run([PY, "bridge/pull_cards.py", "new"]))
    cards = [r for r in fetch_all(c, "experiment_card") if r.get("status") == "new"]
    mechs = {m["bstudio_id"]: m for m in fetch_all(c, "mechanism")}
    cards = [r for r in cards if str(mechs.get(str(r.get("mechanism_id")), {}).get("pain_point_id")) == str(a.pain_point_id)]
    cards.sort(key=lambda r: int(r.get("budget_min") or 60))
    if not cards:
        report.append("## 4. 没有可执行的卡（全部 blocked）\n")
    else:
        card = cards[0]
        cid = card["bstudio_id"]
        card_md = (ROOT / "cards" / f"CARD-{cid}.md").read_text(encoding="utf-8")
        report.append(f"## 4. 选中的实验卡\n\n```markdown\n{card_md}\n```\n")
        if a.skip_cc:
            report.append("（--skip-cc：未执行）\n")
        else:
            print(f"Claude Code runs CARD-{cid} (budget {a.budget_min} min) ...")
            out = run([PY, "bridge/run_card.py", "--card", f"cards/CARD-{cid}.md", "--repo", a.repo, "--budget-min", str(a.budget_min)],
                      timeout=a.budget_min * 60 + 600)
            res = json.loads((ROOT / "runs" / f"card-{cid}.json").read_text(encoding="utf-8"))
            rr = res.get("radar_result") or {}
            cc = res.get("cc") or {}
            report.append(f"## 5. Claude Code 执行结果\n\n- verdict：**{rr.get('verdict', '?')}**\n- 指标：{rr.get('metric')}\n- 基线：{rr.get('baseline')}\n- 改后：{rr.get('after')}\n"
                          f"- 改动文件：{', '.join(rr.get('changed_files') or [])}\n- 用时 {res.get('elapsed_min')} 分钟，{cc.get('num_turns')} 轮，约 {round(cc.get('total_cost_usd') or 0, 2)} 美元\n"
                          f"- 分支：`{res.get('branch')}`（{(res.get('commits') or '').strip()}）\n\n**CC 的说明：**\n\n{rr.get('notes', '')}\n\n**CC 最终回复：**\n\n{(cc.get('result') or '')[:3000]}\n")
            print(f"CC done: {rr.get('verdict')} in {res.get('elapsed_min')} min")
            # 4. WF-D
            print("WF-D ...")
            out = run([PY, "bridge/report_result.py", "--run", f"runs/card-{cid}.json", "--project", a.project])
            exp = next((l.split("experience:", 1)[1].strip() for l in out.splitlines() if l.startswith("experience:")), "")
            report.append(f"## 6. 回写（WF-D）\n\n经验条目已写入知识库：\n\n> {exp}\n")
            print("experience:", exp)

    report.append(f"\n---\n总用时 {round((time.time() - t_start) / 60, 1)} 分钟。数据：Coze Studio 数据库 5 张表；画布见 canvases/，原始运行结果见 runs/。\n")
    (ROOT / "demo").mkdir(exist_ok=True)
    out_path = ROOT / "demo" / f"DEMO-{time.strftime('%Y%m%d-%H%M')}.md"
    out_path.write_text("\n".join(report), encoding="utf-8")
    print("report:", out_path)


if __name__ == "__main__":
    main()
