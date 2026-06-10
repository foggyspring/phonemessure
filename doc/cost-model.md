# CNC 报价模型 · Cost & Quoting Model

本文档说明 CNC 报价引擎（`cnc/`）的成本、交期与可加工性（DFM）模型，以及全部
**可运行时维护**的参数。模型由多角色迭代评审逐步完善（工艺/机械设计/质量/采购/
财务/生产计划/刀具/表面/包装/装配/安全合规/UI/销售/客服/老板/客户），强调
**透明、可追溯、可维护**。

> 单一计算入口：`cnc/service.py::build_quote`。屏幕端与 PDF 共用同一份结果，
> 保证"所见即所得"。

报价结果面板（含价格构成、置信度区间、交付日期、去毛刺单列、工艺路线、
风险概览、报价假设；全程无 emoji）：

![报价结果](img/ui-quote-result.png)

---

## 一、单件成本构成 Unit cost

```
单件成本 = 材料净费 + 加工费 + 表面处理 + 去毛刺/增项 + 编程/装夹摊销
单价     = 单件成本 × (1 + 利润率[+紧公差风险溢价])
```

| 项 | 计算 | 来源 |
|----|------|------|
| 材料净费 | 毛坯重量×料价 − 废料抵扣（去除金属×回收率） | `costing._material_cost` |
| 加工费 | 单件机时/60×机床时租 **+ 刀具消耗** | `_machining_cost` + 迭代20 |
| 刀具消耗 | 切削机时×`tool_wear_cny_per_hour`×max(0, machinability−1) | 迭代20（铝=1.0 不收，钛最高）|
| 表面处理 | 面积(dm²)×每dm²单价（含起步/保底）| `costing.price` |
| 去毛刺/增项 | 金属件去毛刺(base+per_dm²) + 质检增项 | 迭代6 单列对账 |
| 编程/装夹摊销 | 一次性(编程+首件)/数量 | 迭代1 提高打样真实性 |

各成本行求和 == 单件成本，可逐行对账（迭代6）。`price_drivers` 给出排名前三的
成本项及占比与一句话解释（迭代10）。

### 工时（CAPP）

单件机时 = 粗铣 + 精铣 + 钻孔 + 攻丝 + 换刀 + 装夹 + 检验。其中：

- 检验工时 = 公差基线 + `inspection_min_per_feature`×孔数（仅紧公差，迭代11）。
- 工艺路线卡 `process_steps`：下料→编程→装夹→粗铣→精铣→钻孔→攻丝→检验，
  每步含工时与每件/每批范围（迭代3）。
- 刀路仿真后端（trimesh 分层 + shapely 偏置）/ 解析法，按几何规模自动切换。

---

## 二、交期模型 Lead time（全工序链）

```
交期 = max(档位交期, 加工天数) + 备料天数 + 外协后处理天数
```

| 环节 | 计算 | 参数 | 迭代 |
|------|------|------|------|
| 档位交期 | 经济/标准/加急/特急 | `lead_time_tiers[].days` | — |
| 加工天数 | ⌈单件机时×数量÷60÷日产能⌉ | `daily_capacity_hours` | 19 |
| 备料天数 | 非常备料采购等待 | 材料 `stock_lead_days` | 13 |
| 外协后处理 | 阳极/喷粉/钝化送外协 | finish `lead_days` | 21 |

报价输出日历**交付日期** `delivery_date`（及各交期档），客户无需自行换算（迭代8）。

---

## 三、订单层 Order totals

- **起订额兜底**（迭代14）：净额低于 `min_order_cny` 时硬性抬到起订额，单列
  `min_order_topup_cny`（起订补差），税按兜底后净额计。
- **税**：`tax_rate` / `tax_label`，含税总计 = 净额×(1+税率)。
- **物流**：运费 = 包装费 + 重量×每kg；订单超 `crate_threshold_kg` 加 `crate_cny`
  木箱费（迭代22）。
- **报价有效期** `quote_valid_days`；PDF 报价单号为稳定 sha1 摘要（迭代7，修复
  `hash()` 受 PYTHONHASHSEED 影响而重启变化的缺陷）。

---

## 四、可加工性 DFM findings

`analyze_dfm` 输出分级清单（high/medium/low/info），并汇总为 `dfm_summary`
（各级计数 + 一句话结论，无风险给正向信号，迭代17）。当前规则：

| code | 级别 | 触发 | 迭代 |
|------|------|------|------|
| `unit_suspect` | high | 最大尺寸<3mm，疑似单位错误 | — |
| `material_removal` | low | 包络填充率<18%，高去料比(buy-to-fly) | 12 |
| `slender` | high/med | 细长比 >12 / >8 | — |
| `thin_wall` | high/med | 最小壁厚 <0.5 / <1mm | — |
| `deep_hole` | high/med | 深径比 >10 / >4 | — |
| `small_hole` / `small_tap` | med | 微孔/细牙螺纹孔 | — |
| `tap_drill` | info | 螺纹底孔推荐(M3–M16，底孔=公称−螺距) | 9 |
| `thread_engage` | med | 螺纹啮合 <1×D，强度不足 | 2 |
| `undercut` / `multi_axis` | med/info | 倒扣/自由曲面 | — |
| `envelope` | med | 接近机床行程 | — |
| `tight_tol` | info | 精密公差 | — |
| `tol_feasibility` | med | 公差紧于 ±(0.01+0.00008·最长边)mm | 24 |
| `open_mesh` | med | 网格非水密 | — |
| `inner_radius` | info | 内角必带 R | — |

---

## 五、选材与置信度

- **等强度选材**（迭代4）：换料建议带 `tensile_mpa` 抗拉强度校核，强度等效优先、
  弱料标注"强度↓"，附校核提示。
- **置信度与区间**（迭代15）：`confidence` 给 0–100 分 + 等级 + 原因，并按等级映射
  参考价格区间 `price_range_cny`（高±8/中±15/低±25%），成交价不变。
- **报价假设** `assumptions`（迭代23）：公差/螺纹/表面/热处理/毛坯/轴数的确定性清单，
  减少 RFQ 来回。

---

## 五·五、切削参数依据 Machining-model sources

刀路仿真的切削/钻孔/攻丝模型按权威资料校准,关键口径与出处:

| 模型要素 | 口径 | 出处 |
|----------|------|------|
| 铣削进给 | feed = fz×齿数×RPM, RPM = Vc·1000/(πD);Vc/fz 按硬质合金手册区间(铝 320/380, 304 不锈钢 120/140, 钛 45/55 m/min) | 通用切削手册口径(Sandvik/Kennametal 级) |
| **攻丝(刚性)** | **进给被几何锁定 = 螺距×RPM,不是自由参数**;仅攻丝线速度 vc_tap 随材料(铝 20、304 5、316 4、钛 3 m/min,HSS-E 中值);螺距取自公制粗牙表(与 DFM 底孔表共用) | [Slugger 攻丝速度表](https://www.sluggertool.com/resources/tap-speed-chart/) · [CNClathing 攻丝公式](https://www.cnclathing.com/guide/cnc-tapping-speeds-and-feeds-chart-formula-calculator-metric-imperial) · [Haas 攻丝进给表](https://www.haascnc.com/content/dam/haascnc/ecommerce-assets/linedrawings/threading/taps/speed-n-feeds/(03-1562%20to%2003-1615)%20taps%20stainless%20steel,%20speeds%20and%20feeds,%20metric.pdf) |
| **啄钻 G83** | 深度 >3-4×D 启用啄钻;每啄 Q≈1×D;退/回为**快移**空程(Σ当前深度×2/rapid)+每啄 0.4s 停转/换向余量;>3D 段进给降额 25%(手册深度降额) | [Haas G83](https://www.haascnc.com/service/codes-settings.type=gcode.machine=mill.value=G83.html) · [CNCCookbook G81/G73/G83](https://www.cnccookbook.com/g81-g73-g83-drill-peck-canned-cycle/) · [MachinistGuides G83](https://www.machinistguides.com/g83-code/) |
| 钻尖行程 | 118° 钻尖需多走 ≈0.3×D 才到全径(纯几何: D/2·tan31°) | 几何推导 |
| 逐孔开销 | 每孔 4s 定位/趋近/点孔余量(hole_approach_s) | 车间惯例口径, 反标定可校 |
| **循环参数配置化** | 上述全部循环常数(啄钻触发/每啄深/降额/每啄余量/钻尖比/螺距近似比/快移/定位)入 `cutting.json` tools 节, 面板'切削→tools'运行时可改, 审计+可回退, 源码零魔数 | — |
| 快移 | 24 m/min(Haas VF 级 25.4 m/min) | Haas VF 规格 |
| 后端一致性 | 难加工材料(machinability≥2.5)螺纹在解析/刀路两后端均按螺纹铣计价(×thread_mill_factor) | 与 faq_thread_mill 同口径 |

> 一致性锁: `test_knowledge_consistency.py` 断言攻丝 feed=pitch×RPM 闭式吻合、
> vc_tap 排序(铝≫不锈钢≫钛)、两后端螺纹铣口径一致——任一侧漂移即红。

## 六、可维护参数总表

全部参数可在管理面板按标签页维护（见 [price-maintenance.md](price-maintenance.md)），
经白名单校验、审计记录 **before→after**（迭代16）。

| kind | 字段 |
|------|------|
| `material` | price_cny_per_kg, machinability, density_g_cm3, form_factor, scrap_credit_frac, **tensile_mpa**, **stock_lead_days** |
| `machine` | rate_cny_per_hour, base_mrr_cm3_min, max_axes |
| `finish` | setup_cny, per_dm2_cny, min_cny, **lead_days** |
| `business` | margin, tax_rate, tight_tolerance_margin_bonus, rush_factor, deburr_base_cny, deburr_per_dm2_cny, packaging_cny, shipping_cny_per_kg, min_order_cny, quote_valid_days, **daily_capacity_hours**, **tool_wear_cny_per_hour**, **crate_threshold_kg**, **crate_cny** |
| `business`（嵌套） | lead_time_tiers.*.{factor,days}, tolerance_classes.*.{margin_bonus,machining_factor,inspection_min}, surface_classes.*.finish_factor, addons.*.{batch_cny,per_part_cny} |
| `capp` | fixture_min_per_setup, toolchange_min_per_tool, programming_min_base, programming_min_per_complexity, first_article_min, tight_tolerance_machining_factor, tight_tolerance_inspection_min_per_part, **inspection_min_per_feature**, min_machine_min_per_part, stock_margin_mm |
| `cutting` | 按材料 vc/fz/切深/钻孔 + **vc_tap**(攻丝速度;进给=螺距×RPM 几何锁定) |

（**加粗**为多角色迭代新增字段。）

---

## 七、多角色迭代台账（24 轮）

| 轮次 | 角色 | 改动 |
|------|------|------|
| 1 | 工艺/老板/客户 | 提高打样定价真实性 |
| 2 | 机械设计 | 螺纹啮合深度 DFM |
| 3 | 客户/工艺 | 工艺路线卡透明化 |
| 4 | 机械设计 | 等强度选材校核 |
| 5 | UI | 价格维护面板标签页 |
| 6 | 客户/老板 | 成本对账，去毛刺单列，PDF 工艺路线 |
| 7 | 老板 | 稳定报价单号 |
| 8 | 客户 | 日历交付日期 |
| 9 | 机械设计 | 螺纹底孔推荐 |
| 10 | 客户/老板 | 价格构成"为什么这个价" |
| 11 | 质量工程师 | 检测工时随特征数叠加 |
| 12 | 机加工师傅/采购 | 高去料比提示 |
| 13 | 采购/供应链 | 材料备料周期入交期 |
| 14 | 财务/销售 | 起订额硬性兜底 |
| 15 | 算法工程师 | 置信度价格区间 |
| 16 | 安全/合规 | 改价审计 before→after |
| 17 | 客户/销售/UI | 制造风险概览徽标 |
| 18 | 销售/老板 | PDF 阶梯报价"节省"列 |
| 19 | 生产计划/ERP | 大批量按产能兜底交期 |
| 20 | 刀具管理 | 难加工材料计刀具消耗 |
| 21 | 表面/热处理 | 外协表面处理入交期 |
| 22 | 包装/物流 | 重件木箱包装费 |
| 23 | 客服 | 报价假设清单 |
| 24 | 装配/质量 | 公差-尺寸可行性 |

测试：`pytest` 全量 269 通过(含一致性锁/契约/全链路集成)。
