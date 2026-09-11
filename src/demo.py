"""The 60-second artefact: the claimed RTO, and the measured one.

Run: python -m src.demo
"""

from __future__ import annotations

from .drill import run_drill
from .lab import Estate, Store


def hms(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"


def build(credential_trap: bool) -> Estate:
    return Estate([
        Store("vault", needs_credential_from="app_db" if credential_trap else None),
        Store("app_db", needs_credential_from="vault"),
        Store("file_share", depends_on=("app_db",)),
        Store("config"),
    ])


def seed(estate: Estate) -> None:
    for name in estate.stores:
        for i in range(50):
            estate.tick(60)
            estate.write(name, f"{name}-{i}", f"value-{i}")


def drill(label: str, credential_trap: bool, lock_enforced: bool) -> None:
    estate = build(credential_trap)
    seed(estate)
    estate.take_backup(object_lock=True, lock_enforced=lock_enforced)
    estate.tick(45 * 60)
    for i in range(12):
        estate.tick(60)
        estate.write("app_db", f"late-{i}", "v")
    event_at = estate.clock
    pre_keys = {n: set(s.records) for n, s in estate.stores.items()}
    estate.simulated_encryption_event(at=event_at)

    report = run_drill(estate, pre_keys, event_at, claimed_rto_seconds=4 * 3600)

    print(f"\n  {label}")
    print("  " + "-" * 68)
    for probe in report.probes:
        state = "ENFORCED" if probe.genuinely_immutable else "NOT ENFORCED"
        gap = "" if probe.claim_matches_reality else "   <- claim does not match reality"
        print(f"    immutability probe: claims={probe.claims_immutable}  "
              f"actual={state}{gap}")

    if not report.restored:
        print(f"    RECOVERY FAILED: {report.blocked_by}")
        print(f"    measured RTO: unbounded")
        return

    print()
    print(f"    {'phase':<26}{'start':>9}{'end':>9}")
    for name, start, end in report.gantt():
        print(f"    {name:<26}{hms(start):>9}{hms(end):>9}")

    verdict = "MET" if report.meets_claim else "MISSED"
    print()
    print(f"    claimed RTO   4h 00m")
    print(f"    measured RTO  {hms(report.measured_rto_seconds)}   <- {verdict}")
    print(f"    achieved RPO  {hms(report.achieved_rpo_seconds or 0)} "
          f"({report.lost_records} records lost)")


def main() -> None:
    print("\n  LASTKNOWNGOOD - the recovery plan is fiction until it has been run")
    print("  " + "=" * 68)

    drill("DRILL 1 - as the runbook describes it", credential_trap=True,
          lock_enforced=True)
    print("\n    Finding: app_db needs a credential held in vault, and vault")
    print("    needs one held in app_db. Nobody had documented this, because")
    print("    on paper each store restores independently.")

    drill("DRILL 2 - after breaking the credential cycle", credential_trap=False,
          lock_enforced=True)

    drill("DRILL 3 - object lock configured but not enforced",
          credential_trap=False, lock_enforced=False)
    print("\n    The setting said immutable. The probe deleted the backup.")
    print("    A dashboard reading the config would have reported this estate")
    print("    as protected right up until the morning it was needed.\n")


if __name__ == "__main__":
    main()
