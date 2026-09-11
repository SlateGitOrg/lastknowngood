# lastknowngood

> A recovery drill harness that measures your real RTO and proves backup immutability by attacking it - in a sealed lab.

`FLAGSHIP` · **Cybersecurity** · Expert · ~5-6 weeks · Manufacturing - plant floor with OT/IT convergence

**Primary language:** Go
**Tags:** `resilience`, `backup`, `incident-response`, `chaos`, `object-lock`, `measurement`

---

> **Implementation note.** The catalogue specifies **Go** for this
> project and that remains the target. This repository ships a runnable
> **Python** reference implementation of the core differentiator so the
> behaviour is executable and tested today; port it to Go as step one
> of your own build.

## The problem

Every organisation claims a four-hour recovery objective. Almost none has restored under realistic conditions, and the first real attempt reveals that the backups were encrypted too, or the restore takes forty hours, or nobody can find the credentials because they lived in the system that is down. The recovery plan is fiction until it has been run, and the pager is the worst time to discover that.

## ⭐ The differentiator

**Empirically measures RPO and RTO by executing scripted restore drills**, and independently scores backup immutability by *attempting* deletion and modification with the production service account - proving immutability rather than reading a configuration flag. A generic backup project writes a runbook. This one produces a measured number that contradicts the runbook, which is the entire value.

This is the sentence to lead with when someone asks you to walk through the
project. Everything else in this repo exists to make it true and to prove it.

## Data

A fully self-contained lab: a Docker Compose estate (application database, file share, config store) with a synthetic data generator and a **simulated** encryption event confined to the compose network. The 'ransomware' is a documented file-rewriting script that is lab-scoped by construction - it has no network reach, no propagation, and no capability outside the compose project.

> No paid API key is required to run or demo this project. Where a paid
> service would add value it is wired as an optional enhancement behind an
> interface with an offline mock as the default implementation.

## Stack

- Go for the drill orchestrator
- Docker Compose lab estate
- MinIO with object lock; Restic for backups
- PostgreSQL point-in-time recovery
- Prometheus for instrumentation
- CI

## Core capabilities

- Drill orchestrator executing a full restore with wall-clock instrumentation per phase
- Immutability prober attempting deletion and overwrite with production-equivalent credentials
- Achieved-RPO measurement by diffing restored data against the pre-event snapshot
- Dependency-order discovery identifying which service could not start and exactly why
- Drill report with a phase-by-phase Gantt and the delta against the claimed RTO

## Repository layout

```
cmd/drill/
internal/orchestrate/
internal/probe/
internal/measure/
lab/                      # sealed compose estate + scenario scripts
reports/
docs/
```

## Build plan

1. Build the lab and the data generator. Take a known-good snapshot - it is the reference for every RPO measurement.
2. Instrument the restore path before optimising it. The first honest number is the point of the project.
3. Immutability prober next, including the negative case: deliberately misconfigure object lock and confirm the prober reports failure.
4. Dependency-order discovery last; it is where the surprising findings come from.

## Testing strategy

Assert measured RPO equals the known injected data-loss window to within one write. Assert the immutability prober correctly reports **failure** when object lock is deliberately misconfigured - a prober that always says 'immutable' passes a naive test and is worse than useless.

Tests assert **correctness**, not merely that the code runs. A green suite on
this repo is a claim about behaviour under adversarial conditions; treat any
test that would pass against a deliberately broken implementation as a bug in
the test.

## Quality & safety layer

The simulated encryption event is confined to the compose network by construction and documented as such in docs/threat-model.md. There is no propagation logic, no network discovery, and no capability outside the lab. This is a resilience-measurement tool, not malware.

## Measurable outcome

> Claimed RTO four hours; measured RTO eleven hours twenty minutes, six of which trace to a credential dependency nobody had documented. After the fix, the repeat drill measured three hours five minutes.

State it in these terms — business units, not technical ones — in your CV
bullet and in the first thirty seconds of describing the project.

## Interview questions this project answers

- **What is your real RTO, and how do you know?**
- **How do you prove a backup is immutable?**
- **What dependency will stop your restore, and have you tested it?**

## What this deliberately is *not*

- Not offensive tooling, and not generalisable to any system outside the lab.
- Not a backup product. It measures the one you have.


## Run it now

```bash
python -m unittest discover -s tests -v   # the suite
python -m src.demo                        # the 60-second artefact
```

Requires Python 3.11+. The runnable core uses **only the standard
library** (including `sqlite3`), so there is nothing to install.

## Getting started

```bash
git clone <your-fork-url> lastknowngood
cd lastknowngood
make lab-up                   # sealed estate
make seed                     # synthetic data + known-good snapshot
make drill                    # scripted event + full restore, instrumented
make probe-immutability
cat reports/latest.md
```

Docker is supported but optional — every path above works on a plain
Windows/macOS/Linux laptop without a cloud account.

## Definition of done

- [ ] The differentiator above is implemented, and a test proves it
- [ ] The measurable outcome is produced by a command anyone can run
- [ ] `README` explains the one decision a generic version gets wrong
- [ ] CI runs the full suite on every push and is green on `main`
- [ ] A recruiter can see the headline artefact in under 60 seconds

## Licence

MIT — see [LICENSE](LICENSE).
