"""
backend/tests/repetition_score.py

Repetition scoring for Sahaara replies.
Measures how similar consecutive replies are to detect repetitive openings.
"""

from typing import List, Dict, Any
import difflib
import re


def extract_opening_phrase(text: str, max_words: int = 3) -> str:
    """Extract the first N words as the opening phrase."""
    # Remove leading whitespace and punctuation
    text = text.lstrip()
    # Split into words, take first N
    words = re.findall(r'\b\w+\b', text.lower())
    return " ".join(words[:max_words])


def jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two strings (word sets)."""
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def sequence_similarity(a: str, b: str) -> float:
    """Compute SequenceMatcher ratio between two strings (0-1)."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def compute_repetition_metrics(replies: List[str]) -> Dict[str, Any]:
    """
    Compute repetition metrics across a list of replies.
    
    Returns dict with:
    - opening_overlap: list of Jaccard similarities between consecutive opening phrases
    - full_similarity: list of SequenceMatcher ratios between consecutive full replies
    - flags: list of bools indicating if a reply is flagged as repetitive
    - repetition_score: mean of full_similarity (0-1, higher = more repetitive)
    - num_flags: count of flagged replies
    """
    if len(replies) < 2:
        return {
            "opening_phrases": [extract_opening_phrase(r) for r in replies],
            "opening_overlap": [],
            "full_similarity": [],
            "flags": [],
            "repetition_score": 0.0,
            "num_flags": 0,
        }
    
    opening_phrases = [extract_opening_phrase(r) for r in replies]
    
    opening_overlap = []
    full_similarity = []
    flags = []
    
    for i in range(1, len(replies)):
        # Opening phrase overlap (Jaccard on first 3 words)
        oo = jaccard_similarity(opening_phrases[i-1], opening_phrases[i])
        opening_overlap.append(round(oo, 3))
        
        # Full reply similarity (SequenceMatcher)
        fs = sequence_similarity(replies[i-1], replies[i])
        full_similarity.append(round(fs, 3))
        
        # Flag: opening overlap > 0.6 OR full similarity > 0.7
        flagged = oo > 0.6 or fs > 0.7
        flags.append(flagged)
    
    # Overall repetition score = mean full similarity
    repetition_score = round(sum(full_similarity) / len(full_similarity), 3) if full_similarity else 0.0
    
    return {
        "opening_phrases": opening_phrases,
        "opening_overlap": opening_overlap,
        "full_similarity": full_similarity,
        "flags": flags,
        "repetition_score": repetition_score,
        "num_flags": sum(flags),
    }


def interpret_score(score: float, num_flags: int) -> str:
    """Interpret repetition score into a qualitative rating."""
    if score < 0.5 and num_flags == 0:
        return "Good"
    elif score < 0.7 and num_flags <= 2:
        return "Warning"
    else:
        return "Fail"


if __name__ == "__main__":
    # Quick test
    test_replies = [
        "I'm here with you. Would you like some tea?",
        "I'm here with you. Would you like to sit by the window?",
        "I'm here with you. Shall we listen to some music?",
        "That sounds lovely. Tell me more about your day.",
    ]
    
    metrics = compute_repetition_metrics(test_replies)
    print("Replies:", test_replies)
    print("Opening phrases:", metrics["opening_phrases"])
    print("Opening overlap:", metrics["opening_overlap"])
    print("Full similarity:", metrics["full_similarity"])
    print("Flags:", metrics["flags"])
    print(f"Repetition score: {metrics['repetition_score']} ({interpret_score(metrics['repetition_score'], metrics['num_flags'])})")