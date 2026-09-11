"""A sealed lab estate, and a scripted destructive event confined to it.

SCOPE AND SAFETY
----------------
Everything in this module operates on an in-process, in-memory estate. There is
no filesystem traversal, no network discovery, no propagation logic, and no
capability of any kind outside this process. The "encryption event" is a
documented function that overwrites entries in a dictionary, and it exists for
one reason: you cannot measure a recovery objective without something to
recover from.

This is a resilience-measurement harness. It is not malware and it is not
generalisable to any system outside the lab.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Iterable


@dataclass(frozen=True)
class Record:
    key: str
    value: str
    written_at: int

    @property
    def digest(self) -> str:
        return hashlib.sha256(f"{self.key}={self.value}".encode()).hexdigest()[:16]


@dataclass
class Store:
    """One data store in the estate (a database, a file share, a config store)."""

    name: str
    records: dict[str, Record] = field(default_factory=dict)
    # Services this store must be available before, during recovery.
    depends_on: tuple[str, ...] = ()
    # A credential this store needs to start, which may itself be stored
    # somewhere. This is the dependency that ruins real recovery attempts.
    needs_credential_from: str | None = None

    def write(self, key: str, value: str, at: int) -> None:
        self.records[key] = Record(key, value, at)

    def snapshot(self) -> dict[str, Record]:
        return dict(self.records)

    def restore_from(self, snapshot: dict[str, Record]) -> None:
        self.records = dict(snapshot)

    def digest(self) -> str:
        h = hashlib.sha256()
        for k in sorted(self.records):
            h.update(self.records[k].digest.encode())
        return h.hexdigest()[:16]


@dataclass
class Backup:
    """A point-in-time backup with an immutability setting.

    `object_lock` is the claim. Whether the claim is TRUE is not something this
    class can tell you - which is the entire point of the prober.
    """

    taken_at: int
    contents: dict[str, dict[str, Record]]
    object_lock: bool
    # Set when a misconfiguration means the lock is not actually enforced.
    lock_enforced: bool = True
    deleted: bool = False
    corrupted: bool = False


class Estate:
    def __init__(self, stores: Iterable[Store]) -> None:
        self.stores = {s.name: s for s in stores}
        self.backups: list[Backup] = []
        self.clock = 0

    # -- normal operation ---------------------------------------------------

    def tick(self, seconds: int = 1) -> None:
        self.clock += seconds

    def write(self, store: str, key: str, value: str) -> None:
        self.stores[store].write(key, value, self.clock)

    def take_backup(self, *, object_lock: bool = True,
                    lock_enforced: bool = True) -> Backup:
        b = Backup(
            taken_at=self.clock,
            contents={n: s.snapshot() for n, s in self.stores.items()},
            object_lock=object_lock,
            lock_enforced=lock_enforced,
        )
        self.backups.append(b)
        return b

    def snapshot_digests(self) -> dict[str, str]:
        return {n: s.digest() for n, s in self.stores.items()}

    # -- the scripted event, confined to this object ------------------------

    def simulated_encryption_event(self, at: int) -> int:
        """Overwrite every record in every store, and attack the backups.

        Returns the number of records affected. Real ransomware goes for the
        backups first; a drill that leaves them untouched measures the easy case
        and tells you nothing about the one that matters.
        """
        self.clock = at
        affected = 0
        for store in self.stores.values():
            for key in list(store.records):
                store.records[key] = Record(key, "ENCRYPTED", at)
                affected += 1
        for b in self.backups:
            # A lock that is claimed but not enforced protects nothing.
            if not (b.object_lock and b.lock_enforced):
                b.corrupted = True
        return affected


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """Did the deletion attempt actually fail?"""

    backup_taken_at: int
    claims_immutable: bool
    delete_attempt_blocked: bool
    overwrite_attempt_blocked: bool

    @property
    def genuinely_immutable(self) -> bool:
        return self.delete_attempt_blocked and self.overwrite_attempt_blocked

    @property
    def claim_matches_reality(self) -> bool:
        return self.claims_immutable == self.genuinely_immutable


def probe_immutability(backup: Backup) -> ProbeResult:
    """Try to destroy the backup, using production-equivalent credentials.

    Reading `object_lock` off the config and reporting "immutable: true" is what
    every dashboard already does, and it is how organisations discover during an
    incident that the setting was applied to the wrong bucket, or expired, or
    was never enforced. The only way to know is to attempt the destruction and
    observe that it fails.
    """
    delete_blocked = backup.object_lock and backup.lock_enforced
    if not delete_blocked:
        backup.deleted = True

    overwrite_blocked = backup.object_lock and backup.lock_enforced
    if not overwrite_blocked:
        backup.corrupted = True

    return ProbeResult(
        backup_taken_at=backup.taken_at,
        claims_immutable=backup.object_lock,
        delete_attempt_blocked=delete_blocked,
        overwrite_attempt_blocked=overwrite_blocked,
    )
