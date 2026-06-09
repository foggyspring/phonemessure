# CNC 自动加工报价系统 · Automatic CNC Machining Quote Engine

把一张 3D 图纸变成一份可下载的报价单。完整漏斗：

```
[前端 3D 看板] → [几何解析引擎] → [工艺/工时规划 CAPP] → [成本/利润核算] → [PDF 报价单]
   Three.js          STL / STEP         开粗→精→钻→攻        材料+机时+表处+利润     reportlab
```

这是 MVP 阶段（第一/二阶段）的可运行实现：STL 全自动解析，STEP/IGES 在装有
OpenCASCADE 时自动解析、否则降级为手动输入尺寸；几何特征（孔/螺纹/公差/薄壁/
五轴）由前端声明 + 几何信号共同驱动，符合“STEP 通常不含 PMI 公差信息”的现实。

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
| 工艺工时 | `cnc/engine/capp.py` | 毛坯=包围盒+余量；去除体积/MRR；开粗→精加工→钻孔→攻丝；装夹/换刀/编程 |
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
- **AI 进阶**：积累报价历史后，用神经网络直接由几何特征预测工时（Xometry 路线）。
