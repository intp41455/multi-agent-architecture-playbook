# CLAUDE.md

## 项目目的

企业多智能体架构落地方法论（playbook）。解决"不敢用、不会选、落不了地"三个卡点：
安全边界怎么划、框架怎么选、审计熔断怎么做。核心原则：**AI 提供可能性，代码提供确定性**——
模型只负责"自然语言 → 结构化"翻译，所有算术由本地死代码完成。

文档为中文；不含任何具体企业数据。

## 技术栈

- 纯 Markdown 文档仓库，无构建/测试基础设施
- 唯一代码：`templates/payroll_engine_example.py`（Python 3，仅标准库 csv/json/sys/datetime，无第三方依赖）

## 目录结构

```
README.md                     方法论总纲（一句话方法论 + 五大章节）
docs/
  01-确定性与安全边界.md       左移确定性、双门模型（云端内容护栏 vs 本地执行护栏）
  02-多智能体分工设计.md       三角色（需求解析师/精算执行员/审计监察员）+ 红线约束
  03-框架选型横评.md          CrewAI / LangGraph / Dify / 自研 选型矩阵
  04-审计与熔断体系.md        3-Tier Audit（TIER 01 沙盘 / TIER 02 三明治校验 / TIER 03 周期复盘）
templates/
  payroll_engine_example.py   确定性薪资核算引擎参考实现（输入 JSON → 输出 CSV）
  sample_input.json           引擎的示例输入（含 上月基准 环比校验数据）
  10-dimension-questionnaire.md  立项前企业信息采集问卷（含数据精度标注 SOP）
```

## 安装 / 运行

无安装步骤（仅参考实现依赖 Python 标准库）。

运行参考实现：

```bash
python templates/payroll_engine_example.py templates/sample_input.json out.csv
```

- 参数：`<input.json> <output.csv>`
- 输入契约：顶层含 `员工清单` 数组，每员工必填 `姓名`、`底薪`
- 三层校验全绿才写文件（结构性 → 时效性 → 逻辑自洽性/环比）；任一失败打印 `[BLOCK]` 并以退出码 1 终止，不产生输出
- 输出为 UTF-8-SIG 编码 CSV，写入新文件，绝不覆盖原件

## 关键约定与坑点

- **模型只出结构化输入，不出数字**：改业务口径 = 改 `PARAMS` 参数表 + 升 `RULES_VERSION`，不改代码、不调 prompt
- 所有文件写入/网络请求必须走本地写死的白名单；永远把模型当「输入法」（建议值），不当「操作系统」（系统指令）
- `sample_input.json` 省略 `数据时间戳` 字段（= 不做时效校验）；生产输入应带 ISO 8601 时间戳，超过 `FRESHNESS_HOURS`（72h）即 BLOCK
- 输出编码固定 `utf-8-sig`（.gitignore 忽略 `*.csv` 运行产出，`out/` `output/` 同理）
- `.gitignore` 严禁入库：`.env`、`*.key`、`*.pem`、`token.txt`、`*.token`、`*.har`、`*.pcap`
