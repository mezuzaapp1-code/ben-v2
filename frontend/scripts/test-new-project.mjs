import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { readFile, writeFile, unlink } from 'node:fs/promises'
import { JSDOM } from 'jsdom'
const dom = new JSDOM('<div id="app"></div>', { url:'https://ben.test' })
Object.assign(globalThis, { window:dom.window, document:dom.window.document, IS_REACT_ACT_ENVIRONMENT:true })
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
const { default:React, act } = await import('react')
const { createRoot } = await import('react-dom/client')
const output = new URL('../.new-project-test.mjs', import.meta.url)
const built = await build({stdin:{contents:"export {NewProjectModal} from './src/components/NewProjectModal.jsx'; export {createProject} from './src/api/projects.js'",resolveDir:process.cwd()},bundle:true,write:false,format:'esm',platform:'node',jsx:'automatic',external:['react','react/jsx-runtime'],define:{'import.meta.env':'{"DEV":true}'}})
await writeFile(output,built.outputFiles[0].text)
try {
  const {NewProjectModal,createProject}=await import(output.href)
  const root=createRoot(document.getElementById('app'))
  const submitted=[]
  const props={open:true,onClose:()=>{},onSubmit:value=>submitted.push(value)}
  await act(async()=>root.render(React.createElement(NewProjectModal,props)))
  assert.equal(document.querySelectorAll('input').length,1)
  assert.equal(document.querySelectorAll('textarea').length,1)
  assert.equal(document.querySelector('button[type=submit]').disabled,true)
  const input=document.querySelector('input')
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input,'  Video test  '); input.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
  assert.equal(document.querySelector('button[type=submit]').disabled,false)
  await act(async()=>document.querySelector('form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true})))
  assert.deepEqual(submitted,[{name:'Video test',description:undefined}])
  await act(async()=>root.render(React.createElement(NewProjectModal,{...props,submitting:true})))
  assert.equal(document.querySelector('button[type=submit]').disabled,true)
  await act(async()=>root.unmount())
  let request
  globalThis.fetch=async(url,options)=>{request={url,options};return new Response(JSON.stringify({id:'project-id',name:'Video test'}),{status:200,headers:{'Content-Type':'application/json'}})}
  await createProject(submitted[0],{Authorization:'Bearer test-only'})
  assert(request.url.endsWith('/api/projects'))
  assert.equal(request.options.headers['Content-Type'],'application/json')
  assert.equal(request.options.headers.Authorization,'Bearer test-only')
  assert.deepEqual(JSON.parse(request.options.body),{name:'Video test'})
  const app=await readFile(new URL('../src/App.jsx',import.meta.url),'utf8')
  for(const retired of ['conversationalProjectInit','runProjectSetupBootstrap','buildConversationalInitPayload']) assert(!app.includes(retired))
  assert(app.includes('handleOpenProject(project)'))
  console.log('PASS: form, optional description, busy guard, authenticated JSON contract and removal of onboarding')
} finally {await unlink(output)}

