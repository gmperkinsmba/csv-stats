# Context

Outcome Metrics is a standalone local application for duplicate-safe event cohort analysis. It uses an original CSV contract and a SQLite event store. No private source code, customer data, employer data, tokens, or external service is included.

The metric contract is explicit: an entity qualifies only if its first-ever denominator event falls inside the selected cohort range; one outcome event can count if it occurs at or after entry and within the exact observation window. All timestamps normalize to UTC, and date-only values mean midnight UTC. Cohort maturity is evaluated through the end of the selected as-of date. Pending entities are not included in the rate denominator. Runs store a frozen copy of the definition and entity-level evidence so later imports or definition versions do not change prior results.

All demo inputs are generated synthetic data. Runtime storage is outside this source directory. The service binds only to loopback.
