"""Report a finished card run: runs WF-D (updates DB, produces an experience line) and stores the line in KB-radar.

usage: python report_result.py --run runs/card-<id>.json --project ShopSteward
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze  # noqa: E402
from workflow_api import test_run, summarize_process  # noqa: E402
from wfd_build import ensure  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS_FILE = ROOT / "radar_ids.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    run = json.loads(Path(a.run).read_text(encoding="utf-8"))
    rr = run.get("radar_result") or {}
    verdict = rr.get("verdict") or ("blocked" if run.get("exit_code") in ("timeout", None) else "blocked")
    c = Coze()
    c.login()
    wid = ensure(c)
    inputs = {"card_id": str(run["card_id"]), "verdict": str(verdict), "metric": str(rr.get("metric") or ""),
              "baseline": str(rr.get("baseline") or ""), "after": str(rr.get("after") or ""),
              "notes": str(rr.get("notes") or run.get("stderr_tail") or "")[:1500],
              "changed_files": json.dumps(rr.get("changed_files") or [], ensure_ascii=False),
              "elapsed_min": str(run.get("elapsed_min") or ""), "project_name": a.project}
    d = test_run(c, wid, inputs, bot_id=c.acct.get("bot_id"), timeout_s=300)
    print(summarize_process(d)[:1500])
    end = [n for n in d.get("nodeResults", []) if n.get("NodeType") == "End"]
    if not end or d.get("executeStatus") != 2:
        sys.exit("WF-D failed")
    out = json.loads(end[0]["output"])
    exp = out.get("experience") or ""
    if exp.strip().startswith("{"):
        try:
            exp = json.loads(exp).get("experience") or exp
        except Exception:
            pass
    print("experience:", exp)
    ids = json.loads(IDS_FILE.read_text(encoding="utf-8"))
    body = c.post("/api/knowledge/document/create", {
        "dataset_id": ids["kb"], "format_type": 0,
        "document_bases": [{"name": f"experience:{a.project}:card-{run['card_id']}",
                            "source_info": {"document_source": 2, "custom_content": exp}}],
        "chunk_strategy": {"chunk_type": 1, "separator": "\n\n", "max_tokens": 600, "overlap": 0,
                           "remove_extra_spaces": False, "remove_urls_emails": False}})
    infos = body.get("document_infos") or body["data"]["document_infos"]
    ids.setdefault("experiences", {})[str(run["card_id"])] = str(infos[0]["document_id"])
    IDS_FILE.write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    print("stored experience doc", infos[0]["document_id"])


if __name__ == "__main__":
    main()
