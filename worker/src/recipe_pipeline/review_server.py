"""Local review UI: work through the `needs_review` queue with the scan next to the recipe.

`recipe-pipeline review` starts a tiny stdlib HTTP server (no extra deps) on localhost and opens
a browser. For each flagged recipe you see the scanned page(s) beside the extracted fields, edit
if needed, then Approve (queued for upload) or Skip (excluded). Approve/Skip write per-recipe
markers (`.reviewed` / `.rejected`); `upload` then pushes ok recipes + approved needs_review ones.
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings

_OUTBOX: Path = Path("output")  # set by serve()


def _status(d: Path) -> str:
    rev = d / "review.json"
    return json.loads(rev.read_text()).get("status", "?") if rev.exists() else "?"


def _queue() -> list[dict]:
    """All needs_review recipes + their review state, easiest-first (highest confidence)."""
    rows = []
    for d in sorted(_OUTBOX.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        rev = d / "review.json"
        if not (d / "recipe.json").exists() or not rev.exists():
            continue
        v = json.loads(rev.read_text())
        if v.get("status") != "needs_review":
            continue
        r = json.loads((d / "recipe.json").read_text())
        rows.append(
            {
                "slug": d.name,
                "name": r.get("name") or "(no name)",
                "confidence": v.get("confidence") or 0,
                "flags": sorted({f["code"] for f in v.get("flags", []) if isinstance(f, dict)}),
                "reviewed": (d / ".reviewed").exists(),
                "rejected": (d / ".rejected").exists(),
                "uploaded": (d / ".uploaded").exists(),
            }
        )
    rows.sort(key=lambda x: -x["confidence"])
    return rows


def _recipe(slug: str) -> dict:
    d = _OUTBOX / slug
    r = json.loads((d / "recipe.json").read_text())
    v = json.loads((d / "review.json").read_text())
    pages = sorted(p.name for p in (d / "pages").glob("page-*.jpg")) if (d / "pages").exists() else []
    extras_src = r.get("original_transcription") or ""
    return {
        "slug": slug,
        "name": r.get("name") or "",
        "description": r.get("description") or "",
        "ingredients": [i["text"] for i in r.get("ingredients", [])],
        "instructions": [s["text"] for s in r.get("instructions", [])],
        "pages": pages,
        "confidence": v.get("confidence"),
        "flags": [f["code"] for f in v.get("flags", []) if isinstance(f, dict)],
        "verbatim": extras_src,
        "reviewed": (d / ".reviewed").exists(),
        "rejected": (d / ".rejected").exists(),
    }


def _save(slug: str, body: dict) -> None:
    """Apply edits to recipe.json, preserving everything we don't expose for editing."""
    from recipe_pipeline_shared.schema import Ingredient, Recipe, Step

    d = _OUTBOX / slug
    recipe = Recipe.model_validate_json((d / "recipe.json").read_text())
    recipe.name = body.get("name", recipe.name).strip() or recipe.name
    recipe.description = body.get("description", recipe.description) or None
    if "ingredients" in body:
        recipe.ingredients = [Ingredient(text=t) for t in body["ingredients"] if t.strip()]
    if "instructions" in body:
        recipe.instructions = [Step(text=t) for t in body["instructions"] if t.strip()]
    (d / "recipe.json").write_text(recipe.model_dump_json(indent=2))


def _decide(slug: str, decision: str) -> None:
    d = _OUTBOX / slug
    reviewed, rejected = d / ".reviewed", d / ".rejected"
    reviewed.unlink(missing_ok=True)
    rejected.unlink(missing_ok=True)
    if decision == "approve":
        reviewed.write_text("")
    elif decision == "reject":
        rejected.write_text("")
    # "reset" leaves both removed


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/":
            data = _INDEX_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/queue":
            self._json(_queue())
        elif path.startswith("/api/recipe/"):
            self._json(_recipe(path[len("/api/recipe/") :]))
        elif path.startswith("/img/"):
            slug, _, fname = path[len("/img/") :].partition("/")
            img = _OUTBOX / slug / "pages" / fname
            if img.exists():
                blob = img.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or "{}")
        if path.startswith("/api/recipe/") and path.endswith("/decision"):
            slug = path[len("/api/recipe/") : -len("/decision")]
            _decide(slug, body.get("decision", "reset"))
            self._json({"ok": True})
        elif path.startswith("/api/recipe/"):
            slug = path[len("/api/recipe/") :]
            _save(slug, body)
            self._json({"ok": True})
        else:
            self.send_error(404)


def serve(settings: Settings, *, port: int = 8765) -> None:
    global _OUTBOX
    _OUTBOX = settings.outbox
    n = len(_queue())
    url = f"http://127.0.0.1:{port}/"
    print(f"Review UI for {n} recipe(s) -> {url}  (Ctrl-C to stop)")
    webbrowser.open(url)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nReview server stopped.")


_INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Recipe review</title>
<style>
  :root{--bg:#15171c;--panel:#1d2027;--line:#2c313c;--fg:#e6e8ec;--mut:#8b93a3;--acc:#5b9cff;
        --ok:#46b35e;--warn:#d9a13a;--rej:#c2554f;}
  *{box-sizing:border-box} html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;display:flex;height:100vh}
  /* sidebar */
  #side{width:280px;flex:0 0 280px;border-right:1px solid var(--line);overflow:auto;background:var(--panel)}
  #side h1{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin:0;padding:14px 14px 8px}
  #prog{padding:0 14px 10px;color:var(--mut);font-size:12px}
  .item{padding:8px 14px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;gap:8px;align-items:baseline}
  .item:hover{background:#232732} .item.cur{background:#27314a}
  .item .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .dot{width:8px;height:8px;border-radius:50%;flex:0 0 8px;background:var(--mut)}
  .dot.reviewed{background:var(--ok)} .dot.rejected{background:var(--rej)} .dot.uploaded{background:var(--acc)}
  .conf{color:var(--mut);font-size:11px;flex:0 0 auto}
  /* main */
  #main{flex:1;display:flex;min-width:0}
  #scan{flex:1;overflow:auto;background:#0e0f12;padding:16px;text-align:center}
  #scan img{max-width:100%;margin-bottom:12px;border-radius:6px;box-shadow:0 2px 12px #0008}
  #edit{width:46%;flex:0 0 46%;border-left:1px solid var(--line);overflow:auto;padding:18px 20px}
  .row{margin-bottom:14px} label{display:block;color:var(--mut);font-size:12px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.03em}
  input,textarea{width:100%;background:#12141a;border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:8px 10px;font:inherit;resize:vertical}
  textarea{min-height:120px;font-family:ui-monospace,Menlo,monospace;font-size:13px}
  #name{font-size:17px;font-weight:600}
  .badges{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
  .b{font-size:11px;padding:2px 8px;border-radius:999px;background:#2a2f3a;color:var(--mut)}
  .b.conf{background:#1f3a26;color:#9be0ab} .b.lowconf{background:#3a2520;color:#e6a79e}
  .bar{position:sticky;bottom:0;background:var(--panel);border-top:1px solid var(--line);padding:12px;margin:18px -20px -18px;display:flex;gap:8px}
  button{font:inherit;border:0;border-radius:6px;padding:9px 14px;cursor:pointer;font-weight:600}
  .save{background:#2c313c;color:var(--fg)} .approve{background:var(--ok);color:#06210f}
  .reject{background:var(--rej);color:#2a0c0a} .nav{background:#2c313c;color:var(--mut)}
  details{margin-top:6px} summary{cursor:pointer;color:var(--mut);font-size:12px}
  pre{white-space:pre-wrap;background:#12141a;border:1px solid var(--line);border-radius:6px;padding:10px;font-size:12px;color:var(--mut)}
  .hint{color:var(--mut);font-size:11px;margin-top:4px}
  #empty{margin:auto;color:var(--mut);text-align:center}
</style></head>
<body>
  <div id="side"><h1>Needs review</h1><div id="prog"></div><div id="list"></div></div>
  <div id="main">
    <div id="scan"></div>
    <div id="edit"></div>
  </div>
<script>
let Q=[], cur=-1;
const $=s=>document.querySelector(s);
async function load(){ Q=await (await fetch('/api/queue')).json(); renderList();
  const pend=Q.findIndex(r=>!r.reviewed&&!r.rejected); open(pend<0?0:pend); }
function renderList(){
  const rev=Q.filter(r=>r.reviewed).length, rej=Q.filter(r=>r.rejected).length;
  $('#prog').textContent=`${rev} approved · ${rej} skipped · ${Q.length-rev-rej} left of ${Q.length}`;
  $('#list').innerHTML=Q.map((r,i)=>{
    const st=r.uploaded?'uploaded':r.reviewed?'reviewed':r.rejected?'rejected':'';
    return `<div class="item ${i===cur?'cur':''}" onclick="open(${i})">
      <span class="dot ${st}"></span><span class="nm">${esc(r.name)}</span>
      <span class="conf">${(r.confidence*100|0)}%</span></div>`;}).join('');
}
async function open(i){ if(i<0||i>=Q.length)return; cur=i; renderList();
  document.querySelectorAll('.item')[i]?.scrollIntoView({block:'nearest'});
  const r=await (await fetch('/api/recipe/'+encodeURIComponent(Q[i].slug))).json();
  $('#scan').innerHTML=r.pages.map(p=>`<img src="/img/${encodeURIComponent(r.slug)}/${p}">`).join('')||'<div id=empty>no scan image</div>';
  $('#scan').scrollTop=0;
  const lc=r.confidence!=null&&r.confidence<0.7;
  $('#edit').innerHTML=`
    <div class="badges">
      <span class="b ${lc?'lowconf':'conf'}">confidence ${r.confidence!=null?(r.confidence*100|0)+'%':'?'}</span>
      ${r.flags.map(f=>`<span class="b">${f}</span>`).join('')}</div>
    <div class="row"><label>Title</label><input id="name" value="${esc(r.name)}"></div>
    <div class="row"><label>Ingredients (one per line)</label><textarea id="ings">${esc(r.ingredients.join('\n'))}</textarea></div>
    <div class="row"><label>Instructions (one per line / step)</label><textarea id="insts">${esc(r.instructions.join('\n'))}</textarea></div>
    <details><summary>verbatim OCR (what was actually scanned)</summary><pre>${esc(r.verbatim||'(none)')}</pre></details>
    <div class="hint">Shortcuts: ← / → navigate · S save · A approve+next · X skip+next</div>
    <div class="bar">
      <button class="nav" onclick="open(cur-1)">←</button>
      <button class="save" onclick="save()">Save</button>
      <button class="approve" onclick="decide('approve')">Approve ✓</button>
      <button class="reject" onclick="decide('reject')">Skip</button>
      <button class="nav" onclick="open(cur+1)">→</button>
    </div>`;
}
function payload(){return{name:$('#name').value,
  ingredients:$('#ings').value.split('\n').map(s=>s.trim()).filter(Boolean),
  instructions:$('#insts').value.split('\n').map(s=>s.trim()).filter(Boolean)};}
async function save(){ await fetch('/api/recipe/'+encodeURIComponent(Q[cur].slug),
  {method:'POST',body:JSON.stringify(payload())}); flash('saved'); }
async function decide(d){ if(d==='approve') await save();
  await fetch('/api/recipe/'+encodeURIComponent(Q[cur].slug)+'/decision',
    {method:'POST',body:JSON.stringify({decision:d})});
  Q[cur].reviewed=d==='approve'; Q[cur].rejected=d==='reject'; renderList();
  const nx=Q.findIndex((r,i)=>i>cur&&!r.reviewed&&!r.rejected); open(nx<0?cur+1:nx); }
function flash(t){const b=document.querySelector('.save');const o=b.textContent;b.textContent=t;setTimeout(()=>b.textContent=o,800);}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
document.addEventListener('keydown',e=>{ if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT'){
    if((e.metaKey||e.ctrlKey)&&e.key==='s'){e.preventDefault();save();} return; }
  if(e.key==='ArrowLeft')open(cur-1); else if(e.key==='ArrowRight')open(cur+1);
  else if(e.key==='s')save(); else if(e.key==='a')decide('approve'); else if(e.key==='x')decide('reject'); });
load();
</script></body></html>
"""
