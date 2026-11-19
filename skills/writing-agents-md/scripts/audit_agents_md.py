#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""体检 AGENTS.md / CLAUDE.md 等 Agent 指令文件。

只读、零依赖。输出的是**初筛线索**，不是判决：每一条都需要读原文再判断。
它回答的是"这个文件里哪些地方在浪费指令预算"，不是"这个文件好不好"。

用法:
    python3 audit_agents_md.py                     # 自动发现仓库内的指令文件
    python3 audit_agents_md.py AGENTS.md           # 只体检指定文件
    python3 audit_agents_md.py --repo other/path   # 指定仓库根目录
    python3 audit_agents_md.py --json              # 机器可读
    python3 audit_agents_md.py --budget-lines 300 --budget-tokens 2500
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------- 常量

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "vendor",
    ".next", "target", "__pycache__", ".idea", ".cache", "coverage",
    ".pytest_cache", ".mypy_cache", ".turbo", "out",
}

ROOT_NAMES = ["AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md"]
PRIVATE_NAMES = {"CLAUDE.local.md"}

EXTRA_FILES = [".github/copilot-instructions.md", ".cursorrules"]
RULES_DIRS = [".cursor/rules", ".claude/rules"]

MAX_NESTED_DEPTH = 4

BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
IMPERATIVE_RE = re.compile(
    r"必须|不要|避免|切勿|请勿|禁止|务必|一律|统一|严禁|不允许|请|总是|永远|"
    r"\bnever\b|\balways\b|\bmust\b|\bshould\b|\bavoid\b|\bdo not\b|\bdon't\b",
    re.I,
)
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

EMPHASIS_RE = re.compile(
    r"IMPORTANT|YOU MUST|CRITICAL|NEVER|ALWAYS|必须|务必|一定|绝对|严禁|不允许|禁止",
)

NEGATION_RE = re.compile(
    r"(?<!要)不要|禁止|避免|切勿|请勿|勿修改|勿改|别(?:用|提|改|手|直|乱|忘|写|碰)|"
    r"严禁|不允许|\bnever\b|\bdon't\b|\bdo not\b|\bavoid\b",
    re.I,
)
# 同一行里已经给出了正面动作（在禁令之前），说明"不要"只是兜底
POSITIVE_ACTION_RE = re.compile(
    r"先读|先看|参考|详见|见\s*`|按照|遵循|建议|应该|应当|改用|而是|写成|改为|"
    r"降低|提高|采用|使用|保持|只在|仅当|"
    r"\bread\b|\bsee\s*`|\brun\b|\buse\b|\binstead\b|\bprefer\b",
    re.I,
)
ALTERNATIVE_RE = re.compile(
    r"改用|而是|应该|应当|请用|请使用|建议|改为|换用|写成|统一用|只在|仅当|"
    r"instead|\bprefer\b|，用|，改|, use|用\s*`|\buse\s*`|\brun\s*`|->|→|"
    r"改\s*[A-Za-z0-9_./`]",
    re.I,
)

VAGUE_RE = re.compile(
    r"注意|尽量|适当|合理|高效|良好|必要时|尽可能|优化一下|灵活|酌情|最好|"
    r"避免不必要|小心|谨慎|注意性能|注意效率|"
    r"\bcareful(ly)?\b|\bappropriate(ly)?\b|\btry to\b|\bproperly\b|\bas needed\b|\bminimi[sz]e\b",
    re.I,
)

# 只匹配"重申格式规则"的表述；"提交前跑 lint --fix" 这类执行指令不该命中
TOOLING_RE = re.compile(
    r"缩进|两个空格|2 个空格|4 个空格|tab\s*(?:宽度|用)|分号结尾|不加分号|单引号|双引号|"
    r"引号风格|命名(?:风格|规范|用)|prettier\s*配置|eslint\s*配置|gofmt|rustfmt",
    re.I,
)

AT_REF_RE = re.compile(
    r"(?:^|[\s(（])@([A-Za-z0-9_./\\-]+\.(?:md|mdc|txt|json|ya?ml|toml|ts|tsx|js|jsx|py|go|rs|java|kt|sql|sh))"
)
# 跨工具单一来源引用是唯一正当的 @ 用法，不计入"内嵌成本"
INCLUDE_EXEMPT_RE = re.compile(r"include\s*:\s*@", re.I)

DOC_POINTER_RE = re.compile(r"docs/|\.claude/rules|\.cursor/rules|`[^`]*\.md`|\]\([^)]*\.md\)", re.I)

# 阈值（本地策略，不是原文数据）
EMPHASIS_MAX = 5
EMPHASIS_RATIO = 0.30
AT_WARN_TOKENS = 500
AT_HIGH_TOKENS = 1500
RULE_WARN = 100
RULE_HIGH = 150


# ---------------------------------------------------------------- 工具函数

def estimate_tokens(text: str) -> int:
    """粗估 token：CJK 约 1 字 1 token，其余约 3.6 字符 1 token。"""
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return int(round(cjk + other / 3.6))


def normalize(line: str) -> str:
    s = BULLET_RE.sub("", line)
    s = re.sub(r"`|\*|_|>|\[|\]|\(|\)|（）|【|】", "", s)
    s = re.sub(r"[\s，。；、,.!！?？:：\-—]+", "", s)
    return s.strip().lower()


def git(repo: Path, *args):
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def scan(lines):
    """返回 (规则行下标集合, 在代码块内的行下标集合)。"""
    rule_idx, fence_idx = set(), set()
    in_code = False
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if m:
            in_code = not in_code
            fence_idx.add(i)
            continue
        if in_code:
            fence_idx.add(i)
            continue
        if not line.strip() or HEADING_RE.match(line):
            continue
        if BULLET_RE.match(line) or IMPERATIVE_RE.search(line):
            rule_idx.add(i)
    return rule_idx, fence_idx


# ---------------------------------------------------------------- 发现文件

def discover(repo: Path):
    """返回 (主文件列表, 其他工具配置列表, 作用域规则文件列表)。"""
    mains, extras, scoped = [], [], []
    seen = set()

    for name in ROOT_NAMES:
        p = repo / name
        if p.is_file():
            mains.append(p)
            seen.add(p)

    nested = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        for p in repo.rglob(name):
            if p in seen or not p.is_file():
                continue
            parts = p.relative_to(repo).parts
            if len(parts) - 1 > MAX_NESTED_DEPTH:
                continue
            if SKIP_DIRS & set(parts):
                continue
            nested.append(p)
            seen.add(p)
    mains.extend(sorted(nested, key=lambda x: (len(x.relative_to(repo).parts), str(x))))

    for rel in EXTRA_FILES:
        p = repo / rel
        if p.is_file():
            extras.append(p)
            seen.add(p)

    for d in RULES_DIRS:
        base = repo / d
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.suffix in {".md", ".mdc"} and p.is_file() and p not in seen:
                    scoped.append(p)
                    seen.add(p)

    return mains, extras, scoped


# ---------------------------------------------------------------- 单项检查

def check_budget(text, lines, findings, budget_lines, budget_tokens):
    n_lines, tokens = len(lines), estimate_tokens(text)

    if n_lines > budget_lines:
        over = round((n_lines - budget_lines) / budget_lines * 100)
        findings.append({
            "level": "高",
            "title": "行数超出启动上下文预算",
            "detail": f"{n_lines} 行（预算 {budget_lines} 行，超出 {over}%），约 {tokens} token。",
            "action": "把只在特定任务需要的段落移到 docs/ 或 .claude/rules/，这里只留入口。",
        })
    elif n_lines > 150:
        findings.append({
            "level": "中",
            "title": "偏长",
            "detail": f"{n_lines} 行 / 约 {tokens} token。参照：OpenAI 改写后的 AGENTS.md 约 100 行。",
            "action": "逐节问：删掉这段，Agent 会不会犯错？不会就删或外移。",
        })

    if tokens > budget_tokens:
        over = round((tokens - budget_tokens) / budget_tokens * 100)
        findings.append({
            "level": "高" if tokens > budget_tokens * 2 else "中",
            "title": "token 超出启动上下文预算",
            "detail": f"约 {tokens} token（预算 {budget_tokens}），超出 {over}%。"
                      + ("行数不多但 token 很高，通常是命令块或长段落占的。"
                         if n_lines <= 150 else "行数和 token 都超了，优先外移。"),
            "action": "压缩长段落；能外移的表格、清单、示例移到 docs/。",
        })
    return {"lines": n_lines, "tokens": tokens}


def check_rule_count(rule_idx, findings):
    n = len(rule_idx)
    if n > RULE_HIGH:
        findings.append({
            "level": "高",
            "title": "规则条数超过可靠遵循上限",
            "detail": f"约 {n} 条规则。前沿模型可靠遵循的经验上限是 150–200 条，"
                      f"系统提示已占掉约 50 条，留给项目的约 100–150 条。超过后是所有规则一起变差。",
            "action": "砍到 100 条以内；优先砍能从代码推断的、工具能管的、重复的。",
        })
    elif n > RULE_WARN:
        findings.append({
            "level": "中",
            "title": "规则条数接近上限",
            "detail": f"约 {n} 条规则，已经贴近可靠遵循区间。",
            "action": "把模块级、领域级的规则挪到按需加载的文件里。",
        })
    return n


def check_emphasis(lines, rule_idx, findings):
    hits = [i + 1 for i in rule_idx if EMPHASIS_RE.search(lines[i])]
    ratio = len(hits) / len(rule_idx) if rule_idx else 0

    if len(hits) > EMPHASIS_MAX or (len(hits) >= 4 and ratio > EMPHASIS_RATIO):
        shown = ", ".join(map(str, hits[:12]))
        findings.append({
            "level": "中",
            "title": "优先级通胀",
            "detail": f"{len(hits)} 条规则带强强调词（占规则数 {round(ratio * 100)}%），"
                      f"涉及行：{shown}{' …' if len(hits) > 12 else ''}。"
                      f"每条都标重要，等于没有优先级。",
            "action": "只给 Agent 反复违反过的那 1–3 条保留 IMPORTANT/MUST，其余去掉。",
        })

    buried = [ln for ln in hits if ln > len(lines) * 0.6 and len(lines) > 60]
    if buried:
        findings.append({
            "level": "中",
            "title": "关键规则埋在文件中后段",
            "detail": f"强强调词出现在第 {', '.join(map(str, buried[:8]))} 行，"
                      f"而文件共 {len(lines)} 行——越靠后越容易被忽略。",
            "action": "把常被违反的少数规则提到文件前部，或单独成节。",
        })
    return len(hits)


def check_at_embeds(text, repo, base_dir, fence_idx, findings):
    """统计 @path 在每次启动时被整份注入的成本。"""
    refs, exempt = [], 0
    for i, line in enumerate(text.splitlines()):
        if i in fence_idx:
            continue            # 代码块里的 @ 多半是示例
        if INCLUDE_EXEMPT_RE.search(line):
            exempt += 1         # 跨工具单一来源引用，正当用法
            continue
        for m in AT_REF_RE.finditer(line):
            raw = m.group(1)
            for cand in (base_dir / raw, repo / raw):
                try:
                    p = cand.resolve()
                except Exception:
                    continue
                if p.is_file():
                    refs.append((raw, p))
                    break
            else:
                refs.append((raw, None))

    resolved = [(r, p) for r, p in refs if p]
    missing = [r for r, p in refs if p is None]
    total_tokens, rows = 0, []
    for raw, p in resolved:
        try:
            size = estimate_tokens(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            size = 0
        total_tokens += size
        rows.append(f"{raw}（约 {size} token）")

    if total_tokens > AT_HIGH_TOKENS:
        level, title = "高", "详细文档被内嵌进启动上下文"
    elif total_tokens > AT_WARN_TOKENS:
        level, title = "中", "文档引用可能变成注入"
    else:
        level, title = None, None

    if level:
        findings.append({
            "level": level,
            "title": title,
            "detail": f"{len(resolved)} 处 @ 引用合计约 {total_tokens} token，每次启动常驻："
                      + "；".join(rows[:8]) + ("…" if len(rows) > 8 else ""),
            "action": "改成条件指引：'涉及 X 的改动，先读 docs/y.md'。@ 是整份注入，不是按需加载；"
                      "只有 CLAUDE.md 里 `Include: @./AGENTS.md` 这类单一来源引用例外。",
        })

    if missing:
        findings.append({
            "level": "低",
            "title": "引用路径无法解析",
            "detail": f"未找到：{', '.join(missing[:5])}。",
            "action": "核对相对路径，或改成条件指引。死引用会把 Agent 引到空处。",
        })

    return {"at_count": len(resolved), "at_tokens": total_tokens, "at_exempt": exempt}


def check_negation_without_alternative(lines, rule_idx, fence_idx, findings):
    hits = []
    for i in sorted(rule_idx):
        line = lines[i]
        m = NEGATION_RE.search(line)
        if not m:
            continue
        head, tail = line[:m.start()], line[m.end():]
        has_alt = (
            ALTERNATIVE_RE.search(tail)
            or POSITIVE_ACTION_RE.search(head)
            or "`" in head
            or "`" in tail
        )
        if not has_alt:
            hits.append((i + 1, line.strip()))

    if hits:
        findings.append({
            "level": "高" if len(hits) >= 5 else "中",
            "title": "只有“不要”，没有“应该”",
            "detail": f"{len(hits)} 条规则只说了禁止、没给替代路径，Agent 在这些场景下会卡住或自行发挥：\n"
                      + "\n".join(f"  L{ln}: {txt[:90]}" for ln, txt in hits[:6]),
            "action": "每条“不要 X”后面补“，用 Y”。没有替代路径就说明这条规则本身没想过。",
        })
    return len(hits)


def check_vague(lines, rule_idx, findings):
    hits = []
    for i in sorted(rule_idx):
        line = lines[i]
        if "`" in line:          # 已经给了具体命令，形容词只是修饰
            continue
        if VAGUE_RE.search(line) and len(BULLET_RE.sub("", line).strip()) < 120:
            hits.append((i + 1, line.strip()))

    if hits:
        findings.append({
            "level": "中",
            "title": "规则里只有形容词，没有可执行动作",
            "detail": f"{len(hits)} 条规则含“注意/尽量/适当/合理/避免不必要”这类无法执行的说法：\n"
                      + "\n".join(f"  L{ln}: {txt[:90]}" for ln, txt in hits[:6]),
            "action": "换成具体命令或具体判据。'注意测试效率' → '跑测试用 `pnpm test <文件路径>`'。",
        })
    return len(hits)


def check_tooling(lines, rule_idx, findings):
    hits = [(i + 1, lines[i].strip()) for i in sorted(rule_idx) if TOOLING_RE.search(lines[i])]
    if hits:
        findings.append({
            "level": "低",
            "title": "可能在重复确定性工具的职责",
            "detail": f"{len(hits)} 行涉及缩进/格式/lint 具体规则：\n"
                      + "\n".join(f"  L{ln}: {txt[:90]}" for ln, txt in hits[:5]),
            "action": "交给 Prettier/ESLint/CI。确定性的事交给确定性工具，更快更准，也不占指令预算。",
        })
    return len(hits)


def check_duplicates(lines, rule_idx, fence_idx, findings):
    seen, dup = {}, []
    for i in sorted(rule_idx):
        n = normalize(lines[i])
        if len(n) < 12:
            continue
        if n in seen:
            dup.append((seen[n] + 1, i + 1, lines[i].strip()[:80]))
        else:
            seen[n] = i

    headings, dup_head = {}, []
    for i, line in enumerate(lines):
        if not HEADING_RE.match(line):
            continue
        h = normalize(HEADING_RE.sub("", line))
        if not h:
            continue
        if h in headings:
            dup_head.append((headings[h] + 1, i + 1, line.strip()[:60]))
        else:
            headings[h] = i

    if dup:
        findings.append({
            "level": "中",
            "title": "同一规则写了不止一次",
            "detail": "\n".join(f"  L{a} 与 L{b}: {txt}" for a, b, txt in dup[:6]),
            "action": "合并成一条。重复的规则会互相稀释，而且改一处忘另一处。",
        })
    if dup_head:
        findings.append({
            "level": "低",
            "title": "重复的小节标题",
            "detail": "\n".join(f"  L{a} 与 L{b}: {txt}" for a, b, txt in dup_head[:6]),
            "action": "合并小节，不然 Agent 不知道该以哪一段为准。",
        })
    return len(dup) + len(dup_head)


def check_readme_overlap(lines, fence_idx, repo, findings):
    readme = next((repo / n for n in ("README.zh-CN.md", "README.md") if (repo / n).is_file()), None)
    if not readme:
        return 0
    try:
        rset = {
            normalize(l) for l in readme.read_text(encoding="utf-8", errors="ignore").splitlines()
            if len(normalize(l)) >= 12
        }
    except Exception:
        return 0

    hits = []
    for i, line in enumerate(lines):
        if i in fence_idx or HEADING_RE.match(line):
            continue
        n = normalize(line)
        if len(n) >= 12 and n in rset:
            hits.append((i + 1, line.strip()[:80]))

    if hits:
        findings.append({
            "level": "中",
            "title": f"与 {readme.name} 内容重复",
            "detail": f"{len(hits)} 行在 README 里已经出现过：\n"
                      + "\n".join(f"  L{ln}: {txt}" for ln, txt in hits[:5]),
            "action": "删掉或改成一句指引。README 有的不必重复，它每次都占启动预算。",
        })
    return len(hits)


def check_structure(text, lines, findings):
    if len(lines) < 25:
        return 0

    has_cmd = bool(re.search(
        r"```(bash|sh|shell|zsh|console)|pnpm |npm |yarn |bun |make |pytest|cargo |go test|uv |poetry |docker|just ",
        text, re.I))
    has_struct = bool(re.search(r"(结构|目录|structure|directories|/src|/app|/lib|/packages)", text, re.I))
    has_conv = bool(re.search(r"(约定|规范|convention|重要|注意|不要|规则|\brules\b|\bguidelines\b)", text, re.I))
    has_what = bool(re.search(r"(技术栈|stack|项目简介|overview|是什么|项目|project)", text, re.I))
    has_why = bool(re.search(r"(为什么|原因|理由|决策|背景|\bwhy\b|trade-?off)", text, re.I))

    missing = []
    if not has_what:
        missing.append("WHAT（项目是什么、技术栈）")
    if not has_struct:
        missing.append("结构（目录各自负责什么）")
    if not has_cmd:
        missing.append("HOW（可执行的命令）")
    if not has_conv:
        missing.append("约定（Agent 猜不到的规则）")

    if missing:
        findings.append({
            "level": "中" if len(missing) >= 2 else "低",
            "title": "骨架缺项",
            "detail": "缺少：" + "、".join(missing) + "。",
            "action": "按 WHAT（是什么）/ WHY（为什么这样选）/ HOW（怎么跑）三维度补齐，"
                      "没有可写内容的维度就留空，不要编。",
        })
    elif len(lines) > 60 and not has_why:
        findings.append({
            "level": "低",
            "title": "缺 WHY",
            "detail": "有 WHAT 和 HOW，但找不到“为什么这样选”的说明。",
            "action": "补上少数几条决策理由——那是代码里推不出来、也最容易随人员流失的一块。",
        })
    return len(missing)


def check_pointers(text, lines, repo, findings):
    if len(lines) <= 80:
        return 0
    has_pointer = bool(DOC_POINTER_RE.search(text))
    has_docs = (repo / "docs").is_dir() or (repo / ".claude" / "rules").is_dir()

    if not has_pointer:
        findings.append({
            "level": "高",
            "title": "没有指向外部的入口，所有内容都堆在这里",
            "detail": f"{len(lines)} 行的内容里找不到 docs/ 或按需文档的指引。",
            "action": "把领域知识拆到 docs/，这个文件只留地图：什么时候读哪个文件。",
        })
    elif has_docs is False and "docs/" in text:
        findings.append({
            "level": "低",
            "title": "提到了 docs/ 但目录不存在",
            "detail": "文件里有 docs/ 指引，但仓库里没有 docs/ 目录。",
            "action": "确认引用是死的还是指向别处；死引用会把 Agent 引到空处。",
        })
    return 0


def check_freshness(path, repo, findings):
    if path.name in PRIVATE_NAMES:
        return {}

    if not (repo / ".git").exists():
        return {}

    log = git(repo, "log", "-1", "--format=%cs", "--", str(path))
    count = git(repo, "rev-list", "--count", "HEAD", "--", str(path))

    if not log:
        findings.append({
            "level": "低",
            "title": "文件尚未提交",
            "detail": "git 里没有这个文件的记录。",
            "action": "提交到 git。每次 Agent 犯了没拦住的错就回来加一条，这样它才是活文档。",
        })
        return {}

    try:
        commit_day = date.fromisoformat(log)
    except Exception:
        return {}

    try:
        mtime_day = datetime.fromtimestamp(path.stat().st_mtime).date()
    except Exception:
        mtime_day = commit_day

    if mtime_day > commit_day:
        findings.append({
            "level": "低",
            "title": "有未提交的修改",
            "detail": f"最后提交在 {commit_day}，工作区里的版本改到 {mtime_day}。",
            "action": "确认改动是否要提交；长期不提交的规则改动团队里没人看得到。",
        })

    days = (date.today() - commit_day).days
    if days > 90:
        findings.append({
            "level": "低",
            "title": "很久没更新了",
            "detail": f"最后修改 {commit_day}（约 {days} 天前），共 {count or '?'} 次提交。"
                      f"代码变了而它没变，里面就可能有过时规则在误导 Agent。",
            "action": "做一次减法体检：每行问“删掉它 Agent 会不会犯错”，不会就删。",
        })
    return {"last_commit": str(commit_day), "days": days, "commits": count}


def substantive_content(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return 0
    return len([l for l in lines if l.strip() and not l.strip().startswith("---")])


def check_cross_tool(files, extras, findings, repo_root):
    by_name = {p.name: p for p in files if p.parent == repo_root}

    if "AGENTS.md" in by_name and "CLAUDE.md" in by_name:
        try:
            c = by_name["CLAUDE.md"].read_text(encoding="utf-8", errors="ignore")
        except Exception:
            c = ""
        if "AGENTS.md" not in c:
            findings.append({
                "level": "中",
                "title": "根目录 AGENTS.md 与 CLAUDE.md 并存但互不引用",
                "detail": "两份指令文件同在仓库根目录，内容很可能已经开始分叉。",
                "action": "规则只放 AGENTS.md（通用标准），CLAUDE.md 里只写一行 Include: @./AGENTS.md。",
            })

    # 只有"实质内容"的其他工具配置才算重复维护；一行的引用桥不算
    duped = [p for p in extras if substantive_content(p) > 3]
    if duped:
        rel = []
        for p in duped[:8]:
            try:
                rel.append(str(p.relative_to(repo_root)))
            except Exception:
                rel.append(p.name)
        findings.append({
            "level": "低",
            "title": "其他工具配置里有独立内容",
            "detail": "发现：" + "、".join(rel),
            "action": "如果这些内容和 AGENTS.md 重复，删掉并改成一行引用；确实只对该工具生效的规则才留着。",
        })
    return 0


# ---------------------------------------------------------------- 主分析

def analyze(path: Path, repo: Path, budget_lines: int, budget_tokens: int):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return {"path": str(path), "error": str(e)}

    lines = text.splitlines()
    rule_idx, fence_idx = scan(lines)

    findings = []
    meta = {}
    meta.update(check_budget(text, lines, findings, budget_lines, budget_tokens))
    meta["rules"] = check_rule_count(rule_idx, findings)
    meta["emphasis"] = check_emphasis(lines, rule_idx, findings)
    meta.update(check_at_embeds(text, repo, path.parent, fence_idx, findings))
    meta["negations"] = check_negation_without_alternative(lines, rule_idx, fence_idx, findings)
    meta["vague"] = check_vague(lines, rule_idx, findings)
    meta["tooling"] = check_tooling(lines, rule_idx, findings)
    meta["duplicates"] = check_duplicates(lines, rule_idx, fence_idx, findings)
    meta["readme_overlap"] = check_readme_overlap(lines, fence_idx, repo, findings)
    meta["missing_sections"] = check_structure(text, lines, findings)
    check_pointers(text, lines, repo, findings)
    meta.update(check_freshness(path, repo, findings))

    order = {"高": 0, "中": 1, "低": 2}
    findings.sort(key=lambda f: order.get(f["level"], 3))

    return {
        "path": str(path),
        "relpath": str(path.relative_to(repo)) if repo in path.parents else str(path),
        "meta": meta,
        "findings": findings,
    }


# ---------------------------------------------------------------- 输出

def print_block(findings):
    for i, f in enumerate(findings, 1):
        print(f"[{f['level']}] {i}. {f['title']}")
        for ln in f["detail"].splitlines():
            print(f"      {ln}")
        print(f"      → {f['action']}")
        print()


def print_report(result, budget_lines, budget_tokens):
    if "error" in result:
        print(f"跳过 {result['path']}: {result['error']}")
        return

    m = result["meta"]
    print("=" * 68)
    print(result["relpath"])
    print("=" * 68)
    print(f"规模     {m.get('lines', 0)} 行 / 约 {m.get('tokens', 0)} token"
          f"   （预算 {budget_lines} 行 / {budget_tokens} token）")
    at = f"@ 内嵌 {m.get('at_count', 0)} 处 / 约 {m.get('at_tokens', 0)} token"
    if m.get("at_exempt"):
        at += f"（已排除 {m['at_exempt']} 处 Include 引用）"
    print(f"规则     约 {m.get('rules', 0)} 条；强强调词 {m.get('emphasis', 0)} 条；{at}")
    if m.get("last_commit"):
        print(f"最近提交 {m['last_commit']}（{m.get('days', '?')} 天前，{m.get('commits', '?')} 次提交）")
    print()

    if not result["findings"]:
        print("未发现明显问题。仍建议对照 SKILL.md 的「交付前自检」人工过一遍。")
        print()
        return

    counts = {}
    for f in result["findings"]:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
    print("问题（按优先级）： "
          + "  ".join(f"{k} {counts[k]}" for k in ("高", "中", "低") if k in counts))
    print()
    print_block(result["findings"])


def print_repo_report(findings, scoped):
    if scoped:
        print("=" * 68)
        print("按路径分域的作用域规则")
        print("=" * 68)
        for p in scoped[:12]:
            print(f"  {p}")
        if len(scoped) > 12:
            print(f"  … 共 {len(scoped)} 个")
        print("  这是推荐做法：作用域由目录承担，不必写进根文件。")
        print()

    if findings:
        print("=" * 68)
        print("仓库级")
        print("=" * 68)
        print_block(findings)


def main():
    ap = argparse.ArgumentParser(description="体检 AGENTS.md / CLAUDE.md 等 Agent 指令文件")
    ap.add_argument("paths", nargs="*", help="要体检的文件（默认自动发现）")
    ap.add_argument("--repo", default=".", help="仓库根目录，默认当前目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--budget-lines", type=int, default=300, help="行数预算，默认 300")
    ap.add_argument("--budget-tokens", type=int, default=2500, help="token 预算，默认 2500")
    args = ap.parse_args()

    if args.budget_lines <= 0 or args.budget_tokens <= 0:
        print("预算必须是正整数。", file=sys.stderr)
        return 2

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"仓库目录不存在: {repo}", file=sys.stderr)
        return 2

    if args.paths:
        files, extras, scoped = [], [], []
        for raw in args.paths:
            p = Path(raw).resolve()
            if not p.is_file():
                print(f"文件不存在: {p}", file=sys.stderr)
                return 2
            files.append(p)
    else:
        files, extras, scoped = discover(repo)

    if not files:
        if args.json:
            print(json.dumps({"files": [], "repo_findings": [], "scoped_rules": []},
                             ensure_ascii=False, indent=2))
        else:
            print("没有找到 AGENTS.md / CLAUDE.md / .cursor/rules 等指令文件。")
            print("写第一份时用最小可用骨架起步，见 references/templates.md。")
        return 0

    results = [analyze(p, repo, args.budget_lines, args.budget_tokens) for p in files]

    repo_findings = []
    if not args.paths:
        check_cross_tool(files, extras, repo_findings, repo)

    if args.json:
        print(json.dumps({
            "files": results,
            "repo_findings": repo_findings,
            "scoped_rules": [str(p) for p in scoped],
        }, ensure_ascii=False, indent=2))
        return 0

    for r in results:
        print_report(r, args.budget_lines, args.budget_tokens)

    print_repo_report(repo_findings, scoped)

    print("=" * 68)
    print("这是初筛，不是判决：每一条都要读原文再判断。")
    print("下一步：按 SKILL.md 的「工作流」重写，用 references/templates.md 的骨架。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
