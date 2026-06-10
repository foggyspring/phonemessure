# 技能库知识出处 Skill knowledge sources

知识类技能(`cnc/data/skills.json` 中 `kind: knowledge`)的事实依据与专业出处。
每次充实/修订知识条目时同步更新本表,保证话术可溯源、可复核。

| 技能 key | 关键事实 | 出处 |
|----------|----------|------|
| `faq_iso2768` / `faq_tolerance` | ISO 2768 f/m/c/v 四级;m 级 30-120mm 段 ±0.3、6-30 段 ±0.2 等;紧公差只标关键尺寸可省 20-40% 周期 | [AmesWeb ISO 2768-1 表](https://amesweb.info/fits-tolerances/iso-2768-linear-dimensions-tolerances.aspx) · [RpProto ISO 2768 指南](https://www.rpproto.com/blog/iso-2768-tolerance-guide) · [Xometry ISO 2768](https://www.xometry.com/resources/certifications/iso-2768/) · [Hubs 降本设计](https://www.hubs.com/knowledge-base/reducing-cnc-machining-costs-design-tips/) |
| `faq_corner_pocket` | 内角 R ≥ 腔深 1/3;腔深 ≤ 4×宽;标准端铣 3:1、加长 6:1,再深用 EDM/双面 | [Protolabs CNC 铣削设计指南](https://www.protolabs.com/services/cnc-machining/cnc-milling/design-guidelines/) · [Hubs CNC 设计指南](https://www.hubs.com/guides/cnc-machining/) · [MakerStage CNC 设计准则](https://www.makerstage.com/resources/cnc-design-guidelines) |
| `faq_deep_hole` | 深径比 >3-4 即深孔需啄钻(每 1-2×D 退刀);>10:1 超深孔;枪钻可达 400:1 | [CNC Cookbook 深孔钻削](https://www.cnccookbook.com/deep-hole-drilling/) · [UNISIG 深孔加工](https://unisig.com/information-and-resources/what-is-deep-hole-drilling/) · [Wikipedia Deep hole drilling (VDI 3210)](https://en.wikipedia.org/wiki/Deep_hole_drilling) |
| `faq_tap_drill` | 底孔 ≈ 公称−螺距(M3×0.5→2.5 … M8×1.25→6.75),≈70-75% 牙高率 | [Carbide Depot 公制攻丝表](https://www.carbidedepot.com/formulas-tap-metric.htm) · [AmesWeb 公制底孔表](https://amesweb.info/Screws/metric-tap-drill-chart.aspx) · [TR Fastenings](https://www.trfastenings.com/Knowledge-Base/Engineering-Data/tapping-sizes-and-clearance-holes) |
| `faq_roughness` | Ra 3.2=标准 as-machined;1.6 液压/外壳;0.8 密封/精配;0.4 镜面需抛光 | [Geomiq 粗糙度指南](https://geomiq.com/blog/cnc-machining-surface-roughness-guide/) · [RapidDirect 粗糙度表](https://www.rapiddirect.com/blog/surface-roughness-chart/) · [Get It Made](https://get-it-made.co.uk/resources/surface-roughness-explained) |
| `faq_titanium` | TC4 导热 6.7 W/m·K(≈钢 1/6),加工硬化 +10-20%,铣削线速度建议低区间 | [MakerStage 为什么钛难加工](https://www.makerstage.com/resources/why-is-titanium-hard-to-machine) · [Zenithin TC4 加工指南](https://www.zenithinmfg.com/machining-titanium-alloys-guide/) · [PTSMAKE TC4](https://www.ptsmake.com/how-to-effectively-machine-titanium-grade-5-ti-6al-4v/) |
| `faq_stainless` / `faq_thin_wall` | 304/316 加工硬化:保持进给、勿轻蹭;导热 ~16 W/m·K;不锈钢壁厚 ≥1mm,铝可 0.5mm(非承力) | [MakerStage](https://www.makerstage.com/resources/why-is-titanium-hard-to-machine)(对比数据) · [Protolabs 设计指南](https://www.protolabs.com/services/cnc-machining/cnc-milling/design-guidelines/) · [Richconn 壁厚](https://richconn.com/cnc-machining-wall-thickness/) |
| `faq_anodize_dim` | MIL-A-8625:II 型 5-20µm(单边长大 ~5µm 可忽略);III 型硬质可达 50µm+,一半渗透一半生长,单边 ~25µm,精密面需预留/保护 | [Anoplate 硬质阳极](https://www.anoplate.com/finishes/hardcoat-anodize/) · [Lightmetals II vs III](https://www.lightmetalscoloring.com/type-ii-vs-type-iii-anodize) · [SAF MIL-A-8625](https://www.saf.com/how-to-specify/military-specification-anodizing-mil-a-8625/) |
| `faq_cost_design` | 紧公差省 20-40%;孔深≤4×D;标准钻径;少装夹;数量到甜点 | [Hubs 14 条降本设计](https://www.hubs.com/knowledge-base/reducing-cnc-machining-costs-design-tips/) · [Xometry CNC 设计指南 PDF](https://cdn2.hubspot.net/hubfs/340051/Design_Guides/Xometry_DesignGuide_CNCMachining.pdf) |
| `faq_material_choice` / `faq_finish` / `faq_unit` / `faq_batch` | 通识性经验,与本系统材料库/交期模型一致 | 系统内置数据 (`cnc/data/materials.json`、成本模型 [cost-model.md](cost-model.md)) |

> 维护约定:改动知识话术时,若引入新事实,先核对出处再入库;
> 运营在管理面板改话术属于"口径调整",不应改变数字事实。
