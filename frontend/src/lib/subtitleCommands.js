// Deliberately limited local commands: unknown instructions never alter a draft.
export function subtitleStyleCommand(input) {
  const text = input.normalize('NFKC').trim().toLowerCase().replace(/[.!?؟]+$/u, '').trim().replace(/\s+/gu, ' ')
  const hebrew = text.match(/^(?:מקם|מקמי|העבר|העבירי|שים|שימי|הזז|הזיזי|יישר|יישרי) (?:את )?ה?כתוביות (.+)$/u)
  const english = text.match(/^(?:move|place|put|position|align) (?:the )?(?:subtitles|captions) (?:to |at |in )?(?:the )?(.+)$/u)
  const target = hebrew?.[1] || english?.[1]
  if (['במרכז', 'לאמצע', 'למרכז', 'במרכז למטה', 'במרכז התחתון', 'center', 'centre', 'middle', 'bottom center'].includes(target)) return { position: 'bottom', alignment: 'center' }
  if (['לימין', 'בימין', 'לימין למטה', 'right', 'bottom right'].includes(target)) return { position: 'bottom', alignment: 'right' }
  if (['לשמאל', 'בשמאל', 'לשמאל למטה', 'left', 'bottom left'].includes(target)) return { position: 'bottom', alignment: 'left' }
  if (['לפי השפה', 'by language'].includes(target)) return { position: 'bottom', alignment: 'auto' }
  if (['במרכז המסך', 'באמצע המסך', 'middle of the screen', 'center of the screen', 'center of the video'].includes(target)) return { position: 'center', alignment: 'center' }
  if (['למעלה', 'top'].includes(target)) return { position: 'top' }
  if (['בתחתית', 'למטה', 'bottom'].includes(target)) return { position: 'bottom' }
  return null
}
