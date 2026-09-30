import React from 'react'
import { Link } from 'react-router-dom'

const SOCIAL = [
  {
    label: 'Facebook',
    href: 'https://facebook.com/dealfindio',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
        <path d="M22 12c0-5.523-4.477-10-10-10S2 6.477 2 12c0 4.991 3.657 9.128 8.438 9.878v-6.987h-2.54V12h2.54V9.797c0-2.506 1.492-3.89 3.777-3.89 1.094 0 2.238.195 2.238.195v2.46h-1.26c-1.243 0-1.63.771-1.63 1.562V12h2.773l-.443 2.89h-2.33v6.988C18.343 21.128 22 16.991 22 12z" />
      </svg>
    ),
  },
  {
    label: 'Instagram',
    href: 'https://instagram.com/dealfindio',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
        <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838a6.162 6.162 0 100 12.324 6.162 6.162 0 000-12.324zM12 16a4 4 0 110-8 4 4 0 010 8zm6.406-11.845a1.44 1.44 0 100 2.881 1.44 1.44 0 000-2.881z" />
      </svg>
    ),
  },
  {
    label: 'X / Twitter',
    href: 'https://x.com/dealfindio',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
        <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
      </svg>
    ),
  },
  {
    label: 'TikTok',
    href: 'https://tiktok.com/@dealfindio',
    icon: (
      <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
        <path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1V9.01a6.33 6.33 0 00-.79-.05 6.34 6.34 0 00-6.34 6.34 6.34 6.34 0 006.34 6.34 6.34 6.34 0 006.33-6.34V8.69a8.18 8.18 0 004.78 1.52V6.75a4.85 4.85 0 01-1.01-.06z" />
      </svg>
    ),
  },
]

const NAV = [
  {
    heading: 'Compare',
    links: [
      { label: 'All Deals', to: '/deals' },
      { label: 'Products', to: '/products' },
      { label: 'Stores Near Me', to: '/stores' },
    ],
  },
  {
    heading: 'Account',
    links: [
      { label: 'Watchlist', to: '/watchlist' },
      { label: 'Notifications', to: '/notifications' },
      { label: 'Profile', to: '/profile' },
    ],
  },
  {
    heading: 'Retailers',
    links: [
      { label: 'Coles', to: '/deals?chain=coles' },
      { label: 'Woolworths', to: '/deals?chain=woolworths' },
      { label: 'ALDI', to: '/deals?chain=aldi' },
      { label: 'IGA', to: '/deals?chain=iga' },
      { label: 'Drakes', to: '/deals?chain=drakes' },
    ],
  },
]

export const Footer: React.FC = () => (
  <footer className="bg-[#0B1120] border-t border-slate-800/60">
    <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10">

      {/* ── Main grid ── */}
      <div className="grid grid-cols-2 gap-x-8 gap-y-10 py-14 sm:grid-cols-4 lg:grid-cols-[2.2fr_1fr_1fr_1fr]">

        {/* Brand column */}
        <div className="col-span-2 sm:col-span-4 lg:col-span-1 flex flex-col gap-5">
          <div>
            <div className="flex items-center gap-2 mb-3">
              <svg className="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="currentColor">
                <path d="M17 8C8 10 5.9 16.17 3.82 19.14L5.71 21l1-1c.5.5 1 1 1.71 1C13 21 20 15 21 7c-1.5.5-3 .5-4 1z" />
              </svg>
              <span className="text-[15px] font-bold text-white tracking-tight">dealfindio</span>
            </div>
            <p className="text-[13px] leading-[1.7] text-slate-400 max-w-[210px]">
              Compare grocery prices across Australia's major supermarkets and find the best deal near you.
            </p>
          </div>

          {/* Social icons */}
          <div className="flex items-center gap-2">
            {SOCIAL.map(({ label, href, icon }) => (
              <a
                key={label}
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={label}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition-all duration-150 hover:bg-slate-800 hover:text-white"
              >
                {icon}
              </a>
            ))}
          </div>
        </div>

        {/* Nav columns */}
        {NAV.map(({ heading, links }) => (
          <div key={heading}>
            <p className="mb-4 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              {heading}
            </p>
            <ul className="space-y-3">
              {links.map(({ label, to }) => (
                <li key={to}>
                  <Link
                    to={to}
                    className="text-[13px] text-slate-400 transition-colors duration-150 hover:text-emerald-400"
                  >
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}

      </div>

      {/* ── Disclaimer ── */}
      <div className="border-t border-slate-800/70 py-6">
        <p className="text-[11.5px] leading-[1.7] text-slate-600 max-w-[900px]">
          dealfindio is an independent price comparison service and is not affiliated with, endorsed by, or
          sponsored by any retailer shown on this site. Retailer names and trademarks are the property of
          their respective owners. Prices, promotions and availability may change and can vary by location —
          please verify with the retailer before purchasing.
        </p>
      </div>

      {/* ── Bottom bar ── */}
      <div className="border-t border-slate-800/70 py-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <span className="text-[12px] text-slate-600">
          © {new Date().getFullYear()} dealfindio. All rights reserved.
        </span>
        <div className="flex items-center gap-5">
          <a href="/privacy" className="text-[12px] text-slate-500 transition-colors duration-150 hover:text-slate-300">
            Privacy Policy
          </a>
          <a href="/terms" className="text-[12px] text-slate-500 transition-colors duration-150 hover:text-slate-300">
            Terms of Use
          </a>
          <a href="/cookies" className="text-[12px] text-slate-500 transition-colors duration-150 hover:text-slate-300">
            Cookies
          </a>
        </div>
      </div>

    </div>
  </footer>
)
