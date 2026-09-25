// Run Vite and open /scripts/test-new-project-modal.html.
// Exercises the real React component, without backend or provider calls.
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { NewProjectModal } from '../src/components/NewProjectModal.jsx'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const fixture = document.getElementById('fixture')
const root = createRoot(fixture)
const results = []
let submissions = []
let closes = 0

function check(condition, message) {
  if (!condition) throw new Error(message)
  results.push(`PASS: ${message}`)
}

async function render(open, extra = {}) {
  await act(async () => root.render(
    <NewProjectModal
      open={open}
      onClose={() => { closes += 1 }}
      onSubmit={(value) => submissions.push(value)}
      {...extra}
    />,
  ))
}

function fields() {
  return [...fixture.querySelectorAll('input, textarea')]
}

async function fill(values) {
  await act(async () => {
    fields().forEach((field, index) => {
      const prototype = field.tagName === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(field, values[index])
      field.dispatchEvent(new Event('input', { bubbles: true }))
    })
  })
}

try {
  await render(false)
  check(!fixture.querySelector('form'), 'closed modal has no draft form')
  await render(true)
  check(fields().length === 5 && fields().every(field => field.value === ''), 'new form opens with every field blank')
  check(!fixture.innerHTML.includes('Or Akiva') && !fixture.innerHTML.includes('Yossi') && !fixture.innerHTML.includes('Mission-critical'), 'prior-looking examples are absent')
  check(fixture.querySelector('[type="submit"]').disabled, 'blank form cannot create a project')

  await fill(['Previous project', 'Previous description', 'Previous location', 'Previous contacts', 'Previous tasks'])
  check(fields()[0].value === 'Previous project', 'draft accepts input')
  await act(async () => [...fixture.querySelectorAll('button')].find(button => button.textContent === 'Cancel').click())
  check(closes === 1 && submissions.length === 0, 'Cancel only closes; it never submits a project')
  await render(false)
  await render(true)
  check(fields().every(field => field.value === ''), 'reopening after a populated draft starts clean')

  await fill(['  New project  ', '  New description  ', '', '', ''])
  check(!fixture.querySelector('[type="submit"]').disabled, 'valid required fields enable creation')
  await act(async () => fixture.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  check(submissions.length === 1 && submissions[0].name === 'New project' && submissions[0].software_description === 'New description', 'submission sends current trimmed values')
  check(submissions[0].location_base === '' && submissions[0].key_contacts === '' && submissions[0].initial_tactical_tasks === '', 'submission does not inject previous details or default location')

  await render(true, { error: 'Please retry' })
  check(fields()[0].value === '  New project  ', 'retry error preserves the current open draft')
  await render(false)
  await render(true)
  check(fields().every(field => field.value === ''), 'opening after successful submission starts clean')
  await act(async () => fixture.querySelector('[aria-label="Close"]').click())
  check(closes === 2 && submissions.length === 1, 'close button does not submit')
  await render(true, { canSubmit: false })
  await fill(['Name', 'Description', '', '', ''])
  check(fixture.querySelector('[type="submit"]').disabled, 'existing create permission gate remains enforced')
  document.getElementById('results').textContent = `${results.join('\n')}\nALL PASSED (${results.length})`
  document.title = 'PASS: New Project regression tests'
} catch (error) {
  document.getElementById('results').textContent = `${results.join('\n')}\nFAIL: ${error.stack}`
  document.title = 'FAIL: New Project regression tests'
} finally {
  await act(async () => root.unmount())
}
