# CNC 自动加工报价系统 · Automatic CNC Machining Quote Engine

把一张 3D 图纸变成一份可下载的报价单。完整漏斗：

```
[前端 3D 看板] → [几何解析引擎] → [工艺/工时规划 CAPP] → [成本/利润核算] → [PDF 报价单]
   Three.js          STL / STEP         开粗→精→钻→攻        材料+机时+表处+利润     reportlab
```

这是 MVP 阶段（第一/二阶段）的可运行实现：STL 全自动解析，STEP/IGES 在装有
OpenCASCADE 时自动解析、否则降级为手动输入尺寸；几何特征（孔/螺纹/公差/薄壁/
五轴）由前端声明 + 几何信号共同驱动，符合“STEP 通常不含 PMI 公差信息”的现实。

### 准确度模型 Accuracy model（逐层逼近真实成本）

| 成本项 | 模型 |
|---|---|
| 材料费 | 实时 SHFE 料价 ×form_factor（或手动改价/静态）→ 标准板厚毛坯 → 减废料抵扣 → 净料费 |
| 加工费 | 物理 feeds/speeds(Vc·fz，随刀具/孔径变化) → 刀路仿真(trimesh+shapely+opencamlib) → 历史实测反标定 |
| 商务 | 阶梯量价 · 精密公差/加急溢价 · 增值税 · 报价有效期 |

每项都在报价/PDF 里**可追溯**（料价来源、标准板厚、废料抵扣、校准因子 `×1.4 n=5`）。
绝对精度的最后一公里靠**反标定**（实测工时）和按你分销商**标定 form_factor / Vc·fz**。

---

## 快速开始 Quick start

```bash
pip install -r cnc/requirements.txt
python -m cnc.server                 # → http://127.0.0.1:8500/
python -m cnc.server --port 9000 --host 0.0.0.0 --reload
```

打开浏览器，拖入一个 `.stl`（或 `.step` 如装了内核），选材料/数量/表面处理/公差，
点 **生成报价**，再点 **下载 PDF 报价单**。

跑测试：

```bash
python -m pytest cnc/tests -q
```

---

## 架构 Architecture

| 模块 | 文件 | 职责 |
|------|------|------|
| 几何解析 | `cnc/geometry/mesh.py` | 纯 Python 解析 STL（二进制/ASCII）：包围盒、体积（带符号四面体求和）、表面积、复杂度代理 |
| 格式分发 | `cnc/geometry/parser.py` | 按扩展名分发；STEP/IGES 懒加载 OpenCASCADE，缺失时抛 `KernelUnavailable`；含手动尺寸兜底 |
| 特征/DFM | `cnc/geometry/features.py` | 合并几何信号 + 用户声明（孔/螺纹/公差/薄壁/五轴），产出可加工性告警 |
| 工艺工时（解析） | `cnc/engine/capp.py` | 毛坯=包围盒+余量；去除体积/MRR；开粗→精加工→钻孔→攻丝；装夹/换刀/编程 |
| 工时估算后端 | `cnc/estimators/` | 可插拔精度分级：`analytic`（V/MRR）/ `toolpath`（刀路仿真）/ `freecad`（真实CAM） |
| 刀路仿真 | `cnc/estimators/toolpath.py` | trimesh 分层 + shapely 偏置：Z 分层开粗 + 等高精加工，积分真实刀路长度÷进给 |
| G代码工时 | `cnc/estimators/gcode_time.py` | 解析任意 CAM 的 G 代码，按进给+梯形加速积分真实节拍 |
| FreeCAD CAM | `cnc/estimators/freecad_cam.py` | 调用 freecadcmd 自动生成刀路并导出 G 代码（可选，最高精度） |
| 成本利润 | `cnc/engine/costing.py` | 材料费+加工费+表处费+一次性编程，×(1+利润率)；一次性成本按数量摊销→阶梯报价 |
| 参考数据 | `cnc/engine/shopdata.py` + `cnc/data/*.json` | 材料密度/单价、机床时租、CAPP 常数、利润率（生产环境可换 PostgreSQL）；支持运行时价格覆盖 |
| 持久化 | `cnc/store.py` | SQLite：报价历史 + 价格覆盖（当日单价/时租维护），仅用标准库 |
| 编排 | `cnc/service.py` | 把上面串成一份 payload，供 API 与 PDF 共用（保证“屏幕=PDF”一致） |
| 报价单 | `cnc/quote/pdf.py` | reportlab 生成中英双语 PDF，自动嵌入 CJK 字体 |
| 接口/前端 | `cnc/api.py` + `cnc/static/*` | FastAPI + Three.js（上传、3D 预览/旋转/缩放、参数、阶梯报价、PDF 下载） |

### 计算口径（可追溯，非黑箱）

- **毛坯** = 包围盒每边 + `stock_margin_mm`（默认 3mm）。
- **去除体积** = 毛坯体积 − 零件体积。
- **开粗工时** = 去除体积 / 有效MRR；有效MRR = 机床基础MRR / 材料可加工系数。
- **精加工工时** ∝ 表面积 × 复杂度系数（曲面多→系数高，可触发五轴 ×1.5）。
- **钻孔/攻丝** 按孔数、深径比（>4 啄钻）、是否攻丝逐项累加。
- **一次性成本**（编程+首件+表处线 setup）按数量摊销 → 数量越大单价越低（量大从优）。
- **总报价** = (材料 + 加工 + 表处 + 编程准备) × (1 + 利润率)，精密公差另加风险溢价与检验工时，加急乘加急系数。

所有系数都在 `cnc/data/machines.json` / `materials.json` 里，估价员可直接改。

---

## API

| 方法 & 路径 | 说明 |
|---|---|
| `GET /` | Three.js 前端单页 |
| `GET /api/health` | 存活 + 是否装有 B-rep 内核 |
| `GET /api/materials` | 材料/表面处理/机床/商务参数（供前端下拉） |
| `GET /api/backends` | 各工时估算后端的可用性（analytic/toolpath/freecad） |
| `POST /api/parse` | 上传 CAD 文件 → 几何指标（+可能的预览网格 base64） |
| `POST /api/quote` | `multipart`：`file`（选填）+ `params`(JSON) → 完整报价 payload（自动入库，返回 `id`） |
| `POST /api/quote/pdf` | body 传 `/api/quote` 的 payload → 返回 PDF 下载 |
| `GET /api/quotes?limit=` | 报价历史列表（最新在前） |
| `GET /api/quotes/{id}` | 取回某条历史报价 payload |
| `GET /api/quotes/{id}/pdf` | 重新渲染某条历史报价的 PDF |
| `PUT /api/admin/price` | 维护材料单价/机床时租：`{kind, key, field, value}`（当日市场克单价面板） |
| `GET /api/admin/overrides` | 查看当前所有价格覆盖 |

`params` 示例：

```json
{
  "material": "AL6061", "quantity": 10, "finish": "anodize_clear",
  "tight_tolerance": true, "requires_5axis": false, "rush": false,
  "min_wall_mm": 1.5,
  "holes": [{"diameter_mm": 6, "depth_mm": 20, "count": 4, "threaded": true}],
  "manual_dims": {"length_mm": 80, "width_mm": 40, "height_mm": 25, "volume_mm3": null}
}
```

无文件时用 `manual_dims` 兜底（STEP 无内核时前端自动切到此路径）。传 `"save": false`
可跳过入库（用于试算）。

`units`（`mm`/`inch`）按图纸单位缩放几何（STL 无单位，避免英寸件被当毫米导致
25.4× 误差）；尺寸异常会给单位告警。报价单含**零件重量**（物流）、**增值税**
（可配 `tax_rate`，默认 13%）、**有效期**（`quote_valid_days`，默认 30 天）与
**客户抬头**（`customer`）。

---

## 工时估算的三级精度 Estimator backends（复用开源 CAM）

工时不再只靠 V/MRR 标量，而是分级、可插拔，统一返回同一 `ProcessPlan`，所以
成本/PDF 不变。`/api/quote` 传 `"backend"` 选择，`/api/backends` 查可用性：

| 后端 | 精度 | 依赖 | 做法 |
|------|------|------|------|
| `analytic` | ~70%（漏斗粗算） | 无 | 去除体积 ÷ 材料调整后的 MRR（`capp.py`） |
| `toolpath` | 高 | trimesh + shapely（+opencamlib 可选） | **复用 CAM 内核**：trimesh 按 Z 分层切片，shapely 同心偏置生成开粗刀路、等高生成精加工壁刀路；曲面精加工用 **opencamlib 球刀 drop-cutter** 实测真实 3D 刀路斜率/曲率（飞面=1.0，曲面/斜面>1），刀路长度÷真实进给（`data/cutting.json`）得节拍 |
| `freecad` | 最高（真实刀路） | FreeCAD（conda） | 调 `freecadcmd` 让 **FreeCAD Path/CAM 工作台**自动编程、导出 G 代码，再由 `gcode_time` 积分真实节拍 |

`auto` = 有网格(STL/STEP 镶嵌) 且装了 trimesh/shapely 就用 `toolpath`，否则回退
`analytic`；任何一道工序仿真失败也会单独回退，保证总能出价。

```bash
pip install trimesh shapely scipy networkx      # 开启 toolpath（高精度）
conda install -c conda-forge freecad            # 开启 freecad（最高精度）
```

为什么这样设计：`gcode_time` 是通用件，能吃 FreeCAD / HeeksCNC / LinuxCNC /
手写的任意 G 代码；`toolpath` 直接复用 shapely 的多边形偏置（=所有 2.5D 挖槽
CAM 的核心算法）和 trimesh 的网格切片，不重造轮子。每一项仿真都在
`plan.operations` 里留有可追溯明细（分层数、刀路米数、进给）。

---

## 实时材料价格 Live material prices

材料费 = 毛坯重量 × 当日 ¥/kg，所以料价是准确度的关键。系统支持**接入国内实时
金属行情**，并把交易所基础金属价换算成成品棒料/板料价：

```
成品料价 ¥/kg = 交易所现货 ¥/kg × form_factor(合金/状态/型材溢价)
```

精度优先级：**手动改价 > 实时行情 > 静态参考价**。每条报价/PDF 都标注
`price_source`（如 `SHFE沪铝 2026-06-09 ×1.75` / `static` / `manual override`）。

### 选用的 API（中国国内）

| 数据源 | 实时性 | 费用 | 说明 |
|---|---|---|---|
| **SHFE 期货 · 新浪 `hq.sinajs.cn`**（默认适配器） | 实时(盘中) | 免费、无 key | 沪铝/铜/锌/镍/锡/不锈钢主力，¥/吨→¥/kg；本仓库已实现 `SinaShfeFeed` |
| Eastmoney 行情 | 实时 | 免费 | SHFE 同源、JSON 更干净，可作备选适配器 |
| 上海有色 SMM / 我的钢铁 Mysteel | 现货权威 | 企业付费 | 最准现货，需商务对接 |
| metalpriceapi / juhe 聚合数据 | 准实时 | 需 key | 国际/聚合，覆盖一般 |

期货主连作现货代理会有基差，由 `form_factor` 吸收系统性偏差；要更准的**现货**
请接 SMM/Mysteel。`form_factor` 应按你的分销商实际报价标定。

### 开关与加固

```bash
CNC_PRICE_FEED=sina     # 默认 none（纯静态价，行为不变）；设为 sina 启用 SHFE
CNC_PRICE_TTL=900       # 行情缓存/刷新间隔(秒)
```

- 适配器解析有**区间校验**（剔除离谱的 ¥/吨），缓存**陈旧上限 6 小时**——
  行情持续中断超过该时长即放弃缓存、回退静态价，绝不拿隔夜价报价。
- feed 失败/超时/网络不通 → 优雅回退静态价（无 feed 时一切如旧）。
- 接口：`GET /api/prices`（当前生效料价+来源）、`POST /api/prices/refresh`（需管理员）。
- ⚠️ 沙箱/生产若有出站白名单，需放行 `hq.sinajs.cn`（本开发环境被网络策略拦截，
  已验证回退逻辑；解析逻辑用录制样本做了单测）。

---

## 鉴权 Authentication

价格维护类接口（`PUT /api/admin/price`、`GET /api/admin/overrides`）需要管理员
登录；其余报价/查询接口公开。实现为纯标准库（无 bcrypt/jwt 依赖）：

- 密码 PBKDF2-HMAC-SHA256（随机盐、20万次迭代），常量时间比对。
- 会话为 HMAC 签名的无状态令牌 `{用户名, 角色, 过期}`，有效期 8 小时。
- 登录按客户端 IP 限流（60 秒内 8 次失败触发 429），缓解暴力破解。

环境变量：

```bash
CNC_ADMIN_USER=admin            # 默认 admin
CNC_ADMIN_PASSWORD=<强密码>      # 未设则首次播种 admin/admin 并打印告警
CNC_SECRET=<令牌签名密钥>         # 未设则生成并持久化到 DB（重启后令牌仍有效）
```

接口：`POST /api/login {username,password}` → `{token,role,expires_in}`；
`GET /api/me`（带 `Authorization: Bearer <token>`）→ 当前用户；管理接口需带同样的
Bearer 头。前端有登录弹窗，令牌存 localStorage，过期/失效自动重新登录。

> ⚠️ 令牌经请求头明文传输——**生产务必置于 HTTPS/反向代理之后**。多 worker 部署时
> 登录限流需换成 Redis（当前为单进程内存）。

---

## 持久化与价格维护 Persistence & price admin（第三阶段）

- 每次报价自动存入 SQLite（`cnc/store.py`），前端「最近报价」面板可点开重看或重下 PDF。
- 估价员通过 `PUT /api/admin/price` 改材料单价/机床时租，立即对**新**报价生效（基础
  JSON 不变，覆盖值叠加在 `apply_overrides()` 里），无需重启。
- DB 路径默认 `cnc/data/quotes.db`，用 `CNC_DB=/path/to.db` 覆盖；已加入 `.gitignore`。

```bash
# 例：把铝6061当日克单价改成 ¥42/kg
curl -X PUT localhost:8500/api/admin/price \
  -H 'Content-Type: application/json' \
  -d '{"kind":"material","key":"AL6061","field":"price_cny_per_kg","value":42}'
```

---

## STEP / IGES 支持

`.stl` 开箱即用。要解析 `.step/.stp/.igs/.iges` 需安装 OpenCASCADE：

```bash
conda install -c conda-forge pythonocc-core
```

装上后系统会用 OCCT 读包围盒/体积/表面积，并把实体网格化成 STL 供前端 3D 预览。
未安装时返回明确的 422 并提示改用 STL 或手动尺寸——系统始终可用。

---

## 与生产系统的差距（路线图）

本实现刻意停在“可用且可信”的 MVP：

- **第三阶段**：✅ 报价历史持久化 + 价格维护接口已实现（SQLite）；待对接供应商
  API 自动拉取当日料价、加入支付网关。
- **大文件**：当前内联解析并限制 ≤60MB；生产应改为 Celery 异步队列。
- **特征识别**：现为几何信号 + 人工声明；可进一步做真正的孔/型腔/清角自动识别。
- **工时精度**：✅ 已接入开源 CAM——`toolpath`（trimesh+shapely 刀路仿真 +
  opencamlib drop-cutter 曲面精加工）与 `freecad`（真实 G 代码）；下一步可把
  feeds/speeds 表从「按材料」细化到「材料×刀具直径」，并用真实工时反标定系数。
- **AI 进阶**：积累报价历史后，用神经网络直接由几何特征预测工时（Xometry 路线）。
