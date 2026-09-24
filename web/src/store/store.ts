import { create } from 'zustand'
import { Product } from '../types'

interface SearchStore {
  query: string
  results: Product[]
  loading: boolean
  error: string
  setQuery: (query: string) => void
  setResults: (results: Product[]) => void
  setLoading: (loading: boolean) => void
  setError: (error: string) => void
  clearResults: () => void
}

export const useStore = create<SearchStore>((set) => ({
  query: '',
  results: [],
  loading: false,
  error: '',
  setQuery: (query) => set({ query }),
  setResults: (results) => set({ results }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  clearResults: () => set({ results: [], query: '', error: '' }),
}))
