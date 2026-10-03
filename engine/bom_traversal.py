# -*- coding: utf-8 -*-
"""
engine/bom_traversal.py
========================
Upward BOM traversal: given a disrupted material, find all finished-good
work orders that require it.

Algorithm:
  1. Walk the BOM tree UPWARD from material_id, collecting all ancestor
     materials (parents, grandparents, ...) using BFS.
  2. Find all work orders whose product_id is among those ancestors.
  3. Return the list of affected WO IDs.

Cycle guard  : visited = set()  -- terminates on any cycle A->B->C->A.
Depth guard  : max_depth = 10   -- hard stop regardless of cycle guard.
Noise check  : only upward traversal is performed; sibling branches
               (e.g. CAPACITOR-100 under PCB-CTRL-01) are NEVER reachable
               starting from STCOIL-440V.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from typing import List, Set


MAX_TRAVERSAL_DEPTH = 10  # algorithmic constant -- not a business fact


def find_ancestor_materials(
    conn: sqlite3.Connection,
    material_id: str,
    max_depth: int = MAX_TRAVERSAL_DEPTH,
) -> Set[str]:
    """
    Return the set of all ancestor material_ids for material_id in the BOM.
    The material_id itself is NOT included (use the caller to add it if needed).

    Cycle-safe via visited set.
    Depth-safe via max_depth guard.

    Example:
        STCOIL-440V -> parents: [MOTOR-ASSY-01]
        MOTOR-ASSY-01 -> parents: [APEXM-100]
        APEXM-100 -> parents: []
        Result: {MOTOR-ASSY-01, APEXM-100}
    """
    visited: Set[str] = set()
    ancestors: Set[str] = set()
    queue: deque = deque()
    queue.append((material_id, 0))

    while queue:
        mid, depth = queue.popleft()
        if mid in visited:
            continue  # cycle guard
        if depth > max_depth:
            continue  # depth guard
        visited.add(mid)

        rows = conn.execute(
            "SELECT DISTINCT parent_material_id FROM bom WHERE child_material_id = ?",
            (mid,),
        ).fetchall()

        for (parent_id,) in rows:
            ancestors.add(parent_id)
            queue.append((parent_id, depth + 1))

    return ancestors


def find_affected_work_orders(
    conn: sqlite3.Connection,
    material_id: str,
    max_depth: int = MAX_TRAVERSAL_DEPTH,
) -> List[str]:
    """
    Find all work order IDs that require material_id (directly or via BOM).

    Steps:
      1. Traverse BOM upward from material_id to collect all ancestor materials.
      2. Add material_id itself to the candidate set (for WOs producing it directly).
      3. Query work_orders where product_id is in the candidate set.

    Returns:
        Sorted list of wo_id strings.

    Notes:
        - Noise materials (CAPACITOR-100, COPPER-WIRE-2) are under separate BOM
          branches and will NOT appear in traversal starting from STCOIL-440V.
        - The traversal is upward-only -- no downward explosion occurs.
    """
    ancestors = find_ancestor_materials(conn, material_id, max_depth)
    candidates = ancestors | {material_id}

    if not candidates:
        return []

    placeholders = ",".join("?" * len(candidates))
    rows = conn.execute(
        f"SELECT wo_id FROM work_orders WHERE product_id IN ({placeholders}) ORDER BY wo_id",
        list(candidates),
    ).fetchall()

    return [row[0] for row in rows]
