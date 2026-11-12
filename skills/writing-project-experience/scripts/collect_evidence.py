#!/usr/bin/env python3
"""从 git 仓库采集撰写「项目经历」所需的客观证据（只读，不修改仓库）。

用法示例：
  # 1) 不知道用户是谁：先列出所有贡献者，让用户指认
  python collect_evidence.py /path/to/repo

  # 2) 指定身份（可多次传入：姓名、邮箱、昵称的任意子串，大小写不敏感）
  python collect_evidence.py /path/to/repo --me "zhangsan" --me "zs@corp.com" --out evidence.json

  # 3) 同时拉取 GitHub 上的 PR / Issue / 评审（需要已登录的 gh CLI）
  python collect_evidence.py /path/to/repo --me zhangsan --gh-user zhangsan-gh

  # 4) monorepo：只看某个子目录；fork 项目：只看 fork 点之后的提交
  python collect_evidence.py /path/to/repo --me zhangsan --path services/chat
  python collect_evidence.py /path/to/repo --me zhangsan --range upstream/main..HEAD

输出：stdout 为 Markdown 摘要（给人和模型读），--out 为完整 JSON。
注意：脚本只给出「可统计的事实」。这些数字是归属判断的线索，不是结论，
更不能直接写进简历（见 SKILL.md）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

# ---------------------------------------------------------------- 噪声过滤
# 这些文件的改动行数不代表人的工作量：依赖锁、构建产物、生成代码、二进制、数据文件。
NOISE_PATTERNS = [
    r"(^|/)(node_modules|vendor|third_party|dist|build|out|target|\.venv|venv|__pycache__|\.next|\.nuxt|coverage)/",
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|Gemfile\.lock|uv\.lock)$",
    r"\.min\.(js|css)$",
    r"\.(map|snap|lock)$",
    r"(^|/)__snapshots__/",
    r"\.(png|jpe?g|gif|svg|ico|webp|pdf|woff2?|ttf|eot|mp4|mov|zip|gz|tar|jar|bin|onnx|pt|ckpt|safetensors)$",
    r"\.(csv|tsv|jsonl|parquet)$",
    r"(^|/)(generated|gen)/",
    r"\.pb\.go$",
    r"_pb2(_grpc)?\.py$",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.I)

LOW_INFO_RE = re.compile(
    r"^(fix|update|wip|tmp|temp|test|minor|change[sd]?|commit|bug ?fix|fix bug|\.+|"
    r"修改|更新|调整|提交|修复|优化|改动|fix\s*\d*)\s*[.!。]*$",
    re.I,
)
CONV_RE = re.compile(r"^(\w+)(?:\(([^)]+)\))?!?:\s")
REF_RE = re.compile(r"(?<![\w&/])#(\d+)\b|(?:^|\s)!(\d+)\b")
COAUTHOR_RE = re.compile(r"^co-authored-by:\s*(.+)$", re.I | re.M)
BOT_RE = re.compile(r"(\[bot\]|dependabot|renovate|github-actions|bot@)", re.I)

FMT = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%P%x1f%s%x1f%b%x1d"


# ---------------------------------------------------------------- 基础工具
def git(repo, *args, check=True, timeout=300):
    r = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.strip()[:300]}")
    return r.stdout


def is_noise(path):
    return bool(NOISE_RE.search(path))


def module_of(path):
    """取前两级目录作为「模块」；根目录文件归入 (root)。"""
    dirs = path.split("/")[:-1]
    return "/".join(dirs[:2]) if dirs else "(root)"


def make_matcher(needles):
    needles = [n.lower() for n in needles]

    def match(name, email):
        s = f"{name} <{email}>".lower()
        return any(n in s for n in needles)

    return match


def load_commits(repo, rev_args, paths=()):
    cmd = ["log", "--no-renames", "--numstat", f"--format={FMT}", *rev_args]
    if paths:
        cmd += ["--", *paths]
    out = git(repo, *cmd)
    commits = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\x1d")
        f = head.split("\x1f", 8)
        if len(f) < 9:
            continue
        h, an, ae, ad, cn, ce, parents, subj, body = f
        files = []
        for line in rest.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                a, d, p = parts
                files.append((p, 0 if a == "-" else int(a), 0 if d == "-" else int(d)))
        commits.append(dict(
            hash=h, author=an, email=ae, date=ad, committer=cn, committer_email=ce,
            is_merge=len(parents.split()) > 1, subject=subj.strip(), body=body.strip(), files=files,
        ))
    return commits


def month(c):
    return c["date"][:7]


# ---------------------------------------------------------------- 各项采集
def repo_meta(repo, commits):
    shallow = git(repo, "rev-parse", "--is-shallow-repository", check=False).strip() == "true"
    remote = git(repo, "remote", "get-url", "origin", check=False).strip()
    dates = sorted(c["date"] for c in commits)
    return dict(
        remote=remote or None,
        shallow_clone=shallow,
        total_commits=len(commits),
        non_merge_commits=sum(not c["is_merge"] for c in commits),
        merge_commits=sum(c["is_merge"] for c in commits),
        first_commit=dates[0] if dates else None,
        last_commit=dates[-1] if dates else None,
    )


def author_table(commits):
    t = {}
    for c in commits:
        key = c["email"].lower()
        a = t.setdefault(key, dict(
            names=set(), email=c["email"], commits=0, merges=0, ins=0, dele=0,
            first=c["date"], last=c["date"], is_bot=bool(BOT_RE.search(f"{c['author']} {c['email']}")),
        ))
        a["names"].add(c["author"])
        if c["is_merge"]:
            a["merges"] += 1
            continue
        a["first"] = min(a["first"], c["date"])
        a["last"] = max(a["last"], c["date"])
        a["commits"] += 1
        for p, i, d in c["files"]:
            if not is_noise(p):
                a["ins"] += i
                a["dele"] += d
    rows = []
    for a in t.values():
        a["names"] = sorted(a["names"])
        rows.append(a)
    rows.sort(key=lambda a: -a["commits"])
    # 可能的别名：规范化后的姓名、或邮箱本地部分相同，但邮箱不同
    def norm(x):
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", x.lower())

    groups = defaultdict(set)
    for a in rows:
        local = re.sub(r"^\d+\+", "", a["email"].split("@")[0])  # GitHub noreply: 12345+user@...
        for key in {norm(n) for n in a["names"]} | {norm(local)}:
            if len(key) >= 3:
                groups[key].add(a["email"])
    aliases = {k: sorted(e) for k, e in groups.items() if len(e) > 1}
    return rows, aliases


def my_activity(commits, is_me):
    mine = [c for c in commits if not c["is_merge"] and is_me(c["author"], c["email"])]
    merges_by_me = [c for c in commits if c["is_merge"] and is_me(c["committer"], c["committer_email"])]
    merged_others = [c for c in commits if not c["is_merge"]
                     and is_me(c["committer"], c["committer_email"]) and not is_me(c["author"], c["email"])]
    coauth_me = []
    my_coauthors = Counter()
    for c in commits:
        for m in COAUTHOR_RE.findall(c["body"]):
            if is_me(m, m) and not is_me(c["author"], c["email"]):
                coauth_me.append(c)
            elif is_me(c["author"], c["email"]) and not is_me(m, m):
                my_coauthors[m.strip()] += 1

    conv_types, conv_scopes, refs = Counter(), Counter(), set()
    low_info = 0
    for c in mine:
        m = CONV_RE.match(c["subject"])
        if m:
            conv_types[m.group(1).lower()] += 1
            if m.group(2):
                conv_scopes[m.group(2)] += 1
        if LOW_INFO_RE.match(c["subject"]) or len(c["subject"]) <= 6:
            low_info += 1
        for mm in REF_RE.finditer(c["subject"] + "\n" + c["body"]):
            refs.add(mm.group(1) or mm.group(2))

    mod = defaultdict(lambda: dict(mine=0, total=0, my_commits=0))
    files = Counter()
    for c in commits:
        if c["is_merge"]:
            continue
        me = is_me(c["author"], c["email"])
        touched = set()
        for p, i, d in c["files"]:
            if is_noise(p):
                continue
            m = module_of(p)
            mod[m]["total"] += i + d
            if me:
                mod[m]["mine"] += i + d
                files[p] += i + d
                touched.add(m)
        for m in touched:
            mod[m]["my_commits"] += 1
    modules = []
    for name, v in mod.items():
        if v["mine"] > 0:
            modules.append(dict(module=name, **v, share=round(v["mine"] / v["total"], 2) if v["total"] else 0))
    modules.sort(key=lambda m: -m["mine"])

    return dict(
        my_commit_count=len(mine),
        low_info_subjects=low_info,
        first=min((c["date"] for c in mine), default=None),
        last=max((c["date"] for c in mine), default=None),
        monthly=dict(sorted(Counter(month(c) for c in mine).items())),
        conventional_types=dict(conv_types.most_common()),
        conventional_scopes=dict(conv_scopes.most_common(15)),
        referenced_pr_issue_numbers=sorted(refs, key=int),
        merge_commits_by_me=len(merges_by_me),
        others_commits_committed_by_me=len(merged_others),
        commits_where_i_am_coauthor=len(coauth_me),
        my_coauthors=dict(my_coauthors.most_common(10)),
        top_modules=modules[:15],
        top_files=[dict(path=p, my_changed_lines=n) for p, n in files.most_common(25)],
        commits=[dict(hash=c["hash"][:10], date=c["date"][:10], subject=c["subject"][:120],
                      files=len([1 for p, _, _ in c["files"] if not is_noise(p)]),
                      lines=sum(i + d for p, i, d in c["files"] if not is_noise(p)))
                 for c in mine],
    )


def blame_survival(repo, paths, is_me, limit, rev="HEAD"):
    """对改动最多的若干文件做 blame，统计 HEAD 中仍由本人写下的行占比。"""
    tracked = set(git(repo, "ls-files", check=False).splitlines())
    rows = []
    for p in paths:
        if len(rows) >= limit:
            break
        if p not in tracked:
            continue
        try:
            out = git(repo, "blame", "-w", "-M", "--line-porcelain", rev, "--", p, timeout=60)
        except Exception:
            continue
        cur, total, mine = None, 0, 0
        for line in out.splitlines():
            if line.startswith("author "):
                cur = line[7:]
            elif line.startswith("author-mail "):
                total += 1
                if is_me(cur or "", line[12:].strip("<>")):
                    mine += 1
        if total:
            rows.append(dict(path=p, lines=total, mine=mine, share=round(mine / total, 2)))
    return rows


def detect_stack(repo, paths=()):
    files = git(repo, "ls-files", *(["--", *paths] if paths else []), check=False).splitlines()
    ext = Counter(os.path.splitext(f)[1].lower() for f in files if not is_noise(f) and "." in os.path.basename(f))
    manifests = [f for f in files if os.path.basename(f) in (
        "package.json", "requirements.txt", "pyproject.toml", "go.mod", "pom.xml", "Cargo.toml", "Gemfile")
        and "node_modules" not in f][:15]
    deps = {}
    for m in manifests:
        full = os.path.join(repo, m)
        try:
            txt = open(full, encoding="utf-8", errors="replace").read()
            b = os.path.basename(m)
            names = []
            if b == "package.json":
                j = json.loads(txt)
                names = list(j.get("dependencies", {})) + [f"{k} (dev)" for k in j.get("devDependencies", {})]
            elif b == "requirements.txt":
                names = [re.split(r"[=<>~!\[; ]", l.strip(), 1)[0] for l in txt.splitlines()
                         if l.strip() and not l.startswith(("#", "-"))]
            elif b in ("pyproject.toml", "Cargo.toml") and tomllib:
                t = tomllib.loads(txt)
                if b == "pyproject.toml":
                    names = [re.split(r"[=<>~!\[; ]", d, 1)[0] for d in t.get("project", {}).get("dependencies", [])]
                    names += list(t.get("tool", {}).get("poetry", {}).get("dependencies", {}))
                else:
                    names = list(t.get("dependencies", {}))
            elif b == "go.mod":
                names = re.findall(r"^\s*([\w./\-]+)\s+v[\d.]+", txt, re.M)
            elif b == "pom.xml":
                names = re.findall(r"<artifactId>([^<]+)</artifactId>", txt)
            deps[m] = sorted(set(names))[:80]
        except Exception as e:  # 解析失败不致命
            deps[m] = [f"(解析失败: {e.__class__.__name__})"]
    notable = [f for f in files if re.search(
        r"(^|/)(README[^/]*|CHANGELOG[^/]*|CONTRIBUTING[^/]*|Dockerfile[^/]*|docker-compose[^/]*|Makefile|"
        r"\.github/workflows/[^/]+|\.gitlab-ci\.yml|docs?/[^/]+\.(md|rst)|ADR[^/]*|rfcs?/[^/]+)$", f, re.I)][:40]
    readme = next((f for f in files if re.match(r"(?i)readme(\.\w+)?$", os.path.basename(f)) and "/" not in f), None)
    snippet = None
    if readme:
        try:
            snippet = open(os.path.join(repo, readme), encoding="utf-8", errors="replace").read()[:600]
        except Exception:
            pass
    test_files = sum(1 for f in files if re.search(r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec)\.", f))
    return dict(
        extension_counts=dict(ext.most_common(10)), manifests=deps, notable_files=notable,
        readme_path=readme, readme_head=snippet, test_file_count=test_files, tracked_files=len(files),
    )


def gh_json(repo, args):
    try:
        r = subprocess.run(["gh", *args], cwd=repo, capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        return None, "未安装 gh CLI"
    except subprocess.TimeoutExpired:
        return None, "gh 调用超时"
    if r.returncode != 0:
        return None, (r.stderr.strip() or "gh 调用失败")[:200]
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError:
        return None, "gh 输出无法解析"


def github_activity(repo, user):
    res, errs = {}, {}
    jobs = {
        "authored_prs": ["pr", "list", "--state", "all", "--author", user, "--limit", "200", "--json",
                         "number,title,state,createdAt,mergedAt,additions,deletions,labels,url,body"],
        "authored_issues": ["issue", "list", "--state", "all", "--author", user, "--limit", "100", "--json",
                            "number,title,state,createdAt,labels,url"],
        "reviewed_prs": ["pr", "list", "--state", "all", "--search", f"reviewed-by:{user} -author:{user}",
                         "--limit", "100", "--json", "number,title,author,state,url"],
    }
    for k, a in jobs.items():
        data, err = gh_json(repo, a)
        if data is not None:
            for item in data:
                if "body" in item and item["body"]:
                    item["body"] = item["body"][:600]
                if "labels" in item:
                    item["labels"] = [l.get("name") for l in item["labels"]]
            res[k] = data
        else:
            errs[k] = err
    return res, errs


# ---------------------------------------------------------------- 摘要渲染
def render(data):
    L = []
    m = data["meta"]
    L += ["# 仓库证据摘要", "",
          f"- 远程：{m['remote'] or '（无 origin）'}",
          f"- 提交：共 {m['total_commits']}（非合并 {m['non_merge_commits']}，合并 {m['merge_commits']}），"
          f"{(m['first_commit'] or '')[:10]} → {(m['last_commit'] or '')[:10]}",
          f"- 浅克隆：{'**是**——历史不完整，先 `git fetch --unshallow`，否则归属统计不可信' if m['shallow_clone'] else '否'}"]
    if data["warnings"]:
        L += ["", "## ⚠ 需要注意"] + [f"- {w}" for w in data["warnings"]]

    L += ["", "## 贡献者（已排除合并提交；行数已过滤锁文件/构建产物/生成代码/数据文件）", "",
          "| 姓名 | 邮箱 | 提交 | 有效增/删行 | 首次 → 最近 | 备注 |", "|---|---|---|---|---|---|"]
    for a in data["authors"][:25]:
        L.append(f"| {' / '.join(a['names'])} | {a['email']} | {a['commits']} | +{a['ins']}/-{a['dele']} | "
                 f"{a['first'][:10]} → {a['last'][:10]} | {'bot' if a['is_bot'] else ''} |")
    if data["possible_aliases"]:
        L += ["", "可能是同一个人的多个邮箱（请向用户确认）：",
              *[f"- {n}: {', '.join(e)}" for n, e in data["possible_aliases"].items()]]

    st = data["stack"]
    L += ["", "## 技术栈线索（依赖存在 ≠ 本人使用过）", "",
          f"- 主要文件类型：{', '.join(f'{k}×{v}' for k, v in st['extension_counts'].items())}",
          f"- 测试文件数：{st['test_file_count']}；受版本控制文件数：{st['tracked_files']}"]
    for p, d in st["manifests"].items():
        L.append(f"- `{p}`：{', '.join(d[:40])}{' …' if len(d) > 40 else ''}")
    if st["notable_files"]:
        L.append(f"- 值得阅读的文件：{', '.join('`'+f+'`' for f in st['notable_files'][:20])}")
    if st["readme_head"]:
        L += ["", "README 开头：", "```", st["readme_head"].strip(), "```"]

    me = data.get("me")
    if not me:
        L += ["", "## 下一步", "尚未指定身份。请让用户从上表指认自己的姓名/邮箱，然后用 `--me` 重新运行。"]
        return "\n".join(L)

    L += ["", f"## 本人活动（匹配：{', '.join(data['me_needles'])}）", "",
          f"- 非合并提交：{me['my_commit_count']}（占项目非合并提交 "
          f"{round(100 * me['my_commit_count'] / max(m['non_merge_commits'], 1))}%），"
          f"{(me['first'] or '')[:10]} → {(me['last'] or '')[:10]}",
          f"- 信息量过低的提交信息（如 fix/update/修改）：{me['low_info_subjects']} 条"
          + ("——提交信息不可靠，需靠 diff 与用户补充" if me["my_commit_count"] and
             me["low_info_subjects"] / me["my_commit_count"] > 0.4 else ""),
          f"- 作为 committer 合入他人提交：{me['others_commits_committed_by_me']}；合并提交：{me['merge_commits_by_me']}；"
          f"在他人提交中署名 Co-authored-by：{me['commits_where_i_am_coauthor']}"]
    if me["my_coauthors"]:
        L.append(f"- 我的提交中的共同作者：{', '.join(f'{k}×{v}' for k, v in me['my_coauthors'].items())}")
    if me["conventional_types"]:
        L.append(f"- 提交类型：{', '.join(f'{k}×{v}' for k, v in me['conventional_types'].items())}")
    if me["conventional_scopes"]:
        L.append(f"- 提交 scope：{', '.join(f'{k}×{v}' for k, v in me['conventional_scopes'].items())}")
    if me["referenced_pr_issue_numbers"]:
        L.append(f"- 提交中引用的 PR/Issue 编号：{', '.join('#'+n for n in me['referenced_pr_issue_numbers'][:40])}")
    L += ["- 月度提交分布：" + (", ".join(f"{k}:{v}" for k, v in me["monthly"].items()) or "无")]

    L += ["", "### 模块归属线索（我的有效改动行 / 该模块所有人的改动行）", "",
          "| 模块 | 我的改动行 | 模块总改动行 | 占比 | 涉及我的提交数 |", "|---|---|---|---|---|"]
    for x in me["top_modules"]:
        L.append(f"| `{x['module']}` | {x['mine']} | {x['total']} | {int(x['share']*100)}% | {x['my_commits']} |")

    if data.get("blame"):
        L += ["", "### HEAD 中仍存活的代码行（blame，忽略空白，识别移动）", "",
              "| 文件 | 行数 | 我的行数 | 占比 |", "|---|---|---|---|"]
        for b in data["blame"]:
            L.append(f"| `{b['path']}` | {b['lines']} | {b['mine']} | {int(b['share']*100)}% |")
        tot = sum(b["lines"] for b in data["blame"])
        mine = sum(b["mine"] for b in data["blame"])
        if tot:
            L.append(f"\n合计：{mine}/{tot} 行（{int(100*mine/tot)}%）。仅覆盖我改动最多的这些文件，不代表整个项目。")

    gh = data.get("github")
    if gh is not None:
        L += ["", "## GitHub 活动"]
        for k, label in (("authored_prs", "我提交的 PR"), ("authored_issues", "我提出的 Issue"),
                         ("reviewed_prs", "我评审过的他人 PR")):
            items = gh["data"].get(k)
            if items is None:
                L.append(f"- {label}：获取失败（{gh['errors'].get(k)}）")
                continue
            L.append(f"- {label}：{len(items)} 条")
            for it in items[:15]:
                extra = ""
                if "additions" in it:
                    extra = f" (+{it['additions']}/-{it['deletions']}, {it['state']})"
                L.append(f"  - #{it['number']} {it['title'][:90]}{extra}")
    elif data.get("gh_hint"):
        L += ["", "## GitHub 活动", f"- {data['gh_hint']}"]

    L += ["", "## 下一步", "- 对 `top_modules`/`top_files` 中的前几项，用 `git show <hash>` / `git log -p` 阅读真实 diff；",
          "- 提交信息与 diff 不一致时以 diff 为准；",
          "- 完整提交清单见 JSON 的 `me.commits`。"]
    return "\n".join(L)


# ---------------------------------------------------------------- 主程序
def main():
    ap = argparse.ArgumentParser(description="采集项目经历所需的 git 证据（只读）")
    ap.add_argument("repo", help="本地仓库路径")
    ap.add_argument("--me", action="append", default=[], help="本人的姓名/邮箱/昵称片段，可多次传入")
    ap.add_argument("--out", help="完整 JSON 输出路径")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--path", action="append", default=[], help="只统计这些子目录（monorepo），可多次传入")
    ap.add_argument("--range", dest="rev_range", help="提交范围，如 upstream/main..HEAD（fork 项目只看 fork 点之后）")
    ap.add_argument("--all-branches", action="store_true", help="统计所有分支（默认仅当前 HEAD）")
    ap.add_argument("--blame-files", type=int, default=25, help="最多对多少个文件做 blame；0 表示跳过")
    ap.add_argument("--gh-user", help="GitHub 用户名；提供后用 gh CLI 拉取 PR/Issue/评审")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(os.path.join(repo, ".git")) and git(repo, "rev-parse", "--git-dir", check=False) == "":
        sys.exit(f"{repo} 不是 git 仓库")

    rev_args = ["--all"] if args.all_branches else []
    if args.rev_range:
        rev_args.append(args.rev_range)
    if args.since:
        rev_args.append(f"--since={args.since}")
    if args.until:
        rev_args.append(f"--until={args.until}")
    commits = load_commits(repo, rev_args, args.path)
    if not commits:
        sys.exit("没有读取到任何提交（范围过滤过严，或仓库为空）")

    authors, aliases = author_table(commits)
    data = dict(meta=repo_meta(repo, commits), authors=authors, possible_aliases=aliases,
                stack=detect_stack(repo, args.path), warnings=[], me_needles=args.me)
    if data["meta"]["shallow_clone"]:
        data["warnings"].append("这是浅克隆，历史被截断，所有统计都偏低。")
    first = commits[-1]
    if len(commits) > 1 and not first["is_merge"] and sum(i for _, i, _ in first["files"]) > 5000:
        data["warnings"].append(
            f"最早一次提交（{first['hash'][:8]}，{first['author']}）一次性引入大量代码，可能是历史迁移/导入；"
            "此前的真实作者无法从 git 判断，需向用户确认。")
    if sum(a["is_bot"] for a in authors):
        data["warnings"].append("存在 bot 提交（依赖更新/CI 等），已计入贡献者表但不应算作任何人的工作。")

    if args.me:
        is_me = make_matcher(args.me)
        data["me"] = my_activity(commits, is_me)
        if data["me"]["my_commit_count"] == 0:
            data["warnings"].append("按给定身份匹配到 0 条提交：检查拼写，或对方可能使用了其他邮箱（见上方“可能的别名”）。")
        if args.blame_files > 0 and data["me"]["top_files"]:
            data["blame"] = blame_survival(repo, [f["path"] for f in data["me"]["top_files"]],
                                           is_me, args.blame_files)
        if args.gh_user:
            d, e = github_activity(repo, args.gh_user)
            data["github"] = dict(data=d, errors=e)
        elif data["meta"]["remote"] and "github.com" in (data["meta"]["remote"] or ""):
            data["gh_hint"] = "远程为 GitHub。如需 PR/Issue/评审证据，请提供 GitHub 用户名并加 `--gh-user`。"
        else:
            data["gh_hint"] = "远程不是 GitHub 或无远程：PR/Issue 无法自动获取，请向用户索取导出内容或链接，否则标注为缺失。"

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=list)
    print(render(data))


if __name__ == "__main__":
    main()
