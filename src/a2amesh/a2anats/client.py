"""Deprecated import shim for the default-off private NATS RPC client.

Use :class:`a2amesh.a2anats.compatibility.LegacyMeshClientAdapter` explicitly.
"""

from .compatibility import LegacyMeshClientAdapter

MeshClient = LegacyMeshClientAdapter

__all__ = ["LegacyMeshClientAdapter", "MeshClient"]
