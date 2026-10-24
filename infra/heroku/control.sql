CREATE TABLE {schema}.auth_accounts (
 username text PRIMARY KEY,role text NOT NULL CHECK(role IN ('admin','user')),
 password_hash text,enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE {schema}.auth_sessions (
 token_hash text PRIMARY KEY,username text NOT NULL REFERENCES {schema}.auth_accounts(username),
 csrf text NOT NULL,expires_at timestamptz NOT NULL
);
CREATE TABLE {schema}.auth_login_attempts (
 key text PRIMARY KEY,attempts integer NOT NULL,resets_at timestamptz NOT NULL
);
CREATE TABLE {schema}.chat_policies (
 username text PRIMARY KEY,enabled boolean NOT NULL DEFAULT true,
 limit_enabled boolean NOT NULL DEFAULT false,token_limit bigint NOT NULL CHECK(token_limit>0),
 window_seconds integer NOT NULL CHECK(window_seconds BETWEEN 60 AND 2592000)
);
CREATE TABLE {schema}.chat_budgets (
 username text PRIMARY KEY REFERENCES {schema}.chat_policies(username),
 used_tokens bigint NOT NULL DEFAULT 0 CHECK(used_tokens>=0),reserved_tokens bigint NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
 started_at timestamptz NOT NULL DEFAULT now(),resets_at timestamptz NOT NULL
);
CREATE TABLE {schema}.chat_reservations (
 id uuid PRIMARY KEY,username text NOT NULL REFERENCES {schema}.chat_policies(username),
 amount bigint NOT NULL CHECK(amount>=0),state text NOT NULL DEFAULT 'active',charged_tokens bigint,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX reservations_active ON {schema}.chat_reservations(username,created_at) WHERE state='active';
CREATE TABLE {schema}.chat_jobs (
 id uuid PRIMARY KEY,username text NOT NULL REFERENCES {schema}.chat_policies(username),
 state text NOT NULL DEFAULT 'running',request jsonb NOT NULL,result jsonb,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_owner ON {schema}.chat_jobs(username,created_at);
CREATE TABLE {schema}.ontology_versions (name text PRIMARY KEY,version text NOT NULL,payload jsonb NOT NULL);
CREATE TABLE {schema}.ontology_examples (id integer PRIMARY KEY,payload jsonb NOT NULL);
CREATE TABLE {schema}.migration_ledger (version text PRIMARY KEY,report jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
