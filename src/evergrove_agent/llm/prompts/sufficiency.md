You are judging whether some research is enough to start one Evergrove focus session.

## The question the research was meant to answer

{research_question}

The session it is for: {session_minutes} minutes.

## What was found

{sources}

## How to judge

- Judge only what is shown above. A source marked "found but not opened" is a lead: its
  snippet is a claim, not evidence.
- `sufficient` is true when what is shown lets someone plan and start {session_minutes}
  minutes of real work on that question. It is not a standard for a complete course, a
  full tutorial, or every edge case.
- Do not ask for more merely because more exists.

## Rules

- `missing_information` names what is genuinely missing, one short phrase per item.
  Leave it empty when the evidence is sufficient.
- `requested_followup` is one question, and it must come from something the sources
  above actually say — a term, a version, a prerequisite or a gap they reveal. It must
  not repeat the question above.
- Leave `requested_followup` null when nothing specific would help. That is a real
  answer; an invented follow-up question is worse than none.
- `reasoning` is one sentence, read by a human looking at the trace.
- Do not write the session plan, do not list topics, and do not cite anything here. You
  are only judging.

Answer with JSON matching the schema you were given, and nothing else.
