import { useEffect, useRef, useState } from 'react'
import { BEN_API_BASE } from '../config.js'
import { pendingMedia, clearPendingMedia } from '../api/media.js'

export default function PhotoSourceUpload({ workspaceId, scope, buildHeaders, ensureConversation, onReady, disabled, onChooseProject, onCreateProject, initialFile }) {
  const [file,setFile]=useState(null), [preview,setPreview]=useState(''), [busy,setBusy]=useState(false), [message,setMessage]=useState('')
  useEffect(()=>{
    if (!initialFile) return
    if (!/\.(jpe?g|png)$/i.test(initialFile.name) || initialFile.size > 20*1024*1024) {
      setFile(null);setMessage('Choose a JPEG or PNG up to 20 MiB.');return
    }
    setFile(initialFile);setMessage('Your photo is selected. Choose a project if needed, then use this photo.')
  },[initialFile])
  const lock=useRef(false), abort=useRef(null)
  const key=`photo:${scope}:${workspaceId}`
  useEffect(()=>{abort.current=new AbortController();return()=>abort.current.abort()},[key])
  useEffect(()=>{if(!file){setPreview('');return}const url=URL.createObjectURL(file);setPreview(url);return()=>URL.revokeObjectURL(url)},[file])
  async function upload(){
    if(!file || !workspaceId || lock.current || disabled)return
    lock.current=true;setBusy(true);setMessage('Uploading photo…');const signal=abort.current.signal
    try{
      const checksum=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))].map(v=>v.toString(16).padStart(2,'0')).join('')
      if(signal.aborted)return
      const saved=pendingMedia(sessionStorage,key,{conversation_id:await ensureConversation(),workspace_id:workspaceId,checksum})
      if(saved.checksum!==checksum){setMessage('Retry the previous photo to confirm its upload first.');return}
      const query=new URLSearchParams({conversation_id:saved.conversation_id,workspace_id:saved.workspace_id,idempotency_key:saved.idempotency_key})
      const headers=new Headers(await buildHeaders());headers.set('Content-Type','application/octet-stream')
      const response=await fetch(`${BEN_API_BASE}/api/media/photo-sources?${query}`,{method:'POST',headers,body:file,signal})
      if(signal.aborted)return
      if(!response.ok){
        if([400,401,403,404,409,413,422].includes(response.status)){clearPendingMedia(sessionStorage,key);setMessage('Photo rejected. Use a JPEG or PNG up to 20 MiB / 20 megapixels and check project access.');return}
        throw new Error('unconfirmed')
      }
      const source=await response.json();clearPendingMedia(sessionStorage,key)
      if(signal.aborted)return
      onReady(source);setMessage('Photo ready. Describe the motion below, then choose Generate video. Your original is preserved.')
    }catch{if(!signal.aborted)setMessage('Upload not confirmed. Retry the same photo safely.')}
    finally{lock.current=false;if(!signal.aborted)setBusy(false)}
  }
  return <section aria-label="Animate my photo">
    <h3>Animate my photo</h3><p>JPEG or PNG · up to 20 MiB and 20 megapixels. Uploading does not start paid generation.</p>
    {!workspaceId && <div><p>Choose where to keep your original photo.</p><button type="button" onClick={onChooseProject}>Choose project</button><button type="button" onClick={onCreateProject}>New project</button></div>}
    <label>Your photo<input type="file" accept="image/jpeg,image/png,.jpg,.jpeg,.png" disabled={disabled||busy||!workspaceId} onChange={e=>{
      const next=e.target.files?.[0];onReady(null);setFile(null);setMessage('')
      if(next && (!/\.(jpe?g|png)$/i.test(next.name)||next.size>20*1024*1024)){setMessage('Choose a JPEG or PNG up to 20 MiB.');return}setFile(next||null)
    }}/></label>
    {preview && <img src={preview} alt="Your selected photo" style={{display:'block',maxWidth:'100%',maxHeight:220,objectFit:'contain',borderRadius:12,marginBlock:12}}/>}
    <button type="button" disabled={!file||!workspaceId||disabled||busy} onClick={()=>void upload()}>{busy?'Uploading…':'Use this photo'}</button>
    {message && <p role="status">{message}</p>}
  </section>
}
