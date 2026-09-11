"""The drill orchestrator: measure the recovery objectives you claim to have.

THE DIFFERENTIATOR LIVES HERE.

Every organisation has a four-hour RTO in a document. Almost none has restored
under realistic conditions, so the first real attempt is also the first test,
and it is conducted at 3am by people who have never done it.

This module does not write a runbook. It executes the restore with wall-clock
instrumentation per phase, measures the ACHIEVED RPO by diffing restored data
against the pre-event snapshot, and discovers dependency-ordering failures by
actually hitting them. The output is a number that usually contradicts the
document, and that contradiction is the deliverable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .lab import Backup, Estate, ProbeResult, probe_immutability

# Phase costs in seconds. Deliberately explicit and configurable rather than
# buried: every one of these is an assumption a reviewer should be able to
# challenge, and "where did 11 hours come from" is the first question asked.
PHASE_COSTS = {
    "declare_incident": 900,
    "locate_backup": 600,
    "provision_infrastructure": 3_600,
    "restore_store": 1_800,       # per store
    "resolve_credentials": 0,     # only if a dependency is missing
    "verify_integrity": 1_200,
    "cutover": 900,
}
CREDENTIAL_SCRAMBLE_SECONDS = 21_600  # 6h: find someone with the break-glass


@dataclass
class Phase:
    name: str
    seconds: int
    detail: str = ""


@dataclass
class DrillReport:
    claimed_rto_seconds: int
    phases: list[Phase] = field(default_factory=list)
    restored: bool = False
    achieved_rpo_seconds: int | None = None
    lost_records: int = 0
    blocked_by: str | None = None
    probes: list[ProbeResult] = field(default_factory=list)

    @property
    def measured_rto_seconds(self) -> int:
        return sum(p.seconds for p in self.phases)

    @property
    def meets_claim(self) -> bool:
        return self.restored and self.measured_rto_seconds <= self.claimed_rto_seconds

    def gantt(self) -> list[tuple[str, int, int]]:
        out: list[tuple[str, int, int]] = []
        t = 0
        for p in self.phases:
            out.append((p.name, t, t + p.seconds))
            t += p.seconds
        return out


def _usable_backups(estate: Estate) -> list[Backup]:
    return [b for b in estate.backups if not b.deleted and not b.corrupted]


def run_drill(
    estate: Estate,
    pre_event_keys: dict[str, set[str]],
    event_at: int,
    claimed_rto_seconds: int = 4 * 3600,
    probe_backups: bool = True,
) -> DrillReport:
    report = DrillReport(claimed_rto_seconds=claimed_rto_seconds)

    if probe_backups:
        # Probe BEFORE relying on them. A drill that assumes the backups are
        # good is measuring the happy path it already believed in.
        report.probes = [probe_immutability(b) for b in estate.backups]

    report.phases.append(Phase("declare_incident", PHASE_COSTS["declare_incident"]))
    report.phases.append(Phase("locate_backup", PHASE_COSTS["locate_backup"]))

    usable = _usable_backups(estate)
    if not usable:
        report.blocked_by = (
            "no usable backup: every copy was reachable from the compromised "
            "credentials"
        )
        return report

    backup = max(usable, key=lambda b: b.taken_at)

    report.phases.append(
        Phase("provision_infrastructure", PHASE_COSTS["provision_infrastructure"]))

    # Dependency ordering. A store that needs a credential held in a store that
    # is itself down cannot start, and this is the single most common reason a
    # rehearsed-on-paper recovery takes three times as long as planned.
    restored_names: set[str] = set()
    pending = list(estate.stores.values())
    guard = 0
    while pending and guard < 100:
        guard += 1
        progressed = False
        for store in list(pending):
            unmet = [d for d in store.depends_on if d not in restored_names]
            cred = store.needs_credential_from
            cred_unmet = cred is not None and cred not in restored_names

            if unmet:
                continue
            if cred_unmet:
                continue

            store.restore_from(backup.contents[store.name])
            restored_names.add(store.name)
            pending.remove(store)
            report.phases.append(
                Phase("restore_store", PHASE_COSTS["restore_store"], store.name))
            progressed = True

        if not progressed and pending:
            # Deadlock: something needs a credential from a store that cannot
            # start without it. Humans resolve this by finding the break-glass
            # copy, which takes hours nobody planned for.
            stuck = pending[0]
            report.phases.append(Phase(
                "resolve_credentials", CREDENTIAL_SCRAMBLE_SECONDS,
                f"{stuck.name} needs a credential from "
                f"{stuck.needs_credential_from}, which is also down",
            ))
            stuck.needs_credential_from = None

    if pending:
        report.blocked_by = "dependency cycle could not be resolved"
        return report

    report.phases.append(Phase("verify_integrity", PHASE_COSTS["verify_integrity"]))
    report.phases.append(Phase("cutover", PHASE_COSTS["cutover"]))
    report.restored = True

    # Achieved RPO is the gap between the backup and the event - the window of
    # writes that are simply gone.
    report.achieved_rpo_seconds = max(0, event_at - backup.taken_at)

    report.lost_records = _count_lost(pre_event_keys, backup)
    return report


def _count_lost(pre_event_keys: dict[str, set[str]], backup: Backup) -> int:
    """Records that existed immediately before the event but are not in the
    backup we restored from. This is the achieved RPO expressed in rows rather
    than in minutes, which is the version a business owner can price."""
    lost = 0
    for name, keys in pre_event_keys.items():
        in_backup = set(backup.contents.get(name, {}))
        lost += len(keys - in_backup)
    return lost
