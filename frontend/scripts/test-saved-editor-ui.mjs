import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import { EditSession } from '../src/lib/editSession.js'
const dom=new JSDOM('<div id="app"></div>',{url:'https://ben.test'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const {default:React,act}=await import('react'),{createRoot}=await import('react-dom/client')
const output=new URL('../.saved-editor-test.mjs',import.meta.url)
if (!process.env.BEN_EDITOR_PREBUILT) {
  const result=await build({entryPoints:[new URL('../src/components/VideoSubtitleEditor.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'}})
  await writeFile(output,result.outputFiles[0].text)
}
try {
  const {default:Editor}=await import(output.href)
  let revisions=[], pendingResolve, conflict=false, wait=false, lost=false, posts=0
  const resourceId='00000000-0000-4000-8000-000000000001'
  const request=async(path,options)=>{
    if (options?.body) {
      posts++
      if (conflict) throw Object.assign(Error('conflict'),{status:409})
      if (wait) await new Promise(resolve=>{pendingResolve=resolve})
      if (lost) throw Error('lost response')
      const r={document:options.body.document||options.body,revision_id:crypto.randomUUID(),revision_number:revisions.length+1,created_at:'2026-10-04T10:00:00Z'}
      revisions.push(r);return r
    }
    if(path.includes('?resource'))return {documents:revisions.length?[{document_id:revisions[0].document.document_id}]:[]}
    if(path.endsWith('/revisions'))return {revisions:revisions.slice().reverse(),next_before:null}
    if(path.includes('/revisions/'))return revisions.find(r=>path.endsWith(r.revision_id))
    return revisions.at(-1)
  }
  const session=()=>new EditSession({resourceId,scope:'account-a',storage:window.localStorage,request})
  let root,remote
  const render=async()=>{
    root=createRoot(document.getElementById('app'));remote=session()
    await act(async()=>root.render(React.createElement(Editor,{url:'blob:test',open:true,draftKey:'account-a:video',remote,onClose:()=>{}})))
    const v=document.querySelector('video');v.pause=()=>{}
    Object.defineProperties(v,{duration:{value:3},videoWidth:{value:720},videoHeight:{value:1280}})
    await act(async()=>v.dispatchEvent(new dom.window.Event('loadedmetadata')))
  }
  const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent===text)
  const status=()=>document.querySelector('[aria-label="Draft save status"]').textContent
  const importText=async text=>{
    const file=document.querySelector('input[type=file]')
    Object.defineProperty(file,'files',{configurable:true,value:[{size:60,text:async()=>`1\n00:00:00,000 --> 00:00:02,000\n${text}`}]})
    await act(async()=>file.dispatchEvent(new dom.window.Event('change',{bubbles:true})))
  }
  await render();await importText('יוסטון Houston')
  await act(async()=>button('Save').click())
  assert.match(status(),/Saved to BEN · version 1/)
  await act(async()=>root.unmount());await render()
  assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'יוסטון Houston')
  assert.match(status(),/version 1/)
  await importText('Second version');wait=true
  await act(async()=>button('Save').click())
  assert(button('Save').disabled);assert(document.querySelector('.ben-video-editor__body').hasAttribute('inert'))
  await act(async()=>document.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'z',ctrlKey:true,bubbles:true})))
  assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'Second version')
  wait=false;await act(async()=>pendingResolve())
  assert.match(status(),/version 2/)
  await act(async()=>button('Versions').click())
  const older=[...document.querySelectorAll('button')].find(b=>b.textContent.startsWith('Version 1 ·'))
  await act(async()=>older.click());assert.match(status(),/Unsaved/)
  assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'יוסטון Houston')
  await act(async()=>button('Save').click());assert.match(status(),/version 3/)
  assert.equal(revisions.length,3)
  await importText('Keep this draft');conflict=true
  await act(async()=>button('Save').click())
  assert.match(document.querySelector('[role="alert"]').textContent,/newer version/)
  assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'Keep this draft')
  conflict=false;await act(async()=>button('Load latest · keep current changes in Undo').click())
  assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'יוסטון Houston')
  await act(async()=>button('Undo').click())
  assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'Keep this draft')
  lost=true;await act(async()=>button('Save').click())
  assert(button('Retry save'));assert(document.querySelector('.ben-video-editor__body').hasAttribute('inert'))
  const before=posts
  await act(async()=>root.unmount());await render()
  assert.equal(posts,before);assert(button('Retry save'));assert.equal(document.querySelector('.ben-video-editor__caption').textContent,'Keep this draft')
  lost=false;await act(async()=>button('Retry save').click());assert.match(status(),/Saved to BEN/)
  await act(async()=>root.unmount())
  console.log('PASS: actual editor save/reopen, RTL preview, locked in-flight save, immutable restore, conflict recovery with Undo, lost-response reload and explicit retry')
} finally { await unlink(output) }
