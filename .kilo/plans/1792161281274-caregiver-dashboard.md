# Caregiver Dashboard Plan

## Goal
Build a rich caregiver dashboard (Rohan's view) with alert center + acknowledge, conversation transcript storage, alert detail view, physical-distress trigger, timeline, and trends. Fix blank-screen crash first.

## Decisions / Open Questions
- Transcripts retention: TBD (ask user). Plan assumes kept until reset; reset clears event log. Confirm before impl.

## Steps

1. Error boundary around dashboard (React) so render error can't blank screen. Wrap EscalationLadder + new AlertCenter in `DashboardErrorBoundary`.
2. Add `CONVERSATION_TURN` event type to `events/event_log.py` EventType enum + `log_conversation_turn(speaker, text, timestamp, emotion)` helper. speaker in {asha, sahaara}.
3. Wire conversation.py to call log_conversation_turn for each user/Sahaara turn.
4. Alert center component (frontend): unread count badge, list alerts (tier, time, one-line reason, status new/acknowledged), acknowledge button -> POST /alerts/{id}/acknowledge logs `ALERT_ACKNOWLEDGED` with timestamp + optional note. Red alerts flash.
5. Backend: alert store with acknowledge endpoint; persist acknowledged state.
6. Alert detail view: surrounding 5-10 turns + rule that fired + tier path. Requires turns stored (step 2/3).
7. Physical-distress trigger already exists (PHYSICAL_SYMPTOM) - ensure it feeds alert builder (build_physical_symptom_alert) and streams "alert".
8. Timeline view: chronological feed of events.
9. Trends: repeats by time of day, distress episodes this week. Observation only.

## Validation
- pytest backend/tests
- tsc frontend
- manual: trigger red alert, see flash, acknowledge, note logged.

## Risks
- In-app alert only works if dashboard open; document for Q&A, push on roadmap.
- Privacy: transcripts only to account holder; retention decision needed.