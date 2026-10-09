"""
backend/tests/harness.py

Text-mode measurement harness for Phase A.
Runs scenarios through process_turn() and records latency, guardrails, structure, repetition.
"""

import argparse
import json
import math
import re
import statistics
import time
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conversation import process_turn, reset_pipeline
from tests.scenarios import SCENARIOS, get_scenario, list_scenarios
from tests.repetition_score import compute_repetition_metrics, interpret_score
from llm.response_policy import check_response_structure


def run_scenario_once(scenario: Dict, temperature: Optional[float] = None, verbose: bool = False) -> Dict[str, Any]:
    """Run a single scenario through the pipeline once and return results."""
    reset_pipeline()
    
    turns_data = []
    total_llm_time = 0.0
    
    for i, user_message in enumerate(scenario["turns"]):
        # Measure LLM time
        start = time.perf_counter()
        result = process_turn(user_message)
        llm_time_ms = (time.perf_counter() - start) * 1000
        total_llm_time += llm_time_ms
        
        if "error" in result:
            turns_data.append({
                "turn": i + 1,
                "user_message": user_message,
                "error": result["error"],
                "llm_time_ms": round(llm_time_ms, 1),
            })
            continue
        
        # Extract meta
        meta = result.get("meta", {})
        
        # Determine turn type based on scenario (for scenario-aware structure check)
        scenario_name = scenario["name"]
        if "Danger" in scenario_name:
            turn_type = "danger"
        elif "Casual" in scenario_name:
            turn_type = "casual"
        else:
            turn_type = "emotional"
        
        # Detailed structure check (always computed for scenario-aware checking)
        structure_detail = check_response_structure(result["response"], turn_type=turn_type)
        
        turn_data = {
            "turn": i + 1,
            "user_message": user_message,
            "response": result["response"],
            "llm_time_ms": round(llm_time_ms, 1),
            "tier": meta.get("tier", "unknown"),
            "emotion": meta.get("emotion", "unknown"),
            "is_repeated_question": meta.get("is_repeated_question", False),
            "sustained_distress": meta.get("sustained_distress", False),
            "memory_box_offered": meta.get("memory_box_offered"),
            "guardrail_passed": not meta.get("guardrail_fallback_used", False),
            "guardrail_violations": meta.get("guardrail_violations", []),
            "structure_passed": structure_detail.passes,  # Use scenario-aware check
            "model": meta.get("model", "unknown"),
            "consent_state": meta.get("consent_state", "idle"),
        }
        
        if verbose:
            turn_data["structure_detail"] = {
                "has_validation": structure_detail.has_validation_language,
                "has_grounding": structure_detail.has_grounding_language,
                "has_concrete_choice": structure_detail.has_concrete_choice,
                "has_open_ended": structure_detail.has_open_ended_question,
                "has_corrective": structure_detail.has_corrective_language,
                "passes": structure_detail.passes,
                "turn_type": structure_detail.turn_type,
            }
        
        turns_data.append(turn_data)
        
        # Verbose output per turn
        if verbose:
            print(f"\n  Turn {i+1}: '{user_message}'")
            print(f"    Reply: {result['response'][:80]}{'...' if len(result['response']) > 80 else ''}")
            print(f"    Structure: validation={'✓' if structure_detail.has_validation_language else '✗'} "
                  f"grounding={'✓' if structure_detail.has_grounding_language else '✗'} "
                  f"choice={'✓' if structure_detail.has_concrete_choice else '✗'} "
                  f"open-ended={'✗' if structure_detail.has_open_ended_question else '✓'} "
                  f"corrective={'✗' if structure_detail.has_corrective_language else '✓'} "
                  f"OVERALL={'PASS' if structure_detail.passes else 'FAIL'} "
                  f"[turn_type={turn_type}]")
    
    # Compute repetition metrics across all replies
    replies = [t["response"] for t in turns_data if "response" in t]
    repetition_metrics = compute_repetition_metrics(replies)
    
    # Overall pass rates
    guardrail_passed = sum(1 for t in turns_data if t.get("guardrail_passed", False))
    structure_passed = sum(1 for t in turns_data if t.get("structure_passed", False))
    total_turns = len(turns_data)
    
    return {
        "name": scenario["name"],
        "description": scenario.get("description", ""),
        "turns": total_turns,
        "turns_data": turns_data,
        "total_llm_time_ms": round(total_llm_time, 1),
        "median_llm_time_ms": round(total_llm_time / total_turns, 1) if total_turns else 0,
        "guardrail_pass_rate": round(guardrail_passed / total_turns, 3) if total_turns else 1.0,
        "structure_pass_rate": round(structure_passed / total_turns, 3) if total_turns else 1.0,
        "final_tier": turns_data[-1].get("tier", "unknown") if turns_data else "unknown",
        "repetition_score": repetition_metrics["repetition_score"],
        "repetition_rating": interpret_score(repetition_metrics["repetition_score"], repetition_metrics["num_flags"]),
        "repetition_flags": repetition_metrics["num_flags"],
        "opening_overlap": repetition_metrics["opening_overlap"],
        "full_similarity": repetition_metrics["full_similarity"],
        "repetition_details": {
            "opening_phrases": repetition_metrics.get("opening_phrases", []),
            "flags": repetition_metrics["flags"],
        },
    }


def extract_offers_and_openers(replies: List[str]) -> Dict[str, Any]:
    """Extract offers and openers from replies for cross-scenario repetition analysis."""
    # Common offer patterns
    offer_patterns = [
        (r"sit by the window", "sit by the window"),
        (r"sit here|sit with", "sit here/with"),
        (r"hear (some|soft|gentle) music", "hear music"),
        (r"hear (her|his|their) voice", "hear voice"),
        (r"look at (his|her|their) photo", "look at photo"),
        (r"call (her|him|them)", "call"),
        (r"walk", "walk"),
        (r"rest your hand|hold", "rest hand"),
        (r"garden photos|tablet", "tablet/photos"),
        (r"soft music|gentle music|song", "music"),
    ]
    
    # Common opener patterns
    opener_patterns = [
        (r"^i hear how", "i hear how"),
        (r"^i hear that", "i hear that"),
        (r"^i hear you", "i hear you"),
        (r"^i understand", "i understand"),
        (r"^i can hear", "i can hear"),
        (r"^that sounds", "that sounds"),
        (r"^it sounds like", "it sounds like"),
        (r"^i'm so sorry", "i'm so sorry"),
        (r"^i'm right here", "i'm right here"),
        (r"^good afternoon", "good afternoon"),
        (r"^it's a", "it's a"),
        (r"^it is a", "it is a"),
        (r"^she's at|she is at", "she's at"),
        (r"^he's not|he is not", "he's not"),
        (r"^would you like", "would you like"),
    ]
    
    offers = []
    openers = []
    
    for reply in replies:
        text_lower = reply.lower()
        
        # Extract offers
        found_offer = False
        for pattern, label in offer_patterns:
            if re.search(pattern, text_lower):
                offers.append(label)
                found_offer = True
                break
        if not found_offer:
            offers.append("other")
        
        # Extract openers (first ~4 words)
        words = re.findall(r'\b\w+\b', text_lower)[:4]
        opener = " ".join(words)
        matched_opener = "other"
        for pattern, label in opener_patterns:
            if re.search(pattern, text_lower):
                matched_opener = label
                break
        openers.append(matched_opener)
    
    # Count frequencies
    offer_counts = Counter(offers)
    opener_counts = Counter(openers)
    total = len(replies)
    
    return {
        "offer_counts": dict(offer_counts),
        "opener_counts": dict(opener_counts),
        "top_offer": offer_counts.most_common(1)[0] if offers else ("none", 0),
        "top_opener": opener_counts.most_common(1)[0] if openers else ("none", 0),
        "top_offer_share": round(offer_counts.most_common(1)[0][1] / total, 3) if offers else 0.0,
        "top_opener_share": round(opener_counts.most_common(1)[0][1] / total, 3) if openers else 0.0,
    }


def check_unsupported_claims(replies: List[str]) -> Dict[str, Any]:
    """Check for unsupported claims about whereabouts, schedules, weather."""
    import re
    
    claim_patterns = {
        "whereabouts": [
            r"she'?s at (work|home|school|the (office|store|shop|garden))",
            r"she is at (work|home|school|the (office|store|shop|garden))",
            r"he'?s at (work|home|school|the (office|store|shop|garden))",
            r"he is at (work|home|school|the (office|store|shop|garden))",
            r"they'?re at (work|home|school|the (office|store|shop|garden))",
            r"just stepped out|just left|just walked out",
            r"in the (garden|kitchen|living room|bedroom)",
        ],
        "schedule": [
            r"(she|he|they) (will|'ll|is|are) (coming|arriving|visiting) (tomorrow|today|soon|this (morning|afternoon|evening|week))",
            r"planning to come",
            r"scheduled to (come|arrive|visit)",
            r"on (her|his|their) way",
            r"will be (here|home|back) (soon|tomorrow|today)",
        ],
        "weather": [
            r"it'?s (sunny|cloudy|rainy|warm|cold|hot|pleasant|gentle|bright|lovely) (outside|today|today'?s (morning|afternoon|evening))",
            r"the (sun|sky) is (shining|bright|clear)",
            r"(warm|sunny|chilly|breezy|gentle) (morning|afternoon|evening|day)",
            r"(light|soft) breeze",
            r"(sun|sunshine) (shining|is)",
        ],
        "surroundings": [
            r"flowers (blooming|are blooming)",
            r"garden (looks|is) (lovely|nice|beautiful|peaceful)",
            r"(roses|flowers) (are|look)",
        ],
    }
    
    all_claims = []
    claim_counts = {"whereabouts": 0, "schedule": 0, "weather": 0, "surroundings": 0}
    
    for reply in replies:
        text_lower = reply.lower()
        for category, patterns in claim_patterns.items():
            for pattern in patterns:
                matches = re.findall(pattern, text_lower)
                if matches:
                    claim_counts[category] += len(matches)
                    for m in matches:
                        all_claims.append({
                            "category": category,
                            "match": m if isinstance(m, str) else " ".join(m) if isinstance(m, tuple) else str(m),
                            "reply": reply[:100],
                        })
    
    total_claims = sum(claim_counts.values())
    
    return {
        "claim_counts": claim_counts,
        "total_unsupported_claims": total_claims,
        "claims": all_claims,
    }


def aggregate_runs(runs: List[Dict], scenario_name: str) -> Dict:
    """Aggregate multiple runs of the same scenario into mean ± std stats."""
    if not runs:
        return {}
    
    # Extract metrics from each run
    median_llm_times = [r["median_llm_time_ms"] for r in runs]
    structure_rates = [r["structure_pass_rate"] for r in runs]
    rep_scores = [r["repetition_score"] for r in runs]
    rep_ratings = [r["repetition_rating"] for r in runs]
    rep_flags = [r["repetition_flags"] for r in runs]
    guardrail_rates = [r["guardrail_pass_rate"] for r in runs]
    final_tiers = [r["final_tier"] for r in runs]
    
    # Collect all replies across runs for cross-run offer/opener/claim analysis
    all_replies = []
    for r in runs:
        for t in r.get("turns_data", []):
            if "response" in t:
                all_replies.append(t["response"])
    
    # Extract offer/opener metrics
    offer_opener_metrics = extract_offers_and_openers(all_replies)
    
    # Extract unsupported claims metrics
    claim_metrics = check_unsupported_claims(all_replies)
    
    # Check if this is a single-turn Danger scenario (no repetition possible)
    is_danger_single_turn = runs[0]["turns"] == 1 and "Danger" in scenario_name
    
    def mean_std(values: List[float]) -> Tuple[float, float]:
        if len(values) < 2:
            return (values[0], 0.0)
        return (statistics.mean(values), statistics.stdev(values))
    
    mean_llm, std_llm = mean_std(median_llm_times)
    mean_structure, std_structure = mean_std(structure_rates)
    mean_rep, std_rep = mean_std(rep_scores)
    mean_guardrail, std_guardrail = mean_std(guardrail_rates)
    
    # Most common final tier
    tier_counts = {}
    for t in final_tiers:
        tier_counts[t] = tier_counts.get(t, 0) + 1
    most_common_tier = max(tier_counts, key=tier_counts.get)
    
    # Repetition rating - most common
    rating_counts = {}
    for r in rep_ratings:
        rating_counts[r] = rating_counts.get(r, 0) + 1
    most_common_rating = max(rating_counts, key=rating_counts.get)
    
    # Total flags
    total_flags = sum(rep_flags)
    
    result = {
        "name": scenario_name,
        "runs": len(runs),
        "turns_per_run": runs[0]["turns"],
        "description": runs[0].get("description", ""),
        "median_llm_time_ms": {
            "mean": round(mean_llm, 1),
            "std": round(std_llm, 1),
            "min": round(min(median_llm_times), 1),
            "max": round(max(median_llm_times), 1),
        },
        "guardrail_pass_rate": {
            "mean": round(mean_guardrail, 3),
            "std": round(std_guardrail, 3),
        },
        "structure_pass_rate": {
            "mean": round(mean_structure, 3),
            "std": round(std_structure, 3),
        },
        "final_tier": most_common_tier,
        "repetition_score": {
            "mean": round(mean_rep, 3),
            "std": round(std_rep, 3),
            "min": round(min(rep_scores), 3),
            "max": round(max(rep_scores), 3),
        } if not is_danger_single_turn else "n/a",
        "repetition_rating": most_common_rating if not is_danger_single_turn else "n/a",
        "repetition_flags": total_flags if not is_danger_single_turn else "n/a",
        # New metrics
        "offer_opener_metrics": offer_opener_metrics,
        "claim_metrics": claim_metrics,
        # Regression thresholds (a drop in structure is regression; latency increase is regression; repetition increase is regression)
        # For latency: mean + max(2*std, 15% of mean)
        # For structure: mean - max(2*std, 0.10)  (lower is worse)
        # For repetition: mean + max(2*std, 0.10)  (higher is worse)
        "regression_thresholds": {
            "median_llm_time_ms": max(round(mean_llm * 1.15, 1), round(mean_llm + 2 * std_llm, 1)),
            "structure_pass_rate": max(round(mean_structure - max(2 * std_structure, 0.10), 3), 0.0),
            "repetition_score": max(round(mean_rep + max(2 * std_rep, 0.10), 3), 0.0) if not is_danger_single_turn else "n/a",
            "top_offer_share": round(offer_opener_metrics["top_offer_share"] + max(2 * 0.05, 0.10), 3),
            "top_opener_share": round(offer_opener_metrics["top_opener_share"] + max(2 * 0.05, 0.10), 3),
            "total_unsupported_claims": round(claim_metrics["total_unsupported_claims"] + max(2 * 1, 2), 1),
        },
    }
    
    return result


def run_scenario_multiple(scenario: Dict, runs: int, temperature: Optional[float] = None, verbose: bool = False) -> Dict:
    """Run a scenario multiple times and return aggregated results."""
    print(f"Running: {scenario['name']} ({runs} runs)...")
    
    runs_data = []
    for i in range(runs):
        if runs > 1:
            print(f"  Run {i+1}/{runs}...")
        run_result = run_scenario_once(scenario, temperature, verbose if i == 0 else False)
        runs_data.append(run_result)
    
    return aggregate_runs(runs_data, scenario["name"])


def run_all_scenarios(runs: int, temperature: Optional[float] = None, verbose: bool = False) -> List[Dict]:
    """Run all scenarios multiple times and return aggregated results."""
    results = []
    for scenario in SCENARIOS:
        result = run_scenario_multiple(scenario, runs, temperature, verbose)
        results.append(result)
    return results


def print_results_table(results: List[Dict]) -> None:
    """Print a pretty summary table with mean ± std."""
    print("\n" + "=" * 130)
    print(f"{'Scenario':<22} {'Runs':>5} {'LLM (ms) mean±std':>22} {'Guardrail mean±std':>18} {'Structure mean±std':>18} {'Tier':>8} {'Rep Score mean±std':>18} {'Rating':>8}")
    print("-" * 130)
    
    for r in results:
        llm = r["median_llm_time_ms"]
        gr = r["guardrail_pass_rate"]
        sr = r["structure_pass_rate"]
        rep = r["repetition_score"]
        
        llm_str = f"{llm['mean']:.1f}±{llm['std']:.1f}" if isinstance(llm, dict) else str(llm)
        gr_str = f"{gr['mean']:.3f}±{gr['std']:.3f}" if isinstance(gr, dict) else f"{gr:.3f}"
        sr_str = f"{sr['mean']:.3f}±{sr['std']:.3f}" if isinstance(sr, dict) else f"{sr:.3f}"
        
        if isinstance(rep, dict):
            rep_str = f"{rep['mean']:.3f}±{rep['std']:.3f}"
        else:
            rep_str = "n/a"
        
        rating = r.get("repetition_rating", "n/a")
        
        print(f"{r['name']:<22} {r['runs']:>5} {llm_str:>22} {gr_str:>18} {sr_str:>18} {r['final_tier']:>8} {rep_str:>18} {rating:>8}")
    
    # Print regression thresholds
    print("\n" + "=" * 130)
    print("REGRESSION THRESHOLDS (fail if metric exceeds):")
    print("-" * 130)
    print(f"{'Scenario':<22} {'LLM Latency (ms)':>20} {'Structure Pass Rate':>22} {'Repetition Score':>18}")
    print("-" * 130)
    for r in results:
        thresh = r["regression_thresholds"]
        llm_thresh = thresh["median_llm_time_ms"]
        sr_thresh = thresh["structure_pass_rate"]
        rep_thresh = thresh["repetition_score"]
        rep_str = f"{rep_thresh:.3f}" if isinstance(rep_thresh, float) else "n/a"
        print(f"{r['name']:<22} {llm_thresh:>20.1f} {sr_thresh:>22.3f} {rep_str:>18}")
    
    # Summary
    if results:
        avg_llm = sum(r['median_llm_time_ms']['mean'] for r in results) / len(results)
        avg_gr = sum(r['guardrail_pass_rate']['mean'] for r in results) / len(results)
        avg_sr = sum(r['structure_pass_rate']['mean'] for r in results) / len(results)
        rep_vals = [r['repetition_score']['mean'] for r in results if isinstance(r['repetition_score'], dict)]
        avg_rep = sum(rep_vals) / len(rep_vals) if rep_vals else 0
        
        print("-" * 130)
        print(f"{'AVERAGE':<22} {'':>5} {avg_llm:>16.1f} {'':>4} {avg_gr:>12.3f} {'':>4} {avg_sr:>12.3f} {'':>4} {'':>8} {avg_rep:>12.3f} {'':>4} {'':>8}")


def export_results(results: List[Dict], output_path: Path) -> None:
    """Export results to JSON file."""
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scenarios": results,
        "summary": {
            "median_llm_latency_ms": round(sum(r['median_llm_time_ms']['mean'] for r in results) / len(results), 1) if results else 0,
            "overall_guardrail_pass_rate": round(sum(r['guardrail_pass_rate']['mean'] for r in results) / len(results), 3) if results else 1.0,
            "overall_structure_pass_rate": round(sum(r['structure_pass_rate']['mean'] for r in results) / len(results), 3) if results else 1.0,
            "mean_repetition_score": round(
                sum(r['repetition_score']['mean'] for r in results if isinstance(r['repetition_score'], dict)) / 
                len([r for r in results if isinstance(r['repetition_score'], dict)]), 3
            ) if any(isinstance(r['repetition_score'], dict) for r in results) else 0.0,
        },
        "regression_rules": {
            "description": "A metric counts as regression if it exceeds: mean + 2*std_dev OR fixed limit (0.1 for scores, 15% for latency), whichever is larger",
            "latency_fixed_limit_pct": 0.15,
            "score_fixed_limit": 0.1,
            "std_multiplier": 2.0,
        },
    }
    output_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults exported to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run Sahaara scenarios through text pipeline and measure performance.")
    parser.add_argument("--scenario", default="all", help="Scenario name or 'all'")
    parser.add_argument("--runs", type=int, default=5, help="Number of runs per scenario (default: 5)")
    parser.add_argument("--export", type=Path, help="Export results to JSON file")
    parser.add_argument("--verbose", action="store_true", help="Print detailed structure check per turn (first run only)")
    parser.add_argument("--temperature", type=float, help="LLM temperature override (e.g., 0.3)")
    parser.add_argument("--no-llm", action="store_true", help="Use scripted responses (not implemented yet)")
    args = parser.parse_args()
    
    if args.temperature is not None:
        # Set temperature via environment variable for the LLM client
        import os
        os.environ["LLM_TEMPERATURE"] = str(args.temperature)
        print(f"Set LLM temperature to {args.temperature}")
    
    if args.scenario == "all":
        results = run_all_scenarios(args.runs, temperature=args.temperature, verbose=args.verbose)
    else:
        scenario = get_scenario(args.scenario)
        results = [run_scenario_multiple(scenario, args.runs, args.temperature, args.verbose)]
    
    print_results_table(results)
    
    if args.export:
        export_results(results, args.export)


if __name__ == "__main__":
    main()