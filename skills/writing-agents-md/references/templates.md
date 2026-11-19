# 模板与句式库

按项目规模选骨架，把 `【】` 里的内容换成真实信息。读不到的信息不要编，用 `【待补充：xxx】` 明确标出，并在交付时单独列一份待补清单。

---

## 一、最小可用版（个人项目，30–50 行）

适合只有一个开发者、代码量不大的项目。原则：只写你被坑过的地方。

```markdown
# 【项目名】

【一句话说清楚这是什么】。技术栈：【语言/框架/数据库】。

## 命令
- `【安装命令】`
- `【启动命令】`
- `【测试单文件命令】`（全量测试很慢，写法见「标准版」）
- `【类型检查/构建命令】`

## 结构
- `【目录】` - 【职责】
- `【目录】` - 【职责，以及"跨模块共享的逻辑都在这，别重复造"这类约束】

## 注意
- 【曾经踩过的坑，以及正确的做法】
- 【哪个目录不能碰，为什么】
```

写不满没关系。**空着的维度不写，比编一段好。**

---

## 二、标准版（团队项目，80–120 行）

适合多人协作、有 CI、有 code review 的项目。这是最常用的一版。

````markdown
# 【项目名】

【一句话定位】。技术栈：【框架 + 关键依赖】。

## 常用命令

```bash
pnpm dev                          # 开发服务器
pnpm test src/xxx.test.ts         # 单文件测试 —— 默认用这个
pnpm test                         # 全量测试，4 分钟+，只在明确要求时跑
pnpm typecheck                    # 改完代码必须跑
pnpm db:migrate                   # 数据库迁移
```

## 项目结构

- `app/` - 页面与路由（App Router）
- `lib/` - 跨模块共享逻辑，新增工具函数放这里，别在组件里重复造
- `prisma/` - schema 与 migration；**migration 文件不手动改**，用 `pnpm db:migrate`
- `scripts/` - 一次性脚本，不进构建

## 重要约定

- 不要用 `--force` 推送，用 `--force-with-lease`
- 环境变量放 `.env.local`，不要提交；新增变量同步更新 `.env.example`
- 认证相关的改动，先读 `docs/auth-patterns.md`
- 涉及行为打点的改动，先读 `docs/tracking.md`
- 改动 `lib/billing/` 后必须跑 `pnpm test src/billing`

## 代码风格

格式与 lint 全部交给工具，不要手写规则：

- `pnpm lint --fix` / `pnpm format`，提交前由 husky 自动执行

## 上下文压缩时必须保留

- 本次修改过的文件列表
- 已经跑过的测试命令和结果（尤其失败的）
- 当前实现计划与已完成步骤
````

---

## 三、Monorepo 版（根文件做索引，子项目各管各的）

根目录**只做地图**，不写任何单包的业务规则。

### 根 `AGENTS.md`

````markdown
# 【Monorepo 名】

【一句话定位】。pnpm workspaces 管理。

## 命令

```bash
pnpm install
pnpm --filter 【包名】 dev
pnpm --filter 【包名】 test
pnpm -w typecheck              # 全仓库类型检查
```

## 包

| 路径 | 职责 | 读哪个规则 |
|---|---|---|
| `apps/web` | 前端应用 | 改这个目录前读 `apps/web/AGENTS.md` |
| `apps/api` | 后端服务 | 改这个目录前读 `apps/api/AGENTS.md` |
| `packages/ui` | 共享组件库 | 改这个目录前读 `packages/ui/AGENTS.md` |
| `packages/shared` | 前后端共享类型 | 改动会影响两端，先确认接口再动 |

## 全局约定

- 新增依赖用 `pnpm --filter <包名> add`，不要在根目录装运行时依赖
- 跨包引用走 workspace 协议 `workspace:*`，不要用相对路径穿透包边界
````

### 子目录 `apps/api/AGENTS.md`

````markdown
# apps/api

NestJS + Prisma + PostgreSQL。

## 命令
```bash
pnpm --filter api test src/modules/xxx/xxx.spec.ts
pnpm --filter api test:e2e            # 需要本地数据库
```

## 约定

- 新增接口必须同步更新 `docs/api/openapi.yaml`
- 所有 DB 访问经 `src/prisma/` 的封装，不要在 service 里直接 `new PrismaClient()`
- 涉及鉴权/权限的改动，先读 `docs/auth-patterns.md`
````

子目录的规则**只在 Agent 碰到该目录的文件时才加载**——层级本身就是渐进式披露。

---

## 四、`docs/` 目录结构

`AGENTS.md` 指向的目标。按"什么时候需要"分类，不按文件类型分类。

```
docs/
├── auth-patterns.md      认证与权限的既有模式（改认证时读）
├── tracking.md           埋点事件、触发时机、参数含义（改打点时读）
├── db-conventions.md     schema 设计约定、迁移流程（改数据模型时读）
├── deploy.md             部署流程、环境差异（要发布时读）
├── architecture.md       模块划分与关键决策及理由（大改动前读）
└── adr/                  架构决策记录，一个决策一个文件
    ├── 0001-choose-postgres.md
    └── 0002-event-driven-billing.md
```

判断标准：**一个文档只有在"某类任务开始时才需要"才配拥有独立文件**。如果每次任务都要读，它该回到 `AGENTS.md` 里压缩成几句。

---

## 五、`.claude/rules/`：用路径限定作用域

每条规则一个文件，用 frontmatter 的 `paths` 限定生效范围。作用域由目录结构承担，不用写进正文。

`.claude/rules/auth.md`：

```markdown
---
description: 认证与权限相关规则
paths:
  - "src/auth/**"
  - "src/middleware/session.ts"
---

- 新增受保护路由必须在 `src/auth/permissions.ts` 注册；只在组件里判断权限会被绕过
- 改动后跑 `pnpm test src/auth`
- token 有效期相关常量只在 `src/auth/constants.ts` 定义，不要就地写死
```

`.claude/rules/db.md`：

```markdown
---
description: 数据库与迁移规则
paths:
  - "prisma/**"
  - "src/**/repository/**"
---

- migration 文件不手动改，用 `pnpm db:migrate`
- 破坏性变更（删列、改类型）必须先在 `docs/db-conventions.md` 里记一次兼容方案
```

---

## 六、条件引用句式库

把"每次都注入"换成"需要时去读"。四种常见句式：

```markdown
- 涉及【模块】的改动，先读 `docs/【文档】.md`
- 改 `【目录】/` 下的文件前，先读 `docs/【文档】.md`
- 遇到【错误码/组件/场景】相关的问题时，先看 `docs/【文档】.md`
- 需要新增【某类东西】时，按 `docs/【文档】.md` 里的模板来
```

**关键：句子里必须带触发条件。** 写成"详见 `docs/auth-patterns.md`"等于没写——Agent 不知道什么时候该去读。

对照：

| 写法 | 结果 |
|---|---|
| `@docs/tracking.md` | 每次启动整份注入（作者那份打点文档实测约 10K token） |
| 详见 `docs/tracking.md` | 通常不会被读 |
| 涉及行为打点的改动，先读 `docs/tracking.md` | 只在碰打点时读，正确 |

**唯一适合 `@` 的地方**是跨工具的单一来源引用：

```markdown
Include: @./AGENTS.md
```

因为 `AGENTS.md` 本来就是每次启动都会读的文件，`CLAUDE.md` 引用它不多花上下文，也不会有第二份内容需要同步。

---

## 七、多工具单一来源

团队里 Cursor / Copilot / Gemini CLI 混用时，别维护好几份。

| 工具 | 配置文件 | 内容 |
|---|---|---|
| 通用 / Codex / 多数 | `AGENTS.md` | **全部规则写这里** |
| Claude Code | `CLAUDE.md` | `Include: @./AGENTS.md` |
| Cursor | `.cursor/rules/*.mdc` | 一行引用，或 `paths` 限定的补充规则 |
| GitHub Copilot | `.github/copilot-instructions.md` | 一行引用 |
| Gemini CLI | `GEMINI.md` | 一行引用 |

一份内容，处处生效，改一处全部同步。

---

## 八、上下文压缩保留段

长会话被自动压缩时，关键状态会丢。显式声明保留什么：

```markdown
## 上下文压缩时必须保留

- 本次会话修改过的文件列表
- 已执行的测试命令与结果，尤其是失败的用例名
- 当前实现计划、已完成步骤、下一步
- 用户明确说过的约束（不能改的接口、必须兼容的版本、不能引入的依赖）
```

---

## 九、错题本条目格式

每条规则附一句事故背景。三周后回头看，你会知道它为什么存在。

```markdown
- 【规则】。（曾经【事故经过】，【后果】）
```

真实例子：

```markdown
- 改 `src/billing/` 下任何文件后必须跑 `pnpm test src/billing`。
  （曾经改完只跑了 typecheck，漏掉一个金额取整的回归，线上多扣了一次钱）
- 新接口必须走 `withAuth()` 包装，不要自己读 header。
  （曾经有个接口手写解析，漏了过期校验，被扫出来）
```

事故背景不是文学修饰，它是这条规则**不被误删**的依据。做减法时，有事故背书的规则要重新评估；没有的，先过一遍「删掉它 Agent 会不会犯错」这一问，不会犯错才删——别因为没写原因就当它是废话。

---

## 十、常见反例 → 改法

| 反例 | 问题 | 改成 |
|---|---|---|
| `注意测试的执行效率，避免不必要的全量测试。` | 埋在中部 + 没有可执行性 | `跑测试用 pnpm test <文件路径>` + `全量测试很慢（4 分钟+），只在明确要求时跑` |
| `请遵循团队的代码规范。` | 没有任何具体判据 | 删掉，规则交给 `.eslintrc` / `prettier`，最多留一句 `提交前跑 pnpm lint --fix` |
| `如果遇到问题可以先查一下文档。` | 触发条件和目标都不明确 | `遇到 【具体错误码】 时，先读 docs/troubleshooting.md` |
| `使用 2 个空格缩进，单引号，结尾不加分号。` | 确定性工具的职责 | 删掉，配置 `prettier` |
| `本项目使用 React 18 + TypeScript。` | 代码里直接能看出来 | 删掉。改写成"为什么"：`状态管理统一用 zustand，不要引入 redux` |
| `重要：不要修改 src/legacy/ 下的代码` | 只禁不导 | `src/legacy/ 是给老客户的兼容层，改动可能让老客户调不通。确实需要改时，先读 docs/legacy.md` |
| `@docs/api.md` | 每次启动整份注入 | `新增或修改接口时，先读 docs/api.md` |
| `尽量写测试。` | 无法执行 | `新增的纯函数必须在同目录加 xxx.test.ts，覆盖边界值` |
| `注意性能。` | 无法执行 | `列表渲染超过 200 项时用虚拟滚动，参考 lib/VirtualList.tsx` |
| `代码要整洁，注释要清晰。` | 通用噪音 | 删掉。有具体要求就写具体要求，没有就是没有 |

---

## 十一、体检交付格式（模式 B）

重写之后按这五块交付，不要让用户自己去 diff：

```markdown
## 体检结果

【脚本摘要，两三行：行数/token、规则数、最严重的两三个问题】

## 主要问题

按影响排序，每条一句话说清楚"这样写会让 Agent 做什么错事"。最多 5 条。

## 改写后的文件

【完整内容，可直接复制】

## 移走的内容

| 原内容 | 移到哪里 | 什么时候读 |
|---|---|---|
| 埋点文档说明 | docs/tracking.md | 涉及行为打点时 |
| 部署流程 | docs/deploy.md | 要发布时 |
| 缩进/引号规则 | .prettierrc | 不需要读，工具自动执行 |

## 待补充

- 【待补充：测试命令】——没在 package.json 里找到
- 【待补充：legacy 目录的兼容背景】——只有你清楚
```

最后加一句维护节奏：**每次 Agent 犯了没拦住的错，回来加一条；每隔几周问一遍每行"删掉它会不会犯错"。**
