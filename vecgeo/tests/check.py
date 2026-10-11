#!/usr/bin/env python3
"""Dev oracle: runs answer.py on each case and compares with expected.json. Exit 0 only if every field matches."""
import json, subprocess, sys, os
D=os.path.dirname(os.path.abspath(__file__))+"/.."; exp=json.load(open(D+"/tests/expected.json")); bad=0
for c in exp["cases"]:
    cmd=[sys.executable,D+"/answer.py",D+"/"+c.get("pdf","drv8311_p15_p34.pdf"),str(c["page"]),"--bbox"]+[str(v) for v in c["bbox"]]
    r=subprocess.run(cmd,capture_output=True,text=True)
    try: got=json.loads(r.stdout)
    except Exception: print("[%s] answer.py did not print JSON:\n%s"%(c["name"],r.stderr[-500:])); bad+=1; continue
    def ev(e): return None if not isinstance(e,dict) else (e.get("lane"),e.get("kind"),e.get("level_pct") if "level_pct" in (e or {}) else None)
    for p,w in c.get("params",{}).items():
        g=got.get("params",{}).get(p) or {k2:v2 for k2,v2 in got.get("params",{}).items() if k2.lower()==p.lower()}.get(p.lower()) or next((v2 for k2,v2 in got.get("params",{}).items() if k2.lower()==p.lower()),None)
        for end in ("start","end"):
            wl=(w[end]["lane"],w[end]["kind"],w[end].get("level_pct"))
            gl=None if not g else (g[end].get("lane"),g[end].get("kind"),g[end].get("level_pct") if w[end].get("level_pct") is not None else None)
            ok= gl==wl; bad+= (not ok)
            print("[%s] %-9s %-5s want=%s got=%s %s"%(c["name"],p,end,wl,gl,"OK" if ok else "FAIL"))
    for k in ("sampling_edge",):
        if k in c:
            g=got.get(k) or {}; ok=(g.get("lane"),g.get("kind"))==(c[k]["lane"],c[k]["kind"]); bad+=(not ok)
            print("[%s] %s want=%s got=%s %s"%(c["name"],k,c[k],g,"OK" if ok else "FAIL"))
    if "first_mover_lane" in c:
        ok=got.get("first_mover_lane")==c["first_mover_lane"]; bad+=(not ok)
        print("[%s] first_mover_lane want=%s got=%s %s"%(c["name"],c["first_mover_lane"],got.get("first_mover_lane"),"OK" if ok else "FAIL"))
print("RESULT:", "ALL PASS" if not bad else "%d FIELD(S) FAILING"%bad); sys.exit(1 if bad else 0)
