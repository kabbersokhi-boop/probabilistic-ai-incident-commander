"""Namespace-stable deterministic random number generation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from faker import Faker


@dataclass(frozen=True)
class RandomFactory:
    """Create independent reproducible random streams for simulator components.

    Deriving each stream from a namespace prevents adding a new generator from
    perturbing every existing table, which keeps benchmark datasets stable.
    """

    root_seed: int

    def seed_for(self, namespace: str) -> int:
        payload = f"{self.root_seed}:{namespace}".encode()
        digest = hashlib.sha256(payload).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False)

    def numpy(self, namespace: str) -> np.random.Generator:
        return np.random.default_rng(self.seed_for(namespace))

    def faker(self, namespace: str) -> Faker:
        fake = Faker("en_US")
        fake.seed_instance(self.seed_for(namespace) % (2**32))
        return fake
