-- ─────────────────────────────────────────────────────────────────────────────
-- Audio recording for review comments.
--   * Adds audio_url column on rule_reviews (nullable — text-only reviews still OK)
--   * Creates a public Storage bucket review_audio
--   * Adds RLS policies so the anon role can INSERT and SELECT objects there
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE rule_reviews
    ADD COLUMN IF NOT EXISTS audio_url TEXT;

-- Bucket for reviewer audio. public=true so the public URL works without
-- signed-URL machinery; objects are still scoped under {reviewer_id}/ paths.
INSERT INTO storage.buckets (id, name, public)
VALUES ('review_audio', 'review_audio', true)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

-- Drop existing policies (idempotent re-runs)
DROP POLICY IF EXISTS "anon_insert_review_audio" ON storage.objects;
DROP POLICY IF EXISTS "anon_select_review_audio" ON storage.objects;

-- Allow the anon role (which the portal's JS uses) to upload to review_audio
CREATE POLICY "anon_insert_review_audio"
    ON storage.objects FOR INSERT
    TO anon, authenticated
    WITH CHECK (bucket_id = 'review_audio');

-- Allow read of review_audio objects (public bucket also makes /public/ URLs work)
CREATE POLICY "anon_select_review_audio"
    ON storage.objects FOR SELECT
    TO anon, authenticated
    USING (bucket_id = 'review_audio');
