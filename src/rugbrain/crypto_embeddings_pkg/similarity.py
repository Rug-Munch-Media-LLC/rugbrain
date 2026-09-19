"""Cosine / weighted similarity helpers."""

from __future__ import annotations

import numpy as np


class SimilarityMixin:
    """Head-aware similarity decomposition."""

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        a_arr = np.array(a, dtype=np.float32)
        b_arr = np.array(b, dtype=np.float32)
        dot = np.dot(a_arr, b_arr)
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    @staticmethod
    def weighted_similarity(
        vec_a: list[float],
        vec_b: list[float],
        semantic_dim: int,
        weights: tuple[float, float, float, float] = (0.4, 0.2, 0.2, 0.2),
    ) -> float:
        """
        Weighted similarity with head-aware decomposition.
        Weights: (semantic, code, behavioral, wallet)
        Assumes vector layout: [semantic | code(128) | behavioral(64) | wallet(64)]
        """
        if len(vec_a) != len(vec_b):
            # Different dims - use common prefix
            min_len = min(len(vec_a), len(vec_b))
            return SimilarityMixin.cosine_similarity(vec_a[:min_len], vec_b[:min_len])

        code_start = semantic_dim
        behav_start = code_start + 128
        wallet_start = behav_start + 64

        sem_sim = SimilarityMixin.cosine_similarity(vec_a[:semantic_dim], vec_b[:semantic_dim])
        code_sim = (
            SimilarityMixin.cosine_similarity(
                vec_a[code_start:behav_start], vec_b[code_start:behav_start]
            )
            if len(vec_a) > code_start
            else 0.0
        )
        behav_sim = (
            SimilarityMixin.cosine_similarity(
                vec_a[behav_start:wallet_start], vec_b[behav_start:wallet_start]
            )
            if len(vec_a) > behav_start
            else 0.0
        )
        wallet_sim = (
            SimilarityMixin.cosine_similarity(vec_a[wallet_start:], vec_b[wallet_start:])
            if len(vec_a) > wallet_start
            else 0.0
        )

        return (
            weights[0] * sem_sim
            + weights[1] * code_sim
            + weights[2] * behav_sim
            + weights[3] * wallet_sim
        )
