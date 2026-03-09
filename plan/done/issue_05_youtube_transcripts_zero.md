# Issue 05 — YouTube Transcript Collection 0% Success

**Severity:** MEDIUM  
**Root Cause:** yt-dlp finds videos and marks them as "already collected", but the stored DB rows have empty transcript columns. The "already collected" check doesn't verify that transcript text actually exists.

## Files to Investigate

### 1. `app/services/youtube_service.py` (or equivalent)

- Find the "already collected" check — add `AND transcript_text IS NOT NULL AND transcript_text != ''` to the SQL query
- The pattern in logs: "All recent YouTube videos for $X already collected" → "no transcript found"

## Specific Changes

```sql
-- Current (inferred from behavior):
SELECT ... FROM youtube_videos WHERE video_id = ? 
-- Fix:
SELECT ... FROM youtube_videos WHERE video_id = ? AND transcript IS NOT NULL AND LENGTH(transcript) > 100
```

## Verification

### Automated Tests

- `pytest tests/test_youtube_collector.py -v`

### Manual Verification  

- After fix, run discovery for one ticker (e.g., AMD) and check log for "Collected 1 NEW transcripts" instead of "no transcript found"
- Check health report for new metric: "Transcripts available: X/Y"
