import React from 'react'
import { Link } from 'react-router-dom'

export const DemoComingSoon: React.FC = () => (
  <div className="min-h-screen bg-gradient-to-br from-emerald-50 via-white to-amber-50 flex items-center justify-center px-5">
    <div className="text-center max-w-md">
      <div className="inline-flex w-16 h-16 rounded-2xl bg-emerald-100 items-center justify-center text-emerald-600 mb-6">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-8 h-8">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      </div>
      <h1 className="text-3xl font-extrabold text-gray-900 mb-3">Demo coming soon</h1>
      <p className="text-gray-500 text-[15px] leading-relaxed mb-8">
        We're putting the finishing touches on the interactive walkthrough.
        Drop your email below and you'll be first to try it.
      </p>
      <Link
        to="/#notify"
        className="inline-flex items-center gap-2 bg-emerald-500 hover:bg-emerald-600 text-white font-bold px-7 py-4 rounded-xl transition-colors"
      >
        Notify me at launch
        <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
          <path fillRule="evenodd" d="M3 10a.75.75 0 01.75-.75h10.638L10.23 5.29a.75.75 0 111.04-1.08l5.5 5.25a.75.75 0 010 1.08l-5.5 5.25a.75.75 0 11-1.04-1.08l4.158-3.96H3.75A.75.75 0 013 10z" clipRule="evenodd" />
        </svg>
      </Link>
      <p className="mt-6">
        <Link to="/" className="text-[13px] text-gray-400 hover:text-emerald-600 transition-colors">
          ← Back to home
        </Link>
      </p>
    </div>
  </div>
)
