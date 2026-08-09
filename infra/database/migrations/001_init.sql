-- Vioris — Phase 1 initial schema.
-- The audit log is append-only and hash-chained: every row links to the
-- previous row via prev_hash, and on insert the database forces
-- hash := sha256(prev_hash || payload). UPDATE/DELETE are rejected by
-- triggers so no historical record can be silently edited or removed
-- (see docs/03-permissions-and-security.md).

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS pgcrypto;

BEGIN;

-- Append-only, hash-chained audit log.
CREATE TABLE IF NOT EXISTS audit_events (
    event_id   TEXT PRIMARY KEY,          -- 'evt_<hex>'
    task_id    TEXT,                      -- nullable: wake events have no task
    actor      TEXT NOT NULL,             -- 'system' | 'user' | 'agent:<name>'
    action     TEXT NOT NULL,             -- one of AuditAction enum values
    detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
    prev_hash  TEXT NOT NULL DEFAULT '',
    hash       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Genesis anchor: the very first event chains to a fixed, known hash.
CREATE TABLE IF NOT EXISTS audit_chain_anchor (
    anchor_id  BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (anchor_id),
    hash       TEXT NOT NULL
);

-- Seed genesis if it does not already exist (idempotent).
INSERT INTO audit_chain_anchor (anchor_id, hash)
VALUES (TRUE, repeat('0', 64))
ON CONFLICT (anchor_id) DO NOTHING;

-- Canonical payload: event fields joined deterministically. JSONB is
-- serialized with sorted keys by Postgres, so this is stable.
CREATE OR REPLACE FUNCTION audit_event_payload(
    p_event_id TEXT,
    p_actor    TEXT,
    p_action   TEXT,
    p_detail   JSONB
) RETURNS TEXT
LANGUAGE SQL IMMUTABLE
AS $$
    SELECT p_event_id || '|' || p_actor || '|' || p_action || '|' ||
           COALESCE(p_detail::text, '{}')
$$;

-- Append-only enforcement.
CREATE OR REPLACE FUNCTION audit_events_immutable()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'audit_events is append-only: UPDATE not allowed (event_id=%)', OLD.event_id
            USING ERRCODE = '55000';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'audit_events is append-only: DELETE not allowed (event_id=%)', OLD.event_id
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events;
CREATE TRIGGER trg_audit_events_immutable
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();

-- Link new events into the chain.
CREATE OR REPLACE FUNCTION audit_events_link_chain()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_prev TEXT;
BEGIN
    SELECT COALESCE(
        (SELECT e.hash FROM audit_events e
         ORDER BY created_at ASC, event_id ASC
         LIMIT 1),
        (SELECT a.hash FROM audit_chain_anchor a WHERE anchor_id = TRUE)
    ) INTO v_prev;

    -- The genesis hash (or the last inserted row) is the previous link.
    SELECT COALESCE(
        (SELECT e.hash FROM audit_events e
         ORDER BY created_at DESC, event_id DESC
         LIMIT 1),
        v_prev
    ) INTO v_prev;

    NEW.prev_hash = v_prev;
    NEW.hash      = encode(
        digest(
            v_prev ||
            audit_event_payload(NEW.event_id, NEW.actor, NEW.action, NEW.detail),
            'sha256'
        ),
        'hex'
    );

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_events_link_chain ON audit_events;
CREATE TRIGGER trg_audit_events_link_chain
BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_link_chain();

-- Integrity checker: recompute the chain from genesis and report mismatches.
CREATE OR REPLACE FUNCTION verify_audit_chain()
RETURNS TABLE (row_index BIGINT, event_id TEXT, prev_ok BOOLEAN, hash_ok BOOLEAN)
LANGUAGE plpgsql
AS $$
DECLARE
    v_prev TEXT := repeat('0', 64);
    v_idx  BIGINT := 0;
    r      RECORD;
BEGIN
    FOR r IN
        SELECT event_id, actor, action, detail, prev_hash, hash
        FROM audit_events
        ORDER BY created_at ASC, event_id ASC
    LOOP
        v_idx := v_idx + 1;
        row_index := v_idx;
        event_id  := r.event_id;
        prev_ok   := (r.prev_hash = v_prev);
        hash_ok   := (r.hash = encode(
            digest(v_prev ||
                audit_event_payload(r.event_id, r.actor, r.action, r.detail),
                'sha256'), 'hex'));
        v_prev := r.hash;
        RETURN NEXT;
    END LOOP;
END;
$$;

COMMIT;