"""M2 smoke test: Entry -> LLM(JSON) -> Exit. Verifies save/test_run and that the OpenAI key works."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze, save_account  # noqa: E402
import canvas as cv  # noqa: E402
from workflow_api import create_workflow, save_workflow, test_run, summarize_process  # noqa: E402


def build() -> str:
    llm_id = cv.new_id()
    nodes = [
        cv.entry([cv.out("topic", "string")]),
        cv.llm(llm_id, "判定", prompt="主题：{{topic}}\n请输出 JSON：{\"decision\": true/false, \"reason\": \"一句话\"}",
               system_prompt="你是严谨的研究助理。只输出 JSON。",
               params=[cv.param("topic", cv.ref(cv.ENTRY_ID, "topic"))],
               outputs=[cv.out("decision", "boolean"), cv.out("reason", "string")], json_mode=True, max_tokens=512),
        cv.exit_node([cv.param("decision", cv.ref(llm_id, "decision", "boolean")),
                      cv.param("reason", cv.ref(llm_id, "reason", "string"))]),
    ]
    edges = [cv.edge(cv.ENTRY_ID, llm_id), cv.edge(llm_id, cv.EXIT_ID)]
    return cv.canvas(nodes, edges)


def main():
    c = Coze()
    c.login()
    wid = c.acct.get("wf_smoke_id") or create_workflow(c, "radar_smoke", "M2 smoke: entry-llm-exit")
    c.acct["wf_smoke_id"] = wid
    save_account(c.acct)
    print("workflow_id", wid)
    r = save_workflow(c, wid, build())
    print("save ->", r.get("code"), r.get("msg"))
    d = test_run(c, wid, {"topic": "decentralized multi-agent self-healing"})
    print(summarize_process(d))


if __name__ == "__main__":
    main()
