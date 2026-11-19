import React from 'react'
import { Link } from 'react-router-dom'

export const TermsOfUse: React.FC = () => (
  <div className="max-w-2xl mx-auto px-5 sm:px-8 py-16">
    <Link to="/" className="text-[13px] text-emerald-600 hover:underline mb-8 block">← Back to home</Link>
    <h1 className="text-3xl font-extrabold text-gray-900 mb-6">Terms of Use</h1>
    <div className="prose prose-gray text-[15px] leading-relaxed space-y-5 text-gray-600">
      <p>
        By accessing dealfindio.com.au you agree to these Terms of Use. dealfindio is an
        independent price comparison service. We are not affiliated with, endorsed by, or
        sponsored by any retailer shown on this site.
      </p>
      <p>
        <strong>Accuracy:</strong> Prices, promotions and availability are sourced from
        public retailer data and may differ from in-store prices. Always verify with the
        retailer before purchasing.
      </p>
      <p>
        <strong>Intellectual property:</strong> Retailer names and trademarks are the
        property of their respective owners.
      </p>
      <p>
        <strong>Contact:</strong> Questions? Email us at{' '}
        <a href="mailto:hello@dealfindio.com.au" className="text-emerald-600 hover:underline">
          hello@dealfindio.com.au
        </a>.
      </p>
      <p className="text-[13px] text-gray-400">Last updated November 2026. This is a placeholder — full terms will be published at launch.</p>
    </div>
  </div>
)
