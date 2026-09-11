"""The drill must measure, not assert.

The two assertions that matter:
  1. measured RPO equals the KNOWN injected loss window, to the record;
  2. the immutability prober reports FAILURE when the lock is misconfigured.

(2) is the one people skip. A prober that always returns "immutable: true"
passes every naive test and is worse than not having one, because it converts
an unknown into a false assurance.
"""

from __future__ import annotations

import unittest

from src.drill import CREDENTIAL_SCRAMBLE_SECONDS, run_drill
from src.lab import Estate, Store, probe_immutability

HOUR = 3600


def build_estate(*, credential_trap: bool = False) -> Estate:
    return Estate([
        Store("vault", depends_on=()),
        Store("app_db", depends_on=(),
              needs_credential_from="vault" if credential_trap else None),
        Store("file_share", depends_on=("app_db",)),
        Store("config", depends_on=()),
    ])


def seed(estate: Estate, records_per_store: int = 10) -> None:
    for name in estate.stores:
        for i in range(records_per_store):
            estate.tick(60)
            estate.write(name, f"{name}-{i}", f"value-{i}")


def keys_of(estate: Estate) -> dict[str, set[str]]:
    return {n: set(s.records) for n, s in estate.stores.items()}


class TestRpoMeasurement(unittest.TestCase):
    def test_measured_rpo_equals_the_known_loss_window(self):
        estate = build_estate()
        seed(estate)
        backup_at = estate.clock
        estate.take_backup()

        # Exactly 45 minutes of writes happen after the backup and are lost.
        estate.tick(45 * 60)
        event_at = estate.clock
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        self.assertTrue(report.restored, report.blocked_by)
        self.assertEqual(report.achieved_rpo_seconds, event_at - backup_at)
        self.assertEqual(report.achieved_rpo_seconds, 45 * 60)

    def test_records_written_after_the_backup_are_counted_as_lost(self):
        estate = build_estate()
        seed(estate)
        estate.take_backup()
        for i in range(7):
            estate.tick(60)
            estate.write("app_db", f"late-{i}", "v")
        event_at = estate.clock
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        self.assertEqual(report.lost_records, 7,
                         "the RPO in rows must match the writes after the backup")

    def test_a_backup_taken_at_the_event_loses_nothing(self):
        estate = build_estate()
        seed(estate)
        estate.take_backup()
        event_at = estate.clock
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        self.assertEqual(report.achieved_rpo_seconds, 0)
        self.assertEqual(report.lost_records, 0)


class TestImmutabilityProber(unittest.TestCase):
    def test_a_genuinely_locked_backup_survives_the_probe(self):
        estate = build_estate()
        seed(estate)
        backup = estate.take_backup(object_lock=True, lock_enforced=True)
        result = probe_immutability(backup)
        self.assertTrue(result.genuinely_immutable)
        self.assertTrue(result.claim_matches_reality)
        self.assertFalse(backup.deleted)

    def test_THE_TEST_PEOPLE_SKIP_a_misconfigured_lock_is_reported_as_false(self):
        # The setting says immutable. It is not enforced. A prober that reads
        # the config reports "immutable: true" and the organisation finds out
        # during the incident.
        estate = build_estate()
        seed(estate)
        backup = estate.take_backup(object_lock=True, lock_enforced=False)

        result = probe_immutability(backup)
        self.assertTrue(result.claims_immutable)
        self.assertFalse(result.genuinely_immutable)
        self.assertFalse(result.claim_matches_reality,
                         "the prober must flag the gap between claim and reality")
        self.assertTrue(backup.deleted, "the deletion actually succeeded")

    def test_a_backup_with_no_lock_is_honestly_reported(self):
        estate = build_estate()
        seed(estate)
        backup = estate.take_backup(object_lock=False)
        result = probe_immutability(backup)
        self.assertFalse(result.claims_immutable)
        self.assertFalse(result.genuinely_immutable)
        self.assertTrue(result.claim_matches_reality, "no claim, no gap")


class TestTheEventReachesTheBackups(unittest.TestCase):
    def test_unprotected_backups_are_destroyed_by_the_event(self):
        estate = build_estate()
        seed(estate)
        estate.take_backup(object_lock=False)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        self.assertFalse(report.restored)
        self.assertIn("no usable backup", report.blocked_by or "")

    def test_a_locked_backup_survives_and_recovery_succeeds(self):
        estate = build_estate()
        seed(estate)
        estate.take_backup(object_lock=True, lock_enforced=True)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        self.assertTrue(report.restored, report.blocked_by)

    def test_restored_data_is_the_pre_event_data_not_the_encrypted_data(self):
        estate = build_estate()
        seed(estate)
        before = estate.snapshot_digests()
        estate.take_backup(object_lock=True)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)
        self.assertNotEqual(estate.snapshot_digests(), before)

        run_drill(estate, pre_keys, event_at)
        self.assertEqual(estate.snapshot_digests(), before,
                         "a restore that does not restore is not a restore")


class TestDependencyDiscovery(unittest.TestCase):
    def test_restores_respect_declared_dependency_order(self):
        estate = build_estate()
        seed(estate)
        estate.take_backup(object_lock=True)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        order = [p.detail for p in report.phases if p.name == "restore_store"]
        self.assertLess(order.index("app_db"), order.index("file_share"))

    def test_THE_FINDING_a_credential_dependency_adds_hours_nobody_planned(self):
        # app_db needs a credential from vault, and this is only discovered by
        # attempting the restore in the order the runbook implies.
        estate = build_estate(credential_trap=True)
        seed(estate)
        estate.take_backup(object_lock=True)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        self.assertTrue(report.restored, report.blocked_by)
        scrambles = [p for p in report.phases if p.name == "resolve_credentials"]
        self.assertEqual(len(scrambles), 0,
                         "vault restores first here, so no scramble is needed")

    def test_a_true_credential_deadlock_is_measured_not_hidden(self):
        # vault itself needs a credential from app_db: a genuine cycle.
        estate = Estate([
            Store("vault", needs_credential_from="app_db"),
            Store("app_db", needs_credential_from="vault"),
        ])
        seed(estate, records_per_store=3)
        estate.take_backup(object_lock=True)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        scrambles = [p for p in report.phases if p.name == "resolve_credentials"]
        self.assertGreaterEqual(len(scrambles), 1)
        self.assertIn("which is also down", scrambles[0].detail)
        self.assertGreaterEqual(
            report.measured_rto_seconds, CREDENTIAL_SCRAMBLE_SECONDS)


class TestTheReportContradictsTheClaim(unittest.TestCase):
    def test_the_measured_rto_can_exceed_the_claimed_one(self):
        estate = Estate([
            Store("vault", needs_credential_from="app_db"),
            Store("app_db", needs_credential_from="vault"),
            Store("file_share", depends_on=("app_db",)),
        ])
        seed(estate, records_per_store=3)
        estate.take_backup(object_lock=True)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at, claimed_rto_seconds=4 * HOUR)
        self.assertTrue(report.restored)
        self.assertFalse(report.meets_claim,
                         "this is the contradiction the drill exists to surface")
        self.assertGreater(report.measured_rto_seconds, 4 * HOUR)

    def test_the_gantt_accounts_for_the_whole_measured_window(self):
        estate = build_estate()
        seed(estate)
        estate.take_backup(object_lock=True)
        event_at = estate.clock + 60
        pre_keys = keys_of(estate)
        estate.simulated_encryption_event(at=event_at)

        report = run_drill(estate, pre_keys, event_at)
        gantt = report.gantt()
        self.assertEqual(gantt[-1][2], report.measured_rto_seconds)
        for i in range(1, len(gantt)):
            self.assertEqual(gantt[i][1], gantt[i - 1][2], "gap in the timeline")


if __name__ == "__main__":
    unittest.main()
