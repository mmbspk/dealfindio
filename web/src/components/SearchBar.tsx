import React from 'react'

interface SearchBarProps {
  value: string
  onChange: (value: string) => void
  onSearch: (e: React.FormEvent) => void
}

export const SearchBar: React.FC<SearchBarProps> = ({ value, onChange, onSearch }) => {
  return (
    <form onSubmit={onSearch} className="w-full">
      <div className="relative">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Search for products... (e.g., milk, bread)"
          className="w-full pl-5 pr-28 py-4 text-base sm:text-lg border-2 border-gray-300 rounded-lg focus:outline-none focus:border-emerald-500 shadow-lg transition"
          aria-label="Product search input"
        />
        <button
          type="submit"
          className="absolute right-2 top-1/2 -translate-y-1/2 bg-amber-500 text-gray-900 px-6 py-2 rounded-lg hover:bg-amber-600 active:bg-amber-700 transition font-semibold"
          aria-label="Submit search"
        >
          Search
        </button>
      </div>
    </form>
  )
}
