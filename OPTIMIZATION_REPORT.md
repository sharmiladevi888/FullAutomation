# Continuity Studio — Optimization Report

Senior perf/architecture pass on the FastAPI + vanilla-JS pipeline. Priority
order per the brief: (1) API token cost, (2) image-pipeline speed, (3)
ffmpeg/render, (4) code quality.

Method note: there is **no live API key / balance** in this worktree, so no
real Claude/image API round-trips were timed. Token-cost items are reasoned
**statically** from the prompt-builder code. CPU/pipeline items were **measured
live** with a Python timer against the real `pipeline.downsize_for_vision`
code path on representative frames (numbers below are from that harness, since
removed). Nothing here is a fabricated benchmark.

Format per item: `{file:line — problem — fix — impact — risk}`.

---

## APPLIED (safe, high-impact)

### A1. Memoize `downsize_for_vision` + stop inflating small frames  ✅ APPLIED
- **file:line:** `pipeline.py:281` (rewritten; was a single un-cached function)
- **Problem (measured):** every `plan_edit`, `/api/edit-plan`, and
  `/api/characters/{id}/check` request re-reads each sequence frame from disk
  and re-runs PIL decode → resize → JPEG-encode on the **same** bytes. Call
  sites: `app.py:674, 894, 901, 1549, 1765, 2495, 3758, 3902, 4319`. For a
  40-frame sequence that is the whole pipeline recomputed per request. Measured
  cost of the decode+resize+encode: **~1329 ms / 20 detailed (1280px) frames**
  (~66 ms/frame worst case; ~12 ms/frame on lighter frames).
- **Second bug found while measuring:** for any source ≤ `max_side` (1024px) the
  old code did **no resize** but still re-encoded to JPEG q85, producing output
  **1.7–1.8× LARGER than the source PNG** (measured: 21 542 → 36 717 bytes for a
  1024² frame). That inflated the base64 wire payload to the proxy on every
  ≤1024px frame for zero quality gain.
- **Fix applied:**
  1. Bounded thread-safe LRU (256 entries) keyed by
     `sha1(source_bytes):max_side:quality`. Repeat requests on the same frames
     skip all decode/resize/encode work. Different bytes → different key, so
     correctness is preserved.
  2. When no downscale is needed (`scale >= 1.0`) return the **original bytes**
     unchanged instead of re-encoding. Callers feed these to
     `ClaudeClient._image_block`, which sniffs the media type from magic bytes
     (`claude_client.py:457`), so PNG/JPEG/WEBP/GIF all remain valid.
- **Impact (measured):** repeat-request downsize drops from **~1329 ms → ~100 ms
  for 20 frames (≈13× faster; the ~100 ms residual is the sha1 of multi-MB
  sources, still far cheaper than the decode it replaces).** Wire payload for
  ≤1024px frames drops ~1.7× → 1.0× (smaller upload to the vision proxy → lower
  bandwidth + marginally fewer bytes the proxy bills). Public signature
  `downsize_for_vision(image_bytes, max_side=1024, quality=85)` is unchanged.
- **Risk:** Low. Pure read-path memoization; output bytes are identical to (or a
  strict improvement on) the previous result. Cache is process-local and bounded.
- **Verification:** `python -m py_compile *.py` + `python -c "import app"` pass;
  harness confirmed (a) ≤1024px source returned byte-identical, (b) 1536px source
  still downscales to a valid 1024² JPEG, (c) warm calls hit the cache.

---

## PROPOSED (worth doing; left unapplied to stay surgical / low-risk)

### P1. Token cost — `generate_script` system prompt is resent on every retry
- **file:line:** `claude_client.py:546` (system prompt, ~1.6 KB ≈ ~450 tokens)
  consumed by `app.py:2212` and again at `app.py:2232` (`_generate_script_validated`
  corrective retry) and `app.py:5705` (autopilot undershoot retry).
- **Problem:** the large static directive block (RULES + STYLE BAN + FAST-CUT +
  DYNAMIC + DIALOGUE) is rebuilt and resent in full on each retry. With
  Anthropic **prompt caching** (`cache_control: ephemeral` on the system block)
  the unchanged prefix would be billed at ~0.1× on the 2nd+ call within 5 min.
- **Estimated impact:** retries are common on fast-cut videos (the undershoot
  path fires whenever scenes < 60% of target). Caching the system block saves
  ~90% of its input tokens on each retry (~400 tokens/retry). Static estimate —
  not measured (no API access).
- **Risk:** Medium. Requires the proxy to honour `cache_control`; the code
  already notes derouter is non-standard for streaming, so this needs a live
  probe before enabling. Left as proposal.

### P2. Token cost — `master_prompt` / `style_notes` duplicated in prompt body
- **file:line:** `pipeline.py:130 build_full_prompt` deliberately writes
  `style_notes` **twice** (instruction block + prepended onto the shot prompt,
  lines 140–147 and 182–188); `master_prompt` also appears in both the system
  framing and per-shot bibles across `claude_client.py` helpers.
- **Problem:** intentional for image-model anchoring (comment says "appears
  twice … strongly anchors the model"), so this is correct for *image* prompts.
  But the same world-bible text is also re-sent on **every** scene render and
  every plan call. For long bibles this is the single biggest per-call token
  line item.
- **Proposed:** cap/summarize `master_prompt` once (e.g. first ~200 chars, as
  `generate_missing_scenes` already does at `claude_client.py:1069`) for the
  *planning/vision* calls where the full bible adds little, while keeping the
  full text for image generation where the duplication is load-bearing.
- **Estimated impact:** proportional to bible length; meaningful for users with
  large style bibles. Not measured.
- **Risk:** Medium — could subtly change planning output. Needs A/B with a live
  key. Left as proposal.

### P3. Image pipeline — frames hashed twice (downsize + nothing reuses the hash)
- **file:line:** new cache in `pipeline.py` hashes source bytes; callers in
  `app.py` separately hold the same bytes.
- **Proposed:** thread a content key from `store.read_image` so the disk read
  itself can be cached per-`image_url` (the URL is immutable once written), so
  repeat requests skip even the `open()/read()`. Modest win on top of A1.
- **Risk:** Low–Medium (cache invalidation if an image_url is ever overwritten —
  it currently isn't, but that's an assumption). Left as proposal.

### P4. Render — per-shot clip encode loop is serial
- **file:line:** `video.py` `assemble_video:246` builds N per-shot mp4 clips in a
  serial `for` loop, each its own `ffmpeg` subprocess.
- **Observation:** the plain `cut` transition already uses the concat demuxer
  with `-c copy` (no re-encode) at `editor.py:333` — good. The cost is the
  per-clip normalization encodes. These are independent and could run in a
  bounded `ThreadPoolExecutor`, cutting wall-time ~Nx on multi-core.
- **Risk:** Medium — parallel ffmpeg spikes CPU/RAM and the user explicitly
  prefers a lean machine; concurrency would need a config knob. Left as proposal,
  not applied.

### P5. Render — add `-threads 0` is already implicit; `-preset veryfast` is fine
- **file:line:** `editor.py` clip/xfade/mux commands.
- **Finding:** libx264 already auto-threads; `-preset veryfast -crf 20` is a
  reasonable speed/size balance. **No change recommended** — flagged only to
  record that this was checked and is not a real win.

---

## CODE QUALITY — top 3 refactor candidates in `app.py` (6247 lines)

Identified per the brief; **not refactored** (too broad to be surgical/safe).

1. **Vision-frame assembly is copy-pasted 6×.** The
   `for s in st["sequence"]: frames.append(pipeline.downsize_for_vision(
   store.read_image(...)))` block recurs at `app.py:894/901, 1549, 1765, 2495,
   3758, 3902, 4319`. Extract one helper `_sequence_vision_frames(st, ...)`
   returning `(frames, kept_indices)`. Removes ~40 duplicated lines and the
   risk of the variants drifting (one already differs by re-numbering).
2. **`_resolve_claude` / client-factory duplication.** `get_claude_client`
   (`app.py:312`) and `_claude_client_for` (`app.py:2180`) both build a
   `ClaudeClient` from vault settings with near-identical logic. Collapse to one
   factory taking an optional `model` override.
3. **Route handlers mix transport + business logic.** The edit-plan/render
   routes (`app.py:3754–3946`, `4309–4339`) inline frame loading, Claude calls,
   ffmpeg orchestration and state mutation in one function each. Splitting a
   thin `_build_edl(st, ...)` service layer would make these testable and shrink
   the module. Largest effort, highest payoff for maintainability.

---

## What was checked and found already-good (no change)
- `image_queue.py` — throttle (bounded concurrency + adaptive cooldown + shared
  429 backoff) and `run_with_retry` release the semaphore before backing off.
  No redundant work; well-built.
- `derouter.py` / `ImageClient` — retry routed through the queue, 402→OpenRouter
  fallback, size snapping, no double-encode. Clean.
- `claude_client.py` — `plan_edit` already chunks frames in batches of 18 to
  respect Anthropic's ~20-image cap (was a real prior bug, now fixed); script
  retries are condition-guarded, not unconditional double-calls.

## Verification of applied change
```
python -m py_compile *.py   → COMPILE_OK
python -c "import app"       → IMPORT_OK
```
No public function signature or API route name changed. `data/` and `vault.json`
untouched.
