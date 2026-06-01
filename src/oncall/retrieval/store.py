"""Local vector-search math. Pure functions, no network — unit tested.

For the PoC corpus (hundreds to a couple thousand cases) an in-memory cosine
search is more than enough. The managed Bedrock Knowledge Base replaces this at
the infra step, but the answer prompt stays identical.
"""
import numpy as np


def cosine_topk(query_vec, items, k=3):
    """Return [(item, similarity), ...] for the k most similar items.

    `items` is a list of dicts each carrying a "vector" key.
    """
    if not items:
        return []
    matrix = np.asarray([it["vector"] for it in items], dtype=float)
    q = np.asarray(query_vec, dtype=float)
    q_norm = q / (np.linalg.norm(q) + 1e-9)
    m_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    sims = m_norm @ q_norm
    order = np.argsort(-sims)[:k]
    return [(items[i], float(sims[i])) for i in order]
