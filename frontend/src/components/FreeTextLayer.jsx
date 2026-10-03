import { useRef, useState } from 'react'
import { clampSubtitlePoint, subtitleAlignment, subtitleDirection } from '../lib/subtitleDraft.js'
import { textAppearance, textBackground } from '../lib/textAppearance.js'

export default function FreeTextLayer({ layer, selected, onSelect, onMove }) {
  const [preview, setPreview] = useState(null)
  const drag = useRef(null)
  const s = layer.style, direction = subtitleDirection(layer.text)
  function bounded(node, x, y) {
    const stage = node.parentElement.getBoundingClientRect(), box = node.getBoundingClientRect()
    return clampSubtitlePoint(x, y, stage.width ? box.width / stage.width : 0, stage.height ? box.height / stage.height : 0)
  }
  function finish(e, cancel = false) {
    const origin = drag.current
    if (!origin || origin.id !== e.pointerId) return
    drag.current = null; setPreview(null)
    if (!cancel && origin.point && (origin.point.x !== s.x || origin.point.y !== s.y)) onMove(origin.point)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  return <div className={`ben-video-editor__caption ben-video-editor__caption--custom ben-video-editor__text-layer ${selected ? 'ben-video-editor__text-layer--selected' : ''}`}
    dir={direction} role="button" tabIndex={0} aria-label={`Move text: ${layer.text || 'empty text'}`} onClick={onSelect}
    style={{...textAppearance(s),left:`${preview?.x ?? s.x}%`,top:`${preview?.y ?? s.y}%`,textAlign:subtitleAlignment(s.alignment,direction)}}
    onPointerDown={e => {
      if (e.button !== 0) return
      const stage = e.currentTarget.parentElement.getBoundingClientRect()
      if (!stage.width || !stage.height) return
      e.preventDefault(); onSelect(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId)
      drag.current = {id:e.pointerId,left:e.clientX,top:e.clientY,stage,point:null}
    }}
    onPointerMove={e => {
      const origin = drag.current
      if (!origin || origin.id !== e.pointerId) return
      origin.point = bounded(e.currentTarget, s.x + (e.clientX-origin.left)*100/origin.stage.width, s.y + (e.clientY-origin.top)*100/origin.stage.height)
      setPreview(origin.point)
    }} onPointerUp={e=>finish(e)} onPointerCancel={e=>finish(e,true)} onLostPointerCapture={e=>finish(e,true)}
    onKeyDown={e=>{
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return
      e.preventDefault(); const step=e.shiftKey?5:1
      onMove(bounded(e.currentTarget,s.x+(e.key==='ArrowLeft'?-step:e.key==='ArrowRight'?step:0),s.y+(e.key==='ArrowUp'?-step:e.key==='ArrowDown'?step:0)))
    }}>
    <span style={{backgroundColor:textBackground(s)}}>{layer.text || '\u00a0'}</span>
  </div>
}
