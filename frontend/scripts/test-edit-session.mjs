import test from 'node:test'
import assert from 'node:assert/strict'
import { EditSession } from '../src/lib/editSession.js'
import { EDIT_STYLE_DEFAULTS } from '../src/lib/editDocument.js'
const resourceId='00000000-0000-4000-8000-000000000001'
const documentId='00000000-0000-4000-8000-000000000002'
const revisionId='00000000-0000-4000-8000-000000000003'
const draft={cues:[{id:'a',start:0,end:1,text:'שלום Houston'}],style:EDIT_STYLE_DEFAULTS,textLayers:[]}
const store=()=>{const map=new Map();return {getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)}}
function setup(extra={}) {
  const storage=extra.storage||store(), calls=[]
  const request=async(path,opts)=>{calls.push({path,opts});return opts?.body ? {document:opts.body.document||opts.body,revision_id:revisionId,revision_number:1} : {documents:[]}}
  return {storage,calls,session:new EditSession({resourceId,scope:'owner',storage,request,uuid:()=>documentId,...extra})}
}
test('first save roundtrips the exact validated draft with no generation routes',async()=>{
  const {session,calls,storage}=setup(); await session.open(3)
  assert.equal((await session.save(draft)).draft.cues[0].text,draft.cues[0].text)
  assert.equal(storage.getItem(session.key),null)
  assert(calls.every(c=>c.path.startsWith('/edit-documents')))
  assert.equal(calls[1].opts.key,documentId)
})
test('lost response survives reload and retries identical body/key without automatic POST',async()=>{
  const storage=store(), calls=[]
  let lose=true
  const request=async(path,opts)=>{
    calls.push({path,opts})
    if (!opts) return {documents:[]}
    if(lose) throw new Error('network lost')
    return {document:opts.body,revision_id:revisionId,revision_number:1}
  }
  const first=setup({storage,request}).session; await first.open(3)
  await assert.rejects(()=>first.save(draft)); assert(first.pending)
  const resumed=setup({storage,request}).session
  const opened=await resumed.open(3); assert(opened.pending)
  assert.equal(calls.filter(c=>c.opts).length,1)
  lose=false; await resumed.save({...draft,cues:[]})
  assert.deepEqual(calls.filter(c=>c.opts)[0],calls.filter(c=>c.opts)[1])
  assert.equal(storage.getItem(resumed.key),null)
})
test('storage failure stops POST; account scopes do not share recovery data',async()=>{
  const {session,calls}=setup({storage:{getItem:()=>null,setItem:()=>{throw Error('full')}}})
  await session.open(3); await assert.rejects(()=>session.save(draft),/No save was submitted/)
  assert.equal(calls.length,1); assert.equal(session.pending,null)
  assert.notEqual(setup().session.key,setup({scope:'another-owner'}).session.key)
})
test('conflict preserves base and requires explicit latest load; restore saves as new revision',async()=>{
  let number=1
  const doc={schema_version:'video-edit-v1',kind:'video',document_id:documentId,source:{resource_id:resourceId,duration_seconds:3},body:draft}
  const request=async(path,opts)=>{
    if(opts) throw Object.assign(Error('conflict'),{status:409})
    if(path.includes('?resource')) return {documents:[{document_id:documentId}]}
    return {document:doc,revision_id:revisionId,revision_number:number}
  }
  const {session}=setup({request});await session.open(3)
  number=2;await assert.rejects(()=>session.save(draft));assert.equal(session.head.revision_number,1)
  assert.equal(session.pending,null);assert.equal((await session.loadLatest()).revision,2)
  const restored=await session.revision(revisionId);assert.equal(session.head.revision_number,2);assert.deepEqual(restored.cues,draft.cues)
})
test('wrong video and duration cannot be loaded from a server revision',async()=>{
  const {session}=setup();await session.open(3)
  assert.throws(()=>session.draft({schema_version:'video-edit-v1',kind:'video',document_id:documentId,
    source:{resource_id:documentId,duration_seconds:3},body:draft}),/different video/)
  assert.throws(()=>session.draft({schema_version:'video-edit-v1',kind:'video',document_id:documentId,
    source:{resource_id:resourceId,duration_seconds:5},body:draft}),/duration changed/)
})
