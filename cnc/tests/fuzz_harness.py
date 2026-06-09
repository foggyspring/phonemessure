"""Standalone fuzz/property harness — run: PYTHONPATH=. python cnc/tests/fuzz_harness.py [N] [seed]

NOT collected by pytest (no test_ prefix). Generates diverse + pathological
parts, runs the full quote pipeline, and checks invariants. The permanent
regression subset lives in test_invariants.py."""
import math, random, time, traceback, sys
import trimesh
import numpy as np

from cnc.engine import load
from cnc.geometry import analyze, metrics_from_stl_bytes, Hole, GeometryError
from cnc import estimators
from cnc.engine import costing as costing_mod
from cnc.service import build_quote, QuoteRequest, QuoteError

shop = load()
MATS = list(shop.materials)
FIN_OK = {k: list(m.finish_ok) for k, m in shop.materials.items()}

def mk_pathological(rng):
    """Deliberately broken/degenerate meshes a user might upload."""
    t = rng.choice(["open_box","single_tri","inverted","sliver","huge","sub_mm",
                    "nonmanifold","duplicate_face","needle"])
    if t=="open_box":   # box with the top face removed -> not watertight
        m=trimesh.creation.box(extents=(40,40,40)); m.apply_translation((20,20,20))
        keep=[i for i,f in enumerate(m.faces) if not np.all(m.vertices[f][:,2]>39.0)]
        m.update_faces(keep); return t,m
    if t=="single_tri":
        return t, trimesh.Trimesh(vertices=[[0,0,0],[50,0,0],[0,40,5]],faces=[[0,1,2]])
    if t=="inverted":   # flipped winding -> negative volume
        m=trimesh.creation.box(extents=(40,30,20)); m.apply_translation((20,15,10)); m.invert(); return t,m
    if t=="sliver":     # near-zero thickness
        return t, (lambda m:(m.apply_translation((25,25,0.05)),m)[1])(trimesh.creation.box(extents=(50,50,0.1)))
    if t=="huge":       # very large part
        m=trimesh.creation.box(extents=(1800,900,400)); m.apply_translation((900,450,200)); return t,m
    if t=="sub_mm":
        m=trimesh.creation.box(extents=(0.6,0.6,0.6)); m.apply_translation((0.3,0.3,0.3)); return t,m
    if t=="nonmanifold":
        b=trimesh.creation.box(extents=(40,40,40)); b.apply_translation((20,20,20))
        b2=trimesh.creation.box(extents=(20,20,40)); b2.apply_translation((40,40,20))  # share an edge
        return t, trimesh.util.concatenate([b,b2])
    if t=="duplicate_face":
        m=trimesh.creation.box(extents=(30,30,30)); m.apply_translation((15,15,15))
        m=trimesh.util.concatenate([m,m]); return t,m
    if t=="needle":
        return t, (lambda m:(m.apply_translation((1,1,150)),m)[1])(trimesh.creation.box(extents=(2,2,300)))
    return t, trimesh.creation.box(extents=(40,40,40))

def mk_mesh(rng):
    """Return (name, trimesh) for a random part archetype."""
    if rng.random() < 0.22:
        return mk_pathological(rng)
    t = rng.choice(["box","cyl","cone","sphere","pocket","holes","plate","bar",
                    "Lshape","domed","stack","tiny","tall"])
    def box(l,w,h,tr=(0,0,0)):
        m=trimesh.creation.box(extents=(l,w,h)); m.apply_translation((l/2+tr[0],w/2+tr[1],h/2+tr[2])); return m
    if t=="box":   return t, box(rng.uniform(10,200),rng.uniform(10,150),rng.uniform(5,120))
    if t=="cyl":   m=trimesh.creation.cylinder(radius=rng.uniform(5,60),height=rng.uniform(5,120),sections=rng.choice([16,32,48,64])); m.apply_translation((0,0,m.bounds[1][2])); return t,m
    if t=="cone":  m=trimesh.creation.cone(radius=rng.uniform(5,60),height=rng.uniform(10,100),sections=rng.choice([24,48])); return t,m
    if t=="sphere":m=trimesh.creation.icosphere(subdivisions=rng.choice([2,3]),radius=rng.uniform(8,50)); return t,m
    if t=="plate": return t, box(rng.uniform(40,200),rng.uniform(40,200),rng.uniform(1,4))
    if t=="bar":   return t, box(rng.uniform(60,250),rng.uniform(4,12),rng.uniform(4,12))
    if t=="tiny":  return t, box(rng.uniform(2,8),rng.uniform(2,8),rng.uniform(2,8))
    if t=="tall":  return t, box(rng.uniform(15,40),rng.uniform(15,40),rng.uniform(80,180))
    if t=="pocket":
        L,W,H=rng.uniform(60,160),rng.uniform(40,120),rng.uniform(20,60)
        b=box(L,W,H); pk=box(L*0.7,W*0.7,H*0.6); pk.apply_translation((L*0.15,W*0.15,H*0.4))
        try: return t, b.difference(pk)
        except Exception: return "box", b
    if t=="holes":
        L,W,H=rng.uniform(40,120),rng.uniform(40,120),rng.uniform(10,30)
        b=box(L,W,H); cyls=[]
        for _ in range(rng.randint(1,4)):
            c=trimesh.creation.cylinder(radius=rng.uniform(2,6),height=H*1.5,sections=24)
            c.apply_translation((rng.uniform(8,L-8),rng.uniform(8,W-8),H/2))
            cyls.append(c)
        try:
            for c in cyls: b=b.difference(c)
            return t,b
        except Exception: return "box",b
    if t=="Lshape":
        a=box(rng.uniform(60,140),rng.uniform(20,50),rng.uniform(15,40))
        c=box(rng.uniform(20,50),rng.uniform(60,140),rng.uniform(15,40))
        try: return t, a.union(c)
        except Exception: return "box", a
    if t=="domed":
        L=rng.uniform(40,90); b=box(L,L,rng.uniform(15,40))
        s=trimesh.creation.icosphere(subdivisions=2,radius=L/2); s.apply_translation((L/2,L/2,b.bounds[1][2]))
        try: return t, b.union(s)
        except Exception: return "box", b
    if t=="stack":
        a=box(rng.uniform(40,100),rng.uniform(40,100),rng.uniform(10,30))
        b2=box(rng.uniform(20,60),rng.uniform(20,60),rng.uniform(10,30),tr=(10,10,a.bounds[1][2]))
        try: return t, a.union(b2)
        except Exception: return "box", a
    return "box", box(50,50,50)

def check(case, payload, dt, problems):
    q=payload["quote"]; r=q["requested"]; pl=payload["plan"]; t=pl["times"]
    def bad(msg): problems.append((case, msg))
    for k,v in t.items():
        if isinstance(v,(int,float)) and (not math.isfinite(v) or v<-1e-9): bad(f"time {k}={v}")
    if not (t["per_part_min"]>0 and math.isfinite(t["per_part_min"])): bad(f"per_part_min={t['per_part_min']}")
    if t["per_part_min"]>500000: bad(f"per_part_min absurd {t['per_part_min']}")
    for k in ("unit_price_cny","unit_cost_cny","material_cny","machining_cny","line_total_cny","amortized_one_time_cny"):
        v=r[k]
        if not math.isfinite(v): bad(f"{k} not finite ({v})")
    if not (r["unit_price_cny"]>0): bad(f"unit_price={r['unit_price_cny']}")
    if r["unit_price_cny"] < r["unit_cost_cny"]-1e-6: bad("price<cost")
    if r["machining_cny"]<=0: bad("machining<=0")
    if r["material_cny"]<0: bad("material<0")
    # line_total must equal unit_price*qty (rush applied consistently)
    if abs(r["line_total_cny"] - r["unit_price_cny"]*r["quantity"]) > 0.05: bad("line_total != unit*qty")
    if pl["removed_volume_cm3"]<-1e-6: bad(f"removed<0 {pl['removed_volume_cm3']}")
    if pl["part_volume_cm3"] > pl["stock_volume_cm3"]+1e-3: bad("part>stock")
    if pl["stock_volume_cm3"]<=0: bad("stock<=0")
    # tier monotonic non-increasing unit price + total non-decreasing
    tiers=q["tiers"]; ups=[x["unit_price_cny"] for x in tiers]; tot=[x["line_total_cny"] for x in tiers]
    for i in range(1,len(ups)):
        if ups[i] > ups[i-1]+1e-6: bad(f"tier price rose {ups[i-1]:.2f}->{ups[i]:.2f} @qty{tiers[i]['quantity']}")
        if tot[i] < tot[i-1]-1e-6: bad("tier total decreased")
    if dt>12: bad(f"slow {dt:.1f}s")
    # toolpath ops sane
    if payload["estimator"]["used"]=="toolpath":
        for o in pl.get("operations",[]):
            for k,v in o.items():
                if isinstance(v,float) and not math.isfinite(v): bad(f"op {o.get('op')} {k} not finite")

def run(n=100, seed=12345):
    rng=random.Random(seed)
    problems=[]; ok=0; backends={}
    for i in range(n):
        rng_case=random.Random(seed*1000+i)
        name,mesh=mk_mesh(rng_case)
        mat=rng_case.choice(MATS)
        fin=rng_case.choice(FIN_OK[mat])
        qty=rng_case.choice([1,3,5,10,25,50,100,250])
        holes=[]
        if rng_case.random()<0.4:
            for _ in range(rng_case.randint(1,5)):
                holes.append(Hole(diameter_mm=rng_case.uniform(1,16),depth_mm=rng_case.uniform(2,60),
                                  count=rng_case.randint(1,8),threaded=rng_case.random()<0.5))
        backend=rng_case.choice(["auto","toolpath","analytic"])
        case=dict(i=i,shape=name,mat=mat,fin=fin,qty=qty,backend=backend,nholes=len(holes),
                  tight=rng_case.random()<0.3,five=rng_case.random()<0.2,rush=rng_case.random()<0.2)
        try:
            stl=mesh.export(file_type="stl")
            metrics=metrics_from_stl_bytes(stl)
            req=QuoteRequest(material=mat,quantity=qty,finish=fin,holes=holes,
                             tight_tolerance=case["tight"],requires_5axis=case["five"],rush=case["rush"],
                             part_name=name)
            t0=time.time()
            payload=build_quote(metrics,req,shop,mesh_stl=stl,backend=backend)
            dt=time.time()-t0
            used=payload["estimator"]["used"]; backends[used]=backends.get(used,0)+1
            check(case,payload,dt,problems)
            # determinism: same inputs -> identical price
            p2=build_quote(metrics,req,shop,mesh_stl=stl,backend=backend)
            if abs(p2["quote"]["requested"]["unit_price_cny"]-payload["quote"]["requested"]["unit_price_cny"])>1e-6:
                problems.append((case,"non-deterministic price"))
            # sanity: toolpath vs analytic within 0.1x..10x (else suspicious)
            if used=="toolpath":
                pa=build_quote(metrics,req,shop,mesh_stl=stl,backend="analytic")
                a=pa["plan"]["times"]["per_part_min"]; b=payload["plan"]["times"]["per_part_min"]
                if a>0 and (b/a>12 or b/a<0.08):
                    problems.append((case,f"toolpath/analytic ratio {b/a:.2f} (a={a:.1f} b={b:.1f})"))
            # PDF builds on a sample
            if i%17==0:
                from cnc.quote import build_quote_pdf
                pdf=build_quote_pdf(payload)
                if pdf[:5]!=b"%PDF-": problems.append((case,"bad PDF"))
            ok+=1
        except (QuoteError,) as e:
            problems.append((case,f"QuoteError(unexpected, inputs were valid): {e}"))
        except Exception as e:
            problems.append((case,f"EXCEPTION {e.__class__.__name__}: {e}\n"+traceback.format_exc().splitlines()[-3]))
    print(f"\nran {n} cases · clean {ok} · backends {backends} · problems {len(problems)}")
    for case,msg in problems[:40]:
        print(f"  ✗ #{case['i']:3d} {case['shape']:7s} {case['mat']:11s} q{case['qty']:<3} {case['backend']:8s} holes{case['nholes']} -> {msg}")
    return problems

if __name__=="__main__":
    n=int(sys.argv[1]) if len(sys.argv)>1 else 100
    seed=int(sys.argv[2]) if len(sys.argv)>2 else 12345
    probs=run(n, seed)
    sys.exit(1 if probs else 0)
