"""Generates the remaining UI mockups for the user guide (emoji-free, dark theme).

Real-page screenshots aren't possible here (the headless-browser binary download
is blocked by the network policy), so these are high-fidelity mockups that match
the app's actual palette (cnc/static/style.css) and on-screen content. Re-run:
    python doc/make_feature_mockups.py
"""
from PIL import Image, ImageDraw, ImageFont

C = dict(bg="#0b0f15", panel="#161b22", panel2="#1c232c", panel3="#222b35",
         border="#2a323c", border2="#39424d", text="#e6edf3", muted="#8b949e",
         muted2="#6e7781", accent="#2f81f7", accent2="#3fb950", warn="#d29922",
         danger="#f85149")
CJK = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


def F(sz):
    return ImageFont.truetype(CJK, sz)


def new(w, h):
    img = Image.new("RGB", (w, h), C["bg"])
    return img, ImageDraw.Draw(img)


def rr(d, xy, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def topbar(d, w, subtitle):
    rr(d, (0, 0, w, 58), 0, fill="#12181f")
    d.line((0, 58, w, 58), fill=C["border"], width=1)
    d.text((24, 14), "◤", font=F(26), fill=C["accent"])
    d.text((58, 13), "CNC 智能报价 CNC Quote Engine", font=F(16), fill=C["text"])
    d.text((58, 36), subtitle, font=F(11), fill=C["muted"])
    # health dot + login chip
    d.ellipse((w - 250, 26, w - 242, 34), fill=C["accent2"])
    d.text((w - 236, 22), "OCCT 就绪 · STEP 可解析", font=F(11), fill=C["muted"])
    rr(d, (w - 96, 16, w - 20, 42), 13, fill=C["panel2"], outline=C["border2"])
    d.text((w - 84, 22), "价格维护", font=F(12), fill=C["text"])


def card(d, x, y, w, h, title=None):
    rr(d, (x, y, x + w, y + h), 14, fill=C["panel"], outline=C["border"])
    if title:
        d.text((x + 18, y + 15), title, font=F(15), fill=C["text"])


def chip(d, x, y, text, *, fill=None, outline=None, tc=None, fsz=12, pad=11):
    w = d.textlength(text, font=F(fsz)) + pad * 2
    rr(d, (x, y, x + w, y + 26), 13, fill=fill, outline=outline)
    d.text((x + pad, y + 5), text, font=F(fsz), fill=tc or C["text"])
    return x + w + 7


# ───────────────────────────── 1. AI research mode ─────────────────────────
def research():
    W, H = 460, 760
    img, d = new(W, H)
    # sidebar panel
    rr(d, (0, 0, W, H), 0, fill=C["panel"])
    d.line((0, 52, W, 52), fill=C["border"], width=1)
    d.text((18, 12), "AI 报价助手", font=F(15), fill=C["text"])
    d.text((18, 32), "研究模式 · 已接入离线分析引擎", font=F(11), fill=C["accent2"])
    d.text((W - 30, 14), "×", font=F(20), fill=C["muted"])

    # progress strip
    y = 64
    stages = [("需求澄清", "done"), ("几何/DFM", "done"), ("选材权衡", "on"),
              ("批量/交期", ""), ("决策简报", "")]
    x = 16
    for i, (label, st) in enumerate(stages):
        if st == "on":
            wseg = d.textlength(label, font=F(11)) + 14
            rr(d, (x, y, x + wseg, y + 20), 10, fill=C["accent"])
            d.text((x + 7, y + 3), label, font=F(11), fill="#fff")
            x += wseg
        else:
            col = C["accent2"] if st == "done" else C["muted2"]
            d.text((x + 4, y + 3), label, font=F(11), fill=col)
            x += d.textlength(label, font=F(11)) + 8
        if i < len(stages) - 1:
            d.text((x, y + 3), "→", font=F(10), fill=C["muted2"]); x += 14
    d.line((0, y + 30, W, y + 30), fill=C["border"], width=1)

    # messages
    def bot(yy, lines, h):
        rr(d, (16, yy, W - 70, yy + h), 10, fill=C["panel2"])
        ty = yy + 9
        for ln in lines:
            d.text((28, ty), ln, font=F(12), fill=C["text"]); ty += 18
        return yy + h + 10

    def user(yy, text):
        wtxt = d.textlength(text, font=F(12)) + 24
        rr(d, (W - 16 - wtxt, yy, W - 16, yy + 30), 10, fill="#1f3a5f")
        d.text((W - 16 - wtxt + 12, yy + 7), text, font=F(12), fill=C["text"])
        return yy + 40

    yy = 108
    yy = bot(yy, ["开始研究前，先确认几个影响结论的关键点：",
                  "· 这是承力/结构件还是外观件？",
                  "· 本批数量大约多少件？",
                  "· 交期有硬约束吗？"], 88)
    yy = user(yy, "承力件，本批20件，30天内要")
    yy = bot(yy, ["已记录研究前提：承力件；批量 20 件；交期 ≤30 天。",
                  "几何/DFM 评审完成：铝7075 ×20 单价 ¥163.28，",
                  "无明显可加工性风险，置信度 89/100。"], 72)
    yy = user(yy, "继续")
    yy = bot(yy, ["选材权衡完成。当前材料即为合理选择",
                  "（更省的替代均强度不足，承力件不建议）。",
                  "因承力要求剔除：铝6061（强度仅 54%）。",
                  "下一步分析批量价格甜点与交期方案。"], 90)

    # next-step suggestion chips
    d.text((28, yy + 2), "下一步建议：", font=F(11), fill=C["muted"]); yy += 22
    rr(d, (18, yy, 18 + d.textlength("分析批量/交期", font=F(12)) + 22, yy + 26), 13,
       fill="#16263f", outline=C["accent"])
    d.text((29, yy + 5), "分析批量/交期", font=F(12), fill=C["accent"])
    x2 = 18 + d.textlength("分析批量/交期", font=F(12)) + 29
    rr(d, (x2, yy, x2 + d.textlength("先细看 DFM 风险", font=F(12)) + 22, yy + 26), 13,
       fill="#16263f", outline=C["accent"])
    d.text((x2 + 11, yy + 5), "先细看 DFM 风险", font=F(12), fill=C["accent"])

    # input box
    rr(d, (16, H - 58, W - 90, H - 16), 9, fill=C["panel2"], outline=C["border"])
    d.text((28, H - 46), "回复，或随时输入“退出研究”返回自由问答…", font=F(11), fill=C["muted2"])
    rr(d, (W - 78, H - 58, W - 16, H - 16), 9, fill=C["accent"])
    d.text((W - 64, H - 46), "发送", font=F(13), fill="#fff")
    img.save("doc/img/ui-ai-research.png")
    print("wrote doc/img/ui-ai-research.png")


# ───────────────────────────── 2. Batch quote ──────────────────────────────
def batch():
    W, H = 980, 560
    img, d = new(W, H)
    topbar(d, W, "批量导入 · 多文件 BOM 报价")
    x, y, w = 24, 80, W - 48
    card(d, x, y, w, H - 104, "批量报价 Batch quote")
    # aggregate line
    d.text((x + 18, y + 44),
           "3/4 件成功 · 净额 ¥3,094.80 · 含税 ¥3,497.12 · 最长交期 10 天 · "
           "总重 8.54 kg · 预估运费 ¥127.49",
           font=F(12), fill=C["text"])
    rr(d, (x + 18, y + 66, x + 470, y + 86), 9, fill="#3a2a14", outline=C["warn"])
    d.text((x + 26, y + 68), "风险 高1/中0，建议复核：plate.stl", font=F(11), fill=C["warn"])

    # table
    ty = y + 104
    cols = [("零件 Part", x + 18), ("单价 Unit", x + 360), ("数量", x + 470),
            ("小计 Subtotal", x + 560), ("交期", x + 720), ("风险", x + 800), ("置信", x + 880)]
    for label, cx in cols:
        d.text((cx, ty), label, font=F(11), fill=C["muted"])
    d.line((x + 14, ty + 22, x + w - 14, ty + 22), fill=C["border"], width=1)
    ty += 30
    rows = [
        ("bracket.stl", "¥123.75", "10", "¥1,237.50", "10 天", ("无", C["accent2"]), "89", True),
        ("plate.stl", "¥106.01", "10", "¥1,060.10", "10 天", ("高", C["danger"]), "89", True),
        ("broken.stl", "解析失败：STL parsed but contained zero triangles", "", "", "", None, "", False),
        ("block.stl", "¥79.72", "10", "¥797.20", "10 天", ("无", C["accent2"]), "89", True),
    ]
    for i, r in enumerate(rows):
        if i % 2 == 1:
            rr(d, (x + 14, ty - 4, x + w - 14, ty + 24), 6, fill="#19202a")
        name, unit, qty, sub, lead, risk, conf, ok = r
        d.text((x + 18, ty + 2), name, font=F(12), fill=C["text"] if ok else C["muted"])
        if ok:
            d.text((x + 360, ty + 2), unit, font=F(12), fill=C["text"])
            d.text((x + 470, ty + 2), qty, font=F(12), fill=C["text"])
            d.text((x + 560, ty + 2), sub, font=F(12), fill=C["text"])
            d.text((x + 720, ty + 2), lead, font=F(12), fill=C["text"])
            d.text((x + 800, ty + 2), risk[0], font=F(12), fill=risk[1])
            d.text((x + 880, ty + 2), conf, font=F(12), fill=C["text"])
        else:
            d.text((x + 360, ty + 2), unit, font=F(11), fill=C["muted"])
        ty += 30
    d.text((x + 18, H - 44),
           "整单口径：起订额只兜底一次 · 运费按合并重量一票计 · 交期取最长件。点行可载入该零件细看。",
           font=F(11), fill=C["muted"])
    img.save("doc/img/ui-batch.png")
    print("wrote doc/img/ui-batch.png")


if __name__ == "__main__":
    research()
    batch()
