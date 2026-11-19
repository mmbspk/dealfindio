import React from 'react'
import { Link } from 'react-router-dom'

export const PrivacyPolicy: React.FC = () => (
  <div className="max-w-2xl mx-auto px-5 sm:px-8 py-16">
    <Link to="/" className="text-[13px] text-emerald-600 hover:underline mb-8 block">← Back to home</Link>
    <h1 className="text-3xl font-extrabold text-gray-900 mb-6">Privacy Policy</h1>
    <div className="prose prose-gray text-[15px] leading-relaxed space-y-5 text-gray-600">
      <p>
        dealfindio (&quot;we&quot;, &quot;us&quot;, &quot;our&quot;) is committed to protecting your privacy.
        This Privacy Policy explains how we collect, use, and safeguard your information
        when you visit dealfindio.com.au.
      </p>
      <p>
        <strong>Information we collect:</strong> When you submit your email address via our
        waitlist form, we store that address to send you product updates and launch notifications.
        We do not sell or share your email with third parties.
      </p>
      <p>
        <strong>Cookies:</strong> We may use essential cookies to operate the site.
        Analytics cookies are not currently enabled.
      </p>
      <p>
        <strong>Contact:</strong> Questions about this policy? Email us at{' '}
        <a href="mailto:privacy@dealfindio.com.au" className="text-emerald-600 hover:underline">
          privacy@dealfindio.com.au
        </a>.
      </p>
      <p className="text-[13px] text-gray-400">Last updated November 2026. This is a placeholder policy — a full legal document will be published at launch.</p>
    </div>
  </div>
)
