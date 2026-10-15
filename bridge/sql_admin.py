"""Run a fixed maintenance SQL statement through a tiny single-node workflow (Coze has no raw SQL API).

usage: python sql_admin.py <table> "<sql>"
The workflow is recreated on each call (schema differs per statement); results are printed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze, save_account  # noqa: E402
import canvas as cv  # noqa: E402
from workflow_api import create_workflow, save_workflow, test_run, summarize_process  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS = json.loads((ROOT / "radar_ids.json").read_text(encoding="utf-8"))


def main():
    table, sql = sys.argv[1], sys.argv[2]
    c = Coze()
    c.login()
    wid = c.acct.get("wf_admin_id") or create_workflow(c, "radar_sql_admin", "维护用：单条 SQL")
    c.acct["wf_admin_id"] = wid
    save_account(c.acct)
    sid = cv.new_id()
    nodes = [cv.entry([cv.out("noop")]),
             cv.custom_sql(sid, "SQL", IDS["tables_online"][table], sql, [], [], pos=(400, 0)),
             cv.exit_node([cv.param("rowNum", cv.ref(sid, "rowNum", "integer")),
                           cv.param("rows", cv.ref(sid, "outputList", "list<object>", schema={"type": "object", "schema": []}))])]
    save_workflow(c, wid, cv.canvas(nodes, [cv.edge(cv.ENTRY_ID, sid), cv.edge(sid, cv.EXIT_ID)]))
    d = test_run(c, wid, {"noop": "1"}, bot_id=c.acct.get("bot_id"), timeout_s=120)
    print(summarize_process(d)[-1500:])


if __name__ == "__main__":
    main()
