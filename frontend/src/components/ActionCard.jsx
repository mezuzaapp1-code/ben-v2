import { CameraCaptureInput } from './CameraCaptureInput.jsx'
import { WhatsAppLink } from './WhatsAppLink.jsx'
function formatMoney(amount, currency = 'ILS') {
  const val = Number(amount)
  if (!Number.isFinite(val)) return '—'
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(val)
  } catch {
    return `${val.toFixed(2)} ${currency}`
  }
}

function VendorMatch({ match }) {
  if (!match?.matched) return <span className="action-card__match action-card__match--warn">Unmatched vendor</span>
  const tone = match.match_status === 'exact' ? 'ok' : 'partial'
  return (
    <span className={`action-card__match action-card__match--${tone}`}>
      {match.match_status === 'exact' ? 'Matched' : 'Partial'}: {match.member_name}
    </span>
  )
}

function QuotationDeliberationCard({ payload }) {
  return (
    <article className="action-card action-card--quotation">
      <header className="action-card__header">
        <h3>Quotation deliberation</h3>
        {payload.active ? <span className="action-card__badge">In progress</span> : null}
      </header>
      <ol className="action-card__checklist">
        {(payload.checklist || []).map((step) => (
          <li
            key={step.key}
            className={`action-card__check-item${step.completed ? ' action-card__check-item--done' : ''}${step.current ? ' action-card__check-item--current' : ''}`}
          >
            <span className="action-card__check-mark">{step.completed ? '✓' : step.current ? '→' : '○'}</span>
            <span>{step.label}</span>
          </li>
        ))}
      </ol>
      {payload.prompt ? <p className="action-card__prompt">{payload.prompt}</p> : null}
    </article>
  )
}

function ReceiptCreditCard({ payload, previewUrl }) {
  const isCredit = payload.document_type === 'credit_memo' || payload.tool === 'process_credit_memo'
  return (
    <article className={`action-card action-card--receipt${isCredit ? ' action-card--credit' : ''}`}>
      <header className="action-card__header">
        <h3>{isCredit ? 'Credit memo' : 'Receipt captured'}</h3>
        {payload.saved_to_ledger ? (
          <span className="action-card__badge action-card__badge--saved">Saved to Ledger</span>
        ) : null}
      </header>
      {previewUrl ? (
        <div className="action-card__preview">
          <img src={previewUrl} alt="Document preview" loading="lazy" />
        </div>
      ) : null}
      <dl className="action-card__fields">
        <div><dt>Amount</dt><dd>{formatMoney(payload.amount, payload.currency)}</dd></div>
        <div><dt>Vendor</dt><dd>{payload.vendor || '—'}</dd></div>
        <div><dt>Match</dt><dd><VendorMatch match={payload.vendor_match} /></dd></div>
      </dl>
    </article>
  )
}

function CashFlowForecastCard({ payload }) {
  const runway = payload.runway_weeks || []
  const maxBal = Math.max(...runway.map((w) => Math.abs(w.balance)), 1)
  return (
    <article className={`action-card action-card--forecast${payload.safety_trigger ? ' action-card--alert' : ''}`}>
      <header className="action-card__header">
        <h3>Cash flow forecast</h3>
        {payload.safety_trigger ? (
          <span className="action-card__badge action-card__badge--alert">Safety trigger</span>
        ) : (
          <span className="action-card__badge">Stable</span>
        )}
      </header>
      <p className="action-card__summary">{payload.safety_message}</p>
      <div className="action-card__runway" role="img" aria-label="Cash runway visualization">
        {runway.map((w) => (
          <div key={w.week} className="action-card__runway-week" title={`Week ${w.week}: ${w.balance}`}>
            <div
              className="action-card__runway-bar"
              style={{ width: `${Math.max(8, (Math.abs(w.balance) / maxBal) * 100)}%` }}
            />
            <span>W{w.week}</span>
          </div>
        ))}
      </div>
      {payload.totals ? (
        <p className="action-card__meta">
          Net (pending): {formatMoney(payload.totals.net_with_pending, 'ILS')}
        </p>
      ) : null}
    </article>
  )
}

function LifecycleOverviewCard({ payload }) {
  return (
    <article className="action-card action-card--lifecycle">
      <header className="action-card__header">
        <h3>Lifecycle overview</h3>
        <span className="action-card__badge">{payload.project_name || 'Project'}</span>
      </header>
      <dl className="action-card__fields">
        <div><dt>Days elapsed</dt><dd>{payload.days_elapsed ?? '—'}</dd></div>
        <div><dt>Estimated</dt><dd>{formatMoney(payload.estimated_cost_nis, 'ILS')}</dd></div>
        <div><dt>Actual</dt><dd>{formatMoney(payload.actual_cost_nis, 'ILS')}</dd></div>
        <div>
          <dt>Variance</dt>
          <dd>
            {payload.variance_nis != null
              ? `${formatMoney(payload.variance_nis, 'ILS')}${payload.variance_pct != null ? ` (${payload.variance_pct}%)` : ''}`
              : '—'}
          </dd>
        </div>
      </dl>
      {payload.message ? <p className="action-card__prompt">{payload.message}</p> : null}
      {payload.subsistence?.total_overhead_nis ? (
        <p className="action-card__meta">
          Subsistence overhead: {formatMoney(payload.subsistence.total_overhead_nis, 'ILS')}
        </p>
      ) : null}
    </article>
  )
}

function RedFlagList({ flags }) {
  if (!flags?.length) return <span className="action-card__match action-card__match--ok">No active red flags</span>
  return (
    <ul className="action-card__red-flags">
      {flags.map((f) => (
        <li key={f} className="action-card__red-flag">{String(f).replace(/_/g, ' ')}</li>
      ))}
    </ul>
  )
}

function GovernmentIntelligenceCard({ payload }) {
  const isTactical = payload.tool === 'initiate_tactical_quotation'
  return (
    <article className={`action-card action-card--intel${payload.red_flags?.length ? ' action-card--alert' : ''}`}>
      <header className="action-card__header">
        <h3>{isTactical ? 'Tactical quotation & site intel' : 'Government intelligence'}</h3>
        <span className="action-card__badge">{payload.registry_source ? 'MoL Registry' : 'Intel'}</span>
      </header>
      <dl className="action-card__fields">
        <div><dt>Site</dt><dd>{payload.site_address || payload.query || '—'}</dd></div>
        <div><dt>Site manager</dt><dd>{payload.registered_site_manager || '—'}</dd></div>
        <div><dt>Crane status</dt><dd>{(payload.crane_status || '—').replace(/_/g, ' ')}</dd></div>
        <div><dt>Red flags</dt><dd><RedFlagList flags={payload.red_flags} /></dd></div>
      </dl>
      {payload.active_safety_orders?.length ? (
        <p className="action-card__meta">
          {payload.active_safety_orders.length} active safety order(s)
        </p>
      ) : null}
      {payload.shutdown_history?.length ? (
        <p className="action-card__meta action-card__meta--alert">
          Shutdown history: {payload.shutdown_history.length} event(s)
        </p>
      ) : null}
      {isTactical ? (
        <>
          {payload.hazard_map?.length ? (
            <ul className="action-card__hazards">
              {payload.hazard_map.map((h, i) => (
                <li key={i}>
                  <strong>{h.hazard}</strong> — {h.mitigation?.replace(/_/g, ' ')}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="action-card__meta">
            Safety premium {payload.safety_premium_pct}% ({formatMoney(payload.safety_premium_nis, 'ILS')}) ·
            Total quote {formatMoney(payload.total_quote_nis, 'ILS')}
          </p>
        </>
      ) : null}
    </article>
  )
}

function ComplianceInsuranceCard({ payload }) {
  const blocked = payload.blocked
  return (
    <article className={`action-card action-card--compliance${blocked ? ' action-card--alert' : ''}`}>
      <header className="action-card__header">
        <h3>Compliance & insurance</h3>
        <span className={`action-card__badge${blocked ? ' action-card__badge--alert' : ' action-card__badge--saved'}`}>
          {blocked ? 'Blocked' : 'Verified'}
        </span>
      </header>
      <dl className="action-card__fields">
        <div><dt>Worker</dt><dd>{payload.worker_name || '—'}</dd></div>
        <div><dt>Insurance</dt><dd>{payload.insurance_policy_id || '—'}</dd></div>
        <div><dt>Contract until</dt><dd>{payload.contract_valid_until || '—'}</dd></div>
        <div><dt>Safety score</dt><dd>{payload.safety_profile_score ?? '—'}</dd></div>
        <div><dt>Flags</dt><dd><RedFlagList flags={payload.red_flags} /></dd></div>
      </dl>
      {payload.message ? <p className="action-card__prompt">{payload.message}</p> : null}
    </article>
  )
}

function buildBriefingWhatsAppMessage(briefing) {
  const lines = [
    'BASALT — Next-day look-ahead briefing',
    briefing.next_day ? `Date: ${briefing.next_day}` : null,
    briefing.clocked_hours_today != null ? `Hours today: ${briefing.clocked_hours_today}` : null,
    briefing.site_manager ? `Site manager: ${briefing.site_manager}` : null,
    briefing.friction_events?.length ? `Friction: ${briefing.friction_events.join(', ')}` : null,
    briefing.priorities?.length ? `Priorities:\n${briefing.priorities.map((p) => `• ${p}`).join('\n')}` : null,
  ].filter(Boolean)
  return lines.join('\n')
}

function NextDayBriefingCard({ payload }) {
  const briefing = payload.briefing || payload
  const waMessage = buildBriefingWhatsAppMessage(briefing)
  return (
    <article className="action-card action-card--briefing">
      <header className="action-card__header">
        <h3>Next-day look-ahead</h3>
        <span className="action-card__badge">{briefing.next_day || 'Tomorrow'}</span>
      </header>
      <dl className="action-card__fields">
        <div><dt>Hours today</dt><dd>{briefing.clocked_hours_today ?? '—'}</dd></div>
        <div><dt>Fuel overhead</dt><dd>{formatMoney(briefing.fuel_overhead_nis, 'ILS')}</dd></div>
        <div><dt>Subsistence</dt><dd>{formatMoney(briefing.subsistence_overhead_nis, 'ILS')}</dd></div>
        <div><dt>Site manager</dt><dd>{briefing.site_manager || '—'}</dd></div>
      </dl>
      {briefing.friction_events?.length ? (
        <p className="action-card__meta action-card__meta--alert">
          Friction: {briefing.friction_events.join(', ')}
        </p>
      ) : null}
      {briefing.priorities?.length ? (
        <ol className="action-card__priorities">
          {briefing.priorities.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ol>
      ) : null}
      <div className="action-card__attendance-actions">
        <WhatsAppLink message={waMessage}>Share briefing via WhatsApp</WhatsAppLink>
      </div>
    </article>
  )
}

function VarianceBadge({ flag }) {
  const label = String(flag).replace(/_/g, ' ').toLowerCase()
  return <span className="action-card__variance-badge">{label}</span>
}

function DailyAttendanceDelayCard({ payload, onAttendanceAction }) {
  const summary = payload.attendance_summary || {}
  const cards = summary.time_cards || (payload.time_card ? [payload.time_card] : [])
  const primary = payload.time_card || cards[cards.length - 1]
  const foodRange = summary.food_allowance_range_nis || { min: 65, max: 100 }

  const handleApprove = (card, adjustedHours) => {
    onAttendanceAction?.({
      approve: true,
      time_card_id: card.id,
      worker_name: card.worker_name,
      adjusted_hours: adjustedHours,
    })
  }

  const handleEdit = (card) => {
    const current = card.hours_worked
    const input = window.prompt(
      `Adjust hours for ${card.worker_name} (current: ${current}h):`,
      String(current)
    )
    if (input == null) return
    const adjusted = parseFloat(input)
    if (!Number.isFinite(adjusted) || adjusted <= 0) return
    handleApprove(card, adjusted)
  }

  return (
    <article className="action-card action-card--attendance">
      <header className="action-card__header">
        <h3>Daily attendance &amp; delay</h3>
        <span className="action-card__badge">
          {summary.pending_approval_count ?? 0} pending
        </span>
      </header>
      <p className="action-card__meta">
        Poll: {summary.poll_channel === 'sms_whatsapp' ? 'SMS / WhatsApp' : 'Field report'} ·
        Food allowance {foodRange.min}–{foodRange.max} NIS
        {(summary.late_arrival_count ?? 0) > 0 ? ` · ${summary.late_arrival_count} late` : ''}
        {(summary.early_departure_count ?? 0) > 0 ? ` · ${summary.early_departure_count} early out` : ''}
      </p>
      <ul className="action-card__attendance-list">
        {cards.map((card) => (
          <li key={card.id} className="action-card__attendance-row">
            <div className="action-card__attendance-worker">
              <strong>{card.worker_name}</strong>
              <span className={`action-card__status action-card__status--${card.status || 'pending'}`}>
                {card.status || 'pending'}
              </span>
            </div>
            <div className="action-card__attendance-times">
              <span>
                {card.arrival_time || '—'} → {card.departure_time || '—'}
              </span>
              <span>{card.hours_worked}h / {card.standard_hours}h</span>
            </div>
            {card.operational_flags?.length ? (
              <div className="action-card__variance-badges">
                {card.operational_flags.map((f) => (
                  <VarianceBadge key={f} flag={f} />
                ))}
              </div>
            ) : (
              <span className="action-card__match action-card__match--ok">On schedule</span>
            )}
            {card.reason_hints?.length ? (
              <p className="action-card__meta">Note: {card.reason_hints.join(', ')}</p>
            ) : null}
            {card.poll_channel ? (
              <span className="action-card__match action-card__match--ok">
                via {card.poll_channel === 'sms_whatsapp' ? 'SMS/WhatsApp' : card.poll_channel}
              </span>
            ) : null}
            {card.pay ? (
              <p className="action-card__meta">
                Wage {formatMoney(card.pay.wage_nis, 'ILS')} · Food{' '}
                {formatMoney(card.pay.subsistence_nis, 'ILS')}
                {card.pay.subsistence_range_nis
                  ? ` (${card.pay.subsistence_range_nis.min}–${card.pay.subsistence_range_nis.max} NIS band)`
                  : ''}
              </p>
            ) : null}
            {card.status === 'pending' ? (
              <div className="action-card__attendance-actions">
                <button type="button" className="action-card__btn action-card__btn--edit" onClick={() => handleEdit(card)}>
                  Edit hours
                </button>
                <button type="button" className="action-card__btn action-card__btn--approve" onClick={() => handleApprove(card)}>
                  Approve
                </button>
                <WhatsAppLink
                  phone={card.worker_phone}
                  message={[
                    `BASALT attendance — ${card.worker_name}`,
                    `Shift: ${card.arrival_time || '—'} → ${card.departure_time || '—'}`,
                    `Hours: ${card.hours_worked}h / ${card.standard_hours}h`,
                    card.operational_flags?.length
                      ? `Flags: ${card.operational_flags.join(', ')}`
                      : 'Status: on schedule',
                  ].join('\n')}
                >
                  Notify worker
                </WhatsAppLink>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      {primary?.requires_approval || primary?.operational_flags?.length ? (
        <p className="action-card__prompt">Review variance flags before approving partial shifts.</p>
      ) : null}
    </article>
  )
}

const COST_LAYER_COLORS = {
  base_material_cost: '#6fcf97',
  logistics_freight_overhead: '#5b8cff',
  operational_overheads: '#c8a0e8',
  supplier_margin_risk_premium: '#e8d48a',
}

function CostEngineeringBidTabulationCard({ payload, onProcurementAction }) {
  const tender = payload.tender || payload
  const layers = payload.layers || tender.layers || []
  const anomalies = payload.anomalies || tender.anomalies || []
  const total = tender.cost_matrix?.total_bid_nis || payload.cost_matrix?.total_bid_nis

  const handleAccept = () => {
    onProcurementAction?.({
      action: 'accept_bid',
      tender_id: tender.id,
      supplier_name: tender.supplier_name,
    })
  }

  const suggestedCounter = total ? Math.round(total * 0.92) : null

  const handleCounter = () => {
    const input = window.prompt(
      `Counter-offer amount for ${tender.supplier_name} (current: ${total} NIS):`,
      suggestedCounter ? String(suggestedCounter) : ''
    )
    if (input == null) return
    const amount = parseFloat(input)
    if (!Number.isFinite(amount) || amount <= 0) return
    onProcurementAction?.({
      action: 'counter_offer',
      tender_id: tender.id,
      supplier_name: tender.supplier_name,
      counter_offer_nis: amount,
    })
  }

  const counterWhatsAppMessage = [
    `BASALT Cost Engineering — counter-offer`,
    `Supplier: ${tender.supplier_name || 'Supplier'}`,
    `Current bid: ${total ?? '—'} NIS`,
    suggestedCounter ? `Proposed counter: ${suggestedCounter} NIS` : null,
    'Per Or Akiva logistics/freight and subsistence layer tabulation.',
  ]
    .filter(Boolean)
    .join('\n')

  return (
    <article className={`action-card action-card--procurement${anomalies.length ? ' action-card--alert' : ''}`}>
      <header className="action-card__header">
        <h3>Cost engineering bid tabulation</h3>
        <span className="action-card__badge">{tender.supplier_name || 'Supplier'}</span>
      </header>
      <p className="action-card__summary">
        Total bid {formatMoney(total, 'ILS')}
        {anomalies.length ? ` · ${anomalies.length} anomaly flag(s)` : ' · Within baseline'}
      </p>
      <div className="action-card__cost-layers">
        {layers.map((layer) => (
          <div
            key={layer.key}
            className={`action-card__cost-layer${layer.anomaly ? ' action-card__cost-layer--anomaly' : ''}`}
          >
            <div className="action-card__cost-layer-head">
              <span>{layer.label}</span>
              <strong>{formatMoney(layer.amount_nis, 'ILS')}</strong>
            </div>
            <div className="action-card__cost-bar-track">
              <div
                className="action-card__cost-bar-fill"
                style={{
                  width: `${Math.min(100, Math.max(4, layer.share_pct || 0))}%`,
                  background: COST_LAYER_COLORS[layer.key] || '#5b8cff',
                }}
              />
            </div>
            <span className="action-card__cost-share">{layer.share_pct}%</span>
            {layer.anomaly ? (
              <span className="action-card__variance-badge">{layer.anomaly.message}</span>
            ) : null}
          </div>
        ))}
      </div>
      {anomalies.filter((a) => !layers.some((l) => l.anomaly?.layer === a.layer)).map((a) => (
        <p key={a.message} className="action-card__meta action-card__meta--alert">{a.message}</p>
      ))}
      {tender.status === 'evaluated' ? (
        <div className="action-card__attendance-actions">
          <button type="button" className="action-card__btn action-card__btn--approve" onClick={handleAccept}>
            Accept bid
          </button>
          <button type="button" className="action-card__btn action-card__btn--edit" onClick={handleCounter}>
            Counter offer
          </button>
          <WhatsAppLink phone={tender.supplier_phone} message={counterWhatsAppMessage}>
            Send counter via WhatsApp
          </WhatsAppLink>
        </div>
      ) : (
        <p className="action-card__meta">Status: {tender.status}</p>
      )}
    </article>
  )
}

function SkillCategoryTag({ category }) {
  const isStatutory = category === 'statutory_asset'
  return (
    <span
      className={`action-card__skill-tag${isStatutory ? ' action-card__skill-tag--statutory' : ' action-card__skill-tag--trainable'}`}
    >
      {isStatutory ? 'Statutory Asset' : 'Trainable Orientation'}
    </span>
  )
}

function UpskillingStrategyCard({ payload }) {
  const blueprint = payload.skill_blueprint || [
    ...(payload.statutory_assets || []),
    ...(payload.trainable_orientations || []),
  ]
  const scope = payload.engineering_scope || '—'

  const handleEditSkill = (skill) => {
    if (!skill.editable) return
    const input = window.prompt(`Rename trainable skill "${skill.label}":`, skill.label)
    if (input == null || !input.trim()) return
    skill.label = input.trim()
  }

  return (
    <article className="action-card action-card--upskilling">
      <header className="action-card__header">
        <h3>Upskilling strategy</h3>
        <span className="action-card__badge">
          {payload.statutory_count ?? 0} statutory · {payload.trainable_count ?? 0} trainable
        </span>
      </header>
      <p className="action-card__summary">
        Scope: {scope.length > 120 ? `${scope.slice(0, 120)}…` : scope}
      </p>
      {payload.home_base ? (
        <p className="action-card__meta">Home base: {payload.home_base}</p>
      ) : null}
      <ul className="action-card__skill-blueprint">
        {blueprint.map((skill) => (
          <li
            key={skill.skill_id}
            className={`action-card__skill-row${skill.category === 'statutory_asset' ? ' action-card__skill-row--statutory' : ''}`}
          >
            <div className="action-card__skill-head">
              <strong>{skill.label}</strong>
              <SkillCategoryTag category={skill.category} />
            </div>
            <p className="action-card__meta">{skill.regulatory_ref}</p>
            {skill.editable ? (
              <button
                type="button"
                className="action-card__btn action-card__btn--edit action-card__skill-edit"
                onClick={() => handleEditSkill(skill)}
              >
                Edit blueprint
              </button>
            ) : (
              <span className="action-card__match action-card__match--warn">Cannot bypass</span>
            )}
          </li>
        ))}
      </ul>
    </article>
  )
}

function OnsiteProctorSessionCard({ payload, onTrainingAction }) {
  const roi = payload.roi_analysis || {}
  const margin = payload.margin_impact || roi.margin_impact || {}
  const onsite = roi.onsite_proctor || {}
  const offsite = roi.offsite_individual || {}
  const invitees = payload.invitation_list || roi.affected_workers || []
  const session = payload.proctor_session
  const recommended = payload.recommended_path || roi.recommended_path

  const handleSchedule = () => {
    const dateInput = window.prompt(
      'Schedule onsite proctor session (YYYY-MM-DD):',
      session?.scheduled_date || ''
    )
    if (dateInput == null) return
    onTrainingAction?.({
      action: 'schedule_proctor_session',
      scheduled_date: dateInput.trim() || undefined,
    })
  }

  return (
    <article className={`action-card action-card--proctor${invitees.length ? ' action-card--alert' : ''}`}>
      <header className="action-card__header">
        <h3>Onsite proctor session</h3>
        <span className="action-card__badge">
          {roi.certification_gap_count ?? invitees.length} gap(s)
        </span>
      </header>
      <p className="action-card__summary">
        Recommended: {(recommended || 'onsite_proctor').replace(/_/g, ' ')}
        {roi.projected_savings_nis ? ` · saves ${formatMoney(roi.projected_savings_nis, 'ILS')}` : ''}
      </p>
      <dl className="action-card__fields action-card__roi-fields">
        <div>
          <dt>Onsite proctor</dt>
          <dd>{formatMoney(onsite.total_nis, 'ILS')}</dd>
        </div>
        <div>
          <dt>Offsite individual</dt>
          <dd>{formatMoney(offsite.total_nis, 'ILS')}</dd>
        </div>
        <div>
          <dt>Transit / worker</dt>
          <dd>{formatMoney(payload.transit_per_worker_nis, 'ILS')}</dd>
        </div>
        <div>
          <dt>Margin (after training)</dt>
          <dd>{formatMoney(margin.net_after_training_nis, 'ILS')}</dd>
        </div>
      </dl>
      {invitees.length ? (
        <ul className="action-card__invite-list">
          {invitees.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      ) : (
        <p className="action-card__match action-card__match--ok">All certifications current</p>
      )}
      {session ? (
        <p className="action-card__meta action-card__meta--saved">
          Scheduled {session.scheduled_date} · {session.invitation_list?.length ?? 0} invited ·{' '}
          {formatMoney(session.projected_cost_nis, 'ILS')}
          {payload.ledger_entry ? ' · ledger pending' : ''}
        </p>
      ) : invitees.length ? (
        <div className="action-card__attendance-actions">
          <button
            type="button"
            className="action-card__btn action-card__btn--approve"
            onClick={handleSchedule}
          >
            Schedule training day
          </button>
          <WhatsAppLink
            message={[
              'BASALT onsite proctor training day',
              `Workers: ${invitees.join(', ')}`,
              `Skills gap: ${roi.certification_gap_count ?? invitees.length}`,
              session?.scheduled_date ? `Date: ${session.scheduled_date}` : 'Schedule pending',
            ].join('\n')}
          >
            Notify crew via WhatsApp
          </WhatsAppLink>
        </div>
      ) : null}
    </article>
  )
}

function BasaltWebApplicationCard({ payload, onBasaltAction, onCertCapture }) {
  const app = payload.application || (payload.applications || [])[0]
  if (!app) {
    return (
      <article className="action-card action-card--basalt">
        <header className="action-card__header">
          <h3>Basalt web application</h3>
          <span className="action-card__badge">Inbox clear</span>
        </header>
        <p className="action-card__match action-card__match--ok">No pending basalt.co.il applications.</p>
      </article>
    )
  }

  const skills = app.skill_matrix || []

  return (
    <article className="action-card action-card--basalt action-card--alert">
      <header className="action-card__header">
        <h3>Basalt web application</h3>
        <span className="action-card__badge action-card__badge--alert">{app.status || 'PENDING_REVIEW'}</span>
      </header>
      <dl className="action-card__fields">
        <div><dt>Candidate</dt><dd>{app.candidate_name}</dd></div>
        <div><dt>Role</dt><dd>{app.desired_role || '—'}</dd></div>
        <div><dt>Source</dt><dd>{app.source || 'www.basalt.co.il'}</dd></div>
        <div><dt>Certs</dt><dd>{(app.certifications || []).length} uploaded</dd></div>
      </dl>
      {skills.length ? (
        <ul className="action-card__skill-tags">
          {skills.map((s) => (
            <li key={s.role} className="action-card__skill-chip">
              {s.role}
              <span className="action-card__skill-chip-src">{s.confidence || s.source}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="action-card__attendance-actions action-card__attendance-actions--stack">
        <CameraCaptureInput
          mode="certification"
          className="hw-capture-wrap--card"
          triggerClassName="action-card__btn action-card__btn--edit"
          onFile={(file) =>
            onCertCapture?.({ application_id: app.id, file, candidate_name: app.candidate_name })
          }
        >
          Capture certification
        </CameraCaptureInput>
        <button
          type="button"
          className="action-card__btn action-card__btn--approve"
          onClick={() => onBasaltAction?.({ action: 'approve_onboard', application_id: app.id })}
        >
          Approve &amp; onboard
        </button>
        <button
          type="button"
          className="action-card__btn action-card__btn--edit"
          onClick={() => onBasaltAction?.({ action: 'schedule_training', application_id: app.id })}
        >
          Schedule training day
        </button>
        {app.phone ? (
          <WhatsAppLink
            phone={app.phone}
            message={`BASALT application review — ${app.candidate_name}, role: ${app.desired_role || 'TBD'}. Status: ${app.status}.`}
          >
            Contact candidate
          </WhatsAppLink>
        ) : null}
      </div>
    </article>
  )
}

function CustomerInvoiceCard({ payload }) {
  return (
    <article className="action-card action-card--invoice">
      <header className="action-card__header">
        <h3>Customer invoice issued</h3>
        {payload.saved_to_ledger ? (
          <span className="action-card__badge action-card__badge--saved">Saved to Ledger</span>
        ) : null}
      </header>
      <dl className="action-card__fields">
        <div><dt>Milestone</dt><dd>{payload.milestone}</dd></div>
        <div><dt>Amount</dt><dd>{formatMoney(payload.amount, payload.currency)}</dd></div>
        <div><dt>Status</dt><dd>{payload.status || 'pending'}</dd></div>
      </dl>
    </article>
  )
}

export function ActionCard({
  cardType,
  payload,
  previewUrl,
  onAttendanceAction,
  onProcurementAction,
  onTrainingAction,
  onBasaltAction,
  onCertCapture,
}) {
  if (!cardType || !payload) return null
  switch (cardType) {
    case 'quotation_deliberation':
      return <QuotationDeliberationCard payload={payload} />
    case 'receipt_capture':
    case 'credit_memo':
      return <ReceiptCreditCard payload={payload} previewUrl={previewUrl} />
    case 'cash_flow_forecast':
      return <CashFlowForecastCard payload={payload} />
    case 'lifecycle_overview':
      return <LifecycleOverviewCard payload={payload} />
    case 'customer_invoice':
      return <CustomerInvoiceCard payload={payload} />
    case 'government_intelligence':
      return <GovernmentIntelligenceCard payload={payload} />
    case 'compliance_insurance':
      return <ComplianceInsuranceCard payload={payload} />
    case 'next_day_briefing':
      return <NextDayBriefingCard payload={payload} />
    case 'daily_attendance_approval':
    case 'daily_attendance_delay':
      return (
        <DailyAttendanceDelayCard payload={payload} onAttendanceAction={onAttendanceAction} />
      )
    case 'basalt_web_application':
      return (
        <BasaltWebApplicationCard
          payload={payload}
          onBasaltAction={onBasaltAction}
          onCertCapture={onCertCapture}
        />
      )
    case 'cost_engineering_bid_tabulation':
      return (
        <CostEngineeringBidTabulationCard payload={payload} onProcurementAction={onProcurementAction} />
      )
    case 'upskilling_strategy':
      return <UpskillingStrategyCard payload={payload} />
    case 'onsite_proctor_session':
      return (
        <OnsiteProctorSessionCard payload={payload} onTrainingAction={onTrainingAction} />
      )
    default:
      return (
        <article className="action-card">
          <header className="action-card__header">
            <h3>{cardType}</h3>
          </header>
          <pre className="action-card__raw">{JSON.stringify(payload, null, 2)}</pre>
        </article>
      )
  }
}
