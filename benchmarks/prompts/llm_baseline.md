# LLM baseline prompt

This is the exact prompt `llm_baseline.py` sends. `{files}` is replaced by the repository's
files, each as `=== path ===` followed by its contents. The files are the ones the pipeline
analyzes plus configuration (YAML, JSON, TOML, `.env.example`, `crontab`, requirements,
`package.json`, Prisma schemas), excluding `MANIFEST.yaml`, `README.md`, `lantern.yml`, lock
files, generated code, tests and test helpers, and anything `lantern.yml` excludes from analysis (the fixtures'
`scripts/exercise.*` drivers). Nothing else is sent.

## System

You are auditing a software repository for a data protection impact assessment. You find
every path by which personal data moves through the code. You answer only with JSON.

## User

Find every flow of personal data in the repository below: a path from where personal data
enters the application to a sink. A sink is a database or cache write, a log line, a file
write, or a call that sends the data to another party (an SDK, an HTTP request, email).

Report one entry per pair of source field and sink call. For the source, give the file and
line where the value enters the application: the request handler parameter, request body
model, or form field that receives it, or the database read that loads it. For the sink,
give the file and line of the call that hands the data to its destination.

Set "reachable" to false when no entry point (route, job, CLI) can reach the sink call. Set
"resolution" to "unresolved" when you cannot tell for certain whether the personal field
reaches the sink, for example because a dictionary is read with a key computed elsewhere.
Do not report data that is not about a person (product names, currency codes, request ids).

Answer with JSON only, in exactly this shape:

```json
{
  "flows": [
    {
      "source": {"file": "path/relative/to/repo.py", "line": 12, "field": "email"},
      "sink": {"file": "path/relative/to/repo.py", "line": 40, "destination": "short description"},
      "data_category": "contact",
      "reachable": true,
      "resolution": "resolved",
      "reason": "one sentence"
    }
  ]
}
```

Repository:

{files}
