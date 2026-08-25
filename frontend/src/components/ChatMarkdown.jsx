import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { collectNodeText, getMessageTextDirection } from '../lib/markdownDirection.js'
import './ChatMarkdown.css'

function DirectedBlock({ as: Tag, children, className = '' }) {
  const dir = getMessageTextDirection(collectNodeText(children))
  return (
    <Tag dir={dir} className={`${className} chat-markdown__block chat-markdown__block--${dir}`.trim()}>
      {children}
    </Tag>
  )
}

const markdownComponents = {
  p: ({ children }) => <DirectedBlock as="p">{children}</DirectedBlock>,
  h1: ({ children }) => <DirectedBlock as="h1">{children}</DirectedBlock>,
  h2: ({ children }) => <DirectedBlock as="h2">{children}</DirectedBlock>,
  h3: ({ children }) => <DirectedBlock as="h3">{children}</DirectedBlock>,
  h4: ({ children }) => <DirectedBlock as="h4">{children}</DirectedBlock>,
  li: ({ children }) => <DirectedBlock as="li">{children}</DirectedBlock>,
  ul: ({ children }) => <DirectedBlock as="ul">{children}</DirectedBlock>,
  ol: ({ children }) => <DirectedBlock as="ol">{children}</DirectedBlock>,
  blockquote: ({ children }) => <DirectedBlock as="blockquote">{children}</DirectedBlock>,
  pre: ({ children }) => (
    <pre dir="ltr" className="chat-markdown__isolate">
      {children}
    </pre>
  ),
  code: ({ className, children, ...props }) => {
    const inline = !String(className || '').includes('language-')
    if (!inline) {
      return (
        <code className={className} {...props}>
          {children}
        </code>
      )
    }
    return (
      <code dir="ltr" className={`chat-markdown__isolate ${className || ''}`.trim()} {...props}>
        {children}
      </code>
    )
  },
  a: ({ children, ...props }) => (
    <a dir="ltr" className="chat-markdown__isolate" {...props}>
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="chat-markdown__table-wrap" dir="ltr">
      <table>{children}</table>
    </div>
  ),
}

/**
 * Assistant markdown. Direction is per block; chrome layout is never flipped.
 */
export function ChatMarkdown({ content, className = '' }) {
  const text = String(content ?? '')
  if (!text.trim()) return null

  const rootClass = ['chat-markdown', 'bubble-text', className].filter(Boolean).join(' ')

  return (
    <div className={rootClass}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
