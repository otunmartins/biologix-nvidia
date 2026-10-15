"""DiffDock adapter: pose parsing, ranking, and the schema-drift record.

The response schema is not fully documented publicly, so the parser is
deliberately tolerant. These tests pin what that tolerance may and may not do:
it may accept alternative spellings and scalar-vs-list shapes, but it must
record every deviation rather than absorbing it silently, and it must never
invent a confidence score for a pose that came back without one.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from biologix.errors import AdapterCallFailed, AdapterUnavailable
from biologix.nvidia.cache import JsonCache
from biologix.nvidia.diffdock import (
    DiffDockAdapter,
    _parse_poses,
    diffdock_availability,
    dock_candidate,
)

HEALTH = "http://127.0.0.1:8001/v1/health/ready"
GENERATE = "http://127.0.0.1:8001/molecular-docking/diffdock/generate"

_SDF = """pose
  RDKit

  1  0  0  0  0  0
    0.0000    0.0000    0.0000 C
M  END
$$$$
"""


def _adapter(settings, tmp_path):
    return DiffDockAdapter(settings, cache=JsonCache("diffdock", directory=tmp_path / "c"))


def _ok_body(n=3):
    return {
        "status": "success",
        "ligand_positions": [_SDF.replace("pose", f"pose{i}") for i in range(n)],
        "position_confidence": [-0.5, 0.9, 0.2][:n],
        "protein": "ATOM...",
    }


# -- availability ----------------------------------------------------------


def test_hosted_without_key_is_unavailable(hosted_settings):
    report = diffdock_availability(hosted_settings)
    assert report["available"] is False
    assert "NVIDIA_API_KEY" in report["reason"]


def test_availability_states_response_schema_is_unconfirmed(hosted_settings):
    report = diffdock_availability(hosted_settings)
    assert report["api_contract_verified"] == "request only"
    assert "not the" in report["response_schema_note"]
    assert report["code_path_executed_in_this_build"] is False


@respx.mock
def test_local_healthy_is_available(local_settings):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    assert diffdock_availability(local_settings)["available"] is True


# -- request validation ----------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "kwargs", [{"num_poses": 0}, {"num_poses": 101}, {"time_divisions": 2}, {"steps": 19}]
)
def test_out_of_range_parameters_fail_locally(local_settings, tmp_path, kwargs):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_ok_body()))
    with pytest.raises(ValueError, match="outside the documented range"):
        _adapter(local_settings, tmp_path).generate("ATOM 1", "CCO", **kwargs)
    assert not route.called


@respx.mock
def test_bad_ligand_format_is_refused(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    with pytest.raises(ValueError, match="ligand_file_type must be one of"):
        _adapter(local_settings, tmp_path).generate("ATOM 1", "CCO", ligand_file_type="pdbqt")


@respx.mock
def test_smiles_input_cannot_skip_conformer_generation(local_settings, tmp_path):
    """SMILES carries no coordinates, so skipping conformer generation is a
    contradiction that would fail opaquely server-side."""
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    with pytest.raises(ValueError, match="no coordinates"):
        _adapter(local_settings, tmp_path).generate(
            "ATOM 1", "CCO", ligand_file_type="txt", skip_gen_conformer=True
        )


@respx.mock
def test_empty_inputs_are_refused(local_settings, tmp_path):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    adapter = _adapter(local_settings, tmp_path)
    with pytest.raises(ValueError, match="non-empty PDB text"):
        adapter.generate("   ", "CCO")
    with pytest.raises(ValueError, match="ligand must be non-empty"):
        adapter.generate("ATOM 1", "  ")


# -- pose parsing ----------------------------------------------------------


def test_poses_are_ranked_by_confidence():
    poses, mismatch = _parse_poses(_ok_body())
    assert mismatch == []
    assert [p.confidence for p in poses] == [0.9, 0.2, -0.5]
    assert [p.rank for p in poses] == [1, 2, 3]
    assert all(p.structure_format == "sdf" for p in poses)


def test_unscored_poses_keep_their_order_and_stay_unscored():
    """A missing score must not be treated as a low score."""
    poses, mismatch = _parse_poses(
        {"status": "ok", "ligand_positions": [_SDF, _SDF, _SDF], "position_confidence": [0.4]}
    )
    assert [p.confidence for p in poses] == [0.4, None, None]
    assert any("3 poses but 1 confidence" in m for m in mismatch)


def test_alternative_key_is_accepted_but_recorded():
    poses, mismatch = _parse_poses({"status": "ok", "poses": [_SDF], "position_confidence": 0.7})
    assert len(poses) == 1
    assert poses[0].confidence == 0.7
    assert any("rather than 'ligand_positions'" in m for m in mismatch)


def test_absent_expected_keys_are_recorded():
    _, mismatch = _parse_poses({"ligand_positions": [_SDF]})
    assert any("'position_confidence' absent" in m for m in mismatch)
    assert any("'status' absent" in m for m in mismatch)


def test_unparsable_poses_are_skipped_and_counted():
    poses, mismatch = _parse_poses(
        {"status": "ok", "ligand_positions": [_SDF, "", None], "position_confidence": [1.0]}
    )
    assert len(poses) == 1
    assert sum("not a non-empty string" in m for m in mismatch) == 2


def test_wrong_type_for_positions_yields_no_poses():
    poses, mismatch = _parse_poses({"status": "ok", "ligand_positions": {"a": 1}})
    assert poses == []
    assert any("expected a list" in m for m in mismatch)


# -- docking a candidate ---------------------------------------------------


@respx.mock
def test_dock_candidate_records_flexibility(local_settings, tmp_path, tiny_pdb, pvp_candidate):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_ok_body()))

    result = dock_candidate(
        tiny_pdb,
        pvp_candidate,
        adapter=_adapter(local_settings, tmp_path),
        settings=local_settings,
        dp_override=10,
    )
    assert len(result.poses) == 3
    assert result.best.confidence == 0.9
    assert result.n_rotatable_bonds is not None and result.n_rotatable_bonds > 10
    assert "rotatable bonds" in result.flexibility_caveat
    assert any("rotatable bonds" in w for w in result.manifest.warnings)
    # A pose is a starting geometry, not a binding claim.
    assert result.poses[0].interpretation == "candidate starting geometry"
    assert "not binding strength" in result.summary()["interpretation"]


@respx.mock
def test_short_oligomer_has_no_flexibility_caveat(
    local_settings, tmp_path, tiny_pdb, peg_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_ok_body(1)))
    result = dock_candidate(
        tiny_pdb, peg_candidate, adapter=_adapter(local_settings, tmp_path),
        settings=local_settings, dp_override=2,
    )
    assert result.n_rotatable_bonds <= 10
    assert result.flexibility_caveat is None


@respx.mock
def test_protein_is_hashed_into_provenance(local_settings, tmp_path, tiny_pdb, peg_candidate):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_ok_body(1)))
    result = dock_candidate(
        tiny_pdb, peg_candidate, adapter=_adapter(local_settings, tmp_path),
        settings=local_settings, dp_override=2,
    )
    assert len(result.manifest.inputs["protein_sha256"]) == 64
    assert result.manifest.inputs["protein"].endswith("tiny.pdb")


@respx.mock
def test_inline_pdb_text_is_accepted(local_settings, tmp_path, tiny_pdb, peg_candidate):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_ok_body(1)))
    result = dock_candidate(
        tiny_pdb.read_text(encoding="utf-8"),
        peg_candidate,
        adapter=_adapter(local_settings, tmp_path),
        settings=local_settings,
        dp_override=2,
    )
    assert result.manifest.inputs["protein"] == "<inline PDB text>"


def test_missing_structure_file_is_an_error(local_settings, tmp_path, peg_candidate):
    with pytest.raises(FileNotFoundError):
        dock_candidate(
            tmp_path / "nope.pdb", peg_candidate, settings=local_settings, dp_override=2
        )


@respx.mock
def test_service_reported_failure_raises(local_settings, tmp_path, tiny_pdb, peg_candidate):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(
        return_value=httpx.Response(200, json={"status": "failed", "message": "bad ligand"})
    )
    with pytest.raises(AdapterCallFailed, match="bad ligand"):
        dock_candidate(
            tiny_pdb, peg_candidate, adapter=_adapter(local_settings, tmp_path),
            settings=local_settings, dp_override=2,
        )


def test_no_credentials_raises_unavailable(hosted_settings, tmp_path, tiny_pdb, peg_candidate):
    with pytest.raises(AdapterUnavailable):
        dock_candidate(
            tiny_pdb,
            peg_candidate,
            adapter=DiffDockAdapter(
                hosted_settings, cache=JsonCache("diffdock", directory=tmp_path / "c")
            ),
            settings=hosted_settings,
            dp_override=2,
        )


@respx.mock
def test_large_protein_is_cached_by_hash_not_by_value(
    local_settings, tmp_path, tiny_pdb, peg_candidate
):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_ok_body(1)))
    cache = JsonCache("diffdock", directory=tmp_path / "shared")

    for _ in range(2):
        result = dock_candidate(
            tiny_pdb, peg_candidate, adapter=DiffDockAdapter(local_settings, cache=cache),
            settings=local_settings, dp_override=2,
        )
    assert route.call_count == 1
    assert result.cached is True


@respx.mock
def test_pose_can_be_written_to_disk(local_settings, tmp_path, tiny_pdb, peg_candidate):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(GENERATE).mock(return_value=httpx.Response(200, json=_ok_body(1)))
    result = dock_candidate(
        tiny_pdb, peg_candidate, adapter=_adapter(local_settings, tmp_path),
        settings=local_settings, dp_override=2,
    )
    path = result.best.save(tmp_path / "poses", "peg")
    assert path.exists()
    assert path.suffix == ".sdf"
    assert "M  END" in path.read_text(encoding="utf-8")
