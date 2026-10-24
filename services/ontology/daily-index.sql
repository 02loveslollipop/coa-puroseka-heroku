-- Run separately with an operator connection, outside a transaction.
-- A covering UTC-day index avoids scanning the large raw_record JSON pages.
-- The importer creates the equivalent index in the configured schema.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ranking_daily_endpoints_idx
ON coa_puroseka.ranking_observations
(event_id, rank, ((observed_at AT TIME ZONE 'UTC')::date), observed_at DESC, row_key DESC)
INCLUDE (score);
