---
description: Tiny health-check prompt used by `make llm-smoke` and the live smoke test.
---
## system
You are a health check for the FrogRadar LLM gateway. Reply with JSON only.

## user
Reply with {"answer": "pong", "tier": "$tier"} and nothing else.
