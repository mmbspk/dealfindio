/** Normalize package_size strings from all scrapers into clean display labels.
 *
 * Handles legacy data still in the DB from before scraper normalization:
 *   "44 each" / "44EA"  → "44 Pack"
 *   "1 each" / "1EA"    → null  (single-unit marker, not informative)
 *   "330millilitre"     → "330 mL"
 *   "150gram"           → "150 g"
 *   "44 Pack" / "500g"  → unchanged (already clean)
 */
const UNIT_ABBREV: Record<string, string> = {
  millilitre: 'mL', milliliter: 'mL',
  litre: 'L', liter: 'L',
  gram: 'g', grams: 'g',
  kilogram: 'kg', kilograms: 'kg',
  ounce: 'oz', ounces: 'oz',
}

export function formatPackageSize(raw: string | null | undefined): string | null {
  if (!raw) return null
  const s = raw.trim()
  if (!s) return null

  // "N each/ea/pk/pack" patterns
  const countMatch = s.match(/^(\d+)\s*(ea|each|pk|pack)$/i)
  if (countMatch) {
    const n = parseInt(countMatch[1], 10)
    return n > 1 ? `${n} Pack` : null
  }

  // Run-together metric units: "330millilitre" → "330 mL"
  const metricMatch = s.match(/^([\d.]+)([a-zA-Z]+)$/)
  if (metricMatch) {
    const abbrev = UNIT_ABBREV[metricMatch[2].toLowerCase()]
    if (abbrev) return `${metricMatch[1]} ${abbrev}`
  }

  return s
}
