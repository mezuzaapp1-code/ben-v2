export function ProjectSuccessToast({ message, visible }) {
  if (!visible || !message) return null
  return (
    <div className="project-toast" role="status" aria-live="polite">
      {message}
    </div>
  )
}
