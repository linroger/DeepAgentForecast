"""Offline persona fan-out retains explicit parent ownership across concurrent runs."""

import threading
from concurrent.futures import ThreadPoolExecutor

from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from app.services.zep_entity_reader import EntityNode
from app.utils import telemetry as T


def test_persona_jobs_keep_each_run_and_stage_without_shared_context_entry(monkeypatch):
    run_ids = ["pipe_persona_context_a", "pipe_persona_context_b"]
    parent_barrier = threading.Barrier(2)
    worker_barrier = threading.Barrier(4)
    observed = []
    for run_id in [*run_ids, T._DEFAULT_BUCKET]:
        T.LLMMeter.reset(run_id)

    def generate(self, entity, user_id, **kwargs):
        if user_id < 2:
            worker_barrier.wait(timeout=5)
        context = T.get_run_context()
        observed.append((entity.name.split(":")[0], context))
        T.LLMMeter.record("offline", "model", 100, 20, 1)
        # Later jobs in a reused pool worker must receive a fresh parent copy.
        T.set_run_context(context[0], "child-only-stage")
        return OasisAgentProfile(user_id, entity.name, entity.name, "bio", "persona")

    monkeypatch.setattr(OasisProfileGenerator, "generate_profile_from_entity", generate)
    monkeypatch.setattr(OasisProfileGenerator, "_print_generated_profile", lambda *_: None)

    def run(run_id):
        T.set_run_context(run_id, "prepare")
        parent_barrier.wait(timeout=5)
        generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
        entities = [EntityNode(f"node-{i}", f"{run_id}:{i}", ["Person"], "", {}) for i in range(3)]
        profiles = generator.generate_profiles_from_entities(entities, parallel_count=2)
        assert T.get_run_context() == (run_id, "prepare")
        return [profile.name for profile in profiles]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, run_id) for run_id in run_ids]
            results = [future.result(timeout=10) for future in futures]
        assert results == [[f"{run_id}:{i}" for i in range(3)] for run_id in run_ids]
        assert len(observed) == 6
        assert all(context == (run_id, "prepare") for run_id, context in observed)
        assert T.LLMMeter.snapshot(T._DEFAULT_BUCKET)["total"]["calls"] == 0
        for run_id in run_ids:
            snapshot = T.LLMMeter.snapshot(run_id)
            assert snapshot["total"]["calls"] == 3
            assert snapshot["by_stage"]["prepare"]["total_tokens"] == 360
            assert snapshot["fallback_attributed"]["calls"] == 0
    finally:
        for run_id in [*run_ids, T._DEFAULT_BUCKET]:
            T.LLMMeter.reset(run_id)
