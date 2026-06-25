import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { isRtlMarkdown } from '../lib/markdownDirection.js'
import './ChatMarkdown.css'

/**
 * Renders assistant markdown as semantic HTML with RTL-aware typography.
 */
export function ChatMarkdown({ content, className = '' }) {
  const text = String(content ?? '')
  if (!text.trim()) return null

  const rtl = isRtlMarkdown(text)
  const rootClass = [
    'chat-markdown',
    'bubble-text',
    rtl ? 'chat-markdown--rtl' : 'chat-markdown--ltr',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={rootClass} dir={rtl ? 'rtl' : 'ltr'} lang={rtl ? 'he' : undefined}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  )
}
