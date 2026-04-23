-- ============================================================
-- AlphaPulse — Migration: add setup_type column
-- Run this in your Supabase SQL Editor if you already ran
-- setup_supabase.sql before this update.
-- ============================================================
ALTER TABLE trades
  ADD COLUMN IF NOT EXISTS setup_type TEXT NOT NULL DEFAULT 'major';
