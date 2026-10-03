import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { BEN_API_BASE } from '../config.js'
import { outline, problems, roles, saveIntent, clearIntent } from '../lib/productionDraft.js'
import './ProductionPlanner.css'

export default function ProductionPlanner({conversationId,scope,buildHeaders,onClose}) {
  const [brief,setBrief]=useState(''),[plan,setPlan]=useState(null),[saved,setSaved]=useState(null)
  const [selected,setSelected]=useState(0),[expanded,setExpanded]=useState(true)
  const [busy,setBusy]=useState(true),[error,setError]=useState(''),[message,setMessage]=useState('')
  const [dirty,setDirty]=useState(false),[confirmClose,setConfirmClose]=useState(false)
  const [photos,setPhotos]=useState({}),[pending,setPending]=useState(null)
  const [loaded,setLoaded]=useState(false)
  const root=useRef(null),close=useRef(null),picker=useRef(null),urls=useRef([]),lock=useRef(false)
  const abort=useRef(null),closeAction=useRef(null)
  const requestScope=`${scope}:${conversationId}`
  closeAction.current=()=>dirty||pending ? setConfirmClose(true) : onClose()
  async function request(path,options={}) {
    const headers=new Headers(await buildHeaders())
    for(const [k,v] of Object.entries(options.headers||{}))headers.set(k,v)
    const response=await fetch(`${BEN_API_BASE}/api/media${path}`,{...options,headers,signal:abort.current.signal,cache:'no-store'})
    if(!response.ok){const e=new Error(response.status===409?'A newer version or changed source was found. Close and reopen to load the latest plan.':`Request failed (${response.status}). Your original media is unchanged.`);e.status=response.status;throw e}
    return response
  }
  function remember(blob){const url=URL.createObjectURL(blob);urls.current.push(url);return url}
  useEffect(()=>{
    abort.current=new AbortController();const previous=document.activeElement,overflow=document.body.style.overflow
    document.body.style.overflow='hidden';close.current?.focus()
    function keys(e){
      if(e.key==='Escape'){e.preventDefault();closeAction.current()}
      if(e.key==='Tab'){
        const all=[...root.current.querySelectorAll('button:not(:disabled),textarea:not(:disabled),input:not(:disabled),select:not(:disabled)')].filter(el=>!el.hidden)
        const first=all[0],last=all.at(-1)
        if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
      }
    }
    document.addEventListener('keydown',keys)
    async function load(){try{
      const old=sessionStorage.getItem(`ben-plan-pending:${requestScope}`)
      const recovering=old?JSON.parse(old):null
      const body=await (await request(`/production-plans?conversation_id=${conversationId}`)).json()
      if(abort.current.signal.aborted)return
      const record=body.plan
      setLoaded(true)
      const draft=recovering?.body.plan || record?.payload
      setSaved(record);setPending(recovering)
      if(draft){setPlan(draft);setBrief(draft.original_brief);setDirty(!!recovering)}
      if(recovering)setMessage('A previous save is unconfirmed. Retry that exact save before editing.')
      for(const a of draft?.attachments||[]){
        const path=a.source_kind==='chat_photo'?`/production-plans/photos/${a.source_id}?conversation_id=${conversationId}`:`/resources/${a.source_id}/content`
        try{const blob=await(await request(path)).blob();if(!abort.current.signal.aborted)setPhotos(p=>({...p,[a.attachment_id]:remember(blob)}))}catch{if(!abort.current.signal.aborted)setError('One reference image is unavailable. Remove or replace it before saving.')}
      }
    }catch(e){if(!abort.current.signal.aborted)setError(e.message)}finally{if(!abort.current.signal.aborted)setBusy(false)}}
    void load()
    return()=>{abort.current.abort();document.removeEventListener('keydown',keys);document.body.style.overflow=overflow;previous?.focus?.();urls.current.forEach(URL.revokeObjectURL)}
  },[])
  function change(next){setPlan(next);setDirty(true);setMessage('')}
  function editScene(update){change({...plan,scenes:plan.scenes.map((s,i)=>i===selected?{...s,...update}:s)})}
  async function attach(file){
    if(!file||lock.current)return
    if(!/\.(png|jpe?g)$/i.test(file.name)||file.size>20*1024*1024){setError('Choose a JPEG or PNG up to 20 MiB.');return}
    if((plan?.attachments.length||0)>=6){setError('Use up to six reference images in this workspace.');return}
    lock.current=true;setBusy(true);setError('')
    try{
      const checksum=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))).map(n=>n.toString(16).padStart(2,'0')).join('')
      const data=await(await request(`/photo-sources?conversation_id=${conversationId}&idempotency_key=plan-${checksum}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file})).json()
      if(abort.current.signal.aborted)return
      const aid=crypto.randomUUID();const a={attachment_id:aid,source_id:data.file_id,source_kind:'chat_photo',role:'animate_source'}
      const next=plan||outline(brief)
      change({...next,attachments:[...next.attachments,a]});setPhotos(p=>({...p,[aid]:remember(file)}))
    }catch(e){if(!abort.current.signal.aborted)setError(e.message)}finally{lock.current=false;if(!abort.current.signal.aborted)setBusy(false)}
  }
  async function save(){
    if(lock.current)return
    const issue=pending?'':problems(plan)
    if(issue){setError(issue);return}
    lock.current=true;setBusy(true);setError('')
    try{
      const intent=saveIntent(sessionStorage,requestScope,{conversation_id:conversationId,parent_version_id:saved?.id||null,plan})
      setPending(intent)
      const row=await(await request('/production-plans',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':intent.key},body:JSON.stringify(intent.body)})).json()
      if(abort.current.signal.aborted)return
      clearIntent(sessionStorage,requestScope);setPending(null);setSaved(row);setPlan(row.payload);setDirty(false);setMessage(`Version ${row.version} saved. No video generation was started.`)
    }catch(e){if(!abort.current.signal.aborted){
      if([400,401,403,404,409,422].includes(e.status)){clearIntent(sessionStorage,requestScope);setPending(null)}
      setError(`${e.message} ${!e.status||e.status>=500?'Retry the unconfirmed save; no new request will be created.':''}`)
    }}finally{lock.current=false;if(!abort.current.signal.aborted)setBusy(false)}
  }
  const unsupported=plan?.scenes.some(s=>s.duration_ms!==5000||s.transition_to_next.kind!=='cut'||!['generated_scene','animated_attachment'].includes(s.visual.kind)||(s.visual.reference_ids?.length||0)>1)
  let cursor=0;const timing=plan?.scenes.map(s=>{const start=cursor;cursor+=s.duration_ms-(s.transition_to_next.overlap_ms||0);return [start/1000,(start+s.duration_ms)/1000]})
  const scene=plan?.scenes[selected],locked=busy||!!pending||!loaded||unsupported
  return createPortal(<div className="ben-plan-backdrop"><section ref={root} className="ben-plan" role="dialog" aria-modal="true" aria-label="Video planning workspace">
    <header><div><small>BEN / CREATIVE STUDIO</small><h2>Your story, scene by scene</h2></div><button ref={close} onClick={()=>closeAction.current()} aria-label="Close planning workspace">✕</button></header>
    <div className="ben-plan-content">
      <div className="ben-plan-main">
        <div className="ben-plan-meta"><span>Planning only</span><span>3 scenes · 15 seconds · 9:16</span>{saved&&<span>Saved v{saved.version}</span>}</div>
        <label className="ben-plan-brief">What would you like to create?<textarea dir="auto" maxLength={12000} value={brief} disabled={locked} placeholder="Describe your story, or paste your script…" onChange={e=>{setBrief(e.target.value);if(plan)change({...plan,original_brief:e.target.value});else setDirty(true)}}/></label>
        <p className="ben-plan-note">This first workspace creates an editable outline, not an AI-written script. Your full brief is preserved. A 60-second production will need a later profile.</p>
        {unsupported&&<p role="status">This saved plan uses advanced composition or timing. It is read-only in this first editor; its data is preserved.</p>}
        <div className="ben-plan-attachments">
          {plan?.attachments.map((a,i)=><div className="ben-plan-attachment" key={a.attachment_id}>
            {photos[a.attachment_id]?<img src={photos[a.attachment_id]} alt={`Reference ${i+1}`}/>:<span>Reference {i+1}</span>}
            <select aria-label={`Role for reference ${i+1}`} disabled={locked} value={a.role} onChange={e=>{
              const role=e.target.value
              change({...plan,attachments:plan.attachments.map(x=>x===a?{...x,role}:x),scenes:plan.scenes.map(s=>s.visual.kind==='animated_attachment'&&s.visual.attachment_id===a.attachment_id&&role!=='animate_source'?{...s,visual:{kind:'generated_scene',prompt:s.visual.motion_prompt,reference_ids:[a.attachment_id]}}:s)})
            }}>{Object.entries(roles).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select>
            <button disabled={locked} aria-label={`Remove reference ${i+1}`} onClick={()=>change({...plan,attachments:plan.attachments.filter(x=>x!==a),scenes:plan.scenes.map(s=>s.visual.attachment_id===a.attachment_id?{...s,visual:{kind:'generated_scene',prompt:s.visual.motion_prompt||'',reference_ids:[]}}:s.visual.kind==='generated_scene'?{...s,visual:{...s.visual,reference_ids:s.visual.reference_ids.filter(id=>id!==a.attachment_id)}}:s)})}>Remove</button>
          </div>)}
          <button className="ben-plan-add" disabled={locked||!brief.trim()} onClick={()=>picker.current.click()}>+ Add image</button>
          <input ref={picker} type="file" hidden accept="image/png,image/jpeg" onChange={e=>{void attach(e.target.files?.[0]);e.target.value=''}}/>
        </div>
        {!plan&&<button className="ben-plan-primary" disabled={locked||!brief.trim()} onClick={()=>change(outline(brief))}>Create editable outline</button>}
        {plan&&<><div className="ben-plan-section"><h3>Storyboard</h3><button aria-expanded={expanded} onClick={()=>setExpanded(v=>!v)}>{expanded?'Hide scene controls':'Show scene controls'}</button></div>
          <div className="ben-plan-scenes">{plan.scenes.map((s,i)=>{const imageId=s.visual.attachment_id||s.visual.reference_ids?.[0];return <button className={`ben-plan-scene ${selected===i?'is-selected':''}`} key={s.scene_id} aria-pressed={selected===i} onClick={()=>{setSelected(i);setExpanded(true)}}>
            <div className="ben-plan-scene-visual">{photos[imageId]?<img src={photos[imageId]} alt="Scene reference, not a generated frame"/>:<span>{String(i+1).padStart(2,'0')}</span>}<small>{timing[i][0]}–{timing[i][1]}s</small></div>
            <strong>{['Opening','Development','Ending'][i]}</strong><p>{s.visual.prompt||s.visual.motion_prompt||'Describe what happens in this scene'}</p>
          </button>})}</div><p className="ben-plan-note">Images shown are references, not generated frames. Scene timing is fixed to 5 seconds in this editor.</p></>}
      </div>
      {scene&&expanded&&<aside className="ben-plan-panel" aria-label="Scene controls"><div className="ben-plan-section"><h3>Scene {selected+1}</h3><button onClick={()=>setExpanded(false)} aria-label="Hide scene controls">✕</button></div>
        <label>Visual description<textarea dir="auto" maxLength={2000} disabled={locked} value={scene.visual.prompt||scene.visual.motion_prompt||''} onChange={e=>editScene({visual:{...scene.visual,[scene.visual.kind==='animated_attachment'?'motion_prompt':'prompt']:e.target.value}})}/></label>
        <label>Image reference<select disabled={locked} value={scene.visual.attachment_id||scene.visual.reference_ids?.[0]||''} onChange={e=>{const a=plan.attachments.find(x=>x.attachment_id===e.target.value),prompt=scene.visual.prompt||scene.visual.motion_prompt||'';editScene({visual:a?.role==='animate_source'?{kind:'animated_attachment',attachment_id:a.attachment_id,motion_prompt:prompt}:{kind:'generated_scene',prompt,reference_ids:a?[a.attachment_id]:[]}})}}><option value="">No image reference</option>{plan.attachments.map((a,i)=><option key={a.attachment_id} value={a.attachment_id}>Reference {i+1} · {roles[a.role]}</option>)}</select></label>
        <label>Narration script<textarea dir="auto" maxLength={1500} disabled={locked} value={scene.narration_text} onChange={e=>editScene({narration_text:e.target.value})}/></label>
        <label>Language<select disabled={locked} value={plan.language} onChange={e=>change({...plan,language:e.target.value})}><option value="en-US">English</option><option value="he-IL">Hebrew</option></select></label>
        <p className="ben-plan-note">Narration audio has not been generated or timed. No rendering or paid generation is available in this workspace yet.</p>
      </aside>}
    </div>
    <footer><div role="status">{busy?'Working…':message|| (dirty?'Unsaved changes':'Your originals stay unchanged.')}</div><button className="ben-plan-primary" disabled={busy||!loaded||unsupported||(!pending&&(!plan||!dirty))} onClick={()=>void save()}>{pending?'Retry unconfirmed save':'Save plan'}</button></footer>
    {error&&<p className="ben-plan-error" role="alert">{error}</p>}
    {confirmClose&&<div className="ben-plan-confirm" role="alert"><p>{pending?'An unconfirmed save will be recovered when you reopen.':'Discard unsaved local changes?'}</p><button onClick={()=>setConfirmClose(false)}>Keep editing</button><button onClick={onClose}>Close workspace</button></div>}
  </section></div>,document.body)
}

