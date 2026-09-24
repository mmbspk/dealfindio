# dealfindio Web - React Frontend

Production-grade React web application for finding and comparing local grocery deals.

## Features

- **Search Page**: Intuitive product search with real-time filtering (Task #16)
- **Price History Chart**: 90-day price trend visualization with multi-chain comparison using Recharts (Task #17)
- **Responsive Design**: Mobile-first approach with Tailwind CSS
- **Type-Safe**: Full TypeScript implementation with strict mode
- **State Management**: Zustand for client-side state
- **API Integration**: Axios with auth interceptors and error handling

## Technology Stack

- **React 18** - UI framework
- **Vite** - Build tool and dev server
- **TypeScript** - Type-safe development
- **Tailwind CSS** - Utility-first styling
- **Recharts** - Data visualization (price charts)
- **React Router** - Client-side routing
- **Zustand** - Lightweight state management
- **Axios** - HTTP client with interceptors

## Project Structure

```
web/
├── src/
│   ├── pages/
│   │   ├── Home.tsx          # Landing page with features
│   │   ├── Search.tsx        # Product search page (Task #16)
│   │   ├── Product.tsx       # Product detail with price chart (Task #17)
│   │   └── Profile.tsx       # User profile (stub for future)
│   ├── components/
│   │   ├── Header.tsx        # Navigation header
│   │   ├── Footer.tsx        # Footer with links
│   │   ├── SearchBar.tsx     # Search input component
│   │   ├── ProductCard.tsx   # Product card component
│   │   ├── PriceChart.tsx    # Recharts price history visualization
│   │   └── StoresList.tsx    # Store availability list
│   ├── api/
│   │   └── client.ts         # Axios instance with auth interceptors
│   ├── store/
│   │   └── store.ts          # Zustand global state
│   ├── types/
│   │   └── index.ts          # TypeScript interfaces
│   ├── App.tsx               # Main app with routing
│   ├── main.tsx              # React entry point
│   ├── App.css               # Global styles
│   └── index.css             # Tailwind and custom utilities
├── public/
│   └── favicon.svg           # App icon
├── package.json              # Dependencies and scripts
├── vite.config.ts            # Vite configuration
├── tsconfig.json             # TypeScript configuration
├── tailwind.config.js        # Tailwind CSS config
├── postcss.config.js         # PostCSS config
├── .eslintrc.cjs             # ESLint rules
├── .prettierrc                # Code formatting rules
├── .env.example              # Environment variables template
├── index.html                # HTML entry point
└── README.md                 # This file
```

## Setup & Installation

```bash
# Install dependencies
npm install

# Set up environment
cp .env.example .env
# Edit .env to set VITE_API_URL if needed (default: http://localhost:8000)

# Start development server
npm run dev
```

Development server runs at `http://localhost:5173`

## Scripts

```bash
# Development
npm run dev              # Start dev server with hot reload

# Building
npm run build            # Build for production (TSC + Vite)
npm run preview          # Preview production build locally

# Code Quality
npm run lint             # Run ESLint
npm run type-check       # Type-check with TypeScript
npm run format           # Format code with Prettier
```

## Task #16: Search Page

Located at `/search` route.

**Features:**
- Real-time product search with 50 result limit
- Product cards with name, brand, and category
- Loading states with spinner animation
- Error handling with user-friendly messages
- Empty state when no results found
- Responsive grid layout (1-4 columns)
- Accessibility: ARIA labels and semantic HTML

**Components:**
- `Search.tsx` - Main search page component
- `SearchBar.tsx` - Input and submit form
- `ProductCard.tsx` - Individual product card with link to detail

**API Integration:**
```typescript
GET /api/v1/products/search?q={query}&limit=50
```

## Task #17: Price History Chart

Located at `/product/{productId}` route.

**Features:**
- 90-day price history visualization with Recharts
- Multi-chain line chart with different colors
- Price statistics grid (average, min, max per store)
- Responsive chart that adapts to screen size
- Store availability list
- Product details display

**Components:**
- `Product.tsx` - Main product detail page
- `PriceChart.tsx` - Recharts implementation with:
  - Line chart with multiple data series
  - Custom tooltips with formatted prices
  - Date formatting for AU locale
  - Color-coded chains for clarity
  - Statistics cards showing avg/min/max prices

**API Endpoints:**
```typescript
GET /api/v1/products/{productId}        # Product details
GET /api/v1/products/{productId}/prices?days=90  # Price history
GET /api/v1/products/{productId}/stores # Store availability
```

## API Client

Located at `src/api/client.ts`

**Features:**
- Base Axios instance with configurable URL
- Auth token interceptor (reads from localStorage)
- Response interceptor with 401 handling
- Automatic redirect to login on unauthorized

**Usage:**
```typescript
import { apiClient } from '@/api/client'

const response = await apiClient.get('/api/v1/products/search', {
  params: { q: 'milk', limit: 50 }
})
```

## State Management

Uses Zustand for lightweight global state. Located at `src/store/store.ts`

**Store Structure:**
```typescript
useStore = {
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
```

**Usage:**
```typescript
import { useStore } from '@/store/store'

const { query, setQuery, results } = useStore()
```

## Styling

**Tailwind CSS:**
- Custom color palette (Indigo-based)
- Responsive breakpoints (mobile-first)
- Utility classes for common patterns

**Custom Utilities:**
```css
.btn-primary    /* Indigo button */
.btn-secondary  /* Gray button */
.btn-outline    /* Outline button */
.card          /* White card component */
.input-base    /* Base form input styles */
```

**CSS Animations:**
- `animate-spin-slow` - 3s spin animation
- `animate-pulse-ring` - Pulse ring effect
- `text-gradient` - Gradient text effect

## Build & Deployment

### Development Build
```bash
npm run build   # Creates dist/ folder
npm run preview # Serve dist locally
```

### Production Deployment

**Vercel (recommended):**
1. Connect repository to Vercel
2. Set environment variable: `VITE_API_URL`
3. Push to main branch - automatic deployment

**Docker (optional):**
```dockerfile
FROM node:18
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "preview"]
```

## Environment Variables

Create `.env` file (copy from `.env.example`):

```env
# API Configuration
VITE_API_URL=http://localhost:8000

# Environment
VITE_ENV=development
```

## Performance Optimizations

- Code splitting with React.lazy() for routes
- Memoization with useMemo for expensive calculations
- Debounced search inputs
- Optimized bundle size with tree-shaking
- CSS purging with Tailwind
- Image optimization (WebP format recommended)

## Accessibility

- ARIA labels on interactive elements
- Semantic HTML (header, nav, main, footer, article, section)
- Color contrast meets WCAG AA standards
- Keyboard navigation support
- Focus management with visible focus styles
- Screen reader friendly

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Mobile browsers (iOS Safari 14+, Chrome Mobile)

## Development Guidelines

1. **Component Structure**: Use functional components with hooks
2. **Type Safety**: Never use `any`, use proper TypeScript interfaces
3. **Naming**: PascalCase for components, camelCase for functions/variables
4. **File Organization**: Group related files in folders
5. **Testing**: Components should be easily testable (decoupled from API)
6. **Code Style**: Use Prettier + ESLint before committing

## Common Tasks

### Adding a New Page
```typescript
// 1. Create component in src/pages/NewPage.tsx
export const NewPage: React.FC = () => {
  return <div>New Page</div>
}

// 2. Add route in src/App.tsx
<Route path="/new-page" element={<NewPage />} />
```

### Adding a New Component
```typescript
// src/components/NewComponent.tsx
interface NewComponentProps {
  title: string
  onClick: () => void
}

export const NewComponent: React.FC<NewComponentProps> = ({ title, onClick }) => {
  return <button onClick={onClick}>{title}</button>
}
```

### Making an API Call
```typescript
const response = await apiClient.get('/api/v1/endpoint', {
  params: { key: 'value' }
})
const data = response.data
```

## Troubleshooting

**Port 5173 already in use:**
```bash
npm run dev -- --port 3000
```

**API calls failing:**
- Check `VITE_API_URL` environment variable
- Ensure backend is running
- Check browser console for CORS errors

**Build fails with TypeScript errors:**
```bash
npm run type-check  # See detailed errors
```

**Styling not working:**
```bash
# Rebuild Tailwind cache
rm -rf node_modules/.vite
npm run build
```

## Contributing

1. Create feature branch from main
2. Make changes following code guidelines
3. Run `npm run lint` and `npm run type-check`
4. Create pull request
5. After merge, automatic deployment to Vercel

## License

MIT
