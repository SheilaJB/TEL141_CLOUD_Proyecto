"""SQL persistence for compute decisions and image catalog reads."""

from collections.abc import Iterable
from typing import Any


def list_pending_nodes(conn: Any, deployment_id: int) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT id, server_id, vnc_display
          FROM slices.slice_nodo
         WHERE deployment_id = %s
           AND estado_nodo = 'PENDING'
         ORDER BY id
        """,
        (deployment_id,),
    ).fetchall()


def lock_servers(conn: Any, server_ids: Iterable[int]) -> None:
    for server_id in sorted(set(server_ids)):
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (server_id,))


def occupied_displays(conn: Any, server_id: int) -> set[int]:
    rows = conn.execute(
        """
        SELECT vnc_display
          FROM slices.slice_nodo
         WHERE server_id = %s
           AND estado_nodo <> 'DELETED'
           AND vnc_display IS NOT NULL
        """,
        (server_id,),
    ).fetchall()
    return {int(row["vnc_display"]) for row in rows}


def assign_display(
    conn: Any,
    node_id: str,
    server_id: int,
    display: int,
) -> None:
    conn.execute(
        """
        UPDATE slices.slice_nodo
           SET server_id = %s, vnc_display = %s
         WHERE id = %s
           AND estado_nodo = 'PENDING'
        """,
        (server_id, display, node_id),
    )


def rollback_pending(conn: Any, deployment_id: int) -> int:
    result = conn.execute(
        """
        UPDATE slices.slice_nodo
           SET server_id = NULL, vnc_display = NULL
         WHERE deployment_id = %s
           AND estado_nodo = 'PENDING'
        """,
        (deployment_id,),
    )
    return result.rowcount


def list_images(conn: Any) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT id, nombre, cluster_compatible, ruta_referencia,
               contador_refs, estado
          FROM slices.image
         ORDER BY id
        """
    ).fetchall()


def get_image(conn: Any, image_id: int) -> dict[str, Any] | None:
    return conn.execute(
        """
        SELECT id, nombre, cluster_compatible, ruta_referencia,
               contador_refs, estado
          FROM slices.image
         WHERE id = %s
        """,
        (image_id,),
    ).fetchone()
