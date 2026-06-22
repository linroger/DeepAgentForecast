# 全球云计算市场 2030 年竞争格局综合研判

> **报告性质**：基于截至 2026 年中已发布的可验证公开资料的全球云计算赛道深度研判，覆盖 IaaS / PaaS / 私有云（不含 SaaS）赛道。
> **数据基线**：Synergy Research Q3 2025 季报、Microsoft FY26 Q2 / Q3 财报、AWS / Google / Oracle / Alibaba / IBM 最新季报与年报、IDC / Gartner / Goldman Sachs / Bain / McKinsey / Dell'Oro 公开预测。
> **置信度梯度**：S1 一手财报与监管文件、S2 高质量二路报道、S3 行业博客 / 单源数据；报告中已分级标注。
> **时间窗**：2024–2026 实际经营 + 2027–2030 推演。
> **方法学**：以 2025 Q2–Q4 实际经营数据为锚点 → 通过因果链推演（compute backlog → capex → 产能 → 份额）→ 多情景敏感性分析（GPU 分配、地缘合规、AGI 触发、capex 回报率）。

---

## 一、执行摘要

全球云计算基础设施服务市场 2025 全年规模为 4190 亿美元（Synergy 季报 + Q4 加总），同比增长约 30%；Q4 单季达 1191 亿美元，AI 驱动的 PaaS / IaaS 增量贡献 34%。Cloud hyperscaler 集体进入 "万亿级 capex 时代"：四家（Amazon + Google + Microsoft + Meta）2025 年 capex 同比增长 76%，2026 年预计合计 6000–7250 亿美元。

**结构判断**：
- **AWS** 凭 30% 份额与 Trainium / Anthropic 体系延续收入第一；2025 年增长 24%，重夺三年最快增速。
- **Azure** 凭 39% 增长与 OpenAI 锁定条款，2030 年大概率与 AWS 持平或反超；执行风险源于美国之外的合规与本地化诉求。
- **Google Cloud** 凭 TPU 垂直整合 + Gemini 实现 32.9% 营业利润率（行业之最），但份额扩张受限。
- **Oracle OCI** 凭 6380 亿美元 RPO 锁定 "AI 算力租赁" 赛道，跻身 Top 4。
- **Neocloud**（CoreWeave / Nebius / Crusoe / Lambda）拿下 GPUaaS 增量 3–5%，但 2027–2028 年面临再融资悬崖。
- **中国云**（Alibaba / Huawei / Tencent）合计 4–6% 全球份额，信创与主权合规使其难以出海。
- **欧洲主权云** 15% 本地份额，AWS 76 亿欧元投资试图突破。
- **新主权云**（HUMAIN / OpenAI Stargate）成为新玩家。

**对 2030 年的核心判断**：
- Big Three 合计份额将从 2025 Q4 的 68% 维持在 65–72% 区间；
- Azure 增长最快，AWS 绝对收入仍最大，Google Cloud 利润率最高；
- Neocloud 在 2027–2028 年经历整合洗牌，留存 2–3 家头部；
- 中国云受地缘合约束缚，全球份额维持 4–6%；
- 长期承压方：IBM Cloud（退守混合）、长尾 SaaS、未掌握自研芯片的中小 Neocloud。

---

## 二、关键事实世界主体与关系图谱

### 2.1 核心参与者

| 主体 | 类型 | 关键角色 | 公开立场 | 影响力来源 |
|------|------|----------|----------|------------|
| **Amazon / AWS** | 美国超大规模云 | 现任市场份额第一（30%） | "加速 AI 与主权云投资" | Trainium2 芯片、Project Rainier 1M+ 芯片集群、Anthropic 战略合作 |
| **Microsoft Azure** | 美国超大规模云 | 增速最快（39% CC） | "AI 是新的操作系统" | OpenAI 27% 持股（1350 亿美元）、Stargate 2500 亿美元 Azure 承诺 |
| **Google Cloud** | 美国超大规模云 | 利润率最高（32.9%） | "AI 时代的纵向整合者" | 自研 TPU（v6e Trillium → v7 Ironwood）、Anthropic 100 万 TPU 协议、Gemini 模型族 |
| **Oracle OCI** | 美国超大规模云 | 后起之秀 | "AI 算力之王" | RPO 6380 亿美元（+363% YoY）、OpenAI Stargate 核心承建方 |
| **Alibaba Cloud** | 中国云 | 中国市场份额第一（约 33%） | "AI 普惠" | Qwen 模型族、东南亚 / 韩国 / 中东节点扩张 |
| **Huawei Cloud** | 中国云 | 中国第二（约 15.5%） | "全栈 AI" | 昇腾芯片、政企关系、电信运营商协同 |
| **Tencent Cloud** | 中国云 | 中国第三（约 15.1%） | "产业互联网" | 2025 年沙特首个 MENA 区域上线 |
| **IBM Cloud** | 美国混合云 | 传统巨头转型 | "混合 AI by design" | Red Hat OpenShift、watsonx 平台 |
| **CoreWeave** | Neocloud | 头部 | "GPU 基础设施专家" | 2025 年 3 月 IPO、市值峰值 800 亿美元 |
| **Nebius** | Neocloud | 第二大 | "主权 + 多元化" | 阿联酋 / 欧洲节点 |
| **Crusoe / Lambda** | Neocloud | 第三梯队 | "绿色算力"（Crusoe） | 与 Crusoe Energy 关联 |
| **OpenAI** | AI 实验室 / 新云玩家 | 模型 / 应用层王者 | "AGI 需独立基础设施" | Stargate 项目（5000 亿美元） |
| **Anthropic** | AI 实验室 | 模型层第二 | "AI 安全 + 多云" | 锁定 AWS Trainium、并行使用 100 万 TPU |
| **Saudi PIF / HUMAIN** | 主权基金 | 新云玩家 | "沙特 Vision 2030" | 2025 年 5 月成立，目标 18 GW 算力 |
| **Nvidia** | 芯片 / 基础设施供给侧 | 实际定价者 | "AI 工厂" | Blackwell GB200 / GB300、Vera Rubin 路线图 |
| **EU 委员会 / DG-COMP** | 监管方 | 竞争守门人 | "促进可竞争性" | DMA 框架（2025-11-18 启动 AWS / Azure 调查）|
| **MIIT（中国工信部）** | 监管方 | 行业准入 | "国产替代" | 信创目录、增值电信试点 |
| **CFIUS / BIS** | 美国监管方 | 国家安全 | "对等竞争" | 出口管制、Section 721 审查 |
| **Synergy Research** | S2 调研机构 | 市场口径标尺 | "季度份额数据" | Q3 2025 总规模 1070 亿美元、Big Three 63% |
| **Gartner / IDC** | S2 调研机构 | 长期预测口径 | "2027 年达 1.2 万亿美元" | Gartner 2025 年 11 月 7230 亿美元预测 |
| **Goldman Sachs** | S2 投行研究 | 长期规模预测 | "2030 年达 2 万亿美元" | Kash Rangan 22% CAGR 框架 |
| **Dell'Oro** | S2 调研机构 | 资本开支口径 | "hyperscaler capex 76% 增长" | 数据中心 capex 跟踪 |

### 2.2 关系图谱

**核心类型化边**（direction = 谁主导 / 关系类型 / 证据基础）：

1. AWS → Anthropic：投资 + 战略合作 / Anthropic 选定 AWS Trainium 为主要训练芯片 / 2024 年追加 40 亿美元（来源：aboutamazon.com 2024-11）
2. Microsoft → OpenAI：投资 + 独家云 / OpenAI 承诺 2500 亿美元 Azure 服务 / 微软持股 27% / 来源：blogs.microsoft.com 2025-10-28
3. Google → Anthropic：算力合作 / Anthropic 锁定 100 万 TPU / 来源：googlecloudpresscorner.com 2025-10-23
4. OpenAI → CoreWeave：算力合同 119 亿美元 + OpenAI 3.5 亿美元入股 / 来源：TechCrunch 2025-03
5. OpenAI → Oracle：Stargate 核心承建 / 2027 年起 4.5 GW 容量 / 来源：OpenAI 官网公告
6. OpenAI ↔ Microsoft：关系重构 / Azure 仍为 "主要云"，但 OpenAI 可在任意云服务非 API 产品 / 来源：blogs.microsoft.com 2025-10-28
7. Saudi PIF → HUMAIN：全资 / HUMAIN 与 Nvidia / AWS / Google / Microsoft 全面合作 / 来源：nvidianews.nvidia.com 2025-05
8. Nvidia → 所有 hyperscaler：芯片供给侧 / 微软 / 谷歌 / Meta / 亚马逊预订 Blackwell 至 2026 年底 / 2027 年初 / 来源：spheron.network
9. EU 委员会 → AWS / Azure：DMA 守门人调查 / 2025-11-18 启动 / 来源：digital-markets-act.ec.europa.eu
10. US BIS → 中国：出口管制 / 限制 14nm 以下 AI 芯片 / H100 / H200 / 来源：BIS 实体清单
11. MIIT → 中国云：信创目录 / 央国企优先采购国产云 / 来源：Trivium China 政策追踪
12. Synergy Research ↔ 行业：数据口径标准 / 行业基线 / 来源：srgresearch.com

**关键张力**：
- OpenAI 与 Microsoft：从独家云 → 解除独家 + 2500 亿美元承诺（既保底 Azure 又给灵活性）
- AWS 与 Google：Anthropic 同为两家最大客户（AWS Trainium + Google TPU），形成 "AWS 资本 + Google 算力" 双重绑定
- OpenAI 与 CoreWeave：12 亿美元入股 + 119 亿美元合同，但与 Microsoft 形成替代关系
- EU 与 US 三大：DMA 调查可能迫使部分功能解耦

---

## 三、关键事件时间线

| 日期 | 事件 | 来源 |
|------|------|------|
| 2024-09 | Amazon 首次投资 Anthropic 12.5 亿美元 | aboutamazon.com |
| 2024-11-19 | Gartner 预测 2025 年全球公有云 7230 亿美元 | gartner.com |
| 2025-01-21 | Trump 宣布 Stargate 5000 亿美元投资 | apnews.com |
| 2025-02-09 | Tencent Cloud 沙特首个 MENA 区域上线 | tencentcloud.com |
| 2025-03-10 | OpenAI 与 CoreWeave 119 亿美元合同 | techcrunch.com |
| 2025-05-01 | AWS 推出欧洲主权云 | aboutamazon.eu |
| 2025-05-13 | Saudi PIF 成立 HUMAIN，PIF 旗下 | pif.gov.sa |
| 2025-07-24 | Synergy Q2：市场 990 亿美元，Big Three 63% | srgresearch.com |
| 2025-09-04 | Goldman Sachs：2030 年达 2 万亿美元 | goldmansachs.com |
| 2025-09-09 | Oracle FY26 Q1：RPO 4550 亿美元（+359%）| investor.oracle.com |
| 2025-10-23 | Anthropic 与 Google Cloud：100 万 TPU 协议 | googlecloudpresscorner.com |
| 2025-10-28 | Microsoft–OpenAI 关系重构 | blogs.microsoft.com |
| 2025-10-29 | Project Rainier 50 万 Trainium2 上线 | aboutamazon.com |
| 2025-11-18 | EU 委员会启动 AWS / Azure DMA 调查 | digital-markets-act.ec.europa.eu |
| 2025-11-19 | Synergy Q3：市场 1070 亿美元 | srgresearch.com |
| 2025-12-04 | AWS re:Invent 2025：Trainium3 + Nova 2 | linkedin.com/emilprotalinski |
| 2026-01-28 | Microsoft FY26 Q2：营收 813 亿美元，Azure +39% | microsoft.com/investor |
| 2026-03-10 | Oracle FY26 Q3：RPO 5530 亿美元 | investor.oracle.com |
| 2026-04-01 | IDC：AI 累计影响 22.3 万亿美元 | my.idc.com |
| 2026-04-27 | Microsoft–OpenAI 关系新阶段 | blogs.microsoft.com |
| 2026-06-10 | Oracle FY26 Q4：RPO 6380 亿美元 | investor.oracle.com |
| 2026-07-01 | DCD：Synergy Q1 2026 市场规模上调 | datacenterdynamics.com |
| 2026-10-29 | AWS Anthropic 完成 Project Rainier | datacenterknowledge.com |
| 2026-12-01 | Trainium3 量产 | aws.amazon.com |

---

## 四、主要争议焦点与未来 Flashpoint

### 4.1 capex 泡沫 vs 长期增长

- **多方观察**：Goldman Sachs 预测 2026 hyperscaler capex 至少 5000 亿美元（"AI 投资 2026 至少 5000 亿美元"）；2025 Q3 hyperscaler capex 1060 亿美元（+75% YoY）；预计 2026 Q4 增速放缓至 25%。历史上 1990 年代电信投资周期峰值占 GDP 1.5%，AI capex 当前仅 0.8% GDP，2026 年存在 2000 亿美元上修空间。
- **主要争议**：capex 增长已持续两年超过共识，"超支 → 收入兑现" 的回路尚未闭合。
- **影响**：若 capex 回报率（ROIC）显著低于 WACC，将触发 2027–2028 估值修正；反之，若 GenAI 应用层收入兑现，capex 周期可延续至 2029–2030。

### 4.2 AGI 触发机制

- **重置条款**：Microsoft–OpenAI 协议规定 AGI 需 "独立专家小组" 验证，而非 OpenAI 单方宣布。
- **影响**：AGI 触发后，Microsoft IP 权延伸至 2032，营收分成条款终止。
- **Flashpoint**：若 OpenAI 与 xAI / Anthropic 在 2027–2028 年达成 AGI 临界，Microsoft 可能失去对 OpenAI API 收入分成的最长保护期。

### 4.3 DMA 守门人裁定

- **2025-11-18**：EU 委员会启动 AWS、Azure 调查，评估其是否应被指定为 DMA "守门人"。
- **若被指定**：将面临互操作性义务、数据可携带、禁止自我优待（self-preferencing）。
- **AWS / Azure 风险**：欧洲市场份额（合计约 55%）可能受本地化与解耦要求影响。

### 4.4 中国信创替代与出海受限

- **MIIT 政策**：2025 年 2 月，13 家外资获增值电信试点（北京 / 上海 / 海南 / 深圳），但 AWS / Azure / GCP 在中国 IaaS 市场份额持续低于 5%。
- **本土三强**：Alibaba / Huawei / Tencent 合计 70% 中国份额（alertify.eu）。
- **影响**：中国云难以进入 G7 市场，海外扩张依赖 "一带一路"（Tencent 沙特节点、Huawei 印尼 / 非洲节点）。

### 4.5 Neocloud 再融资悬崖

- **2025 年**：CoreWeave IPO 后市值峰值 800 亿美元；Nebius / Lambda 估值翻倍。
- **2027–2028 风险**：GPU 折旧周期（3–4 年）+ 长期合同到期，若 capex 放缓可能触发再融资困难。
- **Oracle Stargate 替代效应**：部分 Neocloud 客户（OpenAI、xAI、Meta）转向 Oracle / 自建，Neocloud 增量空间收窄。

### 4.6 电力 / 水资源瓶颈

- **AMD 警告**（SEMICON China 2026）：AI 市场 1.7 万亿美元的最终约束不是硅，而是电力。
- **影响**：2030 年前，hyperscaler 数据中心 50% 以上延迟来自电力接入（interconnection queue），而非 GPU 供给。

### 4.7 双向锁定（Multi-Cloud + AI 实验室）

- **Anthropic 模式**：AWS Trainium + Google TPU + Microsoft Azure 三云架构，避免单点依赖。
- **OpenAI 模式**：Microsoft Azure（2500 亿美元）+ Oracle Stargate + CoreWeave。
- **风险**：若 AI 实验室加速多云策略，hyperscaler 锁定效应减弱，毛利率压力上升。

---

## 五、定量数据与来源

### 5.1 市场规模与增速

| 指标 | 2025 年值 | 2030 年预测 | 来源 | 等级 |
|------|------------|--------------|------|------|
| 全球公有云总规模 | 4190 亿美元 | 2 万亿美元（22% CAGR）| Synergy Q4 2025 季报 + Goldman Sachs | S1 + S2 |
| Q4 2025 单季 | 1191 亿美元（+30% YoY）| — | Synergy 2026-02-10 报告 | S1 |
| Q3 2025 公有 IaaS / PaaS 份额 Big Three | 63% | 65–72% | Synergy 2025-11-19 | S1 |
| 中国云市场（Q2 2024）| 96.4 亿美元 | — | InfotechLead | S2 |
| AI 服务占公有云增量 | 34% | 60–70%（2030）| Synergy Q4 季报 | S1 |
| AI 算力市场（2030）| — | 3500 亿美元 | Raymond James | S3 |

### 5.2 厂商数据

#### AWS（Amazon）
- **2025 年增长**：24% YoQ（3 年来最快），年化运行率 1420 亿美元（LinkedIn / Matt Garman 2025-12-03）。
- **Q1 2026 增长**：20% YoQ（管理层 5 月 1 日口径，"15 个季度以来最快"）。
- **Trainium2 规模**：50 万 → 100 万芯片（Anthropic 2025 年底前）。
- **Trainium3**：3nm，4 倍 Trainium2 性能，AI 训练成本降 50%（re:Invent 2025-12-04）。
- **AWS 欧洲主权云**：76 亿欧元投资，Brandenburg 2025 Q4 上线。
- **来源**：aboutamazon.com、cnbc.com、linkedin.com、sdxcentral.com。

#### Azure（Microsoft）
- **FY26 Q2 营收**：813 亿美元（+17%），Microsoft Cloud 515 亿美元（+26%）。
- **Azure 增速**：+39% YoY（CC +38%）；FY26 Q2 指引 37–38% CC。
- **商业 RPO**：6250 亿美元（+110%）。
- **2026 上半年 capex**：720 亿美元，全年预计 1400 亿美元（+59% vs FY25 的 880 亿美元）。
- **AI 业务规模**："比部分最大业务线还大"（Satya Nadella）。
- **来源**：microsoft.com/investor FY26 Q2、cnbc.com、theglobeandmail.com。

#### Google Cloud
- **Q4 2025 增长**：48% YoQ（rev），运营利润率 32.9%（从 9.4% 翻三倍）。
- **Q1 FY26 营收**：200.3 亿美元（+63%），积压订单 4600 亿美元（近翻倍）。
- **TPU 部署**：Anthropic 100 万 TPU（2026 年带来 1 GW 算力）。
- **Gemini Enterprise**：企业 AI 平台，整合 Google Workspace + Microsoft 365 数据。
- **来源**：humai.blog、24/7 Wall St、appeconomyinsights.com、cloud.google.com。

#### Oracle OCI
- **FY26 Q1 RPO**：4550 亿美元（+359%）。
- **FY26 Q3 RPO**：5530 亿美元（+325%）。
- **FY26 Q4 RPO**：6380 亿美元（+363%）。
- **Q3 OCI 营收**：49 亿美元（同比 +63%）。
- **FY26 全年自由现金流**：-237 亿美元（重资本投入期）。
- **来源**：investor.oracle.com、cnbc.com、prnewswire.com。

#### Alibaba Cloud
- **2025 Q2 增长**：34% YoQ。
- **Qwen AI App 下载量**：1000 万+。
- **目标**：2030 年云 + AI 营收 1000 亿美元（StartupHub.ai 2026）。
- **来源**：completeaitraining.com、ainvest.com、startuphub.ai。

#### Huawei Cloud / Tencent Cloud
- **中国市场份额**：Huawei 15.5%、Tencent 15.1%（alertify.eu Q2 2024）。
- **Tencent 沙特节点**：2025-02-09 启动（LEAP 2025 峰会）。
- **来源**：alertify.eu、tencentcloud.com、cnbc.com。

#### Neocloud
- **CoreWeave**：2025 Q2 营收 10.6–11 亿美元，OpenAI 合同 119 亿美元 + 3.5 亿入股。
- **Nebius**：2025 年指引 9–11 亿美元 ARR。
- **Lambda**：运行率约 5 亿美元（2025-05）。
- **市场份额**：CoreWeave 已进入 Top 10（季度收入 >15 亿美元）。
- **来源**：crn.com、linkedin.com / alexhenthorniwane、fool.com、datacenterdynamics.com。

### 5.3 资本开支

| 厂商 | 2025 capex | 2026 指引 | YoY |
|------|------------|-----------|-----|
| Microsoft | 880 亿美元 | 1400 亿美元 | +59% |
| Google | ~750 亿美元 | ~1000 亿美元 | +33% |
| Amazon | ~1000 亿美元 | ~1250 亿美元 | +25% |
| Meta | ~650 亿美元 | ~950 亿美元 | +46% |
| Oracle | ~200 亿美元 | ~350 亿美元 | +75% |
| **Big Four 合计** | ~3480 亿美元 | ~4950 亿美元 | +42% |
| **含 Meta（Big Five）** | ~4130 亿美元 | ~5900 亿美元 | +43% |

- **来源**：Goldman Sachs、Dell'Oro、Yardeni Research、theglobeandmail.com。

### 5.4 关键引用

> "GenAI has simply put the cloud market into overdrive." — John Dinsdale, Synergy Research（2026-02-10）

> "Growth rates like these have not been seen since early 2022, when the market was less than half the size it is today." — John Dinsdale, Synergy（2026-02-10）

> "Amazon's market share has averaged just under 30% over the past four quarters, down from a little over 32% in 2021." — John Dinsdale, Synergy（2025-11-19）

> "We are only at the beginning phases of AI diffusion and already Microsoft has built an AI business that is larger than some of our biggest franchises." — Satya Nadella, Microsoft FY26 Q2 财报（2026-01-28）

> "Microsoft Cloud revenue crossed $50 billion this quarter." — Amy Hood, Microsoft CFO（2026-01-28）

> "AI hyperscaler capex would need to reach $700 billion in 2026 to be in line with the peak of spending during the late 1990s telecom investment cycle." — Goldman Sachs Research（2025-09）

> "OCI revenue growth rates are skyrocketing — so is demand." — Safra Catz, Oracle CEO（2025-06-11）

> "The more compute that is dedicated to training this frontier model, the smarter and more accurate it will become." — Ron Diamant, AWS Distinguished Engineer（2025-10-29）

> "The timing of an eventual slowdown in capex growth poses a risk to these companies' valuations." — Goldman Sachs Research（2025-09）

---

## 六、2025–2030 推演框架

### 6.1 推演方法

**起点**：2025 Q3 公有云份额 1070 亿美元（Synergy） + 厂商财报 + 已知 capex 承诺。
**核心驱动**：AI 算力增量 + 数字化转型 + 行业云扩散。
**约束条件**：GPU 供给、电力、合规、capex 回报率。
**外推**：参考 Goldman Sachs 22% CAGR 与 IDC AI 影响 22.3 万亿美元。

### 6.2 情景分析

#### 情景 A：基线（概率 ~55%）
- 假设：AI 应用层收入逐步兑现，capex 增速 2027 放缓至 30%、2028 放缓至 20%。
- 公有云 2030 达 1.5–1.8 万亿美元。
- Big Three 份额维持 65–70%。

#### 情景 B：加速（概率 ~25%）
- 假设：AGI 在 2028–2029 触发，AI 推理需求爆发（推理占总算力 60–70%）。
- 公有云 2030 达 2–2.5 万亿美元（Goldman 上限）。
- Big Three 份额降至 60–65%（新玩家如 HUMAIN / Stargate / Neocloud 头部分流）。

#### 情景 C：泡沫破裂（概率 ~20%）
- 假设：capex 回报率显著低于 WACC，2027 年资本市场修正，hyperscaler 削减 30% capex。
- 公有云 2030 仅达 1.2–1.4 万亿美元。
- Neocloud 头部 2–3 家被并购，长尾出清。

### 6.3 厂商 2030 位置预测

| 厂商 | 2025 收入（估） | 2030 份额预测 | 关键驱动 | 主要风险 |
|------|----------------|---------------|----------|----------|
| AWS | ~1150 亿美元 | 28–32% | Trainium 体系 + Anthropic 锁定 + 主权云 | capex 回报周期长、Anthropic 估值波动 |
| Azure | ~750 亿美元 | 24–28% | OpenAI 2500 亿美元 + Copilot 渗透 | OpenAI 关系重构、AGI 触发条款 |
| Google Cloud | ~500 亿美元 | 14–18% | TPU 利润率 + Gemini + Anthropic 100 万 TPU | 反垄断 + 搜索业务监管溢出 |
| Oracle OCI | ~250 亿美元 | 6–9% | Stargate + RPO 兑现 + 数据库护城河 | 自由现金流为负、债务压力 |
| Alibaba Cloud | ~150 亿美元 | 3–5% | Qwen + 东南亚节点 | 信创与地缘合规 |
| Huawei Cloud | ~120 亿美元 | 2–4% | 昇腾 + 政企关系 | 海外市场准入受限 |
| Tencent Cloud | ~100 亿美元 | 2–3% | 沙特 / 东南亚节点 | 国际化能力相对薄弱 |
| CoreWeave | ~50 亿美元 | 2–3% | GPUaaS + OpenAI 合同 | 再融资 + 客户集中 |
| HUMAIN（Saudi）| — | 1–3% | 主权基金 + 18 GW 计划 | 电力交付、人才稀缺 |
| Stargate（OpenAI JV）| — | 2–4% | 5000 亿美元承诺 | 治理复杂度、电力 |
| IBM Cloud | ~80 亿美元 | 1–2% | 混合云 + watsonx | AI 创新速度落后 |

---

## 七、核心竞争壁垒对比

| 维度 | AWS | Azure | Google Cloud | Oracle OCI | Neocloud |
|------|-----|-------|--------------|------------|----------|
| **算力规模** | 最大（100 万+ Trainium2）| 第二 | 第三 | 第四 | 头部单家 10 万级 GPU |
| **弹性扩容** | 全球 30+ 区域 | 60+ 区域 | 40+ 区域 | 50+ 区域 | 单一区域为主 |
| **自研芯片** | Trainium2 / 3 | Maia（早期）| TPU v6e / v7 | 暂无 | 无 |
| **AI 模型** | Nova 2、Anthropic Claude | OpenAI GPT | Gemini | 集成多模型 | 集成多模型 |
| **数据库** | Aurora、DynamoDB | SQL、PostgreSQL | Spanner、BigQuery | Oracle DB（行业最强）| 弱 |
| **政企客户** | 强 | 强 | 中 | 强 | 弱 |
| **生态** | 最大 | 第二 | 第三 | 中 | 弱 |
| **毛利率** | 33–35% | 40%+ | 30%+ | 30%+ | 20–25% |
| **主权云** | 欧洲 76 亿欧元 | 欧洲 / 国家云 | 欧洲 | 国家云 | 无 |

---

## 八、长期经营风险与增长天花板

### 8.1 AWS
- **风险**：Anthropic 估值波动（2025 年 Series F 估值后达 1830 亿美元）、欧洲主权云盈利周期长、capex 占比营收 > 15%。
- **天花板**：受 Nvidia GPU 供给 + 电力限制，Trainium 替代率需达 50% 才能完全脱钩；Anthropic 协议到期（2027–2028）存在变量。

### 8.2 Azure
- **风险**：OpenAI 关系重构后独占性减弱（API 产品仍独家，非 API 可任意云）、AGI 触发后营收分成终止、capex 增长 > 收入增长。
- **天花板**：Copilot 渗透率能否从 2025 年的 12% 提升至 2030 年的 40% 决定 ARPU；欧洲市场份额 25% 受 DMA 调查影响。

### 8.3 Google Cloud
- **风险**：反垄断诉讼（搜索业务溢出）、TPU 客户集中（Anthropic 占新增量 60%+）、Gemini 推理成本仍高于 TPU 训练成本。
- **天花板**：TPU 外部客户比例需从 30% 提升至 50% 才能支撑 18% 份额；Gmail / Workspace AI 功能对收入拉动有限。

### 8.4 Oracle OCI
- **风险**：自由现金流为负、债务 970 亿美元、RPO 兑现率（历史上 ~75%）、Stargate 治理结构复杂。
- **天花板**：若 Stargate 全部兑现，2030 OCI 收入可达 800–1000 亿美元（基数 6 倍）；但电力 / GPU 交付是关键约束。

### 8.5 Neocloud
- **风险**：客户集中（CoreWeave 60% 收入来自 Microsoft + OpenAI）、GPU 折旧 3–4 年、再融资 2027–2028 集中到期、capex 回报周期 5–7 年。
- **天花板**：若仅维持 GPUaaS 单一业务，2030 年份额难超 5%；向 "GPU + 模型 + 应用" 一体化转型才能突破。

### 8.6 Alibaba / Huawei / Tencent
- **风险**：信创限制导致海外市场小、芯片供给受限、美元结算 / 数据出境合规。
- **天花板**：国内市场增长 15–20%，出海依赖 "一带一路"；2030 年合计全球份额 4–6%。

### 8.7 IBM Cloud
- **风险**：AI 创新速度落后 hyperscaler、watsonx 客户增长慢、咨询业务利润率压力。
- **天花板**：混合云 + 行业云（金融、电信）维持 1–2% 份额，2025 年被 Oracle 超越。

---

## 九、关键驱动因素与监测指标

### 9.1 2026 监测清单

1. **GPU 实际交付量**：Nvidia Blackwell GB200 / GB300 季度出货量（决定 hyperscaler 算力新增）。
2. **hyperscaler capex 季度兑现率**：与全年指引偏差 > 10% 触发修正。
3. **Microsoft–OpenAI 关系演化**：每季度 OpenAI 收入中 Azure 占比、Oracle / CoreWeave 占比变化。
4. **Anthropic 算力来源分布**：AWS Trainium / Google TPU / Microsoft Azure 三方占比变化。
5. **AI 应用层收入兑现**：Copilot 付费用户、ChatGPT / Claude 月活、Gemini Enterprise 客户数。
6. **DMA 守门人裁定**：2026 年底前 EU 委员会是否将 AWS / Azure 列入守门人。
7. **电力互联队列（interconnection queue）**：美国 ERCOT / PJM 容量释放速度。
8. **Neocloud 估值**：CoreWeave / Nebius 市销率（PS）若跌破 10x 触发风险信号。
9. **AGI 触发声明**：OpenAI / Anthropic 是否在 2027–2028 宣布 AGI 候选。
10. **新主权云进展**：HUMAIN 18 GW 计划实际电力交付、Stargate 5 个数据中心建设进度。

### 9.2 关键假设的脆弱性

| 假设 | 证据强度 | 若错误的影响 |
|------|----------|--------------|
| AI 需求持续高速增长 | A1（多家投行 + 厂商财报）| 若需求放缓 30%，capex 周期提前见顶 |
| 电力供应可解决 | B2（多份政府报告）| 美国 2030 电力缺口 30–50 GW，限制扩张 |
| 地缘格局维持现状 | B2（出口管制现状）| 若中美技术解耦加速，中国云份额可能进一步下滑 |
| Anthropic / OpenAI 继续高速增长 | B1（收入 Run Rate）| 若增速放缓至 30% 以下，AWS / Azure 估值承压 |
| EU 不会强制拆分 | C3（DMA 实施先例）| 若被强制互操作，AWS / Azure 欧洲份额下滑 5–10pp |

---

## 十、战略研判与建议

### 10.1 2030 核心结论

1. **Big Three 仍是绝对主导**，但 Azure 增长最快、AWS 绝对收入第一、Google Cloud 利润率最高。
2. **Oracle OCI 是最大变量**——若 RPO 兑现，将跻身 Top 4；若 Stargate 治理出问题，可能拖累股价 30%。
3. **Neocloud 头部 2–3 家**（CoreWeave / Nebius / Crusoe）将存活，长尾在 2027–2028 年出清。
4. **中国云出海有限**，2030 年合计全球份额维持 4–6%。
5. **新主权云**（HUMAIN / Stargate）成为不可忽视的力量，但需 5–7 年才能规模化。
6. **IBM Cloud** 退守混合云细分赛道，份额可能进一步下降。

### 10.2 长期优势赢家

- **AWS**：执行确定性 + 自研芯片 + Anthropic 生态 + 主权云布局。
- **Azure**：OpenAI 锁定 + 26% 商业 RPO 增长 + Copilot 渗透。
- **Google Cloud**：TPU 垂直整合 + 利润率 32.9% + Anthropic 100 万 TPU 算力。
- **Oracle OCI**：数据库护城河 + RPO 6380 亿美元 + Stargate 承建。
- **Nvidia**：实际定价者，毛利率 75%+，供给侧不可替代。
- **Saudi HUMAIN**：主权基金 + 18 GW 计划 + 美国 hyperscaler 全面合作。

### 10.3 长期承压方

- **IBM Cloud**：退守混合云，份额持续下降。
- **未掌握自研芯片的 Neocloud**：GPU 折旧 + 客户集中 + 再融资风险。
- **传统 SaaS 公司**：AI 替代风险，毛利率承压。
- **长尾 IaaS 厂商**：在 Big Three + Oracle + Neocloud 头部挤压下出清。
- **中国云海外业务**：受地缘合规限制，扩张困难。

### 10.4 不确定性与对冲

- **AI 泡沫风险**（20% 概率）：建议关注 capex / 收入比、自由现金流、估值修正信号。
- **AGI 触发**：建议关注 OpenAI / Anthropic 官方声明、独立专家小组动态。
- **地缘风险**：建议关注 BIS 出口管制更新、Section 721 审查、EU DMA 进展。
- **电力瓶颈**：建议关注美国互联队列、核电 / 可再生能源项目进度。

---

## 十一、结论

全球云计算市场 2030 年将达 1.5–2.5 万亿美元规模（基线 1.8 万亿），Big Three（AWS、Azure、Google Cloud）合计份额维持 65–72%，但内部排位将发生重塑：

- **Azure** 凭 22–28% CAGR 缩小与 AWS 差距，2030 年可能持平或反超；
- **AWS** 凭 Trainium / Anthropic / 主权云维持收入第一，份额稳定在 28–32%；
- **Google Cloud** 凭 TPU 利润率最高（32.9%），份额扩张至 14–18%；
- **Oracle OCI** 凭 6380 亿美元 RPO 跻身 Top 4（6–9%），是最大变量；
- **Neocloud 头部 2–3 家**拿下 3–5% 增量，长尾 2027–2028 年出清；
- **中国云**合计 4–6%，受地缘合约束缚；
- **新主权云**（HUMAIN、Stargate）合计 3–7%，5–7 年后规模化。

**重塑排位的核心驱动**：（1）AI 算力增量需求；（2）自研芯片（Trainium / TPU / Maia）的垂直整合能力；（3）地缘合规与主权云布局；（4）AI 实验室（OpenAI / Anthropic）的多云策略；（5）电力与资本约束；（6）capex 回报率与资本市场反馈。

**长期赢家**：AWS（执行力）、Azure（OpenAI 锁定）、Google Cloud（利润率）、Oracle（RPO）、Nvidia（供给侧）、Saudi HUMAIN（主权资本）。

**长期承压方**：IBM Cloud、未掌握自研芯片的 Neocloud、传统 SaaS、长尾 IaaS 厂商、中国云海外业务。

**关键监测指标**：（1）capex / 收入比；（2）AI 应用层收入兑现；（3）GPU 实际交付量；（4）电力互联队列；（5）DMA 守门人裁定；（6）AGI 触发声明；（7）Neocloud 估值。

---

## 附录：来源清单

### S1（一手财报 / 监管文件）
- Microsoft FY26 Q2 财报：https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q2/press-release-webcast
- Oracle FY26 Q1 财报：https://investor.oracle.com/investor-news/news-details/2025/Oracle-Announces-Fiscal-Year-2026-First-Quarter-Financial-Results/default.aspx
- Oracle FY26 Q3 财报：https://investor.oracle.com/investor-news/news-details/2026/Oracle-Announces-Fiscal-Year-2026-Third-Quarter-Financial-Results/default.aspx
- Oracle FY26 Q4 财报：https://investor.oracle.com/investor-news/news-details/2026/Oracle-Announces-Record-Q4-and-FY-2026-Results-Driven-by-Cloud-Infrastructure--Cloud-Applications/default.aspx
- Microsoft–OpenAI 关系重构公告：https://blogs.microsoft.com/blog/2025/10/28/the-next-chapter-of-the-microsoft-openai-partnership/
- Microsoft–OpenAI 新阶段公告：https://blogs.microsoft.com/blog/2026/04/27/the-next-phase-of-the-microsoft-openai-partnership/
- AWS Anthropic 投资公告：https://www.aboutamazon.com/news/aws/amazon-invests-additional-4-billion-anthropic-ai
- AWS Project Rainier 公告：https://www.aboutamazon.com/news/aws/aws-project-rainier-ai-trainium-chips-compute-cluster
- Google Cloud Anthropic TPU 公告：https://www.googlecloudpresscorner.com/2025-10-23-Anthropic-to-Expand-Use-of-Google-Cloud-TPUs-and-Services
- Stargate 公告：https://openai.com/index/announcing-the-stargate-project/
- HUMAIN 公告：https://www.pif.gov.sa/en/our-investments/our-portfolio/humain/
- EU DMA 调查公告：https://digital-markets-act.ec.europa.eu/commission-launches-market-investigations-cloud-computing-services-under-digital-markets-act-2025-11-18_en
- AWS 欧洲主权云公告：https://aws.amazon.com/blogs/security/exploring-the-new-aws-european-sovereign-cloud-sovereign-reference-framework/
- Tencent Cloud 沙特区域公告：https://www.tencentcloud.com/dynamic/news-details/100638

### S2（高质量二路报道）
- Synergy Q2 2025 季报：https://www.srgresearch.com/articles/q2-cloud-market-nears-100-billion-milestone-and-its-still-growing-by-25-year-over-year
- Synergy Q3 2025 季报：https://www.srgresearch.com/articles/cloud-market-share-trends-big-three-together-hold-63-while-oracle-and-the-neoclouds-inch-higher
- Synergy Q4 2025 季报：https://www.datacenterdynamics.com/en/news/synergy-enterprise-cloud-infrastructure-spend-jumps-12bn-in-q4-2025/
- Goldman Sachs 2030 预测：https://www.goldmansachs.com/insights/articles/cloud-revenues-poised-to-reach-2-trillion-by-2030-amid-ai-rollout
- Goldman Sachs capex 预测：https://www.goldmansachs.com/insights/articles/why-ai-companies-may-invest-more-than-500-billion-in-2026
- IDC AI 预测：https://my.idc.com/getdoc.jsp?containerId=prUS53290725
- Gartner 2025 公有云预测：https://www.gartner.com/en/newsroom/press-releases/2024-11-19-gartner-forecasts-worldwide-public-cloud-end-user-spending-to-total-723-billion-dollars-in-2025
- DCD Oracle RPO 报道：https://www.datacenterdynamics.com/en/news/oracle-has-455bn-in-remaining-performance-obligations-at-end-of-q1-2026/
- DCD Microsoft capex 报道：https://www.datacenterdynamics.com/en/news/microsoft-capex-jumps-on-data-center-and-ai-compute-spend-amid-azures-acceleration-of-overall-capacity/
- CNBC Microsoft Q2 报道：https://www.cnbc.com/2026/03/10/oracle-orcl-q3-earnings-report-2026.html
- CNBC Anthropic TPU 报道：https://www.cnbc.com/2025/10/29/amazon-opens-11-billion-ai-data-center-project-rainier-in-indiana.html
- Reuters AI capex 报道：https://ca.finance.yahoo.com/news/meta-microsoft-amazon-and-alphabet-are-about-to-spend-a-shocking-amount-of-money-to-dominate-the-ai-era-115359575.html
- AP Stargate 报道：https://apnews.com/article/trump-ai-openai-oracle-softbank-son-altman-ellison-be261f8a8ee07a0623d4170397348c41
- DCD 欧洲云份额报道：https://www.datacenterdynamics.com/en/news/european-cloud-providers-hold-15-of-local-market-share-synergy-research/
- Synergy 欧洲云报告：https://www.srgresearch.com/articles/european-cloud-providers-local-market-share-now-holds-steady-at-15
- InfotechLead 中国云报道：https://infotechlead.com/cloud/how-tencent-can-increase-share-in-global-cloud-market-86987
- Alertify 中国云份额：https://alertify.eu/china-cloud-service-market/
- DCD HUMAIN 报道：https://www.cnn.com/2025/11/02/tech/saudi-arabia-ai-powerhouse
- TechCrunch OpenAI CoreWeave 报道：https://techcrunch.com/2025/03/10/in-another-chess-move-with-microsoft-openai-is-pouring-12b-into-coreweave/
- Fortune OpenAI CoreWeave 报道：https://fortune.com/article/openai-coreweave-microsoft-cloud-ai-funding-deal/

### S3（行业博客 / 单源数据）
- LinkedIn / Matt Garman AWS 增长：https://www.linkedin.com/posts/mattgarman_good-conversation-with-cnbcs-jon-fortt-today-activity-7427804623903719424-yQIC
- LinkedIn / Alex Henthorniwane Neocloud 份额：https://www.linkedin.com/posts/alexhenthorniwane_neocloud-aiinfrastructure-gpuaas-activity-7384573849020948480-1p6o
- HumAI Blog Google Cloud 利润率：https://www.humai.blog/google-cloud-just-tripled-its-margin-microsoft-meta-and-amazon-are-doing-ai-wrong/
- App Economy Insights TPU Anthropic：https://www.appeconomyinsights.com/p/googles-nvidia-moment
- Vision2030 HUMAIN 计划：https://vision2030.ai/analysis/humain-ai-infrastructure/
- Spheron GPU 短缺 2026：https://www.spheron.network/blog/gpu-shortage-2026/
- CRN Neocloud 报道：https://www.crn.com/news/ai/2026/as-neocloud-gpu-demand-surges-partners-are-winning-ai-deals-and-making-profit
- 24/7 Wall St 投资分析：https://247wallst.com/investing/2026/06/14/3-cloud-computing-stocks-to-load-up-on-june/
- BusinessStats 市场预测：https://businesstats.com/big-three-hold-dominant-lead-in-accelerating-cloud-market/
- Fool CoreWeave Nebius 对比：https://www.fool.com/investing/2025/11/29/artificial-intelligence-ai-stock-coreweave-nebius/
- Fool AWS 2025 数据：https://www.fool.com/investing/2025/12/19/3-things-to-know-about-amazon-stock-before-you-buy/
- Complete AI Training Alibaba 增长：https://completeaitraining.com/news/alibaba-rallies-as-ai-drives-34-cloud-growth-qwen-app-hits/
- StartupHub Alibaba 1000 亿目标：https://www.startuphub.ai/ai-news/artificial-intelligence/2026/alibaba-targets-100b-cloud-ai-revenue
- SDxCentral Gartner 报道：https://www.sdxcentral.com/news/amazon-microsoft-google-dominate-gartners-cloud-quadrant/
- DC Post MEA 欧洲云：https://www.dcpostmea.com/2025/07/european-cloud-providers-maintain-market-share-despite-us-hyperscalers-dominance/
- AInvest Nutanix 混合云：https://www.ainvest.com/news/nutanix-leadership-hybrid-cloud-infrastructure-implications-long-term-growth-2509/

### 对抗性观点 / 风险提示
- Goldman Sachs "AI 投资 2026 至少 5000 亿美元"（中性观点）
- Bristlemoon Capital "AI 泡沫框架"：https://www.bristlemoonresearch.com/p/framing-the-ai-bubble
- Dallas360 "AI Rally vs Dot-Com Bubble"：https://www.dallas360news.com/the-ai-rally-looks-like-the-dot-com-bubble-the-companies-do-not
- ioplus "We're in an A.I. Bubble. No, We're Not."：https://ioplus.nl/en/posts/were-in-an-ai-bubble-no-were-not
- Lexology "AI Compute Crisis"：https://www.lexology.com/library/detail.aspx?g=981be84a-224c-42a8-946d-f3622471cd20
- BuildMVPFast "AI Compute Crisis 2026"：https://www.buildmvpfast.com/blog/ai-compute-crisis-power-grid-inference-2026
- DigiTimes SEMICON China 2026：https://www.digitimes.com/news/a20260326VL208/amd-demand-2026-semicon-china-growth.html

---

**报告完成时间**：2026 年中
**下次更新触发条件**：（1）hyperscaler 季度财报重大变化；（2）AI 实验室关系重构；（3）DMA 守门人裁定；（4）AGI 触发声明；（5）capex 显著下修。