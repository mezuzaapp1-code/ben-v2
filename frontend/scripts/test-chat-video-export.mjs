import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { writeFile, unlink } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
import { exportTimeline, overlaySvg } from '../src/lib/renderTextTrack.js'
const dom=new JSDOM('<div id="app"></div>',{url:'https://ben.test'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const {default:React,act}=await import('react'),{createRoot}=await import('react-dom/client')
const output=new URL('../.mobile-upload-test.mjs',import.meta.url)
if(!process.env.BEN_EDITOR_PREBUILT){const result=await build({entryPoints:[new URL('../src/components/MobileVideoUpload.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react-dom','react/jsx-runtime'],define:{'import.meta.env':'{}'}});await writeFile(output,result.outputFiles[0].text)}
try{
 const {default:Upload}=await import(output.href)
 let requests=[],accepted=0,lost=true
 globalThis.fetch=async(url,options)=>{requests.push({url,options});if(lost)throw Error('connection lost');return {ok:true}}
 const root=createRoot(document.getElementById('app'))
 await act(async()=>root.render(React.createElement(Upload,{scope:'user',ensureConversation:async()=>'chat',buildHeaders:async()=>({}),onAccepted:()=>accepted++})))
 const input=document.querySelector('input');Object.defineProperty(input,'files',{value:[{name:'phone.mov',size:3,arrayBuffer:async()=>new Uint8Array([1,2,3]).buffer}]})
 await act(async()=>input.dispatchEvent(new dom.window.Event('change',{bubbles:true})))
 const button=document.querySelector('button');assert(!button.disabled)
 await act(async()=>{button.click();button.click();await new Promise(r=>setTimeout(r,60))})
 assert.equal(requests.length,1);assert(!requests[0].url.includes('workspace_id'));assert.match(document.body.textContent,/not confirmed/)
 lost=false;await act(async()=>{button.click();await new Promise(r=>setTimeout(r,60))})
 assert.equal(requests.length,2);assert.equal(requests[0].url,requests[1].url);assert.equal(accepted,1)
 assert.match(document.body.textContent,/Video accepted/);await act(async()=>root.unmount())
 const d={source:{duration_seconds:3},body:{cues:[{start:0,end:1,text:'<img src=x> שלום'}],textLayers:[],style:{font:'Tahoma',size:5,alignment:'center',position:'bottom',backgroundMode:'none'}}}
 assert.deepEqual(exportTimeline(d),[{start:0,end:1},{start:1,end:3}])
 const svg=overlaySvg(d,720,1280,.5);assert(svg.includes('&lt;img src=x&gt;'));assert(!svg.includes('<img'));assert(svg.includes('background-color:transparent'));assert(!overlaySvg(d,720,1280,2).includes('שלום'))
 console.log('PASS: projectless upload, double-click guard, uncertain retry identity, text timing and escaping')
}finally{await unlink(output)}
