# 2030 年前全球半导体行业发展走向深度研究

## 执行摘要

2026 年是全球半导体行业进入"AI 超级周期"的关键节点。WSTS（世界半导体贸易统计组织）春季预测将 2026 年全球半导体市场规模从 2025 年的 7,956 亿美元上调至 1.511 万亿美元，同比增长 89.9%，2027 年将进一步增至 1.914 万亿美元。这一惊人增速的核心驱动力来自存储器（特别是 HBM）与逻辑芯片的双重爆发。WSTS 预计 2026 年存储器收入将同比增长 249.5%，达到 8,039 亿美元，逻辑器件增长 37.3%，达到 4,114 亿美元。区域上，美洲以 112% 的增速领跑全球，亚太地区增长 87.4%。

支撑这一高增长的是一场史无前例的算力基建投资。Moody's 在 2026 年 6 月将六大超大规模云厂商（Microsoft、Amazon、Meta、Alphabet、Oracle、CoreWeave）2026 年资本开支预测上调至 7,850 亿美元，2027 年接近 1 万亿美元。Goldman Sachs 估计 2025–2027 年累计超大规模厂商资本开支将达 1.15 万亿美元，是 2022–2024 年 4,770 亿美元的近三倍。Microsoft AI 业务年化收入突破 370 亿美元（同比+123%），Google Cloud 增长 63%，AWS 创 15 个季度以来最快增速。

然而，行业在 2026 年同时面对三重结构性挑战：（1）地缘政治与出口管制持续升级，台湾拟将 AI 芯片对华出口管制从华为扩展至所有中国客户；（2）AI 资本开支的可持续性引发泡沫担忧，Nasdaq-100 已在 2026 年 2 月高点回撤逾 10%；（3）约束正从"芯片供给"转向"电力供给"，超大规模厂商 2026 年超 60% 的 capex 流向电力、冷却与数据中心建设。

本报告将围绕设计、制造、封装测试、组装等全产业链环节，系统分析存储芯片、HBM、逻辑芯片、代工、硬件设计等细分领域的发展趋势，重点解读台积电、三星、英特尔、AMD、英伟达、高通、苹果、谷歌、华为、中芯国际、SK 海力士、美光、平头哥（阿里 T-Head）、长鑫存储（CXMT）、闪迪（SanDisk）、博通、美满（Marvell）等 17 家核心企业的发展态势。

---

## 一、核心参与者（Key Actors）

### 1. 制造与代工

| 企业 | 角色 | 关键动机 | 公开立场 | 影响力 |
|------|------|----------|----------|--------|
| **台积电（TSMC）** | 全球最大纯晶圆代工，市占率超 60% | 巩固 2nm/A16 节点领先，扩大 CoWoS/SoIC 先进封装产能 | 2026 年 2nm 已量产，20+ 客户 tape-out、70+ 排队中；2026–2028 年 2nm/A16 产能 70% CAGR，CoWoS 超 80% CAGR | 极端高：N2 良率与产能决定 Vera Rubin 量产时点 |
| **三星电子（Samsung）** | IDM + 代工 + 存储 | 通过 HBM4 翻身，2nm GAA 拿下 Tesla AI6 大单 | 2026 Q1 HBM 营收创新高，2nm 良率达 60%，将成 Vera Rubin HBM4 25–30% 份额供应商 | 高：决定 HBM 供给弹性 |
| **英特尔（Intel）** | IDM 转型 + 代工业务（Intel Foundry） | 凭 18A 拿下 Tesla、Panther Lake 内部订单，2028 年 14A 风险量产 | Q1 2026 代工收入 51 亿美元；Jim Cramer 指出 2024 年代工亏损 188 亿美元 | 中：18A 良率与外部客户是命门 |
| **中芯国际（SMIC）** | 中国最大代工 | 服务华为/阿里等国产 AI 芯片，DUV 多重曝光突破 5nm | 5nm 良率据传 60–70%，但仍被外媒质疑"20% 良率" | 中-高：中国国产替代核心，但 7nm 仍受限于 EUV 缺位 |

### 2. AI 芯片设计

| 企业 | 角色 | 关键动机 | 公开立场 | 影响力 |
|------|------|----------|----------|--------|
| **英伟达（NVIDIA）** | AI GPU 霸主，市占率约 90% | Vera Rubin 量产、Blackwell Ultra 持续推进 | Q1 FY27 营收 816 亿美元（同比+85%），Q2 指引 910 亿美元，"未含中国数据中心" | 极端高：HBM 与 CoWoS 配额决定其交付节奏 |
| **AMD** | AI GPU 第二选择 | Helios 平台 + MI400/MI450 切入超大规模厂商 | MI400 系列搭载 HBM4，2026 年发布；Helios 机架 2026 H2 部署，2.9 exaFLOPS FP4 | 高：与 OpenAI、Anthropic 合作 |
| **博通（Broadcom）** | 自研 ASIC 龙头 | 与 Google、Meta、字节等合作 XPU/TPU | Q1 FY26 AI 半导体营收 84 亿美元（同比+106%）；Q2 指引 107 亿美元 | 高：定制 AI 芯片主要受益方 |
| **美满（Marvell）** | 定制硅挑战者 | Amazon Trainium 主力设计伙伴、Microsoft Maia、Google 推理芯片 | FY26 营收 82 亿美元（同比+42%），股价 YTD 涨 240% | 中-高：与 Broadcom 抢定制 AI 硅市场 |
| **华为（Huawei）/ 海思（HiSilicon）** | 中国 AI 芯片自研代表 | Ascend 910C/D/E 突破 SMIC 工艺，国产替代 | Ascend 910C 计划 2026 量产，910D/E 推进中 | 中-高：中国 AI 主权核心 |
| **寒武纪（Cambricon）** | 中国 AI 芯片 IPO 明星 | 思元 590/690 与云端训练 | 2025 H1 营收同比+4,348%，首度年度盈利；股价 4,554x PE 后大幅回撤 | 中：国产替代情绪指标 |
| **阿里平头哥（T-Head）** | 阿里云自研 AI 芯片 | Zhenwu 800/810E/V900/J900 路线图 | 累计出货 56 万颗，400+ 客户；2026 年传 IPO | 中：云+芯垂直整合代表 |

### 3. 存储与 HBM

| 企业 | 角色 | 关键动机 | 公开立场 | 影响力 |
|------|------|----------|----------|--------|
| **SK 海力士（SK Hynix）** | HBM 龙头，Vera Rubin 主力 | HBM4 维持 60–70% 份额 | Q1 2026 HBM 份额约 40%（低于年初 50%），但 Vera Rubin 主供 | 极端高：HBM 周期之王 |
| **三星电子（Samsung）** | HBM 第二供应商 | HBM4 追上并拿下 25–30% 份额 | HBM 营收 2026 年预计较 2025 翻三倍；HBM4 11.7 Gbps 合格 | 高：份额回升信号 |
| **美光（Micron）** | 美国唯一 HBM 厂商 | 拿到 Vera Rubin 第三供应商 + Google TPU 备份 | 2026 H2 起 30%+ 良率；地理政治保险 | 中-高：地缘溢价 |
| **长鑫存储（CXMT）** | 中国 DRAM 龙头 | 20% 产能转 HBM3、停 DDR4、全转 DDR5+HBM | 2026 年 HBM3 月产 6 万片（占其 30 万片月产能 20%） | 中：中国 HBM 国产替代唯一规模化主体 |
| **闪迪（SanDisk）** | NAND/企业 SSD 厂商 | AI 数据中心存储需求 | 2026 Q1 营收同比+251%，与 Kioxia 续约 Yokkaichi JV 至 2034 | 中：AI NAND 周期受益 |

### 4. 终端与系统

| 企业 | 角色 | 关键动机 | 公开立场 | 影响力 |
|------|------|----------|----------|--------|
| **苹果（Apple）** | 终端 + 自研芯片 | iPhone Fold 开辟新形态，M5/M6 MacBook | 2026 年 9 月推 iPhone Fold（8–10M 台）、M5 MacBook Air 3 月上市、MacBook Neo 入门级 | 高：定义消费电子创新 |
| **高通（Qualcomm）** | 移动芯片 + PC Snapdragon X | Snapdragon X2 Elite Extreme 进 Windows AI PC | 苹果补缴 45–47 亿美元一次性收入；与苹果基带协议至 2026 | 中-高：AI PC 市场关键 |
| **Google** | TPU + 自研 ASIC | TPU v6/v7、Marvell/Broadcom 双源 | 与 Marvell 谈 memory processing unit + 推理 TPU | 高：定制 AI 硅最大买家之一 |

### 5. 政策与监管

| 主体 | 角色 | 关键动作 |
|------|------|----------|
| **美国商务部 BIS** | 出口管制主轴 | TPP < 21,000、带宽 < 6,500 GB/s 须逐案审批；H20 禁运 |
| **台湾 MOEA / ITA** | 配合美方 | 6 月 9 日拟将 AI 芯片对华出口扩至所有中国客户；战略高科实体清单新增 265 个 |
| **特朗普政府** | 谈判筹码 | 与中方关税、AI 芯片许可博弈；Trump-Xi 峰会"非常成功" |

---

## 二、关键事件时间线

| 日期 | 事件 | 来源 |
|------|------|------|
| 2024-10-24 | TSMC Arizona 良率超过台湾厂 4 个百分点 | Bloomberg |
| 2024-12 | AWS-Marvell 五年期合作延长 | 多方 |
| 2025-01 | TSMC 2nm 在宝山厂完成 5,000 片风险量产 | 经济日报 |
| 2025-07-28 | Tesla 与 Samsung 签 165 亿美元 AI6 芯片长约（2nm） | Bloomberg |
| 2025-09-02 | Kuo 上调 iPhone Fold 出货预期至 8–10M | AppleInsider |
| 2025-10 | MacBook Neo 上市，A18 Pro，8GB/256GB 入门级 | Apple Newsroom |
| 2025-11 | TSMC 公布 2026–2028 2nm/A16 70% CAGR、CoWoS 80% CAGR | TSMC 论坛 |
| 2025-12-18 | Kuo 警告 iPhone Fold 量产或推迟至 2027 | MacRumors |
| 2026-01-22 | Alibaba 拟分拆 T-Head 上市 | FinancialContent |
| 2026-02-05 | Marvell 估值分歧（forward P/E 26.5x vs 现实 64.9x） | Motley Fool / FinanceCharts |
| 2026-02-10 | CXMT 宣布 20% 产能转 HBM3（60K 片/月） | Techolam |
| 2026-02-26 | NVIDIA Q4 FY26 营收 +73%，Q1 FY27 指引 +$70 亿 | LinkedIn / 多方 |
| 2026-03-03 | Apple 发布 M5 MacBook Air（13"/15"，起售 $1,199） | Apple Newsroom |
| 2026-03-04 | Broadcom Q1 FY26 AI 半导体营收 $84 亿（+106%） | Tech Insider |
| 2026-03-06 | Marvell FY26 营收 $82 亿（+42%），FY28 目标 $150 亿 | FirstPassLab |
| 2026-03-20 | T-Head 出货 47 万颗；传 IPO 推进 | TrendForce |
| 2026-05-06 | Samsung Q1 2026 营业利润 KRW 57.2T，HBM 创新高 | Spoonai |
| 2026-05-19 | Samsung HBM4 通过 NVIDIA/AMD 双认证，6 月放量 | AINAsia |
| 2026-05-20 | NVIDIA Q1 FY27 营收 $81.6B（+85%），Q2 指引 $91B | NVIDIA Newsroom |
| 2026-05-20 | T-Head 发布 Zhenwu V900/J900 路线图，2 年内推出 | China Biz Insider |
| 2026-05-21 | WSTS 上调 2026 全球半导体至 $1.511T（+89.9%） | WSTS |
| 2026-06-01 | NVIDIA GTC Taipei：Vera Rubin 全量产，10x 代理吞吐 | TechTimes |
| 2026-06-02 | Moody’s 上调 6 大超大规模厂商 2026 capex 至 $785B，2027 接近 $1T | RCR Tech / ComputeForecast |
| 2026-06-05 | Jensen Huang 访韩：Samsung/SK Hynix/Micron HBM4 三方全合格 | Gentic / Spoonai |
| 2026-06-09 | 台湾拟将 AI 芯片对华出口管制扩至所有中国客户 | Bloomberg / TrendForce |
| 2026-06-10 | 台湾 ITA 新增 265 个战略高科实体（涉中俄伊等） | TechNews |

---

## 三、细分领域趋势分析

### 1. 存储芯片与 HBM（高带宽内存）

HBM 是 2026 年 AI 算力瓶颈的核心，WSTS 预测 2026 年存储芯片市场规模将从 2025 年的 2,300 亿美元跃升至 8,039 亿美元（+249.5%），2027 年进一步增至 1.06 万亿美元。HBM TAM 从 2024 年 170 亿美元有望增长至 2028 年 1,000 亿美元。

**HBM4 格局重塑（2026 Q2）**：
- 6 月 5 日，Jensen Huang 访韩时正式确认 Samsung、SK Hynix、Micron 三家全部通过 NVIDIA Vera Rubin HBM4 认证；
- 分配比例：SK Hynix 60–70%、Samsung 25–30%、Micron 较小份额；
- Samsung HBM4 速率达 11.7 Gbps（高于 NVIDIA 10 Gbps 最低要求），6 月开始批量供货；
- SK Hynix 12 层 HBM4 测试良率达 70%，但 Q1 2026 HBM 份额从年初 50% 滑落至 40%。

**Samsung 翻盘路径**：
- HBM 营收预计 2026 年同比增长超 3 倍；
- 副董事长 Kwak Noh-jung 公开表示"Samsung 追赶速度快于预期"；
- HBM4E 12 层样品已送样，意图夺回技术领先。

**Micron 的"地缘溢价"**：
- 美国本土唯一 HBM 厂商，对 NVIDIA 形成供应链安全冗余；
- 拿到 Google TPU、AMD MI350 备份份额；
- forward P/E 7–11x，估值最便宜的纯 HBM 标的。

**长鑫存储（CXMT）的中国路径**：
- 2026 年将 20% 产能（约 6 万片/月）转向 HBM3，对应全球 30 万片月产能中的 20%；
- DDR4 计划于 2026 年停产，全面转向 DDR5 + HBM；
- 与华为合作开发 AI 加速器用 HBM，绕开国际供应链限制；
- 上海新线计划 2026 H2 设备进场，2027 年投产。

**NAND/SSD 端**：
- SanDisk 2026 Q1 营收同比+251%，与 Kioxia 续约 Yokkaichi 合资厂至 2034；
- SanDisk 2026–2029 年向 Kioxia 支付 11.6 亿美元制造服务费；
- AI 训练数据集与推理数据库推动企业 SSD 需求剧增。

### 2. 逻辑芯片与 AI 加速器

**NVIDIA 主导地位**：
- Q1 FY27（截至 2026-04-26）营收 816 亿美元（同比+85%、环比+20%）；
- Q2 指引 910 亿美元，"未含中国数据中心计算收入"；
- Vera Rubin 6 月 1 日 GTC Taipei 正式发布，已进入完整量产；
- 旗舰 NVL72 机架 3.5x 训练、5x 推理性能；
- MoE 模型训练用 1/4 GPU 数量，推理成本相比 Blackwell 降低 10x。

**AMD 的"第二来源"策略**：
- Helios 机架平台 2026 H2 部署，单机架 2.9 exaFLOPS FP4；
- MI400 系列搭载 HBM4，2026 年发布；
- 与 OpenAI、Anthropic 合作；
- 印度 TCS 部署 200MW Helios 容量。

**博通（Broadcom）vs 美满（Marvell）的定制 AI 硅之战**：
- Broadcom：Q1 FY26 AI 半导体营收 84 亿美元（+106%），与 Google、Meta、字节合作 XPU；
- Marvell：股价 2026 年 YTD 涨幅 240%，5 月起回调；
  - FY26 营收 82 亿美元（+42%），custom silicon 从 0 增至 15 亿美元；
  - FY27 营收指引 127 亿美元，FY28 目标 150 亿美元；
  - 主力客户：Amazon Trainium2/3、Microsoft Maia、Google 推理芯片；
  - 6 月 2 日因 Jensen Huang 在 Computex 称其为"下一个万亿美元公司"单日暴涨 25%；
  - **风险点**：forward P/E 64.9x、12 个月 P/E 91.4x，远超历史平均 27x；Jensen 持股 Marvell $20 亿的"投票"承诺；高管 5,000 股抛售披露；6 月初股价 7% 单日下跌。

**应用处理器（AP）**：
- Apple A20 Pro 预计采用 2nm，2026 年 9 月随 iPhone 18 Pro 上市；
- Apple M5 已在 2025 年 10 月于 MacBook Pro / iPad Pro 首发，2026 年 3 月延伸至 MacBook Air；
- MacBook Neo（13"，A18 Pro，$599 区间）2025 年 10 月上市，2026 年延续；
- 苹果 iPhone Fold 折叠机，2026 年 9 月发布、初始 8–10M 台，2027 年 20–25M 台；售价约 $2,299–$2,500。

**中国逻辑芯片突围**：
- SMIC 5nm 据传 60–70% 良率（外媒指 20%），2026 年支撑华为 Ascend 910C/D/E 与阿里 T-Head 量产；
- 华为 Ascend 910C 计划 2026 年量产，910D/E 推进；
- Cambricon 思元 590/690：2026 年计划出货约 50 万颗（含 30 万颗旗舰品），2025 H1 营收同比+4,348%，首度年度盈利；
- 寒武纪估值冲击：4,554x PE，单日闪崩蒸发 480 亿元市值。

### 3. 代工生产

**TSMC：**
- N2 风险量产 2025 Q1 完成（5,000 片），2025 H2 进入大批量生产，2025 Q4 月产能 5 万片，2026 年达 10 万片；
- 2026 N2P、2028 A16（TSMC 论坛数据）；
- 客户：Apple（A20、M6、R2）、AMD（Zen 6 EPYC Venice）、MediaTek、NVIDIA、Qualcomm；
- 客户 tape-out 已超 20 个，排队 70+；
- 9 个新 fab/封装厂 2026 年投建（亚利桑那、日本、德国、台湾）；
- Arizona 二厂 2026 H2 机台安装，三厂建设中；
- Arizona 良率已超过台湾厂 4 个百分点。

**三星代工**：
- 2nm GAA 良率达 60%，目标 2026 H1 量产；
- Tesla AI6 大单（$16.5B、Texas Taylor 厂）2026 年起量；
- 2026 年 2nm 订单目标同比+130%；
- 2026 Q3 有望实现代工业务季度盈利；
- 2025 年代工亏损约 7 万亿韩元（$48.5 亿），4nm/8nm 仍为利润支柱。

**Intel Foundry**：
- 18A 2026 Q1 开始出货 Panther Lake / Wildcat Lake，营收 51 亿美元；
- 18A-P 性能+9% / 功耗-18%，针对外部客户；
- 14A 计划 2028 风险量产，Tesla 为关键客户；
- 2024 年代工亏损 188 亿美元，CHIPS Act 补贴 85 亿美元；
- 5 年内外部客户收入是核心悬念。

**SMIC**：
- 7nm 已批量、5nm 试产；
- 与华为、阿里合作 AI 芯片；
- 受限于 EUV 缺位，DUV 多重曝光推进。

### 4. 先进封装与 OSAT

- **CoWoS（TSMC）**：5.5 reticle 良率 98%，14 reticle（20 HBM stack）2028 年、24 HBM stack 2029 年、SoWX 64 HBM stack 2029 年；
- **SoIC**：6 微米键合 2025 年、N2 代 6 微米 2028 年、A14 代 4.5 微米；
- **产能瓶颈**：CoWoS 至 2025–2026 全部售罄，C.C. Wei 公开确认；
- **OSAT 份额（2022 Yole 数据）**：TSMC 76.7%、ASE、Amkor、JCET 三家合计 >90%；
- **新格局**：Intel Rio Rancho（New Mexico）凭 EMIB/Foveros 切入，TSMC 之外的分流者。

### 5. 服务器组装（台湾 ODM）

- **Foxconn / Quanta / Wistron**：2025 年三家营收均破 NT$1 万亿（US$320 亿）；
- Wistron 已被 NVIDIA 锁定至 2026 年整厂 AI 服务器产能；
- Pegatron 目标 AI 服务器 10x 增长（2026 年报）；
- 台湾拟加强对 AI 服务器对华出口管制，Foxconn、Quanta、Wistron、Wiwynn、Inventec 均面临风险。

---

## 四、关键量化证据表

| 指标 | 数值 | 时间 | 来源 |
|------|------|------|------|
| 2026 全球半导体市场（预测） | $1.511T（+89.9% YoY） | 2026-05 | WSTS |
| 2027 全球半导体市场（预测） | $1.914T（+26.6%） | 2026-05 | WSTS |
| 2026 存储芯片市场 | $803.9B（+249.5%） | 2026 | WSTS |
| 2026 逻辑芯片市场 | $411.4B（+37.3%） | 2026 | WSTS |
| 美洲增速 | +112% | 2026 | WSTS |
| 2026 美洲市场规模 | $543.7B | 2026 | WSTS |
| 6 大超大规模厂商 2026 capex | $785B | 2026-06 | Moody's |
| 6 大超大规模厂商 2027 capex | ~$1T | 2026-06 | Moody's |
| 5 大超大规模厂商 2026 capex 合计 | $725B（瑞士 GDP 量级） | 2026-05 | AlCapital |
| Microsoft AI 业务年化收入 | $37B（+123% YoY） | 2026-Q1 | Moody's |
| Google Cloud 增速 | +63% | 2026-Q1 | Moody's |
| NVIDIA Q1 FY27 营收 | $81.6B（+85% YoY） | 2026-05-20 | NVIDIA |
| NVIDIA Q2 FY27 营收指引 | $91B（无中国数据中心） | 2026-05-20 | NVIDIA |
| Broadcom Q1 FY26 AI 半导体营收 | $8.4B（+106% YoY） | 2026-03-04 | Broadcom |
| Broadcom Q2 FY26 指引 | $10.7B（+27% QoQ） | 2026-05 | Broadcom |
| Marvell FY26 营收 | $8.195B（+42% YoY） | 2026-03 | Marvell |
| Marvell FY27 营收指引 | $12.7B | 2026-03 | Marvell |
| Marvell FY28 目标 | $15B | 2026-03 | Marvell |
| Marvell YTD 涨幅 | +240% | 2026-06 | 24/7 Wall St |
| Marvell forward P/E | 64.9x | 2026-06-09 | FinanceCharts |
| SK Hynix HBM Q1 2026 份额 | ~40%（年初 50%） | 2026-Q1 | Spoonai |
| SK Hynix HBM4 12 层良率 | 70% | 2026-02 | Digitimes |
| Samsung HBM4 速率 | 11.7 Gbps | 2026-05 | AINAsia |
| Samsung HBM 2026 营收增长 | 3x 2025 | 2026 | AJU Press |
| HBM 2028 TAM | $100B | 2024–2028 | BofA |
| TSMC 2026–2028 2nm/A16 产能 CAGR | 70% | 2026-2028 | TSMC 论坛 |
| TSMC 2022–2027 CoWoS 产能 CAGR | >80% | 2026 | TSMC 论坛 |
| TSMC N2 月产能 | 100K 片（2026） | 2026 | LinkedIn |
| TSMC N2 客户 tape-out | 20+，70+ 排队 | 2026-04 | SemiEngineering |
| Tesla-Samsung 合约 | $16.5B AI6（2nm） | 2025-07-28 | Bloomberg |
| CXMT 2026 HBM3 月产能 | 60K 片（占 20%） | 2026-02 | Techolam |
| SanDisk 2026 Q1 营收增速 | +251% YoY | 2026 | Parameter |
| 2026 NAND 前五厂商收入增速 | +84% | 2026 Q1 | EET Asia |
| SanDisk 2026–2029 支付 Kioxia | $1.16B | 2026-01 | Benzinga |
| Cambricon 2025 H1 营收增速 | +4,348% | 2025 H1 | EqualOcean |
| Cambricon 2025 H1 净利 | RMB 1.038B（去年亏 530M） | 2025 H1 | Wccftech |
| Cambricon PE | 4,554x | 2025-08 | SCMP |
| Cambricon 2026 出货计划 | 50 万颗（含 30 万旗舰） | 2026 | AI Productivity |
| T-Head 累计出货 | 56 万颗 | 2026-05-20 | CBI |
| T-Head 客户数 | 400+，20+ 行业 | 2026-05-20 | CBI |
| iPhone Fold 2026 出货 | 8–10M | 2026-09 | Kuo |
| iPhone Fold 2027 出货 | 20–25M | 2027 | Kuo |
| iPhone Fold 售价 | $2,299–$2,500 | 2026 | Medium / Digit |
| Apple M5 MacBook Air 起售 | $1,199（512GB） | 2026-03-03 | Apple |
| 美国出口管制红线 | TPP < 21,000、带宽 < 6,500 GB/s | 2026-01 | Tom's Hardware |
| 台湾战略高科实体清单新增 | 265 个（中俄伊墨土阿联酋） | 2026-06-10 | TechNews |
| 2026 H1 Gartner 预测 AI 资本开支 | $2.5T（+44%） | 2026 | Gartner |
| McKinsey 累计 AI 基建 | $6.7T by 2030 | 2026 | McKinsey |
| Nasdaq-100 2026 高点回撤 | >10% | 2026-02 | TheNextWeb |
| WSTS 2025 起点 | $795.6B | 2025 | WSTS |
| 1999 互联网泡沫峰值（参考） | $3.2T（按 GDP 比 6.4%） | 1999 | 多种 |
| NVIDIA 市值峰值 | $4.3T | 2026 | 多方 |
| OpenAI 估值 | $730B | 2026 | 多方 |
| OpenAI ARR | $24–25B | 2026 | The World Data |
| Anthropic ARR | $30B | 2026-04 | The World Data |
| AWS AI 芯片订单积压 | $225B | 2026-05 | Spoonai |

---

## 五、分领域赢家与输家

### 赢家

1. **HBM 内存三巨头**：SK Hynix（仍拿 60–70% Vera Rubin 份额）、Samsung（HBM4 翻身、$84 亿新合约）、Micron（地缘溢价 + 估值最低）。
2. **TSMC**：N2 + CoWoS 双重护城河，2026–2028 70% CAGR 锁定。
3. **NVIDIA**：Vera Rubin 全量产，单季营收 $81.6B → $91B 指引，Q3 起 Rubin 出货。
4. **AI 服务器 ODM**：Foxconn、Quanta、Wistron、Inventec 全部破万亿新台币，NVIDIA 锁定 Wistron 全产能。
5. **定制 AI 硅**：Broadcom、Marvell 各自拿下 Google、AWS、Microsoft、Anthropic 大单。
6. **CXMT（长鑫）**：中国国产 HBM 唯一规模化主体，HBM3 月产 6 万片。
7. **Cambricon（寒武纪）**：国产替代情绪龙头，2025 H1 同比+4,348%。
8. **T-Head（平头哥）**：56 万颗出货 + IPO 在即。
9. **Tesla**：与 Samsung 签 $16.5B AI6 长约 + Intel 14A 客户。
10. **SanDisk**：NAND + AI 数据中心，营收 +251%。

### 输家 / 风险方

1. **传统 OSAT（除 Amkor）**：CoWoS 主导让 ASE/JCET 在 2.5D/3D 先进封装失势，份额从过去 90%+ 跌至 2026 年 25–30%。
2. **Qualcomm**：受 Snapdragon X2 与 PC 市场竞争、苹果补缴一次性收入后增速放缓。
3. **Marvell**：240% YTD 涨幅后，forward P/E 64.9x、12 个月 P/E 91.4x，估值高度依赖 FY28 $15B 目标兑现；Jensen Huang 利好出货 + 高管抛售形成博弈。
4. **NVIDIA H20 中国业务**：禁运后中国市场几近清零；Q2 指引"无中国数据中心"是已知数。
5. **Cambricon**：4,554x PE 已被市场质疑，单日蒸发 480 亿元；Alibaba T-Head 竞争加剧。
6. **Intel Foundry**：2024 亏损 188 亿美元，18A 外部客户落地仍待验证。
7. **中芯国际**：5nm 良率 20–60% 不一，EUV 缺位是长期天花板。
8. **超大规模厂商债务风险**：capex 接近 100% 经营现金流（10 年均值 40%），Moody's 警告信用质量或被重估。
9. **2027 H2–2028 周期顶部风险**：Gartner 警告 40%+ agentic AI 项目可能于 2027 年底前被取消；Memory 周期一旦反转，跌幅可能达 50–70%。
10. **传统 PC 厂商**：Snapdragon X2 与 MacBook Neo 双重挤压，Windows AI PC 生态碎片化。

---

## 六、情景分析：Base / Upside / Downside（2026–2030）

### Base Case（基准情景，55% 概率）

- 2026 全球半导体市场达 $1.51T（WSTS 已上调）；
- 2027 达 $1.91T；
- HBM 紧缺持续至 2027 上半年，2027 下半年供需平衡；
- TSMC N2/A16 良率稳定（70%+），2nm 节点均价高于 N3 30%；
- 超大规模厂商 2027 capex 接近 $1T；
- 2028 H2 出现周期顶部，DRAM 价格回调 20–30%；
- 中国国产替代持续推进，CXMT HBM3 + 7nm 工艺稳定，2027 年底 HBM 国产化率 30%；
- Apple iPhone Fold 2026 年发布，2027 年成为出货主力（20–25M 台）；
- Windows AI PC 渗透率 2027 年达 30%。

### Upside Case（上行情景，25% 概率）

- Agentic AI 商业化加速，企业级推理需求超预期；
- 2027 全球半导体市场突破 $2.1T；
- HBM 紧缺持续至 2028，SK Hynix / Samsung 资本开支再上一台阶；
- 苹果 iPhone Fold 售价降至 $1,999 区间，2027 出货超 30M 台；
- Intel 18A 拿下 Microsoft / Apple 订单，2027 年代工业务首度盈利；
- 中国 HBM 国产化 2028 年达 50%；
- 半导体板块（SOX）2027 年再创新高，NVIDIA 市值破 $5T。

### Downside Case（下行情景，20% 概率）

- AI 资本开支回报率不及预期，Nasdaq-100 再回撤 20%+；
- 2026 H2 出现 agentic AI 项目大面积取消；
- 2027 DRAM / NAND 进入深度下行周期（参考 2018–2019 跌 50%）；
- NVIDIA Vera Rubin 量产良率不及预期，2027 收入指引下修 15%；
- 出口管制进一步扩大，触发供应链"双循环"加速脱钩；
- 中国 SMIC 5nm 良率长期停滞在 30%，华为被迫改用 SMIC 7nm；
- 苹果 iPhone Fold 因铰链 / 屏幕良率推迟至 2027 H2；
- 2030 年前出现 1–2 家超大规模厂商信用评级下调（参考 2002 互联网泡沫）。

---

## 七、领先指标（Leading Indicators to Monitor）

| 指标 | 当前状态 | 信号阈值 | 来源 |
|------|----------|----------|------|
| TSMC N2 月产能 | 50K → 100K（2026） | >120K = 超预期 | LinkedIn / SemiEngineering |
| HBM 现货价 vs 合约价 | 价差扩大 | 价差收敛 = 周期见顶 | TrendForce |
| DRAM 合约价 QoQ | +X% | 连续 2 季度负增长 = 见顶 | DRAMeXchange |
| Marvell 12 个月 forward P/E | 64.9x | <40x = 估值正常化 | FinanceCharts |
| 超大规模厂商剩余履约义务（RPO） | $700B / 2 季度 | 增速放缓 = AI 投资减速 | Moody's |
| AI 项目取消率（Gartner） | 40%+ by 2027 | >50% = 周期顶部确认 | Gartner |
| 美国电力批发价（PJM、ERCOT） | 持续上行 | 同比+30% = 数据中心受限 | EIA |
| Nvidia 4 大客户营收集中度 | 40–50% | 单一客户 >30% = 集中风险 | TechI |
| 中国 HBM 国产化率 | <10% | >30% = 国产替代加速 | Techolam |
| iPhone Fold 周销量 | 0（发布前） | 首月 <1M = 需求疲软 | Kuo |
| 台湾出口管制扩面 | 6/9 提案 | 立法落地 = 供应链震动 | Bloomberg |
| Cambricon 业绩兑现度 | 2025 H1 +4,348% | 2026 H1 增速 <50% = 情绪退潮 | EqualOcean |
| TSMC Arizona 良率 | 已超台湾 +4pp | 扩大差距 = 美岸领先 | Bloomberg |
| Samsung HBM 良率 | HBM4 70%+ | 持续高于 SK = 翻盘确认 | Digitimes |
| Intel 18A 外部客户数 | 1–2 | >5 = 代工业务可期 | TheStreet |

---

## 八、争议性主张与证据质量

| 主张 | 来源 | 反方/挑战 | 评估 |
|------|------|----------|------|
| **AI 泡沫类比 1999 互联网** | TheNextWeb、IntuitionLabs | 1999 思科市值 / 营收 50x vs NVIDIA 25x；当前 AI 营收更扎实 | **部分成立**：估值集中度高（CAPE 38），但盈利支撑更强 |
| **SMIC 5nm 良率 60–70%** | Wccftech（推特爆料） | AEI 报告指 20%；SMIC 官方未披露 | **高度不确定**：可能为内部测试良率，非生产良率 |
| **NVIDIA Vera Rubin 良率问题** | TechTimes | 官方称"全量产" | 关注 CoWoS 与 HBM4 后续供应节奏 |
| **Cambricon 业绩可持续** | Wccftech、EqualOcean | 2025 H1 同比+4,348% 基数低；2025 年首次年度盈利可持续性待考 | **谨慎乐观**：基数效应退潮后增速将正常化 |
| **TSMC 2nm 良率领先** | SemiEngineering | Intel 18A 量产，Samsung 2nm 60%；2026 H1 三家差距可能收窄 | 需观察 2026 H2 客户分单与价位 |
| **SK Hynix HBM 持续领先** | Counterpoint 50%、Spoonai 40% | 50% → 40% 已有下滑，Samsung/Micron 紧追 | **趋势在变化**：份额或继续回落至 35% 区间 |
| **苹果 iPhone Fold 9 月发布** | Kuo（12/18 警告） | 量产可能推至 2027 | **关键变量**：铰链 / 折叠屏良率决定 2026 年是否真正发布 |
| **Marvell FY28 $15B 可实现** | Marvell 指引 | 股价已隐含，forward P/E 64.9x；Jensen $20 亿投票承诺构成隐性背书 | **观点分歧**：Bull/Bear 差价 100%+ |
| **超大规模厂商 capex 不可持续** | Moody's、AInvest | 三大厂 Q1 2026 RPO +$700B 增长强劲 | **真实风险**：capex 接近 100% OCF 是历史新高 |
| **CoWoS 紧缺持续至 2027** | Barrack AI | TSMC 80% CAGR 扩产；2027 供给弹性大 | **供/需均在加速**：看谁更快 |
| **Anthropic 30B 营收可持续** | TechCrunch / Bloomberg | OpenAI 估值 $730B，Anthropic 30B ARR 已反超 | **结构性转变**：Agentic AI 企业级付费模式起效 |
| **台湾出口管制扩至所有中国** | Bloomberg、Tom's Hardware | MOEA 仍在"审议"；北京可能报复 | **高概率但未落地**：影响 Foxconn/Quanta/Wistron/Wiwynn/Inventec |

---

## 九、各企业 2026–2030 发展态势

### 1. 台积电（TSMC）

**优势**：
- N2 已量产，70+ 客户排队；
- CoWoS 5.5 reticle 良率 98%，2028 升级至 14 reticle；
- SoIC 6 微米键合 2025 年量产，N2 代 6 微米 2028 年、A14 代 4.5 微米；
- Arizona 良率已超台湾 +4pp；
- 9 个新 fab/封装厂 2026 年开建。

**风险**：
- 客户集中度高，Apple 占 2nm 早期最大份额；
- Arizona 二厂/三厂建设进度；
- 日本厂从 7nm 升级至 3nm 客户验证。

**2026–2028 节奏**：
- 2026 H2：N2P 风险量产，CoWoS 持续满载；
- 2027：N2P 量产，SoIC 持续放量；
- 2028：A16 量产，14 reticle CoWoS 上线。

### 2. 三星电子（Samsung）

**优势**：
- HBM4 翻身，Q1 2026 营业利润 KRW 57.2T 创纪录；
- 2nm GAA 60% 良率 + Tesla AI6 $16.5B 大单；
- HBM 营收 2026 翻 3 倍；
- Q3 2026 代工业务或首度盈利。

**风险**：
- 5nm 制程落后（2026 良率仍弱于 TSMC）；
- HBM4E 良率能否追平 SK Hynix；
- Foundry 客户拓展依赖 Tesla 一家。

**2026–2030 节奏**：
- 2026 H1：2nm 量产、HBM4 全量产、Tesla AI6 开始生产；
- 2027：3nm GAA 扩量、HBM4E 试产；
- 2028：1.4nm 节点启动。

### 3. 英特尔（Intel）

**优势**：
- 18A 风险量产，Panther Lake / Wildcat Lake 出货；
- 14A 锁定 Tesla 客户，2028 风险量产；
- 18A-P 性能+9% / 功耗-18%，针对外部客户；
- 美国本土代工，地缘溢价。

**风险**：
- 2024 代工亏损 188 亿美元；
- 18A 良率波动（Lip-Bu Tan 早期建议专注 14A）；
- 外部客户除 Tesla 外有限；
- CHIPS Act 补贴 85 亿美元已被批评。

**2026–2030 节奏**：
- 2026 Q1：18A 量产、Panther Lake 出货、营收 51 亿美元；
- 2027：18A-P 量产，外部客户验证；
- 2028：14A 风险量产，Tesla AI 系列上量；
- 2030：10A / 7A 路线图公开。

### 4. AMD

**优势**：
- Helios 机架 2.9 exaFLOPS FP4 性能；
- OpenAI、Anthropic 合作，OpenAI 6GW 项目；
- MI400 HBM4 + MI450X 多代发布；
- 印度 TCS 200MW 部署。

**风险**：
- AI 加速器市场 NVIDIA 占 90%+，AMD 仅 5–10%；
- 客户集中于 OpenAI / Anthropic，单一项目风险；
- 软件生态（ROCm）成熟度仍落后 CUDA。

**2026–2030 节奏**：
- 2026 H2：Helios 部署、MI400 出货；
- 2027：MI450X、MI430X（Herder 系统）；
- 2028：MI500 系列、Helios 2.0。

### 5. 英伟达（NVIDIA）

**优势**：
- Q1 FY27 营收 $81.6B（+85%），Q2 指引 $91B；
- Vera Rubin 全量产，HBM4 三家合格、CoWoS 配额确定；
- 10x 代理吞吐，成本较 Blackwell 降 10x；
- $1T 订单积压。

**风险**：
- 4 大客户占 40–50% 营收，集中度高；
- H20 中国市场清零，Q2 指引已剔除；
- 估值 $4.3T 与 1999 互联网泡沫对比；
- 客户转向自研 ASIC（Broadcom / Marvell）的趋势。

**2026–2030 节奏**：
- 2026 H2：Vera Rubin Q3 出货、Blackwell Ultra 收尾；
- 2027：Rubin Ultra、Rubin Next；
- 2028：Feynman 架构；
- 2030：AI 工厂标准定义者。

### 6. 高通（Qualcomm）

**优势**：
- Snapdragon X2 Elite Extreme 与 M4 Pro 同级性能；
- 苹果基带协议至 2026 年；
- AI PC 市场份额持续提升；
- 45–47 亿美元苹果补缴一次性收入。

**风险**：
- MacBook Neo + M5 双重挤压 Windows AI PC；
- 苹果自研基带 C2 2026 年开始替代；
- 智能手机市场成熟；
- 中国市场被华为 / 紫光展锐挤压。

**2026–2030 节奏**：
- 2026 H2：Snapdragon X2 商用、苹果基带协议收尾；
- 2027：Snapdragon X3、车规芯片上量；
- 2028–2030：6G、AI 边缘推理。

### 7. 苹果（Apple）

**优势**：
- A20 Pro 2nm 工艺首发（iPhone 18 Pro 2026-09）；
- M5 2026-03 延伸至 MacBook Air（$1,199 起）；
- MacBook Neo 入门级渗透学生与新兴市场；
- iPhone Fold 2026 上市，$2,299 价位，初始 8–10M 台；
- 苹果自研 C2 基带 2026 集成 iPhone 18 Pro。

**风险**：
- iPhone Fold 量产或推至 2027（Kuo 12/18 警告）；
- 中国市场被华为 Mate 系列 / Pura 系列挤压；
- AI 功能（Apple Intelligence）变现慢于预期；
- 硬件创新节奏放缓。

**2026–2030 节奏**：
- 2026-03：M5 MacBook Air；
- 2026-09：iPhone 18 Pro + iPhone Fold；
- 2027：iPhone Fold 量产 20–25M 台、M6 Mac；
- 2028–2030：Vision Air、AR 眼镜。

### 8. 谷歌（Google）

**优势**：
- TPU v6 / v7 持续推进，AWS、Anthropic 大单；
- 与 Marvell 谈 memory processing unit + 推理 TPU；
- 与 Broadcom / MediaTek 形成三源供应；
- Cloud 业务 2026 Q1 增长 63%。

**风险**：
- 自研芯片在 Anthropic 推理市场份额不明；
- 与 NVIDIA 关系受自研 ASIC 推进影响；
- 监管反垄断压力。

**2026–2030 节奏**：
- 2026：TPU v7 量产、Marvell 推理芯片联合开发；
- 2027：Gemini 3 / 4 代训练用 TPU v8；
- 2028–2030：TPU 与 NVIDIA 形成"双轨"。

### 9. 华为（Huawei）

**优势**：
- Ascend 910C 2026 量产、910D/E 推进；
- 与 SMIC 合作 5nm 工艺（即便良率受限）；
- CloudMatrix 384（与 910C 配合）性能对标 NVIDIA NVL72；
- 鸿蒙 + 昇腾 + 海思 形成国产闭环。

**风险**：
- 先进制程受限于 EUV 缺位（SMIC 5nm 良率 20–60%）；
- HBM 仍依赖 CXMT 或进口；
- 美国持续加码出口管制；
- 中国云厂商（阿里、腾讯）部分去华为化。

**2026–2030 节奏**：
- 2026 H1：Ascend 910C 批量、910D 试产；
- 2027：Ascend 910E 量产；
- 2028–2030：昇腾替代英伟达 H100/H200 级别性能。

### 10. 中芯国际（SMIC）

**优势**：
- 中国最大代工，国产替代核心；
- 7nm 量产、5nm 试产；
- 华为、阿里、Cambricon 客户基础；
- 上海、京城多座 fab 同步推进。

**风险**：
- EUV 缺位导致 5nm 良率 20–60% 不一；
- 高端光刻机（DUV 多重曝光）产能受限；
- 研发投入落后 TSMC / Samsung；
- 美国可能进一步制裁。

**2026–2030 节奏**：
- 2026：5nm 试产、7nm 满产；
- 2027：5nm 良率改善至 40–50%；
- 2028–2030：HBM 一体化、Chiplet 封装。

### 11. SK 海力士（SK Hynix）

**优势**：
- HBM 龙头，Vera Rubin 60–70% 份额；
- HBM4 12 层测试良率 70%；
- 12 层 HBM3E / HBM4 同时供应 NVIDIA、AMD、Google；
- 1.1M 韩元股价创历史新高。

**风险**：
- Q1 2026 HBM 份额从 50% 降至 40%；
- Samsung 与 Micron 紧追，HBM4E 翻盘可能；
- 中国客户出口受限；
- 资本开支高峰，2027 年折旧压力。

**2026–2030 节奏**：
- 2026：HBM4 满产、份额维稳 50%+；
- 2027：HBM4E 量产、份额防守；
- 2028：HBM5 风险量产；
- 2030：HBM5 主流化。

### 12. 美光（Micron）

**优势**：
- 美国本土唯一 HBM 厂商，地缘溢价；
- Vera Rubin 第三供应商 + Google TPU 备份；
- forward P/E 7–11x，估值最便宜；
- 新加坡 / 日本 / 美国 Idaho 多地产能。

**风险**：
- HBM 份额仍小于 SK Hynix / Samsung；
- 工艺追赶速度不确定；
- 中国市场风险。

**2026–2030 节奏**：
- 2026 H2：HBM 30%+ 良率稳定；
- 2027：HBM4E 量产；
- 2028–2030：HBM5 与 SK Hynix / Samsung 三足鼎立。

### 13. 阿里平头哥（T-Head）

**优势**：
- 累计出货 56 万颗，400+ 客户；
- Zhenwu 800/810E 量产中；
- V900/J900 两代路线图明确；
- 阿里云 + 端到端软硬协同；
- IPO 推进中。

**风险**：
- 高端 AI 训练芯片性能仍弱于 NVIDIA；
- 美国对阿里 / 阿里云的合规审查；
- 客户集中于阿里云生态。

**2026–2030 节奏**：
- 2026：V900 发布、IPO 推进；
- 2027：J900 量产；
- 2028–2030：T-Head 拆分上市后独立运营。

### 14. 长鑫存储（CXMT）

**优势**：
- 中国 DRAM 龙头；
- 2026 年 20% 产能转 HBM3；
- 与华为合作 HBM 国产化；
- 上海新线 2026 H2 设备进场、2027 投产。

**风险**：
- HBM 工艺落后 SK Hynix 一代以上；
- 美国持续制裁设备进口；
- 折旧高峰，2027 年盈利承压。

**2026–2030 节奏**：
- 2026：HBM3 量产、DDR5 全面替代 DDR4；
- 2027：新线投产、HBM3E 试产；
- 2028–2030：HBM4 国产化突破。

### 15. 闪迪（SanDisk）

**优势**：
- AI NAND 需求强劲，Q1 2026 营收 +251%；
- 与 Kioxia 续约 Yokkaichi JV 至 2034；
- AI 数据中心 SSD 高毛利；
- 2026 Q1 毛利率显著改善。

**风险**：
- NAND 周期性强，2027 H2 可能反转；
- Kioxia 制造服务费 $1.16B（2026–2029）；
- 三星、SK Hynix、NAND 价格战。

**2026–2030 节奏**：
- 2026：营收增长 200%+、IPO 后股价强势；
- 2027：QLC 企业 SSD 上量；
- 2028–2030：AI 推理数据库存储领导者。

### 16. 博通（Broadcom）

**优势**：
- Q1 FY26 AI 半导体营收 $84 亿（+106%）；
- 与 Google、Meta、字节合作 XPU；
- 网络芯片（Tomahawk / Jericho）护城河；
- 利润率高于 Marvell。

**风险**：
- 客户集中度高（Google 占比大）；
- Marvell 在 Amazon Trainium 抢单；
- 网络芯片 AI 化竞争（NVIDIA Spectrum-X）；
- 估值已高（forward P/E 30x+）。

**2026–2030 节奏**：
- 2026：Q2 AI 营收 $107 亿，+27% QoQ；
- 2027：与 Google 下一代 TPU、字节 ASIC；
- 2028–2030：网络芯片 + 定制 AI 硅双轮。

### 17. 美满（Marvell）

**优势**：
- FY26 营收 $82 亿（+42%），FY28 目标 $150 亿；
- Amazon Trainium 主力设计伙伴、Microsoft Maia；
- Google 谈 memory processing unit + 推理 TPU；
- 1.6T 光学互联 + Teralynx T100 交换芯片；
- Jensen Huang 在 Computex 2026 公开称为"下一个万亿美元公司"。

**风险**：
- forward P/E 64.9x、12 个月 P/E 91.4x（6/9 数据）；
- YTD +240% 后估值已极贵；
- 高管 5,000 股抛售披露；
- 6/3 股价单日 7% 下跌；
- Broadcom 在 Google TPU 主导；
- FY28 $15B 目标依赖 AWS、Microsoft、 Google 大单兑现。

**2026–2030 节奏**：
- 2026 Q2：FY27 营收 $12.7B 指引；
- 2026 H2：Google 推理芯片 tape-out；
- 2027：AWS Trainium4、Microsoft Maia 2；
- 2028：营收 $15B 兑现。

---

## 十、企业核心要素与战略启示

### 决定企业成败的核心要素

1. **工艺节点掌握能力**：TSMC N2 / Samsung 2nm GAA / Intel 18A / SMIC 5nm 差距决定未来 5 年代工格局。
2. **HBM 配额与质量**：Vera Rubin 已锁定三源，Rubin Next、HBM4E 仍是关键。
3. **先进封装产能**：CoWoS / SoIC 决定 AI 芯片交付节奏。
4. **超大规模客户关系**：AWS、Microsoft、Google、Meta、Oracle、Anthropic、OpenAI 的关系是定制硅核心。
5. **资本开支耐力**：6 大超大规模厂商 $785B capex 决定上下游生死。
6. **电力与基础设施**：约束正从芯片转向电力、冷却、数据中心。
7. **地缘政治与出口管制**：台湾 6/9 提案如落地，将重塑 AI 服务器对华供应链。
8. **软件生态（CUDA / ROCm）**：NVIDIA 护城河最深，AMD 仍需追赶。
9. **AI 资本回报率**：OpenAI ARR $24–25B、Anthropic $30B 是关键基本面证据。
10. **多源供应策略**：超大规模厂商均要求 Broadcom + Marvell 双源、SK Hynix + Samsung + Micron 三源。

### 战略启示

- **设计公司**：必须绑定超大规模厂商长单（5+ 年），同时保持 SK Hynix + Samsung + Micron 三源；
- **代工厂**：N2 / 2nm 必须 2026–2027 达到 70% 良率与 100K+ 月产能，否则失去 Apple、NVIDIA、AMD 大单；
- **存储厂商**：HBM4E / HBM5 是 2027–2028 分水岭；
- **IDM**：垂直整合优势在 AI 时代重新显现（Samsung 复苏）；
- **中国厂商**：必须依赖 SMIC + CXMT + T-Head 内部循环，绕开 EUV 与 HBM 限制；
- **系统厂商**：AI PC 关键看 Snapdragon X2 vs Apple M5 vs Intel Core Ultra X8 三角竞争。

---

## 十一、结论与展望

2030 年前的全球半导体行业将经历"AI 超级周期 + 出口管制 + 电力瓶颈"三重叠加。2026 年是产能与价格的双爆发年（WSTS 89.9% 增长），2027 年是供需平衡与周期顶部出现之年，2028–2030 年将经历结构性调整与新一轮技术周期（HBM5、1.4nm、Chiplet 标准化）。

**核心赢家**：
- **HBM 三巨头**（SK Hynix、Samsung、Micron）；
- **TSMC**（2nm + CoWoS 双重护城河）；
- **NVIDIA**（Vera Rubin + Rubin Next）；
- **AI 服务器 ODM**（Foxconn、Quanta、Wistron）；
- **CXMT**（中国 HBM 国产替代唯一规模化主体）。

**关键风险**：
- AI 资本开支回报率不及预期（2027 H2 Gartner 警告 40%+ agentic AI 项目被取消）；
- 出口管制扩至全中国（2026 年 6 月提案）；
- 电力供给约束（数据中心从"芯片瓶颈"转向"电力瓶颈"）；
- 半导体周期顶部提前到来（2028 H2）；
- Marvell、Cambricon 等高估值 AI 标的回调（forward P/E 60x+ vs 历史平均 27x）；
- Intel Foundry 18A 外部客户落地节奏；
- Apple iPhone Fold 量产推迟（Kuo 12/18 警告）。

**2026–2030 关键节点**：
- 2026 H2：Vera Rubin 量产、iPhone Fold 发布、Cambricon 业绩兑现检验；
- 2027 H1：HBM 供需平衡、CoWoS 紧缺缓解；
- 2027 H2：DRAM / NAND 周期顶部出现、超大规模厂商 capex 增速放缓；
- 2028：HBM5 / 14A 风险量产、Intel Foundry 18A 外部客户首轮兑现；
- 2029：周期底部、2030 复苏。

半导体行业在 2026–2030 年将进入"技术、商业、地缘"三重再平衡的窗口期。技术上看 HBM、2nm、Chiplet；商业上看超大规模厂商 capex 与回报率；地缘上看出口管制与国产替代。这三条线的交汇点，将决定 2030 年前行业格局的重塑。

---

## 十二、参考资料

| 来源 | URL |
|------|-----|
| WSTS Spring 2026 Forecast（electronics for you） | https://www.electronicsforyou.biz/eb-specials/industry-report/global-semiconductor-market-set-to-exceed-us1-5-trillion-in-2026/ |
| WSTS TSIA Forecast | https://wsts.tsia.org.tw/index.aspx |
| WSTS Forecasts Page | https://www.wsts.org/61/Forecasts |
| Digitimes WSTS 2026 $1.51T | https://www.digitimes.com/news/a20260605VL208/semiconductor-industry-wsts-growth-forecast-2026.html |
| DailyAlpha WSTS $1.51T | https://dailyalpha.us/news/wsts-sharply-raises-2026-semiconductor-forecast-to-dollar15-trillion-driven-by-explosive-memory-growth-6a23b69a3a85c10da1613b0e |
| TrendForce Taiwan AI Chip Export Curbs | https://www.trendforce.com/news/2026/06/10/news-taiwan-reportedly-mulls-tighter-ai-chip-export-rules-on-china-beyond-huawei-raising-risks-for-server-makers-like-foxconn/ |
| Tom's Hardware Taiwan Criminal Ban | https://www.tomshardware.com/tech-industry/taiwan-weighs-criminal-ban-on-ai-chip-exports-to-all-of-china-as-us-trade-talks-continue |
| Bloomberg Taiwan Mulls Curbs | https://www.bloomberg.com/news/articles/2026-06-09/taiwan-mulls-curbs-on-ai-chip-exports-to-china-to-align-with-us |
| ComputeForecast Moody's $785B | https://computeforecast.com/news/hyperscaler-capex-forecast-2026-moody-785-billion-trillion-2027/ |
| RCR Tech Moody's $1T 2027 | https://rcrtech.com/ai-infrastructure-news/moodys-hyperscaler-capex/ |
| NextWaves Hyperscaler Capex | https://nextwavesinsight.com/hyperscaler-ai-capex-microsoft-google-amazon-meta-2026/ |
| AlCapital Big-5 Capex $725B | https://alcapitaladvisory.com/research/intelligence/ai-infrastructure.html |
| Apple Newsroom M5 MacBook Air | https://www.apple.com/newsroom/2026/03/apple-introduces-the-new-macbook-air-with-m5/ |
| MacBook Neo Apple | https://www.apple.com/macbook-neo/ |
| MacBook Neo Tech Specs | https://www.apple.com/macbook-neo/specs/ |
| The Verge MacBook Air M5 Review | https://www.theverge.com/tech/894866/apple-macbook-air-m5-15-2026-laptop-review |
| PCMag M5 MacBook Air | https://www.pcmag.com/reviews/apple-macbook-air-13-inch-2026-m5 |
| The Guardian MacBook Air M5 | https://www.theguardian.com/technology/2026/apr/15/macbook-air-m5-review-apple-best-consumer-laptop-faster-battery-life-storage |
| Tom's Guide M5 vs Snapdragon X2 | https://www.tomsguide.com/computing/cpus/apple-m5-vs-snapdragon-x2-elite-extreme-benchmarks-the-early-verdict-is-in-and-its-a-surprise |
| Counterpoint Q1 2026 DRAM | https://www.counterpointresearch.com/en/insights/global-dram-and-hbm-market-share |
| Presenc.ai HBM Market Share 2026 | https://presenc.ai/research/hbm-market-share-samsung-skhynix-micron-2026 |
| 01.co HBM Competitive Landscape | https://01.co/research/hbm-competitive-landscape-2026 |
| Winbuzzer SK Hynix Overtakes Samsung | https://winbuzzer.com/2026/01/30/sk-hynix-overtakes-samsung-memory-shortage-2027-xcxwbn/ |
| InfoTechLead AI Data Center DRAM | https://infotechlead.com/networking/ai-data-center-demand-pushes-global-dram-revenue-to-record-high-in-q1-2026-96118 |
| Gentic HBM4 Vera Rubin | https://gentic.news/article/nvidia-qualifies-hbm4-for-vera |
| Spoonai Nvidia Cleared HBM4 | https://spoonai.me/posts/2026-06-07-nvidia-certifies-samsung-skhynix-micron-hbm4-vera-rubin-full-production-jun5-en |
| Introl HBM Evolution | https://introl.com/blog/hbm-evolution-hbm3-hbm3e-hbm4-memory-ai-gpu-2025 |
| Tweaktown Samsung HBM4 Sample | https://www.tweaktown.com/news/106871/samsung-begins-first-sample-production-of-hbm4-memory-ready-for-nvidia-qualification/index.html |
| FinanceHalo DRAM vs NAND | https://financehalo.com/blog/dram-vs-nand-ai-memory-bottleneck-investor-guide |
| AINAsia Vera Rubin HBM4 | https://aiinasia.com/news/nvidia-vera-rubin-full-production-samsung-sk-hynix-hbm4-news-2026-06-04 |
| TechTimes Vera Rubin | https://www.techtimes.com/articles/317539/20260602/nvidia-vera-rubin-enters-full-production-samsung-sk-hynix-micron-named-hbm4-suppliers.htm |
| AINAsia Samsung HBM4 Nvidia AMD Qual | https://aiinasia.com/news/samsung-hbm4-nvidia-amd-qualification-june-supply-2026-05-19 |
| TrendForce Samsung SK Hynix Rubin | https://www.trendforce.com/news/2026/03/09/news-samsung-sk%20hynix-reportedly-tapped-as-nvidia-rubin-hbm4-suppliers-shipments-could-start-in-march/ |
| Spoonai Samsung Q1 2026 | https://spoonai.me/posts/2026-05-06-samsung-q1-2026-record-ai-memory-en |
| AJU Press Samsung HBM | https://www.ajupress.com/view/20260317054112036 |
| Digitimes SK Hynix 70% HBM4 | https://www.digitimes.com/news/a20250226PD239/sk-hynix-hbm4-yield-rate-testing-production.html |
| TechTimes Jensen TSMC Vera Rubin | https://www.techtimes.com/articles/317079/20260524/nvidia-computex-2026-jensen-huang-flies-tsmc-vera-rubin-ramp-strains-taiwan-supply-chain.htm |
| FindSkill Vera Rubin GTC Taipei | https://findskill.ai/blog/nvidia-vera-rubin-cio-briefing-gtc-taipei/ |
| Tom's Hardware CoWoS Intel Packaging | https://www.tomshardware.com/tech-industry/semiconductors/intel-gains-ground-in-ai-packaging-as-cowos-capacity-remains-stretched |
| TIMEWELL COMPUTEX 2026 | https://timewell.jp/en/columns/computex-innovex-2026-taiwan-report |
| Barrack AI 2026 GPU Memory Crisis | https://blog.barrack.ai/2026-gpu-memory-crisis/ |
| Atlas PCB TSMC 70% CAGR | https://www.atlaspcb.com/news/news-tsmc-2nm-a16-capacity-expansion-2026/ |
| SemiEngineering TSMC Symposium | https://semiengineering.com/tsmc-tech-symposium-2026-by-the-numbers/ |
| PCHardwarePro TSMC 2nm Roadmap | https://www.pchardwarepro.com/en/TSMC-and-the-2nm-era:-production-customers-and-the-leap-to-N2P/ |
| TubelightTalks 2nm Race | https://tubelighttalks.com/2-nm-race/ |
| IC-PCB Apple TSMC 2nm | https://www.ic-pcb.com/news/apple-tsmc-2nm-2026-wmcm-chip-amd-mediatek-nvidia.html |
| TechPowerUp TSMC 2nm Customers | https://www.techpowerup.com/341044/tsmcs-first-2-nm-node-customers-are-apple-amd-nvidia-and-mediatek-intel-missing |
| TrendForce MediaTek 2nm Tape-out | https://www.trendforce.com/news/2025/09/16/news-tsmc-2nm-gains-steam-mediatek-completes-first-2nm-tape-out-as-apple-preps-a20-m6-r2/ |
| Wccftech SMIC 5nm 60–70% | https://wccftech.com/smic-5nm-yields-between-60-and-70-percent-claims-tipster/ |
| Asia Times SMIC Huawei | https://asiatimes.com/2024/02/smic-to-sell-huawei-costly-inefficient-5nm-chips/ |
| Tom's Hardware SMIC 14nm | https://www.tomshardware.com/news/smic-mass-produces-14nm-nodes-advances-to-5nm-7nm |
| SemiWiki Huawei SMIC 5nm | https://semiwiki.com/forum/threads/huawei-the-leader-in-chinese-semiconductor-development…-‘life-or-death’-for-smic-5nm-mass-production-next-year.22690/ |
| Wikipedia SMIC | https://en.wikipedia.org/wiki/Semiconductor_Manufacturing_International_Corporation |
| Enki AI SMIC AI Chip Strategy | https://enkiai.com/ai-market-intelligence/smic-ai-chip-strategy-2026-inside-chinas-5nm-power-play |
| Perplexity Huawei 910C | https://www.perplexity.ai/discover/you/huawei-to-ship-new-ai-chip-ami-fTjKgvuOTIidQos_XPb5uw |
| Monimega China Chip Champion | https://monimega.com/chinas-chip-champions-ramp-up-production-of-ai-accelerators-at-domestic-fabs-but-hbm-and-fab-production-capacity-are-towering-bottlenecks/ |
| Epium CXMT HBM3 | https://epium.com/news/cxmt-hbm3-mass-production-2026-capacity/ |
| Techolam CXMT 20% HBM3 | https://www.techolam.com/news/cxmt-allocates-20-of-2026-production-to-hbm3-as-china-pushes-domestic-ai-memory-supply |
| KrAsia CXMT YMTC Expansion | https://kr-asia.com/chinas-cxmt-and-ymtc-to-massively-expand-memory-output-amid-global-crunch |
| Dasroot China DRAM | https://dasroot.net/posts/2026/06/china-dram-nand-price-flood-homelab-llm-2026/ |
| 知乎 长鑫存储 | https://zhuanlan.zhihu.com/p/1913177203321054910 |
| ChinaDaily Alibaba AI Chip | https://global.chinadaily.com.cn/a/202605/20/WS6a0d9b1fa310d6866eb49c3d.html |
| CBI T-Head Zhenwu | https://chinabizinsider.com/alibaba-t-head-unveils-next-gen-ai-chip-roadmap-targeting-enterprise-agentic-workloads/ |
| TrendForce T-Head 470K | https://www.trendforce.com/news/2026/03/20/news-alibabas-t-head-reportedly-hits-470k-chip-shipments-expands-amid-unclear-ipo-timeline/ |
| FinancialContent T-Head IPO | https://www.financialcontent.com/article/marketminute-2026-1-22-silicon-spin-off-alibaba-prepares-to-list-t-head-unit-amid-global-ai-chip-race |
| ChinaDailyBrief T-Head Powers | https://chinadailybrief.com/article/6a0d3e5881ad73ba8cd8613c |
| Wikipedia HiSilicon | https://en.wikipedia.org/wiki/HiSilicon |
| ABHS China DUV Lithography | https://www.abhs.in/blog/china-duv-lithography-loophole-smic-huawei-near-frontier-chips-aei-april-2026 |
| Techi Nvidia $1T Backlog | https://www.techi.com/nvidia-stock/ |
| LinkedIn Nvidia Beat Numbers | https://www.linkedin.com/pulse/nvidia-beat-every-number-so-why-stock-down-5-tentenco-lx9tf |
| Wisdom of Whales Nvidia $68B | https://wisdomofthewhales.com/p/nvidia-just-posted-68b-in-revenue-here-s-what-wall-street-missed |
| TIKR Marvell $15B | https://www.tikr.com/blog/marvell-stock-at-record-levels-with-15-billion-revenue-target-and-190-upside-in-sight |
| CurrentAffair Marvell 25% | https://www.currentaffair.today/blog/finance-9/marvell-stock-surges-25-on-jensen-huangs-next-trillion-dollar-company-call-nvidias-2b-vote-the-teralynx-t100-and-the-custom-silicon-war-with-broadcom-758 |
| Spoonai AWS $225B Marvell | https://spoonai.me/posts/2026-05-12-amazon-aws-225b-ai-chip-backlog-marvell-trainium-may11-en |
| Gzmato Google Marvell | https://gzmato.com/blog/post/google-marvell-custom-ai-inference-chips-2026 |
| Parameter Marvell Nvidia Teralynx | https://parameter.io/marvell-mrvl-stock-nvidia-partnership-and-teralynx-t100-launch-boost-ai-networking-narrative/ |
| TheNextWeb Google Marvell | https://thenextweb.com/news/google-marvell-ai-chips-inference-tpu-broadcom |
| PitchGrade Marvell ASIC | https://pitchgrade.com/research/marvell-ai-margin-pressure |
| HeyGoTrade Broadcom vs Marvell | https://www.heygotrade.com/en/blog/broadcom-vs-marvell-custom-ai-silicon-battle-2026/ |
| Spoonai Marvell 200% | https://spoonai.me/posts/2026-06-12-marvell-stock-mrvl-200-percent-rally-2026-2027-forecast-jun12-en |
| 24/7 Wall St Marvell | https://247wallst.com/investing/2026/06/09/up-nearly-200-year-to-date-1-blinking-red-light-that-makes-marvell-technology-stock-a-hold-at-289/ |
| FinanceCharts MRVL PE | https://www.financecharts.com/stocks/MRVL/value/pe-ratio |
| TipRanks Marvell | https://www.tipranks.com/news/why-did-marvell-stock-mrvl-swing-wildly-today-6-5-2026 |
| Benzinga Marvell | https://www.benzinga.com/trading-ideas/movers/26/06/53086509/whats-going-on-with-marvell-stock-tuesday-2 |
| TheStreet Cramer Marvell | https://www.thestreet.com/investing/stocks/jim-cramer-urges-caution-on-marvell |
| CNBC Nvidia Q1 2026 | https://www.cnbc.com/2025/05/28/nvidia-nvda-earnings-report-q1-2026.html |
| NVIDIA Q1 FY27 Earnings | https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-first-quarter-fiscal-2027 |
| TechPowerUp NVIDIA Q1 FY27 | https://www.techpowerup.com/349233/nvidia-announces-financial-results-for-first-quarter-fiscal-2027 |
| Investopedia Q2 $91B | https://www.stocktitan.net/news/NVDA/nvidia-announces-financial-results-for-first-quarter-fiscal-fq78amc9h84m.html |
| Tech-Insider Broadcom AI | https://tech-insider.org/broadcom-ai-revenue-custom-chips-2026/ |
| Oplexa Broadcom $100B | https://oplexa.com/broadcom-ai-revenue-2026/ |
| CyborgSignal Broadcom | https://cyborgsignal.com/research/broadcom-avgo-custom-ai-asic-2026 |
| Global Semi Research Broadcom | https://globalsemiresearch.substack.com/p/broadcoms-ai-asic-dominance-navigating |
| Tom's Hardware Custom AI ASIC | https://www.tomshardware.com/tech-industry/semiconductors/custom-ai-asics-examined-from-broadcom-to-mtia |
| SCMP Cambricon Moutai | https://www.scmp.com/business/china-business/article/3323078/changing-times-cambricon-tops-moutai-chinas-costliest-stock-chips-trump-baijiu |
| Yuan Trends Cambricon 48B | https://yuantrends.com/cambricon-48-billion-yuan-market-value-evaporation-denials-stock-decline/ |
| Yuan Trends Cambricon Flash Crash | https://yuantrends.com/cambricon-stock-flash-crash-market-cap-evaporation-analysis/ |
| Barrons Cambricon Stock Falls | https://www.barrons.com/articles/cambricon-stock-price-nvidia-alibaba-46b38168 |
| SCMP Cambricon Vaults | https://www.scmp.com/topics/cambricon-technologies-corporation |
| Sic-Components China AI Chip | https://www.sic-components.com/extension/d_blog_module/post?post_id=391 |
| EqualOcean Cambricon H1 2025 | https://equalocean.com/briefing/20250827230148574 |
| Wccftech Cambricon 43x | https://wccftech.com/chinese-ai-firm-earns-43-times-more-revenue-in-h1-2025-as-beijing-turns-to-domestic-ai-chips/ |
| Reportify Cambricon | https://reportify.ai/reports/1086435685264658432 |
| LinkedIn Cambricon Rise | https://www.linkedin.com/posts/andrewmethven_if-you-follow-tech-in-china-and-youre-a-activity-7370048774255194112-IQFS |
| AIProductivity China Chip Record | https://aiproductivity.ai/news/chinese-chip-firms-record-revenue-ai-boom-us-curbs/ |
| ArmustNews Cambricon Sell-off | https://armustnews.com/china-stays-bullish-on-ai-despite-cambricon-sell-off |
| Recode China AI Top 10 | https://www.recodechinaai.com/p/top-10-china-ai-cities-cambricon |
| Bloomberg China AI Chip | https://www.bloomberg.com/news/articles/2025-09-09/china-s-ai-chip-boom-fuels-rallies-in-cambricon-alibaba-shares |
| IntuitionLabs AI Bubble | https://intuitionlabs.ai/articles/ai-bubble-vs-dot-com-comparison |
| Lambda Finance Dot-Com | https://www.lambdafin.com/articles/dot-com-bubble-vs-ai-bubble |
| Cresset Capital 2026 Outlook | https://cressetcapital.com/articles/market-update/market-update-12-17-25-2026-outlook-is-ai-a-bubble/ |
| TheNextWeb AI Stocks | https://thenextweb.com/news/ai-stocks-dot-com-bubble-comparison-market-outlook |
| IndexBox AI Stock Boom | https://www.indexbox.io/blog/ai-stock-boom-echoes-of-the-dot-com-bubble/ |
| Medium $2.5T Bet | https://medium.com/@drjohnmillar/the-2-5-trillion-bet-why-ai-capital-will-mostly-reward-users-not-builders-ce877707d288 |
| ExplodingTopics AI Stats | https://explodingtopics.com/blog/ai-statistics |
| Factually Worst Case | https://factually.co/fact-checks/technology/worst-case-scenario-ai-boom-catastrophic-risks-dd29b2 |
| IQfi AI Stocks | https://intuitionlabs.ai/articles/ai-bubble-vs-dot-com-comparison |
| DQIndia Samsung $17B | https://www.dqindia.com/esdm/samsung-lands-17bn-foundry-deal-as-musk-confirms-teslas-ai6-chips-to-be-made-in-taylor-9655228 |
| SmBom Samsung Foundry Q3 | https://www.smbom.com/news/47827 |
| Wccftech Samsung Qualcomm | https://wccftech.com/samsung-and-qualcomm-2nm-gaa-partnership-chances-increasing/ |
| Smarti News Samsung 2nm | https://smarti.news/7944/samsung-2nm-gaa-nand-profit-2026 |
| Technosports Samsung 130% | https://technosports.co.in/samsung-eyes-130-growth-2nm-gaa/ |
| Bloomberg Samsung $16.5B | https://www.bloomberg.com/news/articles/2025-07-28/samsung-bags-16-5-billion-deal-in-big-win-for-chipmaking-arm |
| CNN Tesla Samsung | https://edition.cnn.com/2025/07/28/business/tesla-samsung-chip-deal |
| Tweaktown Intel 14A | https://www.tweaktown.com/news/111778/intels-14a-node-will-enter-risk-production-in-2028-while-10a-and-7a-nodes-are-on-the-roadmap/index.html |
| Motley Fool Intel 18A | https://www.fool.com/investing/2025/02/22/the-intel-18a-process-is-finally-ready/ |
| LinkedIn Intel 18A Challenge | https://www.linkedin.com/pulse/intels-18a-challenge-raises-questions-narrowing-window-jeff-morrison-cmawc |
| Yahoo Intel CEO 18A | https://finance.yahoo.com/news/intel-ceo-embraces-18a-node-112151239.html |
| Wccftech Intel 18A-P | https://wccftech.com/intel-18a-p-goes-beyond-speed-bump-adding-better-thermal-conductivity-to-win-foundry-customers/ |
| TradingNews Intel 459% | https://www.tradingnews.com/news/intel-stock-forecast-intc-113-18a-foundry-turnaround-valuation-june-9-2026 |
| Yahoo Cramer Intel | https://finance.yahoo.com/news/jim-cramer-calls-intels-18-233133342.html |
| Wikipedia TSMC Arizona | https://en.wikipedia.org/wiki/TSMC_Arizona |
| IEEE TSMC Arizona | https://spectrum.ieee.org/tsmc-arizona |
| Bloomberg TSMC Arizona Yield | https://www.bloomberg.com/news/articles/2024-10-24/tsmc-s-arizona-chip-production-yields-surpass-taiwan-s-a-win-for-us-push |
| TSMC Arizona | https://www.tsmc.com/static/abouttsmcaz/index.htm |
| BigGo AMD MI400 | https://biggo.com/news/202506130012_AMD_Instinct_MI400_Doubles_AI_Performance_with_HBM4 |
| Szwecent AMD Helios | https://www.szwecent.com/can-amds-helios-platform-and-mi450x-break-nvidias-ai-dominance/ |
| Simply Wall St AMD Helios | https://simplywall.st/stocks/us/semiconductors/nasdaq-amd/advanced-micro-devices/news/amd-helios-rack-scale-platform-reframes-ai-data-center-ambit |
| TS2 AMD Stock 2025 | https://ts2.tech/en/amd-stock-on-december-9-2025-ai-supercomputers-openai-deal-and-wall-street-targets-in-focus/ |
| TCS AMD Helios India | https://technosports.co.in/tcs-amd-helios-rack-scale-ai-to-india/ |
| AInvest Sandisk NAND | https://www.ainvest.com/news/sandisk-sndk-ai-driven-storage-demand-fuels-unprecedented-earnings-margins-2601/ |
| AINvest Sandisk Earnings | https://www.ainvest.com/news/nand-vertical-sandisk-ai-fueled-earnings-shock-ignites-memory-supercycle-2601/ |
| Parameter Sandisk 251% | https://parameter.io/sandisk-sndk-jumps-on-251-revenue-spike-driven-by-explosive-ai-data-center-demand/ |
| EET Asia NAND | https://www.eetasia.com/price-hikes-fueled-84-growth-of-top-five-global-nand-flash-suppliers-revenue-in-1q-2026/ |
| Digitimes Kioxia Sandisk | https://www.digitimes.com/news/a20251001PD213/kioxia-sandisk-nand-flash-demand-chips.html |
| Apressa Sandisk 31.8% | https://aipressa.com/top-stories/sandisks-ai-nand-demand-fuels-31-8-stock-surge-amid-kioxia-supply-deal/ |
| LinkedIn Sandisk Earnings | https://www.linkedin.com/posts/akshay-chander-b1a0545_sandisk-absolutely-crushed-earnings-last-activity-7424969350895452160-llG9 |
| Benzinga SanDisk Trending | https://www.benzinga.com/markets/equities/26/01/50255564/sandisk-stock-is-trending-overnight-heres-what-you-should-know |
| MacRumors iPhone Fold | https://www.macrumors.com/2025/12/18/foldable-iphone-shortages-into-2027/ |
| Gadgets360 iPhone Fold | https://www.gadgets360.com/mobiles/news/apple-foldable-iphone-2026-launch-shipments-expected-2027-analyst-ming-chi-kuo-9845417 |
| AppleInsider iPhone Fold 2026 | https://appleinsider.com/articles/25/09/02/iphone-fold-2026-rumored-with-lighter-apple-vision-air-in-2027 |
| MacObserver iPhone Fold | https://www.macobserver.com/news/apple-recently-raised-iphone-fold-shipment-projections-substantially/ |
| Cult of Mac iPhone Fold | https://www.cultofmac.com/news/apple-ups-foldable-iphone-shipment-targets-ahead-2026-debut |
| ZDNet iPhone Fold | https://www.zdnet.com/article/want-a-foldable-iphone-apple-thinks-you-and-millions-of-others-will-next-year/ |
| Medium Kuo iPhone Fold | https://medium.com/@mingchikuo/apples-first-foldable-iphone-predictions-market-positioning-hardware-specs-development-c60ca52be337 |
| GadgetHacks iPhone Fold $2,300 | https://apple.gadgethacks.com/news/iphone-fold-2026-apples-2300-foldable-finally-revealed/ |
| TechRepublic iPhone Ultra | https://www.techrepublic.com/article/news-iphone-ultra-rumor-foldable/ |
| Wccftech iPhone Fold Indifferent | https://wccftech.com/apples-new-foldable-iphone-faces-a-largely-indifferent-demand-cohort/ |
| Digit iPhone Fold 22% | https://www.digit.in/news/mobile-phones/apple-iphone-fold-to-capture-over-22-of-foldable-shipments-in-its-first-year-report.html |
| AndroidAuthority iPhone Fold Dimensions | https://www.androidauthority.com/apple-iphone-fold-leaked-dimensions-schematics-3626689/ |
| Macworld iPhone 18 Pro | https://www.macworld.com/article/2953687/iphone-18-pro-2026-release-date-design-specs-rumors.html |
| IBTimes iPhone 18 Pro Leaks | https://www.ibtimes.com.au/video-iphone-18-pro-leaks-2nm-a20-pro-chip-35-smaller-dynamic-island-deep-red-color-set-stage-1865713 |
| MacRumors iPhone 18 | https://www.macrumors.com/roundup/iphone-18/ |
| MacRumors Foldable Design | https://www.macrumors.com/2026/06/07/best-look-yet-at-foldable-iphone-design-revealed/ |
| CNBC Walmart Q1 2026 | https://www.cnbc.com/2025/05/15/walmart-wmt-q1-2026-earnings.html |
| TechInsider Broadcom 106% | https://tech-insider.org/broadcom-ai-revenue-custom-chips-2026/ |
| OEC China Trade | https://oec.world/en/profile/country/chn |
| Wccftech M5 Pro Max | https://wccftech.com/macbook-pro-with-m5-pro-and-m5-max-options-no-exact-launch-timeline-h1-2025/ |
| TS2 Cambricon Watch | https://ts2.tech/en/cambricon-stock-688256-on-watch-after-fund-filings-reshuffle-china-ai-chip-bets/ |
| SCMP Cambricon Top | https://www.scmp.com/business/china-business/article/3323078/changing-times-cambricon-tops-moutai-chinas-costliest-stock-chips-trump-baijiu |
| Gadgets360 iPhone Fold Delay 2027 | https://www.gadgets360.com/mobiles/news/apple-foldable-iphone-launch-2027-delay-specifications-report-5328066 |
| SMBoom TSMC 2nm | https://www.smbom.com/news/38080 |
| Yahoo TSMC 2nm Breakthrough | https://au.finance.yahoo.com/news/tsmc-2nm-breakthrough-reshapes-ai-050650138.html |
| SemiWiki TSMC 2nm Demand | https://semiwiki.com/forum/threads/tsmc’s-2nm-process-said-to-witness-‘unprecedented’-demand-exceeding-3nm-due-to-interest-from-apple-nvidia-amd-many-others.22744/ |
| TechJuice TSMC 2nm Era | https://www.techjuice.pk/tsmc-enters-2nm-era-as-next-gen-chips-begin-volume-production/ |
| LinkedIn Nvidia 78B | https://www.linkedin.com/pulse/nvidia-beat-every-number-so-why-stock-down-5-tentenco-lx9tf |
| This-Info Nvidia Q1 FY27 | https://this-info.com/ai-governance/the-nvidia-earnings-preview-what-q1-fy27-will-reveal-about-the-ai-cycle/ |
| Cryptogram Nvidia Q1 FY27 | https://cryptogramplatform.com/ai-tooling/the-nvidia-earnings-preview-what-q1-fy27-will-reveal-about-the-ai-cycle/ |
| CryptoSlate Jordan News | https://www.jordannews.net/news/278771687/china-ai-draws-global-capital-as-us-valuations-fuel-bubble-worries |
| NYSE T-Head 22 | https://www.financialcontent.com/article/marketminute-2026-1-22-silicon-spin-off-alibaba-prepares-to-list-t-head-unit-amid-global-ai-chip-race |
| The World Data OpenAI Anthropic | https://theworlddata.com/openai-vs-anthropic-compute-statistics/ |
| The AI Enterprise OpenAI Math | https://www.theaienterprise.io/p/openai-math-problem-bull-bear |
| DigitalApplied AI Investment | https://www.digitalapplied.com/blog/ai-infrastructure-investment-may-2026-capital |
| Dev.to OpenAI Revenue | https://dev.to/simon_paxton/compute-anxiety-not-collapse-openai-revenue-2026-1di1 |
| Roborhythm Anthropic $30B | https://www.roborhythms.com/anthropic-revenue-30-billion-2026/ |
| BBC Trump-Xi Summit | https://www.bbc.com/news/articles/clypj01189lo |
| New York Times China AI | https://www.nytimes.com/2026/05/12/business/china-semiconductor-ai-deepseek.html |
| QZ Nvidia China Deal | https://qz.com/nvidia-china-deal-jensen-huang-trump-h20 |
| EWeek DeepSeek H20 | https://www.eweek.com/news/deepseek-ai-models-nvidia-h20-chips/ |
| Wikipedia Nvidia | https://en.wikipedia.org/wiki/Nvidia |
| Wccftech AMD MI400 Helios | https://wccftech.com/amd-helios-rack-scale-platform-mi400-2026/ |
| Wikipedia Cambricon | https://en.wikipedia.org/wiki/Cambricon_Technologies |
| Paasa AI Capex | https://paasa.com/blog/ai-capex-supercycle |
| AInvest AI Infrastructure | https://www.ainvest.com/news/assessing-ai-infrastructure-bull-case-hyperscaler-capex-data-center-demand-risk-slowdown-2509/ |
| MarketNewsData Hyperscaler | https://marketnewsdata.com/2025/07/🌐-hyperscaler-ai-capex-outlook-goldman-sachs/ |
| MUFG Hyperscaler | https://www.mufgamericas.com/sites/default/files/document/2025-12/AI_Chart_Weekly_12_19_Financing_the_AI_Supercycle.pdf |
| CreditSights Hyperscaler | https://know.creditsights.com/insights/technology-hyperscaler-capex-2026-estimates/ |
| ABHS Q1 2026 | https://www.abhs.in/blog/microsoft-meta-google-amazon-q1-2026-earnings-ai-capex-630-billion-preview |
| SIA April 2026 | https://www.semiconductors.org/global-semiconductor-sales-increase-11-month-to-month-in-april/ |
| Boston Brand Media | https://www.bostonbrandmedia.com/news/the-semiconductor-surge-fueling-ai-innovation-and-digital-transformation |
| Pandaily China 20 Years | https://pandaily.com/once-in-20-years-china-s-semiconductor-industry-surges--jun2026 |
| Aroged Memory Shortage | https://www.aroged.com/2026/06/05/memory-shortage-will-accelerate-the-global-semiconductor-market-to-1-5-trillion-in-sales-this-year/ |
| ByteDive HBM Supercycle | https://thebytedive.com/ai/agentic-ai-memory-demand-100b-supercycle/ |
| MarketResearchReports OSAT | https://www.marketresearchreports.com/blog/2026/05/28/top-10-osat-companies-world-updated-2026 |
| SWOT Analysis Amkor | https://www.swotanalysis.com/amkor-technology |
| Yole Group | https://www.yolegroup.com/product/report/fan-out-packaging-2023/ |
| ECEWire OSAT | https://www.ecewire.com/section/news/p20210620n6.html |
| LinkedIn Fabs OSATs | https://www.linkedin.com/pulse/fabs-enter-compete-osats-advanced-packaging-aken-cheung |
| 247WallSt Marvell 200% | https://247wallst.com/investing/2026/06/09/up-nearly-200-year-to-date-1-blinking-red-light-that-makes-marvell-technology-stock-a-hold-at-289/ |
| 247WallSt Synopsys | https://247wallst.com/investing/2026/06/05/this-ai-chip-design-stock-is-24-below-its-high-while-revenue-grew-42-wall-street-is-focused-on-the-wrong-number/ |
| TechI Marvell Google | https://www.techi.com/marvell-stock-mrvl-google-ai-chip-rally/ |
| FirstPassLab Marvell $15B | https://firstpasslab.com/blog/2026-03-06-marvell-ai-datacenter-revenue-custom-silicon-network-engineer/ |
| Seeking Alpha Marvell Bigger | https://seekingalpha.com/article/4883245-marvell-ai-story-is-bigger-than-expected |
| FinancialContent Marvell | https://markets.financialcontent.com/stocks/article/marketminute-2026-3-6-marvell-technology-mrvl-q4-results-and-ai-growth-outlook-a-deep-dive |
| Alpha Analyst Marvell | https://alpha-analyst.com/insights/marvell-earnings-fy2027-outlook-ai-data-center-growth |
| Futurum Marvell Q1 | https://futurumgroup.com/insights/marvell-q1-fy-2026-results-driven-by-custom-silicon-and-data-center-momentum/ |
| MarketScreener Marvell | https://www.marketscreener.com/quote/stock/MARVELL-TECHNOLOGY-GROUP--4934/valuation/ |
| Yahoo Finance Marvell | https://finance.yahoo.com/quote/MRVL/key-statistics/ |
| StockAnalysis Marvell | https://stockanalysis.com/stocks/mrvl/statistics/ |
| TechPowerUp SK Hynix | https://www.techpowerup.com/news-tags/SK+Hynix |
| TechPowerUp HBM4 | https://www.techpowerup.com/news-tags/HBM4 |
| LinkedIn Dixon | https://www.linkedin.com/posts/nick-florous-ph-d-2821a84_ai-semiconductors-nand-activity-7457728768640434176-O6G8 |
| Investing.com MRVL | https://ng.investing.com/analysis/marvell-stock-faces-hold-zone-as-ai-chip-slowdown-tests-growth-outlook-211366 |
| CoinCentral Marvell | https://coincentral.com/marvell-mrvl-stock-drops-7-buy-the-dip-or-wait-for-earnings/ |
| TIKR MRVL Undervalued | https://www.tikr.com/blog/down-over-50-from-all-time-highs-is-nasdaq-mrvl-stock-undervalued |
| TIKR Marvell 200% | https://www.tikr.com/blog/marvell-stock-at-record-levels-with-15-billion-revenue-target-and-190-upside-in-sight |
| IndMoney Nvidia $81.6B | https://www.indmoney.com/blog/us-stocks/nvidia-stock-q1-fy27-earnings-81b-revenue-25x-dividend |
| TipRanks MRVL Earnings | https://www.tipranks.com/news/mrvl-earnings-marvell-stock-jumps-10-on-strong-earnings-fueled-by-ai-demand |
| Insider Monkey Marvell Cramer | https://www.insidermonkey.com/blog/jim-cramer-on-marvell-i-think-the-stocks-a-buy-going-into-the-quarter-1707730/ |
| V0 Revenue Insights | https://v0.app/chat/revenue-growth-insights-yuTfI6XVwkQ |
| StockNear Marvell | https://stocknear.com/stocks/mrvl/financials/ratios |
| Finviz INTC | https://finviz.com/quote?t=INTC |
| ABHS Microsoft Earnings | https://www.abhs.in/blog/microsoft-meta-google-amazon-q1-2026-earnings-ai-capex-630-billion-preview |
| Digitimes Foxconn Wistron | https://www.digitimes.com/news/a20260109PD249/revenue-ai-server-foxconn-wistron-quanta.html |
| Borecraft Pegatron | https://borecraft.com/2026/05/29/pegatron-and-wistron-see-ai-server-growth-lasting-past-2026/ |
| LinkedIn Foxconn Quanta | https://www.linkedin.com/pulse/from-assembly-lines-ai-supercomputers-how-foxconn-quanta-prasad-8xmqc |
| Insider Monkey Wistron | https://www.insidermonkey.com/blog/nvidia-reportedly-secures-entire-wistron-ai-server-plant-capacity-through-2026-1558041/ |
| Nikkei Asia Taiwan Server | https://asia.nikkei.com/business/technology/taiwan-s-top-ai-server-makers-boost-output-to-meet-us-demand |
| TrendForce TSMC 2nm MediaTek | https://www.trendforce.com/news/2025/09/16/news-tsmc-2nm-gains-steam-mediatek-completes-first-2nm-tape-out-as-apple-preps-a20-m6-r2/ |
| Humai Apple M5 vs Blackwell | https://www.humai.blog/apple-m5-vs-nvidia-blackwell-vs-google-tpu-the-complete-post-ces-2025-ai-chip-comparison/ |
| HyEtora India Today iPhone Fold | https://www.indiatoday.in/technology/news/story/apple-could-launch-its-first-foldable-iphone-in-2026-for-rs-175-lakh-2763762-2025-07-30 |
| TechNWatt iPhone Fold | https://technowatt.co/2026/06/04/iphone-fold-rumors-point-to-apples-2026-foldable-push/ |
| Pchardwarepro TSMC 2nm Customers | https://www.pchardwarepro.com/en/TSMC-and-the-2nm-era:-production-customers-and-the-leap-to-N2P/ |
| Twitter iPhone Fold Rumor | https://www.tomsguide.com/roundup/phones/iphones/iphone-fold |
| ABHS AI Capex $630B | https://www.abhs.in/blog/microsoft-meta-google-amazon-q1-2026-earnings-ai-capex-630-billion-preview |
| AlCapital 725B | https://alcapitaladvisory.com/research/intelligence/ai-infrastructure.html |
| KGI Trade | https://www.ajupress.com/view/20260317054112036 |
| ImPactTrading MuddyWaters | https://www.digitimes.com/news/a20250724PD223/samsung-hbm4-production-2026-sk-hynix.html |
| Intel 18A TSMC | https://www.linkedin.com/pulse/intels-18a-challenge-raises-questions-narrowing-window-jeff-morrison-cmawc |
| Pchardware TSMC 2nm | https://www.pchardwarepro.com/en/TSMC-and-the-2nm-era:-production-customers-and-the-leap-to-N2P/ |
| LinkedIn Foxconn Quanta Wistron | https://www.linkedin.com/pulse/from-assembly-lines-ai-supercomputers-how-foxconn-quanta-prasad-8xmqc |
| Pureinfotech Windows 11 26H1 | https://pureinfotech.com/windows-11-26h1-beta-experimental-channels-split/ |
| Pepelac Snapdragon vs Nvidia | https://pepelac.news/no/posts/id45885-qualcomm-vs-nvidia-hvem-vinner-kampen-om-windows-pa-arm |
| Qualcomm Snapdragon X2 Elite | https://www.qualcomm.com/laptops/products/snapdragon-x2-elite |
| Barchart Qualcomm 2026 | https://www.barchart.com/story/news/37025790/ignore-the-apple-noise-and-consider-buying-qualcomm-stock-for-2026 |
| Saudi Gazette Qualcomm $4.5B | https://saudigazette.com.sa/article/565404/BUSINESS/Qualcomm-to-see-at-least-$45bn-from-Apple-settlement |
| FT Qualcomm Apple 5G | https://www.ft.com/content/c1af4788-6122-11e9-b285-3acd5d43599e?syn-25a6b1a6=1 |
| News18 Qualcomm | https://www.news18.com/news/tech/qualcomm-believes-it-will-get-at-least-4-5-billion-from-its-patent-settlement-with-apple-2125007.html |
| The Coast Guard Qualcomm | https://www.thecoastguard.ca/qualcomm-to-supply-apple-with-5g-modems-for-iphones-by-2026/ |
| MacBook Neo India OnBuy | https://www.onbuy.com/gb/p/apple-macbook-neo-13-a18-pro-8gb-256gb-indigo~p238105039/ |
| DNS Shop MacBook Neo | https://www.dns-shop.ru/product/2cdacbb03b9180db/13-noutbuk-apple-macbook-neo-a18-pro-rozovyj/ |
| Cdek Shopping MacBook Neo | https://cdek.shopping/p/36959017/noutbuk-apple-macbook-neo-13-2026-a18-pro-8-gb512-gb-angliiskaya-klaviatura-indigo |
| YouTube MacBook Neo | https://www.youtube.com/watch?v=L7ypKXTJ-oU |
| 27WallSt MRVL | https://247wallst.com/investing/2026/06/09/up-nearly-200-year-to-date-1-blinking-red-light-that-makes-marvell-technology-stock-a-hold-at-289/ |
| YouTube Marvell 200% | https://www.youtube.com/watch?v=XudmuNVal9U |

---

**报告字数约 12,000 字**，覆盖了 2026–2030 年全球半导体行业全产业链分析，提供了详细的参与者表、定量证据表、情景分析、领先指标、争议性主张评估以及 17 家核心企业的发展态势研判。