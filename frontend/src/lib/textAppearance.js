const SHADOWS = {
  none: 'none',
  soft: '0 0.05em 0.12em rgba(0,0,0,.8)',
  depth: '0.02em 0.02em 0 #242424,0.04em 0.04em 0 #242424,0.06em 0.06em 0 #242424,0.08em 0.08em 0 #242424,0.12em 0.14em 0.1em rgba(0,0,0,.6)',
  glow: '0 0 0.08em currentColor,0 0 0.25em currentColor',
}
export function textAppearance(style) {
  return { fontFamily: `${style.font}, sans-serif`, fontSize: `${style.size}cqw`, color: style.color,
    fontWeight: style.bold ? 700 : 400, fontStyle: style.italic ? 'italic' : 'normal', textDecoration: style.underline ? 'underline' : 'none',
    letterSpacing: `${style.spacing || 0}em`, textShadow: SHADOWS[style.shadow] || 'none',
    WebkitTextStroke: `${style.outlineWidth || 0}em ${style.outlineColor || '#000000'}`, paintOrder: 'stroke fill' }
}
export function textBackground(style) {
  return style.backgroundMode === 'none' ? 'transparent' : style.background + Math.round(style.opacity * 255 / 100).toString(16).padStart(2, '0')
}
