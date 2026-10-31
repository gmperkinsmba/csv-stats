if (new URLSearchParams(location.search).has("desktop")) document.body.classList.add("desktop-app");
let token = "";
let state = {overview:{counts:{},imports:[],runs:[],provenance:[]},definitions:[],current:null,allRuns:{}};
const $ = (q, root=document) => root.querySelector(q);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const today = () => new Date().toISOString().slice(0,10);
function notify(message, bad=false){const n=$("#notice");n.textContent=message;n.hidden=false;n.style.borderColor=bad?"#8a454b":"";window.setTimeout(()=>n.hidden=true,6500)}
async function request(path, options={}){
  const headers=new Headers(options.headers||{});
  if(options.method==="POST"){headers.set("X-Local-Action",token);}
  const res=await fetch(path,{...options,headers});
  const data=await res.json().catch(()=>({error:"The server returned an invalid response"}));
  if(!res.ok)throw new Error(data.error||`Request failed (${res.status})`);
  return data;
}
async function refresh(){
  const b=await request("/api/bootstrap");token=b.token;state.overview=b.overview;state.definitions=b.definitions;
  renderOverview();
  if(state.overview.runs.length){await loadRun(state.overview.runs[0].id)}
}
function activeDefinition(){return state.definitions[0]||null}
function renderOverview(){
  const d=activeDefinition(),o=state.overview;
  $("#event-count").textContent=`${o.counts.events||0} events`;
  $("#import-list").innerHTML=o.imports.length?o.imports.map(i=>`<div class="import-item"><span class="file-mark">CSV</span><div><div class="import-name" title="${esc(i.source_name)}">${esc(i.source_name)}</div><div class="import-sub" title="SHA-256 ${esc(i.sha256)}">${new Date(i.imported_at).toLocaleString()} · ${i.duplicates} duplicate · ${i.excluded} excluded · ${esc(i.sha256.slice(0,10))}</div></div><span class="import-count">+${i.accepted}</span></div>`).join(""):`<div class="empty-compact">No files imported yet.</div>`;
  $("#definition-label").textContent=d?`${d.name} · v${d.version}`:"No metric definition";
  $("#version-tag").textContent=d?`v${d.version}`:"v—";
  $("#denominator-rule").textContent=d?d.denominator_event:"—";
  $("#numerator-rule").textContent=d?d.numerator_event:"—";
  $("#window-label").textContent=d?`${d.observation_days} days`:"—";
  $("#definition-versions").innerHTML=state.definitions.length?state.definitions.map(v=>`<button type="button" data-version="${v.version}" aria-current="${d&&v.version===d.version}">v${v.version}</button>`).join(""):"";
  $("#definition-versions").querySelectorAll("button").forEach(btn=>btn.addEventListener("click",()=>showSettings(Number(btn.dataset.version))));
  $("#cohort-count").textContent=o.runs.length?`${o.runs.length} recent saved run${o.runs.length===1?"":"s"}`:"No saved run";
  $("#run-selector").innerHTML=o.runs.length?o.runs.map(r=>`<option value="${r.id}">#${r.id} · v${r.definition_version} · ${esc(r.created_at.slice(0,10))}</option>`).join(""):`<option value="">None</option>`;
  $("#run-selector").value=state.current&&o.runs.some(r=>r.id===state.current.id)?String(state.current.id):(o.runs[0]?String(o.runs[0].id):"");
}
async function loadRun(id){
  let r=state.allRuns[id];if(!r){r=await request("/api/run",{headers:{"X-Run-Id":String(id)}});state.allRuns[id]=r}state.current=r;renderRun(r);
}
function renderRun(r){
  const denom=r.mature;
  $("#rate-value").textContent=r.rate===null?"—":`${(r.rate*100).toFixed(1)}%`;
  $("#rate-state").textContent=r.status==="missing"?"Missing denominator data":`${r.converted} outcomes among ${denom} mature entities`;
  $("#entered-value").textContent=String(r.entered);
  $("#converted-value").textContent=String(r.converted);
  $("#mature-label").textContent=`${r.mature} mature`;
  $("#pending-label").textContent=`Pending ${r.pending}`;
  $("#definition-label").textContent=`${r.definition.name} · v${r.definition.version}`;
  $("#version-tag").textContent=`v${r.definition.version}`;
  $("#denominator-rule").textContent=r.definition.denominator_event;
  $("#numerator-rule").textContent=r.definition.numerator_event;
  $("#window-label").textContent=`${r.definition.observation_days} days`;
  $("#asof-label").textContent=r.as_of;
  $("#cohort-count").textContent=`${r.entered} entities · ${r.mature} mature · ${r.pending} pending · saved run #${r.id}`;
  $("#run-selector").value=String(r.id);
  const rows=r.entities;
  $("#cohort-body").innerHTML=rows.length?rows.map((e,i)=>`<tr style="animation:row-in .25s ${Math.min(i,12)*18}ms both"><td><span class="entity-id">${esc(e.entity_id)}</span></td><td><span class="event-id">${esc(e.entry_event||"—")}</span><br><span>${esc(e.entry_at||"")}</span></td><td><span class="event-id">${esc(e.outcome_event||"—")}</span>${e.outcome_at?`<br><span>${esc(e.outcome_at)}</span>`:""}</td><td><span class="state-label ${e.state}">${esc(e.state)}</span></td></tr>`).join(""):`<tr><td colspan="4" class="empty-cell">No eligible entities in this cohort.</td></tr>`;
}
function openModal(title,kicker,html,after){
  $("#modal-title").textContent=title;$("#modal-kicker").textContent=kicker;$("#modal-content").innerHTML=html;$("#modal-backdrop").hidden=false;
  $("#modal-close").focus();if(after)after();
}
function closeModal(){ $("#modal-backdrop").hidden=true;$("#settings-open").focus() }
function showSettings(version){
  const d=state.definitions.find(x=>x.version===version)||activeDefinition();
  const title=d?"Create a definition version":"Create your first metric";
  openModal(title,"SETTINGS",`<p class="panel-copy">Choose which event enters the denominator, which event counts as an outcome, and how long to observe.</p>
  <form id="definition-form"><div class="form-grid"><div class="field full"><label for="metric-name">Metric name</label><input id="metric-name" name="name" maxlength="100" value="${esc(d?.name||"Application conversion")}" required><span class="helper">This definition becomes a permanent version when saved.</span></div>
  <div class="field"><label for="denominator">Denominator event</label><input id="denominator" name="denominator_event" maxlength="120" value="${esc(d?.denominator_event||"")}" placeholder="visit" required></div>
  <div class="field"><label for="numerator">Outcome event</label><input id="numerator" name="numerator_event" maxlength="120" value="${esc(d?.numerator_event||"")}" placeholder="apply" required></div>
  <div class="field"><label for="window-days">Observation window (days)</label><input id="window-days" name="observation_days" type="number" min="1" max="365" value="${esc(d?.observation_days||14)}" required></div>
  <div class="field"><label for="cohort-start">Cohort start</label><input id="cohort-start" name="cohort_start" type="date" value="${esc(state.current?.cohort_start||new Date(Date.now()-35*86400000).toISOString().slice(0,10))}" required></div>
  <div class="field"><label for="cohort-end">Cohort end</label><input id="cohort-end" name="cohort_end" type="date" value="${esc(state.current?.cohort_end||today())}" required></div>
  <div class="field"><label for="as-of">As-of date</label><input id="as-of" name="as_of" type="date" value="${esc(state.current?.as_of||today())}" required><span class="helper">Pending entities are excluded from the rate denominator.</span></div></div>
  <div class="modal-actions"><button type="button" class="button button-quiet" id="cancel-definition">Cancel</button><button class="button button-primary" type="submit">Save new version</button></div></form>`,()=>{
    $("#cancel-definition").addEventListener("click",closeModal);
    $("#definition-form").addEventListener("submit",async e=>{e.preventDefault();const f=new FormData(e.currentTarget);try{const created=await request("/api/definitions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:f.get("name"),denominator_event:f.get("denominator_event"),numerator_event:f.get("numerator_event"),observation_days:Number(f.get("observation_days"))})});closeModal();await refresh();state.current=null;notify(`Saved metric definition v${created.version}. Run it to save a cohort snapshot.`)}catch(err){notify(err.message,true)}});
  });
}
function showRunSettings(){
  if(!activeDefinition()){showSettings();return}
  const d=activeDefinition();
  openModal("Run a cohort","COHORT DATES",`<p class="panel-copy">Choose the entry period. The as-of date decides which observation windows have finished.</p><form id="run-form"><div class="form-grid">
  <div class="field"><label for="run-start">Cohort start</label><input type="date" id="run-start" value="${esc(state.current?.cohort_start||new Date(Date.now()-35*86400000).toISOString().slice(0,10))}" required></div>
  <div class="field"><label for="run-end">Cohort end</label><input type="date" id="run-end" value="${esc(state.current?.cohort_end||today())}" required></div>
  <div class="field full"><label for="run-asof">As-of date</label><input type="date" id="run-asof" value="${esc(today())}" required><span class="helper">Pending entities are reported separately and excluded from the rate denominator.</span></div></div>
  <div class="modal-actions"><button type="button" class="button button-quiet" id="cancel-run">Cancel</button><button type="submit" class="button button-primary">Calculate cohort</button></div></form>`,()=>{
    $("#cancel-run").addEventListener("click",closeModal);
    $("#run-form").addEventListener("submit",async e=>{e.preventDefault();try{const result=await request("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({metric_key:d.metric_key,version:d.version,cohort_start:$("#run-start").value,cohort_end:$("#run-end").value,as_of:$("#run-asof").value})});state.allRuns[result.id]=result;closeModal();await refresh();await loadRun(result.id);notify(`Saved cohort run #${result.id}. Earlier snapshots were kept.`)}catch(err){notify(err.message,true)}})
  });
}
function showDrill(kind){
  const r=state.current;if(!r){notify("Run a cohort first.",true);return}
  let list=[],title="Contributing entities";
  if(kind==="contributors"||kind==="denominator"||kind==="outcomes"){
    title=kind==="outcomes"?"Contributing outcomes":"Cohort entities";
    list=r.entities.filter(e=>kind==="outcomes"?e.outcome_event:(kind==="contributors"?e.state!=="pending":true)).map(e=>({entity:e.entity_id,event:e.outcome_event||e.entry_event,type:e.outcome_type||e.entry_type,when:e.outcome_at||e.entry_at,state:e.state,reason:e.state==="zero"?"Included in the mature denominator; no outcome event within the window.":"Included in the mature denominator and counted once."}));
  }else if(kind==="exclusions"){
    title="Excluded events";list=r.exclusions.map(e=>({entity:e.entity_id||"—",event:e.event_id||"—",type:e.event_type||"—",when:e.occurred_at||"",reason:e.reason}));
    list.push(...r.entities.filter(e=>e.state==="zero"||e.state==="pending").map(e=>({entity:e.entity_id,event:e.entry_event,type:e.entry_type,when:e.entry_at,reason:e.state==="zero"?"Observed through the full window with no outcome.":"Outcome window has not ended; not scored yet."})));
    list.push(...state.overview.provenance.filter(p=>p.status.startsWith("excluded:")).map(p=>({entity:"Import row",event:`#${p.import_id} · row ${p.row_number}`,type:p.source_name,when:p.imported_at.slice(0,10),reason:p.status})));
  }
  openModal(title,"RUN EVIDENCE",list.length?`<p class="panel-copy">Run #${r.id} · definition v${r.definition.version} · ${list.length} record${list.length===1?"":"s"}</p><div class="drill-list">${list.map(e=>`<div class="drill-row"><span class="entity-id">${esc(e.entity)}</span><span><span class="event-id">${esc(e.event)}</span><br>${esc(e.type||"")}</span><span>${esc(e.when||"")}</span><span class="reason">${esc(e.reason)}${e.state?` <span class="state-label ${esc(e.state)}">${esc(e.state)}</span>`:""}</span></div>`).join("")}</div>`:`<p class="drill-empty">There are no ${kind=== "exclusions"?"excluded":"contributing"} records for this run.</p>`);
}
function showProvenance(){
  const rows=state.overview.provenance;
  openModal("Import row provenance","DATA TRAIL",rows.length?`<p class="panel-copy">Each uploaded row keeps its import ID, row number, source, event key and disposition. Raw fields remain local.</p><div>${rows.map(x=>`<div class="provenance-row"><span>Import #${x.import_id}<br>row ${x.row_number}</span><code>${esc(x.source_key||"—")} / ${esc(x.event_id||"—")}</code><span>${esc(x.status)}<br><span class="helper">${esc(x.source_name)}</span></span></div>`).join("")}</div>`:`<p class="drill-empty">Import a CSV to see row provenance.</p>`);
}
function showMapping(file,text){
  const matrix=parseCsvMatrix(text),headers=(matrix[0]||[]);
  // The file remains in memory only until an explicit import choice.
  const choices=["(skip)",...headers];
  const mapField=(field,label,guess)=>`<div class="field"><label for="map-${field}">${label}</label><select id="map-${field}" data-map="${field}">${choices.map(h=>`<option value="${esc(h)}" ${h===guess?"selected":""}>${esc(h)}</option>`).join("")}</select></div>`;
  if(!headers.length||headers.length>100){notify("CSV needs between 1 and 100 columns.",true);return}
  openModal("Map CSV columns","IMPORT EVENTS",`<p class="panel-copy">Choose which CSV headers carry each event field. The preview keeps IDs as text, including leading zeroes.</p><div class="form-grid">${mapField("event_id","Event ID","event_id")}${mapField("entity_id","Entity ID","entity_id")}${mapField("event_type","Event type","event_type")}${mapField("occurred_at","Occurred at","occurred_at")}${mapField("source","Source (optional)",headers.includes("source")?"source":"(skip)")}</div><div class="table-wrap" style="margin-top:15px;max-height:150px"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${matrix.slice(1,4).map(cells=>`<tr>${headers.map((_,i)=>`<td>${esc(cells[i]||"")}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="helper">Preview shows the first three data rows. Quotes and embedded commas are parsed before import.</p><div class="modal-actions"><button type="button" class="button button-quiet" id="cancel-map">Cancel</button><button type="button" class="button button-primary" id="confirm-map">Import mapped events</button></div>`,()=>{
    $("#cancel-map").addEventListener("click",closeModal);
    $("#confirm-map").addEventListener("click",async()=>{
      const selected=Object.fromEntries(["event_id","entity_id","event_type","occurred_at","source"].map(k=>[k,$(`[data-map="${k}"]`).value]));
      const required=["event_id","entity_id","event_type","occurred_at"];
      if(required.some(k=>!selected[k]||selected[k]==="(skip)")||new Set(required.map(k=>selected[k])).size!==required.length){notify("Map each required field to a different CSV column.",true);return}
      try{
        const mapping=Object.fromEntries(Object.entries(selected).filter(([k,v])=>k!=="source"||v!=="(skip)"));
        const data=await request("/api/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({file_name:file.name,mapping,csv:text})});closeModal();await refresh();notify(`Import #${data.import_id}: ${data.accepted} new events, ${data.duplicates} duplicates, ${data.excluded} excluded.`)
      }catch(err){notify(err.message,true)}
    })
  });
}
function parseCsvMatrix(text){
  const rows=[];let row=[],field="",quoted=false;
  for(let i=0;i<text.length;i++){const c=text[i];if(quoted){if(c==='"'&&text[i+1]==='"'){field+='"';i++}else if(c==='"')quoted=false;else field+=c}else if(c==='"'&&field==="")quoted=true;else if(c===","){row.push(field);field=""}else if(c==="\n"){row.push(field.replace(/\r$/,""));rows.push(row);row=[];field=""}else field+=c}
  if(field.length||row.length){row.push(field.replace(/\r$/,""));rows.push(row)}return rows;
}
function parseCsv(text){
  const rows=parseCsvMatrix(text);if(!rows.length)return[];
  const headers=rows.shift().map(x=>x.trim());return rows.filter(r=>r.some(x=>x.trim())).map(r=>Object.fromEntries(headers.map((h,i)=>[h,r[i]??""])));
}
async function pickFile(file){if(!file)return;if(file.size>1_000_000){notify("CSV must be 1 MB or smaller.",true);return}try{showMapping(file,await file.text())}catch{notify("This CSV could not be read as text.",true)}}
async function runDemo(){try{const d=await request("/api/demo",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});await refresh();await loadRun(d.revised_run_id);notify(`Synthetic demo complete. Reimport added ${d.repeat_import.duplicates} duplicate rows. Run #${d.initial_run_id} remains saved beside revised run #${d.revised_run_id}.`)}catch(e){notify(e.message,true)}}
$("#settings-open").addEventListener("click",()=>showSettings());$("#edit-definition").addEventListener("click",()=>showSettings());$("#run-button").addEventListener("click",showRunSettings);$("#demo-button").addEventListener("click",runDemo);$("#upload-button").addEventListener("click",()=>$("#file-input").click());$("#file-input").addEventListener("change",e=>{const file=e.target.files[0];e.target.value="";pickFile(file)});$("#modal-close").addEventListener("click",closeModal);$("#modal-backdrop").addEventListener("click",e=>{if(e.target===$("#modal-backdrop"))closeModal()});document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("#modal-backdrop").hidden)closeModal()});document.querySelectorAll("[data-detail]").forEach(el=>el.addEventListener("click",()=>showDrill(el.dataset.detail)));$("#provenance-button").addEventListener("click",showProvenance);
$("#run-selector").addEventListener("change",e=>{if(e.target.value)loadRun(Number(e.target.value)).catch(err=>notify(err.message,true))});
refresh().catch(e=>notify(e.message,true));
