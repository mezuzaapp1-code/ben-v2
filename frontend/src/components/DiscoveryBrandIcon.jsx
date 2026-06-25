import PropTypes from 'prop-types'
import {
  Cloud,
  HardDrive,
  LayoutGrid,
  Library,
  Mail,
  Radar,
  ScanLine,
  Shield,
  Sparkles,
} from 'lucide-react'

const ICON_SIZE = 20

/** @typedef {'grok' | 'claude' | 'gemini' | 'gmail' | 'gdrive' | 'local' | 'library' | 'sonar'} BrandId */

/** @param {{ brandId: BrandId, sonarVariant?: string, active?: boolean }} props */
export function DiscoveryBrandIcon({ brandId, sonarVariant = 'radar', active = false }) {
  const common = {
    size: ICON_SIZE,
    strokeWidth: 2,
    'aria-hidden': true,
  }

  if (brandId === 'grok') {
    return <LayoutGrid {...common} className="text-indigo-200" />
  }
  if (brandId === 'claude') {
    return <Sparkles {...common} className="text-[#f0c4b0]" />
  }
  if (brandId === 'gemini') {
    return <ScanLine {...common} className="text-sky-200" />
  }
  if (brandId === 'gmail') {
    return <Mail {...common} className="text-white" />
  }
  if (brandId === 'gdrive') {
    return <Cloud {...common} className="text-white" />
  }
  if (brandId === 'local') {
    return <HardDrive {...common} className="text-slate-200" />
  }
  if (brandId === 'library') {
    return <Library {...common} className="text-indigo-200" />
  }
  if (brandId === 'sonar') {
    if (sonarVariant === 'shield') return <Shield {...common} className="text-emerald-200" />
    if (sonarVariant === 'scan') return <ScanLine {...common} className="text-emerald-200" />
    return <Radar {...common} className={`text-emerald-200${active ? ' discovery-sonar-spin' : ''}`} />
  }
  return <Library {...common} />
}

DiscoveryBrandIcon.propTypes = {
  brandId: PropTypes.oneOf(['grok', 'claude', 'gemini', 'gmail', 'gdrive', 'local', 'library', 'sonar']).isRequired,
  sonarVariant: PropTypes.oneOf(['radar', 'shield', 'scan']),
  active: PropTypes.bool,
}

DiscoveryBrandIcon.defaultProps = {
  sonarVariant: 'radar',
  active: false,
}
