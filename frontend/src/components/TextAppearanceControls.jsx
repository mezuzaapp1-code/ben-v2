import { FONTS } from '../lib/subtitleDraft.js'
export default function TextAppearanceControls({ style: s, onChange, prefix }) {
  return <div className="ben-video-editor__appearance">
    <label>Font<select aria-label={`${prefix} font`} value={s.font} onChange={e=>onChange('font',e.target.value)}>{FONTS.map(font=><option key={font}>{font}</option>)}</select></label>
    <label>Size<input aria-label={`${prefix} size`} type="range" min="2" max="12" step="0.5" value={s.size} onChange={e=>onChange('size',Number(e.target.value))} /></label>
    <div className="ben-video-editor__formatting" aria-label={`${prefix} formatting`}>
      <button type="button" aria-label={`${prefix} bold`} aria-pressed={!!s.bold} onClick={()=>onChange('bold',!s.bold)}><b>B</b></button>
      <button type="button" aria-label={`${prefix} italic`} aria-pressed={!!s.italic} onClick={()=>onChange('italic',!s.italic)}><i>I</i></button>
      <button type="button" aria-label={`${prefix} underline`} aria-pressed={!!s.underline} onClick={()=>onChange('underline',!s.underline)}><u>U</u></button>
    </div>
    <label>Text color<input aria-label={`${prefix} color`} type="color" value={s.color} onChange={e=>onChange('color',e.target.value)} /></label>
    <label>Background<select aria-label={`${prefix} background mode`} value={s.backgroundMode || 'solid'} onChange={e=>onChange('backgroundMode',e.target.value)}><option value="none">None · ללא רקע</option><option value="solid">Solid color</option></select></label>
    {s.backgroundMode !== 'none' && <>
      <label>Background color<input aria-label={`${prefix} background color`} type="color" value={s.background} onChange={e=>onChange('background',e.target.value)} /></label>
      <label>Background opacity<input aria-label={`${prefix} background opacity`} type="range" min="0" max="100" value={s.opacity} onChange={e=>onChange('opacity',Number(e.target.value))} /></label>
    </>}
    <label>Shadow<select aria-label={`${prefix} shadow`} value={s.shadow || 'none'} onChange={e=>onChange('shadow',e.target.value)}><option value="none">None</option><option value="soft">Soft shadow</option><option value="depth">Depth shadow</option><option value="glow">Glow</option></select></label>
    <div className="ben-video-editor__times">
      <label>Outline color<input aria-label={`${prefix} outline color`} type="color" value={s.outlineColor || '#000000'} onChange={e=>onChange('outlineColor',e.target.value)} /></label>
      <label>Outline width<input aria-label={`${prefix} outline width`} type="range" min="0" max="0.08" step="0.01" value={s.outlineWidth || 0} onChange={e=>onChange('outlineWidth',Number(e.target.value))} /></label>
    </div>
    <label>Letter spacing<input aria-label={`${prefix} letter spacing`} type="range" min="-0.05" max="0.3" step="0.01" value={s.spacing || 0} onChange={e=>onChange('spacing',Number(e.target.value))} /></label>
  </div>
}
