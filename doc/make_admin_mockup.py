"""Generates the tabbed price-maintenance (admin) panel mockup for the docs.
Emoji-free; matches the app's dark theme. Re-run after UI changes:
    python doc/make_admin_mockup.py
"""
from PIL import Image, ImageDraw, ImageFont

C = dict(bg="#0b0f15", panel="#161b22", panel2="#1c232c", border="#2a323c",
         border2="#39424d", text="#e6edf3", muted="#8b949e", accent="#2f81f7")
CJK = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
def f(sz): return ImageFont.truetype(CJK, sz)

W, H = 760, 560
img = Image.new("RGB", (W, H), C["bg"])
d = ImageDraw.Draw(img)

def rrect(xy, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)

# modal card
mx, my, mw, mh = 40, 30, W - 80, H - 60
rrect((mx, my, mx + mw, my + mh), 12, fill=C["panel"], outline=C["border2"])
# header
d.text((mx + 22, my + 20), "价格维护 Price maintenance", font=f(17), fill=C["text"])
d.text((mx + mw - 40, my + 18), "×", font=f(20), fill=C["muted"])
d.text((mx + 22, my + 50), "改动立即对新报价生效（叠加在基础数据上，不改源文件）。",
       font=f(12), fill=C["muted"])

# tab bar
tabs = ["材料", "机床", "表面处理", "商务", "系数", "工时", "切削"]
tx, ty = mx + 22, my + 78
d.line((mx + 22, ty + 30, mx + mw - 22, ty + 30), fill=C["border"], width=1)
cur = tx
for i, t in enumerate(tabs):
    w = d.textlength(t, font=f(13)) + 24
    if i == 0:
        d.text((cur + 12, ty + 6), t, font=f(13), fill=C["accent"])
        d.line((cur, ty + 30, cur + w, ty + 30), fill=C["accent"], width=2)
    else:
        d.text((cur + 12, ty + 6), t, font=f(13), fill=C["muted"])
    cur += w + 4

# active pane title
py = ty + 48
d.text((mx + 22, py), "材料单价 MATERIAL ¥/KG", font=f(11), fill=C["muted"])

# grid of editable rows (2 columns)
rows = [("铝 6061", "35.0"), ("铝 7075", "62.0"), ("不锈钢 304", "28.0"),
        ("不锈钢 316", "46.0"), ("黄铜 C360", "70.0"), ("紫铜 C110", "86.0"),
        ("钛合金 TC4", "380.0"), ("POM 赛钢", "32.0")]
gx, gy = mx + 22, py + 26
colw = (mw - 44 - 14) // 2
rh = 46
for i, (label, val) in enumerate(rows):
    col, r = i % 2, i // 2
    x = gx + col * (colw + 14)
    y = gy + r * (rh + 8)
    d.text((x, y), label, font=f(11), fill=C["muted"])
    rrect((x, y + 16, x + colw, y + 16 + 24), 6, fill=C["panel2"], outline=C["border"])
    d.text((x + 10, y + 21), val, font=f(13), fill=C["text"])
    d.text((x + colw - 26, y + 21), "¥", font=f(12), fill=C["muted"])

# footer button
bw, bh = 150, 32
bx, by = mx + mw - 22 - bw, my + mh - 22 - bh
rrect((bx, by, bx + bw, by + bh), 7, fill=C["accent"])
d.text((bx + 22, by + 8), "保存并刷新报价", font=f(13), fill="#ffffff")

img.save("/home/user/phonemessure/doc/img/ui-admin-tabs.png")
print("wrote doc/img/ui-admin-tabs.png")
