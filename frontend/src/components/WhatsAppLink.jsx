import { buildWhatsAppUrl } from '../mobile/whatsapp.js'

export function WhatsAppLink({ phone, message, children, className = 'action-card__btn action-card__btn--whatsapp' }) {
  const href = buildWhatsAppUrl({ phone, message })
  return (
    <a
      href={href}
      className={className}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
    >
      {children ?? 'WhatsApp'}
    </a>
  )
}
