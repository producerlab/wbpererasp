-- Migration: Encrypt phone numbers in browser_sessions
-- Date: 2026-02-03
-- Description: Add encrypted phone fields for security

-- Add new columns for encrypted phone storage
ALTER TABLE browser_sessions ADD COLUMN IF NOT EXISTS phone_encrypted TEXT;
ALTER TABLE browser_sessions ADD COLUMN IF NOT EXISTS phone_hash VARCHAR(64);
ALTER TABLE browser_sessions ADD COLUMN IF NOT EXISTS phone_last4 VARCHAR(4);

-- Create index for phone hash lookups
CREATE INDEX IF NOT EXISTS idx_browser_sessions_phone_hash ON browser_sessions(phone_hash);

-- Note: The old 'phone' column is kept for backward compatibility
-- New records will have phone=NULL, phone_encrypted=encrypted value
-- Old records can be migrated manually if needed
