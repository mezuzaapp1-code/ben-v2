import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import { outline, problems, saveIntent } from '../src/lib/productionDraft.js'

const dom=new JSDOM('<button id="trigger">Plan</button><div id="app"></div>',{url:'https://ben.test'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const {default:React,act}=await import('react'),{createRoot}=await import('react-dom/client')
const draft=outline('Keep my full 60-second script; this is just a 15-second planning trial.')
assert(problems(draft).includes('scene 1'))
draft.scenes.forEach((s,i)=>s.visual.prompt=`Scene ${i+1} in a blue world`)
assert.equal(problems(draft),'')
assert.deepEqual(saveIntent(sessionStorage,'test',{v:1}),saveIntent(sessionStorage,'test',{v:2}))
sessionStorage.clear()
const out=new URL('../.planner-test.mjs',import.meta.url)
const built=process.env.BEN_PLANNER_PREBUILT ? null : await build({entryPoints:[new URL('../src/components/ProductionPlanner.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],loader:{'.css':'empty'},define:{'import.meta.env':'{}'}})
if(built) await writeFile(out,built.outputFiles[0].text)
let record={id:crypto.randomUUID(),plan_id:crypto.randomUUID(),version:1,payload:draft},fail=true,calls=[]
globalThis.fetch=async(url,options)=>{
 calls.push({url,options})
 assert(!url.includes('/executions'),'planning must never submit a media generation')
 if(options.method==='POST'){
  const body=JSON.parse(options.body)
  record={...record,id:crypto.randomUUID(),version:2,payload:body.plan}
  if(fail){fail=false;throw new TypeError('connection lost')}
  return {ok:true,json:async()=>record}
 }
 return {ok:true,json:async()=>({plan:record})}
}
try{
 const {default:Planner}=await import(out.href)
 const root=createRoot(document.getElementById('app'));let closes=0
 document.getElementById('trigger').focus()
 await act(async()=>root.render(React.createElement(Planner,{conversationId:'conversation',scope:'owner',buildHeaders:async()=>({Authorization:'Bearer test'}),onClose:()=>closes++})))
 const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent===text)
 assert.equal(document.activeElement.getAttribute('aria-label'),'Close planning workspace')
 assert.equal(document.querySelectorAll('.ben-plan-scene').length,3)
 assert(document.querySelector('textarea').value.includes('60-second'))
 await act(async()=>button('Hide scene controls').click());assert.equal(document.querySelector('aside'),null)
 await act(async()=>button('Show scene controls').click());assert(document.querySelector('aside'))
 const input=document.querySelector('aside textarea')
 await act(async()=>{
  Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'An edited opening')
  input.dispatchEvent(new dom.window.Event('input',{bubbles:true}))
 })
 assert.equal(button('Save plan').disabled,false)
 await act(async()=>button('Save plan').click())
 assert(button('Retry unconfirmed save'))
 assert.equal(document.querySelector('aside textarea').disabled,true)
 await act(async()=>button('Retry unconfirmed save').click())
 const posts=calls.filter(c=>c.options.method==='POST')
 assert.equal(posts.length,2)
 assert.equal(posts[0].options.headers.get('Idempotency-Key'),posts[1].options.headers.get('Idempotency-Key'))
 assert.equal(posts[0].options.body,posts[1].options.body)
 assert.equal(JSON.parse(posts[0].options.body).plan.scenes[0].visual.prompt,'An edited opening')
 assert.equal(button('Save plan').disabled,true)
 assert(document.body.textContent.includes('Version 2 saved'))
 await act(async()=>document.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})))
 assert.equal(closes,1)
 await act(async()=>root.unmount())
 assert.equal(document.activeElement.id,'trigger')
 assert.equal(document.body.style.overflow,'')
 console.log('PASS: original script retained; editable storyboard; panel close/reopen; exact uncertain-save replay; no paid endpoint; keyboard focus restoration')
}finally{await unlink(out)}

