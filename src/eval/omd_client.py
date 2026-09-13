"""Client OMD en lecture seule (GET uniquement) : tables d'un service avec
colonnes/tags/owners, et leur lineage. Config via OMD_HOST_PORT / OMD_JWT_TOKEN.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from metadata.generated.schema.entity.data.storedProcedure import StoredProcedure
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    AuthProvider,
    OpenMetadataConnection,
)
from metadata.generated.schema.security.client.openMetadataJWTClientConfig import (
    OpenMetadataJWTClientConfig,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata

TABLE_FIELDS = ["owners", "tags", "columns"]


def client_from_env() -> OpenMetadata:
    """Construit le client OMD depuis les variables d'env (jamais de secret en dur)."""
    config = OpenMetadataConnection(
        hostPort=os.environ["OMD_HOST_PORT"],
        authProvider=AuthProvider.openmetadata,
        securityConfig=OpenMetadataJWTClientConfig(jwtToken=os.environ["OMD_JWT_TOKEN"]),  #  type: ignore[arg-type]
    )
    return OpenMetadata(config)


@dataclass
class LineageFlags:
    has_upstream: bool
    has_downstream: bool


class OMDReadOnlyClient:
    """Wrapper en lecture seule autour du SDK OMD : n'appelle que des GET
    (list_all_entities, get_lineage_by_name), jamais de PATCH/PUT.
    """

    def __init__(self, client: OpenMetadata | None = None):
        self.client = client or client_from_env()

    def list_tables(self, service_name: str):
        """Genere les tables du service ; pagination geree par le SDK."""
        yield from self.client.list_all_entities(
            entity=Table, fields=TABLE_FIELDS, params={"service": service_name}
        )

    def list_stored_procedures(self, service_name: str):
        """Genere les procedures stockees du service ; pagination geree par le SDK."""
        yield from self.client.list_all_entities(
            entity=StoredProcedure, params={"service": service_name}
        )

    def lineage_flags(self, table: Table) -> LineageFlags:
        """GET /lineage pour une table : presence de lineage amont / aval a profondeur 1."""
        lineage = (
            self.client.get_lineage_by_name(
                entity=Table, fqn=table.fullyQualifiedName.root, up_depth=1, down_depth=1
            )
            or {}
        )
        return LineageFlags(
            has_upstream=bool(lineage.get("upstreamEdges")),
            has_downstream=bool(lineage.get("downstreamEdges")),
        )
