/** Localized cognitive UI labels (mirrors services/language_context._labels). */

const LABELS = {
  en: {
    expert_unavailable: 'Expert unavailable ({category})',
    category_timeout: 'timeout',
    status_timeout: 'Unavailable (timeout)',
    status_degraded: 'Partial: {category}',
    status_error: 'Partial: error',
    synthesis_prefix_degraded: 'Based on available expert responses.',
    synthesis_title: 'BEN Synthesis ({ae})',
    consensus: 'Consensus',
    disagreement: 'Disagreement',
    disagreement_none: 'None',
    synthesis_footer: 'This is a structured reasoning layer, not a final answer.',
    council_started: 'Council started…',
    council_experts: 'Waiting for Legal, Business, and Strategy…',
    council_synthesizing: 'Synthesizing…',
    council_timeout: 'Council timed out. You can retry.',
    council_network: 'Network error. Check your connection and try again.',
    council_failed: 'Council failed unexpectedly. You can retry.',
    council_empty: 'Council returned no responses. You can retry.',
    section_shared_recommendation: 'Shared recommendation',
    section_disagreement_points: 'Disagreement & rationale',
    section_legal_reasoning: 'Legal reasoning',
    section_operational_reasoning: 'Operational reasoning',
    section_strategic_reasoning: 'Strategic reasoning',
    section_infrastructure_reasoning: 'Infrastructure reasoning',
    section_minority_or_unique_views: 'Minority or unique views',
  },
  he: {
    expert_unavailable: 'המומחה אינו זמין ({category})',
    category_timeout: 'פסק זמן',
    status_timeout: 'לא זמין (פסק זמן)',
    status_degraded: 'חלקי: {category}',
    status_error: 'חלקי: שגיאה',
    synthesis_prefix_degraded: 'על בסיס תגובות המומחים הזמינות.',
    synthesis_title: 'סינתזת BEN ({ae})',
    consensus: 'קונצנזוס',
    disagreement: 'מחלוקת',
    disagreement_none: 'אין',
    synthesis_footer: 'שכבת נימוק מובנית — לא תשובה סופית.',
    council_started: 'המועצה החלה…',
    council_experts: 'ממתין לייעוץ משפטי, עסקי ואסטרטגי…',
    council_synthesizing: 'מסנתז…',
    council_timeout: 'פסק הזמן של המועצה הסתיים. ניתן לנסות שוב.',
    council_network: 'שגיאת רשת. בדוק את החיבור ונסה שוב.',
    council_failed: 'המועצה נכשלה. ניתן לנסות שוב.',
    council_empty: 'המועצה לא החזירה תגובות. ניתן לנסות שוב.',
    section_shared_recommendation: 'המלצה משותפת',
    section_disagreement_points: 'מחלוקת ונימוק',
    section_legal_reasoning: 'נימוק משפטי',
    section_operational_reasoning: 'נימוק תפעולי',
    section_strategic_reasoning: 'נימוק אסטרטגי',
    section_infrastructure_reasoning: 'נימוק תשתית',
    section_minority_or_unique_views: 'דעות מיעוט או ייחודיות',
  },
  ar: {
    expert_unavailable: 'الخبير غير متاح ({category})',
    category_timeout: 'انتهاء المهلة',
    status_timeout: 'غير متاح (انتهاء المهلة)',
    status_degraded: 'جزئي: {category}',
    status_error: 'جزئي: خطأ',
    synthesis_prefix_degraded: 'بناءً على آراء الخبراء المتاحة.',
    synthesis_title: 'تركيب BEN ({ae})',
    consensus: 'الإجماع',
    disagreement: 'الخلاف',
    disagreement_none: 'لا يوجد',
    synthesis_footer: 'طبقة استدلال منظمة — وليست إجابة نهائية.',
    council_started: 'بدأ المجلس…',
    council_experts: 'في انتظار الخبراء القانوني والتجاري والاستراتيجي…',
    council_synthesizing: 'جاري التركيب…',
    council_timeout: 'انتهت مهلة المجلس. يمكنك إعادة المحاولة.',
    council_network: 'خطأ في الشبكة. تحقق من الاتصال وحاول مرة أخرى.',
    council_failed: 'فشل المجلس. يمكنك إعادة المحاولة.',
    council_empty: 'لم يُرجع المجلس ردوداً. يمكنك إعادة المحاولة.',
    section_shared_recommendation: 'توصية مشتركة',
    section_disagreement_points: 'الخلاف والمبرر',
    section_legal_reasoning: 'الاستدلال القانوني',
    section_operational_reasoning: 'الاستدلال التشغيلي',
    section_strategic_reasoning: 'الاستدلال الاستراتيجي',
    section_infrastructure_reasoning: 'استدلال البنية التحتية',
    section_minority_or_unique_views: 'آراء الأقلية أو الفريدة',
  },
}

export function labelLocale(dominantLanguage) {
  if (dominantLanguage === 'he' || dominantLanguage === 'ar') return dominantLanguage
  return 'en'
}

export function t(locale, key, vars = {}) {
  const table = LABELS[locale] || LABELS.en
  let s = table[key] || LABELS.en[key] || key
  for (const [k, v] of Object.entries(vars)) {
    s = s.replace(`{${k}}`, String(v))
  }
  return s
}

export function expertStatusLabel(locale, outcome, response) {
  if (!outcome || outcome === 'ok') return null
  if (outcome === 'timeout') return t(locale, 'status_timeout')
  const m =
    /Expert unavailable \(([^)]+)\)/.exec(response || '') ||
    /המומחה אינו זמין \(([^)]+)\)/.exec(response || '') ||
    /الخبير غير متاح \(([^)]+)\)/.exec(response || '')
  if (m) return t(locale, 'status_degraded', { category: m[1] })
  if (outcome === 'error') return t(locale, 'status_error')
  return t(locale, 'status_degraded', { category: outcome })
}

export function councilPhaseTimers(locale) {
  return [
    { at: 0, phase: 'started', message: t(locale, 'council_started') },
    { at: 300, phase: 'experts', message: t(locale, 'council_experts') },
    { at: 12_000, phase: 'synthesizing', message: t(locale, 'council_synthesizing') },
  ]
}

const SECTION_KEYS = [
  ['shared_recommendation', 'section_shared_recommendation'],
  ['disagreement_points', 'section_disagreement_points'],
  ['legal_reasoning', 'section_legal_reasoning'],
  ['operational_reasoning', 'section_operational_reasoning'],
  ['strategic_reasoning', 'section_strategic_reasoning'],
  ['infrastructure_reasoning', 'section_infrastructure_reasoning'],
  ['minority_or_unique_views', 'section_minority_or_unique_views'],
]

export function synthesisSectionLabels(locale) {
  return SECTION_KEYS.map(([key, labelKey]) => [key, t(locale, labelKey)])
}

export function buildSynthesisBubbleText(s, anyExpertFailed, locale) {
  const disagree =
    s.main_disagreement != null && String(s.main_disagreement).trim() !== ''
      ? String(s.main_disagreement)
      : t(locale, 'disagreement_none')
  const ae = s.agreement_estimate ?? 'unknown'
  const rec = s.recommendation ?? ''
  const cons = s.consensus_points ?? ''
  const prefix = anyExpertFailed ? `${t(locale, 'synthesis_prefix_degraded')}\n\n` : ''
  return `${prefix}🧠 ${t(locale, 'synthesis_title', { ae })}
${rec}

✅ ${t(locale, 'consensus')}: ${cons}
⚡ ${t(locale, 'disagreement')}: ${disagree}

${t(locale, 'synthesis_footer')}`
}
