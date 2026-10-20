# Latency Optimization Plan - Revert to Cartesia + Pipeline Optimizations

## Current Latency Bottlenecks

1. **LLM → TTS serial dependency**: Full LLM response awaited before TTS starts
2. **Fish Audio HTTP vs Cartesia WebSocket**: HTTP has higher latency than WebSocket streaming
3. **No token streaming to TTS**: Full LLM response awaited before TTS starts
4. **Standby ladder thresholds**: 2.5s / 5.0s are conservative
5. **TTS chunking**: 2048 byte chunks may be suboptimal

## Plan

### Phase 1: Revert to Cartesia WebSocket TTS (Lower Latency)
- Revert `voice/tts.py` to Cartesia WebSocket streaming
- Cartesia WebSocket has lower latency than Fish Audio HTTP
- Use existing `CARTESIA_API_KEY` and `CARTESIA_VOICE_ID` from `.env`

### Phase 2: Token-Level Streaming (LLM → TTS Pipeline)
- Modify `_llm_reply` to yield tokens as they arrive
- Stream tokens directly to Cartesia TTS as they arrive
- Eliminate "wait for full LLM response" bottleneck

### Phase 3: Pipeline Optimizations
- Reduce standby ladder thresholds (2.5s → 1.5s, 5.0s → 3.0s)
- Optimize TTS chunk size for lower latency
- Remove unnecessary `asyncio.sleep(0.01)` in Memory Box playback
- Optimize TTS chunk size for Cartesia (smaller chunks = lower latency)

### Phase 4: Timing & Monitoring
- Ensure timing logs capture all latency components
- Add per-component latency breakdown

## Files to Modify

1. `backend/voice/tts.py` - Revert to Cartesia WebSocket
2. `backend/voice/voice_pipeline.py` - Token streaming + latency optimizations
3. `backend/conversation.py` - Enable token streaming from LLM
4. `.env` - Ensure Cartesia credentials are active