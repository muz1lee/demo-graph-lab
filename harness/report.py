"""report:单文件 report.html——关键帧+提取结果+校验+金标标注(导出 JSON)。"""

from __future__ import annotations

import json

from . import util

_TPL = """<!doctype html><html><head><meta charset="utf-8"><title>{{ task }} · harness report</title>
<style>
body{font-family:-apple-system,'PingFang SC',sans-serif;margin:20px;max-width:1200px}
h2{border-bottom:2px solid #333;padding-bottom:4px}
.stage{border:1px solid #ccc;border-radius:8px;padding:12px;margin:16px 0}
.kf{display:flex;gap:6px;flex-wrap:wrap}.kf figure{margin:0;text-align:center}
.kf img{height:120px;border-radius:4px}.kf figcaption{font-size:11px;color:#666}
table{border-collapse:collapse;width:100%;margin:8px 0}
td,th{border:1px solid #ddd;padding:4px 8px;font-size:13px;text-align:left}
.viol{background:#ffe8e8;padding:8px;border-radius:6px}
.ok{background:#e8f5e9;padding:8px;border-radius:6px}
.meta{color:#555;font-size:13px} select,input[type=text]{font-size:12px}
button{margin:12px 0;padding:8px 16px;font-size:14px;cursor:pointer}
</style></head><body>
<h1>{{ task }} — demo 理解报告</h1>
<p class="meta">instruction: {{ instruction }} | model: {{ model }} | k={{ k }} |
frames: {{ n_frames }} | 总成本: ${{ cost }} | 生成时间: {{ ts }}</p>
{% if validation.passed %}<div class="ok">✅ 校验通过({{ validation.items_checked }} 项)</div>
{% else %}<div class="viol">❌ 校验违例 {{ validation.violations|length }} 条:
<ul>{% for v in validation.violations %}<li>{{ v }}</li>{% endfor %}</ul></div>{% endif %}
{% if validation.warnings %}<div style="background:#fff3e0;padding:8px;border-radius:6px">
⚠️ 告警 {{ validation.warnings|length }} 条(时序错位/装配缺口,标注时重点看):
<ul>{% for w in validation.warnings %}<li>{{ w }}</li>{% endfor %}</ul></div>{% endif %}

{% for st in stages %}
<div class="stage"><h2>Stage {{ st.index }}: {{ st.name }}</h2>
<p class="meta">{{ st.label }} | {{ st.start_sec }}–{{ st.end_sec }}s |
parsed {{ st.k_valid }}/{{ k }}{% if st.parse_fail %} (parse_fail={{ st.parse_fail }}){% endif %}</p>
<div class="kf">{% for f in st.frames %}<figure>
<img src="data:image/jpeg;base64,{{ f.b64 }}"><figcaption>f{{ f.frame_idx }} · {{ f.t_sec }}s</figcaption>
</figure>{% endfor %}</div>
<h3>约束</h3><table><tr><th>name</th><th>args</th><th>votes</th><th>conf</th>
<th>evidence</th><th>金标判定</th><th>备注</th></tr>
{% for c in st.constraints %}<tr data-stage="{{ st.index }}" data-field="constraints"
 data-key='{{ c.name }}|{{ c.args_json }}'>
<td><b>{{ c.name }}</b></td><td><code>{{ c.args_json }}</code></td>
<td>{{ c.votes }}</td><td>{{ c.confidence }}</td><td>{{ c.evidence_frames }}</td>
<td><select class="verdict"><option value="">--</option><option>correct</option>
<option>incidental</option><option>wrong</option><option>unsure</option></select></td>
<td><input type="text" class="note" size="18"></td></tr>{% endfor %}</table>
<h3>验收条件</h3><table><tr><th>name</th><th>args</th><th>votes</th><th>conf</th>
<th>金标判定</th><th>备注</th></tr>
{% for c in st.acceptance %}<tr data-stage="{{ st.index }}" data-field="acceptance"
 data-key='{{ c.name }}|{{ c.args_json }}'>
<td><b>{{ c.name }}</b></td><td><code>{{ c.args_json }}</code></td>
<td>{{ c.votes }}</td><td>{{ c.confidence }}</td>
<td><select class="verdict"><option value="">--</option><option>correct</option>
<option>incidental</option><option>wrong</option><option>unsure</option></select></td>
<td><input type="text" class="note" size="18"></td></tr>{% endfor %}</table>
<h3>Typed holes</h3><table><tr><th>name</th><th>type</th><th>solver_hint</th><th>votes</th></tr>
{% for h in st.holes %}<tr><td>{{ h.name }}</td><td>{{ h.type }}</td>
<td>{{ h.solver_hint }}</td><td>{{ h.votes }}</td></tr>{% endfor %}</table>
<h3>漏提的约束(人工补录)</h3>
<div id="missing-{{ st.index }}"></div>
<button onclick="addMissing({{ st.index }})">+ 补一条</button>
</div>
{% endfor %}
<button style="background:#2e7d32;color:#fff" onclick="exportGold()">导出金标 JSON</button>
<script>
function addMissing(si){
  const d=document.getElementById('missing-'+si);
  d.insertAdjacentHTML('beforeend',
   `<div class="missrow" data-stage="${si}">name:<input type="text" class="mname" size="16">
    args:<input type="text" class="margs" size="30" placeholder='{"axis":"tube.long_axis"}'>
    <input type="text" class="mnote" size="14" placeholder="备注"></div>`);
}
function exportGold(){
  const gold={task:"{{ task }}",annotated_at:new Date().toISOString(),stages:{}};
  document.querySelectorAll('tr[data-key]').forEach(tr=>{
    const s=tr.dataset.stage,f=tr.dataset.field;
    const v=tr.querySelector('.verdict').value; if(!v)return;
    gold.stages[s]=gold.stages[s]||{constraints:[],acceptance:[],missing:[]};
    gold.stages[s][f].push({key:tr.dataset.key,verdict:v,
      note:tr.querySelector('.note').value});
  });
  document.querySelectorAll('.missrow').forEach(r=>{
    const s=r.dataset.stage;
    gold.stages[s]=gold.stages[s]||{constraints:[],acceptance:[],missing:[]};
    if(r.querySelector('.mname').value)
      gold.stages[s].missing.push({name:r.querySelector('.mname').value,
        args:r.querySelector('.margs').value,note:r.querySelector('.mnote').value});
  });
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(gold,null,2)],{type:'application/json'}));
  a.download="{{ task }}_gold.json";a.click();
}
</script></body></html>"""


def run(task: str) -> None:
    import time

    from jinja2 import Template  # lazy

    run_dir = util.latest_run_dir(task)
    graph = util.read_json(run_dir / "graph.json")
    validation = util.read_json(run_dir / "validation.json")
    keyframes = util.read_json(run_dir / "keyframes.json")
    total_cost = 0.0
    ledger = run_dir / "cost.jsonl"
    if ledger.exists():
        total_cost = sum(json.loads(l).get("cost", 0) or 0
                         for l in ledger.read_text().splitlines())
    stages = []
    for st in graph["stages"]:
        frames = [dict(f, b64=util.b64_jpeg(run_dir / f["file"]))
                  for f in keyframes.get(str(st["index"]), [])]
        for field in ("constraints", "acceptance"):
            for c in st.get(field, []):
                c["args_json"] = json.dumps(c.get("args", {}), ensure_ascii=False)
        stages.append(dict(st, frames=frames))
    html = Template(_TPL).render(
        task=graph["task"], instruction=graph["instruction"],
        model=graph.get("model") or "default", k=graph["k"],
        n_frames=sum(len(s["frames"]) for s in stages),
        cost=f"{total_cost:.3f}", ts=time.strftime("%Y-%m-%d %H:%M"),
        stages=stages, validation=validation)
    out = run_dir / "report.html"
    out.write_text(html)
    print(f"[report] {out} ({out.stat().st_size // 1024} KB)")
