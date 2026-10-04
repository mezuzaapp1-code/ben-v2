import { textAppearance, textBackground } from './textAppearance.js'
import { activeSubtitle, subtitleAlignment, subtitleDirection } from './subtitleDraft.js'

const xml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]))
const css = object => Object.entries(object).map(([key,value]) => `${key.replace(/[A-Z]/g, m => '-'+m.toLowerCase())}:${value}`).join(';')
export function exportTimeline(document) {
  const duration = document.source.duration_seconds
  const round = x => Math.round(Math.max(0, Math.min(duration,x))*1e6)/1e6
  const points = [...new Set([0,round(duration), ...[...document.body.cues,...document.body.textLayers].flatMap(c=>[round(c.start),round(c.end)])])].sort((a,b)=>a-b)
  return points.slice(0,-1).map((start,i)=>({start,end:points[i+1]}))
}
export function overlaySvg(document, width, height, time) {
  const {cues,style,textLayers} = document.body
  function box(text,s,custom=false,fallback='ltr') {
    const dir=subtitleDirection(text,fallback), position=custom?'custom':s.position
    const placement=position==='custom'?`left:${s.x}%;top:${s.y}%;width:max-content;max-width:90%;transform:translate(-50%,-50%);`
      :`left:5%;right:5%;${position==='top'?'top:8%;':position==='center'?'top:50%;transform:translateY(-50%);':'bottom:17%;'}`
    const appearance={...textAppearance(s),fontSize:`${s.size*width/100}px`}
    return `<div dir="${dir}" style="position:absolute;${placement}white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.35;text-align:${subtitleAlignment(s.alignment,dir)};${xml(css(appearance))}"><span style="padding:.12em .3em;box-decoration-break:clone;-webkit-box-decoration-break:clone;background-color:${textBackground(s)}">${xml(text)}</span></div>`
  }
  const caption=activeSubtitle(cues,time)
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><foreignObject width="100%" height="100%"><div xmlns="http://www.w3.org/1999/xhtml" style="position:relative;width:${width}px;height:${height}px;overflow:hidden;">${caption?box(caption,style,false,subtitleDirection(cues.map(c=>c.text).join(' '))):''}${textLayers.filter(l=>l.start<=time&&time<l.end).map(l=>box(l.text,l.style,true)).join('')}</div></foreignObject></svg>`
}
export async function rasterizeTextTrack(document,width,height,signal,onProgress=()=>{}) {
  if (![[1280,720],[720,1280]].some(([w,h])=>w===width&&h===height)) throw Error('Export supports prepared 720p BEN videos.')
  await globalThis.document.fonts.ready
  signal.throwIfAborted()
  const canvas=globalThis.document.createElement('canvas');canvas.width=width;canvas.height=height
  const context=canvas.getContext('2d'), frames=[], timeline=exportTimeline(document)
  let total=0
  for (const [i,interval] of timeline.entries()) {
    signal.throwIfAborted()
    const svg=overlaySvg(document,width,height,(interval.start+interval.end)/2)
    const image=new Image()
    await new Promise((resolve,reject)=>{
      const stop=()=>{clearTimeout(timer);image.onload=null;image.onerror=null;signal.removeEventListener('abort',abort)}
      const abort=()=>{stop();reject(Error('Export cancelled'))}
      const timer=setTimeout(()=>{stop();reject(Error('Text preparation timed out. Please retry.'))},10000)
      image.onload=()=>{stop();resolve()};image.onerror=()=>{stop();reject(Error('This browser could not prepare the text. Please try Chrome or Edge.'))}
      signal.addEventListener('abort',abort,{once:true})
      image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg)
    })
    signal.throwIfAborted()
    context.clearRect(0,0,width,height);context.drawImage(image,0,0)
    const png=canvas.toDataURL('image/png').split(',')[1]
    total+=png.length
    if(total>22_000_000)throw Error('This text layout exceeds the export limit. Reduce the number of text changes.')
    frames.push({...interval,png});onProgress(`Preparing text ${i+1}/${timeline.length}…`)
  }
  return {width,height,frames}
}
