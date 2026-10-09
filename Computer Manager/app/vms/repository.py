"""
repository.py — SQL sobre slices.slice_nodo y tablas relacionadas.

VM = nodo de un slice. Estado en slice_nodo.estado_nodo; PID y puerto VNC en slice_nodo.pid / puerto_vnc.
El worker de la VM se toma de slices.reserva_nodo (lo escribe VM Placement).
"""
from typing import Optional


def get_node(conn, nodo_id: str) -> Optional[dict]:
    return conn.execute(
        """
        SELECT n.id::text AS id, n.slice_id, n."name" AS name, n.estado_nodo, n.pid, n.puerto_vnc,
               n.imagen_id, n.flavor_id, f.vcpu, f.vram_mb, f.vdisk_mb
          FROM slices.slice_nodo n
          JOIN slices.flavor f ON f.id = n.flavor_id
         WHERE n.id = %s
        """,
        (nodo_id,),
    ).fetchone()


def get_ports(conn, nodo_id: str) -> list:
    
    return conn.execute(
        """
        SELECT p.id::text AS id, p.name, p.mac::text AS mac, p.vlan_tag
          FROM slices.slice_enlace_puerto p
         WHERE p.nodo_id = %s
         ORDER BY p.name NULLS LAST, p.id
        """,
        (nodo_id,),
    ).fetchall()


def find_worker_ip(conn, nodo_id: str) -> Optional[str]:
    
    row = conn.execute(
        """
        SELECT host(s.ip_serv) AS ip
          FROM slices.reserva_nodo r
          JOIN slices.servidor s ON s.id = r.servidor_id
         WHERE r.nodo_id = %s
         ORDER BY (r.estado = 'RESERVED') DESC, r.fecha_reserva DESC, r.id DESC
         LIMIT 1
        """,
        (nodo_id,),
    ).fetchone()
    return row["ip"] if row else None


def get_image_for_create(conn, imagen_id: int) -> Optional[dict]:
    return conn.execute(
        """
        SELECT i.id, i.nombre, i.ruta_referencia, i.estado, c.nombre AS cluster
          FROM slices.image i
          LEFT JOIN slices.cluster c ON c.id = i.cluster_compatible
         WHERE i.id = %s
        """,
        (imagen_id,),
    ).fetchone()


def set_state(conn, nodo_id: str, estado: str, pid: Optional[int], puerto_vnc: Optional[int]):
    conn.execute(
        "UPDATE slices.slice_nodo SET estado_nodo = %s, pid = %s, puerto_vnc = %s WHERE id = %s",
        (estado, pid, puerto_vnc, nodo_id),
    )


def list_nodes(conn, slice_id: Optional[int] = None, estado: Optional[str] = None) -> list:
    return conn.execute(
        """
        SELECT n.id::text AS id, n.slice_id, n."name" AS name, n.estado_nodo, n.pid, n.puerto_vnc,
               (SELECT host(s.ip_serv)
                  FROM slices.reserva_nodo r JOIN slices.servidor s ON s.id = r.servidor_id
                 WHERE r.nodo_id = n.id
                 ORDER BY (r.estado = 'RESERVED') DESC, r.fecha_reserva DESC, r.id DESC
                 LIMIT 1) AS worker
          FROM slices.slice_nodo n
         WHERE (%(slice_id)s::int IS NULL OR n.slice_id = %(slice_id)s)
           AND (%(estado)s::text IS NULL OR n.estado_nodo = %(estado)s)
         ORDER BY n.slice_id, n."name"
        """,
        {"slice_id": slice_id, "estado": estado},
    ).fetchall()
