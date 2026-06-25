function formatAmount(amount, currency) {
  const cur = (currency || 'USD').toUpperCase()
  const val = Number(amount)
  if (!Number.isFinite(val)) return `— ${cur}`
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(val)
  } catch {
    return `${val.toFixed(2)} ${cur}`
  }
}

function vendorMatchLabel(match) {
  if (!match) return { text: 'Vendor unmatched', tone: 'warn' }
  if (match.match_status === 'exact') {
    return { text: `Matched: ${match.member_name}`, tone: 'ok' }
  }
  if (match.match_status === 'partial') {
    return { text: `Partial match: ${match.member_name}`, tone: 'partial' }
  }
  return { text: 'Vendor unmatched', tone: 'warn' }
}

export function FinancialReceiptCard({ receipt, previewUrl }) {
  if (!receipt) return null

  const vendor = receipt.vendor || 'Unknown vendor'
  const match = vendorMatchLabel(receipt.vendor_match)
  const saved = receipt.saved_to_ledger !== false

  return (
    <article className="financial-receipt-card" aria-label="Financial receipt">
      <div className="financial-receipt-card__header">
        <span className="financial-receipt-card__title">Invoice captured</span>
        {saved ? (
          <span className="financial-receipt-card__badge financial-receipt-card__badge--saved">
            Saved to Ledger
          </span>
        ) : null}
      </div>
      {previewUrl ? (
        <div className="financial-receipt-card__preview">
          <img src={previewUrl} alt="Invoice preview" loading="lazy" />
        </div>
      ) : null}
      <dl className="financial-receipt-card__fields">
        <div className="financial-receipt-card__field">
          <dt>Amount</dt>
          <dd>{formatAmount(receipt.amount, receipt.currency)}</dd>
        </div>
        <div className="financial-receipt-card__field">
          <dt>Vendor</dt>
          <dd>{vendor}</dd>
        </div>
        <div className="financial-receipt-card__field">
          <dt>Vendor match</dt>
          <dd>
            <span className={`financial-receipt-card__match financial-receipt-card__match--${match.tone}`}>
              {match.text}
            </span>
          </dd>
        </div>
      </dl>
      {receipt.ledger_entry?.id ? (
        <p className="financial-receipt-card__ledger-id">
          Ledger entry {receipt.ledger_entry.id.slice(0, 8)}…
        </p>
      ) : null}
    </article>
  )
}
