"""Deprecated import shim for the default-off private NATS RPC server.

Use :class:`a2amesh.a2anats.compatibility.LegacyMeshServerAdapter` explicitly.
"""

from .compatibility import LegacyMeshServerAdapter

MeshServer = LegacyMeshServerAdapter

__all__ = ["LegacyMeshServerAdapter", "MeshServer"]
