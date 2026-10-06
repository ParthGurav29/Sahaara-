#!/usr/bin/env python3
"""
backend/tests/analyze_voice_timing.py

Analyzes the voice timing log (voice_timing.jsonl) and prints
median and p90 of each gap.
"""

import json
import statistics
import sys
from pathlib import Path
from typing import List, Dict, Any


def percentile(data: List[float], p: float) -> float:
    """Compute the p-th percentile (0-100) of a list of floats."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]


def analyze_timing_log(log_path: Path) -> None:
    """Analyze the voice timing log and print statistics."""
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        return
    
    entries = []
    with open(log_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to parse line: {e}")
                continue
    
    if not entries:
        print("No valid entries in log file.")
        return
    
    # Collect gaps
    gap_keys = [
        "asr_end_to_llm_request_ms",
        "llm_time_ms",
        "llm_token_to_tts_first_byte_ms",
        "tts_first_byte_to_audio_sent_ms",
        "speech_end_to_first_audio_ms",
        "speech_end_to_ack_started_ms",
    ]
    
    gaps: Dict[str, List[float]] = {k: [] for k in gap_keys}
    
    for entry in entries:
        g = entry.get("gaps", {})
        for k in gap_keys:
            if k in g and g[k] is not None:
                gaps[k].append(g[k])
    
    print(f"\n{'='*80}")
    print(f"VOICE TIMING ANALYSIS - {len(entries)} turns")
    print(f"{'='*80}")
    print(f"{'Gap':<40} {'Count':>6} {'Median (ms)':>14} {'P90 (ms)':>14} {'Min (ms)':>12} {'Max (ms)':>12}")
    print("-" * 100)
    
    for k in gap_keys:
        data = gaps[k]
        if data:
            med = statistics.median(data)
            p90 = percentile(data, 90)
            mn = min(data)
            mx = max(data)
            print(f"{k:<40} {len(data):>6} {med:>14.1f} {p90:>14.1f} {mn:>12.1f} {mx:>12.1f}")
        else:
            print(f"{k:<40} {'0':>6} {'N/A':>14} {'N/A':>14} {'N/A':>12} {'N/A':>12}")
    
    print("-" * 100)
    
    # Also print raw timings summary
    print(f"\n{'RAW TIMINGS (median)':<40}")
    print("-" * 100)
    timing_keys = [
        "t_speech_end",
        "t_ack_started",
        "t_llm_request",
        "t_first_llm_token",
        "t_first_tts_byte",
        "t_first_audio_sent",
    ]
    
    for k in timing_keys:
        vals = []
        for e in entries:
            if k in e.get("timings", {}):
                vals.append(e["timings"][k])
        if vals:
            print(f"  {k}: median={statistics.median(vals):.3f} (n={len(vals)})")


if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    log_path = BASE_DIR / "backend" / "logs" / "voice_timing.jsonl"
    
    # Allow override via command line
    if len(sys.argv) > 1:
        log_path = Path(sys.argv[1])
    
    analyze_timing_log(log_path)