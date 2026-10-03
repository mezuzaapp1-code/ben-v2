export const roles = {
  animate_source: 'Animate this image', character_reference: 'Character reference',
  background: 'Background', style_reference: 'Style & atmosphere',
}

export function outline(brief) {
  if (!brief.trim()) throw new Error('Add your idea or script first.')
  return { schema_version:'production-plan-v1', profile:'three-scenes-15s-v1',
    original_brief:brief, language:'en-US', aspect_ratio:'9:16', width:720, height:1280,
    target_duration_ms:15000, duration_tolerance_ms:100, attachments:[],
    scenes:[0,1,2].map(() => ({ scene_id:crypto.randomUUID(), duration_ms:5000,
      narration_text:'', visual:{kind:'generated_scene',prompt:'',reference_ids:[]},
      transition_to_next:{kind:'cut',overlap_ms:0} })) }
}

export function problems(plan) {
  if (!plan?.original_brief.trim()) return 'Add your idea or script.'
  if (plan.scenes.length !== 3 || plan.scenes.reduce((n,s)=>n+s.duration_ms,0)!==15000)
    return 'This outline needs three scenes totaling 15 seconds.'
  for (const [i,s] of plan.scenes.entries()) {
    if (!(s.visual.prompt || s.visual.motion_prompt || '').trim()) return `Describe the visuals for scene ${i+1}.`
  }
  return ''
}

// Persist the exact request BEFORE sending. An uncertain response must reuse it.
export function saveIntent(storage, scope, body) {
  const key=`ben-plan-pending:${scope}`
  const old=storage.getItem(key)
  if (old) return JSON.parse(old)
  const intent={key:crypto.randomUUID(),body}
  storage.setItem(key,JSON.stringify(intent))
  return intent
}

export function clearIntent(storage, scope) { storage.removeItem(`ben-plan-pending:${scope}`) }
