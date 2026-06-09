# 价格/参数维护 · Price & Parameter Maintenance

所有报价相关参数均可由管理员在运行时维护，叠加在基础数据上，**不修改源文件**，
改动立即对新报价生效，并写入审计日志（可单条/全部回退）。

## 面板 UI

为避免单页过长，维护面板按**标签页**组织（材料 / 机床 / 表面处理 / 商务 / 系数 /
工时 / 切削），每页只展示该类参数：

![价格维护标签页](img/ui-admin-tabs.png)

## 可维护参数一览

| 标签页 | 参数 | 接口 kind |
|--------|------|-----------|
| 材料 | 单价 ¥/kg、密度、机加工性、毛坯系数、废料抵扣率、抗拉强度 tensile_mpa | `material` |
| 机床 | 时租 ¥/h、基础 MRR、最大轴数 | `machine` |
| 表面处理 | 起步费、每 dm² 单价、保底费 | `finish` |
| 商务 | 利润率、税率、紧公差利润加成、加急系数、去毛刺基价/单价、包装、运费、起订额、报价有效期 | `business` |
| 系数 | 交期档(factor/days)、公差等级(margin/factor/inspection)、表面等级、增项费 | `business`（嵌套路径） |
| 工时 | 编程基准/复杂度、首件、装夹/换刀工时、紧公差系数、最小机时、毛坯余量 | `capp` |
| 切削 | 按材料的 Vc/fz/切深/钻孔/攻丝进给 | `cutting` |

接口：`PUT /api/admin/price`（白名单校验 + 审计）、`DELETE /api/admin/price`
（单条回退或 `scope=all`）、`GET /api/admin/config`（当前全量）、`GET /api/admin/audit`。

> 注：UI 全面禁用 emoji，文案中英双语；改动需管理员登录。
