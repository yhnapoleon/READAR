"""Push a project's docs into KB-radar and build the docs_text that WF-A reads.

usage: python push_project.py <project_name> <repo_path> [--files README.md HANDOVER.md ...]
Each file becomes one knowledge document named "project:<name>:<relpath>" (custom text, <=30k chars).
Doc ids are recorded in radar_ids.json under projects.<name>.kb_docs. Prints the path of the docs_text file.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from coze_client import Coze  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDS_FILE = ROOT / "radar_ids.json"
MAX_DOC_CHARS = 30000
DEFAULT_FILES = ["README.md", "HANDOVER.md", "agent/README.md", "docs/architecture.md"]


def read_clip(p: Path, limit: int) -> str:
    t = p.read_text(encoding="utf-8", errors="replace")
    return t if len(t) <= limit else t[:limit] + "\n\n[... truncated ...]"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    name, repo = args[0], Path(args[1])
    files = sys.argv[sys.argv.index("--files") + 1:] if "--files" in sys.argv else DEFAULT_FILES
    ids = json.loads(IDS_FILE.read_text(encoding="utf-8"))
    proj = ids.setdefault("projects", {}).setdefault(name, {"kb_docs": {}})
    c = Coze()
    c.login()
    docs_text_parts = []
    for rel in files:
        p = repo / rel
        if not p.exists():
            print("skip (missing):", rel)
            continue
        content = read_clip(p, MAX_DOC_CHARS)
        docs_text_parts.append(f"===== {rel} =====\n{content}")
        doc_name = f"project:{name}:{rel}"
        if rel in proj["kb_docs"]:
            print("kb doc exists:", rel, proj["kb_docs"][rel])
            continue
        body = c.post("/api/knowledge/document/create", {
            "dataset_id": ids["kb"], "format_type": 0,
            "document_bases": [{"name": doc_name, "source_info": {"document_source": 2, "custom_content": content}}],
            "chunk_strategy": {"chunk_type": 1, "separator": "\n\n", "max_tokens": 600, "overlap": 10,
                               "remove_extra_spaces": False, "remove_urls_emails": False}})
        infos = body.get("document_infos") or body["data"]["document_infos"]
        doc_id = str(infos[0]["document_id"])
        proj["kb_docs"][rel] = doc_id
        IDS_FILE.write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
        print("uploaded:", rel, doc_id, f"{len(content)} chars")
    # wait for indexing
    pending = list(proj["kb_docs"].values())
    for _ in range(60):
        p = c.post("/api/knowledge/document/progress/get", {"document_ids": pending})
        sts = {d["document_id"]: (d.get("status"), d.get("progress")) for d in p.get("data", [])}
        if all(s[0] == 1 for s in sts.values()):
            break
        time.sleep(3)
    print("index status:", sts)
    out = ROOT / "projects" / f"{name}.docs.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n\n".join(docs_text_parts), encoding="utf-8")
    print("docs_text:", out, f"{sum(len(x) for x in docs_text_parts)} chars")


if __name__ == "__main__":
    main()
