"""Generates the quote-result panel mockup for the docs (emoji-free, dark theme).
Shows the dimensions added across the multi-role iterations. Re-run:
    python doc/make_quote_mockup.py
"""
from PIL import Image, ImageDraw, ImageFont

C = dict(bg="#0b0f15", panel="#161b22", panel2="#1c232c", border="#2a323c",
         border2="#39424d", text="#e6edf3", muted="#8b949e", accent="#2f81f7",
         green="#3fb950", amber="#d29922", red="#d9534f")
CJK = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
def f(sz): return ImageFont.truetype(CJK, sz)

W, H = 820, 720
img = Image.new("RGB", (W, H), C["bg"]); d = ImageDraw.Draw(img)
def rr(xy, r, fill=None, outline=None, w=1): d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=w)

def card(x, y, w, h, title):
    rr((x, y, x+w, y+h), 12, fill=C["panel"], outline=C["border"])
    d.text((x+18, y+14), title, font=f(13), fill=C["muted"])

# ---- left: price card ----
card(24, 24, 372, 672, "单件报价 Unit price")
d.text((40, 60), "¥ 123.75", font=f(40), fill=C["text"])
d.text((40, 116), "× 10 件 · 净额 ¥1,237.50 · 含税 ¥1,398.38 (增值税 13%)",
       font=f(11), fill=C["muted"])
d.text((40, 134), "交期 13 天（约 2026-06-22 交付）", font=f(11), fill=C["muted"])
d.text((40, 156), "价格主要由 加工(41%)、材料(27%)、去毛刺/增项(16%) 构成；",
       font=f(11), fill=C["muted"])
d.text((40, 172), "提高数量可摊薄一次性编程/装夹费用。", font=f(11), fill=C["muted"])
# confidence + range
rr((40, 196, 196, 222), 12, fill="#16341f", outline=C["green"])
d.text((52, 201), "报价置信度 89/100 · 高 High", font=f(11), fill=C["green"])
d.text((206, 203), "参考区间 ¥113.85–133.65 (±8%)", font=f(10), fill=C["muted"])
# cost breakdown
rows = [("材料费 Material", "¥25.65"), ("加工费 Machining", "¥47.08"),
        ("表面处理 Finishing", "¥15.00"), ("去毛刺/增项 Post-process", "¥17.50"),
        ("编程摊销 Setup/ea", "¥7.58"), ("单件成本 Unit cost", "¥112.81"),
        ("利润率 Margin", "30 %")]
yy = 244
for i, (k, v) in enumerate(rows):
    bold = i >= 5
    d.text((40, yy), k, font=f(12), fill=C["muted"] if not bold else C["text"])
    tw = d.textlength(v, font=f(12))
    d.text((380-tw-16, yy), v, font=f(12), fill=C["text"])
    d.line((40, yy+22, 380, yy+22), fill=C["border"], width=1)
    yy += 30
# tier table with save
d.text((40, yy+6), "阶梯报价 Quantity breaks", font=f(11), fill=C["muted"]); yy += 28
for q, u, sv in [("1","¥216.72",""),("10","¥123.75","-43%"),("50","¥120.18","-45%")]:
    d.text((48, yy), q, font=f(12), fill=C["text"])
    d.text((110, yy), u, font=f(12), fill=C["text"])
    if sv:
        d.text((220, yy), sv, font=f(11), fill=C["green"])
    yy += 24

# ---- right top: DFM summary + findings ----
card(412, 24, 384, 300, "工艺提示 Notes & DFM")
d.rectangle((430, 58, 433, 92), fill=C["green"])
d.text((444, 58), "制造风险概览 — 无明显可加工性风险，可顺利加工",
       font=f(11), fill=C["text"])
d.text((444, 74), "（高 0 · 中 0 · 低 0）", font=f(10), fill=C["muted"])
findings = [("info", "螺纹底孔 Tap-drill", "M6 螺纹孔底孔钻 Ø5.0mm（=公称−螺距）"),
            ("info", "内角圆角 Inner radius", "CNC 内壁转角受刀具直径限制必带 R 角")]
yy = 108
for sev, t, det in findings:
    rr((430, yy, 778, yy+44), 6, fill=C["panel2"])
    d.text((442, yy+8), t, font=f(11), fill=C["text"])
    d.text((442, yy+24), det, font=f(10), fill=C["muted"])
    yy += 52
# process routing
d.text((430, yy+4), "工艺路线 Process routing", font=f(10), fill=C["muted"]); yy += 22
for i, (nm, mn) in enumerate([("下料/备料","—"),("编程/首件","75.8 min"),
        ("装夹","24.0 min"),("粗铣","1.7 min"),("精铣","19.5 min"),("钻孔","0.4 min")]):
    d.text((438, yy), f"{i+1}. {nm}", font=f(10), fill=C["muted"])
    tw=d.textlength(mn, font=f(10)); d.text((778-tw, yy), mn, font=f(10), fill=C["muted"])
    yy += 17

# ---- right bottom: assumptions ----
card(412, 344, 384, 352, "报价假设 Quote assumptions")
assums = ["公差按 标准 ±0.1（仅关键尺寸需紧公差请注明）",
          "孔为模型自动识别，均按未攻丝通孔计；如需螺纹请注明",
          "表面处理：本色阳极氧化 (Clear anodize)",
          "未含热处理/去应力/阳极硬化等特殊工艺（如需请注明）",
          "毛坯按标准板/棒料，单面留加工余量；质保书如需请注明",
          "默认三轴加工；如有倒扣/侧孔需五轴请注明"]
yy = 384
for a in assums:
    d.text((430, yy), "·", font=f(12), fill=C["accent"])
    # wrap
    line=a
    while d.textlength(line, font=f(11)) > 350:
        line=line[:-1]
    d.text((444, yy), a if d.textlength(a, font=f(11))<=350 else line+"…",
           font=f(11), fill=C["muted"])
    yy += 26
# crate + min-order notes
d.text((430, yy+8), "预估运费（16.9 kg，含木箱 ¥120）：¥347.50", font=f(10), fill=C["muted"])

img.save("/home/user/phonemessure/doc/img/ui-quote-result.png")
print("wrote doc/img/ui-quote-result.png")
