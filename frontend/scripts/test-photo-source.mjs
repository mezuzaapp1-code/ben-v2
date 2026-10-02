import assert from 'node:assert/strict'
import {build} from 'esbuild'
import {writeFile,unlink} from 'node:fs/promises'
import {JSDOM} from 'jsdom'
const dom=new JSDOM('<div id="app"></div>',{url:'https://ben.test'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
URL.createObjectURL=()=> 'blob:preview';URL.revokeObjectURL=()=>{}
const {default:React,act}=await import('react');const {createRoot}=await import('react-dom/client')
const out=new URL('../.photo-test.mjs',import.meta.url)
const built=await build({entryPoints:[new URL('../src/components/PhotoSourceUpload.jsx',import.meta.url).pathname],bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime']})
await writeFile(out,built.outputFiles[0].text)
try{
 const {default:Upload}=await import(out.href);const root=createRoot(document.getElementById('app'));let ready=null,calls=[]
 globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({file_id:'owned-photo',workspace_id:'project',width:320,height:180})}}
 await act(async()=>root.render(React.createElement(Upload,{workspaceId:'project',scope:'test',buildHeaders:async()=>({}),ensureConversation:async()=> 'thread',onReady:value=>ready=value})))
 const input=document.querySelector('input');Object.defineProperty(input,'files',{value:[{name:'photo.jpg',size:3,arrayBuffer:async()=>new Uint8Array([1,2,3]).buffer}]})
 await act(async()=>input.dispatchEvent(new dom.window.Event('change',{bubbles:true})))
 assert(document.querySelector('img'));assert.equal(calls.length,0)
 await act(async()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='Use this photo').click())
 assert.equal(calls.length,1);assert(calls[0].url.includes('/photo-sources?'));assert.equal(ready.file_id,'owned-photo')
 assert(!calls.some(c=>c.url.includes('/executions')))
 await act(async()=>root.unmount());console.log('PASS: photo preview, explicit upload, owned source receipt, no paid generation on upload')
}finally{await unlink(out)}
