"""Export experiment cards (status=new by default) from Coze DB to cards/CARD-<id>.md."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS = json.loads((ROOT / "radar_ids.json").read_text(encoding="utf-8"))

TEMPLATE = """# CARD-{id}: {title}

来源: arXiv:{arxiv_id} | 代码: {code_url} | 可行性: {score} | 机制: {mech_name}
痛点: {pain}

## 假设
{hypothesis}

## 改动范围
{change_scope}

## 步骤
{steps}

## 评测方法
{eval_method}

## 预算与停止
最多 {budget_min} 分钟。出现以下情况停止并标 blocked: 需要训练 / 需要外部数据 / 评测跑不起来 / 需要改接口。
只在当前分支工作，不要合并、不要推送、不要改 .env 或 Key 文件。

## 回写
完成后在仓库根目录写 radar_result.json: {{"card_id": "{id}", "verdict": "win|neutral|lose|blocked", "metric": "...", "baseline": "...", "after": "...", "notes": "..."}}
"""


def fetch_all(c: Coze, table: str, limit: int = 100) -> list[dict]:
    rows, offset = [], 0
    while True:
        b = c.post("/api/memory/database/list_records", {
            "database_id": IDS["tables"][table], "table_type": 1, "limit": limit, "offset": offset, "bot_id": c.acct["bot_id"]})
        data = b.get("data") or []
        rows.extend(data)
        if not b.get("HasMore") or not data:
            return rows
        offset += limit


def main():
    status = sys.argv[1] if len(sys.argv) > 1 else "new"
    c = Coze()
    c.login()
    mechs = {m["bstudio_id"]: m for m in fetch_all(c, "mechanism")}
    pains = {p["bstudio_id"]: p for p in fetch_all(c, "pain_point")}
    cards = [r for r in fetch_all(c, "experiment_card") if status == "all" or r.get("status") == status]
    (ROOT / "cards").mkdir(exist_ok=True)
    for r in cards:
        m = mechs.get(str(r.get("mechanism_id")), {})
        feas = {}
        try:
            feas = json.loads(m.get("feasibility_json") or "{}")
        except Exception:
            pass
        pain = pains.get(str(m.get("pain_point_id")), {}).get("description", "（未关联）")
        md = TEMPLATE.format(id=r["bstudio_id"], title=r.get("title", ""), arxiv_id=m.get("arxiv_id", "?"),
                             code_url=(feas.get("code_url") or m.get("code_url") or "无"), score=m.get("feasibility_score", "?"),
                             mech_name=m.get("name", "?"), pain=pain, hypothesis=r.get("hypothesis", ""),
                             change_scope=r.get("change_scope", ""), steps=r.get("steps", ""), eval_method=r.get("eval_method", ""),
                             budget_min=r.get("budget_min", 60))
        path = ROOT / "cards" / f"CARD-{r['bstudio_id']}.md"
        path.write_text(md, encoding="utf-8")
        print(f"{r['bstudio_id']}  [{r.get('status')}]  {r.get('title')}  -> {path.name}")
    print(f"{len(cards)} card(s) exported")


if __name__ == "__main__":
    main()
