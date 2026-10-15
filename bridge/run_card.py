"""Run one experiment card with Claude Code inside the target repo, on a throwaway branch.

usage: python run_card.py --card cards/CARD-<id>.md --repo <path> [--budget-min 45] [--dry-run]

Guardrails: new branch radar/card-<id> from the current HEAD; whitelisted tools only; hard timeout;
changes are committed on that branch and the original branch is checked out again afterwards.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GUARDRAILS = """
你在一个真实项目仓库的实验分支上工作。规则：
- 这是一次最小规模实验，目标是验证下面实验卡里的假设，不是把功能做完。
- 只改实验卡"改动范围"里提到的目录，最多 3 个文件；不要改 .env、Key 文件、数据库迁移、依赖清单；不要 git push、不要切分支、不要 merge。
- 评测命令必须原样使用实验卡里的命令，先跑基线并记录输出，改完再跑一次。命令跑不起来就停下，verdict 填 blocked。
- 结束前在仓库根目录写 radar_result.json，字段：card_id, verdict(win|neutral|lose|blocked), metric, baseline, after, changed_files(list), notes。
- 最后用 git add -A && git commit -m "radar: <卡标题>" 提交到当前分支。
- 预算 {budget} 分钟，接近预算就收尾写结果。
"""

ALLOWED = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(git:*)", "Bash(python:*)", "Bash(pytest:*)", "Bash(uv:*)",
           "Bash(..\\.venv\\Scripts\\python.exe:*)", "Bash(.venv\\Scripts\\python.exe:*)", "Bash(.venv/Scripts/python.exe:*)",
           "Bash(../.venv/Scripts/python.exe:*)", "Bash(cd:*)", "Bash(ls:*)", "Bash(dir:*)", "Bash(cat:*)", "Bash(type:*)",
           "Bash(Set-Location:*)", "Bash(Get-Content:*)", "Bash(timeout:*)"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--budget-min", type=int, default=45)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    card_path, repo = Path(a.card), Path(a.repo)
    card = card_path.read_text(encoding="utf-8")
    card_id = re.search(r"CARD-(\d+)", card_path.name).group(1)
    orig_branch = git(repo, "branch", "--show-current")
    dirty = [l for l in git(repo, "status", "--short").splitlines() if not l.startswith("??")]
    if dirty:
        sys.exit(f"repo has uncommitted tracked changes, refusing to run:\n" + "\n".join(dirty))
    branch = f"radar/card-{card_id}"
    prompt = GUARDRAILS.format(budget=a.budget_min) + "\n\n===== 实验卡 =====\n" + card
    cmd = ["claude", "-p", "--output-format", "json", "--max-turns", "80", "--allowedTools", ",".join(ALLOWED)]
    print(f"branch {branch} from {orig_branch}@{git(repo, 'rev-parse', '--short', 'HEAD')}; budget {a.budget_min} min")
    if a.dry_run:
        print(prompt[:1500])
        return
    if git(repo, "branch", "--list", branch):
        git(repo, "branch", "-D", branch)
    git(repo, "checkout", "-b", branch)
    t0 = time.time()
    result = {"card_id": card_id, "branch": branch, "orig_branch": orig_branch, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        p = subprocess.run(cmd, cwd=repo, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=a.budget_min * 60 + 180, shell=True)
        result["exit_code"] = p.returncode
        result["stdout_tail"] = p.stdout[-4000:]
        result["stderr_tail"] = p.stderr[-2000:]
        try:
            j = json.loads(p.stdout)
            result["cc"] = {k: j.get(k) for k in ("result", "total_cost_usd", "duration_ms", "num_turns", "is_error", "subtype")}
        except Exception:
            pass
    except subprocess.TimeoutExpired as e:
        result["exit_code"] = "timeout"
        result["stdout_tail"] = (e.stdout or "")[-4000:] if isinstance(e.stdout, str) else ""
    result["elapsed_min"] = round((time.time() - t0) / 60, 1)
    rr = repo / "radar_result.json"
    if rr.exists():
        try:
            result["radar_result"] = json.loads(rr.read_text(encoding="utf-8"))
        except Exception as ex:
            result["radar_result_error"] = str(ex)
    # make sure everything is committed on the experiment branch, then go back
    # only tracked modifications + the result file; never sweep the user's untracked files into the branch
    if [l for l in git(repo, "status", "--short").splitlines() if not l.startswith("??")] or rr.exists():
        git(repo, "add", "-u")
        if rr.exists():
            git(repo, "add", "radar_result.json")
        subprocess.run(["git", "commit", "-q", "-m", f"radar: card {card_id} (auto-commit of experiment leftovers)\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"],
                       cwd=repo, capture_output=True, text=True)
    result["diff_stat"] = git(repo, "diff", "--stat", f"{orig_branch}...{branch}")
    result["commits"] = git(repo, "log", "--oneline", f"{orig_branch}..{branch}")
    git(repo, "checkout", orig_branch)
    (ROOT / "runs").mkdir(exist_ok=True)
    out = ROOT / "runs" / f"card-{card_id}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("exit_code", "elapsed_min", "cc", "radar_result", "commits")}, ensure_ascii=False, indent=1))
    print("diff:", result["diff_stat"][-800:])
    print("saved", out)


if __name__ == "__main__":
    main()
