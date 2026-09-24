-- ============================================================================
-- dealfindio: Production PostgreSQL Schema with PostGIS
-- ============================================================================
-- This is the canonical DDL for the dealfindio database. It defines all core
-- tables for managing stores, products, price reports, user profiles, and
-- price predictions. Includes comprehensive indexing and spatial queries.
--
-- Requirements:
--   - PostgreSQL 14+
--   - PostGIS extension (for spatial queries on store locations)
--   - pg_trgm extension (for fuzzy product name search)
--   - uuid-ossp extension (for UUID generation)
--
-- Created: 2026-08-28
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================================
-- TABLE: stores
-- ============================================================================
-- Central registry of retail store locations. Includes chain affiliation,
-- address, and geographic coordinates for spatial queries (nearby stores).
--
-- Spatial queries use the PostGIS GIST index on (lat, lon) to find stores
-- within a geographic radius efficiently.
--
CREATE TABLE stores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Store identification
  chain VARCHAR(100) NOT NULL,
  name VARCHAR(255) NOT NULL,
  -- Address components
  address VARCHAR(500),
  suburb VARCHAR(100),
  state VARCHAR(10),
  postcode VARCHAR(10),
  -- Geographic coordinates (required for spatial queries)
  lat DECIMAL(10, 8) NOT NULL,
  lon DECIMAL(11, 8) NOT NULL,
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Constraints
  CONSTRAINT stores_chain_name_suburb_unique UNIQUE (chain, name, suburb),
  CONSTRAINT stores_lat_valid CHECK (lat >= -90 AND lat <= 90),
  CONSTRAINT stores_lon_valid CHECK (lon >= -180 AND lon <= 180)
) WITH (fillfactor = 90);

-- Spatial index for nearby store queries
CREATE INDEX idx_stores_location
  ON stores USING GIST (
    ll_to_earth(lat, lon)
  );

-- B-tree indexes for common lookups
CREATE INDEX idx_stores_chain ON stores (chain);
CREATE INDEX idx_stores_chain_suburb ON stores (chain, suburb);

COMMENT ON TABLE stores IS 'Central registry of retail store locations with geographic coordinates for spatial queries.';
COMMENT ON COLUMN stores.chain IS 'Grocery chain identifier (e.g., Woolworths, Coles, Aldi)';
COMMENT ON COLUMN stores.name IS 'Store display name';
COMMENT ON COLUMN stores.lat IS 'Latitude in decimal degrees (range: -90 to 90)';
COMMENT ON COLUMN stores.lon IS 'Longitude in decimal degrees (range: -180 to 180)';

-- ============================================================================
-- TABLE: products
-- ============================================================================
-- Master product catalog. Each product is uniquely identified by GTIN
-- (Global Trade Item Number). Includes fuzzy search capability on product
-- names via pg_trgm trigram index.
--
-- The trigram GIN index enables efficient approximate string matching for
-- user product searches (e.g., "bred" matches "Bread").
--
CREATE TABLE products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- GTIN is the unique product code (EAN/UPC)
  gtin VARCHAR(20) NOT NULL UNIQUE,
  -- Product details
  name VARCHAR(500) NOT NULL,
  brand VARCHAR(255),
  category VARCHAR(100),
  -- Unit information for volume-relative pricing
  unit_type VARCHAR(50), -- e.g., "mL", "g", "L", "count"
  unit_size DECIMAL(10, 2),
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT products_name_not_empty CHECK (name <> ''),
  CONSTRAINT products_unit_size_positive CHECK (unit_size IS NULL OR unit_size > 0)
) WITH (fillfactor = 90);

-- Trigram index for fuzzy product name search
CREATE INDEX idx_products_name_trgm
  ON products USING GIN (name gin_trgm_ops);

-- B-tree indexes for category and brand filtering
CREATE INDEX idx_products_category ON products (category);
CREATE INDEX idx_products_brand ON products (brand);
CREATE INDEX idx_products_gtin ON products (gtin);

COMMENT ON TABLE products IS 'Master product catalog with GTIN as unique identifier and fuzzy search capability.';
COMMENT ON COLUMN products.gtin IS 'Global Trade Item Number (EAN/UPC) - globally unique product code';
COMMENT ON COLUMN products.name IS 'Product display name (subject to trigram fuzzy search)';
COMMENT ON COLUMN products.unit_type IS 'Unit of measurement (e.g., mL, g, L, count) for volume-relative pricing';
COMMENT ON COLUMN products.unit_size IS 'Quantity of unit_type (e.g., 2L milk has unit_size=2, unit_type=L)';

-- ============================================================================
-- TABLE: user_profiles
-- ============================================================================
-- User identity and preference data. Each user is uniquely identified by
-- Firebase UID. Includes home location for nearby store queries and
-- contribution/reputation metrics.
--
CREATE TABLE user_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Firebase authentication UID (immutable)
  firebase_uid VARCHAR(255) NOT NULL UNIQUE,
  -- Contact information
  email VARCHAR(255),
  -- Mobile push notification token (updated on each app launch)
  fcm_token VARCHAR(500),
  -- Home location (for "near me" store queries)
  home_lat DECIMAL(10, 8),
  home_lon DECIMAL(11, 8),
  -- Community engagement metrics
  contribution_count INT NOT NULL DEFAULT 0,
  reputation_score DECIMAL(10, 2) NOT NULL DEFAULT 0,
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT user_profiles_home_lat_valid CHECK (home_lat IS NULL OR (home_lat >= -90 AND home_lat <= 90)),
  CONSTRAINT user_profiles_home_lon_valid CHECK (home_lon IS NULL OR (home_lon >= -180 AND home_lon <= 180)),
  CONSTRAINT user_profiles_contribution_count_non_negative CHECK (contribution_count >= 0),
  CONSTRAINT user_profiles_reputation_score_valid CHECK (reputation_score >= 0)
) WITH (fillfactor = 90);

-- Indexes for user lookups
CREATE INDEX idx_user_profiles_firebase_uid ON user_profiles (firebase_uid);
CREATE INDEX idx_user_profiles_email ON user_profiles (email);

COMMENT ON TABLE user_profiles IS 'User identity and preferences, including home location and contribution metrics.';
COMMENT ON COLUMN user_profiles.firebase_uid IS 'Unique Firebase authentication identifier (immutable)';
COMMENT ON COLUMN user_profiles.fcm_token IS 'Firebase Cloud Messaging token for push notifications (updated on each app launch)';
COMMENT ON COLUMN user_profiles.home_lat IS 'User home latitude for nearby store and price queries';
COMMENT ON COLUMN user_profiles.home_lon IS 'User home longitude for nearby store and price queries';
COMMENT ON COLUMN user_profiles.reputation_score IS 'Community trust metric (0+) based on price report accuracy';

-- ============================================================================
-- TABLE: price_reports
-- ============================================================================
-- Core price intelligence table. Each report represents a user-submitted or
-- scraper-derived price observation at a specific store/product combination.
-- Includes confidence scores, sale information, and temporal validity.
--
-- The combination of (product_id, store_id, valid_from) enables time-series
-- analysis of pricing trends. Spatial coordinates denormalized for spatial
-- queries without joining to stores table.
--
CREATE TABLE price_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Foreign keys
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
  reporter_user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
  -- Price information (in Australian cents)
  price_cents INT NOT NULL,
  source VARCHAR(50) NOT NULL, -- e.g., "user_report", "flyer_scrape", "web_scrape"
  -- Data quality metrics
  confidence DECIMAL(3, 2) NOT NULL, -- 0.0 to 1.0
  -- Sale information
  is_on_sale BOOLEAN NOT NULL DEFAULT FALSE,
  sale_pct_off DECIMAL(5, 2), -- percentage discount if is_on_sale=TRUE
  -- Temporal validity (enables historical price tracking)
  valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  valid_to TIMESTAMPTZ,
  -- Spatial data denormalized for query efficiency
  lat DECIMAL(10, 8) NOT NULL,
  lon DECIMAL(11, 8) NOT NULL,
  -- Community moderation
  is_flagged BOOLEAN NOT NULL DEFAULT FALSE,
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Constraints
  CONSTRAINT price_reports_price_cents_non_negative CHECK (price_cents >= 0),
  CONSTRAINT price_reports_confidence_valid CHECK (confidence >= 0.0 AND confidence <= 1.0),
  CONSTRAINT price_reports_sale_pct_valid CHECK (sale_pct_off IS NULL OR (sale_pct_off > 0 AND sale_pct_off <= 100)),
  CONSTRAINT price_reports_sale_consistency CHECK (
    (is_on_sale = TRUE AND sale_pct_off IS NOT NULL) OR
    (is_on_sale = FALSE AND sale_pct_off IS NULL)
  ),
  CONSTRAINT price_reports_valid_to_after_valid_from CHECK (valid_to IS NULL OR valid_to > valid_from),
  CONSTRAINT price_reports_lat_valid CHECK (lat >= -90 AND lat <= 90),
  CONSTRAINT price_reports_lon_valid CHECK (lon >= -180 AND lon <= 180)
) WITH (fillfactor = 80);

-- Critical indexes for price queries
CREATE INDEX idx_price_reports_product_id ON price_reports (product_id);
CREATE INDEX idx_price_reports_store_id ON price_reports (store_id);
CREATE INDEX idx_price_reports_created_at ON price_reports (created_at DESC);
CREATE INDEX idx_price_reports_valid_from_to ON price_reports (valid_from, valid_to);

-- Spatial index for geographic price queries
CREATE INDEX idx_price_reports_location
  ON price_reports USING GIST (
    ll_to_earth(lat, lon)
  );

-- Composite indexes for common query patterns
CREATE INDEX idx_price_reports_product_store_time
  ON price_reports (product_id, store_id, valid_from DESC);

CREATE INDEX idx_price_reports_source ON price_reports (source);
CREATE INDEX idx_price_reports_flagged ON price_reports (is_flagged);

COMMENT ON TABLE price_reports IS 'Core table for price intelligence. Tracks product prices at specific stores with confidence metrics and temporal validity for historical analysis.';
COMMENT ON COLUMN price_reports.price_cents IS 'Price in Australian cents (not decimals) for precision without floating point errors';
COMMENT ON COLUMN price_reports.source IS 'Origin of price data (user_report, flyer_scrape, web_scrape)';
COMMENT ON COLUMN price_reports.confidence IS 'Data quality metric (0.0-1.0) representing likelihood of price accuracy';
COMMENT ON COLUMN price_reports.valid_from IS 'Start of pricing validity period (e.g., flyer start date)';
COMMENT ON COLUMN price_reports.valid_to IS 'End of pricing validity period (e.g., flyer end date)';
COMMENT ON COLUMN price_reports.lat IS 'Store latitude (denormalized from stores table for query efficiency)';
COMMENT ON COLUMN price_reports.lon IS 'Store longitude (denormalized from stores table for query efficiency)';

-- ============================================================================
-- TABLE: user_watchlist
-- ============================================================================
-- User price alerts and notifications configuration. Each watchlist item
-- represents a product a user wants to monitor with threshold-based triggers.
--
CREATE TABLE user_watchlist (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Foreign keys
  user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  -- Alert triggers
  target_price_cents INT,
  target_pct_off DECIMAL(5, 2),
  alert_on_prediction BOOLEAN NOT NULL DEFAULT FALSE,
  -- Store preferences (array of chain names)
  preferred_chains VARCHAR(100)[],
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT user_watchlist_user_product_unique UNIQUE (user_id, product_id),
  CONSTRAINT user_watchlist_at_least_one_trigger CHECK (
    target_price_cents IS NOT NULL OR
    target_pct_off IS NOT NULL OR
    alert_on_prediction = TRUE
  ),
  CONSTRAINT user_watchlist_target_price_non_negative CHECK (target_price_cents IS NULL OR target_price_cents >= 0),
  CONSTRAINT user_watchlist_target_pct_valid CHECK (target_pct_off IS NULL OR (target_pct_off > 0 AND target_pct_off <= 100))
);

-- Indexes for user watchlist queries
CREATE INDEX idx_user_watchlist_user_id ON user_watchlist (user_id);
CREATE INDEX idx_user_watchlist_product_id ON user_watchlist (product_id);

COMMENT ON TABLE user_watchlist IS 'User price alert configuration. Each row represents a product the user is monitoring with threshold-based triggers.';
COMMENT ON COLUMN user_watchlist.target_price_cents IS 'Alert when price drops to or below this value (in cents)';
COMMENT ON COLUMN user_watchlist.target_pct_off IS 'Alert when discount percentage reaches or exceeds this value';
COMMENT ON COLUMN user_watchlist.alert_on_prediction IS 'Enable alerts for predicted sales/price drops from ML model';
COMMENT ON COLUMN user_watchlist.preferred_chains IS 'Array of grocery chains to monitor (NULL means all chains)';

-- ============================================================================
-- TABLE: notifications_log
-- ============================================================================
-- Complete audit log of user notifications. Tracks delivery status, timestamps,
-- and user engagement (opened/unopened) for notification analytics.
--
CREATE TABLE notifications_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Foreign keys
  user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
  watchlist_id UUID NOT NULL REFERENCES user_watchlist(id) ON DELETE CASCADE,
  price_report_id UUID REFERENCES price_reports(id) ON DELETE SET NULL,
  -- Notification content
  trigger_type VARCHAR(50) NOT NULL, -- e.g., "price_drop", "predicted_sale", "manual_alert"
  message_title VARCHAR(200) NOT NULL,
  message_body VARCHAR(500),
  -- Delivery tracking
  fcm_message_id VARCHAR(500),
  is_delivered BOOLEAN NOT NULL DEFAULT FALSE,
  -- User engagement tracking
  sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  opened_at TIMESTAMPTZ,
  CONSTRAINT notifications_log_opened_after_sent CHECK (opened_at IS NULL OR opened_at >= sent_at)
);

-- Indexes for notification queries
CREATE INDEX idx_notifications_user_id ON notifications_log (user_id);
CREATE INDEX idx_notifications_sent_at ON notifications_log (sent_at DESC);
CREATE INDEX idx_notifications_is_delivered ON notifications_log (is_delivered);
CREATE INDEX idx_notifications_opened_at ON notifications_log (opened_at);

COMMENT ON TABLE notifications_log IS 'Audit log of all user notifications with delivery and engagement tracking for analytics.';
COMMENT ON COLUMN notifications_log.trigger_type IS 'Reason for notification (price_drop, predicted_sale, manual_alert)';
COMMENT ON COLUMN notifications_log.fcm_message_id IS 'Firebase Cloud Messaging message ID for tracking delivery status';
COMMENT ON COLUMN notifications_log.is_delivered IS 'Confirmation that FCM successfully delivered the message to device';
COMMENT ON COLUMN notifications_log.opened_at IS 'Timestamp when user opened/read the notification (NULL if unopened)';

-- ============================================================================
-- TABLE: price_predictions
-- ============================================================================
-- ML model predictions for future pricing and sales. Forecasts future prices
-- and sale probability for product/chain combinations. Used for "Deal Hunter"
-- alerts.
--
CREATE TABLE price_predictions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Foreign key
  product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  -- Prediction target
  chain VARCHAR(100) NOT NULL,
  -- Prediction data
  predicted_price_cents INT,
  prediction_week DATE NOT NULL,
  -- Confidence bounds
  confidence_interval_low INT,
  confidence_interval_high INT,
  -- Sale prediction
  is_likely_sale BOOLEAN NOT NULL DEFAULT FALSE,
  -- Model metadata
  model_version VARCHAR(50) NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Constraints
  CONSTRAINT price_predictions_price_non_negative CHECK (predicted_price_cents IS NULL OR predicted_price_cents >= 0),
  CONSTRAINT price_predictions_interval_valid CHECK (
    confidence_interval_low IS NULL OR confidence_interval_high IS NULL OR
    confidence_interval_low <= confidence_interval_high
  ),
  CONSTRAINT price_predictions_unique UNIQUE (product_id, chain, prediction_week)
);

-- Indexes for prediction queries
CREATE INDEX idx_price_predictions_product_id ON price_predictions (product_id);
CREATE INDEX idx_price_predictions_chain ON price_predictions (chain);
CREATE INDEX idx_price_predictions_prediction_week ON price_predictions (prediction_week);
CREATE INDEX idx_price_predictions_is_likely_sale ON price_predictions (is_likely_sale);

COMMENT ON TABLE price_predictions IS 'ML model predictions for future pricing and sales. Used for Deal Hunter alerts.';
COMMENT ON COLUMN price_predictions.predicted_price_cents IS 'Forecasted price (in cents) for the prediction week';
COMMENT ON COLUMN price_predictions.prediction_week IS 'Week starting date for which prediction applies';
COMMENT ON COLUMN price_predictions.confidence_interval_low IS 'Lower bound of 95% confidence interval (in cents)';
COMMENT ON COLUMN price_predictions.confidence_interval_high IS 'Upper bound of 95% confidence interval (in cents)';
COMMENT ON COLUMN price_predictions.is_likely_sale IS 'Binary prediction of whether a sale/discount is likely during this week';
COMMENT ON COLUMN price_predictions.model_version IS 'ML model version used for this prediction (for A/B testing and rollback)';

-- ============================================================================
-- TABLE: flyer_scrape_runs
-- ============================================================================
-- Execution log for automated grocery store flyer scraping. Tracks scraper
-- performance, products discovered, and error handling.
--
CREATE TABLE flyer_scrape_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Target chain
  chain VARCHAR(100) NOT NULL,
  -- Execution timeline
  run_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  run_finished_at TIMESTAMPTZ,
  -- Status tracking
  status VARCHAR(50) NOT NULL, -- "running", "completed", "failed", "partial"
  -- Results
  products_found INT NOT NULL DEFAULT 0,
  products_new INT NOT NULL DEFAULT 0, -- newly discovered products
  -- Error tracking
  errors_json JSONB, -- JSON array of error objects
  -- Constraints
  CONSTRAINT flyer_scrape_runs_finished_after_started CHECK (
    run_finished_at IS NULL OR run_finished_at >= run_started_at
  ),
  CONSTRAINT flyer_scrape_runs_products_non_negative CHECK (products_found >= 0 AND products_new >= 0)
);

-- Indexes for scrape run queries
CREATE INDEX idx_flyer_scrape_runs_chain ON flyer_scrape_runs (chain);
CREATE INDEX idx_flyer_scrape_runs_run_started_at ON flyer_scrape_runs (run_started_at DESC);
CREATE INDEX idx_flyer_scrape_runs_status ON flyer_scrape_runs (status);

COMMENT ON TABLE flyer_scrape_runs IS 'Execution log for automated grocery store flyer scraping with performance metrics.';
COMMENT ON COLUMN flyer_scrape_runs.status IS 'Scraper status: running, completed, failed, or partial (partial errors but some products extracted)';
COMMENT ON COLUMN flyer_scrape_runs.products_found IS 'Total products extracted from flyer in this run';
COMMENT ON COLUMN flyer_scrape_runs.products_new IS 'Count of products that were not previously in database';
COMMENT ON COLUMN flyer_scrape_runs.errors_json IS 'JSON array containing error objects: [{page_num: 1, error: "OCR failed", timestamp: "..."}]';

-- ============================================================================
-- MATERIALIZED VIEW: current_best_prices
-- ============================================================================
-- Denormalized view of the most recent valid price for each product at each
-- store. Used for quick price lookups without complex temporal joins.
-- Must be refreshed periodically or through triggers.
--
CREATE MATERIALIZED VIEW current_best_prices AS
SELECT
  p.id AS product_id,
  p.name AS product_name,
  p.gtin,
  s.id AS store_id,
  s.chain,
  s.name AS store_name,
  pr.price_cents,
  pr.is_on_sale,
  pr.sale_pct_off,
  pr.confidence,
  pr.valid_from,
  pr.valid_to,
  pr.created_at
FROM products p
INNER JOIN price_reports pr ON p.id = pr.product_id
INNER JOIN stores s ON s.id = pr.store_id
WHERE (pr.valid_to IS NULL OR pr.valid_to > NOW())
  AND pr.is_flagged = FALSE
  AND pr.created_at = (
    SELECT MAX(pr2.created_at)
    FROM price_reports pr2
    WHERE pr2.product_id = p.id
      AND pr2.store_id = s.id
      AND (pr2.valid_to IS NULL OR pr2.valid_to > NOW())
      AND pr2.is_flagged = FALSE
  );

CREATE UNIQUE INDEX idx_current_best_prices_product_store
  ON current_best_prices (product_id, store_id);

COMMENT ON MATERIALIZED VIEW current_best_prices IS 'Denormalized view of current best prices per product/store. Must be refreshed periodically.';

-- ============================================================================
-- FINAL VALIDATION
-- ============================================================================
-- The schema is now complete with all production-grade features:
-- - 8 core tables with comprehensive constraints
-- - Foreign key relationships with cascading deletes
-- - Temporal validity tracking for price history
-- - PostGIS spatial indexes for geographic queries
-- - pg_trgm trigram index for fuzzy product search
-- - Composite indexes for common query patterns
-- - JSON support for flexible error logging
-- - UUID primary keys throughout
-- - TIMESTAMPTZ with timezone awareness
-- - Extensive inline documentation
-- ============================================================================
