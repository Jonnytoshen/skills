# AI Agent 技能集（Skills）

[English](./README.md) | 简体中文

一套可直接用于生产环境的 agent skills 集合。

## 技能列表

| Skill | 说明 | 安装 |
|-------|------|------|
| [**Writing Project Experience**](./skills/writing-project-experience/) | 从 git 仓库提炼有证据支撑的项目经历 | `npx skills add Jonnytoshen/skills --path skills/writing-project-experience` |
| [**JD Decoder**](./skills/jd-decoder/) | 拆解岗位 JD，看出要求背后真实的用人问题：核心要求排序、面试官关注点，以及值得澄清的模糊之处（仅需 JD） | `npx skills add Jonnytoshen/skills --path skills/jd-decoder` |
| [**Interview Preparation**](./skills/interview-preparation/) | 把岗位 JD 和简历转化为 JD 拆解、证据卡和可直接练习的回答（可选：对照项目仓库进行核对） | `npx skills add Jonnytoshen/skills --path skills/interview-preparation` |
| [**Writing AGENTS.md**](./skills/writing-agents-md/) | 编写、体检、精简 `AGENTS.md` / `CLAUDE.md`：用渐进式披露替代百科全书，规则写得短而准，才真的被遵守 | `npx skills add Jonnytoshen/skills --path skills/writing-agents-md` |

只拿到 JD？先用 **JD Decoder**。既有 JD 又有简历（还可以加上仓库）？用 **Interview Preparation**，它同样从 JD 拆解开始。

## 快速开始

安装任意一个技能：

```bash
npx skills add Jonnytoshen/skills --path skills/<skill-name>
```

然后在 agent 终端中调用：

```bash
/writing-project-experience <repo>         # 从 git 仓库生成一段项目经历
/jd-decoder <JD>                           # 看这个岗位真正在招什么人（仅需 JD）
/interview-preparation <JD> <resume>       # 生成 JD × 简历的面试准备包（仓库可选）
/writing-agents-md <仓库或 AGENTS.md>     # 写一份，或者把臃肿的那份改短改准
```

## 许可证

MIT
