"""Shared fixtures.

Two rules shape everything here:

* **No test touches the network.** The NIM adapters are exercised with
  ``respx`` against recorded response shapes. A test that would make a real
  call is marked ``network`` and skipped by default.
* **No test touches the developer's cache or ``.env``.** Settings are built
  explicitly in fixtures rather than read from the environment, and every
  cache is pointed at ``tmp_path``. Otherwise a test's result would depend on
  whose machine it ran on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biologix.polymers.models import (
    ChainLength,
    EndGroup,
    MonomerBlock,
    PolymerCandidate,
    RepeatUnit,
)
from biologix.settings import Settings
from biologix.targets.models import (
    Biologic,
    BiologicClass,
    Chain,
    Modification,
    ModificationKind,
)

# Local endpoints, so credential_state() reports satisfied without a key and
# the tests exercise the request/response path rather than the refusal path.
LOCAL_OVERRIDES = {
    "BIOLOGIX_ESM2_BASE_URL": "http://127.0.0.1:8010",
    "BIOLOGIX_ESM2_PATH": "/v1/embeddings",
    "BIOLOGIX_GENMOL_BASE_URL": "http://127.0.0.1:8000",
    "BIOLOGIX_GENMOL_PATH": "/generate",
    # Explicit, so a developer .env that selects hosted GenMol cannot leak in.
    "BIOLOGIX_GENMOL_HOSTED": False,
    "BIOLOGIX_MOLMIM_BASE_URL": "http://127.0.0.1:8002",
    "BIOLOGIX_MOLMIM_PATH": "/generate",
    "BIOLOGIX_BOLTZ2_BASE_URL": "http://127.0.0.1:8003",
    "BIOLOGIX_BOLTZ2_PATH": "/predict",
    "BIOLOGIX_DIFFDOCK_BASE_URL": "http://127.0.0.1:8001",
    "BIOLOGIX_DIFFDOCK_PATH": "/molecular-docking/diffdock/generate",
}


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make every test credential-free and offline, whoever runs it.

    Without this, a developer with a real key in ``.env`` gets different
    results from one without: the MCP tools call :func:`get_settings`
    internally, so they would pick the key up, decide the adapter is
    available, and make a real network call. A test suite whose outcome
    depends on local credentials is not a test suite.

    Environment variables take precedence over ``.env`` in pydantic-settings,
    so blanking them here neutralises any ``.env`` on the machine. Caches are
    redirected into ``tmp_path`` for the same reason — a test must not read or
    write the developer's real cache. Tests that *want* a key present build
    Settings explicitly (see :func:`keyed_settings`).
    """
    from biologix.settings import reset_settings_cache

    for var in ("NVIDIA_API_KEY", "NGC_API_KEY"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("BIOLOGIX_DATA_DIR", str(tmp_path / "ambient-var"))
    monkeypatch.setenv("BIOLOGIX_CACHE_DIR", str(tmp_path / "ambient-cache"))
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def local_settings(tmp_path: Path) -> Settings:
    """Settings pointing every adapter at a local endpoint, no credentials."""
    return Settings(
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        **LOCAL_OVERRIDES,
    )


@pytest.fixture
def hosted_settings(tmp_path: Path) -> Settings:
    """Settings pointing at the hosted endpoints with no API key.

    Used to assert that every adapter refuses rather than calling.
    """
    return Settings(
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        NVIDIA_API_KEY=None,
    )


@pytest.fixture
def keyed_settings(tmp_path: Path) -> Settings:
    """Hosted endpoints with a dummy key present."""
    return Settings(
        BIOLOGIX_DATA_DIR=tmp_path / "var",
        BIOLOGIX_CACHE_DIR=tmp_path / "cache",
        NVIDIA_API_KEY="nvapi-test-key-not-real",
    )


@pytest.fixture
def insulin() -> Biologic:
    """Human insulin: two chains held by disulfides, not one 51-mer.

    The disulfide topology is the real one (A6-A11, A7-B7, A20-B19), which is
    what makes this a useful fixture: it exercises the multi-chain and
    unrepresented-chemistry paths that a single-chain fixture would not.
    """
    return Biologic(
        name="insulin",
        biologic_class=BiologicClass.PEPTIDE,
        chains=(
            Chain(chain_id="A", sequence="GIVEQCCTSICSLYQLENYCN", description="A chain"),
            Chain(chain_id="B", sequence="FVNQHLCGSHLVEALYLVCGERGFFYTPKT", description="B chain"),
        ),
        modifications=(
            Modification(kind=ModificationKind.DISULFIDE, sites=(("A", 6), ("A", 11))),
            Modification(kind=ModificationKind.DISULFIDE, sites=(("A", 7), ("B", 7))),
            Modification(kind=ModificationKind.DISULFIDE, sites=(("A", 20), ("B", 19))),
        ),
        pdb_id="3I40",
    )


@pytest.fixture
def pegylated_insulin(insulin: Biologic) -> Biologic:
    """Insulin with a PEGylation, which no sequence model can see."""
    return insulin.model_copy(
        update={
            "name": "insulin-peg",
            "modifications": (
                *insulin.modifications,
                Modification(
                    kind=ModificationKind.PEGYLATION,
                    sites=(("B", 29),),
                    description="PEG2000 on LysB29",
                    attached_smiles="COCCOCCOCCO",
                ),
            ),
        }
    )


@pytest.fixture
def pvp_candidate() -> PolymerCandidate:
    """Poly(N-vinylpyrrolidone) — povidone, a real formulation excipient."""
    return PolymerCandidate(
        blocks=(
            MonomerBlock(
                unit=RepeatUnit(
                    smiles="[*]CC([*])N1CCCC1=O",
                    name="N-vinylpyrrolidone",
                    family="vinyl-amide",
                ),
                mole_fraction=1.0,
            ),
        ),
        chain_length=ChainLength(degree_of_polymerisation=25),
        end_group=EndGroup.METHYL,
    )


@pytest.fixture
def peg_candidate() -> PolymerCandidate:
    """Poly(ethylene glycol), methyl-capped.

    Capped with methyl rather than hydroxyl deliberately: an oxygen-terminated
    repeat unit capped with ``-OH`` makes a peroxide at the terminus, and
    :func:`biologix.polymers.oligomer.build_oligomer` refuses to build it. That
    refusal is the correct behaviour, so this fixture does not provoke it —
    :func:`test_hydroxyl_cap_on_ether_is_refused` covers it instead.
    """
    return PolymerCandidate(
        blocks=(
            MonomerBlock(
                unit=RepeatUnit(smiles="[*]CCO[*]", name="ethylene oxide", family="ether"),
                mole_fraction=1.0,
            ),
        ),
        chain_length=ChainLength(degree_of_polymerisation=10),
        end_group=EndGroup.METHYL,
    )


@pytest.fixture
def tiny_pdb(tmp_path: Path) -> Path:
    """A minimal valid PDB file, enough to exercise docking request paths."""
    text = "\n".join(
        [
            "HEADER    TEST STRUCTURE",
            "ATOM      1  N   GLY A   1      -1.204   0.000   0.000  1.00  0.00           N",
            "ATOM      2  CA  GLY A   1       0.000   0.800   0.000  1.00  0.00           C",
            "ATOM      3  C   GLY A   1       1.204   0.000   0.000  1.00  0.00           C",
            "ATOM      4  O   GLY A   1       2.300   0.600   0.000  1.00  0.00           O",
            "TER       5      GLY A   1",
            "END",
            "",
        ]
    )
    path = tmp_path / "tiny.pdb"
    path.write_text(text, encoding="utf-8")
    return path
