"""Project identity and manifest contracts for downstream authors.

This module is the stable facade over Portfolio project contracts.
"""

from noetrium_platform.foundation.portfolio.api import (
    PROJECT_MANIFEST_SCHEMA,
    ProgramSpec,
    ProjectCapabilityRequirement,
    ProjectConfigurationReference,
    ProjectIdentity,
    ProjectManifest,
    ProjectManifestDecodeError,
    ProjectManifestFacet,
    ProjectManifestFacetChange,
    ProjectManifestFacetDiff,
    ProjectManifestIdentityFacets,
    ProjectMethodRequirement,
    ProjectProviderBinding,
    ProjectRequirementCardinality,
    ProjectSpec,
    ProjectToolProvenance,
    WorkspaceSpec,
    decode_project_manifest_bytes,
    decode_project_manifest_document,
    diff_project_manifest_facets,
    encode_project_manifest,
    project_manifest_document,
    project_manifest_identity_facets,
)

__all__ = [
    "PROJECT_MANIFEST_SCHEMA", "ProgramSpec", "ProjectCapabilityRequirement",
    "ProjectConfigurationReference", "ProjectIdentity", "ProjectManifest",
    "ProjectManifestDecodeError", "ProjectManifestFacet",
    "ProjectManifestFacetChange", "ProjectManifestFacetDiff",
    "ProjectManifestIdentityFacets", "ProjectMethodRequirement",
    "ProjectProviderBinding", "ProjectRequirementCardinality", "ProjectSpec",
    "ProjectToolProvenance", "WorkspaceSpec",
    "decode_project_manifest_bytes", "decode_project_manifest_document",
    "diff_project_manifest_facets", "encode_project_manifest",
    "project_manifest_document", "project_manifest_identity_facets",
]
